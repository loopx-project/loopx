"""Docker writer capture isolation and real open-phase barrier checks."""
import base64
import io
import json
import os
from pathlib import Path
import tarfile
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from benchmark.runtime import docker_checkpoint
from benchmark.runtime.docker_checkpoint import capture_docker_worker, validate_worker_request
from benchmark.tests import test_checkpoint_worker
from loopx.control_plane.effect_runtime import effect_runtime_result


writer = test_checkpoint_worker.writer


def _tar_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def test_validate_worker_request_returns_isolated_snapshot():
    raw = {
        "container_id": "a" * 64,
        "components": [{"id": "worker_state", "source_path": "/data"}],
        "phase_deadline_path": "/phase",
        "handoff": {"source_path": "/handoff.json", "sha256": "c" * 64},
    }
    snapshot = validate_worker_request(raw)
    assert snapshot is not raw
    assert snapshot["components"] is not raw["components"]
    assert snapshot["components"][0] is not raw["components"][0]
    assert snapshot["handoff"] is not raw["handoff"]

    raw["container_id"] = "b" * 64
    raw["phase_deadline_path"] = "/other-phase"
    raw["components"][0]["source_path"] = "/other"
    raw["components"].append({"id": "extra", "source_path": "/extra"})
    raw["handoff"]["sha256"] = "d" * 64

    assert snapshot == {
        "container_id": "a" * 64,
        "components": [{"id": "worker_state", "source_path": "/data"}],
        "phase_deadline_path": "/phase",
        "handoff": {"source_path": "/handoff.json", "sha256": "c" * 64},
    }


def test_dual_container_request_mutation_during_lock_binds_capture_to_original_snapshot(tmp_path, monkeypatch):
    container_a_id = "a" * 64
    container_b_id = "b" * 64
    archives = {
        (container_a_id, "/data"): _tar_bytes({"data/origin": b"worker-A"}),
        (container_a_id, "/phase"): _tar_bytes({"phase": str(int(time.time()) + 3600).encode()}),
        (container_b_id, "/data"): _tar_bytes({"data/origin": b"worker-B"}),
        (container_b_id, "/phase"): _tar_bytes({"phase": str(int(time.time()) + 3600).encode()}),
    }

    class FakeResponse:
        def __init__(self, payload: bytes):
            self.headers = {"x-docker-container-path-stat": base64.b64encode(b"{}").decode()}
            self._payload = payload

        def iter_content(self, _chunk_size, decode_unicode=False):
            yield self._payload

        def close(self):
            pass

    class FakeContainer:
        def __init__(self, cid: str):
            self.id = cid
            self.attrs = {"State": {"Running": True, "Paused": False}, "Image": f"sha256:{cid}"}
            self.client = None
            self.pause_calls = 0

        def reload(self):
            pass

        def pause(self):
            self.pause_calls += 1
            self.attrs["State"]["Paused"] = True

        def unpause(self):
            self.attrs["State"]["Paused"] = False

    class FakeApi:
        timeout = 30

        def _url(self, template: str, cid: str) -> str:
            return template.format(cid)

        def _get(self, url: str, *, params: dict, **_kwargs):
            cid = url.split("/")[2]
            key = (cid, params["path"])
            if key not in archives:
                raise docker.errors.NotFound("missing")
            return FakeResponse(archives[key])

        def _raise_for_status(self, _response):
            pass

    containers = {container_a_id: FakeContainer(container_a_id), container_b_id: FakeContainer(container_b_id)}
    fake_client = SimpleNamespace(
        api=FakeApi(),
        info=lambda: {"ID": "fake-daemon-id"},
        containers=SimpleNamespace(get=lambda cid: containers[cid]),
        close=lambda: None,
    )
    for container in containers.values():
        container.client = fake_client

    import docker

    monkeypatch.setattr(docker, "from_env", lambda timeout=30: fake_client)
    # Filename fidelity is tested independently on compatible volumes by test_checkpoint_storage.
    monkeypatch.setattr(docker_checkpoint, "qualify_export_storage", lambda _path: None)

    request = {
        "container_id": container_a_id,
        "components": [{"id": "worker_state", "source_path": "/data"}],
        "phase_deadline_path": "/phase",
    }
    lock_paths = []
    real_worker_lock = docker_checkpoint._worker_lock

    def mutating_worker_lock(client, cid: str):
        lock_path = real_worker_lock(client, cid)
        lock_paths.append(lock_path)
        mutated = threading.Event()

        def mutate():
            request["container_id"] = container_b_id
            request["components"][0]["source_path"] = "/other"
            request["components"].append({"id": "injected", "source_path": "/injected"})
            request["phase_deadline_path"] = None
            mutated.set()

        thread = threading.Thread(target=mutate)
        thread.start()
        thread.join()
        assert mutated.is_set()
        return lock_path

    monkeypatch.setattr(docker_checkpoint, "_worker_lock", mutating_worker_lock)

    opportunity = tmp_path / "train-calls"
    opportunity.write_text("7")
    bank = tmp_path / "bank"
    bank.mkdir()
    destination = tmp_path / "point"

    def capture_native(target, phase, remaining):
        assert containers[container_a_id].attrs["State"]["Paused"]
        assert not containers[container_b_id].attrs["State"]["Paused"]
        assert phase is not None and 0 < remaining <= 30
        probe = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "probe", "components": [{"id": "train_calls", "source_path": str(opportunity)}],
        })
        return effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "capture", "destination": str(target), "expected_source_sha256": probe["source_sha256"],
            "components": [{"id": "train_calls", "source_path": str(opportunity)}], "point_metadata": {},
        })

    receipt = capture_docker_worker(
        capture_id=uuid4().hex, destination=destination, native_bank=bank,
        request=request, capture_native=capture_native,
    )
    assert receipt["integrity_verified"]
    assert len(lock_paths) == 1 and lock_paths[0].name.endswith(f"-{container_a_id}")
    assert containers[container_a_id].pause_calls == 1
    assert containers[container_b_id].pause_calls == 0
    assert containers[container_a_id].attrs["State"] == {"Running": True, "Paused": False}
    assert containers[container_b_id].attrs["State"] == {"Running": True, "Paused": False}

    point = json.loads((destination / "checkpoint.json").read_bytes())
    metadata = point["point_metadata"]["worker_file_snapshot"]
    assert metadata["container_id"] == container_a_id
    assert metadata["components"] == [{"id": "worker_state", "source_path": "/data"}]
    assert metadata["phase_deadline_path"] == "/phase"
    assert (destination / "components" / "worker_state" / "origin").read_bytes() == b"worker-A"


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires explicit isolated Docker writer-barrier qualification")
def test_dual_container_capture_binds_lock_and_sealed_point_to_original_worker(writer, tmp_path, monkeypatch):
    import docker

    source_a, path = writer
    client = docker.from_env(timeout=15)
    image = os.environ.get("LOOPX_CHECKPOINT_WRITER_IMAGE", "python:3.12-slim")
    script_b = (
        "from pathlib import Path\n"
        "import time\n"
        "Path('/data').mkdir()\n"
        "Path('/phase').write_text(str(int(time.time()) + 7200))\n"
        "Path('/data/build-output').write_bytes(b'foreign worker B')\n"
        "time.sleep(600)\n"
    )
    source_b = client.containers.run(
        image, ["python", "-c", script_b], detach=True,
        name="loopx-checkpoint-decoy-" + uuid4().hex[:12],
        network_disabled=True, mem_limit="64m", nano_cpus=250_000_000,
    )
    try:
        test_checkpoint_worker.wait(lambda: source_b.exec_run(["test", "-f", "/data/build-output"]).exit_code == 0)
        request = json.loads(path.read_bytes())
        assert request["container_id"] == source_a.id
        lock_paths = []
        real_worker_lock = docker_checkpoint._worker_lock

        def mutating_worker_lock(lock_client, cid: str):
            lock_path = real_worker_lock(lock_client, cid)
            lock_paths.append(lock_path)

            def mutate():
                request["container_id"] = source_b.id
                request["components"][0]["source_path"] = "/other"

            thread = threading.Thread(target=mutate)
            thread.start()
            thread.join()
            return lock_path

        monkeypatch.setattr(docker_checkpoint, "_worker_lock", mutating_worker_lock)

        opportunity = tmp_path / "train-calls"
        opportunity.write_text("9")
        bank = tmp_path / "bank"
        bank.mkdir()
        destination = tmp_path / "point"

        def capture_native(target, phase, remaining):
            source_a.reload()
            source_b.reload()
            assert source_a.attrs["State"]["Paused"]
            assert not source_b.attrs["State"]["Paused"]
            assert phase is not None and 0 < remaining <= 30
            probe = effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "probe", "components": [{"id": "train_calls", "source_path": str(opportunity)}],
            })
            return effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "capture", "destination": str(target), "expected_source_sha256": probe["source_sha256"],
                "components": [{"id": "train_calls", "source_path": str(opportunity)}], "point_metadata": {},
            })

        receipt = capture_docker_worker(
            capture_id=uuid4().hex, destination=destination, native_bank=bank,
            request=request, capture_native=capture_native,
        )
        assert receipt["integrity_verified"]
        assert len(lock_paths) == 1 and lock_paths[0].name.endswith(f"-{source_a.id}")
        point = json.loads((destination / "checkpoint.json").read_bytes())
        metadata = point["point_metadata"]["worker_file_snapshot"]
        assert metadata["container_id"] == source_a.id
        assert metadata["components"] == [{"id": "native_capsule", "source_path": "/data"}]
        assert (destination / "components" / "native_capsule" / "build-output").read_bytes() == b"compiled artifact"
        source_a.reload()
        source_b.reload()
        assert source_a.attrs["State"]["Running"] and not source_a.attrs["State"]["Paused"]
        assert source_b.attrs["State"]["Running"] and not source_b.attrs["State"]["Paused"]
    finally:
        source_b.remove(force=True)
        client.close()


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires explicit isolated Docker writer-barrier qualification")
def test_callback_seals_open_phase_with_shared_writer_barrier(writer, tmp_path):
    source, path = writer
    request = json.loads(path.read_bytes())
    request["phase_deadline_path"] = None
    with pytest.raises(ValueError, match="Worker paths"):
        validate_worker_request(request)

    opportunity = tmp_path / "train-calls"
    opportunity.write_text("3")
    bank = tmp_path / "bank"
    bank.mkdir()
    destination = tmp_path / "point"

    def capture_native(target, phase, remaining):
        source.reload()
        assert source.attrs["State"]["Paused"] and phase is None and 0 < remaining <= 30
        probe = effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "probe", "components": [{"id": "train_calls", "source_path": str(opportunity)}],
        })
        return effect_runtime_result("runtime.checkpoint_artifacts", {
            "action": "capture", "destination": str(target), "expected_source_sha256": probe["source_sha256"],
            "components": [{"id": "train_calls", "source_path": str(opportunity)}], "point_metadata": {},
        })

    receipt = capture_docker_worker(
        capture_id=uuid4().hex, destination=destination, native_bank=bank,
        request=request, capture_native=capture_native, allow_open_phase=True,
    )
    assert receipt["integrity_verified"]
    point = json.loads((destination / "checkpoint.json").read_bytes())
    metadata = point["point_metadata"]["worker_file_snapshot"]
    capsule = destination / "components" / metadata["native_capsule_component_id"]
    assert metadata["phase_deadline_path"] is None
    assert (capsule / "components/train_calls").read_text() == "3"
    source.reload()
    assert source.attrs["State"]["Running"] and not source.attrs["State"]["Paused"]


def _fake_single_worker_docker(monkeypatch, container_id: str, on_unpause=None):
    archives = {
        (container_id, "/data"): _tar_bytes({"data/origin": b"worker-A"}),
        (container_id, "/phase"): _tar_bytes({"phase": str(int(time.time()) + 3600).encode()}),
    }

    class FakeResponse:
        def __init__(self, payload: bytes):
            self.headers = {"x-docker-container-path-stat": base64.b64encode(b"{}").decode()}
            self._payload = payload

        def iter_content(self, _chunk_size, decode_unicode=False):
            yield self._payload

        def close(self):
            pass

    class FakeContainer:
        def __init__(self, cid: str):
            self.id = cid
            self.attrs = {"State": {"Running": True, "Paused": False}, "Image": f"sha256:{cid}"}
            self.client = None
            self.unpause_calls = 0

        def reload(self):
            pass

        def pause(self):
            self.attrs["State"]["Paused"] = True

        def unpause(self):
            self.unpause_calls += 1
            self.attrs["State"]["Paused"] = False
            if on_unpause is not None:
                on_unpause()

    class FakeApi:
        timeout = 30

        def _url(self, template: str, cid: str) -> str:
            return template.format(cid)

        def _get(self, url: str, *, params: dict, **_kwargs):
            cid = url.split("/")[2]
            return FakeResponse(archives[(cid, params["path"])])

        def _raise_for_status(self, _response):
            pass

    container = FakeContainer(container_id)
    fake_client = SimpleNamespace(
        api=FakeApi(),
        info=lambda: {"ID": "fake-daemon-id"},
        containers=SimpleNamespace(get=lambda cid: container),
        close=lambda: None,
    )
    container.client = fake_client

    import docker

    monkeypatch.setattr(docker, "from_env", lambda timeout=30: fake_client)
    monkeypatch.setattr(docker_checkpoint, "qualify_export_storage", lambda _path: None)
    return container


def _capture_local_capsule(source_file: Path, target: Path) -> dict:
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "probe", "components": [{"id": "train_calls", "source_path": str(source_file)}],
    })
    return effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "capture", "destination": str(target), "expected_source_sha256": probe["source_sha256"],
        "components": [{"id": "train_calls", "source_path": str(source_file)}], "point_metadata": {},
    })


def test_capture_binds_immutable_native_sha256_against_mutation_on_release(tmp_path, monkeypatch):
    container_id = "a" * 64
    stale_source = tmp_path / "stale-train"
    stale_source.write_text("1")
    stale_receipt = _capture_local_capsule(stale_source, tmp_path / "stale-capsule")

    current_source = tmp_path / "current-train"
    current_source.write_text("7")
    captured_receipt: dict | None = None

    def mutate_on_unpause():
        assert captured_receipt is not None
        captured_receipt["sha256"] = stale_receipt["sha256"]
        captured_receipt["integrity_verified"] = False

    container = _fake_single_worker_docker(monkeypatch, container_id, on_unpause=mutate_on_unpause)
    bank = tmp_path / "bank"
    bank.mkdir()
    destination = tmp_path / "point"
    expected_sha: list[str] = []

    def capture_native(target, phase, remaining):
        nonlocal captured_receipt
        captured_receipt = _capture_local_capsule(current_source, target)
        expected_sha.append(captured_receipt["sha256"])
        return captured_receipt

    receipt = capture_docker_worker(
        capture_id=uuid4().hex, destination=destination, native_bank=bank,
        request={
            "container_id": container_id,
            "components": [{"id": "worker_state", "source_path": "/data"}],
            "phase_deadline_path": "/phase",
        },
        capture_native=capture_native,
    )
    assert receipt["integrity_verified"] is True
    assert container.unpause_calls == 1 and container.attrs["State"] == {"Running": True, "Paused": False}
    point = json.loads((destination / "checkpoint.json").read_bytes())
    metadata = point["point_metadata"]["worker_file_snapshot"]
    assert metadata["native_capsule_sha256"] == expected_sha[0]
    assert metadata["native_capsule_sha256"] != stale_receipt["sha256"]
    verified = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify",
        "root": str(destination / "components" / metadata["native_capsule_component_id"]),
        "expected_sha256": metadata["native_capsule_sha256"],
    })
    assert verified["integrity_verified"] is True


def test_capture_rejects_stale_native_capsule_receipt_and_unpauses_worker(tmp_path, monkeypatch):
    container_id = "a" * 64
    stale_source = tmp_path / "stale-train"
    stale_source.write_text("1")
    stale_receipt = _capture_local_capsule(stale_source, tmp_path / "stale-capsule")

    current_source = tmp_path / "current-train"
    current_source.write_text("9")
    container = _fake_single_worker_docker(monkeypatch, container_id)
    bank = tmp_path / "bank"
    bank.mkdir()
    destination = tmp_path / "point"

    def capture_stale_native(target, phase, remaining):
        _capture_local_capsule(current_source, target)
        return dict(stale_receipt)

    with pytest.raises(Exception, match="checkpoint reviewed digest mismatch|Materialized native capsule"):
        capture_docker_worker(
            capture_id=uuid4().hex, destination=destination, native_bank=bank,
            request={
                "container_id": container_id,
                "components": [{"id": "worker_state", "source_path": "/data"}],
                "phase_deadline_path": "/phase",
            },
            capture_native=capture_stale_native,
        )
    assert not destination.exists()
    assert container.unpause_calls == 1
    assert container.attrs["State"] == {"Running": True, "Paused": False}


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires explicit isolated Docker writer-barrier qualification")
def test_real_docker_capture_rejects_stale_and_mutated_native_receipts(writer, tmp_path, monkeypatch):
    import docker as docker_mod

    source, path = writer
    request = json.loads(path.read_bytes())
    stale_source = tmp_path / "stale-train"
    stale_source.write_text("1")
    stale_receipt = _capture_local_capsule(stale_source, tmp_path / "stale-capsule")

    current_source = tmp_path / "current-train"
    current_source.write_text("5")
    bank = tmp_path / "bank"
    bank.mkdir()
    rejected = tmp_path / "rejected-point"

    with pytest.raises(Exception, match="checkpoint reviewed digest mismatch|Materialized native capsule"):
        capture_docker_worker(
            capture_id=uuid4().hex, destination=rejected, native_bank=bank, request=request,
            capture_native=lambda target, _phase, _rem: (_capture_local_capsule(current_source, target) and dict(stale_receipt)),
        )
    assert not rejected.exists()
    source.reload()
    assert source.attrs["State"]["Running"] and not source.attrs["State"]["Paused"]

    # Also verify returning valid receipt then mutating it during real container unpause preserves valid seal.
    accepted = tmp_path / "accepted-point"
    expected_sha: list[str] = []
    mutable_holder: dict = {}
    orig_from_env = docker_mod.from_env

    def capture_mutated_on_release(target, _phase, _rem):
        rec = _capture_local_capsule(current_source, target)
        expected_sha.append(rec["sha256"])
        mutable_holder["rec"] = rec
        return rec

    def wrapped_from_env(*args, **kwargs):
        real_client = orig_from_env(*args, **kwargs)
        orig_get = real_client.containers.get

        def wrapped_get(cid):
            c = orig_get(cid)
            real_c_unpause = c.unpause

            def wrapped_unpause():
                real_c_unpause()
                if "rec" in mutable_holder:
                    mutable_holder["rec"]["sha256"] = stale_receipt["sha256"]
                    mutable_holder["rec"]["integrity_verified"] = False

            c.unpause = wrapped_unpause
            return c

        real_client.containers.get = wrapped_get
        return real_client

    monkeypatch.setattr(docker_mod, "from_env", wrapped_from_env)
    receipt = capture_docker_worker(
        capture_id=uuid4().hex, destination=accepted, native_bank=bank,
        request=request, capture_native=capture_mutated_on_release,
    )
    assert receipt["integrity_verified"] is True
    point = json.loads((accepted / "checkpoint.json").read_bytes())
    metadata = point["point_metadata"]["worker_file_snapshot"]
    assert metadata["native_capsule_sha256"] == expected_sha[0]
    verified = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify",
        "root": str(accepted / "components" / metadata["native_capsule_component_id"]),
        "expected_sha256": metadata["native_capsule_sha256"],
    })
    assert verified["integrity_verified"] is True
    source.reload()
    assert source.attrs["State"]["Running"] and not source.attrs["State"]["Paused"]
