"""Real NPI acceptance of bounded enumeration, tracing and boundary labels."""
import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.config import validate_config
from clocktree.extract import extract


RTL = '''
module scope_mux(input a,b,s, output y); assign y=s?b:a; endmodule
module scope_gate(input i,en, output o); assign o=i&en; endmodule
module scope_outgate(input i,en, output o); assign o=i&en; endmodule
module scope_occ(input i,en, output o); reg pass=0; always @(negedge i) pass<=en; assign o=i&pass; endmodule
module scope_div(input i, output reg o=0); always @(posedge i) o<=~o; endmodule
module scope_rmux(input a,b,s, output y); assign y=s?b:a; endmodule
module scope_sync(input rst_ni,clk_i, output rst_no); assign rst_no=rst_ni; endmodule
module outside_buf(input i, output o); assign o=i; endmodule
module scope_domain(input clk_a,clk_b,rst_a,rst_b,en,sel,clk_link_i,rst_link_i,
 output clk_link_o,rst_link_o,clk_o,rst_o,output reg [1:0] observed=0);
 wire m,g,occ,div,detour,r,rs0,rs1,rd;
 scope_mux u_mux(.a(clk_a),.b(clk_b),.s(sel),.y(m));
 scope_gate u_gate(.i(m),.en(en),.o(g));
 scope_occ u_occ(.i(g),.en(en),.o(occ));
 scope_div u_div(.i(occ),.o(div));
 scope_outgate u_out(.i(div),.en(en),.o(clk_o));
 scope_gate u_detour(.i(clk_link_i),.en(en),.o(detour));
 assign clk_link_o=m;
 scope_rmux u_rmux(.a(rst_a),.b(rst_b),.s(sel),.y(r));
 scope_sync u_rs0(.rst_ni(r),.clk_i(clk_a),.rst_no(rs0));
 scope_sync u_rs1(.rst_ni(rs0),.clk_i(clk_a),.rst_no(rs1));
 scope_sync u_rout(.rst_ni(rs1),.clk_i(clk_a),.rst_no(rst_o));
 scope_sync u_rdetour(.rst_ni(rst_link_i),.clk_i(clk_a),.rst_no(rd));
 assign rst_link_o=r;
 always @(posedge detour or negedge rd) if(!rd) observed<=0; else observed<=observed+1;
endmodule
'''


def fixture(directory, generated=False):
    directory.mkdir(parents=True, exist_ok=True)
    top = '''module scope_top(input clk_a,clk_b,rst_a,rst_b,en,sel,output [7:0] result);
wire [1:0] co,ro,clo,cli,rlo,rli;
'''
    if generated:
        top += 'genvar k; generate for(k=0;k<2;k=k+1) begin:g\n'
        names = [('domain', 'k', 'result[k*2+:2]')]
    else:
        names = [('domain', '0', 'result[1:0]'), ('domain_extra', '1', 'result[3:2]')]
    for name, index, observed in names:
        top += '''scope_domain {name}(.clk_a(clk_a),.clk_b(clk_b),.rst_a(rst_a),.rst_b(rst_b),.en(en),.sel(sel),
 .clk_link_o(clo[{i}]),.clk_link_i(cli[{i}]),.rst_link_o(rlo[{i}]),.rst_link_i(rli[{i}]),
 .clk_o(co[{i}]),.rst_o(ro[{i}]),.observed({observed}));
 outside_buf clock_detour_{name}(.i(clo[{i}]),.o(cli[{i}]));
 outside_buf reset_detour_{name}(.i(rlo[{i}]),.o(rli[{i}]));
'''.format(name=name, i=index, observed=observed)
    if generated:
        top += 'end endgenerate\n'
    top += 'reg q=0; always @(posedge co[0] or negedge ro[0]) if(!ro[0])q<=0;else q<=~q; assign result[7:4]={3\'b0,q}; endmodule\n'
    source = directory / 'fixture.sv'
    source.write_text(RTL + top, encoding='utf-8')
    filelist = directory / 'rtl.f'
    filelist.write_text(str(source) + '\n', encoding='utf-8')
    scope = 'scope_top.g[0].domain' if generated else 'scope_top.domain'
    return scope, filelist


def config(scope, filelist, mode, strict=True):
    def rule(module, kind, inputs, output):
        prefix = 'reset' if mode == 'reset' else 'clock'
        return {'module': module, 'kind': kind, prefix + '_inputs': inputs, prefix + '_output': output}
    rules = ([rule('scope_mux', 'mux', ['a', 'b'], 'y')],
             [rule('scope_gate', 'gate', ['i'], 'o')],
             [rule('scope_occ', 'occ', ['i'], 'o'), rule('scope_div', 'div', ['i'], 'o')],
             [rule('scope_outgate', 'gate', ['i'], 'o')]) if mode == 'clock' else (
             [rule('scope_rmux', 'mux', ['a', 'b'], 'y')],
             [rule('scope_sync', 'rsy_sync', ['rst_ni'], 'rst_no')], [])
    cfg = {'tree_type': mode, 'design': {'top': 'scope_top', 'filelist': str(filelist)},
           'layers': [{'index': i + 1, 'hierarchy': scope, 'modules': value} for i, value in enumerate(rules)]}
    if strict:cfg['trace_scope'] = scope
    return validate_config(cfg)


def run(cfg, directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'input.json'
    path.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
    return extract(path, directory)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=str(ROOT / 'validation/scoped-trace'))
    args = parser.parse_args()
    out = Path(args.out).resolve()
    checks = []
    for generated in (False, True):
        suffix = 'generate' if generated else 'module'
        scope, filelist = fixture(out / suffix / 'fixture', generated)
        for mode in ('clock', 'reset'):
            cfg = config(scope, filelist, mode)
            bounded = run(cfg, out / suffix / (mode + '-bounded'))
            assert bounded['summary']['connectivity_complete'], bounded['diagnostics']
            assert bounded['summary']['topology_valid'], bounded['diagnostics']
            expected = {'clock': [('u_mux', 'y', 'u_gate', 'i'), ('u_gate', 'o', 'u_occ', 'i'),
                                   ('u_occ', 'o', 'u_div', 'i'), ('u_div', 'o', 'u_out', 'i')],
                        'reset': [('u_rmux', 'y', 'u_rs0', 'rst_ni'), ('u_rs0', 'rst_no', 'u_rs1', 'rst_ni'),
                                  ('u_rs1', 'rst_no', 'u_rout', 'rst_ni')]}[mode]
            observed = sorted((e['source'].split('.')[-1], e['source_pin'], e['target'].split('.')[-1], e['target_pin']) for e in bounded['edges'])
            assert observed == sorted(expected), observed
            assert all(n['id'].startswith(scope + '.') for n in bounded['nodes'])
            assert all(q['scope'] == scope for q in bounded['metadata']['scoped_queries'])
            assert 'design_instances' not in bounded['metadata']
            assert any(n['scope_output_boundary'] for n in bounded['nodes'])
            assert all(not n['terminal'] for n in bounded['nodes'] if n['scope_output_boundary'])
            for item in bounded['boundaries'] + [r for e in bounded['edges'] for r in ({'path': p} for p in e['evidence'].values())]:
                for step in item.get('path', []):
                    assert not step.get('name') or step['name'].startswith(scope + '.'), step
                    assert not step.get('net') or step['net'].startswith(scope + '.'), step
            legacy = run(config(scope, filelist, mode, strict=False), out / suffix / (mode + '-legacy'))
            assert legacy['summary']['connectivity_complete'] and legacy['summary']['topology_valid'], legacy['diagnostics']
            assert {n['id'] for n in legacy['nodes']} == {n['id'] for n in bounded['nodes']}
            assert len(legacy['edges']) == len(expected) + 1
            # Blank instance scopes use the independently specified trace root.
            blank = copy.deepcopy(cfg)
            for layer in blank['layers']:layer['hierarchy'] = ''
            blank_graph = run(blank, out / suffix / (mode + '-blank-rules'))
            assert {e['id'] for e in blank_graph['edges']} == {e['id'] for e in bounded['edges']}
            checks.append({'case': suffix + '-' + mode, 'strict': bounded['summary'],
                           'legacy_edges': len(legacy['edges']), 'blank_rules_use_root': True})
    bad = copy.deepcopy(cfg)
    bad['trace_scope'] = 'scope_top.missing'
    for layer in bad['layers']:layer['hierarchy'] = ''
    invalid = run(bad, out / 'missing-root')
    assert not invalid['summary']['connectivity_complete'] and not invalid['nodes']
    assert any(d['code'] == 'scope_not_found' for d in invalid['diagnostics'])
    # Instance search only examines direct children, excluding the root itself.
    leaf_scope = 'scope_top.domain.u_mux'
    leaf_filelist = out / 'module/fixture/rtl.f'
    leaf = config('scope_top.domain', leaf_filelist, 'clock')
    leaf['trace_scope'] = leaf_scope
    leaf['layers'] = [dict(index=1, hierarchy=leaf_scope, modules=leaf['layers'][0]['modules'])]
    leaf_graph = run(leaf, out / 'leaf-root')
    assert not leaf_graph['summary']['connectivity_complete'] and not leaf_graph['nodes']
    assert any(d['code']=='missing_module' for d in leaf_graph['diagnostics'])
    gen = config('scope_top.g[0].domain', out / 'generate/fixture/rtl.f', 'clock')
    gen['trace_scope'] = 'scope_top.g[0]'
    gen_graph = run(gen, out / 'generate-root')
    assert gen_graph['summary']['connectivity_complete'], gen_graph['diagnostics']
    # Literal drivers are values within the design, not hierarchy escapes.
    const_scope, const_list = fixture(out / 'constants/fixture')
    const_source = const_list.parent / 'fixture.sv'
    const_source.write_text(const_source.read_text().replace('.b(clk_b)', ".b(1'b0)").replace('.b(rst_b)', ".b(1'b1)"), encoding='utf-8')
    for mode in ('clock', 'reset'):
        const_graph = run(config(const_scope, const_list, mode), out / 'constants' / mode)
        assert const_graph['summary']['connectivity_complete'], const_graph['diagnostics']
        assert any(b['status'] == 'constant' for b in const_graph['boundaries']), const_graph['boundaries']
    report = {'pass': True, 'cases': checks, 'missing_root_rejected': True,
              'search_root_itself_excluded': True, 'generate_root_supported': True, 'constant_inputs_preserved': True}
    (out / 'check.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':main()
