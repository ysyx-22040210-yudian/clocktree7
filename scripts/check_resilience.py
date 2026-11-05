"""Cross-fault native acceptance: repeated timeouts, real RSS, crash boundaries.

Hooks affect only scratch workers or the test controller. Production process
ownership, deadlines, durable prefixes, recovery and NPI remain in use.
"""
import argparse
import errno
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.config import tcl_string
from clocktree.graph import read_events
from clocktree.recovery import inspect_recovery,locate
from clocktree.runtime import alive,read_json
from clocktree.session import read_graph
from clocktree.storage import write_graph
from check_trace_timeout import fixture
from check_crash_recovery import wait_for
from check_real_memory_threshold import PRESSURE

HOOK=r'''
proc ct_fault_hook {owner pin direction trace_id} {
    set work [file dirname $::ct_checkpoint]
    set index 0
    foreach action $::ct_fault_actions {
        incr index
        lassign $action kind expected port
        if {$owner ne $expected || $pin ne $port} {continue}
        set once [file join $work fault-once-$index]
        if {[file exists $once]} {continue}
        ct_atomic_json $once [ct_object instance [ct_js $owner] trace_id $trace_id action [ct_js $kind]]
        if {$kind in {soft hard}} {
            ct_real_pressure fault_$kind pressure-$index [expr {$kind eq "hard"}] [expr {$kind eq "soft"}]
        } else {
            puts -nonewline $::ct_stream "UNCOMMITTED_FAULT";flush $::ct_stream
            if {$kind eq "pause"} {ct_atomic_json [file join $work fault-pause.json] [ct_object trace_id $trace_id]}
            exec /bin/sleep 30
        }
    }
}
rename ct_walk ct_fault_original_walk
proc ct_walk {seed direction owner pin trace_id} {
    ct_fault_hook $owner $pin $direction $trace_id
    return [ct_fault_original_walk $seed $direction $owner $pin $trace_id]
}
rename ct_save_checkpoint ct_fault_original_checkpoint
proc ct_save_checkpoint {stage queries traces} {
    set work [file dirname $::ct_checkpoint]
    set marker [file join $work fault-pause.json]
    if {$::ct_fault_stage eq "after-ack" && $stage eq "trace" && $traces == 2 && ![file exists $marker]} {
        ct_atomic_json $marker [ct_object trace_id $traces]
        exec /bin/sleep 30
    }
    ct_fault_original_checkpoint $stage $queries $traces
    if {$::ct_fault_stage eq "skip-commit" && $stage eq "trace" && $traces == 2 &&
            [dict exists $::ct_timeout_instances timeout_top.m0] && ![file exists $marker]} {
        ct_atomic_json $marker [ct_object trace_id $traces]
        exec /bin/sleep 30
    }
}
'''
LANG_HOOK=r'''
rename ct_l_walk ct_fault_original_language
proc ct_l_walk {owner pin trace_id} {
    ct_fault_hook $owner $pin driver $trace_id
    return [ct_fault_original_language $owner $pin $trace_id]
}
'''


def instrument(actions,stage=''):
    extraction=importlib.import_module('clocktree.extract');original=extraction.run_verdi
    def run(command,work,environment,raw,log,output,cfg,**kwargs):
        work=Path(work)
        sets='set ct_fault_actions [list '+ ' '.join('[list '+ ' '.join(tcl_string(v) for v in action)+']' for action in actions)+']\n'
        sets+='set ct_fault_stage '+tcl_string(stage)+'\nset ct_test_limit '+str(cfg['memory_limit_gib'])+'\n'
        path=work/'clocktree-extract.tcl'
        path.write_text(path.read_text(encoding='utf-8').replace('if {[catch {ct_main} message]}',sets+PRESSURE+HOOK+'\nif {[catch {ct_main} message]}'),encoding='utf-8')
        path=work/'target_trace.tcl';path.write_text(path.read_text(encoding='utf-8')+LANG_HOOK,encoding='utf-8')
        environment=dict(environment,CT7_TEST_MEMORY_REPORT=str(Path(output)/'memory.json'))
        if stage=='before-ack':
            from clocktree.trace_channel import TraceChannel
            reply=TraceChannel.reply
            def held(channel,trace,action):
                if trace==2 and action=='continue' and not (work/'fault-pause.json').exists():
                    write_graph(work/'fault-pause.json',dict(trace_id=trace))
                    while True:time.sleep(.02)
                return reply(channel,trace,action)
            TraceChannel.reply=held
        result=original(command,work,environment,raw,log,output,cfg,**kwargs)
        if stage=='complete':
            write_graph(work/'fault-pause.json',dict(complete=True))
            while True:time.sleep(.02)
        return result
    extraction.run_verdi=run
    return extraction,original


def result(directory):
    config=read_json(directory/'input.json')
    return read_graph(directory/('clocktree.json' if config['tree_type']=='clock' else 'resettree.json'))


def verify(directory,skipped=(),memory_restarts=0):
    graph=result(directory);manifest=locate(directory);work=Path(manifest['work'])
    events=list(read_events(manifest['raw']))
    text=Path(manifest['raw']).read_text(encoding='utf-8')
    assert 'UNCOMMITTED' not in text
    begin=[e['trace_id'] for e in events if e['event']=='trace_begin']
    end=[e['trace_id'] for e in events if e['event']=='trace_end']
    assert begin==end==list(range(1,len(begin)+1)),(begin,end)
    assert len(graph['nodes'])==6 and len(graph['edges'])==3
    assert len({n['id'] for n in graph['nodes']})==6
    actual={n['id'] for n in graph['nodes'] if n.get('trace_timeout')}
    assert actual==set(skipped),(actual,skipped)
    assert graph['summary']['connectivity_complete']==(not skipped)
    assert graph['summary']['trace_timeout_instances']==len(skipped)
    assert sum(e['event']=='instance_trace_timeout' for e in events)==len(skipped)
    assert sum(e['event']=='complete' for e in events)==1
    for node in graph['nodes']:
        if node['id'] in skipped:
            assert not node['root'] and not node['terminal']
            assert node['port_trace_status']['a']=='completed'
            assert node['port_trace_status']['b']=='trace_timeout'
            assert node['port_trace_status']['c']=='trace_skipped_timeout'
    report=graph['metadata']['memory_protection']
    assert report['restart_count']==memory_restarts,report
    assert report['trace_timeout_count']==len(skipped)
    # All three downstream traces must still run, even when every MUX timed out.
    assert all(any(e.get('instance')=='timeout_top.g'+str(i) and e['event']=='trace_begin' for e in events) for i in range(3))
    workers=[read_json(p) for p in work.glob('attempt-*/worker-identity.json')]
    assert workers and all(alive(worker) is False for worker in workers),workers
    return dict(passed=True,instances=6,edges=3,timeout_instances=sorted(actual),
                attempts=report['attempts'],memory_restarts=memory_restarts,
                timeout_restarts=report['timeout_restart_count'],transport=report.get('trace_monitor_transport'),
                peak_rss_gib=report['peak_rss_gib'],exact_trace_sequence=True,no_remaining_workers=True)


def run_case(out,name,actions=(),tree='clock',mode='target',seconds=3,transport='socket',directory=None):
    directory=directory or out/name;directory.mkdir(parents=True,exist_ok=True)
    cfg=fixture(directory/'fixture',tree,mode,seconds)
    mem=sum(a[0] in ('soft','hard') for a in actions)
    cfg['memory_limit_gib']=.5 if mem else 0
    write_graph(directory/'input.json',cfg)
    extraction,original=instrument(actions)
    old=os.environ.get('CLOCKTREE_TRACE_TRANSPORT');os.environ['CLOCKTREE_TRACE_TRANSPORT']=transport
    start=time.monotonic()
    try:extraction.extract(directory/'input.json',directory)
    finally:
        extraction.run_verdi=original
        if old is None:os.environ.pop('CLOCKTREE_TRACE_TRANSPORT',None)
        else:os.environ['CLOCKTREE_TRACE_TRANSPORT']=old
    report=verify(directory,[a[1] for a in actions if a[0]=='timeout'],mem)
    assert report['attempts']==1+len(actions)
    assert report['transport']==transport if seconds else report['transport']=='disabled'
    if mem:
        pressures=list(read_events(Path(locate(directory)['work'])/'pressure-allocations.jsonl'))
        assert len(pressures)==mem
        memory=read_json(directory/'memory.json')
        for action,event in zip((a[0] for a in actions if a[0] in ('soft','hard')),memory['restarts']):
            assert event['reason']==('checkpoint_rotation' if action=='soft' else 'hard_limit')
            assert event['rss_gib']>=.5*(.9 if action=='soft' else 1)
    report.update(case=name,seconds=round(time.monotonic()-start,3))
    write_graph(directory/(name+'-acceptance.json'),report);print(json.dumps(report),flush=True)
    return report


def child(directory,stage):
    actions=[('timeout','timeout_top.m0','b')] if stage=='skip-commit' else ([('pause','timeout_top.m0','b')] if stage in ('cancel','worker-kill') else [])
    extraction,_=instrument(actions,stage)
    extraction.extract(directory/'input.json',directory)


def crash_case(out,stage,corrupt=False,tree='clock'):
    name=stage+('-corrupt-cursor' if corrupt else '')+'-'+tree
    directory=out/name;directory.mkdir()
    cfg=fixture(directory/'fixture',tree,seconds=1 if stage=='skip-commit' else (60 if stage in ('cancel','worker-kill') else .25))
    write_graph(directory/'input.json',cfg)
    with (directory/'child.log').open('w') as log:
        process=subprocess.Popen([sys.executable,__file__,'--out',str(directory),'--child',stage],stdout=log,stderr=subprocess.STDOUT)
        try:
            def paused():
                try:
                    work=Path(locate(directory)['work'])
                    return work if (work/'fault-pause.json').exists() else None
                except (RuntimeError,KeyError,OSError):return None
            work=wait_for(paused,'fault boundary '+stage)
            state=read_json(directory/'run-status.json');worker=state.get('worker')
            if stage in ('before-ack','after-ack'):
                time.sleep(.6)  # deliberately longer than .25 s: waiting to commit is not native trace time
                assert read_json(directory/'memory.json')['trace_timeout_count']==0
            if stage=='cancel':process.terminate();process.wait(timeout=15)
            elif stage=='worker-kill':os.killpg(worker['pid'],signal.SIGKILL);process.wait(timeout=15)
            else:process.kill();process.wait(timeout=10)
            if corrupt:(work/'checkpoint.json').write_text('{partial checkpoint')
            # Cache damage must not be interpreted as a saved valid step.
            with (work/'language-steps.cache').open('a') as cache:cache.write('\n{unfinished cache entry')
            info=inspect_recovery(directory);assert info['resumable'],info
            expected=2 if stage=='skip-commit' and not corrupt else (12 if stage=='complete' else 1)
            assert info['checkpoint']['completed_traces']==expected,info['checkpoint']
            write_graph(directory/'before-resume.json',info)
            with (directory/'resume.log').open('w') as stream:
                rc=subprocess.call([sys.executable,'-m','clocktree','resume','--run',str(directory)],stdout=stream,stderr=subprocess.STDOUT)
            assert rc==(2 if stage=='skip-commit' else 0),rc
            assert alive(worker) is False
            report=verify(directory,['timeout_top.m0'] if stage=='skip-commit' else [])
            if stage=='complete':assert report['attempts']==1
            report.update(case=name,reopened=True,orphan_stopped=True,committed_traces=expected)
            write_graph(directory/'acceptance.json',report);print(json.dumps(report),flush=True)
            return report
        finally:
            if process.poll() is None:process.kill();process.wait()
            worker=read_json(directory/'run-status.json',{}).get('worker')
            if alive(worker):os.killpg(worker['pid'],signal.SIGKILL)


def storage_case(out):
    """A failed durable skip write must fail the run, then safely retry on reopen."""
    import clocktree.trace_timeout as module
    directory=out/'decision-write-failure';directory.mkdir()
    cfg=fixture(directory/'fixture',seconds=.7)
    write_graph(directory/'input.json',cfg)
    extraction,original_run=instrument([('timeout','timeout_top.m0','b')])
    original_write=module.write_graph
    def fail_decision(path,data):
        if Path(path).name=='trace-timeouts.json' and data.get('instances'):
            raise OSError(errno.ENOSPC,'injected durable decision write failure')
        return original_write(path,data)
    module.write_graph=fail_decision
    try:
        try:extraction.extract(directory/'input.json',directory)
        except OSError as error:assert error.errno==errno.ENOSPC
        else:raise AssertionError('Write failure was silently accepted')
    finally:
        module.write_graph=original_write;extraction.run_verdi=original_run
    manifest=locate(directory);work=Path(manifest['work'])
    assert read_json(work/'trace-timeouts.json')['instances']=={}
    state=read_json(directory/'run-status.json')
    assert alive(state.get('worker')) is False
    assert not (directory/'clocktree.json').exists()
    info=inspect_recovery(directory);assert info['resumable'],info
    assert info['checkpoint']['completed_traces']==1
    write_graph(directory/'before-resume.json',info)
    with (directory/'resume.log').open('w') as stream:
        rc=subprocess.call([sys.executable,'-m','clocktree','resume','--run',str(directory)],stdout=stream,stderr=subprocess.STDOUT)
    assert rc==0,rc
    report=verify(directory)
    report.update(case='decision-write-failure',reopened=True,failed_write_not_committed=True)
    write_graph(directory/'acceptance.json',report);print(json.dumps(report),flush=True)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    p.add_argument('--child',choices=['before-ack','after-ack','skip-commit','complete','cancel','worker-kill'])
    p.add_argument('--only',choices=['mixed','crash','rerun','file','storage'])
    args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.child:return child(out,args.child)
    cases=[]
    def add(case):
        cases.append(case);write_graph(out/'acceptance.json',dict(passed=False,complete=False,cases=cases))
    if args.only in (None,'mixed'):
        for mode in ('target','full'):
            add(run_case(out,'three-timeouts-'+mode,[('timeout','timeout_top.m'+str(i),'b') for i in range(3)],mode=mode,seconds=.7))
        for kind,order,tree in [('hard','first','clock'),('hard','last','reset'),('soft','first','reset'),('soft','last','clock')]:
            actions=[(kind,'timeout_top.m0','b'),('timeout','timeout_top.m1','b')] if order=='first' else [('timeout','timeout_top.m0','b'),(kind,'timeout_top.m1','b')]
            add(run_case(out,kind+'-'+order+'-'+tree,actions,tree=tree))
    if args.only in (None,'crash'):
        for stage in ('before-ack','after-ack','skip-commit','complete','cancel','worker-kill'):
            add(crash_case(out,stage,tree='reset' if stage in ('after-ack','cancel') else 'clock'))
        add(crash_case(out,'skip-commit',corrupt=True))
    if args.only in (None,'rerun'):
        for i,tree in enumerate(('clock','reset','clock')):
            add(run_case(out,'reuse-'+str(i),[('timeout','timeout_top.m0','b')] if i==0 else [],tree=tree,
                         seconds=.7 if i==0 else 0,directory=out/'same-output'))
        assert len(list((out/'same-output/.clocktree-history').iterdir()))>=2
    if args.only in (None,'file'):
        add(run_case(out,'file-fallback',[('timeout','timeout_top.m0','b')],seconds=.7,transport='file'))
    if args.only in (None,'storage'):
        add(storage_case(out))
    write_graph(out/'acceptance.json',dict(passed=True,complete=True,cases=cases))


if __name__=='__main__':main()
