# GBScript 2

GBScript is an export-time language. GB3D Studio validates it on the PC and emits native C into `scene_gb3d.h`. The Game Boy does **not** run an interpreter.

## Events

```text
@start
@update
@a
@b
@select
@start_button
@up
@down
@left
@right
@a_held
@b_held
@select_held
@up_held
@down_held
@left_held
@right_held
```

Code before the first event is treated as `@update`, except `var` declarations, which are script-wide.

## Variables

Variables are signed 16-bit integers and persist between events/frames.

```text
var timer = 0
var speed = 2
var direction = 1

@update
timer += 1
```

Supported assignments:

```text
x = expression
x += expression
x -= expression
x *= expression
x /= expression
x %= expression
```

## Expressions

Supported operators:

```text
+  -  *  /  %
&  |  ^  <<  >>
<  <=  >  >=  ==  !=
and  or  not
```

Functions:

```text
abs(x)
min(a, b)
max(a, b)
rand(n)      # integer 0..n-1
```

Useful built-in values:

```text
self_x  self_y  self_z  self_visible
player_x player_y player_z
camera_x camera_y camera_z
camera_yaw camera_pitch
frame
true false
```

`self_*` means the 3D object or NPC that owns the script.

## Conditions

```text
if player_x > self_x
    move 1, 0, 0
else
    move -1, 0, 0
end
```

## Loops

```text
repeat 4
    move 1, 0, 0
end
```

```text
while timer < 10
    timer += 1
end
```

`break` and `continue` work inside loops.

**Important:** a `while` loop that never becomes false will lock the game, exactly like an infinite loop in C.

## Command argument syntax

Old whitespace syntax still works for simple arguments:

```text
move 1 0 0
```

For expressions containing spaces, use commas:

```text
move speed * 2, 0, -1
camera_move 0, abs(player_y-self_y), 0
```

## Self commands

For 3D object scripts:

```text
move dx, dy, dz
set_pos x, y, z
spin pitch, yaw
set_rot pitch, yaw
show
hide
toggle
```

For NPC scripts, `move`, `set_pos`, `show`, `hide`, and `toggle` operate on that NPC. NPC billboards do not have 3D `spin`/`set_rot`.

## Other 3D objects

```text
move_object "Door", dx, dy, dz
set_object_pos "Door", x, y, z
spin_object "Door", pitch, yaw
set_object_rot "Door", pitch, yaw
show_object "Door"
hide_object "Door"
toggle_object "Door"
```

Names containing spaces must be quoted. You may also use `#0`, `#1`, etc.

## NPCs

```text
npc_move "Bob", dx, dy, dz
npc_set "Bob", x, y, z
npc_show "Bob"
npc_hide "Bob"
npc_toggle "Bob"
```

Example patrol:

```text
var timer = 0
var direction = 1

@update
move direction, 0, 0
timer += 1

if timer >= 30
    direction *= -1
    timer = 0
end
```

## Player

```text
player_move dx, dy, dz
player_set x, y, z
player_jump
player_show
player_hide
player_toggle
```

## Camera / scene

```text
camera_move dx, dy, dz
camera_set x, y, z
camera_look yaw_delta, pitch_delta
camera_set_look yaw, pitch
reset_camera
set_sky Blue
```

`set_sky` accepts a color name or a numeric expression.

## HUD / UI

UI elements are addressed by their editor name.

```text
ui_set_value "Score", 100
ui_add_value "Score", 1
ui_set_max "Health", 20
ui_show "Health"
ui_hide "Health"
ui_toggle "Health"
```

Example score counter:

```text
var score = 0

@b
score += 10
ui_set_value "Score", score
```

## Hardware Window layer (Studio 1.0)

HUD elements now render on the Game Boy Window layer instead of into the 3D framebuffer. Element commands mark the Window map dirty and it is rebuilt only when needed.

```text
window_show
window_hide
window_toggle
window_move x, y
```

`window_move` uses physical LCD coordinates. A typical bottom HUD begins around:

```text
window_move 0, 112
```

The Window always extends from its position toward the bottom-right of the screen, which is a hardware limitation of the Game Boy Window layer.
