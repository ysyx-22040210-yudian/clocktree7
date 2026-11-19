from contextlib import contextmanager
import copy
import hashlib
import sqlite3
import threading

import pytest

from clocktree.branch import BranchCancelled, branch_seed, build_branch, close_branch
from clocktree.indexed_view import IndexedGraphView
from clocktree.l1_inputs import entries, group_signals, lookup_node
from clocktree.navigation import neighbour_edges
from clocktree.navigation_target import display_node, location_target
from clocktree.storage import ResultStore, index_graph, new_store
from clocktree.viewmodel import GraphView
from scripts.check_boundary_canvas import fixture, grouped_fixture, proof


@contextmanager
def model_for(graph, folder, indexed):
    model = IndexedGraphView(index_graph(graph,new_store(folder))) if indexed else GraphView(graph)
    try:
        yield model
    finally:
        if indexed:model.store.close()


@contextmanager
def opened(model, marker):
    graph = build_branch(model, marker)
    try:
        yield graph, IndexedGraphView(graph) if hasattr(model,'store') else GraphView(graph)
    finally:
        close_branch(graph)


@pytest.mark.parametrize('indexed', [False, True], ids=['json','sqlite'])
@pytest.mark.parametrize('mode', ['port','wire','legacy'])
def test_exact_boundary_fanout_from_partial_main_view(tmp_path,indexed,mode):
    graph = fixture()
    sig = 'top.ifc.clk[3]'
    for n in graph['nodes']:
        for v in n.get('input_boundaries',{}).values():
            if v['signal']==sig and mode=='wire':v['source_kind']='wire'
        if mode=='legacy':
            n['l1_inputs']=n.pop('input_boundaries',{})
            for v in n['l1_inputs'].values():v.pop('layer',None)
    graph['boundaries']=[dict(instance='top.w',direction=direction,pin=p,status='scope_boundary')
                         for direction,p in [('driver','i0'),('driver','i1'),('load','x'),('load','y')]]
    original = copy.deepcopy(graph)
    with model_for(graph,tmp_path,indexed) as model:
        model.project(scope='top.r')
        marker = next(iter(model.l1_nodes.values()))
        assert marker['input_entry']['signal']==sig
        assert marker['kind']==('l1_input' if mode=='legacy' else 'boundary_input')
        with opened(model,marker) as (child,view):
            assert child['branch_view']['direct_loads']==4
            assert set(n['id'] for n in child['nodes'])=={'top.r','top.m','top.g','top.solo','top.w','top.end'}
            assert {e['id'] for e in child['edges']}=={'0','2','3','4'}
            wire=view.original['top.w']
            assert wire['inputs']==['i0'] and wire['outputs']==['x']
            assert wire['internal_connections']==[dict(input='i0',output='x',status='verified')]
            assert view.original['top.end']['inputs']==['a','c','direct']
            assert {b['pin'] for b in child['boundaries']}=={'i0','x'}
            for projection in ('instances','hierarchy'):
                view.project(projection,depth=1)
                assert set(view.l1_nodes)=={marker['id']}
                assert view.l1_nodes[marker['id']]['input_entry']['signal']==sig
            view.project()
            direct=next(e for e in view.l1_edges.values() if e['target_pin']=='direct')
            assert direct['evidence']['driver']==[dict(type='npiNetBit',name=sig)]
            ref=lookup_node(view,direct['target'])
            assert ref['reference_of']=='top.end' and ref['inputs']==['direct']
            assert display_node(view.nodes,location_target(ref,direct,'target'))==ref
            assert neighbour_edges(view,ref,'upstream')==[direct]
            assert len(neighbour_edges(view,view.l1_nodes[marker['id']],'downstream'))==4
            assert [event['name'] for event in view.original_edges['0']['evidence']['driver']]==['top.r.o']
    assert graph==original


@pytest.mark.parametrize('indexed',[False,True])
def test_direct_reset_subsystem_and_unverified_or_other_scope_ignored(tmp_path,indexed):
    graph=fixture()
    graph['tree_type']=graph['config']['tree_type']='reset'
    sig='top.rst_if.rst_n[2]'
    for n in graph['nodes']:n.pop('input_boundaries',None)
    end=next(n for n in graph['nodes'] if n['id']=='top.end')
    main=proof('rst_n[2]',scope='top.rst_if')
    end['input_boundaries']={'direct':main,'foreign':proof('rst_n[2]',scope='top.other')}
    end['l1_inputs']={'foreign':main}
    mux=next(n for n in graph['nodes'] if n['id']=='top.m')
    mux['input_boundaries']={'test':dict(main,status='unresolved'),'absent':main}
    graph['nodes'].remove(next(n for n in graph['nodes'] if n['kind']=='wire'))
    graph['edges']=[e for e in graph['edges'] if e['source']!='top.w']
    with model_for(graph,tmp_path,indexed) as model:
        model.project()
        marker=next(n for n in model.l1_nodes.values() if n['input_entry']['signal']==sig)
        with opened(model,marker) as (child,v):
            assert child['tree_type']=='reset' and child['branch_view']['direct_loads']==1
            assert [n['id'] for n in child['nodes']]==['top.end'] and not child['edges']
            assert v.original['top.end']['inputs']==['direct']
            v.project()
            assert len(v.l1_edges)==1 and sig in v.svg()
            assert entries(v.original['top.end'])=={'direct':main}


@pytest.mark.parametrize('indexed',[False,True])
def test_mixed_group_has_only_its_actual_members_and_pages(tmp_path,indexed):
    graph=grouped_fixture()
    unrelated=copy.deepcopy(graph['nodes'][-1])
    unrelated.update(id='top.other.u',path='top.other.u',input_boundaries={'i':proof('from_elsewhere')})
    graph['nodes'].append(unrelated)
    with model_for(graph,tmp_path,indexed) as model:
        model.project('hierarchy',scope='top.grp',depth=2)
        marker=next(iter(model.l1_nodes.values()))
        entry=marker['input_entry']
        target=model.nodes[marker['boundary_group']]
        assert entry['aggregate_count']==105
        first=group_signals(model,target,entry,limit=100)
        tail=group_signals(model,target,entry,offset=100)
        assert len(first)==100 and len(tail)==4 and not set(first).intersection(tail)
        assert group_signals(model,target,entry,'FROM_ELSEWHERE')==[]
        assert group_signals(model,target,entry,'CLK_000')==[('top.ifc.clk_000','clk_000',2)]
        with pytest.raises(ValueError,match='具体'):branch_seed(marker)


def test_indexed_high_fanout_opened_from_one_load_and_grouped_overview(tmp_path):
    graph=grouped_fixture(1205)
    for n in graph['nodes']:n['input_boundaries']['i']=proof('common')
    with model_for(graph,tmp_path,True) as model:
        model.project('hierarchy',depth=1)
        group=next(iter(model.l1_nodes.values()))
        assert not group.get('boundary_group') and group['input_entry']['signal']=='top.ifc.common'
        model.project(scope='top.grp.u000')
        seed=next(iter(model.l1_nodes.values()))
        with opened(model,seed) as (child,v):
            assert child['branch_view']['direct_loads']==1205 and len(child['nodes'])==1205
            assert not child['edges']
            v.project('hierarchy',depth=1)
            assert set(v.l1_nodes)=={seed['id']}
            assert sum(e['aggregate_count'] for e in v.l1_edges.values())==1205
        cancel=threading.Event()
        cancel.set()
        with pytest.raises(BranchCancelled):build_branch(model,seed,cancel)


def test_saved_legacy_index_works_without_modification(tmp_path):
    path=tmp_path/'legacy.sqlite'
    store=ResultStore(path,True)
    index_graph(fixture(),store)
    store.close()
    with sqlite3.connect(str(path)) as conn:
        conn.execute('DROP TABLE ct7_verified_inputs')
    original_hash=hashlib.sha256(path.read_bytes()).digest()
    store=ResultStore(path)
    try:
        model=IndexedGraphView(store.graph())
        model.project('hierarchy',depth=1)
        model.project(scope='top.r')
        with opened(model,next(iter(model.l1_nodes.values()))) as (child,_):
            assert len(child['edges'])==4
        assert not store.connection().execute("SELECT 1 FROM main.sqlite_master WHERE name='ct7_verified_inputs'").fetchone()
    finally:
        store.close()
    assert original_hash==hashlib.sha256(path.read_bytes()).digest()
