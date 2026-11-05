"""Real NPI high-fanout acceptance, with exact selected edges and sink counts."""
import argparse
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.extract import extract
from clocktree.storage import write_graph

PAUSE = r'''
rename ct_trace_note ct_test_note
proc ct_trace_note {phase hdl api {force 0}} {
    global ct_trace_id ct_walk_stats ct_checkpoint ct_test_pause ct_stream ct_target_stream
    set marker [file join [file dirname $ct_checkpoint] test-paused.json]
    set pause [expr {$ct_test_pause eq "during" && $ct_trace_id == 2 &&
        $phase eq "iterator_scan" && [dict get $ct_walk_stats scanned] >= 128 && ![file exists $marker]}]
    set context [ct_test_note $phase $hdl $api [expr {$force || $pause}]]
    if {$pause} {ct_test_pause_here $marker}
    return $context
}
rename ct_walk ct_test_walk
proc ct_walk {seed direction owner pin trace_id} {
    global ct_test_pause ct_checkpoint
    set marker [file join [file dirname $ct_checkpoint] test-paused.json]
    if {$ct_test_pause eq "after" && $trace_id == 3 && ![file exists $marker]} {
        ct_journal item_begin trace [ct_object trace_id 3 instance [ct_js $owner] pin [ct_js $pin] direction [ct_js $direction]] "next port after committed fanout"
        ct_test_pause_here $marker
    }
    return [ct_test_walk $seed $direction $owner $pin $trace_id]
}
proc ct_test_pause_here {marker} {
    global ct_stream ct_target_stream
    puts -nonewline $ct_stream "\{UNCOMMITTED";flush $ct_stream
    puts -nonewline $ct_target_stream "\n\{UNCOMMITTED";flush $ct_target_stream
    set file [open $marker w];puts $file [ct_object pid [pid]];close $file
    while {1} {after 50}
}
'''


def recover(out):
    from check_crash_recovery import wait_for, check_result
    from clocktree.recovery import inspect_recovery
    from clocktree.runtime import alive,read_json
    cfg=fixture(out/'fixture',128)
    baseline=extract(cfg,out/'baseline')
    report=[]
    for stage in ('during','after'):
        directory=out/stage;directory.mkdir(parents=True,exist_ok=True)
        with (directory/'test-cli.log').open('w') as log:
            process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child',stage,
                                      '--out',str(directory),'--config',str(cfg)],
                                     stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            identity={}
            try:
                def paused():
                    state=read_json(directory/'run-status.json',{})
                    work=Path(state['journal_directory']) if state.get('journal_directory') else None
                    return work if work and (work/'test-paused.json').is_file() else None
                work=wait_for(paused,'high-fanout pause '+stage)
                identity=read_json(directory/'run-status.json')['worker']
                process.kill();process.wait(timeout=10)
                info=inspect_recovery(directory)
                assert info['resumable'] and info['diagnosis']['stage']=='trace',info
                checkpoint=info['checkpoint']
                assert checkpoint['completed_traces']==(0 if stage=='during' else 2),checkpoint
                if stage=='during':
                    assert info['diagnosis']['context']['scanned']>=128
                    assert info['diagnosis']['context']['phase']=='iterator_scan'
                write_graph(directory/'before-reopen.json',info)
                with (directory/'resume-cli.log').open('w') as resume_log:
                    subprocess.check_call([sys.executable,'-m','clocktree','resume','--run',str(directory)],
                                          stdout=resume_log,stderr=subprocess.STDOUT)
                assert not alive(identity)
                result=check_result(directory,'clock',baseline,checkpoint)
                report.append(dict(case=stage,pass_=True,**result))
            finally:
                if process.poll() is None:process.kill();process.wait()
                if alive(identity):os.killpg(identity['pid'],signal.SIGKILL)
    write_graph(out/'acceptance.json',{'pass':True,'cases':report})
    print(json.dumps(report,indent=2))


def child(stage,config,out):
    extraction=importlib.import_module('clocktree.extract')
    runner=extraction.run_verdi
    def instrument(command,work,environment,raw,log_path,output,cfg,**kwargs):
        script=Path(work)/'clocktree-extract.tcl'
        code=script.read_text(encoding='utf-8')
        hook='set ct_test_pause '+stage+'\n'+PAUSE
        script.write_text(code.replace('if {[catch {ct_main} message]}',hook+'\nif {[catch {ct_main} message]}'),encoding='utf-8')
        return runner(command,work,environment,raw,log_path,output,cfg,**kwargs)
    extraction.run_verdi=instrument
    extraction.extract(config,out)


def gui(out):
    from clocktree.gui import ClockTreeApp
    from clocktree.runtime import alive,read_json
    from check_crash_recovery import wait_for
    from check_flexible_gui import pump,capture
    cfg=fixture(out/'fixture',128)
    baseline=extract(cfg,out/'baseline')
    directory=out/('live-'+str(int(time.time())));directory.mkdir()
    errors=[];app=None;identity={}
    with (directory/'test-cli.log').open('w') as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--child','during',
                                  '--out',str(directory),'--config',str(cfg)],
                                 stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:
            def paused():
                state=read_json(directory/'run-status.json',{})
                work=Path(state['journal_directory']) if state.get('journal_directory') else None
                return work if work and (work/'test-paused.json').is_file() else None
            wait_for(paused,'live GUI scan')
            identity=read_json(directory/'run-status.json')['worker']
            app=ClockTreeApp(runs=out/'gui-runs',remember=False)
            app.report_callback_exception=lambda *exc:errors.append(str(exc))
            app.geometry('1840x1000+20+20')
            pump(app);app.show_graph(baseline);pump(app)
            app._runtime_directory=directory
            def visible():
                app.update();app._update_runtime_view()
                text=app.stage_view.item.get()
                return text if '已扫描 128' in text and '持续等待' in text else None
            label=wait_for(visible,'live progress and no-progress age',seconds=20)
            assert '逐项读取连接' in label and 'source' in label,label
            capture(app,out/'high-fanout-progress.png')
            assert not errors,errors
            write_graph(out/'gui.json',{'pass':True,'native_tk':True,'live_owned_worker':True,
                'controlled_pause':True,'port_progress':label,'callback_errors':errors})
            print(label)
        finally:
            if app is not None:app.destroy()
            if process.poll() is None:process.kill();process.wait()
            if alive(identity):os.killpg(identity['pid'],signal.SIGKILL)


def fixture(out, count):
    out.mkdir(parents=True, exist_ok=True)
    lines = ['''module fan_source(input i,output o); assign o=i; endmodule
module fan_gate(input i,en,output o); assign o=i&en; endmodule
module fan_sink(input c,d,output reg q); always @(posedge c) q<=d; endmodule
module fan_domain(input clk,en,d,output [%d:0] q,output [%d:0] clocks);
wire root_clk; fan_source source(.i(clk),.o(root_clk));
wire alias_clk; assign alias_clk=root_clk;
wire [3:0] packed_clk; assign packed_clk={root_clk,clk,alias_clk,clk};
''' % (count-1, count+1)]
    for i in range(count):
        lines.append('fan_gate g%d(.i(root_clk),.en(en),.o(clocks[%d]));' % (i, i))
        lines.append('fan_sink ff%d(.c(root_clk),.d(d),.q(q[%d]));' % (i, i))
    lines += ['fan_gate via_bit(.i(packed_clk[3]),.en(en),.o(clocks[%d]));' % count,
              'fan_gate via_alias(.i(packed_clk[1]),.en(en),.o(clocks[%d]));' % (count+1),
              'endmodule',
              'module fan_top(input clk,en,d,output [%d:0] q,output [%d:0] clocks);' % (count-1,count+1),
              'fan_domain domain(.*); endmodule']
    (out/'fixture.sv').write_text('\n'.join(lines), encoding='utf-8')
    (out/'rtl.f').write_text(str(out/'fixture.sv')+'\n', encoding='utf-8')
    config = {'design':{'top':'fan_top','filelist':str(out/'rtl.f')}, 'trace_scope':'fan_top.domain',
              'layers':[{'index':1,'hierarchy':'fan_top.domain','modules':[
                  {'module':'fan_source','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]},
                  {'index':2,'hierarchy':'fan_top.domain','modules':[
                  {'module':'fan_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]}]}
    write_graph(out/'config.json', config)
    return out/'config.json'


def check(graph, count):
    source = 'fan_top.domain.source'
    expected = {'fan_top.domain.g%d' % i for i in range(count)} | {
        'fan_top.domain.via_bit','fan_top.domain.via_alias'}
    assert graph['summary']['connectivity_complete'], graph['diagnostics']
    assert graph['summary']['topology_valid'], graph['diagnostics']
    assert len(graph['nodes']) == count+3
    assert {(e['source'],e['source_pin'],e['target'],e['target_pin']) for e in graph['edges']} == {
        (source,'o',target,'i') for target in expected}
    assert all(set(e['evidence']) == {'driver','load'} for e in graph['edges'])
    sinks = sum(b['count'] for b in graph['boundaries'] if b['instance']==source and b['status']=='sink_group')
    assert sinks == count, sinks
    assert all(n['scope_output_boundary'] for n in graph['nodes'] if n['id'] != source)
    return {'instances':len(graph['nodes']),'edges':len(graph['edges']),'sinks':sinks,
            'both_directions':True,'concat_alias_bits':True,'scope_boundaries':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default=str(ROOT/'validation/high-fanout/acceptance'))
    parser.add_argument('--counts',type=int,nargs='+',default=[128,1024])
    parser.add_argument('--recover',action='store_true',help='Kill a controller inside/after a high-fanout port, then resume in a new process')
    parser.add_argument('--gui',action='store_true',help='Native Tk live scan progress with an owned, deliberately paused worker')
    parser.add_argument('--child',choices=['during','after'],help=argparse.SUPPRESS)
    parser.add_argument('--config',help=argparse.SUPPRESS)
    args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.child:return child(args.child,args.config,out)
    if args.recover:return recover(out)
    if args.gui:return gui(out)
    checks=[]
    for count in args.counts:
        cfg=fixture(out/str(count)/'fixture',count)
        start=time.monotonic()
        graph=extract(cfg,out/str(count)/'run')
        result=check(graph,count)
        result.update(fanout=count*2+2,seconds=round(time.monotonic()-start,3),
                      memory=graph['metadata'].get('memory_protection',{}))
        checks.append(result)
        print(json.dumps({k:v for k,v in result.items() if k!='memory'}),flush=True)
        write_graph(out/'acceptance.json',{'pass':True,'cases':checks})


if __name__=='__main__':main()
