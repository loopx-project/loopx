"""Typed Chat actions for canonical Todo lifecycle transitions."""

from __future__ import annotations

from typing import Any

from .todos import complete_goal_todo, update_goal_todo
from .control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable, read_canonical_todos_if_promoted,
)
from .control_plane.coordination.local_authority_shadow_adapter import effective_runtime_root
from .control_plane.todos.provider_projection import projection_delivery_requires_ack


def _decision_readback(result: dict[str, Any]) -> dict[str, Any]:
    """Carry the canonical outcome and dependent effect, never a local guess."""
    resume = result.get("unblock_resume")
    return {
        "decision_outcome": result.get("decision_outcome"),
        "unblock_resume_state": resume.get("state") if isinstance(resume, dict) else None,
    }


class ChatTodoActionMixin:
    """Keep Todo preview/apply parity separate from general orchestration."""

    def _run_todo_update(
        self, parameters: dict[str, Any], *, dry_run: bool,
        basis: dict[str, Any] | None = None, operation_id: str | None = None,
    ) -> dict[str, Any]:
        goal_id = str(parameters["goal_id"])
        operation = str(parameters.get("operation") or "edit")
        if operation == "complete" and (basis is None or
                basis.get("schema_version") == "loopx_chat_canonical_terminal_basis_v0"):
            return complete_goal_todo(
                registry_path=self.registry_path,
                goal_id=goal_id,
                todo_id=str(parameters["todo_id"]),
                note=parameters.get("note"),
                no_followup=bool(parameters.get("no_followup", True)),
                successor_todo_ids=parameters.get("successor_todo_ids"),
                agent_id=parameters.get("agent_id"),
                authority_reason="owner-confirmed typed Chat action",
                dry_run=dry_run,
                **self._reviewed_terminal_options(basis, operation_id),
            )
        status = parameters.get("status")
        if operation == "complete":
            status = "done"
        elif operation == "block":
            status = "blocked"
        elif operation == "defer":
            status = "deferred"
        return update_goal_todo(
            registry_path=self.registry_path,
            goal_id=goal_id,
            todo_id=str(parameters["todo_id"]),
            text=parameters.get("text"),
            priority=parameters.get("priority"),
            clear_priority=(parameters.get("clear_priority", False) or
                            ("priority" in parameters and parameters["priority"] is None)),
            status=status,
            **({"role": "user", "no_followup": bool(parameters.get("no_followup", True))}
               if operation == "complete" else {}),
            # A reviewed block is a lifecycle transition, not a copy edit.
            # Its UI/Lark note supplies the public reason; a combined note
            # patch would cross the hard-lease execution fence.
            note=(parameters.get("note") if operation != "block" or basis is None else None),
            reason=(parameters.get("note") if operation == "block" and basis is not None else None),
            claimed_by=(
                parameters.get("agent_id") if operation == "reassign" else None
            ),
            resume_when=parameters.get("resume_when"),
            clear_resume_when=operation == "block" and basis is not None,
            successor_todo_ids=parameters.get("successor_todo_ids"),
            agent_id=parameters.get("agent_id"),
            authority_reason="owner-confirmed typed Chat action",
            dry_run=dry_run,
            **self._reviewed_update_options(basis, operation_id),
        )

    def _run_gate_resolve(
        self, parameters: dict[str, Any], *, dry_run: bool,
        basis: dict[str, Any] | None = None, operation_id: str | None = None,
    ) -> dict[str, Any]:
        # The typed User completion owner decides whether this source may carry
        # the outcome and what it does to the dependent; the App only records it.
        return complete_goal_todo(
            registry_path=self.registry_path,
            goal_id=str(parameters["goal_id"]),
            todo_id=str(parameters["todo_id"]),
            role="user",
            decision_outcome=str(parameters["decision"]),
            evidence=parameters.get("note"),
            no_followup=True,
            agent_id=parameters.get("agent_id"),
            authority_reason="owner-confirmed typed Chat decision",
            dry_run=dry_run,
            **self._reviewed_terminal_options(basis, operation_id),
        )

    @staticmethod
    def _reviewed_terminal_options(
        basis: dict[str, Any] | None, operation_id: str | None,
    ) -> dict[str, Any]:
        if basis is None:
            return {}
        if basis.get("schema_version") != "loopx_chat_canonical_terminal_basis_v0":
            raise ValueError("unsupported canonical terminal review basis; regenerate preview")
        return {"completion_turn_key": operation_id, "terminal_review_basis": {
            "provider_revision": basis["provider_revision"], "registry_sha256": basis["registry_sha256"],
        }}

    @staticmethod
    def _reviewed_update_options(
        basis: dict[str, Any] | None, operation_id: str | None,
    ) -> dict[str, Any]:
        if basis is None:
            return {}
        if basis.get("schema_version") != "loopx_chat_canonical_update_basis_v0":
            raise ValueError("unsupported canonical update review basis; regenerate preview")
        return {
            "update_operation_id": operation_id,
            "update_expected_provider_revision": basis["provider_revision"],
            "update_expected_registry_sha256": basis["registry_sha256"],
        }

    def _canonical_update_basis(
        self, goal_id: str, *, completion_todo_id: str | None = None,
        decision: bool = False,
    ) -> dict[str, Any] | None:
        registry_sha256 = self._registry_fingerprint()
        authority = read_canonical_todos_if_promoted(
            runtime_root=effective_runtime_root(self.registry_path, None),
            goal_id=goal_id, include_leases=True,
        )
        if self._registry_fingerprint() != registry_sha256:
            raise ValueError("Todo authority registration changed while reading; retry")
        if authority is None:
            return None
        # User updates retain their combined edit/completion contract. Agent
        # completion, Monitor stop and a User decision outcome bind the
        # dedicated terminal transaction, the only owner of decision_outcome.
        terminal = decision or completion_todo_id is not None and not any(
            todo.get("todo_id") == completion_todo_id and todo.get("role") == "user"
            for todo in authority["todos"])
        return {
            "schema_version": ("loopx_chat_canonical_terminal_basis_v0" if terminal else
                               "loopx_chat_canonical_update_basis_v0"),
            "provider_revision": authority["provider_revision"],
            "source_authority": authority["source_authority"],
            "registry_sha256": registry_sha256,
        }

    def _apply_reviewed_todo_edit(
        self, proposal_id: str, proposal: dict[str, Any], parameters: dict[str, Any],
    ) -> dict[str, Any]:
        """Recover the canonical operation before testing present-day freshness.

        CAS admits a new edit; the immutable receipt proves a previous edit.
        Both paths drain the current projection, never an old receipt's state.
        """
        from .chat_actions import _digest, _opaque

        operation_id = f"chat-update:{proposal_id}"
        basis = proposal["canonical_update_basis"]
        action_kind = proposal["action_kind"]
        run = {"monitor.update": self._run_monitor_update,
               "gate.resolve": self._run_gate_resolve}.get(action_kind, self._run_todo_update)
        terminal = action_kind == "gate.resolve" or parameters.get("operation") in {"complete", "stop"}
        try:
            result = run(parameters, dry_run=False, basis=basis, operation_id=operation_id)
        except LocalCoordinationAuthorityUnavailable as error:
            if (terminal and error.code == "authority_source_changed"
                    and error.payload.get("completion_validation_executed") is True):
                self.store.mark_failed(
                    proposal_id, error_code="canonical_update_validation_source_changed",
                    message="Todo authority registration changed during completion validation; retry",
                    details={"operation_id": operation_id, "reason_code": error.code},
                )
                raise ValueError(
                    "Todo authority registration changed during completion validation; retry"
                ) from error
            if error.code in {"provider_revision_mismatch", "authority_source_changed", "provider_revision_conflict"}:
                # The native transaction first established that no matching
                # historical receipt exists. This is a rejected new edit.
                stale = self.store.apply(proposal_id, current_state_fingerprint=_digest({
                    "current": self._goal_state_fingerprint(str(parameters["goal_id"])),
                    "conflict": error.code,
                }), receipt={})
                return {"proposal": stale, "turn": None}
            failed = self.store.mark_failed(
                proposal_id, error_code="canonical_update_retry_required",
                message="The edit is not yet verified. Retry this proposal to recover the original operation.",
                details={"operation_id": operation_id, "reason_code": error.code},
            )
            return {"proposal": failed, "turn": None}
        if result.get("ok") is not True:
            failed = self.store.mark_failed(proposal_id, error_code="canonical_update_validation_failed",
                message="Completion validation did not pass. The Todo was not completed; retry after resolving the validation failure.",
                details={"operation_id": operation_id, "reason_code": result.get("reason_code")})
            return {"proposal": failed, "turn": None}
        original = result.get("original_receipt") or result
        todo_id = _opaque(result.get("todo_id"), field="todo_id")
        self.store.save_checkpoint(proposal_id, step="canonical_update", receipt={
            "operation_id": operation_id, "todo_id": todo_id,
            "changed": original.get("changed") is True,
        })
        if not projection_delivery_requires_ack(result["projection_delivery"]):
            failed = self.store.mark_failed(
                proposal_id, error_code="canonical_update_projection_pending",
                message="The edit was committed. Display delivery is pending; retry this proposal to restore the current view.",
                details={"operation_id": operation_id, "canonical_committed": True},
            )
            return {"proposal": failed, "turn": None}
        operation = parameters.get("operation", "edit")
        outcome = ("gate_resolved" if action_kind == "gate.resolve" else
                   {"pause": "monitor_paused", "resume": "monitor_resumed", "edit": "monitor_updated", "stop": "monitor_stopped"}[operation]
                   if action_kind == "monitor.update" else
                   "todo_completed" if operation == "complete" else
                   "todo_updated" if original.get("changed") else "todo_unchanged")
        stored = self.store.apply(proposal_id,
            current_state_fingerprint=str(proposal["expected_state_fingerprint"]), receipt={
                "receipt_id": _digest({"proposal_id": proposal_id, "todo_id": todo_id})[:32],
                "outcome": outcome, "projection_verified": True,
                "projection_delivery": result["projection_delivery"],
                "operation_id": operation_id,
                "canonical_status": result.get("provider_status", result["status"]),
                "resource_ids": {"goal_id": str(parameters["goal_id"]), "todo_id": todo_id},
                **(_decision_readback(result) if action_kind == "gate.resolve" else {}),
            })
        return {"proposal": stored, "turn": None}

    def _apply_todo_update(
        self, proposal_id: str, proposal: dict[str, Any], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        from .chat_actions import _digest, _opaque

        if proposal.get("canonical_update_basis") is not None:
            return self._apply_reviewed_todo_edit(proposal_id, proposal, parameters)
        goal_id = str(parameters["goal_id"])
        current_fingerprint = self._goal_state_fingerprint(goal_id)
        if current_fingerprint != proposal.get("expected_state_fingerprint"):
            stale = self.store.apply(
                proposal_id, current_state_fingerprint=current_fingerprint, receipt={}
            )
            return {"proposal": stale, "turn": None}
        operation = str(parameters.get("operation") or "edit")
        decision = proposal.get("action_kind") == "gate.resolve"
        result = (self._run_gate_resolve if decision else self._run_todo_update)(parameters, dry_run=False)
        todo_id = _opaque(result.get("todo_id"), field="todo_id")
        receipt = {
            "receipt_id": _digest({"proposal_id": proposal_id, "todo_id": todo_id})[
                :32
            ],
            "outcome": (
                "gate_resolved"
                if decision
                else "todo_completed"
                if operation == "complete"
                else "todo_updated" if result.get("changed") else "todo_unchanged"
            ),
            "projection_verified": True,
            "resource_ids": {"goal_id": goal_id, "todo_id": todo_id},
            **(_decision_readback(result) if decision else {}),
        }
        stored = self.store.apply(
            proposal_id, current_state_fingerprint=current_fingerprint, receipt=receipt
        )
        return {"proposal": stored, "turn": None}
