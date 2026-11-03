"""Real NPI: checkpoint + fresh RSS check after every configured module rule."""
import argparse
import copy
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tkinter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.config import load_config
from clocktree.graph import read_events
from clocktree.storage import write_graph
from check_memory_resume import same_graph


ROTATE=r'''
rename ct_save_checkpoint ct_test_original_checkpoint
proc ct_save_checkpoint {stage queries traces} {
    global ct_completed_modules ct_rotate_file ct_checkpoint
    ct_test_original_checkpoint $stage $queries $traces
    if {$stage eq "enumerate" && $ct_completed_modules <= 3} {
        set marker [file join [file dirname $ct_checkpoint] "rotated-$ct_completed_modules"]
        if {![file exists $marker]} {
            set stream [open $marker w]; puts $stream once; close $stream
            set stream [open $ct_rotate_file w]; puts $stream "test checkpoint recovery"; close $stream
        }
    }
}
'''


def inspect(directory,graph,config,enabled=True):
    count=sum(len(layer['modules']) for layer in config['layers'])
    guard=graph['metadata']['memory_protection']
    assert guard['last_checkpoint']['completed_modules']==count
    assert graph['metadata']['module_checkpoints']==count
    assert graph['metadata']['expected_module_checkpoints']==count
    snapshots=sorted(Path(guard['module_checkpoint_directory']).glob('*.json'))
    assert len(snapshots)==count
    interpreter=tkinter.Tcl()
    raw=directory/'raw.jsonl'
    targets=Path(guard['checkpoint_file']).parent/'targets.tcl'
    for index,path in enumerate(snapshots,1):
        state=json.loads(path.read_text(encoding='utf-8'))
        assert state['completed_modules']==index and state['last_module']['index']==index
        with raw.open('rb') as stream:events=[json.loads(line) for line in stream.read(state['raw_offset']).splitlines()]
        assert events[-1]['event']=='module_checkpoint' and events[-1]['checkpoint']['index']==index
        assert sum(e['event']=='instance' for e in events)==state['instances']
        with targets.open('rb') as stream:prefix=stream.read(state['targets_offset']).decode('utf-8')
        assert interpreter.call('info','complete',prefix)==1
    events=list(read_events(raw))
    assert [event['checkpoint']['index'] for event in events if event['event']=='module_checkpoint']==list(range(1,count+1))
    if enabled:
        checks=[json.loads(line) for line in (directory/'module-memory-checks.jsonl').read_text().splitlines()]
        assert [check['index'] for check in checks]==list(range(1,count+1))
        assert guard['module_checks']==count and all(check['rss_gib']>0 for check in checks)
        # Every next completed lookup occurs strictly after its predecessor's
        # supervisor memory check, even within one shared native query.
        order=[]
        for line in (directory/'verdi.log').read_text(encoding='utf-8').splitlines():
            if line.startswith('CT7_ENUM layer='):order.append('lookup')
            elif line.startswith('CT7_MODULE_CHECKPOINT '):order.append('checkpoint')
            elif line.startswith('CT7_MODULE_MEMORY_CHECK '):order.append('check')
        assert order==['lookup','checkpoint','check']*count,order
    else:
        assert guard['module_checks']==0 and not (directory/'module-memory-checks.jsonl').exists()
    return [json.loads(path.read_text())['last_module'] for path in snapshots]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default=str(ROOT/'validation/module-checkpoints'))
    p.add_argument('--soc',action='store_true',help='Also check every OpenC910 module rule and compare its complete graph/evidence')
    args=p.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    fixtures=ROOT/'validation/low-memory/scoped'
    if not (fixtures/'module/clock-bounded/config.json').is_file():
        subprocess.check_call([sys.executable,str(ROOT/'scripts/check_scoped_trace.py'),'--out',str(fixtures)])
    extraction=importlib.import_module('clocktree.extract')
    runner=extraction.run_verdi
    configs={mode:load_config(fixtures/relative) for mode,relative in (
        ('clock','module/clock-bounded/config.json'),('reset','generate/reset-bounded/config.json'))}
    baseline={}
    report={'cases':[]}
    for mode,config in configs.items():
        directory=out/mode
        directory.mkdir(parents=True,exist_ok=True)
        source=directory/'input.json';write_graph(source,config)
        graph=extraction.extract(source,directory)
        modules=inspect(directory,graph,config)
        baseline[mode]=graph
        report['cases'].append({'case':mode,'pass':True,'module_checkpoints':len(modules),'fresh_memory_checks':len(modules)})
    # Four identical rules share one native query. Rotate after rules 1,2,3:
    # completed_queries and instance count stay fixed, but rule progress must
    # prevent the stalled-retry detector from stopping the third rotation.
    config=copy.deepcopy(configs['clock'])
    config['layers'][0]['modules']=[copy.deepcopy(config['layers'][0]['modules'][0]) for _ in range(4)]
    directory=out/'shared-query-retry';directory.mkdir(exist_ok=True)
    source=directory/'input.json';write_graph(source,config)
    def instrument(command,work,environment,raw,log_path,output,cfg):
        worker=Path(work)/'clocktree-extract.tcl'
        code=worker.read_text(encoding='utf-8')
        worker.write_text(code.replace('if {[catch {ct_main} message]}',ROTATE+'\nif {[catch {ct_main} message]}'),encoding='utf-8')
        return runner(command,work,environment,raw,log_path,output,cfg)
    extraction.run_verdi=instrument
    try:graph=extraction.extract(source,directory)
    finally:extraction.run_verdi=runner
    modules=inspect(directory,graph,config)
    same_graph(baseline['clock'],graph)
    guard=graph['metadata']['memory_protection']
    assert guard['restart_count']==3 and guard['attempts']==4
    assert [event['checkpoint']['completed_modules'] for event in guard['restarts']]==[1,2,3]
    assert [event['checkpoint']['completed_queries'] for event in guard['restarts']]==[0,0,0]
    assert [module['new_instances'] for module in modules[:4]]==[1,0,0,0]
    assert all('CT7_MODULE_SKIP index={}'.format(i) in (directory/'verdi.log').read_text() for i in (1,2,3))
    report['cases'].append({'case':'within_shared_query','pass':True,'module_checkpoints':len(modules),'restarts':3,
        'completed_rules_skipped':True,'full_graph_and_dual_evidence_equal':True})
    # An empty result and a nonexistent scope are both committed as handled
    # rules with explicit failures; neither can certify complete connectivity.
    config=copy.deepcopy(configs['clock'])
    config['layers'][1]['modules'].append({'module':'not_present_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o'})
    config['layers'].append({'index':len(config['layers'])+1,'hierarchy':'scope_top.domain.not_here',
                             'modules':[{'module':'scope_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]})
    directory=out/'failed-lookups';directory.mkdir(exist_ok=True)
    source=directory/'input.json';write_graph(source,config)
    graph=extraction.extract(source,directory)
    modules=inspect(directory,graph,config)
    assert {'missing_module','scope_not_found'}.issubset({m['status'] for m in modules})
    assert not graph['summary']['connectivity_complete']
    report['cases'].append({'case':'zero_match_and_missing_scope','pass':True,'module_checkpoints':len(modules),'not_certified':True})
    directory=out/'memory-disabled';directory.mkdir(exist_ok=True)
    source=directory/'input.json';write_graph(source,dict(configs['clock'],memory_limit_gib=0))
    graph=extraction.extract(source,directory)
    modules=inspect(directory,graph,configs['clock'],enabled=False)
    same_graph(baseline['clock'],graph)
    report['cases'].append({'case':'memory_disabled','pass':True,'module_checkpoints':len(modules),'memory_checks':0})
    if args.soc:
        from check_direct_instances import direct_soc_config
        cfg,source,expected=direct_soc_config(out)
        directory=out/'openc910'
        graph=extraction.extract(source,directory)
        modules=inspect(directory,graph,cfg)
        assert graph['summary']['connectivity_complete'] and {n['id'] for n in graph['nodes']}==expected
        report['soc']={'pass':True,'module_checkpoints':len(modules),'instances':len(expected),
                       'edges':len(graph['edges']),'scope':cfg['trace_scope'],'full_chip':False,'direct_nodes_match_reference':True}
    report['pass']=True
    write_graph(out/'acceptance.json',report)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
