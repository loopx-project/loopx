"""Regression coverage for the typed blocked-transition notice (#4381).

#4543 made the quota projection admit that a blocked higher-priority Todo
should be surfaced while a fallback runs. An admission is not a delivery: this
suite pins the contract that turns that intent into one owner-visible notice
with a cause, an impact, a responsible party and a recovery condition, plus the
revision digest a later ledger dedups on.

Scope note: the emission decision, the persisted delivery/readback state and
the reconciliation of resolved and superseded blockers arrive with the
successor slice that owns a real caller. Only behaviour that is reachable from
production code is pinned here.
"""

from __future__ import annotations

from typing import Any

from loopx.control_plane.quota.blocked_transition_notice import (
    NOTICE_DELIVERY_PENDING,
    build_blocked_transition_notice,
)
from loopx.control_plane.quota.should_run_prepare import _blocked_priority_fallback
from loopx.control_plane.todos.summary_item import compact_todo_summary_item
from loopx.control_plane.todos.todo_summary import project_asset_todo_summary
from loopx.control_plane.work_items.interaction_contract import (
    _blocked_priority_fallback_user_reason,
)

BLOCKED_TODO_ID = "todo_aaaaaaaaaaaa"
FALLBACK_TODO_ID = "todo_bbbbbbbbbbbb"


def _advancement_item(
    todo_id: str,
    text: str,
    *,
    status: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "todo_id": todo_id,
        "text": text,
        "status": status,
        "task_class": "advancement_task",
        **extra,
    }


def _fallback() -> dict[str, Any]:
    return _advancement_item(
        FALLBACK_TODO_ID,
        "[P1] Prepare independent documentation",
        status="open",
    )


def _agent_owned_blocker(reason: str = "Required input has not arrived") -> dict[str, Any]:
    return _advancement_item(
        BLOCKED_TODO_ID,
        "[P0] Validate the primary deliverable",
        status="blocked",
        reason=reason,
    )


def _owner_gate_blocker() -> dict[str, Any]:
    return {
        "todo_id": BLOCKED_TODO_ID,
        "text": "[P0] Confirm the production rollout window",
        "status": "blocked",
        "task_class": "user_gate",
        "role": "user",
        "reason": "The owner has not confirmed the rollout window",
    }


def test_agent_owned_blocker_tells_the_owner_without_asking_for_action() -> None:
    notice = build_blocked_transition_notice(
        _agent_owned_blocker(), selected_executable=_fallback()
    )

    assert notice is not None
    assert notice["owner_must_know"] is True
    assert notice["owner_must_act"] is False
    assert notice["responsible_party"] == "agent"
    assert notice["task"]["todo_id"] == BLOCKED_TODO_ID
    assert notice["cause"] == "Required input has not arrived"
    assert "reason=Required input has not arrived" in notice["evidence"]
    assert "status=blocked" in notice["evidence"]
    assert "will not advance until" in notice["impact"]
    assert notice["recovery_condition"]["satisfied"] is False
    assert notice["recovery_condition"]["kind"] == "status_blocked"
    assert notice["next_action"].startswith("No owner action is required")
    # No surface has been authorized yet, so the notice is pending, not sent.
    assert notice["delivery"]["state"] == NOTICE_DELIVERY_PENDING


def test_owner_gate_blocker_asks_one_concrete_question() -> None:
    notice = build_blocked_transition_notice(
        _owner_gate_blocker(), selected_executable=_fallback()
    )

    assert notice is not None
    assert notice["owner_must_know"] is True
    assert notice["owner_must_act"] is True
    assert notice["responsible_party"] == "owner"
    assert "one concrete question" in notice["next_action"]


def test_resume_condition_names_the_party_that_can_resolve_it() -> None:
    waiting = _advancement_item(
        BLOCKED_TODO_ID,
        "[P0] Validate the primary deliverable",
        status="open",
        resume_when="pr_merged:acme/loopx#4543",
        resume_ready=False,
    )

    notice = build_blocked_transition_notice(waiting, selected_executable=_fallback())

    assert notice is not None
    assert notice["recovery_condition"]["kind"] == "pr_merged"
    assert "pull request acme/loopx#4543 must merge" in notice["impact"]
    assert notice["responsible_party"] == "external_dependency"
    assert notice["owner_must_act"] is False


def test_scheduled_future_monitor_earns_no_notice() -> None:
    future_monitor = {
        "todo_id": BLOCKED_TODO_ID,
        "text": "[P1-monitor] Observe the stable public fixture",
        "status": "open",
        "task_class": "continuous_monitor",
        "next_due_at": "2999-01-01T00:00:00Z",
    }

    assert build_blocked_transition_notice(future_monitor) is None


def test_the_revision_digest_separates_an_unchanged_cause_from_a_new_one() -> None:
    notice = build_blocked_transition_notice(
        _agent_owned_blocker(), selected_executable=_fallback()
    )
    repeat = build_blocked_transition_notice(
        _agent_owned_blocker(), selected_executable=_fallback()
    )
    changed = build_blocked_transition_notice(
        _agent_owned_blocker(reason="Required input has not arrived (vendor retry 2)"),
        selected_executable=_fallback(),
    )
    superseded = build_blocked_transition_notice(
        {**_agent_owned_blocker(), "superseded_by": "todo_cccccccccccc"},
        selected_executable=_fallback(),
    )
    assert notice is not None and repeat is not None
    assert changed is not None and superseded is not None

    # The digest is the dedup key the successor ledger consumes: same blocker
    # and same cause is one notice, a materially changed cause is a new one.
    assert notice["blocker_identity"] == repeat["blocker_identity"]
    assert notice["blocker_revision"] == repeat["blocker_revision"]
    assert changed["blocker_revision"] != notice["blocker_revision"]
    assert superseded["blocker_revision"] != notice["blocker_revision"]


def test_blocked_primary_with_running_fallback_carries_the_notice() -> None:
    fallback = _blocked_priority_fallback(
        {
            "first_open_items": [_agent_owned_blocker(), _fallback()],
            "first_executable_items": [_fallback()],
        }
    )

    assert fallback is not None
    assert fallback["notify_user"] is True
    assert fallback["requires_user_action"] is False
    notices = fallback["blocked_transition_notices"]
    assert len(notices) == 1
    assert notices[0]["blocker_identity"] == f"todo:{BLOCKED_TODO_ID}"
    assert notices[0]["owner_must_act"] is False
    assert "Prepare independent documentation" in notices[0]["impact"]
    # The fallback keeps running; the notice does not gate delivery.
    assert fallback["selected_executable"]["todo_id"] == FALLBACK_TODO_ID


def test_compact_blocked_advancement_keeps_cause_for_fallback_notice() -> None:
    blocked = compact_todo_summary_item(
        {**_agent_owned_blocker(), "note": "private detail", "evidence": "private path"}
    )
    selected = compact_todo_summary_item(_fallback())

    assert blocked["reason"] == "Required input has not arrived"
    assert "note" not in blocked
    assert "evidence" not in blocked
    fallback = _blocked_priority_fallback(
        {"first_open_items": [blocked, selected], "first_executable_items": [selected]}
    )
    assert fallback is not None
    assert fallback["blocked_transition_notices"][0]["cause"] == blocked["reason"]
    assert "private detail" not in str(fallback)
    assert "private path" not in str(fallback)


def test_project_asset_blocked_advancement_cause_is_public_safe() -> None:
    blocked = _agent_owned_blocker()
    summary = {"items": [blocked], "first_open_items": [blocked], "open_count": 1}

    asset = project_asset_todo_summary(summary, role="agent")
    assert asset is not None
    assert asset["items"][0]["reason"] == "Required input has not arrived"

    unsafe = {**blocked, "reason": "/" + "Users/example/private/plan.md"}
    unsafe_asset = project_asset_todo_summary(
        {"items": [unsafe], "first_open_items": [unsafe], "open_count": 1},
        role="agent",
    )
    assert unsafe_asset is not None
    assert "reason" not in unsafe_asset["items"][0]


def test_owner_facing_reason_carries_the_typed_notice() -> None:
    fallback = _blocked_priority_fallback(
        {
            "first_open_items": [_agent_owned_blocker(), _fallback()],
            "first_executable_items": [_fallback()],
        }
    )
    assert fallback is not None

    reason = _blocked_priority_fallback_user_reason(
        {"blocked_priority_fallback": fallback}
    )

    assert reason is not None
    assert "Validate the primary deliverable" in reason
    assert "Required input has not arrived" in reason
    assert "will not advance until" in reason
    assert "No owner action is required" in reason


def test_owner_facing_reason_falls_back_to_the_generic_prose() -> None:
    legacy = {
        "kind": "blocked_priority_fallback",
        "notify_user": True,
        "reason": "a higher-priority agent todo is blocked",
    }

    assert (
        _blocked_priority_fallback_user_reason({"blocked_priority_fallback": legacy})
        == "a higher-priority agent todo is blocked"
    )


def test_the_owner_reason_renders_one_notice_while_the_payload_keeps_every_one() -> None:
    notices = [
        build_blocked_transition_notice(
            _agent_owned_blocker(), selected_executable=_fallback()
        ),
        build_blocked_transition_notice(
            _owner_gate_blocker(), selected_executable=_fallback()
        ),
    ]
    assert all(notice is not None for notice in notices)

    payload = {
        "kind": "blocked_priority_fallback",
        "notify_user": True,
        "reason": "a higher-priority agent todo is blocked",
        "blocked_transition_notices": notices,
    }

    reason = _blocked_priority_fallback_user_reason(
        {"blocked_priority_fallback": payload}
    )

    assert reason is not None
    # The reason is one sentence for one blocker, not a list to scan.
    assert "Validate the primary deliverable" in reason
    assert "Confirm the production rollout window" not in reason
    # The payload keeps the full set for whoever needs to read it.
    assert len(payload["blocked_transition_notices"]) == 2
