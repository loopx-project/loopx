"""Agent-neutral request, decision and result records.

Manager Chat is one ingress/egress adapter. Coordination never changes Agent
registration, execution binding, Todo ownership or lease authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from ...file_lock import exclusive_file_lock

if TYPE_CHECKING:
    from .goal_instance_scope import CollaborationGoalScope

ENTRY_SCHEMA = "loopx_manager_context_entry_v1"
EXACT_ENTRY_SCHEMA = "loopx_manager_context_entry_v2"
REQUEST_TRIAGE_INSTRUCTION = (
    "Read the supplied requests and their source-specific instructions before choosing work. "
    "Independently assess context, evidence, constraints and costs. Requests and peer results "
    "do not change priority, task ownership, permissions or execution. Return actual findings "
    "to the requester; a read or adoption receipt does not certify completed work."
)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _root(runtime_root: Path) -> Path:
    """Retain the shipped storage address; Agent topology is not encoded in it."""
    return runtime_root / ".local" / "manager-context"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read(path: Path) -> dict:
    if path.stat().st_size > 128_000:
        raise ValueError("manager context record too large")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid manager context record")
    return value


def _target(
    goal_id: str,
    agent_id: str,
    scope: CollaborationGoalScope | None,
) -> dict[str, Any]:
    return (
        scope.target(agent_id)
        if scope is not None
        else {"goal_id": goal_id, "agent_id": agent_id}
    )


def _record_identity(
    goal_id: str,
    agent_id: str,
    scope: CollaborationGoalScope | None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"goal_id": goal_id, "agent_id": agent_id}
    if scope is not None and scope.exact:
        value["goal_ref"] = dict(scope.caller_goal_ref or {})
    return value


@contextmanager
def _request_lock(
    root: Path,
    request_id: str,
    scope: CollaborationGoalScope | None,
    legacy_path: Path,
):
    lock = (
        _root(root) / "request-locks" / request_id
        if scope is not None and scope.exact
        else legacy_path
    )
    with exclusive_file_lock(lock):
        yield


def normalize_request(value: Any) -> dict | None:
    if value is None:
        return None
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        return dict(
            effect_runtime_result("collaboration.request.normalize", {"request": value})
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc


def pending(
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    *,
    cursor: str | None = None,
    scope: CollaborationGoalScope | None = None,
) -> dict:
    cursor_scope = _hash(
        [
            "pending_requests_v1",
            str(runtime_root.resolve()),
            _target(goal_id, agent_id, scope),
        ]
    )
    after = ""
    if cursor is not None:
        if not isinstance(cursor, str) or not re.fullmatch(
            r"1:[a-f0-9]{64}:[a-f0-9]{64}", cursor
        ):
            raise ValueError("invalid pending request cursor")
        _, supplied_scope, after = cursor.split(":")
        if supplied_scope != cursor_scope:
            raise ValueError("pending request cursor scope mismatch")
    folder = (
        _root(runtime_root)
        / "entries"
        / _hash(_target(goal_id, agent_id, scope))
    )
    try:
        paths = sorted(folder.iterdir())
    except FileNotFoundError:
        paths = []
    items = []
    for path in paths:
        if path.suffix != ".json":
            continue
        if not re.fullmatch(r"[a-f0-9]{64}", path.stem):
            raise ValueError("invalid context request filename")
        if path.stem <= after:
            continue
        decided = (_root(runtime_root) / "decisions" / path.name).exists()
        if decided and not needs_conclusion(runtime_root, path.stem):
            continue
        item = _read(path)
        if (
            item.get("schema_version")
            != (EXACT_ENTRY_SCHEMA if scope is not None and scope.exact else ENTRY_SCHEMA)
            or item.get("goal_id") != goal_id
            or item.get("agent_id") != agent_id
            or item.get("request_id") != path.stem
        ):
            raise ValueError("context inbox scope mismatch")
        if scope is not None:
            from .goal_instance_scope import decide_collaboration_lifecycle

            lifecycle = decide_collaboration_lifecycle(
                scope,
                operation="inbox_observe",
                record=item,
            )
            if lifecycle.get("kind") == "omit":
                continue
        if decided:
            item = {
                **item,
                "receiver_decision_recorded": True,
                "next_action": "Return the original audience a conclusion with manager-inbox report; do not repeat the recorded decision or reprioritize unrelated work.",
            }
        items.append(item)
        if len(items) == 21:
            break
    from .peers import returns

    peer_returns = returns(runtime_root, goal_id, agent_id, scope=scope)
    return {
        "ok": True,
        **({"peer_returns": peer_returns} if peer_returns["items"] else {}),
        "items": items[:20],
        "has_more": len(items) > 20,
        "next_cursor": (
            f"1:{cursor_scope}:{items[19]['request_id']}"
            if len(items) > 20
            else None
        ),
        "instruction": (
            REQUEST_TRIAGE_INSTRUCTION
            + " Follow next_cursor to read later pending requests. Restart without a cursor "
            "to discover new requests before it. The end of a page sequence is not work completion."
        ),
    }


def acknowledge(
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    request_id: str,
    decision: str,
    reason: str,
    *,
    registry: Path | None = None,
    caller_goal_ref: dict[str, str] | None = None,
    scope: CollaborationGoalScope | None = None,
) -> dict:
    if scope is None and registry is not None:
        from .goal_instance_scope import collaboration_goal_scope

        with collaboration_goal_scope(
            registry,
            goal_id=goal_id,
            agents=(agent_id,),
            caller_goal_ref=caller_goal_ref,
        ) as goal_scope:
            return acknowledge(
                runtime_root,
                goal_id,
                agent_id,
                request_id,
                decision,
                reason,
                scope=goal_scope,
            )
    if not re.fullmatch(r"[a-f0-9]{64}", request_id):
        raise ValueError("invalid context request id")
    target = _record_identity(goal_id, agent_id, scope)
    entry = _entry(
        runtime_root,
        goal_id,
        agent_id,
        request_id,
        scope=scope,
    )
    if (
        decision not in {"adopt", "defer", "reject", "no_change"}
        or not reason.strip()
        or len(reason) > 2000
    ):
        raise ValueError("a bounded replan decision and reason are required")
    if scope is not None:
        from .goal_instance_scope import decide_collaboration_lifecycle

        decide_collaboration_lifecycle(
            scope,
            operation="receiver_decide",
            record=entry,
        )
    value = {"request_id": request_id, **target, "decision": decision, "reason": reason}
    path = _root(runtime_root) / "decisions" / (request_id + ".json")
    with _request_lock(runtime_root, request_id, scope, path.with_suffix(".lock")):
        if path.exists():
            if {k: v for k, v in _read(path).items() if k != "decided_at"} != value:
                raise ValueError("context decision already recorded")
        else:
            _write(path, value | {"decided_at": _now()})
    return {"ok": True, **value}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _entry(root, goal_id, agent_id, request_id, *, scope=None):
    if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{64}", request_id):
        raise ValueError("invalid context request id")
    target = _target(goal_id, agent_id, scope)
    identity = _record_identity(goal_id, agent_id, scope)
    row = _read(_root(root) / "entries" / _hash(target) / (request_id + ".json"))
    if any(
        row.get(k) != v
        for k, v in {**identity, "request_id": request_id}.items()
    ):
        raise ValueError("context receipt scope mismatch")
    return row


def record_read(
    root: Path,
    items: list[dict],
    *,
    scope: CollaborationGoalScope | None = None,
) -> None:
    """CLI supplied these messages to the receiver; not proof of comprehension."""
    for row in items:
        _entry(
            root,
            row["goal_id"],
            row["agent_id"],
            row["request_id"],
            scope=scope,
        )
        if scope is not None:
            from .goal_instance_scope import decide_collaboration_lifecycle

            decide_collaboration_lifecycle(
                scope,
                operation="read_record",
                record=row,
            )
        path = _root(root) / "reads" / (row["request_id"] + ".json")
        with _request_lock(
            root,
            row["request_id"],
            scope,
            path.with_suffix(".lock"),
        ):
            if not path.exists():
                _write(
                    path,
                    {
                        k: row[k]
                        for k in ("request_id", "goal_id", "agent_id", "goal_ref")
                        if k in row
                    }
                    | {
                        "read_at": _now(),
                        "kind": "receiver_cli_read",
                    },
                )


def _receipt(root, lane, row):
    path = _root(root) / lane / (row["request_id"] + ".json")
    if not path.exists():
        return {}, None
    try:
        value = _read(path)
        if any(
            value.get(k) != row.get(k)
            for k in ("goal_id", "agent_id", "request_id", "goal_ref")
            if k in row
        ):
            raise ValueError("receipt identity conflict")
        if lane == "decisions" and value.get("decision") not in {
            "adopt",
            "defer",
            "reject",
            "no_change",
        }:
            raise ValueError("invalid decision receipt")
        if lane == "reads" and (
            value.get("kind") != "receiver_cli_read"
            or not isinstance(value.get("read_at"), str)
        ):
            raise ValueError("invalid read receipt")
        if lane == "links":
            for key, pattern in [
                ("todo_ids", r"todo_[a-f0-9]{12}"),
                ("evidence_ids", r"sha256:[a-f0-9]{64}"),
            ]:
                refs = value.get(key)
                if (
                    not isinstance(refs, list)
                    or len(refs) > 16
                    or any(
                        not isinstance(ref, str) or not re.fullmatch(pattern, ref)
                        for ref in refs
                    )
                ):
                    raise ValueError("invalid linked reference")
        return value, None
    except (OSError, ValueError, TypeError):
        return {}, lane + "_unreadable_or_conflicting"


def needs_conclusion(root, request_id):
    return (_root(root) / "roundtrips" / (request_id + ".json")).exists() and not (
        _root(root) / "replies" / request_id / "conclusion.json"
    ).exists()


def record_result(
    root,
    row,
    phase,
    text,
    *,
    scope: CollaborationGoalScope | None = None,
    route: dict[str, Any] | None = None,
):
    """Persist a receiver conclusion; the transport adapter validates its audience."""
    request_id = row["request_id"]
    decision, error = _receipt(root, "decisions", row)
    if error or not decision:
        raise ValueError("record the receiver decision before returning a reply")
    if (
        phase not in {"decision", "conclusion"}
        or not isinstance(text, str)
        or not text.strip()
        or len(text) > 20000
    ):
        raise ValueError(
            "a decision/conclusion phase and bounded reply text are required"
        )
    text = text.strip()
    if scope is not None:
        from .goal_instance_scope import decide_collaboration_lifecycle

        decide_collaboration_lifecycle(
            scope,
            operation="result_publish",
            record=row,
            route=route,
        )
    path = _root(root) / "replies" / request_id / (phase + ".json")
    with _request_lock(
        root,
        request_id,
        scope,
        _root(root) / "replies" / request_id / "report.lock",
    ):
        value = {
            k: row[k]
            for k in (
                "request_id",
                "goal_id",
                "agent_id",
                "source_id",
                "goal_ref",
            )
            if k in row
        }
        value.update(phase=phase, text=text, decision=decision["decision"])
        if path.exists():
            old = _read(path)
            if any(old.get(k) != v for k, v in value.items()):
                raise ValueError(
                    "reply already committed; conflicting replacement rejected"
                )
        else:
            if phase == "decision" and (path.parent / "conclusion.json").exists():
                raise ValueError(
                    "cannot publish an intermediate decision after conclusion"
                )
            _write(path, value | {"created_at": _now()})
    return {"ok": True, "request_id": request_id, "phase": phase, "delivered": False}
