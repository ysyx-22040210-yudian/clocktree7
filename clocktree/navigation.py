"""Searchable neighbour chooser with bounded pages and exact port connections."""
import tkinter as tk
from tkinter import ttk
from .devices import connection_label


class NeighbourDialog(tk.Toplevel):
    def __init__(self,app,instance,direction):
        super().__init__(app)
        self.app,self.instance,self.direction=app,instance,direction
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

    def show(self,delta,reset=False):
        self.offset=0 if reset else max(0,self.offset+delta*100)
        model=self.app.diagram.model;query=self.search.get().strip().lower()
        endpoint,other,port,local=('target','source','source_pin','target_pin') if self.direction=='upstream' else ('source','target','target_pin','source_pin')
        if hasattr(model,'store'):
            from .indexed_view import QuerySequence
            sql='SELECT e.data FROM edges e JOIN nodes n ON n.id=e.'+other+' WHERE e.'+endpoint+'=?'
            params=[self.instance]
            if query:sql+=' AND instr(lower(n.data||e.'+port+'),?)>0';params.append(query)
            edges=QuerySequence(model.store,sql,params,decode=True)
        else:
            edges=model.incoming[self.instance] if self.direction=='upstream' else model.outgoing[self.instance]
            if query:edges=[e for e in edges if query in (e[other]+e[port]+connection_label(model.original[e[other]],e[port])).lower()]
        total=len(edges);self.offset=min(self.offset,max(0,((total-1)//100)*100));self.rows={}
        self.table.delete(*self.table.get_children())
        for i,edge in enumerate(edges[self.offset:self.offset+100]):
            self.rows[str(i)]=edge[other];node=model.original[edge[other]]
            self.table.insert('','end',iid=str(i),values=(edge[other],edge[port],connection_label(node,edge[port]),edge[local]))
        self.status.set('{}–{} / {} 条端口连接'.format(self.offset+1 if total else 0,min(total,self.offset+100),total))

    def locate(self,event=None):
        keys=self.table.selection()
        if keys:
            self.app.diagram.locate(self.app.diagram.model.original[self.rows[keys[0]]]);self.destroy()
