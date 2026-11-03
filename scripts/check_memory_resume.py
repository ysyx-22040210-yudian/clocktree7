"""VM acceptance of RSS-triggered restart and real NPI checkpoint recovery.

The NPI fault tests inject threshold readings at deterministic stop points;
they do NOT allocate 60 GiB. A separate owned Python worker allocates 128 MiB
to exercise the actual /proc RSS monitor and process termination.
"""
import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import clocktree.memory as memory
from clocktree.graph import read_events
from clocktree.session import read_graph
from clocktree.storage import new_store, index_graph, write_graph


HOOK = r'''
proc ct_test_pause {stage marker corrupt soft} {
    global ct_checkpoint ct_stream ct_target_stream ct_rotate_file
    set path [file join [file dirname $ct_checkpoint] $marker]
    if {[file exists $path]} {return}
    set file [open $path w]; puts $file once; close $file
    if {$corrupt} {
        puts -nonewline $ct_stream [format %c 123]
        puts -nonewline $ct_stream "UNCOMMITTED-测试"
        flush $ct_stream
        puts -nonewline $ct_target_stream [format %c 123]
        flush $ct_target_stream
    }
    ct_worker_status $stage
    set start [clock milliseconds]
    while {!$soft || ![file exists $ct_rotate_file]} {
        if {[clock milliseconds]-$start > 20000} {error "Acceptance hook did not receive memory action"}
        after 20
    }
}
'''
HARD_HOOK = r'''
rename ct_register_instance ct_original_register
proc ct_register_instance {path module layer inputs output kind source emit} {
    ct_original_register $path $module $layer $inputs $output $kind $source $emit
    if {$emit && [uplevel 1 {set query_count}] == 2} {ct_test_pause test_hard_enum hard-enum-once 1 0}
}
rename ct_walk ct_original_walk
proc ct_walk {seed direction owner pin trace_id} {
    set count [ct_original_walk $seed $direction $owner $pin $trace_id]
    if {$trace_id == 2} {ct_test_pause test_hard_trace hard-trace-once 1 0}
    return $count
}
'''
SOFT_HOOK = r'''
rename ct_save_checkpoint ct_original_checkpoint
proc ct_save_checkpoint {stage queries traces} {
    ct_original_checkpoint $stage $queries $traces
    if {$stage eq "enumerate" && $queries == 1} {ct_test_pause test_soft_enum soft-enum-once 0 1}
}
rename ct_walk ct_original_walk
proc ct_walk {seed direction owner pin trace_id} {
    set count [ct_original_walk $seed $direction $owner $pin $trace_id]
    if {$trace_id == 2} {ct_test_pause test_soft_trace soft-trace-once 0 1}
    return $count
}
'''


def same_graph(left, right):
    for field, key in [('nodes', 'id'), ('edges', 'id')]:
        assert {item[key] for item in left[field]} == {item[key] for item in right[field]}
    for key in ('instances','edges','roots','terminals','components','expected_pin_traces','observed_pin_traces',
                'unresolved','connectivity_complete','topology_valid'):
        assert left['summary'][key] == right['summary'][key]
    before = {edge['id']: edge for edge in left['edges']}
    for edge in right['edges']:
        old = before[edge['id']]
        for direction in ('load', 'driver'):
            assert list(edge['evidence'][direction]) == list(old['evidence'][direction])
    assert [(b['status'], b['instance'], b['pin'], b['direction'], list(b.get('path', []))) for b in left['boundaries']] == [
        (b['status'], b['instance'], b['pin'], b['direction'], list(b.get('path', []))) for b in right['boundaries']]


def actual_rss(out):
    work = out / 'work'
    work.mkdir(parents=True, exist_ok=True)
    (work / 'clocktree-extract.tcl').write_text('# supervisor fixture, not Verdi', encoding='utf-8')
    worker = work / 'rss-worker.py'
    worker.write_text(r'''
import json, os, pathlib, time
work = pathlib.Path(os.environ['CT7_RESUME']).parent.parent
checkpoint = work / 'checkpoint.json'
state = json.loads(checkpoint.read_text())
raw = pathlib.Path(os.environ['CT7_TEST_RAW'])
targets = work / 'targets.tcl'
with raw.open('ab') as stream:stream.write(b'completed unit\n')
with targets.open('ab') as stream:stream.write(b'completed targets\n')
state['completed_queries'] += 1
state['instances'] += 1
state.update(raw_offset=raw.stat().st_size, targets_offset=targets.stat().st_size,
             stage='complete' if state['completed_queries']==3 else 'enumerate')
temporary = checkpoint.with_suffix('.tmp')
temporary.write_text(json.dumps(state))
os.replace(str(temporary), str(checkpoint))
if state['stage'] != 'complete':
    payload = bytearray(128*1024*1024)
    for index in range(0,len(payload),4096):payload[index] = 1
    while True:time.sleep(.1)
''', encoding='utf-8')
    raw = out / 'raw.jsonl'
    environment = dict(os.environ, CT7_TEST_RAW=str(raw))
    start = time.monotonic()
    def sample(pid):
        if time.monotonic()-start > 45:
            raise AssertionError('RSS acceptance worker failed to finish')
        return memory.process_memory(pid)
    code, _, report = memory.run_verdi([sys.executable, str(worker)], work, environment, raw, out / 'verdi.log', out,
                                       {'memory_limit_gib':.12}, sampler=sample)
    assert code == 0 and report['restart_count'] == 2 and report['peak_rss_gib'] >= .12
    assert report['last_checkpoint']['completed_queries'] == 3
    write_graph(out / 'acceptance.json', {'pass': True, 'worker': 'controlled Python process; not Verdi', 'actual_proc_rss': True,
                                        'limit_gib': .12, 'memory': report})
    return {'pass': True, 'actual_proc_rss': True, 'restart_count': report['restart_count'], 'peak_rss_gib': report['peak_rss_gib']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / 'validation/memory-resume'))
    parser.add_argument('--soc', action='store_true', help='Also inject two restarts into the complete 6,984-instance OpenC910 extraction')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    extraction = importlib.import_module('clocktree.extract')
    actual_runner = extraction.run_verdi
    report = {'actual_rss_monitor': actual_rss(out / 'actual-rss'), 'npi': []}
    for mode, relative in [('clock', 'module/clock-bounded/config.json'), ('reset', 'generate/reset-bounded/config.json')]:
        config = ROOT / 'validation/low-memory/scoped' / relative
        baseline = extraction.extract(config, out / (mode + '-baseline'))
        for kind in ('soft', 'hard'):
            injection_samples = []
            def run(command, work, environment, raw, log_path, output, cfg):
                script = Path(work) / 'clocktree-extract.tcl'
                code = script.read_text(encoding='utf-8')
                code = code.replace('if {[catch {ct_main} message]}', HOOK + (SOFT_HOOK if kind == 'soft' else HARD_HOOK) + '\nif {[catch {ct_main} message]}')
                script.write_text(code, encoding='utf-8')
                actual_peak = [0]
                def sample(pid):
                    actual = memory.process_memory(pid)
                    actual_peak[0] = max(actual_peak[0], actual['rss_bytes'])
                    status = Path(work) / 'worker-status.json'
                    try:
                        stage = json.loads(status.read_text(encoding='utf-8')).get('stage', '')
                    except (OSError, ValueError):
                        stage = ''
                    if stage.startswith('test_'):
                        injection_samples.append({'stage': stage, 'actual_rss_bytes': actual['rss_bytes']})
                        actual['rss_bytes'] = (55 if kind == 'soft' else 61)*memory.GIB
                    return actual
                code, directory, guard = actual_runner(command, work, environment, raw, log_path, output, cfg, sampler=sample)
                guard.update(test_injected_memory_samples=True, test_actual_peak_rss_gib=round(actual_peak[0]/memory.GIB,3))
                write_graph(Path(output) / 'memory.json', guard)
                return code, directory, guard
            extraction.run_verdi = run
            directory = out / (mode + '-' + kind)
            try:
                result = extraction.extract(config, directory)
            finally:
                extraction.run_verdi = actual_runner
            same_graph(baseline, result)
            guard = result['metadata']['memory_protection']
            assert guard['restart_count'] == 2 and guard['attempts'] == 3
            events = list(read_events(directory / 'raw.jsonl'))
            begins = [event['trace_id'] for event in events if event['event'] == 'trace_begin']
            ends = [event['trace_id'] for event in events if event['event'] == 'trace_end']
            assert begins == ends == list(range(1, len(begins)+1))
            assert sum(event['event'] == 'complete' for event in events) == 1
            assert sum(event['event'] == 'instance' for event in events) == len(result['nodes'])
            report['npi'].append({'mode': mode, 'injected': kind, 'pass': True, 'restarts': 2,
                'instances': len(result['nodes']), 'edges': len(result['edges']), 'trace_ids_unique': True,
                'both_direction_evidence_equal': True, 'actual_60_gib_allocation': False, 'samples': injection_samples})
    if args.soc:
        kind, injection_samples = 'hard', []
        extraction.run_verdi = run
        try:
            soc = extraction.extract(ROOT / 'validation/configs/openc910_ordered7_kdb.json', out / 'openc910-hard')
        finally:
            extraction.run_verdi = actual_runner
        same_graph(read_graph(ROOT / 'validation/runs/openc910-ordered7-01/clocktree.json'), soc)
        guard = soc['metadata']['memory_protection']
        assert guard['restart_count'] == 2 and len(soc['nodes']) == 6984 and len(soc['edges']) == 6983
        report['soc'] = {'pass': True, 'instances':6984, 'edges':6983, 'terminals':6959, 'restarts':2,
                         'full_graph_and_dual_evidence_equal':True, 'actual_60_gib_allocation':False, 'samples':injection_samples}
    # A real threshold below the controller's own RSS must stop, not loop.
    blocked = out / 'baseline-too-large'
    try:
        extraction.extract(ROOT / 'validation/low-memory/scoped/module/clock-bounded/config.json', blocked, memory_limit_gib=.001)
        raise AssertionError('Expected baseline memory protection failure')
    except memory.MemoryResumeError:
        state = json.loads((blocked / 'memory.json').read_text(encoding='utf-8'))
        assert state['state'] == 'blocked' and state['attempts'] == 3
        assert not (blocked / 'clocktree.json').exists()
    report['no_progress_stops_without_result'] = True
    report['pass'] = True
    write_graph(out / 'acceptance.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
