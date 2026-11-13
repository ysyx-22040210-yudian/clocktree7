import copy
import json
import pytest
from clocktree.graph import build_graph
from clocktree.storage import ResultStore,index_graph
from clocktree.viewmodel import GraphView
from clocktree.indexed_view import IndexedGraphView
from clocktree.sv_templates import prepare


def fixture():
    config={'design':{'top':'top','filelist':'x.f'},'layers':[
        {'index':1,'hierarchy':'top.domain','reset_signal':'rst1_n','modules':[{'module':'M','kind':'mux','clock_inputs':['i'],'clock_output':'o'}]},
        {'index':2,'hierarchy':'top.domain','reset_signal':'rst2_n','modules':[{'module':'G','kind':'gate','clock_inputs':['i'],'clock_output':'o'},
         {'module':'S','kind':'subsystem','clock_inputs':['a','b']}]}]}
    def instance(name,kind,inputs):
        pins=inputs+([] if kind=='subsystem' else ['o'])
        return dict(event='instance',path='top.domain.'+name,module={'mux':'M','gate':'G','subsystem':'S'}[kind],
            kind=kind,layer=1 if kind=='mux' else 2,inputs=inputs,output=None if kind=='subsystem' else 'o',
            port_connections={p:{'status':'connected','full_name':'top.domain.'+name+'_'+p,'expression':name+'_'+p} for p in pins})
    instances=[instance('m','mux',['i']),instance('g0','gate',['i']),instance('g1','gate',['i']),instance('s','subsystem',['a','b'])]
    def boundary(port):return {'status':'input_boundary','endpoint':'top.domain.'+port,'l1_scope':'top.domain','l1_port':port,'l1_verified':True,
                              'path':[{'type':'npiNetBit','name':'top.domain.'+port}]}
    def trace(inst,pin,result):return dict(event='trace',instance='top.domain.'+inst,pin=pin,direction='driver',results=[result])
    events=[{'event':'metadata','extraction_mode':'target'}]+instances+[
        trace('m','i',boundary('clk[7]')),trace('g0','i',boundary('clk[3]')),
        trace('g1','i',{'status':'target','instance':'top.domain.g0','pin':'o'}),
        trace('s','a',boundary('clk[2]')),trace('s','b',{'status':'target','instance':'top.domain.m','pin':'o'})]
    events.extend(dict(event='output_check',instance=n['path'],pin='o',status='ok') for n in instances if n['output'])
    events.append({'event':'complete'})
    return events,config


@pytest.mark.parametrize('indexed',[False,True])
def test_l1_sources_display_without_fake_instances(tmp_path,indexed):
    events,cfg=fixture();graph=build_graph(events,cfg)
    assert graph['summary']['instances']==4 and graph['summary']['edges']==2
    assert graph['summary']['l1_input_connections']==3 and graph['summary']['topology_valid']
    if indexed:
        store=ResultStore(tmp_path/'g.sqlite',True);index_graph(graph,store);graph=store.graph();view=IndexedGraphView(graph)
    else:view=GraphView(graph)
    for mode in ('instances','hierarchy'):
        view.project(mode,depth=1)
        assert view.actual_count==4
        assert view.l1_nodes and view.l1_edges
        assert all(n['layer']==1 for n in view.l1_nodes.values())
        svg=view.svg();assert 'data-device-kind="l1_input"' in svg
        if mode=='instances':assert 'top.domain.clk[3]' in svg
        for edge in view.l1_edges.values():assert view.actual_edges(edge)==[edge]
        if mode=='instances':
            from clocktree.navigation import neighbour_edges
            from clocktree.l1_inputs import branch_highlights
            incoming=neighbour_edges(view,view.original['top.domain.g0'],'upstream')
            assert len(incoming)==1 and incoming[0]['l1_boundary']
            path=view.path_edges('top.domain.g1','upstream')
            highlighted=branch_highlights(view,path,view.original['top.domain.g1'])
            assert highlighted=={incoming[0]['id']}
    if indexed:store.close()


def test_l1_terminal_output_constants_and_subsystem_input(tmp_path):
    events,cfg=fixture();graph=build_graph(events,cfg)
    plan=prepare(graph,None,tmp_path/'plan',False);count,rows=plan.branches()
    assert count==3 and not any(r['instance']=='top.domain.g0' for r in rows)
    template=' '.join('$'+v for v in ['var_pllsel','var_div','var_swdone','var_gt0','var_gt1','var_gt2','var_pll0','var_pll1','var_pll2','var_pll3','var_clkip','var_rst'])
    templates={'with_mux':'$var_clkip','without_mux':template}
    resolved=[plan.resolve(row,templates) for row in rows]
    assert all(row['ready'] for row in resolved),resolved
    gate=next(r for r in resolved if r['instance']=='top.domain.g1')
    assert gate['pin']=='o' and gate['values']['var_clkip']=='top.domain.g1_o'
    assert gate['values']['var_pll0']=='top.domain.clk[3]' and gate['values']['var_div']=='0'
    assert gate['values']['var_rst']=='top.domain.rst2_n'
    for var in ('var_pllsel','var_swdone','var_gt0','var_gt1','var_gt2','var_pll1','var_pll2','var_pll3'):
        assert gate['values'][var]=="1'b1"
    sink=next(r for r in resolved if r['instance']=='top.domain.s' and r['pin']=='a')
    assert sink['values']['var_clkip']=='top.domain.s_a' and sink['values']['var_pll0']=='top.domain.clk[2]'
    assert next(r for r in resolved if r['pin']=='b')['mode']=='with_mux'
    report=plan.export(templates,tmp_path/'all.sv');assert report['blocks']==3 and report['skipped']==0


@pytest.mark.parametrize('extra',[{'status':'unknown_cell'},{'status':'trace_timeout'},
    {'status':'input_boundary','endpoint':'top.elsewhere'},
    {'status':'input_boundary','endpoint':'top.domain.clk[4]','l1_scope':'top.domain','l1_port':'clk[4]','l1_verified':True}])
def test_partial_and_ambiguous_l1_trace_never_claims_direct(extra):
    events,cfg=fixture()
    event=next(e for e in events if e.get('instance')=='top.domain.g0' and e.get('direction')=='driver')
    event['results'].append(extra)
    graph=build_graph(events,cfg)
    assert not next(n for n in graph['nodes'] if n['id']=='top.domain.g0').get('l1_inputs')


def test_old_unmarked_boundaries_do_not_gain_new_proofs():
    events,cfg=fixture()
    for e in events:
        for r in e.get('results',[]):r.pop('l1_verified',None)
    graph=build_graph(events,cfg)
    assert not any(n.get('l1_inputs') for n in graph['nodes'])


@pytest.mark.parametrize('indexed', [False, True])
def test_legacy_later_roots_load_without_former_l1_issue(tmp_path, indexed):
    from clocktree.session import read_graph
    from clocktree.storage import publish_index, write_graph
    events, config = fixture()
    for event in events:
        for result in event.get('results', []):
            result.pop('l1_verified', None)
    graph = build_graph(events, config)
    assert graph['summary']['topology_valid']
    assert next(n for n in graph['nodes'] if n['id']=='top.domain.g0')['root']
    graph['diagnostics'].append({'code': 'root_outside_L1', 'instance': 'top.domain.g0'})
    graph['summary'].update(topology_valid=False, diagnostics=len(graph['diagnostics']))
    path = tmp_path/'clocktree.json'
    write_graph(path, graph)
    if indexed:
        store = ResultStore(tmp_path/'result.sqlite',True)
        publish_index(path,index_graph(graph,store))
        store.close()
    loaded = read_graph(path)
    assert loaded['summary']['topology_valid'] and loaded['summary']['diagnostics'] == 0
    assert not list(loaded['diagnostics'])
    assert json.loads(path.read_text(encoding='utf-8'))['diagnostics'][0]['code']=='root_outside_L1'


def test_partial_mux_l1_entry_is_reported_as_mixed_instead_of_ignored(tmp_path):
    events,cfg=fixture()
    target=next(e for e in events if e.get('path')=='top.domain.g1')
    target.update(kind='mux',inputs=['i','other'])
    events.insert(-1,dict(event='trace',instance='top.domain.g1',pin='other',direction='driver',results=[
        dict(status='target',instance='top.domain.m',pin='o')]))
    graph=build_graph(events,cfg);plan=prepare(graph,None,tmp_path/'plan',False)
    rows=[r for r in plan.branches()[1] if r['instance']=='top.domain.g1']
    assert rows and not plan.resolve(rows[0],{'without_mux':'$var_pll0 $var_clkip'})['ready']


def test_wire_terminal_channels_use_outputs_not_whole_input_bus(tmp_path):
    events,cfg=fixture()
    events=[e for e in events if e.get('path') not in ('top.domain.g1','top.domain.s') and e.get('instance') not in ('top.domain.g1','top.domain.s')]
    wire=next(e for e in events if e.get('path')=='top.domain.g0')
    wire.update(kind='wire',output=None,outputs=['x','y'])
    wire['port_connections'].update({p:{'status':'connected','full_name':'top.domain.w_'+p} for p in ('x','y')})
    events=[e for e in events if not(e.get('event')=='output_check' and e.get('instance')==wire['path'])]
    for p in ('x','y'):
        events.insert(-1,dict(event='output_check',instance=wire['path'],pin=p,status='ok'))
        events.insert(-1,dict(event='trace',instance=wire['path'],pin=p,direction='internal',results=[dict(status='target',instance=wire['path'],pin='i')]))
    graph=build_graph(events,cfg);plan=prepare(graph,None,tmp_path/'plan',False)
    rows=[r for r in plan.branches()[1] if r['instance']==wire['path']]
    assert {r['clkip'] for r in rows}=={'top.domain.w_x','top.domain.w_y'}
    assert all(json.loads(r['bindings'])['var_pll0']['value']=='top.domain.clk[3]' for r in rows)


def test_real_control_mapping_wins_and_missing_job_never_becomes_default(tmp_path):
    from clocktree.register_trace import create_database
    from clocktree.storage import write_graph
    events,cfg=fixture()
    next(e for e in events if e.get('path')=='top.domain.g0')['aux_ports']={'gt':'enable'}
    graph=build_graph(events,cfg)
    missing=prepare(graph,None,tmp_path/'missing',False)
    row=next(r for r in missing.branches()[1] if r['instance']=='top.domain.g1')
    assert not missing.resolve(row,{'without_mux':'$var_gt0 $var_clkip'})['ready']
    reg=tmp_path/'registers';reg.mkdir();write_graph(reg/'manifest.json',{'request':{'config':cfg}})
    db=create_database(reg/'registers.sqlite')
    db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(1,json.dumps({'instance':'top.domain.g0','role':'gt','pin':'enable'}),'complete',json.dumps({'bits':['top.domain.g0.enable']})))
    db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(1,'enable','top.ctrl.Q[7]','Q','direct','register','',None,'{}'))
    db.commit();db.close()
    plan=prepare(graph,reg,tmp_path/'mapped',False)
    row=next(r for r in plan.branches()[1] if r['instance']=='top.domain.g1')
    result=plan.resolve(row,{'without_mux':'$var_gt0 $var_gt1 $var_div $var_pll0 $var_clkip'})
    assert result['ready'],result
    assert result['values']['var_gt0']=='top.ctrl.Q[7]' and result['values']['var_gt1']=="1'b1"
    assert result['values']['var_div']=='0' and result['values']['var_clkip']=='top.domain.g1_o'
