# Update: NovaPoly Studio v0.6.3

**NEW:** See [README_v0.6.3.md](README_v0.6.3.md) for scrolling inspector tabs, the Game-menu profiler switch, and the diagnostic 512-vs-1024 GBA queue comparison.

# Update: NovaPoly Studio v0.5

**NEW:** See [README_NOVAPOLY_v0.5.md](README_NOVAPOLY_v0.5.md) for the main editor textured viewport, GBA preview and perspective-scaled NPC sprites.

# Update: NovaPoly Studio v0.4

**NEW:** See [README_NOVAPOLY_v0.4.md](README_NOVAPOLY_v0.4.md) for the Material Editor, Blockbench repairs and new UI.

# NovaPoly Studio 0.3 — GBA textured polygon update

**NovaPoly** is the new platform-neutral name for the GB3D family of software 3D renderers. The existing `gb3d_studio.py`, `gb3d_core.py` and `.gb3d` files retain their names for backwards compatibility. No scene conversion is required. The GBC exporter continues to use the original flat-color SM83 engine.

## Real textured polygons (GBA)

The GBA Mode 5 software rasterizer now supports Wavefront OBJ + MTL **multiple diffuse textures per mesh**. Faces pick their material's `map_Kd` image, sample imported per-corner `vt` UV coordinates, and retain classic flat-shaded faces when no diffuse texture is assigned. GBA framebuffer resolution remains 160x128 with aspect-correct centered display.

**Images are preprocessed once, on your computer:**
- Auto downscale to power-of-two width and height; default maximum 32px (16/32/64 selectable).
- Preserve aspect ratio as closely as available power-of-two dimensions allow.
- Reduce to 32 colors per texture by default (16/32/64 selectable), convert RGB -> GBA 15-bit BGR555, and generate 3 pre-shaded palettes.
- Store indices and palette directly **inside the `.gb3d` scene**. Original PNGs/JPEGs are not needed after import.
- OBJ materials can reference more than one texture file. Reusing a texture within a model deduplicates it.

The GBA writes indexed nearest-neighbour **affine UV-mapped** pixels in a fast ARM assembly span loop. This is actual texture mapping on triangles, not Mode 7 or sprites pasted onto faces. It is intentionally **not perspective-correct** (texture wobble can occur on oblique triangles). Rendering remains painter's 16-bucket sorting; there is no per-pixel Z-buffer or texture transparency. Alpha is composited against black during import. GBC editor preview is still flat-shaded rather than textured.

## Getting started

1. Install Python 3 + `pip install pillow` (Pillow>=10). Tkinter is also needed for Studio.
2. Run `python gb3d_studio.py`.
3. Optionally open **Game → GBA Texture Settings** (defaults: max 32px, 32 colors/image).
4. Choose **Import OBJ + Textures**. Put the `.obj`, `.mtl`, and referenced images together or in their proper relative subfolders. Models must have OBJ `vt`, `usemtl`, `mtllib`, and MTL `map_Kd` data for the texture to appear. Ordinary untextured OBJ continues to work.
5. **Export GBA**, then `make clean && make` in the exported directory with devkitPro `gba-dev` installed.
6. Open `novapoly_gba.gba` in mGBA or on compatible hardware.

Try the included `texture_demo/dual_material.obj` (uses two separate PNG textures in one mesh). `sample_gba_export` remains the original gameplay sample. `textured_gba_export` is the ready-to-compile dual-material example.

### Technical limits

- 64 total texture slots per exported GBA scene (counted per model; identical images in separate models are not currently globally deduplicated).
- Each image up to 64×64 pixels, 64 colors. Default 32×32 maximum, 32 colors.
- 8.8 fixed-point UVs, wrap addressing, affine mapping; no perspective correction or mipmaps.
- Color depth on screen: RGB555. Texture image dimensions are powers of two.
- Original GBC color palettes/shading and GBScript 2 gameplay remain supported.
- `map_Kd` with a plain filename/path is supported; exotic MTL mapping flags/options may require editing the MTL.

## Builds and tests

The GBA C and ARM assembly sources can be syntax-checked with an ARM cross compiler; building a full `.gba` ROM still requires devkitARM/libgba. `python gb3d_studio.py --self-test` tests the original Studio/GBC pipeline. `python tests/test_novapoly_textures.py` tests two-map OBJ import and GBA header export (Pillow required). Test textured sample rendering and performance in mGBA / on GBA hardware.

## Name

**NovaPoly** is a working project name, chosen because it's no longer only for Game Boy Color or Advance. If you prefer another name, we can rename the branding without changing the `.gb3d` file format.
