"""Reject hierarchy-truncated RTL and VCS KDB before instance enumeration."""
import argparse
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.storage import write_graph
from clocktree.runtime import read_json
from check_resilience_scale import deep


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',required=True)
    args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    cfg,_,_=deep(out/'fixture',256)
    negative=out/'rtl-truncated';negative.mkdir();write_graph(negative/'input.json',cfg)
    extraction=importlib.import_module('clocktree.extract')
    graph=extraction.extract(negative/'input.json',negative)
    log=(negative/'verdi.log').read_text(encoding='utf-8')
    assert not graph['summary']['connectivity_complete']
    # Some Verdi batch errors still return OS code 0; the worker's committed
    # completion and failed stage are authoritative, not that exit code alone.
    assert graph['metadata']['memory_protection']['state']=='failed'
    assert 'HDL elaboration was truncated' in log and 'CT7_ENUM_BEGIN' not in log
    assert read_json(negative/'run-status.json')['last_failure']['stage']=='import'
    cases=[dict(case='256-deep-RTL-truncation-detected',passed=True,complete_result=False,stopped_before_enumeration=True)]
    write_graph(out/'acceptance.json',dict(passed=False,cases=cases))
    build=out/'kdb';build.mkdir()
    with (build/'build.log').open('w') as log:
        subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all',
                               '-top','deep_top','-f',cfg['design']['filelist'],'-o','simv'],
                              cwd=str(build),stdout=log,stderr=subprocess.STDOUT)
    kdb=build/'simv.daidir/kdb.elab++';assert kdb.is_dir()
    cfg=copy.deepcopy(cfg);cfg['design'].pop('filelist');cfg['design']['elab']=str(kdb)
    directory=out/'kdb-truncated';directory.mkdir();write_graph(directory/'input.json',cfg)
    graph=extraction.extract(directory/'input.json',directory)
    log=(directory/'verdi.log').read_text(encoding='utf-8')
    assert not graph['summary']['connectivity_complete']
    assert graph['metadata']['memory_protection']['state']=='failed'
    assert 'HDL elaboration was truncated' in log and 'CT7_ENUM_BEGIN' not in log
    assert read_json(directory/'run-status.json')['last_failure']['stage']=='import'
    cases.append(dict(case='256-deep-KDB-truncation-detected',passed=True,complete_result=False,stopped_before_enumeration=True))
    cfg,_,_=deep(out/'boundary-fixture',48)
    directory=out/'rtl-boundary';directory.mkdir();write_graph(directory/'input.json',cfg)
    graph=extraction.extract(directory/'input.json',directory)
    log=(directory/'verdi.log').read_text(encoding='utf-8')
    assert not graph['summary']['connectivity_complete']
    assert graph['metadata']['memory_protection']['state']=='failed'
    assert 'Instance search scope has no elaborated module definition' in log
    assert read_json(directory/'run-status.json')['last_failure']['stage']=='modules'
    cases.append(dict(case='48-deep-empty-definition-detected',passed=True,complete_result=False))
    write_graph(out/'acceptance.json',dict(passed=True,cases=cases))
    print(json.dumps(cases),flush=True)


if __name__=='__main__':main()
