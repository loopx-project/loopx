"""Real source/HTTP acceptance for the on-demand workspace review."""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.runtime.public_safety import validate_public_safe_value
from loopx.state_refresh import refresh_state_run
from loopx.status import collect_status


def make_project(root: Path) -> tuple[Path, Path]:
    """A synthetic engineering handoff, usable by the packaged-browser smoke."""
    project, runtime = root / "project", root / "runtime"
    project.mkdir(parents=True)
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\nstatus: active\n---\n\n# Release workspace\n\n## Agent Todo\n\n"
        "- [ ] Integrate the verified release package\n"
        "  <!-- loopx:todo todo_id=todo_integrate status=open task_class=advancement_task claimed_by=builder -->\n"
        "- [x] Specify the release acceptance criteria\n"
        "  <!-- loopx:todo todo_id=todo_design status=done task_class=advancement_task claimed_by=reviewer successor_todo_ids=todo_integrate unblocks_todo_id=todo_integrate evidence=acceptance-spec-v1 -->\n\n"
        "## User Todo\n\n"
        "- [ ] Review the release candidate\n"
        "  <!-- loopx:todo todo_id=todo_review status=open task_class=user_gate blocks_agent=builder -->\n"
        "- [ ] Confirm support coverage\n"
        "  <!-- loopx:todo todo_id=todo_support status=open task_class=user_gate -->\n"
        "- [ ] Approve the publication boundary\n"
        "  <!-- loopx:todo todo_id=todo_publish status=open task_class=user_gate -->\n"
    )
    registry = project / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": "release-demo", "display_name": "Release collaboration", "status": "active", "domain": "software",
        "repo": str(project), "state_file": state.name,
        "adapter": {"kind": "harness_self_improvement", "status": "connected-read-only"},
    }]}))
    refresh_state_run(
        registry_path=registry, runtime_root_override=str(runtime), goal_id="release-demo", project=project,
        state_file=state, classification="state_refreshed", recommended_action="Review the independent verification evidence",
        agent_id="reviewer", agent_vision_packet={"vision_patch": {
            "acceptance_summary": "Independent installation and recovery evidence",
            "replan_trigger_summary": "The independent verification is not complete",
        }}, dry_run=False, sync_global=False,
    )
    return registry, runtime


@pytest.fixture
def workspace(tmp_path):
    registry, runtime = make_project(tmp_path)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path = registry
    server.runtime_root_override = str(runtime)
    server.runtime_root = runtime
    server.scan_roots = []
    server.limit = 20
    server.selected_goal_id = None
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, registry, runtime
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def get(server, path="/api/chat/delivery-review?goal_id=release-demo", origin=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=30)
    connection.request("GET", path, headers={"Origin": origin} if origin else {})
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()
    return response.status, body


def test_real_http_review_matches_cli_sources_without_canonical_writes(workspace):
    server, registry, runtime = workspace
    state = registry.parent / "ACTIVE_GOAL_STATE.md"
    before = (registry.read_bytes(), state.read_bytes())
    status, review = get(server)
    assert status == 200
    assert set(review) == {"ok", "goal_id", "observed_at", "graph", "goal_map", "acceptance"}
    goal_map = review["goal_map"]
    titles = {node["refs"]["todo_ids"][0]: node for node in goal_map["nodes"]}
    # The whole-Goal map is not limited to the bounded chain's two expanded gates.
    assert set(titles) == {"todo_integrate", "todo_design", "todo_review", "todo_support", "todo_publish"}
    assert [titles[t]["kind"] for t in ("todo_review", "todo_integrate")] == ["gate", "deliverable"]
    assert titles["todo_design"]["state"] == "done" and titles["todo_design"]["depth"] == 0
    assert titles["todo_integrate"]["depth"] == 1
    node_todo = {node["node_id"]: tid for tid, node in titles.items()}
    assert {(node_todo[e["from_node_id"]], node_todo[e["to_node_id"]], e["relation"]) for e in goal_map["edges"]} == {
        ("todo_integrate", "todo_design", "continues"), ("todo_integrate", "todo_design", "depends_on")}
    assert goal_map["limits"]["topology_complete"] is True
    canonical = collect_status(registry_path=registry, runtime_root_override=str(runtime), scan_roots=[], limit=20,
                               goal_id="release-demo", include_public_boundary_scan=False, include_task_graph=True)
    item = canonical["attention_queue"]["items"][0]
    assert review["graph"] == item["task_graph_projection"]
    assert review["acceptance"] == canonical["run_history"]["goals"][0]["acceptance_observation"]
    assert review["acceptance"]["acceptance_assessed"] is False
    assert review["graph"]["limits"]["user_gate_truncated_count"] == 1
    assert any(node.get("owner_agent") == "builder" for node in review["graph"]["nodes"])
    assert any(edge["relation"] == "continues" for edge in review["graph"]["edges"])
    assert any("todo_design" in node["refs"].get("todo_ids", []) for node in review["graph"]["nodes"])
    validate_public_safe_value(review)
    assert (registry.read_bytes(), state.read_bytes()) == before


def test_review_is_cold_and_missing_observations_stay_unknown(workspace, monkeypatch):
    server, _, _ = workspace
    calls = []
    def collect(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "attention_queue": {"items": []}, "run_history": {"goals": []}}
    monkeypatch.setattr("loopx.chat_status_api.collect_status", collect)
    assert get(server, "/status.json?goal_id=release-demo")[0] == 200
    assert calls[-1]["include_task_graph"] is False
    _, result = get(server)
    assert calls[-1]["include_task_graph"] is True
    assert result["graph"] is None and result["acceptance"] is None
    assert result["goal_map"]["schema_version"] == "goal_task_map_v0"


def test_unreadable_todo_source_keeps_review_and_marks_map_unavailable(workspace):
    server, registry, _ = workspace
    (registry.parent / "ACTIVE_GOAL_STATE.md").unlink()
    status, result = get(server)
    assert status == 200
    assert result["goal_map"] is None


@pytest.mark.parametrize("query, expected", [("", 400), ("?goal_id=", 400), ("?goal_id=missing", 404),
    ("?goal_id=release-demo&goal_id=release-demo", 400), ("?goal_id=release-demo&view=workspace-directory", 400)])
def test_review_rejects_ambiguous_or_absent_scope(workspace, query, expected):
    assert get(workspace[0], "/api/chat/delivery-review" + query)[0] == expected


def test_review_respects_origin_selected_goal_and_registry_drift(workspace, monkeypatch):
    server, registry, _ = workspace
    assert get(server, origin="https://example.com")[0] == 403
    server.selected_goal_id = "other-goal"
    assert get(server)[0] == 400
    server.selected_goal_id = "release-demo"
    assert get(server, "/api/chat/delivery-review")[0] == 200
    def collect(**kwargs):
        registry.write_text(json.dumps({"goals": []}))
        return {"ok": True}
    monkeypatch.setattr("loopx.chat_status_api.collect_status", collect)
    assert get(server)[0] == 409


def test_review_failure_does_not_expose_local_diagnostics(workspace, monkeypatch):
    def fail(**kwargs):
        raise OSError("private diagnostic and local filesystem details")
    monkeypatch.setattr("loopx.chat_status_api.collect_status", fail)
    status, payload = get(workspace[0])
    assert status == 500
    assert "private diagnostic" not in json.dumps(payload)


@pytest.mark.parametrize("projection", [
    {"ok": False},
    {"ok": True, "attention_queue": {"items": [{"goal_id": "release-demo", "task_graph_projection": {"raw_log": "must not export"}}]}},
])
def test_failed_or_unsafe_projection_cannot_be_exported(workspace, monkeypatch, projection):
    monkeypatch.setattr("loopx.chat_status_api.collect_status", lambda **kwargs: projection)
    status, result = get(workspace[0])
    assert status in {500, 503}
    assert result["ok"] is False
    assert "must not export" not in json.dumps(result)
