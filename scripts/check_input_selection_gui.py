"""Real Verdi input discovery and live selection counts, with duplicate-width stress."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from clocktree.gui import ClockTreeApp,RuleDialog
from clocktree.input_view import InputPortDialog
from clocktree.input_ports import confirmed_ports
from check_indexed_gui import capture

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--request',type=Path,required=True)
parser.add_argument('--ports',type=int,default=10000)
args=parser.parse_args()
out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
errors=[];records=[]
app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=False)
app.geometry('1800x980+0+0')
app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
app.show_error=lambda title,error:errors.append(title+': '+str(error))

def pump(predicate=lambda:True):
    started=time.monotonic()
    while True:
        app.update()
        if predicate():break
        if time.monotonic()-started>120:raise AssertionError('GUI wait exceeded: '+ports.status.get())
        time.sleep(.01)

def count(case,selected,total,pending=False):
    text=ports.selection_summary.get()
    expected='已选 {} 个输入端口 / {}匹配 {} 个端口名{}'.format(selected,'上次' if pending else '当前',total,'（待重新筛选）' if pending else '')
    assert text==expected,(case,text,expected)
    if not pending and total:
        names={r['name'] for r in ports.shown}&ports.selected
        assert len(names)==selected
        if selected:assert len(confirmed_ports(ports.rows,ports.expression.get(),ports.selected))==selected
    records.append(dict(mode=mode,case=case,display=text,rows=len(ports.shown)))

def click_name(name):
    row=next(str(i) for i,r in enumerate(ports.shown) if r['name']==name)
    ports.table.see(row);pump()
    x,y,w,h=ports.table.bbox(row,'#0')
    ports.table.event_generate('<ButtonPress-1>',x=x+12,y=y+h//2)
    ports.table.event_generate('<ButtonRelease-1>',x=x+12,y=y+h//2)
    pump()

base=json.loads(args.request.read_text())['config']
for mode in ('clock','reset'):
    config=copy.deepcopy(base);config['tree_type']=mode
    if mode=='reset':
        rule=config['layers'][0]['modules'][0]
        rule['kind']='rsy_sync';rule['reset_inputs']=rule.pop('clock_inputs');rule['reset_output']=rule.pop('clock_output')
    app.set_config(config);pump()
    key='clock_inputs' if mode=='clock' else 'reset_inputs'
    saved=[]
    editor=RuleDialog(app,2,{'module':'wb_sub','kind':'subsystem',key:['clk_bus'],'input_regex':'clk'},saved.append)
    editor.edit_inputs();ports=next(w for w in editor.winfo_children() if isinstance(w,InputPortDialog));pump()
    count('before_read',0,0)
    ports.start();count('read_reset',0,0)
    pump(lambda:bool(ports.rows) and str(ports.read_button['state'])=='normal')
    assert len(ports.rows)==6 and len(ports.shown)==4
    count('restored_bus_selection',1,2)
    click_name('aux_clk');count('single_check',2,2)
    click_name('clk_bus');count('single_uncheck',1,2)
    ports.toggle_all(False);count('clear',0,2)
    ports.toggle_all(True);count('all',2,2)
    ports.expression.set('aux');count('edited_regex',2,2,True)
    ports.filter();count('narrow_filter_excludes_hidden',1,1)
    ports.toggle_all(False);count('clear_narrow',0,1)
    ports.expression.set('^no_match$');ports.filter();count('zero_match',0,0)
    ports.expression.set('[');ports.filter();count('invalid_regex',0,0)
    assert str(ports.confirm_button['state'])=='disabled' and '正则无效' in ports.status.get()
    ports.expression.set('clk');ports.filter();count('restore_wider_filter',1,2)
    ports.toggle_all(True);count('all_again',2,2)
    ports.geometry('760x460+30+30');pump()
    pending=list(ports.winfo_children());widgets=[]
    while pending:
        widget=pending.pop();widgets.append(widget);pending.extend(widget.winfo_children())
    label=next(w for w in widgets if w.winfo_class()=='TLabel' and str(w.cget('textvariable'))==str(ports.selection_summary))
    assert label.winfo_ismapped() and label.winfo_rooty()+label.winfo_height()<=ports.winfo_rooty()+ports.winfo_height()
    assert label.winfo_reqwidth()<=ports.winfo_width()-36
    assert ports.confirm_button.winfo_ismapped() and ports.confirm_button.winfo_rooty()+ports.confirm_button.winfo_height()<=ports.winfo_rooty()+ports.winfo_height()
    capture(ports,out/(mode+'-minimum.png'))
    ports.geometry('950x620+30+30');pump()
    capture(ports,out/(mode+'-selected.png'))
    ports.start();count('reread_reset',0,0)
    ports.cancel();pump(lambda:ports.process.poll() is not None and str(ports.read_button['state'])=='normal')
    count('cancelled_read',0,0)
    ports.start();pump(lambda:bool(ports.rows) and str(ports.read_button['state'])=='normal')
    count('reread_restores',2,2)
    ports.confirm();editor.save();pump()
    assert saved[0][key]==['aux_clk','clk_bus']

    # Reuse real parameterized discovery metadata, then stress the preview only.
    editor=RuleDialog(app,2,{'module':'wb_sub','kind':'subsystem',key:['clk_bus'],'input_regex':'^clk_'},lambda rule:None)
    editor.edit_inputs();ports=next(w for w in editor.winfo_children() if isinstance(w,InputPortDialog));pump()
    prototype={'name':'clk_0','direction':'input','left':1,'right':0,'width':2,'instance':'wb_top.s0'}
    ports.rows=[]
    for i in range(args.ports):
        for width in (2,3):
            row=dict(prototype,name='clk_{}'.format(i),width=width,left=width-1,instance='wb_top.s'+str(width-2))
            ports.rows.append(row)
    ports.selected=set();ports.expression.set('^clk_');ports.filter();pump()
    count('stress_empty',0,args.ports)
    ports.toggle_all(True);count('stress_distinct_names',args.ports,args.ports)
    for i in range(30):
        ports.expression.set('^clk_{}$'.format(i));ports.filter()
        count('stress_narrow_'+str(i),1,1)
        ports.toggle_all(False);count('stress_uncheck_'+str(i),0,1)
    ports.expression.set('^clk_');ports.filter();count('stress_restore',args.ports-30,args.ports)
    assert len(confirmed_ports(ports.rows,ports.expression.get(),ports.selected))==args.ports-30
    ports.close();editor.destroy();pump()

assert not errors,errors
report=dict(passed=True,python=sys.version,tk=app.tk.call('info','patchlevel'),cases=records,callback_errors=errors,
            real_eda_discovery=True,clock_and_reset=True,confirmed_names=['aux_clk','clk_bus'])
(out/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
app.close()
print(json.dumps(dict(passed=True,cases=len(records),callback_errors=len(errors)),ensure_ascii=False),flush=True)
