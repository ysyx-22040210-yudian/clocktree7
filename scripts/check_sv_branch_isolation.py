"""Native fault injection and large mixed-branch SV acceptance on the EDA VM."""
import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
from collections import Counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from check_l1_interfaces import compile_kdb
from check_target_mode import run as extract
import check_trace_timeout
from clocktree.config import tcl_string
from clocktree.register_trace import run as trace_registers, seeds_from_graph
from clocktree.session import read_graph
from clocktree.storage import JSONReader, write_graph
from clocktree.sv_templates import prepare


TOP = 'sv_branch_top'
GOOD = 'a_ok'
BAD = 'b_fault'
LATE = 'c_late'
TEMPLATE = 'wire check_$var_clkip = $var_gt0;\ninitial $display("%b", $var_pll0, $var_clkip);'
TEMPLATES = {'with_mux':'', 'without_mux':TEMPLATE}
RTL = '''
module iso_gate(input i, en, output o);
  assign o = i & en;
endmodule
module iso_wire(input a, b, output x, y);
  assign x = a;
  assign y = a & b;
endmodule
module iso_sink(input a_ok, b_fault, c_late);
endmodule
module sv_branch_top(input in_clk, other_clk, cond, d, output [4:0] observe);
  reg enable_q;
  always @(posedge in_clk) enable_q <= d;
  wire clk_ok, clk_fault, clk_late, clk_through, clk_logic;
  iso_gate u_good(.i(in_clk), .en(enable_q), .o(clk_ok));
  iso_gate u_bad(.i(in_clk & other_clk), .en(cond), .o(clk_fault));
  iso_gate u_late(.i(other_clk), .en(enable_q), .o(clk_late));
  iso_wire u_wire(.a(in_clk), .b(other_clk), .x(clk_through), .y(clk_logic));
  iso_sink u_sink(.a_ok(clk_ok), .b_fault(clk_fault), .c_late(clk_late));
  assign observe = {clk_ok, clk_fault, clk_late, clk_through, clk_logic};
endmodule
'''


def audit_export(graph, registers, directory, rtl, expected):
    plan = prepare(graph, registers, directory/'plan')
    stats = plan.export(TEMPLATES, directory/'generated.sv')
    report = json.loads(Path(stats['mapping']).read_text(encoding='utf-8'))
    rows = {row['pin']: row for row in report['branches']}
    assert {pin for pin, row in rows.items() if row['ready']} == expected, rows
    assert stats['blocks']==len(expected) and stats['skipped']==len(rows)-len(expected)
    assert not report['source']['connectivity_complete']
    assert rows[GOOD]['values']['var_gt0'] == TOP+'.enable_q'
    assert rows[GOOD]['values']['var_clkip'] == TOP+'.clk_ok'
    if LATE in expected:
        assert rows[LATE]['values']['var_clkip'] == TOP+'.clk_late'
    if 'x' in expected:
        assert rows['x']['values']['var_clkip'] == TOP+'.clk_through'
    code = Path(stats['sv']).read_text(encoding='utf-8')
    assert code.count('// CT7 branch ') == stats['blocks']
    source = directory/'checks.sv'
    source.write_text('module gen_checks;\n'+code+'\nendmodule\n')
    compile_kdb(directory/'code-compile', [rtl,source], [TOP,'gen_checks'])
    return rows, {'generated':sorted(expected), 'written':stats['blocks'], 'skipped':stats['skipped'],
                  'issues':{pin:row['issues'] for pin,row in rows.items() if not row['ready']}, 'vcs_compiled':True}


def inject_binding_fault(directory, action):
    """Instrument only the scratch Verdi worker, after real elab import."""
    marker = directory/'binding-fault-injected'
    launch = subprocess.Popen

    def popen(command, *args, **kwargs):
        if isinstance(command, list) and command[-1]=='register_worker.tcl':
            script = Path(kwargs['cwd'])/'register_worker.tcl'
            hook = '''
rename cr_port ct_test_original_port
proc cr_port {instance pin} {
    set marker %s
    if {$instance eq "sv_branch_top.u_sink" && $pin eq "b_fault" && ![file exists $marker]} {
        set f [open $marker w]; puts $f $pin; close $f
        %s
    }
    ct_test_original_port $instance $pin
}
''' % (tcl_string(str(marker)), action)
            code = script.read_text()
            trigger = 'set cr_socket [socket '
            assert code.count(trigger)==1
            script.write_text(code.replace(trigger,hook+'\n'+trigger))
        return launch(command, *args, **kwargs)

    return marker, patch('clocktree.register_worker.subprocess.Popen', popen)


def native(out):
    rtl = out/'fixture.sv'
    rtl.write_text(RTL)
    elab = compile_kdb(out/'fixture-kdb', [rtl], [TOP])
    rules = [
        dict(module='iso_gate',kind='sync_gate',clock_inputs=['i'],clock_output='o',aux_ports={'gt':'en'}),
        dict(module='iso_wire',kind='wire',clock_inputs=['a','b'],clock_outputs=['x','y']),
        dict(module='iso_sink',kind='subsystem',clock_inputs=[GOOD,BAD,LATE]),
    ]
    config = {'design':{'top':TOP,'elab':str(elab)},
              'layers':[dict(index=i,hierarchy=TOP,modules=[rule]) for i,rule in enumerate(rules,1)]}
    first, _ = extract(dict(config, extraction_mode='target'), out/'base-tree')
    request = out/'register-request.json'
    write_graph(request, {'config':first['config'], 'seeds':list(seeds_from_graph(first))})
    registers = out/'registers'
    reg = trace_registers(request, registers)
    assert reg['summary']['finished'] and not reg['summary']['complete'], reg['summary']
    cases = {}
    for mode in ('target','full'):
        directory = out/('timeout-'+mode)
        hook = check_trace_timeout.HOOK.replace(r'\.m[0-9]+$',r'\.u_sink$')
        target_hook = check_trace_timeout.TARGET_HOOK.replace('pin trace_id}', 'pin trace_id {direction driver}}')
        target_hook = target_hook.replace('$pin driver', '$pin $direction').replace('$pin $trace_id]', '$pin $trace_id $direction]')
        with patch.multiple(check_trace_timeout, HOOK=hook, TARGET_HOOK=target_hook):
            extraction, original = check_trace_timeout.instrument(BAD, 5)
            try:
                graph, _ = extract(dict(config, extraction_mode=mode, port_trace_timeout_s=1.5), directory/'tree')
            finally:
                extraction.run_verdi = original
        assert graph['summary']['trace_timeout_instances']==1, graph['summary']
        sink = next(n for n in graph['nodes'] if n['id']==TOP+'.u_sink')
        assert sink['port_trace_status']=={GOOD:'completed', BAD:'trace_timeout', LATE:'trace_skipped_timeout'}, sink
        assert graph['metadata']['memory_protection']['timeout_restart_count']==1
        assert 'UNCOMMITTED_TIMEOUT' not in (directory/'tree/raw.jsonl').read_text()
        expected = {GOOD,'x'} if mode=='full' else {GOOD}
        rows, case = audit_export(graph, registers, directory, rtl, expected)
        assert any('端口超时' in s for s in rows[BAD]['issues'])
        assert any('随实例跳过' in s for s in rows[LATE]['issues'])
        case.update(trace_status=sink['port_trace_status'], rolled_back_uncommitted_trace=True)
        cases['timeout-'+mode] = case

    for label, action in [('binding-exit','exit 77'), ('binding-deadline','exec /bin/sleep 5')]:
        directory = out/label
        graph = dict(first)
        graph['config'] = dict(first['config'], port_trace_timeout_s=1.5 if label=='binding-deadline' else 0)
        graph['nodes'] = [dict(n, port_connections=dict(n.get('port_connections', {}))) for n in first['nodes']]
        sink = next(n for n in graph['nodes'] if n['id']==TOP+'.u_sink')
        for pin in (BAD,LATE):sink['port_connections'].pop(pin)
        marker, scope = inject_binding_fault(directory, action)
        with scope:
            rows, case = audit_export(graph, registers, directory, rtl, {GOOD,LATE})
        assert marker.is_file()
        assert any('回查 elab 连接失败' in issue for issue in rows[BAD]['issues']), rows[BAD]
        log = (directory/'plan/connection-worker/verdi.log').read_text()
        assert log.count('REGISTER_WORKER ')==2
        case['worker_launches'] = 2
        cases[label] = case
    write_graph(out/'acceptance.json', {'passed':True, 'real_verdi':True, 'cases':cases})
    print('SV_BRANCH_NATIVE_ISOLATION_ACCEPTED',flush=True)


LANES = ('a0','a1','a2','a3','b_edge','c_register','d_timeout','e_skipped')


def mixed_graph(source, groups):
    native_graph = read_graph(source/'base-tree/clocktree.json')
    graph = dict(native_graph)
    paths = {TOP+'.'+name for name in ('u_good','u_bad','u_wire')}
    nodes = [dict(n, port_connections=dict(n.get('port_connections', {}))) for n in native_graph['nodes'] if n['id'] in paths]
    good, bad, wire = (next(n for n in nodes if n['id']==TOP+'.'+name) for name in ('u_good','u_bad','u_wire'))
    bad['port_connections']['i'] = good['port_connections']['i']
    wire['internal_connections'] = [dict(input='a',output='x',status='verified'),dict(input='b',output='y',status='verified')]
    for n in nodes:n['terminal'] = False
    edges = [dict(id='upstream-x',source=good['id'],source_pin='o',target=wire['id'],target_pin='a'),
             dict(id='upstream-y',source=bad['id'],source_pin='o',target=wire['id'],target_pin='b')]
    diagnostics, boundaries = [], []
    for index in range(groups):
        instance = '{}.sink_{:08d}'.format(TOP,index)
        bindings = {pin:{'status':'connected','full_name':instance+'.clk_'+pin} for pin in LANES}
        statuses = dict.fromkeys(LANES,'completed')
        statuses.update(d_timeout='trace_timeout',e_skipped='trace_skipped_timeout')
        decision = dict(instance=instance, pin='d_timeout', direction='driver')
        nodes.append(dict(id=instance,kind='subsystem',layer=3,inputs=list(LANES),terminal=False,
                          port_connections=bindings,port_trace_status=statuses,trace_timeout=decision))
        diagnostics.append(dict(decision,code='instance_trace_timeout'))
        for pin in LANES[:-2]:
            edge = dict(id=instance+'.'+pin,source=wire['id'],source_pin='y' if pin=='c_register' else 'x',
                        target=instance,target_pin=pin)
            edges.append(edge)
            if pin=='b_edge':diagnostics.append(dict(code='trace_mismatch',edge=edge['id']))
        for pin in LANES[-2:]:
            boundaries.append(dict(instance=instance,pin=pin,direction='driver',status=statuses[pin]))
    graph.update(nodes=nodes,edges=edges,diagnostics=diagnostics,boundaries=boundaries)
    graph['summary'] = dict(graph['summary'], instances=len(nodes), edges=len(edges), unresolved=len(boundaries),
                            diagnostics=len(diagnostics), connectivity_complete=False)
    if hasattr(native_graph,'store'):native_graph.store.close()
    return graph


def audit_scale(mapping, output, groups):
    reasons = {'b_edge':'证据不一致','c_register':'缺少寄存器映射',
               'd_timeout':'端口超时','e_skipped':'随实例跳过'}
    observed = Counter()
    with mapping.open(encoding='utf-8') as report, output.open(encoding='utf-8') as code:
        reader = JSONReader(report)
        reader.take('{')
        for key in ('schema_version','source'):
            assert reader.value()==key
            reader.take(':');reader.skip();reader.take(',')
        assert reader.value()=='branches'
        reader.take(':');reader.take('[')
        assert code.readline().startswith('// Generated by 观钟王')
        assert code.readline().startswith('// DFT zero assumptions:')
        for index in range(groups):
            instance = '{}.sink_{:08d}'.format(TOP,index)
            for pin in LANES:
                if index or pin!=LANES[0]:reader.take(',')
                row = reader.value()
                assert (row['instance'],row['pin']) == (instance,pin), row
                observed[pin] += 1
                if pin in reasons:
                    assert not row['ready'] and any(reasons[pin] in issue for issue in row['issues']),row
                    continue
                signal = instance+'.clk_'+pin
                assert row['ready'] and row['issues']==[] and row['values']=={
                    'var_clkip':signal,'var_gt0':TOP+'.enable_q','var_pll0':TOP+'.in_clk'}, row
                label = signal.replace('.','_')
                expected = ('\n// CT7 branch {} | {}.{} | {}\nwire check_{} = {}.enable_q;\n'
                            'initial $display("%b", {}.in_clk, {});\n').format(
                                row['id'],instance,pin,signal,label,TOP,TOP,signal)
                assert code.read(len(expected))==expected, (instance,pin)
        reader.take(']')
        assert code.read()==''
    return dict(observed)


def scale(out, source, count):
    import resource
    assert count > 0 and count % len(LANES) == 0
    groups = count//len(LANES)
    graph = mixed_graph(source, groups)
    start = time.monotonic()
    plan = prepare(graph, source/'registers', out/'plan', False)
    plan_seconds = time.monotonic()-start
    graph.clear()
    output = out/'mixed.sv'
    start = time.monotonic()
    stats = plan.export(TEMPLATES, output)
    export_seconds = time.monotonic()-start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    assert (stats['terminals'],stats['blocks'],stats['skipped']) == (count,count//2,count//2), stats
    cases = audit_scale(Path(stats['mapping']),output,groups)
    write_graph(out/'acceptance.json', {'passed':True, 'synthetic':True, 'terminals':count, 'subsystems':groups,
                'generated':stats['blocks'], 'skipped':stats['skipped'], 'per_lane_verified':cases,
                'plan_seconds':plan_seconds, 'export_seconds':export_seconds, 'peak_rss_mib':peak})
    print('SV_BRANCH_SCALE_ISOLATION_ACCEPTED '+str(count),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--native-source',type=Path,help='Run synthetic scale using this native acceptance directory')
    parser.add_argument('--terminals',type=int,default=100000)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True)
    if args.native_source:scale(out,args.native_source.resolve(),args.terminals)
    else:native(out)


if __name__=='__main__':main()
