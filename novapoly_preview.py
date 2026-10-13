"""CPU texture previews shared by the main NovaPoly viewport and GBA inset.

No GPU/OpenGL required: the GBA view renders at the actual Mode 5 framebuffer
resolution, nearest-neighbor / affine, using the indexed RGB555 texture data.
This is a visual approximation, NOT an instruction-accurate ARM rasterizer.
"""
from __future__ import annotations
import math
from PIL import Image, ImageDraw
import gb3d_core as core

GBA_W, GBA_H = 160, 128
# The GBA palette comes straight from gba_runtime/main.c (RGB555).
MATERIAL = [
 [(0,0,0),(8,8,8),(20,20,20),(31,31,31)],
 [(0,0,0),(11,2,3),(25,4,5),(31,16,16)],
 [(0,0,0),(2,10,3),(4,23,6),(16,31,17)],
 [(0,0,0),(2,4,11),(4,10,25),(16,20,31)],
 [(0,0,0),(9,3,11),(20,7,25),(31,18,31)],
 [(0,0,0),(12,6,1),(27,15,2),(31,29,8)],
 [(0,0,0),(1,9,10),(3,22,24),(15,31,31)],
 [(0,0,0),(8,6,3),(19,14,8),(29,24,16)],
]
GBA_LOGICAL_PALETTE = [0,0,0,0,1,1,2,2,3,3,5,5,6,4,7,4]
GBA_LOGICAL_SHADE = [0,3,2,1,2,1,2,1,2,1,3,2,3,2,2,3]


def rgb555(value):
    v=int(value)
    return ((v & 31)*255//31, ((v>>5 & 31)*255//31), ((v>>10 & 31)*255//31))


def texture_rgb(tex, shade=3):
    """Convert the exact embedded GBA RGB555 palette, with the ROM's 3 lighting steps."""
    shade_scale=(0.56, 0.78, 1.0)[max(1,min(3,int(shade)))-1]
    colors=[]
    for color in tex['palette']:
        r=int(color)&31;g=(int(color)>>5)&31;b=(int(color)>>10)&31
        colors.append(((round(r*shade_scale)*255)//31,(round(g*shade_scale)*255)//31,(round(b*shade_scale)*255)//31))
    return (int(tex['width']),int(tex['height']),colors,list(map(int,tex['pixels'])))


def gba_primitives(project, camera, wireframe=False):
    """GBA-sized projection using the runtime's 112px focal distance and area-based shade."""
    result=[];count=0
    for obj in project.get('objects',[]):
        if not obj.get('visible',True):continue
        model=core.model_by_id(project,obj.get('model_id',''))
        if model is None:continue
        rx,ry=(obj.get('rotation') or [0,0])[:2]
        px,py,pz=(obj.get('position') or [0,0,0])[:3]
        coords=[];zs=[]
        for v in model.get('vertices',[]):
            x,y,z=core._editor_rotate_vertex(v,int(rx),int(ry))
            x,y,z=core._editor_world_to_camera((x+px,y+py,z+pz),camera)
            zs.append(z)
            coords.append((80+int(x*112/z),64-int(y*112/z)) if 8<=z<=255 else None)
        for fi,face in enumerate(model.get('faces',[])):
            if len(face)<3:continue
            a,b,c=map(int,face[:3]);count+=1
            if max(a,b,c)>=len(coords) or min(a,b,c)<0:continue
            if any(coords[k] is None for k in (a,b,c)):continue
            pa,pb,pc=coords[a],coords[b],coords[c]
            cross=(pb[0]-pa[0])*(pc[1]-pa[1])-(pb[1]-pa[1])*(pc[0]-pa[0])
            if not wireframe and cross<=0:continue
            if max(p[0] for p in (pa,pb,pc))<0 or min(p[0] for p in (pa,pb,pc))>=GBA_W:continue
            if max(p[1] for p in (pa,pb,pc))<0 or min(p[1] for p in (pa,pb,pc))>=GBA_H:continue
            shade=3 if cross>1000 else 2 if cross>280 else 1
            logical=int(face[3] if len(face)>3 else model.get('default_color',1))&15
            texids=model.get('face_textures',[]);uvs=model.get('face_uvs',[])
            ti=int(texids[fi]) if fi<len(texids) else -1
            uv=uvs[fi] if fi<len(uvs) else None
            result.append(dict(kind='triangle',points=(pa,pb,pc),depth=max(zs[a],zs[b],zs[c]),
                               shade=shade,logical_color=logical,texture=model['textures'][ti]
                               if 0<=ti<len(model.get('textures',[])) and uv and len(uv)==3 else None,
                               uv=uv,object_id=obj.get('id',''),face_index=fi))
    # GBA uses 16 far-to-near depth buckets and LIFO per bucket.
    result=list(reversed(result))
    result.sort(key=lambda p:-max(0,min(15,int(p['depth'])>>4)))
    return result,{'faces':count,'drawn':len(result)}


def _draw_triangle(img, prim, model_tex, hardware):
    p0,p1,p2=prim['points']
    x0,y0=p0;x1,y1=p1;x2,y2=p2
    w,h=img.size
    left=max(0,int(math.floor(min(x0,x1,x2))))
    right=min(w,int(math.ceil(max(x0,x1,x2))))
    top=max(0,int(math.floor(min(y0,y1,y2))))
    bottom=min(h,int(math.ceil(max(y0,y1,y2))))
    if right<=left or bottom<=top:return
    area=(y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
    if abs(area)<1e-6:return
    tex=prim.get('texture')
    uv=prim.get('uv')
    if tex is None or uv is None or len(uv)!=3:
        if hardware:
            logical=prim.get('logical_color',1)&15
            rgb=MATERIAL[GBA_LOGICAL_PALETTE[logical]][prim['shade']]
            color=tuple(n*255//31 for n in rgb)
        else: color=prim.get('color',(200,200,200))
        ImageDraw.Draw(img).polygon(prim['points'],fill=(*color,255))
        return
    tw,th,pal,texels=model_tex(id(tex),tex,prim.get('shade',3) if hardware else 3)
    # GBA uses power-of-two wrap. UVs are stored in OBJ bottom-left coordinates.
    # Sample pixel centers to approximate the GBA affine rasterizer.
    output=img.load()
    uu=[float(pair[0]) for pair in uv];vv=[float(pair[1]) for pair in uv]
    da0=(y1-y2)/area;db0=(x2-x1)/area
    da1=(y2-y0)/area;db1=(x0-x2)/area
    for y in range(top,bottom):
        py=y+0.5
        a=(da0*(left+0.5-x2)+db0*(py-y2))
        b=(da1*(left+0.5-x2)+db1*(py-y2))
        for x in range(left,right):
            c=1-a-b
            if a>=-1e-7 and b>=-1e-7 and c>=-1e-7:
                u=a*uu[0]+b*uu[1]+c*uu[2]
                v=a*vv[0]+b*vv[1]+c*vv[2]
                tx=math.floor(u*tw)%tw;ty=math.floor((1-v)*th)%th
                output[x,y]=(*pal[texels[ty*tw+tx]],255)
            a+=da0;b+=da1


def draw_billboard(img, npc, camera, player=False):
    """GBA-style NPC preview: projected world-space height, no constant 32px sprite."""
    from PIL import Image
    if not npc.get('visible',True):return
    x,y,z=(npc.get('position') or [0,0,0])[:3]
    height=max(1,int(npc.get('height',12)))
    x,y,z=core._editor_world_to_camera((x,y+height/2,z),camera)
    if z<8 or z>255:return
    size=32 if player else max(1,min(96,int(height*112/z)))
    cx=80+int(x*112/z);cy=64-int(y*112/z)
    if cx+size//2<0 or cy+size//2<0 or cx-size//2>=160 or cy-size//2>=128:return
    p=npc.get('sprite_pixels') or core.default_player_sprite_pixels()
    palette=npc.get('sprite_palette') or core.default_player_sprite_palette(14)
    # The exporter converts character palettes to RGB555 as well.
    colors=[tuple((((int(v)*31+127)//255)*255//31) for v in q[:3]) for q in palette]
    for oy in range(size):
        yy=cy-size//2+oy
        if yy<0 or yy>=128:continue
        sy=min(31,oy*32//size)
        for ox in range(size):
            xx=cx-size//2+ox
            if xx<0 or xx>=160:continue
            sx=min(31,ox*32//size)
            idx=int(p[sy*32+sx])&3
            if idx:img.putpixel((xx,yy),(*colors[idx],255))


def draw_gba_hud(img, project):
    """Static editor approximation of the Mode 5 software Window UI."""
    win=project.get('settings',{}).get('window_ui',{})
    if not win.get('enabled',False):return
    left=max(0,min(GBA_W,int(win.get('x',0))))
    top=max(0,min(GBA_H,int(win.get('y',112))*GBA_H//144))
    draw=ImageDraw.Draw(img)
    def ui_color(index):
        i=int(index)&15
        return tuple(v*255//31 for v in MATERIAL[GBA_LOGICAL_PALETTE[i]][GBA_LOGICAL_SHADE[i]])
    draw.rectangle((left,top,GBA_W-1,GBA_H-1),fill=(*ui_color(win.get('background',0)),255))
    def text(x,y,message,color):
        for ch in str(message).upper():
            if x>=GBA_W-2:break
            rows=core._FONT3X5.get(ch,core._FONT3X5.get('?'))
            for yy,bits in enumerate(rows):
                for xx in range(3):
                    if bits & (1<<(2-xx)) and 0<=x+xx<GBA_W and 0<=y+yy<GBA_H:
                        img.putpixel((x+xx,y+yy),(*color,255))
            x+=4
    for u in project.get('ui',[]):
        if not u.get('visible',True):continue
        x=left+int(u.get('x',0))*8
        y=top+int(u.get('y',0))*8
        fg=ui_color(u.get('color',1))
        if u.get('type','text')=='text':
            label=str(u.get('text',''))
            if u.get('show_value',False):label+=' '+str(int(u.get('value',0)))
            text(x,y,label,fg)
        else:
            count=min(20,max(0,int(u.get('width',10))))
            maxval=max(1,int(u.get('max',10)))
            filled=max(0,min(count,int(u.get('value',0))*count//maxval))
            for n in range(count):
                xx=x+n*8
                draw.rectangle((xx,y,xx+6,y+5),fill=(*fg,255) if n<filled else (*ui_color(u.get('bg_color',3)),255))


def render_gba(project,camera, include_npcs=True):
    """Render GBA Mode5 source 160x128; presentation uses 200x160 centered on LCD."""
    sky=int(project.get('settings',{}).get('sky_color',0))&15
    bpal=GBA_LOGICAL_PALETTE[sky];bshade=GBA_LOGICAL_SHADE[sky]
    bg=tuple(v*255//31 for v in MATERIAL[bpal][bshade])
    im=Image.new('RGBA',(GBA_W,GBA_H),(*bg,255))
    prims,stats=gba_primitives(project,camera)
    cache={}
    def get_tex(key,tex,shade):
        k=(key,shade)
        if k not in cache: cache[k]=texture_rgb(tex,shade)
        return cache[k]
    billboards={b:[] for b in range(16)}
    if include_npcs:
        items=[]
        player=project.get('player',{})
        if player.get('enabled',False) and player.get('visible',True):items.append((player,True))
        items.extend((npc,False) for npc in project.get('npcs',[]))
        for char,is_player in items:
            x,y,z=(char.get('position') or [0,0,0])[:3]
            height=max(1,int(char.get('height',12)))
            _,_,depth=core._editor_world_to_camera((x,y+height/2,z),camera)
            if 8<=depth<=255: billboards[min(15,int(depth)//16)].append((char,is_player))
    # Like the runtime, flush 16 painter buckets far-to-near, with sprite art
    # after triangles in the same bucket. Preview does not run GBScript or physics.
    by_bucket={b:[] for b in range(16)}
    for prim in prims:by_bucket[min(15,max(0,int(prim['depth'])//16))].append(prim)
    for b in range(15,-1,-1):
        for prim in by_bucket[b]:_draw_triangle(im,prim,get_tex,True)
        for char,is_player in billboards[b]:draw_billboard(im,char,camera,player=is_player)
    draw_gba_hud(im,project)
    return im,stats


def render_editor_textures(project, camera, width,height, primitives):
    """Transparent textured overlay for existing canvas, so guides and object picking survive."""
    # Cap software render resolution to keep Tk responsive with many triangles.
    w=max(64,int(width));h=max(64,int(height))
    scale=min(1,640/w,440/h)
    rw=max(64,round(w*scale));rh=max(64,round(h*scale))
    im=Image.new('RGBA',(rw,rh),(0,0,0,0))
    cache={}
    def get_tex(key,tex,shade):
        if key not in cache:cache[key]=texture_rgb(tex)
        return cache[key]
    models={m.get('id'):m for m in project.get('models',[])}
    object_models={o.get('id'):models.get(o.get('model_id')) for o in project.get('objects',[])}
    for p in primitives:
        if p.get('kind')!='triangle':continue
        model=object_models.get(p.get('object_id'))
        if model is None:continue
        fi=p.get('face_index',-1)
        ids=model.get('face_textures',[]);uvs=model.get('face_uvs',[])
        ti=int(ids[fi]) if 0<=fi<len(ids) else -1
        tex=model.get('textures',[])
        payload=dict(p,points=tuple((x*rw/w,y*rh/h) for x,y in p['points']),
                     texture=tex[ti] if 0<=ti<len(tex) else None,
                     uv=uvs[fi] if 0<=fi<len(uvs) else None)
        _draw_triangle(im,payload,get_tex,False)
    if (rw,rh)!=(w,h):im=im.resize((w,h),Image.Resampling.NEAREST)
    return im


def gba_lcd_image(frame):
    """160x128 Mode5 enlarged uniformly to 200x160, with 20px side margins."""
    backdrop=frame.getpixel((0,0))
    lcd=Image.new('RGB',(240,160),backdrop[:3])
    lcd.paste(frame.convert('RGB').resize((200,160),Image.Resampling.NEAREST),(20,0))
    return lcd
