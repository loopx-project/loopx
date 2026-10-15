"""Local Chat-action transport for an Agent-prepared acceptance check.

Reuse ChatActionStore's proposal/checkpoint owner and the TS acceptance owner.
The per-proposal host lock and started checkpoint fence command execution;
preparation only validates a document and cannot configure or run it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

from ...chat_action_store import ActionConflictError, ChatActionStore
from ...file_lock import exclusive_file_lock
from .acceptance import (
    configure_goal_acceptance, inspect_goal_acceptance, public_goal_acceptance,
    verify_goal_acceptance,
)


class _AcceptanceRoute(TypedDict):
    registry_path: Path
    runtime_root: str
    goal_id: str


def prepare_acceptance_check(*, registry_path: Path, runtime_root: str | None,
                             goal_id: str, agent_id: str | None,
                             document: dict[str, Any], operation_id: str,
                             execute: bool) -> dict[str, Any]:
    from ...chat_actions import ChatActionService
    from ...paths import effective_runtime_root

    root = effective_runtime_root(registry_path, runtime_root)
    if not execute:
        route: _AcceptanceRoute = {
            "registry_path": registry_path, "runtime_root": str(root), "goal_id": goal_id,
        }
        before = inspect_goal_acceptance(**route, agent_id=agent_id)
        preview = configure_goal_acceptance(**route, document=document,
            expected_provider_revision=before["provider_revision"],
            operation_id=f"acceptance-preview:{uuid4().hex}", execute=False)
        return {**public_goal_acceptance(preview), "prepared": False, "executed": False}
    service = ChatActionService(store=ChatActionStore(root / "chat" / "actions"),
                                registry_path=registry_path)
    parameters = service._normalize("goal.acceptance.check", {
        "goal_id": goal_id, "document": document, "prepared_by": agent_id})
    proposal = service.preview({"action_kind": "goal.acceptance.check",
        "summary": str(document.get("objective") or "Check the delivery"),
        "idempotency_key": operation_id, "context": {"kind": "goal", "goal_id": goal_id},
        "normalized_parameters": parameters})
    return {"ok": True, "goal_id": goal_id, "prepared": True, "executed": False,
            "proposal": proposal}


class GoalAcceptanceActionMixin:
    store: ChatActionStore
    registry_path: Path

    def _acceptance_route(self, goal_id: str) -> _AcceptanceRoute:
        # ChatHTTPServer and the prepare CLI both use runtime/chat/actions.
        return {"registry_path": self.registry_path, "goal_id": goal_id,
                "runtime_root": str(self.store.root.parent.parent)}

    def _acceptance_preview(self, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        route = self._acceptance_route(parameters["goal_id"])
        before = inspect_goal_acceptance(**route)
        preview = configure_goal_acceptance(
            **route, document=parameters["document"],
            expected_provider_revision=before["provider_revision"],
            operation_id=f"acceptance-preview:{uuid4().hex}", execute=False,
        )
        if preview.get("status") != "planned":
            raise ValueError("The current Goal cannot prepare this check plan")
        return before["provider_revision"], preview

    def _apply_acceptance_check(self, proposal_id: str) -> dict[str, Any]:
        # The existing generic start_apply is replayable. Serialize this host
        # command boundary so concurrent tabs/processes consume one intent.
        with exclusive_file_lock(self.store.root / f"{proposal_id}.check",
                                 agent_id="loopx-chat", operation="acceptance_check"):
            proposal = self.store.load(proposal_id)
            if proposal is None:
                raise KeyError("Check plan was not found")
            if proposal["status"] == "applied":
                return {"proposal": proposal, "turn": None}
            parameters = proposal["normalized_parameters"]
            route = self._acceptance_route(parameters["goal_id"])
            steps = (proposal.get("checkpoint") or {}).get("steps", {})
            configured = steps.get("acceptance_configured")
            operation_id = f"acceptance-check:{proposal_id}"
            if configured is None:
                expected = proposal["expected_state_fingerprint"]
                try:
                    # Its native idempotency receipt also recovers a lost
                    # configuration response before the Chat checkpoint.
                    saved = configure_goal_acceptance(
                        **route, document=parameters["document"],
                        expected_provider_revision=expected,
                        operation_id=f"acceptance-configure:{proposal_id}", execute=True,
                    )
                except ValueError:
                    current = inspect_goal_acceptance(**route)
                    if current["provider_revision"] != expected:
                        stored = self.store.apply(proposal_id,
                            current_state_fingerprint=current["provider_revision"], receipt={})
                        return {"proposal": stored, "turn": None}
                    raise
                configured = {"provider_revision": saved["provider_revision"],
                    "digest": saved["goal_acceptance_contract"]["digest"]}
                self.store.save_checkpoint(proposal_id, step="acceptance_configured", receipt=configured)
            current = inspect_goal_acceptance(**route)
            contract = current["goal_acceptance_contract"]
            observed = contract.get("verification") or {}
            if steps.get("acceptance_check_started"):
                # A native committed receipt may recover a lost HTTP result.
                # Missing receipt is uncertainty, never permission to rerun.
                if observed.get("operation_id") != operation_id:
                    raise ActionConflictError("The previous check result is unconfirmed. Read current evidence before explicitly running another check.")
                result = {**public_goal_acceptance(current), "executed": True,
                          "checks_passed": all(row["passed"] for row in observed["results"])}
            else:
                if (current["provider_revision"] != configured["provider_revision"]
                        or contract.get("digest") != configured["digest"]):
                    raise ActionConflictError("The check basis changed. Prepare a current plan before executing.")
                self.store.save_checkpoint(proposal_id, step="acceptance_check_started",
                                           receipt={"operation_id": operation_id})
                result = verify_goal_acceptance(**route, execute=True,
                    expected_provider_revision=configured["provider_revision"], operation_id=operation_id)
            receipt = {"receipt_id": f"acceptance-{proposal_id}", "outcome": "acceptance_checked",
                "projection_verified": True, "goal_id": parameters["goal_id"],
                "operation_id": operation_id, "checks_passed": result["checks_passed"]}
            stored = self.store.apply(proposal_id,
                current_state_fingerprint=proposal["expected_state_fingerprint"], receipt=receipt)
            return {"proposal": stored, "turn": None}
