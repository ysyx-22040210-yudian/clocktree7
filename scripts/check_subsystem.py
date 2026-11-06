"""Real Verdi subsystem discovery/trace acceptance; Python 3.6, no pytest."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.input_ports import filter_ports,confirmed_ports


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);args=p.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    rtl='''module wb_gate(input i, output o); assign o=i; endmodule
module wb_sub #(parameter W=2)(input [W+1:2] clk_bus,input aux_clk,input data,output unused);
 wire hidden; wb_gate internal_gate(.i(data),.o(hidden)); assign unused=hidden;
endmodule
module wb_top(input a,input b);
 wire ga,gb,u0,u1;
 wb_gate g0(.i(a),.o(ga)); wb_gate g1(.i(b),.o(gb));
 wb_sub #(.W(2)) s0(.clk_bus({gb,ga}),.aux_clk(ga),.data(b),.unused(u0));
 wb_sub #(.W(3)) s1(.clk_bus({ga,gb,ga}),.aux_clk(gb),.data(a),.unused(u1));
endmodule
'''
    (out/'design.sv').write_text(rtl);(out/'files.f').write_text(str(out/'design.sv')+'\n')
    config={'tree_type':'clock','design':{'top':'wb_top','filelist':str(out/'files.f')},'memory_limit_gib':4,
            'layers':[{'index':1,'hierarchy':'wb_top','modules':[{'module':'wb_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]},
                      {'index':2,'hierarchy':'wb_top','modules':[]}]}
    request={'config':config,'module':'wb_sub','hierarchy':'wb_top'}
    (out/'request.json').write_text(json.dumps(request))
    def run(arguments,log,expected=0):
        with open(str(log),'w') as stream:code=subprocess.call([sys.executable,'-m','clocktree']+arguments,cwd=str(ROOT),stdout=stream,stderr=subprocess.STDOUT)
        if code!=expected:raise AssertionError('exit {} expected {}: {}'.format(code,expected,log))
    run(['discover-inputs','--request',str(out/'request.json'),'--out',str(out/'discovery')],out/'discovery.log')
    discovery=json.loads((out/'discovery/input-ports.json').read_text())
    assert discovery['traced_ports']==0 and discovery['instances']==2
    rows=discovery['ports'];assert set(r['name'] for r in rows)=={'clk_bus','aux_clk','data'}
    assert {(r['instance'],r['width'],r['left'],r['right']) for r in rows if r['name']=='clk_bus'}=={('wb_top.s0',2,'3','2'),('wb_top.s1',3,'4','2')}
    expression='clk_bus|aux_clk'
    names=confirmed_ports(rows,expression,['clk_bus','aux_clk'])
    assert names==['aux_clk','clk_bus'] and filter_ports(rows,'^no_match$')==[]
    for expression,selected in [('^no_match$',[]),('clk',[]),('[',[])]:
        try:confirmed_ports(rows,expression,selected)
        except Exception:pass
        else:raise AssertionError('invalid/empty selection accepted')
    config['layers'][1]['modules']=[{'module':'wb_sub','kind':'subsystem','clock_inputs':names,'input_regex':'NEVER_MATCH_MAIN_EXTRACTION'}]
    reports=[]
    for mode in ('target','full'):
        config['extraction_mode']=mode;(out/'config.json').write_text(json.dumps(config))
        run(['extract','--config',str(out/'config.json'),'--out',str(out/mode)],out/(mode+'.log'))
        graph=json.loads((out/mode/'clocktree.json').read_text())
        actual={(e['source'],e['target'],e['target_pin']) for e in graph['edges']}
        expected={('wb_top.g0','wb_top.s0','clk_bus[2]'),('wb_top.g1','wb_top.s0','clk_bus[3]'),('wb_top.g0','wb_top.s0','aux_clk'),
                  ('wb_top.g0','wb_top.s1','clk_bus[2]'),('wb_top.g1','wb_top.s1','clk_bus[3]'),('wb_top.g0','wb_top.s1','clk_bus[4]'),('wb_top.g1','wb_top.s1','aux_clk')}
        assert actual==expected,(mode,actual)
        assert len(graph['nodes'])==4 and len(graph['components'])==2
        assert all(n['output'] is None for n in graph['nodes'] if n['kind']=='subsystem')
        assert not any(e['source'].startswith('wb_top.s') for e in graph['edges'])
        reports.append({'mode':mode,'summary':graph['summary']})
    (out/'acceptance.json').write_text(json.dumps({'discovery':discovery,'cases':reports},indent=2))
    print('SUBSYSTEM_ACCEPTED '+str(out/'acceptance.json'),flush=True)


if __name__=='__main__':main()
