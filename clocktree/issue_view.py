import json
import queue
import threading
import tkinter as tk
from tkinter import ttk,filedialog
from .issues import IssueIndex,COLUMNS,HEADINGS


class IssueDialog(tk.Toplevel):
    def __init__(self,app,extra=()):
        super().__init__(app)
        self.app=app;self.title('完整问题表 · 未解析与诊断');self.geometry('1380x680')
        self.index=IssueIndex(app.graph or {'nodes':[],'edges':[]},extra);self.offset=0;self.ready=False;self.cancelled=threading.Event();self.queue=queue.Queue()
        controls=ttk.Frame(self,padding=12);controls.pack(fill='x')
        self.category=tk.StringVar(value='全部类别');self.search=tk.StringVar()
        self.combo=ttk.Combobox(controls,textvariable=self.category,values=['全部类别'],state='readonly',width=23);self.combo.pack(side='left')
        entry=ttk.Entry(controls,textvariable=self.search,width=40);entry.pack(side='left',padx=10)
        entry.bind('<Return>',lambda e:self.page(0,reset=True));self.combo.bind('<<ComboboxSelected>>',lambda e:self.page(0,reset=True))
        ttk.Button(controls,text='筛选',command=lambda:self.page(0,reset=True)).pack(side='left')
        ttk.Button(controls,text='导出筛选结果 CSV',command=self.export).pack(side='right')
        body=ttk.Frame(self);body.pack(fill='both',expand=True)
        self.table=ttk.Treeview(body,columns=COLUMNS,show='headings',selectmode='browse')
        widths=dict(zip(COLUMNS,(110,65,45,120,220,90,150,220,240)))
        for key,label in zip(COLUMNS,HEADINGS):self.table.heading(key,text=label);self.table.column(key,width=widths[key],minwidth=40)
        self.table.tag_configure('错误',foreground='#a13f40')
        self.table.tag_configure('警告',foreground='#865f20')
        vertical=ttk.Scrollbar(body,command=self.table.yview);vertical.pack(side='right',fill='y')
        horizontal=ttk.Scrollbar(self,orient='horizontal',command=self.table.xview);horizontal.pack(fill='x')
        self.table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set);self.table.pack(fill='both',expand=True)
        self.table.bind('<Double-1>',lambda e:self.locate())
        self.table.bind('<<TreeviewSelect>>',self.show_reason)
        self.reason=tk.StringVar(value='选择问题可阅读完整原因和处理提示；双击定位。')
        ttk.Label(self,textvariable=self.reason,wraplength=1320,padding=10).pack(fill='x')
        actions=ttk.Frame(self,padding=10);actions.pack(fill='x')
        for label,command in [('上一页',lambda:self.page(-1)),('下一页',lambda:self.page(1)),('定位问题',self.locate),('关联对象 / 原因',self.details)]:ttk.Button(actions,text=label,command=command).pack(side='left',padx=4)
        self.status=tk.StringVar(value='正在建立完整问题索引…');ttk.Label(actions,textvariable=self.status).pack(side='right')
        self.protocol('WM_DELETE_WINDOW',self.close)
        def build():
            try:self.index.build(self.cancelled);self.queue.put(None)
            except Exception as error:self.queue.put(str(error))
        self.worker=threading.Thread(target=build,daemon=True);self.worker.start();self.poll_timer=self.after(50,self.poll)

    def poll(self):
        self.poll_timer=None
        try:error=self.queue.get_nowait()
        except queue.Empty:self.poll_timer=self.after(50,self.poll);return
        if error:self.status.set('问题索引失败：'+error);return
        self.ready=True;self.combo.configure(values=['全部类别']+self.index.categories());self.page(0)

    def filters(self):return '' if self.category.get()=='全部类别' else self.category.get(),self.search.get().strip()

    def page(self,delta,reset=False):
        if not self.ready:return
        self.offset=0 if reset else max(0,self.offset+delta*100)
        total,instances,rows=self.index.query(*self.filters(),offset=self.offset)
        if self.offset>=total and self.offset:
            self.offset=max(0,((total-1)//100)*100);total,instances,rows=self.index.query(*self.filters(),offset=self.offset)
        self.table.delete(*self.table.get_children());self.rows={str(row[0]):row for row in rows}
        for row in rows:self.table.insert('','end',iid=str(row[0]),values=row[1:-1],tags=(row[2],))
        self.status.set('{} 个问题 · 涉及 {} 个实例 · {}–{}'.format(total,instances,self.offset+1 if total else 0,self.offset+len(rows)))

    def selected(self):
        selection=self.table.selection()
        return self.rows.get(selection[0]) if selection else None

    def show_reason(self,event=None):
        row=self.selected()
        if row:self.reason.set('原因：{}\n处理提示：{}'.format(row[8],row[9]))

    def locate(self):
        row=self.selected()
        if row:self.app.locate_issue(row)

    def details(self):
        row=self.selected()
        if not row:return
        dialog=tk.Toplevel(self);dialog.title('问题 {} · 关联对象'.format(row[0]));dialog.geometry('920x560')
        info=json.loads(row[-1]);text=tk.Text(dialog,height=8,wrap='word');text.pack(fill='x')
        text.insert('end',json.dumps(info,ensure_ascii=False,indent=2));text.configure(state='disabled')
        table=ttk.Treeview(dialog,columns=('pin',),show='tree headings');table.heading('#0',text='真实实例路径');table.heading('pin',text='端口');table.pack(fill='both',expand=True)
        page=[0];objects={}
        def show(delta):
            page[0]=max(0,min(max(0,(info['associated_objects']-1)//100),page[0]+delta));table.delete(*table.get_children());objects.clear()
            for i,(key,pin) in enumerate(self.index.objects(row[0],page[0]*100)):
                objects[str(i)]=(key,pin);table.insert('','end',iid=str(i),text=key,values=(pin,))
        def locate(event=None):
            keys=table.selection()
            if keys:
                key,pin=objects[keys[0]];self.app.locate_issue_object(key,pin)
        table.bind('<Double-1>',locate)
        buttons=ttk.Frame(dialog);buttons.pack(fill='x')
        ttk.Button(buttons,text='上一页',command=lambda:show(-1)).pack(side='left');ttk.Button(buttons,text='下一页',command=lambda:show(1)).pack(side='left')
        ttk.Button(buttons,text='定位所选对象',command=locate).pack(side='right');show(0)

    def export(self):
        if not self.ready:return
        path=filedialog.asksaveasfilename(parent=self,title='导出全部筛选问题',defaultextension='.csv',filetypes=[('CSV','*.csv')])
        if path:self.status.set('已导出 {} 个问题：{}'.format(self.index.export(path,*self.filters()),path))

    def close(self):
        if self.poll_timer is not None:self.after_cancel(self.poll_timer)
        self.cancelled.set()
        # The worker owns its connection; clean only after it has finished.
        index,worker=self.index,self.worker
        def cleanup():worker.join();index.close()
        threading.Thread(target=cleanup,daemon=True).start();self.destroy()
