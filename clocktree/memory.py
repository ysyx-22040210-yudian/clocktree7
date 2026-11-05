"""Monitor owned EDA processes and resume only durable, complete work units.

There is no overall runtime timeout. Optional per-port deadlines skip only
the timed-out instance. A restart rebuilds NPI handles from
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
from collections import OrderedDict

from .config import tcl_string
from .storage import write_graph
from .runtime import RunJournal, WorkerEvents, process_identity
from .trace_timeout import TraceTimeouts


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
            'completed_modules': 0, 'last_module': None, 'raw_offset': 0, 'targets_offset': 0}


def _module_groups(config):
    groups = OrderedDict()
    for layer in config.get('layers', []):
        scope = layer['hierarchy'].strip().rstrip('./') or config.get('trace_scope', '').strip().rstrip('./') or config.get('design', {}).get('top', '')
        for rule in layer.get('modules', []):
            groups.setdefault((scope, rule['module']), []).append(rule)
    return list(groups.values())


def read_checkpoint(path, signature, raw, targets, config=None):
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
            groups = _module_groups(config or {})
            # Older checkpoints committed entire groups. Infer their module
            # cursor from that prefix without treating unfinished rules as done.
            if 'completed_modules' not in data:
                data['completed_modules'] = sum(len(group) for group in groups[:data['completed_queries']])
            data.setdefault('last_module', None)
            modules = data['completed_modules']
            if type(modules) is not int or modules < 0:
                raise ValueError('Invalid completed_modules')
            last = data['last_module']
            if last is not None and (not isinstance(last, dict) or type(last.get('index')) is not int or last['index'] != modules):
                raise ValueError('Invalid last_module checkpoint')
            if groups:
                queries = data['completed_queries']
                if queries > len(groups):
                    raise ValueError('Checkpoint exceeds configured queries')
                lower = sum(len(group) for group in groups[:queries])
                upper = lower + (len(groups[queries]) if queries < len(groups) else 0)
                if not (lower <= modules < upper if queries < len(groups) else modules == lower):
                    raise ValueError('Module cursor does not match the completed query prefix')
            for source, key in ((raw, 'raw_offset'), (targets, 'targets_offset')):
                size = source.stat().st_size if source.exists() else 0
                if data[key] > size:
                    raise ValueError('Checkpoint extends beyond ' + source.name)
            return data
        except (OSError, ValueError, TypeError) as error:
            errors.append(str(error))
    raise MemoryResumeError('Cannot safely resume: ' + '; '.join(errors))


def rollback_files(checkpoint, raw, targets, module_directory=None):
    """Discard only the uncommitted suffix of this run's two known files."""
    for path, key in ((raw, 'raw_offset'), (targets, 'targets_offset')):
        if path.exists():
            with path.open('r+b') as stream:
                stream.truncate(checkpoint[key])
        elif checkpoint[key]:
            raise MemoryResumeError('Missing committed file: ' + str(path))
    if module_directory is not None:
        # A snapshot published just before a killed latest-checkpoint update
        # is an uncommitted candidate. Remove only our numbered suffix files.
        for path in Path(module_directory).glob('*.json'):
            if path.stem.isdigit() and len(path.stem) >= 8 and int(path.stem) > checkpoint.get('completed_modules', 0):
                path.unlink()


def _context(path, checkpoint, signature, raw, targets, control, status, checkpoint_path, enabled, timeouts=None):
    lines = []
    for key, value in (('checkpoint', checkpoint_path), ('target_file', targets),
                       ('rotate_file', control), ('status_file', status), ('signature', signature)):
        lines.append('set ct_' + key + ' ' + tcl_string(str(value)))
    for key, value in (('module_checkpoint_dir', checkpoint_path.parent / 'module-checkpoints'),
                       ('module_check_request', checkpoint_path.parent / 'module-check.request'),
                       ('module_check_ack', checkpoint_path.parent / 'module-check.ack')):
        lines.append('set ct_' + key + ' ' + tcl_string(str(value)))
    lines.extend(['set ct_module_check_enabled ' + str(int(enabled)), 'set ct_controller_pid ' + str(os.getpid()),
                  'set ct_resume_last_module ' + tcl_string(json.dumps(checkpoint.get('last_module'), ensure_ascii=False))])
    for key in ('completed_queries', 'completed_traces', 'completed_modules', 'instances', 'raw_offset', 'targets_offset'):
        lines.append('set ct_resume_' + key + ' ' + str(checkpoint[key]))
    lines.append('set ct_resume_stage ' + tcl_string(checkpoint['stage']))
    lines.append('set ct_timeout_instances [dict create]')
    if timeouts is not None:
        lines.append('set ct_trace_transport '+tcl_string(timeouts.transport))
        if timeouts.channel:
            lines += ['set ct_trace_monitor_port '+str(timeouts.channel.port),
                      'set ct_trace_monitor_token '+tcl_string(timeouts.channel.token)]
        lines += ['set ct_trace_current_file '+tcl_string(str(timeouts.status)),
                  'set ct_trace_ack_file '+tcl_string(str(timeouts.ack))]
        for owner, record in timeouts.records.items():
            lines.append('dict set ct_timeout_instances {} [list {} {}]'.format(tcl_string(owner),
                record['trace_id'], tcl_string(json.dumps(record,ensure_ascii=False,separators=(',',':')))))
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def run_verdi(command, work, environment, raw, log_path, output, config,
              sampler=None, pause=None, popen=None, monitored=None, resume=None):
    """Launch fresh workers; completed enumeration/trace batches survive rotation."""
    work, raw, log_path, output = map(Path, (work, raw, log_path, output))
    record_stages=(output/'run-status.json').exists()
    journal=RunJournal.attach(output)
    limit_gib = float(config.get('memory_limit_gib', DEFAULT_MEMORY_LIMIT_GIB))
    monitored = sys.platform.startswith('linux') if monitored is None else monitored
    enabled = monitored and limit_gib > 0
    native_launch = popen is None and sys.platform.startswith('linux')
    sampler, popen = sampler or process_memory, popen or subprocess.Popen
    signature = resume['signature'] if resume else hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    transport = environment.get('CLOCKTREE_TRACE_TRANSPORT', 'socket') if native_launch else 'file'
    if transport not in ('socket','file'):raise ValueError('CLOCKTREE_TRACE_TRANSPORT must be socket or file')
    timeouts = TraceTimeouts(work, signature, config.get('port_trace_timeout_s', 0), resume=bool(resume),transport=transport)
    pause = pause or timeouts.wait
    poll_interval = .1 if timeouts.channel or not timeouts.seconds else .01
    if resume and timeouts.records:
        with (output/'trace-timeouts.jsonl').open('w',encoding='utf-8') as audit:
            for record in sorted(timeouts.records.values(),key=lambda r:r['trace_id']):
                audit.write(json.dumps(record,ensure_ascii=False)+'\n')
            audit.flush();os.fsync(audit.fileno())
    checkpoint_path, targets = work / 'checkpoint.json', work / 'targets.tcl'
    control, status = work / 'rotate.request', work / 'worker-status.json'
    module_directory = work / 'module-checkpoints'
    module_directory.mkdir(exist_ok=True)
    module_request, module_ack = work / 'module-check.request', work / 'module-check.ack'
    checkpoint = read_checkpoint(checkpoint_path, signature, raw, targets, config) if resume else _initial_checkpoint(signature)
    if not resume:write_graph(checkpoint_path, checkpoint)
    report_path = output / 'memory.json'
    report = {'enabled': enabled, 'limit_gib': limit_gib, 'soft_limit_gib': limit_gib*.9,
              'metric': 'supervisor_and_verdi_tree_rss', 'restart_count': 0, 'attempts': 0,
              'scope': 'Verdi worker phase, including extraction controller; excludes GUI and later graph processing',
              'module_checks': 0, 'module_checkpoint_directory': str(module_directory),
              'peak_rss_gib': 0., 'current_rss_gib': None, 'state': 'starting',
              'checkpoint_file': str(checkpoint_path), 'restarts': [],
              'port_trace_timeout_s':timeouts.seconds, 'trace_timeout_count':len(timeouts.records),
              'timeout_restart_count':0, 'trace_timeout_file':str(timeouts.file),
              'trace_monitor_transport':timeouts.transport if timeouts.seconds else 'disabled',
              'trace_monitor_fallback':timeouts.fallback_reason}
    if resume:
        from .runtime import read_json
        previous = read_json(work / 'memory.json', {})
        for key in ('restart_count', 'module_checks', 'peak_rss_gib', 'restarts', 'timeout_restart_count'):
            if key in previous:report[key] = previous[key]
        report['timeout_restart_count'] = max(report['timeout_restart_count'], len(timeouts.records))
        report['attempts'] = max([0]+[int(p.name.split('-')[1]) for p in work.glob('attempt-*')
                                     if p.is_dir() and p.name.split('-')[-1].isdigit()])
        report['recovery_count'] = journal.state().get('recovery_count', 1)
        if checkpoint['stage'] == 'complete':
            timeouts.close()
            report.update(state='finished', last_checkpoint=checkpoint, exit_code=0, reused_complete_evidence=True)
            write_graph(report_path, report); write_graph(work / 'memory.json', report)
            journal.emit('end','trace','已恢复完整原始证据；跳过 Verdi，重建结果',checkpoint=checkpoint)
            return 0, work / ('attempt-{:04d}'.format(report['attempts'])), report
    def save_report():
        report.update(trace_monitor_transport=timeouts.transport if timeouts.seconds else 'disabled',
                      trace_monitor_fallback=timeouts.fallback_reason)
        write_graph(report_path, report)
        write_graph(work / 'memory.json', report)
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
        with log_path.open('a' if resume else 'w', encoding='utf-8') as log:
            while True:
                report['attempts'] += 1
                attempt = report['attempts']
                attempt_dir = work / ('attempt-{:04d}'.format(attempt))
                attempt_dir.mkdir()
                import shutil
                shutil.copy2(str(work / 'clocktree-extract.tcl'), str(attempt_dir / 'clocktree-extract.tcl'))
                if (work / 'target_trace.tcl').is_file():
                    shutil.copy2(str(work / 'target_trace.tcl'), str(attempt_dir / 'target_trace.tcl'))
                rollback_files(checkpoint, raw, targets, module_directory)
                for path in (control, status, module_request, module_ack):
                    if path.exists():
                        path.unlink()
                context = attempt_dir / 'resume.tcl'
                timeouts.reset_attempt()
                _context(context, checkpoint, signature, raw, targets, control, status, checkpoint_path, enabled, timeouts)
                worker_events=attempt_dir/'worker-events.jsonl'
                reader=WorkerEvents(worker_events)
                worker_environment = dict(environment, CT7_RESUME=str(context), CT7_WORKER_EVENTS=str(worker_events))
                journal.emit('begin','worker','启动新的 Verdi 进程',{'completed_modules':checkpoint['completed_modules'],
                    'completed_traces':checkpoint['completed_traces']},attempt=attempt,worker_events=str(worker_events),checkpoint=checkpoint)
                report.update(state='running', last_checkpoint=checkpoint, current_rss_gib=None)
                save_report()
                log.write('CT7_WORKER_BEGIN attempt={} queries={} traces={} modules={}\n'.format(attempt, checkpoint['completed_queries'], checkpoint['completed_traces'], checkpoint['completed_modules']))
                log.flush()
                kwargs = dict(cwd=str(attempt_dir), env=worker_environment, stdout=log, stderr=subprocess.STDOUT)
                if monitored:
                    kwargs['start_new_session'] = True
                launch = ([sys.executable, str(Path(__file__).with_name('worker_launch.py')),
                           str(attempt_dir/'worker-identity.json'), json.dumps(process_identity())] + list(command)) if native_launch else command
                process = popen(launch, **kwargs)
                report['worker_pid'] = getattr(process, 'pid', None)
                worker_identity=process_identity(process.pid) if getattr(process,'pid',None) else {}
                journal.emit('progress','worker','Verdi 已启动',attempt=attempt,worker=worker_identity,
                             worker_events=str(worker_events),durable=True)
                memory_hit, soft_requested, rotation_rss = False, False, None
                timeout_hit = None
                if not enabled and not timeouts.seconds and not (record_stages and monitored):
                    code = process.wait()
                else:
                    next_sample, serviced_module, next_events = 0., 0, 0.
                    while process.poll() is None:
                        # Keep full worker logs on disk; avoid rewriting mirrored
                        # progress snapshots for every fast IPC acknowledgement.
                        if time.monotonic() >= next_events:
                            reader.drain(journal,attempt,worker_identity)
                            next_events = time.monotonic()+.1
                        decision = timeouts.poll()
                        if decision:
                            committed = read_checkpoint(checkpoint_path, signature, raw, targets, config)
                            timeout_hit = timeouts.commit(decision, committed, attempt)
                            report.update(state='restarting', trace_timeout_count=len(timeouts.records), last_trace_timeout=timeout_hit)
                            save_report()
                            journal.emit('progress','trace','端口 trace 超时；跳过当前例化的剩余追踪',
                                         timeout_hit,attempt=attempt,checkpoint=committed,durable=True)
                            with (output/'trace-timeouts.jsonl').open('a',encoding='utf-8') as audit:
                                audit.write(json.dumps(timeout_hit,ensure_ascii=False)+'\n');audit.flush();os.fsync(audit.fileno())
                            log.write('CT7_PORT_TIMEOUT '+json.dumps(timeout_hit,ensure_ascii=False)+'\n');log.flush()
                            code = _stop_worker(process)
                            break
                        if not enabled:
                            pause(poll_interval)
                            continue
                        try:
                            pending_module = int(module_request.read_text(encoding='ascii').strip())
                        except (OSError, ValueError):
                            pending_module = 0
                        boundary = pending_module > 0 and pending_module != serviced_module
                        if not boundary and time.monotonic() < next_sample:
                            pause(poll_interval)
                            continue
                        sample = sampler(process.pid)
                        next_sample = time.monotonic() + 1.
                        used = sample['rss_bytes'] / GIB
                        report['current_rss_gib'] = round(used, 3)
                        report['peak_rss_gib'] = round(max(report['peak_rss_gib'], used), 3)
                        report.update(sample)
                        try:
                            report['worker'] = json.loads(status.read_text(encoding='utf-8'))
                        except (OSError, ValueError):
                            pass
                        if boundary:
                            committed = read_checkpoint(checkpoint_path, signature, raw, targets, config)
                            if committed['completed_modules'] != pending_module:
                                raise MemoryResumeError('Module memory check does not match its durable checkpoint')
                            report['last_checkpoint'] = committed
                            report['module_checks'] += 1
                            check = {'attempt': attempt, 'index': pending_module, 'module': committed['last_module'],
                                     'worker_pid': process.pid, 'sampled_at': time.time(),
                                     'rss_gib': round(used, 3), 'limit_gib': limit_gib,
                                     'action': 'hard_stop' if used >= limit_gib else ('rotate' if soft_requested or control.exists() or used >= limit_gib*.9 else 'continue')}
                            report['last_module_check'] = check
                            journal.emit('checkpoint','modules','module 断点与实时内存检查',
                                         context=check,attempt=attempt,checkpoint=committed)
                            with (output / 'module-memory-checks.jsonl').open('a', encoding='utf-8') as audit:
                                audit.write(json.dumps(check, ensure_ascii=False) + '\n')
                            log.write('CT7_MODULE_MEMORY_CHECK index={} rss_gib={:.3f} limit_gib={:.3f} action={}\n'.format(
                                pending_module, used, limit_gib, check['action']))
                            log.flush()
                        if used >= limit_gib:
                            memory_hit = True
                            rotation_rss = used
                            report['state'] = 'restarting'
                            save_report()
                            log.write('CT7_MEMORY_HARD rss_gib={:.3f} limit_gib={:.3f}\n'.format(used, limit_gib))
                            log.flush()
                            code = _stop_worker(process)
                            break
                        if used >= limit_gib*.9 and not soft_requested:
                            control.write_text('rotate at a complete checkpoint\n', encoding='utf-8')
                            soft_requested = True
                            rotation_rss = used
                            log.write('CT7_MEMORY_SOFT rss_gib={:.3f}; checkpoint rotation requested\n'.format(used))
                            log.flush()
                        if boundary:
                            action = 'rotate' if soft_requested or control.exists() else 'continue'
                            temporary = module_ack.with_name(module_ack.name + '.tmp')
                            temporary.write_text('{} {} {}\n'.format(pending_module, action, sample['rss_bytes']), encoding='ascii')
                            os.replace(str(temporary), str(module_ack))
                            serviced_module = pending_module
                        save_report()
                        pause(poll_interval)
                    else:
                        code = process.wait()
                reader.drain(journal,attempt,worker_identity,all_records=True)
                process = None
                checkpoint = read_checkpoint(checkpoint_path, signature, raw, targets, config)
                try:
                    worker_status = json.loads(status.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    worker_status = {}
                last_stage=(reader.last or {}).get('stage') or journal.state().get('stage','worker')
                last_context=(reader.last or {}).get('context',{})
                journal.emit('worker_exit',last_stage,'Verdi 进程已退出',last_context,
                             attempt=attempt,returncode=code,worker=worker_identity,checkpoint=checkpoint)
                if timeout_hit:
                    # The decision is durable before killing the worker. A
                    # later GUI/controller recovery loads the same skip set.
                    rollback_files(checkpoint, raw, targets, module_directory)
                    report['timeout_restart_count'] += 1
                    save_report()
                    journal.emit('restart','trace','trace 超时后重启 Verdi；继续其他例化',timeout_hit,
                                 attempt=attempt,reason='port_trace_timeout',returncode=code,checkpoint=checkpoint,
                                 controlled_trace_timeout_restart=True)
                    log.write('CT7_TIMEOUT_RESTART next_attempt={} instance={}\n'.format(attempt+1,timeout_hit['instance']));log.flush()
                    continue
                if checkpoint['stage'] == 'complete':
                    code = 0 if memory_hit else code
                    break
                if not memory_hit and worker_status.get('stage') != 'rotating':
                    journal.emit('failed',last_stage,'Verdi 在完整结束标志之前退出',last_context,
                                 attempt=attempt,returncode=code,worker=worker_identity,checkpoint=checkpoint,
                                 exit_signal=signal.Signals(-code).name if code<0 and -code in signal.Signals._value2member_map_ else None)
                    break  # an ordinary EDA failure is never a memory restart
                key = tuple(checkpoint[k] for k in ('completed_queries', 'completed_modules', 'completed_traces', 'instances'))
                stalls = stalls+1 if key == last_key else 1
                last_key = key
                reason = 'hard_limit' if memory_hit else 'checkpoint_rotation'
                event = {'attempt': attempt, 'reason': reason, 'exit_code': code, 'checkpoint': checkpoint,
                         'worker': worker_status, 'rss_gib': round(rotation_rss, 3) if rotation_rss is not None else report['current_rss_gib']}
                report['restarts'].append(event)
                # Keep the complete audit on disk and only a bounded preview
                # in memory.json / the result metadata.
                with (output / 'memory-restarts.jsonl').open('a', encoding='utf-8') as audit:
                    audit.write(json.dumps(event, ensure_ascii=False) + '\n')
                report['restarts'] = report['restarts'][-32:]
                if stalls >= MAX_STALLED_ATTEMPTS:
                    rollback_files(checkpoint, raw, targets, module_directory)
                    report.update(state='blocked', last_checkpoint=checkpoint,
                                  error='Memory pressure repeated without checkpoint progress')
                    save_report()
                    raise MemoryResumeError('Verdi memory protection could not advance after {} attempts at the same checkpoint '
                        '(stage={}, completed module queries={}, completed traces={}). Import/handle restoration or one query '
                        'may already exceed the threshold. Narrow the hierarchy or raise memory capacity; '
                        'completed evidence is retained in {}. No modules were skipped.'.format(stalls, worker_status.get('stage', checkpoint['stage']),
                            checkpoint['completed_queries'], checkpoint['completed_traces'], work))
                report['restart_count'] += 1
                journal.emit('restart',last_stage,'内存保护轮换 Verdi',last_context,attempt=attempt,
                             reason=reason,returncode=code,checkpoint=checkpoint,controlled_memory_restart=True)
                log.write('CT7_MEMORY_RESTART next_attempt={} queries={} traces={}\n'.format(
                    attempt+1, checkpoint['completed_queries'], checkpoint['completed_traces']))
                log.flush()
            report.update(state='finished' if checkpoint['stage'] == 'complete' else 'failed',
                          last_checkpoint=checkpoint, exit_code=code, current_rss_gib=None)
            save_report()
            return code, attempt_dir, report
    except BaseException as error:
        if process is not None:
            _stop_worker(process)
        if 'reader' in locals():reader.drain(journal,report['attempts'],locals().get('worker_identity',{}),all_records=True)
        journal.fail(error,cancelled=isinstance(error,(KeyboardInterrupt,SystemExit)))
        if report.get('state') not in ('blocked', 'finished', 'failed'):
            report['state'] = 'interrupted'
            save_report()
        raise
    finally:
        timeouts.close()
        if old_signal is not None:
            signal.signal(signal.SIGTERM, old_signal)
