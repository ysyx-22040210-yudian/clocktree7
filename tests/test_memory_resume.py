import json
import os
from pathlib import Path
import tkinter

import pytest

from clocktree.config import validate_config
from clocktree.profiles import empty_config
from clocktree.storage import write_graph
from clocktree.session import same_config
import clocktree.memory as memory


def context(environment):
    interpreter = tkinter.Tcl()
    interpreter.eval(Path(environment['CT7_RESUME']).read_text(encoding='utf-8'))
    return {name: interpreter.getvar(name) for name in interpreter.splitlist(interpreter.eval('info globals ct_*'))}


def setup_run(tmp_path):
    work, output = tmp_path / 'work', tmp_path / 'output'
    work.mkdir(); output.mkdir()
    (work / 'clocktree-extract.tcl').write_text('# copied worker')
    return work, output, output / 'raw.jsonl'


def commit(values, raw, queries=1, traces=0, stage='enumerate'):
    targets = Path(values['ct_target_file'])
    raw.write_text('committed query {} trace {}\n'.format(queries, traces), encoding='utf-8')
    targets.write_text('safe manifest\n', encoding='utf-8')
    state = dict(version=1, signature=values['ct_signature'], stage=stage, completed_queries=queries,
                 completed_traces=traces, instances=queries, raw_offset=raw.stat().st_size, targets_offset=targets.stat().st_size)
    write_graph(values['ct_checkpoint'], state)
    return state


class FakeProcess:
    def __init__(self, pid, done=False, code=0):
        self.pid, self.done, self.code = pid, done, code

    def poll(self):
        return self.code if self.done else None

    def wait(self, *args, **kwargs):
        assert not args and not kwargs, 'no extraction runtime timeout'
        self.done = True
        return self.code


def test_hard_limit_rolls_back_partial_query_then_partial_trace(tmp_path, monkeypatch):
    work, output, raw = setup_run(tmp_path)
    attempts, killed = [], []
    def launch(command, **kwargs):
        values = context(kwargs['env'])
        attempts.append(values)
        count = len(attempts)
        assert kwargs['start_new_session']
        if count > 1:
            assert 'BROKEN' not in raw.read_text()
            assert 'BROKEN' not in Path(values['ct_target_file']).read_text()
        if count == 1:
            commit(values, raw, queries=1)
        elif count == 2:
            assert int(values['ct_resume_completed_queries']) == 1
            commit(values, raw, queries=2, traces=32, stage='trace')
        else:
            assert int(values['ct_resume_completed_traces']) == 32
            commit(values, raw, queries=2, traces=64, stage='complete')
            return FakeProcess(count, done=True)
        with raw.open('a') as stream:stream.write('BROKEN')
        with Path(values['ct_target_file']).open('a') as stream:stream.write('BROKEN')
        return FakeProcess(count)
    def stop(process):
        killed.append(process.pid); process.done = True
        return -9
    monkeypatch.setattr(memory, '_stop_worker', stop)
    code, last, report = memory.run_verdi(['verdi'], work, {}, raw, output / 'verdi.log', output, {},
        sampler=lambda pid: {'rss_bytes': 61*memory.GIB}, pause=lambda seconds: None, popen=launch, monitored=True)
    assert code == 0 and len(attempts) == 3 and killed == [1, 2]
    assert report['restart_count'] == 2 and report['peak_rss_gib'] == 61
    assert report['last_checkpoint']['stage'] == 'complete'
    assert last.name == 'attempt-0003'


def test_soft_limit_requests_checkpoint_and_graceful_rotation(tmp_path):
    work, output, raw = setup_run(tmp_path)
    attempts, processes = [], []
    def launch(command, **kwargs):
        values = context(kwargs['env']); attempts.append(values)
        process = FakeProcess(len(attempts), done=len(attempts)>1)
        processes.append(process)
        if len(attempts)>1:
            assert int(values['ct_resume_completed_queries']) == 1
            commit(values, raw, stage='complete')
        return process
    def pause(seconds):
        values = attempts[-1]
        assert Path(values['ct_rotate_file']).is_file()
        commit(values, raw)
        write_graph(values['ct_status_file'], {'stage': 'rotating'})
        processes[-1].done = True
    code, _, report = memory.run_verdi(['verdi'], work, {}, raw, output / 'verdi.log', output, {},
        sampler=lambda pid: {'rss_bytes': 55*memory.GIB}, pause=pause, popen=launch, monitored=True)
    assert code == 0 and report['restart_count'] == 1
    assert report['restarts'][0]['reason'] == 'checkpoint_rotation'


def test_import_or_oversized_unit_cannot_restart_forever(tmp_path, monkeypatch):
    work, output, raw = setup_run(tmp_path)
    attempts = []
    def launch(command, **kwargs):
        attempts.append(context(kwargs['env']))
        raw.write_text('uncommitted')
        return FakeProcess(len(attempts))
    monkeypatch.setattr(memory, '_stop_worker', lambda process: process.wait())
    with pytest.raises(memory.MemoryResumeError, match='No modules were skipped'):
        memory.run_verdi(['verdi'], work, {}, raw, output / 'verdi.log', output, {}, sampler=lambda pid: {'rss_bytes':61*memory.GIB},
                         pause=lambda seconds: None, popen=launch, monitored=True)
    assert len(attempts) == memory.MAX_STALLED_ATTEMPTS
    report = json.loads((output / 'memory.json').read_text())
    assert report['state'] == 'blocked' and not (output / 'clocktree.json').exists()


def test_memory_disabled_waits_without_sampling_or_runtime_timeout(tmp_path):
    work, output, raw = setup_run(tmp_path)
    calls = []
    def launch(command, **kwargs):
        calls.append(1)
        values = context(kwargs['env'])
        commit(values, raw, stage='complete')
        return FakeProcess(1)
    def forbidden(pid):raise AssertionError('monitor disabled')
    code, _, report = memory.run_verdi(['verdi'], work, {}, raw, output / 'verdi.log', output,
                                      {'memory_limit_gib':0}, sampler=forbidden, popen=launch, monitored=True)
    assert code == 0 and not report['enabled'] and len(calls) == 1


def test_ordinary_eda_failure_is_not_retried_as_memory_pressure(tmp_path):
    work, output, raw = setup_run(tmp_path)
    calls = []
    def launch(command, **kwargs):
        calls.append(1)
        return FakeProcess(1, done=True, code=7)
    code, _, report = memory.run_verdi(['verdi'], work, {}, raw, output / 'verdi.log', output, {}, popen=launch, monitored=True)
    assert code == 7 and len(calls) == 1 and report['state'] == 'failed' and report['restart_count'] == 0


def test_checkpoint_backup_and_offsets_are_validated_before_truncation(tmp_path):
    work, output, raw = setup_run(tmp_path)
    targets, checkpoint = work / 'targets.tcl', work / 'checkpoint.json'
    raw.write_bytes(b'12345'); targets.write_bytes(b'abc')
    valid = memory._initial_checkpoint('signature')
    valid.update(raw_offset=4, targets_offset=2)
    write_graph(str(checkpoint)+'.previous', valid)
    checkpoint.write_text('broken')
    restored = memory.read_checkpoint(checkpoint, 'signature', raw, targets)
    memory.rollback_files(restored, raw, targets)
    assert raw.read_bytes() == b'1234' and targets.read_bytes() == b'ab'
    with pytest.raises(memory.MemoryResumeError):memory.read_checkpoint(checkpoint, 'wrong', raw, targets)
    valid['raw_offset'] = 100
    write_graph(str(checkpoint)+'.previous', valid)
    with pytest.raises(memory.MemoryResumeError):memory.read_checkpoint(checkpoint, 'signature', raw, targets)


def test_memory_sampler_counts_owned_descendants_and_group_only(tmp_path, monkeypatch):
    def stat(pid, parent, group, pages):
        entry = tmp_path / str(pid); entry.mkdir()
        fields = ['0']*22
        fields[0], fields[1], fields[2], fields[21] = 'S', str(parent), str(group), str(pages)
        (entry / 'stat').write_text('{} (name (with spaces)) {}'.format(pid, ' '.join(fields)))
    stat(10, 1, 10, 10)
    stat(11, 10, 11, 20)
    stat(12, 1, 10, 30)
    stat(13, 1, 13, 9999)
    stat(os.getpid(), 1, os.getpid(), 40)
    monkeypatch.setattr(memory.os, 'sysconf', lambda name:4096, raising=False)
    sample = memory.process_memory(10, tmp_path)
    assert sample['rss_bytes'] == 100*4096 and sample['processes'] == 4


@pytest.mark.parametrize('value', [-1, True, '60', float('nan'), float('inf')])
def test_invalid_memory_limit_is_rejected(value):
    cfg = empty_config('clock')
    cfg.update(memory_limit_gib=value)
    cfg['design'] = {'top':'top', 'elab':'/kdb'}
    cfg['layers'][0]['modules'] = [{'module':'gate', 'kind':'gate', 'clock_inputs':['i'], 'clock_output':'o'}]
    with pytest.raises(ValueError, match='memory_limit_gib'):validate_config(cfg)


def test_runtime_memory_threshold_does_not_invalidate_proven_connectivity():
    cfg = empty_config('clock')
    assert same_config(cfg, dict(cfg, memory_limit_gib=42.5))


def test_target_manifest_is_parsed_as_data_and_never_executed(tmp_path):
    script = (Path(__file__).resolve().parents[1] / 'npi/extract.tcl').read_text(encoding='utf-8').split('if {[catch {ct_main}')[0]
    interpreter = tkinter.Tcl()
    interpreter.eval(script)
    interpreter.eval('''
        set ct_resume_instances 1
        set ct_instances {}
        proc ct_register_instance {path module layer inputs output kind source emit} {
            global ct_instances captured
            dict set ct_instances $path $layer
            set captured [list $path $module $source $emit]
        }
    ''')
    target = tmp_path / 'targets.tcl'
    interpreter.setvar('ct_target_file', str(target))
    record = interpreter.call('list','ct_target','top.a','mux',1,('i',),'o','mux','[set injected 1];\nRTL info')
    # Tcl objects stringify through Tcl itself to preserve literal list syntax.
    interpreter.setvar('record', record)
    target.write_text(interpreter.eval('format %s $record')+'\n', encoding='utf-8')
    interpreter.call('ct_restore_targets')
    assert interpreter.eval('info exists injected') == '0'
    target.write_text('set injected 1\n')
    with pytest.raises(tkinter.TclError, match='Invalid target'):interpreter.call('ct_restore_targets')
