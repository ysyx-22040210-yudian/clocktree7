import copy
import csv
import json
from contextlib import closing

import pytest

from clocktree.config import div_source_scopes
from clocktree.div_sources import classify_div_source
from clocktree.register_trace import RegisterIndex, create_database
from clocktree.storage import write_graph
from clocktree.sv_templates import graph_for_templates, prepare
from test_sv_branch_endpoints import graph, node


def test_div_scope_matching_uses_rtl_boundaries_and_the_most_specific_path():
    scopes = ('chip.crg', 'chip.crg.bank[2]')
    assert classify_div_source('chip.crg.bank[2].Q[7]', scopes) == {
        'status': 'matched', 'scope': scopes[1], 'level': 2,
    }
    assert classify_div_source('chip.crg.bank[20].Q[7]', scopes)['level'] == 1
    assert classify_div_source('chip.crg2.Q', scopes)['status'] == 'outside'
    assert classify_div_source('chip.crg', scopes)['status'] == 'outside'
    assert classify_div_source('chip.crg.Q', ()) == {'status': 'unconfigured'}


@pytest.mark.parametrize('paths', [
    ['chip.ctrl', ''], ['chip.ctrl', 'chip.ctrl.'], ['chip.ctrl', 'other.ctrl'],
    ['chip.ctrl', 'chip.ctrl.*'], ['chip.ctrl'], ['chip.ctrl', 2],
])
def test_div_source_fields_reject_invalid_pairs(paths):
    config = {'design': {'top': 'chip'}, 'div_source_scopes': paths}
    with pytest.raises(ValueError, match='DIV 来源'):
        div_source_scopes(config)


def test_div_metadata_uses_saved_register_bits_in_table_csv_and_sv(tmp_path):
    device = node('d', 'div', 3)
    device['aux_ports'] = {'div': 'ratio'}
    original = graph([device])
    config = copy.deepcopy(original['config'])
    config['div_source_scopes'] = ['chip.ctrl_a', 'chip.ctrl_b']
    updated = graph_for_templates(original, config)
    assert updated['nodes'] is original['nodes']
    directory = tmp_path/'registers'
    directory.mkdir()
    # Simulate a result saved before source labels were added.
    write_graph(directory/'manifest.json', {'request': {'config': original['config']}})
    endpoints = ['chip.ctrl_a.Q[7]', 'chip.ctrl_b.Q[2]', 'chip.ctrl_a_extra.Q[3]']
    seed = {'instance': device['id'], 'role': 'div', 'pin': 'ratio'}
    names = ['ratio[{}]'.format(i) for i in range(len(endpoints))]
    with closing(create_database(directory/'registers.sqlite')) as db:
        db.execute('INSERT INTO jobs VALUES(1,?,?,?)', (json.dumps(seed), 'complete',
                   json.dumps({'bits': [device['id']+'.'+bit for bit in names]})))
        db.executemany('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',
                       [(1,bit,endpoint,'Q','direct','register','',None,'{}') for bit,endpoint in zip(names,endpoints)])
        db.commit()
    index = RegisterIndex(directory, div_source_scopes(config))
    total, rows = index.query()
    assert total == 3
    tags = [index.detail(row[0])['div_source'] for row in rows]
    assert [(tag['status'], tag.get('level')) for tag in tags] == [('matched',1),('matched',2),('outside',None)]
    index.export(tmp_path/'mapping.csv')
    with (tmp_path/'mapping.csv').open(encoding='utf-8-sig') as stream:
        mappings = list(csv.DictReader(stream))
    assert [row['DIV 来源'] for row in mappings] == ['来源层次 1','来源层次 2','来源未归属']
    index.export_json(tmp_path/'mapping.json')
    exported = json.loads((tmp_path/'mapping.json').read_text(encoding='utf-8'))
    assert [row['div_source'] for row in exported['results']] == tags
    plan = prepare(updated,directory,tmp_path/'plan',False)
    row = plan.branches()[1][0]
    candidates = plan.candidates(row,'var_div')
    assert len(candidates) == 1 and [r['signal'] for r in candidates[0]['div_sources']] == endpoints
    templates = {'without_mux': 'wire [2:0] d = $var_div;'}
    assert not plan.resolve(row, templates)['ready']
    result = plan.export(templates,tmp_path/'generated.sv',{row['id']:{'var_div':candidates[0]['id']}})
    assert result['blocks'] == 1
    assert '{'+', '.join(reversed(endpoints))+'}' in (tmp_path/'generated.sv').read_text()
    report = json.loads((tmp_path/'generated.sv.mapping.json').read_text(encoding='utf-8'))
    assert report['branches'][0]['selected']['var_div']['div_sources'] == candidates[0]['div_sources']
