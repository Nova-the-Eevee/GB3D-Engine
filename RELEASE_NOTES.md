# GB3D Studio 1.0 Release Notes

Studio 1.0 is the usability release: the GBC runtime stays compatible with the fast v0.9.x architecture, while the PC editor is rebuilt around a much more practical scene workflow.

## Main changes

- Full-resolution editor viewport with floating-point projection and global scene triangle sorting.
- Hardware-style 160 x 144 GBC preview inset remains visible while editing.
- Detached editor camera by default, so navigating the scene no longer changes the exported game camera.
- Optional **Game camera** mode to inspect the actual player/camera setup.
- Click visible geometry to select scene objects.
- Editor world grid, wireframe mode, frame-selection shortcut, and mouse-wheel camera movement.
- Reorganized Scene / Models / Viewport / Inspector interface.
- Models panel supports double-click to create an instance.
- File, Edit, Game, View, and Help menus plus common keyboard shortcuts.
- LIGHT / MEDIUM / HEAVY GBC cost estimate in the status bar.
- Built-in Quick Start and links to the full guide.
- New `GB3D_GUIDE.md` and `GB3D_GUIDE.pdf` manuals.
- Project format version 7. Older GB3D projects are upgraded when opened.

## Runtime

The 1.0 export keeps the optimized GBC renderer introduced before 1.0:

- SM83 assembly triangle rasterizer
- object culling and depth buckets
- packed 40 x 36 logical 3D framebuffer
- hardware Window UI
- hardware player/NPC sprites
- GBScript 2 native-C compilation
- on-device profiler

The large editor viewport is intentionally not a GBC performance simulator. Use the corner preview and exported-ROM profiler for hardware decisions.
