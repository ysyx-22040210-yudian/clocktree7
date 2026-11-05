"""Scale unrelated background, fanout, unique nets and deep RTL independently."""
import argparse
import json
from pathlib import Path
import resource
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.storage import write_graph
from check_high_fanout import fixture as fan_fixture
from check_target_mode import run,edges


def rules(top,filelist,entries):
    return dict(design=dict(top=top,filelist=str(filelist)),trace_scope=top,extraction_mode='target',
                port_trace_timeout_s=30,memory_limit_gib=4,layers=[dict(index=i,hierarchy=scope,modules=[dict(
                    module=module,kind=kind,clock_inputs=['i'],clock_output='o')])
                for i,scope,module,kind in entries])


def unique(directory,count=768):
    directory.mkdir(parents=True)
    lines=['module src(input i,output o);assign o=i;endmodule',
           'module gate1(input i,en,output o);assign o=i&en;endmodule',
           'module unique_top(input clk,en,output [%d:0] q);'%(count-1)]
    expected=[]
    for i in range(count):
        lines.append('wire [4:0] c%d;src s%d(clk,c%d[0]);'%(i,i,i))
        for j in range(4):
            lines.append('gate1 g%d_%d(c%d[%d],en,c%d[%d]);'%(i,j,i,j,i,j+1))
            expected.append(['unique_top.'+('s%d'%i if j==0 else 'g%d_%d'%(i,j-1)),'o','unique_top.g%d_%d'%(i,j),'i'])
        lines.append('assign q[%d]=c%d[4];'%(i,i))
    lines.append('endmodule')
    rtl=directory/'rtl.sv';rtl.write_text('\n'.join(lines));filelist=directory/'rtl.f';filelist.write_text(str(rtl)+'\n')
    return rules('unique_top',filelist,[(1,'unique_top','src','div'),(2,'unique_top','gate1','gate')]),expected,count*5


def deep(directory,depth=40):
    directory.mkdir(parents=True)
    lines=['module src(input i,output o);assign o=i;endmodule',
           'module gate1(input i,en,output o);assign o=i&en;endmodule']
    for i in range(depth):
        instance='gate1 leaf(i,en,o);' if i==depth-1 else 'deep%d u(i,en,o);'%(i+1)
        lines.append('module deep%d(input i,en,output o);%sendmodule'%(i,instance))
    lines.append('module deep_top(input clk,en,output q);wire c;src source(clk,c);deep0 wrap(c,en,q);endmodule')
    rtl=directory/'rtl.sv';rtl.write_text('\n'.join(lines));filelist=directory/'rtl.f';filelist.write_text(str(rtl)+'\n')
    parent='deep_top.wrap'+'.u'*(depth-1)
    return rules('deep_top',filelist,[(1,'deep_top','src','div'),(2,parent,'gate1','gate')]),[['deep_top.source','o',parent+'.leaf','i']],2


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',required=True)
    p.add_argument('--only',choices=['fanout','background','unique','deep']);args=p.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True);cases=[]
    for kind in ('fanout','background','unique','deep'):
        if args.only and args.only!=kind:continue
        folder=out/kind;probes=[]
        if kind in ('fanout','background'):
            count=8192 if kind=='fanout' else 128
            path=fan_fixture(folder/'fixture',count,mode='target');cfg=json.loads(path.read_text())
            cfg.update(port_trace_timeout_s=30,memory_limit_gib=4)
            expected=[['fan_top.domain.source','o','fan_top.domain.'+n,'i'] for n in ['g'+str(i) for i in range(count)]+['via_bit','via_alias']]
            total=count+3
            if kind=='background':
                rtl=path.parent/'fixture.sv';code=rtl.read_text();n=1000000
                code=code.replace('fan_domain domain(.*); endmodule','fan_domain domain(.*); wire [%d:0] unused_q; bulk unused(clk,d,unused_q); endmodule'%(n-1))
                code+='\nmodule bulk(input clk,d,output [%d:0] q);genvar k;generate for(k=0;k<%d;k=k+1)begin:tile fan_sink ff(clk,d,q[k]);end endgenerate endmodule\n'%(n-1,n)
                rtl.write_text(code);probes=['fan_top.unused.tile[0].ff','fan_top.unused.tile[999999].ff']
        elif kind=='unique':cfg,expected,total=unique(folder/'fixture')
        else:cfg,expected,total=deep(folder/'fixture')
        before=resource.getrusage(resource.RUSAGE_CHILDREN)
        graph,report=run(cfg,folder/'run',probes)
        after=resource.getrusage(resource.RUSAGE_CHILDREN)
        assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid']
        assert len(graph['nodes'])==total and edges(graph)==sorted(expected)
        assert graph['summary']['trace_timeout_instances']==0
        guard=graph['metadata']['memory_protection'];assert guard['trace_monitor_transport']=='socket'
        work=Path(guard['checkpoint_file']).parent
        assert not (work/'trace-current.json').exists() and not (work/'trace-ack.txt').exists()
        cache_bytes=(work/'language-steps.cache').stat().st_size;assert cache_bytes<=8388608
        if kind=='unique':assert graph['metadata']['language_trace_stats']['driver_queries']>2048
        report.update(case=kind,passed=True,exact_edges=True,timeout_s=30,transport='socket',cache_file_bytes=cache_bytes,
            child_user_cpu_s=round(after.ru_utime-before.ru_utime,3),child_system_cpu_s=round(after.ru_stime-before.ru_stime,3))
        cases.append(report);write_graph(out/'acceptance.json',dict(passed=False,complete=False,cases=cases));print(json.dumps(report),flush=True)
    write_graph(out/'acceptance.json',dict(passed=True,complete=True,cases=cases))


if __name__=='__main__':main()
