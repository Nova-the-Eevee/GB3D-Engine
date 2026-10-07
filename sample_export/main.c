#include <gb/gb.h>
#include <gb/cgb.h>
#include <gb/hardware.h>
#include <stdint.h>

// ============================================================
// GB3D STUDIO 1.2 RUNTIME + DDA RASTER / FAST MATH / CGB GDMA
// ============================================================
//
// V3 removes the expensive 16-color software framebuffer entirely.
// Triangles rasterize DIRECTLY into the final GBC tilemap + attribute map.
//
// Logical resolution: 40x36 (one logical pixel = 4x4 LCD pixels)
// Final RAM tilemap uses the native 32x18 BG-map stride. Only the first
// 20 columns are rasterized/cleared, while CGB GDMA uploads all 32 columns
// in one contiguous transfer per VRAM bank.
//
// Every 8x8 hardware tile is one of 256 pre-generated 2x2 shade patterns:
//
//      TL | TR
//     ----+----
//      BL | BR
//
// Each quadrant stores a 2-bit shade index. The tile's CGB attribute selects
// one of eight material palettes. All material palettes use the SAME index
// semantics: 0=black/background, 1=dark, 2=mid, 3=bright. This is important:
// if differently colored triangles touch the same hardware tile, the newest
// triangle may change the tile palette, but the existing quadrants keep their
// shade/intensity instead of turning into random colors.
//
// Main hot path:
//   C: transform/project/cull/depth-bucket
//   ASM: scan-convert whole triangle with exact quotient/remainder DDA
//
// NO per-frame palette search.
// NO intermediate 720-byte color framebuffer.
// NO C callback for every triangle scanline.
// ============================================================

#define FB_WIDTH          40
#define FB_HEIGHT         36
#define FB_VISIBLE_TILE_W 20
#define FB_TILE_H         18
#define FB_MAP_W          32
#define FB_MAP_SIZE       (FB_MAP_W * FB_TILE_H)
#define FB_DMA_BLOCKS     (FB_MAP_SIZE / 16u)

#define DEPTH_BUCKETS     16u
#define FACE_NONE         0xFFu

#define SCREEN_CX         20
#define SCREEN_CY         18
#define FOCAL_LEN         28
#define NEAR_Z            8
#define FAR_Z             255
#define MOVE_SPEED        2
#define RENDER_DIVISOR    1u
#define GB3D_PROFILE       SCENE_PROFILE_BUILD

#define MAX_MODEL_VERTICES 128u
#define MAX_MODEL_FACES    128u
#define MAX_SCENE_OBJECTS   32u
#define MAX_FRAME_TRIANGLES 192u
#define MAX_NPCS            4u

// Keep projected vertices bounded so face math stays small and pathological
// near-plane geometry cannot create enormous scan loops.
#define PROJ_X_MIN       (-32)
#define PROJ_X_MAX       71
#define PROJ_Y_MIN       (-32)
#define PROJ_Y_MAX       67

#define TRI_X_MIN        (-64)
#define TRI_X_MAX        95
#define TRI_Y_MIN        (-64)
#define TRI_Y_MAX        99

// ------------------------------------------------------------
// 3D types
// ------------------------------------------------------------

// Model-local coordinates are intentionally signed 8-bit in V3.
// OBJ import normalizes models into this range, halving vertex ROM bandwidth.
typedef struct {
    int8_t x;
    int8_t y;
    int8_t z;
} ModelVertex;

// Static scene transforms can be baked completely on the PC. These 16-bit
// vertices are already in world space (or fixed-camera-rotated world space),
// so the GBC never pays object rotation/translation for them.
typedef struct {
    int16_t x;
    int16_t y;
    int16_t z;
} WorldVertex;

typedef struct {
    int16_t x;
    int16_t y;
    int16_t z;
} Vec3;

typedef struct {
    int8_t x;
    int8_t y;
    uint8_t visible;
} Vec2;

// palette: 0..7 = hardware material palette. Black/invisible faces are stripped by the exporter.
typedef struct {
    uint8_t a;
    uint8_t b;
    uint8_t c;
    uint8_t palette;
} Face3D;

typedef struct {
    const ModelVertex *vertices;
    const Face3D *faces;
    uint8_t vertex_count;
    uint8_t face_count;
    int8_t min_x, max_x;
    int8_t min_y, max_y;
    int8_t min_z, max_z;
    uint8_t radius;  // conservative object-space culling radius
} Model3D;

typedef struct {
    const Model3D *model;
    int16_t x;
    int16_t y;
    int16_t z;
    uint8_t rot_x;
    uint8_t rot_y;
    uint8_t visible;
    uint8_t solid;
} SceneObject3D;

// Render-only view of a scene object. In the optional fixed-camera fast path,
// Studio pre-rotates static geometry and the object origin by the locked camera
// orientation. Logical scene_objects stay untouched for scripts/collision.
typedef struct {
    // Non-null only when Studio proved the object's position+rotation are
    // immutable and baked every transformed vertex on the PC.
    const WorldVertex *world_vertices;
    int16_t x;
    int16_t y;
    int16_t z;
    uint8_t camera_baked;
} SceneRenderInfo;

// ------------------------------------------------------------
// Camera + script-visible helpers
// ------------------------------------------------------------

typedef struct {
    int16_t x;
    int16_t y;
    int16_t z;
    uint8_t yaw;
    int8_t pitch;
} Camera;

static Camera camera = {0, 0, -64, 0, 0};

// Screen-space billboard character controlled by scripts. The 32x32 art is
// rendered with the same GBC hardware-metasprite technique as the player.
typedef struct {
    int16_t x;
    int16_t y;
    int16_t z;
    uint8_t height;
    uint8_t visible;
} GB3DNPC;

// Hardware-Window HUD elements. type: 0=text, 1=bar.
// Coordinates are native 8x8 Window tile coordinates.
typedef struct {
    uint8_t type;
    uint8_t tile_x;
    uint8_t tile_y;
    uint8_t width;
    uint8_t color;
    uint8_t bg_color;
    uint8_t visible;
    uint8_t show_value;
    int16_t value;
    int16_t max_value;
    const char *text;
} GB3DUIElement;

static GB3DNPC npcs[MAX_NPCS];
static uint16_t gb3d_frame_counter = 0u;

// Generated scene scripts call these helpers, so declare them before the
// generated header is included.
static int8_t gb3d_clamp_pitch(int16_t value);
static void gb3d_set_sky_logical(uint8_t logical_color);
static void gb3d_player_move(int16_t dx, int16_t dy, int16_t dz);
static void gb3d_player_set(int16_t x, int16_t y, int16_t z);
static void gb3d_player_jump(void);
static void gb3d_player_set_visible(uint8_t visible);
static void gb3d_player_toggle_visible(void);
static int16_t gb3d_player_get_x(void);
static int16_t gb3d_player_get_y(void);
static int16_t gb3d_player_get_z(void);
static void gb3d_ui_set_value(uint8_t index, int16_t value);
static void gb3d_ui_add_value(uint8_t index, int16_t delta);
static void gb3d_ui_set_max(uint8_t index, int16_t value);
static void gb3d_ui_set_element_visible(uint8_t index, uint8_t visible);
static void gb3d_ui_toggle_element(uint8_t index);
static void gb3d_window_set_visible(uint8_t visible);
static void gb3d_window_toggle_visible(void);
static void gb3d_window_move(uint8_t x, uint8_t y);
static void gb3d_npc_set_visible(uint8_t index, uint8_t visible);
static void gb3d_npc_toggle_visible(uint8_t index);
static int16_t gb3d_abs16(int16_t value);
static int16_t gb3d_min16(int16_t a, int16_t b);
static int16_t gb3d_max16(int16_t a, int16_t b);
static int16_t gb3d_rand16(int16_t limit);
static void world_to_camera(const Vec3 *world, Vec3 *out);
static void project_vertex(const Vec3 *in, Vec2 *out);

// Generated by GB3D Studio. Types and script-visible globals above must exist.
#include "scene_gb3d.h"

// ============================================================
// GBSCRIPT 2 SMALL RUNTIME HELPERS
// ============================================================

static uint16_t gb3d_rand_state = 0xACE1u;

static int16_t gb3d_abs16(int16_t value) { return value < 0 ? (int16_t)-value : value; }
static int16_t gb3d_min16(int16_t a, int16_t b) { return a < b ? a : b; }
static int16_t gb3d_max16(int16_t a, int16_t b) { return a > b ? a : b; }
static int16_t gb3d_rand16(int16_t limit) {
    uint16_t bit;
    if (limit <= 0) return 0;
    // Tiny 16-bit Galois-ish LFSR: deterministic, cheap, good enough for games.
    bit = (uint16_t)(((gb3d_rand_state >> 0) ^ (gb3d_rand_state >> 2) ^ (gb3d_rand_state >> 3) ^ (gb3d_rand_state >> 5)) & 1u);
    gb3d_rand_state = (uint16_t)((gb3d_rand_state >> 1) | (bit << 15));
    return (int16_t)(gb3d_rand_state % (uint16_t)limit);
}

// ============================================================
// FINAL TILEMAP FRAMEBUFFER
// ============================================================

// CGB General-Purpose VRAM DMA ignores the low four source-address bits, so
// both framebuffer maps must begin on 16-byte boundaries. SDCC does not give
// portable alignment attributes here, so one padded storage block is aligned
// at runtime. FB_MAP_SIZE (576) is itself a multiple of 16, therefore the
// attribute map is automatically aligned too.
static uint8_t framebuffer_storage[(FB_MAP_SIZE * 2u) + 15u];
uint8_t *framebuffer_tilemap;
uint8_t *framebuffer_attributes;

// Native Game Boy BG maps are 32 tiles wide. The renderer only touches the
// visible first 20 columns; the ASM rasterizer computes row*32 directly.

// Startup scratch for the 256 static 2x2 shade-pattern tiles.
static uint8_t tile_batch[16u * 16u];

// Generic assembly memset used to clear the final tilemap each frame.
uint8_t *gb3d_asm_ptr;
uint16_t gb3d_asm_count;
uint8_t gb3d_asm_value;
void gb3d_fill_bytes_asm(void);
void gb3d_clear_visible_asm(void);
static void fast_fill_bytes(uint8_t *ptr, uint16_t count, uint8_t value);

// ------------------------------------------------------------
// Whole-triangle ASM interface
// ------------------------------------------------------------
// Projected queue vertices are already bounded to the renderer's safe 8-bit
// domain. C only Y-sorts the three points and copies them to the interface;
// ASM computes packed material values plus quotient/remainder edge DDA setup.
// q is a signed modular byte and each scanline still needs at most ONE
// remainder correction, preserving the exact 1.2 coverage rules.

int8_t gb3d_tri_x0, gb3d_tri_y0;
int8_t gb3d_tri_x1, gb3d_tri_y1;
int8_t gb3d_tri_x2, gb3d_tri_y2;
uint8_t gb3d_tri_material;
uint8_t gb3d_tri_palette;
uint8_t gb3d_tri_shade;
uint8_t gb3d_tri_pair_top;
uint8_t gb3d_tri_pair_bottom;

uint8_t gb3d_top_rows;
uint8_t gb3d_bottom_rows;

uint8_t gb3d_long_q, gb3d_long_r, gb3d_long_dy;
int8_t gb3d_long_step;
uint8_t gb3d_top_q, gb3d_top_r, gb3d_top_dy;
int8_t gb3d_top_step;
uint8_t gb3d_bottom_q, gb3d_bottom_r, gb3d_bottom_dy;
int8_t gb3d_bottom_step;

// Mutable edge/span state used internally by assembly.
int8_t gb3d_current_y;
int8_t gb3d_long_x;
int8_t gb3d_short_x;
uint8_t gb3d_long_err;
uint8_t gb3d_short_err;
uint8_t gb3d_rows_left;
int8_t gb3d_span_x1;
int8_t gb3d_span_x2;

void gb3d_draw_triangle_asm(void);

// ============================================================
// MATERIAL PALETTES
// ============================================================
// Every palette is [black, dark, mid, bright]. Keeping the same shade meaning
// at every palette index makes direct tile writes practical and cheap.

static const palette_color_t bg_palettes[8u * 4u] = {
    // 0 grayscale
    RGB(0, 0, 0),   RGB(8, 8, 8),    RGB(20, 20, 20), RGB(31, 31, 31),
    // 1 red
    RGB(0, 0, 0),   RGB(11, 2, 3),   RGB(25, 4, 5),   RGB(31, 16, 16),
    // 2 green
    RGB(0, 0, 0),   RGB(2, 10, 3),   RGB(4, 23, 6),   RGB(16, 31, 17),
    // 3 blue
    RGB(0, 0, 0),   RGB(2, 4, 11),   RGB(4, 10, 25),  RGB(16, 20, 31),
    // 4 purple / pink
    RGB(0, 0, 0),   RGB(9, 3, 11),   RGB(20, 7, 25),  RGB(31, 18, 31),
    // 5 warm yellow / orange
    RGB(0, 0, 0),   RGB(12, 6, 1),   RGB(27, 15, 2),  RGB(31, 29, 8),
    // 6 cyan
    RGB(0, 0, 0),   RGB(1, 9, 10),   RGB(3, 22, 24),  RGB(15, 31, 31),
    // 7 earth / tan
    RGB(0, 0, 0),   RGB(8, 6, 3),    RGB(19, 14, 8),  RGB(29, 24, 16)
};

// ============================================================
// SKY / BACKGROUND
// ============================================================

// Editor logical color -> direct runtime palette/shade. Black uses grayscale
// shade 0. Every frame starts from this packed shade and palette.
static const uint8_t sky_palette_for_color[16] = {
    0u, 0u, 0u, 0u,
    1u, 1u, 2u, 2u,
    3u, 3u, 5u, 5u,
    6u, 4u, 7u, 4u
};

static const uint8_t sky_shade_for_color[16] = {
    0u, 3u, 2u, 1u,
    2u, 1u, 2u, 1u,
    2u, 1u, 3u, 2u,
    3u, 2u, 2u, 3u
};

static uint8_t sky_palette = 0u;
static uint8_t sky_shade = 0u;
static uint8_t sky_tile_id = 0u;

static int8_t gb3d_clamp_pitch(int16_t value) {
    if (value < -12) return -12;
    if (value > 12) return 12;
    return (int8_t)value;
}

static void gb3d_set_sky_logical(uint8_t logical_color) {
    logical_color &= 15u;
    sky_palette = sky_palette_for_color[logical_color];
    sky_shade = sky_shade_for_color[logical_color];
    // Repeat the same 2-bit shade into TL/TR/BL/BR of the static pattern tile.
    sky_tile_id = (uint8_t)(sky_shade | (sky_shade << 2) | (sky_shade << 4) | (sky_shade << 6));
}

// ============================================================
// HARDWARE WINDOW UI
// ============================================================
//
// The 3D renderer owns the Background layer. UI now lives on the real Game
// Boy Window layer (normally map $9C00), exactly what the hardware was made
// for. Window tile graphics use VRAM bank 1 so they do not consume any of the
// 256 packed 3D pattern tiles in bank 0.
//
// Bank 1 tile allocation:
//   0..79   player + up to 4 NPC 32x32 metasprites (worst case)
//   80..95  profiler 5x7 glyphs 0..15
//   96..228 Window font + solid bar/background tiles
//   229..248 profiler 5x7 glyphs 16..35
//
// Splitting the profiler font around the Window font gives it a complete
// A-Z + 0-9 alphabet without stealing sprite or normal HUD tile space.
// UI data is rebuilt only when a script changes it. Static HUD text therefore
// costs effectively nothing per rendered frame.

#define UI_MAP_W             20u
#define UI_MAP_H             18u
#define UI_MAP_SIZE          (UI_MAP_W * UI_MAP_H)
#define UI_TILE_BASE         96u
#define UI_GLYPH_COUNT       43u
#define UI_GLYPH_VARIANTS    3u
#define UI_SOLID_BASE        (UI_TILE_BASE + UI_GLYPH_COUNT * UI_GLYPH_VARIANTS)

#define PROF_GLYPH_COUNT      36u
#define PROF_TILE_LOW_BASE    80u
#define PROF_TILE_HIGH_BASE   229u
#define PROF_TILE_SPLIT       16u

static uint8_t window_tilemap[UI_MAP_SIZE];
static uint8_t window_attributes[UI_MAP_SIZE];
static uint8_t ui_tile_scratch[16];
static uint8_t gb3d_ui_dirty = 1u;
static uint8_t gb3d_ui_upload_pending = 1u;
static uint8_t gb3d_window_visible = SCENE_WINDOW_ENABLED;
static uint8_t gb3d_window_x = SCENE_WINDOW_X;
static uint8_t gb3d_window_y = SCENE_WINDOW_Y;

static const uint8_t ui_font3x5[36u * 5u] = {
    // A-Z
    2,5,7,5,5, 6,5,6,5,6, 3,4,4,4,3, 6,5,5,5,6, 7,4,6,4,7, 7,4,6,4,4,
    3,4,5,5,3, 5,5,7,5,5, 7,2,2,2,7, 1,1,1,5,2, 5,5,6,5,5, 4,4,4,4,7,
    5,7,7,5,5, 5,7,7,7,5, 2,5,5,5,2, 6,5,6,4,4, 2,5,5,3,1, 6,5,6,5,5,
    3,4,2,1,6, 7,2,2,2,2, 5,5,5,5,7, 5,5,5,5,2, 5,5,7,7,5, 5,5,2,5,5,
    5,5,2,2,2, 7,1,2,4,7,
    // 0-9
    2,5,5,5,2, 2,6,2,2,7, 6,1,2,4,7, 6,1,2,1,6, 5,5,7,1,1,
    7,4,6,1,6, 3,4,6,5,2, 7,1,2,2,2, 2,5,2,5,2, 2,5,3,1,6
};

static const char ui_glyph_chars[UI_GLYPH_COUNT + 1u] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-:.!/? ";

static uint8_t ui_font_row(char ch, uint8_t row) {
    uint8_t index;
    if (ch >= 'a' && ch <= 'z') ch = (char)(ch - ('a' - 'A'));
    if (ch >= 'A' && ch <= 'Z') index = (uint8_t)(ch - 'A');
    else if (ch >= '0' && ch <= '9') index = (uint8_t)(26u + ch - '0');
    else {
        if (ch == '-') return row == 2u ? 7u : 0u;
        if (ch == ':') return (row == 1u || row == 3u) ? 2u : 0u;
        if (ch == '.') return row == 4u ? 2u : 0u;
        if (ch == '!') return (row < 3u || row == 4u) ? 2u : 0u;
        if (ch == '/') return (row < 2u) ? 1u : (row == 2u ? 2u : 4u);
        if (ch == '?') { static const uint8_t q[5] = {6u,1u,2u,0u,2u}; return q[row]; }
        return 0u;
    }
    return ui_font3x5[(uint16_t)index * 5u + row];
}

static uint8_t ui_glyph_index(char ch) {
    uint8_t i;
    if (ch >= 'a' && ch <= 'z') ch = (char)(ch - ('a' - 'A'));
    for (i = 0u; i < UI_GLYPH_COUNT; i++)
        if (ui_glyph_chars[i] == ch) return i;
    return 41u; // '?'
}

static void ui_make_glyph_tile(char ch, uint8_t shade) {
    uint8_t y, bits, mask;
    for (y = 0u; y < 8u; y++) {
        bits = (y >= 1u && y <= 5u) ? ui_font_row(ch, (uint8_t)(y - 1u)) : 0u;
        // Center the 3x5 glyph in the 8x8 cell: bits 5,4,3.
        mask = (uint8_t)(bits << 3);
        ui_tile_scratch[(uint8_t)(y << 1)] = (shade & 1u) ? mask : 0u;
        ui_tile_scratch[(uint8_t)((y << 1) + 1u)] = (shade & 2u) ? mask : 0u;
    }
}

static void ui_make_solid_tile(uint8_t shade) {
    uint8_t y;
    uint8_t lo = (shade & 1u) ? 0xFFu : 0x00u;
    uint8_t hi = (shade & 2u) ? 0xFFu : 0x00u;
    for (y = 0u; y < 8u; y++) {
        ui_tile_scratch[(uint8_t)(y << 1)] = lo;
        ui_tile_scratch[(uint8_t)((y << 1) + 1u)] = hi;
    }
}

#if GB3D_PROFILE
static const uint8_t prof_font5x7[PROF_GLYPH_COUNT][7] = {
    // A-Z
    {14,17,17,31,17,17,17}, // A
    {30,17,17,30,17,17,30}, // B
    {14,17,16,16,16,17,14}, // C
    {30,17,17,17,17,17,30}, // D
    {31,16,16,30,16,16,31}, // E
    {31,16,16,30,16,16,16}, // F
    {14,17,16,23,17,17,15}, // G
    {17,17,17,31,17,17,17}, // H
    {31,4,4,4,4,4,31},      // I
    {7,2,2,2,18,18,12},     // J
    {17,18,20,24,20,18,17}, // K
    {16,16,16,16,16,16,31}, // L
    {17,27,21,21,17,17,17}, // M
    {17,25,25,21,19,19,17}, // N
    {14,17,17,17,17,17,14}, // O
    {30,17,17,30,16,16,16}, // P
    {14,17,17,17,21,18,13}, // Q
    {30,17,17,30,20,18,17}, // R
    {15,16,16,14,1,1,30},   // S
    {31,4,4,4,4,4,4},       // T
    {17,17,17,17,17,17,14}, // U
    {17,17,17,17,17,10,4},  // V
    {17,17,17,21,21,21,10}, // W
    {17,17,10,4,10,17,17},  // X
    {17,17,10,4,4,4,4},     // Y
    {31,1,2,4,8,16,31},     // Z
    // 0-9
    {14,17,19,21,25,17,14},
    {4,12,4,4,4,4,14},
    {14,17,1,2,4,8,31},
    {30,1,1,14,1,1,30},
    {2,6,10,18,31,2,2},
    {31,16,16,30,1,1,30},
    {14,16,16,30,17,17,14},
    {31,1,2,4,8,8,8},
    {14,17,17,14,17,17,14},
    {14,17,17,15,1,1,14}
};

static uint8_t gb3d_prof_tile_id(uint8_t glyph) {
    if (glyph < PROF_TILE_SPLIT)
        return (uint8_t)(PROF_TILE_LOW_BASE + glyph);
    return (uint8_t)(PROF_TILE_HIGH_BASE + glyph - PROF_TILE_SPLIT);
}

static void init_profiler_tiles(void) {
    uint8_t g, y, mask;
    VBK_REG = VBK_BANK_1;
    for (g = 0u; g < PROF_GLYPH_COUNT; g++) {
        for (y = 0u; y < 8u; y++) {
            mask = (y < 7u) ? (uint8_t)(prof_font5x7[g][y] << 2) : 0u;
            // Shade 3 in palette 0 = white.
            ui_tile_scratch[(uint8_t)(y << 1)] = mask;
            ui_tile_scratch[(uint8_t)((y << 1) + 1u)] = mask;
        }
        set_win_data(gb3d_prof_tile_id(g), 1u, ui_tile_scratch);
    }
    VBK_REG = VBK_BANK_0;
}
#endif

static void init_window_tiles(void) {
    uint8_t shade, glyph, tile;
    VBK_REG = VBK_BANK_1;
    for (shade = 1u; shade <= 3u; shade++) {
        for (glyph = 0u; glyph < UI_GLYPH_COUNT; glyph++) {
            ui_make_glyph_tile(ui_glyph_chars[glyph], shade);
            tile = (uint8_t)(UI_TILE_BASE + (shade - 1u) * UI_GLYPH_COUNT + glyph);
            set_win_data(tile, 1u, ui_tile_scratch);
        }
    }
    for (shade = 0u; shade <= 3u; shade++) {
        ui_make_solid_tile(shade);
        set_win_data((uint8_t)(UI_SOLID_BASE + shade), 1u, ui_tile_scratch);
    }
    VBK_REG = VBK_BANK_0;
#if GB3D_PROFILE
    init_profiler_tiles();
#endif
}

static uint8_t ui_tile_for_char(char ch, uint8_t logical_color) {
    uint8_t shade;
    if (ch == ' ') return (uint8_t)(UI_SOLID_BASE + 0u);
    logical_color &= 15u;
    shade = sky_shade_for_color[logical_color];
    if (shade == 0u) return (uint8_t)(UI_SOLID_BASE + 0u);
    return (uint8_t)(UI_TILE_BASE + (shade - 1u) * UI_GLYPH_COUNT + ui_glyph_index(ch));
}

static uint8_t ui_attr_for_color(uint8_t logical_color) {
    return (uint8_t)(BKGF_BANK1 | (sky_palette_for_color[logical_color & 15u] & 7u));
}

static void ui_map_put(uint8_t x, uint8_t y, uint8_t tile, uint8_t attr) {
    uint16_t idx;
    if (x >= UI_MAP_W || y >= UI_MAP_H) return;
    idx = (uint16_t)y * UI_MAP_W + x;
    window_tilemap[idx] = tile;
    window_attributes[idx] = attr;
}

static uint8_t ui_draw_text_tiles(uint8_t x, uint8_t y, const char *text, uint8_t color) {
    uint8_t attr = ui_attr_for_color(color);
    while (*text && x < UI_MAP_W) {
        ui_map_put(x, y, ui_tile_for_char(*text, color), attr);
        x++;
        text++;
    }
    return x;
}

static uint8_t ui_draw_number_tiles(uint8_t x, uint8_t y, int16_t value, uint8_t color) {
    char digits[7];
    uint8_t count = 0u, i;
    uint16_t v;
    if (value < 0) {
        if (x < UI_MAP_W) ui_map_put(x++, y, ui_tile_for_char('-', color), ui_attr_for_color(color));
        v = (uint16_t)(-value);
    } else v = (uint16_t)value;
    if (v == 0u) {
        if (x < UI_MAP_W) ui_map_put(x++, y, ui_tile_for_char('0', color), ui_attr_for_color(color));
        return x;
    }
    // This path only runs when a UI value changes, not every rendered frame.
    while (v && count < 6u) { digits[count++] = (char)('0' + (v % 10u)); v /= 10u; }
    for (i = count; i != 0u && x < UI_MAP_W; ) {
        i--;
        ui_map_put(x++, y, ui_tile_for_char(digits[i], color), ui_attr_for_color(color));
    }
    return x;
}

static void gb3d_ui_mark_dirty(void) { gb3d_ui_dirty = 1u; }

static void gb3d_ui_set_value(uint8_t index, int16_t value) {
    if (index >= SCENE_UI_COUNT) return;
    if (scene_ui[index].value != value) { scene_ui[index].value = value; gb3d_ui_mark_dirty(); }
}

static void gb3d_ui_add_value(uint8_t index, int16_t delta) {
    if (index >= SCENE_UI_COUNT) return;
    scene_ui[index].value += delta;
    gb3d_ui_mark_dirty();
}

static void gb3d_ui_set_max(uint8_t index, int16_t value) {
    if (index >= SCENE_UI_COUNT) return;
    if (value < 1) value = 1;
    if (scene_ui[index].max_value != value) { scene_ui[index].max_value = value; gb3d_ui_mark_dirty(); }
}

static void gb3d_ui_set_element_visible(uint8_t index, uint8_t visible) {
    if (index >= SCENE_UI_COUNT) return;
    visible = visible ? 1u : 0u;
    if (scene_ui[index].visible != visible) { scene_ui[index].visible = visible; gb3d_ui_mark_dirty(); }
}

static void gb3d_ui_toggle_element(uint8_t index) {
    if (index >= SCENE_UI_COUNT) return;
    scene_ui[index].visible ^= 1u;
    gb3d_ui_mark_dirty();
}

static void gb3d_window_set_visible(uint8_t visible) {
    gb3d_window_visible = visible ? 1u : 0u;
    gb3d_ui_upload_pending = 1u;
}

static void gb3d_window_toggle_visible(void) {
    gb3d_window_visible ^= 1u;
    gb3d_ui_upload_pending = 1u;
}

static void gb3d_window_move(uint8_t x, uint8_t y) {
    if (x > 159u) x = 159u;
    if (y > 143u) y = 143u;
    gb3d_window_x = x;
    gb3d_window_y = y;
    gb3d_ui_upload_pending = 1u;
}

static void gb3d_ui_prepare(void) {
    uint16_t i;
    uint8_t n, ix, width, filled, endx;
    int16_t value, maxv;
    uint8_t bg_color, bg_pal, bg_shade, bg_tile, bg_attr;

    if (!gb3d_ui_dirty) return;

    bg_color = SCENE_WINDOW_BG_COLOR & 15u;
    bg_pal = sky_palette_for_color[bg_color] & 7u;
    bg_shade = sky_shade_for_color[bg_color];
    bg_tile = (uint8_t)(UI_SOLID_BASE + bg_shade);
    bg_attr = (uint8_t)(BKGF_BANK1 | bg_pal);

    for (i = 0u; i < UI_MAP_SIZE; i++) {
        window_tilemap[i] = bg_tile;
        window_attributes[i] = bg_attr;
    }

    for (n = 0u; n < SCENE_UI_COUNT; n++) {
        if (!scene_ui[n].visible) continue;
        if (scene_ui[n].type == 0u) {
            endx = ui_draw_text_tiles(scene_ui[n].tile_x, scene_ui[n].tile_y, scene_ui[n].text, scene_ui[n].color);
            if (scene_ui[n].show_value && endx < UI_MAP_W) {
                if (endx < UI_MAP_W) endx++;
                ui_draw_number_tiles(endx, scene_ui[n].tile_y, scene_ui[n].value, scene_ui[n].color);
            }
        } else {
            width = scene_ui[n].width;
            if (width > UI_MAP_W) width = UI_MAP_W;
            maxv = scene_ui[n].max_value;
            if (maxv < 1) maxv = 1;
            value = scene_ui[n].value;
            if (value < 0) value = 0;
            if (value > maxv) value = maxv;
            filled = (uint8_t)(((uint16_t)value * width) / (uint16_t)maxv);
            for (ix = 0u; ix < width && (uint8_t)(scene_ui[n].tile_x + ix) < UI_MAP_W; ix++) {
                uint8_t c = ix < filled ? scene_ui[n].color : scene_ui[n].bg_color;
                uint8_t sh = sky_shade_for_color[c & 15u];
                ui_map_put((uint8_t)(scene_ui[n].tile_x + ix), scene_ui[n].tile_y,
                    (uint8_t)(UI_SOLID_BASE + sh), ui_attr_for_color(c));
            }
        }
    }

    gb3d_ui_dirty = 0u;
    gb3d_ui_upload_pending = 1u;
}

static void gb3d_ui_upload(void) {
    if (!gb3d_ui_upload_pending) return;
    if (!gb3d_window_visible) {
        HIDE_WIN;
        gb3d_ui_upload_pending = 0u;
        return;
    }

    move_win((uint8_t)(gb3d_window_x + 7u), gb3d_window_y);
    VBK_REG = VBK_TILES;
    set_win_tiles(0u, 0u, UI_MAP_W, UI_MAP_H, window_tilemap);
    VBK_REG = VBK_ATTRIBUTES;
    set_win_tiles(0u, 0u, UI_MAP_W, UI_MAP_H, window_attributes);
    VBK_REG = VBK_TILES;
    SHOW_WIN;
    gb3d_ui_upload_pending = 0u;
}

static void init_window_ui(void) {
    init_window_tiles();
    gb3d_window_visible = SCENE_WINDOW_ENABLED;
    gb3d_window_x = SCENE_WINDOW_X;
    gb3d_window_y = SCENE_WINDOW_Y;
    gb3d_ui_dirty = 1u;
    gb3d_ui_upload_pending = 1u;
    gb3d_ui_prepare();
    gb3d_ui_upload();
}

// Profiler rendering uses the hardware Window layer too. There is no
// framebuffer-text path in 1.2; debug text can no longer corrupt or recolor
// the 3D tilemap.

// ============================================================
// TRIG / CAMERA CACHE
// ============================================================

static const int8_t sin_table[64] = {
       0,  12,  25,  37,  49,  60,  71,  81,
      90,  98, 106, 112, 117, 122, 125, 126,
     127, 126, 125, 122, 117, 112, 106,  98,
      90,  81,  71,  60,  49,  37,  25,  12,
       0, -12, -25, -37, -49, -60, -71, -81,
     -90, -98,-106,-112,-117,-122,-125,-126,
    -127,-126,-125,-122,-117,-112,-106, -98,
     -90, -81, -71, -60, -49, -37, -25, -12
};

static int8_t isin(uint8_t angle) { return sin_table[angle & 63u]; }
static int8_t icos(uint8_t angle) { return sin_table[(angle + 16u) & 63u]; }

static int8_t camera_sy;
static int8_t camera_cy;
static int8_t camera_sp;
static int8_t camera_cp;
static int16_t camera_fast_x;
static int16_t camera_fast_y;
static int16_t camera_fast_z;

// ============================================================
// QUARTER-SQUARE FAST MULTIPLY
// ============================================================
// The SM83 has no hardware multiply.  GB3D's hot path mostly needs a
// signed 16-bit coordinate multiplied by a signed 8-bit trig/projection
// value.  Split the coordinate into two bytes, then use the identity:
//
//     a*b = Q(a+b) - Q(|a-b|),  Q(n)=floor(n*n/4)
//
// for each unsigned 8x8 partial product.  a+b is at most 382, so the
// whole table fits in 383 uint16_t entries (766 bytes) without overflow.
// Unlike 1.1's experimental large table this is exact for the full input
// range and never falls back to SDCC's software multiply helper.
#define GB3D_QS_INDEX_MAX 382u
static const uint16_t gb3d_quarter_square[GB3D_QS_INDEX_MAX + 1u] = {
    0u, 0u, 1u, 2u, 4u, 6u, 9u, 12u, 16u, 20u, 25u, 30u, 36u, 42u, 49u, 56u,
    64u, 72u, 81u, 90u, 100u, 110u, 121u, 132u, 144u, 156u, 169u, 182u, 196u, 210u, 225u, 240u,
    256u, 272u, 289u, 306u, 324u, 342u, 361u, 380u, 400u, 420u, 441u, 462u, 484u, 506u, 529u, 552u,
    576u, 600u, 625u, 650u, 676u, 702u, 729u, 756u, 784u, 812u, 841u, 870u, 900u, 930u, 961u, 992u,
    1024u, 1056u, 1089u, 1122u, 1156u, 1190u, 1225u, 1260u, 1296u, 1332u, 1369u, 1406u, 1444u, 1482u, 1521u, 1560u,
    1600u, 1640u, 1681u, 1722u, 1764u, 1806u, 1849u, 1892u, 1936u, 1980u, 2025u, 2070u, 2116u, 2162u, 2209u, 2256u,
    2304u, 2352u, 2401u, 2450u, 2500u, 2550u, 2601u, 2652u, 2704u, 2756u, 2809u, 2862u, 2916u, 2970u, 3025u, 3080u,
    3136u, 3192u, 3249u, 3306u, 3364u, 3422u, 3481u, 3540u, 3600u, 3660u, 3721u, 3782u, 3844u, 3906u, 3969u, 4032u,
    4096u, 4160u, 4225u, 4290u, 4356u, 4422u, 4489u, 4556u, 4624u, 4692u, 4761u, 4830u, 4900u, 4970u, 5041u, 5112u,
    5184u, 5256u, 5329u, 5402u, 5476u, 5550u, 5625u, 5700u, 5776u, 5852u, 5929u, 6006u, 6084u, 6162u, 6241u, 6320u,
    6400u, 6480u, 6561u, 6642u, 6724u, 6806u, 6889u, 6972u, 7056u, 7140u, 7225u, 7310u, 7396u, 7482u, 7569u, 7656u,
    7744u, 7832u, 7921u, 8010u, 8100u, 8190u, 8281u, 8372u, 8464u, 8556u, 8649u, 8742u, 8836u, 8930u, 9025u, 9120u,
    9216u, 9312u, 9409u, 9506u, 9604u, 9702u, 9801u, 9900u, 10000u, 10100u, 10201u, 10302u, 10404u, 10506u, 10609u, 10712u,
    10816u, 10920u, 11025u, 11130u, 11236u, 11342u, 11449u, 11556u, 11664u, 11772u, 11881u, 11990u, 12100u, 12210u, 12321u, 12432u,
    12544u, 12656u, 12769u, 12882u, 12996u, 13110u, 13225u, 13340u, 13456u, 13572u, 13689u, 13806u, 13924u, 14042u, 14161u, 14280u,
    14400u, 14520u, 14641u, 14762u, 14884u, 15006u, 15129u, 15252u, 15376u, 15500u, 15625u, 15750u, 15876u, 16002u, 16129u, 16256u,
    16384u, 16512u, 16641u, 16770u, 16900u, 17030u, 17161u, 17292u, 17424u, 17556u, 17689u, 17822u, 17956u, 18090u, 18225u, 18360u,
    18496u, 18632u, 18769u, 18906u, 19044u, 19182u, 19321u, 19460u, 19600u, 19740u, 19881u, 20022u, 20164u, 20306u, 20449u, 20592u,
    20736u, 20880u, 21025u, 21170u, 21316u, 21462u, 21609u, 21756u, 21904u, 22052u, 22201u, 22350u, 22500u, 22650u, 22801u, 22952u,
    23104u, 23256u, 23409u, 23562u, 23716u, 23870u, 24025u, 24180u, 24336u, 24492u, 24649u, 24806u, 24964u, 25122u, 25281u, 25440u,
    25600u, 25760u, 25921u, 26082u, 26244u, 26406u, 26569u, 26732u, 26896u, 27060u, 27225u, 27390u, 27556u, 27722u, 27889u, 28056u,
    28224u, 28392u, 28561u, 28730u, 28900u, 29070u, 29241u, 29412u, 29584u, 29756u, 29929u, 30102u, 30276u, 30450u, 30625u, 30800u,
    30976u, 31152u, 31329u, 31506u, 31684u, 31862u, 32041u, 32220u, 32400u, 32580u, 32761u, 32942u, 33124u, 33306u, 33489u, 33672u,
    33856u, 34040u, 34225u, 34410u, 34596u, 34782u, 34969u, 35156u, 35344u, 35532u, 35721u, 35910u, 36100u, 36290u, 36481u,
};

static uint16_t gb3d_fast_mul_u8(uint8_t a, uint8_t b) {
    uint16_t sum = (uint16_t)a + (uint16_t)b;
    uint16_t diff = (a >= b) ? (uint16_t)(a - b) : (uint16_t)(b - a);
    return (uint16_t)(gb3d_quarter_square[sum] - gb3d_quarter_square[diff]);
}

static int16_t gb3d_fast_mul(int16_t a, int16_t b) {
    uint16_t ua = (uint16_t)a;
    uint8_t ub = (uint8_t)b;
    uint8_t negative = (uint8_t)((a < 0) ^ (b < 0));
    uint16_t lo, hi, product;

    // Absolute values in unsigned two's-complement space avoid the -32768
    // signed-negation corner case on a 16-bit C implementation.
    if (a < 0) ua = (uint16_t)(0u - ua);
    if (b < 0) ub = (uint8_t)(0u - ub);

    lo = gb3d_fast_mul_u8((uint8_t)ua, ub);
    if ((ua & 0xFF00u) == 0u) {
        // Most local vertices, projected cross products, and movement math
        // fit in one byte, so skip the second pair of ROM lookups entirely.
        product = lo;
    } else {
        hi = gb3d_fast_mul_u8((uint8_t)(ua >> 8), ub);
        product = (uint16_t)(lo + (uint16_t)(hi << 8));
    }
    if (negative) product = (uint16_t)(0u - product);
    return (int16_t)product;
}

static void prepare_camera_transform(void) {
    int16_t tx, ty, tz;
#if SCENE_FIXED_CAMERA_FAST
    // Fixed-camera scenes bake R(world) into static render geometry on the PC.
    // Only R(camera) is calculated here once per rendered frame.
    camera.yaw = SCENE_FIXED_CAMERA_YAW;
    camera.pitch = SCENE_FIXED_CAMERA_PITCH;
#endif
    camera_sy = isin(camera.yaw);
    camera_cy = icos(camera.yaw);
    camera_sp = isin((uint8_t)camera.pitch);
    camera_cp = icos((uint8_t)camera.pitch);

#if SCENE_FIXED_CAMERA_FAST
    tx = (gb3d_fast_mul(camera.x, camera_cy) - gb3d_fast_mul(camera.z, camera_sy)) >> 7;
    tz = (gb3d_fast_mul(camera.x, camera_sy) + gb3d_fast_mul(camera.z, camera_cy)) >> 7;
    ty = (gb3d_fast_mul(camera.y, camera_cp) - gb3d_fast_mul(tz, camera_sp)) >> 7;
    camera_fast_z = (gb3d_fast_mul(camera.y, camera_sp) + gb3d_fast_mul(tz, camera_cp)) >> 7;
    camera_fast_x = tx;
    camera_fast_y = ty;
#else
    camera_fast_x = camera.x;
    camera_fast_y = camera.y;
    camera_fast_z = camera.z;
#endif
}

// ============================================================
// BUILT-IN PLATFORMER PLAYER
// ============================================================
//
// The player lives in the 3D world but is displayed using two GBC 8x16
// hardware sprites (a 16x16 metasprite). Sprite tiles live in VRAM bank 1,
// leaving bank 0's 256 pre-generated framebuffer patterns untouched.
//
// Collision is intentionally tiny and cheap: objects marked Solid use the
// axis-aligned bounds stored in Model3D. Static rotations baked by the exporter
// therefore also get baked collision bounds. Runtime-rotating solid objects
// keep axis-aligned unrotated bounds.

typedef struct {
    int16_t x;
    int16_t y;  // feet position
    int16_t z;
    int8_t vy;
    uint8_t grounded;
    uint8_t visible;
} GB3DPlayer;

static GB3DPlayer player = {0, 0, 0, 0, 0u, 1u};

// GBDK's move_sprite() is inline in current releases, but the hot metasprite
// update is still clearer and smaller when we write the documented Shadow OAM
// fields directly. The default VBlank ISR DMA-copies shadow_OAM to real OAM.
#define GB3D_OAM_MOVE(index, px, py) do { \
    shadow_OAM[(index)].x = (uint8_t)(px); \
    shadow_OAM[(index)].y = (uint8_t)(py); \
} while (0)

// GBScript builtins use accessors instead of referring to the private player
// global from scene_gb3d.h. The generated header is included before the
// player storage is defined, so direct `player.x` references would not compile.
static int16_t gb3d_player_get_x(void) { return player.x; }
static int16_t gb3d_player_get_y(void) { return player.y; }
static int16_t gb3d_player_get_z(void) { return player.z; }

// The 32x32 player art is generated into scene_gb3d.h as 16 native 8x8
// tile patterns. In 8x16 mode, those tiles are arranged in consecutive
// top/bottom pairs so eight OAM entries can display the image. In 8x8 mode,
// all sixteen OAM entries are used directly. Color index 0 stays transparent.

static void hide_player_sprite(void) {
    uint8_t i;
    for (i = 0u; i < SCENE_PLAYER_SPRITE_OAM_COUNT; i++)
        GB3D_OAM_MOVE(i, 0u, 0u);
}

static void init_player_sprite(void) {
    uint8_t i;

#if SCENE_PLAYER_SPRITE_8X16
    SPRITES_8x16;
#else
    SPRITES_8x8;
#endif

    // Background framebuffer patterns occupy bank 0 tiles 0..255. Player art
    // gets its own full 16-tile set in VRAM bank 1.
    VBK_REG = VBK_BANK_1;
    set_sprite_data(0u, SCENE_PLAYER_SPRITE_TILE_COUNT, scene_player_sprite_tiles);
    VBK_REG = VBK_BANK_0;

    set_sprite_palette(0u, 1u, scene_player_sprite_palette);

    for (i = 0u; i < SCENE_PLAYER_SPRITE_OAM_COUNT; i++) {
#if SCENE_PLAYER_SPRITE_8X16
        // 8x16 sprites must use even tile numbers; hardware implicitly uses
        // the following odd tile for the lower half.
        set_sprite_tile(i, (uint8_t)(i << 1));
#else
        set_sprite_tile(i, i);
#endif
        set_sprite_prop(i, OAMF_BANK1 | OAMF_CGB_PAL0);
    }

    hide_player_sprite();
    SHOW_SPRITES;
}

static uint8_t player_xz_overlaps_object(int16_t x, int16_t z, const SceneObject3D *obj) {
    int16_t ominx, omaxx, ominz, omaxz;
    const Model3D *m;

    if (!obj->solid || !obj->visible || obj->model == 0)
        return 0u;

    m = obj->model;
    ominx = obj->x + m->min_x;
    omaxx = obj->x + m->max_x;
    ominz = obj->z + m->min_z;
    omaxz = obj->z + m->max_z;

    if ((x + SCENE_PLAYER_HALF_WIDTH) <= ominx) return 0u;
    if ((x - SCENE_PLAYER_HALF_WIDTH) >= omaxx) return 0u;
    if ((z + SCENE_PLAYER_HALF_WIDTH) <= ominz) return 0u;
    if ((z - SCENE_PLAYER_HALF_WIDTH) >= omaxz) return 0u;
    return 1u;
}

static uint8_t player_body_overlaps_object_y(int16_t y, const SceneObject3D *obj) {
    int16_t ominy, omaxy;
    const Model3D *m;
    m = obj->model;
    ominy = obj->y + m->min_y;
    omaxy = obj->y + m->max_y;
    if (y >= omaxy) return 0u;
    if ((y + SCENE_PLAYER_HEIGHT) <= ominy) return 0u;
    return 1u;
}

static uint8_t player_blocked_at(int16_t x, int16_t y, int16_t z) {
    uint8_t i;
    const SceneObject3D *obj;
    for (i = 0u; i < SCENE_SOLID_COUNT; i++) {
        obj = &scene_objects[scene_solid_indices[i]];
        if (!obj->visible || obj->model == 0)
            continue;
        if (player_xz_overlaps_object(x, z, obj) && player_body_overlaps_object_y(y, obj))
            return 1u;
    }
    return 0u;
}

static void player_try_move_xz(int16_t dx, int16_t dz) {
    int16_t nx, nz;

    nx = player.x + dx;
    if (!player_blocked_at(nx, player.y, player.z))
        player.x = nx;

    nz = player.z + dz;
    if (!player_blocked_at(player.x, player.y, nz))
        player.z = nz;
}

static void gb3d_player_move(int16_t dx, int16_t dy, int16_t dz) {
    if (!SCENE_PLAYER_ENABLED) return;
    player_try_move_xz(dx, dz);
    player.y += dy;
}

static void gb3d_player_set(int16_t x, int16_t y, int16_t z) {
    if (!SCENE_PLAYER_ENABLED) return;
    player.x = x;
    player.y = y;
    player.z = z;
    player.vy = 0;
    player.grounded = 0u;
}

static void gb3d_player_jump(void) {
    if (!SCENE_PLAYER_ENABLED) return;
    if (player.grounded) {
        player.vy = (int8_t)SCENE_PLAYER_JUMP_SPEED;
        player.grounded = 0u;
    }
}

static void gb3d_player_set_visible(uint8_t visible) {
    if (!SCENE_PLAYER_ENABLED) return;
    player.visible = visible ? 1u : 0u;
    if (!player.visible) hide_player_sprite();
}

static void gb3d_player_toggle_visible(void) {
    gb3d_player_set_visible((uint8_t)!player.visible);
}

static void player_vertical_physics(void) {
    uint8_t i;
    uint8_t hit;
    int16_t old_y, new_y;
    int16_t old_head, new_head;
    int16_t surface, candidate;
    const SceneObject3D *obj;
    const Model3D *m;

    old_y = player.y;
    new_y = old_y + player.vy;

    if (player.vy > 0) {
        // Rising: stop the player's head at the lowest crossed ceiling.
        old_head = old_y + SCENE_PLAYER_HEIGHT;
        new_head = new_y + SCENE_PLAYER_HEIGHT;
        hit = 0u;
        surface = 32767;

        for (i = 0u; i < SCENE_SOLID_COUNT; i++) {
            obj = &scene_objects[scene_solid_indices[i]];
            if (!obj->visible || obj->model == 0)
                continue;
            if (!player_xz_overlaps_object(player.x, player.z, obj))
                continue;
            m = obj->model;
            candidate = obj->y + m->min_y;
            if (old_head <= candidate && new_head >= candidate && candidate < surface) {
                surface = candidate;
                hit = 1u;
            }
        }

        if (hit) {
            player.y = surface - SCENE_PLAYER_HEIGHT;
            player.vy = 0;
        } else {
            player.y = new_y;
        }
        player.grounded = 0u;
    } else {
        // Falling/still: land on the highest crossed platform top or ground plane.
        hit = 0u;
        surface = SCENE_PLAYER_GROUND_Y;
        if (old_y >= SCENE_PLAYER_GROUND_Y && new_y <= SCENE_PLAYER_GROUND_Y)
            hit = 1u;

        for (i = 0u; i < SCENE_SOLID_COUNT; i++) {
            obj = &scene_objects[scene_solid_indices[i]];
            if (!obj->visible || obj->model == 0)
                continue;
            if (!player_xz_overlaps_object(player.x, player.z, obj))
                continue;
            m = obj->model;
            candidate = obj->y + m->max_y;
            if (old_y >= candidate && new_y <= candidate && (!hit || candidate > surface)) {
                surface = candidate;
                hit = 1u;
            }
        }

        if (hit) {
            player.y = surface;
            player.vy = 0;
            player.grounded = 1u;
        } else {
            player.y = new_y;
            player.grounded = 0u;
        }
    }

    if (!player.grounded) {
        if (player.vy > -120)
            player.vy = (int8_t)(player.vy - SCENE_PLAYER_GRAVITY);
    }
}

static void update_platformer_input(uint8_t keys, uint8_t pressed) {
    int16_t dx, dz;
    int8_t s, c;
    int8_t amount;

    if (keys & J_SELECT) {
#if !SCENE_FIXED_CAMERA_FAST
        if (keys & J_LEFT) camera.yaw--;
        if (keys & J_RIGHT) camera.yaw++;
        if ((keys & J_UP) && camera.pitch < 12) camera.pitch++;
        if ((keys & J_DOWN) && camera.pitch > -12) camera.pitch--;
#endif
    } else {
        dx = 0;
        dz = 0;
        s = isin(camera.yaw);
        c = icos(camera.yaw);
        amount = (int8_t)SCENE_PLAYER_SPEED;

        if (keys & J_UP) {
            dx += gb3d_fast_mul(s, amount) >> 7;
            dz += gb3d_fast_mul(c, amount) >> 7;
        }
        if (keys & J_DOWN) {
            dx -= gb3d_fast_mul(s, amount) >> 7;
            dz -= gb3d_fast_mul(c, amount) >> 7;
        }
        if (keys & J_LEFT) {
            dx -= gb3d_fast_mul(c, amount) >> 7;
            dz += gb3d_fast_mul(s, amount) >> 7;
        }
        if (keys & J_RIGHT) {
            dx += gb3d_fast_mul(c, amount) >> 7;
            dz -= gb3d_fast_mul(s, amount) >> 7;
        }

        if (dx || dz)
            player_try_move_xz(dx, dz);
    }

    if (pressed & J_A)
        gb3d_player_jump();
}

static void update_platformer_camera(void) {
    int8_t s, c;
    s = isin(camera.yaw);
    c = icos(camera.yaw);
    camera.x = player.x - (gb3d_fast_mul(s, SCENE_PLAYER_CAMERA_DISTANCE) >> 7);
    camera.z = player.z - (gb3d_fast_mul(c, SCENE_PLAYER_CAMERA_DISTANCE) >> 7);
    camera.y = player.y + SCENE_PLAYER_CAMERA_HEIGHT;
}

static void init_platformer_player(void) {
    player.x = SCENE_PLAYER_X;
    player.y = SCENE_PLAYER_Y;
    player.z = SCENE_PLAYER_Z;
    player.vy = 0;
    player.grounded = 0u;
    player.visible = SCENE_PLAYER_VISIBLE ? 1u : 0u;
    camera.pitch = SCENE_PLAYER_CAMERA_PITCH;

    if (player.y <= SCENE_PLAYER_GROUND_Y) {
        player.y = SCENE_PLAYER_GROUND_Y;
        player.grounded = 1u;
    }
    update_platformer_camera();
}

static void update_player_sprite(void) {
    Vec3 world;
    Vec3 cam;
    Vec2 p;
    int16_t cx, cy;
    int16_t base_x, base_y;
    int16_t ox, oy;
    uint8_t i;
    uint8_t col;
    uint8_t row;

    if (!SCENE_PLAYER_ENABLED || !player.visible) {
        hide_player_sprite();
        return;
    }

    // Project the middle of the character. The 32x32 hardware metasprite stays
    // the same screen size regardless of distance: classic sprite-in-3D style.
    world.x = player.x;
    world.y = player.y + (SCENE_PLAYER_HEIGHT >> 1);
    world.z = player.z;
    world_to_camera(&world, &cam);
    project_vertex(&cam, &p);

    if (!p.visible) {
        hide_player_sprite();
        return;
    }

    // Logical renderer coordinates are 4 physical LCD pixels each.
    cx = ((int16_t)p.x << 2);
    cy = ((int16_t)p.y << 2);

    // Completely outside the 160x144 screen? Half-size is 16 pixels.
    if (cx <= -16 || cx >= 176 || cy <= -16 || cy >= 160) {
        hide_player_sprite();
        return;
    }

    // Game Boy OAM coordinates are screen X+8 and screen Y+16. A centered
    // 32x32 sprite therefore starts at OAM (centerX-8, centerY). Negative
    // values wrapping to the far side are okay for fully clipped edge chunks.
    base_x = cx - 8;
    base_y = cy;

    for (i = 0u; i < SCENE_PLAYER_SPRITE_OAM_COUNT; i++) {
        col = i & 3u;
        row = i >> 2;
        ox = base_x + ((int16_t)col << 3);
#if SCENE_PLAYER_SPRITE_8X16
        oy = base_y + ((int16_t)row << 4);
#else
        oy = base_y + ((int16_t)row << 3);
#endif
        GB3D_OAM_MOVE(i, ox, oy);
    }
}

// ============================================================
// SCRIPTABLE NPC HARDWARE METASPRITES
// ============================================================

static uint8_t npc_oam_base(void) {
#if SCENE_PLAYER_ENABLED
    return SCENE_PLAYER_SPRITE_OAM_COUNT;
#else
    return 0u;
#endif
}

static uint8_t npc_tile_base(void) {
#if SCENE_PLAYER_ENABLED
    return SCENE_PLAYER_SPRITE_TILE_COUNT;
#else
    return 0u;
#endif
}

static uint8_t npc_palette_base(void) {
#if SCENE_PLAYER_ENABLED
    return 1u;
#else
    return 0u;
#endif
}

static void hide_npc_sprite(uint8_t index) {
    uint8_t i;
    uint8_t base = (uint8_t)(npc_oam_base() + index * SCENE_NPC_SPRITE_OAM_COUNT);
    for (i = 0u; i < SCENE_NPC_SPRITE_OAM_COUNT; i++)
        GB3D_OAM_MOVE((uint8_t)(base + i), 0u, 0u);
}

static void gb3d_npc_set_visible(uint8_t index, uint8_t visible) {
    if (index >= SCENE_NPC_COUNT) return;
    npcs[index].visible = visible ? 1u : 0u;
    if (!npcs[index].visible) hide_npc_sprite(index);
}

static void gb3d_npc_toggle_visible(uint8_t index) {
    if (index >= SCENE_NPC_COUNT) return;
    gb3d_npc_set_visible(index, (uint8_t)!npcs[index].visible);
}

static void init_npc_sprites(void) {
    uint8_t n, i;
    uint8_t oam_base;
    uint8_t tile_base;
    uint8_t pal_base;

    oam_base = npc_oam_base();
    tile_base = npc_tile_base();
    pal_base = npc_palette_base();

    for (n = 0u; n < SCENE_NPC_COUNT; n++) {
        npcs[n].x = scene_npc_start_x[n];
        npcs[n].y = scene_npc_start_y[n];
        npcs[n].z = scene_npc_start_z[n];
        npcs[n].height = scene_npc_height[n];
        npcs[n].visible = scene_npc_visible[n];

        VBK_REG = VBK_BANK_1;
        set_sprite_data((uint8_t)(tile_base + n * 16u), 16u, scene_npc_sprite_tiles[n]);
        VBK_REG = VBK_BANK_0;
        set_sprite_palette((uint8_t)(pal_base + n), 1u, scene_npc_sprite_palettes[n]);

        for (i = 0u; i < SCENE_NPC_SPRITE_OAM_COUNT; i++) {
#if SCENE_SPRITE_8X16
            set_sprite_tile((uint8_t)(oam_base + n * SCENE_NPC_SPRITE_OAM_COUNT + i),
                (uint8_t)(tile_base + n * 16u + (i << 1)));
#else
            set_sprite_tile((uint8_t)(oam_base + n * SCENE_NPC_SPRITE_OAM_COUNT + i),
                (uint8_t)(tile_base + n * 16u + i));
#endif
            set_sprite_prop((uint8_t)(oam_base + n * SCENE_NPC_SPRITE_OAM_COUNT + i),
                (uint8_t)(OAMF_BANK1 | ((pal_base + n) & 7u)));
        }
        hide_npc_sprite(n);
    }
    if (SCENE_NPC_COUNT) SHOW_SPRITES;
}

static void update_npc_sprites(void) {
    uint8_t n, i, col, row;
    uint8_t base;
    Vec3 world, cam;
    Vec2 p;
    int16_t cx, cy, base_x, base_y, ox, oy;

    for (n = 0u; n < SCENE_NPC_COUNT; n++) {
        if (!npcs[n].visible) {
            hide_npc_sprite(n);
            continue;
        }
        world.x = npcs[n].x;
        world.y = npcs[n].y + (npcs[n].height >> 1);
        world.z = npcs[n].z;
        world_to_camera(&world, &cam);
        project_vertex(&cam, &p);
        if (!p.visible) {
            hide_npc_sprite(n);
            continue;
        }
        cx = ((int16_t)p.x << 2);
        cy = ((int16_t)p.y << 2);
        if (cx <= -16 || cx >= 176 || cy <= -16 || cy >= 160) {
            hide_npc_sprite(n);
            continue;
        }
        base_x = cx - 8;
        base_y = cy;
        base = (uint8_t)(npc_oam_base() + n * SCENE_NPC_SPRITE_OAM_COUNT);
        for (i = 0u; i < SCENE_NPC_SPRITE_OAM_COUNT; i++) {
            col = i & 3u;
            row = i >> 2;
            ox = base_x + ((int16_t)col << 3);
#if SCENE_SPRITE_8X16
            oy = base_y + ((int16_t)row << 4);
#else
            oy = base_y + ((int16_t)row << 3);
#endif
            GB3D_OAM_MOVE((uint8_t)(base + i), ox, oy);
        }
    }
}

// ============================================================
// STATIC TILE PATTERNS
// ============================================================

static void init_framebuffer_memory(void) {
    uint16_t addr;
    addr = (uint16_t)framebuffer_storage;
    addr = (uint16_t)((addr + 15u) & 0xFFF0u);
    framebuffer_tilemap = (uint8_t *)addr;
    framebuffer_attributes = framebuffer_tilemap + FB_MAP_SIZE;

    // Hidden columns are never rasterized. Initialize them once so a full
    // 32-wide DMA map is deterministic; visible columns are cleared per frame.
    fast_fill_bytes(framebuffer_tilemap, FB_MAP_SIZE, 0u);
    fast_fill_bytes(framebuffer_attributes, FB_MAP_SIZE, 0u);
}

static void init_framebuffer_tiles(void) {
    uint16_t base;
    uint16_t offset;
    uint8_t i;
    uint8_t py;
    uint8_t tile_id;
    uint8_t tl, tr, bl, br;
    uint8_t left, right;
    uint8_t plane0, plane1;

    VBK_REG = VBK_BANK_0;

    for (base = 0u; base < 256u; base += 16u) {
        for (i = 0u; i < 16u; i++) {
            tile_id = (uint8_t)(base + i);
            tl = tile_id & 3u;
            tr = (tile_id >> 2) & 3u;
            bl = (tile_id >> 4) & 3u;
            br = (tile_id >> 6) & 3u;
            offset = ((uint16_t)i << 4);

            for (py = 0u; py < 8u; py++) {
                if (py < 4u) { left = tl; right = tr; }
                else { left = bl; right = br; }

                plane0 = 0u;
                plane1 = 0u;
                if (left & 1u)  plane0 |= 0xF0u;
                if (right & 1u) plane0 |= 0x0Fu;
                if (left & 2u)  plane1 |= 0xF0u;
                if (right & 2u) plane1 |= 0x0Fu;

                tile_batch[offset + ((uint16_t)py << 1)] = plane0;
                tile_batch[offset + ((uint16_t)py << 1) + 1u] = plane1;
            }
        }
        set_bkg_data((uint8_t)base, 16u, tile_batch);
    }
}

static void fast_fill_bytes(uint8_t *ptr, uint16_t count, uint8_t value) {
    gb3d_asm_ptr = ptr;
    gb3d_asm_count = count;
    gb3d_asm_value = value;
    gb3d_fill_bytes_asm();
}

// Start each frame with the selected sky material. Attributes are reset too,
// because blank tiles must not inherit a material palette from the prior frame.
static void fb_begin_frame(void) {
    // Clear only the 20 visible columns of each 32-byte hardware-map row.
    // Keep visible clear bandwidth low while retaining contiguous GDMA storage.
    gb3d_asm_ptr = framebuffer_tilemap;
    gb3d_asm_value = sky_tile_id;
    gb3d_clear_visible_asm();
    gb3d_asm_ptr = framebuffer_attributes;
    gb3d_asm_value = sky_palette;
    gb3d_clear_visible_asm();
}

// Debug pixel writer used by wireframe mode. Solid triangles use assembly.
static void fb_pixel(uint8_t x, uint8_t y, uint8_t palette, uint8_t shade) {
    uint16_t idx;
    uint8_t shift;
    uint8_t mask;
    uint8_t old;

    if (x >= FB_WIDTH || y >= FB_HEIGHT)
        return;

    idx = (uint16_t)(y >> 1) * FB_MAP_W + (x >> 1);
    shift = (uint8_t)(((y & 1u) << 2) + ((x & 1u) << 1));
    mask = (uint8_t)(3u << shift);
    old = framebuffer_tilemap[idx];
    framebuffer_tilemap[idx] = (uint8_t)((old & (uint8_t)~mask) | ((shade & 3u) << shift));
    framebuffer_attributes[idx] = palette & 7u;
}

static void fb_line(
    int16_t x0, int16_t y0,
    int16_t x1, int16_t y1,
    uint8_t palette, uint8_t shade
) {
    int16_t dx, dy, sx, sy, err, e2;

    dx = (x0 < x1) ? (x1 - x0) : (x0 - x1);
    sx = (x0 < x1) ? 1 : -1;
    dy = (y0 < y1) ? (y0 - y1) : (y1 - y0);
    sy = (y0 < y1) ? 1 : -1;
    err = dx + dy;

    while (1) {
        if ((uint16_t)x0 < FB_WIDTH && (uint16_t)y0 < FB_HEIGHT)
            fb_pixel((uint8_t)x0, (uint8_t)y0, palette, shade);
        if (x0 == x1 && y0 == y1)
            break;
        e2 = err << 1;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
    }
}

static int16_t clamp_i16(int16_t v, int16_t lo, int16_t hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

// Triangle edge quotient/remainder setup moved to gb3d_render.s.
// The queued projection domain is only x=-32..71 and y=-32..67, so the ASM
// setup can use a specialized register-only 7-round restoring divide after
// skipping the known-zero top input bit.

// Triangle queue entries already contain bounded int8_t projected vertices.
// triangle_queue_flush() copies those bytes straight into the ASM interface;
// Y sorting and all edge setup happen in gb3d_render.s.

static void gb3d_gdma_to_vram(const uint8_t *source, uint16_t destination, uint8_t blocks_minus_one) {
    uint16_t src;
    src = (uint16_t)source;
    HDMA1_REG = (uint8_t)(src >> 8);
    HDMA2_REG = (uint8_t)(src & 0xF0u);
    HDMA3_REG = (uint8_t)((destination >> 8) & 0x1Fu);
    HDMA4_REG = (uint8_t)(destination & 0xF0u);
    HDMA5_REG = blocks_minus_one; // bit 7 clear = General-Purpose DMA
}

static void upload_framebuffer(void) {
    // V12 FAST PATH: each RAM map already has the hardware's native 32-tile
    // stride, so the CGB DMA controller can copy 576 contiguous bytes directly
    // to $9800. At 36 x 16-byte blocks per bank, both maps fit comfortably in
    // the VBlank window. CPU execution pauses only for the actual DMA transfer.
    if (_cpu == CGB_TYPE) {
        VBK_REG = VBK_BANK_0;
        gb3d_gdma_to_vram(framebuffer_tilemap, 0x9800u, (uint8_t)(FB_DMA_BLOCKS - 1u));
        VBK_REG = VBK_BANK_1;
        gb3d_gdma_to_vram(framebuffer_attributes, 0x9800u, (uint8_t)(FB_DMA_BLOCKS - 1u));
        VBK_REG = VBK_BANK_0;
    } else {
        // Defensive fallback. GB3D targets CGB, but this keeps non-CGB startup
        // from doing undefined CGB-DMA register writes.
        VBK_REG = VBK_BANK_0;
        set_bkg_tiles(0u, 0u, FB_MAP_W, FB_TILE_H, framebuffer_tilemap);
    }
}

// ============================================================
// 3D MATH
// ============================================================

// Projection reciprocals are generated ahead of time. This used to consume
// 256 bytes of WRAM and run a software division loop during startup. The PC
// can know all 256 values, so the cartridge now stores them directly in ROM.
static const uint8_t projection_scale[256] = {
    0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 56u, 49u, 44u, 40u, 37u, 34u, 32u, 29u,
    28u, 26u, 24u, 23u, 22u, 21u, 20u, 19u, 18u, 17u, 17u, 16u, 16u, 15u, 14u, 14u,
    14u, 13u, 13u, 12u, 12u, 12u, 11u, 11u, 11u, 10u, 10u, 10u, 10u, 9u, 9u, 9u,
    9u, 9u, 8u, 8u, 8u, 8u, 8u, 8u, 8u, 7u, 7u, 7u, 7u, 7u, 7u, 7u,
    7u, 6u, 6u, 6u, 6u, 6u, 6u, 6u, 6u, 6u, 6u, 5u, 5u, 5u, 5u, 5u,
    5u, 5u, 5u, 5u, 5u, 5u, 5u, 5u, 5u, 5u, 4u, 4u, 4u, 4u, 4u, 4u,
    4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u, 4u,
    4u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u,
    3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u, 3u,
    3u, 3u, 3u, 3u, 3u, 3u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u,
    2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u,
    2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u,
    2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u,
    2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u, 2u,
    2u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u,
    1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u, 1u,
};

static void world_to_camera(const Vec3 *world, Vec3 *out) {
    int16_t x, y, z, tx, ty, tz;

    x = world->x - camera.x;
    y = world->y - camera.y;
    z = world->z - camera.z;

    if (camera.yaw != 0u) {
        tx = (gb3d_fast_mul(x, camera_cy) - gb3d_fast_mul(z, camera_sy)) >> 7;
        tz = (gb3d_fast_mul(x, camera_sy) + gb3d_fast_mul(z, camera_cy)) >> 7;
        x = tx; z = tz;
    }
    if (camera.pitch != 0) {
        ty = (gb3d_fast_mul(y, camera_cp) - gb3d_fast_mul(z, camera_sp)) >> 7;
        tz = (gb3d_fast_mul(y, camera_sp) + gb3d_fast_mul(z, camera_cp)) >> 7;
        y = ty; z = tz;
    }

    out->x = x; out->y = y; out->z = z;
}

static void project_vertex(const Vec3 *in, Vec2 *out) {
    int16_t sx, sy;
    uint8_t scale;

    if (in->z < NEAR_Z || in->z > FAR_Z) {
        out->visible = 0u;
        return;
    }

    scale = projection_scale[(uint8_t)in->z];
    sx = SCREEN_CX + (gb3d_fast_mul(in->x, scale) >> 4);
    sy = SCREEN_CY - (gb3d_fast_mul(in->y, scale) >> 4);

    sx = clamp_i16(sx, PROJ_X_MIN, PROJ_X_MAX);
    sy = clamp_i16(sy, PROJ_Y_MIN, PROJ_Y_MAX);

    out->x = (int8_t)sx;
    out->y = (int8_t)sy;
    out->visible = 1u;
}

// ============================================================
// MODEL RENDERER + GLOBAL TRIANGLE BUCKETS
// ============================================================
//
// 1.1 depth-sorted faces separately inside each model, then sorted objects.
// That was both extra work and only approximately correct. 1.2 transforms one
// model at a time but copies each visible projected triangle into a compact
// frame queue. All models therefore share the same 16 depth buckets.
//
// 192 queued triangles cost 1536 bytes of WRAM. Scenes trying to submit more
// are already well beyond the sensible GBC budget; the profiler counts drops.

static Vec3 model_camera[MAX_MODEL_VERTICES];
static Vec2 model_projected[MAX_MODEL_VERTICES];

typedef struct {
    int8_t x0, y0;
    int8_t x1, y1;
    int8_t x2, y2;
    uint8_t material; // bits 0..2 palette, bits 3..4 shade
    uint8_t next;
} GB3DQueuedTriangle;

// Assembly owns queue begin/flush now, so these need link-visible names.
GB3DQueuedTriangle triangle_queue[MAX_FRAME_TRIANGLES];
uint8_t triangle_bucket_heads[DEPTH_BUCKETS];
uint8_t triangle_count;
typedef char GB3DQueueRecordMustBe8Bytes[(sizeof(GB3DQueuedTriangle) == 8u) ? 1 : -1];
void gb3d_triangle_queue_begin_asm(void);
void gb3d_triangle_queue_flush_asm(void);

#if GB3D_PROFILE
// The renderer is above the profiler implementation in this translation unit,
// so expose the cheap timer helpers here for fine-grained render timings.
static uint16_t gb3d_prof_now(void);
static uint16_t gb3d_prof_delta(uint16_t start);

static uint16_t gb3d_prof_vertices;
static uint16_t gb3d_prof_faces;
static uint16_t gb3d_prof_triangles;
static uint16_t gb3d_prof_triangles_dropped;
static uint16_t gb3d_prof_faces_offscreen;
static uint8_t gb3d_prof_objects_visible;
static uint8_t gb3d_prof_objects_culled;
static uint16_t gb3d_prof_vertex_ticks;
static uint16_t gb3d_prof_face_ticks;
static uint16_t gb3d_prof_draw_ticks;
#endif

static void triangle_queue_begin(void) {
    // Fixed 16-byte bucket reset is cheaper as one tiny SM83 loop than as C.
    gb3d_triangle_queue_begin_asm();
}

static void triangle_queue_add(
    const Vec2 *a, const Vec2 *b, const Vec2 *c,
    uint8_t palette, uint8_t shade, uint8_t depth
) {
    uint8_t index, bucket;
    GB3DQueuedTriangle *q;
    if (triangle_count >= MAX_FRAME_TRIANGLES) {
#if GB3D_PROFILE
        gb3d_prof_triangles_dropped++;
#endif
        return;
    }

    index = triangle_count++;
    q = &triangle_queue[index];
    q->x0 = a->x; q->y0 = a->y;
    q->x1 = b->x; q->y1 = b->y;
    q->x2 = c->x; q->y2 = c->y;
    q->material = (uint8_t)((palette & 7u) | ((shade & 3u) << 3));

    bucket = depth >> 4;
    if (bucket >= DEPTH_BUCKETS) bucket = DEPTH_BUCKETS - 1u;
    q->next = triangle_bucket_heads[bucket];
    triangle_bucket_heads[bucket] = index;
}

static void triangle_queue_flush(void) {
#if GB3D_PROFILE
    uint16_t prof_draw_start = gb3d_prof_now();
    // Every queued record is flushed exactly once. Count it here instead of
    // making the assembly loop touch a profiler global per triangle.
    gb3d_prof_triangles += triangle_count;
#endif

    // Bucket traversal, record address calculation, ABI copy and triangle
    // calls are all one assembly loop now. C no longer walks linked lists or
    // performs seven byte assignments for every submitted triangle.
    gb3d_triangle_queue_flush_asm();
#if GB3D_PROFILE
    gb3d_prof_draw_ticks += gb3d_prof_delta(prof_draw_start);
#endif
}

static void collect_model_triangles(
    const Model3D *model, const WorldVertex *world_vertices,
    int16_t world_x, int16_t world_y, int16_t world_z,
    uint8_t rot_x, uint8_t rot_y, uint8_t camera_baked
) {
    uint8_t i, a, b, c;
    uint8_t max_z;
    uint8_t shade;
    int16_t cross;
    int16_t abx, aby, acx, acy;
    int16_t x, y, z, tx, ty, tz;
    int16_t sx, sy;
    uint8_t scale;
    int8_t object_sx, object_cx, object_sy, object_cy;
    Vec2 *pa, *pb, *pc;
#if GB3D_PROFILE
    uint16_t prof_part_start;
#endif

    // Model vertices already live in x/y/z here. Project them directly instead
    // of writing Vec3 to WRAM, calling project_vertex(), then reading the same
    // Vec3 back. This keeps model_camera for face depth while removing a hot
    // function call and three immediate WRAM reads per transformed vertex.
#define GB3D_STORE_PROJECT_CURRENT(_i) do { \
        model_camera[(_i)].x = x; \
        model_camera[(_i)].y = y; \
        model_camera[(_i)].z = z; \
        if (z < NEAR_Z || z > FAR_Z) { \
            model_projected[(_i)].visible = 0u; \
        } else { \
            scale = projection_scale[(uint8_t)z]; \
            sx = SCREEN_CX + (gb3d_fast_mul(x, scale) >> 4); \
            sy = SCREEN_CY - (gb3d_fast_mul(y, scale) >> 4); \
            if (sx < PROJ_X_MIN) sx = PROJ_X_MIN; else if (sx > PROJ_X_MAX) sx = PROJ_X_MAX; \
            if (sy < PROJ_Y_MIN) sy = PROJ_Y_MIN; else if (sy > PROJ_Y_MAX) sy = PROJ_Y_MAX; \
            model_projected[(_i)].x = (int8_t)sx; \
            model_projected[(_i)].y = (int8_t)sy; \
            model_projected[(_i)].visible = 1u; \
        } \
    } while (0)

    if (model->vertex_count > MAX_MODEL_VERTICES || model->face_count > MAX_MODEL_FACES)
        return;

#if GB3D_PROFILE
    gb3d_prof_vertices += model->vertex_count;
    gb3d_prof_faces += model->face_count;
    prof_part_start = gb3d_prof_now();
#endif

    if (world_vertices != 0) {
        // Compiler fast path: immutable object rotation + translation were
        // already applied to every vertex on the PC. With a locked camera
        // orientation, the PC also applied that rotation, leaving only three
        // camera-origin subtracts before projection.
#if SCENE_FIXED_CAMERA_FAST
        if (camera_baked) {
            for (i = 0u; i < model->vertex_count; i++) {
                x = world_vertices[i].x - camera_fast_x;
                y = world_vertices[i].y - camera_fast_y;
                z = world_vertices[i].z - camera_fast_z;
                GB3D_STORE_PROJECT_CURRENT(i);
            }
        } else
#endif
        {
            for (i = 0u; i < model->vertex_count; i++) {
                x = world_vertices[i].x - camera.x;
                y = world_vertices[i].y - camera.y;
                z = world_vertices[i].z - camera.z;

                if (camera.yaw != 0u) {
                    tx = (gb3d_fast_mul(x, camera_cy) - gb3d_fast_mul(z, camera_sy)) >> 7;
                    tz = (gb3d_fast_mul(x, camera_sy) + gb3d_fast_mul(z, camera_cy)) >> 7;
                    x = tx; z = tz;
                }
                if (camera.pitch != 0) {
                    ty = (gb3d_fast_mul(y, camera_cp) - gb3d_fast_mul(z, camera_sp)) >> 7;
                    tz = (gb3d_fast_mul(y, camera_sp) + gb3d_fast_mul(z, camera_cp)) >> 7;
                    y = ty; z = tz;
                }

                GB3D_STORE_PROJECT_CURRENT(i);
            }
        }
    } else {
        // Dynamic fallback for objects whose transform can change at runtime.
        if (rot_x || rot_y) {
            object_sx = isin(rot_x);
            object_cx = icos(rot_x);
            object_sy = isin(rot_y);
            object_cy = icos(rot_y);
        } else {
            object_sx = 0; object_cx = 127;
            object_sy = 0; object_cy = 127;
        }

        for (i = 0u; i < model->vertex_count; i++) {
            x = model->vertices[i].x;
            y = model->vertices[i].y;
            z = model->vertices[i].z;

            if (rot_y != 0u) {
                tx = (gb3d_fast_mul(x, object_cy) - gb3d_fast_mul(z, object_sy)) >> 7;
                tz = (gb3d_fast_mul(x, object_sy) + gb3d_fast_mul(z, object_cy)) >> 7;
                x = tx; z = tz;
            }
            if (rot_x != 0u) {
                ty = (gb3d_fast_mul(y, object_cx) - gb3d_fast_mul(z, object_sx)) >> 7;
                tz = (gb3d_fast_mul(y, object_sx) + gb3d_fast_mul(z, object_cx)) >> 7;
                y = ty; z = tz;
            }

            x = x + world_x - camera.x;
            y = y + world_y - camera.y;
            z = z + world_z - camera.z;

            if (camera.yaw != 0u) {
                tx = (gb3d_fast_mul(x, camera_cy) - gb3d_fast_mul(z, camera_sy)) >> 7;
                tz = (gb3d_fast_mul(x, camera_sy) + gb3d_fast_mul(z, camera_cy)) >> 7;
                x = tx; z = tz;
            }
            if (camera.pitch != 0) {
                ty = (gb3d_fast_mul(y, camera_cp) - gb3d_fast_mul(z, camera_sp)) >> 7;
                tz = (gb3d_fast_mul(y, camera_sp) + gb3d_fast_mul(z, camera_cp)) >> 7;
                y = ty; z = tz;
            }

            GB3D_STORE_PROJECT_CURRENT(i);
        }
    }

#undef GB3D_STORE_PROJECT_CURRENT

#if GB3D_PROFILE
    gb3d_prof_vertex_ticks += gb3d_prof_delta(prof_part_start);
    prof_part_start = gb3d_prof_now();
#endif

    for (i = 0u; i < model->face_count; i++) {
        a = model->faces[i].a;
        b = model->faces[i].b;
        c = model->faces[i].c;

        if (!model_projected[a].visible || !model_projected[b].visible || !model_projected[c].visible)
            continue;

        // Palette-0xFF faces are stripped by the exporter, so no permanent
        // invisible-material branch is paid here.
        pa = &model_projected[a];
        pb = &model_projected[b];
        pc = &model_projected[c];

        // Reject trivially off-screen faces before backface/shading math.
        if ((pa->x < 0 && pb->x < 0 && pc->x < 0) ||
            (pa->x >= FB_WIDTH && pb->x >= FB_WIDTH && pc->x >= FB_WIDTH) ||
            (pa->y < 0 && pb->y < 0 && pc->y < 0) ||
            (pa->y >= FB_HEIGHT && pb->y >= FB_HEIGHT && pc->y >= FB_HEIGHT)) {
#if GB3D_PROFILE
            gb3d_prof_faces_offscreen++;
#endif
            continue;
        }

        // Inline the projected cross product here. On SM83 the old helper
        // call/return plus three pointer arguments was measurable face-stage
        // overhead; the two exact fast multiplies are unchanged.
        abx = (int16_t)pb->x - pa->x;
        aby = (int16_t)pb->y - pa->y;
        acx = (int16_t)pc->x - pa->x;
        acy = (int16_t)pc->y - pa->y;
        cross = (int16_t)(gb3d_fast_mul(abx, acy) - gb3d_fast_mul(aby, acx));
        if (cross <= 0)
            continue;

        if (cross > 80) shade = 3u;
        else if (cross > 20) shade = 2u;
        else shade = 1u;

        max_z = (uint8_t)model_camera[a].z;
        if ((uint8_t)model_camera[b].z > max_z) max_z = (uint8_t)model_camera[b].z;
        if ((uint8_t)model_camera[c].z > max_z) max_z = (uint8_t)model_camera[c].z;

        triangle_queue_add(pa, pb, pc, model->faces[i].palette, shade, max_z);
    }
#if GB3D_PROFILE
    gb3d_prof_face_ticks += gb3d_prof_delta(prof_part_start);
#endif
}

// ============================================================
// SCENE - sector reject + object frustum cull + global face queue
// ============================================================

static int16_t gb3d_floor_div64(int16_t v) {
    if (v >= 0) return (int16_t)(v >> 6);
    if (v == (int16_t)-32768) return -512;
    return (int16_t)-(((-v) + 63) >> 6);
}

static uint8_t model_is_visible_fast(const Model3D *model, uint8_t visible, const Vec3 *camera_origin) {
    int16_t radius;
    int16_t side_limit;
    if (!visible || model == 0) return 0u;
    radius = model->radius;

    if ((camera_origin->z + radius) < NEAR_Z) return 0u;
    if ((camera_origin->z - radius) > FAR_Z) return 0u;

    if (camera_origin->z > 0) {
        side_limit = camera_origin->z + radius + 12;
        if (camera_origin->x > side_limit || camera_origin->x < -side_limit) return 0u;
        if (camera_origin->y > side_limit || camera_origin->y < -side_limit) return 0u;
    }
    return 1u;
}

static uint8_t scene_sector_visible(uint8_t index, const SceneObject3D *obj, int16_t cam_sx, int16_t cam_sz) {
#if SCENE_SECTOR_CULLING
    int16_t dx, dz, limit;
    if (!scene_object_sector_static[index]) return 1u;
    // Four sectors cover FAR_Z (255); one extra sector plus the model radius
    // keeps this conservative at boundaries.
    limit = (int16_t)(5 + ((obj->model->radius + 63u) >> 6));
    dx = scene_object_sector_x[index] - cam_sx;
    dz = scene_object_sector_z[index] - cam_sz;
    if (dx < 0) dx = -dx;
    if (dz < 0) dz = -dz;
    if (dx > limit || dz > limit) return 0u;
#else
    index; obj; cam_sx; cam_sz;
#endif
    return 1u;
}

static void draw_scene(void) {
    uint8_t i;
    int16_t cam_sector_x, cam_sector_z;
    Vec3 origin, camera_origin;
    const SceneObject3D *obj;
    const SceneRenderInfo *ri;
    const Model3D *render_model;

    if (SCENE_OBJECT_COUNT > MAX_SCENE_OBJECTS)
        return;

    triangle_queue_begin();
    cam_sector_x = gb3d_floor_div64(camera.x);
    cam_sector_z = gb3d_floor_div64(camera.z);

    for (i = 0u; i < SCENE_OBJECT_COUNT; i++) {
        obj = &scene_objects[i];
        if (!obj->visible || obj->model == 0) continue;

        if (!scene_sector_visible(i, obj, cam_sector_x, cam_sector_z)) {
#if GB3D_PROFILE
            gb3d_prof_objects_culled++;
#endif
            continue;
        }

        ri = &scene_render_info[i];
        render_model = obj->model;

#if SCENE_FIXED_CAMERA_FAST
        if (ri->camera_baked && ri->world_vertices != 0) {
            // The compiler already rotated this immutable object's world-space
            // origin into the locked camera basis.
            camera_origin.x = ri->x - camera_fast_x;
            camera_origin.y = ri->y - camera_fast_y;
            camera_origin.z = ri->z - camera_fast_z;
        } else
#endif
        {
            origin.x = obj->x; origin.y = obj->y; origin.z = obj->z;
            world_to_camera(&origin, &camera_origin);
        }

        if (!model_is_visible_fast(render_model, obj->visible, &camera_origin)) {
#if GB3D_PROFILE
            gb3d_prof_objects_culled++;
#endif
            continue;
        }

#if GB3D_PROFILE
        gb3d_prof_objects_visible++;
#endif

        collect_model_triangles(
            obj->model, ri->world_vertices,
            obj->x, obj->y, obj->z, obj->rot_x, obj->rot_y, ri->camera_baked
        );
    }

    triangle_queue_flush();
}

// ============================================================
// CAMERA / INPUT
// ============================================================

static void camera_forward(int8_t amount) {
    int8_t s, c;
    s = isin(camera.yaw); c = icos(camera.yaw);
    camera.x += gb3d_fast_mul(s, amount) >> 7;
    camera.z += gb3d_fast_mul(c, amount) >> 7;
}

static void camera_strafe(int8_t amount) {
    int8_t s, c;
    s = isin(camera.yaw); c = icos(camera.yaw);
    camera.x += gb3d_fast_mul(c, amount) >> 7;
    camera.z -= gb3d_fast_mul(s, amount) >> 7;
}

static void update_camera(uint8_t keys) {
    if (keys & J_SELECT) {
#if !SCENE_FIXED_CAMERA_FAST
        if (keys & J_LEFT) camera.yaw--;
        if (keys & J_RIGHT) camera.yaw++;
        if ((keys & J_UP) && camera.pitch < 12) camera.pitch++;
        if ((keys & J_DOWN) && camera.pitch > -12) camera.pitch--;
#endif
    } else {
        if (keys & J_UP) camera_forward(MOVE_SPEED);
        if (keys & J_DOWN) camera_forward(-MOVE_SPEED);
        if (keys & J_LEFT) camera_strafe(-MOVE_SPEED);
        if (keys & J_RIGHT) camera_strafe(MOVE_SPEED);
    }
}

// ============================================================
// OPTIONAL HARDWARE PROFILER - HARDWARE WINDOW OVERLAY
// ============================================================
// Profile mode is compile-time. Release exports set GB3D_PROFILE=0, so all
// timing calls/counters disappear from generated code.
//
// The overlay no longer draws 3x5 pixels into the 3D framebuffer. It owns a
// temporary 20x3 Window tilemap at LCD Y=120 and uses dedicated 5x7 tiles.
// Normal game Window UI is restored when START+SELECT hides the profiler.

#if GB3D_PROFILE
#define PROF_MAP_W 20u
#define PROF_MAP_H 3u
#define PROF_MAP_SIZE (PROF_MAP_W * PROF_MAP_H)
#define PROF_PAGE_COUNT 4u

static volatile uint8_t gb3d_prof_timer_hi = 0u;
static uint8_t gb3d_prof_show = 0u;
static uint8_t gb3d_prof_page = 0u;
static uint8_t gb3d_prof_combo_latched = 0u;
static uint16_t gb3d_prof_input_ticks = 0u;
static uint16_t gb3d_prof_scripts_ticks = 0u;
static uint16_t gb3d_prof_physics_ticks = 0u;
static uint16_t gb3d_prof_clear_ticks = 0u;
static uint16_t gb3d_prof_scene_ticks = 0u;
static uint16_t gb3d_prof_ui_ticks = 0u;
static uint16_t gb3d_prof_upload_ticks = 0u;
static uint16_t gb3d_prof_sprites_ticks = 0u;
static uint16_t gb3d_prof_other_ticks = 0u;
static uint16_t gb3d_prof_cpu_ticks = 0u;
static uint8_t gb3d_prof_tilemap[PROF_MAP_SIZE];
static uint8_t gb3d_prof_attributes[PROF_MAP_SIZE];

static void gb3d_prof_timer_isr(void) { gb3d_prof_timer_hi++; }

static uint16_t gb3d_prof_now(void) {
    uint8_t hi1, hi2, lo;
    do {
        hi1 = gb3d_prof_timer_hi;
        lo = TIMA_REG;
        hi2 = gb3d_prof_timer_hi;
    } while (hi1 != hi2);
    return ((uint16_t)hi1 << 8) | lo;
}

static uint16_t gb3d_prof_delta(uint16_t start) {
    return (uint16_t)(gb3d_prof_now() - start);
}

static char gb3d_hex_digit(uint8_t v) {
    v &= 15u;
    return (char)(v < 10u ? ('0' + v) : ('A' + (v - 10u)));
}

static uint8_t gb3d_prof_glyph(char ch) {
    if (ch >= 'a' && ch <= 'z') ch = (char)(ch - ('a' - 'A'));
    if (ch >= 'A' && ch <= 'Z') return (uint8_t)(ch - 'A');
    if (ch >= '0' && ch <= '9') return (uint8_t)(26u + ch - '0');
    return 0xFFu;
}

static void gb3d_prof_put(uint8_t x, uint8_t y, char ch) {
    uint8_t gi;
    uint16_t index;
    if (x >= PROF_MAP_W || y >= PROF_MAP_H) return;
    index = (uint16_t)y * PROF_MAP_W + x;
    gi = gb3d_prof_glyph(ch);
    if (gi == 0xFFu)
        gb3d_prof_tilemap[index] = UI_SOLID_BASE;
    else
        gb3d_prof_tilemap[index] = gb3d_prof_tile_id(gi);
    gb3d_prof_attributes[index] = (uint8_t)(BKGF_BANK1 | 0u);
}

static void gb3d_prof_hex4(uint8_t x, uint8_t y, uint16_t v) {
    gb3d_prof_put(x++, y, gb3d_hex_digit((uint8_t)(v >> 12)));
    gb3d_prof_put(x++, y, gb3d_hex_digit((uint8_t)(v >> 8)));
    gb3d_prof_put(x++, y, gb3d_hex_digit((uint8_t)(v >> 4)));
    gb3d_prof_put(x,   y, gb3d_hex_digit((uint8_t)v));
}

static void gb3d_prof_pair(uint8_t row, char a, uint16_t av, char b, uint16_t bv) {
    gb3d_prof_put(0u, row, a);
    gb3d_prof_hex4(1u, row, av);
    gb3d_prof_put(7u, row, b);
    gb3d_prof_hex4(8u, row, bv);
}

static uint16_t gb3d_prof_scene_overhead(void) {
    uint16_t parts = gb3d_prof_vertex_ticks;
    parts = (uint16_t)(parts + gb3d_prof_face_ticks);
    parts = (uint16_t)(parts + gb3d_prof_draw_ticks);
    return (gb3d_prof_scene_ticks > parts) ? (uint16_t)(gb3d_prof_scene_ticks - parts) : 0u;
}

static void gb3d_prof_draw_overlay(void) {
    uint8_t i;
    if (!gb3d_prof_show) return;

    for (i = 0u; i < PROF_MAP_SIZE; i++) {
        gb3d_prof_tilemap[i] = UI_SOLID_BASE; // black
        gb3d_prof_attributes[i] = (uint8_t)(BKGF_BANK1 | 0u);
    }

    if (gb3d_prof_page == 0u) {
        // Page 1: top-level frame costs + submitted triangles.
        gb3d_prof_pair(0u, 'F', gb3d_prof_cpu_ticks,    'R', gb3d_prof_scene_ticks);
        gb3d_prof_pair(1u, 'U', gb3d_prof_upload_ticks, 'T', gb3d_prof_triangles);
        gb3d_prof_pair(2u, 'S', gb3d_prof_scripts_ticks,'P', gb3d_prof_physics_ticks);
    } else if (gb3d_prof_page == 1u) {
        // Page 2: frame orchestration around the renderer.
        // I=input, C=camera+framebuffer clear/setup, W=Window/UI RAM work,
        // G=sprite/OAM work, X=unclassified pre-VBlank CPU remainder.
        gb3d_prof_pair(0u, 'I', gb3d_prof_input_ticks,  'C', gb3d_prof_clear_ticks);
        gb3d_prof_pair(1u, 'W', gb3d_prof_ui_ticks,     'G', gb3d_prof_sprites_ticks);
        gb3d_prof_pair(2u, 'X', gb3d_prof_other_ticks,  'F', gb3d_prof_cpu_ticks);
    } else if (gb3d_prof_page == 2u) {
        // Page 3: renderer split. D is the rasterizer hot path we optimize.
        // V=vertex transform/project, B=face tests+bucket insertion,
        // D=bucket flush+triangle rasterization, Q=remaining scene overhead,
        // N=trivially off-screen faces, T=triangles actually submitted.
        gb3d_prof_pair(0u, 'V', gb3d_prof_vertex_ticks, 'B', gb3d_prof_face_ticks);
        gb3d_prof_pair(1u, 'D', gb3d_prof_draw_ticks,   'Q', gb3d_prof_scene_overhead());
        gb3d_prof_pair(2u, 'N', gb3d_prof_faces_offscreen, 'T', gb3d_prof_triangles);
    } else {
        // Page 4: scene workload counters.
        // M=mesh vertices, E=faces examined, T=triangles submitted,
        // D=queue drops, O=visible objects, C=culled objects.
        gb3d_prof_pair(0u, 'M', gb3d_prof_vertices,     'E', gb3d_prof_faces);
        gb3d_prof_pair(1u, 'T', gb3d_prof_triangles,    'D', gb3d_prof_triangles_dropped);
        gb3d_prof_pair(2u, 'O', gb3d_prof_objects_visible, 'C', gb3d_prof_objects_culled);
    }

    // Put the page number at the far right just like the expanded profiler.
    gb3d_prof_put(19u, 0u, (char)('1' + gb3d_prof_page));
}

static void gb3d_prof_window_upload(void) {
    if (!gb3d_prof_show) return;
    move_win(7u, 120u);
    VBK_REG = VBK_TILES;
    set_win_tiles(0u, 0u, PROF_MAP_W, PROF_MAP_H, gb3d_prof_tilemap);
    VBK_REG = VBK_ATTRIBUTES;
    set_win_tiles(0u, 0u, PROF_MAP_W, PROF_MAP_H, gb3d_prof_attributes);
    VBK_REG = VBK_TILES;
    SHOW_WIN;
}

static uint8_t gb3d_prof_is_showing(void) {
    return gb3d_prof_show;
}

static void gb3d_prof_reset_counts(void) {
    gb3d_prof_vertices = 0u;
    gb3d_prof_faces = 0u;
    gb3d_prof_triangles = 0u;
    gb3d_prof_triangles_dropped = 0u;
    gb3d_prof_faces_offscreen = 0u;
    gb3d_prof_objects_visible = 0u;
    gb3d_prof_objects_culled = 0u;
    gb3d_prof_vertex_ticks = 0u;
    gb3d_prof_face_ticks = 0u;
    gb3d_prof_draw_ticks = 0u;
}

static void gb3d_prof_toggle_combo(uint8_t keys) {
    uint8_t combo = ((keys & (J_START | J_SELECT)) == (J_START | J_SELECT));
    if (combo && !gb3d_prof_combo_latched) {
        gb3d_prof_show ^= 1u;
        if (gb3d_prof_show) gb3d_prof_page = 0u;
        gb3d_prof_combo_latched = 1u;
        // When leaving profiler mode, force normal HUD/window position back.
        gb3d_ui_upload_pending = 1u;
    } else if (!combo) {
        gb3d_prof_combo_latched = 0u;
    }
}

static void gb3d_prof_handle_page(uint8_t *keys, uint8_t *pressed) {
    if (!gb3d_prof_show) return;
    if (*pressed & J_RIGHT)
        gb3d_prof_page = (uint8_t)((gb3d_prof_page + 1u) & 3u);
    else if (*pressed & J_LEFT)
        gb3d_prof_page = (uint8_t)((gb3d_prof_page + 3u) & 3u);

    // While the profiler is visible, Left/Right belong to page navigation.
    // Mask them from the game so changing profiler pages cannot move the camera
    // and contaminate an A/B performance comparison.
    *keys &= (uint8_t)~(J_LEFT | J_RIGHT);
    *pressed &= (uint8_t)~(J_LEFT | J_RIGHT);
}

static void gb3d_prof_init(void) {
    gb3d_prof_timer_hi = 0u;
    TMA_REG = 0u;
    TIMA_REG = 0u;
    TAC_REG = (TACF_START | TACF_4KHZ);
    add_TIM(gb3d_prof_timer_isr);
    set_interrupts(VBL_IFLAG | TIM_IFLAG);
}
#else
#define gb3d_prof_toggle_combo(k) ((void)0)
#define gb3d_prof_handle_page(k,p) ((void)0)
#define gb3d_prof_draw_overlay() ((void)0)
#define gb3d_prof_reset_counts() ((void)0)
#define gb3d_prof_window_upload() ((void)0)
#define gb3d_prof_is_showing() 0u
#endif

// ============================================================
// VIDEO / MAIN
// ============================================================

static void video_init(void) {
    DISPLAY_OFF;
    if (_cpu == CGB_TYPE) cpu_fast();
#if GB3D_PROFILE
    gb3d_prof_init();
#endif

    // BG renderer uses map $9800; hardware Window UI uses the separate $9C00 map.
    LCDC_REG = (uint8_t)((LCDC_REG & (uint8_t)~LCDCF_BG9C00) | LCDCF_BG8000 | LCDCF_WIN9C00);
    VBK_REG = VBK_BANK_0;
    init_framebuffer_memory();
    init_framebuffer_tiles();
    set_bkg_palette(0u, 8u, bg_palettes);

    gb3d_set_sky_logical(SCENE_SKY_COLOR);
    fb_begin_frame();
    upload_framebuffer();
    init_window_ui();

    if (!SCENE_PLAYER_ENABLED && SCENE_NPC_COUNT) {
#if SCENE_SPRITE_8X16
        SPRITES_8x16;
#else
        SPRITES_8x8;
#endif
    }
    if (SCENE_PLAYER_ENABLED)
        init_player_sprite();
    if (SCENE_NPC_COUNT)
        init_npc_sprites();

    move_bkg(0u, 0u);
    SHOW_BKG;
    if (SCENE_PLAYER_ENABLED || SCENE_NPC_COUNT) SHOW_SPRITES;
    DISPLAY_ON;
}

void main(void) {
    uint8_t keys, old_keys, pressed, render_counter;
#if GB3D_PROFILE
    uint16_t prof_start, prof_cpu_start;
#endif

    old_keys = 0u;
    render_counter = 0u;

    camera.x = SCENE_CAMERA_X;
    camera.y = SCENE_CAMERA_Y;
    camera.z = SCENE_CAMERA_Z;
    camera.yaw = SCENE_CAMERA_YAW;
    camera.pitch = SCENE_CAMERA_PITCH;

    if (SCENE_PLAYER_ENABLED)
        init_platformer_player();

    video_init();
    scene_script_start();

    while (1) {
#if GB3D_PROFILE
        prof_cpu_start = gb3d_prof_now();
        gb3d_prof_reset_counts();
        prof_start = gb3d_prof_now();
#endif

        keys = joypad();
        pressed = (uint8_t)(keys & (uint8_t)~old_keys);
        old_keys = keys;
        gb3d_prof_toggle_combo(keys);
        gb3d_prof_handle_page(&keys, &pressed);

        if (SCENE_PLAYER_ENABLED)
            update_platformer_input(keys, pressed);
        else
            update_camera(keys);

        gb3d_frame_counter++;
#if GB3D_PROFILE
        gb3d_prof_input_ticks = gb3d_prof_delta(prof_start);
        prof_start = gb3d_prof_now();
#endif
        scene_script_update();

        // START+SELECT is reserved for the profiler toggle while profiling.
#if GB3D_PROFILE
        if ((keys & (J_START | J_SELECT)) != (J_START | J_SELECT)) {
#endif
            if (pressed & J_A)      scene_script_a();
            if (pressed & J_B)      scene_script_b();
            if (pressed & J_SELECT) scene_script_select();
            if (pressed & J_START)  scene_script_start_button();
#if GB3D_PROFILE
        }
#endif
        if (pressed & J_UP)     scene_script_up();
        if (pressed & J_DOWN)   scene_script_down();
        if (pressed & J_LEFT)   scene_script_left();
        if (pressed & J_RIGHT)  scene_script_right();

        if (keys & J_A)      scene_script_a_held();
        if (keys & J_B)      scene_script_b_held();
        if (keys & J_SELECT) scene_script_select_held();
        if (keys & J_UP)     scene_script_up_held();
        if (keys & J_DOWN)   scene_script_down_held();
        if (keys & J_LEFT)   scene_script_left_held();
        if (keys & J_RIGHT)  scene_script_right_held();
#if GB3D_PROFILE
        gb3d_prof_scripts_ticks = gb3d_prof_delta(prof_start);
        prof_start = gb3d_prof_now();
#endif

        if (SCENE_PLAYER_ENABLED) {
            player_vertical_physics();
            update_platformer_camera();
        }
#if GB3D_PROFILE
        gb3d_prof_physics_ticks = gb3d_prof_delta(prof_start);
#endif

        render_counter++;
        if (render_counter >= RENDER_DIVISOR) {
            render_counter = 0u;
#if GB3D_PROFILE
            prof_start = gb3d_prof_now();
#endif
            prepare_camera_transform();
            fb_begin_frame();
#if GB3D_PROFILE
            gb3d_prof_clear_ticks = gb3d_prof_delta(prof_start);
            prof_start = gb3d_prof_now();
#endif
            draw_scene();
#if GB3D_PROFILE
            gb3d_prof_scene_ticks = gb3d_prof_delta(prof_start);
            prof_start = gb3d_prof_now();
#endif
            // Window UI is event-driven: rebuilding its RAM map only happens
            // after a script actually changed a value/visibility/position.
            gb3d_ui_prepare();
#if GB3D_PROFILE
            gb3d_prof_ui_ticks = gb3d_prof_delta(prof_start);
            prof_start = gb3d_prof_now();
#endif

            // Update shadow OAM BEFORE VBlank so GBDK's next OAM DMA sees it.
            if (SCENE_PLAYER_ENABLED)
                update_player_sprite();
            if (SCENE_NPC_COUNT)
                update_npc_sprites();
#if GB3D_PROFILE
            {
                uint16_t known;
                gb3d_prof_sprites_ticks = gb3d_prof_delta(prof_start);
                gb3d_prof_cpu_ticks = gb3d_prof_delta(prof_cpu_start);
                known = gb3d_prof_input_ticks;
                known = (uint16_t)(known + gb3d_prof_scripts_ticks);
                known = (uint16_t)(known + gb3d_prof_physics_ticks);
                known = (uint16_t)(known + gb3d_prof_clear_ticks);
                known = (uint16_t)(known + gb3d_prof_scene_ticks);
                known = (uint16_t)(known + gb3d_prof_ui_ticks);
                known = (uint16_t)(known + gb3d_prof_sprites_ticks);
                gb3d_prof_other_ticks = (gb3d_prof_cpu_ticks > known) ?
                    (uint16_t)(gb3d_prof_cpu_ticks - known) : 0u;
            }
#endif
            // Build the profiler map after all current-frame values are final.
            // Diagnostic map generation itself stays outside the reported F.
            gb3d_prof_draw_overlay();

            // IMPORTANT V12 FAST PATH: wait for VBlank immediately BEFORE
            // touching VRAM. Older versions waited at frame start, then spent
            // the whole render during active display and called the VRAM-safe
            // map-copy helpers afterward, making them crawl through LCD modes.
            vsync();
#if GB3D_PROFILE
            prof_start = gb3d_prof_now();
#endif
            upload_framebuffer();
            // Normal HUD uploads are event-driven. While profiling, the
            // profiler temporarily owns the Window instead.
            if (!gb3d_prof_is_showing())
                gb3d_ui_upload();
#if GB3D_PROFILE
            gb3d_prof_upload_ticks = gb3d_prof_delta(prof_start);
#endif
            // Keep profiler Window upload OUTSIDE U so the overlay does not
            // report its own diagnostic cost as framebuffer upload time.
            gb3d_prof_window_upload();
        } else {
            if (SCENE_PLAYER_ENABLED) update_player_sprite();
            if (SCENE_NPC_COUNT) update_npc_sprites();
            gb3d_ui_prepare();
            gb3d_prof_draw_overlay();
            vsync();
            if (gb3d_prof_is_showing()) gb3d_prof_window_upload();
            else gb3d_ui_upload();
        }
    }
}
