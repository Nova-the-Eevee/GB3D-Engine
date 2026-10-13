**NovaPoly Studio v0.5:** GBA NPC billboards now scale by height/distance and the PC editor can preview GBA-style textures. See `README_NOVAPOLY_v0.5.md`.

# NovaPoly Studio 0.3 (formerly GB3D Advance)

This is the original GB3D 1.2 GBC engine and its GBA renderer/gameplay port,
now under the console-neutral **NovaPoly** working name.

GBA v0.3 adds *real per-triangle image texturing* with multiple OBJ/MTL `map_Kd`
images, PC-side resizing and quantization, UV mapping and an ARM assembly
texture span rasterizer. Read `README_NOVAPOLY.md` for workflow, limits,
performance caveats and demo instructions. Old `.gb3d` files work unchanged.

To build: Install devkitPro gba-dev, export a GBA project with Studio,
then `make clean && make`; output is `novapoly_gba.gba`.

Both the original gameplay sample (`sample_gba_export`) and the textured
sample (`textured_gba_export`) are included.
