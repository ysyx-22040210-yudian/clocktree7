"""Independent, cancellable input discovery. Regex is an editor operation only."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from .config import resolve_design_paths, write_tcl_config
from .environment import resolve_verdi
from .extract import vendor_work_dir


def filter_ports(rows, expression):
    pattern = re.compile(expression)
    return [row for row in rows if pattern.search(row['name'])]


def confirmed_ports(rows, expression, selected):
    matches = filter_ports(rows, expression)
    if not matches:
        raise ValueError('正则未匹配任何 input 端口')
    names = sorted(set(row['name'] for row in matches) & set(selected))
    if not names:
        raise ValueError('至少勾选一个匹配的 input 端口')
    return names


def discover(request_path, output):
    with open(str(request_path), encoding='utf-8-sig') as stream:
        request = json.load(stream)
    config = resolve_design_paths(copy.deepcopy(request['config']), request_path)
    module, scope = request['module'].strip(), request['hierarchy'].strip()
    if not module or not config.get('design', {}).get('top'):
        raise ValueError('请填写 module 和设计顶层')
    if not any(config['design'].get(k) for k in ('elab', 'filelist')):
        raise ValueError('请指定 elab 或 RTL 文件列表')
    config['layers'] = [{'index': 1, 'hierarchy': scope, 'modules': [
        {'module': module, 'kind': 'subsystem'}]}]
    config['port_trace_timeout_s'] = 0
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = vendor_work_dir(output)
    executable, home = resolve_verdi(config['design'], os.environ.copy())
    root = Path(__file__).resolve().parents[1]
    for source, target in [('extract.tcl', 'clocktree-extract.tcl'),
                           ('input_ports.tcl', 'input_ports.tcl'), ('discover_inputs.tcl', 'discover_inputs.tcl')]:
        shutil.copy2(str(root/'npi'/source), str(work/target))
    for key in ('elab', 'filelist'):
        if config['design'].get(key):
            from .extract import vendor_safe_path
            if not vendor_safe_path(config['design'][key]):
                alias = work/('input.elab++' if key == 'elab' else 'input.f')
                alias.symlink_to(config['design'][key], target_is_directory=key == 'elab')
                config['design'][key] = str(alias)
    raw = work/'input-ports.jsonl'
    write_tcl_config(config, work/'rules.tcl', raw)
    environment = dict(os.environ, VERDI_HOME=home, CT7_CONFIG=str(work/'rules.tcl'))
    # Inherit the GUI CLI's process group: cancellation kills both Python and
    # Verdi. No trace calls, no timer, and no source-code regex are involved.
    command = [executable, '-batch', '-nologo', '-play', 'discover_inputs.tcl']
    code = subprocess.call(command, cwd=str(work), env=environment)
    rows, completed, failure = [], None, None
    if raw.exists():
        with raw.open(encoding='utf-8') as stream:
            for line in stream:
                item = json.loads(line)
                if item['event'] == 'input_port': rows.append(item)
                elif item['event'] == 'input_discovery_complete': completed = item
                elif item['event'] == 'input_discovery_failed': failure = item['reason']
    if code or failure or not completed or not completed['instances']:
        raise RuntimeError(failure or '输入端口读取未完成，请查看前处理日志')
    result = dict(module=module, hierarchy=scope, instances=completed['instances'], ports=rows,
                  operation='input-discovery-only', traced_ports=0)
    (output/'input-ports.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
