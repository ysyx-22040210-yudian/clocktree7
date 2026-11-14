"""Native L1 interface entry validation; ports must retain formal bit identity."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import copy
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from check_target_mode import run
from clocktree.storage import write_graph


def gate_entries(out):
    """An L1 gate can reach a proven input before finding external logic."""
    out.mkdir(parents=True, exist_ok=True)
    rtl = out/'rtl.sv'
    rtl.write_text('''
module l1_gate(input i,en,output o); assign o=i&en; endmodule
module l1_source(input i,output o); assign o=~i; endmodule
module l1_osc(input refclk,output reg o);
 always @(posedge refclk) o <= ~o;
endmodule
module region(input raw, external_clk, tracked, open_clk,
              input [0:3] clocks, input [5:5] single_bit,
              input en, output [7:0] q);
 wire raw_alias;
 assign raw_alias=raw;
 l1_gate g_raw(.i(raw_alias),.en(en),.o(q[0]));
 l1_gate g_external(.i(external_clk),.en(en),.o(q[1]));
 l1_gate g_external2(.i(external_clk),.en(en),.o(q[2]));
 l1_gate g_tracked(.i(tracked),.en(en),.o(q[3]));
 l1_gate g_open(.i(open_clk),.en(en),.o(q[4]));
 l1_gate g_bit(.i(clocks[2]),.en(en),.o(q[5]));
 l1_gate g_one(.i(single_bit),.en(en),.o(q[6]));
 l1_gate g_internal(.i(en ? raw : clocks[2]),.en(en),.o(q[7]));
endmodule
module l1_gate_top(input [3:0] clk,input en,rst_n,output [7:0] q);
 wire generated_clk, tracked_clk;
 l1_osc outside_model(.refclk(clk[0]),.o(generated_clk));
 l1_source source(.i(clk[1]),.o(tracked_clk));
 region crg(.raw(clk[0]),.external_clk(generated_clk),.tracked(tracked_clk),
            .open_clk(),.clocks({clk[3],clk[0],clk[2],clk[1]}),.single_bit(clk[3]),.en(en),.q(q));
endmodule
''')
    kdb = out/'compile'
    kdb.mkdir()
    with (kdb/'vcs.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all',
            '-top','l1_gate_top',str(rtl)], cwd=str(kdb), stdout=log, stderr=subprocess.STDOUT)
    scope = 'l1_gate_top.crg'
    config = {'design': {'top':'l1_gate_top','elab':str(kdb/'simv.daidir/kdb.elab++')},
              'layers':[{'index':1,'hierarchy':scope,'reset_signal':'l1_gate_top.rst_n','modules':[
                  {'module':'l1_gate','kind':'sync_gate','clock_inputs':['i'],'clock_output':'o'}]},
                  {'index':2,'hierarchy':'l1_gate_top','modules':[
                  {'module':'l1_source','kind':'occ_root','clock_inputs':['i'],'clock_output':'o'}]}]}
    reports = []
    for mode in ('target', 'full'):
        graph, report = run(dict(config, extraction_mode=mode), out/mode)
        expected = {'g_raw':'raw','g_external':'external_clk','g_external2':'external_clk',
                    'g_bit':'clocks[2]','g_one':'single_bit[5]'}
        entries = {n['id'].rsplit('.',1)[-1]: n['l1_inputs']['i']['port'] for n in graph['nodes'] if n.get('l1_inputs')}
        assert entries == expected, (mode, entries, list(graph['boundaries']))
        assert {(e['source'],e['target']) for e in graph['edges']} == {('l1_gate_top.source',scope+'.g_tracked')}
        assert any(b['instance']==scope+'.g_external' and b['status']=='unknown_cell' for b in graph['boundaries'])
        assert not graph['summary']['connectivity_complete']
        nodes = {n['id']:n for n in graph['nodes']}
        assert nodes[scope+'.g_external']['component'] == nodes[scope+'.g_external2']['component']
        from clocktree.indexed_view import IndexedGraphView
        view = IndexedGraphView(graph)
        view.project('instances')
        assert len(view.l1_nodes) == 4 and len(view.l1_edges) == 5
        assert scope+'.external_clk' in view.svg()
        reports.append(dict(mode=mode, entries=entries, **report))

    # Also verify complete branches and SV binding with L1 as the trace root.
    from clocktree.sv_templates import prepare
    bounded = copy.deepcopy(config)
    bounded['trace_scope'] = scope
    bounded['layers'][1].update(hierarchy=scope, modules=[])
    clean_rtl = out/'bounded.sv'
    clean_rtl.write_text(rtl.read_text().replace('en ? raw : clocks[2]', 'raw')
                         .replace('.open_clk()', '.open_clk(clk[2])'))
    filelist = out/'bounded.f'
    filelist.write_text(str(clean_rtl)+'\n')
    bounded['design'] = {'top':'l1_gate_top','filelist':str(filelist)}
    clean, report = run(bounded, out/'bounded')
    assert clean['summary']['connectivity_complete'], list(clean['boundaries'])
    plan = prepare(clean, None, out/'sv', False)
    templates = {'without_mux':'wire check_$var_clkip = $var_pll0;\n'}
    for key, port in [('g_raw','raw'),('g_external','external_clk'),('g_one','single_bit[5]')]:
        row = next(row for row in plan.branches()[1] if row['instance']==scope+'.'+key)
        result = plan.resolve(row, templates)
        assert result['ready'] and result['values']['var_pll0']==scope+'.'+port, result
    generated = plan.export(templates, out/'sv'/'branches.sv')
    assert generated['blocks'] == 8 and generated['skipped'] == 0, generated
    checks = out/'sv'/'checks.sv'
    checks.write_text('module l1_gate_checks;\n' + (out/'sv'/'branches.sv').read_text() + '\nendmodule\n')
    with (out/'sv'/'vcs.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all',
            '-top','l1_gate_top','-top','l1_gate_checks',str(clean_rtl),str(checks)],
            cwd=str(out/'sv'), stdout=log, stderr=subprocess.STDOUT)
    write_graph(out/'acceptance.json', {'passed':True,'cases':reports,'bounded':report,'sv':generated,
                                       'vcs_sv_elaborated':True})


def fixture(out, mode='target'):
    out.mkdir(parents=True, exist_ok=True)
    (out/'rtl.sv').write_text('''
module source1(input i,output o);assign o=i;endmodule
module gate1(input i,en,output o);assign o=i&en;endmodule
module sink1(input c0,c1,c2);endmodule
module domain(input [5:2] raw,input en,output normal_clk,direct_clk);
 wire selected,mid,final_clk;
 source1 m(.i(raw[5]),.o(selected));
 gate1 gn(.i(selected),.en(en),.o(normal_clk));
 gate1 ga(.i(raw[3]),.en(en),.o(mid));
 gate1 gb(.i(mid),.en(en),.o(final_clk));
 gate1 gd(.i(raw[4]),.en(en),.o(direct_clk));
 sink1 s(.c0(raw[2]),.c1(final_clk),.c2(selected));
endmodule
module l1_top(input [7:4] pads,input en,output normal_clk,direct_clk);
 domain crg(.raw({pads[4],pads[6],pads[5],pads[7]}),.en(en),.normal_clk(normal_clk),.direct_clk(direct_clk));
endmodule
''')
    (out/'rtl.f').write_text(str(out/'rtl.sv')+'\n')
    return {'design':{'top':'l1_top','filelist':str(out/'rtl.f')},'extraction_mode':mode,
        'layers':[{'index':1,'hierarchy':'l1_top.crg','reset_signal':'rst_n','modules':[
            {'module':'source1','kind':'mux','clock_inputs':['i'],'clock_output':'o'}]},
            {'index':2,'hierarchy':'l1_top.crg','reset_signal':'rst2_n','modules':[
                {'module':'gate1','kind':'gate','clock_inputs':['i'],'clock_output':'o'},
                {'module':'sink1','kind':'subsystem','clock_inputs':['c0','c1','c2']}]}]}


def matrix(out):
    reports=[]
    for mode in ('target','full'):
        config=fixture(out/('fixture-'+mode),mode)
        graph,report=run(config,out/mode)
        nodes={n['id']:n for n in graph['nodes']}
        expected={'m':('i','raw[5]'),'ga':('i','raw[3]'),'gd':('i','raw[4]'),'s':('c0','raw[2]')}
        for inst,(pin,port) in expected.items():
            value=nodes['l1_top.crg.'+inst].get('l1_inputs',{}).get(pin)
            assert value and value['signal']=='l1_top.crg.'+port,(mode,inst,pin,value,list(graph['boundaries']))
        assert not nodes['l1_top.crg.gb'].get('l1_inputs') and not nodes['l1_top.crg.gn'].get('l1_inputs')
        assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid'],graph['summary']
        report['sv']=sv(out/('sv-'+mode),graph)
        reports.append(dict(mode=mode,**report))
    for mode in ('target','full'):
        config=fixture(out/('reset-fixture-'+mode),mode);config['tree_type']='reset'
        for layer in config['layers']:
            for rule in layer['modules']:
                rule['reset_inputs']=rule.pop('clock_inputs')
                if 'clock_output' in rule:rule['reset_output']=rule.pop('clock_output')
                if rule['kind']=='gate':rule['kind']='rsy_sync'
        graph,report=run(config,out/('reset-'+mode))
        assert graph['summary']['l1_input_connections']==4 and graph['summary']['connectivity_complete']
        reports.append(dict(mode='reset-'+mode,**report))
    config=fixture(out/'negative-fixture')
    path=out/'negative-fixture/rtl.sv'
    path.write_text(path.read_text().replace('.i(raw[3])','.i(en ? raw[3] : raw[2])').replace('.c0(raw[2])','.c0()'))
    graph,report=run(config,out/'negative')
    nodes={n['id']:n for n in graph['nodes']}
    assert not nodes['l1_top.crg.ga'].get('l1_inputs') and not nodes['l1_top.crg.s'].get('l1_inputs')
    assert not graph['summary']['connectivity_complete']
    reports.append(dict(mode='negative',**report))
    write_graph(out/'acceptance.json',{'passed':True,'cases':reports})


def stress(out,count):
    out.mkdir(parents=True,exist_ok=True)
    (out/'rtl.sv').write_text('module gate1(input i,output o);assign o=i;endmodule\n'+
        'module l1_scale(input [%d:0] clocks,output [%d:0] outs);\n'%(count-1,count-1)+
        '\n'.join('gate1 g%d(.i(clocks[%d]),.o(outs[%d]));'%(i,i,i) for i in range(count))+'\nendmodule\n')
    (out/'rtl.f').write_text(str(out/'rtl.sv')+'\n')
    config={'design':{'top':'l1_scale','filelist':str(out/'rtl.f')},'layers':[
        {'index':1,'hierarchy':'l1_scale','modules':[]},
        {'index':2,'hierarchy':'l1_scale','modules':[{'module':'gate1','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]}]}
    graph,report=run(config,out/'run')
    assert graph['summary']['l1_input_connections']==count and graph['summary']['instances']==count and graph['summary']['connectivity_complete']
    from clocktree.sv_templates import prepare
    plan=prepare(graph,None,out/'sv')
    assert plan.branches(limit=0)[0]==count
    for offset in range(0,count,100):
        for row in plan.branches(offset=offset)[1]:
            index=int(row['instance'].rsplit('.g',1)[1]);values=json.loads(row['bindings'])
            assert values['var_pll0']['value']=='l1_scale.clocks[{}]'.format(index)
            assert values['var_clkip']['value']=='l1_scale.outs[{}]'.format(index)
    export=plan.export({'without_mux':'// $var_pll0 -> $var_clkip : $var_div $var_gt0\n'},out/'all.sv')
    assert export['blocks']==count and export['skipped']==0
    write_graph(out/'acceptance.json',dict(report,passed=True,export=export))


def sv(out,graph):
    from clocktree.sv_templates import prepare
    plan=prepare(graph,None,out)
    template='// $var_pllsel $var_div $var_swdone $var_gt0 $var_gt1 $var_gt2 $var_pll0 $var_pll1 $var_pll2 $var_pll3 $var_clkip\n'
    templates={'without_mux':template,'with_mux':'// normal: $var_clkip\n'}
    results=[plan.resolve(row,templates) for row in plan.branches()[1]]
    assert len(results)==5 and all(r['ready'] for r in results),results
    expected={('gd','o'):('raw[4]','direct_clk'),('s','c0'):('raw[2]','raw[2]'),('s','c1'):('raw[3]','final_clk')}
    for (inst,pin),(pll,clk) in expected.items():
        row=next(r for r in results if r['instance']=='l1_top.crg.'+inst and r['pin']==pin)
        assert row['values']['var_pll0']=='l1_top.crg.'+pll,(row,expected)
        assert row['values']['var_clkip']=='l1_top.crg.'+clk,(row,expected)
        assert row['values']['var_div']=='0' and row['mode']=='without_mux'
        assert all(row['values'][v]=="1'b1" for v in ('var_pllsel','var_swdone','var_gt0','var_gt1','var_gt2','var_pll1','var_pll2','var_pll3'))
    result=plan.export(templates,out/'all.sv');assert result['blocks']==5 and result['skipped']==0
    return result


def gui(out,phase,result):
    from clocktree.gui import ClockTreeApp
    from clocktree.session import read_graph
    from clocktree.branch_view import BranchDialog
    from clocktree.sv_view import SVDialog
    from check_workbench_gui import pump
    from check_indexed_gui import capture
    out.mkdir(parents=True,exist_ok=True);errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=True);app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if phase=='gui-write':app.show_graph(read_graph(result));pump(app)
    model=app.diagram.model
    assert model and len(model.l1_nodes)==4 and model.actual_count==6
    node=next(n for n in model.l1_nodes.values() if n['l1_entry']['port']=='raw[4]')
    app.diagram.locate(node);pump(app)
    assert 'l1_top.crg.raw[4]' in app.prop_text.get('1.0','end')
    edge=next(e for e in model.l1_edges.values() if e['source']==node['id'])
    app.inspect_edges([edge]);pump(app);assert 'l1_top.crg.raw[4]' in app.evidence_text.get('1.0','end')
    app.raw_evidence_var.set(True);app.toggle_raw_evidence();pump(app)
    app.diagram.locate_edge(edge,'downstream');pump(app)
    assert app.selected_object['id']=='l1_top.crg.gd'
    from clocktree.navigation import jump_neighbour
    jump_neighbour(app,app.selected_object,'upstream');pump(app)
    assert app.selected_object['kind']=='l1_input'
    jump_neighbour(app,app.selected_object,'downstream');pump(app)
    assert app.selected_object['id']=='l1_top.crg.gd'
    app.diagram.trace('branch');pump(app)
    assert edge['id'] in app.diagram.scene.highlighted
    app.diagram.clear_highlight()
    window=BranchDialog(app,model,model.original['l1_top.crg.gb'])
    pump(app,lambda:window.graph is not None and not window.diagram.layout_pending)
    assert any(n['l1_entry']['signal']=='l1_top.crg.raw[3]' for n in window.diagram.model.l1_nodes.values())
    window.close();pump(app)
    if phase=='gui-write':
        dialog=SVDialog(app);pump(app);dialog.registers.set('')
        dialog.editors['without_mux'].insert('1.0','// $var_pll0 -> $var_clkip / $var_div\n')
        dialog.editors['with_mux'].insert('1.0','// normal $var_clkip\n')
        dialog.analyze();pump(app,lambda:dialog.plan is not None and dialog.process.poll() is not None)
        assert dialog.plan.branches()[0]==5
        dialog.export(out/'gui.sv');pump(app,lambda:dialog.process.poll() is not None)
        assert (out/'gui.sv').exists();dialog.close();pump(app)
        app.diagram.locate(node);pump(app);capture(app,out/'l1-input-workbench.png')
        app.export_svg(out/'l1-input.svg');app._save_session_now();pump(app)
    assert not errors,errors
    write_graph(out/(phase+'.json'),{'passed':True,'callback_errors':errors,'tk':app.tk.call('info','patchlevel')})
    app.close();deadline=time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:app.update();time.sleep(.01)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True);parser.add_argument('--phase',default='matrix');parser.add_argument('--result');parser.add_argument('--count',type=int,default=1000)
    args=parser.parse_args()
    if args.phase.startswith('gui-'):gui(Path(args.out).resolve(),args.phase,args.result)
    elif args.phase=='stress':stress(Path(args.out).resolve(),args.count)
    elif args.phase=='l1-gates':gate_entries(Path(args.out).resolve())
    else:matrix(Path(args.out).resolve())
