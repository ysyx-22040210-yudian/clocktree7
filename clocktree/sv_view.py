"""Native Tk template editors, branch review and strict single-file SV export."""
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk,filedialog
import uuid

from .storage import write_graph
from .register_view import RegisterDialog,RELATIONS
from .sv_templates import SVPlan,ROLES,architecture_signature,write_source,graph_for_templates
from .runtime import tail_text
from .sv_editor import SVCodeEditor
from .register_results import latest_register_result,register_result_stamp,result_directory


class CandidateDialog(tk.Toplevel):
    def __init__(self,parent,branch,var,callback):
        super().__init__(parent);self.title('$'+var+' · 选择已追到的寄存器映射');self.geometry('1160x520');self.transient(parent)
        self.parent=parent;self.branch=branch;self.var=var;self.callback=callback;self.offset=0;self.items={}
        self.search=tk.StringVar();bar=ttk.Frame(self,padding=10);bar.pack(fill='x')
        ttk.Entry(bar,textvariable=self.search).pack(side='left',fill='x',expand=True)
        ttk.Button(bar,text='搜索',command=lambda:self.refresh(True)).pack(side='left')
        table_frame=ttk.Frame(self);table_frame.pack(fill='both',expand=True,padx=10)
        table_frame.rowconfigure(0,weight=1);table_frame.columnconfigure(0,weight=1)
        self.table=ttk.Treeview(table_frame,columns=('instance','pin','expression','capture','certainty'),show='headings')
        for col,label,width in [('instance','器件实例',190),('pin','实际辅助端口',100),('expression','模板信号 / 精确向量',300),('capture','接收寄存器位（sw_done）',250),('certainty','关系（请核对）',150)]:
            self.table.heading(col,text=label);self.table.column(col,width=width)
        scroll=ttk.Scrollbar(table_frame,command=self.table.yview);scroll.grid(row=0,column=1,sticky='ns')
        horizontal=ttk.Scrollbar(table_frame,orient='horizontal',command=self.table.xview);horizontal.grid(row=1,column=0,sticky='ew')
        self.table.configure(yscrollcommand=scroll.set,xscrollcommand=horizontal.set);self.table.grid(row=0,column=0,sticky='nsew')
        bar=ttk.Frame(self,padding=10);bar.pack(fill='x')
        ttk.Button(bar,text='上一页',command=lambda:self.page(-1)).pack(side='left')
        ttk.Button(bar,text='下一页',command=lambda:self.page(1)).pack(side='left',padx=8)
        ttk.Button(bar,text='确认所选映射',command=self.choose).pack(side='right')
        self.table.bind('<Double-Button-1>',lambda e:self.choose());self.protocol('WM_DELETE_WINDOW',self.close);self.grab_set();self.refresh()
    def refresh(self,reset=False):
        if reset:self.offset=0
        self.table.delete(*self.table.get_children())
        rows=self.parent.plan.candidates(self.branch,self.var,self.search.get(),self.offset,100);self.items={r['id']:r for r in rows}
        for row in rows:
            label='、'.join(RELATIONS.get(v,'跨多个寄存器' if v=='multiple_registers' else v) for v in row['certainty'].replace(';',',').split(','))
            self.table.insert('','end',iid=row['id'],values=(row['instance'],row['pin'],row['expression'],row.get('capture_expression',''),label))
    def page(self,delta):self.offset=max(0,self.offset+delta*100);self.refresh()
    def choose(self):
        selection=self.table.selection()
        if selection:self.callback(selection[0]);self.close()
    def close(self):self.destroy();self.parent.grab_set()


class SVDialog(tk.Toplevel):
    def __init__(self,app):
        graph=graph_for_templates(app.graph,app.get_config(validate=False))
        super().__init__(app);self.app=app;self.graph=graph;self.plan=None;self.directory=None;self.process=None;self.stream=None;self.offset=0;self.closed=False;self.rows={}
        saved=app.ui_state.get(self.graph['config']);self.selections=dict(saved.get('sv_branch_selections',{}))
        self.title('时钟树分支 · 用户模板生成 SV');self.geometry('1400x900');self.minsize(1050,700);self.transient(app)
        frame=ttk.Frame(self,padding=14);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='每个分支末端生成一段模板代码，全部写入一个 SV 文件',font=(app.family,14,'bold')).pack(anchor='w')
        ttk.Label(frame,text='$var_pllsel · $var_div · $var_swdone · $var_gt0～2 · $var_pll0～3 · $var_clkip · $var_rst  |  $var_rst 取最初器件所属层复位；门控/PLL 缺位补 1\'b1',wraplength=1330).pack(anchor='w',pady=6)
        bar=ttk.Frame(frame);bar.pack(fill='x',pady=5)
        self.registers=tk.StringVar();self.auto_registers=tk.BooleanVar(value=True);self._setting_registers=False
        ttk.Label(bar,text='寄存器结果目录（L1 直达可留空）').pack(side='left');ttk.Entry(bar,textvariable=self.registers).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(bar,text='选择',command=self.choose_registers).pack(side='left')
        ttk.Checkbutton(bar,text='自动选择最新',variable=self.auto_registers,command=self.refresh_registers).pack(side='left',padx=8)
        ttk.Button(bar,text='打开寄存器追踪',command=self.open_register_trace).pack(side='left',padx=8)
        self.analyze_button=ttk.Button(bar,text='分析全部分支',command=self.analyze);self.analyze_button.pack(side='right')
        self.register_notice=tk.StringVar()
        ttk.Label(frame,textvariable=self.register_notice,wraplength=1330).pack(fill='x')
        self.status=tk.StringVar(value='填写两套模板，然后分析分支。多寄存器或未解析分支跳过，其余先生成；选定映射后重新生成同一个文件，不重复追加。')
        ttk.Label(frame,textvariable=self.status,wraplength=1330).pack(fill='x',pady=5)
        # Reserve actions before allocating the expanding notebook on Tk 8.5.
        footer=ttk.Frame(frame);footer.pack(side='bottom',fill='x',pady=(9,0))
        self.tabs=ttk.Notebook(frame);self.tabs.pack(fill='both',expand=True);self.editors={};self.code_editors={}
        templates=saved.get('sv_templates',{})
        for key,label in [('with_mux','起始有选择器 · 模板'),('without_mux','起始无选择器 · 模板')]:
            pane=ttk.Frame(self.tabs);self.tabs.add(pane,text=label)
            editor=SVCodeEditor(pane,filename=key+'.sv',initial=templates.get(key,''));editor.pack(fill='both',expand=True)
            self.code_editors[key]=editor;self.editors[key]=editor.text
        review=ttk.Frame(self.tabs,padding=5);self.tabs.add(review,text='分支映射与代码预览');self.review=review
        toolbar=ttk.Frame(review);toolbar.pack(fill='x',pady=4);self.search=tk.StringVar()
        ttk.Entry(toolbar,textvariable=self.search).pack(side='left',fill='x',expand=True)
        ttk.Button(toolbar,text='查找 / 重新核对模板',command=lambda:self.refresh(True)).pack(side='left',padx=8)
        ttk.Button(toolbar,text='上一页',command=lambda:self.page(-1)).pack(side='left')
        ttk.Button(toolbar,text='下一页',command=lambda:self.page(1)).pack(side='left',padx=8)
        panes=ttk.Panedwindow(review,orient='vertical');panes.pack(fill='both',expand=True)
        top=ttk.Frame(panes);panes.add(top,weight=2)
        self.table=ttk.Treeview(top,columns=('instance','pin','clkip','mode','status'),show='headings',height=10)
        for col,label,width in [('instance','真实末端实例',300),('pin','末端输入',120),('clkip','$var_clkip',350),('mode','模板类型',140),('status','生成状态',140)]:
            self.table.heading(col,text=label);self.table.column(col,width=width)
        scroll=ttk.Scrollbar(top,command=self.table.yview);scroll.pack(side='right',fill='y');self.table.configure(yscrollcommand=scroll.set);self.table.pack(fill='both',expand=True)
        self.table.bind('<<TreeviewSelect>>',self.detail)
        bottom=ttk.Frame(panes);panes.add(bottom,weight=3)
        choose=ttk.Frame(bottom);choose.pack(fill='x',pady=6)
        for var in ROLES:
            if var!='var_gt':ttk.Button(choose,text='选择 $'+var,command=lambda v=var:self.pick(v)).pack(side='left',padx=3)
        ttk.Button(choose,text='清除该分支选择',command=self.clear).pack(side='left',padx=6)
        self.mode=tk.StringVar();modes=ttk.Combobox(choose,textvariable=self.mode,values=('自动','with_mux','without_mux'),state='readonly',width=13);modes.pack(side='right');modes.bind('<<ComboboxSelected>>',self.set_mode)
        self.preview=tk.Text(bottom,wrap='none',font=('Consolas',10),height=15);scroll=ttk.Scrollbar(bottom,command=self.preview.yview);scroll.pack(side='right',fill='y')
        horizontal=ttk.Scrollbar(bottom,orient='horizontal',command=self.preview.xview);horizontal.pack(side='bottom',fill='x')
        self.preview.configure(yscrollcommand=scroll.set,xscrollcommand=horizontal.set);self.preview.pack(fill='both',expand=True)
        ttk.Button(footer,text='导入两套模板 JSON',command=self.import_templates).pack(side='left')
        ttk.Button(footer,text='保存两套模板 JSON',command=self.save_templates).pack(side='left',padx=8)
        ttk.Button(footer,text='取消当前任务',command=self.cancel).pack(side='left')
        self.export_button=ttk.Button(footer,text='全部分支 → 一个 SV 文件',command=self.export,state='disabled');self.export_button.pack(side='right')
        self.protocol('WM_DELETE_WINDOW',self.close);self.grab_set()
        self.registers.trace('w',self.registers_edited)
        self.refresh_registers()
        previous=saved.get('sv_plan_path')
        if previous and Path(previous).exists():
            try:
                plan=SVPlan(previous)
                if plan.source().get('architecture_signature')==architecture_signature(self.graph) and self.plan_matches_registers(plan):
                    self.plan=plan;self.directory=Path(previous).parent;self.tabs.select(review);self.refresh();self.export_button.configure(state='normal')
                else:self.status.set('架构、复位配置或寄存器结果已更新，请重新分析全部分支；模板和候选选择已保留。')
            except Exception as error:self.status.set('历史分支计划无法读取，请重新分析：'+str(error))
    def templates(self):return {k:e.get('1.0','end-1c') for k,e in self.editors.items()}
    def remember(self):
        fields=dict(sv_templates=self.templates(),sv_branch_selections=self.selections,
                    sv_register_trace_directory=self.registers.get().strip(),sv_plan_path=str(self.plan.path) if self.plan else '')
        self.app.ui_state.update(self.graph['config'],**fields)
    def plan_matches_registers(self,plan):
        source=plan.source();value=self.registers.get().strip()
        return source.get('registers','')==(str(result_directory(value)) if value else '') and source.get('register_result_stamp')==register_result_stamp(value)
    def invalidate_register_plan(self):
        if self.plan and not self.plan_matches_registers(self.plan):
            self.plan=None;self.rows={};self.table.delete(*self.table.get_children());self.preview.delete('1.0','end')
            self.export_button.configure(state='disabled')
            self.status.set('寄存器结果已更新，请重新分析全部分支；模板和候选选择已保留。')
    def registers_edited(self,*args):
        if self._setting_registers:return
        self.auto_registers.set(False);self.register_notice.set('手动选择寄存器结果；勾选“自动选择最新”可恢复自动选择。')
        self.invalidate_register_plan()
    def refresh_registers(self):
        if self.auto_registers.get():
            saved=self.app.ui_state.get(self.graph['config'])
            known=[self.registers.get()]+[saved.get(key) for key in
                   ('register_trace_directory','register_trace_completed_directory','sv_register_trace_directory')]
            path=latest_register_result(self.graph['config'],self.app.runs,known)
            self._setting_registers=True
            try:self.registers.set(path)
            finally:self._setting_registers=False
            self.register_notice.set('已自动选择本设计最新完成的寄存器结果（含待核对项的结果仍可分析）。' if path else
                                     '未找到本设计已完成的寄存器结果；可开始追踪或手动选择。L1 直达分支可留空。')
        else:self.register_notice.set('手动选择寄存器结果；勾选“自动选择最新”可恢复自动选择。')
        self.invalidate_register_plan()
    def open_register_trace(self):
        dialog=RegisterDialog(self.app);self.wait_window(dialog)
        if not self.closed:self.grab_set();self.refresh_registers()
    def choose_registers(self):
        path=filedialog.askopenfilename(parent=self,title='选择寄存器 manifest.json / register-trace.json',filetypes=[('JSON','*.json')])
        if path:self.registers.set(str(Path(path).parent))
    def start_process(self,command,operation):
        self.operation=operation;self.stream=(self.directory/(operation+'.log')).open('w',encoding='utf-8')
        self.process=subprocess.Popen(command,cwd=str(Path(__file__).resolve().parents[1]),stdout=self.stream,stderr=subprocess.STDOUT)
        self.analyze_button.configure(state='disabled');self.export_button.configure(state='disabled');self.after(150,self.poll)
    def analyze(self):
        if self.process and self.process.poll() is None:return
        if self.app.busy:self.status.set('请等待主界面当前任务完成，再分析分支。');return
        try:
            self.refresh_registers()
            if self.registers.get().strip() and not (Path(self.registers.get())/'manifest.json').exists():raise ValueError('寄存器结果目录无效；L1 直达分支可留空，普通分支仍需寄存器映射')
            self.remember();self.directory=self.app.runs/'.sv-templates'/uuid.uuid4().hex;self.directory.mkdir(parents=True)
            graph_path=self.directory/'architecture.json';write_source(graph_path,self.graph)
            command=[sys.executable,'-m','clocktree','sv-plan','--graph',str(graph_path),'--registers',self.registers.get(),'--out',str(self.directory)]
            self.status.set('正在读取末端、分支和逐位寄存器证据…');self.start_process(command,'plan')
        except (OSError,ValueError) as error:self.status.set(str(error))
    def poll(self):
        if self.closed:return
        if self.process.poll() is None:
            path=self.directory/'status.json'
            if self.operation=='plan' and path.exists():
                try:
                    status=json.loads(path.read_text(encoding='utf-8'));self.status.set(status['message']+' · '+str(status.get('context',{})))
                except (ValueError,OSError):pass
            self.after(200,self.poll);return
        self.stream.close();self.stream=None;self.analyze_button.configure(state='normal')
        if self.process.returncode:
            self.status.set(tail_text(self.directory/(self.operation+'.log'),1800))
        elif self.operation=='plan':
            self.plan=SVPlan(self.directory/'plan.sqlite');self.invalidate_register_plan()
            if self.plan:self.tabs.select(self.review);self.refresh(True)
            self.remember()
        else:
            result=json.loads((self.directory/'export.log').read_text(encoding='utf-8'))
            self.status.set('已生成 {} 段，跳过 {} 个分支；{}。所有候选寄存器及原因在同名 .mapping.json 中。'.format(result['blocks'],result['skipped'],self.output_path if result['sv_written'] else '本次没有可生成的分支，原 SV 文件保留'))
        if self.plan:self.export_button.configure(state='normal')
    def refresh(self,reset=False):
        if not self.plan:return
        if reset:self.offset=0
        selected=self.table.selection();self.table.delete(*self.table.get_children());total,rows=self.plan.branches(self.search.get(),self.offset)
        self.rows={r['id']:r for r in rows};ready=0
        for row in rows:
            result=self.plan.resolve(row,self.templates(),self.selections.get(row['id']));ready+=int(result['ready'])
            self.table.insert('','end',iid=row['id'],values=(row['instance'],row['pin'],row['clkip'],result['mode'],'已就绪' if result['ready'] else '需处理'))
        if selected and selected[0] in self.rows:self.table.selection_set(selected)
        self.status.set('第 {}–{} / {} 个分支末端 · 本页 {} 个已就绪。导出前会重新检查全部分支。'.format(self.offset+1 if rows else 0,self.offset+len(rows),total,ready))
    def page(self,step):
        if not self.plan:return
        total,_=self.plan.branches(self.search.get(),limit=0);offset=self.offset+100*step
        if 0<=offset<total:self.offset=offset;self.refresh()
    def current(self):
        selection=self.table.selection();return self.rows.get(selection[0]) if selection else None
    def detail(self,event=None):
        row=self.current()
        if not row:return
        result=self.plan.resolve(row,self.templates(),self.selections.get(row['id']));self.mode.set(self.selections.get(row['id'],{}).get('mode','自动'))
        text='\n'.join('$'+k+' = '+v for k,v in result['values'].items())
        if result.get('l1_input'):text+='\n\nL1 直达来源：'+', '.join(result['l1_input']['signals'])+'\n采用无选择器模板；$var_clkip 取末端输出，无输出器件取对应输入。'
        reset=result.get('reset',{})
        if 'var_rst' in result['values']:
            for origin in reset.get('origins',[reset]):
                text+='\n复位来源：起始器件 {} · L{}（用户配置：{}）'.format(origin.get('instance','?'),origin.get('layer','?'),origin.get('configured',''))
        if result['issues']:text+='\n\n需要处理：\n'+'\n'.join(result['issues'])
        else:text+='\n\n该分支生成预览：\n'+result['code']
        self.preview.delete('1.0','end');self.preview.insert('1.0',text)
    def pick(self,var):
        row=self.current()
        if row:
            def selected(key):self.selections.setdefault(row['id'],{})[var]=key;self.remember();self.refresh();self.detail()
            CandidateDialog(self,row,var,selected)
    def clear(self):
        row=self.current()
        if row:self.selections.pop(row['id'],None);self.remember();self.refresh();self.detail()
    def set_mode(self,event=None):
        row=self.current()
        if row:
            choice=self.selections.setdefault(row['id'],{})
            if self.mode.get()=='自动':choice.pop('mode',None)
            else:choice['mode']=self.mode.get()
            self.remember();self.refresh();self.detail()
    def export(self,path=None):
        if not self.plan or self.process and self.process.poll() is None:return
        self.refresh_registers()
        if not self.plan:return
        path=path or filedialog.asksaveasfilename(parent=self,defaultextension='.sv',initialfile='clock_branches.sv')
        if not path:return
        self.remember();request=self.directory/'templates.json';write_graph(request,dict(self.templates(),selections=self.selections))
        self.output_path=str(path);self.status.set('正在检查并生成全部分支…')
        self.start_process([sys.executable,'-m','clocktree','sv-export','--plan',str(self.plan.path),'--templates',str(request),'--out',str(path)],'export')
    def import_templates(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[('JSON','*.json')])
        if path:
            try:
                data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
                if not isinstance(data,dict) or any(not isinstance(data.get(k),str) for k in self.editors):raise ValueError('需要 with_mux 和 without_mux 两个文本模板')
                for key,editor in self.editors.items():editor.delete('1.0','end');editor.insert('1.0',data[key])
                self.remember();self.refresh()
            except (OSError,ValueError) as error:self.status.set(str(error))
    def save_templates(self):
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.json',initialfile='clock-sv-templates.json')
        if path:write_graph(path,self.templates());self.remember()
    def cancel(self):
        if self.process and self.process.poll() is None:self.process.terminate()
    def close(self):
        self.remember();self.cancel();self.closed=True
        if self.stream:self.stream.close()
        self.destroy()
