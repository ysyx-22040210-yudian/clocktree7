"""VM native Tk acceptance for connected devices within one logical layer."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import traceback
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.gui import ClockTreeApp, write_json
from clocktree.storage import IndexedGraph
from clocktree.indexed_view import IndexedGraphView, OVERVIEW_LIMIT
from clocktree.session import read_graph
from check_flexible_gui import pump, capture, compare
from make_flexible_fixture import generate as generate_clock
from make_reset_fixture import generate as generate_reset


def compact_fixture(out):
    out.mkdir(parents=True, exist_ok=True)
    rtl = '''module demo_mux(input a,b,sel,output o); assign o=sel?b:a; endmodule
module demo_gate(input i,en,output o); assign o=i&en; endmodule
module demo_occ(input i,en,output o); reg pass=0; always @(negedge i) pass<=en; assign o=i&pass; endmodule
module demo_div(input i,output reg o=0); always @(posedge i) o<=~o; endmodule
module horizontal_top(input clk_a,clk_b,sel,en,output clk_out,early_out);
wire source,gated,test_clk,divided;
generate if(1) begin : l1
  demo_mux u_mux(.a(clk_a),.b(clk_b),.sel(sel),.o(source));
end if(1) begin : l2
  demo_gate u_gate(.i(source),.en(en),.o(gated));
  demo_occ u_occ(.i(gated),.en(en),.o(test_clk));
  demo_div u_div(.i(test_clk),.o(divided));
  demo_gate u_early(.i(gated),.en(en),.o(early_out));
end if(1) begin : l3
  demo_gate u_out(.i(divided),.en(en),.o(clk_out));
end endgenerate
endmodule
'''
    (out / 'fixture.sv').write_text(rtl, encoding='utf-8')
    (out / 'rtl.f').write_text(str(out / 'fixture.sv') + '\n', encoding='utf-8')
    config = {'design': {'top': 'horizontal_top', 'filelist': str(out / 'rtl.f')}, 'layers': []}
    for layer, kinds in ((1, ['mux']), (2, ['gate', 'occ', 'div']), (3, ['gate'])):
        config['layers'].append({'index': layer, 'hierarchy': 'horizontal_top.l{}'.format(layer),
            'modules': [{'module': 'demo_' + kind, 'kind': kind,
                         'clock_inputs': ['a', 'b'] if kind == 'mux' else ['i'], 'clock_output': 'o'} for kind in kinds]})
    def path(name):
        return 'horizontal_top.l{}.u_{}'.format(1 if name == 'mux' else 3 if name == 'out' else 2, name)
    links = [('mux', 'gate'), ('gate', 'occ'), ('occ', 'div'), ('div', 'out'), ('gate', 'early')]
    golden = {'nodes': sorted(path(name) for name in ('mux', 'gate', 'occ', 'div', 'early', 'out')),
              'edges': sorted([path(a), 'o', path(b), 'i'] for a, b in links),
              'terminals': sorted(path(name) for name in ('early', 'out'))}
    write_json(out / 'config.json', config)
    write_json(out / 'golden.json', golden)
    return config, golden


def check_horizontal(app):
    view = app.diagram.model
    assert isinstance(view, IndexedGraphView)
    same = [edge for edge in view.edges if edge['layer_delta'] == 0]
    assert same
    for edge in same:
        assert view.positions[edge['source']][0] < view.positions[edge['target']][0]
    assert len(view.nodes) == len(app.graph['nodes'])
    assert len(view.edges) == len(app.graph['edges'])
    return {'same_layer_edges': len(same), 'layer_columns': view.layer_columns,
            'instances': len(view.nodes), 'edges': len(view.edges)}


def click_wire(app):
    diagram = app.diagram
    for item in diagram.canvas.find_withtag('wire'):
        x1, y1, x2, y2 = diagram.canvas.coords(item)
        x, y = int((x1 + x2) / 2), int((y1 + y2) / 2)
        hit = diagram._hit(x, y)
        if hit and hit[0] == 'edge':
            diagram.canvas.event_generate('<ButtonPress-1>', x=x, y=y)
            diagram.canvas.event_generate('<ButtonRelease-1>', x=x, y=y)
            pump(app)
            evidence = app.evidence_text.get('1.0', 'end')
            edge = diagram.model.actual_edges(hit[1][0])[0]
            assert list(edge['evidence']['driver'])[0]['name'] in evidence
            return
    raise AssertionError('No visible wire accepted a click')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / 'validation/horizontal-layout'))
    parser.add_argument('--extract', action='store_true', help='Run real Verdi through the GUI before layout checks')
    parser.add_argument('--soc', help='Optional existing SoC JSON; this checks display, not a new SoC extraction')
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    errors, checks = [], {}
    app = ClockTreeApp(runs=out / 'gui-runs', remember=False)
    app.geometry('1840x1000+20+20')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title, error: errors.append(title + ': ' + str(error))
    checks['tk'] = app.tk.call('info', 'patchlevel')
    try:
        pump(app)
        cases = [('compact-clock', compact_fixture), ('clock-chain', generate_clock), ('reset-chain', generate_reset)]
        for name, generate in cases:
            if args.extract:
                config, golden = generate(out / 'fixtures' / name)
                app.set_config(config)
                app.run_button.invoke()
                pump(app, lambda: not app.busy)
                assert not errors, errors
                compare(app.graph, golden)
                app.export_result(out / (name + '.json'))
            else:
                app.load_result(out / (name + '.json'))
                pump(app, lambda: not app.busy)
                assert not errors, errors
            assert isinstance(app.graph, IndexedGraph)
            checks[name] = check_horizontal(app)
            app.show_log(False)
            app.diagram.fit()
            pump(app)
            app.export_svg(out / (name + '.svg'))
            bands = ET.parse(str(out / (name + '.svg'))).findall('.//{http://www.w3.org/2000/svg}rect[@class="layer-band"]')
            assert len(bands) == len(app.layers)
            assert len(read_graph(out / (name + '.json'))['edges']) == len(app.graph['edges'])
            if name == 'compact-clock':
                view = app.diagram.model
                chain = ['horizontal_top.l2.u_' + suffix for suffix in ('gate', 'occ', 'div')]
                assert len({view.positions[key][1] for key in chain}) == 1
                assert view.layer_columns == {1: 1, 2: 3, 3: 1}
                assert view.positions['horizontal_top.l2.u_early'][1] > view.positions[chain[1]][1]
                click_wire(app)
                capture(app, out / 'horizontal-workbench.png')
                app.toggle_focus(); pump(app)
                app.diagram.fit(); pump(app)
                capture(app, out / 'horizontal-clock.png')
                app.toggle_focus(); pump(app)
                app.diagram.zoom(1.5); pump(app)
                before = app.diagram.ox
                app.diagram.minimap.event_generate('<Button-1>', x=140, y=40)
                pump(app)
                assert app.diagram.ox != before
                app.diagram.locate(view.original['horizontal_top.l3.u_out'])
                pump(app)
                assert 'horizontal_top.l3.u_out' in app.prop_text.get('1.0', 'end')
                node = app.diagram.model.positions['horizontal_top.l3.u_out']
                x, y = app.diagram.point(node[0] + node[2]/2, node[1] + node[3]/2)
                assert 0 < x < app.diagram.canvas.winfo_width() and 44 < y < app.diagram.canvas.winfo_height()
                app.diagram.mode, app.diagram.depth = 'hierarchy', 2
                app.diagram.rebuild(); pump(app)
                group = next(node for node in app.diagram.model.nodes.values() if node.get('aggregate'))
                app.diagram.expand(group); pump(app)
                assert len(app.diagram.model.nodes) == 4 and app.diagram.model.layer_columns[2] == 3
                checks['interaction'] = dict(wire_evidence=True, zoom=True, minimap=True, locate=True, aggregate_expand=True)
            else:
                app.diagram.locate(next(node for node in app.graph['nodes'] if node['id'].endswith('u_occ0' if name == 'clock-chain' else 'u_mux_mid')))
                pump(app)
                capture(app, out / (name + '-located.png'))
        if args.soc:
            staged = out / 'recorded-soc.json'
            shutil.copy2(args.soc, str(staged))
            app.load_result(staged); pump(app, lambda: not app.busy)
            assert isinstance(app.graph, IndexedGraph)
            checks['soc'] = {'instances': len(app.graph['nodes']), 'edges': len(app.graph['edges']), 'new_extraction': False}
            assert app.diagram.mode == 'hierarchy' and len(app.diagram.model.nodes) <= OVERVIEW_LIMIT
            capture(app, out / 'soc-overview.png')
            node = next(node for node in app.graph['nodes'] if node['terminal'])
            app.diagram.locate(node); pump(app)
            assert node['id'] in app.prop_text.get('1.0', 'end')
            checks['soc'].update(overview=True, locate=True)
        assert not errors, errors
    finally:
        app.destroy()
    checks.update({'pass': True, 'real_extraction': args.extract, 'callback_errors': errors,
                   'screenshots': 'actual native Tk X11 pixels'})
    write_json(out / 'acceptance.json', checks)
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
