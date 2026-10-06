# GB3D project format - v7 / Studio 1.0

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

## UI text

UI element `x`/`y` are **8×8 Window tile coordinates**, range 0..19 and 0..17.

```json
{
  "id": "ui_...",
  "name": "Score",
  "type": "text",
  "x": 0,
  "y": 0,
  "text": "SCORE",
  "color": 10,
  "visible": true,
  "show_value": true,
  "value": 0
}
```

Text is limited to 18 characters. Values are generated only when the element changes.

## UI bar

```json
{
  "id": "ui_...",
  "name": "Health",
  "type": "bar",
  "x": 0,
  "y": 2,
  "width": 10,
  "value": 8,
  "max": 10,
  "color": 4,
  "bg_color": 3,
  "visible": true
}
```

`width` is measured in 8×8 Window tiles, maximum 20.

## NPC

NPCs remain 32×32 hardware metasprites with 1024 palette-index pixels and a four-color sprite palette.

## Compatibility

Older projects are upgraded when opened. v0.8 framebuffer-UI coordinates are converted from 4×4 logical pixels to 8×8 Window cells. The new Window defaults to the bottom of the screen; its position can be changed in **UI...**.


## Studio 1.0 note

The full-resolution editor camera and world grid are editor-only view state and are not exported into the `.gb3d` scene. The saved `camera` field remains the game/start camera. Studio 1.0 reads older project versions and upgrades them when loaded.
