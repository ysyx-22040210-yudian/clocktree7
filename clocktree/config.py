import json
import re
from pathlib import Path
from .devices import DEVICE_TYPES


def load_config(path):
    with open(str(path), encoding="utf-8-sig") as stream:
        return validate_config(json.load(stream))


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Configuration must be an object")
    layers = config.get("layers", [])
    if (not isinstance(layers, list) or not layers or
            any(not isinstance(x, dict) for x in layers) or
            any(type(x.get('index')) is not int for x in layers) or
            [x.get("index") for x in layers] != list(range(1, len(layers) + 1))):
        raise ValueError("Provide one or more ordered layers with consecutive index values starting at 1")
    for layer in layers:
        if not isinstance(layer.get("hierarchy"), str):
            raise ValueError("Each layer needs an RTL hierarchy string")
        for rule in layer.get("modules", []):
            if not rule.get("module") or not isinstance(rule["module"], str):
                raise ValueError("Each rule needs a module definition name")
            inputs = rule.get("clock_inputs", [])
            output = rule.get("clock_output")
            if not inputs or not isinstance(inputs, list) or not all(isinstance(p, str) and p for p in inputs):
                raise ValueError("clock_inputs must be a nonempty list of port names")
            if not isinstance(output, str) or not output or output in inputs or len(set(inputs)) != len(inputs):
                raise ValueError("Provide distinct clock inputs and one distinct clock_output")
            kind = rule.get('kind')
            if kind is not None and (not isinstance(kind, str) or kind not in DEVICE_TYPES):
                raise ValueError("kind must be mux, gate, occ or div")
            if kind in ('gate', 'occ', 'div') and len(inputs) != 1:
                raise ValueError("Gate, OCC and DIV rules require exactly one clock input")
    if not any(layer.get("modules") for layer in layers):
        raise ValueError("Provide at least one module rule")
    design = config.get("design", {})
    if not design.get("elab") and not design.get("filelist"):
        raise ValueError("design.elab or design.filelist is required")
    if not design.get("top"):
        raise ValueError("design.top is required")
    return config


def tcl_string(value):
    # Double-quoted Tcl literal: no substitutions from untrusted RTL paths/rules.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('[', '\\[').replace(']', '\\]').replace('\n', '\\n').replace('\r', '\\r') + '"'


def write_tcl_config(config, path, output):
    lines = ["set ct_rules {}"]
    for layer in config["layers"]:
        for rule in layer.get("modules", []):
            inputs = "[list " + " ".join(tcl_string(p) for p in rule["clock_inputs"]) + "]"
            fields = [str(layer["index"]), tcl_string(layer["hierarchy"]),
                      tcl_string(rule["module"]), inputs, tcl_string(rule["clock_output"]), tcl_string(rule.get('kind') or '')]
            lines.append("lappend ct_rules [list " + " ".join(fields) + "]")
    for key, value in config["design"].items():
        if key in ("elab", "filelist", "top"):
            lines.append("set ct_" + key + " " + tcl_string(value))
    lines.append("set ct_output " + tcl_string(output))
    lines.append("set ct_max_steps " + str(int(config.get("max_trace_steps", 0))))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
