"""Use the existing Goal update proposal and local-owner apply surface.

Preparing is effect-free for Goal intent. The shared TS owner performs the
revision/CAS; Chat stores only the reviewed request and its recovery checkpoint.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ...chat_action_store import ChatActionStore, ActionConflictError, normalize_idempotency_key
from ...file_lock import exclusive_file_lock
from .acceptance import inspect_goal_acceptance, update_goal_objective, public_goal_acceptance, goal_acceptance_route, GoalAcceptanceRoute

if TYPE_CHECKING:
    from ...chat_actions import ChatActionService


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
    parameters = proposal["normalized_parameters"]
    if (proposal["action_kind"] != "goal.update"
        or proposal["context"].get("goal_id") != goal_id
        or parameters.get("goal_id") != goal_id
        or not {"objective", "reason", "source_ref"}.issubset(parameters)):
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
    # Serialize peer preparation; reuse one pending review for the same objective.
    with exclusive_file_lock(service.store.root / f"{goal_id}.objective-proposals", agent_id="loopx-chat", operation="prepare_objective"):
        rows = service.store.list(goal_id=goal_id, context_kind="goal")
        prior = next((row for row in rows if row["idempotency_key"] == operation_id), None)
        if prior is not None:
            if prior["action_kind"] != "goal.update" or prior["normalized_parameters"] != parameters:
                raise ActionConflictError("This operation belongs to another objective proposal")
            return {"ok": True, "prepared": True, "executed": False, "proposal": prior}
        for row in rows:
            if row["action_kind"] == "goal.update" and row["status"] in {"preview_ready", "deferred", "applying"} and row["normalized_parameters"].get("objective") == parameters["objective"]:
                return {"ok": True, "prepared": True, "executed": False, "proposal": row}
        proposal = service.preview({"action_kind": "goal.update", "summary": document["objective"],
            "idempotency_key": operation_id, "context": {"kind": "goal", "goal_id": goal_id}, "normalized_parameters": parameters})
        return {"ok": True, "prepared": True, "executed": False, "proposal": proposal}


class GoalObjectiveActionMixin:
    store: ChatActionStore
    registry_path: Path

    def _objective_route(self, goal_id: str) -> GoalAcceptanceRoute:
        return goal_acceptance_route(self.registry_path, goal_id, str(self.store.root.parent.parent))

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
            parameters = proposal["normalized_parameters"]
            basis = json.loads(proposal["expected_state_fingerprint"])
            document = {key: parameters[key] for key in ("objective", "reason", "source_ref")}
            # Retain exactly the native request before its first attempt. A
            # missing reply retries that operation, never creates another edit.
            steps = (proposal.get("checkpoint") or {}).get("steps", {})
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
            stored = self.store.apply(proposal_id, current_state_fingerprint=proposal["expected_state_fingerprint"], receipt=receipt)
            return {"proposal": stored, "turn": None, "current": public_goal_acceptance(current)}
