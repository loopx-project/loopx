from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .control_plane.projection_envelope_facts import (
    render_projection_envelope_markdown,
    seal_projection_envelope,
    source_fact,
)
from .control_plane.runtime.time import now_utc, now_utc_iso
from .control_plane.todos.decision_scope import todo_gate_relations
from .control_plane.todos.user_gate import open_user_gate_todo_items
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

GLOBAL_GATES_SOURCE_SURFACES = [
    "status attention queue",
    "quota should-run summaries",
    "active-state todo projections",
    "compact run-history projection consumed by status",
]

LANE_PRIORITY = {
    "eligible": 0,
    "normal_run": 0,
    "run": 0,
    "operator_gate": 1,
    "focus_wait": 1,
    "waiting": 2,
    "throttled": 3,
    "paused": 4,
    "quota_unavailable": 5,
}

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


def _first_open_todo(quota_payload: dict[str, Any]) -> dict[str, Any] | None:
    lane = _as_dict(quota_payload.get("agent_lane_next_action"))
    if lane:
        return lane
    summary = _as_dict(quota_payload.get("agent_todo_summary"))
    for key in ("first_open_items", "items", "open_items"):
        for item in _as_list(summary.get(key)):
            if isinstance(item, dict):
                return item
    return None


def _goal_agent_id(item: dict[str, Any], quota_payload: dict[str, Any]) -> str | None:
    lane = _as_dict(quota_payload.get("agent_lane_next_action"))
    if lane.get("agent_id"):
        return str(lane.get("agent_id"))
    agent_identity = _as_dict(quota_payload.get("agent_identity"))
    if agent_identity.get("agent_id"):
        return str(agent_identity.get("agent_id"))
    project_asset = _as_dict(item.get("project_asset"))
    owner = _as_dict(project_asset.get("owner"))
    if owner.get("agent_id"):
        return str(owner.get("agent_id"))
    return None


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


def _quota_unavailable(quota_payload: dict[str, Any]) -> bool:
    return quota_payload.get("state") == "quota_unavailable"


def _global_projection_envelope(
    projection: str,
    status_payload: dict[str, Any],
    *,
    quota_read_at: str,
    quota_evaluated: int,
    quota_unavailable: int,
    shown_count: int,
    available_count: int,
) -> dict[str, Any]:
    """A global read is complete only when it covers every global-registry goal."""
    global_registry = _as_dict(status_payload.get("global_registry"))
    status_envelope = _as_dict(status_payload.get("projection_envelope"))
    status_coverage = _as_dict(status_envelope.get("coverage"))
    excluded = 0
    refs: list[str] = []
    if global_registry.get("available"):
        expected = int(global_registry.get("global_goal_count") or 0)
        excluded = int(global_registry.get("current_registry_excluded_goal_count") or 0)
        refs = [str(ref) for ref in _as_list(global_registry.get("current_registry_excluded_goal_ids"))]
    else:
        expected = int(status_coverage.get("expected_count") or 0)
    return seal_projection_envelope(
        projection=projection,
        observed_at=_now_iso(),
        sources=[
            source_fact(
                "goal_quota",
                last_read_at=quota_read_at,
                item_count=quota_evaluated,
                unreadable_count=quota_unavailable,
            ),
            *([] if status_envelope else [source_fact("status", read_status="not_read")]),
        ],
        coverage={
            "scope": "global",
            "expected_count": expected,
            "included_count": max(0, expected - excluded),
            "omitted": (
                [{"reason": "outside_current_registry", "count": excluded, "refs": refs[:8]}]
                if excluded
                else []
            ),
            "shown_count": shown_count,
            "available_count": available_count,
        },
        upstream=[status_envelope] if status_envelope else [],
    )


def _lane_from_item(
    item: dict[str, Any],
    *,
    quota_payload: dict[str, Any],
) -> dict[str, Any]:
    todo = _first_open_todo(quota_payload)
    rollout_event = _as_dict(quota_payload.get("rollout_event"))
    goal_id = str(item.get("goal_id") or quota_payload.get("goal_id") or "")
    status = str(quota_payload.get("state") or item.get("status") or "unknown")
    waiting_on = str(item.get("waiting_on") or quota_payload.get("waiting_on") or "")
    next_action = (
        quota_payload.get("recommended_action")
        or item.get("recommended_action")
        or _as_dict(item.get("project_asset")).get("next_action")
    )
    lane: dict[str, Any] = {
        "goal_id": goal_id,
        "status": status,
        "waiting_on": waiting_on,
        "agent_id": _goal_agent_id(item, quota_payload),
        "top_todo_id": todo.get("todo_id") if isinstance(todo, dict) else None,
        "last_event_id": rollout_event.get("event_id"),
        "next_safe_action": _redact_text(next_action),
    }
    return {key: value for key, value in lane.items() if value not in (None, "")}


def _gate_from_item(
    item: dict[str, Any],
    *,
    quota_payload: dict[str, Any],
) -> dict[str, Any] | None:
    user_channel = _as_dict(_as_dict(quota_payload.get("interaction_contract")).get("user_channel"))
    user_summary = _as_dict(quota_payload.get("user_todo_summary"))
    open_count = int(user_summary.get("open_count") or user_summary.get("count") or 0)
    if not user_channel.get("action_required") and open_count <= 0:
        return None
    goal_id = str(item.get("goal_id") or quota_payload.get("goal_id") or "")
    first_item: dict[str, Any] | None = None
    for key in ("first_open_items", "items", "open_items"):
        for candidate in _as_list(user_summary.get(key)):
            if isinstance(candidate, dict):
                first_item = candidate
                break
        if first_item:
            break
    question = (
        user_channel.get("question")
        or quota_payload.get("operator_question")
        or quota_payload.get("gate_prompt")
        or (first_item or {}).get("text")
        or item.get("recommended_action")
    )
    blocked_todo = (first_item or {}).get("todo_id")
    return {
        "gate_id": str(quota_payload.get("gate_id") or f"{goal_id}:user-gate"),
        "goal_id": goal_id,
        "owner": "user",
        "blocks": [blocked_todo] if blocked_todo else [goal_id],
        "question": _redact_text(question),
        "next_safe_action": "Wait for the projected user/controller decision before running the blocked path.",
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


def _agent_todo_candidates(quota_payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[object, object, object]] = set()

    def add_candidate(value: object) -> None:
        if not isinstance(value, dict):
            return
        item_key = (value.get("todo_id"), value.get("index"), value.get("text"))
        if item_key in seen:
            return
        seen.add(item_key)
        candidates.append(value)

    add_candidate(quota_payload.get("selected_todo"))
    add_candidate(quota_payload.get("agent_lane_next_action"))
    summary = _as_dict(quota_payload.get("agent_todo_summary"))
    for key in (
        "first_open_items",
        "first_executable_items",
        "items",
        "open_items",
        "backlog_items",
        "executable_backlog_items",
    ):
        for item in _as_list(summary.get(key)):
            add_candidate(item)
    return candidates


def _blocked_refs_for_gate(
    gate_item: dict[str, Any] | None,
    *,
    quota_payload: dict[str, Any],
    goal_id: str,
) -> list[str]:
    if not gate_item:
        return [goal_id]
    exact_todo_id = str(gate_item.get("unblocks_todo_id") or "").strip()
    if exact_todo_id:
        return [exact_todo_id]
    candidates = _agent_todo_candidates(quota_payload)
    for agent_item, relation in zip(candidates, todo_gate_relations([gate_item], candidates)[0], strict=True):
        if not relation or relation.get("state") not in {
            "gate_targets_todo",
            "gate_covers_action",
        }:
            continue
        todo_id = str(agent_item.get("todo_id") or "").strip()
        if todo_id:
            return [todo_id]
    return [goal_id]


def _global_gate_from_item(
    item: dict[str, Any],
    *,
    quota_payload: dict[str, Any],
    agent_id: str | None,
) -> dict[str, Any] | None:
    interaction = _as_dict(quota_payload.get("interaction_contract"))
    user_channel = _as_dict(interaction.get("user_channel"))
    user_summary = _as_dict(quota_payload.get("user_todo_summary"))
    formal_gates = open_user_gate_todo_items(user_summary)
    formal_gate = formal_gates[0] if formal_gates else None
    action_required = user_channel.get("action_required") is True
    waiting_on = str(item.get("waiting_on") or "").strip()
    controller_routed = waiting_on in {"controller", "user_or_controller"} and (
        not agent_id or quota_payload.get("state") == "operator_gate"
    )
    if not action_required and not formal_gate and not controller_routed:
        return None

    owner = "user" if formal_gate or action_required else "controller"
    goal_id = str(item.get("goal_id") or quota_payload.get("goal_id") or "").strip()
    question = (
        user_channel.get("question")
        or item.get("operator_question")
        or (formal_gate or {}).get("text")
        or (formal_gate or {}).get("title")
        or quota_payload.get("recommended_action")
        or item.get("recommended_action")
    )
    gate_id = (
        quota_payload.get("gate_id")
        or (formal_gate or {}).get("todo_id")
        or f"{goal_id}:{owner}-gate"
    )
    return {
        "gate_id": str(gate_id),
        "goal_id": goal_id,
        "owner": owner,
        "waiting_on": waiting_on or owner,
        "blocks": _blocked_refs_for_gate(
            formal_gate,
            quota_payload=quota_payload,
            goal_id=goal_id,
        ),
        "question": _redact_text(question),
        "next_safe_action": (
            "Wait for the projected user/controller decision before "
            "running the blocked path."
        ),
    }


def _collect_global_gate_state(
    status_payload: dict[str, Any],
    *,
    agent_id: str | None,
    limit: int,
) -> dict[str, Any]:
    queue = _as_dict(status_payload.get("attention_queue"))
    queue_items = [
        item for item in _as_list(queue.get("items")) if isinstance(item, dict)
    ]
    gates: list[dict[str, Any]] = []
    lanes: list[dict[str, Any]] = []
    seen_goal_ids: set[str] = set()
    quota_evaluated = quota_unavailable = 0
    for item in queue_items:
        goal_id = str(item.get("goal_id") or "").strip()
        if not goal_id or goal_id in seen_goal_ids:
            continue
        seen_goal_ids.add(goal_id)
        quota_payload = _build_goal_quota(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
        )
        quota_evaluated += 1
        quota_unavailable += int(_quota_unavailable(quota_payload))
        if not _quota_matches_agent_scope(quota_payload, agent_id=agent_id):
            continue
        gate = _global_gate_from_item(
            item,
            quota_payload=quota_payload,
            agent_id=agent_id,
        )
        if not gate:
            continue
        gates.append(gate)
        lane = _lane_from_item(item, quota_payload=quota_payload)
        verified_todo_ids = {
            str(blocked_ref)
            for blocked_ref in _as_list(gate.get("blocks"))
            if str(blocked_ref) != goal_id
        }
        if str(lane.get("top_todo_id") or "") not in verified_todo_ids:
            lane.pop("top_todo_id", None)
        lanes.append(lane)
        if len(gates) >= limit:
            break
    unique_goal_count = len({str(item.get("goal_id") or "").strip() for item in queue_items} - {""})
    return {
        "gates": gates,
        "lanes": lanes,
        "quota_evaluated": quota_evaluated,
        "quota_unavailable": quota_unavailable,
        "scanned_goal_count": len(seen_goal_ids),
        "queue_goal_count": unique_goal_count,
    }


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


def build_global_gates(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    agent_id: str | None,
    limit: int,
) -> dict[str, Any]:
    normalized_limit = max(1, limit)
    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_override,
        scan_roots=scan_roots,
        limit=normalized_limit,
    )
    if status_payload.get("ok") is not True:
        return build_global_gates_error("Global status source unavailable.")
    quota_read_at = _now_iso()
    state = _collect_global_gate_state(
        status_payload,
        agent_id=agent_id,
        limit=normalized_limit,
    )
    envelope = _global_projection_envelope(
        "global_gates",
        status_payload,
        quota_read_at=quota_read_at,
        quota_evaluated=state["quota_evaluated"],
        quota_unavailable=state["quota_unavailable"],
        shown_count=state["scanned_goal_count"],
        available_count=state["queue_goal_count"],
    )
    gates = state["gates"]
    lanes = state["lanes"]
    gate_count = len(gates)
    noun = "gate requires" if gate_count == 1 else "gates require"
    actions = []
    if gates:
        actions = [
            {
                "action_id": "act_read_gate_detail",
                "kind": "read_more",
                "requires_user_approval": False,
                "requires_maintainer_authority": False,
                "preview": (
                    "Run `loopx quota should-run --goal-id <goal>` for one "
                    "blocked lane."
                ),
            },
            {
                "action_id": "act_ask_gate_owner",
                "kind": "ask_user",
                "requires_user_approval": False,
                "requires_maintainer_authority": False,
                "preview": "Surface the projected gate question without changing state.",
            },
        ]
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "request": _global_gates_request(),
        "generated_at": _now_iso(),
        "summary": {
            "headline": f"{gate_count} open user/controller {noun} a decision.",
            "open_gate_count": gate_count,
            "blocked_lane_count": len(lanes),
            "source_surfaces": GLOBAL_GATES_SOURCE_SURFACES,
        },
        "groups": {
            "user_gates": [gate for gate in gates if gate.get("owner") == "user"],
            "controller_gates": [
                gate for gate in gates if gate.get("owner") == "controller"
            ],
            "blocked_work": lanes,
        },
        "gates": gates,
        "lanes": lanes,
        "actions": actions,
        "omissions": [
            (
                "Raw logs, raw transcripts, connector payloads, credential values, "
                "local paths, and private source bodies were intentionally omitted."
            ),
            "Recent progress and unrelated risks are outside this focused command.",
        ],
        "projection_envelope": envelope,
        "boundary": BOUNDARY,
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
        return "# LoopX Global Gates\n\n- ok: `False`\n- error: " + _redact_text(
            payload.get("error")
        )

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


def _todo_from_quota(goal_id: str, quota_payload: dict[str, Any]) -> dict[str, Any] | None:
    todo = _first_open_todo(quota_payload)
    if not isinstance(todo, dict):
        return None
    return {
        "todo_id": todo.get("todo_id"),
        "goal_id": goal_id,
        "role": todo.get("role") or "agent",
        "claimed_by": todo.get("claimed_by"),
        "state": todo.get("status") or "open",
        "priority": todo.get("priority"),
        "title": _redact_text(todo.get("title") or todo.get("text")),
        "next_safe_action": _redact_text(quota_payload.get("recommended_action")),
    }


def _risk_from_finding(finding: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": str(finding.get("kind") or "status_warning"),
        "severity": str(finding.get("severity") or "info"),
        "goal_id": finding.get("goal_id"),
        "evidence_refs": ["status_contract"],
        "next_safe_action": _redact_text(finding.get("recommended_action") or finding.get("message")),
    }


def _recent_progress(history_payload: dict[str, Any], *, since: datetime | None, limit: int) -> list[dict[str, Any]]:
    progress: list[dict[str, Any]] = []
    for run in _as_list(history_payload.get("runs")):
        if not isinstance(run, dict):
            continue
        generated_at = _parse_datetime(run.get("generated_at"))
        if since and generated_at and generated_at < since:
            continue
        progress.append(
            {
                "goal_id": run.get("goal_id"),
                "generated_at": run.get("generated_at"),
                "classification": _redact_text(run.get("classification"), limit=120),
                "recommended_action": _redact_text(run.get("recommended_action"), limit=220),
            }
        )
        if len(progress) >= limit:
            break
    return progress


def _lane_sort_key(lane: dict[str, Any]) -> tuple[int, str]:
    status = str(lane.get("status") or "unknown")
    return (LANE_PRIORITY.get(status, 4), str(lane.get("goal_id") or ""))


def build_summary_all(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    agent_id: str | None,
    time_range: str,
    limit: int,
) -> dict[str, Any]:
    normalized_range, since = _parse_time_range(time_range)
    recent_progress_limit = max(limit * 2, 5)
    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_override,
        scan_roots=scan_roots,
        limit=max(limit, 1),
        recent_run_limit=recent_progress_limit,
    )

    queue = _as_dict(status_payload.get("attention_queue"))
    queue_items = [item for item in _as_list(queue.get("items")) if isinstance(item, dict)]
    lanes: list[dict[str, Any]] = []
    gates: list[dict[str, Any]] = []
    todos: list[dict[str, Any]] = []
    quota_states: dict[str, int] = {}
    seen_goal_ids: set[str] = set()
    quota_unavailable = 0
    quota_read_at = _now_iso()

    for item in queue_items[: max(limit * 4, 40)]:
        goal_id = str(item.get("goal_id") or "").strip()
        if not goal_id or goal_id in seen_goal_ids:
            continue
        seen_goal_ids.add(goal_id)
        quota_payload = _build_goal_quota(status_payload, goal_id=goal_id, agent_id=agent_id)
        quota_unavailable += int(_quota_unavailable(quota_payload))
        lane = _lane_from_item(item, quota_payload=quota_payload)
        if lane:
            lanes.append(lane)
            quota_states[str(lane.get("status") or "unknown")] = quota_states.get(str(lane.get("status") or "unknown"), 0) + 1
        gate = _gate_from_item(item, quota_payload=quota_payload)
        if gate:
            gates.append(gate)
        todo = _todo_from_quota(goal_id, quota_payload)
        if todo:
            todos.append(todo)
    lanes.sort(key=_lane_sort_key)
    envelope = _global_projection_envelope(
        "global_summary",
        status_payload,
        quota_read_at=quota_read_at,
        quota_evaluated=len(seen_goal_ids),
        quota_unavailable=quota_unavailable,
        shown_count=min(len(lanes), limit),
        available_count=len({str(item.get("goal_id") or "").strip() for item in queue_items} - {""}),
    )

    global_registry = _as_dict(status_payload.get("global_registry"))
    risks = [_risk_from_finding(item) for item in _as_list(global_registry.get("findings")) if isinstance(item, dict)]
    run_history = _as_dict(status_payload.get("run_history"))
    recent_progress = _recent_progress(
        {"runs": _as_list(run_history.get("recent_runs"))},
        since=since,
        limit=limit,
    )

    runnable_count = sum(1 for lane in lanes if lane.get("status") in {"eligible", "run", "normal_run"})
    waiting_count = sum(1 for lane in lanes if str(lane.get("waiting_on") or "") in {"controller", "user", "external"})
    headline = (
        f"{len(recent_progress)} recent progress items, {len(gates)} open user/controller gates, "
        f"{len(todos)} runnable or visible todos."
    )
    payload: dict[str, Any] = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "request": {
            "schema_version": "global_manager_command_request_v0",
            "command": COMMAND,
            "legacy_aliases": ["/loop-global-summary"],
            "cli_command": "loopx global-summary",
            "time_range": normalized_range,
            "include": ["progress", "gates", "todos", "risks", "next_actions"],
            "privacy_mode": "public_safe_summary",
            "dry_run": True,
        },
        "generated_at": _now_iso(),
        "summary": {
            "headline": headline,
            "progress_count": len(recent_progress),
            "open_gate_count": len(gates),
            "runnable_todo_count": runnable_count,
            "waiting_lane_count": waiting_count,
            "risk_count": len(risks),
            "source_surfaces": SOURCE_SURFACES,
            "quota_states": quota_states,
        },
        "groups": {
            "user_gates": gates[:limit],
            "runnable_agent_work": [
                lane for lane in lanes if lane.get("status") in {"eligible", "normal_run", "run"}
            ][:limit],
            "waiting_lanes": [
                lane
                for lane in lanes
                if str(lane.get("waiting_on") or "") in {"controller", "user", "external"}
                or lane.get("status") in {"waiting", "focus_wait", "operator_gate"}
            ][:limit],
            "health_risks": risks[:limit],
            "recent_progress": recent_progress,
        },
        "lanes": lanes[:limit],
        "gates": gates[:limit],
        "todos": todos[:limit],
        "risks": risks[:limit],
        "recent_progress": recent_progress,
        "actions": [
            {
                "action_id": "act_read_goal_detail",
                "kind": "read_more",
                "requires_user_approval": False,
                "requires_maintainer_authority": False,
                "preview": "Run `loopx review-packet --goal-id <goal>` or `loopx quota should-run --goal-id <goal>` for one lane.",
            },
            {
                "action_id": "act_ask_user_gate",
                "kind": "ask_user",
                "requires_user_approval": False,
                "requires_maintainer_authority": False,
                "preview": "Surface the projected gate question when open_gate_count is non-zero.",
            },
        ],
        "omissions": [
            "Raw logs, raw transcripts, connector payloads, credential values, local paths, and private source bodies were intentionally omitted.",
            "Status health findings are summarized without filesystem paths.",
        ],
        "projection_envelope": envelope,
        "boundary": BOUNDARY,
    }
    return payload


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
