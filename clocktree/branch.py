"""Independent branch snapshots; retain exact edges and lazy NPI evidence."""
from collections import defaultdict, deque
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading

from .storage import IndexedGraph, ResultStore


class BranchCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise BranchCancelled()


def branch_seed(node):
    if node.get('aggregate'):
        raise ValueError('请先展开 RTL 聚合框，再双击具体器件。')
    return node.get('reference_of', node['id']), list(node['inputs']) if node.get('kind') == 'subsystem' else None


def trim_subsystem(node, pins):
    if node.get('kind') != 'subsystem':
        return dict(node)
    node = dict(node, inputs=[pin for pin in node['inputs'] if pin in pins])
    if 'input_components' in node:
        node['input_components'] = {pin: component for pin, component in node['input_components'].items() if pin in pins}
        components = sorted(set(node['input_components'].values()))
        if components:
            node.update(component=components[0], components=components)
    return node


def branch_header(graph, instance, pins, nodes, edges):
    return {'schema_version': graph.get('schema_version', 1),
            'tree_type': graph.get('tree_type', graph.get('config', {}).get('tree_type', 'clock')),
            'config': copy.deepcopy(graph.get('config', {})),
            'presentation': copy.deepcopy(graph.get('presentation', {})),
            'branch_view': {'instance': instance, 'inputs': pins,
                            'scope': 'all_upstream_union_all_downstream'},
            'summary': dict(graph.get('summary', {}), instances=nodes, edges=edges)}


class BranchStore(ResultStore):
    """Only branch records are copied. Evidence paths stay in the source index."""
    def __init__(self, path, source):
        self.evidence_store = getattr(source, 'evidence_store', source)
        super().__init__(path, create=True)

    def decode(self, data):
        return self.evidence_store.decode(data)


def close_branch(graph):
    if isinstance(graph, IndexedGraph) and isinstance(graph.store, BranchStore):
        graph.store.close()
        if getattr(graph.store, 'temporary', None):
            graph.store.temporary.cleanup()
            graph.store.temporary = None


def _indexed_branch(model, node, cancel):
    instance, seed_pins = branch_seed(node)
    temporary = tempfile.TemporaryDirectory(prefix='ct7-branch-')
    store = None
    try:
        store = BranchStore(Path(temporary.name)/'branch.sqlite3', model.store)
        store.temporary = temporary
        conn = store.connection()
        conn.set_progress_handler(lambda: int(cancel is not None and cancel.is_set()), 1000)
        conn.execute('ATTACH DATABASE ? AS origin', (str(model.store.path),))
        if not conn.execute('SELECT 1 FROM origin.nodes WHERE id=?', (instance,)).fetchone():
            raise ValueError('找不到器件：' + instance)
        for table in ('picked_edges', 'picked_nodes', 'seen', 'front', 'next_front'):
            conn.execute('CREATE TEMP TABLE '+table+'(id TEXT PRIMARY KEY)')
        has_wire=conn.execute("SELECT 1 FROM origin.nodes WHERE kind='wire' LIMIT 1").fetchone()
        if has_wire:
            from .connectivity import indexed_paths
            for direction in ('upstream','downstream'):
                selection=indexed_paths(model,instance,direction,seed_pins,cancel)
                conn.executemany('INSERT OR IGNORE INTO picked_edges VALUES(?)', model.store.connection().execute(selection.sql,selection.params))
                del selection
        for direction in (() if has_wire else ('upstream', 'downstream')):
            check_cancel(cancel)
            for table in ('seen', 'front', 'next_front'):
                conn.execute('DELETE FROM '+table)
            conn.execute('INSERT INTO seen VALUES (?)', (instance,))
            conn.execute('INSERT INTO front VALUES (?)', (instance,))
            match, other = ('target', 'source') if direction == 'upstream' else ('source', 'target')
            if direction == 'upstream' and seed_pins is not None:
                conn.execute('DELETE FROM front')
                for pin in seed_pins:
                    conn.execute('INSERT OR IGNORE INTO picked_edges SELECT id FROM origin.edges WHERE target=? AND target_pin=?', (instance, pin))
                    conn.execute('INSERT OR IGNORE INTO front SELECT source FROM origin.edges WHERE target=? AND target_pin=?', (instance, pin))
                conn.execute('INSERT OR IGNORE INTO seen SELECT id FROM front')
            while True:
                check_cancel(cancel)
                join = ' FROM origin.edges e JOIN front f ON e.'+match+'=f.id'
                conn.execute('INSERT OR IGNORE INTO picked_edges SELECT e.id'+join)
                conn.execute('INSERT OR IGNORE INTO next_front SELECT e.'+other+join+
                             ' LEFT JOIN seen v ON e.'+other+'=v.id WHERE v.id IS NULL')
                if not conn.execute('SELECT 1 FROM next_front LIMIT 1').fetchone():
                    break
                conn.execute('INSERT OR IGNORE INTO seen SELECT id FROM next_front')
                conn.execute('DELETE FROM front')
                conn.execute('INSERT INTO front SELECT id FROM next_front')
                conn.execute('DELETE FROM next_front')
        conn.execute('INSERT INTO picked_nodes VALUES (?)', (instance,))
        for endpoint in ('source', 'target'):
            conn.execute('INSERT OR IGNORE INTO picked_nodes SELECT e.'+endpoint+' FROM origin.edges e JOIN picked_edges p ON e.id=p.id')
        for table, selected in (('nodes', 'picked_nodes'), ('edges', 'picked_edges')):
            conn.execute('INSERT INTO '+table+' SELECT s.* FROM origin.'+table+' s JOIN '+selected+' p ON s.id=p.id')
        conn.execute('INSERT INTO memberships SELECT s.* FROM origin.memberships s JOIN picked_nodes p ON s.instance=p.id')
        conn.execute('INSERT INTO boundaries SELECT s.* FROM origin.boundaries s JOIN picked_nodes p ON s.instance=p.id')
        # Unrelated inputs of downstream subsystems must not appear as phantom
        # disconnected branches. The selected subsystem keeps its seed inputs.
        for key, data in conn.execute('SELECT id,data FROM nodes'):
            check_cancel(cancel)
            original = model.graph.present_node(json.loads(data))
            if original.get('kind') not in ('subsystem', 'wire'):
                continue
            pins = {row[0] for row in conn.execute('SELECT target_pin FROM edges WHERE target=?', (key,))}
            if key == instance and seed_pins is not None:
                pins.update(seed_pins)
            if original.get('kind')=='wire':
                from .connectivity import trim_wire
                outputs={row[0] for row in conn.execute('SELECT source_pin FROM edges WHERE source=?',(key,))}
                trimmed=trim_wire(original,pins,outputs,key==instance)
            else:trimmed = trim_subsystem(original, pins)
            conn.execute('UPDATE nodes SET data=?,kind=?,component=? WHERE id=?',
                         (json.dumps(trimmed, ensure_ascii=False), trimmed['kind'], trimmed['component'], key))
            conn.execute('DELETE FROM memberships WHERE instance=?', (key,))
            conn.executemany('INSERT INTO memberships VALUES (?,?)',
                             ((key, c) for c in trimmed.get('components', [trimmed['component']])))
        counts = [conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('nodes', 'edges')]
        header = branch_header(model.graph, instance, seed_pins, *counts)
        conn.commit()
        conn.execute('DETACH DATABASE origin')
        store.finish(header)
        graph = store.graph()
        conn.set_progress_handler(None, 0)
        store.close()  # The Tk thread opens its own connection when ready.
        return graph
    except BaseException as error:
        if store is not None:
            store.close()
            store.temporary = None
        temporary.cleanup()
        if isinstance(error, sqlite3.OperationalError):
            check_cancel(cancel)
        raise


def build_branch(model, node, cancel=None):
    check_cancel(cancel)
    if isinstance(model.graph, IndexedGraph):
        return _indexed_branch(model, node, cancel)
    instance, seed_pins = branch_seed(node)
    if instance not in model.original:
        raise ValueError('找不到器件：' + instance)
    selected = set()
    from .connectivity import walk_edges, trim_wire
    for direction in ('upstream', 'downstream'):
        selected.update(e['id'] for e in walk_edges(model,instance,direction,seed_pins,cancel))
    ids, pins, outputs, edges = {instance}, defaultdict(set), defaultdict(set), []
    for key in sorted(selected):
        check_cancel(cancel)
        edge = model.original_edges[key]
        edges.append(edge)
        ids.update((edge['source'], edge['target']))
        pins[edge['target']].add(edge['target_pin'])
        outputs[edge['source']].add(edge['source_pin'])
    if seed_pins is not None:
        pins[instance].update(seed_pins)
    nodes = [trim_wire(trim_subsystem(model.original[key], pins[key]),pins[key],outputs[key],key==instance) for key in sorted(ids)]
    graph = branch_header(model.graph, instance, seed_pins, len(nodes), len(edges))
    graph.update(nodes=nodes, edges=edges,
                 boundaries=[b for b in model.graph.get('boundaries', []) if b.get('instance') in ids])
    return graph


class BranchJob:
    """Cancellable preparation with no Tk calls or all-design evidence copies."""
    def __init__(self, model, node):
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.result = self.error = None
        def worker():
            graph = None
            try:
                graph = build_branch(model, node, self.cancel)
                with self.lock:
                    if not self.cancel.is_set():
                        self.result, graph = graph, None
            except BranchCancelled:
                pass
            except Exception as error:
                self.error = str(error)
            finally:
                if graph is not None:
                    close_branch(graph)
                self.done.set()
        self.thread = threading.Thread(target=worker, daemon=True)
        self.thread.start()

    def take(self):
        with self.lock:
            graph, self.result = self.result, None
        return graph, self.error

    def close(self):
        with self.lock:
            self.cancel.set()
            graph, self.result = self.result, None
        if graph is not None:
            close_branch(graph)
