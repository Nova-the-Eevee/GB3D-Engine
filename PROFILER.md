# GB3D 1.2 four-page hardware profiler

Build/export with **Profiler build** enabled.

- Toggle: **START + SELECT**
- Change page: **LEFT / RIGHT** while the profiler is visible

The page number is shown at the far right. Left/Right is consumed by the
profiler while visible so changing pages does not move the player/camera.

## Page 1 — overview

`F` total pre-VBlank CPU work  
`R` scene renderer  
`U` framebuffer/HUD VRAM upload  
`T` triangles submitted  
`S` scripts  
`P` player physics/camera

## Page 2 — frame plumbing

`I` input/update  
`C` camera transform cache + framebuffer clear/setup  
`W` Window/UI RAM preparation  
`G` player/NPC sprite + shadow-OAM work  
`X` other/unclassified pre-VBlank CPU work  
`F` total pre-VBlank CPU work

## Page 3 — renderer split

`V` vertex transform + projection  
`B` face rejection/backface/shading + depth-bucket insertion  
`D` global bucket flush + triangle rasterization  
`Q` remaining scene overhead (object/sector visibility and queue setup)  
`N` trivially off-screen faces  
`T` triangles submitted

For the current raster optimization, **D is the number to compare**. The prior
known baseline screenshot was `D03EE` at roughly the same camera position.

## Page 4 — workload counters

`M` mesh vertices processed  
`E` faces examined  
`T` triangles submitted  
`D` triangles dropped because the frame queue was full  
`O` visible objects  
`C` culled objects

Profiler Window map generation and profiler Window VRAM upload are deliberately
kept outside the reported `F`/`U` measurements so the diagnostic display does
not mostly measure itself.
