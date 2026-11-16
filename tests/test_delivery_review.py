"""Real source/HTTP acceptance for the on-demand workspace review."""
from __future__ import annotations

import http.client
import json
import threading
from hashlib import sha256
from pathlib import Path

import pytest

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.goals.acceptance import inspect_goal_acceptance, refine_goal_outcome
from loopx.control_plane.runtime.public_safety import validate_public_safe_value
from loopx.control_plane.todos.active_state_todo_parser import parse_todo_source
from loopx.control_plane.todos.list_readback import list_goal_todos
from loopx.control_plane.todos.todo_summary import canonical_todo_read_record, structured_todo_item
from loopx.state_refresh import refresh_state_run
from loopx.status import collect_status
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)


def make_project(root: Path, *, provider: str = "file") -> tuple[Path, Path]:
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
        "  <!-- loopx:todo todo_id=todo_support status=open task_class=user_gate goal_bound=true global_gate=true -->\n"
        "- [ ] Approve the publication boundary\n"
        "  <!-- loopx:todo todo_id=todo_publish status=open task_class=user_gate goal_bound=true global_gate=true -->\n"
    )
    registry = project / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": "release-demo", "display_name": "Release collaboration", "status": "active", "domain": "software",
        "repo": str(project), "state_file": state.name,
        "adapter": {"kind": "harness_self_improvement", "status": "connected-read-only"},
    }]}))
    groups, _, _ = parse_todo_source(state.read_text())
    records = [canonical_todo_read_record(structured_todo_item(
        item, role=role, source_section=item["source_section"],
    )) for role, rows in groups.items() for item in rows]
    initialize_canonical_authority(runtime, "release-demo", build_todo_runtime_shadow_projection(
        goal_id="release-demo", todos=records,
    ), state_path=state, provider=provider)
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


def test_http_review_exposes_source_freshness_and_map_coverage(workspace):
    server, _, _ = workspace
    status, review = get(server)
    assert status == 200
    envelope = review["projection_envelope"]
    assert envelope["projection"] == "delivery_review"
    assert envelope["fresh"]
    task_map = review["goal_map"]["projection_envelope"]
    assert task_map["projection"] == "goal_task_map"
    assert task_map["fresh"] and task_map["complete"]
    assert task_map["coverage"]["expected_count"] == 5
    assert task_map["coverage"]["included_count"] == 5
    assert task_map["sources"][0]["last_read_at"] <= task_map["observed_at"]


def test_http_review_discloses_unreadable_map_without_hiding_other_components(workspace, monkeypatch):
    server, _, _ = workspace

    def unreadable(**_kwargs):
        raise PermissionError("synthetic unavailable source")

    monkeypatch.setattr("loopx.chat.status_api.list_goal_todos", unreadable)
    status, review = get(server)
    assert status == 200 and review["goal_map"] is None and review["graph"] is not None
    envelope = review["projection_envelope"]
    assert not envelope["fresh"] and not envelope["complete"]
    assert "unreadable_sources" in envelope["alert_reasons"]
    assert envelope["coverage"]["omitted"] == [{"reason": "unavailable_components", "count": 1, "refs": []}]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_http_review_keeps_status_evidence_when_the_second_authority_read_fails(
    workspace, tmp_path, monkeypatch, provider,
):
    server, _, _ = workspace
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime = make_project(tmp_path / "canonical", provider=provider)
    server.registry_path = registry
    server.runtime_root = runtime
    server.runtime_root_override = str(runtime)
    state = registry.parent / "ACTIVE_GOAL_STATE.md"
    source_before = (registry.read_bytes(), state.read_bytes())
    head_before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="release-demo")
    status, before = get(server)
    assert status == 200 and before["graph"] and before["goal_map"] and before["acceptance"]

    marker = runtime / "authority" / f"provider-{sha256(b'release-demo').hexdigest()}.json"
    marker_before = marker.read_bytes() if marker.exists() else None

    def read_unavailable_map(**options):
        # The status snapshot is already collected. Now fail only the map's
        # independent provider read, using the real authority selector.
        marker.write_text('{"private_fixture":"invalid selector"}', encoding="utf-8")
        try:
            return list_goal_todos(**options)
        finally:
            if marker_before is None:
                marker.unlink()
            else:
                marker.write_bytes(marker_before)

    with monkeypatch.context() as patch:
        patch.setattr("loopx.chat.status_api.list_goal_todos", read_unavailable_map)
        status, partial = get(server)
    assert status == 200, partial
    assert partial["goal_id"] == "release-demo"
    assert partial["graph"] == before["graph"]
    assert partial["acceptance"] == before["acceptance"]
    assert partial["goal_map"] is None
    envelope = partial["projection_envelope"]
    assert not envelope["fresh"] and not envelope["complete"]
    assert "unreadable_sources" in envelope["alert_reasons"]
    assert envelope["coverage"]["included_count"] == 2
    assert any(source["source_id"] == "goal_todos" and source["read_status"] == "unreadable"
               for source in envelope["sources"])
    validate_public_safe_value(partial)
    assert "private_fixture" not in json.dumps(partial)

    status, restored = get(server)
    assert status == 200 and restored["goal_map"]["nodes"] == before["goal_map"]["nodes"]
    assert restored["goal_map"]["projection_envelope"]["fresh"]
    head_after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="release-demo")
    assert (head_after["provider_revision"], head_after["todos"]) == (
        head_before["provider_revision"], head_before["todos"],
    )
    assert (registry.read_bytes(), state.read_bytes()) == source_before


def test_http_review_inherits_source_age_and_replay_keeps_observation(workspace, monkeypatch):
    from datetime import datetime, timedelta

    from loopx.control_plane.status import collection
    from loopx.control_plane.projection_envelope_facts import serve_projection_envelope

    server, _, _ = workspace
    monkeypatch.setattr(collection, "now_utc_iso", lambda: "2026-10-01T00:00:00Z")
    status, review = get(server)
    assert status == 200
    envelope = review["projection_envelope"]
    assert not envelope["fresh"] and "stale_sources" in envelope["alert_reasons"]
    assert review["goal_map"]["projection_envelope"]["fresh"]
    later = datetime.fromisoformat(envelope["served_at"].replace("Z", "+00:00")) + timedelta(minutes=6)
    replay = serve_projection_envelope(envelope, served_at=later.isoformat())
    assert replay["observed_at"] == envelope["observed_at"]
    assert replay["served_from_cache"] and not replay["fresh"]


def test_real_http_review_matches_cli_sources_without_canonical_writes(workspace):
    server, registry, runtime = workspace
    state = registry.parent / "ACTIVE_GOAL_STATE.md"
    before = (registry.read_bytes(), state.read_bytes())
    status, review = get(server)
    assert status == 200
    assert set(review) == {"ok", "goal_id", "observed_at", "graph", "goal_map", "acceptance", "projection_envelope"}
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
    canonical = collect_status(registry_path=registry, runtime_root_override=str(runtime), limit=20,
                               goal_id="release-demo", include_task_graph=True)
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("display", ["current", "missing"])
def test_canonical_http_goal_map_survives_repeated_reads_and_runtime_restart(
    workspace, tmp_path, monkeypatch, provider, display,
):
    server, _, _ = workspace
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime = make_project(tmp_path / "canonical", provider=provider)
    server.registry_path, server.runtime_root = registry, runtime
    server.runtime_root_override = str(runtime)
    state = registry.parent / "ACTIVE_GOAL_STATE.md"
    registry_before, source = registry.read_bytes(), state.read_bytes()
    # This independently specified map covers every source Todo, including the
    # third gate omitted from the bounded attention graph.
    expected_nodes = [
        {"node_id": node_id, "kind": kind, "title": title, "state": status,
         "depth": depth, "refs": {"todo_ids": [todo_id]},
         **({"owner_agent": owner} if owner else {})}
        for todo_id, node_id, kind, title, status, depth, owner in [
            ("todo_review", "node_gate_todo_review_32e2a5", "gate",
             "Review the release candidate", "open", 0, None),
            ("todo_support", "node_gate_todo_support_0bc755", "gate",
             "Confirm support coverage", "open", 0, None),
            ("todo_publish", "node_gate_todo_publish_6b56a7", "gate",
             "Approve the publication boundary", "open", 0, None),
            ("todo_integrate", "node_todo_todo_integrate_c547fe", "deliverable",
             "Integrate the verified release package", "open", 1, "builder"),
            ("todo_design", "node_todo_todo_design_2ab147", "deliverable",
             "Specify the release acceptance criteria", "done", 0, "reviewer"),
        ]
    ]
    expected = {
        "schema_version": "goal_task_map_v0", "mode": "read_only", "goal_id": "release-demo",
        "limits": {"node_limit": 120, "emitted_node_count": 5, "omitted_node_count": 0,
                   "source_truncated": False, "missing_endpoint_count": 0,
                   "cycle_edge_count": 0, "topology_complete": True},
        "nodes": sorted(expected_nodes, key=lambda node: node["node_id"]),
        "edges": [
            {"edge_id": "edge_successor_node_todo_todo_integrate_c547fe_node_todo_to_87b417_87b417",
             "from_node_id": "node_todo_todo_integrate_c547fe", "to_node_id": "node_todo_todo_design_2ab147",
             "relation": "continues", "enforcement": "lineage_only",
             "reason": "Work continues predecessor lineage; this is not a completion prerequisite."},
            {"edge_id": "edge_unblocks_node_todo_todo_integrate_c547fe_node_todo_to_87b417_87b417",
             "from_node_id": "node_todo_todo_integrate_c547fe", "to_node_id": "node_todo_todo_design_2ab147",
             "relation": "depends_on", "enforcement": "typed_lifecycle",
             "reason": "Work has a typed lifecycle dependency on the linked unblocking Todo."},
        ],
    }
    try:
        before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="release-demo")
        assert before is not None
        assert before["source_authority"] == f"{provider}_v0"
        assert before["provider_revision"]
        assert len(before["todos"]) == 5
        if display == "missing":
            state.unlink()
        previous_map = None
        for restart in (False, False, True):
            if restart:
                assert restart_effect_runtime(wait_for_exit=True)["status"] == "stopped"
            status, review = get(server)
            assert status == 200
            goal_map = review["goal_map"]
            assert goal_map is not None
            envelope = goal_map.pop("projection_envelope")
            assert envelope["fresh"] and envelope["complete"]
            assert {**goal_map,
                    "nodes": sorted(goal_map["nodes"], key=lambda node: node["node_id"]),
                    "edges": sorted(goal_map["edges"], key=lambda edge: edge["edge_id"])} == expected
            if previous_map is not None:
                assert goal_map == previous_map
            previous_map = goal_map
            validate_public_safe_value(review)
            after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="release-demo")
            assert after is not None
            assert (after["provider_revision"], after["todos"]) == (before["provider_revision"], before["todos"])
            assert registry.read_bytes() == registry_before
            assert (state.read_bytes() if state.exists() else None) == (source if display == "current" else None)
    finally:
        restart_effect_runtime(wait_for_exit=True)


def test_review_is_cold_and_missing_observations_stay_unknown(workspace, monkeypatch):
    server, _, _ = workspace
    calls = []
    def collect(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "attention_queue": {"items": []}, "run_history": {"goals": []}}
    monkeypatch.setattr("loopx.chat.status_api.collect_status", collect)
    assert get(server, "/status.json?goal_id=release-demo")[0] == 200
    assert calls[-1]["include_task_graph"] is False
    _, result = get(server)
    assert calls[-1]["include_task_graph"] is True
    assert result["graph"] is None and result["acceptance"] is None
    assert result["goal_map"]["schema_version"] == "goal_task_map_v0"

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
    monkeypatch.setattr("loopx.chat.status_api.collect_status", collect)
    assert get(server)[0] == 409


def test_review_failure_does_not_expose_local_diagnostics(workspace, monkeypatch):
    def fail(**kwargs):
        raise OSError("private diagnostic and local filesystem details")
    monkeypatch.setattr("loopx.chat.status_api.collect_status", fail)
    status, payload = get(workspace[0])
    assert status == 500
    assert "private diagnostic" not in json.dumps(payload)


@pytest.mark.parametrize("projection", [
    {"ok": False},
    {"ok": True, "attention_queue": {"items": [{"goal_id": "release-demo", "task_graph_projection": {"raw_log": "must not export"}}]}},
])
def test_failed_or_unsafe_projection_cannot_be_exported(workspace, monkeypatch, projection):
    monkeypatch.setattr("loopx.chat.status_api.collect_status", lambda **kwargs: projection)
    status, result = get(workspace[0])
    assert status in {500, 503}
    assert result["ok"] is False
    assert "must not export" not in json.dumps(result)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_review_redacts_local_evidence_without_changing_original_acceptance(
    workspace, tmp_path, monkeypatch, provider,
):
    server, _, _ = workspace
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime = make_project(tmp_path / "canonical", provider=provider)
    state = registry.parent / "ACTIVE_GOAL_STATE.md"
    configuration = json.loads(registry.read_text())
    configuration["goals"][0]["coordination"] = {"registered_agents": ["agent-a", "builder", "reviewer"]}
    registry.write_text(json.dumps(configuration))
    evidence = runtime / "reports" / "verification.txt"
    evidence.parent.mkdir()
    evidence.write_text("Independent verification is still pending.")
    basis = dict(registry_path=registry, goal_id="release-demo", runtime_root=str(runtime))
    refine_goal_outcome(**basis, agent_id="agent-a", execute=True,
        expected_provider_revision=inspect_goal_acceptance(**basis)["provider_revision"],
        operation_id="local-evidence-review", refinement={
            "outcome_id": "release", "owner_agent_id": "agent-a",
            "objective": "Deliver the verified release",
            "source": {"ref": "message:release", "summary": "The owner requested independent verification"},
            "requirements": [{"id": "verification", "description": "The release is independently verified"}],
            "review": [{"id": "verification", "conclusion": "uncertain",
                        "evidence_refs": [str(evidence)], "note": "The verification remains incomplete"}],
        })
    before = inspect_goal_acceptance(**basis)
    source_before = registry.read_bytes(), state.read_bytes(), evidence.read_bytes()
    store = ChatSessionStore(tmp_path / "private-chat")
    session = store.create_session(goal_id="release-demo", agent_id="agent-a",
        adapter_kind="codex_app_server", upstream_thread_id="original", channel_id="workspace")
    store.append_message(session["session_id"], role="agent", text="Retain the original conversation.")
    chat_before = {path: path.read_bytes() for path in store.root.rglob("*.json")}
    controller = ChatRuntimeController(store=store, registry_path=registry, codex_bin="must-not-run")
    server.registry_path = registry
    server.runtime_root = store.root.parent
    server.runtime_root_override = str(store.root.parent)
    server.runtime_controller = controller
    try:
        for restart in (False, True):
            if restart:
                restart_effect_runtime(wait_for_exit=True)
            status, review = get(server)
            assert status == 200, review
            assert review["goal_id"] == "release-demo"
            requirement = review["acceptance"]["goal_acceptance_contract"]["outcome_requirements"]["outcomes"][0]["requirements"][0]
            assert requirement["id"] == "verification"
            assert requirement["agent_review"]["evidence_refs"] == ["[local-path]"]
            assert requirement["agent_review"]["conclusion"] == "uncertain"
            assert requirement["agent_review"]["independently_verified"] is False
            validate_public_safe_value(review)
            assert str(tmp_path) not in json.dumps(review)
            assert inspect_goal_acceptance(**basis) == before
            assert (registry.read_bytes(), state.read_bytes(), evidence.read_bytes()) == source_before
            assert {path: path.read_bytes() for path in chat_before} == chat_before
    finally:
        controller.close()


@pytest.mark.parametrize("unsafe", [
    {"api_key": "synthetic private field"},
    {"note": "sk-" + "a" * 48},
    {"raw_log": "synthetic private diagnostic"},
    {"requestBody": {"message": "synthetic private payload"}},
])
def test_review_still_refuses_unsafe_acceptance_and_recovers(workspace, monkeypatch, unsafe):
    server, _, _ = workspace
    projection = {"ok": True, "run_history": {"goals": [
        {"id": "release-demo", "acceptance_observation": unsafe},
    ]}}
    monkeypatch.setattr("loopx.chat.status_api.collect_status", lambda **kwargs: projection)
    status, result = get(server)
    assert status == 500 and result["ok"] is False
    assert "synthetic private" not in json.dumps(result)
    assert "sk-" not in json.dumps(result)
    projection["run_history"]["goals"][0]["acceptance_observation"] = {"acceptance_assessed": False}
    status, result = get(server)
    assert status == 200
    assert result["acceptance"]["acceptance_assessed"] is False
    validate_public_safe_value(result)
