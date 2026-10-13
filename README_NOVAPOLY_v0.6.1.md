# NovaPoly Studio v0.6.1 — GBA IWRAM linker hotfix

This patch fixes the `address 0x30089ec ... .init_array / .fini_array ... not within region iwram` error in v0.6.

## What changed

The triangle queue was increased from 512 to 1024 slots in v0.6, but the 30,720-byte queue was incorrectly left in IWRAM. devkitARM normally places zero-initialized static variables in IWRAM, and the queue plus the other runtime state exceeded the GBA's 32 KiB limit.

**v0.6.1 puts the large queue in external work RAM via `.sbss`**, while leaving the ARM span filler and texture rasterizer in fast IWRAM. The game's 3D appearance, 1024 slot capacity, texture settings, and resolution are unchanged. This may change CPU access costs for queued triangles, so benchmark performance again.

## Existing Crash GBA project — one edit

In your already exported `source/main.c`, locate:

```c
static Triangle triangles[MAX_TRIANGLES];
```

Replace it with:

```c
static Triangle triangles[MAX_TRIANGLES] __attribute__((section(".sbss")));
```

Then run `make clean && make`. You **do not** need to re-import the model or reassign any textures.

## For future exports

Run this version of `gb3d_studio.py` and use **Export GBA** as before. All bundled GBA projects have also been patched.

## Verified here

- 5 GBA examples (including profiler) compile to ARM object files.
- ELF object sections confirm 30,720 bytes in `.sbss` (EWRAM) rather than `.bss` (IWRAM).
- Remaining ordinary `.bss` in samples is around 4.1 KiB.
- Studio self-test and dynamic exporter smoke test pass.

The devkitARM linker and mGBA are not installed in this environment, so final game linking and performance measurements still need your setup.

See `LINKER_IWRAM_FIX_v0.6.1.md` for details.
