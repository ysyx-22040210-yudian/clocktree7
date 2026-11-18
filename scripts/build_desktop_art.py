"""Build the original desktop icons and ttk nine-slice surfaces. Runtime needs only Tk."""
import json
from pathlib import Path

from PIL import Image, ImageDraw


DEST = Path(__file__).resolve().parents[1] / 'clocktree' / 'artwork'
# Each icon uses a 24-unit square. Paths remain separate at real circuit branches.
PATHS = {
    'folder': [[(3,7),(3,5),(9,5),(12,8),(20,8),(20,11)],[(3,18),(5,10),(22,10),(19,19),(3,19),(2,8)]],
    'chip': [[(6,6),(18,6),(18,18),(6,18),(6,6)],[(9,9),(15,9),(15,15),(9,15),(9,9)]] +
            [[(i,3),(i,6)] for i in (9,15)] + [[(i,18),(i,21)] for i in (9,15)] +
            [[(3,i),(6,i)] for i in (9,15)] + [[(18,i),(21,i)] for i in (9,15)],
    'sliders': [[(3,6),(8,6)],[(12,6),(21,6)],[(8,3),(12,3),(12,9),(8,9),(8,3)],
                [(3,17),(14,17)],[(18,17),(21,17)],[(14,14),(18,14),(18,20),(14,20),(14,14)]],
    'inspector': [[(3,4),(21,4),(21,20),(3,20),(3,4)],[(13,4),(13,20)],[(16,9),(18,9)],[(16,13),(18,13)]],
    'branch': [[(3,11),(9,11),(13,6),(18,6)],[(9,11),(14,17),(18,17)],[(17,3),(21,3),(21,8),(17,8),(17,3)],
               [(17,15),(21,15),(21,20),(17,20),(17,15)],[(2,9),(6,9),(6,13),(2,13),(2,9)]],
    'layers': [[(2,8),(12,3),(22,8),(12,13),(2,8)],[(2,12),(12,17),(22,12)],[(2,16),(12,21),(22,16)]],
    'register': [[(3,6),(9,6),(9,18),(3,18),(3,6)],[(15,6),(21,6),(21,18),(15,18),(15,6)],[(9,12),(15,12)]],
    'code': [[(7,6),(2,12),(7,18)],[(17,6),(22,12),(17,18)],[(14,4),(10,20)]],
    'issue': [[(12,3),(22,20),(2,20),(12,3)],[(12,9),(12,13)],[(12,16),(12,17)]],
    'focus': [[(3,9),(3,4),(9,4)],[(15,4),(21,4),(21,9)],[(21,15),(21,20),(15,20)],[(9,20),(3,20),(3,15)]],
    'search': [[(15,15),(21,21)]],
    'link': [[(4,13),(2,11),(2,8),(7,3),(10,3),(13,6)],[(20,11),(22,13),(22,16),(17,21),(14,21),(11,18)],[(7,17),(17,7)]],
    'root': [[(12,5),(12,13)],[(4,19),(4,15),(20,15),(20,19)],[(12,13),(12,16)]],
    'terminal': [[(4,20),(4,4),(18,4),(15,9),(18,13),(4,13)]],
    'network': [[(4,9),(4,6),(12,6),(12,10)],[(12,14),(12,18),(20,18),(20,15)]],
    'check': [[(4,12),(9,17),(20,6)]],
    'log': [[(2,4),(22,4),(22,20),(2,20),(2,4)],[(5,8),(9,12),(5,16)],[(12,15),(18,15)]],
    'upstream': [[(21,12),(3,12)],[(9,6),(3,12),(9,18)]],
    'downstream': [[(3,12),(21,12)],[(15,6),(21,12),(15,18)]],
    'play': [[(6,3),(20,12),(6,21),(6,3)]],
    'save': [[(3,3),(17,3),(21,7),(21,21),(3,21),(3,3)],[(7,3),(7,9),(16,9),(16,3)],[(7,20),(7,14),(17,14),(17,20)]],
    'chevron': [[(6,9),(12,15),(18,9)]],
    'clock': [[(12,6),(12,12),(16,15)]],
}
CIRCLES = {'clock': [(3,3,21,21)], 'search': [(3,3,16,16)], 'root': [(9,2,15,8)],
           'network': [(2,9,6,13),(10,10,14,14),(18,11,22,15)]}
BAR, BOARD, BLUE = '#172339', '#101b2b', '#365ad9'
WHITE = '#ffffff'
ICON_TONES = {'ink':('#52657f',WHITE), 'blue':(BLUE,WHITE),
              'light':('#f3f7ff',BLUE), 'night':('#aebfda',BAR),
              'slate':('#8290a6',WHITE), 'teal':('#087f77',WHITE), 'amber':('#a36211',WHITE)}
# Surface, edge and outer matte. The centre pixel is repeated by Tk.
SURFACES = {
    'button':(WHITE,'#ced8e5',WHITE),'hover':('#eff3ff','#9aacec',WHITE),
    'pressed':('#e1e9fc','#7795e4',WHITE),'focus':(WHITE,BLUE,WHITE),
    'muted':('#f6f8fb','#e0e6ef',WHITE),'blue':(BLUE,'#3155cf',WHITE),
    'blue-hover':('#294bca','#294bca',WHITE),'blue-pressed':('#233faa','#233faa',WHITE),
    'blue-focus':(BLUE,'#172f8f',WHITE),'soft':('#eaf0ff','#bfcef8',WHITE),
    'ghost':(WHITE,WHITE,WHITE),'card':('#f6f8fc','#e0e6f0',WHITE),
    'first-card':('#eef2ff','#d5dffd',WHITE),'tab':(WHITE,WHITE,WHITE),
    'tab-hover':('#f2f5fc','#f2f5fc',WHITE),'tab-on':('#eef2ff','#eef2ff',WHITE),
    'header':('#263650','#3e516f',BAR),'header-focus':('#2e4162','#9bb4ff',BAR),
    'header-cta':(BLUE,BLUE,BAR),'header-cta-hover':('#496be7','#718bf1',BAR),
    'idle':('#28374d','#40536d',BAR),'pass':('#173e3e','#2a6259',BAR),
    'busy':('#273d69','#3f5c95',BAR),'warn':('#463828','#76603a',BAR),
}
SAMPLE = 6


def sheet(multiplier, filename):
    assets = []
    def draw_asset(key,w,h,matte,draw_fn):
        img = Image.new('RGBA',(w*SAMPLE,h*SAMPLE))
        draw_fn(ImageDraw.Draw(img), SAMPLE)
        size = (round(w*multiplier),round(h*multiplier))
        img = img.resize(size,Image.Resampling.LANCZOS)
        solid = Image.new('RGBA',size,matte)
        solid.alpha_composite(img)
        assets.append((key,solid.convert('RGB'),img.getchannel('A')))
    for tone,(ink,matte) in ICON_TONES.items():
        for name,paths in PATHS.items():
            def icon(ctx,s):
                unit=16*s/24
                for path in paths:
                    ctx.line([(round(x*unit),round(y*unit)) for x,y in path],fill=ink,width=round(1.85*unit),joint='curve')
                for box in CIRCLES.get(name,[]):
                    ctx.ellipse(tuple(round(v*unit) for v in box),outline=ink,width=round(1.85*unit))
            draw_asset('icon/'+name+'/'+tone,16,16,matte,icon)
    for name,(fill,edge,matte) in SURFACES.items():
        def surface(ctx,s):
            box=(1,1,24*s-2,24*s-2)
            if name.startswith('tab'):
                ctx.rectangle(box,fill=fill)
                if name=='tab-on':ctx.rectangle((0,22*s,24*s-1,24*s-1),fill=BLUE)
            else:
                ctx.rounded_rectangle(box,radius=(9 if name in ('idle','pass','busy','warn') else 5)*s,
                                      fill=fill,outline=edge,width=2*s if 'focus' in name else s)
        draw_asset('surface/'+name,24,24,matte,surface)
    def circuit(ctx,s):
        ctx.rounded_rectangle((1,1,36*s-2,36*s-2),radius=9*s,fill='#3c60e3',outline='#7694ff',width=s)
        for points in [[(6,18),(12,18),(16,13),(26,13)],[(15,15),(19,23),(26,23)]]:
            ctx.line([(x*s,y*s) for x,y in points],fill='#e9f2ff',width=2*s,joint='curve')
        for x,y in ((7,18),(27,13),(27,23)):
            ctx.ellipse(((x-2)*s,(y-2)*s,(x+2)*s,(y+2)*s),fill='#99f0e3')
    draw_asset('brand',36,36,BAR,circuit)
    if filename=='large':
        launcher=Image.new('RGBA',(36*SAMPLE,36*SAMPLE))
        circuit(ImageDraw.Draw(launcher),SAMPLE)
        launcher.resize((128,128),Image.Resampling.LANCZOS).save(DEST/'clocktree7.png')
    # The empty workspace shows a chip with a clock pulse, separate from any result.
    def emblem(ctx,s):
        ctx.rounded_rectangle((1,1,74*s-2,66*s-2),radius=13*s,fill='#1a2c47',outline='#395578',width=s)
        ctx.rounded_rectangle((19*s,15*s,55*s,51*s),radius=5*s,fill='#213a62',outline='#99b9ef',width=2*s)
        for n in (25,33,41,49):
            for a,b in [((n,10),(n,14)),((n,52),(n,56)),((14,n-4),(18,n-4)),((56,n-4),(60,n-4))]:
                ctx.line((a[0]*s,a[1]*s,b[0]*s,b[1]*s), fill='#6382aa',width=2*s)
        ctx.line([(x*s,y*s) for x,y in ((23,35),(30,35),(30,26),(37,26),(37,39),(44,39),(44,32),(51,32))],
                 fill='#83e4d8',width=2*s)
    draw_asset('empty',74,66,BOARD,emblem)
    width=round(640*multiplier)
    layout={}
    x=y=row_h=0
    for key,img,_ in assets:
        if x+img.width>width:
            x,y,row_h=0,y+row_h,0
        layout[key]=[x,y,x+img.width,y+img.height]
        x+=img.width
        row_h=max(row_h,img.height)
    size=(width,y+row_h)
    atlas=Image.new('RGB',size,WHITE)
    transparent=Image.new('L',size,255)
    for key,img,alpha in assets:
        box=layout[key]
        atlas.paste(img,box[:2])
        if key.startswith('surface/'):
            transparent.paste(0,box)
        else:
            transparent.paste(alpha.point(lambda v:255 if v<12 else 0),box[:2])
    palette=atlas.quantize(colors=255,dither=Image.Dither.NONE)
    palette.paste(255,(0,0,*size),transparent)
    palette.save(DEST/(filename+'.gif'),transparency=255,optimize=False)
    return layout


if __name__=='__main__':
    DEST.mkdir(parents=True,exist_ok=True)
    atlas = {name:sheet(scale,name) for scale,name in ((1,'native'),(1.5,'medium'),(2,'large'))}
    # The packing is proportional at all three sizes; store only canonical coordinates.
    (DEST/'atlas.json').write_text(json.dumps(atlas['native'],sort_keys=True,separators=(',',':'))+'\n',encoding='utf-8')
