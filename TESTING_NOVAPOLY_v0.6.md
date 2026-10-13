# v0.6 validation

## Completed tests in packaging environment

- Python `gb3d_studio.py --self-test`: **PASS**.
- Python `py_compile` of Studio and exporter: **PASS**.
- Exported untextured sample, textured sample, NPC depth demo, and repaired Blockbench demo: **PASS**.
- Exported fifth `benchmark_gba_export` with `SCENE_PROFILE_BUILD=1`: **PASS**.
- Cross-compiled each of those five generated `main.c` and `gb3d_arm.s` to ARM7TDMI object files using Clang `--target=arm-none-eabi -march=armv4t`: **PASS**.
- Verified reference-vs-2-pixel store semantics in a Python host-side test for **25,000 randomized spans** with texel address wrapping, negative coordinates, odd/even pixel-start alignment and UV-gradient recurrence: **PASS**.

The host test is **not** a cycle-accurate emulator and does not execute the actual GBA binary; assembler syntax was checked independently by the ARM-target compiler.

## Not tested

- Full devkitARM cartridge link / boot in mGBA / real GBA.
- Crash Bandicoot scene (not included in this ZIP).
- Actual CPU cycle count or FPS improvement: requires user to benchmark the same camera angles on mGBA or GBA.

## Recommended test

1. Export a copy of the user's 700-poly Crash scene using v0.6 and **Profiler build**.
2. Hold START+SELECT to show profiler. Use L/R to select page 1 and page 2.
3. Record front / back view engine FPS, TEX timing and T PIX. Repeat after compiling with `NOVAPOLY_FAST_TEX 0` for the original texture kernel.
4. Repeat both as release builds to compare actual player-facing performance.
