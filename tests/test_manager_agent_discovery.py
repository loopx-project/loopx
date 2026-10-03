"""Discovery searches the allowed inventory; a delivery list is not that scope."""

import json
import subprocess
import sys

import pytest

from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME


def setup(tmp_path, *, owner=True, scope=None, valid=lambda: True):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [
        {"id": "research", "coordination": {
            "registered_agents": [f"worker-{i:02}" for i in range(35)],
            "agent_profiles": {"worker-34": {"profile_role": "reviewer", "scope_summary": "Independent PR review"}},
        }},
        {"id": "history", "activation_state": "stopped", "registered_agents": ["old-worker"]},
        {"id": "private", "registered_agents": ["hidden-worker"]},
    ]}))
    records = []
    inspector = ManagerInspection(
        context={"scope": "owner_global" if owner else "external_goal_scope", "goals": []},
        registry_path=registry, runtime_root=tmp_path, owner_scope=owner,
        scope_valid=valid, record=records.append,
        discovery_scope=scope or (lambda: None if owner else ["research"]),
        delegation_authority=lambda: {"mode": "context_only", "targets": [{"goal_id": "research", "agent_id": "worker-00"}]},
    )
    return registry, inspector, records


def test_full_inventory_search_is_independent_of_initial_portfolio_and_delivery(tmp_path):
    _, reader, records = setup(tmp_path)
    result = reader.read(TOOL_NAME, {"view": "agents", "query": "PR REVIEW"})
    assert result["ok"] and result["matched"] == 1
    row = result["rows"][0]
    assert row["agent_id"] == "worker-34"  # beyond both historical 8/24 row caps
    assert row["context_delivery"] == "not_granted"
    assert row["execution_readiness"] == "not_checked"
    assert len(records) == 1
    offset, found, revisions = 0, [], set()
    while offset is not None:
        page = reader.read(TOOL_NAME, {"view": "agents", "goal_id": "research", "offset": offset})
        found.extend(r["agent_id"] for r in page["rows"])
        revisions.add(page["source"]["source_revision"])
        offset = page["next_offset"]
    assert found == [f"worker-{i:02}" for i in range(35)]
    assert len(revisions) == 1


def test_audience_scope_is_distinct_from_sender_delivery_and_cannot_leak(tmp_path):
    _, reader, _ = setup(tmp_path, owner=False)
    all_rows = reader.read(TOOL_NAME, {"view": "agents"})
    assert all_rows["matched"] == 35
    assert all_rows["rows"][0]["context_delivery"] == "allowed"
    assert reader.read(TOOL_NAME, {"view": "agents", "query": "hidden"})["matched"] == 0
    assert reader.read(TOOL_NAME, {"view": "agents", "goal_id": "private"})["error"] == "goal_outside_available_scope"


def test_stopped_is_historical_not_a_delivery_target(tmp_path):
    _, reader, _ = setup(tmp_path)
    assert reader.read(TOOL_NAME, {"view": "agents", "query": "old-worker"})["matched"] == 0
    result = reader.read(TOOL_NAME, {"view": "agents", "query": "old-worker", "include_stopped": True})
    assert result["rows"][0]["context_delivery"] == "goal_stopped"


def test_no_match_does_not_hide_unreadable_inventory(tmp_path):
    registry, reader, _ = setup(tmp_path)
    registry.write_text("{")
    result = reader.read(TOOL_NAME, {"view": "agents"})
    assert not result["ok"] and result["unknown"] and result["matched"] is None
    assert result["error"] == "agent_inventory_unavailable"


def test_revocation_during_discovery_discards_evidence(tmp_path):
    checks = iter([True, False])
    _, reader, records = setup(tmp_path, valid=lambda: next(checks))
    assert reader.read(TOOL_NAME, {"view": "agents"}) == {"ok": False, "error": "authorization_changed"}
    assert not records


def test_null_external_scope_never_becomes_owner_scope(tmp_path):
    _, reader, records = setup(tmp_path, owner=False, scope=lambda: None)
    assert reader.read(TOOL_NAME, {"view": "agents"})["error"] == "authorization_changed"
    assert not records


def test_goal_chat_cannot_inherit_owner_global_discovery(tmp_path):
    _, reader, _ = setup(tmp_path, scope=lambda: ["research"])
    reader.context["scope"] = "owner_goal"
    assert reader.read(TOOL_NAME, {"view": "agents", "query": "hidden"})["matched"] == 0


@pytest.mark.parametrize("arguments", [
    {"view": "portfolio", "query": "review"},
    {"view": "agents", "query": False},
    {"view": "agents", "query": "x" * 201},
])
def test_invalid_search_never_reads_source(tmp_path, arguments):
    _, reader, records = setup(tmp_path)
    assert reader.read(TOOL_NAME, arguments)["error"] == "invalid_arguments"
    assert not records


def test_real_cli_export_reaches_owner_beyond_old_caps_without_a_live_provider(tmp_path):
    registry, _, _ = setup(tmp_path)
    result = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(tmp_path / "runtime"), "--format", "json",
        "goal-portfolio", "--manager-view", "agents", "--query", "PR review",
        "--goal-id", "research",
    ], capture_output=True, text=True, check=True)
    page = json.loads(result.stdout)
    assert page["schema_version"] == "manager_evidence_page_v1"
    assert [r["agent_id"] for r in page["rows"]] == ["worker-34"]
    assert page["rows"][0]["context_delivery"] == "not_checked"
    assert not (tmp_path / "runtime" / "chat").exists()


@pytest.mark.parametrize("tool", [TOOL_NAME, "loopx_context_read"])
def test_conversation_resolves_existing_peer_from_real_read_only_host_store(tmp_path, monkeypatch, tool):
    import sqlite3

    registry, reader, records = setup(tmp_path, owner=False)
    content = json.loads(registry.read_text())
    content["goals"][0]["coordination"]["thread_agent_bindings"] = [
        {"agent_id": "worker-34", "host_surface": "codex-app", "thread_id": name}
        for name in ("old", "current")
    ]
    registry.write_text(json.dumps(content))
    home = tmp_path / "host"
    home.mkdir()
    rollout = home / "current.jsonl"
    rollout.write_text(json.dumps({"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
        "payload": {"type": "task_complete"}}) + "\n")
    db = home / "state_5.sqlite"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, archived INTEGER)")
        connection.executemany("INSERT INTO threads VALUES(?,?,?)", [("old", "", 1), ("current", str(rollout), 0)])
    monkeypatch.setenv("LOOPX_CODEX_HOMES", str(home))
    before = [p.read_bytes() for p in (registry, db, rollout)]
    result = reader.read(tool, {"view": "agent_route", "goal_id": "research", "agent_id": "worker-34"})
    assert result["status"] == "resolved"
    assert result["selected_route"] == {"host_surface": "codex-app", "thread_id": "current"}
    assert result["host_observation"]["state"] == "idle"
    assert result["host_delivery"] == "not_attempted" and result["authority"] == "locator_only"
    assert records == [result]
    assert before == [p.read_bytes() for p in (registry, db, rollout)]
    assert not (tmp_path / "manager-context").exists()
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE threads SET archived=0 WHERE id='old'")
    ambiguous = reader.read(tool, {"view": "agent_route", "goal_id": "research", "agent_id": "worker-34"})
    # Missing rollout evidence is unknown, not permission to discard the old binding.
    assert ambiguous["status"] == "ambiguous" and ambiguous["selected_route"] is None
    exact = reader.read(tool, {"view": "agent_route", "goal_id": "research", "agent_id": "worker-34",
                               "thread_link": "codex://threads/current"})
    assert exact["status"] == "resolved" and exact["selected_route"]["thread_id"] == "current"
    assert exact["host_delivery"] == "not_attempted"


def test_route_read_obeys_live_audience_scope_before_observing_host(tmp_path, monkeypatch):
    _, reader, records = setup(tmp_path, owner=False)
    def forbidden(*args, **kwargs):
        pytest.fail("out-of-scope host must not be observed")
    monkeypatch.setattr("loopx.control_plane.collaboration.peer_host_route.resolve_peer_host_route", forbidden)
    result = reader.read(TOOL_NAME, {"view": "agent_route", "goal_id": "private", "agent_id": "hidden-worker"})
    assert result["error"] == "goal_outside_available_scope"
    assert not records


def test_route_read_discards_evidence_after_scope_revocation(tmp_path):
    checks = iter([True, False])
    _, reader, records = setup(tmp_path, owner=False, valid=lambda: next(checks))
    assert reader.read(TOOL_NAME, {"view": "agent_route", "goal_id": "research", "agent_id": "worker-34"}) == {
        "ok": False, "error": "authorization_changed"}
    assert not records


@pytest.mark.parametrize("arguments", [
    {"view": "agent_route", "agent_id": "worker-34"},
    {"view": "agent_route", "goal_id": "research"},
    {"view": "agents", "agent_id": "worker-34"},
    {"view": "agents", "thread_link": "codex://threads/current"},
])
def test_invalid_route_read_is_repairable_and_effect_free(tmp_path, arguments):
    _, reader, records = setup(tmp_path)
    result = reader.read(TOOL_NAME, arguments)
    assert result["error"] == "invalid_arguments" and result["rejected_arguments"]
    assert not records


def test_remote_route_read_does_not_silently_inspect_local_host(tmp_path):
    _, reader, records = setup(tmp_path)
    result = reader.read(TOOL_NAME, {"view": "agent_route", "source_id": "ssh:remote",
        "goal_id": "research", "agent_id": "worker-34"})
    assert result["error"] == "remote_agent_route_not_supported"
    assert not records
