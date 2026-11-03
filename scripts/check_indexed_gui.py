"""Native Tk acceptance: real indexed SoC, paging, evidence and fresh-process restore."""
import argparse
import copy
import json
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.gui import ClockTreeApp
from clocktree.session import read_graph
from clocktree.storage import IndexedGraph, new_store, write_graph, publish_index
from clocktree.indexed_view import IndexedGraphView, DETAIL_LIMIT, OVERVIEW_LIMIT
from capture_x11 import x11_image


def pump(app, predicate=lambda: True):
    start = time.monotonic()
    while not predicate():
        app.update()
        if time.monotonic()-start > 300:
            raise AssertionError(app.status.get())
        time.sleep(.01)
    for _ in range(8):
        if app._destroying:
            break
        app.update()
        time.sleep(.02)


def capture(app, path):
    app.update()
    image = x11_image()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    image.crop((x, y, x+app.winfo_width(), y+app.winfo_height())).save(str(path))


def stress_index(directory, count):
    store = new_store(directory)
    example = read_graph(ROOT / 'examples/flexible_layers/clocktree.json')
    config = copy.deepcopy(example['config'])
    config['design'] = {'top': 'stress', 'elab': '/synthetic/not-an-eda-run'}
    config['trace_scope'] = ''
    config['layers'] = [{'index': i, 'hierarchy': 'stress', 'modules': []} for i in (1, 2)]
    root = 'stress.domain.root'
    node = dict(id=root, path=root, layer=1, component=0, module='gate', kind='gate', inputs=['i'], output='o', root=True, terminal=False)
    store.put('nodes', 0, node)
    for i in range(count):
        path = 'stress.domain.g{:06d}.u'.format(i)
        store.put('nodes', i+1, dict(node, id=path, path=path, layer=2, root=False, terminal=True))
        store.put('edges', i, dict(id='edge{}'.format(i), source=root, target=path, source_pin='o', target_pin='i', layer_delta=1, evidence={}))
    summary = dict(instances=count+1, edges=count, roots=1, terminals=count, components=1, layers={'1': 1, '2': count},
                   terminal_layers={'2': count}, unresolved=0, diagnostics=0, connectivity_complete=False, topology_valid=True)
    store.finish(dict(schema_version=1, config=config, tree_type='clock', metadata={'synthetic': True, 'purpose': 'GUI scale acceptance; not NPI proof'},
                      summary=summary, array_fields=['nodes', 'edges', 'boundaries', 'components', 'diagnostics', 'clock_sources']))
    return store.graph()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / 'validation/low-memory/gui'))
    parser.add_argument('--result', default=str(ROOT / 'validation/runs/openc910-ordered7-01/clocktree.json'))
    parser.add_argument('--phase', choices=['write', 'read', 'stress'])
    parser.add_argument('--stress-count', type=int, default=100000)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.phase is None:
        for phase in ('write', 'read', 'stress'):
            subprocess.check_call([sys.executable, __file__, '--out', str(out), '--result', args.result,
                                   '--phase', phase, '--stress-count', str(args.stress_count)])
        reports = {phase: json.loads((out / (phase + '.json')).read_text()) for phase in ('write', 'read', 'stress')}
        write_graph(out / 'acceptance.json', {'pass': True, 'phases': reports})
        print(json.dumps(reports, indent=2))
        return
    errors = []
    app = ClockTreeApp(runs=out / 'runs', session_dir=out / 'session', remember=args.phase != 'stress')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title, error: errors.append(title + ': ' + str(error))
    pump(app, lambda: not app.busy)
    if args.phase == 'write':
        reset = ROOT / 'validation/low-memory/scoped/generate/reset-bounded/resettree.json'
        app.load_result(reset)
        pump(app, lambda: not app.busy)
        assert isinstance(app.graph, IndexedGraph)
        app.switch_tree_type('clock')
        staged = out / 'source/clocktree.json'
        staged.parent.mkdir(exist_ok=True)
        shutil.copy2(args.result, str(staged))
        app.load_result(staged)
        pump(app, lambda: not app.busy)
        assert isinstance(app.graph, IndexedGraph) and isinstance(app.diagram.model, IndexedGraphView)
        assert len(app.diagram.model.original) == 6984 and len(app.graph['edges']) == 6983
        assert app.diagram.mode == 'hierarchy' and len(app.diagram.model.nodes) <= OVERVIEW_LIMIT
        capture(app, out / 'indexed-soc-overview.png')
        node = next(n for n in app.graph['nodes'] if n['terminal'])
        app.search_var.set(node['id'])
        app.locate_query()
        pump(app)
        assert node['id'] in app.prop_text.get('1.0', 'end')
        edge = app.diagram.model.incoming[node['id']][0]
        app.inspect_edges([edge])
        assert list(edge['evidence']['driver'])[0]['name'] in app.evidence_text.get('1.0', 'end')
        capture(app, out / 'indexed-soc-evidence.png')
        app.export_result(out / 'portable-export.json')
        assert len(read_graph(out / 'portable-export.json')['edges']) == 6983
        app.diagram.reset_scope()
        app.close(); pump(app, lambda: app._destroying)
        for manifest in (out / 'session').glob('clock-*.json'):
            assert manifest.stat().st_size < 50000 and manifest.with_suffix('.sqlite3').is_file()
        staged.rename(staged.with_suffix('.json.moved'))
    elif args.phase == 'read':
        assert isinstance(app.graph, IndexedGraph) and len(app.graph['nodes']) == 6984
        assert len(app.diagram.model.nodes) <= OVERVIEW_LIMIT and '自动恢复' in app.status.get()
        assert isinstance(app._workspaces['reset']['graph'], IndexedGraph)
        capture(app, out / 'indexed-session-restored.png')
        app.switch_tree_type('reset')
        assert len(app.graph['nodes']) == 5 and len(app.graph['edges']) == 3
        app.close(); pump(app, lambda: app._destroying)
    else:
        graph = stress_index(out / 'stress', args.stress_count)
        app.show_graph(graph)
        pump(app)
        assert len(app.diagram.model.nodes) <= OVERVIEW_LIMIT
        assert sum(len(n['members']) if n.get('aggregate') else 1 for n in app.diagram.model.nodes.values()) == args.stress_count+1
        root = app.diagram.model.original['stress.domain.root']
        app.diagram.select(root)
        pump(app)
        assert len(app._connection_rows) == args.stress_count and len(app.related_edges) == 500
        app.connection_page(1)
        assert app._connection_offset == 500 and len(app.related_edges) == 500
        capture(app, out / 'indexed-100k-overview.png')
        group = next(n for n in app.diagram.model.nodes.values() if n.get('aggregate'))
        app.diagram.expand(group)
        pump(app)
        assert app.diagram.model.pages > 1 and app.view_next.winfo_manager()
        app.diagram.model.page = app.diagram.model.pages-1
        app.diagram.rebuild()
        pump(app)
        assert len(app.diagram.model.nodes) <= OVERVIEW_LIMIT
        capture(app, out / 'indexed-100k-paged.png')
        app.search_var.set('stress.domain.g{:06d}.u'.format(args.stress_count-1))
        app.locate_query()
        pump(app)
        assert len(app.diagram.model.nodes) <= DETAIL_LIMIT
        assert app.diagram.trace('upstream') == 1
        app.diagram.locate(root)
        assert app.diagram.trace('downstream') == args.stress_count
        pump(app)
        assert len(app.diagram.model.original) == args.stress_count+1
        app.close(); pump(app, lambda: app._destroying)
    assert not errors, errors
    report = {'pass': True, 'phase': args.phase, 'peak_rss_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2),
              'callback_errors': errors, 'stress_count': args.stress_count if args.phase == 'stress' else None}
    write_graph(out / (args.phase + '.json'), report)


if __name__ == '__main__':
    main()
