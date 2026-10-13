# GB3D Advance 0.2.1 — Aspect-ratio fix

This update fixes the vertically-squished characters and scenery reported in mGBA.

GBA Mode 5 stores a 160x128 framebuffer. The previous BG2 settings filled
a 240x160 display with X at 1.5x and Y at 1.25x. That distorts square
pixels and makes the entire game 20% wider than intended.

**Now:** BG2PA = BG2PD = 205 (8.8); BG2X = -(20*205).
This centers a uniformly scaled 200x160 view, leaving 20 physical GBA
pixels per side. BG palette index 0 / backdrop tracks the scene sky color,
so these margins blend into the background. No additional software pixels
are rendered.

## For people with an existing exported project

In `source/main.c`, find the Mode 5 initialization and use the new settings:

```c
REG_BG2PA = 205;
REG_BG2PB = 0;
REG_BG2PC = 0;
REG_BG2PD = 205;
REG_BG2X = (uint32_t)(-20 * 205);
REG_BG2Y = 0;
```

To keep margins the same color as the scene sky, add
`#define REG_BG_BACKDROP REG16(0x05000000)` alongside your GBA registers
and assign `REG_BG_BACKDROP = bg;` just after `wait_vblank_start()` in the
render loop (where `bg` is the current RGB555 sky color).

The newest Studio exporter produces this automatically.

## Optional backwards-compatibility switch

In the newer `main.c`, `#define GB3D_CORRECT_ASPECT 1` enables the fix.
Set it to 0 to return to the old stretch-to-full-screen display.

## Test limitations

This update has been checked with automated exporter and ARM source compile
checks, but native GBA ROM hardware display verification still requires mGBA
or a GBA. The earlier v0.2 game was confirmed to run in mGBA by the user.
