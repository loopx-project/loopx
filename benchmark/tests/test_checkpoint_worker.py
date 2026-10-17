"""Real Docker writer barrier plus native HTTP capture; no model inference."""
from __future__ import annotations

import io
import hashlib
import json
import os
import sqlite3
import tarfile
import threading
import time
import uuid
from pathlib import Path

import pytest

from benchmark.edgebench import checkpoint_controller as transport
from benchmark.edgebench.checkpoint_worker import capture_worker
from benchmark.tests import test_checkpoint_controller as native_fixtures
from benchmark.tests.test_checkpoint_controller import register, submit, wait


controllers = native_fixtures.controllers


pytestmark = pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
    reason="Requires explicit isolated Docker writer-barrier qualification")


@pytest.fixture
def writer(tmp_path):
    import docker

    client = docker.from_env(timeout=15)
    image = os.environ.get("LOOPX_CHECKPOINT_WRITER_IMAGE", "python:3.12-slim")
    client.images.get(image)  # Never pull implicitly for this test.
    script = """from pathlib import Path
import time, sqlite3
Path('/data').mkdir()
Path('/phase').write_text(str(int(time.time())+7200))
Path('/link').symlink_to('/data', target_is_directory=True)
db=sqlite3.connect('/data/research.sqlite')
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute('CREATE TABLE evidence (value INTEGER)')
db.commit()
db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
db.execute('INSERT INTO evidence VALUES (42)')
db.commit()
db.execute('INSERT INTO evidence VALUES (99)')
count=0
while True:
    count+=1
    Path('/data/next').write_text(str(count))
    Path('/data/next').replace('/data/counter')
    time.sleep(.03)
"""
    container = client.containers.run(image, ["python", "-c", script], detach=True,
        name="loopx-checkpoint-writer-" + uuid.uuid4().hex[:12],
        network_disabled=True, mem_limit="64m", nano_cpus=250_000_000)
    request = tmp_path / "worker.json"
    request.write_text(json.dumps({"container_id": container.id,
        "components": [{"id": "native_capsule", "source_path": "/data"}],
        "phase_deadline_path": "/phase"}))
    try:
        wait(lambda: container.exec_run(["test", "-f", "/data/counter"]).exit_code == 0)
        yield container, request
    finally:
        container.remove(force=True)
        assert not client.containers.list(all=True, filters={"id": container.id})
        client.close()


def counter(container):
    stream, _ = container.get_archive("/data/counter")
    with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as archive:
        return int(archive.extractfile("counter").read())


def test_ordinary_controller_stages_root_private_native_configuration(writer, tmp_path):
    from benchmark.tests.qualify_checkpoint_joint import _stage_source_configuration

    assert os.getuid() != 0, "Qualify the ordinary controller, not a privileged reader"
    container, _ = writer
    raw = b'model = "fixture"\n'
    command = ("from pathlib import Path; p=Path('/private-codex'); p.mkdir(mode=0o700); "
               f"f=p/'config.toml'; f.write_bytes({raw!r}); f.chmod(0o600)")
    assert container.exec_run(["python", "-c", command], user="root").exit_code == 0
    assert container.exec_run(["stat", "-c", "%u %a", "/private-codex",
                               "/private-codex/config.toml"]).output == b"0 700\n0 600\n"
    rejected = container.exec_run(["python", "-c",
        "from pathlib import Path; Path('/private-codex/config.toml').read_bytes()"], user=str(os.getuid()))
    assert rejected.exit_code != 0 and b"PermissionError" in rejected.output
    native = {"guest_home": "/private-codex", "config_sha256": hashlib.sha256(raw).hexdigest()}
    config = _stage_source_configuration(container, native, tmp_path / "staged")
    assert config.read_bytes() == raw and config.stat().st_uid == os.getuid()
    assert config.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="differs from the sealed checkpoint"):
        _stage_source_configuration(container, {**native, "config_sha256": "f" * 64}, tmp_path / "wrong")
    assert container.exec_run(["stat", "-c", "%u %a", "/private-codex",
                               "/private-codex/config.toml"]).output == b"0 700\n0 600\n"


def test_archive_keeps_private_directory_modes_without_special_bits(writer, tmp_path):
    from benchmark.edgebench.checkpoint_worker import _archive
    container, _ = writer
    assert container.exec_run(["python", "-c", "from pathlib import Path; "
        "p=Path('/private-state'); p.mkdir(mode=0o700); "
        "(p/'child').mkdir(mode=0o700); (p/'child').chmod(0o2700)"]).exit_code == 0
    copied = _archive(container, "/private-state", tmp_path / "copy")
    assert copied.stat().st_mode & 0o7777 == 0o700
    assert (copied / "child").stat().st_mode & 0o7777 == 0o700


@pytest.mark.parametrize("fault", [None, "source_moved", "expired", "read_race", "late_native"])
def test_supervisor_captures_only_the_prepared_worker_boundary(controllers, writer, tmp_path, monkeypatch, fault):
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from benchmark.edgebench.checkpoint_worker import _archive
    from benchmark.edgebench.checkpoint_supervisor import CheckpointSupervisor

    source, _, first, _, files, grades = controllers
    container, _ = writer
    token = register(source, first, submission_cooldown=3600)
    assert submit(first, token, 7).status_code == 200
    assert container.exec_run(["python", "-c", "from pathlib import Path; "
        "Path('/candidate').write_text('retained'); "
        "Path('/logs/agent/checkpoint-controller').mkdir(parents=True); "
        "Path('/opt/loopx-benchmark/control').mkdir(parents=True); "
        "Path('/opt/loopx-benchmark/control/phase-deadline').write_bytes(Path('/phase').read_bytes())"]).exit_code == 0
    prepared = _archive(container, "/candidate", tmp_path / "prepared")
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe",
        "components": [{"id": "code", "source_path": str(prepared)}]})
    nonce = "e" * 32
    request = {"capture_id": nonce, "components": [{"id": "code", "source_path": "/candidate"}],
        "point_metadata": {"synthetic_boundary": True}, "source_sha256": probe["source_sha256"],
        "source_witness": probe["source_witness"],
        "deadline_epoch": time.time() + (-1 if fault == "expired" else 8 if fault == "late_native" else 30)}
    if fault == "source_moved":
        assert container.exec_run(["python", "-c", "from pathlib import Path; Path('/candidate').write_text('later')"]).exit_code == 0
    assert container.exec_run(["python", "-c", "from pathlib import Path; "
        "Path('/logs/agent/checkpoint-controller/ready.json').write_text(" + repr(json.dumps(request)) + ")"]).exit_code == 0
    if fault == "read_race":
        from benchmark.edgebench import checkpoint_supervisor
        original = checkpoint_supervisor._archive
        reads = []
        def interrupted_archive(*args):
            reads.append(True)
            if len(reads) == 1:
                raise tarfile.ReadError("request removed during Docker archive")
            return original(*args)
        monkeypatch.setattr(checkpoint_supervisor, "_archive", interrupted_archive)
    if fault == "late_native":
        from benchmark.edgebench import checkpoint_worker
        original_capture = checkpoint_worker.capture
        def late_capture(*args, **kwargs):
            result = original_capture(*args, **kwargs)
            time.sleep(max(0, request["deadline_epoch"] + .1 - time.time()))
            return result
        monkeypatch.setattr(checkpoint_worker, "capture", late_capture)
    supervisor = CheckpointSupervisor(files[0], tmp_path / "points")
    supervisor.bind({"SFORGE_TOKEN": token})
    supervisor.start(container)
    try:
        wait(lambda: (tmp_path / "points" / (nonce + ".json")).exists())
        wait(lambda: container.exec_run(["test", "-f", "/logs/agent/checkpoint-controller/" + nonce + ".json"]).exit_code == 0)
    finally:
        supervisor.close()
    response = json.loads((tmp_path / "points" / (nonce + ".json")).read_bytes())
    assert response["ok"] is (fault in {None, "read_race"})
    unit = tmp_path / "points" / nonce
    if fault in {None, "read_race"}:
        manifest = json.loads((unit / "checkpoint.json").read_bytes())
        metadata = manifest["point_metadata"]
        assert metadata["synthetic_boundary"] is True
        assert metadata["worker_file_snapshot"]["prepared_source_verified"] is True
        assert (unit / "components/code").read_text() == "retained"
        assert response["capture"]["research_point_qualified"] is False
    else:
        assert not unit.exists()
        if fault != "late_native":
            assert not (Path(json.loads(files[0].read_bytes())["bank_root"]) / nonce).exists()
    container.reload()
    assert not container.attrs["State"]["Paused"] and len(grades) == 1


@pytest.mark.parametrize("seal_within_phase,expire_phase", [(True, False), (False, False), (True, True)])
def test_post_release_sealing_preserves_captured_bytes_and_original_phase(
        controllers, writer, tmp_path, monkeypatch, seal_within_phase, expire_phase):
    from benchmark.edgebench import checkpoint_worker
    from loopx.control_plane.effect_runtime import effect_runtime_result

    source, _, first, _, files, grades = controllers
    container, request_file = writer
    token = register(source, first, submission_cooldown=3600)
    assert container.exec_run(["python", "-c", "from pathlib import Path; "
        "Path('/candidate').write_text('captured'); Path('/ready').write_text('')"]).exit_code == 0
    prepared = checkpoint_worker._archive(container, "/candidate", tmp_path / "prepared")
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe",
        "components": [{"id": "code", "source_path": str(prepared)}]})
    held_deadline = time.time() + 8
    phase_epoch = int(time.time()) + (12 if expire_phase else 7200)
    assert container.exec_run(["python", "-c", "from pathlib import Path; "
        f"Path('/phase').write_text({str(phase_epoch)!r})"]).exit_code == 0
    nonce = "9" * 32
    ready = {"capture_id": nonce, "components": [{"id": "code", "source_path": "/candidate"}],
        "point_metadata": {}, "source_sha256": probe["source_sha256"],
        "source_witness": probe["source_witness"], "deadline_epoch": held_deadline}
    raw = json.dumps(ready).encode()
    assert container.exec_run(["python", "-c", "from pathlib import Path; "
        f"Path('/ready').write_bytes({raw!r})"]).exit_code == 0
    request_file.write_text(json.dumps({"container_id": container.id, "components": ready["components"],
        "phase_deadline_path": "/phase", "handoff": {"source_path": "/ready",
        "sha256": hashlib.sha256(raw).hexdigest()}}))
    unit = tmp_path / "point"
    def delayed_seal(method, params, **kwargs):
        if params.get("action") == "capture" and params.get("destination") == str(unit):
            if seal_within_phase:
                assert params["deadline_epoch_ms"] <= min(held_deadline + 30, phase_epoch) * 1000 + 1
            container.reload()
            assert container.attrs["State"]["Running"] and not container.attrs["State"]["Paused"]
            before = counter(container)
            assert container.exec_run(["python", "-c", "from pathlib import Path; "
                "Path('/candidate').write_text('later source')"]).exit_code == 0
            target = phase_epoch if expire_phase else held_deadline
            time.sleep(max(0, target + .1 - time.time()))
            assert counter(container) > before
        return effect_runtime_result(method, params, **kwargs)
    monkeypatch.setattr(checkpoint_worker, "effect_runtime_result", delayed_seal)
    if seal_within_phase and not expire_phase:
        result = capture_worker(files[0], token=token, capture_id=nonce, destination=unit,
            request_file=request_file, timeout=20, seal_within_phase=True)
        assert result["integrity_verified"] and (unit / "components/code").read_text() == "captured"
    else:
        with pytest.raises(RuntimeError):
            capture_worker(files[0], token=token, capture_id=nonce, destination=unit,
                request_file=request_file, timeout=20, seal_within_phase=seal_within_phase)
        assert not unit.exists()
    container.reload()
    assert not container.attrs["State"]["Paused"] and not grades


@pytest.mark.parametrize("directory", [False, True])
def test_paused_sqlite_capture_retains_committed_wal_not_pending_transaction(
        controllers, writer, tmp_path, directory):
    """A valid main DB can omit all recent research while integrity_check passes."""
    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    payload = json.loads(request.read_text())
    payload["components"] = [{"id": "research", "source_path":
        "/data" if directory else "/data/research.sqlite"}]
    request.write_text(json.dumps(payload))
    unit = tmp_path / "wal-point"
    capture_worker(files[0], token=token, capture_id="b" * 32,
                   destination=unit, request_file=request)
    captured = unit / "components/research"
    database = captured / "research.sqlite" if directory else captured
    with sqlite3.connect(database) as readback:
        assert readback.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert readback.execute("SELECT value FROM evidence").fetchall() == [(42,)]
    assert not database.with_name(database.name + "-wal").exists()
    container.reload()
    assert container.attrs["State"]["Running"] and not container.attrs["State"]["Paused"]


@pytest.mark.parametrize("fault", ["symlink", "directory", "transport"])
def test_sqlite_companion_failure_cannot_publish_an_older_valid_database(
        controllers, writer, tmp_path, monkeypatch, fault):
    from docker.errors import APIError
    from benchmark.edgebench import checkpoint_worker

    source, _, first, _, files, grades = controllers
    container, request = writer
    token = register(source, first)
    payload = json.loads(request.read_text())
    payload["components"] = [{"id": "research", "source_path": "/data/research.sqlite"}]
    request.write_text(json.dumps(payload))
    if fault == "transport":
        original = checkpoint_worker._archive
        def unavailable(worker, remote, destination, **kwargs):
            if remote.endswith("-wal"):
                raise APIError("Synthetic Docker archive transport failure")
            return original(worker, remote, destination, **kwargs)
        monkeypatch.setattr(checkpoint_worker, "_archive", unavailable)
    else:
        replacement = "p.symlink_to('/phase')" if fault == "symlink" else "p.mkdir()"
        assert container.exec_run(["python", "-c", "from pathlib import Path; "
            "p=Path('/data/research.sqlite-wal'); p.unlink(); " + replacement]).exit_code == 0
    unit = tmp_path / "invalid-point"
    with pytest.raises((ValueError, APIError)):
        capture_worker(files[0], token=token, capture_id="c" * 32,
                       destination=unit, request_file=request)
    assert not unit.exists() and not grades
    assert not (Path(json.loads(files[0].read_text())["bank_root"]) / ("c" * 32)).exists()
    container.reload()
    assert container.attrs["State"]["Running"] and not container.attrs["State"]["Paused"]


def test_non_sqlite_file_does_not_capture_similarly_named_neighbors(controllers, writer, tmp_path):
    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    assert container.exec_run(["python", "-c",
        "from pathlib import Path; Path('/data/counter-wal').write_text('PRIVATE_NEIGHBOR')"]).exit_code == 0
    payload = json.loads(request.read_text())
    payload["components"] = [{"id": "counter", "source_path": "/data/counter"}]
    request.write_text(json.dumps(payload))
    unit = tmp_path / "ordinary-point"
    capture_worker(files[0], token=token, capture_id="d" * 32,
                   destination=unit, request_file=request)
    assert all(b"PRIVATE_NEIGHBOR" not in member.read_bytes()
               for member in unit.rglob("*") if member.is_file())


def test_controller_cli_captures_frozen_worker_with_original_native_point(
        controllers, writer, tmp_path, monkeypatch, capsys):
    source, destination, first, second, files, grades = controllers
    container, request = writer
    token = register(source, first, max_agent_submissions=3, submission_cooldown=3600)
    assert submit(first, token, 7).status_code == 200
    observed = []
    original = transport._post
    def while_frozen(*args, **kwargs):
        container.reload()
        assert container.attrs["State"]["Paused"]
        before = counter(container)
        result = original(*args, **kwargs)
        time.sleep(.15)
        assert counter(container) == before
        observed.append(before)
        return result
    monkeypatch.setattr(transport, "_post", while_frozen)
    token_file = tmp_path / "token"
    token_file.write_text(token)
    unit = tmp_path / "unit"
    transport.main(["--controller-file", str(files[0]), "capture-worker",
        "--token-file", str(token_file), "--capture-id", "7" * 32,
        "--destination", str(unit), "--worker-request", str(request)])
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["research_point_qualified"] is False
    manifest = json.loads((unit / "checkpoint.json").read_bytes())
    metadata = manifest["point_metadata"]["worker_file_snapshot"]
    assert metadata["native_cursor_qualified"] is False and metadata["process_memory_captured"] is False
    assert metadata["native_capsule_component_id"] == "native_capsule_1"
    assert int((unit / "components/native_capsule/counter").read_text()) == observed[0]
    nested = unit / "components/native_capsule_1"
    capsule = json.loads((nested / "checkpoint.json").read_bytes())
    native = capsule["point_metadata"]["native_reference"]
    assert native["opportunity"]["next_agent"] == 2 and native["incumbent"]["round"] == "agent-1"
    assert 3590 < native["opportunity"]["remaining_cooldown_seconds"] <= 3600
    assert 7190 < capsule["point_metadata"]["host_phase_budget"]["remaining_seconds"] <= 7200
    container.reload()
    assert not container.attrs["State"]["Paused"]
    wait(lambda: counter(container) > observed[0])
    assert int((unit / "components/native_capsule/counter").read_text()) == observed[0]
    for secret in (token, source.state.judge.admin_secret):
        assert all(secret.encode() not in p.read_bytes() for p in unit.rglob("*") if p.is_file())
    # This is an inert joint file export, not a shortcut to activation.
    with pytest.raises(ValueError, match="declared capsule"):
        transport.restore(files[1], root=unit, expected_sha256=receipt["sha256"], run_id="branch")
    with pytest.raises(ValueError, match="later worker"):
        capture_worker(files[0], token=token, capture_id="7" * 32,
                       destination=tmp_path / "later", request_file=request)
    assert not (tmp_path / "later").exists() and destination.state.judge.tokens == {} and len(grades) == 1


@pytest.mark.parametrize("fault", ["missing", "symlink", "judge", "pause_reply", "prepaused", "cancel", "unpause"])
def test_worker_capture_failure_releases_only_its_barrier(
        controllers, writer, tmp_path, monkeypatch, fault):
    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    from docker.errors import NotFound

    expected_error = {"missing": NotFound, "symlink": ValueError, "judge": ValueError,
        "pause_reply": OSError, "prepaused": ValueError, "cancel": KeyboardInterrupt,
        "unpause": OSError}[fault]
    if fault in {"missing", "symlink"}:
        body = json.loads(request.read_bytes())
        body["components"][0]["source_path"] = "/missing" if fault == "missing" else "/link"
        request.write_text(json.dumps(body))
    elif fault == "judge":
        body = json.loads(files[0].read_bytes())
        body["admin_secret"] = "wrong-synthetic-controller"
        files[0].write_text(json.dumps(body))
    elif fault == "pause_reply":
        original = type(container).pause
        def lost_reply(self):
            original(self)
            raise OSError("injected lost pause reply")
        monkeypatch.setattr(type(container), "pause", lost_reply)
    elif fault == "prepaused":
        container.pause()
    elif fault == "cancel":
        def cancel(*args, **kwargs):
            raise KeyboardInterrupt("injected controller cancellation")
        monkeypatch.setattr(transport, "_post", cancel)
    else:
        def unavailable(self):
            raise OSError("injected daemon unpause failure")
        monkeypatch.setattr(type(container), "unpause", unavailable)
    before = counter(container)
    with pytest.raises(expected_error):
        capture_worker(files[0], token=token, capture_id="8" * 32,
                       destination=tmp_path / "unit", request_file=request)
    assert not (tmp_path / "unit").exists()
    container.reload()
    assert container.attrs["State"]["Paused"] == (fault in {"prepaused", "unpause"})
    if fault not in {"prepaused", "unpause"}:
        wait(lambda: counter(container) > before)
    # No failed attempt is silently replaced by a later research state.
    with pytest.raises((FileExistsError, ValueError)):
        capture_worker(files[0], token=token, capture_id="8" * 32,
                       destination=tmp_path / "later", request_file=request)
    assert not (tmp_path / "later").exists()


def test_full_export_volume_releases_real_archive_and_writer(
        controllers, writer, tmp_path, monkeypatch):
    import errno
    from contextlib import contextmanager
    from benchmark.edgebench import checkpoint_worker as worker

    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    # Exceed the socket buffer so abandoning the reader cannot finish by luck.
    assert container.exec_run(["python", "-c",
        "from pathlib import Path;Path('/data/payload').write_bytes(b'x'*(16*1024*1024))"]).exit_code == 0
    responses = []
    api_type = type(container.client.api)
    original_get = api_type._get

    def retain_response(self, url, *args, **kwargs):
        response = original_get(self, url, *args, **kwargs)
        if "/archive" in url:
            responses.append(response)
        return response

    @contextmanager
    def full_disk(**kwargs):
        raise OSError(errno.ENOSPC, "synthetic full export volume")
        yield

    monkeypatch.setattr(api_type, "_get", retain_response)
    before = counter(container)
    responses.clear()
    monkeypatch.setattr(worker.tempfile, "TemporaryFile", full_disk)
    container.client.api.timeout = 2
    try:
        # Retain the traceback too: GC is not an acceptable resource-release rule.
        with pytest.raises(OSError, match="synthetic full export volume") as failure:
            capture_worker(files[0], token=token, capture_id="9" * 32,
                destination=tmp_path / "unit", request_file=request, timeout=2)
        assert failure.value.errno == errno.ENOSPC
        assert not (tmp_path / "unit").exists()
        container.reload()
        assert container.attrs["State"]["Running"] and not container.attrs["State"]["Paused"]
        wait(lambda: counter(container) > before)
    finally:
        # Also makes a baseline regression run safe when it strands its reader.
        for response in responses:
            response.close()
        container.reload()
        if container.attrs["State"]["Paused"]:
            container.unpause()


@pytest.mark.parametrize("separate_bank", [False, True])
def test_concurrent_capture_does_not_release_another_operation_barrier(
        controllers, writer, tmp_path, monkeypatch, separate_bank):
    from concurrent.futures import ThreadPoolExecutor
    from loopx.file_lock import LockAcquireTimeoutError

    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    other_controller = files[0]
    if separate_bank:
        bank = tmp_path / "other-bank"
        bank.mkdir()
        config = json.loads(files[0].read_bytes())
        config["bank_root"] = str(bank)
        other_controller = tmp_path / "other-controller.json"
        other_controller.write_text(json.dumps(config))
        other_controller.chmod(0o600)
    entered, release = threading.Event(), threading.Event()
    original = transport._post
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(15)
        return original(*args, **kwargs)
    monkeypatch.setattr(transport, "_post", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(capture_worker, files[0], token=token, capture_id="1" * 32,
            destination=tmp_path / "first", request_file=request)
        try:
            assert entered.wait(15)
            frozen = counter(container)
            with pytest.raises(LockAcquireTimeoutError):
                capture_worker(other_controller, token=token, capture_id="2" * 32,
                    destination=tmp_path / "second", request_file=request, timeout=.5)
            container.reload()
            assert container.attrs["State"]["Paused"] and counter(container) == frozen
            assert not (tmp_path / "second").exists()
        finally:
            release.set()
        assert pending.result(timeout=20)["research_point_qualified"] is False
    container.reload()
    assert not container.attrs["State"]["Paused"]


@pytest.mark.parametrize("entrypoint", ["provider", "cli"])
def test_rejected_pause_preserves_foreign_barrier(
        controllers, writer, tmp_path, monkeypatch, entrypoint):
    from docker.errors import APIError

    source, _, first, _, files, _ = controllers
    container, request = writer
    token = register(source, first)
    original = type(container).pause
    def raced_pause(self):
        # Another actor pauses after our initial state read; the second RPC
        # reaches the real daemon and is determinately rejected.
        original(container)
        original(self)
    monkeypatch.setattr(type(container), "pause", raced_pause)
    def unexpected_native_call(*args, **kwargs):
        pytest.fail("Rejected writer barrier must not capture evaluator state")
    monkeypatch.setattr(transport, "_post", unexpected_native_call)
    unit = tmp_path / "rejected"
    with pytest.raises(APIError) as error:
        if entrypoint == "provider":
            capture_worker(files[0], token=token, capture_id="3" * 32,
                destination=unit, request_file=request)
        else:
            token_file = tmp_path / "token"
            token_file.write_text(token)
            transport.main(["--controller-file", str(files[0]), "capture-worker",
                "--token-file", str(token_file), "--capture-id", "3" * 32,
                "--destination", str(unit), "--worker-request", str(request)])
    assert error.value.response.status_code == 409
    container.reload()
    assert container.attrs["State"]["Paused"]
    assert not unit.exists()
    with pytest.raises(FileExistsError):
        capture_worker(files[0], token=token, capture_id="3" * 32,
            destination=tmp_path / "retry", request_file=request)


def test_capture_budget_is_shared_and_exhaustion_releases_writer(controllers, writer, tmp_path, monkeypatch):
    from benchmark.edgebench import checkpoint_worker

    source, _, first, _, files, grades = controllers
    container, request = writer
    token = register(source, first)
    original = checkpoint_worker._archive_component
    def slow_archive(*args, **kwargs):
        copied = original(*args, **kwargs)
        time.sleep(2.1)
        return copied
    monkeypatch.setattr(checkpoint_worker, "_archive_component", slow_archive)
    unit = tmp_path / "expired"
    with pytest.raises(TimeoutError, match="capture window"):
        capture_worker(files[0], token=token, capture_id="5" * 32,
            destination=unit, request_file=request, timeout=2)
    container.reload()
    assert container.attrs["State"]["Running"] and not container.attrs["State"]["Paused"]
    assert not unit.exists() and not grades
    assert not (Path(json.loads(files[0].read_text())["bank_root"]) / ("5" * 32)).exists()
    with pytest.raises(FileExistsError):
        capture_worker(files[0], token=token, capture_id="5" * 32,
            destination=tmp_path / "retry", request_file=request)
    # A different later boundary remains useful; the failed identity is not replayed.
    monkeypatch.setattr(checkpoint_worker, "_archive_component", original)
    budgets = []
    actual = checkpoint_worker.effect_runtime_result
    def checked_artifact(method, params, **kwargs):
        assert kwargs["retry_safe"] is False
        assert 10 < kwargs["timeout"] <= 45
        budgets.append(kwargs["timeout"])
        return actual(method, params, **kwargs)
    monkeypatch.setattr(checkpoint_worker, "effect_runtime_result", checked_artifact)
    monkeypatch.setattr(transport, "effect_runtime_result", checked_artifact)
    result = capture_worker(files[0], token=token, capture_id="6" * 32,
        destination=tmp_path / "later", request_file=request, timeout=45)
    assert result["sha256"] and len(budgets) == 4
    assert budgets == sorted(budgets, reverse=True)
    assert not grades


def test_persistent_mailbox_read_error_remains_visible(writer, tmp_path, monkeypatch):
    from benchmark.edgebench import checkpoint_supervisor

    container, _ = writer
    # No native admission is needed to exercise the read-only poll boundary.
    supervisor = object.__new__(checkpoint_supervisor.CheckpointSupervisor)
    supervisor.destination = tmp_path
    supervisor.failure = None
    supervisor.stop = threading.Event()
    monkeypatch.setattr(supervisor.stop, "wait", lambda seconds: None)
    reads = []
    def broken(*args):
        reads.append(True)
        raise tarfile.ReadError("persistent invalid archive")
    monkeypatch.setattr(checkpoint_supervisor, "_archive", broken)
    supervisor._run(container)
    assert len(reads) == 3 and isinstance(supervisor.failure, tarfile.ReadError)
    assert json.loads((tmp_path / "supervisor-failure.json").read_text()) == {"error_kind": "ReadError"}


@pytest.mark.parametrize("stage", ["download", "file_copy"])
def test_one_progressing_archive_expires_without_stranding_writer(
        controllers, writer, tmp_path, monkeypatch, stage):
    from contextlib import contextmanager
    import requests
    from benchmark.edgebench import checkpoint_worker as worker

    source, _, first, _, files, grades = controllers
    container, request = writer
    token = register(source, first)
    assert container.exec_run(["python", "-c",
        "from pathlib import Path;Path('/data/payload').write_bytes(b'x'*(16*1024*1024))"]).exit_code == 0
    before = counter(container)
    progress, responses = [], []
    original_get = type(container.client.api)._get
    def observed_get(self, url, *args, **kwargs):
        response = original_get(self, url, *args, **kwargs)
        if "/archive" in url:
            responses.append(response)
            # The archive reader must not use Docker's process-stream helper,
            # which deliberately changes this socket to an infinite timeout.
            socket = self._get_raw_response_socket(response)
            assert getattr(socket, "_sock", socket).gettimeout() is not None
        return response
    monkeypatch.setattr(type(container.client.api), "_get", observed_get)
    if stage == "download":
        original = requests.Response.iter_content
        def progressing(response, *args, **kwargs):
            if "/archive" not in response.url:
                yield from original(response, *args, **kwargs)
                return
            for block in original(response, *args, **kwargs):
                socket = container.client.api._get_raw_response_socket(response)
                assert getattr(socket, "_sock", socket).gettimeout() is not None
                progress.append(len(block))
                time.sleep(.04)
                yield block
        monkeypatch.setattr(requests.Response, "iter_content", progressing)
    else:
        original = worker.tempfile.TemporaryFile
        @contextmanager
        def slow_file(**kwargs):
            with original(**kwargs) as raw:
                class SlowFile:
                    def __getattr__(self, name):
                        return getattr(raw, name)
                    def read(self, size=-1):
                        block = raw.read(size)
                        if size > 512:
                            progress.append(len(block))
                            time.sleep(.01)
                        return block
                yield SlowFile()
        monkeypatch.setattr(worker.tempfile, "TemporaryFile", slow_file)
    responses.clear()
    progress.clear()
    started = time.monotonic()
    unit = tmp_path / "expired"
    try:
        with pytest.raises(TimeoutError, match="capture window closed"):
            capture_worker(files[0], token=token, capture_id="4" * 32,
                destination=unit, request_file=request, timeout=3)
        assert progress and sum(progress) < 16 * 1024 * 1024
        assert time.monotonic() - started < 10, "one archive must stop before consuming its slow remainder"
        assert all(response.raw.closed for response in responses)
        container.reload()
        assert container.attrs["State"]["Running"] and not container.attrs["State"]["Paused"]
        assert not unit.exists() and not grades
        bank = Path(json.loads(files[0].read_text())["bank_root"])
        assert not (bank / ("4" * 32)).exists()
        with pytest.raises(FileExistsError):
            capture_worker(files[0], token=token, capture_id="4" * 32,
                destination=tmp_path / "retry", request_file=request)
    finally:
        # Keep a failing characterization run safe too; no GC-based cleanup.
        for response in responses:
            response.close()
        container.reload()
        if container.attrs["State"]["Paused"]:
            container.unpause()
    # Observe the live writer only after the slowed archive reader is removed.
    monkeypatch.setattr(type(container.client.api), "_get", original_get)
    if stage == "download":
        monkeypatch.setattr(requests.Response, "iter_content", original)
    else:
        monkeypatch.setattr(worker.tempfile, "TemporaryFile", original)
    wait(lambda: counter(container) > before)


def test_declared_directory_includes_research_created_after_declaration(controllers, writer, tmp_path):
    from loopx.control_plane.effect_runtime import effect_runtime_result
    source, _, first, _, files, grades = controllers
    container, request = writer
    token = register(source, first)
    # writer already declared /data. New artifacts need no new component IDs.
    payload = json.loads(request.read_text())
    assert payload["components"] == [{"id": "native_capsule", "source_path": "/data"}]
    assert container.exec_run(["python", "-c", "from pathlib import Path; "
        "p=Path('/data/research/new-method'); p.mkdir(parents=True); "
        "(p/'candidate.py').write_text('candidate'); "
        "(p/'negative.json').write_text('{\"accepted\": false}'); "
        "Path('/outside-report.json').write_text('not declared')"]).exit_code == 0
    unit = tmp_path / "directory-point"
    result = capture_worker(files[0], token=token, capture_id="f" * 32,
                           destination=unit, request_file=request)
    assert result["integrity_verified"]
    assert (unit / "components/native_capsule/research/new-method/candidate.py").read_text() == "candidate"
    assert json.loads((unit / "components/native_capsule/research/new-method/negative.json").read_text()) == {"accepted": False}
    assert not list(unit.rglob("outside-report.json"))
    effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify", "root": str(unit),
        "expected_sha256": result["sha256"]})
    restored = tmp_path / "restored"
    receipt = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "materialize", "root": str(unit),
        "expected_sha256": result["sha256"], "destination": str(restored),
    })
    assert receipt["materialized"] and not receipt["activation_performed"]
    research = restored / "components/native_capsule/research/new-method"
    assert (research / "candidate.py").read_text() == "candidate"
    assert json.loads((research / "negative.json").read_text()) == {"accepted": False}
    assert not list(restored.rglob("outside-report.json"))
    assert request.read_text() == json.dumps(payload) and not grades
