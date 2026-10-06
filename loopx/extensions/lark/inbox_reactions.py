from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock
from .event_inbox import (
    MESSAGE_ID_PATTERN,
    REACTION_EMOJI_PATTERN,
    _event_from_file,
    _load_processed,
    load_lark_event_inbox_config,
)
from .private_json import write_private_json_atomic

REACTION_RECEIPTS_SCHEMA_VERSION = "lark_event_inbox_reaction_receipts_v0"
TURN_START_READS_SCHEMA_VERSION = "lark_event_inbox_turn_start_reads_v0"
RECEIVED_OPERATION_SCHEMA_VERSION = "lark_event_inbox_received_operation_v0"
PROCESSED_STATE_FILENAME = "processed.json"
REACTION_PHASES = {"received", "processing"}
CREATION_OPERATION_PHASES = {"prepared", "created"}
REACTION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,200}")
CommandRunner = Callable[[Sequence[str]], Mapping[str, Any]]
ReactionCreator = Callable[[str, str], str | None]
ReactionDeleter = Callable[[str, str], bool]


def _default_runner(args: Sequence[str]) -> Mapping[str, Any]:
    result = subprocess.run(
        list(args),
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        check=False,
        timeout=10,
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _call(runner: CommandRunner, args: Sequence[str]) -> Mapping[str, Any]:
    try:
        return runner(args)
    except (OSError, subprocess.SubprocessError):
        return {"returncode": 1}


def _json_object(value: Any) -> Mapping[str, Any]:
    try:
        payload = json.loads(str(value or ""))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _find_string_by_key(value: object, key: str) -> str | None:
    if isinstance(value, Mapping):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return next(
            (
                found
                for child in value.values()
                if (found := _find_string_by_key(child, key))
            ),
            None,
        )
    if isinstance(value, list):
        return next(
            (found for child in value if (found := _find_string_by_key(child, key))),
            None,
        )
    return None


def _contains_string_by_key(value: object, key: str, expected: str) -> bool:
    if isinstance(value, Mapping):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip() == expected:
            return True
        return any(
            _contains_string_by_key(child, key, expected) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_string_by_key(child, key, expected) for child in value)
    return False


def _has_more_pages(value: object) -> bool:
    if isinstance(value, Mapping):
        return value.get("has_more") is True or any(_has_more_pages(child) for child in value.values())
    if isinstance(value, list):
        return any(_has_more_pages(child) for child in value)
    return False


def _receipt_path(inbox: Path) -> Path:
    return inbox / "reactions" / "receipts.json"


def _operation_lock_target(inbox: Path, message_id: str) -> Path:
    bucket = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:2]
    return inbox / "reactions" / f"operation-{bucket}"


def _turn_start_reads_path(inbox: Path) -> Path:
    return inbox / "reactions" / "turn-start-reads.json"


def _reaction_creation_operation_path(inbox: Path, message_id: str, reaction_phase: str = "received") -> Path:
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    if reaction_phase not in REACTION_PHASES:
        raise ValueError("reaction phase is invalid")
    return inbox / "reactions" / f"{reaction_phase}-operations" / f"{digest}.json"


def _load_turn_start_reads(inbox: Path) -> set[str]:
    path = _turn_start_reads_path(inbox)
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("lark inbox turn-start reads are unreadable") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != TURN_START_READS_SCHEMA_VERSION
        or not isinstance(payload.get("message_ids"), list)
    ):
        raise ValueError("lark inbox turn-start reads schema is invalid")
    message_ids = payload["message_ids"]
    if any(
        not isinstance(message_id, str) or not MESSAGE_ID_PATTERN.fullmatch(message_id)
        for message_id in message_ids
    ):
        raise ValueError("lark inbox turn-start read entry is invalid")
    return set(message_ids)


def record_lark_inbox_turn_start_read(*, inbox: Path, message_id: str) -> bool:
    """Record the first Agent-owned turn-start read independently of reactions."""

    normalized = str(message_id or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("turn-start read requires a valid Lark message id")
    path = _turn_start_reads_path(inbox)
    with exclusive_file_lock(path, operation="lark_inbox_turn_start_reads"):
        message_ids = _load_turn_start_reads(inbox)
        if normalized in message_ids:
            return False
        if not _captured_pending_message(inbox=inbox, message_id=normalized):
            return False
        message_ids.add(normalized)
        write_private_json_atomic(
            path,
            {
                "schema_version": TURN_START_READS_SCHEMA_VERSION,
                "message_ids": sorted(message_ids),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        return True


def lark_inbox_pending_turn_start_read_message_ids(*, inbox: Path) -> list[str]:
    """Return locally durable turn-start reads that remain pending settlement."""

    path = _turn_start_reads_path(inbox)
    with exclusive_file_lock(path, operation="lark_inbox_turn_start_reads"):
        message_ids = _load_turn_start_reads(inbox)
        processed = _load_processed(inbox / PROCESSED_STATE_FILENAME)
        captured = {
            str(event["message_id"])
            for event_path in (inbox.glob("*.json") if inbox.is_dir() else [])
            if event_path.name != PROCESSED_STATE_FILENAME
            if (event := _event_from_file(event_path)) is not None
            if isinstance(event.get("message_id"), str)
        }
        pending = message_ids.intersection(captured).difference(processed)
        if pending != message_ids:
            write_private_json_atomic(
                path,
                {
                    "schema_version": TURN_START_READS_SCHEMA_VERSION,
                    "message_ids": sorted(pending),
                    "updated_at": datetime.now(UTC).isoformat(),
                },
            )
        receipts = _load_receipts(_receipt_path(inbox))
        return sorted(
            message_id
            for message_id in pending
            if not REACTION_PHASES.intersection(receipts.get(message_id, {}))
        )


def _creation_operation_schema(reaction_phase: str) -> str:
    # Preserve the existing received journal and reuse its lifecycle for processing.
    return (RECEIVED_OPERATION_SCHEMA_VERSION if reaction_phase == "received"
            else "lark_event_inbox_processing_operation_v0")


def _load_reaction_creation_operation(*, inbox: Path, message_id: str, reaction_phase: str = "received") -> dict[str, str] | None:
    path = _reaction_creation_operation_path(inbox, message_id, reaction_phase)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("lark inbox received operation is unreadable") from exc
    phase = str(payload.get("phase") or "") if isinstance(payload, Mapping) else ""
    emoji_type = (
        str(payload.get("emoji_type") or "") if isinstance(payload, Mapping) else ""
    )
    reaction_id = (
        str(payload.get("reaction_id") or "") if isinstance(payload, Mapping) else ""
    )
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != _creation_operation_schema(reaction_phase)
        or payload.get("message_id") != message_id
        or phase not in CREATION_OPERATION_PHASES
        or not REACTION_EMOJI_PATTERN.fullmatch(emoji_type)
        or (phase == "created" and not REACTION_ID_PATTERN.fullmatch(reaction_id))
        or (phase == "prepared" and reaction_id)
    ):
        raise ValueError("lark inbox received operation schema is invalid")
    result = {"phase": phase, "emoji_type": emoji_type}
    if reaction_id:
        result["reaction_id"] = reaction_id
    return result


def _write_reaction_creation_operation(
    *,
    inbox: Path,
    message_id: str,
    phase: str,
    emoji_type: str,
    reaction_id: str = "",
    reaction_phase: str = "received",
) -> None:
    payload = {
        "schema_version": _creation_operation_schema(reaction_phase),
        "message_id": message_id,
        "phase": phase,
        "emoji_type": emoji_type,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if reaction_id:
        payload["reaction_id"] = reaction_id
    write_private_json_atomic(_reaction_creation_operation_path(inbox, message_id, reaction_phase), payload)


def _clear_reaction_creation_operation(*, inbox: Path, message_id: str, reaction_phase: str = "received") -> None:
    path = _reaction_creation_operation_path(inbox, message_id, reaction_phase)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def lark_inbox_reaction_lock(*, inbox: Path, message_id: str) -> Iterator[None]:
    normalized = str(message_id or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("reaction lock requires a valid Lark message id")
    with exclusive_file_lock(
        _operation_lock_target(inbox, normalized),
        operation="lark_inbox_reaction_transition",
    ):
        yield


def _valid_receipt(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    reaction_id = str(value.get("reaction_id") or "").strip()
    emoji_type = str(value.get("emoji_type") or "").strip()
    if not REACTION_ID_PATTERN.fullmatch(
        reaction_id
    ) or not REACTION_EMOJI_PATTERN.fullmatch(emoji_type):
        return None
    return {
        "reaction_id": reaction_id,
        "emoji_type": emoji_type,
    }


def _load_receipts(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("lark inbox reaction receipts are unreadable") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != REACTION_RECEIPTS_SCHEMA_VERSION
    ):
        raise ValueError("lark inbox reaction receipts schema is invalid")
    raw_receipts = payload.get("receipts")
    if not isinstance(raw_receipts, Mapping):
        raise TypeError("lark inbox reaction receipts payload is invalid")
    receipts: dict[str, dict[str, dict[str, str]]] = {}
    for raw_message_id, raw_phases in raw_receipts.items():
        message_id = str(raw_message_id)
        if not MESSAGE_ID_PATTERN.fullmatch(message_id) or not isinstance(
            raw_phases, Mapping
        ):
            raise ValueError("lark inbox reaction receipt entry is invalid")
        phases = {
            phase: receipt
            for phase in REACTION_PHASES
            if (receipt := _valid_receipt(raw_phases.get(phase))) is not None
        }
        if set(raw_phases) != set(phases):
            raise ValueError("lark inbox reaction receipt phase is invalid")
        if phases:
            receipts[message_id] = phases
    return receipts


def lark_inbox_reaction_receipts(
    *, inbox: Path, message_id: str
) -> dict[str, dict[str, str]]:
    normalized = str(message_id or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("reaction receipts require a valid Lark message id")
    return dict(_load_receipts(_receipt_path(inbox)).get(normalized, {}))


def _update_receipts(
    *,
    inbox: Path,
    update: Callable[[dict[str, dict[str, dict[str, str]]]], None],
) -> None:
    path = _receipt_path(inbox)
    with exclusive_file_lock(
        path,
        operation="lark_inbox_reaction_receipts",
    ):
        receipts = _load_receipts(path)
        update(receipts)
        write_private_json_atomic(
            path,
            {
                "schema_version": REACTION_RECEIPTS_SCHEMA_VERSION,
                "receipts": receipts,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )


def record_lark_inbox_reaction(
    *,
    inbox: Path,
    message_id: str,
    phase: str,
    reaction_id: str,
    emoji_type: str,
) -> None:
    normalized_message_id = str(message_id or "").strip()
    normalized_phase = str(phase or "").strip()
    normalized_reaction_id = str(reaction_id or "").strip()
    normalized_emoji_type = str(emoji_type or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(normalized_message_id):
        raise ValueError("reaction receipt requires a valid Lark message id")
    if normalized_phase not in REACTION_PHASES:
        raise ValueError("reaction receipt phase is invalid")
    if not REACTION_ID_PATTERN.fullmatch(normalized_reaction_id):
        raise ValueError("reaction receipt requires a valid reaction id")
    if not REACTION_EMOJI_PATTERN.fullmatch(normalized_emoji_type):
        raise ValueError("reaction receipt requires a valid emoji type")

    def update(receipts: dict[str, dict[str, dict[str, str]]]) -> None:
        phases = receipts.setdefault(normalized_message_id, {})
        phases[normalized_phase] = {
            "reaction_id": normalized_reaction_id,
            "emoji_type": normalized_emoji_type,
        }

    _update_receipts(inbox=inbox, update=update)


def clear_lark_inbox_reaction(
    *,
    inbox: Path,
    message_id: str,
    phase: str,
    reaction_id: str,
) -> None:
    normalized_message_id = str(message_id or "").strip()
    normalized_phase = str(phase or "").strip()
    normalized_reaction_id = str(reaction_id or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(normalized_message_id):
        raise ValueError("reaction receipt requires a valid Lark message id")
    if normalized_phase not in REACTION_PHASES:
        raise ValueError("reaction receipt phase is invalid")
    if not REACTION_ID_PATTERN.fullmatch(normalized_reaction_id):
        raise ValueError("reaction receipt requires a valid reaction id")

    def update(receipts: dict[str, dict[str, dict[str, str]]]) -> None:
        phases = receipts.get(normalized_message_id)
        if not phases:
            return
        receipt = phases.get(normalized_phase)
        if receipt and receipt.get("reaction_id") == normalized_reaction_id:
            phases.pop(normalized_phase, None)
        if not phases:
            receipts.pop(normalized_message_id, None)

    _update_receipts(inbox=inbox, update=update)


def _create_reaction(
    *,
    runner: CommandRunner,
    profile: str,
    message_id: str,
    emoji_type: str,
) -> str | None:
    result = _call(
        runner,
        [
            "lark-cli",
            "--profile",
            profile,
            "im",
            "reactions",
            "create",
            "--message-id",
            message_id,
            "--data",
            json.dumps(
                {"reaction_type": {"emoji_type": emoji_type}},
                separators=(",", ":"),
            ),
            "--as",
            "bot",
            "--format",
            "json",
        ],
    )
    if result.get("returncode") != 0:
        return None
    payload = _json_object(result.get("stdout"))
    return (
        _find_string_by_key(payload, "reaction_id")
        if payload.get("ok") is True
        else None
    )


def _delete_reaction(
    *,
    runner: CommandRunner,
    profile: str,
    message_id: str,
    reaction_id: str,
) -> bool:
    result = _call(
        runner,
        [
            "lark-cli",
            "--profile",
            profile,
            "im",
            "reactions",
            "delete",
            "--message-id",
            message_id,
            "--reaction-id",
            reaction_id,
            "--as",
            "bot",
            "--format",
            "json",
        ],
    )
    if (
        result.get("returncode") == 0
        and _json_object(result.get("stdout")).get("ok") is True
    ):
        return True
    readback = _call(
        runner,
        [
            "lark-cli",
            "--profile",
            profile,
            "im",
            "reactions",
            "list",
            "--message-id",
            message_id,
            "--page-all",
            "--as",
            "bot",
            "--format",
            "json",
        ],
    )
    if readback.get("returncode") != 0:
        return False
    payload = _json_object(readback.get("stdout"))
    return bool(
        payload.get("ok") is True
        and not _has_more_pages(payload)
        and not _contains_string_by_key(payload, "reaction_id", reaction_id)
    )


def _captured_pending_message(*, inbox: Path, message_id: str) -> bool:
    if message_id in _load_processed(inbox / PROCESSED_STATE_FILENAME):
        return False
    return any(
        event.get("message_id") == message_id
        for path in (inbox.glob("*.json") if inbox.is_dir() else [])
        if path.name != PROCESSED_STATE_FILENAME
        if (event := _event_from_file(path)) is not None
    )


def _received_reaction_result(
    *,
    status: str,
    ok: bool,
    configured: bool,
    captured_pending: bool,
    created_count: int = 0,
    external_writes_performed: bool | None = None,
    blocker: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": ok,
        "schema_version": "lark_event_inbox_received_reaction_v0",
        "status": status,
        "configured": configured,
        "captured_pending": captured_pending,
        "read_ack_attempted": status in {"received", "failed", "receipt_failed"},
        "created_count": created_count,
        "external_writes_performed": (
            created_count > 0
            if external_writes_performed is None
            else external_writes_performed
        ),
        "private_sender_profile_captured": False,
        "private_message_id_captured": False,
        "private_reaction_id_captured": False,
        "raw_provider_payload_captured": False,
    }
    if blocker:
        result["blocker"] = blocker
    return result


def ensure_lark_event_inbox_received_reaction_locked(
    *,
    config: Mapping[str, Any],
    event: Mapping[str, Any],
    create_reaction: ReactionCreator,
    delete_reaction: ReactionDeleter,
) -> dict[str, Any]:
    """Idempotently acknowledge one message read by the turn-start hook.

    The caller owns the per-message reaction lock.  The message may have been
    captured by any ingress, but only Agent turn-start consumption invokes this
    boundary.  Attention classification remains independent and decides reply
    priority rather than whether the read acknowledgement is written.
    """

    message_id = str(event.get("message_id") or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(message_id):
        raise ValueError("received reaction requires a valid Lark message id")
    inbox = config["inbox_path"]
    # Settlement and provider reaction creation must be one lifecycle
    # transition. Otherwise an Agent can ACK the message after the pending
    # check but before the reaction receipt is persisted, leaving a reaction on
    # an already-settled message.
    with exclusive_file_lock(
        inbox / ".state" / "settlement",
        operation="lark_inbox_received_reaction_settlement",
    ):
        reply = config["reply"]
        emoji_type = str(reply.get("received_reaction_emoji") or "")
        if not emoji_type:
            return _received_reaction_result(
                status="not_configured",
                ok=True,
                configured=False,
                captured_pending=False,
            )
        if not _captured_pending_message(inbox=inbox, message_id=message_id):
            return _received_reaction_result(
                status="already_settled",
                ok=True,
                configured=True,
                captured_pending=False,
            )
        receipts = lark_inbox_reaction_receipts(inbox=inbox, message_id=message_id)
        if receipts.get("received") is None and receipts.get("processing") is not None:
            _clear_reaction_creation_operation(inbox=inbox, message_id=message_id)
            return _received_reaction_result(
                status="already_processing",
                ok=True,
                configured=True,
                captured_pending=True,
            )
        return _ensure_reaction_creation_locked(
            inbox=inbox, message_id=message_id, emoji_type=emoji_type, reaction_phase="received",
            create_reaction=create_reaction, delete_reaction=delete_reaction,
        )


def _ensure_reaction_creation_locked(
    *, inbox: Path, message_id: str, emoji_type: str, reaction_phase: str,
    create_reaction: ReactionCreator, delete_reaction: ReactionDeleter,
) -> dict[str, Any]:
    """One provider creation lifecycle for received and processing feedback.

    Caller owns the per-source transition/settlement locks. Unknown writes stay
    prepared; known provider ids recover their receipt without another create.
    """
    receipts = lark_inbox_reaction_receipts(
        inbox=inbox,
        message_id=message_id,
    )
    if receipts.get(reaction_phase) is not None:
        _clear_reaction_creation_operation(inbox=inbox, message_id=message_id, reaction_phase=reaction_phase)
        return _received_reaction_result(
            status=f"already_{reaction_phase}",
            ok=True,
            configured=True,
            captured_pending=True,
        )
    operation = _load_reaction_creation_operation(inbox=inbox, message_id=message_id, reaction_phase=reaction_phase)
    if operation and operation["emoji_type"] != emoji_type:
        return _received_reaction_result(
            status="operation_config_changed",
            ok=False,
            configured=True,
            captured_pending=True,
            blocker=f"lark_inbox_{reaction_phase}_reaction_operation_config_changed",
        )
    if operation and operation["phase"] == "prepared":
        return _received_reaction_result(
            status="provider_outcome_uncertain",
            ok=False,
            configured=True,
            captured_pending=True,
            blocker=f"lark_inbox_{reaction_phase}_reaction_provider_outcome_uncertain",
        )
    if operation and operation["phase"] == "created":
        reaction_id = operation["reaction_id"]
        try:
            record_lark_inbox_reaction(
                inbox=inbox,
                message_id=message_id,
                phase=reaction_phase,
                reaction_id=reaction_id,
                emoji_type=emoji_type,
            )
        except (OSError, TypeError, ValueError):
            return _received_reaction_result(
                status="receipt_failed",
                ok=False,
                configured=True,
                captured_pending=True,
                external_writes_performed=False,
                blocker=f"lark_inbox_{reaction_phase}_reaction_receipt_failed",
            )
        _clear_reaction_creation_operation(inbox=inbox, message_id=message_id, reaction_phase=reaction_phase)
        return _received_reaction_result(
            status="receipt_recovered",
            ok=True,
            configured=True,
            captured_pending=True,
        )
    _write_reaction_creation_operation(
        inbox=inbox,
        message_id=message_id,
        phase="prepared",
        reaction_phase=reaction_phase,
        emoji_type=emoji_type,
    )
    created_reaction_id = create_reaction(message_id, emoji_type)
    if created_reaction_id is None:
        return _received_reaction_result(
            status="failed",
            ok=False,
            configured=True,
            captured_pending=True,
            blocker=f"lark_inbox_{reaction_phase}_reaction_create_failed",
        )
    try:
        _write_reaction_creation_operation(
            inbox=inbox,
            message_id=message_id,
            phase="created",
            reaction_phase=reaction_phase,
            emoji_type=emoji_type,
            reaction_id=created_reaction_id,
        )
    except (OSError, TypeError, ValueError):
        cleaned_up = delete_reaction(message_id, created_reaction_id)
        if cleaned_up:
            _clear_reaction_creation_operation(inbox=inbox, message_id=message_id, reaction_phase=reaction_phase)
        return _received_reaction_result(
            status="operation_receipt_failed",
            ok=False,
            configured=True,
            captured_pending=True,
            created_count=int(not cleaned_up),
            external_writes_performed=True,
            blocker=(
                f"lark_inbox_{reaction_phase}_reaction_operation_receipt_failed"
                if cleaned_up
                else f"lark_inbox_{reaction_phase}_reaction_provider_outcome_uncertain"
            ),
        )
    try:
        record_lark_inbox_reaction(
            inbox=inbox,
            message_id=message_id,
            phase=reaction_phase,
            reaction_id=created_reaction_id,
            emoji_type=emoji_type,
        )
    except (OSError, TypeError, ValueError):
        return _received_reaction_result(
            status="receipt_failed",
            ok=False,
            configured=True,
            captured_pending=True,
            created_count=1,
            external_writes_performed=True,
            blocker=f"lark_inbox_{reaction_phase}_reaction_receipt_failed",
        )
    _clear_reaction_creation_operation(inbox=inbox, message_id=message_id, reaction_phase=reaction_phase)
    return _received_reaction_result(
        status=reaction_phase,
        ok=True,
        configured=True,
        captured_pending=True,
        created_count=1,
    )


def ensure_lark_event_inbox_received_reaction(
    *,
    project: str | Path,
    config_path: str | Path,
    event: Mapping[str, Any],
    create_reaction: ReactionCreator,
    delete_reaction: ReactionDeleter,
) -> dict[str, Any]:
    """Lock and ACK one turn-start-read pending message."""

    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"]:
        raise ValueError("lark event inbox is not enabled")
    message_id = str(event.get("message_id") or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(message_id):
        raise ValueError("received reaction requires a valid Lark message id")
    inbox = config["inbox_path"]
    with lark_inbox_reaction_lock(inbox=inbox, message_id=message_id):
        return ensure_lark_event_inbox_received_reaction_locked(
            config=config,
            event=event,
            create_reaction=create_reaction,
            delete_reaction=delete_reaction,
        )


def mark_lark_event_inbox_received(
    *, project: str | Path, config_path: str | Path, event: Mapping[str, Any],
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    profile = str(config["reply"]["sender_profile"])
    return ensure_lark_event_inbox_received_reaction(
        project=project, config_path=config_path, event=event,
        create_reaction=lambda message_id, emoji_type: _create_reaction(
            runner=runner, profile=profile, message_id=message_id, emoji_type=emoji_type),
        delete_reaction=lambda message_id, reaction_id: _delete_reaction(
            runner=runner, profile=profile, message_id=message_id, reaction_id=reaction_id),
    )


def _operation_result(
    *,
    operation: str,
    status: str,
    ok: bool,
    execute: bool,
    configured: bool,
    created_count: int = 0,
    deleted_count: int = 0,
    pending_delete_count: int = 0,
    blocker: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": ok,
        "schema_version": "lark_event_inbox_reaction_operation_v0",
        "operation": operation,
        "status": status,
        "execute": execute,
        "configured": configured,
        "external_write_authority_asserted": execute,
        "external_writes_performed": created_count + deleted_count > 0,
        "created_count": created_count,
        "deleted_count": deleted_count,
        "pending_delete_count": pending_delete_count,
        "private_sender_profile_captured": False,
        "private_message_id_captured": False,
        "private_reaction_id_captured": False,
        "raw_provider_payload_captured": False,
    }
    if blocker:
        result["blocker"] = blocker
    return result


def mark_lark_event_inbox_processing(
    *,
    project: str | Path,
    config_path: str | Path,
    message_id: str,
    execute: bool = False,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"]:
        raise ValueError("lark event inbox is not enabled")
    normalized = str(message_id or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("processing requires a valid Lark message id")
    inbox = config["inbox_path"]
    with lark_inbox_reaction_lock(inbox=inbox, message_id=normalized):
        with exclusive_file_lock(inbox / ".state" / "settlement", operation="lark_inbox_processing_settlement"):
            if not _captured_pending_message(inbox=inbox, message_id=normalized):
                raise ValueError("processing requires a captured pending inbox message")
            return _mark_lark_event_inbox_processing_locked(
                config=config, message_id=normalized, execute=execute, runner=runner,
            )


def _mark_lark_event_inbox_processing_locked(
    *,
    config: Mapping[str, Any],
    message_id: str,
    execute: bool,
    runner: CommandRunner,
) -> dict[str, Any]:
    inbox = config["inbox_path"]
    normalized = message_id
    reply = config["reply"]
    processing_emoji = str(reply.get("processing_reaction_emoji") or "")
    if not processing_emoji:
        return _operation_result(
            operation="processing",
            status="not_configured",
            ok=True,
            execute=execute,
            configured=False,
        )
    receipts = lark_inbox_reaction_receipts(
        inbox=inbox,
        message_id=normalized,
    )
    received = receipts.get("received")
    processing = receipts.get("processing")
    if not execute:
        return _operation_result(
            operation="processing",
            status="preview_ready",
            ok=True,
            execute=False,
            configured=True,
            pending_delete_count=int(received is not None),
        )

    profile = str(reply["sender_profile"])
    created_count = 0
    if processing is None:
        creation = _ensure_reaction_creation_locked(
            inbox=inbox, message_id=normalized, emoji_type=processing_emoji, reaction_phase="processing",
            create_reaction=lambda message_id, emoji_type: _create_reaction(
                runner=runner, profile=profile, message_id=message_id, emoji_type=emoji_type),
            delete_reaction=lambda message_id, reaction_id: _delete_reaction(
                runner=runner, profile=profile, message_id=message_id, reaction_id=reaction_id),
        )
        if not creation["ok"]:
            return _operation_result(operation="processing", status=creation["status"],
                ok=False, execute=True, configured=True, created_count=creation["created_count"],
                blocker=creation.get("blocker"))
        created_count = creation["created_count"]

    deleted_count = 0
    if received is not None and config["reply"].get("received_reaction_policy") != "retain":
        if not _delete_reaction(
            runner=runner,
            profile=profile,
            message_id=normalized,
            reaction_id=received["reaction_id"],
        ):
            return _operation_result(
                operation="processing",
                status="cleanup_pending",
                ok=False,
                execute=True,
                configured=True,
                created_count=created_count,
                pending_delete_count=1,
                blocker="lark_inbox_received_reaction_delete_failed",
            )
        clear_lark_inbox_reaction(
            inbox=inbox,
            message_id=normalized,
            phase="received",
            reaction_id=received["reaction_id"],
        )
        deleted_count = 1
    return _operation_result(
        operation="processing",
        status="processing",
        ok=True,
        execute=True,
        configured=True,
        created_count=created_count,
        deleted_count=deleted_count,
    )


def complete_lark_event_inbox_reactions(
    *,
    project: str | Path,
    config_path: str | Path,
    message_id: str,
    execute: bool = False,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    config = load_lark_event_inbox_config(project=project, config_path=config_path)
    if not config["enabled"]:
        raise ValueError("lark event inbox is not enabled")
    normalized = str(message_id or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("reaction completion requires a valid Lark message id")
    inbox = config["inbox_path"]
    with lark_inbox_reaction_lock(inbox=inbox, message_id=normalized):
        return _complete_lark_event_inbox_reactions_locked(
            config=config,
            message_id=normalized,
            execute=execute,
            runner=runner,
        )


def _complete_lark_event_inbox_reactions_locked(
    *,
    config: Mapping[str, Any],
    message_id: str,
    execute: bool,
    runner: CommandRunner,
) -> dict[str, Any]:
    inbox = config["inbox_path"]
    normalized = message_id
    operation = _load_reaction_creation_operation(inbox=inbox, message_id=normalized, reaction_phase="processing")
    if operation and execute:
        profile = str(config["reply"]["sender_profile"])
        creation = _ensure_reaction_creation_locked(
            inbox=inbox, message_id=normalized, emoji_type=operation["emoji_type"], reaction_phase="processing",
            create_reaction=lambda *_: None,
            delete_reaction=lambda message_id, reaction_id: _delete_reaction(
                runner=runner, profile=profile, message_id=message_id, reaction_id=reaction_id),
        )
        if not creation["ok"]:
            return _operation_result(operation="complete", status=creation["status"],
                ok=False, execute=True, configured=True, created_count=creation["created_count"],
                blocker=creation.get("blocker"))
    receipts = lark_inbox_reaction_receipts(
        inbox=inbox,
        message_id=normalized,
    )
    # A received ACK records consumption, not unfinished processing. Retention
    # keeps that provider receipt visible after the answer and across replay.
    if config["reply"].get("received_reaction_policy") == "retain":
        receipts = {phase: row for phase, row in receipts.items() if phase != "received"}
    configured = bool(
        config["reply"].get("received_reaction_emoji")
        or config["reply"].get("processing_reaction_emoji")
    )
    if not receipts:
        return _operation_result(
            operation="complete",
            status="already_complete",
            ok=True,
            execute=execute,
            configured=configured,
        )
    if not execute:
        return _operation_result(
            operation="complete",
            status="preview_ready",
            ok=True,
            execute=False,
            configured=configured,
            pending_delete_count=len(receipts),
        )

    profile = str(config["reply"]["sender_profile"])
    deleted_count = 0
    for phase in ("processing", "received"):
        receipt = receipts.get(phase)
        if receipt is None:
            continue
        if not _delete_reaction(
            runner=runner,
            profile=profile,
            message_id=normalized,
            reaction_id=receipt["reaction_id"],
        ):
            remaining = len(receipts) - deleted_count
            return _operation_result(
                operation="complete",
                status="cleanup_pending",
                ok=False,
                execute=True,
                configured=configured,
                deleted_count=deleted_count,
                pending_delete_count=remaining,
                blocker="lark_inbox_reaction_delete_failed",
            )
        clear_lark_inbox_reaction(
            inbox=inbox,
            message_id=normalized,
            phase=phase,
            reaction_id=receipt["reaction_id"],
        )
        deleted_count += 1
    return _operation_result(
        operation="complete",
        status="completed",
        ok=True,
        execute=True,
        configured=configured,
        deleted_count=deleted_count,
    )
