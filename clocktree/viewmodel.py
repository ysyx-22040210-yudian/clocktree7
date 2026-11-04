"""Presentation of the complete graph; aggregation never changes the source graph."""
from collections import Counter, defaultdict, deque
from xml.sax.saxutils import escape, quoteattr
from .devices import typed_nodes, node_height, port_offsets, symbol_parts, symbol_svg
from .profiles import tree_type


NODE_WIDTH = 242
COLUMN_STEP = 302


def horizontal_ranks(nodes, edges):
    """Rank only same-layer connections; keep cycles in one vertical column.

    Iterative SCC condensation handles long chains without recursion and keeps
    a cyclic group from hiding its acyclic downstream continuation.
    """
    successors, predecessors = defaultdict(set), defaultdict(set)
    for edge in edges:
        source, target = edge['source'], edge['target']
        if nodes[source]['layer'] == nodes[target]['layer']:
            successors[source].add(target)
            predecessors[target].add(source)
    seen, finished = set(), []
    for root in sorted(nodes):
        if root in seen:continue
        seen.add(root)
        stack = [(root, iter(sorted(successors[root])))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(node); stack.pop()
            elif child not in seen:
                seen.add(child); stack.append((child, iter(sorted(successors[child]))))
    owners, groups = {}, []
    for root in reversed(finished):
        if root in owners:continue
        group = len(groups)
        owners[root] = group
        stack, members = [root], []
        while stack:
            node = stack.pop(); members.append(node)
            for parent in predecessors[node]:
                if parent not in owners:
                    owners[parent] = group; stack.append(parent)
        groups.append(sorted(members))
    outgoing, degrees = defaultdict(set), [0] * len(groups)
    for source, targets in successors.items():
        a = owners[source]
        for target in targets:
            b = owners[target]
            if a != b and b not in outgoing[a]:
                outgoing[a].add(b); degrees[b] += 1
    queue = deque(sorted((i for i, degree in enumerate(degrees) if not degree), key=lambda i:groups[i][0]))
    ranks = [0] * len(groups)
    while queue:
        source = queue.popleft()
        for target in sorted(outgoing[source]):
            ranks[target] = max(ranks[target], ranks[source] + 1)
            degrees[target] -= 1
            if not degrees[target]:queue.append(target)
    return {key:ranks[owner] for key, owner in owners.items()}


class GraphView:
    def __init__(self, graph):
        self.graph = graph
        self.tree_type = tree_type(graph.get('config',graph))
        self.original = typed_nodes(graph)
        self.layer_count = max([2 if not graph.get('config', {}).get('layers') else len(graph['config']['layers'])] +
                               [n['layer'] for n in self.original.values()])
        self.original_edges = {e['id']: e for e in graph['edges']}
        self.incoming, self.outgoing = defaultdict(list), defaultdict(list)
        for edge in graph['edges']:
            self.incoming[edge['target']].append(edge)
            self.outgoing[edge['source']].append(edge)
        self.nodes, self.edges, self.positions = {}, [], {}
        self.actual_count = 0
        self.bounds = (0, 0, self.layer_count * COLUMN_STEP + 36, 560)

    def project(self, mode='instances', component=None, scope='', depth=5):
        actual = [n for n in self.original.values() if
                  (component is None or n['component'] == component) and
                  (not scope or n['id'] == scope or n['id'].startswith(scope + '.') or n['id'].startswith(scope + '['))]
        self.actual_count = len(actual)
        ids = {n['id'] for n in actual}
        edges = [e for e in self.graph['edges'] if e['source'] in ids and e['target'] in ids]
        if mode == 'instances':
            self.nodes = {n['id']: n for n in actual}
            self.edges = edges
        else:
            groups, owners = {}, {}
            for node in actual:
                prefix = '.'.join(node['id'].split('.')[:depth])
                key = 'group:{}:{}:{}'.format(node['component'], node['layer'], prefix)
                if key not in groups:
                    groups[key] = {'id': key, 'path': prefix, 'layer': node['layer'], 'component': node['component'],
                                   'aggregate': True, 'members': [], 'module_counts': Counter(), 'kind_counts': Counter(), 'internal_edges': 0,
                                   'inputs': ['输入'], 'output': '输出', 'root_count': 0, 'terminal_count': 0,
                                   'has_unresolved_output': False, 'scope_input_boundary': False, 'scope_output_boundary': False}
                group = groups[key]
                group['members'].append(node['id'])
                group['module_counts'][node['module']] += 1
                group['kind_counts'][node.get('kind') or 'unclassified'] += 1
                group['root_count'] += bool(node.get('root'))
                group['terminal_count'] += bool(node.get('terminal'))
                group['has_unresolved_output'] |= bool(node.get('has_unresolved_output'))
                group['scope_input_boundary'] |= bool(node.get('scope_input_boundary'))
                group['scope_output_boundary'] |= bool(node.get('scope_output_boundary'))
                owners[node['id']] = group
            self.nodes = {}
            for group in groups.values():
                if len(group['members']) == 1:
                    node = self.original[group['members'][0]]
                    self.nodes[node['id']] = node
                    owners[node['id']] = node
                else:
                    group.update(module='{} 个实例 · {} 种模块'.format(len(group['members']), len(group['module_counts'])),
                                 root=group['root_count'] > 0, terminal=group['terminal_count'] > 0)
                    self.nodes[group['id']] = group
            bundles = {}
            for edge in edges:
                src, dst = owners[edge['source']], owners[edge['target']]
                if src['id'] == dst['id']:
                    src['internal_edges'] += 1
                    continue
                sp, dp = ('输出' if src.get('aggregate') else edge['source_pin']), ('输入' if dst.get('aggregate') else edge['target_pin'])
                key = (src['id'], sp, dst['id'], dp)
                if key not in bundles:
                    bundles[key] = dict(id='bundle:{}'.format(len(bundles)), source=src['id'], source_pin=sp,
                                        target=dst['id'], target_pin=dp, layer_delta=dst['layer'] - src['layer'], members=[])
                bundles[key]['members'].append(edge['id'])
            self.edges = list(bundles.values())
        self._layout()
        return self

    def _layout(self):
        self.positions = {}
        self.component_tops = {}
        ranks = horizontal_ranks(self.nodes, self.edges)
        self.layer_columns = dict.fromkeys(range(1, self.layer_count + 1), 1)
        for key, node in self.nodes.items():
            self.layer_columns[node['layer']] = max(self.layer_columns[node['layer']], ranks[key] + 1)
        self.layer_spans, starts = {}, {}
        column = 0
        for layer in range(1, self.layer_count + 1):
            starts[layer] = column
            width = self.layer_columns[layer] * COLUMN_STEP
            self.layer_spans[layer] = (18 + column * COLUMN_STEP, width - 24)
            column += self.layer_columns[layer]
        self.node_columns = {key:starts[node['layer']] + ranks[key] for key, node in self.nodes.items()}
        components, incoming, fanout = defaultdict(lambda:defaultdict(list)), defaultdict(list), Counter()
        heights, anchors = {}, {}
        for key, node in self.nodes.items():
            components[node['component']][self.node_columns[key]].append(key)
            heights[key] = node_height(node)
            anchors[key] = port_offsets(node, NODE_WIDTH, heights[key])
        for edge in self.edges:
            incoming[edge['target']].append(edge)
            if self.node_columns[edge['target']] > self.node_columns[edge['source']]:fanout[edge['source']] += 1
        offset = 92
        for component, columns in sorted(components.items()):
            self.component_tops[component] = offset
            bottom = offset + 116
            baseline = offset + max(anchors[key][1][1] for key in columns[min(columns)])
            for column, keys in sorted(columns.items()):
                preferred = {}
                for key in keys:
                    desired = []
                    node = self.nodes[key]
                    for edge in incoming[key]:
                        source = edge['source']
                        if source not in self.positions or self.node_columns[source] >= column:continue
                        index = node['inputs'].index(edge['target_pin']) if edge['target_pin'] in node['inputs'] else 0
                        desired.append(self.positions[source][1] + anchors[source][1][1] - anchors[key][0][index][1])
                    preferred[key] = sum(desired) / len(desired) if desired else baseline - anchors[key][1][1]
                next_y = offset
                for key in sorted(keys, key=lambda key:(preferred[key], -fanout[key], key)):
                    y = max(next_y, preferred[key])
                    self.positions[key] = (30 + column * COLUMN_STEP, y, NODE_WIDTH, heights[key])
                    next_y = y + heights[key] + 32
                bottom = max(bottom, next_y)
            offset = bottom + 56
        width = sum(self.layer_columns.values()) * COLUMN_STEP + 36
        self.bounds = (0, 0, width, max(500, offset))

    def edge_points(self, edge):
        sx, sy, sw, sh = self.positions[edge['source']]
        tx, ty, tw, th = self.positions[edge['target']]
        source, target = self.nodes[edge['source']], self.nodes[edge['target']]
        index = target['inputs'].index(edge['target_pin']) if edge['target_pin'] in target['inputs'] else 0
        _, source_output = port_offsets(source, sw, sh)
        target_inputs, _ = port_offsets(target, tw, th)
        start = (sx + source_output[0], sy + source_output[1])
        end = (tx + target_inputs[index][0], ty + target_inputs[index][1])
        distance = self.node_columns[edge['target']] - self.node_columns[edge['source']]
        if distance > 1 or distance < 0:
            # Intermediate columns can belong to the same logical layer. Route
            # above the component instead of crossing unrelated device bodies.
            top = self.component_tops[source['component']] - 16
            return [start, (start[0]+22, start[1]), (start[0]+22, top),
                    (tx-22, top), (tx-22, end[1]), end]
        if distance == 1:
            if start[1] == end[1]:return [start, end]
            lane = start[0] + max(18, (tx - start[0]) / 2)
            return [start, (lane, start[1]), (lane, end[1]), end]
        lane = start[0] + 24
        return [start, (lane, start[1]), (lane, ty - 15), (tx - 14, ty - 15), (tx - 14, end[1]), end]

    def find(self, query):
        query = query.strip().lower()
        if not query:
            return []
        exact = [n for n in self.original.values() if n['id'].lower() == query]
        return exact or [n for n in self.original.values() if query in n['id'].lower() or query in n['module'].lower()]

    def actual_edges(self, edge):
        return [self.original_edges[k] for k in edge['members']] if 'members' in edge else [edge]

    def path_edges(self, instance, direction):
        """All upstream/downstream selected edges, without duplicating shared nodes."""
        queue, seen, result = deque([instance]), {instance}, set()
        adjacency = self.incoming if direction == 'upstream' else self.outgoing
        endpoint = 'source' if direction == 'upstream' else 'target'
        while queue:
            for edge in adjacency[queue.popleft()]:
                result.add(edge['id'])
                other = edge[endpoint]
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
        return result

    def svg(self):
        """Export the current scope at readable world scale, retaining all projected nodes."""
        _, _, width, height = self.bounds
        lines = ['<svg xmlns="http://www.w3.org/2000/svg" data-tree-type="{}" width="{}" height="{}" viewBox="0 0 {} {}">'.format(self.tree_type, width, height, width, height),
                 '<rect width="100%" height="100%" fill="#101b2e"/>',
                 '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#6c94c8"/></marker></defs>']
        for layer in range(1, self.layer_count + 1):
            x, band_width = self.layer_spans[layer]
            lines.append('<rect class="layer-band" data-layer="{}" x="{}" y="12" width="{}" height="{}" rx="10" fill="#152239"/>'.format(layer, x, band_width, height - 24))
            lines.append('<text x="{}" y="46" fill="#a7bfe1" font-size="17" font-family="sans-serif">L{}</text>'.format(x + 13, layer))
        for edge in self.edges:
            points = ' '.join('{},{}'.format(x, y) for x, y in self.edge_points(edge))
            lines.append('<polyline class="edge" points="{}" fill="none" stroke="#6c94c8" stroke-width="1.4" marker-end="url(#a)"/>'.format(points))
        for node in self.nodes.values():
            x, y, w, h = self.positions[node['id']]
            path = node.get('path', node['id'])
            device_kind = 'aggregate' if node.get('aggregate') else node.get('kind') or 'unclassified'
            lines.append('<g class="device" data-device-kind={} data-instance={} transform="translate({},{})"><title>{}</title>'.format(quoteattr(device_kind),quoteattr(path),x,y,escape(path)))
            lines.append(symbol_svg(symbol_parts(node,w,h)))
            lines.append('</g>')
        return '\n'.join(lines + ['</svg>'])


def clip_segment(a, b, bounds):
    """Clip orthogonal wiring to the viewport, before issuing any Tk commands."""
    left, top, right, bottom = bounds
    x1, y1 = a
    x2, y2 = b
    if x1 == x2:
        if not left <= x1 <= right or max(y1, y2) < top or min(y1, y2) > bottom:
            return None
        return (x1, max(top, min(bottom, y1))), (x2, max(top, min(bottom, y2)))
    if y1 == y2:
        if not top <= y1 <= bottom or max(x1, x2) < left or min(x1, x2) > right:
            return None
        return (max(left, min(right, x1)), y1), (max(left, min(right, x2)), y2)
    return None
