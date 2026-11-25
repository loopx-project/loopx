"""Admission evaluation, effect-time gates, and transition preview builders."""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import TypedDict

from ...domain_packs.issue_fix import issue_fix_maintainer_correction_entries
from .correction_settlement_input import (
    BOT_LOGIN_SUFFIX,
    ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION,
    ISSUE_FIX_MAINTAINER_CORRECTION_LEDGER_ENTRY_SCHEMA_VERSION,
    TERMINAL_PR_STATES,
    _normalise_event_timestamp,
    _timestamp,
    _upper_label,
    build_issue_fix_transition_preview,
    issue_fix_maintainer_correction_operation_id,
)
from .github_public import MAINTAINER_ASSOCIATIONS as MAINTAINER_ASSOCIATIONS


class _CorrectionTodoIntent(TypedDict, total=False):
    role: str
    text: str
    task_class: str
    action_kind: str
    required_write_scopes: list[str]


def _same_instant(left: object, right: object) -> bool:
    left_time = _timestamp(left)
    right_time = _timestamp(right)
    return left_time is not None and right_time is not None and left_time == right_time


def _is_bot_or_self_login(login: object) -> bool:
    text = str(login or "").strip().lower()
    return text.endswith(BOT_LOGIN_SUFFIX)


def _source_event_updated_at(entry: Mapping[str, object]) -> object:
    event = entry.get("source_event")
    return event.get("updated_at") if isinstance(event, Mapping) else None


def _observed_source_events(
    source_events: Mapping[str, object] | None,
    source_event: Mapping[object, object],
) -> list[Mapping[object, object]]:
    raw_events = source_events.get("events") if isinstance(source_events, Mapping) else []
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        return []
    return [
        event
        for event in raw_events
        if isinstance(event, Mapping)
        and event.get("kind") == source_event.get("kind")
        and str(event.get("id")) == str(source_event.get("id"))
    ]


def _matching_observed_event_revision(
    observed: Sequence[Mapping[object, object]],
    updated_at: object,
) -> Mapping[object, object] | None:
    return next(
        (
            event
            for event in observed
            if _same_instant(event.get("updated_at"), updated_at)
        ),
        None,
    )


def _latest_accepted_revision(
    prior_entries: Sequence[Mapping[str, object]],
    *,
    source_event: Mapping[object, object],
) -> dict[str, object] | None:
    latest: dict[str, object] | None = None
    for entry in prior_entries:
        event = entry.get("source_event")
        if not isinstance(event, Mapping):
            continue
        if event.get("kind") != source_event.get("kind") or str(
            event.get("id")
        ) != str(source_event.get("id")):
            continue
        if entry.get("disposition") not in {"accepted", "superseded"}:
            continue
        candidate_time = _timestamp(event.get("updated_at"))
        if candidate_time is None:
            continue
        latest_time = (
            _timestamp(_source_event_updated_at(latest)) if latest is not None else None
        )
        if latest_time is None or candidate_time > latest_time:
            latest = dict(entry)
    return latest


def evaluate_issue_fix_maintainer_correction_admission(
    correction: Mapping[str, object],
    *,
    observation: Mapping[str, object],
    source_events: Mapping[str, object] | None,
    prior_entries: Sequence[Mapping[str, object]] | None,
    prior_ledger_read: bool,
    recorded_at: str | None,
) -> dict[str, object]:
    """Run the deterministic admission gates for one maintainer correction.

    Returns the admission record plus the ledger entries this run touches:
    the entry for this correction and any earlier revision it supersedes.
    """

    repo = str(observation.get("repo") or "")
    pr_ref = str(observation.get("pr_ref") or "")
    operation_id = issue_fix_maintainer_correction_operation_id(
        correction, repo=repo, pr_ref=pr_ref
    )
    source_event = correction.get("source_event")
    admission_basis = str(correction.get("admission_basis") or "host_asserted")
    prior = issue_fix_maintainer_correction_entries(
        {"maintainer_corrections": prior_entries}
    )
    existing_entry = next(
        (entry for entry in prior if entry.get("operation_id") == operation_id), None
    )
    gates: list[dict[str, object]] = []
    reason: str | None = None
    verified_event: dict[str, object] | None = None
    provider_events_status = (
        str(source_events.get("status") or "unavailable")
        if isinstance(source_events, Mapping)
        else "unavailable"
    )

    def record(name: str, passed: bool, failure_reason: str | None = None) -> None:
        nonlocal reason
        gates.append({"gate": name, "passed": passed})
        if not passed and reason is None:
            reason = failure_reason

    if isinstance(source_event, Mapping):
        observed = _observed_source_events(source_events, source_event)
        matched_event = (
            _matching_observed_event_revision(observed, source_event.get("updated_at"))
            if observed
            else None
        )
        if not observed:
            record("source_event_observed", False, "source_event_not_observed")
        elif matched_event is None:
            record("source_event_observed", False, "source_event_revision_mismatch")
        else:
            record("source_event_observed", True)
            verified_event = {
                **{str(k): v for k, v in source_event.items()},
                "author_login": str(
                    matched_event.get("author_login")
                    or source_event.get("author_login")
                ),
                "author_association": _upper_label(
                    matched_event.get("author_association"),
                    str(source_event.get("author_association") or "NONE"),
                ),
                "created_at": _normalise_event_timestamp(
                    matched_event.get("created_at") or source_event.get("created_at"),
                    field="source_event created_at",
                ),
            }
        effective_dict: dict[str, object] = verified_event or {
            str(k): v for k, v in source_event.items()
        }
        effective_event: dict[str, object] | None = effective_dict
        latest_prior = _latest_accepted_revision(prior, source_event=source_event)
        prior_time = (
            _timestamp(_source_event_updated_at(latest_prior))
            if latest_prior is not None
            else None
        )
        incoming_time = _timestamp(source_event.get("updated_at"))
        revision_relation = "first"
        if prior_time is not None and incoming_time is not None:
            if incoming_time < prior_time:
                revision_relation = "stale"
            elif incoming_time == prior_time:
                revision_relation = "replay"
            else:
                revision_relation = "newer"
        record("revision_not_stale", revision_relation != "stale", "stale_revision")
        record(
            "not_self_or_bot_author",
            not _is_bot_or_self_login(effective_dict.get("author_login")),
            "self_or_bot_author",
        )
        record(
            "author_is_maintainer",
            _upper_label(effective_dict.get("author_association"), "NONE")
            in MAINTAINER_ASSOCIATIONS,
            "author_not_maintainer",
        )
    else:
        effective_event = None
        latest_prior = None
        revision_relation = "replay" if existing_entry else "first"
    record(
        "pr_state_not_terminal",
        str(observation.get("state") or "") not in TERMINAL_PR_STATES,
        "terminal_pr_state",
    )

    accepted = reason is None
    replay = existing_entry is not None and (
        not accepted or existing_entry.get("disposition") in {"accepted", "superseded"}
    )
    if existing_entry is not None and replay:
        entry = dict(existing_entry)
    else:
        entry = {
            "schema_version": ISSUE_FIX_MAINTAINER_CORRECTION_LEDGER_ENTRY_SCHEMA_VERSION,
            "operation_id": operation_id,
            "source_event": effective_event,
            "correction_fingerprint": correction.get("correction_fingerprint"),
            "correction_kind": correction.get("correction_kind"),
            "admission_basis": admission_basis,
            "disposition": "accepted" if accepted else "context_only",
            "reason": reason,
            "todo_id": None,
            "accepted_head_sha": observation.get("head_commit_ref") if accepted else None,
            "recorded_at": recorded_at,
            "superseded_by": None,
            "body_captured": False,
        }
    touched_entries = [entry]
    superseded_operation_id = None
    if (
        accepted
        and revision_relation == "newer"
        and latest_prior is not None
        and latest_prior.get("operation_id") != operation_id
    ):
        superseded_operation_id = str(latest_prior.get("operation_id"))
        touched_entries.append(
            {
                **latest_prior,
                "disposition": "superseded",
                "superseded_by": operation_id,
            }
        )
    return {
        "schema_version": ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION,
        "operation_id": operation_id,
        "admission_basis": admission_basis,
        "disposition": "accepted" if accepted else "context_only",
        "reason": reason,
        "host_summary_mismatch": bool(
            existing_entry is not None
            and correction.get("correction_fingerprint")
            != existing_entry.get("correction_fingerprint")
        ),
        "gates": gates,
        "revision_relation": revision_relation,
        "replay": replay,
        "supersedes_operation_id": superseded_operation_id,
        "provider_events_status": provider_events_status,
        "prior_ledger_read": bool(prior_ledger_read),
        "source_event": effective_event,
        "entries": touched_entries,
        "body_captured": False,
    }


def recheck_issue_fix_maintainer_correction_effect_gates(
    entry: Mapping[str, object],
    *,
    observation: Mapping[str, object],
    ledger_entries: Sequence[Mapping[str, object]] | None,
    goal_stopped: bool,
    source_events: Mapping[str, object] | None = None,
) -> str | None:
    """Re-run the effect-time gates for an accepted entry just before its Todo write."""

    source_event = entry.get("source_event")
    if isinstance(source_event, Mapping):
        observed = _observed_source_events(source_events, source_event)
        if not observed:
            return "source_event_not_observed"
        current_event = _matching_observed_event_revision(
            observed, source_event.get("updated_at")
        )
        if current_event is None:
            return "source_event_revision_mismatch"
        latest = _latest_accepted_revision(
            issue_fix_maintainer_correction_entries(
                {"maintainer_corrections": ledger_entries}
            ),
            source_event=source_event,
        )
        if latest is not None and latest.get("operation_id") != entry.get("operation_id"):
            latest_time = _timestamp(_source_event_updated_at(latest))
            own_time = _timestamp(source_event.get("updated_at"))
            if latest_time is not None and own_time is not None and own_time < latest_time:
                return "stale_revision"
        if _is_bot_or_self_login(current_event.get("author_login")):
            return "self_or_bot_author"
        if (
            _upper_label(current_event.get("author_association"), "NONE")
            not in MAINTAINER_ASSOCIATIONS
        ):
            return "author_not_maintainer"
    if str(observation.get("state") or "") in TERMINAL_PR_STATES:
        return "terminal_pr_state"
    if goal_stopped:
        return "goal_not_active"
    return None


def maintainer_correction_transition(
    correction: Mapping[str, object],
    *,
    pr_permalink: str,
) -> dict[str, object]:
    """Build the transition preview dict for one admitted maintainer correction."""

    correction_kind = str(correction.get("correction_kind"))
    source_ref = str(correction.get("source_ref"))
    summary = str(correction.get("summary"))
    source_event = correction.get("source_event")
    # Todo creation dedupes identical active text, so distinct provider events
    # or revisions with the same wording must name the event they came from.
    event_suffix = (
        f" Source event: {source_event.get('kind')} {source_event.get('id')}"
        f" updated {source_event.get('updated_at')}."
        if isinstance(source_event, Mapping)
        else ""
    )
    if correction_kind == "actionable_patch":
        verification_plan = str(correction.get("verification_plan"))
        pr_update_path = str(correction.get("pr_update_path"))
        return build_issue_fix_transition_preview(
            decision="runnable_successor",
            action_kind="issue_fix_maintainer_correction_patch",
            priority="P0",
            task_class="advancement_task",
            reason="Maintainer correction is bounded and actionable; create one claimed patch successor.",
            text=(
                f"[P0] Apply maintainer correction for {pr_permalink} from {source_ref}: "
                f"{summary} Verify: {verification_plan} Update PR via: {pr_update_path}"
                f"{event_suffix}"
            ),
            material_change=True,
            required_write_scopes=["write", "publish"],
        )
    if correction_kind == "semantic_ambiguity":
        return build_issue_fix_transition_preview(
            decision="user_gate",
            action_kind="clarify_issue_fix_maintainer_correction",
            priority="P0",
            role="user",
            task_class="user_gate",
            reason="Maintainer correction changes or leaves the intended behavior ambiguous.",
            text=(
                f"[P0] Clarify maintainer correction for {pr_permalink} from {source_ref}: "
                f"{correction.get('user_question')} Context: {summary}{event_suffix}"
            ),
            material_change=True,
        )
    if correction_kind == "missing_authority":
        raw_scopes = correction.get("missing_authority_scopes")
        if raw_scopes is None:
            scope_list: list[object] = []
        elif isinstance(raw_scopes, Iterable) and not isinstance(raw_scopes, (str, bytes)):
            scope_list = list(raw_scopes)
        else:
            raise TypeError("missing_authority_scopes must be a sequence")
        scopes = ", ".join(str(value) for value in scope_list)
        return build_issue_fix_transition_preview(
            decision="user_gate",
            action_kind="grant_issue_fix_maintainer_correction_authority",
            priority="P0",
            role="user",
            task_class="user_gate",
            reason="The correction is understood but the required write authority is not active.",
            text=(
                f"[P0] Grant or decline [{scopes}] authority for maintainer correction "
                f"{source_ref} on {pr_permalink}: {summary}{event_suffix}"
            ),
            material_change=True,
            missing_authority_scopes=scope_list,
        )
    return build_issue_fix_transition_preview(
        decision="monitor_continuation",
        action_kind="issue_fix_maintainer_correction_unchanged_monitor",
        priority="P2",
        reason="Maintainer correction is unchanged; keep the monitor quiet and create no successor.",
    )


def attach_correction_todo_intent(
    admission: Mapping[str, object],
    transition: Mapping[str, object],
) -> None:
    """Attach the pending Todo intent onto the lead admitted ledger entry."""

    raw_entries = admission.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries or not isinstance(
        raw_entries[0], dict
    ):
        raise TypeError("maintainer_correction_admission.entries[0] must be a dict")
    role = str(transition.get("role") or "")
    raw_scopes = transition.get("required_write_scopes")
    if role == "agent":
        if raw_scopes is None:
            write_scopes: list[str] | None = []
        elif isinstance(raw_scopes, Sequence) and not isinstance(raw_scopes, (str, bytes)):
            write_scopes = [str(scope) for scope in raw_scopes]
        else:
            raise TypeError("required_write_scopes must be a sequence")
    else:
        write_scopes = None
    raw_entries[0]["todo_intent"] = {
        "role": transition["role"],
        "text": transition["text"],
        "task_class": transition["task_class"],
        "action_kind": transition["action_kind"],
        "required_write_scopes": write_scopes,
    }


def _goal_stopped_for_correction(registry_path: Path | None, goal_id: str | None) -> bool:
    if registry_path is None or not goal_id:
        return False
    from ...agent_registry import load_goal_from_registry
    from ...control_plane.goals.activation import goal_is_stopped

    try:
        goal = load_goal_from_registry(registry_path, goal_id)
        if goal is None:
            return True
        return goal_is_stopped(goal)
    except ValueError:
        # An unreadable activation record cannot prove the Goal is active.
        return True


def _write_maintainer_correction_todo(
    *,
    registry_path: Path,
    goal_id: str,
    claimed_by: str,
    project: Path,
    operation_id: str,
    intent: Mapping[str, object],
) -> dict[str, object]:
    from ...control_plane.todos.mutation_api import add_goal_todo

    role = str(intent.get("role") or "agent")
    raw_scopes = intent.get("required_write_scopes")
    if role == "agent":
        if raw_scopes is None:
            write_scopes: list[str] | None = []
        elif isinstance(raw_scopes, Sequence) and not isinstance(raw_scopes, (str, bytes)):
            write_scopes = [str(scope) for scope in raw_scopes]
        else:
            raise TypeError("required_write_scopes must be a sequence")
    else:
        write_scopes = None
    return add_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        role=role,
        text=str(intent.get("text") or ""),
        task_class=str(intent.get("task_class") or "advancement_task"),
        action_kind=str(intent.get("action_kind") or ""),
        required_write_scopes=write_scopes,
        claimed_by=claimed_by if role == "agent" else None,
        agent_id=claimed_by if role == "user" else None,
        project=project,
        operation_id=operation_id,
    )


def _maintainer_correction_todo_write_receipt(
    todo_write: Mapping[str, object], *, operation_id: str
) -> dict[str, object]:
    todo_changed = bool(
        todo_write.get("added")
        or todo_write.get("metadata_updated")
        or todo_write.get("status_changed")
    )
    raw_todo = todo_write.get("todo")
    created_todo: Mapping[object, object] = raw_todo if isinstance(raw_todo, dict) else {}
    return {
        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
        "write_performed": todo_changed,
        "already_exists": bool(todo_write.get("already_exists")),
        "todo_id": todo_write.get("todo_id"),
        "operation_id": operation_id,
        "role": todo_write.get("role"),
        "claimed_by": created_todo.get("claimed_by"),
        "blocks_agent": created_todo.get("blocks_agent"),
        "path_recorded": False,
    }


def _correction_todo_readback_receipt(
    *, skip_reason: str, operation_id: str | None = None, todo_id: str | None = None
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "issue_fix_maintainer_correction_todo_write_v0",
        "write_performed": False,
        "already_exists": bool(todo_id),
        "skip_reason": skip_reason,
        "path_recorded": False,
    }
    if operation_id is not None:
        receipt["operation_id"] = operation_id
    if todo_id is not None:
        receipt["todo_id"] = todo_id
    return receipt
