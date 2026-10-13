# NovaPoly 0.6.1 — GBA IWRAM linker fix

## Symptoms

Linking v0.6 with a large triangle queue fails with `.init_array` and
`.fini_array` addresses around `0x030089EC` outside the 32 KiB `iwram` region.
The linker error names the *next* sections after the IWRAM overflow; those
sections are not the root cause.

## Root cause and fix

The 1,024-entry `Triangle triangles[MAX_TRIANGLES]` array takes 30,720 bytes,
and default static variables are linked into 32 KiB IWRAM. That leaves almost
no space for the projection cache, global state, small startup sections, or hot
ARM assembly. Put the queue in EWRAM, a 256 KiB RAM area specifically supported
by devkitARM's GBA linker:

```c
#define NOVAPOLY_EWRAM_BSS __attribute__((section(".sbss")))
static Triangle triangles[MAX_TRIANGLES] NOVAPOLY_EWRAM_BSS;
```

The official devkitARM GBA linker routes `.sbss` to EWRAM, and its startup
code zeroes it automatically. No custom linker script or Makefile edit is
necessary. `gb3d_arm.s` stays in IWRAM so the hot texture loop remains fast.

## How to build

Open a GBA project exported from the updated NovaPoly Studio v0.6.1.
In the exported project folder, run `make clean && make`.

For an *already exported* v0.6 game (including Crash), you can change just
`source/main.c`: replace the original declaration
`static Triangle triangles[MAX_TRIANGLES];` with the declaration above.
Then rebuild. You do not need to re-import your models or redo your textures.

## Testing

Validated the source section layout with Clang's ARM7TDMI backend, and ran
Studio smoke tests. **Full devkitARM linking and mGBA runtime testing are
still required**; devkitARM is unavailable in this build environment.

If you still get an IWRAM overflow in a scene with huge initialized script
or object state, please include the new linker address and the `.map` file.
