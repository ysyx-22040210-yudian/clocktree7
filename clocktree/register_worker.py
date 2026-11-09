"""Owned Verdi Language Model worker with on-disk, completed-query caching."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import uuid

from .config import tcl_string, write_tcl_config
from .environment import resolve_verdi
from .extract import vendor_work_dir, vendor_safe_path
from .memory import GIB, process_memory, _stop_worker
from .runtime import process_identity, alive
from .storage import write_graph


class WorkerFault(RuntimeError):pass
class MemoryFault(WorkerFault):pass
class PortDeadline(WorkerFault):pass
class QueryError(ValueError):pass


class RegisterWorker:
    def __init__(self, config, directory, memory_limit, report):
        self.config=config;self.directory=Path(directory);self.memory_limit=memory_limit;self.report=report
        self.process=None;self.socket=None;self.server=None;self.stream=None;self.buffer=b''
        self.peak=0;self.rss=0;self.deadline=None;self.last_memory=0
        self.cache=sqlite3.connect(str(self.directory/'queries.sqlite'))
        self.cache.execute('CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY)')
        self.cache.execute('CREATE TABLE IF NOT EXISTS records (key TEXT, seq INTEGER, data TEXT, PRIMARY KEY(key,seq))')
        self.cache.commit()
        self.identity=self.directory/'worker.json'
        if self.identity.exists():
            old=json.loads(self.identity.read_text(encoding='utf-8'))
            if alive(old.get('controller',{})) is True:
                raise WorkerFault('Previous register controller is still running')
            worker=old.get('worker',{})
            if alive(worker) is True and os.name!='nt':os.killpg(worker['pid'],signal.SIGKILL)

    def check(self):
        now=time.monotonic()
        if self.deadline is not None and now>=self.deadline:raise PortDeadline('辅助端口寄存器追踪超时')
        if self.process and now-self.last_memory>=.2:
            self.last_memory=now
            if os.name!='nt':
                self.rss=process_memory(self.process.pid)['rss_bytes']
                self.peak=max(self.peak,self.rss)
                if self.memory_limit and self.rss>=self.memory_limit*GIB:
                    raise MemoryFault('Verdi/控制进程 RSS 达到 {} GiB'.format(self.memory_limit))
        if self.process and self.process.poll() is not None:
            raise WorkerFault('Verdi 提前退出，退出码 {}；查看 verdi.log'.format(self.process.returncode))

    def receive(self):
        while b'\n' not in self.buffer:
            self.check()
            try:part=self.socket.recv(65536)
            except socket.timeout:continue
            if not part:raise WorkerFault('Verdi 连接断开；当前未提交端口不会保留为成功')
            self.buffer+=part
        line,self.buffer=self.buffer.split(b'\n',1)
        try:record=json.loads(line.decode('utf-8'))
        except (ValueError,UnicodeError):raise WorkerFault('Invalid NPI query response')
        if record.get('event')=='fatal':raise WorkerFault(record.get('reason','NPI import failed'))
        return record

    def ensure(self):
        if self.process and self.process.poll() is None:return
        self.stop();self.deadline=None
        identifier=uuid.uuid4().hex
        work=vendor_work_dir(self.directory/('verdi-'+identifier))
        root=Path(__file__).resolve().parents[1]
        for name in ('extract.tcl','register_worker.tcl'):
            shutil.copy2(str(root/'npi'/name),str(work/('clocktree-extract.tcl' if name=='extract.tcl' else name)))
        cfg=json.loads(json.dumps(self.config))
        for key in ('elab','filelist'):
            value=cfg['design'].get(key)
            if value and not vendor_safe_path(value):
                alias=work/('input.elab++' if key=='elab' else 'input.f');alias.symlink_to(value,target_is_directory=key=='elab')
                cfg['design'][key]=str(alias)
        write_tcl_config(cfg,work/'rules.tcl',work/'unused.jsonl')
        exe,home=resolve_verdi(cfg['design'],os.environ.copy())
        self.server=socket.socket();self.server.bind(('127.0.0.1',0));self.server.listen(1);self.server.settimeout(.1)
        token=uuid.uuid4().hex
        env=dict(os.environ,VERDI_HOME=home,CT7_CONFIG=str(work/'rules.tcl'),
                 CT7_REGISTER_PORT=str(self.server.getsockname()[1]),CT7_REGISTER_TOKEN=token)
        self.stream=(self.directory/'verdi.log').open('a',encoding='utf-8')
        self.stream.write('\nREGISTER_WORKER '+identifier+'\n');self.stream.flush()
        self.process=subprocess.Popen([exe,'-batch','-nologo','-play','register_worker.tcl'],cwd=str(work),env=env,
                                      stdout=self.stream,stderr=subprocess.STDOUT,start_new_session=os.name!='nt')
        write_graph(self.identity,{'controller':process_identity(),'worker':process_identity(self.process.pid)})
        self.report('import','正在导入 elab',{'work':str(work),'pid':self.process.pid})
        while self.socket is None:
            self.check()
            try:self.socket,_=self.server.accept();self.socket.settimeout(.1)
            except socket.timeout:continue
        hello=self.receive()
        if hello.get('event')!='hello' or hello.get('token')!=token:raise WorkerFault('Unexpected register worker identity')
        if self.receive().get('event')!='ready':raise WorkerFault('NPI worker did not finish import')
        self.report('ready','elab 导入完成',{})

    def query(self,operation,*args):
        key=json.dumps([operation]+list(args),ensure_ascii=False)
        if not self.cache.execute('SELECT 1 FROM requests WHERE key=?',(key,)).fetchone():
            self.ensure();self.check()
            self.report('query','NPI '+operation,{'operation':operation,'arguments':list(args)})
            request=' '.join(tcl_string(a) for a in (operation,)+args)+'\n'
            self.socket.sendall(request.encode('utf-8'))
            try:
                seq=0;failure=None
                while True:
                    row=self.receive()
                    if row.get('event')=='end':break
                    if row.get('event')=='error':failure=row.get('reason','NPI query failed')
                    self.cache.execute('INSERT INTO records VALUES (?,?,?)',(key,seq,json.dumps(row,ensure_ascii=False)));seq+=1
                if failure:raise QueryError(failure)
                self.cache.execute('INSERT INTO requests VALUES (?)',(key,));self.cache.commit()
            except BaseException:
                self.cache.rollback();raise
        # A cursor streams rows; neither large fanout lists nor NPI handles are
        # retained in Python RAM. Only completed replies enter this cache.
        return (json.loads(row[0]) for row in self.cache.execute('SELECT data FROM records WHERE key=? ORDER BY seq',(key,)))

    def stop(self):
        self.deadline=None
        if self.process:
            _stop_worker(self.process)
            if self.identity.exists():self.identity.unlink()
        self.process=None
        for stream in (self.socket,self.server,self.stream):
            if stream:
                try:stream.close()
                except OSError:pass
        self.socket=self.server=self.stream=None;self.buffer=b''

    def close(self):
        self.stop();self.cache.close()
