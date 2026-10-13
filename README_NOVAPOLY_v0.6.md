# NovaPoly Studio v0.6 — measured GBA texture-renderer speed pass

This release is a continuation of NovaPoly v0.5. The original `.gb3d` scene format,
GBC exporter, textured Studio viewport, Mode 5 framebuffer, NPC scaling, and gameplay remain intact.

## Performance changes

- `gba_runtime/gb3d_arm.s`: optimized ARM7TDMI texture span kernel packs two 16-bit RGB555 palette results into one **aligned 32-bit VRAM write**. Handles odd starting addresses and trailing pixels with halfword stores. Previous pixel-at-a-time version is kept as `novapoly_tex_span_reference_arm` for comparison.
- `gba_runtime/main.c`: scanlines use **incremental vertical UV origins** instead of two UV multiplications for every row. Same affine UVs, 8.8 fixed point, texture wrapping, RGB555 shading, image palette, and rendering resolution.
- Shaded palettes were **already precomputed** in previous versions; textured ARM code was **already placed in IWRAM**. Both are preserved, not newly added.
- Triangle queue grows from 512 to 1024 slots. **v0.6.1 linker fix:** its ~30 KiB array now lives in the GBA 256 KiB EWRAM (`.sbss`), not IWRAM (`.bss`). The assembly hot loops remain in IWRAM. The profiler's `DROP` counter will show if more visible faces are submitted than can be queued. A larger queue can increase work in large scenes; it does not imply faster rendering.

## Test first: enable profiler

In NovaPoly Studio's Scene inspector select **Profiler build** (important: a normal Release export does not include counters). Export GBA and build with `make clean && make`.

In the ROM press **START+SELECT** together to toggle stats. Use **L/R** to change the four pages:

- **Page 0:** engine FPS (not mGBA emulator speed), visible triangles, queued triangles and dropped triangles.
- **Page 1:** CPU total, geometry transforms/queue work (`GEO`), textured triangle rasterization (`TEX`) and flat triangle rasterization (`FLAT`). Timings are expressed in roughly **quarter-milliseconds** (1 unit = 64 Timer1 ticks = 0.244 ms). CPU total excludes VBlank waiting and the profiler overlay itself. `TEX` includes textured triangle setup as well as spans, not strictly texel fetches.
- **Page 2:** total textured and flat pixel writes, number of textured triangles, and `OVER10` (total triangle pixel writes divided by framebuffer area, multiplied by 10). `OVER10` measures fill pressure, **not exact invisible overdraw**.
- **Page 3:** framebuffer clear (`CLEAR`), billboard drawing (`NPC`, includes player), visible objects and baked static flag.

The instrumentation adds overhead. Compare **Profile to Profile** or **Release to Release**, never compare a profiled FPS with a release FPS as if they were identical.

## A/B compare ARM pixel writes

Open the exported `source/main.c` and change:

```c
#define NOVAPOLY_FAST_TEX 1
```

to `0` to use the original v0.5 one-pixel texture span routine. Rebuild with `make clean && make` and test the **same camera angle and scene**. UV row stepping remains optimized in both modes so this isolates the ARM span changes.

For a fair **complete v0.5 vs v0.6** comparison, also test the unmodified old v0.5 ROM at the same camera angles. Track worst-case render FPS, `TEX`, `GEO` and `T PIX` on Crash from front and back. The goal of 15 FPS is only a target; there is no measured claim of an improvement here.

## Known constraints

- GBA Mode 5 160×128 scaled evenly to a centered 200×160, exact same pixel count and palette conversion as v0.5.
- 16 painter's buckets, affine texture mapping; no Z-buffer or perspective correction.
- Fragment overdraw is not eliminated in v0.6. Use pixel/write counters to decide whether future early occlusion is worthwhile.
- Scene geometry close to camera near plane is rejected, as before.
- The profiler is a CPU-time approximation. Timer1 is 16-bit, with ~250ms wrap at /64. Normal frames under 250ms are measured; very slow scenes should not use timing totals without accounting for rollover.
- Cannot verify a full `.gba` executable/mGBA result without devkitARM in this environment. ARM object compilation and source regression tests are performed separately.
