# GB3D Studio 1.2 Optimization Notes

The 1.2 rule is: optimize measured hot work without changing the visible 40x36 coverage format.

## Per-frame pipeline

1. Scripts and physics.
2. Camera transform cache.
3. Clear visible 20x18 tile columns in the native 32x18 RAM maps.
4. Reject distant static sectors and whole invisible objects.
5. Transform/project vertices with table-based 16x8 multiplication.
6. Reject trivially off-screen/back-facing faces.
7. Queue visible triangles into global depth buckets.
8. Rasterize with exact quotient/remainder DDA and packed tile spans.
9. Update shadow OAM.
10. Wait for VBlank and GDMA tile IDs/attributes to VRAM.
11. Upload Window UI only when dirty; profiler Window separately when enabled.

## Why DDA changed

The old edge loop could increment X many times on a single scanline. 1.2 precomputes quotient/remainder once per edge. Runtime edge stepping has fixed work and at most one correction.

## Why the quarter-square table is only 383 entries

An unsigned 8x8 product can be computed as `Q(a+b)-Q(|a-b|)` where `Q(n)=floor(n*n/4)`. The largest index is 382 and every Q value fits in 16 bits. A signed 16x8 product is two such byte products. This avoids both SDCC multiply helpers and the overflowing large table from the first 1.2 draft.

## Profiling

Profile builds are intentionally separate from Release builds. Do not compare release FPS with profiler instrumentation compiled in. Enable it only when collecting F/R/U/T/S/P values.

## 1.2 raster hot-path pass (post-profiler)

The expanded profiler showed triangle draw/raster work as the largest single
renderer cost, so the exact DDA path received another pass without changing
coverage:

- Edge quotient `q` is pre-signed in C, removing the per-scanline sign branch.
- DDA remainder errors are now 8-bit. The SM83 carry flag from `err + r` is the
  ninth bit, so the correction remains exact even when the sum exceeds 255.
- Long + short edges are stepped together, cutting one `CALL`/`RET` pair per
  rasterized row.
- Span clipping falls directly into the packed span writer, cutting another
  `CALL`/`RET` pair per row.
- Packed-row address `(y >> 1) * 32` is computed directly instead of loading a
  16-bit row-offset table.
- The bottom-half loop does not advance either edge after the final `y2` row.
- Top/bottom row loops test their counters only where needed instead of at the
  top of every iteration.

This deliberately keeps the known-correct one-row-at-a-time rasterizer. It does
**not** bring back the broken two-row/tile-pair coverage experiment.

## Four-page profiler restored

The first raster-hotpath package accidentally kept the older single-page
profiler from its source ZIP. The expanded profiler is now restored without
changing the optimized rasterizer.

- Page 1: `F/R`, `U/T`, `S/P` — whole-frame overview.
- Page 2: `I/C`, `W/G`, `X/F` — input, camera/clear setup, Window work,
  sprite/OAM work, unclassified pre-VBlank CPU remainder, total CPU.
- Page 3: `V/B`, `D/Q`, `N/T` — vertex transform/project, face tests + bucket
  insertion, bucket flush/rasterizer, other scene overhead, off-screen faces,
  triangles submitted.
- Page 4: `M/E`, `T/D`, `O/C` — mesh vertices, faces examined, triangles,
  queue drops, visible objects, culled objects.

Use Left/Right to change pages while the profiler is visible. Those buttons are
consumed by the profiler in that mode so a page change does not alter the scene
being measured.


## Triangle setup hot path

Profiler page 3 showed `D` still dominating after scanline-loop cleanup, while
the span-writer bulk-fill experiment produced essentially no measurable gain.
The next pass therefore targets fixed per-triangle overhead rather than covered
pixels. Queue coordinates are already compact projected int8 values, so the
flush now goes directly from the 7-byte queue record to SM83 setup. The old
16-bit draw wrapper, duplicate culling/clamping, reciprocal edge helper calls,
and C-side sort/material/row setup are removed from the hot path.


## Compiler Does Everything pass

The exporter now classifies scene objects by whether scripts can change their position or rotation. Immutable transforms are resolved on the PC and emitted as `WorldVertex` arrays. This intentionally trades ROM for frame time: the GBC reads already transformed coordinates instead of redoing object transforms forever. If the camera orientation is locked, the compiler folds that rotation in too.

Other work moved off-device in this pass:

- the projection-scale reciprocal table is precomputed into ROM;
- static object transform classification happens during export;
- fixed-camera baking is per-object, so dynamic objects can coexist with the fast path;
- queue record layout is fixed/verified at export-runtime compile time, enabling an index-times-8 assembly walk.

### C -> assembly candidates after this pass

Profiler page 3 gives a useful priority order. The next assembly candidates are:

1. **Batch vertex transform + projection (`V`)** — highest-value target when Fixed Camera Fast Path is off. One assembly call per model can avoid repeated C function calls, 16-bit temporaries, and structure traffic.
2. **Face reject/backface/depth-bucket loop (`B`)** — projected cross product, trivial off-screen tests, max-Z selection and bucket insertion can potentially become one tight assembly walk over 4-byte faces.
3. **Camera transform preparation** — only once per frame, so lower priority than V/B despite being multiply-heavy.
4. **Collision AABB loops** — worthwhile only if profiler physics `P` grows in collision-heavy games.

Window/UI code and script orchestration are deliberately poor assembly targets right now: their profiler costs are tiny compared with V/B/D.

### Export-time dead-data stripping

Editor edge lists are useful for selection/wireframe previews but the cartridge renderer never reads them, so they are omitted from generated scene data. Permanently black/invisible faces are removed from runtime face arrays as well; this saves ROM and prevents the GBC from testing a face that can never submit a triangle.

The model projection step is also inlined into the transform loop. This is not a semantic change: it uses the same projection scale, fast multiply and clamp rules, but avoids writing `model_camera`, calling a pointer-based helper, then immediately reading the coordinates back for projection. `model_camera` is still retained for face depth.
