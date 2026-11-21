"""Docker writer capture isolation and real open-phase barrier checks."""
import base64
import io
import json
import os
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
