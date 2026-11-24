"""Run SV macro acceptance on a saved native fixture, or drive the native Tk GUI."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.storage import write_graph
from clocktree.sv_macros import template_macros


def settings(top):
    body = (r'initial #1 $display("CT7_VALUE mode=MODE clock=$clkip bit=%0d gt=%0d '
            + 'continued=sv_\\\ntop.test '
            + r'quote=\"ok\" url=http://$var_clkip/a, tick=`LIT, ) %0s", $var_clkip, $var_gt0, "READY");')
    body = ('// ignored $var_unknown\n`define UNUSED $var_swdone \\\n $var_bad\n'
            '`undef DUT\n/* remove this block */\n' + body + '\n`undefineall')
    return {'with_mux': body.replace('MODE', 'mux'), 'without_mux': body.replace('MODE', 'direct'),
            'macros': [{'name': 'DUT', 'text': top}, {'name': 'LABEL', 'text': '"READY"'}]}


def simulate(directory, rtl, include):
    wrapper = directory/'checks.sv'
    wrapper.write_text(
        'module sv_mirror; wire cd=0, ng3=0; wire [15:0] C=0; clock_sink sink(.clocks({ng3,cd})); endmodule\n'
        'module macro_checks;\n'
        'initial begin force sv_top.cd=1; force sv_top.ng3=1; force sv_top.C=16\'h00c0; end\n'
        '`include "' + include.name + '"\nendmodule\n', encoding='utf-8')
    with (directory/'compile.log').open('w') as log:
        subprocess.check_call(['vcs', '-full64', '-sverilog', '-top', 'sv_top', '-top', 'sv_mirror',
                               '-top', 'macro_checks', str(rtl), str(wrapper), '-o', 'simv'],
                              cwd=str(directory), stdout=log, stderr=subprocess.STDOUT)
    output = subprocess.check_output([str(directory/'simv')], cwd=str(directory), stderr=subprocess.STDOUT)
    (directory/'simulate.log').write_bytes(output)
    rows = sorted(line for line in output.decode('utf-8').splitlines() if line.startswith('CT7_VALUE'))
    assert len(rows) == 2, rows
    if shutil.which('iverilog') and shutil.which('vvp'):
        with (directory/'iverilog.log').open('w') as log:
            subprocess.check_call(['iverilog','-g2012','-s','sv_top','-s','sv_mirror','-s','macro_checks',
                                   '-o','checks.vvp',str(rtl),str(wrapper)], cwd=str(directory),stdout=log,stderr=log)
        iv = subprocess.check_output(['vvp','checks.vvp'],cwd=str(directory)).decode('utf-8')
        assert sorted(line for line in iv.splitlines() if line.startswith('CT7_VALUE')) == rows
    return rows


def vcs(fixture, out):
    # The KDB DUT, register results and plan come from check_sv_templates.py --phase native.
    templates = settings('sv_top')
    plain = dict(templates)
    del plain['macros']
    observed = {}
    for name, config in [('plain', plain), ('macros', templates), ('redirected', templates)]:
        directory = out/name
        directory.mkdir(parents=True, exist_ok=True)
        sv = directory/'clock_branches.sv'
        if name == 'redirected':
            source = (out/'macros'/sv.name).read_text(encoding='utf-8')
            assert source.count('`define DUT(ct7_value=sv_top) ct7_value\n') == 1
            sv.write_text(source.replace('ct7_value=sv_top)', 'ct7_value=sv_mirror)'), encoding='utf-8')
        else:
            write_graph(directory/'templates.json', config)
            result = subprocess.check_output([
                sys.executable, '-m', 'clocktree', 'sv-export', '--plan', str(fixture/'plan/plan.sqlite'),
                '--templates', str(directory/'templates.json'), '--out', str(sv)], cwd=str(ROOT))
            counts = json.loads(result.decode('utf-8'))
            assert (counts['blocks'], counts['skipped']) == (2, 0), counts
            code = sv.read_text(encoding='utf-8')
            assert '`undef' not in code and 'UNUSED' not in code and 'ignored' not in code
            assert code.count('`define ') == len(config.get('macros', []))
        observed[name] = simulate(directory, fixture/'fixture/design.sv', sv)
    assert observed['plain'] == observed['macros']
    for line in observed['plain']:
        assert 'bit=1 gt=1 continued=sv_top.test' in line and 'quote="ok"' in line and 'tick=`LIT, ) READY' in line, line
    assert observed['redirected'] == [s.replace('sv_top.', 'sv_mirror.').replace('bit=1 gt=1', 'bit=0 gt=0')
                                      for s in observed['plain']]
    # A top-level comma cannot be a formal's default. Its sole configured name
    # must still expand both as SV punctuation and as part of a format literal.
    source = 'initial $display("CT7_PUNCT a,b=%0d", 3);'
    macros = template_macros({'with_mux':source,'macros':[{'name':'SEP','text':','}]})
    module = out/'punct.sv'
    module.write_text(macros.header()+'module punct;\n'+macros.apply(source)+'\nendmodule\n',encoding='utf-8')
    with (out/'punct-compile.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-top','punct',str(module),'-o','punct-sim'],
                              cwd=str(out),stdout=log,stderr=log)
    assert b'CT7_PUNCT a,b=3' in subprocess.check_output([str(out/'punct-sim')],cwd=str(out))
    if shutil.which('iverilog') and shutil.which('vvp'):
        subprocess.check_call(['iverilog','-g2012','-o','punct.vvp',str(module)],cwd=str(out))
        assert b'CT7_PUNCT a,b=3' in subprocess.check_output(['vvp','punct.vvp'],cwd=str(out))
    write_graph(out/'comparison.json', observed)
    print('PASS: two real KDB branches, three compiled and executed exports')


def gui(graph_path, out, reopen):
    from clocktree.gui import ClockTreeApp
    from clocktree.session import read_graph
    from clocktree.sv_view import SVDialog
    errors = []
    app = ClockTreeApp(runs=out/'runs', session_dir=out/'session', remember=True)
    app.report_callback_exception = lambda exc, value, trace: errors.append(str(value))
    app.show_error = lambda title, message: errors.append(str(message))

    def pump(done=lambda: True):
        start = time.monotonic()
        while True:
            app.update()
            if done():
                return
            if time.monotonic() - start > 60:
                raise AssertionError('GUI wait: {}'.format(errors or app.status.get()))
            time.sleep(.01)

    pump(lambda: not app.busy)
    if not reopen:
        app.show_graph(read_graph(graph_path))
        pump()
    top = app.graph['config']['design']['top']
    template = 'initial $display("clock=$var_clkip value=%0d", $var_clkip);'
    expected = dict(with_mux=template, without_mux=template, macros=[{'name': 'DUT', 'text': top}])
    dialog = SVDialog(app)
    editor = dialog.macro_editor
    if reopen:
        assert dialog.templates() == expected and dialog.plan
        assert dialog.export_result and dialog.export_result['sv_written']
        assert '`define DUT(ct7_value=' + top + ')' in dialog.output_code.get('1.0', 'end')
    else:
        for pane in dialog.editors.values():
            pane.delete('1.0', 'end')
            pane.insert('1.0', template)
        dialog.tabs.select(editor)
        for name, text in [('DUT', top), ('OLD', 'some.other')]:
            editor.name.set(name)
            editor.text.set(text)
            editor.add_button.invoke()
            pump()
        editor.name.set('DUT')
        editor.update_button.invoke()
        assert '重复' in editor.notice.get() and len(editor.rows) == 2
        editor.name.set('EXTRA')
        editor.update_button.invoke()
        assert editor.rows[1]['name'] == 'EXTRA'
        editor.remove_button.invoke()
        pump()
        assert dialog.templates() == expected
        backup = out/'template-backup.json'
        with patch('clocktree.sv_view.filedialog.asksaveasfilename', return_value=str(backup)):
            dialog.save_templates_button.invoke()
        assert json.loads(backup.read_text(encoding='utf-8')) == expected
        editor.table.selection_set('0')
        pump()
        editor.remove_button.invoke()
        assert not editor.rows
        with patch('clocktree.sv_view.filedialog.askopenfilename', return_value=str(backup)):
            dialog.import_templates()
        assert dialog.templates() == expected
        dialog.analyze_button.invoke()
        pump(lambda: dialog.process.poll() is not None and dialog.analyze_button.instate(['!disabled']))
        assert dialog.plan, dialog.status.get()
        row = next(r for r in dialog.rows.values() if dialog.plan.resolve(r, expected)['ready'])
        dialog.table.selection_set(row['id'])
        dialog.detail()
        assert '`define DUT(ct7_value=' + top + ')' in dialog.preview.get('1.0', 'end')
        with patch('clocktree.sv_view.filedialog.asksaveasfilename', return_value=str(out/'gui.sv')):
            dialog.export_button.invoke()
        pump(lambda: dialog.process.poll() is not None and dialog.export_button.instate(['!disabled']))
        assert dialog.export_result['blocks'] > 0, dialog.status.get()
        code = (out/'gui.sv').read_text(encoding='utf-8')
        assert code.count('`define ') == 1 and '`undef' not in code and '`DUT().' in code
        assert dialog.output_code.get('1.0', 'end-1c') == code
        editor.table.selection_set('0')
        pump()
        editor.name.set('RENAMED')
        editor.update_button.invoke()
        assert '重新生成后生效' in dialog.output_notice.get()
        pump()
        editor.name.set('DUT')
        editor.update_button.invoke()
        assert '重新生成后生效' not in dialog.output_notice.get()
    dialog.close()
    app._save_session_now()
    pump()
    assert not errors, errors
    write_graph(out/('gui-reopen.json' if reopen else 'gui-write.json'), {'passed': True, 'macros': expected['macros']})
    app.close()
    if not app._destroying:
        pump(lambda: app._destroying)
    print('PASS: native Tk ' + ('restored saved rules, plan and SV' if reopen else 'edited, imported and generated SV'))


def scale(plan_path, out):
    import resource
    from clocktree.sv_templates import SVPlan
    plan = SVPlan(plan_path)
    total, first = plan.branches(limit=1)
    assert total and first[0]['clkip']
    prefix = first[0]['clkip'].split('.')[0]
    template = 'initial $display("clock=$var_clkip bit=%b", $var_clkip);'
    config = dict(with_mux=template, without_mux=template, macros=[{'name': 'DUT', 'text': prefix}])
    start = time.monotonic()
    result = plan.export(config, out/'clocks.sv')
    elapsed = time.monotonic() - start
    counts = dict(header=0, other_definitions=0, cleanup=0, comments=0, display=0)
    with (out/'clocks.sv').open(encoding='utf-8') as sv:
        for line in sv:
            if line.startswith('`define '):
                counts['header' if line == '`define DUT(ct7_value={}) ct7_value\n'.format(prefix) else 'other_definitions'] += 1
            counts['cleanup'] += int('`undef' in line)
            counts['comments'] += int('//' in line or '/*' in line)
            counts['display'] += int(line.startswith('initial $display(`DUT('))
    assert result['blocks'] == total and result['skipped'] == 0, result
    assert counts == dict(header=1, other_definitions=0, cleanup=0, comments=0, display=total), counts
    report = dict(terminals=total, seconds=round(elapsed, 3), peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  sv_bytes=(out/'clocks.sv').stat().st_size, counts=counts)
    write_graph(out/'scale.json', report)
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['vcs', 'gui-write', 'gui-reopen', 'scale'])
    parser.add_argument('--source', required=True, type=Path, help='VCS fixture directory, scalar graph for GUI, or large SV plan for scale')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    destination = args.out.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if args.phase == 'vcs':
        vcs(args.source.resolve(), destination)
    elif args.phase == 'scale':
        scale(args.source.resolve(), destination)
    else:
        gui(args.source.resolve(), destination, args.phase == 'gui-reopen')
