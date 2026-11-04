import json
import os
import socket
import subprocess
import sys

from clocktree.runtime import (RunJournal, WorkerEvents, alive, diagnose,
                              diagnose_gui, last_event, process_identity, read_json, bind_gui)


def test_stage_is_durable_before_operation_and_mirrored(tmp_path):
    journal=RunJournal.start(tmp_path)
    journal.emit('begin','modules','native query',{'module':'clock_gate','scope':'top.crg'})
    status=read_json(tmp_path/'run-status.json')
    assert status['state']=='running' and status['steps']['modules']=='running'
    assert status['last_entered']['context']['module']=='clock_gate'
    assert (tmp_path/'run.log').read_bytes()==journal.log_file.read_bytes()
    assert last_event(journal.event_file)['kind']=='begin'
    journal.emit('end','modules','committed')
    assert journal.state()['last_completed']['stage']=='modules'


def test_rerun_preserves_immutable_logs_and_previous_mirror(tmp_path):
    first=RunJournal.start(tmp_path)
    first.emit('begin','import','old run')
    old=first.event_file.read_bytes()
    second=RunJournal.start(tmp_path)
    second.emit('failed','config','bad configuration')
    assert first.event_file.read_bytes()==old
    assert diagnose(first.directory)['stage']=='import'
    assert diagnose(tmp_path)['stage']=='config'
    history=list((tmp_path/'.clocktree-history').glob('*/run-events.jsonl'))
    assert len(history)==1 and history[0].read_bytes()==old


def test_incomplete_append_does_not_hide_last_record(tmp_path):
    path=tmp_path/'events.jsonl'
    path.write_bytes(b'{"stage":"import"}\n'+b'{"partial":')
    assert last_event(path)['stage']=='import'
    path.write_text(json.dumps({'stage':'trace','context':'x'*80000})+'\n{"partial":',encoding='utf-8')
    assert last_event(path)['stage']=='trace'


def test_pid_liveness_does_not_signal_or_terminate_windows_process():
    child=subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.read()'],stdin=subprocess.PIPE)
    try:
        identity=process_identity(child.pid)
        assert alive(identity) is True
        assert child.poll() is None
        reused=dict(identity,start_ticks='different')
        if identity.get('start_ticks'):assert alive(reused) is False
        assert alive(dict(identity,host='a different device')) is None
    finally:
        child.stdin.close();child.wait(timeout=10)
    assert alive(identity) is False


def test_dead_controller_uses_newer_worker_stage_and_never_infers_oom(tmp_path):
    journal=RunJournal.start(tmp_path)
    worker=tmp_path/'worker.jsonl'
    worker.write_text(json.dumps({'kind':'item_begin','stage':'trace',
        'context':{'instance':'top.crg.u_gate','pin':'clk_o','direction':'load','trace_id':87}})+'\n')
    journal.emit('begin','import','loading',worker_events=str(worker))
    state=journal.state();state['owner']={'host':socket.gethostname(),'pid':2147483647}
    from clocktree.storage import write_graph
    write_graph(journal.status_file,state)
    result=diagnose(tmp_path)
    assert result['state']=='interrupted' and result['stage']=='trace'
    assert result['context']['trace_id']==87 and result['cause']=='unknown'
    assert 'OOM' in result['detail']


def test_worker_boundaries_and_partial_records_are_streamed(tmp_path):
    journal=RunJournal.start(tmp_path)
    path=tmp_path/'worker.jsonl'
    path.write_text('\n'.join(json.dumps({'stage':'trace','kind':kind,'context':{'trace_id':i}})
        for i,kind in enumerate(['begin','item_begin','item_end','item_begin','end']))+'\n{"kind":')
    reader=WorkerEvents(path)
    reader.drain(journal,2,{},all_records=True)
    events=[json.loads(line) for line in journal.event_file.read_text(encoding='utf-8').splitlines()]
    assert [e['kind'] for e in events if e['stage']=='trace']==['begin','item_begin','end']
    assert journal.state()['attempt']==2 and journal.state()['steps']['trace']=='done'
    with path.open('a') as stream:stream.write('"failed","stage":"trace"}\n')
    reader.drain(journal,2,{})
    assert journal.state()['state']=='failed'


def test_completed_worker_cannot_mask_postprocessing_interruption(tmp_path):
    journal=RunJournal.start(tmp_path)
    worker=tmp_path/'worker.jsonl'
    worker.write_text('{"kind":"end","stage":"trace"}\n')
    journal.emit('begin','topology','build graph',{'raw':'raw.jsonl'},worker_events=str(worker))
    report=diagnose(tmp_path)
    assert report['stage']=='topology' and report['context']['raw']=='raw.jsonl'


def test_failure_wrapper_retains_worker_signal_and_context(tmp_path):
    journal=RunJournal.start(tmp_path)
    journal.emit('failed','modules','worker exited',{'module':'gate'},returncode=-9,exit_signal='SIGKILL')
    journal.emit('begin','topology','attempt to build partial evidence')
    journal.fail(RuntimeError('extract wrapper'))
    report=diagnose(tmp_path)
    assert report['cause']=='worker_exit_-9'
    assert report['last_failure']['exit_signal']=='SIGKILL'
    assert report['context']['module']=='gate'


def test_reopening_gui_preserves_previous_crash_reference(tmp_path):
    from clocktree.storage import write_graph
    old=RunJournal(tmp_path/'old-ui',prefix='gui')
    old.emit('begin','layout','project',{'result':'clocktree.json'})
    state=old.state();state['owner']={'host':socket.gethostname(),'pid':2147483647}
    write_graph(old.status_file,state)
    run=tmp_path/'job';run.mkdir()
    bind_gui(run,old)
    new=RunJournal(tmp_path/'new-ui',prefix='gui')
    new.emit('finished','display','ready')
    bind_gui(run,new)
    assert diagnose_gui(old.directory)['state']=='interrupted'
    assert read_json(run/'gui-log-location.previous.json')['journal_directory']==str(old.directory)


def test_diagnose_command_is_read_only_and_does_not_require_eda(tmp_path):
    journal=RunJournal.start(tmp_path)
    journal.emit('begin','index','indexing')
    old=journal.event_file.read_bytes()
    result=subprocess.run([sys.executable,'-m','clocktree','diagnose','--run',str(tmp_path),'--json'],
                          stdout=subprocess.PIPE,check=True)
    assert json.loads(result.stdout)['stage']=='index'
    assert old==journal.event_file.read_bytes()
