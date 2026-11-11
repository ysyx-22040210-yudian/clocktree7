"""Bounded, query-on-expansion presentation of a disk indexed complete graph."""
from collections import Counter
import json
from collections.abc import Mapping, Sequence
import uuid

from .storage import RecordSequence
from .viewmodel import GraphView, COLUMN_STEP
from .profiles import tree_type


DETAIL_LIMIT = 12000
OVERVIEW_LIMIT = 1600
BUNDLE_LIMIT = 8000


class QuerySequence(Sequence):
    def __init__(self, store, sql, params=(), decode=False, count=None):
        self.store, self.sql, self.params, self.decode = store, sql, tuple(params), decode
        self.count = count

    def __len__(self):
        if self.count is None:
            self.count = self.store.connection().execute('SELECT COUNT(*) FROM (' + self.sql + ')', self.params).fetchone()[0]
        return self.count

    def __iter__(self):
        for value, in self.store.connection().execute(self.sql, self.params):
            yield self.store.decode(value) if self.decode else value

    def __getitem__(self, key):
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            if step < 0:
                return list(self)[key]
            rows = self.store.connection().execute('SELECT * FROM (' + self.sql + ') LIMIT ? OFFSET ?',
                                                    self.params + (max(0, stop-start), start))
            return [self.store.decode(row[0]) if self.decode else row[0] for row in rows][::step]
        if key < 0:
            key += len(self)
        if not 0 <= key < len(self):
            raise IndexError(key)
        row = self.store.connection().execute('SELECT * FROM (' + self.sql + ') LIMIT 1 OFFSET ?', self.params + (key,)).fetchone()
        return self.store.decode(row[0]) if self.decode else row[0]

    def __contains__(self, key):
        if not self.decode:
            return self.store.connection().execute('SELECT 1 FROM (' + self.sql + ') WHERE id=? LIMIT 1', self.params + (key,)).fetchone() is not None
        return super().__contains__(key)


class RecordMap(Mapping):
    def __init__(self, graph, field):
        self.graph, self.store, self.field = graph, graph.store, field

    def __len__(self):
        return len(self.graph[self.field])

    def __iter__(self):
        return iter(RecordSequence(self.store, self.field, ids=True))

    def __getitem__(self, key):
        row = self.store.connection().execute('SELECT data FROM ' + self.field + ' WHERE id=?', (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        record = self.store.decode(row[0])
        return self.graph.present_node(record) if self.field == 'nodes' else record


class Adjacency:
    def __init__(self, store, endpoint):
        self.store, self.endpoint = store, endpoint

    def __getitem__(self, key):
        return RecordSequence(self.store, 'edges', self.endpoint + '=?', (key,))


class SQLSelection:
    """A highlight set that can contain millions of edge IDs without a Python set."""
    def __init__(self, store, sql, params=(), temporary=None):
        self.store, self.sql, self.params, self.temporary = store, sql, tuple(params), temporary
        self.count = None

    def __len__(self):
        if self.count is None:
            self.count = self.store.connection().execute('SELECT COUNT(*) FROM (' + self.sql + ')', self.params).fetchone()[0]
        return self.count

    def intersection(self, members):
        conn = self.store.connection()
        if isinstance(members, QuerySequence):
            row = conn.execute('SELECT 1 FROM (' + self.sql + ') a JOIN (' + members.sql +
                               ') b ON a.id=b.id LIMIT 1', self.params + members.params).fetchone()
            return {True} if row else set()
        for key in members:
            if conn.execute('SELECT 1 FROM (' + self.sql + ') WHERE id=? LIMIT 1', self.params + (key,)).fetchone():
                return {True}
        return set()

    def __del__(self):
        if self.temporary:
            try:
                self.store.connection().execute('DROP TABLE IF EXISTS ' + self.temporary)
            except Exception:
                pass


class IndexedGraphView(GraphView):
    def __init__(self, graph, layout_mode='branch'):
        self.graph, self.store = graph, graph.store
        self.layout_mode=layout_mode
        self.tree_type = tree_type(graph.get('config', graph))
        self.original, self.original_edges = RecordMap(graph, 'nodes'), RecordMap(graph, 'edges')
        self.incoming, self.outgoing = Adjacency(self.store, 'target'), Adjacency(self.store, 'source')
        row = self.store.connection().execute('SELECT MAX(layer) FROM nodes').fetchone()
        self.layer_count = max(2, len(graph.get('config', {}).get('layers', [])), row[0] or 0)
        self.nodes, self.edges, self.positions = {}, [], {}
        self.actual_count = 0
        self.bounds = (0, 0, self.layer_count * COLUMN_STEP + 36, 560)
        self.effective_mode, self.effective_depth, self.focus = 'hierarchy', 5, None
        self.focus_edge = None
        self.page, self.pages, self.page_signature, self.external_edges = 0, 1, None, 0

    @staticmethod
    def condition(component, scope, alias=''):
        prefix = alias + '.' if alias else ''
        clauses, params = [], []
        if component is not None:
            clauses.append(prefix + 'id IN (SELECT instance FROM memberships WHERE component=?)')
            params.append(component)
        if scope:
            clauses.append('ct7_scope(' + prefix + 'id,?)')
            params.append(scope)
        return ' AND '.join(clauses) or '1', tuple(params)

    def component_edge_filter(self,component):
        if component is None:return '',()
        conn=self.store.connection()
        if not conn.execute("SELECT 1 FROM nodes WHERE kind='wire' LIMIT 1").fetchone():return '',()
        # Private disk-backed port index, built once per immutable result /
        # connection. Old result indexes need no destructive schema migration.
        if not conn.execute("SELECT 1 FROM sqlite_temp_master WHERE name='ct7_wire_outputs'").fetchone():
            conn.execute('CREATE TEMP TABLE ct7_wire_outputs(instance TEXT,pin TEXT,component INTEGER,PRIMARY KEY(instance,pin))')
            for data, in conn.execute("SELECT data FROM nodes WHERE kind='wire'"):
                node=json.loads(data)
                conn.executemany('INSERT INTO ct7_wire_outputs VALUES(?,?,?)',
                    ((node['id'],pin,node.get('output_components',{}).get(pin,node['component'])) for pin in node.get('outputs',[])))
        return " AND (s.kind IS NULL OR s.kind!='wire' OR EXISTS (SELECT 1 FROM ct7_wire_outputs wp WHERE wp.instance=e.source AND wp.pin=e.source_pin AND wp.component=?))",(component,)

    def project(self, mode='instances', component=None, scope='', depth=5):
        conn = self.store.connection()
        where, params = self.condition(component, scope)
        self.actual_count = conn.execute('SELECT COUNT(*) FROM nodes WHERE ' + where, params).fetchone()[0]
        self.focus = None
        self.focus_edge = None
        signature = (component, scope, depth, mode)
        if signature != self.page_signature:
            self.page = 0
        self.page_signature = signature
        self.pages, self.external_edges = 1, 0
        if mode == 'instances' and self.actual_count <= DETAIL_LIMIT:
            self.effective_mode = 'instances'
            self.nodes = {n['id']: self.graph.present_node(n) for n in RecordSequence(self.store, 'nodes', where, params)}
            sw, sp = self.condition(component, scope, 's')
            tw, tp = self.condition(component, scope, 't')
            sql = 'SELECT e.data FROM edges e JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target WHERE ' + sw + ' AND ' + tw
            extra,ep=self.component_edge_filter(component)
            self.edges = [self.store.decode(row[0]) for row in conn.execute(sql+extra, sp + tp + ep)]
        else:
            self.effective_mode = 'hierarchy'
            self._overview(component, scope, max(1, depth))
        self._references(component)
        self._layout()
        return self

    def _overview(self, component, scope, depth):
        conn = self.store.connection()
        where, params = self.condition(component, scope)
        collapse = False
        paged = False
        while True:
            component_expr = 'component-component' if collapse else 'component'
            grouping = component_expr + ',layer,ct7_prefix(id,{})'.format(depth)
            count = conn.execute('SELECT COUNT(*) FROM (SELECT 1 FROM nodes WHERE ' + where +
                                 ' GROUP BY ' + grouping + ' LIMIT ?)', params + (OVERVIEW_LIMIT+1,)).fetchone()[0]
            if count <= OVERVIEW_LIMIT or (depth == 1 and collapse):
                break
            if scope:
                # Explicit expansion pages a wide scope instead of reducing
                # back to the same aggregate forever. The global overview
                # still accounts for every instance without paging.
                paged = True
                count = conn.execute('SELECT COUNT(*) FROM (SELECT 1 FROM nodes WHERE ' + where +
                                     ' GROUP BY ' + grouping + ')', params).fetchone()[0]
                self.pages = max(1, (count + OVERVIEW_LIMIT-1)//OVERVIEW_LIMIT)
                self.page = min(max(0, self.page), self.pages-1)
                break
            if depth > 1:
                depth -= 1
            else:
                collapse = True  # aggregate independent networks, never invent edges
        self.effective_depth = depth
        sql = ('SELECT ' + component_expr + ',layer,ct7_prefix(id,{}),COUNT(*),MIN(id),SUM(root),SUM(terminal),'
               'MAX(unresolved),MAX(scope_in),MAX(scope_out),COUNT(DISTINCT component) FROM nodes WHERE '.format(depth) +
               where + ' GROUP BY ' + grouping)
        sql += ' ORDER BY ' + grouping
        if paged:
            sql += ' LIMIT {} OFFSET {}'.format(OVERVIEW_LIMIT, self.page*OVERVIEW_LIMIT)
        groups, nodes = {}, {}
        for c, layer, path, count, first, roots, terminals, unresolved, scope_in, scope_out, networks in conn.execute(sql, params):
            condition = where + ' AND layer=? AND ct7_prefix(id,?)=?' + ('' if collapse else ' AND component=?')
            gp = params + (layer, depth, path) + (() if collapse else (c,))
            if count == 1:
                node = dict(self.original[first])
                if collapse:
                    node['component'] = 0
            else:
                members = QuerySequence(self.store, 'SELECT id FROM nodes WHERE ' + condition, gp, count=count)
                modules, kinds = Counter(), Counter()
                for module, kind, n in conn.execute('SELECT module,kind,COUNT(*) FROM nodes WHERE ' + condition + ' GROUP BY module,kind', gp):
                    modules[module] += n
                    kinds[kind or 'unclassified'] += n
                if self.graph.get('presentation', {}).get('kind_overrides'):
                    kinds = self.graph.kind_counts(condition, gp)
                node = dict(id='group:{}:{}:{}'.format(c, layer, path), path=path, layer=layer, component=c,
                    aggregate=True, members=members, module_counts=modules, kind_counts=kinds,
                    module='{} 个实例 · {} 种模块'.format(count, len(modules)), inputs=['输入'], output='输出',
                    root_count=roots, terminal_count=terminals, root=bool(roots), terminal=bool(terminals),
                    has_unresolved_output=bool(unresolved), scope_input_boundary=bool(scope_in),
                    scope_output_boundary=bool(scope_out), internal_edges=0, network_count=networks,
                    _condition=condition, _params=gp)
            groups[(c, layer, path)] = node
            nodes[node['id']] = node
        sw, sp = self.condition(component, scope, 's')
        tw, tp = self.condition(component, scope, 't')
        cs, ct = ('s.component-s.component', 't.component-t.component') if collapse else ('s.component', 't.component')
        fields = cs + ',s.layer,ct7_prefix(s.id,{0}),{1},t.layer,ct7_prefix(t.id,{0}),e.source_pin,e.target_pin'.format(depth, ct)
        extra,ep=self.component_edge_filter(component)
        sql = ('SELECT ' + fields + ',COUNT(*) FROM edges e JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target WHERE ' +
               sw + ' AND ' + tw + extra + ' GROUP BY ' + fields)
        bundles = {}
        for sc, sl, sn, tc, tl, tn, source_pin, target_pin, count in conn.execute(sql, sp + tp + ep):
            source, target = groups.get((sc, sl, sn)), groups.get((tc, tl, tn))
            if source is None or target is None:
                if source is not None or target is not None:
                    self.external_edges += count
                continue
            if source['id'] == target['id']:
                source['internal_edges'] = source.get('internal_edges', 0) + count
                continue
            source_pin = '输出' if source.get('aggregate') else source_pin
            target_pin = '输入' if target.get('aggregate') else target_pin
            key = (source['id'], source_pin, target['id'], target_pin)
            if key in bundles:
                bundles[key]['count'] += count
                continue
            sm, smp = self.member_condition(source, 's')
            tm, tmp = self.member_condition(target, 't')
            pins, pp = [], []
            if not source.get('aggregate'):
                pins.append('e.source_pin=?'); pp.append(source_pin)
            if not target.get('aggregate'):
                pins.append('e.target_pin=?'); pp.append(target_pin)
            query = 'SELECT e.id AS id FROM edges e JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target WHERE (' + sm + ') AND (' + tm + ')'
            if pins:
                query += ' AND ' + ' AND '.join(pins)
            query += extra
            bundles[key] = dict(id='bundle:{}'.format(len(bundles)), source=source['id'], target=target['id'],
                source_pin=source_pin, target_pin=target_pin, layer_delta=tl-sl, count=count,
                members=QuerySequence(self.store, query, smp + tmp + tuple(pp) + ep))
            if len(bundles) > BUNDLE_LIMIT and depth > 1 and not paged:
                return self._overview(component, scope, depth-1)
        self.nodes, self.edges = nodes, list(bundles.values())
        for bundle in self.edges:
            bundle['members'].count = bundle['count']
        if paged:
            self.actual_count = sum(len(node['members']) if node.get('aggregate') else 1 for node in self.nodes.values())

    @staticmethod
    def member_condition(node, alias=''):
        prefix = alias + '.' if alias else ''
        if not node.get('aggregate'):
            return prefix + 'id=?', (node['id'],)
        # Use an indexed subquery; do not build a Python set of all members.
        return prefix + 'id IN (SELECT id FROM nodes WHERE ' + node['_condition'] + ')', node['_params']

    def connections_for(self, node):
        sw, sp = self.member_condition(node, 's')
        tw, tp = self.member_condition(node, 't')
        if node.get('aggregate'):
            where = '((' + sw + ') != (' + tw + '))'
        else:
            where = '((' + sw + ') OR (' + tw + '))'
        sql = 'SELECT e.data FROM edges e JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target WHERE ' + where
        return QuerySequence(self.store, sql, sp + tp, decode=True)

    def selection_edges(self, node):
        sw, sp = self.member_condition(node, 's')
        tw, tp = self.member_condition(node, 't')
        sql = 'SELECT e.id AS id FROM edges e JOIN nodes s ON s.id=e.source JOIN nodes t ON t.id=e.target WHERE (' + sw + ') OR (' + tw + ')'
        return SQLSelection(self.store, sql, sp + tp)

    def find(self, query):
        query = query.strip().lower()
        if not query:
            return []
        exact = QuerySequence(self.store, 'SELECT data FROM nodes WHERE lower(id)=? ORDER BY seq', (query,), decode=True)
        return exact if len(exact) else QuerySequence(self.store,
            'SELECT data FROM nodes WHERE instr(lower(id),?)>0 OR instr(lower(module),?)>0 ORDER BY seq', (query, query), decode=True)

    def actual_edges(self, edge):
        if edge.get('l1_boundary'):return [edge]
        if 'members' not in edge:
            return [self.original_edges[edge['id']]]
        members = edge['members']
        return QuerySequence(self.store, 'SELECT data FROM edges WHERE id IN (' + members.sql + ')', members.params,
                             decode=True, count=len(members))

    def project_focus(self, node, required_edge=None):
        # Exact location loads a bounded neighbourhood. All other instances
        # remain searchable in the index and accessible via the full overview.
        conn = self.store.connection()
        rows = conn.execute('SELECT source,target FROM edges WHERE source=? OR target=? LIMIT ?',
                            (node['id'], node['id'], DETAIL_LIMIT-2 if required_edge else DETAIL_LIMIT-1))
        ids = {node['id']}
        if required_edge:
            required_edge=self.original_edges[required_edge['id']]
            ids.update((required_edge['source'],required_edge['target']))
        for source, target in rows:
            ids.update((source, target))
        conn.execute('CREATE TEMP TABLE IF NOT EXISTS ct7_focus(id TEXT PRIMARY KEY)')
        conn.execute('DELETE FROM ct7_focus')
        conn.executemany('INSERT INTO ct7_focus VALUES (?)', ((key,) for key in ids))
        self.nodes = {key: self.original[key] for key in ids}
        self.edges = [self.store.decode(row[0]) for row in conn.execute(
            'SELECT data FROM edges WHERE source IN (SELECT id FROM ct7_focus) AND target IN (SELECT id FROM ct7_focus) LIMIT ?', (DETAIL_LIMIT*2,))]
        if required_edge and not any(edge['id']==required_edge['id'] for edge in self.edges):
            self.edges.append(required_edge)
        self.actual_count, self.focus = len(ids), node['id']
        self.focus_edge=required_edge['id'] if required_edge else None
        self.page, self.pages, self.external_edges = 0, 1, 0
        self.effective_mode = 'instances'
        self._references()
        self._layout()

    def branch_edges(self, instance):
        upstream=self.path_edges(instance,'upstream')
        downstream=self.path_edges(instance,'downstream')
        name='ct7_branch_'+uuid.uuid4().hex
        conn=self.store.connection()
        conn.execute('CREATE TEMP TABLE '+name+'(id TEXT PRIMARY KEY)')
        for selection in (upstream,downstream):
            conn.execute('INSERT OR IGNORE INTO '+name+' '+selection.sql,selection.params)
        result=SQLSelection(self.store,'SELECT id FROM '+name,temporary=name)
        result.l1_upstream=upstream
        return result

    def path_edges(self, instance, direction, seed_pins=None):
        if self.store.connection().execute("SELECT 1 FROM nodes WHERE kind='wire' LIMIT 1").fetchone():
            from .connectivity import indexed_paths
            return indexed_paths(self,instance,direction,seed_pins)
        # BFS frontier, visited vertices and selected edges all live in TEMP
        # SQLite tables (temp_store=FILE). No full-design Python ID sets.
        conn = self.store.connection()
        name = 'ct7_path_' + uuid.uuid4().hex
        for suffix in ('', '_seen', '_front', '_next'):
            conn.execute('CREATE TEMP TABLE ' + name + suffix + '(id TEXT PRIMARY KEY)')
        conn.execute('INSERT INTO ' + name + '_seen VALUES (?)', (instance,))
        conn.execute('INSERT INTO ' + name + '_front VALUES (?)', (instance,))
        match, other = ('target', 'source') if direction == 'upstream' else ('source', 'target')
        try:
            if seed_pins is not None and direction=='upstream':
                conn.execute('DELETE FROM '+name+'_front')
                for pin in seed_pins:
                    conn.execute('INSERT OR IGNORE INTO '+name+' SELECT id FROM edges WHERE target=? AND target_pin=?',(instance,pin))
                    conn.execute('INSERT OR IGNORE INTO '+name+'_front SELECT source FROM edges WHERE target=? AND target_pin=?',(instance,pin))
                conn.execute('INSERT OR IGNORE INTO '+name+'_seen SELECT id FROM '+name+'_front')
            while True:
                join = ' FROM edges e JOIN ' + name + '_front f ON e.' + match + '=f.id'
                conn.execute('INSERT OR IGNORE INTO ' + name + ' SELECT e.id' + join)
                conn.execute('INSERT OR IGNORE INTO ' + name + '_next SELECT e.' + other + join +
                             ' LEFT JOIN ' + name + '_seen v ON e.' + other + '=v.id WHERE v.id IS NULL')
                count = conn.execute('SELECT COUNT(*) FROM ' + name + '_next').fetchone()[0]
                if not count:
                    break
                conn.execute('INSERT OR IGNORE INTO ' + name + '_seen SELECT id FROM ' + name + '_next')
                conn.execute('DELETE FROM ' + name + '_front')
                conn.execute('INSERT INTO ' + name + '_front SELECT id FROM ' + name + '_next')
                conn.execute('DELETE FROM ' + name + '_next')
            return SQLSelection(self.store, 'SELECT id FROM ' + name, temporary=name)
        except Exception:
            conn.execute('DROP TABLE ' + name)
            raise
        finally:
            for suffix in ('_seen', '_front', '_next'):
                conn.execute('DROP TABLE ' + name + suffix)
