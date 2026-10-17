"""Ordinary Goal reads/actions follow the admitted local target, not old workers."""
from __future__ import annotations

import http.client
import json

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError
from loopx.control_plane.goals.planned_transfer_io import control_transferred_goal, inspect_registered_goal_transfer
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture
from tests.control_plane.test_planned_transfer_operation import _http
from tests.control_plane.test_planned_transfer_product import _goal_document

source = source_fixture


def _status(server):
    connection = http.client.HTTPConnection(*server.server_address, timeout=90)
    try:
        connection.request("GET", "/status.json?goal_id=report")
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def _preview(service, operation, key):
    return service.preview({"action_kind": "goal.lifecycle", "summary": operation,
        "normalized_parameters": {"goal_id": "report", "operation": operation},
        "context": {"kind": "goal_directory", "goal_id": "report"}, "idempotency_key": key})


def _status_server(source):
    server, thread, call = _http(source)
    server.selected_goal_id = None
    server.scan_roots = [source["project"]]
    server.limit = 50
    server.goal_subagent_configuration_enabled = False
    return server, thread, call


def test_ordinary_goal_workspace_follows_current_target_and_rejects_source_fallback(source, tmp_path):
    _goal_document(source, absolute=True)
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=source["registry"])
    old_preview = _preview(service, "stop", "before-move")
    destination = tmp_path / "destination"
    destination.mkdir()
    prepared = source["cli"]("goal-transfer", "prepare-local", "--goal-id", "report",
        "--operation-id", "move-a", "--destination-root", str(destination), "--execute")
    server, thread, call = _status_server(source)
    try:
        assert call({"action": "confirm", "goal_id": "report", "operation_id": "move-a",
            "preview_sha256": prepared["prepared_cutover"]["preview_sha256"]})[0] == 200
        source_before = source["registry"].read_bytes(), source["fence"].read_bytes()
        control = inspect_registered_goal_transfer(registry_path=source["registry"], goal_id="report")["destination_control"]
        control_transferred_goal(registry_path=source["registry"], goal_id="report", operation_id="move-a",
            operation="stop", expected_state_fingerprint=control["expected_state_fingerprint"])
        status, view = _status(server)
        assert status == 200
        assert view["run_history"]["goals"][0]["activation_state"] == "stopped"
        assert view["run_history"]["goals"][0]["goal_objective"] == "Produce report.txt from input.txt"
        pending = view["attention_queue"]["items"][0]["agent_todos"]["items"]
        assert any(todo["todo_id"] == source["todo"] and todo["status"] == "open" for todo in pending)
        assert str(destination) not in json.dumps(view)
        # Approval of the old source cannot be laundered into target authority.
        assert service.apply(old_preview["proposal_id"])["proposal"]["status"] == "stale"
        resume = _preview(service, "resume", "resume-current-target")
        assert service.apply(resume["proposal_id"])["proposal"]["status"] == "applied"
        target_registry = destination / "workspace/.loopx/registry.json"
        assert json.loads(target_registry.read_text())["goals"][0]["activation"]["state"] == "active"
        assert _status(server)[1]["run_history"]["goals"][0]["activation_state"] == "active"
        assert source_before == (source["registry"].read_bytes(), source["fence"].read_bytes())
        retained = target_registry.with_suffix(".retained")
        target_registry.rename(retained)
        try:
            status, unavailable = _status(server)
            assert status >= 400 and "run_history" not in unavailable
            with pytest.raises((EffectRuntimeRemoteError, ValueError, OSError)):
                _preview(service, "stop", "unavailable-target")
            assert source_before == (source["registry"].read_bytes(), source["fence"].read_bytes())
        finally:
            retained.rename(target_registry)
        assert _status(server)[0] == 200
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_ordinary_goal_without_transfer_keeps_its_existing_lifecycle_owner(source, tmp_path):
    _goal_document(source, absolute=True)
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"), registry_path=source["registry"])
    original_fence = source["fence"].read_bytes()
    server, thread, _ = _status_server(source)
    try:
        assert _status(server)[1]["run_history"]["goals"][0]["activation_state"] == "active"
        stop = _preview(service, "stop", "ordinary-stop")
        assert service.apply(stop["proposal_id"])["proposal"]["status"] == "applied"
        assert json.loads(source["registry"].read_text())["goals"][0]["activation"]["state"] == "stopped"
        resume = _preview(service, "resume", "ordinary-resume")
        assert service.apply(resume["proposal_id"])["proposal"]["status"] == "applied"
        assert _status(server)[1]["run_history"]["goals"][0]["activation_state"] == "active"
        assert source["fence"].read_bytes() == original_fence
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
