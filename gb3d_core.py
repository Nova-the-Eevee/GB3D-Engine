from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import shlex
import uuid
from pathlib import Path
from typing import Any

FORMAT_NAME = "GB3D Studio"
FORMAT_VERSION = 9
FB_WIDTH = 40
FB_HEIGHT = 36
SCREEN_CX = FB_WIDTH // 2
SCREEN_CY = FB_HEIGHT // 2
FOCAL_LEN = 28
NEAR_Z = 8
FAR_Z = 255
MAX_MODEL_VERTICES = 128
MAX_MODEL_FACES = 128
MAX_SCENE_OBJECTS = 32

COLOR_NAMES = [
    "Black", "White", "Light Gray", "Dark Gray",
    "Red", "Dark Red", "Green", "Dark Green",
    "Blue", "Dark Blue", "Yellow", "Orange",
    "Cyan", "Purple", "Tan", "Pink",
]

# V3 runtime material family for each editor logical color.
# 0xFF means true black / do not draw a filled face.
LOGICAL_TO_PALETTE = [
    0xFF, 0, 0, 0,
    1, 1, 2, 2,
    3, 3, 5, 5,
    6, 4, 7, 4,
]

# Preferred shade inside the palette family above. Used for sky colors and
# script-driven scene colors. 0/1/2/3 = black/dark/mid/bright.
LOGICAL_TO_SHADE = [
    0, 3, 2, 1,
    2, 1, 2, 1,
    2, 1, 3, 2,
    3, 2, 2, 3,
]

SCRIPT_EVENTS = [
    "start", "update",
    "a", "b", "select", "start_button",
    "up", "down", "left", "right",
    "a_held", "b_held", "select_held",
    "up_held", "down_held", "left_held", "right_held",
]

SCRIPT_EVENT_LABELS = {
    "start": "once when the scene starts",
    "update": "every 60 Hz game tick",
    "a": "when A is pressed",
    "b": "when B is pressed",
    "select": "when Select is pressed",
    "start_button": "when Start is pressed",
    "up": "when D-Pad Up is pressed",
    "down": "when D-Pad Down is pressed",
    "left": "when D-Pad Left is pressed",
    "right": "when D-Pad Right is pressed",
    "a_held": "every tick while A is held",
    "b_held": "every tick while B is held",
    "select_held": "every tick while Select is held",
    "up_held": "every tick while Up is held",
    "down_held": "every tick while Down is held",
    "left_held": "every tick while Left is held",
    "right_held": "every tick while Right is held",
}

# Human-friendly approximations used by the PC editor.
LOGICAL_RGB = [
    (0, 0, 0),
    (255, 255, 255),
    (181, 181, 181),
    (82, 82, 82),
    (255, 41, 41),
    (123, 16, 24),
    (33, 220, 49),
    (20, 96, 30),
    (33, 99, 255),
    (18, 42, 120),
    (255, 239, 41),
    (255, 132, 25),
    (41, 230, 255),
    (174, 74, 255),
    (198, 156, 99),
    (255, 124, 199),
]

# Exact GBC 5-bit palettes used by the V3 direct renderer.
# Every palette uses 0=black, 1=dark, 2=mid, 3=bright.
BG_PALETTES_5 = [
    [(0,0,0), (8,8,8), (20,20,20), (31,31,31)],
    [(0,0,0), (11,2,3), (25,4,5), (31,16,16)],
    [(0,0,0), (2,10,3), (4,23,6), (16,31,17)],
    [(0,0,0), (2,4,11), (4,10,25), (16,20,31)],
    [(0,0,0), (9,3,11), (20,7,25), (31,18,31)],
    [(0,0,0), (12,6,1), (27,15,2), (31,29,8)],
    [(0,0,0), (1,9,10), (3,22,24), (15,31,31)],
    [(0,0,0), (8,6,3), (19,14,8), (29,24,16)],
]

# Editor-only nearest-color tables for the approximate hardware preview.
def _build_palette_tables():
    nearest_idx = []
    nearest_err = []
    for pal in BG_PALETTES_5:
        row_i = []
        row_e = []
        for r8, g8, b8 in LOGICAL_RGB:
            r = round(r8 * 31 / 255)
            g = round(g8 * 31 / 255)
            b = round(b8 * 31 / 255)
            best_i = 0
            best_e = 10**9
            for i, (pr, pg, pb) in enumerate(pal):
                e = abs(pr-r) + abs(pg-g) + abs(pb-b)
                if e < best_e:
                    best_i = i
                    best_e = e
            row_i.append(best_i)
            row_e.append(best_e)
        nearest_idx.append(row_i)
        nearest_err.append(row_e)
    return nearest_idx, nearest_err

PALETTE_NEAREST_INDEX, PALETTE_NEAREST_ERROR = _build_palette_tables()

# Keep these in sync with main.c.
SHADE_DARK = [0,3,3,0,5,5,7,7,9,9,11,5,9,9,3,13]
SHADE_MID = [0,2,2,3,4,5,6,7,8,9,10,11,12,13,14,15]
SHADE_BRIGHT = [3,1,1,2,11,4,10,6,12,8,1,10,1,15,1,1]

SIN_TABLE = [
     0, 12, 25, 37, 49, 60, 71, 81,
    90, 98,106,112,117,122,125,126,
   127,126,125,122,117,112,106, 98,
    90, 81, 71, 60, 49, 37, 25, 12,
     0,-12,-25,-37,-49,-60,-71,-81,
   -90,-98,-106,-112,-117,-122,-125,-126,
  -127,-126,-125,-122,-117,-112,-106,-98,
   -90,-81,-71,-60,-49,-37,-25,-12,
]

# ---------------------------------------------------------------------------
# 32x32 hardware-sprite player art
# ---------------------------------------------------------------------------
# Projects store the player picture as 1024 palette indices (0=transparent,
# 1..3=opaque colors) plus one four-entry RGB palette. This keeps the project
# self-contained: after importing a PNG, the original file is no longer needed.

_OLD_PLAYER_TILES = [
    24,0, 36,24, 66,60, 65,62, 64,63, 80,63, 68,63, 64,63,
    35,31, 16,15, 24,7, 8,7, 16,15, 36,24, 48,0, 0,0,
    24,0, 36,24, 66,60, 130,124, 2,252, 10,252, 34,252, 2,252,
    196,248, 16,224, 48,192, 32,192, 16,224, 36,24, 48,0, 0,0,
]

def _decode_old_player_16x16() -> list[int]:
    out = [0] * (16 * 16)
    # Old order: left-top, left-bottom, right-top, right-bottom.
    tile_positions = [(0, 0), (0, 8), (8, 0), (8, 8)]
    for ti, (ox, oy) in enumerate(tile_positions):
        base = ti * 16
        for y in range(8):
            lo = _OLD_PLAYER_TILES[base + y * 2]
            hi = _OLD_PLAYER_TILES[base + y * 2 + 1]
            for x in range(8):
                bit = 7 - x
                v = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                out[(oy + y) * 16 + (ox + x)] = v
    return out

def default_player_sprite_pixels() -> list[int]:
    src = _decode_old_player_16x16()
    out = [0] * (32 * 32)
    # Nearest-neighbour 2x upscale preserves the old built-in critter style.
    for y in range(32):
        for x in range(32):
            out[y * 32 + x] = src[(y >> 1) * 16 + (x >> 1)]
    return out

def default_player_sprite_palette(logical_color: int = 14) -> list[list[int]]:
    pal, _ = logical_material(logical_color) if 'logical_material' in globals() else (7, 2)
    rgb5 = BG_PALETTES_5[pal]
    return [[round(r * 255 / 31), round(g * 255 / 31), round(b * 255 / 31)] for r, g, b in rgb5]

def _normalize_player_sprite(player: dict[str, Any]) -> None:
    pixels = player.get('sprite_pixels')
    if not isinstance(pixels, list) or len(pixels) != 1024:
        player['sprite_pixels'] = default_player_sprite_pixels()
    else:
        player['sprite_pixels'] = [max(0, min(3, int(v))) for v in pixels]
    palette = player.get('sprite_palette')
    if not isinstance(palette, list) or len(palette) != 4:
        player['sprite_palette'] = default_player_sprite_palette(int(player.get('color', 14)))
    else:
        fixed = []
        for i, rgb in enumerate(palette):
            try:
                vals = list(rgb)[:3]
                if len(vals) != 3:
                    raise ValueError
                fixed.append([max(0, min(255, int(c))) for c in vals])
            except Exception:
                fixed.append([0, 0, 0] if i == 0 else [255, 255, 255])
        fixed[0] = [0, 0, 0]  # sprite color 0 is always transparent
        player['sprite_palette'] = fixed
    mode = str(player.get('sprite_mode', '8x16')).lower()
    player['sprite_mode'] = '8x8' if mode == '8x8' else '8x16'
    player.setdefault('sprite_source_name', 'Built-in critter')

def import_player_sprite_image(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load a 32x32 image, preserve transparency, and quantize to 3 colors.

    Requires Pillow only for this editor-side import operation. Exported projects
    do not depend on Pillow. The returned data can be embedded directly in .gb3d.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError('Importing player images requires Pillow: pip install pillow') from exc

    src_path = Path(path)
    img = Image.open(src_path).convert('RGBA')
    if img.size != (32, 32):
        raise ValueError(f'Player image must be exactly 32x32 pixels; got {img.size[0]}x{img.size[1]}.')

    rgba = list(img.getdata())
    opaque = [(r, g, b) for r, g, b, a in rgba if a >= 128]
    if not opaque:
        return {
            'sprite_pixels': [0] * 1024,
            'sprite_palette': [[0,0,0], [85,85,85], [170,170,170], [255,255,255]],
            'sprite_source_name': src_path.name,
        }

    unique = []
    seen = set()
    for rgb in opaque:
        if rgb not in seen:
            seen.add(rgb)
            unique.append(rgb)

    if len(unique) <= 3:
        colors = unique[:]
    else:
        # Quantize only opaque pixels so transparent RGB junk cannot influence
        # the three visible GBC sprite colors.
        strip = Image.new('RGB', (len(opaque), 1))
        strip.putdata(opaque)
        q = strip.quantize(colors=3, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        raw = q.getpalette() or []
        used = sorted(set(q.getdata()))
        colors = []
        for idx in used[:3]:
            off = idx * 3
            colors.append(tuple(raw[off:off+3]))

    if not colors:
        colors = [(255,255,255)]
    while len(colors) < 3:
        colors.append(colors[-1])

    def nearest(rgb):
        rr, gg, bb = rgb
        best_i = 0
        best_d = 1 << 30
        for i, (r, g, b) in enumerate(colors[:3]):
            d = (r-rr)*(r-rr) + (g-gg)*(g-gg) + (b-bb)*(b-bb)
            if d < best_d:
                best_d = d
                best_i = i
        return best_i + 1

    pixels = []
    for r, g, b, a in rgba:
        pixels.append(0 if a < 128 else nearest((r, g, b)))

    return {
        'sprite_pixels': pixels,
        'sprite_palette': [[0,0,0]] + [list(c) for c in colors[:3]],
        'sprite_source_name': src_path.name,
    }

def player_sprite_tile_bytes(player: dict[str, Any]) -> list[int]:
    temp = dict(player)
    _normalize_player_sprite(temp)
    pixels = temp['sprite_pixels']
    mode = temp['sprite_mode']

    def encode_tile(tx: int, ty: int) -> list[int]:
        data: list[int] = []
        ox, oy = tx * 8, ty * 8
        for y in range(8):
            lo = hi = 0
            for x in range(8):
                v = int(pixels[(oy + y) * 32 + (ox + x)]) & 3
                bit = 7 - x
                if v & 1:
                    lo |= 1 << bit
                if v & 2:
                    hi |= 1 << bit
            data.extend([lo, hi])
        return data

    out: list[int] = []
    if mode == '8x16':
        # Each OAM entry uses an even tile plus the following odd tile. Arrange
        # data in exactly that pair order: four columns x two 16px rows.
        for block_y in range(2):
            for tx in range(4):
                out.extend(encode_tile(tx, block_y * 2))
                out.extend(encode_tile(tx, block_y * 2 + 1))
    else:
        for ty in range(4):
            for tx in range(4):
                out.extend(encode_tile(tx, ty))
    return out


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def safe_c_name(name: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    if not out:
        out = "asset"
    if out[0].isdigit():
        out = "_" + out
    return out.lower()


def new_project() -> dict[str, Any]:
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "name": "Untitled",
        "camera": {"x": 0, "y": 0, "z": -64, "yaw": 0, "pitch": 0},
        "settings": {
            "background": 0,
            "sky_color": 0,
            "hardware_palette_preview": True,
        },
        "player": {
            "enabled": False,
            "position": [0, 0, 0],
            "speed": 2,
            "jump_speed": 7,
            "gravity": 1,
            "ground_y": 0,
            "half_width": 3,
            "height": 12,
            "camera_distance": 48,
            "camera_height": 24,
            "camera_pitch": -4,
            "color": 14,
            "visible": True,
            "sprite_mode": "8x16",
            "sprite_pixels": default_player_sprite_pixels(),
            "sprite_palette": default_player_sprite_palette(14),
            "sprite_source_name": "Built-in critter",
        },
        "models": [],
        "objects": [],
    }


def save_project(project: dict[str, Any], path: str | os.PathLike[str]) -> None:
    p = Path(path)
    p.write_text(json.dumps(project, indent=2), encoding="utf-8")


def load_project(path: str | os.PathLike[str]) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != FORMAT_NAME:
        raise ValueError("This is not a GB3D Studio project.")
    if int(data.get("version", 0)) > FORMAT_VERSION:
        raise ValueError("This project was made by a newer GB3D Studio version.")

    # Backward-compatible upgrade from V1/V3-era files.
    settings = data.setdefault("settings", {})
    settings.setdefault("background", 0)
    settings.setdefault("sky_color", int(settings.get("background", 0)) & 15)
    settings.setdefault("hardware_palette_preview", True)
    for obj in data.setdefault("objects", []):
        obj.setdefault("script", "")
        obj.setdefault("visible", True)
        obj.setdefault("solid", False)
    player = data.setdefault("player", {})
    defaults = new_project()["player"]
    for key, value in defaults.items():
        player.setdefault(key, copy.deepcopy(value))
    _normalize_player_sprite(player)
    data.setdefault("models", [])
    data.setdefault("camera", {"x": 0, "y": 0, "z": -64, "yaw": 0, "pitch": 0})
    data["version"] = FORMAT_VERSION
    return data


def parse_mtl(path: Path) -> dict[str, tuple[float, float, float]]:
    result: dict[str, tuple[float, float, float]] = {}
    current: str | None = None
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] == "newmtl" and len(parts) >= 2:
            current = " ".join(parts[1:])
        elif parts[0] == "Kd" and len(parts) >= 4 and current:
            try:
                result[current] = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError:
                pass
    return result


def nearest_logical_color(rgb: tuple[float, float, float]) -> int:
    # Accept MTL-style 0..1 or normal 0..255.
    if max(rgb) <= 1.0001:
        rr, gg, bb = (v * 255.0 for v in rgb)
    else:
        rr, gg, bb = rgb
    best = 0
    best_d = float("inf")
    for i, (r, g, b) in enumerate(LOGICAL_RGB):
        d = (r - rr) ** 2 + (g - gg) ** 2 + (b - bb) ** 2
        if d < best_d:
            best = i
            best_d = d
    return best


def _parse_obj_index(token: str, vertex_count: int) -> int:
    first = token.split("/")[0]
    idx = int(first)
    return idx - 1 if idx > 0 else vertex_count + idx


def import_obj(path: str | os.PathLike[str], target_size: float = 32.0, default_color: int = 1) -> dict[str, Any]:
    path = Path(path)
    vertices_f: list[tuple[float, float, float]] = []
    polygons: list[tuple[list[int], str | None]] = []
    mtllibs: list[str] = []
    current_mtl: str | None = None

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        tag = parts[0]
        if tag == "v" and len(parts) >= 4:
            vertices_f.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif tag == "f" and len(parts) >= 4:
            poly = [_parse_obj_index(tok, len(vertices_f)) for tok in parts[1:]]
            polygons.append((poly, current_mtl))
        elif tag == "usemtl" and len(parts) >= 2:
            current_mtl = " ".join(parts[1:])
        elif tag == "mtllib" and len(parts) >= 2:
            mtllibs.extend(parts[1:])

    if not vertices_f:
        raise ValueError("OBJ contains no vertices.")
    if not polygons:
        raise ValueError("OBJ contains no faces.")

    # Material colors.
    materials: dict[str, tuple[float, float, float]] = {}
    for lib in mtllibs:
        materials.update(parse_mtl(path.parent / lib))

    # Center + normalize into compact integer engine units.
    xs = [v[0] for v in vertices_f]
    ys = [v[1] for v in vertices_f]
    zs = [v[2] for v in vertices_f]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0
    largest = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)) or 1.0
    scale = float(target_size) / largest
    vertices = [
        [round((x - cx) * scale), round((y - cy) * scale), round((z - cz) * scale)]
        for x, y, z in vertices_f
    ]

    edges_set: set[tuple[int, int]] = set()
    faces: list[list[int]] = []
    for poly, material_name in polygons:
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            if a > b:
                a, b = b, a
            edges_set.add((a, b))

        color = default_color & 15
        if material_name and material_name in materials:
            color = nearest_logical_color(materials[material_name])

        # Fan triangulation.
        for i in range(1, len(poly) - 1):
            faces.append([poly[0], poly[i], poly[i + 1], color])

    name = safe_c_name(path.stem)
    return {
        "id": new_id("model"),
        "name": name,
        "source": str(path),
        "vertices": vertices,
        "edges": [list(e) for e in sorted(edges_set)],
        "faces": faces,
        "default_color": default_color & 15,
    }


def make_object(model: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    return {
        "id": new_id("obj"),
        "name": name or model.get("name", "Object"),
        "model_id": model["id"],
        "position": [0, 0, 0],
        "rotation": [0, 0],
        "visible": True,
        "solid": False,
        "script": "",
    }


def model_by_id(project: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    return next((m for m in project.get("models", []) if m.get("id") == model_id), None)


def isin(angle: int) -> int:
    return SIN_TABLE[angle & 63]


def icos(angle: int) -> int:
    return SIN_TABLE[(angle + 16) & 63]


def rotate_vertex(v: list[int] | tuple[int, int, int], angle_x: int, angle_y: int) -> tuple[int, int, int]:
    x, y, z = map(int, v)
    sx, cx = isin(angle_x), icos(angle_x)
    sy, cy = isin(angle_y), icos(angle_y)
    tx = ((x * cy) - (z * sy)) >> 7
    tz = ((x * sy) + (z * cy)) >> 7
    x, z = tx, tz
    ty = ((y * cx) - (z * sx)) >> 7
    tz = ((y * sx) + (z * cx)) >> 7
    return x, ty, tz


def world_to_camera(v: tuple[int, int, int], cam: dict[str, int]) -> tuple[int, int, int]:
    x = v[0] - int(cam["x"])
    y = v[1] - int(cam["y"])
    z = v[2] - int(cam["z"])
    sy, cy = isin(int(cam["yaw"])), icos(int(cam["yaw"]))
    tx = ((x * cy) - (z * sy)) >> 7
    tz = ((x * sy) + (z * cy)) >> 7
    x, z = tx, tz
    sp, cp = isin(int(cam["pitch"]) & 0xFF), icos(int(cam["pitch"]) & 0xFF)
    ty = ((y * cp) - (z * sp)) >> 7
    tz = ((y * sp) + (z * cp)) >> 7
    return x, ty, tz


def project_vertex(v: tuple[int, int, int]) -> tuple[int, int] | None:
    x, y, z = v
    if z < NEAR_Z or z > FAR_Z:
        return None
    scale = (FOCAL_LEN * 16) // z
    return SCREEN_CX + ((x * scale) >> 4), SCREEN_CY - ((y * scale) >> 4)


def _put_pixel(buf: list[list[int]], x: int, y: int, color: int) -> None:
    if 0 <= x < FB_WIDTH and 0 <= y < FB_HEIGHT:
        buf[y][x] = color & 31


def _line(buf: list[list[int]], x0: int, y0: int, x1: int, y1: int, color: int) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    # Bound the loop so pathological offscreen lines cannot hang the editor.
    for _ in range(2048):
        _put_pixel(buf, x0, y0, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = err * 2
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _span(buf: list[list[int]], y: int, x1: int, x2: int, color: int) -> None:
    if y < 0 or y >= FB_HEIGHT:
        return
    if x1 > x2:
        x1, x2 = x2, x1
    if x2 < 0 or x1 >= FB_WIDTH:
        return
    x1 = max(0, x1)
    x2 = min(FB_WIDTH - 1, x2)
    row = buf[y]
    for x in range(x1, x2 + 1):
        row[x] = color & 31


def _triangle(buf: list[list[int]], p0: tuple[int, int], p1: tuple[int, int], p2: tuple[int, int], color: int) -> None:
    pts = sorted([p0, p1, p2], key=lambda p: p[1])
    x0, y0 = pts[0]
    x1, y1 = pts[1]
    x2, y2 = pts[2]
    if y2 < 0 or y0 >= FB_HEIGHT:
        return
    if x0 < 0 and x1 < 0 and x2 < 0:
        return
    if x0 >= FB_WIDTH and x1 >= FB_WIDTH and x2 >= FB_WIDTH:
        return
    if y0 == y2:
        _span(buf, y0, min(x0, x1, x2), max(x0, x1, x2), color)
        return
    slope_long = ((x2 - x0) * 256) // (y2 - y0)
    x_long = x0 * 256
    if y1 > y0:
        slope_top = ((x1 - x0) * 256) // (y1 - y0)
        x_short = x0 * 256
        for y in range(y0, y1):
            _span(buf, y, x_long >> 8, x_short >> 8, color)
            x_long += slope_long
            x_short += slope_top
    if y2 > y1:
        slope_bottom = ((x2 - x1) * 256) // (y2 - y1)
        x_short = x1 * 256
        x_long = (x0 * 256) + slope_long * (y1 - y0)
        for y in range(y1, y2 + 1):
            _span(buf, y, x_long >> 8, x_short >> 8, color)
            x_long += slope_long
            x_short += slope_bottom


def logical_material(logical_color: int) -> tuple[int, int]:
    logical_color &= 15
    pal = LOGICAL_TO_PALETTE[logical_color]
    if pal == 0xFF:
        return 0, 0
    return int(pal), int(LOGICAL_TO_SHADE[logical_color])


def effective_camera(project: dict[str, Any]) -> dict[str, int]:
    cam0 = project.get("camera", {})
    cam = {
        "x": int(cam0.get("x", 0)), "y": int(cam0.get("y", 0)), "z": int(cam0.get("z", -64)),
        "yaw": int(cam0.get("yaw", 0)) & 63, "pitch": int(cam0.get("pitch", 0)),
    }
    player = project.get("player", {})
    if player.get("enabled", False):
        pos = (player.get("position") or [0, 0, 0])[:3]
        px, py, pz = map(int, pos)
        dist = int(player.get("camera_distance", 48))
        height = int(player.get("camera_height", 24))
        pitch = int(player.get("camera_pitch", -4))
        s, c = isin(cam["yaw"]), icos(cam["yaw"])
        cam["x"] = px - ((s * dist) >> 7)
        cam["z"] = pz - ((c * dist) >> 7)
        cam["y"] = py + height
        cam["pitch"] = max(-12, min(12, pitch))
    return cam


def preview_player(project: dict[str, Any]) -> tuple[int, int] | None:
    player = project.get("player", {})
    if not player.get("enabled", False) or not player.get("visible", True):
        return None
    pos = (player.get("position") or [0, 0, 0])[:3]
    px, py, pz = map(int, pos)
    height = int(player.get("height", 12))
    cam = effective_camera(project)
    cv = world_to_camera((px, py + height // 2, pz), cam)
    pv = project_vertex(cv)
    if pv is None:
        return None
    return int(pv[0]), int(pv[1])


def render_scene(project: dict[str, Any], wireframe: bool = False) -> tuple[list[list[int]], dict[str, int]]:
    # V6 preview tokens: bits 0-1 = shade index, bits 2-4 = palette family.
    # The whole framebuffer starts as the selected sky material.
    sky_color = int(project.get("settings", {}).get("sky_color", 0)) & 15
    sky_pal, sky_shade = logical_material(sky_color)
    sky_token = (sky_pal << 2) | sky_shade
    buf = [[sky_token for _ in range(FB_WIDTH)] for _ in range(FB_HEIGHT)]
    cam = effective_camera(project)

    objects = [o for o in project.get("objects", []) if o.get("visible", True)]
    sortable: list[tuple[int, dict[str, Any]]] = []
    for obj in objects:
        pos = obj.get("position", [0, 0, 0])
        center_cam = world_to_camera((int(pos[0]), int(pos[1]), int(pos[2])), cam)
        sortable.append((center_cam[2], obj))
    sortable.sort(key=lambda item: item[0], reverse=True)

    stat_vertices = 0
    stat_faces = 0
    stat_drawn = 0

    for _, obj in sortable:
        model = model_by_id(project, obj.get("model_id", ""))
        if not model:
            continue
        vertices = model.get("vertices", [])
        faces = model.get("faces", [])
        edges = model.get("edges", [])
        stat_vertices += len(vertices)
        stat_faces += len(faces)
        rx, ry = (obj.get("rotation") or [0, 0])[:2]
        px, py, pz = (obj.get("position") or [0, 0, 0])[:3]
        camera_vertices: list[tuple[int, int, int]] = []
        projected: list[tuple[int, int] | None] = []
        for v in vertices:
            x, y, z = rotate_vertex(v, int(rx), int(ry))
            world = (x + int(px), y + int(py), z + int(pz))
            cv = world_to_camera(world, cam)
            camera_vertices.append(cv)
            pv = project_vertex(cv)
            if pv is not None:
                pv = (
                    max(-32, min(71, pv[0])),
                    max(-32, min(67, pv[1])),
                )
            projected.append(pv)

        if wireframe:
            for a, b in edges:
                pa, pb = projected[a], projected[b]
                if pa is not None and pb is not None:
                    _line(buf, pa[0], pa[1], pb[0], pb[1], 3)  # grayscale bright
                    stat_drawn += 1
            continue

        visible: list[tuple[int, int, int]] = []  # depth, face index, cross
        for fi, face in enumerate(faces):
            a, b, c = face[:3]
            pa, pb, pc = projected[a], projected[b], projected[c]
            if pa is None or pb is None or pc is None:
                continue
            cross = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
            if cross <= 0:
                continue
            depth = camera_vertices[a][2] + camera_vertices[b][2] + camera_vertices[c][2]
            visible.append((depth, fi, cross))
        visible.sort(key=lambda item: item[0], reverse=True)

        for _, fi, cross in visible:
            face = faces[fi]
            a, b, c = face[:3]
            base = int(face[3] if len(face) > 3 else model.get("default_color", 1)) & 15
            pal = LOGICAL_TO_PALETTE[base]
            if pal == 0xFF:
                continue
            shade = 3 if cross > 80 else (2 if cross > 20 else 1)
            token = (int(pal) << 2) | shade
            _triangle(buf, projected[a], projected[b], projected[c], token)  # type: ignore[arg-type]
            stat_drawn += 1

    return buf, {"vertices": stat_vertices, "faces": stat_faces, "drawn": stat_drawn, "objects": len(sortable)}


def hardware_palette_rgb(tokens: list[list[int]]) -> list[list[tuple[int, int, int]]]:
    """Approximate V3's one-palette-per-8x8-tile rule.

    The editor does not retain exact triangle write timestamps per hardware tile,
    so for mixed-material 2x2 logical tiles it picks the last nonblack quadrant
    in BR/BL/TR/TL order as the palette winner. Shade indices are preserved,
    matching the runtime's cheap last-writer palette behavior.
    """
    out = [[(0, 0, 0) for _ in range(FB_WIDTH)] for _ in range(FB_HEIGHT)]
    pal8 = [[tuple(round(c * 255 / 31) for c in rgb) for rgb in pal] for pal in BG_PALETTES_5]
    for ty in range(0, FB_HEIGHT, 2):
        for tx in range(0, FB_WIDTH, 2):
            vals = [
                tokens[ty][tx],
                tokens[ty][tx + 1],
                tokens[ty + 1][tx],
                tokens[ty + 1][tx + 1],
            ]
            winner = 0
            for v in vals:
                if (v & 3) != 0:
                    winner = (v >> 2) & 7
            for n, (yy, xx) in enumerate(((ty, tx), (ty, tx + 1), (ty + 1, tx), (ty + 1, tx + 1))):
                shade = vals[n] & 3
                out[yy][xx] = pal8[winner][shade]
    return out


def logical_rgb(tokens: list[list[int]]) -> list[list[tuple[int, int, int]]]:
    # Ideal per-logical-pixel material preview before the hardware tile palette
    # collision rule is applied.
    pal8 = [[tuple(round(c * 255 / 31) for c in rgb) for rgb in pal] for pal in BG_PALETTES_5]
    return [[pal8[(v >> 2) & 7][v & 3] for v in row] for row in tokens]



# ============================================================
# GB3D SCRIPTING
# ============================================================
# Scripts are export-time compiled. The Game Boy runs native C statements,
# not a text parser or bytecode VM.

_SCRIPT_ARITY = {
    "move": 3,
    "set_pos": 3,
    "spin": 2,
    "set_rot": 2,
    "show": 0,
    "hide": 0,
    "toggle": 0,

    "move_object": 4,
    "set_object_pos": 4,
    "spin_object": 3,
    "set_object_rot": 3,
    "show_object": 1,
    "hide_object": 1,
    "toggle_object": 1,

    "camera_move": 3,
    "camera_set": 3,
    "camera_look": 2,
    "camera_set_look": 2,
    "reset_camera": 0,
    "set_sky": 1,

    "player_move": 3,
    "player_set": 3,
    "player_jump": 0,
    "player_show": 0,
    "player_hide": 0,
    "player_toggle": 0,
}


def _script_int(value: str, line_no: int, command: str) -> int:
    try:
        return int(value, 0)
    except ValueError:
        raise ValueError(f"line {line_no}: {command}: expected an integer, got {value!r}")


def parse_script(script: str) -> tuple[dict[str, list[tuple[int, str, list[str]]]], list[str]]:
    events: dict[str, list[tuple[int, str, list[str]]]] = {e: [] for e in SCRIPT_EVENTS}
    errors: list[str] = []
    current_event = "update"

    for line_no, raw in enumerate((script or "").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("@"):
            event_name = stripped[1:].strip().lower()
            if event_name not in SCRIPT_EVENTS:
                errors.append(
                    f"line {line_no}: unknown event @{event_name}. "
                    f"Use one of: {', '.join('@' + e for e in SCRIPT_EVENTS)}"
                )
            else:
                current_event = event_name
            continue

        try:
            parts = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            errors.append(f"line {line_no}: {exc}")
            continue
        if not parts:
            continue

        command = parts[0].lower()
        args = parts[1:]
        if command not in _SCRIPT_ARITY:
            errors.append(f"line {line_no}: unknown command {command!r}")
            continue
        want = _SCRIPT_ARITY[command]
        if len(args) != want:
            errors.append(f"line {line_no}: {command} expects {want} argument(s), got {len(args)}")
            continue

        string_first = command in {
            "move_object", "set_object_pos", "spin_object", "set_object_rot",
            "show_object", "hide_object", "toggle_object",
        }
        valid = True
        for ai, arg in enumerate(args):
            if command == "set_sky":
                break
            if string_first and ai == 0:
                continue
            try:
                int(arg, 0)
            except ValueError:
                errors.append(f"line {line_no}: {command}: expected integer, got {arg!r}")
                valid = False
                break
        if valid:
            events[current_event].append((line_no, command, args))

    return events, errors


def _resolve_object_ref(project: dict[str, Any], ref: str, self_index: int | None = None) -> int:
    objects = project.get("objects", [])
    if ref.lower() == "self":
        if self_index is None:
            raise ValueError("'self' is not valid here")
        return self_index

    raw = ref[1:] if ref.startswith("#") else ref
    if raw.isdigit():
        idx = int(raw)
        if 0 <= idx < len(objects):
            return idx
        raise ValueError(f"object index {idx} is out of range")

    matches = [i for i, obj in enumerate(objects) if str(obj.get("name", "")).casefold() == ref.casefold()]
    if not matches:
        raise ValueError(f"no object named {ref!r}")
    if len(matches) > 1:
        raise ValueError(f"object name {ref!r} is ambiguous; use #index instead")
    return matches[0]


def _parse_sky_color(value: str) -> int:
    try:
        return int(value, 0) & 15
    except ValueError:
        pass
    matches = [i for i, name in enumerate(COLOR_NAMES) if name.casefold() == value.casefold()]
    if not matches:
        raise ValueError(
            f"unknown sky color {value!r}; use 0..15 or one of: {', '.join(COLOR_NAMES)}"
        )
    return matches[0]


def validate_object_script(project: dict[str, Any], object_index: int, script: str | None = None) -> list[str]:
    obj = project.get("objects", [])[object_index]
    events, errors = parse_script(obj.get("script", "") if script is None else script)
    result = list(errors)
    if result:
        return result

    for event_cmds in events.values():
        for line_no, command, args in event_cmds:
            try:
                if command in {
                    "move_object", "set_object_pos", "spin_object", "set_object_rot",
                    "show_object", "hide_object", "toggle_object",
                }:
                    _resolve_object_ref(project, args[0], object_index)
                if command == "set_sky":
                    _parse_sky_color(args[0])
            except ValueError as exc:
                result.append(f"line {line_no}: {command}: {exc}")
    return result


def _script_rotation_targets(project: dict[str, Any]) -> set[int]:
    targets: set[int] = set()
    for oi, obj in enumerate(project.get("objects", [])):
        events, errors = parse_script(obj.get("script", ""))
        if errors:
            continue
        for cmds in events.values():
            for _, command, args in cmds:
                if command in {"spin", "set_rot"}:
                    targets.add(oi)
                elif command in {"spin_object", "set_object_rot"}:
                    try:
                        targets.add(_resolve_object_ref(project, args[0], oi))
                    except ValueError:
                        pass
    return targets


def _compile_script_command(
    project: dict[str, Any],
    self_index: int,
    line_no: int,
    command: str,
    args: list[str],
) -> list[str]:
    def n(i: int) -> int:
        return _script_int(args[i], line_no, command)

    def target() -> int:
        return _resolve_object_ref(project, args[0], self_index)

    lines: list[str] = []
    i = self_index

    if command == "move":
        lines.append(f"scene_objects[{i}].x += {n(0)};")
        lines.append(f"scene_objects[{i}].y += {n(1)};")
        lines.append(f"scene_objects[{i}].z += {n(2)};")
    elif command == "set_pos":
        lines.append(f"scene_objects[{i}].x = {n(0)};")
        lines.append(f"scene_objects[{i}].y = {n(1)};")
        lines.append(f"scene_objects[{i}].z = {n(2)};")
    elif command == "spin":
        lines.append(f"scene_objects[{i}].rot_x = (uint8_t)(scene_objects[{i}].rot_x + ({n(0)}));")
        lines.append(f"scene_objects[{i}].rot_y = (uint8_t)(scene_objects[{i}].rot_y + ({n(1)}));")
    elif command == "set_rot":
        lines.append(f"scene_objects[{i}].rot_x = (uint8_t)({n(0)} & 63);")
        lines.append(f"scene_objects[{i}].rot_y = (uint8_t)({n(1)} & 63);")
    elif command == "show":
        lines.append(f"scene_objects[{i}].visible = 1u;")
    elif command == "hide":
        lines.append(f"scene_objects[{i}].visible = 0u;")
    elif command == "toggle":
        lines.append(f"scene_objects[{i}].visible ^= 1u;")

    elif command in {"move_object", "set_object_pos", "spin_object", "set_object_rot",
                     "show_object", "hide_object", "toggle_object"}:
        t = target()
        if command == "move_object":
            lines.append(f"scene_objects[{t}].x += {_script_int(args[1], line_no, command)};")
            lines.append(f"scene_objects[{t}].y += {_script_int(args[2], line_no, command)};")
            lines.append(f"scene_objects[{t}].z += {_script_int(args[3], line_no, command)};")
        elif command == "set_object_pos":
            lines.append(f"scene_objects[{t}].x = {_script_int(args[1], line_no, command)};")
            lines.append(f"scene_objects[{t}].y = {_script_int(args[2], line_no, command)};")
            lines.append(f"scene_objects[{t}].z = {_script_int(args[3], line_no, command)};")
        elif command == "spin_object":
            lines.append(f"scene_objects[{t}].rot_x = (uint8_t)(scene_objects[{t}].rot_x + ({_script_int(args[1], line_no, command)}));")
            lines.append(f"scene_objects[{t}].rot_y = (uint8_t)(scene_objects[{t}].rot_y + ({_script_int(args[2], line_no, command)}));")
        elif command == "set_object_rot":
            lines.append(f"scene_objects[{t}].rot_x = (uint8_t)({_script_int(args[1], line_no, command)} & 63);")
            lines.append(f"scene_objects[{t}].rot_y = (uint8_t)({_script_int(args[2], line_no, command)} & 63);")
        elif command == "show_object":
            lines.append(f"scene_objects[{t}].visible = 1u;")
        elif command == "hide_object":
            lines.append(f"scene_objects[{t}].visible = 0u;")
        elif command == "toggle_object":
            lines.append(f"scene_objects[{t}].visible ^= 1u;")

    elif command == "camera_move":
        lines.append(f"camera.x += {n(0)}; camera.y += {n(1)}; camera.z += {n(2)};")
    elif command == "camera_set":
        lines.append(f"camera.x = {n(0)}; camera.y = {n(1)}; camera.z = {n(2)};")
    elif command == "camera_look":
        lines.append(f"camera.yaw = (uint8_t)(camera.yaw + ({n(0)}));")
        lines.append(f"camera.pitch = gb3d_clamp_pitch((int16_t)camera.pitch + ({n(1)}));")
    elif command == "camera_set_look":
        lines.append(f"camera.yaw = (uint8_t)({n(0)} & 63);")
        lines.append(f"camera.pitch = gb3d_clamp_pitch({n(1)});")
    elif command == "reset_camera":
        lines.append("camera.x = SCENE_CAMERA_X; camera.y = SCENE_CAMERA_Y; camera.z = SCENE_CAMERA_Z;")
        lines.append("camera.yaw = SCENE_CAMERA_YAW; camera.pitch = SCENE_CAMERA_PITCH;")
    elif command == "set_sky":
        lines.append(f"gb3d_set_sky_logical({_parse_sky_color(args[0])}u);")
    elif command == "player_move":
        lines.append(f"gb3d_player_move({n(0)}, {n(1)}, {n(2)});")
    elif command == "player_set":
        lines.append(f"gb3d_player_set({n(0)}, {n(1)}, {n(2)});")
    elif command == "player_jump":
        lines.append("gb3d_player_jump();")
    elif command == "player_show":
        lines.append("gb3d_player_set_visible(1u);")
    elif command == "player_hide":
        lines.append("gb3d_player_set_visible(0u);")
    elif command == "player_toggle":
        lines.append("gb3d_player_toggle_visible();")

    return lines


def generate_script_c(project: dict[str, Any]) -> list[str]:
    compiled: dict[str, list[str]] = {event: [] for event in SCRIPT_EVENTS}
    for oi, obj in enumerate(project.get("objects", [])):
        events, errors = parse_script(obj.get("script", ""))
        if errors:
            continue
        for event, cmds in events.items():
            for line_no, command, args in cmds:
                compiled[event].extend(_compile_script_command(project, oi, line_no, command, args))

    lines: list[str] = []
    for event in SCRIPT_EVENTS:
        lines.append(f"static void scene_script_{event}(void) {{")
        if compiled[event]:
            for stmt in compiled[event]:
                lines.append(f"    {stmt}")
        else:
            lines.append("    /* empty */")
        lines.append("}")
        lines.append("")
    return lines


def _unique_c_names(models: list[dict[str, Any]]) -> dict[str, str]:
    used: set[str] = set()
    result: dict[str, str] = {}
    for m in models:
        base = safe_c_name(m.get("name", "model"))
        name = base
        n = 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        result[m["id"]] = name
    return result


def validate_for_export(project: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    models = project.get("models", [])
    objects = project.get("objects", [])
    if len(objects) > MAX_SCENE_OBJECTS:
        errors.append(f"Scene has {len(objects)} objects; runtime limit is {MAX_SCENE_OBJECTS}.")
    for m in models:
        verts = m.get("vertices", [])
        if len(verts) > MAX_MODEL_VERTICES:
            errors.append(f"{m.get('name','model')}: {len(verts)} vertices (limit {MAX_MODEL_VERTICES}).")
        if len(m.get("faces", [])) > MAX_MODEL_FACES:
            errors.append(f"{m.get('name','model')}: {len(m.get('faces', []))} triangles (limit {MAX_MODEL_FACES}).")
        for vi, v in enumerate(verts):
            if any(int(c) < -127 or int(c) > 127 for c in v[:3]):
                errors.append(
                    f"{m.get('name','model')}: vertex {vi} is outside V7's signed 8-bit local range (-127..127). "
                    "Re-import the OBJ at a smaller target size."
                )
                break
        for face in m.get("faces", []):
            if any(int(i) < 0 or int(i) >= len(verts) for i in face[:3]):
                errors.append(f"{m.get('name','model')}: face index is out of range.")
                break

    for oi, obj in enumerate(objects):
        for err in validate_object_script(project, oi):
            errors.append(f"{obj.get('name','Object')} script: {err}")

    sky = int(project.get("settings", {}).get("sky_color", 0))
    if not 0 <= sky < len(COLOR_NAMES):
        errors.append("Sky color must be between 0 and 15.")

    player = project.get("player", {})
    if player.get("enabled", False):
        pos = player.get("position", [0, 0, 0])
        if len(pos) < 3:
            errors.append("Player position must have X, Y and Z values.")
        if not 1 <= int(player.get("speed", 2)) <= 8:
            errors.append("Player speed must be 1..8.")
        if not 1 <= int(player.get("jump_speed", 7)) <= 15:
            errors.append("Player jump speed must be 1..15.")
        if not 1 <= int(player.get("gravity", 1)) <= 8:
            errors.append("Player gravity must be 1..8.")
        if not 1 <= int(player.get("half_width", 3)) <= 12:
            errors.append("Player half width must be 1..12.")
        if not 4 <= int(player.get("height", 12)) <= 32:
            errors.append("Player height must be 4..32.")
        pixels = player.get("sprite_pixels", [])
        if not isinstance(pixels, list) or len(pixels) != 1024:
            errors.append("Player sprite must contain exactly 32x32 = 1024 pixels.")
        elif any(int(v) < 0 or int(v) > 3 for v in pixels):
            errors.append("Player sprite pixels must use palette indices 0..3.")
        palette = player.get("sprite_palette", [])
        if not isinstance(palette, list) or len(palette) != 4:
            errors.append("Player sprite palette must contain exactly 4 colors.")
        if str(player.get("sprite_mode", "8x16")) not in ("8x8", "8x16"):
            errors.append("Player sprite mode must be 8x8 or 8x16.")
    return errors

def _face_palette(face: list[int], model: dict[str, Any]) -> int:
    logical = int(face[3] if len(face) > 3 else model.get("default_color", 1)) & 15
    return int(LOGICAL_TO_PALETTE[logical])


def _vertex_bounds(vertices: list[list[int]] | list[tuple[int, int, int]]) -> tuple[int, int, int, int, int, int]:
    if not vertices:
        return (0, 0, 0, 0, 0, 0)
    xs = [int(v[0]) for v in vertices]
    ys = [int(v[1]) for v in vertices]
    zs = [int(v[2]) for v in vertices]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def _bounds_radius(bounds: tuple[int, int, int, int, int, int]) -> int:
    # Conservative Chebyshev radius: intentionally cheap to test on the GBC.
    # OBJ import centers meshes, so this is usually tight enough.
    return max(1, min(127, max(abs(int(v)) for v in bounds)))


def generate_scene_header(project: dict[str, Any]) -> str:
    models = project.get("models", [])
    objects = project.get("objects", [])
    names = _unique_c_names(models)
    model_map = {m["id"]: m for m in models}
    runtime_rotation_targets = _script_rotation_targets(project)
    runtime_face_counts: dict[str, int] = {}

    lines: list[str] = [
        "// Generated by GB3D Studio 1.2. Do not edit by hand.",
        "#ifndef GB3D_SCENE_H",
        "#define GB3D_SCENE_H",
        "",
    ]

    for model in models:
        n = names[model["id"]]
        verts = model.get("vertices", [])
        # Editor wireframe edges are not used by the cartridge renderer at all,
        # so they are deliberately omitted from generated ROM data.
        # Palette 0xFF faces are never drawn. Strip them on the PC so the GBC
        # does not test the same permanently-invisible face every frame.
        faces = [face for face in model.get("faces", []) if _face_palette(face, model) != 0xFF]
        runtime_face_counts[model["id"]] = len(faces)

        lines.append(f"static const ModelVertex {n}_vertices[] = {{")
        for x, y, z in verts:
            lines.append(f"    {{{int(x)}, {int(y)}, {int(z)}}},")
        if not verts:
            lines.append("    {0, 0, 0},")
        lines.append("};\n")

        lines.append(f"static const Face3D {n}_faces[] = {{")
        for face in faces:
            a, b, c = face[:3]
            pal = _face_palette(face, model)
            pal_text = "0xFFu" if pal == 0xFF else f"{pal}u"
            lines.append(f"    {{{int(a)}, {int(b)}, {int(c)}, {pal_text}}},")
        if not faces:
            lines.append("    {0, 0, 0, 0u},")
        lines.append("};\n")

        bminx, bmaxx, bminy, bmaxy, bminz, bmaxz = _vertex_bounds(verts)
        bradius = _bounds_radius((bminx, bmaxx, bminy, bmaxy, bminz, bmaxz))
        lines.extend([
            f"static const Model3D {n}_model = {{",
            f"    {n}_vertices,",
            f"    {n}_faces,",
            f"    {len(verts)}u,",
            f"    {len(faces)}u,",
            f"    {bminx}, {bmaxx}, {bminy}, {bmaxy}, {bminz}, {bmaxz},",
            f"    {bradius}u",
            "};",
            "",
        ])

    object_model_expr: dict[str, tuple[str, int, int]] = {}
    for oi, obj in enumerate(objects):
        model = model_map.get(obj.get("model_id", ""))
        if not model:
            continue
        base = names[model["id"]]
        rx, ry = [int(v) & 63 for v in (obj.get("rotation") or [0, 0])[:2]]
        expr = f"&{base}_model"
        out_rx, out_ry = rx, ry

        # A second 8-bit rotated copy is only needed for Solid collision bounds.
        # Non-solid immutable objects render from the compiler-generated 16-bit
        # world vertices below, so duplicating their local mesh in ROM is waste.
        if (rx or ry) and oi not in runtime_rotation_targets and bool(obj.get("solid", False)):
            rotated = [rotate_vertex(v, rx, ry) for v in model.get("vertices", [])]
            if rotated and all(-127 <= c <= 127 for v in rotated for c in v):
                baked = f"scene_obj_{oi}_{base}_baked"
                lines.append(f"// Object {oi}: static rotation baked by the PC exporter.")
                lines.append(f"static const ModelVertex {baked}_vertices[] = {{")
                for x, y, z in rotated:
                    lines.append(f"    {{{int(x)}, {int(y)}, {int(z)}}},")
                lines.append("};")
                bbminx, bbmaxx, bbminy, bbmaxy, bbminz, bbmaxz = _vertex_bounds(rotated)
                bbradius = _bounds_radius((bbminx, bbmaxx, bbminy, bbmaxy, bbminz, bbmaxz))
                lines.extend([
                    f"static const Model3D {baked}_model = {{",
                    f"    {baked}_vertices,",
                    f"    {base}_faces,",
                    f"    {len(model.get('vertices', []))}u,",
                    f"    {runtime_face_counts.get(model['id'], len(model.get('faces', [])))}u,",
                    f"    {bbminx}, {bbmaxx}, {bbminy}, {bbmaxy}, {bbminz}, {bbmaxz},",
                    f"    {bbradius}u",
                    "};",
                    "",
                ])
                expr = f"&{baked}_model"
                out_rx = 0
                out_ry = 0

        object_model_expr[obj.get("id", f"obj{oi}")] = (expr, out_rx, out_ry)

    lines.append(f"#define SCENE_OBJECT_COUNT {len(objects)}u")
    lines.append("static SceneObject3D scene_objects[(SCENE_OBJECT_COUNT > 0u) ? SCENE_OBJECT_COUNT : 1u] = {")
    if objects:
        for oi, obj in enumerate(objects):
            model = model_map.get(obj.get("model_id", ""))
            if not model:
                lines.append("    {0, 0, 0, 0, 0u, 0u, 0u, 0u},")
                continue
            base = names[model["id"]]
            expr, rx, ry = object_model_expr.get(obj.get("id", f"obj{oi}"), (f"&{base}_model", 0, 0))
            x, y, z = [int(v) for v in (obj.get("position") or [0, 0, 0])[:3]]
            visible = 1 if obj.get("visible", True) else 0
            solid = 1 if obj.get("solid", False) else 0
            lines.append(f"    {{{expr}, {x}, {y}, {z}, {rx}u, {ry}u, {visible}u, {solid}u}},")
    else:
        lines.append("    {0, 0, 0, 0, 0u, 0u, 0u, 0u},")
    lines.append("};\n")

    # Runtime acceleration tables. Collision only scans objects which were
    # exported as Solid. Static scene objects also receive a coarse 64-unit
    # world-sector coordinate so far-away chunks can be rejected before any
    # camera rotation/projection math is paid.
    solid_indices = [i for i, obj in enumerate(objects) if bool(obj.get("solid", False))]
    moving_targets = _script_position_targets(project)
    lines.append(f"#define SCENE_SOLID_COUNT {len(solid_indices)}u")
    lines.append("static const uint8_t scene_solid_indices[(SCENE_SOLID_COUNT > 0u) ? SCENE_SOLID_COUNT : 1u] = {")
    if solid_indices:
        lines.append("    " + ", ".join(f"{i}u" for i in solid_indices))
    else:
        lines.append("    0u")
    lines.append("};")
    sectorable = []
    sector_x = []
    sector_z = []
    for oi, obj in enumerate(objects):
        pos = (obj.get("position") or [0, 0, 0])[:3]
        x, z = int(pos[0]), int(pos[2])
        sectorable.append(0 if oi in moving_targets else 1)
        sector_x.append(math.floor(x / 64))
        sector_z.append(math.floor(z / 64))
    arrn = max(1, len(objects))
    lines.append(f"#define SCENE_SECTOR_CULLING {1 if project.get('settings', {}).get('sector_culling', True) else 0}u")
    lines.append("#define SCENE_SECTOR_SIZE 64")
    lines.append("#define SCENE_SECTOR_RADIUS 6")
    lines.append(f"static const int16_t scene_object_sector_x[{arrn}] = {{")
    lines.append("    " + (", ".join(str(v) for v in sector_x) if sector_x else "0"))
    lines.append("};")
    lines.append(f"static const int16_t scene_object_sector_z[{arrn}] = {{")
    lines.append("    " + (", ".join(str(v) for v in sector_z) if sector_z else "0"))
    lines.append("};")
    lines.append(f"static const uint8_t scene_object_sector_static[{arrn}] = {{")
    lines.append("    " + (", ".join(f"{v}u" for v in sectorable) if sectorable else "0u"))
    lines.append("};")
    lines.append(f"#define SCENE_PROFILE_BUILD {1 if project.get('settings', {}).get('profile_build', False) else 0}u")
    lines.append("")

    # Compiler-first static render path. If scripts never change an object's
    # position or rotation, Studio resolves the entire object transform on the
    # PC and exports 16-bit world-space vertices. This deliberately spends ROM
    # to save SM83 work every frame. A locked camera orientation can be folded
    # into those world vertices too, leaving only camera-origin subtraction.
    settings = project.get("settings", {})
    fixed_fast = bool(settings.get("fixed_camera_fast", False))
    cam = project.get("camera", {})
    player = project.get("player", {})
    fixed_yaw = int(cam.get("yaw", 0)) & 63
    fixed_pitch = (max(-12, min(12, int(player.get("camera_pitch", -4))))
                   if player.get("enabled", False) else int(cam.get("pitch", 0)))

    render_entries: list[tuple[str, int, int, int, int]] = []
    baked_static_count = 0
    for oi, obj in enumerate(objects):
        model = model_map.get(obj.get("model_id", ""))
        pos = (obj.get("position") or [0, 0, 0])[:3]
        px, py, pz = map(int, pos)
        if not model:
            render_entries.append(("0", px, py, pz, 0))
            continue

        transform_static = oi not in moving_targets and oi not in runtime_rotation_targets
        vertices = model.get("vertices", [])
        if not transform_static or not vertices:
            render_entries.append(("0", px, py, pz, 0))
            continue

        rx, ry = [int(v) & 63 for v in (obj.get("rotation") or [0, 0])[:2]]
        rotated_local = [rotate_vertex(v, rx, ry) for v in vertices]
        world_vertices = [(vx + px, vy + py, vz + pz) for vx, vy, vz in rotated_local]
        if not all(-32768 <= c <= 32767 for v in world_vertices for c in v):
            render_entries.append(("0", px, py, pz, 0))
            continue

        camera_baked = 0
        render_vertices = world_vertices
        ox, oy, oz = px, py, pz
        if fixed_fast:
            # Preserve the previous fixed-camera fast path bit-for-bit: rotate
            # local geometry and object origin separately, then add them. This
            # keeps the same fixed-point rounding while moving the final adds
            # from the GBC to the exporter.
            camera_local = [rotate_vertex(v, fixed_pitch, fixed_yaw) for v in rotated_local]
            ox, oy, oz = rotate_vertex((px, py, pz), fixed_pitch, fixed_yaw)
            render_vertices = [(vx + ox, vy + oy, vz + oz) for vx, vy, vz in camera_local]
            if not all(-32768 <= c <= 32767 for v in render_vertices for c in v):
                render_entries.append(("0", px, py, pz, 0))
                continue
            camera_baked = 1

        base = names[model["id"]]
        baked = f"scene_world_obj_{oi}_{base}"
        lines.append(f"// Object {oi}: immutable transform baked completely by the PC exporter.")
        if fixed_fast:
            lines.append("// Locked camera orientation is baked too: runtime does only camera-origin subtraction.")
        lines.append(f"static const WorldVertex {baked}_vertices[] = {{")
        for vx, vy, vz in render_vertices:
            lines.append(f"    {{{int(vx)}, {int(vy)}, {int(vz)}}},")
        lines.append("};")
        lines.append("")
        render_entries.append((f"{baked}_vertices", ox, oy, oz, camera_baked))
        baked_static_count += 1

    lines.append(f"#define SCENE_FIXED_CAMERA_FAST {1 if fixed_fast else 0}u")
    lines.append(f"#define SCENE_FIXED_CAMERA_YAW {fixed_yaw}u")
    lines.append(f"#define SCENE_FIXED_CAMERA_PITCH {fixed_pitch}")
    lines.append(f"#define SCENE_STATIC_WORLD_BAKED_COUNT {baked_static_count}u")
    lines.append("static const SceneRenderInfo scene_render_info[(SCENE_OBJECT_COUNT > 0u) ? SCENE_OBJECT_COUNT : 1u] = {")
    if objects:
        for vertex_expr, ox, oy, oz, baked in render_entries:
            lines.append(f"    {{{vertex_expr}, {int(ox)}, {int(oy)}, {int(oz)}, {int(baked)}u}},")
    else:
        lines.append("    {0, 0, 0, 0, 0u},")
    lines.append("};\n")

    sky = int(project.get("settings", {}).get("sky_color", 0)) & 15
    ppos = (player.get("position") or [0, 0, 0])[:3]
    lines.extend([
        f"#define SCENE_CAMERA_X {int(cam.get('x', 0))}",
        f"#define SCENE_CAMERA_Y {int(cam.get('y', 0))}",
        f"#define SCENE_CAMERA_Z {int(cam.get('z', -64))}",
        f"#define SCENE_CAMERA_YAW {int(cam.get('yaw', 0)) & 63}u",
        f"#define SCENE_CAMERA_PITCH {int(cam.get('pitch', 0))}",
        f"#define SCENE_SKY_COLOR {sky}u",
        f"#define SCENE_PLAYER_ENABLED {1 if player.get('enabled', False) else 0}u",
        f"#define SCENE_PLAYER_X {int(ppos[0])}",
        f"#define SCENE_PLAYER_Y {int(ppos[1])}",
        f"#define SCENE_PLAYER_Z {int(ppos[2])}",
        f"#define SCENE_PLAYER_SPEED {max(1, min(8, int(player.get('speed', 2))))}u",
        f"#define SCENE_PLAYER_JUMP_SPEED {max(1, min(15, int(player.get('jump_speed', 7))))}",
        f"#define SCENE_PLAYER_GRAVITY {max(1, min(8, int(player.get('gravity', 1))))}",
        f"#define SCENE_PLAYER_GROUND_Y {int(player.get('ground_y', 0))}",
        f"#define SCENE_PLAYER_HALF_WIDTH {max(1, min(12, int(player.get('half_width', 3))))}",
        f"#define SCENE_PLAYER_HEIGHT {max(4, min(32, int(player.get('height', 12))))}",
        f"#define SCENE_PLAYER_CAMERA_DISTANCE {max(12, min(120, int(player.get('camera_distance', 48))))}",
        f"#define SCENE_PLAYER_CAMERA_HEIGHT {max(-64, min(96, int(player.get('camera_height', 24))))}",
        f"#define SCENE_PLAYER_CAMERA_PITCH {max(-12, min(12, int(player.get('camera_pitch', -4))))}",
        f"#define SCENE_PLAYER_COLOR {int(player.get('color', 14)) & 15}u",
        f"#define SCENE_PLAYER_VISIBLE {1 if player.get('visible', True) else 0}u",
        f"#define SCENE_PLAYER_SPRITE_8X16 {1 if str(player.get('sprite_mode', '8x16')) == '8x16' else 0}u",
        f"#define SCENE_PLAYER_SPRITE_OAM_COUNT {8 if str(player.get('sprite_mode', '8x16')) == '8x16' else 16}u",
        f"#define SCENE_PLAYER_SPRITE_TILE_COUNT 16u",
        "",
    ])
    sprite_bytes = player_sprite_tile_bytes(player)
    lines.append("// 32x32 player image converted to native Game Boy 2bpp sprite tiles.")
    lines.append("static const uint8_t scene_player_sprite_tiles[256] = {")
    for i in range(0, len(sprite_bytes), 16):
        lines.append("    " + ", ".join(str(v) for v in sprite_bytes[i:i+16]) + ",")
    lines.append("};")
    palette = copy.deepcopy(player.get('sprite_palette', default_player_sprite_palette(int(player.get('color', 14)))))
    if not isinstance(palette, list) or len(palette) != 4:
        palette = default_player_sprite_palette(int(player.get('color', 14)))
    lines.append("static const palette_color_t scene_player_sprite_palette[4] = {")
    for rgb in palette:
        rr, gg, bb = [max(0, min(255, int(c))) for c in list(rgb)[:3]]
        r5 = round(rr * 31 / 255); g5 = round(gg * 31 / 255); b5 = round(bb * 31 / 255)
        lines.append(f"    RGB({r5}, {g5}, {b5}),")
    lines.append("};")
    lines.extend([
        "",
        "// Export-time compiled GB3D scripts. No parser/VM runs on the Game Boy.",
    ])
    lines.extend(generate_script_c(project))
    lines.extend([
        "#endif",
        "",
    ])
    return "\n".join(lines)

def export_gbdk(project: dict[str, Any], output_dir: str | os.PathLike[str], template_dir: str | os.PathLike[str]) -> list[Path]:
    errors = validate_for_export(project)
    if errors:
        raise ValueError("Cannot export:\n" + "\n".join(errors))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    template_dir = Path(template_dir)

    scene_path = out / "scene_gb3d.h"
    scene_path.write_text(generate_scene_header(project), encoding="utf-8")

    main_template = (template_dir / "main.c.tpl").read_text(encoding="utf-8")
    (out / "main.c").write_text(main_template, encoding="utf-8")
    shutil.copy2(template_dir / "gb3d_render.s", out / "gb3d_render.s")
    shutil.copy2(template_dir / "Makefile", out / "Makefile")
    shutil.copy2(template_dir / "compile.bat", out / "compile.bat")

    save_project(project, out / "scene.gb3d")
    readme = (
        "GB3D Studio 1.2 export - correct DDA raster + fast math + CGB GDMA\n"
        "=======================================\n\n"
        "Compile with compile.bat (GBDK in PATH) or make with GBDK_HOME set.\n\n"
        "Studio 1.2 runtime:\n"
        "  rasterizes triangles directly into final tile IDs + CGB attributes\n"
        "  no 16-color intermediate framebuffer\n"
        "  no per-frame 8-palette search / tile palette compiler\n"
        "  whole scan-converted triangle lives in gb3d_render.s\n"
        "  native 32x18 RAM map stride for direct CGB GDMA upload\n"
        "  exact quotient/remainder DDA rasterizer; no per-row edge while-loop\n"
        "  reciprocal table removes generic edge division\n"
        "  quarter-square tables replace hot signed multiply helpers\n"
        "  global 16-bucket triangle queue across all visible objects\n"
        "  optional static sector reject before vertex transforms\n"
        "  visible-only framebuffer clear; hidden BG-map columns stay untouched\n"
        "  compiler-first static objects bake rotation + translation into 16-bit world vertices\n"
        "  optional Fixed Camera Fast Path also folds locked camera rotation into static vertices\n"
        "  dynamic scripted objects automatically keep the general runtime transform path\n"
        "  projection scale is a precomputed ROM table; no startup division/256-byte WRAM table\n"
        "  triangle bucket reset + flush/list traversal run in SM83 assembly\n"
        "  editor-only edge lists and permanently invisible faces are stripped from ROM\n"
        "  model transform loops project inline without a per-vertex C helper round-trip\n"
        "  model-local source vertices remain signed 8-bit\n"
        "  16 coarse depth buckets remain for cheap painter ordering\n"
        "  object scripts compile to native C at export time (no runtime parser)\n"
        "  selectable 16-color sky background\n"
        "  optional built-in third-person platformer player using hardware sprites\n"
        "  scriptable 32x32 hardware-sprite NPC billboards\n"
        "  GBScript 2 variables, expressions, conditions, loops, NPC and HUD commands\n"
        "  native Window-layer 8x8 text + value bars; UI never touches the 3D framebuffer\n"
        "  Window map is rebuilt/uploaded only when UI state changes\n"
        "  simple gravity/jump plus AABB collision against Solid scene objects\n\n"
        "Color rule:\n"
        "  each hardware 8x8 tile still has ONE CGB palette. All eight palettes\n"
        "  share shade indices 0/1/2/3. If different materials overlap the same\n"
        "  tile, the nearest (latest drawn) triangle palette wins that tile, so\n"
        "  existing quadrants keep brightness but may inherit its hue.\n\n"
        "Controls:\n"
        "  D-Pad: move camera\n"
        "  Hold Select + D-Pad: look\n"
        "  A/B/Start/Select/D-Pad press+held events are available to scripts\n"
        "  Start is no longer hardwired to camera reset; use reset_camera in a script\n"
        "  Player mode: D-Pad move, hold Select+D-Pad orbit/look, A jump\n"
    )
    (out / "README.txt").write_text(readme, encoding="utf-8")
    return [scene_path, out / "main.c", out / "gb3d_render.s", out / "Makefile", out / "compile.bat", out / "scene.gb3d", out / "README.txt"]


def performance_report(project: dict[str, Any]) -> dict[str, Any]:
    """PC-side estimate of runtime work. Hardware profiler is still authoritative."""
    models = {m.get("id"): m for m in project.get("models", [])}
    visible_objects = [(i, o) for i, o in enumerate(project.get("objects", [])) if o.get("visible", True)]
    dynamic_rotation = _script_rotation_targets(project)
    dynamic_position = _script_position_targets(project)
    dynamic_transform = dynamic_rotation | dynamic_position
    vertices = faces = 0
    static_vertices = dynamic_vertices = 0
    dynamic_objects = 0
    static_baked_objects = 0
    solid = 0
    for i, obj in visible_objects:
        m = models.get(obj.get("model_id"))
        if not m:
            continue
        vc = len(m.get("vertices", []))
        vertices += vc
        faces += len(m.get("faces", []))
        if i in dynamic_transform:
            dynamic_vertices += vc
        else:
            static_vertices += vc
            static_baked_objects += 1
        if i in dynamic_rotation:
            dynamic_objects += 1
        if obj.get("solid", False):
            solid += 1
    fixed_camera_fast = bool(project.get("settings", {}).get("fixed_camera_fast", False))
    # Static world vertices remove object transform work even with a free camera.
    # Locked camera orientation removes camera rotation from those static vertices too.
    static_weight = 3 if fixed_camera_fast else 8
    dynamic_weight = 10
    score = static_vertices * static_weight + dynamic_vertices * dynamic_weight + faces * 3 + len(visible_objects) * 20
    warnings = []
    if vertices > 160 and not fixed_camera_fast:
        warnings.append("High transformed-vertex count; immutable objects are PC-baked, but a locked camera can remove more vertex math.")
    elif vertices > 260 and fixed_camera_fast:
        warnings.append("High vertex count even with Fixed Camera Fast Path; sectoring/culling may still help.")
    if faces > 160:
        warnings.append("High total triangle count; object/frustum culling matters a lot here.")
    if dynamic_objects:
        warnings.append(f"{dynamic_objects} object(s) need runtime rotation; immutable transforms are cheaper because the exporter bakes them.")
    if len(project.get("ui", [])) > 12:
        warnings.append("Large Window HUD: edits are event-driven, but rebuilding many elements at once costs some CPU.")
    if len(project.get("npcs", [])) >= 3:
        warnings.append("Several NPC metasprites increase OAM update work and may hit scanline sprite limits.")
    return {
        "objects": len(visible_objects), "vertices": vertices, "faces": faces,
        "solid": solid, "dynamic_rotation": dynamic_objects,
        "static_baked_objects": static_baked_objects,
        "static_baked_vertices": static_vertices, "dynamic_vertices": dynamic_vertices,
        "npcs": len(project.get("npcs", [])), "ui": len(project.get("ui", [])),
        "cost_score": score, "fixed_camera_fast": fixed_camera_fast, "warnings": warnings,
    }

def duplicate_project(project: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(project)
# ============================================================
# GB3D V7 EXTENSIONS: NPCs, GBScript 2, and HUD UI
# ============================================================
# This block intentionally overrides a few V6 helpers below. Keeping the V6
# implementations above makes old project files and the renderer code easy to
# compare while V7 adds higher-level game features.

import ast as _gb_ast

MAX_NPCS = 4
MAX_UI_ELEMENTS = 16

_v6_new_project = new_project
_v6_load_project = load_project
_v6_render_scene = render_scene
_v6_validate_for_export = validate_for_export
_v6_generate_scene_header = generate_scene_header


def make_npc(name: str = "NPC") -> dict[str, Any]:
    return {
        "id": new_id("npc"),
        "name": name,
        "position": [0, 0, 0],
        "height": 12,
        "visible": True,
        "sprite_pixels": default_player_sprite_pixels(),
        "sprite_palette": default_player_sprite_palette(10),
        "sprite_source_name": "Built-in critter",
        "script": "",
    }


def make_ui_text(name: str = "Label") -> dict[str, Any]:
    return {
        "id": new_id("ui"),
        "name": name,
        "type": "text",
        # Window-layer coordinates are hardware 8x8 tile cells (20x18 map).
        "x": 0,
        "y": 0,
        "text": name.upper()[:18],
        "color": 1,
        "visible": True,
        "show_value": False,
        "value": 0,
    }


def make_ui_bar(name: str = "Bar") -> dict[str, Any]:
    return {
        "id": new_id("ui"),
        "name": name,
        "type": "bar",
        "x": 0,
        "y": 1,
        "width": 10,
        "value": 10,
        "max": 10,
        "color": 4,
        "bg_color": 3,
        "visible": True,
    }


def _normalize_npc(npc: dict[str, Any]) -> None:
    npc.setdefault("id", new_id("npc"))
    npc.setdefault("name", "NPC")
    npc.setdefault("position", [0, 0, 0])
    npc.setdefault("height", 12)
    npc.setdefault("visible", True)
    npc.setdefault("script", "")
    tmp = {
        "sprite_pixels": npc.get("sprite_pixels"),
        "sprite_palette": npc.get("sprite_palette"),
        "sprite_mode": "8x16",
        "sprite_source_name": npc.get("sprite_source_name", "Built-in critter"),
        "color": 10,
    }
    _normalize_player_sprite(tmp)
    npc["sprite_pixels"] = tmp["sprite_pixels"]
    npc["sprite_palette"] = tmp["sprite_palette"]
    npc["sprite_source_name"] = tmp["sprite_source_name"]


def _normalize_ui(item: dict[str, Any]) -> None:
    item.setdefault("id", new_id("ui"))
    item.setdefault("name", "UI")
    item["type"] = "bar" if str(item.get("type", "text")).lower() == "bar" else "text"
    # UI is now native Window-layer tilemap data: 20x18 cells, not 40x36
    # chunky framebuffer pixels.
    item["x"] = max(0, min(19, int(item.get("x", 0))))
    item["y"] = max(0, min(17, int(item.get("y", 0))))
    item["visible"] = bool(item.get("visible", True))
    item["color"] = int(item.get("color", 1)) & 15
    item["value"] = int(item.get("value", 0))
    if item["type"] == "text":
        item["text"] = str(item.get("text", item.get("name", "TEXT")))[:18]
        item["show_value"] = bool(item.get("show_value", False))
    else:
        item["width"] = max(1, min(20, int(item.get("width", 10))))
        item["max"] = max(1, int(item.get("max", 10)))
        item["bg_color"] = int(item.get("bg_color", 3)) & 15


def _normalize_window_ui(settings: dict[str, Any]) -> None:
    settings.setdefault("fixed_camera_fast", False)
    settings["fixed_camera_fast"] = bool(settings.get("fixed_camera_fast", False))
    # Profiling is a compile-time build mode in 1.2. Release exports default
    # to zero profiler bookkeeping in the hot path.
    settings.setdefault("profile_build", False)
    settings["profile_build"] = bool(settings.get("profile_build", False))
    # Coarse 64-unit static sector rejection happens before camera rotation.
    settings.setdefault("sector_culling", True)
    settings["sector_culling"] = bool(settings.get("sector_culling", True))
    win = settings.setdefault("window_ui", {})
    win.setdefault("enabled", False)
    win.setdefault("x", 0)
    win.setdefault("y", 112)
    win.setdefault("background", 0)
    win["enabled"] = bool(win.get("enabled", False))
    win["x"] = max(0, min(159, int(win.get("x", 0))))
    win["y"] = max(0, min(143, int(win.get("y", 112))))
    win["background"] = int(win.get("background", 0)) & 15


def new_project() -> dict[str, Any]:
    data = _v6_new_project()
    data["version"] = FORMAT_VERSION
    data.setdefault("npcs", [])
    data.setdefault("ui", [])
    _normalize_window_ui(data.setdefault("settings", {}))
    return data


def load_project(path: str | os.PathLike[str]) -> dict[str, Any]:
    # Keep the pre-upgrade version so V8 framebuffer-UI coordinates can be
    # migrated to V9 Window-layer tile coordinates.
    try:
        raw_version = int(json.loads(Path(path).read_text(encoding="utf-8")).get("version", 0))
    except Exception:
        raw_version = FORMAT_VERSION
    data = _v6_load_project(path)
    data.setdefault("npcs", [])
    for npc in data["npcs"]:
        _normalize_npc(npc)
    data.setdefault("ui", [])
    if raw_version < 6:
        # Old UI coordinates were 4x4 logical framebuffer pixels. Two of those
        # equal one native 8x8 Window cell. Bars used logical-pixel widths too.
        for item in data["ui"]:
            item["x"] = int(item.get("x", 0)) // 2
            item["y"] = int(item.get("y", 0)) // 2
            if str(item.get("type", "text")).lower() == "bar":
                item["width"] = max(1, (int(item.get("width", 10)) + 1) // 2)
    for item in data["ui"]:
        _normalize_ui(item)
    _normalize_window_ui(data.setdefault("settings", {}))
    data["version"] = FORMAT_VERSION
    return data


def npc_by_id(project: dict[str, Any], npc_id: str) -> dict[str, Any] | None:
    return next((n for n in project.get("npcs", []) if n.get("id") == npc_id), None)


def _resolve_npc_ref(project: dict[str, Any], ref: str, self_index: int | None = None) -> int:
    npcs = project.get("npcs", [])
    if ref.lower() == "self":
        if self_index is None:
            raise ValueError("'self' is not an NPC in this script")
        return self_index
    raw = ref[1:] if ref.startswith("#") else ref
    if raw.isdigit():
        idx = int(raw)
        if 0 <= idx < len(npcs):
            return idx
        raise ValueError(f"NPC index {idx} is out of range")
    matches = [i for i, npc in enumerate(npcs) if str(npc.get("name", "")).casefold() == ref.casefold()]
    if not matches:
        raise ValueError(f"no NPC named {ref!r}")
    if len(matches) > 1:
        raise ValueError(f"NPC name {ref!r} is ambiguous; use #index instead")
    return matches[0]


def _resolve_ui_ref(project: dict[str, Any], ref: str) -> int:
    items = project.get("ui", [])
    raw = ref[1:] if ref.startswith("#") else ref
    if raw.isdigit():
        idx = int(raw)
        if 0 <= idx < len(items):
            return idx
        raise ValueError(f"UI index {idx} is out of range")
    matches = [i for i, item in enumerate(items) if str(item.get("name", "")).casefold() == ref.casefold()]
    if not matches:
        raise ValueError(f"no UI element named {ref!r}")
    if len(matches) > 1:
        raise ValueError(f"UI name {ref!r} is ambiguous; use #index instead")
    return matches[0]


# ---------------------------------------------------------------------------
# GBScript 2
# ---------------------------------------------------------------------------
# Persistent variables + expressions + if/else + while + repeat + break /
# continue. Scripts still compile to native C, so there is no interpreter on
# the Game Boy.

_SCRIPT_COMMAND_SPECS = {
    "move": (3, False), "set_pos": (3, False), "spin": (2, False), "set_rot": (2, False),
    "show": (0, False), "hide": (0, False), "toggle": (0, False),
    "move_object": (4, True), "set_object_pos": (4, True), "spin_object": (3, True),
    "set_object_rot": (3, True), "show_object": (1, True), "hide_object": (1, True), "toggle_object": (1, True),
    "npc_move": (4, True), "npc_set": (4, True), "npc_show": (1, True), "npc_hide": (1, True), "npc_toggle": (1, True),
    "camera_move": (3, False), "camera_set": (3, False), "camera_look": (2, False), "camera_set_look": (2, False),
    "reset_camera": (0, False), "set_sky": (1, False),
    "player_move": (3, False), "player_set": (3, False), "player_jump": (0, False),
    "player_show": (0, False), "player_hide": (0, False), "player_toggle": (0, False),
    "ui_set_value": (2, True), "ui_add_value": (2, True), "ui_set_max": (2, True),
    "ui_show": (1, True), "ui_hide": (1, True), "ui_toggle": (1, True),
    "window_show": (0, False), "window_hide": (0, False), "window_toggle": (0, False),
    "window_move": (2, False),
}

_SCRIPT_KEYWORDS = {"var", "if", "else", "while", "repeat", "end", "break", "continue"}


def _split_top_level_commas(text: str) -> list[str]:
    result: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote:
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            result.append(text[start:i].strip())
            start = i + 1
    result.append(text[start:].strip())
    return result


def _strip_ref_arg(text: str) -> str:
    try:
        vals = shlex.split(text, comments=False, posix=True)
    except ValueError:
        vals = []
    if len(vals) == 1:
        return vals[0]
    return text.strip().strip('"').strip("'")


def _parse_command_args(command: str, rest: str, line_no: int) -> tuple[list[str], str | None]:
    want, string_first = _SCRIPT_COMMAND_SPECS[command]
    if want == 0:
        if rest.strip():
            return [], f"line {line_no}: {command} expects no arguments"
        return [], None

    # Commas enable full expressions containing spaces:
    #   move speed * 2, 0, -1
    # Old V6 whitespace-only commands remain valid.
    if "," in rest:
        parts = _split_top_level_commas(rest)
    else:
        try:
            parts = shlex.split(rest, comments=True, posix=True)
        except ValueError as exc:
            return [], f"line {line_no}: {exc}"

    if len(parts) != want:
        return [], f"line {line_no}: {command} expects {want} argument(s), got {len(parts)}"
    parts = [p.strip() for p in parts]
    if string_first and parts:
        parts[0] = _strip_ref_arg(parts[0])
    return parts, None


def _parse_script_program(script: str) -> tuple[dict[str, Any], list[str]]:
    program: dict[str, Any] = {
        "vars": [],
        "events": {e: [] for e in SCRIPT_EVENTS},
    }
    errors: list[str] = []
    current_event = "update"
    current_list = program["events"][current_event]
    stack: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    var_names: set[str] = set()

    for line_no, raw in enumerate((script or "").splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("@"):
            if stack:
                errors.append(f"line {line_no}: cannot change event inside an open control-flow block")
                continue
            event_name = stripped[1:].strip().lower()
            if event_name not in SCRIPT_EVENTS:
                errors.append(f"line {line_no}: unknown event @{event_name}")
            else:
                current_event = event_name
                current_list = program["events"][current_event]
            continue

        low = stripped.lower()
        if low.startswith("var "):
            if stack:
                errors.append(f"line {line_no}: variables are script-wide; declare var outside if/while/repeat blocks")
                continue
            body = stripped[4:].strip()
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(.+))?$", body)
            if not m:
                errors.append(f"line {line_no}: expected 'var name = expression'")
                continue
            name = m.group(1)
            expr = (m.group(2) or "0").strip()
            if name in var_names:
                errors.append(f"line {line_no}: variable {name!r} already exists")
                continue
            if name.lower() in _SCRIPT_KEYWORDS:
                errors.append(f"line {line_no}: {name!r} is reserved")
                continue
            var_names.add(name)
            program["vars"].append({"name": name, "expr": expr, "line": line_no})
            continue

        if low.startswith("if "):
            node = {"type": "if", "line": line_no, "expr": stripped[3:].strip(), "then": [], "else": []}
            current_list.append(node)
            stack.append(("if_then", node, current_list))
            current_list = node["then"]
            continue
        if low == "else":
            if not stack or stack[-1][0] != "if_then":
                errors.append(f"line {line_no}: else without matching if")
                continue
            kind, node, parent = stack[-1]
            stack[-1] = ("if_else", node, parent)
            current_list = node["else"]
            continue
        if low.startswith("while "):
            node = {"type": "while", "line": line_no, "expr": stripped[6:].strip(), "body": []}
            current_list.append(node)
            stack.append(("while", node, current_list))
            current_list = node["body"]
            continue
        if low.startswith("repeat "):
            node = {"type": "repeat", "line": line_no, "expr": stripped[7:].strip(), "body": []}
            current_list.append(node)
            stack.append(("repeat", node, current_list))
            current_list = node["body"]
            continue
        if low == "end":
            if not stack:
                errors.append(f"line {line_no}: end without open block")
                continue
            _, _, parent = stack.pop()
            current_list = parent
            continue
        if low in ("break", "continue"):
            if not any(kind in ("while", "repeat") for kind, _, _ in stack):
                errors.append(f"line {line_no}: {low} is only valid inside a loop")
            else:
                current_list.append({"type": low, "line": line_no})
            continue

        am = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(\+=|-=|\*=|/=|%=|=)\s*(.+)$", stripped)
        if am:
            name, op, expr = am.group(1), am.group(2), am.group(3).strip()
            if name not in var_names:
                errors.append(f"line {line_no}: assign to undeclared variable {name!r}; add 'var {name} = 0'")
            else:
                current_list.append({"type": "assign", "line": line_no, "name": name, "op": op, "expr": expr})
            continue

        first = stripped.split(None, 1)
        command = first[0].lower()
        rest = first[1] if len(first) > 1 else ""
        if command not in _SCRIPT_COMMAND_SPECS:
            errors.append(f"line {line_no}: unknown command {command!r}")
            continue
        args, err = _parse_command_args(command, rest, line_no)
        if err:
            errors.append(err)
            continue
        current_list.append({"type": "command", "line": line_no, "command": command, "args": args})

    if stack:
        errors.append("script ended before matching 'end' for one or more blocks")
    return program, errors


def parse_script(script: str) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    program, errors = _parse_script_program(script)
    return program["events"], errors


def _script_source_prefix(kind: str, index: int) -> str:
    return f"gbv_{kind}_{index}_"


def _compile_expr(expr: str, varmap: dict[str, str], kind: str, index: int, line_no: int) -> str:
    try:
        root = _gb_ast.parse(expr, mode="eval").body
    except SyntaxError as exc:
        raise ValueError(f"line {line_no}: invalid expression {expr!r}: {exc.msg}")

    builtins: dict[str, str] = {
        "true": "1", "false": "0", "frame": "gb3d_frame_counter",
        "player_x": "gb3d_player_get_x()", "player_y": "gb3d_player_get_y()", "player_z": "gb3d_player_get_z()",
        "camera_x": "camera.x", "camera_y": "camera.y", "camera_z": "camera.z",
        "camera_yaw": "camera.yaw", "camera_pitch": "camera.pitch",
    }
    if kind == "object":
        builtins.update({
            "self_x": f"scene_objects[{index}].x",
            "self_y": f"scene_objects[{index}].y",
            "self_z": f"scene_objects[{index}].z",
            "self_visible": f"scene_objects[{index}].visible",
        })
    elif kind == "npc":
        builtins.update({
            "self_x": f"npcs[{index}].x",
            "self_y": f"npcs[{index}].y",
            "self_z": f"npcs[{index}].z",
            "self_visible": f"npcs[{index}].visible",
        })

    def emit(node: _gb_ast.AST) -> str:
        if isinstance(node, _gb_ast.Constant) and isinstance(node.value, (int, bool)):
            return str(int(node.value))
        if isinstance(node, _gb_ast.Name):
            if node.id in varmap:
                return varmap[node.id]
            if node.id in builtins:
                return builtins[node.id]
            raise ValueError(f"line {line_no}: unknown name {node.id!r} in expression")
        if isinstance(node, _gb_ast.BinOp):
            ops = {
                _gb_ast.Add: "+", _gb_ast.Sub: "-", _gb_ast.Mult: "*", _gb_ast.Div: "/", _gb_ast.FloorDiv: "/",
                _gb_ast.Mod: "%", _gb_ast.BitAnd: "&", _gb_ast.BitOr: "|", _gb_ast.BitXor: "^",
                _gb_ast.LShift: "<<", _gb_ast.RShift: ">>",
            }
            op = ops.get(type(node.op))
            if not op:
                raise ValueError(f"line {line_no}: unsupported arithmetic operator")
            return f"({emit(node.left)} {op} {emit(node.right)})"
        if isinstance(node, _gb_ast.UnaryOp):
            ops = {_gb_ast.USub: "-", _gb_ast.UAdd: "+", _gb_ast.Not: "!", _gb_ast.Invert: "~"}
            op = ops.get(type(node.op))
            if not op:
                raise ValueError(f"line {line_no}: unsupported unary operator")
            return f"({op}{emit(node.operand)})"
        if isinstance(node, _gb_ast.BoolOp):
            op = " && " if isinstance(node.op, _gb_ast.And) else " || " if isinstance(node.op, _gb_ast.Or) else None
            if not op:
                raise ValueError(f"line {line_no}: unsupported boolean operator")
            return "(" + op.join(emit(v) for v in node.values) + ")"
        if isinstance(node, _gb_ast.Compare):
            opmap = {_gb_ast.Eq: "==", _gb_ast.NotEq: "!=", _gb_ast.Lt: "<", _gb_ast.LtE: "<=", _gb_ast.Gt: ">", _gb_ast.GtE: ">="}
            left = node.left
            chunks = []
            for opnode, right in zip(node.ops, node.comparators):
                op = opmap.get(type(opnode))
                if not op:
                    raise ValueError(f"line {line_no}: unsupported comparison")
                chunks.append(f"({emit(left)} {op} {emit(right)})")
                left = right
            return "(" + " && ".join(chunks) + ")"
        if isinstance(node, _gb_ast.Call) and isinstance(node.func, _gb_ast.Name):
            fn = node.func.id
            if fn == "abs" and len(node.args) == 1:
                return f"gb3d_abs16({emit(node.args[0])})"
            if fn == "min" and len(node.args) == 2:
                return f"gb3d_min16({emit(node.args[0])}, {emit(node.args[1])})"
            if fn == "max" and len(node.args) == 2:
                return f"gb3d_max16({emit(node.args[0])}, {emit(node.args[1])})"
            if fn == "rand" and len(node.args) == 1:
                return f"gb3d_rand16({emit(node.args[0])})"
            raise ValueError(f"line {line_no}: only abs(x), min(a,b), max(a,b), and rand(n) are allowed")
        raise ValueError(f"line {line_no}: unsupported expression syntax")

    return emit(root)


def _walk_nodes(nodes: list[dict[str, Any]]):
    for node in nodes:
        yield node
        if node["type"] == "if":
            yield from _walk_nodes(node["then"])
            yield from _walk_nodes(node["else"])
        elif node["type"] in ("while", "repeat"):
            yield from _walk_nodes(node["body"])


def _script_rotation_targets(project: dict[str, Any]) -> set[int]:
    targets: set[int] = set()
    for oi, obj in enumerate(project.get("objects", [])):
        program, errors = _parse_script_program(obj.get("script", ""))
        if errors:
            continue
        for event_nodes in program["events"].values():
            for node in _walk_nodes(event_nodes):
                if node["type"] != "command":
                    continue
                command, args = node["command"], node["args"]
                if command in {"spin", "set_rot"}:
                    targets.add(oi)
                elif command in {"spin_object", "set_object_rot"}:
                    try:
                        targets.add(_resolve_object_ref(project, args[0], oi))
                    except ValueError:
                        pass
    return targets


def _script_position_targets(project: dict[str, Any]) -> set[int]:
    """Objects whose world position can change at runtime.

    Static-sector coordinates are only safe for objects not touched by move /
    set-position script commands.
    """
    targets: set[int] = set()
    for kind, index, source in _script_sources(project):
        program, errors = _parse_script_program(source.get("script", ""))
        if errors:
            continue
        for event_nodes in program["events"].values():
            for node in _walk_nodes(event_nodes):
                if node["type"] != "command":
                    continue
                command, args = node["command"], node["args"]
                if kind == "object" and command in {"move", "set_pos"}:
                    targets.add(index)
                elif command in {"move_object", "set_object_pos"} and args:
                    try:
                        targets.add(_resolve_object_ref(project, args[0], index if kind == "object" else None))
                    except ValueError:
                        pass
    return targets


def _compile_script_command_v2(project: dict[str, Any], kind: str, self_index: int, node: dict[str, Any], varmap: dict[str, str]) -> list[str]:
    command = node["command"]
    args = node["args"]
    line_no = int(node["line"])

    def e(i: int) -> str:
        return _compile_expr(args[i], varmap, kind, self_index, line_no)
    def obj_target() -> int:
        return _resolve_object_ref(project, args[0], self_index if kind == "object" else None)
    def npc_target() -> int:
        return _resolve_npc_ref(project, args[0], self_index if kind == "npc" else None)

    out: list[str] = []
    if command in {"move", "set_pos", "show", "hide", "toggle", "spin", "set_rot"}:
        if kind == "object":
            base = f"scene_objects[{self_index}]"
            if command == "move": out += [f"{base}.x += {e(0)};", f"{base}.y += {e(1)};", f"{base}.z += {e(2)};"]
            elif command == "set_pos": out += [f"{base}.x = {e(0)};", f"{base}.y = {e(1)};", f"{base}.z = {e(2)};"]
            elif command == "spin": out += [f"{base}.rot_x = (uint8_t)({base}.rot_x + ({e(0)}));", f"{base}.rot_y = (uint8_t)({base}.rot_y + ({e(1)}));"]
            elif command == "set_rot": out += [f"{base}.rot_x = (uint8_t)(({e(0)}) & 63);", f"{base}.rot_y = (uint8_t)(({e(1)}) & 63);"]
            elif command == "show": out.append(f"{base}.visible = 1u;")
            elif command == "hide": out.append(f"{base}.visible = 0u;")
            elif command == "toggle": out.append(f"{base}.visible ^= 1u;")
        elif kind == "npc":
            if command in {"spin", "set_rot"}:
                raise ValueError(f"line {line_no}: sprites do not have 3D rotation; use movement or another sprite image")
            base = f"npcs[{self_index}]"
            if command == "move": out += [f"{base}.x += {e(0)};", f"{base}.y += {e(1)};", f"{base}.z += {e(2)};"]
            elif command == "set_pos": out += [f"{base}.x = {e(0)};", f"{base}.y = {e(1)};", f"{base}.z = {e(2)};"]
            elif command == "show": out.append(f"gb3d_npc_set_visible({self_index}u, 1u);")
            elif command == "hide": out.append(f"gb3d_npc_set_visible({self_index}u, 0u);")
            elif command == "toggle": out.append(f"gb3d_npc_toggle_visible({self_index}u);")
        else:
            raise ValueError(f"line {line_no}: {command} requires an object or NPC script")
    elif command.startswith("move_object") or command in {"set_object_pos", "spin_object", "set_object_rot", "show_object", "hide_object", "toggle_object"}:
        t = obj_target(); base = f"scene_objects[{t}]"
        if command == "move_object": out += [f"{base}.x += {e(1)};", f"{base}.y += {e(2)};", f"{base}.z += {e(3)};"]
        elif command == "set_object_pos": out += [f"{base}.x = {e(1)};", f"{base}.y = {e(2)};", f"{base}.z = {e(3)};"]
        elif command == "spin_object": out += [f"{base}.rot_x = (uint8_t)({base}.rot_x + ({e(1)}));", f"{base}.rot_y = (uint8_t)({base}.rot_y + ({e(2)}));"]
        elif command == "set_object_rot": out += [f"{base}.rot_x = (uint8_t)(({e(1)}) & 63);", f"{base}.rot_y = (uint8_t)(({e(2)}) & 63);"]
        elif command == "show_object": out.append(f"{base}.visible = 1u;")
        elif command == "hide_object": out.append(f"{base}.visible = 0u;")
        elif command == "toggle_object": out.append(f"{base}.visible ^= 1u;")
    elif command in {"npc_move", "npc_set", "npc_show", "npc_hide", "npc_toggle"}:
        t = npc_target(); base = f"npcs[{t}]"
        if command == "npc_move": out += [f"{base}.x += {e(1)};", f"{base}.y += {e(2)};", f"{base}.z += {e(3)};"]
        elif command == "npc_set": out += [f"{base}.x = {e(1)};", f"{base}.y = {e(2)};", f"{base}.z = {e(3)};"]
        elif command == "npc_show": out.append(f"gb3d_npc_set_visible({t}u, 1u);")
        elif command == "npc_hide": out.append(f"gb3d_npc_set_visible({t}u, 0u);")
        elif command == "npc_toggle": out.append(f"gb3d_npc_toggle_visible({t}u);")
    elif command == "camera_move": out.append(f"camera.x += {e(0)}; camera.y += {e(1)}; camera.z += {e(2)};")
    elif command == "camera_set": out.append(f"camera.x = {e(0)}; camera.y = {e(1)}; camera.z = {e(2)};")
    elif command == "camera_look": out += [f"camera.yaw = (uint8_t)(camera.yaw + ({e(0)}));", f"camera.pitch = gb3d_clamp_pitch((int16_t)camera.pitch + ({e(1)}));"]
    elif command == "camera_set_look": out += [f"camera.yaw = (uint8_t)(({e(0)}) & 63);", f"camera.pitch = gb3d_clamp_pitch({e(1)});"]
    elif command == "reset_camera": out += ["camera.x = SCENE_CAMERA_X; camera.y = SCENE_CAMERA_Y; camera.z = SCENE_CAMERA_Z;", "camera.yaw = SCENE_CAMERA_YAW; camera.pitch = SCENE_CAMERA_PITCH;"]
    elif command == "set_sky":
        try:
            c = _parse_sky_color(args[0])
            out.append(f"gb3d_set_sky_logical({c}u);")
        except ValueError:
            out.append(f"gb3d_set_sky_logical((uint8_t)({e(0)}));")
    elif command == "player_move": out.append(f"gb3d_player_move({e(0)}, {e(1)}, {e(2)});")
    elif command == "player_set": out.append(f"gb3d_player_set({e(0)}, {e(1)}, {e(2)});")
    elif command == "player_jump": out.append("gb3d_player_jump();")
    elif command == "player_show": out.append("gb3d_player_set_visible(1u);")
    elif command == "player_hide": out.append("gb3d_player_set_visible(0u);")
    elif command == "player_toggle": out.append("gb3d_player_toggle_visible();")
    elif command in {"ui_set_value", "ui_add_value", "ui_set_max", "ui_show", "ui_hide", "ui_toggle"}:
        t = _resolve_ui_ref(project, args[0])
        if command == "ui_set_value": out.append(f"gb3d_ui_set_value({t}u, (int16_t)({e(1)}));")
        elif command == "ui_add_value": out.append(f"gb3d_ui_add_value({t}u, (int16_t)({e(1)}));")
        elif command == "ui_set_max": out.append(f"gb3d_ui_set_max({t}u, (int16_t)gb3d_max16(1, {e(1)}));")
        elif command == "ui_show": out.append(f"gb3d_ui_set_element_visible({t}u, 1u);")
        elif command == "ui_hide": out.append(f"gb3d_ui_set_element_visible({t}u, 0u);")
        elif command == "ui_toggle": out.append(f"gb3d_ui_toggle_element({t}u);")
    elif command == "window_show": out.append("gb3d_window_set_visible(1u);")
    elif command == "window_hide": out.append("gb3d_window_set_visible(0u);")
    elif command == "window_toggle": out.append("gb3d_window_toggle_visible();")
    elif command == "window_move": out.append(f"gb3d_window_move((uint8_t)({e(0)}), (uint8_t)({e(1)}));")
    return out


def _compile_nodes(project: dict[str, Any], kind: str, index: int, nodes: list[dict[str, Any]], varmap: dict[str, str], indent: int, repeat_counter: list[int]) -> list[str]:
    lines: list[str] = []
    pad = "    " * indent
    for node in nodes:
        typ = node["type"]
        line = int(node.get("line", 0))
        if typ == "command":
            for stmt in _compile_script_command_v2(project, kind, index, node, varmap):
                lines.append(pad + stmt)
        elif typ == "assign":
            expr = _compile_expr(node["expr"], varmap, kind, index, line)
            lines.append(pad + f"{varmap[node['name']]} {node['op']} (int16_t)({expr});")
        elif typ == "if":
            expr = _compile_expr(node["expr"], varmap, kind, index, line)
            lines.append(pad + f"if ({expr}) {{")
            lines.extend(_compile_nodes(project, kind, index, node["then"], varmap, indent + 1, repeat_counter))
            if node["else"]:
                lines.append(pad + "} else {")
                lines.extend(_compile_nodes(project, kind, index, node["else"], varmap, indent + 1, repeat_counter))
            lines.append(pad + "}")
        elif typ == "while":
            expr = _compile_expr(node["expr"], varmap, kind, index, line)
            lines.append(pad + f"while ({expr}) {{")
            lines.extend(_compile_nodes(project, kind, index, node["body"], varmap, indent + 1, repeat_counter))
            lines.append(pad + "}")
        elif typ == "repeat":
            rid = repeat_counter[0]; repeat_counter[0] += 1
            expr = _compile_expr(node["expr"], varmap, kind, index, line)
            lines.append(pad + "{")
            lines.append(pad + f"    int16_t __gb_lim_{rid} = (int16_t)({expr});")
            lines.append(pad + f"    int16_t __gb_i_{rid};")
            lines.append(pad + f"    for (__gb_i_{rid} = 0; __gb_i_{rid} < __gb_lim_{rid}; __gb_i_{rid}++) {{")
            lines.extend(_compile_nodes(project, kind, index, node["body"], varmap, indent + 2, repeat_counter))
            lines.append(pad + "    }")
            lines.append(pad + "}")
        elif typ == "break": lines.append(pad + "break;")
        elif typ == "continue": lines.append(pad + "continue;")
    return lines


def _script_sources(project: dict[str, Any]):
    for i, obj in enumerate(project.get("objects", [])):
        yield "object", i, obj
    for i, npc in enumerate(project.get("npcs", [])):
        yield "npc", i, npc


def validate_script_source(project: dict[str, Any], kind: str, index: int, script: str) -> list[str]:
    program, errors = _parse_script_program(script)
    if errors:
        return errors
    prefix = _script_source_prefix(kind, index)
    varmap = {v["name"]: prefix + safe_c_name(v["name"]) for v in program["vars"]}
    result: list[str] = []
    try:
        for v in program["vars"]:
            _compile_expr(v["expr"], varmap, kind, index, int(v["line"]))
        for event_nodes in program["events"].values():
            _compile_nodes(project, kind, index, event_nodes, varmap, 1, [0])
    except ValueError as exc:
        result.append(str(exc))
    return result


def validate_object_script(project: dict[str, Any], object_index: int, script: str | None = None) -> list[str]:
    obj = project.get("objects", [])[object_index]
    return validate_script_source(project, "object", object_index, obj.get("script", "") if script is None else script)


def validate_npc_script(project: dict[str, Any], npc_index: int, script: str | None = None) -> list[str]:
    npc = project.get("npcs", [])[npc_index]
    return validate_script_source(project, "npc", npc_index, npc.get("script", "") if script is None else script)


def generate_script_c(project: dict[str, Any]) -> list[str]:
    parsed: list[tuple[str, int, dict[str, Any], dict[str, Any], dict[str, str]]] = []
    lines: list[str] = []
    for kind, index, source in _script_sources(project):
        program, errors = _parse_script_program(source.get("script", ""))
        if errors:
            continue
        prefix = _script_source_prefix(kind, index)
        varmap = {v["name"]: prefix + safe_c_name(v["name"]) for v in program["vars"]}
        for v in program["vars"]:
            lines.append(f"static int16_t {varmap[v['name']]} = 0;")
        parsed.append((kind, index, source, program, varmap))
    if lines:
        lines.append("")

    for event in SCRIPT_EVENTS:
        lines.append(f"static void scene_script_{event}(void) {{")
        any_code = False
        # Script variables are reset from their declared initializers once at scene start.
        if event == "start":
            for kind, index, _, program, varmap in parsed:
                for v in program["vars"]:
                    expr = _compile_expr(v["expr"], varmap, kind, index, int(v["line"]))
                    lines.append(f"    {varmap[v['name']]} = (int16_t)({expr});")
                    any_code = True
        for kind, index, _, program, varmap in parsed:
            nodes = program["events"][event]
            if nodes:
                body = _compile_nodes(project, kind, index, nodes, varmap, 1, [0])
                lines.extend(body)
                any_code = any_code or bool(body)
        if not any_code:
            lines.append("    /* empty */")
        lines.append("}")
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Preview helpers for NPCs + UI
# ---------------------------------------------------------------------------

_FONT3X5: dict[str, tuple[int, int, int, int, int]] = {
    "A": (2,5,7,5,5), "B": (6,5,6,5,6), "C": (3,4,4,4,3), "D": (6,5,5,5,6),
    "E": (7,4,6,4,7), "F": (7,4,6,4,4), "G": (3,4,5,5,3), "H": (5,5,7,5,5),
    "I": (7,2,2,2,7), "J": (1,1,1,5,2), "K": (5,5,6,5,5), "L": (4,4,4,4,7),
    "M": (5,7,7,5,5), "N": (5,7,7,7,5), "O": (2,5,5,5,2), "P": (6,5,6,4,4),
    "Q": (2,5,5,3,1), "R": (6,5,6,5,5), "S": (3,4,2,1,6), "T": (7,2,2,2,2),
    "U": (5,5,5,5,7), "V": (5,5,5,5,2), "W": (5,5,7,7,5), "X": (5,5,2,5,5),
    "Y": (5,5,2,2,2), "Z": (7,1,2,4,7),
    "0": (2,5,5,5,2), "1": (2,6,2,2,7), "2": (6,1,2,4,7), "3": (6,1,2,1,6),
    "4": (5,5,7,1,1), "5": (7,4,6,1,6), "6": (3,4,6,5,2), "7": (7,1,2,2,2),
    "8": (2,5,2,5,2), "9": (2,5,3,1,6),
    "-": (0,0,7,0,0), ":": (0,2,0,2,0), ".": (0,0,0,0,2), "!": (2,2,2,0,2),
    "/": (1,1,2,4,4), "?": (6,1,2,0,2), " ": (0,0,0,0,0),
}


def _preview_ui_pixel(buf: list[list[int]], x: int, y: int, logical_color: int) -> None:
    if 0 <= x < FB_WIDTH and 0 <= y < FB_HEIGHT:
        pal, shade = logical_material(logical_color)
        buf[y][x] = (pal << 2) | shade


def _preview_ui_text(buf: list[list[int]], x: int, y: int, text: str, color: int) -> None:
    cx = x
    for ch in str(text).upper():
        rows = _FONT3X5.get(ch, _FONT3X5["?"])
        for yy, bits in enumerate(rows):
            for xx in range(3):
                if bits & (1 << (2 - xx)):
                    _preview_ui_pixel(buf, cx + xx, y + yy, color)
        cx += 4
        if cx >= FB_WIDTH:
            break


def _preview_ui(buf: list[list[int]], project: dict[str, Any]) -> None:
    for item in project.get("ui", []):
        if not item.get("visible", True):
            continue
        typ = item.get("type", "text")
        x = int(item.get("x", 0)); y = int(item.get("y", 0)); color = int(item.get("color", 1)) & 15
        if typ == "text":
            text = str(item.get("text", ""))
            if item.get("show_value", False):
                text += (" " if text else "") + str(int(item.get("value", 0)))
            _preview_ui_text(buf, x, y, text, color)
        else:
            width = max(1, min(FB_WIDTH, int(item.get("width", 10))))
            maxv = max(1, int(item.get("max", 10)))
            value = max(0, min(maxv, int(item.get("value", 0))))
            filled = (value * width) // maxv
            bg = int(item.get("bg_color", 3)) & 15
            for xx in range(width):
                _preview_ui_pixel(buf, x + xx, y, color if xx < filled else bg)


def render_scene(project: dict[str, Any], wireframe: bool = False) -> tuple[list[list[int]], dict[str, int]]:
    # V9 UI lives on the Game Boy Window layer and no longer touches the 3D
    # framebuffer. The Tk editor draws a separate Window preview on top.
    buf, stats = _v6_render_scene(project, wireframe=wireframe)
    stats["npcs"] = len(project.get("npcs", []))
    stats["ui"] = len(project.get("ui", []))
    return buf, stats


def preview_npcs(project: dict[str, Any]) -> list[tuple[dict[str, Any], int, int]]:
    cam = effective_camera(project)
    result = []
    for npc in project.get("npcs", []):
        if not npc.get("visible", True):
            continue
        pos = (npc.get("position") or [0,0,0])[:3]
        x, y, z = map(int, pos)
        h = int(npc.get("height", 12))
        pv = project_vertex(world_to_camera((x, y + h // 2, z), cam))
        if pv is not None:
            result.append((npc, int(pv[0]), int(pv[1])))
    return result


# ---------------------------------------------------------------------------
# Export validation + header extension
# ---------------------------------------------------------------------------

def _fixed_camera_fast_conflicts(project: dict[str, Any]) -> list[str]:
    """Return script features that would invalidate locked camera orientation.

    Studio 1.2's compiler-first renderer can now mix PC-baked static objects
    with runtime-transform dynamic objects, so object movement/rotation no
    longer disables Fixed Camera Fast Path. Only camera-orientation changes
    conflict with the locked orientation itself.
    """
    if not bool(project.get("settings", {}).get("fixed_camera_fast", False)):
        return []
    conflicts: list[str] = []
    banned_commands = {"camera_look", "camera_set_look", "reset_camera"}
    for kind, index, source in _script_sources(project):
        for raw in str(source.get("script", "") or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("@") or line.startswith("#"):
                continue
            cmd = line.split(None, 1)[0].lower()
            if cmd in banned_commands:
                label = (project.get("objects", [])[index].get("name", f"Object {index}")
                         if kind == "object" else project.get("npcs", [])[index].get("name", f"NPC {index}"))
                conflicts.append(f"{label}: '{cmd}' changes the camera orientation locked by Fixed Camera Fast Path")
    return conflicts


def validate_for_export(project: dict[str, Any]) -> list[str]:
    errors = _v6_validate_for_export(project)
    npcs = project.get("npcs", [])
    ui = project.get("ui", [])
    player = project.get("player", {})
    if len(npcs) > MAX_NPCS:
        errors.append(f"Scene has {len(npcs)} NPCs; current limit is {MAX_NPCS}.")
    if len(ui) > MAX_UI_ELEMENTS:
        errors.append(f"Scene has {len(ui)} UI elements; current limit is {MAX_UI_ELEMENTS}.")

    sprite_mode = str(player.get("sprite_mode", "8x16"))
    per_character = 8 if sprite_mode == "8x16" else 16
    total_oam = (per_character if player.get("enabled", False) else 0) + len(npcs) * per_character
    if total_oam > 40:
        errors.append(f"Player + NPCs need {total_oam} hardware sprites; GBC OAM limit is 40. Use 8x16 mode or fewer NPCs.")
    sprite_palettes = (1 if player.get("enabled", False) else 0) + len(npcs)
    if sprite_palettes > 8:
        errors.append(f"Player + NPCs need {sprite_palettes} sprite palettes; CGB limit is 8.")

    for ni, npc in enumerate(npcs):
        _normalize_npc(npc)
        pos = npc.get("position", [])
        if len(pos) < 3:
            errors.append(f"NPC {npc.get('name','NPC')}: position needs X/Y/Z.")
        if not 1 <= int(npc.get("height", 12)) <= 64:
            errors.append(f"NPC {npc.get('name','NPC')}: height must be 1..64.")
        if len(npc.get("sprite_pixels", [])) != 1024:
            errors.append(f"NPC {npc.get('name','NPC')}: sprite must be 32x32.")
        for err in validate_npc_script(project, ni):
            errors.append(f"NPC {npc.get('name','NPC')} script: {err}")
    for item in ui:
        _normalize_ui(item)
        if item["type"] == "text" and len(item.get("text", "")) > 18:
            errors.append(f"UI {item.get('name','UI')}: text is limited to 18 characters.")
    _normalize_window_ui(project.setdefault("settings", {}))
    errors.extend(_fixed_camera_fast_conflicts(project))
    return errors


def _c_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=True)


def _npc_ui_header_block(project: dict[str, Any]) -> str:
    lines: list[str] = []
    npcs = project.get("npcs", [])
    player = project.get("player", {})
    ui = project.get("ui", [])
    sprite_8x16 = str(player.get("sprite_mode", "8x16")) == "8x16"
    npc_oam = 8 if sprite_8x16 else 16
    lines += [
        "// ---- V7 NPC hardware-sprite data ----",
        f"#define SCENE_NPC_COUNT {len(npcs)}u",
        f"#define SCENE_SPRITE_8X16 {1 if sprite_8x16 else 0}u",
        f"#define SCENE_NPC_SPRITE_OAM_COUNT {npc_oam}u",
        "#define SCENE_NPC_SPRITE_TILE_COUNT 16u",
    ]
    arrn = max(1, len(npcs))
    def arr(name: str, ctype: str, values: list[str]):
        lines.append(f"static const {ctype} {name}[{arrn}] = {{")
        if values:
            lines.extend("    " + v + "," for v in values)
        else:
            lines.append("    0,")
        lines.append("};")
    arr("scene_npc_start_x", "int16_t", [str(int((n.get('position') or [0,0,0])[0])) for n in npcs])
    arr("scene_npc_start_y", "int16_t", [str(int((n.get('position') or [0,0,0])[1])) for n in npcs])
    arr("scene_npc_start_z", "int16_t", [str(int((n.get('position') or [0,0,0])[2])) for n in npcs])
    arr("scene_npc_height", "uint8_t", [str(max(1,min(64,int(n.get('height',12)))))+'u' for n in npcs])
    arr("scene_npc_visible", "uint8_t", [('1u' if n.get('visible',True) else '0u') for n in npcs])
    lines.append(f"static const uint8_t scene_npc_sprite_tiles[{arrn}][256] = {{")
    if npcs:
        for npc in npcs:
            data = player_sprite_tile_bytes({**npc, "sprite_mode": "8x16" if sprite_8x16 else "8x8"})
            lines.append("    {")
            for i in range(0, 256, 16):
                lines.append("        " + ", ".join(str(v) for v in data[i:i+16]) + ",")
            lines.append("    },")
    else:
        lines.append("    {0},")
    lines.append("};")
    lines.append(f"static const palette_color_t scene_npc_sprite_palettes[{arrn}][4] = {{")
    if npcs:
        for npc in npcs:
            pal = npc.get("sprite_palette", default_player_sprite_palette(10))
            entries=[]
            for rgb in pal:
                rr,gg,bb=[max(0,min(255,int(c))) for c in list(rgb)[:3]]
                entries.append(f"RGB({round(rr*31/255)}, {round(gg*31/255)}, {round(bb*31/255)})")
            lines.append("    {" + ", ".join(entries) + "},")
    else:
        lines.append("    {RGB(0,0,0), RGB(0,0,0), RGB(0,0,0), RGB(0,0,0)},")
    lines.append("};")
    lines.append("")

    win = project.get("settings", {}).get("window_ui", {})
    _normalize_window_ui(project.setdefault("settings", {}))
    win = project.get("settings", {}).get("window_ui", {})
    lines += [
        "// ---- Studio 1.2 hardware Window HUD / UI ----",
        f"#define SCENE_UI_COUNT {len(ui)}u",
        f"#define SCENE_WINDOW_ENABLED {1 if win.get('enabled', False) else 0}u",
        f"#define SCENE_WINDOW_X {max(0,min(159,int(win.get('x',0))))}u",
        f"#define SCENE_WINDOW_Y {max(0,min(143,int(win.get('y',112))))}u",
        f"#define SCENE_WINDOW_BG_COLOR {int(win.get('background',0)) & 15}u",
    ]
    for idx, item in enumerate(ui):
        if item.get("type", "text") == "text":
            lines.append(f"static const char scene_ui_text_{idx}[] = {_c_string(item.get('text',''))};")
    lines.append("static GB3DUIElement scene_ui[(SCENE_UI_COUNT > 0u) ? SCENE_UI_COUNT : 1u] = {")
    if ui:
        for idx,item in enumerate(ui):
            typ = item.get("type","text")
            textptr = f"scene_ui_text_{idx}" if typ == "text" else "0"
            type_id = 0 if typ == "text" else 1
            lines.append(
                "    {" + ", ".join([
                    f"{type_id}u", f"{int(item.get('x',1))}u", f"{int(item.get('y',1))}u",
                    f"{int(item.get('width',0)) if typ=='bar' else 0}u", f"{int(item.get('color',1)) & 15}u",
                    f"{int(item.get('bg_color',3)) & 15}u", f"{1 if item.get('visible',True) else 0}u",
                    f"{1 if item.get('show_value',False) else 0}u", str(int(item.get('value',0))),
                    str(max(1,int(item.get('max',10)))), textptr
                ]) + "},"
            )
    else:
        lines.append("    {0u,0u,0u,0u,1u,0u,0u,0u,0,1,0},")
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def generate_scene_header(project: dict[str, Any]) -> str:
    # The V6 generator still owns meshes, scene objects, player art, and the
    # optimized rotation baking. Its global calls resolve to V7 script helpers.
    base = _v6_generate_scene_header(project)
    base = base.replace("Generated by GB3D Studio 1.0", "Generated by GB3D Studio 1.2", 1)
    marker = "// Export-time compiled GB3D scripts. No parser/VM runs on the Game Boy."
    pos = base.find(marker)
    if pos < 0:
        return base
    return base[:pos] + _npc_ui_header_block(project) + "\n" + base[pos:]


# ============================================================
# GB3D Studio 1.2 editor renderer
# ============================================================
# This renderer is PC/editor-only.  It intentionally does NOT emulate the
# 40x36 packed-tile GBC rasterizer; it provides a comfortable full-resolution
# scene view while render_scene() remains the hardware-accurate preview.

def _editor_rotate_vertex(v, angle_x: int, angle_y: int) -> tuple[float, float, float]:
    x, y, z = (float(v[0]), float(v[1]), float(v[2]))
    ax = (int(angle_x) & 63) * (math.tau / 64.0)
    ay = (int(angle_y) & 63) * (math.tau / 64.0)
    sx, cx = math.sin(ax), math.cos(ax)
    sy, cy = math.sin(ay), math.cos(ay)
    tx = x * cy - z * sy
    tz = x * sy + z * cy
    x, z = tx, tz
    ty = y * cx - z * sx
    tz = y * sx + z * cx
    return x, ty, tz


def _editor_world_to_camera(v, cam: dict[str, int | float]) -> tuple[float, float, float]:
    x = float(v[0]) - float(cam.get('x', 0))
    y = float(v[1]) - float(cam.get('y', 0))
    z = float(v[2]) - float(cam.get('z', -64))
    ay = (int(cam.get('yaw', 0)) & 63) * (math.tau / 64.0)
    ap = (int(cam.get('pitch', 0)) & 63) * (math.tau / 64.0)
    sy, cy = math.sin(ay), math.cos(ay)
    sp, cp = math.sin(ap), math.cos(ap)
    tx = x * cy - z * sy
    tz = x * sy + z * cy
    x, z = tx, tz
    ty = y * cp - z * sp
    tz = y * sp + z * cp
    return x, ty, tz


def editor_project_camera(v, width: int, height: int) -> tuple[float, float] | None:
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    if z < NEAR_Z or z > FAR_Z:
        return None
    scale = min(max(1.0, float(width)) / FB_WIDTH, max(1.0, float(height)) / FB_HEIGHT)
    focal = FOCAL_LEN * scale
    return (float(width) * 0.5 + (x * focal / z),
            float(height) * 0.5 - (y * focal / z))


def editor_project_world(point, width: int, height: int, camera: dict[str, int | float]) -> tuple[float, float] | None:
    return editor_project_camera(_editor_world_to_camera(point, camera), width, height)


def _editor_shade_rgb(base: tuple[int, int, int], brightness: float) -> tuple[int, int, int]:
    brightness = max(0.25, min(1.18, float(brightness)))
    return tuple(max(0, min(255, int(round(c * brightness)))) for c in base)


def editor_scene_primitives(project: dict[str, Any], width: int, height: int,
                            camera: dict[str, int | float] | None = None,
                            wireframe: bool = False) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Return full-resolution PC viewport primitives.

    The Game Boy renderer deliberately uses chunky 40x36 logical pixels and a
    painter-style tile buffer.  This editor renderer instead projects with
    floating point and globally sorts all visible triangles.  The small GBC
    preview should be used to judge actual hardware output.
    """
    width = max(64, int(width)); height = max(64, int(height))
    cam = dict(camera or effective_camera(project))
    primitives: list[dict[str, Any]] = []
    transformed = tested = visible_count = 0

    for obj in project.get('objects', []):
        if not obj.get('visible', True):
            continue
        model = model_by_id(project, obj.get('model_id', ''))
        if not model:
            continue
        rx, ry = (obj.get('rotation') or [0, 0])[:2]
        px, py, pz = (obj.get('position') or [0, 0, 0])[:3]
        camera_vertices: list[tuple[float, float, float]] = []
        projected: list[tuple[float, float] | None] = []
        for v in model.get('vertices', []):
            x, y, z = _editor_rotate_vertex(v, int(rx), int(ry))
            cv = _editor_world_to_camera((x + int(px), y + int(py), z + int(pz)), cam)
            camera_vertices.append(cv)
            projected.append(editor_project_camera(cv, width, height))
            transformed += 1

        if wireframe:
            for a, b in model.get('edges', []):
                if a >= len(projected) or b >= len(projected):
                    continue
                pa, pb = projected[a], projected[b]
                if pa is None or pb is None:
                    continue
                depth = (camera_vertices[a][2] + camera_vertices[b][2]) * 0.5
                primitives.append({
                    'kind': 'line', 'points': (pa, pb), 'depth': depth,
                    'object_id': obj.get('id', ''), 'color': (220, 225, 235),
                })
                visible_count += 1
            continue

        for fi, face in enumerate(model.get('faces', [])):
            tested += 1
            if len(face) < 3:
                continue
            a, b, c = map(int, face[:3])
            if max(a, b, c) >= len(projected):
                continue
            pa, pb, pc = projected[a], projected[b], projected[c]
            if pa is None or pb is None or pc is None:
                continue
            cross2 = (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])
            if cross2 <= 0.0:
                continue
            if (max(pa[0], pb[0], pc[0]) < 0 or min(pa[0], pb[0], pc[0]) >= width or
                    max(pa[1], pb[1], pc[1]) < 0 or min(pa[1], pb[1], pc[1]) >= height):
                continue

            va, vb, vc = camera_vertices[a], camera_vertices[b], camera_vertices[c]
            ab = (vb[0]-va[0], vb[1]-va[1], vb[2]-va[2])
            ac = (vc[0]-va[0], vc[1]-va[1], vc[2]-va[2])
            nx = ab[1]*ac[2] - ab[2]*ac[1]
            ny = ab[2]*ac[0] - ab[0]*ac[2]
            nz = ab[0]*ac[1] - ab[1]*ac[0]
            nlen = math.sqrt(nx*nx + ny*ny + nz*nz) or 1.0
            # Gentle editor lighting: readable shape, not an attempt to emulate
            # the GBC's cross-area shade choice.
            light = 0.70 + 0.22 * abs(ny) / nlen + 0.12 * abs(nz) / nlen
            logical = int(face[3] if len(face) > 3 else model.get('default_color', 1)) & 15
            fill = _editor_shade_rgb(LOGICAL_RGB[logical], light)
            depth = (va[2] + vb[2] + vc[2]) / 3.0
            primitives.append({
                'kind': 'triangle', 'points': (pa, pb, pc), 'depth': depth,
                'object_id': obj.get('id', ''), 'face_index': fi,
                'logical_color': logical, 'color': fill,
            })
            visible_count += 1

    primitives.sort(key=lambda p: float(p.get('depth', 0.0)), reverse=True)
    return primitives, {
        'vertices': transformed,
        'faces': tested,
        'drawn': visible_count,
        'objects': sum(1 for o in project.get('objects', []) if o.get('visible', True)),
    }
