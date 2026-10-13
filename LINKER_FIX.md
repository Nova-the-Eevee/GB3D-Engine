# GB3D Advance v0.1 linker fix

Fixes `linking cartridge` followed by `bash: ld: command not found`.

The devkitPro `gba_rules` linker rule uses `$(LD)`. This project now sets:

```makefile
export LD := $(CC)
```

immediately after the GBA rules include. The Studio GBA exporter copies the fixed
`gba_runtime/Makefile`, so future exports also contain the correction.

If you have already exported a project from v0.1, either re-export with
this package or add the same `export LD := $(CC)` line below the include
in your existing exported project's Makefile.

Run `make clean && make` from the GBA export directory.

This patch is for the linker command selection; successful hardware execution
and a complete devkitARM ROM link have not been tested in this environment.
