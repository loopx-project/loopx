"""Doctor readback of whether bound host lanes still deliver scheduled prompts.

A Codex App automation that stops delivering its prompt leaves the Goal in the
same shape as a Goal that is simply waiting: an open Todo, a normal
``should-run`` decision, and no Turn entering the LoopX protocol. Nothing in the
Goal's own records distinguishes the two, so the operator learns days later.

The readback here reuses the observation and window owners that already serve
the status projection. It reads each bound lane once, reports one typed window
per lane, and names a missed-delivery window only when the lane has a cadence to
keep and was observed past it. A lane with no cadence, no record or an
unreadable record is reported as ``missing``/``unknown`` -- never as healthy and
never as a fault.

This module performs no host action: it does not start a Turn, write the host
store, or spend quota.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re
from typing import Any

from ...public_safe_text import SECRET_LIKE_SURFACE_PATTERN, find_public_safe_local_path
from ..goals.activation import GoalActivationState, goal_activation_state
from ..runtime.public_safety import validate_public_safe_value
from .host_thread_activity import (
    DEFAULT_DELIVERY_WINDOW_TOLERANCE,
    HOST_DELIVERY_WINDOW_SCHEMA_VERSION,
    MAX_OBSERVED_THREADS_PER_GOAL,
    HostDeliveryEvidenceProvider,
    HostDeliveryWindowState,
    HostThreadObservationCompleteness,
    HostDeliveryExpectationProvider,
    HostThreadObserver,
    HostThreadState,
    attach_host_delivery_windows,
    attach_host_thread_activity,
)

HOST_DELIVERY_DIAGNOSIS_SCHEMA_VERSION = "loopx_host_delivery_diagnosis_v0"
# Two typed outcomes, deliberately distinct. A lane observed past its cadence
# shows a missed delivery; a lane with no comparable tick shows only that the
# receipt is unverified. An absent record is not proof the host stopped.
HOST_PROMPT_DELIVERY_MISSED = "host_prompt_delivery_missed"
HOST_PROMPT_DELIVERY_UNVERIFIED = "host_prompt_delivery_unverified"

# Doctor reads one bounded slice: a few dozen lanes describe every plausible
# workspace, and an unbounded scan would turn a diagnostic into a walk over the
# whole Codex history.
MAX_DIAGNOSED_LANES = 64

_LOCAL_OBSERVED_STATES = (
    HostDeliveryWindowState.FRESH,
    HostDeliveryWindowState.STALE,
    HostDeliveryWindowState.MISSING,
    HostDeliveryWindowState.UNKNOWN,
)


def _observed_bindings(
    goal: Mapping[str, Any], observer_surfaces: Iterable[str]
) -> tuple[list[dict[str, Any]], int]:
    """Return the Goal's bindings this doctor can observe, and unmeasured ones.

    Only a binding with a host surface this process can read is kept: a Goal
    bound to a host without an observer must not be reported as a missed
    delivery by an observer that never had a way to see it. A binding on an
    observable surface that names no thread cannot be measured, so it is counted
    rather than dropped: an operator who mistyped a binding otherwise reads a
    healthy workspace with one lane missing.
    """

    coordination = goal.get("coordination")
    raw_bindings = (
        coordination.get("thread_agent_bindings")
        if isinstance(coordination, Mapping)
        else None
    )
    surfaces = set(observer_surfaces)
    bindings: list[dict[str, Any]] = []
    unmeasured = 0
    for raw in raw_bindings if isinstance(raw_bindings, list) else []:
        if not isinstance(raw, Mapping):
            continue
        host_surface = str(raw.get("host_surface") or "").strip()
        thread_id = str(raw.get("thread_id") or "").strip()
        agent_id = str(raw.get("agent_id") or "").strip()
        if host_surface not in surfaces:
            continue
        if thread_id and agent_id:
            bindings.append(dict(raw))
        else:
            # The shared observer drops a binding without both an agent and a
            # thread, so it would vanish from the report entirely.
            unmeasured += 1
    return bindings, unmeasured


def _ambiguous_lanes(bindings: Iterable[Mapping[str, Any]]) -> set[tuple[str, str]]:
    """Return the ``(agent, surface)`` lanes bound to more than one thread.

    This reads the Goal's complete binding list, before the lane bound trims
    what this run observes. Truncation removes observations, not identities: a
    lane that names two threads is ambiguous whether the second binding sits at
    position 2 or 65, and slicing first would let the survivor look like a
    single canonical thread.
    """

    counts: dict[tuple[str, str], int] = {}
    for raw in bindings:
        key = (
            str(raw.get("agent_id") or "").strip(),
            str(raw.get("host_surface") or "").strip(),
        )
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


_SAFE_LABEL_TAIL = re.compile(r"[A-Za-z0-9._-]{1,40}")
# A registry identifier is free text; keeping a very long one would push a
# pasted `doctor` transcript past any reader's patience without adding detail a
# digest does not already carry.
MAX_LABEL_LENGTH = 120


def _unsafe_label(text: str) -> bool:
    if len(text) > MAX_LABEL_LENGTH:
        return True
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate cannot be written to a UTF-8 stdout at all.
        return True
    return bool(
        find_public_safe_local_path(text) or SECRET_LIKE_SURFACE_PATTERN.search(text)
    )


def _public_label(value: object) -> tuple[str, bool]:
    """Return a label that may be published, and whether it was redacted.

    A Goal, agent or surface identifier is free-form text in the registry: an
    operator can name a Goal after a directory or paste a token-shaped value.
    Doctor output also serves ``--format json`` and pasted transcripts, and the
    public-output boundary rejects both shapes, so an unsafe identifier would
    otherwise abort the whole run instead of naming the lane. Keep a digest of
    the exact identifier (stable across runs and distinct per identifier) and,
    when the last path segment is itself safe, append it so the operator can
    still recognize the lane.
    """

    text = str(value or "").strip()
    if not text:
        return "", False
    if not _unsafe_label(text):
        return text, False
    digest = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:10]
    tail = text.replace("\\", "/").rsplit("/", 1)[-1].strip()
    safe_tail = tail if _SAFE_LABEL_TAIL.fullmatch(tail) and not _unsafe_label(tail) else ""
    marker = f"<redacted:{digest}>"
    return (f"{marker}:{safe_tail}" if safe_tail else marker), True


def _lane_segment(text: str) -> str:
    """Spell one label component so ``/`` keeps separating exactly three parts.

    Two different registries can spell ``goal=a`` + ``agent=b/c`` and
    ``goal=a/b`` + ``agent=c``; joined verbatim both read ``a/b/c`` and one
    silent lane would hide behind the other. Percent-encoding the separator (and
    the escape itself) keeps the label reversible and unique.
    """

    return text.replace("%", "%25").replace("/", "%2F")


def _lane_identity(goal_id: str, agent_id: object, host_surface: object) -> str:
    return "/".join(
        _lane_segment(str(part or "")) for part in (goal_id, agent_id, host_surface)
    )


def _lane_payload(goal_id: str, row: Mapping[str, Any]) -> dict[str, Any]:
    window = row.get("delivery_window")
    window = window if isinstance(window, Mapping) else {}
    goal_label, goal_redacted = _public_label(goal_id)
    agent_label, agent_redacted = _public_label(row.get("agent_id"))
    surface_label, surface_redacted = _public_label(row.get("host_surface"))
    lane: dict[str, Any] = {
        "lane": _lane_identity(goal_label, agent_label, surface_label),
        "goal_id": goal_label,
        "agent_id": agent_label,
        "host_surface": surface_label,
        "thread_state": row.get("state"),
        "window_state": window.get("state") or HostDeliveryWindowState.UNKNOWN.value,
    }
    if goal_redacted or agent_redacted or surface_redacted:
        # The digest carries identity; this flag tells a reader the shown name
        # is not the registry spelling, so it does not grep for it in the store.
        lane["label_redacted"] = True
    for key in (
        "last_observed_at",
        "age_hours",
        "expected_interval_minutes",
        "window_minutes",
        "source",
        "reason",
        "delivery_evidence_at",
    ):
        if window.get(key) is not None:
            lane[key] = window[key]
    if lane["window_state"] in {
        HostDeliveryWindowState.MISSING.value,
        HostDeliveryWindowState.UNKNOWN.value,
    }:
        # Why the lane could not be measured, so a reader does not go looking
        # for an absent automation that was never installed for this surface.
        reason = window.get("reason")
        if lane["thread_state"] == HostThreadState.ARCHIVED.value:
            # The host's own record says this thread is archived, so its
            # rollout holds no comparable tick. That is not the "unparseable
            # observation" the window layer infers from an empty timestamp, and
            # naming it keeps the operator off the wrong trail.
            reason = "thread_archived"
        lane["diagnosis"] = {
            "code": HOST_PROMPT_DELIVERY_UNVERIFIED,
            "state": lane["window_state"],
            "reason": reason,
        }
    if lane["window_state"] == HostDeliveryWindowState.STALE.value:
        lane["diagnosis"] = {
            "code": HOST_PROMPT_DELIVERY_MISSED,
            "state": HostDeliveryWindowState.STALE.value,
            # The last tick the host did record: without it the operator cannot
            # tell how long the lane has been silent.
            "last_observed_at": window.get("last_observed_at"),
            "age_hours": window.get("age_hours"),
            "window_minutes": window.get("window_minutes"),
            "expected_interval_minutes": window.get("expected_interval_minutes"),
            "source": window.get("source"),
        }
    return lane


def collect_host_delivery_diagnosis(
    goals: Iterable[Mapping[str, Any]],
    *,
    observers: Mapping[str, HostThreadObserver],
    expectations: HostDeliveryExpectationProvider,
    now: datetime | None = None,
    tolerance: int = DEFAULT_DELIVERY_WINDOW_TOLERANCE,
    max_lanes: int = MAX_DIAGNOSED_LANES,
    evidence: HostDeliveryEvidenceProvider | None = None,
) -> dict[str, Any]:
    """Report one typed delivery window per bound host lane of ``goals``.

    ``goals`` is the registry view the caller already read; this function never
    writes it. Stopped goals are skipped: their automations are not a live
    promise, so a quiet lane there is not a missed delivery.

    ``evidence`` names the most recent tick each lane's scheduled prompt
    produced. When it is given, recent activity alone no longer reads ``fresh``:
    an empty turn or a hand-written message leaves the lane unverified while its
    activity time is still reported.
    """

    if max_lanes < 1:
        raise ValueError("max_lanes must be at least one")
    surfaces = tuple(observers)
    projection_goals: list[dict[str, Any]] = []
    stopped = 0
    lanes_seen = 0
    truncated = False
    unreadable_activation = 0
    unmeasured_bindings = 0
    ambiguous_by_goal: dict[str, set[tuple[str, str]]] = {}
    for goal in goals:
        if not isinstance(goal, Mapping) or not str(goal.get("id") or "").strip():
            continue
        try:
            state = goal_activation_state(dict(goal))
        except ValueError:
            # An unreadable activation record is not this check's finding; the
            # registry and activation readers already report it. Count the Goal
            # so the verdict below does not present its lanes as measured.
            unreadable_activation += 1
            continue
        if state is GoalActivationState.STOPPED:
            stopped += 1
            continue
        local, unmeasured = _observed_bindings(goal, surfaces)
        unmeasured_bindings += unmeasured
        if not local:
            continue
        goal_id = str(goal["id"])
        # Resolve identity over every binding this Goal records, then trim what
        # the run reads.
        ambiguous_by_goal.setdefault(goal_id, set()).update(_ambiguous_lanes(local))
        remaining = max_lanes - lanes_seen
        if remaining <= 0:
            truncated = True
            break
        if len(local) > remaining:
            local = local[:remaining]
            truncated = True
        lanes_seen += len(local)
        projection_goals.append(
            {"id": goal_id, "coordination": {"thread_agent_bindings": local}}
        )

    payload: dict[str, Any] = {"run_history": {"goals": projection_goals}}
    observed_at = now
    if projection_goals:
        attach_host_thread_activity(payload, observers=observers, now=observed_at)
        attach_host_delivery_windows(
            payload,
            expectations=expectations,
            tolerance=tolerance,
            now=observed_at,
            evidence=evidence,
            ambiguous_lanes=ambiguous_by_goal,
        )

    lanes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    incomplete_goals = 0
    for goal in payload["run_history"]["goals"]:
        activity = goal.get("host_thread_activity")
        threads = activity.get("threads") if isinstance(activity, Mapping) else None
        if isinstance(activity, Mapping) and (
            activity.get("completeness")
            == HostThreadObservationCompleteness.INCOMPLETE.value
        ):
            # The shared observer keeps a per-Goal binding bound. A Goal past it
            # is reported here as truncated rather than as fully observed: a
            # silent lane can hide among the bindings it did not read.
            incomplete_goals += 1
        for row in threads if isinstance(threads, list) else []:
            if not isinstance(row, Mapping):
                continue
            # Dedupe on the exact identity parts, not on the joined label: two
            # different registries can spell ids whose concatenation collides
            # (``a`` + ``b/c`` and ``a/b`` + ``c``).
            identity = (
                str(goal.get("id") or ""),
                str(row.get("agent_id") or ""),
                str(row.get("host_surface") or ""),
            )
            # Two bindings for one agent on one surface are one lane whose
            # canonical thread is ambiguous; both resolve to the same window, so
            # a second row would only duplicate the label an operator greps.
            if identity in seen:
                continue
            seen.add(identity)
            lanes.append(_lane_payload(identity[0], row))

    counts = {state.value: 0 for state in _LOCAL_OBSERVED_STATES}
    for lane in lanes:
        if lane["window_state"] in counts:
            counts[lane["window_state"]] += 1
    stale = [lane for lane in lanes if lane["window_state"] == HostDeliveryWindowState.STALE.value]
    unverified = [
        lane
        for lane in lanes
        if lane["window_state"] in {
            HostDeliveryWindowState.MISSING.value,
            HostDeliveryWindowState.UNKNOWN.value,
        }
    ]
    bounded = truncated or bool(incomplete_goals)
    # Every lane this run did not read is a lane that could be silent.
    unmeasured = bounded or bool(unmeasured_bindings) or bool(unreadable_activation)
    incomplete_reason = "scan_truncated" if bounded else "unmeasured_lane"
    if not lanes:
        # A scan that read nothing proves nothing: a lane may sit past the bound
        # or past the per-Goal binding cap this run did not read.
        status, reason = "not_applicable", (
            incomplete_reason if unmeasured else "no_bound_host_lane"
        )
    elif stale:
        status, reason = "stale", HOST_PROMPT_DELIVERY_MISSED
    elif unverified:
        status, reason = "unverified", "no_comparable_tick"
    elif unmeasured:
        # Everything read is inside its window, but the unread lanes could be
        # silent. Reporting `fresh` here would claim coverage this run did not
        # have.
        status, reason = "unverified", incomplete_reason
    else:
        status, reason = "fresh", None
    diagnosis = {
        "schema_version": HOST_DELIVERY_DIAGNOSIS_SCHEMA_VERSION,
        "window_schema_version": HOST_DELIVERY_WINDOW_SCHEMA_VERSION,
        "observed_at": (observed_at or datetime.now(timezone.utc)).isoformat(),
        "status": status,
        "reason": reason,
        "lane_count": len(lanes),
        "counts": counts,
        "truncated": bounded,
        "incomplete_goals": incomplete_goals,
        "unmeasured_bindings": unmeasured_bindings,
        "unreadable_activation_goals": unreadable_activation,
        "per_goal_binding_cap": MAX_OBSERVED_THREADS_PER_GOAL,
        "stale_lanes": [lane["lane"] for lane in stale],
        "unverified_lanes": [lane["lane"] for lane in unverified],
        "skipped_stopped_goals": stopped,
        "tolerance": tolerance,
        "lanes": lanes,
    }
    return _publishable(diagnosis)


def _publishable(diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Return ``diagnosis`` only if the public-output boundary accepts it.

    Lane labels are sanitized before they are built, so this is a last-resort
    guard against a field this module did not anticipate. An optional check
    must never take the whole ``doctor`` run down: on a remaining violation the
    verdict and per-state counts survive and only the identifying names are
    withheld, so an operator still sees that a lane is silent.
    """

    try:
        validate_public_safe_value(diagnosis)
        return diagnosis
    except ValueError:
        withheld = dict(diagnosis)
        withheld["lanes"] = []
        withheld["stale_lanes"] = []
        withheld["unverified_lanes"] = []
        withheld["labels_withheld"] = True
        validate_public_safe_value(withheld)
        return withheld


def collect_registry_host_delivery_diagnosis(
    *,
    registry_path: Path | str,
    max_lanes: int = MAX_DIAGNOSED_LANES,
) -> dict[str, Any]:
    """Read the registry and report the delivery window of each bound lane.

    The registry is an input the caller already trusts; a missing or unreadable
    registry is reported as ``not_applicable``/``registry_unavailable`` so a
    broken workspace cannot turn this optional check into a doctor failure.
    """

    from ...registry import registry_goals
    from ..projects.registry_codec import ProjectRegistryProtocolError, load_registry

    try:
        registry = load_registry(Path(registry_path))
    except ProjectRegistryProtocolError:
        # The file is readable; this build just cannot decode it. Telling the
        # operator the registry is unavailable would send them looking for a
        # missing file instead of the upgrade the reader asked for.
        return _empty_diagnosis("registry_requires_upgrade")
    except (OSError, ValueError):
        return _empty_diagnosis("registry_unavailable")
    goals = registry_goals(registry)
    if not goals:
        return _empty_diagnosis("registry_empty")

    from ...codex_app_thread_activity import (
        CODEX_APP_BINDING_SURFACE,
        codex_delivery_evidence,
        codex_delivery_expectations,
        codex_thread_observers,
    )

    # Only an installed app automation carries a cadence. A Goal bound to a CLI
    # TUI or IDE thread keeps its own loop, so reporting such a lane here would
    # permanently read "unverified" and teach operators to ignore the check.
    observers = codex_thread_observers()
    observers = (
        {CODEX_APP_BINDING_SURFACE: observers[CODEX_APP_BINDING_SURFACE]}
        if CODEX_APP_BINDING_SURFACE in observers
        else {}
    )
    return collect_host_delivery_diagnosis(
        goals,
        observers=observers,
        expectations=codex_delivery_expectations,
        evidence=codex_delivery_evidence,
        max_lanes=max_lanes,
    )


def _empty_diagnosis(reason: str) -> dict[str, Any]:
    return {
        "schema_version": HOST_DELIVERY_DIAGNOSIS_SCHEMA_VERSION,
        "window_schema_version": HOST_DELIVERY_WINDOW_SCHEMA_VERSION,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "not_applicable",
        "reason": reason,
        "lane_count": 0,
        "counts": {state.value: 0 for state in _LOCAL_OBSERVED_STATES},
        "stale_lanes": [],
        "unverified_lanes": [],
        "truncated": False,
        "incomplete_goals": 0,
        "unmeasured_bindings": 0,
        "unreadable_activation_goals": 0,
        "per_goal_binding_cap": MAX_OBSERVED_THREADS_PER_GOAL,
        "skipped_stopped_goals": 0,
        "lanes": [],
    }


def collect_host_delivery_diagnosis_route(
    *,
    registry_path: Path,
    route_status: str,
    max_lanes: int = MAX_DIAGNOSED_LANES,
) -> dict[str, Any]:
    """Doctor entry point: skip a route the local-state reader already refused."""

    if route_status in {"conflict", "invalid"}:
        return _empty_diagnosis("local_state_route_unresolved")
    return collect_registry_host_delivery_diagnosis(
        registry_path=registry_path, max_lanes=max_lanes
    )


HOST_DELIVERY_CHECK_ID = "host_prompt_delivery_windows"


def host_delivery_diagnosis_check(diagnosis: Mapping[str, Any]) -> dict[str, Any]:
    """Project one diagnosis onto the doctor check vocabulary.

    Two outcomes fail the check: a missed window, and a run that could not
    measure every lane. A lane with no comparable tick is reported as unverified
    in the detail only: absence of a receipt is not proof the host stopped
    delivering, and claiming a fault would teach operators to ignore this check
    on the first install.
    """

    counts = diagnosis.get("counts") or {}
    status = str(diagnosis.get("status") or "not_applicable")
    # A run that skipped lanes cannot claim the workspace is observed. That is a
    # gap in this readback rather than a host fault, so it is named in the
    # detail and turns the optional check red without failing `doctor`.
    coverage_complete = not any(
        diagnosis.get(key)
        for key in ("truncated", "unmeasured_bindings", "unreadable_activation_goals")
    )
    detail = (
        f"status={status};reason={diagnosis.get('reason')};"
        f"stale={counts.get('stale', 0)};fresh={counts.get('fresh', 0)};"
        f"unverified={counts.get('missing', 0) + counts.get('unknown', 0)};"
        f"lanes={diagnosis.get('lane_count', 0)};"
        f"truncated={str(bool(diagnosis.get('truncated'))).lower()};"
        f"incomplete_goals={diagnosis.get('incomplete_goals', 0)};"
        f"unmeasured_bindings={diagnosis.get('unmeasured_bindings', 0)};"
        f"unreadable_activation_goals="
        f"{diagnosis.get('unreadable_activation_goals', 0)}"
    )
    stale = diagnosis.get("stale_lanes") or []
    if stale:
        last_ticks = {
            lane.get("lane"): lane.get("last_observed_at")
            for lane in diagnosis.get("lanes") or []
            if lane.get("window_state") == HostDeliveryWindowState.STALE.value
        }
        detail += ";missed=" + ",".join(
            f"{lane}@{last_ticks.get(lane) or 'unknown'}" for lane in stale[:5]
        )
    return {
        "id": HOST_DELIVERY_CHECK_ID,
        "required": False,
        "ok": status != "stale" and coverage_complete,
        "detail": detail,
    }
