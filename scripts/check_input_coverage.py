"""Verify missing upstream port reports on fresh KDB extractions and on the native Tk canvas."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.extract import extract
from clocktree.gui import ClockTreeApp
from clocktree.input_coverage import FIELD, LABEL
from clocktree.issue_view import IssueDialog
from clocktree.session import read_graph
from clocktree.storage import write_graph
from check_l1_interfaces import compile_kdb


RTL="""
module cov_gate(input clk, en, output o);
  assign o = clk & en;
endmodule
module cov_mux(input a, b, c, sel, output o);
  assign o = sel ? a : (b | c);
endmodule
module cov_sub(input [2:0] bus, output observed);
  assign observed = ^bus;
endmodule
module cov_sync(input ri, clock, output reg ro);
  always @(posedge clock) ro <= ri;
endmodule
module cov_top(input clk, rst_n, sel, output observed, open_q, good_rst, open_rst);
  wire ingress, gated, selected;
  assign ingress=clk;
  cov_gate g(.clk(ingress), .en(1'b1), .o(gated));
  cov_gate open_g(.clk(), .en(sel), .o(open_q));
  cov_mux m(.a(gated), .b(clk), .c(1'b0), .sel(sel), .o(selected));
  cov_sub s(.bus({selected,1'b0,clk}), .observed(observed));
  cov_sync good(.ri(rst_n), .clock(clk), .ro(good_rst));
  cov_sync open_r(.ri(), .clock(clk), .ro(open_rst));
endmodule
"""


def config(kdb,tree,mode):
    if tree=='clock':
        rules=[[dict(module='cov_gate',kind='sync_gate',clock_inputs=['clk'],clock_output='o',aux_ports={'gt':'en'})],
               [dict(module='cov_mux',kind='mux',clock_inputs=['a','b','c'],clock_output='o',aux_ports={'pll_sel':'sel'})],
               [dict(module='cov_sub',kind='subsystem',clock_inputs=['bus'])]]
    else:
        rules=[[dict(module='cov_sync',kind='rsy_sync',reset_inputs=['ri'],reset_output='ro')]]
    return dict(tree_type=tree,design={'top':'cov_top','elab':str(kdb)},extraction_mode=mode,memory_limit_gib=8,
                layers=[dict(index=i+1,hierarchy='cov_top',input_boundary=(i==0),modules=modules) for i,modules in enumerate(rules)])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--reuse',action='store_true',help='Open the four existing extraction results in this directory')
    args=parser.parse_args()
    out=args.out.resolve()
    out.mkdir(parents=True,exist_ok=True)
    if not args.reuse:
        rtl=out/'coverage.sv'
        rtl.write_text(RTL,encoding='utf-8')
        kdb=compile_kdb(out/'compile',[rtl],['cov_top'])
    cases=[]
    for tree in ('clock','reset'):
        expected={('cov_top.m','c'),('cov_top.open_g','clk'),('cov_top.s','bus[1]')} if tree=='clock' else {('cov_top.open_r','ri')}
        for mode in ('target','full'):
            directory=out/(tree+'-'+mode)
            if not args.reuse:
                directory.mkdir(parents=True,exist_ok=True)
                write_graph(directory/'rules.json',config(kdb,tree,mode))
                graph=extract(directory/'rules.json',directory)
                assert graph['metadata']['verdi_exit_code']==0 and graph['summary']['extraction_finished']
            path=directory/(tree+'tree.json')
            public=json.loads(path.read_text(encoding='utf-8'))
            actual={(n['id'],pin) for n in public['nodes'] for pin in n.get(FIELD,())}
            assert actual==expected and public['summary'][FIELD]==len(expected),(tree,mode,actual,expected)
            cases.append((tree+'-'+mode,path,expected))
            if tree=='clock' and mode=='target':
                for n in public['nodes']:n.pop(FIELD,None)
                del public['summary'][FIELD]
                legacy=out/'legacy-clock.json';write_graph(legacy,public)
                cases.append(('legacy-clock-json',legacy,expected))
    app=ClockTreeApp(runs=out/'gui-runs',session_dir=out/'session',remember=False)
    app.geometry('1780x960+0+0')
    errors=[]
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    def wait(check):
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            app.update()
            assert not errors,errors
            if check():return
            time.sleep(.01)
        raise AssertionError('Tk timeout')
    report=[]
    try:
        for case,path,expected in cases:
            app.show_graph(read_graph(path))
            wait(lambda:not app.diagram.layout_pending and app.diagram._pending is None)
            assert app.graph['summary'][FIELD]==len(expected) and '输入缺少上游连接' in app.state.get()
            assert '缺少上游' in app.diagram.model.svg()
            app.state_badge.event_generate('<Button-1>')
            dialog=next(window for window in app.winfo_children() if isinstance(window,IssueDialog))
            wait(lambda:dialog.ready)
            assert dialog.category.get()==LABEL
            rows=list(dialog.rows.values())
            assert {(r[5],r[6]) for r in rows}==expected and len(rows)==len(expected)
            file=out/(case+'-issues.csv')
            assert dialog.index.export(file,category=LABEL)==len(expected)
            with file.open(encoding='utf-8-sig',newline='') as f:
                assert {(r['实例'],r['端口']) for r in csv.DictReader(f)}==expected
            for r in rows:
                dialog.table.selection_set(str(r[0]));dialog.locate()
                wait(lambda:not app.diagram.layout_pending and app.diagram._pending is None)
                assert app.diagram.issue_port==(r[5],r[6])
                assert app.selected_object['id']==r[5] and r[6] in app.selected_object['inputs'],(case,r,app.selected_object)
            report.append(dict(case=case,inputs=[dict(instance=r[5],pin=r[6],signal=r[7],reason=r[8]) for r in rows],
                               warning=app.state.get(),csv=True,located=True))
            dialog.close()
        write_graph(out/'acceptance.json',dict(passed=True,python=sys.version.split()[0],tk=app.tk.call('info','patchlevel'),
                                              cases=report,tk_errors=errors))
        print('VERIFIED_INPUT_COVERAGE '+str(len(report))+' Tk cases',flush=True)
    finally:
        app.destroy()


if __name__=='__main__':
    main()
