import copy
import json
import os
import math
from pathlib import Path
from .profiles import TREE_PROFILES, SINGLE_INPUT_KINDS, tree_type, profile, rule_ports
from .auxiliary import AUX_ROLES, validate_aux_ports
from .layer_reset import validate_reset_signal

EXTRACTION_MODES = {'target': '目标结构提取（大设计推荐）', 'full': '全负载核验'}


def load_config(path):
    with open(str(path), encoding="utf-8-sig") as stream:
        config = validate_config(json.load(stream))
    return resolve_design_paths(config, path)


def resolve_design_paths(config, config_path=None):
    """Resolve a declared project base once, before a GUI job moves the config.

    Legacy relative paths retain their launch-directory semantics. Explicit
    base_dir is relative to the configuration file, making project configs
    independent of where the tool itself is installed.
    """
    design = config['design']
    base = Path.cwd()
    if design.get('base_dir'):
        base = expanded_path(design['base_dir'])
        if not base.is_absolute():
            base = (Path(config_path).resolve().parent if config_path else Path.cwd()) / base
        base = base.resolve()
        design['base_dir'] = str(base)
    for key in ('elab', 'filelist'):
        if design.get(key):
            path = expanded_path(design[key])
            design[key] = str((path if path.is_absolute() else base / path).resolve())
    return config


def expanded_path(value):
    return Path(os.path.expandvars(str(value))).expanduser()


def hierarchy_contains(path, root):
    root = root.rstrip('./')
    return not root or path == root or path.startswith(root + '.') or path.startswith(root + '[')


def div_source_scopes(config, allow_incomplete=False):
    """Two user-configured RTL scopes, in the order shown in the editor."""
    values = config.get('div_source_scopes', [])
    if not isinstance(values, list) or len(values) not in (0, 2) or any(not isinstance(v, str) for v in values):
        raise ValueError('DIV 来源需填写两条 RTL 层次路径')
    scopes = tuple(v.strip().rstrip('./') for v in values)
    if not any(scopes):
        return ()
    if allow_incomplete:
        return scopes
    if not all(scopes) or scopes[0] == scopes[1]:
        raise ValueError('DIV 来源层次 1 和 2 需同时填写，且路径必须不同')
    top = config.get('design', {}).get('top', '')
    if any(not top or not hierarchy_contains(v, top) or any(c in v for c in '*?') for v in scopes):
        raise ValueError('DIV 来源请填写设计顶层内的实际 RTL 层次路径')
    return scopes


def extraction_settings(config):
    """Settings that determine the graph, excluding branch annotations."""
    result = copy.deepcopy(config)
    result.pop('div_source_scopes', None)
    for layer in result.get('layers', []):
        layer.pop('reset_signal', None)
        for rule in layer.get('modules', []):
            for field in ('input_regex', 'output_regex', 'port_selection'):
                rule.pop(field, None)
    return result


def validate_config(config, allow_incomplete=False):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object")
    mode = tree_type(config)
    if not isinstance(mode, str) or mode not in TREE_PROFILES:
        raise ValueError('tree_type must be clock or reset')
    info = profile(config)
    if not isinstance(config.get('extraction_mode', 'target'), str) or config.get('extraction_mode', 'target') not in EXTRACTION_MODES:
        raise ValueError('extraction_mode must be target or full')
    layers = config.get("layers", [])
    if (not isinstance(layers, list) or not layers or
            any(not isinstance(x, dict) for x in layers) or
            any(type(x.get('index')) is not int for x in layers) or
            [x.get("index") for x in layers] != list(range(1, len(layers) + 1))):
        raise ValueError("Provide one or more ordered layers with consecutive index values starting at 1")
    for layer in layers:
        if not isinstance(layer.get("hierarchy"), str):
            raise ValueError("Each layer needs an RTL hierarchy string")
        validate_reset_signal(layer.get('reset_signal',''),allow_incomplete)
        for rule in layer.get("modules", []):
            if not rule.get("module") or not isinstance(rule["module"], str):
                raise ValueError("Each rule needs a module definition name")
            other = TREE_PROFILES['clock' if mode == 'reset' else 'reset']
            if other['inputs'] in rule or other['output'] in rule or other['output']+'s' in rule:
                raise ValueError('Use {} and {} in {} rules'.format(info['inputs'], info['output'], mode))
            inputs, output = rule_ports(rule, mode)
            if not inputs or not isinstance(inputs, list) or not all(isinstance(p, str) and p for p in inputs):
                raise ValueError(info['inputs'] + " must be a nonempty list of port names")
            kind = rule.get('kind')
            if kind == 'subsystem' and output not in (None, ''):
                raise ValueError('SUBSYSTEM has input terminals only; omit its output')
            if kind == 'wire':
                if (not isinstance(output, list) or not output or
                        not all(isinstance(p, str) and p for p in output) or
                        len(set(output)) != len(output) or set(inputs).intersection(output) or rule.get(info['output'])):
                    raise ValueError('穿线器件需要不重复的输入和输出列表：' + info['output']+'s')
            elif info['output']+'s' in rule:
                raise ValueError('Only wire devices support multiple outputs')
            if len(set(inputs)) != len(inputs) or (kind not in ('subsystem', 'wire') and
                    (not isinstance(output, str) or not output or output in inputs)):
                raise ValueError('Provide distinct inputs and one distinct ' + info['output'])
            if (kind is not None or mode == 'reset') and (not isinstance(kind, str) or kind not in info['kinds']):
                raise ValueError('kind must be ' + ', '.join(info['kinds']) + ' for ' + mode + ' analysis')
            if kind in SINGLE_INPUT_KINDS and len(inputs) != 1:
                raise ValueError('{} rules require exactly one input'.format(kind.upper()))
            validate_aux_ports(rule, inputs, output)
    if not allow_incomplete and not any(layer.get("modules") for layer in layers):
        raise ValueError("Provide at least one module rule")
    design = config.get("design", {})
    if not isinstance(design, dict):
        raise ValueError('design must be an object')
    if not allow_incomplete and not design.get("elab") and not design.get("filelist"):
        raise ValueError("design.elab or design.filelist is required")
    if not allow_incomplete and not design.get("top"):
        raise ValueError("design.top is required")
    div_source_scopes(config, allow_incomplete)
    trace_scope = config.get('trace_scope', '')
    if not isinstance(trace_scope, str):
        raise ValueError('trace_scope must be an exact RTL hierarchy string')
    trace_scope = trace_scope.strip().rstrip('./')
    if 'memory_limit_gib' in config and not allow_incomplete:
        limit = config['memory_limit_gib']
        if type(limit) not in (int, float) or not math.isfinite(limit) or limit < 0:
            raise ValueError('memory_limit_gib must be a finite nonnegative number (0 disables memory protection)')
    if 'port_trace_timeout_s' in config and not allow_incomplete:
        seconds = config['port_trace_timeout_s']
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('port_trace_timeout_s must be a finite nonnegative number (0 means unlimited)')
    if trace_scope and not allow_incomplete:
        if '*' in trace_scope or '?' in trace_scope or not hierarchy_contains(trace_scope, design['top']):
            raise ValueError('trace_scope must be an exact hierarchy at or below design.top')
        for layer in layers:
            scope = layer['hierarchy'].strip().rstrip('./')
            if layer.get('modules') and scope and not hierarchy_contains(scope, trace_scope):
                raise ValueError('L{} hierarchy must be inside trace_scope {}; leave it blank to use that root'.format(layer['index'], trace_scope))
    return config


def tcl_string(value):
    # Double-quoted Tcl literal: no substitutions from untrusted RTL paths/rules.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('[', '\\[').replace(']', '\\]').replace('\n', '\\n').replace('\r', '\\r') + '"'


def write_tcl_config(config, path, output):
    first = next((layer for layer in config['layers'] if layer['index'] == 1), {})
    l1 = first.get('hierarchy', '').strip().rstrip('./') or config.get('trace_scope', '').strip().rstrip('./') or config['design'].get('top', '')
    lines = ["set ct_rules {}", 'set ct_tree_type ' + tcl_string(tree_type(config)),
             'set ct_l1_scope ' + tcl_string(l1),
             'set ct_extraction_mode ' + tcl_string(config.get('extraction_mode', 'target')),
             'set ct_port_trace_timeout_s ' + str(config.get('port_trace_timeout_s', 0)),
             'set ct_trace_scope ' + tcl_string(config.get('trace_scope', '').strip().rstrip('./'))]
    for layer in config["layers"]:
        for rule in layer.get("modules", []):
            pins, output_pin = rule_ports(rule, tree_type(config))
            inputs = "[list " + " ".join(tcl_string(p) for p in pins) + "]"
            out = ('[list ' + ' '.join(tcl_string(p) for p in output_pin) + ']') if rule.get('kind') == 'wire' else tcl_string(output_pin or '')
            fields = [str(layer["index"]), tcl_string(layer["hierarchy"]),
                      tcl_string(rule["module"]), inputs, out, tcl_string(rule.get('kind') or '')]
            aux = validate_aux_ports(rule, pins, output_pin)
            entries = ['[list {} {} {}]'.format(tcl_string(role), tcl_string(aux[role]), tcl_string('npi' + direction.title()))
                       for role, direction in AUX_ROLES.get(rule.get('kind'), ()) if role in aux]
            fields.append('[list ' + ' '.join(entries) + ']')
            lines.append("lappend ct_rules [list " + " ".join(fields) + "]")
    for key, value in config["design"].items():
        if key in ("elab", "filelist", "top"):
            lines.append("set ct_" + key + " " + tcl_string(value))
    lines.append("set ct_output " + tcl_string(output))
    lines.append("set ct_max_steps " + str(int(config.get("max_trace_steps", 0))))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
