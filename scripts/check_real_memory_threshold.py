"""Exercise configurable thresholds with real memory allocated inside Verdi.

Only the scratch Tcl worker is instrumented. The production /proc RSS sampler,
threshold comparisons, signals, checkpoints and resume code are unchanged.
This is controlled memory pressure on small RTL fixtures, not a 60-GiB chip run.
"""
import argparse
import contextlib
import copy
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.config import load_config
from clocktree.graph import read_events
from clocktree.memory import MemoryResumeError
from clocktree.storage import write_graph
from check_memory_resume import same_graph


PRESSURE = r'''
proc ct_real_pressure {stage marker corrupt soft} {
    global ct_checkpoint ct_stream ct_target_stream ct_rotate_file ct_pressure_buffer ct_test_limit
    set directory [file dirname $ct_checkpoint]
    set marker_path [file join $directory $marker]
    if {[file exists $marker_path]} {return}
    set stream [open $marker_path w]; puts $stream once; close $stream
    # Wait for the normal supervisor to observe this exact stage. This avoids
    # calibrating against a stale pre-import RSS sample.
    ct_worker_status $stage
    set started [clock milliseconds]
    set report $::env(CT7_TEST_MEMORY_REPORT)
    while {1} {
        set data ""
        if {![catch {set stream [open $report r]}]} {
            set data [read $stream]; close $stream
        }
        if {[string first [ct_js $stage] $data] >= 0 &&
            [regexp {"rss_bytes":([0-9]+)} $data -> used]} {break}
        if {[clock milliseconds]-$started > 30000} {error "Test did not receive a fresh RSS sample"}
        after 20
    }
    set target [expr {wide($ct_test_limit * 1073741824 * ($soft ? 0.95 : 1.0))}]
    if {!$soft} {incr target 67108864}
    set extra [expr {max(1048576,$target-$used)}]
    if {$extra > 805306368} {error "Test allocation exceeds its 768 MiB safety bound"}
    set audit [open [file join $directory pressure-allocations.jsonl] a]
    puts $audit [ct_object stage [ct_js $stage] pid [pid] limit_gib $ct_test_limit \
        sampled_before_bytes $used requested_extra_bytes $extra target_rss_bytes $target]
    close $audit
    if {$corrupt} {
        puts -nonewline $ct_stream [format %c 123]
        puts -nonewline $ct_stream "UNCOMMITTED-测试"
        flush $ct_stream
        puts -nonewline $ct_target_stream [format %c 123]
        flush $ct_target_stream
    }
    # Nonzero bytes touch real physical pages in this Verdi process. No RSS
    # values are injected and no helper-process RSS is substituted for Verdi.
    set ct_pressure_buffer [string repeat X $extra]
    while {!$soft || ![file exists $ct_rotate_file]} {
        if {[clock milliseconds]-$started > 30000} {error "Test did not receive a memory action"}
        after 20
    }
    unset ct_pressure_buffer
}
'''
HARD = r'''
rename ct_register_instance ct_pressure_original_register
proc ct_register_instance {path module layer inputs output kind source emit} {
    ct_pressure_original_register $path $module $layer $inputs $output $kind $source $emit
    if {$emit && [uplevel 1 {set query_count}] == 2} {ct_real_pressure actual_hard_enum hard-enum-once 1 0}
}
rename ct_walk ct_pressure_original_walk
proc ct_walk {seed direction owner pin trace_id} {
    set count [ct_pressure_original_walk $seed $direction $owner $pin $trace_id]
    if {$trace_id == 2} {ct_real_pressure actual_hard_trace hard-trace-once 1 0}
    return $count
}
'''
SOFT = r'''
rename ct_save_checkpoint ct_pressure_original_checkpoint
proc ct_save_checkpoint {stage queries traces} {
    ct_pressure_original_checkpoint $stage $queries $traces
    if {$stage eq "enumerate" && $queries == 1} {ct_real_pressure actual_soft_enum soft-enum-once 0 1}
}
rename ct_walk ct_pressure_original_walk
proc ct_walk {seed direction owner pin trace_id} {
    set count [ct_pressure_original_walk $seed $direction $owner $pin $trace_id]
    if {$trace_id == 2} {ct_real_pressure actual_soft_trace soft-trace-once 0 1}
    return $count
}
'''


def no_live_process(pid):
    try:
        state = (Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[0]
        return state == 'Z'
    except OSError:
        return True


def gui_check(out, phase):
    from clocktree.gui import ClockTreeApp
    errors = []
    app = ClockTreeApp(runs=out/'gui/runs', session_dir=out/'gui/session')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title, error: errors.append(title+': '+str(error))
    def pump(predicate=lambda:True):
        started = time.monotonic()
        while not predicate():
            app.update()
            if time.monotonic()-started > 90:raise AssertionError(app.status.get())
            time.sleep(.01)
        for _ in range(8):
            if app._destroying:break
            app.update(); time.sleep(.02)
    pump(lambda:not app.busy)
    if phase == 'write':
        app.load_result(out/'clock-hard-config/clocktree.json')
        pump(lambda:not app.busy)
        assert float(app.memory_limit_var.get()) == .375
        assert '重启 2 次' in app.memory_status.get()
        from capture_x11 import x11_image
        picture=x11_image()
        x,y=app.winfo_rootx(),app.winfo_rooty()
        picture.crop((x,y,x+app.winfo_width(),y+app.winfo_height())).save(str(out/'gui/real-threshold-retries.png'))
        app.memory_limit_var.set('0.42')
        assert app.get_config()['memory_limit_gib'] == .42 and not app._config_dirty
        app.save_config(out/'gui/custom-config.json')
        assert json.loads((out/'gui/custom-config.json').read_text())['memory_limit_gib'] == .42
    else:
        assert float(app.memory_limit_var.get()) == .42
        assert app.graph['metadata']['memory_protection']['restart_count'] == 2
        assert len(app.graph['nodes']) == 6 and len(app.graph['edges']) == 4
    app.close(); pump(lambda:app._destroying)
    assert not errors,errors
    write_graph(out/'gui'/('check-'+phase+'.json'),{'pass':True,'phase':phase,'callback_errors':errors})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / 'validation/real-memory-threshold'))
    parser.add_argument('--fixtures', default=str(ROOT / 'validation/low-memory/scoped'))
    parser.add_argument('--gui', action='store_true', help='Also verify editable threshold, real restart result display and fresh-process restore')
    parser.add_argument('--gui-phase', choices=['write','read'], help=argparse.SUPPRESS)
    args = parser.parse_args()
    out, fixtures = Path(args.out).resolve(), Path(args.fixtures).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.gui_phase:
        gui_check(out,args.gui_phase)
        return
    extraction = importlib.import_module('clocktree.extract')
    runner = extraction.run_verdi
    config_paths = {'clock': fixtures / 'module/clock-bounded/config.json',
                    'reset': fixtures / 'generate/reset-bounded/config.json'}
    if not all(path.is_file() for path in config_paths.values()):
        subprocess.check_call([sys.executable, str(ROOT / 'scripts/check_scoped_trace.py'), '--out', str(fixtures)])
    baselines = {mode: extraction.extract(path, out / (mode + '-baseline'), memory_limit_gib=.75)
                 for mode, path in config_paths.items()}
    cases = []
    specifications = [('clock', 'soft', .5, False), ('clock', 'hard', .375, False),
                      ('reset', 'soft', .5, False), ('reset', 'hard', .375, False),
                      ('clock', 'hard', .625, True)]
    for mode, kind, limit, cli_override in specifications:
        directory = out / '{}-{}-{}'.format(mode, kind, 'cli' if cli_override else 'config')
        directory.mkdir(parents=True, exist_ok=True)
        cfg = copy.deepcopy(load_config(config_paths[mode]))
        cfg['memory_limit_gib'] = .5 if cli_override else limit
        config_file = directory / 'input.json'
        write_graph(config_file, cfg)
        def instrument(command, work, environment, raw, log_path, output, configuration):
            worker = Path(work) / 'clocktree-extract.tcl'
            source = worker.read_text(encoding='utf-8')
            hook = 'set ct_test_limit {}\n'.format(configuration['memory_limit_gib']) + PRESSURE + (SOFT if kind == 'soft' else HARD)
            source = source.replace('if {[catch {ct_main} message]}', hook + '\nif {[catch {ct_main} message]}')
            worker.write_text(source, encoding='utf-8')
            environment = dict(environment, CT7_TEST_MEMORY_REPORT=str(Path(output) / 'memory.json'))
            # Deliberately do not pass sampler/pause/popen overrides.
            code, final, guard = runner(command, work, environment, raw, log_path, output, configuration)
            guard.update(test_real_verdi_allocations=True, test_rss_readings_injected=False)
            write_graph(Path(output) / 'memory.json', guard)
            return code, final, guard
        extraction.run_verdi = instrument
        try:
            if cli_override:
                from clocktree.__main__ import main as cli
                previous = sys.argv
                sys.argv = ['clocktree', 'extract', '--config', str(config_file), '--out', str(directory),
                            '--memory-limit-gib', str(limit)]
                try:
                    stream = io.StringIO()
                    with contextlib.redirect_stdout(stream):
                        assert cli() == 0
                    (directory / 'cli-output.txt').write_text(stream.getvalue(), encoding='utf-8')
                finally:
                    sys.argv = previous
                from clocktree.session import read_graph
                result = read_graph(directory / 'clocktree.json')
            else:
                result = extraction.extract(config_file, directory)
        finally:
            extraction.run_verdi = runner
        same_graph(baselines[mode], result)
        guard = result['metadata']['memory_protection']
        assert guard['limit_gib'] == limit and guard['attempts'] == 3 and guard['restart_count'] == 2
        assert guard['state'] == 'finished' and guard['test_rss_readings_injected'] is False
        expected = 'checkpoint_rotation' if kind == 'soft' else 'hard_limit'
        assert [event['reason'] for event in guard['restarts']] == [expected, expected]
        for event in guard['restarts']:
            assert event['rss_gib'] >= limit * (.9 if kind == 'soft' else 1.)
            if kind == 'soft':assert event['rss_gib'] < limit
        events = list(read_events(directory / 'raw.jsonl'))
        begins = [event['trace_id'] for event in events if event['event'] == 'trace_begin']
        ends = [event['trace_id'] for event in events if event['event'] == 'trace_end']
        assert begins == ends == list(range(1, len(begins)+1))
        assert sum(event['event'] == 'complete' for event in events) == 1
        assert sum(event['event'] == 'instance' for event in events) == len(result['nodes'])
        assert guard['restarts'][0]['checkpoint']['completed_queries'] == 1
        assert guard['restarts'][1]['checkpoint']['completed_traces'] == (2 if kind == 'soft' else 0)
        allocations = [json.loads(line) for line in (Path(guard['checkpoint_file']).parent / 'pressure-allocations.jsonl').read_text().splitlines()]
        assert len(allocations) == 2 and all(no_live_process(item['pid']) for item in allocations)
        assert all((directory / name).is_file() for name in ('memory.json','memory-restarts.jsonl','verdi.log'))
        case = {'pass':True, 'tree_type':mode, 'action':kind, 'configured_limit_gib':cfg['memory_limit_gib'],
                'effective_limit_gib':limit, 'cli_override':cli_override, 'restarts':2, 'attempts':3,
                'actual_peak_rss_gib':guard['peak_rss_gib'], 'trigger_rss_gib':[event['rss_gib'] for event in guard['restarts']],
                'instances':len(result['nodes']), 'edges':len(result['edges']), 'dual_evidence_equal':True,
                'trace_ids_unique':True, 'all_workers_terminated':True, 'allocations':allocations}
        cases.append(case)
        print(json.dumps({key:case[key] for key in ('tree_type','action','effective_limit_gib','restarts','trigger_rss_gib','pass')}),flush=True)
    # Actual RSS below the controller baseline must stop after bounded retries.
    directory = out / 'threshold-too-low'
    try:
        extraction.extract(config_paths['clock'], directory, memory_limit_gib=.001)
        raise AssertionError('Expected an insufficient-baseline-memory failure')
    except MemoryResumeError:
        guard = json.loads((directory / 'memory.json').read_text())
        assert guard['state'] == 'blocked' and guard['attempts'] == 3
        assert not (directory / 'clocktree.json').exists()
    report = {'pass':True, 'measurement':'unmodified production /proc RSS sampler',
              'pressure':'real bytes allocated in Verdi via test-only scratch Tcl',
              'rss_readings_injected':False, 'cases':cases, 'too_low_threshold_stops_without_result':True}
    if args.gui:
        for phase in ('write','read'):
            subprocess.check_call([sys.executable,__file__,'--out',str(out),'--gui-phase',phase])
        report['gui_custom_value_saved_and_restored'] = True
    write_graph(out / 'acceptance.json', report)
    print(json.dumps({'pass':True, 'cases':len(cases), 'rss_readings_injected':False},indent=2))


if __name__ == '__main__':main()
