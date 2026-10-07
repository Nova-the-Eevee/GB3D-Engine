# GB3D Studio 1.2 Release Notes

Studio 1.2 is the corrective performance release after 1.1. The 1.1 two-row/tile-pair experiment produced corrupted geometry in real testing, so 1.2 deliberately returns to the known-good packed single-row coverage rules while keeping the useful architecture changes such as native-stride buffers and CGB GDMA.

## Renderer

Triangle edges use quotient/remainder DDA. Edge setup calculates `q = abs(dx)/dy` and `r = abs(dx)%dy`; each rendered row then performs a fixed `x += q`, `error += r`, and at most one correction. The current setup hot path performs the bounded 8-bit divmod directly in SM83 assembly, replacing the earlier C reciprocal/multiply helper chain.

The packed span writer writes the same logical 40x36 coverage format used by the stable renderer: two horizontal logical pixels and two vertical logical rows share one GBC tile byte. The broken 1.1 paired-row integration is gone.

## Fast math

The SM83 has no hardware multiply, so 1.2 uses a 383-entry quarter-square table to build exact unsigned 8x8 products. Signed 16x8 products are split into two byte partial products. This avoids the overflowing 1.1 experimental table and removes generic software multiply calls from the normal transform/projection/backface path.

## Scene work

Visible faces from all objects are inserted into one global 16-bucket triangle queue, then drawn far-to-near. Static objects get a conservative sector reject before vertex transformation. Collision loops use an exported list of Solid objects rather than scanning every scene object. Sprite metasprite positions are written directly into shadow OAM.

## Profiler

The profiler no longer draws 3x5 text into the 3D framebuffer. A dedicated readable 5x7 debug font lives in Window VRAM and the profiler temporarily owns the bottom three Window rows. Studio exposes **Profiler build (START+SELECT)**; when OFF, profiler code is compiled out.

## Still intentionally not done

The full vertex-transform loop is not yet rewritten as one giant SM83 assembly routine. 1.2 removes its expensive generic multiplication while keeping the transform logic in C, which is much safer after 1.1's raster regression. The profiler will tell us whether an assembly transform loop is still worth the complexity.

Likewise, sector culling is coarse object-level culling, not a BSP/portal system, and collision uses a Solid-object list rather than a full spatial grid.

### Raster hot-path follow-up
- Kept the exact single-row DDA coverage, but removed two per-scanline helper
  call/return pairs from the hot path.
- Switched DDA error accumulators from 16-bit RAM state to exact 8-bit + carry.
- Pre-signed whole-pixel edge deltas and removed the row-offset lookup table.
- Avoided stepping edges after the final bottom scanline.

### Expanded profiler restoration
- Restored the four-page hardware profiler that was missing from the first
  raster-hotpath package.
- Added full 5x7 A-Z/0-9 profiler glyph coverage using the two unused bank-1
  tile gaps around the normal Window font.
- Left/Right switches profiler pages and is masked from game input while the
  profiler is visible, making same-camera comparisons much easier.
- Renderer page 3 now directly exposes `V`, `B`, `D`, and `Q` timing so the
  raster hot-path result can be measured against the earlier `D03EE` baseline.


### Triangle setup hot-path follow-up
- Uses the actual queue invariant: projected triangle coordinates are already
  clamped to x=-32..71 and y=-32..67 before they enter the draw queue. The old
  six wider `clamp_i16()` calls in the draw path were therefore redundant and
  are gone.
- The face collector already rejects positive-area failures and trivially
  off-screen faces, so those duplicate tests are no longer repeated during
  bucket flush.
- Removed the C `fb_triangle()` argument-heavy wrapper. The bucket flush copies
  each compact seven-byte queued record directly into the ASM interface.
- Moved the stable three-comparison Y sort to SM83 registers.
- Material unpacking, pair-pattern creation, row counts, and all three edge
  quotient/remainder setups now happen in assembly.
- Edge divmod uses the real projected domain (`|dx| <= 103`, `dy <= 99`): the
  known-zero top dividend bit is skipped and the remaining seven restoring
  divide rounds are fully unrolled. Flat top/bottom edge setup is skipped when
  its stepper cannot execute.
- The per-scanline rasterizer from the successful raster-hotpath build is left
  unchanged; this pass targets per-triangle setup only.


### Compiler-first + ASM queue follow-up

This pass applies the cartridge-era rule **do work once on the PC instead of every frame on the GBC**. Objects whose position and rotation are never modified by GBScript are exported with fully transformed 16-bit world vertices. The general-camera runtime therefore skips object rotation and object-origin addition for those vertices. With Fixed Camera Fast Path enabled, the locked camera rotation is folded into the baked vertices as well, preserving the previous fixed-point rounding while moving the final per-vertex origin adds off-device. Dynamic objects remain fully supported through the existing runtime transform path.

The 256-entry projection scale table is now emitted as ROM data rather than constructed in WRAM with startup division. Fixed Camera Fast Path no longer rejects scripted object movement/rotation; only commands that alter the locked camera orientation are incompatible.

The global depth-bucket reset and flush are now SM83 assembly. Because a queued triangle is exactly 8 bytes, the flush computes `queue + index*8` with three 16-bit doubles, follows the linked list, and feeds the seven-byte draw payload directly into the triangle setup entry. This removes C pointer/list traversal and the old per-triangle C-to-global copy loop from `D`.

The exporter also strips data that the cartridge can never use: editor wireframe edge lists are no longer emitted into ROM, and faces whose logical color maps to true black/invisible are removed from the runtime face arrays. That also removes the per-face `palette == 0xFF` branch from the GBC. The model vertex loop now performs projection inline from its current `x/y/z` temporaries, avoiding an immediate WRAM write -> `project_vertex()` call -> WRAM read round-trip for every model vertex.
