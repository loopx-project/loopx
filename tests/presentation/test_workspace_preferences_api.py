"""Production HTTP dispatch and a disposable runtime, never active Goal state."""
from contextlib import contextmanager
import http.client
import json
import threading

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

PATH = '/api/chat/sidebar-goal-order'


@contextmanager
def server(root):
    instance = ChatHTTPServer(('127.0.0.1', 0), ChatRequestHandler)
    instance.runtime_root = root
    instance.verbose = False
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance.server_address
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def request(address, method='GET', body=None, query='?source=%2Fstatus.json', origin=None):
    connection = http.client.HTTPConnection(*address, timeout=5)
    headers = {'Content-Type': 'application/json'}
    if origin:
        headers['Origin'] = origin
    connection.request(method, PATH + query, json.dumps(body) if body is not None else None, headers)
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def test_order_survives_server_restart_and_new_port_without_goal_state(tmp_path):
    with server(tmp_path) as address:
        assert request(address) == (200, {'ok': True, 'order': None})
        assert request(address, 'POST', {'source': '/status.json', 'order': ['b', 'absent', 'a']}) == (200, {'ok': True, 'order': ['b', 'absent', 'a']})
        assert request(address, 'POST', {'source': 'remote', 'order': ['a', 'b']})[0] == 200
    with server(tmp_path) as restarted:
        assert request(restarted)[1]['order'] == ['b', 'absent', 'a']
        assert request(restarted, query='?source=remote')[1]['order'] == ['a', 'b']
        # Migrating a second tab's stale browser cache cannot replace durable data.
        assert request(restarted, 'POST', {'source': '/status.json', 'order': ['a'], 'only_if_absent': True})[1]['order'] == ['b', 'absent', 'a']
        assert request(restarted, 'POST', {'source': '/status.json', 'order': []})[1]['order'] == []
    assert not (tmp_path / 'goals').exists()
    assert not (tmp_path / 'machine').exists()


@pytest.mark.parametrize('body', [
    {}, {'source': '', 'order': []}, {'source': '/status.json', 'order': ['a', 1]},
    {'source': 'local', 'order': 'a'}, {'source': 'local', 'order': ['a', 'a']},
    {'source': 'local', 'order': ['']}, {'source': 'local', 'order': [], 'only_if_absent': 'true'},
    {'source': 'local', 'order': [], 'goal_id': 'forbidden'},
])
def test_invalid_writes_do_not_create_preferences(tmp_path, body):
    with server(tmp_path) as address:
        assert request(address, 'POST', body)[0] == 400
        assert request(address)[1]['order'] is None


def test_corrupt_preferences_are_not_silently_overwritten(tmp_path):
    folder = tmp_path / 'presentation'
    folder.mkdir()
    path = folder / 'sidebar-goal-order-v1.json'
    path.write_text('{broken')
    with server(tmp_path) as address:
        assert request(address)[0] == 503
        assert request(address, 'POST', {'source': '/status.json', 'order': ['a']})[0] == 503
    assert path.read_text() == '{broken'


def test_origin_and_query_boundaries(tmp_path):
    with server(tmp_path) as address:
        assert request(address, 'POST', {'source': 'local', 'order': ['a']}, origin='https://example.com')[0] == 403
        for query in ['', '?source=a&source=b', '?source=a&goal_id=x']:
            assert request(address, query=query)[0] == 400


def test_write_failure_keeps_last_complete_order(tmp_path, monkeypatch):
    from loopx.presentation import workspace_preferences_api as api
    with server(tmp_path) as address:
        assert request(address, 'POST', {'source': 'local', 'order': ['a', 'b']})[0] == 200
        def fail(*args, **kwargs):
            raise OSError('Private path must not appear in response')
        monkeypatch.setattr(api, 'atomic_write_json', fail)
        status, result = request(address, 'POST', {'source': 'local', 'order': ['b', 'a']})
        assert status == 503
        assert 'Private path' not in json.dumps(result)
        assert request(address, query='?source=local')[1]['order'] == ['a', 'b']
