import copy
import csv
import json
import pytest
from clocktree.viewmodel import GraphView,clip_segment
from clocktree.indexed_view import IndexedGraphView
from clocktree.networks import memberships
from clocktree.storage import index_graph,new_store
from clocktree.config import validate_config
from clocktree.devices import symbol_parts,port_offsets,display_inputs
from clocktree.input_ports import filter_ports,confirmed_ports
from clocktree.ui_state import UIState
from clocktree.issues import IssueIndex


def fixture():
    nodes={key:dict(id=key,path=key,module='mod_'+key,kind='gate',inputs=['i'],output='o',layer=layer,root=False,terminal=False)
           for key,layer in [('r',1),('a',5),('b',2),('c',5),('d',7),('sink',8)]}
    nodes['sink'].update(kind='subsystem',inputs=['clk[3]','clk[2]','reset'],configured_inputs=['clk','reset'],output=None)
    links=[('r','a','i'),('a','c','i'),('r','b','i'),('c','sink','clk[3]'),('b','sink','clk[2]'),('d','sink','reset')]
    edges=[dict(id=str(i),source=s,target=t,source_pin='o',target_pin=p,layer_delta=nodes[t]['layer']-nodes[s]['layer'],evidence={'driver':[]}) for i,(s,t,p) in enumerate(links)]
    components=memberships(nodes,edges,[])
    return dict(nodes=list(nodes.values()),edges=edges,components=components,diagnostics=[],boundaries=[],
                config={'design':{'top':'top','elab':'/test/kdb'},'layers':[{'index':i,'hierarchy':'top','modules':[]} for i in range(1,9)]},summary={'instances':6,'edges':6})


def assert_geometry(view):
    boxes=list(view.positions.items())
    for i,(key,(x,y,w,h)) in enumerate(boxes):
        for other,(a,b,c,d) in boxes[i+1:]:assert x+w<=a or a+c<=x or y+h<=b or b+d<=y,(key,other)
    for edge in view.edges:
        points=view.edge_points(edge)
        for key,(x,y,w,h) in boxes:
            if key in (edge['source'],edge['target']):continue
            assert all(clip_segment(a,b,(x+.01,y+.01,x+w-.01,y+h-.01)) is None for a,b in zip(points,points[1:])),(edge,key)


def test_branch_compaction_preserves_edges_and_input_networks(tmp_path):
    graph=fixture();saved=copy.deepcopy(graph)
    sink=next(n for n in graph['nodes'] if n['id']=='sink')
    assert len(sink['components'])==2
    assert sink['input_components']['clk[3]']==sink['input_components']['clk[2]']!=sink['input_components']['reset']
    branch=GraphView(graph).project();legacy=GraphView(graph,layout_mode='layers').project()
    assert len(branch.original)==6 and sum(n.get('reference_of')=='sink' for n in branch.nodes.values())==3
    assert {e['id'] for e in branch.edges}=={e['id'] for e in graph['edges']}
    def distance(v):return sum(abs(v.positions[e['source']][0]-v.positions[e['target']][0]) for e in v.edges)
    assert distance(branch)<distance(legacy)
    assert_geometry(branch);assert_geometry(legacy)
    assert graph==saved
    indexed=IndexedGraphView(index_graph(graph,new_store(tmp_path))).project()
    assert indexed.positions==branch.positions
    assert sum(len(c['nodes']) for c in indexed.graph['components'])==7
    for component in sink['components']:
        indexed.project(component=component)
        assert any(n.get('reference_of')=='sink' for n in indexed.nodes.values())


def test_whole_branch_excludes_upstream_side_branch(tmp_path):
    graph=fixture();model=GraphView(graph).project()
    expected={'0','1','3'}
    assert model.path_edges('a','upstream')|model.path_edges('a','downstream')==expected
    indexed=IndexedGraphView(index_graph(graph,new_store(tmp_path))).project()
    selection=indexed.branch_edges('a')
    assert {r[0] for r in indexed.store.connection().execute(selection.sql,selection.params)}==expected
    assert model.path_edges('sink','upstream',seed_pins=['clk[3]'])==expected
    scoped=indexed.path_edges('sink','upstream',seed_pins=['clk[3]'])
    assert {r[0] for r in indexed.store.connection().execute(scoped.sql,scoped.params)}==expected


def test_input_selection_is_explicit_and_subsystem_has_no_output():
    rows=[{'name':'clk_bus'},{'name':'clk_aux'},{'name':'data'}]
    assert confirmed_ports(rows,'clk',['clk_bus'])==['clk_bus']
    assert filter_ports(rows,'CLK')==[]
    for pattern,names in [('missing',[]),('clk',[]),('[',[])]:
        with pytest.raises(Exception):confirmed_ports(rows,pattern,names)
    config=fixture()['config'];config['layers'][0]['modules']=[{'module':'sub','kind':'subsystem','clock_inputs':['clk_bus']}]
    validate_config(config)
    config['layers'][0]['modules'][0]['clock_output']='fake'
    with pytest.raises(ValueError):validate_config(config)
    node=next(n for n in fixture()['nodes'] if n['id']=='sink')
    assert display_inputs(node)==['clk','reset']
    assert port_offsets(node,382,200)[1] is None
    assert not any(p.get('text')=='输出' for p in symbol_parts(node,382,200))
    assert len(port_offsets(dict(node,expand_bus=True),382,200)[0])==3


def test_searches_persist_per_design_and_type_without_graph_writes(tmp_path):
    path=tmp_path/'ui.json';state=UIState(path);config=fixture()['config']
    for i in range(110):state.search(config,'query{}'.format(i))
    state.search(config,'query25');restored=UIState(path)
    assert len(restored.get(config)['searches'])==100 and restored.get(config)['searches'][0]=='query25'
    assert restored.get(dict(config,tree_type='reset'))=={}
    assert restored.get(dict(config,design={'top':'different','elab':'/test/kdb'}))=={}
    restored.update(config,searches=[]);assert UIState(path).get(config)['searches']==[]
    assert list(tmp_path.iterdir())==[path]


def test_issues_cover_more_than_500_records_and_expand_objects(tmp_path):
    graph=fixture()
    graph['diagnostics']=[{'code':'missing_trace','instance':'a','pin':'i'} for i in range(701)]
    graph['diagnostics'].append({'code':'cycle_or_cycle_downstream','instances':['a','b','c']})
    graph['boundaries']=[{'status':'trace_timeout','instance':'d','pin':'i','direction':'driver'},
                         {'status':'undriven','instance':'b','pin':'i','direction':'driver'},
                         {'status':'scope_boundary','instance':'r','pin':'i','direction':'driver'}]
    issues=IssueIndex(graph);issues.build()
    total,affected,rows=issues.query(offset=700)
    assert total==704 and affected==4 and len(rows)==4
    assert len(issues.objects(rows[1][0]))==3
    path=tmp_path/'all.csv';assert issues.export(path)==total
    with path.open(encoding='utf-8-sig',newline='') as stream:assert len(list(csv.reader(stream)))==total+1
    assert issues.query(category='端口超时')[0]==1
    assert issues.query(category='输入无驱动')[0]==1
    issues.close()


def test_cycles_and_mux_merges_have_obstacle_free_routes():
    graph=fixture();graph['nodes'][1].update(kind='mux',inputs=['i','test'])
    graph['edges'].append(dict(id='return',source='c',source_pin='o',target='a',target_pin='test',layer_delta=0))
    view=GraphView(graph).project();assert_geometry(view)
    assert len(view.edges)==7 and view.node_columns['a']==view.node_columns['c']


def test_old_index_only_session_snapshot_migrates_without_modifying_history(tmp_path):
    from clocktree.storage import snapshot_index,open_graph
    import sqlite3
    graph=index_graph(fixture(),new_store(tmp_path))
    path=tmp_path/'old-session.json';snapshot_index(path,graph)
    pointer=json.loads(path.read_text());pointer['ct7_index']=1;path.write_text(json.dumps(pointer))
    database=tmp_path/pointer['database']
    conn=sqlite3.connect(str(database))
    conn.execute('DROP TABLE memberships');conn.execute("UPDATE header SET value='1' WHERE key='ct7_index_version'");conn.commit();conn.close()
    original=database.read_bytes()
    restored=open_graph(path)
    assert len(restored['nodes'])==6 and len(restored['edges'])==6
    assert database.read_bytes()==original and restored.store.path!=database


def test_evidence_excerpt_is_bounded_and_keeps_raw_path(tmp_path):
    from clocktree.evidence import mapping_excerpt
    store=new_store(tmp_path)
    path=store.add_path([dict(type='npiNet',name='wire{}'.format(i)) for i in range(400)])
    statements=[];store.connection().set_trace_callback(statements.append)
    excerpt=mapping_excerpt({'evidence':{'driver':path}})
    assert 'wire399' in excerpt and 'wire391' not in excerpt and '8 / 400' in excerpt
    assert len(statements)<=11
    store.connection().set_trace_callback(None)
    assert len(list(path))==400


def test_import_failure_is_available_without_a_result():
    issues=IssueIndex({'nodes':[],'edges':[]},[{'code':'fatal','message':'导入失败','run_directory':'/run/failed'}])
    issues.build()
    total,affected,rows=issues.query()
    assert total==1 and affected==0 and rows[0][8]=='导入失败'
    issues.close()
