"""Verdi 2018 case-controlled sequential D inputs: bit oracle, fanout, SV, Tk."""
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
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--fanout',type=int,default=1000)
    parser.add_argument('--gui',action='store_true')
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    fixture=out/'fixture';fixture.mkdir()
    rtl=['module ack_div #(parameter W=4)(input i,output o,output [W-1:0] done);assign o=i;assign done={W{i}};endmodule',
         'module case_top(input clk,rst_n,dft_mode,en,input [1:0] state,state2,C,D,input [7:0] payload,input [2:0] idx);']
    expected=set();positive=[]
    def device(name,width=4):
        b='b_'+name
        rtl.extend(['wire [{}:0] {};'.format(width-1,b),'ack_div #({}) {}(.i(clk),.o(),.done({}));'.format(width,name,b)])
        return b
    def mapping(name,b,targets):
        positive.append(name)
        for bit,target in enumerate(targets):expected.add(('case_top.'+name,'done[{}]'.format(bit),'case_top.'+b+'[{}]'.format(bit),'case_top.'+target))
    b=device('plain');rtl.extend(['reg [7:0] A;','always @(posedge clk) begin case(state) 2\'d1,2\'d2: begin A<={'+b+',C,D};end default:A<=0;endcase end'])
    mapping('plain',b,['A[{}]'.format(i+4) for i in range(4)])
    b=device('default_item');rtl.extend(['reg [7:0] AD;','always @(posedge clk) case(state) default:AD<={'+b+',C,D};2\'d1:AD<=0;endcase'])
    mapping('default_item',b,['AD[{}]'.format(i+4) for i in range(4)])
    b=device('nested');rtl.extend(['reg [15:8] AN;',
        'always_ff @(posedge clk or negedge rst_n) if(!rst_n) AN<=0; else if(en) begin',
        'case(state) 2\'d1:begin case(state2) 2\'d0:AN<={'+b+'[1:0],'+b+'[3:2],C,D};default:AN<=0;endcase end default:AN<=0;endcase end'])
    mapping('nested',b,['AN[14]','AN[15]','AN[12]','AN[13]'])
    b=device('ascending');rtl.extend(['reg [0:7] AU;','always @(posedge clk) case(state) 0:AU<={'+b+',C,D};default:AU<=0;endcase'])
    mapping('ascending',b,['AU[{}]'.format(3-i) for i in range(4)])
    b=device('functional');rtl.extend(['wire test_alias=dft_mode;reg [7:0] AF,excluded_F;',
        'always @(posedge clk) case(test_alias) 1\'b1:excluded_F<={'+b+',C,D};default:AF<={'+b+',C,D};endcase'])
    mapping('functional',b,['AF[{}]'.format(i+4) for i in range(4)])
    for name,keyword,pattern,target in [('wild_z','casez',"2'b0?",'AZ'),('wild_x','casex',"2'b0x",'AX')]:
        b=device(name);rtl.extend(['reg [7:0] '+target+',excluded_'+target+',excluded_default_'+target+';',
            'always @(posedge clk) '+keyword+"({1'b0,dft_mode}) "+pattern+':'+target+'<={'+b+',C,D};'+
            "2'b00:excluded_"+target+'<={'+b+',C,D};default:excluded_default_'+target+'<={'+b+',C,D};endcase'])
        mapping(name,b,[target+'[{}]'.format(i+4) for i in range(4)])
    b=device('dynamic_neighbor');rtl.extend(['reg [6:0] AP;',
        'always @(posedge clk) case(state) 0:AP<={'+b+',payload[idx],D};default:AP<=0;endcase'])
    mapping('dynamic_neighbor',b,['AP[{}]'.format(i+3) for i in range(4)])
    b=device('comb');rtl.extend(['reg [7:0] comb_reg;',
        'always @* case(state) 0:comb_reg={'+b+',C,D};default:comb_reg=0;endcase'])
    b=device('initial_only');rtl.extend(['reg [7:0] initial_reg;',
        'initial case(state) 0:initial_reg={'+b+',C,D};default:initial_reg=0;endcase'])
    b=device('control_only',1);rtl.extend(['reg [7:0] control_reg;',
        'always @(posedge clk) case('+b+'[0]) 0:control_reg<=0;default:control_reg<=1;endcase'])
    b=device('ambiguous_literal');rtl.extend(['reg [7:0] literal_reg;',
        "always @(posedge clk) case(4'b0001) '1:literal_reg<={"+b+',C,D};default:literal_reg<=0;endcase'])
    b=device('stress',1);rtl.extend(['genvar n;generate for(n=0;n<{};n=n+1) begin:loads'.format(args.fanout),
        'reg [2:0] q;always @(posedge clk) case(state) 0,1:q<={1\'b0,'+b+',1\'b0};default:q<=0;endcase end endgenerate'])
    for i in range(args.fanout):expected.add(('case_top.stress','done[0]','case_top.b_stress[0]','case_top.loads[{}].q[1]'.format(i)))
    rtl.append('endmodule');design=fixture/'design.sv';design.write_text('\n'.join(rtl),encoding='utf-8')
    kdb=out/'kdb';kdb.mkdir()
    with (kdb/'build.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','case_top',str(design),'-o','simv'],cwd=str(kdb),stdout=log,stderr=subprocess.STDOUT)
    rule=dict(module='ack_div',kind='div',clock_inputs=['i'],clock_output='o',aux_ports={'sw_done':'done'})
    config=dict(design=dict(top='case_top',elab=str(kdb/'simv.daidir/kdb.elab++')),memory_limit_gib=4,
                layers=[dict(index=1,hierarchy='case_top',modules=[rule])])
    graph,_=extract(config,out/'tree')
    request=dict(config=graph['config'],seeds=list(seeds_from_graph(graph)),trace_root='case_top',dft_zero=['dft_mode'])
    write_graph(out/'request.json',request);start=time.monotonic();result=trace(out/'request.json',out/'registers');seconds=time.monotonic()-start
    actual=set();issues=[];case_evidence=0
    with sqlite3.connect(str(out/'registers/registers.sqlite')) as db:
        for seed,bit,target,side,mode,status,reason,detail,path in db.execute('SELECT j.seed,r.bit,r.endpoint,r.side,r.relation,r.status,r.reason,r.detail,r.path FROM results r JOIN jobs j ON j.id=r.job'):
            instance=json.loads(seed)['instance'];detail=json.loads(detail)
            if status=='register':
                assert side=='D' and mode=='direct' and detail['capture_target']==target
                assert detail['proof']['basis']=='edge_triggered_always'
                actual.add((instance,bit,detail['data_input'],target))
                evidence=json.loads(db.execute('SELECT evidence FROM paths WHERE id=?',(path,)).fetchone()[0])
                assert any(a['type']=='npiCase' and a.get('case_items') for a in evidence['ancestors'])
                case_evidence+=1
            else:issues.append((instance,bit,status,reason))
    write_graph(out/'oracle.json',dict(expected=sorted(expected),actual=sorted(actual),missing=sorted(expected-actual),extra=sorted(actual-expected),issues=issues))
    assert actual==expected,(sorted(expected-actual)[:5],sorted(actual-expected)[:5])
    assert len(issues)==13,issues
    assert all(r[0] in ('case_top.comb','case_top.initial_only','case_top.control_only','case_top.ambiguous_literal') for r in issues),issues
    assert all(r[2]=='unresolved' for r in issues if r[0]!='case_top.control_only')
    assert all(r[2]=='pruned' for r in issues if r[0]=='case_top.control_only')
    assert all(r[3]=='ambiguous_case_literal_extension' for r in issues if r[0]=='case_top.ambiguous_literal')
    index=RegisterIndex(out/'registers');total=index.export(out/'all.csv');index.export_json(out/'all.json')
    with (out/'all.csv').open(encoding='utf-8-sig') as stream:csv_rows=list(csv.DictReader(stream))
    assert total==len(csv_rows)==len(expected)+len(issues)
    assert all(row['已确认时序数据输入'] for row in csv_rows if row['状态']=='已确认寄存器')
    before=(out/'registers/verdi.log').stat().st_size
    assert trace(out/'request.json',out/'registers',resume=True)['summary']==result['summary']
    assert (out/'registers/verdi.log').stat().st_size==before
    plan=prepare(graph,out/'registers',out/'plan',False);branches={r['instance']:r for r in plan.branches(limit=100)[1]}
    templates={'without_mux':'initial $display("sw_done=%b", $var_swdone);'}
    resolved=plan.resolve(branches['case_top.plain'],templates)
    assert resolved['ready'] and resolved['values']['var_swdone']=='case_top.b_plain[3:0]'
    choices=plan.candidates(branches['case_top.stress'],'var_swdone',search='loads[{}].q[1]'.format(args.fanout-1))
    assert len(choices)==1 and choices[0]['expression']=='case_top.b_stress[0]'
    assert not plan.resolve(branches['case_top.stress'],templates)['ready'] if args.fanout>1 else True
    selections={branches['case_top.stress']['id']:{'var_swdone':choices[0]['id']}}
    report=plan.export(templates,out/'generated.sv',selections)
    assert report['blocks']==len(positive)+1 and report['skipped']==4,report
    check=fixture/'generated_checks.sv';check.write_text('module generated_checks;\n'+(out/'generated.sv').read_text()+'\nendmodule\n')
    compile_dir=out/'compile';compile_dir.mkdir()
    with (compile_dir/'vcs.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','case_top','-top','generated_checks',str(design),str(check),'-o','simv'],cwd=str(compile_dir),stdout=log,stderr=subprocess.STDOUT)
    errors=[]
    if args.gui:
        from clocktree.gui import ClockTreeApp
        from clocktree.register_view import RegisterDialog
        from check_workbench_gui import pump
        from check_indexed_gui import capture
        app=ClockTreeApp(runs=out/'gui-runs',session_dir=out/'gui-session',remember=True)
        app.geometry('1840x1000+0+0');app.report_callback_exception=lambda *e:errors.append(''.join(traceback.format_exception(*e)))
        pump(app,lambda:not app.busy);app.show_graph(graph);pump(app)
        dialog=RegisterDialog(app);dialog.geometry('1800x860+0+0');dialog.load_directory(out/'registers');pump(app)
        dialog.search.set('case_top.b_plain');dialog.refresh(True);assert dialog.total==4
        item=dialog.table.get_children()[0];dialog.table.selection_set(item);dialog.show_detail();pump(app)
        detail=dialog.detail.get('1.0','end');assert 'case_top.b_plain' in detail and 'case_top.A[' in detail
        assert dialog.table.item(item,'values')[-1].startswith('case_top.b_plain[')
        capture(dialog,out/'swdone-case-registers.png');dialog.close();app.close();assert not errors,errors
    write_graph(out/'acceptance.json',dict(passed=True,native_kdb=True,verdi='O-2018.09-SP2',fanout=args.fanout,
        exact_input_capture_pairs=len(expected),case_evidence=case_evidence,negative_rows=len(issues),summary=result['summary'],
        trace_seconds=round(seconds,3),gui_checked=args.gui,gui_errors=errors,sv_blocks=report['blocks'],sv_skipped=report['skipped'],
        vcs_compiled=True,resume_unchanged=True))
    print('SWDONE_CASE_ACCEPTED '+str(len(expected)),flush=True)


if __name__=='__main__':main()
