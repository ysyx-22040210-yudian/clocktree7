"""Native Tk auxiliary-register trace workbench; original tree is immutable."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import uuid
import tkinter as tk
from tkinter import ttk,filedialog

from .register_trace import RegisterIndex,LABELS,seeds_from_graph
from .runtime import tail_text
from .storage import write_graph


RELATIONS={'direct':'精确位映射','inverted':'反相位映射','logic':'组合逻辑依赖','conditional':'条件未固定'}
SIDES={'Q':'Q · 来源时序输出','D':'D · 目的写入位'}


class RegisterDialog(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app)
        self.app=app;self.graph=app.graph;self.process=None;self.stream=None;self.closed=False
        self.directory=None;self.index=None;self.offset=0;self.total=0
        self.title('辅助端口 → 寄存器信号路径');self.geometry('1360x860');self.minsize(980,650);self.transient(app)
        frame=ttk.Frame(self,padding=16);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='辅助端口寄存器追踪',font=(app.family,15,'bold')).pack(anchor='w')
        ttk.Label(frame,text='pll_sel / div / gt 反查至时序输出；sw_done 沿负载到目的寄存器写入位。逐 bit 保存；不修改树连线。').pack(anchor='w',pady=7)
        settings=ttk.Frame(frame);settings.pack(fill='x',pady=4)
        self.root_var=tk.StringVar(value=self.graph['config'].get('trace_scope') or self.graph['config']['design']['top'])
        self.dft_var=tk.StringVar(value='dft_mode')
        self.memory_var=tk.StringVar(value=str(self.graph['config'].get('memory_limit_gib',60)))
        self.timeout_var=tk.StringVar(value=str(self.graph['config'].get('port_trace_timeout_s',0)))
        for label,var,width in [('追踪根层次',self.root_var,28),('置 0 的 DFT 信号（逗号分隔）',self.dft_var,23),
                                 ('内存 GiB',self.memory_var,7),('每端口超时秒',self.timeout_var,7)]:
            ttk.Label(settings,text=label).pack(side='left',padx=(0,5));ttk.Entry(settings,textvariable=var,width=width).pack(side='left',padx=(0,12))
        ttk.Label(frame,text='DFT 名称精确匹配：短名称匹配同名信号，完整层次路径限定一个信号；0 超时表示不限时。Q/D 由时序语句证明，reg 声明不足以作为依据。').pack(anchor='w',pady=5)
        actions=ttk.Frame(frame);actions.pack(fill='x',pady=6)
        self.start_button=ttk.Button(actions,text='开始新的寄存器追踪',command=self.start);self.start_button.pack(side='left')
        self.resume_button=ttk.Button(actions,text='从已提交端口继续',command=lambda:self.start(True),state='disabled');self.resume_button.pack(side='left',padx=8)
        ttk.Button(actions,text='取消当前追踪',command=self.cancel).pack(side='left')
        ttk.Button(actions,text='打开历史结果',command=self.open_result).pack(side='left',padx=8)
        ttk.Button(actions,text='导出全部 CSV',command=self.export_csv).pack(side='right')
        ttk.Button(actions,text='导出映射与证据 JSON',command=self.export_json).pack(side='right',padx=8)
        self.status=tk.StringVar(value='使用当前架构结果中保存的辅助端口配置；修改规则后请先重新提取架构。')
        ttk.Label(frame,textvariable=self.status,wraplength=1280).pack(fill='x',pady=5)
        searchbar=ttk.Frame(frame);searchbar.pack(fill='x',pady=4)
        self.search=tk.StringVar()
        ttk.Label(searchbar,text='搜索实例 / 端口 / 寄存器 / 状态').pack(side='left')
        entry=ttk.Entry(searchbar,textvariable=self.search);entry.pack(side='left',fill='x',expand=True,padx=10)
        entry.bind('<Return>',lambda e:self.refresh(True))
        ttk.Button(searchbar,text='查找',command=lambda:self.refresh(True)).pack(side='left')
        ttk.Button(searchbar,text='定位所选实例',command=self.locate).pack(side='left',padx=8)
        panes=ttk.Panedwindow(frame,orient='vertical');panes.pack(fill='both',expand=True)
        table_pane=ttk.Frame(panes);panes.add(table_pane,weight=3)
        container=ttk.Frame(table_pane);container.pack(fill='both',expand=True)
        columns=('instance','role','pin','bit','endpoint','side','relation','status','reason')
        self.table=ttk.Treeview(container,columns=columns,show='headings',selectmode='browse',height=9)
        widths=(245,80,130,130,270,125,110,110,220)
        for col,label,width in zip(columns,RegisterIndex.columns,widths):
            self.table.heading(col,text=label);self.table.column(col,width=width,minwidth=60,stretch=False)
        vertical=ttk.Scrollbar(container,orient='vertical',command=self.table.yview);vertical.pack(side='right',fill='y')
        horizontal=ttk.Scrollbar(container,orient='horizontal',command=self.table.xview);horizontal.pack(side='bottom',fill='x')
        self.table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set);self.table.pack(fill='both',expand=True)
        self.table.bind('<<TreeviewSelect>>',self.show_detail)
        self.table.bind('<Double-Button-1>',lambda e:self.locate())
        page=ttk.Frame(table_pane);page.pack(fill='x',pady=6)
        ttk.Button(page,text='上一页',command=lambda:self.page(-1)).pack(side='left')
        ttk.Button(page,text='下一页',command=lambda:self.page(1)).pack(side='left',padx=8)
        self.page_status=tk.StringVar();ttk.Label(page,textvariable=self.page_status).pack(side='right')
        detail_pane=ttk.Frame(panes);panes.add(detail_pane,weight=2)
        ttk.Label(detail_pane,text='选中行查看时序依据、DFT 假设及完整路径。拖动此处的分隔条调整表格 / 详情高度。').pack(anchor='w',pady=5)
        detail_frame=ttk.Frame(detail_pane);detail_frame.pack(fill='both',expand=True)
        self.detail=tk.Text(detail_frame,height=8,wrap='word',font=(app.family,10),background='#f4f7fb',relief='flat')
        scroll=ttk.Scrollbar(detail_frame,orient='vertical',command=self.detail.yview);scroll.pack(side='right',fill='y')
        self.detail.configure(yscrollcommand=scroll.set);self.detail.pack(fill='both',expand=True)
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.grab_set()
        self.bind('<Destroy>',self.destroyed,add='+')
        previous=app.ui_state.get(self.graph['config']).get('register_trace_directory')
        if previous and (Path(previous)/'manifest.json').exists():
            try:self.load_directory(previous)
            except (OSError,ValueError,KeyError) as error:self.status.set('历史任务读取失败：'+str(error))

    def load_directory(self,path):
        self.directory=Path(path);self.index=RegisterIndex(path) if (self.directory/'registers.sqlite').exists() else None
        self.offset=0
        manifest=json.loads((self.directory/'manifest.json').read_text(encoding='utf-8'));request=manifest['request']
        self.root_var.set(request.get('trace_root',''));self.dft_var.set(', '.join(request.get('dft_zero',[])))
        self.memory_var.set(str(request.get('memory_limit_gib',60)));self.timeout_var.set(str(request.get('port_trace_timeout_s',0)))
        status=self.directory/'status.json'
        state=json.loads(status.read_text(encoding='utf-8')) if status.exists() else {}
        from .runtime import alive
        if state and state.get('stage') not in ('finished','interrupted') and alive(state.get('controller',{})) is False:
            context=state.get('context',{})
            state['message']='上次进程已退出；最后阶段 {}，信号 {}。具体原因查看 controller.log / verdi.log，可从已提交端口继续。'.format(state.get('stage'),context.get('signal',context.get('arguments','')))
        self.resume_button.configure(state='disabled' if state.get('stage')=='finished' else 'normal')
        attention='（含未解析 / 边界 / 未固定条件，请检查状态列）' if state.get('stage')=='finished' and not state.get('context',{}).get('complete') else ''
        self.status.set(('历史任务 · '+state.get('message','断点已保存'))+attention+' · '+str(path))
        self.refresh()

    def start(self,resume=False):
        if self.process and self.process.poll() is None:return
        if self.app.busy:
            self.status.set('架构提取仍在运行，请等待完成后启动寄存器追踪。');return
        try:
            if resume:
                if self.directory is None:raise ValueError('尚未选择历史任务')
                request_path=self.directory/'request.json'
                manifest=json.loads((self.directory/'manifest.json').read_text(encoding='utf-8'))
                if not request_path.exists():write_graph(request_path,manifest['request'])
            else:
                seeds=list(seeds_from_graph(self.graph))
                if not seeds:raise ValueError('当前结果没有辅助端口，请先编辑器件并重新提取架构')
                request={'config':self.graph['config'],'seeds':seeds,'trace_root':self.root_var.get().strip(),
                         'dft_zero':[v.strip() for v in self.dft_var.get().split(',') if v.strip()],
                         'memory_limit_gib':float(self.memory_var.get()),'port_trace_timeout_s':float(self.timeout_var.get())}
                self.directory=self.app.runs/'.register-trace'/uuid.uuid4().hex;self.directory.mkdir(parents=True)
                request_path=self.directory/'request.json';write_graph(request_path,request)
                self.index=None;self.table.delete(*self.table.get_children());self.offset=0
            self.app.ui_state.update(self.graph['config'],register_trace_directory=str(self.directory))
            command=[sys.executable,'-m','clocktree','trace-registers','--request',str(request_path),'--out',str(self.directory)]
            if resume:command.append('--resume')
            self.stream=(self.directory/'controller.log').open('a',encoding='utf-8')
            self.process=subprocess.Popen(command,cwd=str(Path(__file__).resolve().parents[1]),stdout=self.stream,stderr=subprocess.STDOUT,start_new_session=os.name!='nt')
            self.start_button.configure(state='disabled');self.resume_button.configure(state='disabled')
            self.status.set('正在启动寄存器追踪；日志：'+str(self.directory/'controller.log'));self.after(150,self.poll)
        except (ValueError,OSError) as error:self.status.set(str(error))

    def poll(self):
        if self.closed:return
        status=self.directory/'status.json'
        if status.exists():
            try:
                data=json.loads(status.read_text(encoding='utf-8'));context=data.get('context',{})
                self.status.set('{} · {} · {} {} {}'.format(data['stage'],data['message'],context.get('instance',''),context.get('bit',context.get('pin','')),context.get('signal','')))
            except (ValueError,OSError):pass
        if self.process.poll() is None:self.after(200,self.poll);return
        self.stream.close();self.stream=None;self.start_button.configure(state='normal')
        self.index=RegisterIndex(self.directory) if (self.directory/'registers.sqlite').exists() else None
        result=self.directory/'register-trace.json'
        if self.process.returncode in (0,2) and result.exists():
            summary=json.loads(result.read_text(encoding='utf-8'))['summary']
            self.status.set('已完成 {} 个辅助端口 · {} 个 bit 找到寄存器 · {} 条寄存器映射 · {} · {}'.format(
                summary['ports'],summary['register_bits'],summary['counts'].get('register',0),
                '存在未解析/边界/条件，请查看状态列' if not summary['complete'] else '追踪完成',str(self.directory)))
        else:self.status.set('任务中断，可从已提交端口继续：'+tail_text(self.directory/'controller.log',900));self.resume_button.configure(state='normal')
        self.refresh()

    def refresh(self,reset=False):
        if reset:self.offset=0
        self.table.delete(*self.table.get_children())
        if not self.index:return
        try:self.total,rows=self.index.query(self.search.get().strip(),self.offset)
        except Exception as error:self.status.set(str(error));return
        for row in rows:
            identifier,*values=row;values[5]=SIDES.get(values[5],values[5]);values[6]=RELATIONS.get(values[6],values[6]);values[7]=LABELS.get(values[7],values[7])
            self.table.insert('','end',iid=str(identifier),values=values)
        self.page_status.set('{}–{} / {} 条位映射与问题'.format(self.offset+1 if rows else 0,self.offset+len(rows),self.total))

    def page(self,step):
        offset=self.offset+step*100
        if 0<=offset<self.total:self.offset=offset;self.refresh()

    def show_detail(self,event=None):
        selected=self.table.selection()
        if not selected or not self.index:return
        data=self.index.detail(int(selected[0]));proof=data.get('proof',{})
        text='状态：{}\n端点：{} {}\n'.format(LABELS.get(data.get('status'),data.get('status','')),data.get('endpoint',''),SIDES.get(data.get('side'),''))
        if proof:
            basis={'edge_triggered_always':'边沿触发的 always 赋值（触发器）','all_writers_share_conditional_hold':'全部写入语句具有共同保持条件（锁存器）','npiSeqPrim':'NPI 确认的时序 UDP'}.get(proof.get('basis'),proof.get('basis',''))
            text+='时序依据：{}\n'.format(basis)+'触发条件：'+', '.join(proof.get('events',[]))+'\n'
        if data.get('data_expression'):text+='目的写入表达式：'+data['data_expression']+'\n'
        if data.get('reason'):text+='说明：'+data['reason']+'\n'
        applied=data.get('port_summary',{}).get('assumptions_applied',[])
        text+='本端口采用的置零假设：'+(', '.join(applied)+' = 0' if applied else '未采用')+'\n'
        text+='路径：\n'+'\n'.join('  '+s['signal'] for s in data.get('steps',[]))
        text+='\n\n原始时序依据：\n'+json.dumps(proof,ensure_ascii=False,indent=2)
        self.detail.delete('1.0','end');self.detail.insert('end',text)

    def locate(self):
        selected=self.table.selection()
        if not selected:return
        name=self.table.item(selected[0],'values')[0]
        node=self.app.diagram.model.original.get(name)
        if node:self.app.diagram.locate(node)

    def open_result(self):
        if self.process and self.process.poll() is None:return
        path=filedialog.askopenfilename(parent=self,title='选择寄存器任务 manifest.json 或 register-trace.json',filetypes=[('JSON','*.json')])
        if path:
            try:self.load_directory(Path(path).parent)
            except (OSError,ValueError,KeyError) as error:self.status.set(str(error))

    def export_csv(self):
        if not self.index:return
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.csv',initialfile='register-bit-mapping.csv')
        if path:
            try:self.status.set('已导出 {} 条记录：{}'.format(self.index.export(path),path))
            except OSError as error:self.status.set(str(error))

    def export_json(self):
        if not self.index:return
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.json',initialfile='register-bit-mapping.json')
        if path:
            try:self.index.export_json(path);self.status.set('已导出完整位映射与时序依据：'+path)
            except OSError as error:self.status.set(str(error))

    def cancel(self):
        process=self.process
        if not process or process.poll() is not None:return
        try:
            if os.name!='nt':os.killpg(process.pid,signal.SIGTERM)
            else:process.terminate()
        except OSError:pass
        def reap():
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:process.kill()
                except OSError:pass
                # Verdi owns a separate process group. Kill only the worker
                # whose saved controller identity belongs to this task.
                from .runtime import alive
                try:
                    saved=json.loads((self.directory/'worker.json').read_text(encoding='utf-8'))
                    worker=saved.get('worker',{})
                    if os.name!='nt' and saved.get('controller',{}).get('pid')==process.pid and alive(worker) is True:
                        os.killpg(worker['pid'],signal.SIGKILL)
                except (OSError,ValueError):pass
        threading.Thread(target=reap,daemon=True).start()

    def destroyed(self,event):
        if event.widget is self:self.cancel()

    def close(self):
        self.cancel();self.closed=True
        if self.stream:self.stream.close();self.stream=None
        self.destroy()
