"""Reopen completed register evidence even when its UI snapshot is stale."""
import argparse
import json
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.gui import ClockTreeApp
from clocktree.register_view import RegisterDialog
from clocktree.register_progress import read_progress
from clocktree.session import read_graph
from clocktree.storage import write_graph
from check_workbench_gui import pump
from check_indexed_gui import capture


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=False);app.geometry('1840x1000+0+0');errors=[]
    app.report_callback_exception=lambda *args:errors.append(''.join(traceback.format_exception(*args)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    app.show_graph(read_graph(a.base/'tree/clocktree.json'));pump(app)
    dialog=RegisterDialog(app);dialog.load_directory(a.base/'registers');pump(app)
    snapshot=json.loads((a.base/'registers/status.json').read_text())
    assert read_progress(a.base/'registers')['stage']=='finished'
    assert dialog.resume_button.instate(['disabled'])
    assert '追踪完成' in dialog.status.get() and '上次进程已退出' not in dialog.status.get(),dialog.status.get()
    assert dialog.index.query()[0]==546
    capture(dialog,out/'restored-register-progress.png')
    assert not errors,errors
    write_graph(out/'acceptance.json',dict(passed=True,snapshot_stage=snapshot['stage'],log_stage='finished',
                stale_snapshot_recovered=snapshot['stage']!='finished',exact_rows=546,callback_errors=errors))
    dialog.close();app.close()


if __name__=='__main__':main()
