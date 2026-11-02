import json
import os
from pathlib import Path
import subprocess
import time
import re
import shutil
import uuid

from .config import load_config, write_tcl_config
from .graph import build_graph, read_events
from .environment import resolve_verdi


RESULT_FILES = ('rules.tcl', 'config.json', 'raw.jsonl', 'verdi.log', 'clocktree.json', 'summary.json')


def prepare_output(out):
    """Retain previous evidence and use fresh Verdi scratch space on each run."""
    identifier = time.strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex
    if any((out / name).is_file() for name in ('raw.jsonl', 'verdi.log', 'clocktree.json', 'summary.json')):
        history = out / '.clocktree-history' / identifier
        history.mkdir(parents=True)
        for name in RESULT_FILES:
            source = out / name
            if source.is_file():
                shutil.copy2(str(source), str(history / name))
    # Stale evidence can never make a failed new attempt appear successful.
    for name in ('raw.jsonl', 'clocktree.json', 'summary.json'):
        source = out / name
        if source.exists():
            source.unlink()
    work = out / '.clocktree-runs' / identifier
    work.mkdir(parents=True)
    return work


def extract(config_path, output_dir):
    config = load_config(config_path)
    environment = os.environ.copy()
    executable, verdi_home = resolve_verdi(config['design'], environment)
    environment['VERDI_HOME'] = verdi_home
    for key in ('elab', 'filelist'):
        if config['design'].get(key):
            config['design'][key] = str(Path(config['design'][key]).expanduser().resolve())
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    work = prepare_output(out)
    root = Path(__file__).resolve().parents[1]
    write_tcl_config(config, out / "rules.tcl", str(out / "raw.jsonl"))
    (out / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    environment["CT7_CONFIG"] = str(out / "rules.tcl")
    command = [executable, "-batch", "-nologo", "-play", str(root / "npi" / "extract.tcl")]
    started = time.time()
    with (out / "verdi.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=str(work), env=environment, stdout=log, stderr=subprocess.STDOUT)
        exit_code = process.wait()
    if not (out / "raw.jsonl").exists():
        raise RuntimeError("Verdi produced no raw evidence; inspect " + str(out / "verdi.log"))
    graph = build_graph(read_events(out / "raw.jsonl"), config)
    compiler_log = work / "verdiLog" / "compiler.log"
    compiler_errors = []
    if compiler_log.exists():
        compiler_errors = [line for line in compiler_log.read_text(encoding="utf-8", errors="replace").splitlines()
                           if re.search(r"\*Error\*|Error-\[", line)]
    graph["metadata"]["compiler_error_count"] = len(compiler_errors)
    if compiler_errors:
        graph["diagnostics"].append({"code": "elaboration_error", "count": len(compiler_errors), "examples": compiler_errors[:10], "log": str(compiler_log)})
        graph["summary"]["connectivity_complete"] = False
    graph["metadata"].update({"elapsed_seconds": round(time.time() - started, 3), "verdi_exit_code": exit_code,
                              "command": command, "host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME"),
                              "output_directory": str(out), "working_directory": str(work),
                              "verdi_executable": executable, "verdi_home": verdi_home})
    if exit_code:
        graph["summary"]["connectivity_complete"] = False
        graph["diagnostics"].append({"code": "verdi_exit", "exit_code": exit_code})
    graph["summary"]["diagnostics"] = len(graph["diagnostics"])
    (out / "clocktree.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(graph["summary"], indent=2), encoding="utf-8")
    return graph
