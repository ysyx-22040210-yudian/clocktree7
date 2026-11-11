import copy
import json
from pathlib import Path
import pytest
from clocktree.config import validate_config, write_tcl_config
from clocktree.graph import build_graph
from clocktree.profiles import output_ports
from clocktree.devices import symbol_parts, node_height, output_offset
from clocktree.viewmodel import GraphView
from clocktree.indexed_view import IndexedGraphView
from clocktree.storage import ResultStore,index_graph
from clocktree.branch import build_branch,close_branch
from clocktree.navigation import neighbour_edges
from clocktree.sv_templates import prepare


def config():
    return {'design':{'top':'top','filelist':'x.f'},'layers':[{'index':1,'hierarchy':'top','modules':[
        {'module':'W','kind':'wire','clock_inputs':['a','b'],'clock_outputs':['x','y']},
        {'module':'G','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]}]}


def fixture():
    def inst(name,kind='gate'):
        return dict(event='instance',path='top.'+name,module='W' if kind=='wire' else 'G',kind=kind,layer=1,
                    inputs=['a','b'] if kind=='wire' else ['i'],output=None if kind=='wire' else 'o',
                    **({'outputs':['x','y']} if kind=='wire' else {}))
    events=[{'event':'metadata','extraction_mode':'target'}]+[inst(x) for x in ['s0','s1','g0','g1']]+[inst('w','wire')]
    def trace(name,pin,result,direction='driver'):
        return {'event':'trace','instance':'top.'+name,'pin':pin,'direction':direction,'results':[result]}
    def target(name,pin):return dict(status='target',instance='top.'+name,pin=pin,path=[])
    events += [trace('s0','i',dict(status='input_boundary',endpoint='top.c0')),trace('s1','i',dict(status='input_boundary',endpoint='top.c1')),
               trace('w','a',target('s0','o')),trace('w','b',target('s1','o')),
               trace('g0','i',target('w','x')),trace('g1','i',target('w','y')),
               trace('w','x',target('w','b'),'internal'),trace('w','y',target('w','a'),'internal')]
    for event in events[:]:
        if event['event']=='instance':
            for pin in output_ports(event):events.append(dict(event='output_check',instance=event['path'],pin=pin,status='ok'))
    events.append({'event':'complete'})
    return events,config()


def test_wire_rule_roundtrip_and_validation(tmp_path):
    cfg=config();validate_config(cfg);write_tcl_config(cfg,tmp_path/'rules.tcl','raw.jsonl')
    assert '[list "x" "y"]' in (tmp_path/'rules.tcl').read_text()
    for outputs in ([],['x','x'],['a'],['x',None],'x'):
        bad=copy.deepcopy(cfg);bad['layers'][0]['modules'][0]['clock_outputs']=outputs
        with pytest.raises(ValueError):validate_config(bad)
    bad=copy.deepcopy(cfg);bad['layers'][0]['modules'][0]['kind']='mux'
    with pytest.raises(ValueError):validate_config(bad)


@pytest.mark.parametrize('indexed',[False,True])
def test_channels_branch_navigation_and_render(tmp_path,indexed):
    events,cfg=fixture();graph=build_graph(events,cfg)
    assert graph['summary']['connectivity_complete'] and graph['summary']['components']==2
    if indexed:
        store=ResultStore(tmp_path/'graph.sqlite',True);index_graph(graph,store);graph=store.graph();view=IndexedGraphView(graph)
    else:view=GraphView(graph)
    view.project('instances')
    selection=view.path_edges('top.g0','upstream')
    keys=[r[0] for r in view.store.connection().execute(selection.sql)] if indexed else selection
    assert {(view.original_edges[e]['source'],view.original_edges[e]['target']) for e in keys}=={('top.s1','top.w'),('top.w','top.g0')}
    branch=build_branch(view,view.original['top.g0'])
    assert {n['id'] for n in branch['nodes']}=={'top.g0','top.w','top.s1'}
    wire=next(n for n in branch['nodes'] if n['id']=='top.w')
    assert wire['inputs']==['b'] and wire['outputs']==['x']
    close_branch(branch)
    wire=view.original['top.w'];height=node_height(wire)
    assert output_offset(wire,382,height,'x')!=output_offset(wire,382,height,'y')
    assert {'b → x','a → y'} <= {p.get('text','') for p in symbol_parts(wire,382,height)}
    assert 'data-device-kind="wire"' in view.svg()
    focused=dict(wire,_focus_pin='x')
    assert {e['source'] for e in neighbour_edges(view,focused,'upstream')}=={'top.s1'}
    assert {e['target'] for e in neighbour_edges(view,focused,'downstream')}=={'top.g0'}
    if indexed:store.close()


@pytest.mark.parametrize('status',['unknown_cell','constant','undriven','trace_timeout','trace_skipped_timeout'])
def test_unproven_mapping_not_joined(status):
    events,cfg=fixture()
    next(e for e in events if e.get('direction')=='internal' and e['pin']=='x')['results']=[{'status':status}]
    graph=build_graph(events,cfg);wire=next(n for n in graph['nodes'] if n['kind']=='wire')
    assert [(p['input'],p['output']) for p in wire['internal_connections']]==[('a','y')]
    assert not graph['summary']['connectivity_complete'] and graph['summary']['unresolved']==1
    assert GraphView(graph).path_edges('top.g0','upstream')=={e['id'] for e in graph['edges'] if e['target']=='top.g0'}


def test_node_cycle_across_distinct_wire_channels_is_not_signal_cycle():
    events,cfg=fixture()
    # s0 -> w.a -> w.y -> s1 -> w.b -> w.x -> g0: revisits w, no signal cycle.
    next(e for e in events if e.get('instance')=='top.s1' and e.get('pin')=='i' and e.get('direction')=='driver')['results']=[dict(status='target',instance='top.w',pin='y')]
    graph=build_graph(events,cfg)
    assert not any(d['code']=='cycle_or_cycle_downstream' for d in graph['diagnostics'])
    assert len(GraphView(graph).path_edges('top.g0','upstream'))==4


def test_wire_partial_terminal_and_ambiguous_internal_source():
    events,cfg=fixture()
    events=[e for e in events if e.get('path')!='top.g0' and e.get('instance')!='top.g0']
    graph=build_graph(events,cfg)
    wire=next(n for n in graph['nodes'] if n['kind']=='wire')
    assert wire['terminal_inputs']==['b'] and wire['terminal']
    trace=next(e for e in events if e.get('direction')=='internal' and e['pin']=='x')
    trace['results'].append(dict(status='target',instance='top.w',pin='a'))
    graph=build_graph(events,cfg)
    wire=next(n for n in graph['nodes'] if n['kind']=='wire')
    assert wire['terminal_inputs']==[] and not wire['terminal']
    assert wire['has_unresolved_output'] and not any(c['output']=='x' for c in wire['internal_connections'])


def test_layout_worker_preserves_multi_output_anchors():
    import time
    from clocktree.layout_job import LayoutJob
    events,cfg=fixture();view=GraphView(build_graph(events,cfg));view.project()
    job=LayoutJob(view)
    try:
        deadline=time.monotonic()+30
        while job.poll() is None:
            assert time.monotonic()<deadline
            time.sleep(.01)
        result=job.result()
        starts={tuple(result['routes'][e['id']][0]) for e in view.edges if e['source']=='top.w'}
        assert len(starts)==2
    finally:job.close()


@pytest.mark.parametrize('indexed',[False,True])
def test_network_filter_on_edges_between_shared_wire_instances(tmp_path,indexed):
    events=[{'event':'metadata','extraction_mode':'target'}]
    for name in ('w0','w1'):
        events.append(dict(event='instance',path='top.'+name,module='W',kind='wire',layer=1,inputs=['a','b'],outputs=['x','y'],output=None))
        for pin,out in [('a','y'),('b','x')]:
            result=dict(status='input_boundary',endpoint='top.'+pin) if name=='w0' else dict(status='target',instance='top.w0',pin=out)
            events.append(dict(event='trace',instance='top.'+name,pin=pin,direction='driver',results=[result]))
            events.append(dict(event='trace',instance='top.'+name,pin=out,direction='internal',results=[dict(status='target',instance='top.'+name,pin=pin)]))
            events.append(dict(event='output_check',instance='top.'+name,pin=out,status='ok'))
    events.append({'event':'complete'});graph=build_graph(events,config())
    if indexed:
        store=ResultStore(tmp_path/'component.sqlite',True);index_graph(graph,store);view=IndexedGraphView(store.graph())
    else:view=GraphView(graph)
    for component in (0,1):
        for mode,depth in [('instances',2),('hierarchy',2),('hierarchy',1)]:
            view.project(mode,component=component,depth=depth)
            if depth==1:assert sum(n.get('internal_edges',0) for n in view.nodes.values())==1
            else:
                assert len(view.edges)==1
                assert all(len(n['outputs'])==1 and len(n['inputs'])==1 for n in view.nodes.values())
    if indexed:store.close()


def test_sv_wire_channels_choose_only_related_gate_registers(tmp_path):
    from clocktree.register_trace import create_database
    from clocktree.storage import write_graph
    events,cfg=fixture();graph=build_graph(events,cfg)
    cfg['layers'][0]['reset_signal']='rst_n'
    for node in graph['nodes']:
        node['port_connections']={p:{'status':'connected','full_name':node['id']+'.'+p,'coverage':'port'} for p in node['inputs']}
        if node['kind']=='gate':node['aux_ports']={'gt':'en'}
    registers=tmp_path/'registers';registers.mkdir()
    write_graph(registers/'manifest.json',{'request':{'config':cfg}})
    db=create_database(registers/'registers.sqlite')
    for index,name in enumerate(['s0','s1','g0','g1'],1):
        seed={'instance':'top.'+name,'role':'gt','pin':'en'}
        db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(index,json.dumps(seed),'complete',json.dumps({'bits':['en']})))
        db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(index,'en','top.reg_'+name,'Q','direct','register','',None,'{}'))
    db.commit();db.close()
    plan=prepare(graph,registers,tmp_path/'sv',False)
    rows=plan.branches()[1]
    assert len(rows)==2
    for row in rows:
        data=dict(row);bindings=json.loads(data['bindings'])
        expected='top.s1' if data['instance']=='top.g0' else 'top.s0'
        assert bindings['var_gt0']['instance']==expected
        assert bindings['var_gt1']['instance']==data['instance']
        assert bindings['var_gt2']['value']=="1'b1"
        assert bindings['var_pll0']['value']==expected+'.i'
