"""Verify subsystem input endpoints with Verdi 2018, Tk, VCS and synthetic scale."""
import argparse
from contextlib import closing
import shutil
import sqlite3
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.storage import write_graph
from clocktree.session import read_graph
from clocktree.sv_templates import prepare,SVPlan,architecture_signature,graph_for_templates
from clocktree.register_trace import run as trace,seeds_from_graph
from check_target_mode import run as extract

ALL=' '.join('$'+v for v in ('var_pllsel','var_div','var_swdone','var_gt0','var_gt1','var_gt2',
    'var_pll0','var_pll1','var_pll2','var_pll3','var_clkip','var_rst'))
TEMPLATES={mode:'initial $display("'+mode+'", $var_pll0, $clkip, $var_rst);' for mode in ('with_mux','without_mux')}


def native(out):
    out.mkdir(parents=True,exist_ok=True);fixture=out/'fixture';fixture.mkdir()
    rtl='''module unit_gate(input i,en,output o);assign o=i&en;endmodule
module unit_occ(input i,output o);assign o=i;endmodule
module unit_div(input i,input [1:0] ratio,output o,done);assign o=i;assign done=i;endmodule
module unit_mux(input a,b,sel,output o);assign o=sel?a:b;endmodule
module sink(input [3:2] c);endmodule
module scalar_sink(input clk_i, open_i, output status_o, inout probe_io); assign status_o=clk_i; endmodule
module wire_unit(input a,b,output x,y,z);assign x=b;assign y=a;assign z=a;endmodule
module mid(input i,output o);assign o=i;endmodule
module end3(input i,output o);assign o=i;endmodule
module end5(input i,output o);assign o=i;endmodule
module endmux(input a,b,sel,output o);assign o=sel?a:b;endmodule
module sv_ends(input clk,rst_n,input [7:0] pads,data,output [15:0] outs);
reg [7:0] Q,E;always @(posedge clk) Q<=data;
wire done_unused,done_real,h,m;
unit_gate g(.i(pads[0]),.en(1'b1),.o(outs[0]));
unit_occ occ(.i(pads[1]),.o(outs[1]));
unit_div d(.i(pads[2]),.ratio(2'b0),.o(outs[2]),.done(done_unused));
unit_mux mux(.a(pads[3]),.b(pads[4]),.sel(1'b0),.o(outs[3]));
sink sub(.c({pads[5],pads[6]}));
scalar_sink gpu(.clk_i(outs[0]));
scalar_sink bus_ip(.clk_i(outs[0]));
wire_unit wire0(.a(pads[1]),.b(pads[2]),.x(outs[4]),.y(outs[5]),.z(outs[6]));
real_gate rg(.i(pads[0]),.en(Q[7]),.o(outs[7]));
real_div rd(.i(pads[1]),.ratio(Q[5:4]),.o(outs[8]),.done(done_real));
real_mux rm(.a(pads[2]),.b(pads[3]),.sel(Q[2]),.o(outs[9]));
always @(posedge clk) E[6]<=done_real;
head first(.i(pads[7]),.o(h));mid middle(.i(h),.o(m));
end3 short_end(.i(m),.o(outs[10]));end5 long_end(.i(m),.o(outs[11]));
endmux merge(.a(h),.b(h),.sel(Q[0]),.o(outs[12]));
endmodule
'''
    for old,new in [('unit_gate','real_gate'),('unit_div','real_div'),('unit_mux','real_mux'),('unit_occ','head')]:
        original=next(line for line in rtl.splitlines() if line.startswith('module '+old+'('))
        rtl+=original.replace('module '+old+'(','module '+new+'(')+'\n'
    (fixture/'rtl.sv').write_text(rtl)
    build=out/'kdb';build.mkdir()
    with (build/'build.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','sv_ends',str(fixture/'rtl.sv')],cwd=str(build),stdout=log,stderr=subprocess.STDOUT)
    def rule(module,kind,inputs=('i',),aux=None):
        r=dict(module=module,kind=kind,clock_inputs=list(inputs))
        if kind=='wire':r['clock_outputs']=['x','y','z']
        elif kind!='subsystem':r['clock_output']='o'
        if aux:r['aux_ports']=aux
        return r
    first=[rule('unit_gate','gate'),rule('unit_occ','occ'),rule('unit_div','div'),rule('unit_mux','mux',('a','b')),
        rule('sink','subsystem',('c',)),rule('wire_unit','wire',('a','b')),rule('real_gate','gate',aux={'gt':'en'}),
        rule('real_div','div',aux={'div':'ratio','sw_done':'done'}),rule('real_mux','mux',('a','b'),{'pll_sel':'sel'}),
        rule('head','occ'),rule('mid','occ')]
    cfg={'design':{'top':'sv_ends','elab':str(build/'simv.daidir/kdb.elab++')},'memory_limit_gib':4,
        'layers':[dict(index=i,hierarchy='sv_ends',reset_signal='rst_n' if i==1 else 'unused_rst',
            modules=first if i==1 else [rule('end3','occ'),rule('endmux','mux',('a','b'))] if i==3 else [rule('end5','occ')] if i==5 else [rule('scalar_sink','subsystem',('clk_i',))] if i==4 else []) for i in range(1,6)]}
    graph,report=extract(cfg,out/'tree');assert graph['summary']['connectivity_complete'],graph['summary']
    write_graph(out/'register-request.json',{'config':graph['config'],'seeds':list(seeds_from_graph(graph))})
    registers=trace(out/'register-request.json',out/'registers');assert registers['summary']['complete'],registers
    start=time.monotonic();plan=prepare(graph,out/'registers',out/'plan')
    count,rows=plan.branches()
    expected={'sv_ends.sub.c[2]':'sv_ends.pads[6]', 'sv_ends.sub.c[3]':'sv_ends.pads[5]',
              'sv_ends.gpu.clk_i':'sv_ends.pads[0]', 'sv_ends.bus_ip.clk_i':'sv_ends.pads[0]'}
    assert count==len(expected) and {r['clkip'] for r in rows}==set(expected), rows
    assert any(n['kind']=='wire' for n in graph['nodes']) and any(n['kind']=='mux' and n['terminal'] for n in graph['nodes'])
    details=[]
    for row in rows:
        value=plan.resolve(row,TEMPLATES)
        assert value['ready'] and value['values']['var_rst']=='sv_ends.rst_n', value
        assert value['values']['var_pll0']==expected[row['clkip']] and value['values']['var_clkip']==row['instance']+'.'+row['pin'], value
        details.append(value)
    export=plan.export(TEMPLATES,out/'all.sv')
    assert export['blocks']==len(expected) and export['skipped']==0
    content=(out/'all.sv').read_text(encoding='utf-8')
    assert content.count('initial $display(')==len(expected) and '//' not in content and '$clkip' not in content
    (fixture/'checks.sv').write_text('module template_checks;\n'+content+'\nendmodule\n')
    compiled=out/'compile';compiled.mkdir()
    with (compiled/'compile.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','sv_ends','-top','template_checks',str(fixture/'rtl.sv'),str(fixture/'checks.sv')],cwd=str(compiled),stdout=log,stderr=subprocess.STDOUT)
    invalid_cases = {}
    for mode in ('target','full'):
        case=out/('port-validation-'+mode)
        pins=['clk_i','open_i','status_o','probe_io','absent']
        validation=dict(cfg,extraction_mode=mode,layers=[
            dict(layer,modules=[rule('scalar_sink','subsystem',pins) if item['module']=='scalar_sink' else item
                                for item in layer['modules'] if item['module']!='sink'])
            for layer in cfg['layers']])
        extracted,_=extract(validation,case/'tree')
        checked=prepare(extracted,None,case/'plan',False)
        templates={'without_mux':'initial $display($clkip);'}
        result=checked.export(templates,case/'valid.sv')
        mapping=json.loads(Path(result['mapping']).read_text(encoding='utf-8'))
        assert {r['clkip'] for r in mapping['branches'] if r['ready']}=={'sv_ends.gpu.clk_i','sv_ends.bus_ip.clk_i'}
        assert (result['blocks'],result['skipped'])==(2,8), result
        assert all(r['issues'] for r in mapping['branches'] if r['pin']!='clk_i')
        invalid_cases[mode]={'ready':result['blocks'],'skipped':result['skipped']}
    write_graph(out/'acceptance.json',dict(passed=True,real_kdb=True,branches=len(expected),unique_formals=sorted(expected),invalid_port_cases=invalid_cases,
        sv_vcs_elaborated=True,elapsed_seconds=time.monotonic()-start,extraction=report,register_summary=registers['summary'],values=details))
    print('SV_BRANCH_ENDPOINTS_NATIVE_PASS',flush=True)


def scale(out,count):
    import resource
    out.mkdir(parents=True,exist_ok=True)
    nodes=[dict(id='scale.ip'+str(i),kind='subsystem',layer=1,inputs=['i'],output=None,terminal=True,aux_ports={},
        port_connections={p:dict(status='connected',full_name='scale.'+p+str(i)) for p in ('i',)}) for i in range(count)]
    graph={'tree_type':'clock','config':{'design':{'top':'scale'},'layers':[dict(index=1,hierarchy='scale',reset_signal='rst_n')]},
        'summary':{'connectivity_complete':True},'nodes':nodes,'edges':[]}
    nodes.extend(dict(id='scale.g'+str(i),kind='gate',layer=1,inputs=['i'],output='o',terminal=True) for i in range(count))
    start=time.monotonic();plan=prepare(graph,None,out/'plan',False)
    body='initial $display('+', '.join(ALL.split())+');'
    export=plan.export(dict(with_mux=body,without_mux=body),out/'all.sv')
    assert export['blocks']==count and export['skipped']==0 and plan.source()['branches']==count
    with sqlite3.connect(str(plan.path)) as db:
        assert db.execute('PRAGMA integrity_check').fetchall()==[('ok',)]
        for name,pin,path,bindings in db.execute('SELECT instance,pin,clkip,bindings FROM branches ORDER BY seq'):
            i=name.split('.ip')[1]
            assert pin=='i' and path==name+'.i' and json.loads(bindings)['var_pll0']['value']=='scale.i'+i
    write_graph(out/'acceptance.json',dict(passed=True,synthetic=True,branches=count,excluded_gate_tips=count,elapsed_seconds=time.monotonic()-start,
        peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.,integrity_check='ok',export=export))
    print('SV_BRANCH_ENDPOINTS_SCALE_PASS',flush=True)


def gui(out,source,phase):
    from clocktree.gui import ClockTreeApp
    from clocktree.sv_view import SVDialog
    from check_workbench_gui import pump
    from check_indexed_gui import capture
    out.mkdir(parents=True,exist_ok=True);errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=True);app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *args:errors.append(''.join(traceback.format_exception(*args)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if phase=='gui-write':
        app.show_graph(read_graph(source/'tree/clocktree.json'));pump(app)
        app.ui_state.update(app.graph['config'],register_trace_directory=str(source/'registers'))
    if phase=='gui-upgrade':
        # Reproduce a pre-upgrade session in the test directory, including a stale export.
        settings=app.ui_state.get(app.graph['config'])
        old=out/'legacy-test-plan.sqlite'
        shutil.copy2(settings['sv_plan_path'],str(old))
        arch=graph_for_templates(app.graph,app.get_config(validate=False))
        arch['sv_source_revision']=app._graph_token
        signature=architecture_signature(arch,'sv-plan-v10')
        metadata=dict(SVPlan(old).source(),architecture_signature=signature)
        del metadata['endpoint_policy']
        with closing(sqlite3.connect(str(old))) as db:
            db.execute("UPDATE meta SET value=? WHERE key='source'",(json.dumps(metadata),))
            db.execute("UPDATE branches SET clkip='obsolete.parent_net'")
            db.commit()
        app.ui_state.update(app.graph['config'],sv_plan_path=str(old),
                            sv_export_plan_path=str(old),sv_export_signature=signature)
    dialog=SVDialog(app);pump(app)
    if phase=='gui-write':
        for key,editor in dialog.editors.items():editor.delete('1.0','end');editor.insert('1.0',TEMPLATES[key])
        dialog.analyze();pump(app,lambda:dialog.plan is not None and dialog.process.poll() is not None)
    elif phase=='gui-upgrade':
        pump(app,lambda:dialog.plan is not None and dialog.process is not None and dialog.process.poll() is not None)
        assert dialog.plan.path!=old and dialog.export_result is None
        assert all(r['clkip']==r['instance']+'.'+r['pin'] for r in dialog.rows.values())
        assert 'obsolete.parent_net' not in dialog.output_code.get('1.0','end')
        assert dialog.templates()==TEMPLATES
    else:assert dialog.plan and dialog.templates()==TEMPLATES
    assert dialog.plan.branches()[0]==4
    assert dialog.table.heading('pin','text')=='输入端口 / 位'
    row=next(r for r in dialog.rows.values() if r['instance']=='sv_ends.sub' and r['pin']=='c[2]')
    dialog.table.selection_set(row['id']);dialog.detail();pump(app)
    assert 'sv_ends.sub.c[2]' in dialog.preview.get('1.0','end') and '同一子系统' in dialog.preview.get('1.0','end')
    if phase=='gui-write':
        dialog.export(out/'gui-all.sv');pump(app,lambda:dialog.process.poll() is not None and dialog.export_button.instate(['!disabled']))
        assert json.loads(Path(dialog.export_result['mapping']).read_text())['block_count']==4
        capture(dialog,out/'sv-branch-endpoints.png')
    dialog.close();app._save_session_now();pump(app);assert not errors,errors
    write_graph(out/(phase+'.json'),dict(passed=True,errors=errors,branches=4,phase=phase))
    app.close()
    deadline=time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:app.update();time.sleep(.01)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    p.add_argument('--phase',choices=['native','scale','gui-write','gui-read','gui-upgrade'],default='native')
    p.add_argument('--source',type=Path);p.add_argument('--count',type=int,default=10000);a=p.parse_args()
    if a.phase=='native':native(a.out.resolve())
    elif a.phase=='scale':scale(a.out.resolve(),a.count)
    else:gui(a.out.resolve(),a.source.resolve(),a.phase)
