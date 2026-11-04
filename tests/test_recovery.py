import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from clocktree.recovery import (RunLease, RecoveryError, config_signature, engine_signature,
                                input_snapshot, inspect_recovery, locate, prepare_recovery,
                                verify_inputs, resume, cause_text)
from clocktree.runtime import RunJournal, read_json, diagnose_gui
from clocktree.storage import write_graph


def fixture(tmp_path):
    source=tmp_path/'rtl.sv'; source.write_text('module top; endmodule')
    filelist=tmp_path/'rtl.f'; filelist.write_text(source.as_posix())
    config={'design':{'top':'top','filelist':str(filelist)},'layers':[
        {'index':1,'hierarchy':'top','modules':[{'module':'gate','clock_inputs':['i'],'clock_output':'o','kind':'gate'}]}]}
    output=tmp_path/'run';journal=RunJournal.start(output)
    manifest=prepare_recovery(journal,output,journal.directory,journal.directory/'raw.jsonl',config,sys.executable)
    journal.emit('failed','modules','test interrupted',returncode=-9,exit_signal='SIGKILL')
    return journal,manifest,config,source


def test_job_lock_is_released_when_owner_is_killed(tmp_path):
    code=('import sys;from clocktree.recovery import RunLease;'
          'lease=RunLease(sys.argv[1]);lease.__enter__();print("locked",flush=True);sys.stdin.read()')
    child=subprocess.Popen([sys.executable,'-c',code,str(tmp_path)],stdout=subprocess.PIPE,stdin=subprocess.PIPE)
    try:
        assert child.stdout.readline().strip()==b'locked'
        with pytest.raises(RecoveryError):
            with RunLease(tmp_path):pass
    finally:child.kill();child.wait(timeout=10)
    with RunLease(tmp_path):pass


def test_changed_source_and_config_cannot_truncate_committed_evidence(tmp_path):
    journal,manifest,config,source=fixture(tmp_path)
    raw=Path(manifest['raw']);raw.write_text('uncommitted preserved for diagnosis')
    assert inspect_recovery(journal.directory)['resumable']
    source.write_text('module top; wire changed; endmodule')
    with pytest.raises(RecoveryError,match='输入已改变'):
        verify_inputs(manifest,sys.executable)
    assert raw.read_text()=='uncommitted preserved for diagnosis'
    manifest['config']['layers'][0]['hierarchy']='top.other'
    write_graph(journal.directory/'recovery.json',manifest)
    with pytest.raises(RecoveryError,match='配置已改变'):locate(journal.directory)


def test_input_guard_covers_nested_lists_headers_and_kdb_inventory(tmp_path):
    include=tmp_path/'includes';include.mkdir()
    header=include/'defs.svh';header.write_text('`define A 1')
    rtl=tmp_path/'rtl.sv';rtl.write_text('`include "includes/defs.svh"\nmodule top; endmodule')
    nested=tmp_path/'sub.f';nested.write_text(rtl.as_posix())
    filelist=tmp_path/'rtl.f';filelist.write_text('-F "{}" +incdir+{}\n+define+MODE=1'.format(nested.as_posix(),include.as_posix()))
    config={'design':{'filelist':str(filelist)}}
    before=input_snapshot(config);assert before['valid'] and before['files']>=4
    header.write_text('`define A 222')
    assert input_snapshot(config)!=before
    kdb=tmp_path/'kdb';kdb.mkdir();(kdb/'design.db').write_bytes(b'123')
    config={'design':{'elab':str(kdb)}};before=input_snapshot(config)
    (kdb/'new.db').write_bytes(b'0')
    assert input_snapshot(config)!=before
    (kdb/'new.db').unlink()
    assert input_snapshot(config)==before


def test_missing_truncated_and_moved_checkpoints_are_not_resumable(tmp_path):
    journal,manifest,config,_=fixture(tmp_path)
    checkpoint=read_json(journal.directory/'checkpoint.json');checkpoint['raw_offset']=100
    write_graph(journal.directory/'checkpoint.json',checkpoint)
    assert not inspect_recovery(journal.directory)['resumable']
    manifest['journal']=str(tmp_path/'moved');write_graph(journal.directory/'recovery.json',manifest)
    with pytest.raises(RecoveryError,match='迁移'):locate(journal.directory)


def test_live_controller_is_observed_and_no_second_worker_is_allowed(tmp_path):
    journal,manifest,_,_=fixture(tmp_path)
    journal.emit('begin','trace','still running')
    info=inspect_recovery(journal.directory)
    assert info['running'] and not info['resumable']
    with pytest.raises(RecoveryError,match='仍在运行'):resume(journal.directory)


def test_closed_gui_vs_idle_gui_crash_and_unknown_reason(tmp_path):
    journal=RunJournal(tmp_path/'ui',prefix='gui')
    journal.emit('finished','display','canvas ready')
    state=journal.state();state['owner']={'host':socket.gethostname(),'pid':2147483647}
    write_graph(journal.status_file,state)
    assert diagnose_gui(journal.directory)['state']=='interrupted'
    journal.emit('finished','display','GUI 正常关闭',clean_shutdown=True)
    assert diagnose_gui(journal.directory)['state']=='finished'
    assert '具体原因未知' in cause_text({'state':'interrupted'})
    assert 'SIGKILL' in cause_text({'last_failure':{'exit_signal':'SIGKILL'}})


def test_safe_startup_does_not_drop_cached_result(tmp_path):
    from clocktree.session import SessionStore
    from test_session import workspace
    store=SessionStore(tmp_path/'runs',tmp_path/'session')
    store.save({'active':'clock','workspaces':{'clock':workspace()}})
    state=store.load(load_results=False)
    cached=state['workspaces']['clock']['deferred_result']
    assert state['workspaces']['clock']['graph'] is None
    store.save(state);store.save(state)
    assert (store.directory/cached).is_file()
    assert store.load()['workspaces']['clock']['graph'] is not None


def test_safe_history_fallback_never_loads_a_potentially_crashing_graph(tmp_path,monkeypatch):
    from clocktree.session import SessionStore
    import clocktree.session as session
    from test_session import workspace
    directory=tmp_path/'runs'/'job';directory.mkdir(parents=True)
    saved=workspace()
    write_graph(directory/'clocktree.json',saved['graph'])
    write_graph(directory/'config.json',saved['config'])
    monkeypatch.setattr(session,'read_graph',lambda *args:(_ for _ in ()).throw(AssertionError('eager graph load')))
    restored=SessionStore(tmp_path/'runs',tmp_path/'session').load(load_results=False)
    assert restored['workspaces']['clock']['graph'] is None
    assert restored['workspaces']['clock']['path']==directory/'clocktree.json'
