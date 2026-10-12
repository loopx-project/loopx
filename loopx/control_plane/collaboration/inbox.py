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


def normalize_source_context(value: str) -> str:
    """The shared typed owner qualifies source text before persistence."""
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        return str(effect_runtime_result(
            "collaboration.source_context.normalize", {"source_message": value},
        )["source_message"])
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc


def pending(
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    *,
    cursor: str | None = None,
    operation_cursor: str | None = None,
    scope: CollaborationGoalScope | None = None,
) -> dict:
    cursor_scope = _hash(
        (
            [
                "pending_requests_v1",
                str(runtime_root.resolve()),
                _target(goal_id, agent_id, scope),
            ]
            if scope is not None and scope.exact
            else [
                "pending_requests_v1",
                str(runtime_root.resolve()),
                goal_id,
                agent_id,
            ]
        )
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
    items, batch = [], []

    def append_batch():
        classified = iter(_pending_receipt_states(runtime_root, batch))
        for row in batch:
            if isinstance(row, Exception):
                raise row
            state = next(classified)
            if state["kind"] == "settled":
                continue
            if scope is not None:
                from .goal_instance_scope import decide_collaboration_lifecycle

                lifecycle = decide_collaboration_lifecycle(
                    scope, operation="inbox_observe", record=row,
                )
                if lifecycle.get("kind") == "omit":
                    continue
            item = {**row, "inbox_state": state["kind"]}
            item["recorded_decision"] = state["recorded_decision"]
            if state["recorded_decision"] is not None:
                item["receiver_decision_recorded"] = True
                item["next_action"] = "Review this request's recorded decision and linked work before returning its actual result. A recorded adoption is not a completed task; preserve the current owner and do not reprioritize unrelated work."
            if state["warnings"]:
                item["warnings"] = state["warnings"]
                item["next_action"] = "Receiver receipt readback is unavailable or conflicting; recover the original receipt before continuing this request. Do not repeat the decision, result or execution. Other requests can continue."
            items.append(item)
            if len(items) == 21:
                break
        batch.clear()

    for path in paths:
        if path.suffix != ".json" or not BARE_SHA256_PATTERN.fullmatch(path.stem):
            continue  # Ignore lock holder sidecars and other non-entry files.
        if path.stem <= after:
            continue
        try:
            batch.append(_pending_entry(path, goal_id, agent_id, scope))
        except (OSError, ValueError) as exc:
            # Evaluate errors in scan order; batch lookahead cannot let a later
            # page's damaged entry block an already complete current page.
            batch.append(exc)
        if len(batch) == 128:
            append_batch()
            if len(items) == 21:
                break
    if batch:
        append_batch()
    from .peers import returns

    peer_returns = returns(runtime_root, goal_id, agent_id, scope=scope)
    from .operation_handoff import pending_operation_handoffs

    operation_handoffs = pending_operation_handoffs(
        runtime_root,
        goal_id,
        agent_id,
        registry_path=scope.registry_path if scope is not None else None,
        scope=scope,
        cursor=operation_cursor,
        cursor_scope=cursor_scope,
    )
    return {
        "ok": True,
        **(
            {
                "operation_handoffs": operation_handoffs["items"],
                "operation_handoff_pending_count": operation_handoffs["pending_count"],
                "operation_handoff_overflow": operation_handoffs["overflow"],
                "operation_handoff_next_cursor": operation_handoffs["next_cursor"],
            }
            if operation_handoffs["items"] or operation_cursor is not None
            else {}
        ),
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


def _pending_entry(path, goal_id, agent_id, scope):
    if not BARE_SHA256_PATTERN.fullmatch(path.stem):
        raise ValueError("invalid context request filename")
    item = _read(path)
    if (
        item.get("schema_version")
        != (EXACT_ENTRY_SCHEMA if scope is not None and scope.exact else ENTRY_SCHEMA)
        or item.get("goal_id") != goal_id
        or item.get("agent_id") != agent_id
        or item.get("request_id") != path.stem
    ):
        raise ValueError("context inbox scope mismatch")
    return item


def _pending_receipt_states(runtime_root, rows):
    """Observe private files, then batch the typed read model by wire bytes."""
    from ..effect_runtime import effect_runtime_result

    root = _root(runtime_root)
    observations, states, encoded_bytes = [], [], 0
    for row in rows:
        if isinstance(row, Exception):
            continue
        request_id = row["request_id"]
        observation = {
            "request": {key: row[key] for key in (
                "request_id", "goal_id", "agent_id", "source_id", "goal_ref"
            ) if key in row},
            "route_present": (root / "roundtrips" / (request_id + ".json")).exists(),
            "decision": _observe_receipt(root / "decisions" / (request_id + ".json")),
            "conclusion": _observe_receipt(root / "replies" / request_id / "conclusion.json"),
        }
        size = len(json.dumps(observation, separators=(",", ":")).encode())
        # The existing bridge serializes ASCII-escaped JSON and admits 2 MiB.
        # Leave room for its envelope; legal Unicode replies must not turn
        # a bounded page into another oversized history request.
        if observations and encoded_bytes + size > 1_000_000:
            states.extend(effect_runtime_result(
                "collaboration.inbox.inspect_receipts", {"observations": observations}
            )["items"])
            observations, encoded_bytes = [], 0
        observations.append(observation)
        encoded_bytes += size
    if observations:
        states.extend(effect_runtime_result(
            "collaboration.inbox.inspect_receipts", {"observations": observations}
        )["items"])
    return states


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
        if path.exists():
            if {k: v for k, v in _read(path).items() if k != "decided_at"} != value:
                raise ValueError("context decision already recorded")
        else:
            _write(path, value | {"decided_at": _now()})
    return {"ok": True, **value}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _entry(root, goal_id, agent_id, request_id, *, scope=None):
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
):
    """Persist a receiver conclusion; the transport adapter validates its audience."""
    request_id = row["request_id"]
    decision, error = _receipt(root, "decisions", row)
    if error or not decision:
        raise ValueError("record the receiver decision before returning a reply")
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
        observations = [_result_observation(path.stem, _read(path)) for path in result_paths(folder)]
        try:
            plan = effect_runtime_result("collaboration.result.plan_publication", {
                "request": row, "phase": phase, "text": text,
                "decision": decision["decision"], "update_id": update_id,
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
        **({"result_key": plan["result_key"]} if update_id is not None else {}),
    }


def result_paths(folder: Path):
    """Enumerate the result-store layout, excluding delivery and lock sidecars."""
    paths = [path for path in folder.glob("*.json")
             if path.stem in {"decision", "conclusion"}
             or re.fullmatch(r"conclusion-[0-9]{8}", path.stem)]
    return sorted(paths, key=lambda path: (path.stem not in {"decision", "conclusion"},
                                          path.stem != "decision", path.stem))


def result_identity_matches(reply, row, path):
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        observations = [_result_observation(path.stem, reply)]
        if reply.get("update_id") is not None:
            observations = [_result_observation(candidate.stem, _read(candidate))
                            for candidate in result_paths(path.parent)]
        effect_runtime_result("collaboration.result.plan_publication", {
            "request": row, "phase": reply.get("phase"), "text": reply.get("text"),
            "decision": reply.get("decision"), "update_id": reply.get("update_id"),
            "attachments": reply.get("attachments"),
            "results": observations,
        })
    except (EffectRuntimeRejected, OSError, ValueError):
        return False
    return reply.get("result_key", reply.get("phase")) == path.stem


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
        ) if field in value},
        "text_chars": len(text),
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def iter_result_paths(folder: Path):
    for request_folder in sorted(folder.glob("*")):
        if request_folder.is_dir():
            yield from result_paths(request_folder)


def prior_result_delivered(path, reply):
    """Ordered transport: a newer update cannot race an uncertain earlier send."""
    from ..effect_runtime import effect_runtime_result

    previous = reply.get("previous_result_key")
    state_path = path.with_name(previous + ".delivery.json") if previous else None
    delivery = _read(state_path) if state_path and state_path.exists() else None
    return effect_runtime_result("collaboration.result.delivery_ready", {
        "result": reply, "previous_delivery": delivery,
    })["ready"]
