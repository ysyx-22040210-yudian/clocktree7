import copy
import threading

import pytest

from clocktree.branch import BranchCancelled, BranchJob, build_branch, close_branch
from clocktree.indexed_view import IndexedGraphView
from clocktree.networks import memberships
from clocktree.storage import EvidencePath, index_graph, new_store
from clocktree.viewmodel import GraphView


def fixture():
    layers = {'r0': 1, 'r1': 1, 'm': 2, 'x': 3, 'y': 4, 'z': 4, 'side': 2, 'sink': 5, 'solo': 3}
    nodes = {key: dict(id=key, path=key, layer=layer, kind='gate', module='g', inputs=['i'], output='o',
                      root=key in ('r0', 'r1', 'solo'), terminal=key in ('sink', 'solo')) for key, layer in layers.items()}
    nodes['m'].update(kind='mux', module='mux', inputs=['a', 'b'])
    nodes['sink'].update(kind='subsystem', module='sub', inputs=['c0', 'c1', 'other', 'unconnected'], output=None)
    pairs = [('r0','m','a'), ('r1','m','b'), ('m','x','i'), ('x','y','i'), ('x','z','i'),
             ('y','sink','c0'), ('z','sink','c1'), ('r0','side','i'), ('side','sink','other')]
    edges = [dict(id=str(i), source=s, target=t, source_pin='o', target_pin=p,
                  layer_delta=layers[t]-layers[s], evidence={'driver':[{'name':s+'.o', 'type':'port'}, {'name':t+'.'+p, 'type':'port'}]})
             for i, (s,t,p) in enumerate(pairs)]
    components = memberships(nodes, edges, [])
    return dict(nodes=list(nodes.values()), edges=edges, components=components, boundaries=[], tree_type='clock',
                summary={'instances':len(nodes), 'edges':len(edges), 'extraction_mode':'target'},
                config={'tree_type':'clock', 'design':{'top':'top'}, 'layers':[dict(index=i, hierarchy='top', modules=[]) for i in range(1,6)]})


def model_for(graph, directory, indexed):
    return IndexedGraphView(index_graph(graph, new_store(directory))) if indexed else GraphView(graph)


@pytest.mark.parametrize('indexed', [False, True])
def test_exact_branch_excludes_upstream_sibling_and_unrelated_subsystem_ports(tmp_path, indexed):
    original = fixture(); saved = copy.deepcopy(original)
    model = model_for(original, tmp_path, indexed)
    branch = build_branch(model, model.original['x'])
    try:
        assert {e['id'] for e in branch['edges']} == set(map(str, range(7)))
        assert {n['id'] for n in branch['nodes']} == {'r0','r1','m','x','y','z','sink'}
        assert next(n for n in branch['nodes'] if n['id']=='sink')['inputs'] == ['c0','c1']
        assert branch['config'] == original['config'] and branch['config'] is not original['config']
        view = IndexedGraphView(branch) if indexed else GraphView(branch)
        view.project()
        assert {e['id'] for e in view.edges} == set(map(str, range(7)))
        assert all(n['inputs'] in (['c0'],['c1']) for n in view.nodes.values() if n.get('reference_of')=='sink')
        if indexed:
            assert branch.store.connection().execute('SELECT COUNT(*) FROM path_nodes').fetchone()[0] == 0
            path = next(iter(branch['edges']))['evidence']['driver']
            assert isinstance(path, EvidencePath) and path.store is model.store
            assert list(path) == original['edges'][0]['evidence']['driver']
        assert original == saved
        assert len(model.original) == 9 and len(model.original_edges) == 9
    finally:
        close_branch(branch)


@pytest.mark.parametrize('indexed', [False, True])
def test_subsystem_reference_keeps_only_its_inputs_and_isolated_reference(tmp_path, indexed):
    model = model_for(fixture(), tmp_path, indexed)
    ref = dict(model.original['sink'], id='ref:sink:0', reference_of='sink', reference_source='y', inputs=['c0'])
    branch = build_branch(model, ref)
    try:
        assert {e['id'] for e in branch['edges']} == {'0','1','2','3','5'}
        assert next(n for n in branch['nodes'] if n['id']=='sink')['inputs'] == ['c0']
    finally:
        close_branch(branch)
    ref.update(inputs=['unconnected'], reference_source=None)
    branch = build_branch(model, ref)
    try:
        assert len(branch['nodes']) == 1 and not branch['edges']
        assert next(iter(branch['nodes']))['inputs'] == ['unconnected']
    finally:
        close_branch(branch)


@pytest.mark.parametrize('indexed', [False, True])
def test_cycles_and_isolated_nodes_remain_and_cancel_is_explicit(tmp_path, indexed):
    graph = fixture()
    graph['edges'].append(dict(id='cycle', source='y', target='x', source_pin='o', target_pin='i', layer_delta=-1, evidence={}))
    model = model_for(graph, tmp_path, indexed)
    branch = build_branch(model, model.original['x'])
    try:
        assert {e['id'] for e in branch['edges']} == set(map(str, range(7))) | {'cycle'}
    finally:
        close_branch(branch)
    branch = build_branch(model, model.original['solo'])
    try:
        assert [n['id'] for n in branch['nodes']] == ['solo'] and not branch['edges']
    finally:
        close_branch(branch)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(BranchCancelled):
        build_branch(model, model.original['x'], cancel)
    with pytest.raises(ValueError):
        build_branch(model, dict(id='group', aggregate=True))


def test_background_snapshot_and_cancelled_result_release_private_index(tmp_path):
    model = model_for(fixture(), tmp_path, True)
    job = BranchJob(model, model.original['x'])
    assert job.done.wait(10)
    graph, error = job.take()
    assert error is None and len(graph['nodes']) == 7
    branch_path = graph.store.path
    job.close(); close_branch(graph)
    assert not branch_path.exists() and model.store.path.exists()
    assert len(model.original) == 9
    job = BranchJob(model, model.original['x']); job.close()
    assert job.done.wait(10) and job.take() == (None, None)


def test_branch_of_branch_keeps_original_lazy_evidence_after_parent_closed(tmp_path):
    source = model_for(fixture(), tmp_path, True)
    first = build_branch(source, source.original['x'])
    model = IndexedGraphView(first)
    second = build_branch(model, model.original['y'])
    close_branch(first)
    try:
        assert next(iter(second['edges']))['evidence']['driver'].store is source.store
        assert list(next(iter(second['edges']))['evidence']['driver']) == fixture()['edges'][0]['evidence']['driver']
    finally:
        close_branch(second)


def wire_fixture():
    graph = fixture()
    wire = next(n for n in graph['nodes'] if n['id'] == 'side')
    wire.update(kind='wire', output=None, outputs=['o'],
                internal_connections=[dict(input='i', output='o', status='verified')])
    return graph


def test_wire_branch_does_not_share_callers_active_sql_cursor(tmp_path):
    model = model_for(wire_fixture(), tmp_path, True)
    # The caller can be iterating an indexed result while a branch is requested.
    rows = model.store.connection().execute('SELECT id FROM nodes ORDER BY id')
    assert rows.fetchone()
    branch = None
    try:
        branch = build_branch(model, model.original['x'])
        assert {e['id'] for e in branch['edges']} == set(map(str, range(7)))
        assert rows.fetchone(), 'Preparing a branch must not close the caller cursor'
    finally:
        rows.close(); close_branch(branch)


def test_wire_branch_worker_leaves_no_source_connection_or_temp_tables(tmp_path):
    model = model_for(wire_fixture(), tmp_path, True)
    before = list(model.store.connection().execute('SELECT name FROM sqlite_temp_master'))
    worker_connections = []
    original_connection = model.store.connection
    def observe():
        conn = original_connection()
        if threading.current_thread() is not threading.main_thread():
            worker_connections.append(conn)
        return conn
    model.store.connection = observe
    job = BranchJob(model, model.original['x'])
    assert job.done.wait(10)
    graph, error = job.take()
    try:
        assert error is None
        assert not worker_connections, 'Branch preparation must own its read connection'
        assert list(model.store.connection().execute('SELECT name FROM sqlite_temp_master')) == before
    finally:
        job.close(); close_branch(graph)


def test_wire_branch_cancel_mid_traversal_cleans_private_files(tmp_path, monkeypatch):
    import clocktree.branch as branch_module
    import clocktree.connectivity as connectivity
    model = model_for(wire_fixture(), tmp_path, True)
    cancel = threading.Event()
    temporary_paths = []
    real_directory = branch_module.tempfile.TemporaryDirectory
    def directory(*args, **kwargs):
        result = real_directory(*args, **kwargs)
        temporary_paths.append(result.name)
        return result
    monkeypatch.setattr(branch_module.tempfile, 'TemporaryDirectory', directory)
    allowed = connectivity.allowed_pins
    def stop(node, direction, arrival=None):
        cancel.set()
        return allowed(node, direction, arrival)
    monkeypatch.setattr(connectivity, 'allowed_pins', stop)
    with pytest.raises(BranchCancelled):
        build_branch(model, model.original['x'], cancel)
    from pathlib import Path
    assert temporary_paths and all(not Path(path).exists() for path in temporary_paths)
    assert len(model.original) == 9


def test_wire_branch_evidence_survives_private_reader_and_parent_snapshot(tmp_path):
    model = model_for(wire_fixture(), tmp_path, True)
    first = build_branch(model, model.original['x'])
    child = build_branch(IndexedGraphView(first), next(n for n in first['nodes'] if n['id']=='y'))
    close_branch(first)
    try:
        evidence = next(iter(child['edges']))['evidence']['driver']
        assert evidence.store is model.store
        assert list(evidence) == fixture()['edges'][0]['evidence']['driver']
    finally:close_branch(child)
