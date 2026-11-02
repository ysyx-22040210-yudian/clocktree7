"""Explicit analysis profiles; clock configurations remain backward compatible."""
TREE_PROFILES = {
    'clock': {'label': '时钟树', 'signal': '时钟', 'network': '时钟网络',
              'kinds': ('mux', 'gate', 'occ', 'div'),
              'inputs': 'clock_inputs', 'output': 'clock_output',
              'sources': 'clock_sources', 'result': 'clocktree.json'},
    'reset': {'label': '复位树', 'signal': '复位', 'network': '复位网络',
              'kinds': ('mux', 'rsy_sync'),
              'inputs': 'reset_inputs', 'output': 'reset_output',
              'sources': 'reset_sources', 'result': 'resettree.json'},
}
LABEL_TO_TREE = {info['label']: key for key, info in TREE_PROFILES.items()}


def tree_type(config):
    return config.get('tree_type', 'clock')


def profile(config):
    return TREE_PROFILES[tree_type(config)]


def rule_ports(rule, kind=None):
    # The field names explicitly identify the interface; never guess from a
    # module/instance name. Validated configurations cannot mix these fields.
    info = TREE_PROFILES[kind or ('reset' if 'reset_inputs' in rule else 'clock')]
    return rule.get(info['inputs'], []), rule.get(info['output'])


def empty_config(kind='clock'):
    return {'tree_type': kind, 'design': {},
            'layers': [{'index': i, 'hierarchy': '', 'modules': []} for i in (1, 2)]}


def result_filename(config):
    return profile(config)['result']
