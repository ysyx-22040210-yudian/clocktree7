"""Edit the file-wide macro rules used by both SV templates."""
import tkinter as tk
from tkinter import ttk

from .sv_macros import template_macros


class MacroEditor(ttk.Frame):
    def __init__(self, parent, rows, on_change):
        super().__init__(parent, padding=12)
        self.on_change = on_change
        self.rows = []
        ttk.Label(self, text='同一组宏适用于两套模板。生成时先展开变量，再替换代码和双引号中的原文。').pack(anchor='w')
        fields = ttk.Frame(self)
        fields.pack(fill='x', pady=8)
        fields.columnconfigure(1, weight=1)
        self.name, self.text = tk.StringVar(), tk.StringVar()
        for col, (label, var) in enumerate([('宏名', self.name), ('要替换的原文', self.text)]):
            ttk.Label(fields, text=label).grid(row=0, column=col, sticky='w')
            entry = ttk.Entry(fields, textvariable=var, width=27)
            entry.grid(row=1, column=col, sticky='ew', padx=(0, 8))
        buttons = ttk.Frame(self)
        buttons.pack(fill='x')
        self.add_button = ttk.Button(buttons, text='添加宏', command=lambda:self.edit('add'))
        self.update_button = ttk.Button(buttons, text='更新所选宏', command=lambda:self.edit('update'))
        self.remove_button = ttk.Button(buttons, text='删除所选宏', command=lambda:self.edit('remove'))
        for button in (self.add_button, self.update_button, self.remove_button):
            button.pack(side='left', padx=(0, 8))
        self.notice = tk.StringVar()
        ttk.Label(self, textvariable=self.notice).pack(anchor='w', pady=6)
        area = ttk.Frame(self)
        area.pack(fill='both', expand=True)
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(area, columns=('name','text'), show='headings', selectmode='browse')
        self.table.heading('name', text='宏名')
        self.table.heading('text', text='要替换的原文')
        self.table.column('name', width=160, stretch=False)
        self.table.column('text', width=480)
        self.table.grid(row=0, column=0, sticky='nsew')
        for axis, orient, row, col, sticky in [('y','vertical',0,1,'ns'), ('x','horizontal',1,0,'ew')]:
            scroll = ttk.Scrollbar(area, orient=orient, command=getattr(self.table, axis+'view'))
            self.table.configure(**{axis+'scrollcommand':scroll.set})
            scroll.grid(row=row, column=col, sticky=sticky)
        self.table.bind('<<TreeviewSelect>>', self.selected)
        ttk.Label(self, text='例如 CRG 对应 tb.dut.u_crg，代码中的 tb.dut.u_crg.clk 会显示为 `CRG.clk。'
                  '双引号内也可匹配片段，仿真时会展开为原文。', wraplength=900).pack(anchor='w', pady=8)
        self.load(rows)

    def load(self, rows, index=None):
        self.rows = template_macros({'macros':rows}).rows()
        self.table.delete(*self.table.get_children())
        for i, row in enumerate(self.rows):
            self.table.insert('', 'end', iid=str(i), values=(row['name'], row['text']))
        self.name.set('')
        self.text.set('')
        if index is not None:
            self.table.selection_set(str(index))
            self.table.see(str(index))
        self.selected()
        self.notice.set('已配置 {} 条宏'.format(len(self.rows)))

    def selected(self, event=None):
        items = self.table.selection()
        state = 'normal' if items else 'disabled'
        self.update_button.configure(state=state)
        self.remove_button.configure(state=state)
        if items:
            row = self.rows[int(items[0])]
            self.name.set(row['name'])
            self.text.set(row['text'])

    def edit(self, action):
        items = self.table.selection()
        if action != 'add' and not items:
            return
        rows = list(self.rows)
        index = len(rows) if action == 'add' else int(items[0])
        row = {'name':self.name.get(), 'text':self.text.get()}
        if action == 'add':
            rows.append(row)
        elif action == 'update':
            rows[index] = row
        else:
            rows.pop(index)
            index = None
        try:
            self.load(rows, index)
        except ValueError as error:
            self.notice.set(str(error))
            return
        self.on_change()
