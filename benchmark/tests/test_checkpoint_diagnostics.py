"""Real Effect failures through worker cleanup and supervisor reply transport.

Docker IO is synthetic; this does not qualify a real Docker or native provider.
"""
from __future__ import annotations

import hashlib
import io
import json
import stat
import tarfile
import tempfile
import time
from types import SimpleNamespace

import docker
import pytest

from benchmark.edgebench import checkpoint_supervisor as supervisor_module
from benchmark.edgebench import checkpoint_worker as worker_module
from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime


@pytest.fixture(scope="module", autouse=True)
def isolated_runtime(tmp_path_factory):
    temporary = tmp_path_factory.mktemp("checkpoint-diagnostic-runtime")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("LOOPX_USAGE_PING", "0")
        for variable in ("TMPDIR", "TEMP", "TMP"):
            patch.setenv(variable, str(temporary))
        patch.setattr(tempfile, "tempdir", str(temporary))
        yield
        restart_effect_runtime(wait_for_exit=True)


@pytest.mark.parametrize("fault,exception_kind,runtime_kind,code", [
    ("expired", "EffectRuntimeRejected", "request_rejected", "checkpoint_capture_window_closed"),
    ("missing", "EffectRuntimePermanentIOError", "io_permanent", "io_not_found"),
    ("directory", "EffectRuntimePermanentIOError", "io_permanent", "io_is_directory"),
    ("unexpected", "EffectRuntimeInternalError", "internal_failure", "unexpected_handler_error"),
    ("untyped", "ValueError", None, None),
])
def test_failed_capture_replies_once_after_releasing_source(
    tmp_path, monkeypatch, fault, exception_kind, runtime_kind, code,
):
    events = []
    replies = []
    nonce = "a" * 32
    token, admin = "synthetic-worker-secret", "synthetic-admin-secret"
    candidate = tmp_path / "candidate"
    candidate.write_bytes(b"original source bytes\n")
    source = [{"id": "code", "source_path": str(candidate)}]
    observed = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "probe", "components": source}, retry_safe=False)
    ready = tmp_path / "ready.json"
    ready.write_text(json.dumps({
        "capture_id": nonce, "components": [{"id": "code", "source_path": "/candidate"}],
        "point_metadata": {}, "source_sha256": observed["source_sha256"],
        "source_witness": observed["source_witness"], "deadline_epoch": time.time() + 60,
    }))
    bank = tmp_path / "bank"
    bank.mkdir()
    controller = tmp_path / "controller.json"
    controller.write_text(json.dumps({
        "url": "http://127.0.0.1:9", "admin_secret": admin, "bank_root": str(bank),
    }))
    controller.chmod(0o600)

    class Worker:
        id = "b" * 64

        def __init__(self):
            self.attrs = {"State": {"Running": True, "Paused": False}, "Image": "synthetic-image"}

        def reload(self):
            pass

        def pause(self):
            events.append("pause")
            self.attrs["State"]["Paused"] = True

        def unpause(self):
            events.append("unpause")
            self.attrs["State"]["Paused"] = False

        def put_archive(self, directory, raw):
            assert self.attrs["State"] == {"Running": True, "Paused": False}
            assert directory == "/logs/agent/checkpoint-controller"
            events.append("reply_transfer")
            with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
                assert archive.getnames() == [nonce + ".pending"]
                replies.append(json.load(archive.extractfile(nonce + ".pending")))
            return True

        def exec_run(self, command):
            assert command == ["/bin/mv", directory + "/" + nonce + ".pending",
                               directory + "/" + nonce + ".json"]
            assert self.attrs["State"] == {"Running": True, "Paused": False}
            events.append("reply_publish")
            return SimpleNamespace(exit_code=0)

    directory = "/logs/agent/checkpoint-controller"
    worker = Worker()

    def get_container(identity):
        assert identity == worker.id
        return worker

    client = SimpleNamespace(api=SimpleNamespace(timeout=60),
        containers=SimpleNamespace(get=get_container), close=lambda: events.append("client_close"))
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: client)
    monkeypatch.setattr(worker_module, "_worker_lock", lambda *args: tmp_path / "worker.lock")
    monkeypatch.setattr(supervisor_module, "_archive", lambda *args, **kwargs: ready)

    def held_archive(*args, **kwargs):
        assert worker.attrs["State"]["Paused"]
        return ready

    def source_archive(*args, **kwargs):
        assert worker.attrs["State"]["Paused"]
        return candidate

    monkeypatch.setattr(worker_module, "_archive", held_archive)
    monkeypatch.setattr(worker_module, "_archive_component", source_archive)

    def native_capture(*args, **kwargs):
        pytest.fail("A failed source probe must not reach native capture")

    monkeypatch.setattr(worker_module, "capture", native_capture)
    malformed_root = tmp_path / "synthetic-private-path"
    malformed_root.mkdir()
    if fault == "directory":
        (malformed_root / "checkpoint.json").mkdir()
    else:
        (malformed_root / "checkpoint.json").write_text("invalid JSON " + token + " " + admin)

    def failed_probe(method, request, **kwargs):
        assert method == "runtime.checkpoint_artifacts" and request["action"] == "probe"
        assert kwargs["retry_safe"] is False
        assert worker.attrs["State"]["Paused"]
        events.append("probe")
        if fault == "untyped":
            error = ValueError("provider body " + token)
            # Attributes alone must not make an arbitrary provider exception trusted.
            error.error_kind, error.diagnostic_code = admin, token
            raise error
        if fault == "expired":
            request = {**request, "deadline_epoch_ms": 0}
        elif fault == "missing":
            request = {"action": "probe", "components": [
                {"id": "code", "source_path": str(tmp_path / "missing")}]}
        else:
            request = {"action": "read_basis", "root": str(malformed_root), "expected_sha256": "0" * 64}
        return effect_runtime_result(method, request, **kwargs)

    monkeypatch.setattr(worker_module, "effect_runtime_result", failed_probe)
    points = tmp_path / "points"
    supervisor = supervisor_module.CheckpointSupervisor(controller, points, retention_enabled=False)
    supervisor.bind({"SFORGE_TOKEN": token})
    points.mkdir(mode=0o700)
    supervisor.container = worker
    # Read the same handoff a second time: production's seen set must prevent a
    # second attempt, then stop at its ordinary polling wait without sleeping.
    monkeypatch.setattr(supervisor.stop, "wait", lambda *args: supervisor.stop.set())
    supervisor._run(worker)
    supervisor.close()

    assert supervisor.failure is None
    assert worker.attrs["State"] == {"Running": True, "Paused": False}
    assert events == ["pause", "probe", "unpause", "client_close", "reply_transfer", "reply_publish"]
    assert candidate.read_bytes() == b"original source bytes\n"
    assert not (points / nonce).exists() and not (bank / nonce).exists()
    assert (bank / ".loopx-worker-attempts" / nonce).is_dir()
    assert not list(points.glob(".worker-capture-*"))
    receipt = json.loads((points / (nonce + ".json")).read_bytes())
    assert replies == [receipt]
    expected = {"request_sha256": hashlib.sha256(ready.read_bytes()).hexdigest(),
                "ok": False, "error_kind": exception_kind}
    if runtime_kind is not None:
        expected["runtime_failure"] = {"error_kind": runtime_kind, "diagnostic_code": code}
    assert receipt == expected
    for private in (token, admin, str(tmp_path), "provider body", "invalid JSON"):
        assert private not in json.dumps(receipt)
    diagnostic = points / (nonce + ".error.txt")
    assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
    assert token not in diagnostic.read_text()
    if fault == "unexpected":
        assert diagnostic.read_text() == "Effect runtime handler failed unexpectedly"
