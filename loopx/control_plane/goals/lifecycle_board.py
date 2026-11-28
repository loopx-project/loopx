"""Read-only cross-Goal view over the derived artifact lifecycle projection.

The accepted RFC `goal-artifact-lifecycle-projection-v0` §4 names one mid-term
application: aggregate the per-Goal projection across Goals into a single
board that answers "which Goals reached which milestones, which guards block
how many Goals, and what is the next transition per Goal".

This module is that aggregation and nothing more. Every input is a status
payload the caller already collected; it reads no files, writes no state and
grants no authority. A Goal whose projection the status owner could not derive
becomes a typed gap row, never a re-derivation and never a silent omission, so
"not derived" and "nobody looked" stay distinguishable to the operator.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ...public_safe_text import ARTIFACT_LIFECYCLE_CATEGORIES, classify_private_text
from ..runtime.public_safety import (
    public_safe_compact_text,
    validate_public_safe_value,
)
from ...agent_registry import normalize_registered_agents, registered_agent_ids_for_goal
from .artifact_lifecycle import (
    GOAL_ARTIFACT_LIFECYCLE_PROJECTION_SCHEMA_VERSION,
    PHASE_CLOSED,
    PHASE_CLOSING,
    PHASE_QUALIFYING,
    PHASE_STARTING,
    PHASE_WAITING_OWNER,
)

GLOBAL_GOAL_LIFECYCLE_BOARD_SCHEMA_VERSION = "global_goal_lifecycle_board_v0"
GLOBAL_GOAL_LIFECYCLE_GAP_SCHEMA_VERSION = "global_goal_lifecycle_gap_v0"

# Display order only; a Goal moves between phases without a durable transition.
PHASE_ORDER = (
    PHASE_WAITING_OWNER,
    PHASE_QUALIFYING,
    PHASE_STARTING,
    PHASE_CLOSING,
    PHASE_CLOSED,
)
_PHASE_RANK = {phase: index for index, phase in enumerate(PHASE_ORDER)}
UNKNOWN_PHASE = "unknown"

GAP_PROJECTION_NOT_DERIVED = "projection_not_derived"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _label(value: Any, *, limit: int = 120) -> str | None:
    """Bound one already-derived label without widening the public boundary.

    The projection validated its own text before publishing it. Re-validating
    the complete source keeps this board from minting a wider label if a future
    producer hands it an unchecked string.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        validate_public_safe_value(text)
    except ValueError:
        return None
    if classify_private_text(text, categories=ARTIFACT_LIFECYCLE_CATEGORIES) is not None:
        return None
    compact: str | None = public_safe_compact_text(text, limit=limit)
    return compact


def _identity(value: Any) -> str | None:
    """Validate one machine identity without truncating it.

    A Goal id decides roster membership, rollup dedupe and the row key a
    consumer joins on, so two ids must stay distinguishable even when their
    first characters agree. Display bounds are applied separately by `_label`;
    identity keeps the validated text exactly as the status owner published it.
    """

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        validate_public_safe_value(text)
    except ValueError:
        return None
    if classify_private_text(text, categories=ARTIFACT_LIFECYCLE_CATEGORIES) is not None:
        return None
    return text


def _is_projection(value: Any) -> bool:
    projection = _mapping(value)
    return (
        projection.get("schema_version")
        == GOAL_ARTIFACT_LIFECYCLE_PROJECTION_SCHEMA_VERSION
        and isinstance(projection.get("milestones"), list)
    )


def _goal_row(
    goal_id: str, projection: dict[str, Any], *, status: dict[str, Any]
) -> dict[str, Any]:
    guards = [
        guard for guard in _list(projection.get("guards")) if _mapping(guard)
    ]
    open_guards = [guard for guard in guards if guard.get("blocked") is True]
    milestones = [
        milestone
        for milestone in _list(projection.get("milestones"))
        if _mapping(milestone)
    ]
    reached = [
        milestone
        for milestone in milestones
        if milestone.get("reached") is True
    ]
    transitions = [
        transition
        for transition in _list(projection.get("next_transitions"))
        if _mapping(transition)
    ]
    seen_guards: set[tuple[str, str, str]] = set()
    open_guard_rows: list[dict[str, str]] = []
    for guard in open_guards:
        row = {
            "id": _label(guard.get("id"), limit=120) or "unnamed_guard",
            "kind": _label(guard.get("kind"), limit=60) or "unknown",
            "owner": _label(guard.get("owner"), limit=120) or "unowned",
        }
        key = (row["id"], row["kind"], row["owner"])
        if key in seen_guards:
            continue
        seen_guards.add(key)
        open_guard_rows.append(row)
    phase = _label(projection.get("lifecycle_phase"), limit=40) or UNKNOWN_PHASE
    # Count what this row publishes, not what it received: a milestone id that
    # cannot be published is dropped from the list, and a count that still
    # included it would describe a row the operator cannot see.
    reached_ids: list[str] = []
    for item in reached:
        label = _label(item.get("id"), limit=60)
        # A producer that repeats a milestone must not make the row claim more
        # reached milestones than the rollup credits this Goal with.
        if label and label not in reached_ids:
            reached_ids.append(label)
    return {
        "goal_id": goal_id,
        "goal_status": _label(status.get("status"), limit=40),
        "activation_state": _label(status.get("activation_state"), limit=40),
        "lifecycle_phase": phase,
        "projection_status": "derived",
        "milestones_reached_count": len(reached_ids),
        "milestones_total_count": len(milestones),
        "milestones_reached": reached_ids,
        "guards_open_count": len(open_guard_rows),
        "guards_open": open_guard_rows,
        "next_transition": (
            {
                "target_phase": _label(transitions[0].get("target_phase"), limit=40)
                or UNKNOWN_PHASE,
                "precondition": _label(transitions[0].get("precondition")),
                "reason_codes": [
                    code
                    for code in (
                        _label(item, limit=60)
                        for item in _list(transitions[0].get("reason_codes"))
                    )
                    if code
                ],
            }
            if transitions
            else None
        ),
    }


def _gap_row(goal_id: str, *, reason: str) -> dict[str, Any]:
    return {
        "schema_version": GLOBAL_GOAL_LIFECYCLE_GAP_SCHEMA_VERSION,
        "goal_id": goal_id,
        "projection_status": "unavailable",
        "reason": reason,
    }


def _phase_rank(row: dict[str, Any]) -> tuple[int, str]:
    phase = str(row.get("lifecycle_phase") or "")
    return (_PHASE_RANK.get(phase, len(PHASE_ORDER)), str(row.get("goal_id") or ""))


def build_global_goal_lifecycle_board(
    status_payload: dict[str, Any] | None,
    *,
    limit: int = 50,
    phases: list[str] | None = None,
    agent_id: str | None = None,
    member_goal_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Aggregate every derived projection the status payload already carries.

    `limit` bounds returned Goal rows, not what was inspected: the phase, guard
    and milestone rollups always describe every projected Goal so a truncated
    page cannot read as a smaller estate. Unknown projection schemas are typed
    gaps rather than silently skipped Goals.

    `phases` and `agent_id` choose which rows the page lists. A phase is a
    window over an already-derived phase, and an agent matches only Goals whose
    recorded roster registers that exact identity, so both filters can only
    narrow the page: coverage and the cross-Goal rollups keep describing the
    whole inspected estate. An unknown agent matches nothing instead of
    silently widening back to every Goal.

    `member_goal_ids`, when given, is the current global membership this read
    was asked to describe. A status payload can retain history for a Goal that
    has already left the global registry; such a row is not current work, so it
    is dropped before any rollup or coverage count instead of inflating them.
    """

    bounded_limit = max(1, int(limit))
    status = _mapping(status_payload)
    run_history = _mapping(status.get("run_history"))
    goals = [goal for goal in _list(run_history.get("goals")) if _mapping(goal)]
    if member_goal_ids is not None:
        # Membership decides the estate; a history row for a Goal no longer
        # registered must not appear as an operator's current Goal. Matching
        # uses the complete recorded id so a display truncation can never merge
        # two members or admit a non-member.
        goals = [
            goal
            for goal in goals
            # A Goal without a publishable identity cannot be matched to a
            # member list; it stays visible as an identity gap rather than
            # disappearing from the inspected count.
            if _identity(goal.get("id")) is None
            or str(goal.get("id")) in member_goal_ids
        ]

    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    scoped_goal_ids: set[str] = set()
    unidentified = 0
    # The flag's presence decides whether a filter applies, never its text: an
    # identity that does not normalize matches no roster, so it selects an empty
    # page instead of silently widening back to the whole estate.
    agent_scope_requested = agent_id is not None
    requested_agents = (
        normalize_registered_agents(agent_id) if agent_scope_requested else []
    )
    for goal in goals:
        goal_id = _identity(goal.get("id"))
        if goal_id and (
            not agent_scope_requested
            or any(
                agent in registered_agent_ids_for_goal(goal)
                for agent in requested_agents
            )
        ):
            scoped_goal_ids.add(goal_id)
        if not goal_id:
            # A Goal whose identity cannot be published is still part of the
            # inspected estate. Dropping it would shrink the board silently and
            # let a partial answer read as complete.
            unidentified += 1
            continue
        projection = goal.get("artifact_lifecycle")
        if not _is_projection(projection):
            gaps.append(_gap_row(goal_id, reason=GAP_PROJECTION_NOT_DERIVED))
            continue
        rows.append(_goal_row(goal_id, _mapping(projection), status=goal))

    # One ordering pass decides every row list: phase rank for the operator
    # order, then Goal id, so the rollups and the page agree regardless of the
    # order the status payload happened to use.
    rows.sort(key=_phase_rank)
    phase_counts = Counter(str(row["lifecycle_phase"]) for row in rows)
    # Known phases keep the operator order; a phase a future producer adds is
    # still listed after them instead of vanishing from the rollup.
    rollup_phases = [
        phase for phase in PHASE_ORDER if phase_counts.get(phase)
    ] + sorted(set(phase_counts) - set(PHASE_ORDER))
    phases_summary = [
        {
            "lifecycle_phase": phase,
            "goal_count": phase_counts.get(phase, 0),
            "goal_ids": [
                str(row["goal_id"])
                for row in rows
                if row["lifecycle_phase"] == phase
            ],
        }
        for phase in rollup_phases
    ]

    guard_rollup: dict[tuple[str, str], set[str]] = {}
    milestone_rollup: dict[str, set[str]] = {}
    for row in rows:
        goal_id = str(row["goal_id"])
        # One Goal blocked by two guards of the same kind/owner is one blocked
        # Goal for that guard, not two; otherwise the board overstates reach.
        for guard in row["guards_open"]:
            key = (str(guard["kind"]), str(guard["owner"]))
            guard_rollup.setdefault(key, set()).add(goal_id)
        for milestone in row["milestones_reached"]:
            milestone_rollup.setdefault(milestone, set()).add(goal_id)
    guards_summary: list[dict[str, Any]] = [
        {
            "kind": kind,
            "owner": owner,
            "blocked_goal_count": len(blocked),
            "blocked_goal_ids": sorted(blocked),
        }
        for (kind, owner), blocked in sorted(
            guard_rollup.items(),
            key=lambda entry: (-len(entry[1]), entry[0][0], entry[0][1]),
        )
    ]
    milestones_summary: list[dict[str, Any]] = [
        {
            "milestone_id": milestone_id,
            "reached_goal_count": len(reached),
            "reached_goal_ids": sorted(reached),
        }
        for milestone_id, reached in sorted(milestone_rollup.items())
    ]

    wanted = {str(phase) for phase in (phases or []) if phase}
    # The page is the only filtered surface: an agent-scoped Goal row is listed
    # when its roster registers the requested identity, and a phase narrows that
    # page further. `filtered_goal_count` is measured after both filters and
    # before the page limit so the envelope can describe the same page.
    page_rows = [
        row
        for row in rows
        if (not agent_scope_requested or row["goal_id"] in scoped_goal_ids)
        and (not wanted or row["lifecycle_phase"] in wanted)
    ]
    page_gaps = [
        gap
        for gap in gaps
        if not agent_scope_requested or gap["goal_id"] in scoped_goal_ids
    ]
    returned_rows = page_rows[:bounded_limit]
    board = {
        "schema_version": GLOBAL_GOAL_LIFECYCLE_BOARD_SCHEMA_VERSION,
        "summary": {
            "goals_inspected": len(rows) + len(gaps) + unidentified,
            "goals_with_projection": len(rows),
            "goals_without_projection": len(gaps),
            "goals_without_identity": unidentified,
            "agent_scope_goal_count": (
                len(scoped_goal_ids)
                if agent_scope_requested
                else len(rows) + len(gaps)
            ),
            "filtered_goal_count": len(page_rows),
            "returned_goal_count": len(returned_rows),
            "blocked_goal_count": sum(1 for row in rows if row["guards_open_count"]),
            "phase_counts": dict(phase_counts),
            "truncated": len(page_rows) > len(returned_rows),
            "phase_filter": sorted(wanted) if wanted else [],
            "agent_filter": list(requested_agents),
            # A blank or malformed Agent value still arms the scope; without
            # this flag an empty page would be indistinguishable from an
            # unfiltered read that happened to match nothing.
            "agent_scope_requested": agent_scope_requested,
        },
        "phases": phases_summary,
        "guards": guards_summary,
        "milestones": milestones_summary,
        "goals": returned_rows,
        "gaps": page_gaps,
        "complete": not gaps and not unidentified,
    }
    try:
        validate_public_safe_value(board)
    except ValueError:
        # Fail closed: a board that cannot prove its own boundary is not
        # published as a partial board, because the omitted Goal would read as
        # "no lifecycle evidence" instead of "this read is unavailable".
        raise ValueError("lifecycle board violates the public-safe boundary") from None
    return board


__all__ = [
    "GAP_PROJECTION_NOT_DERIVED",
    "GLOBAL_GOAL_LIFECYCLE_BOARD_SCHEMA_VERSION",
    "GLOBAL_GOAL_LIFECYCLE_GAP_SCHEMA_VERSION",
    "PHASE_ORDER",
    "build_global_goal_lifecycle_board",
]
