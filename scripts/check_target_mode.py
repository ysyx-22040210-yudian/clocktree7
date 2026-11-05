"""Native Verdi acceptance of the Language Model target extraction mode."""
import argparse
import copy
import importlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.storage import write_graph
from clocktree.graph import read_events
from clocktree.config import tcl_string
from make_flexible_fixture import generate as clock_fixture
from make_reset_fixture import generate as reset_fixture
from check_high_fanout import fixture as fan_fixture
from check_scoped_trace import fixture as scope_fixture, config as scope_config

GUARD = '''
proc ct_test_no_netlist_or_load {args} {error "FORBIDDEN_NETLIST_OR_LOAD: $args"}
foreach c [concat [info commands npi_nl_*] [info commands ::npi_L1::npi_nl_*] [info commands ::npi_L1::npi_trace_load*]] {
 trace add execution $c enter ct_test_no_netlist_or_load
}
puts "CT7_LANGUAGE_GUARD_ACTIVE"
'''


def run(cfg, directory, probes=()):
    directory.mkdir(parents=True, exist_ok=True)
    cfg = copy.deepcopy(cfg)
    cfg.setdefault('extraction_mode', 'target')
    cfg.setdefault('memory_limit_gib', 8)
    write_graph(directory/'input.json', cfg)
    extraction = importlib.import_module('clocktree.extract')
    original = extraction.run_verdi
    def checked(command, work, *args, **kwargs):
        if cfg['extraction_mode'] == 'target':
            script = Path(work)/'clocktree-extract.tcl'
            code = script.read_text(encoding='utf-8')
            guard=GUARD
            for name in probes:
                guard+='\nif {[npi_handle_by_name -name '+tcl_string(name)+' -scope ""] eq ""} {error "Missing background instance"}\n'
                guard+='puts '+tcl_string('CT7_SCALE_PROBE '+name)+'\n'
            code = code.replace('    set top_hdl [npi_handle_by_name', guard+'\n    set top_hdl [npi_handle_by_name')
            script.write_text(code, encoding='utf-8')
        return original(command, work, *args, **kwargs)
    extraction.run_verdi = checked
    started = time.monotonic()
    try:
        graph = extraction.extract(directory/'input.json', directory)
    finally:
        extraction.run_verdi = original
    if cfg['extraction_mode'] == 'target':
        log = (directory/'verdi.log').read_text(encoding='utf-8')
        assert 'CT7_LANGUAGE_GUARD_ACTIVE' in log
        assert 'FORBIDDEN_NETLIST_OR_LOAD:' not in log
        assert all('CT7_SCALE_PROBE '+name in log for name in probes)
        assert all(set(e['evidence']) == {'driver'} for e in graph['edges'])
        assert all(e.get('direction') != 'load' for e in read_events(directory/'raw.jsonl'))
    report = dict(seconds=round(time.monotonic()-started,3), summary=graph['summary'],
                  rss_peak_gib=graph['metadata']['memory_protection'].get('peak_rss_gib'),
                  language_stats=graph['metadata'].get('language_trace_stats',{}),elaborated_probes=list(probes))
    return graph, report


def edges(graph):
    return sorted([e['source'],e['source_pin'],e['target'],e['target_pin']] for e in graph['edges'])


def golden_check(graph, golden):
    assert graph['summary']['connectivity_complete'], (graph['diagnostics'],list(graph['boundaries']))
    assert sorted(n['id'] for n in graph['nodes']) == golden['nodes']
    assert edges(graph) == golden['edges'], edges(graph)
    assert sorted(n['id'] for n in graph['nodes'] if n['terminal']) == golden['terminals']


def matrix(out):
    checks=[]
    def record(name, report):
        checks.append(dict(case=name,passed=True,**report))
        write_graph(out/'matrix.json', {'passed':False,'complete':False,'cases':checks})
        print(json.dumps(checks[-1]), flush=True)
    for mode, factory in [('clock',clock_fixture),('reset',reset_fixture)]:
        for layers in (2,10):
            directory=out/('{}-{}'.format(mode,layers))
            cfg,golden=factory(directory/'fixture',layers)
            graph,report=run(cfg,directory/'target');golden_check(graph,golden)
            record('{}-{}-target'.format(mode,layers),report)
            if layers==2:
                cfg['extraction_mode']='full'
                full,report=run(cfg,directory/'full');golden_check(full,golden)
                assert edges(full)==edges(graph)
                assert all(set(e['evidence'])=={'driver','load'} for e in full['edges'])
                record(mode+'-full-equivalence',report)
    for generated in (False,True):
        scope,filelist=scope_fixture(out/('scope-'+str(generated))/'fixture',generated)
        for mode in ('clock','reset'):
            for bounded in (True,False):
                cfg=scope_config(scope,filelist,mode,bounded)
                graph,report=run(cfg,out/('scope-{}-{}-{}'.format(generated,mode,bounded)))
                assert graph['summary']['connectivity_complete'], (graph['diagnostics'],list(graph['boundaries']))
                expected=4 if mode=='clock' else 3
                assert len(graph['edges'])==expected+(0 if bounded else 1), edges(graph)
                assert len(graph['nodes'])==(6 if mode=='clock' else 5)
                assert all(q['scope']==scope for q in graph['metadata']['scoped_queries'])
                if bounded:
                    assert any(b['status']=='scope_boundary' for b in graph['boundaries'])
                    assert all(not n['scope_output_boundary'] for n in graph['nodes'])
                record('scope-{}-{}-{}'.format(generated,mode,bounded),report)
    from check_port_connections import fixture as ports_fixture, check as ports_check
    path=ports_fixture(out/'ports/fixture')
    graph,report=run(json.loads(path.read_text()),out/'ports/run')
    ports_check(graph)
    assert not graph['summary']['connectivity_complete']
    assert any(b['status']=='unsupported_expression' for b in graph['boundaries'])
    assert not any(n['terminal'] for n in graph['nodes'])
    record('port-labels-expressions-constants-open-bits',report)
    custom_cases(out,run,record)
    array_cases(out,run,record)
    write_graph(out/'matrix.json', {'passed':True,'complete':True,'cases':checks})
    return checks


def array_cases(out,run,record):
    directory=out/'instance-arrays';directory.mkdir(parents=True,exist_ok=True)
    (directory/'rtl.sv').write_text('''module src(input i,output o);assign o=i;endmodule
module gate1(input i,en,output o);assign o=i&en;endmodule
module arr_top(input a,b,en,output [7:0] q,output z);
wire [1:0] bus;wire [3:0] d;
src s[1:0](.i({a,b}),.o(bus));
gate1 dec[3:0](.i({2{bus}}),.en(en),.o(d));
gate1 asc[4:7](.o(q[3:0]),.en(en),.i({bus[0],bus[1],bus[1],bus[0]}));
gate1 last(.i(d[2]),.en(en),.o(z));assign q[7:4]=d;
endmodule
''')
    (directory/'rtl.f').write_text(str(directory/'rtl.sv')+'\n')
    cfg={'design':{'top':'arr_top','filelist':str(directory/'rtl.f')},'layers':[
        {'index':1,'hierarchy':'arr_top','modules':[{'module':'src','kind':'div','clock_inputs':['i'],'clock_output':'o'}]},
        {'index':2,'hierarchy':'arr_top','modules':[{'module':'gate1','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]}]}
    expected=[]
    for i in range(4):expected.append(['arr_top.s[{}]'.format(i%2),'o','arr_top.dec[{}]'.format(i),'i'])
    for i,s in [(4,0),(5,1),(6,1),(7,0)]:expected.append(['arr_top.s[{}]'.format(s),'o','arr_top.asc[{}]'.format(i),'i'])
    expected.append(['arr_top.dec[2]','o','arr_top.last','i'])
    for mode in ('target','full'):
        cfg['extraction_mode']=mode;graph,report=run(cfg,directory/mode)
        if mode=='target':
            # Verdi 2018 exposes NIY for concat-connected array members.
            # Native array npiExpr follows source association order, which
            # need not equal npiPortIndex. Never guess that mapping.
            assert not graph['summary']['connectivity_complete']
            assert edges(graph)==[['arr_top.dec[2]','o','arr_top.last','i']]
            assert sum(b['status']=='unsupported_expression' for b in graph['boundaries'])==10
            assert not any(n['terminal'] for n in graph['nodes'])
        else:
            assert graph['summary']['connectivity_complete'],list(graph['boundaries'])
            assert edges(graph)==sorted(expected),edges(graph)
        record('instance-arrays-complex-mapping-'+mode,report)


def custom_cases(out, run, record):
    rtl='''module src(input i,output o);assign o=i;endmodule
module gate1(input i,en,output o);assign o=i&en;endmodule
module vec(input [3:0] i,output [3:0] o);assign o=i;endmodule
module wrap(input wi,en,output wo);gate1 dst(wi,en,wo);endmodule
module top(input a,b,en,output [12:0] q);
wire c,d,alias_c;wire [3:0] bus;wire x3,x2,x1,x0;
src s0(a,c);src s1(b,d);assign alias_c=c;assign bus={c,a,alias_c,b};
vec v(.i({a,c,b,a}),.o({x3,x2,x1,x0}));
gate1 g0(bus[3],en,q[0]);gate1 g1(bus[1],en,q[1]);gate1 g2(x1,en,q[2]);
wrap w(c,en,q[3]);
wire mixed;assign mixed=c;assign mixed=d;gate1 multi(mixed,en,q[4]);
wire inv;assign inv=~c;gate1 invert(inv,en,q[5]);
gate1 open_input(.i(),.en(en),.o(q[6]));
src dangling(a,);assign q[8:7]=2'b0;
wire buffered,inverted;buf transparent_buf(buffered,c);not transparent_not(inverted,c);
gate1 primitive_buf(buffered,en,q[9]);gate1 primitive_not(inverted,en,q[10]);
wire shorted;src s2(a,shorted);assign shorted=d;gate1 direct_multi(shorted,en,q[11]);
gate1 port_invert(~c,en,q[12]);
endmodule
'''
    directory=out/'bits-and-multidriver';directory.mkdir(parents=True,exist_ok=True)
    (directory/'rtl.sv').write_text(rtl)
    (directory/'rtl.f').write_text(str(directory/'rtl.sv')+'\n')
    def rule(module,inputs,output):return {'module':module,'kind':'gate','clock_inputs':inputs,'clock_output':output}
    cfg={'design':{'top':'top','filelist':str(directory/'rtl.f')},'layers':[
        {'index':1,'hierarchy':'top','modules':[rule('src',['i'],'o')]},
        {'index':2,'hierarchy':'top','modules':[rule('vec',['i[2]'],'o[1]'),rule('gate1',['i'],'o')]},
        {'index':3,'hierarchy':'top.w','modules':[rule('gate1',['i'],'o')]}]}
    graph,report=run(cfg,directory/'run')
    expected=[['top.s0','o','top.'+dst,pin] for dst,pin in [('v','i[2]'),('g0','i'),('g1','i'),('w.dst','i'),('multi','i'),('invert','i'),('primitive_buf','i'),('primitive_not','i'),('port_invert','i')]]
    expected += [['top.s1','o','top.multi','i'],['top.v','o[1]','top.g2','i']]
    expected += [['top.s1','o','top.direct_multi','i'],['top.s2','o','top.direct_multi','i']]
    assert edges(graph)==sorted(expected), (edges(graph),list(graph['boundaries']))
    assert graph['summary']['connectivity_complete'], (graph['diagnostics'],list(graph['boundaries']))
    assert any(d['code']=='multiple_drivers' for d in graph['diagnostics'])
    assert graph['summary']['validated_outputs']==len(graph['nodes'])
    record('bits-alias-inversion-wrappers-multiple-drivers-open',report)
    broken=copy.deepcopy(cfg);broken['layers'][0]['modules'][0]['clock_output']='missing'
    graph,report=run(broken,directory/'missing-output')
    assert not graph['summary']['connectivity_complete']
    assert any(d['code']=='pin_validation' for d in graph['diagnostics'])
    record('missing-output-rejected',report)
    directory=out/'symbolic-bits';directory.mkdir(parents=True,exist_ok=True)
    rtl='''package indexes;parameter integer BASE=0;integer runtime_idx;endpackage
module src(input i,output o);assign o=i;endmodule
module gate1(input i,en,output o);assign o=i&en;endmodule
module top(input a,b,en,input idx,output [3:0] q);
import indexes::*;
wire c,d;wire [1:0] clocks;src s0(a,c);src s1(b,d);assign clocks={d,c};
genvar k;generate for(k=0;k<2;k=k+1)begin:g gate1 gg(clocks[BASE+k],en,q[k]);end endgenerate
wire bad;assign bad=c&en;
gate1 dynamic_index(clocks[idx],en,q[2]);gate1 unknown_logic(bad,en,q[3]);
gate1 dynamic_package(clocks[indexes::runtime_idx],en,);
endmodule'''
    (directory/'rtl.sv').write_text(rtl);(directory/'rtl.f').write_text(str(directory/'rtl.sv')+'\n')
    cfg={'design':{'top':'top','filelist':str(directory/'rtl.f')},'layers':[
        {'index':1,'hierarchy':'top','modules':[rule('src',['i'],'o')]},
        {'index':2,'hierarchy':'top.g[0]','modules':[rule('gate1',['i'],'o')]},
        {'index':3,'hierarchy':'top.g[1]','modules':[rule('gate1',['i'],'o')]}]}
    graph,report=run(cfg,directory/'positive')
    assert graph['summary']['connectivity_complete'],list(graph['boundaries'])
    assert edges(graph)==[['top.s0','o','top.g[0].gg','i'],['top.s1','o','top.g[1].gg','i']]
    record('generate-symbolic-bit-cache-isolation',report)
    cfg['layers'].append({'index':4,'hierarchy':'top','modules':[rule('gate1',['i'],'o')]})
    graph,report=run(cfg,directory/'negative')
    assert not graph['summary']['connectivity_complete']
    assert {b['status'] for b in graph['boundaries']} >= {'unsupported_expression','unknown_cell'}
    assert len(graph['edges'])==2
    record('dynamic-index-and-unknown-logic-not-guessed',report)


def stress(out,counts,background):
    checks=[]
    for count in counts:
        path=fan_fixture(out/str(count)/'fixture',count,mode='target')
        graph,report=run(json.loads(path.read_text()),out/str(count)/'run')
        assert graph['summary']['connectivity_complete'], graph['diagnostics']
        expected=['fan_top.domain.g'+str(i) for i in range(count)]+['fan_top.domain.via_bit','fan_top.domain.via_alias']
        assert edges(graph)==sorted(['fan_top.domain.source','o',p,'i'] for p in expected)
        assert len(graph['nodes'])==count+3
        assert report['language_stats']['driver_queries']<15,report
        checks.append(dict(case='fanout',selected_loads=count+2,ordinary_loads=count,passed=True,**report))
        print(json.dumps(checks[-1]),flush=True)
        write_graph(out/'stress.json',{'passed':False,'complete':False,'cases':checks})
    for count in background:
        path=fan_fixture(out/('background-'+str(count))/'fixture',128,mode='target')
        rtl=path.parent/'fixture.sv';code=rtl.read_text()
        code=code.replace('fan_domain domain(.*); endmodule','fan_domain domain(.*); wire [%d:0] unused_q; bulk unused(clk,d,unused_q); endmodule' % (count-1))
        code+='\nmodule bulk(input clk,d,output [%d:0] q);genvar k;generate for(k=0;k<%d;k=k+1)begin:tile fan_sink ff(clk,d,q[k]);end endgenerate endmodule\n' % (count-1,count)
        rtl.write_text(code)
        graph,report=run(json.loads(path.read_text()),path.parent.parent/'run',
                         ['fan_top.unused.tile[0].ff','fan_top.unused.tile[%d].ff'%(count-1)])
        assert graph['summary']['connectivity_complete'],graph['diagnostics']
        assert len(graph['nodes'])==131 and len(graph['edges'])==130
        assert report['language_stats']['driver_queries']<15
        checks.append(dict(case='large-background',background_instances=count,selected_loads=130,ordinary_loads=128,passed=True,**report))
        print(json.dumps(checks[-1]),flush=True)
        write_graph(out/'stress.json',{'passed':False,'complete':False,'cases':checks})
    write_graph(out/'stress.json',{'passed':True,'complete':True,'cases':checks})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--stress',action='store_true')
    parser.add_argument('--counts',type=int,nargs='+',default=[128,1024,8192])
    parser.add_argument('--background',type=int,nargs='*',default=[100000,250000])
    args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.stress:stress(out,args.counts,args.background)
    else:matrix(out)


if __name__=='__main__':main()
