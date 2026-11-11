"""Retained Tk scene: transforms existing items and updates viewport differences."""
from collections import Counter

from .devices import DEVICE_TYPES
from .layout import SpatialIndex


class CanvasScene:
    def __init__(self, diagram):
        self.diagram = diagram
        self.signature = None
        self.nodes, self.wires = {}, {}
        self.transform = None
        self.highlight_signature = None
        self.highlighted = set()
        self.map_signature = None

    def reset(self):
        d = self.diagram
        d.canvas.delete('all')
        d.rendered_nodes.clear(); d.rendered_wires.clear(); d.port_tooltips.clear()
        self.nodes.clear(); self.wires.clear()
        self.transform = None
        self.signature = None
        self.map_signature = None
        self.segments={}
        self.node_index=self.wire_index=None

    def prepare(self):
        d, m = self.diagram, self.diagram.model
        signature = id(m.positions)
        if signature == self.signature: return
        self.reset()
        self.signature = signature
        if getattr(m,'layout_mode','branch')=='layers':
            for layer,(x,w) in m.layer_spans.items():
                d.canvas.create_rectangle(x*d.scale+d.ox,34,x*d.scale+d.ox+w*d.scale,m.bounds[3]*d.scale+d.oy,
                    fill='#142138' if layer%2 else '#122035',outline='',tags=('world','layer-band'))
            self.transform=(d.scale,d.ox,d.oy)
        self.node_index = SpatialIndex({key:(x,y,x+w,y+h) for key,(x,y,w,h) in m.positions.items()})
        self.segments, groups = {}, {}
        for edge in m.edges:
            points = m.edge_points(edge)
            for i,(a,b) in enumerate(zip(points,points[1:])):
                key = tuple(a)+tuple(b)+(i == len(points)-2, edge['source'], edge['source_pin'])
                if key not in groups:
                    index = len(groups)
                    groups[key] = index
                    self.segments[index] = {'a':a,'b':b,'arrow':i == len(points)-2,'edges':[]}
                self.segments[groups[key]]['edges'].append(edge)
        self.wire_index = SpatialIndex({key:(min(s['a'][0],s['b'][0]),min(s['a'][1],s['b'][1]),
                                                max(s['a'][0],s['b'][0]),max(s['a'][1],s['b'][1]))
                                       for key,s in self.segments.items()})
        self.highlight_signature = None
        self.layer_counts = Counter(n['layer'] for n in m.nodes.values())
        self.draw_map_base()

    def highlights(self):
        d = self.diagram
        signature = id(d.highlights)
        if signature == self.highlight_signature: return
        self.highlight_signature = signature
        self.highlighted = set()
        # One SQL membership decision per projected edge when the selection
        # changes, never per segment per frame. Exact instance edges are joined
        # in one query rather than issuing thousands of point SELECTs.
        selected = d.highlights
        if hasattr(selected, 'sql'):
            plain = {e['id'] for e in d.model.edges if 'members' not in e}
            if plain:
                conn = selected.store.connection()
                self.highlighted.update(row[0] for row in conn.execute(selected.sql,selected.params) if row[0] in plain)
            for edge in d.model.edges:
                if 'members' in edge and selected.intersection(edge['members']): self.highlighted.add(edge['id'])
        elif selected:
            for edge in d.model.edges:
                if selected.intersection(edge.get('members',[edge['id']])): self.highlighted.add(edge['id'])
        if d._branch_highlight and getattr(d,'_l1_trace_direction',None) in ('upstream','branch'):
            from .l1_inputs import branch_highlights
            self.highlighted.update(branch_highlights(d.model,getattr(d,'_l1_upstream',set()),getattr(d,'_l1_trace_node',{})))
        for key,item in self.wires.items(): self.style_wire(key,item)

    def style_wire(self,key,item):
        d, segment = self.diagram,self.segments[key]
        selected = any(edge['id'] in self.highlighted for edge in segment['edges'])
        reverse = any(edge.get('layer_delta',0)<0 for edge in segment['edges'])
        cycle = any(d.model.node_columns[e['source']]==d.model.node_columns[e['target']] for e in segment['edges'])
        d.canvas.itemconfigure(item,fill='#78caff' if selected else ('#ef928a' if reverse or cycle else '#4b6c96'),
                               width=2.6 if selected else 1.2,dash=(5,3) if cycle else ())

    def paint(self):
        from .tkdiagram import draw_device
        d, c, m = self.diagram,self.diagram.canvas,self.diagram.model
        self.prepare(); self.highlights()
        scale,ox,oy = d.scale,d.ox,d.oy
        cw,ch = c.winfo_width(),c.winfo_height()
        if self.transform is not None:
            old,px,py = self.transform
            ratio = scale/old
            if ratio != 1: c.scale('world',px,py,ratio,ratio)
            if ox != px or oy != py: c.move('world',ox-px,oy-py)
        self.transform = (scale,ox,oy)
        margin = 100
        bounds = ((-ox-margin)/scale,(-oy-margin)/scale,(cw-ox+margin)/scale,(ch-oy+margin)/scale)
        visible = set(self.node_index.query(bounds))
        wires = set(self.wire_index.query(bounds))
        for key in set(self.nodes)-visible:
            tag,_,_ = self.nodes.pop(key)
            for item in c.find_withtag(tag): d.port_tooltips.pop(item,None)
            c.delete(tag); d.rendered_nodes.pop(tag,None)
        for key in set(self.wires)-wires:
            c.delete(self.wires.pop(key)); d.rendered_wires.pop('w:'+str(key),None)
        for key in wires-self.wires.keys():
            segment = self.segments[key]
            a,b = segment['a'],segment['b']
            tag = 'w:'+str(key)
            item = c.create_line(a[0]*scale+ox,a[1]*scale+oy,b[0]*scale+ox,b[1]*scale+oy,
                                 arrow='last' if segment['arrow'] else 'none',arrowshape=(6,7,3),
                                 tags=('world','wire',tag))
            self.wires[key] = item
            d.rendered_wires[tag] = segment['edges']
            self.style_wire(key,item)
            c.tag_lower(item)
        c.tag_lower('layer-band')
        coarse = scale<.22
        zooming = getattr(d,'_zoom_active',False)
        for key in visible:
            node = m.nodes[key]
            selected = d.selected in (key,node.get('reference_of'))
            old = self.nodes.get(key)
            # Existing detailed geometry is transformed during a wheel burst;
            # text sizes are regenerated just once at the settled zoom.
            issue=getattr(d,'issue_port',(None,None))
            issue_pin=issue[1] if issue[0] and issue[0] in (key,node.get('reference_of')) else None
            navigation_pin=(getattr(d,'selection_target',None) or {}).get('pin') if selected else None
            detail_key = ('coarse' if coarse else 'detail', round(scale,5) if not zooming else None,selected,issue_pin,navigation_pin)
            if old:
                tag,previous,_ = old
                if previous[0] == detail_key[0] and previous[2] == selected and (zooming or previous == detail_key): continue
                for item in c.find_withtag(tag): d.port_tooltips.pop(item,None)
                c.delete(tag)
            else:
                tag='n:'+str(self.next_tag); self.next_tag+=1
            x,y,w,h = m.positions[key]
            tags=('world','node',tag)
            if coarse or zooming:
                color=DEVICE_TYPES.get(node.get('kind'),{}).get('color','#8199b9')
                c.create_rectangle(x*scale+ox,y*scale+oy,(x+w)*scale+ox,(y+h)*scale+oy,
                                   fill=color,outline='#def0ff' if selected else '',tags=tags)
                if zooming and not coarse: detail_key=('temporary',None,selected,issue_pin,navigation_pin)
            else:
                d.port_tooltips.update(draw_device(c,node,x*scale+ox,y*scale+oy,w,h,scale,d.family,selected,tags,d._font_cache))
                if issue_pin or navigation_pin:
                    from .devices import port_offsets,pin_index,auxiliary_port_y,output_offset
                    pin=issue_pin or navigation_pin
                    marker_color='#ff9b78' if issue_pin else '#78caff'
                    auxiliary_y=auxiliary_port_y(node,h,pin)
                    offset=port_offsets(node,w,h)[0][pin_index(node,pin)] if pin in node['inputs'] else output_offset(node,w,h,pin)
                    if auxiliary_y is not None:
                        c.create_rectangle((x+8)*scale+ox,(y+auxiliary_y-17)*scale+oy,
                                           (x+w-8)*scale+ox,(y+auxiliary_y+17)*scale+oy,
                                           outline=marker_color,width=2,tags=tags)
                    elif offset:
                        px,py=(x+offset[0])*scale+ox,(y+offset[1])*scale+oy
                        c.create_oval(px-8,py-8,px+8,py+8,outline=marker_color,width=3,tags=tags)
            self.nodes[key]=(tag,detail_key,None)
            d.rendered_nodes[tag]=node
        c.itemconfigure('detail',state='hidden' if zooming else 'normal')
        d.visible_nodes=len(visible)
        # Fixed status strip, not a rebuilt set of layer bands on each pan.
        header='分支紧凑布局 · L 级数仅作规则标注' if getattr(m,'layout_mode','branch')=='branch' else '按层排列'
        if not c.find_withtag('header'):
            c.create_rectangle(0,0,cw,34,fill='#17263e',outline='',tags=('header','header-bg'))
            c.create_text(12,17,text=header,anchor='w',fill='#97afce',font=(d.family,10),tags=('header',))
        c.coords('header-bg',0,0,cw,34); c.tag_raise('header')
        self.update_map()

    def draw_map_base(self):
        d,m = self.diagram,self.diagram.model
        self.next_tag=0
        c=d.minimap; c.delete('all')
        x1,y1,x2,y2=m.bounds
        ratio=min(160/max(1,x2-x1),84/max(1,y2-y1))
        d._map_transform=ratio,8,7
        bins=Counter((round((x+w/2)*ratio/2),round((y+h/2)*ratio/2)) for x,y,w,h in m.positions.values())
        for (x,y),count in bins.items():
            c.create_rectangle(7+x*2,6+y*2,10+x*2,9+y*2,fill='#8db6e8',outline='')
        self.map_view=c.create_rectangle(1,1,175,97,outline='#54b2ff',width=1.5)

    def update_map(self):
        d=self.diagram
        ratio,px,py=d._map_transform
        left,top=-d.ox/d.scale,-d.oy/d.scale
        right,bottom=(d.canvas.winfo_width()-d.ox)/d.scale,(d.canvas.winfo_height()-d.oy)/d.scale
        d.minimap.coords(self.map_view,max(1,px+left*ratio),max(1,py+top*ratio),
                         min(175,px+right*ratio),min(97,py+bottom*ratio))
