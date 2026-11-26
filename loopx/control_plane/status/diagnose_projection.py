"""Pure signal compaction and machine-signal classification for ``loopx diagnose``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _as_mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_items(value: object) -> Sequence[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _compact_text_signals(
    value: object,
    *,
    limit: int = 3,
) -> dict[str, object]:
    items = [text for item in _as_items(value) if (text := str(item).strip())]
    bounded = items[: max(0, limit)]
    return {"items": bounded, "truncated": len(items) > len(bounded), "total_count": len(items)}


def _first_text(summary: Mapping[str, object]) -> str | None:
    for key in ("first_open_items", "items", "backlog_items"):
        for item in _as_items(summary.get(key)):
            if isinstance(item, Mapping) and item.get("text"):
                return str(item.get("text"))
    return str(summary.get("next")) if summary.get("next") else None


def _first_agent_todo_text(
    quota: Mapping[str, object],
    summary: Mapping[str, object],
) -> str | None:
    selected = _as_mapping(quota.get("agent_lane_next_action"))
    if selected.get("text"):
        return str(selected.get("text"))
    if selected.get("title"):
        return str(selected.get("title"))
    for item in _as_items(summary.get("first_executable_items")):
        if isinstance(item, Mapping) and item.get("text"):
            return str(item.get("text"))
    return _first_text(summary)


def _open_count(summary: Mapping[str, object]) -> int:
    for key in ("open_count", "open"):
        value = summary.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return len(_as_items(summary.get("first_open_items")) or _as_items(summary.get("items")))


def _attention_items(status_payload: Mapping[str, object]) -> list[Mapping[str, object]]:
    queue = _as_mapping(status_payload.get("attention_queue"))
    return [item for item in _as_items(queue.get("items")) if isinstance(item, Mapping)]


def _select_attention_item(
    status_payload: Mapping[str, object],
    *,
    goal_id: str | None,
) -> Mapping[str, object] | None:
    items = _attention_items(status_payload)
    if goal_id:
        return next((item for item in items if str(item.get("goal_id") or "") == goal_id), None)
    return items[0] if items else None


def _goal_ids_from_items(items: Sequence[Mapping[str, object]], *, limit: int) -> list[str]:
    ids: list[str] = []
    bounded = max(1, limit)
    for item in items:
        candidate = str(item.get("goal_id") or "").strip()
        if candidate and candidate not in ids:
            ids.append(candidate)
            if len(ids) >= bounded:
                break
    return ids


def _goal_ids_for_packet(
    status_payload: Mapping[str, object],
    *,
    goal_id: str | None,
    limit: int,
) -> list[str]:
    if goal_id:
        return [goal_id]
    return _goal_ids_from_items(_attention_items(status_payload), limit=limit)


def _unregistered_goal_ids(status_payload: Mapping[str, object]) -> set[str]:
    history = _as_mapping(status_payload.get("run_history"))
    return {
        gid
        for goal in _as_items(history.get("goals"))
        if isinstance(goal, Mapping)
        and goal.get("registry_member") is False
        and (gid := str(goal.get("id") or ""))
    }


def _todo_summary(
    quota: Mapping[str, object],
    item: Mapping[str, object],
    *,
    role: str,
) -> Mapping[str, object]:
    return _as_mapping(quota.get(f"{role}_todo_summary")) or _as_mapping(item.get(f"{role}_todos"))


def _interaction_user_required(quota: Mapping[str, object]) -> bool:
    contract = _as_mapping(quota.get("interaction_contract"))
    user_channel = _as_mapping(contract.get("user_channel"))
    if user_channel.get("action_required") is not None:
        return bool(user_channel.get("action_required"))
    return bool(quota.get("requires_user_action") or quota.get("notify_user_on_gate"))


def _quota_user_action_required(quota: Mapping[str, object]) -> bool:
    if quota.get("action_required") is not None:
        return bool(quota.get("action_required"))
    return _interaction_user_required(quota)


def _quota_user_open_count(quota: Mapping[str, object]) -> int:
    value = quota.get("open_count")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _open_count(_as_mapping(quota.get("user_todo_summary")))


def _machine_signal(
    *,
    status_payload: Mapping[str, object],
    item: Mapping[str, object] | None,
    quota: Mapping[str, object],
) -> str:
    if not status_payload.get("ok"):
        return "status_health_attention"
    if item is None:
        return "not_connected_or_not_projected"
    if not quota.get("ok", True):
        return "quota_attention"
    user_summary = _todo_summary(quota, item, role="user")
    if _quota_user_action_required(quota) or _open_count(user_summary) > 0:
        return "user_or_controller_attention"
    for flag, signal in (
        ("capability_repair_allowed", "capability_repair_attention"),
        ("self_repair_allowed", "self_repair_attention"),
        ("recovery_delivery_allowed", "recovery_attention"),
    ):
        if quota.get(flag):
            return signal
    if quota.get("should_run") and quota.get("normal_delivery_allowed", True):
        return "agent_work_attention"
    if quota.get("safe_bypass_allowed"):
        return "safe_bypass_attention"
    if item.get("waiting_on") == "external_evidence":
        return "external_evidence_attention"
    return "no_immediate_agent_delivery_signal"


def _first_user_question(quota: Mapping[str, object], item: Mapping[str, object]) -> str | None:
    for key in ("operator_question", "gate_prompt"):
        if quota.get(key):
            return str(quota.get(key))
        if item.get(key):
            return str(item.get(key))
    return _first_text(_todo_summary(quota, item, role="user"))


def _compact_scheduler_hint(scheduler_hint: Mapping[str, object]) -> dict[str, object]:
    """Summarize scheduler-cadence hints without mutating source dictionaries."""
    if not scheduler_hint:
        return {}
    canonical_app_automation = _as_mapping(scheduler_hint.get("app_automation"))
    app_automation = canonical_app_automation or _as_mapping(scheduler_hint.get("codex_app"))
    app_key = "app_automation" if canonical_app_automation else "codex_app"
    stateful_backoff = _as_mapping(app_automation.get("stateful_backoff"))
    reset_policy = _as_mapping(scheduler_hint.get("reset_policy"))
    unchanged_poll = _as_mapping(scheduler_hint.get("unchanged_poll"))
    reset_key = "app_automation_initial_rrule" if canonical_app_automation else "codex_app_initial_rrule"
    return {
        "schema_version": "diagnose_scheduler_hint_summary_v0",
        "action": scheduler_hint.get("action"),
        "cadence_class": scheduler_hint.get("cadence_class"),
        "reason": scheduler_hint.get("reason"),
        app_key: {
            "apply": app_automation.get("apply"),
            "host_action": app_automation.get("host_action"),
            "recommended_rrule": app_automation.get("recommended_rrule"),
            "recommended_interval_minutes": app_automation.get("recommended_interval_minutes"),
            "current_rrule": stateful_backoff.get("current_rrule"),
            "apply_needed": stateful_backoff.get("apply_needed"),
            "no_spend_for_cadence_change": app_automation.get("no_spend_for_cadence_change"),
        },
        "unchanged_poll": {
            "final_quota_replan_check_enabled": unchanged_poll.get("final_quota_replan_check_enabled"),
            "final_quota_replan_check_action": unchanged_poll.get("final_quota_replan_check_action"),
        },
        "reset_policy": {"reset_token": reset_policy.get("reset_token"), reset_key: reset_policy.get(reset_key)},
    }


def _compact_quota_signals(quota: Mapping[str, object]) -> dict[str, object]:
    keys = (
        "ok", "decision", "should_run", "state", "effective_action", "actionable_by_codex",
        "normal_delivery_allowed", "recovery_delivery_allowed", "capability_repair_allowed",
        "self_repair_allowed", "safe_bypass_allowed", "safe_bypass_kind", "requires_user_action",
        "recommended_action", "reason", "agent_identity", "agent_lane_next_action",
        "agent_scoped_user_gate_override", "agent_scoped_user_action_override",
        "agent_scope_frontier", "goal_frontier_projection", "autonomous_replan_decision",
    )
    signals: dict[str, object] = {key: quota.get(key) for key in keys if key in quota}
    signals["action_required"] = _quota_user_action_required(quota)
    signals["open_count"] = _quota_user_open_count(quota)
    scheduler_hint = _compact_scheduler_hint(_as_mapping(quota.get("scheduler_hint")))
    if scheduler_hint:
        signals["scheduler_hint"] = scheduler_hint
    return signals


def _compact_interaction_contract(contract: Mapping[str, object]) -> dict[str, object]:
    compact: dict[str, object] = {str(key): value for key, value in contract.items()}
    compact["agent_channel"] = {
        str(k): v
        for k, v in _as_mapping(contract.get("agent_channel")).items()
        if k not in {"action_portfolio_ref", "suggested_action_count"}
    }
    compact["cli_channel"] = {
        str(k): v
        for k, v in _as_mapping(contract.get("cli_channel")).items()
        if k not in {"selection_policy_ref", "selection_command"}
    }
    return compact


def _goal_list_view(packet: dict[str, object], *, selected: bool) -> dict[str, object]:
    """Avoid repeating the selected work's source-read plan in the overview."""
    if not selected:
        return packet
    contract = _as_mapping(packet.get("interaction_contract"))
    agent_channel = _as_mapping(contract.get("agent_channel"))
    if not agent_channel.get("required_reads") and not agent_channel.get("work_context"):
        return packet
    overview_contract = {str(key): value for key, value in contract.items()}
    overview_contract["agent_channel"] = {
        str(key): value
        for key, value in agent_channel.items()
        if key not in {"required_reads", "work_context"}
    }
    return {**packet, "interaction_contract": overview_contract}
