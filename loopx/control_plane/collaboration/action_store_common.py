"""Shared Zero-Any schema, validation, and lock helpers for Chat action persistence."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Protocol, TypeAlias, TypeGuard

from ...public_safe_text import OPAQUE_ID_PATTERN as _OPAQUE_ID

JsonPrimitive: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonDict: TypeAlias = dict[str, object]
ObjectDict: TypeAlias = dict[str, object]

CHAT_ACTION_STORE_SCHEMA_VERSION = "loopx_chat_action_store_v1"
CHAT_ACTION_PROPOSAL_SCHEMA_VERSION = "loopx_chat_action_proposal_v1"
HUMAN_ACTOR_CLASS = "human"
AUTHENTICATED_LARK_CARD_CALLBACK = "lark_authenticated_card_callback_v1"

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


class ActionStoreBackend(Protocol):
    """Minimal storage contract satisfied by ChatActionStore."""

    path: Path

    def _read(self) -> JsonDict: ...

    def _write(self, payload: JsonDict) -> None: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _opaque_id(value: object, *, field: str) -> str:
    token = str(value or "").strip()
    if not _OPAQUE_ID.fullmatch(token):
        raise ValueError(f"{field} must be a compact opaque id")
    return token


def normalize_idempotency_key(value: object) -> str:
    """Return the canonical token this store binds and persists."""
    return _opaque_id(value, field="idempotency_key")


def _bounded_text(value: object, *, field: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(ord(character) < 32 for character in text):
        raise ValueError(f"{field} must be bounded visible text")
    return text


def _safe_json_value(value: object, *, path: str = "payload") -> JsonValue:
    """Recursively validate and clone JSON-compatible public-safe values."""
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
        safe: dict[str, JsonValue] = {}
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


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_str_dict(value: object) -> TypeGuard[JsonDict]:
    """Narrow an arbitrary object to a string-keyed mutable dictionary."""
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def require_str_dict(value: object, *, error: str) -> JsonDict:
    """Return a validated string-keyed dict or raise ValueError."""
    if not is_str_dict(value):
        raise ValueError(error)
    return value


def safe_object_dict(
    value: Mapping[str, object], *, path: str, label: str
) -> JsonDict:
    """Sanitize a mapping and preserve its mutable string-keyed dict contract."""
    safe = _safe_json_value(dict(value), path=path)
    if not is_str_dict(safe):
        raise ValueError(f"{label} must be an object")
    return safe


def validate_safe_object(
    value: Mapping[str, object],
    *,
    path: str,
    label: str,
    required_keys: frozenset[str] | set[str] | None = None,
    allowed_extra_keys: frozenset[str] | set[str] = frozenset(),
    bounded_fields: Iterable[str] = (),
    field_prefix: str = "",
    fields_error: str | None = None,
) -> JsonDict:
    """Validate a public-safe object payload, key set, and bounded text fields."""
    safe = safe_object_dict(value, path=path, label=label)
    if required_keys is not None:
        actual_keys = set(safe)
        allowed_keys = set(required_keys) | set(allowed_extra_keys)
        if not (set(required_keys) <= actual_keys <= allowed_keys):
            raise ValueError(fields_error or f"{label} has unsupported or missing fields")
    prefix = field_prefix or path
    for field in bounded_fields:
        _bounded_text(safe.get(field), field=f"{prefix}.{field}", limit=512)
    return safe


def validate_decision(decision: str, *, label: str) -> str:
    """Validate a human confirmation decision token."""
    selected = str(decision or "").strip().lower()
    if selected not in {"confirm", "reject"}:
        raise ValueError(f"{label} decision must be confirm or reject")
    return selected


def validate_human_confirmation(
    confirmation: Mapping[str, object],
    *,
    path: str,
    label: str,
    required_keys: frozenset[str],
) -> JsonDict:
    """Validate common human-verified callback envelope invariants."""
    safe = validate_safe_object(
        confirmation,
        path=path,
        label=label,
        required_keys=required_keys,
        bounded_fields=required_keys,
        field_prefix=path,
    )
    if safe["actor_class"] != HUMAN_ACTOR_CLASS:
        raise ValueError(f"{label} requires a human actor")
    _opaque_id(safe["verification_kind"], field=f"{path}.verification_kind")
    return safe


def parse_utc_timestamp(value: object, *, error: str) -> datetime:
    """Parse an ISO timestamp and enforce an explicit timezone offset."""
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(error)
    return parsed


def current_utc_now() -> str:
    """Read `_utc_now` through `loopx.chat_action_store` so monkeypatches apply."""
    from ... import chat_action_store as facade

    return facade._utc_now()


@contextmanager
def store_lock(
    path: Path,
    *,
    operation: str,
    agent_id: str | None = "loopx-chat",
) -> Iterator[Path]:
    """Acquire `exclusive_file_lock` through `loopx.chat_action_store`."""
    from ... import chat_action_store as facade

    if agent_id is None:
        with facade.exclusive_file_lock(path, operation=operation) as locked:
            yield locked
        return
    with facade.exclusive_file_lock(
        path, agent_id=agent_id, operation=operation
    ) as locked:
        yield locked


def store_proposals(payload: JsonDict) -> JsonDict:
    """Extract the top-level proposals map from a validated store payload."""
    return require_str_dict(
        payload.get("proposals"), error="typed Chat action store is malformed"
    )


def store_idempotency(payload: JsonDict) -> JsonDict:
    """Extract the top-level idempotency index from a validated store payload."""
    return require_str_dict(
        payload.get("idempotency"), error="typed Chat action store is malformed"
    )


def require_proposal(
    payload: JsonDict,
    token: str,
    *,
    message: str = "typed Chat action proposal was not found",
) -> JsonDict:
    """Load one proposal dict by token or raise KeyError."""
    proposal = store_proposals(payload).get(token)
    if not is_str_dict(proposal):
        raise KeyError(message)
    return proposal


def require_operation(payload: JsonDict, token: str) -> tuple[JsonDict, JsonDict]:
    """Load a proposal and its operation envelope, raising KeyError if absent."""
    proposal = store_proposals(payload).get(token)
    operation = proposal.get("operation") if is_str_dict(proposal) else None
    if not is_str_dict(proposal) or not is_str_dict(operation):
        raise KeyError("typed operation was not found")
    return proposal, operation


def commit_proposal_mutation(
    store: ActionStoreBackend,
    payload: JsonDict,
    proposal: JsonDict,
    *,
    updated_at: str | None = None,
) -> JsonDict:
    """Stamp `updated_at`, persist the store payload, and return the proposal."""
    proposal["updated_at"] = updated_at if updated_at is not None else current_utc_now()
    store._write(payload)
    return proposal


def is_agent_operation_executor(proposal: Mapping[str, object]) -> bool:
    """Return whether a proposal's normalized executor uses agent handoff."""
    parameters = proposal.get("normalized_parameters")
    if not is_str_dict(parameters):
        return False
    executor = parameters.get("executor")
    return is_str_dict(executor) and executor.get("kind") in {
        "agent_session",
        "managed_turn",
    }


def operation_confirmation_digest(
    proposal: Mapping[str, object], parameters: Mapping[str, object]
) -> str:
    """Compute the canonical confirmation digest for an operation.execute request."""
    return _canonical_digest(
        {
            "action_kind": "operation.execute",
            "normalized_parameters": parameters,
            "request_digest": proposal.get("request_digest"),
        }
    )


def _matching_pending_preview(
    proposals: Mapping[str, object],
    *,
    action_kind: str,
    context: Mapping[str, object],
    normalized_parameters: Mapping[str, object],
    shared_fields: tuple[str, ...],
    reusable_states: set[str],
    matches_review_basis: Callable[[object], bool],
) -> JsonDict | None:
    candidates: list[JsonDict] = []
    for row in proposals.values():
        if not is_str_dict(row) or row.get("status") not in reusable_states:
            continue
        row_context = row.get("context")
        if (
            row.get("action_kind") != action_kind
            or not is_str_dict(row_context)
            or any(row_context.get(field) != value for field, value in context.items())
        ):
            continue
        row_params = row.get("normalized_parameters")
        if not is_str_dict(row_params):
            continue
        if any(
            row_params.get(field) != normalized_parameters[field]
            for field in shared_fields
        ):
            continue
        if not matches_review_basis(row.get("expected_state_fingerprint")):
            continue
        candidates.append(row)
    return max(
        candidates,
        key=lambda row: (
            str(row.get("updated_at") or ""),
            str(row.get("proposal_id") or ""),
        ),
        default=None,
    )


def replay_or_reuse_preview(
    store: ActionStoreBackend,
    *,
    action_kind: str,
    context: Mapping[str, object],
    normalized_parameters: Mapping[str, object],
    idempotency_key: str,
    shared_fields: tuple[str, ...],
    reusable_states: set[str],
    matches_review_basis: Callable[[object], bool] | None = None,
    require_exact_context: bool = False,
    summary: str | None = None,
) -> JsonDict | None:
    """Resolve an accepted request or bind it to an equivalent pending review."""
    if summary is not None:
        _safe_json_value(
            _bounded_text(summary, field="summary"), path="preview.summary"
        )
    key = normalize_idempotency_key(idempotency_key)
    request = safe_object_dict(
        {
            "action_kind": action_kind,
            "context": context,
            "normalized_parameters": normalized_parameters,
        },
        path="payload",
        label="preview",
    )
    digest = _canonical_digest(request)

    with store_lock(store.path, operation="reuse_chat_action_preview"):
        payload = store._read()
        proposals = store_proposals(payload)
        bindings = store_idempotency(payload)
        if key in bindings:
            binding = bindings[key]
            bound_id = binding.get("proposal_id") if is_str_dict(binding) else None
            row = proposals.get(bound_id) if isinstance(bound_id, str) else None
            if not is_str_dict(binding) or not is_str_dict(row):
                raise ValueError(
                    "typed Chat action idempotency index is inconsistent"
                )
            previous_digest = binding.get("reused_request_digest")
            if previous_digest is not None:
                same_request = previous_digest == digest
            else:
                same_request = row.get("normalized_parameters") == request.get(
                    "normalized_parameters"
                ) and (
                    not require_exact_context
                    or row.get("context") == request.get("context")
                )
            row_context = row.get("context")
            matches_scope = (
                row.get("action_kind") == action_kind
                and is_str_dict(row_context)
                and all(
                    row_context.get(field) == value for field, value in context.items()
                )
            )
            if not matches_scope or not same_request:
                raise ActionConflictError(
                    "idempotency key already belongs to another preview"
                )
            return row

        if not reusable_states or matches_review_basis is None:
            return None
        pending = _matching_pending_preview(
            proposals,
            action_kind=action_kind,
            context=context,
            normalized_parameters=normalized_parameters,
            shared_fields=shared_fields,
            reusable_states=reusable_states,
            matches_review_basis=matches_review_basis,
        )
        if pending is None:
            return None
        bindings[key] = {
            "proposal_id": pending["proposal_id"],
            "reused_request_digest": digest,
        }
        store._write(payload)
        return pending


def list_proposals(
    store: ActionStoreBackend,
    *,
    goal_id: str | None = None,
    context_kind: str | None = None,
    status: str | None = None,
) -> list[JsonDict]:
    """List valid stored proposals ordered newest-first."""
    selected_goal = _opaque_id(goal_id, field="goal_id") if goal_id else None
    selected_context = (
        _opaque_id(context_kind, field="context_kind") if context_kind else None
    )
    if status and status not in PROPOSAL_STATES:
        raise ValueError("status must be a supported typed action state")
    proposals: list[JsonDict] = []
    for raw in store_proposals(store._read()).values():
        if not is_str_dict(raw) or raw.get("status") not in PROPOSAL_STATES:
            continue
        raw_context = raw.get("context")
        context = raw_context if is_str_dict(raw_context) else {}
        raw_params = raw.get("normalized_parameters")
        parameters = raw_params if is_str_dict(raw_params) else {}
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


def cancel_proposal(store: ActionStoreBackend, proposal_id: str) -> JsonDict:
    """Cancel an awaiting/retryable proposal and any armed operation envelope."""
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="cancel_chat_action_preview"):
        payload = store._read()
        proposal = require_proposal(payload, token)
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
        now = current_utc_now()
        proposal["status"] = "cancelled"
        proposal["cancelled_at"] = now
        operation = proposal.get("operation")
        if is_str_dict(operation):
            outcome: JsonDict = {
                "schema_version": "loopx_operation_outcome_v0",
                "outcome": "cancelled_before_confirmation",
                "projection_verified": True,
                "observed_at": now,
            }
            operation["lifecycle_state"] = "outcome_observed"
            operation["outcome"] = outcome
            proposal["receipt"] = outcome
            proposal["gate"] = None
        return commit_proposal_mutation(store, payload, proposal, updated_at=now)


def save_proposal_checkpoint(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    step: str,
    receipt: Mapping[str, object],
) -> JsonDict:
    """Persist one completed step receipt inside an active/recoverable proposal."""
    safe_step = _opaque_id(step, field="step")
    safe_receipt = safe_object_dict(
        receipt, path="checkpoint.receipt", label="checkpoint receipt"
    )
    token = _opaque_id(proposal_id, field="proposal_id")
    with store_lock(store.path, operation="checkpoint_chat_action_preview"):
        payload = store._read()
        proposal = require_proposal(payload, token)
        if proposal.get("status") not in {"applying", "gated", "failed"}:
            raise ActionConflictError(
                f"proposal in {proposal.get('status')} state cannot save a checkpoint"
            )
        raw_checkpoint = proposal.get("checkpoint")
        checkpoint: JsonDict = (
            dict(raw_checkpoint) if is_str_dict(raw_checkpoint) else {"steps": {}}
        )
        raw_steps = checkpoint.get("steps")
        steps: JsonDict = dict(raw_steps) if is_str_dict(raw_steps) else {}
        steps[safe_step] = safe_receipt
        now = current_utc_now()
        checkpoint["steps"] = steps
        checkpoint["last_completed_step"] = safe_step
        checkpoint["updated_at"] = now
        proposal["checkpoint"] = checkpoint
        return commit_proposal_mutation(store, payload, proposal, updated_at=now)


def transition_proposal(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    from_states: set[str],
    to_state: str,
    updates: Mapping[str, object],
    idempotent_states: set[str],
) -> JsonDict:
    """Atomically move one proposal across validated lifecycle states."""
    token = _opaque_id(proposal_id, field="proposal_id")
    if to_state not in PROPOSAL_STATES:
        raise ValueError("unsupported typed action state")
    with store_lock(store.path, operation=f"transition_chat_action_{to_state}"):
        payload = store._read()
        proposal = require_proposal(payload, token)
        status = str(proposal.get("status") or "")
        if status in idempotent_states:
            return proposal
        if status not in from_states:
            raise ActionConflictError(
                f"proposal in {status} state cannot become {to_state}"
            )
        proposal["status"] = to_state
        proposal.update(
            safe_object_dict(updates, path="transition", label="transition")
        )
        return commit_proposal_mutation(store, payload, proposal)


def apply_proposal(
    store: ActionStoreBackend,
    proposal_id: str,
    *,
    current_state_fingerprint: str,
    receipt: Mapping[str, object],
) -> JsonDict:
    """Commit one applied receipt or mark the proposal stale on fingerprint drift."""
    token = _opaque_id(proposal_id, field="proposal_id")
    current_fingerprint = _bounded_text(
        current_state_fingerprint,
        field="current_state_fingerprint",
        limit=512,
    )
    with store_lock(store.path, operation="apply_chat_action_preview"):
        payload = store._read()
        proposal = require_proposal(payload, token)
        status = str(proposal.get("status") or "")
        if status == "applied":
            return proposal
        if status not in {"preview_ready", "applying"}:
            raise ActionConflictError(
                f"proposal in {status} state cannot be applied"
            )
        expected_fingerprint = str(proposal.get("expected_state_fingerprint") or "")
        now = current_utc_now()
        if current_fingerprint != expected_fingerprint:
            proposal["status"] = "stale"
            proposal["stale"] = {
                "expected_state_fingerprint": expected_fingerprint,
                "current_state_fingerprint": current_fingerprint,
                "detected_at": now,
            }
            return commit_proposal_mutation(store, payload, proposal, updated_at=now)

        safe_receipt = safe_object_dict(receipt, path="receipt", label="receipt")
        _opaque_id(safe_receipt.get("receipt_id"), field="receipt.receipt_id")
        _opaque_id(safe_receipt.get("outcome"), field="receipt.outcome")
        if safe_receipt.get("projection_verified") is not True:
            raise ValueError("receipt.projection_verified must be true")
        proposal["status"] = "applied"
        proposal["receipt"] = safe_receipt
        proposal["applied_at"] = now
        return commit_proposal_mutation(store, payload, proposal, updated_at=now)
