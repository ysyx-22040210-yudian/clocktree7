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
from .runtime import RunJournal


RESULT_FILES = ('rules.tcl', 'config.json', 'raw.jsonl', 'verdi.log', 'clocktree.json', 'resettree.json', 'summary.json', 'memory.json', 'memory-restarts.jsonl', 'module-memory-checks.jsonl', 'trace-timeouts.jsonl')


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


def prepare_output(out, identifier=None, work=None):
    """Retain previous evidence and use fresh Verdi scratch space on each run."""
    identifier = identifier or time.strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex
    if any((out / name).is_file() for name in ('raw.jsonl', 'verdi.log', 'clocktree.json', 'resettree.json', 'summary.json')):
        history = out / '.clocktree-history' / identifier
        history.mkdir(parents=True, exist_ok=True)
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
    for name in ('memory.json', 'memory-restarts.jsonl', 'module-memory-checks.jsonl', 'trace-timeouts.jsonl'):
        source = out / name
        if source.is_file():
            source.unlink()
    work = Path(work) if work is not None else out / '.clocktree-runs' / identifier
    work.mkdir(parents=True,exist_ok=True)
    return work


def extract(config_path, output_dir, memory_limit_gib=None):
    from .recovery import RunLease
    with RunLease(output_dir):
        return _new_extraction(config_path, output_dir, memory_limit_gib)


def finish_extraction(graph, journal):
    failure=journal.state().get('last_failure') or {}
    if graph.get('metadata',{}).get('verdi_exit_code') or graph.get('metadata',{}).get('memory_protection',{}).get('state')=='failed':
        journal.emit('failed',failure.get('stage','worker'),'Verdi 异常退出；已保留可用的部分结果',
                     context=failure.get('context',{}),returncode=graph['metadata']['verdi_exit_code'],
                     exit_signal=failure.get('exit_signal'),partial_result=True)
    else:
        journal.emit('finished' if graph['summary']['connectivity_complete'] else 'attention','export',
                     '后端提取结束' if graph['summary']['connectivity_complete'] else '结果已写出，存在未解析或诊断',
                     context={'instances':graph['summary']['instances'],'edges':graph['summary']['edges']})


def _new_extraction(config_path, output_dir, memory_limit_gib):
    journal=RunJournal.start(output_dir)
    try:
        graph=_extract(config_path,output_dir,memory_limit_gib,journal)
        finish_extraction(graph, journal)
        return graph
    except BaseException as error:
        journal.fail(error,cancelled=isinstance(error,(KeyboardInterrupt,SystemExit)))
        raise


def _extract(config_path, output_dir, memory_limit_gib, journal, recovery=None):
    journal.emit('progress','config','读取并校验配置',{'config_file':str(Path(config_path).resolve()) if config_path else '原任务配置快照'})
    config = json.loads(json.dumps(recovery['config'])) if recovery else load_config(config_path)
    if memory_limit_gib is not None:
        config['memory_limit_gib'] = memory_limit_gib
        validate_config(config)
    environment = os.environ.copy()
    executable, verdi_home = resolve_verdi(config['design'], environment)
    journal.emit('end','config','配置与 Verdi 环境检查完成',{'tree_type':config.get('tree_type','clock'),
                 'top':config['design']['top'],'verdi_executable':executable})
    environment['VERDI_HOME'] = verdi_home
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    recorded_work = journal.directory
    work = Path(recovery['work']) if recovery else vendor_work_dir(prepare_output(out,identifier=journal.directory.name,work=recorded_work))
    if work != recorded_work:
        (recorded_work / 'runtime-location.json').write_text(json.dumps({'working_directory': str(work)}), encoding='utf-8')
    root = Path(__file__).resolve().parents[1]
    runtime_config = json.loads(json.dumps(config))
    for key in ('elab', 'filelist'):
        source = runtime_config['design'].get(key)
        if source and not vendor_safe_path(source):
            alias = work / ('input-kdb.elab++' if key == 'elab' else 'input.f')
            if not alias.exists():alias.symlink_to(source, target_is_directory=key == 'elab')
            runtime_config['design'][key] = str(alias)
    # The authoritative raw prefix belongs to this logical job, even when the
    # user starts another fresh task in the same output directory.
    raw_path = Path(recovery['raw']) if recovery else work / 'raw.jsonl'
    write_tcl_config(runtime_config, work / 'rules.tcl', str(raw_path))
    shutil.copy2(str(work / 'rules.tcl'), str(out / 'rules.tcl'))
    # A relative -play path also avoids legacy Verdi path decoding on installs
    # whose source directory contains Unicode characters or spaces.
    if not recovery:
        shutil.copy2(str(root / 'npi/extract.tcl'), str(work / 'clocktree-extract.tcl'))
        shutil.copy2(str(root / 'npi/target_trace.tcl'), str(work / 'target_trace.tcl'))
        shutil.copy2(str(root / 'npi/input_ports.tcl'), str(work / 'input_ports.tcl'))
        from .recovery import prepare_recovery
        journal.emit('progress','config','记录 RTL/elab 输入身份与任务恢复信息')
        prepare_recovery(journal, out, work, raw_path, config, executable)
    journal.emit('end','config','运行配置与恢复记录已就绪')
    (out / "config.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    environment["CT7_CONFIG"] = str(work / "rules.tcl")
    command = [executable, "-batch", "-nologo", "-play", 'clocktree-extract.tcl']
    started = time.time()
    try:
        arguments = {'resume': recovery} if recovery else {}
        exit_code, final_work, memory_report = run_verdi(command, work, environment, raw_path, out / 'verdi.log', out, config, **arguments)
    finally:
        if raw_path != out / 'raw.jsonl' and raw_path.is_file():
            shutil.copy2(str(raw_path), str(out / 'raw.jsonl'))
    if not (out / "raw.jsonl").exists():
        raise RuntimeError("Verdi produced no raw evidence; inspect " + str(out / "verdi.log"))
    print('CT7_BUILD_BEGIN', flush=True)
    journal.emit('begin','topology','逐事件构建完整拓扑',{'raw':str(out/'raw.jsonl')})
    store = new_store(out)
    failed_worker = memory_report.get('state') == 'failed'
    limit = memory_report.get('last_checkpoint',{}).get('raw_offset',0) if failed_worker else None
    graph = build_graph(read_events(out / "raw.jsonl", limit=limit), config, evidence_store=store)
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
    graph['metadata']['runtime'] = dict(journal.paths(), recovery_count=journal.state().get('recovery_count',0))
    if exit_code or failed_worker:
        graph["summary"]["connectivity_complete"] = False
        graph["diagnostics"].append({"code": "verdi_exit", "exit_code": exit_code})
    graph["summary"]["diagnostics"] = len(graph["diagnostics"])
    graph['metadata']['storage'] = 'sqlite-index / shared-paths / raw-protocol-2'
    journal.emit('end','topology','拓扑构建与诊断完成',{'instances':len(graph['nodes']),'edges':len(graph['edges'])})
    print('CT7_INDEX_BEGIN', flush=True)
    journal.emit('begin','index','写入磁盘索引',{'database':str(store.path)})
    indexed = index_graph(graph, store)
    del graph
    journal.emit('end','index','磁盘索引已提交')
    print('CT7_JSON_BEGIN', flush=True)
    journal.emit('begin','export','流式写出结果 JSON',{'result':str(out/result_filename(config))})
    write_graph(out / result_filename(config), indexed)
    publish_index(out / result_filename(config), indexed)
    graph = indexed
    (out / "summary.json").write_text(json.dumps(graph["summary"], indent=2), encoding="utf-8")
    store.close()
    journal.emit('end','export','结果、索引和摘要已写出',{'result':str(out/result_filename(config))})
    print('CT7_RESULT_READY', flush=True)
    return graph
