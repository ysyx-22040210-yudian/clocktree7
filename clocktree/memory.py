"""Monitor owned EDA processes and resume only durable, complete work units.

No extraction runtime timeout is imposed. A restart rebuilds NPI handles from
names; process-local handles and partial queries are never treated as results.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .config import tcl_string
from .storage import write_graph


GIB = 1024 ** 3
DEFAULT_MEMORY_LIMIT_GIB = 60
MAX_STALLED_ATTEMPTS = 3
CHECKPOINT_VERSION = 1


class MemoryResumeError(RuntimeError):
    pass


def process_memory(pid, proc='/proc'):
    """RSS of the supervisor plus the worker's descendants/process group.

    Shared mappings can be counted more than once. This intentionally reports
    RSS, not an estimate of unique physical pages or a scheduler's billing.
    """
    root = Path(proc)
    records = {}
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            data = (entry / 'stat').read_text()
            fields = data[data.rindex(')')+2:].split()
            records[int(entry.name)] = (int(fields[1]), int(fields[2]), int(fields[21]))
        except (OSError, ValueError, IndexError):
            continue  # a process can exit between enumeration and reading
    members = {pid}
    members.update(key for key, (_, group, _) in records.items() if group == pid)
    while True:
        more = {key for key, (parent, _, _) in records.items() if parent in members}
        if more.issubset(members):
            break
        members.update(more)
    members.add(os.getpid())
    page = os.sysconf('SC_PAGE_SIZE')
    rss = sum(max(0, records.get(key, (0, 0, 0))[2]) for key in members) * page
    return {'rss_bytes': rss, 'processes': len(members & set(records)), 'metric': 'supervisor_and_verdi_tree_rss'}


def _stop_worker(process):
    if process.poll() is None:
        try:
            if sys.platform.startswith('linux'):
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
    # No timeout: reap the owned child before starting a replacement.
    return process.wait()


def _initial_checkpoint(signature):
    return {'version': CHECKPOINT_VERSION, 'signature': signature, 'stage': 'import',
            'completed_queries': 0, 'completed_traces': 0, 'instances': 0,
            'raw_offset': 0, 'targets_offset': 0}


def read_checkpoint(path, signature, raw, targets):
    errors = []
    for candidate in (Path(path), Path(str(path) + '.previous')):
        try:
            data = json.loads(candidate.read_text(encoding='utf-8'))
            if (not isinstance(data, dict) or data.get('version') != CHECKPOINT_VERSION or
                    data.get('signature') != signature or data.get('stage') not in ('import', 'enumerate', 'trace', 'complete')):
                raise ValueError('Checkpoint version/configuration mismatch')
            for key in ('completed_queries', 'completed_traces', 'instances', 'raw_offset', 'targets_offset'):
                if type(data.get(key)) is not int or data[key] < 0:
                    raise ValueError('Invalid checkpoint ' + key)
            for source, key in ((raw, 'raw_offset'), (targets, 'targets_offset')):
                size = source.stat().st_size if source.exists() else 0
                if data[key] > size:
                    raise ValueError('Checkpoint extends beyond ' + source.name)
            return data
        except (OSError, ValueError, TypeError) as error:
            errors.append(str(error))
    raise MemoryResumeError('Cannot safely resume: ' + '; '.join(errors))


def rollback_files(checkpoint, raw, targets):
    """Discard only the uncommitted suffix of this run's two known files."""
    for path, key in ((raw, 'raw_offset'), (targets, 'targets_offset')):
        if path.exists():
            with path.open('r+b') as stream:
                stream.truncate(checkpoint[key])
        elif checkpoint[key]:
            raise MemoryResumeError('Missing committed file: ' + str(path))


def _context(path, checkpoint, signature, raw, targets, control, status, checkpoint_path):
    lines = []
    for key, value in (('checkpoint', checkpoint_path), ('target_file', targets),
                       ('rotate_file', control), ('status_file', status), ('signature', signature)):
        lines.append('set ct_' + key + ' ' + tcl_string(str(value)))
    for key in ('completed_queries', 'completed_traces', 'instances', 'raw_offset', 'targets_offset'):
        lines.append('set ct_resume_' + key + ' ' + str(checkpoint[key]))
    lines.append('set ct_resume_stage ' + tcl_string(checkpoint['stage']))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run_verdi(command, work, environment, raw, log_path, output, config,
              sampler=None, pause=None, popen=None, monitored=None):
    """Launch fresh workers; completed enumeration/trace batches survive rotation."""
    work, raw, log_path, output = map(Path, (work, raw, log_path, output))
    limit_gib = float(config.get('memory_limit_gib', DEFAULT_MEMORY_LIMIT_GIB))
    monitored = sys.platform.startswith('linux') if monitored is None else monitored
    enabled = monitored and limit_gib > 0
    sampler, pause, popen = sampler or process_memory, pause or time.sleep, popen or subprocess.Popen
    signature = hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    checkpoint_path, targets = work / 'checkpoint.json', work / 'targets.tcl'
    control, status = work / 'rotate.request', work / 'worker-status.json'
    checkpoint = _initial_checkpoint(signature)
    write_graph(checkpoint_path, checkpoint)
    report_path = output / 'memory.json'
    report = {'enabled': enabled, 'limit_gib': limit_gib, 'soft_limit_gib': limit_gib*.9,
              'metric': 'supervisor_and_verdi_tree_rss', 'restart_count': 0, 'attempts': 0,
              'scope': 'Verdi worker phase, including extraction controller; excludes GUI and later graph processing',
              'peak_rss_gib': 0., 'current_rss_gib': None, 'state': 'starting',
              'checkpoint_file': str(checkpoint_path), 'restarts': []}
    last_key, stalls, process = None, 0, None
    old_signal = None
    # The Tk job already has its own process group. Workers need separate
    # groups for memory rotation, so forward cancellation to that owned group.
    if monitored and threading.current_thread() is threading.main_thread():
        old_signal = signal.getsignal(signal.SIGTERM)
        def cancelled(signum, frame):
            if process is not None:
                _stop_worker(process)
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, cancelled)
    try:
        with log_path.open('w', encoding='utf-8') as log:
            while True:
                report['attempts'] += 1
                attempt = report['attempts']
                attempt_dir = work / ('attempt-{:04d}'.format(attempt))
                attempt_dir.mkdir()
                import shutil
                shutil.copy2(str(work / 'clocktree-extract.tcl'), str(attempt_dir / 'clocktree-extract.tcl'))
                rollback_files(checkpoint, raw, targets)
                for path in (control, status):
                    if path.exists():
                        path.unlink()
                context = attempt_dir / 'resume.tcl'
                _context(context, checkpoint, signature, raw, targets, control, status, checkpoint_path)
                worker_environment = dict(environment, CT7_RESUME=str(context))
                report.update(state='running', last_checkpoint=checkpoint, current_rss_gib=None)
                write_graph(report_path, report)
                log.write('CT7_WORKER_BEGIN attempt={} queries={} traces={}\n'.format(attempt, checkpoint['completed_queries'], checkpoint['completed_traces']))
                log.flush()
                kwargs = dict(cwd=str(attempt_dir), env=worker_environment, stdout=log, stderr=subprocess.STDOUT)
                if monitored:
                    kwargs['start_new_session'] = True
                process = popen(command, **kwargs)
                report['worker_pid'] = getattr(process, 'pid', None)
                memory_hit, soft_requested = False, False
                if not enabled:
                    code = process.wait()
                else:
                    while process.poll() is None:
                        sample = sampler(process.pid)
                        used = sample['rss_bytes'] / GIB
                        report['current_rss_gib'] = round(used, 3)
                        report['peak_rss_gib'] = round(max(report['peak_rss_gib'], used), 3)
                        report.update(sample)
                        try:
                            report['worker'] = json.loads(status.read_text(encoding='utf-8'))
                        except (OSError, ValueError):
                            pass
                        if used >= limit_gib:
                            memory_hit = True
                            report['state'] = 'restarting'
                            write_graph(report_path, report)
                            log.write('CT7_MEMORY_HARD rss_gib={:.3f} limit_gib={:.3f}\n'.format(used, limit_gib))
                            log.flush()
                            code = _stop_worker(process)
                            break
                        if used >= limit_gib*.9 and not soft_requested:
                            control.write_text('rotate at a complete checkpoint\n', encoding='utf-8')
                            soft_requested = True
                            log.write('CT7_MEMORY_SOFT rss_gib={:.3f}; checkpoint rotation requested\n'.format(used))
                            log.flush()
                        write_graph(report_path, report)
                        pause(1.)
                    else:
                        code = process.wait()
                process = None
                checkpoint = read_checkpoint(checkpoint_path, signature, raw, targets)
                try:
                    worker_status = json.loads(status.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    worker_status = {}
                if checkpoint['stage'] == 'complete':
                    code = 0 if memory_hit else code
                    break
                if not memory_hit and worker_status.get('stage') != 'rotating':
                    break  # an ordinary EDA failure is never a memory restart
                key = tuple(checkpoint[k] for k in ('completed_queries', 'completed_traces', 'instances'))
                stalls = stalls+1 if key == last_key else 1
                last_key = key
                reason = 'hard_limit' if memory_hit else 'checkpoint_rotation'
                event = {'attempt': attempt, 'reason': reason, 'exit_code': code, 'checkpoint': checkpoint,
                         'worker': worker_status, 'rss_gib': report['current_rss_gib']}
                report['restarts'].append(event)
                # Keep the complete audit on disk and only a bounded preview
                # in memory.json / the result metadata.
                with (output / 'memory-restarts.jsonl').open('a', encoding='utf-8') as audit:
                    audit.write(json.dumps(event, ensure_ascii=False) + '\n')
                report['restarts'] = report['restarts'][-32:]
                if stalls >= MAX_STALLED_ATTEMPTS:
                    rollback_files(checkpoint, raw, targets)
                    report.update(state='blocked', last_checkpoint=checkpoint,
                                  error='Memory pressure repeated without checkpoint progress')
                    write_graph(report_path, report)
                    raise MemoryResumeError('Verdi memory protection could not advance after {} attempts at the same checkpoint '
                        '(stage={}, completed module queries={}, completed traces={}). Import/handle restoration or one query '
                        'may already exceed the threshold. Narrow the hierarchy or raise memory capacity; '
                        'completed evidence is retained in {}. No modules were skipped.'.format(stalls, worker_status.get('stage', checkpoint['stage']),
                            checkpoint['completed_queries'], checkpoint['completed_traces'], work))
                report['restart_count'] += 1
                log.write('CT7_MEMORY_RESTART next_attempt={} queries={} traces={}\n'.format(
                    attempt+1, checkpoint['completed_queries'], checkpoint['completed_traces']))
                log.flush()
            report.update(state='finished' if checkpoint['stage'] == 'complete' else 'failed',
                          last_checkpoint=checkpoint, exit_code=code, current_rss_gib=None)
            write_graph(report_path, report)
            return code, attempt_dir, report
    except BaseException:
        if process is not None:
            _stop_worker(process)
        if report.get('state') not in ('blocked', 'finished', 'failed'):
            report['state'] = 'interrupted'
            write_graph(report_path, report)
        raise
    finally:
        if old_signal is not None:
            signal.signal(signal.SIGTERM, old_signal)
