"""Real VM acceptance: SIGKILL, a new controller/GUI, then committed-prefix recovery.

Only test wrappers pause work/write uncommitted tails. Product recovery,
checkpoint validation, worker ownership, Verdi and NPI calls are unchanged.
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
from clocktree.recovery import inspect_recovery, locate
from clocktree.runtime import read_json, diagnose, alive, process_identity
from clocktree.storage import write_graph
from clocktree.session import read_graph
from clocktree.graph import read_events
from check_memory_resume import same_graph


RTL='''
module rec_mux(input a,b,s,output y); assign y=s?b:a; endmodule
module rec_gate(input i,en,output o); assign o=i&en; endmodule
module rec_div(input i,output o); assign o=i; endmodule
module rec_rmux(input a,b,s,output y); assign y=s?b:a; endmodule
module rec_sync(input rn,ck,output ro); assign ro=rn; endmodule
module rec_top(input a,b,en,s,rn,output [39:0] clk,output d,output [19:0] rst);
 wire m,r;
 rec_mux u_mux(.a(a),.b(b),.s(s),.y(m));
 rec_gate u_gate[39:0](.i(m),.en(en),.o(clk));
 rec_div u_div(.i(clk[0]),.o(d));
 rec_rmux u_rmux(.a(rn),.b(rn),.s(s),.y(r));
 rec_sync u_sync[19:0](.rn(r),.ck(a),.ro(rst));
endmodule
'''

HOOK=r'''
rename ct_journal ct_recovery_test_original
proc ct_journal {kind stage {context "{}"} {detail ""}} {
    ct_recovery_test_original $kind $stage $context $detail
    global ct_checkpoint ct_completed_modules ct_stream ct_target_stream ct_test_pause_stage ct_test_dirty_tail
    if {![info exists ct_checkpoint]} {return}
    set directory [file dirname $ct_checkpoint]
    set marker [file join $directory test-paused.json]
    if {[file exists $marker]} {return}
    set pause 0
    if {$ct_test_pause_stage eq "import" && $stage eq "import" && $kind eq "begin"} {set pause 1}
    if {$ct_test_pause_stage eq "modules" && $stage eq "modules" && $detail eq "native instance query" && $ct_completed_modules>=1} {set pause 1}
    if {$ct_test_pause_stage eq "trace" && $stage eq "trace" && $kind eq "item_begin" &&
            [regexp {"trace_id":([0-9]+),} $context -> trace] && $trace == min(33,$::ct_pin_count)} {set pause 1}
    if {!$pause} {return}
    if {$stage ne "import" && $ct_test_dirty_tail} {
        puts -nonewline $ct_stream "\{\"UNCOMMITTED-测试\":"; flush $ct_stream
        puts -nonewline $ct_target_stream "\n\{UNCOMMITTED-测试"; flush $ct_target_stream
    }
    set stream [open $marker w]
    puts $stream [ct_object stage [ct_js $stage] pid [pid] context $context]
    close $stream
    while {![file exists [file join $directory test-release]]} {after 50}
}
'''


def wait_for(test,description,seconds=150):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=test()
        if value:return value
        time.sleep(.04)
    raise AssertionError('Test watchdog: '+description)


def config_path(out,mode):return out/'fixture'/('{}-input.json'.format(mode))


def make_fixture(out):
    directory=out/'fixture';directory.mkdir(parents=True,exist_ok=True)
    (directory/'rtl.sv').write_text(RTL)
    filelist=directory/'rtl.f';filelist.write_text(str(directory/'rtl.sv')+'\n')
    for mode in ('clock','reset'):
        rules=([('rec_mux','mux',['a','b'],'y'),('rec_gate','gate',['i'],'o'),('rec_div','div',['i'],'o')]
               if mode=='clock' else [('rec_rmux','mux',['a','b'],'y'),('rec_sync','rsy_sync',['rn'],'ro')])
        config={'tree_type':mode,'trace_scope':'rec_top','design':{'top':'rec_top','filelist':str(filelist)},
                'layers':[{'index':i,'hierarchy':'rec_top','modules':[{'module':module,'kind':kind,
                           mode+'_inputs':inputs,mode+'_output':output}]} for i,(module,kind,inputs,output) in enumerate(rules,1)]}
        write_graph(config_path(out,mode),config)


def instrument(stage,dirty=True):
    extraction=importlib.import_module('clocktree.extract')
    if stage in ('import','modules','trace'):
        runner=extraction.run_verdi
        def wrapped(command,work,environment,raw,log_path,output,config,**kwargs):
            path=Path(work)/'clocktree-extract.tcl'
            code=path.read_text(encoding='utf-8')
            hook='set ct_test_pause_stage '+tcl_string(stage)+'\nset ct_test_dirty_tail '+str(int(dirty))+'\n'+HOOK
            path.write_text(code.replace('if {[catch {ct_main} message]}',hook+'\nif {[catch {ct_main} message]}'),encoding='utf-8')
            return runner(command,work,environment,raw,log_path,output,config,**kwargs)
        extraction.run_verdi=wrapped
    else:
        function={'topology':'build_graph','index':'index_graph','export':'write_graph'}[stage]
        def paused(*args,**kwargs):
            # Only postprocessing uses these imported extraction functions.
            write_graph(Path(os.environ['CT7_RECOVERY_TEST_OUT'])/'post-paused.json',{'pid':os.getpid(),'stage':stage})
            while True:time.sleep(.05)
        setattr(extraction,function,paused)
    return extraction


def child(config,out,stage,dirty=True):
    os.environ['CT7_RECOVERY_TEST_OUT']=str(out)
    graph=instrument(stage,dirty).extract(config,out)
    return 0 if graph['summary']['connectivity_complete'] else 2


def paused_at(directory):
    state=read_json(directory/'run-status.json',{})
    if state.get('journal_directory'):
        manifest=read_json(Path(state['journal_directory'])/'recovery.json',{})
        if manifest:
            marker=read_json(Path(manifest['work'])/'test-paused.json')
            if marker:return marker
    return read_json(directory/'post-paused.json')


def result_path(directory,mode):return directory/('clocktree.json' if mode=='clock' else 'resettree.json')


def check_result(directory,mode,baseline,checkpoint):
    graph=read_graph(result_path(directory,mode));same_graph(baseline,graph)
    manifest=locate(directory);work=Path(manifest['work'])
    events=list(read_events(directory/'raw.jsonl'))
    assert sum(e['event']=='complete' for e in events)==1
    ids=[e['trace_id'] for e in events if e['event']=='trace_begin']
    assert len(ids)==len(set(ids))==graph['summary']['expected_pin_traces']
    assert b'UNCOMMITTED' not in Path(manifest['raw']).read_bytes()
    assert b'UNCOMMITTED' not in (work/'targets.tcl').read_bytes()
    audit=read_json(Path(manifest['journal'])/'interruption-0001.json')
    assert audit['checkpoint']==checkpoint
    report=read_json(directory/'memory.json')
    if checkpoint['stage']=='complete':
        assert report['attempts']==1 and report['reused_complete_evidence']
    else:
        assert report['attempts']==2
        text=(directory/'verdi.log').read_text(encoding='utf-8',errors='replace')
        assert 'modules={}'.format(checkpoint['completed_modules']) in text
        assert 'traces={}'.format(checkpoint['completed_traces']) in text
    return {'instances':len(graph['nodes']),'edges':len(graph['edges']),
            'committed_modules':checkpoint['completed_modules'],'committed_traces':checkpoint['completed_traces'],
            'attempts':report['attempts'],'old_worker_gone':True,'exact_evidence':True,
            'extraction_mode':graph['summary'].get('extraction_mode','full')}


def crash_case(out,mode,stage,victim,baseline):
    directory=out/(mode+'-'+victim+'-'+stage);directory.mkdir()
    with (directory/'test-cli.log').open('w') as stream:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',stage,'--config',str(config_path(out,mode)),
                                  '--out',str(directory)],stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            wait_for(lambda:paused_at(directory),'pause '+str(directory))
            state=read_json(directory/'run-status.json');identity=state.get('worker')
            if victim=='worker':
                os.killpg(identity['pid'],signal.SIGKILL)
                assert process.wait(timeout=60) in (1,2)
            else:
                process.kill();process.wait(timeout=10)
                # Deliberately leave a live orphan Verdi. Recovery must own and
                # stop that group before rolling back either evidence file.
            info=inspect_recovery(directory)
            assert info['resumable'],info
            assert info['diagnosis']['stage']==stage,info
            checkpoint=info['checkpoint']
            if stage=='modules':assert checkpoint['completed_modules']==1
            if stage=='trace':assert checkpoint['completed_traces']==min(32,baseline['summary']['expected_pin_traces']-1)
            write_graph(directory/'before-reopen.json',info)
            with (directory/'resume-cli.log').open('w') as stream:
                subprocess.check_call([sys.executable,'-m','clocktree','resume','--run',str(directory),
                                       '--memory-limit-gib','1'],stdout=stream,stderr=subprocess.STDOUT)
            assert not alive(identity)
            return dict(case=directory.name,pass_=True,**check_result(directory,mode,baseline,checkpoint))
        finally:
            if process.poll() is None:process.kill();process.wait()
            state=read_json(directory/'run-status.json',{})
            identity=state.get('worker') or {}
            if alive(identity):os.killpg(identity['pid'],signal.SIGKILL)


def gui_child(out,mode,live=False):
    from clocktree.gui import ClockTreeApp
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session')
    app.geometry('1840x1000+20+20')
    original=app._launch_extraction
    def launch(directory,result,command,resuming=False):
        command=[sys.executable,str(Path(__file__).resolve()),'--child','modules',
                 '--out',str(directory),'--config',command[command.index('--config')+1]]
        if live:command.append('--clean-tail')
        original(directory,result,command,resuming)
    app._launch_extraction=launch
    def start():
        if app.busy:app.after(50,start);return
        app.open_config(config_path(out.parent,mode));app.start_extraction()
        write_graph(out/'gui-started.json',{'pid':os.getpid(),'job':str(app.job_dir)})
    app.after(500,start);app.mainloop()


def gui_reopen(out,mode,live=False):
    from clocktree.gui import ClockTreeApp
    from capture_x11 import x11_image
    errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session')
    app.geometry('1840x1000+20+20')
    app.show_error=lambda title,error:errors.append(str(error))
    app.report_callback_exception=lambda kind,error,tb:errors.append(str(error))
    def pump(test):
        return wait_for(lambda:(app.update() or test()),'GUI '+mode)
    pump(lambda:app.recovery_dialog is not None)
    dialog=app.recovery_dialog;app.update()
    if live:
        assert dialog.info['running'] and not dialog.info['resumable']
        assert '仍在运行' in dialog.detail.get('1.0','end')
    else:
        assert dialog.info['resumable'] and '具体原因未知' in dialog.detail.get('1.0','end')
        assert dialog.info['checkpoint']['completed_modules']==1
    x11_image().save(str(out/'recovery-notice.png'))
    if live:
        work=Path(dialog.info['manifest']['work'])
        dialog.monitor()
        (work/'test-release').write_text('continue original worker')
    else:dialog.limit.set('1');dialog.resume_button.invoke()
    pump(lambda:not app.busy)
    assert not errors,errors
    assert app.graph and app.graph['summary']['connectivity_complete']
    baseline=read_graph(result_path(out.parent/(mode+'-baseline'),mode));same_graph(baseline,app.graph)
    assert app.graph['metadata']['runtime']['recovery_count']==(0 if live else 1)
    assert app.graph['metadata']['memory_protection']['attempts']==(1 if live else 2)
    app.update();x11_image(app.winfo_id()).save(str(out/'recovery-finished.png'))
    write_graph(out/'gui-acceptance.json',{'pass':True,'real_gui_reopened':True,'reason_notice':True,
                                        'button_resumed_real_verdi':not live,'observed_existing_worker':live,
                                        'no_duplicate_worker':True,'exact_evidence':True,'errors':errors})
    app.close();pump(lambda:app._destroying)


def gui_case(out,mode,live=False):
    directory=out/('gui-'+mode+('-live' if live else ''));directory.mkdir()
    with (directory/'child.log').open('w') as stream:
        command=[sys.executable,str(Path(__file__).resolve()),'--gui-child',mode,'--out',str(directory)]
        if live:command.append('--live')
        process=subprocess.Popen(command,
                                  stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            started=wait_for(lambda:read_json(directory/'gui-started.json'),'GUI start')
            job=Path(started['job']);wait_for(lambda:paused_at(job),'GUI worker pause')
            state=read_json(job/'run-status.json')
            process.kill();process.wait(timeout=10)
            if not live:
                os.kill(state['owner']['pid'],signal.SIGKILL)
                wait_for(lambda:alive(state['owner']) is False,'dead GUI supervisor')
            command=[sys.executable,str(Path(__file__).resolve()),'--gui-reopen',mode,'--out',str(directory)]
            if live:command.append('--live')
            subprocess.check_call(command)
            assert not alive(state['worker'])
        finally:
            if process.poll() is None:process.kill();process.wait()


def display_pause(out):
    from clocktree.gui import ClockTreeApp
    from clocktree.tkdiagram import ClockDiagram
    original=ClockDiagram._paint
    def pause(self):
        if self.model is None:return original(self)
        write_graph(out/'display-paused.json',{'pid':os.getpid()})
        while True:time.sleep(.05)
    ClockDiagram._paint=pause
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session')
    app.mainloop()


def display_reopen(out):
    from clocktree.gui import ClockTreeApp
    from capture_x11 import x11_image
    errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session')
    app.show_error=lambda title,error:errors.append(str(error))
    app.report_callback_exception=lambda kind,error,tb:errors.append(str(error))
    def pump(test):return wait_for(lambda:(app.update() or test()),'safe display recovery')
    pump(lambda:app.recovery_dialog is not None)
    assert app.graph is None and app._previous_gui_issue['stage']=='canvas'
    assert app.recovery_dialog.info['diagnosis']['state']=='finished'
    x11_image().save(str(out/'display-recovery-notice.png'))
    app.recovery_dialog.open_result()
    pump(lambda:not app.busy and app.diagram._pending is None)
    assert not errors,errors
    assert app.graph['summary']['connectivity_complete']
    assert app.graph['metadata']['memory_protection']['attempts']==2
    assert app.graph['metadata']['runtime']['recovery_count']==1
    app.close();pump(lambda:app._destroying)
    write_graph(out/'display-acceptance.json',{'pass':True,'killed_at':'canvas','safe_configuration_first':True,
                                            'explicit_result_opened':True,'no_extra_verdi':True,'errors':errors})


def display_case(out):
    directory=out/'gui-clock'
    with (directory/'display-child.log').open('w') as stream:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gui-display-pause','--out',str(directory)],
                                  stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            wait_for(lambda:read_json(directory/'display-paused.json'),'real GUI canvas')
            process.kill();process.wait(timeout=10)
            subprocess.check_call([sys.executable,str(Path(__file__).resolve()),'--gui-display-reopen','--out',str(directory)])
        finally:
            if process.poll() is None:process.kill();process.wait()


def reuse_case(out):
    from clocktree.extract import extract
    directory=out/'reused-output';directory.mkdir()
    process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child','trace','--config',str(config_path(out,'clock')),
                              '--out',str(directory)],stdout=subprocess.DEVNULL,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        wait_for(lambda:paused_at(directory),'old task before directory reuse')
        state=read_json(directory/'run-status.json');old_journal=Path(state['journal_directory'])
        process.kill();process.wait(timeout=10);os.killpg(state['worker']['pid'],signal.SIGKILL)
        wait_for(lambda:alive(state['worker']) is False,'old worker stopped')
        checkpoint=inspect_recovery(old_journal)['checkpoint']
        newer=extract(config_path(out,'reset'),directory)
        new_work=Path(newer['metadata']['working_directory']);new_raw=(new_work/'raw.jsonl').read_bytes()
        subprocess.check_call([sys.executable,'-m','clocktree','resume','--run',str(old_journal)],stdout=subprocess.DEVNULL)
        baseline=read_graph(result_path(out/'clock-baseline','clock'))
        result=check_result(directory,'clock',baseline,checkpoint)
        assert (new_work/'raw.jsonl').read_bytes()==new_raw
        assert list((directory/'.clocktree-history').glob('*/resettree.json'))
        write_graph(out/'reuse-acceptance.json',dict(result,pass_=True,newer_result_archived=True,newer_raw_unchanged=True))
    finally:
        if process.poll() is None:process.kill();process.wait()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default=str(ROOT/'validation/crash-recovery/acceptance'))
    parser.add_argument('--child',choices=('import','modules','trace','topology','index','export'))
    parser.add_argument('--config')
    parser.add_argument('--clean-tail',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--soc',action='store_true')
    parser.add_argument('--gui-child',choices=('clock','reset'))
    parser.add_argument('--gui-reopen',choices=('clock','reset'))
    parser.add_argument('--gui',action='store_true')
    parser.add_argument('--gui-display',action='store_true')
    parser.add_argument('--gui-display-pause',action='store_true')
    parser.add_argument('--gui-display-reopen',action='store_true')
    parser.add_argument('--reuse',action='store_true')
    args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.child:return child(args.config,out,args.child,not args.clean_tail)
    if args.gui_child:return gui_child(out,args.gui_child,args.live)
    if args.gui_reopen:return gui_reopen(out,args.gui_reopen,args.live)
    if args.gui_display_pause:return display_pause(out)
    if args.gui_display_reopen:return display_reopen(out)
    if args.gui_display:return display_case(out)
    if args.reuse:return reuse_case(out)
    if args.gui:
        if args.live:gui_case(out,'clock',live=True)
        else:
            for mode in ('clock','reset'):gui_case(out,mode)
            gui_case(out,'clock',live=True)
            display_case(out)
        return 0
    if args.soc:
        from check_direct_instances import direct_soc_config
        from clocktree.extract import extract
        cfg,_,expected=direct_soc_config(out/'fixture');write_graph(config_path(out,'clock'),cfg)
        baseline=extract(config_path(out,'clock'),out/'clock-baseline')
        assert {n['id'] for n in baseline['nodes']}==expected
        report={'cases':[crash_case(out,'clock',stage,'controller',baseline) for stage in ('trace','export')],
                'pass':True,'scope':'OpenC910 selected direct-parent subnetwork, not full-chip extraction'}
        write_graph(out/'acceptance.json',report);return 0
    make_fixture(out);report={'cases':[]}
    from clocktree.extract import extract
    for mode in ('clock','reset'):
        baseline=extract(config_path(out,mode),out/(mode+'-baseline'))
        assert baseline['summary']['connectivity_complete'],list(baseline['diagnostics'])
        for stage in ('import','modules','trace','topology','index','export'):
            result=crash_case(out,mode,stage,'controller',baseline);report['cases'].append(result)
            write_graph(out/'acceptance.json',report);print(json.dumps(result),flush=True)
        for stage in ('modules','trace'):
            result=crash_case(out,mode,stage,'worker',baseline);report['cases'].append(result)
            write_graph(out/'acceptance.json',report);print(json.dumps(result),flush=True)
    report['pass']=True;write_graph(out/'acceptance.json',report)
    return 0


if __name__=='__main__':raise SystemExit(main())
