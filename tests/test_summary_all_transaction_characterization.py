"""Frozen migration evidence; remove when the Python CLI facade is retired."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import pytest

import loopx.summary_all as manager
import loopx.control_plane.projection_envelope_facts as envelopes
import loopx.control_plane.todos.decision_scope as scopes

FIXTURE = Path(__file__).parent / "fixtures/summary_all/transactions.json"
NOW = "2026-11-02T12:00:00+00:00"


def scenarios():
    scope = {"kind": "approval", "granularity": "action", "scope_key": "synthetic.publish"}
    quotas = {}
    items = []
    for index, state in enumerate(("eligible", "operator_gate", "waiting", "paused", "run", "normal_run")):
        goal = f"goal-{index}"
        todo = {"todo_id": f"work-{index}", "text": "Synthetic work", "status": "open",
                "required_decision_scopes": [scope]}
        quota = {"ok": True, "goal_id": goal, "state": state,
                 "agent_identity": {"agent_id": "agent-a" if index != 2 else "agent-b"},
                 "recommended_action": "Read /loopx-summary-all " + "/" + "synthetic/example.json then review synthetic work.",
                 "agent_lane_next_action": todo, "selected_todo": todo,
                 "rollout_event": {"event_id": f"event-{index}"}}
        if index in (1, 2):
            gate = {"todo_id": f"gate-{index}", "text": "Approve synthetic work?",
                    "task_class": "user_gate", "decision_scope": scope}
            if index == 1:
                gate["unblocks_todo_id"] = "exact-target"
            quota["user_todo_summary"] = {"open_count": 1, "gate_open_items": [gate], "items": [gate]}
        quotas[goal] = quota
        items.append({"goal_id": goal, "waiting_on": "controller" if index in (3, 4) else "user",
                      "operator_question": "Which synthetic route?", "recommended_action": "Wait safely."})
    quotas["unavailable"] = None
    items = [items[0], items[0], {}, {"goal_id": "unavailable"}, *items[1:]]
    status = {"ok": True, "attention_queue": {"items": items},
              "global_registry": {"available": True, "global_goal_count": 9,
                                  "current_registry_excluded_goal_count": 2,
                                  "current_registry_excluded_goal_ids": ["outside-a", "outside-b"],
                                  "findings": [{"kind": "synthetic_warning", "message": "Review synthetic warning."}]},
              "run_history": {"recent_runs": [
                  {"goal_id": "old", "generated_at": "2026-10-01T00:00:00Z", "classification": "old"},
                  {"goal_id": "goal-0", "generated_at": NOW, "classification": "progress", "recommended_action": "Continue."},
                  {"goal_id": "unknown-time", "generated_at": "invalid", "classification": "visible"}]}}
    cases = [dict(name="empty", status={"ok": True}, quotas={}, limit=8, time_range="24h", agent_id=None),
             dict(name="unhealthy", status={"ok": False}, quotas={}, limit=1, time_range="bad", agent_id=None)]
    for name, limit, period, agent in (("mixed", 8, "24h", None), ("early-stop", 2, "2d", None),
                                       ("agent", 8, "0h", "agent-a"), ("zero", 0, "bad", None),
                                       ("negative", -1, "48h", None)):
        cases.append(dict(name=name, status=status, quotas=quotas, limit=limit, time_range=period, agent_id=agent))
    return cases


def relation_scenarios():
    case = copy.deepcopy(scenarios()[2])
    case["name"] = "scope-and-envelope"
    case["fixture"] = "relations.json"
    case["quotas"]["goal-2"]["user_todo_summary"]["gate_open_items"][0]["decision_scope"] = {
        "kind": "production", "granularity": "project", "scope_key": "deploy"}
    case["quotas"]["goal-2"]["agent_lane_next_action"]["required_decision_scopes"] = [
        {"kind": "production", "granularity": "action", "scope_key": "deploy"}]
    case["quotas"]["goal-2"]["agent_lane_next_action"]["todo_id"] = "todo_synthetic_work"
    case["quotas"]["goal-2"]["selected_todo"] = case["quotas"]["goal-2"]["agent_lane_next_action"]
    case["status"]["projection_envelope"] = envelopes.seal_projection_envelope(
        projection="status", observed_at=NOW,
        sources=[envelopes.source_fact("global_registry", last_read_at="2026-10-01T00:00:00+00:00"),
                 envelopes.source_fact("synthetic_unreadable", read_status="unreadable")],
        coverage={"scope": "global", "expected_count": 9, "included_count": 7})
    return [case]


def observe(case, operation, monkeypatch, tmp_path):
    reads, crossings = [], []
    monkeypatch.setattr(manager, "_now_iso", lambda: NOW)
    monkeypatch.setattr(manager, "now_utc", lambda: datetime.fromisoformat(NOW))
    def status(**kwargs):
        reads.append(["status", kwargs["limit"], kwargs.get("recent_run_limit")])
        return copy.deepcopy(case["status"])
    def quota(_status, *, goal_id, agent_id):
        reads.append(["quota", goal_id, agent_id])
        value = case["quotas"][goal_id]
        if value is None:
            raise OSError("Synthetic quota unavailable")
        return copy.deepcopy(value)
    monkeypatch.setattr(manager, "collect_status", status)
    monkeypatch.setattr(manager, "build_quota_should_run", quota)
    for module in (envelopes, scopes, manager):
        if not hasattr(module, "effect_runtime_result"):
            continue
        original = module.effect_runtime_result
        def instrument(operation, request, _original=original, **kwargs):
            crossings.append(operation)
            return _original(operation, request, **kwargs)
        monkeypatch.setattr(module, "effect_runtime_result", instrument)
    kwargs = dict(registry_path=tmp_path / "registry.json", runtime_root_override=None,
                  agent_id=case["agent_id"], limit=case["limit"])
    try:
        return project(operation, kwargs, case, reads, crossings)
    except Exception as exc:
        return {"error": str(exc), "reads": reads, "crossings": crossings}


def project(operation, kwargs, case, reads, crossings):
    if operation == "summary":
        payload = manager.build_summary_all(**kwargs, time_range=case["time_range"])
        markdown = manager.render_summary_all_markdown(payload)
    else:
        payload = manager.build_global_gates(**kwargs)
        markdown = manager.render_global_gates_markdown(payload)
    return {"json": json.dumps(payload, ensure_ascii=False, indent=2), "markdown": markdown,
            "reads": reads, "crossings": crossings}


@pytest.mark.parametrize("case", scenarios() + relation_scenarios(), ids=lambda case: case["name"])
@pytest.mark.parametrize("operation", ["summary", "gates"])
def test_transaction_bytes_and_source_reads(case, operation, monkeypatch, tmp_path):
    expected = json.loads(FIXTURE.with_name(case.get("fixture", FIXTURE.name)).read_text())[case["name"]][operation]
    actual = observe(case, operation, monkeypatch, tmp_path)
    for key in ("json", "markdown", "error"):
        assert actual.get(key) == expected.get(key)
    assert actual["reads"] == expected["reads"]
    if hasattr(manager, "effect_runtime_result"):
        assert actual["crossings"] == [f"presentation.global_{operation if operation == 'gates' else 'summary'}.project"]
    else:
        assert actual["crossings"] == expected["crossings"]
