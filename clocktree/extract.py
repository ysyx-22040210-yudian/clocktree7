import json
import os
from pathlib import Path
import subprocess
import time
import re
import shutil
import uuid
import platform
import sys
import tempfile

from .config import load_config, write_tcl_config, validate_config
from .graph import build_graph, read_events
from .environment import resolve_verdi
from .profiles import result_filename
from .storage import new_store, index_graph, write_graph, publish_index, index_pointer
from .memory import run_verdi


RESULT_FILES = ('rules.tcl', 'config.json', 'raw.jsonl', 'verdi.log', 'clocktree.json', 'resettree.json', 'summary.json', 'memory.json', 'memory-restarts.jsonl', 'module-memory-checks.jsonl')


def vendor_safe_path(path):
    # Old Verdi launch scripts use unquoted shell paths and legacy encodings.
    value = Path(path).as_posix() if os.name == 'nt' else str(path)
    return re.fullmatch(r'[A-Za-z0-9_./:+-]+', value) is not None


def vendor_work_dir(preferred):
    preferred = Path(preferred).resolve()
    if vendor_safe_path(preferred):
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    base = Path(os.environ.get('CLOCKTREE_EDA_TMPDIR', tempfile.gettempdir())).expanduser().resolve()
    if not vendor_safe_path(base):
        raise ValueError('Set CLOCKTREE_EDA_TMPDIR to a writable ASCII directory without spaces or shell metacharacters')
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='clocktree7-', dir=str(base)))


def prepare_output(out):
    """Retain previous evidence and use fresh Verdi scratch space on each run."""
    identifier = time.strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex
    if any((out / name).is_file() for name in ('raw.jsonl', 'verdi.log', 'clocktree.json', 'resettree.json', 'summary.json')):
        history = out / '.clocktree-history' / identifier
        history.mkdir(parents=True)
        for name in RESULT_FILES:
            source = out / name
            if source.is_file():
                shutil.copy2(str(source), str(history / name))
        # Indexes are immutable; retain the database referenced by each result
        # alongside its archived JSON, without invalidating an open viewer.
        for name in ('clocktree.json', 'resettree.json'):
            pointer = index_pointer(out / name)
            if pointer.is_file():
                try:
                    data = json.loads(pointer.read_text(encoding='utf-8'))
                    database = (out / data['database']).resolve()
                    database.relative_to(out.resolve())
                    destination = history / data['database']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(database), str(destination))
                    shutil.copy2(str(pointer), str(index_pointer(history / name)))
                except (OSError, ValueError, KeyError):
                    pass  # JSON is always a self-contained fallback
    # Stale evidence can never make a failed new attempt appear successful.
    for name in ('raw.jsonl', 'clocktree.json', 'resettree.json', 'summary.json'):
        source = out / name
        if source.exists():
            source.unlink()
        pointer = index_pointer(source)
        if pointer.is_file():
            pointer.unlink()
    for name in ('memory.json', 'memory-restarts.jsonl', 'module-memory-checks.jsonl'):
        source = out / name
        if source.is_file():
            source.unlink()
    work = out / '.clocktree-runs' / identifier
    work.mkdir(parents=True)
    return work


def extract(config_path, output_dir, memory_limit_gib=None):
    config = load_config(config_path)
    if memory_limit_gib is not None:
        config['memory_limit_gib'] = memory_limit_gib
        validate_config(config)
    environment = os.environ.copy()
    executable, verdi_home = resolve_verdi(config['design'], environment)
    environment['VERDI_HOME'] = verdi_home
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    recorded_work = prepare_output(out)
    work = vendor_work_dir(recorded_work)
    if work != recorded_work:
        (recorded_work / 'runtime-location.json').write_text(json.dumps({'working_directory': str(work)}), encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    runtime_config = json.loads(json.dumps(config))
    for key in ('elab', 'filelist'):
        source = runtime_config['design'].get(key)
        if source and not vendor_safe_path(source):
            alias = work / ('input-kdb.elab++' if key == 'elab' else 'input.f')
            alias.symlink_to(source, target_is_directory=key == 'elab')
            runtime_config['design'][key] = str(alias)
    raw_path = out / 'raw.jsonl' if vendor_safe_path(out) else work / 'raw.jsonl'
    write_tcl_config(runtime_config, work / 'rules.tcl', str(raw_path))
    shutil.copy2(str(work / 'rules.tcl'), str(out / 'rules.tcl'))
    # A relative -play path also avoids legacy Verdi path decoding on installs
    # whose source directory contains Unicode characters or spaces.
    shutil.copy2(str(root / 'npi/extract.tcl'), str(work / 'clocktree-extract.tcl'))
    (out / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    environment["CT7_CONFIG"] = str(work / "rules.tcl")
    command = [executable, "-batch", "-nologo", "-play", 'clocktree-extract.tcl']
    started = time.time()
    try:
        exit_code, final_work, memory_report = run_verdi(command, work, environment, raw_path, out / 'verdi.log', out, config)
    finally:
        if raw_path != out / 'raw.jsonl' and raw_path.is_file():
            shutil.copy2(str(raw_path), str(out / 'raw.jsonl'))
    if not (out / "raw.jsonl").exists():
        raise RuntimeError("Verdi produced no raw evidence; inspect " + str(out / "verdi.log"))
    print('CT7_BUILD_BEGIN', flush=True)
    store = new_store(out)
    graph = build_graph(read_events(out / "raw.jsonl"), config, evidence_store=store)
    compiler_log = final_work / "verdiLog" / "compiler.log"
    compiler_errors = []
    compiler_error_count = 0
    if compiler_log.exists():
        with compiler_log.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if re.search(r"\*Error\*|Error-\[", line):
                    compiler_error_count += 1
                    if len(compiler_errors) < 10:
                        compiler_errors.append(line.rstrip())
    graph["metadata"]["compiler_error_count"] = compiler_error_count
    if compiler_error_count:
        graph["diagnostics"].append({"code": "elaboration_error", "count": compiler_error_count, "examples": compiler_errors, "log": str(compiler_log)})
        graph["summary"]["connectivity_complete"] = False
    graph["metadata"].update({"elapsed_seconds": round(time.time() - started, 3), "verdi_exit_code": exit_code,
                              "command": command, "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME"),
                              "output_directory": str(out), "working_directory": str(work),
                              "verdi_executable": executable, "verdi_home": verdi_home})
    graph['metadata'].update(python_version=platform.python_version(), platform=sys.platform)
    graph['metadata']['memory_protection'] = memory_report
    if exit_code:
        graph["summary"]["connectivity_complete"] = False
        graph["diagnostics"].append({"code": "verdi_exit", "exit_code": exit_code})
    graph["summary"]["diagnostics"] = len(graph["diagnostics"])
    graph['metadata']['storage'] = 'sqlite-index / shared-paths / raw-protocol-2'
    print('CT7_INDEX_BEGIN', flush=True)
    indexed = index_graph(graph, store)
    del graph
    print('CT7_JSON_BEGIN', flush=True)
    write_graph(out / result_filename(config), indexed)
    publish_index(out / result_filename(config), indexed)
    graph = indexed
    (out / "summary.json").write_text(json.dumps(graph["summary"], indent=2), encoding="utf-8")
    store.close()
    print('CT7_RESULT_READY', flush=True)
    return graph
