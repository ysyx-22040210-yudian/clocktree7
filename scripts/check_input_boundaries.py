"""Exercise configurable RTL port and wire boundaries in a native Verdi KDB."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clocktree.extract import extract
from clocktree.indexed_view import IndexedGraphView
from clocktree.storage import write_graph
from clocktree.sv_templates import prepare
from check_high_fanout import fixture as fanout_fixture
from check_l1_interfaces import MODPORT_RTL, MODPORT_TOP, compile_kdb
from check_scoped_trace import config as scope_config, fixture as scope_fixture
from check_target_mode import run as guarded_extract


TOP = 'boundary_top'
OUTER = TOP + '.u_outer'
INNER = OUTER + '.u_inner'
RTL = """
interface boundary_bus;
  wire [0:3] clk;
  modport slv(input clk);
endinterface
module boundary_source(input i, output o); assign o = i; endmodule
module boundary_gate(input i, output o); assign o = i; endmodule
module boundary_reset_sync(input i, output o); assign o = i; endmodule
module boundary_inner(input [3:0] clk_bus, boundary_bus.slv ext,
                      input rst_i, output [2:0] out, output rst_o);
  wire [0:3] mid;
  boundary_gate from_bits(.i(clk_bus[1]), .o(mid[2]));
  boundary_gate downstream(.i(mid[2:2]), .o(out[0]));
  boundary_gate from_member(.i(ext.clk[2]), .o(out[1]));
  boundary_gate other_bit(.i(clk_bus[3]), .o(out[2]));
  wire reset_internal, reset_stage;
  assign reset_internal = rst_i;
  boundary_reset_sync u_sync(.i(reset_internal), .o(reset_stage));
  boundary_reset_sync u_follow(.i(reset_stage), .o(rst_o));
endmodule
module boundary_outer(input [3:0] clk_bus, boundary_bus.slv ext,
                      input rst_i, output [3:0] out, output rst_o);
  boundary_gate u_local(.i(clk_bus[1]), .o(out[3]));
  boundary_inner u_inner(.clk_bus(clk_bus), .ext(ext), .rst_i(rst_i),
                         .out(out[2:0]), .rst_o(rst_o));
endmodule
module boundary_top(input [3:0] clocks, input rst_i, output [3:0] out, output rst_o);
  wire s;
  boundary_source u_source(.i(clocks[1]), .o(s));
  boundary_bus fabric();
  assign fabric.clk[2] = s;
  assign fabric.clk[0] = clocks[0];
  assign fabric.clk[1] = clocks[2];
  assign fabric.clk[3] = clocks[3];
  boundary_outer u_outer(.clk_bus({clocks[3:2],s,clocks[0]}), .ext(fabric),
                         .rst_i(rst_i), .out(out), .rst_o(rst_o));
endmodule
"""


MIXED_RTL = """
module mixed_source(input i, output o); assign o = i; endmodule
module mixed_sink(input [3:0] link, input formal_clk); endmodule
module mixed_region(input clk_a, clk_b, ingress, input wire shared_in);
  wire a, b;
  mixed_source src_a(.i(clk_a), .o(a)), src_b(.i(clk_b), .o(b));
  wire [3:0] branch_clk;
  assign branch_clk[0] = ingress;
  assign branch_clk[0] = a;
  assign branch_clk[1] = b;
  assign branch_clk[1] = a;
  assign branch_clk[1] = ingress;
  assign branch_clk[2] = ingress;
  assign branch_clk[3] = a & b;
  assign shared_in = a;
  mixed_sink sink(.link(branch_clk), .formal_clk(shared_in));
endmodule
module mixed_top(input clk_a, clk_b, raw);
  wire external_clk;
  mixed_source outside(.i(raw), .o(external_clk));
  wire shared_in;
  assign shared_in = raw;
  mixed_region region(.clk_a(clk_a), .clk_b(clk_b), .ingress(external_clk), .shared_in(shared_in));
endmodule
"""


def check_mixed_drivers(out):
    region = 'mixed_top.region'
    sink = region+'.sink'
    source = out/'mixed.sv'
    source.write_text(MIXED_RTL, encoding='utf-8')
    elab = compile_kdb(out/'mixed-kdb', [source], ['mixed_top'])
    reports = []
    for mode in ('target', 'full'):
        case = out/(mode+'-mixed')
        config = {'design':{'top':'mixed_top','elab':str(elab)}, 'trace_scope':region, 'extraction_mode':mode,
                  'layers':[{'index':1, 'hierarchy':region, 'input_boundary':True, 'modules':[
                      {'module':'mixed_source', 'kind':'occ_root', 'clock_inputs':['i'], 'clock_output':'o'},
                      {'module':'mixed_sink', 'kind':'subsystem', 'clock_inputs':['link','formal_clk']}]}]}
        graph = run(config, case, ('multiple_drivers',))
        by_pin = {}
        for e in graph['edges']:
            assert e['target'] == sink and e['source_pin'] == 'o'
            assert set(e['evidence']) == ({'driver','load'} if mode=='full' else {'driver'})
            by_pin.setdefault(e['target_pin'], set()).add(e['source'])
        assert by_pin == {'link[0]':{region+'.src_a'}, 'link[1]':{region+'.src_a', region+'.src_b'},
                          'formal_clk':{region+'.src_a'}}, by_pin
        issue, = graph['diagnostics']
        assert (issue['instance'], issue['pin']) == (sink, 'link[1]')
        view = IndexedGraphView(graph)
        view.project('instances')
        entries = view.original[sink]['input_boundaries']
        assert {p:v['signal'] for p,v in entries.items()} == {
            'link[2]': region+'.branch_clk[2]', 'link[3]': region+'.branch_clk[3]'}, entries
        assert all(v['source_kind'] == 'wire' for v in entries.values())
        assert {e['target_pin'] for e in view.l1_edges.values() if
                view.nodes[e['target']].get('reference_of',e['target'])==sink} == set(entries)
        plan = prepare(graph, None, case/'plan')
        templates = {'without_mux':'wire check_$var_clkip = $var_pll0 & $var_clkip;\n'}
        resolved = {r['pin']:plan.resolve(r, templates) for r in plan.branches()[1] if r['instance']==sink}
        assert {p for p,r in resolved.items() if r['ready']} == {'link[0]', 'link[2]', 'link[3]', 'formal_clk'}, resolved
        assert resolved['link[0]']['values']['var_pll0'] == region+'.clk_a'
        assert resolved['formal_clk']['values']['var_pll0'] == region+'.clk_a'
        assert all(resolved[p]['values']['var_pll0']==v['signal'] for p,v in entries.items())
        sv = case/'mixed.sv'
        exported = plan.export(templates, sv)
        assert (exported['blocks'],exported['skipped']) == (4,1), exported
        checks = case/'checks.sv'
        checks.write_text('module checks;\n'+sv.read_text(encoding='utf-8')+'\nendmodule\n', encoding='utf-8')
        compile_kdb(case/'sv-check', [source, checks], ['mixed_top','checks'])
        reports.append({'mode':mode, 'mixed_drivers':sorted(issue['drivers']), 'sv_blocks':4, 'sv_skipped':1})
    return reports


def clock_config(elab):
    return {'design': {'top': TOP, 'elab': str(elab)},
            'layers': [
                {'index': 1, 'hierarchy': TOP, 'modules': [
                    {'module': 'boundary_source', 'kind': 'sync_gate', 'clock_inputs': ['i'], 'clock_output': 'o'}]},
                *({'index': index, 'hierarchy': scope, 'modules': [
                    {'module': 'boundary_gate', 'kind': 'gate_wrap', 'clock_inputs': ['i'], 'clock_output': 'o'}]}
                  for index, scope in [(2, OUTER), (3, INNER)]),
            ]}


def run(config, directory, expected_diagnostics=()):
    directory.mkdir()
    write_graph(directory/'input.json', config)
    graph = extract(directory/'input.json', directory)
    assert graph['summary']['connectivity_complete'], (directory, list(graph['diagnostics']), list(graph['boundaries']))
    assert sorted(d['code'] for d in graph['diagnostics']) == sorted(expected_diagnostics), (directory, list(graph['diagnostics']))
    assert graph['summary']['topology_valid'] == (not expected_diagnostics)
    return graph


def check_modport_boundaries(out):
    source = out/'peer-modports.sv'
    source.write_text(MODPORT_RTL, encoding='utf-8')
    elab = compile_kdb(out/'peer-kdb', [source], [MODPORT_TOP])
    scopes = [MODPORT_TOP+'.u_mst', MODPORT_TOP+'.u_slv']
    rules = [{'module': module, 'kind': kind, 'clock_inputs': ['i'], 'clock_output': 'o'}
             for module, kind in [('clk_source','occ_root'), ('clk_gate','sync_gate')]]
    expected_inputs = {}
    for scope in scopes:
        for device, port in [('u_drive','port_if.ref_clk'), ('u_shared','port_if.ref_clk'), ('u_decoy','alt_clk')]:
            expected_inputs[scope+'.'+device] = scope+'.'+port
    expected_edges = {(source+'.u_drive', target+'.'+gate)
                      for source, peer in (scopes, scopes[::-1])
                      for target, gate in [(source, 'u_self'), (peer, 'u_receive')]}
    reports = []
    for mode in ('target', 'full'):
        config = {'design': {'top': MODPORT_TOP, 'elab': str(elab)}, 'extraction_mode': mode,
                  'layers': [{'index': index, 'hierarchy': scope, 'modules': rules, 'input_boundary': True}
                             for index, scope in enumerate(scopes, 1)]}
        graph = run(config, out/(mode+'-peer'), ('reverse_layer',))
        edges = {(e['source'], e['target']) for e in graph['edges']}
        inputs = {n['id']: n['input_boundaries']['i']['signal'] for n in graph['nodes'] if n.get('input_boundaries')}
        assert edges == expected_edges, (mode, edges)
        reverse, = [e for e in graph['edges'] if e['id'] == graph['diagnostics'][0]['edge']]
        assert (reverse['source'], reverse['target']) == (scopes[1]+'.u_drive', scopes[0]+'.u_receive')
        assert all(e['source_pin']=='o' and e['target_pin']=='i' and
                   set(e['evidence'])==({'driver','load'} if mode=='full' else {'driver'}) for e in graph['edges'])
        assert inputs == expected_inputs, (mode, inputs)
        reports.append({'mode':mode, 'peer_modport_inputs':inputs, 'edges':sorted(edges)})
    return reports


def check_trace_root(out):
    scope, filelist = scope_fixture(out/'scope-fixture')
    reports = []
    for mode in ('target','full'):
        cfg = scope_config(scope, filelist, 'clock', True)
        cfg['layers'][0]['input_boundary'] = True
        cfg['extraction_mode'] = mode
        graph = run(cfg, out/(mode+'-trace-root'))
        expected = [('u_mux','u_gate'), ('u_gate','u_occ'), ('u_occ','u_div'), ('u_div','u_out')]
        assert {(e['source'],e['target']) for e in graph['edges']} == {
            (scope+'.'+up,scope+'.'+down) for up,down in expected}
        # The detour is wired back to the MUX only through a net outside this root.
        detour, = [n for n in graph['nodes'] if n['id']==scope+'.u_detour']
        assert detour['input_boundaries']['i']['signal'] == scope+'.clk_link_i'
        reports.append({'mode':mode, 'trace_root':scope, 'detour_boundary':detour['input_boundaries']['i']['signal']})
    return reports


def check_fanout(out, count):
    scope = 'fan_top.domain'
    reports = []
    for mode in ('target','full'):
        case = out/(mode+'-fanout')
        cfg = json.loads(fanout_fixture(case/'fixture', count, mode).read_text(encoding='utf-8'))
        cfg['layers'][0]['input_boundary'] = True
        graph, stats = guarded_extract(cfg, case/'run')
        assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid']
        loads = ['g'+str(i) for i in range(count)] + ['via_alias','via_bit']
        assert {(e['source'],e['source_pin'],e['target'],e['target_pin']) for e in graph['edges']} == {
            (scope+'.source','o',scope+'.'+name,'i') for name in loads}
        assert graph['summary']['input_boundary_connections'] == 1
        assert all(set(e['evidence']) == ({'driver','load'} if mode=='full' else {'driver'}) for e in graph['edges'])
        reports.append({'mode':mode, 'selected_loads':len(loads), 'other_flops':count, 'seconds':stats['seconds'],
                        'worker_peak_gib':stats['rss_peak_gib']})
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--fanout', type=int, default=0, help='Also test N gates and N register loads on a boundary wire')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = out/'design.sv'
    source.write_text(RTL, encoding='utf-8')
    elab = compile_kdb(out/'kdb', [source], [TOP])
    reports = []
    for mode in ('target', 'full'):
        for label, selected in [('legacy', ()), ('top', (1,)), ('outer', (2,)), ('inner', (3,)), ('both', (2, 3))]:
            config = clock_config(elab)
            config['extraction_mode'] = mode
            for layer in config['layers']:
                if layer['index'] in selected:
                    layer['input_boundary'] = True
            graph = run(config, out/(mode+'-'+label))
            edges = {(e['source'], e['target']) for e in graph['edges']}
            inputs = {n['id']: (n['input_boundaries']['i']['layer'], n['input_boundaries']['i']['signal'])
                      for n in graph['nodes'] if n.get('input_boundaries')}
            expected_edges = {(TOP+'.u_source', OUTER+'.u_local'), (INNER+'.from_bits', INNER+'.downstream'),
                              (TOP+'.u_source', INNER+'.from_bits'), (TOP+'.u_source', INNER+'.from_member')}
            expected_inputs = {}
            if 2 in selected or 3 in selected:
                index, scope = (3, INNER) if 3 in selected else (2, OUTER)
                expected_inputs[INNER+'.other_bit'] = (index, scope+'.clk_bus[3]')
            elif 1 in selected:
                expected_inputs = {TOP+'.u_source': (1, TOP+'.clocks[1]'), INNER+'.other_bit': (1, TOP+'.clocks[3]')}
            assert edges == expected_edges, (mode, label, edges)
            assert inputs == expected_inputs, (mode, label, inputs)
            assert graph['summary']['input_boundary_connections'] == len(inputs)
            assert all(set(edge['evidence']) == ({'driver', 'load'} if mode == 'full' else {'driver'})
                       for edge in graph['edges'])
            cuts = {b['endpoint'] for b in graph['boundaries']
                    if b['status'] in ('layer_input_boundary','layer_wire_boundary') and b['direction'] == 'load'}
            expected_cuts = set()
            if mode == 'full':
                if 3 in selected:
                    expected_cuts.add(INNER+'.mid[2]')
                if 1 in selected:
                    expected_cuts.add(TOP+'.s')
                if 2 in selected or 3 in selected:
                    first_cut = OUTER if 2 in selected else INNER
                    expected_cuts.update((first_cut+'.clk_bus[1]', first_cut+'.ext.clk[2]'))
            assert cuts == expected_cuts, (mode, label, cuts)
            reports.append({'mode': mode, 'selected': list(selected), 'pins': inputs, 'edges':sorted(edges), 'output_cuts': sorted(cuts)})
        reset = {'tree_type': 'reset', 'extraction_mode': mode, 'design': {'top': TOP, 'elab': str(elab)},
                 'layers': [{'index': 1, 'hierarchy': TOP, 'modules': []},
                            {'index': 2, 'hierarchy': INNER, 'input_boundary': True, 'modules': [
                                {'module': 'boundary_reset_sync', 'kind': 'rsy_sync', 'reset_inputs': ['i'], 'reset_output': 'o'}]}]}
        graph = run(reset, out/(mode+'-reset'))
        edge, = graph['edges']
        assert (edge['source'],edge['source_pin'],edge['target'],edge['target_pin']) == (INNER+'.u_sync','o',INNER+'.u_follow','i')
        assert set(edge['evidence']) == ({'driver','load'} if mode=='full' else {'driver'})
        node, = [n for n in graph['nodes'] if n.get('input_boundaries')]
        assert node['input_boundaries']['i']['signal'] == INNER+'.reset_internal'
        assert node['input_boundaries']['i']['source_kind'] == 'wire'
        reports.append({'mode': mode, 'tree': 'reset', 'reset_pin': node['input_boundaries']['i']['signal']})
        seed = {'extraction_mode': mode, 'design': {'top': TOP, 'elab': str(elab)},
                'layers': [{'index': 1, 'hierarchy': TOP, 'modules': [
                               {'module': 'boundary_outer', 'kind': 'gate_wrap', 'clock_inputs': ['clk_bus[1]'],
                                'clock_output': 'out[3]'}]},
                           {'index': 2, 'hierarchy': OUTER, 'input_boundary': True, 'modules': []}]}
        graph = run(seed, out/(mode+'-seed'))
        node, = graph['nodes']
        assert node['input_boundaries']['clk_bus[1]']['signal'] == OUTER+'.clk_bus[1]'
        reports.append({'mode': mode, 'seed_pin': node['input_boundaries']['clk_bus[1]']['signal']})
        seed['layers'][0]['modules'].extend(clock_config(elab)['layers'][0]['modules'])
        graph = run(seed, out/(mode+'-seed-source'))
        edge, = graph['edges']
        assert (edge['source'],edge['source_pin'],edge['target'],edge['target_pin']) == (TOP+'.u_source','o',OUTER,'clk_bus[1]')
        assert set(edge['evidence']) == ({'driver','load'} if mode=='full' else {'driver'})
        assert not graph['summary']['input_boundary_connections']
        reports.append({'mode':mode, 'selected_seed_driver':edge['source']})
    reports.extend(check_modport_boundaries(out))
    reports.extend(check_mixed_drivers(out))
    reports.extend(check_trace_root(out))
    if args.fanout:
        reports.extend(check_fanout(out, args.fanout))
    write_graph(out/'acceptance.json', {'passed': True, 'cases': reports})
    print(json.dumps(reports, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
