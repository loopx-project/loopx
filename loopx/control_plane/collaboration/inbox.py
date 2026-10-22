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
from ...file_lock import exclusive_cross_runtime_file_lock
from ..runtime.file_paths import windows_extended_path
from ..content_digest import BARE_SHA256_PATTERN, ENVELOPED_SHA256_PATTERN
from ..todos.contract import TODO_ID_PATTERN

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


def read_inbox_transaction(root: Path, request: dict, *, scope=None) -> dict:
    from ..effect_runtime import EffectRuntimeIOError, EffectRuntimeRejected, effect_runtime_result

    facts = {
        "runtime_root": str(Path(root).resolve()),
        "scope": {
            "profile_id": scope.profile_id if scope is not None else None,
            "caller_goal_ref": scope.caller_goal_ref if scope is not None else None,
            "current_goal_ref": scope.current_goal_ref if scope is not None else None,
        },
        "observed_at": _now(),
        **request,
    }
    if request.get("operation") == "record_read":
        facts["items"] = [{key: item[key] for key in (
            "goal_id", "agent_id", "goal_ref", "request_id", "request_revision", "request_revision_digest",
        ) if key in item} for item in request["items"]]
    # Full source pages may exceed the ordinary wire even when every record is legal.
    snapshot = request["operation"] in {"pending", "turn_start", "receiver_read", "returns"}
    snapshot = snapshot or len(request.get("rows", [])) > 1 or request.get("include_agenda") is True
    try:
        result = effect_runtime_result("collaboration.inbox.read", facts, large_local_snapshot=snapshot)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    except EffectRuntimeIOError as exc:
        # The native owner reports filesystem failures as typed runtime errors.
        # Callers and the CLI still classify them as IO failures; keep the
        # retryable/permanent diagnostic instead of leaking a runtime traceback.
        raise OSError(f"{exc.diagnostic_code}: {exc}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("native Inbox read must return an object")
    return result


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _root(runtime_root: Path) -> Path:
    """Retain the shipped storage address; Agent topology is not encoded in it."""
    # Full request hashes plus lock sidecars can exceed MAX_PATH even in an
    # ordinary workspace. Keep extended syntax inside the private store.
    return windows_extended_path(runtime_root / ".local" / "manager-context")


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
    with exclusive_cross_runtime_file_lock(lock):
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


def normalize_source_context(value: str) -> str:
    """The shared typed owner qualifies source text before persistence."""
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        return str(effect_runtime_result(
            "collaboration.source_context.normalize", {"source_message": value},
        )["source_message"])
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc


def plan_request_publication(request, *, entry, operation=None):
    """Adapt one locked source snapshot to the shared typed publication owner."""
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        return effect_runtime_result("collaboration.request.plan_publication", {
            "request": request, "entry": entry, "operation": operation,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc




def pending(
    runtime_root: Path, goal_id: str, agent_id: str, *, cursor: str | None = None,
    operation_cursor: str | None = None, request_id: str | None = None,
    scope: CollaborationGoalScope | None = None,
) -> dict:
    snapshot = read_inbox_transaction(runtime_root, {
        "operation": "pending", "goal_id": goal_id, "agent_id": agent_id,
        "cursor": cursor, "operation_cursor": operation_cursor, "request_id": request_id,
    }, scope=scope)
    snapshot.update(goal_id=goal_id, agent_id=agent_id)
    return _pending_result(runtime_root, snapshot, scope, operation_cursor)


def _pending_result(root, snapshot, scope, operation_cursor):
    result = snapshot["result"]
    if "request_id" in result or snapshot.get("completed"):
        return result
    from .operation_handoff import pending_operation_handoffs

    handoffs = pending_operation_handoffs(
        root, snapshot["goal_id"],
        snapshot["agent_id"], registry_path=scope.registry_path if scope else None,
        scope=scope, cursor=operation_cursor, cursor_scope=snapshot["cursor_scope"],
    )
    if handoffs["items"] or operation_cursor is not None:
        result.update(operation_handoffs=handoffs["items"],
            operation_handoff_pending_count=handoffs["pending_count"],
            operation_handoff_overflow=handoffs["overflow"],
            operation_handoff_next_cursor=handoffs["next_cursor"])
    return result




def _pending_receipt_states(runtime_root, rows):
    return read_inbox_transaction(runtime_root, {
        "operation": "receipt_states", "rows": rows,
    })["items"]


def controlled_request_readback(root: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    """Reuse the actual inbox/result owners for every current request surface."""
    if "request_control" not in row:
        return None
    return _pending_receipt_states(root, [row])[0]


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
    request_revision: int | None = None,
    request_revision_digest: str | None = None,
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
                request_revision=request_revision,
                request_revision_digest=request_revision_digest,
            )
    if not BARE_SHA256_PATTERN.fullmatch(request_id):
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
        current = _entry(runtime_root, goal_id, agent_id, request_id, scope=scope)
        if "request_control" in current:
            from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
            from .goal_instance_scope import goal_accepts_collaboration

            prior, prior_error = _receipt(runtime_root, "decisions", current)
            read, read_error = _receipt(runtime_root, "reads", current)
            try:
                plan = effect_runtime_result("collaboration.request.assess", {
                    "entry": current, "actor_agent_id": agent_id, "observed_at": _now(),
                    "goal_active": scope is not None and goal_accepts_collaboration(scope.goal),
                    "revision": request_revision, "revision_digest": request_revision_digest,
                    "decision": decision, "reason": reason, "legacy_assessment": prior or None,
                    "legacy_assessment_unavailable": bool(prior_error), "legacy_read": read or None, "legacy_read_unavailable": bool(read_error)})
            except EffectRuntimeRejected as exc:
                raise ValueError(str(exc)) from exc
            if plan["changed"]:
                entry_path = _root(runtime_root) / "entries" / _hash(_target(goal_id, agent_id, scope)) / (request_id + ".json")
                _write(entry_path, plan["entry"])
            return {"ok": True, **value, "request_revision": plan["result"]["revision"],
                "current_request_revision": plan["view"]["revision"], "request_control": plan["view"]}
        if request_revision is not None or request_revision_digest is not None:
            raise ValueError("explicit revision assessment requires a controlled request")
        if path.exists():
            if {k: v for k, v in _read(path).items() if k != "decided_at"} != value:
                raise ValueError("context decision already recorded")
        else:
            _write(path, value | {"decided_at": _now()})
    return {"ok": True, **value}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry(root: Path, goal_id: str, agent_id: str, request_id: str, *,
           scope: CollaborationGoalScope | None = None) -> dict[str, Any]:
    if not isinstance(request_id, str) or not BARE_SHA256_PATTERN.fullmatch(request_id):
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


def record_read(root: Path, items: list[dict], *, scope: CollaborationGoalScope | None = None) -> None:
    """Record supplied source without certifying comprehension or adoption."""
    if not items:
        return
    result = read_inbox_transaction(root, {
        "operation": "record_read", "goal_id": items[0]["goal_id"],
        "agent_id": items[0]["agent_id"], "items": items, "deliveries": [],
    }, scope=scope)
    for row, receipt in zip(items, result["read_receipts"], strict=True):
        if receipt is not None:
            row["request_read_receipt"] = receipt


def _receipt(root: Path, lane: str, row: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
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
                ("todo_ids", TODO_ID_PATTERN),
                ("evidence_ids", ENVELOPED_SHA256_PATTERN),
            ]:
                refs = value.get(key)
                if (
                    not isinstance(refs, list)
                    or len(refs) > 16
                    or any(
                        not isinstance(ref, str) or not pattern.fullmatch(ref)
                        for ref in refs
                    )
                ):
                    raise ValueError("invalid linked reference")
        return value, None
    except (OSError, ValueError, TypeError):
        return {}, lane + "_unreadable_or_conflicting"


def _observe_receipt(path: Path) -> dict:
    try:
        value = _read(path)
        # Python's JSON reader accepts non-finite numbers; the typed bridge
        # does not. Keep a damaged record local to its request, not its batch.
        json.dumps(value, allow_nan=False)
        return {"state": "read", "value": value}
    except FileNotFoundError:
        return {"state": "absent"}
    except (OSError, ValueError):
        return {"state": "unavailable"}


def record_result(
    root,
    row,
    phase,
    text,
    *,
    scope: CollaborationGoalScope | None = None,
    route: dict[str, Any] | None = None,
    update_id: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    request_revision: int | None = None,
):
    """Persist a receiver conclusion; the transport adapter validates its audience."""
    request_id = row["request_id"]
    if scope is not None:
        from .goal_instance_scope import decide_collaboration_lifecycle

        decide_collaboration_lifecycle(
            scope,
            operation="result_publish",
            record=row,
            route=route,
        )
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    folder = _root(root) / "replies" / request_id
    with _request_lock(root, request_id, scope, folder / "report.lock"):
        row = _entry(root, row["goal_id"], row["agent_id"], request_id, scope=scope)
        decision, error = _receipt(root, "decisions", row)
        if "request_control" not in row and (error or not decision):
            raise ValueError("record the receiver decision before returning a reply")
        observations = [_result_observation(path.stem, _read(path)) for path in result_paths(folder)]
        try:
            plan = effect_runtime_result("collaboration.result.plan_publication", {
                "request": row, "phase": phase, "text": text,
                "decision": decision.get("decision"), "update_id": update_id,
                "request_revision": request_revision, "legacy_assessment": decision or None,
                "attachments": attachments,
                "results": observations,
            })
        except EffectRuntimeRejected as exc:
            raise ValueError(str(exc)) from exc
        path = folder / (plan["result_key"] + ".json")
        if not plan["replay"]:
            _write(path, plan["value"] | {"created_at": _now()})
    return {
        "ok": True, "request_id": request_id, "phase": phase, "delivered": False,
        **({"result_key": plan["result_key"]} if update_id is not None or "request_revision" in plan else {}),
        **({key: plan[key] for key in ("request_revision", "current_request_revision")} if "request_revision" in plan else {}),
    }


def result_paths(folder: Path):
    """Enumerate the result-store layout, excluding delivery and lock sidecars."""
    paths = [path for path in folder.glob("*.json")
             if path.stem in {"decision", "conclusion"}
             or re.fullmatch(r"(?:conclusion|decision)-[0-9]{8}", path.stem)]
    return sorted(paths, key=lambda path: (path.stem not in {"decision", "conclusion"},
                                          path.stem != "decision", path.stem))


def inspect_result_identity(reply, row, path):
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        observations = [_result_observation(path.stem, reply)]
        if reply.get("update_id") is not None or reply.get("phase") == "decision":
            observations = [_result_observation(candidate.stem, _read(candidate))
                            for candidate in result_paths(path.parent)]
        legacy = _observe_receipt(path.parent.parent.parent / "decisions" / (row["request_id"] + ".json")) if (
            "request_control" in row or "request_revision" in reply) else {}
        inspected = effect_runtime_result("collaboration.result.plan_publication", {
            "request": row, "phase": reply.get("phase"), "text": reply.get("text"),
            "decision": reply.get("decision"), "update_id": reply.get("update_id"),
            "attachments": reply.get("attachments"),
            "request_revision": reply.get("request_revision"), "legacy_assessment": legacy.get("value"),
            "results": observations,
        })
    except (EffectRuntimeRejected, OSError, ValueError):
        return None
    return inspected if reply.get("result_key", reply.get("phase")) == path.stem else None


def result_identity_matches(reply, row, path):
    return inspect_result_identity(reply, row, path) is not None


def _result_observation(key, value):
    """Keep full texts in their receipts rather than replaying them over the bridge."""
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("bounded reply text is required")
    return {
        "key": key,
        "value": {field: value[field] for field in (
            "request_id", "goal_id", "agent_id", "source_id", "goal_ref", "phase",
            "decision", "result_key", "previous_result_key", "update_id", "attachments",
            "request_revision", "request_revision_digest",
        ) if field in value},
        "text_chars": len(text),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def iter_result_paths(folder: Path):
    for request_folder in sorted(folder.glob("*")):
        if request_folder.is_dir():
            yield from result_paths(request_folder)


# Retained public re-exports share the native delivery contract.
_delivery_contract = json.loads(Path(__file__).with_name("return_delivery_contract.json").read_text(encoding="utf-8"))
DELIVERY_STATUSES = set(_delivery_contract["delivery_statuses"])
DELIVERY_ERRORS = set(_delivery_contract["delivery_errors"])


def reply_status(root, row):
    result = []
    for path in result_paths(_root(root) / "replies" / row["request_id"]):
        phase = "decision" if path.stem == "decision" or path.stem.startswith("decision-") else "conclusion"
        state_path = path.with_name(path.stem + ".delivery.json")
        try:
            reply = _read(path)
            state = _read(state_path) if state_path.exists() else {}
        except (OSError, ValueError):
            result.append({
                "phase": phase, "status": "explicit_unverified",
                **({"result_key": path.stem} if path.stem != phase else {}),
                "created_at": None, "delivered_at": None,
                "error": "delivery_state_unreadable",
            })
            continue
        status = state.get("status", "queued")
        error = state.get("error")
        if status not in DELIVERY_STATUSES:
            status, error = "explicit_unverified", "delivery_state_unreadable"
        elif error is not None and error not in DELIVERY_ERRORS:
            status, error = "explicit_unverified", "delivery_state_unreadable"
        delivered_at = state.get("delivered_at")
        if not isinstance(delivered_at, str) or len(delivered_at) > 80:
            delivered_at = None
        item = {
            "phase": phase,
            **({"result_key": path.stem} if path.stem != phase else {}),
            "status": status,
            "created_at": reply.get("created_at"),
            "delivered_at": delivered_at,
            "error": error,
        }
        if state.get("verification") == "reconciled_after_restart":
            item["verification"] = "reconciled_after_restart"
        result.append(item)
    return result


def prior_result_delivered(path, reply):
    """Ordered transport: a newer update cannot race an uncertain earlier send."""
    from ..effect_runtime import effect_runtime_result

    previous = reply.get("previous_result_key")
    state_path = path.with_name(previous + ".delivery.json") if previous else None
    delivery = _read(state_path) if state_path and state_path.exists() else None
    return effect_runtime_result("collaboration.result.delivery_ready", {
        "result": reply, "previous_delivery": delivery,
    })["ready"]
