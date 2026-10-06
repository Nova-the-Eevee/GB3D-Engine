from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import subprocess
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import gb3d_core as core

APP_NAME = "GB3D Studio 1.0"


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class GB3DStudio:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} - Untitled")
        self.root.geometry("1440x860")
        self.root.minsize(1080, 680)

        self.project = core.new_project()
        self.project_path: Path | None = None
        self.selected_object_id: str | None = None
        self.dirty = False
        self.wireframe_var = tk.BooleanVar(value=False)
        self.hardware_var = tk.BooleanVar(value=True)
        self.grid_var = tk.BooleanVar(value=True)
        self.gbc_inset_var = tk.BooleanVar(value=True)
        self.game_camera_var = tk.BooleanVar(value=False)
        self.sky_var = tk.StringVar(value=core.COLOR_NAMES[0])
        self.status_var = tk.StringVar(value="Ready")
        self.perf_status_var = tk.StringVar(value="")
        self.editor_camera = dict(self.project.get("camera", {"x":0,"y":0,"z":-64,"yaw":0,"pitch":0}))
        self._inspector_lock = False
        self._render_after: str | None = None

        self._build_ui()
        self._bind_keys()
        self.refresh_all()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        # Native Tk/ttk on purpose: GB3D should stay lightweight and easy to run.
        try:
            style = ttk.Style(self.root)
            if "clam" in style.theme_names():
                style.theme_use("clam")
            style.configure("Toolbar.TButton", padding=(9, 5))
            style.configure("PanelTitle.TLabel", font=("TkDefaultFont", 10, "bold"))
            style.configure("Hint.TLabel", foreground="#707780")
            style.configure("Status.TLabel", padding=(7, 3))
        except tk.TclError:
            pass

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # ----- menu bar -----
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="New Project", accelerator="Ctrl+N", command=self.new_project)
        file_menu.add_command(label="Open...", accelerator="Ctrl+O", command=self.open_project)
        file_menu.add_separator()
        file_menu.add_command(label="Save", accelerator="Ctrl+S", command=self.save_project)
        file_menu.add_command(label="Save As...", accelerator="Ctrl+Shift+S", command=self.save_project_as)
        file_menu.add_separator()
        file_menu.add_command(label="Import OBJ...", command=self.import_obj)
        file_menu.add_command(label="Export GBDK...", command=self.export_gbdk)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="Duplicate Selected", accelerator="Ctrl+D", command=self.duplicate_selected)
        edit_menu.add_command(label="Delete Selected", accelerator="Delete", command=self.delete_selected)
        edit_menu.add_separator()
        edit_menu.add_command(label="Frame Selected", accelerator="F", command=self.frame_selected)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        game_menu = tk.Menu(menubar, tearoff=False)
        game_menu.add_command(label="Player...", command=self.edit_player_settings)
        game_menu.add_command(label="NPCs...", command=self.edit_npcs)
        game_menu.add_command(label="Window UI...", command=self.edit_ui)
        game_menu.add_separator()
        game_menu.add_command(label="Performance...", command=self.show_performance)
        menubar.add_cascade(label="Game", menu=game_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_checkbutton(label="World Grid", variable=self.grid_var, command=self.schedule_render)
        view_menu.add_checkbutton(label="Wireframe", variable=self.wireframe_var, command=self.schedule_render)
        view_menu.add_checkbutton(label="GBC Preview Inset", variable=self.gbc_inset_var, command=self.schedule_render)
        view_menu.add_checkbutton(label="Accurate GBC Palette", variable=self.hardware_var, command=self.schedule_render)
        view_menu.add_checkbutton(label="Use Game Camera", variable=self.game_camera_var, command=self._camera_mode_changed)
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Quick Start", command=self.show_quick_start)
        help_menu.add_command(label="Open User Guide", command=self.open_guide)
        help_menu.add_separator()
        help_menu.add_command(label="About GB3D Studio", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.root.configure(menu=menubar)

        # ----- compact toolbar -----
        toolbar = ttk.Frame(self.root, padding=(7, 5))
        toolbar.grid(row=0, column=0, sticky="ew")
        for text, cmd in [("New", self.new_project), ("Open", self.open_project), ("Save", self.save_project)]:
            ttk.Button(toolbar, text=text, command=cmd, style="Toolbar.TButton").pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=7)
        for text, cmd in [("Import OBJ", self.import_obj), ("Add Instance", self.add_instance), ("Duplicate", self.duplicate_selected), ("Delete", self.delete_selected)]:
            ttk.Button(toolbar, text=text, command=cmd, style="Toolbar.TButton").pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=7)
        for text, cmd in [("Player", self.edit_player_settings), ("NPCs", self.edit_npcs), ("Window UI", self.edit_ui)]:
            ttk.Button(toolbar, text=text, command=cmd, style="Toolbar.TButton").pack(side="left", padx=2)
        ttk.Button(toolbar, text="Export GBDK", command=self.export_gbdk, style="Toolbar.TButton").pack(side="right", padx=2)

        main = ttk.Panedwindow(self.root, orient="horizontal")
        main.grid(row=1, column=0, sticky="nsew")

        # ----- scene + asset panel -----
        left = ttk.Frame(main, padding=(8, 6))
        main.add(left, weight=0)
        ttk.Label(left, text="SCENE HIERARCHY", style="PanelTitle.TLabel").pack(anchor="w")
        self.scene_tree = ttk.Treeview(left, columns=("model",), show="tree headings", height=18, selectmode="browse")
        self.scene_tree.heading("#0", text="Object")
        self.scene_tree.heading("model", text="Model")
        self.scene_tree.column("#0", width=145, stretch=True)
        self.scene_tree.column("model", width=95, stretch=True)
        self.scene_tree.pack(fill="both", expand=True, pady=(5, 5))
        self.scene_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        scene_buttons = ttk.Frame(left)
        scene_buttons.pack(fill="x")
        ttk.Button(scene_buttons, text="+ Instance", command=self.add_instance).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(scene_buttons, text="Duplicate", command=self.duplicate_selected).pack(side="left", fill="x", expand=True, padx=2)
        ttk.Button(scene_buttons, text="Delete", command=self.delete_selected).pack(side="left", fill="x", expand=True, padx=(2, 0))

        ttk.Separator(left).pack(fill="x", pady=9)
        ttk.Label(left, text="MODELS", style="PanelTitle.TLabel").pack(anchor="w")
        self.model_list = tk.Listbox(left, height=7, exportselection=False, activestyle="dotbox")
        self.model_list.pack(fill="x", pady=(5, 4))
        self.model_list.bind("<Double-Button-1>", self._add_model_from_asset)
        model_buttons = ttk.Frame(left)
        model_buttons.pack(fill="x")
        ttk.Button(model_buttons, text="Import OBJ...", command=self.import_obj).pack(side="left", fill="x", expand=True, padx=(0, 2))
        ttk.Button(model_buttons, text="Recolor...", command=self.recolor_selected_model).pack(side="left", fill="x", expand=True, padx=(2, 0))
        ttk.Label(left, text="Tip: double-click a model to add an instance.", style="Hint.TLabel", wraplength=245).pack(anchor="w", pady=(5, 0))

        # ----- full-resolution viewport -----
        center = ttk.Frame(main, padding=(4, 6))
        main.add(center, weight=1)
        view_header = ttk.Frame(center)
        view_header.pack(fill="x", padx=3)
        ttk.Label(view_header, text="EDITOR VIEWPORT", style="PanelTitle.TLabel").pack(side="left")
        ttk.Checkbutton(view_header, text="Game camera", variable=self.game_camera_var, command=self._camera_mode_changed).pack(side="right", padx=(8, 0))
        ttk.Checkbutton(view_header, text="GBC inset", variable=self.gbc_inset_var, command=self.schedule_render).pack(side="right", padx=(8, 0))
        ttk.Checkbutton(view_header, text="Wireframe", variable=self.wireframe_var, command=self.schedule_render).pack(side="right", padx=(8, 0))
        ttk.Checkbutton(view_header, text="Grid", variable=self.grid_var, command=self.schedule_render).pack(side="right")
        ttk.Label(center, text="WASD move  •  Q/E vertical  •  arrows look  •  F frames selection  •  click geometry to select", style="Hint.TLabel").pack(anchor="w", padx=3, pady=(2, 2))
        self.canvas = tk.Canvas(center, bg="#171a1f", highlightthickness=1, highlightbackground="#5a6069", takefocus=1)
        self.canvas.pack(fill="both", expand=True, padx=3, pady=(2, 0))
        self.canvas.bind("<Configure>", lambda e: self.schedule_render())
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Double-Button-1>", lambda e: self.frame_selected())
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self._zoom_editor_camera(8))
        self.canvas.bind("<Button-5>", lambda e: self._zoom_editor_camera(-8))

        # ----- inspector -----
        right = ttk.Frame(main, padding=(7, 6))
        main.add(right, weight=0)
        ttk.Label(right, text="INSPECTOR", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 5))
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        object_tab = ttk.Frame(notebook, padding=9)
        scene_tab = ttk.Frame(notebook, padding=9)
        notebook.add(object_tab, text="Object")
        notebook.add(scene_tab, text="Scene")

        self.name_var = tk.StringVar()
        self.model_var = tk.StringVar()
        self.pos_vars = [tk.StringVar() for _ in range(3)]
        self.rot_vars = [tk.StringVar() for _ in range(2)]
        self.visible_var = tk.BooleanVar(value=True)
        self.solid_var = tk.BooleanVar(value=False)
        object_tab.columnconfigure(1, weight=1)

        ttk.Label(object_tab, text="Name").grid(row=0, column=0, sticky="w", pady=3)
        self.name_entry = ttk.Entry(object_tab, textvariable=self.name_var, width=20)
        self.name_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self.name_entry.bind("<Return>", lambda e: self.apply_inspector())
        self.name_entry.bind("<FocusOut>", lambda e: self.apply_inspector())
        ttk.Label(object_tab, text="Model").grid(row=1, column=0, sticky="w", pady=3)
        self.model_combo = ttk.Combobox(object_tab, textvariable=self.model_var, state="readonly", width=18)
        self.model_combo.grid(row=1, column=1, sticky="ew", pady=3)
        self.model_combo.bind("<<ComboboxSelected>>", lambda e: self.apply_inspector())

        ttk.Label(object_tab, text="Position", style="PanelTitle.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 3))
        for i, lab in enumerate(["X", "Y", "Z"]):
            ttk.Label(object_tab, text=lab).grid(row=3+i, column=0, sticky="w", pady=2)
            ent = ttk.Entry(object_tab, textvariable=self.pos_vars[i], width=11)
            ent.grid(row=3+i, column=1, sticky="ew", pady=2)
            ent.bind("<Return>", lambda e: self.apply_inspector())
            ent.bind("<FocusOut>", lambda e: self.apply_inspector())
        ttk.Label(object_tab, text="Rotation (0-63)", style="PanelTitle.TLabel").grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 3))
        for i, lab in enumerate(["Pitch X", "Yaw Y"]):
            ttk.Label(object_tab, text=lab).grid(row=7+i, column=0, sticky="w", pady=2)
            ent = ttk.Entry(object_tab, textvariable=self.rot_vars[i], width=11)
            ent.grid(row=7+i, column=1, sticky="ew", pady=2)
            ent.bind("<Return>", lambda e: self.apply_inspector())
            ent.bind("<FocusOut>", lambda e: self.apply_inspector())
        ttk.Checkbutton(object_tab, text="Visible at start", variable=self.visible_var, command=self.apply_inspector).grid(row=9, column=0, columnspan=2, sticky="w", pady=(9,2))
        ttk.Checkbutton(object_tab, text="Solid platform / collider", variable=self.solid_var, command=self.apply_inspector).grid(row=10, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Button(object_tab, text="Apply Transform", command=self.apply_inspector).grid(row=11, column=0, columnspan=2, sticky="ew", pady=(7,2))
        ttk.Button(object_tab, text="Edit Object Script...", command=self.edit_selected_script).grid(row=12, column=0, columnspan=2, sticky="ew", pady=2)
        self.script_status_label = ttk.Label(object_tab, text="Script: none", style="Hint.TLabel")
        self.script_status_label.grid(row=13, column=0, columnspan=2, sticky="w", pady=(2, 8))

        scene_tab.columnconfigure(1, weight=1)
        ttk.Label(scene_tab, text="Sky color").grid(row=0, column=0, sticky="w", pady=3)
        self.sky_combo = ttk.Combobox(scene_tab, textvariable=self.sky_var, values=core.COLOR_NAMES, state="readonly", width=14)
        self.sky_combo.grid(row=0, column=1, sticky="ew", pady=3)
        self.sky_combo.bind("<<ComboboxSelected>>", self._on_sky_changed)
        ttk.Checkbutton(scene_tab, text="Accurate palette in GBC inset", variable=self.hardware_var, command=self.schedule_render).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 8))
        ttk.Separator(scene_tab).grid(row=2, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Label(scene_tab, text="Viewport Camera", style="PanelTitle.TLabel").grid(row=3, column=0, columnspan=2, sticky="w")
        self.cam_label = ttk.Label(scene_tab, text="", justify="left")
        self.cam_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 6))
        ttk.Button(scene_tab, text="Frame Selected", command=self.frame_selected).grid(row=5, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(scene_tab, text="Reset View Camera", command=self.reset_camera).grid(row=6, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Separator(scene_tab).grid(row=7, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(scene_tab, text="Game", style="PanelTitle.TLabel").grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Button(scene_tab, text="Player...", command=self.edit_player_settings).grid(row=9, column=0, columnspan=2, sticky="ew", pady=(5,2))
        ttk.Button(scene_tab, text="NPCs...", command=self.edit_npcs).grid(row=10, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(scene_tab, text="Window UI...", command=self.edit_ui).grid(row=11, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Button(scene_tab, text="Performance Report...", command=self.show_performance).grid(row=12, column=0, columnspan=2, sticky="ew", pady=(8,2))
        ttk.Separator(scene_tab).grid(row=13, column=0, columnspan=2, sticky="ew", pady=8)
        self.stats_label = ttk.Label(scene_tab, text="", justify="left")
        self.stats_label.grid(row=14, column=0, columnspan=2, sticky="w")

        status = ttk.Frame(self.root)
        status.grid(row=2, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.status_var, relief="sunken", anchor="w", style="Status.TLabel").grid(row=0, column=0, sticky="ew")
        ttk.Label(status, textvariable=self.perf_status_var, relief="sunken", anchor="e", style="Status.TLabel").grid(row=0, column=1, sticky="e")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _bind_keys(self):
        for key in ["w", "a", "s", "d", "W", "A", "S", "D", "q", "e", "Q", "E", "Left", "Right", "Up", "Down"]:
            self.root.bind(f"<KeyPress-{key}>", self._on_key)
        self.root.bind("<Control-s>", lambda e: self.save_project())
        self.root.bind("<Control-S>", lambda e: self.save_project_as())
        self.root.bind("<Control-o>", lambda e: self.open_project())
        self.root.bind("<Control-n>", lambda e: self.new_project())
        self.root.bind("<Control-d>", lambda e: self.duplicate_selected())
        self.root.bind("<Delete>", lambda e: None if self._editing_text() else self.delete_selected())
        self.root.bind("<KeyPress-f>", lambda e: None if self._editing_text() else self.frame_selected())
        self.root.bind("<KeyPress-F>", lambda e: None if self._editing_text() else self.frame_selected())

    def _camera_mode_changed(self):
        if not self.game_camera_var.get():
            self.editor_camera = dict(core.effective_camera(self.project))
        self.refresh_camera_label()
        self.schedule_render()

    def _on_canvas_click(self, event):
        self.canvas.focus_set()
        items = self.canvas.find_withtag("current")
        if not items:
            return
        for tag in self.canvas.gettags(items[-1]):
            if tag.startswith("obj:"):
                oid = tag[4:]
                if self.scene_tree.exists(oid):
                    self.selected_object_id = oid
                    self.scene_tree.selection_set(oid)
                    self.scene_tree.see(oid)
                    self.refresh_inspector()
                    self.schedule_render()
                break

    def _on_mousewheel(self, event):
        delta = getattr(event, "delta", 0)
        if delta:
            self._zoom_editor_camera(8 if delta > 0 else -8)

    def _zoom_editor_camera(self, amount):
        if self.game_camera_var.get():
            return
        cam = self.editor_camera
        sn, cs = core.isin(int(cam.get("yaw", 0))), core.icos(int(cam.get("yaw", 0)))
        cam["x"] = int(cam.get("x", 0)) + ((sn * int(amount)) >> 7)
        cam["z"] = int(cam.get("z", -64)) + ((cs * int(amount)) >> 7)
        self.refresh_camera_label()
        self.schedule_render()

    def _add_model_from_asset(self, event=None):
        if not hasattr(self, "model_list"):
            return
        sel = self.model_list.curselection()
        models = self.project.get("models", [])
        if not sel or sel[0] >= len(models):
            return
        model = models[sel[0]]
        obj = core.make_object(model)
        self.project.setdefault("objects", []).append(obj)
        self.selected_object_id = obj["id"]
        self.set_dirty(True)
        self.refresh_all()

    def show_quick_start(self):
        messagebox.showinfo(APP_NAME,
            "1. Import an OBJ model.\n"
            "2. Double-click it in MODELS or press Add Instance.\n"
            "3. Select geometry in the viewport or hierarchy and edit its transform.\n"
            "4. Use the large viewport for editing; the corner preview is the real GBC look.\n"
            "5. Configure Player, NPCs, and Window UI if needed.\n"
            "6. Open Performance before export.\n"
            "7. Export GBDK, then run compile.bat in the exported folder.\n\n"
            "The complete manual is GB3D_GUIDE.md / GB3D_GUIDE.pdf.", parent=self.root)

    def open_guide(self):
        base = Path(__file__).resolve().parent
        path = base / "GB3D_GUIDE.pdf"
        if not path.exists():
            path = base / "GB3D_GUIDE.md"
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            messagebox.showinfo(APP_NAME, f"Guide: {path}", parent=self.root)

    def show_about(self):
        messagebox.showinfo(APP_NAME,
            "GB3D Studio 1.0\n\n"
            "A tiny 3D game engine/editor targeting the Game Boy Color with GBDK.\n\n"
            "The large viewport is an editor renderer. The GBC inset uses the engine's actual 40x36 logical rendering rules, hardware palette approximation, sprites, and Window UI.",
            parent=self.root)

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def set_dirty(self, dirty=True):
        self.dirty = dirty
        name = self.project_path.name if self.project_path else self.project.get("name", "Untitled")
        self.root.title(f"{APP_NAME} - {name}{' *' if dirty else ''}")

    def current_object(self):
        if not self.selected_object_id:
            return None
        return next((o for o in self.project.get("objects", []) if o.get("id") == self.selected_object_id), None)

    def current_model(self):
        obj = self.current_object()
        if not obj:
            return None
        return core.model_by_id(self.project, obj.get("model_id", ""))

    def maybe_discard(self) -> bool:
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(APP_NAME, "Save changes to the current project?")
        if ans is None:
            return False
        if ans:
            return self.save_project()
        return True

    # ------------------------------------------------------------------
    # Project actions
    # ------------------------------------------------------------------
    def new_project(self):
        if not self.maybe_discard():
            return
        self.project = core.new_project()
        self.project_path = None
        self.selected_object_id = None
        self.editor_camera = dict(self.project.get("camera", {"x":0,"y":0,"z":-64,"yaw":0,"pitch":0}))
        self.set_dirty(False)
        self.refresh_all()

    def open_project(self):
        if not self.maybe_discard():
            return
        path = filedialog.askopenfilename(title="Open GB3D Project", filetypes=[("GB3D projects", "*.gb3d"), ("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.project = core.load_project(path)
            self.project_path = Path(path)
            self.selected_object_id = None
            self.editor_camera = dict(core.effective_camera(self.project))
            self.hardware_var.set(bool(self.project.get("settings", {}).get("hardware_palette_preview", True)))
            sky = int(self.project.get("settings", {}).get("sky_color", 0)) & 15
            self.sky_var.set(core.COLOR_NAMES[sky])
            self.set_dirty(False)
            self.refresh_all()
            self.status_var.set(f"Opened {Path(path).name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def save_project(self):
        if not self.project_path:
            return self.save_project_as()
        try:
            settings = self.project.setdefault("settings", {})
            settings["hardware_palette_preview"] = bool(self.hardware_var.get())
            try:
                settings["sky_color"] = core.COLOR_NAMES.index(self.sky_var.get())
            except ValueError:
                settings["sky_color"] = 0
            core.save_project(self.project, self.project_path)
            self.set_dirty(False)
            self.status_var.set(f"Saved {self.project_path.name}")
            return True
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return False

    def save_project_as(self):
        path = filedialog.asksaveasfilename(title="Save GB3D Project", defaultextension=".gb3d", filetypes=[("GB3D projects", "*.gb3d")])
        if not path:
            return False
        self.project_path = Path(path)
        self.project["name"] = self.project_path.stem
        return self.save_project()

    def import_obj(self):
        path = filedialog.askopenfilename(title="Import OBJ", filetypes=[("Wavefront OBJ", "*.obj"), ("All files", "*.*")])
        if not path:
            return
        size = simpledialog.askfloat(APP_NAME, "Largest model dimension in GB3D units:", initialvalue=32.0, minvalue=1.0, maxvalue=240.0)
        if size is None:
            return
        try:
            model = core.import_obj(path, size, default_color=1)
            # Avoid duplicate asset names while keeping generated C identifiers sane.
            existing = {m.get("name") for m in self.project.get("models", [])}
            base = model["name"]
            n = 2
            while model["name"] in existing:
                model["name"] = f"{base}_{n}"
                n += 1
            self.project["models"].append(model)
            obj = core.make_object(model)
            self.project["objects"].append(obj)
            self.selected_object_id = obj["id"]
            self.set_dirty(True)
            self.refresh_all()
            warning = ""
            if len(model["vertices"]) > core.MAX_MODEL_VERTICES or len(model["faces"]) > core.MAX_MODEL_FACES:
                warning = "  ⚠ exceeds current GBC per-model limits"
            self.status_var.set(f"Imported {Path(path).name}: {len(model['vertices'])} vertices, {len(model['faces'])} triangles{warning}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not import OBJ:\n{exc}")

    def add_instance(self):
        models = self.project.get("models", [])
        if not models:
            messagebox.showinfo(APP_NAME, "Import an OBJ first.")
            return
        # Use the selected object's model, otherwise the first asset.
        selected = self.current_model() or models[0]
        obj = core.make_object(selected, f"{selected['name']}_instance")
        self.project["objects"].append(obj)
        self.selected_object_id = obj["id"]
        self.set_dirty(True)
        self.refresh_all()

    def duplicate_selected(self):
        obj = self.current_object()
        if not obj:
            return
        dup = copy.deepcopy(obj)
        dup["id"] = core.new_id("obj")
        dup["name"] = obj.get("name", "Object") + " Copy"
        dup["position"][0] = int(dup["position"][0]) + 8
        self.project["objects"].append(dup)
        self.selected_object_id = dup["id"]
        self.set_dirty(True)
        self.refresh_all()

    def delete_selected(self):
        if not self.selected_object_id:
            return
        self.project["objects"] = [o for o in self.project.get("objects", []) if o.get("id") != self.selected_object_id]
        self.selected_object_id = None
        self.set_dirty(True)
        self.refresh_all()

    def recolor_selected_model(self):
        model = self.current_model()
        if not model:
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Recolor model")
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text=f"Set every triangle in {model['name']} to:", padding=8).pack(anchor="w")
        choice = tk.IntVar(value=int(model.get("default_color", 1)))
        grid = ttk.Frame(dialog, padding=8)
        grid.pack(fill="both", expand=True)
        for i, name in enumerate(core.COLOR_NAMES):
            rgb = core.LOGICAL_RGB[i]
            sw = tk.Canvas(grid, width=18, height=18, highlightthickness=1, highlightbackground="#555")
            sw.grid(row=i // 2, column=(i % 2) * 2, padx=(0, 4), pady=2)
            sw.create_rectangle(0, 0, 20, 20, fill="#%02x%02x%02x" % rgb, outline="")
            ttk.Radiobutton(grid, text=name, variable=choice, value=i).grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 10))

        def apply():
            c = choice.get() & 15
            model["default_color"] = c
            for face in model.get("faces", []):
                if len(face) < 4:
                    face.append(c)
                else:
                    face[3] = c
            self.set_dirty(True)
            self.schedule_render()
            dialog.destroy()

        ttk.Button(dialog, text="Apply", command=apply).pack(pady=(0, 8))

    def _on_sky_changed(self, event=None):
        try:
            sky = core.COLOR_NAMES.index(self.sky_var.get())
        except ValueError:
            sky = 0
        self.project.setdefault("settings", {})["sky_color"] = sky
        self.set_dirty(True)
        self.schedule_render()

    def _open_script_editor(self, title, initial_script, validate_callback, save_callback, context_label="Script"):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.geometry("900x650")
        dialog.minsize(700, 480)
        dialog.transient(self.root)

        top = ttk.Frame(dialog, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="GBScript 2  •  compiled to native C at export", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(
            top,
            text="Persistent variables • expressions • if/else • while • repeat • break/continue • input events",
            foreground="#666"
        ).pack(anchor="w", pady=(2, 0))

        body = ttk.Panedwindow(dialog, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        editor_frame = ttk.Frame(body)
        body.add(editor_frame, weight=4)
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.columnconfigure(1, weight=1)

        line_numbers = tk.Text(editor_frame, width=5, padx=4, takefocus=0, borderwidth=0,
                               background="#202020", foreground="#777", state="disabled",
                               font=("Consolas", 10), wrap="none")
        line_numbers.grid(row=0, column=0, sticky="ns")
        script_text = tk.Text(editor_frame, wrap="none", undo=True, font=("Consolas", 10))
        script_text.grid(row=0, column=1, sticky="nsew")
        sy = ttk.Scrollbar(editor_frame, orient="vertical")
        sx = ttk.Scrollbar(editor_frame, orient="horizontal", command=script_text.xview)
        sy.grid(row=0, column=2, sticky="ns")
        sx.grid(row=1, column=1, sticky="ew")
        script_text.configure(xscrollcommand=sx.set)

        def scroll_both(*args):
            script_text.yview(*args)
            line_numbers.yview(*args)
        sy.configure(command=scroll_both)

        def on_text_scroll(first, last):
            sy.set(first, last)
            try:
                line_numbers.yview_moveto(first)
            except tk.TclError:
                pass
        script_text.configure(yscrollcommand=on_text_scroll)

        ref = tk.Text(body, width=34, wrap="word", font=("Consolas", 9), padx=8, pady=8)
        body.add(ref, weight=1)
        ref.insert("1.0", (
            "GBSCRIPT 2 QUICK REFERENCE\n\n"
            "var speed = 1\nvar timer = 0\n\n"
            "@update\n"
            "timer += 1\n"
            "if timer >= 30\n"
            "    repeat 3\n"
            "        move speed, 0, 0\n"
            "    end\n"
            "    timer = 0\n"
            "else\n"
            "    # comments work too\n"
            "end\n\n"
            "LOOPS\nwhile condition\n    ...\nend\n\nrepeat expression\n    ...\nend\n\n"
            "EXPRESSIONS\n+ - * / %  < <= > >= == !=\nand / or / not\nabs(x) min(a,b) max(a,b) rand(n)\n\n"
            "BUILT-INS\nself_x self_y self_z\nplayer_x player_y player_z\ncamera_x camera_y camera_z\nframe true false\n\n"
            "COMMAND EXPRESSIONS\nFor expressions with spaces, separate command arguments with commas:\nmove speed * 2, 0, -1\n\n"
            "NPC\nnpc_move \"Bob\", 1, 0, 0\nnpc_set / npc_show / npc_hide / npc_toggle\n\n"
            "WINDOW UI\nui_set_value \"Score\", score\nui_add_value \"Score\", 1\nui_set_max \"Health\", 10\nui_show / ui_hide / ui_toggle\nwindow_show / window_hide / window_toggle\nwindow_move 0, 112\n\n"
            "Events: @start @update @a @b @select @start_button @up @down @left @right and *_held\n"
        ))
        ref.configure(state="disabled")

        script_text.insert("1.0", initial_script or "")
        status = tk.StringVar(value="")
        ttk.Label(dialog, textvariable=status, padding=(10, 2)).pack(fill="x")

        # Syntax colors are deliberately subtle so they work with platform themes.
        script_text.tag_configure("event", foreground="#a66ee8")
        script_text.tag_configure("keyword", foreground="#477bd1")
        script_text.tag_configure("command", foreground="#c77932")
        script_text.tag_configure("string", foreground="#579b58")
        script_text.tag_configure("comment", foreground="#7a7a7a")
        script_text.tag_configure("number", foreground="#3e99a8")
        script_text.tag_configure("var", foreground="#a05f93")

        live_after = [None]

        def update_line_numbers():
            count = int(script_text.index("end-1c").split(".")[0])
            line_numbers.configure(state="normal")
            line_numbers.delete("1.0", "end")
            line_numbers.insert("1.0", "\n".join(str(i) for i in range(1, count + 1)))
            line_numbers.configure(state="disabled")

        def highlight():
            text = script_text.get("1.0", "end-1c")
            for tag in ("event", "keyword", "command", "string", "comment", "number", "var"):
                script_text.tag_remove(tag, "1.0", "end")
            for ln, line in enumerate(text.splitlines(), 1):
                base = f"{ln}.0"
                comment = line.find("#")
                code = line if comment < 0 else line[:comment]
                if comment >= 0:
                    script_text.tag_add("comment", f"{ln}.{comment}", f"{ln}.end")
                stripped = code.strip()
                leading = len(code) - len(code.lstrip())
                if stripped.startswith("@"):
                    script_text.tag_add("event", f"{ln}.{leading}", f"{ln}.{leading + len(stripped)}")
                else:
                    m = re.match(r"\s*(var|if|else|while|repeat|end|break|continue)\b", code)
                    if m:
                        a, b = m.span(1); script_text.tag_add("keyword", f"{ln}.{a}", f"{ln}.{b}")
                    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", code)
                    if m and m.group(1).lower() in getattr(core, "_SCRIPT_COMMAND_SPECS", {}):
                        a,b=m.span(1); script_text.tag_add("command",f"{ln}.{a}",f"{ln}.{b}")
                    vm = re.match(r"\s*var\s+([A-Za-z_][A-Za-z0-9_]*)", code)
                    if vm:
                        a,b=vm.span(1); script_text.tag_add("var",f"{ln}.{a}",f"{ln}.{b}")
                for sm in re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', code):
                    script_text.tag_add("string", f"{ln}.{sm.start()}", f"{ln}.{sm.end()}")
                for nm in re.finditer(r"\b(?:0x[0-9A-Fa-f]+|\d+)\b", code):
                    script_text.tag_add("number", f"{ln}.{nm.start()}", f"{ln}.{nm.end()}")

        def validate_now(show_ok=True):
            candidate = script_text.get("1.0", "end-1c")
            errors = validate_callback(candidate)
            if errors:
                status.set("ERROR: " + errors[0])
                return False
            if show_ok:
                events, _ = core.parse_script(candidate)
                count = sum(len(v) for v in events.values())
                status.set(f"OK • {count} top-level statement(s) • {context_label}")
            return True

        def live_update(event=None):
            update_line_numbers(); highlight()
            if live_after[0]:
                try: dialog.after_cancel(live_after[0])
                except tk.TclError: pass
            live_after[0] = dialog.after(250, lambda: validate_now(False))

        script_text.bind("<KeyRelease>", live_update)
        script_text.bind("<MouseWheel>", lambda e: dialog.after_idle(lambda: line_numbers.yview_moveto(script_text.yview()[0])))

        buttons = ttk.Frame(dialog, padding=(10, 6, 10, 10))
        buttons.pack(fill="x")
        def save_script():
            if not validate_now(False):
                messagebox.showerror(APP_NAME, status.get(), parent=dialog)
                return
            save_callback(script_text.get("1.0", "end-1c"))
            self.set_dirty(True)
            self.refresh_all()
            dialog.destroy()
        ttk.Button(buttons, text="Validate", command=validate_now).pack(side="left")
        ttk.Button(buttons, text="Insert loop", command=lambda: script_text.insert("insert", "repeat 4\n    \nend\n")).pack(side="left", padx=4)
        ttk.Button(buttons, text="Insert if", command=lambda: script_text.insert("insert", "if true\n    \nelse\n    \nend\n")).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save script", command=save_script).pack(side="right", padx=6)

        update_line_numbers(); highlight(); validate_now(False)
        script_text.focus_set()

    def edit_selected_script(self):
        obj = self.current_object()
        if not obj:
            messagebox.showinfo(APP_NAME, "Select an object first.")
            return
        oi = self.project.get("objects", []).index(obj)
        self._open_script_editor(
            f"Object script - {obj.get('name','Object')}", obj.get("script", ""),
            lambda text: core.validate_object_script(self.project, oi, text),
            lambda text: obj.__setitem__("script", text),
            "3D object script"
        )

    def edit_npcs(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("NPCs")
        dialog.geometry("620x430")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=10); frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=("pos","sprite"), show="tree headings", height=12)
        tree.heading("#0", text="NPC"); tree.heading("pos", text="Position"); tree.heading("sprite", text="Sprite")
        tree.column("#0", width=150); tree.column("pos", width=170); tree.column("sprite", width=180)
        tree.pack(fill="both", expand=True)

        def refresh():
            for i in tree.get_children(): tree.delete(i)
            for i,npc in enumerate(self.project.get("npcs", [])):
                p=npc.get("position",[0,0,0])
                tree.insert("", "end", iid=str(i), text=npc.get("name",f"NPC {i}"), values=(f"{p[0]}, {p[1]}, {p[2]}", npc.get("sprite_source_name","Sprite")))
            mode=self.project.get("player",{}).get("sprite_mode","8x16")
            info.set(f"{len(self.project.get('npcs',[]))}/{core.MAX_NPCS} NPCs • global sprite mode: {mode} • NPCs use the same 32x32 hardware metasprite renderer as the player")

        def selected():
            sel=tree.selection()
            return int(sel[0]) if sel else None
        def add():
            if len(self.project.get("npcs",[])) >= core.MAX_NPCS:
                messagebox.showwarning(APP_NAME, f"V7 supports up to {core.MAX_NPCS} NPCs.", parent=dialog); return
            n=1; names={x.get('name') for x in self.project.get('npcs',[])}
            while f"NPC {n}" in names: n+=1
            self.project.setdefault("npcs",[]).append(core.make_npc(f"NPC {n}")); self.set_dirty(True); refresh(); self.schedule_render()
        def edit():
            i=selected()
            if i is not None: self._edit_npc_settings(i, refresh)
        def script():
            i=selected()
            if i is None: return
            npc=self.project["npcs"][i]
            self._open_script_editor(
                f"NPC script - {npc.get('name','NPC')}", npc.get("script",""),
                lambda text, ii=i: core.validate_npc_script(self.project, ii, text),
                lambda text, nn=npc: nn.__setitem__("script", text),
                "NPC billboard script"
            )
        def delete():
            i=selected()
            if i is None: return
            del self.project["npcs"][i]; self.set_dirty(True); refresh(); self.schedule_render()
        buttons=ttk.Frame(frame); buttons.pack(fill="x", pady=(8,0))
        ttk.Button(buttons,text="Add NPC",command=add).pack(side="left")
        ttk.Button(buttons,text="Edit...",command=edit).pack(side="left",padx=4)
        ttk.Button(buttons,text="Script...",command=script).pack(side="left")
        ttk.Button(buttons,text="Delete",command=delete).pack(side="left",padx=4)
        ttk.Button(buttons,text="Close",command=dialog.destroy).pack(side="right")
        info=tk.StringVar(); ttk.Label(frame,textvariable=info,foreground="#666",wraplength=580).pack(fill="x",pady=(8,0))
        tree.bind("<Double-1>",lambda e: edit())
        refresh()

    def _edit_npc_settings(self, index, refresh_parent=None):
        npc=self.project.get("npcs",[])[index]
        local_pixels=list(npc.get("sprite_pixels",core.default_player_sprite_pixels()))
        local_palette=copy.deepcopy(npc.get("sprite_palette",core.default_player_sprite_palette(10)))
        local_source=str(npc.get("sprite_source_name","Built-in critter"))
        dialog=tk.Toplevel(self.root); dialog.title(f"NPC - {npc.get('name','NPC')}"); dialog.geometry("560x600"); dialog.transient(self.root); dialog.grab_set()
        form=ttk.Frame(dialog,padding=12); form.pack(fill="both",expand=True); form.columnconfigure(1,weight=1)
        name=tk.StringVar(value=npc.get("name","NPC")); visible=tk.BooleanVar(value=bool(npc.get("visible",True)))
        vals=[tk.StringVar(value=str(v)) for v in npc.get("position",[0,0,0])[:3]]; height=tk.StringVar(value=str(npc.get("height",12)))
        row=0
        ttk.Label(form,text="Name").grid(row=row,column=0,sticky="w",pady=3); ttk.Entry(form,textvariable=name).grid(row=row,column=1,sticky="ew",pady=3); row+=1
        ttk.Checkbutton(form,text="Visible at start",variable=visible).grid(row=row,column=0,columnspan=2,sticky="w",pady=3); row+=1
        for lab,v in zip(("X","Y","Z"),vals):
            ttk.Label(form,text=f"Position {lab}").grid(row=row,column=0,sticky="w",pady=3); ttk.Entry(form,textvariable=v).grid(row=row,column=1,sticky="ew",pady=3); row+=1
        ttk.Label(form,text="Billboard height").grid(row=row,column=0,sticky="w",pady=3); ttk.Entry(form,textvariable=height).grid(row=row,column=1,sticky="ew",pady=3); row+=1
        ttk.Label(form,text="32×32 NPC image",font=("",10,"bold")).grid(row=row,column=0,columnspan=2,sticky="w",pady=(12,4)); row+=1
        source_var=tk.StringVar(value=local_source); ttk.Label(form,textvariable=source_var,foreground="#666").grid(row=row,column=0,columnspan=2,sticky="w"); row+=1
        preview=tk.Canvas(form,width=160,height=160,bg="#303030",highlightthickness=1); preview.grid(row=row,column=0,columnspan=2,pady=8); row+=1
        def redraw():
            preview.delete("all"); scale=5
            for yy in range(32):
                for xx in range(32):
                    pi=int(local_pixels[yy*32+xx])&3
                    if pi==0: continue
                    r,g,b=local_palette[pi]; c=f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                    preview.create_rectangle(xx*scale,yy*scale,(xx+1)*scale,(yy+1)*scale,fill=c,outline=c)
        def choose():
            nonlocal local_pixels,local_palette,local_source
            path=filedialog.askopenfilename(parent=dialog,title="Choose NPC 32x32 image",filetypes=[("PNG/images","*.png *.bmp *.gif *.jpg *.jpeg"),("All files","*.*")])
            if not path:return
            try:
                data=core.import_player_sprite_image(path); local_pixels=data["sprite_pixels"]; local_palette=data["sprite_palette"]; local_source=data["sprite_source_name"]; source_var.set(local_source); redraw()
            except Exception as exc: messagebox.showerror(APP_NAME,str(exc),parent=dialog)
        ttk.Button(form,text="Choose 32×32 image...",command=choose).grid(row=row,column=0,columnspan=2,sticky="ew",pady=4); row+=1
        ttk.Label(form,text="All characters share the Player sprite mode (8x16 recommended).",foreground="#666").grid(row=row,column=0,columnspan=2,sticky="w",pady=4); row+=1
        def save():
            try:
                pos=[int(v.get(),0) for v in vals]; h=int(height.get(),0)
                if not 1<=h<=64: raise ValueError("Height must be 1..64")
            except ValueError as exc: messagebox.showerror(APP_NAME,str(exc),parent=dialog); return
            npc.update({"name":name.get().strip() or "NPC","visible":visible.get(),"position":pos,"height":h,"sprite_pixels":local_pixels,"sprite_palette":local_palette,"sprite_source_name":local_source})
            self.set_dirty(True); self.schedule_render();
            if refresh_parent: refresh_parent()
            dialog.destroy()
        bar=ttk.Frame(form); bar.grid(row=row,column=0,columnspan=2,sticky="ew",pady=(12,0)); ttk.Button(bar,text="Cancel",command=dialog.destroy).pack(side="right"); ttk.Button(bar,text="Save",command=save).pack(side="right",padx=6)
        redraw()

    def edit_ui(self):
        dialog=tk.Toplevel(self.root); dialog.title("Hardware Window HUD / UI"); dialog.geometry("760x610"); dialog.transient(self.root)
        frame=ttk.Frame(dialog,padding=10); frame.pack(fill="both",expand=True)

        # Hardware Window settings. move_win() receives x+7 on the Game Boy;
        # the editor exposes actual on-screen pixel coordinates instead.
        settings=self.project.setdefault("settings",{})
        win=settings.setdefault("window_ui",{"enabled":False,"x":0,"y":112,"background":0})
        box=ttk.LabelFrame(frame,text="Game Boy Window layer",padding=8); box.pack(fill="x",pady=(0,8))
        enabled=tk.BooleanVar(value=bool(win.get("enabled",False)))
        wx=tk.StringVar(value=str(win.get("x",0))); wy=tk.StringVar(value=str(win.get("y",112)))
        bg=tk.StringVar(value=core.COLOR_NAMES[int(win.get("background",0))&15])
        ttk.Checkbutton(box,text="Show Window UI",variable=enabled).grid(row=0,column=0,columnspan=2,sticky="w",pady=2)
        ttk.Label(box,text="Screen X (pixels)").grid(row=1,column=0,sticky="w",pady=2); ttk.Entry(box,textvariable=wx,width=8).grid(row=1,column=1,sticky="w",pady=2)
        ttk.Label(box,text="Screen Y (pixels)").grid(row=1,column=2,sticky="w",padx=(14,0),pady=2); ttk.Entry(box,textvariable=wy,width=8).grid(row=1,column=3,sticky="w",pady=2)
        ttk.Label(box,text="Background").grid(row=2,column=0,sticky="w",pady=2); ttk.Combobox(box,textvariable=bg,values=core.COLOR_NAMES,state="readonly",width=15).grid(row=2,column=1,sticky="w",pady=2)
        ttk.Label(box,text="The Window starts here and extends to the bottom-right of the LCD. UI X/Y below are 8x8 tile cells relative to it.",foreground="#666").grid(row=3,column=0,columnspan=4,sticky="w",pady=(5,0))
        def save_window_settings():
            try:
                x=max(0,min(159,int(wx.get(),0))); y=max(0,min(143,int(wy.get(),0)))
            except ValueError:
                messagebox.showerror(APP_NAME,"Window X/Y must be integers",parent=dialog); return False
            win.update({"enabled":enabled.get(),"x":x,"y":y,"background":core.COLOR_NAMES.index(bg.get())})
            self.set_dirty(True); self.schedule_render(); return True
        ttk.Button(box,text="Apply Window settings",command=save_window_settings).grid(row=2,column=3,sticky="e")

        tree=ttk.Treeview(frame,columns=("type","pos","value"),show="tree headings",height=12)
        for col,title,width in (("#0","Name",150),("type","Type",80),("pos","Window cell",110),("value","Content / value",260)):
            tree.heading(col,text=title); tree.column(col,width=width)
        tree.pack(fill="both",expand=True)
        def refresh():
            for x in tree.get_children():tree.delete(x)
            for i,item in enumerate(self.project.get("ui",[])):
                content=item.get("text","") if item.get("type")=="text" else f"{item.get('value',0)}/{item.get('max',1)} width {item.get('width',1)} tiles"
                tree.insert("","end",iid=str(i),text=item.get("name",f"UI {i}"),values=(item.get("type","text"),f"{item.get('x',0)}, {item.get('y',0)}",content))
            info.set(f"{len(self.project.get('ui',[]))}/{core.MAX_UI_ELEMENTS} elements • native Window tilemap • rebuilt only when UI data changes")
            self.schedule_render()
        def sel():
            x=tree.selection(); return int(x[0]) if x else None
        def add_text():
            if len(self.project.setdefault("ui",[]))>=core.MAX_UI_ELEMENTS:return
            self.project["ui"].append(core.make_ui_text("Label")); win["enabled"]=True; enabled.set(True); self.set_dirty(True); refresh()
        def add_bar():
            if len(self.project.setdefault("ui",[]))>=core.MAX_UI_ELEMENTS:return
            self.project["ui"].append(core.make_ui_bar("Bar")); win["enabled"]=True; enabled.set(True); self.set_dirty(True); refresh()
        def edit():
            i=sel()
            if i is not None:self._edit_ui_element(i,refresh)
        def delete():
            i=sel()
            if i is not None: del self.project["ui"][i]; self.set_dirty(True); refresh()
        b=ttk.Frame(frame); b.pack(fill="x",pady=(8,0)); ttk.Button(b,text="Add text",command=add_text).pack(side="left"); ttk.Button(b,text="Add bar",command=add_bar).pack(side="left",padx=4); ttk.Button(b,text="Edit...",command=edit).pack(side="left"); ttk.Button(b,text="Delete",command=delete).pack(side="left",padx=4)
        def close(): save_window_settings(); dialog.destroy()
        ttk.Button(b,text="Close",command=close).pack(side="right")
        info=tk.StringVar(); ttk.Label(frame,textvariable=info,foreground="#666").pack(fill="x",pady=(8,0)); tree.bind("<Double-1>",lambda e:edit()); refresh()

    def _edit_ui_element(self,index,refresh_parent=None):
        item=self.project.get("ui",[])[index]; typ=item.get("type","text")
        d=tk.Toplevel(self.root); d.title(f"UI {typ} - {item.get('name','UI')}"); d.geometry("460x430"); d.transient(self.root); d.grab_set()
        f=ttk.Frame(d,padding=12); f.pack(fill="both",expand=True); f.columnconfigure(1,weight=1)
        name=tk.StringVar(value=item.get("name","UI")); x=tk.StringVar(value=str(item.get("x",1))); y=tk.StringVar(value=str(item.get("y",1))); visible=tk.BooleanVar(value=bool(item.get("visible",True))); color=tk.StringVar(value=core.COLOR_NAMES[int(item.get("color",1))&15])
        row=0
        def field(label,var):
            nonlocal row; ttk.Label(f,text=label).grid(row=row,column=0,sticky="w",pady=3); ttk.Entry(f,textvariable=var).grid(row=row,column=1,sticky="ew",pady=3); row+=1
        field("Name",name); field("X (Window tile 0..19)",x); field("Y (Window tile 0..17)",y)
        ttk.Checkbutton(f,text="Visible",variable=visible).grid(row=row,column=0,columnspan=2,sticky="w",pady=3); row+=1
        ttk.Label(f,text="Color").grid(row=row,column=0,sticky="w",pady=3); ttk.Combobox(f,textvariable=color,values=core.COLOR_NAMES,state="readonly").grid(row=row,column=1,sticky="ew",pady=3); row+=1
        if typ=="text":
            text=tk.StringVar(value=item.get("text","TEXT")); show=tk.BooleanVar(value=bool(item.get("show_value",False))); value=tk.StringVar(value=str(item.get("value",0)))
            field("Text (max 18)",text); ttk.Checkbutton(f,text="Append numeric value",variable=show).grid(row=row,column=0,columnspan=2,sticky="w",pady=3); row+=1; field("Initial value",value)
        else:
            width=tk.StringVar(value=str(item.get("width",10))); value=tk.StringVar(value=str(item.get("value",10))); maxv=tk.StringVar(value=str(item.get("max",10))); bg=tk.StringVar(value=core.COLOR_NAMES[int(item.get("bg_color",3))&15])
            field("Width",width); field("Initial value",value); field("Maximum",maxv); ttk.Label(f,text="Empty color").grid(row=row,column=0,sticky="w",pady=3); ttk.Combobox(f,textvariable=bg,values=core.COLOR_NAMES,state="readonly").grid(row=row,column=1,sticky="ew",pady=3); row+=1
        def save():
            try: xx=max(0,min(19,int(x.get(),0))); yy=max(0,min(17,int(y.get(),0))); vv=int(value.get(),0)
            except ValueError: messagebox.showerror(APP_NAME,"X/Y/value must be integers",parent=d); return
            item.update({"name":name.get().strip() or "UI","x":xx,"y":yy,"visible":visible.get(),"color":core.COLOR_NAMES.index(color.get()),"value":vv})
            if typ=="text": item.update({"text":text.get()[:18],"show_value":show.get()})
            else:
                try: ww=max(1,min(20,int(width.get(),0))); mm=max(1,int(maxv.get(),0))
                except ValueError: messagebox.showerror(APP_NAME,"Width/max must be integers",parent=d); return
                item.update({"width":ww,"max":mm,"bg_color":core.COLOR_NAMES.index(bg.get())})
            self.set_dirty(True); self.schedule_render();
            if refresh_parent:refresh_parent()
            d.destroy()
        bar=ttk.Frame(f); bar.grid(row=row,column=0,columnspan=2,sticky="ew",pady=(14,0)); ttk.Button(bar,text="Cancel",command=d.destroy).pack(side="right"); ttk.Button(bar,text="Save",command=save).pack(side="right",padx=6)

    def edit_player_settings(self):
        player = self.project.setdefault("player", copy.deepcopy(core.new_project()["player"]))
        # Normalize old V5-era projects in case this dialog is opened before a save.
        temp_project = {"format": core.FORMAT_NAME, "version": core.FORMAT_VERSION, "player": player}
        # The public helper used by the exporter also accepts these fields, but
        # keep local copies so Cancel truly cancels image changes.
        local_pixels = list(player.get("sprite_pixels", core.default_player_sprite_pixels()))
        local_palette = copy.deepcopy(player.get("sprite_palette", core.default_player_sprite_palette(int(player.get("color", 14)))))
        local_source = str(player.get("sprite_source_name", "Built-in critter"))

        dialog = tk.Toplevel(self.root)
        dialog.title("Built-in platformer player")
        dialog.geometry("650x780")
        dialog.minsize(560, 650)
        dialog.transient(self.root)
        dialog.grab_set()

        outer = ttk.Frame(dialog, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas_host = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas_host.yview)
        form = ttk.Frame(canvas_host)
        form.columnconfigure(1, weight=1)
        win = canvas_host.create_window((0, 0), window=form, anchor="nw")
        canvas_host.configure(yscrollcommand=scrollbar.set)
        canvas_host.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        form.bind("<Configure>", lambda e: canvas_host.configure(scrollregion=canvas_host.bbox("all")))
        canvas_host.bind("<Configure>", lambda e: canvas_host.itemconfigure(win, width=e.width))

        enabled = tk.BooleanVar(value=bool(player.get("enabled", False)))
        visible = tk.BooleanVar(value=bool(player.get("visible", True)))
        vars_num = {}

        ttk.Checkbutton(form, text="Enable built-in platformer player", variable=enabled).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Checkbutton(form, text="Player sprite visible", variable=visible).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        row = 2
        def add_num(label, key, value):
            nonlocal row
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=str(player.get(key, value)))
            vars_num[key] = v
            ttk.Entry(form, textvariable=v, width=14).grid(row=row, column=1, sticky="ew", pady=3)
            row += 1

        pos = player.get("position", [0, 0, 0])
        for axis, val in zip(("X", "Y", "Z"), pos):
            add_num(f"Spawn {axis}", f"pos_{axis.lower()}", val)

        ttk.Separator(form).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8); row += 1
        add_num("Move speed", "speed", 2)
        add_num("Jump speed", "jump_speed", 7)
        add_num("Gravity", "gravity", 1)
        add_num("Ground Y", "ground_y", 0)
        add_num("Half width", "half_width", 3)
        add_num("Height", "height", 12)

        ttk.Separator(form).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8); row += 1
        add_num("Camera distance", "camera_distance", 48)
        add_num("Camera height", "camera_height", 24)
        add_num("Camera pitch (-12..12)", "camera_pitch", -4)

        # ------------------------------------------------------------------
        # Custom 32x32 hardware sprite
        # ------------------------------------------------------------------
        ttk.Separator(form).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8); row += 1
        ttk.Label(form, text="Player appearance", font=("TkDefaultFont", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 5)); row += 1

        mode_labels = {
            "8x16": "8x16 sprites — 8 OAM entries (recommended)",
            "8x8": "8x8 sprites — 16 OAM entries",
        }
        reverse_modes = {v: k for k, v in mode_labels.items()}
        mode_var = tk.StringVar(value=mode_labels.get(str(player.get("sprite_mode", "8x16")), mode_labels["8x16"]))
        ttk.Label(form, text="Hardware layout").grid(row=row, column=0, sticky="w", pady=3)
        ttk.Combobox(form, textvariable=mode_var, values=list(mode_labels.values()), state="readonly").grid(row=row, column=1, sticky="ew", pady=3); row += 1

        source_var = tk.StringVar(value=local_source)
        ttk.Label(form, text="Image").grid(row=row, column=0, sticky="nw", pady=3)
        sprite_box = ttk.Frame(form)
        sprite_box.grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Label(sprite_box, textvariable=source_var).pack(side="left", fill="x", expand=True)
        row += 1

        preview = tk.Canvas(form, width=160, height=160, bg="#202020", highlightthickness=1, highlightbackground="#666")
        preview.grid(row=row, column=0, columnspan=2, pady=6)
        row += 1

        def draw_sprite_preview():
            preview.delete("all")
            cell = 5
            for y in range(32):
                for x in range(32):
                    idx = int(local_pixels[y * 32 + x]) & 3
                    if idx == 0:
                        # Checkerboard makes transparency obvious.
                        shade = "#303030" if ((x >> 2) ^ (y >> 2)) & 1 else "#242424"
                        preview.create_rectangle(x*cell, y*cell, (x+1)*cell, (y+1)*cell, fill=shade, outline=shade)
                    else:
                        r, g, b = local_palette[idx]
                        color = f"#{int(r):02x}{int(g):02x}{int(b):02x}"
                        preview.create_rectangle(x*cell, y*cell, (x+1)*cell, (y+1)*cell, fill=color, outline=color)

        def choose_sprite():
            nonlocal local_pixels, local_palette, local_source
            path = filedialog.askopenfilename(
                title="Choose 32x32 player image",
                filetypes=[("Images", "*.png *.bmp *.gif *.jpg *.jpeg"), ("All files", "*.*")],
                parent=dialog,
            )
            if not path:
                return
            try:
                result = core.import_player_sprite_image(path)
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=dialog)
                return
            local_pixels = list(result["sprite_pixels"])
            local_palette = copy.deepcopy(result["sprite_palette"])
            local_source = str(result.get("sprite_source_name", Path(path).name))
            source_var.set(local_source)
            draw_sprite_preview()

        def reset_sprite():
            nonlocal local_pixels, local_palette, local_source
            local_pixels = core.default_player_sprite_pixels()
            local_palette = core.default_player_sprite_palette(int(player.get("color", 14)))
            local_source = "Built-in critter"
            source_var.set(local_source)
            draw_sprite_preview()

        sprite_actions = ttk.Frame(form)
        sprite_actions.grid(row=row, column=0, columnspan=2, pady=(0, 8))
        ttk.Button(sprite_actions, text="Choose 32x32 image...", command=choose_sprite).pack(side="left", padx=4)
        ttk.Button(sprite_actions, text="Reset to built-in", command=reset_sprite).pack(side="left", padx=4)
        row += 1

        ttk.Label(
            form,
            text=(
                "PNG transparency becomes sprite color 0. Opaque pixels are automatically\n"
                "quantized to the GBC sprite's three visible colors. The image is embedded\n"
                "inside the .gb3d project, so the original PNG is not needed after import.\n\n"
                "8x16 uses 8 hardware sprites; 8x8 uses 16. Both are 4 sprites wide, so\n"
                "both stay under the GBC's 10-sprites-per-scanline limit by themselves."
            ),
            justify="left",
            foreground="#555",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10)); row += 1

        ttk.Separator(form).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8); row += 1
        ttk.Label(
            form,
            text=(
                "Runtime controls:\n"
                "D-Pad = move relative to camera\n"
                "Hold Select + D-Pad = orbit/look\n"
                "A = jump\n"
                "B / Start remain available to scripts\n\n"
                "Objects checked 'Solid platform / collider' use their model AABB for collision."
            ),
            justify="left",
            foreground="#555",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10)); row += 1

        buttons = ttk.Frame(form)
        buttons.grid(row=row, column=0, columnspan=2, sticky="ew")

        def save_player():
            try:
                newp = dict(player)
                newp["enabled"] = bool(enabled.get())
                newp["visible"] = bool(visible.get())
                newp["position"] = [
                    int(vars_num["pos_x"].get()),
                    int(vars_num["pos_y"].get()),
                    int(vars_num["pos_z"].get()),
                ]
                for key in ("speed", "jump_speed", "gravity", "ground_y", "half_width", "height", "camera_distance", "camera_height", "camera_pitch"):
                    newp[key] = int(vars_num[key].get())
                newp["sprite_mode"] = reverse_modes.get(mode_var.get(), "8x16")
                newp["sprite_pixels"] = list(local_pixels)
                newp["sprite_palette"] = copy.deepcopy(local_palette)
                newp["sprite_source_name"] = local_source
            except (ValueError, KeyError) as exc:
                messagebox.showerror(APP_NAME, f"Invalid player setting: {exc}", parent=dialog)
                return

            # Keep values inside runtime-friendly ranges.
            newp["speed"] = clamp(newp["speed"], 1, 8)
            newp["jump_speed"] = clamp(newp["jump_speed"], 1, 15)
            newp["gravity"] = clamp(newp["gravity"], 1, 8)
            newp["half_width"] = clamp(newp["half_width"], 1, 12)
            newp["height"] = clamp(newp["height"], 4, 32)
            newp["camera_distance"] = clamp(newp["camera_distance"], 12, 120)
            newp["camera_height"] = clamp(newp["camera_height"], -64, 96)
            newp["camera_pitch"] = clamp(newp["camera_pitch"], -12, 12)

            self.project["player"] = newp
            self.project.setdefault("camera", {})["pitch"] = newp["camera_pitch"]
            self.set_dirty(True)
            self.refresh_camera_label()
            self.schedule_render()
            dialog.destroy()

        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save player", command=save_player).pack(side="right", padx=6)
        draw_sprite_preview()

    def show_performance(self):
        report = core.performance_report(self.project)
        lines = [
            "PC-side scene estimate (the GBC profiler is authoritative):",
            "",
            f"Visible objects: {report['objects']}",
            f"Vertices potentially transformed: {report['vertices']}",
            f"Triangles potentially tested: {report['faces']}",
            f"Solid colliders: {report['solid']}",
            f"Runtime-rotating objects: {report['dynamic_rotation']}",
            f"NPCs: {report['npcs']}",
            f"UI elements: {report['ui']}",
            f"Rough cost score: {report['cost_score']}",
            "",
            "1.0 exported ROM profiler:",
            "Hold START + SELECT to toggle the overlay.",
            "F = total CPU work, R = 3D scene, U = VRAM upload,",
            "S = scripts, P = physics, T = triangles drawn.",
            "Values are hexadecimal timer ticks; in CGB double-speed 0x0010 ~= 1.95 ms.",
            "",
            "1.0 keeps the optimized runtime path and hardware Window UI. It culls whole objects",
            "before vertex transforms, buckets objects instead of sorting them,",
            "and rejects fully off-screen triangles before backface math.",
        ]
        if report['warnings']:
            lines += ["", "Warnings:"] + ["• " + w for w in report['warnings']]
        messagebox.showinfo(APP_NAME, "\n".join(lines), parent=self.root)

    def export_gbdk(self):
        errors = core.validate_for_export(self.project)
        if errors:
            messagebox.showerror(APP_NAME, "Cannot export yet:\n\n" + "\n".join(errors))
            return
        path = filedialog.askdirectory(title="Choose GBDK export folder")
        if not path:
            return
        try:
            template_dir = Path(__file__).resolve().parent / "templates"
            files = core.export_gbdk(self.project, path, template_dir)
            self.status_var.set(f"Exported GBDK project to {path}")
            messagebox.showinfo(APP_NAME, "Export complete!\n\n" + "\n".join(p.name for p in files))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    # ------------------------------------------------------------------
    # Inspector / hierarchy
    # ------------------------------------------------------------------
    def refresh_tree(self):
        selected = self.selected_object_id
        for item in self.scene_tree.get_children():
            self.scene_tree.delete(item)
        model_names = {m["id"]: m.get("name", "model") for m in self.project.get("models", [])}
        for obj in self.project.get("objects", []):
            iid = obj["id"]
            self.scene_tree.insert("", "end", iid=iid, text=obj.get("name", "Object"), values=(model_names.get(obj.get("model_id"), "?"),))
        if hasattr(self, "model_list"):
            self.model_list.delete(0, "end")
            for model in self.project.get("models", []):
                self.model_list.insert("end", f"{model.get('name','model')}   ({len(model.get('faces',[]))} tris)")
        if selected and self.scene_tree.exists(selected):
            self.scene_tree.selection_set(selected)
            self.scene_tree.see(selected)

    def refresh_inspector(self):
        self._inspector_lock = True
        try:
            model_names = [m.get("name", "model") for m in self.project.get("models", [])]
            self.model_combo["values"] = model_names
            obj = self.current_object()
            sky = int(self.project.get("settings", {}).get("sky_color", 0)) & 15
            self.sky_var.set(core.COLOR_NAMES[sky])
            if not obj:
                self.name_var.set("")
                self.model_var.set("")
                for v in self.pos_vars + self.rot_vars:
                    v.set("")
                self.visible_var.set(False)
                self.solid_var.set(False)
                self.script_status_label.configure(text="Script: none")
            else:
                model = core.model_by_id(self.project, obj.get("model_id", ""))
                self.name_var.set(obj.get("name", "Object"))
                self.model_var.set(model.get("name", "") if model else "")
                for i, value in enumerate(obj.get("position", [0, 0, 0])):
                    self.pos_vars[i].set(str(value))
                for i, value in enumerate(obj.get("rotation", [0, 0])):
                    self.rot_vars[i].set(str(value))
                self.visible_var.set(bool(obj.get("visible", True)))
                self.solid_var.set(bool(obj.get("solid", False)))
                events, errors = core.parse_script(obj.get("script", ""))
                command_count = sum(len(v) for v in events.values())
                if errors:
                    self.script_status_label.configure(text=f"Script: {len(errors)} error(s)")
                elif command_count:
                    self.script_status_label.configure(text=f"Script: {command_count} command(s)")
                else:
                    self.script_status_label.configure(text="Script: none")
        finally:
            self._inspector_lock = False
        self.refresh_camera_label()

    def _on_tree_select(self, event=None):
        sel = self.scene_tree.selection()
        self.selected_object_id = sel[0] if sel else None
        self.refresh_inspector()
        self.schedule_render()

    def apply_inspector(self):
        if self._inspector_lock:
            return
        obj = self.current_object()
        if not obj:
            return
        try:
            obj["name"] = self.name_var.get().strip() or "Object"
            model = next((m for m in self.project.get("models", []) if m.get("name") == self.model_var.get()), None)
            if model:
                obj["model_id"] = model["id"]
            obj["position"] = [int(v.get()) for v in self.pos_vars]
            obj["rotation"] = [int(self.rot_vars[0].get()) & 63, int(self.rot_vars[1].get()) & 63]
            obj["visible"] = bool(self.visible_var.get())
            obj["solid"] = bool(self.solid_var.get())
            self.set_dirty(True)
            self.refresh_tree()
            self.schedule_render()
        except ValueError:
            self.status_var.set("Inspector values must be integers.")

    # ------------------------------------------------------------------
    # Camera / viewport
    # ------------------------------------------------------------------
    def _editing_text(self):
        w = self.root.focus_get()
        return isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text, ttk.Spinbox))

    def _on_key(self, event):
        if self._editing_text():
            return
        key = event.keysym.lower()
        use_game = bool(self.game_camera_var.get())
        if use_game:
            cam = self.project["camera"]
            player = self.project.get("player", {})
            player_mode = bool(player.get("enabled", False))
            move = int(player.get("speed", 2)) if player_mode else 2
        else:
            cam = self.editor_camera
            player = {}
            player_mode = False
            move = 4

        if key in ("left", "right", "up", "down"):
            if key == "left": cam["yaw"] = (int(cam.get("yaw", 0)) - 1) & 63
            if key == "right": cam["yaw"] = (int(cam.get("yaw", 0)) + 1) & 63
            if key == "up":
                if player_mode:
                    player["camera_pitch"] = clamp(int(player.get("camera_pitch", -4)) + 1, -12, 12)
                    cam["pitch"] = player["camera_pitch"]
                else:
                    cam["pitch"] = clamp(int(cam.get("pitch", 0)) + 1, -12, 12)
            if key == "down":
                if player_mode:
                    player["camera_pitch"] = clamp(int(player.get("camera_pitch", -4)) - 1, -12, 12)
                    cam["pitch"] = player["camera_pitch"]
                else:
                    cam["pitch"] = clamp(int(cam.get("pitch", 0)) - 1, -12, 12)
        elif key in ("w", "s", "a", "d"):
            yaw = int(cam.get("yaw", 0))
            sn, cs = core.isin(yaw), core.icos(yaw)
            amt = move if key in ("w", "d") else -move
            target = player.setdefault("position", [0, 0, 0]) if player_mode else None
            if key in ("w", "s"):
                dx = (sn * amt) >> 7; dz = (cs * amt) >> 7
            else:
                dx = (cs * amt) >> 7; dz = -(sn * amt) >> 7
            if player_mode:
                target[0] = int(target[0]) + dx; target[2] = int(target[2]) + dz
            else:
                cam["x"] = int(cam.get("x", 0)) + dx; cam["z"] = int(cam.get("z", -64)) + dz
        elif key == "q":
            if player_mode: player["position"][1] = int(player["position"][1]) + 2
            else: cam["y"] = int(cam.get("y", 0)) + move
        elif key == "e":
            if player_mode: player["position"][1] = int(player["position"][1]) - 2
            else: cam["y"] = int(cam.get("y", 0)) - move
        else:
            return
        if use_game:
            self.set_dirty(True)
        self.refresh_camera_label()
        self.schedule_render()

    def reset_camera(self):
        fresh = {"x": 0, "y": 0, "z": -64, "yaw": 0, "pitch": 0}
        if self.game_camera_var.get():
            self.project["camera"] = fresh
            self.set_dirty(True)
        else:
            self.editor_camera = fresh
        self.refresh_camera_label()
        self.schedule_render()

    def frame_selected(self):
        obj = self.current_object()
        if not obj:
            return
        x, y, z = [int(v) for v in obj.get("position", [0, 0, 0])]
        framed = {"x": x, "y": y + 8, "z": z - 64, "yaw": 0, "pitch": 0}
        if self.game_camera_var.get():
            self.project["camera"] = framed
            self.set_dirty(True)
        else:
            self.editor_camera = framed
        self.refresh_camera_label()
        self.schedule_render()

    def refresh_camera_label(self):
        if self.game_camera_var.get():
            c = core.effective_camera(self.project)
            mode = "Game camera"
        else:
            c = self.editor_camera
            mode = "Editor camera (not exported)"
        self.cam_label.configure(text=(
            f"{mode}\n"
            f"pos: {int(c.get('x',0))}, {int(c.get('y',0))}, {int(c.get('z',-64))}\n"
            f"yaw: {int(c.get('yaw',0)) & 63}   pitch: {int(c.get('pitch',0))}"
        ))

    def schedule_render(self):
        if self._render_after:
            try:
                self.root.after_cancel(self._render_after)
            except tk.TclError:
                pass
        self._render_after = self.root.after(20, self.render_viewport)

    def render_viewport(self):
        self._render_after = None
        w = max(64, self.canvas.winfo_width())
        h = max(64, self.canvas.winfo_height())
        camera = core.effective_camera(self.project) if self.game_camera_var.get() else self.editor_camera
        try:
            primitives, editor_stats = core.editor_scene_primitives(
                self.project, w, h, camera=camera, wireframe=self.wireframe_var.get())
            logical, gbc_stats = core.render_scene(self.project, wireframe=self.wireframe_var.get())
            gbc_pixels = core.hardware_palette_rgb(logical) if self.hardware_var.get() else core.logical_rgb(logical)
        except Exception as exc:
            self.status_var.set(f"Render error: {exc}")
            return

        self.canvas.delete("all")
        sky_idx = int(self.project.get("settings", {}).get("sky_color", 0)) & 15
        sr, sg, sb = core.LOGICAL_RGB[sky_idx]
        # Make the editor sky a little less harsh without pretending it is GBC output.
        editor_sky = (int(sr * 0.55 + 24), int(sg * 0.55 + 27), int(sb * 0.55 + 31))
        editor_sky = tuple(max(0, min(255, c)) for c in editor_sky)
        self.canvas.create_rectangle(0, 0, w, h, fill="#%02x%02x%02x" % editor_sky, outline="", tags="editor_bg")

        # World grid is an editor aid only.
        if self.grid_var.get():
            extent = 160
            step = 16
            for n in range(-extent, extent + 1, step):
                for a, b, color, width_px in [
                    ((-extent, 0, n), (extent, 0, n), "#414750", 1),
                    ((n, 0, -extent), (n, 0, extent), "#414750", 1),
                ]:
                    pa = core.editor_project_world(a, w, h, camera)
                    pb = core.editor_project_world(b, w, h, camera)
                    if pa is not None and pb is not None:
                        self.canvas.create_line(pa[0], pa[1], pb[0], pb[1], fill=color, width=width_px, tags="grid")
            # X/Z axes.
            for a, b, color in [((-extent,0,0),(extent,0,0),"#8e5151"), ((0,0,-extent),(0,0,extent),"#4f648f")]:
                pa = core.editor_project_world(a, w, h, camera); pb = core.editor_project_world(b, w, h, camera)
                if pa is not None and pb is not None:
                    self.canvas.create_line(pa[0], pa[1], pb[0], pb[1], fill=color, width=2, tags="grid")

        selected = self.selected_object_id
        for prim in primitives:
            oid = str(prim.get("object_id", ""))
            tags = ("scene_object", f"obj:{oid}")
            if prim["kind"] == "triangle":
                pts = prim["points"]
                flat = [coord for p in pts for coord in p]
                color = "#%02x%02x%02x" % tuple(prim["color"])
                outline = "#ffd166" if oid == selected else "#252a31"
                ow = 2 if oid == selected else 1
                self.canvas.create_polygon(*flat, fill=color, outline=outline, width=ow, tags=tags)
            else:
                pa, pb = prim["points"]
                color = "#ffd166" if oid == selected else "#d8dee9"
                self.canvas.create_line(pa[0], pa[1], pb[0], pb[1], fill=color, width=2 if oid == selected else 1, tags=tags)

        # Editor-only player/NPC markers. The inset below draws the actual OAM art.
        player = self.project.get("player", {})
        if player.get("enabled", False) and player.get("visible", True):
            pos = (player.get("position") or [0,0,0])[:3]
            pp = core.editor_project_world((int(pos[0]), int(pos[1]) + int(player.get("height",12))//2, int(pos[2])), w, h, camera)
            if pp:
                self.canvas.create_oval(pp[0]-7, pp[1]-7, pp[0]+7, pp[1]+7, fill="#f4d35e", outline="#111", width=2, tags="marker")
                self.canvas.create_text(pp[0]+11, pp[1]-11, text="PLAYER", anchor="sw", fill="#f2f2f2", tags="marker")
        for npc in self.project.get("npcs", []):
            if not npc.get("visible", True):
                continue
            pos = (npc.get("position") or [0,0,0])[:3]
            pp = core.editor_project_world((int(pos[0]), int(pos[1]) + int(npc.get("height",12))//2, int(pos[2])), w, h, camera)
            if pp:
                self.canvas.create_rectangle(pp[0]-5, pp[1]-5, pp[0]+5, pp[1]+5, fill="#80cbc4", outline="#111", tags="marker")
                self.canvas.create_text(pp[0]+9, pp[1]-9, text=str(npc.get("name","NPC")), anchor="sw", fill="#e8ecef", tags="marker")

        if self.gbc_inset_var.get():
            self._draw_gbc_inset(gbc_pixels, w, h)

        report = core.performance_report(self.project)
        score = int(report.get("cost_score", 0))
        grade = "LIGHT" if score < 1200 else ("MEDIUM" if score < 2400 else "HEAVY")
        self.perf_status_var.set(f"GBC estimate: {grade}  •  cost {score}  •  {gbc_stats.get('drawn',0)} primitives")
        self.stats_label.configure(text=(
            f"Editor viewport\n"
            f"  Vertices: {editor_stats['vertices']}\n"
            f"  Faces tested: {editor_stats['faces']}\n"
            f"  Visible: {editor_stats['drawn']}\n\n"
            f"GBC preview\n"
            f"  Objects: {gbc_stats['objects']}\n"
            f"  Vertices: {gbc_stats['vertices']}\n"
            f"  Triangles: {gbc_stats['faces']}\n"
            f"  Drawn: {gbc_stats['drawn']}\n"
            f"  NPCs: {gbc_stats.get('npcs',0)}  UI: {gbc_stats.get('ui',0)}"
        ))

    def _draw_gbc_inset(self, pixels, canvas_w, canvas_h):
        # One inset pixel equals one real 160x144 LCD pixel whenever space permits.
        scale = 1.0 if canvas_w >= 560 and canvas_h >= 360 else 0.75
        pw, ph = 160 * scale, 144 * scale
        ox = canvas_w - pw - 18
        oy = 34
        self.canvas.create_rectangle(ox-7, oy-25, ox+pw+7, oy+ph+7, fill="#0c0e11", outline="#777f89", width=1, tags="gbc_preview")
        self.canvas.create_text(ox, oy-17, text="GBC 160 x 144", anchor="w", fill="#e6e8eb", tags="gbc_preview")
        self.canvas.create_text(ox+pw, oy-17, text="hardware preview", anchor="e", fill="#8e959f", tags="gbc_preview")

        # 40x36 logical background; each logical pixel is 4x4 LCD pixels.
        unit = 4.0 * scale
        for y, row in enumerate(pixels):
            x = 0
            while x < core.FB_WIDTH:
                rgb = row[x]; x2 = x + 1
                while x2 < core.FB_WIDTH and row[x2] == rgb:
                    x2 += 1
                color = "#%02x%02x%02x" % rgb
                self.canvas.create_rectangle(ox+x*unit, oy+y*unit, ox+x2*unit, oy+(y+1)*unit,
                                             fill=color, outline=color, tags="gbc_preview")
                x = x2

        # Hardware sprites: merge horizontal runs so four NPCs do not create
        # thousands of Tk items every refresh.
        def draw_sprite(character, logical_x, logical_y):
            spr = character.get("sprite_pixels", core.default_player_sprite_pixels())
            pal = character.get("sprite_palette", core.default_player_sprite_palette(14))
            left = ox + logical_x * unit - 16 * scale
            top = oy + logical_y * unit - 16 * scale
            for sy in range(32):
                sx = 0
                while sx < 32:
                    idx = int(spr[sy*32+sx]) & 3
                    if idx == 0:
                        sx += 1; continue
                    sx2 = sx + 1
                    while sx2 < 32 and (int(spr[sy*32+sx2]) & 3) == idx:
                        sx2 += 1
                    r,g,b = pal[idx]; color = "#%02x%02x%02x" % (int(r),int(g),int(b))
                    self.canvas.create_rectangle(left+sx*scale, top+sy*scale, left+sx2*scale, top+(sy+1)*scale,
                                                 fill=color, outline=color, tags="gbc_preview")
                    sx = sx2

        for npc, nx, ny in core.preview_npcs(self.project):
            draw_sprite(npc, nx, ny)
        pp = core.preview_player(self.project)
        if pp is not None:
            draw_sprite(self.project.get("player", {}), pp[0], pp[1])

        # Hardware Window UI in physical-pixel coordinates.
        win = self.project.get("settings", {}).get("window_ui", {})
        if win.get("enabled", False):
            sx = max(0, min(159, int(win.get("x", 0))))
            sy = max(0, min(143, int(win.get("y", 112))))
            bg = core.LOGICAL_RGB[int(win.get("background", 0)) & 15]
            bg_hex = "#%02x%02x%02x" % bg
            left = ox + sx*scale; top = oy + sy*scale
            self.canvas.create_rectangle(left, top, ox+160*scale, oy+144*scale, fill=bg_hex, outline=bg_hex, tags="gbc_preview")

            def glyph(tx, ty, ch, logical_color):
                rows = core._FONT3X5.get(str(ch).upper(), core._FONT3X5["?"])
                col = "#%02x%02x%02x" % core.LOGICAL_RGB[int(logical_color)&15]
                bx = left + tx*8*scale; by = top + ty*8*scale
                for gy,bits in enumerate(rows):
                    for gx in range(3):
                        if bits & (1 << (2-gx)):
                            x0=bx+(gx+2)*scale; y0=by+(gy+1)*scale
                            self.canvas.create_rectangle(x0,y0,x0+scale,y0+scale,fill=col,outline=col,tags="gbc_preview")

            for item in self.project.get("ui", []):
                if not item.get("visible", True):
                    continue
                tx=max(0,min(19,int(item.get("x",0)))); ty=max(0,min(17,int(item.get("y",0))))
                if item.get("type","text") == "text":
                    txt=str(item.get("text",""))
                    if item.get("show_value",False):
                        txt += (" " if txt else "") + str(int(item.get("value",0)))
                    for ci,ch in enumerate(txt):
                        if tx+ci >= 20: break
                        glyph(tx+ci,ty,ch,item.get("color",1))
                else:
                    width=max(1,min(20,int(item.get("width",10))))
                    maxv=max(1,int(item.get("max",10))); value=max(0,min(maxv,int(item.get("value",0))))
                    filled=(value*width)//maxv
                    for bi in range(width):
                        if tx+bi >= 20: break
                        ci=int(item.get("color",4) if bi < filled else item.get("bg_color",3))&15
                        c="#%02x%02x%02x" % core.LOGICAL_RGB[ci]
                        x0=left+(tx+bi)*8*scale; y0=top+ty*8*scale
                        self.canvas.create_rectangle(x0,y0,x0+8*scale,y0+8*scale,fill=c,outline=c,tags="gbc_preview")
        self.canvas.create_rectangle(ox, oy, ox+pw, oy+ph, outline="#c1c6cc", width=1, tags="gbc_preview")

    def refresh_all(self):
        self.refresh_tree()
        self.refresh_inspector()
        self.schedule_render()

    def on_close(self):
        if self.maybe_discard():
            self.root.destroy()


def self_test(base: Path) -> int:
    # Headless smoke test: import sample OBJ, render, save/load, and export.
    sample = base / "sample" / "model.obj"
    if not sample.exists():
        print("self-test: sample OBJ missing", file=sys.stderr)
        return 2
    project = core.new_project()
    model = core.import_obj(sample, 32)
    project["models"].append(model)
    obj = core.make_object(model)
    obj["solid"] = True
    obj["script"] = "@update\nspin 0 1\n@a\nmove 0 0 2\n@b\nset_sky Blue\n@start_button\nplayer_jump"
    project["objects"].append(obj)
    project["settings"]["sky_color"] = 8
    project["player"]["enabled"] = True
    project["player"]["position"] = [0, 20, -20]
    project["player"]["sprite_mode"] = "8x16"
    npc = core.make_npc("Walker")
    npc["position"] = [12, 0, 12]
    npc["script"] = "var t = 0\n@update\nt += 1\nif player_x > self_x\nmove 1, 0, 0\nelse\nmove -1, 0, 0\nend\nif t >= 10\nt = 0\nend"
    project["npcs"].append(npc)
    score = core.make_ui_text("Score")
    score["text"] = "SCORE"
    score["show_value"] = True
    project["ui"].append(score)
    project["settings"]["window_ui"].update({"enabled": True, "x": 0, "y": 112, "background": 0})
    buf, stats = core.render_scene(project)
    assert len(buf) == core.FB_HEIGHT and len(buf[0]) == core.FB_WIDTH
    editor_prims, editor_stats = core.editor_scene_primitives(project, 800, 600, camera=core.effective_camera(project))
    assert editor_stats["vertices"] >= 1
    assert isinstance(editor_prims, list)
    temp = base / "_selftest_export"
    if temp.exists():
        import shutil
        shutil.rmtree(temp)
    core.export_gbdk(project, temp, base / "templates")
    assert (temp / "main.c").exists()
    assert (temp / "scene_gb3d.h").exists()
    assert (temp / "gb3d_render.s").exists()
    scene_text = (temp / "scene_gb3d.h").read_text(encoding="utf-8")
    assert "scene_script_update" in scene_text
    assert "SCENE_SKY_COLOR 8u" in scene_text
    assert "SCENE_PLAYER_ENABLED 1u" in scene_text
    assert "SCENE_PLAYER_SPRITE_8X16 1u" in scene_text
    assert "scene_player_sprite_tiles[256]" in scene_text
    assert "gb3d_player_jump" in scene_text
    assert "SCENE_NPC_COUNT 1u" in scene_text
    assert "SCENE_UI_COUNT 1u" in scene_text
    assert "SCENE_WINDOW_ENABLED 1u" in scene_text
    assert "SCENE_WINDOW_Y 112u" in scene_text
    assert "gbv_npc_0_t" in scene_text
    assert "gb3d_player_get_x()" in scene_text
    assert "player.x" not in scene_text
    assert ", 1u}," in scene_text  # solid object flag
    print("self-test OK", stats)
    return 0


def main():
    parser = argparse.ArgumentParser(description="GB3D Studio - tiny Game Boy Color 3D scene editor")
    parser.add_argument("project", nargs="?", help="Optional .gb3d project to open")
    parser.add_argument("--self-test", action="store_true", help="Run a headless import/render/export smoke test")
    args = parser.parse_args()
    base = Path(__file__).resolve().parent
    if args.self_test:
        raise SystemExit(self_test(base))

    root = tk.Tk()
    app = GB3DStudio(root)
    if args.project:
        try:
            app.project = core.load_project(args.project)
            app.project_path = Path(args.project)
            app.editor_camera = dict(core.effective_camera(app.project))
            app.set_dirty(False)
            app.refresh_all()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))
    root.mainloop()


if __name__ == "__main__":
    main()
