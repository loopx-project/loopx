from __future__ import annotations

from typing import Any

from ...control_plane.quota.ledger_readback import QUOTA_SLOT_SPENT_CLASSIFICATION
from ...control_plane.quota.settlement import render_settlement_progress_markdown
from ..markdown import append_operator_action_markdown, as_dict


def render_quota_monitor_poll_markdown(payload: dict[str, Any]) -> str:
    if payload.get("ok") is False:
        lines = [
            "# LoopX Quota Monitor Poll",
            "",
            "- ok: `False`",
            f"- mode: `{payload.get('mode') or 'monitor-poll'}`",
            f"- goal_id: `{payload.get('goal_id') or ''}`",
            f"- appended: `{bool(payload.get('appended'))}`",
            f"- registry_mutated: `{bool(payload.get('registry_mutated'))}`",
            f"- agent_id: `{payload.get('agent_id') or ''}`",
            f"- source: `{payload.get('source') or ''}`",
            f"- todo_id: `{payload.get('todo_id') or ''}`",
            f"- target_key: `{payload.get('target_key') or ''}`",
            f"- material_change: `{bool(payload.get('material_change'))}`",
            f"- reason: {payload.get('reason') or 'monitor-poll rejected'}",
        ]
        append_operator_action_markdown(lines, payload)
        return "\n".join(lines)
    event = as_dict(payload.get("monitor_event"))
    before = as_dict(event.get("before"))
    todo_writeback = as_dict(event.get("todo_writeback"))
    monitor_target = as_dict(event.get("monitor_target"))
    lines = [
        "# LoopX Quota Monitor Poll",
        "",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- classification: `{payload.get('classification')}`",
        f"- agent_id: `{payload.get('agent_id') or event.get('agent_id') or ''}`",
        f"- source: `{event.get('source')}`",
        f"- effective_action: `{before.get('effective_action')}`",
        f"- monitor_target: `{monitor_target.get('target_id')}`",
        f"- todo_id: `{event.get('todo_id') or ''}`",
        f"- target_key: `{event.get('target_key') or ''}`",
        f"- material_change: `{event.get('material_change')}`",
        f"- should_run: `{before.get('should_run')}`",
        f"- self_repair_allowed: `{before.get('self_repair_allowed')}`",
        f"- state: `{before.get('state')}`",
        f"- health_check: {payload.get('health_check')}",
        f"- reason: {event.get('reason_summary')}",
    ]
    if todo_writeback:
        lines.append(
            "- todo_writeback: "
            f"dry_run={todo_writeback.get('dry_run')} "
            f"consecutive_no_change={todo_writeback.get('consecutive_no_change')} "
            f"last_checked_at={todo_writeback.get('last_checked_at')} "
            f"next_due_at={todo_writeback.get('next_due_at')}"
        )
    append_operator_action_markdown(lines, payload)
    return "\n".join(lines)


def render_quota_slot_preview_markdown(payload: dict[str, Any]) -> str:
    before = as_dict(payload.get("before"))
    after = as_dict(payload.get("after"))
    before_quota = (
        as_dict(before.get("quota"))
        if isinstance(before.get("quota"), dict)
        else before
    )
    after_quota = (
        as_dict(after.get("quota"))
        if isinstance(after.get("quota"), dict)
        else after
    )
    lines = [
        "# LoopX Quota Slot Preview",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- classification: `{payload.get('classification') or QUOTA_SLOT_SPENT_CLASSIFICATION}`",
        f"- agent_id: `{payload.get('agent_id') or ''}`",
        f"- slots: `{payload.get('slots')}`",
        f"- appended: `{payload.get('appended')}`",
        f"- registry_mutated: `{payload.get('registry_mutated')}`",
        f"- would_throttle: `{payload.get('would_throttle')}`",
    ]
    lines.extend(render_settlement_progress_markdown(payload))
    if payload.get("json_path"):
        lines.append(f"- json_path: `{payload.get('json_path')}`")
    if payload.get("index_path"):
        lines.append(f"- index_path: `{payload.get('index_path')}`")
    if payload.get("reason"):
        lines.append(f"- reason: {payload.get('reason')}")
    if before:
        lines.append(
            "- before: "
            f"state={before.get('state')} "
            f"should_run={before.get('should_run')} "
            f"slots={before_quota.get('spent_slots')}/{before_quota.get('allowed_slots')}"
        )
    if after:
        lines.append(
            "- after: "
            f"state={after.get('state')} "
            f"should_run={after.get('should_run')} "
            f"slots={after_quota.get('spent_slots')}/{after_quota.get('allowed_slots')}"
        )
        summary = as_dict(after.get("plan_summary"))
        if summary:
            lines.append(
                f"- after_plan_next_automatic_turn: {summary.get('next_automatic_turn') or 'none'}"
            )
    accounting_projection = as_dict(payload.get("accounting_projection"))
    if accounting_projection:
        lines.append(
            "- accounting_projection: "
            f"settlement_event={accounting_projection.get('settlement_event_semantics')} "
            f"spent_slots={accounting_projection.get('spent_slots_semantics')} "
            f"before_after={accounting_projection.get('before_after_semantics')} "
            f"window_hours={accounting_projection.get('window_hours')}"
        )
    if payload.get("rolling_window_note"):
        lines.append(f"- rolling_window_note: {payload.get('rolling_window_note')}")
    append_operator_action_markdown(lines, payload)
    return "\n".join(lines)
