"""Queued Chat turn discovery, admission, mutation, steering, and recovery helpers."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from typing import cast
import uuid

from .attachments import normalize_chat_attachments, validate_chat_turn_envelope
from .ingress_common import (
    IngressStoreHostProtocol,
    LockFileFn,
    NONTERMINAL_TURN_STATES,
    as_dict,
    as_mapping,
    is_valid_turn_record,
    read_chat_json,
)


def turn_discovery_rows(
    host: IngressStoreHostProtocol,
    session_id: str,
    turns_dir: Path,
    read: Callable[[Path], dict[str, object]],
) -> Iterator[tuple[Path, dict[str, object], dict[str, object] | None]]:
    """Iterate cached terminal observations or freshly parsed Chat Turn rows."""
    from ..chat_store import TERMINAL_TURN_STATES
    from ..control_plane.collaboration.source_chat_observation import CHAT_TURN_SCHEMA_VERSION

    def terminal(path: Path, payload: Mapping[str, object]) -> dict[str, object] | None:
        status = payload.get("status")
        client_id = payload.get("client_turn_id")
        if (
            payload.get("schema_version") == CHAT_TURN_SCHEMA_VERSION
            and payload.get("session_id") == session_id
            and payload.get("turn_id") == path.stem
            and isinstance(status, str)
            and status in TERMINAL_TURN_STATES
            and (client_id is None or isinstance(client_id, str))
        ):
            return {
                "schema_version": CHAT_TURN_SCHEMA_VERSION,
                "session_id": session_id,
                "turn_id": path.stem,
                "status": status,
                "client_turn_id": client_id,
            }
        return None

    failed = getattr(host, "_last_read_error_paths", None)
    if isinstance(failed, dict):
        failed.pop(session_id, None)
    paths = (path for path in turns_dir.glob("*.json") if not path.name.endswith(".events.json"))
    fresh: dict[Path, dict[str, object]] = {}

    def read_current(path: Path) -> dict[str, object]:
        try:
            payload = read(path)
        except Exception:
            failed_map = getattr(host, "_last_read_error_paths", None)
            if not isinstance(failed_map, dict):
                failed_map = {}
                setattr(host, "_last_read_error_paths", failed_map)
            failed_map[session_id] = path
            raise
        fresh[path] = payload
        return payload

    for path, observation in host._terminal_turn_discovery.rows(
        turns_dir,
        paths,
        read=read_current,
        project=terminal,
    ):
        # Retain only the current iteration's complete read, never a body
        # in the discovery cache or a second read on the cold path.
        yield path, observation, fresh.pop(path, None)


def matched_discovered_turn(
    session_id: str,
    client_id: str,
    path: Path,
    fresh: dict[str, object] | None,
) -> dict[str, object]:
    """Validate and return the complete Turn record matched during discovery."""
    from ..control_plane.collaboration.source_chat_observation import CHAT_TURN_SCHEMA_VERSION

    payload = fresh if fresh is not None else read_chat_json(path)
    if (
        payload.get("schema_version") != CHAT_TURN_SCHEMA_VERSION
        or payload.get("session_id") != session_id
        or payload.get("turn_id") != path.stem
        or payload.get("client_turn_id") != client_id
    ):
        raise ValueError("Matched Chat Turn is unavailable; retry the same client_turn_id.")
    return payload


def turn_for_client(
    host: IngressStoreHostProtocol,
    session_id: str,
    client_turn_id: str,
) -> dict[str, object] | None:
    """Return the persisted Turn owned by ``client_turn_id`` in ``session_id`` if any."""
    turns_dir = host._session_dir(session_id) / "turns"
    if not turns_dir.is_dir():
        return None
    for path, observation, fresh in host._turn_discovery_rows(session_id, turns_dir, read_chat_json):
        if observation.get("client_turn_id") != client_turn_id:
            continue
        # Replay validation needs the complete current record, including
        # the original request body and the latest persisted result.
        return host._matched_discovered_turn(session_id, client_turn_id, path, fresh)
    return None


def queued_turns(
    host: IngressStoreHostProtocol,
    session_id: str,
    *,
    strict: bool = False,
    include_reserved: bool = False,
    capacity_at: str | None = None,
) -> list[dict[str, object]]:
    """List ordered queued Turns projected through the TS queue selection owner."""
    from ..chat_store import TERMINAL_TURN_STATES
    from ..control_plane.collaboration.source_chat_observation import CHAT_TURN_SCHEMA_VERSION

    session = host.load_session(session_id)
    if session is None:
        raise KeyError("chat session was not found")
    active_turn_id = str(session.get("active_turn_id") or "")
    turns_dir = host._session_dir(session_id) / "turns"
    rows: list[dict[str, object]] = []
    valid_statuses = TERMINAL_TURN_STATES | NONTERMINAL_TURN_STATES

    def read(path: Path) -> dict[str, object]:
        if strict:
            try:
                raw_payload: object = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise OSError("Queued Turn state is unreadable") from exc
            if not is_valid_turn_record(
                raw_payload,
                schema_version=CHAT_TURN_SCHEMA_VERSION,
                valid_statuses=valid_statuses,
                session_id=session_id,
                turn_id=path.stem,
            ):
                raise OSError("Queued Turn state is invalid")
            return raw_payload
        return read_chat_json(path)

    for _, payload, _ in host._turn_discovery_rows(session_id, turns_dir, read):
        if (
            payload.get("schema_version") == CHAT_TURN_SCHEMA_VERSION
            and payload.get("status") == "queued"
            and str(payload.get("turn_id") or "") != active_turn_id
        ):
            rows.append(payload)
    # The ordinary read has no selectable Turn when storage has no queued
    # candidates. Explicit capacity and strict reads still use the owner.
    if not rows and not strict and not include_reserved and capacity_at is None:
        return []

    from ..control_plane.chat_turn_acceptance import queued_chat_turn_ids

    selected = set(
        queued_chat_turn_ids(
            rows,
            active_turn_id,
            include_reserved=include_reserved,
            capacity_at=capacity_at,
        )
    )
    return sorted(
        [row for row in rows if row["turn_id"] in selected],
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("turn_id") or ""),
        ),
    )


def queue_steering_lock(
    host: IngressStoreHostProtocol,
    session_id: str,
    turn_id: str,
    *,
    lock_fn: LockFileFn,
) -> AbstractContextManager[object]:
    """Use the same Session->Turn fences as automatic queue consumption."""
    stack = ExitStack()
    try:
        stack.enter_context(host._session_lock(session_id))
        stack.enter_context(
            lock_fn(
                host._session_path(session_id),
                agent_id="loopx-chat",
                operation="steer_session_queue_turn",
            )
        )
        stack.enter_context(
            lock_fn(
                host._turn_path(session_id, turn_id),
                agent_id="loopx-chat",
                operation="steer_queued_chat_turn",
            )
        )
        return stack
    except BaseException:
        stack.close()
        raise


def _apply_queued_input_message_patch(
    messages_path: Path,
    *,
    turn_id: str,
    turn: Mapping[str, object],
    receipt: Mapping[str, object],
    message_patch: Mapping[str, object],
    lock_fn: LockFileFn,
) -> None:
    from ..chat_store import _read_jsonl
    from ..file_lock import write_bytes_atomic

    with lock_fn(messages_path, agent_id="loopx-chat", operation="update_queued_input"):
        rows = cast(
            list[dict[str, object]],
            _read_jsonl(messages_path, raise_on_error=True, strict=True),
        )
        matches = [r for r in rows if r.get("turn_id") == turn_id and r.get("role") == "user"]
        if len(matches) != 1:
            raise OSError("Original queued input is unavailable")
        row = matches[0]
        if row.get("queue_input_revision", 0) != receipt["input_revision"]:
            if row.get("text") != turn["message"] or row.get("attachments") != turn.get("attachments"):
                raise OSError("Original queued input changed")
            if "text" in message_patch:
                history = row.get("queue_edit_history")
                prior_history = list(history) if isinstance(history, list) else []
                row["queue_edit_history"] = [
                    *prior_history,
                    {
                        "text": row["text"],
                        "input_revision": turn.get("input_revision", 0),
                        "operation_id": receipt["operation_id"],
                    },
                ]
            row.update(message_patch)
            write_bytes_atomic(
                messages_path,
                ("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)).encode(),
                preserve_mode=True,
            )
        elif any(row.get(key) != value for key, value in message_patch.items()):
            raise OSError("Queued input revision conflicts with its receipt")


def mutate_queued_input(
    host: IngressStoreHostProtocol,
    session_id: str,
    turn_id: str,
    request: Mapping[str, object],
    *,
    lock_fn: LockFileFn,
) -> dict[str, object]:
    """Execute the TS owner's recoverable input plan under the consumer fences."""
    from ..chat_store import _atomic_write_json, utc_now
    from ..control_plane.chat_turn_acceptance import plan_queued_chat_mutation

    with host.queue_steering_lock(session_id, turn_id):
        session, turn = host.load_session(session_id), host.load_turn(session_id, turn_id)
        if session is None or turn is None:
            raise KeyError("chat turn was not found")
        request_dict = (
            cast(dict[str, object], request) if isinstance(request, dict) else as_dict(request)
        )
        plan = as_mapping(plan_queued_chat_mutation(session, turn, request_dict))
        if plan["action"] == "reject":
            raise RuntimeError(str(plan["error_code"]))
        receipt = as_dict(plan["receipt"])
        if plan["action"] == "apply":
            if turn.get("_queue_mutation") is None:
                host._recover_queued_turn_input(session_id, turn)
            path = host._turn_path(session_id, turn_id)
            # The receipt precedes all other writes. A partial write stays
            # reserved, including across process death; only this identity
            # can finish it. No native call or active-Turn interrupt occurs.
            turn = {
                **turn,
                "_queue_mutation": receipt,
                "_queue_original_message": turn.get("_queue_original_message", turn["message"]),
            }
            _atomic_write_json(path, turn, preserve_mode=True)
            messages_path = host._session_dir(session_id) / "messages.jsonl"
            _apply_queued_input_message_patch(
                messages_path,
                turn_id=turn_id,
                turn=turn,
                receipt=receipt,
                message_patch=as_mapping(plan["message_patch"]),
                lock_fn=lock_fn,
            )
            event = as_mapping(plan["event"])
            event_kind = str(event["kind"])
            event_payload = as_dict(event["payload"])
            if not any(
                e.get("kind") == event_kind and e.get("payload") == event_payload
                for e in host.events_after(session_id, turn_id, None)
            ):
                host.append_event(session_id, turn_id, kind=event_kind, payload=event_payload)
            # Metadata edits do not invent worker activity; invalidate the
            # existing Session history cache without changing liveness.
            session["updated_at"] = utc_now()
            _atomic_write_json(host._session_path(session_id), session, preserve_mode=True)
            turn.update(as_mapping(plan["turn_patch"]))
            existing_ops = turn.get("_queue_operations")
            prior_ops = list(existing_ops) if isinstance(existing_ops, list) else []
            turn["_queue_operations"] = [*prior_ops, receipt]
            turn.pop("_queue_mutation")
            if receipt["action"] == "cancel":
                turn["completed_at"] = utc_now()
            _atomic_write_json(path, turn, preserve_mode=True)
        return {
            "session_id": session_id,
            "queued_turn_id": turn_id,
            "operation_id": receipt["operation_id"],
            "action": receipt["action"],
            "input_revision": receipt["input_revision"],
            "status": "applied",
        }


def write_queue_steering(
    host: IngressStoreHostProtocol,
    session_id: str,
    turn: dict[str, object],
    plan: Mapping[str, object],
) -> dict[str, object]:
    """Persist the typed plan while the caller holds both queue fences."""
    from ..chat_store import _atomic_write_json, utc_now

    if plan["action"] not in {"dispatch", "complete", "release"}:
        return turn
    reservation = as_dict(plan["reservation"])
    updated_turn: dict[str, object] = {**turn, "_queue_steering": reservation}
    if plan["action"] == "complete":
        # Keep the existing SSE response envelope for older readers. The
        # adoption marker distinguishes this receipt from a model answer;
        # no assistant transcript row or invented answer is published.
        response: dict[str, object] = {
            "schema_version": "loopx_chat_agent_response_v0",
            "message": "",
            "proposals": [],
            "protected_action": None,
            "gate": None,
        }
        updated_turn.update(
            status="completed",
            completed_at=updated_turn.get("completed_at") or utc_now(),
            response=response,
        )
    turn_id = str(updated_turn["turn_id"])
    _atomic_write_json(host._turn_path(session_id, turn_id), updated_turn, preserve_mode=True)
    if plan["action"] == "complete":
        payload: dict[str, object] = {
            "steered_into_turn_id": reservation["target_turn_id"],
            "response": updated_turn["response"],
        }
        if not any(
            e.get("kind") == "turn.completed" and e.get("payload") == payload
            for e in host.events_after(session_id, turn_id, None)
        ):
            host.append_event(session_id, turn_id, kind="turn.completed", payload=payload)
    return updated_turn


def complete_recovered_terminalization(
    host: IngressStoreHostProtocol,
    session_id: str,
    turn_id: str,
    session: Mapping[str, object] | None,
    turn: Mapping[str, object] | None,
    *,
    recovery_messages: Mapping[str, str],
) -> None:
    """Finish the lifecycle owner's already-persisted failure, never dispatch.

    State precedes the event, error message and claim release. If one of
    those writes failed, retain the exact recovery result and idempotently
    finish only its missing writes under the caller's execution fence.
    """
    from ..chat_store import CHAT_SESSION_MODE_MANAGED, utc_now

    if (
        session is None
        or turn is None
        or session.get("session_mode") != CHAT_SESSION_MODE_MANAGED
        or turn.get("status") != "failed"
    ):
        return
    code = str(turn.get("error_code") or "")
    message = recovery_messages.get(code)
    if message is None or (code != "server_restarted" and turn.get("error") != message):
        return  # Match this owner's recorded recovery, not arbitrary failures.
    if not any(
        event.get("kind") == "turn.failed"
        and as_mapping(event.get("payload")).get("error_code") == code
        and as_mapping(event.get("payload")).get("message") == message
        for event in host.events_after(session_id, turn_id, None)
    ):
        host.append_event(
            session_id,
            turn_id,
            kind="turn.failed",
            payload={"error_code": code, "message": message},
        )
    if not any(
        item.get("turn_id") == turn_id
        and item.get("role") == "error"
        and item.get("text") == message
        for item in host.messages(session_id)
    ):
        host.append_message(session_id, role="error", text=message, turn_id=turn_id)
    if session.get("active_turn_id") == turn_id:
        host.release_active_turn(
            session_id,
            turn_id,
            last_activity_at=str(turn.get("completed_at") or utc_now()),
            last_error_code=code,
        )


def _build_queued_turn_payload(
    *,
    schema_version: str,
    turn_id: str,
    session_id: str,
    client_id: str,
    message: str,
    normalized_attachments: list[dict[str, object]],
    origin_id: str,
    now: datetime,
    now_text: str,
    ttl_seconds: int | None,
    goal_instance_token: str | None,
    external_agent_target: Mapping[str, object] | None,
    collaboration_request: Mapping[str, object] | None,
) -> dict[str, object]:
    expires_at = (
        (now + timedelta(seconds=max(1, ttl_seconds))).isoformat().replace("+00:00", "Z")
        if ttl_seconds is not None
        else None
    )
    return {
        "schema_version": schema_version,
        "turn_id": turn_id,
        "session_id": session_id,
        **({"goal_instance_id": goal_instance_token} if goal_instance_token is not None else {}),
        "client_turn_id": client_id,
        "status": "queued",
        **({"external_agent_target": external_agent_target} if external_agent_target is not None else {}),
        **({"collaboration_request": collaboration_request} if collaboration_request is not None else {}),
        "message": str(message),
        **({"attachments": normalized_attachments} if normalized_attachments else {}),
        "origin": origin_id,
        "upstream_turn_id": None,
        "response": None,
        "error_code": None,
        "error": None,
        "created_at": now_text,
        "expires_at": expires_at,
        "started_at": None,
        "first_event_at": None,
        "completed_at": None,
        "last_activity_at": now_text,
        "delta_count": 0,
        "sse_reconnect_count": 0,
    }


def create_queued_turn(
    host: IngressStoreHostProtocol,
    session_id: str,
    *,
    client_turn_id: str,
    message: str,
    attachments: Sequence[Mapping[str, object]] | None = None,
    goal_instance_id: str | None = None,
    ttl_seconds: int | None = None,
    origin: str = "external",
    external_agent_target: Mapping[str, object] | None = None,
    collaboration_request: Mapping[str, object] | None = None,
    max_pending: int,
    lock_fn: LockFileFn,
) -> tuple[dict[str, object], bool]:
    """Persist one follow-up without replacing the active Turn.

    Accepted user input has no implicit wall-clock expiry. Callers may
    still supply an explicit TTL for time-sensitive work. Queue capacity,
    source revalidation and Stop remain independent bounds.
    """
    from ..chat import require_matching_replay
    from ..chat_store import _atomic_write_json, utc_now
    from ..control_plane.collaboration.source_chat_observation import (
        CHAT_TURN_SCHEMA_VERSION,
        _opaque_id,
    )

    client_id = _opaque_id(client_turn_id, field="client_turn_id")
    normalized_attachments = cast(list[dict[str, object]], normalize_chat_attachments(attachments))
    attachments_payload = normalized_attachments or None
    if attachments_payload:
        validate_chat_turn_envelope({"message": message, "attachments": attachments_payload})
    session_path = host._session_path(session_id)
    with host.runtime_activation_lock(), host._session_lock(session_id):
        with lock_fn(
            session_path,
            agent_id="loopx-chat",
            operation="create_chat_queued_turn",
        ):
            existing = host.turn_for_client(session_id, client_id)
            if existing is not None:
                require_matching_replay(
                    {**existing, "message": existing.get("_queue_original_message", existing.get("message"))},
                    identity="client_turn_id",
                    request={
                        "message": str(message),
                        "attachments": attachments_payload,
                        "origin": _opaque_id(origin, field="origin"),
                        "external_agent_target": external_agent_target,
                        **(
                            {"collaboration_request": collaboration_request}
                            if collaboration_request is not None
                            else {}
                        ),
                    },
                )
                # Managed acceptance retains its own message identity and
                # visible text. A queue-mode retry only replays that receipt;
                # the original typed acceptance owner finishes its writes.
                if "_acceptance" not in existing:
                    host._recover_queued_turn_input(session_id, existing)
                return existing, False
            session = host.load_session(session_id)
            if session is None or session.get("status") == "closed":
                raise KeyError("chat session was not found")
            now = datetime.now(timezone.utc)
            queued = host.queued_turns(session_id, include_reserved=True, capacity_at=now.isoformat())
            if len(queued) >= max_pending:
                raise RuntimeError("session_queue_full")
            turn_id = uuid.uuid4().hex
            now_text = now.isoformat().replace("+00:00", "Z")
            origin_id = _opaque_id(origin, field="origin")
            goal_instance_token = (
                _opaque_id(goal_instance_id, field="goal_instance_id")
                if goal_instance_id is not None
                else None
            )
            payload = _build_queued_turn_payload(
                schema_version=CHAT_TURN_SCHEMA_VERSION,
                turn_id=turn_id,
                session_id=session_id,
                client_id=client_id,
                message=message,
                normalized_attachments=normalized_attachments,
                origin_id=origin_id,
                now=now,
                now_text=now_text,
                ttl_seconds=ttl_seconds,
                goal_instance_token=goal_instance_token,
                external_agent_target=external_agent_target,
                collaboration_request=collaboration_request,
            )
            path = host._turn_path(session_id, turn_id)
            _atomic_write_json(path, payload)
            os.chmod(path, 0o600)
            session.update(
                {
                    "last_activity_at": now_text,
                    "updated_at": utc_now(),
                }
            )
            _atomic_write_json(session_path, session, preserve_mode=True)
            # Keep the cross-process Session fence through publication:
            # neither another retry nor a queue claim may overtake it.
            host.append_message(
                session_id,
                role="user",
                text=message,
                turn_id=turn_id,
                origin=origin,
                attachments=attachments_payload,
            )
            host.append_event(
                session_id,
                turn_id,
                kind="turn.queued",
                payload={"delivery_mode": "session_queue"},
            )
            return payload, True


def recover_queued_turn_input(
    host: IngressStoreHostProtocol,
    session_id: str,
    turn: Mapping[str, object],
) -> None:
    """Finish interrupted input IO under the caller's Session fence.

    The existing queued event follows the transcript append. It is the
    publication marker, so healthy claims need no full transcript scan.
    Recovery uses the saved request, never a retry's replacement text.
    """
    from ..chat import require_matching_message_replay
    from ..chat_store import _read_jsonl

    if "_acceptance" in turn:
        raise ValueError("managed Turn acceptance must finish before queue dispatch")
    turn_id = str(turn["turn_id"])
    event_path = host._event_path(session_id, turn_id)
    try:
        with event_path.open("rb") as stream:
            first = stream.readline()
    except FileNotFoundError:
        first = b""
    if first.strip():
        event: object = json.loads(first)
        if isinstance(event, dict) and event.get("kind") == "turn.queued":
            return
    host.flush_events(session_id, turn_id)
    events = _read_jsonl(event_path, raise_on_error=True, strict=True)
    if any(row["kind"] == "turn.queued" for row in events):
        return
    inputs = [
        row
        for row in _read_jsonl(host._session_dir(session_id) / "messages.jsonl", raise_on_error=True, strict=True)
        if row.get("turn_id") == turn_id and row.get("role") == "user"
    ]
    expected_attachments = turn.get("attachments")
    expected: dict[str, object] = {
        "message_id": "queue." + turn_id,
        "role": "user",
        "turn_id": turn_id,
        "text": str(turn["message"]),
        "origin": str(turn["origin"]),
        "attachments": expected_attachments,
    }
    if inputs:
        if len(inputs) != 1:
            raise ValueError("queued Turn has ambiguous input messages")
        require_matching_message_replay(inputs[0], expected)
    else:
        if expected_attachments is None:
            attachments: list[dict[str, object]] | None = None
        elif isinstance(expected_attachments, list) and all(
            isinstance(item, dict) for item in expected_attachments
        ):
            attachments = cast(list[dict[str, object]], expected_attachments)
        else:
            raise ValueError("queued Turn attachments must be null or a list of objects")
        host.append_message(
            session_id,
            message_id="queue." + turn_id,
            role="user",
            turn_id=turn_id,
            text=str(turn["message"]),
            origin=str(turn["origin"]),
            attachments=attachments,
        )
    host.append_event(session_id, turn_id, kind="turn.queued", payload={"delivery_mode": "session_queue"})
