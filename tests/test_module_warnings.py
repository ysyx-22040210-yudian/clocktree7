import csv

import pytest

from clocktree.graph import build_graph
from clocktree.issues import IssueIndex, LABELS
from clocktree.sv_templates import structure_issues


def zero_match(layer=1, scope='top.domain'):
    # Previous worker versions did not include a severity in the raw stream.
    return dict(event='diagnostic', code='missing_module', module='optional_gate',
                hierarchy=scope, layer=layer, search_mode='direct_children')


@pytest.mark.parametrize('mode', ['target', 'full'])
@pytest.mark.parametrize('tree', ['clock', 'reset'])
def test_empty_module_rule_preserves_traced_terminals(mode, tree):
    events = [
        dict(event='metadata', extraction_mode=mode),
        dict(event='instance', path='top.g', module='kept', layer=1, inputs=['i'], output='o'),
        dict(event='trace', instance='top.g', pin='i', direction='driver',
             results=[dict(status='input_boundary', endpoint='top.clk')])]
    if mode == 'target':
        events.append(dict(event='output_check', instance='top.g', pin='o', status='ok'))
    else:
        events.append(dict(event='trace', instance='top.g', pin='o', direction='load', results=[dict(status='no_load')]))
    events.append(dict(event='complete'))
    config = dict(tree_type=tree)
    baseline = build_graph(events, config)
    result = build_graph([zero_match(), zero_match(2, 'top.other')] + events, config)
    assert result['nodes'] == baseline['nodes']
    assert result['summary']['terminals'] == 1
    assert result['summary']['connectivity_complete'] and result['summary']['topology_valid']
    assert result['summary']['physical_loads_checked'] == (mode == 'full')
    assert result['summary']['uninstantiated_module_rules'] == 2
    assert result['summary']['unresolved'] == 0
    assert [row['severity'] for row in result['diagnostics']] == ['warning', 'warning']


def test_empty_graph_reports_rule_in_issues_and_csv(tmp_path):
    graph = build_graph([zero_match(), dict(event='complete')], {})
    assert graph['nodes'] == [] and graph['summary']['connectivity_complete']
    # Also exercise the GUI/CSV classification of an archived result.
    graph['diagnostics'] = [zero_match()]
    issues = IssueIndex(graph)
    try:
        issues.build()
        category = LABELS['missing_module'][0]
        total, affected, rows = issues.query(category=category)
        assert (total, affected) == (1, 0)
        row = rows[0]
        assert row[2:6] == ('警告', '1', 'optional_gate', '')
        assert 'top.domain' in row[8] and 'optional_gate' in row[8]
        path = tmp_path/'warnings.csv'
        assert issues.export(path, category=category) == 1
        with path.open(encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.DictReader(stream))
        assert [(r['严重程度'], r['Module'], r['原因']) for r in exported] == [('警告', 'optional_gate', row[8])]
    finally:
        issues.close()


@pytest.mark.parametrize('events,code', [
    ([], None),
    ([dict(event='diagnostic', code='scope_not_found', hierarchy='top.missing', layer=2), dict(event='complete')], 'scope_not_found'),
    ([dict(event='metadata', module_checkpoint_protocol=1), dict(event='complete', module_rules=1)], 'missing_module_checkpoint'),
    ([dict(event='instance', path='top.g', module='kept', layer=1, inputs=['i'], output='o'), dict(event='complete')], 'missing_trace'),
])
def test_empty_module_rule_does_not_hide_failed_or_unfinished_work(events, code):
    graph = build_graph([zero_match()] + events, {'layers':[{'index':1, 'modules':[]}]})
    assert graph['summary']['uninstantiated_module_rules'] == 1
    assert not graph['summary']['connectivity_complete']
    if code:assert code in {d['code'] for d in graph['diagnostics']}


def test_sv_unknown_coverage_failure_is_not_explained_by_a_warning():
    graph = build_graph([zero_match(), dict(event='complete')], {})
    assert not structure_issues(graph)[1]
    graph['summary']['connectivity_complete'] = False
    assert structure_issues(graph)[1]
