from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..effect_program import ReceiptBoundMonitorPhase
from ..todos.contract import TODO_TASK_CLASS_MONITOR, normalize_todo_id
from ..todos.todo_semantics import todo_priority_label, todo_priority_rank


@dataclass(frozen=True)
class WorkLaneObservation:
    """Read-only work facts; neither a scheduling decision nor execution authority."""

    lane: str | None
    must_attempt: bool
    next_action: str | None


def observe_work_lane(
    contract: Any, *, next_action: Any = None,
) -> WorkLaneObservation | None:
    """Keep legacy field decoding with the lane owner, outside new consumers."""
    if not isinstance(contract, dict):
        return None
    lane = contract.get("lane")
    action = contract.get("obligation") or next_action
    return WorkLaneObservation(
        lane=lane if isinstance(lane, str) else None,
        must_attempt=contract.get("must_attempt_work") is True,
        next_action=action if isinstance(action, str) else None,
    )


WORK_LANE_CONTRACT_SCHEMA_VERSION = "work_lane_contract_v1"
WORK_LANE_RECEIPT_BOUND_MONITOR_SETTLEMENT_OBLIGATION = (
    "settle_receipt_bound_monitor"
)
WORK_LANE_RECEIPT_BOUND_MONITOR_SETTLED_OBLIGATION = (
    "finish_settled_receipt_bound_monitor_turn"
)
WORK_LANE_RECEIPT_BOUND_DEFERRED_OBLIGATION = (
    "wait_for_receipt_bound_deferred_todo"
)
WORK_LANE_CURRENT_AGENT_MONITOR_REPAIR_OBLIGATIONS = {
    "attempt_due_monitor",
    "repair_monitor_schedule_metadata",
    "repair_resume_gate_or_close_standing_monitor",
    WORK_LANE_RECEIPT_BOUND_MONITOR_SETTLEMENT_OBLIGATION,
}
WORK_LANE_EXTERNAL_EVIDENCE_OBSERVATION_OBLIGATION = (
    "observe_external_evidence_or_blocker"
)
WORK_LANE_LARK_INBOX_REPLY_DUE_OBLIGATION = "drain_lark_inbox_reply_due"
WORK_LANE_OPERATOR_INBOX_MATERIAL_REVIEW_DUE_OBLIGATION = (
    "drain_operator_inbox_material_review_due"
)
WORK_LANE_TODO_MONITOR_DUE_KIND = "todo_monitor_due"


WORK_LANE_TODO_ITEM_FIELDS = (
    "index",
    "text",
    "todo_id",
    "status",
    "priority",
    "task_class",
    "action_kind",
    "task_repository",
    "claimed_by",
    "target_key",
    "next_due_at",
    "expires_at",
    "watch_only",
    "resume_when",
    "resume_ready",
    "blocking_monitor_todo_id",
    "result_hash",
)
PRIVATE_BOUNDARY_MONITOR_RESULT_HASHES = {
    "private_boundary_no_authorized_read",
}
PRIVATE_BOUNDARY_MONITOR_ACTION_KIND_HINTS = (
    "private",
    "local_department_doc",
)


def due_monitor_can_preempt_advancement(item: dict[str, Any]) -> bool:
    """Return false for monitor work that requires private/local material."""

    result_hash = str(item.get("result_hash") or "").strip().lower()
    if result_hash in PRIVATE_BOUNDARY_MONITOR_RESULT_HASHES:
        return False
    action_kind = str(item.get("action_kind") or "").strip().lower()
    if any(hint in action_kind for hint in PRIVATE_BOUNDARY_MONITOR_ACTION_KIND_HINTS):
        return False
    priority = str(todo_priority_label(item) or "").strip().upper()
    return "LOCAL" not in priority


def due_monitor_preempts_advancement(
    due_monitor: dict[str, Any] | None,
    *,
    first_advancement: dict[str, Any] | None,
) -> bool:
    if not due_monitor:
        return False
    if not due_monitor_can_preempt_advancement(due_monitor):
        return False
    return first_advancement is None or (
        todo_priority_rank(due_monitor) < todo_priority_rank(first_advancement)
    )


def work_lane_contract_requires_current_agent_attempt(
    contract: dict[str, Any] | None,
) -> bool:
    """Return true when the work-lane contract is itself an actionable lane.

    This intentionally covers monitor-derived repair/attempt obligations, not
    every `advancement_task` contract. A generic advancement contract may still
    describe goal-level work claimed by another agent; monitor-derived contracts
    are built from current-agent/unclaimed monitor projections and must not be
    collapsed into agent-scope wait just because no ordinary advancement todo
    exists.
    """

    if not isinstance(contract, dict):
        return False
    if contract.get("must_attempt_work") is not True:
        return False
    obligation = str(contract.get("obligation") or "")
    if obligation in WORK_LANE_CURRENT_AGENT_MONITOR_REPAIR_OBLIGATIONS:
        return True
    if obligation == WORK_LANE_LARK_INBOX_REPLY_DUE_OBLIGATION:
        return True
    if obligation == WORK_LANE_OPERATOR_INBOX_MATERIAL_REVIEW_DUE_OBLIGATION:
        return True
    return bool(
        obligation == WORK_LANE_EXTERNAL_EVIDENCE_OBSERVATION_OBLIGATION
        and contract.get("selected_todo_id")
        and int(contract.get("monitor_due_count") or 0) > 0
    )


def work_lane_contract_is_due_monitor_attempt(
    contract: dict[str, Any] | None,
) -> bool:
    return bool(
        isinstance(contract, dict)
        and contract.get("monitor_kind") == WORK_LANE_TODO_MONITOR_DUE_KIND
        and contract.get("must_attempt_work") is True
    )


def preserve_heartbeat_receipt_bound_work_lane(
    contract: dict[str, Any] | None,
    *,
    selected_todo: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Keep a committed same-turn Todo binding ahead of a newly due monitor."""

    if not isinstance(contract, dict):
        return contract
    if not isinstance(selected_todo, dict):
        return contract
    todo_id = normalize_todo_id(selected_todo.get("todo_id"))
    if not todo_id or selected_todo.get("selection_binding") != "heartbeat_receipt":
        return contract
    if selected_todo.get("task_class") == TODO_TASK_CLASS_MONITOR:
        raw_monitor_phase = selected_todo.get("receipt_bound_monitor_phase")
        try:
            if not isinstance(raw_monitor_phase, str):
                raise TypeError("monitor phase must be a string")
            monitor_phase = ReceiptBoundMonitorPhase(raw_monitor_phase)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "receipt-bound monitor selection requires an explicit monitor phase"
            ) from exc
        if monitor_phase is ReceiptBoundMonitorPhase.SETTLEMENT_PENDING:
            return {
                **contract,
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "continuous_monitor",
                "obligation": (
                    WORK_LANE_RECEIPT_BOUND_MONITOR_SETTLEMENT_OBLIGATION
                ),
                "must_attempt_work": True,
                "selection_binding": "heartbeat_receipt",
                "selected_todo_id": todo_id,
                "monitor_due_count": 0,
                "monitor_due_items": [],
                "receipt_bound_monitor_item": selected_todo,
                "reason_codes": [
                    "heartbeat_receipt_bound_replay",
                    "monitor_poll_already_recorded",
                    "same_turn_settlement_identity",
                ],
                "monitor_policy": "settle_receipt_bound_monitor_without_repoll",
                "deferred_work_lane": contract,
                "action": (
                    "finish refresh and quota settlement for the already-polled "
                    "monitor bound to this heartbeat turn; do not poll again; "
                    "reconsider its successor on the next turn"
                ),
            }
        if monitor_phase is ReceiptBoundMonitorPhase.SETTLED:
            return {
                **contract,
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "continuous_monitor",
                "obligation": WORK_LANE_RECEIPT_BOUND_MONITOR_SETTLED_OBLIGATION,
                "must_attempt_work": False,
                "selection_binding": "heartbeat_receipt",
                "selected_todo_id": todo_id,
                "monitor_due_count": 0,
                "monitor_due_items": [],
                "receipt_bound_monitor_item": selected_todo,
                "reason_codes": [
                    "heartbeat_receipt_bound_replay",
                    "monitor_poll_already_recorded",
                    "same_turn_settlement_complete",
                ],
                "monitor_policy": "finish_settled_turn_before_reselection",
                "deferred_work_lane": contract,
                "action": (
                    "finish this already-settled heartbeat turn without another "
                    "poll, writeback, or quota spend; reconsider its successor "
                    "under a new turn identity"
                ),
            }
        return {
            **contract,
            "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
            "lane": "continuous_monitor",
            "obligation": "attempt_due_monitor",
            "must_attempt_work": True,
            "selection_binding": "heartbeat_receipt",
            "selected_todo_id": todo_id,
            "monitor_due_items": [selected_todo],
            "reason_codes": [
                "due_monitor_context",
                "heartbeat_receipt_bound_replay",
                "same_turn_settlement_identity",
            ],
            "monitor_policy": "settle_receipt_bound_monitor_before_reselection",
            "deferred_work_lane": contract,
            "action": (
                "settle the due monitor already bound to this heartbeat turn; "
                "reconsider newly runnable monitor priority on the next turn"
            ),
        }
    if not work_lane_contract_is_due_monitor_attempt(contract):
        return contract
    return {
        "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
        "lane": "advancement_task",
        "next_lane": str(contract.get("lane") or "continuous_monitor"),
        "obligation": "continue_heartbeat_receipt_bound_todo",
        "must_attempt_work": True,
        "selection_binding": "heartbeat_receipt",
        "selected_todo_id": todo_id,
        "monitor_due_count": int(contract.get("monitor_due_count") or 0),
        "monitor_due_items": list(contract.get("monitor_due_items") or []),
        "reason_codes": [
            "heartbeat_receipt_bound_replay",
            "same_turn_settlement_identity",
            "due_monitor_context",
            "auxiliary_monitor_observation_allowed",
        ],
        "monitor_policy": (
            "auxiliary_no_spend_observation_then_continue_bound_todo"
        ),
        "deferred_work_lane": contract,
        "action": (
            "the Todo already bound to this heartbeat turn remains the only quota "
            "settlement target; a separately identified due monitor may record one "
            "no-spend observation receipt before that Todo continues"
        ),
    }


def receipt_bound_deferred_work_lane(
    *, todo_id: str,
) -> dict[str, Any]:
    """Keep an immutable Turn binding visible without executing a deferred Todo."""

    normalized = normalize_todo_id(todo_id)
    if not normalized:
        raise ValueError("receipt-bound deferred work lane requires a Todo id")
    return {
        "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
        "lane": "advancement_task",
        "obligation": WORK_LANE_RECEIPT_BOUND_DEFERRED_OBLIGATION,
        "must_attempt_work": False,
        "selection_binding": "heartbeat_receipt",
        "selected_todo_id": normalized,
        "reason_codes": [
            "heartbeat_receipt_bound_replay",
            "receipt_bound_todo_deferred",
            "successor_requires_fresh_turn",
        ],
        "action": (
            "the Todo bound to this heartbeat turn is deferred; do not execute "
            "or spend this turn, and select independent work under a fresh turn"
        ),
    }


def work_lane_contract_is_lark_inbox_reply_due(
    contract: dict[str, Any] | None,
) -> bool:
    return bool(
        isinstance(contract, dict)
        and contract.get("obligation") == WORK_LANE_LARK_INBOX_REPLY_DUE_OBLIGATION
        and contract.get("must_attempt_work") is True
    )


def work_lane_contract_is_operator_inbox_material_review_due(
    contract: dict[str, Any] | None,
) -> bool:
    return bool(
        isinstance(contract, dict)
        and contract.get("obligation")
        == WORK_LANE_OPERATOR_INBOX_MATERIAL_REVIEW_DUE_OBLIGATION
        and contract.get("must_attempt_work") is True
    )


def work_lane_contract_is_receipt_bound_monitor_settled(
    contract: dict[str, Any] | None,
) -> bool:
    return bool(
        isinstance(contract, dict)
        and contract.get("obligation")
        == WORK_LANE_RECEIPT_BOUND_MONITOR_SETTLED_OBLIGATION
        and contract.get("must_attempt_work") is False
        and contract.get("selection_binding") == "heartbeat_receipt"
    )


def lark_inbox_reply_due_work_lane_contract(
    goal_boundary: dict[str, Any] | None,
    *,
    current_contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Preempt ordinary work when a configured inbox has a direct reply due."""

    if not isinstance(goal_boundary, dict):
        return current_contract
    capabilities = goal_boundary.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    inbox = capabilities.get("lark_event_inbox")
    inbox = inbox if isinstance(inbox, dict) else {}
    urgency = inbox.get("urgency")
    urgency = urgency if isinstance(urgency, dict) else {}
    if urgency.get("reply_due") is not True:
        return current_contract
    return {
        "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
        "lane": "lark_event_inbox",
        "next_lane": (
            str(current_contract.get("lane") or "advancement_task")
            if isinstance(current_contract, dict)
            else "advancement_task"
        ),
        "obligation": WORK_LANE_LARK_INBOX_REPLY_DUE_OBLIGATION,
        "must_attempt_work": True,
        "priority_preemption": True,
        "reason_codes": [
            "lark_inbox_reply_due",
            "direct_question_mention_or_bot_reply",
        ],
        "pending_count": max(0, int(urgency.get("pending_count") or 0)),
        "direct_question_count": max(
            0, int(urgency.get("direct_question_count") or 0)
        ),
        "direct_mention_count": max(
            0, int(urgency.get("direct_mention_count") or 0)
        ),
        "reply_to_bot_count": max(
            0, int(urgency.get("reply_to_bot_count") or 0)
        ),
        "oldest_pending_age_seconds": urgency.get("oldest_pending_age_seconds"),
        "drain_command": inbox.get("drain_command"),
        "semantic_triage_required": True,
        "allowed_dispositions": [
            "steer_current_turn",
            "replan_goal",
            "record_context",
            "continue_current_work",
        ],
        "monitor_policy": "durable_effect_then_one_verified_reply_then_ack",
        "action": (
            "drain and read the configured Lark inbox before ordinary work; classify "
            "the direct question, mention, or verified bot reply as steering, Goal "
            "replan, context capture, or continue-current-work; commit any required "
            "durable effect, then send exactly one "
            "source-thread reply with idempotency and readback, then ACK"
        ),
    }


def operator_inbox_material_review_due_work_lane_contract(
    goal_boundary: dict[str, Any] | None,
    *,
    current_contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Schedule bounded review of captured material that does not require a reply."""

    if not isinstance(goal_boundary, dict):
        return current_contract
    capabilities = goal_boundary.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    inbox = capabilities.get("lark_event_inbox")
    inbox = inbox if isinstance(inbox, dict) else {}
    urgency = inbox.get("urgency")
    urgency = urgency if isinstance(urgency, dict) else {}
    if urgency.get("material_review_due") is not True:
        return current_contract
    drain_limit = max(
        1,
        min(int(urgency.get("material_review_drain_limit") or 20), 100),
    )
    drain_command = str(inbox.get("drain_command") or "").strip()
    if drain_command and " --limit " not in f" {drain_command} ":
        drain_command = f"{drain_command} --limit {drain_limit}"
    return {
        "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
        "lane": "operator_inbox_material_review",
        "next_lane": (
            str(current_contract.get("lane") or "advancement_task")
            if isinstance(current_contract, dict)
            else "advancement_task"
        ),
        "obligation": WORK_LANE_OPERATOR_INBOX_MATERIAL_REVIEW_DUE_OBLIGATION,
        "must_attempt_work": True,
        "priority_preemption": True,
        "reason_codes": [
            "operator_inbox_material_review_due",
            "captured_unaddressed_material",
        ],
        "pending_count": max(0, int(urgency.get("pending_count") or 0)),
        "material_review_count": max(
            0, int(urgency.get("material_review_count") or 0)
        ),
        "material_attachment_count": max(
            0, int(urgency.get("material_attachment_count") or 0)
        ),
        "drain_limit": drain_limit,
        "drain_command": drain_command or None,
        "semantic_triage_required": True,
        "allowed_dispositions": [
            "steer_current_turn",
            "replan_goal",
            "record_context",
            "continue_current_work",
            "no_follow_up",
        ],
        "monitor_policy": "bounded_effect_or_no_follow_up_receipt_then_ack",
        "action": (
            f"drain and read at most {drain_limit} captured operator-inbox materials "
            "before ordinary work; classify each as steering, Goal replan, context "
            "capture, continue-current-work, or no-follow-up; write the matching "
            "durable effect or explicit no-follow-up receipt, settle it idempotently, "
            "and ACK; "
            "do not send a reply unless a separate reply_due item requires one"
        ),
    }


def scoped_user_gate_due_monitor_contract(
    fallback: dict[str, Any] | None,
    *,
    current_contract: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Project a selected monitor fallback as the executable work lane."""

    if not isinstance(fallback, dict):
        return None
    selected = fallback.get("selected_executable")
    if not isinstance(selected, dict):
        return None
    if str(selected.get("task_class") or "") != "continuous_monitor":
        return None
    todo_id = str(selected.get("todo_id") or "").strip()
    if not todo_id:
        return None
    next_lane = (
        "advancement_task"
        if isinstance(current_contract, dict)
        and current_contract.get("lane") == "advancement_task"
        else "continuous_monitor"
    )
    return {
        "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
        "lane": "continuous_monitor",
        "monitor_kind": WORK_LANE_TODO_MONITOR_DUE_KIND,
        "next_lane": next_lane,
        "obligation": "attempt_due_monitor",
        "must_attempt_work": True,
        "reason_codes": ["monitor_due", "scoped_user_gate_fallback"],
        "monitor_policy": (
            "attempt_due_monitor_once_then_writeback_or_no_spend_if_unchanged"
        ),
        "monitor_due_count": 1,
        "monitor_due_items": [_compact_work_lane_todo_item(selected)],
        "selected_todo_id": todo_id,
        "selected_next_due_at": selected.get("next_due_at"),
        "action": (
            "attempt the selected due continuous_monitor todo because the open "
            "user gate blocks only a different action scope"
        ),
    }


def _compact_work_lane_todo_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in WORK_LANE_TODO_ITEM_FIELDS
        if item.get(key) is not None
    }


def _compact_work_lane_todo_items(
    items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    return [
        _compact_work_lane_todo_item(item)
        for item in items[:limit]
        if isinstance(item, dict)
    ]


def build_work_lane_contract(
    *,
    progress_scope: str,
    external_poll_signal: bool,
    todo_counts: dict[str, int],
    monitor_due_count: int,
    due_monitor_items: list[dict[str, Any]],
    watch_only_due_monitor_items: list[dict[str, Any]] | None = None,
    non_watch_only_due_monitor_items: list[dict[str, Any]] | None = None,
    first_advancement: dict[str, Any] | None,
    due_monitor_preempts_advancement: bool,
    outcome_followthrough: dict[str, Any] | None,
    next_action_requires_advancement: bool,
    monitor_due_item_limit: int,
    monitor_schedule_gap_count: int = 0,
    monitor_schedule_gap_items: list[dict[str, Any]] | None = None,
    resume_blocked_by_monitor_count: int = 0,
    resume_blocked_by_monitor_items: list[dict[str, Any]] | None = None,
    monitor_attempt_already_recorded: bool = False,
    monitor_debt_backoff_active: bool = False,
) -> dict[str, Any] | None:
    """Return the work-lane execution contract from precomputed quota facts.

    The helper is pure and deliberately receives quota-local observations
    instead of importing quota internals. This keeps the lane routing policy
    testable while the surrounding quota builder still owns extraction from
    status/project-asset payloads.
    """

    open_count = int(todo_counts.get("open") or 0)
    advancement_count = int(todo_counts.get("advancement") or 0)
    monitor_count = int(todo_counts.get("monitor") or 0)
    has_agent_todos = open_count > 0
    has_advancement_todos = advancement_count > 0
    has_monitor_todos = monitor_count > 0
    monitor_only_schedule = (
        has_agent_todos and has_monitor_todos and not has_advancement_todos
        and todo_counts.get("complete", True) is True
    )
    non_runnable_non_monitor_count = max(0, open_count - monitor_count)
    first_due_monitor = due_monitor_items[0] if due_monitor_items else None
    watch_only_due_items = watch_only_due_monitor_items or []
    ordinary_due_items = (
        non_watch_only_due_monitor_items
        if non_watch_only_due_monitor_items is not None
        else due_monitor_items
    )
    first_preemptive_due_monitor = ordinary_due_items[0] if ordinary_due_items else None
    blocked_by_monitor_items = resume_blocked_by_monitor_items or []
    schedule_gap_items = monitor_schedule_gap_items or []
    first_schedule_gap = schedule_gap_items[0] if schedule_gap_items else None
    monitor_debt_backoff_applies = bool(
        monitor_debt_backoff_active
        and first_preemptive_due_monitor
        and first_advancement
        and todo_priority_rank(first_advancement)
        <= todo_priority_rank(first_preemptive_due_monitor)
    )
    effective_due_monitor_preemption = due_monitor_preempts_advancement and not (
        has_advancement_todos
        and (monitor_attempt_already_recorded or monitor_debt_backoff_applies)
    )

    def due_monitor_contract(
        *, reason_codes: list[str], selected_monitor: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        selected = selected_monitor or first_due_monitor or {}
        selected_todo_id = normalize_todo_id(selected.get("todo_id"))
        projected_due_items = [
            selected,
            *[
                item
                for item in due_monitor_items
                if normalize_todo_id(item.get("todo_id")) != selected_todo_id
            ],
        ]
        return {
            "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
            "lane": "continuous_monitor",
            "monitor_kind": WORK_LANE_TODO_MONITOR_DUE_KIND,
            "next_lane": "advancement_task" if has_advancement_todos else "continuous_monitor",
            "obligation": "attempt_due_monitor",
            "must_attempt_work": True,
            "reason_codes": reason_codes,
            "monitor_policy": "attempt_due_monitor_once_then_writeback_or_no_spend_if_unchanged",
            "monitor_due_count": max(0, int(monitor_due_count)),
            "monitor_due_items": _compact_work_lane_todo_items(
                projected_due_items,
                limit=monitor_due_item_limit,
            ),
            "selected_todo_id": selected.get("todo_id"),
            "selected_next_due_at": selected.get("next_due_at"),
            "action": (
                "attempt the selected due continuous_monitor todo; write back only a "
                "material transition, blocker, or compact reschedule/no-change note"
            ),
        }

    if progress_scope != "dependency_observation":
        if has_advancement_todos and effective_due_monitor_preemption:
            return due_monitor_contract(
                reason_codes=["monitor_due", "due_monitor_priority_preempts_advancement"],
                selected_monitor=first_preemptive_due_monitor,
            )
        if has_advancement_todos and first_advancement is not None:
            reason_codes = ["open_agent_todo"]
            if first_due_monitor:
                reason_codes.append("due_monitor_context")
            if first_due_monitor and monitor_attempt_already_recorded:
                reason_codes.append("monitor_attempt_already_recorded")
            if monitor_debt_backoff_applies:
                reason_codes.append("monitor_debt_backoff")
            if external_poll_signal:
                reason_codes.append("external_monitor_context")
            if outcome_followthrough:
                reason_codes.append("outcome_followthrough_required")
            obligation = (
                "advance_primary_outcome_or_write_blocker"
                if outcome_followthrough
                else "advance_one_bounded_segment"
            )
            action = (
                "advance the first executable agent todo to product-path evidence, "
                "benchmark/case evidence, or a precise blocker; do not spend for "
                "another contract-only preparation layer"
                if outcome_followthrough
                else (
                    "advance the first executable agent todo or write a concrete blocker; "
                    "treat monitor todos as auxiliary observation context"
                )
            )
            contract: dict[str, Any] = {
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "advancement_task",
                "next_lane": "advancement_task",
                "obligation": obligation,
                "must_attempt_work": True,
                "reason_codes": reason_codes,
                "monitor_policy": "material_transition_only",
                "action": action,
            }
            if watch_only_due_items:
                selected_watch_only_monitor = watch_only_due_items[0]
                contract["auxiliary_monitor_poll"] = {
                    "schema_version": "auxiliary_monitor_poll_v0",
                    "required": False,
                    "preempts_advancement": False,
                    "spend_policy": "no_spend",
                    "continuation": "advancement_remains_primary",
                    "monitor_due_count": len(watch_only_due_items),
                    "monitor_due_items": _compact_work_lane_todo_items(
                        watch_only_due_items,
                        limit=monitor_due_item_limit,
                    ),
                    "selected_todo_id": selected_watch_only_monitor.get("todo_id"),
                    "selected_next_due_at": selected_watch_only_monitor.get(
                        "next_due_at"
                    ),
                }
            if outcome_followthrough:
                contract["outcome_followthrough"] = outcome_followthrough
            return contract
        if external_poll_signal:
            contract = {
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "continuous_monitor",
                "monitor_kind": "external_evidence",
                "next_lane": "continuous_monitor",
                "obligation": "observe_external_evidence_or_blocker",
                "must_attempt_work": True,
                "reason_codes": ["external_evidence_poll_signal"],
                "monitor_policy": "read_only_observation_then_no_spend_if_unchanged",
                "action": (
                    "verify the observable external result handle; if it is absent, "
                    "write a compact blocker instead of rerunning launched work"
                ),
            }
            if first_due_monitor:
                contract.update(
                    {
                        "monitor_due_count": max(0, int(monitor_due_count)),
                        "monitor_due_items": _compact_work_lane_todo_items(
                            due_monitor_items,
                            limit=monitor_due_item_limit,
                        ),
                        "selected_todo_id": first_due_monitor.get("todo_id"),
                        "selected_next_due_at": first_due_monitor.get("next_due_at"),
                    }
                )
            return contract
        if resume_blocked_by_monitor_count > 0 and not first_due_monitor:
            selected = blocked_by_monitor_items[0] if blocked_by_monitor_items else {}
            reason_codes = ["resume_blocked_by_open_monitor"]
            if monitor_only_schedule:
                reason_codes.insert(0, "monitor_todo_only")
            return {
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "advancement_task",
                "next_lane": "advancement_task",
                "obligation": "repair_resume_gate_or_close_standing_monitor",
                "must_attempt_work": True,
                "reason_codes": reason_codes,
                "monitor_policy": "material_transition_only",
                "resume_blocked_by_monitor_count": max(0, int(resume_blocked_by_monitor_count)),
                "resume_blocked_by_monitor_items": _compact_work_lane_todo_items(
                    blocked_by_monitor_items,
                    limit=monitor_due_item_limit,
                ),
                "selected_todo_id": selected.get("todo_id"),
                "selected_resume_when": selected.get("resume_when"),
                "action": (
                    "repair the standing monitor dependency: close/supersede the monitor "
                    "after validated evidence, or replan the gated advancement todo with a "
                    "non-blocking monitor contract"
                ),
            }
        if first_due_monitor and todo_counts.get("complete", True) is not True:
            # Incomplete counts cannot certify a monitor-only schedule, but
            # the admitted due item is an exact executable candidate. Do not
            # let an unrelated blocked successor turn it into a quiet wait.
            return due_monitor_contract(
                reason_codes=["monitor_due", "todo_source_incomplete"]
            )
        if monitor_only_schedule:
            if first_due_monitor:
                return due_monitor_contract(
                    reason_codes=["monitor_todo_only", "monitor_due"]
                )
            if next_action_requires_advancement:
                return {
                    "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                    "lane": "advancement_task",
                    "next_lane": "advancement_task",
                    "obligation": "materialize_advancement_todo_or_blocker",
                    "must_attempt_work": True,
                    "reason_codes": [
                        "monitor_todo_only",
                        "next_action_requires_advancement",
                    ],
                    "monitor_policy": "material_transition_only",
                    "action": (
                        "materialize the planning/self-repair advancement todo or write a concrete blocker"
                    ),
                }
            if first_schedule_gap:
                return {
                    "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                    "lane": "advancement_task",
                    "monitor_kind": "todo_monitor_schedule_gap",
                    "next_lane": "continuous_monitor",
                    "obligation": "repair_monitor_schedule_metadata",
                    "must_attempt_work": True,
                    "reason_codes": [
                        "monitor_todo_only",
                        "monitor_schedule_metadata_gap",
                    ],
                    "monitor_policy": "repair_schedule_metadata_before_quiet_wait",
                    "monitor_schedule_gap_count": max(0, int(monitor_schedule_gap_count)),
                    "monitor_schedule_gap_items": _compact_work_lane_todo_items(
                        schedule_gap_items,
                        limit=monitor_due_item_limit,
                    ),
                    "selected_todo_id": first_schedule_gap.get("todo_id"),
                    "action": (
                        "repair the selected continuous_monitor todo by adding cadence/"
                        "next_due_at, superseding it, or recording an explicit no-schedule "
                        "policy; do not silently collapse it into monitor_quiet_skip"
                    ),
                }
            return {
                "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
                "lane": "continuous_monitor",
                "monitor_kind": "todo_monitor",
                "next_lane": "continuous_monitor",
                "obligation": "quiet_until_material_monitor_transition",
                "must_attempt_work": False,
                "reason_codes": [
                    *(
                        ["non_runnable_non_monitor_todos_present"]
                        if non_runnable_non_monitor_count
                        else ["monitor_todo_only"]
                    ),
                ],
                "non_runnable_non_monitor_count": non_runnable_non_monitor_count,
                "monitor_policy": "write_once_per_material_transition_else_no_spend",
                "material_transition": (
                    "a monitor todo may write back only material state transitions, regressions, or concrete blockers"
                ),
                "action": "wait quietly for material monitor evidence",
            }
        return None

    reason_codes = ["dependency_observation"]
    if has_advancement_todos:
        reason_codes.append("open_agent_todo")
        if effective_due_monitor_preemption:
            reason_codes.append("due_monitor_priority_preempts_advancement")
    elif monitor_only_schedule:
        reason_codes.append("monitor_todo_only")
        if first_due_monitor:
            reason_codes.append("monitor_due")
    else:
        reason_codes.append("no_open_agent_todo")
    if effective_due_monitor_preemption:
        return due_monitor_contract(reason_codes=reason_codes)
    return {
        "schema_version": WORK_LANE_CONTRACT_SCHEMA_VERSION,
        "lane": "continuous_monitor",
        "monitor_kind": "dependency_observation",
        "next_lane": "advancement_task" if has_advancement_todos else "continuous_monitor",
        "obligation": (
            "advance_unless_material_monitor_transition"
            if has_advancement_todos
            else "attempt_due_monitor"
            if first_due_monitor
            else "quiet_until_material_monitor_transition"
        ),
        "must_attempt_work": has_advancement_todos or bool(first_due_monitor),
        "reason_codes": reason_codes,
        "monitor_policy": "write_once_per_material_transition_else_no_spend",
        "material_transition": (
            "a dependency-state transition may be written back once when it changes the selected goal decision"
        ),
        "action": (
            "advance the first executable agent todo or write a concrete blocker"
            if has_advancement_todos
            else "wait quietly for new external evidence"
        ),
    }
