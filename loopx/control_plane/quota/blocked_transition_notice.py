"""Typed blocked-transition notice contract (#4381).

Refs #4381. Arrives with #4543, which made the quota projection admit that a
higher-priority Agent Todo blocked ahead of an executable fallback should be
told to the owner. That admission is an *intent*, not a delivery: it answers
"the owner should know" but says nothing about what the owner is actually told.

This module closes the first of the gaps #4543 left open: the notice itself.
Every first material entry into ``blocked`` carries the task, the concrete cause
and evidence, the impact, the party that can resolve it, the recovery condition
and the next action. The contract keeps ``owner_must_know`` and ``owner_must_act``
as separate decisions: an agent-owned blocker states that no owner action is
required, while an owner gate asks one concrete question.

Each notice also carries a ``blocker_revision`` digest over the cause, evidence,
recovery condition, responsible party and supersession marker. The digest is the
dedup key used by an authorized delivery adapter — unchanged means "already
told", changed means "tell again". This transport-neutral builder does not
decide emission: it reports ``delivery.state == "pending"`` until a delivery
adapter records a separate receipt. The Lark Goal Channel adapter owns
send/readback state in its private binding; a NOTIFY intent is not delivery.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ..runtime.public_safety import public_safe_compact_text
from ..todos.contract import (
    TODO_STATUS_BLOCKED,
    TODO_TASK_CLASS_USER_ACTION,
    TODO_TASK_CLASS_USER_GATE,
    normalize_todo_role,
    normalize_todo_status,
)
from ..todos.resume_condition import (
    TODO_RESUME_KIND_CAPACITY_AVAILABLE,
    TODO_RESUME_KIND_MONITOR_CHANGED,
    TODO_RESUME_KIND_PR_MERGED,
    TODO_RESUME_KIND_RESUME_AT,
    TODO_RESUME_KIND_TODO_DONE,
    normalize_todo_resume_when,
)

BLOCKED_TRANSITION_NOTICE_SCHEMA_VERSION = "blocked_transition_notice_v0"
BLOCKED_TRANSITION_NOTICE_KIND = "blocked_transition_notice"

# The transport-neutral notice remains pending until an authorized adapter
# records delivery/readback in its own receipt store.
NOTICE_DELIVERY_PENDING = "pending"

RESPONSIBLE_AGENT = "agent"
RESPONSIBLE_OWNER = "owner"
RESPONSIBLE_EXTERNAL = "external_dependency"


def _compact(value: Any, limit: int = 180) -> str:
    text = public_safe_compact_text(value, limit=limit)
    if text is None:
        return ""
    return str(text).strip()


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def blocked_transition_notice_identity(item: Mapping[str, Any]) -> str | None:
    """Return the stable identity of a blocker, or ``None`` when unidentifiable."""

    todo_id = _compact(item.get("todo_id"), limit=120)
    if todo_id:
        return f"todo:{todo_id}"
    text = _compact(item.get("text"))
    if not text:
        return None
    return f"text:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"


def _resume_kind(resume_when: str | None) -> str | None:
    if not resume_when:
        return None
    return resume_when.split(":", 1)[0] or None


def _resume_target(resume_when: str | None) -> str:
    if not resume_when:
        return ""
    return resume_when.split(":", 1)[1] if ":" in resume_when else ""


def _recovery_description(kind: str | None, resume_when: str | None, cause: str) -> str:
    target = _resume_target(resume_when)
    if kind == TODO_RESUME_KIND_TODO_DONE:
        return f"todo {target or 'unknown'} must reach done"
    if kind == TODO_RESUME_KIND_PR_MERGED:
        return f"pull request {target or 'unknown'} must merge"
    if kind == TODO_RESUME_KIND_CAPACITY_AVAILABLE:
        return "a runtime with the required capability must become available"
    if kind == TODO_RESUME_KIND_MONITOR_CHANGED:
        return f"monitor {target or 'unknown'} must report a material change"
    if kind == TODO_RESUME_KIND_RESUME_AT:
        return f"the scheduled resume time {target or 'unknown'} must arrive"
    return f"the recorded blocker must clear: {cause}"


def _responsible_party(
    item: Mapping[str, Any],
    *,
    resume_kind: str | None,
) -> str:
    role = normalize_todo_role(item.get("role"))
    task_class = _compact(item.get("task_class"), limit=60).lower()
    if role == "user" or task_class in {
        TODO_TASK_CLASS_USER_GATE,
        TODO_TASK_CLASS_USER_ACTION,
    }:
        return RESPONSIBLE_OWNER
    if resume_kind in {TODO_RESUME_KIND_PR_MERGED, TODO_RESUME_KIND_CAPACITY_AVAILABLE}:
        return RESPONSIBLE_EXTERNAL
    return RESPONSIBLE_AGENT


def _next_action(
    *,
    owner_must_act: bool,
    responsible_party: str,
    recovery: str,
    fallback_text: str,
) -> str:
    if owner_must_act:
        question = (
            "Confirm whether to proceed or cancel this blocked step"
            if responsible_party == RESPONSIBLE_OWNER
            else f"Decide how to resolve this blocker: {recovery}"
        )
        return f"Ask the owner one concrete question — {question}."
    if fallback_text:
        return (
            "No owner action is required: the agent keeps the fallback "
            f"'{fallback_text}' moving and reports again if this blocker changes."
        )
    return (
        "No owner action is required: the agent keeps independent work moving "
        "and reports again if this blocker changes."
    )


def build_blocked_transition_notice(
    item: Mapping[str, Any],
    *,
    selected_executable: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the typed first-transition notice for one blocked Todo.

    Returns ``None`` when the item is not in a blocked state: a scheduled
    future monitor window is a deferral, and an open item is simply work that
    has not started. Only a real blocker earns a notice.

    The returned notice is a plain JSON-serializable mapping. It carries a
    ``blocker_revision`` digest so a later ledger can dedup "same blocker, same
    cause" from "same blocker, materially changed cause", but it does not itself
    decide whether to emit: emission, delivery recording and reconciliation need
    an authorized delivery surface and belong to the delivery adapter.

The module also owns how a notice is rendered for the owner
(:func:`blocked_priority_fallback_owner_reason`), so the projection that shows
the reason stays a thin caller instead of growing a second copy of the rules.
"""

    if not isinstance(item, Mapping):
        return None
    identity = blocked_transition_notice_identity(item)
    if identity is None:
        return None
    status = normalize_todo_status(item.get("status")) or "open"
    resume_when = normalize_todo_resume_when(item.get("resume_when"))
    resume_pending = bool(resume_when and item.get("resume_ready") is False)
    if status != TODO_STATUS_BLOCKED and not resume_pending:
        return None

    text = _compact(item.get("text"))
    reason = _compact(item.get("reason"), limit=240)
    resume_kind = _resume_kind(resume_when)
    cause = (
        reason
        if status == TODO_STATUS_BLOCKED and reason
        else (
            f"the todo waits on {resume_when}"
            if resume_pending
            else "the todo is marked blocked without a recorded reason"
        )
    )
    evidence: list[str] = [f"status={status}"]
    if reason:
        evidence.append(f"reason={reason}")
    if resume_when:
        evidence.append(f"resume_when={resume_when}")
        evidence.append(f"resume_ready={str(item.get('resume_ready')).lower()}")
    claimed_by = _compact(item.get("claimed_by"), limit=80)
    if claimed_by:
        evidence.append(f"claimed_by={claimed_by}")

    responsible_party = _responsible_party(item, resume_kind=resume_kind)
    owner_must_act = responsible_party == RESPONSIBLE_OWNER
    fallback_text = _compact(
        selected_executable.get("text") if isinstance(selected_executable, Mapping) else None
    )
    recovery = _recovery_description(resume_kind, resume_when, cause)
    impact = (
        f"'{text}' will not advance until {recovery}."
        + (
            f" The goal keeps moving on the lower-priority fallback '{fallback_text}'."
            if fallback_text
            else " No executable fallback is selected, so no agent work advances."
        )
    )
    superseded_by = _compact(item.get("superseded_by"), limit=120)

    notice: dict[str, Any] = {
        "schema_version": BLOCKED_TRANSITION_NOTICE_SCHEMA_VERSION,
        "kind": BLOCKED_TRANSITION_NOTICE_KIND,
        "blocker_identity": identity,
        "task": {
            "todo_id": _compact(item.get("todo_id"), limit=120) or None,
            "text": text,
            "status": status,
            "task_class": _compact(item.get("task_class"), limit=60) or None,
            "priority": _compact(item.get("priority"), limit=40) or None,
        },
        "cause": cause,
        "evidence": evidence,
        "impact": impact,
        "responsible_party": responsible_party,
        "recovery_condition": {
            "kind": resume_kind or "status_blocked",
            "satisfied": False,
            "resume_when": resume_when,
            "description": recovery,
        },
        "next_action": _next_action(
            owner_must_act=owner_must_act,
            responsible_party=responsible_party,
            recovery=recovery,
            fallback_text=fallback_text,
        ),
        "owner_must_know": True,
        "owner_must_act": owner_must_act,
        "superseded_by": superseded_by or None,
        "delivery": {
            "state": NOTICE_DELIVERY_PENDING,
            "surface": None,
            "delivered_at": None,
            "readback_verified_at": None,
        },
    }
    notice["blocker_revision"] = _digest(
        {
            "blocker_identity": identity,
            "cause": cause,
            "evidence": evidence,
            "recovery_condition": notice["recovery_condition"],
            "responsible_party": responsible_party,
            "owner_must_act": owner_must_act,
            "superseded_by": notice["superseded_by"],
        }
    )
    return notice


def blocked_transition_notice_owner_reason(notice: Mapping[str, Any]) -> str | None:
    """Render one typed notice as the single sentence the owner reads."""

    if not isinstance(notice, Mapping):
        return None
    task = notice.get("task")
    task_text = str(task.get("text") or "").strip() if isinstance(task, Mapping) else ""
    cause = str(notice.get("cause") or "").strip()
    impact = str(notice.get("impact") or "").strip()
    next_action = str(notice.get("next_action") or "").strip()
    parts = [
        part
        for part in (
            f"'{task_text}' is blocked: {cause}." if task_text and cause else (cause or None),
            impact or None,
            next_action or None,
        )
        if part
    ]
    return " ".join(parts) if parts else None


def blocked_priority_fallback_owner_reason(fallback: Mapping[str, Any]) -> str | None:
    """Render the owner-facing reason for a blocked-priority-fallback payload.

    The typed notice wins over the generic fallback prose so the sentence the
    owner reads is the notice that was contracted. The payload keeps every
    notice; the reason renders the first one, because one sentence per
    heartbeat beats a list the owner has to scan.
    """

    if not isinstance(fallback, Mapping):
        return None
    notices = fallback.get("blocked_transition_notices")
    if isinstance(notices, list):
        notice = next(
            (item for item in notices if isinstance(item, Mapping)),
            None,
        )
        if notice is not None:
            reason = blocked_transition_notice_owner_reason(notice)
            if reason:
                return reason
    prose = str(fallback.get("reason") or "").strip()
    return prose or None
