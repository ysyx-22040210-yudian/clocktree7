import json
import socket

import pytest

from clocktree.trace_timeout import TraceTimeouts


def send(client,trace=1,state='active',**kwargs):
    data=dict(state=state,trace_id=trace,instance='top.时钟[0]',pin='a',direction='driver',**kwargs)
    client.sendall((json.dumps(data,ensure_ascii=False)+'\n').encode('utf-8'))


@pytest.fixture
def connected(tmp_path):
    watch=TraceTimeouts(tmp_path,'sig',1,transport='socket')
    client=socket.create_connection(('127.0.0.1',watch.channel.port))
    client.settimeout(1)
    client.sendall(('CT7 '+watch.channel.token+'\n').encode())
    try:yield watch,client
    finally:client.close();watch.close()


def test_socket_roundtrip_preserves_unicode_and_has_no_transient_disk_writes(connected):
    watch,client=connected
    send(client);assert watch.poll(10) is None
    assert client.recv(100)==b'1 start\n'
    send(client,state='complete',elapsed_ms=99);assert watch.poll(10.1) is None
    assert client.recv(100)==b'1 continue\n'
    assert not watch.status.exists() and not watch.ack.exists()
    send(client,trace=2);watch.poll(20)
    assert client.recv(100)==b'2 start\n'
    assert watch.poll(21)['instance']=='top.时钟[0]'


def test_native_blocking_deadline_does_not_need_worker_messages(connected):
    watch,client=connected;send(client);watch.poll(5)
    assert client.recv(100)==b'1 start\n'
    assert watch.poll(6)['elapsed_seconds']==1
    assert not watch.records  # polling alone cannot commit a skip


def test_completion_after_deadline_never_receives_commit_ack(connected):
    watch,client=connected;send(client);watch.poll(0);client.recv(100)
    send(client,state='complete',elapsed_ms=1001)
    assert watch.poll(.1)['elapsed_seconds']==1.001
    assert watch.acknowledged==0


def test_disconnect_on_active_port_fails_instead_of_skipping(connected):
    watch,client=connected;send(client);watch.poll(0);client.recv(100);client.close()
    with pytest.raises(RuntimeError,match='disconnected'):watch.poll(.5)
    assert not watch.records


def test_finished_disconnect_does_not_turn_valid_result_into_failure(connected):
    watch,client=connected;send(client);watch.poll(0);client.recv(100)
    send(client,state='complete',elapsed_ms=0);watch.poll(.01);client.recv(100);client.close()
    assert watch.poll(.1) is None


def test_stale_or_changed_port_cannot_rearm_deadline(connected):
    watch,client=connected;send(client);watch.poll(0);client.recv(100)
    send(client,trace=2)
    with pytest.raises(ValueError,match='changed an uncommitted'):watch.poll(.5)


def test_replayed_finished_request_fails_instead_of_waiting_forever(connected):
    watch,client=connected;send(client);watch.poll(0);client.recv(100)
    send(client,state='complete',elapsed_ms=0);watch.poll(.01);client.recv(100)
    send(client)
    with pytest.raises(ValueError,match='Stale'):watch.poll(.5)


def test_partial_messages_wait_for_complete_utf8_line(connected):
    watch,client=connected
    packet=(json.dumps(dict(state='active',trace_id=1,instance='top.中文',pin='i',direction='driver'),ensure_ascii=False)+'\n').encode()
    split=packet.index('中'.encode())+1
    client.sendall(packet[:split]);assert watch.poll(0) is None and watch.active is None
    client.sendall(packet[split:]);watch.wait(.1);watch.poll(.1)
    assert client.recv(100)==b'1 start\n'


def test_blocked_local_socket_uses_explicitly_reported_file_fallback(tmp_path,monkeypatch):
    import clocktree.trace_timeout as module
    def unavailable():raise OSError('local listener disabled')
    monkeypatch.setattr(module,'TraceChannel',unavailable)
    watch=TraceTimeouts(tmp_path,'sig',1,transport='socket')
    assert watch.transport=='file' and 'disabled' in watch.fallback_reason


def test_socket_rotation_closes_old_channel_and_changes_token(connected):
    watch,client=connected
    old=watch.channel;watch.reset_attempt()
    assert old.closed and watch.channel.token!=old.token
    assert watch.active is None and watch.acknowledged==0


def test_failed_durable_decision_does_not_change_live_skip_state(connected,monkeypatch):
    import errno
    import clocktree.trace_timeout as module
    watch,client=connected;send(client);watch.poll(0);client.recv(100)
    decision=watch.poll(2);before=watch.file.read_bytes()
    original=module.write_graph
    def full(*args,**kwargs):raise OSError(errno.ENOSPC,'injected full filesystem')
    monkeypatch.setattr(module,'write_graph',full)
    with pytest.raises(OSError):watch.commit(decision,dict(stage='trace',completed_traces=0),1)
    assert not watch.records and watch.file.read_bytes()==before
    monkeypatch.setattr(module,'write_graph',original)
    watch.commit(decision,dict(stage='trace',completed_traces=0),1)
    assert list(watch.records)==['top.时钟[0]']
