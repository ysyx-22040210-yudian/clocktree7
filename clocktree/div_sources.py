"""Classify a proven DIV Q endpoint by its enclosing RTL scope."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .config import hierarchy_contains


def classify_div_source(endpoint, scopes):
    if not scopes:
        return {'status': 'unconfigured'}
    owner = endpoint.rpartition('.')[0]
    matches = [(index, scope) for index, scope in enumerate(scopes, 1)
               if hierarchy_contains(owner, scope)]
    if not matches:
        return {'status': 'outside', 'hierarchy': owner}
    index, scope = max(matches, key=lambda match: len(match[1]))
    return {'status': 'matched', 'level': index, 'scope': scope}


def div_source_label(source):
    status = source.get('status')
    if status == 'matched':
        return '来源层次 {}'.format(source['level'])
    return {'outside': '来源未归属', 'unconfigured': '来源层次未配置'}.get(status, '')


def read_div_source_levels(directory, scopes):
    """Read the completed DIV jobs without loading their trace paths or other port results."""
    if not directory or not scopes:
        return {}
    uri = (Path(directory) / 'registers.sqlite').resolve().as_uri() + '?mode=ro'
    levels = {}
    with closing(sqlite3.connect(uri, uri=True)) as db:
        for job, seed_json, summary_json in db.execute("SELECT id,seed,summary FROM jobs WHERE state='complete'"):
            seed = json.loads(seed_json)
            if seed.get('role') != 'div' or seed.get('direction', 'driver') != 'driver':
                continue
            instance, pin = seed['instance'], seed['pin']
            prefix = instance + '.'
            bits = {name[len(prefix):] for name in json.loads(summary_json).get('bits', []) if name.startswith(prefix)}
            if not bits:
                continue
            covered = {}
            level = None
            for bit, endpoint, side, relation, status in db.execute(
                    'SELECT bit,endpoint,side,relation,status FROM results WHERE job=?', (job,)):
                if bit not in bits or status != 'register' or side != 'Q' or relation not in ('direct', 'inverted'):
                    break
                source_level = classify_div_source(endpoint, scopes).get('level')
                signal = (endpoint, relation)
                if source_level is None or (level is not None and level != source_level):
                    break
                if bit in covered and covered[bit] != signal:
                    break
                covered[bit] = signal
                level = source_level
            else:
                if covered.keys() == bits:
                    levels[instance] = (pin, seed.get('module'), level)
    return levels


def div_source_level(node, levels):
    if node.get('kind') == 'div':
        match = levels.get(node['id'])
        if match:
            pin, module, level = match
            if (module is None or module == node['module']) and pin == node.get('aux_ports', {}).get('div'):
                return level
    return None
