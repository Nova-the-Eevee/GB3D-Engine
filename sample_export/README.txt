GB3D Studio 1.2 export - correct DDA raster + fast math + CGB GDMA
=======================================

Compile with compile.bat (GBDK in PATH) or make with GBDK_HOME set.

Studio 1.2 runtime:
  rasterizes triangles directly into final tile IDs + CGB attributes
  no 16-color intermediate framebuffer
  no per-frame 8-palette search / tile palette compiler
  whole scan-converted triangle lives in gb3d_render.s
  native 32x18 RAM map stride for direct CGB GDMA upload
  exact quotient/remainder DDA rasterizer; no per-row edge while-loop
  reciprocal table removes generic edge division
  quarter-square tables replace hot signed multiply helpers
  global 16-bucket triangle queue across all visible objects
  optional static sector reject before vertex transforms
  visible-only framebuffer clear; hidden BG-map columns stay untouched
  compiler-first static objects bake rotation + translation into 16-bit world vertices
  optional Fixed Camera Fast Path also folds locked camera rotation into static vertices
  dynamic scripted objects automatically keep the general runtime transform path
  projection scale is a precomputed ROM table; no startup division/256-byte WRAM table
  triangle bucket reset + flush/list traversal run in SM83 assembly
  editor-only edge lists and permanently invisible faces are stripped from ROM
  model transform loops project inline without a per-vertex C helper round-trip
  model-local source vertices remain signed 8-bit
  16 coarse depth buckets remain for cheap painter ordering
  object scripts compile to native C at export time (no runtime parser)
  selectable 16-color sky background
  optional built-in third-person platformer player using hardware sprites
  scriptable 32x32 hardware-sprite NPC billboards
  GBScript 2 variables, expressions, conditions, loops, NPC and HUD commands
  native Window-layer 8x8 text + value bars; UI never touches the 3D framebuffer
  Window map is rebuilt/uploaded only when UI state changes
  simple gravity/jump plus AABB collision against Solid scene objects

Color rule:
  each hardware 8x8 tile still has ONE CGB palette. All eight palettes
  share shade indices 0/1/2/3. If different materials overlap the same
  tile, the nearest (latest drawn) triangle palette wins that tile, so
  existing quadrants keep brightness but may inherit its hue.

Controls:
  D-Pad: move camera
  Hold Select + D-Pad: look
  A/B/Start/Select/D-Pad press+held events are available to scripts
  Start is no longer hardwired to camera reset; use reset_camera in a script
  Player mode: D-Pad move, hold Select+D-Pad orbit/look, A jump
