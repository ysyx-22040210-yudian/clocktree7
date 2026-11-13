"""Atomic per-user GUI sessions. Configuration writes never rewrite an unchanged graph."""
from contextlib import contextmanager
import copy
import datetime
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
import sqlite3

from .config import validate_config
from .profiles import TREE_PROFILES, tree_type
from .paths import user_state_root, path_key
from .storage import IndexedGraph, PresentedNodes, RecordSequence, open_graph, snapshot_index


def default_session_dir(runs):
    return user_state_root() / path_key(runs)


def same_config(left, right):
    def normalized(config):
        result = dict(config, tree_type=tree_type(config))
        result.pop('timeout_seconds', None)
        result.pop('memory_limit_gib', None)
        return result
    return normalized(left) == normalized(right)


def snapshot_graph(graph):
    # The viewer never mutates connectivity/evidence. Copy the fields changed
    # by device-type edits, keeping large immutable connectivity lists shared.
    result = IndexedGraph(graph.store, graph) if isinstance(graph, IndexedGraph) else dict(graph)
    if isinstance(graph, IndexedGraph):
        result['nodes'] = PresentedNodes(graph.store, result)
    else:
        result['nodes'] = [dict(node) for node in graph['nodes']]
    result['config'] = copy.deepcopy(graph['config'])
    if 'presentation' in graph:
        result['presentation'] = copy.deepcopy(graph['presentation'])
    return result


def read_graph(path, expected=None):
    try:
        graph = open_graph(path)
    except sqlite3.Error as error:
        raise ValueError('Result index is unreadable: ' + str(error))
    from collections.abc import Sequence
    if (not isinstance(graph, dict) or not all(isinstance(graph.get(k), Sequence) and not isinstance(graph[k], (str, bytes)) for k in ('nodes', 'edges', 'components'))
            or not isinstance(graph.get('summary'), dict) or not isinstance(graph.get('config'), dict)):
        raise ValueError('Not a clock/reset result')
    mode = tree_type(graph['config'])
    if mode not in TREE_PROFILES or graph.get('tree_type', mode) != mode or (expected and mode != expected):
        raise ValueError('Result analysis type does not match')
    validate_config(graph['config'])
    if mode == 'clock' and graph['summary'].get('topology_valid') is False:
        # Old results can contain a former L1-only topology diagnostic.
        # Filter the presentation, keeping the saved result and evidence intact.
        original = graph.get('diagnostics', [])
        if isinstance(graph, IndexedGraph):
            diagnostics = RecordSequence(graph.store, 'diagnostics', 'ct7_diagnostic_code(data) != ?', ('root_outside_L1',))
        else:
            diagnostics = [record for record in original if record.get('code') != 'root_outside_L1']
        if len(diagnostics) != len(original):
            from .graph import TOPOLOGY_ERRORS
            graph['diagnostics'] = diagnostics
            graph['summary'] = dict(graph['summary'], diagnostics=len(diagnostics),
                                    topology_valid=not any(row.get('code') in TOPOLOGY_ERRORS for row in diagnostics))
    return graph


class SessionStore:
    def __init__(self, runs, directory=None):
        self.runs = Path(runs).resolve()
        self.directory = Path(directory).resolve() if directory is not None else default_session_dir(self.runs)
        self.manifest = self.directory / 'session.json'
        self.backup = self.directory / 'session.previous.json'

    @contextmanager
    def _lock(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.directory / 'session.lock').open('a+b') as stream:
            stream.seek(0, 2)
            if not stream.tell():
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError as error:
                        if error.errno not in (13, 36):
                            raise
                        time.sleep(.05)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _atomic_json(self, path, value):
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, separators=(',', ':'))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(path))
        finally:
            if temporary.exists():
                temporary.unlink()

    def _read_manifest(self, path):
        with path.open(encoding='utf-8') as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get('schema_version') != 1 or data.get('active') not in TREE_PROFILES or not isinstance(data.get('workspaces'), dict):
            raise ValueError('Unsupported GUI session')
        for mode, workspace in data['workspaces'].items():
            if (mode not in TREE_PROFILES or not isinstance(workspace, dict)
                    or not isinstance(workspace.get('config'), dict) or tree_type(workspace['config']) != mode):
                raise ValueError('Session analysis type does not match')
            validate_config(workspace['config'], allow_incomplete=True)
            filename = workspace.get('result')
            if filename and not re.fullmatch(mode + r'-[0-9a-f]{32}\.json', filename):
                raise ValueError('Invalid cached result filename')
        return data

    @staticmethod
    def _references(data):
        return {w['result'] for w in data.get('workspaces', {}).values() if w.get('result')}

    def save(self, state):
        with self._lock():
            if state.get('active') not in TREE_PROFILES:
                raise ValueError('Invalid active analysis mode')
            old = {}
            for path in (self.manifest, self.backup):
                try:
                    old = self._read_manifest(path)
                    break
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            entries = dict(old.get('workspaces', {}))
            for mode, workspace in state['workspaces'].items():
                if mode not in TREE_PROFILES or tree_type(workspace['config']) != mode:
                    raise ValueError('Session analysis type does not match')
                validate_config(workspace['config'], allow_incomplete=True)
                graph = workspace.get('graph')
                filename = workspace.get('deferred_result') if graph is None else None
                if filename and (not re.fullmatch(mode + r'-[0-9a-f]{32}\.json', filename) or not (self.directory / filename).is_file()):
                    filename = None
                if graph is not None:
                    token = workspace['graph_token']
                    if not re.fullmatch(r'[0-9a-f]{32}', token):
                        raise ValueError('Invalid result revision')
                    filename = '{}-{}.json'.format(mode, token)
                    if not (self.directory / filename).is_file():
                        if isinstance(graph, IndexedGraph):
                            snapshot_index(self.directory / filename, graph)
                        else:
                            self._atomic_json(self.directory / filename, graph)
                entries[mode] = {'config': workspace['config'], 'result': filename,
                                 'source_result': str(workspace['path']) if workspace.get('path') else None,
                                 'dirty': bool(workspace.get('dirty')), 'active_layer': workspace.get('active_layer', 1)}
            data = {'schema_version': 1, 'active': state['active'], 'workspaces': entries,
                    'saved_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
            if old:
                self._atomic_json(self.backup, old)
            self._atomic_json(self.manifest, data)
            keep = self._references(data) | self._references(old)
            for path in self.directory.glob('*.json'):
                if re.fullmatch(r'(clock|reset)-[0-9a-f]{32}\.json', path.name) and path.name not in keep:
                    try:
                        path.unlink()
                        database = path.with_suffix('.sqlite3')
                        if database.is_file():
                            database.unlink()
                    except OSError:
                        pass

    def history(self, load_results=True):
        files = list(self.runs.glob('*/clocktree.json')) + list(self.runs.glob('*/resettree.json'))
        candidates = []
        for path in files:
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                pass
        workspaces, active = {}, 'clock'
        for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
            try:
                # Do not parse every large historical graph after finding a mode.
                mode = 'reset' if path.name == 'resettree.json' else 'clock'
                if mode in workspaces:
                    continue
                if not load_results:
                    from .config import load_config
                    config=load_config(path.parent/'config.json')
                    if tree_type(config)!=mode:continue
                    if not workspaces:active=mode
                    workspaces[mode]={'config':config,'graph':None,'path':path,
                                     'dirty':False,'active_layer':1,'graph_token':None}
                    continue
                graph = read_graph(path, mode)
                if not workspaces:
                    active = mode
                workspaces[mode] = {'config': copy.deepcopy(graph['config']), 'graph': graph, 'path': path,
                                    'dirty': False, 'active_layer': 1, 'graph_token': uuid.uuid4().hex}
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return {'active': active, 'workspaces': workspaces, 'warnings': [],
                'origin': 'history' if workspaces else 'empty'}

    def load(self, load_results=True):
        if not self.manifest.exists() and not self.backup.exists():
            return self.history(load_results=load_results)
        warnings = []
        with self._lock():
            data = None
            for path in (self.manifest, self.backup):
                try:
                    data = self._read_manifest(path)
                    if path == self.backup:
                        warnings.append('自动记录损坏，已恢复上一份自动记录。')
                    break
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            if data is None:
                state = self.history(load_results=load_results)
                state['warnings'].append('自动记录不可读，已尝试恢复历史运行。')
                return state
            workspaces = {}
            for mode, entry in data['workspaces'].items():
                workspace = {'config': entry['config'], 'path': entry.get('source_result'),
                             'dirty': bool(entry.get('dirty')), 'active_layer': entry.get('active_layer', 1),
                             'graph': None, 'graph_token': None}
                if not load_results and entry.get('result'):
                    workspace['deferred_result'] = entry['result']
                if entry.get('result') and load_results:
                    try:
                        workspace['graph'] = read_graph(self.directory / entry['result'], mode)
                        workspace['graph_token'] = Path(entry['result']).stem.split('-', 1)[1]
                    except (OSError, ValueError, KeyError, TypeError):
                        if entry.get('source_result'):
                            try:
                                workspace['graph'] = read_graph(entry['source_result'], mode)
                                workspace['graph_token'] = uuid.uuid4().hex
                            except (OSError, ValueError, KeyError, TypeError):
                                pass
                        workspace['dirty'] = True
                        warnings.append('{}结果缓存不可读，{}。'.format(TREE_PROFILES[mode]['label'],
                                        '已加载原结果文件' if workspace['graph'] is not None else '配置已保留，请重新打开结果或提取'))
                workspaces[mode] = workspace
            return {'active': data['active'], 'workspaces': workspaces, 'warnings': warnings, 'origin': 'session'}


class SessionWriter:
    """Coalesce edits and serialize large snapshots off the Tk thread."""
    def __init__(self, store, on_error):
        self.store, self.on_error = store, on_error
        self.condition = threading.Condition()
        self.pending, self.saving, self.stopping, self.error = None, False, False, None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def submit(self, state):
        with self.condition:
            self.pending = state
            self.condition.notify_all()

    @property
    def idle(self):
        with self.condition:
            return self.pending is None and not self.saving

    def _run(self):
        while True:
            with self.condition:
                while self.pending is None and not self.stopping:
                    self.condition.wait()
                if self.pending is None and self.stopping:
                    return
                state, self.pending, self.saving = self.pending, None, True
            try:
                self.store.save(state)
                self.error = None
            except Exception as error:
                self.error = str(error)
                self.on_error(str(error))
            finally:
                with self.condition:
                    self.saving = False
                    self.condition.notify_all()

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        self.thread.join()
