"""Offline, Python 3.6 / Tk 8.5 SystemVerilog template editor."""
from bisect import bisect_right,bisect_left
from collections import defaultdict
import itertools
import re
import time
import tkinter as tk
from tkinter import ttk,font as tkfont

from .sv_template_text import VARIABLE_BUTTONS,template_tokens


KEYWORDS=frozenset('''always always_comb always_ff always_latch and assert assign assume automatic
begin bind break case casex casez checker class clocking const constraint continue cover covergroup
coverpoint default disable dist do else end endcase endchecker endclass endclocking endfunction
endgenerate endgroup endinterface endmodule endpackage endprogram endproperty endsequence endspecify
endtask enum event expect export extends extern final for force foreach forever fork function generate
genvar if iff ignore_bins illegal_bins import initial inout input inside instance interface intersect
join join_any join_none local localparam modport module nand negedge new nor not null or output
package parameter posedge priority program property protected pure rand randc randcase randsequence
ref release repeat return sequence solve specify static struct super tagged task this throughout
timeprecision timeunit typedef union unique unique0 until until_with var virtual void wait wait_order
while wildcard with within xnor xor'''.split())
TYPES=frozenset('bit byte chandle int integer logic longint real realtime reg shortint shortreal signed string time tri unsigned wand wire wor'.split())
LEXER=re.compile(r'(?P<comment>//[^\n]*|/\*[\s\S]*?(?:\*/|\Z))'
    r'|(?P<string>"(?:\\[\s\S]|[^"\\])*(?:"|\Z))'
    r'|(?P<escaped>\\[^\s]+)'
    r'|(?P<macro>`[A-Za-z_][A-Za-z0-9_$]*)'
    r'|(?P<system>\$[A-Za-z_][A-Za-z0-9_$]*)'
    r"|(?P<number>(?:\b[0-9][0-9_]*\s*)?'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ?]+|'[01xXzZ]|\b[0-9][0-9_]*(?:\.[0-9_]+)?(?:[eE][+-]?[0-9_]+)?(?:s|ms|us|ns|ps|fs)?\b)"
    r'|(?P<word>\b[A-Za-z_][A-Za-z0-9_$]*\b)')
COLORS={'keyword':'#569cd6','type':'#4ec9b0','comment':'#6a9955','string':'#ce9178',
        'number':'#b5cea8','system':'#dcdcaa','macro':'#c586c0','variable':'#4fc1ff','invalid':'#f48771'}


def syntax_spans(source):
    for match in LEXER.finditer(source):
        kind=match.lastgroup
        if kind=='word':kind='keyword' if match.group() in KEYWORDS else 'type' if match.group() in TYPES else None
        if kind in COLORS:yield kind,match.start(),match.end()


class SVCodeEditor(tk.Frame):
    def __init__(self,parent,filename='template.sv',initial=''):
        super().__init__(parent,background='#1e1e1e',highlightbackground='#3c3c3c',highlightthickness=1)
        self.revision=0;self.highlight_revision=-1;self.highlight_busy=False;self.highlight_seconds=0
        self.max_slice_ms=0;self._job=None;self._gutter_job=None;self._closed=False;self._work=None
        families=set(tkfont.families(self));family=next((f for f in ('Cascadia Code','Consolas','DejaVu Sans Mono','Liberation Mono') if f in families),'TkFixedFont')
        self.code_font=tkfont.Font(self,family=family,size=11)
        self.small_font=tkfont.Font(self,family=family,size=10)
        heading=tk.Frame(self,background='#252526');heading.pack(fill='x')
        tk.Label(heading,text='  SV  '+filename,foreground='#d4d4d4',background='#1e1e1e',font=self.small_font,padx=10,pady=7).pack(side='left')
        tk.Button(heading,text='查找  Ctrl+F',command=self.show_find,foreground='#cccccc',background='#252526',activebackground='#3c3c3c',activeforeground='white',relief='flat',takefocus=False).pack(side='right',padx=8)
        self.variable_buttons={}
        for label,names in [('时钟 / 复位',VARIABLE_BUTTONS[:6]),('控制 / 状态',VARIABLE_BUTTONS[6:])]:
            row=tk.Frame(self,background='#252526');row.pack(fill='x')
            tk.Label(row,text=label,background='#252526',foreground='#9da5b4',width=12,anchor='w',padx=8).pack(side='left')
            for name in names:
                button=tk.Button(row,text='$'+name,font=self.small_font,foreground='#9cdcfe',background='#333333',
                    activebackground='#094771',activeforeground='white',relief='flat',borderwidth=0,highlightthickness=0,padx=9,pady=3,takefocus=False,
                    command=lambda name=name:self.insert_variable(name))
                button.pack(side='left',padx=(0,6),pady=3);self.variable_buttons[name]=button
        tk.Label(self,text='点击变量插入光标处；单独变量保留信号路径，连接前后缀时转换为 SV 名称。',anchor='w',padx=12,pady=5,
                 background='#252526',foreground='#9da5b4').pack(fill='x')
        self.find_bar=tk.Frame(self,background='#333333');self.find_var=tk.StringVar();self.find_entry=tk.Entry(self.find_bar,textvariable=self.find_var,
            background='#3c3c3c',foreground='#eeeeee',insertbackground='white',relief='flat')
        self.find_entry.pack(side='left',fill='x',expand=True,padx=8,pady=5)
        tk.Button(self.find_bar,text='下一个',command=self.find_next).pack(side='left',padx=4)
        tk.Button(self.find_bar,text='关闭',command=self.hide_find).pack(side='left',padx=4)
        self.find_entry.bind('<Return>',self.find_next);self.find_entry.bind('<Escape>',self.hide_find)
        status=tk.Frame(self,background='#007acc');status.pack(side='bottom',fill='x')
        self.position=tk.StringVar(value='Ln 1, Col 1');self.diagnostic=tk.StringVar(value='SystemVerilog  |  UTF-8  |  Spaces: 4')
        tk.Label(status,textvariable=self.position,background='#007acc',foreground='white',padx=10,pady=3).pack(side='left')
        tk.Label(status,textvariable=self.diagnostic,background='#007acc',foreground='white',padx=10).pack(side='right')
        self.body=tk.Frame(self,background='#1e1e1e');self.body.pack(fill='both',expand=True)
        self.body.rowconfigure(0,weight=1);self.body.columnconfigure(1,weight=1)
        self.gutter=tk.Canvas(self.body,width=54,background='#1e1e1e',borderwidth=0,highlightthickness=0,takefocus=False)
        self.gutter.grid(row=0,column=0,sticky='ns')
        self.text=tk.Text(self.body,wrap='none',undo=True,maxundo=200,font=self.code_font,background='#1e1e1e',foreground='#d4d4d4',
            insertbackground='#ffffff',selectbackground='#264f78',selectforeground='#ffffff',inactiveselectbackground='#264f78',
            borderwidth=0,highlightthickness=0,padx=8,pady=6,spacing1=1,spacing3=1,exportselection=False)
        self.text.configure(tabs=(self.code_font.measure('    '),))
        self.text.grid(row=0,column=1,sticky='nsew')
        vertical=ttk.Scrollbar(self.body,command=self.text.yview);vertical.grid(row=0,column=2,sticky='ns')
        horizontal=ttk.Scrollbar(self.body,orient='horizontal',command=self.text.xview);horizontal.grid(row=1,column=1,sticky='ew')
        self.text.configure(yscrollcommand=lambda *args:self._scrolled(vertical,*args),xscrollcommand=horizontal.set)
        self.text.tag_configure('current_line',background='#282828');self.text.tag_lower('current_line')
        for name,color in COLORS.items():self.text.tag_configure(name,foreground=color,underline=(name=='invalid'))
        self.text.tag_raise('variable');self.text.tag_raise('invalid');self.text.tag_raise('sel')
        self.text.bind('<<Modified>>',self._modified)
        self.text.bind('<KeyRelease>',self._cursor);self.text.bind('<ButtonRelease-1>',self._cursor)
        self.text.bind('<Configure>',self._queue_gutter)
        self.text.bind('<Tab>',self.indent);self.text.bind('<Shift-Tab>',lambda e:self.indent(e,True))
        self.text.bind('<ISO_Left_Tab>',lambda e:self.indent(e,True));self.text.bind('<Return>',self.newline)
        self.text.bind('<Control-a>',self.select_all);self.text.bind('<Control-z>',self.undo)
        self.text.bind('<Control-y>',self.redo);self.text.bind('<Control-Shift-Z>',self.redo)
        self.text.bind('<Control-f>',self.show_find);self.text.bind('<F3>',self.find_next)
        self.bind('<Destroy>',self._destroyed,add='+')
        try:self._utf16=int(self.tk.call('string','length','\U0001f680'))==2;self.nonbmp_supported=True
        except tk.TclError:self._utf16=False;self.nonbmp_supported=False
        self.text.insert('1.0',initial);self.text.edit_reset();self.text.edit_modified(False)
        self._schedule();self._cursor()

    def _scrolled(self,scroll,*args):
        scroll.set(*args);self._queue_gutter()

    def _queue_gutter(self,event=None):
        if not self._closed and self._gutter_job is None:self._gutter_job=self.after_idle(self._draw_gutter)

    def _draw_gutter(self):
        self._gutter_job=None
        if self._closed:return
        self.gutter.delete('all');total=int(self.text.index('end-1c').split('.')[0])
        width=self.code_font.measure('0')*max(4,len(str(total))+1)+16;self.gutter.configure(width=width)
        index=self.text.index('@0,0');active=self.text.index('insert').split('.')[0]
        while True:
            info=self.text.dlineinfo(index)
            if info is None:break
            line=index.split('.')[0]
            self.gutter.create_text(width-12,info[1],anchor='ne',text=line,font=self.code_font,fill='#c6c6c6' if line==active else '#858585')
            index=self.text.index(index+'+1line')

    def _cursor(self,event=None):
        if self._closed:return
        line,column=self.text.index('insert').split('.');self.position.set('Ln {}, Col {}'.format(line,int(column)+1))
        self.text.tag_remove('current_line','1.0','end');self.text.tag_add('current_line','insert linestart','insert lineend+1c')
        self._queue_gutter()

    def _modified(self,event=None):
        if self._closed or not self.text.edit_modified():return
        self.text.edit_modified(False);self.revision+=1;self._schedule();self._cursor()

    def _schedule(self):
        if self._job is not None:self.after_cancel(self._job)
        self._work=None;self.highlight_busy=True;self._job=self.after(100,self._begin_highlight)

    def _begin_highlight(self):
        self._job=None;source=self.text.get('1.0','end-1c');self._highlight_started=time.perf_counter()
        self._lines=[0]+[m.end() for m in re.finditer('\n',source)]
        self._wide=[i for i,c in enumerate(source) if ord(c)>0xffff] if self._utf16 else []
        tokens=template_tokens(source);errors=sorted({t.error for t in tokens if t.error})
        self.diagnostic.set(errors[0] if errors else 'SystemVerilog  |  UTF-8  |  Spaces: 4')
        for name in COLORS:self.text.tag_remove(name,'1.0','end')
        self._work=itertools.chain(syntax_spans(source),(('invalid' if t.error else 'variable',t.start,t.end) for t in tokens))
        self._highlight_slice()

    def _index(self,offset):
        line=bisect_right(self._lines,offset);start=self._lines[line-1]
        extra=bisect_left(self._wide,offset)-bisect_left(self._wide,start)
        return '{}.{}'.format(line,offset-start+extra)

    def _highlight_slice(self):
        self._job=None;begin=time.perf_counter();ranges=defaultdict(list);done=False
        # Apply bounded batches and yield to Tk between them. New edits cancel
        # the pending batch; no stale worker writes to a changed document.
        for count in range(180):
            try:kind,start,end=next(self._work)
            except StopIteration:done=True;break
            ranges[kind].extend((self._index(start),self._index(end)))
            if time.perf_counter()-begin>.004:break
        for name,indices in ranges.items():self.text.tag_add(name,*indices)
        self.max_slice_ms=max(self.max_slice_ms,(time.perf_counter()-begin)*1000)
        if done:
            self.highlight_revision=self.revision;self.highlight_busy=False;self._work=None
            self.highlight_seconds=time.perf_counter()-self._highlight_started
        else:self._job=self.after(1,self._highlight_slice)

    def _delete_selection(self):
        if self.text.tag_ranges('sel'):self.text.delete('sel.first','sel.last')

    def insert_variable(self,name):
        self.text.focus_set();self.text.edit_separator();self._delete_selection()
        self.text.insert('insert','$'+name);self.text.edit_separator();self.text.see('insert');self._cursor()

    def select_all(self,event=None):
        self.text.tag_add('sel','1.0','end-1c');return 'break'

    def undo(self,event=None):
        try:self.text.edit_undo()
        except tk.TclError:pass
        self._cursor();return 'break'

    def redo(self,event=None):
        try:self.text.edit_redo()
        except tk.TclError:pass
        self._cursor();return 'break'

    def indent(self,event=None,reverse=False):
        text=self.text;text.edit_separator();selection=text.tag_ranges('sel')
        if not selection and not reverse:
            column=int(text.index('insert').split('.')[1]);text.insert('insert',' '*(4-column%4))
        else:
            start=text.index('sel.first linestart') if selection else text.index('insert linestart')
            end=text.index('sel.last') if selection else text.index('insert lineend')
            first=int(start.split('.')[0]);last=int(end.split('.')[0])
            if selection and end.endswith('.0') and last>first:last-=1
            text.mark_set('block_start',start);text.mark_gravity('block_start','left')
            text.mark_set('block_end','{}.end'.format(last));text.mark_gravity('block_end','right')
            for line in range(last,first-1,-1):
                index='{}.0'.format(line)
                if reverse:
                    prefix=text.get(index,index+'+4c');count=1 if prefix.startswith('\t') else len(prefix)-len(prefix.lstrip(' '))
                    if count:text.delete(index,index+'+{}c'.format(count))
                else:text.insert(index,'    ')
            if selection:text.tag_add('sel','block_start','block_end')
            text.mark_unset('block_start','block_end')
        text.edit_separator();self._cursor();return 'break'

    def newline(self,event=None):
        self.text.edit_separator();self._delete_selection();prefix=self.text.get('insert linestart','insert')
        indentation=re.match(r'[ \t]*',prefix).group()
        if re.search(r'\b(?:begin|fork|generate)\s*(?://.*)?$',prefix):indentation+='    '
        self.text.insert('insert','\n'+indentation);self.text.edit_separator();self.text.see('insert');self._cursor();return 'break'

    def show_find(self,event=None):
        self.find_bar.pack(fill='x',before=self.body);self.find_entry.focus_set();return 'break'

    def hide_find(self,event=None):
        self.find_bar.pack_forget();self.text.focus_set();return 'break'

    def find_next(self,event=None):
        needle=self.find_var.get()
        if not needle:return self.show_find()
        index=self.text.search(needle,'insert',stopindex='end',nocase=True) or self.text.search(needle,'1.0',stopindex='insert',nocase=True)
        if index:
            end=index+'+{}c'.format(int(self.tk.call('string','length',needle)))
            self.text.tag_remove('sel','1.0','end');self.text.tag_add('sel',index,end);self.text.mark_set('insert',end);self.text.see(index);self._cursor()
        return 'break'

    def _destroyed(self,event):
        if event.widget is not self:return
        self._closed=True
        for job in (self._job,self._gutter_job):
            if job is not None:
                try:self.after_cancel(job)
                except tk.TclError:pass
        self._work=None
