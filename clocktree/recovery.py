"""Persistent job recovery. Only committed evidence can cross controller lifetimes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import sys
import time

from .runtime import RunJournal, alive, diagnose, read_json, process_identity
from .storage import write_graph


class RecoveryError(RuntimeError):
    pass


class RunLease:
    """OS-released lock: a crashed process cannot leave a stale lock owner."""
    def __init__(self, output):
        self.output = Path(output).resolve()
        self.stream = None

    def __enter__(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.stream = (self.output / '.clocktree.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0, 2)
                if not self.stream.tell():
                    self.stream.write(b'0'); self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RecoveryError('该运行目录已有提取或恢复进程，不能同时写入：' + str(self.output))
        return self

    def __exit__(self, *args):
        if os.name == 'nt':
            import msvcrt
            self.stream.seek(0); msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def config_signature(config):
    return hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def engine_signature():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name in ('npi/extract.tcl', 'npi/target_trace.tcl', 'npi/input_ports.tcl', 'clocktree/config.py', 'clocktree/extract.py',
                 'clocktree/memory.py', 'clocktree/trace_timeout.py', 'clocktree/trace_channel.py', 'clocktree/graph.py', 'clocktree/networks.py', 'clocktree/storage.py', 'clocktree/recovery.py',
                 'clocktree/worker_launch.py', 'clocktree/runtime.py', 'clocktree/profiles.py', 'clocktree/devices.py', 'clocktree/auxiliary.py', 'clocktree/connectivity.py'):
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def input_snapshot(config):
    """Stream file metadata; never load or hash a large elaboration database.

    File lists, included file lists, RTL, include/library directories and literal
    RTL includes are covered. Unsupported indirect syntax disables recovery,
    not extraction. Metadata is a change guard, not an adversarial checksum.
    """
    digest = hashlib.sha256()
    seen, count = set(), [0]

    def record(path):
        path = Path(path).resolve()
        stat = path.stat()
        digest.update(json.dumps([str(path), stat.st_size, stat.st_mtime_ns],
                                 ensure_ascii=False).encode('utf-8'))
        count[0] += 1
        return path

    def tree(path):
        path = Path(path).resolve()
        if path in seen:return
        seen.add(path)
        if not path.is_dir():record(path); return
        # Directory mtimes are intentionally omitted; record sorted file names,
        # sizes and mtimes so added/deleted files also invalidate the snapshot.
        for base, dirs, files in os.walk(str(path), followlinks=True):
            dirs.sort(); files.sort()
            real = Path(base).resolve()
            if real != path and real in seen:
                dirs[:] = []; continue
            seen.add(real)
            digest.update(str(real).encode('utf-8'))
            for name in files:record(Path(base) / name)

    def expanded(value, base):
        value = os.path.expandvars(os.path.expanduser(value))
        if '$' in value or '`' in value:raise ValueError('未展开的环境变量或动态文件路径：' + value)
        path = Path(value)
        return path if path.is_absolute() else base / path

    def rtl(path):
        path = Path(path).resolve()
        if path in seen:return
        seen.add(path); record(path)
        with path.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                match = re.match(r'\s*`include\s+(.+)', line)
                if not match:continue
                literal = re.match(r'"([^"\n]+)"', match.group(1))
                if not literal:raise ValueError('动态 `include 无法校验：' + str(path))
                candidate = expanded(literal.group(1), path.parent)
                if candidate.is_file():rtl(candidate)
                # +incdir directories are fingerprinted in full below.

    def filelist(path, base=None):
        path = Path(path).resolve()
        if path in seen:return
        seen.add(path); record(path)
        base = base or path.parent
        content = re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
        content = re.sub(r'(?m)//.*$', '', content)
        words = iter(shlex.split(content, comments=True, posix=True))
        for word in words:
            if word in ('-f', '-F'):
                child = expanded(next(words), base)
                filelist(child, child.parent if word == '-F' else base)
            elif word in ('-v', '-y', '-I'):
                child = expanded(next(words), base)
                (rtl if word == '-v' else tree)(child)
            elif word.startswith('+incdir+'):
                for value in word[len('+incdir+'):].split('+'):
                    if value:tree(expanded(value, base))
            elif word.startswith('-I') and len(word) > 2:tree(expanded(word[2:], base))
            elif word.startswith(('+define+', '+libext+')) or word in ('-sv', '-sverilog', '-v2001'):
                continue
            elif word.startswith(('-', '+')):
                raise ValueError('文件列表选项尚不支持输入变更校验：' + word)
            else:rtl(expanded(word, base))

    try:
        design = config['design']
        if design.get('elab'):tree(design['elab'])
        else:filelist(design['filelist'])
        return {'valid': True, 'method': 'paths_size_mtime_ns', 'sha256': digest.hexdigest(), 'files': count[0]}
    except (OSError, ValueError, StopIteration, RuntimeError) as error:
        return {'valid': False, 'error': str(error), 'files': count[0]}


def prepare_recovery(journal, output, work, raw, config, executable):
    from .memory import _initial_checkpoint
    manifest = {'version': 1, 'run_id': journal.directory.name, 'output': str(Path(output).resolve()),
                'journal': str(journal.directory), 'work': str(work), 'raw': str(raw),
                'config': copy.deepcopy(config), 'signature': config_signature(config),
                'engine': engine_signature(), 'inputs': input_snapshot(config),
                'host': socket.gethostname(), 'platform': sys.platform,
                'verdi': str(Path(executable).resolve()),
                'verdi_stat': [Path(executable).stat().st_size, Path(executable).stat().st_mtime_ns],
                'created_at': time.time()}
    write_graph(work / 'checkpoint.json', _initial_checkpoint(manifest['signature']))
    write_graph(journal.directory / 'recovery.json', manifest)
    return manifest


def locate(directory):
    directory = Path(directory).resolve()
    if (directory / 'recovery.json').is_file():journal = directory
    else:
        state = read_json(directory / 'run-status.json', {})
        journal = Path(state.get('journal_directory', directory))
    manifest = read_json(journal / 'recovery.json')
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise RecoveryError('该任务没有跨次启动恢复记录（旧版任务或尚未完成准备），请保留日志后新建提取。')
    if Path(manifest['journal']).resolve() != journal.resolve():
        raise RecoveryError('运行目录已迁移，不能自动恢复原路径下的断点。')
    if config_signature(manifest['config']) != manifest.get('signature'):
        raise RecoveryError('保存的任务配置已改变，不能与旧断点混用。')
    return manifest


def cause_text(report):
    if report.get('state') in ('running','restarting') and report.get('owner_alive') is True:
        return '原提取控制进程仍在运行，可观察现有任务；此时不会重复启动 Verdi。'
    if report.get('state') in ('finished','attention'):
        return '后端已完成并保存结果，可直接打开；无需重新提取。'
    failure = report.get('last_failure') or {}
    cause = report.get('cause')
    if cause == 'memory_protection_stalled':return '内存保护连续重试仍无法推进；可调整资源或内存阈值后继续。'
    if cause == 'memory_protection':return '上次停在内存保护轮换中；已提交证据仍可恢复。'
    if cause == 'port_trace_timeout':return '上次停在端口 trace 超时后的重启中；恢复后跳过超时例化的剩余端口，其他例化继续。'
    if failure.get('exit_signal'):
        return 'Verdi 被 {} 终止；日志未证明具体来源，需结合调度器/系统日志判断。'.format(failure['exit_signal'])
    if failure.get('returncode') is not None:
        return 'Verdi 退出码 {}：{}'.format(failure['returncode'], failure.get('detail', '查看 Verdi 日志'))
    if failure:return '{}：{}'.format(failure.get('error_type', '运行错误'), failure.get('detail', '查看阶段日志'))
    if report.get('state') == 'cancelled':return '上次任务被取消；保留了已提交断点。'
    return '进程已退出且没有正常结束记录；具体原因未知，不能仅据此判定内存不足。'


def inspect_recovery(directory):
    """Small read-only status used on GUI startup; defer input scans until resume."""
    report = diagnose(directory)
    result = {'directory': str(Path(directory).resolve()), 'diagnosis': report,
              'reason': cause_text(report), 'resumable': False}
    try:
        manifest = locate(directory)
        result.update(manifest=manifest, directory=manifest['output'], journal=manifest['journal'])
        state = read_json(Path(manifest['journal']) / 'run-status.json', {})
        if report.get('state') in ('finished', 'attention'):
            result['blocked'] = '任务已完成，可打开已有结果。'; return result
        if state.get('state') in ('running', 'restarting') and report.get('owner_alive') is not False:
            result['blocked'] = '原后端仍在运行或无法确认已退出；继续观察，避免重复启动。'
            result['running'] = report.get('owner_alive') is True
            return result
        if manifest['host'] != socket.gethostname() or manifest['platform'] != sys.platform:
            raise RecoveryError('断点来自另一台设备，请在原设备、原共享路径下恢复。')
        if not manifest.get('inputs', {}).get('valid'):
            raise RecoveryError('原输入无法校验，不能安全续跑：' + manifest.get('inputs', {}).get('error', '缺少输入快照'))
        from .memory import read_checkpoint
        work = Path(manifest['work'])
        checkpoint = read_checkpoint(work / 'checkpoint.json', manifest['signature'],
                                     Path(manifest['raw']), work / 'targets.tcl', manifest['config'])
        decisions = read_json(work/'trace-timeouts.json', {})
        if decisions.get('signature') == manifest['signature']:
            pending = [record for record in decisions.get('instances', {}).values()
                       if record['trace_id'] > checkpoint['completed_traces']]
            if pending:
                result['pending_trace_timeouts'] = pending
                result['reason'] += '\n已保存 {} 个例化的 trace 超时决定；继续后会跳过它们剩余的端口。'.format(len(pending))
        result.update(resumable=True, checkpoint=checkpoint,
                      next_step='重建拓扑、索引与结果（不再启动 Verdi）' if checkpoint['stage'] == 'complete'
                      else '重新导入 elab、恢复句柄后继续未提交的查找/追踪')
    except (RecoveryError, RuntimeError, OSError, KeyError, ValueError, TypeError) as error:
        result['blocked'] = str(error)
    return result


def verify_inputs(manifest, executable=None):
    if manifest['engine'] != engine_signature():
        raise RecoveryError('提取实现版本已改变；请使用原版本恢复，或新建任务，避免混用证据。')
    if executable and manifest['verdi'] != str(Path(executable).resolve()):
        raise RecoveryError('Verdi 安装已改变；请恢复原运行环境后续跑。')
    if executable and manifest.get('verdi_stat') != [Path(executable).stat().st_size, Path(executable).stat().st_mtime_ns]:
        raise RecoveryError('Verdi 启动程序已改变；请恢复原版本后续跑。')
    snapshot = input_snapshot(manifest['config'])
    if not snapshot.get('valid') or snapshot != manifest['inputs']:
        raise RecoveryError('RTL/elab 输入已改变或不可访问，不能继续旧断点。请恢复原输入，或新建提取。')


def stop_orphan_worker(state):
    """Stop only the recorded Linux worker group, after its controller is gone."""
    identity = state.get('worker') or {}
    if not identity.get('pid'):return []
    if sys.platform != 'linux' or identity.get('host') != socket.gethostname():
        raise RecoveryError('无法在当前设备核对原 Verdi 进程，请在原设备恢复。')
    current = process_identity()
    if identity.get('boot_id') and identity['boot_id'] != current.get('boot_id'):return []
    pid = identity['pid']
    live = alive(identity)
    if live is None:raise RecoveryError('无法核对原 Verdi 进程身份。')
    members = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pid and fields[0] != 'Z':
                if int(fields[3]) != pid or int(fields[19]) < int(identity.get('start_ticks', '0')):
                    raise RecoveryError('原进程组身份不一致，拒绝自动终止。')
                members.append(int(path.name))
        except (OSError, ValueError, IndexError):continue
    if not members:return []
    if not identity.get('start_ticks') or (Path('/proc/{}'.format(pid)).exists() and not live):
        # A zombie original leader is harmless; a reused live PID is not.
        now = process_identity(pid)
        if now.get('start_ticks') != identity.get('start_ticks'):
            raise RecoveryError('原 Verdi PID 已被复用，拒绝自动终止。')
    identities = [process_identity(p) for p in members]
    try:os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:return members
    # No runtime timeout; never start a second writer before the old one exits.
    while any(alive(saved) for saved in identities):
        time.sleep(.05)
    return members


def resume(directory, memory_limit_gib=None):
    from .extract import _extract, finish_extraction, prepare_output
    manifest = locate(directory)
    output = Path(manifest['output'])
    with RunLease(output):
        info = inspect_recovery(manifest['journal'])
        if not info['resumable']:raise RecoveryError(info.get('blocked', '断点不可恢复'))
        from .environment import resolve_verdi
        executable, _ = resolve_verdi(manifest['config']['design'])
        verify_inputs(manifest, executable)
        journal = RunJournal(manifest['journal'], mirror=output)
        state = journal.state()
        work = Path(manifest['work'])
        # Also cover a supervisor killed between Popen and its journal update.
        launches = sorted(work.glob('attempt-*/worker-identity.json'))
        worker = read_json(launches[-1], state.get('worker', {})) if launches else state.get('worker', {})
        stopped = stop_orphan_worker(dict(state, worker=worker))
        # The worker could advance after the startup preview; use the newest
        # checkpoint only after the old group is confirmed stopped.
        from .memory import read_checkpoint, rollback_files
        checkpoint = read_checkpoint(work / 'checkpoint.json', manifest['signature'],
                                     Path(manifest['raw']), work / 'targets.tcl', manifest['config'])
        write_graph(journal.directory / ('interruption-{:04d}.json'.format(state.get('recovery_count', 0)+1)),
                    dict(diagnosis=info['diagnosis'], checkpoint=checkpoint, stopped_orphans=stopped))
        rollback_files(checkpoint, Path(manifest['raw']), work / 'targets.tcl', work / 'module-checkpoints')
        # Archive any previous/partial published result before reusing this
        # directory. The authoritative per-job raw prefix is never archived away.
        previous = read_json(output / 'run-status.json', {})
        if previous.get('run_id') != state.get('run_id'):
            import shutil
            history = output / '.clocktree-history' / ('before-resume-' + str(time.time_ns() if hasattr(time,'time_ns') else int(time.time()*1000000)))
            history.mkdir(parents=True, exist_ok=True)
            for name in ('run.log','run-events.jsonl','run-status.json'):
                if (output/name).is_file():shutil.copy2(str(output/name),str(history/name))
            for name in ('run.log','run-events.jsonl'):
                if journal.directory.joinpath(name).is_file():shutil.copy2(str(journal.directory/name),str(output/name))
        prepare_output(output, work=work)
        state.update(owner=process_identity(), state='running', recovery_count=state.get('recovery_count', 0)+1)
        state.pop('last_failure', None)
        write_graph(journal.status_file, state)
        journal.emit('begin', 'config', '从上次已提交断点恢复任务',
                     {'completed_modules': checkpoint['completed_modules'], 'completed_traces': checkpoint['completed_traces'],
                      'stopped_orphans': stopped, 'recovery_count': state['recovery_count']}, checkpoint=checkpoint)
        try:
            graph = _extract(None, output, memory_limit_gib, journal, recovery=manifest)
            finish_extraction(graph, journal)
            return graph
        except BaseException as error:
            journal.fail(error, cancelled=isinstance(error, (KeyboardInterrupt, SystemExit)))
            raise
