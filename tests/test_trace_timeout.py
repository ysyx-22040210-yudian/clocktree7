import copy
import json
from pathlib import Path
import tkinter

import pytest

from clocktree.config import validate_config, write_tcl_config
from clocktree.devices import connection_details, symbol_parts
from clocktree.graph import build_graph
from clocktree.storage import write_graph
from clocktree.trace_timeout import TraceTimeouts
from test_devices import config
from test_memory_resume import setup_run, context, commit, FakeProcess
import clocktree.memory as memory


def status(watch, state='active', trace_id=1, instance='top.bad', pin='a', **kw):
    value=dict(state=state, trace_id=trace_id, instance=instance, pin=pin, direction='driver', **kw)
    write_graph(watch.status, value)


@pytest.mark.parametrize('value', [-1, float('inf'), float('nan'), True, '10'])
def test_invalid_deadline_config(value):
    cfg=config();cfg['port_trace_timeout_s']=value
    with pytest.raises(ValueError, match='port_trace_timeout_s'):validate_config(cfg)


def test_deadline_config_default_and_decimal(tmp_path):
    cfg=config();validate_config(cfg)
    path=tmp_path/'rules.tcl';write_tcl_config(cfg,path,tmp_path/'raw')
    tcl=tkinter.Tcl();tcl.eval(path.read_text(encoding='utf-8'))
    assert float(tcl.getvar('ct_port_trace_timeout_s'))==0
    cfg['port_trace_timeout_s']=1.25;validate_config(cfg)
    write_tcl_config(cfg,path,tmp_path/'raw');tcl.eval(path.read_text(encoding='utf-8'))
    assert float(tcl.getvar('ct_port_trace_timeout_s'))==1.25


def test_only_active_port_is_timed_not_import_or_commit(tmp_path):
    watch=TraceTimeouts(tmp_path,'sig',2)
    assert watch.poll(100) is None
    status(watch)
    assert watch.poll(102) is None
    assert watch.poll(103.9) is None
    status(watch,state='complete',elapsed_ms=1900)
    assert watch.poll(110) is None
    assert watch.ack.read_text().strip()=='1 continue'
    assert watch.poll(1000) is None  # no timeout while committing acknowledged data
    status(watch,trace_id=2,pin='b')
    assert watch.poll(2000) is None
    assert watch.poll(2002)['pin']=='b'


def test_completion_between_polls_still_obeys_limit(tmp_path):
    watch=TraceTimeouts(tmp_path,'sig',1.5)
    status(watch,state='complete',elapsed_ms=1500)
    decision=watch.poll(30)
    assert decision['elapsed_seconds']==1.5 and not watch.ack.exists()


def test_disabled_deadline_never_inspects_status(tmp_path):
    watch=TraceTimeouts(tmp_path,'sig',0)
    watch.status.write_text('broken')
    assert watch.poll(999999999) is None


def test_fractional_millisecond_limit_remains_valid_after_reopen(tmp_path):
    watch=TraceTimeouts(tmp_path,'sig',1.2345)
    status(watch);watch.poll(0)
    decision=watch.poll(1.23451)
    watch.commit(decision,{'stage':'trace','completed_traces':0},1)
    assert TraceTimeouts(tmp_path,'sig',1.2345,resume=True).records['top.bad']['elapsed_seconds']>=1.2345


def test_missing_skip_decisions_refuse_unsafe_recovery(tmp_path):
    with pytest.raises(ValueError,match='Missing saved trace timeout decisions'):
        TraceTimeouts(tmp_path,'sig',1,resume=True)


def test_durable_instance_decision_and_escaped_tcl_context(tmp_path):
    watch=TraceTimeouts(tmp_path,'sig',2)
    owner='top.gen[0].u;[set hacked 1]'
    status(watch,instance=owner,trace_id=7)
    watch.poll(3);decision=watch.poll(5)
    checkpoint={'stage':'trace','completed_traces':6}
    with pytest.raises(ValueError,match='prefix'):
        watch.commit(decision,dict(checkpoint,completed_traces=5),1)
    saved=watch.commit(decision,checkpoint,1)
    restarted=TraceTimeouts(tmp_path,'sig',2,resume=True)
    assert restarted.records=={owner:saved} and not restarted.status.exists()
    cursor=memory._initial_checkpoint('sig')
    memory._context(tmp_path/'context.tcl',cursor,'sig',tmp_path/'raw',tmp_path/'targets',tmp_path/'rotate',
                    tmp_path/'status',tmp_path/'checkpoint',False,restarted)
    tcl=tkinter.Tcl();tcl.eval((tmp_path/'context.tcl').read_text(encoding='utf-8'))
    record=tcl.call('dict','get',tcl.getvar('ct_timeout_instances'),owner)
    assert int(tcl.splitlist(record)[0])==7
    assert json.loads(tcl.splitlist(record)[1])==saved
    assert tcl.eval('info exists hacked')=='0'
    with pytest.raises(ValueError,match='match'):
        TraceTimeouts(tmp_path,'another-run',2,resume=True)


def test_supervisor_timeout_without_memory_monitor_rolls_back_and_restarts(tmp_path, monkeypatch):
    work,output,raw=setup_run(tmp_path);attempts=[];killed=[]
    def launch(command,**kw):
        values=context(kw['env']);attempts.append(values)
        if len(attempts)==1:
            commit(values,raw,queries=1,traces=2,stage='trace')
            with raw.open('a') as stream:stream.write('UNCOMMITTED')
            write_graph(work/'trace-current.json',dict(state='complete',trace_id=3,instance='top.bad',pin='y',
                                                      direction='load',elapsed_ms=2000))
            return FakeProcess(11)
        assert 'UNCOMMITTED' not in raw.read_text()
        assert int(values['ct_resume_completed_traces'])==2
        assert 'top.bad' in str(values['ct_timeout_instances'])
        commit(values,raw,queries=1,traces=8,stage='complete')
        return FakeProcess(12,done=True)
    def stop(p):killed.append(p.pid);p.done=True;return -9
    monkeypatch.setattr(memory,'_stop_worker',stop)
    code,_,report=memory.run_verdi(['verdi'],work,{},raw,output/'verdi.log',output,
        {'memory_limit_gib':0,'port_trace_timeout_s':1},popen=launch,monitored=True,pause=lambda _:None)
    assert code==0 and killed==[11] and len(attempts)==2
    assert report['timeout_restart_count']==1 and report['trace_timeout_count']==1
    assert report['restart_count']==0 and not report['enabled']
    assert 'CT7_PORT_TIMEOUT' in (output/'verdi.log').read_text(encoding='utf-8')


def test_real_tcl_trace_loop_skips_only_remaining_instance_ports():
    code=(Path(__file__).resolve().parents[1]/'npi/extract.tcl').read_text(encoding='utf-8')
    tcl=tkinter.Tcl();tcl.eval(code.split('if {[catch {ct_main}')[0])
    emitted=[];walked=[]
    tcl.createcommand('test_emit',lambda s:emitted.append(json.loads(s)) or '')
    tcl.createcommand('test_walk',lambda *a:walked.append(a) or '')
    tcl.eval('''
        proc ct_emit {s} {test_emit $s}
        proc ct_journal {args} {}
        proc ct_save_checkpoint {args} {}
        proc ct_maybe_rotate {args} {return 0}
        proc ct_l_walk {args} {test_walk {*}$args}
        set count 1; set ct_pin_count 4; set query_count 1
        set ct_extraction_mode target; set ct_port_trace_timeout_s 0
        set ct_pins_to_trace [dict create 1 {{} driver top.bad b} 2 {{} driver top.bad c} 3 {{} driver top.good a}]
        set ct_timeout_instances [dict create top.bad [list 2 {{"instance":"top.bad","pin":"b"}}]]
    ''')
    loop=code.split('    while {$count < $ct_pin_count} {',1)[1].split('    if {$ct_extraction_mode eq "target"} {ct_emit',1)[0]
    tcl.eval('while {$count < $ct_pin_count} {'+loop)
    assert walked==[('top.good','a','4')]
    assert [e['result']['status'] for e in emitted if e['event']=='trace_result']==['trace_timeout','trace_skipped_timeout']
    assert len([e for e in emitted if e['event']=='instance_trace_timeout'])==1


def timeout_graph(target=True):
    cfg=config();cfg['layers']=cfg['layers'][:2];cfg['extraction_mode']='target' if target else 'full'
    cfg['port_trace_timeout_s']=2
    decision=dict(instance='top.bad',pin='b' if target else 'q',direction='driver' if target else 'load',
                  trace_id=2,limit_seconds=2.,elapsed_seconds=2.01)
    events=[dict(event='metadata',extraction_mode=cfg['extraction_mode'])]
    for owner in ('top.bad','top.good'):
        events.append(dict(event='instance',path=owner,module='clock_cell',kind='mux',layer=1,
            inputs=['a','b','c'],output='q',port_connections={p:dict(status='connected',expression='rtl_'+p) for p in ('a','b','c','q')}))
        events.append(dict(event='output_check',instance=owner,pin='q',status='ok'))
    events.append(dict(event='instance_trace_timeout',detail=decision))
    for owner in ('top.bad','top.good'):
        for pin in (['a','b','c'] if target else ['a','b','c','q']):
            direction='load' if pin=='q' else 'driver'
            if owner=='top.bad' and pin==decision['pin']: result=dict(status='trace_timeout',path=[],timeout=decision)
            elif target and owner=='top.bad' and pin=='c':result=dict(status='trace_skipped_timeout',path=[],timeout=decision)
            elif owner=='top.good' and pin=='a':result=dict(status='target',instance='top.bad',pin='q',path=[])
            else:result=dict(status='output_boundary' if pin=='q' else 'input_boundary',endpoint='top.'+pin)
            events.append(dict(event='trace',instance=owner,pin=pin,direction=direction,results=[result]))
    events.append(dict(event='complete'))
    return build_graph(events,cfg)


@pytest.mark.parametrize('target', [True,False])
def test_timeout_graph_keeps_instances_edges_and_marks_incomplete(target):
    graph=timeout_graph(target);bad,good=graph['nodes']
    assert not graph['summary']['connectivity_complete'] and graph['summary']['extraction_finished']
    assert graph['summary']['trace_timeout_instances']==1
    assert bad['trace_timeout'] and not bad['terminal'] and not bad['root']
    assert 'trace_timeout' not in good
    assert graph['edges'][0]['source']=='top.bad' and graph['edges'][0]['target']=='top.good'
    assert bad['port_trace_status']['a']=='completed'
    if target:
        assert bad['port_trace_status']['c']=='trace_skipped_timeout'
        assert bad['port_trace_status']['q']=='not_requested'
        assert '未执行输出 load trace' in connection_details(bad,['q'])
    else:assert bad['port_trace_status']['q']=='trace_timeout'
    assert all('missing_trace'!=d['code'] for d in graph['diagnostics'])
    texts=[p['text'] for p in symbol_parts(bad,382,184) if p['op']=='text']
    assert 'trace 超时' in texts and 'rtl_q' in texts and any(' · trace 超时' in t for t in texts)
    assert 'rtl_a' in connection_details(bad,['a']) and '证据保留' in connection_details(bad,['a'])
