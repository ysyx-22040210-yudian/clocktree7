"""Run actual Tk controls, a real NPI job, and the full C910 graph on the VM."""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from capture_x11 import x11_image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clocktree.gui import ClockTreeApp, RuleDialog, read_json


def screenshot(app,path):
    # xwd captures the X server's actual rendered pixels, including ttk and fonts.
    app.update()
    picture=x11_image()
    x,y=app.winfo_rootx(),app.winfo_rooty()
    picture.crop((x,y,x+app.winfo_width(),y+app.winfo_height())).save(str(path))


def pump(app,predicate,timeout=30):
    started=time.monotonic()
    while not predicate():
        app.update()
        if time.monotonic()-started>timeout:
            raise AssertionError('Tk condition timed out: {} / {}'.format(app.state.get(),app.status.get()))
        time.sleep(.012)
    for _ in range(5):
        app.update()
        time.sleep(.02)


def native_click(app,x,y):
    c=app.diagram.canvas
    c.event_generate('<ButtonPress-1>',x=round(x),y=round(y))
    c.event_generate('<ButtonRelease-1>',x=round(x),y=round(y))
    app.update()


def controls(widget):
    yield widget
    for child in widget.winfo_children():
        yield from controls(child)


p=argparse.ArgumentParser()
p.add_argument('--extract',action='store_true')
p.add_argument('--cancel',action='store_true',help='Cancel a real large NPI job and verify its process group exits')
p.add_argument('--output',default=str(Path(__file__).resolve().parents[1]/'validation/tk_gui'))
args=p.parse_args()
root=Path(__file__).resolve().parents[1]
out=Path(args.output)
out.mkdir(parents=True,exist_ok=True)
callbacks=[]
app=ClockTreeApp()
app.geometry('1840x1000+20+20')
app.report_callback_exception=lambda *exc:callbacks.append(''.join(traceback.format_exception(*exc)))
app.show_error=lambda title,error:callbacks.append(title+': '+str(error))
checks={}
try:
    pump(app,lambda:app.graph is not None and not app.busy)
    assert len(app.graph['nodes'])==16 and len(app.diagram.model.nodes)==16
    assert len(app.diagram.model.edges)==15
    assert app.run_button.winfo_ismapped() and app.run_button.winfo_y()>0
    app.component_combo.current(1)
    app.component_combo.event_generate('<<ComboboxSelected>>')
    app.update()
    assert app.diagram.model.actual_count==14
    app.component_combo.current(0)
    app.component_combo.event_generate('<<ComboboxSelected>>')
    app.update()
    assert app.diagram.model.actual_count==16
    node=next(n for n in app.graph['nodes'] if n['id'].endswith('n0_mux') and n['layer']==2)
    x,y,w,h=app.diagram.model.positions[node['id']]
    native_click(app,*app.diagram.point(x+w/2,y+h/2))
    assert app.selected_object['id']==node['id']
    assert 'ct_mux' in app.prop_text.get('1.0','end')
    app.select_layer(2)
    original=len(app.layers[1]['modules'])
    app.add_rule()
    dialog=next(w for w in app.winfo_children() if isinstance(w,RuleDialog))
    for key,value in [('module','temporary_test_module'),('clock_inputs','clk_a, clk_b'),('clock_output','clk_o')]:
        dialog.fields[key].set(value)
    dialog.kind_var.set('门控')
    dialog.save()
    assert dialog.winfo_exists() and '一个关注输入' in dialog.error.get()
    dialog.kind_var.set('选择器')
    next(w for w in controls(dialog) if w.winfo_class()=='TButton' and w.cget('text')=='保存规则').invoke()
    assert len(app.layers[1]['modules'])==original+1
    app.rules_tree.selection_set(str(original))
    app.remove_rule()
    assert len(app.layers[1]['modules'])==original
    app.save_config(out/'config-roundtrip.json')
    assert len(read_json(out/'config-roundtrip.json')['layers'])==7
    app.select_layer(1)
    app.export_svg(out/'seven-layers.svg')
    svg=ET.parse(str(out/'seven-layers.svg'))
    assert len(svg.findall('.//{http://www.w3.org/2000/svg}polyline[@class="edge"]'))==15
    assert len(svg.findall('.//{http://www.w3.org/2000/svg}title'))==16
    screenshot(app,out/'seven-layers.png')
    checks['seven_layer_controls']={'instances':16,'edges':15,'components':2,'filter_instances':14,
                                    'canvas_click':True,'module_rule_dialog':True,'type_selection_and_input_validation':True,'config_roundtrip':True,'svg':True}
    if args.extract:
        fixture=root/'validation/fixtures/device-models' if (root/'validation/fixtures/device-models/config.json').is_file() else root/'validation/fixtures/small'
        app.set_config(read_json(fixture/'config.json'))
        beats=[0]
        def beat():
            beats[0]+=1
            if app.busy:app.after(25,beat)
        app.run_button.invoke()
        assert app.busy
        app.after(25,beat)
        pump(app,lambda:not app.busy,timeout=120)
        assert app.last_job and app.state.get()=='全量连接已解析'
        golden=read_json(fixture/'golden.json')
        actual={'nodes':sorted(n['id'] for n in app.graph['nodes']),
                'edges':sorted([e['source'],e['source_pin'],e['target'],e['target_pin']] for e in app.graph['edges']),
                'terminals':sorted(n['id'] for n in app.graph['nodes'] if n['terminal'])}
        assert actual==golden
        assert beats[0]>=10
        screenshot(app,out/'after-npi-extraction.png')
        checks['real_npi_job']={'directory':app.last_job,'golden_full_set_match':True,
                                 'ui_heartbeat_ticks':beats[0],'summary':app.graph['summary']}
        app.show_log(False)
        app.diagram.fit()
        node=next(n for n in app.graph['nodes'] if n['id'].endswith('n0_mux') and n['layer']==2)
        app.diagram.select(node)
        pump(app,lambda:app.diagram._pending is None)
        screenshot(app,out/'clocktree7-desktop.png')
        if fixture.name=='device-models':
            from collections import Counter
            kinds=Counter(n.get('kind') for n in app.graph['nodes'])
            assert kinds=={'occ':10,'gate':4,'mux':2},kinds
            assert all(n.get('kind_source')=='configured' for n in app.graph['nodes'])
            screenshot(app,out/'clocktree-device-models.png')
            before_edges=json.dumps(app.graph['edges'],sort_keys=True)
            app.select_layer(1)
            app.rules_tree.selection_set('0')
            app.edit_rule()
            editor=next(w for w in app.winfo_children() if isinstance(w,RuleDialog))
            editor.kind_var.set('门控')
            editor.save()
            assert app.diagram.model.original['fixture_top.l1.n0_root_a']['kind']=='gate'
            assert json.dumps(app.graph['edges'],sort_keys=True)==before_edges
            app.rules_tree.selection_set('0')
            app.edit_rule()
            editor=next(w for w in app.winfo_children() if isinstance(w,RuleDialog))
            editor.kind_var.set('OCC')
            editor.save()
            assert app.diagram.model.original['fixture_top.l1.n0_root_a']['kind']=='occ'
            assert json.dumps(app.graph['edges'],sort_keys=True)==before_edges
            app.export_result(out/'typed-models-export.json')
            app.export_svg(out/'typed-models.svg')
            typed_svg=ET.parse(str(out/'typed-models.svg'))
            actual_kinds=Counter(g.get('data-device-kind') for g in typed_svg.findall('.//{http://www.w3.org/2000/svg}g[@class="device"]'))
            assert actual_kinds==kinds
            library=app.open_device_library()
            screenshot(library,out/'device-library.png')
            library.destroy()
            app.add_rule()
            editor=next(w for w in app.winfo_children() if isinstance(w,RuleDialog))
            editor.kind_var.set('选择器')
            editor.fields['module'].set('clock_mux')
            editor.fields['clock_inputs'].set('clk0_i, clk1_i')
            editor.fields['clock_output'].set('clk_o')
            screenshot(editor,out/'device-rule-dialog.png')
            editor.destroy()
            checks['device_models']={'kind_counts':dict(kinds),'raw_elab_type_metadata':True,'three_native_symbols':True,
                                     'svg_type_counts':dict(actual_kinds),'appearance_edit_reuses_real_edges':True,'model_library':True}
    started=time.monotonic()
    app.load_result(root/'validation/runs/openc910-ordered7-01/clocktree.json')
    pump(app,lambda:not app.busy and app.graph['summary']['instances']==6984,timeout=60)
    overview=len(app.diagram.model.nodes)
    assert app.diagram.mode=='hierarchy' and 1<overview<200
    screenshot(app,out/'openc910-overview.png')
    app.focus_button.invoke()
    pump(app,lambda:app.focused_view and app.diagram._pending is None)
    screenshot(app,out/'openc910-focus.png')
    app.focus_button.invoke()
    pump(app,lambda:not app.focused_view and app.diagram._pending is None)
    aggregate=next(n for n in app.diagram.model.nodes.values() if n.get('aggregate'))
    app.diagram.select(aggregate)
    app.expand_button.invoke()
    app.update()
    assert app.diagram.scope==aggregate['path']
    app.diagram.reset_scope()
    app.update()
    assert len(app.diagram.model.nodes)==overview
    app.search_var.set('x_ct_rtu_pst_entry_vreg63.x_alloc_gated_clk')
    before=time.monotonic()
    app.locate_button.invoke()
    pump(app,lambda:app.diagram._pending is None)
    locate_seconds=time.monotonic()-before
    assert len(app.diagram.model.nodes)==6984 and len(app.diagram.model.edges)==6983
    assert 'x_ct_rtu_pst_entry_vreg63.x_alloc_gated_clk' in app.selected_object['id']
    selected=app.selected_object
    edge=app.diagram.model.incoming[selected['id']][0]
    points=app.diagram.model.edge_points(edge)
    a,b=points[-2:]
    native_click(app,*app.diagram.point(a[0]+(b[0]-a[0])*.18,a[1]+(b[1]-a[1])*.18))
    assert 'npiNl' in app.evidence_text.get('1.0','end')
    assert app._inspected_edge['id'] in app.diagram.model.original_edges
    app.diagram.select(selected)
    app.highlight_path('upstream')
    pump(app,lambda:app.diagram._pending is None)
    assert app.diagram.highlights
    screenshot(app,out/'openc910-instance.png')
    visible=app.diagram.visible_nodes
    app.export_result(out/'openc910-full-export.json')
    exported=read_json(out/'openc910-full-export.json')
    assert len(exported['nodes'])==6984 and len(exported['edges'])==6983
    app.diagram.reset_scope()
    app.update()
    app.export_svg(out/'openc910-overview.svg')
    checks['full_soc_architecture']={'instances':6984,'edges':6983,'overview_boxes':overview,'layer_expand':True,
                                     'locate_seconds':round(locate_seconds,3),'visible_detail_nodes':visible,
                                     'native_wire_click_evidence':True,'upstream_path_highlight':True,
                                     'full_json_export':True,'overview_svg_export':True,
                                     'total_check_seconds':round(time.monotonic()-started,3)}
    if args.cancel:
        app.set_config(read_json(root/'validation/configs/openc910_ordered7_kdb.json'))
        app.run_button.invoke()
        pump(app,lambda:app.process is not None and (app.job_dir/'raw.jsonl').is_file(),timeout=30)
        pgid=app.process.pid
        cancelled_dir=app.job_dir
        app.cancel_button.invoke()
        pump(app,lambda:not app.busy,timeout=15)
        assert read_json(cancelled_dir/'gui-job.json')['state']=='cancelled'
        assert not (cancelled_dir/'summary.json').exists()
        assert app.graph['summary']['instances']==6984
        process_table=subprocess.check_output(['/usr/bin/ps','-eo','pgid=,stat=,args='],universal_newlines=True)
        alive=[line for line in process_table.splitlines() if line.strip().split(None,2)[0]==str(pgid) and not line.strip().split(None,2)[1].startswith('Z')]
        assert not alive,alive
        checks['cancel_real_npi_job']={'directory':str(cancelled_dir),'state':'cancelled',
                                      'process_group_terminated':True,'previous_graph_preserved':True,'partial_result_not_certified':True}
    assert not callbacks,callbacks
    assert not app.error_messages,app.error_messages
    result={'pass':True,'native_toolkit':'Tk/ttk '+str(app.tk.call('info','patchlevel')),
            'display':os.environ.get('DISPLAY'),'font':app.family,'workspace':str(root),'user':getpass.getuser(),
            'checks':checks,'callback_errors':callbacks}
    (out/'tk-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
finally:
    app.destroy()
