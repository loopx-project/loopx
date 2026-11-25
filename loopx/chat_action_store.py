"""Owner-local persistence for typed LoopX Chat action proposals."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
import os
from pathlib import Path
import uuid

from . import chat_action_store_common as _common
from . import chat_action_store_operations as _operations
from . import chat_action_store_review_cards as _review_cards
from .chat_action_store_common import (
    AUTHENTICATED_LARK_CARD_CALLBACK,
    CHAT_ACTION_PROPOSAL_SCHEMA_VERSION,
    CHAT_ACTION_STORE_SCHEMA_VERSION,
    HUMAN_ACTOR_CLASS,
    OPERATION_ENVELOPE_SCHEMA_VERSION,
    OPERATION_LIFECYCLE_STATES,
    PROPOSAL_STATES,
    RETRYABLE_PROPOSAL_STATES,
    ActionConflictError,
    JsonDict,
    JsonPrimitive,
    JsonValue,
    ObjectDict,
    _bounded_text,
    _canonical_digest,
    _opaque_id,
    _safe_json_value,
    _utc_now,
    is_str_dict,
    normalize_idempotency_key,
    safe_object_dict,
    store_idempotency,
    store_proposals,
)
from .file_lock import exclusive_file_lock
from .public_safe_text import OPAQUE_ID_PATTERN as _OPAQUE_ID
from .registry import atomic_write_json

ACTION_KINDS = {
    "goal.acceptance.check",
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
    "pr.merge",
    "team.plan",
}

__all__ = [
    "ACTION_KINDS",
    "AUTHENTICATED_LARK_CARD_CALLBACK",
    "ActionConflictError",
    "CHAT_ACTION_PROPOSAL_SCHEMA_VERSION",
    "CHAT_ACTION_STORE_SCHEMA_VERSION",
    "ChatActionStore",
    "HUMAN_ACTOR_CLASS",
    "JsonDict",
    "JsonPrimitive",
    "JsonValue",
    "OPERATION_ENVELOPE_SCHEMA_VERSION",
    "OPERATION_LIFECYCLE_STATES",
    "ObjectDict",
    "PROPOSAL_STATES",
    "RETRYABLE_PROPOSAL_STATES",
    "_OPAQUE_ID",
    "_bounded_text",
    "_canonical_digest",
    "_opaque_id",
    "_safe_json_value",
    "_utc_now",
    "atomic_write_json",
    "exclusive_file_lock",
    "normalize_idempotency_key",
]


class ChatActionStore:
    """Persist typed action previews and receipts in an explicit local root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "actions.json"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        if not self.path.exists():
            with exclusive_file_lock(
                self.path,
                agent_id="loopx-chat",
                operation="initialize_chat_action_store",
            ):
                if not self.path.exists():
                    atomic_write_json(self.path, self._empty_payload())
        os.chmod(self.path, 0o600)

    @staticmethod
    def _empty_payload() -> JsonDict:
        return {
            "schema_version": CHAT_ACTION_STORE_SCHEMA_VERSION,
            "proposals": {},
            "idempotency": {},
        }

    def _read(self) -> JsonDict:
        try:
            payload: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("typed Chat action store is unreadable") from exc
        if (
            not is_str_dict(payload)
            or payload.get("schema_version") != CHAT_ACTION_STORE_SCHEMA_VERSION
        ):
            raise ValueError("typed Chat action store has an unsupported schema")
        if not is_str_dict(payload.get("proposals")) or not is_str_dict(
            payload.get("idempotency")
        ):
            raise ValueError("typed Chat action store is malformed")
        return payload

    def _write(self, payload: JsonDict) -> None:
        atomic_write_json(self.path, payload, preserve_mode=True)
        os.chmod(self.path, 0o600)

    def create_preview(
        self,
        *,
        action_kind: str,
        summary: str,
        normalized_parameters: Mapping[str, object],
        context: Mapping[str, object],
        expected_state_fingerprint: str,
        permission_classification: str,
        validation_evidence: Sequence[object],
        available_transitions: Sequence[object],
        idempotency_key: str,
        canonical_update_basis: Mapping[str, object] | None = None,
        workspace_binding: Mapping[str, object] | None = None,
    ) -> JsonDict:
        selected_kind = str(action_kind or "").strip()
        if selected_kind not in ACTION_KINDS:
            raise ValueError(f"unsupported action_kind: {selected_kind or '<empty>'}")
        request_input: JsonDict = {
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
        }
        if canonical_update_basis is not None:
            request_input["canonical_update_basis"] = dict(canonical_update_basis)
        if workspace_binding is not None:
            request_input["workspace_binding"] = dict(workspace_binding)
        request = safe_object_dict(request_input, path="preview", label="preview")
        key = normalize_idempotency_key(idempotency_key)
        request_digest = _canonical_digest(request)
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="create_chat_action_preview",
        ):
            payload = self._read()
            proposals = store_proposals(payload)
            idempotency = store_idempotency(payload)
            existing_binding = idempotency.get(key)
            if is_str_dict(existing_binding):
                proposal_id = str(existing_binding.get("proposal_id") or "")
                if existing_binding.get("request_digest") != request_digest:
                    raise ActionConflictError(
                        "idempotency key already belongs to another preview"
                    )
                existing = proposals.get(proposal_id)
                if not is_str_dict(existing):
                    raise ValueError(
                        "typed Chat action idempotency index is inconsistent"
                    )
                return existing

            now = _utc_now()
            proposal_id = f"proposal-{uuid.uuid4().hex}"
            proposal: JsonDict = {
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
            proposals[proposal_id] = proposal
            idempotency[key] = {
                "proposal_id": proposal_id,
                "request_digest": request_digest,
            }
            self._write(payload)
            return proposal

    def replay_or_reuse_preview(
        self,
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
        return _common.replay_or_reuse_preview(
            self,
            action_kind=action_kind,
            context=context,
            normalized_parameters=normalized_parameters,
            idempotency_key=idempotency_key,
            shared_fields=shared_fields,
            reusable_states=reusable_states,
            matches_review_basis=matches_review_basis,
            require_exact_context=require_exact_context,
            summary=summary,
        )

    def record_review_card_delivery(
        self,
        proposal_id: str,
        *,
        audience_id: str,
        delivery: Mapping[str, object],
    ) -> JsonDict:
        """Bind one verified external review surface to a typed proposal."""
        return _review_cards.record_review_card_delivery(
            self, proposal_id, audience_id=audience_id, delivery=delivery
        )

    def prepare_review_card_delivery(
        self,
        proposal_id: str,
        *,
        audience_ids: Sequence[str],
        authorized_principal: str,
    ) -> JsonDict:
        """Freeze every audience before the first actionable card is sent."""
        return _review_cards.prepare_review_card_delivery(
            self,
            proposal_id,
            audience_ids=audience_ids,
            authorized_principal=authorized_principal,
        )

    def decide_review_card(
        self,
        proposal_id: str,
        *,
        decision: str,
        confirmation: Mapping[str, object],
    ) -> JsonDict:
        """Consume one verified card decision across every delivered audience."""
        return _review_cards.decide_review_card(
            self, proposal_id, decision=decision, confirmation=confirmation
        )

    def record_review_card_result_delivery(
        self,
        proposal_id: str,
        *,
        audience_id: str,
        result: Mapping[str, object],
    ) -> JsonDict:
        """Record exact result-card readback for one already-bound audience."""
        return _review_cards.record_review_card_result_delivery(
            self, proposal_id, audience_id=audience_id, result=result
        )

    def arm_operation(self, proposal_id: str) -> JsonDict:
        """Turn one provider-neutral preview into the canonical confirmation gate."""
        return _operations.arm_operation(self, proposal_id)

    def record_operation_delivery(
        self, proposal_id: str, *, delivery: Mapping[str, object]
    ) -> JsonDict:
        """Bind the exact provider message which may attest a later click."""
        return _operations.record_operation_delivery(
            self, proposal_id, delivery=delivery
        )

    def record_operation_delivery_snapshot(
        self, proposal_id: str, *, submitted_card: Mapping[str, object]
    ) -> JsonDict:
        """Persist the immutable sent card after verifying its recorded digest."""
        return _operations.record_operation_delivery_snapshot(
            self, proposal_id, submitted_card=submitted_card
        )

    def decide_operation(
        self,
        proposal_id: str,
        *,
        decision: str,
        confirmation: Mapping[str, object],
    ) -> JsonDict:
        """Atomically reject, or confirm and claim, one delivered operation."""
        return _operations.decide_operation(
            self, proposal_id, decision=decision, confirmation=confirmation
        )

    def observe_operation_outcome(
        self,
        proposal_id: str,
        *,
        outcome: Mapping[str, object],
        agent_actor: Mapping[str, object] | None = None,
        agent_binding_current: bool = False,
        agent_actor_binding_current: bool = False,
    ) -> JsonDict:
        """Persist the domain result without making it a retryable submission."""
        return _operations.observe_operation_outcome(
            self,
            proposal_id,
            outcome=outcome,
            agent_actor=agent_actor,
            agent_binding_current=agent_binding_current,
            agent_actor_binding_current=agent_actor_binding_current,
        )

    @staticmethod
    def _agent_operation_plan(
        proposal: Mapping[str, object], **request: object
    ) -> JsonDict:
        """One typed decision, using the existing canonical JSON byte format."""
        return _operations.agent_operation_plan(proposal, **request)

    def reserve_agent_operation_turn(
        self, proposal_id: str, **launch_context: object
    ) -> JsonDict:
        """Internal callback launch intent."""
        return _operations.reserve_agent_operation_turn(
            self, proposal_id, **launch_context
        )

    def record_agent_operation_host_start(
        self,
        proposal_id: str,
        *,
        actor: Mapping[str, object],
        binding_current: bool,
        turn_key: str,
    ) -> JsonDict:
        """Internal native transport observation; never a caller proof or permit."""
        return _operations.record_agent_operation_host_start(
            self,
            proposal_id,
            actor=actor,
            binding_current=binding_current,
            turn_key=turn_key,
        )

    def consume_agent_operation(
        self,
        proposal_id: str,
        *,
        actor: Mapping[str, object],
        binding_current: bool,
        consumption_id: str,
    ) -> JsonDict:
        """Commit before external execution. Even an exact replay grants no retry."""
        return _operations.consume_agent_operation(
            self,
            proposal_id,
            actor=actor,
            binding_current=binding_current,
            consumption_id=consumption_id,
        )

    def record_operation_result_delivery(
        self, proposal_id: str, *, delivery: Mapping[str, object]
    ) -> JsonDict:
        """Persist exact result-card readback without changing domain outcome."""
        return _operations.record_operation_result_delivery(
            self, proposal_id, delivery=delivery
        )

    def load(self, proposal_id: str) -> JsonDict | None:
        token = _opaque_id(proposal_id, field="proposal_id")
        proposal = store_proposals(self._read()).get(token)
        if proposal is None:
            return None
        if not is_str_dict(proposal) or proposal.get("status") not in PROPOSAL_STATES:
            raise ValueError("typed Chat action proposal is malformed")
        return proposal

    def list(
        self,
        *,
        goal_id: str | None = None,
        context_kind: str | None = None,
        status: str | None = None,
    ) -> list[JsonDict]:
        return _common.list_proposals(
            self, goal_id=goal_id, context_kind=context_kind, status=status
        )

    def cancel(self, proposal_id: str) -> JsonDict:
        return _common.cancel_proposal(self, proposal_id)

    def start_apply(self, proposal_id: str) -> JsonDict:
        """Persist the execution boundary before any canonical write is attempted."""
        return self._transition(
            proposal_id,
            from_states=RETRYABLE_PROPOSAL_STATES,
            to_state="applying",
            updates={"gate": None, "failure": None},
            idempotent_states={"applying", "applied"},
        )

    def mark_gated(
        self, proposal_id: str, *, gate: Mapping[str, object]
    ) -> JsonDict:
        safe_gate = safe_object_dict(gate, path="gate", label="gate")
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
        details: Mapping[str, object] | None = None,
    ) -> JsonDict:
        # A failure that happened after part of the work was committed has to
        # carry those identities, or the retry cannot tell what already exists
        # and the owner has to reconstruct it from the message.
        record: JsonDict = {
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
        receipt: Mapping[str, object],
    ) -> JsonDict:
        return _common.save_proposal_checkpoint(
            self, proposal_id, step=step, receipt=receipt
        )

    def mark_rejected(self, proposal_id: str) -> JsonDict:
        now = _utc_now()
        return self._transition(
            proposal_id,
            from_states={"preview_ready", "gated", "deferred"},
            to_state="rejected",
            updates={"rejected_at": now},
            idempotent_states={"rejected"},
        )

    def mark_deferred(self, proposal_id: str) -> JsonDict:
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
    ) -> JsonDict:
        token = _opaque_id(proposal_id, field="proposal_id")
        source = _opaque_id(regenerated_from, field="regenerated_from")
        with exclusive_file_lock(
            self.path,
            agent_id="loopx-chat",
            operation="link_chat_action_regeneration",
        ):
            payload = self._read()
            proposal = _common.require_proposal(payload, token)
            proposal["regenerated_from"] = source
            return _common.commit_proposal_mutation(self, payload, proposal)

    def _transition(
        self,
        proposal_id: str,
        *,
        from_states: set[str],
        to_state: str,
        updates: Mapping[str, object],
        idempotent_states: set[str],
    ) -> JsonDict:
        return _common.transition_proposal(
            self,
            proposal_id,
            from_states=from_states,
            to_state=to_state,
            updates=updates,
            idempotent_states=idempotent_states,
        )

    def apply(
        self,
        proposal_id: str,
        *,
        current_state_fingerprint: str,
        receipt: Mapping[str, object],
    ) -> JsonDict:
        return _common.apply_proposal(
            self,
            proposal_id,
            current_state_fingerprint=current_state_fingerprint,
            receipt=receipt,
        )
