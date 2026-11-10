"""Real Verdi 2018 proof of sw_done input lanes in sequential concatenations."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.register_trace import run as trace,seeds_from_graph,RegisterIndex
from clocktree.storage import write_graph
from clocktree.sv_templates import prepare
from check_target_mode import run as extract


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--fanout',type=int,default=1000)
    parser.add_argument('--gui',action='store_true')
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    fixture=out/'fixture';fixture.mkdir()
    rtl=['module ack_div #(parameter W=4)(input i,output o,output [W-1:0] done);',
         'assign o=i;assign done={W{i}};endmodule',
         'module concat_top(input clk,rst_n,dft_mode,input [1:0] C,D,idx,input [7:0] payload);']
    expected=set();negative=set()
    def device(name,width=4):
        net='b_'+name
        rtl.extend(['wire [{}:0] {};'.format(width-1,net),
                    'ack_div #({}) {}(.i(clk),.o(),.done({}));'.format(width,name,net)])
        return net
    def mapping(instance,bit,source,target):
        expected.add(('concat_top.'+instance,'done[{}]'.format(bit),'concat_top.'+source,'concat_top.'+target))
    b=device('plain');rtl.extend(['reg [7:0] A;reg [0:7] AU;',
        'always @(posedge clk or negedge rst_n) if(!rst_n) A<=0; else A<={'+b+',C,D};',
        'always_ff @(posedge clk) AU<={'+b+',C,D};'])
    for i in range(4):
        mapping('plain',i,b+'[{}]'.format(i),'A[{}]'.format(i+4))
        mapping('plain',i,b+'[{}]'.format(i),'AU[{}]'.format(3-i))
    b=device('scalar',1);rtl.extend(['reg [15:0] AS;','always @(posedge clk) AS<={5\'b0,'+b+',10\'b0};'])
    mapping('scalar',0,b+'[0]','AS[10]')
    b=device('reverse');rtl.extend(['reg [31:16] AR;','always @(posedge clk) AR<={12\'b0,'+','.join(b+'[{}]'.format(i) for i in range(4))+'};'])
    for i in range(4):mapping('reverse',i,b+'[{}]'.format(i),'AR[{}]'.format(19-i))
    b=device('repeated');rtl.extend(['reg [5:0] AP;','always @(posedge clk) AP<={'+b+'[1:0],{2{'+b+'[3:2]}}};'])
    for i,targets in enumerate(((4,),(5,),(0,2),(1,3))):
        for target in targets:mapping('repeated',i,b+'[{}]'.format(i),'AP[{}]'.format(target))
    b=device('dynamic_neighbor');rtl.extend(['reg [6:0] AD;','always @(posedge clk) AD<={'+b+',payload[idx],D};'])
    for i in range(4):mapping('dynamic_neighbor',i,b+'[{}]'.format(i),'AD[{}]'.format(i+3))
    b=device('functional');rtl.extend(['reg [7:0] AF;','always @(posedge clk) AF<=dft_mode?8\'b0:{'+b+',C,D};'])
    for i in range(4):mapping('functional',i,b+'[{}]'.format(i),'AF[{}]'.format(i+4))
    b=device('comb');rtl.extend(['reg [7:0] comb_reg,AQ;','always @* comb_reg={'+b+',C,D};','always @(posedge clk) AQ<=comb_reg;'])
    for i in range(4):mapping('comb',i,'comb_reg[{}]'.format(i+4),'AQ[{}]'.format(i+4))
    b=device('aliases',1);rtl.extend(['wire x_alias='+b+'[0],y_alias='+b+'[0];reg AA;',
        'always @(posedge clk) if(payload[0]) AA<={x_alias};else AA<={y_alias};'])
    for input_name in ('x_alias','y_alias'):mapping('aliases',0,input_name,'AA')
    b=device('stress',1);rtl.extend(['genvar n;generate for(n=0;n<{};n=n+1) begin:loads'.format(args.fanout),
        'reg [2:0] q;always @(posedge clk) q<={1\'b0,'+b+',1\'b0};end endgenerate'])
    for i in range(args.fanout):mapping('stress',0,b+'[0]','loads[{}].q[1]'.format(i))
    b=device('unknown');rtl.extend(['reg [4:0] AX;','always @(posedge clk) AX<={'+b+','+b+'[idx]};'])
    for i in range(4):mapping('unknown',i,b+'[{}]'.format(i),'AX[{}]'.format(i+1));negative.add(('concat_top.unknown','done[{}]'.format(i),'unresolved','dynamic_or_multidimensional_index'))
    b=device('opaque');rtl.extend(['function hidden;hidden=payload[0];endfunction','reg [4:0] AO;',
        'always @(posedge clk) AO<={'+b+',hidden()};'])
    for i in range(4):mapping('opaque',i,b+'[{}]'.format(i),'AO[{}]'.format(i+1));negative.add(('concat_top.opaque','done[{}]'.format(i),'unresolved','unsupported_expression:npiFuncCall'))
    rtl.append('endmodule');design=fixture/'design.sv';design.write_text('\n'.join(rtl),encoding='utf-8')
    kdb=out/'kdb';kdb.mkdir()
    with (kdb/'build.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','concat_top',str(design),'-o','simv'],cwd=str(kdb),stdout=log,stderr=subprocess.STDOUT)
    rule=dict(module='ack_div',kind='div',clock_inputs=['i'],clock_output='o',aux_ports={'sw_done':'done'})
    config=dict(design=dict(top='concat_top',elab=str(kdb/'simv.daidir/kdb.elab++')),memory_limit_gib=4,
                layers=[dict(index=1,hierarchy='concat_top',modules=[rule])])
    graph,_=extract(config,out/'tree')
    request=dict(config=graph['config'],seeds=list(seeds_from_graph(graph)),trace_root='concat_top',dft_zero=['dft_mode'])
    write_graph(out/'request.json',request);start=time.monotonic();result=trace(out/'request.json',out/'registers')
    actual=set();issues=set()
    with sqlite3.connect(str(out/'registers/registers.sqlite')) as db:
        for seed,bit,target,side,mode,status,reason,detail in db.execute('SELECT j.seed,r.bit,r.endpoint,r.side,r.relation,r.status,r.reason,r.detail FROM results r JOIN jobs j ON j.id=r.job'):
            instance=json.loads(seed)['instance'];detail=json.loads(detail)
            if status=='register':
                assert side=='D' and mode=='direct' and detail['capture_target']==target
                assert detail['proof']['basis']=='edge_triggered_always'
                actual.add((instance,bit,detail['data_input'],target))
            else:issues.add((instance,bit,status,reason))
    write_graph(out/'oracle.json',dict(expected=sorted(expected),actual=sorted(actual),missing=sorted(expected-actual),extra=sorted(actual-expected),issues=sorted(issues)))
    assert actual==expected,(sorted(expected-actual)[:5],sorted(actual-expected)[:5])
    assert issues==negative,(issues,negative)
    index=RegisterIndex(out/'registers');total=index.export(out/'all.csv');index.export_json(out/'all.json')
    with (out/'all.csv').open(encoding='utf-8-sig') as stream:csv_rows=list(csv.DictReader(stream))
    assert total==len(csv_rows)==len(expected)+len(negative)
    assert all(row['已确认时序数据输入'] for row in csv_rows if row['状态']=='已确认寄存器')
    before=(out/'registers/verdi.log').stat().st_size
    assert trace(out/'request.json',out/'registers',resume=True)['summary']==result['summary']
    assert (out/'registers/verdi.log').stat().st_size==before
    plan=prepare(graph,out/'registers',out/'plan',False)
    branches={row['instance']:row for row in plan.branches(limit=100)[1]}
    scalar=plan.resolve(branches['concat_top.scalar'],{'without_mux':'check($var_swdone);'})
    assert scalar['ready'] and scalar['code']=='check(concat_top.b_scalar[0]);',scalar
    choices=plan.candidates(branches['concat_top.plain'],'var_swdone')
    assert len(choices)==2 and {c['expression'] for c in choices}=={'concat_top.b_plain[3:0]'}
    assert {c['capture_expression'] for c in choices}=={'concat_top.A[7:4]','concat_top.AU[0:3]'}
    assert not plan.resolve(branches['concat_top.plain'],{'without_mux':'$var_swdone'})['ready']
    selected=plan.resolve(branches['concat_top.plain'],{'without_mux':'$var_swdone'},{'var_swdone':choices[0]['id']})
    assert selected['ready'] and selected['code']=='concat_top.b_plain[3:0]'
    ambiguous=plan.resolve(branches['concat_top.aliases'],{'without_mux':'$var_swdone'})
    assert not ambiguous['ready'] and '多个时序数据输入' in ' '.join(ambiguous['issues'])
    gui_errors=[]
    if args.gui:
        from clocktree.gui import ClockTreeApp
        from clocktree.register_view import RegisterDialog
        from check_workbench_gui import pump
        from check_indexed_gui import capture
        app=ClockTreeApp(runs=out/'gui-runs',session_dir=out/'gui-session',remember=True)
        app.geometry('1840x1000+0+0');app.report_callback_exception=lambda *e:gui_errors.append(''.join(traceback.format_exception(*e)))
        pump(app,lambda:not app.busy);app.show_graph(graph);pump(app)
        dialog=RegisterDialog(app);dialog.geometry('1800x860+0+0');dialog.load_directory(out/'registers');pump(app)
        assert dialog.total==total and len(dialog.table.get_children())==100
        dialog.search.set('concat_top.b_plain');dialog.refresh(True);assert dialog.total==8
        item=dialog.table.get_children()[0];dialog.table.selection_set(item);dialog.show_detail();pump(app)
        assert 'concat_top.b_plain' in dialog.detail.get('1.0','end') and '接收寄存器位' in dialog.detail.get('1.0','end')
        assert dialog.table.item(item,'values')[-1].startswith('concat_top.b_plain[')
        capture(dialog,out/'swdone-input-and-capture.png');dialog.close();app._save_session_now();app.close()
        assert not gui_errors,gui_errors
    write_graph(out/'acceptance.json',dict(passed=True,native_kdb=True,fanout=args.fanout,proven_input_capture_pairs=len(expected),
        explicit_negative_results=len(negative),summary=result['summary'],seconds=time.monotonic()-start,
        gui_checked=args.gui,gui_errors=gui_errors,csv_rows=total,sv_data_input=True,multiple_register_choices=True,resume_unchanged=True))
    print('SWDONE_CONCAT_ACCEPTED '+str(len(expected)),flush=True)


if __name__=='__main__':main()
