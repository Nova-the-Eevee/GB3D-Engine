"""NovaPoly indexed texture importer and editable per-face materials.

This module has no Tk dependency: UI, OBJ import, tests and exporters share the
same per-model texture mapping. Legacy .gb3d scenes remain valid.
"""
from __future__ import annotations
from pathlib import Path
import math
import re
import shlex
from typing import Any


def _parse_idx(token: str, count: int) -> int:
    n = int(token)
    if n == 0:
        raise ValueError('OBJ indices cannot be zero')
    return n - 1 if n > 0 else count + n


def _rgb555(rgb: tuple[int,int,int]) -> int:
    return sum(((max(0,min(255,c))*31+127)//255) << (5*i) for i,c in enumerate(rgb))


def _pot_dim(v: float, cap: int) -> int:
    v = max(4, min(float(cap), v))
    dim = 4
    while dim*2 <= cap and abs(dim*2-v) < abs(dim-v):
        dim *= 2
    return dim


def convert_texture(path: Path, max_dimension: int=32, max_colors: int=32) -> dict[str, Any]:
    if max_dimension not in (16,32,64) or max_colors not in (16,32,64):
        raise ValueError('Texture size must be 16/32/64; palette size must be 16/32/64')
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError('Textured OBJ import requires Pillow: pip install pillow') from exc
    with Image.open(path) as img:
        img.load()
        w,h=img.size
        if not w or not h: raise ValueError(f'Empty texture: {path}')
        scale=min(1.0, max_dimension/max(w,h))
        tw=_pot_dim(w*scale,max_dimension);th=_pot_dim(h*scale,max_dimension)
        rgba=img.convert('RGBA')
        background=Image.new('RGBA',rgba.size,(0,0,0,255))
        background.alpha_composite(rgba)
        rgb=background.convert('RGB').resize((tw,th),Image.Resampling.LANCZOS)
        indexed=rgb.quantize(colors=max_colors,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
        used=sorted(set(indexed.getdata()))
        raw_palette=indexed.getpalette()
        palette=[_rgb555(tuple(raw_palette[3*i:3*i+3])) for i in used]
        remap={old:i for i,old in enumerate(used)}
        pixels=[remap[i] for i in indexed.getdata()]
        return {'name':path.name,'width':tw,'height':th,'palette':palette,'pixels':pixels}


def ensure_material_data(model: dict[str,Any]) -> dict[str,Any]:
    """Upgrade untextured or pre-0.4 models in memory, preserving existing UVs."""
    count=len(model.get('faces',[]))
    model.setdefault('textures',[])
    for key, fill in (('face_textures',-1),('face_uvs',None)):
        existing=list(model.get(key,[]))[:count]
        existing.extend([fill]*(count-len(existing)))
        model[key]=existing
    names=list(model.get('face_materials',[]))[:count]
    for i in range(len(names),count):
        texid=model['face_textures'][i]
        texs=model['textures']
        names.append('Texture '+str(texid+1) if 0<=texid<len(texs) else 'Default')
    model['face_materials']=[str(v or 'Default') for v in names]
    model.setdefault('import_warnings',[])
    return model


def material_names(model:dict[str,Any])->list[str]:
    ensure_material_data(model)
    return list(dict.fromkeys(model['face_materials']))


def faces_for_material(model:dict[str,Any], material:str)->list[int]:
    ensure_material_data(model)
    return [i for i,name in enumerate(model['face_materials']) if name==material]


def _project_face_uv(model:dict[str,Any],fi:int)->list[list[float]]:
    """Per-triangle box projection: choose the plane most facing the polygon.
    Great for block-models; cannot reconstruct custom unwraps from missing data.
    """
    f=model['faces'][fi]
    verts=[model['vertices'][int(i)] for i in f[:3]]
    ax=[verts[1][k]-verts[0][k] for k in range(3)]
    bx=[verts[2][k]-verts[0][k] for k in range(3)]
    normal=(ax[1]*bx[2]-ax[2]*bx[1],ax[2]*bx[0]-ax[0]*bx[2],ax[0]*bx[1]-ax[1]*bx[0])
    major=max(range(3),key=lambda k:abs(normal[k]))
    if major==0: a,b=2,1
    elif major==1: a,b=0,2
    else: a,b=0,1
    xs=[float(v[a]) for v in verts];ys=[float(v[b]) for v in verts]
    minx,maxx=min(xs),max(xs);miny,maxy=min(ys),max(ys)
    dx=maxx-minx or 1.0;dy=maxy-miny or 1.0
    return [[round((x-minx)/dx,7),round((y-miny)/dy,7)] for x,y in zip(xs,ys)]


def uv_is_usable(uv:Any)->bool:
    if not isinstance(uv,list) or len(uv)!=3:
        return False
    try:
        coords=[(float(p[0]),float(p[1])) for p in uv]
        return all(math.isfinite(v) for p in coords for v in p) and abs(
            (coords[1][0]-coords[0][0])*(coords[2][1]-coords[0][1])-
            (coords[2][0]-coords[0][0])*(coords[1][1]-coords[0][1]))>1e-9
    except (TypeError,ValueError,IndexError):
        return False


def generate_box_uv(model:dict[str,Any],indices:list[int],overwrite:bool=False)->int:
    ensure_material_data(model)
    changed=0
    for fi in indices:
        if not 0<=fi<len(model['faces']):raise IndexError('Triangle index out of range')
        if overwrite or not uv_is_usable(model['face_uvs'][fi]):
            model['face_uvs'][fi]=_project_face_uv(model,fi)
            changed+=1
    return changed


def _compact(model:dict[str,Any])->None:
    old=model['textures'];used=list(dict.fromkeys(i for i in model['face_textures'] if 0<=i<len(old)))
    mapping={v:i for i,v in enumerate(used)}
    model['textures']=[old[i] for i in used]
    model['face_textures']=[mapping.get(i,-1) for i in model['face_textures']]


def assign_image(model:dict[str,Any],image_path:str|Path,indices:list[int],max_dimension:int=32,max_colors:int=32,
                 regenerate_uv:bool=False)->dict[str,Any]:
    """Assign an image to chosen triangles; preserve existing UVs if possible.

    A new texture slot is used to avoid modifying unrelated materials sharing
    the same old texture. Orphan slots are removed to avoid wasting GBA ROM.
    """
    ensure_material_data(model)
    if not indices:raise ValueError('No triangles selected')
    if any(not 0<=i<len(model['faces']) for i in indices):raise IndexError('Triangle index out of range')
    tex=convert_texture(Path(image_path),max_dimension,max_colors)
    model['textures'].append(tex)
    idx=len(model['textures'])-1
    for fi in indices:
        model['face_textures'][fi]=idx
    count=generate_box_uv(model,indices,overwrite=regenerate_uv)
    _compact(model)
    return {'texture':tex,'generated_uv':count,'faces':len(indices)}


def remove_texture(model:dict[str,Any],indices:list[int])->None:
    ensure_material_data(model)
    for fi in indices:
        if not 0<=fi<len(model['faces']):raise IndexError('Triangle index out of range')
        model['face_textures'][fi]=-1
    _compact(model)


def _resolve_texture_path(objdir:Path,mtldir:Path,value:str)->Path|None:
    # Handle Windows path separators in exported MTL on Linux and vice versa.
    value=value.strip().strip('"\'').replace('\\','/')
    if not value:return None
    candidates=[mtldir/value,objdir/value,mtldir/Path(value).name,objdir/Path(value).name]
    for candidate in candidates:
        if candidate.is_file():return candidate.resolve()
    # Windows paths may have lost their drive prefix; try filename match.
    filename=Path(value).name.lower()
    for directory in dict.fromkeys([mtldir,objdir]):
        try:
            for candidate in directory.iterdir():
                if candidate.is_file() and candidate.name.lower()==filename:return candidate.resolve()
        except OSError:pass
    return None


def _mtl_file_refs(obj_path:Path,libs:list[str])->dict[str,tuple[Path,str]]:
    found={}
    for lib in libs:
        path=obj_path.parent/lib.replace('\\','/')
        if not path.is_file():
            path=_resolve_texture_path(obj_path.parent,obj_path.parent,lib)
        if path is None or not path.is_file():continue
        current=None
        for raw in path.read_text(encoding='utf8',errors='replace').splitlines():
            line=raw.strip()
            if not line or line.startswith('#'):continue
            tag,_,body=line.partition(' ')
            if tag.lower()=='newmtl':current=body.strip()
            elif tag.lower()=='map_kd' and current and body.strip():
                value=body.strip().strip('"')
                if value.startswith('-'):
                    # Support common mapping arguments; preserve spaces in names.
                    pieces=shlex.split(value)
                    pos=0
                    option_sizes={'-s':3,'-o':3,'-t':3,'-mm':2,'-bm':1,'-clamp':1,'-blendu':1,'-blendv':1,'-type':1,'-imfchan':1,'-texres':1}
                    while pos<len(pieces) and pieces[pos].startswith('-'):
                        op=pieces[pos].lower();pos+=1
                        n=option_sizes.get(op,1)
                        if op in ('-s','-o','-t'):
                            n=0
                            while n<3 and pos+n<len(pieces) and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',pieces[pos+n]):n+=1
                        pos+=n
                    value=' '.join(pieces[pos:])
                found[current]=(path.parent,value)
    return found


def add_obj_textures(model:dict[str,Any],obj_path:str|Path,max_dimension:int=32,max_colors:int=32)->dict[str,Any]:
    """Import material groups and OBJ UVs; missing image files are editable later."""
    path=Path(obj_path)
    texcoords=[];polygons=[];libs=[];material=None
    for raw in path.read_text(encoding='utf8',errors='replace').splitlines():
        line=raw.strip()
        if not line or line.startswith('#'):continue
        head,_,tail=line.partition(' ')
        args=tail.split()
        if head=='vt' and len(args)>=2:
            texcoords.append((float(args[0]),float(args[1])))
        elif head=='f' and len(args)>=3:
            polygons.append((args,material or 'Default'))
        elif head=='usemtl':material=tail.strip() or 'Default'
        elif head=='mtllib':
            # Most exporters use one MTL per OBJ, including MTLs with spaces.
            parsed=shlex.split(tail)
            if (path.parent/tail).is_file():libs.append(tail)
            else:libs.extend(parsed)
    refs=_mtl_file_refs(path,libs)
    cache={};textures=[];face_textures=[];face_uvs=[];face_materials=[];warnings=[]
    for corners,mat in polygons:
        texindex=-1
        if mat in refs:
            base,source=refs[mat]
            texpath=_resolve_texture_path(path.parent,base,source)
            if texpath is None:
                warning=f'{mat}: cannot find {source}; use Materials... to load image manually'
                if warning not in warnings:warnings.append(warning)
            else:
                try:
                    if texpath not in cache:
                        data=convert_texture(texpath,max_dimension,max_colors)
                        cache[texpath]=len(textures);textures.append(data)
                    texindex=cache[texpath]
                except (OSError,ValueError,RuntimeError) as exc:
                    warning=f'{mat}: {exc}; use Materials... to load a PNG'
                    if warning not in warnings:warnings.append(warning)
        uv=[]
        for corner in corners:
            bits=corner.split('/')
            if len(bits)>1 and bits[1]:
                idx=_parse_idx(bits[1],len(texcoords))
                if not 0<=idx<len(texcoords):raise ValueError(f'Invalid OBJ texture coordinate: {corner}')
                uv.append([round(texcoords[idx][0],7),round(texcoords[idx][1],7)])
            else:uv.append(None)
        for n in range(1,len(corners)-1):
            tri=[uv[0],uv[n],uv[n+1]]
            face_materials.append(mat)
            face_uvs.append(tri if all(v is not None for v in tri) else None)
            face_textures.append(texindex)
    if len(face_uvs)!=len(model['faces']):
        raise ValueError('OBJ parsing mismatch: face triangulation differs')
    model.update(textures=textures,face_textures=face_textures,face_uvs=face_uvs,
                 face_materials=face_materials,import_warnings=warnings)
    # Some Blockbench OBJ exports include map_Kd but omit vt coordinates.
    # In that case prefer visible (automatically box-mapped) textures to an
    # inexplicably flat GBA export. Existing valid UVs are never overwritten.
    fixed=generate_box_uv(model,[i for i,idx in enumerate(face_textures) if idx>=0])
    if fixed:
        model['import_warnings'].append(f'Generated fallback box UVs for {fixed} triangle(s) without usable UVs')
    return model
