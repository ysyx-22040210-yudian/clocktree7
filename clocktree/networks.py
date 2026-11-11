"""Network membership at subsystem input terminals, never through its body."""
from collections import defaultdict
from .profiles import output_ports
from .connectivity import internal_pairs


def memberships(nodes, edges, sources):
    parents = {}
    def endpoint(instance, pin=None):
        return (instance, pin if nodes[instance].get('kind') in ('subsystem', 'wire') else None)
    def find(key):
        parents.setdefault(key, key)
        root = key
        while parents[root] != root:
            root = parents[root]
        while parents[key] != key:
            previous = parents[key]
            parents[key] = root
            key = previous
        return root
    def join(a, b):
        a, b = find(a), find(b)
        if a != b: parents[b] = a
    for key, node in nodes.items():
        for pin in node['inputs'] + output_ports(node) if node.get('kind') in ('subsystem', 'wire') else [None]:
            find(endpoint(key, pin))
        for a,b in internal_pairs(node):join(endpoint(key,a),endpoint(key,b))
    for edge in edges:
        join(endpoint(edge['source'], edge['source_pin']), endpoint(edge['target'], edge['target_pin']))
    for source in sources:
        loads = [endpoint(load['instance'], load['pin']) for load in source['loads'] if load['instance'] in nodes]
        for load in loads[1:]: join(loads[0], load)
    groups = defaultdict(list)
    for key in sorted(parents, key=lambda x:(x[0], x[1] or '')):
        groups[find(key)].append(key)
    components = []
    for index, terminals in enumerate(sorted(groups.values(), key=lambda x:(x[0][0], x[0][1] or ''))):
        members = sorted(set(key for key, pin in terminals))
        components.append({'id': index, 'nodes': members,
                           'input_terminals': [{'instance': key, 'pin': pin} for key, pin in terminals if pin is not None and pin in nodes[key]['inputs']]})
        for key, pin in terminals:
            node = nodes[key]
            node.setdefault('components', []).append(index)
            if pin is not None:
                node.setdefault('input_components' if pin in node['inputs'] else 'output_components', {})[pin] = index
    for node in nodes.values():
        node['components'] = sorted(set(node['components']))
        node['component'] = node['components'][0]
    return components


def subsystem_references(nodes, edges, enabled=True, component=None):
    """Only presentation IDs change. Evidence IDs and physical IDs stay intact."""
    if not enabled: return nodes, edges
    result, projected, groups = {}, [], {}
    for key, node in nodes.items():
        if node.get('kind') != 'subsystem': result[key] = node
    def reference(key, branch, pin):
        original = nodes[key]
        group = (key, branch)
        if group not in groups:
            ref = dict(original, id='ref:{}:{}'.format(key, len(groups)), reference_of=key,
                       reference_source=None if branch.startswith('unconnected:') else branch,
                       path=key, inputs=[], output=None, root=False, terminal=True)
            if component is not None: ref['component'] = component
            elif pin in original.get('input_components', {}): ref['component'] = original['input_components'][pin]
            groups[group] = ref
            result[ref['id']] = ref
        ref = groups[group]
        if pin not in ref['inputs']: ref['inputs'].append(pin)
        return ref['id']
    attached = defaultdict(set)
    for edge in edges:
        if nodes[edge['target']].get('kind') == 'subsystem':
            key, pin = edge['target'], edge['target_pin']
            attached[key].add(pin)
            target = reference(key, edge['source'], pin)
            projected.append(dict(edge, target=target, actual_target=key))
        else: projected.append(edge)
    for key, node in nodes.items():
        if node.get('kind') == 'subsystem':
            for pin in node['inputs']:
                if pin not in attached[key] and (component is None or node.get('input_components', {}).get(pin) == component):
                    reference(key, 'unconnected:' + str(node.get('input_components', {}).get(pin)), pin)
    return result, projected
