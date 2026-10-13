# GB3D Advance 0.2 check results

2026-10-10

- Python syntax check: `gb3d_gba.py`, `gb3d_core.py`, `gb3d_studio.py` PASS
- Original `gb3d_studio.py --self-test` PASS (normal and fixed-camera GBC exports)
- GBA scene export and clang `--target=armv4t-none-eabi -mcpu=arm7tdmi` compile:
  - Original Studio sample with spinning object, player, NPC, score HUD: PASS
  - Immutable fixed-camera mesh baking: PASS
  - Fixed-camera scene with dynamic object: PASS
  - Multi-event GBScript with camera/NPC/player/HUD commands: PASS
  - Invisible object with `@a show`: PASS
  - Script mutating a different object (only non-mutated geometry baked): PASS
  - Empty scene: PASS
- ARM7 assembly hot loop `gb3d_arm.s`: clang target assembler PASS
- Source bundle retains linker-fix Makefile (`export LD := $(CC)`).

NOT TESTED HERE: full devkitPro ROM link, mGBA boot, real GBA performance,
rendering and collision behaviors under actual gameplay. Those need user-side
GBA tests and may reveal further bugs; do not treat this as hardware verified.

## v0.2.1 aspect fix

- Aspect transform: PA=PD=205; BG2X=-4100, BG2Y=0.
- Current sky color is written to the backdrop during VBlank.
- Same 160x128 framebuffer, so no additional fill work.
- Editor/arm compiler checks performed separately; mGBA visual confirmation pending.
