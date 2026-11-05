"""User-declared clock/reset device kinds and shared Tk/SVG geometry."""
import math
import unicodedata
from xml.sax.saxutils import escape, quoteattr
from .profiles import profile, rule_ports


DEVICE_TYPES = {
    'mux': {'label': '选择器', 'short': 'MUX', 'color': '#66b7ff', 'fill': '#193954'},
    'gate': {'label': '门控', 'short': 'GATE', 'color': '#42d1b2', 'fill': '#163b38'},
    'occ': {'label': 'OCC', 'short': 'OCC', 'color': '#f1ba64', 'fill': '#3c3125'},
    'div': {'label': 'DIV 分频器', 'short': 'DIV', 'color': '#bb9bff', 'fill': '#302847'},
    'rsy_sync': {'label': 'RSY_SYNC', 'short': 'RSY_SYNC', 'color': '#f39bbd', 'fill': '#402939'},
}
TYPE_LABELS = [DEVICE_TYPES[kind]['label'] for kind in profile({})['kinds']]
LABEL_TO_KIND = {v['label']: k for k, v in DEVICE_TYPES.items()}
LABEL_TO_KIND['MUX'] = 'mux'


def kind_label(kind, tree='clock'):
    if kind == 'mux' and tree == 'reset':
        return 'MUX'
    return DEVICE_TYPES[kind]['label'] if kind in DEVICE_TYPES else '未分类'


def type_labels(config):
    return [kind_label(kind, config.get('tree_type', 'clock')) for kind in profile(config)['kinds']]


def scope_match(path, scope):
    return not scope or path == scope or path.startswith(scope + '.') or path.startswith(scope + '[')


def matching_rule(node, layer, rule):
    inputs, output = rule_ports(rule)
    return (node['layer'] == layer['index'] and node['module'] == rule['module'] and
            scope_match(node['id'] if 'id' in node else node['path'], layer['hierarchy']) and
            node['inputs'] == inputs and node['output'] == output)


def resolve_kind(node, config):
    """Read explicit metadata only. A module name never determines its kind."""
    allowed = profile(config)['kinds']
    if node.get('kind') in allowed:
        return node['kind']
    for layer in config.get('layers', []):
        for rule in layer.get('modules', []):
            if rule.get('kind') in allowed and matching_rule(node, layer, rule):
                return rule['kind']
    return None


def typed_nodes(graph):
    result = {}
    allowed = profile(graph.get('config', graph))['kinds']
    # Index rules so opening a large graph is linear in the number of instances.
    rules = {}
    for layer in graph.get('config', {}).get('layers', []):
        for rule in layer.get('modules', []):
            if rule.get('kind') in allowed:
                rules.setdefault((layer['index'], rule['module']), []).append((layer, rule))
    for original in graph['nodes']:
        node = dict(original)
        kind = node.get('kind')
        if kind not in allowed:
            kind = next((rule['kind'] for layer, rule in rules.get((node['layer'], node['module']), [])
                         if matching_rule(node, layer, rule)), None)
        if kind:
            node['kind'], node['kind_source'] = kind, 'configured'
        else:
            node.pop('kind', None)
        result[node['id']] = node
    return result


def node_height(node):
    if node.get('aggregate'):
        return 144
    return max(152, 88 + 32 * len(node['inputs']))


def port_offsets(node, width, height):
    """The sole source of pin anchors for symbols and all routed edges."""
    if node.get('aggregate'):
        return [(0, 110)], (width, 110)
    count = len(node['inputs'])
    middle = (50 + height - 18) / 2
    # Leave room beneath the last signal label for boundary/status markers.
    ys = [middle] if count == 1 else [60 + i * (height - 104) / (count - 1) for i in range(count)]
    return [(0, y) for y in ys], (width, middle)


def _short(text, limit):
    return text if len(text) <= limit else text[:limit - 1] + '…'


def connection_label(node, pin):
    """Instantiation expression only; never substitute a traced net or driver."""
    connection = node.get('port_connections', {}).get(pin)
    if not connection:
        return '未采集'
    if connection.get('status') == 'unconnected':
        return '未连接'
    if connection.get('status') != 'connected' or not connection.get('expression'):
        return '未取得'
    prefix = '整口: ' if connection.get('coverage') == 'whole_port' else ''
    return prefix + connection['expression']


def connection_details(node, pins):
    lines = []
    for pin in pins:
        connection = node.get('port_connections', {}).get(pin, {})
        lines.append('{} = {}'.format(pin, connection_label(node, pin)))
        if connection.get('full_name'):
            lines.append('  完整信号：' + connection['full_name'])
        if connection.get('coverage') == 'whole_port':
            lines.append('  .{} 的整体连接；关注 {}，未推断逐位映射'.format(connection['formal_port'], pin))
        if connection.get('reason'):
            lines.append('  原因：' + connection['reason'])
        if node.get('trace_timeout'):
            state = node.get('port_trace_status', {}).get(pin, 'not_traced')
            label = {'trace_timeout':'trace 超时；本条未完成证据已撤回',
                     'trace_skipped_timeout':'trace 已跳过（本例化其他端口超时）',
                     'completed':'此前 trace 已完成，证据保留',
                     'not_requested':'本例化 trace 超时；目标结构模式未执行输出 load trace',
                     'not_traced':'本例化 trace 超时；此端口未执行 trace'}.get(state, state)
            lines.append('  ' + label)
    return '\n'.join(lines)


def trace_pin_label(node, pin):
    state = node.get('port_trace_status', {}).get(pin)
    suffix = {'trace_timeout':'trace 超时', 'trace_skipped_timeout':'trace 跳过',
              'not_requested':'实例超时', 'not_traced':'实例超时'}.get(state)
    return pin + ' · ' + suffix if suffix else pin


def symbol_parts(node, width, height, selected=False):
    """Return neutral drawing primitives consumed by native Tk and SVG alike.

    Only configured ports have external wires. Internal pulse/clock marks
    are pictograms, not invented SEL/EN/test pins or simulated waveforms.
    """
    parts = []
    kind = node.get('kind')
    aggregate = node.get('aggregate', False)
    info = DEVICE_TYPES.get(kind, {'color': '#8199b9', 'fill': '#1c2d44', 'short': '未分类'})
    color = info['color']
    inputs, output = port_offsets(node, width, height)
    def add(op, **kwargs):
        parts.append(dict(op=op, **kwargs))
    def line(points, fill=color, stroke_width=1.5, **kwargs):
        add('polyline', points=points, fill=fill, width=stroke_width, **kwargs)
    def text(x, y, value, size=10, fill='#b9cee9', bold=False, anchor='w', max_width=None, tooltip=None):
        add('text', x=x, y=y, text=value, size=size, fill=fill, bold=bold, anchor=anchor, max_width=max_width, tooltip=tooltip)
    add('rect', x=0, y=0, w=width, h=height, radius=8,
        fill='#1b3350' if selected else '#17283e', stroke='#78bdff' if selected else ('#f5a46b' if node.get('trace_timeout') else '#2e445f'),
        width=2 if selected else 1, dash=aggregate)
    path = node.get('path', node['id'])
    title = path.split('.')[-1] if aggregate else '.'.join(path.split('.')[-2:])
    text(11, 16, title, size=12, fill='#e5edf9', bold=True, max_width=width-(88 if kind=='rsy_sync' else 70))
    text(11, 34, node['module'], size=10, fill='#91a9c8', max_width=width-(115 if node.get('trace_timeout') else 20))
    if node.get('trace_timeout'):
        text(width-11, 34, 'trace 超时', size=10, fill='#ffb27b', bold=True, anchor='e')
    if aggregate:
        text(width-11, 16, 'RTL', size=9, fill='#9eb5d4', anchor='e')
        counts = node.get('kind_counts', {})
        slot = 0
        for device in DEVICE_TYPES:
            n = counts.get(device, 0)
            if n:
                text(12 + (slot % 2) * 114, 56 + (slot // 2) * 20,
                     '{} ×{}'.format(DEVICE_TYPES[device]['short'], n), size=9,
                     fill=DEVICE_TYPES[device]['color'], max_width=105)
                slot += 1
        if counts.get('unclassified'):
            text(12, 130, '未分类 ×{}'.format(counts['unclassified']), size=9, fill='#7e95b3')
        for (px, py) in inputs:
            line([(px, py), (18, py)], fill='#8eabc8')
        line([(width-18, output[1]), output], fill='#8eabc8')
        text(22, 110, '输入', size=9)
        text(width-22, 110, '输出', size=9, anchor='e')
    elif kind in DEVICE_TYPES:
        text(width-11, 16, info['short'], size=9, fill=color, bold=True, anchor='e')
        symbol_begin = len(parts)
        cy = output[1]
        if kind == 'mux':
            add('polygon', points=[(78, 50), (166, 63), (166, height-31), (78, height-18)],
                fill=info['fill'], stroke=color, width=1.8)
            text(120, cy-6, 'MUX', size=15, fill=color, bold=True, anchor='center')
            text(120, cy+13, '{}:1'.format(len(inputs)), size=10, fill='#a4c9e8', anchor='center')
            input_end, output_start = 78, 166
        elif kind == 'gate':
            points = [(78, cy-29), (114, cy-29)]
            points += [(114+29*math.cos(a), cy+29*math.sin(a))
                       for a in [-math.pi/2+i*math.pi/24 for i in range(25)]]
            points += [(78, cy+29)]
            add('polygon', points=points, fill=info['fill'], stroke=color, width=1.8)
            text(108, cy-6, 'GATE', size=12, fill=color, bold=True, anchor='center')
            line([(92,cy+13),(98,cy+13),(98,cy+5),(105,cy+5),(105,cy+13),(117,cy+13)], stroke_width=1.2)
            input_end, output_start = 78, 143
        elif kind == 'div':
            left, right = 56, 180
            add('polygon', points=[(left+12,cy-31),(right-12,cy-31),(right,cy),
                                   (right-12,cy+31),(left+12,cy+31),(left,cy)],
                fill=info['fill'], stroke=color, width=1.8)
            text(118, cy-10, 'DIV', size=15, fill=color, bold=True, anchor='center')
            text(118, cy+12, '÷N', size=17, fill='#d6c3ff', anchor='center')
            input_end, output_start = left, right
        elif kind == 'rsy_sync':
            left, right = 66, 178
            add('rect', x=left, y=cy-32, w=right-left, h=64, radius=5,
                fill=info['fill'], stroke=color, width=1.8)
            text(122, cy-18, 'RSY_SYNC', size=12, fill=color, bold=True, anchor='center')
            for x, label in ((83, 'S1'), (135, 'S2')):
                add('rect', x=x, y=cy-2, w=26, h=22, radius=2,
                    fill='#2c2334', stroke=color, width=1.1)
                text(x+13, cy+9, label, size=8, fill='#f3c9dc', anchor='center')
            line([(109,cy+9),(132,cy+9),(127,cy+5)], stroke_width=1.1)
            input_end, output_start = left, right
        else:
            left, right, top, bottom, cut = 66, 178, cy-31, cy+31, 8
            add('polygon', points=[(left+cut,top),(right-cut,top),(right,top+cut),(right,bottom-cut),
                                   (right-cut,bottom),(left+cut,bottom),(left,bottom-cut),(left,top+cut)],
                fill=info['fill'], stroke=color, width=1.8)
            text(122, cy-8, 'OCC', size=16, fill=color, bold=True, anchor='center')
            pulse = [(84,cy+16)]
            for x in (91,112,133):
                pulse += [(x,cy+16),(x,cy+6),(x+9,cy+6),(x+9,cy+16)]
            pulse.append((160,cy+16))
            line(pulse, stroke_width=1.3)
            input_end, output_start = left, right
        shift = (width - 242) / 2
        for part in parts[symbol_begin:]:
            if 'x' in part:part['x'] += shift
            if 'points' in part:part['points'] = [(x + shift, y) for x, y in part['points']]
        input_end, output_start = input_end + shift, output_start + shift
        for pin, (px, py) in zip(node['inputs'], inputs):
            line([(px, py), (input_end, py)])
            detail = '{} · {}'.format(path, connection_details(node, [pin]))
            text(7, py-11, trace_pin_label(node,pin), size=11, fill='#ffb27b' if node.get('trace_timeout') else '#b5cbe5', max_width=input_end-12, tooltip=detail)
            text(7, py+11, connection_label(node,pin), size=11, fill='#9fcfc3', max_width=input_end-12, tooltip=detail)
        line([(output_start, cy), output])
        detail = '{} · {}'.format(path, connection_details(node, [node['output']]))
        text(width-7, cy-11, trace_pin_label(node,node['output']), size=11, fill='#ffb27b' if node.get('trace_timeout') else '#b5cbe5', anchor='e', max_width=width-output_start-10, tooltip=detail)
        text(width-7, cy+11, connection_label(node,node['output']), size=11, fill='#9fcfc3', anchor='e', max_width=width-output_start-10, tooltip=detail)
    else:
        text(width-10,16,'待分类',size=9,fill='#8097b6',anchor='e')
        for pin, (px,py) in zip(node['inputs'],inputs):
            text(10,py-7,trace_pin_label(node,pin),size=9,max_width=width/2-12,tooltip=connection_details(node,[pin]))
            text(10,py+7,connection_label(node,pin),size=9,max_width=width/2-12,tooltip=connection_details(node,[pin]))
        text(width-10,output[1]-7,trace_pin_label(node,node['output']),size=9,anchor='e',max_width=width/2-12,tooltip=connection_details(node,[node['output']]))
        text(width-10,output[1]+7,connection_label(node,node['output']),size=9,anchor='e',max_width=width/2-12,tooltip=connection_details(node,[node['output']]))
    for px, py in inputs+[output]:
        add('circle', x=px, y=py, r=3, fill='#14253a', stroke=color, width=1.3)
    status_x = width-12
    scope_labels = [label for value, label in [(node.get('scope_input_boundary'), '范围入口'),
                                              (node.get('scope_output_boundary'), '范围出口')] if value]
    if scope_labels and not aggregate:
        text(11, height-10, ' / '.join(scope_labels), size=8, fill='#c1a6ff', max_width=width-55)
    if scope_labels:
        add('rect',x=status_x-3,y=height-12,w=6,h=6,radius=0,fill='#b99af2',stroke='',width=0)
        status_x -= 11
    for state, fill in [(node.get('root'),'#4ad4ab'),(node.get('terminal'),'#eeb75e'),(node.get('has_unresolved_output'),'#f0827d')]:
        if state:
            add('circle',x=status_x,y=height-9,r=2.6,fill=fill,stroke='',width=0)
            status_x -= 11
    return parts


def display_text(part):
    text = part['text']
    maximum = part.get('max_width')
    if not maximum:
        return text
    def measure(value):
        return sum(part['size'] * (1 if unicodedata.east_asian_width(c) in ('W','F') else .6) for c in value)
    if measure(text) <= maximum:
        return text
    while text and measure(text+'…') > maximum:
        text = text[:-1]
    return text+'…'


def symbol_svg(parts):
    lines = []
    for p in parts:
        op = p['op']
        if op == 'text':
            anchor = {'w':'start','e':'end','center':'middle'}[p['anchor']]
            tooltip = '<title>{}</title>'.format(escape(p['tooltip'])) if p.get('tooltip') else ''
            lines.append('<text x="{}" y="{}" fill={} font-size="{}" font-weight="{}" text-anchor="{}" dominant-baseline="middle" font-family="Microsoft YaHei,WenQuanYi Micro Hei,sans-serif">{}{}</text>'.format(
                p['x'],p['y'],quoteattr(p['fill']),p['size'],'bold' if p['bold'] else 'normal',anchor,escape(display_text(p)),tooltip))
        elif op == 'rect':
            lines.append('<rect x="{}" y="{}" width="{}" height="{}" rx="{}" fill={} stroke={} stroke-width="{}"{}/>'.format(
                p['x'],p['y'],p['w'],p['h'],p['radius'],quoteattr(p['fill']),quoteattr(p['stroke']),p['width'],
                ' stroke-dasharray="5 3"' if p.get('dash') else ''))
        elif op == 'circle':
            lines.append('<circle cx="{}" cy="{}" r="{}" fill={} stroke={} stroke-width="{}"/>'.format(
                p['x'],p['y'],p['r'],quoteattr(p['fill']),quoteattr(p['stroke'] or 'none'),p['width']))
        elif op == 'polygon':
            points = ' '.join('{},{}'.format(x,y) for x,y in p['points'])
            lines.append('<polygon points="{}" fill={} stroke={} stroke-width="{}"/>'.format(points,quoteattr(p['fill']),quoteattr(p['stroke']),p['width']))
        elif op == 'polyline':
            points = ' '.join('{},{}'.format(x,y) for x,y in p['points'])
            lines.append('<polyline class="device-mark" points="{}" fill="none" stroke={} stroke-width="{}"/>'.format(points,quoteattr(p['fill']),p['width']))
    return '\n'.join(lines)
