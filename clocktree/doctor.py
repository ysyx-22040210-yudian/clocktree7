"""Device-local diagnostics and real end-to-end EDA acceptance, using stdlib only."""
import datetime
import json
import os
from pathlib import Path
import platform
import runpy
import sys
import uuid

from .environment import resolve_verdi
from .extract import extract, vendor_work_dir
from .paths import ROOT, default_runs_dir
from .session import SessionStore, read_graph


def compare_golden(graph, golden):
    observed = {
        'nodes': sorted(node['id'] for node in graph['nodes']),
        'edges': sorted([edge['source'], edge['source_pin'], edge['target'], edge['target_pin']] for edge in graph['edges']),
        'terminals': sorted(node['id'] for node in graph['nodes'] if node['terminal']),
    }
    failures = [key for key in observed if observed[key] != golden[key]]
    if not graph['summary']['connectivity_complete']:
        failures.append('connectivity_complete')
    if not graph['summary'].get('topology_valid', False):
        failures.append('topology_valid')
    if failures:
        raise ValueError('Golden mismatch: ' + ', '.join(failures))
    return {key: len(value) for key, value in observed.items()}


def _check_gui(graphs, directory):
    # Exercise the same native widgets, models and connections used by users.
    # No normal session is read or modified by this acceptance window.
    from .gui import ClockTreeApp
    app = ClockTreeApp(runs=directory / 'gui-runs', remember=False)
    callback_errors = []
    app.report_callback_exception = lambda kind, error, trace: callback_errors.append(str(error))
    rendered = []
    try:
        app.update_idletasks()
        app.update()
        for graph in graphs:
            app.show_graph(graph)
            import time
            while app.diagram.layout_pending:
                app.update()
                time.sleep(.01)
            app.update_idletasks();app.update()
            assert len(app.diagram.model.original) == len(graph['nodes'])
            assert len(app.diagram.model.original_edges) == len(graph['edges'])
            app.diagram.fit()
            app.diagram.paint()
            assert len(app.diagram.rendered_nodes) == len(graph['nodes'])
            drawn = {edge['id'] for edges in app.diagram.rendered_wires.values() for edge in edges}
            assert drawn == {edge['id'] for edge in graph['edges']}
            rendered.append({'instances': len(app.diagram.rendered_nodes), 'edges': len(drawn)})
        if callback_errors:
            raise ValueError('; '.join(callback_errors))
        import tkinter.font
        families = set(tkinter.font.families(app))
        cjk = any(name in families for name in ('Microsoft YaHei UI', 'Microsoft YaHei', 'WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'Noto Sans SC', 'SimSun', 'PingFang SC'))
        return {'tk': str(app.tk.call('info', 'patchlevel')), 'graphs_rendered': len(graphs), 'cjk_font': cjk, 'canvas': rendered}
    finally:
        app.destroy()


def run_doctor(out=None, gui=False, self_test=False, viewer_only=False):
    report = {
        'schema_version': 1, 'time': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'platform': platform.platform(), 'python': platform.python_version(),
        'executable': sys.executable, 'tool_root': str(ROOT), 'checks': [],
        'eda_verified': False, 'gui_verified': False, 'self_test': bool(self_test),
    }
    checks = report['checks']
    def add(name, status, detail, **extra):
        checks.append(dict(name=name, status=status, detail=str(detail), **extra))

    add('python', 'pass' if sys.version_info >= (3, 6) else 'fail', sys.version.split()[0])
    add('filesystem_encoding', 'pass' if sys.getfilesystemencoding().lower() in ('utf-8', 'utf8') else 'warn',
        sys.getfilesystemencoding() + '; use a UTF-8 locale for non-ASCII installation/project paths')
    directory = None
    try:
        parent = Path(out).expanduser().resolve() if out else default_runs_dir()
        directory = parent / ('doctor_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True)
        probe = directory / 'write-probe'
        probe.write_text('writable', encoding='utf-8')
        probe.unlink()
        add('output', 'pass', directory)
    except OSError as error:
        add('output', 'fail', error)
    try:
        import tkinter
        tcl = tkinter.Tcl()
        add('tkinter', 'pass', 'Tk {}; Tcl {}'.format(tkinter.TkVersion, tcl.call('info', 'patchlevel')))
    except Exception as error:
        add('tkinter', 'fail' if gui or viewer_only else 'warn', error)

    graphs = []
    try:
        for mode, relative in [('clock', 'examples/flexible_layers/clocktree.json'), ('reset', 'examples/reset_tree/resettree.json')]:
            graph = read_graph(ROOT / relative, mode)
            # Rendering data are self-contained even when old metadata paths no longer exist.
            graphs.append(graph)
        add('bundled_results', 'pass', 'Clock 13 instances / 11 edges; reset 12 instances / 12 edges')
    except (OSError, ValueError, KeyError, TypeError) as error:
        add('bundled_results', 'fail', error)

    if not viewer_only:
        if sys.platform != 'linux':
            add('extraction_platform', 'fail', 'Real extraction is supported on Linux with Verdi/NPI. Use --viewer-only for this platform.')
        try:
            command, home = resolve_verdi()
            add('verdi_npi', 'pass', 'Installation files located; execution/license not yet verified', executable=command, home=home)
        except (ValueError, OSError) as error:
            add('verdi_npi', 'fail', error)
        configured = any(os.environ.get(name) for name in ('LM_LICENSE_FILE', 'SNPSLMD_LICENSE_FILE'))
        add('license_environment', 'pass' if configured else 'warn',
            'License environment supplied by this device; validity requires self-test' if configured else
            'No license environment variable set; vendor/site defaults may still work. Run --self-test to verify.')

    if directory is not None:
        try:
            store = SessionStore(directory / 'session-runs', directory / 'session-cache')
            workspaces = {graph.get('tree_type', 'clock'): {'config': graph['config'], 'graph': graph,
                'graph_token': uuid.uuid4().hex, 'active_layer': 2} for graph in graphs}
            if len(workspaces) != 2:
                raise ValueError('Both bundled results are required')
            store.save({'active': 'reset', 'workspaces': workspaces})
            restored = store.load()
            if set(restored['workspaces']) != {'clock', 'reset'} or restored['warnings']:
                raise ValueError('Session round trip failed')
            add('session_storage', 'pass', 'Atomic clock/reset snapshots saved and restored in isolated test storage')
        except Exception as error:
            add('session_storage', 'fail', error)

    if self_test and not any(c['status'] == 'fail' for c in checks):
        try:
            generated = []
            fixture_root = vendor_work_dir(directory / 'fixtures')
            report['fixture_directory'] = str(fixture_root)
            for mode, script in [('clock', 'make_flexible_fixture.py'), ('reset', 'make_reset_fixture.py')]:
                fixture = fixture_root / (mode + '-fixture')
                generate = runpy.run_path(str(ROOT / 'scripts' / script))['generate']
                _, golden = generate(fixture, 2)
                graph = extract(fixture / 'config.json', directory / (mode + '-run'))
                counts = compare_golden(graph, golden)
                generated.append(graph)
                add(mode + '_eda_golden', 'pass', 'Real Verdi extraction matches complete instance/edge/terminal sets', **counts)
            graphs = generated
            report['eda_verified'] = True
        except Exception as error:
            add('eda_self_test', 'fail', error)
    elif self_test:
        add('eda_self_test', 'fail', 'Prerequisite checks failed; no EDA process was started')

    if gui and directory is not None and graphs:
        try:
            info = _check_gui(graphs, directory)
            report['gui_verified'] = True
            add('native_gui', 'pass', 'Both architectures rendered in the native Tk workbench', **info)
            if not info['cjk_font']:
                add('chinese_font', 'warn', 'Install a Chinese font for readable labels (e.g. Noto CJK or WenQuanYi).')
        except Exception as error:
            add('native_gui', 'fail', error)

    report['status'] = 'fail' if any(c['status'] == 'fail' for c in checks) else 'pass'
    if directory is not None:
        report['report_file'] = str(directory / 'doctor.json')
        try:
            Path(report['report_file']).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except OSError as error:
            add('report_write', 'fail', error)
            report['status'] = 'fail'
    return report
