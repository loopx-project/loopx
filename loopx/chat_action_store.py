"""Owner-local persistence for typed LoopX Chat action proposals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
import uuid

from .file_lock import exclusive_file_lock
from .registry import atomic_write_json


CHAT_ACTION_STORE_SCHEMA_VERSION = "loopx_chat_action_store_v1"
CHAT_ACTION_PROPOSAL_SCHEMA_VERSION = "loopx_chat_action_proposal_v1"

ACTION_KINDS = {
    "goal.create",
    "goal.update",
    "goal.lifecycle",
    "todo.create",
    "todo.update",
    "agent.bind",
    "heartbeat.bind",
    "monitor.create",
    "monitor.update",
    "gate.resolve",
    "run.correct",
    "operation.execute",
    "team.plan",
}
PROPOSAL_STATES = {
    "preview_ready",
    "applying",
    "gated",
    "failed",
    "rejected",
    "deferred",
    "cancelled",
    "stale",
    "applied",
}
RETRYABLE_PROPOSAL_STATES = {"preview_ready", "gated", "failed", "deferred"}

OPERATION_ENVELOPE_SCHEMA_VERSION = "loopx_operation_envelope_v0"
OPERATION_LIFECYCLE_STATES = {
    "awaiting_confirmation",
    "claimed",
    "outcome_observed",
}

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_LOCAL_PATH = re.compile(
    r"(?:^|[\s\"'])(?:/Users/|/home/|/private/|/var/folders/|/tmp/|~[/\\]|file://)",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/-]+|sk-[A-Za-z0-9_-]{4,}|raw\s+(?:provider|tool)\s+(?:payload|output))",
    re.IGNORECASE,
)
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "credentials",
    "credential",
    "private_key",
    "raw_provider_payload",
    "provider_payload",
    "raw_tool_output",
    "tool_output",
    "stderr",
    "command",
}


class ActionConflictError(RuntimeError):
    """Raised when a typed action transition conflicts with durable state."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _opaque_id(value: Any, *, field: str) -> str:
    token = str(value or "").strip()
    if not _OPAQUE_ID.fullmatch(token):
        raise ValueError(f"{field} must be a compact opaque id")
    return token


def _bounded_text(value: Any, *, field: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(ord(character) < 32 for character in text):
        raise ValueError(f"{field} must be bounded visible text")
    return text


def _safe_json_value(value: Any, *, path: str = "payload") -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain finite JSON numbers")
        return value
    if isinstance(value, str):
        if _LOCAL_PATH.search(value) or _SENSITIVE_TEXT.search(value):
            raise ValueError(f"{path} contains sensitive material")
        if len(value) > 8000 or any(ord(character) < 9 for character in value):
            raise ValueError(f"{path} must contain bounded public-safe text")
        return value
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key).strip()
            normalized_key = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
            if not key or normalized_key in _SENSITIVE_KEYS:
                raise ValueError(f"{path}.{key or '<empty>'} is a sensitive field")
            safe[key] = _safe_json_value(item, path=f"{path}.{key}")
        return safe
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [
            _safe_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{path} must be JSON-compatible")


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ChatActionStore:
    """Persist typed action previews and receipts in an explicit local root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "actions.json"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        if not self.path.exists():
            atomic_write_json(self.path, self._empty_payload())
        os.chmod(self.path, 0o600)

    @staticmethod
    def _empty_payload() -> dict[str, Any]:
        return {
            "schema_version": CHAT_ACTION_STORE_SCHEMA_VERSION,
            "proposals": {},
            "idempotency": {},
        }

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("typed Chat action store is unreadable") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != CHAT_ACTION_STORE_SCHEMA_VERSION
        ):
            raise ValueError("typed Chat action store has an unsupported schema")
        if not isinstance(payload.get("proposals"), dict) or not isinstance(
            payload.get("idempotency"), dict
        ):
            raise ValueError("typed Chat action store is malformed")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.path, payload, preserve_mode=True)
        os.chmod(self.path, 0o600)

    def create_preview(
        self,
        *,
        action_kind: str,
        summary: str,
        normalized_parameters: Mapping[str, Any],
        context: Mapping[str, Any],
        expected_state_fingerprint: str,
        permission_classification: str,
        validation_evidence: Sequence[Any],
        available_transitions: Sequence[Any],
        idempotency_key: str,
        canonical_update_basis: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_kind = str(action_kind or "").strip()
        if selected_kind not in ACTION_KINDS:
            raise ValueError(f"unsupported action_kind: {selected_kind or '<empty>'}")
        request = _safe_json_value(
            {
                "action_kind": selected_kind,
                "summary": _bounded_text(summary, field="summary"),
                "normalized_parameters": dict(normalized_parameters),
                "context": dict(context),
                "expected_state_fingerprint": _bounded_text(
                    expected_state_fingerprint,
                    field="expected_state_fingerprint",
                    limit=512,
                ),
                "permission_classification": _opaque_id(
                    permission_classification,
                    field="permission_classification",
                ),
                "validation_evidence": list(validation_evidence),
                "available_transitions": list(available_transitions),
                **({"canonical_update_basis": dict(canonical_update_basis)}
                   if canonical_update_basis is not None else {}),
            },
            path="preview",
        )
        key = _opaque_id(idempotency_key, field="idempotency_key")
        request_digest = _canonical_digest(request)
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="create_chat_action_preview",
        ):
            payload = self._read()
            existing_binding = payload["idempotency"].get(key)
            if isinstance(existing_binding, dict):
                proposal_id = str(existing_binding.get("proposal_id") or "")
                if existing_binding.get("request_digest") != request_digest:
                    raise ActionConflictError(
                        "idempotency key already belongs to another preview"
                    )
                existing = payload["proposals"].get(proposal_id)
                if not isinstance(existing, dict):
                    raise ValueError(
                        "typed Chat action idempotency index is inconsistent"
                    )
                return existing

            now = _utc_now()
            proposal_id = f"proposal-{uuid.uuid4().hex}"
            proposal = {
                "schema_version": CHAT_ACTION_PROPOSAL_SCHEMA_VERSION,
                "proposal_id": proposal_id,
                **request,
                "idempotency_key": key,
                "request_digest": request_digest,
                "status": "preview_ready",
                "receipt": None,
                "operation": None,
                "review_card": None,
                "gate": None,
                "failure": None,
                "checkpoint": None,
                "stale": None,
                "regenerated_from": None,
                "created_at": now,
                "updated_at": now,
                "cancelled_at": None,
                "rejected_at": None,
                "deferred_at": None,
                "applied_at": None,
            }
            payload["proposals"][proposal_id] = proposal
            payload["idempotency"][key] = {
                "proposal_id": proposal_id,
                "request_digest": request_digest,
            }
            self._write(payload)
            return proposal

    def record_review_card_delivery(
        self,
        proposal_id: str,
        *,
        audience_id: str,
        delivery: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind one verified external review surface to a typed proposal.

        A proposal may be rendered into more than one audience, but every card
        retains the proposal id and state fingerprint owned by the typed action
        store.  Recording all audiences on that one proposal is what prevents a
        manager card and a Goal card from becoming two independent approvals.
        """

        audience = _opaque_id(audience_id, field="review_card.audience_id")
        safe_delivery = _safe_json_value(
            dict(delivery), path=f"review_card.deliveries.{audience}"
        )
        if not isinstance(safe_delivery, dict):
            raise ValueError("review card delivery must be an object")
        required = {
            "provider",
            "message_id",
            "chat_id",
            "app_id",
            "cli_bin",
            "sender_profile",
            "binding_digest",
            "card_digest",
            "submitted_card",
            "delivered_at",
            "authorized_principal",
        }
        if set(safe_delivery) != required:
            raise ValueError(
                "review card delivery has unsupported or missing fields"
            )
        for field in required - {"submitted_card"}:
            _bounded_text(
                safe_delivery.get(field),
                field=f"review_card.delivery.{field}",
                limit=512,
            )
        submitted_card = safe_delivery.get("submitted_card")
        if not isinstance(submitted_card, dict):
            raise ValueError("review card submitted payload must be an object")
        if _canonical_digest(submitted_card) != safe_delivery["card_digest"]:
            raise ActionConflictError("review card submitted payload drifted")
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="record_review_card_delivery",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            if proposal.get("action_kind") != "team.plan":
                raise ActionConflictError(
                    "only a team plan can bind this review card"
                )
            review_card = proposal.get("review_card")
            if review_card is None:
                raise ActionConflictError(
                    "review card audiences must be prepared before delivery"
                )
            if not isinstance(review_card, dict):
                raise ValueError("typed review card state is malformed")
            if review_card.get("state_fingerprint") != proposal.get(
                "expected_state_fingerprint"
            ):
                raise ActionConflictError("review card state fingerprint drifted")
            if review_card.get("authorized_principal") != safe_delivery.get(
                "authorized_principal"
            ):
                raise ActionConflictError(
                    "review card audience changed the authorized principal"
                )
            deliveries = review_card.get("deliveries")
            if not isinstance(deliveries, dict):
                raise ValueError("typed review card deliveries are malformed")
            expected_audiences = review_card.get("expected_audience_ids")
            if (
                not isinstance(expected_audiences, list)
                or audience not in expected_audiences
            ):
                raise ActionConflictError("review card audience was not prepared")
            existing = deliveries.get(audience)
            if existing is not None:
                immutable_fields = required - {"delivered_at"}
                if not isinstance(existing, Mapping) or any(
                    existing.get(field) != safe_delivery.get(field)
                    for field in immutable_fields
                ):
                    raise ActionConflictError(
                        "review card audience is already bound to another message"
                    )
                return proposal
            if review_card.get("confirmation") is not None:
                raise ActionConflictError("review card decision is already consumed")
            deliveries[audience] = safe_delivery
            proposal["updated_at"] = _utc_now()
            self._write(payload)
            return proposal

    def prepare_review_card_delivery(
        self,
        proposal_id: str,
        *,
        audience_ids: Sequence[str],
        authorized_principal: str,
    ) -> dict[str, Any]:
        """Freeze every audience before the first actionable card is sent."""

        normalized_audiences = sorted(
            {_opaque_id(value, field="review_card.audience_id") for value in audience_ids}
        )
        if not normalized_audiences or len(normalized_audiences) != len(audience_ids):
            raise ValueError("review card audiences must be non-empty and unique")
        principal = _opaque_id(
            authorized_principal, field="review_card.authorized_principal"
        )
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="prepare_review_card_delivery",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            if proposal.get("action_kind") != "team.plan":
                raise ActionConflictError(
                    "only a team plan can bind this review card"
                )
            expected = {
                "schema_version": "loopx_review_card_delivery_v0",
                "state_fingerprint": proposal.get("expected_state_fingerprint"),
                "expected_audience_ids": normalized_audiences,
                "deliveries": {},
                "confirmation": None,
                "authorized_principal": principal,
            }
            existing = proposal.get("review_card")
            if existing is None:
                proposal["review_card"] = expected
                proposal["updated_at"] = _utc_now()
                self._write(payload)
                return proposal
            if not isinstance(existing, dict):
                raise ValueError("typed review card state is malformed")
            immutable = {
                "schema_version": expected["schema_version"],
                "state_fingerprint": expected["state_fingerprint"],
                "expected_audience_ids": expected["expected_audience_ids"],
                "authorized_principal": expected["authorized_principal"],
            }
            if any(existing.get(key) != value for key, value in immutable.items()):
                raise ActionConflictError("review card audience plan drifted")
            return proposal

    def decide_review_card(
        self,
        proposal_id: str,
        *,
        decision: str,
        confirmation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Consume one verified card decision across every delivered audience."""

        selected_decision = str(decision or "").strip().lower()
        if selected_decision not in {"confirm", "reject"}:
            raise ValueError("review card decision must be confirm or reject")
        safe_confirmation = _safe_json_value(
            dict(confirmation), path="review_card.confirmation"
        )
        if not isinstance(safe_confirmation, dict):
            raise ValueError("review card confirmation must be an object")
        required = {
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
        }
        if set(safe_confirmation) != required:
            raise ValueError(
                "review card confirmation has unsupported or missing fields"
            )
        for field in required:
            _bounded_text(
                safe_confirmation.get(field),
                field=f"review_card.confirmation.{field}",
                limit=512,
            )
        token = _opaque_id(proposal_id, field="proposal_id")
        audience = _opaque_id(
            safe_confirmation["audience_id"], field="review_card.audience_id"
        )
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="decide_review_card",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            review_card = (
                proposal.get("review_card") if isinstance(proposal, dict) else None
            )
            if not isinstance(review_card, dict):
                raise KeyError("typed review card was not found")
            deliveries = review_card.get("deliveries")
            delivery = (
                deliveries.get(audience) if isinstance(deliveries, dict) else None
            )
            if not isinstance(delivery, dict):
                raise ActionConflictError("review card audience was not delivered")
            expected_audiences = review_card.get("expected_audience_ids")
            if (
                not isinstance(expected_audiences, list)
                or set(deliveries) != set(expected_audiences)
            ):
                raise ActionConflictError(
                    "review card audiences are not completely delivered"
                )
            expected = {
                "provider": delivery.get("provider"),
                "message_id": delivery.get("message_id"),
                "chat_id": delivery.get("chat_id"),
                "app_id": delivery.get("app_id"),
                "state_fingerprint": review_card.get("state_fingerprint"),
                "card_digest": delivery.get("card_digest"),
            }
            if any(
                safe_confirmation.get(field) != value
                for field, value in expected.items()
            ):
                raise ActionConflictError(
                    "review card callback does not match the delivered request"
                )
            if safe_confirmation.get("principal") != review_card.get(
                "authorized_principal"
            ):
                raise ActionConflictError(
                    "principal is not authorized for this review card"
                )
            existing = review_card.get("confirmation")
            if isinstance(existing, dict):
                # Every audience is a view of the same proposal. Once one exact
                # decision wins, later clicks only observe that decision and can
                # never launch a second canonical effect.
                return proposal
            status = str(proposal.get("status") or "")
            if status not in {"preview_ready", "deferred"}:
                raise ActionConflictError(
                    "review card proposal is no longer awaiting confirmation"
                )
            now = _utc_now()
            review_card["confirmation"] = {
                **safe_confirmation,
                "decision": selected_decision,
            }
            if selected_decision == "confirm":
                proposal["status"] = "applying"
                proposal["gate"] = None
                proposal["failure"] = None
            else:
                proposal["status"] = "rejected"
                proposal["rejected_at"] = now
            proposal["updated_at"] = now
            self._write(payload)
            return proposal

    def record_review_card_result_delivery(
        self,
        proposal_id: str,
        *,
        audience_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Record exact result-card readback for one already-bound audience."""

        audience = _opaque_id(audience_id, field="review_card.audience_id")
        safe_result = _safe_json_value(
            dict(result), path=f"review_card.deliveries.{audience}.result"
        )
        if not isinstance(safe_result, dict):
            raise ValueError("review card result delivery must be an object")
        required = {"card_digest", "transport", "delivered_at"}
        if set(safe_result) != required:
            raise ValueError(
                "review card result delivery has unsupported or missing fields"
            )
        for field in required:
            _bounded_text(
                safe_result.get(field),
                field=f"review_card.result.{field}",
                limit=512,
            )
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="record_review_card_result_delivery",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            review_card = (
                proposal.get("review_card") if isinstance(proposal, dict) else None
            )
            deliveries = (
                review_card.get("deliveries")
                if isinstance(review_card, dict)
                else None
            )
            delivery = (
                deliveries.get(audience) if isinstance(deliveries, dict) else None
            )
            if not isinstance(delivery, dict):
                raise KeyError("typed review card audience was not found")
            existing = delivery.get("result")
            if existing is not None:
                if existing != safe_result:
                    raise ActionConflictError(
                        "review card result delivery is already immutable"
                    )
                return proposal
            delivery["result"] = safe_result
            proposal["updated_at"] = _utc_now()
            self._write(payload)
            return proposal

    def arm_operation(self, proposal_id: str) -> dict[str, Any]:
        """Turn one provider-neutral preview into the canonical confirmation gate.

        The immutable request already lives on the proposal.  This method adds
        only lifecycle state derived from that request; Lark, the Dashboard and
        domain executors must all read this same envelope.
        """

        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="arm_operation_confirmation",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            if proposal.get("action_kind") != "operation.execute":
                raise ActionConflictError(
                    "only operation.execute previews can be armed"
                )
            existing = proposal.get("operation")
            if isinstance(existing, dict):
                if (
                    existing.get("schema_version") != OPERATION_ENVELOPE_SCHEMA_VERSION
                    or existing.get("lifecycle_state") not in OPERATION_LIFECYCLE_STATES
                ):
                    raise ValueError("typed operation envelope is malformed")
                return proposal
            if proposal.get("status") != "preview_ready":
                raise ActionConflictError(
                    f"proposal in {proposal.get('status')} state cannot await confirmation"
                )
            parameters = proposal.get("normalized_parameters")
            if not isinstance(parameters, dict):
                raise ValueError("typed operation parameters are malformed")
            now = _utc_now()
            confirmation_digest = _canonical_digest(
                {
                    "action_kind": "operation.execute",
                    "normalized_parameters": parameters,
                    "request_digest": proposal.get("request_digest"),
                }
            )
            operation = {
                "schema_version": OPERATION_ENVELOPE_SCHEMA_VERSION,
                "operation_id": token,
                "lifecycle_state": "awaiting_confirmation",
                "confirmation_digest": confirmation_digest,
                "payload_digest": parameters.get("payload_digest"),
                "projection_digest": parameters.get("projection_digest"),
                "executor_revision": (
                    parameters.get("executor", {}).get("revision")
                    if isinstance(parameters.get("executor"), dict)
                    else None
                ),
                "destination_account_ref": parameters.get("destination_account_ref"),
                "expires_at": parameters.get("expires_at"),
                "authorized_principals": list(
                    parameters.get("authorized_principals") or []
                ),
                "delivery": None,
                "confirmation": None,
                "claim": None,
                "outcome": None,
                "result_delivery": None,
                "armed_at": now,
            }
            proposal["operation"] = _safe_json_value(operation, path="operation")
            proposal["status"] = "gated"
            proposal["gate"] = {
                "kind": "human_operation_confirmation",
                "summary": "This exact operation is awaiting an authenticated human decision.",
                "next_action": "Use the bound operation card; ordinary typed-action apply is not confirmation.",
            }
            proposal["updated_at"] = now
            self._write(payload)
            return proposal

    def record_operation_delivery(
        self,
        proposal_id: str,
        *,
        delivery: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind the exact provider message which may attest a later click."""

        safe_delivery = _safe_json_value(dict(delivery), path="operation.delivery")
        if not isinstance(safe_delivery, dict):
            raise ValueError("operation delivery must be an object")
        for field in (
            "provider",
            "message_id",
            "chat_id",
            "app_id",
            "binding_digest",
            "card_digest",
            "delivered_at",
        ):
            _bounded_text(
                safe_delivery.get(field),
                field=f"operation.delivery.{field}",
                limit=512,
            )
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="record_operation_delivery",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            operation = (
                proposal.get("operation") if isinstance(proposal, dict) else None
            )
            if not isinstance(operation, dict):
                raise KeyError("typed operation was not found")
            if operation.get("lifecycle_state") != "awaiting_confirmation":
                existing = operation.get("delivery")
                if isinstance(existing, dict) and existing == safe_delivery:
                    return proposal
                raise ActionConflictError("operation is no longer awaiting delivery")
            existing = operation.get("delivery")
            if existing is not None:
                if existing != safe_delivery:
                    raise ActionConflictError(
                        "operation is already bound to another provider message"
                    )
                return proposal
            operation["delivery"] = safe_delivery
            proposal["updated_at"] = _utc_now()
            self._write(payload)
            return proposal

    def record_operation_delivery_snapshot(
        self,
        proposal_id: str,
        *,
        submitted_card: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist the immutable sent card after verifying its recorded digest."""

        safe_card = _safe_json_value(
            dict(submitted_card), path="operation.delivery.submitted_card"
        )
        if not isinstance(safe_card, dict):
            raise ValueError("operation submitted card must be an object")
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="record_operation_delivery_snapshot",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            operation = (
                proposal.get("operation") if isinstance(proposal, dict) else None
            )
            delivery = (
                operation.get("delivery") if isinstance(operation, dict) else None
            )
            if not isinstance(delivery, dict):
                raise ActionConflictError("operation card delivery was not recorded")
            if _canonical_digest(safe_card) != delivery.get("card_digest"):
                raise ActionConflictError("operation submitted card digest drifted")
            existing = delivery.get("submitted_card")
            if existing is not None:
                if existing != safe_card:
                    raise ActionConflictError(
                        "operation submitted card snapshot already differs"
                    )
                return proposal
            delivery["submitted_card"] = safe_card
            proposal["updated_at"] = _utc_now()
            self._write(payload)
            return proposal

    def decide_operation(
        self,
        proposal_id: str,
        *,
        decision: str,
        confirmation: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically reject, or confirm and claim, one delivered operation.

        The transport owner must supply a verified callback envelope.  A
        successful confirmation consumes the operation in this transaction so
        concurrent web/Lark clicks cannot both dispatch it.
        """

        selected_decision = str(decision or "").strip().lower()
        if selected_decision not in {"confirm", "reject"}:
            raise ValueError("operation decision must be confirm or reject")
        safe_confirmation = _safe_json_value(
            dict(confirmation), path="operation.confirmation"
        )
        if not isinstance(safe_confirmation, dict):
            raise ValueError("operation confirmation must be an object")
        required = {
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
        }
        if set(safe_confirmation) != required:
            raise ValueError("operation confirmation has unsupported or missing fields")
        for field in required:
            _bounded_text(
                safe_confirmation.get(field),
                field=f"operation.confirmation.{field}",
                limit=512,
            )
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="decide_operation",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            operation = (
                proposal.get("operation") if isinstance(proposal, dict) else None
            )
            if not isinstance(operation, dict):
                raise KeyError("typed operation was not found")
            existing_confirmation = operation.get("confirmation")
            if isinstance(existing_confirmation, dict):
                if (
                    existing_confirmation.get("event_id")
                    == safe_confirmation["event_id"]
                ):
                    return proposal
                raise ActionConflictError("operation already consumed another decision")
            if (
                proposal.get("status") != "gated"
                or operation.get("lifecycle_state") != "awaiting_confirmation"
            ):
                raise ActionConflictError("operation is not awaiting confirmation")
            delivery = operation.get("delivery")
            if not isinstance(delivery, dict):
                raise ActionConflictError("operation card delivery is not verified")
            expected = {
                "provider": delivery.get("provider"),
                "message_id": delivery.get("message_id"),
                "chat_id": delivery.get("chat_id"),
                "app_id": delivery.get("app_id"),
                "card_digest": delivery.get("card_digest"),
                "confirmation_digest": operation.get("confirmation_digest"),
            }
            if any(
                safe_confirmation.get(field) != value
                for field, value in expected.items()
            ):
                raise ActionConflictError(
                    "operation callback does not match the delivered request"
                )
            if safe_confirmation.get("principal") not in set(
                operation.get("authorized_principals") or []
            ):
                raise ActionConflictError(
                    "principal is not authorized for this operation"
                )
            expires_at = datetime.fromisoformat(
                str(operation.get("expires_at") or "").replace("Z", "+00:00")
            )
            confirmed_at = datetime.fromisoformat(
                str(safe_confirmation["confirmed_at"]).replace("Z", "+00:00")
            )
            delivered_at = datetime.fromisoformat(
                str(delivery.get("delivered_at") or "").replace("Z", "+00:00")
            )
            if (
                expires_at.tzinfo is None
                or confirmed_at.tzinfo is None
                or delivered_at.tzinfo is None
            ):
                raise ValueError("operation timestamps require a timezone")
            if confirmed_at < delivered_at - timedelta(minutes=5):
                raise ActionConflictError(
                    "operation confirmation predates the delivered request"
                )
            if confirmed_at > expires_at:
                raise ActionConflictError("operation confirmation arrived after expiry")
            now = _utc_now()
            now_at = datetime.fromisoformat(now.replace("Z", "+00:00"))
            if confirmed_at > now_at + timedelta(minutes=5):
                raise ActionConflictError(
                    "operation confirmation timestamp is in the future"
                )
            operation["confirmation"] = {
                **safe_confirmation,
                "decision": selected_decision,
            }
            if selected_decision == "reject":
                operation["lifecycle_state"] = "outcome_observed"
                operation["outcome"] = {
                    "schema_version": "loopx_operation_outcome_v0",
                    "outcome": "rejected_by_operator",
                    "projection_verified": True,
                    "observed_at": now,
                }
                proposal["status"] = "rejected"
                proposal["rejected_at"] = now
                proposal["receipt"] = operation["outcome"]
            else:
                operation["lifecycle_state"] = "claimed"
                operation["claim"] = {
                    "claim_id": "claim-" + uuid.uuid4().hex,
                    "event_id": safe_confirmation["event_id"],
                    "claimed_at": now,
                }
                proposal["status"] = "applying"
            proposal["gate"] = None
            proposal["updated_at"] = now
            self._write(payload)
            return proposal

    def observe_operation_outcome(
        self,
        proposal_id: str,
        *,
        outcome: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist the domain result without making it a retryable submission."""

        safe_outcome = _safe_json_value(dict(outcome), path="operation.outcome")
        if not isinstance(safe_outcome, dict):
            raise ValueError("operation outcome must be an object")
        if safe_outcome.get("schema_version") != "loopx_operation_outcome_v0":
            raise ValueError("operation outcome schema is unsupported")
        _opaque_id(safe_outcome.get("outcome"), field="operation.outcome.outcome")
        if safe_outcome.get("projection_verified") is not True:
            raise ValueError("operation outcome projection must be verified")
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="observe_operation_outcome",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            operation = (
                proposal.get("operation") if isinstance(proposal, dict) else None
            )
            if not isinstance(operation, dict):
                raise KeyError("typed operation was not found")
            if operation.get("lifecycle_state") == "outcome_observed":
                if operation.get("outcome") != safe_outcome:
                    raise ActionConflictError("operation outcome is already immutable")
                return proposal
            if (
                operation.get("lifecycle_state") != "claimed"
                or proposal.get("status") != "applying"
            ):
                raise ActionConflictError(
                    "only a claimed operation can record an outcome"
                )
            now = _utc_now()
            operation["lifecycle_state"] = "outcome_observed"
            operation["outcome"] = safe_outcome
            proposal["status"] = "applied"
            proposal["receipt"] = safe_outcome
            proposal["applied_at"] = now
            proposal["updated_at"] = now
            self._write(payload)
            return proposal

    def record_operation_result_delivery(
        self,
        proposal_id: str,
        *,
        delivery: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist exact result-card readback without changing domain outcome."""

        safe_delivery = _safe_json_value(
            dict(delivery), path="operation.result_delivery"
        )
        if not isinstance(safe_delivery, dict):
            raise ValueError("operation result delivery must be an object")
        required = {
            "provider",
            "message_id",
            "chat_id",
            "app_id",
            "card_digest",
            "transport",
            "delivered_at",
        }
        if set(safe_delivery) != required:
            raise ValueError(
                "operation result delivery has unsupported or missing fields"
            )
        for field in required:
            _bounded_text(
                safe_delivery.get(field),
                field=f"operation.result_delivery.{field}",
                limit=512,
            )
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="record_operation_result_delivery",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            operation = (
                proposal.get("operation") if isinstance(proposal, dict) else None
            )
            if not isinstance(operation, dict):
                raise KeyError("typed operation was not found")
            if operation.get("lifecycle_state") != "outcome_observed":
                raise ActionConflictError(
                    "operation outcome is unavailable for result delivery"
                )
            source_delivery = operation.get("delivery")
            if not isinstance(source_delivery, dict) or any(
                safe_delivery.get(field) != source_delivery.get(field)
                for field in ("provider", "message_id", "chat_id", "app_id")
            ):
                raise ActionConflictError(
                    "operation result delivery does not match the original card"
                )
            existing = operation.get("result_delivery")
            if isinstance(existing, dict):
                if existing != safe_delivery:
                    raise ActionConflictError(
                        "operation result delivery is already immutable"
                    )
                return proposal
            operation["result_delivery"] = safe_delivery
            proposal["updated_at"] = _utc_now()
            self._write(payload)
            return proposal

    def load(self, proposal_id: str) -> dict[str, Any] | None:
        token = _opaque_id(proposal_id, field="proposal_id")
        proposal = self._read()["proposals"].get(token)
        if proposal is None:
            return None
        if (
            not isinstance(proposal, dict)
            or proposal.get("status") not in PROPOSAL_STATES
        ):
            raise ValueError("typed Chat action proposal is malformed")
        return proposal

    def list(
        self,
        *,
        goal_id: str | None = None,
        context_kind: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        selected_goal = _opaque_id(goal_id, field="goal_id") if goal_id else None
        selected_context = (
            _opaque_id(context_kind, field="context_kind") if context_kind else None
        )
        if status and status not in PROPOSAL_STATES:
            raise ValueError("status must be a supported typed action state")
        proposals: list[dict[str, Any]] = []
        for raw in self._read()["proposals"].values():
            if not isinstance(raw, dict) or raw.get("status") not in PROPOSAL_STATES:
                continue
            context = raw.get("context")
            context = context if isinstance(context, dict) else {}
            parameters = raw.get("normalized_parameters")
            parameters = parameters if isinstance(parameters, dict) else {}
            proposal_goal = str(
                context.get("goal_id") or parameters.get("goal_id") or ""
            )
            if selected_goal and proposal_goal != selected_goal:
                continue
            if selected_context and str(context.get("kind") or "") != selected_context:
                continue
            if status and raw.get("status") != status:
                continue
            proposals.append(raw)
        return sorted(
            proposals,
            key=lambda proposal: (
                str(proposal.get("updated_at") or ""),
                str(proposal.get("proposal_id") or ""),
            ),
            reverse=True,
        )

    def cancel(self, proposal_id: str) -> dict[str, Any]:
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="cancel_chat_action_preview",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            if proposal.get("status") == "cancelled":
                return proposal
            if proposal.get("status") not in {
                "preview_ready",
                "gated",
                "failed",
                "deferred",
            }:
                raise ActionConflictError(
                    f"proposal in {proposal.get('status')} state cannot be cancelled"
                )
            now = _utc_now()
            proposal["status"] = "cancelled"
            proposal["cancelled_at"] = now
            operation = proposal.get("operation")
            if isinstance(operation, dict):
                operation["lifecycle_state"] = "outcome_observed"
                operation["outcome"] = {
                    "schema_version": "loopx_operation_outcome_v0",
                    "outcome": "cancelled_before_confirmation",
                    "projection_verified": True,
                    "observed_at": now,
                }
                proposal["receipt"] = operation["outcome"]
                proposal["gate"] = None
            proposal["updated_at"] = now
            self._write(payload)
            return proposal

    def start_apply(self, proposal_id: str) -> dict[str, Any]:
        """Persist the execution boundary before any canonical write is attempted."""

        return self._transition(
            proposal_id,
            from_states=RETRYABLE_PROPOSAL_STATES,
            to_state="applying",
            updates={"gate": None, "failure": None},
            idempotent_states={"applying", "applied"},
        )

    def mark_gated(
        self, proposal_id: str, *, gate: Mapping[str, Any]
    ) -> dict[str, Any]:
        safe_gate = _safe_json_value(dict(gate), path="gate")
        if not isinstance(safe_gate, dict):
            raise ValueError("gate must be an object")
        _opaque_id(safe_gate.get("kind"), field="gate.kind")
        return self._transition(
            proposal_id,
            from_states={"applying", "preview_ready"},
            to_state="gated",
            updates={"gate": safe_gate, "failure": None},
            idempotent_states={"gated"},
        )

    def mark_failed(
        self,
        proposal_id: str,
        *,
        error_code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        # A failure that happened after part of the work was committed has to
        # carry those identities, or the retry cannot tell what already exists
        # and the owner has to reconstruct it from the message.
        record: dict[str, Any] = {
            "error_code": _opaque_id(error_code, field="error_code"),
            "message": _bounded_text(message, field="message", limit=1000),
            "failed_at": _utc_now(),
            "retry_safe": True,
        }
        if details is not None:
            record["details"] = dict(details)
        failure = _safe_json_value(record, path="failure")
        return self._transition(
            proposal_id,
            from_states={"applying", "preview_ready", "gated"},
            to_state="failed",
            updates={"failure": failure},
            idempotent_states={"failed"},
        )

    def save_checkpoint(
        self,
        proposal_id: str,
        *,
        step: str,
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        safe_step = _opaque_id(step, field="step")
        safe_receipt = _safe_json_value(dict(receipt), path="checkpoint.receipt")
        if not isinstance(safe_receipt, dict):
            raise ValueError("checkpoint receipt must be an object")
        token = _opaque_id(proposal_id, field="proposal_id")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="checkpoint_chat_action_preview",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            if proposal.get("status") not in {"applying", "gated", "failed"}:
                raise ActionConflictError(
                    f"proposal in {proposal.get('status')} state cannot save a checkpoint"
                )
            checkpoint = proposal.get("checkpoint")
            checkpoint = (
                dict(checkpoint) if isinstance(checkpoint, dict) else {"steps": {}}
            )
            steps = checkpoint.get("steps")
            steps = dict(steps) if isinstance(steps, dict) else {}
            steps[safe_step] = safe_receipt
            checkpoint["steps"] = steps
            checkpoint["last_completed_step"] = safe_step
            checkpoint["updated_at"] = _utc_now()
            proposal["checkpoint"] = checkpoint
            proposal["updated_at"] = checkpoint["updated_at"]
            self._write(payload)
            return proposal

    def mark_rejected(self, proposal_id: str) -> dict[str, Any]:
        now = _utc_now()
        return self._transition(
            proposal_id,
            from_states={"preview_ready", "gated", "deferred"},
            to_state="rejected",
            updates={"rejected_at": now},
            idempotent_states={"rejected"},
        )

    def mark_deferred(self, proposal_id: str) -> dict[str, Any]:
        now = _utc_now()
        return self._transition(
            proposal_id,
            from_states={"preview_ready", "gated", "failed"},
            to_state="deferred",
            updates={"deferred_at": now},
            idempotent_states={"deferred"},
        )

    def link_regeneration(
        self, proposal_id: str, *, regenerated_from: str
    ) -> dict[str, Any]:
        token = _opaque_id(proposal_id, field="proposal_id")
        source = _opaque_id(regenerated_from, field="regenerated_from")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="link_chat_action_regeneration",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            proposal["regenerated_from"] = source
            proposal["updated_at"] = _utc_now()
            self._write(payload)
            return proposal

    def _transition(
        self,
        proposal_id: str,
        *,
        from_states: set[str],
        to_state: str,
        updates: Mapping[str, Any],
        idempotent_states: set[str],
    ) -> dict[str, Any]:
        token = _opaque_id(proposal_id, field="proposal_id")
        if to_state not in PROPOSAL_STATES:
            raise ValueError("unsupported typed action state")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation=f"transition_chat_action_{to_state}",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            status = str(proposal.get("status") or "")
            if status in idempotent_states:
                return proposal
            if status not in from_states:
                raise ActionConflictError(
                    f"proposal in {status} state cannot become {to_state}"
                )
            proposal["status"] = to_state
            proposal.update(_safe_json_value(dict(updates), path="transition"))
            proposal["updated_at"] = _utc_now()
            self._write(payload)
            return proposal

    def apply(
        self,
        proposal_id: str,
        *,
        current_state_fingerprint: str,
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        token = _opaque_id(proposal_id, field="proposal_id")
        current_fingerprint = _bounded_text(
            current_state_fingerprint,
            field="current_state_fingerprint",
            limit=512,
        )
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="apply_chat_action_preview",
        ):
            payload = self._read()
            proposal = payload["proposals"].get(token)
            if not isinstance(proposal, dict):
                raise KeyError("typed Chat action proposal was not found")
            status = str(proposal.get("status") or "")
            if status == "applied":
                return proposal
            if status not in {"preview_ready", "applying"}:
                raise ActionConflictError(
                    f"proposal in {status} state cannot be applied"
                )
            expected_fingerprint = str(proposal.get("expected_state_fingerprint") or "")
            now = _utc_now()
            if current_fingerprint != expected_fingerprint:
                proposal["status"] = "stale"
                proposal["stale"] = {
                    "expected_state_fingerprint": expected_fingerprint,
                    "current_state_fingerprint": current_fingerprint,
                    "detected_at": now,
                }
                proposal["updated_at"] = now
                self._write(payload)
                return proposal

            safe_receipt = _safe_json_value(dict(receipt), path="receipt")
            if not isinstance(safe_receipt, dict):
                raise ValueError("receipt must be an object")
            _opaque_id(safe_receipt.get("receipt_id"), field="receipt.receipt_id")
            _opaque_id(safe_receipt.get("outcome"), field="receipt.outcome")
            if safe_receipt.get("projection_verified") is not True:
                raise ValueError("receipt.projection_verified must be true")
            proposal["status"] = "applied"
            proposal["receipt"] = safe_receipt
            proposal["applied_at"] = now
            proposal["updated_at"] = now
            self._write(payload)
            return proposal
