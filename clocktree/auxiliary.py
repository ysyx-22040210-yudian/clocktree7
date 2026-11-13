"""Optional annotation ports. Never add these ports to connectivity seeds."""
import re
from .profiles import GATE_KINDS


AUX_ROLES = {
    'mux': (('pll_sel', 'input'),),
    'div': (('div', 'input'), ('sw_done', 'output')),
}
AUX_ROLES.update({kind: (('gt', 'input'),) for kind in GATE_KINDS})
DIRECTIONS = {'input': '输入', 'output': '输出'}


def validate_aux_ports(rule, inputs, output):
    values = rule.get('aux_ports', {})
    if not isinstance(values, dict):
        raise ValueError('aux_ports 必须是辅助功能到实际端口名的对象')
    allowed = dict(AUX_ROLES.get(rule.get('kind'), ()))
    result, used = {}, set()
    primary = {re.sub(r'\[-?\d+\]$', '', pin) for pin in list(inputs) + (output if isinstance(output, list) else [output] if output else [])}
    for role, value in values.items():
        if role not in allowed:
            raise ValueError('{} 器件不支持辅助端口 {}'.format(rule.get('kind'), role))
        if not isinstance(value, str):
            raise ValueError('{} 请填写实际端口名，或留空'.format(role))
        pin = value.strip()
        if not pin:
            continue
        base = re.sub(r'\[-?\d+\]$', '', pin)
        if base in primary or base in used:
            raise ValueError('辅助端口 {} 不能重复或与参与追踪的端口重合'.format(pin))
        used.add(base)
        result[role] = pin
    return result


def aux_entries(node):
    values = node.get('aux_ports', {})
    return [(role, values[role], direction) for role, direction in AUX_ROLES.get(node.get('kind'), ())
            if values.get(role)]


def aux_height(node):
    count = len(aux_entries(node)) if not node.get('aggregate') else 0
    return 24 + 36 * count if count else 0


def aux_problem(node, role, pin, direction):
    value = node.get('port_connections', {}).get(pin, {})
    if value.get('reason') == 'port_not_found':
        return 'aux_port_missing', '{} 辅助端口 {} 不存在'.format(role, pin)
    actual = value.get('direction')
    if actual and actual != 'npi' + direction.title():
        return 'aux_port_direction', '{} 需要{}端口，elab 方向为 {}'.format(role, DIRECTIONS[direction], actual)
    if value.get('status') == 'unconnected':
        return 'aux_port_unconnected', '{} 辅助端口 {} 未连接'.format(role, pin)
    if value.get('status') != 'connected' or not value.get('expression') or not actual:
        return 'aux_connection_unavailable', '{} 辅助端口 {} 的绑定或方向未取得：{}'.format(role, pin, value.get('reason', '请重新提取并查看 NPI 日志'))
    return None


def aux_diagnostics(node):
    for role, pin, direction in aux_entries(node):
        problem = aux_problem(node, role, pin, direction)
        if problem:
            yield {'code': problem[0], 'message': problem[1], 'instance': node['id'], 'pin': pin,
                   'role': role, 'expected_direction': direction, 'display_only': True}
