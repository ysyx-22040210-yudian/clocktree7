"""External per-port deadlines and durable, instance-specific skip decisions.

The worker waits for acknowledgement before committing a completed trace.
This lets the supervisor kill a blocked native call without accepting a
partial trace or racing into an unrelated instance's next port.
"""
import json
import math
import os
from pathlib import Path
import time

from .storage import write_graph
from .trace_channel import TraceChannel


class TraceTimeouts:
    def __init__(self, work, signature, seconds, resume=False, transport='file'):
        self.work, self.signature, self.seconds = Path(work), signature, float(seconds)
        self.file = self.work / 'trace-timeouts.json'
        self.status = self.work / 'trace-current.json'
        self.ack = self.work / 'trace-ack.txt'
        self.records = {}
        self.transport, self.channel, self.fallback_reason = transport, None, None
        if resume and self.seconds and not self.file.is_file():
            raise ValueError('Missing saved trace timeout decisions; cannot safely resume')
        if resume and self.file.exists():
            data = json.loads(self.file.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or data.get('version') != 1 or data.get('signature') != signature or not isinstance(data.get('instances'), dict):
                raise ValueError('Trace timeout decisions do not match this run')
            for owner, record in data['instances'].items():
                if (not isinstance(record, dict) or record.get('instance') != owner or
                        type(record.get('trace_id')) is not int or record['trace_id'] < 1 or
                        record.get('direction') not in ('driver', 'load') or not isinstance(record.get('pin'), str) or
                        not record['pin'] or record.get('limit_seconds') != self.seconds or
                        type(record.get('elapsed_seconds')) not in (int, float) or
                        not math.isfinite(record['elapsed_seconds']) or record['elapsed_seconds'] < self.seconds):
                    raise ValueError('Invalid saved trace timeout decision')
            self.records = data['instances']
        if not resume and self.seconds:
            write_graph(self.file, {'version':1, 'signature':signature, 'instances':{}})
        self.reset_attempt()

    def reset_attempt(self):
        self.close()
        if self.seconds and self.transport == 'socket':
            try:self.channel = TraceChannel()
            except OSError as error:
                self.transport, self.fallback_reason = 'file', str(error)
        self.active = None
        self.started = None
        self.acknowledged = 0
        self.current = None
        for path in (self.status, self.ack):
            if path.exists():path.unlink()

    def poll(self, now=None):
        """Return a deadline violation, or acknowledge an on-time completion."""
        if not self.seconds:return None
        now = time.monotonic() if now is None else now
        if self.channel:
            incoming = self.channel.read()
            if incoming is not None:self.current = incoming
            if self.channel.closed and self.active:
                raise RuntimeError('Trace monitor disconnected with an uncommitted port')
            if self.current is None:return None
            data = self.current
        else:
            try:data = json.loads(self.status.read_text(encoding='utf-8'))
            except FileNotFoundError:return None
        if not isinstance(data,dict):raise ValueError('Invalid worker trace deadline status')
        if data.get('state') == 'idle':
            self.active, self.started = None, None
            return None
        if (data.get('state') not in ('active', 'complete') or type(data.get('trace_id')) is not int or
                data['trace_id'] < 1 or not isinstance(data.get('instance'), str) or not data['instance'] or
                not isinstance(data.get('pin'), str) or not data['pin'] or data.get('direction') not in ('driver', 'load')):
            raise ValueError('Invalid worker trace deadline status')
        key = tuple(data[k] for k in ('trace_id', 'instance', 'pin', 'direction'))
        if data['trace_id'] <= self.acknowledged:
            if self.channel and incoming is not None:raise ValueError('Stale trace monitor request')
            return None
        if self.active != key:
            if self.active is not None:raise ValueError('Trace worker changed an uncommitted port')
            self.active, self.started = key, now
            if self.channel:
                if data['state'] != 'active':raise ValueError('Trace completion has no active start')
                self.channel.reply(data['trace_id'], 'start')
        elapsed = max(0., now-self.started)
        if data['state'] == 'complete':
            duration = data.get('elapsed_ms')
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                raise ValueError('Invalid completed trace duration')
            elapsed = duration / 1000.
            if elapsed < self.seconds:
                if self.channel:self.channel.reply(data['trace_id'], 'continue')
                else:
                    temporary = self.ack.with_suffix('.tmp')
                    temporary.write_text('{} continue\n'.format(data['trace_id']), encoding='ascii')
                    os.replace(str(temporary), str(self.ack))
                self.acknowledged = data['trace_id']
                self.active, self.started = None, None
                return None
        if elapsed < self.seconds:return None
        return dict({k:data[k] for k in ('trace_id','instance','pin','direction')},
                    limit_seconds=self.seconds, elapsed_seconds=elapsed)

    def wait(self, seconds):
        if self.channel:
            if self.started is not None:seconds = min(seconds, max(0.,self.seconds-(time.monotonic()-self.started)))
            self.channel.wait(seconds)
        else:time.sleep(seconds)

    def close(self):
        if self.channel:self.channel.close()
        self.channel = None

    def commit(self, decision, checkpoint, attempt):
        # With deadlines enabled, every port starts after a durable checkpoint
        # and waits for an acknowledgement before advancing that checkpoint.
        if checkpoint['stage'] != 'trace' or checkpoint['completed_traces'] != decision['trace_id']-1:
            raise ValueError('Timed-out port does not follow the committed trace prefix')
        owner = decision['instance']
        if owner in self.records:
            raise ValueError('Worker retraced an instance already skipped for timeout')
        record = dict(decision, attempt=attempt, timestamp=time.time())
        records = dict(self.records)
        records[owner] = record
        write_graph(self.file, {'version':1,'signature':self.signature,'instances':records})
        self.records = records
        return record
