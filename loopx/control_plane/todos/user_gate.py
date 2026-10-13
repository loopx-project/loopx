from __future__ import annotations

from typing import Any

from ..quota.effective_action import EffectiveAction
from .contract import TODO_TASK_CLASS_USER_ACTION, TODO_TASK_CLASS_USER_GATE
from .todo_semantics import todo_item_task_class

USER_GATE_ACTION_KIND_HINTS = (
    "approval",
    "approve",
    "boundary",
    "gate",
    "blocker",
    "credential",
    "private",
    "production",
    "leaderboard",
    "submission",
    "public_claim",
)


def open_todo_count(summary: dict[str, Any] | None) -> int:
    if not isinstance(summary, dict):
        return 0
    try:
        return max(0, int(summary.get("open_count") or 0))
    except (TypeError, ValueError):
        return 0


def is_user_gate_todo_item(item: dict[str, Any]) -> bool:
    explicit_task_class = str(item.get("task_class") or "").strip()
    if explicit_task_class:
        return todo_item_task_class(item) == TODO_TASK_CLASS_USER_GATE
    if todo_item_task_class(item) == TODO_TASK_CLASS_USER_GATE:
        return True
    action_kind = str(item.get("action_kind") or "").strip().lower()
    if not action_kind:
        return False
    return any(hint in action_kind for hint in USER_GATE_ACTION_KIND_HINTS)


def open_user_gate_todo_items(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(summary, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for key in ("gate_open_items", "first_open_items", "items", "backlog_items"):
        values = summary.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or item.get("done") is True:
                continue
            if not is_user_gate_todo_item(item):
                continue
            item_key = (item.get("todo_id"), item.get("index"), item.get("text"))
            if any(
                (existing.get("todo_id"), existing.get("index"), existing.get("text"))
                == item_key
                for existing in candidates
            ):
                continue
            candidates.append(item)
    return candidates


def has_open_user_gate_todo(summary: dict[str, Any] | None) -> bool:
    return bool(open_user_gate_todo_items(summary))


def has_only_open_user_actions(summary: dict[str, Any] | None) -> bool:
    """Return whether every open user Todo is an explicit non-gating action."""

    if not isinstance(summary, dict) or has_open_user_gate_todo(summary):
        return False
    # ``open_count`` is already the complete Todo frontier for the current
    # Agent after quota scope filtering.  ``all_open_count`` remains a
    # Goal-wide diagnostic and must not be compared with Agent-scoped action
    # rows.
    expected_count = open_todo_count(summary)
    if expected_count <= 0:
        return False
    try:
        if int(summary.get("user_action_open_count") or 0) == expected_count:
            return True
    except (TypeError, ValueError):
        pass

    # Small or legacy summaries may not project the aggregate count.  Only
    # infer the same fact when the visible items account for the full frontier;
    # compacted partial lists must remain fail-closed.
    visible: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for key in ("first_open_items", "items", "backlog_items"):
        rows = summary.get(key)
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict) or item.get("done") is True:
                continue
            identity = (item.get("todo_id"), item.get("index"), item.get("text"))
            visible[identity] = item
    return len(visible) == expected_count and all(
        todo_item_task_class(item) == TODO_TASK_CLASS_USER_ACTION
        for item in visible.values()
    )


def user_gate_todo_notify_reason(summary: dict[str, Any] | None) -> str:
    items = open_user_gate_todo_items(summary)
    if not items:
        return "open user todo can resolve the current gate"
    first = items[0]
    action_kind = str(first.get("action_kind") or "").strip()
    if action_kind:
        return f"open user_gate todo requires owner decision before {action_kind}"
    return "open user_gate todo requires owner decision before agent execution"


def should_notify_user_on_open_todo(
    *,
    state: str,
    waiting_on: str,
    user_todo_summary: dict[str, Any] | None,
) -> bool:
    if state == "operator_gate":
        return False
    if open_todo_count(user_todo_summary) <= 0:
        return False
    if has_only_open_user_actions(user_todo_summary):
        return False
    if state in {"focus_wait", "waiting"}:
        return True
    return waiting_on in {"user_or_controller", "controller", "external_evidence"}


def open_todo_notify_reason(*, state: str, waiting_on: str) -> str:
    if state == "focus_wait":
        return "open user todo can unblock focus_wait after owner evidence, external eval, or a clean baseline changes"
    if waiting_on == "external_evidence":
        return "open user todo can provide or defer the external-evidence checkpoint"
    if waiting_on in {"user_or_controller", "controller"}:
        return "open user todo can resolve the user/controller blocker"
    return "open user todo can resolve the current waiting lane"


def build_user_todo_notification(
    summary: dict[str, Any] | None,
    *,
    state: str,
    waiting_on: str,
    repeat_notification_required: bool = False,
    repeat_notification_reason: Any = None,
) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    if has_open_user_gate_todo(summary):
        return {
            "notify_user_on_gate": True,
            "open_todo_notify_reason": user_gate_todo_notify_reason(summary),
            "open_todo_notification_policy": "repeat_until_resolved",
        }
    if not repeat_notification_required and not should_notify_user_on_open_todo(
        state=state,
        waiting_on=waiting_on,
        user_todo_summary=summary,
    ):
        return {}
    reason = open_todo_notify_reason(state=state, waiting_on=waiting_on)
    if repeat_notification_required:
        reason = (
            repeat_notification_reason
            or "no-work polling should ask the current open user todo"
        )
    return {
        "notify_user_on_open_todo": True,
        "open_todo_notify_reason": reason,
        **(
            {"open_todo_notification_policy": "repeat_until_resolved"}
            if repeat_notification_required
            else {}
        ),
    }


def scoped_user_gate_fallback_fields() -> dict[str, Any]:
    """Project the existing scoped-fallback readback independently of execution."""
    return {
        "safe_bypass_allowed": True,
        "safe_bypass_kind": "scoped_user_gate_fallback",
        "safe_bypass_policy": (
            "The user gate blocks only the matched agent action scope. Surface "
            "that gate, then advance the selected non-gated fallback; spend only "
            "after validated writeback."
        ),
    }


def apply_scoped_user_gate_fallback_projection(
    payload: dict[str, Any],
    *,
    fallback: dict[str, Any] | None,
    replan_decision_allowed: bool,
) -> dict[str, Any]:
    if not fallback or replan_decision_allowed:
        return payload

    projected = dict(payload)
    projected["scoped_user_gate_fallback"] = fallback
    projected["should_run"] = True
    if projected.get("decision") == "skip":
        projected["decision"] = "safe_bypass_user_gate_fallback"
    if projected.get("effective_action") in {EffectiveAction.QUOTA_SKIP.value, EffectiveAction.MONITOR_QUIET_SKIP.value, None}:
        projected["effective_action"] = EffectiveAction.SCOPED_USER_GATE_FALLBACK.value

    raw_execution_obligation = projected.get("execution_obligation")
    execution_obligation = (
        dict(raw_execution_obligation)
        if isinstance(raw_execution_obligation, dict)
        else {}
    )
    execution_obligation.update(
        {
            "must_attempt_work": True,
            "kind": "scoped_user_gate_fallback",
            "minimum": "one_non_gated_fallback_segment_after_user_gate_notice",
            "delivery_allowed": True,
            "notify_is_execution_gate": False,
            "contract": "scoped_user_gate_fallback",
            "contract_obligation": fallback.get("recommended_action"),
            "reason": fallback.get("reason"),
        }
    )
    projected["execution_obligation"] = execution_obligation
    projected.update(scoped_user_gate_fallback_fields())
    projected["actionable_by_codex"] = True
    return projected


def build_gate_prompt(
    item: dict[str, Any],
    *,
    user_todo_summary: dict[str, Any] | None = None,
) -> str | None:
    question = str(item.get("operator_question") or "").strip()
    recommended_action = str(item.get("recommended_action") or "").strip()
    next_handoff_condition = str(item.get("next_handoff_condition") or "").strip()
    raw_missing_gates = item.get("missing_gates")
    missing_gates = [
        str(gate).strip()
        for gate in (raw_missing_gates if isinstance(raw_missing_gates, list) else [])
        if str(gate).strip()
    ]
    if user_todo_summary is None:
        from .quota_summary import summarize_user_todos_for_quota

        user_todo_summary = summarize_user_todos_for_quota(item.get("user_todos"))
    first_open = (
        user_todo_summary.get("first_open_items")
        if isinstance(user_todo_summary, dict)
        and isinstance(user_todo_summary.get("first_open_items"), list)
        else []
    )
    other_agent_scoped_items = (
        user_todo_summary.get("other_agent_scoped_items")
        if isinstance(user_todo_summary, dict)
        and isinstance(user_todo_summary.get("other_agent_scoped_items"), list)
        else []
    )

    if not any(
        [
            question,
            recommended_action,
            next_handoff_condition,
            missing_gates,
            first_open,
            other_agent_scoped_items,
        ]
    ):
        return None

    lines = ["请用户/控制器确认当前 gate："]
    if question:
        lines.append(f"- 问题：{question}")
    if recommended_action:
        lines.append(f"- 当前建议：{recommended_action}")
    if next_handoff_condition:
        lines.append(f"- 放行条件：{next_handoff_condition}")
    if missing_gates:
        lines.append(f"- 缺失 gate：{', '.join(missing_gates)}")
    if isinstance(user_todo_summary, dict) and first_open:
        open_count = user_todo_summary.get("open_count")
        lines.append(f"- 用户待办：{open_count} 项未完成，优先确认：")
        for todo in first_open:
            index = todo.get("index")
            prefix = f"  {index}. " if index is not None else "  - "
            lines.append(f"{prefix}{todo.get('text')}")
    elif isinstance(user_todo_summary, dict) and other_agent_scoped_items:
        scoped_count = user_todo_summary.get("other_agent_scoped_open_count")
        all_open_count = user_todo_summary.get("all_open_count")
        count = scoped_count if scoped_count is not None else len(other_agent_scoped_items)
        suffix = f"（全局共 {all_open_count} 项）" if all_open_count is not None else ""
        lines.append(f"- 当前 agent 无阻塞用户待办；其他 agent/global 用户待办：{count} 项{suffix}，优先确认：")
        for todo in other_agent_scoped_items[:3]:
            index = todo.get("index")
            prefix = f"  {index}. " if index is not None else "  - "
            lines.append(f"{prefix}{todo.get('text')}")
    lines.append("- 建议回复格式：同意 / 不同意 / 已完成 / 仍待确认 + 一句话原因。")
    return "\n".join(lines)
