import copy
import json
import os
from pathlib import Path

from clocktree.register_results import latest_register_result, register_result_stamp
from clocktree.register_trace import create_database
from clocktree.storage import write_graph
from clocktree.sv_templates import prepare
from test_sv_templates import setup


CONFIG = {'design': {'top': 'chip', 'elab': '/design/chip.elab++'}, 'tree_type': 'clock'}


def result(path, when, config=None, finished=True, complete=True):
    config = copy.deepcopy(config or CONFIG)
    path.mkdir(parents=True)
    write_graph(path / 'manifest.json', {'request': {'config': config}})
    db = create_database(path / 'registers.sqlite'); db.close()
    write_graph(path / 'register-trace.json', {
        'operation': 'auxiliary-register-trace', 'config': config,
        'summary': {'finished': finished, 'complete': complete}})
    os.utime(str(path / 'register-trace.json'), (when, when))
    return path


def test_newest_completed_result_not_saved_directory_or_uuid_order(tmp_path):
    runs = tmp_path / 'runs'
    old = result(runs / '.register-trace' / 'zzz-old', 100)
    new = result(runs / '.register-trace' / 'aaa-new', 200, complete=False)
    result(runs / '.register-trace' / 'running', 300, finished=False)
    assert latest_register_result(CONFIG, runs, [old]) == str(new)


def test_selection_keeps_design_source_top_and_analysis_mode_separate(tmp_path):
    expected = result(tmp_path / '.register-trace' / 'correct', 100)
    variants = [dict(tree_type='reset'), dict(design={'top':'other','elab':'/design/chip.elab++'}),
                dict(design={'top':'chip','elab':'/design/other.elab++'})]
    for i, change in enumerate(variants):
        config = copy.deepcopy(CONFIG); config.update(change)
        result(tmp_path / '.register-trace' / str(i), 200+i, config=config)
    assert latest_register_result(CONFIG, tmp_path) == str(expected)


def test_skips_missing_corrupt_or_resumed_jobs_and_recovers_from_stale_snapshot(tmp_path):
    good = result(tmp_path / '.register-trace' / 'good', 100)
    write_graph(good / 'status.json', {'stage':'query'})
    (good / 'events.jsonl').write_text(json.dumps({'stage':'finished'})+'\n', encoding='utf-8')
    broken = []
    for i in range(7):
        broken.append(result(tmp_path / '.register-trace' / str(i), 200+i))
    (broken[0] / 'registers.sqlite').unlink()
    (broken[1] / 'register-trace.json').write_text('{', encoding='utf-8')
    (broken[2] / 'manifest.json').write_text('{}', encoding='utf-8')
    write_graph(broken[3] / 'status.json', {'stage':'query'})
    write_graph(broken[4] / 'status.json', {'stage':'interrupted'})
    (broken[5] / 'registers.sqlite').write_bytes(b'not a database')
    write_graph(broken[6] / 'manifest.json', {'request': {'config': {'design': {'top': 'wrong'}}}})
    assert latest_register_result(CONFIG, tmp_path) == str(good)


def test_external_saved_results_and_siblings_are_discovered_without_recursive_scan(tmp_path):
    external = tmp_path / 'external'
    old = result(external / 'old', 100)
    new = result(external / 'new', 200)
    result(external / 'rtl' / 'nested' / 'not-a-task-location', 999)
    assert latest_register_result(CONFIG, tmp_path / 'empty', [old / 'manifest.json']) == str(new)
    assert latest_register_result(CONFIG, tmp_path / 'empty') == ''


def test_result_revision_detects_same_directory_update_and_is_saved_in_plan(tmp_path):
    graph, registers = setup(tmp_path)
    plan = prepare(graph, registers, tmp_path / 'plan', False)
    stamp = register_result_stamp(registers)
    assert plan.source()['register_result_stamp'] == stamp
    assert register_result_stamp(registers / 'manifest.json') == stamp
    write_graph(registers / 'register-trace.json', {'updated': True})
    assert register_result_stamp(registers) != stamp
    assert register_result_stamp('') is None


def test_latest_selection_generates_actual_new_register_binding(tmp_path):
    graph, registers = setup(tmp_path)
    old = result(tmp_path / 'runs' / '.register-trace' / 'old', 100, graph['config'])
    new = result(tmp_path / 'runs' / '.register-trace' / 'new', 200, graph['config'])
    for directory in (old,new):
        (directory/'registers.sqlite').write_bytes((registers/'registers.sqlite').read_bytes())
    db = create_database(new/'registers.sqlite')
    db.execute("UPDATE results SET endpoint='chip.C[6]' WHERE endpoint='chip.C[7]'")
    db.commit(); db.close()
    selected = latest_register_result(graph['config'], tmp_path/'runs', [old])
    plan = prepare(graph, selected, tmp_path/'plan', False)
    exported = plan.export({'with_mux':'$var_clkip', 'without_mux':'$var_gt0'}, tmp_path/'all.sv')
    assert exported['blocks'] == 2 and exported['skipped'] == 0
    code = (tmp_path/'all.sv').read_text(encoding='utf-8')
    assert 'chip.C[6]' in code and 'chip.C[7]' not in code
