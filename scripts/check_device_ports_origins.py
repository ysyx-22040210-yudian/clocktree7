"""Verify configurable ports, later-layer origins and DIV source labels in native Verdi/Tk."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.register_trace import RegisterIndex, run as trace_registers, seeds_from_graph
from clocktree.storage import write_graph
from clocktree.sv_templates import graph_for_templates, prepare
from check_target_mode import run as extract


SCOPES = ['origin_top.ctrl_a', 'origin_top.ctrl_b']
TEMPLATES = {
    'with_mux': 'wire [2:0] check_$var_clkip_ratio = $var_div;\n'
                'wire check_$var_clkip_en = $var_pllsel & $var_gt0 & $var_gt1 & $var_rst;\n',
    'without_mux': 'wire check_$var_clkip_clk = $var_clkip;\n',
}
RTL = '''
module check_mux(input a, b, sel, output o, debug_o);
  assign o = sel ? b : a;
  assign debug_o = b;
endmodule
module check_occ_root(input i, output o); assign o = i; endmodule
module check_occ_branch(input i, output o); assign o = i; endmodule
module check_sync_gate(input i, en, output o); assign o = i & en; endmodule
module check_gate_wrap(input i, en, output o); assign o = i & en; endmodule
module check_div(input i, input [2:0] ratio, output o, done);
  assign o = i;
  assign done = ratio[0];
endmodule
module check_ctrl(input clk, data, output q);
  reg Q;
  always @(posedge clk) Q <= data;
  assign q = Q;
endmodule
module check_probe #(parameter W=2)(input [W+1:2] lanes, input test,
                                     output [W+1:2] pass, output extra);
  assign pass = lanes;
  assign extra = test;
endmodule
module check_region(input clk_in, raw_in, en_a, en_b, input [2:0] rate,
                    output clk_out, raw_out);
  wire c0, c1, c2, c3;
  check_occ_root root_i(.i(clk_in), .o(c0));
  check_sync_gate sync_i(.i(c0), .en(en_a), .o(c1));
  check_gate_wrap wrap_i(.i(c1), .en(en_b), .o(c2));
  check_div div_i(.i(c2), .ratio(rate), .o(c3), .done());
  check_occ_branch end_i(.i(c3), .o(clk_out));
  check_occ_root raw_i(.i(raw_in), .o(raw_out));
endmodule
module origin_top(input [3:0] pads, input cfg_clk, rst_n,
                  input [2:0] data, output out_clk, raw_clk, iso_clk);
  wire qa, qb, qc, selected;
  check_ctrl ctrl_a(.clk(cfg_clk), .data(data[0]), .q(qa));
  check_ctrl ctrl_b(.clk(cfg_clk), .data(data[1]), .q(qb));
  check_ctrl ctrl_a_extra(.clk(cfg_clk), .data(data[2]), .q(qc));
  check_mux m(.a(pads[0]), .b(pads[1]), .sel(qa), .o(selected), .debug_o());
  check_region region(.clk_in(selected), .raw_in(pads[2]), .en_a(qa), .en_b(qb),
                      .rate({qc,qb,qa}), .clk_out(out_clk), .raw_out(raw_clk));
  check_occ_branch iso(.i(pads[3]), .o(iso_clk));
  check_probe #(.W(2)) probe2(.lanes(pads[1:0]), .test(qa), .pass(), .extra());
  check_probe #(.W(3)) probe3(.lanes(pads[2:0]), .test(qb), .pass(), .extra());
endmodule
'''


def fixture(out):
    rtl = out/'design.sv'
    rtl.write_text(RTL, encoding='utf-8')
    build = out/'kdb'
    build.mkdir()
    with (build/'build.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all',
                               '-top','origin_top',str(rtl)], cwd=str(build), stdout=log, stderr=subprocess.STDOUT)

    def rule(module, kind, inputs=('i',), auxiliary=None):
        result = {'module': 'check_'+module, 'kind': kind, 'clock_inputs': list(inputs), 'clock_output': 'o'}
        if auxiliary:
            result['aux_ports'] = auxiliary
        return result

    config = {'tree_type':'clock', 'design': {'top':'origin_top','elab':str(build/'simv.daidir/kdb.elab++')},
              'layers': [
                  {'index':1, 'hierarchy':'origin_top.region', 'modules':[]},
                  {'index':2, 'hierarchy':'origin_top', 'reset_signal':'rst_n', 'modules':[
                      rule('mux','mux',('a','b'),{'pll_sel':'sel'}), rule('occ_branch','occ_branch')]},
                  {'index':3, 'hierarchy':'origin_top.region', 'reset_signal':'origin_top.rst_n', 'modules':[
                      rule('occ_root','occ_root'), rule('sync_gate','sync_gate',auxiliary={'gt':'en'}),
                      rule('gate_wrap','gate_wrap',auxiliary={'gt':'en'}), rule('div','div',auxiliary={'div':'ratio'}),
                      rule('occ_branch','occ_branch')]},
              ]}
    return config, rtl


def native(out):
    config, rtl = fixture(out)
    cases = []
    for mode in ('target', 'full'):
        graph, _ = extract(dict(config, extraction_mode=mode), out/mode)
        nodes = {n['id']: n for n in graph['nodes']}
        assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid'], graph['summary']
        chain = ['origin_top.m'] + ['origin_top.region.'+name for name in ('root_i','sync_i','wrap_i','div_i','end_i')]
        assert {(e['source'],e['target']) for e in graph['edges']} == set(zip(chain,chain[1:]))
        roots = [n for n in nodes.values() if n['root']]
        assert len(roots) == 3 and all(n['layer'] > 1 for n in roots)
        assert not nodes['origin_top.region.root_i'].get('l1_inputs')
        assert nodes['origin_top.region.raw_i']['l1_inputs']['i']['signal'] == 'origin_top.region.raw_in'
        cases.append({'mode':mode,'instances':len(nodes),'edges':len(graph['edges']),
                      'roots':{n['id']:n['layer'] for n in roots}})
        if mode == 'target':
            target = graph
    seeds = list(seeds_from_graph(target))
    assert [s['kind'] for s in seeds if s['role']=='gt'] == ['sync_gate','gate_wrap']
    request = out/'register-request.json'
    write_graph(request, {'config':target['config'],'seeds':seeds})
    registers = out/'registers'
    summary = trace_registers(request, registers)['summary']
    assert summary['complete'], summary
    index = RegisterIndex(registers, SCOPES)
    div = [index.detail(row[0]) for row in index.query()[1] if row[2]=='div']
    assert [(d['endpoint'], d['div_source'].get('level')) for d in div] == [
        ('origin_top.ctrl_a.Q',1), ('origin_top.ctrl_b.Q',2), ('origin_top.ctrl_a_extra.Q',None),
    ], div
    index.export(out/'registers.csv')
    index.export_json(out/'registers.json')
    draft = dict(target['config'], div_source_scopes=SCOPES)
    plan = prepare(graph_for_templates(target,draft),registers,out/'plan')
    total, rows = plan.branches()
    assert total == 3
    mux = next(row for row in rows if row['instance']=='origin_top.region.end_i')
    assert mux['mode']=='with_mux'
    assert not plan.resolve(mux,TEMPLATES)['ready']
    choices = plan.candidates(mux,'var_div')
    assert len(choices) == 1
    selections = {mux['id']: {'var_div':choices[0]['id']}}
    mapped = plan.resolve(mux,TEMPLATES,selections[mux['id']])
    assert mapped['ready'], mapped
    assert mapped['values']['var_rst'] == 'origin_top.rst_n'
    assert mapped['values']['var_gt0'] == 'origin_top.ctrl_a.Q'
    assert mapped['values']['var_gt1'] == 'origin_top.ctrl_b.Q'
    assert [s.get('level') for s in mapped['selected']['var_div']['div_sources']] == [1,2,None]
    output = out/'branches.sv'
    result = plan.export(TEMPLATES,output,selections)
    assert result['blocks']==3 and result['skipped']==0
    checks = out/'checks.sv'
    checks.write_text('module origin_checks;\n'+output.read_text(encoding='utf-8')+'\nendmodule\n',encoding='utf-8')
    compile_dir = out/'compile'
    compile_dir.mkdir()
    with (compile_dir/'compile.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all',
                               '-top','origin_top','-top','origin_checks',str(rtl),str(checks)],
                              cwd=str(compile_dir),stdout=log,stderr=subprocess.STDOUT)
    report = {'passed':True,'real_kdb':True,'vcs_sv_elaborated':True,'cases':cases,
              'registers':summary,'div_sources':mapped['selected']['var_div']['div_sources'],
              'branches':result['blocks']}
    write_graph(out/'acceptance.json',report)
    print(json.dumps(report,ensure_ascii=False),flush=True)


def gui(out, native_dir, phase):
    from clocktree.gui import ClockTreeApp, RuleDialog
    from clocktree.input_view import InputPortDialog
    from clocktree.register_view import RegisterDialog
    from clocktree.session import read_graph
    from clocktree.sv_view import SVDialog
    from check_indexed_gui import capture
    from check_workbench_gui import pump

    errors = []
    app = ClockTreeApp(runs=out/'runs', session_dir=out/'session', remember=True)
    app.geometry('1840x1000+0+0')
    app.report_callback_exception = lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if phase == 'gui-read':
        assert app.graph and app.get_config()['div_source_scopes']==SCOPES
        dialog = RegisterDialog(app)
        assert dialog.index and list(dialog.index.div_scopes)==SCOPES
        dialog.close()
        write_graph(out/'gui-read.json',{'passed':True,'restored':True,'errors':errors})
        assert not errors,errors
        app.close()
        return

    graph = read_graph(native_dir/'target/clocktree.json')
    app.show_graph(graph);pump(app)
    capture(app,out/'new-device-models.png')
    for variable, path in zip(app.div_source_vars,SCOPES):
        variable.set(path)
    assert app.get_config()['div_source_scopes']==SCOPES

    def picker(editor, direction, pattern, names, read=True, role=None, screenshot=None):
        if role:
            dialog = editor.edit_ports(direction,role)
        else:
            editor.port_buttons[direction].invoke()
            dialog = next(child for child in editor.winfo_children() if isinstance(child,InputPortDialog))
        if read:
            dialog.read_button.invoke()
            pump(app, lambda:dialog.poll_id is None)
            assert dialog.rows,dialog.status.get()
            data = json.loads((dialog.directory/'input-ports.json').read_text(encoding='utf-8'))
            assert data['traced_ports']==0
        else:
            assert dialog.rows and dialog.process is None
        dialog.expression.set(pattern)
        dialog.filter()
        assert {row['direction'] for row in dialog.shown} == {direction}
        dialog.toggle_all(False)
        for name in names:
            row = next(str(i) for i,item in enumerate(dialog.shown) if item['name']==name)
            dialog.table.see(row);pump(app)
            x,y,w,h = dialog.table.bbox(row,'#0')
            dialog.table.event_generate('<ButtonRelease-1>',x=x+w//2,y=y+h//2)
            pump(app)
        selected_count = 1 if dialog.single and names else len(names)
        assert dialog.selection_summary.get().startswith('已选 {} 个'.format(selected_count))
        if screenshot:
            capture(dialog, out/screenshot)
        rows = dialog.shown[:]
        dialog.confirm_button.invoke();pump(app)
        assert not dialog.winfo_exists()
        return rows

    saved = []
    editor = RuleDialog(app,2,{'module':'check_mux','kind':'mux','clock_inputs':['b']},saved.append)
    picker(editor,'input','^[ab]$',('a','b'))
    assert editor.fields['clock_inputs'].get() == 'b, a'
    picker(editor,'output','^(o|debug_o)$',('debug_o','o'),read=False,screenshot='output-regex.png')
    picker(editor,'input','^sel$',('sel',),read=False,role='pll_sel')
    entry = editor.entries['clock_inputs']
    entry.delete(0,'end');entry.insert(0,'a， b')
    editor.save();pump(app)
    assert saved[-1]['clock_inputs']==['a','b'] and saved[-1]['clock_output']=='o'
    assert saved[-1]['aux_ports']=={'pll_sel':'sel'}

    wire = RuleDialog(app,2,{'module':'check_probe','kind':'wire'},saved.append)
    incoming = picker(wire,'input','^(lanes|test)$',('lanes','test'))
    outgoing = picker(wire,'output','^(pass|extra)$',('pass','extra'),read=False)
    for rows, name in [(incoming,'lanes'),(outgoing,'pass')]:
        assert {(r['left'],r['right'],r['width']) for r in rows if r['name']==name} == {('3','2',2),('4','2',3)}
    wire.save();pump(app)
    assert saved[-1]['clock_outputs']==['extra','pass']

    subsystem = RuleDialog(app,2,{'module':'check_probe','kind':'subsystem'},saved.append)
    assert subsystem.port_buttons['output'].instate(['disabled'])
    picker(subsystem,'input','^(lanes|test)$',('lanes','test'))
    entry = subsystem.entries['clock_inputs']
    entry.delete(0,'end');entry.insert(0,'lanes[2]')
    subsystem.save();pump(app)
    assert saved[-1]['clock_inputs']==['lanes[2]'] and 'clock_output' not in saved[-1]

    for kind, module in [('occ_root','check_occ_root'),('occ_branch','check_occ_branch'),
                         ('sync_gate','check_sync_gate'),('gate_wrap','check_gate_wrap'),('div','check_div')]:
        editor = RuleDialog(app,3,{'module':module,'kind':kind},saved.append)
        picker(editor,'input','^i$',('i',))
        picker(editor,'output','^o$',('o',),read=False)
        editor.save();pump(app)
        assert saved[-1]['kind']==kind and saved[-1]['clock_output']=='o'

    reset_rules = []
    app.switch_tree_type('reset')
    app.set_config({'tree_type':'reset','design':graph['config']['design'],
                    'layers':[{'index':1,'hierarchy':'origin_top','modules':[]},
                              {'index':2,'hierarchy':'origin_top.region','modules':[]}]})
    for kind, module, layer, pattern, names in [('mux','check_mux',1,'^[ab]$',('a','b')),
            ('rsy_sync','check_sync_gate',2,'^i$',('i',)),('wire','check_probe',1,'^lanes$',('lanes',)),
            ('subsystem','check_probe',1,'^lanes$',('lanes',))]:
        editor = RuleDialog(app,layer,{'module':module,'kind':kind},reset_rules.append)
        picker(editor,'input',pattern,names)
        if kind == 'subsystem':
            assert editor.port_buttons['output'].instate(['disabled'])
        else:
            output = 'pass' if kind == 'wire' else 'o'
            picker(editor,'output','^'+output+'$',(output,),read=False)
        editor.save();pump(app)
        assert reset_rules[-1]['kind']==kind and reset_rules[-1]['reset_inputs']==list(names)
    app.switch_tree_type('clock');pump(app)
    assert app.get_config()['div_source_scopes']==SCOPES
    app.ui_state.update(graph['config'],register_trace_directory=str(native_dir/'registers'))
    registers = RegisterDialog(app)
    pump(app)
    tags = {registers.table.set(row,'div_source') for row in registers.table.get_children()}
    assert {'来源层次 1','来源层次 2','来源未归属'} <= tags
    capture(registers,out/'div-sources.png')
    registers.close();pump(app)
    dialog = SVDialog(app)
    assert dialog.registers.get()==str(native_dir/'registers')
    for key, editor in dialog.editors.items():
        editor.delete('1.0','end');editor.insert('1.0',TEMPLATES[key])
    dialog.analyze()
    pump(app, lambda:dialog.process is not None and dialog.process.poll() is not None and dialog.plan is not None)
    row = next(r for r in dialog.plan.branches()[1] if r['mode']=='with_mux')
    choices = dialog.plan.candidates(row,'var_div')
    dialog.selections[row['id']] = {'var_div':choices[0]['id']}
    dialog.refresh();dialog.table.selection_set(row['id']);dialog.detail();pump(app)
    assert '来源层次 1' in dialog.preview.get('1.0','end') and '来源层次 2' in dialog.preview.get('1.0','end')
    dialog.export(out/'gui.sv')
    pump(app, lambda:dialog.export_result is not None)
    assert dialog.export_result['blocks']==3 and (out/'gui.sv').is_file()
    dialog.close();pump(app)
    app._save_session_now();pump(app)
    assert not errors,errors
    write_graph(out/'gui-write.json',{'passed':True,'rules':saved,'reset_rules':reset_rules,'blocks':3,
                                    'errors':errors,'tk':app.tk.call('info','patchlevel')})
    app.close()
    deadline = time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:
        app.update();time.sleep(.01)
    assert app._destroying


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--phase',choices=('native','gui-write','gui-read'),default='native')
    parser.add_argument('--native',type=Path)
    args = parser.parse_args()
    out = args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    if args.phase=='native':native(out)
    else:gui(out,args.native.resolve(),args.phase)
