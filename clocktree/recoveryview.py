"""Native recovery notice; opening it never starts EDA or changes evidence."""
import tkinter as tk
from tkinter import ttk
from pathlib import Path


class RecoveryDialog(tk.Toplevel):
    def __init__(self, app, info, gui_issue=None):
        super().__init__(app)
        self.app, self.info = app, info
        self.title('恢复上次运行 · ClockTree7')
        self.configure(bg='white'); self.transient(app)
        frame = ttk.Frame(self, padding=24, style='White.TFrame'); frame.pack(fill='both', expand=True)
        self.title_label = ttk.Label(frame, text='发现上次未完成的运行', style='Title.TLabel')
        self.title_label.pack(anchor='w')
        report = info.get('diagnosis', {})
        checkpoint = info.get('checkpoint') or report.get('checkpoint') or {}
        context = report.get('context') or {}
        lines = []
        if gui_issue:
            finished=gui_issue.get('last_event',{}).get('kind') in ('end','finished')
            lines.append('上次界面最后记录：{}{}。未记录正常关闭；具体原因以日志为准。'.format(
                gui_issue.get('label', '界面'),'（该操作已结束）' if finished else ''))
            lines.append('GUI 日志：' + str(gui_issue.get('log', '')))
        if report.get('available'):
            lines.extend(['任务：' + info['directory'], '最后阶段：' + str(report.get('label', '准备')),
                          '状态说明：' + info.get('reason', '')])
            for key, label in (('module', 'Module'), ('scope', '范围'), ('instance', '实例'), ('pin', '端口')):
                if context.get(key):lines.append('{}：{}'.format(label, context[key]))
            if context.get('api'):
                lines.append('最后记录的 NPI 操作：{} · {}'.format(context['api'],context.get('object','')))
            if 'scanned' in context:
                lines.append('端口内进度：已扫描 {}，已处理 {}，待处理 {}。'.format(
                    context['scanned'],context.get('processed',0),context.get('pending',0)))
            lines.append('已提交断点：{} 条 module 规则 / {} 个端口追踪'.format(
                checkpoint.get('completed_modules', 0), checkpoint.get('completed_traces', 0)))
            if checkpoint.get('last_module'):
                last = checkpoint['last_module']
                lines.append('最后模块：L{} · {} · {}'.format(last.get('layer'), last.get('scope'), last.get('module')))
            lines.append('继续方式：' + info.get('next_step', info.get('blocked', '查看日志')))
            lines.append('阶段日志：' + str(report.get('logs', {}).get('run', '')))
        content=ttk.Frame(frame,style='White.TFrame');content.pack(fill='both',expand=True,pady=(14,12))
        self.detail = tk.Text(content, height=15, width=86, wrap='word', bg='#f4f7fc', fg='#263852',
                              relief='flat', padx=13, pady=12, font=(app.family, 10))
        scroll=ttk.Scrollbar(content,command=self.detail.yview)
        scroll.pack(side='right',fill='y');self.detail.configure(yscrollcommand=scroll.set)
        self.detail.pack(fill='both', expand=True)
        self.detail.insert('1.0', '\n\n'.join(lines)); self.detail.configure(state='disabled')
        row = ttk.Frame(frame, style='White.TFrame'); row.pack(fill='x', pady=(0,12))
        ttk.Label(row, text='续跑内存阈值（GiB）', style='White.TLabel').pack(side='left')
        self.limit = tk.StringVar(value=str(info.get('manifest', {}).get('config', {}).get('memory_limit_gib', 60)))
        self.limit_entry = ttk.Entry(row, textvariable=self.limit, width=10)
        self.limit_entry.pack(side='left', padx=12)
        if not info.get('resumable'):self.limit_entry.configure(state='disabled')
        ttk.Label(frame, text='继续使用原任务的规则与输入；未提交尾部会撤回，已提交的实例和追踪不会重复计入。',
                  style='Muted.TLabel', wraplength=730).pack(anchor='w')
        actions = ttk.Frame(frame, style='White.TFrame'); actions.pack(fill='x', pady=(18,0))
        ttk.Button(actions, text='稍后处理', command=self.destroy).pack(side='right')
        ttk.Button(actions, text='查看日志', command=self.logs).pack(side='left')
        self.resume_button = ttk.Button(actions, text='从断点继续', style='Accent.TButton', command=self.continue_run,
                                       state='normal' if info.get('resumable') else 'disabled')
        self.resume_button.pack(side='right', padx=10)
        if info.get('running'):
            self.title_label.configure(text='上次界面退出，后端仍在运行')
            ttk.Button(actions, text='观察现有任务', command=self.monitor).pack(side='left', padx=8)
        if report.get('state') in ('finished', 'attention') or (gui_issue and not report.get('available')):
            self.title_label.configure(text='上次界面未正常关闭')
            ttk.Button(actions, text='打开已保存结果', command=self.open_result).pack(side='left', padx=8)
        self.geometry('820x650+{}+{}'.format(app.winfo_rootx()+100, app.winfo_rooty()+60))
        self.bind('<Escape>', lambda event:self.destroy())

    def logs(self):
        if self.info.get('directory'):self.app._runtime_directory = Path(self.info['directory'])
        self.app.show_run_diagnosis()
        self.destroy()

    def continue_run(self):
        try:limit = float(self.limit.get())
        except ValueError:
            self.app.show_error('内存阈值无效', '请输入非负数，0 表示关闭内存轮换。'); return
        import math
        if not math.isfinite(limit) or limit < 0:
            self.app.show_error('内存阈值无效', '请输入有限非负数。'); return
        self.app.resume_extraction(self.info, limit)
        if self.app.busy:self.destroy()

    def monitor(self):
        self.app.monitor_extraction(self.info); self.destroy()

    def open_result(self):
        from pathlib import Path
        from .profiles import result_filename
        manifest = self.info.get('manifest', {})
        path = Path(self.info['directory']) / result_filename(manifest.get('config', {})) if manifest else self.app._recovery_result
        if path and Path(path).is_file():self.app.load_result(path); self.destroy()
        else:self.app.show_error('结果尚不可用', '请在运行记录中选择结果，或从有效断点重建。')
