"""VM acceptance: complete reset networks, independent modes and native Tk."""
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.gui import ClockTreeApp, RuleDialog, read_json, write_json
from capture_x11 import x11_image
from make_reset_fixture import generate
from make_flexible_fixture import generate as generate_clock


def pump(app,condition=lambda:True,timeout=180):
    # This is a test deadline, never an extraction runtime limit.
    started=time.monotonic()
    while not condition():
        app.update()
        if time.monotonic()-started>timeout:raise AssertionError(app.status.get())
        time.sleep(.015)
    for _ in range(10):
        app.update()
        time.sleep(.02)


def capture(window,path):
    window.update()
    picture=x11_image()
    x,y=window.winfo_rootx(),window.winfo_rooty()
    picture.crop((x,y,x+window.winfo_width(),y+window.winfo_height())).save(str(path))


def compare(graph,golden):
    assert graph['tree_type']==graph['summary']['tree_type']=='reset'
    assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid'],graph['diagnostics']
    assert sorted(n['id'] for n in graph['nodes'])==golden['nodes']
    assert sorted([e['source'],e['source_pin'],e['target'],e['target_pin']] for e in graph['edges'])==golden['edges']
    assert sorted(n['id'] for n in graph['nodes'] if n['terminal'])==golden['terminals']
    assert Counter(n['kind'] for n in graph['nodes'])=={'mux':4,'rsy_sync':8}
    assert len(graph['reset_sources'])==3 and 'clock_sources' not in graph
    assert all('clk_i' not in source['id'] for source in graph['reset_sources'])


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default=str(ROOT/'validation/reset-gui'))
    parser.add_argument('--extract',action='store_true')
    args=parser.parse_args()
    out=Path(args.out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    configs,goldens={},{}
    for count in (2,10):configs[count],goldens[count]=generate(out/'fixtures'/str(count),count)
    errors=[]
    app=ClockTreeApp(runs=out/'gui-runs')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    checks={'tk':app.tk.call('info','patchlevel')}
    try:
        pump(app)
        assert app._active_tree_type=='clock' and len(app.layers)==2
        app.top_var.set('unfinished_clock_draft')
        app.search_var.set('clock_only')
        app.nav_query.set('clock_only')
        app.add_layer_button.invoke()
        app.analysis_combo.current(1)
        app.analysis_combo.event_generate('<<ComboboxSelected>>')
        pump(app)
        assert app._active_tree_type=='reset' and len(app.layers)==2 and app.graph is None
        assert not app.search_var.get() and not app.nav_query.get()
        assert app.diagram_name.get()=='复位树架构图'
        assert app.run_button.cget('text')=='开始提取全部复位树'
        capture(app,out/'reset-default-two.png')
        for _ in range(18):app.add_layer_button.invoke()
        assert len(app.layers)==20
        for _ in range(18):app.remove_layer_button.invoke()
        assert len(app.layers)==2
        app.switch_tree_type('clock')
        assert app.top_var.get()=='unfinished_clock_draft' and len(app.layers)==3
        app.switch_tree_type('reset')
        assert len(app.layers)==2 and app.graph is None
        saved=[]
        dialog=RuleDialog(app,2,{'module':'reset_sync','kind':'rsy_sync',
                               'reset_inputs':['rst_ni','other_rst'],'reset_output':'rst_no'},saved.append)
        assert tuple(dialog.kind_combo.cget('values'))==('MUX','RSY_SYNC')
        dialog.save()
        assert '一个关注输入口' in dialog.error.get() and not saved
        dialog.fields['reset_inputs'].set('rst_ni')
        pump(app)
        capture(dialog,out/'reset-sync-rule.png')
        dialog.save()
        assert saved[0]['kind']=='rsy_sync' and 'clock_inputs' not in saved[0]
        library=app.open_device_library()
        pump(app)
        capture(library,out/'reset-device-library.png')
        library.destroy()
        if (ROOT/'examples/reset_tree/resettree.json').is_file():
            app.open_example()
            pump(app,lambda:not app.busy)
            compare(app.graph,goldens[2])
            assert app.graph['metadata']['recorded_example']
            checks['bundled_reset_example']=True
        checks['controls']={'default_layers':2,'add_to_20':True,'independent_unsaved_clock_draft':True,
                            'only_mux_and_rsy_sync':True,'single_reset_input_validation':True}
        if args.extract:
            for count in (2,10):
                app.set_config(configs[count])
                app.save_config(out/'reset-config-{}.json'.format(count))
                app.run_button.invoke()
                assert app.busy,errors
                assert str(app.analysis_combo.cget('state'))=='disabled'
                beats=[0]
                def heartbeat():
                    beats[0]+=1
                    if app.busy:app.after(25,heartbeat)
                app.after(25,heartbeat)
                pump(app,lambda:not app.busy)
                assert not errors,errors
                compare(app.graph,goldens[count])
                assert app.graph_file.name=='resettree.json'
                assert not (app.job_dir/'clocktree.json').exists()
                assert app.diagram.model.layer_count==count
                assert len(app.diagram.model.positions)==12
                traces=[json.loads(line) for line in (app.job_dir/'raw.jsonl').read_text().splitlines()]
                trace_pins={e['pin'] for e in traces if e['event']=='trace'}
                assert trace_pins=={'rst0_ni','rst1_ni','rst_ni','rst_no'}
                app.export_svg(out/'reset-{}.svg'.format(count))
                svg=ET.parse(str(out/'reset-{}.svg'.format(count)))
                assert len(svg.findall('.//{http://www.w3.org/2000/svg}g[@data-device-kind="rsy_sync"]'))==8
                app.show_log(False)
                capture(app,out/'reset-architecture-{}.png'.format(count))
                checks['real_npi_{}_layers'.format(count)]={'summary':app.graph['summary'],'golden_full_sets':True,
                                                           'clock_pin_excluded':True,'ui_heartbeat_ticks':beats[0],
                                                           'run':str(app.job_dir)}
            target=next(n for n in app.diagram.model.original.values() if n['layer']==10)
            app.diagram.locate(target)
            pump(app)
            assert 'RSY_SYNC' in app.prop_text.get('1.0','end')
            app.connections.selection_set('0')
            app.connections.event_generate('<<TreeviewSelect>>')
            pump(app)
            assert 'npiNl' in app.evidence_text.get('1.0','end')
            capture(app,out/'reset-sync-evidence.png')
            app.export_result(out/'reset-full-export.json')
            compare(read_json(out/'reset-full-export.json'),goldens[10])
            last=app.graph_file.parent.name
            app.runs_tree.selection_set(last)
            app.open_history()
            pump(app,lambda:not app.busy)
            compare(app.graph,goldens[10])
            assert all(name.startswith('rst_') for name in app.runs_tree.get_children())
            # The clock example is a previously recorded result; this checks
            # independent workspaces, without claiming a new clock extraction.
            app.switch_tree_type('clock')
            assert app.top_var.get()=='unfinished_clock_draft' and len(app.layers)==3 and app.graph is None
            assert not app.runs_tree.get_children()
            app.open_example()
            pump(app,lambda:not app.busy)
            assert len(app.graph['nodes'])==13 and app._active_tree_type=='clock'
            app.top_var.set('clock_draft_to_restore')
            app.add_layer_button.invoke()
            app.switch_tree_type('reset')
            compare(app.graph,goldens[10])
            assert len(app.layers)==10 and app.analysis_var.get()=='复位树'
            app.switch_tree_type('clock')
            assert app.top_var.get()=='clock_draft_to_restore' and len(app.layers)==3
            assert len(app.graph['nodes'])==13 and app._config_dirty
            app.switch_tree_type('reset')
            compare(app.graph,goldens[10])
            checks['separate_workspaces']={'both_graphs_restored':True,'unsaved_edits_restored':True,
                                            'history_filtered_by_tree_type':True,'history_resettree_open':True}
        assert not errors,errors
    finally:app.destroy()
    if args.extract:
        repeat=out/'repeat-current-directory'
        repeat.mkdir(exist_ok=True)
        environment=dict(os.environ,PYTHONPATH=str(ROOT))
        command=[sys.executable,'-m','clocktree','extract','--config',str(out/'fixtures/10/config.json')]
        for _ in range(2):
            subprocess.check_call(command,cwd=str(repeat),env=environment,stdout=subprocess.DEVNULL)
            compare(read_json(repeat/'resettree.json'),goldens[10])
        generate_clock(out/'fixtures/clock',2)
        clock_command=command[:-1]+[str(out/'fixtures/clock/config.json')]
        subprocess.check_call(clock_command,cwd=str(repeat),env=environment,stdout=subprocess.DEVNULL)
        assert not (repeat/'resettree.json').exists()
        assert read_json(repeat/'clocktree.json')['summary']['connectivity_complete']
        subprocess.check_call(command,cwd=str(repeat),env=environment,stdout=subprocess.DEVNULL)
        compare(read_json(repeat/'resettree.json'),goldens[10])
        assert not (repeat/'clocktree.json').exists()
        assert list((repeat/'.clocktree-history').glob('*/resettree.json'))
        assert list((repeat/'.clocktree-history').glob('*/clocktree.json'))
        checks['same_directory_repeat_and_modes']={'reset_repeats':True,'clock_then_reset':True,
                                                  'both_result_names_archived':True,'stale_opposite_result_removed':True}
    checks.update({'pass':True,'callback_errors':errors,'screenshots':'actual native Tk X11 pixels'})
    write_json(out/'check.json',checks)
    print(json.dumps(checks,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
