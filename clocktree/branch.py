"""Independent branch snapshots; retain exact edges and lazy NPI evidence."""
from collections import defaultdict
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import traceback

from .storage import IndexedGraph, ResultStore
from .l1_inputs import INPUT_SOURCE_KINDS, entries, verified_table
from .profiles import output_ports


class BranchCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise BranchCancelled()


def branch_seed(node):
    if node.get('aggregate'):
        raise ValueError('请先展开 RTL 聚合框，再双击具体器件。')
    if node.get('kind') in INPUT_SOURCE_KINDS:
        entry=node['input_entry']
        if entry.get('aggregate_count') or not entry.get('signal'):
            raise ValueError('请选择一个具体的边界信号。')
        return entry['signal'], None
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


def branch_node(node, inputs, outputs, keep_all, signal):
    from .connectivity import trim_wire
    if signal is not None:
        node=dict(node)
        confirmed={pin for pin,value in entries(node).items() if pin in inputs and value['signal']==signal}
        for field in ('l1_inputs','input_boundaries'):
            if field in node:
                node[field]={pin:value for pin,value in node[field].items() if pin in confirmed
                             and value.get('status')=='verified' and value.get('signal')==signal}
        if node.get('kind')=='wire':
            pairs=[c for c in node.get('internal_connections',[]) if c.get('input') in inputs and c.get('status')=='verified']
            node['internal_connections']=pairs
            outputs=outputs.union(c['output'] for c in pairs)
    trimmed = trim_wire(trim_subsystem(node,inputs), inputs, outputs, keep_all)
    if trimmed.get('inputs_without_source'):
        visible = set(trimmed['inputs'])
        trimmed['inputs_without_source'] = [p for p in trimmed['inputs_without_source'] if p in visible]
    return trimmed


def branch_header(graph, instance, pins, nodes, edges, boundary=None, loads=0):
    view={'instance':instance,'inputs':pins,'scope':'all_upstream_union_all_downstream'}
    if boundary is not None:
        view.update(scope='boundary_downstream',signal=instance,source_id=boundary['id'],direct_loads=loads)
    summary = dict(graph.get('summary', {}), instances=nodes, edges=edges)
    summary.pop('inputs_without_source', None)
    return {'schema_version': graph.get('schema_version', 1),
            'tree_type': graph.get('tree_type', graph.get('config', {}).get('tree_type', 'clock')),
            'config': copy.deepcopy(graph.get('config', {})),
            'presentation': copy.deepcopy(graph.get('presentation', {})),
            'branch_view': view,
            'summary': summary}


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
    boundary=node if node.get('kind') in INPUT_SOURCE_KINDS else None
    directions=('downstream',) if boundary else ('upstream','downstream')
    loads=0
    temporary = tempfile.TemporaryDirectory(prefix='ct7-branch-')
    store = None
    reader = None
    try:
        store = BranchStore(Path(temporary.name)/'branch.sqlite3', model.store)
        store.temporary = temporary
        conn = store.connection()
        conn.set_progress_handler(lambda: int(cancel is not None and cancel.is_set()), 1000)
        conn.execute('ATTACH DATABASE ? AS origin', (str(model.store.path),))
        if not boundary and not conn.execute('SELECT 1 FROM origin.nodes WHERE id=?', (instance,)).fetchone():
            raise ValueError('找不到器件：' + instance)
        for table in ('picked_edges', 'picked_nodes', 'seen', 'front', 'next_front'):
            conn.execute('CREATE TEMP TABLE '+table+'(id TEXT PRIMARY KEY)')
        has_wire=conn.execute("SELECT 1 FROM origin.nodes WHERE kind='wire' LIMIT 1").fetchone()
        if boundary or has_wire or model.graph.legacy_input_coverage:
            # A branch owns its traversal connection. Reusing the caller's
            # connection can make DROP TEMP TABLE collide with a live cursor;
            # worker-local connections on that shared store also outlive jobs.
            reader = ResultStore(model.store.path)
            reader.connection().set_progress_handler(lambda: int(cancel is not None and cancel.is_set()),1000)
        if boundary:
            conn.execute('CREATE TEMP TABLE direct_inputs(instance TEXT,pin TEXT,PRIMARY KEY(instance,pin))')
            conn.execute('CREATE TEMP TABLE wire_outputs(instance TEXT,pin TEXT,PRIMARY KEY(instance,pin))')
            source_conn=reader.connection()
            records=source_conn.execute('SELECT instance,pin FROM '+verified_table(source_conn)+' WHERE signal=?',(instance,))
            conn.executemany('INSERT INTO direct_inputs VALUES(?,?)',records)
            loads=conn.execute('SELECT COUNT(*) FROM direct_inputs').fetchone()[0]
            if not loads:raise ValueError('找不到已确认的边界输入连接：'+instance)
            conn.execute('INSERT OR IGNORE INTO picked_nodes SELECT instance FROM direct_inputs')
        if has_wire:
            from .connectivity import indexed_paths
            from .indexed_view import IndexedGraphView
            source = reader.graph()
            for field in ('config', 'presentation'):
                if field in model.graph:source[field] = model.graph[field]
            traversal = IndexedGraphView(source)
            for direction in directions:
                if boundary:
                    selection=indexed_paths(traversal,None,direction,cancel=cancel,arrivals=conn.execute('SELECT instance,pin FROM direct_inputs'))
                else:selection=indexed_paths(traversal,instance,direction,seed_pins,cancel)
                rows=reader.connection().execute(selection.sql,selection.params)
                try:conn.executemany('INSERT OR IGNORE INTO picked_edges VALUES(?)', rows)
                finally:rows.close()
                del selection
        for direction in (() if has_wire else directions):
            check_cancel(cancel)
            for table in ('seen', 'front', 'next_front'):
                conn.execute('DELETE FROM '+table)
            if boundary:conn.execute('INSERT INTO front SELECT DISTINCT instance FROM direct_inputs')
            else:conn.execute('INSERT INTO front VALUES (?)', (instance,))
            conn.execute('INSERT INTO seen SELECT id FROM front')
            match, other = ('target', 'source') if direction == 'upstream' else ('source', 'target')
            if direction == 'upstream' and seed_pins is not None:
                conn.execute('DELETE FROM front')
                for pin in seed_pins:
                    conn.execute('INSERT OR IGNORE INTO picked_edges SELECT id FROM origin.edges WHERE target=? AND target_pin=?', (instance, pin))
                    conn.execute('INSERT OR IGNORE INTO front SELECT source FROM origin.edges WHERE target=? AND target_pin=?', (instance, pin))
                conn.execute('INSERT OR IGNORE INTO seen SELECT id FROM front')
            while True:
                check_cancel(cancel)
                join = ' FROM front f CROSS JOIN origin.edges e ON e.'+match+'=f.id'
                conn.execute('INSERT OR IGNORE INTO picked_edges SELECT e.id'+join)
                conn.execute('INSERT OR IGNORE INTO next_front SELECT e.'+other+join+
                             ' LEFT JOIN seen v ON e.'+other+'=v.id WHERE v.id IS NULL')
                if not conn.execute('SELECT 1 FROM next_front LIMIT 1').fetchone():
                    break
                conn.execute('INSERT OR IGNORE INTO seen SELECT id FROM next_front')
                conn.execute('DELETE FROM front')
                conn.execute('INSERT INTO front SELECT id FROM next_front')
                conn.execute('DELETE FROM next_front')
        if not boundary:conn.execute('INSERT INTO picked_nodes VALUES (?)', (instance,))
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
            original = model.graph.present_node(json.loads(data), reader.connection() if model.graph.legacy_input_coverage else None)
            legacy_gaps = model.graph.legacy_input_coverage and original.get('inputs_without_source')
            if original.get('kind') not in ('subsystem', 'wire') and not legacy_gaps and not (boundary and (original.get('l1_inputs') or original.get('input_boundaries'))):
                continue
            pins = {row[0] for row in conn.execute('SELECT target_pin FROM edges WHERE target=?', (key,))}
            if boundary:
                pins.update(row[0] for row in conn.execute('SELECT pin FROM direct_inputs WHERE instance=?',(key,)))
            if key == instance and seed_pins is not None:
                pins.update(seed_pins)
            outputs={row[0] for row in conn.execute('SELECT source_pin FROM edges WHERE source=?',(key,))} if original.get('kind')=='wire' else set()
            trimmed=branch_node(original,pins,outputs,not boundary and key==instance,instance if boundary else None)
            if boundary and trimmed['kind']=='wire':
                conn.executemany('INSERT INTO wire_outputs VALUES(?,?)', ((key,pin) for pin in output_ports(trimmed)))
            conn.execute('UPDATE nodes SET data=?,kind=?,component=? WHERE id=?',
                         (json.dumps(trimmed, ensure_ascii=False), trimmed['kind'], trimmed['component'], key))
            conn.execute('DELETE FROM memberships WHERE instance=?', (key,))
            conn.executemany('INSERT INTO memberships VALUES (?,?)',
                             ((key, c) for c in trimmed.get('components', [trimmed['component']])))
        counts = [conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in ('nodes', 'edges')]
        if boundary:
            conn.create_function('ct7_branch_pin',1,lambda data:json.loads(data).get('pin',''))
            conn.execute("""DELETE FROM boundaries WHERE direction='driver'
                AND NOT EXISTS (SELECT 1 FROM direct_inputs d WHERE d.instance=boundaries.instance AND d.pin=ct7_branch_pin(boundaries.data))
                AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.target=boundaries.instance AND e.target_pin=ct7_branch_pin(boundaries.data))""")
            conn.execute("""DELETE FROM boundaries WHERE direction='load'
                AND EXISTS (SELECT 1 FROM nodes n WHERE n.id=boundaries.instance AND n.kind='wire')
                AND NOT EXISTS (SELECT 1 FROM wire_outputs o WHERE o.instance=boundaries.instance AND o.pin=ct7_branch_pin(boundaries.data))""")
        header = branch_header(model.graph, instance, seed_pins, counts[0], counts[1], boundary, loads)
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
    finally:
        if reader is not None:reader.close()


def build_branch(model, node, cancel=None):
    check_cancel(cancel)
    if isinstance(model.graph, IndexedGraph):
        return _indexed_branch(model, node, cancel)
    instance, seed_pins = branch_seed(node)
    boundary=node if node.get('kind') in INPUT_SOURCE_KINDS else None
    if not boundary and instance not in model.original:
        raise ValueError('找不到器件：' + instance)
    selected = set()
    from .connectivity import walk_edges
    pins, outputs, edges = defaultdict(set), defaultdict(set), []
    direct=[]
    if boundary:
        for key,original in model.original.items():
            check_cancel(cancel)
            for pin,entry in entries(original).items():
                if entry['signal']==instance:
                    pins[key].add(pin);direct.append((key,pin))
        if not direct:raise ValueError('找不到已确认的边界输入连接：'+instance)
        ids=set(pins)
        selected.update(e['id'] for e in walk_edges(model,None,'downstream',cancel=cancel,arrivals=direct))
    else:
        ids={instance}
        for direction in ('upstream', 'downstream'):
            selected.update(e['id'] for e in walk_edges(model,instance,direction,seed_pins,cancel))
    for key in sorted(selected):
        check_cancel(cancel)
        edge = model.original_edges[key]
        edges.append(edge)
        ids.update((edge['source'], edge['target']))
        pins[edge['target']].add(edge['target_pin'])
        outputs[edge['source']].add(edge['source_pin'])
    if seed_pins is not None:
        pins[instance].update(seed_pins)
    nodes = [branch_node(model.original[key],pins[key],outputs[key],not boundary and key==instance,instance if boundary else None) for key in sorted(ids)]
    graph = branch_header(model.graph, instance, seed_pins, len(nodes), len(edges), boundary,len(direct))
    wire_outputs={n['id']:set(output_ports(n)) for n in nodes if boundary and n.get('kind')=='wire'}
    def visible_record(record):
        key=record.get('instance')
        if key not in ids:return False
        if boundary and record.get('direction')=='driver':return record.get('pin') in pins[key]
        if key in wire_outputs and record.get('direction')=='load':return record.get('pin') in wire_outputs[key]
        return True
    graph.update(nodes=nodes, edges=edges,
                 boundaries=[b for b in model.graph.get('boundaries', []) if visible_record(b)])
    return graph


class BranchJob:
    """Cancellable preparation with no Tk calls or all-design evidence copies."""
    def __init__(self, model, node):
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.result = self.error = None
        self.error_detail = None
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
                self.error_detail = traceback.format_exc()
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
