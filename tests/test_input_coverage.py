import copy
import csv
import hashlib

import pytest

from clocktree.branch import build_branch, close_branch
from clocktree.devices import node_height, symbol_parts
from clocktree.graph import build_graph
from clocktree.indexed_view import IndexedGraphView
from clocktree.input_coverage import FIELD, LABEL, nodes_with_gaps, prepare_coverage
from clocktree.issues import IssueIndex
from clocktree.session import read_graph
from clocktree.storage import ResultStore, index_graph, new_store, snapshot_index, write_graph
from clocktree.sv_templates import architecture_signature
from clocktree.viewmodel import GraphView


def design(mode, tree):
    mux = dict(module='mux4', kind='mux', clock_inputs=['a','b','c','d'], clock_output='o')
    sink = dict(module='sink', kind='subsystem', clock_inputs=['bus'])
    driver = dict(module='r', kind='mux', clock_inputs=['i'], clock_output='o')
    if tree=='reset':
        for rule in (mux,sink,driver):
            rule['reset_inputs']=rule.pop('clock_inputs')
            if 'clock_output' in rule:rule['reset_output']=rule.pop('clock_output')
    cfg = dict(tree_type=tree,extraction_mode=mode,design={'top':'top','filelist':'f'},
               layers=[dict(index=1,hierarchy='top',input_boundary=True,modules=[driver,mux,sink])])
    def inst(path,rule,pins,out):
        return dict(event='instance',path='top.'+path,layer=1,module=rule['module'],kind=rule['kind'],inputs=pins,
                    output=out,port_connections={p:dict(status='connected',expression='sig_'+p) for p in pins})
    def drv(n,p,*items):
        return dict(event='trace',instance='top.'+n,pin=p,direction='driver',results=list(items))
    def target(n,p):
        return dict(status='target',instance='top.'+n,pin=p)
    def boundary(name,wire=False):
        result=dict(status='layer_wire_boundary' if wire else 'layer_input_boundary',endpoint='top.'+name,
                    scope='top',boundary_layer=1)
        result['boundary_wire' if wire else 'boundary_port']=name
        return result
    mod=inst('m',mux,['a','b','c','d'],'o')
    mod['aux_ports']={'pll_sel':'select'}
    mod['port_connections']['select']=dict(status='connected',expression='sel')
    sub=inst('s',sink,['bus[0]','bus[1]','bus[2]'],None)
    sub['port_connections']['bus[2]']=dict(status='unconnected')
    events=[dict(event='metadata',extraction_mode=mode),inst('r',driver,['i'],'o'),mod,sub,
            drv('r','i',boundary('clk_if.clk[0]')),drv('m','a',target('r','o'),boundary('shared')),
            drv('m','b',boundary('internal',True)),drv('m','c',dict(status='constant',endpoint="1'b0")),
            drv('m','d',dict(status='trace_timeout')),
            drv('s','bus[0]',target('m','o')),drv('s','bus[1]',boundary('clk_if.clk[1]')),
            drv('s','bus[2]',dict(status='undriven'),dict(status='undriven'))]
    if mode=='target':
        events.extend(dict(event='output_check',instance='top.'+n,pin='o',status='ok') for n in ('m','r'))
    else:
        for n,t,p in [('r','m','a'),('m','s','bus[0]')]:
            events.append(dict(event='trace',instance='top.'+n,pin='o',direction='load',results=[target(t,p)]))
    return build_graph(events+[dict(event='complete')],cfg)


EXPECTED={('top.m','c'),('top.m','d'),('top.s','bus[2]')}


def check_table(g, tmp_path):
    index=IssueIndex(g)
    try:
        index.build()
        count,affected,rows=index.query(category=LABEL)
        assert count==3 and affected==2
        assert {(r[5],r[6]) for r in rows}==EXPECTED
        by_pin={r[6]:r for r in rows}
        assert '常量' in by_pin['c'][8] and by_pin['c'][7]=='sig_c'
        assert '超时' in by_pin['d'][8] and '无驱动' not in by_pin['d'][8]
        assert '留空' in by_pin['bus[2]'][8] and '无驱动' in by_pin['bus[2]'][8]
        assert index.objects(by_pin['bus[2]'][0])==[('top.s','bus[2]')]
        csv_path=tmp_path/'problems.csv'
        assert index.export(csv_path,category=LABEL)==3
        with csv_path.open(encoding='utf-8-sig', newline='') as f:
            assert {(r['实例'],r['端口']) for r in csv.DictReader(f)}==EXPECTED
    finally:
        index.close()


@pytest.mark.parametrize('mode',['target','full'])
@pytest.mark.parametrize('tree',['clock','reset'])
def test_exported_result_marks_each_primary_input_once(tmp_path,mode,tree):
    g=design(mode,tree)
    assert g['summary'][FIELD]==3
    assert {(node['id'],p) for node,pins in nodes_with_gaps(g) for p in pins}==EXPECTED
    check_table(g,tmp_path)
    source=next(n for n in g['nodes'] if n['id']=='top.r')
    assert FIELD not in source
    sub=next(n for n in g['nodes'] if n['id']=='top.s')
    parts=symbol_parts(sub,382,node_height(sub))
    assert any('1 位缺少上游' in p.get('text','') for p in parts)
    sub=dict(sub,expand_bus=True)
    parts=symbol_parts(sub,382,node_height(sub))
    assert [p['text'] for p in parts if '缺少上游' in p.get('text','')]==['bus[2] · 缺少上游']
    # The reference shown along m.o contains only bus[0].
    ref=dict(sub,inputs=['bus[0]'],reference_of='top.s')
    assert not any('缺少上游' in p.get('text','') for p in symbol_parts(ref,382,node_height(ref)))


@pytest.mark.parametrize('indexed',[False,True])
def test_old_saved_results_and_partial_branch_use_global_coverage(tmp_path,indexed):
    g=design('target','clock')
    g['summary'].pop(FIELD)
    for n in g['nodes']:n.pop(FIELD,None)
    # A scope exit without a verified boundary must still be reported.
    g['boundaries'].append(dict(instance='top.m',pin='c',direction='driver',status='scope_boundary'))
    path=tmp_path/'result.json'
    if indexed:
        store=new_store(tmp_path)
        snapshot_index(path,index_graph(g,store))
        store.close()
        source=path.with_suffix('.sqlite3')
    else:
        write_graph(path,g)
        source=path
    original=hashlib.sha256(source.read_bytes()).digest()
    raw=read_graph(path)
    signature=architecture_signature(raw)
    view_graph=prepare_coverage(raw)
    assert view_graph['summary'][FIELD]==3 and architecture_signature(view_graph)==signature
    assert prepare_coverage(view_graph) is view_graph
    check_table(view_graph,tmp_path)
    model=IndexedGraphView(view_graph) if indexed else GraphView(view_graph)
    model.project(scope='top.r')
    branch=build_branch(model,next(iter(model.l1_nodes.values())))
    try:
        m=next(n for n in branch['nodes'] if n['id']=='top.m')
        assert m[FIELD]==['c','d']  # Good input b is outside this boundary branch.
        assert not m.get('input_boundaries')
        assert prepare_coverage(branch) is branch
        assert {(n['id'],p) for n,pins in nodes_with_gaps(branch) for p in pins}=={('top.m','c'),('top.m','d')}
    finally:
        close_branch(branch)
        if indexed:view_graph.store.close()
    assert hashlib.sha256(source.read_bytes()).digest()==original


def test_unverified_boundaries_and_internal_wire_pair_do_not_count():
    g=design('target','clock')
    g['summary'].pop(FIELD)
    for n in g['nodes']:n.pop(FIELD,None)
    wire=copy.deepcopy(g['nodes'][0])
    wire.update(id='top.w',path='top.w',kind='wire',inputs=['i0','i1','i2'],output=None,outputs=['o'],
                internal_connections=[dict(input='i0',output='o',status='verified')],input_boundaries={})
    wire.pop('l1_inputs',None)
    direct=dict(status='verified',scope='top',signal='top.clk',port='clk')
    wire['l1_inputs']={'i1':direct,'i2':direct}
    wire['input_boundaries']={'i1':dict(direct,status='unresolved')}
    g['nodes'].append(wire)
    g['edges'].append(dict(source='top.missing',target='top.w',source_pin='o',target_pin='i0'))
    flagged={n['id']:p for n,p in nodes_with_gaps(g)}
    assert flagged['top.w']==['i0','i1']


def test_many_missing_bits_page_search_export(tmp_path):
    g=design('target','clock')
    g['summary'].pop(FIELD)
    for n in g['nodes']:n.pop(FIELD,None)
    sub=next(n for n in g['nodes'] if n['id']=='top.s')
    sub['inputs'].extend('extra[{}]'.format(i) for i in range(1205))
    store=new_store(tmp_path)
    graph=prepare_coverage(index_graph(g,store))
    index=IssueIndex(graph)
    try:
        assert graph['summary'][FIELD]==1208
        index.build()
        total,affected,last=index.query(category=LABEL,offset=1200)
        assert (total,affected,len(last))==(1208,2,8)
        assert index.query(category=LABEL,search='extra[1199]')[0]==1
        assert index.export(tmp_path/'all.csv',category=LABEL)==total
    finally:
        index.close()
        store.close()
