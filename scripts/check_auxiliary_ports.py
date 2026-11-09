"""Real Verdi 2018/Tk annotation-port acceptance; stdlib, Python 3.6+."""
import argparse
import copy
import importlib
import json
from pathlib import Path
import re
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.graph import read_events
from clocktree.storage import write_graph
from clocktree.session import read_graph
from clocktree.devices import node_height, connection_label
from check_target_mode import run, edges


def fixture(directory):
    directory.mkdir(parents=True,exist_ok=True)
    (directory/'aux.sv').write_text('''
module ctl_mux(input a,b,choose_src,output o); assign o=choose_src?b:a; endmodule
module ctl_gate(input i,enable_ctl,output o); assign o=i; endmodule
module ctl_div #(parameter W=4)(input i,input [W+1:2] ratio_cfg,output o,locked_o);
assign o=i; assign locked_o=|ratio_cfg; endmodule
module aux_top(input a,b,enable,input [3:0] cfg_sel,input [7:0] ratio,output c0,c1,output [1:0] done);
wire mclk0,mclk1,gclk0,gclk1;
ctl_mux m0(.a(a),.b(b),.choose_src(cfg_sel[1]),.o(mclk0));
ctl_mux m1(.a(b),.b(a),.choose_src(1'b0),.o(mclk1));
ctl_gate g0(.i(mclk0),.enable_ctl(enable & cfg_sel[0]),.o(gclk0));
ctl_gate g1(mclk1,enable,gclk1);
ctl_div d0(.i(gclk0),.ratio_cfg(ratio[5:2]),.o(c0),.locked_o(done[0]));
ctl_div d1(.i(gclk1),.ratio_cfg(4'h5),.o(c1),.locked_o());
endmodule
''',encoding='utf-8')
    (directory/'files.f').write_text(str(directory/'aux.sv')+'\n')
    rules=[{'module':'ctl_mux','kind':'mux','clock_inputs':['a','b'],'clock_output':'o','aux_ports':{'pll_sel':'choose_src'}},
           {'module':'ctl_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o','aux_ports':{'gt':'enable_ctl'}},
           {'module':'ctl_div','kind':'div','clock_inputs':['i'],'clock_output':'o','aux_ports':{'div':'ratio_cfg','sw_done':'locked_o'}}]
    config={'design':{'top':'aux_top','filelist':str(directory/'files.f')},'memory_limit_gib':4,
            'layers':[{'index':i+1,'hierarchy':'aux_top','modules':[rule]} for i,rule in enumerate(rules)]}
    write_graph(directory/'config.json',config)
    return config


def annotations(graph):
    return {n['id']:{'aux_ports':n.get('aux_ports',{}),'connections':{p:n['port_connections'][p] for p in n.get('aux_ports',{}).values()}} for n in graph['nodes']}


def check(graph,directory,mode):
    nodes={n['id'].split('.')[-1]:n for n in graph['nodes']}
    assert len(nodes)==6 and len(graph['edges'])==4
    expected=[['aux_top.m0','o','aux_top.g0','i'],['aux_top.g0','o','aux_top.d0','i'],
              ['aux_top.m1','o','aux_top.g1','i'],['aux_top.g1','o','aux_top.d1','i']]
    assert edges(graph)==sorted(expected)
    count=8 if mode=='target' else 14
    assert graph['summary']['expected_pin_traces']==graph['summary']['observed_pin_traces']==count
    assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid']
    observed={(e['instance'],e['pin']) for e in read_events(directory/'raw.jsonl') if e['event'] in ('trace','trace_begin')}
    assert not any(pin in ('choose_src','enable_ctl','ratio_cfg','locked_o') for _,pin in observed)
    assert all(e['source_pin']=='o' and e['target_pin']=='i' for e in graph['edges'])
    for key,pin,expression in [('m0','choose_src','cfg_sel[1]'),('g0','enable_ctl','(enable & cfg_sel[0])'),
                               ('g1','enable_ctl','enable'),('d0','ratio_cfg','ratio[5:2]'),('d0','locked_o','done[0]')]:
        value=nodes[key]['port_connections'][pin]
        assert re.sub(r'\s+','',value['expression'])==re.sub(r'\s+','',expression),(key,pin,value)
        assert value['direction']==('npiOutput' if pin=='locked_o' else 'npiInput')
    assert nodes['m1']['port_connections']['choose_src']['status']=='connected'
    assert nodes['m1']['port_connections']['choose_src']['expression'] in ("'b0","1'b0")
    assert nodes['d1']['port_connections']['ratio_cfg']['status']=='connected'
    assert nodes['d1']['port_connections']['ratio_cfg']['expression']!=nodes['d0']['port_connections']['ratio_cfg']['expression']
    assert nodes['d1']['port_connections']['locked_o']['status']=='unconnected'
    assert len(graph['diagnostics'])==1 and graph['diagnostics'][0]['code']=='aux_port_unconnected'
    return nodes


def native(out):
    config=fixture(out/'fixture');reports=[];baseline={}
    for mode in ('target','full'):
        cfg=copy.deepcopy(config);cfg['extraction_mode']=mode
        directory=out/mode;graph,report=run(cfg,directory);check(graph,directory,mode)
        baseline[mode]=graph
        no_aux=copy.deepcopy(cfg)
        for layer in no_aux['layers']:
            for rule in layer['modules']:rule.pop('aux_ports')
        plain,unused=run(no_aux,out/(mode+'-without-aux'))
        assert edges(plain)==edges(graph) and {n['id'] for n in plain['nodes']}=={n['id'] for n in graph['nodes']}
        assert plain['summary']['observed_pin_traces']==graph['summary']['observed_pin_traces']
        reports.append(dict(case=mode,passed=True,trace_seeds_unchanged=True,annotations=annotations(graph),**report))
    bad=copy.deepcopy(config)
    bad['layers'][1]['modules'][0]['aux_ports']={'gt':'not_a_port'}
    bad['layers'][2]['modules'][0]['aux_ports']={'div':'locked_o','sw_done':'ratio_cfg'}
    graph,report=run(bad,out/'invalid-ports')
    codes=[d['code'] for d in graph['diagnostics']]
    assert codes.count('aux_port_missing')==2 and codes.count('aux_port_direction')==4,codes
    assert edges(graph)==edges(baseline['target']) and graph['summary']['connectivity_complete']
    reports.append(dict(case='missing-and-wrong-direction',passed=True,**report))
    for mode in ('target','full'):
        cfg=copy.deepcopy(config);cfg.update(tree_type='reset',extraction_mode=mode)
        for layer in cfg['layers']:
            for rule in layer['modules']:
                rule['reset_inputs']=rule.pop('clock_inputs');rule['reset_output']=rule.pop('clock_output')
                if rule['kind']!='mux':rule['kind']='rsy_sync';rule.pop('aux_ports')
        graph,report=run(cfg,out/('reset-'+mode))
        assert edges(graph)==edges(baseline[mode]) and graph['summary']['connectivity_complete']
        assert sum(bool(n.get('aux_ports')) for n in graph['nodes'])==2
        reports.append(dict(case='reset-'+mode,passed=True,**report))
    # Rotate real Verdi after committed module records. Previously collected
    # annotations must survive while only primary trace handles are restored.
    from check_module_checkpoints import ROTATE
    extraction=importlib.import_module('clocktree.extract');original=extraction.run_verdi
    def instrument(command,work,*args,**kwargs):
        path=Path(work)/'clocktree-extract.tcl';text=path.read_text(encoding='utf-8')
        path.write_text(text.replace('if {[catch {ct_main} message]}',ROTATE+'\nif {[catch {ct_main} message]}'),encoding='utf-8')
        return original(command,work,*args,**kwargs)
    extraction.run_verdi=instrument
    try:
        for mode in ('target','full'):
            cfg=copy.deepcopy(config);cfg['extraction_mode']=mode;directory=out/('rotated-'+mode)
            graph,report=run(cfg,directory);check(graph,directory,mode)
            assert graph['metadata']['memory_protection']['restart_count']==3
            assert annotations(graph)==annotations(baseline[mode])
            reports.append(dict(case='rotated-'+mode,passed=True,restarts=3,annotations_equal=True,**report))
    finally:extraction.run_verdi=original
    write_graph(out/'acceptance.json',{'passed':True,'native_verdi':True,'cases':reports})


def gui(out,phase,capture_screenshots=True):
    from clocktree.gui import ClockTreeApp,RuleDialog
    from clocktree.issue_view import IssueDialog
    from check_workbench_gui import pump
    from check_indexed_gui import capture
    def screenshot(window,path):
        if capture_screenshots:capture(window,path)
    errors=[];app=ClockTreeApp(runs=out/'gui-runs',session_dir=out/'gui-session',remember=True)
    app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *args:errors.append(''.join(traceback.format_exception(*args)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if phase=='gui-write':
        graph=read_graph(out/'target/clocktree.json');app.show_graph(graph);pump(app)
        for i,layer in enumerate(app.layers,1):
            rule=layer['modules'][0];saved=[];editor=RuleDialog(app,i,rule,saved.append);pump(app)
            assert all(editor.aux_fields[k].get()==v for k,v in rule['aux_ports'].items())
            if rule['kind']=='div':
                screenshot(editor,out/'div-editor.png')
                assert editor.winfo_rooty()+editor.winfo_height()<=app.winfo_screenheight()
            editor.save();assert saved[0]['aux_ports']==rule['aux_ports']
        rule=app.layers[2]['modules'][0];saved=[];editor=RuleDialog(app,3,rule,saved.append)
        editor.aux_fields['div'].set('i');editor.save();assert not saved and '重合' in editor.error.get()
        editor.aux_fields['div'].set('');editor.aux_fields['sw_done'].set('');editor.save()
        assert 'aux_ports' not in saved[0] and saved[0]['clock_output']=='o'
        app.diagram.locate(app.diagram.model.original['aux_top.d0']);pump(app)
        assert app.diagram.model.positions['aux_top.d0'][3]==node_height(app.diagram.model.original['aux_top.d0'])
        props=app.prop_text.get('1.0','end');assert 'ratio[5:2]' in props and 'done[0]' in props and '不参与追踪' in props
        texts=[app.diagram.canvas.itemcget(item,'text') for item in app.diagram.canvas.find_all() if app.diagram.canvas.type(item)=='text']
        assert 'ratio[5:2]' in texts and 'done[0]' in texts
        assert any('ratio_cfg' in tip and '不追踪' in tip for tip in app.diagram.port_tooltips.values())
        app.export_svg(out/'auxiliary.svg');assert 'done[0]' in (out/'auxiliary.svg').read_text(encoding='utf-8')
        screenshot(app,out/'div-details.png')
        app.toggle_focus();pump(app);app.diagram.fit();pump(app);screenshot(app,out/'auxiliary-workbench.png');app.toggle_focus();pump(app)
        bad=read_graph(out/'invalid-ports/clocktree.json');app.show_graph(bad);pump(app)
        issues=IssueDialog(app);pump(app,lambda:issues.ready)
        assert issues.index.query()[0]==6
        row=next(row for row in issues.index.query()[2] if row[6]=='not_a_port')
        app.locate_issue(row);pump(app);assert app.diagram.issue_port==(row[5],row[6])
        issues.close();app.show_graph(graph);pump(app)
        app._save_session_now();pump(app)
    else:
        assert app.graph and annotations(app.graph)==annotations(read_graph(out/'target/clocktree.json'))
        assert app.get_config()['layers'][2]['modules'][0]['aux_ports']=={'div':'ratio_cfg','sw_done':'locked_o'}
    assert not errors,errors
    write_graph(out/(phase+'.json'),{'passed':True,'phase':phase,'callback_errors':errors,'instances':6,'edges':4,'screenshots':capture_screenshots})
    app.close()
    deadline=time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:app.update();time.sleep(.01)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--phase',choices=['native','gui-write','gui-read'],default='native')
    p.add_argument('--no-screenshots',action='store_true',help='Run all GUI assertions without the optional Pillow screenshot helper')
    a=p.parse_args()
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if a.phase=='native':native(out)
    else:gui(out,a.phase,not a.no_screenshots)
    print('AUXILIARY_PORTS_ACCEPTED '+a.phase,flush=True)


if __name__=='__main__':main()
