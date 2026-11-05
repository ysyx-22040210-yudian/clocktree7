"""Native Tk timeout configuration, instance/port labels, exports and history."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.gui import ClockTreeApp
from clocktree.storage import write_graph
from check_flexible_gui import pump,capture


def phase(out,fixtures,stage):
    errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception=lambda *e:errors.append(str(e))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if stage=='write':
        assert float(app.port_timeout_var.get())==0
        app.load_result(fixtures/'clock-target-input/clocktree.json');pump(app,lambda:not app.busy)
        assert float(app.port_timeout_var.get())==1
        assert 'trace 超时' in app.state.get()
        bad=next(n for n in app.graph['nodes'] if n.get('trace_timeout'))
        app.diagram.locate(bad);pump(app)
        details=app.prop_text.get('1.0','end')
        assert 'trace 超时' in details and '此前 trace 已完成' in details
        assert 'trace 已跳过' in details and '未执行输出 load trace' in details
        app.export_svg(out/'clock-timeout.svg');ET.parse(str(out/'clock-timeout.svg'))
        svg=(out/'clock-timeout.svg').read_text(encoding='utf-8')
        assert 'b · trace 超时' in svg and 'c · trace 跳过' in svg and 'y · 实例超时' in svg
        app.export_result(out/'clock-timeout.json')
        exported=json.loads((out/'clock-timeout.json').read_text(encoding='utf-8'))
        assert exported['summary']['trace_timeout_instances']==1
        app.settings_canvas.yview_moveto(.22);pump(app)
        capture(app,out/'clock-target-timeout.png')
        for invalid in ('-1','nan','inf','abc'):
            app.port_timeout_var.set(invalid)
            try:app.get_config()
            except ValueError:pass
            else:raise AssertionError('Accepted invalid trace timeout '+invalid)
        app.port_timeout_var.set('12.5');app.save_config(out/'clock-config.json')
        assert json.loads((out/'clock-config.json').read_text())['port_trace_timeout_s']==12.5
        app.switch_tree_type('reset');pump(app,lambda:not app.busy)
        app.load_result(fixtures/'reset-full-output/resettree.json');pump(app,lambda:not app.busy)
        bad=next(n for n in app.graph['nodes'] if n.get('trace_timeout'))
        app.diagram.locate(bad);pump(app)
        assert 'y / load' in app.prop_text.get('1.0','end')
        app.export_svg(out/'reset-timeout.svg')
        assert 'y · trace 超时' in (out/'reset-timeout.svg').read_text(encoding='utf-8')
        capture(app,out/'reset-output-timeout.png')
        app.port_timeout_var.set('45');pump(app)
    else:
        assert app._active_tree_type=='reset' and float(app.port_timeout_var.get())==45
        assert app.graph['summary']['trace_timeout_instances']==1
        app.switch_tree_type('clock');pump(app,lambda:not app.busy)
        assert float(app.port_timeout_var.get())==12.5
        assert app.graph['summary']['trace_timeout_instances']==1
        bad=next(n for n in app.graph['nodes'] if n.get('trace_timeout'))
        app.diagram.locate(bad);pump(app)
        assert 'b / driver' in app.prop_text.get('1.0','end')
        app.port_timeout_var.set('0');assert app.get_config()['port_trace_timeout_s']==0
        capture(app,out/'restored-timeout.png')
    assert not errors,errors
    write_graph(out/(stage+'.json'),dict(passed=True,native_tk=True,tk=app.tk.call('info','patchlevel'),
        fresh_process=stage=='read',instance_and_port_labels=True,independent_clock_reset_settings=True,errors=errors))
    app.close()
    try:
        if not app._destroying:pump(app,lambda:app._destroying)
    except tk.TclError:
        if not app._destroying:raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True);p.add_argument('--fixtures',required=True);p.add_argument('--phase',choices=['write','read'])
    args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.phase:return phase(out,Path(args.fixtures).resolve(),args.phase)
    for stage in ('write','read'):
        subprocess.check_call([sys.executable,__file__,'--out',str(out),'--fixtures',args.fixtures,'--phase',stage])
    write_graph(out/'acceptance.json',dict(passed=True,native_tk=True,fresh_processes=2))
    print('Trace timeout GUI acceptance passed',flush=True)


if __name__=='__main__':main()
