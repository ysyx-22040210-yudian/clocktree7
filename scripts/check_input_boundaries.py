"""Exercise configurable RTL port and wire boundaries in a native Verdi KDB."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clocktree.extract import extract
from clocktree.storage import write_graph
from check_l1_interfaces import MODPORT_RTL, MODPORT_TOP, compile_kdb


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
  wire reset_internal;
  assign reset_internal = rst_i;
  boundary_reset_sync u_sync(.i(reset_internal), .o(rst_o));
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


def clock_config(elab):
    return {'design': {'top': TOP, 'elab': str(elab)},
            'layers': [
                {'index': 1, 'hierarchy': TOP, 'modules': [
                    {'module': 'boundary_source', 'kind': 'sync_gate', 'clock_inputs': ['i'], 'clock_output': 'o'}]},
                *({'index': index, 'hierarchy': scope, 'modules': [
                    {'module': 'boundary_gate', 'kind': 'gate_wrap', 'clock_inputs': ['i'], 'clock_output': 'o'}]}
                  for index, scope in [(2, OUTER), (3, INNER)]),
            ]}


def run(config, directory):
    directory.mkdir()
    write_graph(directory/'input.json', config)
    graph = extract(directory/'input.json', directory)
    assert graph['summary']['connectivity_complete'], (directory, list(graph['diagnostics']), list(graph['boundaries']))
    assert graph['summary']['topology_valid'], (directory, list(graph['diagnostics']))
    return graph


def check_modport_boundaries(out):
    source = out/'peer-modports.sv'
    source.write_text(MODPORT_RTL, encoding='utf-8')
    elab = compile_kdb(out/'peer-kdb', [source], [MODPORT_TOP])
    scopes = [MODPORT_TOP+'.u_mst', MODPORT_TOP+'.u_slv']
    rules = [{'module': module, 'kind': kind, 'clock_inputs': ['i'], 'clock_output': 'o'}
             for module, kind in [('clk_source','occ_root'), ('clk_gate','sync_gate')]]
    expected_inputs = {}
    for scope, peer_port in zip(scopes, ('port_if.from_slv[4]', 'port_if.from_mst[2]')):
        for device, port in [('u_receive',peer_port), ('u_drive','port_if.ref_clk'),
                             ('u_shared','port_if.ref_clk'), ('u_decoy','alt_clk'), ('u_self','drive')]:
            expected_inputs[scope+'.'+device] = scope+'.'+port
    reports = []
    for mode in ('target', 'full'):
        config = {'design': {'top': MODPORT_TOP, 'elab': str(elab)}, 'extraction_mode': mode,
                  'layers': [{'index': index, 'hierarchy': scope, 'modules': rules, 'input_boundary': True}
                             for index, scope in enumerate(scopes, 1)]}
        graph = run(config, out/(mode+'-peer'))
        edges = {(e['source'], e['target']) for e in graph['edges']}
        inputs = {n['id']: n['input_boundaries']['i']['signal'] for n in graph['nodes'] if n.get('input_boundaries')}
        assert not edges, (mode, edges)
        assert inputs == expected_inputs, (mode, inputs)
        assert all(n['input_boundaries']['i']['source_kind'] == 'wire'
                   for n in graph['nodes'] if n['id'].endswith('.u_self'))
        reports.append({'mode':mode, 'peer_modport_inputs':inputs})
    return reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
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
            expected_edges = set()
            expected_inputs = {}
            expected_wires = {}
            if 3 in selected:
                expected_wires[INNER+'.downstream'] = (3, INNER+'.mid[2]')
            else:
                expected_edges.add((INNER+'.from_bits', INNER+'.downstream'))
            if 2 in selected:
                expected_inputs[OUTER+'.u_local'] = (2, OUTER+'.clk_bus[1]')
            elif 1 not in selected:
                expected_edges.add((TOP+'.u_source', OUTER+'.u_local'))
            if 2 in selected or 3 in selected:
                index, scope = (3, INNER) if 3 in selected else (2, OUTER)
                expected_inputs.update({INNER+'.from_bits': (index, scope+'.clk_bus[1]'),
                                        INNER+'.other_bit': (index, scope+'.clk_bus[3]'),
                                        INNER+'.from_member': (index, scope+'.ext.clk[2]')})
            else:
                if 1 in selected:
                    expected_inputs.update({TOP+'.u_source': (1, TOP+'.clocks[1]'),
                                            INNER+'.other_bit': (1, TOP+'.clocks[3]')})
                    expected_wires.update({instance: (1, TOP+'.s') for instance in
                                           (OUTER+'.u_local', INNER+'.from_bits', INNER+'.from_member')})
                else:
                    expected_edges.update((TOP+'.u_source', INNER+'.'+name) for name in ('from_bits', 'from_member'))
            expected_inputs.update(expected_wires)
            assert edges == expected_edges, (mode, label, edges)
            assert inputs == expected_inputs, (mode, label, inputs)
            assert {n['id'] for n in graph['nodes']
                    if n.get('input_boundaries',{}).get('i',{}).get('source_kind') == 'wire'} == set(expected_wires)
            assert graph['summary']['input_boundary_connections'] == len(inputs)
            assert all(set(edge['evidence']) == ({'driver', 'load'} if mode == 'full' else {'driver'})
                       for edge in graph['edges'])
            cuts = {b['endpoint'] for b in graph['boundaries']
                    if b['status'] in ('layer_input_boundary','layer_wire_boundary') and b['direction'] == 'load'}
            expected_cuts = set()
            if mode == 'full':
                expected_cuts.update(endpoint for _, endpoint in expected_wires.values())
                if 2 in selected or 3 in selected:
                    first_cut = OUTER if 2 in selected else INNER
                    expected_cuts.update((first_cut+'.clk_bus[1]', first_cut+'.ext.clk[2]'))
            assert cuts == expected_cuts, (mode, label, cuts)
            reports.append({'mode': mode, 'selected': list(selected), 'pins': inputs, 'output_cuts': sorted(cuts)})
        reset = {'tree_type': 'reset', 'extraction_mode': mode, 'design': {'top': TOP, 'elab': str(elab)},
                 'layers': [{'index': 1, 'hierarchy': TOP, 'modules': []},
                            {'index': 2, 'hierarchy': INNER, 'input_boundary': True, 'modules': [
                                {'module': 'boundary_reset_sync', 'kind': 'rsy_sync', 'reset_inputs': ['i'], 'reset_output': 'o'}]}]}
        graph = run(reset, out/(mode+'-reset'))
        node, = graph['nodes']
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
    reports.extend(check_modport_boundaries(out))
    write_graph(out/'acceptance.json', {'passed': True, 'cases': reports})
    print(json.dumps(reports, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
