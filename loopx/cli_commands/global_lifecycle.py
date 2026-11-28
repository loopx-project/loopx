"""Read-only cross-Goal lifecycle board over already-collected status.

`loopx global-lifecycle` is the CLI face of the accepted RFC
`goal-artifact-lifecycle-projection-v0` §4 application 1: one cross-Goal view
of phases, milestone coverage, blocking guards and the next transition per
Goal. It collects status exactly once and aggregates the projections the
status owner already derived; it never derives a second lifecycle, writes no
state and spends no quota.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..control_plane.goals.lifecycle_board import (
    GLOBAL_GOAL_LIFECYCLE_BOARD_SCHEMA_VERSION,
    GAP_PROJECTION_NOT_DERIVED,
    PHASE_ORDER,
    build_global_goal_lifecycle_board,
)
from ..control_plane.projection_envelope_facts import (
    render_projection_envelope_markdown,
    seal_projection_envelope,
    source_fact,
)
from ..control_plane.runtime.time import now_utc_iso
from ..presentation.markdown import as_dict, as_list
from ..presentation.public_safety import public_safe_boundary, redact_public_text
from ..status import collect_status


COMMAND = "/loopx-global-lifecycle"
SCHEMA_VERSION = "global_manager_command_response_v0"
GAP_LIMIT = 8


def _redact_text(value: object, *, limit: int = 220) -> str:
    redacted: str = redact_public_text(
        value,
        limit=limit,
        replacements={"/loop-global-lifecycle": COMMAND},
        truncation_marker="…",
    )
    return redacted


def _request(
    *, limit: int, phases: list[str], agent_id: str | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "global_manager_command_request_v0",
        "command": COMMAND,
        "legacy_aliases": ["/loop-global-lifecycle"],
        "cli_command": "loopx global-lifecycle",
        "include": ["phases", "milestones", "guards", "next_transitions"],
        "phase_filter": list(phases),
        # The request states the Agent scope it actually applied, so a reader
        # cannot take an unfiltered estate for a filtered page. Presence is what
        # arms the filter, so an explicitly blank value is echoed, never dropped.
        "agent_filter": [agent_id] if agent_id is not None else [],
        "limit": limit,
        "privacy_mode": "public_safe_summary",
        "dry_run": True,
    }


def _global_member_ids(status_payload: dict[str, Any]) -> set[str] | None:
    """The complete current global membership, when the status owner published it.

    A `None` answer means the payload cannot bind rows to membership, and the
    board must stay uncertified rather than describe an unknown estate as whole.
    """

    global_registry = as_dict(status_payload.get("global_registry"))
    if global_registry.get("available") is not True:
        return None
    ids = global_registry.get("global_goal_ids")
    if not isinstance(ids, list):
        return None
    return {item for item in ids if isinstance(item, str) and item}


def _scope_counts(
    status_payload: dict[str, Any], summary: dict[str, Any]
) -> dict[str, int | None]:
    """Global scope accounting shared by the envelope and the top-level verdict.

    The scope is global, so the denominator is global membership, never the
    local registry this process happened to read. A bounded status read that
    never reached some in-scope Goals is an omission, not a smaller estate.
    """

    global_registry = as_dict(status_payload.get("global_registry"))
    available = global_registry.get("available") is True
    global_count = global_registry.get("global_goal_count")
    expected = (
        global_count
        if available and isinstance(global_count, int) and not isinstance(global_count, bool)
        else None
    )
    excluded = (
        int(global_registry.get("current_registry_excluded_goal_count") or 0)
        if available
        else 0
    )
    inspected = summary.get("goals_inspected")
    unscanned = (
        max(0, expected - excluded - inspected)
        if expected is not None and isinstance(inspected, int)
        else 0
    )
    return {"expected": expected, "excluded": excluded, "inspected": inspected, "unscanned": unscanned}


def _envelope(
    status_payload: dict[str, Any],
    *,
    status_read_at: str | None,
    board: dict[str, Any] | None,
    failed_status_goals: int = 0,
) -> dict[str, Any]:
    status_envelope = as_dict(status_payload.get("projection_envelope"))
    # Only a sealed upstream envelope may be composed; an absent or foreign
    # status envelope stays out rather than failing this read.
    upstream = (
        [status_envelope]
        if isinstance(status_envelope.get("coverage"), dict)
        and isinstance(status_envelope.get("sources"), list)
        else []
    )
    global_registry = as_dict(status_payload.get("global_registry"))
    global_source = next(
        (
            row
            for row in as_list(status_envelope.get("sources"))
            if isinstance(row, dict) and row.get("source_id") == "global_registry"
        ),
        {},
    )
    summary = as_dict(as_dict(board).get("summary"))
    scope = _scope_counts(status_payload, summary)
    shown = summary.get("returned_goal_count")
    if not isinstance(shown, int) or isinstance(shown, bool):
        shown = None
    filtered = summary.get("filtered_goal_count")
    if not isinstance(filtered, int) or isinstance(filtered, bool):
        filtered = None
    available = global_registry.get("available") is True
    expected = scope["expected"]
    excluded = scope["excluded"] or 0
    unscanned = scope["unscanned"] or 0
    omitted: list[dict[str, Any]] = []
    if excluded:
        omitted.append(
            {
                "reason": "outside_current_registry",
                "count": excluded,
                "refs": [
                    _redact_text(ref, limit=120)
                    for ref in as_list(
                        global_registry.get("current_registry_excluded_goal_ids")
                    )[:8]
                ],
            }
        )
    if failed_status_goals:
        omitted.append({"reason": "status_unavailable", "count": failed_status_goals})
    elif status_payload.get("ok") is not True:
        # Whether the read raised before producing a payload or returned an
        # unhealthy one, the estate behind this answer is missing; say so
        # rather than reporting an empty omission list next to `not_read`.
        omitted.append({"reason": "status_unavailable", "count": 1})
    without_projection = summary.get("goals_without_projection")
    if isinstance(without_projection, int) and without_projection:
        omitted.append(
            {"reason": GAP_PROJECTION_NOT_DERIVED, "count": without_projection}
        )
    without_identity = summary.get("goals_without_identity")
    if isinstance(without_identity, int) and without_identity:
        omitted.append({"reason": "goal_identity_unavailable", "count": without_identity})
    if unscanned:
        omitted.append({"reason": "goal_scan_omitted", "count": unscanned})
    projected = summary.get("goals_with_projection")
    # Included means "carried a derived projection in this answer". It can never
    # exceed what was inspected or what remains inside the current registry.
    included = (
        min(projected, max(0, expected - excluded))
        if isinstance(projected, int) and expected is not None
        else 0
    )
    sealed: dict[str, Any] = seal_projection_envelope(
        projection="global_goal_lifecycle",
        observed_at=now_utc_iso(),
        sources=[
            source_fact(
                "global_registry",
                read_status=(
                    str(global_source.get("read_status") or "read")
                    if available
                    else str(
                        global_registry.get("read_status")
                        or ("missing" if status_read_at else "not_read")
                    )
                ),
                last_read_at=global_source.get("last_read_at")
                or (status_read_at if available else None),
                item_count=expected,
            ),
            *(
                []
                if upstream
                else [
                    source_fact(
                        "status",
                        read_status="read" if status_read_at else "not_read",
                        last_read_at=status_read_at,
                        item_count=summary.get("goals_inspected"),
                    )
                ]
            ),
        ],
        coverage={
            "scope": "global",
            "expected_count": expected,
            "included_count": included,
            "omitted": omitted,
            "shown_count": shown,
            # A display limit can only page through the rows the filters
            # selected. `available_count` is that pageable count — after Agent
            # and phase selection, before the limit — so an empty filtered page
            # is not reported as a truncated one.
            "available_count": filtered if filtered is not None else shown,
        },
        upstream=upstream,
    )
    return sealed


def build_global_lifecycle_error(
    error: object,
    *,
    limit: int,
    phases: list[str],
    agent_id: str | None = None,
    status_payload: dict[str, Any] | None = None,
    status_read_at: str | None = None,
    error_code: str = "global_lifecycle_unavailable",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "request": _request(limit=limit, phases=phases, agent_id=agent_id),
        "generated_at": now_utc_iso(),
        "error_code": _redact_text(error_code, limit=120),
        "error": _redact_text(error),
        "omissions": [
            "Raw/private failure details and local paths were intentionally omitted."
        ],
        "boundary": public_safe_boundary(),
    }
    try:
        payload["projection_envelope"] = _envelope(
            status_payload or {},
            status_read_at=status_read_at,
            board=None,
        )
    except Exception:
        pass
    return payload


def _status_failure_count(status_payload: dict[str, Any]) -> int:
    contract = as_dict(status_payload.get("contract"))
    diagnostics = contract.get("error_diagnostics")
    if not isinstance(diagnostics, list):
        return 0
    return sum(
        1
        for row in diagnostics
        if isinstance(row, dict) and row.get("goal_id")
    )


def build_global_lifecycle(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    limit: int,
    phases: list[str] | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    wanted = [str(phase) for phase in (phases or []) if phase]
    # A blank value still means "the caller asked for an Agent scope": dropping
    # it here would silently return the unfiltered estate.
    requested_agent = _redact_text(agent_id, limit=120) if agent_id is not None else None
    unknown = sorted(set(wanted) - set(PHASE_ORDER))
    if unknown:
        return build_global_lifecycle_error(
            "Unsupported lifecycle phase filter: " + ", ".join(unknown) + ".",
            limit=max(1, limit),
            phases=wanted,
            agent_id=requested_agent,
            error_code="unsupported_phase_filter",
        )
    # One bounded status read feeds every rollup, so the envelope names the
    # in-scope Goals this cap never reached instead of presenting the sample as
    # the whole estate. The cap is generous enough to cover ordinary estates.
    scan_limit = max(min(max(1, limit), 200) * 25, 2500)
    try:
        status_payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=runtime_root_override,
            limit=scan_limit,
            agent_lane_id=requested_agent,
        )
    except Exception as exc:
        return build_global_lifecycle_error(
            exc,
            limit=max(1, limit),
            phases=wanted,
            agent_id=requested_agent,
            error_code="status_collection_unavailable",
        )
    status_read_at = now_utc_iso()
    if status_payload.get("ok") is not True:
        return build_global_lifecycle_error(
            "Global status source unavailable.",
            limit=max(1, limit),
            phases=wanted,
            agent_id=requested_agent,
            status_payload=status_payload,
            status_read_at=status_read_at,
            error_code="status_collection_unavailable",
        )
    try:
        board = build_global_goal_lifecycle_board(
            status_payload,
            limit=max(1, limit),
            phases=wanted,
            agent_id=requested_agent,
            member_goal_ids=_global_member_ids(status_payload),
        )
    except ValueError as exc:
        return build_global_lifecycle_error(
            exc,
            limit=max(1, limit),
            phases=wanted,
            agent_id=requested_agent,
            status_payload=status_payload,
            status_read_at=status_read_at,
            error_code="lifecycle_board_boundary_rejected",
        )
    summary = as_dict(board.get("summary"))
    envelope = _envelope(
        status_payload,
        status_read_at=status_read_at,
        board=board,
        failed_status_goals=_status_failure_count(status_payload),
    )
    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "request": _request(
            limit=max(1, limit), phases=wanted, agent_id=requested_agent
        ),
        "generated_at": now_utc_iso(),
        "board_schema_version": GLOBAL_GOAL_LIFECYCLE_BOARD_SCHEMA_VERSION,
        "summary": {
            **summary,
            "headline": (
                f"{summary.get('goals_with_projection')} of "
                f"{summary.get('goals_inspected')} Goals carry a derived lifecycle "
                f"projection; {summary.get('blocked_goal_count')} are guard-blocked."
            ),
        },
        "phases": board["phases"],
        "guards": board["guards"],
        "milestones": board["milestones"],
        "goals": board["goals"],
        "gaps": board["gaps"][:GAP_LIMIT],
        "gaps_truncated": len(board["gaps"]) > GAP_LIMIT,
        # One verdict, one meaning: the board is complete only when every Goal
        # in global scope carried a projection and no source was omitted. The
        # envelope decides scope; this field repeats it so a JSON consumer that
        # reads only `complete` cannot mistake a partial scan for a whole estate.
        "complete": bool(board["complete"] and envelope.get("complete") is True),
        "projection_envelope": envelope,
        "omissions": [
            (
                "Raw logs, transcripts, connector payloads, credential values, local "
                "paths and private source bodies were intentionally omitted."
            ),
            (
                "Milestones and guards are the status owner's derived projection; this "
                "board never re-derives a lifecycle or a second acceptance verdict."
            ),
        ],
        "boundary": public_safe_boundary(),
    }


def _render_goal_line(goal: dict[str, Any]) -> str:
    line = (
        f"- goal=`{_redact_text(goal.get('goal_id'), limit=120)}` "
        f"phase=`{_redact_text(goal.get('lifecycle_phase'), limit=40)}` "
        f"milestones=`{goal.get('milestones_reached_count')}/"
        f"{goal.get('milestones_total_count')}` "
        f"open_guards=`{goal.get('guards_open_count')}`"
    )
    reached = [
        _redact_text(item, limit=60)
        for item in as_list(goal.get("milestones_reached"))
        if _redact_text(item, limit=60)
    ]
    if reached:
        line += " reached=" + ", ".join(reached) + "."
    for guard in as_list(goal.get("guards_open")):
        record = as_dict(guard)
        line += (
            f" blocked_by {_redact_text(record.get('kind'), limit=60)}"
            f"({_redact_text(record.get('owner'), limit=80)}):"
            f"{_redact_text(record.get('id'), limit=120)}."
        )
    transition = as_dict(goal.get("next_transition"))
    if transition:
        line += (
            f" Next: {_redact_text(transition.get('target_phase'), limit=40)} — "
            f"{_redact_text(transition.get('precondition'))}"
        )
    return line


def render_global_lifecycle_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        lines = [
            "# LoopX Global Lifecycle Unavailable",
            "",
            "- ok: `False`",
            f"- error_code: `{_redact_text(payload.get('error_code'), limit=120)}`",
            f"- error: {_redact_text(payload.get('error'))}",
            *render_projection_envelope_markdown(payload.get("projection_envelope")),
        ]
        omissions = [
            _redact_text(item)
            for item in as_list(payload.get("omissions"))
            if _redact_text(item)
        ]
        if omissions:
            lines.extend(["", "## Omissions"])
            lines.extend(f"- {item}" for item in omissions)
        return "\n".join(lines)

    summary = as_dict(payload.get("summary"))
    request = as_dict(payload.get("request"))
    lines = [
        "# LoopX Global Lifecycle",
        "",
        f"- command: `{_redact_text(request.get('command'), limit=120)}`",
        f"- {_redact_text(summary.get('headline'))}",
        f"- complete: `{bool(payload.get('complete'))}`",
        f"- truncated: `{bool(summary.get('truncated'))}`",
        *render_projection_envelope_markdown(payload.get("projection_envelope")),
        "",
        "Phases, guards and milestones are the status owner's derived projection.",
    ]
    lines.extend(["", "## Phases"])
    phase_rows = [as_dict(item) for item in as_list(payload.get("phases"))]
    if phase_rows:
        lines.extend(
            f"- `{_redact_text(row.get('lifecycle_phase'), limit=40)}`: "
            f"{row.get('goal_count')} goal(s)"
            for row in phase_rows
        )
    else:
        lines.append("- No Goal carries a derived lifecycle projection.")
    lines.extend(["", "## Blocking Guards"])
    guards = [as_dict(item) for item in as_list(payload.get("guards"))]
    if guards:
        lines.extend(
            f"- `{_redact_text(row.get('kind'), limit=60)}` "
            f"owner=`{_redact_text(row.get('owner'), limit=80)}` blocks "
            f"{row.get('blocked_goal_count')} goal(s): "
            + ", ".join(
                _redact_text(goal_id, limit=120)
                for goal_id in as_list(row.get("blocked_goal_ids"))[:8]
            )
            for row in guards
        )
    else:
        lines.append("- None.")
    lines.extend(["", "## Milestones Reached"])
    milestones = [as_dict(item) for item in as_list(payload.get("milestones"))]
    if milestones:
        lines.extend(
            f"- `{_redact_text(row.get('milestone_id'), limit=60)}`: "
            f"{row.get('reached_goal_count')} goal(s)"
            for row in milestones
        )
    else:
        lines.append("- None.")
    lines.extend(["", "## Goals"])
    goals = [as_dict(item) for item in as_list(payload.get("goals"))]
    if goals:
        lines.extend(_render_goal_line(goal) for goal in goals)
    else:
        lines.append("- None.")
    gaps = [as_dict(item) for item in as_list(payload.get("gaps"))]
    if gaps:
        lines.extend(["", "## Lifecycle Gaps"])
        lines.extend(
            f"- goal=`{_redact_text(gap.get('goal_id'), limit=120)}` "
            f"reason=`{_redact_text(gap.get('reason'), limit=80)}`"
            for gap in gaps
        )
        if payload.get("gaps_truncated"):
            lines.append("- Further gaps were omitted from display; see the envelope.")
    lines.extend(
        [
            "",
            "## Boundary",
            "- Read-only report; it does not close Goals, resolve guards or spend quota.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "COMMAND",
    "build_global_lifecycle",
    "build_global_lifecycle_error",
    "render_global_lifecycle_markdown",
]
