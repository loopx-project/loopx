"""Pinned pre-cutover complete results, including lossless Python metadata.

Snapshots were captured from 867615e2c before any production changes. Retain
these complete-output snapshots as the transaction parity guard.
"""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.todos import quota_selection, quota_summary


def row(number=1, **fields):
    return {"todo_id": f"todo_synthetic_{number}", "index": number,
            "text": "Synthetic work", "status": "open", "priority": "P1",
            "task_class": "advancement_task", **fields}


def source(rows=None, **fields):
    rows = rows or []
    return {"schema_version": "todo_summary_v0", "source_section": "User Todo",
            "total_count": len(rows), "open_count": len(rows), "done_count": 0,
            "deferred_count": 0, "items": rows, **fields}


def cases():
    return {
        "non_dict": (None, {}),
        "empty": ({}, {}),
        "user_only": (source([row(task_class="user_gate", global_gate=True)]), {}),
        "agent_scoped": (source([row(claimed_by="agent-a"), row(2, claimed_by="agent-b")]),
                         {"agent_identity": {"agent_id": "agent-a"}}),
        "user_actions": (source([row(task_class="user_gate", blocks_agent="agent-b"),
                                  row(2, task_class="user_action", bound_agent="agent-b"),
                                  row(3, task_class="user_action")]),
                         {"agent_identity": {"agent_id": "agent-a"},
                          "filter_user_gate_blocks_agent": True}),
        "recent_batches": (source(recent_completed_advancement_items=[
            row(i, status="done", text="Completed synthetic work. " * 40)
            for i in range(1, 9)]), {}),
        "vision_blockers": (source([row(task_class="blocker", status="blocked",
                                        reason="Synthetic dependency", claimed_by="agent-a")],
                                   vision_wait_states=[{"agent_id": "agent-a", "state": "waiting"},
                                                       {"agent_id": "agent-b", "state": "waiting"}]),
                            {"agent_identity": {"agent_id": "agent-a"}}),
        "monitors_warnings": (source([
            row(task_class="continuous_monitor", next_due_at="2020-01-01T00:00:00Z", watch_only=True),
            row(2, task_class="continuous_monitor")],
            completed_without_successor_count="2", completed_without_successor_items=[row(3, status="done")]), {}),
        "legacy_next": ({"next": "Synthetic next", "next_index": "7", "next_claimed_by": "agent-a",
                         "total": "3", "open": "1", "done": "2"}, {}),
        "malformed_history": (source([row(priority=7, index="2", claimed_by=["agent-a"])],
                                     total_count="historical", done_count="2",
                                     deferred_count=None, recent_completed_advancement_items=[None, 7]), {}),
        "large_backlog": (source([row(i) for i in range(1, 25)]), {}),
        "lossless_metadata": (source(total_count=9007199254740993, done_count=1.0,
                                     goal_acceptance_contract={"revision": 9007199254740993,
                                                               "ratio": -0.0}), {}),
    }


def case_input(name, kind):
    value, options = cases()[name]
    if kind == "project_asset" and isinstance(value, dict) and "total_count" in value:
        value = dict(value)
        # Exercise the legacy asset composition, rather than its user-summary delegation.
        for old, new in (("total_count", "total"), ("open_count", "open"), ("done_count", "done")):
            value[new] = value.pop(old)
    return value, options


@pytest.fixture
def runtime_calls(monkeypatch):
    calls = Counter()
    original = effect_runtime.effect_runtime_result

    def counted(operation, *args, **kwargs):
        calls[operation] += 1
        return original(operation, *args, **kwargs)

    # quota_selection holds an imported reference; summary_item imports lazily.
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", counted)
    monkeypatch.setattr(quota_selection, "effect_runtime_result", counted)
    monkeypatch.setattr(quota_selection, "now_utc",
                        lambda: datetime(2026, 10, 1, tzinfo=timezone.utc))
    # Exercise display batching with small synthetic rows, without a large fixture.
    monkeypatch.setattr(effect_runtime, "MAX_REQUEST_BYTES", 8192)
    return calls


@pytest.mark.parametrize("kind", ["user", "project_asset"])
@pytest.mark.parametrize("name", list(cases()))
def test_complete_pre_cutover_output_and_crossings(name, kind, runtime_calls):
    value, options = case_input(name, kind)
    project = (quota_summary.summarize_user_todos_for_quota if kind == "user"
               else quota_summary.summarize_project_asset_todos_for_quota)
    result = project(value, **options)
    expected = json.loads(Path(__file__).with_name("quota_summary_transaction_snapshots.json").read_text())
    snapshot = expected[f"{kind}/{name}"]
    assert json.dumps(result, sort_keys=False) == snapshot["output"]
    assert dict(runtime_calls) == ({"todo.quota_planning.project": 1} if snapshot["calls"] else {})
    if name == "lossless_metadata":
        assert result["total_count"] == 9007199254740993
        assert type(result["total_count"]) is int
        assert type(result["done_count"]) is float
    if name == "large_backlog":
        assert len(result["backlog_items"]) == 8
    if name == "legacy_next" and kind == "project_asset":
        assert result["first_open_items"][0]["index"] == "7"


@pytest.mark.parametrize("kind", ["user", "project_asset"])
def test_owner_refusal_propagates_without_fallback(kind, monkeypatch):
    calls = []

    def refuse(operation, *args, **kwargs):
        calls.append(operation)
        raise effect_runtime.EffectRuntimeRejected("synthetic owner refusal")

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", refuse)
    monkeypatch.setattr(quota_selection, "effect_runtime_result", refuse)
    project = (quota_summary.summarize_user_todos_for_quota if kind == "user"
               else quota_summary.summarize_project_asset_todos_for_quota)
    with pytest.raises(ValueError, match="synthetic owner refusal"):
        project({})
    assert calls == ["todo.quota_planning.project"]


def test_plain_javascript_json_transport_cannot_preserve_current_metadata():
    import subprocess

    source_value = cases()["lossless_metadata"][0]
    encoded = json.dumps(source_value, sort_keys=False)
    transported = subprocess.check_output(
        ["node", "-e", "let s='';process.stdin.on('data',c=>s+=c);"
         "process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s))))"],
        input=encoded, text=True,
    )
    decoded = json.loads(transported)
    assert source_value["total_count"] == 9007199254740993
    assert decoded["total_count"] == 9007199254740992
    assert type(source_value["done_count"]) is float
    assert type(decoded["done_count"]) is int
