"""Export a GB3D Studio scene into a playable Game Boy Advance project.

Keeps Studio's .gb3d format and *shares* the GBScript 2 native C compiler.
The original Game Boy Color exporter is not modified.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import shutil
from pathlib import Path
from typing import Any

import gb3d_core as core

PALETTE = [255,0,0,0,1,1,2,2,3,3,5,5,6,4,7,4]
SIN = [round(math.sin(2*math.pi*i/64)*127) for i in range(64)]
MAX_MODELS = 256
MAX_VERTICES = 512
MAX_OBJECTS = 128
MAX_NPCS = 16
MAX_UI = 32
MAX_TEXTURES = 64


def v3(value: Any) -> tuple[int,int,int]:
    return tuple(int(n) for n in value[:3])  # type: ignore


def bake(v:tuple[int,int,int],pos:tuple[int,int,int],rx:int,ry:int) -> tuple[int,int,int]:
    x,y,z=v
    if ry & 63:
        x,z=(x*SIN[(ry+16)&63]-z*SIN[ry])>>7, (x*SIN[ry]+z*SIN[(ry+16)&63])>>7
    if rx & 63:
        y,z=(y*SIN[(rx+16)&63]-z*SIN[rx])>>7, (y*SIN[rx]+z*SIN[(rx+16)&63])>>7
    return x+pos[0], y+pos[1], z+pos[2]


def camera_rotate(v:tuple[int,int,int],yaw:int,pitch:int)->tuple[int,int,int]:
    x,y,z=v
    if yaw & 63:
        x,z=(x*SIN[(yaw+16)&63]-z*SIN[yaw&63])>>7, (x*SIN[yaw&63]+z*SIN[(yaw+16)&63])>>7
    if pitch & 63:
        y,z=(y*SIN[(pitch+16)&63]-z*SIN[pitch&63])>>7, (y*SIN[pitch&63]+z*SIN[(pitch+16)&63])>>7
    return x,y,z


def carray(name:str,typ:str,values:list[str],default:str='0')->list[str]:
    return [f'static const {typ} {name}[{max(1,len(values))}] = {{',
            *[f'    {x},' for x in (values or [default])], '};']


def sprite_pixels(p:dict[str,Any])->list[int]:
    vals=p.get('sprite_pixels') or []
    if len(vals)!=1024:
        return [0]*1024
    return [int(x)&3 for x in vals]


def pack_sprite(p:dict[str,Any])->list[int]:
    src=sprite_pixels(p)
    return [src[i] | (src[i+1]<<2) | (src[i+2]<<4) | (src[i+3]<<6) for i in range(0,1024,4)]


def sprite_pal(p:dict[str,Any])->str:
    values=p.get('sprite_palette') or core.default_player_sprite_palette(int(p.get('color',10)))
    if len(values)!=4:raise ValueError('Sprite palettes must have four RGB colors')
    colors=[]
    for c in values:
        if len(c)<3:raise ValueError('Sprite palette entries require RGB')
        rgb=[max(0,min(255,int(n))) for n in c[:3]]
        v=[(n*31+127)//255 for n in rgb]
        colors.append(f'RGB5({v[0]},{v[1]},{v[2]})')
    return '{'+', '.join(colors)+'}'


def verify(project:dict[str,Any])->None:
    if not isinstance(project,dict) or not isinstance(project.get('models'),list) or not isinstance(project.get('objects'),list):
        raise ValueError('Not a GB3D Studio .gb3d project')
    if len(project['objects'])>MAX_OBJECTS or len(project['models'])>MAX_MODELS:
        raise ValueError(f'GBA prototype supports up to {MAX_OBJECTS} objects and {MAX_MODELS} models')
    if len(project.get('npcs',[]))>MAX_NPCS or len(project.get('ui',[]))>MAX_UI:
        raise ValueError(f'GBA prototype supports {MAX_NPCS} NPCs and {MAX_UI} UI elements')
    total_textures=sum(len(m.get('textures',[])) for m in project['models'])
    if total_textures>MAX_TEXTURES:
        raise ValueError(f'Too many textures: {total_textures} (limit {MAX_TEXTURES})')
    for model in project['models']:
        vs=model.get('vertices',[])
        if len(vs)>MAX_VERTICES:raise ValueError(f"{model.get('name','?')}: too many vertices (max {MAX_VERTICES})")
        if len(model.get('faces',[]))>8192:raise ValueError('Too many faces in model')
        if model.get('textures'):
            if len(model.get('face_textures',[]))!=len(model.get('faces',[])) or len(model.get('face_uvs',[]))!=len(model.get('faces',[])):
                raise ValueError(f"{model.get('name','?')}: missing texture-to-face mapping")
        for texture in model.get('textures',[]):
            w=int(texture['width']);h=int(texture['height'])
            if w not in (4,8,16,32,64) or h not in (4,8,16,32,64):raise ValueError('Textures must be power-of-two and at most 64x64')
            if len(texture['pixels'])!=w*h or not 1<=len(texture['palette'])<=64:raise ValueError('Invalid embedded texture')
            if any(not 0<=int(v)<len(texture['palette']) for v in texture['pixels']):raise ValueError('Texture pixel index out of range')
        for v in vs:
            if len(v)<3 or any(abs(int(n))>32767 for n in v[:3]):raise ValueError('Invalid int16 vertex')
        for face in model.get('faces',[]):
            if len(face)>=3 and (min(map(int,face[:3]))<0 or max(map(int,face[:3]))>=len(vs)):
                raise ValueError(f"Invalid triangle indices: {model.get('name','?')}")
    if project.get('settings',{}).get('fixed_camera_fast',False):
        conflicts=core._fixed_camera_fast_conflicts(project)
        if conflicts:raise ValueError('Fixed-camera fast path conflict: '+'; '.join(conflicts))
    for i,obj in enumerate(project['objects']):
        errors=core.validate_object_script(project,i)
        if errors:raise ValueError(f"Object {obj.get('name',i)} GBScript: {'; '.join(errors)}")
    for i,npc in enumerate(project.get('npcs',[])):
        errors=core.validate_npc_script(project,i)
        if errors:raise ValueError(f"NPC {npc.get('name',i)} GBScript: {'; '.join(errors)}")
    for person in [project.get('player',{})]+project.get('npcs',[]):
        pix=person.get('sprite_pixels')
        if pix is not None and len(pix)!=1024:raise ValueError('Character art must be 32x32')


def generate_scene_header(project:dict[str,Any])->str:
    verify(project)
    models={m.get('id'):(i,m) for i,m in enumerate(project['models'])}
    lines=['// Generated for GBA from unmodified GB3D Studio .gb3d source',
           '#ifndef GB3D_SCENE_GBA_H','#define GB3D_SCENE_GBA_H','']
    # Each model can use many map_Kd images, with texture indices assigned per
    # face. Index 255 means the legacy flat-color rasterizer.
    textures=[]
    for m in project['models']:
        textures.extend(m.get('textures',[]))
    for ti,tex in enumerate(textures):
        w,h=int(tex['width']),int(tex['height'])
        idx=list(map(int,tex['pixels']))
        lines.extend(carray(f'novapoly_tex_{ti}_indices','uint8_t',[str(x) for x in idx]))
        colors=list(map(int,tex['palette']))
        shaded=[]
        for scale in (0.56,0.78,1.0):
            for rgb in colors:
                r=rgb&31;g=(rgb>>5)&31;b=(rgb>>10)&31
                shaded.append(str((round(r*scale)) | (round(g*scale)<<5) | (round(b*scale)<<10)))
        lines.extend(carray(f'novapoly_tex_{ti}_palette','uint16_t',shaded))
    texture_items=[]
    for ti,tex in enumerate(textures):
        texture_items.append(f'{{novapoly_tex_{ti}_indices,novapoly_tex_{ti}_palette,{int(tex["width"])},{int(tex["height"])},{len(tex["palette"])} }}')
    lines.extend(['#define SCENE_TEXTURE_COUNT '+str(len(textures))+'u',
                  'static const Texture scene_textures[(SCENE_TEXTURE_COUNT)?SCENE_TEXTURE_COUNT:1] = {',
                  *[f'    {t},' for t in (texture_items or ['{0,0,0,0,0}'])],'};',''])
    tex_offset=0
    for i,m in enumerate(project['models']):
        verts=[v3(v) for v in m.get('vertices',[])]
        faces=[]
        texids=m.get('face_textures',[])
        uvdata=m.get('face_uvs',[])
        for fi,f in enumerate(m.get('faces',[])):
            if len(f)<3:continue
            color=int(f[3] if len(f)>3 else m.get('default_color',1))&15
            pal=PALETTE[color]
            local=int(texids[fi]) if fi<len(texids) else -1
            uv=uvdata[fi] if fi<len(uvdata) else None
            texture_id=255
            coords=[0]*6
            if pal==255 and (local<0 or uv is None):continue
            if local>=0 and local<len(m.get('textures',[])) and uv is not None:
                tex=m['textures'][local]
                texture_id=tex_offset+local
                coords=[]
                for uu,vv in uv:
                    # OBJ V=0 is image bottom, while Pillow first row is top.
                    # 8.8 texel-space coordinates allow seamless wrap/tiling.
                    for val in (float(uu)*tex['width'],(1.0-float(vv))*tex['height']):
                        coords.append(max(-32768,min(32767,round(val*256))))
            faces.append((int(f[0]),int(f[1]),int(f[2]),pal,texture_id,*coords))
        tex_offset+=len(m.get('textures',[]))
        lines.extend([f'// {str(m.get("name","model"))[:70]}',f'static const Vertex model_{i}_vertices[]={{'])
        lines.extend(['    {'+','.join(map(str,v))+'},' for v in (verts or [(0,0,0)])])
        lines.extend(['};',f'static const Face model_{i}_faces[]={{'])
        lines.extend(['    {'+','.join(map(str,f))+'},' for f in (faces or [(0,0,0,0,255,0,0,0,0,0,0)])])
        radius=max([math.isqrt(sum(n*n for n in v))+2 for v in verts] or [2])
        bounds=[(min(q),max(q)) for q in zip(*verts)] if verts else [(0,0)]*3
        bval=','.join(str(x) for pair in bounds for x in pair)
        lines.extend(['};',f'static const Model model_{i}={{model_{i}_vertices,model_{i}_faces,{len(verts)},{len(faces)},{radius},{{{bval}}}}};',''])
    # Geometry mutated by ANY script cannot be statically baked (even if its own script is empty).
    dynamic=set(core._script_rotation_targets(project))|set(core._script_position_targets(project))
    use_fixed=bool(project.get('settings',{}).get('fixed_camera_fast',False))
    cam_info=project.get('camera',{})
    fixed_yaw=int(cam_info.get('yaw',0))&63
    fixed_pitch=int((project.get('player',{}).get('camera_pitch',cam_info.get('pitch',0))) if project.get('player',{}).get('enabled',False) else cam_info.get('pitch',0))&63
    obj_data=[]; bounds_data=[]; baked_n=0
    for i,o in enumerate(project['objects']):
        pos=v3(o.get('position',[0,0,0]));rot=o.get('rotation',[0,0]);rx=int(rot[0])&63;ry=int(rot[1])&63
        meta=models.get(o.get('model_id'))
        if meta is None:
            obj_data.append('{0,0,'+','.join(map(str,pos))+f',{rx},{ry},{int(bool(o.get("visible",True)))},{int(bool(o.get("solid",False)))},0}}')
            bounds_data.append('{0,0,0,0,0,0}')
            continue
        mi,m=meta
        verts=[v3(v) for v in m.get('vertices',[])]
        world=[bake(v,pos,rx,ry) for v in verts]
        can_bake=(i not in dynamic and all(all(-32768<=n<=32767 for n in v) for v in world))
        if can_bake:
            lines.append(f'// Immutable object #{i}, compiler-baked into world space')
            lines.append(f'static const Vertex baked_{i}[]={{')
            vertices_to_emit=[camera_rotate(v,fixed_yaw,fixed_pitch) for v in world] if use_fixed else world
            # The baked basis must still fit in int16.
            if any(any(n<-32768 or n>32767 for n in v) for v in vertices_to_emit):
                raise ValueError('Fixed-camera transformed static vertex exceeds int16')
            lines.extend('    {'+','.join(map(str,v))+'},' for v in (vertices_to_emit or [(0,0,0)]))
            lines.append('};')
            baked_ref=f'baked_{i}';baked_n+=1
            cb=[(min(q)-pos[k],max(q)-pos[k]) for k,q in enumerate(zip(*world))] if world else [(0,0)]*3
        else:
            baked_ref='0'
            cb=[(min(q),max(q)) for q in zip(*verts)] if verts else [(0,0)]*3
        cbvals=','.join(map(str,[x for pair in cb for x in pair]))
        bounds_data.append('{'+cbvals+'}')
        obj_data.append('{'+f'&model_{mi},{baked_ref},'+','.join(map(str,pos))+f',{rx},{ry},{int(bool(o.get("visible",True)))},{int(bool(o.get("solid",False)))},{int(can_bake and use_fixed)}}}')
    lines.extend([f'#define SCENE_OBJECT_COUNT {len(obj_data)}u',f'#define SCENE_STATIC_BAKED {baked_n}u',
                  'static Object scene_objects[(SCENE_OBJECT_COUNT)?SCENE_OBJECT_COUNT:1] = {',
                  *[f'    {v},' for v in (obj_data or ['{0,0,0,0,0,0,0,0,0,0}'])], '};',
                  'static const Bounds scene_bounds[(SCENE_OBJECT_COUNT)?SCENE_OBJECT_COUNT:1] = {',
                  *[f'    {v},' for v in (bounds_data or ['{0,0,0,0,0,0}'])],'};',''])
    cam=project.get('camera',{});settings=project.get('settings',{});p=project.get('player',{})
    macros={'SCENE_CAM_X':cam.get('x',0),'SCENE_CAM_Y':cam.get('y',0),'SCENE_CAM_Z':cam.get('z',-64),
            'SCENE_CAM_YAW':int(cam.get('yaw',0))&63,'SCENE_CAM_PITCH':cam.get('pitch',0),
            'SCENE_SKY_COLOR':int(settings.get('sky_color',8))&15,
            'SCENE_PROFILE_BUILD':int(bool(settings.get('profile_build',False))),
            'SCENE_FIXED_CAMERA_FAST':int(bool(settings.get('fixed_camera_fast',False))),
            'SCENE_PLAYER_ENABLED':int(bool(p.get('enabled',False))),
            'SCENE_PLAYER_X':(p.get('position') or [0,0,0])[0],
            'SCENE_PLAYER_Y':(p.get('position') or [0,0,0])[1],
            'SCENE_PLAYER_Z':(p.get('position') or [0,0,0])[2],
            'SCENE_PLAYER_SPEED':p.get('speed',2),'SCENE_PLAYER_JUMP_SPEED':p.get('jump_speed',7),
            'SCENE_PLAYER_GRAVITY':p.get('gravity',1),'SCENE_PLAYER_GROUND_Y':p.get('ground_y',0),
            'SCENE_PLAYER_HALF_WIDTH':p.get('half_width',3),'SCENE_PLAYER_HEIGHT':p.get('height',12),
            'SCENE_PLAYER_CAMERA_DISTANCE':p.get('camera_distance',48),
            'SCENE_PLAYER_CAMERA_HEIGHT':p.get('camera_height',24),
            'SCENE_PLAYER_CAMERA_PITCH':p.get('camera_pitch',-4),
            'SCENE_PLAYER_VISIBLE':int(bool(p.get('visible',True))),
            'SCENE_NPC_COUNT':len(project.get('npcs',[])),
            'SCENE_UI_COUNT':len(project.get('ui',[]))}
    win=settings.get('window_ui',{})
    macros.update({'SCENE_WINDOW_ENABLED':int(bool(win.get('enabled',False))),
                   'SCENE_WINDOW_X':win.get('x',0),'SCENE_WINDOW_Y':win.get('y',112),
                   'SCENE_WINDOW_BG_COLOR':int(win.get('background',0))&15})
    for k,v in macros.items():lines.append(f'#define {k} {int(v)}')
    for suffix in ('X','Y','Z','YAW','PITCH'):
        lines.append(f'#define SCENE_CAMERA_{suffix} SCENE_CAM_{suffix}')
    lines.append('')
    lines.append('static const uint8_t scene_player_sprite[256] = {')
    vals=pack_sprite(p)
    lines.extend('    '+', '.join(map(str,vals[i:i+16]))+',' for i in range(0,256,16))
    lines.extend(['};',f'static const uint16_t scene_player_palette[4] = {sprite_pal(p)};',''])
    npcs=project.get('npcs',[])
    lines.append('static GB3DNPC npcs[(SCENE_NPC_COUNT)?SCENE_NPC_COUNT:1] = {')
    for n in npcs:
        x,y,z=v3(n.get('position',[0,0,0]));lines.append(f'    {{{x},{y},{z},{int(n.get("height",12))},{int(bool(n.get("visible",True)))}}},')
    if not npcs:lines.append('    {0,0,0,0,0},')
    lines.append('};')
    lines.append('static const uint8_t scene_npc_sprites[(SCENE_NPC_COUNT)?SCENE_NPC_COUNT:1][256] = {')
    for n in npcs:
        vals=pack_sprite(n);lines.append('    {')
        lines.extend('        '+', '.join(map(str,vals[i:i+16]))+',' for i in range(0,256,16))
        lines.append('    },')
    if not npcs:lines.append('    {0},')
    lines.append('};')
    lines.extend(carray('scene_npc_palettes','uint16_t [4]',[sprite_pal(n) for n in npcs],'{0,0,0,0}')) if False else None
    lines.append('static const uint16_t scene_npc_palettes[(SCENE_NPC_COUNT)?SCENE_NPC_COUNT:1][4] = {')
    lines.extend(f'    {sprite_pal(n)},' for n in npcs)
    if not npcs:lines.append('    {0,0,0,0},')
    lines.append('};')
    for i,u in enumerate(project.get('ui',[])):
        if u.get('type','text')=='text':lines.append(f'static const char ui_text_{i}[] = {json.dumps(str(u.get("text","")),ensure_ascii=True)};')
    lines.append('static GB3DUIElement scene_ui[(SCENE_UI_COUNT)?SCENE_UI_COUNT:1] = {')
    for i,u in enumerate(project.get('ui',[])):
        typ=int(u.get('type','text')=='bar')
        text=f'ui_text_{i}' if not typ else '0'
        args=[typ,u.get('x',0),u.get('y',0),u.get('width',0) if typ else 0,
              int(u.get('color',1))&15,int(u.get('bg_color',3))&15,int(bool(u.get('visible',True))),
              int(bool(u.get('show_value',False))),u.get('value',0),max(1,int(u.get('max',10)))]
        lines.append('    {'+','.join(map(lambda v:str(int(v)),args))+','+text+'},')
    if not project.get('ui'):lines.append('    {0,0,0,0,0,0,0,0,0,1,0},')
    lines.extend(['};','','// GBScript 2: shared compiler with the original GBC exporter.','// Events, variables, expressions and native C commands are preserved.'])
    lines.extend(core.generate_script_c(project))
    lines.extend(['#endif',''])
    return '\n'.join(lines)


def export_gba(project:dict[str,Any],folder:str|Path,base:str|Path|None=None)->list[Path]:
    project=copy.deepcopy(project)
    header=generate_scene_header(project)
    folder=Path(folder)
    template=Path(base) if base is not None else Path(__file__).resolve().parent/'gba_runtime'
    srcdir=folder/'source';srcdir.mkdir(parents=True,exist_ok=True)
    made=[]
    for source,dest in [('Makefile',folder/'Makefile'),('main.c',srcdir/'main.c'),
                        ('gb3d_arm.s',srcdir/'gb3d_arm.s'),('gb3d_gameplay.h',srcdir/'gb3d_gameplay.h'),('gb3d_overlay.h',srcdir/'gb3d_overlay.h')]:
        shutil.copyfile(template/source,dest)
        if source == 'main.c' and project.get('settings', {}).get('gba_queue_512', False):
            # A/B comparison against the old 512-face cap, deliberately not
            # the default: 1024 slots can preserve more of a complex model.
            code = dest.read_text(encoding='utf8')
            old = '#define NOVAPOLY_MAX_TRIANGLES 1024'
            assert old in code, 'GBA triangle-cap setting missing from runtime'
            dest.write_text(code.replace(old,
                '#define NOVAPOLY_MAX_TRIANGLES 512', 1), encoding='utf8')
        made.append(dest)
    scene=srcdir/'scene_gba.h';scene.write_text(header,encoding='utf8');made.append(scene)
    original=folder/'scene.gb3d';original.write_text(json.dumps(project,ensure_ascii=False,indent=2)+'\n',encoding='utf8');made.append(original)
    instructions=folder/'README_GBA.txt';instructions.write_text(
        'NovaPoly GBA v0.6.3 - accessible profiling + queue comparison\n\n'
        'Build with devkitPro gba-dev: make clean && make\n'
        'Output novapoly_gba.gba (test with mGBA or GBA flashcart).\n\n'
        'When PLAYER is enabled: D-pad move, SELECT+D-pad look, A jump, B for script actions.\n'
        'When PLAYER is disabled: D-pad free-camera walk, SELECT+D-pad look, A/B vertical.\n'
        'All GBC input script events apply in both modes.\n'
        'When Profiler build is enabled: START+SELECT toggles profiler, L/R cycle pages.\n'
        'Timing pages report 1/4ms units (64 timer ticks) for G/R/T/F/S/C.\n'
        'T=texture triangles including setup, F=flat; P is shaded pixel writes.\n'
        'Set NOVAPOLY_FAST_TEX=1 to A/B test experimental paired-write kernel; 0 is v0.5 baseline.\n'
        'Memory: first 512 triangles in IWRAM, remainder in EWRAM when using 1024 mode.\n'
        'Use Game > GBA benchmark 512-triangle queue to compare to v0.5; this may drop faces.\n'
        'GBScript 2 @start/@update/@a/@b and all documented events supported,\n'
        'including object/NPC movement, HUD values, window control, and player control.\n'
        'Player remains 32x32; NPC billboards scale by world height / camera distance in\n'
        'Mode 5, not GBC OAM sprites. HUD is a software overlay, not GBC Window hardware.\n'
        'The GBC SM83 assembler is replaced with the ARM span-filling routine.\n'
        'Textured OBJ meshes use affine UV sampling with per-material map_Kd images.\n'
        'Images are automatically resized/quantized during import and embedded in .gb3d.\n'
        'GBA Mode 5 is aspect-correct: 160x128 -> centered 200x160, with sky-colored side margins.\n'
        'Change #define GB3D_CORRECT_ASPECT in source/main.c to 0 for old full-screen stretch.\n'
        'LIMITATIONS: GBA Mode 5 160x128; coarse depth buckets, simple AABB collision\n'
        'like GBC; near-plane triangles still rejected; dynamic object collision\n'
        'bounds are axis-aligned; sprite depth is bucket-approximate.\n',encoding='utf8')
    made.append(instructions)
    return made

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('scene',type=Path);parser.add_argument('output',type=Path)
    a=parser.parse_args()
    files=export_gba(json.loads(a.scene.read_text(encoding='utf8')),a.output)
    print('Exported',len(files),'GBA project files to',a.output)
