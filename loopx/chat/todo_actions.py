"""Typed Chat actions for canonical Todo lifecycle transitions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from ..chat_store import ChatSessionStore, TERMINAL_TURN_STATES
from ..file_lock import exclusive_cross_runtime_file_lock
from ..history import load_registry
from ..registry import find_registry_goal
from ..control_plane.goals.activation import goal_is_stopped
from ..control_plane.coordination.shadow_management import shadow_maintenance_lock_target
from ..control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..control_plane.todos.mutation_api import complete_goal_todo, update_goal_todo
from ..control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable, read_canonical_todos_if_promoted,
)
from ..paths import effective_runtime_root
from ..control_plane.todos.provider_projection import projection_delivery_requires_ack
from ..control_plane.work_items.task_lease import lease_is_active


def _decision_readback(result: dict[str, Any]) -> dict[str, Any]:
    """Carry the canonical outcome and dependent effect, never a local guess."""
    resume = result.get("unblock_resume")
    return {
        "decision_outcome": result.get("decision_outcome"),
        "unblock_resume_state": resume.get("state") if isinstance(resume, dict) else None,
    }


class ChatTodoActionMixin:
    """Keep Todo preview/apply parity separate from general orchestration."""

    @contextmanager
    def _confirmed_task_commit(
        self, *, goal_id: str, todo_id: str, endpoint: str, project: Path,
        claim: dict[str, Any],
    ) -> Iterator[None]:
        """Fence acceptance with the same registry and canonical writers as Stop and Todo edits."""
        invoked = self.registry_path.resolve()
        route = resolve_goal_source_runtime_route(registry_path=invoked, goal_id=goal_id)
        source = Path(route["source_registry"])
        # Claim used this runtime. Recheck it after taking the same maintenance
        # guard as canonical writers, in the order used by Goal deletion.
        runtime = effective_runtime_root(invoked, None).resolve()
        targets: tuple[Path, ...] = (shadow_maintenance_lock_target(runtime, goal_id), source)
        if source != invoked:
            targets += (invoked,)
        with ExitStack() as locks:
            for target in targets:
                locks.enter_context(exclusive_cross_runtime_file_lock(target, operation="confirm_task_admission"))
            if (route != resolve_goal_source_runtime_route(registry_path=invoked, goal_id=goal_id)
                    or runtime != effective_runtime_root(invoked, None).resolve()):
                raise ValueError("The reviewed Task's Goal source changed")
            goal = find_registry_goal(load_registry(source), goal_id)
            if goal is None or goal.get("status", "active") not in {"active", "ready"} or goal_is_stopped(goal):
                raise ValueError("The Task's Goal is no longer active")
            if Path(str(goal.get("repo") or ".")).expanduser().resolve() != project:
                raise ValueError("The reviewed Task's project root changed")
            owner = claim["claimed_by"]
            if self._resolve_goal_agent(goal_id, endpoint, goal=goal) != owner:
                raise ValueError("The reviewed Agent endpoint now belongs to another Goal Agent")
            authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal_id, include_leases=True)
            todo = next((row for row in authority["todos"] if row["todo_id"] == todo_id), None)
            if (todo is None or todo.get("role") != "agent" or todo.get("status") != "open"
                    or todo.get("claimed_by") != owner or authority["handoff_mode"] != claim["handoff_mode"]):
                raise ValueError("The reviewed Task assignment changed before launch")
            if authority["handoff_mode"] == "hard_lease":
                lease = next((row for row in authority["leases"] if row["todo_id"] == todo_id), None)
                original = claim.get("lease")
                if (not lease or not isinstance(original, dict) or not lease_is_active(lease)
                        or any(lease.get(key) != original.get(key) for key in ("owner", "idempotency_key", "lease_epoch"))):
                    raise ValueError("The reviewed Task execution lease changed before launch")
            yield

    @staticmethod
    def _read_task_start(
        chat: ChatSessionStore, *, goal_id: str, agent_id: str, channel_id: str,
        client_turn_id: str, checkpoint: dict[str, Any] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        from ..chat_actions import _opaque

        if checkpoint is not None:
            sid = _opaque(checkpoint.get("session_id"), field="session_id")
            session = chat.load_session(sid)
            if (session is None or session.get("goal_id") != goal_id
                    or session.get("agent_id") != agent_id or session.get("channel_id") != channel_id):
                raise ValueError("The original Task Session is unavailable")
            return session, chat.turn_for_client(sid, client_turn_id)

        # A process may have persisted the Session before the proposal's
        # checkpoint. Include closed Sessions so retry cannot replace one.
        sessions = chat.session_candidates(goal_id=goal_id, agent_id=agent_id, channel_id=channel_id)
        matches = [
            (session, turn) for session in sessions
            if (turn := chat.turn_for_client(session["session_id"], client_turn_id)) is not None
        ]
        if len(matches) == 1:
            return matches[0]
        if len(sessions) > 1:
            raise ValueError("The original Task Session is ambiguous")
        return (sessions[0] if sessions else None), None

    def _start_confirmed_task(
        self, proposal_id: str, parameters: dict[str, Any], todo_id: str, steps: dict[str, Any],
    ) -> dict[str, Any]:
        from ..chat_actions import _opaque

        controller = self.runtime_controller
        chat = getattr(controller, "store", None) or self.chat_store
        if chat is None:
            raise ValueError("Chat Runtime Controller is unavailable")
        goal_id = str(parameters["goal_id"])
        owner = str(parameters["agent_id"])
        endpoint = str(parameters.get("endpoint_id") or owner)
        message = str(parameters["text"])
        channel_id = f"task.{todo_id}"
        client_turn_id = f"task-start-{proposal_id}"
        opened = steps.get("task_session_opened")
        execution = steps.get("execution_started")
        session, turn = self._read_task_start(
            chat, goal_id=goal_id, agent_id=endpoint, channel_id=channel_id,
            client_turn_id=client_turn_id, checkpoint=execution if execution is not None else opened,
        )
        if execution is not None and (turn is None or turn["turn_id"] != execution.get("turn_id")):
            raise ValueError("The original Task Turn is unavailable")
        if turn is not None and turn.get("message") != message:
            raise ValueError("The original Task Turn request changed")

        # An already dispatched or terminal Turn is readback. Only work that
        # still needs dispatch passes through the current Goal and claim gates.
        created = False
        if turn is None or (
            turn.get("status") not in TERMINAL_TURN_STATES
            and not (turn.get("started_at") or turn.get("upstream_turn_id"))
        ):
            if controller is None:
                raise ValueError("Chat Runtime Controller is unavailable")
            if session is not None and session.get("status") == "closed":
                raise ValueError("The original Task Session is closed")
            goal = self._goal(goal_id)
            if goal.get("status", "active") not in {"active", "ready"} or goal_is_stopped(goal):
                raise ValueError("The Task's Goal is no longer active")
            project = Path(str(goal.get("repo") or ".")).expanduser().resolve()
            if not project.is_dir():
                raise ValueError("the Goal project root is unavailable")
            self._agent_eligibility(endpoint, project=project)
            if parameters.get("endpoint_id") and self._resolve_goal_agent(goal_id, endpoint) != owner:
                raise ValueError("The reviewed Agent endpoint now belongs to another Goal Agent")
            claim_args = {
                "registry_path": self.registry_path, "goal_id": goal_id, "todo_id": todo_id,
                "claimed_by": owner, "agent_id": owner, "claim_only": True, "manage_execution": True,
            }
            claim = update_goal_todo(**claim_args, claim_operation_id=f"chat-task-start:{proposal_id}", dry_run=False)
            if claim["handoff_mode"] == "soft_claim" and claim["status"] in {"recovered", "replayed"}:
                # A past soft claim still needs a read-only current-owner check.
                if update_goal_todo(**claim_args, dry_run=True)["changed"]:
                    raise ValueError("The reviewed Task assignment changed before launch")
            if not isinstance(steps.get("todo_assigned"), dict):
                self.store.save_checkpoint(
                    proposal_id, step="todo_assigned", receipt={"todo_id": todo_id, "agent_id": owner},
                )
            if session is None:
                session, _ = controller.open_session(
                    goal_id=goal_id, agent_id=endpoint, work_dir=project, objective=message,
                    mode="resume_latest", channel_id=channel_id, agent_goal_id=goal_id,
                )
            sid = _opaque(session.get("session_id"), field="session_id")
            if opened is None and execution is None:
                self.store.save_checkpoint(proposal_id, step="task_session_opened", receipt={"session_id": sid})
            turn, created = controller.submit_turn(
                session_id=sid, client_turn_id=client_turn_id, message=message, work_dir=project, objective=message,
                _goal_commit=lambda: self._confirmed_task_commit(
                    goal_id=goal_id, todo_id=todo_id, endpoint=endpoint, project=project, claim=claim,
                ),
            )

        identity = {
            "session_id": _opaque(turn.get("session_id"), field="session_id"),
            "turn_id": _opaque(turn.get("turn_id"), field="turn_id"),
        }
        if execution is None:
            self.store.save_checkpoint(proposal_id, step="execution_started", receipt=identity)
        return {**identity, "status": str(turn.get("status") or "accepted"), "created": created}

    def _run_todo_update(
        self, parameters: dict[str, Any], *, dry_run: bool,
        basis: dict[str, Any] | None = None, operation_id: str | None = None,
    ) -> dict[str, Any]:
        goal_id = str(parameters["goal_id"])
        operation = str(parameters.get("operation") or "edit")
        if parameters.get("review_basis") is not None and parameters["review_basis"] != basis:
            raise ValueError("The Task source changed. Reload its complete requirements before saving.")
        # Transport only caller-supplied proof. The terminal owner revalidates
        # the execution; a lease read or a Todo claim is never substitute proof.
        proof = {key: parameters[key] for key in (
            "task_lease_idempotency_key", "task_lease_expected_version",
        ) if key in parameters} if operation == "complete" else {}
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
                **proof,
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
            # The established block control uses note as its pause reason;
            # preserve the task's existing notes. Explicit ordinary note edits
            # and lifecycle effects still share the canonical transaction.
            note=(parameters.get("note") if operation != "block" or basis is None else None),
            reason=(parameters.get("note") or "Owner requested deferral") if operation in {"block", "defer"} else (
                parameters.get("note") or "Owner explicitly requested task resumption" if status == "open" else None),
            claimed_by=(
                parameters.get("agent_id") if operation == "reassign" else None
            ),
            resume_when=parameters.get("resume_when"),
            clear_resume_when=(operation == "block" or (status == "open" and not parameters.get("resume_when"))) and basis is not None,
            successor_todo_ids=parameters.get("successor_todo_ids"),
            agent_id=parameters.get("agent_id"),
            authority_reason="owner-confirmed typed Chat action",
            dry_run=dry_run,
            **proof,
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
        from ..chat_actions import _digest, _opaque

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
        from ..chat_actions import _digest, _opaque

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
