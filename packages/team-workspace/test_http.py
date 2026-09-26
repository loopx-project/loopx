"""Real localhost transport checks; no model calls or personal history reads."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

from model import Codex
from service import Workspace
from server import handler


class TransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='team-http-test-')
        cls.app = Workspace(cls.tmp.name, Codex('codex'))
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(cls.app))
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join()
        cls.server.server_close()
        cls.app.store.close()
        cls.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        return urllib.request.urlopen(urllib.request.Request(self.url + path,
            data=json.dumps(data).encode() if data is not None else None, headers=headers or {}))

    def test_app_and_state(self):
        with self.request('/healthz') as r:
            self.assertEqual(json.load(r)['application'], 'team-workspace')
        with self.request('/') as r:
            self.assertIn('frame-ancestors', r.headers['Content-Security-Policy'])
            self.assertIn('团队工作台', r.read().decode())
        with self.request('/api/state') as r:
            self.assertEqual(json.load(r)['goals'], [])

    def test_cross_site_write_rejected(self):
        for headers in ({'Origin': 'https://untrusted.invalid', 'Content-Type': 'application/json', 'X-Team-Workspace': 'local'},
                        {'Content-Type': 'application/json'}, {'Host': 'evil.invalid'}):
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.request('/api/members', {}, headers)
            self.assertEqual(e.exception.code, 403)
            e.exception.close()
        self.assertEqual(self.app.store.all('members'), [])

    def test_member_save_readback(self):
        data = {'name': 'Test person', 'role': 'Research', 'description': 'Synthetic test profile',
                'skills': ['research'], 'decision_scopes': []}
        with self.request('/api/members', data, {'Content-Type': 'application/json', 'X-Team-Workspace': 'local'}) as r:
            result = json.load(r)['result']
        self.assertEqual(result['name'], data['name'])
        self.assertEqual(self.app.store.get('members', result['id'])['decision_scopes'], [])

    def test_static_traversal_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request('/%2e%2e/server.py')
        self.assertEqual(e.exception.code, 404)
        e.exception.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
