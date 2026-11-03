#!/usr/bin/env python3
"""Opt-in compatibility checks with synthetic payloads; no model or native grader."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import uuid

SOURCE: Path
DOCKER_IMAGE: str | None = None


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive(path, content=b'synthetic candidate'):
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as output:
        member = tarfile.TarInfo('payload/solution.patch')
        member.size = len(content)
        output.addfile(member, io.BytesIO(content))
    return path


class Compatibility(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='frontiercs2-contract-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bridge_module = load('submission_bridge')
        self.client_module = load('frontier_submit')
        self.client_module.REQUEST_ATTEMPTS = 1
        self.jobs = queue.Queue()
        self.trial = self.bridge_module.Trial(self.root / 'trial', 'fixture', self.jobs)
        self.trial.start(60)
        self.payload = archive(self.root / 'candidate.tar')
        self.client_module.FINAL_RECEIPT = self.root / 'receipt' / 'final.json'
        self.token_file = self.root / 'capability'
        self.token_file.write_text(self.trial.token)
        self.token_file.chmod(0o600)
        self.server = None
        self.start_server()
        self.addCleanup(self.stop_server)
        environment = patch.dict(os.environ, {
            'FRONTIER_DRIVER_API_URL': self.origin,
            'FRONTIER_DRIVER_TOKEN_FILE': str(self.token_file),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.client = self.client_module.Client()

    def start_server(self, port=0):
        self.server = self.bridge_module.Bridge(('127.0.0.1', port))
        self.server.trials[self.trial.token] = self.trial
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_address[1]}'

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
            self.server = None

    def restart(self):
        port = self.server.server_address[1]
        self.stop_server()
        self.jobs = queue.Queue()
        self.trial = self.bridge_module.Trial(self.root / 'trial', 'fixture', self.jobs)
        self.start_server(port)

    def test_repeat_final_reuses_receipt_and_remote_selection(self):
        first = self.client_module.run_final(self.client, self.payload)
        second = self.client_module.run_final(self.client, self.payload)
        self.assertEqual(first, second)
        self.assertEqual(first['state'], 'accepted')
        self.assertEqual(len(self.trial.jobs), 1)
        self.assertEqual(first['accepted_at'], self.trial.snapshot()['final_at'])
        self.assertEqual(json.loads(self.client_module.FINAL_RECEIPT.read_text()), first)
        self.assertEqual(self.client_module.FINAL_RECEIPT.stat().st_mode & 0o777, 0o600)

    def test_receipt_io_failure_keeps_acceptance_and_recovers(self):
        blocked = self.root / 'not-a-directory'
        blocked.write_text('fixture')
        self.client_module.FINAL_RECEIPT = blocked / 'final.json'
        accepted = self.client_module.run_final(self.client, self.payload)
        self.assertEqual(accepted['state'], 'accepted')
        self.assertTrue(accepted['receipt_pending_recovery'])
        self.client_module.FINAL_RECEIPT = self.root / 'recovered.json'
        recovered = self.client_module.run_final(self.client, self.payload)
        self.assertEqual(recovered['verification_id'], accepted['verification_id'])
        self.assertEqual(recovered['accepted_at'], accepted['accepted_at'])
        self.assertNotIn('receipt_pending_recovery', recovered)
        self.assertEqual(len(self.trial.jobs), 1)

    def test_client_cli_repeats_without_error_or_duplicate_effect(self):
        code = (
            'import importlib.util,pathlib,sys; '
            's=importlib.util.spec_from_file_location("client",sys.argv[1]); '
            'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
            'm.FINAL_RECEIPT=pathlib.Path(sys.argv[2]); '
            'sys.exit(m.main(["final",sys.argv[3]]))'
        )
        argv = [sys.executable, '-c', code, str(SOURCE / 'frontier_submit.py'),
                str(self.client_module.FINAL_RECEIPT), str(self.payload)]
        rows = [subprocess.run(argv, capture_output=True, text=True, timeout=10)
                for _ in range(2)]
        self.assertEqual([r.returncode for r in rows], [0, 0])
        self.assertEqual(json.loads(rows[0].stdout), json.loads(rows[1].stdout))
        self.assertEqual(len(self.trial.jobs), 1)

    def test_restart_preserves_capability_deadline_cost_and_feedback(self):
        result, _ = self.client_module.run_train(self.client, self.payload)
        job_id = result['verification_id']
        self.trial.jobs[job_id].update(state='passed', raw_score=7, metrics={'score_unbounded': 7})
        self.trial.train_wall_seconds = 13.5
        self.trial.persist()
        before_token, before_deadline = self.trial.token, self.trial.deadline
        self.restart()
        self.assertEqual(self.trial.token, before_token)
        self.assertEqual(self.trial.deadline, before_deadline)
        self.assertEqual(self.trial.train_calls, 1)
        self.assertEqual(self.trial.train_wall_seconds, 13.5)
        feedback, status = self.client_module.run_status(self.client, job_id)
        self.assertEqual((status, feedback['raw_score']), (0, 7))
        self.client_module.run_train(self.client, self.payload)
        self.assertEqual(self.trial.train_calls, 1)
        self.assertTrue(self.jobs.empty())
        record = self.root / 'trial' / 'trial-recovery.json'
        self.assertEqual(record.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(before_token, (record.parent / 'submission-state.json').read_text())

    def test_final_replay_after_restart_and_deadline_is_readback(self):
        first = self.client_module.run_final(self.client, self.payload)
        self.trial.deadline = time.time() - 1
        self.trial.persist()
        self.restart()
        second = self.client_module.run_final(self.client, self.payload)
        self.assertEqual(second, first)
        self.assertEqual(len(self.trial.jobs), 1)
        self.assertTrue(self.jobs.empty())
        other = archive(self.root / 'other.tar', b'new candidate')
        with self.assertRaisesRegex(self.client_module.SubmitError, '409'):
            self.client_module.run_final(self.client, other)

    def test_queued_handoff_is_explicit_and_not_repeated(self):
        result, _ = self.client_module.run_train(self.client, self.payload)
        self.restart()
        self.assertTrue(self.jobs.empty())
        self.trial.requeue_pending()
        self.trial.requeue_pending()
        self.assertEqual(self.jobs.get_nowait(), (self.trial, result['verification_id']))
        self.assertTrue(self.jobs.empty())
        self.assertEqual(self.trial.train_calls, 1)

    def test_interrupted_native_worker_requires_reconciliation(self):
        result, _ = self.client_module.run_train(self.client, self.payload)
        self.trial.jobs[result['verification_id']]['state'] = 'running'
        self.trial.train_wall_seconds = 3.5
        self.trial.persist()
        self.restart()
        feedback, status = self.client_module.run_status(self.client, result['verification_id'])
        self.assertEqual(status, 2)
        self.assertEqual(feedback['failure']['code'], 'controller_recovery_required')
        self.assertEqual(self.trial.train_wall_seconds, 3.5)
        self.assertEqual(self.trial.train_calls, 1)
        self.assertTrue(self.jobs.empty())
        with self.assertRaises(self.bridge_module.Rejected):
            self.trial.requeue_pending()
        other = archive(self.root / 'other.tar', b'new candidate')
        with self.assertRaisesRegex(self.client_module.SubmitError, '409'):
            self.client_module.run_train(self.client, other)

    def test_failed_payload_commit_can_adopt_its_immutable_orphan(self):
        real_write = self.trial._write

        def fail_recovery(path, value):
            if path == self.trial.recovery_path:
                raise OSError('synthetic failed commit')
            real_write(path, value)

        with patch.object(self.trial, '_write', side_effect=fail_recovery):
            with self.assertRaises(self.client_module.SubmitError):
                self.client_module.run_train(self.client, self.payload)
        self.assertEqual(self.trial.train_calls, 0)
        self.assertEqual(self.trial.payloads, {})
        self.assertTrue(self.jobs.empty())
        # Reuse the immutable orphan left by the failed upload commit.
        self.client_module.run_train(self.client, self.payload)
        self.assertEqual(self.trial.train_calls, 1)
        self.assertEqual(self.jobs.qsize(), 1)

    @unittest.skipUnless(importlib.util.find_spec('resource'), 'requires POSIX file limits')
    def test_interrupted_payload_write_does_not_poison_retry(self):
        self.stop_server()
        # Kill a separate writer after the filesystem accepts only a prefix.
        # No finally block runs; the next bridge must still accept identical bytes.
        code = '''import hashlib,os,pathlib,queue,resource,signal,sys
sys.path.insert(0, sys.argv[1])
from submission_bridge import Trial
trial = Trial(pathlib.Path(sys.argv[2]), 'fixture', queue.Queue())
body = pathlib.Path(sys.argv[3]).read_bytes()
signal.signal(signal.SIGXFSZ, lambda *_: os._exit(73))
resource.setrlimit(resource.RLIMIT_FSIZE, (128, 128))
trial.request('POST', '/v1/payloads', body,
              {'X-Content-SHA256': 'sha256:'+hashlib.sha256(body).hexdigest(),
               'X-Frontier-Purpose': 'train'})
'''
        result = subprocess.run([sys.executable, '-c', code, str(SOURCE),
                                 str(self.root / 'trial'), str(self.payload)],
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        self.jobs = queue.Queue()
        self.trial = self.bridge_module.Trial(self.root / 'trial', 'fixture', self.jobs)
        self.start_server()
        with patch.dict(os.environ, {'FRONTIER_DRIVER_API_URL': self.origin}):
            client = self.client_module.Client()
            self.client_module.run_train(client, self.payload)
        self.assertEqual(self.trial.train_calls, 1)
        self.assertEqual(self.jobs.qsize(), 1)
        payload = next(iter(self.trial.payloads.values()))
        self.assertEqual(Path(payload['path']).read_bytes(), self.payload.read_bytes())
        self.assertEqual(payload['sha256'], 'sha256:'+hashlib.sha256(self.payload.read_bytes()).hexdigest())

    def test_failed_train_commit_does_not_enqueue_or_consume_an_opportunity(self):
        self.client_module.upload(self.client, 'train', self.payload)
        real_write = self.trial._write

        def fail_admission(path, value):
            if path == self.trial.recovery_path and value['train_calls']:
                raise OSError('synthetic failed admission')
            real_write(path, value)

        with patch.object(self.trial, '_write', side_effect=fail_admission):
            with self.assertRaisesRegex(self.client_module.SubmitError, '503'):
                self.client_module.run_train(self.client, self.payload)
        self.assertEqual(self.trial.train_calls, 0)
        self.assertEqual(self.trial.jobs, {})
        self.assertTrue(self.jobs.empty())
        self.restart()
        self.assertEqual(self.trial.train_calls, 0)
        self.client_module.run_train(self.client, self.payload)
        self.assertEqual(self.trial.train_calls, 1)
        self.assertEqual(self.jobs.qsize(), 1)

    def test_checkpoint_and_explicit_final_selection_survive_restart(self):
        first = self.client_module.run_store_final(self.client, self.payload)
        other = archive(self.root / 'other.tar', b'other candidate')
        second = self.client_module.run_store_final(self.client, other)
        self.restart()
        self.assertEqual(self.trial.saved, second['payload_id'])
        # Preserve an explicit final choice even when it differs from the checkpoint.
        final = self.client_module.run_final(self.client, self.payload)
        self.assertEqual(final['payload_id'], first['payload_id'])
        self.assertEqual(self.trial.saved, second['payload_id'])
        self.assertEqual(self.trial.final, first['payload_id'])

    def test_projection_failure_does_not_reject_a_committed_final(self):
        real_write = self.trial._write

        def fail_projection(path, value):
            if path.name == 'submission-state.json':
                raise OSError('synthetic observation failure')
            real_write(path, value)

        with patch.object(self.trial, '_write', side_effect=fail_projection):
            final = self.client_module.run_final(self.client, self.payload)
        self.assertEqual(final['state'], 'accepted')
        self.restart()
        replay = self.client_module.run_final(self.client, self.payload)
        self.assertEqual(replay, final)
        self.assertEqual(len(self.trial.jobs), 1)

    def test_wrong_capability_purpose_and_final_status_stay_rejected(self):
        auth = self.client.auth
        self.client.auth = 'wrong-fixture-capability'
        with self.assertRaisesRegex(self.client_module.SubmitError, '401'):
            self.client_module.upload(self.client, 'train', self.payload)
        self.client.auth = auth
        payload, _ = self.client_module.upload(self.client, 'train', self.payload)
        with self.assertRaisesRegex(self.client_module.SubmitError, '400'):
            self.client.request('POST', '/v1/submissions',
                                body=json.dumps({'payload_id': payload}).encode())
        final = self.client_module.run_final(self.client, self.payload)
        self.trial.jobs[final['verification_id']].update(state='passed', raw_score=99)
        with self.assertRaisesRegex(self.client_module.SubmitError, '404'):
            self.client_module.run_status(self.client, final['verification_id'])

    def test_restart_rejects_wrong_identity_and_tampered_payload(self):
        self.client_module.upload(self.client, 'train', self.payload)
        with self.assertRaises(ValueError):
            self.bridge_module.Trial(self.root / 'trial', 'other-arm', queue.Queue())
        payload = next(iter(self.trial.payloads.values()))
        path = Path(payload['path'])
        path.chmod(0o600)
        path.write_bytes(b'tampered fixture')
        with self.assertRaises(ValueError):
            self.bridge_module.Trial(self.root / 'trial', 'fixture', queue.Queue())

    def test_original_quota_and_deadline_remain_enforced(self):
        self.trial.train_calls = 100
        with self.assertRaisesRegex(self.client_module.SubmitError, '429'):
            self.client_module.run_train(self.client, self.payload)
        self.trial.train_calls = 0
        self.trial.train_wall_seconds = 180000
        with self.assertRaisesRegex(self.client_module.SubmitError, '429'):
            self.client_module.run_train(self.client, self.payload)
        self.trial.deadline = time.time() - 1
        other = archive(self.root / 'other.tar', b'other candidate')
        with self.assertRaisesRegex(self.client_module.SubmitError, '409'):
            self.client_module.run_train(self.client, other)


class DockerTransport(unittest.TestCase):
    def test_streamed_upload_replaces_readonly_files_with_preserved_modes(self):
        module = load('docker_environment')

        async def check(root):
            env = module.DockerEnvironment('synthetic-local-transport', '')

            async def local_run(argv, *, timeout, data=None):
                # Execute the actual emitted upload program on a real writable
                # directory. Container namespace/UID coverage stays in Docker.
                return await module.DockerEnvironment._run(
                    [sys.executable, *argv[-4:]], timeout=timeout, data=data)

            async def writable(*args, **kwargs):
                return SimpleNamespace(return_code=0)

            with patch.object(env, 'exec', side_effect=writable), patch.object(env, '_run', side_effect=local_run):
                source, target = root / 'source', root / 'target'
                for mode in (0o444, 0o555):
                    for content in (b'first upload', b'replacement bytes'):
                        if source.exists():
                            source.chmod(0o600)
                        source.write_bytes(content)
                        source.chmod(mode)
                        await env.upload_file(source, str(target))
                        self.assertEqual(target.read_bytes(), content)
                        self.assertEqual(target.stat().st_mode & 0o777, mode)
                        self.assertEqual(target.stat().st_uid, os.getuid())

        with tempfile.TemporaryDirectory() as temporary:
            asyncio.run(check(Path(temporary)))

    def test_event_loop_can_release_a_real_child_process(self):
        module = load('docker_environment')

        async def check(root):
            gate = root / 'gate'
            child = 'import pathlib,sys,time; p=pathlib.Path(sys.argv[1]);\nwhile not p.exists(): time.sleep(.01)\nprint("released")'
            task = asyncio.create_task(module.DockerEnvironment._run(
                [sys.executable, '-c', child, str(gate)], timeout=3))
            await asyncio.sleep(0.02)
            gate.touch()
            result = await task
            self.assertEqual(result.stdout.strip(), b'released')
            with self.assertRaises(subprocess.TimeoutExpired):
                await module.DockerEnvironment._run(
                    [sys.executable, '-c', 'import time; time.sleep(30)'], timeout=0.05)

        with tempfile.TemporaryDirectory() as temporary:
            asyncio.run(check(Path(temporary)))

    def test_real_docker_exec_and_both_upload_paths(self):
        if not DOCKER_IMAGE:
            self.skipTest('pass --docker-image to exercise a real isolated container')
        module = load('docker_environment')
        name = 'frontiercs2-contract-' + uuid.uuid4().hex
        # This synthetic transport fixture does not qualify the campaign sandbox.
        bootstrap = 'import os,sys; os.execv(sys.argv[1],sys.argv[1:])'
        subprocess.run(['docker', 'run', '-d', '--rm', '--pull=never', '--name', name,
                        '--network=none', '--cap-drop=ALL',
                        '--memory=128m', '--pids-limit=64',
                        '--tmpfs', '/workspace:rw,uid=10001,gid=10001,mode=0777',
                        DOCKER_IMAGE, 'sleep', '120'],
                       capture_output=True, check=True, timeout=15)
        try:
            subprocess.run(['docker', 'exec', '--user=0', name, '/bin/sh', '-c',
                            'test -x /usr/bin/python3 || ln -s /usr/local/bin/python3 /usr/bin/python3'],
                           capture_output=True, check=True, timeout=10)
            async def check(root):
                env = module.DockerEnvironment(name, bootstrap, sealed=True)
                wait = 'import pathlib,time; p=pathlib.Path("/workspace/gate");\nwhile not p.exists(): time.sleep(.01)\nprint("ready")'
                first, second = await asyncio.gather(
                    env.exec('python3 -c ' + shlex.quote(wait), timeout_sec=4),
                    env.exec('touch /workspace/gate', timeout_sec=4))
                self.assertEqual((first.return_code, first.stdout.strip(), second.return_code), (0, 'ready', 0))
                user = await env.exec('id -u', user='root')
                self.assertEqual(user.stdout.strip(), '10001')
                data = root / 'fixture.txt'
                data.write_text('synthetic transfer')
                await env.upload_file(data, '/workspace/sealed.txt')
                unsealed = module.DockerEnvironment(name, bootstrap)
                await unsealed.upload_file(data, '/workspace/streamed.txt')
                for mode in (0o444, 0o555):
                    data.chmod(mode)
                    await unsealed.upload_file(data, '/workspace/readonly.txt')
                    await unsealed.upload_file(data, '/workspace/readonly.txt')
                    observed = await env.exec('stat -c "%a %u" /workspace/readonly.txt')
                    self.assertEqual(observed.stdout.strip(), f'{mode:o} 10001')
                    observed = await env.exec('cat /workspace/readonly.txt')
                    self.assertEqual(observed.stdout, 'synthetic transfer')
                data.chmod(0o644)
                await unsealed.upload_dir(root, '/workspace/tree')
                await env.upload_dir(root, '/workspace/sealed-tree')
                await unsealed.upload_file(data, '/usr/local/bin/frontier-fixture')
                await unsealed.upload_dir(root, '/opt/frontier-fixture')
                result = await env.exec('cat /workspace/sealed.txt /workspace/streamed.txt '
                                        '/workspace/tree/fixture.txt /workspace/sealed-tree/fixture.txt '
                                        '/usr/local/bin/frontier-fixture /opt/frontier-fixture/fixture.txt')
                self.assertEqual(result.return_code, 0)
                self.assertEqual(result.stdout, 'synthetic transfer' * 6)
            with tempfile.TemporaryDirectory() as temporary:
                asyncio.run(check(Path(temporary)))
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=15)


def main():
    global SOURCE, DOCKER_IMAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--docker-image', help='already-local image with python3 and /bin/bash')
    args = parser.parse_args()
    SOURCE = args.source.resolve()
    DOCKER_IMAGE = args.docker_image
    # The fixtures run against a copy; do not write bytecode into a frozen source.
    sys.dont_write_bytecode = True
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(Compatibility),
                               unittest.defaultTestLoader.loadTestsFromTestCase(DockerTransport)])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
