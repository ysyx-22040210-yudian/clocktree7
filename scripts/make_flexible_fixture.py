"""Independent full-network golden for 2..N layers and all four device kinds."""
import argparse
import json
from pathlib import Path


def generate(destination, layer_count=2):
    if layer_count < 2:
        raise ValueError('This fixture requires at least two layers')
    out = Path(destination).resolve()
    out.mkdir(parents=True, exist_ok=True)
    # name, natural layer, device, driving instance(s) or external clock(s).
    specifications = [
        ('mux0', 1, 'mux', ['clk_a', 'clk_b']), ('mux1', 1, 'mux', ['clk_c', 'clk_c']),
        ('gate0', 2, 'gate', ['mux0']), ('gate1', 2, 'gate', ['gate0']),
        ('early', 2, 'gate', ['mux0']), ('occ0', 3, 'occ', ['gate1']),
        ('div0', 4, 'div', ['occ0']), ('gate2', 5, 'gate', ['div0']),
        ('occ1', 6, 'occ', ['gate2']), ('div1', 7, 'div', ['occ1']),
        ('gate3', 8, 'gate', ['div1']), ('div2', 9, 'div', ['div1']),
        ('occ2', 10, 'occ', ['div2'])]
    assigned = {name: min(layer, layer_count) for name, layer, kind, sources in specifications}
    def path(name):
        return 'flex_top.l{}.u_{}'.format(assigned[name], name)
    def signal(name):
        return name if name not in assigned else 's_' + name
    used = {source for _, _, _, sources in specifications for source in sources if source in assigned}
    leaves = sorted(set(assigned) - used)
    rtl = [
        'module flex_mux(input a,b,sel, output o); assign o=sel?b:a; endmodule',
        'module flex_gate(input i,en, output o); assign o=i&en; endmodule',
        'module flex_occ(input i,en, output o); reg pass=0; always @(negedge i) pass<=en; assign o=i&pass; endmodule',
        'module flex_div(input i, output reg o=0); always @(posedge i) o<=~o; endmodule',
        'module flex_top(input clk_a,clk_b,clk_c,en,sel, output [{}:0] sinks);'.format(len(leaves)-1),
        'wire ' + ','.join(signal(name) for name in assigned) + ';']
    layers = []
    edges = []
    for layer in range(1, layer_count + 1):
        members = [record for record in specifications if assigned[record[0]] == layer]
        rtl.append('generate if(1) begin : l{}'.format(layer))
        for name, _, kind, sources in members:
            pins = ['a', 'b'] if kind == 'mux' else ['i']
            connections = ['.{}({})'.format(pin, signal(source)) for pin, source in zip(pins, sources)]
            if kind in ('gate', 'occ'):
                connections.append('.en(en)')
            if kind == 'mux':
                connections.append('.sel(sel)')
            connections.append('.o({})'.format(signal(name)))
            rtl.append('flex_{} u_{}({});'.format(kind, name, ','.join(connections)))
            edges.extend([path(source), 'o', path(name), pin] for pin, source in zip(pins, sources) if source in assigned)
        rtl.append('end endgenerate')
        rules = [{'module': 'flex_' + kind, 'kind': kind,
                  'clock_inputs': ['a', 'b'] if kind == 'mux' else ['i'], 'clock_output': 'o'}
                 for kind in sorted({record[2] for record in members})]
        layers.append({'index': layer, 'hierarchy': 'flex_top.l{}'.format(layer), 'modules': rules})
    rtl.extend(['assign sinks={' + ','.join(signal(name) for name in leaves) + '};', 'endmodule'])
    (out / 'fixture.sv').write_text('\n'.join(rtl) + '\n', encoding='utf-8')
    (out / 'rtl.f').write_text(str(out / 'fixture.sv') + '\n', encoding='utf-8')
    config = {'design': {'top': 'flex_top', 'filelist': str(out / 'rtl.f')}, 'layers': layers}
    golden = {'nodes': sorted(path(name) for name in assigned), 'edges': sorted(edges),
              'terminals': sorted(path(name) for name in leaves)}
    for name, value in (('config.json', config), ('golden.json', golden)):
        (out / name).write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    return config, golden


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination')
    parser.add_argument('--layers', type=int, default=2)
    args = parser.parse_args()
    config, golden = generate(args.destination, args.layers)
    print(json.dumps({'layers': len(config['layers']), 'nodes': len(golden['nodes']), 'edges': len(golden['edges'])}))
