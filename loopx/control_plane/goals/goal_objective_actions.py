"""Use the existing Goal update proposal and local-owner apply surface.

Preparing is effect-free for Goal intent. The shared TS owner performs the
revision/CAS; Chat stores only the reviewed request and its recovery checkpoint.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ...agent_registry import load_goal_from_registry
from ...chat_action_store import ChatActionStore, normalize_idempotency_key
from ...file_lock import exclusive_cross_runtime_file_lock, exclusive_file_lock
from .acceptance import (
    GoalAcceptanceRoute,
    goal_acceptance_route,
    inspect_goal_acceptance,
    public_goal_acceptance,
    require_active_goal_for_objective_update,
    update_goal_objective,
)

if TYPE_CHECKING:
    from ...chat_actions import ChatActionService


def _reviewed_objective_digest(fingerprint: object) -> str | None:
    if not isinstance(fingerprint, str):
        return None
    try:
        basis = json.loads(fingerprint)
    except json.JSONDecodeError:
        return None
    digest = basis.get("objective_digest") if isinstance(basis, dict) else None
    return digest if isinstance(digest, str) else None


def _objective_service(registry_path: Path, runtime_root: str | None) -> ChatActionService:
    from ...chat_actions import ChatActionService
    from ...paths import effective_runtime_root

    root = effective_runtime_root(registry_path, runtime_root)
    return ChatActionService(store=ChatActionStore(root / "chat" / "actions"), registry_path=registry_path)


def objective_revision_proposal(*, registry_path: Path, runtime_root: str | None,
    goal_id: str, agent_id: str | None, proposal_id: str, execute: bool = False) -> dict[str, Any]:
    """CLI transport for the same reviewed request, owner apply and recovery as Chat."""
    service = _objective_service(registry_path, runtime_root)
    current = inspect_goal_acceptance(**service._objective_route(goal_id), agent_id=agent_id)
    proposal = service.load(proposal_id)
    if proposal is None:
        raise ValueError("Objective proposal was not found")
    raw_parameters = proposal["normalized_parameters"]
    raw_context = proposal["context"]
    if (proposal["action_kind"] != "goal.update"
        or not isinstance(raw_context, Mapping)
        or not isinstance(raw_parameters, Mapping)
        or raw_context.get("goal_id") != goal_id
        or raw_parameters.get("goal_id") != goal_id
        or not {"objective", "reason", "source_ref"}.issubset(raw_parameters)):
        raise ValueError("Proposal is not a current-objective revision for this Goal")
    if not execute:
        return {"ok": True, "executed": False, "proposal": proposal,
            "current": public_goal_acceptance(current)}
    if agent_id is not None:
        raise ValueError("Shared objective revision requires the local Goal owner; an Agent may prepare or inspect the proposal, not apply it")
    result = service.apply(proposal_id)
    # Replays may return only a historical receipt. Always read today's basis;
    # never reinstall that historical objective or imply another peer adopted it.
    current = inspect_goal_acceptance(**service._objective_route(goal_id))
    applied = result["proposal"]["status"] == "applied"
    return {**result, "ok": applied, "executed": applied,
        "current": public_goal_acceptance(current)}


def prepare_objective_revision(*, registry_path: Path, runtime_root: str | None,
    goal_id: str, agent_id: str | None, document: dict[str, Any], operation_id: str,
    execute: bool) -> dict[str, Any]:
    service = _objective_service(registry_path, runtime_root)
    if not isinstance(document, dict) or set(document) != {"objective", "reason", "source_ref"}:
        raise ValueError("objective proposal needs objective, reason and an opaque source_ref")
    parameters = service._normalize("goal.update", {"goal_id": goal_id, **document, "prepared_by": agent_id})
    operation_id = normalize_idempotency_key(operation_id)
    inspect_goal_acceptance(**service._objective_route(goal_id), agent_id=agent_id)
    if not execute:
        _, preview = service._objective_preview(parameters)
        return {**public_goal_acceptance(preview), "prepared": False, "executed": False}
    proposal = service._prepare_objective_preview(
        parameters=parameters,
        context={"kind": "goal", "goal_id": goal_id},
        summary=document["objective"],
        idempotency_key=operation_id,
        reuse_pending=True,
    )
    return {"ok": True, "prepared": True, "executed": False, "proposal": proposal}


class GoalObjectiveActionMixin:
    store: ChatActionStore
    registry_path: Path

    def _objective_route(self, goal_id: str) -> GoalAcceptanceRoute:
        return goal_acceptance_route(self.registry_path, goal_id, str(self.store.root.parent.parent))

    def _prepare_objective_preview(
        self, *, parameters: dict[str, Any], context: Mapping[str, Any],
        summary: Any, idempotency_key: str, reuse_pending: bool = False,
    ) -> dict[str, Any]:
        from ...chat_actions import _text

        goal_id = parameters["goal_id"]
        if context.get("kind") != "goal" or context.get("goal_id") != goal_id:
            raise ValueError("objective proposal context must match its Goal")

        preview_summary = None if reuse_pending else _text(summary, field="summary", limit=600)
        def resolve_saved(
            reusable_states: set[str], matches_review_basis: Callable[[object], bool] | None = None,
        ) -> dict[str, Any] | None:
            return self.store.replay_or_reuse_preview(
                action_kind="goal.update",
                context=context,
                normalized_parameters=parameters,
                idempotency_key=idempotency_key,
                shared_fields=("objective", "reason", "source_ref"),
                reusable_states=reusable_states,
                matches_review_basis=matches_review_basis,
                require_exact_context=not reuse_pending,
                summary=preview_summary,
            )

        with exclusive_file_lock(
            self.store.root / f"{goal_id}.objective-proposals",
            agent_id="loopx-chat",
            operation="prepare_objective",
        ):
            # An already accepted request remains readable after Stop.
            existing = resolve_saved(set())
            if existing is not None:
                return existing

            # Serialize new proposals and bindings with Stop on the source
            # registry, and keep the same source-before-projection lock order.
            route = self._objective_route(goal_id)
            source_registry = route["registry_path"]
            registries = (source_registry, self.registry_path.resolve())
            with ExitStack() as locks:
                for registry in dict.fromkeys(registries):
                    locks.enter_context(exclusive_cross_runtime_file_lock(
                        registry, operation="prepare_goal_objective",
                    ))
                if self._objective_route(goal_id) != route:
                    raise ValueError("The reviewed objective's Goal source changed")
                require_active_goal_for_objective_update(load_goal_from_registry(source_registry, goal_id))
                fingerprint, _ = self._objective_preview(parameters)
                if reuse_pending:
                    # The typed preview digest already includes the objective
                    # lineage. Unrelated provider revisions retain that basis.
                    digest = _reviewed_objective_digest(fingerprint)
                    reused = resolve_saved(
                        {"preview_ready", "deferred", "applying"},
                        lambda saved: digest is not None and _reviewed_objective_digest(saved) == digest,
                    )
                    if reused is not None:
                        return reused

                return self.store.create_preview(
                    action_kind="goal.update",
                    summary=preview_summary or _text(summary, field="summary", limit=600),
                    normalized_parameters=parameters,
                    context=context,
                    expected_state_fingerprint=fingerprint,
                    permission_classification="durable_write",
                    validation_evidence=[
                        "The typed Goal owner validated this current-objective revision. Root commitments, permissions and existing acceptance remain unchanged; peers must adopt the new revision before new execution."
                    ],
                    available_transitions=["apply", "cancel"],
                    idempotency_key=idempotency_key,
                )

    def _objective_preview(self, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        route = self._objective_route(parameters["goal_id"])
        before = inspect_goal_acceptance(**route)
        document = {key: parameters[key] for key in ("objective", "reason", "source_ref")}
        preview = update_goal_objective(**route, operation="revise-objective", document=document,
            expected_provider_revision=before["provider_revision"], operation_id=f"objective-preview:{uuid4().hex}")
        objective = preview["goal_acceptance_contract"]["goal_objective"]
        return json.dumps({"provider_revision": before["provider_revision"], "objective_digest": objective["digest"]}, sort_keys=True), preview

    def _apply_objective_revision(self, proposal_id: str) -> dict[str, Any]:
        with exclusive_file_lock(self.store.root / f"{proposal_id}.objective", agent_id="loopx-chat", operation="objective_revision"):
            proposal = self.store.load(proposal_id)
            if proposal is None:
                raise KeyError("Objective proposal is unavailable")
            if proposal["status"] == "applied":
                return {"proposal": proposal, "turn": None}
            raw_parameters = proposal["normalized_parameters"]
            if not isinstance(raw_parameters, Mapping):
                raise ValueError("Objective proposal parameters are malformed")
            parameters = dict(raw_parameters)
            expected_fingerprint = str(proposal["expected_state_fingerprint"])
            basis = json.loads(expected_fingerprint)
            document = {key: parameters[key] for key in ("objective", "reason", "source_ref")}
            # Retain exactly the native request before its first attempt. A
            # missing reply retries that operation, never creates another edit.
            raw_checkpoint = proposal.get("checkpoint")
            raw_steps = (
                raw_checkpoint.get("steps", {})
                if isinstance(raw_checkpoint, Mapping)
                else {}
            )
            steps = raw_steps if isinstance(raw_steps, Mapping) else {}
            if not steps.get("objective_started"):
                fingerprint, _ = self._objective_preview(parameters)
                if json.loads(fingerprint)["objective_digest"] != basis["objective_digest"]:
                    return {"proposal": self.store.apply(proposal_id, current_state_fingerprint=fingerprint, receipt={}), "turn": None}
                self.store.save_checkpoint(proposal_id, step="objective_started", receipt={"operation_id": f"objective:{proposal_id}"})
            saved = update_goal_objective(**self._objective_route(parameters["goal_id"]), operation="revise-objective",
                document=document, expected_provider_revision=basis["provider_revision"], operation_id=f"objective:{proposal_id}", execute=True, review_objective_digest=basis["objective_digest"])
            # Historical replay is distinct from the current readback delivered
            # to peers; it cannot re-install an earlier revision.
            current = inspect_goal_acceptance(**self._objective_route(parameters["goal_id"]))
            receipt = {"receipt_id": f"objective-{proposal_id}", "outcome": "objective_revised", "projection_verified": True,
                "goal_id": parameters["goal_id"], "operation_id": saved["operation_id"]}
            stored = self.store.apply(proposal_id, current_state_fingerprint=expected_fingerprint, receipt=receipt)
            return {"proposal": stored, "turn": None, "current": public_goal_acceptance(current)}
