"""Reproducible real Verdi 2018 register-endpoint and native Tk acceptance."""
import argparse
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.register_trace import run as trace, seeds_from_graph, RegisterIndex
from clocktree.storage import write_graph
from clocktree.session import read_graph
from check_target_mode import run as extract


def fixture(directory,fanout=1000):
    directory.mkdir(parents=True,exist_ok=True)
    rtl='''
module ctl_mux #(parameter W=4)(input a,b,input [W+3:4] choose_src,output o);
assign o=choose_src[4]?a:b; endmodule
module ctl_gate(input i,enable_ctl,output o); assign o=i; endmodule
module ctl_div(input i,input [3:0] ratio_cfg,output o,locked_o);
assign o=i;assign locked_o=i;endmodule
module wrap(input [0:3] xi,output [3:0] xo);assign xo=xi;endmodule
module reg_top(input clk,rst,dft_mode,mode,input [15:0] wdata,input a,b,output o);
reg [15:0] C,D,E,F; reg [0:3] UP; reg [3:0] comb_reg; reg latch_reg;
wire [3:0] msel,alias_sel,ratio; wire gate_sel,mclk,gclk,done,done_mux;
wire test_alias=dft_mode; reg if_sel; wire cyc_a,cyc_b;
always @(posedge clk or negedge rst) begin
 if(!rst) begin C<=0;D<=0;F<=0;UP<=0;end
 else begin C<=wdata;D<=wdata;F<=wdata;UP<=wdata[3:0];end
end
always @* comb_reg=D[5:2];
always @* if (!test_alias) if_sel=C[6]; else if_sel=F[6];
always @* if (mode) latch_reg=wdata[0];
assign msel=dft_mode?F[11:8]:comb_reg;
wrap uw(.xi(msel),.xo(alias_sel));
assign gate_sel=dft_mode?F[1]:C[7];
assign ratio={C[3:2],D[9:8]};
ctl_mux m0(.a(a),.b(b),.choose_src(alias_sel),.o(mclk));
ctl_mux m_asc(.a(a),.b(b),.choose_src(UP),.o());
ctl_mux m_reverse(.a(a),.b(b),.choose_src({D[2],D[3],D[4],D[5]}),.o());
ctl_mux m_index(.a(a),.b(b),.choose_src(C[4+:4]),.o());
ctl_mux #(2) m_small(.a(a),.b(b),.choose_src({2{C[7]}}),.o());
ctl_gate g0(.i(mclk),.enable_ctl(gate_sel),.o(gclk));
ctl_gate g_if(.i(a),.enable_ctl(if_sel),.o());
ctl_gate g_latch(.i(a),.enable_ctl(latch_reg),.o());
ctl_gate g_logic(.i(a),.enable_ctl(C[0]&D[0]),.o());
ctl_gate g_invert(.i(a),.enable_ctl(~C[1]),.o());
ctl_gate g_constant(.i(a),.enable_ctl(1'b0),.o());
ctl_gate g_unknown(.i(a),.enable_ctl(mode?C[2]:D[2]),.o());
ctl_gate g_dynamic(.i(a),.enable_ctl(C[wdata[3:0]]),.o());
ctl_gate g_cycle(.i(a),.enable_ctl(cyc_a),.o());
ctl_gate g_missing(.i(a),.enable_ctl(),.o());
assign cyc_a=~cyc_b;assign cyc_b=~cyc_a;
ctl_div d0(.i(gclk),.ratio_cfg(ratio),.o(o),.locked_o(done));
assign done_mux=test_alias?F[2]:done;
always @(posedge clk) E[10]<=done_mux;
always @(posedge clk) E[11]<=done;
always @(posedge clk) E[12]<=dft_mode?done:F[3];
// A clock/control use must not be mistaken for the D pin.
always @(posedge done) E[13]<=C[0];
genvar i;generate for(i=0;i<FANOUT;i=i+1) begin:loads
 reg sink;always @(posedge clk) sink<=done_mux;
end endgenerate
endmodule
'''.replace('FANOUT',str(fanout))
    (directory/'registers.sv').write_text(rtl,encoding='utf-8')
    (directory/'files.f').write_text(str(directory/'registers.sv')+'\n',encoding='utf-8')
    rules=[{'module':'ctl_mux','kind':'mux','clock_inputs':['a','b'],'clock_output':'o','aux_ports':{'pll_sel':'choose_src'}},
           {'module':'ctl_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o','aux_ports':{'gt':'enable_ctl'}},
           {'module':'ctl_div','kind':'div','clock_inputs':['i'],'clock_output':'o','aux_ports':{'div':'ratio_cfg','sw_done':'locked_o'}}]
    config={'design':{'top':'reg_top','filelist':str(directory/'files.f')},'memory_limit_gib':4,
            'layers':[{'index':i+1,'hierarchy':'reg_top','modules':[rule]} for i,rule in enumerate(rules)]}
    write_graph(directory/'config.json',config);return config


def mappings(directory,instance=None):
    with sqlite3.connect(str(directory/'registers.sqlite')) as db:
        rows=[]
        for seed,bit,endpoint,side,relation,status,detail in db.execute('SELECT j.seed,r.bit,r.endpoint,r.side,r.relation,r.status,r.detail FROM results r JOIN jobs j ON j.id=r.job'):
            seed=json.loads(seed)
            if instance and seed['instance']!='reg_top.'+instance:continue
            rows.append((seed['instance'],bit,endpoint,side,relation,status,json.loads(detail)))
        return rows


def verify(directory,fanout):
    def ends(instance):return {(r[1],r[2],r[3],r[4]) for r in mappings(directory,instance) if r[5]=='register'}
    expected=lambda pin,bits,side='Q',relation='direct':{(pin+('[{}]'.format(i) if i is not None else ''),'reg_top.'+name,side,relation) for i,name in bits}
    assert ends('m0')==expected('choose_src',[(i+4,'D[{}]'.format(i+2)) for i in range(4)]),ends('m0')
    assert ends('m_asc')==expected('choose_src',[(i+4,'UP[{}]'.format(3-i)) for i in range(4)]),ends('m_asc')
    assert ends('m_reverse')==expected('choose_src',[(i+4,'D[{}]'.format(5-i)) for i in range(4)]),ends('m_reverse')
    assert ends('m_index')==expected('choose_src',[(i+4,'C[{}]'.format(i+4)) for i in range(4)]),ends('m_index')
    assert ends('m_small')==expected('choose_src',[(4,'C[7]'),(5,'C[7]')]),ends('m_small')
    for inst,signal in [('g0','C[7]'),('g_if','C[6]'),('g_latch','latch_reg')]:
        assert ends(inst)==expected('enable_ctl',[(None,signal)]),(inst,ends(inst))
    assert ends('g_invert')==expected('enable_ctl',[(None,'C[1]')],relation='inverted')
    assert ends('g_logic')==expected('enable_ctl',[(None,'C[0]'),(None,'D[0]')],relation='logic')
    assert all(r[5]=='constant' for r in mappings(directory,'g_constant'))
    assert not ends('g_dynamic') and any(r[5]=='unresolved' and 'dynamic' in r[6]['reason'] for r in mappings(directory,'g_dynamic'))
    assert any(r[5]=='cycle' for r in mappings(directory,'g_cycle'))
    assert not ends('g_missing')
    assert ends('g_unknown')==expected('enable_ctl',[(None,'C[2]'),(None,'D[2]')],relation='conditional'),ends('g_unknown')
    expected_div=expected('ratio_cfg',[(0,'D[8]'),(1,'D[9]'),(2,'C[2]'),(3,'C[3]')])
    expected_div|=expected('locked_o',[(None,'E[10]'),(None,'E[11]')]+[(None,'loads[{}].sink'.format(i)) for i in range(fanout)],side='D')
    assert ends('d0')==expected_div, (len(ends('d0')),len(expected_div),list(ends('d0')-expected_div)[:3])
    all_rows=mappings(directory)
    assert not any(r[5]=='register' and ('.F[' in r[2] or '.comb_reg' in r[2] or '.if_sel' in r[2]) for r in all_rows)
    assert all(r[6].get('proof',{}).get('basis') for r in all_rows if r[5]=='register')
    assert any(r[6].get('proof',{}).get('kind')=='latch' for r in mappings(directory,'g_latch'))
    index=RegisterIndex(directory);count=index.export(directory/'all.csv');index.export_json(directory/'all-evidence.json')
    exported=json.loads((directory/'all-evidence.json').read_text(encoding='utf-8'))
    assert len(exported['results'])==count==len(all_rows)
    assert index.query('loads[999].sink')[0]==(1 if fanout>=1000 else 0)
    return count


def native(out,fanout,kdb=False):
    config=fixture(out/'fixture',fanout);graph,graph_report=extract(config,out/'tree')
    if kdb:
        build=out/'kdb';build.mkdir()
        with (build/'build.log').open('w') as log:
            subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','reg_top','-f',config['design']['filelist'],'-o','simv'],cwd=str(build),stdout=log,stderr=subprocess.STDOUT)
        config=copy.deepcopy(config);config['design'].pop('filelist');config['design']['elab']=str(build/'simv.daidir/kdb.elab++')
        graph,graph_report=extract(config,out/'kdb-tree')
    seeds=list(seeds_from_graph(graph));assert len(seeds)==17,len(seeds)
    request={'config':graph['config'],'seeds':seeds,'trace_root':'reg_top','dft_zero':['dft_mode']}
    write_graph(out/'request.json',request)
    started=time.monotonic();result=trace(out/'request.json',out/'native');elapsed=time.monotonic()-started
    count=verify(out/'native',fanout)
    # Reopening a completed task must not launch Verdi or add duplicate rows.
    before=(out/'native/verdi.log').stat().st_size
    again=trace(out/'request.json',out/'native',resume=True)
    assert (out/'native/verdi.log').stat().st_size==before
    assert result['summary']==again['summary']
    write_graph(out/'acceptance.json',{'passed':True,'real_verdi':True,'kdb':kdb,'fanout':fanout,'rows':count,
                'seconds':round(elapsed,3),'summary':result['summary'],'original_tree':graph_report,'resume_deduplicated':True})
    print(json.dumps({'passed':True,'rows':count,'seconds':round(elapsed,3),'summary':result['summary']}),flush=True)


def gui(out,phase,screenshots):
    from clocktree.gui import ClockTreeApp
    from clocktree.register_view import RegisterDialog
    from check_workbench_gui import pump
    from check_indexed_gui import capture
    errors=[];app=ClockTreeApp(runs=out/'gui-runs',session_dir=out/'gui-session',remember=True)
    app.geometry('1840x1000+0+0');app.report_callback_exception=lambda *args:errors.append(''.join(traceback.format_exception(*args)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if phase=='gui-write':
        app.show_graph(read_graph(out/'tree/clocktree.json'));pump(app)
        dialog=RegisterDialog(app);dialog.load_directory(out/'native');pump(app)
        assert dialog.total>100 and len(dialog.table.get_children())==100
        dialog.page(1);assert dialog.offset==100
        dialog.search.set('E[10]');dialog.refresh(True);assert dialog.total==1
        item=dialog.table.get_children()[0];dialog.table.selection_set(item);dialog.show_detail();pump(app)
        detail=dialog.detail.get('1.0','end');assert 'edge_triggered_always' in detail and 'reg_top.dft_mode = 0' in detail and 'E[10]' in detail
        dialog.locate();pump(app)
        if screenshots:capture(dialog,out/'register-dialog.png')
        dialog.search.set('');dialog.start();pump(app,lambda:dialog.process is not None and dialog.process.poll() is not None)
        pump(app,lambda:dialog.start_button.instate(['!disabled']))
        assert dialog.process.returncode==2 and dialog.total>100,dialog.status.get()
        write_graph(out/'gui-run.json',{'directory':str(dialog.directory)})
        dialog.close();app._save_session_now();pump(app)
    else:
        assert app.graph
        dialog=RegisterDialog(app);pump(app)
        assert str(dialog.directory)==json.loads((out/'gui-run.json').read_text())['directory'] and dialog.total>100
        dialog.search.set('g0');dialog.refresh(True);assert dialog.total==1
        if screenshots:capture(dialog,out/'register-history.png')
        dialog.close()
    app.close();assert not errors,errors
    deadline=time.monotonic()+30
    while not app._destroying and time.monotonic()<deadline:app.update();time.sleep(.01)
    write_graph(out/(phase+'.json'),{'passed':True,'errors':errors})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--phase',choices=('native','gui-write','gui-read'),default='native');parser.add_argument('--fanout',type=int,default=1000)
    parser.add_argument('--no-screenshots',action='store_true');args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    if args.phase=='native':native(out,args.fanout,True)
    else:gui(out,args.phase,not args.no_screenshots)


if __name__=='__main__':main()
