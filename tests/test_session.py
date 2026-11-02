import copy
import json
import os
from pathlib import Path
import threading
import uuid

import pytest

from clocktree.config import validate_config
from clocktree.profiles import empty_config
from clocktree.session import SessionStore, SessionWriter, default_session_dir, snapshot_graph


ROOT=Path(__file__).resolve().parents[1]


def graph(mode='clock'):
    path=ROOT/('examples/reset_tree/resettree.json' if mode=='reset' else 'examples/flexible_layers/clocktree.json')
    return json.loads(path.read_text(encoding='utf-8'))


def workspace(mode='clock'):
    data=graph(mode)
    return {'config':copy.deepcopy(data['config']),'graph':snapshot_graph(data),'path':None,
            'dirty':False,'active_layer':2,'graph_token':uuid.uuid4().hex}


def test_incomplete_drafts_are_allowed_only_for_session_validation():
    for mode in ('clock','reset'):
        draft=empty_config(mode)
        validate_config(draft,allow_incomplete=True)
        with pytest.raises(ValueError):validate_config(draft)
    invalid=workspace('reset')['config']
    invalid['layers'][1]['modules'][0]['kind']='gate'
    with pytest.raises(ValueError):validate_config(invalid,allow_incomplete=True)


def test_both_modes_draft_dirty_flag_and_selected_layer_round_trip(tmp_path):
    store=SessionStore(tmp_path/'runs',tmp_path/'session')
    clock,reset=workspace(),workspace('reset')
    reset['config']['design']['top']='unfinished_reset_draft'
    reset['config']['layers'].append({'index':3,'hierarchy':'top.new_domain','modules':[]})
    reset.update(dirty=True,active_layer=3)
    store.save({'active':'reset','workspaces':{'clock':clock,'reset':reset}})
    restored=store.load()
    assert restored['active']=='reset' and restored['origin']=='session'
    assert set(restored['workspaces'])=={'clock','reset'}
    saved=restored['workspaces']['reset']
    assert saved['config']['design']['top']=='unfinished_reset_draft' and saved['active_layer']==3
    assert saved['graph']['config']['design']['top']=='reset_top' and saved['dirty']
    assert saved['graph']['edges']==reset['graph']['edges']


def test_snapshot_protects_type_edits_during_background_write():
    original=graph()
    original['presentation']={'kind_overrides':[]}
    frozen=snapshot_graph(original)
    original['nodes'][0]['kind']='div'
    original['config']['layers'][0]['modules'][0]['kind']='div'
    original['presentation']['kind_overrides'].append({'module':'changed'})
    assert frozen['nodes'][0]['kind']=='mux'
    assert frozen['config']['layers'][0]['modules'][0]['kind']=='mux'
    assert not frozen['presentation']['kind_overrides']


def test_config_autosave_does_not_rewrite_unchanged_large_result(tmp_path):
    store=SessionStore(tmp_path/'runs',tmp_path/'session')
    item=workspace()
    state={'active':'clock','workspaces':{'clock':item}}
    store.save(state)
    manifest=json.loads(store.manifest.read_text())
    snapshot=store.directory/manifest['workspaces']['clock']['result']
    before=(snapshot.stat().st_mtime_ns,snapshot.read_bytes())
    item['config']['design']['top']='changed_draft'
    item['dirty']=True
    store.save(state)
    assert (snapshot.stat().st_mtime_ns,snapshot.read_bytes())==before
    assert store.load()['workspaces']['clock']['config']['design']['top']=='changed_draft'


def test_deleted_source_result_recovers_from_snapshot_then_preserves_config_if_both_missing(tmp_path):
    store=SessionStore(tmp_path/'runs',tmp_path/'session')
    item=workspace('reset')
    original=tmp_path/'original.json'
    original.write_text(json.dumps(item['graph']))
    item['path']=original
    store.save({'active':'reset','workspaces':{'reset':item}})
    original.unlink()
    restored=store.load()
    assert len(restored['workspaces']['reset']['graph']['nodes'])==12 and not restored['warnings']
    cached=json.loads(store.manifest.read_text())['workspaces']['reset']['result']
    (store.directory/cached).unlink()
    restored=store.load()
    assert restored['workspaces']['reset']['graph'] is None and restored['warnings']
    assert restored['workspaces']['reset']['config']==item['config']


def test_bad_primary_manifest_uses_previous_atomic_record(tmp_path):
    store=SessionStore(tmp_path/'runs',tmp_path/'session')
    item=workspace()
    state={'active':'clock','workspaces':{'clock':item}}
    store.save(state)
    item['config']['design']['top']='new_draft'
    store.save(state)
    for damaged in ('{broken json',json.dumps({'schema_version':1,'active':'clock','workspaces':{'clock':{'config':[]}}})):
        store.manifest.write_text(damaged)
        restored=store.load()
        assert restored['warnings'] and restored['workspaces']['clock']['config']['design']['top']=='flex_top'
        assert len(restored['workspaces']['clock']['graph']['nodes'])==13


def test_failed_atomic_commit_keeps_previous_record_readable(tmp_path,monkeypatch):
    store=SessionStore(tmp_path/'runs',tmp_path/'session')
    item=workspace()
    state={'active':'clock','workspaces':{'clock':item}}
    store.save(state)
    original=store._atomic_json
    def fail_manifest(path,value):
        if path==store.manifest:raise OSError('disk full')
        original(path,value)
    monkeypatch.setattr(store,'_atomic_json',fail_manifest)
    item['config']['design']['top']='not_committed'
    with pytest.raises(OSError,match='disk full'):store.save(state)
    assert store.load()['workspaces']['clock']['config']['design']['top']=='flex_top'


def test_first_upgrade_uses_readable_recent_history_for_each_mode(tmp_path):
    runs=tmp_path/'runs'
    for name,mode,mtime in [('old','clock',100),('latest','reset',200)]:
        directory=runs/name
        directory.mkdir(parents=True)
        file=directory/('clocktree.json' if mode=='clock' else 'resettree.json')
        file.write_text(json.dumps(graph(mode)))
        os.utime(str(file),(mtime,mtime))
    bad=runs/'broken'
    bad.mkdir()
    (bad/'clocktree.json').write_text('invalid')
    restored=SessionStore(runs,tmp_path/'cache').load()
    assert restored['origin']=='history' and restored['active']=='reset'
    assert len(restored['workspaces']['clock']['graph']['nodes'])==13
    assert len(restored['workspaces']['reset']['graph']['nodes'])==12


def test_session_cache_is_scoped_to_run_directory(tmp_path):
    assert default_session_dir(tmp_path/'project1')!=default_session_dir(tmp_path/'project2')


def test_writer_coalesces_pending_edits_and_finishes_on_close(tmp_path):
    entered,released=threading.Event(),threading.Event()
    writes,errors=[],[]
    main=threading.current_thread()
    class Store:
        def save(self,state):
            assert threading.current_thread() is not main
            writes.append(state['value'])
            if len(writes)==1:
                entered.set()
                assert released.wait(5)
    writer=SessionWriter(Store(),errors.append)
    try:
        writer.submit({'value':1})
        assert entered.wait(5)
        writer.submit({'value':2})
        writer.submit({'value':3})
    finally:
        released.set()
        writer.close()
    assert writes==[1,3] and writer.idle and not errors


def test_old_snapshot_cleanup_does_not_touch_unrelated_files(tmp_path):
    store=SessionStore(tmp_path/'runs',tmp_path/'session')
    item=workspace()
    for _ in range(3):
        item['graph_token']=uuid.uuid4().hex
        store.save({'active':'clock','workspaces':{'clock':item}})
        (store.directory/'notes.json').write_text('user note')
    assert len(list(store.directory.glob('clock-*.json')))==2
    assert (store.directory/'notes.json').read_text()=='user note'
