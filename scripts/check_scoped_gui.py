"""Native Tk field, boundary models, exports and fresh-process restoration."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.gui import ClockTreeApp, read_json
from capture_x11 import x11_image


def pump(app, predicate=lambda: True):
    start = time.monotonic()
    while not predicate():
        app.update()
        if time.monotonic() - start > 90:
            raise AssertionError(app.status.get())
        time.sleep(.01)
    for _ in range(12):
        if app._destroying:break
        app.update()
        time.sleep(.02)


def capture(app, path):
    app.update()
    image = x11_image()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    image.crop((x, y, x + app.winfo_width(), y + app.winfo_height())).save(str(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(ROOT / 'validation/scoped-trace'))
    parser.add_argument('--reopen', action='store_true')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    errors = []
    app = ClockTreeApp(runs=out / 'gui-runs', session_dir=out / 'gui-session')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title, error: errors.append(title + ': ' + str(error))
    pump(app, lambda: not app.busy)
    if args.reopen:
        assert app._active_tree_type == 'reset'
        assert app.trace_scope_var.get() == 'scope_top.g[0].domain'
        assert len(app.graph['nodes']) == 5 and len(app.graph['edges']) == 3
        app.switch_tree_type('clock')
        assert app.trace_scope_var.get() == 'scope_top.domain'
        assert len(app.graph['nodes']) == 6 and len(app.graph['edges']) == 4
        capture(app, out / 'gui-reopened.png')
    else:
        for mode, relative in [('clock', 'module/clock-bounded/clocktree.json'),
                               ('reset', 'generate/reset-bounded/resettree.json')]:
            path = out / relative
            graph = read_json(path)
            app.show_graph(graph, path)
            pump(app)
            assert app.trace_scope_var.get() == graph['config']['trace_scope']
            assert app.get_config()['trace_scope'] == graph['config']['trace_scope']
            assert '范围内连接已解析' in app.state.get()
            node = next(n for n in graph['nodes'] if n['scope_output_boundary'])
            app.diagram.locate(node)
            pump(app)
            assert '范围出口' in app.prop_text.get('1.0', 'end')
            assert any('范围出口' in str(app.diagram.canvas.itemcget(i, 'text'))
                       for i in app.diagram.canvas.find_all() if app.diagram.canvas.type(i) == 'text')
            app.export_svg(out / (mode + '-scope.svg'))
            assert '范围出口' in (out / (mode + '-scope.svg')).read_text(encoding='utf-8')
            app.diagram.fit()
            pump(app)
            capture(app, out / ('gui-' + mode + '.png'))
        saved = out / 'gui-config.json'
        app.save_config(saved)
        assert read_json(saved)['trace_scope'] == 'scope_top.g[0].domain'
        app.switch_tree_type('clock')
        assert app.trace_scope_var.get() == 'scope_top.domain'
        app.switch_tree_type('reset')
    app.close()
    pump(app, lambda: app._destroying)
    assert not errors, errors
    if not args.reopen:
        subprocess.check_call([sys.executable, str(Path(__file__).resolve()), '--out', str(out), '--reopen'])
        report = {'pass': True, 'independent_scopes': True, 'bounded_status_and_models': True,
                  'json_svg_export': True, 'fresh_process_restore': True, 'callback_errors': []}
        (out / 'gui-check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))


if __name__ == '__main__':main()
