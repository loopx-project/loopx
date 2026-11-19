from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .control_plane.effect_runtime import effect_runtime_result
from .control_plane.projection_envelope_facts import (
    render_projection_envelope_markdown,
)
from .control_plane.runtime.time import now_utc, now_utc_iso
from .control_plane.todos.user_gate import open_user_gate_todo_items
from .global_todos import is_unregistered_agent_scope_miss, registered_goal_agents
from .presentation.markdown import as_dict as _as_dict
from .presentation.markdown import as_list as _as_list
from .presentation.public_safety import public_safe_boundary, redact_public_text
from .quota import build_quota_should_run
from .status import collect_status


COMMAND = "/loopx-global-summary"
GLOBAL_GATES_COMMAND = "/loopx-global-gates"
LEGACY_COMMAND_ALIASES = {
    "/loopx-summary-all": COMMAND,
    "/loop-global-summary": COMMAND,
}
SCHEMA_VERSION = "global_manager_command_response_v0"

BOUNDARY = public_safe_boundary()

SOURCE_SURFACES = [
    "global registry compact status",
    "status attention queue",
    "quota should-run summaries",
    "active-state todo projections",
    "run history index summaries",
]


def _now_iso() -> str:
    return now_utc_iso()


def _parse_time_range(value: str) -> tuple[str, datetime | None]:
    normalized = (value or "24h").strip().lower()
    now = now_utc()
    if normalized.endswith("h") and normalized[:-1].isdigit():
        return normalized, now - timedelta(hours=int(normalized[:-1]))
    if normalized.endswith("d") and normalized[:-1].isdigit():
        return normalized, now - timedelta(days=int(normalized[:-1]))
    return "24h", now - timedelta(hours=24)


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _redact_text(value: object, *, limit: int = 260) -> str:
    return redact_public_text(
        value,
        limit=limit,
        replacements=LEGACY_COMMAND_ALIASES,
        truncation_marker="…",
    )


def _build_goal_quota(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    try:
        return build_quota_should_run(status_payload, goal_id=goal_id, agent_id=agent_id)
    except Exception as exc:
        return {
            "ok": False,
            "goal_id": goal_id,
            "state": "quota_unavailable",
            "decision": "quota_unavailable",
            "reason": _redact_text(exc),
        }


def _quota_matches_agent_scope(
    quota_payload: dict[str, Any],
    *,
    agent_id: str | None,
) -> bool:
    if not agent_id:
        return True
    if quota_payload.get("ok") is False:
        return False
    identity = _as_dict(quota_payload.get("agent_identity"))
    return str(identity.get("agent_id") or "") == agent_id


def _build_goal_quotas(
    status_payload: dict[str, Any], *, goal_id: str, agent_id: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Collect complete scoped facts; the TS projection selects the visible lane."""
    payload = _build_goal_quota(status_payload, goal_id=goal_id, agent_id=agent_id)
    if is_unregistered_agent_scope_miss(payload, agent_id=agent_id):
        return [], False
    if agent_id is not None or payload.get("ok") is False:
        return [payload], False
    agents = registered_goal_agents(
        status_payload, goal_id, payload, prefer_queue_recommendation=True,
    )
    if not agents:
        return [payload], False
    return [_build_goal_quota(status_payload, goal_id=goal_id, agent_id=agent)
            for agent in agents], True


def _global_gates_request() -> dict[str, Any]:
    return {
        "schema_version": "global_manager_command_request_v0",
        "command": GLOBAL_GATES_COMMAND,
        "legacy_aliases": ["/loop-global-gates"],
        "cli_command": "loopx global-gates",
        "include": ["gates", "blocked_work", "next_questions"],
        "privacy_mode": "public_safe_summary",
        "dry_run": True,
    }


def build_global_gates_error(error: object) -> dict[str, Any]:
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "request": _global_gates_request(),
        "generated_at": _now_iso(),
        "error": _redact_text(error),
        "omissions": [
            "Raw/private failure details and local paths were intentionally omitted."
        ],
        "boundary": BOUNDARY,
    }


def render_global_gates_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        lines = ["# LoopX Global Gates", "", "- ok: `False`",
                 "- error: " + _redact_text(payload.get("error"))]
        if payload.get("projection_envelope"):
            lines.extend(render_projection_envelope_markdown(payload["projection_envelope"]))
        return "\n".join(lines)

    summary = _as_dict(payload.get("summary"))
    lines = [
        "# LoopX Global Gates",
        "",
        f"- command: `{_as_dict(payload.get('request')).get('command')}`",
        f"- open_gate_count: `{summary.get('open_gate_count')}`",
        *render_projection_envelope_markdown(payload.get("projection_envelope")),
        "",
        "## Gates",
    ]
    gates = [item for item in _as_list(payload.get("gates")) if isinstance(item, dict)]
    if not gates:
        lines.append("- none")
    for gate in gates:
        blocked_refs = ", ".join(str(item) for item in _as_list(gate.get("blocks")))
        lines.append(
            "- "
            f"`{gate.get('goal_id')}` owner=`{gate.get('owner')}` "
            f"waiting_on=`{gate.get('waiting_on')}` blocks=`{blocked_refs}`: "
            f"{gate.get('question')} Next: {gate.get('next_safe_action')}"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "- raw/private material omitted; local paths are not recorded.",
        ]
    )
    return "\n".join(lines)


def render_summary_all_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "# LoopX Global Summary\n\n- ok: `False`\n- error: " + _redact_text(payload.get("error"))

    summary = _as_dict(payload.get("summary"))
    lines = [
        "# LoopX Global Summary",
        "",
        f"- command: `{_as_dict(payload.get('request')).get('command')}`",
        f"- time_range: `{_as_dict(payload.get('request')).get('time_range')}`",
        f"- headline: {summary.get('headline')}",
        f"- counts: progress=`{summary.get('progress_count')}`, gates=`{summary.get('open_gate_count')}`, todos=`{summary.get('runnable_todo_count')}`, risks=`{summary.get('risk_count')}`",
        *render_projection_envelope_markdown(payload.get("projection_envelope")),
        "",
        "## Lanes",
    ]
    for lane in _as_list(payload.get("lanes")):
        if not isinstance(lane, dict):
            continue
        lines.append(
            "- "
            f"`{lane.get('goal_id')}` status=`{lane.get('status')}` "
            f"waiting_on=`{lane.get('waiting_on')}` todo=`{lane.get('top_todo_id')}`: "
            f"{lane.get('next_safe_action')}"
        )
    lines.extend(["", "## Gates"])
    gates = [item for item in _as_list(payload.get("gates")) if isinstance(item, dict)]
    if not gates:
        lines.append("- none")
    for gate in gates:
        lines.append(f"- `{gate.get('goal_id')}` owner=`{gate.get('owner')}`: {gate.get('question')}")
    lines.extend(["", "## Recent Progress"])
    for item in _as_list(payload.get("recent_progress")):
        if not isinstance(item, dict):
            continue
        lines.append(
            "- "
            f"`{item.get('generated_at')}` `{item.get('goal_id')}` "
            f"`{item.get('classification')}`: {item.get('recommended_action')}"
        )
    lines.extend(["", "## Boundary"])
    lines.append("- raw/private material omitted; local paths are not recorded.")
    return "\n".join(lines)


def _read_facts(value: Any, *, progress: bool = False, history: bool = False) -> Any:
    """Preserve Python text and timestamp spellings, without selecting results."""
    if isinstance(value, list):
        return [_read_facts(item, progress=progress, history=history) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _read_facts(item, progress=history and key == "recent_runs", history=history)
              for key, item in value.items()}
    text_keys = {"question", "operator_question", "gate_prompt", "text", "title",
                 "recommended_action", "next_action", "message", "classification"}
    display = {
        key: _redact_text(item, limit=120 if key == "classification" else 260)
        for key, item in value.items() if key in text_keys
    }
    if display:
        result["_display"] = display
    if progress:
        parsed = _parse_datetime(value.get("generated_at"))
        result["_parsed_at"] = int(parsed.timestamp() * 1_000_000) if parsed else None
        result["_naive_time"] = bool(parsed and parsed.tzinfo is None)
        result["_progress_action"] = _redact_text(value.get("recommended_action"), limit=220)
    return result


def _copy_present_keys(
    source: dict[str, Any],
    keys: tuple[str, ...],
) -> dict[str, Any]:
    return {key: source[key] for key in keys if key in source}


def _todo_item_source_facts(todo: dict[str, Any]) -> dict[str, Any]:
    projected = _copy_present_keys(
        todo,
        (
            "todo_id",
            "index",
            "title",
            "text",
            "role",
            "claimed_by",
            "agent_id",
            "status",
            "priority",
            "unblocks_todo_id",
            "decision_scope",
            "required_decision_scopes",
        ),
    )
    if todo and not projected:
        projected["_present"] = True
    return projected


def _item_source_facts(item: dict[str, Any]) -> dict[str, Any]:
    projected = _copy_present_keys(
        item,
        (
            "goal_id",
            "status",
            "waiting_on",
            "recommended_action",
            "operator_question",
        ),
    )
    raw_asset = item.get("project_asset")
    if isinstance(raw_asset, dict):
        asset = _copy_present_keys(raw_asset, ("next_action",))
        raw_owner = raw_asset.get("owner")
        if isinstance(raw_owner, dict):
            owner = _copy_present_keys(raw_owner, ("agent_id",))
            if owner:
                asset["owner"] = owner
        if asset:
            projected["project_asset"] = asset
    return projected


def _quota_source_facts(quota: dict[str, Any]) -> dict[str, Any]:
    projected = _copy_present_keys(
        quota,
        (
            "ok",
            "goal_id",
            "state",
            "waiting_on",
            "source",
            "status",
            "gate_id",
            "normal_delivery_allowed",
            "recommended_action",
            "operator_question",
            "gate_prompt",
        ),
    )
    raw_identity = quota.get("agent_identity")
    if isinstance(raw_identity, dict):
        identity = _copy_present_keys(raw_identity, ("agent_id",))
        if identity:
            projected["agent_identity"] = identity
    raw_rollout = quota.get("rollout_event")
    if isinstance(raw_rollout, dict):
        rollout = _copy_present_keys(raw_rollout, ("event_id",))
        if rollout:
            projected["rollout_event"] = rollout
    for todo_key in ("selected_todo", "agent_lane_next_action"):
        raw_todo = quota.get(todo_key)
        if isinstance(raw_todo, dict):
            projected[todo_key] = _todo_item_source_facts(raw_todo)
    raw_agent_summary = quota.get("agent_todo_summary")
    if isinstance(raw_agent_summary, dict):
        agent_summary: dict[str, Any] = {}
        for list_key in (
            "first_open_items",
            "first_executable_items",
            "items",
            "open_items",
            "backlog_items",
            "executable_backlog_items",
        ):
            raw_rows = raw_agent_summary.get(list_key)
            if isinstance(raw_rows, list):
                agent_summary[list_key] = [
                    _todo_item_source_facts(row)
                    for row in raw_rows
                    if isinstance(row, dict)
                ]
        if agent_summary:
            projected["agent_todo_summary"] = agent_summary
    raw_user_summary = quota.get("user_todo_summary")
    if isinstance(raw_user_summary, dict):
        user_summary = _copy_present_keys(raw_user_summary, ("open_count", "count"))
        raw_scope_filter = raw_user_summary.get("agent_scope_filter")
        if isinstance(raw_scope_filter, dict):
            scope_filter = _copy_present_keys(
                raw_scope_filter,
                (
                    "agent_id",
                    "current_agent_blocking_open_count",
                    "other_agent_scoped_open_count",
                ),
            )
            if scope_filter:
                user_summary["agent_scope_filter"] = scope_filter
        for list_key in ("first_open_items", "items", "open_items"):
            raw_rows = raw_user_summary.get(list_key)
            if isinstance(raw_rows, list):
                user_summary[list_key] = [
                    _todo_item_source_facts(row)
                    for row in raw_rows
                    if isinstance(row, dict)
                ]
        if user_summary:
            projected["user_todo_summary"] = user_summary
    raw_contract = quota.get("interaction_contract")
    if isinstance(raw_contract, dict):
        raw_channel = raw_contract.get("user_channel")
        if isinstance(raw_channel, dict):
            channel = _copy_present_keys(raw_channel, ("action_required", "question"))
            if channel:
                projected["interaction_contract"] = {"user_channel": channel}
    return projected


def _status_source_facts(
    status: dict[str, Any],
    *,
    include_history: bool,
) -> dict[str, Any]:
    projected = _copy_present_keys(status, ("ok",))
    raw_envelope = status.get("projection_envelope")
    if isinstance(raw_envelope, dict) and raw_envelope:
        projected["projection_envelope"] = raw_envelope
    raw_registry = status.get("global_registry")
    if isinstance(raw_registry, dict):
        registry = _copy_present_keys(
            raw_registry,
            (
                "available",
                "global_goal_count",
                "current_registry_excluded_goal_count",
                "current_registry_excluded_goal_ids",
            ),
        )
        if include_history:
            raw_findings = raw_registry.get("findings")
            if isinstance(raw_findings, list):
                registry["findings"] = [
                    _copy_present_keys(
                        row,
                        ("kind", "severity", "goal_id", "recommended_action", "message"),
                    )
                    for row in raw_findings
                    if isinstance(row, dict)
                ]
        if registry:
            projected["global_registry"] = registry
    raw_queue = status.get("attention_queue")
    if isinstance(raw_queue, dict):
        raw_items = raw_queue.get("items")
        if isinstance(raw_items, list):
            projected["attention_queue"] = {
                "items": [
                    _copy_present_keys(row, ("goal_id",))
                    for row in raw_items
                    if isinstance(row, dict)
                ]
            }
    if include_history:
        raw_history = status.get("run_history")
        if isinstance(raw_history, dict):
            raw_runs = raw_history.get("recent_runs")
            if isinstance(raw_runs, list):
                projected["run_history"] = {
                    "recent_runs": [
                        _copy_present_keys(
                            row,
                            (
                                "goal_id",
                                "generated_at",
                                "classification",
                                "recommended_action",
                            ),
                        )
                        for row in raw_runs
                        if isinstance(row, dict)
                    ]
                }
    return projected


def _gate_source_facts(item: dict[str, Any], quota: dict[str, Any],
                       agent_id: str | None) -> dict[str, Any]:
    formal_gates = open_user_gate_todo_items(_as_dict(quota.get("user_todo_summary")))
    formal_gate = _todo_item_source_facts(formal_gates[0]) if formal_gates else None
    channel = _as_dict(_as_dict(quota.get("interaction_contract")).get("user_channel"))
    routed = str(item.get("waiting_on") or "").strip() in {"controller", "user_or_controller"}
    return {
        "formal_gate": formal_gate,
        "eligible": (channel.get("action_required") is True or formal_gate is not None
                     or routed and (not agent_id or quota.get("state") == "operator_gate")),
    }


def _read_samples(status: dict[str, Any], *, agent_id: str | None,
                  limit: int, gates_only: bool) -> list[dict[str, Any]]:
    queue = [item for item in _as_list(_as_dict(status.get("attention_queue")).get("items"))
             if isinstance(item, dict)]
    samples = []
    seen = set()
    found = 0
    for item in queue if gates_only else queue[:max(limit * 4, 40)]:
        goal_id = str(item.get("goal_id") or "").strip()
        if not goal_id or goal_id in seen:
            continue
        seen.add(goal_id)
        quotas, scoped_reads = _build_goal_quotas(status, goal_id=goal_id, agent_id=agent_id)
        projected_item = _read_facts(_item_source_facts(item))
        for quota in quotas:
            sample = {
                "item": projected_item,
                "quota": _read_facts(_quota_source_facts(quota)),
            }
            scoped_agent = (_as_dict(quota.get("agent_identity")).get("agent_id")
                            if scoped_reads else agent_id)
            facts = (_gate_source_facts(item, quota, scoped_agent)
                     if gates_only or scoped_reads else None)
            if facts is not None:
                sample["gate_source_facts"] = _read_facts(facts)
            samples.append(sample)
            if not gates_only or not _quota_matches_agent_scope(quota, agent_id=agent_id):
                continue
            if facts is not None and facts["eligible"]:
                found += 1
        if gates_only and found >= limit:
            break
    return samples


def build_summary_all(*, registry_path: Path, runtime_root_override: str | None,
                      agent_id: str | None, time_range: str, limit: int) -> dict[str, Any]:
    normalized_range, since = _parse_time_range(time_range)
    status = collect_status(registry_path=registry_path,
                            runtime_root_override=runtime_root_override,
                            limit=max(limit, 1), recent_run_limit=max(limit * 2, 5))
    read_at = _now_iso()
    samples = _read_samples(status, agent_id=agent_id, limit=limit, gates_only=False)
    return effect_runtime_result("presentation.global_summary.project", {
        "schema_version": "global_manager_read_request_v0",
        "status": _read_facts(_status_source_facts(status, include_history=True), history=True),
        "samples": samples, "agent_id": agent_id, "limit": limit,
        "source_surfaces": SOURCE_SURFACES, "time_range": normalized_range,
        "since": int(since.timestamp() * 1_000_000) if since else None,
        "quota_read_at": read_at, "observed_at": _now_iso(), "generated_at": _now_iso(),
        "boundary": BOUNDARY,
    }, large_local_snapshot=True)


def build_global_gates(*, registry_path: Path, runtime_root_override: str | None,
                       agent_id: str | None, limit: int) -> dict[str, Any]:
    normalized_limit = max(1, limit)
    status = collect_status(registry_path=registry_path,
                            runtime_root_override=runtime_root_override, limit=normalized_limit)
    healthy = status.get("ok") is True
    # The unhealthy path previously generated the error before sealing its envelope.
    error_at = _now_iso() if not healthy else None
    read_at = _now_iso() if healthy else None
    samples = (_read_samples(status, agent_id=agent_id, limit=normalized_limit, gates_only=True)
               if healthy else [])
    return effect_runtime_result("presentation.global_gates.project", {
        "schema_version": "global_manager_read_request_v0",
        "status": _read_facts(_status_source_facts(status, include_history=False)),
        "samples": samples, "agent_id": agent_id, "limit": normalized_limit,
        "quota_read_at": read_at, "observed_at": _now_iso(),
        "generated_at": error_at or _now_iso(), "boundary": BOUNDARY,
    }, large_local_snapshot=True)
