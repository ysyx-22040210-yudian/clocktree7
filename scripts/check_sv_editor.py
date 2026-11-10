"""Native Tk keyboard/palette, syntax and long-template acceptance (offline)."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback
import tkinter as tk

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.sv_editor import SVCodeEditor
from clocktree.sv_template_text import VARIABLE_BUTTONS,render
from clocktree.storage import write_graph
from check_indexed_gui import capture


SAMPLE='''// ClockTree7 · 时钟分支检查模板
// 单独变量保留路径；check_$var_clkip_sva 自动生成合法名称。
property check_$var_clkip_sva;
    @(posedge $var_clkip) disable iff (!$var_rst)
        ($var_gt0 && $var_gt1 && $var_gt2) |-> ##1 $stable($var_div);
endproperty

check_$var_clkip_assert: assert property (check_$var_clkip_sva)
    else $error("clock branch: %m");

initial begin
    logic [7:0] expected = 8'hff;
    $display("PLL %b", $var_pll0);
    $display("select=%h done=%b", $var_pllsel, $var_swdone);
end
'''


def pump(root,condition=lambda:True,limit=90):
    start=time.monotonic()
    while True:
        root.update()
        if condition():break
        if time.monotonic()-start>limit:raise AssertionError('Tk acceptance deadline')
        time.sleep(.004)


def replace(editor,value):
    editor.text.delete('1.0','end');editor.text.insert('1.0',value);editor.text.edit_reset()


def rss():
    return int(next(v.split()[1] for v in Path('/proc/self/status').read_text().splitlines() if v.startswith('VmRSS:')))/1024.


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);parser.add_argument('--lines',type=int,default=20000)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    root=tk.Tk();root.title('ClockTree7 · SV 模板编辑验收');root.geometry('1240x820+0+0');errors=[]
    root.report_callback_exception=lambda *e:errors.append(''.join(traceback.format_exception(*e)))
    editor=SVCodeEditor(root,'with_mux.sv',SAMPLE);editor.pack(fill='both',expand=True)
    pump(root,lambda:not editor.highlight_busy);text=editor.text;text.focus_force();pump(root)
    for name in VARIABLE_BUTTONS:
        replace(editor,'left  right');text.mark_set('insert','1.5');text.focus_force();pump(root)
        button=editor.variable_buttons[name]
        assert button.winfo_rootx()+button.winfo_width()<=root.winfo_rootx()+root.winfo_width()
        button.event_generate('<Enter>');button.event_generate('<ButtonPress-1>',x=8,y=6);pump(root)
        button.event_generate('<ButtonRelease-1>',x=8,y=6);pump(root)
        button.event_generate('<Leave>');pump(root)
        assert text.get('1.0','end-1c')=='left $'+name+' right',(name,text.get('1.0','end-1c'))
        text.event_generate('<Control-z>');pump(root);assert text.get('1.0','end-1c')=='left  right'
        text.event_generate('<Control-y>');pump(root);assert text.get('1.0','end-1c')=='left $'+name+' right'
    replace(editor,'left REPLACE right');text.tag_add('sel','1.5','1.12');text.mark_set('insert','1.5')
    editor.variable_buttons['var_pll0'].invoke();pump(root)
    assert text.get('1.0','end-1c')=='left $var_pll0 right'
    text.tag_remove('sel','1.0','end');replace(editor,'begin');text.mark_set('insert','end-1c');text.focus_force()
    text.event_generate('<Return>');pump(root);assert text.get('1.0','end-1c')=='begin\n    '
    text.event_generate('<Tab>');pump(root);assert text.get('2.0','2.end')=='        '
    text.event_generate('<Shift-Tab>');pump(root);assert text.get('2.0','2.end')=='    '
    replace(editor,'a\nb\nc');text.tag_add('sel','1.0','3.0');text.event_generate('<Tab>');pump(root)
    assert text.get('1.0','end-1c')=='    a\n    b\nc'
    text.event_generate('<Shift-Tab>');pump(root);assert text.get('1.0','end-1c')=='a\nb\nc'
    replace(editor,'// 中文 '+('\U0001f680 ' if editor.nonbmp_supported else '')+'comment\nlogic [7:0] x = 8\'hff;\n'+SAMPLE)
    pump(root,lambda:not editor.highlight_busy)
    for spelling,tag in [('logic','type'),("8'hff",'number'),('property','keyword'),('$var_clkip','variable'),('$display','system'),('// 中文','comment')]:
        index=text.search(spelling,'1.0','end');assert index and tag in text.tag_names(index),(spelling,index,text.tag_names(index))
    editor.show_find();editor.find_var.set('property');editor.find_next();pump(root)
    assert text.get('sel.first','sel.last')=='property';editor.hide_find()
    replace(editor,'check_$var_clkip_sva $var_pll10');pump(root,lambda:not editor.highlight_busy)
    assert '未知' in editor.diagnostic.get() and 'invalid' in text.tag_names(text.search('$var_pll10','1.0','end'))
    replace(editor,SAMPLE);pump(root,lambda:not editor.highlight_busy)
    assert not text.tag_ranges('invalid') and 'SystemVerilog' in editor.diagnostic.get()
    values={name:'top.ctrl.signal' for name in VARIABLE_BUTTONS};values['var_clkip']='top.domain.clk[3]'
    generated=render(SAMPLE,values)
    assert 'property check_top_domain_clk_3_sva;' in generated and '@(posedge top.domain.clk[3])' in generated
    long_text=''.join("logic [7:0] value_{} = 8'hff; // 中文 {}\n".format(i,i) for i in range(args.lines))
    memory=[];highlight=[];updates=[];heartbeat=[];last_beat=[time.perf_counter()];beat_job=[None]
    def beat():
        now=time.perf_counter();heartbeat.append((now-last_beat[0])*1000);last_beat[0]=now
        beat_job[0]=root.after(10,beat)
    beat_job[0]=root.after(10,beat)
    for cycle in range(12):
        replace(editor,long_text+'// revision '+str(cycle));pump(root)
        pump(root,lambda:not editor.highlight_busy)
        assert editor.highlight_revision==editor.revision
        highlight.append(editor.highlight_seconds);memory.append(rss())
        for step in range(30):
            begin=time.perf_counter();text.yview_moveto(step/30.);root.update();updates.append((time.perf_counter()-begin)*1000)
        assert len(editor.gutter.find_all())<100
    # Repeated edits cancel unfinished coloring and leave only final content.
    replace(editor,long_text);pump(root)
    for i in range(30):
        text.insert('end-1c','x');begin=time.perf_counter();root.update();updates.append((time.perf_counter()-begin)*1000)
    replace(editor,SAMPLE);pump(root,lambda:not editor.highlight_busy)
    assert text.get('1.0','end-1c')==SAMPLE and not text.tag_ranges('invalid')
    capture(root,out/'sv-template-editor.png')
    jobs=(editor._job,editor._gutter_job);editor.destroy();pump(root)
    assert all(job is None or job not in root.tk.call('after','info') for job in jobs)
    for i in range(5):
        temporary=SVCodeEditor(root,initial=long_text);temporary.pack();pump(root)
        temporary._begin_highlight();assert temporary.highlight_busy
        temporary.destroy();pump(root)
    assert not errors,errors
    assert max(memory[8:])-min(memory[8:])<24,memory
    root.after_cancel(beat_job[0])
    record=dict(passed=True,tk=root.tk.call('info','patchlevel'),palette_buttons=len(VARIABLE_BUTTONS),real_button_events=True,
        cursor_selection_undo_redo=True,indentation=True,syntax_and_unknown_variables=True,chinese=True,nonbmp_supported=editor.nonbmp_supported,find=True,
        canceled_highlighting=True,destroy_cycles=6,lines=args.lines,characters=len(long_text),cycles=12,
        highlight_seconds=highlight,gui_rss_mib=memory,late_rss_spread_mib=max(memory[8:])-min(memory[8:]),
        update_p95_ms=sorted(updates)[int(.95*(len(updates)-1))],update_max_ms=max(updates),highlight_slice_max_ms=editor.max_slice_ms,
        heartbeat_p95_ms=sorted(heartbeat)[int(.95*(len(heartbeat)-1))],heartbeat_max_ms=max(heartbeat),errors=errors)
    write_graph(out/'acceptance.json',record);root.destroy();print(json.dumps(record,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
