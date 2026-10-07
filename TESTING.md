# Studio 1.2 validation

Before packaging this build:

- Python source passed `py_compile`.
- Editor `--self-test` passed normal and fixed-camera export tests.
- Generated Release, Profile, Fixed-Camera Release, and Fixed-Camera Profile `main.c` files passed host C syntax checks against a small GBDK API stub.
- Release preprocessing confirmed profiler code is absent; Profile preprocessing confirmed it is present.
- Reciprocal quotient/remainder setup was exhaustively checked for every engine edge numerator 0..159 and denominator 1..163.
- New quotient/remainder DDA edge sequences exactly matched the old Bresenham-style edge sequence over every clamped dx/dy combination.
- 100,000 randomized triangles produced identical logical coverage between the old stable edge behavior and the new DDA behavior in the reference test.
- Quarter-square fast multiplication was checked exhaustively over the common coordinate range (-1024..1024) x (-127..127), including signed values.
- The generated 5x7 profiler font was visually inspected.
- The updated PDF guide was rendered and visually checked.

The build environment used for packaging does **not** contain GBDK's actual `lcc`/SDASGB toolchain, so the final SM83 assemble/link step must be performed with a real GBDK install. Real GBC/emulator testing remains the final authority.

## Post-profiler raster hot-path validation

For the raster-only follow-up in this package:

- Python source passed `py_compile` again.
- Editor `--self-test` passed normal and fixed-camera export tests again.
- 200,000 randomized clamped DDA edges matched the previous quotient/remainder edge sequence exactly after switching to signed-q + 8-bit-error/carry stepping.
- 300,000 randomized triangles produced identical scanline edge spans between the previous DDA walker and the new tighter loop structure.
- Direct `(y >> 1) * 32` packed-row address arithmetic was checked against the old row-offset table for all 36 logical scanlines.
- The known-correct single-row packed span writer is retained. The old broken two-row experiment is not used.

As with the base 1.2 package, the build environment used here does not contain
GBDK `lcc`/SDASGB, so a real GBDK compile plus emulator/hardware run is still the
final assembly/link and timing test.

## Expanded profiler restoration after raster hot-path pass

The raster-hotpath archive initially inherited the older one-page profiler from
its source ZIP, so only `F/R/U/T/S/P` was visible. This follow-up restores the
expanded four-page profiler in both the export template and bundled sample
export.

Validation performed for this follow-up:

- Python sources passed `py_compile`.
- Editor `--self-test` passed normal and fixed-camera export tests.
- Profile tile allocation was checked for overlap: profiler tiles use 80..95
  and 229..248 in VRAM bank 1; sprite art remains 0..79 and normal Window UI
  remains 96..228.
- Preprocessor conditional counts in the modified runtime remain balanced.
- Rasterizer assembly is unchanged from the already validated hot-path pass.

Controls in a Profile build:

- `START + SELECT`: toggle profiler.
- `LEFT / RIGHT`: previous/next profiler page while it is visible. These inputs
  are masked from game movement while profiling so page changes do not move the
  camera and spoil comparisons.

Page 3 is the important renderer page for the current optimization work:
`V` transform/project, `B` face tests + bucket insertion, `D` bucket flush +
triangle rasterization, `Q` remaining scene overhead, `N` trivially off-screen
faces, `T` triangles submitted.


## Triangle setup hot-path validation

For the per-triangle setup follow-up in this package:

- Python sources pass `py_compile`.
- Editor `--self-test` passes normal and fixed-camera export tests.
- The specialized edge divider was exhaustively checked for every legal
  projected numerator 0..103 and denominator 1..99 (10,296 combinations),
  matching Python integer quotient/remainder exactly.
- The new SM83 stable Y-sort logic was compared against the previous C sorting
  network over 1,000,000 randomized projected triangles, including equal-Y
  ties; every sorted vertex order matched.
- 300,000 randomized queue-eligible positive-area/non-trivially-offscreen
  triangles matched the previous setup exactly for sorted coordinates and all
  long/top/bottom q/r/dy/step values.
- The removed draw-time clamps are proven redundant by the queue domain:
  `PROJ_X=-32..71` is strictly inside `TRI_X=-64..95`, and
  `PROJ_Y=-32..67` is strictly inside `TRI_Y=-64..99`.
- The removed flat-triangle special case is unreachable from the queue because
  `collect_model_triangles()` rejects `cross <= 0` before insertion.
- The bundled `sample_export`, `_selftest_export`, and
  `_selftest_fixed_export` were regenerated from the updated templates.

The packaging environment still does not contain GBDK `lcc`/SDASGB, so the
user's normal GBDK compile is the final assembler/link check and the hardware
profiler remains the final performance authority.


## Compiler-first / ASM queue checks

- Export a static object with Fixed Camera Fast Path OFF and confirm `SCENE_STATIC_WORLD_BAKED_COUNT` is non-zero and `scene_world_obj_*_vertices` contains 16-bit world coordinates.
- Add `move`, `set_pos`, `spin`, or `set_rot` to that object and confirm it automatically falls back to runtime transforms (`world_vertices = 0`) rather than exporting stale baked geometry.
- With Fixed Camera Fast Path ON, scripted object transforms are allowed; camera-orientation commands such as `camera_look` remain rejected.
- Compare profiler page 3 before/after: static-heavy scenes should primarily reduce `V`; the assembly bucket flush should reduce `D`.
- Verify painter order and visuals from multiple camera angles; queue flush order must remain bucket 15 -> 0 and preserve each bucket's linked-list order.
