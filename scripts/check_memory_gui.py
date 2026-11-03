"""Real Tk controls, configurable RSS guard, cancellation and session restore."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.gui import ClockTreeApp
from clocktree.session import read_graph
from clocktree.storage import write_graph
from capture_x11 import x11_image


def pump(app, predicate=lambda: True):
    start = time.monotonic()
    while not predicate():
        app.update()
        if time.monotonic()-start > 90:
            raise AssertionError(app.status.get())
        time.sleep(.01)
    for _ in range(8):
        if app._destroying:break
        app.update(); time.sleep(.02)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', default=str(ROOT / 'validation/memory-resume/gui'))
    p.add_argument('--reopen', action='store_true')
    args = p.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    errors = []
    app = ClockTreeApp(runs=out / 'runs', session_dir=out / 'session')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title, error: errors.append(title + ': ' + str(error))
    pump(app, lambda: not app.busy)
    if args.reopen:
        assert float(app.memory_limit_var.get()) == 42.5 and len(app.graph['nodes']) == 6
        assert app.active_layer == 3
    else:
        graph = read_graph(ROOT / 'validation/memory-resume/acceptance/clock-baseline/clocktree.json')
        app.show_graph(graph)
        assert float(app.memory_limit_var.get()) == 60
        app.memory_limit_var.set('0.6')
        app.start_extraction()
        pump(app, lambda: not app.busy)
        assert len(app.graph['nodes']) == 6 and len(app.graph['edges']) == 4
        report = app.graph['metadata']['memory_protection']
        assert report['enabled'] and report['limit_gib'] == .6 and report['state'] == 'finished'
        assert 'Verdi 阶段峰值' in app.memory_status.get()
        app.memory_limit_var.set('60')
        assert not app._config_dirty
        pump(app)
        app.update()
        image = x11_image()
        x,y = app.winfo_rootx(),app.winfo_rooty()
        image.crop((x,y,x+app.winfo_width(),y+app.winfo_height())).save(str(out / 'memory-guard.png'))
        # Cancel the CLI while its separate Verdi process group is active.
        old = app.graph
        config = json.loads((ROOT / 'validation/configs/openc910_ordered7_kdb.json').read_text())
        app.set_config(config)
        app.start_extraction()
        def launched():
            try:
                state=json.loads((app.job_dir / 'memory.json').read_text())
                return (app.job_dir / 'raw.jsonl').exists() and state.get('worker_pid')
            except (OSError,ValueError):return False
        pump(app, launched)
        cli_pid = app.process.pid
        worker_pid = json.loads((app.job_dir / 'memory.json').read_text())['worker_pid']
        directory = app.job_dir
        app.cancel_job()
        pump(app, lambda: not app.busy)
        table = subprocess.check_output(['ps','-eo','pgid=,stat='],universal_newlines=True)
        assert not [line for line in table.splitlines() if line.split() and
                    line.split()[0] in (str(cli_pid),str(worker_pid)) and not line.split()[1].startswith('Z')]
        assert not (directory / 'summary.json').exists() and app.graph is old
        draft = copy.deepcopy(old['config'])
        draft['memory_limit_gib'] = 42.5
        app.set_config(draft); app.select_layer(3)
        app.save_config(out / 'saved-config.json')
        assert json.loads((out / 'saved-config.json').read_text())['memory_limit_gib'] == 42.5
    app.close(); pump(app, lambda: app._destroying)
    assert not errors, errors
    if not args.reopen:
        subprocess.check_call([sys.executable,__file__,'--out',str(out),'--reopen'])
        report = {'pass':True,'gui_memory_limit_and_readings':True,'real_npi_guard':True,
                  'both_cli_and_verdi_cancelled':True,'configuration_and_result_restored':True,'callback_errors':[]}
        write_graph(out / 'acceptance.json',report)
        print(json.dumps(report,indent=2))


if __name__ == '__main__':main()
