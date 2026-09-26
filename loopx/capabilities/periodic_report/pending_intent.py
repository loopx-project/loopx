from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...control_plane.capability_hooks import (
    INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
    POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION,
    InteractionProjectionHookRegistration,
)
from ...control_plane.effect_runtime import effect_runtime_result
from ...history import load_registry
from .todo_source import read_report_todo_source
from ...registry import (
    atomic_write_json,
    find_registry_goal,
)
from ...todos import add_goal_todo
from ...file_lock import LockAcquisitionPolicy, exclusive_file_lock
from ...presentation.renderers.periodic_report_html import render_periodic_report_html
from ...presentation.renderers.periodic_report_markdown import (
    render_periodic_report_markdown,
)
from .adapters import (
    build_periodic_report_document,
    build_periodic_report_source_result,
)
from .bindings import build_periodic_report_generation_bundle
from .core import _reject_raw_keys
from .post_writeback_hook import (
    PERIODIC_REPORT_POST_WRITEBACK_HOOK_ID,
    PERIODIC_REPORT_TRIGGER_EVALUATION_INTENT,
    evaluate_periodic_report_trigger_evaluation_intent,
)
from .request_action import (
    PeriodicReportRequestPorts,
    periodic_report_request_intents,
    request_entry_for_intent,
    settle_periodic_report_request,
)
from .project_progress_snapshot import build_project_progress_snapshot
from .incremental import (
    build_periodic_report_publication_candidate,
    write_periodic_report_publication_candidate,
)
from .machine_defaults import (
    build_periodic_report_delivery_authority,
    resolve_goal_periodic_report_subscription,
)
from .machine_store import read_periodic_report_machine_defaults
from .cadence_journal import cadence_intent, cadence_journal_path, read_cadence_journal
from .workspace import (
    build_periodic_report_workspace_projection,
    write_periodic_report_workspace_projection,
)


PENDING_INTENT_SCHEMA = "pending_capability_intent_projection_v0"
CONSUMPTION_RECEIPT_SCHEMA = "periodic_report_intent_consumption_receipt_v0"
EDITORIAL_REQUEST_SCHEMA = "periodic_report_editorial_request_v0"
EDITORIAL_RESPONSE_SCHEMA = "periodic_report_editorial_response_v0"
HOOK_ID = "periodic_report.pending_intent"
CAPABILITY_ID = "periodic-report"
_DISPATCH_RE = re.compile(r"^pwh_[0-9a-f]{64}\.json$")
_IDENTITY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
_CHINESE_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_ANALYSIS_SECTION_CONTRACT = (
    ("overview", "全景判断"),
    ("problem_map", "问题版图"),
    ("causal_analysis", "重点因果下钻"),
    ("coverage_and_actions", "版本覆盖与处置"),
    ("next_actions", "下一步"),
)
_SECTION_CONTENT_KINDS = {
    "overview": "decision",
    "problem_map": "risk",
    "causal_analysis": "progress",
    "coverage_and_actions": "outcome",
    "next_actions": "next_action",
}


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _intent_key(intent: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        str(intent.get("idempotency_key") or "").encode("utf-8")
    ).hexdigest()[:24]


def _periodic_report_delivery_binding_ref(
    generation_id: object, authority: Mapping[str, Any]
) -> str:
    digest_suffix = str(generation_id).split("_")[-1][:16]
    authority_suffix = str(authority.get("effective_revision") or "").split(":")[-1][
        :12
    ]
    # Todo capability binding refs require the namespaced value to start with
    # a letter. Generation digests are hexadecimal and may start with a digit.
    return f"periodic-report:g{digest_suffix}-a{authority_suffix}"


def _attempt_dir(
    runtime_root: Path,
    goal_id: str,
    intent: Mapping[str, Any],
    *,
    rejection_revision: str | None = None,
) -> Path:
    base = runtime_root / "goals" / goal_id / "periodic_reports" / _intent_key(intent)
    return base / f"retry-{rejection_revision}" if rejection_revision else base


def _receipt_path(
    runtime_root: Path,
    goal_id: str,
    intent: Mapping[str, Any],
    *,
    rejection_revision: str | None = None,
) -> Path:
    return (
        _attempt_dir(
            runtime_root,
            goal_id,
            intent,
            rejection_revision=rejection_revision,
        )
        / "receipt.json"
    )


def _editorial_request_path(
    runtime_root: Path,
    goal_id: str,
    intent: Mapping[str, Any],
    *,
    rejection_revision: str | None = None,
) -> Path:
    return (
        _attempt_dir(
            runtime_root,
            goal_id,
            intent,
            rejection_revision=rejection_revision,
        )
        / "editorial_request.json"
    )


def _editorial_response_path(
    runtime_root: Path,
    goal_id: str,
    intent: Mapping[str, Any],
    *,
    rejection_revision: str | None = None,
) -> Path:
    return (
        _attempt_dir(
            runtime_root,
            goal_id,
            intent,
            rejection_revision=rejection_revision,
        )
        / "editorial.json"
    )


def _superseding_approval_revision(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    receipt: Mapping[str, Any],
    runtime_root: Path | None = None,
) -> str | None:
    approval_scope = str(receipt.get("approval_scope") or "")
    if receipt.get("status") != "approval_pending" or not approval_scope:
        return None
    _, items = read_report_todo_source(
        registry_path=registry_path, goal_id=goal_id, runtime_root=runtime_root
    )
    keys = (
        "todo_id",
        "status",
        "action_kind",
        "decision_outcome",
        "decision_scope",
        "bound_agent",
        "blocks_agent",
        "updated_at",
    )
    result = effect_runtime_result(
        "capabilities.periodic_report.approval_retry.select",
        {
            "schema_version": "periodic_report_approval_retry_request_v0",
            "agent_id": agent_id,
            "approval_scope": approval_scope,
            "items": [{key: item.get(key) for key in keys} for item in items],
        },
    )
    if (
        not isinstance(result, dict)
        or result.get("schema_version") != "periodic_report_approval_retry_result_v0"
    ):
        raise ValueError("periodic-report approval retry selection result mismatch")
    return result["revision"]


def _load_consumption_receipt(
    path: Path, *, intent_digest: str
) -> Mapping[str, Any] | None:
    if not path.is_file():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != CONSUMPTION_RECEIPT_SCHEMA
        or receipt.get("intent_digest") != intent_digest
        or receipt.get("status") not in {"approval_pending", "delivery_ready"}
    ):
        return None
    return receipt


def _active_delivery_subscription(
    *, registry_path: Path, runtime_root: Path, goal_id: str
) -> dict[str, Any] | None:
    """Resolve the current standing delivery authority for one pending report."""

    registry = load_registry(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if not isinstance(goal, Mapping) or goal.get("status") in {"stopped", "paused", "archived"}:
        return None
    subscription = resolve_goal_periodic_report_subscription(
        goal,
        read_periodic_report_machine_defaults(runtime_root),
    )
    return dict(subscription) if subscription.get("enabled") is True else None


def _next_attempt_revision(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    intent: Mapping[str, Any],
) -> tuple[bool, str | None]:
    """Return whether an intent is actionable and its immutable attempt revision."""

    base = _attempt_dir(runtime_root, goal_id, intent)
    receipt_paths = [base / "receipt.json"]
    if base.is_dir():
        receipt_paths.extend(sorted(base.glob("retry-*/receipt.json")))
    intent_digest = _canonical_digest(intent)
    receipts = [
        receipt
        for path in receipt_paths
        if (receipt := _load_consumption_receipt(path, intent_digest=intent_digest))
        is not None
    ]
    if not receipts:
        return True, None

    candidate_revisions: list[str] = []
    for receipt in receipts:
        revision = _superseding_approval_revision(
            runtime_root=runtime_root, registry_path=registry_path,
            goal_id=goal_id,
            agent_id=agent_id,
            receipt=receipt,
        )
        if revision is None:
            return False, None
        retry_receipt = _receipt_path(
            runtime_root,
            goal_id,
            intent,
            rejection_revision=revision,
        )
        if (
            _load_consumption_receipt(retry_receipt, intent_digest=intent_digest)
            is None
        ):
            candidate_revisions.append(revision)

    if not candidate_revisions:
        return False, None
    # Sequential approval gates yield one unconsumed rejection. Sorting keeps
    # malformed or manually duplicated state deterministic without overwriting
    # any earlier frozen attempt.
    return True, sorted(candidate_revisions)[-1]


def _valid_sidecar_intent(
    value: object, *, dispatch_id: str, goal_id: str, agent_id: str
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    if (
        value.get("schema_version") != POST_WRITEBACK_HOOK_RECEIPT_SCHEMA_VERSION
        or value.get("dispatch_id") != dispatch_id
        or value.get("status") != "intent_recorded"
        or value.get("hook_id") != PERIODIC_REPORT_POST_WRITEBACK_HOOK_ID
        or value.get("capability_id") != CAPABILITY_ID
        or value.get("error_code") is not None
        or type(value.get("attempt_count")) is not int
        or int(value["attempt_count"]) < 1
    ):
        return None
    intent = value.get("intent")
    if not isinstance(intent, Mapping):
        return None
    if intent.get("source_receipt_id") != value.get("source_receipt_id"):
        return None
    try:
        decision = evaluate_periodic_report_trigger_evaluation_intent(intent)
    except ValueError:
        return None
    if decision.get("eligible") is not True:
        return None
    payload = intent.get("payload")
    stage = payload.get("stage_completion") if isinstance(payload, Mapping) else None
    if not isinstance(stage, Mapping) or stage.get("agent_id") != agent_id:
        return None
    if not goal_id or not agent_id:
        return None
    return dict(intent)


def pending_periodic_report_intents(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str
) -> list[dict[str, Any]]:
    """Read only exact, eligible, unconsumed intents for one Goal/Agent."""

    if not _IDENTITY_RE.fullmatch(goal_id) or not _IDENTITY_RE.fullmatch(agent_id):
        return []
    pending: list[dict[str, Any]] = []
    for intent in periodic_report_request_intents(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
    ):
        try:
            decision = evaluate_periodic_report_trigger_evaluation_intent(intent)
        except ValueError:
            continue
        if decision.get("eligible") is not True:
            continue
        actionable, _revision = _next_attempt_revision(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            intent=intent,
        )
        receipt = _load_consumption_receipt(
            _receipt_path(runtime_root, goal_id, intent),
            intent_digest=_canonical_digest(intent),
        )
        if actionable or (
            isinstance(receipt, Mapping) and receipt.get("status") == "delivery_ready"
        ):
            pending.append(intent)

    sidecars = runtime_root / "goals" / goal_id / "post_writeback_hooks"
    if sidecars.is_dir():
        for path in sorted(sidecars.iterdir()):
            if not path.is_file() or not _DISPATCH_RE.fullmatch(path.name):
                continue
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            sidecar_intent = _valid_sidecar_intent(
                value,
                dispatch_id=path.stem,
                goal_id=goal_id,
                agent_id=agent_id,
            )
            if sidecar_intent is None:
                continue
            actionable, _revision = _next_attempt_revision(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                intent=sidecar_intent,
            )
            if actionable:
                pending.append(sidecar_intent)
    # Explicit requests and validated stage reports precede automatic calendars.
    subscription = _active_delivery_subscription(registry_path=registry_path,
        runtime_root=runtime_root, goal_id=goal_id)
    cadence = None
    if subscription and subscription.get("schedule") is not None:
        try:
            cadence = read_cadence_journal(runtime_root=runtime_root, goal_id=goal_id)
        except (OSError, ValueError):
            if not pending:
                raise
            # Turn-start still reports the failure; other valid work can run.
    if cadence is not None and cadence["publication"] is None and cadence["window"]["agent_id"] == agent_id:
        if subscription and subscription.get("schedule") is not None and (
            subscription["effective_revision"] == cadence["window"]["subscription_revision"]
        ):
            intent = cadence_intent(cadence["window"])
            actionable, _revision = _next_attempt_revision(registry_path=registry_path,
                runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id, intent=intent)
            # A delivery-ready window already has a delivery Todo. Do not
            # regenerate it or project a consume command that masks that Todo.
            if actionable:
                pending.append(intent)
    deduplicated: dict[str, dict[str, Any]] = {}
    for intent in pending:
        deduplicated.setdefault(str(intent.get("idempotency_key") or ""), intent)
    return list(deduplicated.values())


def periodic_report_pending_intent_interaction_hook(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str | None
) -> InteractionProjectionHookRegistration:
    normalized_agent_id = str(agent_id or "").strip()

    def produce() -> Mapping[str, Any]:
        try:
            subscription = _active_delivery_subscription(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
            )
        except (OSError, TypeError, ValueError):
            subscription = None
        intents = (
            pending_periodic_report_intents(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=normalized_agent_id,
            )
            if normalized_agent_id and subscription is not None
            else []
        )
        if not intents:
            return {
                "schema_version": INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
                "hook_id": HOOK_ID,
                "capability_id": CAPABILITY_ID,
                "phase": "interaction_projection",
                "status": "not_applicable",
                "projection_slot": None,
                "payload": None,
            }
        intent = intents[0]
        return {
            "schema_version": INTERACTION_PROJECTION_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": HOOK_ID,
            "capability_id": CAPABILITY_ID,
            "phase": "interaction_projection",
            "status": "candidate",
            "projection_slot": "pending_capability_intent",
            "payload": {
                "schema_version": PENDING_INTENT_SCHEMA,
                "capability_id": CAPABILITY_ID,
                "intent_kind": PERIODIC_REPORT_TRIGGER_EVALUATION_INTENT,
                "idempotency_key": str(intent["idempotency_key"]),
                "intent_digest": _canonical_digest(intent),
                "goal_id": goal_id,
                "agent_id": normalized_agent_id,
                "state": "pending",
                "action_kind": "consume_periodic_report_intent",
                "action_summary": (
                    "Prepare the typed report facts, author the required Chinese "
                    "analysis narrative, freeze one validated draft, then queue its "
                    "configured Goal Channel delivery."
                ),
                "command": (
                    "loopx periodic-report consume-pending "
                    f"--goal-id {goal_id} --agent-id {normalized_agent_id} --execute"
                ),
                "generation_authorized": True,
                "external_delivery_authorized": True,
                "agent_read_required": True,
            },
        }

    return InteractionProjectionHookRegistration(
        hook_id=HOOK_ID,
        capability_id=CAPABILITY_ID,
        projection_slots=("pending_capability_intent",),
        requested_read_scope=(
            "periodic_report_request_journal",
            "periodic_report_cadence_journal",
            "post_writeback_intent_journal",
        ),
        producer=produce,
    )


def _progress_facts(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    completed_at: str,
    available_capabilities: Any = None,
    allow_empty: bool = False,
) -> list[dict[str, Any]]:
    from ...control_plane.todos.todo_index import (
        MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
    )
    from ...rollout_event_log import load_rollout_events, rollout_event_log_path

    snapshot = build_project_progress_snapshot(
        runtime_root=runtime_root, registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        completed_at=completed_at,
        available_capabilities=available_capabilities,
        rollout_events=load_rollout_events(
            rollout_event_log_path(runtime_root, goal_id),
            limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
        ),
    )
    if not isinstance(snapshot, Mapping):
        if snapshot is None and allow_empty:
            return []
        raise ValueError("periodic-report has no public-safe progress items")
    return _progress_facts_from_snapshot(
        snapshot,
        goal_id=goal_id,
        completed_at=completed_at,
    )


def _progress_facts_from_snapshot(
    snapshot: Mapping[str, Any], *, goal_id: str, completed_at: str
) -> list[dict[str, Any]]:
    if (
        snapshot.get("schema_version")
        != "periodic_report_project_progress_projection_v0"
        or snapshot.get("goal_id") != goal_id
        or snapshot.get("observed_at") != completed_at
    ):
        raise ValueError("periodic-report progress snapshot identity is invalid")
    raw_items = snapshot.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("periodic-report progress snapshot items are invalid")
    facts: list[dict[str, Any]] = []
    seen_source_refs: set[str] = set()
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise ValueError(
                f"periodic-report progress snapshot item {index} is invalid"
            )
        item_id = str(raw_item.get("item_id") or "").strip()
        title = " ".join(str(raw_item.get("title") or "").split())
        summary = " ".join(str(raw_item.get("summary") or "").split())
        source_ref = str(raw_item.get("source_ref") or "").strip()
        content_kind = str(raw_item.get("content_kind") or "").strip()
        if not item_id or not title or not summary or not source_ref:
            raise ValueError("periodic-report progress snapshot item is incomplete")
        if source_ref in seen_source_refs:
            raise ValueError("periodic-report progress snapshot source is duplicated")
        raw_completed_at = str(raw_item.get("completed_at") or "").strip()
        if content_kind == "outcome":
            status = "done"
            completed = _validated_snapshot_timestamp(
                raw_completed_at,
                observed_at=completed_at,
            )
        elif content_kind == "next_action":
            status = "open"
            completed = None
        else:
            raise ValueError("periodic-report progress snapshot item kind is invalid")
        fact: dict[str, Any] = {
            "fact_id": item_id,
            "title": title[:500],
            "summary": summary[:1000],
            "status": status,
            "source_ref": source_ref,
        }
        change_kind = str(raw_item.get("change_kind") or "").strip()
        if change_kind:
            if change_kind not in {"added", "changed"}:
                raise ValueError(
                    "periodic-report progress snapshot change_kind is invalid"
                )
            fact["change_kind"] = change_kind
        if change_kind == "changed":
            previous_status = str(raw_item.get("previous_status") or "").strip()
            previous_kind = str(raw_item.get("previous_content_kind") or "").strip()
            previous_fingerprint = str(
                raw_item.get("previous_fact_fingerprint") or ""
            ).strip()
            if not previous_status or not previous_kind or not previous_fingerprint:
                raise ValueError(
                    "periodic-report changed fact requires its previous state"
                )
            fact.update(
                {
                    "previous_status": previous_status,
                    "previous_content_kind": previous_kind,
                    "previous_fact_fingerprint": previous_fingerprint,
                }
            )
        if completed is not None:
            fact["completed_at"] = completed
        facts.append(fact)
        seen_source_refs.add(source_ref)
    if not facts:
        raise ValueError("periodic-report has no public-safe progress items")
    return facts


def _validated_snapshot_timestamp(value: str, *, observed_at: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        boundary = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "periodic-report progress snapshot timestamp is invalid"
        ) from exc
    if timestamp.tzinfo is None or boundary.tzinfo is None or timestamp > boundary:
        raise ValueError("periodic-report progress snapshot timestamp is invalid")
    return value


def _cadence_coverage_fact(cadence_window: Mapping[str, Any]) -> dict[str, Any]:
    """Build the bounded coverage caveat for one frozen calendar window.

    A calendar report reads only what this agent's records can still show inside
    the window, so the caveat describes the report's own coverage. It used to be
    classified as a risk, which rendered a bounded coverage limitation to the
    reader under the risks section and let it stand in for the period's headline
    risk; supporting coverage evidence is the honest classification.
    """

    return {
        "fact_id": "calendar_coverage",
        "title": "本期证据覆盖范围",
        "summary": (
            "仅核对当前可读的本 Agent 任务记录，并按记录时间筛选本期交付；"
            "未验证完整历史和其他 Agent。空结果不能证明本期没有进展。"
        ),
        "status": "unknown",
        "content_kind": "coverage",
        "visibility": "supporting",
        "source_ref": "cadence:" + str(cadence_window["window_id"]),
    }


def _build_editorial_request(
    *,
    intent: Mapping[str, Any],
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    completed_at: str,
    facts: list[dict[str, Any]],
    incremental_baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    work_window = _actual_work_window(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        facts=facts,
        completed_at=completed_at,
        publication_boundary=(
            str(incremental_baseline.get("covered_until") or "")
            if isinstance(incremental_baseline, Mapping)
            else None
        ),
    )
    cadence_window = intent.get("payload", {}).get("cadence_window")
    if isinstance(cadence_window, Mapping):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(cadence_window["schedule"]["timezone"])
        start = datetime.fromisoformat(cadence_window["start_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(cadence_window["due_at"].replace("Z", "+00:00"))
        work_window = {"start_at": cadence_window["start_at"], "end_at": cadence_window["due_at"],
            "period_label": f"{start.astimezone(zone):%Y-%m-%d %H:%M} — {end.astimezone(zone):%Y-%m-%d %H:%M}（{zone.key}）",
            "source": "frozen_calendar_window"}
    request: dict[str, Any] = {
        "schema_version": EDITORIAL_REQUEST_SCHEMA,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "intent_digest": _canonical_digest(intent),
        "language": "zh-CN",
        "narrative_contract": {
            "contract_id": "analysis_from_overview_to_depth_v1",
            "section_order": [item[0] for item in _ANALYSIS_SECTION_CONTRACT],
            "section_titles": dict(_ANALYSIS_SECTION_CONTRACT),
            "requirements": [
                "Lead with the current overall judgment, not a work log.",
                "Map the complete problem space before selecting deep dives.",
                "Trace the highest-value findings to evidence-backed causes.",
                "Separate current-version coverage from historical evidence.",
                "Keep report-building work out of the analysis mainline.",
                "Write audience-facing Chinese while preserving necessary technical terms.",
                "Report only the supplied incremental facts; never restate facts absent from this request.",
                "Render change_kind=changed as a concise transition from the supplied previous state to the current state.",
            ],
        },
        "completed_at": completed_at,
        "actual_work_window": work_window,
        "facts": facts,
        "boundary": {
            "agent_authors_business_judgment": True,
            "cli_validates_and_freezes": True,
            "external_writes_performed": False,
        },
    }
    if isinstance(incremental_baseline, Mapping):
        request["incremental_baseline"] = dict(incremental_baseline)
    request["request_digest"] = _canonical_digest(request)
    return request


def _actual_work_window(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    facts: list[dict[str, Any]],
    completed_at: str,
    publication_boundary: str | None = None,
) -> dict[str, str]:
    end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    observed: list[datetime] = []
    boundary: datetime | None = None
    if publication_boundary:
        try:
            boundary = datetime.fromisoformat(
                publication_boundary.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ValueError("periodic-report publication boundary is invalid") from exc
        if boundary.tzinfo is None or boundary >= end:
            raise ValueError("periodic-report publication boundary is invalid")
        boundary = boundary.astimezone(end.tzinfo)
        observed.append(boundary)
    run_index = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    if run_index.is_file():
        try:
            # LF framing, not `splitlines()`: see `loopx/history.py` for the same reason.
            rows = run_index.read_text(encoding="utf-8").split("\n")
        except OSError:
            rows = []
        for raw_row in rows:
            try:
                row = json.loads(raw_row)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, Mapping) or row.get("agent_id") != agent_id:
                continue
            raw = str(row.get("generated_at") or "").strip()
            try:
                value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if value.tzinfo is None or end.tzinfo is None:
                continue
            value = value.astimezone(end.tzinfo)
            if value <= end and (boundary is None or value >= boundary):
                observed.append(value)
    for fact in facts:
        raw = str(fact.get("completed_at") or fact.get("updated_at") or "").strip()
        if not raw:
            continue
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if value.tzinfo is None or end.tzinfo is None:
            continue
        value = value.astimezone(end.tzinfo)
        if value <= end and (boundary is None or value >= boundary):
            observed.append(value)
    start = min(observed, default=end)
    # The document protocol requires a non-empty half-open window. A stage with
    # only one timestamp still represents that exact completion instant.
    document_start = start if start < end else end - timedelta(microseconds=1)
    display_tz = end.tzinfo
    timezone_label = "UTC"
    registry_tz = None
    try:
        from zoneinfo import ZoneInfo

        registry_tz = ZoneInfo("Asia/Shanghai")
    except (ImportError, ValueError):
        registry_tz = None
    if registry_tz is not None:
        display_tz = registry_tz
        timezone_label = "北京时间"
    display_start = start.astimezone(display_tz)
    display_end = end.astimezone(display_tz)
    offset = display_end.utcoffset()
    if offset not in {timedelta(hours=8), timedelta(0)}:
        total_minutes = int((offset or timedelta()).total_seconds() // 60)
        sign = "+" if total_minutes >= 0 else "-"
        hours, minutes = divmod(abs(total_minutes), 60)
        timezone_label = f"UTC{sign}{hours:02d}:{minutes:02d}"
    if start == end:
        period_label = f"{display_end:%Y-%m-%d %H:%M}（{timezone_label}）"
    else:
        period_label = (
            f"{display_start:%Y-%m-%d %H:%M} — "
            f"{display_end:%Y-%m-%d %H:%M}"
            f"（{timezone_label}）"
        )
    window = {
        "start_at": document_start.isoformat(),
        "end_at": end.isoformat(),
        "period_label": period_label,
        "source": "agent_run_history_or_report_facts",
    }
    if boundary is not None:
        window["publication_boundary_applied"] = "true"
    return window


def _load_frozen_editorial_request(
    *,
    request_path: Path,
    intent_digest: str,
    goal_id: str,
    agent_id: str,
) -> dict[str, Any] | None:
    if not request_path.is_file():
        return None
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("periodic-report editorial request is unreadable") from exc
    if not isinstance(request, Mapping):
        raise ValueError("periodic-report editorial request must be an object")
    request = dict(request)
    recorded_digest = request.pop("request_digest", None)
    if (
        request.get("schema_version") != EDITORIAL_REQUEST_SCHEMA
        or request.get("intent_digest") != intent_digest
        or request.get("goal_id") != goal_id
        or request.get("agent_id") != agent_id
        or recorded_digest != _canonical_digest(request)
    ):
        raise ValueError("periodic-report editorial request identity is invalid")
    request["request_digest"] = recorded_digest
    return request


def _chinese_density(value: object) -> float:
    text = str(value or "")
    chinese = len(_CHINESE_RE.findall(text))
    letters = sum(character.isalpha() for character in text)
    return chinese / max(letters, 1)


def _load_editorial_response(
    *, request: Mapping[str, Any], response_path: Path
) -> dict[str, Any]:
    try:
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("periodic-report editorial response is unreadable") from exc
    if not isinstance(response, Mapping):
        raise ValueError("periodic-report editorial response must be an object")
    _reject_raw_keys(response, "periodic-report editorial response")
    if (
        response.get("schema_version") != EDITORIAL_RESPONSE_SCHEMA
        or response.get("request_digest") != request.get("request_digest")
        or response.get("language") != "zh-CN"
    ):
        raise ValueError("periodic-report editorial response identity is invalid")
    title = " ".join(str(response.get("title") or "").split())
    if not title or _chinese_density(title) < 0.45:
        raise ValueError("periodic-report editorial title must be Chinese-first")
    work_window = request.get("actual_work_window")
    if not isinstance(work_window, Mapping):
        raise ValueError("periodic-report actual work window is missing")
    expected_period_label = str(work_window.get("period_label") or "")
    supplied_period_label = str(response.get("period_label") or "")
    if supplied_period_label and supplied_period_label != expected_period_label:
        raise ValueError(
            "periodic-report editorial period_label must match the actual work window"
        )
    raw_sections = response.get("sections")
    if not isinstance(raw_sections, list):
        raise ValueError("periodic-report editorial sections must be a list")
    expected_ids = [item[0] for item in _ANALYSIS_SECTION_CONTRACT]
    section_ids = [
        str(section.get("section_id") or "")
        for section in raw_sections
        if isinstance(section, Mapping)
    ]
    if section_ids != expected_ids or len(raw_sections) != len(expected_ids):
        raise ValueError(
            "periodic-report editorial sections must follow the overview-to-depth contract"
        )
    allowed_refs = {
        str(fact.get("source_ref") or "")
        for fact in request.get("facts") or []
        if isinstance(fact, Mapping)
    }
    normalized_sections: list[dict[str, Any]] = []
    narrative_text: list[str] = [title]
    item_total = 0
    for order, ((section_id, expected_title), raw_section) in enumerate(
        zip(_ANALYSIS_SECTION_CONTRACT, raw_sections, strict=True), start=1
    ):
        if not isinstance(raw_section, Mapping):
            raise ValueError("periodic-report editorial section is invalid")
        section_title = " ".join(str(raw_section.get("title") or "").split())
        if section_title != expected_title:
            raise ValueError(
                f"periodic-report editorial section {section_id} title is invalid"
            )
        raw_items = raw_section.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError(
                f"periodic-report editorial section {section_id} must not be empty"
            )
        normalized_items: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, Mapping):
                raise ValueError("periodic-report editorial item is invalid")
            item = dict(raw_item)
            if item.get("item_id") is not None:
                raise ValueError(
                    "periodic-report editorial item_id is assigned by the consumer"
                )
            if (
                item.get("value_rank") is not None
                or item.get("content_kind") is not None
            ):
                raise ValueError(
                    "periodic-report editorial ordering and semantics are assigned by the consumer"
                )
            source_ref = str(item.get("source_ref") or "")
            if source_ref not in allowed_refs:
                raise ValueError(
                    "periodic-report editorial item must reference a supplied fact"
                )
            item["item_id"] = f"{section_id}_{index + 1}"
            item["value_rank"] = order * 10 + index
            item["content_kind"] = _SECTION_CONTENT_KINDS[section_id]
            narrative_text.extend(
                [str(item.get("title") or ""), str(item.get("summary") or "")]
            )
            if section_id == "causal_analysis":
                details = item.get("details")
                if not isinstance(details, list) or len(details) < 2:
                    raise ValueError(
                        "causal analysis items require at least two evidence/boundary details"
                    )
            normalized_items.append(item)
            item_total += 1
        normalized_sections.append(
            {
                "section_id": section_id,
                "title": section_title,
                "order": order * 10,
                "items": normalized_items,
            }
        )
    if item_total < 6 or _chinese_density(" ".join(narrative_text)) < 0.45:
        raise ValueError(
            "periodic-report editorial response must contain a substantive Chinese narrative"
        )
    highlights = response.get("highlights")
    if not isinstance(highlights, list) or not 2 <= len(highlights) <= 4:
        raise ValueError("periodic-report editorial response requires 2-4 highlights")
    highlight_text = " ".join(
        f"{highlight.get('value', '')} {highlight.get('label', '')} "
        f"{highlight.get('detail', '')}"
        for highlight in highlights
        if isinstance(highlight, Mapping)
    )
    if _chinese_density(highlight_text) < 0.45:
        raise ValueError("periodic-report editorial highlights must be Chinese-first")
    return {
        "title": title,
        "editorial": {
            "language": "zh-CN",
            "kicker": str(response.get("kicker") or "阶段分析周报"),
            "period_label": expected_period_label,
            "highlights": highlights,
        },
        "sections": normalized_sections,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _build_authored_source(
    authored: Mapping[str, Any], *, completed_at: str
) -> dict[str, Any]:
    return build_periodic_report_source_result(
        source_id="project_progress",
        source_kind="validated_project_progress",
        status="complete",
        observed_at=completed_at,
        sections=list(authored["sections"]),
        retryable=False,
    )


def consume_pending_periodic_report_intent(
    *, registry_path: Path, runtime_root: Path, goal_id: str, agent_id: str,
    execute: bool, provider_request_ports: PeriodicReportRequestPorts | None = None,
) -> dict[str, Any]:
    kwargs = dict(registry_path=registry_path, runtime_root=runtime_root,
                  goal_id=goal_id, agent_id=agent_id, execute=execute,
                  provider_request_ports=provider_request_ports)
    # Leave effect-free/empty reads unchanged. Re-read under the same lock as
    # calendar admission before any preparation, generation, or Todo write.
    if not execute:
        return _consume_pending_periodic_report_intent(**kwargs)
    if not pending_periodic_report_intents(
        registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, agent_id=agent_id,
    ):
        return {"ok": True, "schema_version": CONSUMPTION_RECEIPT_SCHEMA,
                "status": "no_pending_intent", "external_writes_performed": False}
    with exclusive_file_lock(cadence_journal_path(runtime_root, goal_id),
            policy=LockAcquisitionPolicy.MUTATION, agent_id=agent_id,
            operation="periodic_report_intent_consumption"):
        return _consume_pending_periodic_report_intent(**kwargs)


def _consume_pending_periodic_report_intent(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    execute: bool,
    provider_request_ports: PeriodicReportRequestPorts | None = None,
) -> dict[str, Any]:
    intents = pending_periodic_report_intents(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    if not intents:
        return {
            "ok": True,
            "schema_version": CONSUMPTION_RECEIPT_SCHEMA,
            "status": "no_pending_intent",
            "external_writes_performed": False,
        }
    subscription = _active_delivery_subscription(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
    )
    if subscription is None:
        return {
            "ok": True,
            "schema_version": CONSUMPTION_RECEIPT_SCHEMA,
            "status": "subscription_disabled",
            "external_writes_performed": False,
        }
    intent = intents[0]
    trigger = evaluate_periodic_report_trigger_evaluation_intent(intent)
    payload = intent["payload"]
    report_request = payload.get("report_request")
    request_entry = (
        request_entry_for_intent(
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            intent=intent,
        )
        if isinstance(report_request, Mapping)
        else None
    )
    stage = payload.get("stage_completion")
    cadence_window = payload.get("cadence_window")
    completed_at = str(
        report_request.get("requested_at")
        if isinstance(report_request, Mapping)
        else stage.get("completed_at")
        if isinstance(stage, Mapping)
        else cadence_window.get("due_at")
        if isinstance(cadence_window, Mapping)
        else ""
    )
    project_progress = payload.get("project_progress")
    fallback_capabilities = payload.get("available_capabilities")
    if fallback_capabilities is None and isinstance(payload.get("turn"), Mapping):
        fallback_capabilities = payload["turn"].get("available_capabilities")
    actionable, rejection_revision = _next_attempt_revision(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        intent=intent,
    )
    if not actionable:
        durable = _load_consumption_receipt(
            _receipt_path(
                runtime_root,
                goal_id,
                intent,
                rejection_revision=rejection_revision,
            ),
            intent_digest=_canonical_digest(intent),
        )
        if (
            request_entry is not None
            and isinstance(durable, Mapping)
            and durable.get("status") == "delivery_ready"
        ):
            settlement = settle_periodic_report_request(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                intent=intent,
                request_ports=provider_request_ports,
                execute=execute,
            )
            result = {
                **dict(durable),
                "source_settlement": settlement,
                "settlement_only_retry": True,
            }
            if execute:
                atomic_write_json(
                    _receipt_path(
                        runtime_root,
                        goal_id,
                        intent,
                        rejection_revision=rejection_revision,
                    ),
                    result,
                )
            return result
        return {
            "ok": True,
            "schema_version": CONSUMPTION_RECEIPT_SCHEMA,
            "status": "no_pending_intent",
            "external_writes_performed": False,
        }
    facts = (
        _progress_facts_from_snapshot(
            project_progress,
            goal_id=goal_id,
            completed_at=completed_at,
        )
        if isinstance(project_progress, Mapping)
        else _progress_facts(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            completed_at=completed_at,
            available_capabilities=fallback_capabilities,
            allow_empty=isinstance(cadence_window, Mapping),
        )
    )
    if isinstance(cadence_window, Mapping):
        start = datetime.fromisoformat(cadence_window["start_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(cadence_window["due_at"].replace("Z", "+00:00"))
        facts = [fact for fact in facts if fact.get("status") != "done" or (
            start < datetime.fromisoformat(str(fact["completed_at"]).replace("Z", "+00:00")) <= end
        )]
        facts.append(_cadence_coverage_fact(cadence_window))
    request_path = _editorial_request_path(
        runtime_root,
        goal_id,
        intent,
        rejection_revision=rejection_revision,
    )
    response_path = _editorial_response_path(
        runtime_root,
        goal_id,
        intent,
        rejection_revision=rejection_revision,
    )
    intent_digest = _canonical_digest(intent)
    editorial_request = _load_frozen_editorial_request(
        request_path=request_path,
        intent_digest=intent_digest,
        goal_id=goal_id,
        agent_id=agent_id,
    ) or _build_editorial_request(
        intent=intent,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        completed_at=completed_at,
        facts=facts,
        incremental_baseline=(
            project_progress.get("incremental_baseline")
            if isinstance(project_progress, Mapping)
            and isinstance(project_progress.get("incremental_baseline"), Mapping)
            else None
        ),
    )
    if not response_path.is_file():
        editorial_result = {
            "ok": True,
            "schema_version": CONSUMPTION_RECEIPT_SCHEMA,
            "status": "editorial_required",
            "goal_id": goal_id,
            "agent_id": agent_id,
            "intent_digest": intent_digest,
            "agent_read_required": True,
            "editorial_contract": editorial_request["narrative_contract"],
            "editorial_request_path": str(request_path),
            "editorial_response_path": str(response_path),
            "external_writes_performed": False,
        }
        if execute:
            request_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(request_path, editorial_request)
        return editorial_result
    authored = _load_editorial_response(
        request=editorial_request,
        response_path=response_path,
    )
    source = _build_authored_source(authored, completed_at=completed_at)
    work_window = editorial_request["actual_work_window"]
    profile_ref = payload["profile_ref"]
    document = build_periodic_report_document(
        title=str(authored["title"]),
        generated_at=completed_at,
        period_window={
            "start_at": str(work_window["start_at"]),
            "end_at": str(work_window["end_at"]),
        },
        profile={
            "profile_id": profile_ref["profile_id"],
            "profile_version": profile_ref["profile_version"],
        },
        sources=[source],
        editorial=dict(authored["editorial"]),
        trigger_receipt=trigger,
    )
    markdown = render_periodic_report_markdown(document)
    html = render_periodic_report_html(document)
    bundle = build_periodic_report_generation_bundle(
        document=document, artifacts=[markdown, html]
    )
    generation = bundle["generation_receipt"]
    delivery_authority = build_periodic_report_delivery_authority(subscription)
    result: dict[str, Any] = {
        "ok": True,
        "schema_version": CONSUMPTION_RECEIPT_SCHEMA,
        "status": "preview",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "intent_digest": intent_digest,
        "generation_receipt": generation,
        "content_checks": {
            "schema_version": "periodic_report_content_checks_v0",
            "document_normalized": True,
            "artifact_digests_verified": True,
            "html_self_contained": html.get("external_dependencies") == [],
            "matching_document_digest": (
                html.get("document_digest") == markdown.get("document_digest")
            ),
            "language_is_zh_cn": True,
            "analysis_narrative_validated": True,
            "evidence_lineage_validated": True,
            "external_writes_performed": False,
        },
        "external_delivery_authorized": True,
        "delivery_authority": delivery_authority,
        "external_writes_performed": False,
    }
    if not execute:
        return result

    receipt_path = _receipt_path(
        runtime_root,
        goal_id,
        intent,
        rejection_revision=rejection_revision,
    )
    artifact_dir = receipt_path.parent
    artifact_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = artifact_dir / "report.md"
    html_path = artifact_dir / "report.html"
    generation_bundle_path = artifact_dir / "generation-bundle.json"
    publication_candidate_path = artifact_dir / "publication-candidate.json"
    workspace_projection_path = artifact_dir / "workspace-projection.json"
    _atomic_write_text(markdown_path, str(markdown["content"]))
    _atomic_write_text(html_path, str(html["content"]))
    atomic_write_json(generation_bundle_path, bundle)
    workspace_projection = build_periodic_report_workspace_projection(
        goal_id=goal_id,
        agent_id=agent_id,
        generation_id=str(generation["generation_id"]),
        document=document,
        facts=facts,
    )
    write_periodic_report_workspace_projection(
        path=workspace_projection_path,
        projection=workspace_projection,
    )
    incremental_baseline = (
        project_progress.get("incremental_baseline")
        if isinstance(project_progress, Mapping)
        and isinstance(project_progress.get("incremental_baseline"), Mapping)
        else None
    )
    publication_candidate = build_periodic_report_publication_candidate(
        goal_id=goal_id,
        agent_id=agent_id,
        generation_id=str(generation["generation_id"]),
        trigger_receipt=trigger,
        facts=facts,
        baseline=incremental_baseline,
        workspace_projection_sha256=str(workspace_projection["content_sha256"]),
    )
    write_periodic_report_publication_candidate(
        path=publication_candidate_path,
        candidate=publication_candidate,
    )
    delivery = add_goal_todo(
        registry_path=registry_path,
        goal_id=goal_id,
        role="agent",
        text=(
            "[P0] Deliver the configured periodic report as two independent Goal "
            "Channel messages: the report entry and the Lark document entry for "
            f"{generation['generation_id']}."
        ),
        status="open",
        note=(
            "Use only the frozen generation consumption receipt and the current "
            "Goal Channel project_bot binding; do not fall back to a user or "
            "default Bot identity. The enabled periodic-report subscription is "
            "the standing delivery authority."
        ),
        task_class="advancement_task",
        action_kind="deliver_periodic_report_goal_channel",
        task_domain="provider_delivery",
        capability_binding_ref=_periodic_report_delivery_binding_ref(
            generation["generation_id"], delivery_authority
        ),
        required_write_scopes=["goal_channel/lark/messages"],
        required_capabilities=["network", "lark_bot_message_write"],
        target_capabilities=["periodic_report", "goal_channel"],
        claimed_by=agent_id,
        agent_id=agent_id,
    )
    durable = {
        **result,
        "status": "delivery_ready",
        "delivery_todo_id": delivery.get("todo_id"),
        "artifacts": {
            "html_path": str(html_path),
            "html_digest": html["content_digest"],
            "markdown_path": str(markdown_path),
            "markdown_digest": markdown["content_digest"],
            "generation_bundle_path": str(generation_bundle_path),
            "publication_candidate_path": str(publication_candidate_path),
            "workspace_projection_path": str(workspace_projection_path),
            "workspace_projection_sha256": workspace_projection["content_sha256"],
        },
        "incremental_baseline": publication_candidate.get("incremental_baseline"),
    }
    atomic_write_json(receipt_path, durable)
    if request_entry is not None:
        settlement = settle_periodic_report_request(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            intent=intent,
            request_ports=provider_request_ports,
            execute=True,
        )
        durable["source_settlement"] = settlement
        atomic_write_json(receipt_path, durable)
    return durable


__all__ = [
    "consume_pending_periodic_report_intent",
    "pending_periodic_report_intents",
    "periodic_report_pending_intent_interaction_hook",
]
