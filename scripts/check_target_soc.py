"""Compare target-mode KDB connectivity with existing full-mode SoC references.

Requires the local validation KDBs and reference results; neither is packaged.
Direct-parent rules are derived from recorded instance locations, never from
expected edges. Parent groups occupy separate display levels in this test.
"""
import argparse
import collections
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from check_target_mode import run,edges
from clocktree.storage import write_graph


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True)
    out=Path(parser.parse_args().out).resolve();out.mkdir(parents=True,exist_ok=True)
    cases=[]
    projects=[('opentitan','opentitan_clkmgr_kdb.json','opentitan-clkmgr-01'),
              ('pulp','pulp_soc_kdb_complete.json','pulp-soc-kdb-02'),
              ('openc910','openc910_ordered7_kdb.json','openc910-ordered7-01')]
    for name,config_file,reference in projects:
        cfg=json.loads((ROOT/'validation/configs'/config_file).read_text())
        old=json.loads((ROOT/'validation/runs'/reference/'clocktree.json').read_text())
        nodes=old['nodes']
        if name=='openc910':
            parents=collections.Counter(n['id'].rsplit('.',1)[0] for n in nodes if n['module']=='gated_clk_cell')
            parent=parents.most_common(1)[0][0]
            nodes=[n for n in nodes if n['module']!='gated_clk_cell' or n['id'].rsplit('.',1)[0]==parent]
        groups=collections.OrderedDict()
        for node in sorted(nodes,key=lambda n:(n['layer'],n['id'])):
            parent=node['id'].rsplit('.',1)[0]
            key=(node['module'],tuple(node['inputs']),node['output'])
            groups.setdefault(parent,{})[key]=node
        layers=[]
        for parent,rules in groups.items():
            modules=[]
            for (module,inputs,output),node in rules.items():
                kind='mux' if len(inputs)>1 else ('div' if 'div' in module.lower() else 'gate')
                modules.append({'module':module,'kind':kind,'clock_inputs':list(inputs),'clock_output':output})
            layers.append({'index':len(layers)+1,'hierarchy':parent,'modules':modules})
        cfg.update(layers=layers,extraction_mode='target',memory_limit_gib=8)
        cfg['design'].pop('verdi_home',None)
        graph,report=run(cfg,out/name)
        selected={n['id'] for n in nodes}
        expected=sorted([e['source'],e['source_pin'],e['target'],e['target_pin']] for e in old['edges']
                        if e['source'] in selected and e['target'] in selected)
        assert {n['id'] for n in graph['nodes']}==selected
        assert edges(graph)==expected,(name,edges(graph),expected)
        assert graph['summary']['connectivity_complete'],(name,list(graph['boundaries']))
        cases.append(dict(project=name,passed=True,reference=reference,reference_nodes_and_edges_equal=True,
                          scope='selected direct-parent subnetwork' if name=='openc910' else 'all targets in recorded block configuration',
                          **report))
        write_graph(out/'acceptance.json',{'passed':False,'cases':cases})
        print(name,len(selected),len(expected),flush=True)
    write_graph(out/'acceptance.json',{'passed':True,'cases':cases})


if __name__=='__main__':main()
