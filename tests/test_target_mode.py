import copy
from pathlib import Path

import pytest
from clocktree.config import validate_config, write_tcl_config
from clocktree.graph import build_graph


def fixture():
    config={'design':{'top':'top','filelist':'rtl.f'},'layers':[
        {'index':1,'hierarchy':'top','modules':[{'module':'cell','clock_inputs':['i'],'clock_output':'o'}]}]}
    events=[{'event':'metadata','extraction_mode':'target'}]
    for name in ('a','b','c'):
        events += [{'event':'instance','path':'top.'+name,'module':'cell','layer':1,'inputs':['i'],'output':'o'},
                   {'event':'output_check','instance':'top.'+name,'pin':'o','status':'ok'},
                   {'event':'trace','instance':'top.'+name,'pin':'i','direction':'driver','results':[
                       {'status':'input_boundary','endpoint':'top.clk','path':[]} if name=='a' else
                       {'status':'target','instance':'top.a','pin':'o','path':[{'type':'npiPort','name':'top.a.o'}]}]}]
    events.append({'event':'complete'})
    return config,events


def test_target_fanout_requires_inputs_and_output_validation_not_fake_loads():
    cfg,events=fixture();graph=build_graph(iter(events),cfg)
    assert graph['summary']['connectivity_complete']
    assert graph['summary']['expected_pin_traces']==3
    assert graph['summary']['terminals']==2
    assert not graph['summary']['physical_loads_checked']
    assert all(set(edge['evidence'])=={'driver'} for edge in graph['edges'])


@pytest.mark.parametrize('failure',['missing_input','missing_output_check','invalid_output','unsupported_expression','fake_load'])
def test_incomplete_target_coverage_never_claims_proven_terminals(failure):
    cfg,events=fixture()
    if failure=='missing_input':events=[e for e in events if not(e['event']=='trace' and e['instance']=='top.c')]
    if failure=='missing_output_check':events=[e for e in events if not(e['event']=='output_check' and e['instance']=='top.a')]
    if failure=='invalid_output':next(e for e in events if e['event']=='output_check')['status']='missing_pin'
    if failure=='unsupported_expression':next(e for e in events if e['event']=='trace')['results']=[{'status':'unsupported_expression','path':[]}]
    if failure=='fake_load':events.insert(-1,{'event':'trace','instance':'top.a','pin':'o','direction':'load','results':[{'status':'no_load','path':[]}]})
    graph=build_graph(events,cfg)
    assert not graph['summary']['connectivity_complete']
    assert not graph['summary']['terminals']
    if failure=='unsupported_expression':assert not next(n for n in graph['nodes'] if n['id']=='top.a')['root']


def test_old_evidence_keeps_dual_direction_completeness():
    cfg,events=fixture();events[0].pop('extraction_mode')
    graph=build_graph(events,cfg)
    assert not graph['summary']['connectivity_complete']
    assert any(d['code']=='trace_mismatch' for d in graph['diagnostics'])
    assert graph['summary']['extraction_mode']=='full'


def test_mode_validation_default_and_tcl(tmp_path):
    cfg,_=fixture();path=tmp_path/'rules.tcl'
    validate_config(cfg);write_tcl_config(cfg,path,'raw.jsonl')
    assert 'set ct_extraction_mode "target"' in path.read_text()
    cfg['extraction_mode']='full';write_tcl_config(cfg,path,'raw.jsonl')
    assert 'set ct_extraction_mode "full"' in path.read_text()
    cfg['extraction_mode']='invalid'
    with pytest.raises(ValueError,match='extraction_mode'):validate_config(cfg)


def test_live_monitor_waits_through_completion_snapshot_race(monkeypatch,tmp_path):
    from types import SimpleNamespace
    import queue
    import clocktree.gui as gui
    # A status replacement between inspect_recovery's two reads can leave
    # running unset while its diagnosis still identifies a live controller.
    states=iter([{'diagnosis':{'state':'running','owner_alive':True}},
                 {'diagnosis':{'state':'finished','owner_alive':False}}])
    monkeypatch.setattr(gui,'inspect_recovery',lambda directory:next(states))
    monkeypatch.setattr(gui.time,'sleep',lambda seconds:None)
    monkeypatch.setattr(gui.threading,'Thread',lambda target,daemon:SimpleNamespace(start=target))
    graph={'summary':{'connectivity_complete':True}}
    monkeypatch.setattr(gui,'read_graph',lambda path:graph)
    (tmp_path/'clocktree.json').write_text('{}')
    app=SimpleNamespace(busy=False,_destroying=False,events=queue.Queue(),
                        _set_busy=lambda state:None,show_log=lambda show:None,set_status=lambda text:None)
    gui.ClockTreeApp.monitor_extraction(app,{'directory':str(tmp_path),'manifest':{'config':{'tree_type':'clock'}}})
    event=app.events.get_nowait()
    assert event[0]=='loaded' and event[1] is graph
