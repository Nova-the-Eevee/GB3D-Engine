# GB3D Studio 1.0

GB3D Studio is a lightweight 3D scene editor and GBDK exporter for Game Boy Color. It combines a comfortable full-resolution PC editor viewport with a corner preview of the engine's actual 40 x 36 logical GBC renderer.

## 1.0 highlights

- New full-resolution editor viewport with floating-point projection and global triangle sorting
- Always-available GBC-accurate preview inset for the real chunky renderer
- Click geometry in the viewport to select objects
- Detached editor camera, optional game-camera view, grid, wireframe, frame-selection, and mouse-wheel navigation
- Reorganized Scene / Models / Viewport / Inspector layout with proper menus and shortcuts
- Built-in LIGHT / MEDIUM / HEAVY GBC cost estimate in the status bar
- Hardware Window-layer HUD system from v0.9.x
- Complete `GB3D_GUIDE.md` and `GB3D_GUIDE.pdf`

## Run

```text
python gb3d_studio.py
```

or on Windows:

```text
run.bat
```

Pillow is needed only for importing custom 32 x 32 player/NPC images:

```text
pip install pillow
```

## Export

Use **Export GBDK**, then run `compile.bat` in the exported folder with GBDK installed and `lcc` available in PATH.

## Rendering architecture

```text
PC editor viewport     -> full-resolution editing renderer
GBC preview/runtime    -> 40 x 36 packed-tile 3D background
Game UI                -> hardware Window layer
Player/NPCs            -> hardware sprites
```

For the full workflow, open **Help -> Open User Guide** or read `GB3D_GUIDE.md`.
