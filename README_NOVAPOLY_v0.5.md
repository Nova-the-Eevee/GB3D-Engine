# NovaPoly Studio v0.5 — Textured Viewport + Perspective NPCs

This is an in-place upgrade of the **working v0.4 Studio**. Old `.gb3d` scenes and both original exporters remain compatible.

## Textured editor viewport

Run `python gb3d_studio.py`. The toolbar's **View** dropdown switches between:

- **Textured** (default): main editor viewport displays textures on the actual imported models using the embedded RGB555 indexed PNG data and their UVs. Mouse camera movement, selection, grid, wireframe, object placement and materials window remain available. Textures are nearest-neighbor affine; editor shading and geometric projection are meant for editing, not hardware simulation.
- **GBA**: renders a low-resolution 160×128 Mode 5 source image with the GBA's 112px focal distance, 16 depth buckets, affine nearest-neighbor texture mapping, 3 levels of RGB555 shading, correct 200×160 centered LCD aspect, projected NPC sprites and initial software HUD. This is a CPU-side **visual approximation**, not an ARM instruction-level emulator. Coverage at triangle edges, texture UV stepping, exact culling, script motion, depth overlaps and animation can differ in mGBA.

The View menu also offers **GBA preview inset** (on by default), **GBC Preview Inset** (off by default), and **Use Game Camera**. To approximate the exported game's viewpoint, enable *Use Game Camera*. The regular editor camera can also be used for inspecting things from other angles.

Pillow (`pip install pillow`) is required for previews and the existing PNG material importer. Large textured models may make editor redraws slower; the PC viewport is rendered at a capped 640×440 internal resolution to stay responsive. Actual exported GBA game performance is unchanged by the PC preview.

## Perspective NPCs on the GBA

The exported GBA Mode 5 software billboard for each NPC uses the NPC's **Height** setting as its world-space height. Screen height is computed from GBA focal length and NPC camera depth:

    pixel_height = clamp((npc_height * 112) / camera_depth, 1, 96)

The NPC's original 32×32 2-bit artwork is sampled with nearest-neighbor scaling and transparency. Sprites grow as they approach, shrink with distance and remain clipped to the framebuffer. The 16 depth buckets still approximate sprite-vs-polygon depth ordering. **Player sprite stays fixed at 32×32**, preserving v0.4 third-person behavior. GBC export and GBC's hardware OAM sprites are unchanged.

For a quick depth-scaling test, choose Game → NPCs..., set Height to 12, position an NPC in front of the camera, and try closer/farther Z positions. The GBA preview will show the approximate size immediately; test on hardware to verify actual speed.

## Build

1. Launch editor with Python 3 + Pillow (Tkinter also required).
2. Open your existing `.gb3d` project or import a Blockbench OBJ + PNG and assign textures via **Materials...**.
3. Choose **Export GBA...** to a fresh folder, or try the updated `sample_gba_export`, `textured_gba_export` and `blockbench_repair_demo/repaired_gba_export`.
4. In the exported directory (with devkitPro `gba-dev`): `make clean && make`.
5. Run `novapoly_gba.gba` in mGBA or on GBA hardware.

## Test notes

- Existing GBC Studio `--self-test` and 0.3/0.4 texture/material tests passed.
- v0.5 software preview tests check distinct textures, RGB555 output and NPC image area at three distances.
- Tkinter's editor and GBA views were smoke-tested under a virtual display.
- Generated GBA C and ARM sources passed target=arm-none-eabi compilation with Clang. Clang's `long_call` attribute warning is expected there; devkitARM/GCC supports it.
- A complete devkitARM ROM link and execution in mGBA or physical GBA was **not possible in this environment**. Your on-device performance results are still needed.

### Included NPC depth demonstration

Open `npc_depth_demo/npc_depth.gb3d` and switch the View dropdown to **GBA**. You will see the same sprite design at a near and far position, next to a two-texture plane. The ready-to-build demo is in `npc_depth_demo/gba_export` and `GBA_preview_approx.png` is a generated **PC approximation**, not a ROM screenshot.
