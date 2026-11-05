"""Durable execution stages, bounded live status and post-mortem inspection."""
import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import threading
import time
import uuid

from .storage import write_graph


STAGES = [('config','准备'),('import','导入'),('modules','查找'),('trace','追踪'),
          ('topology','拓扑'),('index','索引'),('export','结果'),('display','显示')]
LABELS = dict(STAGES, worker='启动 Verdi', restore='恢复句柄', load='读取结果',
              layout='布局计算', canvas='绘制画布', session='恢复工作区')
BACKEND_LOGS = ('run.log','run-events.jsonl','run-status.json')


def stage_node(stage):
    return {'worker':'import','restore':'import','load':'display','layout':'display','canvas':'display',
            'session':'display','module_checkpoint':'modules'}.get(stage,stage)


def process_identity(pid=None):
    pid = os.getpid() if pid is None else pid
    value = {'pid':pid,'host':socket.gethostname(),'platform':sys.platform}
    if sys.platform.startswith('linux'):
        try:
            value['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            fields = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')',1)[1].split()
            value['start_ticks'] = fields[19]
            value['process_state'] = fields[0]
        except (OSError,IndexError):
            pass
    elif os.name=='nt':
        details=_windows_process(pid)
        if details: value.update(details)
    return value


def _windows_process(pid):
    # os.kill(pid, 0) uses TerminateProcess on Windows. Query a handle instead.
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle: return {'alive':False} if ctypes.get_last_error()==87 else None
    try:
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):return None
        times=[wintypes.FILETIME() for _ in range(4)]
        result={'alive':code.value==259}
        if kernel.GetProcessTimes(handle,*[ctypes.byref(value) for value in times]):
            result['start_ticks']=str((times[0].dwHighDateTime<<32)|times[0].dwLowDateTime)
        return result
    finally:kernel.CloseHandle(handle)


def alive(identity):
    if not identity or identity.get('host') != socket.gethostname():
        return None
    pid = identity.get('pid')
    if not isinstance(pid,int) or pid<=0:
        return None
    if sys.platform.startswith('linux'):
        now = process_identity(pid)
        if identity.get('boot_id') and identity['boot_id'] != now.get('boot_id'):return False
        if 'start_ticks' not in now or now.get('process_state')=='Z':
            return False
        if identity.get('start_ticks') and identity['start_ticks'] != now['start_ticks']:
            return False
        return True
    if os.name=='nt':
        now=_windows_process(pid)
        if not now:return None
        if identity.get('start_ticks') and now.get('start_ticks')!=identity['start_ticks']:return False
        return now['alive']
    try:
        os.kill(pid,0)
        return True
    except OSError:
        return False


def read_json(path, default=None):
    try:
        with Path(path).open(encoding='utf-8') as stream:
            return json.load(stream)
    except (OSError,ValueError):
        return default


def tail_text(path, limit=24000):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0,2)
            stream.seek(max(0,stream.tell()-limit))
            return stream.read().decode('utf-8','replace')
    except OSError:
        return ''


def last_event(path):
    """Read the last complete record, even after a killed partial append."""
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0,2)
            end=stream.tell(); start=max(0,end-16384)
            while True:
                stream.seek(start)
                lines=stream.read(end-start).splitlines()
                candidates=lines if start==0 else lines[1:]
                for line in reversed(candidates):
                    try:
                        event=json.loads(line.decode('utf-8'))
                        if isinstance(event,dict):return event
                    except (ValueError,UnicodeError):pass
                if start==0:return None
                start=max(0,start-max(16384,end-start))
    except OSError:
        return None


def _append(path, text, durable):
    with Path(path).open('a',encoding='utf-8') as stream:
        stream.write(text+'\n');stream.flush()
        if durable:os.fsync(stream.fileno())


class RunJournal:
    """One writer process per channel; each event precedes its heavy operation."""
    def __init__(self, directory, prefix='run', mirror=None):
        self.directory=Path(directory).resolve()
        self.directory.mkdir(parents=True,exist_ok=True)
        self.prefix=prefix
        self.mirror=Path(mirror).resolve() if mirror else None
        self.status_file=self.directory/(prefix+'-status.json')
        self.event_file=self.directory/(prefix+'-events.jsonl')
        self.log_file=self.directory/(prefix+'.log')
        self.lock=threading.RLock()

    @classmethod
    def start(cls, output):
        output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
        identifier=time.strftime('%Y%m%d_%H%M%S_')+uuid.uuid4().hex
        old=[output/name for name in BACKEND_LOGS if (output/name).is_file()]
        if old:
            history=output/'.clocktree-history'/identifier
            history.mkdir(parents=True,exist_ok=True)
            for path in old:shutil.copy2(str(path),str(history/path.name))
        directory=output/'.clocktree-runs'/identifier
        journal=cls(directory,mirror=output)
        state={'schema_version':1,'run_id':identifier,'owner':process_identity(),'state':'running',
               'started_at_ms':int(time.time()*1000),
               'journal_directory':str(directory),'steps':{},'sequence':0,'stage':'config'}
        write_graph(journal.status_file,state)
        for name in BACKEND_LOGS:
            target=output/name
            if target.exists():target.unlink()
        journal.emit('begin','config',detail='检查配置与运行环境')
        return journal

    @classmethod
    def attach(cls, output):
        output=Path(output).resolve()
        state=read_json(output/'run-status.json',{})
        if state.get('journal_directory'):
            return cls(state['journal_directory'],mirror=output)
        return cls.start(output)

    def state(self):
        return read_json(self.status_file,{})

    def emit(self, kind, stage, detail='', context=None, durable=True, **extra):
        with self.lock:
            state=self.state()
            stamp=time.time()
            event={'time_ms':int(stamp*1000),'timestamp':datetime.datetime.fromtimestamp(stamp,datetime.timezone.utc).isoformat(),
                   'run_id':state.get('run_id',self.directory.name),'source':self.prefix,'pid':os.getpid(),
                   'sequence':state.get('sequence',0)+1,'kind':kind,'stage':stage,
                   'label':LABELS.get(stage,stage),'detail':str(detail),'context':context or {}}
            event.update(extra)
            state.update(sequence=event['sequence'],updated_at=event['timestamp'],stage=stage,kind=kind,
                         detail=event['detail'],context=event['context'],last_event=event,
                         journal_directory=str(self.directory),owner=state.get('owner') or process_identity())
            steps=state.setdefault('steps',{})
            node=stage_node(stage)
            if kind in ('begin','item_begin','progress'):
                state['state']='running'
                steps[node]='running'
                if kind in ('begin','item_begin'):state['last_entered']=event
            elif kind=='end':
                steps[node]='done';state['last_completed']=event
            elif kind=='item_end':
                state['last_completed_item']=event
            elif kind in ('failed','interrupted','cancelled'):
                steps[node]='failed' if kind=='failed' else 'interrupted'
                state['state']=kind
                if kind=='failed':state['last_failure']=event
            elif kind=='restart':
                state['state']='restarting';state['last_interruption']=event
                for key in ('import','modules','trace'):steps[key]='pending'
            elif kind in ('finished','attention'):
                state['state']=kind
            if 'worker_events' in extra:state['worker_events']=extra['worker_events']
            if 'worker' in extra:state['worker']=extra['worker']
            if 'attempt' in extra:state['attempt']=extra['attempt']
            if 'checkpoint' in extra:state['checkpoint']=extra['checkpoint']
            if 'clean_shutdown' in extra:state['clean_shutdown']=extra['clean_shutdown']
            line=json.dumps(event,ensure_ascii=False,separators=(',',':'))
            item=event['context']
            readable='{} [{}] {} {} {}'.format(event['timestamp'],kind.upper(),event['label'],detail,
                json.dumps(item,ensure_ascii=False,separators=(',',':')) if item else '')
            directories=[self.directory]+([self.mirror] if self.mirror and self.mirror!=self.directory else [])
            for directory in directories:
                _append(directory/(self.prefix+'-events.jsonl'),line,durable)
                _append(directory/(self.prefix+'.log'),readable,durable)
                write_graph(directory/(self.prefix+'-status.json'),state)
            return state

    def fail(self, error, cancelled=False):
        state=self.state()
        failure=state.get('last_failure') or {}
        if failure and not cancelled:
            # Keep the original worker signal, context and error in the final marker.
            return self.emit('failed',failure.get('stage','config'),detail=failure.get('detail',str(error))[:6000],
                             context=failure.get('context',{}),error_type=type(error).__name__,
                             subsequent_error=str(error)[:6000],
                             **{k:failure.get(k) for k in ('returncode','exit_signal')})
        return self.emit('cancelled' if cancelled else 'failed',state.get('stage','config'),
                         detail=str(error)[:6000],context=state.get('context',{}),error_type=type(error).__name__)

    def worker_event(self, event, attempt, worker):
        if not event:return
        kind=event.get('kind','progress')
        extra={}
        if kind=='checkpoint':
            saved=checkpoint_state(self.directory)
            if saved:extra['checkpoint']=saved
        if kind not in ('begin','end','item_begin','item_end','failed','progress'):kind='progress'
        self.emit(kind,event.get('stage','worker'),event.get('detail',''),event.get('context',{}),
                  durable=kind in ('begin','end','failed'),attempt=attempt,worker=worker,worker_event=event,**extra)

    def paths(self):
        state=self.state()
        return {'run_id':state.get('run_id',self.directory.name),'journal_directory':str(self.directory),
                'log':str(self.log_file),'events':str(self.event_file),'status':str(self.status_file)}


class WorkerEvents:
    """Stream worker records; coalesce fast units, retain every major boundary."""
    def __init__(self,path):
        self.path=Path(path);self.offset=0;self.pending=b'';self.last=None

    def drain(self,journal,attempt,worker,all_records=False):
        while True:
            try:
                with self.path.open('rb') as stream:
                    stream.seek(self.offset);data=stream.read(262144);self.offset=stream.tell()
            except OSError:return
            if not data:return
            parts=(self.pending+data).split(b'\n');self.pending=parts.pop()
            sampled=None
            for line in parts:
                try:event=json.loads(line.decode('utf-8'))
                except (ValueError,UnicodeError):continue
                self.last=event
                if event.get('kind') in ('begin','end','failed','checkpoint'):
                    if sampled:journal.worker_event(sampled,attempt,worker);sampled=None
                    journal.worker_event(event,attempt,worker)
                else:sampled=event
            if sampled:journal.worker_event(sampled,attempt,worker)
            if not all_records:return


def checkpoint_state(journal):
    journal=Path(journal)
    local=read_json(journal/'checkpoint.json')
    if local:return local
    location=read_json(journal/'runtime-location.json',{})
    if location.get('working_directory'):
        return read_json(Path(location['working_directory'])/'checkpoint.json')
    return None


def diagnose(directory):
    directory=Path(directory).resolve()
    state=read_json(directory/'run-status.json',{})
    if not state and (directory/'gui-status.json').exists():
        return diagnose_gui(directory)
    if not state:
        return {'available':False,'directory':str(directory),'detail':'没有找到阶段记录；可查看旧版 verdi.log / cli.log'}
    recorded=Path(state.get('journal_directory',directory))
    local=directory/'.clocktree-runs'/state.get('run_id','')
    journal=local if (local/'run-status.json').is_file() else recorded
    if not (journal/'run-status.json').is_file():journal=directory
    canonical=read_json(journal/'run-status.json',state)
    output=directory.parent.parent if directory.parent.name=='.clocktree-runs' else directory
    same_run=read_json(output/'run-status.json',{}).get('run_id')==canonical.get('run_id')
    worker_path=canonical.get('worker_events')
    if worker_path:
        original=Path(worker_path)
        try:
            relative=original.relative_to(recorded)
            if (journal/relative).is_file():worker_path=str(journal/relative)
        except ValueError:pass
    event=last_event(worker_path) if worker_path else None
    result={'available':True,'directory':str(directory),'journal_directory':str(journal),
            'state':canonical.get('state'),'stage':canonical.get('stage'),'label':LABELS.get(canonical.get('stage'),canonical.get('stage')),
            'last_event':canonical.get('last_event'),'last_completed':canonical.get('last_completed'),
            'last_failure':canonical.get('last_failure'),'last_restart':canonical.get('last_interruption'),
            'checkpoint':canonical.get('checkpoint'),
            'owner_alive':alive(canonical.get('owner')),'cause':'unknown',
            'logs':{'run':str(journal/'run.log'),'events':str(journal/'run-events.jsonl'),
                    'worker':worker_path,'verdi':str(output/'verdi.log') if same_run else None,
                    'cli':str(output/'cli.log') if same_run else None}}
    active=canonical.get('state') in ('running','restarting')
    if active and result['owner_alive'] is False:
        result['state']='interrupted'
        result['detail']='控制进程已退出，日志没有记录正常结束；不能仅凭此断言 OOM。'
    if event and active and stage_node(canonical.get('stage')) in ('import','modules','trace'):
        result.update(last_worker_event=event,stage=event.get('stage',result['stage']),
                      label=LABELS.get(event.get('stage'),event.get('stage')),context=event.get('context',{}))
    else:result['context']=canonical.get('context',{})
    result['last_entered']=canonical.get('last_entered')
    saved=checkpoint_state(journal)
    if saved:result['checkpoint']=saved
    failure=canonical.get('last_failure') or {}
    if failure.get('controlled_memory_restart'):result['cause']='memory_protection'
    elif failure.get('returncode') is not None:result['cause']='worker_exit_{}'.format(failure['returncode'])
    memory=read_json(output/'memory.json',{}) if same_run else {}
    if memory:
        result['memory']={key:memory.get(key) for key in ('state','limit_gib','current_rss_gib','peak_rss_gib','restart_count','error','trace_timeout_count','timeout_restart_count','last_trace_timeout')}
        if memory.get('state')=='blocked':result['cause']='memory_protection_stalled'
        elif canonical.get('state')=='restarting':
            result['cause']='port_trace_timeout' if (canonical.get('last_interruption') or {}).get('controlled_trace_timeout_restart') else 'memory_protection'
    reference=read_json(directory/'gui-log-location.json')
    if reference:
        result['gui']=diagnose_gui(reference['journal_directory'])
    previous=read_json(directory/'gui-log-location.previous.json')
    if previous:
        issue=diagnose_gui(previous['journal_directory'])
        if issue.get('state') in ('interrupted','failed'):result['previous_gui_issue']=issue
    return result


def diagnose_gui(directory):
    directory=Path(directory).resolve()
    state=read_json(directory/'gui-status.json',{})
    result={'available':bool(state),'directory':str(directory),'state':state.get('state'),
            'stage':state.get('stage'),'label':LABELS.get(state.get('stage'),state.get('stage')),
            'last_event':state.get('last_event'),'last_entered':state.get('last_entered'),
            'last_completed':state.get('last_completed'),'context':state.get('context',{}),
            'owner_alive':alive(state.get('owner')),'log':str(directory/'gui.log'),
            'events':str(directory/'gui-events.jsonl'),'status':str(directory/'gui-status.json')}
    if result['owner_alive'] is False and not state.get('clean_shutdown') and state.get('state') in ('running','finished','attention'):
        # "finished" can mean that drawing finished while the window stayed
        # open. Only the explicit close marker makes the GUI session clean.
        if state.get('detail') != 'GUI 正常关闭':result['state']='interrupted'
    return result


def bind_gui(directory,journal):
    directory=Path(directory)
    reference=directory/'gui-log-location.json'
    new=dict(journal.paths(),owner=process_identity())
    old=read_json(reference)
    try:
        if old and old.get('journal_directory')!=new['journal_directory']:
            write_graph(directory/'gui-log-location.previous.json',old)
        write_graph(reference,new)
    except OSError:
        pass  # read-only imported results retain GUI diagnostics in user storage
