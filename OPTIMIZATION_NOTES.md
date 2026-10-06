# GB3D 1.0.1 optimization notes

The polygon renderer is intentionally almost unchanged from the fast direct/ASM architecture that reached full frame rate in small scenes.

NPCs do **not** add polygons. They are projected world points displayed as 32×32 GBC hardware metasprites. Their main cost is OAM updates and sprite scanline limits.

GBScript 2 is compiled to native C on the PC. Variables and control flow therefore add only the operations explicitly written by the script; there is no runtime tokenizer, bytecode VM, or interpreter.

HUD text/bars write directly into the final 20×18 packed tilemap after polygon rendering. There is no second framebuffer or palette search pass.

Things to keep cheap on real hardware:

- Avoid large `while`/`repeat` loops in `@update`.
- Multiplication/division inside scripts is legal but comparatively expensive on SM83.
- Prefer 8×16 character sprite mode: a 32×32 character then uses 8 OAM entries instead of 16.
- The hardware limit is 40 sprites total and 10 sprites on a scanline.
- Four 32×32 characters aligned on the same horizontal scanline can exceed the 10-sprite scanline limit even though total OAM fits; hardware may drop some chunks.
- HUD text is deliberately tiny (3×5 logical pixels) to keep writes low.

## 1.0.1 Window UI fast path

Game HUD rendering was removed from the packed 40×36 3D framebuffer. The hardware Window layer now owns UI.

Benefits:

- static text performs no per-frame pixel rasterization
- changing one UI value only dirties the Window map
- Window tilemap/attributes upload only when UI state changes
- 3D framebuffer clear/raster cost is independent of HUD complexity
- Window graphics live in VRAM bank 1 and do not consume bank-0 3D pattern tiles

The optional profiler overlay intentionally remains a tiny framebuffer overlay because it is debug-only and measures the game UI path separately.


## Studio 1.0 editor rendering

The new large PC viewport is intentionally not a performance model of the GBC. It uses floating-point projection and global triangle sorting for comfortable editing. Use the corner GBC preview and the exported ROM profiler for runtime decisions.
