# NovaPoly Studio v0.6.3 — Inspector visibility + comparable GBA benchmarks

This is a diagnostic/corrective release based on **v0.6.2**, not a claimed performance improvement. No renderer quality is reduced by default.

## Editor visibility bug fixed

The Object and Scene inspector tabs now scroll vertically, including with the mouse wheel. The Studio window also sizes to the desktop more conservatively. On small displays, scroll to reach the lower Scene options.

For permanent access, use the **Game** menu:

- **Profiler build (slower; for diagnostics)**: toggle *off* when benchmarking game FPS. The on-screen profiler being hidden in the ROM is not the same thing as compiling without profiling.
- **GBA benchmark: 512-triangle queue (v0.5 limit)**: toggle on ONLY to compare performance with v0.5. When off (default), the GBA exporter retains 1,024 triangle slots.

Your selection is stored in the project's `.gb3d` settings (`profile_build` and `gba_queue_512`). The original GBC exporter remains unchanged.

## Comparing the triangle queues

By default v0.6.3 retains v0.6.2's configuration: first 512 queued triangles in GBA IWRAM, extra 512 in EWRAM. The 512-triangle comparison toggle exports a GBA source with just the 512 fast-RAM triangle records, **no EWRAM queue**, and a simpler direct lookup.

**Important:** This comparison mode may silently omit visible faces if more than 512 are submitted. It is a benchmark, *not* an unconditional optimization or a promise of identical appearance. A 700-polygon model does not necessarily submit all 700 faces; backface and view culling happen first. Use the normal 1,024 mode for correctness.

If the 512 version runs faster, possibilities include EWRAM accesses *and* drawing fewer triangles. Compare appearance and the profiler's queued/dropped triangle counts when available. Do not attribute the entire speed gap to EWRAM without controlled tests.

## Suggested Crash test

1. Open the **same** Crash `.gb3d` project in v0.6.3.
2. Under **Game**, uncheck **Profiler build**.
3. Leave **512-triangle queue** unchecked; export, `make clean && make`, record front-view FPS.
4. Check **512-triangle queue**; export to a **different folder**, rebuild, compare front-view FPS and model appearance.
5. For instrumented measurements, enable profiler as a separate diagnostic build, but compare ordinary FPS only with profiling disabled.

Only change one variable at a time. The v0.5 reference ARM texture sampling routine remains the default in both configurations.

## Validation

- New project + original GBC Studio self-test passed.
- ARM C and ARM assembly object compilation passed for all combinations of 512/1024 queue and Profile/Release.
- At a 1000x550 Tk window, the Scene panel scrolls and Game menu toggles work.
- Readelf checked 512 mode has only the 15,360-byte IWRAM triangle queue and **no** cold/EWRAM queue; 1024 mode adds a 15,360-byte `.sbss` queue.

A complete devkitARM cartridge link and mGBA benchmark weren't available here. Your measured FPS is the deciding test.
