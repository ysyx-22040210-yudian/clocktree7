"""Real NPI: direct children only; tracing still crosses internal subhierarchies."""
import argparse
import copy
import json
from collections import Counter
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.extract import extract
from clocktree.graph import read_events
from clocktree.storage import write_graph


RTL=r'''
module direct_gate #(parameter W=1,ID=0)(input [W-1:0] i,input en,output [W-1:0] o);assign o=i&{W{en}};endmodule
module direct_gate_extra(input i,output o);assign o=i;endmodule
module direct_outgate(input i,input en,output o);assign o=i&en;endmodule
module direct_mux(input a,b,s,output y);assign y=s?b:a;endmodule
module direct_rmux(input a,b,s,output y);assign y=s?b:a;endmodule
module direct_sync(input rst_ni,clk_i,output rst_no);assign rst_no=rst_ni;endmodule
module direct_deep_only(input i,output o);assign o=i;endmodule
module direct_bridge(input i,output o);wire w;assign w=i;assign o=w;endmodule
module direct_nested(input clk,rst,en,output c,r,d);
 direct_gate u_gate(.i(clk),.en(en),.o(c));
 direct_sync u_sync(.rst_ni(rst),.clk_i(clk),.rst_no(r));
 direct_deep_only u_only(.i(clk),.o(d));
endmodule
module direct_domain(input a,b,rst,en,s,output [9:0] result);
 wire m,g,path_clk,rm;
 direct_mux u_mux(.a(a),.b(b),.s(s),.y(m));
 direct_gate #(.ID(17)) u_gate(.i(m),.en(en),.o(g));
 direct_gate #(.ID(3)) u_arr[1:0](.i({g,g}),.en({en,en}),.o(result[1:0]));
 direct_bridge u_path(.i(g),.o(path_clk));
 direct_outgate u_out(.i(path_clk),.en(en),.o(result[2]));
 direct_rmux u_rmux(.a(rst),.b(rst),.s(s),.y(rm));
 direct_sync u_sync(.rst_ni(rm),.clk_i(a),.rst_no(result[3]));
 direct_nested u_child(.clk(a),.rst(rst),.en(en),.c(result[4]),.r(result[5]),.d(result[6]));
 direct_gate_extra u_extra(.i(a),.o(result[7]));
 genvar k;
 generate for(k=0;k<2;k=k+1) begin:gen_branch
   wire co,ro;
   direct_gate u_gate(.i(a),.en(en),.o(co));
   direct_sync u_sync(.rst_ni(rst),.clk_i(a),.rst_no(ro));
   assign result[8+k]=co&ro;
 end endgenerate
endmodule
module direct_top(input a,b,rst,en,s,output [19:0] result);
 direct_domain domain(.a(a),.b(b),.rst(rst),.en(en),.s(s),.result(result[9:0]));
 direct_domain domain_extra(.a(a),.b(b),.rst(rst),.en(en),.s(s),.result(result[19:10]));
endmodule
'''


def rule(module,kind,inputs,output,mode='clock'):
    return {'module':module,'kind':kind,mode+'_inputs':inputs,mode+'_output':output}


def config(filelist,mode='clock',scope='direct_top.domain'):
    rules=([rule('direct_mux','mux',['a','b'],'y')],
           [rule('direct_gate','gate',['i'],'o')],
           [rule('direct_outgate','gate',['i'],'o')]) if mode=='clock' else (
           [rule('direct_rmux','mux',['a','b'],'y',mode)],
           [rule('direct_sync','rsy_sync',['rst_ni'],'rst_no',mode)])
    return {'extraction_mode':'full','tree_type':mode,'trace_scope':scope,'design':{'top':'direct_top','filelist':str(filelist)},
            'layers':[{'index':i,'hierarchy':scope,'modules':modules} for i,modules in enumerate(rules,1)]}


def run(config,directory):
    directory.mkdir(parents=True,exist_ok=True)
    source=directory/'input.json';write_graph(source,config)
    return extract(source,directory)


def direct_soc_config(directory):
    """Use a recorded SoC's actual parent; never broaden a direct query to soc."""
    from clocktree.config import load_config
    from clocktree.session import read_graph
    reference=read_graph(ROOT/'validation/runs/openc910-ordered7-01/clocktree.json')
    counts=Counter(n['id'].rsplit('.',1)[0] for n in reference['nodes'] if n['module']=='gated_clk_cell')
    scope=counts.most_common(1)[0][0]
    expected={n['id'] for n in reference['nodes'] if n['id'].rsplit('.',1)[0]==scope and n['module']=='gated_clk_cell'}
    old=load_config(ROOT/'validation/configs/openc910_ordered7_kdb.json')
    cfg={'design':copy.deepcopy(old['design']),'trace_scope':scope,'layers':[
        {'index':1,'hierarchy':scope,'modules':[rule('gated_clk_cell','gate',['clk_in'],'clk_out')]},
        {'index':2,'hierarchy':scope,'modules':[]}]}
    path=Path(directory)/'direct-soc-input.json';path.parent.mkdir(parents=True,exist_ok=True);write_graph(path,cfg)
    return cfg,path,expected


def gui_check(out,phase):
    from clocktree.gui import ClockTreeApp
    from capture_x11 import x11_image
    errors=[]
    app=ClockTreeApp(runs=out/'gui-runs',session_dir=out/'gui-session')
    app.geometry('1840x1000+20+20')
    app.show_error=lambda title,error:errors.append(str(title)+': '+str(error))
    app.report_callback_exception=lambda kind,error,tb:errors.append(str(error))
    def pump(done):
        start=time.monotonic()
        while not done():
            app.update();time.sleep(.02)
            if time.monotonic()-start>90:raise AssertionError(app.status.get())
        for _ in range(8):
            if app._destroying:break
            app.update();time.sleep(.02)
    pump(lambda:not app.busy)
    if phase=='write':
        app.open_config(out/'clock/input.json')
        app.run_button.invoke()
        pump(lambda:not app.busy)
    assert not errors,errors
    assert app.graph['metadata']['instance_search']=='direct_children'
    assert len(app.graph['nodes'])==5 and len(app.graph['edges'])==4
    assert 'RTL 父层路径' in app.layer_title.cget('text')
    assert any('不递归查找子层次' in str(widget.cget('text')) for widget in app._settings_inner.winfo_children()
               if widget.winfo_class()=='TLabel')
    app.settings_canvas.yview_moveto(.7)
    pump(lambda:True)
    if phase=='write':x11_image(app.winfo_id()).save(str(out/'direct-parent-gui.png'))
    app.close();pump(lambda:app._destroying)
    assert not errors,errors
    if phase=='write':
        subprocess.check_call([sys.executable,str(Path(__file__).resolve()),'--out',str(out),'--gui-phase','read'])
        write_graph(out/'gui-acceptance.json',{'pass':True,'real_npi_direct_only':True,'parent_hint':True,
                                            'configuration_and_result_restored':True,'callback_errors':errors})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default=str(ROOT/'validation/direct-instances/acceptance'))
    p.add_argument('--soc',action='store_true',help='Also validate a real OpenC910 direct-parent subnetwork')
    p.add_argument('--gui-phase',choices=('write','read'),help='Native GUI extraction and fresh-process restore')
    args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.gui_phase:return gui_check(out,args.gui_phase)
    fixture=out/'fixture';fixture.mkdir(exist_ok=True)
    (fixture/'fixture.sv').write_text(RTL,encoding='utf-8')
    filelist=fixture/'rtl.f';filelist.write_text(str(fixture/'fixture.sv')+'\n',encoding='utf-8')
    report={'cases':[]}
    for mode in ('clock','reset'):
        cfg=config(filelist,mode)
        graph=run(cfg,out/mode)
        assert graph['summary']['connectivity_complete'],list(graph['diagnostics'])
        expected=['u_mux','u_gate','u_arr[0]','u_arr[1]','u_out'] if mode=='clock' else ['u_rmux','u_sync']
        assert {n['id'] for n in graph['nodes']}=={'direct_top.domain.'+name for name in expected}
        expected_edges=[('u_mux','u_gate'),('u_gate','u_arr[0]'),('u_gate','u_arr[1]'),('u_gate','u_out')] if mode=='clock' else [('u_rmux','u_sync')]
        assert {(e['source'].rsplit('.',1)[-1],e['target'].rsplit('.',1)[-1]) for e in graph['edges']}==set(expected_edges)
        assert graph['metadata']['instance_search']=='direct_children'
        assert all(q['search_mode']=='direct_children' and q['recursive'] is False for q in graph['metadata']['scoped_queries'])
        if mode=='clock':
            edge=next(e for e in graph['edges'] if e['target'].endswith('.u_out'))
            assert all(direction in edge['evidence'] for direction in ('load','driver'))
            assert any('.u_path.' in step.get('name','') for step in edge['evidence']['load'])
        report['cases'].append({'case':mode,'pass':True,'instances':len(graph['nodes']),'edges':len(graph['edges']),
                               'nested_modules_excluded':True,'same_prefix_scope_excluded':True})
        # Threshold zero still uses the direct query and preserves every checkpoint.
        if mode=='clock':
            zero=run(dict(cfg,memory_limit_gib=0),out/'clock-memory-disabled')
            assert {n['id'] for n in zero['nodes']}=={n['id'] for n in graph['nodes']}
            assert zero['metadata']['module_checkpoints']==3
    for suffix in ('u_child','gen_branch[0]'):
        scope='direct_top.domain.'+suffix
        for mode,module,kind,pin,output in (('clock','direct_gate','gate','i','o'),('reset','direct_sync','rsy_sync','rst_ni','rst_no')):
            cfg=config(filelist,mode,scope)
            cfg['layers']=[{'index':1,'hierarchy':scope,'modules':[rule(module,kind,[pin],output,mode)]}]
            graph=run(cfg,out/('explicit-'+suffix.replace('[','').replace(']','')+'-'+mode))
            assert graph['summary']['connectivity_complete'],list(graph['diagnostics'])
            assert {n['id'] for n in graph['nodes']}=={scope+('.u_gate' if mode=='clock' else '.u_sync')}
            report['cases'].append({'case':'explicit-'+suffix+'-'+mode,'pass':True,'instances':1})
    for module in ('direct_deep_only','direct_domain'):
        cfg=config(filelist)
        cfg['layers']=[{'index':1,'hierarchy':'direct_top.domain','modules':[rule(module,'gate',['i'],'o')]}]
        graph=run(cfg,out/('excluded-'+module))
        assert not graph['summary']['connectivity_complete'] and not graph['nodes']
        assert any(d['code']=='missing_module' and d.get('search_mode')=='direct_children' for d in graph['diagnostics'])
        events=list(read_events(out/('excluded-'+module)/'raw.jsonl'))
        assert sum(e['event']=='module_checkpoint' for e in events)==1
        report['cases'].append({'case':'exclude-'+module,'pass':True,'missing_direct_module_reported':True})
    if args.soc:
        cfg,source,expected=direct_soc_config(out)
        graph=extract(source,out/'openc910-direct')
        assert graph['summary']['connectivity_complete'],list(graph['diagnostics'])
        assert {n['id'] for n in graph['nodes']}==expected
        report['soc']={'pass':True,'scope':cfg['trace_scope'],'instances':len(expected),'full_chip':False,
                       'direct_nodes_match_recorded_reference':True,'pin_traces':graph['summary']['observed_pin_traces']}
    report['pass']=True
    write_graph(out/'acceptance.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
