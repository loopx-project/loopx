from __future__ import annotations

from typing import Any

from loopx.control_plane.effect_program import interpret_quota_should_run_packet
from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.todos import resume_condition
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos


def test_resume_evaluator_sends_only_matching_compact_merge_evidence(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_runtime(method: str, request: dict[str, Any]) -> dict[str, Any]:
        captured.update(request)
        assert method == "todo.resume_condition.evaluate"
        return {
            "schema_version": "todo_resume_evaluation_v0",
            "conditions": [],
        }

    monkeypatch.setattr(resume_condition, "effect_runtime_result", fake_runtime)
    huge = "x" * 3_000_000
    resume_condition.evaluate_todo_resume_conditions(
        [
            {
                "todo_id": "todo_waiting",
                "status": "deferred",
                "resume_when": "pr_merged:owner/repo#42",
            }
        ],
        source_items=[],
        rollout_events=[
            {
                "event_id": "irrelevant",
                "event_kind": "todo_update",
                "details": huge,
            },
            {
                "event_id": "wrong-pr",
                "event_kind": "pr_merged",
                "code_refs": {"pr_ref": "owner/repo#41", "payload": huge},
                "details": huge,
            },
            {
                "event_id": "matching-pr",
                "event_kind": "pr_merged",
                "recorded_at": "2026-09-03T07:00:00Z",
                "code_refs": {"pr_ref": "owner/repo#42", "payload": huge},
                "details": huge,
            },
        ],
    )

    assert captured["rollout_events"] == [
        {
            "event_kind": "pr_merged",
            "event_id": "matching-pr",
            "recorded_at": "2026-09-03T07:00:00Z",
            "code_refs": {"pr_ref": "owner/repo#42"},
        }
    ]


def test_timezone_aware_resume_at_is_stable_across_ticks_and_summary_restarts() -> None:
    authored = "resume_at:2026-09-14T09:30:00+08:00"
    normalized = resume_condition.require_supported_todo_resume_when(authored)
    assert normalized == "resume_at:2026-09-14T01:30:00Z"
    fractional = "resume_at:2026-09-14T09:30:00.12+08:00"
    assert resume_condition.require_supported_todo_resume_when(fractional) == (
        "resume_at:2026-09-14T01:30:00.120Z"
    )
    assert resume_condition.normalize_todo_resume_when(fractional) == (
        "resume_at:2026-09-14T01:30:00.120Z"
    )
    item = {
        "todo_id": "todo_scheduled",
        "role": "agent",
        "status": "deferred",
        "task_class": "advancement_task",
        "resume_when": authored,
    }

    pending = resume_condition.evaluate_todo_resume_conditions(
        [item], source_items=[], evaluated_at="2026-09-14T01:29:59Z"
    )["todo_scheduled"]
    first_due = resume_condition.evaluate_todo_resume_conditions(
        [item], source_items=[], evaluated_at="2026-09-14T01:30:00Z"
    )["todo_scheduled"]
    replayed = resume_condition.evaluate_todo_resume_conditions(
        [item], source_items=[], evaluated_at="2026-09-15T00:00:00Z"
    )["todo_scheduled"]

    assert pending["satisfied"] is False
    assert pending["material_change_generation"] == 0
    assert pending["resume_receipt"] is None
    assert first_due["satisfied"] is True
    assert first_due["material_change_generation"] == 1
    assert first_due["resume_receipt"] == replayed["resume_receipt"]

    state = (
        "# Active Goal State\n\n## Agent Todo\n\n"
        "- [x] Wake after the scheduled instant.\n"
        "  <!-- loopx:todo todo_id=todo_scheduled status=deferred "
        f"task_class=advancement_task resume_when={authored} -->\n"
    )
    before_projection = parse_active_state_todos(
        state, item_limit=None, evaluated_at="2026-09-14T01:29:59Z"
    )
    after_projection = parse_active_state_todos(
        state, item_limit=None, evaluated_at="2026-09-14T01:30:00Z"
    )
    restarted_projection = parse_active_state_todos(
        state, item_limit=None, evaluated_at="2026-09-15T00:00:00Z"
    )
    before = before_projection["agent_todos"]["items"][0]
    after = after_projection["agent_todos"]["items"][0]
    restarted = restarted_projection["agent_todos"]["items"][0]
    assert before["resume_ready"] is False
    assert after["resume_ready"] is True
    assert after["resume_condition"]["resume_receipt"] == restarted[
        "resume_condition"
    ]["resume_receipt"]

    def managed_turn(projection: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        summary = projection["agent_todos"]
        summary["claim_scope"] = {"agent_id": "agent-a"}
        status = quota_status_payload(
            goal_id="typed-date-resume-fixture",
            status="active",
            agent_todos=summary,
            recommended_action="Wake after the scheduled instant.",
            next_action="Wake after the scheduled instant.",
            claim_scope_agent_id="agent-a",
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": ["agent-a"],
            },
        )
        packet = build_quota_should_run(
            status,
            goal_id="typed-date-resume-fixture",
            agent_id="agent-a",
            turn_instance_id="managed-date-turn",
        )
        return packet, interpret_quota_should_run_packet(
            packet,
            goal_id="typed-date-resume-fixture",
            agent_id="agent-a",
        )

    pending_packet, pending_turn = managed_turn(before_projection)
    due_packet, due_turn = managed_turn(after_projection)
    replay_packet, replay_turn = managed_turn(restarted_projection)
    assert pending_packet["decision"] == "agent_scope_wait"
    assert pending_turn.observation.effective_action == "agent_scope_wait"
    for packet, turn in ((due_packet, due_turn), (replay_packet, replay_turn)):
        assert packet["decision"] == "successor_replan_required"
        assert packet["selected_todo"]["todo_id"] == "todo_scheduled"
        assert turn.observation.effective_action == "successor_replan_required"
    due_receipt = due_packet["agent_todo_summary"]["deferred_items"][0][
        "resume_condition"
    ]["resume_receipt"]
    replay_receipt = replay_packet["agent_todo_summary"]["deferred_items"][0][
        "resume_condition"
    ]["resume_receipt"]
    assert due_receipt == replay_receipt


def test_resume_at_rejects_missing_timezone_and_invalid_calendar_date() -> None:
    for value in (
        "resume_at:2026-09-14T09:30:00",
        "resume_at:2026-02-30T09:30:00+08:00",
        "resume_at:2026-09-14T09:30:00+08:60",
        "resume_at:2026-09-14T09:30:00+15:00",
        "resume_at:1000-01-01T00:00:00+14:00",
        "resume_at:9999-12-31T23:59:59-14:00",
    ):
        try:
            resume_condition.require_supported_todo_resume_when(value)
        except ValueError as exc:
            assert "timezone-aware-rfc3339" in str(exc)
        else:
            raise AssertionError(f"expected invalid resume_at: {value}")


def test_resume_evaluator_omits_rollout_history_without_pr_waits(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_runtime(method: str, request: dict[str, Any]) -> dict[str, Any]:
        captured.update(request)
        assert method == "todo.resume_condition.evaluate"
        return {
            "schema_version": "todo_resume_evaluation_v0",
            "conditions": [],
        }

    monkeypatch.setattr(resume_condition, "effect_runtime_result", fake_runtime)
    resume_condition.evaluate_todo_resume_conditions(
        [
            {
                "todo_id": "todo_waiting",
                "status": "deferred",
                "resume_when": "todo_done:todo_source",
            }
        ],
        source_items=[{"todo_id": "todo_source", "status": "done"}],
        rollout_events=[
            {
                "event_id": "large-history-row",
                "event_kind": "todo_update",
                "details": "x" * 3_000_000,
            }
        ],
    )

    assert captured["rollout_events"] == []


def test_resume_evaluator_deduplicates_identity_without_losing_the_first_proof(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_runtime(method: str, request: dict[str, Any]) -> dict[str, Any]:
        captured.update(request)
        assert method == "todo.resume_condition.evaluate"
        return {
            "schema_version": "todo_resume_evaluation_v0",
            "conditions": [],
        }

    monkeypatch.setattr(resume_condition, "effect_runtime_result", fake_runtime)
    events = [
        {
            "event_id": f"merge-{index}",
            "event_kind": "pr_merge",
            "pr_ref": "owner/repo#42",
        }
        for index in range(300)
    ]
    resume_condition.evaluate_todo_resume_conditions(
        [
            {
                "todo_id": "todo_waiting",
                "status": "deferred",
                "resume_when": "pr_merged:owner/repo#42",
            }
        ],
        source_items=[],
        rollout_events=events,
    )

    compacted = captured["rollout_events"]
    assert len(compacted) == 1
    assert compacted[0]["event_id"] == "merge-0"


def test_resume_evaluator_filters_exact_repository_before_bounding_events(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_runtime(method: str, request: dict[str, Any]) -> dict[str, Any]:
        captured.update(request)
        assert method == "todo.resume_condition.evaluate"
        return {
            "schema_version": "todo_resume_evaluation_v0",
            "conditions": [],
        }

    monkeypatch.setattr(resume_condition, "effect_runtime_result", fake_runtime)
    events = [
        {
            "event_id": "exact-repository-merge",
            "event_kind": "pr_merge",
            "pr_ref": "owner/repo#42",
        },
        *[
            {
                "event_id": f"other-repository-merge-{index}",
                "event_kind": "pr_merge",
                "pr_ref": "other/repo#42",
            }
            for index in range(300)
        ],
    ]

    resume_condition.evaluate_todo_resume_conditions(
        [
            {
                "todo_id": "todo_waiting",
                "status": "deferred",
                "resume_when": "pr_merged:owner/repo#42",
            }
        ],
        source_items=[],
        rollout_events=events,
    )

    assert captured["rollout_events"] == [
        {
            "event_kind": "pr_merge",
            "event_id": "exact-repository-merge",
            "pr_ref": "owner/repo#42",
        }
    ]


def test_resume_evaluator_uses_task_repository_to_bound_unqualified_pr_wait(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_runtime(method: str, request: dict[str, Any]) -> dict[str, Any]:
        captured.update(request)
        assert method == "todo.resume_condition.evaluate"
        return {
            "schema_version": "todo_resume_evaluation_v0",
            "conditions": [],
        }

    monkeypatch.setattr(resume_condition, "effect_runtime_result", fake_runtime)
    resume_condition.evaluate_todo_resume_conditions(
        [
            {
                "todo_id": "todo_waiting",
                "status": "deferred",
                "resume_when": "pr_merged:#42",
                "task_repository": "git:github.com/owner/repo",
            }
        ],
        source_items=[],
        rollout_events=[
            {
                "event_id": "wrong-repository",
                "event_kind": "pr_merge",
                "pr_ref": "other/repo#42",
            },
            {
                "event_id": "matching-repository",
                "event_kind": "pr_merge",
                "pr_ref": "https://github.com/owner/repo/pull/42",
            },
        ],
    )

    assert captured["rollout_events"] == [
        {
            "event_kind": "pr_merge",
            "event_id": "matching-repository",
            "pr_ref": "https://github.com/owner/repo/pull/42",
        }
    ]


def test_no_global_merge_budget_can_evict_a_different_exact_dependency():
    items = [{"todo_id": f"todo_wait_{index}", "status": "open",
              "resume_when": f"pr_merged:owner/repo#{index + 1}"} for index in range(270)]
    events = [{"event_kind": "pr_merge", "event_id": f"original-{index}",
               "pr_ref": f"owner/repo#{index + 1}"} for index in range(270)]
    result = resume_condition.evaluate_todo_resume_conditions(items, source_items=[], rollout_events=events)
    assert len(result) == 270
    assert all(condition["satisfied"] for condition in result.values())
    assert result["todo_wait_0"]["matched_event_id"] == "original-0"
