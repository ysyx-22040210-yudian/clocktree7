"""Stable instance/port identities for navigation across projected references."""


def location_target(node, edge=None, endpoint=None, pin=None):
    if node.get('aggregate'):
        return {'aggregate': node['id']}
    target = {'instance': node.get('reference_of', node['id'])}
    if node.get('reference_of'):
        target.update(reference_source=node.get('reference_source'), inputs=tuple(node['inputs']))
    if pin:
        target['pin'] = pin
    if edge is not None:
        if endpoint not in ('source', 'target') or edge[endpoint] != target['instance']:
            raise ValueError('Navigation edge does not identify the requested endpoint')
        target.update(edge=edge['id'], endpoint=endpoint, pin=edge[endpoint+'_pin'])
        if endpoint == 'target':
            target['reference_source'] = edge['source']
    return target


def display_node(nodes, target):
    """Never fall back to a different input/source when context is known."""
    if not target:
        return None
    if target.get('aggregate'):
        node = nodes.get(target['aggregate'])
        return node if node and node.get('aggregate') else None
    for node in nodes.values():
        if node.get('aggregate') or node.get('reference_of', node['id']) != target['instance']:
            continue
        pin = target.get('pin')
        if node.get('reference_of'):
            if pin and pin not in node['inputs']:
                continue
            if 'reference_source' in target and node.get('reference_source') != target['reference_source']:
                continue
            if target.get('inputs') and not set(target['inputs']).intersection(node['inputs']):
                continue
        return node
    return None
