"""Original transfer entry controls the admitted destination, never old writers."""
from __future__ import annotations

import json

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, restart_effect_runtime
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_operation import _http
from tests.control_plane.test_planned_transfer_product import _goal_document

source = source_fixture


def test_original_entry_stops_and_resumes_only_the_admitted_destination(source, tmp_path):
    _goal_document(source, absolute=True)
    destination = tmp_path / "destination"
    destination.mkdir()
    prepared = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report",
        "--operation-id", "move-a", "--destination-root", str(destination), "--execute")
    server, thread, call = _http(source)
    try:
        status, accepted = call({"action": "confirm", "goal_id": "report", "operation_id": "move-a",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]})
        assert status == 200 and accepted["recorded"] is True
        assert "execution" not in accepted
        target_registry = destination / "workspace/.loopx/registry.json"
        source_before = source["registry"].read_bytes(), source["fence"].read_bytes()
        target_before = target_registry.read_bytes()
        admission = FirstPartyHostGoalAdmission.for_plan(registry_path=target_registry,
            goal_id="report", planned_goal_ref=None)
        admission.require_current()
        view = call({"action": "inspect", "goal_id": "report"})[1]
        control = view["destination_control"]
        assert control["operation"] == "stop"
        assert "registry" not in json.dumps(control)

        stop = {"action": "stop", "goal_id": "report", "operation_id": "move-a",
            "expected_state_fingerprint": control["expected_state_fingerprint"]}
        for invalid in ({**stop, "operation_id": "other-move"},
                        {**stop, "expected_state_fingerprint": "0" * 64},
                        {**stop, "target_registry": str(source["registry"])}):
            assert call(invalid)[0] == 400
            assert target_registry.read_bytes() == target_before
            assert source_before == (source["registry"].read_bytes(), source["fence"].read_bytes())

        status, stopped = call(stop)
        assert status == 200 and stopped["readback"]["local_cutover"]["target_activation_state"] == "stopped"
        assert stopped["readback"]["destination_control"]["operation"] == "resume"
        with pytest.raises(EffectRuntimeRemoteError, match="Current Goal registration no longer matches"):
            admission.require_current()
        stopped_bytes = target_registry.read_bytes()
        # A response-loss retry observes the same state, without rewriting its
        # timestamp, source route or any execution receipt.
        restart_effect_runtime(wait_for_exit=True)
        assert call(stop)[0] == 200
        assert target_registry.read_bytes() == stopped_bytes
        assert source_before == (source["registry"].read_bytes(), source["fence"].read_bytes())
        assert "prepared for transfer" in source["acquire"]("old-source-after-stop", expected_code=1)["error"]

        resume = {"action": "resume", "goal_id": "report", "operation_id": "move-a",
            "expected_state_fingerprint": stopped["readback"]["destination_control"]["expected_state_fingerprint"]}
        status, resumed = call(resume)
        assert status == 200 and resumed["readback"]["local_cutover"]["target_activation_state"] == "active"
        admission.require_current()
        assert source_before == (source["registry"].read_bytes(), source["fence"].read_bytes())
        assert not (destination / "runtime/goals/report/turns").exists()
        assert not (destination / "runtime/goals/report/turn-sessions").exists()
        assert resumed["readback"]["work"] == accepted["readback"]["work"]
        assert "prepared for transfer" in source["acquire"]("old-source-after-resume", expected_code=1)["error"]
        resumed_bytes = target_registry.read_bytes()
        assert call(stop)[0] == 400
        assert target_registry.read_bytes() == resumed_bytes
        # The work view can remain readable while its lifecycle projection is
        # unavailable. Do not present a control or fall back to the old source.
        projection = destination / "runtime/registry.global.json"
        retained = projection.with_suffix(".retained")
        projection.rename(retained)
        projection.mkdir()
        try:
            unavailable = call({"action": "inspect", "goal_id": "report"})[1]
            assert unavailable["destination_control_unavailable"] is True
            assert "destination_control" not in unavailable
            assert unavailable["work"] == resumed["readback"]["work"]
            assert call({**stop, "expected_state_fingerprint": resumed["readback"]["destination_control"]["expected_state_fingerprint"]})[0] == 400
            assert target_registry.read_bytes() == resumed_bytes
            assert source_before == (source["registry"].read_bytes(), source["fence"].read_bytes())
        finally:
            projection.rmdir()
            retained.rename(projection)
        assert call({"action": "inspect", "goal_id": "report"})[1]["destination_control"] == resumed["readback"]["destination_control"]
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_source_only_preparation_never_exposes_destination_control(source, tmp_path):
    _goal_document(source, absolute=True)
    original = source["cli"]("goal-transfer", "inspect", "--goal-id", "report")
    assert "destination_control" not in original
    server, thread, call = _http(source)
    before = source["registry"].read_bytes(), source["fence"].read_bytes()
    try:
        assert call({"action": "resume", "goal_id": "report", "operation_id": "move-a",
            "expected_state_fingerprint": "0" * 64})[0] == 400
        assert before == (source["registry"].read_bytes(), source["fence"].read_bytes())
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
