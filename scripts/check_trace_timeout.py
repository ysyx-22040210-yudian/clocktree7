"""Real Verdi deadline acceptance with a blocking call in the scratch worker.

The test hook uses synchronous exec sleep, which does not run Tcl after events.
It also leaves a partial raw suffix. Production timers, process-group signals,
rollback, instance skip decisions, NPI and graph assembly remain unchanged.
"""
import argparse
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
from clocktree.recovery import locate, inspect_recovery
from clocktree.runtime import read_json, alive
from clocktree.session import read_graph
from clocktree.storage import write_graph
from check_crash_recovery import wait_for


HOOK=r'''
proc ct_timeout_test {owner pin direction trace_id} {
    set directory [file dirname $::ct_checkpoint]
    set audit [open [file join $directory test-trace-calls.jsonl] a]
    puts $audit [ct_object instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction] trace_id $trace_id]
    close $audit
    set marker [file join $directory test-blocked.json]
    if {[file exists $marker] || ![regexp {\.m[0-9]+$} $owner] || $pin ne $::ct_test_pin} {return}
    ct_atomic_json $marker [ct_object instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction] trace_id $trace_id pid [pid]]
    if {$::ct_port_trace_timeout_s > 0} {
        puts -nonewline $::ct_stream "UNCOMMITTED_TIMEOUT"; flush $::ct_stream
    }
    exec /bin/sleep $::ct_test_delay
}
rename ct_walk ct_timeout_test_original
proc ct_walk {seed direction owner pin trace_id} {
    ct_timeout_test $owner $pin $direction $trace_id
    return [ct_timeout_test_original $seed $direction $owner $pin $trace_id]
}
'''
TARGET_HOOK=r'''
rename ct_l_walk ct_timeout_test_language_original
proc ct_l_walk {owner pin trace_id} {
    ct_timeout_test $owner $pin driver $trace_id
    return [ct_timeout_test_language_original $owner $pin $trace_id]
}
'''


def fixture(directory,tree='clock',mode='target',seconds=1,fanout=3):
    directory.mkdir(parents=True,exist_ok=True)
    source=directory/'rtl.sv'
    source.write_text('''
module to_mux(input a,b,c,input [1:0] s,output y);assign y=s==0?a:s==1?b:c;endmodule
module to_gate(input i,en,output o);assign o=i&en;endmodule
module to_sync(input i,ck,output o);assign o=i;endmodule
module to_load(input i,output o);assign o=~i;endmodule
module timeout_top(input a,b,c,en,input [1:0] s,output [2:0] o,output [%d:0] bg);
wire [2:0] mid;
to_mux m0(.a(a),.b(b),.c(c),.s(s),.y(mid[0]));
to_mux m1(.a(a),.b(b),.c(c),.s(s),.y(mid[1]));
to_mux m2(.a(a),.b(b),.c(c),.s(s),.y(mid[2]));
%s
%s
endmodule
''' % (fanout-1,'\n'.join(('to_gate g%d(.i(mid[%d]),.en(en),.o(o[%d]));' if tree=='clock' else
                          'to_sync g%d(.i(mid[%d]),.ck(a),.o(o[%d]));')%(i,i,i) for i in range(3)),
                         '\n'.join('to_load l%d(.i(mid[0]),.o(bg[%d]));'%(i,i) for i in range(fanout))))
    filelist=directory/'rtl.f';filelist.write_text(str(source)+'\n')
    cfg=dict(tree_type=tree,extraction_mode=mode,memory_limit_gib=0,port_trace_timeout_s=seconds,
             trace_scope='timeout_top',design=dict(top='timeout_top',filelist=str(filelist)),layers=[])
    for index,module,kind,inputs,output in [(1,'to_mux','mux',['a','b','c'],'y'),
                (2,'to_gate' if tree=='clock' else 'to_sync','gate' if tree=='clock' else 'rsy_sync',['i'],'o')]:
        cfg['layers'].append(dict(index=index,hierarchy='timeout_top',modules=[dict(
            module=module,kind=kind,**{tree+'_inputs':inputs,tree+'_output':output})]))
    return cfg


def instrument(pin,delay,crash=False):
    extraction=importlib.import_module('clocktree.extract');runner=extraction.run_verdi
    def wrapped(command,work,*args,**kw):
        path=Path(work)/'clocktree-extract.tcl'
        hook='set ct_test_pin '+tcl_string(pin)+'\nset ct_test_delay '+str(delay)+'\n'+HOOK
        path.write_text(path.read_text(encoding='utf-8').replace('if {[catch {ct_main} message]}',hook+'\nif {[catch {ct_main} message]}'),encoding='utf-8')
        target=Path(work)/'target_trace.tcl'
        target.write_text(target.read_text(encoding='utf-8')+TARGET_HOOK,encoding='utf-8')
        return runner(command,work,*args,**kw)
    extraction.run_verdi=wrapped
    if crash:
        from clocktree.trace_timeout import TraceTimeouts
        commit=TraceTimeouts.commit
        def pause(self,*a,**kw):
            record=commit(self,*a,**kw)
            write_graph(self.work/'test-decision-saved.json',record)
            while True:time.sleep(.05)
        TraceTimeouts.commit=pause
    return extraction,runner


def verify(directory,expected=True):
    cfg=read_json(directory/'input.json');tree=cfg['tree_type'];mode=cfg['extraction_mode']
    graph=read_graph(directory/('clocktree.json' if tree=='clock' else 'resettree.json'))
    manifest=locate(directory);work=Path(manifest['work'])
    raw=Path(manifest['raw']).read_text(encoding='utf-8')
    assert 'UNCOMMITTED_TIMEOUT' not in raw
    events=list(read_events(manifest['raw']))
    ids=[e['trace_id'] for e in events if e['event']=='trace_begin']
    assert ids==list(range(1,len(ids)+1)),ids
    memory=graph['metadata']['memory_protection']
    assert len(graph['nodes'])==6 and graph['summary']['extraction_finished']
    if not expected:
        assert graph['summary']['connectivity_complete']
        assert memory['attempts']==1 and memory['trace_timeout_count']==0
        return dict(passed=True,timeout_instances=0,attempts=1,summary=graph['summary'])
    blocked=read_json(work/'test-blocked.json');bad=blocked['instance']
    decisions=read_json(work/'trace-timeouts.json')['instances']
    assert list(decisions)==[bad]
    assert memory['trace_timeout_count']==1 and memory['timeout_restart_count']==1
    assert memory['restart_count']==0 and not memory['enabled']
    assert memory['attempts']==2 and not graph['summary']['connectivity_complete']
    node=next(n for n in graph['nodes'] if n['id']==bad)
    assert node['port_trace_status'][blocked['pin']]=='trace_timeout' and not node['terminal'] and not node['root']
    assert node['port_trace_status']['a']=='completed'
    assert all(not n.get('trace_timeout') for n in graph['nodes'] if n['id']!=bad)
    calls=list(read_events(work/'test-trace-calls.jsonl'))
    bad_calls=[e['pin'] for e in calls if e['instance']==bad]
    assert bad_calls==(['a','b'] if blocked['pin']=='b' else ['a','b','c','y']),bad_calls
    later=[e for e in calls if e['trace_id']>blocked['trace_id'] and e['instance']!=bad and '.m' in e['instance']]
    assert later,'Other instances of the same module must continue after timeout'
    assert len([e for e in events if e['event']=='instance_trace_timeout'])==1
    assert len([e for e in events if e['event']=='trace_result' and e['result']['status']=='trace_timeout'])==1
    if blocked['pin']=='b':
        assert node['port_trace_status']['c']=='trace_skipped_timeout'
        assert node['port_trace_status']['y']==('not_requested' if mode=='target' else 'trace_skipped_timeout')
    assert len(graph['edges'])==3,'Independent downstream reverse traces must retain all three connections'
    assert len(list(read_events(directory/'trace-timeouts.jsonl')))==1
    if mode=='target':assert not any(e.get('direction')=='load' for e in events)
    return dict(passed=True,instance=bad,pin=blocked['pin'],direction=blocked['direction'],
                limit_seconds=cfg['port_trace_timeout_s'],elapsed_seconds=decisions[bad]['elapsed_seconds'],
                attempts=memory['attempts'],same_module_continued=True,partial_trace_rolled_back=True,
                summary=graph['summary'])


def run_case(out,name,tree='clock',mode='target',seconds=1,pin='b',delay=10,fanout=3):
    directory=out/name;directory.mkdir(parents=True)
    cfg=fixture(directory/'fixture',tree,mode,seconds,fanout);write_graph(directory/'input.json',cfg)
    extraction,runner=instrument(pin,delay)
    start=time.monotonic()
    try:extraction.extract(directory/'input.json',directory)
    finally:extraction.run_verdi=runner
    report=verify(directory,expected=seconds>0 and pin!='none')
    report.update(case=name,seconds=round(time.monotonic()-start,3))
    write_graph(directory/'acceptance.json',report);print(json.dumps(report),flush=True)
    return report


def crash_case(out):
    directory=out/'decision-crash';directory.mkdir(parents=True)
    write_graph(directory/'input.json',fixture(directory/'fixture',seconds=1.5))
    with (directory/'child.log').open('w') as log:
        child=subprocess.Popen([sys.executable,__file__,'--out',str(directory),'--child'],stdout=log,stderr=subprocess.STDOUT)
        try:
            def paused():
                try:return (Path(locate(directory)['work'])/'test-decision-saved.json').is_file()
                except (RuntimeError,KeyError,OSError):return False
            wait_for(paused,'durable trace timeout decision')
            child.kill();child.wait(timeout=10)
            info=inspect_recovery(directory)
            assert info['resumable'] and info['pending_trace_timeouts'],info
            identity=read_json(directory/'run-status.json')['worker']
            write_graph(directory/'before-resume.json',info)
            with (directory/'resume.log').open('w') as stream:
                code=subprocess.call([sys.executable,'-m','clocktree','resume','--run',str(directory)],stdout=stream,stderr=subprocess.STDOUT)
                assert code==2,'Incomplete connectivity is the documented CLI status 2, got '+str(code)
            assert alive(identity) is False
            report=verify(directory);report.update(case='decision-crash',fresh_process_resumed=True,old_worker_stopped=True)
            write_graph(directory/'acceptance.json',report);print(json.dumps(report),flush=True)
            return report
        finally:
            if child.poll() is None:child.kill();child.wait()
            worker=read_json(directory/'run-status.json',{}).get('worker')
            if alive(worker):os.killpg(worker['pid'],signal.SIGKILL)


def stress(out,count):
    from check_high_fanout import fixture as fan_fixture
    from check_target_mode import run, edges
    cfg=read_json(fan_fixture(out/'fixture',count,mode='target'))
    cases=[];reference=None
    for seconds in (0,10):
        cfg['port_trace_timeout_s']=seconds
        graph,report=run(cfg,out/('limit-'+str(seconds)))
        assert graph['summary']['connectivity_complete']
        assert len(graph['nodes'])==count+3 and len(graph['edges'])==count+2
        assert graph['summary']['trace_timeout_instances']==0
        if reference is None:reference=edges(graph)
        else:assert reference==edges(graph)
        cases.append(dict(port_trace_timeout_s=seconds,selected_loads=count+2,ordinary_loads=count,passed=True,**report))
        print(json.dumps(cases[-1]),flush=True)
    write_graph(out/'acceptance.json',dict(passed=True,cases=cases,native_verdi=True,exact_edges_equal=True))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    p.add_argument('--child',action='store_true');p.add_argument('--no-crash',action='store_true')
    p.add_argument('--crash-only',action='store_true')
    p.add_argument('--stress',action='store_true');p.add_argument('--count',type=int,default=1024)
    args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.child:
        extraction,_=instrument('b',20,crash=True)
        return extraction.extract(out/'input.json',out)
    if args.crash_only:
        write_graph(out/'acceptance.json',dict(passed=True,cases=[crash_case(out)],native_verdi=True))
        return
    if args.stress:return stress(out,args.count)
    cases=[]
    for tree in ('clock','reset'):
        cases.append(run_case(out,tree+'-target-input',tree,seconds=1 if tree=='clock' else 1.5))
        cases.append(run_case(out,tree+'-full-input',tree,mode='full',seconds=1.25))
        cases.append(run_case(out,tree+'-full-output',tree,mode='full',seconds=1.75,pin='y'))
    cases.append(run_case(out,'disabled','clock',seconds=0,delay=.3))
    cases.append(run_case(out,'fanout-512','clock',seconds=10,pin='none',fanout=512))
    if not args.no_crash:cases.append(crash_case(out))
    write_graph(out/'acceptance.json',dict(passed=True,cases=cases,native_verdi=True,blocked_native_surrogate='synchronous exec sleep'))


if __name__=='__main__':main()
