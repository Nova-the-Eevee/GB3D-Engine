# NovaPoly Studio v0.6.2 — performance regression recovery

## Why this version exists

The user measured about **7 FPS** with a textured ~700-polygon Crash Bandicoot
model in v0.5, and **4 FPS** after v0.6.1. This is a real-world regression.
Neither the packed texel writes nor the full EWRAM triangle queue had been
benchmarked on actual hardware when introduced.

## Changes

- **Reference UV row calculations are the default**, with the v0.6
  incremental approach available using `NOVAPOLY_INCREMENTAL_UV=1`.
- **Reference ARM texture loop is the default** (`NOVAPOLY_FAST_TEX 0`). This
  is the known-good v0.5 pixel-at-a-time kernel, retained in v0.6.
- **Hybrid triangle queue**: first 512 entries in IWRAM (fast), overflow
  entries 512–1023 in EWRAM (`.sbss`). Never again put all 1024 in IWRAM.
- Preserves 1024 queued triangle capacity, all textures, Mode 5 scaling,
  gameplay, the original GBC exporter, and the v0.6 profiler.
- All bundled GBA exports have received the same change. New editor exports
  copy it from `gba_runtime`.

These are reasoned rollback/fixes, **not** a measured GBA FPS improvement. No
cycle-accurate hardware benchmark was available to the packager.

## Test Crash fairly (IMPORTANT)

1. In NovaPoly **disable Profiler build** under Scene settings; it adds per-
   triangle timer reads and other instrumentation and can skew FPS.
2. Export Crash again from v0.6.2, `make clean && make`, and compare from the
   exact same camera position as the previous 7 FPS / 4 FPS tests.
3. If it is still slower, try changing just `NOVAPOLY_HOT_TRIANGLES` from `512`
   to `0` in exported `source/main.c` to isolate the queue-placement effect.
4. Try setting `NOVAPOLY_FAST_TEX` from `0` to `1` to isolate the texture-loop
   effect. Rebuild after **each** change (`make clean && make`).
5. For a separate test, change `NOVAPOLY_INCREMENTAL_UV` from `0` to `1`.
6. Use `Profiler build` only for finding hotspots; measure actual playable FPS
   with the profiler disabled on all builds being compared.

Do not change texture sizes, resolution, model polygons, or camera positions
between comparisons.

## Simple retroactive fix for an exported Crash project

Re-export the scene with v0.6.2. The memory changes involve more than a
single array attribute; don't paste only one definition into the old source.

## What has and hasn't been checked

The exporter/self-test and ARM-target C/assembly object compilation can be
checked in the packaging environment. A complete devkitARM link and real GBA
or mGBA frame timings have **not** been confirmed here.
