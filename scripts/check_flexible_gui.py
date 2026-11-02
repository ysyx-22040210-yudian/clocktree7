"""Exercise flexible layers, DIV, autodetection and repeat extraction on the VM."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.environment import resolve_verdi
from clocktree.gui import ClockTreeApp, RuleDialog, read_json, write_json
from make_flexible_fixture import generate
from capture_x11 import x11_image


def pump(app, predicate=lambda: True, timeout=180):
    # QA-only deadline detects a broken test. The product has no run timeout.
    started = time.monotonic()
    while not predicate():
        app.update()
        if time.monotonic() - started > timeout:
            raise AssertionError('GUI test did not finish: ' + app.status.get())
        time.sleep(.015)
    for _ in range(10):
        app.update()
        time.sleep(.02)


def capture(window, path):
    window.update()
    picture = x11_image()
    x, y = window.winfo_rootx(), window.winfo_rooty()
    picture.crop((x, y, x + window.winfo_width(), y + window.winfo_height())).save(str(path))


def compare(graph, golden):
    assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid'], graph['diagnostics']
    assert sorted(n['id'] for n in graph['nodes']) == golden['nodes']
    assert sorted([e['source'], e['source_pin'], e['target'], e['target_pin']] for e in graph['edges']) == golden['edges']
    assert sorted(n['id'] for n in graph['nodes'] if n['terminal']) == golden['terminals']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(ROOT / 'validation/flexible-gui'))
    parser.add_argument('--extract', action='store_true')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    configs, goldens = {}, {}
    for count in (2, 10):
        configs[count], goldens[count] = generate(out / 'fixtures' / str(count), count)
    os.environ.pop('VERDI_HOME', None)
    executable, home = resolve_verdi()
    checks = {'automatic_verdi': {'command': executable, 'home': home, 'explicit_path_in_config': False,
                                  'VERDI_HOME_supplied': False}}
    errors = []
    app = ClockTreeApp(runs=out / 'gui-runs')
    checks['tk'] = app.tk.call('info', 'patchlevel')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title, error: errors.append(title + ': ' + str(error))
    try:
        pump(app)
        assert len(app.layers) == 2 and app.graph is None
        capture(app, out / 'default-two-layers.png')
        app.open_example()
        pump(app, lambda: not app.busy)
        assert len(app.layers) == 2 and app.graph['summary']['instances'] == 13
        assert sum(n.get('kind') == 'div' for n in app.graph['nodes']) == 3
        app.new_config()
        assert len(app.layers) == 2 and not any(layer['modules'] for layer in app.layers)
        app.hierarchy_var.set('top.keep_first')
        for _ in range(18):
            app.add_layer_button.invoke()
        assert len(app.layers) == 20 and len(app.layer_buttons) == 20
        assert app.layers[0]['hierarchy'] == 'top.keep_first'
        for _ in range(11):
            app.remove_layer_button.invoke()
        assert len(app.layers) == 9 and [l['index'] for l in app.layers] == list(range(1, 10))
        app.select_layer(4)
        app.remove_layer_button.invoke()
        assert len(app.layers) == 8 and app.active_layer == 4
        app.add_layer_button.invoke()
        assert len(app.layers) == 9
        checks['layer_controls'] = {'default': 2, 'added_to': 20, 'middle_delete_and_renumber': True,
                                     'existing_scope_preserved': True}
        app.set_config(configs[2])
        saved = []
        dialog = RuleDialog(app, 2, {'module': 'flex_div', 'kind': 'div', 'clock_inputs': ['i', 'bad'], 'clock_output': 'o'}, saved.append)
        assert 'DIV 分频器' in dialog.kind_combo.cget('values')
        dialog.save()
        assert not saved and '一个关注输入' in dialog.error.get()
        dialog.fields['clock_inputs'].set('i')
        pump(app)
        capture(dialog, out / 'div-rule-dialog.png')
        dialog.save()
        assert saved[0]['kind'] == 'div'
        library = app.open_device_library()
        pump(app)
        capture(library, out / 'four-device-library.png')
        library.destroy()
        environment = app.edit_environment()
        pump(app)
        capture(environment, out / 'automatic-environment.png')
        environment.destroy()
        checks['div_rule_and_preview'] = {'fourth_type': True, 'single_input_validation': True}
        if args.extract:
            for count in (2, 10):
                app.set_config(configs[count])
                app.save_config(out / 'roundtrip-{}.json'.format(count))
                assert len(read_json(out / 'roundtrip-{}.json'.format(count))['layers']) == count
                app.run_button.invoke()
                assert app.busy
                beats = [0]
                def heartbeat():
                    beats[0] += 1
                    if app.busy:
                        app.after(25, heartbeat)
                app.after(25, heartbeat)
                pump(app, lambda: not app.busy)
                assert not errors, errors
                compare(app.graph, goldens[count])
                assert app.diagram.model.layer_count == count
                assert len(app.diagram.model.positions) == 13
                assert Counter(n['kind'] for n in app.graph['nodes']) == {'mux': 2, 'gate': 5, 'occ': 3, 'div': 3}
                app.show_log(False)
                app.export_svg(out / 'architecture-{}.svg'.format(count))
                svg = ET.parse(str(out / 'architecture-{}.svg'.format(count)))
                assert len(svg.findall('.//{http://www.w3.org/2000/svg}g[@data-device-kind="div"]')) == 3
                capture(app, out / 'architecture-{}.png'.format(count))
                checks['real_gui_{}_layers'.format(count)] = {'full_golden_match': True, 'summary': app.graph['summary'],
                                                             'ui_heartbeat_ticks': beats[0], 'run': str(app.job_dir)}
            div = next(n for n in app.diagram.model.original.values() if n['layer'] == 9)
            app.diagram.locate(div)
            pump(app)
            assert 'DIV 分频器' in app.prop_text.get('1.0', 'end')
            capture(app, out / 'div-locate.png')
            assert 'L10' in (out / 'architecture-10.svg').read_text()
        assert not errors, errors
    finally:
        app.destroy()
    if args.extract:
        repeat = out / 'repeat-current-directory'
        repeat.mkdir(exist_ok=True)
        cfg = dict(configs[10], timeout_seconds=1)  # An old timeout setting is intentionally ignored.
        config_path = out / 'legacy-timeout-config.json'
        write_json(config_path, cfg)
        environment = os.environ.copy()
        environment['PYTHONPATH'] = str(ROOT)
        command = [sys.executable, '-m', 'clocktree', 'extract', '--config', str(config_path)]
        workspaces = []
        previous = len(list((repeat / '.clocktree-history').glob('*')))
        existing_attempt = any((repeat / name).is_file() for name in ('raw.jsonl', 'verdi.log', 'clocktree.json', 'summary.json'))
        untouched = repeat / 'user-input.sv'
        untouched.write_text('// user source is retained\n')
        # A stale Verdi compiler error in the user's current directory cannot
        # contaminate new attempts, which use independent scratch directories.
        stale = repeat / 'verdiLog'
        stale.mkdir(exist_ok=True)
        (stale / 'compiler.log').write_text('Error-[STALE] previous attempt\n')
        for _ in range(3):
            subprocess.check_call(command, cwd=str(repeat), env=environment, stdout=subprocess.DEVNULL)
            graph = read_json(repeat / 'clocktree.json')
            compare(graph, goldens[10])
            assert graph['metadata']['elapsed_seconds'] > 1
            workspaces.append(graph['metadata']['working_directory'])
        bad = dict(cfg, design=dict(cfg['design'], top='missing_top'))
        write_json(config_path, bad)
        assert subprocess.call(command, cwd=str(repeat), env=environment, stdout=subprocess.DEVNULL) == 2
        failed = read_json(repeat / 'clocktree.json')
        assert not failed['summary']['connectivity_complete'] and not failed['nodes']
        write_json(config_path, cfg)
        subprocess.check_call(command, cwd=str(repeat), env=environment, stdout=subprocess.DEVNULL)
        compare(read_json(repeat / 'clocktree.json'), goldens[10])
        assert len(set(workspaces)) == 3 and untouched.read_text() == '// user source is retained\n'
        histories = list((repeat / '.clocktree-history').glob('*'))
        assert len(histories) == previous + 4 + int(existing_attempt)
        checks['repeat_current_directory'] = {'success_runs': 4, 'failed_run': 1, 'recovery': True,
                                              'old_timeout_ignored': True, 'previous_results_archived': True,
                                              'stale_compiler_log_ignored': True, 'user_source_unchanged': True,
                                              'full_golden_match': True}
    checks.update({'pass': True, 'callback_errors': errors,
                   'screenshots': 'actual native Tk X11 pixels'})
    write_json(out / 'check.json', checks)
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
