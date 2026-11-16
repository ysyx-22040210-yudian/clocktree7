"""Native Verdi regression for L1 SystemVerilog interface member inputs."""
import argparse
from pathlib import Path
import subprocess

from check_target_mode import run
from clocktree.indexed_view import IndexedGraphView
from clocktree.storage import write_graph
from clocktree.sv_templates import prepare


TOP = 'interface_top'
SCOPE = TOP + '.u_crg'
RTL = '''
interface clocks_if;
  wire clk;
  wire [0:3] bus;
  wire feedback;
  modport sink(input clk,bus,output feedback);
  modport source(output clk,bus,input feedback);
endinterface
module i_gate(input i,en,output o); assign o=i&en; endmodule
module i_source(input i,output o); assign o=i; endmodule
module i_output(clocks_if.source link, input clk);
  assign link.clk=clk;
endmodule
module i_crg(clocks_if.sink ext, clocks_if.sink other, clocks_if unrestricted,
             input raw,en, output [7:0] q);
  wire via_alias;
  clocks_if local_if();
  assign via_alias=ext.clk;
  assign ext.feedback=raw;
  assign unrestricted.feedback=raw;
  assign local_if.clk=raw;
  i_gate g_ext(ext.clk,en,q[0]);
  i_gate g_bit(ext.bus[2],en,q[1]);
  i_gate g_alias(via_alias,en,q[2]);
  i_gate g_selected(other.clk,en,q[3]);
  i_gate g_local(local_if.clk,en,q[4]);
  i_gate g_unrestricted(unrestricted.clk,en,q[5]);
  i_gate g_feedback(ext.feedback,en,q[6]);
  i_gate g_plain_output(unrestricted.feedback,en,q[7]);
endmodule
module interface_top(input [3:0] raw,input en,output [7:0] q);
  clocks_if link(), other(), typed();
  wire selected;
  i_source u_source(raw[0],selected);
  assign link.clk=raw[1];
  assign link.bus={raw[1],raw[0],raw[3],raw[2]};
  i_output u_output(other,selected);
  assign typed.clk=raw[2];
  i_crg u_crg(.ext(link),.other(other),.unrestricted(typed),.raw(raw[0]),.en(en),.q(q));
endmodule
'''
ENTRIES = {
    'g_ext': 'ext.clk', 'g_bit': 'ext.bus[2]', 'g_alias': 'ext.clk',
    'g_local': 'raw', 'g_unrestricted': 'unrestricted.clk',
    'g_feedback': 'raw', 'g_plain_output': 'raw',
}
MODPORT_TOP = 'modport_top'
MODPORT_RTL = '''
interface clock_link(input wire ref_clk);
    wire [0:3] from_mst;
    wire [5:2] from_slv;
    modport mst(input ref_clk, from_slv, output from_mst);
    modport slv(input ref_clk, from_mst, output from_slv);
endinterface
module clk_source(input i, output o); assign o=i; endmodule
module clk_gate(input i,en, output o); assign o=i&en; endmodule

module master(clock_link.mst port_if, input en,alt_clk, output [2:0] q);
    wire drive, decoy;
    clk_source u_drive(port_if.ref_clk,drive);
    clk_source u_decoy(alt_clk,decoy);
    assign port_if.from_mst={1'b0,decoy,drive,1'b1};
    clk_gate u_receive(port_if.from_slv[4],en,q[0]);
    clk_gate u_self(port_if.from_mst[2],en,q[1]);
    clk_gate u_shared(port_if.ref_clk,en,q[2]);
endmodule

module slave(clock_link.slv port_if, input en,alt_clk, output [2:0] q);
    wire drive, decoy;
    clk_source u_drive(port_if.ref_clk,drive);
    clk_source u_decoy(alt_clk,decoy);
    assign port_if.from_slv={1'b1,drive,decoy,1'b0};
    clk_gate u_receive(port_if.from_mst[2],en,q[0]);
    clk_gate u_self(port_if.from_slv[4],en,q[1]);
    clk_gate u_shared(port_if.ref_clk,en,q[2]);
endmodule

module modport_top(input ref_clk,en,alt_clk, output [2:0] qm,qs);
    clock_link link(ref_clk);
    master u_mst(.port_if(link.mst),.en(en),.alt_clk(alt_clk),.q(qm));
    slave u_slv(.port_if(link.slv),.en(en),.alt_clk(alt_clk),.q(qs));
endmodule
'''


def compile_kdb(out, files, tops):
    out.mkdir(parents=True, exist_ok=True)
    command = ['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all']
    for top in tops:
        command.extend(['-top',top])
    with (out/'vcs.log').open('w') as log:
        subprocess.check_call(command + [str(p) for p in files], cwd=str(out), stdout=log, stderr=subprocess.STDOUT)
    return out/'simv.daidir/kdb.elab++'


def audit(out):
    out.mkdir(parents=True, exist_ok=True)
    rtl = out/'interface.sv'
    rtl.write_text(RTL, encoding='utf-8')
    filelist = out/'rtl.f'
    filelist.write_text(str(rtl)+'\n', encoding='utf-8')
    kdb = compile_kdb(out/'compile',[rtl],[TOP])
    layers = [
        {'index':1,'hierarchy':SCOPE,'modules':[{'module':'i_gate','kind':'sync_gate','clock_inputs':['i'],'clock_output':'o'}]},
        {'index':2,'hierarchy':TOP,'modules':[{'module':'i_source','kind':'occ_root','clock_inputs':['i'],'clock_output':'o'}]},
    ]
    reports = []
    for mode in ('target','full'):
        for design, entry in [('filelist',filelist),('elab',kdb)]:
            for bounded in (False,True):
                name = '{}-{}-{}'.format(mode, design, 'bounded' if bounded else 'wide')
                config = {'design':{'top':TOP,design:str(entry)},'layers':layers[:1] if bounded else layers,
                          'extraction_mode':mode,'trace_scope':SCOPE if bounded else ''}
                case = out/name
                graph, stats = run(config,case)
                expected = dict(ENTRIES, g_selected='other.clk') if bounded else ENTRIES
                l1 = {n['id'].rsplit('.',1)[-1]:n['l1_inputs']['i'] for n in graph['nodes'] if n.get('l1_inputs')}
                assert {name:e['signal'] for name,e in l1.items()} == {n:SCOPE+'.'+p for n,p in expected.items()}, (name,l1)
                assert graph['summary']['connectivity_complete'] and not graph['summary']['undriven_inputs'], (name,list(graph['boundaries']))
                edges = list(graph['edges'])
                if bounded:
                    assert not edges
                else:
                    assert len(edges)==1 and (edges[0]['source'],edges[0]['source_pin'],edges[0]['target'],edges[0]['target_pin']) == (TOP+'.u_source','o',SCOPE+'.g_selected','i'), edges
                    assert set(edges[0]['evidence'])==({'driver','load'} if mode=='full' else {'driver'}), edges
                    bit = [b['endpoint'] for b in graph['boundaries'] if b['instance']==SCOPE+'.g_bit' and b['direction']=='driver']
                    assert bit==[TOP+('.raw[3]' if mode=='target' else '.raw[3:0]#[3]')], bit
                model = IndexedGraphView(graph)
                model.project('instances')
                assert len(model.l1_edges)==len(expected) and SCOPE+'.ext.bus[2]' in model.svg()
                stats['ports'] = {name:e['port'] for name,e in l1.items()}
                if not bounded:
                    template = {'without_mux':'wire check_$var_clkip = $var_pll0;\n'}
                    plan = prepare(graph,None,case/'plan',False)
                    bits = next(row for row in plan.branches()[1] if row['instance']==SCOPE+'.g_bit')
                    resolved = plan.resolve(bits,template)
                    assert resolved['ready'] and resolved['values']['var_pll0']==SCOPE+'.ext.bus[2]', resolved
                    sv = case/'branches.sv'
                    export = plan.export(template,sv)
                    assert export['blocks']==len(expected)+1 and not export['skipped'], export
                    checks = case/'checks.sv'
                    checks.write_text('module checks;\n'+sv.read_text(encoding='utf-8')+'\nendmodule\n',encoding='utf-8')
                    compile_kdb(case/'sv-check',[rtl,checks],[TOP,'checks'])
                    stats['vcs_sv_elaborated'] = True
                    stats['sv_blocks'] = export['blocks']
                reports.append(dict(case=name,**stats))
    write_graph(out/'acceptance.json',{'passed':True,'cases':reports})


def audit_mst_slv(out):
    out.mkdir(parents=True, exist_ok=True)
    rtl = out/'modports.sv'
    rtl.write_text(MODPORT_RTL, encoding='utf-8')
    filelist = out/'rtl.f'
    filelist.write_text(str(rtl)+'\n', encoding='utf-8')
    kdb = compile_kdb(out/'compile',[rtl],[MODPORT_TOP])
    scopes = [MODPORT_TOP+'.u_mst', MODPORT_TOP+'.u_slv']
    port_from_peer = dict(zip(scopes,('port_if.from_slv[4]','port_if.from_mst[2]')))
    rules = [{'module':module,'kind':kind,'clock_inputs':['i'],'clock_output':'o'}
             for module,kind in [('clk_source','occ_root'),('clk_gate','sync_gate')]]
    reports = []
    for design, entry in [('filelist',filelist),('elab',kdb)]:
        for mode in ('target','full'):
            for scope, peer in [scopes, scopes[::-1]]:
                layers = [{'index':index,'hierarchy':path,'modules':rules}
                          for index,path in enumerate((scope,peer),1)]
                for bounded in (False,True):
                    name = '{}-{}-{}-{}'.format(mode,design,scope.rsplit('.',1)[1],'bounded' if bounded else 'wide')
                    cfg = {'design':{'top':MODPORT_TOP,design:str(entry)},'layers':layers[:1] if bounded else layers,
                           'extraction_mode':mode,'trace_scope':scope if bounded else ''}
                    graph, stats = run(cfg,out/name)
                    assert graph['summary']['connectivity_complete'] and not graph['summary']['undriven_inputs'], (name,list(graph['boundaries']),list(graph['diagnostics']))
                    expected = {(scope+'.u_drive','o',scope+'.u_self','i')}
                    if not bounded:
                        expected.update([(scope+'.u_drive','o',peer+'.u_receive','i'),
                                         (peer+'.u_drive','o',scope+'.u_receive','i'),
                                         (peer+'.u_drive','o',peer+'.u_self','i')])
                    edges = list(graph['edges'])
                    assert {(e['source'],e['source_pin'],e['target'],e['target_pin']) for e in edges} == expected, (name,edges)
                    assert all(set(e['evidence'])==({'driver','load'} if mode=='full' else {'driver'}) for e in edges)
                    entries = {'u_drive':'port_if.ref_clk','u_shared':'port_if.ref_clk','u_decoy':'alt_clk'}
                    if bounded:
                        entries['u_receive'] = port_from_peer[scope]
                    observed = {n['id']:n['l1_inputs']['i']['signal'] for n in graph['nodes'] if n.get('l1_inputs')}
                    assert observed == {scope+'.'+n:scope+'.'+p for n,p in entries.items()}, (name,observed)
                    assert [d['code'] for d in graph['diagnostics']] == ([] if bounded else ['reverse_layer']), graph['diagnostics']
                    if mode=='full' and not bounded:
                        no_loads = {b['instance'] for b in graph['boundaries'] if b['status']=='no_load' and b['direction']=='load'}
                        assert no_loads == {path+'.u_decoy' for path in scopes}, no_loads
                    view = IndexedGraphView(graph)
                    view.project('instances')
                    assert len(view.l1_edges)==len(entries)
                    if bounded:
                        assert scope+'.'+port_from_peer[scope] in view.svg()
                    reports.append(dict(case=name, ports=entries, edges=sorted(expected), **stats))
    write_graph(out/'acceptance.json',{'passed':True,'cases':reports})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--mst-slv',action='store_true',help='Test opposing modports bound to the same interface')
    args = parser.parse_args()
    (audit_mst_slv if args.mst_slv else audit)(Path(args.out).resolve())
