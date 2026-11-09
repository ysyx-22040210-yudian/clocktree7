"""Real-worker fault injection; instrumentation exists only in this QA script."""
import argparse
import copy
import importlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.register_trace import run, RegisterIndex
from clocktree.runtime import alive
from clocktree.storage import write_graph
from clocktree.memory import GIB


def child(request,out,mode):
    module=importlib.import_module('clocktree.register_worker')
    tracer=importlib.import_module('clocktree.register_trace');original_trace=tracer.trace_job
    injected=[False]
    def trace_job(db,job,seed,flow,report):
        original_trace(db,job,seed,flow,report)
        if mode=='crash':os._exit(79)
        if mode=='rotate' and not injected[0]:
            flow.worker.rss=flow.worker.memory_limit*GIB*.95;injected[0]=True
    tracer.trace_job=trace_job
    original_init=module.RegisterWorker.__init__;original_popen=module.subprocess.Popen
    def initialize(self,*args,**kwargs):
        original_init(self,*args,**kwargs);original_report=self.report
        state={'injected':False,'armed':False}
        def report(stage,message,context):
            original_report(stage,message,context)
            if mode in ('threshold','stalled') and stage=='query' and context.get('arguments')==['reg_top.m0.choose_src[4]','driver'] and (mode=='stalled' or not state['injected']):
                state['armed']=True;state['injected']=True
        self.report=report
        if mode in ('threshold','stalled'):
            original_memory=module.process_memory
            def memory(pid):
                value=original_memory(pid)
                if state['armed']:
                    state['armed']=False;value['rss_bytes']=int(self.memory_limit*GIB*1.01)
                return value
            module.process_memory=memory
            original_check=self.check
            def check():
                if state['armed']:self.last_memory=0
                original_check()
            self.check=check
    module.RegisterWorker.__init__=initialize
    def popen(command,*args,**kwargs):
        if isinstance(command,list) and command[-1]=='register_worker.tcl':
            path=Path(kwargs['cwd'])/'register_worker.tcl';text=path.read_text(encoding='utf-8')
            # Guard the actual vendor calls; loader is allowed but NL is not.
            guard='''
proc cr_no_netlist {args} {error "FORBIDDEN_REGISTER_NETLIST"}
foreach cmd [concat [info commands npi_nl_*] [info commands ::npi_L1::npi_nl_*]] {trace add execution $cmd enter cr_no_netlist}
puts "REGISTER_LANGUAGE_GUARD_ACTIVE"
'''
            text=text.replace('proc cr_prop',guard+'\nproc cr_prop',1)
            if mode=='timeout':
                text=text.replace('proc cr_query {name direction} {','proc cr_query {name direction} {\n if {$name eq "reg_top.d0.ratio_cfg\\[0\\]"} {after 10000}')
            path.write_text(text,encoding='utf-8')
        return original_popen(command,*args,**kwargs)
    module.subprocess.Popen=popen
    run(request,out,resume=mode=='resume')


def execute(request,out,mode):
    out.mkdir(parents=True,exist_ok=True)
    with (out/'qa-controller.log').open('a') as log:
        process=subprocess.Popen([sys.executable,__file__,'--request',str(request),'--out',str(out),'--child',mode],stdout=log,stderr=subprocess.STDOUT)
        code=process.wait()
    if mode=='crash':assert code==79,code
    elif mode=='stalled':assert code!=0,code
    else:assert code==0,(mode,code,(out/'qa-controller.log').read_text()[-2400:])
    return json.loads((out/'register-trace.json').read_text()) if (out/'register-trace.json').exists() else None


def rows(path):
    index=RegisterIndex(path);return set(tuple(r[1:]) for r in index.query(limit=1000000)[1])


def verify_no_worker(path):
    saved=path/'worker.json'
    if saved.exists():assert alive(json.loads(saved.read_text()).get('worker',{})) is not True


def suite(request_path,out):
    request=json.loads(request_path.read_text());seeds={ (s['instance'],s['role']):s for s in request['seeds']}
    # First port can time out and skip same-instance sw_done; g0/m0 still run.
    order=[('reg_top.d0','div'),('reg_top.d0','sw_done'),('reg_top.g0','gt'),('reg_top.m0','pll_sel')]
    request['seeds']=[seeds[k] for k in order];request['memory_limit_gib']=4
    base=out/'request.json';write_graph(base,request);reports=[]
    baseline=execute(base,out/'baseline','normal');golden=rows(out/'baseline');assert baseline['summary']['complete']
    reports.append({'case':'baseline-language-only','passed':True,'summary':baseline['summary']})
    for mode in ('rotate','threshold'):
        result=execute(base,out/mode,mode)
        assert rows(out/mode)==golden and result['summary']['verdi_restarts']==1,result['summary']
        events=[json.loads(line) for line in (out/mode/'events.jsonl').read_text().splitlines()]
        assert any(e['stage']==('retry' if mode=='threshold' else 'rotate') for e in events)
        verify_no_worker(out/mode);reports.append({'case':mode,'passed':True,'rows_equal':True,'summary':result['summary']})
    execute(base,out/'crash','crash')
    with sqlite3.connect(str(out/'crash/registers.sqlite')) as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE state='complete'").fetchone()[0]==1
        committed=db.execute('SELECT * FROM results').fetchall()
    saved=json.loads((out/'crash/worker.json').read_text());old_worker=saved['worker']
    result=execute(base,out/'crash','resume')
    assert rows(out/'crash')==golden and alive(old_worker) is not True
    with sqlite3.connect(str(out/'crash/registers.sqlite')) as db:
        assert db.execute('SELECT * FROM results WHERE job=1').fetchall()==committed
    reports.append({'case':'hard-crash-and-resume','passed':True,'committed_rows_unchanged':len(committed),'orphan_reaped':True})
    timed=copy.deepcopy(request);timed['port_trace_timeout_s']=2;timed_path=out/'timeout-request.json';write_graph(timed_path,timed)
    result=execute(timed_path,out/'timeout','timeout');counts=result['summary']['counts']
    assert counts=={'register':5,'skipped':1,'timeout':1},counts
    assert result['summary']['timeout_instances']==1 and result['summary']['verdi_restarts']==1
    verify_no_worker(out/'timeout');reports.append({'case':'native-query-timeout','passed':True,'summary':result['summary']})
    changed=copy.deepcopy(request);changed['dft_zero']=[];changed_path=out/'changed.json';write_graph(changed_path,changed)
    try:run(changed_path,out/'baseline',resume=True)
    except ValueError as error:assert '变化' in str(error)
    else:raise AssertionError('changed configuration reused old checkpoint')
    reports.append({'case':'changed-input-rejected','passed':True})
    execute(base,out/'stalled','stalled')
    events=[json.loads(line) for line in (out/'stalled/events.jsonl').read_text().splitlines()]
    assert sum(e['stage']=='retry' for e in events)==3
    assert events[-1]['stage']=='interrupted' and '3' in events[-1]['message']
    verify_no_worker(out/'stalled')
    with sqlite3.connect(str(out/'stalled/registers.sqlite')) as db:
        assert db.execute("SELECT count(*) FROM jobs WHERE state='complete'").fetchone()[0]==3
    reports.append({'case':'three-no-progress-retries-stop','passed':True,'prior_ports_retained':3})
    for mode in ('baseline','rotate','threshold','crash','timeout','stalled'):
        log=(out/mode/'verdi.log').read_text()
        assert 'REGISTER_LANGUAGE_GUARD_ACTIVE' in log and 'FORBIDDEN_REGISTER_NETLIST' not in log
    write_graph(out/'acceptance.json',{'passed':True,'native_verdi':True,'injection':'QA only; real workers killed/restarted; RSS samples forced, not physical 64 GiB allocation','cases':reports})
    print(json.dumps({'passed':True,'cases':len(reports)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--request',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--child',choices=('normal','rotate','threshold','crash','resume','timeout','stalled'))
    args=parser.parse_args();args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=True)
    if args.child:child(args.request,args.out,args.child)
    else:suite(args.request,args.out)
