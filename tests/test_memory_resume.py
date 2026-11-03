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


def duplicate_rules(count):
    return {'layers':[{'index':1,'hierarchy':'top','modules':[{'module':'gate'} for _ in range(count)]}],
            'design':{'top':'top'},'memory_limit_gib':60}


def module_commit(values, raw, index, total, complete=False):
    state=commit(values,raw,queries=1 if index==total else 0,stage='complete' if complete else 'enumerate')
    state.update(instances=1,completed_modules=index,last_module={'index':index,'layer':1,'module':'gate','scope':'top'})
    write_graph(values['ct_checkpoint'],state)
    Path(values['ct_module_check_request']).write_text(str(index)+'\n',encoding='ascii')
    return state


def test_every_module_boundary_forces_a_fresh_sample_between_periodic_ticks(tmp_path,monkeypatch):
    work,output,raw=setup_run(tmp_path)
    contexts,processes,samples=[],[],[]
    monkeypatch.setattr(memory.time,'monotonic',lambda:100.)
    def launch(command,**kwargs):
        values=context(kwargs['env']);contexts.append(values)
        module_commit(values,raw,1,2)
        process=FakeProcess(12345);processes.append(process)
        return process
    def sample(pid):
        samples.append(pid)
        return {'rss_bytes':memory.GIB}
    def pause(seconds):
        values=contexts[-1]
        reply=Path(values['ct_module_check_ack']).read_text().split()
        assert reply[1:] == ['continue',str(memory.GIB)]
        if reply[0]=='1':module_commit(values,raw,2,2)
        else:
            module_commit(values,raw,2,2,complete=True)
            processes[-1].done=True
    code,_,report=memory.run_verdi(['verdi'],work,{},raw,output/'verdi.log',output,duplicate_rules(2),
                                  sampler=sample,pause=pause,popen=launch,monitored=True)
    assert code==0 and len(samples)==2 and report['module_checks']==2
    assert [json.loads(line)['index'] for line in (output/'module-memory-checks.jsonl').read_text().splitlines()]==[1,2]


def test_partial_group_rules_advance_retry_cursor_even_without_new_instances(tmp_path,monkeypatch):
    work,output,raw=setup_run(tmp_path)
    attempts=[]
    def launch(command,**kwargs):
        values=context(kwargs['env']);attempts.append(values)
        assert int(values['ct_resume_completed_modules'])==len(attempts)-1
        module_commit(values,raw,len(attempts),4,complete=len(attempts)==4)
        return FakeProcess(len(attempts),done=len(attempts)==4)
    monkeypatch.setattr(memory,'_stop_worker',lambda process:process.wait())
    code,_,report=memory.run_verdi(['verdi'],work,{},raw,output/'verdi.log',output,duplicate_rules(4),
        sampler=lambda pid:{'rss_bytes':61*memory.GIB},pause=lambda seconds:None,popen=launch,monitored=True)
    assert code==0 and len(attempts)==4 and report['restart_count']==3
    assert [event['checkpoint']['completed_queries'] for event in report['restarts']]==[0,0,0]
    assert [event['checkpoint']['completed_modules'] for event in report['restarts']]==[1,2,3]


def test_module_cursor_validation_legacy_inference_and_uncommitted_snapshot_cleanup(tmp_path):
    work,output,raw=setup_run(tmp_path)
    raw.write_bytes(b'123456');targets=work/'targets.tcl';targets.write_bytes(b'abc')
    path=work/'checkpoint.json'
    state=dict(version=1,signature='s',stage='enumerate',completed_queries=1,completed_traces=0,instances=1,raw_offset=4,targets_offset=2)
    write_graph(path,state)
    assert memory.read_checkpoint(path,'s',raw,targets,duplicate_rules(2))['completed_modules']==2
    state.update(completed_queries=0,completed_modules=1,last_module={'index':1})
    write_graph(path,state)
    restored=memory.read_checkpoint(path,'s',raw,targets,duplicate_rules(2))
    directory=work/'module-checkpoints';directory.mkdir()
    for name in ('00000001.json','00000002.json','notes.json'):(directory/name).write_text('retained only if committed')
    memory.rollback_files(restored,raw,targets,directory)
    assert sorted(p.name for p in directory.iterdir())==['00000001.json','notes.json']
    state['completed_modules']=2;state['last_module']={'index':2}
    write_graph(path,state)
    with pytest.raises(memory.MemoryResumeError,match='prefix'):
        memory.read_checkpoint(path,'s',raw,targets,duplicate_rules(2))


def test_new_results_require_complete_ordered_module_checkpoints():
    from clocktree.graph import build_graph
    cfg=duplicate_rules(2)
    prefix=[{'event':'metadata','module_checkpoint_protocol':1},
            {'event':'instance','path':'top.a','module':'gate','layer':1,'inputs':['i'],'output':'o'},
            {'event':'trace','instance':'top.a','pin':'i','direction':'driver','results':[{'status':'input_boundary','endpoint':'top.clk'}]},
            {'event':'trace','instance':'top.a','pin':'o','direction':'load','results':[{'status':'no_load'}]}]
    end={'event':'complete','module_rules':2}
    valid=prefix+[{'event':'module_checkpoint','checkpoint':{'index':i}} for i in (1,2)]+[end]
    assert build_graph(valid,cfg)['summary']['connectivity_complete']
    for events in (prefix+[end],prefix+[valid[-3],valid[-3],end]):
        graph=build_graph(events,cfg)
        assert not graph['summary']['connectivity_complete']
        assert any(d['code'] in ('missing_module_checkpoint','module_checkpoint_sequence') for d in graph['diagnostics'])
