"""Native Verdi / VCS and Tk acceptance of user branch templates."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.storage import write_graph
from clocktree.register_trace import run as trace,seeds_from_graph
from clocktree.sv_templates import prepare
from clocktree.session import read_graph
from check_target_mode import run as extract


TEMPLATES={
 'with_mux':'initial begin\n  $display("MUX branch", $var_pllsel, $var_div, $var_swdone, $var_gt0, $var_gt1, $var_gt2, $var_pll0, $var_pll1, $var_pll2, $var_pll3, $var_clkip, $var_rst);\nend',
 'without_mux':'initial begin\n  $display("NO MUX branch", $var_div, $var_swdone, $var_gt0, $var_gt1, $var_gt2, $var_pll0, $var_pll1, $var_pll2, $var_pll3, $var_clkip, $var_rst);\nend'}
for key in TEMPLATES:
    TEMPLATES[key]+='\nproperty check_$var_clkip_sva;\n  @(posedge $var_clkip) disable iff (!$var_rst) 1\'b1 |-> ($var_gt0 || $var_gt1 || $var_gt2);\nendproperty\nassert property (check_$var_clkip_sva);'


def native(out):
    fixture=out/'fixture';fixture.mkdir(parents=True)
    rtl='''module ctl_mux(input a,b,input [7:4] select_i,output o);assign o=select_i[4]?a:b;endmodule
module ctl_div(input i,input [3:0] ratio_i,output o,done_o);assign o=i;assign done_o=i;endmodule
module clock_sink(input [3:2] clocks);endmodule
module sv_top(input clk,dft_mode,a,b,rst_n,rst_alt_n,rst_upstream_n,input [15:0] data);
reg [15:0] C,D,E;always @(posedge clk) begin C<=data;D<=data;end
wire cm,cg,cd,ng,nd,ng2,ng3,done0,done1;
ctl_mux m(.a(a),.b(b),.select_i(dft_mode?C[15:12]:D[5:2]),.o(cm));
ctl_gate g(.i(cm),.enable_i(C[7]),.o(cg));
ctl_div d(.i(cg),.ratio_i(D[9:6]),.o(cd),.done_o(done0));
ctl_gate z_first(.i(a),.enable_i(C[6]),.o(ng));
ctl_div d_other(.i(ng),.ratio_i(C[3:0]),.o(nd),.done_o(done1));
ctl_gate2 a_second(.i(nd),.enable_i(C[5]),.o(ng2));
ctl_gate3 m_third(.i(ng2),.enable_i(C[4]),.o(ng3));
clock_sink sink(.clocks({ng3,cd}));
always @(posedge clk) begin E[10]<=done0;E[11]<=done1;E[12]<=done1;end
endmodule
'''
    for name in ('ctl_gate','ctl_gate2','ctl_gate3'):
        rtl+='module '+name+'(input i,enable_i,output o);assign o=i;endmodule\n'
    (fixture/'design.sv').write_text(rtl);(fixture/'files.f').write_text(str(fixture/'design.sv')+'\n')
    build=out/'kdb';build.mkdir()
    with (build/'build.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','sv_top','-f',str(fixture/'files.f'),'-o','simv'],cwd=str(build),stdout=log,stderr=subprocess.STDOUT)
    rules=[{'module':'ctl_mux','kind':'mux','clock_inputs':['a','b'],'clock_output':'o','aux_ports':{'pll_sel':'select_i'}},
           {'module':'ctl_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o','aux_ports':{'gt':'enable_i'}},
           {'module':'ctl_div','kind':'div','clock_inputs':['i'],'clock_output':'o','aux_ports':{'div':'ratio_i','sw_done':'done_o'}},
           {'module':'ctl_gate2','kind':'gate','clock_inputs':['i'],'clock_output':'o','aux_ports':{'gt':'enable_i'}},
           {'module':'ctl_gate3','kind':'gate','clock_inputs':['i'],'clock_output':'o','aux_ports':{'gt':'enable_i'}},
           {'module':'clock_sink','kind':'subsystem','clock_inputs':['clocks']}]
    cfg={'design':{'top':'sv_top','elab':str(build/'simv.daidir/kdb.elab++')},'memory_limit_gib':4,
         'layers':[{'index':i+1,'hierarchy':'sv_top','reset_signal':'rst_n' if i==0 else 'rst_alt_n' if i==1 else 'rst_upstream_n','modules':[rule]} for i,rule in enumerate(rules)]}
    graph,tree_report=extract(cfg,out/'tree');assert graph['summary']['connectivity_complete']
    request={'config':graph['config'],'seeds':list(seeds_from_graph(graph))};write_graph(out/'register-request.json',request)
    result=trace(out/'register-request.json',out/'registers');assert result['summary']['complete']
    plan=prepare(graph,out/'registers',out/'plan');total,branches=plan.branches();assert total==2
    mux=next(b for b in branches if b['mode']=='with_mux');other=next(b for b in branches if b['mode']=='without_mux')
    first=plan.resolve(mux,TEMPLATES);assert first['ready'],first['issues']
    assert first['values']=={'var_rst':'sv_top.rst_n','var_clkip':'sv_top.cd','var_pllsel':'sv_top.D[5:2]','var_div':'sv_top.D[9:6]','var_swdone':'sv_top.done0',
                            'var_gt0':'sv_top.C[7]','var_gt1':"1'b1",'var_gt2':"1'b1",'var_pll0':'sv_top.a','var_pll1':'sv_top.b','var_pll2':"1'b1",'var_pll3':"1'b1"},first['values']
    second=plan.resolve(other,TEMPLATES);assert not second['ready'] and any('多组' in p for p in second['issues'])
    assert [second['values']['var_gt'+str(i)] for i in range(3)]==['sv_top.C[6]','sv_top.C[5]','sv_top.C[4]']
    assert second['values']['var_pll0']=='sv_top.a' and second['values']['var_clkip']=='sv_top.ng3'
    assert first['reset']['layer']==1 and first['reset']['instance']=='sv_top.m'
    assert second['values']['var_rst']=='sv_top.rst_alt_n' and second['reset']['layer']==2 and second['reset']['instance']=='sv_top.z_first'
    output=out/'all-branches.sv';partial=plan.export(TEMPLATES,output);assert partial['blocks']==partial['skipped']==1
    write_graph(out/'partial-report.json',json.loads((out/'all-branches.sv.mapping.json').read_text()))
    choices=plan.candidates(other,'var_swdone')
    assert len(choices)==2 and {c['expression'] for c in choices}=={'sv_top.done1'}
    assert {c['capture_expression'] for c in choices}=={'sv_top.E[11]','sv_top.E[12]'}
    selected=next(c for c in choices if c['capture_expression']=='sv_top.E[11]')
    selections={other['id']:{'var_swdone':selected['id']}};complete=plan.export(TEMPLATES,output,selections)
    assert complete['blocks']==2 and complete['skipped']==0
    content=output.read_text();assert content.count('// CT7 branch ')==2 and content.count('$display')==2 and '$var_' not in content
    write_graph(out/'templates.json',dict(TEMPLATES,selections=selections))
    (fixture/'template_checks.sv').write_text('module template_checks;\n'+content+'\nendmodule\n')
    compiled=out/'compile';compiled.mkdir()
    with (compiled/'compile.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','sv_top','-top','template_checks',str(fixture/'design.sv'),str(fixture/'template_checks.sv'),'-o','simv'],cwd=str(compiled),stdout=log,stderr=subprocess.STDOUT)
    write_graph(out/'acceptance.json',{'passed':True,'kdb':True,'sv_vcs_compiled':True,'terminal_inputs':2,'initial_generated':1,'initial_skipped':1,
                'final_generated':2,'gate_order':['sv_top.C[6]','sv_top.C[5]','sv_top.C[4]'],'missing_literal':"1'b1",'mux_values':first['values'],'no_mux_values':plan.resolve(other,TEMPLATES,selections[other['id']])['values']})
    print('SV_NATIVE_ACCEPTED',flush=True)


def gui(out,phase,screenshots):
    from clocktree.gui import ClockTreeApp
    from clocktree.sv_view import SVDialog,CandidateDialog
    from check_workbench_gui import pump
    from check_indexed_gui import capture
    app=ClockTreeApp(runs=out/'gui-runs',session_dir=out/'gui-session',remember=True);app.geometry('1840x1000+0+0');errors=[]
    app.report_callback_exception=lambda *args:errors.append(''.join(traceback.format_exception(*args)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error));pump(app,lambda:not app.busy)
    if phase=='gui-write':
        app.show_graph(read_graph(out/'tree/clocktree.json'));pump(app)
        app.select_layer(1);assert app.layer_reset_var.get()=='rst_n'
        app.layer_reset_var.set('rst_alt_n');app.select_layer(2);assert app.layer_reset_var.get()=='rst_alt_n'
        app.layer_reset_var.set('rst_n');app.select_layer(6);assert app.layer_reset_var.get()=='rst_upstream_n'
        app.add_layer();assert app.layer_reset_var.get()==''
        app.layer_reset_var.set('temporary_rst');app.remove_layer();assert app.active_layer==6 and app.layer_reset_var.get()=='rst_upstream_n'
        app.select_layer(1);assert app.layer_reset_var.get()=='rst_alt_n'
        app.layer_reset_var.set('rst_alt_n');assert not app._config_dirty
        app.switch_tree_type('reset');pump(app);app.select_layer(2);app.layer_reset_var.set('reset_workspace_n')
        app.switch_tree_type('clock');pump(app);assert app.active_layer==1 and app.layer_reset_var.get()=='rst_alt_n'
        write_graph(out/'gui-reset-config.json',app.get_config())
        from clocktree.config import load_config
        assert load_config(out/'gui-reset-config.json')['layers'][0]['reset_signal']=='rst_alt_n'
        assert app.graph['config']['layers'][0]['reset_signal']=='rst_n'  # original evidence stays immutable
        app.settings_canvas.yview_moveto(1.0);pump(app)
        if screenshots:capture(app,out/'layer-reset-input.png')
        app.ui_state.update(app.graph['config'],register_trace_directory=str(out/'registers'))
        dialog=SVDialog(app)
        for key,editor in dialog.editors.items():editor.delete('1.0','end');editor.insert('1.0',TEMPLATES[key])
        for number,key in enumerate(('with_mux','without_mux')):
            dialog.tabs.select(number);pane=dialog.code_editors[key];pump(app,lambda:not pane.highlight_busy)
            before=dialog.templates();pane.text.mark_set('insert','1.0');pane.variable_buttons['var_pll0'].invoke();pump(app)
            assert dialog.templates()[key]=='$var_pll0'+before[key]
            other='without_mux' if key=='with_mux' else 'with_mux';assert dialog.templates()[other]==before[other]
            pane.undo();pump(app);assert dialog.templates()==before
        dialog.selections={};dialog.analyze();pump(app,lambda:dialog.process is not None and dialog.process.poll() is not None and dialog.analyze_button.instate(['!disabled']))
        assert dialog.plan is not None,dialog.status.get()
        assert dialog.plan.branches()[0]==2
        dialog.export(out/'gui-generated.sv');pump(app,lambda:dialog.process.poll() is not None and dialog.export_button.instate(['!disabled']))
        assert json.loads(Path(dialog.export_result['mapping']).read_text())['block_count']==1
        other=next(r for r in dialog.rows.values() if r['mode']=='without_mux');dialog.table.selection_set(other['id']);dialog.detail()
        dialog.pick('var_swdone');picker=next(w for w in dialog.winfo_children() if isinstance(w,CandidateDialog));pump(app)
        assert len(picker.items)==2
        if screenshots:capture(picker,out/'sv-swdone-candidates.png')
        picker.search.set('sv_top.E[11]');picker.refresh(True);pump(app);assert len(picker.items)==1
        item=next(k for k,r in picker.items.items() if r.get('capture_expression',r['expression'])=='sv_top.E[11]');picker.table.selection_set(item);picker.choose();pump(app)
        dialog.export(out/'gui-generated.sv');pump(app,lambda:dialog.process.poll() is not None and dialog.export_button.instate(['!disabled']))
        assert json.loads(Path(dialog.export_result['mapping']).read_text())['block_count']==2
        content=(out/'gui-generated.sv').read_text()
        assert content.count('sv_top.rst_alt_n')==TEMPLATES['with_mux'].count('$var_rst') and content.count('sv_top.rst_n')==TEMPLATES['without_mux'].count('$var_rst') and 'sv_top.rst_upstream_n' not in content
        dialog.table.selection_set(other['id']);dialog.detail();pump(app)
        assert dialog.export_button.winfo_y()+dialog.export_button.winfo_height()<=dialog.export_button.master.winfo_height()
        assert dialog.export_button.winfo_rooty()+dialog.export_button.winfo_height()<=dialog.winfo_rooty()+dialog.winfo_height()
        if screenshots:capture(dialog,out/'sv-template-workbench.png')
        dialog.tabs.select(0);pump(app,lambda:not dialog.code_editors['with_mux'].highlight_busy)
        if screenshots:capture(dialog,out/'sv-template-code-editor.png')
        dialog.geometry('1050x700+0+0');pump(app)
        for button in dialog.code_editors['with_mux'].variable_buttons.values():
            assert button.winfo_rootx()+button.winfo_width()<=dialog.winfo_rootx()+dialog.winfo_width()
        assert dialog.code_editors['with_mux'].text.winfo_height()>150
        assert dialog.export_button.winfo_rooty()+dialog.export_button.winfo_height()<=dialog.winfo_rooty()+dialog.winfo_height()
        dialog.close();app._save_session_now();pump(app)
    else:
        assert app.active_layer==1 and app.layer_reset_var.get()=='rst_alt_n'
        assert app._workspaces['reset']['config']['layers'][1]['reset_signal']=='reset_workspace_n'
        dialog=SVDialog(app);pump(app);assert dialog.plan and dialog.templates()==TEMPLATES and dialog.selections
        assert all(dialog.plan.resolve(r,TEMPLATES,dialog.selections.get(r['id']))['ready'] for r in dialog.rows.values())
        for row in dialog.rows.values():
            expected='sv_top.rst_alt_n' if row['mode']=='with_mux' else 'sv_top.rst_n'
            assert dialog.plan.resolve(row,TEMPLATES,dialog.selections.get(row['id']))['values']['var_rst']==expected
        dialog.close()
    assert not errors,errors;write_graph(out/(phase+'.json'),{'passed':True,'errors':errors,'layer_reset_config':True,'post_extraction_edit':True,'reset_values':{'with_mux':'sv_top.rst_alt_n','without_mux':'sv_top.rst_n'},'reset_basis':'branch_origin'});app.close()
    deadline=time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:app.update();time.sleep(.01)


def scale(out,native_source,count):
    """Synthetic terminal pressure; no synthetic node is submitted to Verdi."""
    import resource
    import signal
    graph=dict(read_graph(native_source/'tree/clocktree.json'))
    source_nodes=list(graph['nodes']);source_edges=list(graph['edges'])
    sink=next(n for n in source_nodes if n['id']=='sv_top.sink')
    nodes=[n for n in source_nodes if n['id']!=sink['id']]
    edges=[e for e in source_edges if e['target']!=sink['id']]
    end_edges={e['target_pin']:e for e in source_edges if e['target']==sink['id']}
    for i in range(count):
        pin='clocks[{}]'.format(2+i%2);node=copy.deepcopy(sink);node['id']='sv_top.sink_{}'.format(i)
        node['inputs']=[pin];node['port_connections']={pin:{'status':'connected','coverage':'bit','full_name':'sv_top.cd' if i%2==0 else 'sv_top.ng3'}}
        edge=dict(end_edges[pin],target=node['id']);nodes.append(node);edges.append(edge)
    graph['nodes']=nodes;graph['edges']=edges
    start=time.monotonic();plan=prepare(graph,native_source/'registers',out/'plan',False);planned=time.monotonic()-start
    start=time.monotonic();result=plan.export(TEMPLATES,out/'scale.sv');elapsed=time.monotonic()-start
    assert result['terminals']==count and result['blocks']==(count+1)//2 and result['skipped']==count//2,result
    total,page=plan.branches('sink_',offset=count-100);assert total==count and len(page)==100
    write_graph(out/'templates.json',TEMPLATES)
    canceled=out/'canceled.sv';canceled.write_text('preserve prior file')
    with (out/'cancel.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-m','clocktree','sv-export','--plan',str(plan.path),'--templates',str(out/'templates.json'),'--out',str(canceled)],stdout=log,stderr=subprocess.STDOUT)
        deadline=time.monotonic()+30
        while process.poll() is None and not list(out.glob('canceled.sv.*.tmp')) and time.monotonic()<deadline:time.sleep(.01)
        assert process.poll() is None,'export finished before cancellation test'
        process.send_signal(signal.SIGTERM);assert process.wait(timeout=30)!=0
    assert canceled.read_text()=='preserve prior file' and not list(out.glob('canceled.sv.*.tmp*'))
    write_graph(out/'acceptance.json',dict(passed=True,synthetic=True,nodes=len(nodes),terminal_inputs=count,
        plan_seconds=planned,export_seconds=elapsed,peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.,
        generated=result['blocks'],skipped=result['skipped'],last_page=len(page),cancellation_preserves_prior_file=True))
    print('SV_SCALE_ACCEPTED',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--phase',choices=('native','gui-write','gui-read','scale'),default='native');p.add_argument('--no-screenshots',action='store_true')
    p.add_argument('--native-source',type=Path);p.add_argument('--terminals',type=int,default=10000)
    a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
    if a.phase=='native':native(out)
    elif a.phase=='scale':scale(out,a.native_source.resolve(),a.terminals)
    else:gui(out,a.phase,not a.no_screenshots)
