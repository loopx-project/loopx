"""Maintainer-correction admission, transition, settlement, and origin-request binding."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import os
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict, cast

from ...control_plane.projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from ...control_plane.runtime.time import now_utc_iso
from ...domain_packs.issue_fix import (
    issue_fix_maintainer_correction_entries as issue_fix_maintainer_correction_entries,
    read_issue_fix_pr_lifecycle_ledger_row as read_issue_fix_pr_lifecycle_ledger_row,
    upsert_issue_fix_pr_lifecycle_ledger_jsonl as upsert_issue_fix_pr_lifecycle_ledger_jsonl,
)
from .correction_settlement_gates import (
    _correction_todo_readback_receipt as _correction_todo_readback_receipt,
    _CorrectionTodoIntent as _CorrectionTodoIntent,
    _goal_stopped_for_correction as _goal_stopped_for_correction,
    _maintainer_correction_todo_write_receipt as _maintainer_correction_todo_write_receipt,
    _write_maintainer_correction_todo as _write_maintainer_correction_todo,
    attach_correction_todo_intent as attach_correction_todo_intent,
    evaluate_issue_fix_maintainer_correction_admission as evaluate_issue_fix_maintainer_correction_admission,
    maintainer_correction_transition as maintainer_correction_transition,
    recheck_issue_fix_maintainer_correction_effect_gates as recheck_issue_fix_maintainer_correction_effect_gates,
)
from .correction_settlement_input import (
    _SOURCE_EVENT_REFUSALS,
    BOT_LOGIN_SUFFIX as BOT_LOGIN_SUFFIX,
    ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION as ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION,
    ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION as ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION,
    ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1 as ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1,
    ISSUE_FIX_MAINTAINER_CORRECTION_LEDGER_ENTRY_SCHEMA_VERSION as ISSUE_FIX_MAINTAINER_CORRECTION_LEDGER_ENTRY_SCHEMA_VERSION,
    MAINTAINER_CORRECTION_ADMISSION_BASES as MAINTAINER_CORRECTION_ADMISSION_BASES,
    MAINTAINER_CORRECTION_DISPOSITIONS as MAINTAINER_CORRECTION_DISPOSITIONS,
    MAINTAINER_CORRECTION_FAULT_ENV as MAINTAINER_CORRECTION_FAULT_ENV,
    MAINTAINER_CORRECTION_INPUT_FIELDS as MAINTAINER_CORRECTION_INPUT_FIELDS,
    MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSIONS as MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSIONS,
    MAINTAINER_CORRECTION_KINDS as MAINTAINER_CORRECTION_KINDS,
    MAINTAINER_CORRECTION_OPERATION_ID_PREFIX as MAINTAINER_CORRECTION_OPERATION_ID_PREFIX,
    MAINTAINER_CORRECTION_REJECTION_REASONS as MAINTAINER_CORRECTION_REJECTION_REASONS,
    MAINTAINER_CORRECTION_SOURCE_EVENT_FIELDS as MAINTAINER_CORRECTION_SOURCE_EVENT_FIELDS,
    MAINTAINER_CORRECTION_SOURCE_EVENT_KINDS as MAINTAINER_CORRECTION_SOURCE_EVENT_KINDS,
    MAINTAINER_CORRECTION_SOURCE_KINDS as MAINTAINER_CORRECTION_SOURCE_KINDS,
    TERMINAL_PR_STATES as TERMINAL_PR_STATES,
    WRITE_SCOPES as WRITE_SCOPES,
    _timestamp as _timestamp,
    _upper_label as _upper_label,
    build_issue_fix_transition_preview as build_issue_fix_transition_preview,
    issue_fix_maintainer_correction_operation_id as issue_fix_maintainer_correction_operation_id,
    normalise_issue_fix_maintainer_correction_input as normalise_issue_fix_maintainer_correction_input,
    normalise_issue_fix_maintainer_correction_source_event as normalise_issue_fix_maintainer_correction_source_event,
    normalise_issue_fix_pr_source_events as normalise_issue_fix_pr_source_events,
)
from .github_public import MAINTAINER_ASSOCIATIONS as MAINTAINER_ASSOCIATIONS

__all__ = [
    "BOT_LOGIN_SUFFIX",
    "ISSUE_FIX_MAINTAINER_CORRECTION_ADMISSION_SCHEMA_VERSION",
    "ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION",
    "ISSUE_FIX_MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSION_V1",
    "ISSUE_FIX_MAINTAINER_CORRECTION_LEDGER_ENTRY_SCHEMA_VERSION",
    "MAINTAINER_ASSOCIATIONS",
    "MAINTAINER_CORRECTION_ADMISSION_BASES",
    "MAINTAINER_CORRECTION_DISPOSITIONS",
    "MAINTAINER_CORRECTION_FAULT_ENV",
    "MAINTAINER_CORRECTION_INPUT_FIELDS",
    "MAINTAINER_CORRECTION_INPUT_SCHEMA_VERSIONS",
    "MAINTAINER_CORRECTION_KINDS",
    "MAINTAINER_CORRECTION_OPERATION_ID_PREFIX",
    "MAINTAINER_CORRECTION_REJECTION_REASONS",
    "MAINTAINER_CORRECTION_SOURCE_EVENT_FIELDS",
    "MAINTAINER_CORRECTION_SOURCE_EVENT_KINDS",
    "MAINTAINER_CORRECTION_SOURCE_KINDS",
    "TERMINAL_PR_STATES",
    "WRITE_SCOPES",
    "attach_correction_todo_intent",
    "build_issue_fix_transition_preview",
    "evaluate_issue_fix_maintainer_correction_admission",
    "issue_fix_maintainer_correction_operation_id",
    "maintainer_correction_transition",
    "normalise_issue_fix_maintainer_correction_input",
    "normalise_issue_fix_maintainer_correction_source_event",
    "normalise_issue_fix_pr_source_events",
    "recheck_issue_fix_maintainer_correction_effect_gates",
]


class _OriginRequestIdentity(TypedDict):
    goal_id: str
    agent_id: str
    goal_ref: NotRequired[dict[str, str]]


class _ReplyStatusFn(Protocol):
    def __call__(
        self, root: Path, row: Mapping[str, object]
    ) -> Sequence[Mapping[str, object]]: ...


class _ReportFn(Protocol):
    def __call__(
        self,
        root: Path,
        goal_id: str,
        agent_id: str,
        request_id: str,
        phase: str,
        text: str,
        *,
        registry: Path | None = ...,
        update_id: str | None = ...,
        workspace: Path | None = ...,
        request_revision: int | None = ...,
        caller_goal_ref: Mapping[str, str] | None = ...,
    ) -> Mapping[str, object]: ...


def _require_origin_registry(registry_path: Path) -> None:
    """Check the origin profile before looking up the request in its store."""
    try:
        profile = load_project_registry(registry_path).get("profile_id")
    except (ValueError, OSError) as exc:
        raise ValueError("origin registry is unreadable") from exc
    if profile != SOURCE_SESSION_PROFILE_ID:
        raise ValueError(f"origin registry requires {SOURCE_SESSION_PROFILE_ID}")


def _resolve_origin_request_identity(
    runtime_root: Path, request_id: str
) -> _OriginRequestIdentity:
    paths = list(
        (runtime_root / ".local/manager-context/entries").glob(f"*/{request_id}.json")
    )
    if not paths:
        raise ValueError("origin_request_not_found")
    if len(paths) != 1:
        raise ValueError("origin_request_ambiguous")
    try:
        entry = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("origin_request_not_found") from exc
    if not isinstance(entry, dict):
        raise ValueError("origin_request_not_found")
    if isinstance(entry.get("recipient"), dict):
        raise ValueError("origin_request_unsupported_target")
    goal_id = entry.get("goal_id")
    agent_id = entry.get("agent_id")
    if (
        not isinstance(goal_id, str)
        or not goal_id
        or not isinstance(agent_id, str)
        or not agent_id
    ):
        raise ValueError("origin_request_unsupported_target")
    identity: _OriginRequestIdentity = {"goal_id": goal_id, "agent_id": agent_id}
    raw_goal_ref = entry.get("goal_ref")
    if isinstance(raw_goal_ref, dict):
        identity["goal_ref"] = {
            str(key): str(val) for key, val in raw_goal_ref.items()
        }
    return identity


def _str_mapping(raw: object) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        return {}
    return {str(key): str(val) for key, val in raw.items()}


def _entry_returns(entry: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = entry.get("returns")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TypeError("maintainer correction entry returns must be a list")
    return [item for item in raw if isinstance(item, Mapping)]


def _inspect_origin_request(
    origin: Mapping[object, object],
) -> tuple[
    _OriginRequestIdentity | None,
    dict[str, str],
    Mapping[str, object] | None,
    str | None,
]:
    from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
    from ...control_plane.collaboration.request_control_source import execute_request_control

    identity: _OriginRequestIdentity | None = None
    goal_ref: dict[str, str] = {}
    view: Mapping[str, object] | None = None
    origin_refusal: str | None = None
    try:
        origin_runtime_raw = origin["runtime_root"]
        origin_request_raw = origin["request_id"]
        origin_registry_raw = origin["registry_path"]
        if (
            not isinstance(origin_runtime_raw, str)
            or not isinstance(origin_request_raw, str)
            or not isinstance(origin_registry_raw, str)
        ):
            raise ValueError("origin request fields must be strings")
        identity = _resolve_origin_request_identity(
            Path(origin_runtime_raw), origin_request_raw
        )
        if (
            identity["goal_id"] != origin.get("goal_id")
            or identity["agent_id"] != origin.get("agent_id")
        ):
            raise ValueError("origin request identity changed")
        with collaboration_goal_scope(
            Path(origin_registry_raw),
            goal_id=identity["goal_id"],
            agents=(),
            require_active=False,
        ) as scope:
            if scope.profile_id != SOURCE_SESSION_PROFILE_ID:
                raise ValueError("origin registry profile changed")
            resolved_ref = (
                identity.get("goal_ref")
                or scope.current_goal_ref
                or {"goal_id": identity["goal_id"]}
            )
            goal_ref = _str_mapping(resolved_ref)
        stored_goal_ref = origin.get("goal_ref")
        if stored_goal_ref and resolved_ref != stored_goal_ref:
            raise ValueError("origin request identity changed")
        result = execute_request_control(
            registry_path=Path(origin_registry_raw),
            runtime_root=Path(origin_runtime_raw),
            goal_ref=goal_ref,
            receiver_agent_id=identity["agent_id"],
            request_id=origin_request_raw,
            actor_agent_id=identity["agent_id"],
            operation="inspect",
            execute=False,
        )
        if not result.get("ok"):
            raise ValueError("request inspection failed")
        raw_view = result["view"]
        if not isinstance(raw_view, Mapping) or not raw_view.get("control_state"):
            raise ValueError("request control state unavailable")
        view = {str(k): v for k, v in raw_view.items()}
        if view["control_state"] in {"cancelled", "cancellation_pending"}:
            origin_refusal = "origin_request_cancelled"
    except (OSError, ValueError, KeyError):
        origin_refusal = "origin_request_unreadable"
    return identity, goal_ref, view, origin_refusal


def _deliver_origin_returns(
    *,
    payload: dict[str, object],
    entries: list[dict[str, object]],
    origin: Mapping[str, object],
    identity: _OriginRequestIdentity | None,
    goal_ref: Mapping[str, str],
    view: Mapping[str, object] | None,
    origin_refusal: str | None,
    observation: Mapping[str, object],
    ledger_path: Path,
    registry_path: Path,
    goal_id: str,
    project: Path,
) -> None:
    from ...control_plane.collaboration import inbox as _inbox
    from ...control_plane.todos.list_readback import list_goal_todos
    from ..manager_context import roundtrip as _roundtrip

    reply_status_fn = cast(_ReplyStatusFn, _inbox.reply_status)
    report_fn = cast(_ReportFn, _roundtrip.report)
    origin_root_raw = origin.get("runtime_root")
    origin_registry_raw = origin.get("registry_path")
    origin_request_raw = origin.get("request_id")
    todos: dict[str, Mapping[object, object]] = {}
    if not origin_refusal and identity is not None:
        raw_todos = list_goal_todos(registry_path=registry_path, goal_id=goal_id).get(
            "todos"
        )
        if isinstance(raw_todos, Sequence) and not isinstance(raw_todos, (str, bytes)):
            todos = {
                str(todo.get("todo_id")): todo
                for todo in raw_todos
                if isinstance(todo, Mapping) and isinstance(todo.get("todo_id"), str)
            }
    head = str(observation.get("head_commit_ref") or "")
    for entry in entries:
        todo_id = entry.get("todo_id")
        if (
            origin_refusal
            or identity is None
            or not view
            or not isinstance(origin_root_raw, str)
            or not origin_root_raw
            or not isinstance(origin_registry_raw, str)
            or not origin_registry_raw
            or not isinstance(origin_request_raw, str)
            or not origin_request_raw
            or entry.get("disposition") != "accepted"
            or not isinstance(todo_id, str)
            or not todo_id
            or todos.get(todo_id, {}).get("status") != "done"
        ):
            continue
        origin_root = Path(origin_root_raw)
        origin_registry = Path(origin_registry_raw)
        origin_request_id = origin_request_raw
        raw_returns = entry.setdefault("returns", [])
        if not isinstance(raw_returns, list):
            raise TypeError("maintainer correction entry returns must be a list")
        operation_id = str(entry["operation_id"])
        raw_pending = entry.get("pending_return")
        pending_record: dict[str, object] | None = (
            dict(raw_pending)
            if (
                isinstance(raw_pending, Mapping)
                and isinstance(raw_pending.get("head_sha"), str)
                and isinstance(raw_pending.get("text"), str)
                and isinstance(raw_pending.get("request_revision"), int)
            )
            else None
        )
        pending_head = (
            str(pending_record["head_sha"]) if pending_record is not None else None
        )
        # Finish the immutable publication before a newer observation can
        # replace it, and record the return against its original full head.
        for return_head in dict.fromkeys((pending_head, head)):
            if (
                not return_head
                or return_head == entry.get("accepted_head_sha")
                or any(
                    isinstance(r, Mapping) and r.get("head_sha") == return_head
                    for r in raw_returns
                )
            ):
                continue
            update_id = f"issue-fix-correction:{operation_id}:{return_head}"
            if return_head != pending_head or pending_record is None:
                raw_event = entry.get("source_event")
                event: Mapping[object, object] = (
                    raw_event if isinstance(raw_event, Mapping) else {}
                )
                raw_checks = observation.get("checks")
                checks: Mapping[object, object] = (
                    raw_checks if isinstance(raw_checks, Mapping) else {}
                )
                text = (
                    f"Maintainer correction {entry.get('correction_kind')} "
                    f"({event.get('kind')} {event.get('id')}) applied in "
                    f"{observation.get('permalink')} at head {return_head[:12]}; "
                    f"PR state {observation.get('state')}, checks {checks.get('aggregate')}; "
                    f"todo {todo_id} done."
                )
                # Persist the entire publication before report(), including
                # the first conclusion's absence of an update_id.
                pending_record = {
                    "schema_version": "issue_fix_maintainer_correction_publication_v1",
                    "head_sha": return_head,
                    "update_id": update_id
                    if any(
                        r.get("phase") == "conclusion"
                        for r in reply_status_fn(origin_root, origin)
                    )
                    else None,
                    "text": text,
                    "request_revision": view["revision"],
                    "phase": "conclusion",
                }
                entry["pending_return"] = pending_record
                upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
            pending_update_id = pending_record.get("update_id")
            pending_revision = pending_record.get("request_revision")
            result = report_fn(
                origin_root,
                identity["goal_id"],
                identity["agent_id"],
                origin_request_id,
                str(pending_record.get("phase") or "conclusion"),
                str(pending_record["text"]),
                registry=origin_registry,
                update_id=str(pending_update_id) if pending_update_id is not None else None,
                workspace=project,
                request_revision=pending_revision if isinstance(pending_revision, int) else None,
                caller_goal_ref=goal_ref,
            )
            entry["pending_return"] = None
            raw_returns.append(
                {
                    "update_id": update_id,
                    "result_key": result.get("result_key", "conclusion"),
                    "enqueued_at": now_utc_iso(),
                    "head_sha": return_head,
                }
            )
            upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
    statuses = (
        {
            str(r.get("result_key", r.get("phase"))): str(r.get("status"))
            for r in reply_status_fn(Path(origin_root_raw), origin)
        }
        if isinstance(origin_root_raw, str)
        and origin_root_raw
        and isinstance(origin_request_raw, str)
        and origin_request_raw
        else {}
    )
    payload["return_delivery"] = [
        {
            "operation_id": e.get("operation_id"),
            "update_id": r.get("update_id"),
            "status": statuses.get(str(r.get("result_key")), "explicit_unverified"),
        }
        for e in entries
        for r in _entry_returns(e)
    ]


def _settle_maintainer_correction_entries(
    *,
    payload: dict[str, object],
    ledger_path: Path,
    registry_path: Path,
    goal_id: str,
    claimed_by: str,
    project: Path,
    source_events: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Write Todos for accepted ledger entries that still lack a todo_id.

    Each accepted entry stores its own `todo_intent`, so an entry left pending
    by an interrupted run is retried under its original operation id on any
    later run. Every candidate is re-gated against the ledger as it stands
    now, the same observation, and the Goal activation state.
    """

    raw_observation = payload.get("observation")
    observation: Mapping[str, object] = (
        raw_observation if isinstance(raw_observation, dict) else {}
    )
    raw_admission = payload.get("maintainer_correction_admission")
    admission: dict[str, object] = (
        raw_admission if isinstance(raw_admission, dict) else {}
    )
    current_operation_id = str(admission.get("operation_id") or "")
    goal_stopped = _goal_stopped_for_correction(registry_path, goal_id)
    repo = str(observation.get("repo") or "")
    pr_ref = str(observation.get("pr_ref") or "")

    row = read_issue_fix_pr_lifecycle_ledger_row(ledger_path, repo=repo, pr_ref=pr_ref)
    ledger_entries = issue_fix_maintainer_correction_entries(row)
    if source_events is None:
        pending_source_events = payload.pop("_maintainer_correction_source_events", None)
        source_events = (
            pending_source_events if isinstance(pending_source_events, dict) else None
        )
    raw_origin = (row or {}).get("origin_request")
    origin_map: dict[str, object] | None = (
        {str(k): v for k, v in raw_origin.items()}
        if isinstance(raw_origin, Mapping)
        else None
    )
    identity: _OriginRequestIdentity | None = None
    goal_ref: dict[str, str] = {}
    view: Mapping[str, object] | None = None
    origin_refusal: str | None = None
    if raw_origin:
        if not isinstance(raw_origin, Mapping):
            origin_refusal = "origin_request_unreadable"
        else:
            identity, goal_ref, view, origin_refusal = _inspect_origin_request(
                raw_origin
            )

    settlements: list[dict[str, object]] = []
    current_todo_write: dict[str, object] | None = None
    changed_entries: list[dict[str, object]] = []
    for entry in ledger_entries:
        if (
            raw_origin
            and entry.get("disposition") == "accepted"
            and "accepted_head_sha" not in entry
        ):
            entry["accepted_head_sha"] = observation.get("head_commit_ref")
            changed_entries.append(dict(entry))
        operation_id = str(entry.get("operation_id") or "")
        is_current = operation_id == current_operation_id
        intent = entry.get("todo_intent")
        if (
            entry.get("disposition") != "accepted"
            or not isinstance(intent, dict)
            or (entry.get("todo_id") and not is_current)
        ):
            continue
        refusal = recheck_issue_fix_maintainer_correction_effect_gates(
            entry,
            observation=observation,
            ledger_entries=ledger_entries,
            goal_stopped=goal_stopped,
            source_events=source_events,
        )
        refusal = origin_refusal or refusal
        raw_todo_id = entry.get("todo_id")
        todo_id_str = raw_todo_id if isinstance(raw_todo_id, str) else None
        if refusal is not None and not todo_id_str:
            if refusal in _SOURCE_EVENT_REFUSALS:
                settlements.append(
                    {"operation_id": operation_id, "status": "refused", "reason": refusal}
                )
                if is_current:
                    current_todo_write = _correction_todo_readback_receipt(
                        skip_reason=refusal, operation_id=operation_id
                    )
                continue
            if is_current and origin_refusal:
                admission["disposition"] = "context_only"
                admission["reason"] = refusal
            changed_entries.append(
                {
                    **entry,
                    "disposition": "context_only",
                    "reason": refusal,
                    "effect_recheck": "refused",
                }
            )
            settlements.append(
                {"operation_id": operation_id, "status": "refused", "reason": refusal}
            )
            if is_current:
                current_todo_write = _correction_todo_readback_receipt(
                    skip_reason=refusal, operation_id=operation_id
                )
            continue
        if refusal is not None:
            # The Todo already exists for this revision; a replay only reads it
            # back and leaves its lifecycle to the Todo owner.
            settlements.append(
                {
                    "operation_id": operation_id,
                    "status": "settled_earlier",
                    "reason": refusal,
                }
            )
            if is_current:
                current_todo_write = _correction_todo_readback_receipt(
                    skip_reason=refusal,
                    operation_id=operation_id,
                    todo_id=todo_id_str,
                )
            continue
        if not todo_id_str and os.environ.get(MAINTAINER_CORRECTION_FAULT_ENV) == "1":
            raise RuntimeError(
                "maintainer correction Todo write aborted by "
                f"{MAINTAINER_CORRECTION_FAULT_ENV}"
            )
        todo_write = _write_maintainer_correction_todo(
            registry_path=registry_path,
            goal_id=goal_id,
            claimed_by=claimed_by,
            project=project,
            operation_id=operation_id,
            intent=intent,
        )
        receipt = _maintainer_correction_todo_write_receipt(
            todo_write, operation_id=operation_id
        )
        if entry.get("todo_id") != receipt["todo_id"]:
            changed_entries.append({**entry, "todo_id": receipt["todo_id"]})
        settlements.append(
            {
                "operation_id": operation_id,
                "status": "written" if receipt["write_performed"] else "already_exists",
                "todo_id": receipt["todo_id"],
            }
        )
        if is_current:
            current_todo_write = receipt
    if current_todo_write is None and current_operation_id:
        current_todo_write = _correction_todo_readback_receipt(
            skip_reason=(
                str(admission.get("reason") or admission.get("disposition") or "")
                if admission.get("disposition") != "accepted"
                else "unchanged_monitor_quiet"
            ),
            operation_id=current_operation_id,
        )
    if changed_entries:
        payload["maintainer_corrections"] = changed_entries
        upsert_issue_fix_pr_lifecycle_ledger_jsonl(ledger_path, payload)
        row = read_issue_fix_pr_lifecycle_ledger_row(
            ledger_path, repo=repo, pr_ref=pr_ref
        )
    entries = issue_fix_maintainer_correction_entries(row)
    payload["maintainer_corrections"] = entries
    if origin_map is not None:
        if origin_refusal is None and identity is None:
            raise ValueError("origin request identity unavailable")
        _deliver_origin_returns(
            payload=payload,
            entries=entries,
            origin=origin_map,
            identity=identity,
            goal_ref=goal_ref,
            view=view,
            origin_refusal=origin_refusal,
            observation=observation,
            ledger_path=ledger_path,
            registry_path=registry_path,
            goal_id=goal_id,
            project=project,
        )
    return {
        "schema_version": "issue_fix_maintainer_correction_settlement_v0",
        "settlements": settlements,
        "goal_stopped": goal_stopped,
        "todo_write": current_todo_write,
        "path_recorded": False,
    }
