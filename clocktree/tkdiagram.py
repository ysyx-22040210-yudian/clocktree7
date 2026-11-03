"""Native Tk Canvas clock architecture viewer with viewport clipping and a minimap."""
import tkinter as tk
from tkinter import font as tkfont
from collections import Counter

from .viewmodel import GraphView, COLUMN_STEP, clip_segment
from .devices import DEVICE_TYPES, symbol_parts
from .profiles import tree_type


COLORS = ['#55a9ff', '#46c8bb', '#a492f4', '#edb55b', '#df88bb', '#5db7d6', '#91bd72']


def rounded(canvas, x, y, w, h, radius=8, **kwargs):
    r = min(radius, w / 3, h / 3)
    return canvas.create_polygon(x+r,y, x+w-r,y, x+w,y, x+w,y+r,
                                 x+w,y+h-r, x+w,y+h, x+w-r,y+h,
                                 x+r,y+h, x,y+h, x,y+h-r, x,y+r, x,y,
                                 smooth=True, splinesteps=12, **kwargs)


def draw_device(canvas,node,x,y,w,h,scale,family,selected=False,tags=(),font_cache=None):
    """Use exactly the same symbol primitives and port anchors as SVG export."""
    font_cache = font_cache if font_cache is not None else {}
    for p in symbol_parts(node,w,h,selected):
        op=p['op']
        if op=='rect':
            options={'fill':p['fill'],'outline':p['stroke'],'width':p['width'],'tags':tags}
            if p.get('dash'):options['dash']=(4,3)
            rounded(canvas,x+p['x']*scale,y+p['y']*scale,p['w']*scale,p['h']*scale,radius=p['radius']*scale,**options)
        elif op=='text':
            size=max(8 if p['bold'] else 7,int(p['size']*scale))
            font=(family,-size,'bold') if p['bold'] else (family,-size)
            text=p['text']
            if p.get('max_width'):
                if font not in font_cache:font_cache[font]=tkfont.Font(root=canvas,font=font)
                metric=font_cache[font]
                maximum=max(8,p['max_width']*scale)
                if metric.measure(text)>maximum:
                    while text and metric.measure(text+'…')>maximum:text=text[:-1]
                    text+='…'
            canvas.create_text(x+p['x']*scale,y+p['y']*scale,text=text,fill=p['fill'],font=font,anchor=p['anchor'],tags=tags)
        elif op in ('polygon','polyline'):
            points=[v for xx,yy in p['points'] for v in (x+xx*scale,y+yy*scale)]
            if op=='polygon':
                canvas.create_polygon(*points,fill=p['fill'],outline=p['stroke'],width=max(1,p['width']*scale),tags=tags)
            else:
                canvas.create_line(*points,fill=p['fill'],width=max(1,p['width']*scale),tags=tags)
        elif op=='circle':
            xx,yy=x+p['x']*scale,y+p['y']*scale
            r=max(1.5,p['r']*scale)
            canvas.create_oval(xx-r,yy-r,xx+r,yy+r,fill=p['fill'],outline=p['stroke'],width=p['width'],tags=tags)


class ClockDiagram(tk.Frame):
    def __init__(self, master, font_family, on_node, on_edge, on_view, on_hover):
        super().__init__(master, bg='#101b2e')
        self.family = font_family
        self.on_node, self.on_edge, self.on_view, self.on_hover = on_node, on_edge, on_view, on_hover
        self.model = None
        self.tree_type = 'clock'
        self.mode, self.component, self.scope, self.depth = 'instances', None, '', 5
        self.scale, self.ox, self.oy = 1., 0., 0.
        self.selected, self.highlights = None, set()
        self.rendered_nodes, self.rendered_wires = {}, {}
        self._font_cache = {}
        self.runtime_journal=None
        self.runtime_context={}
        self._record_first_paint=False
        self.visible_nodes = 0
        self._pending, self._drag = None, None
        self.canvas = tk.Canvas(self, bg='#101b2e', highlightthickness=0, borderwidth=0, cursor='arrow')
        self.canvas.pack(fill='both', expand=True)
        self.map_frame = tk.Frame(self, bg='#273a55', bd=1)
        self.map_label = tk.Label(self.map_frame, text='全图导航', font=(self.family, 8), bg='#1a2a42', fg='#94abc9', anchor='w', padx=9, pady=4)
        self.map_label.pack(fill='x')
        self.minimap = tk.Canvas(self.map_frame, width=176, height=98, bg='#132238', highlightthickness=0)
        self.minimap.pack()
        self.map_frame.place(relx=1, rely=1, anchor='se', x=-18, y=-18)
        self.canvas.bind('<Configure>', lambda e: self.schedule())
        self.canvas.bind('<ButtonPress-1>', self._press)
        self.canvas.bind('<B1-Motion>', self._motion)
        self.canvas.bind('<ButtonRelease-1>', self._release)
        self.canvas.bind('<Double-Button-1>', self._double)
        self.canvas.bind('<ButtonPress-2>', self._press)
        self.canvas.bind('<B2-Motion>', self._motion)
        self.canvas.bind('<ButtonRelease-2>', self._release)
        self.canvas.bind('<MouseWheel>', self._wheel)
        self.canvas.bind('<Button-4>', lambda e: self.zoom(1.15, e.x, e.y))
        self.canvas.bind('<Button-5>', lambda e: self.zoom(1/1.15, e.x, e.y))
        self.canvas.bind('<Motion>', self._hover)
        self.minimap.bind('<Button-1>', self._map_click)
        self.canvas.bind('<Leave>', lambda e: self.on_hover('滚轮缩放 · 拖动画布平移 · 双击聚合框展开'))

    def load(self, graph):
        self.tree_type = tree_type(graph.get('config', graph))
        from .storage import IndexedGraph
        from .indexed_view import IndexedGraphView
        self._stage('begin','layout','建立架构视图模型')
        try:self.model = IndexedGraphView(graph) if isinstance(graph, IndexedGraph) else GraphView(graph)
        except BaseException as error:
            if self.runtime_journal:self.runtime_journal.fail(error)
            raise
        self.mode = 'hierarchy' if len(graph['nodes']) > 400 else 'instances'
        self.component, self.scope, self.depth = None, '', 5
        self.selected, self.highlights = None, set()
        self.rebuild(record=False)
        self._stage('end','layout','视图模型和初始布局已建立')

    def clear(self, mode='clock'):
        self.tree_type=mode
        self.model=None
        self.selected,self.highlights=None,set()
        self.scale,self.ox,self.oy=1.,0.,0.
        self.mode,self.component,self.scope,self.depth='instances',None,'',5
        self.rendered_nodes,self.rendered_wires={},{}
        self.minimap.delete('all')
        self.schedule()

    def _stage(self,kind,stage,detail,**context):
        if self.runtime_journal:
            self.runtime_journal.emit(kind,stage,detail,dict(self.runtime_context,**context))

    def rebuild(self, fit=True, record=True):
        if not self.model:
            return
        if record:self._stage('begin','layout','计算架构布局',mode=self.mode,scope=self.scope)
        try:self.model.project(self.mode, self.component, self.scope, self.depth)
        except BaseException as error:
            if self.runtime_journal:self.runtime_journal.fail(error)
            raise
        if record:self._stage('end','layout','架构布局已完成',boxes=len(self.model.nodes))
        self._record_first_paint=True
        self.mode = getattr(self.model, 'effective_mode', self.mode)
        if fit:
            self.fit()
        else:
            self.schedule()
        self.notify()

    def notify(self):
        if self.model:
            self.on_view({'mode': self.mode, 'scope': self.scope, 'component': self.component,
                          'boxes': len(self.model.nodes), 'actual': self.model.actual_count,
                          'total': len(self.model.original), 'zoom': self.scale,
                          'focus': getattr(self.model, 'focus', None), 'page': getattr(self.model, 'page', 0),
                          'pages': getattr(self.model, 'pages', 1), 'external_edges': getattr(self.model, 'external_edges', 0)})

    def page_view(self, delta):
        if self.model and hasattr(self.model, 'page'):
            self.model.page += delta
            self.selected, self.highlights = None, set()
            self.rebuild()

    def fit(self):
        if not self.model:
            return
        self.update_idletasks()
        width, height = max(300, self.canvas.winfo_width()), max(240, self.canvas.winfo_height())
        x1, y1, x2, y2 = self.model.bounds
        self.scale = min((width-36)/(x2-x1), (height-74)/(y2-y1))
        self.ox = (width-(x2-x1)*self.scale)/2 - x1*self.scale
        self.oy = 58 - y1*self.scale
        self.schedule()
        self.notify()

    def zoom(self, factor, x=None, y=None):
        if not self.model:
            return
        x = self.canvas.winfo_width()/2 if x is None else x
        y = self.canvas.winfo_height()/2 if y is None else y
        new = min(3., max(.0001, self.scale*factor))
        factor = new/self.scale
        self.ox, self.oy = x-(x-self.ox)*factor, y-(y-self.oy)*factor
        self.scale = new
        self.schedule()
        self.notify()

    def reset_scope(self):
        self.scope, self.depth = '', 5
        self.mode = 'hierarchy' if self.model and len(self.model.original) > 400 else 'instances'
        self.rebuild()

    def expand(self, node):
        if not node.get('aggregate'):
            return
        self.scope = node['path']
        self.depth = len(self.scope.split('.')) + 1
        self.mode = 'instances' if len(node['members']) <= 160 else 'hierarchy'
        self.selected, self.highlights = None, set()
        self.rebuild()

    def locate(self, node):
        if not self.model:
            return
        self.mode, self.component, self.scope = 'instances', None, ''
        from .indexed_view import DETAIL_LIMIT
        if hasattr(self.model, 'project_focus') and len(self.model.original) > DETAIL_LIMIT:
            self._stage('begin','layout','定位实例并读取局部连接',instance=node['id'])
            try:self.model.project_focus(node)
            except BaseException as error:
                if self.runtime_journal:self.runtime_journal.fail(error)
                raise
            self._stage('end','layout','实例局部布局已完成',instance=node['id'])
            self._record_first_paint=True
            self.schedule()
        else:
            self.rebuild(fit=False)
        node = self.model.original[node['id']]
        x, y, w, h = self.model.positions[node['id']]
        self.scale = min(1.05, max(.7, self.canvas.winfo_width()/1100))
        self.ox = self.canvas.winfo_width()/2 - (x+w/2)*self.scale
        self.oy = self.canvas.winfo_height()/2 - (y+h/2)*self.scale
        self.select(node)
        self.notify()

    def select(self, node):
        self.selected = node['id']
        self.highlights = set()
        if hasattr(self.model, 'selection_edges'):
            self.highlights = self.model.selection_edges(node)
        else:
            members = node.get('members', [node['id']])
            for key in members:
                self.highlights.update(e['id'] for e in self.model.incoming[key] + self.model.outgoing[key])
        self.schedule()
        self.on_node(node)

    def trace(self, direction):
        if self.model and self.selected in self.model.original:
            self.highlights = self.model.path_edges(self.selected, direction)
            self.schedule()
            return len(self.highlights)
        return 0

    def schedule(self):
        if self._pending is None:
            self._pending = self.after(16, self.paint)

    def point(self, x, y):
        return x*self.scale+self.ox, y*self.scale+self.oy

    def paint(self):
        record=self._record_first_paint
        self._record_first_paint=False
        if record:self._stage('begin','canvas','绘制新的架构视图',boxes=len(self.model.nodes))
        try:self._paint()
        except BaseException as error:
            if self.runtime_journal:self.runtime_journal.fail(error)
            raise
        if record:
            self._stage('end','canvas','架构视图绘制完成',visible_nodes=self.visible_nodes)
            self._stage('finished','display','架构图已显示')

    def _paint(self):
        self._pending = None
        c = self.canvas
        c.delete('all')
        self.rendered_nodes, self.rendered_wires = {}, {}
        cw, ch = c.winfo_width(), c.winfo_height()
        if not self.model:
            c.create_text(cw/2, ch/2-24, text='RTL RESET ARCHITECTURE' if self.tree_type=='reset' else 'RTL CLOCK ARCHITECTURE', fill='#8ea9ce', font=(self.family, 18, 'bold'))
            c.create_text(cw/2, ch/2+14, text='默认两级 · 可添加层级 · 配置规则后开始提取', fill='#617d9f', font=(self.family, 11))
            return
        scale = self.scale
        bounds = ((-self.ox)/scale, (48-self.oy)/scale, (cw-self.ox)/scale, (ch-self.oy)/scale)
        for layer in range(1, self.model.layer_count + 1):
            x, _ = self.point(18+(layer-1)*COLUMN_STEP, 0)
            c.create_rectangle(x, 44, x+278*scale, ch, fill='#142138' if layer % 2 else '#122035', outline='')
        # A clipped, deduplicated wire segment can stand for several actual
        # edges sharing a trunk. Its click inspector preserves all edge IDs.
        segments = {}
        for edge in self.model.edges:
            highlighted = bool(self.highlights) and bool(self.highlights.intersection(edge.get('members', [edge['id']])))
            color = '#70bbff' if highlighted else ('#ee8783' if edge['layer_delta'] < 0 else '#4b6c96')
            points = self.model.edge_points(edge)
            for i, (a, b) in enumerate(zip(points, points[1:])):
                segment = clip_segment(a, b, bounds)
                if not segment:
                    continue
                start, end = self.point(*segment[0]), self.point(*segment[1])
                if abs(start[0]-end[0]) + abs(start[1]-end[1]) < .6:
                    continue
                arrow = i == len(points)-2 and segment[1] == b
                key = tuple(round(v, 1) for v in start+end) + (color, arrow)
                entry = segments.setdefault(key, {'coords': start+end, 'color': color, 'arrow': arrow,
                                                   'selected': highlighted, 'edges': []})
                entry['edges'].append(edge)
        for index, segment in enumerate(sorted(segments.values(), key=lambda x: x['selected'])):
            tag = 'w:{}'.format(index)
            self.rendered_wires[tag] = segment['edges']
            c.create_line(*segment['coords'], fill=segment['color'], width=2.3 if segment['selected'] else 1.2,
                          arrow=tk.LAST if segment['arrow'] else tk.NONE, arrowshape=(6, 7, 3), tags=('wire', tag))
        visible = 0
        for index, node in enumerate(self.model.nodes.values()):
            x, y, w, h = self.model.positions[node['id']]
            if x+w < bounds[0] or x > bounds[2] or y+h < bounds[1] or y > bounds[3]:
                continue
            visible += 1
            px, py = self.point(x, y)
            pw, ph = w*scale, h*scale
            tag = 'n:{}'.format(index)
            tags = ('node', tag)
            self.rendered_nodes[tag] = node
            selected = self.selected == node['id']
            color = DEVICE_TYPES.get(node.get('kind'), {}).get('color', '#8199b9')
            if scale < .22:
                c.create_rectangle(px, py, px+max(2,pw), py+max(1.5,ph), fill=color, outline='', tags=tags)
                continue
            draw_device(c,node,px,py,w,h,scale,self.family,selected,tags,self._font_cache)
        self.visible_nodes = visible
        # Fixed layer headers remain readable while panning and zooming.
        c.create_rectangle(0, 0, cw, 44, fill='#17263e', outline='')
        layer_counts = Counter(n['layer'] for n in self.model.nodes.values())
        for layer in range(1, self.model.layer_count + 1):
            xx, _ = self.point(30+(layer-1)*COLUMN_STEP, 0)
            if -100 < xx < cw:
                c.create_text(xx, 21, text='L{}'.format(layer), anchor='w', fill=COLORS[(layer-1) % len(COLORS)], font=(self.family, 10, 'bold'))
                if scale > .34:
                    c.create_text(xx+29, 21, text='{} 图元'.format(layer_counts[layer]), anchor='w', fill='#7f99bb', font=(self.family, 8))
        if not self.model.nodes:
            c.create_text(cw/2, ch/2, text='当前范围没有匹配实例', fill='#94abc9', font=(self.family, 13))
        self._draw_map()

    def _draw_map(self):
        c = self.minimap
        c.delete('all')
        if not self.model:
            return
        width, height = 176, 98
        _, _, ww, hh = self.model.bounds
        ratio = min((width-16)/ww, (height-14)/hh)
        self._map_transform = ratio, 8, 7
        bins = Counter()
        for x, y, w, h in self.model.positions.values():
            bins[(round((x+w/2)*ratio/2), round((y+h/2)*ratio/2))] += 1
        for (x, y), count in bins.items():
            c.create_rectangle(8+x*2-1, 7+y*2-1, 8+x*2+2, 7+y*2+2, fill='#6a98cf' if count<10 else '#9cc3f0', outline='')
        left, top = -self.ox/self.scale, (44-self.oy)/self.scale
        right, bottom = (self.canvas.winfo_width()-self.ox)/self.scale, (self.canvas.winfo_height()-self.oy)/self.scale
        c.create_rectangle(max(1,8+left*ratio),max(1,7+top*ratio),min(width-1,8+right*ratio),min(height-1,7+bottom*ratio), outline='#54b2ff', width=1.5)

    def _hit(self, x, y):
        candidates = self.canvas.find_overlapping(x-4, y-4, x+4, y+4)
        for item in reversed(candidates):
            for tag in self.canvas.gettags(item):
                if tag in self.rendered_nodes:
                    return 'node', self.rendered_nodes[tag]
        for item in reversed(candidates):
            for tag in self.canvas.gettags(item):
                if tag in self.rendered_wires:
                    return 'edge', self.rendered_wires[tag]
        return None

    def _press(self, event):
        self._drag = {'x':event.x, 'y':event.y, 'ox':self.ox, 'oy':self.oy, 'moved':False, 'button':event.num}
        self.canvas.focus_set()

    def _motion(self, event):
        if not self._drag:
            return
        dx, dy = event.x-self._drag['x'], event.y-self._drag['y']
        if abs(dx)+abs(dy)>4:
            self._drag['moved'] = True
            self.canvas.configure(cursor='fleur')
            self.ox, self.oy = self._drag['ox']+dx, self._drag['oy']+dy
            self.schedule()

    def _release(self, event):
        drag, self._drag = self._drag, None
        self.canvas.configure(cursor='arrow')
        if drag and not drag['moved'] and drag['button']==1:
            hit = self._hit(event.x, event.y)
            if hit:
                if hit[0]=='node':
                    self.select(hit[1])
                else:
                    self.on_edge(hit[1])

    def _double(self, event):
        hit = self._hit(event.x, event.y)
        if hit and hit[0]=='node':
            if hit[1].get('aggregate'):
                self.expand(hit[1])
            else:
                self.locate(hit[1])

    def _hover(self, event):
        if self._drag:
            return
        hit = self._hit(event.x,event.y)
        self.canvas.configure(cursor='hand2' if hit else 'arrow')
        if hit:
            if hit[0]=='node':
                node=hit[1]
                self.on_hover(node.get('path', node['id']))
            else:
                self.on_hover('实际连接 · 点击查看驱动 / 负载双向证据')

    def _wheel(self, event):
        self.zoom(1.15 if event.delta>0 else 1/1.15, event.x, event.y)

    def _map_click(self, event):
        if not self.model or not hasattr(self, '_map_transform'):
            return
        ratio, px, py = self._map_transform
        x, y = (event.x-px)/ratio, (event.y-py)/ratio
        self.ox = self.canvas.winfo_width()/2-x*self.scale
        self.oy = self.canvas.winfo_height()/2-y*self.scale
        self.schedule()
