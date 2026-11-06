"""Formal-port preview belongs to device editing, before the main run."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import uuid
import tkinter as tk
from tkinter import ttk
from .input_ports import filter_ports, confirmed_ports


class InputPortDialog(tk.Toplevel):
    def __init__(self,parent,config,module,hierarchy,expression,selected,callback):
        super().__init__(parent)
        self.parent,self.config,self.module,self.hierarchy,self.callback=parent,config,module,hierarchy,callback
        self.rows,self.shown,self.selected=[],[],set(selected)
        self.process=None;self.stream=None;self.closed=False
        self.title('子系统 · 从 elab 读取并确认输入端口')
        self.geometry('950x620');self.minsize(760,460);self.transient(parent)
        frame=ttk.Frame(self,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='{}  ·  直接父层 {}'.format(module,hierarchy or config['design'].get('top','')),font=(parent.app.family,12,'bold')).pack(anchor='w')
        ttk.Label(frame,text='只读取 input 端口，不追踪连线。整口确认后，主提取按每个实例的实际位宽展开。').pack(anchor='w',pady=8)
        actions=ttk.Frame(frame);actions.pack(fill='x',pady=6)
        self.read_button=ttk.Button(actions,text='① 读取输入端口',command=self.start);self.read_button.pack(side='left')
        ttk.Button(actions,text='取消读取',command=self.cancel).pack(side='left',padx=8)
        self.expression=tk.StringVar(value=expression or '.*')
        ttk.Entry(actions,textvariable=self.expression,width=28).pack(side='left',fill='x',expand=True)
        ttk.Button(actions,text='② 正则筛选',command=self.filter).pack(side='left',padx=8)
        self.table=ttk.Treeview(frame,columns=('name','direction','range','instance'),show='tree headings',selectmode='extended')
        self.table.heading('#0',text='勾选');self.table.column('#0',width=48,stretch=False)
        for key,label,width in [('name','端口名',190),('direction','方向',65),('range','实际位宽 / 范围',140),('instance','适用实例',360)]:
            self.table.heading(key,text=label);self.table.column(key,width=width,minwidth=50)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.table.pack(fill='both',expand=True)
        self.table.bind('<ButtonRelease-1>',self.click)
        row=ttk.Frame(frame);row.pack(fill='x',pady=10)
        ttk.Button(row,text='勾选全部匹配口',command=lambda:self.toggle_all(True)).pack(side='left')
        ttk.Button(row,text='清空勾选',command=lambda:self.toggle_all(False)).pack(side='left',padx=8)
        self.confirm_button=ttk.Button(row,text='③ 确认端口',command=self.confirm,state='disabled');self.confirm_button.pack(side='right')
        self.status=tk.StringVar(value='点击“读取输入端口”，等待读取完成后再筛选、勾选和确认。')
        ttk.Label(frame,textvariable=self.status,wraplength=870).pack(fill='x',pady=6)
        self.expression.trace('w',self.invalidate)
        self.protocol('WM_DELETE_WINDOW',self.close);self.grab_set()
        self.bind('<Destroy>',self.destroyed,add='+')

    def destroyed(self,event):
        if event.widget is self:
            self.stop_process()
            if self.stream:self.stream.close();self.stream=None

    def stop_process(self):
        process=self.process
        if not process or process.poll() is not None:return
        try:
            if os.name!='nt':os.killpg(process.pid,signal.SIGTERM)
            else:process.terminate()
        except OSError:pass
        # Reap outside Tk. This wait is only for explicit cancellation, never
        # an execution deadline on reading the elaborated design.
        def reap():
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
                    else:process.kill()
                except OSError:pass
                process.wait()
        threading.Thread(target=reap,daemon=True).start()

    def invalidate(self,*args):
        self.confirm_button.configure(state='disabled')
        self.status.set('正则已修改，请重新筛选并确认。')

    def start(self):
        if self.process and self.process.poll() is None:return
        self.rows=[];self.table.delete(*self.table.get_children());self.confirm_button.configure(state='disabled')
        directory=self.parent.app.runs/'.input-discovery'/uuid.uuid4().hex
        directory.mkdir(parents=True)
        self.directory=directory
        request={'config':self.config,'module':self.module,'hierarchy':self.hierarchy}
        (directory/'request.json').write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
        self.stream=(directory/'preprocess.log').open('w',encoding='utf-8')
        root=Path(__file__).resolve().parents[1]
        self.process=subprocess.Popen([sys.executable,'-m','clocktree','discover-inputs','--request',str(directory/'request.json'),
                                       '--out',str(directory)],cwd=str(root),stdout=self.stream,stderr=subprocess.STDOUT,
                                      start_new_session=os.name!='nt')
        self.read_button.configure(state='disabled');self.status.set('正在启动 Verdi 读取 elab；前处理日志：'+str(directory/'preprocess.log'))
        self.after(100,self.poll)

    def poll(self):
        if self.closed:return
        if self.process.poll() is None:
            from .runtime import tail_text
            text=tail_text(self.directory/'preprocess.log',2000)
            lines=[line for line in text.splitlines() if 'CT7_INPUT_' in line]
            if lines:self.status.set(lines[-1])
            self.after(150,self.poll);return
        self.stream.close();self.stream=None;self.read_button.configure(state='normal')
        report=self.directory/'input-ports.json'
        if self.process.returncode or not report.exists():
            from .runtime import tail_text
            self.status.set('读取失败或已取消：'+tail_text(self.directory/'preprocess.log',1200))
            return
        data=json.loads(report.read_text(encoding='utf-8'))
        self.rows=data['ports'];self.filter()

    def filter(self):
        try:self.shown=filter_ports(self.rows,self.expression.get())
        except Exception as error:
            self.confirm_button.configure(state='disabled');self.status.set('正则无效：'+str(error));return
        self.table.delete(*self.table.get_children())
        for i,row in enumerate(self.shown):
            extent='{} bit{}'.format(row['width'],' [{}:{}]'.format(row['left'],row['right']) if row.get('left')!='' else '')
            self.table.insert('','end',iid=str(i),text='☑' if row['name'] in self.selected else '☐',
                              values=(row['name'],'input',extent,row['instance']))
        self.filtered_expression=self.expression.get()
        self.confirm_button.configure(state='normal' if self.shown else 'disabled')
        self.status.set('匹配 {} 项实例端口 / {} 个端口名；请勾选后确认。'.format(len(self.shown),len(set(row['name'] for row in self.shown))))

    def click(self,event):
        key=self.table.identify_row(event.y)
        if key and self.table.identify_column(event.x)=='#0':
            name=self.shown[int(key)]['name']
            if name in self.selected:self.selected.remove(name)
            else:self.selected.add(name)
            self.refresh_checks()

    def refresh_checks(self):
        for key in self.table.get_children():self.table.item(key,text='☑' if self.shown[int(key)]['name'] in self.selected else '☐')

    def toggle_all(self,value):
        names={r['name'] for r in self.shown}
        if value:self.selected.update(names)
        else:self.selected.difference_update(names)
        self.refresh_checks()

    def confirm(self):
        try:
            if self.expression.get()!=getattr(self,'filtered_expression',None):raise ValueError('请重新筛选')
            names=confirmed_ports(self.rows,self.expression.get(),self.selected)
        except Exception as error:self.status.set(str(error));return
        self.callback(names,self.expression.get(),self.rows)
        self.close()

    def cancel(self):
        self.stop_process()
        self.status.set('已取消读取；没有改变已确认的规则。')

    def close(self):
        self.cancel();self.closed=True
        if self.stream:self.stream.close();self.stream=None
        self.destroy()
        if self.parent.winfo_exists():self.parent.grab_set()
