"""Real Verdi 2018 multi-port wire fixture and independent channel oracle."""
import argparse
import json
from pathlib import Path
import sys
import copy
import importlib
import time
import traceback
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from check_target_mode import run, edges
from clocktree.storage import write_graph


def fixture(directory, tree='clock'):
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'rtl.sv').write_text('''
module src(input i, output o); assign o=i; endmodule
module gate1(input i,en, output o); assign o=i&en; endmodule
module crosswire(input a,b, input [3:0] bus, output x,y,z, output [5:2] q);
 wire alias_a; assign alias_a=a; assign x=b; assign y=alias_a; buf(z,alias_a);
 assign q={bus[1:0],bus[3:2]};
endmodule
module wire_top(input a,b,en,input [3:0] bus,output [6:0] sinks);
 wire sa,sb,x,y,z; wire [5:2] q;
 src s0(.i(a),.o(sa)); src s1(.i(b),.o(sb));
 crosswire w(.a(sa),.b(sb),.bus(bus),.x(x),.y(y),.z(z),.q(q));
 gate1 g0(.i(x),.en(en),.o(sinks[0]));
 gate1 g1(.i(y),.en(en),.o(sinks[1]));
 gate1 g2(.i(z),.en(en),.o(sinks[2]));
 gate1 g3(.i(q[2]),.en(en),.o(sinks[3]));
 gate1 g4(.i(q[3]),.en(en),.o(sinks[4]));
 gate1 g5(.i(q[4]),.en(en),.o(sinks[5]));
 gate1 g6(.i(q[5]),.en(en),.o(sinks[6]));
endmodule
''')
    (directory/'rtl.f').write_text(str(directory/'rtl.sv')+'\n')
    def rule(module,kind,inputs,output):
        return dict(module=module,kind=kind,**{tree+'_inputs':inputs,tree+('_outputs' if kind=='wire' else '_output'):output})
    return {'tree_type':tree,'design':{'top':'wire_top','filelist':str(directory/'rtl.f')},
            'layers':[{'index':1,'hierarchy':'wire_top','modules':[rule('src','mux',['i'],'o')]},
                      {'index':2,'hierarchy':'wire_top','modules':[rule('crosswire','wire',['a','b','bus'],['x','y','z','q'])]},
                      {'index':3,'hierarchy':'wire_top','modules':[rule('gate1','gate' if tree=='clock' else 'rsy_sync',['i'],'o')]}]}


def check(graph):
    nodes={n['id']:n for n in graph['nodes']}
    wire=nodes['wire_top.w']
    pairs=sorted((p['input'],p['output']) for p in wire.get('internal_connections',[]))
    assert pairs==sorted([('a','y'),('a','z'),('b','x'),('bus[0]','q[4]'),('bus[1]','q[5]'),('bus[2]','q[2]'),('bus[3]','q[3]')]), pairs
    assert graph['summary']['connectivity_complete'], (graph['diagnostics'],list(graph['boundaries']))
    assert len(graph['nodes'])==10 and len(graph['edges'])==9, graph['summary']
    assert len(graph['components'])==6, graph['components']
    from clocktree.viewmodel import GraphView
    view=GraphView(graph)
    for terminal,source in [('g0','s1'),('g1','s0'),('g2','s0')]:
        ids=view.path_edges('wire_top.'+terminal,'upstream')
        found={view.original_edges[i]['source'] for i in ids}
        assert found=={'wire_top.w','wire_top.'+source}, (terminal, found)
    return {'pairs':pairs,'summary':graph['summary']}


def matrix(out):
    reports=[]
    def record(name,graph,report):
        reports.append(dict(case=name,**report));write_graph(out/'acceptance.json',{'passed':False,'cases':reports})
    cfg=fixture(out/'fixture')
    for tree,mode in [('clock','target'),('clock','full'),('reset','target'),('reset','full')]:
        local=fixture(out/(tree+'-fixture'),tree);local['extraction_mode']=mode
        graph,report=run(local,out/(tree+'-'+mode));report.update(check(graph));record(tree+'-'+mode,graph,report)
    bad=copy.deepcopy(cfg)
    source=(out/'fixture/rtl.sv').read_text().replace('buf(z,alias_a);','assign z=~alias_a;').replace('assign x=b;','assign x=a & b;')
    (out/'negative.sv').write_text(source);(out/'negative.f').write_text(str(out/'negative.sv')+'\n');bad['design']['filelist']=str(out/'negative.f')
    graph,report=run(bad,out/'negative')
    wire=next(n for n in graph['nodes'] if n['kind']=='wire')
    assert len(wire['internal_connections'])==5 and not graph['summary']['connectivity_complete']
    assert {b['pin'] for b in graph['boundaries'] if b['status']=='wire_connection_unresolved'}=={'x','z'}
    record('logic-not-direct',graph,report)
    missing=copy.deepcopy(cfg);missing['layers'][1]['modules'][0]['clock_outputs'].append('missing')
    graph,report=run(missing,out/'missing')
    assert not graph['summary']['connectivity_complete'] and any(d.get('pin')=='missing' for d in graph['diagnostics'])
    record('missing-output',graph,report)
    # Inject a genuinely blocking call before one internal trace. The normal
    # external watchdog must roll back its suffix and preserve earlier channels.
    extraction=importlib.import_module('clocktree.extract');runner=extraction.run_verdi
    hook='''
rename ct_l_walk ct_wire_original_walk
proc ct_l_walk {owner pin trace_id {direction driver}} {
 set marker [file join [file dirname $::ct_output] wire-injected]
 if {$direction eq "internal" && $pin eq "y" && ![file exists $marker]} {
  set f [open $marker w];puts $f blocked;close $f
  puts -nonewline $::ct_stream "UNCOMMITTED_WIRE";flush $::ct_stream
  exec /bin/sleep 5
 }
 return [ct_wire_original_walk $owner $pin $trace_id $direction]
}
'''
    def instrument(command,work,*args,**kw):
        path=Path(work)/'target_trace.tcl'
        if 'ct_wire_original_walk' not in path.read_text():path.write_text(path.read_text()+hook)
        return runner(command,work,*args,**kw)
    extraction.run_verdi=instrument
    timeout=copy.deepcopy(cfg);timeout['port_trace_timeout_s']=1
    try:graph,report=run(timeout,out/'timeout')
    finally:extraction.run_verdi=runner
    wire=next(n for n in graph['nodes'] if n['kind']=='wire')
    assert [(c['input'],c['output']) for c in wire.get('internal_connections',[])]==[('b','x')]
    assert graph['summary']['trace_timeout_instances']==1 and wire['internal_trace_status']['y']=='trace_timeout'
    assert 'UNCOMMITTED_WIRE' not in (out/'timeout/raw.jsonl').read_text()
    assert graph['metadata']['memory_protection']['attempts']==2
    record('internal-timeout-rollback',graph,report)
    # Rotate after completed internal ports through the same persistent
    # checkpoint path used by memory protection.
    hook2='''
rename ct_l_walk ct_wire_rotate_walk
proc ct_l_walk {owner pin trace_id {direction driver}} {
 set result [ct_wire_rotate_walk $owner $pin $trace_id $direction]
 if {$direction eq "internal" && $pin in {x y}} {set f [open $::ct_rotate_file w];close $f}
 return $result
}
'''
    def rotate(command,work,*args,**kw):
        path=Path(work)/'target_trace.tcl'
        if 'ct_wire_rotate_walk' not in path.read_text():path.write_text(path.read_text()+hook2)
        return runner(command,work,*args,**kw)
    extraction.run_verdi=rotate
    try:graph,report=run(cfg,out/'rotated')
    finally:extraction.run_verdi=runner
    report.update(check(graph));assert graph['metadata']['memory_protection']['restart_count']==2
    record('internal-checkpoint-rotation',graph,report)
    write_graph(out/'acceptance.json',{'passed':True,'cases':reports})


def stress(out,count):
    out.mkdir(parents=True,exist_ok=True)
    (out/'rtl.sv').write_text('module wirepair(input a,b,output x,y);assign x=b;assign y=a;endmodule\n'+
        'module wire_scale(input a,b,output [%d:0] x,y);\n'% (count-1)+
        '\n'.join('wirepair w%d(.a(a),.b(b),.x(x[%d]),.y(y[%d]));'%(i,i,i) for i in range(count))+'\nendmodule\n')
    (out/'rtl.f').write_text(str(out/'rtl.sv')+'\n')
    cfg={'design':{'top':'wire_scale','filelist':str(out/'rtl.f')},'layers':[{'index':1,'hierarchy':'wire_scale','modules':[
        {'module':'wirepair','kind':'wire','clock_inputs':['a','b'],'clock_outputs':['x','y']}]}]}
    graph,report=run(cfg,out/'run')
    assert graph['summary']['connectivity_complete'] and graph['summary']['instances']==count and graph['summary']['components']==2
    assert all({(c['input'],c['output']) for c in n['internal_connections']}=={('a','y'),('b','x')} for n in graph['nodes'])
    report.update(passed=True,internal_pairs=2*count);write_graph(out/'stress.json',report)


def sv(out,result):
    from clocktree.session import read_graph
    from clocktree.register_trace import create_database
    from clocktree.sv_templates import prepare
    out.mkdir(parents=True,exist_ok=True);graph=read_graph(result)
    registers=out/'registers';registers.mkdir()
    write_graph(registers/'manifest.json',{'request':{'config':graph['config']}})
    db=create_database(registers/'registers.sqlite');db.close()
    plan=prepare(graph,registers,out/'plan')
    rows=plan.branches()[1];assert len(rows)==7
    expected={'g0':('x','b'),'g1':('y','a'),'g2':('z','a'),'g3':('q[2]','bus[2]'),
              'g4':('q[3]','bus[3]'),'g5':('q[4]','bus[0]'),'g6':('q[5]','bus[1]')}
    for row in rows:
        clkip,pll=expected[row['instance'].split('.')[-1]];bindings=json.loads(row['bindings'])
        assert bindings['var_clkip']['value']=='wire_top.'+clkip,(row,bindings)
        assert bindings['var_pll0']['value']=='wire_top.'+pll,(row,bindings)
        assert not json.loads(row['issues']),row
    result=plan.export({'with_mux':'// $var_clkip <- $var_pll0\n','without_mux':'// $var_clkip <- $var_pll0\n'},out/'branches.sv')
    write_graph(out/'acceptance.json',{'passed':True,'branches':7,'bit_mapping':expected,'export':result})


def gui(out,phase,result):
    from clocktree.gui import ClockTreeApp,RuleDialog
    from clocktree.session import read_graph
    from clocktree.branch import build_branch,close_branch
    from clocktree.navigation import jump_neighbour
    from clocktree.devices import wire_details
    from check_workbench_gui import pump
    from check_indexed_gui import capture
    out.mkdir(parents=True,exist_ok=True);errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=True);app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if phase=='gui-write':
        graph=read_graph(result);app.show_graph(graph);pump(app)
        saved=[];editor=RuleDialog(app,2,graph['config']['layers'][1]['modules'][0],saved.append);pump(app)
        assert editor.kind_var.get()=='穿线' and editor.fields['clock_output'].get()=='x, y, z, q'
        capture(editor,out/'wire-rule.png');editor.save();assert saved[0]['clock_outputs']==['x','y','z','q']
        app.diagram.locate(app.diagram.model.original['wire_top.g0']);pump(app)
        assert not errors,errors
        assert app.selected_object is not None,(app.status.get(),app.diagram.selection_target)
        jump_neighbour(app,app.selected_object,'upstream');pump(app)
        assert not errors,errors
        assert app.selected_object is not None,(app.status.get(),app.diagram.selection_target)
        assert app.selected_object['id']=='wire_top.w' and app.selected_object['_focus_pin']=='x'
        assert app.diagram.trace('branch')==2
        jump_neighbour(app,app.selected_object,'upstream');pump(app)
        assert app.selected_object['id']=='wire_top.s1'
        app.diagram.navigate_history();pump(app);assert app.selected_object['id']=='wire_top.w'
        branch=build_branch(app.diagram.model,app.diagram.model.original['wire_top.g0'])
        assert {n['id'] for n in branch['nodes']}=={'wire_top.g0','wire_top.w','wire_top.s1'};close_branch(branch)
        from clocktree.branch_view import BranchDialog
        window=BranchDialog(app,app.diagram.model,app.diagram.model.original['wire_top.g0'])
        pump(app,lambda:window.graph is not None and not window.diagram.layout_pending)
        assert set(window.diagram.model.original)=={'wire_top.g0','wire_top.w','wire_top.s1'}
        capture(window,out/'wire-branch.png');window.close()
        app.diagram.clear_highlight();app.diagram.locate(app.diagram.model.original['wire_top.w']);pump(app)
        assert 'b → x' in app.prop_text.get('1.0','end') and 'bus[0] → q[4]' in app.prop_text.get('1.0','end')
        app.toggle_focus();settled=time.monotonic()+.2;pump(app,lambda:time.monotonic()>=settled)
        app.diagram.locate(app.diagram.model.original['wire_top.w']);pump(app)
        capture(app,out/'wire-workbench.png');app.toggle_focus();pump(app)
        app.export_svg(out/'wire-tree.svg');assert 'b → x' in (out/'wire-tree.svg').read_text()
        app._save_session_now();pump(app)
    else:
        assert app.graph and 'b → x' in wire_details(app.diagram.model.original['wire_top.w'])
        assert app.get_config()['layers'][1]['modules'][0]['clock_outputs']==['x','y','z','q']
    assert not errors,errors
    write_graph(out/(phase+'.json'),{'passed':True,'callback_errors':errors,'tk':app.tk.call('info','patchlevel')})
    app.close();deadline=time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:app.update();time.sleep(.01)


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--tree',default='clock');p.add_argument('--mode',default='target');p.add_argument('--extract-only',action='store_true');p.add_argument('--phase',default='native');p.add_argument('--result');p.add_argument('--count',type=int,default=1000);a=p.parse_args()
    if a.phase=='matrix':matrix(Path(a.out).resolve());return
    if a.phase=='stress':stress(Path(a.out).resolve(),a.count);return
    if a.phase=='sv':sv(Path(a.out).resolve(),a.result);return
    if a.phase.startswith('gui-'):gui(Path(a.out).resolve(),a.phase,a.result);return
    out=Path(a.out).resolve();cfg=fixture(out/'fixture',a.tree);cfg['extraction_mode']=a.mode
    graph,report=run(cfg,out/'run')
    if not a.extract_only:report.update(check(graph))
    write_graph(out/'report.json',report);print(json.dumps(report),flush=True)
if __name__=='__main__':main()
