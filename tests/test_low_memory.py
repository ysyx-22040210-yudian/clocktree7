import copy
import json
from pathlib import Path
import tkinter
import uuid

import pytest

from clocktree.graph import build_graph, read_events
from clocktree.indexed_view import IndexedGraphView, QuerySequence, DETAIL_LIMIT, OVERVIEW_LIMIT
from clocktree.session import SessionStore, snapshot_graph, read_graph
from clocktree.storage import (EvidencePath, IndexedGraph, ResultStore, RecordSequence,
                               index_graph, new_store, open_graph, publish_index, write_graph)


ROOT = Path(__file__).resolve().parents[1]


def sample():
    return json.loads((ROOT / 'examples/flexible_layers/clocktree.json').read_text(encoding='utf-8'))


def canonical(graph, path):
    write_graph(path, graph)
    return json.loads(path.read_text(encoding='utf-8'))


def test_event_reader_is_incremental_and_releases_each_trace(tmp_path):
    raw = tmp_path / 'raw.jsonl'
    raw.write_text('{"event":"metadata","top":"top"}\nnot JSON\n')
    events = read_events(raw)
    assert iter(events) is events
    assert next(events)['top'] == 'top'
    with pytest.raises(ValueError):
        next(events)
    # Consume a trace before the next generator step and do not retain its
    # terminal list. This specifically catches the old traces.append approach.
    terminal = {'status': 'input_boundary', 'endpoint': 'clk'}
    results = [terminal]
    def source():
        yield dict(event='trace', instance='a', pin='i', direction='driver', results=results)
        terminal['status'] = 'unknown_cell'
        results.clear()
        yield dict(event='instance', path='a', module='gate', inputs=['i'], output='o', layer=1)
        yield dict(event='trace', instance='a', pin='o', direction='load', results=[{'status': 'no_load'}])
        yield dict(event='complete')
    graph = build_graph(source(), {})
    assert graph['summary']['connectivity_complete']
    assert graph['boundaries'][0]['status'] == 'input_boundary'


def protocol():
    yield dict(event='instance', path='top.a', module='gate', inputs=['i'], output='o', layer=1)
    for trace_id, pin, direction, status in [(1, 'i', 'driver', 'input_boundary'), (2, 'o', 'load', 'no_load')]:
        yield dict(event='trace_begin', trace_id=trace_id, instance='top.a', pin=pin, direction=direction)
        yield dict(event='path_step', trace_id=trace_id, path_id=1, parent_id=0, evidence={'name': 'shared', 'type': 'net', 'net': 'shared'})
        yield dict(event='path_step', trace_id=trace_id, path_id=2, parent_id=1, evidence={'name': pin, 'type': 'port'})
        yield dict(event='trace_result', trace_id=trace_id, result={'status': status, 'endpoint': 'clk', 'path_id': 2})
        yield dict(event='trace_end', trace_id=trace_id, result_count=1)
    yield dict(event='complete')


def test_prefix_protocol_deduplicates_paths_and_partial_trace_never_passes(tmp_path):
    store = new_store(tmp_path)
    graph = build_graph(protocol(), {}, store)
    assert graph['summary']['connectivity_complete']
    assert store.connection().execute('SELECT COUNT(*) FROM path_nodes').fetchone()[0] == 3
    assert store.connection().execute('SELECT COUNT(*) FROM live_paths').fetchone()[0] == 0
    assert list(graph['boundaries'][0]['path']) == [{'name': 'shared', 'type': 'net', 'net': 'shared'}, {'name': 'i', 'type': 'port'}]
    partial = [event for event in protocol() if not (event['event'] == 'trace_end' and event['trace_id'] == 2)]
    bad = build_graph(partial, {}, new_store(tmp_path))
    assert not bad['summary']['connectivity_complete'] and not bad['nodes'][0]['terminal']
    assert any(d['code'] == 'trace_protocol' for d in bad['diagnostics'])


def test_legacy_index_export_preserves_every_record_and_evidence(tmp_path):
    original = sample()
    source = tmp_path / 'legacy.json'
    source.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
    indexed = open_graph(source, threshold=0)
    assert isinstance(indexed, IndexedGraph)
    assert canonical(indexed, tmp_path / 'export.json') == original
    assert isinstance(indexed['edges'][0]['evidence']['driver'], EvidencePath)
    # A new process can open the sidecar without reading the source JSON.
    reopened = open_graph(source)
    assert isinstance(reopened, IndexedGraph) and reopened.store.path == indexed.store.path
    assert indexed['nodes'][1:4] == original['nodes'][1:4]
    assert indexed.boundaries_for(original['nodes'][0]['id'])[:20] == [b for b in original['boundaries'] if b['instance'] == original['nodes'][0]['id']][:20]


def test_invalid_sidecar_rebuilds_and_immutable_index_survives_rerun(tmp_path):
    original = sample()
    path = tmp_path / 'tree.json'
    write_graph(path, original)
    first = open_graph(path, threshold=0)
    old_path = first.store.path
    modified = copy.deepcopy(original)
    modified['metadata']['revision'] = 'second'
    write_graph(path, modified)
    second = open_graph(path, threshold=0)
    assert second.store.path != old_path
    assert first['metadata'].get('revision') is None and second['metadata']['revision'] == 'second'
    assert old_path.exists()


def test_indexed_overview_queries_without_enumerating_all_records(tmp_path, monkeypatch):
    original = sample()
    indexed = index_graph(original, new_store(tmp_path))
    view = IndexedGraphView(indexed)
    legacy_iter = RecordSequence.__iter__
    monkeypatch.setattr(RecordSequence, '__iter__', lambda self: (_ for _ in ()).throw(AssertionError('Full record scan')))
    view.project('hierarchy', depth=2)
    assert view.actual_count == len(original['nodes'])
    assert len(view.nodes) < len(original['nodes'])
    group = next(node for node in view.nodes.values() if node.get('aggregate'))
    selection = view.selection_edges(group)
    assert len(selection) > 0
    edge = view.edges[0]
    assert len(view.actual_edges(edge)) == len(edge['members'])
    assert selection.intersection(edge['members']) in ({True}, set())
    monkeypatch.setattr(RecordSequence, '__iter__', legacy_iter)
    view.project('instances')
    assert {e['id'] for e in view.edges} == {e['id'] for e in original['edges']}
    found = view.find('gating')
    if found:
        assert isinstance(found, QuerySequence)
    terminal = next(n for n in original['nodes'] if n['terminal'])
    selected = view.path_edges(terminal['id'], 'upstream')
    from clocktree.viewmodel import GraphView
    expected = GraphView(original).path_edges(terminal['id'], 'upstream')
    assert len(selected) == len(expected)
    assert all(selected.intersection([key]) for key in expected)


def test_many_independent_networks_have_bounded_overview_and_search(tmp_path):
    graph = sample()
    seed = graph['nodes'][0]
    count = DETAIL_LIMIT + 100
    graph['nodes'] = [dict(seed, id='top.g{}.u'.format(i), path='top.g{}.u'.format(i), component=i) for i in range(count)]
    graph['edges'], graph['boundaries'], graph['clock_sources'] = [], [], []
    graph['components'] = [{'id': i, 'nodes': [node['id']]} for i, node in enumerate(graph['nodes'])]
    graph['summary'].update(instances=count, edges=0, components=count)
    indexed = index_graph(graph, new_store(tmp_path))
    view = IndexedGraphView(indexed).project('instances')
    assert view.effective_mode == 'hierarchy' and len(view.nodes) <= OVERVIEW_LIMIT
    assert sum(len(n['members']) if n.get('aggregate') else 1 for n in view.nodes.values()) == count
    view.project('hierarchy', scope='top', depth=3)
    assert view.pages > 1 and len(view.nodes) <= OVERVIEW_LIMIT
    view.page = view.pages-1
    view.project('hierarchy', scope='top', depth=3)
    assert view.page == view.pages-1 and len(view.nodes) <= OVERVIEW_LIMIT
    target = view.find('top.g12001.u')[0]
    view.project_focus(target)
    assert target['id'] in view.nodes and len(view.nodes) == 1
    assert len(view.original) == count


def test_index_session_snapshot_restores_without_source_or_full_graph_load(tmp_path):
    graph = sample()
    indexed = index_graph(graph, new_store(tmp_path / 'source'))
    original_nodes = indexed['nodes']
    frozen = snapshot_graph(indexed)
    assert isinstance(frozen, IndexedGraph) and frozen['nodes'] is not original_nodes
    token = uuid.uuid4().hex
    session = SessionStore(tmp_path / 'runs', tmp_path / 'session')
    workspace = dict(config=graph['config'], graph=frozen, path=None, dirty=False, active_layer=2, graph_token=token)
    session.save({'active': 'clock', 'workspaces': {'clock': workspace}})
    manifest = session.directory / ('clock-' + token + '.json')
    assert manifest.stat().st_size < 20000
    indexed.store.close()
    indexed.store.path.unlink()
    restored = session.load()['workspaces']['clock']['graph']
    assert isinstance(restored, IndexedGraph)
    assert canonical(restored, tmp_path / 'restored.json') == graph
    # Type-only edits remain small overlays and are frozen independently.
    node = restored['nodes'][0]
    layer = restored['config']['layers'][node['layer']-1]
    rule = next(r for r in layer['modules'] if r['module'] == node['module'])
    before = snapshot_graph(restored)
    old_kind = rule['kind']
    rule['kind'] = 'div'
    restored['presentation'] = {'kind_overrides': [{'layer': node['layer'], 'module': node['module'], 'kind': 'div', 'from_kind': old_kind, 'instances': 1}]}
    assert restored['nodes'][0]['kind'] == 'div' and before['nodes'][0]['kind'] == old_kind


def test_legacy_reader_handles_huge_component_members_and_unicode(tmp_path):
    graph = sample()
    graph['metadata']['note'] = '汉字" \\ \n' * 30000
    source = tmp_path / 'unicode.json'
    write_graph(source, graph)
    indexed = open_graph(source, threshold=0)
    assert canonical(indexed, tmp_path / 'out.json') == graph
    truncated = tmp_path / 'truncated.json'
    truncated.write_bytes(source.read_bytes()[:-20])
    with pytest.raises(ValueError):
        open_graph(truncated, threshold=0)
