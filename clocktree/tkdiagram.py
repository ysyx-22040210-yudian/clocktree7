"""Native Tk Canvas clock architecture viewer with viewport clipping and a minimap."""
import tkinter as tk
from tkinter import font as tkfont
import time
from collections import OrderedDict

from .viewmodel import GraphView
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
    tooltips = {}
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
                cache=getattr(canvas,'_text_cache',None)
                if cache is None:cache={};canvas._text_cache=cache
                key=(font,text,round(maximum))
                if key in cache:text=cache[key]
                else:
                    if metric.measure(text)>maximum:
                        lo,hi=0,len(text)
                        while lo<hi:
                            mid=(lo+hi+1)//2
                            if metric.measure(text[:mid]+'…')<=maximum:lo=mid
                            else:hi=mid-1
                        text=text[:lo]+'…'
                    if len(cache)>=8192:cache.clear()
                    cache[key]=text
            item = canvas.create_text(x+p['x']*scale,y+p['y']*scale,text=text,fill=p['fill'],font=font,anchor=p['anchor'],tags=tuple(tags)+('detail',))
            if p.get('tooltip'):tooltips[item] = p['tooltip']
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
    return tooltips


class ClockDiagram(tk.Frame):
    def __init__(self, master, font_family, on_node, on_edge, on_view, on_hover, on_branch=None):
        super().__init__(master, bg='#101b2e')
        self.family = font_family
        self.on_node, self.on_edge, self.on_view, self.on_hover = on_node, on_edge, on_view, on_hover
        self.on_branch = on_branch
        self.model = None
        self.tree_type = 'clock'
        self.mode, self.component, self.scope, self.depth = 'instances', None, '', 5
        self.scale, self.ox, self.oy = 1., 0., 0.
        self.selected, self.highlights = None, set()
        self.rendered_nodes, self.rendered_wires = {}, {}
        self.port_tooltips = {}
        self._font_cache = {}
        self.runtime_journal=None
        self.runtime_context={}
        self._record_first_paint=False
        self.visible_nodes = 0
        self._pending, self._drag = None, None
        self.layout_mode = 'branch'
        self.layout_pending = False
        self._layout_job = None
        self._layout_poll = None
        self._committed_view = None
        self._layout_cache=OrderedDict()
        self._zoom_active = False
        self._zoom_timer = None
        self._branch_highlight = False
        self._history, self._future = [], []
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
        self.canvas.bind('<Leave>', lambda e: self.on_hover('滚轮缩放 · 拖动画布平移 · 双击器件打开分支画布 · 双击聚合框展开'))
        from .canvas_scene import CanvasScene
        self.scene = CanvasScene(self)
        self.bind('<Destroy>', self._destroyed, add='+')

    def _destroyed(self,event):
        if event.widget is self:
            self.cancel_layout(restore=False)
            for attribute in ('_pending','_zoom_timer'):
                timer=getattr(self,attribute,None)
                if timer is not None:self.after_cancel(timer);setattr(self,attribute,None)

    def cancel_layout(self, restore=True):
        if self._layout_poll is not None:
            self.after_cancel(self._layout_poll);self._layout_poll=None
        if self._layout_job is not None:
            self._layout_job.close();self._layout_job=None
        self.layout_pending=False
        if restore and self.model and self._committed_view and self._committed_view[0] is self.model:
            for key,value in self._committed_view[1].items():setattr(self.model,key,value)
            for key,value in self._committed_view[2].items():setattr(self,key,value)
            self.canvas.delete('layout-progress');self.scene.reset();self.schedule();self.notify()
        elif restore and self.model:
            self.model.nodes,self.model.edges,self.model.positions,self.model.routes={},[],{},{}
            for key in ('node_columns','component_tops','layer_spans','layer_columns'):setattr(self.model,key,{})
            self.canvas.delete('layout-progress');self.scene.reset();self.schedule()
        if restore:self._stage('end','layout','已取消当前布局计算')

    def _calculate_layout(self, fit=True, after=None):
        from .layout_job import LayoutJob
        self.cancel_layout(restore=False)
        self.model.layout_mode=self.layout_mode
        cache_key=(self.mode,self.component,self.scope,self.depth,getattr(self.model,'page',0),getattr(self.model,'focus',None),
                   self.layout_mode,getattr(self.model,'subsystem_references',True),tuple(sorted(getattr(self.model,'expanded_buses',()))))
        def finish(data):
            for key,value in data.items():setattr(self.model,key,value)
            self.layout_pending=False
            self._committed_view=(self.model,{key:getattr(self.model,key) for key in ('nodes','edges','positions','routes','bounds',
                'node_columns','component_tops','layer_spans','layer_columns','actual_count','layout_mode')},
                {key:getattr(self,key) for key in ('mode','component','scope','depth','layout_mode')})
            for key in ('page','pages','focus','external_edges'):
                if hasattr(self.model,key):self._committed_view[1][key]=getattr(self.model,key)
            self.scene.reset();self._record_first_paint=True
            self._stage('end','layout','架构布局已完成',boxes=len(self.model.nodes))
            if fit:self.fit()
            else:self.schedule()
            if after:after()
            self.notify()
        if cache_key in self._layout_cache:
            data=self._layout_cache.pop(cache_key);self._layout_cache[cache_key]=data
            finish(data);return
        self.layout_pending=True
        self._layout_job=LayoutJob(self.model)
        job=self._layout_job
        self.canvas.delete('layout-progress')
        self.canvas.create_text(18,52,text='正在计算布局… 可继续切换视图或取消',anchor='w',fill='#d4e4fb',tags=('layout-progress',))
        def check():
            self._layout_poll=None
            if job is not self._layout_job:return
            if job.poll() is None:
                self._layout_poll=self.after(25,check);return
            try:
                data=job.result()
            except Exception as error:
                self.on_hover('布局失败：'+str(error))
                self._stage('failed','layout',str(error))
                self.cancel_layout();return
            job.close();self._layout_job=None;self.layout_pending=False
            self._layout_cache[cache_key]=data
            while len(self._layout_cache)>3 or sum(len(v['positions'])+len(v['routes']) for v in self._layout_cache.values())>30000:
                self._layout_cache.popitem(last=False)
            finish(data)
        self._layout_poll=self.after(25,check)

    def load(self, graph):
        self.cancel_layout(restore=False)
        self._committed_view=None
        self._layout_cache.clear()
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
        self._history,self._future=[],[]
        self._branch_highlight=False
        self.model.defer_layout=True
        self.rebuild(record=False)
        self._stage('end','layout','视图模型和初始布局已建立')

    def clear(self, mode='clock'):
        self.cancel_layout(restore=False)
        self._committed_view=None
        self._layout_cache.clear()
        self.scene.reset()
        self.tree_type=mode
        self.model=None
        self.selected,self.highlights=None,set()
        self.scale,self.ox,self.oy=1.,0.,0.
        self.mode,self.component,self.scope,self.depth='instances',None,'',5
        self.rendered_nodes,self.rendered_wires={},{}
        self.port_tooltips={}
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
        self.mode = getattr(self.model, 'effective_mode', self.mode)
        self._calculate_layout(fit)
        self.notify()

    def notify(self):
        if self.model:
            self.on_view({'mode': self.mode, 'scope': self.scope, 'component': self.component,
                          'layout_mode': self.layout_mode,
                          'boxes': len(self.model.nodes), 'actual': self.model.actual_count,
                          'total': len(self.model.original), 'zoom': self.scale,
                          'focus': getattr(self.model, 'focus', None), 'page': getattr(self.model, 'page', 0),
                          'pages': getattr(self.model, 'pages', 1), 'external_edges': getattr(self.model, 'external_edges', 0)})

    def page_view(self, delta):
        if self.model and hasattr(self.model, 'page'):
            self.model.page += delta
            self.selected = None
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
        self._zoom_active=True
        self._zoom_last=time.perf_counter()
        if self._zoom_timer is not None:self.after_cancel(self._zoom_timer)
        self._zoom_timer=self.after(110,self._settle_zoom)
        self.schedule()
        self.notify()

    def _settle_zoom(self):
        self._zoom_timer=None
        self._zoom_active=False
        self.schedule()

    def reset_scope(self):
        self.scope, self.depth = '', 5
        self.mode = 'hierarchy' if self.model and len(self.model.original) > 400 else 'instances'
        self.rebuild()

    def expand(self, node):
        if node.get('kind')=='subsystem':
            actual=node.get('reference_of',node['id'])
            values=getattr(self.model,'expanded_buses',set())
            if actual in values:values.remove(actual)
            else:values.add(actual)
            self.model.expanded_buses=values
            self.rebuild(fit=False)
            return
        if not node.get('aggregate'):
            return
        self.scope = node['path']
        self.depth = len(self.scope.split('.')) + 1
        self.mode = 'instances' if len(node['members']) <= 160 else 'hierarchy'
        self.selected = None
        self.rebuild()

    def locate(self, node, record=True):
        if not self.model:
            return
        if record:self._remember_position()
        actual=node.get('reference_of',node['id'])
        keys=[key for key,n in self.model.nodes.items() if key == node['id'] or n.get('reference_of') == actual]
        def center():
            candidates=[key for key,n in self.model.nodes.items() if key == node['id'] or key == actual or n.get('reference_of') == actual]
            if not candidates:return
            key=candidates[0]
            x,y,w,h=self.model.positions[key]
            self.scale=min(1.05,max(.7,self.canvas.winfo_width()/1100))
            self.ox=self.canvas.winfo_width()/2-(x+w/2)*self.scale
            self.oy=self.canvas.winfo_height()/2-(y+h/2)*self.scale
            self.select(self.model.nodes[key]);self.notify()
        if keys and not self.layout_pending:
            center();return
        self.mode, self.component, self.scope = 'instances', None, ''
        from .indexed_view import DETAIL_LIMIT
        if hasattr(self.model, 'project_focus') and len(self.model.original) > DETAIL_LIMIT:
            self._stage('begin','layout','定位实例并读取局部连接',instance=node['id'])
            try:self.model.project_focus(self.model.original[actual])
            except BaseException as error:
                if self.runtime_journal:self.runtime_journal.fail(error)
                raise
            self._stage('end','layout','实例局部布局已完成',instance=node['id'])
        else:
            self.model.project(self.mode,self.component,self.scope,self.depth)
        self._calculate_layout(False,center)

    def _position(self):
        return dict(selected=self.selected,scale=self.scale,ox=self.ox,oy=self.oy,
                    mode=self.mode,component=self.component,scope=self.scope,depth=self.depth,
                    focus=getattr(self.model,'focus',None),page=getattr(self.model,'page',0))

    def _remember_position(self):
        if self.model and not self.layout_pending:
            self._history.append(self._position());self._history=self._history[-100:];self._future=[]

    def navigate_history(self,forward=False):
        source,target=(self._future,self._history) if forward else (self._history,self._future)
        if not source or not self.model:return
        state=source.pop();target.append(self._position())
        same=all(getattr(self,k)==state[k] for k in ('mode','component','scope','depth')) and getattr(self.model,'focus',None)==state['focus'] and getattr(self.model,'page',0)==state['page']
        def restore():
            for k in ('scale','ox','oy','selected'):setattr(self,k,state[k])
            node=self.model.nodes.get(self.selected)
            if node:self.on_node(node)
            self.schedule();self.notify()
        if same:restore();return
        for k in ('mode','component','scope','depth'):setattr(self,k,state[k])
        if hasattr(self.model,'page'):self.model.page=state['page']
        if state['focus']:self.model.project_focus(self.model.original[state['focus']])
        else:self.model.project(self.mode,self.component,self.scope,self.depth)
        self._calculate_layout(False,restore)

    def select(self, node):
        self.selected = node['id']
        if not self._branch_highlight:
            self.highlights = set()
            actual=node.get('reference_of',node['id'])
            if node.get('reference_of'):
                self.highlights={edge['id'] for edge in self.model.edges if edge['target']==node['id']}
            elif hasattr(self.model, 'selection_edges'):
                self.highlights = self.model.selection_edges(self.model.original[actual] if node.get('reference_of') else node)
            else:
                members = node.get('members', [actual])
                for key in members:
                    self.highlights.update(e['id'] for e in self.model.incoming[key] + self.model.outgoing[key])
        self.schedule()
        self.on_node(node)

    def trace(self, direction):
        selected=self.model.nodes.get(self.selected,{}) if self.model else {}
        actual=selected.get('reference_of',self.selected)
        if self.model and actual in self.model.original:
            self._branch_highlight=True
            if selected.get('reference_of') and selected.get('kind')=='subsystem':
                self.highlights = self.model.path_edges(actual,'upstream',seed_pins=selected['inputs']) if direction!='downstream' else set()
            elif direction == 'branch':
                if hasattr(self.model,'branch_edges'):self.highlights=self.model.branch_edges(actual)
                else:self.highlights=self.model.path_edges(actual,'upstream') | self.model.path_edges(actual,'downstream')
            else:self.highlights = self.model.path_edges(actual, direction)
            self.schedule()
            return len(self.highlights)
        return 0

    def clear_highlight(self):
        self._branch_highlight=False;self.highlights=set();self.schedule()

    def fit_highlight(self, projected=False):
        if self.layout_pending:return
        if not projected and self.highlights and (getattr(self.model,'focus',None) or self.scope or getattr(self.model,'pages',1)>1):
            self.component,self.scope,self.depth=None,'',5
            self.mode='hierarchy' if len(self.model.original)>400 else 'instances'
            self.model.project(self.mode,self.component,self.scope,self.depth)
            self._calculate_layout(False,lambda:self.fit_highlight(True));return
        self.scene.prepare();self.scene.highlights()
        keys=set()
        for e in self.model.edges:
            if e['id'] in self.scene.highlighted:keys.update((e['source'],e['target']))
        if not keys:return
        boxes=[self.model.positions[k] for k in keys]
        x1,y1=min(p[0] for p in boxes),min(p[1] for p in boxes)
        x2,y2=max(p[0]+p[2] for p in boxes),max(p[1]+p[3] for p in boxes)
        self.scale=min((self.canvas.winfo_width()-40)/(x2-x1),(self.canvas.winfo_height()-80)/(y2-y1))
        self.ox=20-x1*self.scale;self.oy=54-y1*self.scale
        self.schedule();self.notify()

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
        if self.layout_pending:return
        if self.model:
            self.scene.paint()
            if not self._zoom_active and hasattr(self,'_zoom_last'):
                self.canvas.update_idletasks()
                self.last_detail_restore_ms=(time.perf_counter()-self._zoom_last)*1000
                del self._zoom_last
            return
        c = self.canvas
        c.delete('all')
        self.rendered_nodes, self.rendered_wires = {}, {}
        self.port_tooltips = {}
        cw, ch = c.winfo_width(), c.winfo_height()
        if not self.model:
            c.create_text(cw/2, ch/2-24, text='RTL RESET ARCHITECTURE' if self.tree_type=='reset' else 'RTL CLOCK ARCHITECTURE', fill='#8ea9ce', font=(self.family, 18, 'bold'))
            c.create_text(cw/2, ch/2+14, text='默认两级 · 可添加层级 · 配置规则后开始提取', fill='#617d9f', font=(self.family, 11))
            return
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
                    self._remember_position()
                    self.select(hit[1])
                else:
                    self.on_edge(hit[1])

    def _double(self, event):
        hit = self._hit(event.x, event.y)
        if hit and hit[0]=='node':
            self._drag=None
            if hit[1].get('aggregate'):
                self.expand(hit[1])
            elif self.on_branch is not None:
                self.on_branch(hit[1])
            else:
                self.locate(hit[1])
            return 'break'

    def _hover(self, event):
        if self._drag:
            return
        hit = self._hit(event.x,event.y)
        self.canvas.configure(cursor='hand2' if hit else 'arrow')
        if hit:
            if hit[0]=='node':
                node=hit[1]
                label=next((self.port_tooltips[item] for item in reversed(self.canvas.find_overlapping(event.x-2,event.y-2,event.x+2,event.y+2)) if item in self.port_tooltips),None)
                self.on_hover(label.replace('\n',' · ') if label else node.get('path', node['id']))
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
