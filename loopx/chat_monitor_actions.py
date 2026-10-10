"""Typed Chat actions for updating continuous monitors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .control_plane.runtime.time import parse_timestamp
from .control_plane.todos.mutation_api import complete_goal_todo, update_goal_todo


class ChatMonitorActionMixin:
    """Keep monitor execution policy separate from the general action service."""

    def _run_monitor_update(
        self, parameters: dict[str, Any], *, dry_run: bool,
        basis: dict[str, Any] | None = None, operation_id: str | None = None,
    ) -> dict[str, Any]:
        from .chat_actions import _monitor_metadata, _monitor_text

        goal_id = str(parameters["goal_id"])
        operation = str(parameters["operation"])
        if operation == "stop":
            result = complete_goal_todo(
                registry_path=self.registry_path,
                goal_id=goal_id,
                todo_id=str(parameters["todo_id"]),
                evidence="Owner stopped the continuous monitor from typed Chat action.",
                **self._reviewed_terminal_options(basis, operation_id),
                no_followup=True,
                claimed_by=str(parameters["agent_id"]),
                agent_id=str(parameters["agent_id"]),
                authority_reason="owner-confirmed typed Chat action",
                dry_run=dry_run,
            )
        else:
            kwargs: dict[str, Any] = {
                "registry_path": self.registry_path,
                "goal_id": goal_id,
                "todo_id": str(parameters["todo_id"]),
                "agent_id": str(parameters["agent_id"]),
                "authority_reason": "owner-confirmed typed Chat action",
                "dry_run": dry_run,
                **self._reviewed_update_options(basis, operation_id),
            }
            if operation == "pause":
                kwargs.update(
                    status="blocked",
                    note="Owner paused this continuous monitor from typed Chat action.",
                )
            elif operation == "resume":
                kwargs.update(status="open")
            else:
                stop_condition_raw = parameters.get("stop_condition")
                # Canonical intent contains the cadence, not a fresh timestamp
                # on every retry. The TS planner derives the due date exactly
                # once when a new operation commits; receipt recovery skips it.
                base_metadata = _monitor_metadata(parameters, schedule=basis is None)
                if stop_condition_raw is not None:
                    stop_condition = str(stop_condition_raw).strip()
                    if parse_timestamp(stop_condition) is not None:
                        kwargs.update(
                            text=_monitor_text(parameters),
                            resume_when=None,
                            clear_resume_when=True,
                            monitor_metadata={
                                **base_metadata,
                                "expires_at": stop_condition,
                                "watch_only": None,
                            },
                        )
                    elif stop_condition.lower() in {"watch_only", "watch-only", "watch", "continuous", "never"}:
                        kwargs.update(
                            text=_monitor_text(parameters),
                            resume_when=None,
                            clear_resume_when=True,
                            monitor_metadata={
                                **base_metadata,
                                "expires_at": None,
                                "watch_only": "true",
                            },
                        )
                    elif stop_condition:
                        kwargs.update(
                            text=_monitor_text(parameters),
                            resume_when=stop_condition,
                            clear_resume_when=False,
                            monitor_metadata={
                                **base_metadata,
                                "expires_at": None,
                                "watch_only": None,
                            },
                        )
                    else:
                        kwargs.update(
                            text=_monitor_text(parameters),
                            resume_when=None,
                            clear_resume_when=True,
                            monitor_metadata={
                                **base_metadata,
                                "expires_at": None,
                                "watch_only": "true",
                            },
                        )
                else:
                    kwargs.update(
                        text=_monitor_text(parameters),
                        monitor_metadata=base_metadata,
                    )
            result = update_goal_todo(**kwargs)
        return result

    def _apply_monitor_update(
        self, proposal_id: str, proposal: dict[str, Any], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        from .chat_actions import (
            ProtectedActionGate,
            _digest,
            _opaque,
        )

        goal_id = str(parameters["goal_id"])
        operation = str(parameters["operation"])
        if proposal.get("canonical_update_basis") is not None:
            return self._apply_reviewed_todo_edit(proposal_id, proposal, parameters)
        current_fingerprint = self._goal_state_fingerprint(goal_id)
        if operation == "run_now":
            if self.runtime_controller is None or self.chat_store is None:
                raise ProtectedActionGate(
                    "monitor.update",
                    gate={
                        "kind": "runtime_unavailable",
                        "summary": "A recoverable Agent runtime is required to run this monitor now.",
                        "next_action": "Open the Goal channel, establish a Session, and regenerate the preview.",
                    },
                )
            expected = str(proposal.get("expected_state_fingerprint") or "")
            session_id = str(parameters.get("session_id") or "")
            composite = current_fingerprint
            if session_id:
                session_fingerprint = self._session_fingerprint(session_id, goal_id)
                composite = _digest(
                    {"goal": current_fingerprint, "session": session_fingerprint}
                )
            if composite != expected:
                stale = self.store.apply(
                    proposal_id, current_state_fingerprint=composite, receipt={}
                )
                return {"proposal": stale, "turn": None}
            goal = self._goal(goal_id)
            project = Path(str(goal.get("repo") or "")).expanduser().resolve()
            if not session_id:
                execution_agent_id = str(
                    parameters.get("endpoint_id") or parameters["agent_id"]
                )
                self._agent_eligibility(execution_agent_id, project=project)
                session, _resumed = self.runtime_controller.open_session(
                    goal_id=goal_id,
                    agent_id=execution_agent_id,
                    work_dir=project,
                    objective=str(
                        parameters.get("target")
                        or f"Run monitor {parameters['todo_id']}"
                    ),
                    mode="resume_latest",
                    channel_id=f"task.{parameters['todo_id']}",
                    agent_goal_id=goal_id,
                )
                session_id = _opaque(session.get("session_id"), field="session_id")
            turn, created = self.runtime_controller.submit_turn(
                session_id=session_id,
                client_turn_id=f"action-{proposal_id}",
                message=(
                    f"Run continuous monitor Todo {parameters['todo_id']} now and "
                    "write back only verified material change."
                ),
                work_dir=project,
                objective=str(goal.get("domain") or goal_id),
            )
            turn_id = _opaque(turn.get("turn_id"), field="turn_id")
            receipt = {
                "receipt_id": _digest(
                    {"proposal_id": proposal_id, "turn_id": turn_id}
                )[:32],
                "outcome": (
                    "monitor_turn_created"
                    if created
                    else "monitor_turn_already_exists"
                ),
                "projection_verified": True,
                "resource_ids": {
                    "goal_id": goal_id,
                    "todo_id": str(parameters["todo_id"]),
                    "session_id": session_id,
                    "turn_id": turn_id,
                },
            }
            stored = self.store.apply(
                proposal_id, current_state_fingerprint=composite, receipt=receipt
            )
            return {
                "proposal": stored,
                "turn": {
                    "turn_id": turn_id,
                    "status": str(turn.get("status") or "queued"),
                    "created": created,
                },
            }
        if current_fingerprint != proposal.get("expected_state_fingerprint"):
            stale = self.store.apply(
                proposal_id, current_state_fingerprint=current_fingerprint, receipt={}
            )
            return {"proposal": stale, "turn": None}
        result = self._run_monitor_update(parameters, dry_run=False, operation_id=f"action-{proposal_id}")
        outcome = {"stop": "monitor_stopped", "pause": "monitor_paused",
                   "resume": "monitor_resumed", "edit": "monitor_updated"}[operation]
        todo_id = _opaque(result.get("todo_id"), field="todo_id")
        receipt = {
            "receipt_id": _digest(
                {
                    "proposal_id": proposal_id,
                    "todo_id": todo_id,
                    "operation": operation,
                }
            )[:32],
            "outcome": outcome,
            "projection_verified": True,
            "resource_ids": {"goal_id": goal_id, "todo_id": todo_id},
        }
        stored = self.store.apply(
            proposal_id, current_state_fingerprint=current_fingerprint, receipt=receipt
        )
        return {"proposal": stored, "turn": None}
