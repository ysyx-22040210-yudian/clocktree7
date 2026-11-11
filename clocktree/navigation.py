"""Searchable neighbour chooser with bounded pages and exact port connections."""
import tkinter as tk
from tkinter import ttk
from .devices import connection_label
from .connectivity import focused_pins


def _neighbour_filter(node,direction):
    instance=node.get('reference_of',node['id'])
    endpoint='target' if direction=='upstream' else 'source'
    where=endpoint+'=?';params=[instance]
    if node.get('reference_of'):
        where+=' AND source=?';params.append(node.get('reference_source'))
    pins=focused_pins(node,direction)
    if pins is not None:
        where+=' AND '+endpoint+'_pin IN ('+','.join('?' for p in pins)+')';params.extend(sorted(pins))
    return where,params


def neighbour_edges(model,node,direction):
    instance=node.get('reference_of',node['id'])
    if node.get('reference_of') and (direction!='upstream' or not node.get('reference_source')):
        return []
    if hasattr(model,'store'):
        from .indexed_view import QuerySequence
        where,params=_neighbour_filter(node,direction)
        return QuerySequence(model.store,'SELECT data FROM edges WHERE '+where+' ORDER BY seq',params,decode=True)
    edges=model.incoming[instance] if direction=='upstream' else model.outgoing[instance]
    if node.get('reference_of'):
        edges=[e for e in edges if e['source']==node.get('reference_source') and e['target_pin'] in node['inputs']]
    pins=focused_pins(node,direction)
    if pins is not None:edges=[e for e in edges if e[('target_pin' if direction=='upstream' else 'source_pin')] in pins]
    return edges


def neighbour_destinations(model,node,direction):
    """Two destinations suffice to decide direct jump vs. port chooser."""
    if node.get('reference_of') and (direction!='upstream' or not node.get('reference_source')):
        return []
    other='source' if direction=='upstream' else 'target'
    if hasattr(model,'store'):
        where,params=_neighbour_filter(node,direction)
        sql='SELECT data FROM edges WHERE seq IN (SELECT MIN(seq) FROM edges WHERE '+where+' GROUP BY '+other+','+other+'_pin LIMIT 2) ORDER BY seq'
        return [model.store.decode(row[0]) for row in model.store.connection().execute(sql,params)]
    found={}
    for edge in neighbour_edges(model,node,direction):
        found.setdefault((edge[other],edge[other+'_pin']),edge)
        if len(found)==2:break
    return list(found.values())


def jump_neighbour(app,node,direction):
    if not node or node.get('aggregate'):return
    edges=neighbour_destinations(app.diagram.model,node,direction)
    if not edges:
        app.set_status('当前器件没有已解析的'+('上游' if direction=='upstream' else '下游')+'连接。')
    elif len(edges)==1:
        edge=edges[0]
        # Several inputs may share one upstream output. Returning along a
        # just-navigated connection must keep its exact local input as well.
        selected=getattr(app.diagram,'selection_target',None) or {}
        previous=app.diagram.model.original_edges.get(selected.get('edge'))
        endpoint,other=('target','source') if direction=='upstream' else ('source','target')
        if (previous and previous[endpoint]==node.get('reference_of',node['id'])
                and (previous[other],previous[other+'_pin'])==(edge[other],edge[other+'_pin'])
                and (not node.get('reference_of') or previous['target_pin'] in node['inputs'])):
            edge=previous
        app.diagram.locate_edge(edge,direction)
    else:
        NeighbourDialog(app,node,direction)


class NeighbourDialog(tk.Toplevel):
    def __init__(self,app,instance,direction):
        super().__init__(app)
        self.model=app.diagram.model
        self.node=dict(instance) if isinstance(instance,dict) else self.model.original[instance]
        self.app,self.instance,self.direction=app,self.node.get('reference_of',self.node['id']),direction
        self.offset=0;self.title('选择上游器件' if direction=='upstream' else '选择下游器件');self.geometry('1040x570')
        row=ttk.Frame(self,padding=12);row.pack(fill='x')
        self.search=tk.StringVar();entry=ttk.Entry(row,textvariable=self.search);entry.pack(side='left',fill='x',expand=True)
        entry.bind('<Return>',lambda e:self.show(0,True));ttk.Button(row,text='搜索实例 / 端口 / 信号',command=lambda:self.show(0,True)).pack(side='left',padx=8)
        self.table=ttk.Treeview(self,columns=('instance','port','signal','local'),show='headings')
        for key,label,width in [('instance','对端真实实例',400),('port','对端端口',130),('signal','实际信号',260),('local','当前端口',140)]:
            self.table.heading(key,text=label);self.table.column(key,width=width)
        self.table.pack(fill='both',expand=True,padx=12);self.table.bind('<Double-1>',self.locate)
        buttons=ttk.Frame(self,padding=12);buttons.pack(fill='x')
        ttk.Button(buttons,text='上一页',command=lambda:self.show(-1)).pack(side='left');ttk.Button(buttons,text='下一页',command=lambda:self.show(1)).pack(side='left',padx=6)
        ttk.Button(buttons,text='跳转到所选器件',command=self.locate).pack(side='right')
        self.status=tk.StringVar();ttk.Label(buttons,textvariable=self.status).pack(side='left',padx=12)
        self.show(0);entry.focus_set()
        self.bind('<Destroy>',self._destroyed,add='+')

    def _destroyed(self,event):
        if event.widget is self:
            self.search=None;self.status=None

    def show(self,delta,reset=False):
        self.offset=0 if reset else max(0,self.offset+delta*100)
        model=self.model;query=self.search.get().strip().lower()
        endpoint,other,port,local=('target','source','source_pin','target_pin') if self.direction=='upstream' else ('source','target','target_pin','source_pin')
        if hasattr(model,'store'):
            from .indexed_view import QuerySequence
            sql='SELECT e.data FROM edges e JOIN nodes n ON n.id=e.'+other+' WHERE e.'+endpoint+'=?'
            params=[self.instance]
            if self.node.get('reference_of'):
                sql+=' AND e.source=?';params.append(self.node['reference_source'])
            if query:sql+=' AND instr(lower(n.data||e.'+port+'),?)>0';params.append(query)
            edges=QuerySequence(model.store,sql+' ORDER BY e.seq',params,decode=True)
        else:
            edges=neighbour_edges(model,self.node,self.direction)
            if query:edges=[e for e in edges if query in (e[other]+e[port]+connection_label(model.original[e[other]],e[port])).lower()]
        total=len(edges);self.offset=min(self.offset,max(0,((total-1)//100)*100));self.rows={}
        self.table.delete(*self.table.get_children())
        for i,edge in enumerate(edges[self.offset:self.offset+100]):
            self.rows[str(i)]=edge;node=model.original[edge[other]]
            self.table.insert('','end',iid=str(i),values=(edge[other],edge[port],connection_label(node,edge[port]),edge[local]))
        self.status.set('{}–{} / {} 条端口连接'.format(self.offset+1 if total else 0,min(total,self.offset+100),total))

    def locate(self,event=None):
        keys=self.table.selection()
        if keys:
            if self.app.diagram.model is self.model:
                self.app.diagram.locate_edge(self.rows[keys[0]],self.direction)
            else:self.app.set_status('结果图已切换，请重新选择上下游连接。')
            self.destroy()
