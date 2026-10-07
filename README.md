# GB3D Studio 1.2 - Correctness + Speed Pass

GB3D Studio is a lightweight 3D scene editor and GBDK exporter for Game Boy Color. 1.2 is the follow-up to the experimental 1.1 speed build: it fixes the broken tile-pair rasterizer, moves the profiler to the hardware Window layer, and attacks the remaining CPU-heavy parts of the runtime.

## 1.2 highlights

- Correct single-row packed-tile SM83 triangle writer, based on the known-good pre-1.1 output path.
- Quotient/remainder DDA edges: no variable `while(error >= dy)` loop on every scanline.
- Triangle setup hot path in SM83: stable Y sort, material unpacking, row counts, and exact edge divmod setup all run in assembly; no per-triangle reciprocal/multiply helper chain.
- Exact quarter-square 16x8 fast multiply built from two 8x8 partial products; no overflowing giant lookup table.
- Projection, camera/object rotation, movement math, and projected backface tests use the fast multiply path.
- One global 16-bucket triangle queue across visible objects.
- Static sector rejection before vertex transforms.
- Collision scans only exported Solid objects.
- Player/NPC position updates write directly to GBDK shadow OAM instead of calling `move_sprite()` for every component.
- Native 32x18 RAM maps + CGB General-Purpose DMA retained from 1.1.
- Fixed Camera Fast Path retained for games with locked camera yaw/pitch.
- **Compiler-first static geometry:** immutable object rotation + translation are baked into 16-bit world vertices on the PC; fixed-camera exports can bake the locked camera rotation too.
- Projection reciprocal/scale table is ROM data generated ahead of time instead of a 256-byte WRAM table built with startup divisions.
- Triangle bucket reset + far-to-near linked-list flush now run in SM83 assembly; queued triangle records feed the rasterizer directly without the old C copy loop.
- Cartridge data is stripped harder: editor-only edge lists are not exported, black/invisible faces are removed entirely, and the model vertex loop projects directly from live transform temporaries instead of round-tripping through a C helper.
- Fixed Camera Fast Path can now coexist with scripted moving/rotating objects: only immutable objects are baked, while dynamic objects automatically use the general fallback.
- Profiler now uses a readable 5x7 font on the hardware Window; it never draws into the 3D framebuffer.
- Release/Profile build switch. In Release builds profiler code is preprocessor-stripped entirely.

## Profiler

Enable **Profiler build (START+SELECT)** in the Scene inspector before exporting. In the ROM hold START + SELECT to toggle the bottom Window overlay:

```text
Fxxxx  Rxxxx
Uxxxx  Txxxx
Sxxxx  Pxxxx
```

`F` = CPU work, `R` = 3D scene, `U` = VRAM upload, `T` = submitted triangles, `S` = scripts, `P` = player physics/camera. While the profiler is visible, Left/Right switches among four pages; page 3 exposes `V/B/D/Q` renderer timing.

Leave Profiler build OFF for normal/release exports.

## Build

Export from Studio, then run `compile.bat` with GBDK's `bin` directory in PATH, or use the generated Makefile with `GBDK_HOME` set.

## Important

The Python/editor/export path and generated C are tested in this package, but the distribution environment used to build Studio 1.2 does not contain GBDK's real SM83 assembler/linker. Compile the exported project with your installed GBDK before treating a ROM as validated.

See `GB3D_GUIDE.md` / `GB3D_GUIDE.pdf` for the full workflow and `RELEASE_NOTES.md` for the renderer changes.
