"""Retained Tk scene: transforms existing items and updates viewport differences."""
from collections import Counter

from .devices import device_style
from .div_sources import div_source_level
from .layout import SpatialIndex
from .desktop_art import draw_grid
from .canvas_wires import TrunkInterval, partition_trunks


class CanvasScene:
    def __init__(self, diagram):
        self.diagram = diagram
        self.signature = None
        self.nodes, self.wires = {}, {}
        self.transform = None
        self.highlight_signature = None
        self.highlighted = set()
        self.map_signature = None
        self.grid_size=None
        self.detail_hidden=False

    def reset(self):
        d = self.diagram
        d.canvas.delete('all')
        d.rendered_nodes.clear(); d.rendered_wires.clear(); d.port_tooltips.clear()
        self.nodes.clear(); self.wires.clear()
        self.transform = None
        self.signature = None
        self.map_signature = None
        self.grid_size=None
        self.detail_hidden=False
        self.segments={}
        self.trunks=[]
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
            reverse=edge.get('layer_delta',0)<0
            cycle=m.node_columns[edge['source']]==m.node_columns[edge['target']]
            for i,(a,b) in enumerate(zip(points,points[1:])):
                key = tuple(a)+tuple(b)+(i == len(points)-2, edge['source'], edge['source_pin'])
                if key not in groups:
                    index = len(groups)
                    groups[key] = index
                    self.segments[index] = {'a':a,'b':b,'arrow':i == len(points)-2,'edges':[],'reverse':False,'cycle':False}
                segment=self.segments[groups[key]]
                segment['edges'].append(edge)
                segment['reverse'] |= reverse
                segment['cycle'] |= cycle
        self.trunks=partition_trunks(self.segments)
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
        for trunk in self.trunks:trunk.highlight(self.highlighted)
        for key,item in self.wires.items(): self.style_wire(key,item)

    def style_wire(self,key,item):
        d, segment = self.diagram,self.segments[key]
        edges=segment['edges']
        selected = edges.highlighted() if isinstance(edges,TrunkInterval) else self.highlighted and any(edge['id'] in self.highlighted for edge in edges)
        reverse,cycle=segment['reverse'],segment['cycle']
        d.canvas.itemconfigure(item,fill='#78caff' if selected else ('#ef928a' if reverse or cycle else '#4b6c96'),
                               width=2.6 if selected else 1.2,dash=(5,3) if cycle else ())

    def paint(self):
        from .tkdiagram import draw_device
        d, c, m = self.diagram,self.diagram.canvas,self.diagram.model
        self.prepare(); self.highlights()
        scale,ox,oy = d.scale,d.ox,d.oy
        cw,ch = c.winfo_width(),c.winfo_height()
        if self.grid_size!=(cw,ch):
            draw_grid(c,cw,ch)
            self.grid_size=(cw,ch)
            c.tag_lower('paper')
            c.tag_lower('layer-band')
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
        new_wires=wires-self.wires.keys()
        for key in new_wires:
            segment = self.segments[key]
            a,b = segment['a'],segment['b']
            tag = 'w:'+str(key)
            item = c.create_line(a[0]*scale+ox,a[1]*scale+oy,b[0]*scale+ox,b[1]*scale+oy,
                                 arrow='last' if segment['arrow'] else 'none',arrowshape=(6,7,3),
                                 tags=('world','wire',tag))
            self.wires[key] = item
            d.rendered_wires[tag] = segment['edges']
            self.style_wire(key,item)
        if new_wires:
            c.tag_lower('wire')
            c.tag_lower('paper')
            c.tag_lower('layer-band')
        coarse = scale<.19
        zooming = getattr(d,'_zoom_active',False)
        for key in visible:
            node = m.nodes[key]
            selected = d.selected is not None and d.selected in (key,node.get('reference_of'))
            old = self.nodes.get(key)
            # Existing detailed geometry is transformed during a wheel burst;
            # text sizes are regenerated just once at the settled zoom.
            issue=getattr(d,'issue_port',(None,None))
            issue_pin=issue[1] if issue[0] and issue[0] in (key,node.get('reference_of')) else None
            navigation_pin=(getattr(d,'selection_target',None) or {}).get('pin') if selected else None
            div_level = div_source_level(node, m.div_source_levels)
            detail_key = ('coarse' if coarse else 'detail', round(scale,5) if not zooming else None,
                          selected,issue_pin,navigation_pin,div_level)
            if old:
                tag,previous,_ = old
                if (previous[0] == detail_key[0] and previous[2] == selected and previous[-1] == div_level
                        and (zooming or previous == detail_key)):
                    continue
                for item in c.find_withtag(tag): d.port_tooltips.pop(item,None)
                c.delete(tag)
            else:
                tag='n:'+str(self.next_tag); self.next_tag+=1
            x,y,w,h = m.positions[key]
            tags=('world','node',tag)
            if coarse or zooming:
                style=device_style(node.get('kind'),div_level)
                c.create_rectangle(x*scale+ox,y*scale+oy,(x+w)*scale+ox,(y+h)*scale+oy,
                                   fill=style['fill'],outline='#def0ff' if selected else style['color'],tags=tags)
                if zooming and not coarse:
                    detail_key = ('temporary',) + detail_key[1:]
            else:
                d.port_tooltips.update(draw_device(c,node,x*scale+ox,y*scale+oy,w,h,scale,d.family,
                                                   selected,tags,d._font_cache,div_level,overview=scale<.47))
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
        if zooming!=self.detail_hidden:
            c.itemconfigure('detail',state='hidden' if zooming else 'normal')
            self.detail_hidden=zooming
        d.visible_nodes=len(visible)
        self.update_map()

    def draw_map_base(self):
        d,m = self.diagram,self.diagram.model
        self.next_tag=0
        c=d.minimap; c.delete('all')
        x1,y1,x2,y2=m.bounds
        ratio=min(160/max(1,x2-x1),84/max(1,y2-y1))
        px,py=88-(x1+x2)*ratio/2,49-(y1+y2)*ratio/2
        d._map_transform=ratio,px,py
        if len(m.positions)<=96 and len(m.edges)<=256:
            for edge in m.edges:
                points=[v for x,y in m.edge_points(edge) for v in (px+x*ratio,py+y*ratio)]
                if len(points)>=4:c.create_line(*points,fill='#526d8e',tags='map-route')
            for key,(x,y,w,h) in m.positions.items():
                color=device_style(m.nodes[key].get('kind'),div_source_level(m.nodes[key],m.div_source_levels))['color']
                nx,ny=px+x*ratio,py+y*ratio
                c.create_rectangle(nx,ny,nx+max(2,w*ratio),ny+max(2,h*ratio),fill=color,outline='',tags='map-node')
        else:
            bins=Counter((round((px+(x+w/2)*ratio)/2),round((py+(y+h/2)*ratio)/2)) for x,y,w,h in m.positions.values())
            for (x,y),count in bins.items():
                c.create_rectangle(x*2-1,y*2-1,x*2+2,y*2+2,fill='#94b6e6' if count==1 else '#61caca',outline='')
        self.map_view=c.create_rectangle(1,1,175,97,outline='#54b2ff',width=1.5)

    def update_map(self):
        d=self.diagram
        ratio,px,py=d._map_transform
        left,top=-d.ox/d.scale,-d.oy/d.scale
        right,bottom=(d.canvas.winfo_width()-d.ox)/d.scale,(d.canvas.winfo_height()-d.oy)/d.scale
        clip_x=lambda x:max(1,min(175,px+x*ratio))
        clip_y=lambda y:max(1,min(97,py+y*ratio))
        d.minimap.coords(self.map_view,clip_x(left),clip_y(top),clip_x(right),clip_y(bottom))
