"""Actual native HTTP and typed artifact transport; grading is synthetic here."""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import socket
import tarfile
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmark.edgebench import checkpoint_controller as transport


@contextmanager
def server(app):
    import uvicorn
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    host = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                      log_level="error", access_log=False))
    thread = threading.Thread(target=host.run)
    thread.start()
    try:
        wait(lambda: host.started)
        yield f"http://127.0.0.1:{port}"
    finally:
        host.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


def wait(predicate):
    until = time.monotonic() + 30
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("Native synthetic HTTP journey did not settle")


@pytest.fixture
def controllers(tmp_path, monkeypatch):
    pytest.importorskip("sforge.harness.checkpoint", reason="Requires native checkpoint API source pin")
    from sforge.harness import judge_server as native
    from sforge.harness.config import SForgeConfig
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "BENCHMARK.yaml").write_text("name: transport-test\nbase_images: {python: {}}\n")
    (tasks / "fixture.json").write_text(json.dumps({
        "task_id": "fixture", "name": "Synthetic transport", "base_image": "python",
        "platform": "linux/arm64", "cwd": "/tmp", "internet": False,
        "submit_paths": ["candidate.json"], "submit_exclude": [],
        "work": {"image_tag": "fixture", "agent_query": "Synthetic fixture"},
        "judge": {"image_tag": "fixture", "eval_timeout": 15, "parser": "structured_json",
                  "selection": "score_first", "score_direction": "maximize", "eval_cmd": "true"},
    }))
    backend = Mock(backend_name="docker")
    backend.client.images.get.return_value.id = "sha256:synthetic-transport-image"
    monkeypatch.setattr(native, "create_backend_from_config", lambda config: backend)
    grades = []
    def grade(**kwargs):
        grades.append(kwargs["archive"])
        kwargs["log_dir"].mkdir(parents=True, exist_ok=True)
        (kwargs["log_dir"] / "submission.tar.gz").write_bytes(kwargs["archive"])
        return SimpleNamespace(to_dict=lambda: {"score": float(kwargs["archive"]),
            "valid": True, "pass_rate": 1.0, "passed": 1, "failed": 0, "total_tests": 1})
    monkeypatch.setattr(native, "judge_submission", grade)
    config = SForgeConfig(tasks_dir=tasks, log_dir=tmp_path / "source")
    source = native.create_app(config, checkpoints=True)
    destination = native.create_app(replace(config, log_dir=tmp_path / "branch"), checkpoints=True)
    try:
        with server(source) as first, server(destination) as second:
            files = []
            for name, app, url in (("source", source, first), ("branch", destination, second)):
                bank = app.state.judge.config.log_dir / "checkpoints"
                bank.mkdir(parents=True, mode=0o700)
                path = tmp_path / (name + "-controller.json")
                path.write_text(json.dumps({"url": url, "admin_secret": app.state.judge.admin_secret,
                                            "bank_root": str(bank)}))
                path.chmod(0o600)
                files.append(path)
            yield source, destination, first, second, files, grades
    finally:
        for app in (source, destination):
            app.state.judge._reaper_stop.set()
            app.state.judge._reaper_thread.join(timeout=1)


def register(app, url, **options):
    import requests
    response = requests.post(url + "/api/v1/register", json={"task_id": "fixture", "run_id": "source",
        "admin_secret": app.state.judge.admin_secret, **options}, timeout=5)
    response.raise_for_status()
    return response.json()["token"]


def submit(url, token, score):
    import requests
    response = requests.post(url + "/api/v1/submit", data={"token": token},
        files={"archive": ("candidate.tar.gz", str(score).encode())}, timeout=5)
    if response.status_code == 200:
        identifier = response.json()["submission_id"]
        wait(lambda: requests.get(url + "/api/v1/result/" + identifier, timeout=5).json()["status"] == "completed")
    return response


def test_native_controller_transport_preserves_original_point_and_fresh_authority(controllers, tmp_path, capsys):
    import requests
    source, destination, first, second, files, grades = controllers
    token = register(source, first, max_agent_submissions=3, submission_cooldown=0)
    assert submit(first, token, 7).status_code == 200
    assert submit(first, token, 3).status_code == 200
    # Retain a future cooldown without changing the original accepted clock.
    source.state.judge.tokens[token]["submission_cooldown"] = 3600
    before = copy.deepcopy((source.state.judge.tokens, source.state.judge.submissions, source.state.judge.run_history))
    token_file = tmp_path / "token"
    token_file.write_text(token)
    exported = tmp_path / "exported"
    transport.main(["--controller-file", str(files[0]), "capture", "--token-file", str(token_file),
                    "--capture-id", "a" * 32, "--destination", str(exported)])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["research_point_qualified"] is False
    assert receipt["native_reference"]["incumbent"]["round"] == "agent-1"
    assert (exported / "components/native_judge/incumbent.tar.gz").read_bytes() == b"7"
    manifest = json.loads((exported / "components/native_judge/manifest.json").read_text())
    assert set(json.loads((exported / "checkpoint.json").read_bytes())["point_metadata"]) == {"native_reference"}
    assert not (source.state.judge.config.log_dir / "checkpoints/.loopx-phase-attempts").exists()
    assert len(manifest["history"]) == 2 and manifest["opportunity"]["next_agent"] == 3
    assert 0 < manifest["opportunity"]["remaining_cooldown_seconds"] <= 3600
    for secret in (token, source.state.judge.admin_secret, destination.state.judge.admin_secret):
        assert secret not in json.dumps(receipt)
        assert all(secret.encode() not in p.read_bytes() for p in exported.rglob("*") if p.is_file())
    assert (source.state.judge.tokens, source.state.judge.submissions, source.state.judge.run_history) == before
    # Later source state cannot replace this first native capture identity.
    source.state.judge.tokens[token]["submission_cooldown"] = 0
    assert submit(first, token, 99).status_code == 200
    repeated = transport.capture(files[0], token=token, capture_id="a" * 32, destination=tmp_path / "same-point")
    assert repeated["native_reference"] == receipt["native_reference"]
    private_result = tmp_path / "fresh-result.json"
    transport.main(["--controller-file", str(files[1]), "restore", "--root", str(exported),
        "--expected-sha256", receipt["sha256"], "--run-id", "branch", "--result-file", str(private_result)])
    output = capsys.readouterr().out
    fresh = json.loads(private_result.read_text())["token"]
    assert fresh != token and fresh not in output
    assert private_result.stat().st_mode & 0o077 == 0 and len(grades) == 3
    history = requests.get(second + "/api/v1/history", params={"token": fresh}, timeout=5).json()
    assert history["best_score"] == 7 and history["best_round"] == "agent-1" and history["agent_submissions"] == 2
    assert submit(second, fresh, 1).status_code == 429
    assert source.state.judge.get_run_history("source", "fixture")["best_score"] == 99
    with pytest.raises(ValueError, match="HTTP 409"):
        transport.restore(files[1], root=exported, expected_sha256=receipt["sha256"], run_id="branch")
    assert len(grades) == 3


def host_phase_command(tmp_path, monkeypatch):
    """Real command builder; only installation/inference are outside this oracle."""
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import BenchmarkCodex, SForgeWorker

    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-not-read")
    logs = tmp_path / "phase-worker"
    logs.mkdir()
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile="heartbeat-resume", cwd="/task", timeout_seconds=43200, task_entry="seeded-todo")
    worker.install_stop_hook(None, None, logs, None)
    worker.prepared = True
    # The provider's own phase-file/admission shell executes. Its following
    # model command is excluded explicitly; candidate writing below is synthetic.
    return worker.format_run_cmd("/task.md").split(
        "set +e; timeout --signal=TERM --kill-after=30 ${remaining}s env", 1)[0]


def test_host_phase_and_native_opportunities_continue_without_offline_refill(
        controllers, tmp_path, monkeypatch, capsys):
    import requests
    source, destination, first, second, files, grades = controllers
    token = register(source, first, max_agent_submissions=3, submission_cooldown=2)
    assert submit(first, token, 7).status_code == 200
    before = copy.deepcopy((source.state.judge.tokens, source.state.judge.submissions, source.state.judge.run_history))
    # An already admitted Host has 2h left from its original 12h configured
    # trial. Fixture seeding represents that prior elapsed research, not a new run.
    source_phase = tmp_path / "source-phase"
    source_phase.write_text(f"{int(time.time()) + 7200}\n")
    source_bytes = source_phase.read_bytes()
    unit = tmp_path / "unit"
    point = transport.capture(files[0], token=token, capture_id="c" * 32,
                              destination=unit, phase_deadline_file=source_phase)
    body = json.loads((unit / "checkpoint.json").read_bytes())
    retained = body["point_metadata"]["host_phase_budget"]["remaining_seconds"]
    assert 7190 < retained <= 7200
    prefix = host_phase_command(tmp_path, monkeypatch)
    control = tmp_path / "host-control"
    control.mkdir()
    phase = control / "phase-deadline"
    # A destination Host clock 8h later must not expire a copied source epoch
    # or mint the configured 12h allowance. Native uses its real server clock.
    clock = time.time() + 28800
    monkeypatch.setattr(transport, "time", SimpleNamespace(time=lambda: clock))
    private_result = tmp_path / "branch-result"
    transport.main(["--controller-file", str(files[1]), "restore", "--root", str(unit),
        "--expected-sha256", point["sha256"], "--run-id", "phase-branch", "--result-file", str(private_result),
        "--phase-deadline-file", str(phase)])
    fresh = json.loads(private_result.read_bytes())["token"]
    assert fresh not in capsys.readouterr().out and phase.stat().st_mode & 0o077 == 0
    assert 0 <= clock + retained - int(phase.read_bytes()) < 1
    bin = tmp_path / "bin"
    bin.mkdir()
    date = bin / "date"
    date.write_text(f"#!/bin/sh\nprintf '%s\\n' {int(clock)}\n")
    date.chmod(0o700)
    candidate = tmp_path / "continued-candidate"
    shell = prefix.replace("/opt/loopx-benchmark/control", str(control))
    result = subprocess.run(["/bin/bash", "-c", shell +
        f"printf '8' > '{candidate}'; printf '%s' \"$remaining\""],
        env={"PATH": str(bin) + ":/usr/bin:/bin"}, capture_output=True, text=True, check=True)
    assert 7180 < int(result.stdout) <= 7200 and candidate.read_bytes() == b"8"
    history = requests.get(second + "/api/v1/history", params={"token": fresh}, timeout=5).json()
    assert history["agent_submissions"] == 1 and history["best_score"] == 7
    assert submit(second, fresh, 8).status_code == 429
    time.sleep(body["point_metadata"]["native_reference"]["opportunity"]["remaining_cooldown_seconds"] + .05)
    assert submit(second, fresh, float(candidate.read_bytes())).status_code == 200
    history = requests.get(second + "/api/v1/history", params={"token": fresh}, timeout=5).json()
    assert history["agent_submissions"] == 2 and history["best_round"] == "agent-2" and history["best_score"] == 8
    assert source_phase.read_bytes() == source_bytes
    assert (source.state.judge.tokens, source.state.judge.submissions, source.state.judge.run_history) == before
    assert len(grades) == 2  # No implicit grading of the retained incumbent.
    with pytest.raises(ValueError, match="fresh physical"):
        transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
                          run_id="second-branch", phase_deadline_file=phase)
    assert len(destination.state.judge.tokens) == 1 and len(grades) == 2


def test_explicit_phase_extension_is_first_activation_bound_without_native_refill(
        controllers, tmp_path, monkeypatch):
    import requests
    source, destination, first, second, files, grades = controllers
    token = register(source, first, max_agent_submissions=3, submission_cooldown=3600)
    assert submit(first, token, 7).status_code == 200
    phase = tmp_path / "source-phase"
    phase.write_text(f"{int(time.time()) + 7200}\n")
    original = phase.read_bytes()
    unit = tmp_path / "unit"
    point = transport.capture(files[0], token=token, capture_id="d" * 32,
                              destination=unit, phase_deadline_file=phase)
    retained = json.loads((unit / "checkpoint.json").read_bytes())["point_metadata"]["host_phase_budget"]["remaining_seconds"]
    target = tmp_path / "continued-phase"
    started = time.time()
    result = transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
        run_id="extended-branch", phase_deadline_file=target, phase_extension_seconds=64800)
    deadline = int(target.read_text())
    assert started + retained + 64800 - 1 <= deadline <= time.time() + retained + 64800
    binding = json.loads(target.with_name(target.name + ".restore.json").read_text())
    assert binding["binding"]["phase_extension_seconds"] == 64800
    history = requests.get(second + "/api/v1/history", params={"token": result["token"]}, timeout=5).json()
    assert history["agent_submissions"] == 1 and history["best_score"] == 7
    assert submit(second, result["token"], 8).status_code == 429
    info = destination.state.judge.tokens[result["token"]]
    assert info["next_agent"] == 2 and info["next_auto"] == 1
    assert info["max_agent_submissions"] == 3 and info["submission_cooldown"] == 3600
    accepted_clock = info["last_agent_submit_at"]
    # A delayed readback must retain the first activation's deadline and native timer.
    clock = time.time() + 120
    monkeypatch.setattr(transport, "time", SimpleNamespace(time=lambda: clock))
    assert transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
        run_id="extended-branch", phase_deadline_file=target, recover=True,
        phase_extension_seconds=64800) == result
    assert int(target.read_text()) == deadline and info["last_agent_submit_at"] == accepted_clock
    for changed in (0, 64801):
        with pytest.raises(ValueError, match="original activation"):
            transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
                run_id="extended-branch", phase_deadline_file=target, recover=True,
                phase_extension_seconds=changed)
    assert phase.read_bytes() == original and len(grades) == 1
    assert len(destination.state.judge.tokens) == 1


@pytest.mark.parametrize("extension", [-1, True, 1.5, None, 2 ** 53])
def test_invalid_phase_extension_rejects_before_controller_access(tmp_path, extension):
    with pytest.raises(ValueError, match="non-negative integer"):
        transport.restore(tmp_path / "absent-controller", root=tmp_path / "absent-point",
            expected_sha256="a" * 64, run_id="branch", phase_deadline_file=tmp_path / "phase",
            phase_extension_seconds=extension)
    assert not (tmp_path / "phase").exists()


def test_phase_extension_requires_a_captured_clock_before_controller_access(tmp_path):
    with pytest.raises(ValueError, match="captured Host phase clock"):
        transport.restore(tmp_path / "absent-controller", root=tmp_path / "absent-point",
            expected_sha256="a" * 64, run_id="branch", phase_extension_seconds=64800)


@pytest.mark.parametrize("fault", ["missing_clock", "exhausted", "existing", "alias", "modified"])
def test_host_phase_faults_refuse_before_native_admission(controllers, tmp_path, fault):
    source, destination, first, second, files, grades = controllers
    token = register(source, first)
    assert submit(first, token, 7).status_code == 200
    phase_source = tmp_path / "source-phase"
    phase_source.write_text(f"{int(time.time()) + (10 if fault == 'exhausted' else 7200)}\n")
    unit = tmp_path / "unit"
    point = transport.capture(files[0], token=token, capture_id="d" * 32,
                              destination=unit, phase_deadline_file=phase_source)
    target = tmp_path / "phase"
    if fault == "existing":
        target.write_text("keep")
    elif fault == "alias":
        target.symlink_to(phase_source)
    elif fault == "modified":
        (unit / "components/host_phase_deadline").write_text(f"{int(time.time()) + 43200}\n")
    with pytest.raises((ValueError, RuntimeError)):
        transport.restore(files[1], root=unit, expected_sha256=point["sha256"], run_id="phase-branch",
                          phase_deadline_file=None if fault == "missing_clock" else target)
    assert destination.state.judge.tokens == {} and len(grades) == 1
    if fault == "existing":
        assert target.read_text() == "keep"


@pytest.mark.parametrize("fault", ["source_moves", "response_lost", "empty_response"])
def test_phase_ambiguity_never_replaces_point_or_refills_budget(controllers, tmp_path, monkeypatch, fault):
    source, destination, first, second, files, grades = controllers
    token = register(source, first)
    phase_source = tmp_path / "source-phase"
    phase_source.write_text(f"{int(time.time()) + 7200}\n")
    unit = tmp_path / "unit"
    native_post = transport._post
    def post(controller, route, body, timeout):
        result = native_post(controller, route, body, timeout)
        if route.endswith("/capture") and fault == "source_moves":
            phase_source.write_text(f"{int(time.time()) + 43200}\n")
        if route.endswith("/restore"):
            if fault == "response_lost":
                raise TimeoutError("Synthetic transport lost a committed native result")
            if fault == "empty_response":
                return {}
        return result
    monkeypatch.setattr(transport, "_post", post)
    if fault == "source_moves":
        with pytest.raises(ValueError, match="moved"):
            transport.capture(files[0], token=token, capture_id="e" * 32,
                              destination=unit, phase_deadline_file=phase_source)
        assert not unit.exists()
        with pytest.raises(ValueError, match="Existing native point"):
            transport.capture(files[0], token=token, capture_id="e" * 32,
                              destination=unit, phase_deadline_file=phase_source)
        assert destination.state.judge.tokens == {}
    else:
        point = transport.capture(files[0], token=token, capture_id="e" * 32,
                                  destination=unit, phase_deadline_file=phase_source)
        control = tmp_path / "host-control"
        control.mkdir()
        target = control / "phase-deadline"
        with pytest.raises((TimeoutError, ValueError)):
            transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
                              run_id="phase-branch", phase_deadline_file=target)
        assert target.read_bytes() == b"" and len(destination.state.judge.tokens) == 1
        # Existing SForge entry treats empty as expired, never initializes a
        # fresh configured allowance. Same identity requires owner readback.
        shell = host_phase_command(tmp_path, monkeypatch).replace("/opt/loopx-benchmark/control", str(control))
        observed = subprocess.run(["/bin/bash", "-c", shell + "printf unexpected-work"],
                                  capture_output=True, text=True)
        assert observed.returncode != 0 and observed.stdout == ""
        with pytest.raises(ValueError, match="fresh physical"):
            transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
                              run_id="different-branch", phase_deadline_file=target)
    assert grades == []


@pytest.mark.parametrize("fault", ["wrong_run", "expired", "partial_record", "phase_alias", "result_corrupt", "lost_authority", "busy"])
def test_recovery_refuses_mismatched_or_unavailable_original_activation(controllers, tmp_path, monkeypatch, fault):
    import fcntl
    source, destination, first, second, files, grades = controllers
    token = register(source, first)
    source_phase = tmp_path / "source-phase"
    source_phase.write_text(f"{int(time.time()) + 7200}\n")
    unit = tmp_path / "unit"
    point = transport.capture(files[0], token=token, capture_id="9" * 32,
                              destination=unit, phase_deadline_file=source_phase)
    phase, result = tmp_path / "phase", tmp_path / "result"
    args = ["--controller-file", str(files[1]), "restore", "--root", str(unit),
            "--expected-sha256", point["sha256"], "--run-id", "branch",
            "--result-file", str(result), "--phase-deadline-file", str(phase)]
    post = transport._post
    calls = []
    def lost(controller, route, body, timeout):
        calls.append(route)
        response = post(controller, route, body, timeout)
        if route.endswith("/restore"):
            raise TimeoutError("Lost committed result")
        return response
    monkeypatch.setattr(transport, "_post", lost)
    with pytest.raises(TimeoutError):
        transport.main(args)
    record = phase.with_name("phase.restore.json")
    original_epoch = json.loads(record.read_bytes())["deadline_epoch"]
    lock = None
    if fault == "wrong_run":
        args[args.index("--run-id") + 1] = "another-branch"
    elif fault == "expired":
        monkeypatch.setattr(transport, "time", SimpleNamespace(time=lambda: original_epoch + 1))
    elif fault == "partial_record":
        record.write_bytes(b"{")
    elif fault == "phase_alias":
        phase.unlink()
        phase.symlink_to(source_phase)
    elif fault == "result_corrupt":
        result.write_bytes(b"{")
    elif fault == "lost_authority":
        destination.state.judge.tokens.clear()
    elif fault == "busy":
        lock = phase.open("r+")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = copy.deepcopy((destination.state.judge.tokens, destination.state.judge.run_history))
    old_bytes = (phase.read_bytes(), result.read_bytes(), record.read_bytes())
    try:
        with pytest.raises((ValueError, OSError)):
            transport.main(args + ["--recover"])
    finally:
        if lock:
            lock.close()
    assert (phase.read_bytes(), result.read_bytes(), record.read_bytes()) == old_bytes
    assert (destination.state.judge.tokens, destination.state.judge.run_history) == before
    assert calls.count("/api/v1/checkpoint/restore") == 1 and grades == []
    if fault != "lost_authority":
        assert calls == ["/api/v1/checkpoint/restore"]


@pytest.mark.parametrize("raw", [b"", b"nan\n", b"1.5\n", b"9007199254740992\n"])
def test_invalid_host_epoch_cannot_create_native_point(controllers, tmp_path, raw):
    source, destination, first, second, files, grades = controllers
    token = register(source, first)
    phase = tmp_path / "phase"
    phase.write_bytes(raw)
    with pytest.raises(ValueError, match="phase|clocks"):
        transport.capture(files[0], token=token, capture_id="f" * 32,
                          destination=tmp_path / "unit", phase_deadline_file=phase)
    bank = source.state.judge.config.log_dir / "checkpoints"
    assert not (bank / ("f" * 32)).exists() and not (bank / ".loopx-phase-attempts").exists()
    assert destination.state.judge.tokens == {} and grades == []


def test_native_only_point_cannot_acquire_a_later_host_budget(controllers, tmp_path):
    source, destination, first, second, files, grades = controllers
    token = register(source, first)
    transport.capture(files[0], token=token, capture_id="f" * 32, destination=tmp_path / "first")
    phase = tmp_path / "phase"
    phase.write_text(f"{int(time.time()) + 43200}\n")
    with pytest.raises(ValueError, match="Existing native point"):
        transport.capture(files[0], token=token, capture_id="f" * 32,
                          destination=tmp_path / "later", phase_deadline_file=phase)
    assert not (tmp_path / "later").exists() and destination.state.judge.tokens == {} and grades == []


def test_partial_phase_write_keeps_committed_native_result_inert(controllers, tmp_path, monkeypatch):
    source, destination, first, second, files, grades = controllers
    token = register(source, first)
    phase_source = tmp_path / "source-phase"
    phase_source.write_text(f"{int(time.time()) + 7200}\n")
    unit = tmp_path / "unit"
    point = transport.capture(files[0], token=token, capture_id="f" * 32,
                              destination=unit, phase_deadline_file=phase_source)
    class PartialWrite:
        def __getattr__(self, name):
            return getattr(os, name)
        def write(self, descriptor, data):
            return os.write(descriptor, data[:1])
    monkeypatch.setattr(transport, "os", PartialWrite())
    target = tmp_path / "phase"
    with pytest.raises(OSError, match="incomplete"):
        transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
                          run_id="phase-branch", phase_deadline_file=target)
    assert target.read_bytes() == b"" and len(destination.state.judge.tokens) == 1 and grades == []
    original = copy.deepcopy(destination.state.judge.tokens)
    epoch = json.loads(target.with_name("phase.restore.json").read_bytes())["deadline_epoch"]
    monkeypatch.setattr(transport, "os", os)
    fresh = transport.restore(files[1], root=unit, expected_sha256=point["sha256"],
                              run_id="phase-branch", phase_deadline_file=target, recover=True)
    assert fresh["token"] in original and destination.state.judge.tokens == original
    assert int(target.read_bytes()) == epoch and grades == []


@pytest.mark.parametrize("fault", ["wrong_admin", "corrupt", "wrong_digest", "aliased", "existing_bank"])
def test_invalid_controller_import_never_registers_or_regrades(controllers, tmp_path, fault):
    source, destination, first, second, files, grades = controllers
    token = register(source, first)
    assert submit(first, token, 7).status_code == 200
    exported = tmp_path / "exported"
    point = transport.capture(files[0], token=token, capture_id="b" * 32, destination=exported)
    root, digest = exported, point["sha256"]
    if fault == "wrong_admin":
        config = json.loads(files[1].read_text())
        config["admin_secret"] = "not-current-admin"
        files[1].write_text(json.dumps(config))
    elif fault == "corrupt":
        (exported / "components/native_judge/incumbent.tar.gz").write_bytes(b"99")
    elif fault == "wrong_digest":
        digest = "0" * 64
    elif fault == "aliased":
        root = tmp_path / "alias"
        root.symlink_to(exported, target_is_directory=True)
    else:
        bank = destination.state.judge.config.log_dir / "checkpoints" / ("b" * 32)
        bank.mkdir()
        (bank / "foreign").write_text("keep")
    with pytest.raises((ValueError, RuntimeError, FileNotFoundError)):
        transport.restore(files[1], root=root, expected_sha256=digest, run_id="branch")
    assert destination.state.judge.tokens == {} and len(grades) == 1
    if fault == "existing_bank":
        assert (bank / "foreign").read_text() == "keep"


def test_controller_connects_to_declared_origin_without_environment_proxy(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    origin, proxy = FastAPI(), FastAPI()
    origin_requests, proxy_requests = [], []

    async def capture(request):
        origin_requests.append(await request.json())
        return {"source": "declared-origin"}

    capture.__annotations__["request"] = Request
    origin.post("/api/v1/checkpoint/capture")(capture)

    @proxy.middleware("http")
    async def intercept(request, call_next):
        proxy_requests.append(await request.body())
        return JSONResponse({"source": "unexpected-proxy"})

    with server(origin) as url, server(proxy) as proxy_url:
        for key in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
            monkeypatch.setenv(key, proxy_url)
        for key in ("no_proxy", "NO_PROXY"):
            monkeypatch.setenv(key, "")
        result = transport._post({"url": url, "admin_secret": "synthetic-credential"},
                                 "/api/v1/checkpoint/capture", {"token": "source"}, 5)
    assert result == {"source": "declared-origin"}
    assert origin_requests == [{"token": "source", "admin_secret": "synthetic-credential"}]
    assert proxy_requests == []


def test_redirect_never_forwards_controller_authority(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse
    first, second = FastAPI(), FastAPI()
    received = []
    @second.post("/steal")
    def steal():
        received.append(True)
        return {}
    with server(second) as target:
        @first.post("/api/v1/checkpoint/capture")
        def redirect():
            return RedirectResponse(target + "/steal", status_code=307)
        with server(first) as origin:
            config = {"url": origin, "admin_secret": "synthetic-credential"}
            with pytest.raises(ValueError, match="HTTP 307"):
                transport._post(config, "/api/v1/checkpoint/capture", {"token": "source"}, 5)
    assert received == []


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires explicit isolated native Docker qualification")
@pytest.mark.parametrize("phase_budget,lose_response", [(False, False), (True, False), (True, True)])
def test_real_docker_controller_export_import_and_next_grade(tmp_path, monkeypatch, phase_budget, lose_response):
    import uuid
    import docker
    import requests
    from sforge.harness import checkpoint, judge_server as native
    from sforge.harness.config import SForgeConfig

    name = "loopx-controller-" + uuid.uuid4().hex[:10]
    client = docker.from_env()
    image = client.images.get("python:3.12-slim")
    image.tag(name + ".judge.fixture", tag="fixture")
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "BENCHMARK.yaml").write_text(f"name: {name}\nbase_images: {{python: {{}}}}\n")
    (tasks / "fixture.json").write_text(json.dumps({
        "task_id": "fixture", "name": "Synthetic real grade", "base_image": "python",
        "platform": "linux/arm64", "cwd": "/tmp", "internet": False,
        "submit_paths": ["candidate.json"], "submit_exclude": [],
        "work": {"image_tag": "fixture", "agent_query": "Synthetic fixture"},
        "judge": {"image_tag": "fixture", "eval_timeout": 20, "parser": "structured_json",
            "selection": "score_first", "score_direction": "maximize",
            "eval_cmd": "python3 -c 'import json; print(json.dumps(dict(score=json.load(open(\"candidate.json\"))[\"score\"], valid=True)))'"},
    }))
    config = SForgeConfig(tasks_dir=tasks, log_dir=tmp_path / "source", judge_cpu_limit=1, judge_mem_limit="256m")
    source = native.create_app(config, checkpoints=True)
    destination = native.create_app(replace(config, log_dir=tmp_path / "branch"), checkpoints=True)
    grades = []
    original = native.judge_submission
    def actual_grade(**kwargs):
        grades.append(kwargs["archive"])
        return original(**kwargs)
    monkeypatch.setattr(native, "judge_submission", actual_grade)
    def grade(url, token, score):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            body = json.dumps({"score": score}).encode()
            member = tarfile.TarInfo("candidate.json")
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
        response = requests.post(url + "/api/v1/submit", data={"token": token},
            files={"archive": ("candidate.tar.gz", raw.getvalue())}, timeout=10)
        if response.status_code == 200:
            sid = response.json()["submission_id"]
            report = wait(lambda: (packet if (packet := requests.get(url + "/api/v1/result/" + sid,
                timeout=10).json())["status"] in {"completed", "error"} else None))
            assert report["status"] == "completed" and report["report"]["score"] == score
        return response
    try:
        with server(source) as first, server(destination) as second:
            files = []
            for label, app, url in (("source", source, first), ("branch", destination, second)):
                bank = app.state.judge.config.log_dir / "checkpoints"
                bank.mkdir(parents=True, mode=0o700)
                path = tmp_path / (label + "-admin.json")
                path.write_text(json.dumps({"url": url, "admin_secret": app.state.judge.admin_secret,
                                            "bank_root": str(bank)}))
                path.chmod(0o600)
                files.append(path)
            token = register(source, first, max_agent_submissions=2, submission_cooldown=3600)
            assert grade(first, token, 7).status_code == 200
            before = copy.deepcopy((source.state.judge.tokens, source.state.judge.submissions, source.state.judge.run_history))
            exported = tmp_path / "capsule"
            phase_source = tmp_path / "source-phase"
            if phase_budget:
                phase_source.write_text(f"{int(time.time()) + 7200}\n")
            point = transport.capture(files[0], token=token, capture_id="c" * 32, destination=exported,
                                      phase_deadline_file=phase_source if phase_budget else None)
            # Admission clock only: HTTP, transport/fsync, Docker and native
            # grading remain real. Offline/import IO must not spend cooldown.
            now = [time.time() + 7200]
            clock = SimpleNamespace(time=lambda: now[0])
            monkeypatch.setattr(native, "time", clock)
            monkeypatch.setattr(checkpoint, "time", clock)
            monkeypatch.setattr(transport, "time", clock)
            control = tmp_path / "host-control"
            control.mkdir()
            phase = control / "phase-deadline"
            args = ["--controller-file", str(files[1]), "restore", "--root", str(exported),
                    "--expected-sha256", point["sha256"], "--run-id", "branch",
                    "--result-file", str(control / "result.json")]
            if phase_budget:
                args += ["--phase-deadline-file", str(phase)]
            post = transport._post
            routes = []
            def observed_post(controller, route, body, timeout):
                routes.append(route)
                result = post(controller, route, body, timeout)
                if lose_response and route.endswith("/restore"):
                    raise TimeoutError("Lost reply after real native commit")
                return result
            monkeypatch.setattr(transport, "_post", observed_post)
            if lose_response:
                with pytest.raises(TimeoutError):
                    transport.main(args)
                assert phase.read_bytes() == (control / "result.json").read_bytes() == b""
                retained_epoch = json.loads((control / "phase-deadline.restore.json").read_bytes())["deadline_epoch"]
                native_state = copy.deepcopy((destination.state.judge.tokens, destination.state.judge.run_history))
                now[0] += 60
                transport.main(args + ["--recover"])
                transport.main(args + ["--recover"])
                assert int(phase.read_bytes()) == retained_epoch
                assert (destination.state.judge.tokens, destination.state.judge.run_history) == native_state
                assert routes == ["/api/v1/checkpoint/restore", "/api/v1/checkpoint/restore-result",
                                  "/api/v1/checkpoint/restore-result"]
            else:
                transport.main(args)
            fresh = json.loads((control / "result.json").read_bytes())["token"]
            assert fresh != token and len(grades) == 1
            assert grade(second, fresh, 3).status_code == 429
            now[0] += 3601
            continued_score = 3
            if phase_budget:
                # The actual SForge phase shell consumes the restored duration
                # inside a disposable Linux container, then writes a new candidate.
                prefix = host_phase_command(tmp_path, monkeypatch)
                guest_control = "/opt/loopx-benchmark/control"
                command = prefix + f"printf '%s' \"$remaining\"; printf '{{\"score\":3}}' > '{guest_control}/candidate.json'"
                guest = client.containers.create(image.id, ["/bin/bash", "-c", command],
                    environment={"PATH": guest_control + "/bin:/usr/bin:/bin"},
                    network_disabled=True, mem_limit="128m", nano_cpus=1_000_000_000)
                try:
                    # Upload explicitly: a remote Docker daemon cannot read
                    # the caller's temporary directory through a host mount.
                    upload = io.BytesIO()
                    with tarfile.open(fileobj=upload, mode="w") as archive:
                        for relative in ("opt", "opt/loopx-benchmark", "opt/loopx-benchmark/control",
                                         "opt/loopx-benchmark/control/bin"):
                            member = tarfile.TarInfo(relative)
                            member.type, member.mode = tarfile.DIRTYPE, 0o700
                            archive.addfile(member)
                        for relative, data, mode in (
                            ("phase-deadline", phase.read_bytes(), 0o600),
                            ("bin/date", f"#!/bin/sh\nprintf '%s\\n' {int(now[0])}\n".encode(), 0o700)):
                            member = tarfile.TarInfo("opt/loopx-benchmark/control/" + relative)
                            member.size, member.mode = len(data), mode
                            archive.addfile(member, io.BytesIO(data))
                    assert guest.put_archive("/", upload.getvalue())
                    guest.start()
                    assert guest.wait(timeout=30)["StatusCode"] == 0
                    assert 3580 - (60 if lose_response else 0) < int(guest.logs()) <= 3600 - (60 if lose_response else 0)
                    stream, _ = guest.get_archive(guest_control + "/candidate.json")
                    try:
                        with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as archive:
                            continued_score = json.loads(archive.extractfile("candidate.json").read())["score"]
                            assert continued_score == 3
                    finally:
                        stream.close()
                finally:
                    guest.remove(force=True)
            assert grade(second, fresh, continued_score).json()["round_id"] == "agent-2"
            history = requests.get(second + "/api/v1/history", params={"token": fresh}, timeout=5).json()
            assert history["best_score"] == 7 and history["best_round"] == "agent-1"
            assert grade(second, fresh, 99).status_code == 429 and len(grades) == 2
            assert (source.state.judge.tokens, source.state.judge.submissions, source.state.judge.run_history) == before
            with pytest.raises(ValueError, match="fresh physical" if phase_budget else "HTTP 409"):
                transport.restore(files[1], root=exported, expected_sha256=point["sha256"], run_id="branch",
                                  phase_deadline_file=phase if phase_budget else None)
    finally:
        for app in (source, destination):
            app.state.judge._reaper_stop.set()
            app.state.judge._reaper_thread.join(timeout=1)
        client.images.remove(name + ".judge.fixture:fixture")
        client.close()


def test_concurrent_same_point_imports_preserve_both_fresh_native_runs(controllers, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    source, destination, first, second, files, grades = controllers
    token = register(source, first, max_agent_submissions=3, submission_cooldown=0)
    assert submit(first, token, 7).status_code == 200
    unit = tmp_path / 'shared-point'
    point = transport.capture(files[0], token=token, capture_id='b' * 32, destination=unit)
    bank = destination.state.judge.config.log_dir / 'checkpoints'
    target = bank / ('b' * 32)
    reserved, release, staged_second = threading.Event(), threading.Event(), threading.Event()
    original_mkdir = Path.mkdir
    original_effect = transport.effect_runtime_result
    counts = {'reserve': 0, 'stage': 0}
    mutex = threading.Lock()

    def mkdir(path, *args, **kwargs):
        if path == target:
            with mutex:
                counts['reserve'] += 1
                first_import = counts['reserve'] == 1
            if first_import:
                reserved.set()
                assert release.wait(15), 'Test did not release first publisher'
        return original_mkdir(path, *args, **kwargs)

    def effect(method, params, **kwargs):
        result = original_effect(method, params, **kwargs)
        if params.get('action') == 'materialize':
            with mutex:
                counts['stage'] += 1
                if counts['stage'] == 2:
                    staged_second.set()
        return result

    monkeypatch.setattr(Path, 'mkdir', mkdir)
    monkeypatch.setattr(transport, 'effect_runtime_result', effect)
    def restore(run):
        return transport.restore(files[1], root=unit, expected_sha256=point['sha256'], run_id=run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(restore, 'branch-one')
        try:
            assert reserved.wait(15)
            two = pool.submit(restore, 'branch-two')
            assert staged_second.wait(15)
            # Both have staged verified bytes. A competing publisher must wait,
            # rather than create the destination while the first is reserving it.
            time.sleep(.2)
            assert not two.done()
        finally:
            release.set()
        a, b = one.result(timeout=15), two.result(timeout=15)
    assert a['token'] != b['token'] != token
    assert counts['reserve'] == 1
    for run in ('branch-one', 'branch-two'):
        history = destination.state.judge.get_run_history(run, 'fixture')
        assert history['best_score'] == 7 and history['agent_submissions'] == 1
    assert len(grades) == 1
    assert (target / 'incumbent.tar.gz').read_bytes() == b'7'


def test_bank_import_contention_is_bounded_and_does_not_erase_the_lock(tmp_path):
    target = tmp_path / ('e' * 32)
    with transport._bank_import_lock(target, 1):
        with pytest.raises(TimeoutError, match='Native bank import is busy'):
            with transport._bank_import_lock(target, .03):
                pytest.fail('Competing publication entered the lock')
    assert not target.exists()
    with transport._bank_import_lock(target, 1):
        assert (tmp_path / ('.loopx-import-' + target.name + '.lock')).is_file()


def test_bank_import_rejects_a_redirected_lock(tmp_path):
    target = tmp_path / ('f' * 32)
    foreign = tmp_path / 'foreign'
    foreign.write_text('unchanged')
    (tmp_path / ('.loopx-import-' + target.name + '.lock')).symlink_to(foreign)
    with pytest.raises(OSError):
        with transport._bank_import_lock(target, 1):
            pytest.fail('Symlink lock accepted')
    assert foreign.read_text() == 'unchanged' and not target.exists()
