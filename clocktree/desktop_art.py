"""Theme artwork shared by all native windows in the same Tcl interpreter."""
import json
from pathlib import Path
import tkinter as tk


DIRECTORY = Path(__file__).resolve().parent / 'artwork'


class DesktopArt:
    def __init__(self, root):
        dpi_scale = float(root.tk.call('tk','scaling'))
        name,self.scale = ('large',2) if dpi_scale>=2.35 else ('medium',1.5) if dpi_scale>=1.72 else ('native',1)
        self.root = root
        self.sheet = tk.PhotoImage(master=root,file=str(DIRECTORY/(name+'.gif')))
        self.rects = json.loads((DIRECTORY/'atlas.json').read_text(encoding='utf-8'))
        self.cache = {}

    def photo(self, key):
        if key not in self.cache:
            box=[round(v*self.scale) for v in self.rects[key]]
            part=tk.PhotoImage(master=self.root,width=box[2]-box[0],height=box[3]-box[1])
            self.root.tk.call(str(part),'copy',str(self.sheet),'-from',*box)
            self.cache[key]=part
        return self.cache[key]


def artwork(widget):
    root=widget._root()
    if not hasattr(root,'_desktop_art'):
        root._desktop_art=DesktopArt(root)
    return root._desktop_art


def glyph(widget,name,tone='ink'):
    return artwork(widget).photo('icon/{}/{}'.format(name,tone))


def draw_grid(canvas,width,height):
    """One native stippled item, independent of scale and design size."""
    if canvas.find_withtag('paper'):
        canvas.coords('paper',0,0,width,height)
    else:
        canvas.create_rectangle(0,0,width,height,outline='',fill='#344860',
                                stipple='@'+str(DIRECTORY/'paper.xbm'),tags='paper')
