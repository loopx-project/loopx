"""Private native-client transport; scoring stays in the pinned verifier.

This is a single-host experiment adapter, not the upstream Management/Driver
implementation. Every accepted train request uses its real verifier output.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
import uuid


class Rejected(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


class Trial:
    def __init__(self, root, arm, queue):
        self.root, self.arm, self.queue = Path(root), arm, queue
        self.token = secrets.token_hex(32)
        self.lock = threading.RLock()
        self.deadline = None
        self.phase = 'prepared'
        self.payloads = {}
        self.jobs = {}
        self.idempotency = {}
        self.train_calls = 0
        self.train_wall_seconds = 0.0
        self.saved = None
        self.final = None
        self.final_at = None
        self.rejected_requests = 0
        (self.root/'payloads').mkdir(parents=True, mode=0o700, exist_ok=True)

    def snapshot(self):
        return {'arm': self.arm, 'phase': self.phase, 'deadline': self.deadline,
                'train_calls': self.train_calls, 'train_wall_seconds': self.train_wall_seconds,
                'saved_payload': self.saved, 'final_payload': self.final,
                'jobs': {k: {x: v[x] for x in ['mode', 'state', 'payload_id']}
                         for k, v in self.jobs.items()},
                'rejected_requests': self.rejected_requests}

    def persist(self):
        p = self.root/'submission-state.json'
        temporary = p.with_suffix('.pending')
        temporary.write_text(json.dumps(self.snapshot(), indent=2))
        temporary.replace(p)

    def start(self, seconds):
        with self.lock:
            assert self.phase == 'prepared'
            self.deadline = time.time()+seconds
            self.phase = 'solving'
            self.persist()

    def admit(self):
        if self.phase != 'solving' or self.deadline is None or time.time() >= self.deadline:
            raise Rejected(409, 'answering phase is closed')

    def view(self, job):
        return {**job, 'remaining_train_calls': max(0, 100-self.train_calls)}

    def request(self, method, path, body, headers):
        with self.lock:
            fingerprint = hashlib.sha256(method.encode()+path.encode()+body).hexdigest()
            key = headers.get('Idempotency-Key')
            if key and key in self.idempotency:
                old, status, response = self.idempotency[key]
                if old != fingerprint:
                    raise Rejected(409, 'idempotency key is bound to different bytes')
                return status, response
            status, result = self._request(method, path, body, headers)
            if key:
                self.idempotency[key] = (fingerprint, status, result)
            self.persist()
            return status, result

    def _request(self, method, path, body, headers):
        if method == 'GET' and path.startswith('/v1/verifications/'):
            identifier = path.rsplit('/', 1)[-1]
            job = self.jobs.get(identifier)
            if not job or job['mode'] != 'train':
                raise Rejected(404, 'train verification not found')
            return 200, self.view(job)
        self.admit()
        if method == 'POST' and path == '/v1/payloads':
            if not 0 < len(body) <= 524288:
                raise Rejected(413, 'task submission size limit exceeded')
            digest = 'sha256:'+hashlib.sha256(body).hexdigest()
            if headers.get('X-Content-SHA256') != digest:
                raise Rejected(400, 'payload digest mismatch')
            purpose = headers.get('X-Frontier-Purpose')
            if purpose not in ('train', 'final'):
                raise Rejected(400, 'unsupported payload purpose')
            identifier = 'payload_'+digest.split(':')[1]+'_'+purpose
            if identifier not in self.payloads:
                target = self.root/'payloads'/(identifier+'.tar')
                target.write_bytes(body)
                # The enclosing host directory remains private; the native
                # unprivileged verifier receives this one file read-only.
                target.chmod(0o444)
                self.payloads[identifier] = {'path': str(target), 'sha256': digest, 'purpose': purpose}
            return 201, {'payload_id': identifier}
        try:
            document = json.loads(body)
            identifier = document['payload_id']
            payload = self.payloads[identifier]
        except (ValueError, KeyError, TypeError):
            raise Rejected(400, 'unknown trial payload') from None
        if method == 'POST' and path == '/v1/verifier-train':
            if payload['purpose'] != 'train':
                raise Rejected(400, 'payload purpose mismatch')
            if self.train_calls >= 100 or self.train_wall_seconds >= 180000:
                raise Rejected(429, 'authored train budget exhausted')
            verification = 'verification_'+uuid.uuid4().hex
            job = {'verification_id': verification, 'payload_id': identifier,
                   'mode': 'train', 'state': 'queued', 'metrics': {}}
            self.jobs[verification] = job
            self.train_calls += 1
            self.queue.put((self, verification))
            return 202, self.view(job)
        if method == 'POST' and path == '/v1/submissions/checkpoint':
            if payload['purpose'] != 'final':
                raise Rejected(400, 'payload purpose mismatch')
            # The latest explicitly chosen immutable snapshot wins. A rejected
            # replacement never reaches this mutation.
            self.saved = identifier
            return 200, {'remaining_runtime_s': max(0, int(self.deadline-time.time())),
                         'remaining_checkpoint_submissions': None}
        if method == 'POST' and path == '/v1/submissions':
            if payload['purpose'] != 'final':
                raise Rejected(400, 'payload purpose mismatch')
            verification = 'verification_'+uuid.uuid4().hex
            self.final, self.final_at = identifier, time.time()
            self.phase = 'final_selected'
            self.jobs[verification] = {'verification_id': verification,
                'payload_id': identifier, 'mode': 'eval', 'state': 'queued', 'metrics': {}}
            return 202, {'verification_id': verification}
        raise Rejected(404, 'unsupported operation')


class Bridge(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        self.trials = {}
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def handle_request(self):
        trial = next((t for token, t in self.server.trials.items()
                      if secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+token)), None)
        try:
            if trial is None:
                raise Rejected(401, 'trial capability required')
            length = int(self.headers.get('Content-Length', 0))
            if length < 0 or length > 524288:
                raise Rejected(413, 'task submission size limit exceeded')
            body = self.rfile.read(length)
            if len(body) != length:
                raise Rejected(400, 'incomplete request body')
            status, result = trial.request(self.command, self.path, body, self.headers)
        except Rejected as error:
            status, result = error.status, {'error': error.reason}
            if trial:
                trial.rejected_requests += 1
        except (ValueError, OSError):
            status, result = 400, {'error': 'malformed request'}
        raw = json.dumps(result, separators=(',', ':'), allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
