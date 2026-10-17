"""The product caller reads original work and recovers its prepared source.

This is the source half of the transfer continuation journey. It deliberately
requires a visible destination-admission hold instead of a false move result.
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.goals.planned_transfer_io import (
    prepare_registered_goal_transfer,
    preview_registered_goal_transfer,
)
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture

source = source_fixture


def _http(source: dict):
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.runtime_root = source["runtime"]
    server.registry_path = source["registry"]
    server.runtime_root_override = str(source["runtime"])
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(body: dict, *, origin: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=90)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        connection.request("POST", "/api/chat/goal-transfer", json.dumps(body), headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    return server, thread, call


def test_shared_caller_preserves_the_original_work_and_never_claims_cutover(source: dict):
    registry, cli = source["registry"], source["cli"]
    accepted = cli("todo", "add", "--goal-id", "report", "--role", "user", "--task-class", "user_action",
        "--text", "Keep input.txt and accept the original sum report",
        "--operation-id", "original-report-context")["todo_id"]
    cli("todo", "complete", "--goal-id", "report", "--role", "user", "--todo-id", accepted,
        "--agent-id", "agent-a", "--note", "Keep the original input and acceptance", "--evidence", "input.txt",
        "--successor-todo-id", source["todo"])
    cli("todo", "archive-completed", "--goal-id", "report", "--role", "user", "--max-active-done", "0", "--execute")
    before = registry.read_bytes(), source["fence"].read_bytes()
    expected = cli("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    original = read_canonical_todos_if_promoted(runtime_root=source["runtime"], goal_id="report")
    assert original is not None
    inspected = cli("goal-transfer", "inspect", "--goal-id", "report")
    assert inspected["work"]["todos"] == original["todos"]
    archived = next(row for row in inspected["work"]["todos"] if row["todo_id"] == accepted)
    assert archived["archive_state"] == "archive" and archived["successor_todo_ids"] == [source["todo"]]
    assert inspected["work"]["objective"] == "Produce report.txt from input.txt"
    assert inspected["work"]["agents"] == ["agent-a"]
    assert inspected["source_state"] == "available" and inspected["operation_id"] is None
    assert inspected["continuation_available"] is False
    assert any(row["code"] == "destination_admission_unavailable" for row in inspected["holds"])
    assert before == (registry.read_bytes(), source["fence"].read_bytes())

    prepared = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a")
    prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a",
        expected_preview_sha256=prepared["preview_sha256"])
    server, thread, call = _http(source)
    try:
        status, app = call({"action": "inspect", "goal_id": "report"})
        assert status == 200 and app == cli("goal-transfer", "inspect", "--goal-id", "report")
        assert app["source_state"] == "prepared" and app["operation_id"] == "move-a"
        assert app["cancel_available"] is True
        assert app["work"]["todos"] == original["todos"]
        # Refusal is a real native-writer refusal, not a disabled UI button.
        assert "prepared for transfer" in source["acquire"]("while-prepared", expected_code=1)["error"]
        # A browser reload/cold transport does not require the user to find an
        # operation id, runtime path, source receipt or owner command.
        restart_effect_runtime(wait_for_exit=True)
        assert call({"action": "inspect", "goal_id": "report"})[1] == app
        status, cancelled = call({"action": "cancel", "goal_id": "report", "operation_id": "move-a"})
        assert status == 200 and cancelled["readback"]["original_writer_fence_restored"] is True
        # Duplicate confirmation reads the original decision; no second grant.
        assert call({"action": "cancel", "goal_id": "report", "operation_id": "move-a"})[1] == cancelled
        resumed = call({"action": "inspect", "goal_id": "report"})[1]
        assert resumed["source_state"] == "available" and resumed["work"]["todos"] == original["todos"]
        lease = source["acquire"]("resume-original-report")["lease"]
        # Independent acceptance is fixed by the original request/data, not by
        # anything produced by the transfer implementation. This proves source
        # recovery only; the destination has not produced this report.
        report = Path(source["project"]) / "report.txt"
        report.write_text(str(sum(map(int, (Path(source["project"]) / "input.txt").read_text().split()))) + "\n")
        assert report.read_text() == "21\n"
        source["release"](lease)
        assert registry.read_bytes() == before[0]
        assert cli("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"] == expected
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def test_browser_cannot_supply_owner_paths_or_waive_destination_admission(source: dict):
    server, thread, call = _http(source)
    try:
        before = source["registry"].read_bytes(), source["fence"].read_bytes()
        for body in [
            {"action": "inspect", "goal_id": "report", "runtime_root": "/elsewhere"},
            {"action": "inspect", "goal_id": "report", "source_context": {}},
            {"action": "activate", "goal_id": "report"},
            {"action": "cancel", "goal_id": "report"},
        ]:
            status, value = call(body)
            assert status == 400 and value["ok"] is False
            assert str(source["project"]) not in json.dumps(value)
        assert call({"action": "inspect", "goal_id": "report"}, origin="https://untrusted.invalid")[0] == 403
        assert before == (source["registry"].read_bytes(), source["fence"].read_bytes())
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
