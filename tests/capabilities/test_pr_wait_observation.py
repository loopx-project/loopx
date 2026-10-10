"""A closed queue entry must still resume its exact canonical PR dependency."""
from __future__ import annotations

import pytest
from subprocess import TimeoutExpired

from loopx.heartbeat_prequota import run_heartbeat_pre_quota
from loopx.todos import add_goal_todo, list_goal_todos
from tests.control_plane.canonical_authority_fixture import (
    isolate_sqlite_runtime, promoted_create_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_prequota_observes_merged_dependency_without_open_queue_membership(tmp_path, monkeypatch, provider):
    from loopx.capabilities.issue_fix import pr_lifecycle

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Continue after the dependency merges", claimed_by="agent-a",
        resume_when="pr_merged:owner/repo#42")
    todo_id = created["todo_id"]
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        return {"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
                "url": "https://github.com/owner/repo/pull/42"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    before = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert before["resume_ready"] is False
    result = run_heartbeat_pre_quota(registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="goal-a", agent_id="agent-a")
    assert len(calls) == 1
    assert result["checks"]["pr_dependencies"]["merged_count"] == 1
    after = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
    assert after["resume_ready"] is True
    for field in ("resume_when", "status", "claimed_by", "text"):
        assert after[field] == before[field]
    run_heartbeat_pre_quota(registry_path=registry, runtime_root_arg=str(runtime),
        goal_id="goal-a", agent_id="agent-a")
    assert len(calls) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("outcome", ["open", "closed", "timeout", "unauthenticated", "wrong_repo", "malformed"])
def test_unsatisfied_and_failed_observations_survive_restart_with_bounded_retry(tmp_path, monkeypatch, provider, outcome):
    from loopx.capabilities.issue_fix import pr_lifecycle, pr_wait_reconcile
    from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    todo_id = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
        text="Pending dependency", claimed_by="agent-a", resume_when="pr_merged:owner/repo#42")["todo_id"]
    clock = ["2026-10-01T01:00:00Z"]
    monkeypatch.setattr(pr_wait_reconcile, "now_utc_iso", lambda: clock[0])
    calls = []

    def fetch(reference, **kwargs):
        calls.append(reference)
        assert kwargs["compact"] is True
        if outcome == "timeout":
            raise TimeoutExpired("gh", 10)
        if outcome == "unauthenticated":
            raise RuntimeError("provider unavailable")
        if outcome == "malformed":
            return {"url": "https://github.com/owner/repo/pull/42"}
        return {"state": "OPEN" if outcome == "open" else "CLOSED" if outcome == "closed" else "MERGED",
                "mergedAt": "2026-10-01T00:00:00Z" if outcome == "wrong_repo" else None,
                "url": "https://github.com/other/repo/pull/42" if outcome == "wrong_repo" else "https://github.com/owner/repo/pull/42"}

    monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
    args = dict(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a", agent_id="agent-a")
    first = run_heartbeat_pre_quota(**args)["checks"]["pr_dependencies"]
    assert first["external_read_count"] == 1
    assert first["merged_count"] == 0
    assert first["degraded"] is (outcome not in {"open", "closed"})
    assert first["closed_without_merge_count"] == (1 if outcome == "closed" else 0)
    assert list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]["resume_ready"] is False
    events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
    assert not any(event["event_kind"] == "pr_merge" for event in events)
    clock[0] = "2026-10-01T01:29:59Z"
    quiet = run_heartbeat_pre_quota(**args)["checks"]["pr_dependencies"]
    assert quiet["external_read_count"] == 0
    assert quiet["degraded"] is (outcome not in {"open", "closed"})
    assert len(calls) == 1
    clock[0] = "2026-10-01T01:30:00Z"
    assert run_heartbeat_pre_quota(**args)["checks"]["pr_dependencies"]["external_read_count"] == 1
    assert len(calls) == 2
