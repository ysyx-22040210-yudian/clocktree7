"""Configured tree inputs without a device edge or a verified boundary in the full result."""
from collections import defaultdict
import json

from .l1_inputs import entries


FIELD = 'inputs_without_source'
LABEL = '输入缺少上游连接'


def missing_inputs(node, incoming):
    connected = set(incoming)
    connected.update(entries(node))
    return list(dict.fromkeys(pin for pin in node.get('inputs', ()) if pin not in connected))


def visible_gaps(node):
    missing = node.get(FIELD, ())
    if not missing:return []
    visible = set(node['inputs'])
    return [pin for pin in missing if pin in visible]


def _legacy_index(graph, cancel):
    # Merge two ordered cursors so an old read-only index need not be copied
    # or materialized. Only endpoint columns are read from the edge table.
    conn = graph.store.connection()
    incoming = conn.execute('SELECT e.target,e.target_pin FROM edges e JOIN nodes s ON s.id=e.source ORDER BY e.target')
    nodes = conn.execute('SELECT id,data FROM nodes ORDER BY id')
    try:
        edge = next(incoming, None)
        for key,data in nodes:
            if cancel is not None and cancel.is_set():return
            pins = set()
            while edge is not None and edge[0] <= key:
                if edge[0] == key:pins.add(edge[1])
                edge = next(incoming, None)
            node = json.loads(data)
            missing = missing_inputs(node, pins)
            if missing:yield node, missing
    finally:
        incoming.close()
        nodes.close()


def nodes_with_gaps(graph, cancel=None):
    if graph.get('summary', {}).get(FIELD) == 0:
        return
    indexed = hasattr(graph, 'store')
    stored = FIELD in graph.get('summary', {}) and not getattr(graph, 'legacy_input_coverage', False)
    if stored or graph.get('branch_view'):
        if indexed:
            rows = graph.store.connection().execute('SELECT data FROM nodes WHERE instr(data,?)>0 ORDER BY seq', ('"'+FIELD+'"',))
            nodes = (json.loads(data) for data, in rows)
        else:
            nodes = graph.get('nodes', ())
        try:
            for node in nodes:
                if cancel is not None and cancel.is_set():return
                if node.get(FIELD):yield node, node[FIELD]
        finally:
            if indexed:rows.close()
    elif indexed:
        yield from _legacy_index(graph, cancel)
    else:
        incoming = defaultdict(set)
        nodes = graph.get('nodes', ())
        ids = {n['id'] for n in nodes}
        for edge in graph.get('edges', ()):
            if cancel is not None and cancel.is_set():return
            if edge['source'] in ids:incoming[edge['target']].add(edge['target_pin'])
        for node in nodes:
            if cancel is not None and cancel.is_set():return
            missing = missing_inputs(node, incoming.get(node['id'], ()))
            if missing:yield node, missing


def prepare_coverage(graph):
    """Backfill a saved full result in memory. Never infer gaps from a projected view."""
    if FIELD in graph['summary'] or graph.get('branch_view'):
        return graph
    if hasattr(graph,'store'):
        graph.legacy_input_coverage = True
        count = sum(len(pins) for _,pins in nodes_with_gaps(graph))
    else:
        marked = {node['id']:dict(node, inputs_without_source=pins) for node,pins in nodes_with_gaps(graph)}
        count = sum(len(n[FIELD]) for n in marked.values())
        graph = dict(graph, nodes=[marked.get(n['id'], n) for n in graph['nodes']])
    graph['summary'] = dict(graph['summary'], inputs_without_source=count)
    return graph


def legacy_node(graph, node, connection=None):
    if not graph.get('summary', {}).get(FIELD):return node
    conn = graph.store.connection() if connection is None else connection
    incoming = conn.execute(
        'SELECT e.target_pin FROM edges e JOIN nodes s ON s.id=e.source WHERE e.target=?', (node['id'],))
    missing = missing_inputs(node, (pin for pin, in incoming))
    return dict(node, inputs_without_source=missing) if missing else node


CAUSES = {
    'undriven':'输入反查发现无驱动',
    'unconnected':'RTL 例化输入留空',
    'constant':'输入追到常量',
    'input_boundary':'到达了层次端口，结果中缺少已确认的边界信号',
    'scope_boundary':'追踪在范围边缘停止',
    'unselected_port_boundary':'追到未纳入规则的器件端口',
    'layer_input_boundary':'出现了层次端口候选，未唯一确认',
    'layer_wire_boundary':'出现了层次 wire 候选，未唯一确认',
    'multiple_l1_inputs':'存在多个 L1 输入候选',
    'multiple_layer_inputs':'存在多个边界候选',
    'multiple_drivers':'存在多个来源候选',
    'trace_timeout':'输入反查超时',
    'trace_skipped_timeout':'该输入因同实例的其他端口超时而跳过',
    'not_traced':'该输入未执行反查',
    'missing_trace':'缺少输入反查记录',
    'trace_protocol':'输入反查记录不完整',
    'missing_pin':'elab 中未找到该输入端口',
    'unknown_cell':'追踪在未解析的器件处停止',
    'unsupported_expression':'追踪遇到不支持的表达式',
    'trace_limit':'追踪达到限制',
    'width_requires_bit':'输入位宽或位映射未确定',
}


def gap_reason(node, pin, observations):
    statuses = set(observations)
    trace = node.get('port_trace_status', {}).get(pin)
    if trace in CAUSES:statuses.add(trace)
    if node.get('port_connections', {}).get(pin, {}).get('status') == 'unconnected':
        statuses.add('unconnected')
    detail = '、'.join(CAUSES[s] for s in sorted(statuses) if s in CAUSES)
    message = '该输入在提取结果中既没有上游器件输出，也没有已确认的边界信号。'
    return message + (detail+'。' if detail else '')
