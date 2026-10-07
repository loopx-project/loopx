"""Local notice intake is independent of optional transport and model prose."""
import hashlib
import json

from loopx.chat_manager_context import manager_turn_context
from loopx.capabilities.manager_context.inspection import manager_index
import loopx.goal_portfolio as portfolio


def test_steward_turn_receives_blocker_and_decision_without_channel(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "alpha", "coordination": {
        "registered_agents": ["worker"]}}]}))
    gate = {"todo_id": "todo_gate", "role": "user", "task_class": "user_gate",
            "status": "open", "text": "Choose the public release window",
            "note": "Only the reviewed build", "updated_at": "2026-10-02T00:00:00Z"}
    blocker = {"todo_id": "todo_work", "role": "agent", "task_class": "advancement_task",
               "status": "blocked", "text": "Verify the primary result",
               "reason": "Required input has not arrived"}
    monkeypatch.setattr("loopx.todos.list_goal_todos", lambda **_: {
        "ok": True, "todos": [gate, blocker], "source": "canonical_fixture",
    })
    monkeypatch.setattr(portfolio, "collect_status", lambda **_: {
        "ok": True, "run_history": {"goals": [{"id": "alpha"}]},
        "attention_queue": {"items": [{"goal_id": "alpha",
            "agent_todos": {"items": [blocker]}, "user_todos": {"items": [gate]}}]},
    })
    monkeypatch.setattr(portfolio, "build_quota_should_run", lambda *a, **k: {
        "ok": True, "agent_identity": {"agent_id": "worker"},
        "blocked_priority_fallback": {"selected_executable": {
            "todo_id": "todo_fallback", "text": "Prepare independent documentation"}},
    })
    context = manager_turn_context(registry, {"channel_id": "manager"}, tmp_path,
                                   include_details=False)
    attention = context["goals"][0]["attention"]
    assert attention["status"] == "read"
    decision, work = attention["items"]
    assert work["blocker"]["cause"] == blocker["reason"]
    assert work["owner_must_act"] is False
    assert "independent documentation" in work["blocker"]["impact"]
    assert decision["request"]["text"] == gate["text"]
    assert decision["owner_must_act"] is True
    preview = manager_index(context)["goals"][0]["attention"]
    assert preview["details_omitted"] is True
    assert preview["items"][0]["request"]["text"] == gate["text"]
    assert preview["items"][1]["blocker"]["cause"] == blocker["reason"]
    assert preview["coverage"] == attention["coverage"]
    assert "impact" not in preview["items"][1]["blocker"]
    assert "impact" in attention["items"][1]["blocker"]
    assert not list(tmp_path.rglob("goal-channel*.json"))


def test_missing_source_is_unknown_and_external_scope_is_not_expanded(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "allowed"}]}))
    revision = "sha256:" + hashlib.sha256(registry.read_bytes()).hexdigest()
    selected = []
    def read(**kwargs):
        selected.append(kwargs["goal_ids"])
        return {"goals": [{"goal_id": "allowed", "agents": []}], "coverage": {},
                "inventory_revision": revision}
    monkeypatch.setattr(portfolio, "build_goal_portfolio", read)
    monkeypatch.setattr("loopx.chat_manager_context.build_goal_portfolio", read)
    context = manager_turn_context(registry, {
        "channel_id": "manager.external.fixture"}, tmp_path,
        authorized_goal_ids=["allowed"], include_details=False)
    assert selected == [["allowed"]]
    assert context["goals"][0]["attention"]["status"] == "unavailable"
    registry.unlink()
    changed = manager_turn_context(registry, {"channel_id": "manager.external.fixture"},
                                   tmp_path, authorized_goal_ids=["allowed"], include_details=False)
    assert changed["goals"] == []
    assert "external_authorization_changed" in changed["warnings"]


def test_multi_goal_turn_keeps_coverage_without_repeating_every_request(tmp_path, monkeypatch):
    goals = [{"goal_id": f"goal_{i}", "attention": {"status": "read",
        "items": [{"todo_id": f"todo_{i}_{j}", "owner_must_act": i == 127,
                   "request": {"text": "Choose the reviewed release window"}}
                  for j in range(8)],
        "coverage": {"known": 8, "included": 8, "omitted": 0}}} for i in range(128)]
    monkeypatch.setattr("loopx.chat_manager_context.build_goal_portfolio",
                        lambda **_: {"goals": goals})
    context = manager_turn_context(tmp_path / "registry", {"channel_id": "manager"},
                                   tmp_path, include_details=False)
    index = manager_index(context)
    assert sum(len(g["attention"]["items"]) for g in index["goals"]) == 12
    assert index["goals"][-1]["attention"]["coverage"]["included"] == 8
    assert index["goals"][1]["attention"]["coverage"] == {
        "known": 8, "included": 0, "omitted": 8}
