"""ClockTree7 native Tk/ttk desktop application (compatible with Tk 8.5)."""
import copy
import datetime
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

from .config import load_config, validate_config
from .environment import resolve_verdi
from .tkdiagram import ClockDiagram, draw_device
from .devices import DEVICE_TYPES, LABEL_TO_KIND, kind_label, type_labels, node_height, matching_rule
from .viewmodel import GraphView
from .profiles import TREE_PROFILES, LABEL_TO_TREE, tree_type, profile, rule_ports, empty_config, result_filename


ROOT = Path(__file__).resolve().parents[1]
INK, MUTED, BLUE, LINE = '#263852', '#7e8da3', '#2868df', '#dee5ef'


def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


class RuleDialog(tk.Toplevel):
    def __init__(self, app, layer, rule, callback):
        super().__init__(app)
        self.app, self.callback = app, callback
        self.tree_type = app._active_tree_type
        self.info = TREE_PROFILES[self.tree_type]
        self.input_key, self.output_key = self.info['inputs'], self.info['output']
        self.title('L{} · 模块与关注端口'.format(layer))
        self.configure(bg='white')
        self.resizable(False, False)
        self.transient(app)
        frame = ttk.Frame(self, padding=24, style='White.TFrame')
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='模块规则', style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame, text='选择器件类型，填写 module 和关注端口。连接由 elab 确定。', style='Muted.TLabel').pack(anchor='w', pady=(6,14))
        ttk.Label(frame,text='器件类型',style='Small.TLabel').pack(anchor='w',pady=(0,4))
        self.kind_var=tk.StringVar(value=kind_label(rule.get('kind'),self.tree_type) if rule.get('kind') in self.info['kinds'] else '请选择器件类型')
        self.kind_combo=ttk.Combobox(frame,textvariable=self.kind_var,values=type_labels({'tree_type':self.tree_type}),state='readonly')
        self.kind_combo.pack(fill='x')
        self.type_hint=tk.StringVar(value=('MUX 可多路复位输入；RSY_SYNC 为一个复位输入和一个复位输出。同步时钟口不参与复位连线。'
                                         if self.tree_type=='reset' else '选择器可多输入；门控、OCC、DIV 均为单输入，四类都只有一个关注输出。'))
        ttk.Label(frame,textvariable=self.type_hint,style='Muted.TLabel',wraplength=430).pack(anchor='w',pady=(6,5))
        self.fields = {}
        for key, label, value in [('module','Module 定义名',rule.get('module','')),
                                  (self.input_key,'关注的{}输入口（多个以逗号分隔）'.format(self.info['signal']),', '.join(rule.get(self.input_key,[]))),
                                  (self.output_key,'唯一关注的{}输出口'.format(self.info['signal']),rule.get(self.output_key,''))]:
            ttk.Label(frame, text=label, style='Small.TLabel').pack(anchor='w', pady=(7,4))
            variable = tk.StringVar(value=value)
            entry = ttk.Entry(frame, textvariable=variable, width=46)
            entry.pack(fill='x', ipady=3)
            self.fields[key] = variable
            if key=='module':
                self.first = entry
        ttk.Label(frame,text='器件外观预览',style='Small.TLabel').pack(anchor='w',pady=(12,6))
        self.preview=tk.Canvas(frame,width=440,height=158,bg='#101b2e',highlightthickness=0)
        self.preview.pack(fill='x')
        self.preview_fonts={}
        self.error = tk.StringVar()
        ttk.Label(frame, textvariable=self.error, foreground='#b74c4c', background='white', wraplength=380).pack(anchor='w', pady=10)
        actions = ttk.Frame(frame, style='White.TFrame')
        actions.pack(fill='x')
        ttk.Button(actions, text='取消', command=self.destroy).pack(side='right')
        ttk.Button(actions, text='保存规则', style='Accent.TButton', command=self.save).pack(side='right', padx=8)
        self.kind_var.trace('w',self.update_preview)
        for variable in self.fields.values():variable.trace('w',self.update_preview)
        self.update_preview()
        self.bind('<Return>', lambda e:self.save())
        self.bind('<Escape>', lambda e:self.destroy())
        self.update_idletasks()
        self.geometry('+{}+{}'.format(app.winfo_rootx()+(app.winfo_width()-self.winfo_width())//2,
                                     app.winfo_rooty()+max(40,(app.winfo_height()-self.winfo_height())//2)))
        self.grab_set()
        self.first.focus_set()

    def save(self):
        kind=LABEL_TO_KIND.get(self.kind_var.get())
        if kind not in self.info['kinds']:
            self.error.set('请选择“{}”中的一种器件类型。'.format(' / '.join(type_labels({'tree_type':self.tree_type}))))
            return
        rule = {'module':self.fields['module'].get().strip(),
                self.input_key:[x.strip() for x in self.fields[self.input_key].get().replace('，',',').split(',') if x.strip()],
                self.output_key:self.fields[self.output_key].get().strip(),'kind':kind}
        inputs,output=rule_ports(rule,self.tree_type)
        if not rule['module'] or not inputs or not output:
            self.error.set('请填写 module 名、至少一个输入口和唯一输出口。')
            return
        if len(set(inputs))!=len(inputs) or output in inputs:
            self.error.set('输入口不能重复，输入口和输出口不能相同。')
            return
        if kind in ('gate','occ','div','rsy_sync') and len(inputs)!=1:
            self.error.set('{}只允许一个关注输入口；多个{}输入请选择 MUX。'.format(kind_label(kind,self.tree_type),self.info['signal']))
            return
        self.callback(rule)
        self.destroy()

    def update_preview(self,*args):
        c=self.preview
        c.delete('all')
        kind=LABEL_TO_KIND.get(self.kind_var.get())
        if kind not in self.info['kinds']:
            c.create_text(220,78,text='选择器件类型后预览外观',fill='#8da7ca',font=(self.app.family,11))
            return
        pins=[v.strip() for v in self.fields[self.input_key].get().replace('，',',').split(',') if v.strip()]
        pins=pins or (['输入 1','输入 2'] if kind=='mux' else ['输入'])
        node={'id':'u_instance','module':self.fields['module'].get().strip() or 'module_name',
              'kind':kind,'inputs':pins,'output':self.fields[self.output_key].get().strip() or '输出'}
        h=node_height(node)
        scale=min(1.,142/h)
        draw_device(c,node,(440-242*scale)/2,8,242,h,scale,self.app.family,font_cache=self.preview_fonts)


class ClockTreeApp(tk.Tk):
    def __init__(self, runs=None, result=None, config=None):
        super().__init__(className='ClockTree7')
        self.title('ClockTree7  |  RTL 时钟树 / 复位树架构分析')
        icon=ROOT/'docs/clocktree7.gif'
        if icon.is_file():
            self._icon_image=tk.PhotoImage(master=self,file=str(icon))
            self.iconphoto(True,self._icon_image)
        self.configure(bg='#edf1f7')
        self.minsize(1280, 760)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        width, height = min(1840, sw-60), min(1000, sh-72)
        self.geometry('{}x{}+{}+{}'.format(max(1280,width),max(760,height),max(0,(sw-width)//2),max(0,(sh-height)//2)))
        families = set(tkfont.families(self))
        self.family = next((x for x in ['Microsoft YaHei UI','Microsoft YaHei','WenQuanYi Micro Hei','Noto Sans CJK SC','DejaVu Sans'] if x in families),'sans-serif')
        self.mono = 'Consolas' if 'Consolas' in families else 'DejaVu Sans Mono'
        self._style()
        self.runs = Path(runs or ROOT/'validation/runs').resolve()
        self.runs.mkdir(parents=True, exist_ok=True)
        self.events = queue.Queue()
        self.graph = None
        self.graph_file = None
        self.busy = False
        self.job_dir = None
        self.job_kind = None
        self.process = None
        self.cancel_event = threading.Event()
        self._closing = False
        self._filling = False
        self._log_mtime = None
        self._base_config = {}
        self._active_tree_type = 'clock'
        self._workspaces = {}
        self._config_dirty = False
        self.layers = [{'index':i,'hierarchy':'','modules':[]} for i in range(1,3)]
        self.active_layer = 1
        self.selected_object = None
        self.related_edges = {}
        self.match_query, self.match_index = '', -1
        self.last_job = None
        self.error_messages = []
        self.focused_view = False
        self.status = tk.StringVar(value='就绪 · 默认两级，可添加层级或导入已有配置')
        self.state = tk.StringVar(value='等待设计')
        self.project = tk.StringVar(value='未打开设计')
        self.view_label = tk.StringVar(value='全部时钟网络')
        self.scope_label = tk.StringVar(value='全部层次 / 全量关注结构')
        self.analysis_var = tk.StringVar(value='时钟树')
        self.diagram_name = tk.StringVar(value='时钟树架构图')
        self._build()
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Control-o>', lambda e:self.open_config())
        self.bind('<Control-s>', lambda e:self.save_config())
        self.bind('<Control-l>', lambda e:self.open_result())
        self.bind('<Control-plus>', lambda e:self.diagram.zoom(1.2))
        self.bind('<Control-minus>', lambda e:self.diagram.zoom(1/1.2))
        self.bind('<F5>', lambda e:self.diagram.fit())
        self.after(100, self._poll)
        self.after(120, self._set_sashes)
        self.set_config({'design':{},'layers':self.layers})
        self.refresh_runs()
        if config:
            self.after(180,lambda:self.open_config(config))
        elif result:
            self.after(180,lambda:self.load_result(Path(result)))

    def _style(self):
        self.option_add('*Font',(self.family,10))
        self.option_add('*Menu.Font',(self.family,10))
        self.option_add('*TCombobox*Listbox.font',(self.family,10))
        style=ttk.Style(self)
        style.theme_use('clam')
        style.configure('.',font=(self.family,10),foreground=INK,background='#f4f6fa',bordercolor=LINE)
        style.configure('TFrame',background='#f4f6fa')
        style.configure('White.TFrame',background='white')
        style.configure('TLabel',background='#f4f6fa',foreground=INK)
        style.configure('White.TLabel',background='white')
        style.configure('Title.TLabel',background='white',font=(self.family,14,'bold'))
        style.configure('Muted.TLabel',background='white',foreground=MUTED,font=(self.family,9))
        style.configure('Small.TLabel',background='white',foreground='#64758e',font=(self.family,9))
        style.configure('TButton',padding=(10,7),background='white',foreground=INK,borderwidth=1,relief='flat',focuscolor='')
        style.map('TButton',background=[('active','#edf3fe')],bordercolor=[('active','#8eb0ed')],foreground=[('disabled','#a4afbf')])
        style.configure('Accent.TButton',background=BLUE,foreground='white',bordercolor=BLUE,font=(self.family,10,'bold'),padding=(13,9))
        style.map('Accent.TButton',background=[('disabled','#aebcce'),('active','#205bc6')],foreground=[('disabled','#e8edf5'),('active','white')])
        style.configure('Layer.TButton',padding=(4,7),font=(self.family,9,'bold'))
        style.configure('LayerActive.TButton',padding=(4,7),background='#e5efff',foreground='#225fc8',bordercolor='#93b6f1',font=(self.family,9,'bold'))
        style.configure('TEntry',fieldbackground='white',padding=6,borderwidth=1,lightcolor=LINE,darkcolor=LINE)
        style.map('TEntry',bordercolor=[('focus','#5c91e8')])
        style.configure('TCombobox',fieldbackground='white',background='white',padding=5,arrowsize=12)
        style.map('TCombobox',fieldbackground=[('readonly','white')],selectbackground=[('readonly','white')],selectforeground=[('readonly',INK)])
        style.configure('Treeview',background='white',fieldbackground='white',foreground=INK,rowheight=29,borderwidth=0,font=(self.family,9))
        style.configure('Treeview.Heading',background='#f1f4f9',foreground='#6f7f96',padding=(7,8),font=(self.family,9),relief='flat')
        style.map('Treeview',background=[('selected','#e5efff')],foreground=[('selected','#205fc4')])
        style.configure('TNotebook',background='white',borderwidth=0,tabmargins=(0,0,0,0))
        style.configure('TNotebook.Tab',padding=(13,9),background='#f3f5f9',foreground='#7b8ba2',borderwidth=0)
        style.map('TNotebook.Tab',background=[('selected','white')],foreground=[('selected',BLUE)])
        style.configure('Horizontal.TProgressbar',background=BLUE,troughcolor='#e7edf5',borderwidth=0,thickness=3)
        style.configure('TPanedwindow',background=LINE)
        style.configure('Vertical.TScrollbar',background='#d8e0ec',troughcolor='#f5f7fa',borderwidth=0,arrowsize=10)

    def _build(self):
        header=tk.Frame(self,bg='#111e34',height=72)
        header.pack(fill='x')
        header.pack_propagate(False)
        logo=tk.Canvas(header,width=44,height=44,bg='#111e34',highlightthickness=0)
        logo.pack(side='left',padx=(20,10))
        logo.create_line(12,10,22,10,22,32,34,32,fill='#74b8ff',width=2)
        logo.create_line(22,21,34,21,fill='#74b8ff',width=2)
        for x,y in [(10,10),(35,21),(35,32)]:
            logo.create_rectangle(x-3,y-3,x+3,y+3,fill='#74b8ff',outline='')
        brand=tk.Frame(header,bg='#111e34')
        brand.pack(side='left')
        tk.Label(brand,text='ClockTree7',font=(self.family,21,'bold'),fg='white',bg='#111e34').pack(anchor='w')
        tk.Label(brand,text='RTL  时钟 / 复位树架构分析',font=(self.family,8),fg='#8ca6ca',bg='#111e34').pack(anchor='w')
        tk.Label(header,text='DESIGN  /  CONNECTIVITY  /  INSIGHT',font=(self.family,8),fg='#6682a9',bg='#111e34').pack(side='left',padx=30)
        for text,command in [('导出…',self.export_menu),('打开结果',self.open_result),('保存配置',self.save_config),('导入配置',self.open_config),('查看示例',self.open_example),('新建配置',self.new_config)]:
            button=tk.Button(header,text=text,command=command,bg='#233a5a',fg='#e3edfc',activebackground='#31517c',activeforeground='white',relief='flat',borderwidth=0,padx=15,pady=8,cursor='hand2',font=(self.family,9))
            button.pack(side='right',padx=(0,9))
            if text=='打开结果':self.open_result_button=button
        project_bar=ttk.Frame(self,padding=(19,9),style='White.TFrame')
        project_bar.pack(fill='x')
        ttk.Label(project_bar,text='WORKSPACE  /',style='Muted.TLabel').pack(side='left')
        self.analysis_combo=ttk.Combobox(project_bar,textvariable=self.analysis_var,values=[p['label'] for p in TREE_PROFILES.values()],width=9,state='readonly')
        self.analysis_combo.pack(side='left',padx=(10,0))
        self.analysis_combo.bind('<<ComboboxSelected>>',self.switch_tree_type)
        ttk.Label(project_bar,textvariable=self.project,style='White.TLabel',font=(self.family,10,'bold')).pack(side='left',padx=10)
        self.state_badge=tk.Label(project_bar,textvariable=self.state,bg='#edf2fa',fg='#70819a',font=(self.family,9),padx=10,pady=4)
        self.state_badge.pack(side='right')
        self.panes=ttk.Panedwindow(self,orient='horizontal')
        self.panes.pack(fill='both',expand=True,pady=(1,0))
        self.left=ttk.Frame(self.panes,width=310,style='White.TFrame')
        self.center=ttk.Frame(self.panes,style='White.TFrame')
        self.right=ttk.Frame(self.panes,width=300,style='White.TFrame')
        self.panes.add(self.left,weight=0)
        self.panes.add(self.center,weight=1)
        self.panes.add(self.right,weight=0)
        self._build_left()
        self._build_center()
        self._build_inspector()
        bottom=tk.Frame(self,bg='#e9eef6',height=29)
        bottom.pack(fill='x')
        bottom.pack_propagate(False)
        tk.Label(bottom,textvariable=self.status,fg='#657894',bg='#e9eef6',font=(self.family,8),anchor='w').pack(side='left',fill='x',expand=True,padx=15)
        tk.Label(bottom,text='TK {}  ·  NATIVE CANVAS'.format(self.tk.call('info','patchlevel')),fg='#8798b0',bg='#e9eef6',font=(self.family,8)).pack(side='right',padx=14)

    def _build_left(self):
        head=ttk.Frame(self.left,padding=(17,18,17,14),style='White.TFrame')
        head.pack(fill='x')
        ttk.Label(head,text='设计工作台',style='Title.TLabel').pack(anchor='w')
        ttk.Label(head,text='从 elab 自动解析全部关注结构',style='Muted.TLabel').pack(anchor='w',pady=(5,0))
        self.left_tabs=ttk.Notebook(self.left)
        self.left_tabs.pack(fill='both',expand=True)
        settings_outer=ttk.Frame(self.left_tabs,style='White.TFrame')
        footer=ttk.Frame(settings_outer,padding=(15,8,15,12),style='White.TFrame')
        footer.pack(side='bottom',fill='x')
        scroll_area=ttk.Frame(settings_outer,style='White.TFrame')
        scroll_area.pack(fill='both',expand=True)
        self.settings_canvas=tk.Canvas(scroll_area,bg='white',highlightthickness=0,width=280)
        scroll=ttk.Scrollbar(scroll_area,command=self.settings_canvas.yview)
        self.settings_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        self.settings_canvas.pack(fill='both',expand=True)
        settings=ttk.Frame(self.settings_canvas,padding=(15,12),style='White.TFrame')
        window=self.settings_canvas.create_window(0,0,window=settings,anchor='nw')
        settings.bind('<Configure>',lambda e:self.settings_canvas.configure(scrollregion=self.settings_canvas.bbox('all')))
        self.settings_canvas.bind('<Configure>',lambda e:self.settings_canvas.itemconfigure(window,width=e.width))
        self._settings_inner=settings
        history=ttk.Frame(self.left_tabs,padding=(10,12),style='White.TFrame')
        browser=ttk.Frame(self.left_tabs,padding=(10,12),style='White.TFrame')
        for frame,title in [(settings_outer,'提取配置'),(history,'运行记录'),(browser,'实例导航')]:
            self.left_tabs.add(frame,text=title)
        self.top_var=tk.StringVar()
        self.mode_var=tk.StringVar(value='已有 elab / KDB')
        self.path_var=tk.StringVar()
        self.hierarchy_var=tk.StringVar()
        top_label=ttk.Frame(settings,style='White.TFrame')
        top_label.pack(fill='x')
        ttk.Label(top_label,text='设计顶层',style='Small.TLabel').pack(side='left')
        ttk.Button(top_label,text='运行环境',width=7,command=self.edit_environment).pack(side='right')
        ttk.Entry(settings,textvariable=self.top_var).pack(fill='x',pady=(5,10))
        ttk.Label(settings,text='设计入口',style='Small.TLabel').pack(anchor='w')
        ttk.Combobox(settings,textvariable=self.mode_var,values=['已有 elab / KDB','RTL 文件列表'],state='readonly').pack(fill='x',pady=(5,6))
        pathrow=ttk.Frame(settings,style='White.TFrame')
        pathrow.pack(fill='x')
        ttk.Entry(pathrow,textvariable=self.path_var).pack(side='left',fill='x',expand=True)
        ttk.Button(pathrow,text='…',width=2,command=self.browse_design).pack(side='right',padx=(4,0))
        ttk.Separator(settings).pack(fill='x',pady=15)
        rule_heading=ttk.Frame(settings,style='White.TFrame')
        rule_heading.pack(fill='x')
        self.layer_count_var=tk.StringVar(value='传播规则 · 2 级')
        ttk.Label(rule_heading,textvariable=self.layer_count_var,style='White.TLabel',font=(self.family,11,'bold')).pack(side='left')
        ttk.Button(rule_heading,text='器件模型',width=7,command=self.open_device_library).pack(side='right')
        self.layerrow=ttk.Frame(settings,style='White.TFrame')
        self.layerrow.pack(fill='x',pady=(10,6))
        self.layer_buttons=[]
        layer_actions=ttk.Frame(settings,style='White.TFrame')
        layer_actions.pack(fill='x',pady=(0,10))
        self.add_layer_button=ttk.Button(layer_actions,text='+ 添加层级',command=self.add_layer)
        self.add_layer_button.pack(side='left')
        self.remove_layer_button=ttk.Button(layer_actions,text='删除当前层',command=self.remove_layer)
        self.remove_layer_button.pack(side='left',padx=(5,0))
        self.layer_title=ttk.Label(settings,text='L1 · RTL 层次范围',style='Small.TLabel')
        self.layer_title.pack(anchor='w')
        ttk.Entry(settings,textvariable=self.hierarchy_var).pack(fill='x',pady=(5,10))
        self.rules_tree=ttk.Treeview(settings,columns=('kind','inputs','output'),show='tree headings',height=3,selectmode='browse')
        self.rules_tree.heading('#0',text='Module')
        self.rules_tree.heading('kind',text='类型')
        self.rules_tree.heading('inputs',text='输入')
        self.rules_tree.heading('output',text='输出')
        self.rules_tree.column('#0',width=96,minwidth=58)
        self.rules_tree.column('kind',width=54,minwidth=45)
        self.rules_tree.column('inputs',width=63,minwidth=35)
        self.rules_tree.column('output',width=55,minwidth=35)
        self.rules_tree.pack(fill='x')
        self.rules_tree.bind('<Double-1>',lambda e:self.edit_rule())
        actions=ttk.Frame(settings,style='White.TFrame')
        actions.pack(fill='x',pady=(7,9))
        for text,command in [('+ 添加',self.add_rule),('编辑',self.edit_rule),('删除',self.remove_rule)]:
            ttk.Button(actions,text=text,command=command,width=6).pack(side='left',padx=(0,4))
        ttk.Label(settings,text='多输入 MUX · 同层串接 · 跨层跳接\n分支允许在任意层结束',style='Muted.TLabel',justify='left').pack(anchor='w',pady=(3,4))
        self.run_button=ttk.Button(footer,text='开始提取全部时钟树',style='Accent.TButton',command=self.start_extraction)
        self.run_button.pack(fill='x',pady=(10,5))
        self.cancel_button=ttk.Button(footer,text='取消当前提取',command=self.cancel_job,state='disabled')
        self.cancel_button.pack(fill='x')
        self.progress=ttk.Progressbar(footer,mode='indeterminate')
        self.progress.pack(fill='x',pady=(8,0))
        self.progress.configure(mode='determinate',value=0)
        self.bind('<MouseWheel>',self._config_wheel,add='+')
        self.bind('<Button-4>',self._config_wheel,add='+')
        self.bind('<Button-5>',self._config_wheel,add='+')
        for variable in [self.top_var,self.mode_var,self.path_var,self.hierarchy_var]:
            variable.trace('w',self.mark_dirty)
        self.runs_tree=ttk.Treeview(history,columns=('count','state'),show='tree headings',selectmode='browse')
        self.runs_tree.heading('#0',text='运行记录')
        self.runs_tree.heading('count',text='实例')
        self.runs_tree.heading('state',text='状态')
        self.runs_tree.column('#0',width=145,minwidth=80)
        self.runs_tree.column('count',width=58,anchor='e',minwidth=45)
        self.runs_tree.column('state',width=52,minwidth=40)
        self.runs_tree.pack(fill='both',expand=True)
        self.runs_tree.bind('<Double-1>',lambda e:self.open_history())
        ttk.Button(history,text='打开所选运行',command=self.open_history).pack(fill='x',pady=(9,5))
        ttk.Button(history,text='刷新记录',command=self.refresh_runs).pack(fill='x')
        self.nav_query=tk.StringVar()
        navtop=ttk.Frame(browser,style='White.TFrame')
        navtop.pack(fill='x',pady=(0,8))
        ttk.Entry(navtop,textvariable=self.nav_query).pack(side='left',fill='x',expand=True)
        ttk.Button(navtop,text='筛选',width=5,command=self.refresh_nodes).pack(side='right',padx=(4,0))
        self.nav_tree=ttk.Treeview(browser,columns=('layer',),show='tree headings',selectmode='browse')
        self.nav_tree.heading('#0',text='真实实例路径')
        self.nav_tree.heading('layer',text='层')
        self.nav_tree.column('#0',width=230,minwidth=130)
        self.nav_tree.column('layer',width=35,stretch=False)
        self.nav_tree.pack(fill='both',expand=True)
        self.nav_tree.bind('<Double-1>',lambda e:self.locate_nav())
        self.nav_caption=ttk.Label(browser,text='尚未载入设计',style='Muted.TLabel',wraplength=270)
        self.nav_caption.pack(anchor='w',pady=9)
        ttk.Button(browser,text='定位所选实例',command=self.locate_nav).pack(fill='x')

    def _build_center(self):
        title=ttk.Frame(self.center,padding=(18,16,18,9),style='White.TFrame')
        self.diagram_title=title
        title.pack(fill='x')
        ttk.Label(title,textvariable=self.diagram_name,style='Title.TLabel').pack(side='left')
        ttk.Label(title,textvariable=self.view_label,style='Muted.TLabel').pack(side='right')
        stats=ttk.Frame(self.center,padding=(10,4,10,12),style='White.TFrame')
        self.stats_frame=stats
        stats.pack(fill='x')
        self.stats={}
        for key,label in [('instances','目标实例'),('edges','端口连线'),('roots','根结构'),('terminals','分支末端'),('components','独立网络'),('unresolved','未解析')]:
            frame=tk.Frame(stats,bg='#f5f7fb',highlightbackground='#e7edf5',highlightthickness=1)
            frame.pack(side='left',fill='x',expand=True,padx=4)
            value=tk.StringVar(value='—')
            tk.Label(frame,textvariable=value,font=(self.family,20,'bold'),fg=INK,bg='#f5f7fb',anchor='w').pack(fill='x',padx=11,pady=(8,0))
            tk.Label(frame,text=label,font=(self.family,8),fg=MUTED,bg='#f5f7fb',anchor='w').pack(fill='x',padx=12,pady=(0,9))
            self.stats[key]=value
        toolbar=ttk.Frame(self.center,padding=(14,5,14,8),style='White.TFrame')
        self.diagram_toolbar=toolbar
        toolbar.pack(fill='x')
        self.component_var=tk.StringVar(value='全部时钟网络')
        self.component_combo=ttk.Combobox(toolbar,textvariable=self.component_var,values=['全部时钟网络'],width=17,state='readonly')
        self.component_combo.pack(side='left',padx=(0,6))
        self.component_combo.bind('<<ComboboxSelected>>',self.change_component)
        self.view_mode=tk.StringVar(value='实例明细')
        self.mode_combo=ttk.Combobox(toolbar,textvariable=self.view_mode,values=['实例明细','RTL 层次概览'],width=12,state='readonly')
        self.mode_combo.pack(side='left',padx=(0,6))
        self.mode_combo.bind('<<ComboboxSelected>>',self.change_mode)
        ttk.Button(toolbar,text='返回全网',command=lambda:self.diagram.reset_scope()).pack(side='left',padx=(0,6))
        self.focus_button=ttk.Button(toolbar,text='专注画布',command=self.toggle_focus)
        self.focus_button.pack(side='left')
        ttk.Button(toolbar,text='全图',command=lambda:self.diagram.fit()).pack(side='right')
        ttk.Button(toolbar,text='+',width=3,command=lambda:self.diagram.zoom(1.25)).pack(side='right',padx=4)
        self.zoom_label=ttk.Label(toolbar,text='100%',style='Muted.TLabel',width=6,anchor='center')
        self.zoom_label.pack(side='right')
        ttk.Button(toolbar,text='−',width=3,command=lambda:self.diagram.zoom(.8)).pack(side='right',padx=4)
        searchrow=ttk.Frame(self.center,padding=(14,0,14,9),style='White.TFrame')
        searchrow.pack(fill='x')
        ttk.Label(searchrow,text='定位',style='Small.TLabel').pack(side='left',padx=(0,8))
        self.search_var=tk.StringVar()
        self.search_entry=ttk.Entry(searchrow,textvariable=self.search_var)
        self.search_entry.pack(side='left',fill='x',expand=True)
        self.search_entry.bind('<Return>',lambda e:self.locate_query())
        self.locate_button=ttk.Button(searchrow,text='查找实例',command=self.locate_query)
        self.locate_button.pack(side='left',padx=6)
        ttk.Button(searchrow,text='下一个',command=lambda:self.locate_query(True)).pack(side='left')
        self.match_label=ttk.Label(searchrow,text='路径 / module',style='Muted.TLabel',width=15,anchor='e')
        self.match_label.pack(side='left',padx=(6,0))
        scopebar=tk.Frame(self.center,bg='#edf2fa',height=31)
        scopebar.pack(fill='x')
        scopebar.pack_propagate(False)
        tk.Label(scopebar,textvariable=self.scope_label,font=(self.family,8),fg='#6680a4',bg='#edf2fa',anchor='w',width=1).pack(side='left',fill='x',expand=True,padx=14)
        legend=tk.Canvas(scopebar,width=158,height=31,bg='#edf2fa',highlightthickness=0)
        legend.create_oval(6,12,12,18,fill='#29aa85',outline='')
        legend.create_text(18,15,text='根',anchor='w',fill='#7e93b0',font=(self.family,8))
        legend.create_oval(48,12,54,18,fill='#d39c3f',outline='')
        legend.create_text(60,15,text='末端',anchor='w',fill='#7e93b0',font=(self.family,8))
        legend.create_rectangle(100,10,110,20,outline='#698dbd',dash=(2,1))
        legend.create_text(116,15,text='聚合',anchor='w',fill='#7e93b0',font=(self.family,8))
        legend.pack(side='right',padx=12)
        devices=tk.Canvas(scopebar,width=312,height=31,bg='#edf2fa',highlightthickness=0)
        self.devices_legend=devices
        devices.create_polygon(4,7,22,11,22,21,4,25,fill='#e0efff',outline=DEVICE_TYPES['mux']['color'])
        devices.create_text(29,16,text='选择器',anchor='w',fill='#6680a4',font=(self.family,8))
        devices.create_arc(81,8,103,24,start=-90,extent=180,style='pieslice',fill='#d7f3ea',outline='#2eac8d')
        devices.create_rectangle(81,8,92,24,fill='#d7f3ea',outline='#2eac8d')
        devices.create_text(110,16,text='门控',anchor='w',fill='#6680a4',font=(self.family,8))
        devices.create_polygon(164,8,180,8,184,12,184,21,180,25,164,25,160,21,160,12,fill='#fff0d7',outline='#d09b45')
        devices.create_text(192,16,text='OCC',anchor='w',fill='#6680a4',font=(self.family,8))
        devices.create_polygon(234,8,250,8,257,16,250,25,234,25,227,16,fill='#eee5ff',outline='#9a77df')
        devices.create_text(264,16,text='DIV',anchor='w',fill='#6680a4',font=(self.family,8))
        devices.pack(side='right')
        self.diagram=ClockDiagram(self.center,self.family,self.inspect_node,self.inspect_edges,self.view_changed,self.set_status)
        self.diagram.pack(fill='both',expand=True)
        self.log_toggle=ttk.Button(self.center,text='任务日志与诊断  ▴',command=self.toggle_log)
        self.log_toggle.pack(fill='x')
        self.log_frame=ttk.Frame(self.center,style='White.TFrame')
        self.log_visible=False
        self.log_text=tk.Text(self.log_frame,height=6,font=(self.mono,8),bg='#f6f8fc',fg='#647792',relief='flat',wrap='word',padx=13,pady=8,state='disabled')
        scrollbar=ttk.Scrollbar(self.log_frame,command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y')
        self.log_text.pack(fill='both',expand=True)

    def _build_inspector(self):
        head=ttk.Frame(self.right,padding=(16,18,16,13),style='White.TFrame')
        head.pack(fill='x')
        ttk.Label(head,text='属性与连接依据',style='Title.TLabel').pack(anchor='w')
        ttk.Label(head,text='实际实例 · 关注端口 · NPI 证据',style='Muted.TLabel').pack(anchor='w',pady=(5,0))
        self.inspector_tabs=ttk.Notebook(self.right)
        self.inspector_tabs.pack(fill='both',expand=True)
        prop=ttk.Frame(self.inspector_tabs,style='White.TFrame',padding=11)
        ev=ttk.Frame(self.inspector_tabs,style='White.TFrame')
        diag=ttk.Frame(self.inspector_tabs,style='White.TFrame')
        for f,t in [(prop,'属性'),(ev,'连线证据'),(diag,'诊断')]:
            self.inspector_tabs.add(f,text=t)
        self.prop_text=self._scroll_text(prop,self.family,10)
        self.prop_text.pack(fill='both',expand=True)
        for tag,opts in [('heading',{'font':(self.family,13,'bold'),'foreground':INK,'spacing3':9}),
                         ('label',{'font':(self.family,8),'foreground':MUTED,'spacing1':10}),
                         ('value',{'font':(self.family,9),'foreground':INK,'spacing3':6}),
                         ('good',{'foreground':'#21886f'}),('accent',{'foreground':BLUE})]:
            self.prop_text.tag_configure(tag,**opts)
        self.expand_button=ttk.Button(prop,text='展开此 RTL 层次',command=self.expand_selected,state='disabled')
        self.expand_button.pack(fill='x',pady=(6,6))
        paths=ttk.Frame(prop,style='White.TFrame')
        paths.pack(fill='x')
        ttk.Button(paths,text='上游全路径',command=lambda:self.highlight_path('upstream')).pack(side='left',fill='x',expand=True,padx=(0,4))
        ttk.Button(paths,text='下游全路径',command=lambda:self.highlight_path('downstream')).pack(side='left',fill='x',expand=True)
        self.connection_title=ttk.Label(prop,text='相关端口连接',style='Small.TLabel')
        self.connection_title.pack(anchor='w',pady=(16,5))
        self.connections=ttk.Treeview(prop,columns=('direction','pin'),show='tree headings',height=5,selectmode='browse')
        self.connections.heading('#0',text='对端实例')
        self.connections.heading('direction',text='方向')
        self.connections.heading('pin',text='端口')
        self.connections.column('#0',width=138,minwidth=70)
        self.connections.column('direction',width=42,stretch=False)
        self.connections.column('pin',width=64,minwidth=35)
        self.connections.pack(fill='x')
        self.connections.bind('<<TreeviewSelect>>',self.inspect_related)
        self.connections.bind('<Double-1>',self.locate_related)
        ttk.Label(prop,text='单击连线查看证据 · 双击对端定位',style='Muted.TLabel').pack(anchor='w',pady=(6,0))
        self.evidence_text=self._scroll_text(ev,self.mono,8)
        self.diag_text=self._scroll_text(diag,self.family,9)
        self._properties('全量时钟树架构', [('选择节点','查看真实 RTL 实例、输入输出端口和上下游连接。'),('选择连线','查看驱动和负载两个方向的 NPI 路径。'),('层次概览','虚线框表示多个真实实例的聚合，双击展开。')])

    def _text(self,parent,family,size,height=10):
        return tk.Text(parent,font=(family,size),height=height,bg='white',fg=INK,borderwidth=0,highlightthickness=0,wrap='word',padx=3,pady=8,state='disabled',width=1)

    def _scroll_text(self,parent,family,size):
        frame=ttk.Frame(parent,style='White.TFrame',padding=9)
        frame.pack(fill='both',expand=True)
        text=self._text(frame,family,size)
        scroll=ttk.Scrollbar(frame,command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y')
        text.pack(fill='both',expand=True)
        return text

    def _set_sashes(self):
        if self.focused_view:return
        self.update_idletasks()
        self.panes.sashpos(0,310)
        self.panes.sashpos(1,max(800,self.winfo_width()-302))

    def toggle_focus(self):
        self.focused_view=not self.focused_view
        if self.focused_view:
            self.panes.forget(self.left)
            self.panes.forget(self.right)
            self.diagram_title.pack_forget()
            self.stats_frame.pack_forget()
            self.focus_button.configure(text='恢复面板')
        else:
            self.panes.insert(0,self.left,weight=0)
            self.panes.add(self.right,weight=0)
            self.diagram_title.pack(fill='x',before=self.diagram_toolbar)
            self.stats_frame.pack(fill='x',before=self.diagram_toolbar)
            self.focus_button.configure(text='专注画布')
            self.after(50,self._set_sashes)
        self.after(80,self.diagram.fit)

    def _config_wheel(self,event):
        if str(event.widget).startswith(str(self._settings_inner)) and event.widget is not self.rules_tree:
            up=event.num==4 or (getattr(event,'delta',0)>0)
            self.settings_canvas.yview_scroll(-2 if up else 2,'units')
            return 'break'

    def set_status(self,text):
        self.status.set(str(text))

    def mark_dirty(self,*args):
        if not self._filling and not self.busy:self._config_dirty=True
        if not self._filling and self.graph and not self.busy:
            self._badge('规则已修改 · 待重新提取','warn')

    def _badge(self,text,kind='idle'):
        colors={'pass':('#e2f4ec','#217d5b'),'warn':('#fff0da','#a36a17'),'busy':('#e6efff','#2868df'),'idle':('#edf2fa','#70819a')}
        bg,fg=colors[kind]
        self.state.set(text)
        self.state_badge.configure(bg=bg,fg=fg)

    def _remember_workspace(self):
        self._workspaces[self._active_tree_type]={
            'config':self.get_config(validate=False),'graph':self.graph,'path':self.graph_file,
            'dirty':self._config_dirty}

    def _clear_result(self):
        self.graph,self.graph_file=None,None
        self.selected_object,self.related_edges=None,{}
        self.match_query,self.match_index='',-1
        self.search_var.set('')
        self.nav_query.set('')
        self.view_mode.set('实例明细')
        self.zoom_label.configure(text='100%')
        self.component_ids=[None]
        info=TREE_PROFILES[self._active_tree_type]
        self.component_combo.configure(values=['全部'+info['network']])
        self.component_combo.current(0)
        self.view_label.set('全部'+info['network'])
        self.scope_label.set('全部层次 / 全量关注结构')
        self.project.set('未打开'+info['signal']+'设计')
        for value in self.stats.values():value.set('—')
        self.connections.delete(*self.connections.get_children())
        self.nav_tree.delete(*self.nav_tree.get_children())
        self.nav_caption.configure(text='尚未载入设计')
        self.expand_button.configure(state='disabled')
        self.connection_title.configure(text='相关端口连接')
        self.match_label.configure(text='路径 / module')
        self._properties(info['label']+'架构', [('开始','填写层次和模块规则，或打开已有结果。'),
                                            ('连接依据','实例与连接由 RTL elab 自动解析。')])
        for widget in (self.diag_text,self.evidence_text,self.log_text):self._replace_text(widget,'')
        self.show_log(False)
        self.diagram.clear(self._active_tree_type)
        self._badge('等待设计')

    def _apply_profile(self):
        info=TREE_PROFILES[self._active_tree_type]
        self.analysis_var.set(info['label'])
        self.diagram_name.set(info['label']+'架构图')
        self.run_button.configure(text='开始提取全部'+info['label'])
        self.rules_tree.heading('inputs',text='输入')
        self.rules_tree.heading('output',text='输出')
        self.rules_tree.column('kind',width=82 if self._active_tree_type=='reset' else 54)
        self.rules_tree.column('#0',width=80 if self._active_tree_type=='reset' else 96)
        self.rules_tree.column('inputs',width=55 if self._active_tree_type=='reset' else 63)
        if not self.graph:
            self.component_combo.configure(values=['全部'+info['network']])
            self.component_combo.current(0)
            self.view_label.set('全部'+info['network'])
        self.diagram.tree_type=self._active_tree_type
        self.diagram.schedule()
        c=self.devices_legend
        c.delete('all')
        step=130 if self._active_tree_type=='reset' else 78
        for i,kind in enumerate(info['kinds']):
            x=i*step+4
            color=DEVICE_TYPES[kind]['color']
            if kind=='mux':
                c.create_polygon(x,7,x+18,11,x+18,21,x,25,fill='#e0efff',outline=color)
            elif kind=='gate':
                c.create_arc(x,8,x+22,24,start=-90,extent=180,style='pieslice',fill='#d7f3ea',outline='#2eac8d')
                c.create_rectangle(x,8,x+11,24,fill='#d7f3ea',outline='#2eac8d')
            elif kind=='rsy_sync':
                c.create_rectangle(x,8,x+24,25,fill='#ffe6ef',outline='#c86c93')
                c.create_line(x+7,12,x+7,21,x+17,21,x+17,12,fill='#c86c93')
            else:
                c.create_polygon(x+4,8,x+20,8,x+24,12,x+24,21,x+20,25,x+4,25,x,21,x,12,fill='#eee5ff' if kind=='div' else '#fff0d7',outline=color)
            label={'mux':'MUX' if self._active_tree_type=='reset' else '选择器','gate':'门控'}.get(kind,DEVICE_TYPES[kind]['short'])
            c.create_text(x+29,16,text=label,anchor='w',fill='#6680a4',font=(self.family,8))
        c.configure(width=step*len(info['kinds']))

    def switch_tree_type(self,event=None):
        target=event if isinstance(event,str) and event in TREE_PROFILES else LABEL_TO_TREE[self.analysis_var.get()]
        if self.busy:
            self.analysis_var.set(TREE_PROFILES[self._active_tree_type]['label'])
            return
        if target==self._active_tree_type:return
        saved=self._workspaces.get(target)
        if saved and saved['graph'] is not None:
            self.show_graph(saved['graph'],saved['path'])
            self.set_config(saved['config'])
            if saved['dirty']:self.mark_dirty()
        else:
            self.set_config(saved['config'] if saved else empty_config(target))
        self.set_status('已切换到{}；两类分析的配置和图形分别保留。'.format(TREE_PROFILES[target]['label']))

    def rebuild_layer_buttons(self):
        for button in self.layer_buttons:button.destroy()
        self.layer_buttons=[]
        for index in range(1,len(self.layers)+1):
            button=ttk.Button(self.layerrow,text='L{}'.format(index),width=4,style='Layer.TButton',command=lambda i=index:self.select_layer(i))
            button.grid(row=(index-1)//5,column=(index-1)%5,sticky='ew',padx=(0,3),pady=(0,4))
            self.layer_buttons.append(button)
        for column in range(5):self.layerrow.columnconfigure(column,weight=1)
        self.layer_count_var.set('传播规则 · {} 级'.format(len(self.layers)))

    def add_layer(self):
        self.layers[self.active_layer-1]['hierarchy']=self.hierarchy_var.get().strip()
        self.layers.append({'index':len(self.layers)+1,'hierarchy':'','modules':[]})
        self.rebuild_layer_buttons()
        self.select_layer(len(self.layers),save=False)
        self.mark_dirty()
        self.set_status('已添加 L{}，层级数量不设固定上限。'.format(len(self.layers)))

    def remove_layer(self):
        if len(self.layers)==1:
            self.set_status('至少保留一级传播规则。')
            return
        layer=self.layers[self.active_layer-1]
        if layer.get('modules') and not messagebox.askyesno('删除层级','删除 L{} 及其全部模块规则？后续层级将顺序重新编号。'.format(self.active_layer),parent=self):return
        del self.layers[self.active_layer-1]
        for i,layer in enumerate(self.layers,1):layer['index']=i
        self.rebuild_layer_buttons()
        self.select_layer(min(self.active_layer,len(self.layers)),save=False)
        self.mark_dirty()

    def new_config(self):
        if self.busy:return
        self.set_config(empty_config(self._active_tree_type))
        self.mark_dirty()
        self.set_status('已新建两级配置。填写设计与规则，或点击“添加层级”。')

    def open_example(self):
        self.load_result(ROOT/('examples/reset_tree/resettree.json' if self._active_tree_type=='reset' else 'examples/flexible_layers/clocktree.json'))

    def select_layer(self,index,save=True):
        if save:
            self.layers[self.active_layer-1]['hierarchy']=self.hierarchy_var.get().strip()
        self.active_layer=index
        self._filling=True
        self.hierarchy_var.set(self.layers[index-1].get('hierarchy',''))
        self._filling=False
        self.layer_title.configure(text='L{} · RTL 层次范围'.format(index))
        for i,button in enumerate(self.layer_buttons,1):
            button.configure(style='LayerActive.TButton' if i==index else 'Layer.TButton')
        self.refresh_rules()

    def refresh_rules(self):
        self.rules_tree.delete(*self.rules_tree.get_children())
        rules=self.layers[self.active_layer-1].get('modules',[])
        self.rules_tree.configure(height=min(6,max(3,len(rules))))
        for i,rule in enumerate(rules):
            inputs,output=rule_ports(rule,self._active_tree_type)
            self.rules_tree.insert('', 'end',iid=str(i),text=rule['module'],values=(kind_label(rule.get('kind'),self._active_tree_type),', '.join(inputs),output))

    def add_rule(self,rule=None):
        if rule is not None:
            self.layers[self.active_layer-1].setdefault('modules',[]).append(copy.deepcopy(rule))
            self.refresh_rules()
            self.mark_dirty()
            return
        RuleDialog(self,self.active_layer,{},lambda item:self.add_rule(item))

    def edit_rule(self):
        selection=self.rules_tree.selection()
        if not selection:
            self.set_status('先选择要编辑的 module 规则。')
            return
        index=int(selection[0])
        rules=self.layers[self.active_layer-1]['modules']
        def commit(item):
            old=rules[index]
            rules[index]=item
            self.refresh_rules()
            if not self._apply_kind_change(old,item):self.mark_dirty()
        RuleDialog(self,self.active_layer,rules[index],commit)

    def _apply_kind_change(self,old,new):
        # Appearance-only changes can reuse already-proven elab connectivity.
        if not self.graph or {k:v for k,v in old.items() if k!='kind'}!={k:v for k,v in new.items() if k!='kind'}:
            return False
        if old.get('kind')==new.get('kind'):return True
        if self.active_layer>len(self.graph.get('config',{}).get('layers',[])):return False
        layer=self.graph['config']['layers'][self.active_layer-1]
        if layer['hierarchy']!=self.hierarchy_var.get().strip():return False
        matching=[n for n in self.graph['nodes'] if matching_rule(n,layer,new)]
        if not matching:return False
        for rule in layer['modules']:
            if all(rule.get(k)==old.get(k) for k in ('module',profile(self._base_config)['inputs'],profile(self._base_config)['output'])):rule['kind']=new['kind']
        for node in matching:
            node['kind'],node['kind_source']=new['kind'],'configured'
        self.graph.setdefault('presentation',{}).setdefault('kind_overrides',[]).append({
            'layer':self.active_layer,'module':new['module'],'kind':new['kind'],'instances':len(matching),
            'changed_at':datetime.datetime.now().isoformat(),'source':'user_type_selection'})
        selected=self.diagram.selected
        self.diagram.model=GraphView(self.graph)
        self.diagram.rebuild()
        if selected in self.diagram.model.nodes:self.diagram.select(self.diagram.model.nodes[selected])
        self.set_status('已将 {} 的 {} 个实例更新为{}模型。'.format(new['module'],len(matching),kind_label(new['kind'],self._active_tree_type)))
        return True

    def remove_rule(self):
        selection=self.rules_tree.selection()
        if selection:
            del self.layers[self.active_layer-1]['modules'][int(selection[0])]
            self.refresh_rules()
            self.mark_dirty()

    def set_config(self,config):
        mode=tree_type(config)
        if mode not in TREE_PROFILES:raise ValueError('tree_type must be clock or reset')
        if mode!=self._active_tree_type:
            self._remember_workspace()
            self._active_tree_type=mode
            self._clear_result()
        self._filling=True
        self._base_config=copy.deepcopy(config)
        design=config.get('design',{})
        self.top_var.set(design.get('top',''))
        self.mode_var.set('已有 elab / KDB' if design.get('elab') else 'RTL 文件列表')
        self.path_var.set(design.get('elab',design.get('filelist','')))
        self.layers=copy.deepcopy(config.get('layers',[]))
        if not self.layers:
            self.layers=[{'index':i,'hierarchy':'','modules':[]} for i in (1,2)]
        self.rebuild_layer_buttons()
        self.select_layer(1,save=False)
        self._apply_profile()
        self._filling=False
        self._config_dirty=False
        self.refresh_runs()

    def get_config(self,validate=True):
        self.layers[self.active_layer-1]['hierarchy']=self.hierarchy_var.get().strip()
        data=copy.deepcopy(self._base_config)
        design=data.setdefault('design',{})
        design.pop('elab',None)
        design.pop('filelist',None)
        design.update(top=self.top_var.get().strip())
        design['elab' if self.mode_var.get()=='已有 elab / KDB' else 'filelist']=self.path_var.get().strip()
        data['layers']=copy.deepcopy(self.layers)
        data['tree_type']=self._active_tree_type
        data.pop('timeout_seconds',None)
        return validate_config(data) if validate else data

    def browse_design(self):
        if self.mode_var.get()=='已有 elab / KDB':
            path=filedialog.askdirectory(parent=self,title='选择 kdb.elab++ 目录')
        else:
            path=filedialog.askopenfilename(parent=self,title='选择 RTL 文件列表',filetypes=[('RTL filelist','*.f *.fl *.lst'),('全部文件','*')])
        if path:self.path_var.set(path)

    def edit_environment(self):
        dialog=tk.Toplevel(self)
        dialog.title('提取运行环境')
        dialog.transient(self)
        dialog.configure(bg='white')
        frame=ttk.Frame(dialog,padding=22,style='White.TFrame')
        frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='Verdi 自动定位',style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame,text='从当前环境的 verdi 命令定位安装目录，无需逐次填写。\n提取在运行此 GUI 的机器执行，运行时间不设上限。',style='Muted.TLabel').pack(anchor='w',pady=(8,14))
        detected=tk.StringVar()
        def detect():
            try:
                executable,home=resolve_verdi(self._base_config.get('design',{}))
                detected.set('命令：{}\n安装目录：{}\nNPI：可用'.format(executable,home))
            except ValueError as error:detected.set(str(error))
        ttk.Label(frame,textvariable=detected,style='White.TLabel',wraplength=570).pack(anchor='w')
        ttk.Button(frame,text='重新检测',command=detect).pack(anchor='w',pady=(12,0))
        detect()
        ttk.Button(frame,text='完成',style='Accent.TButton',command=dialog.destroy).pack(anchor='e',pady=(18,0))
        dialog.grab_set()
        return dialog

    def open_device_library(self):
        dialog=tk.Toplevel(self)
        dialog.title('ClockTree7 · 器件模型库')
        dialog.configure(bg='white')
        dialog.transient(self)
        frame=ttk.Frame(dialog,padding=24,style='White.TFrame')
        frame.pack(fill='both',expand=True)
        info=profile(self._base_config)
        ttk.Label(frame,text=info['label']+'器件模型',style='Title.TLabel').pack(anchor='w')
        ttk.Label(frame,text='类型由用户选择；架构图与 SVG 使用相同模型和端口位置。',style='Muted.TLabel').pack(anchor='w',pady=(6,20))
        row=ttk.Frame(frame,style='White.TFrame')
        row.pack(fill='x')
        for index,kind in enumerate(info['kinds']):
            card=ttk.Frame(row,style='White.TFrame',padding=(0,0,12,0))
            card.grid(row=index//2,column=index%2,sticky='nsew',pady=(0,12))
            ttk.Label(card,text=kind_label(kind,self._active_tree_type),style='White.TLabel',font=(self.family,12,'bold')).pack(anchor='w',pady=(0,5))
            ttk.Label(card,text='多路输入 · 单路输出' if kind=='mux' else '单路输入 · 单路输出',style='Muted.TLabel').pack(anchor='w',pady=(0,9))
            canvas=tk.Canvas(card,width=270,height=164,bg='#101b2e',highlightthickness=0)
            canvas.pack()
            is_reset=self._active_tree_type=='reset'
            node={'id':'u_'+self._active_tree_type+'_'+kind,'module':self._active_tree_type+'_'+kind,'kind':kind,
                  'inputs':(['rst0_ni','rst1_ni'] if kind=='mux' else ['rst_ni']) if is_reset else (['clk0_i','clk1_i'] if kind=='mux' else ['clk_i']),
                  'output':'rst_no' if is_reset else 'clk_o'}
            draw_device(canvas,node,14,14,242,node_height(node),1.,self.family)
        ttk.Label(frame,text=('RSY_SYNC 仅展示复位输入/输出；图内同步标记不增加外部时钟连线。' if self._active_tree_type=='reset' else '模型展示关注端口；DIV 的 ÷N 为类型标记，未推断实际分频比。'),style='Muted.TLabel').pack(anchor='w',pady=(18,0))
        ttk.Button(frame,text='关闭',command=dialog.destroy).pack(anchor='e',pady=(18,0))
        dialog.update_idletasks()
        dialog.geometry('+{}+{}'.format(self.winfo_rootx()+max(20,(self.winfo_width()-dialog.winfo_width())//2),
                                       self.winfo_rooty()+max(30,(self.winfo_height()-dialog.winfo_height())//2)))
        return dialog

    def open_config(self,path=None):
        if self.busy:return
        path=path or filedialog.askopenfilename(parent=self,title='导入传播规则配置',initialdir=str(ROOT/'validation/configs'),filetypes=[('JSON','*.json')])
        if not path:return
        try:
            config=load_config(path)
            self.set_config(config)
            self.mark_dirty()
            self.set_status('已导入规则：{}'.format(path))
            self.left_tabs.select(0)
        except Exception as error:self.show_error('配置导入失败',error)

    def save_config(self,path=None):
        try:
            config=self.get_config()
            path=path or filedialog.asksaveasfilename(parent=self,title='保存传播规则配置',defaultextension='.json',initialfile=self._active_tree_type+'-config.json',filetypes=[('JSON','*.json')])
            if path:
                write_json(path,config)
                self.set_status('配置已保存：{}'.format(path))
        except Exception as error:self.show_error('配置检查未通过',error)

    def open_result(self):
        if self.busy:return
        path=filedialog.askopenfilename(parent=self,title='打开时钟树 / 复位树提取结果',initialdir=str(self.runs),filetypes=[('ClockTree7 JSON','*.json')])
        if path:self.load_result(path)

    def _set_busy(self,kind):
        self.busy=kind is not None
        self.job_kind=kind
        self.run_button.configure(state='disabled' if self.busy else 'normal')
        self.cancel_button.configure(state='normal' if kind=='extract' else 'disabled')
        self.analysis_combo.configure(state='disabled' if self.busy else 'readonly')
        if self.busy:
            self.progress.configure(mode='indeterminate')
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate',value=0)

    def load_result(self,path):
        if self.busy:return
        path=Path(path).resolve()
        self._set_busy('load')
        self._badge('正在载入架构图','busy')
        self.set_status('读取提取结果：{}'.format(path))
        def worker():
            try:
                graph=read_json(path)
                if not all(k in graph for k in ['nodes','edges','summary','config']):
                    raise ValueError('请选择 clocktree.json 或 resettree.json 提取结果。')
                mode=tree_type(graph['config'])
                if not isinstance(mode,str) or mode not in TREE_PROFILES or graph.get('tree_type',mode)!=mode:
                    raise ValueError('结果中的 tree_type 必须与配置一致，并为 clock 或 reset。')
                self.events.put(('loaded',graph,path,None))
            except Exception as error:self.events.put(('error','结果打开失败',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def show_graph(self,graph,path=None):
        self.set_config(graph['config'])
        self.graph=graph
        self.graph_file=Path(path) if path else None
        self.project.set('{}  /  {}'.format(graph['config']['design'].get('top','DESIGN'),self.graph_file.parent.name if self.graph_file else '导入结果'))
        for key,value in self.stats.items():value.set('{:,}'.format(graph['summary'].get(key,0)))
        summary=graph['summary']
        if summary['connectivity_complete'] and summary.get('topology_valid',True):self._badge('全量连接已解析','pass')
        elif summary['connectivity_complete']:self._badge('连接完整 · 结构存在异常','warn')
        else:self._badge('存在未解析断点 / 诊断','warn')
        self.component_ids=[None]+[c['id'] for c in graph['components']]
        values=['全部'+profile(graph['config'])['network']]+['网络 {} · {} 实例'.format(c['id']+1,len(c['nodes'])) for c in graph['components']]
        self.component_combo.configure(values=values)
        self.component_combo.current(0)
        self.diagram.load(graph)
        self.match_query,self.match_index='',-1
        self.match_label.configure(text='路径 / module')
        self.selected_object=None
        self.related_edges={}
        self.connections.delete(*self.connections.get_children())
        self.expand_button.configure(state='disabled')
        self.inspector_tabs.select(0)
        self._properties('全网提取概况', [('设计顶层',graph['config']['design'].get('top','')),
            ('覆盖规模','{:,} 个结构 · {:,} 条端口连线'.format(summary['instances'],summary['edges'])),
            ('器件类型',' / '.join('{} {}'.format(kind_label(k,self._active_tree_type),v) for k,v in Counter(n.get('kind') for n in self.diagram.model.original.values()).items())),
            ('分支末端','  /  '.join('L{}: {}'.format(k,v) for k,v in sorted(summary.get('terminal_layers',{}).items()))),
            ('完整性','全部关注输入追驱动，全部关注输出追负载。连接保留双向 NPI 证据。'),
            ('实际运行',graph.get('metadata',{}).get('output_directory','导入结果'))])
        self._replace_text(self.diag_text,json.dumps({'summary':summary,'diagnostics':graph.get('diagnostics',[]),'metadata':graph.get('metadata',{})},ensure_ascii=False,indent=2))
        self.refresh_nodes()
        self.refresh_runs()
        self.set_status('架构图就绪 · {:,} 实例 / {:,} 连接 · 滚轮缩放，拖动画布平移'.format(summary['instances'],summary['edges']))

    def start_extraction(self):
        if self.busy:return
        try:
            config=self.get_config()
            resolve_verdi(config['design'])
            identifier=datetime.datetime.now().strftime(('rst_' if self._active_tree_type=='reset' else 'tk_')+'%Y%m%d_%H%M%S_')+uuid.uuid4().hex[:6]
            directory=self.runs/identifier
            directory.mkdir()
            path=directory/'config.json'
            write_json(path,config)
            result_path=directory/result_filename(config)
        except Exception as error:
            self.show_error('无法开始提取',error)
            return
        self.job_dir=directory
        self._log_mtime=None
        self.cancel_event.clear()
        self._set_busy('extract')
        self._badge('正在解析 elab · 提取全网','busy')
        self.set_status('正在执行 NPI，界面可继续缩放和查看已有架构图。')
        self.show_log(True)
        self._replace_text(self.log_text,'启动真实 Verdi NPI 提取：{}\n'.format(identifier))
        command=[sys.executable,'-m','clocktree','extract','--config',str(path),'--out',str(directory)]
        write_json(directory/'gui-job.json',{'state':'running','command':command,'workspace':str(ROOT)})
        def worker():
            try:
                with (directory/'cli.log').open('w',encoding='utf-8') as stream:
                    if self.cancel_event.is_set():
                        self.events.put(('cancelled',directory))
                        return
                    self.process=subprocess.Popen(command,cwd=str(ROOT),stdout=stream,stderr=subprocess.STDOUT,start_new_session=os.name!='nt')
                    if self.cancel_event.is_set():self._terminate_process()
                    code=self.process.wait()
                self.process=None
                if self.cancel_event.is_set():self.events.put(('cancelled',directory))
                elif code in (0,2) and result_path.is_file():
                    self.events.put(('loaded',read_json(result_path),result_path,directory))
                else:
                    detail=(directory/'cli.log').read_text(encoding='utf-8',errors='replace')[-6000:]
                    self.events.put(('error','提取失败',detail or '请查看运行目录中的 verdi.log。'))
            except Exception as error:
                self.process=None
                self.events.put(('error','提取失败',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def _terminate_process(self):
        process=self.process
        if process is None or process.poll() is not None:return
        try:
            if os.name!='nt':os.killpg(process.pid,signal.SIGTERM)
            else:process.terminate()
            try:process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
                else:process.kill()
        except ProcessLookupError:pass

    def cancel_job(self):
        if self.job_kind!='extract':return
        self.cancel_event.set()
        self.cancel_button.configure(state='disabled')
        self.set_status('正在停止当前提取进程，已有证据保留。')
        threading.Thread(target=self._terminate_process,daemon=True).start()

    def _poll(self):
        if not self.winfo_exists():return
        try:
            while True:
                item=self.events.get_nowait()
                kind=item[0]
                if kind=='loaded':
                    _,graph,path,job=item
                    self._set_busy(None)
                    if job:
                        self.last_job=str(job)
                        write_json(job/'gui-job.json',{'state':'finished','summary':graph['summary']})
                    self.show_graph(graph,path)
                elif kind=='error':
                    was_extract=self.job_kind=='extract'
                    self._set_busy(None)
                    self._badge('操作失败 · 查看日志','warn')
                    self.error_messages.append(item[2])
                    if was_extract and self.job_dir:
                        write_json(self.job_dir/'gui-job.json',{'state':'failed','error':item[2]})
                    if not self._closing:self.show_error(item[1],item[2])
                elif kind=='cancelled':
                    self._set_busy(None)
                    write_json(item[1]/'gui-job.json',{'state':'cancelled','raw_evidence_preserved':True})
                    self._badge('提取已取消','warn')
                    self.set_status('当前任务已取消，运行目录和原始日志已保留。')
                    self.refresh_runs()
        except queue.Empty:pass
        if self.job_kind=='extract' and self.job_dir:
            log=self.job_dir/'verdi.log'
            if log.is_file():
                stamp=(log.stat().st_mtime,log.stat().st_size)
                if stamp!=self._log_mtime:
                    self._log_mtime=stamp
                    with log.open('rb') as stream:
                        stream.seek(max(0,log.stat().st_size-16000))
                        value=stream.read().decode('utf-8','replace')
                    self._replace_text(self.log_text,value)
                    self.log_text.see('end')
                    progress=re.findall(r'CT7_TRACE (\d+)/(\d+)',value)
                    if progress:
                        done,total=map(int,progress[-1])
                        self.progress.stop()
                        self.progress.configure(mode='determinate',value=100*done/max(1,total))
                        self._badge('正在追踪端口 · {:,} / {:,}'.format(done,total),'busy')
        if self._closing and not self.busy:
            self.destroy()
            return
        self.after(120,self._poll)

    def refresh_runs(self):
        self.runs_tree.delete(*self.runs_tree.get_children())
        directories=sorted([p for p in self.runs.iterdir() if p.is_dir()],key=lambda p:p.stat().st_mtime,reverse=True)
        for directory in directories:
            try:
                run_config=read_json(directory/'config.json') if (directory/'config.json').is_file() else {}
                if tree_type(run_config)!=self._active_tree_type:continue
                if (directory/'summary.json').is_file():
                    s=read_json(directory/'summary.json')
                    status='完整' if s['connectivity_complete'] and s.get('topology_valid',True) else '诊断'
                    count=s['instances']
                elif (directory/'gui-job.json').is_file():
                    job=read_json(directory/'gui-job.json')
                    status={'cancelled':'已取消','failed':'失败','running':'运行中'}.get(job.get('state'),'—')
                    count='—'
                else:continue
                self.runs_tree.insert('','end',iid=directory.name,text=directory.name,values=(count,status))
            except (OSError,ValueError,KeyError):continue

    def open_history(self):
        selection=self.runs_tree.selection()
        if not selection:return
        directory=self.runs/selection[0]
        result=directory/result_filename({'tree_type':self._active_tree_type})
        if result.is_file():self.load_result(result)
        else:
            path=directory/'verdi.log'
            if path.is_file():
                self._replace_text(self.log_text,path.read_text(encoding='utf-8',errors='replace')[-16000:])
                self.show_log(True)
            self.set_status('该记录未生成完整结果：{}'.format(directory))

    def refresh_nodes(self):
        self.nav_tree.delete(*self.nav_tree.get_children())
        if not self.graph:return
        query=self.nav_query.get().strip()
        nodes=self.diagram.model.find(query) if query else self.graph['nodes']
        for index,node in enumerate(nodes[:500]):
            self.nav_tree.insert('','end',iid=str(index),text=node['id'],values=('L{}'.format(node['layer']),))
        self._nav_nodes=nodes[:500]
        self.nav_caption.configure(text='显示 {:,} / {:,} 个匹配实例。完整数据保留在架构图；可搜索完整路径定位。'.format(min(500,len(nodes)),len(nodes)))

    def locate_nav(self):
        selection=self.nav_tree.selection()
        if selection:self.diagram.locate(self._nav_nodes[int(selection[0])])

    def locate_query(self,next_match=False):
        if not self.graph:return
        query=self.search_var.get().strip()
        matches=self.diagram.model.find(query)
        if not matches:
            self.match_label.configure(text='没有匹配实例')
            return
        self.match_index=(self.match_index+1)%len(matches) if next_match and query==self.match_query else 0
        self.match_query=query
        self.match_label.configure(text='{} / {} 匹配'.format(self.match_index+1,len(matches)))
        self.diagram.locate(matches[self.match_index])

    def change_mode(self,event=None):
        self.diagram.mode='hierarchy' if self.view_mode.get()=='RTL 层次概览' else 'instances'
        self.diagram.rebuild()

    def change_component(self,event=None):
        index=self.component_combo.current()
        self.diagram.component=self.component_ids[index]
        self.diagram.scope=''
        self.diagram.rebuild()

    def view_changed(self,info):
        self.view_mode.set('RTL 层次概览' if info['mode']=='hierarchy' else '实例明细')
        if hasattr(self,'component_ids'):
            self.component_combo.current(self.component_ids.index(info['component']))
        self.zoom_label.configure(text='{:.0f}%'.format(info['zoom']*100))
        self.view_label.set('{:,} 图元 · 当前 {:,} / 全量 {:,} 实例'.format(info['boxes'],info['actual'],info['total']))
        scope=info['scope']
        self.scope_label.set(('局部范围 / '+scope[-95:]) if scope else '全部层次 / 全量关注结构')

    def _properties(self,title,fields):
        text=self.prop_text
        text.configure(state='normal')
        text.delete('1.0','end')
        text.insert('end',title+'\n','heading')
        for label,value in fields:
            text.insert('end',label+'\n','label')
            text.insert('end',str(value)+'\n','value')
        text.configure(state='disabled')
        text.yview_moveto(0)

    def inspect_node(self,node):
        self.selected_object=node
        self.expand_button.configure(state='normal' if node.get('aggregate') else 'disabled')
        if node.get('aggregate'):
            fields=[('RTL 范围',node['path']),('显示类型','层次聚合（不是新增的 RTL 实例）'),
                    ('归属层','L{}'.format(node['layer'])),('实际实例数',len(node['members'])),
                    ('Module 分布','\n'.join('{} × {}'.format(k,v) for k,v in node['module_counts'].items())),
                    ('器件类型分布',' / '.join('{} {}'.format(kind_label(k,self._active_tree_type),v) for k,v in node.get('kind_counts',{}).items())),
                    ('内部真实连线',node['internal_edges']),('根 / 末端','{} / {}'.format(node['root_count'],node['terminal_count']))]
            title='RTL 层次概览'
            members=set(node['members'])
            edges=[e for e in self.graph['edges'] if (e['source'] in members)!=(e['target'] in members)]
        else:
            boundaries=[b for b in self.graph.get('boundaries',[]) if b['instance']==node['id']]
            fields=[('完整实例路径',node['id']),('Module 定义',node['module']),('器件类型',kind_label(node.get('kind'),self._active_tree_type)),('传播层次','L{}'.format(node['layer'])),
                    ('关注输入','\n'.join(node['inputs'])),('关注输出',node['output']),
                    ('结构状态',' / '.join(x for value,x in [(node.get('root'),'根结构'),(node.get('terminal'),'分支末端'),(node.get('has_unresolved_output'),'存在未解析输出')] if value) or '中间结构'),
                    ('范围边界','{} 项，原始证据保留在 raw.jsonl'.format(len(boundaries))),('RTL 来源',node.get('source','—'))]
            title=node['module']
            edges=self.diagram.model.incoming[node['id']]+self.diagram.model.outgoing[node['id']]
            members={node['id']}
        self._properties(title,fields)
        self._fill_connections(edges,members)
        self.inspector_tabs.select(0)

    def _fill_connections(self,edges,members=None):
        self.connections.delete(*self.connections.get_children())
        self.related_edges={}
        unique={e['id']:e for e in edges}
        for i,edge in enumerate(unique.values()):
            upstream=members and edge['target'] in members
            other=edge['source'] if upstream else edge['target']
            label='.'.join(other.split('.')[-2:])
            key=str(i)
            self.related_edges[key]=edge
            self.connections.insert('','end',iid=key,text=label,values=('上游' if upstream else '下游',edge['source_pin'] if upstream else edge['target_pin']))
        self.connection_title.configure(text='相关端口连接 · {:,} 条'.format(len(unique)))

    def inspect_edges(self,display_edges):
        edges={}
        for edge in display_edges:
            for item in self.diagram.model.actual_edges(edge):edges[item['id']]=item
        if not edges:return
        self.selected_object=None
        self.expand_button.configure(state='disabled')
        self._fill_connections(list(edges.values()))
        self._properties('实际端口连接', [('共用当前线段','{} 条真实端口连接'.format(len(edges))),
                                        ('查看依据','在下面列表选择具体连线，或在“连线证据”页查看第一条。'),
                                        ('数据来源','驱动 / 负载双向 NPI 静态网络追踪')])
        self._edge_evidence(next(iter(edges.values())))
        self.inspector_tabs.select(1)

    def _edge_evidence(self,edge):
        lines=['实际端口连接','═'*24,'来源实例',edge['source'],'输出口  '+edge['source_pin'],'',
               '目标实例',edge['target'],'输入口  '+edge['target_pin'],'','层差  {}'.format(edge['layer_delta'])]
        for direction,label in [('load','输出 → 全部负载'),('driver','输入 ← 实际驱动')]:
            lines+=['','─'*24,label,'']
            evidence=edge.get('evidence',{}).get(direction,[])
            if not evidence:lines.append('此方向没有证据，请查看诊断。')
            for i,step in enumerate(evidence,1):
                lines+=['{}. {}'.format(i,step.get('type','')),step.get('name','')]
                if step.get('net'):lines.append('NET  '+step['net'])
                lines.append('')
        self._replace_text(self.evidence_text,'\n'.join(lines))
        self._inspected_edge=edge

    def inspect_related(self,event=None):
        selection=self.connections.selection()
        if selection and selection[0] in self.related_edges:
            self._edge_evidence(self.related_edges[selection[0]])
            self.inspector_tabs.select(1)

    def locate_related(self,event=None):
        selection=self.connections.selection()
        if not selection or selection[0] not in self.related_edges:return
        edge=self.related_edges[selection[0]]
        selected=self.selected_object
        target=edge['source'] if selected and edge['target']==selected.get('id') else edge['target']
        self.diagram.locate(self.diagram.model.original[target])

    def expand_selected(self):
        if self.selected_object:self.diagram.expand(self.selected_object)

    def highlight_path(self,direction):
        count=self.diagram.trace(direction)
        self.set_status('{}已高亮 {:,} 条真实连接。'.format('上游' if direction=='upstream' else '下游',count))

    def _replace_text(self,text,value):
        text.configure(state='normal')
        text.delete('1.0','end')
        text.insert('end',value)
        text.configure(state='disabled')

    def show_log(self,visible):
        self.log_visible=visible
        if visible:self.log_frame.pack(fill='x')
        else:self.log_frame.pack_forget()
        self.log_toggle.configure(text='任务日志与诊断  '+('▾' if visible else '▴'))

    def toggle_log(self):
        self.show_log(not self.log_visible)

    def export_menu(self):
        menu=tk.Menu(self,tearoff=False)
        menu.add_command(label='保存传播规则配置 JSON',command=self.save_config)
        menu.add_command(label='导出全部结构与连线 JSON',command=self.export_result)
        menu.add_command(label='导出当前范围架构图 SVG',command=self.export_svg)
        menu.tk_popup(self.winfo_pointerx(),self.winfo_pointery())

    def export_result(self,path=None):
        if not self.graph:return
        path=path or filedialog.asksaveasfilename(parent=self,title='导出全量结构图',defaultextension='.json',initialfile=self._active_tree_type+'-full.json',filetypes=[('JSON','*.json')])
        if path:
            try:
                write_json(path,self.graph)
                self.set_status('已导出全部 {:,} 实例与 {:,} 连线：{}'.format(len(self.graph['nodes']),len(self.graph['edges']),path))
            except OSError as error:self.show_error('导出失败',error)

    def export_svg(self,path=None):
        if not self.graph:return
        path=path or filedialog.asksaveasfilename(parent=self,title='导出当前范围架构图',defaultextension='.svg',initialfile=self._active_tree_type+'-architecture.svg',filetypes=[('SVG','*.svg')])
        if path:
            try:
                Path(path).write_text(self.diagram.model.svg(),encoding='utf-8')
                self.set_status('当前范围的全部图元已导出 SVG：{}'.format(path))
            except OSError as error:self.show_error('导出失败',error)

    def show_error(self,title,error):
        self.set_status('{}：{}'.format(title,str(error)[-220:]))
        messagebox.showerror(title,str(error),parent=self)

    def close(self):
        if self.job_kind=='extract':
            self._closing=True
            self.cancel_job()
        elif self.busy:
            self._closing=True
        else:self.destroy()


def run_gui(runs=None,result=None,config=None):
    app=ClockTreeApp(runs=runs,result=result,config=config)
    app.mainloop()
