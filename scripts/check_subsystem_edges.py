"""Native subsystem reset, partial selection, missing pins, unconnected and timeout cases."""
import argparse
import copy
import importlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.storage import write_graph
from clocktree.session import read_graph
from clocktree.input_ports import discover
import check_trace_timeout as timeout_test


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--base',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();base=Path(args.base).resolve();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    config=json.loads((base/'config.json').read_text());config['extraction_mode']='target'
    extraction=importlib.import_module('clocktree.extract');reports=[]
    def run(name,cfg):
        directory=out/name;directory.mkdir(parents=True,exist_ok=True);write_graph(directory/'input.json',cfg)
        graph=extraction.extract(directory/'input.json',directory)
        reports.append({'case':name,'summary':graph['summary']});return graph
    for mode in ('target','full'):
        cfg=copy.deepcopy(config);cfg.update(tree_type='reset',extraction_mode=mode)
        for layer in cfg['layers']:
            for rule in layer['modules']:
                rule['reset_inputs']=rule.pop('clock_inputs')
                if 'clock_output' in rule:rule['reset_output']=rule.pop('clock_output')
                if rule['kind']=='gate':rule['kind']='rsy_sync'
        graph=run('reset-'+mode,cfg)
        assert graph['summary']['connectivity_complete'] and len(graph['edges'])==7 and len(graph['components'])==2
    cfg=copy.deepcopy(config);cfg['layers'][1]['modules'][0]['clock_inputs']=['aux_clk']
    graph=run('manual-subset',cfg)
    assert len(graph['edges'])==2 and all(e['target_pin']=='aux_clk' for e in graph['edges'])
    cfg['layers'][1]['modules'][0]['clock_inputs']=['aux_clk','missing_clock']
    graph=run('missing-port',cfg)
    assert not graph['summary']['connectivity_complete'] and len(graph['edges'])==2
    assert sum(b['status']=='missing_pin' for b in graph['boundaries'])==2
    cfg=copy.deepcopy(config)
    rtl=(base/'design.sv').read_text();rtl=rtl.rsplit('endmodule',1)[0]+'''wb_sub s2(.clk_bus(),.aux_clk(),.data(a),.unused());
endmodule
'''
    (out/'unconnected.sv').write_text(rtl);(out/'unconnected.f').write_text(str(out/'unconnected.sv')+'\n')
    cfg['design']['filelist']=str(out/'unconnected.f')
    graph=run('unconnected',cfg)
    assert len(graph['edges'])==7 and len(graph['nodes'])==5
    node=next(n for n in graph['nodes'] if n['id']=='wb_top.s2')
    assert all(v['status']=='unconnected' for v in node['port_connections'].values())
    assert all(e['target']!='wb_top.s2' for e in graph['edges'])
    cfg=copy.deepcopy(config);cfg['port_trace_timeout_s']=.75
    old_hook=timeout_test.HOOK;timeout_test.HOOK=old_hook.replace(r'\.m[0-9]+$',r'\.s[0-9]+$')
    instrumented,runner=timeout_test.instrument('aux_clk',4)
    try:graph=run('subsystem-timeout',cfg)
    finally:instrumented.run_verdi=runner;timeout_test.HOOK=old_hook
    assert graph['summary']['extraction_finished'] and graph['summary']['trace_timeout_instances']==1
    bad=next(n for n in graph['nodes'] if n.get('trace_timeout'))
    assert bad['kind']=='subsystem' and bad['output'] is None
    assert bad['port_trace_status']['aux_clk']=='trace_timeout'
    assert all(status=='trace_skipped_timeout' for pin,status in bad['port_trace_status'].items() if pin!='aux_clk')
    assert 'UNCOMMITTED_TIMEOUT' not in (out/'subsystem-timeout/raw.jsonl').read_text()
    assert len(graph['edges'])==4 and all(e['target']!='wb_top.s0' for e in graph['edges'])
    assert graph['metadata']['memory_protection']['timeout_restart_count']==1
    write_graph(out/'acceptance.json',{'passed':True,'real_verdi':True,'timeout_injection':'blocking sleep in scratch NPI worker; production timer and rollback',
                                     'cases':reports})
    print('SUBSYSTEM_EDGE_CASES_ACCEPTED',flush=True)


if __name__=='__main__':main()
