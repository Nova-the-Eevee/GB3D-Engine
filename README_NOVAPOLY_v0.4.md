# NovaPoly Studio v0.4 — Material Editor & Blockbench OBJ Repair

This version keeps **both** render backends: the original Game Boy Color (GBC) flat-color software renderer and the Game Boy Advance (GBA) ARM affine texture mapper. Existing `.gb3d` projects still open without conversion.

## How to add an image texture to an existing model

1. Install Python 3 with Tkinter and Pillow (`python -m pip install pillow`).
2. Run `python gb3d_studio.py` (or `run.bat` on Windows).
3. Import your Blockbench model using **Import OBJ**. Use an **OBJ export**, not `.bbmodel`; keep the `.mtl` if present. An OBJ missing or misreferencing a PNG now still imports instead of aborting.
4. Click your model in the **MODELS** list on the left, then **Materials...** (or Game -> Materials & UVs...).
5. Pick the imported material group (e.g. `body`, `eyes`, `ears`). Its faces become selected, even if the source MTL has missing image references.
6. Click **Load PNG for selected faces**, then choose its image file. Do this for each material group; the model can have multiple textures.
7. The 3D preview and Texture + UV preview show the assignments. Select any individual triangles from the list or click triangles in the 3D preview to edit individual faces.
8. Choose **Generate box UVs** if the OBJ has no usable texture coordinates or the image appears on the wrong faces. This is a fallback UV projection, not a recreation of Blockbench's original unwrap.
9. Save your project and select **Export GBA**. Inside the export folder run `make clean && make` with devkitPro gba-dev installed.

You can repair **models already imported in older `.gb3d` projects** without importing them again. If the old model already has UV coordinates, the editor reuses them.

## Image conversion and hardware limits

- Automatic image resize to power-of-two sizes, max dimension 16/32/64 (default 32).
- Automatic color quantization to 16/32/64 colors (default 32), then RGB555 conversion.
- Converted PNG data stored inside `.gb3d`; the original image file is not needed for later export.
- Up to 64 texture slots across the whole GBA scene, 64x64 max per texture.
- Separate images on separate model faces, **not** multiple simultaneous texture layers per polygon.
- Nearest-neighbour, **affine** UV mapping. No perspective-correct mapping, alpha blending, Z-buffer, or exact hardware GBA emulator inside the editor.
- The Material Editor's 3D preview is a PC-side approximation, while the large main Studio viewport and GBC inset still use their original untextured drawing paths.
- The GBC export continues to use original flat-colored polygons; texture assets only affect the GBA backend.

## Blockbench tips

- If OBJ + MTL + PNG files are exported separately, place the images next to the `.mtl` file or preserve the original relative folder structure.
- If the importer warns about missing images, that's now recoverable: the material names remain, and you can assign them manually from the Material Editor.
- If Blockbench exported no `vt` UVs, NovaPoly can automatically generate per-triangle box UVs. This works well for simple block shapes but won't match hand-painted atlas UV islands. To preserve a carefully packed texture atlas, export the UV data from Blockbench and avoid **Generate box UVs**.
- PNGs may contain transparency, but NovaPoly v0.4 composites transparency onto black, just as v0.3 did.

## Included examples

- `texture_demo/dual_material.obj` — original two-texture OBJ demo with existing UVs.
- `blockbench_repair_demo/blockbench_style.obj` — purposely missing both referenced PNG filenames; demonstrates repair using two replacement images.
- `blockbench_repair_demo/repaired_example.gb3d` — same scene with the replacement PNGs embedded.
- `blockbench_repair_demo/repaired_gba_export/` — generated devkitARM project to compile.

### Testing performed

- Original Studio GBC `--self-test` and v0.3 texture test pass.
- Material operations tested with broken OBJ/MTL paths, existing UVs, missing UVs, multiple images, texture replacement/removal, project save/open and GBA scene export.
- Tkinter dialog smoke-tested under Xvfb with scripted material selection, image assignment and preview updates.
- Generated v0.4 GBA C and ARM assembly compiled to ARM object files with Clang cross compilation. **A full devkitARM ROM link and mGBA / real GBA test was not run here**; test a generated `.gba` on your setup.
