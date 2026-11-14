"""Project authenticated LoopX card decisions into one source-bound capture.

The Chat action store remains the durable receipt owner.  This adapter emits a
minimal public-safe source for benchmark-toolkit; it never copies operation
payloads, card contents, principals, transcripts, or host-native traces.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ...chat_action_store import (
    AUTHENTICATED_LARK_CARD_CALLBACK,
    HUMAN_ACTOR_CLASS,
    ChatActionStore,
)
from ...control_plane.runtime.document_io import atomic_write_state_text


_CHANNELS = (
    "loopx_operation_confirmation",
    "loopx_team_plan_review",
)
_OMITTED = (
    "active_attention_time",
    "dashboard_and_private_chat_actions_without_authenticated_callbacks",
    "pending_and_expired_requests",
    "runtime_native_approvals",
)
_LEGACY_LARK_CALLBACK = "legacy_lark_authenticated_card_callback_v0"
_LEGACY_OPERATION_CONFIRMATION_FIELDS = frozenset(
    {
        "provider",
        "event_id",
        "principal",
        "message_id",
        "chat_id",
        "app_id",
        "surface_kind",
        "interaction_kind",
        "confirmation_digest",
        "card_digest",
        "confirmed_at",
        "decision",
    }
)
_LEGACY_REVIEW_CONFIRMATION_FIELDS = frozenset(
    {
        "provider",
        "event_id",
        "principal",
        "message_id",
        "chat_id",
        "app_id",
        "audience_id",
        "state_fingerprint",
        "card_digest",
        "confirmed_at",
        "decision",
    }
)


def _instant(value: object, *, field: str) -> datetime:
    token = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _capture_instant(value: object, *, field: str) -> tuple[datetime, str]:
    token = str(value or "").strip()
    if not token.endswith("Z"):
        raise ValueError(f"{field} must be a UTC timestamp ending in Z")
    parsed = _instant(token, field=field)
    return parsed, _canonical(parsed)


def _canonical(value: datetime) -> str:
    timespec = "seconds" if value.microsecond == 0 else "milliseconds"
    return value.isoformat(timespec=timespec).replace("+00:00", "Z")


def _object(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _matches_delivery(
    confirmation: Mapping[str, Any],
    delivery: Mapping[str, Any],
    fields: tuple[str, ...],
) -> bool:
    return all(confirmation.get(field) == delivery.get(field) for field in fields)


def _verified_confirmation(
    value: object, *, legacy_fields: frozenset[str]
) -> tuple[Mapping[str, Any], str] | None:
    confirmation = _object(value)
    if (
        confirmation is None
        or confirmation.get("provider") != "lark"
        or confirmation.get("decision") not in {"confirm", "reject"}
    ):
        return None
    if (
        confirmation.get("actor_class") == HUMAN_ACTOR_CLASS
        and confirmation.get("verification_kind") == AUTHENTICATED_LARK_CARD_CALLBACK
    ):
        return confirmation, AUTHENTICATED_LARK_CARD_CALLBACK
    if (
        set(confirmation) == legacy_fields
        and "actor_class" not in confirmation
        and "verification_kind" not in confirmation
    ):
        return confirmation, _LEGACY_LARK_CALLBACK
    return None


def _operation_decision(proposal: Mapping[str, Any]) -> dict[str, Any] | None:
    operation = _object(proposal.get("operation"))
    parameters = _object(proposal.get("normalized_parameters"))
    if operation is None or parameters is None:
        return None
    verified = _verified_confirmation(
        operation.get("confirmation"),
        legacy_fields=_LEGACY_OPERATION_CONFIRMATION_FIELDS,
    )
    delivery = _object(operation.get("delivery"))
    if verified is None or delivery is None:
        return None
    confirmation, verification_kind = verified
    if (
        confirmation.get("surface_kind") != "group_message_card"
        or confirmation.get("interaction_kind") != "button_callback"
    ):
        return None
    if not _matches_delivery(
        confirmation,
        delivery,
        ("provider", "message_id", "chat_id", "app_id", "card_digest"),
    ):
        return None
    if confirmation.get("confirmation_digest") != operation.get(
        "confirmation_digest"
    ) or confirmation.get("principal") not in set(
        parameters.get("authorized_principals") or []
    ):
        return None
    return _decision(
        proposal,
        confirmation,
        delivery,
        source_channel=_CHANNELS[0],
        kind="approval",
        verification_kind=verification_kind,
    )


def _review_decision(proposal: Mapping[str, Any]) -> dict[str, Any] | None:
    review = _object(proposal.get("review_card"))
    if review is None:
        return None
    verified = _verified_confirmation(
        review.get("confirmation"), legacy_fields=_LEGACY_REVIEW_CONFIRMATION_FIELDS
    )
    deliveries = _object(review.get("deliveries"))
    if verified is None or deliveries is None:
        return None
    confirmation, verification_kind = verified
    delivery = _object(deliveries.get(confirmation.get("audience_id")))
    if delivery is None or not _matches_delivery(
        confirmation,
        delivery,
        ("provider", "message_id", "chat_id", "app_id", "card_digest"),
    ):
        return None
    if confirmation.get("principal") != review.get(
        "authorized_principal"
    ) or confirmation.get("state_fingerprint") != review.get("state_fingerprint"):
        return None
    return _decision(
        proposal,
        confirmation,
        delivery,
        source_channel=_CHANNELS[1],
        kind="review",
        verification_kind=verification_kind,
    )


def _decision(
    proposal: Mapping[str, Any],
    confirmation: Mapping[str, Any],
    delivery: Mapping[str, Any],
    *,
    source_channel: str,
    kind: str,
    verification_kind: str,
) -> dict[str, Any]:
    identity = json.dumps(
        [proposal.get("proposal_id"), confirmation.get("event_id")],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt_id = "loopx-card-" + hashlib.sha256(identity).hexdigest()[:32]
    decision = str(confirmation["decision"])
    return {
        "schema_version": "loopx_human_attention_receipt_v1",
        "receipt_id": receipt_id,
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "goal_id": str(
            (_object(proposal.get("normalized_parameters")) or {}).get("goal_id")
            or (_object(proposal.get("context")) or {}).get("goal_id")
            or ""
        ),
        "action_kind": str(proposal.get("action_kind") or ""),
        "source_channel": source_channel,
        "actor_class": HUMAN_ACTOR_CLASS,
        "verification_kind": verification_kind,
        "decision_outcome": decision,
        "authority_effect": "granted" if decision == "confirm" else "denied",
        "delivered_at": _canonical(
            _instant(delivery.get("delivered_at"), field="delivery.delivered_at")
        ),
        "confirmed_at": _canonical(
            _instant(
                confirmation.get("confirmed_at"), field="confirmation.confirmed_at"
            )
        ),
        "attention_kind": kind,
    }


def _publish(path: Path, text: str) -> None:
    if path.is_symlink():
        raise ValueError("LoopX attention artifact path cannot be a symlink")
    if path.exists():
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            raise ValueError("LoopX attention artifact conflicts with existing bytes")
        return
    try:
        atomic_write_state_text(path, text, create_only=True)
    except FileExistsError:
        if path.is_symlink() or path.read_text(encoding="utf-8") != text:
            raise ValueError("LoopX attention artifact conflicts with existing bytes")


def capture_verified_loopx_human_attention(
    *,
    runtime_root: Path,
    input_value: Mapping[str, Any],
    source_root: Path,
    start: str,
    end: str,
) -> dict[str, Any]:
    """Return a copied trajectory input bound to immutable projected receipts."""

    input_data = deepcopy(dict(input_value))
    scope = _object(input_data.get("scope"))
    sources = input_data.get("sources")
    if scope is None or not isinstance(sources, list):
        raise ValueError("Trajectory input requires scope and sources")
    if input_data.get("human_attention_source_id") is not None:
        raise ValueError("Trajectory input already binds a human attention capture")
    goal_id = str(scope.get("goal_id") or "")
    if not goal_id:
        raise ValueError("Trajectory input requires a Goal scope")
    start_at, canonical_start = _capture_instant(start, field="LoopX attention start")
    end_at, canonical_end = _capture_instant(end, field="LoopX attention end")
    if end_at < start_at:
        raise ValueError("LoopX attention end must not precede start")
    action_root = runtime_root.resolve() / "chat" / "actions"
    if not (action_root / "actions.json").is_file():
        raise ValueError("LoopX Chat action receipts are unavailable")

    projected: list[dict[str, Any]] = []
    store = ChatActionStore(action_root)
    for proposal in store.list(goal_id=goal_id):
        decision = (
            _operation_decision(proposal)
            if proposal.get("action_kind") == "operation.execute"
            else _review_decision(proposal)
            if proposal.get("action_kind") == "team.plan"
            else None
        )
        if decision is None:
            continue
        confirmed_at = _instant(
            decision["confirmed_at"], field="confirmation.confirmed_at"
        )
        if start_at <= confirmed_at <= end_at:
            projected.append(decision)
    projected.sort(key=lambda row: (row["confirmed_at"], row["receipt_id"]))
    if len(projected) > 10_000:
        raise ValueError("LoopX attention capture exceeds 10000 decisions")

    header = {
        "schema_version": "loopx_human_attention_receipts_v1",
        "scope": dict(scope),
        "start": canonical_start,
        "end": canonical_end,
        "complete": False,
        "channels": list(_CHANNELS),
        "omitted": list(_OMITTED),
    }
    receipt_text = "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in [header, *projected]
    )
    receipt_digest = hashlib.sha256(receipt_text.encode("utf-8")).hexdigest()
    receipt_id = "loopx-human-attention-receipts-" + receipt_digest[:16]
    receipt_artifact = f"{receipt_id}.jsonl"

    events = []
    for line_number, row in enumerate(projected, 2):
        delivered_at = _instant(row["delivered_at"], field="delivery.delivered_at")
        confirmed_at = _instant(row["confirmed_at"], field="confirmation.confirmed_at")
        request = (
            {"start": row["delivered_at"], "end": row["confirmed_at"]}
            if start_at <= delivered_at <= confirmed_at <= end_at
            else None
        )
        events.append(
            {
                "event_id": row["receipt_id"],
                "episode_id": None,
                "kind": row["attention_kind"],
                "necessity": "required",
                "occurred_at": row["confirmed_at"],
                "active": None,
                "request": request,
                "citation": {
                    "source_id": receipt_id,
                    "first_line": line_number,
                    "last_line": line_number,
                },
            }
        )
    capture = {
        "schema_version": "trajectory_human_attention_v1",
        "scope": dict(scope),
        "start": canonical_start,
        "end": canonical_end,
        "complete": False,
        "events": events,
    }
    capture_text = json.dumps(
        capture, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    capture_digest = hashlib.sha256(capture_text.encode("utf-8")).hexdigest()
    capture_id = "loopx-human-attention-capture-" + capture_digest[:16]
    capture_artifact = f"{capture_id}.json"

    root = source_root.resolve(strict=True)
    existing_ids = {
        str(source.get("id") or "")
        for source in sources
        if isinstance(source, Mapping)
    }
    if existing_ids.intersection({receipt_id, capture_id}):
        raise ValueError("Generated LoopX attention source ID already exists")
    _publish(root / receipt_artifact, receipt_text)
    _publish(root / capture_artifact, capture_text)
    additions = [
        {
            "id": receipt_id,
            "artifact": receipt_artifact,
            "artifact_sha256": receipt_digest,
            "first_line": 1,
            "text": receipt_text,
        },
        {
            "id": capture_id,
            "artifact": capture_artifact,
            "artifact_sha256": capture_digest,
            "first_line": 1,
            "text": capture_text,
        },
    ]
    input_data["sources"] = [*sources, *additions]
    input_data["human_attention_source_id"] = capture_id
    return input_data
