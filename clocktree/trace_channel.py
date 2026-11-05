"""Transient, local trace messages; durable decisions remain in the run files."""
import json
import select
import socket
import time
import uuid


class TraceChannel:
    def __init__(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.server.bind(('127.0.0.1', 0))
            self.server.listen(1)
            self.server.setblocking(False)
        except BaseException:
            self.server.close()
            raise
        self.port = self.server.getsockname()[1]
        self.token = uuid.uuid4().hex
        self.client = None
        self.buffer = b''
        self.authenticated = False
        self.closed = False

    def read(self):
        if self.closed:return None
        if self.client is None:
            try:self.client, _ = self.server.accept()
            except BlockingIOError:return None
            self.client.setblocking(False)
            self.client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:data = self.client.recv(65536)
        except BlockingIOError:return None
        if not data:
            self.closed = True
            return None
        self.buffer += data
        if len(self.buffer) > 262144:
            raise ValueError('Trace monitor message exceeds 256 KiB')
        if not self.authenticated and b'\n' in self.buffer:
            auth, self.buffer = self.buffer.split(b'\n', 1)
            if auth != ('CT7 '+self.token).encode('ascii'):
                raise ValueError('Trace monitor authentication failed')
            self.authenticated = True
        if not self.authenticated or b'\n' not in self.buffer:return None
        line, self.buffer = self.buffer.split(b'\n', 1)
        # One request may be outstanding: the worker waits for each reply.
        if self.buffer:
            raise ValueError('Trace worker advanced before monitor acknowledgement')
        return json.loads(line.decode('utf-8'))

    def reply(self, trace_id, action):
        # Each reply is tiny and the worker is waiting to read it. Refuse a
        # broken/full channel rather than accepting an unacknowledged trace.
        self.client.sendall('{} {}\n'.format(trace_id, action).encode('ascii'))

    def wait(self, seconds):
        if self.closed:
            time.sleep(max(0.,seconds))
            return
        select.select([self.client or self.server], [], [], max(0., seconds))

    def close(self):
        for sock in (self.client, self.server):
            if sock is not None:sock.close()
        self.closed = True
