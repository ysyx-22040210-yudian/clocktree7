"""Reproducible Linux RSS measurement; run each mode in a fresh process."""
import argparse
import gc
import json
from pathlib import Path
import resource
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.graph import build_graph, read_events
from clocktree.indexed_view import IndexedGraphView
from clocktree.session import read_graph, snapshot_graph
from clocktree.storage import new_store, index_graph, write_graph, publish_index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['baseline-build', 'indexed-build', 'baseline-open', 'indexed-open'], required=True)
    parser.add_argument('--raw')
    parser.add_argument('--config')
    parser.add_argument('--result')
    parser.add_argument('--baseline-ref', default='99d528a66d05a71cdbdcbe95f465d05c746007ee')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started, samples = time.monotonic(), []
    def sample(stage):
        with open('/proc/self/status') as stream:
            rss = next(float(line.split()[1])/1024 for line in stream if line.startswith('VmRSS:'))
        samples.append({'stage': stage, 'rss_mib': round(rss, 2),
                        'peak_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2),
                        'seconds': round(time.monotonic()-started, 3)})
    sample('start')
    if args.mode.endswith('build'):
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        if args.mode.startswith('baseline'):
            # Execute the repository's actual pre-optimization builder, not a
            # synthetic approximation, without modifying the working tree.
            code = subprocess.check_output(['git', 'show', args.baseline_ref + ':clocktree/graph.py'], cwd=str(ROOT))
            module = types.ModuleType('clocktree._baseline_graph')
            module.__package__ = 'clocktree'
            exec(compile(code.decode('utf-8'), '<baseline graph.py>', 'exec'), module.__dict__)
            graph = module.build_graph(module.read_events(args.raw), config)
            sample('graph_built')
            (out / 'baseline.json').write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding='utf-8')
            sample('json_written')
        else:
            store = new_store(out)
            graph = build_graph(read_events(args.raw), config, store)
            sample('graph_built')
            indexed = index_graph(graph, store)
            del graph
            gc.collect()
            sample('index_ready')
            graph = indexed
            write_graph(out / 'clocktree.json', graph)
            publish_index(out / 'clocktree.json', graph)
            sample('json_written')
    elif args.mode == 'baseline-open':
        with Path(args.result).open(encoding='utf-8') as stream:
            graph = json.load(stream)
        sample('json_loaded')
        from clocktree.viewmodel import GraphView
        view = GraphView(graph).project('hierarchy')
        sample('overview')
        frozen = snapshot_graph(graph)
        sample('session_snapshot')
    else:
        graph = read_graph(args.result)
        sample('index_opened')
        view = IndexedGraphView(graph).project('hierarchy')
        sample('overview')
        frozen = snapshot_graph(graph)
        sample('session_snapshot')
    report = {'mode': args.mode, 'samples': samples, 'summary': graph['summary'],
              'raw': str(args.raw), 'result': str(args.result), 'python': sys.version,
              'scope': 'Python postprocessing/viewer only; excludes Verdi/KDB import'}
    (out / (args.mode + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
