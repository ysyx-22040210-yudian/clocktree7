"""Classify a proven DIV Q endpoint by its enclosing RTL scope."""
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
