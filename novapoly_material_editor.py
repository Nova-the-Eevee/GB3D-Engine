"""Tk material editor for NovaPoly .gb3d assets, with textured 3D/UV previews."""
from __future__ import annotations
import copy
import math
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,messagebox,simpledialog,ttk

import gb3d_core as core
from novapoly_textures import (ensure_material_data, material_names, faces_for_material,
                               assign_image,remove_texture,generate_box_uv,uv_is_usable)


def texture_rgb(tex):
    """Decode the exact RGB555 palette used by the GBA (ignoring lighting)."""
    from PIL import Image
    w,h=int(tex['width']),int(tex['height'])
    im=Image.new('RGB',(w,h))
    palette=[((int(c)&31)*255//31,((int(c)>>5)&31)*255//31,((int(c)>>10)&31)*255//31) for c in tex['palette']]
    im.putdata([palette[int(i)] for i in tex['pixels']])
    return im


def render_model_preview(model, selected_faces=(), width=360,height=260):
    """Affine, nearest sampled painter preview. Not a cycle-accurate GBA render."""
    from PIL import Image,ImageDraw
    proj=core.new_project()
    proj['models']=[model]
    proj['objects']=[core.make_object(model)]
    proj['camera']={'x':0,'y':0,'z':-max(55,max((abs(int(c)) for v in model.get('vertices',[]) for c in v),default=20)*4),'yaw':0,'pitch':0}
    prims,_=core.editor_scene_primitives(proj,width,height,camera=proj['camera'])
    out=Image.new('RGB',(width,height),(29,35,46));draw=ImageDraw.Draw(out)
    selected=set(selected_faces)
    tex_images=[texture_rgb(tex) for tex in model.get('textures',[])]
    mapping=model.get('face_textures',[]); uv_list=model.get('face_uvs',[])
    draw.rectangle((2,2,width-3,height-3),outline=(70,78,94))
    for prim in prims:
        if prim['kind']!='triangle':continue
        fi=prim['face_index'];pts=prim['points']
        tex_i=mapping[fi] if fi<len(mapping) else -1
        uv=uv_list[fi] if fi<len(uv_list) else None
        if 0<=tex_i<len(tex_images) and uv_is_usable(uv):
            tex=tex_images[tex_i]; tw,th=tex.size
            (x0,y0),(x1,y1),(x2,y2)=pts
            denom=(y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
            if abs(denom)>1e-7:
                bounds=(max(0,math.floor(min(x0,x1,x2))),min(width,math.ceil(max(x0,x1,x2))+1),
                        max(0,math.floor(min(y0,y1,y2))),min(height,math.ceil(max(y0,y1,y2))+1))
                pixels=out.load();source=tex.load()
                for y in range(bounds[2],bounds[3]):
                    for x in range(bounds[0],bounds[1]):
                        px=x+0.5;py=y+0.5
                        a=((y1-y2)*(px-x2)+(x2-x1)*(py-y2))/denom
                        b=((y2-y0)*(px-x2)+(x0-x2)*(py-y2))/denom
                        c=1-a-b
                        if a>=-0.0001 and b>=-0.0001 and c>=-0.0001:
                            u=a*float(uv[0][0])+b*float(uv[1][0])+c*float(uv[2][0])
                            v=a*float(uv[0][1])+b*float(uv[1][1])+c*float(uv[2][1])
                            pixels[x,y]=source[math.floor(u*tw)%tw,math.floor((1-v)*th)%th]
        else:
            draw.polygon(pts,fill=tuple(prim['color']))
        draw.line([*pts,pts[0]],fill=(248,214,87) if fi in selected else (30,32,36),width=2 if fi in selected else 1)
    return out,prims


class MaterialEditor:
    def __init__(self,studio):
        self.studio=studio;self.root=studio.root
        assets=studio.project.get('models',[])
        selected=None
        if hasattr(studio,'model_list') and studio.model_list.curselection():
            idx=studio.model_list.curselection()[0]
            if idx<len(assets):selected=assets[idx]
        self.model=selected or studio.current_model() or (assets[0] if assets else None)
        if self.model is None:
            messagebox.showinfo('NovaPoly Materials','Import an OBJ first.',parent=self.root);return
        ensure_material_data(self.model)
        self.window=tk.Toplevel(self.root);self.window.title('NovaPoly — Materials & UVs')
        self.window.geometry('1000x720');self.window.minsize(780,600);self.window.transient(self.root)
        self.max_dim=tk.StringVar(value=str(studio.texture_max_dimension))
        self.max_colors=tk.StringVar(value=str(studio.texture_max_colors))
        self.model_var=tk.StringVar(value=self.model.get('name','model'))
        self.status=tk.StringVar(value='Select a material or triangles, then load an image.')
        self.preview_3d_image=None;self.preview_uv_image=None;self._last_prims=[]
        self._build()
        self.refresh()

    def _build(self):
        top=ttk.Frame(self.window,padding=8);top.pack(fill='x')
        ttk.Label(top,text='Model').pack(side='left')
        self.model_combo=ttk.Combobox(top,state='readonly',width=24,textvariable=self.model_var,
            values=[m.get('name','model') for m in self.studio.project['models']]);self.model_combo.pack(side='left',padx=7)
        self.model_combo.bind('<<ComboboxSelected>>',self.change_model)
        ttk.Label(top,text='Max image size').pack(side='left',padx=(12,4))
        ttk.Combobox(top,state='readonly',width=4,values=['16','32','64'],textvariable=self.max_dim).pack(side='left')
        ttk.Label(top,text='Colors').pack(side='left',padx=(12,4))
        ttk.Combobox(top,state='readonly',width=4,values=['16','32','64'],textvariable=self.max_colors).pack(side='left')
        ttk.Label(self.window,text='Select a material to work on all its triangles, or select individual faces below.',
                  padding=(10,0)).pack(anchor='w')
        body=ttk.Panedwindow(self.window,orient='horizontal');body.pack(fill='both',expand=True,padx=8,pady=6)
        left=ttk.Frame(body,padding=6);body.add(left,weight=1)
        ttk.Label(left,text='MATERIAL GROUPS').pack(anchor='w')
        self.mat_list=tk.Listbox(left,height=4,exportselection=False);self.mat_list.pack(fill='x',pady=(4,10))
        self.mat_list.bind('<<ListboxSelect>>',self.on_material)
        ttk.Label(left,text='TRIANGLES  (Ctrl/Shift to multi-select)').pack(anchor='w')
        faces_frame=ttk.Frame(left);faces_frame.pack(fill='both',expand=False,pady=(4,7))
        self.face_list=tk.Listbox(faces_frame,selectmode='extended',exportselection=False,height=7)
        scrollbar=ttk.Scrollbar(faces_frame,orient='vertical',command=self.face_list.yview)
        self.face_list.configure(yscrollcommand=scrollbar.set)
        self.face_list.pack(side='left',fill='both',expand=True)
        scrollbar.pack(side='right',fill='y')
        self.face_list.bind('<<ListboxSelect>>',self.on_faces)
        ttk.Button(left,text='Load PNG for selected faces',command=self.load_image).pack(fill='x',pady=2)
        ttk.Button(left,text='Generate box UVs (selected faces)',command=self.make_uv).pack(fill='x',pady=2)
        ttk.Button(left,text='Remove texture (selected faces)',command=self.clear_texture).pack(fill='x',pady=2)
        ttk.Button(left,text='Rename material for selected faces',command=self.rename_material).pack(fill='x',pady=2)
        ttk.Label(left,text='Existing UVs are preserved. Missing UVs get box mapping.',wraplength=260,justify='left').pack(anchor='w',pady=5)
        right=ttk.Frame(body,padding=6);body.add(right,weight=3)
        ttk.Label(right,text='3D PREVIEW  (click a triangle to select it)').pack(anchor='w')
        self.preview3d=tk.Canvas(right,width=360,height=260,bg='#1d2330',highlightthickness=0)
        self.preview3d.pack(fill='both',expand=True,pady=(4,8));self.preview3d.bind('<Button-1>',self.pick_face)
        uv_row=ttk.Frame(right);uv_row.pack(fill='x')
        ttk.Label(uv_row,text='TEXTURE + UV PREVIEW').pack(side='left')
        self.selected_label=ttk.Label(uv_row,text='');self.selected_label.pack(side='right')
        self.previewuv=tk.Label(right,text='No image assigned')
        self.previewuv.pack(fill='x',pady=(4,0))
        ttk.Label(self.window,textvariable=self.status,padding=(8,5),wraplength=950).pack(fill='x')
        ttk.Button(self.window,text='Close',command=self.window.destroy).pack(anchor='e',padx=10,pady=(0,9))

    def change_model(self,event=None):
        idx=self.model_combo.current()
        models=self.studio.project['models']
        if 0<=idx<len(models):
            self.model=models[idx];ensure_material_data(self.model);self.refresh()

    def selected_faces(self):
        return [int(i) for i in self.face_list.curselection()]

    def refresh(self,focus_faces=None):
        ensure_material_data(self.model)
        chosen=focus_faces if focus_faces is not None else self.selected_faces()
        self.mat_list.delete(0,'end')
        for mat in material_names(self.model):
            n=len(faces_for_material(self.model,mat))
            self.mat_list.insert('end',f'{mat}  ({n} tris)')
        self.face_list.delete(0,'end')
        maps=self.model['face_textures']
        for i,mat in enumerate(self.model['face_materials']):
            t=maps[i];label=(self.model['textures'][t]['name'] if 0<=t<len(self.model['textures']) else 'NO TEXTURE')
            self.face_list.insert('end',f'{i:>4}  {mat}   [{label}]')
        for fi in chosen:
            if 0<=fi<self.face_list.size():self.face_list.selection_set(fi)
        self.draw_previews()

    def on_material(self,event=None):
        sel=self.mat_list.curselection()
        if not sel:return
        name=material_names(self.model)[sel[0]]
        self.face_list.selection_clear(0,'end')
        for i in faces_for_material(self.model,name):self.face_list.selection_set(i)
        self.face_list.see(faces_for_material(self.model,name)[0]);self.draw_previews()

    def on_faces(self,event=None):
        self.draw_previews()

    def _choose(self):
        indices=self.selected_faces()
        if not indices:messagebox.showinfo('NovaPoly Materials','Select a material or triangle first.',parent=self.window)
        return indices

    def _changed(self):
        self.studio.texture_max_dimension=int(self.max_dim.get())
        self.studio.texture_max_colors=int(self.max_colors.get())
        self.studio.set_dirty(True)
        self.studio.schedule_render()

    def load_image(self):
        indices=self._choose()
        if not indices:return
        path=filedialog.askopenfilename(parent=self.window,title='Assign image to selected triangles',
            filetypes=[('Image files','*.png *.jpg *.jpeg *.bmp *.gif *.webp'),('All files','*.*')])
        if not path:return
        try:
            result=assign_image(self.model,path,indices,int(self.max_dim.get()),int(self.max_colors.get()))
            self._changed();self.refresh(indices)
            self.status.set(f"Assigned {Path(path).name} to {result['faces']} faces; generated UVs for {result['generated_uv']} face(s).")
        except Exception as exc:messagebox.showerror('NovaPoly Materials',str(exc),parent=self.window)

    def make_uv(self):
        indices=self._choose()
        if not indices:return
        n=generate_box_uv(self.model,indices,overwrite=True)
        self._changed();self.refresh(indices);self.status.set(f'Generated new per-triangle box UVs on {n} faces.')

    def clear_texture(self):
        indices=self._choose()
        if not indices:return
        remove_texture(self.model,indices)
        self._changed();self.refresh(indices);self.status.set(f'Removed texture assignment from {len(indices)} faces.')

    def rename_material(self):
        indices=self._choose()
        if not indices:return
        name=simpledialog.askstring('Material group','New group name for selected faces:',parent=self.window)
        if not name or not name.strip():return
        for fi in indices:self.model['face_materials'][fi]=name.strip()
        self._changed();self.refresh(indices);self.status.set('Material group updated.')

    def pick_face(self,event):
        x=float(event.x);y=float(event.y)
        # Primitives are painter sorted far-to-near; the last hit is topmost.
        for prim in reversed(self._last_prims):
            if prim['kind']!='triangle':continue
            a,b,c=prim['points']
            d=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(d)<1e-7:continue
            u=((b[1]-c[1])*(x-c[0])+(c[0]-b[0])*(y-c[1]))/d
            v=((c[1]-a[1])*(x-c[0])+(a[0]-c[0])*(y-c[1]))/d
            if u>=0 and v>=0 and u+v<=1:
                i=prim['face_index'];self.face_list.selection_clear(0,'end')
                self.face_list.selection_set(i);self.face_list.see(i);self.draw_previews();return

    def draw_previews(self):
        from PIL import Image,ImageDraw,ImageTk
        selected=self.selected_faces()
        self.selected_label.configure(text=f'{len(selected)} selected')
        w=max(320,self.preview3d.winfo_width());h=max(210,self.preview3d.winfo_height())
        w=min(w,550);h=min(h,320)
        scene,prims=render_model_preview(self.model,selected,w,h)
        self._last_prims=prims
        self.preview_3d_image=ImageTk.PhotoImage(scene,master=self.window)
        self.preview3d.delete('all')
        self.preview3d.create_image(0,0,image=self.preview_3d_image,anchor='nw')
        preview=Image.new('RGB',(240,160),(28,32,40));d=ImageDraw.Draw(preview)
        if selected:
            fi=selected[0];idx=self.model['face_textures'][fi]
            uv=self.model['face_uvs'][fi]
            if 0<=idx<len(self.model['textures']):
                tex=texture_rgb(self.model['textures'][idx])
                tex=tex.resize((160,160),Image.Resampling.NEAREST)
                preview.paste(tex,(40,0))
                if uv_is_usable(uv):
                    def preview_uv(v):
                        v=float(v);fraction=v%1.0
                        return 1.0 if v>0 and abs(fraction)<1e-6 else fraction
                    points=[(40+preview_uv(u)*160,(1-preview_uv(v))*160) for u,v in uv]
                    d.line(points+[points[0]],fill=(255,231,64),width=2)
        self.preview_uv_image=ImageTk.PhotoImage(preview,master=self.window)
        self.previewuv.configure(image=self.preview_uv_image,text='')
