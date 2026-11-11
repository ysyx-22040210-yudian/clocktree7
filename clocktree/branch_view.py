"""Non-modal, independent Tk canvases for a selected device's exact branch."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from .branch import BranchJob, close_branch
from .devices import connection_details, connection_label, kind_label, auxiliary_details, wire_details
from .profiles import output_ports
from .tkdiagram import ClockDiagram


class BranchDialog(tk.Toplevel):
    def __init__(self, app, source_model, node):
        super().__init__(app)
        self.app, self.source_model = app, source_model
        self.seed = dict(node)
        self.graph = None
        self.selected_object = None
        self.closed = False
        self._poll_id = None
        self._initial_selection = True
        self._matches, self._match_index = [], -1
        self.family = app.family
        instance = node.get('reference_of', node['id'])
        mode = '复位' if source_model.tree_type == 'reset' else '时钟'
        self.title('{}分支 · {}'.format(mode, instance))
        self.geometry('1320x820'); self.minsize(960, 580)
        self.configure(bg='#eef2f8')
        head = ttk.Frame(self, padding=(14, 12))
        head.pack(fill='x')
        ttk.Label(head, text='独立{}分支画布'.format(mode), font=(self.family, 14, 'bold')).pack(anchor='w')
        ttk.Label(head, text=instance, wraplength=1200).pack(anchor='w', pady=(5, 0))
        caption = '全部上游 + 全部下游 · 不包含上游其他旁支 · 仅使用已提取的真实连接'
        if node.get('reference_of'):
            caption += ' · 子系统引用只沿当前输入'
        ttk.Label(head, text=caption).pack(anchor='w', pady=(4, 0))
        toolbar = ttk.Frame(self, padding=(12, 3))
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text='后退', command=lambda:self.diagram.navigate_history()).pack(side='left')
        ttk.Button(toolbar, text='前进', command=lambda:self.diagram.navigate_history(True)).pack(side='left', padx=4)
        ttk.Button(toolbar, text='−', width=3, command=lambda:self.diagram.zoom(1/1.2)).pack(side='left')
        ttk.Button(toolbar, text='+', width=3, command=lambda:self.diagram.zoom(1.2)).pack(side='left', padx=4)
        ttk.Button(toolbar, text='适配分支', command=lambda:self.diagram.fit()).pack(side='left')
        ttk.Button(toolbar, text='返回分支全图', command=lambda:self.diagram.reset_scope()).pack(side='left', padx=4)
        self.mode = tk.StringVar(value='实例明细')
        combo = ttk.Combobox(toolbar, textvariable=self.mode, values=['实例明细', 'RTL 层次概览'], state='readonly', width=14)
        combo.pack(side='left', padx=4); combo.bind('<<ComboboxSelected>>', self.change_mode)
        ttk.Button(toolbar, text='导出当前视图 SVG', command=self.export_svg).pack(side='right')
        searchbar = ttk.Frame(self, padding=(12, 5))
        searchbar.pack(fill='x')
        self.search = tk.StringVar()
        entry = ttk.Entry(searchbar, textvariable=self.search)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda event:self.locate_query())
        ttk.Button(searchbar, text='在本分支查找', command=self.locate_query).pack(side='left', padx=5)
        ttk.Button(searchbar, text='下一个', command=lambda:self.locate_query(True)).pack(side='left')
        self.counts = tk.StringVar(value='正在准备分支… 可关闭窗口取消')
        ttk.Label(self, textvariable=self.counts, padding=(14, 5)).pack(fill='x')
        bottom = ttk.Frame(self, padding=(12, 7)); bottom.pack(side='bottom', fill='x')
        self.status = tk.StringVar(value='正在读取此器件的全部上游和下游连接…')
        ttk.Button(bottom, text='关闭分支窗口', command=self.close).pack(side='right')
        ttk.Label(bottom, textvariable=self.status, wraplength=1000).pack(side='left', fill='x', expand=True)
        body = ttk.Panedwindow(self, orient='horizontal'); body.pack(fill='both', expand=True, padx=12, pady=5)
        canvas_area = ttk.Frame(body); inspector = ttk.Frame(body, width=290, padding=10)
        body.add(canvas_area, weight=5); body.add(inspector, weight=1)
        self.diagram = ClockDiagram(canvas_area, self.family, self.inspect_node, self.inspect_edges,
                                    self.view_changed, self.set_status,
                                    on_branch=lambda chosen:app.open_branch(chosen, source_model))
        self.diagram.pack(fill='both', expand=True)
        self.diagram.layout_mode = 'branch'
        ttk.Label(inspector, text='分支内属性与连接', font=(self.family, 11, 'bold')).pack(anchor='w', pady=(0, 6))
        buttons = ttk.Frame(inspector); buttons.pack(fill='x')
        self.upstream = ttk.Button(buttons, text='上游', command=lambda:self.jump_neighbour('upstream'), state='disabled')
        self.upstream.pack(side='left', fill='x', expand=True)
        self.downstream = ttk.Button(buttons, text='下游', command=lambda:self.jump_neighbour('downstream'), state='disabled')
        self.downstream.pack(side='left', fill='x', expand=True, padx=(5, 0))
        self.expand_button = ttk.Button(inspector, text='展开 / 收起总线各位', command=self.expand_selected, state='disabled')
        self.expand_button.pack(fill='x', pady=6)
        pages = ttk.Frame(inspector); pages.pack(fill='x')
        self.prev = ttk.Button(pages, text='上一组', command=lambda:self.diagram.page_view(-1), state='disabled')
        self.prev.pack(side='left', fill='x', expand=True)
        self.next = ttk.Button(pages, text='下一组', command=lambda:self.diagram.page_view(1), state='disabled')
        self.next.pack(side='left', fill='x', expand=True, padx=(5, 0))
        self.details = tk.Text(inspector, width=30, wrap='word', relief='flat', bg='white', fg='#263852',
                               font=(self.family, 10), padx=10, pady=10, state='disabled')
        scroll = ttk.Scrollbar(inspector, command=self.details.yview)
        self.details.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y', pady=(8, 0)); self.details.pack(fill='both', expand=True, pady=(8, 0))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Destroy>', self._destroyed, add='+')
        # No grab: the original canvas and other branch windows remain usable.
        self.job = BranchJob(source_model, self.seed)
        self._poll_id = self.after(30, self._poll)

    def _poll(self):
        self._poll_id = None
        if self.closed:
            return
        if not self.job.done.is_set():
            self._poll_id = self.after(40, self._poll); return
        graph, error = self.job.take()
        if error:
            self.counts.set('分支准备失败'); self.set_status(error); return
        if graph is None:
            self.counts.set('已取消分支准备'); return
        self.graph = graph
        self.diagram.load(graph)
        self.set_status('滚轮缩放 · 拖动平移 · 双击器件可再开分支画布 · 主画布保留')

    def set_status(self, text):
        if not self.closed:
            self.status.set(text)

    def view_changed(self, info):
        if self.graph is None:
            return
        self.mode.set('RTL 层次概览' if info['mode'] == 'hierarchy' else '实例明细')
        self.counts.set('本分支 {:,} 个真实实例 / {:,} 条真实连接 · 当前 {:,} 个图元 · 缩放 {:.0f}% · 第 {}/{} 组{}'.format(
            len(self.graph['nodes']), len(self.graph['edges']), info['boxes'], info['zoom']*100,
            info['page']+1, info['pages'], ' · 正在计算布局…' if self.diagram.layout_pending else ''))
        self.prev.configure(state='normal' if info['page'] else 'disabled')
        self.next.configure(state='normal' if info['page']+1 < info['pages'] else 'disabled')
        if self._initial_selection and not self.diagram.layout_pending:
            self._initial_selection = False
            from .navigation_target import display_node, location_target
            chosen = display_node(self.diagram.model.nodes, location_target(self.seed))
            if chosen:
                self.diagram.select(chosen)

    def change_mode(self, event=None):
        if self.diagram.model:
            self.diagram.mode = 'hierarchy' if self.mode.get() == 'RTL 层次概览' else 'instances'
            self.diagram.rebuild()

    def _details(self, text):
        self.details.configure(state='normal'); self.details.delete('1.0', 'end')
        self.details.insert('end', text); self.details.configure(state='disabled')

    def inspect_node(self, node):
        if node.get('kind')=='l1_input':
            from .l1_inputs import describe
            self.selected_object=node
            for button in (self.upstream,self.expand_button):button.configure(state='disabled')
            self.downstream.configure(state='normal' if self._neighbours('downstream') else 'disabled')
            self._details('\n\n'.join(label+'\n'+str(value) for label,value in describe(node)))
            return
        self.selected_object = node
        self.expand_button.configure(state='normal' if node.get('aggregate') or node.get('kind') == 'subsystem' else 'disabled',
                                     text='展开此 RTL 层次' if node.get('aggregate') else '展开 / 收起总线各位')
        if node.get('aggregate'):
            self.upstream.configure(state='disabled'); self.downstream.configure(state='disabled')
            self._details('{}\n\nRTL 层次聚合\n{} 个分支内真实实例\n双击展开此层次，再选择具体器件。'.format(node['path'], len(node['members'])))
            return
        actual = node.get('reference_of', node['id'])
        for direction, button in (('upstream', self.upstream), ('downstream', self.downstream)):
            available = bool(self._neighbours(direction))
            button.configure(state='normal' if available else 'disabled')
        lines = [actual, '', '{} · L{}'.format(kind_label(node.get('kind'), self.diagram.tree_type), node['layer']),
                 'Module: '+node['module'], '', '输入端口与实际信号', connection_details(node, node['inputs']),
                 '', '输出端口与实际信号', connection_details(node, output_ports(node)) if output_ports(node) else '无 · 子系统输入终点']
        if node.get('kind')=='wire':lines.extend(['','穿线内部连接',wire_details(node)])
        if node.get('reference_of'):
            lines += ['', '器件引用：仅显示此分支相关输入；真实实例不重复计算。']
        if node.get('trace_timeout') or node.get('has_unresolved_output'):
            lines += ['', '此器件存在 trace 超时或未解析状态；缺失路径没有补画。']
        auxiliary = auxiliary_details(node)
        if auxiliary:
            lines += ['', '辅助端口', auxiliary]
        self._details('\n'.join(lines))

    def _neighbours(self, direction):
        node = self.selected_object
        if not node or node.get('aggregate'):
            return []
        if node.get('reference_of'):
            return [node['reference_source']] if direction == 'upstream' and node.get('reference_source') else []
        model = self.diagram.model
        from .l1_inputs import neighbours as l1_neighbours
        if l1_neighbours(model,node,direction):
            from .navigation import neighbour_destinations
            return neighbour_destinations(model,node,direction)
        match, other = ('target', 'source') if direction == 'upstream' else ('source', 'target')
        if hasattr(model, 'store'):
            return [r[0] for r in model.store.connection().execute('SELECT DISTINCT '+other+' FROM edges WHERE '+match+'=? LIMIT 2', (node['id'],))]
        edges = model.incoming[node['id']] if direction == 'upstream' else model.outgoing[node['id']]
        return sorted({edge[other] for edge in edges})

    def jump_neighbour(self, direction):
        from .navigation import jump_neighbour
        jump_neighbour(self, self.selected_object, direction)

    def expand_selected(self):
        if self.selected_object:
            self.diagram.expand(self.selected_object)

    def inspect_edges(self, displayed):
        self.selected_object = None
        self.upstream.configure(state='disabled'); self.downstream.configure(state='disabled')
        self.expand_button.configure(state='disabled')
        from .evidence import mapping_excerpt
        lines = []
        remaining = 30
        for bundle in displayed:
            actual = self.diagram.model.actual_edges(bundle)
            for edge in actual[:remaining]:
                from .l1_inputs import lookup_node
                source = lookup_node(self.diagram.model,edge['source'])
                target = self.diagram.model.nodes.get(edge['target']) or lookup_node(self.diagram.model,edge['target'])
                lines += ['{}.{}（{}）'.format(edge['source'], edge['source_pin'], connection_label(source, edge['source_pin'])),
                          ' → {}.{}（{}）'.format(edge['target'], edge['target_pin'], connection_label(target, edge['target_pin']))]
                evidence = edge.get('evidence', {})
                lines.append('输入反查与输出负载双向核验' if evidence.get('driver') and evidence.get('load') else
                             ('输入反查已证明此连接' if evidence.get('driver') else '历史结果未记录输入反查证据'))
                excerpt = mapping_excerpt(edge)
                if excerpt:
                    lines.append(excerpt)
                lines.append('')
            remaining -= min(remaining, len(actual))
            if remaining <= 0:
                lines.append('当前面板最多显示 30 条连接摘要；放大或展开聚合后可选择具体连线。')
                break
        self._details('\n'.join(lines))

    def locate_query(self, next_match=False):
        if not self.diagram.model:
            return
        query = self.search.get().strip()
        if not next_match or query != getattr(self, '_query', None):
            self._query = query; self._matches = self.diagram.model.find(query); self._match_index = -1
        if not self._matches:
            self.set_status('本分支没有匹配的实例。'); return
        self._match_index = (self._match_index+1) % len(self._matches)
        self.diagram.locate(self._matches[self._match_index])
        self.set_status('本分支匹配 {} / {}'.format(self._match_index+1, len(self._matches)))

    def export_svg(self):
        if not self.diagram.model or self.diagram.layout_pending:
            self.set_status('请等待分支布局完成后导出。'); return
        path = filedialog.asksaveasfilename(parent=self, title='导出当前分支视图', defaultextension='.svg', filetypes=[('SVG', '*.svg')])
        if path:
            try:
                Path(path).write_text(self.diagram.model.svg(), encoding='utf-8')
                self.set_status('已导出当前分支视图：'+path)
            except Exception as error:
                messagebox.showerror('导出失败', str(error), parent=self)

    def close(self):
        self._cleanup()
        self.destroy()

    def _destroyed(self, event):
        if event.widget is self:
            self._cleanup()

    def _cleanup(self):
        if self.closed:
            return
        self.closed = True
        if self._poll_id is not None:
            self.after_cancel(self._poll_id); self._poll_id = None
        self.job.close()
        self.diagram.cancel_layout(restore=False)
        # Widget callback cycles can otherwise be collected by the next SQL
        # worker, which would run StringVar finalizers outside the Tk thread.
        # Release variables and model callbacks synchronously on their owner.
        for name in ('mode', 'search', 'counts', 'status'):
            setattr(self, name, None)
        self.diagram.on_node = self.diagram.on_edge = self.diagram.on_view = self.diagram.on_hover = lambda *args: None
        self.diagram.on_branch = None
        self.diagram._committed_view = None
        self.diagram._layout_cache.clear()
        self.diagram.model = None
        self._matches = []
        if self.graph is not None:
            close_branch(self.graph)
        self.graph = None
        self.source_model = None
