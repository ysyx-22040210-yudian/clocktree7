"""ClockTree7 native Tk/ttk desktop application (compatible with Tk 8.5)."""
import copy
import datetime
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from itertools import islice
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font as tkfont

from .config import configured_input_boundaries, div_source_scopes, extraction_settings, load_config, validate_config, EXTRACTION_MODES
from .paths import default_runs_dir
from .paths import user_state_root
from .environment import resolve_verdi
from .tkdiagram import ClockDiagram, draw_device
from .devices import DIV_SOURCE_STYLES, LABEL_TO_KIND, device_style, kind_label, type_labels, node_height, matching_rule, connection_details
from .div_sources import read_div_source_levels
from .l1_inputs import INPUT_SOURCE_KINDS, source_title, source_basis
from .register_results import latest_register_result
from .ui_state import UIState, design_key
from .auxiliary import AUX_ROLES, DIRECTIONS, validate_aux_ports
from .input_ports import port_names
from .devices import auxiliary_details, wire_details
from .viewmodel import GraphView
from .profiles import (GATE_KINDS, SINGLE_INPUT_KINDS, TREE_PROFILES, LABEL_TO_TREE,
                       tree_type, profile, rule_ports, empty_config, result_filename, output_ports, selectable_kinds)
from .session import SessionStore, SessionWriter, read_graph, same_config, snapshot_graph
from .storage import IndexedGraph, CombinedSequence, RecordSequence, write_graph
from .indexed_view import IndexedGraphView
from .runtime import RunJournal, read_json as runtime_json, diagnose, diagnose_gui, tail_text, stage_node, bind_gui, alive, LABELS
from .stageview import StageView
from .recovery import inspect_recovery
from .recoveryview import RecoveryDialog
from .ui_layout import ACCENT as BLUE, BAR, BOARD, INK, LINE, MUTED, SURFACE, dialog_heading, fit_window, panel_heading, scroll_body, style_desktop, wrapped_label
from .desktop_art import artwork, draw_grid, glyph


ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def write_json(path, data):
    write_graph(path, data)


class RuleDialog(tk.Toplevel):
    def __init__(self, app, layer, rule, callback):
        super().__init__(app)
        self.app, self.callback = app, callback
        self.layer_index=layer
        self.hierarchy=app.get_config(validate=False)['layers'][layer-1]['hierarchy']
        self.port_regex = {direction: rule.get(direction+'_regex', '.*') for direction in ('input', 'output')}
        self.aux_regex = {}
        self.discovered_module = None
        self.discovered_rows = None
        self.tree_type = app._active_tree_type
        self.info = TREE_PROFILES[self.tree_type]
        self.input_key, self.output_key = self.info['inputs'], self.info['output']
        self.title('L{} · 模块与关注端口'.format(layer))
        self.configure(bg='white')
        self.transient(app)
        fit_window(self, app, (860, 560), (680, 430))
        dialog_heading(self,'模块规则','chip','L{}  /  {}'.format(layer,self.hierarchy or '设计顶层'))
        actions = ttk.Frame(self, padding=(12,8))
        actions.pack(side='bottom',fill='x')
        body = ttk.Frame(self, style='White.TFrame')
        body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=3,uniform='rule')
        body.columnconfigure(1,weight=2,uniform='rule')
        body.rowconfigure(0,weight=1)
        form = ttk.Frame(body,style='White.TFrame')
        form.grid(row=0,column=0,sticky='nsew')
        frame = scroll_body(form, (14,6))
        side = ttk.Frame(body,padding=(12,8),style='White.TFrame')
        side.grid(row=0,column=1,sticky='nsew')
        ttk.Label(frame,text='器件类型',style='Small.TLabel').pack(anchor='w',pady=(0,4))
        self.kind_var=tk.StringVar(value=kind_label(rule.get('kind'),self.tree_type) if rule.get('kind') in self.info['kinds'] else '请选择器件类型')
        self.kind_combo=ttk.Combobox(frame,textvariable=self.kind_var,
                                    values=type_labels({'tree_type':self.tree_type}, rule.get('kind')),state='readonly')
        self.kind_combo.pack(fill='x')
        self.type_hint=tk.StringVar()
        self.fields = {}
        self.entries={}
        self.port_buttons = {}
        for key, label, value in [('module','Module 定义名',rule.get('module','')),
                                  (self.input_key,'关注的{}输入口（多个以逗号分隔）'.format(self.info['signal']),', '.join(rule.get(self.input_key,[]))),
                                  (self.output_key,'关注的{}输出口（穿线可多个，以逗号分隔）'.format(self.info['signal']),', '.join(rule.get(self.output_key+'s',[])) if rule.get('kind')=='wire' else rule.get(self.output_key,''))]:
            ttk.Label(frame, text=label, style='Small.TLabel').pack(anchor='w', pady=(7,4))
            variable = tk.StringVar(value=value)
            row = ttk.Frame(frame, style='White.TFrame')
            row.pack(fill='x')
            entry = ttk.Entry(row, textvariable=variable, width=40)
            entry.pack(side='left', fill='x', expand=True, ipady=3)
            self.fields[key] = variable
            self.entries[key]=entry
            if key=='module':
                self.first = entry
            else:
                direction = 'input' if key == self.input_key else 'output'
                button = ttk.Button(row, text='正则添加', command=lambda d=direction:self.edit_ports(d))
                button.pack(side='right', padx=(8, 0))
                self.port_buttons[direction] = button
        self.aux_frame=ttk.Frame(frame,style='White.TFrame')
        self.aux_frame.pack(fill='x',pady=(6,0))
        self.aux_hint=ttk.Label(self.aux_frame,text='辅助端口（选填），用于显示绑定与追踪寄存器。',style='Muted.TLabel')
        self.aux_fields={};self.aux_rows={}
        for role,direction in (('pll_sel','input'),('div','input'),('sw_done','output'),('gt','input')):
            row=ttk.Frame(self.aux_frame,style='White.TFrame')
            ttk.Label(row,text='{} {}端口名'.format(role,DIRECTIONS[direction]),style='Small.TLabel',width=22).pack(side='left')
            variable=tk.StringVar(value=rule.get('aux_ports',{}).get(role,''))
            ttk.Entry(row,textvariable=variable,width=21).pack(side='left',fill='x',expand=True,ipady=2)
            ttk.Button(row, text='正则添加', command=lambda d=direction,r=role:self.edit_ports(d,r)).pack(side='right',padx=(8,0))
            self.aux_rows[role]=row;self.aux_fields[role]=variable
        self.selection_status=tk.StringVar(value='直接编辑端口名，或从 elab 正则筛选后加入。')
        ttk.Label(frame,textvariable=self.selection_status,style='Muted.TLabel',wraplength=440).pack(anchor='w',pady=(8,0))
        ttk.Label(side,text='器件预览',style='Section.TLabel').pack(anchor='w',pady=(0,8))
        self.preview=tk.Canvas(side,width=1,height=256,bg=BOARD,highlightthickness=0)
        self.preview.pack(fill='x')
        hint=ttk.Label(side,textvariable=self.type_hint,style='Muted.TLabel',wraplength=245,justify='left')
        hint.pack(anchor='w',pady=8)
        side.bind('<Configure>',lambda e:hint.configure(wraplength=max(100,e.width-28)))
        self.preview_fonts={}
        self.error = tk.StringVar()
        ttk.Button(actions, text='取消', command=self.destroy).pack(side='right')
        self.save_button=ttk.Button(actions,text='保存规则',image=glyph(self,'save','light'),compound='left',style='Accent.TButton',command=self.save)
        self.save_button.pack(side='right', padx=8)
        ttk.Label(actions, textvariable=self.error, foreground='#a43232', wraplength=430).pack(side='left',fill='x',expand=True)
        self.kind_var.trace('w',self.update_preview)
        for variable in self.fields.values():variable.trace('w',self.update_preview)
        for variable in self.aux_fields.values():variable.trace('w',self.update_preview)
        self.fields['module'].trace('w',self.invalidate_ports)
        self.preview.bind('<Configure>',self.draw_preview)
        self.update_preview()
        self.bind('<Return>', lambda e:self.save())
        self.bind('<Escape>', lambda e:self.destroy())
        self.grab_set()
        self.first.focus_set()

    def save(self):
        kind=LABEL_TO_KIND.get(self.kind_var.get())
        if kind not in self.info['kinds']:
            self.error.set('请选择“{}”中的一种器件类型。'.format(' / '.join(type_labels({'tree_type':self.tree_type}))))
            return
        inputs = port_names(self.fields[self.input_key].get())
        outputs = [] if kind == 'subsystem' else port_names(self.fields[self.output_key].get())
        rule = {'module':self.fields['module'].get().strip(), self.input_key:inputs, 'kind':kind}
        if not rule['module'] or not inputs or (kind != 'subsystem' and not outputs):
            self.error.set('请填写 module 名、输入口和输出口；穿线可填写多个输出。')
            return
        if kind not in ('subsystem', 'wire') and len(outputs) != 1:
            self.error.set('此器件需要一个关注输出口。');return
        if len(set(inputs))!=len(inputs) or len(set(outputs))!=len(outputs) or set(outputs).intersection(inputs):
            self.error.set('端口名不能重复，输入和输出不能同名。')
            return
        if kind in SINGLE_INPUT_KINDS and len(inputs) != 1:
            self.error.set('{} 需要一个关注输入口。'.format(kind_label(kind,self.tree_type)))
            return
        if kind == 'wire':rule[self.output_key+'s'] = outputs
        elif outputs:rule[self.output_key] = outputs[0]
        for direction, expression in self.port_regex.items():
            if expression != '.*' and (direction == 'input' or outputs):
                rule[direction+'_regex'] = expression
        _, output = rule_ports(rule, self.tree_type)
        aux={role:self.aux_fields[role].get().strip() for role,_ in AUX_ROLES.get(kind,()) if self.aux_fields[role].get().strip()}
        if aux:rule['aux_ports']=aux
        try:validate_aux_ports(rule,inputs,output)
        except ValueError as error:self.error.set(str(error));return
        self.callback(rule)
        self.destroy()

    def edit_inputs(self):
        return self.edit_ports('input')

    def edit_ports(self, direction, role=None):
        from .input_view import InputPortDialog
        module=self.fields['module'].get().strip()
        if not module:self.error.set('请先填写 module 名。');return
        kind = LABEL_TO_KIND.get(self.kind_var.get())
        if kind not in self.info['kinds'] or (direction == 'output' and kind == 'subsystem'):
            return
        if role:
            if (role, direction) not in AUX_ROLES.get(kind, ()):
                return
            single = True
            field, expressions, regex_key = self.aux_fields[role], self.aux_regex, role
        else:
            single = kind in SINGLE_INPUT_KINDS if direction == 'input' else kind != 'wire'
            field = self.fields[self.input_key if direction == 'input' else self.output_key]
            expressions, regex_key = self.port_regex, direction
        previous = port_names(field.get())

        def confirmed(names, expression, rows):
            expressions[regex_key] = expression
            merged = names if single else list(dict.fromkeys(previous + names))
            field.set(', '.join(merged))
            self.selection_status.set('已写入{}端口名，可在文本框继续编辑。'.format(DIRECTIONS[direction]))
            self.error.set('')

        def cache(rows):
            self.discovered_module = (module, self.hierarchy, directions)
            self.discovered_rows = rows

        directions = ['input'] if kind == 'subsystem' else ['input', 'output']
        cached = self.discovered_rows if self.discovered_module == (module, self.hierarchy, directions) else None
        return InputPortDialog(self, self.app.get_config(validate=False), module, self.hierarchy,
                               expressions.get(regex_key, '.*'), previous, confirmed, direction=direction,
                               read_directions=directions, cached_rows=cached, cache_callback=cache, single=single)

    def invalidate_ports(self,*args):
        self.discovered_module = None
        self.discovered_rows = None
        self.selection_status.set('请核对文本框中的端口名与当前 module。')

    def update_preview(self,*args):
        kind=LABEL_TO_KIND.get(self.kind_var.get())
        roles=dict(AUX_ROLES.get(kind,()))
        self.aux_hint.pack_forget()
        for row in self.aux_rows.values():row.pack_forget()
        if roles:
            self.aux_hint.pack(anchor='w',pady=(2,4))
            for role,_ in AUX_ROLES[kind]:self.aux_rows[role].pack(fill='x',pady=3)
        subsystem=kind=='subsystem'
        self.port_buttons['input'].configure(state='normal' if kind in self.info['kinds'] else 'disabled')
        self.port_buttons['output'].configure(state='normal' if kind in self.info['kinds'] and not subsystem else 'disabled')
        self.entries[self.output_key].configure(state='disabled' if subsystem else 'normal')
        if subsystem:self.type_hint.set('子系统只有输入终点。直接父层：'+(self.hierarchy or '设计顶层'))
        elif kind=='wire':self.type_hint.set('穿线支持多个输入和输出。内部逐位对应关系由 elab 证明；未知连接不补画。')
        elif kind=='mux':self.type_hint.set('选择器可填写多个输入和一个输出。输入顺序用于 SV 模板。')
        else:self.type_hint.set('填写一个关注输入和一个关注输出。')
        self.draw_preview()

    def draw_preview(self,event=None):
        c=self.preview
        c.delete('all')
        w,h=c.winfo_width(),c.winfo_height()
        if min(w,h)<2:return
        draw_grid(c,w,h)
        kind=LABEL_TO_KIND.get(self.kind_var.get())
        subsystem=kind=='subsystem'
        if kind not in self.info['kinds']:
            c.create_text(w/2,h/2,text='选择类型后预览器件',fill='#b8ccdb',font=(self.app.family,10))
            return
        pins=port_names(self.fields[self.input_key].get())
        pins=pins or (['输入 1','输入 2'] if kind=='mux' else ['输入'])
        node={'id':'u_instance','module':self.fields['module'].get().strip() or 'module_name',
              'kind':kind,'inputs':pins,'output':None if subsystem else self.fields[self.output_key].get().strip() or '输出','preview':True}
        if kind=='wire':
            node.update(output=None,outputs=port_names(self.fields[self.output_key].get()) or ['输出 1','输出 2'])
        node['aux_ports']={role:self.aux_fields[role].get().strip() for role,_ in AUX_ROLES.get(kind,()) if self.aux_fields[role].get().strip()}
        height=node_height(node)
        scale=min(1.,(w-16)/242,(h-16)/height)
        width=min(340,(w-16)/scale)
        draw_device(c,node,(w-width*scale)/2,(h-height*scale)/2,width,height,scale,self.app.family,font_cache=self.preview_fonts)


class ClockTreeApp(tk.Tk):
    def __init__(self, runs=None, result=None, config=None, remember=True, session_dir=None):
        gui_base=Path(session_dir) if session_dir else user_state_root()/'gui-logs'
        self.gui_journal=RunJournal(gui_base/('ui_'+uuid.uuid4().hex),prefix='gui')
        latest=gui_base/'latest.json'
        old=runtime_json(latest)
        self._previous_gui_issue=diagnose_gui(old['journal_directory']) if old and old.get('journal_directory') else None
        if self._previous_gui_issue and self._previous_gui_issue.get('state') not in ('interrupted','failed'):
            self._previous_gui_issue=None
        self.recovery_dialog=None
        self._recovery_result=None
        if old:write_json(gui_base/'previous.json',old)
        write_json(latest,self.gui_journal.paths())
        self.gui_journal.emit('begin','display','初始化 Tk 界面')
        try:super().__init__(className='ClockTree7')
        except BaseException as error:
            self.gui_journal.fail(error)
            raise
        self.title('ClockTree7  |  RTL 时钟树 / 复位树架构分析')
        self.iconphoto(True,artwork(self).photo('brand'))
        self.configure(bg=SURFACE)
        fit_window(self, None, (1760, 980), (940, 600))
        families = set(tkfont.families(self))
        self.family = next((x for x in ['Microsoft YaHei UI','Microsoft YaHei','Noto Sans CJK SC','WenQuanYi Micro Hei','DejaVu Sans'] if x in families),'sans-serif')
        self.latin = next((x for x in ['Inter','Segoe UI','DejaVu Sans'] if x in families),self.family)
        self.mono = 'Consolas' if 'Consolas' in families else 'DejaVu Sans Mono'
        style_desktop(self)
        self.runs = Path(runs or default_runs_dir()).resolve()
        self.runs.mkdir(parents=True, exist_ok=True)
        self.events = queue.Queue()
        self._session_store=SessionStore(self.runs,session_dir) if remember else None
        self.ui_state=UIState((Path(session_dir) if session_dir else user_state_root())/'ui-preferences.json')
        self._div_color_after = None
        self._div_color_request = None
        self._div_color_context = None
        self._session_writer=SessionWriter(self._session_store,lambda error:self.events.put(('session_save_error',error))) if remember else None
        self._session_ready=False
        self._session_restoring=False
        self._session_after=None
        self._session_prepared={}
        self._close_save_started=False
        self._destroying=False
        self._graph_token=None
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
        self._runtime_directory=None
        self._runtime_state={}
        self._runtime_stamp=None
        self._log_filter=None
        self._log_token=0
        self._log_diagnosis=False
        self._journal_rendering=False
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
        self._compact = None
        self._compact_side = 'left'
        self._side_widths = {'left': 280, 'right': 280}
        self._pane_job = None
        self.status = tk.StringVar(value='就绪 · 默认两级，可添加层级或导入已有配置')
        self.state = tk.StringVar(value='等待设计')
        self.project = tk.StringVar(value='未打开设计')
        self.view_label = tk.StringVar(value='全部时钟网络')
        self.scope_label = tk.StringVar(value='全部层次 / 全量关注结构')
        self.analysis_var = tk.StringVar(value='时钟树')
        self.diagram_name = tk.StringVar(value='时钟树架构图')
        self._build()
        self.diagram.runtime_journal=self.gui_journal
        self.gui_journal.emit('end','display','Tk 工作台已初始化')
        self.gui_journal.emit('finished','display','等待设计或历史恢复')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Control-o>', lambda e:self.open_config())
        self.bind('<Control-s>', lambda e:self.save_config())
        self.bind('<Control-l>', lambda e:self.open_result())
        self.bind('<Control-plus>', lambda e:self.diagram.zoom(1.2))
        self.bind('<Control-minus>', lambda e:self.diagram.zoom(1/1.2))
        self.bind('<F5>', lambda e:self.diagram.fit())
        self.bind('<Control-f>', self.focus_search)
        self.bind('<F11>', self.toggle_focus)
        self.bind('<Escape>', lambda e:self.toggle_focus() if self.focused_view else None)
        self.after(100, self._poll)
        self.set_config({'design':{},'layers':self.layers})
        self.refresh_runs()
        if remember:
            self._set_busy('restore')
            self._badge('正在恢复上次工作区','busy')
            self.set_status('正在恢复历史配置与架构图…')
            self.after(180,lambda:self._load_previous_session(config,result))
        elif config:
            self.after(180,lambda:self.open_config(config))
        elif result:
            self.after(180,lambda:self.load_result(Path(result)))

    def _build(self):
        header = tk.Frame(self,bg=BAR,padx=14,pady=7)
        header.pack(fill='x')
        header.columnconfigure(3, weight=1)
        tk.Label(header,image=artwork(self).photo('brand'),bg=BAR,bd=0).grid(row=0,column=0,padx=(0,9))
        tk.Label(header,text='ClockTree7',font=(self.latin,16,'bold'),bg=BAR,fg='#f3f6ff').grid(row=0,column=1,padx=(0,20))
        self.analysis_combo = ttk.Combobox(header, textvariable=self.analysis_var, values=[p['label'] for p in TREE_PROFILES.values()],
                                           width=7,state='readonly',style='Header.TCombobox')
        self.analysis_combo.grid(row=0, column=2)
        self.analysis_combo.bind('<<ComboboxSelected>>', self.switch_tree_type)
        project=tk.Frame(header,bg=BAR)
        project.grid(row=0,column=3,sticky='ew',padx=14)
        tk.Frame(project,bg='#43516a',width=1,height=20).pack(side='left',padx=(0,12))
        tk.Label(project,textvariable=self.project,width=1,anchor='w',bg=BAR,fg='#ccd7ec',font=(self.family,10)).pack(side='left',fill='x',expand=True)
        self.state_badge = ttk.Label(header,textvariable=self.state,style='idle.Status.TLabel')
        self.state_badge.grid(row=0, column=4, padx=(0, 13))
        file_button = tk.Menubutton(header, text='文件 ▾', bg=BAR, fg='white', activebackground='#294855',
                                    activeforeground='white', padx=9, pady=3, takefocus=True)
        file_button.grid(row=0, column=5, padx=(0, 8))
        self.file_menu = tk.Menu(file_button, tearoff=False)
        file_button.configure(menu=self.file_menu)
        for label, command, shortcut in [('新建配置', self.new_config, ''), ('导入配置…', self.open_config, 'Ctrl+O'),
                                          ('保存配置…', self.save_config, 'Ctrl+S'), ('打开结果…', self.open_result, 'Ctrl+L'),
                                          ('查看示例', self.open_example, '')]:
            self.file_menu.add_command(label=label, command=command, accelerator=shortcut)
        self.file_menu.add_separator()
        self.file_menu.add_command(label='导出全量 JSON…', command=self.export_result)
        self.file_menu.add_command(label='导出当前视图 SVG…', command=self.export_svg)
        self.open_result_button=ttk.Button(header,text='打开结果',image=glyph(header,'folder','light'),compound='left',command=self.open_result,style='Header.TButton')
        self.open_result_button.grid(row=0, column=6)

        bottom = tk.Frame(self,bg='#eaf0f7',padx=12,pady=2)
        bottom.pack(side='bottom', fill='x')
        tk.Label(bottom, textvariable=self.status, width=1, fg='#465c77', bg='#eaf0f7', font=(self.family, 9),
                 anchor='w').pack(side='left', expand=True, fill='x')
        self.panes = ttk.Panedwindow(self, orient='horizontal')
        self.panes.pack(fill='both', expand=True)
        self.left = ttk.Frame(self.panes, width=280, style='White.TFrame')
        self.center = ttk.Frame(self.panes, style='White.TFrame')
        self.right = ttk.Frame(self.panes, width=280, style='White.TFrame')
        self.panes.add(self.left, weight=0)
        self.panes.add(self.center, weight=1)
        self.panes.add(self.right, weight=0)
        self._build_left()
        self._build_center()
        self._build_inspector()
        self._analysis_state()
        self._fill_connections([],None)
        self.panes.bind('<Configure>', self._resize_workspace)
        self.panes.bind('<ButtonRelease-1>', self._remember_sashes, add='+')

    def _build_left(self):
        panel_heading(self.left,'设计面板','sliders')
        self.left_tabs=ttk.Notebook(self.left)
        self.left_tabs.pack(fill='both',expand=True)
        settings_outer=ttk.Frame(self.left_tabs,style='White.TFrame')
        footer=ttk.Frame(settings_outer,padding=(15,8,15,12),style='White.TFrame')
        footer.pack(side='bottom',fill='x')
        scroll_area=ttk.Frame(settings_outer,style='White.TFrame')
        scroll_area.pack(fill='both',expand=True)
        self.settings_canvas=tk.Canvas(scroll_area,bg='white',highlightthickness=0,width=238)
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
        self.trace_scope_var=tk.StringVar()
        self.extraction_mode_var=tk.StringVar(value=EXTRACTION_MODES['target'])
        self.memory_limit_var=tk.StringVar(value='60')
        self.port_timeout_var=tk.StringVar(value='0')
        self.div_source_vars = [tk.StringVar(), tk.StringVar()]
        self.memory_status=tk.StringVar(value='等待提取 · RSS 监测在 Linux 上启用')
        self.hierarchy_var=tk.StringVar()
        self.layer_input_boundary_var=tk.BooleanVar()
        self.layer_reset_var=tk.StringVar()
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
        ttk.Label(settings,text='端口追踪根层次',style='Small.TLabel').pack(anchor='w',pady=(12,0))
        self.trace_scope_entry=ttk.Entry(settings,textvariable=self.trace_scope_var)
        self.trace_scope_entry.pack(fill='x',pady=(5,4))
        ttk.Label(settings,text='留空时从设计顶层追踪。',style='Muted.TLabel').pack(anchor='w')
        self.options_open = False
        self.options_button=ttk.Button(settings,text='▸ 提取选项',image=glyph(settings,'sliders'),compound='left',command=self.toggle_options)
        self.options_button.pack(fill='x',pady=(8,1))
        self.options_frame = advanced = ttk.Frame(settings,style='White.TFrame')
        ttk.Label(advanced,text='连接提取方式',style='Small.TLabel').pack(anchor='w',pady=(7,0))
        self.extraction_mode_box=ttk.Combobox(advanced,textvariable=self.extraction_mode_var,
            values=[EXTRACTION_MODES[k] for k in ('target','full')],state='readonly')
        self.extraction_mode_box.pack(fill='x',pady=(5,4))
        ttk.Label(advanced,text='目标结构反查关注输入。全负载模式另行核验输出负载。',style='Muted.TLabel',wraplength=218).pack(anchor='w')
        memory_row=ttk.Frame(advanced,style='White.TFrame')
        memory_row.pack(fill='x',pady=(12,4))
        ttk.Label(memory_row,text='内存重启阈值（GiB）',style='Small.TLabel').pack(side='left')
        self.memory_limit_entry=ttk.Entry(memory_row,textvariable=self.memory_limit_var,width=7)
        self.memory_limit_entry.pack(side='right')
        ttk.Label(advanced,text='接近阈值时提交断点并重启 Verdi。填 0 关闭监测。',style='Muted.TLabel',wraplength=218).pack(anchor='w')
        ttk.Label(advanced,textvariable=self.memory_status,style='Muted.TLabel',wraplength=218).pack(anchor='w',pady=(4,0))
        timeout_row=ttk.Frame(advanced,style='White.TFrame')
        timeout_row.pack(fill='x',pady=(12,4))
        ttk.Label(timeout_row,text='端口 trace 超时（秒）',style='Small.TLabel').pack(side='left')
        self.port_timeout_entry=ttk.Entry(timeout_row,textvariable=self.port_timeout_var,width=7)
        self.port_timeout_entry.pack(side='right')
        ttk.Label(advanced,text='0 表示不限时。超时后跳过当前实例的剩余端口。',style='Muted.TLabel',wraplength=218).pack(anchor='w')
        self.div_sources_frame = ttk.Frame(advanced, style='White.TFrame')
        self.div_sources_frame.pack(fill='x', pady=(12, 0))
        ttk.Label(self.div_sources_frame, text='DIV 寄存器来源', style='Small.TLabel').pack(anchor='w')
        for index, variable in enumerate(self.div_source_vars, 1):
            ttk.Label(self.div_sources_frame, text='来源层次 {} · RTL 路径'.format(index), style='Muted.TLabel').pack(anchor='w', pady=(5, 0))
            ttk.Entry(self.div_sources_frame, textvariable=variable).pack(fill='x', pady=(3, 0))
        wrapped_label(self.div_sources_frame, text='两条路径成对填写，用于归类 div 端口的源寄存器。',
                      style='Muted.TLabel').pack(fill='x', pady=(5, 0))
        self.rules_separator = ttk.Separator(settings)
        self.rules_separator.pack(fill='x',pady=8)
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
        self.layer_title=ttk.Label(settings,text='L1 · RTL 父层路径',style='Small.TLabel')
        self.layer_title.pack(anchor='w')
        ttk.Entry(settings,textvariable=self.hierarchy_var).pack(fill='x',pady=(5,10))
        ttk.Label(settings,text='填写父层路径，仅查本层直接例化\n不递归查找子层次中的 module',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        self.layer_input_boundary_check = ttk.Checkbutton(
            settings, text='将本层输入口和 wire 设为边界',
            variable=self.layer_input_boundary_var, command=self.change_input_boundary)
        self.layer_input_boundary_check.pack(anchor='w')
        wrapped_label(settings,text='输入优先连接已选器件的输出。未找到时，使用先遇到的边界。已勾选的层标有 *。',
                      style='Muted.TLabel').pack(fill='x',pady=(3,10))
        ttk.Label(settings,text='该层复位信号 · $var_rst',style='Small.TLabel').pack(anchor='w')
        self.layer_reset_entry=ttk.Entry(settings,textvariable=self.layer_reset_var)
        self.layer_reset_entry.pack(fill='x',pady=(5,4))
        ttk.Label(settings,text='信号名或完整 RTL 路径；短名相对该父层\nSV 每个分支取最初器件所属层的复位',style='Muted.TLabel').pack(anchor='w',pady=(0,10))
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
        self.layer_hint = ttk.Label(settings, style='Muted.TLabel', justify='left')
        self.layer_hint.pack(anchor='w',pady=(3,4))
        self.run_button=ttk.Button(footer,text='开始提取全部时钟树',image=glyph(footer,'play','light'),compound='left',style='Accent.TButton',command=self.start_extraction)
        footer.columnconfigure(0,weight=1)
        self.run_button.grid(row=0,column=0,sticky='ew',pady=(3,0))
        self.cancel_button=ttk.Button(footer,text='取消',width=4,command=self.cancel_job,state='disabled')
        self.cancel_button.grid(row=0,column=1,sticky='ns',padx=(5,0),pady=(3,0))
        self.progress=ttk.Progressbar(footer,mode='indeterminate')
        self.progress.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(5,0))
        self.progress.configure(mode='determinate',value=0)
        self.bind('<MouseWheel>',self._config_wheel,add='+')
        self.bind('<Button-4>',self._config_wheel,add='+')
        self.bind('<Button-5>',self._config_wheel,add='+')
        for variable in [self.top_var,self.mode_var,self.path_var,self.hierarchy_var,self.trace_scope_var,self.extraction_mode_var]:
            variable.trace('w',self.mark_dirty)
        self.memory_limit_var.trace('w',lambda *args:self._schedule_session_save() if not self._filling else None)
        self.layer_reset_var.trace('w',self.mark_reset_dirty)
        for variable in self.div_source_vars:
            variable.trace('w', self.mark_div_sources_dirty)
        self.port_timeout_var.trace('w',lambda *args:self._schedule_session_save() if not self._filling else None)
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
        ttk.Button(history,text='诊断 / 从断点继续',command=self.recover_history).pack(fill='x',pady=(0,5))
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

    def toggle_options(self):
        self.options_open = not self.options_open
        self.options_button.configure(text='▾ 收起提取选项' if self.options_open else '▸ 提取选项')
        if self.options_open:
            self.options_frame.pack(fill='x',before=self.rules_separator)
        else:
            self.options_frame.pack_forget()

    def _build_center(self):
        self.diagram_title = title = ttk.Frame(self.center,padding=(12,6),style='White.TFrame')
        title.pack(fill='x')
        title.columnconfigure(1, weight=1)
        self.diagram_heading=ttk.Label(title,textvariable=self.diagram_name,style='Title.TLabel')
        self.diagram_heading.grid(row=0,column=0,sticky='w')
        self.summary_font=tkfont.Font(root=self,font=(self.family,9))
        ttk.Label(title, textvariable=self.view_label, style='Muted.TLabel', width=1, anchor='w').grid(
            row=0, column=1, sticky='ew', padx=12)
        self.tools_button = ttk.Menubutton(title, text='分析工具')
        self.tools_menu = tk.Menu(self.tools_button, tearoff=False)
        self.tools_button.configure(menu=self.tools_menu)
        self.tools_menu.add_command(label='辅助口寄存器追踪…', command=self.open_register_trace, state='disabled')
        self.tools_menu.add_command(label='分支 SV 模板…', command=self.open_sv_templates, state='disabled')
        self.tools_menu.add_separator()
        self.tools_menu.add_command(label='问题表 / CSV…', command=self.open_issues)
        self.tools_button.grid(row=0, column=2, padx=(0, 4))
        self.quick_tools=ttk.Frame(title,style='White.TFrame')
        self.quick_tools.grid(row=0,column=2,sticky='e')
        self.register_button=ttk.Button(self.quick_tools,text='寄存器追踪',image=glyph(title,'register'),compound='left',command=self.open_register_trace,state='disabled')
        self.sv_button=ttk.Button(self.quick_tools,text='分支 SV 模板',image=glyph(title,'code'),compound='left',command=self.open_sv_templates,state='disabled')
        self.issue_button=ttk.Button(self.quick_tools,text='问题表',image=glyph(title,'issue'),compound='left',command=self.open_issues)
        for button in (self.register_button,self.sv_button,self.issue_button):
            button.pack(side='left',padx=(0,4))
        self.quick_tools.grid_remove()
        title.bind('<Configure>',self._size_analysis_tools)
        self.panel_switch = ttk.Button(title, text='检查器', command=self.switch_panel)
        self.panel_switch.grid(row=0, column=3, padx=(0, 4))
        self.focus_button=ttk.Button(title,text='专注画布',image=glyph(title,'focus','blue'),compound='left',command=self.toggle_focus)
        self.focus_button.grid(row=0, column=4)

        self.stats_frame = stats = tk.Frame(self.center,bg='white',bd=0)
        stats.pack(fill='x', padx=12, pady=(0, 5))
        self.stats = {}
        for col,(key,label,icon,tone) in enumerate([
                ('instances','目标实例','chip','blue'),('edges','端口连线','link','teal'),
                ('roots','根结构','root','ink'),('terminals','分支末端','terminal','ink'),
                ('components','独立网络','network','ink'),('unresolved','未解析','issue','slate')]):
            stats.columnconfigure(col, weight=1, uniform='metrics')
            bg='#eef2ff' if col==0 else '#f6f8fc'
            cell=ttk.Frame(stats,style='First.Metric.TFrame' if col==0 else 'Metric.TFrame',padding=(8,3))
            cell.grid(row=0,column=col,sticky='nsew',padx=(0 if col==0 else 4,0))
            value = self.stats[key] = tk.StringVar(value='—')
            caption=tk.Label(cell,text=label,image=glyph(cell,icon,tone),compound='left',font=(self.family,8),
                             fg=MUTED,bg=bg,bd=0,padx=1,pady=0,anchor='w')
            caption.pack(fill='x')
            number=tk.Label(cell,textvariable=value,font=(self.latin,15,'bold'),fg=BLUE if col==0 else INK,
                            bg=bg,bd=0,padx=1,pady=0,anchor='w')
            number.pack(fill='x')
            if key == 'unresolved':
                self.unresolved_number = number
                self.unresolved_caption=caption

        self.diagram_toolbar = toolbar = ttk.Frame(self.center, padding=(12, 2, 12, 4), style='White.TFrame')
        toolbar.pack(fill='x')
        self.component_var = tk.StringVar(value='全部时钟网络')
        self.component_combo = ttk.Combobox(toolbar, textvariable=self.component_var, values=['全部时钟网络'], width=15, state='readonly')
        self.component_combo.pack(side='left', padx=(0, 4))
        self.component_combo.bind('<<ComboboxSelected>>', self.change_component)
        self.view_mode = tk.StringVar(value='实例明细')
        self.mode_combo = ttk.Combobox(toolbar, textvariable=self.view_mode, values=['实例明细','RTL 层次概览'], width=11, state='readonly')
        self.mode_combo.pack(side='left', padx=(0, 4))
        self.mode_combo.bind('<<ComboboxSelected>>', self.change_mode)
        self.layout_var = tk.StringVar(value='分支紧凑布局')
        self.references_var = tk.BooleanVar(value=True)
        self.legend_var = tk.BooleanVar(value=False)
        self.view_button = ttk.Menubutton(toolbar, text='视图')
        self.view_menu = menu = tk.Menu(self.view_button, tearoff=False)
        self.view_button.configure(menu=menu)
        self.view_button.pack(side='left')
        for layout in ('分支紧凑布局','按层排列'):
            menu.add_radiobutton(label=layout, variable=self.layout_var, value=layout, command=self.change_layout)
        menu.add_checkbutton(label='子系统分支引用', variable=self.references_var, command=self.change_layout)
        menu.add_checkbutton(label='显示器件图例', variable=self.legend_var, command=self.toggle_legend)
        menu.add_separator()
        menu.add_command(label='导航后退', command=lambda:self.diagram.navigate_history())
        menu.add_command(label='导航前进', command=lambda:self.diagram.navigate_history(True))
        menu.add_command(label='返回全网', command=lambda:self.diagram.reset_scope())
        menu.add_command(label='取消布局计算', command=lambda:self.diagram.cancel_layout())
        menu.add_separator()
        menu.add_command(label='清空搜索历史', command=self.clear_search_history)
        self.fit_button = ttk.Button(toolbar, text='全图', width=5, command=lambda:self.diagram.fit())
        self.fit_button.pack(side='right')
        ttk.Button(toolbar, text='+', width=2, command=lambda:self.diagram.zoom(1.25)).pack(side='right', padx=(0, 4))
        self.zoom_label = ttk.Label(toolbar, text='100%', style='Muted.TLabel', width=5, anchor='center')
        self.zoom_label.pack(side='right', padx=2)
        ttk.Button(toolbar, text='−', width=2, command=lambda:self.diagram.zoom(.8)).pack(side='right')

        searchrow = ttk.Frame(self.center, padding=(12, 1, 12, 5), style='White.TFrame')
        searchrow.pack(fill='x')
        ttk.Label(searchrow,text='实例定位',image=glyph(searchrow,'search'),compound='left',style='Small.TLabel').pack(side='left',padx=(0,6))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Combobox(searchrow, textvariable=self.search_var, width=6)
        self.search_entry.pack(side='left', fill='x', expand=True)
        self.search_entry.bind('<Return>', lambda e:self.locate_query())
        self.search_entry.bind('<<ComboboxSelected>>', lambda e:self.locate_query())
        self.locate_button = ttk.Button(searchrow, text='查找', width=5, command=self.locate_query)
        self.locate_button.pack(side='left', padx=(4, 2))
        ttk.Button(searchrow, text='下一个', width=6, command=lambda:self.locate_query(True)).pack(side='left')
        self.match_label = ttk.Label(searchrow, text='路径 / module', style='Muted.TLabel', width=12, anchor='e')
        self.match_label.pack(side='left', padx=(4, 0))

        scopebar=tk.Frame(self.center,bg='#1d2e47',padx=10,pady=2)
        scopebar.pack(fill='x')
        tk.Label(scopebar,image=glyph(scopebar,'branch','night'),bg='#1d2e47',bd=0).pack(side='left',padx=(0,5))
        tk.Label(scopebar,textvariable=self.layout_var,font=(self.family,8,'bold'),fg='#dce8ff',bg='#1d2e47',bd=0).pack(side='left')
        tk.Frame(scopebar,bg='#41536d',width=1,height=12).pack(side='left',padx=8)
        self.scope_text=tk.Label(scopebar,textvariable=self.scope_label,font=(self.family,8),fg='#b9c9e0',
                                 bg='#1d2e47',anchor='w',width=1)
        self.scope_text.pack(side='left', expand=True, fill='x')
        self.view_prev = ttk.Button(scopebar,text='上一组',width=5,command=lambda:self.diagram.page_view(-1))
        self.view_next = ttk.Button(scopebar,text='下一组',width=5,command=lambda:self.diagram.page_view(1))
        self.legend_button=tk.Checkbutton(scopebar,text='器件图例',variable=self.legend_var,command=self.toggle_legend,
                                          font=(self.family,8),bg='#1d2e47',fg='#d3e0f2',activebackground='#2a3d59',
                                          activeforeground='white',selectcolor='#344e70',highlightthickness=0,padx=2,pady=0)
        self.legend_button.pack(side='right')
        self.devices_legend = tk.Canvas(self.center, width=1, height=24, bg='#edf2fa', highlightthickness=0)
        self._legend_font = tkfont.Font(root=self, font=(self.family, 8))
        self._legend_key = None
        self.devices_legend.bind('<Configure>', self._draw_device_legend)

        dock = ttk.Frame(self.center, style='White.TFrame')
        dock.pack(side='bottom', fill='x')
        dockbar = ttk.Frame(dock, padding=(8, 2), style='White.TFrame')
        dockbar.pack(fill='x')
        self.log_toggle=ttk.Button(dockbar,text='运行阶段与日志 ▴',image=glyph(dockbar,'log'),compound='left',style='Quiet.TButton',command=self.toggle_log)
        self.log_toggle.pack(side='left')
        self.log_frame = ttk.Frame(dock, style='White.TFrame')
        self.stage_view = StageView(self.log_frame, self.family, self.show_stage_log)
        self.stage_view.pack(fill='x')
        ttk.Label(dockbar, textvariable=self.stage_view.caption, width=1, anchor='e', style='Muted.TLabel').pack(
            side='right', fill='x', expand=True, padx=7)
        self.log_visible = False
        self.diagram = ClockDiagram(self.center, self.family, self.inspect_node, self.inspect_edges, self.view_changed,
                                    self.set_status, on_branch=self.open_branch)
        self.diagram.pack(fill='both', expand=True)
        self.empty_actions=tk.Frame(self.diagram,bg=BOARD)
        ttk.Button(self.empty_actions,text='打开结果',image=glyph(self,'folder','light'),compound='left',command=self.open_result,style='Header.TButton').pack(side='left',padx=4)
        ttk.Button(self.empty_actions,text='查看示例',command=self.open_example).pack(side='left',padx=4)
        self.empty_actions.place(relx=.5,rely=.5,y=10,anchor='n')
        logbar=ttk.Frame(self.log_frame,style='White.TFrame',padding=(10,3))
        logbar.pack(fill='x')
        self.log_source=tk.StringVar(value='阶段日志')
        choices=ttk.Combobox(logbar,textvariable=self.log_source,values=['阶段日志','Verdi 日志','CLI 日志','GUI 日志'],state='readonly',width=12)
        choices.pack(side='left')
        choices.bind('<<ComboboxSelected>>',lambda event:self.show_stage_log(None))
        ttk.Button(logbar,text='阶段诊断',command=self.show_run_diagnosis).pack(side='left',padx=7)
        self.log_path=tk.StringVar()
        ttk.Entry(logbar,textvariable=self.log_path,state='readonly',width=1).pack(side='left',fill='x',expand=True)
        self.log_text=tk.Text(self.log_frame,height=6,font=(self.mono,8),bg='#f6f8fc',fg='#647792',relief='flat',wrap='word',padx=13,pady=8,state='disabled')
        scrollbar=ttk.Scrollbar(self.log_frame,command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y')
        self.log_text.pack(fill='both',expand=True)

    def _build_inspector(self):
        panel_heading(self.right,'检查器','inspector')
        self.inspector_tabs=ttk.Notebook(self.right)
        self.inspector_tabs.pack(fill='both',expand=True)
        prop=ttk.Frame(self.inspector_tabs,style='White.TFrame',padding=11)
        ev=ttk.Frame(self.inspector_tabs,style='White.TFrame')
        diag=ttk.Frame(self.inspector_tabs,style='White.TFrame')
        for f,t in [(prop,'属性'),(ev,'连线证据'),(diag,'诊断')]:
            self.inspector_tabs.add(f,text=t)
        details=ttk.Panedwindow(prop,orient='vertical')
        values=ttk.Frame(details,style='White.TFrame')
        links=ttk.Frame(details,style='White.TFrame')
        details.add(values,weight=3)
        details.add(links,weight=2)
        self.prop_text=self._scroll_text(values,self.family,10)
        for tag,opts in [('heading',{'font':(self.family,13,'bold'),'foreground':INK,'spacing3':9}),
                         ('label',{'font':(self.family,8),'foreground':MUTED,'spacing1':10}),
                         ('value',{'font':(self.family,10),'foreground':INK,'spacing3':5}),
                         ('inline-label',{'font':(self.family,9),'foreground':MUTED,'spacing1':3}),
                         ('inline-value',{'font':(self.family,10),'foreground':INK,'spacing3':4}),
                         ('good',{'foreground':'#21886f'}),('accent',{'foreground':BLUE})]:
            self.prop_text.tag_configure(tag,**opts)
        self.node_actions=ttk.Frame(prop,style='White.TFrame')
        self.property_font=tkfont.Font(root=self,family=self.family,size=10)
        self.property_tab=self.property_font.measure('Module 定义  ')+14
        self.prop_text.configure(tabs=(self.property_tab,))
        identity=tk.Frame(self.node_actions,bg='#f0f4fb',bd=0)
        identity.pack(fill='x',pady=(0,6))
        self.identity_stripe=tk.Frame(identity,width=3)
        self.identity_stripe.pack(side='left',fill='y',padx=(0,9))
        self.identity_type=tk.Label(identity,anchor='w',bg='#f0f4fb',fg='#4d6484',font=(self.family,9))
        self.identity_type.pack(fill='x',pady=(4,0))
        self.identity_name=tk.Label(identity,anchor='w',width=1,bg='#f0f4fb',fg=INK,font=(self.family,12,'bold'))
        self.identity_name.pack(fill='x',padx=(0,6),pady=(0,5))
        self.property_panes, self.links_pane=details,links
        self.expand_button=ttk.Button(self.node_actions,text='展开此 RTL 层次',command=self.expand_selected,state='disabled')
        paths=ttk.Frame(self.node_actions,style='White.TFrame')
        paths.pack(fill='x')
        self.upstream_button=ttk.Button(paths,text='上游 (0)',image=glyph(paths,'upstream'),compound='left',command=lambda:self.jump_neighbour('upstream'))
        self.upstream_button.pack(side='left',fill='x',expand=True,padx=(0,4))
        self.downstream_button=ttk.Button(paths,text='下游 (0)',image=glyph(paths,'downstream'),compound='left',command=lambda:self.jump_neighbour('downstream'))
        self.downstream_button.pack(side='left',fill='x',expand=True)
        actions=ttk.Frame(self.node_actions,style='White.TFrame')
        actions.pack(fill='x',pady=(4,2))
        ttk.Button(actions,text='高亮整个分支',image=glyph(actions,'branch','blue'),compound='left',command=lambda:self.highlight_path('branch')).pack(side='left',expand=True,fill='x')
        more=ttk.Menubutton(actions,text='高亮选项')
        more.pack(side='left',padx=(4,0))
        choices=tk.Menu(more,tearoff=False)
        more.configure(menu=choices)
        choices.add_command(label='适配高亮范围',command=lambda:self.diagram.fit_highlight())
        choices.add_command(label='清除高亮',command=lambda:self.diagram.clear_highlight())
        details.pack(fill='both',expand=True)
        self.connection_title=ttk.Label(links,text='相关端口连接',style='Small.TLabel')
        self.connection_title.pack(anchor='w',pady=(5,3))
        self.connections=ttk.Treeview(links,columns=('direction','pin'),show='tree headings',height=4,selectmode='browse')
        self.connections.heading('#0',text='对端实例')
        self.connections.heading('direction',text='方向')
        self.connections.heading('pin',text='端口')
        self.connections.column('#0',width=138,minwidth=70)
        self.connections.column('direction',width=42,stretch=False)
        self.connections.column('pin',width=64,minwidth=35)
        self.connections.bind('<<TreeviewSelect>>',self.inspect_related)
        self.connections.bind('<Double-1>',self.locate_related)
        connection_pager=ttk.Frame(links,style='White.TFrame')
        connection_pager.pack(side='bottom',fill='x',pady=(3,0))
        self.connection_prev=ttk.Button(connection_pager,text='上一页',command=lambda:self.connection_page(-1),state='disabled')
        self.connection_prev.pack(side='left')
        self.connection_next=ttk.Button(connection_pager,text='下一页',command=lambda:self.connection_page(1),state='disabled')
        self.connection_next.pack(side='right')
        scroll=ttk.Scrollbar(links,command=self.connections.yview)
        scroll.pack(side='right',fill='y')
        self.connections.configure(yscrollcommand=scroll.set)
        self.connections.pack(fill='both',expand=True)
        self.raw_evidence_var=tk.BooleanVar(value=False)
        ttk.Checkbutton(ev,text='展开原始证据（按需读取 NPI 路径）',variable=self.raw_evidence_var,command=self.toggle_raw_evidence).pack(anchor='w',padx=10,pady=7)
        self.evidence_text=self._scroll_text(ev,self.family,9)
        ttk.Button(diag,text='打开完整问题表 · 筛选 / 定位 / 导出',command=self.open_issues).pack(fill='x',padx=8,pady=8)
        self.diag_text=self._scroll_text(diag,self.family,9)
        self._properties('全量时钟树架构', [('选择节点','查看真实 RTL 实例、输入输出端口和上下游连接。'),('选择连线','查看本次提取的 NPI 连接证据。'),('层次概览','虚线框表示多个真实实例的聚合，双击展开。')])

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

    def _size_analysis_tools(self,event=None):
        title=self.diagram_title
        caption_width=max(140,self.summary_font.measure(self.view_label.get()))
        needed=(self.quick_tools.winfo_reqwidth()+self.focus_button.winfo_reqwidth()
                +self.diagram_heading.winfo_reqwidth()+caption_width+56)
        if self._compact and not self.focused_view:
            needed += self.panel_switch.winfo_reqwidth()
        show_buttons=title.winfo_width() >= needed
        if show_buttons==(self.quick_tools.winfo_manager()=='grid'):
            return
        if show_buttons:
            self.tools_button.grid_remove()
            self.quick_tools.grid()
        else:
            self.quick_tools.grid_remove()
            self.tools_button.grid()

    def _analysis_state(self):
        loaded = self.graph is not None
        for index, (button, enabled) in enumerate(((self.register_button, loaded),
                                                   (self.sv_button, loaded and self._active_tree_type == 'clock'))):
            state = 'normal' if enabled else 'disabled'
            button.configure(state=state)
            self.tools_menu.entryconfigure(index, state=state)
        for label in ('导出全量 JSON…','导出当前视图 SVG…'):
            self.file_menu.entryconfigure(label,state='normal' if loaded else 'disabled')

    def _resize_workspace(self, event):
        if event.width > 1 and (event.width < 1200) != self._compact and self._pane_job is None:
            self._pane_job = self.after_idle(self._arrange_panes)

    def _remember_sashes(self, event=None):
        if self.focused_view:
            return
        visible = tuple(map(str,self.panes.panes()))
        for side in ('left', 'right'):
            pane = getattr(self, side)
            if str(pane) in visible:
                self._side_widths[side] = max(220, min(500, pane.winfo_width()))
        if self.center.winfo_width() < 500:
            self.after_idle(self._arrange_panes)

    def _arrange_panes(self):
        if self._pane_job is not None:
            self.after_cancel(self._pane_job)
            self._pane_job = None
        width = self.panes.winfo_width()
        if width <= 1:
            return
        self._compact = width < 1200
        wanted = [self.center]
        if not self.focused_view:
            if not self._compact or self._compact_side == 'left':
                wanted.insert(0, self.left)
            if not self._compact or self._compact_side == 'right':
                wanted.append(self.right)
        changed = tuple(map(str,self.panes.panes())) != tuple(map(str,wanted))
        for pane in (self.left, self.right):
            if pane not in wanted and str(pane) in tuple(map(str,self.panes.panes())):
                self.panes.forget(pane)
        for index, pane in enumerate(wanted):
            if str(pane) not in tuple(map(str,self.panes.panes())):
                self.panes.insert(index if index < len(self.panes.panes()) else 'end', pane, weight=0)
        if changed:
            # Tk applies pane insertion at idle. Set sash positions after the new
            # pane has received geometry, including when swapping compact panels.
            self.panes.update_idletasks()
        limit = max(220, (width - 550) // (len(wanted)-1)) if len(wanted)>1 else 0
        if self.left in wanted:
            self.panes.sashpos(0, min(limit, self._side_widths['left']))
        if self.right in wanted:
            self.panes.sashpos(len(wanted)-2, width - min(limit, self._side_widths['right']))
        if self._compact and not self.focused_view:
            self.panel_switch.configure(text='检查器' if self._compact_side == 'left' else '设计面板')
            self.panel_switch.grid()
        else:
            self.panel_switch.grid_remove()
        self._size_analysis_tools()

    def show_panel(self, side):
        if self._compact and self._compact_side != side:
            self._compact_side = side
            if not self.focused_view:
                self._arrange_panes()

    def switch_panel(self):
        self.show_panel('right' if self._compact_side == 'left' else 'left')

    def toggle_focus(self, event=None):
        self.focused_view = not self.focused_view
        if self.focused_view:
            self.stats_frame.pack_forget()
        else:
            self.stats_frame.pack(fill='x', padx=12, pady=(0, 5), before=self.diagram_toolbar)
        self.focus_button.configure(text='恢复面板' if self.focused_view else '专注画布')
        self._arrange_panes()
        return 'break'

    def focus_search(self, event=None):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, 'end')
        return 'break'

    def toggle_legend(self):
        if self.legend_var.get():
            self.devices_legend.pack(fill='x', before=self.diagram)
        else:
            self.devices_legend.pack_forget()

    def _config_wheel(self,event):
        if str(event.widget).startswith(str(self._settings_inner)) and event.widget is not self.rules_tree:
            up=event.num==4 or (event.num!=5 and getattr(event,'delta',0)>0)
            self.settings_canvas.yview_scroll(-2 if up else 2,'units')
            return 'break'

    def set_status(self,text):
        self.status.set(str(text))

    def mark_dirty(self,*args):
        if not self._filling:
            self._config_dirty=True
            self._schedule_session_save()
        if not self._filling and self.graph and not self.busy:
            self._badge('规则已修改 · 待重新提取','warn')

    def mark_reset_dirty(self,*args):
        if self._filling:return
        if self.graph and not self.busy:
            self._config_dirty=not same_config(extraction_settings(self.get_config(validate=False)),extraction_settings(self.graph['config']))
            if not self._config_dirty:self._badge('复位配置已更新','idle')
        self._schedule_session_save()
        if self.graph and not self.busy:self.set_status('复位配置已修改；重新分析 SV 分支后生效。仅修改复位无需重提取时钟树或寄存器。')

    def mark_div_sources_dirty(self, *args):
        if self._filling:
            return
        self._schedule_session_save()
        self.refresh_div_colors()
        if self.graph and not self.busy:
            self.set_status('DIV 来源路径已更新，画布按已完成的寄存器追踪刷新。')

    def refresh_div_colors(self):
        self._div_color_request = token = object()
        if self._div_color_after is not None:
            self.after_cancel(self._div_color_after)
            self._div_color_after = None
        model = self.diagram.model
        if self.graph is None or model is None or model.tree_type != 'clock':
            self._div_color_context = None
            return
        config = self.graph['config']
        draft = self.get_config(validate=False)
        try:
            scopes = div_source_scopes(draft if design_key(draft) == design_key(config) else config)
        except ValueError:
            scopes = ()
        context = (model, scopes)
        if context != self._div_color_context:
            self._div_color_context = context
            self._apply_div_colors(model, {})
        if scopes:
            self._div_color_after = self.after(180, self._start_div_colors, model, config, scopes, token)

    def _start_div_colors(self, model, config, scopes, token):
        self._div_color_after = None
        preferences = self.ui_state.get(config)
        known = tuple(preferences.get(key) for key in ('register_trace_completed_directory',
                      'register_trace_directory', 'sv_register_trace_directory'))

        def worker():
            try:
                directory = latest_register_result(config, self.runs, known)
                levels = read_div_source_levels(directory, scopes)
            except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.DatabaseError) as error:
                self.events.put(('div_colors', token, model, {}, str(error)))
            else:
                self.events.put(('div_colors', token, model, levels, ''))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_div_colors(self, model, levels):
        if levels == model.div_source_levels:
            return
        model.div_source_levels = levels
        self.diagram.schedule()
        from .branch_view import BranchDialog
        for branch in self.winfo_children():
            if isinstance(branch, BranchDialog) and branch.source_model is model and branch.diagram.model:
                branch.diagram.model.div_source_levels = levels
                branch.diagram.schedule()

    def _badge(self,text,kind='idle'):
        self.state.set(text)
        self.state_badge.configure(style=kind+'.Status.TLabel')

    def _remember_workspace(self):
        if self._session_restoring:return
        config=self.get_config(validate=False)
        deferred=self._workspaces.get(self._active_tree_type,{}).get('deferred_result') if self.graph is None else None
        self._workspaces[self._active_tree_type]={
            'config':config,'graph':self.graph,'path':self.graph_file,
            'dirty':self._config_dirty or bool(self.graph and not same_config(extraction_settings(config),extraction_settings(self.graph['config']))),
            'active_layer':self.active_layer,'graph_token':self._graph_token,'deferred_result':deferred}

    def _load_previous_session(self,config_path=None,result_path=None):
        def worker():
            self.gui_journal.emit('begin','session','恢复历史配置与结果索引',{'runs':str(self.runs),
                'manifest':str(self._session_store.manifest)})
            try:
                state=self._session_store.load(load_results=not bool(self._previous_gui_issue))
            except Exception as error:
                state=self._session_store.history(load_results=not bool(self._previous_gui_issue))
                state['warnings'].append('自动记录不可读：'+str(error))
            try:
                if config_path:
                    config=load_config(config_path)
                    mode=tree_type(config)
                    workspace=state['workspaces'].get(mode,{'graph':None,'path':None,'graph_token':None})
                    state['workspaces'][mode]=dict(workspace,config=config,dirty=bool(workspace['graph']),active_layer=1)
                    state.update(active=mode,origin='explicit')
                elif result_path:
                    graph=read_graph(result_path)
                    mode=tree_type(graph['config'])
                    state['workspaces'][mode]={'config':copy.deepcopy(graph['config']),'graph':graph,
                        'path':Path(result_path).resolve(),'dirty':False,'active_layer':1,'graph_token':uuid.uuid4().hex}
                    state.update(active=mode,origin='explicit')
            except Exception as error:
                state['warnings'].append('启动指定文件未能加载：'+str(error))
            self.gui_journal.emit('end','session','历史工作区读取完成')
            self.events.put(('session_loaded',state))
        threading.Thread(target=worker,daemon=True).start()

    def _apply_previous_session(self,state):
        self._session_restoring=True
        self._workspaces=state['workspaces']
        mode=state['active']
        workspace=self._workspaces.get(mode,{'config':empty_config(mode),'graph':None,'path':None,
                                            'dirty':False,'active_layer':1,'graph_token':None})
        try:
            if workspace.get('graph') is not None:
                self.show_graph(workspace['graph'],workspace.get('path'),workspace.get('graph_token'))
            self.set_config(workspace['config'])
            index=workspace.get('active_layer',1)
            self.select_layer(index if type(index) is int and 1<=index<=len(self.layers) else 1,save=False)
            self._graph_token=workspace.get('graph_token')
        finally:
            self._session_restoring=False
            self._session_ready=True
            self._set_busy(None)
        if workspace.get('dirty'):self.mark_dirty()
        origin=state.get('origin')
        if origin=='empty':
            self.set_status('未找到历史记录 · 默认两级，可添加层级或导入配置。')
        else:
            label='已加载启动指定文件' if origin=='explicit' else ('已从最近历史运行恢复' if origin=='history' else '已自动恢复上次工作区')
            self.set_status('{}：{} · {}级{}。'.format(label,TREE_PROFILES[mode]['label'],len(self.layers),
                            ' · 配置及结果' if self.graph else ' · 配置草稿'))
        if state['warnings']:
            self.set_status('；'.join(state['warnings']))
            if not self.graph:self._badge('配置已恢复 · 查看状态提示','warn')
        self._schedule_session_save()
        if self._previous_gui_issue:
            self._recovery_result=workspace.get('path')
            if workspace.get('deferred_result') and (not self._recovery_result or not Path(self._recovery_result).is_file()):
                self._recovery_result=self._session_store.directory/workspace['deferred_result']
            self.set_status('检测到上次界面异常退出；先恢复配置，等待选择继续任务或打开结果。')
        self.after(100,self.check_previous_run)

    def _schedule_session_save(self):
        if not self._session_writer or not self._session_ready or self._session_restoring or self._destroying:return
        if self._session_after is not None:self.after_cancel(self._session_after)
        self._session_after=self.after(650,self._save_session_now)

    def _save_session_now(self):
        if self._session_after is not None:
            self.after_cancel(self._session_after)
            self._session_after=None
        if not self._session_writer or not self._session_ready or self._session_restoring:return
        self._remember_workspace()
        workspaces,prepared={},{}
        for mode,original in self._workspaces.items():
            workspace=dict(original,config=copy.deepcopy(original['config']))
            graph=workspace.get('graph')
            if graph is not None:
                token=workspace.get('graph_token') or uuid.uuid4().hex
                original['graph_token']=workspace['graph_token']=token
                if mode==self._active_tree_type:self._graph_token=token
                frozen=self._session_prepared.get(token)
                if frozen is None:frozen=snapshot_graph(graph)
                prepared[token]=workspace['graph']=frozen
            workspaces[mode]=workspace
        self._session_prepared=prepared
        self._session_writer.submit({'active':self._active_tree_type,'workspaces':workspaces})

    def _finish_close(self):
        if not self._close_save_started:
            self.gui_journal.emit('begin','session','保存退出前的历史配置与结果')
            self._save_session_now()
            self._close_save_started=True
        writer=self._session_writer
        if writer and not writer.idle:
            self._set_busy('session_save')
            self.set_status('正在保存上次配置与结果，请稍候…')
            return
        if writer and writer.error:
            self._set_busy(None)
            self._closing=False
            self._close_save_started=False
            if not messagebox.askyesno('自动保存失败','无法写入自动恢复记录：{}\n\n当前配置和结果仍在窗口中，可先手动保存。是否仍要退出？'.format(writer.error),parent=self,default='no'):
                return
            self._close_save_started=True
        self.destroy()

    def _clear_result(self):
        self.graph,self.graph_file=None,None
        self._graph_token=None
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
        self.unresolved_number.configure(fg=INK)
        self.unresolved_caption.configure(image=glyph(self,'issue','slate'))
        self._analysis_state()
        self.node_actions.pack_forget()
        self._fill_connections([],None)
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
        if not self.busy:self.empty_actions.place(relx=.5,rely=.5,y=10,anchor='n')
        self._badge('等待设计')

    def _apply_profile(self):
        info=TREE_PROFILES[self._active_tree_type]
        self.analysis_var.set(info['label'])
        self.diagram_name.set(info['label']+'架构图')
        self.run_button.configure(text='开始提取全部'+info['label'])
        clock_mode = self._active_tree_type == 'clock'
        self.layer_hint.configure(text='多输入 MUX · 同层串接 · 跨层跳接\n分支可在任意层开始或结束' if clock_mode
                                  else '多输入 MUX · 同层串接 · 跨层跳接\n分支允许在任意层结束')
        if clock_mode:
            self.div_sources_frame.pack(fill='x', pady=(12, 0))
        else:
            self.div_sources_frame.pack_forget()
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
        self._draw_device_legend()

    def _draw_device_legend(self, event=None):
        c = self.devices_legend
        width = c.winfo_width()
        if width <= 1:
            return
        kinds = list(selectable_kinds(self._base_config))
        if self._active_tree_type == 'clock':
            configured = {rule.get('kind') for layer in self.layers for rule in layer.get('modules', [])}
            for legacy, subtype in [('gate', 'sync_gate'), ('occ', 'occ_root')]:
                if legacy in configured:
                    kinds.insert(kinds.index(subtype), legacy)
        key = (width, self._active_tree_type, tuple(kinds))
        if key == self._legend_key:
            return
        self._legend_key = key
        c.delete('all')
        x, y = 12, 11
        entries = [(kind, level) for kind in kinds
                   for level in (tuple(DIV_SOURCE_STYLES) + (None,) if kind == 'div' else (None,))]
        for kind, div_level in entries:
            style = device_style(kind, div_level)
            color, fill = style['color'], style['fill']
            labels = {'mux': 'MUX' if self._active_tree_type == 'reset' else '选择器', 'gate': '门控', 'wire': '穿线'}
            label = 'DIV 未归类' if kind == 'div' and div_level is None else labels.get(kind, style['short'])
            step = 38 + self._legend_font.measure(label)
            if x > 12 and x + step > width:
                x, y = 12, y + 23
            if kind == 'mux':
                c.create_polygon(x,y-8,x+22,y-5,x+22,y+5,x,y+8,fill=fill,outline=color)
            elif kind in GATE_KINDS:
                c.create_polygon(x,y-8,x+13,y-8,x+19,y-6,x+22,y-2,x+22,y+2,x+19,y+6,x+13,y+8,x,y+8,
                                 fill=fill,outline=color)
            elif kind == 'div':
                c.create_polygon(x+5,y-8,x+19,y-8,x+24,y,x+19,y+8,x+5,y+8,x,y,fill=fill,outline=color)
            elif kind == 'rsy_sync':
                c.create_rectangle(x,y-8,x+24,y+8,fill=fill,outline=color)
                c.create_line(x+7,y-4,x+7,y+4,x+17,y+4,x+17,y-4,fill=color)
            elif kind == 'subsystem':
                c.create_rectangle(x+5,y-8,x+24,y+8,fill=fill,outline=color)
                for yy in (y-4, y+4):
                    c.create_line(x,yy,x+5,yy,fill=color)
            elif kind == 'wire':
                c.create_rectangle(x,y-8,x+24,y+8,fill=fill,outline=color)
                c.create_line(x+3,y-4,x+21,y+4,fill=color)
                c.create_line(x+3,y+4,x+21,y-4,fill=color)
            else:
                c.create_polygon(x+4,y-8,x+20,y-8,x+24,y-4,x+24,y+4,x+20,y+8,x+4,y+8,x,y+4,x,y-4,fill=fill,outline=color)
            c.create_text(x+28,y,text=label,anchor='w',fill='#586e8b',font=self._legend_font)
            x += step
        for label,color in (('根','#29aa85'),('末端','#d39c3f'),('聚合','#698dbd')):
            step=25+self._legend_font.measure(label)
            if x>12 and x+step>width:
                x,y=12,y+23
            if label=='聚合':
                c.create_rectangle(x,y-5,x+10,y+5,outline=color,dash=(2,1))
            else:
                c.create_oval(x+1,y-4,x+9,y+4,fill=color,outline='')
            c.create_text(x+15,y,text=label,anchor='w',fill='#586e8b',font=self._legend_font)
            x+=step
        c.configure(height=y+12)

    def switch_tree_type(self,event=None):
        target=event if isinstance(event,str) and event in TREE_PROFILES else LABEL_TO_TREE[self.analysis_var.get()]
        if self.busy:
            self.analysis_var.set(TREE_PROFILES[self._active_tree_type]['label'])
            return
        if target==self._active_tree_type:return
        saved=self._workspaces.get(target)
        if saved and saved['graph'] is not None:
            self.show_graph(saved['graph'],saved['path'],saved.get('graph_token'))
            self.set_config(saved['config'])
            if saved['dirty']:self.mark_dirty()
        else:
            self.set_config(saved['config'] if saved else empty_config(target))
        if saved:
            index=saved.get('active_layer',1)
            self.select_layer(index if type(index) is int and 1<=index<=len(self.layers) else 1,save=False)
        self.set_status('已切换到{}；两类分析的配置和图形分别保留。'.format(TREE_PROFILES[target]['label']))
        self._schedule_session_save()

    def rebuild_layer_buttons(self):
        for button in self.layer_buttons:button.destroy()
        self.layer_buttons=[]
        for index in range(1,len(self.layers)+1):
            button=ttk.Button(self.layerrow,text=self.layer_button_label(index),width=4,style='Layer.TButton',command=lambda i=index:self.select_layer(i))
            button.grid(row=(index-1)//5,column=(index-1)%5,sticky='ew',padx=(0,3),pady=(0,4))
            self.layer_buttons.append(button)
        for column in range(5):self.layerrow.columnconfigure(column,weight=1)
        self.layer_count_var.set('传播规则 · {} 级'.format(len(self.layers)))

    def layer_button_label(self, index):
        return 'L{}{}'.format(index, '*' if self.layers[index-1].get('input_boundary') else '')

    def change_input_boundary(self):
        self._save_layer_inputs()
        self.layer_buttons[self.active_layer-1].configure(text=self.layer_button_label(self.active_layer))
        self.mark_dirty()

    def add_layer(self):
        self._save_layer_inputs()
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
        self.show_config_panel()
        self.mark_dirty()
        self.set_status('已新建两级配置。填写设计与规则，或点击“添加层级”。')

    def open_example(self):
        self.load_result(ROOT/('examples/reset_tree/resettree.json' if self._active_tree_type=='reset' else 'examples/flexible_layers/clocktree.json'))

    def select_layer(self,index,save=True):
        if save:self._save_layer_inputs()
        self.active_layer=index
        self._filling=True
        self.hierarchy_var.set(self.layers[index-1].get('hierarchy',''))
        self.layer_input_boundary_var.set(self.layers[index-1].get('input_boundary', False))
        self.layer_reset_var.set(self.layers[index-1].get('reset_signal',''))
        self._filling=False
        self.layer_title.configure(text='L{} · RTL 父层路径'.format(index))
        for i,button in enumerate(self.layer_buttons,1):
            button.configure(style='LayerActive.TButton' if i==index else 'Layer.TButton')
        self.refresh_rules()
        self._schedule_session_save()

    def _save_layer_inputs(self):
        layer=self.layers[self.active_layer-1]
        layer['hierarchy']=self.hierarchy_var.get().strip()
        if self.layer_input_boundary_var.get():
            layer['input_boundary'] = True
        else:
            layer.pop('input_boundary', None)
        reset=self.layer_reset_var.get().strip()
        if reset:layer['reset_signal']=reset
        else:layer.pop('reset_signal',None)

    def refresh_rules(self):
        self.rules_tree.delete(*self.rules_tree.get_children())
        rules=self.layers[self.active_layer-1].get('modules',[])
        self.rules_tree.configure(height=min(6,max(3,len(rules))))
        for i,rule in enumerate(rules):
            inputs,output=rule_ports(rule,self._active_tree_type)
            self.rules_tree.insert('', 'end',iid=str(i),text=rule['module'],values=(kind_label(rule.get('kind'),self._active_tree_type),', '.join(inputs),', '.join(output) if isinstance(output,list) else output or '—'))

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
        metadata_fields = ('kind', 'input_regex', 'output_regex', 'port_selection')
        if not self.graph or {k:v for k,v in old.items() if k not in metadata_fields}!={k:v for k,v in new.items() if k not in metadata_fields}:
            return False
        if old.get('kind')==new.get('kind'):return True
        if self.active_layer>len(self.graph.get('config',{}).get('layers',[])):return False
        layer=self.graph['config']['layers'][self.active_layer-1]
        if layer['hierarchy']!=self.hierarchy_var.get().strip():return False
        candidates=RecordSequence(self.graph.store,'nodes','layer=? AND module=?',(self.active_layer,new['module'])) if isinstance(self.graph,IndexedGraph) else self.graph['nodes']
        matching_count=0
        for node in candidates:
            if matching_rule(node,layer,new):
                matching_count+=1
                if not isinstance(self.graph,IndexedGraph):node['kind'],node['kind_source']=new['kind'],'configured'
        if not matching_count:return False
        for rule in layer['modules']:
            if all(rule.get(k)==old.get(k) for k in ('module',profile(self._base_config)['inputs'],profile(self._base_config)['output'])):rule['kind']=new['kind']
        self.graph.setdefault('presentation',{}).setdefault('kind_overrides',[]).append({
            'layer':self.active_layer,'module':new['module'],'kind':new['kind'],'from_kind':old.get('kind'),'instances':matching_count,
            'changed_at':datetime.datetime.now().isoformat(),'source':'user_type_selection'})
        self._graph_token=uuid.uuid4().hex
        selected=self.diagram.selected
        self._apply_profile()
        self.diagram.model=IndexedGraphView(self.graph) if isinstance(self.graph,IndexedGraph) else GraphView(self.graph)
        self.refresh_div_colors()
        self.diagram.rebuild()
        if selected in self.diagram.model.nodes:self.diagram.select(self.diagram.model.nodes[selected])
        self.set_status('已将 {} 的 {} 个实例更新为{}模型。'.format(new['module'],matching_count,kind_label(new['kind'],self._active_tree_type)))
        self._schedule_session_save()
        return True

    def remove_rule(self):
        selection=self.rules_tree.selection()
        if selection:
            del self.layers[self.active_layer-1]['modules'][int(selection[0])]
            self.refresh_rules()
            self.mark_dirty()

    def set_config(self,config,refresh_colors=True):
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
        use_elab=bool(design.get('elab')) or ('elab' in design and not design.get('filelist'))
        self.mode_var.set('已有 elab / KDB' if use_elab else 'RTL 文件列表')
        self.path_var.set(design.get('elab',design.get('filelist','')))
        self.trace_scope_var.set(config.get('trace_scope',''))
        for variable, scope in zip(self.div_source_vars, list(div_source_scopes(config, allow_incomplete=True)) or ['', '']):
            variable.set(scope)
        self.extraction_mode_var.set(EXTRACTION_MODES[config.get('extraction_mode','target')])
        self.memory_limit_var.set(str(config.get('memory_limit_gib',60)))
        self.port_timeout_var.set(str(config.get('port_trace_timeout_s',0)))
        self.layers=copy.deepcopy(config.get('layers',[]))
        if not self.layers:
            self.layers=[{'index':i,'hierarchy':'','modules':[]} for i in (1,2)]
        self.rebuild_layer_buttons()
        self.select_layer(1,save=False)
        self._apply_profile()
        self._filling=False
        self._config_dirty=False
        self.refresh_runs()
        self._schedule_session_save()
        if refresh_colors:
            self.refresh_div_colors()

    def get_config(self,validate=True):
        self._save_layer_inputs()
        data=copy.deepcopy(self._base_config)
        design=data.setdefault('design',{})
        design.pop('elab',None)
        design.pop('filelist',None)
        design.update(top=self.top_var.get().strip())
        design['elab' if self.mode_var.get()=='已有 elab / KDB' else 'filelist']=self.path_var.get().strip()
        data['layers']=copy.deepcopy(self.layers)
        data['tree_type']=self._active_tree_type
        scope=self.trace_scope_var.get().strip().rstrip('./')
        if scope:data['trace_scope']=scope
        else:data.pop('trace_scope',None)
        div_paths = [variable.get().strip().rstrip('./') for variable in self.div_source_vars]
        if self._active_tree_type == 'clock' and any(div_paths):
            data['div_source_scopes'] = div_paths
        else:
            data.pop('div_source_scopes', None)
        extraction_mode=next(k for k,v in EXTRACTION_MODES.items() if v==self.extraction_mode_var.get())
        if extraction_mode=='target' and 'extraction_mode' not in self._base_config:data.pop('extraction_mode',None)
        else:data['extraction_mode']=extraction_mode
        memory_text=self.memory_limit_var.get().strip()
        try:
            memory_limit=float(memory_text)
            if not math.isfinite(memory_limit):raise ValueError('finite number required')
        except ValueError:
            if validate:raise ValueError('内存阈值需填写非负数字，单位 GiB；0 表示关闭。')
            memory_limit=memory_text
        if memory_limit==60 and 'memory_limit_gib' not in self._base_config:data.pop('memory_limit_gib',None)
        else:data['memory_limit_gib']=memory_limit
        timeout_text=self.port_timeout_var.get().strip()
        try:
            timeout=float(timeout_text)
            if not math.isfinite(timeout) or timeout<0:raise ValueError('nonnegative finite number required')
        except ValueError:
            if validate:raise ValueError('端口 trace 超时需填写非负数字，单位秒；0 表示不限时。')
            timeout=timeout_text
        if timeout==0 and 'port_trace_timeout_s' not in self._base_config:data.pop('port_trace_timeout_s',None)
        else:data['port_trace_timeout_s']=timeout
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
                detected.set('命令：{}\n安装目录：{}\nNPI 安装文件：已找到'.format(executable,home))
            except ValueError as error:detected.set(str(error))
        ttk.Label(frame,textvariable=detected,style='White.TLabel',wraplength=570).pack(anchor='w')
        ttk.Button(frame,text='重新检测',command=detect).pack(anchor='w',pady=(12,0))
        detect()
        ttk.Button(frame,text='完成',style='Accent.TButton',command=dialog.destroy).pack(anchor='e',pady=(18,0))
        fit_window(dialog,self,(620,295),(440,230))
        dialog.grab_set()
        return dialog

    def open_device_library(self):
        dialog=tk.Toplevel(self)
        dialog.title('ClockTree7 · 器件模型库')
        dialog.configure(bg='white')
        dialog.transient(self)
        fit_window(dialog,self,(945,645),(580,400))
        frame=ttk.Frame(dialog,padding=12,style='White.TFrame')
        frame.pack(fill='both',expand=True)
        info=profile(self._base_config)
        ttk.Label(frame,text=info['label']+'器件模型',style='Title.TLabel').pack(anchor='w')
        footer=ttk.Frame(frame,style='White.TFrame')
        footer.pack(side='bottom',fill='x',pady=(6,0))
        ttk.Button(footer,text='关闭',command=dialog.destroy).pack(side='right')
        ttk.Label(footer,text='图形和端口位置与 SVG 导出一致。',style='Muted.TLabel').pack(side='left')
        row=scroll_body(frame,(0,6))
        cards=[]
        for index,kind in enumerate(selectable_kinds(self._base_config)):
            card=ttk.Frame(row,style='White.TFrame',padding=(0,0,12,0))
            cards.append(card)
            ttk.Label(card,text=kind_label(kind,self._active_tree_type),style='White.TLabel',font=(self.family,12,'bold')).pack(anchor='w',pady=(0,5))
            ttk.Label(card,text='多输入 · 多输出 · elab 对应关系' if kind=='wire' else '多个独立输入 · 无输出' if kind=='subsystem' else ('多路输入 · 单路输出' if kind=='mux' else '单路输入 · 单路输出'),style='Muted.TLabel').pack(anchor='w',pady=(0,9))
            canvas=tk.Canvas(card,width=1,height=125,bg='#101b2e',highlightthickness=0)
            canvas.pack(fill='x')
            is_reset=self._active_tree_type=='reset'
            node={'id':'u_'+self._active_tree_type+'_'+kind,'module':self._active_tree_type+'_'+kind,'kind':kind,
                  'inputs':(['rst0_ni','rst1_ni'] if kind=='mux' else ['rst_ni']) if is_reset else (['clk0_i','clk1_i'] if kind=='mux' else ['clk_i']),
                  'output':None if kind=='subsystem' else 'rst_no' if is_reset else 'clk_o'}
            if kind=='wire':node.update(inputs=['in0','in1'],output=None,outputs=['out0','out1'],preview=True)
            def paint(event,model=node):
                c=event.widget
                c.delete('all')
                height=node_height(model)
                scale=max(.05,min(1.,(event.width-14)/242,(event.height-12)/height))
                draw_device(c,model,(event.width-242*scale)/2,(event.height-height*scale)/2,242,height,scale,self.family)
            canvas.bind('<Configure>',paint)
        last_columns=[0]
        def arrange(event):
            columns=3 if event.width>=750 else 2
            if columns==last_columns[0]:return
            last_columns[0]=columns
            for col in range(3):
                row.columnconfigure(col,weight=int(col<columns),uniform='cards' if col<columns else '')
            for i,card in enumerate(cards):
                card.grid(row=i//columns,column=i%columns,sticky='nsew',pady=(3,9))
        row.bind('<Configure>',arrange,add='+')
        dialog.bind('<Escape>',lambda e:dialog.destroy())
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
            self.show_config_panel()
        except Exception as error:self.show_error('配置导入失败',error)

    def show_config_panel(self):
        if self.focused_view:
            self.toggle_focus()
        self.show_panel('left')
        self.left_tabs.select(0)

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
        if self.busy or self.graph:self.empty_actions.place_forget()
        else:self.empty_actions.place(relx=.5,rely=.5,y=10,anchor='n')
        if self.busy:
            self.progress.configure(mode='indeterminate')
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate',value=0)

    def load_result(self,path):
        if self.busy:return
        path=Path(path).resolve()
        self._runtime_directory=path.parent
        self._runtime_stamp=None
        bind_gui(path.parent,self.gui_journal)
        self._set_busy('load')
        self._badge('正在载入架构图','busy')
        self.set_status('读取提取结果：{}'.format(path))
        self.gui_journal.emit('begin','load','打开结果与索引',{'result':str(path)})
        def worker():
            try:
                graph=read_graph(path)
                if not all(k in graph for k in ['nodes','edges','summary','config']):
                    raise ValueError('请选择 clocktree.json 或 resettree.json 提取结果。')
                mode=tree_type(graph['config'])
                if not isinstance(mode,str) or mode not in TREE_PROFILES or graph.get('tree_type',mode)!=mode:
                    raise ValueError('结果中的 tree_type 必须与配置一致，并为 clock 或 reset。')
                self.gui_journal.emit('end','load','结果与索引读取完成',{'result':str(path)})
                self.events.put(('loaded',graph,path,None))
            except Exception as error:
                self.gui_journal.fail(error)
                self.events.put(('error','结果打开失败',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def show_graph(self,graph,path=None,graph_token=None):
        self._latest_job_failure=None
        runtime=graph.get('metadata',{}).get('runtime',{})
        if runtime.get('journal_directory') and Path(runtime['journal_directory']).is_dir():
            self._runtime_directory=Path(runtime['journal_directory'])
            self._runtime_stamp=None
        elif path:
            self._runtime_directory=Path(path).parent
            self._runtime_stamp=None
        if path:bind_gui(Path(path).parent,self.gui_journal)
        self.diagram.runtime_context={'result':str(path) if path else None,'backend_run_id':runtime.get('run_id')}
        self.set_config(graph['config'], refresh_colors=False)
        self.graph=graph
        self.empty_actions.place_forget()
        self.graph_file=Path(path) if path else None
        memory=graph.get('metadata',{}).get('memory_protection',{})
        self.memory_status.set(('Verdi 阶段峰值 {} GiB · 重启 {} 次'.format(memory.get('peak_rss_gib','—'),memory.get('restart_count',0)) if memory.get('enabled') else '本次运行未启用 RSS 监测') if memory else '等待提取 · RSS 监测在 Linux 上启用')
        if memory.get('last_checkpoint',{}).get('completed_modules') is not None:
            self.memory_status.set(self.memory_status.get()+'\n已提交 {} 条 module 查找断点'.format(memory['last_checkpoint']['completed_modules']))
        if memory.get('trace_timeout_count'):
            self.memory_status.set(self.memory_status.get()+'\n{} 个例化 trace 超时 · 超时重启 {} 次'.format(memory['trace_timeout_count'],memory.get('timeout_restart_count',0)))
        self._graph_token=graph_token or uuid.uuid4().hex
        self.project.set('{}  /  {}'.format(graph['config']['design'].get('top','DESIGN'),self.graph_file.parent.name if self.graph_file else '导入结果'))
        for key,value in self.stats.items():value.set('{:,}'.format(graph['summary'].get(key,0)))
        self.unresolved_number.configure(fg='#a24a12' if graph['summary'].get('unresolved') else INK)
        self.unresolved_caption.configure(image=glyph(self,'issue','amber' if graph['summary'].get('unresolved') else 'slate'))
        self._analysis_state()
        summary=graph['summary']
        input_boundaries = configured_input_boundaries(graph['config'])
        undriven=summary.get('undriven_inputs',0)
        if summary['connectivity_complete'] and undriven:
            self._badge('追踪完成 · {:,} 个输入无驱动'.format(undriven),'warn')
        elif summary['connectivity_complete'] and summary.get('topology_valid',True):
            if input_boundaries:
                self._badge('按已选层次边界解析 · {} 条入口'.format(summary.get('input_boundary_connections',0)),'pass')
            else:
                self._badge('目标结构连接已解析 · 未核验普通负载' if summary.get('extraction_mode')=='target' else ('范围内连接已解析 · {} 处边界'.format(summary.get('scope_boundaries',0)) if summary.get('scope_limited',bool(graph['config'].get('trace_scope','').strip())) else '全量连接已解析'),'pass')
        elif summary['connectivity_complete']:self._badge('连接完整 · 结构存在异常','warn')
        else:self._badge('{} 个例化 trace 超时 · 连接不完整'.format(summary['trace_timeout_instances']) if summary.get('trace_timeout_instances') else '存在未解析断点 / 诊断','warn')
        components=graph['components'][:500]
        self.component_ids=[None]+[c['id'] for c in components]
        values=['全部'+profile(graph['config'])['network']]+['网络 {} · {} 实例'.format(c['id']+1,len(c['nodes'])) for c in components]
        self.component_combo.configure(values=values)
        self.component_combo.current(0)
        self.diagram.load(graph)
        self.refresh_div_colors()
        preferences=self.ui_state.get(graph['config'])
        self.diagram.layout_mode=preferences.get('layout','branch')
        self.layout_var.set('按层排列' if self.diagram.layout_mode=='layers' else '分支紧凑布局')
        self.references_var.set(preferences.get('subsystem_references',True))
        self.diagram.model.subsystem_references=self.references_var.get()
        if preferences:self.diagram.rebuild()
        self.search_entry.configure(values=preferences.get('searches',[]))
        self.match_query,self.match_index='',-1
        self.match_label.configure(text='路径 / module')
        self.selected_object=None
        self.related_edges={}
        self.node_actions.pack_forget()
        self._fill_connections([],None)
        self.expand_button.configure(state='disabled')
        self.inspector_tabs.select(0)
        self._properties('全网提取概况', [('设计顶层',graph['config']['design'].get('top','')),
            ('端口追踪范围',graph['config'].get('trace_scope') or '设计顶层（兼容模式）'),
            *([('配置的边界层次', '\n'.join('L{} · {}'.format(layer, scope) for scope, layer in input_boundaries.items()))]
              if input_boundaries else []),
            ('范围出口','{} 个实例；出口不计为分支末端'.format(summary.get('scope_output_instances',0))),
            ('覆盖规模','{:,} 个结构 · {:,} 条端口连线'.format(summary['instances'],summary['edges'])),
            ('器件类型',' / '.join('{} {}'.format(kind_label(k,self._active_tree_type),v) for k,v in (graph.kind_counts() if isinstance(graph,IndexedGraph) else Counter(n.get('kind') for n in self.diagram.model.original.values())).items())),
            ('结果读取','磁盘索引 · 概览优先 · 展开读取明细' if isinstance(graph,IndexedGraph) else '直接读取'),
            ('分支末端','  /  '.join('L{}: {}'.format(k,v) for k,v in sorted(summary.get('terminal_layers',{}).items()))),
            ('提取方式',EXTRACTION_MODES[summary.get('extraction_mode','full')]),
            ('完整性','全部关注输入反查驱动；末端表示没有已配置的下游器件。普通负载未核验。' if summary.get('extraction_mode')=='target' else '全部关注输入追驱动，全部关注输出追负载。连接保留双向 NPI 证据。'),
            ('实际运行',graph.get('metadata',{}).get('output_directory','导入结果'))])
        self._replace_text(self.diag_text,'{} 个诊断 · {} 个未解析端点 · {} 个输入无驱动\n\n打开完整问题表，可筛选、搜索、分页、定位和导出全部记录。\n正常范围边界不作为未解析错误。'.format(len(graph.get('diagnostics',[])),summary.get('unresolved',0),undriven))
        self.refresh_nodes()
        self.refresh_runs()
        self.set_status('架构图就绪 · {:,} 实例 / {:,} 连接 · 滚轮缩放，拖动画布平移'.format(summary['instances'],summary['edges']))
        self._schedule_session_save()

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
        command=[sys.executable,'-m','clocktree','extract','--config',str(path),'--out',str(directory)]
        self._launch_extraction(directory,result_path,command)

    def _launch_extraction(self,directory,result_path,command,resuming=False):
        self._latest_job_failure=None
        self.job_dir=directory
        if self._session_store:
            write_json(self._session_store.directory/'active-job.json',{'directory':str(directory),'command':command})
        write_json(self.gui_journal.directory/'active-job.json',{'directory':str(directory),'command':command})
        bind_gui(directory,self.gui_journal)
        self._runtime_directory=directory
        self._runtime_state={}
        self._runtime_stamp=None
        self._log_mtime=None
        self.cancel_event.clear()
        self._set_busy('extract')
        self._badge('正在校验断点与原输入' if resuming else '正在解析 elab · 提取全网','busy')
        self.set_status('正在校验原任务输入并准备恢复，随后继续未提交的工作。' if resuming else '正在执行 NPI，界面可继续缩放和查看已有架构图。')
        self.show_log(True)
        self.log_source.set('阶段日志')
        self._log_filter=None
        self._replace_text(self.log_text,('恢复上次任务：' if resuming else '启动真实 Verdi NPI 提取：')+str(directory)+'\n')
        write_json(directory/'gui-job.json',{'state':'running','command':command,'workspace':str(ROOT)})
        def worker():
            try:
                with (directory/'cli.log').open('a' if resuming else 'w',encoding='utf-8') as stream:
                    if self.cancel_event.is_set():
                        self.events.put(('cancelled',directory))
                        return
                    self.process=subprocess.Popen(command,cwd=str(ROOT),stdout=stream,stderr=subprocess.STDOUT,start_new_session=os.name!='nt')
                    if self.cancel_event.is_set():self._terminate_process()
                    code=self.process.wait()
                self.process=None
                if self.cancel_event.is_set():self.events.put(('cancelled',directory))
                elif code in (0,2) and result_path.is_file():
                    self.gui_journal.emit('begin','load','读取新生成结果',{'result':str(result_path)})
                    graph=read_graph(result_path)
                    self.gui_journal.emit('end','load','新结果索引已打开',{'result':str(result_path)})
                    self.events.put(('loaded',graph,result_path,directory))
                else:
                    location=diagnose(directory)
                    detail='最后阶段：{}\n{}\n'.format(location.get('label','尚未进入后端'),location.get('context',{}))+tail_text(directory/'cli.log',6000)
                    self.events.put(('error','提取失败',detail or '请查看运行目录中的 verdi.log。'))
            except Exception as error:
                self.process=None
                if self.gui_journal.state().get('stage')=='load':self.gui_journal.fail(error)
                self.events.put(('error','提取失败',str(error)))
        threading.Thread(target=worker,daemon=True).start()

    def resume_extraction(self,info,limit):
        if self.busy:return
        manifest=info.get('manifest')
        if not manifest:return
        self._remember_workspace()
        self.set_config(copy.deepcopy(manifest['config']))
        self.memory_limit_var.set(str(limit))
        directory=Path(manifest['output'])
        command=[sys.executable,'-m','clocktree','resume','--run',manifest['journal'],'--memory-limit-gib',str(limit)]
        self._launch_extraction(directory,directory/result_filename(manifest['config']),command,resuming=True)

    def check_previous_run(self):
        if self._destroying or self.busy:return
        def worker():
            pointer=runtime_json(self._session_store.directory/'active-job.json',{}) if self._session_store else {}
            if not pointer and self._previous_gui_issue:
                pointer=runtime_json(Path(self._previous_gui_issue['directory'])/'active-job.json',{})
            directory=Path(pointer['directory']) if pointer.get('directory') else None
            if directory is None:
                candidates=[p for p in self.runs.iterdir() if p.is_dir() and (p/'run-status.json').is_file()]
                if candidates:directory=max(candidates,key=lambda p:(p/'run-status.json').stat().st_mtime)
            info=inspect_recovery(directory) if directory else {'diagnosis':{},'resumable':False}
            if self._previous_gui_issue or info.get('diagnosis',{}).get('state') in ('interrupted','failed','cancelled') or info.get('running'):
                self.events.put(('recovery_notice',info))
        threading.Thread(target=worker,daemon=True).start()

    def recover_history(self):
        if self.busy:return
        selection=self.runs_tree.selection()
        if not selection:return
        self.show_recovery(inspect_recovery(self.runs/selection[0]))

    def show_recovery(self,info):
        if self.recovery_dialog and self.recovery_dialog.winfo_exists():self.recovery_dialog.destroy()
        self.recovery_dialog=RecoveryDialog(self,info,self._previous_gui_issue)
        self._badge('发现历史中断 · 可查看日志与断点','warn')

    def monitor_extraction(self,info):
        if self.busy:return
        directory=Path(info['directory'])
        self.job_dir=directory;self._runtime_directory=directory;self._runtime_stamp=None
        self._set_busy('monitor');self.show_log(True)
        self.set_status('正在观察原后端任务；不会重复启动 Verdi。')
        def worker():
            while True:
                if self._destroying:return
                current=inspect_recovery(directory)
                report=current['diagnosis']
                if report.get('state') in ('finished','attention'):break
                # inspect_recovery reads two atomically replaced status
                # snapshots. Completion may fall between them: a missing
                # running flag does not prove a live owner has stopped.
                if not current.get('running') and report.get('owner_alive') is not True:break
                time.sleep(.5)
            path=directory/result_filename(info['manifest']['config'])
            if current['diagnosis'].get('state') in ('finished','attention') and path.is_file():
                try:self.events.put(('loaded',read_graph(path),path,None))
                except Exception as error:self.events.put(('error','打开结果失败',str(error)))
            else:self.events.put(('monitor_stopped',current))
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
                if kind=='session_loaded':
                    self._apply_previous_session(item[1])
                elif kind=='recovery_notice':
                    if not self.busy:self.show_recovery(item[1])
                elif kind=='monitor_stopped':
                    self._set_busy(None);self.show_recovery(item[1])
                elif kind=='session_save_error':
                    if not self._closing:self.set_status('自动恢复记录未能保存：'+item[1])
                elif kind=='div_colors':
                    _,token,model,levels,error = item
                    if token is self._div_color_request and model is self.diagram.model:
                        self._apply_div_colors(model, levels)
                        if error:self.set_status('DIV 来源读取失败：'+error)
                elif kind=='loaded':
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
                        self._latest_job_failure={'code':'fatal','message':item[2],'run_directory':str(self.job_dir)}
                    if not self._closing:self.show_error(item[1],item[2])
                elif kind=='cancelled':
                    self._set_busy(None)
                    write_json(item[1]/'gui-job.json',{'state':'cancelled','raw_evidence_preserved':True})
                    self._badge('提取已取消','warn')
                    self.set_status('当前任务已取消，运行目录和原始日志已保留。')
                    self.refresh_runs()
                elif kind=='stage_log':
                    if item[3]==self._log_token:
                        self._replace_text(self.log_text,item[1])
                        self.log_text.see('end')
                        self.log_path.set(item[2])
        except queue.Empty:pass
        if self.job_kind in ('extract','monitor') and self.job_dir:
            memory_file=self.job_dir/'memory.json'
            try:
                stamp=(memory_file.stat().st_mtime,memory_file.stat().st_size)
                if stamp!=getattr(self,'_memory_mtime',None):
                    self._memory_mtime=stamp
                    memory=read_json(memory_file)
                    used=memory.get('current_rss_gib')
                    self.memory_status.set('RSS {} GiB / {} GiB · 重启 {} 次'.format(used if used is not None else '—',memory.get('limit_gib','—'),memory.get('restart_count',0)))
                    if memory.get('trace_timeout_count'):
                        self.memory_status.set(self.memory_status.get()+'\n{} 个例化 trace 超时 · 超时重启 {} 次'.format(memory['trace_timeout_count'],memory.get('timeout_restart_count',0)))
                    module=memory.get('last_checkpoint',{}).get('last_module')
                    if module:self.memory_status.set(self.memory_status.get()+'\n断点 {} · L{} {}'.format(module['index'],module['layer'],module['module'][-30:]))
            except (OSError,ValueError,KeyError):pass
            # Progress is independent of the user's selected log source.
            value=tail_text(self.job_dir/'verdi.log',16000)
            progress=re.findall(r'CT7_TRACE (\d+)/(\d+)',value)
            if progress:
                done,total=map(int,progress[-1])
                self.progress.stop()
                self.progress.configure(mode='determinate',value=100*done/max(1,total))
                self._badge('正在追踪端口 · {:,} / {:,}'.format(done,total),'busy')
            else:
                modules=re.findall(r'CT7_MODULE_CHECKPOINT index=(\d+)/(\d+)',value)
                if modules:
                    done,total=map(int,modules[-1])
                    self._badge('模块查找已提交断点 · {} / {}'.format(done,total),'busy')
        self._update_runtime_view()
        if self.log_visible and self._log_filter is None and not self._log_diagnosis:
            log=self._selected_log()
            try:
                stamp=(str(log),log.stat().st_mtime,log.stat().st_size)
                if stamp!=self._log_mtime:
                    self._log_mtime=stamp
                    self._replace_text(self.log_text,tail_text(log))
                    self.log_text.see('end')
                    self.log_path.set(str(log))
            except OSError:pass
        if self._closing and (not self.busy or self.job_kind=='session_save'):
            self._finish_close()
            if self._destroying:return
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
                    status='完整' if s['connectivity_complete'] and s.get('topology_valid',True) and not s.get('undriven_inputs') else '诊断'
                    count=s['instances']
                elif (directory/'gui-job.json').is_file():
                    job=read_json(directory/'gui-job.json')
                    status={'cancelled':'已取消','failed':'失败','running':'运行中'}.get(job.get('state'),'—')
                    count='—'
                elif (directory/'run-status.json').is_file():status='阶段记录';count='—'
                else:continue
                runtime=runtime_json(directory/'run-status.json',{})
                current=runtime.get('state')
                if current in ('running','restarting') and alive(runtime.get('owner')) is False:current='interrupted'
                if current in ('running','restarting','failed','interrupted','cancelled'):
                    status={'running':'运行中','restarting':'重启中','failed':'失败','interrupted':'中断','cancelled':'已取消'}[current]
                    status+=' · '+LABELS.get(runtime.get('stage'),'阶段日志')
                self.runs_tree.insert('','end',iid=directory.name,text=directory.name,values=(count,status))
            except (OSError,ValueError,KeyError):continue

    def open_history(self):
        selection=self.runs_tree.selection()
        if not selection:return
        directory=self.runs/selection[0]
        result=directory/result_filename({'tree_type':self._active_tree_type})
        runtime=runtime_json(directory/'run-status.json',{})
        interrupted=runtime.get('state') in ('failed','cancelled','interrupted') or (
            runtime.get('state') in ('running','restarting') and alive(runtime.get('owner')) is False)
        if result.is_file() and not interrupted:self.load_result(result)
        else:
            self._runtime_directory=directory
            self._runtime_stamp=None
            self._update_runtime_view()
            self.show_run_diagnosis()
            self.set_status('该记录未生成完整结果：{}'.format(directory))
            if not self.busy:self.show_recovery(inspect_recovery(directory))

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
        if not next_match and query:
            self.ui_state.search(self.graph['config'],query)
            self.search_entry.configure(values=self.ui_state.get(self.graph['config']).get('searches',[]))
        matches=self.diagram.model.find(query)
        if not matches:
            self.match_label.configure(text='没有匹配实例')
            return
        self.match_index=(self.match_index+1)%len(matches) if next_match and query==self.match_query else 0
        self.match_query=query
        self.match_label.configure(text='{} / {} 匹配'.format(self.match_index+1,len(matches)))
        self.diagram.locate(matches[self.match_index])

    def clear_search_history(self):
        config=self.graph['config'] if self.graph else self.get_config(False)
        self.ui_state.update(config,searches=[]);self.search_entry.configure(values=[])

    def change_layout(self,event=None):
        if not self.graph:return
        self.diagram.layout_mode='layers' if self.layout_var.get()=='按层排列' else 'branch'
        self.diagram.model.subsystem_references=self.references_var.get()
        self.ui_state.update(self.graph['config'],layout=self.diagram.layout_mode,subsystem_references=self.references_var.get())
        self.diagram.rebuild()

    def open_issues(self):
        extra=[]
        directory=getattr(self,'_runtime_directory',None)
        if directory:
            report=diagnose(directory)
            if report.get('state') in ('failed','interrupted'):
                failure=report.get('last_failure') or {}
                extra.append({'code':'fatal','message':'{}：{}'.format(report.get('label','运行阶段'),
                    failure.get('detail') or report.get('detail') or failure.get('error') or '任务未正常结束，请查看阶段日志'),
                    'run_directory':str(directory),'stage':report.get('stage')})
        if not extra and getattr(self,'_latest_job_failure',None):extra.append(self._latest_job_failure)
        from .issue_view import IssueDialog
        IssueDialog(self,extra)

    def locate_issue_object(self,instance,pin=''):
        if self.diagram.model and instance in self.diagram.model.original:
            self.diagram.issue_port=(instance,pin)
            self.diagram.locate(self.diagram.model.original[instance],pin=pin or None)
            self.set_status('问题端口：{}.{}'.format(instance,pin))

    def locate_issue(self,row):
        if row[5] and self.diagram.model and row[5] in self.diagram.model.original:self.locate_issue_object(row[5],row[6]);return
        detail=json.loads(row[-1]);layer=detail.get('layer')
        if layer and 1<=int(layer)<=len(self.layers):
            self.show_config_panel()
            self.select_layer(int(layer));self.set_status('已定位 L{} 规则：{}'.format(layer,detail.get('module','')))
            for key in self.rules_tree.get_children():
                if self.rules_tree.item(key,'text')==detail.get('module'):
                    self.rules_tree.selection_set(key);self.rules_tree.see(key);break
        else:
            if detail.get('run_directory'):self._runtime_directory=Path(detail['run_directory']);self._runtime_stamp=None
            self.show_log(True);self.log_source.set('阶段日志');self.set_status(row[8]+' · '+row[9])

    def jump_neighbour(self,direction):
        from .navigation import jump_neighbour
        jump_neighbour(self,self.selected_object,direction)

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
        self.layout_var.set('按层排列' if info.get('layout_mode')=='layers' else '分支紧凑布局')
        if hasattr(self,'component_ids'):
            if info['component'] not in self.component_ids:
                self.component_ids.append(info['component'])
                values=list(self.component_combo['values'])+['网络 {}'.format(info['component']+1)]
                self.component_combo.configure(values=values)
            self.component_combo.current(self.component_ids.index(info['component']))
        self.zoom_label.configure(text='{:.0f}%'.format(info['zoom']*100))
        summary='{:,} 图元 · 当前 {:,} / 全量 {:,} 实例'.format(info['boxes'],info['actual'],info['total'])
        if self.view_label.get()!=summary:
            self.view_label.set(summary)
            self._size_analysis_tools()
        scope=info['scope']
        label=('实例邻域 / '+info['focus'][-70:]) if info.get('focus') else (('局部范围 / '+scope[-70:]) if scope else '全部层次 / 全量关注结构')
        if info.get('pages',1)>1:
            label+=' · 第 {}/{} 组 · {} 条跨组连接可在属性中查看'.format(info['page']+1,info['pages'],info.get('external_edges',0))
            self.view_prev.pack(side='left',before=self.scope_text); self.view_next.pack(side='left',before=self.scope_text)
            self.view_prev.configure(state='normal' if info['page'] else 'disabled')
            self.view_next.configure(state='normal' if info['page']+1<info['pages'] else 'disabled')
        else:
            self.view_prev.pack_forget(); self.view_next.pack_forget()
        self.scope_label.set(label)

    def _properties(self,title,fields):
        text=self.prop_text
        text.configure(state='normal')
        text.delete('1.0','end')
        if title:text.insert('end',title+'\n','heading')
        width=text.winfo_width()-self.property_tab-18
        for label,value in fields:
            value=str(value)
            if ('\n' not in value and len(value)<48 and
                    self.property_font.measure(label)<self.property_tab and self.property_font.measure(value)<width):
                text.insert('end',label+'\t','inline-label')
                text.insert('end',value+'\n','inline-value')
                continue
            text.insert('end',label+'\n','label')
            text.insert('end',value+'\n','value')
        text.configure(state='disabled')
        text.yview_moveto(0)

    def inspect_node(self,node):
        if not self.focused_view:self.show_panel('right')
        self.node_actions.pack(fill='x',before=self.property_panes)
        level=node.get('layer')
        kind=node.get('kind')
        from .div_sources import div_source_level
        look=device_style(kind,div_source_level(node,self.diagram.model.div_source_levels))
        category='RTL 层次聚合' if node.get('aggregate') else kind_label(kind,self._active_tree_type)
        if kind=='div' and look.get('short')!='DIV':category=look['short']
        self.identity_type.configure(text='  ·  '.join(filter(None,(category,'L{}'.format(level) if level is not None else ''))))
        self.identity_stripe.configure(bg=look['color'])
        real_path=node.get('reference_of') or node.get('path') or node['id']
        self.identity_name.configure(text=real_path.rsplit('.',1)[-1])
        if node.get('aggregate') or node.get('kind')=='subsystem':
            self.expand_button.pack(fill='x',pady=(2,4))
        else:self.expand_button.pack_forget()
        if node.get('kind') in INPUT_SOURCE_KINDS:
            from .l1_inputs import describe
            self.selected_object=node
            for button in (self.expand_button,self.upstream_button):button.configure(state='disabled')
            extra=[e for e in self.diagram.model.edges if e['source']==node['id']]
            self.downstream_button.configure(text='下游 ({})'.format(len(extra)),state='normal' if extra else 'disabled')
            self._properties(None,describe(node))
            self._fill_connections(extra,{node['id']})
            self.inspector_tabs.select(0)
            return
        if node.get('reference_of'):
            node=dict(node,id=node['reference_of'])
        self.selected_object=node
        self.expand_button.configure(state='normal' if node.get('aggregate') or node.get('kind')=='subsystem' else 'disabled',
                                     text='展开 / 收起总线各位' if node.get('kind')=='subsystem' else '展开此 RTL 层次')
        if node.get('aggregate'):
            fields=[('RTL 范围',node['path']),('显示类型','层次聚合（不是新增的 RTL 实例）'),
                    ('归属层','L{}'.format(node['layer'])),('实际实例数',len(node['members'])),
                    ('Module 分布','\n'.join('{} × {}'.format(k,v) for k,v in node['module_counts'].items())),
                    ('器件类型分布',' / '.join('{} {}'.format(kind_label(k,self._active_tree_type),v) for k,v in node.get('kind_counts',{}).items())),
                    ('内部真实连线',node['internal_edges']),('根 / 末端','{} / {}'.format(node['root_count'],node['terminal_count']))]
            if isinstance(self.graph,IndexedGraph):
                members=node['members']
                edges=self.diagram.model.connections_for(node)
            else:
                members=set(node['members'])
                edges=[e for e in self.graph['edges'] if (e['source'] in members)!=(e['target'] in members)]
        else:
            boundaries=self.graph.boundaries_for(node['id']) if isinstance(self.graph,IndexedGraph) else [b for b in self.graph.get('boundaries',[]) if b['instance']==node['id']]
            fields=[('完整实例路径',node['id']),('Module 定义',node['module']),('器件类型',kind_label(node.get('kind'),self._active_tree_type)),('传播层次','L{}'.format(node['layer'])),
                    ('关注输入','\n'.join(node['inputs'])),('关注输出','\n'.join(output_ports(node)) or '无 · 子系统输入终点'),
                    ('输入口例化连接',connection_details(node,node['inputs'])),
                    ('输出口例化连接',connection_details(node,output_ports(node)) if output_ports(node) else '无输出口'),
                    *([('穿线内部连接',wire_details(node))] if node.get('kind')=='wire' else []),
                    ('辅助端口例化连接（不参与追踪）',auxiliary_details(node) or '未配置'),
                    ('连接名来源','NPI 例化端口 npiHighConn；表达式由 elab 还原' if 'port_connections' in node else '该历史结果未采集例化连接名；使用新版重新提取后显示'),
                    ('结构状态',' / '.join(x for value,x in [(node.get('trace_timeout'),'实例 trace 超时'),(node.get('root'),'根结构'),(node.get('terminal'),'分支末端'),(node.get('scope_input_boundary'),'范围入口'),(node.get('scope_output_boundary'),'范围出口'),(node.get('has_unresolved_output'),'存在未解析输出')] if value) or '中间结构'),
                    ('范围边界','{} 项，原始证据保留在 raw.jsonl'.format(len(boundaries))),('RTL 来源',node.get('source','—'))]
            if node.get('trace_timeout'):
                timeout=node['trace_timeout']
                fields.insert(4,('trace 超时断点','{} / {} · 阈值 {:g} 秒\n已用 {:g} 秒；仅跳过本例化剩余端口'.format(
                    timeout['pin'],timeout['direction'],timeout['limit_seconds'],timeout['elapsed_seconds'])))
            if node.get('reference_of'):
                from .navigation import neighbour_edges
                edges=neighbour_edges(self.diagram.model,node,'upstream')
                fields.insert(1,('器件引用','同一个真实子系统；此引用只显示当前分支相关输入。'))
            else:edges=self.diagram.model.incoming[node['id']]+self.diagram.model.outgoing[node['id']]
            members={node['id']}
        from .l1_inputs import neighbours as l1_neighbours,CombinedEdges
        extra=l1_neighbours(self.diagram.model,node,'upstream') if not node.get('aggregate') else []
        if extra:edges=CombinedEdges(edges,extra)
        for direction,button,endpoint in [('upstream',self.upstream_button,'source'),('downstream',self.downstream_button,'target')]:
            if node.get('kind')=='wire' and node.get('_focus_pin'):
                from .navigation import neighbour_edges
                count=len({e[endpoint] for e in neighbour_edges(self.diagram.model,node,direction)})
            elif node.get('reference_of'):count=1 if direction=='upstream' and node.get('reference_source') else 0
            elif node.get('aggregate'):count=0
            elif isinstance(self.graph,IndexedGraph):
                match='target' if direction=='upstream' else 'source'
                count=self.graph.store.connection().execute('SELECT COUNT(DISTINCT '+endpoint+') FROM edges WHERE '+match+'=?',(node['id'],)).fetchone()[0]
            else:count=len({e[endpoint] for e in (self.diagram.model.incoming[node['id']] if direction=='upstream' else self.diagram.model.outgoing[node['id']])})
            if direction=='upstream' and not (node.get('kind')=='wire' and node.get('_focus_pin')):count+=len({e['source'] for e in extra})
            button.configure(text='{} ({})'.format('上游' if direction=='upstream' else '下游',count),state='normal' if count else 'disabled')
        self._properties(None,fields)
        self._fill_connections(edges,members)
        self.inspector_tabs.select(0)

    def _fill_connections(self,edges,members=None):
        self._connection_rows,self._connection_members,self._connection_offset=edges,members,0
        self.connection_page(0)

    def connection_page(self,delta):
        edges=getattr(self,'_connection_rows',[])
        members=getattr(self,'_connection_members',None)
        total=len(edges)
        visible=str(self.links_pane) in tuple(map(str,self.property_panes.panes()))
        if total and not visible:self.property_panes.add(self.links_pane,weight=2)
        elif not total and visible:self.property_panes.forget(self.links_pane)
        self._connection_offset=max(0,min(max(0,((total-1)//500)*500),getattr(self,'_connection_offset',0)+delta*500))
        self.connections.delete(*self.connections.get_children())
        self.related_edges={}
        unique={e['id']:e for e in edges[self._connection_offset:self._connection_offset+500]}
        for i,edge in enumerate(unique.values()):
            upstream=members and edge['target'] in members
            other=edge['source'] if upstream else edge['target']
            label='.'.join(other.split('.')[-2:])
            if upstream and edge.get('input_boundary'):
                label='L{} · {}'.format(edge['boundary_layer'],edge['source_pin'])
            key=str(i)
            self.related_edges[key]=edge
            self.connections.insert('','end',iid=key,text=label,values=('上游' if upstream else '下游',edge['source_pin'] if upstream else edge['target_pin']))
        self.connection_title.configure(text='连接 {:,}–{:,} / {:,}'.format(self._connection_offset+1 if total else 0,min(total,self._connection_offset+500),total))
        self.connection_prev.configure(state='normal' if self._connection_offset else 'disabled')
        self.connection_next.configure(state='normal' if self._connection_offset+500<total else 'disabled')

    def inspect_edges(self,display_edges):
        if any(e.get('input_boundary') for e in display_edges):
            edges=list({e['id']:e for e in display_edges if e.get('input_boundary')}.values())
        elif isinstance(self.graph,IndexedGraph):
            edges=self.diagram.model.inspector_edges(display_edges)
        else:
            unique={}
            for edge in display_edges:
                for item in self.diagram.model.actual_edges(edge):unique[item['id']]=item
            edges=list(unique.values())
        if not edges:return
        if not self.focused_view:self.show_panel('right')
        self.node_actions.pack_forget()
        self.selected_object=None
        self.expand_button.configure(state='disabled')
        self._fill_connections(edges)
        self._properties('实际端口连接', [('共用当前线段','{} 条真实端口连接'.format(len(edges))),
                                        ('查看依据','在下面列表选择具体连线，或在“连线证据”页查看第一条。'),
                                        ('数据来源','Language Model 输入驱动证据' if self.graph['summary'].get('extraction_mode')=='target' else '驱动 / 负载双向 NPI 静态网络追踪')])
        self._edge_evidence(edges[0])
        self.inspector_tabs.select(1)

    def _edge_evidence(self,edge):
        from .devices import connection_label
        from .l1_inputs import lookup_node
        source=lookup_node(self.diagram.model,edge['source']);target=self.diagram.model.nodes.get(edge['target']) or lookup_node(self.diagram.model,edge['target'])
        directions=edge.get('evidence',{})
        lines=['{}.{}（{}）'.format(edge['source'],edge['source_pin'],connection_label(source,edge['source_pin'])),
               '  → {}.{}（{}）'.format(edge['target'],edge['target_pin'],connection_label(target,edge['target_pin'])),'']
        if edge.get('input_boundary'):
            lines=[source_title(source)+'：'+source['input_entry']['signal'],
                   '  → '+edge['target']+'.'+edge['target_pin'],source_basis(source)]
        elif directions.get('load') and directions.get('driver'):
            lines.append('验证结论：输入反查与输出负载双向证据一致。')
        elif directions.get('driver'):
            lines.append('验证结论：输入反查已找到此输出。')
            lines.append('输出全负载方向：未执行。' if self.graph['summary'].get('extraction_mode')=='target' else '输出方向缺少证据，请查看问题表。')
        else:lines.append('验证结论：缺少输入反查证据，请查看问题表。')
        lines.append('规则层次：L{} → L{}{}'.format(source['layer'],target['layer'],
                     ' · 逆序层连接' if edge['layer_delta']<0 and not edge.get('input_boundary') else ''))
        if edge['source'].rsplit('.',1)[0]!=edge['target'].rsplit('.',1)[0]:lines.append('连接跨越不同 RTL 父层；展开原始证据可回查跨层及别名映射。')
        from .evidence import mapping_excerpt
        mapping=mapping_excerpt(edge)
        if mapping:lines+=['',mapping]
        self._inspected_edge=edge
        if not self.raw_evidence_var.get():
            self._replace_text(self.evidence_text,'\n'.join(lines));return
        summary_lines=lines+['','原始证据（保留全部 NPI 类型及逐步路径）','']
        lines=['实际端口连接','═'*24,'来源实例',edge['source'],'输出口  '+edge['source_pin'],'',
               '目标实例',edge['target'],'输入口  '+edge['target_pin'],'','层差  {}'.format(edge['layer_delta'])]
        for endpoint,pin_key,label in [('source','source_pin','来源端例化连接'),('target','target_pin','目标端例化连接')]:
            node=lookup_node(self.diagram.model,edge[endpoint])
            lines+=['',label,connection_details(node,[edge[pin_key]])]
        for direction,label in [('load','输出 → 全部负载'),('driver','输入 ← 实际驱动')]:
            lines+=['','─'*24,label,'']
            if direction=='load' and edge.get('input_boundary'):
                lines.append('层次入口以目标器件的输入反查为依据。')
                continue
            if direction=='load' and self.graph['summary'].get('extraction_mode')=='target':
                lines.append('本次为目标结构提取，未执行输出全负载核验。')
                continue
            evidence=edge.get('evidence',{}).get(direction,[])
            if not evidence:lines.append('此方向没有证据，请查看诊断。')
            for i,step in enumerate(evidence,1):
                lines+=['{}. {}'.format(i,step.get('type','')),step.get('name','')]
                if step.get('net'):lines.append('NET  '+step['net'])
                lines.append('')
        self._replace_text(self.evidence_text,'\n'.join(summary_lines+lines))
        self._inspected_edge=edge

    def toggle_raw_evidence(self):
        if getattr(self,'_inspected_edge',None):self._edge_evidence(self._inspected_edge)

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
        direction='upstream' if selected and edge['target']==selected.get('reference_of',selected.get('id')) else 'downstream'
        self.diagram.locate_edge(edge,direction)

    def expand_selected(self):
        if self.selected_object:self.diagram.expand(self.selected_object)

    def open_branch(self,node,source_model=None):
        model=source_model or self.diagram.model
        if model is None:return
        if node.get('kind') in INPUT_SOURCE_KINDS:
            self.set_status('双击该边界的下游器件，可打开对应分支。');return
        if node.get('aggregate'):
            self.set_status('请先展开 RTL 聚合框，再双击具体器件。');return
        from .branch_view import BranchDialog
        return BranchDialog(self,model,node)

    def highlight_path(self,direction):
        count=self.diagram.trace(direction)
        self.set_status('{}已高亮 {:,} 条真实连接。'.format({'upstream':'上游','downstream':'下游','branch':'整个分支'}[direction],count))

    def _replace_text(self,text,value):
        text.configure(state='normal')
        text.delete('1.0','end')
        text.insert('end',value)
        text.configure(state='disabled')

    def show_log(self,visible):
        self.log_visible=visible
        if visible:self.log_frame.pack(fill='x')
        else:self.log_frame.pack_forget()
        self.log_toggle.configure(text='运行阶段与日志 '+('▾' if visible else '▴'))

    def _update_runtime_view(self):
        directory=self._runtime_directory
        files=([directory/'run-status.json'] if directory else [])+[self.gui_journal.status_file]
        stamp=[]
        for path in files:
            try:stamp.append((str(path),path.stat().st_mtime,path.stat().st_size))
            except OSError:stamp.append((str(path),None))
        # One idle redraw per second also notices an owner killed without a final write.
        stamp.append(int(time.monotonic()))
        if stamp==self._runtime_stamp:return
        self._runtime_stamp=stamp
        state=runtime_json(directory/'run-status.json',{}) if directory else {}
        if state.get('state') in ('running','restarting') and alive(state.get('owner')) is False:
            report=diagnose(directory)
            state.update(stage=report.get('stage'),context=report.get('context',{}))
            state['state']='interrupted'
            for key,value in list(state.setdefault('steps',{}).items()):
                if value=='running':state['steps'][key]='pending'
            state.setdefault('steps',{})[stage_node(state.get('stage'))]='interrupted'
        self._runtime_state=state
        gui_state=self.gui_journal.state()
        if self.graph is None and not state and not self.busy and gui_state.get('state')=='finished':
            gui_state={}
        self.stage_view.update_state(state,gui_state)

    def _selected_log(self):
        source=self.log_source.get()
        if source=='GUI 日志' or (not self._runtime_directory and source=='阶段日志'):
            return self.gui_journal.log_file
        directory=self._runtime_directory or self.job_dir or self.runs
        if source=='阶段日志':return directory/'run.log'
        output=directory.parent.parent if directory.parent.name=='.clocktree-runs' else directory
        return output/('verdi.log' if source=='Verdi 日志' else 'cli.log')

    def show_stage_log(self,stage=None):
        self.show_log(True)
        self._log_filter=stage
        self._log_diagnosis=False
        self._log_token+=1
        token=self._log_token
        if stage:self.log_source.set('GUI 日志' if stage=='display' else '阶段日志')
        log=self._selected_log()
        self._log_mtime=None
        if not stage:
            self._replace_text(self.log_text,tail_text(log) or '尚无日志：'+str(log))
            self.log_text.see('end')
            self.log_path.set(str(log))
            return
        self._replace_text(self.log_text,'正在读取 {} 阶段记录…'.format(LABELS.get(stage,stage)))
        def worker():
            from collections import deque
            lines=deque(maxlen=160)
            events=log.with_name(log.stem+'-events.jsonl')
            try:
                with events.open(encoding='utf-8') as stream:
                    for line in stream:
                        if token!=self._log_token:return
                        try:event=json.loads(line)
                        except ValueError:continue
                        if stage_node(event.get('stage'))==stage:
                            lines.append('{} [{}] {} {} {}'.format(event.get('timestamp',event.get('time_ms','')),
                                event.get('kind',''),LABELS.get(event.get('stage'),event.get('stage')),
                                event.get('detail',''),json.dumps(event.get('context',{}),ensure_ascii=False)))
            except OSError:pass
            self.events.put(('stage_log','\n'.join(lines) or '该阶段尚无记录。',str(events)+' · 最近 160 条（快照）',token))
        threading.Thread(target=worker,daemon=True).start()

    def show_run_diagnosis(self):
        self.show_log(True)
        self._log_token+=1
        self._log_diagnosis=True
        self._log_filter=None
        report=diagnose(self._runtime_directory) if self._runtime_directory else {}
        report['current_gui']=diagnose_gui(self.gui_journal.directory)
        previous=runtime_json(self.gui_journal.directory.parent/'previous.json',{})
        if previous.get('journal_directory'):
            issue=diagnose_gui(previous['journal_directory'])
            if issue.get('state') in ('interrupted','failed'):report['previous_gui_issue']=issue
        lines=['阶段诊断 · 最后进入 / 完成 / 断点 / 进程退出信息',
               '后端：{} · {}'.format(report.get('label','尚未运行'),report.get('state','—')),
               '当前对象：'+json.dumps(report.get('context',{}),ensure_ascii=False),
               '日志缺少正常结束标志可定位中断位置；SIGKILL 或闪退本身不能证明 OOM。','',
               json.dumps(report,ensure_ascii=False,indent=2)]
        self._replace_text(self.log_text,'\n'.join(lines))
        self.log_path.set(str(self._runtime_directory or self.gui_journal.directory))

    def toggle_log(self):
        self.show_log(not self.log_visible)

    def open_register_trace(self):
        if not self.graph:
            self.status.set('请先打开或提取架构结果，再追踪其中的辅助端口。')
            return
        from .register_view import RegisterDialog
        try:RegisterDialog(self)
        except ValueError as error:self.set_status(str(error))

    def open_sv_templates(self):
        if not self.graph:
            self.status.set('请先打开时钟树架构结果。');return
        if self.graph.get('tree_type',self.graph['config'].get('tree_type','clock'))!='clock':
            self.status.set('分支 SV 模板当前用于时钟树。');return
        from .sv_view import SVDialog
        try:SVDialog(self)
        except ValueError as error:self.status.set(str(error))

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
        if self.diagram.layout_pending:
            self.set_status('正在计算布局；完成后即可导出 SVG。');return
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
        self._closing=True
        if self.job_kind=='extract':
            self.cancel_job()
        elif self.job_kind=='monitor':
            self._set_busy(None);self._finish_close()
        elif self.busy:
            pass
        else:self._finish_close()

    def destroy(self):
        if self._destroying:return
        self._destroying=True
        if self._session_writer:
            if not self._close_save_started:self._save_session_now()
            self._session_writer.close()
        self.gui_journal.emit('end','session','历史配置与结果保存流程结束')
        self.gui_journal.emit('finished','display','GUI 正常关闭',clean_shutdown=True)
        for timer in self.tk.splitlist(self.tk.call('after','info')):
            # Native ttk timers contain Tcl command lists on Tk 8.5, which
            # Python 3.6's after_cancel cannot delete as Python callbacks.
            self.tk.call('after','cancel',timer)
        super().destroy()

    def report_callback_exception(self,exc,value,tb):
        self.gui_journal.fail(value)
        super().report_callback_exception(exc,value,tb)


def run_gui(runs=None,result=None,config=None,remember=True):
    app=ClockTreeApp(runs=runs,result=result,config=config,remember=remember)
    app.mainloop()
