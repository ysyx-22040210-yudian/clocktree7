"""Real Verdi/Tk stage journals and deterministic SIGKILL post-mortem checks.

Pauses are test-only wrappers around the unchanged native query/trace calls.
The test watchdogs below never change the product's unlimited execution time.
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
from clocktree.runtime import diagnose, diagnose_gui, read_json, last_event, alive
from clocktree.storage import write_graph

HOOK=r'''
rename ct_journal ct_test_journal
proc ct_journal {kind stage {context "{}"} {detail ""}} {
    ct_test_journal $kind $stage $context $detail
    global env ct_completed_modules
    set target $env(CT7_TEST_STAGE)
    set pause 0
    if {$target eq "import" && $stage eq "import" && $kind eq "begin"} {set pause 1}
    if {$target eq "modules" && $stage eq "modules" && $detail eq "native instance query" && $ct_completed_modules>=1} {set pause 1}
    if {$target eq "trace" && $stage eq "trace" && $kind eq "item_begin"} {set pause 1}
    if {$pause} {
        set stream [open $env(CT7_TEST_PAUSE) w]
        puts $stream [ct_object stage [ct_js $stage] pid [pid] context $context]
        close $stream
        while {1} {after 50}
    }
}
'''


def config_path(mode='clock'):
    return ROOT/('validation/low-memory/scoped/module/clock-bounded/config.json' if mode=='clock'
                 else 'validation/low-memory/scoped/generate/reset-bounded/config.json')


def child(out,stage,mode='clock'):
    extraction=importlib.import_module('clocktree.extract')
    if stage in ('import','modules','trace'):
        runner=extraction.run_verdi
        def instrument(command,work,environment,raw,log_path,output,config):
            path=Path(work)/'clocktree-extract.tcl'
            code=path.read_text(encoding='utf-8')
            path.write_text(code.replace('if {[catch {ct_main} message]}',HOOK+'\nif {[catch {ct_main} message]}'),encoding='utf-8')
            env=dict(environment,CT7_TEST_STAGE=stage,CT7_TEST_PAUSE=str(out/'test-pause.json'))
            return runner(command,work,env,raw,log_path,output,config)
        extraction.run_verdi=instrument
    elif stage in ('topology','index','export'):
        def paused(*args,**kwargs):
            write_graph(out/'test-pause.json',{'stage':stage,'pid':os.getpid()})
            while True:time.sleep(.05)
        setattr(extraction,{'topology':'build_graph','index':'index_graph','export':'write_graph'}[stage],paused)
    graph=extraction.extract(config_path(mode),out)
    return 0 if graph['summary']['connectivity_complete'] else 2


def wait_for(test,description,seconds=120):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        result=test()
        if result:return result
        time.sleep(.05)
    raise AssertionError('Test watchdog expired: '+description)


def stop_group(identity):
    pid=identity.get('pid')
    if alive(identity) and os.getpgid(pid)==pid:
        os.killpg(pid,signal.SIGKILL)


def crash_case(out,stage,victim='worker'):
    directory=out/(victim+'-'+stage);directory.mkdir(parents=True,exist_ok=True)
    marker=directory/'test-pause.json'
    if marker.exists():marker.unlink()
    with (directory/'test-cli.log').open('w') as stream:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',stage,'--out',str(directory)],
                                 stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            pause=wait_for(lambda:read_json(marker),'pause '+stage)
            state=read_json(directory/'run-status.json',{})
            worker=state.get('worker',{})
            if victim=='worker':
                assert worker.get('pid') and os.getpgid(worker['pid'])==worker['pid']
                stop_group(worker)
                code=process.wait(timeout=60)
                assert code in (1,2),code
            else:
                process.kill();process.wait(timeout=10)
                stop_group(worker)
            result=diagnose(directory)
            assert result['stage']==stage,result
            assert result['state']==('failed' if victim=='worker' else 'interrupted'),result
            if victim=='worker':
                assert result['last_failure']['exit_signal']=='SIGKILL',result
                assert result['cause']=='worker_exit_-9'
            else:assert result['cause']=='unknown'
            if stage=='modules':assert result['checkpoint']['completed_modules']>=1
            if stage=='trace':
                assert result['context']['instance'] and result['context']['pin']
                assert result['context']['direction'] in ('load','driver')
            write_graph(directory/'diagnosis.json',result)
            return {'case':victim+'-'+stage,'pass':True,'stage':result['stage'],'state':result['state'],
                    'context':result['context'],'cause':result['cause']}
        finally:
            if process.poll() is None:process.kill();process.wait()
            stop_group(read_json(directory/'run-status.json',{}).get('worker',{}))


def gui_child(out,result,stage):
    from clocktree.gui import ClockTreeApp
    from clocktree.indexed_view import IndexedGraphView
    from clocktree.tkdiagram import ClockDiagram
    marker=out/'test-pause.json'
    def paused(*args,**kwargs):
        write_graph(marker,{'stage':stage,'pid':os.getpid()})
        while True:time.sleep(.05)
    if stage=='layout':IndexedGraphView.project=paused
    if stage=='canvas':
        original=ClockDiagram._paint
        def pause_result_canvas(self):
            if self.model is None:return original(self)
            return paused(self)
        ClockDiagram._paint=pause_result_canvas
    app=ClockTreeApp(runs=out/'runs',remember=False,session_dir=out/'gui')
    app.after(100,lambda:app.load_result(result))
    app.mainloop()


def gui_cases(out):
    from clocktree.gui import ClockTreeApp
    from capture_x11 import x11_image
    baseline=out/'baseline-clock/clocktree.json'
    report=[]
    app=ClockTreeApp(runs=out/'gui-runs',remember=False,session_dir=out/'gui-display')
    app.report_callback_exception=lambda kind,value,tb:(_ for _ in ()).throw(value)
    app.load_result(baseline)
    def pump():
        app.update()
        return app.graph is not None and app.gui_journal.state().get('stage')=='display' and not app.busy
    wait_for(pump,'GUI real result')
    app._update_runtime_view()
    assert all(app.stage_view.steps.get(stage)=='done' for stage in
               ('config','import','modules','trace','topology','index','export','display')),app.stage_view.steps
    app.show_stage_log('modules')
    wait_for(lambda:(app.update() or ('module checkpoint' in app.log_text.get('1.0','end'))),'stage click log')
    app.show_stage_log(None)
    app.update()
    app.diagram.fit();app.update();time.sleep(.05);app.update()
    x11_image(app.winfo_id()).save(str(out/'stages-complete.png'))
    app.show_run_diagnosis();app.update()
    assert '阶段诊断' in app.log_text.get('1.0','end')
    directory=out/'gui-live-trace';directory.mkdir(exist_ok=True)
    marker=directory/'test-pause.json'
    if marker.exists():marker.unlink()
    with (directory/'test-cli.log').open('w') as stream:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child','trace','--out',str(directory)],
                                 stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            app._runtime_directory=directory
            app._runtime_stamp=None
            app.job_dir=directory;app._set_busy('extract')
            app._badge('正在追踪端口','busy')
            def live():
                app.update();app._update_runtime_view()
                return read_json(marker) and app.stage_view.current=='trace' and '端口' in app.stage_view.item.get()
            wait_for(live,'live Tk port stage')
            assert app.stage_view.steps['trace']=='running'
            app.show_stage_log(None);app.update()
            app.diagram.fit();app.update();time.sleep(.05);app.update()
            x11_image(app.winfo_id()).save(str(out/'stages-running-port.png'))
            process.kill();process.wait(timeout=10)
            stop_group(read_json(directory/'run-status.json',{}).get('worker',{}))
            app._runtime_stamp=None;app._update_runtime_view()
            assert app._runtime_state['state']=='interrupted'
            app._set_busy(None);app._badge('任务中断 · 查看阶段诊断','warn')
            app.show_run_diagnosis();app.update()
            assert 'interrupted' in app.log_text.get('1.0','end')
            x11_image(app.winfo_id()).save(str(out/'stages-interrupted-port.png'))
            report.append({'case':'native-gui-live-port-and-interruption','pass':True,'stage':'trace'})
        finally:
            if process.poll() is None:process.kill();process.wait()
            stop_group(read_json(directory/'run-status.json',{}).get('worker',{}))
    app.destroy()
    report.append({'case':'native-gui-complete-strip-and-click-filter','pass':True})
    for stage in ('layout','canvas'):
        directory=out/('gui-kill-'+stage);directory.mkdir(exist_ok=True)
        marker=directory/'test-pause.json'
        if marker.exists():marker.unlink()
        with (directory/'test-cli.log').open('w') as stream:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--gui-child',stage,
                '--out',str(directory),'--result',str(baseline)],stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                wait_for(lambda:read_json(marker),'GUI pause '+stage)
                latest=read_json(directory/'gui/latest.json')
                before=diagnose_gui(latest['journal_directory'])
                assert before['stage']==stage and before['state']=='running',before
                process.kill();process.wait(timeout=10)
                result=diagnose_gui(latest['journal_directory'])
                assert result['state']=='interrupted' and result['stage']==stage,result
                write_graph(directory/'diagnosis.json',result)
                report.append({'case':'gui-kill-'+stage,'pass':True,'stage':stage,'state':result['state']})
            finally:
                if process.poll() is None:process.kill();process.wait()
    # Reopening records a new GUI channel while retaining the previous crash.
    app=ClockTreeApp(runs=out/'gui-runs',remember=False,session_dir=out/'gui-kill-canvas/gui')
    app.load_result(baseline)
    wait_for(lambda:(app.update() or (app.graph is not None and not app.busy)),'reopen GUI')
    app.show_run_diagnosis();app.update()
    assert 'previous_gui_issue' in app.log_text.get('1.0','end')
    x11_image(app.winfo_id()).save(str(out/'gui-previous-interruption.png'))
    app.destroy()
    report.append({'case':'reopen-retains-gui-crash-location','pass':True})
    write_graph(out/'gui-acceptance.json',{'pass':True,'cases':report})
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default=str(ROOT/'validation/runtime-stages'))
    p.add_argument('--child');p.add_argument('--gui-child');p.add_argument('--result')
    p.add_argument('--gui-only',action='store_true')
    args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.child:return child(out,args.child)
    if args.gui_child:return gui_child(out,args.result,args.gui_child)
    if args.gui_only:
        print(json.dumps({'pass':True,'cases':gui_cases(out)},indent=2));return 0
    extraction=importlib.import_module('clocktree.extract')
    from check_memory_resume import same_graph
    report={'cases':[]}
    for mode in ('clock','reset'):
        directory=out/('baseline-'+mode)
        graph=extraction.extract(config_path(mode),directory)
        assert graph['summary']['connectivity_complete']
        from clocktree.session import read_graph
        same_graph(read_graph(config_path(mode).with_name('clocktree.json' if mode=='clock' else 'resettree.json')),graph)
        events=[json.loads(line) for line in (directory/'run-events.jsonl').read_text(encoding='utf-8').splitlines()]
        assert all(any(e['stage']==stage and e['kind']=='begin' for e in events)
                   for stage in ('import','modules','trace','topology','index','export'))
        state=read_json(directory/'run-status.json')
        assert state['state']=='finished'
        worker=Path(state['worker_events'])
        records=[json.loads(line) for line in worker.read_text(encoding='utf-8').splitlines()]
        begins=[r for r in records if r['stage']=='trace' and r['kind']=='item_begin']
        ends=[r for r in records if r['stage']=='trace' and r['kind']=='item_end']
        assert len(begins)==len(ends)==graph['summary']['observed_pin_traces']
        assert [e['context'] for e in begins]==[e['context'] for e in ends]
        report['cases'].append({'case':'real-'+mode,'pass':True,'traces':len(begins),'graph_and_evidence_equal':True})
    for stage in ('import','modules','trace'):report['cases'].append(crash_case(out,stage))
    for stage in ('modules','trace','topology','index','export'):
        report['cases'].append(crash_case(out,stage,'controller'))
    report['pass']=True
    write_graph(out/'acceptance.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
