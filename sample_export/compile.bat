@echo off
lcc -msm83:gb -Wf--max-allocs-per-node50000 -Wf--opt-code-speed -Wm-yC -Wm-yn"GB3D" -o game.gb main.c gb3d_render.s
if errorlevel 1 pause
