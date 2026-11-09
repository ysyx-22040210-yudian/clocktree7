"""Native KDB proof for shuffled wide controls and sparse register destinations."""
import argparse
import json
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.storage import write_graph
from clocktree.register_trace import run as trace,seeds_from_graph
from check_target_mode import run as extract


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True)
    fixture=out/'fixture';fixture.mkdir();rng=random.Random(20261110)
    rtl=['module ctl_desc #(parameter W=4)(input a,b,input [W+3:4] sel,output o);assign o=sel[4]?a:b;endmodule',
         'module ctl_asc #(parameter W=4)(input a,b,input [4:W+3] sel,output o);assign o=sel[4]?a:b;endmodule',
         'module ack_div(input i,input [3:0] ratio,output o,output [35:4] done);assign o=i;assign done={32{i}};endmodule',
         'module matrix_top(input clk,dft_mode,a,b,input [511:0] data);',
         'reg [511:0] Q,R;always @(posedge clk) Q<=data;wire [35:4] ack;']
    expected=set();widths=[1,2,4,8,16,32,64,128]
    for ascending in (False,True):
        for width in widths:
            name=('asc' if ascending else 'desc')+str(width)
            indices=[rng.randrange(512) for _ in range(width)]  # repeated bits intentional
            concatenation='{'+','.join('Q[{}]'.format(i) for i in reversed(indices))+'}'
            rtl.append('wire [{}:0] ctrl_{};assign ctrl_{}=dft_mode ? {}\'b0 : {};'.format(width-1,name,name,width,concatenation))
            rtl.append('ctl_{} #({}) {}(.a(a),.b(b),.sel(ctrl_{}),.o());'.format('asc' if ascending else 'desc',width,name,name))
            for i,target in enumerate(indices):
                bit=width+3-i if ascending else 4+i
                expected.add(('matrix_top.'+name,'sel[{}]'.format(bit),'matrix_top.Q[{}]'.format(target),'Q','direct'))
    rtl.append('ack_div div0(.i(a),.ratio(Q[12:9]),.o(),.done(ack));')
    for i in range(4):expected.add(('matrix_top.div0','ratio[{}]'.format(i),'matrix_top.Q[{}]'.format(i+9),'Q','direct'))
    targets=rng.sample(range(512),32)
    for i,target in enumerate(targets):
        rtl.append('always @(posedge clk) R[{}]<=dft_mode ? 1\'b0 : ack[{}];'.format(target,i+4))
        expected.add(('matrix_top.div0','done[{}]'.format(i+4),'matrix_top.R[{}]'.format(target),'D','direct'))
    rtl.append('endmodule');design=fixture/'design.sv';design.write_text('\n'.join(rtl),encoding='utf-8')
    kdb=out/'kdb';kdb.mkdir()
    with (kdb/'build.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','matrix_top',str(design),'-o','simv'],cwd=str(kdb),stdout=log,stderr=subprocess.STDOUT)
    rules=[dict(module='ctl_'+suffix,kind='mux',clock_inputs=['a','b'],clock_output='o',aux_ports={'pll_sel':'sel'}) for suffix in ('asc','desc')]
    rules.append(dict(module='ack_div',kind='div',clock_inputs=['i'],clock_output='o',aux_ports={'div':'ratio','sw_done':'done'}))
    config=dict(tree_type='clock',design=dict(top='matrix_top',elab=str(kdb/'simv.daidir/kdb.elab++')),memory_limit_gib=4,
                layers=[dict(index=1,hierarchy='matrix_top',modules=rules)])
    graph,report=extract(config,out/'tree')
    request=dict(config=graph['config'],seeds=list(seeds_from_graph(graph)),trace_root='matrix_top',dft_zero=['dft_mode'])
    write_graph(out/'request.json',request);start=time.monotonic();result=trace(out/'request.json',out/'registers')
    actual=set()
    with sqlite3.connect(str(out/'registers/registers.sqlite')) as db:
        for seed,bit,endpoint,side,relation,status,detail in db.execute('SELECT j.seed,r.bit,r.endpoint,r.side,r.relation,r.status,r.detail FROM results r JOIN jobs j ON j.id=r.job'):
            assert status=='register',(status,detail)
            assert json.loads(detail)['proof']['basis']
            actual.add((json.loads(seed)['instance'],bit,endpoint,side,relation))
    write_graph(out/'oracle.json',dict(expected=sorted(expected),actual=sorted(actual),missing=sorted(expected-actual),extra=sorted(actual-expected)))
    assert actual==expected,(len(actual),len(expected),list(expected-actual)[:5],list(actual-expected)[:5])
    assert result['summary']['complete'],result
    write_graph(out/'acceptance.json',dict(passed=True,native_kdb=True,random_seed=20261110,widths=widths,
        descending_and_ascending=True,ports=len(request['seeds']),exact_bit_mappings=len(expected),sparse_load_bits=32,
        summary=result['summary'],seconds=time.monotonic()-start))
    print('REGISTER_BIT_MATRIX_ACCEPTED '+str(len(expected)),flush=True)


if __name__=='__main__':main()
