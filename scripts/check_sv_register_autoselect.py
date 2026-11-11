"""Native Tk regression for latest register selection, stale plans and restart."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clocktree.storage import write_graph
from clocktree.session import read_graph


def run(source, out, phase):
    from clocktree.gui import ClockTreeApp
    from clocktree.sv_view import SVDialog
    from check_workbench_gui import pump
    from check_indexed_gui import capture
    out.mkdir(parents=True, exist_ok=True)
    app=ClockTreeApp(runs=out/'runs', session_dir=out/'session', remember=True)
    app.geometry('1840x1000+0+0');errors=[];checks=[]
    app.report_callback_exception=lambda *args:errors.append(''.join(traceback.format_exception(*args)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)

    def job(name, order, change=None, running=False):
        directory=out/'runs'/'.register-trace'/name;directory.mkdir(parents=True)
        for filename in ('manifest.json','register-trace.json','registers.sqlite'):
            shutil.copy2(str(source/'registers'/filename),str(directory/filename))
        summary=json.loads((directory/'register-trace.json').read_text(encoding='utf-8'))
        if change:
            summary['config']['design']['top']=change
            manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
            manifest['request']['config']['design']['top']=change
            write_graph(directory/'manifest.json',manifest)
        if running:summary['summary']['finished']=False
        write_graph(directory/'register-trace.json',summary)
        os.utime(str(directory/'register-trace.json'),(1700000000+order,1700000000+order))
        return directory

    def analyze(dialog,expected):
        dialog.analyze()
        pump(app,lambda:dialog.process is not None and dialog.process.poll() is not None and dialog.analyze_button.instate(['!disabled']))
        assert dialog.plan is not None,dialog.status.get()
        assert dialog.plan.source()['registers']==str(expected)
        assert dialog.plan.branches()[0]==2

    def export(dialog,name,expected):
        path=out/name;dialog.export(path)
        pump(app,lambda:dialog.process.poll() is not None and dialog.export_button.instate(['!disabled']))
        report=json.loads(Path(dialog.export_result['mapping']).read_text(encoding='utf-8'))
        assert report['block_count']==2,report
        assert report['source']['registers']==str(expected)

    if phase=='write':
        app.show_graph(read_graph(source/'tree'/'clocktree.json'));pump(app)
        old=job('zzz-old',10);new=job('aaa-new',20)
        job('newer-other-design',30,change='other_top');job('newer-running',40,running=True)
        app.ui_state.update(app.graph['config'],register_trace_directory=str(old))
        dialog=SVDialog(app);pump(app)
        assert dialog.auto_registers.get() and dialog.registers.get()==str(new)
        for mode,text in {'with_mux':'// $var_clkip $var_pllsel\n','without_mux':'// $var_clkip $var_gt0\n'}.items():
            dialog.editors[mode].delete('1.0','end');dialog.editors[mode].insert('1.0',text)
        analyze(dialog,new);export(dialog,'auto.sv',new)
        checks.append('opening selects latest completed same-design result; two SV blocks use it')
        dialog.registers.set(str(old));assert not dialog.auto_registers.get() and dialog.plan is None
        third=job('third',50)
        analyze(dialog,old);export(dialog,'manual.sv',old)
        checks.append('manual selection survives analysis even when a newer result exists')
        dialog.auto_registers.set(True);dialog.refresh_registers()
        assert dialog.registers.get()==str(third) and dialog.plan is None
        assert dialog.export_button.instate(['disabled'])
        analyze(dialog,third)
        fourth=job('fourth',60)
        dialog.export(out/'must-not-export.sv');pump(app)
        assert dialog.plan is None and not (out/'must-not-export.sv').exists()
        assert dialog.registers.get()==str(fourth)
        checks.append('new result between analysis and export blocks stale plan')
        analyze(dialog,fourth)
        result=fourth/'register-trace.json';data=json.loads(result.read_text(encoding='utf-8'))
        data['qa_revision']=2;write_graph(result,data)
        dialog.export(out/'must-not-export-revision.sv');pump(app)
        assert dialog.plan is None and not (out/'must-not-export-revision.sv').exists()
        checks.append('in-place result update blocks stale plan')
        analyze(dialog,fourth);export(dialog,'final.sv',fourth)
        dialog.geometry('1050x700+0+0');pump(app)
        assert dialog.analyze_button.winfo_rootx()+dialog.analyze_button.winfo_width()<=dialog.winfo_rootx()+dialog.winfo_width()
        capture(dialog,out/'sv-register-auto.png')
        dialog.close();app._save_session_now();pump(app)
    else:
        dialog=SVDialog(app);pump(app)
        latest=out/'runs'/'.register-trace'/'fourth'
        assert dialog.auto_registers.get() and dialog.registers.get()==str(latest)
        assert dialog.plan is not None and dialog.plan.source()['registers']==str(latest)
        assert dialog.templates()['without_mux']=='// $var_clkip $var_gt0\n'
        dialog.close()
        job('after-restart',70)
        # Make this result later than the in-place update of "fourth".
        path=out/'runs'/'.register-trace'/'after-restart'/'register-trace.json'
        current=(latest/'register-trace.json').stat().st_mtime+1
        os.utime(str(path),(current,current))
        dialog=SVDialog(app);pump(app)
        assert dialog.registers.get()==str(path.parent) and dialog.plan is None
        assert '重新分析' in dialog.status.get()
        assert dialog.templates()['without_mux']=='// $var_clkip $var_gt0\n'
        dialog.close();checks.append('independent GUI restart restores current plan; newer result invalidates it while preserving templates')
    assert not errors,errors
    write_graph(out/(phase+'.json'),{'passed':True,'checks':checks,'callback_errors':errors,'tk':app.tk.call('info','patchlevel')})
    app.close();deadline=time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:app.update();time.sleep(.01)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,help='Existing native check_sv_templates output')
    parser.add_argument('--out',required=True);parser.add_argument('--phase',choices=('write','read'),required=True)
    args=parser.parse_args();run(Path(args.source).resolve(),Path(args.out).resolve(),args.phase)
