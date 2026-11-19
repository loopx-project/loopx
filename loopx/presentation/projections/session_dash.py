"""Build the public-safe session dash projection from the status contract.

This module is presentation-only. It consumes the same public-safe inputs the
dashboard already reads (status attention items, run history goal blocks, todo
index, agent management projection, usage summary) and folds them into one
compact fleet snapshot for the no-dependency single-page renderer.

The projection is organised the way an operator thinks about the system:
sessions (agent runtimes) on top, the goals each session owns underneath, and
for every goal only the progress signal that matters to a human: status,
waiting reason, todo progress, and the latest run evidence. Internal control
machinery (decision frames, work-lane contracts, quota slot math, truth
contracts, source warnings) is intentionally not projected into the page.

It never reads raw transcripts, logs, credentials, or local paths. Raw-looking
keys found in inputs are recorded as boundary warnings without copying values,
mirroring ``goal_channel_projection`` behavior.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

SESSION_DASH_PROJECTION_SCHEMA_VERSION = "session_dash_projection_v1"

_DONE_STATUS_MARKERS = (
    "done",
    "complete",
    "completed",
    "closed",
    "success",
    "shipped",
    "archived",
)
_BLOCKED_STATUS_MARKERS = (
    "blocked",
    "stalled",
    "stuck",
    "failed",
    "waiting_user_gate",
)
_ACTIVE_STATUS_MARKERS = (
    "active",
    "progress",
    "running",
    "in_progress",
    "keepalive",
    "relaunched",
)
# ``controller`` is a human gate in the runtime contract: the attention
# emitters write ``waiting_on="controller"`` (see ``session_runtime``) while
# ``acceptance_observation`` and ``project_agent_context`` read
# ``{"controller", "user_or_controller"}`` as the human-gate set. Leaving it out
# dropped those goals into ``other`` instead of ``needs_user``.
_USER_WAITING_MARKERS = (
    "user",
    "controller",
    "operator",
    "human",
    "owner",
    "user_gate",
)
# A status that names the user is waiting on them, the same way a ``waiting_on``
# that names a human is.
_USER_STATUS_MARKERS = ("user",)


# Statuses are free-form: goal registries, run classifiers, and attention
# emitters each write their own words, so the bucket fold has to read them as
# words instead of raw substrings. Otherwise ``installed`` reads as ``stalled``
# and ``incomplete`` reads as ``complete`` -- the prose-substring defect class
# reported in #4336, where a healthy ``installed`` goal was folded into a
# stalled one.
_MARKER_BOUNDARY_TEMPLATE = r"(?<![a-z0-9]){marker}(?![a-z0-9])"
_NEGATED_STATUS_PREFIXES = ("not_", "not-", "no_", "no-", "never_")


def _has_status_marker(text: str, markers: tuple[str, ...]) -> bool:
    """True when one of ``markers`` appears in ``text`` as a whole word."""

    return any(
        re.search(_MARKER_BOUNDARY_TEMPLATE.format(marker=re.escape(marker)), text)
        for marker in markers
    )


def _is_negated_status(status_text: str) -> bool:
    """True for a status that negates the word it carries (``not_completed``)."""

    return status_text.startswith(_NEGATED_STATUS_PREFIXES)


def _as_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_mappings(values: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if not values:
        return []
    return [dict(item) for item in values if isinstance(item, Mapping)]


def _text(value: Any, *, limit: int = 180) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _first_text(item: Mapping[str, Any], keys: Sequence[str], *, limit: int = 180) -> str | None:
    for key in keys:
        value = _text(item.get(key), limit=limit)
        if value:
            return value
    return None


def _status_bucket(*, status: str | None, waiting_on: str | None) -> str:
    """Fold goal status + waiting reason into one human-readable bucket.

    Buckets: ``active`` (agent is working), ``needs_user`` (waiting on a
    human), ``blocked`` (stuck on a gate/blocker), ``done`` (finished),
    ``other`` (not enough signal to classify).

    Markers are matched as whole words, so a status that merely contains a
    marker's letters keeps its own bucket, and a negated status
    (``not_completed``) is not a done signal. ``waiting_on`` of ``controller``
    is a human gate, matching the set the runtime emitters and
    ``acceptance_observation`` already agree on.
    """

    status_text = (status or "").lower()
    waiting = (waiting_on or "").lower()
    done_markers = () if _is_negated_status(status_text) else _DONE_STATUS_MARKERS
    if _has_status_marker(status_text, done_markers):
        return "done"
    if _has_status_marker(status_text, _BLOCKED_STATUS_MARKERS):
        return "blocked"
    if _has_status_marker(waiting, _USER_WAITING_MARKERS) or _has_status_marker(
        status_text, _USER_STATUS_MARKERS
    ):
        return "needs_user"
    if _has_status_marker(status_text, _ACTIVE_STATUS_MARKERS):
        return "active"
    return "other"


def _todo_tally(
    todo_items: Sequence[Mapping[str, Any]],
    *,
    goal_id: str,
) -> dict[str, int]:
    """Count open agent/user todos and finished todos for one goal."""

    open_agent = 0
    open_user = 0
    done = 0
    for item in todo_items:
        if str(item.get("goal_id") or "") != goal_id:
            continue
        status = str(item.get("status") or "").strip().lower()
        done_flag = bool(item.get("done"))
        if done_flag or status in _DONE_STATUS_MARKERS:
            done += 1
            continue
        role = str(item.get("role") or "agent").strip().lower()
        if role == "user":
            open_user += 1
        else:
            open_agent += 1
    return {
        "open_agent_todos": open_agent,
        "open_user_todos": open_user,
        "done_todos": done,
    }


def _goal_entry(
    *,
    goal_id: str,
    status_item: Mapping[str, Any],
    run_goal: Mapping[str, Any],
    todo_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project one goal into the compact operator-facing shape."""

    status = (
        _text(status_item.get("status"))
        or _text(run_goal.get("status"))
        or "unknown"
    )
    waiting_on = (
        _text(status_item.get("waiting_on"))
        or _text(run_goal.get("waiting_on"))
        or "auto"
    )
    # ``codex`` is LoopX's classification for an agent-actionable lane; surface
    # it with the host-neutral ``agent`` word so the panel reads the same for
    # Codex, Pi, or any other agent host. Both words classify identically in
    # the bucket logic below.
    if waiting_on == "codex":
        waiting_on = "agent"
    display_name = (
        _text(status_item.get("display_name"), limit=120)
        or _text(run_goal.get("display_name"), limit=120)
        or str(goal_id)
    )
    domain = _text(run_goal.get("domain"), limit=120)

    latest_runs = _as_mappings(run_goal.get("latest_runs"))
    latest_run = latest_runs[0] if latest_runs else None
    latest_run_at = _text(latest_run.get("generated_at"), limit=80) if latest_run else None
    latest_classification = (
        _text(latest_run.get("classification"), limit=120) if latest_run else None
    )
    next_action = (
        _text(status_item.get("recommended_action"), limit=220)
        or (
            _text(latest_run.get("recommended_action"), limit=220)
            if latest_run
            else None
        )
        or None
    )

    tally = _todo_tally(todo_items, goal_id=goal_id)
    return {
        "goal_id": str(goal_id),
        "display_name": display_name,
        "domain": domain,
        "status": status,
        "status_bucket": _status_bucket(status=status, waiting_on=waiting_on),
        "lifecycle_phase": _text(run_goal.get("lifecycle_phase"), limit=64),
        "waiting_on": waiting_on,
        "open_agent_todos": tally["open_agent_todos"],
        "open_user_todos": tally["open_user_todos"],
        "done_todos": tally["done_todos"],
        "latest_run_at": latest_run_at,
        "latest_classification": latest_classification,
        "next_action": next_action,
    }


def build_session_dash_projection(
    *,
    status_payload: Mapping[str, Any] | None = None,
    run_history: Mapping[str, Any] | None = None,
    todo_index: Mapping[str, Any] | None = None,
    agent_management: Mapping[str, Any] | None = None,
    usage_summary: Mapping[str, Any] | None = None,
    focus_goal_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a read-only fleet snapshot: sessions -> goals -> status.

    The returned object is a presentation projection, not a new source of
    truth. It groups goals under the agent sessions that own them and keeps
    only the progress/result signal an operator can act on. Raw/private
    looking inputs are recorded as warnings without copying their values.

    ``focus_goal_id`` narrows the snapshot to sessions that contain that goal;
    when omitted the whole fleet is projected.
    """

    status_map = _as_mapping(status_payload)
    history = _as_mapping(run_history)
    todo_map = _as_mapping(todo_index)
    agent_map = _as_mapping(agent_management)
    usage_map = _as_mapping(usage_summary)

    attention_items = _as_mappings(_as_mapping(status_map.get("attention_queue")).get("items"))
    status_by_goal = {str(item.get("goal_id") or ""): item for item in attention_items}

    goals = _as_mappings(history.get("goals"))
    goals_by_id = {str(goal.get("id") or ""): goal for goal in goals if goal.get("id")}

    todo_items = _as_mappings(todo_map.get("items"))

    # Sessions come from the agent-management projection (each agent runtime is
    # one session); goals are attached by the session's declared goal_ids.
    sessions: list[dict[str, Any]] = []
    assigned_goal_ids: set[str] = set()
    for agent in _as_mappings(agent_map.get("agents")):
        session_id = _text(agent.get("agent_id"), limit=120)
        if not session_id:
            continue
        goal_ids = [
            str(goal_id)
            for goal_id in (agent.get("goal_ids") or [])
            if goal_id and str(goal_id) in goals_by_id
        ]
        if focus_goal_id and focus_goal_id not in goal_ids:
            continue
        goal_entries: list[dict[str, Any]] = []
        for goal_id in goal_ids:
            run_goal = goals_by_id[goal_id]
            goal_entries.append(
                _goal_entry(
                    goal_id=goal_id,
                    status_item=status_by_goal.get(goal_id, {}),
                    run_goal=run_goal,
                    todo_items=todo_items,
                )
            )
        goal_entries.sort(key=lambda entry: entry["display_name"].lower())
        assigned_goal_ids.update(goal_ids)
        sessions.append(
            {
                "session_id": session_id,
                "agent_model": _text(agent.get("agent_model"), limit=64),
                "role": _text(agent.get("profile_role"), limit=64),
                "state": _text(agent.get("state"), limit=64) or "unknown",
                "next_action": _text(agent.get("next_action"), limit=220),
                "last_activity_at": _text(agent.get("last_activity_at"), limit=80),
                "goal_count": len(goal_entries),
                "goals": goal_entries,
            }
        )
    sessions.sort(key=lambda session: session["session_id"].lower())

    # Goals that no session declares land in an explicit unassigned bucket so
    # nothing silently disappears from the operator's view.
    unassigned: list[dict[str, Any]] = []
    if not focus_goal_id:
        for goal_id in sorted(goals_by_id):
            if goal_id in assigned_goal_ids:
                continue
            unassigned.append(
                _goal_entry(
                    goal_id=goal_id,
                    status_item=status_by_goal.get(goal_id, {}),
                    run_goal=goals_by_id[goal_id],
                    todo_items=todo_items,
                )
            )

    all_goal_ids = list(goals_by_id) if not focus_goal_id else [
        goal_id for goal_id in goals_by_id if goal_id == focus_goal_id
    ]
    bucket_counts = {"active": 0, "needs_user": 0, "blocked": 0, "done": 0, "other": 0}
    open_agent_todos = 0
    open_user_todos = 0
    done_todos = 0
    for goal_id in all_goal_ids:
        entry = _goal_entry(
            goal_id=goal_id,
            status_item=status_by_goal.get(goal_id, {}),
            run_goal=goals_by_id[goal_id],
            todo_items=todo_items,
        )
        bucket_counts[entry["status_bucket"]] = bucket_counts.get(entry["status_bucket"], 0) + 1
        open_agent_todos += entry["open_agent_todos"]
        open_user_todos += entry["open_user_todos"]
        done_todos += entry["done_todos"]

    usage_totals = _as_mapping(usage_map.get("totals"))
    raw_keys = [
        str(key)
        for key in (_as_mapping(status_map.get("boundary")).get("raw_material_key_names") or [])
        if key
    ]
    source_warnings = [
        {
            "kind": "raw_material",
            "key_names": key,
            "message": "raw/private-looking input key detected; value not copied",
        }
        for key in raw_keys
    ]

    projection = {
        "schema_version": SESSION_DASH_PROJECTION_SCHEMA_VERSION,
        "mode": "read_only",
        "generated_at": _text(generated_at, limit=80),
        "focus_goal_id": focus_goal_id,
        "overview": {
            "session_count": len(sessions),
            "goal_count": len(all_goal_ids),
            "run_count": _int(history.get("run_count")),
            "goals_by_status": bucket_counts,
            "open_agent_todos": open_agent_todos,
            "open_user_todos": open_user_todos,
            "done_todos": done_todos,
            "runs_24h": _int(usage_totals.get("runs_24h")),
            "runs_7d": _int(usage_totals.get("runs_7d")),
            "progress_runs_24h": _int(usage_totals.get("progress_signal_run_count_24h")),
            "quota_spend_slots_24h": _int(usage_totals.get("quota_spend_slots_24h")),
        },
        "sessions": sessions,
        "unassigned_goals": unassigned,
        "source_warnings": source_warnings,
    }
    return projection
