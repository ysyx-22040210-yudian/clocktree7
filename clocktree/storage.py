"""Disk backed evidence and immutable result indexes (stdlib only, Python 3.6+).

The JSON result remains a complete, portable interchange file.  SQLite is an
optional, rebuildable accelerator; neither a view nor a session needs to load
every instance or every trace path to open it.
"""
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import uuid


INDEX_VERSION = 2
ARRAY_KEYS = ('nodes', 'edges', 'boundaries', 'components', 'clock_sources', 'reset_sources', 'diagnostics')
INDEX_THRESHOLD = 8 * 1024 * 1024


def stream_json(stream, value):
    """Write records/paths one at a time, including lazy SQLite sequences."""
    if isinstance(value, Mapping):
        stream.write('{')
        for index, (key, child) in enumerate(value.items()):
            if index:
                stream.write(',')
            stream.write(json.dumps(str(key), ensure_ascii=False) + ':')
            stream_json(stream, child)
        stream.write('}')
    elif isinstance(value, (list, tuple, Sequence)) and not isinstance(value, (str, bytes)):
        stream.write('[')
        for index, child in enumerate(value):
            if index:
                stream.write(',')
            stream_json(stream, child)
        stream.write(']')
    else:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False))


def write_graph(path, graph):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            stream_json(stream, graph)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


class EvidencePath(Sequence):
    def __init__(self, store, key):
        self.store, self.key = store, key

    def __len__(self):
        if not self.key:
            return 0
        row = self.store.connection().execute('SELECT depth FROM path_nodes WHERE id=?', (self.key,)).fetchone()
        if row is None:
            raise ValueError('Missing evidence path {}'.format(self.key))
        return row[0]

    def __iter__(self):
        # Only one requested path is expanded; sibling branches share parents.
        chain, key = [], self.key
        conn = self.store.connection()
        while key:
            row = conn.execute('SELECT parent,data FROM path_nodes WHERE id=?', (key,)).fetchone()
            if row is None:
                raise ValueError('Missing evidence path {}'.format(key))
            key, data = row
            chain.append(data)
        for data in reversed(chain):
            yield json.loads(data)

    def __getitem__(self, key):
        return list(self)[key]

    def tail(self, limit=8):
        """Read a bounded preview without expanding the complete parent chain."""
        chain,key=[],self.key
        conn=self.store.connection()
        while key and len(chain)<limit:
            row=conn.execute('SELECT parent,data FROM path_nodes WHERE id=?',(key,)).fetchone()
            if row is None:raise ValueError('Missing evidence path {}'.format(key))
            key,data=row;chain.append(json.loads(data))
        return list(reversed(chain))

    def __eq__(self, other):
        return list(self) == other


class ResultStore:
    def __init__(self, path, create=False):
        self.path = Path(path).resolve()
        self.local = threading.local()
        self.create = create
        self.trace_commits = 0
        conn = self.connection()
        if create:
            conn.executescript('''
                CREATE TABLE path_nodes(id INTEGER PRIMARY KEY, parent INTEGER NOT NULL,
                    digest TEXT NOT NULL, depth INTEGER NOT NULL, data TEXT NOT NULL,
                    UNIQUE(parent,digest));
                CREATE TABLE live_paths(trace TEXT, local INTEGER, path INTEGER, PRIMARY KEY(trace,local));
                CREATE TABLE nodes(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,layer INTEGER,component INTEGER,
                    module TEXT,kind TEXT,root INTEGER,terminal INTEGER,unresolved INTEGER,scope_in INTEGER,scope_out INTEGER,data TEXT);
                CREATE INDEX nodes_component ON nodes(component);
                CREATE INDEX nodes_layer ON nodes(layer);
                CREATE TABLE edges(seq INTEGER PRIMARY KEY,id TEXT UNIQUE,source TEXT,target TEXT,
                    source_pin TEXT,target_pin TEXT,delta INTEGER,data TEXT);
                CREATE INDEX edges_source ON edges(source);
                CREATE INDEX edges_target ON edges(target);
                CREATE TABLE boundaries(seq INTEGER PRIMARY KEY,instance TEXT,status TEXT,direction TEXT,data TEXT);
                CREATE INDEX boundaries_instance ON boundaries(instance);
                CREATE TABLE records(field TEXT,seq INTEGER,data TEXT,PRIMARY KEY(field,seq));
                CREATE TABLE header(key TEXT PRIMARY KEY,value TEXT);
                CREATE TABLE memberships(instance TEXT,component INTEGER,PRIMARY KEY(instance,component));
                CREATE INDEX memberships_component ON memberships(component);
            ''')

    def connection(self):
        conn = getattr(self.local, 'conn', None)
        if conn is None:
            if self.create:
                conn = sqlite3.connect(str(self.path))
                conn.execute('PRAGMA synchronous=OFF')
            else:
                conn = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
            conn.execute('PRAGMA cache_size=-4096')
            conn.execute('PRAGMA temp_store=FILE')
            conn.create_function('ct7_prefix', 2, lambda path, depth: '.'.join(path.split('.')[:depth]))
            conn.create_function('ct7_scope', 2, lambda path, scope: int(not scope or path == scope or
                                 path.startswith(scope + '.') or path.startswith(scope + '[')))
            conn.create_function('ct7_diagnostic_code', 1, lambda data: json.loads(data).get('code', ''))
            self.local.conn = conn
        return conn

    def close(self):
        conn = getattr(self.local, 'conn', None)
        if conn is not None:
            conn.close()
            self.local.conn = None

    def __del__(self):
        try:
            self.close()
            temporary = getattr(self, 'temporary', None)
            if temporary:
                temporary.cleanup()
        except Exception:
            pass

    def add_step(self, parent, evidence):
        data = json.dumps(evidence, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(data.encode('utf-8')).hexdigest()
        conn = self.connection()
        row = conn.execute('SELECT id FROM path_nodes WHERE parent=? AND digest=?', (parent, digest)).fetchone()
        if row:
            return row[0]
        depth = conn.execute('SELECT depth FROM path_nodes WHERE id=?', (parent,)).fetchone()[0] if parent else 0
        return conn.execute('INSERT INTO path_nodes(parent,digest,depth,data) VALUES (?,?,?,?)',
                            (parent, digest, depth + 1, data)).lastrowid

    def add_path(self, evidence):
        if isinstance(evidence, EvidencePath) and evidence.store.path == self.path:
            return evidence
        key = 0
        for step in evidence:
            key = self.add_step(key, step)
        return EvidencePath(self, key)

    def trace_step(self, trace, local, parent, evidence):
        conn = self.connection()
        parent_key = self.trace_path(trace, parent).key if parent else 0
        key = self.add_step(parent_key, evidence)
        conn.execute('INSERT INTO live_paths(trace,local,path) VALUES (?,?,?)', (str(trace), local, key))

    def trace_path(self, trace, local):
        if not local:
            return EvidencePath(self, 0)
        row = self.connection().execute('SELECT path FROM live_paths WHERE trace=? AND local=?', (str(trace), local)).fetchone()
        if row is None:
            raise ValueError('Trace {} references missing path {}'.format(trace, local))
        return EvidencePath(self, row[0])

    def end_trace(self, trace):
        self.connection().execute('DELETE FROM live_paths WHERE trace=?', (str(trace),))
        self.trace_commits += 1
        if self.trace_commits % 256 == 0:
            self.connection().commit()

    def encode(self, value):
        def default(child):
            if isinstance(child, EvidencePath):
                return {'$ct7_path': self.add_path(child).key}
            if isinstance(child, Sequence) and not isinstance(child, (str, bytes)):
                return list(child)
            raise TypeError(type(child).__name__)
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=default)

    def decode(self, data):
        return json.loads(data, object_hook=lambda item: EvidencePath(self, item['$ct7_path'])
                          if set(item) == {'$ct7_path'} else item)

    def put(self, field, seq, record):
        record = dict(record) if isinstance(record, dict) else record
        if field == 'edges':
            record['evidence'] = {direction: self.add_path(path) for direction, path in record.get('evidence', {}).items()}
        elif field == 'boundaries' and 'path' in record:
            record['path'] = self.add_path(record['path'])
        data = self.encode(record)
        conn = self.connection()
        if field == 'nodes':
            conn.executemany('INSERT INTO memberships VALUES (?,?)',
                             ((record['id'], component) for component in record.get('components', [record['component']])))
            conn.execute('INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (seq, record['id'], record['layer'], record['component'], record['module'], record.get('kind'),
                          bool(record.get('root')), bool(record.get('terminal')), bool(record.get('has_unresolved_output')),
                          bool(record.get('scope_input_boundary')), bool(record.get('scope_output_boundary')), data))
        elif field == 'edges':
            conn.execute('INSERT INTO edges VALUES (?,?,?,?,?,?,?,?)',
                         (seq, record['id'], record['source'], record['target'], record['source_pin'], record['target_pin'], record['layer_delta'], data))
        elif field == 'boundaries':
            conn.execute('INSERT INTO boundaries VALUES (?,?,?,?,?)',
                         (seq, record.get('instance'), record.get('status'), record.get('direction'), data))
        elif field != 'components':
            conn.execute('INSERT INTO records VALUES (?,?,?)', (field, seq, data))

    def finish(self, header):
        conn = self.connection()
        header = dict(header, ct7_index_version=INDEX_VERSION)
        config = header.get('config', {})
        if any(rule.get('kind') for layer in config.get('layers', []) for rule in layer.get('modules', [])):
            from .devices import resolve_kind
            for seq, data in conn.execute('SELECT seq,data FROM nodes WHERE kind IS NULL'):
                node = self.decode(data)
                kind = resolve_kind(node, config)
                if kind:
                    node.update(kind=kind, kind_source='configured')
                    conn.execute('UPDATE nodes SET kind=?,data=? WHERE seq=?', (kind, self.encode(node), seq))
        conn.executemany('INSERT OR REPLACE INTO header VALUES (?,?)',
                         ((key, self.encode(value)) for key, value in header.items()))
        conn.execute('DELETE FROM live_paths')
        conn.commit()
        conn.execute('ANALYZE')
        conn.commit()

    def graph(self):
        header = {key: self.decode(value) for key, value in self.connection().execute('SELECT key,value FROM header')}
        if header.pop('ct7_index_version', None) != INDEX_VERSION:
            raise ValueError('Unsupported result index')
        graph = IndexedGraph(self, header)
        for field in ('nodes', 'edges', 'boundaries'):
            graph[field] = PresentedNodes(self, graph) if field == 'nodes' else RecordSequence(self, field)
        graph['components'] = ComponentSequence(self)
        for field, in self.connection().execute('SELECT DISTINCT field FROM records'):
            graph[field] = RecordSequence(self, field)
        for field in header.pop('array_fields', []):
            graph.setdefault(field, RecordSequence(self, field))
        graph.pop('array_fields', None)
        return graph


class RecordSequence(Sequence):
    def __init__(self, store, field, where='', params=(), ids=False):
        self.store, self.field, self.where, self.params, self.ids = store, field, where, tuple(params), ids
        self.count = None
        self.table = field if field in ('nodes', 'edges', 'boundaries') else 'records'
        self.clause = (' WHERE ' + where) if where else ''
        if self.table == 'records':
            self.clause = ' WHERE field=?' + (' AND ' + where if where else '')
            self.params = (field,) + self.params

    def __len__(self):
        if self.count is None:
            self.count = self.store.connection().execute('SELECT COUNT(*) FROM ' + self.table + self.clause, self.params).fetchone()[0]
        return self.count

    def _rows(self, start=0, count=-1):
        column = 'id' if self.ids else 'data'
        sql = 'SELECT ' + column + ' FROM ' + self.table + self.clause + (' ORDER BY id' if self.ids else ' ORDER BY seq') + ' LIMIT ? OFFSET ?'
        for data, in self.store.connection().execute(sql, self.params + (count, start)):
            yield data if self.ids else self.store.decode(data)

    def __iter__(self):
        return self._rows()

    def __getitem__(self, key):
        size = len(self)
        if isinstance(key, slice):
            start, stop, step = key.indices(size)
            if step < 0:
                return list(self)[key]
            return list(self._rows(start, max(0, stop-start)))[::step]
        key = key if key >= 0 else size + key
        if not 0 <= key < size:
            raise IndexError(key)
        return next(self._rows(key, 1))

    def __contains__(self, key):
        if self.ids:
            return self.store.connection().execute('SELECT 1 FROM ' + self.table + self.clause +
                (' AND ' if self.clause else ' WHERE ') + 'id=? LIMIT 1', self.params + (key,)).fetchone() is not None
        return super().__contains__(key)

    def __add__(self, other):
        return CombinedSequence(self, other)


class CombinedSequence(Sequence):
    def __init__(self, *parts):
        self.parts = parts

    def __len__(self):
        return sum(len(part) for part in self.parts)

    def __iter__(self):
        for part in self.parts:
            for item in part:
                yield item

    def __getitem__(self, key):
        if isinstance(key, slice):
            from itertools import islice
            start, stop, step = key.indices(len(self))
            return list(islice(iter(self), start, stop, step))
        if key < 0:
            key += len(self)
        for part in self.parts:
            if key < len(part):
                return part[key]
            key -= len(part)
        raise IndexError(key)


class ComponentSequence(Sequence):
    def __init__(self, store):
        self.store = store
        self.count = None

    def __len__(self):
        if self.count is None:
            self.count = self.store.connection().execute('SELECT COUNT(DISTINCT component) FROM memberships').fetchone()[0]
        return self.count

    def __iter__(self):
        for component, in self.store.connection().execute('SELECT DISTINCT component FROM memberships ORDER BY component'):
            yield {'id': component, 'nodes': RecordSequence(self.store, 'nodes', 'id IN (SELECT instance FROM memberships WHERE component=?)', (component,), ids=True)}

    def __getitem__(self, key):
        from itertools import islice
        if isinstance(key, slice):
            start, stop, step = key.indices(len(self))
            return list(islice(iter(self), start, stop, step))
        if key < 0:
            key += len(self)
        try:
            return next(islice(iter(self), key, key+1))
        except StopIteration:
            raise IndexError(key)


class IndexedGraph(dict):
    def __init__(self, store, header):
        super().__init__(header)
        self.store = store

    def boundaries_for(self, instance):
        return RecordSequence(self.store, 'boundaries', 'instance=?', (instance,))

    def kind_counts(self, where='1', params=()):
        from collections import Counter
        conn = self.store.connection()
        counts = Counter(dict(conn.execute('SELECT kind,COUNT(*) FROM nodes WHERE ' + where + ' GROUP BY kind', params)))
        pairs = sorted({(edit['layer'], edit['module']) for edit in self.get('presentation', {}).get('kind_overrides', [])})
        if pairs:
            clause = ' OR '.join('(layer=? AND module=?)' for _ in pairs)
            arguments = tuple(value for pair in pairs for value in pair)
            for data, in conn.execute('SELECT data FROM nodes WHERE (' + where + ') AND (' + clause + ')', tuple(params) + arguments):
                raw = self.store.decode(data)
                current = self.present_node(raw)
                if current.get('kind') != raw.get('kind'):
                    counts[raw.get('kind')] -= 1
                    counts[current.get('kind')] += 1
        return {key: value for key, value in counts.items() if value}

    def present_node(self, record):
        from .devices import matching_rule, resolve_kind
        kind = resolve_kind(record, self['config'])
        if kind and kind != record.get('kind'):
            record = dict(record, kind=kind, kind_source='configured')
        for edit in reversed(self.get('presentation', {}).get('kind_overrides', [])):
            if record['layer'] == edit['layer'] and record['module'] == edit['module']:
                layer = self['config']['layers'][record['layer']-1]
                if any(rule.get('kind') == edit['kind'] and matching_rule(record, layer, rule) for rule in layer['modules']):
                    record = dict(record, kind=edit['kind'], kind_source='configured')
                    break
        return record


class PresentedNodes(RecordSequence):
    def __init__(self, store, graph):
        super().__init__(store, 'nodes')
        self.graph = graph

    def __iter__(self):
        for node in super().__iter__():
            yield self.graph.present_node(node)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        return [self.graph.present_node(node) for node in value] if isinstance(key, slice) else self.graph.present_node(value)


def new_store(directory):
    directory = Path(directory) / '.clocktree-indexes'
    directory.mkdir(parents=True, exist_ok=True)
    return ResultStore(directory / (uuid.uuid4().hex + '.sqlite3'), create=True)


def index_graph(graph, store):
    header = {key: value for key, value in graph.items() if key not in ARRAY_KEYS}
    header['array_fields'] = [key for key in ARRAY_KEYS if key in graph]
    for field in header['array_fields']:
        if field == 'components':
            continue  # component membership is indexed once in nodes
        for seq, record in enumerate(graph[field]):
            store.put(field, seq, record)
        store.connection().commit()
    store.finish(header)
    return store.graph()


def fingerprint(path):
    stat = Path(path).stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def index_pointer(path):
    return Path(str(path) + '.index.json')


def publish_index(path, graph, destination=None):
    destination = Path(destination) if destination else index_pointer(path)
    graph.store.path.relative_to(destination.parent.resolve())
    pointer = {'ct7_index': INDEX_VERSION, 'database': os.path.relpath(str(graph.store.path), str(destination.parent)),
               'source': fingerprint(path)}
    write_graph(destination, pointer)


def snapshot_index(path, graph):
    path = Path(path)
    database = path.with_suffix('.sqlite3')
    temporary = database.with_suffix('.tmp')
    # Committed indexes are immutable; a plain copy works on NFS/HGFS too.
    graph.store.connection().commit()
    try:
        shutil.copyfile(str(graph.store.path), str(temporary))
        os.replace(str(temporary), str(database))
        write_graph(path, {'ct7_index': INDEX_VERSION, 'database': database.name,
                          'overrides': {key: graph[key] for key in ('config', 'presentation') if key in graph}})
    finally:
        if temporary.exists():
            temporary.unlink()


def _open_pointer(path, pointer):
    if pointer.get('ct7_index') not in (1, INDEX_VERSION) or not isinstance(pointer.get('database'), str):
        raise ValueError('Invalid result index')
    database = (Path(path).parent / pointer['database']).resolve()
    # Sidecars/snapshots are local to their result; never interpret arbitrary URIs.
    try:
        database.relative_to(Path(path).parent.resolve())
    except ValueError:
        raise ValueError('Result index is outside its result directory')
    store = ResultStore(database)
    if pointer.get('ct7_index') == 1:
        # Old session snapshots can contain only an index pointer, without a
        # complete JSON fallback. Migrate a private copy; leave history intact.
        from .paths import user_state_root
        identity=str(database)+json.dumps(fingerprint(database),sort_keys=True)
        key=hashlib.sha256(identity.encode('utf-8')).hexdigest()
        cache=user_state_root()/'index-upgrades';cache.mkdir(parents=True,exist_ok=True)
        migrated=cache/(key+'.sqlite3')
        if not migrated.is_file():
            temporary=cache/(key+'.'+uuid.uuid4().hex+'.tmp')
            shutil.copyfile(str(database),str(temporary))
            conn=sqlite3.connect(str(temporary))
            try:
                conn.execute('CREATE TABLE IF NOT EXISTS memberships(instance TEXT,component INTEGER,PRIMARY KEY(instance,component))')
                conn.execute('CREATE INDEX IF NOT EXISTS memberships_component ON memberships(component)')
                conn.execute('INSERT OR IGNORE INTO memberships SELECT id,component FROM nodes')
                conn.execute('INSERT OR REPLACE INTO header VALUES (?,?)',('ct7_index_version',str(INDEX_VERSION)))
                conn.commit()
            finally:conn.close()
            os.replace(str(temporary),str(migrated))
        store=ResultStore(migrated)
    graph = store.graph()
    graph.update(pointer.get('overrides', {}))
    return graph


class JSONReader:
    """Incremental reader for legacy JSON. Buffer is one record, not one file."""
    def __init__(self, stream):
        self.stream, self.buffer, self.pos, self.eof = stream, '', 0, False
        self.decoder = json.JSONDecoder()

    def more(self):
        self.buffer = self.buffer[self.pos:]
        self.pos = 0
        chunk = self.stream.read(65536)
        self.eof = not chunk
        self.buffer += chunk
        return bool(chunk)

    def peek(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if not self.more():
                return ''

    def take(self, char):
        if self.peek() != char:
            raise ValueError('Expected JSON ' + char)
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                # A number may continue in the next chunk.
                if end == len(self.buffer) and not self.eof and isinstance(value, (float, int)):
                    if self.more():
                        continue
                self.pos = end
                return value
            except ValueError:
                if self.eof or not self.more():
                    raise ValueError('Truncated or invalid result JSON')

    def skip(self):
        # Skip the redundant legacy components array without holding its often
        # enormous membership lists. Strings/escapes can cross chunk boundaries.
        char = self.peek()
        if char not in '[{':
            self.value()
            return
        depth, quoted, escaped = 0, False, False
        while True:
            if self.pos == len(self.buffer):
                if not self.more():
                    raise ValueError('Truncated result JSON')
            char = self.buffer[self.pos]
            self.pos += 1
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in '[{':
                depth += 1
            elif char in ']}':
                depth -= 1
                if not depth:
                    return


def index_legacy(path, store):
    header, fields = {}, []
    with Path(path).open(encoding='utf-8-sig') as stream:
        reader = JSONReader(stream)
        reader.take('{')
        while reader.peek() != '}':
            key = reader.value()
            reader.take(':')
            if key == 'components':
                reader.skip()
                fields.append(key)
            elif key in ARRAY_KEYS:
                fields.append(key)
                reader.take('[')
                seq = 0
                while reader.peek() != ']':
                    store.put(key, seq, reader.value())
                    seq += 1
                    if seq % 1000 == 0:
                        store.connection().commit()
                    if reader.peek() != ']':
                        reader.take(',')
                reader.take(']')
            else:
                header[key] = reader.value()
            if reader.peek() != '}':
                reader.take(',')
        reader.take('}')
        if reader.peek():
            raise ValueError('Unexpected trailing JSON content')
    if not all(field in fields for field in ('nodes', 'edges', 'components')):
        raise ValueError('Not a clock/reset result')
    header['array_fields'] = fields
    store.finish(header)
    return store.graph()


def open_graph(path, threshold=INDEX_THRESHOLD):
    path = Path(path).resolve()
    from .paths import user_state_root, path_key
    cache = user_state_root() / 'indexes' / path_key(path)
    for candidate in (index_pointer(path), cache / 'result.index.json'):
        try:
            with candidate.open(encoding='utf-8') as stream:
                pointer = json.load(stream)
            if isinstance(pointer, dict) and pointer.get('source') == fingerprint(path):
                return _open_pointer(candidate, pointer)
        except (OSError, ValueError, sqlite3.Error, KeyError):
            pass
    if path.stat().st_size < threshold:
        with path.open(encoding='utf-8-sig') as stream:
            graph = json.load(stream)
        return _open_pointer(path, graph) if isinstance(graph, dict) and 'ct7_index' in graph else graph
    # A read-only source can still be indexed in the user's cache.
    store = None
    try:
        try:
            store = new_store(path.parent)
        except (OSError, sqlite3.Error):
            store = new_store(cache)
        graph = index_legacy(path, store)
        store.close()
        graph = ResultStore(store.path).graph()
        try:
            publish_index(path, graph)
        except (OSError, ValueError):
            try:
                publish_index(path, graph, cache / 'result.index.json')
            except (OSError, ValueError):
                pass
        return graph
    except Exception:
        if store:
            store.close()
            try:
                store.path.unlink()
            except OSError:
                pass
        raise
