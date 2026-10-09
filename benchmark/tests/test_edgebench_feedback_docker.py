"""Opt-in qualification with the real native judge, Docker and host firewall.

Run on the Docker Linux host with passwordless iptables sudo:
LOOPX_EDGEBENCH_DOCKER_SMOKE=1 python -m pytest -q <this file>
Requires a locally available python:3.12-slim image. No model calls or task data.
"""
import gzip
import hashlib
import io
import json
import logging
import os
import socket
import tarfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                                reason="Requires explicit isolated Linux Docker qualification")


def wait_for(predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError("Synthetic feedback journey did not finish before its deadline")


@pytest.fixture(scope="module")
def agent_image():
    """Match the native worker's ordinary agent, including its image USER."""
    import docker
    client = docker.from_env()
    base = client.images.get("python:3.12-slim")
    setup = client.containers.run(base.id, ["sleep", "300"], detach=True, user="root")
    try:
        result = setup.exec_run(["sh", "-c", "groupadd -g 1000 agent && useradd -u 1000 -g agent -m agent "
            "&& mkdir -p /logs/agent && chown agent:agent /logs/agent"])
        assert result.exit_code == 0, result.output
        image = setup.commit(conf={"User": "agent"})
    finally:
        setup.remove(force=True)
    try:
        yield image
    finally:
        client.images.remove(image.id)
        client.close()


@pytest.mark.parametrize("selection", ["score_first", "pass_rate_first"])
def test_private_host_checkpoint_is_readable_before_notification(tmp_path, agent_image, selection):
    """Real native copy preserves host permissions; publication must repair only public files."""
    from sforge.harness.backend.docker_backend import DockerBackend
    from benchmark.edgebench.feedback import BestOnlyFeedback, FEEDBACK_FILE

    previous_umask = os.umask(0o077)
    try:
        for n in (2, 3):
            archive = tmp_path / f"submissions/auto-{n}/submission.tar.gz"
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b"synthetic worker source")
        assert archive.stat().st_mode & 0o777 == 0o600
        assert os.getuid() != 1000  # Host and image identity must differ.
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        sampler = SimpleNamespace(admitted=lambda: {
            "s1": {"round_id": "auto-1", "source_sha256": digest},
            "s2": {"round_id": "auto-2", "source_sha256": digest},
            "s3": {"round_id": "auto-3", "source_sha256": digest}})
        publisher = BestOnlyFeedback(trial=tmp_path, run_id="fixture", task_id="fixture",
            direction="maximize", judge_url="http://127.0.0.1:1", admin_secret="synthetic",
            logger=logging.getLogger("source-permission-smoke"), sampler=sampler, selection=selection)
        backend = DockerBackend()
        handle = backend.create_container(agent_image.id, "source-smoke-" + uuid.uuid4().hex[:10])
        backend.start_container(handle)
        try:
            assert backend.exec_run(handle, ["id", "-u"]).output.strip() == "1000"
            # The installer prepares this public directory before the publisher starts.
            assert backend.exec_run(handle, ["sh", "-c", "mkdir -p /opt/edgebench-feedback && "
                "chmod 0755 /opt/edgebench-feedback"], user="root").exit_code == 0
            assert backend.exec_run(handle, ["sh", "-c", "umask 077; "
                "printf synthetic-secret > /tmp/host-only-sentinel"], user="root").exit_code == 0
            values = {"run_id": "fixture", "entries": [dict(type="submission", status="completed",
                valid=True, task_id="fixture", submission_id=f"s{n}", round=f"auto-{n}", score=n,
                pass_rate=0)
                for n in (1, 2)]}
            if selection == "pass_rate_first":
                publisher.update(values, backend, handle)
                publisher.update(values, backend, handle)
                assert backend.exec_run(handle, ["test", "-e", str(FEEDBACK_FILE)]).exit_code != 0
                assert publisher.notifications == publisher.errors == 0
                values["entries"][1]["pass_rate"] = .2
            publisher.update(values, backend, handle)
            packet = json.loads(backend.exec_run(handle, ["cat", str(FEEDBACK_FILE)]).output)
            remote = packet["latest"]["source_archive"]
            source = backend.exec_run(handle, ["cat", remote])
            assert source.exit_code == 0 and source.output == "synthetic worker source"
            # Failed ordinary-agent verification must not publish or advance.
            original_exec = backend.exec_run
            def deny_read(handle, cmd, **kwargs):
                if isinstance(cmd, list) and cmd[0] == "sha256sum":
                    return SimpleNamespace(exit_code=1, output="synthetic unreadable source")
                return original_exec(handle, cmd, **kwargs)
            backend.exec_run = deny_read
            values["entries"].append(dict(type="submission", status="completed", valid=True,
                task_id="fixture", submission_id="s3", round="auto-3", score=3, pass_rate=.3))
            with pytest.raises(RuntimeError, match="source checkpoint"):
                publisher.update(values, backend, handle)
            assert publisher.score == 2 and publisher.notifications == 1
            assert json.loads(original_exec(handle, ["cat", str(FEEDBACK_FILE)]).output) == packet
            backend.exec_run = original_exec
            publisher.update(values, backend, handle)
            packet = json.loads(backend.exec_run(handle, ["cat", str(FEEDBACK_FILE)]).output)
            remote = packet["latest"]["source_archive"]
            assert backend.exec_run(handle, ["cat", remote]).output == "synthetic worker source"
            assert backend.exec_run(handle, ["sha256sum", remote], user="agent").output.split()[0] == digest
            publisher.update(values, backend, handle)
            assert publisher.score == 3 and publisher.notifications == 2
            assert archive.stat().st_mode & 0o777 == 0o600  # Host file remains private.
            assert backend.exec_run(handle, ["cat", "/tmp/host-only-sentinel"], user="agent").exit_code != 0
        finally:
            backend.cleanup_container(handle)
    finally:
        os.umask(previous_umask)


def test_native_judge_to_isolated_worker_positive_only(tmp_path, agent_image, monkeypatch):
    import docker
    import requests
    import uvicorn
    from sforge.harness.config import SForgeConfig
    from sforge.harness.constants import get_admin_secret
    from benchmark.edgebench.online_judge import create_app
    from benchmark.edgebench.online_sampling import OnlineSampler
    from sforge.harness.network_isolation import AllowedEndpoint
    from benchmark.edgebench.feedback import BestOnlyFeedback, FEEDBACK_FILE
    from benchmark.runtime.sforge_backend import RecordingDockerBackend

    name = "feedback-smoke-" + uuid.uuid4().hex[:10]
    client = docker.from_env()
    image = client.images.get("python:3.12-slim")
    judge_tag = f"{name}.judge.fixture:fixture"
    image.tag(f"{name}.judge.fixture", tag="fixture")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "BENCHMARK.yaml").write_text(f"name: {name}\nbase_images: {{python: {{}}}}\n")
    (tasks / "fixture.json").write_text(json.dumps({
        "task_id": "fixture", "name": "Synthetic feedback", "base_image": "python",
        "platform": "linux/arm64", "cwd": "/tmp", "internet": False,
        "submit_paths": ["candidate.json"], "submit_exclude": [],
        "work": {"image_tag": "fixture", "agent_query": "Synthetic fixture"},
        "judge": {"image_tag": "fixture", "eval_timeout": 15, "parser": "structured_json",
                  "selection": "score_first", "score_direction": "maximize",
                  "eval_cmd": "python3 -c 'import json,time; d=json.load(open(\"candidate.json\")); "
                              "time.sleep(d.get(\"delay\",0)); print(json.dumps(dict(score=d[\"value\"], valid=True, "
                              "summary=\"JUDGE_DIAGNOSTIC_SENTINEL\")))'"},
    }))
    config = SForgeConfig(tasks_dir=tasks, log_dir=tmp_path, judge_cpu_limit=1, judge_mem_limit="256m")
    app = create_app(config, slots=2, reservation={"slots": 2})
    # Keep one real native grade nonterminal independently of host Docker
    # cleanup latency. Grading still uses its actual backend and task parser.
    from sforge.harness import judge_server
    native_grade = judge_server.judge_submission
    finish_held_grade = threading.Event()
    def held_grade(**kwargs):
        with tarfile.open(fileobj=io.BytesIO(kwargs["archive"])) as archive:
            held = json.load(archive.extractfile("candidate.json")).get("held", False)
        report = native_grade(**kwargs)
        if held:
            assert finish_held_grade.wait(300), "Native drain probe was not released"
        return report
    monkeypatch.setattr(judge_server, "judge_submission", held_grade)
    sock = socket.socket()
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    server_thread.start()
    wait_for(lambda: server.started)
    session = requests.Session()
    session.trust_env = False
    url = f"http://127.0.0.1:{port}"
    secret = get_admin_secret(tmp_path)
    response = session.post(url + "/api/v1/register", json={
        "task_id": "fixture", "run_id": name, "admin_secret": secret,
        "max_agent_submissions": 0}, timeout=5)
    response.raise_for_status()
    token = response.json()["token"]
    trial = tmp_path / "runs" / name / "fixture"
    trial.mkdir(parents=True)
    logger = logging.getLogger(name)
    sampler = OnlineSampler(trial=trial, task=None, interval=3600, judge_url=url,
                            secret=secret, logger=logger)
    sampler.qualify()
    publisher = BestOnlyFeedback(trial=trial, run_id=name, task_id="fixture", direction="maximize",
                                judge_url=url, admin_secret=secret, logger=logger, sampler=sampler)
    backend = RecordingDockerBackend(log_dir=trial / "collected", logger=logger, oauth_proxy=True,
                                     blind_api_endpoint=("192.0.2.10", 443), feedback=publisher)
    handle, isolation = None, None
    try:
        handle = backend.create_container(agent_image.id, name, environment={
            "SFORGE_TOKEN": token, "SFORGE_JUDGE_URL": url})
        backend.start_container(handle)
        gateway = backend.get_container_gateway_ip(handle)
        # A real listening judge is reachable before policy and denied afterward.
        connect = ["python3", "-c", f"import socket; socket.create_connection(('{gateway}', {port}), 1).close()"]
        assert backend.exec_run(handle, connect).exit_code == 0
        isolation = backend.create_network_isolation(handle, [
            AllowedEndpoint(ip=gateway, port=port, hostname="judge"),
            AllowedEndpoint(ip="192.0.2.10", port=443, hostname="fixture-api"),
        ], logger)
        isolation.apply()
        assert backend.exec_run(handle, connect).exit_code != 0
        assert backend.exec_run(handle, ["sh", "-c",
            "test -z \"$SFORGE_TOKEN\" && test -z \"$SFORGE_JUDGE_URL\""]).exit_code == 0
        # Even possession of the host token cannot grant an agent submission.
        denied = session.post(url + "/api/v1/submit", data={"token": token},
                              files={"archive": ("source.tar.gz", b"unused")}, timeout=5)
        assert denied.status_code == 403
        backend.start_feedback(handle)

        def read_packet():
            result = backend.exec_run(handle, ["cat", str(FEEDBACK_FILE)])
            assert result.exit_code == 0
            return json.loads(result.output)

        def submit(value):
            content = json.dumps({"value": value}).encode()
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as bundle:
                entry = tarfile.TarInfo("candidate.json")
                entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
            sampler.capture(stream.getvalue())
            history = session.get(url + "/api/v1/history", params={"token": token, "admin_secret": secret}, timeout=5).json()
            sampler.tick(history)
            current = sampler.inflight
            identifier = current["submission_id"]
            wait_for(lambda: session.get(url + "/api/v1/result/" + identifier, timeout=5).json()["status"] == "completed")
            return current["round_id"]

        assert read_packet()["latest"] is None
        submit(1)
        wait_for(lambda: publisher.score == 1)
        assert read_packet()["latest"] is None
        winning_round = submit(3)
        wait_for(lambda: publisher.notifications == 1)
        packet = read_packet()
        assert packet["latest"]["snapshot_id"] == winning_round
        assert "JUDGE_DIAGNOSTIC_SENTINEL" not in json.dumps(packet)
        source = backend.exec_run(handle, ["tar", "-xzOf", packet["latest"]["source_archive"], "candidate.json"])
        assert source.exit_code == 0 and json.loads(source.output) == {"value": 3}
        digest = backend.exec_run(handle, ["sha256sum", packet["latest"]["source_archive"]])
        assert digest.output.split()[0] == packet["latest"]["source_sha256"]
        submit(2)
        # Observe a full publisher poll after the regression completes.
        time.sleep(11)
        assert read_packet() == packet and publisher.notifications == 1
        assert publisher.errors == 0
        backend.start_feedback(handle)
        assert read_packet() == packet  # Native resume does not erase the signal.
        # Reserved run B is never queued behind repeated submissions from A.
        second = session.post(url + "/api/v1/register", json={"task_id": "fixture", "run_id": name + "-b",
            "admin_secret": secret, "max_agent_submissions": 0}, timeout=5)
        second.raise_for_status()
        full = session.post(url + "/api/v1/register", json={"task_id": "fixture", "run_id": name + "-c",
            "admin_secret": secret, "max_agent_submissions": 0}, timeout=5)
        assert full.status_code == 503

        def online(token_value, capture_id, held=False, value=1, epoch=None):
            content = json.dumps({"value": value, "held": held}).encode()
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as bundle:
                entry = tarfile.TarInfo("candidate.json")
                entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
            return session.post(url + "/api/v1/best-only/submit", data={"token": token_value,
                "admin_secret": secret, "epoch_id": epoch or sampler.epoch, "capture_id": capture_id},
                files={"archive": ("source.tar.gz", gzip.compress(stream.getvalue(), mtime=0))}, timeout=5)

        slow = online(token, "capture-100", held=True)
        slow.raise_for_status()
        assert online(token, "capture-101").status_code == 503
        fast = online(second.json()["token"], "capture-1")
        fast.raise_for_status()
        wait_for(lambda: session.get(url + "/api/v1/result/" + fast.json()["submission_id"], timeout=5).json()["status"] == "completed")
        assert session.get(url + "/api/v1/result/" + slow.json()["submission_id"], timeout=5).json()["status"] == "running"
        assert online(token, "capture-100", held=True).json() == slow.json()
        assert online(token, "capture-100", value=999).status_code == 409
        assert online(token, "capture-101", epoch="previous-process").status_code == 409
        release_data = dict(run_id=name, task_id="fixture", epoch_id=sampler.epoch,
                            admin_secret=secret)
        release_url = url + "/api/v1/best-only/release"
        assert session.post(release_url, data={**release_data, "admin_secret": "wrong"}).status_code == 403
        assert session.post(release_url, data={**release_data, "epoch_id": "old"}).status_code == 409
        assert session.post(release_url, data={**release_data, "run_id": "unknown"}).status_code == 404
        released = session.post(release_url, data=release_data)
        released.raise_for_status()
        assert released.json()["state"] == "draining"
        assert online(token, "capture-101").status_code == 410
        assert online(token, "capture-100", held=True).json() == slow.json()
        assert session.post(url + "/api/v1/register", json={"task_id": "fixture", "run_id": name + "-c",
            "admin_secret": secret, "max_agent_submissions": 0}).status_code == 503
        finish_held_grade.set()
        wait_for(lambda: session.get(url + "/api/v1/result/" + slow.json()["submission_id"], timeout=5).json()["status"] == "completed")
        replacement = session.post(url + "/api/v1/register", json={"task_id": "fixture", "run_id": name + "-c",
            "admin_secret": secret, "max_agent_submissions": 0})
        replacement.raise_for_status()
        assert replacement.json()["token"] not in (token, second.json()["token"])
        assert session.post(release_url, data=release_data).json()["state"] == "released"
        assert session.post(url + "/api/v1/register", json={"task_id": "fixture", "run_id": name,
            "admin_secret": secret, "max_agent_submissions": 0}).status_code == 409
        assert online(token, "capture-102").status_code == 410
        admission = session.get(url + "/api/v1/best-only/admission", params={"admin_secret": secret}).json()
        assert admission["admitted"] == 2 and admission["registrations_total"] == 3
        history = session.get(url + "/api/v1/history", params={"token": token, "admin_secret": secret}).json()
        assert any(row.get("submission_id") == slow.json()["submission_id"] for row in history["entries"])
        # Registration can succeed before container creation/feedback startup.
        failed_trial = tmp_path / "runs" / (name + "-c") / "fixture"
        failed_trial.mkdir(parents=True)
        failed_sampler = OnlineSampler(trial=failed_trial, task=None, interval=3600,
            judge_url=url, secret=secret, logger=logger)
        failed_sampler.qualify()
        failed_publisher = BestOnlyFeedback(trial=failed_trial, run_id=name + "-c", task_id="fixture",
            direction="maximize", judge_url=url, admin_secret=secret, logger=logger, sampler=failed_sampler)
        failed_backend = RecordingDockerBackend(log_dir=failed_trial / "collected", logger=logger,
                                                oauth_proxy=True, feedback=failed_publisher)
        failed_backend.cleanup_container(None)
        assert failed_publisher.token is None and failed_publisher.thread is None
        assert json.loads((failed_sampler.directory / "release.json").read_text())["state"] == "released"
        assert online(replacement.json()["token"], "capture-1").status_code == 410
        recovered = session.post(url + "/api/v1/register", json={"task_id": "fixture", "run_id": name + "-d",
            "admin_secret": secret, "max_agent_submissions": 0})
        recovered.raise_for_status()
        # These direct service probes are deliberately not sampler-admitted;
        # even an extra native history row cannot alter the online incumbent.
        time.sleep(11)
        assert publisher.notifications == 1 and publisher.score == 3
        # Exercise the real periodic capture loop against the live workspace.
        task = app.state.judge.tasks["fixture"]
        write = backend.exec_run(handle, ["python3", "-c",
            "import pathlib;pathlib.Path('/tmp/candidate.json').write_text('{\"value\":4}')"])
        assert write.exit_code == 0
        capture_trial = tmp_path / "capture-probe"
        capture_trial.mkdir()
        probe = OnlineSampler(trial=capture_trial, task=task, interval=0.2,
                              judge_url=url, secret=secret, logger=logger)
        probe.qualify()
        probe.start(backend, handle, token)
        try:
            wait_for(lambda: len(probe.records) >= 2)
        finally:
            probe.close()
        assert probe.records[0]["status"] == "superseded_online"
        assert probe.records[-1]["status"] == "pending"
        assert probe.admitted() == {}
        with tarfile.open(probe.directory / (probe.records[0]["capture_id"] + ".tar.gz")) as source:
            assert json.load(source.extractfile("candidate.json")) == {"value": 4}

        # A superseded/pending high score is scored only after the solver and
        # online service stop; offline grading cannot send model feedback.
        publisher.pause()
        content = json.dumps({"value": 10}).encode()
        bundle_bytes = io.BytesIO()
        with tarfile.open(fileobj=bundle_bytes, mode="w:gz") as bundle:
            member = tarfile.TarInfo("candidate.json")
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))
        sampler.capture(bundle_bytes.getvalue())
        (trial / "final_archive.tar.gz").write_bytes(bundle_bytes.getvalue())
        publisher.close()
        isolation.cleanup()
        isolation = None
        backend.cleanup_container(handle)
        handle = None
        server.should_exit = True
        server_thread.join(timeout=10)
        assert not server_thread.is_alive()
        import hashlib
        (trial / "runtime-receipt.json").write_text(json.dumps(dict(status="terminal", feedback="best-only",
            task="fixture", run_id=name, task_sha256=hashlib.sha256((tasks / "fixture.json").read_bytes()).hexdigest())))
        from benchmark.edgebench.offline_scoring import score_captures
        from benchmark.edgebench.online_judge import cohort_lock
        # This synthetic native service owns its own test pool; never acquire
        # or release the operator's active cohort lock during qualification.
        with monkeypatch.context() as isolated:
            isolated.setattr(Path, "home", lambda: tmp_path / "isolated-home")
            with cohort_lock():
                scored = score_captures(trial, app.state.judge.tasks["fixture"], config, app.state.judge.backend)
        assert scored["offline_scoring_complete"] and scored["evaluated_captures"] == 5
        assert scored["best_score"] == 10
        assert publisher.score == 3 and publisher.notifications == 1
    finally:
        finish_held_grade.set()
        publisher.close()
        if isolation is not None:
            isolation.cleanup()
        if handle is not None:
            backend.cleanup_container(handle)
        server.should_exit = True
        server_thread.join(timeout=10)
        session.close()
        client.images.remove(judge_tag)


def test_codex_hook_enters_next_model_request_and_resume(tmp_path, agent_image):
    """Real staged Codex, system hook and Docker; synthetic Responses, no paid model."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import docker
    from benchmark.edgebench import feedback_hook

    payload = Path(os.environ['LOOPX_TEST_CODEX_DIR']).resolve()
    requests = []
    client = docker.from_env()
    worker = client.containers.run(agent_image.id, ['sleep', '300'], detach=True,
        volumes={str(payload): {'bind': '/opt/codex-bin', 'mode': 'ro'}})
    root = '/opt/edgebench-feedback'
    digest = 'a' * 64

    def write(path, data):
        result = worker.exec_run(['python3', '-c',
            'import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);'
            'p.write_text(sys.argv[2]);p.chmod(0o644)', path, data], user='root')
        assert result.exit_code == 0, result.output

    def notify(n):
        archive = f'{root}/auto-{n}-{digest}.tar.gz'
        write(archive, 'synthetic source')
        write(f'{root}/latest.json', json.dumps({'schema_version': 'edgebench_best_feedback_v1',
            'latest': {'kind': 'new_best', 'snapshot_id': f'auto-{n}', 'source_sha256': digest,
                       'source_archive': archive}}))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            index = len(requests)
            if index == 1:
                notify(2)  # Arrives after startup, before the tool finishes.
                item = {'type': 'function_call', 'id': 'fc_test', 'call_id': 'call_test',
                        'name': 'exec_command', 'arguments': json.dumps({'cmd': 'printf LOCAL_TOOL_OK'})}
            else:
                item = {'id': f'msg_{index}', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'Done.', 'annotations': []}]}
            response = {'id': f'resp_{index}', 'object': 'response', 'model': 'gpt-5.4',
                        'status': 'completed', 'output': [item],
                        'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
            events = [
                {'type': 'response.created', 'response': response | {'status': 'in_progress', 'output': []}},
                {'type': 'response.output_item.added', 'output_index': 0, 'item': item},
                {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                {'type': 'response.completed', 'response': response}]
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for event in events:
                self.wfile.write(('data: ' + json.dumps(event) + '\n\n').encode())
            self.wfile.flush()

    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        worker.reload()
        gateway = next(iter(worker.attrs['NetworkSettings']['Networks'].values()))['Gateway']
        write(f'{root}/latest.json', json.dumps({'schema_version': 'edgebench_best_feedback_v1', 'latest': None}))
        write(f'{root}/hook.py', Path(feedback_hook.__file__).read_text())
        assert worker.exec_run(['sh', '-c', f'chmod 0600 {root}/hook.py; umask 077; '
            f'python3 {root}/hook.py --install'], user='root').exit_code == 0
        assert worker.exec_run(['python3', '-c', 'from pathlib import Path; '
            f'Path("{root}/hook.py").read_bytes();Path("/etc/codex/hooks.json").read_bytes()']).exit_code == 0
        write('/tmp/codex-home/config.toml', f'''model = "gpt-5.4"
model_provider = "fixture"
[model_providers.fixture]
name = "fixture"
base_url = "http://{gateway}:{server.server_port}/v1"
wire_api = "responses"
env_key = "FIXTURE_API_KEY"
''')
        assert worker.exec_run(['chown', '-R', 'agent:agent', '/tmp/codex-home'], user='root').exit_code == 0
        env = {'CODEX_HOME': '/tmp/codex-home', 'FIXTURE_API_KEY': 'synthetic',
               'PATH': '/opt/codex-bin:/usr/local/bin:/usr/bin:/bin'}
        command = ['/opt/codex-bin/codex', 'exec', '--skip-git-repo-check',
                   '--dangerously-bypass-approvals-and-sandbox', '--json', '-c', 'features.code_mode=false']
        result = worker.exec_run(command + ['Run the local check then finish.'], environment=env, workdir='/tmp')
        assert result.exit_code == 0, result.output.decode()[-5000:]
        assert len(requests) == 2, result.output.decode()[-5000:]
        first, second = (json.dumps(req['input']) for req in requests)
        assert 'EdgeBench new-best feedback' not in first
        assert 'EdgeBench new-best feedback' in second and 'auto-2' in second
        assert 'LOCAL_TOOL_OK' in second  # Informational hook preserves tool result.
        notify(3)
        result = worker.exec_run(command[:2] + ['resume', '--last'] + command[2:] + ['Continue.'],
                                 environment=env, workdir='/tmp')
        assert result.exit_code == 0, result.output.decode()[-5000:]
        assert len(requests) == 3 and 'auto-3' in json.dumps(requests[-1]['input'])
        assert 'PRIVATE_SCORE' not in json.dumps(requests)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        worker.remove(force=True)
