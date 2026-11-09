import io
import json
import pytest
from clocktree import register_progress as module


def test_locked_snapshot_preserves_every_event_and_retries(tmp_path,monkeypatch,capsys):
    calls=[];ticks=[0.0];actual=module.write_graph
    def write(path,value):
        calls.append(value['stage'])
        if len(calls)<=2:raise PermissionError('HGFS replacement denied')
        actual(path,value)
    monkeypatch.setattr(module,'write_graph',write)
    monkeypatch.setattr(module.time,'monotonic',lambda:ticks[0])
    with (tmp_path/'events.jsonl').open('w',encoding='utf-8') as events:
        report=module.progress_reporter(tmp_path,events)
        report('import','start',{})
        for bit in range(100):report('query','trace bit',{'bit':bit})
        ticks[0]=.3;report('query','next',{'bit':100})
        report('checkpoint','committed',{});report('finished','done',{'complete':True})
    assert calls==['import','query','checkpoint','finished']
    assert len((tmp_path/'events.jsonl').read_text().splitlines())==104
    assert module.read_progress(tmp_path)['stage']=='finished'
    assert capsys.readouterr().out.count('temporarily unavailable')==1


def test_log_write_failure_is_not_silently_ignored(tmp_path):
    class Broken:
        def write(self,value):raise OSError('disk full')
    with pytest.raises(OSError,match='disk full'):
        module.progress_reporter(tmp_path,Broken())('checkpoint','cannot save',{})


def test_final_stage_survives_a_permanently_locked_snapshot(tmp_path,monkeypatch):
    def locked(*args):raise PermissionError('locked')
    monkeypatch.setattr(module,'write_graph',locked)
    (tmp_path/'status.json').write_text(json.dumps({'stage':'query','message':'stale'}))
    with (tmp_path/'events.jsonl').open('w') as events:
        module.progress_reporter(tmp_path,events)('finished','done',{'complete':True})
    assert module.read_progress(tmp_path)['stage']=='finished'
    with (tmp_path/'events.jsonl').open('a') as events:events.write('{"partial":')
    assert module.read_progress(tmp_path)['stage']=='finished'


def test_old_snapshot_without_log_and_malformed_files(tmp_path):
    assert module.read_progress(tmp_path)=={}
    (tmp_path/'status.json').write_text('{')
    assert module.read_progress(tmp_path)=={}
    (tmp_path/'status.json').write_text(json.dumps({'stage':'interrupted','message':'old task'}))
    assert module.read_progress(tmp_path)['stage']=='interrupted'
