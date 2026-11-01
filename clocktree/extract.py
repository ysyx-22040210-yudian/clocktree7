import json
import os
from pathlib import Path
import subprocess
import time
import re

from .config import load_config, write_tcl_config
from .graph import build_graph, read_events


def extract(config_path, output_dir):
    config = load_config(config_path)
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "raw.jsonl").exists():
        raise ValueError("Use a new output directory; previous raw evidence will not be overwritten")
    root = Path(__file__).resolve().parents[1]
    write_tcl_config(config, out / "rules.tcl", str(out / "raw.jsonl"))
    (out / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    environment = os.environ.copy()
    verdi_home = config["design"].get("verdi_home", environment.get("VERDI_HOME", ""))
    if verdi_home:
        environment["VERDI_HOME"] = verdi_home
    environment["CT7_CONFIG"] = str(out / "rules.tcl")
    executable = str(Path(verdi_home) / "bin" / "verdi") if verdi_home else "verdi"
    command = [executable, "-batch", "-nologo", "-play", str(root / "npi" / "extract.tcl")]
    started = time.time()
    with (out / "verdi.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, cwd=str(out), env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            exit_code = process.wait(timeout=int(config.get("timeout_seconds", 1800)))
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise RuntimeError("Verdi extraction timed out; inspect " + str(out / "verdi.log"))
    if not (out / "raw.jsonl").exists():
        raise RuntimeError("Verdi produced no raw evidence; inspect " + str(out / "verdi.log"))
    graph = build_graph(read_events(out / "raw.jsonl"), config)
    compiler_log = out / "verdiLog" / "compiler.log"
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
                              "output_directory": str(out)})
    if exit_code:
        graph["summary"]["connectivity_complete"] = False
        graph["diagnostics"].append({"code": "verdi_exit", "exit_code": exit_code})
    graph["summary"]["diagnostics"] = len(graph["diagnostics"])
    (out / "clocktree.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(graph["summary"], indent=2), encoding="utf-8")
    return graph
