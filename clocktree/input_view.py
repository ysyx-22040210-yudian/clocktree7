"""Formal-port preview belongs to device editing, before the main run."""
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import uuid
import tkinter as tk
from tkinter import ttk
from .input_ports import DIRECTION_LABELS, filter_ports, confirmed_ports


class InputPortDialog(tk.Toplevel):
    def __init__(self,parent,config,module,hierarchy,expression,selected,callback,direction='input',
                 read_directions=None,cached_rows=None,cache_callback=None,single=False):
        super().__init__(parent)
        self.parent,self.config,self.module,self.hierarchy,self.callback=parent,config,module,hierarchy,callback
        self.rows,self.shown,self.selected=[],[],set(selected)
        self.direction = direction
        self.direction_label = DIRECTION_LABELS[direction]
        self.read_directions = list(read_directions or [direction])
        self.cache_callback = cache_callback
        self.single = single
        self.process=None;self.stream=None;self.closed=False;self.cancelled=False;self.poll_id=None
        self.title('从 elab 筛选{}端口'.format(self.direction_label))
        self.geometry('950x620');self.minsize(760,460);self.transient(parent)
        frame=ttk.Frame(self,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='{}  ·  直接父层 {}'.format(module,hierarchy or config['design'].get('top','')),font=(parent.app.family,12,'bold')).pack(anchor='w')
        instruction = '选一个{}端口写入文本框。'.format(self.direction_label) if single else '勾选{}端口，确认后加入文本框。'.format(self.direction_label)
        ttk.Label(frame,text=instruction).pack(anchor='w',pady=8)
        actions=ttk.Frame(frame);actions.pack(fill='x',pady=6)
        self.read_button=ttk.Button(actions,text='读取 elab 端口',command=self.start);self.read_button.pack(side='left')
        ttk.Button(actions,text='取消读取',command=self.cancel).pack(side='left',padx=8)
        self.expression=tk.StringVar(value=expression or '.*')
        ttk.Entry(actions,textvariable=self.expression,width=28).pack(side='left',fill='x',expand=True)
        ttk.Button(actions,text='正则筛选',command=self.filter).pack(side='left',padx=8)
        # Reserve the counter and confirmation controls before giving the
        # remaining height to the scrollable table, including at minimum size.
        footer=ttk.Frame(frame);footer.pack(side='bottom',fill='x')
        table_frame=ttk.Frame(frame);table_frame.pack(fill='both',expand=True)
        self.table=ttk.Treeview(table_frame,columns=('name','direction','range','instance'),show='tree headings',selectmode='extended')
        self.table.heading('#0',text='勾选');self.table.column('#0',width=48,stretch=False)
        for key,label,width in [('name','端口名',190),('direction','方向',65),('range','实际位宽 / 范围',140),('instance','适用实例',360)]:
            self.table.heading(key,text=label);self.table.column(key,width=width,minwidth=50)
        scroll=ttk.Scrollbar(table_frame,orient='vertical',command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.table.pack(fill='both',expand=True)
        self.table.bind('<ButtonRelease-1>',self.click)
        self.selection_summary=tk.StringVar(value='已选 0 个{}端口 / 当前匹配 0 个端口名'.format(self.direction_label))
        ttk.Label(footer,textvariable=self.selection_summary,font=(parent.app.family,10,'bold')).pack(anchor='w',pady=(8,0))
        row=ttk.Frame(footer);row.pack(fill='x',pady=10)
        if not single:
            ttk.Button(row,text='勾选全部匹配口',command=lambda:self.toggle_all(True)).pack(side='left')
        ttk.Button(row,text='清空勾选',command=lambda:self.toggle_all(False)).pack(side='left',padx=8)
        self.confirm_button=ttk.Button(row,text='确认勾选端口',command=self.confirm,state='disabled');self.confirm_button.pack(side='right')
        self.status=tk.StringVar(value='点击“读取 elab 端口”，然后输入正则并勾选端口。')
        ttk.Label(footer,textvariable=self.status,wraplength=700).pack(fill='x',pady=6)
        self.expression.trace('w',self.invalidate)
        self.protocol('WM_DELETE_WINDOW',self.close);self.grab_set()
        self.bind('<Destroy>',self.destroyed,add='+')
        if cached_rows is not None:
            self.set_rows(cached_rows)

    def destroyed(self,event):
        if event.widget is self:
            self.closed = True
            if self.poll_id is not None:
                self.after_cancel(self.poll_id)
                self.poll_id = None
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
        self.update_selection_summary()

    def start(self):
        if self.poll_id is not None or (self.process and self.process.poll() is None):return
        self.cancelled = False
        self.rows=[];self.shown=[];self.filtered_expression=None
        self.table.delete(*self.table.get_children());self.confirm_button.configure(state='disabled')
        self.update_selection_summary()
        try:
            directory=self.parent.app.runs/'.input-discovery'/uuid.uuid4().hex
            directory.mkdir(parents=True)
            self.directory=directory
            request={'config':self.config,'module':self.module,'hierarchy':self.hierarchy,
                     'directions':self.read_directions}
            (directory/'request.json').write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
            self.stream=(directory/'preprocess.log').open('w',encoding='utf-8')
            root=Path(__file__).resolve().parents[1]
            self.process=subprocess.Popen([sys.executable,'-m','clocktree','discover-inputs','--request',str(directory/'request.json'),
                                           '--out',str(directory)],cwd=str(root),stdout=self.stream,stderr=subprocess.STDOUT,
                                          start_new_session=os.name!='nt')
        except OSError as error:
            if self.stream:self.stream.close();self.stream=None
            self.status.set('无法启动端口读取：'+str(error))
            return
        self.read_button.configure(state='disabled');self.status.set('正在启动 Verdi 读取 elab；前处理日志：'+str(directory/'preprocess.log'))
        self.poll_id = self.after(100,self.poll)

    def poll(self):
        self.poll_id = None
        if self.closed:return
        if self.process.poll() is None:
            from .runtime import tail_text
            text=tail_text(self.directory/'preprocess.log',2000)
            lines=[line for line in text.splitlines() if 'CT7_INPUT_' in line]
            if lines and not self.cancelled:self.status.set(lines[-1])
            self.poll_id = self.after(150,self.poll);return
        self.stream.close();self.stream=None;self.read_button.configure(state='normal')
        report=self.directory/'input-ports.json'
        if self.cancelled:
            self.status.set('已取消读取。规则端口名单保留。')
            return
        if self.process.returncode or not report.exists():
            from .runtime import tail_text
            self.status.set('读取失败或已取消：'+tail_text(self.directory/'preprocess.log',1200))
            return
        try:
            rows = json.loads(report.read_text(encoding='utf-8'))['ports']
        except (OSError, ValueError, KeyError) as error:
            self.status.set('无法读取端口结果：'+str(error))
            return
        if self.cache_callback:self.cache_callback(rows)
        self.set_rows(rows)

    def set_rows(self, rows):
        self.rows = [row for row in rows if row['direction'] == self.direction]
        self.filter()

    def filter(self):
        try:self.shown=filter_ports(self.rows,self.expression.get())
        except re.error as error:
            self.shown = []
            self.table.delete(*self.table.get_children())
            self.confirm_button.configure(state='disabled');self.status.set('正则无效：'+str(error))
            self.update_selection_summary();return
        self.table.delete(*self.table.get_children())
        for i,row in enumerate(self.shown):
            extent='{} bit{}'.format(row['width'],' [{}:{}]'.format(row['left'],row['right']) if row.get('left')!='' else '')
            self.table.insert('','end',iid=str(i),text='☑' if row['name'] in self.selected else '☐',
                              values=(row['name'],row['direction'],extent,row['instance']))
        self.filtered_expression=self.expression.get()
        self.confirm_button.configure(state='normal' if self.shown else 'disabled')
        self.update_selection_summary()
        self.status.set('匹配 {} 项实例端口 / {} 个端口名；请勾选后确认。'.format(len(self.shown),len(set(row['name'] for row in self.shown))))

    def click(self,event):
        key=self.table.identify_row(event.y)
        if key and self.table.identify_column(event.x)=='#0':
            name=self.shown[int(key)]['name']
            if name in self.selected:self.selected.remove(name)
            elif self.single:self.selected = {name}
            else:self.selected.add(name)
            self.refresh_checks()

    def refresh_checks(self):
        for key in self.table.get_children():self.table.item(key,text='☑' if self.shown[int(key)]['name'] in self.selected else '☐')
        self.update_selection_summary()

    def update_selection_summary(self):
        # Confirmation uses distinct matching formal names, not instance rows
        # or individual bus bits. Hidden selections are not submitted.
        names={row['name'] for row in self.shown}
        pending=bool(self.shown) and self.expression.get()!=getattr(self,'filtered_expression',None)
        self.selection_summary.set('已选 {} 个{}端口 / {}匹配 {} 个端口名{}'.format(
            len(names & self.selected),self.direction_label,'上次' if pending else '当前',len(names),
            '（待重新筛选）' if pending else ''))

    def toggle_all(self,value):
        names={r['name'] for r in self.shown}
        if value:self.selected.update(names)
        else:self.selected.difference_update(names)
        self.refresh_checks()

    def confirm(self):
        try:
            if self.expression.get()!=getattr(self,'filtered_expression',None):raise ValueError('请重新筛选')
            names=confirmed_ports(self.rows,self.expression.get(),self.selected,self.direction)
            if self.single and len(names) != 1:
                raise ValueError('请只勾选一个{}端口'.format(self.direction_label))
        except (ValueError, re.error) as error:self.status.set(str(error));return
        self.callback(names,self.expression.get(),self.rows)
        self.close()

    def cancel(self):
        if self.process and self.process.poll() is None:
            self.cancelled = True
            self.stop_process()
            self.status.set('正在取消读取…')

    def close(self):
        self.destroy()
        if self.parent.winfo_exists():self.parent.grab_set()
