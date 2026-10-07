# GB3D project format - v9 / Studio 1.2

`.gb3d` files are human-readable JSON.

Top-level fields include:

```text
format
version
name
camera
settings
player
models
objects
npcs
ui
```

## 1.2 runtime settings

`settings.fixed_camera_fast` is a boolean:

```json
{
  "fixed_camera_fast": true
}
```

When enabled, export creates render-only models whose static object rotation and fixed game-camera orientation are baked on the PC. Logical `objects` remain in normal world coordinates for collision and game state.

Camera position is still dynamic. Camera yaw/pitch and scene-object position/rotation must remain fixed during play; export validation rejects GBScript commands that would invalidate the bake.

The default is `false`, including for upgraded older projects.

## Window UI settings

`settings.window_ui` controls the hardware Window layer:

```json
{
  "enabled": true,
  "x": 0,
  "y": 112,
  "background": 0
}
```

`x` and `y` are physical 160×144 LCD pixel coordinates. The Game Boy Window begins there and extends to the bottom-right of the display.

UI element `x`/`y` are 8×8 Window tile coordinates, range 0..19 and 0..17. NPCs remain 32×32 hardware metasprites.

## Compatibility

Studio 1.2 reads older project versions and upgrades them to v9. Editor-only camera/navigation state is not exported as game state; the saved `camera` object remains the game/start camera.


Studio 1.2 settings include `fixed_camera_fast`, `profile_build`, and `sector_culling`. `profile_build=false` is the release default.
