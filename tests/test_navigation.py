import pytest

from clocktree.indexed_view import IndexedGraphView
from clocktree.navigation import neighbour_destinations, neighbour_edges
from clocktree.navigation_target import display_node, location_target
from clocktree.storage import index_graph, new_store
from clocktree.viewmodel import GraphView
from test_branch_view import fixture


@pytest.mark.parametrize('indexed',[False,True])
def test_downstream_keeps_pin_and_driver_instead_of_first_instance_reference(tmp_path,indexed):
    graph=fixture()
    model=IndexedGraphView(index_graph(graph,new_store(tmp_path))) if indexed else GraphView(graph)
    model.project()
    edge=model.original_edges['6']  # z -> sink.c1; first displayed reference is y -> c0.
    target=location_target(model.original['sink'],edge,'target')
    chosen=display_node(model.nodes,target)
    assert chosen['reference_source']=='z' and chosen['inputs']==['c1']
    assert neighbour_destinations(model,model.original['z'],'downstream')[0]['id']=='6'
    assert [e['id'] for e in neighbour_edges(model,chosen,'upstream')]==['6']
    # If only another input's reference is visible, request a new projection.
    wrong={k:n for k,n in model.nodes.items() if n.get('reference_source')!='z'}
    assert display_node(wrong,target) is None
    # Display IDs are temporary; even reusing a stale ID may not change branch.
    old_id=chosen['id'];others=[n for n in model.nodes.values() if n.get('reference_of')=='sink' and n is not chosen]
    changed={old_id:dict(others[0],id=old_id),'new':dict(chosen,id='new')}
    assert display_node(changed,target)['id']=='new'
    # Single physical-node mode still preserves the exact port context.
    assert display_node({'sink':model.original['sink']},target)['id']=='sink'


@pytest.mark.parametrize('indexed',[False,True])
def test_multiple_ports_to_same_instance_require_choice_but_same_output_is_direct(tmp_path,indexed):
    graph=fixture()
    graph['edges'].append(dict(graph['edges'][5],id='extra',target_pin='c1'))
    model=IndexedGraphView(index_graph(graph,new_store(tmp_path))) if indexed else GraphView(graph)
    model.project()
    assert len(neighbour_destinations(model,model.original['y'],'downstream'))==2
    reference=next(n for n in model.nodes.values() if n.get('reference_source')=='y')
    assert {e['target_pin'] for e in neighbour_edges(model,reference,'upstream')}=={'c0','c1'}
    assert {e['source'] for e in neighbour_edges(model,reference,'upstream')}=={'y'}
    assert len(neighbour_destinations(model,reference,'upstream'))==1
    edge=model.original_edges['1']
    target=location_target(model.original['m'],edge,'target')
    assert display_node(model.nodes,target)['id']=='m' and target['pin']=='b'
    # Diagnostic navigation can address an auxiliary or missing port on a
    # physical device. It must not reject the device itself for that reason.
    assert display_node(model.nodes,location_target(model.original['m'],pin='select_mode'))['id']=='m'
    with pytest.raises(ValueError):location_target(model.original['y'],edge,'target')


def test_reference_identity_survives_projection_reordering_and_unconnected_input():
    model=GraphView(fixture()).project()
    reference=next(n for n in model.nodes.values() if n.get('reference_source')=='z')
    target=location_target(reference)
    assert display_node(model.nodes,target)['inputs']==['c1']
    unconnected=next(n for n in model.nodes.values() if n.get('reference_of')=='sink' and n.get('reference_source') is None)
    assert display_node(model.nodes,location_target(unconnected))['inputs']==['unconnected']
    assert display_node(model.nodes,location_target(model.original['sink'],pin='other'))['reference_source']=='side'


def test_focus_pins_requested_edge_even_when_neighbourhood_limit_excludes_it(tmp_path,monkeypatch):
    from clocktree import indexed_view
    monkeypatch.setattr(indexed_view,'DETAIL_LIMIT',3)
    model=IndexedGraphView(index_graph(fixture(),new_store(tmp_path)))
    edge=model.original_edges['8']
    model.project_focus(model.original['sink'],required_edge=edge)
    target=location_target(model.original['sink'],edge,'target')
    assert display_node(model.nodes,target)['reference_source']=='side'
    assert any(e['id']=='8' for e in model.edges) and model.focus_edge=='8'
    model.project();assert model.focus_edge is None
