"""A real Docker capture with an evaluator that has no solver-phase clock."""
import json
import os
from uuid import uuid4

import pytest

from benchmark.runtime.docker_checkpoint import capture_docker_worker, validate_worker_request
from benchmark.tests import test_checkpoint_worker
from loopx.control_plane.effect_runtime import effect_runtime_result


writer = test_checkpoint_worker.writer


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
