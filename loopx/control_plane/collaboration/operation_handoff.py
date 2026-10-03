"""IO for confirmed operation continuations; no second approval/inbox store.

The original registry owns the session route. The typed action store owns human
confirmation and consumption. An inbox/wakeup message is only a locator.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from contextlib import ExitStack
from typing import Any

from ...chat_action_store import ActionConflictError, ChatActionStore
from ...thread_agent_binding import resolve_registry_thread_agent_binding
from ..goals.activation import goal_is_stopped
from .goal_instance_scope import (
    CollaborationGoalScope,
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
)


def _store(runtime_root: Path) -> ChatActionStore:
    root = runtime_root / "chat" / "actions"
    if not (root / "actions.json").is_file():
        raise ValueError("canonical operation store is unavailable")
    return ChatActionStore(root)


def managed_operation_binding_current(
    runtime_root: Path, parameters: Mapping[str, Any]
) -> bool:
    """Read the original Turn session owner; the shared TS owner judges it."""
    from ..effect_runtime import effect_runtime_result
    from ..turn_driver.codex_cli import load_codex_cli_session

    executor = parameters["executor"]
    lineage = {
        "goal_id": parameters["goal_id"],
        "agent_id": parameters["agent_id"],
        "todo_id": executor["todo_id"],
    }
    return (
        effect_runtime_result(
            "operation.managed_binding.current",
            {
                "parameters": dict(parameters),
                "session": load_codex_cli_session(runtime_root, lineage=lineage),
            },
        )["current"]
        is True
    )


def _binding(
    registry_path: Path, parameters: Mapping[str, Any], runtime_root: Path
) -> bool:
    executor = parameters["executor"]
    if executor.get("kind") == "managed_turn":
        return managed_operation_binding_current(runtime_root, parameters)
    observed = resolve_registry_thread_agent_binding(
        registry_path=registry_path,
        host_surface=executor["host_surface"],
        thread_id=executor["thread_id"],
    )
    return observed.get("status") == "bound" and (
        observed.get("goal_id"),
        observed.get("agent_id"),
    ) == (parameters["goal_id"], parameters["agent_id"])


def pending_operation_handoffs(
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    *,
    registry_path: Path | None = None,
    scope: CollaborationGoalScope | None = None,
    cursor: str | None = None,
    cursor_scope: str,
    executor_route: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project canonical tickets into the existing Inbox; do not copy authority."""
    store = (
        _store(runtime_root)
        if (runtime_root / "chat" / "actions" / "actions.json").is_file()
        else None
    )
    if store is None and cursor is None:
        return {"items": [], "pending_count": 0, "overflow": None, "next_cursor": None}
    result = []
    for proposal in store.list(goal_id=goal_id) if store is not None else []:
        parameters = proposal.get("normalized_parameters") or {}
        executor = parameters.get("executor") or {}
        if (
            proposal.get("action_kind") != "operation.execute"
            or parameters.get("agent_id") != agent_id
            or executor.get("kind") not in {"agent_session", "managed_turn"}
        ):
            continue
        if (
            scope is not None
            and decide_collaboration_lifecycle(
                scope,
                operation="inbox_observe",
                record={"goal_ref": parameters.get("origin_goal_ref")},
            ).get("kind")
            == "omit"
        ):
            continue
        plan = store._agent_operation_plan(proposal, action="project")
        if plan["status"] not in {
            "authorized_pending",
            "consumed_outcome_pending",
            "submission_unknown",
        }:
            continue
        current = registry_path is None or _binding(
            registry_path, parameters, runtime_root
        )
        if not current and not plan["needs_reconciliation"]:
            continue
        result.append(
            {
                **plan,
                "binding_current": current,
                "summary": proposal["summary"],
                "instruction": (
                    "Use the bound managed Turn's loopx_operation tool; source conversation flags grant no authority. "
                    if executor.get("kind") == "managed_turn"
                    else "The original host must authenticate through its session-bound tool transport before reading "
                    "private operation terms or consuming authority. No qualified producer is connected to the CLI; "
                    "environment thread ids are not identity proof. "
                )
                + "Only the first successful consumption permits execution; consumed/unknown results require "
                "original external-system reconciliation, never another submission. Inbox delivery is not execution authority.",
                "next_action": (
                    "Resume the original managed binding through delegation/Turn; inspect and consume through loopx_operation."
                    if executor.get("kind") == "managed_turn"
                    else "Integrate the original host's authenticated session-bound tool transport; do not retry via environment identity."
                )
                if plan["status"] == "authorized_pending"
                else "Reconcile the original external result; do not submit again.",
            }
        )
    if not result and cursor is None:
        return {"items": [], "pending_count": 0, "overflow": None, "next_cursor": None}
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        return dict(
            effect_runtime_result(
                "operation.agent_handoff.inbox",
                {
                    "items": result,
                    "cursor": cursor,
                    "cursor_scope": cursor_scope,
                    "executor_route": dict(executor_route) if executor_route is not None else None,
                },
            )
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc


def agent_operation_action(
    runtime_root: Path,
    registry_path: Path,
    *,
    proposal_id: str,
    actor: Mapping[str, Any],
    action: str,
    consumption_id: str | None = None,
    outcome: Mapping[str, Any] | None = None,
    turn_key: str | None = None,
) -> dict[str, Any]:
    """Internal locked IO seam, not a public caller-authentication endpoint.

    `actor` must be supplied by a qualified host transport, never by CLI flags,
    environment variables or an arbitrary model tool argument. Fixture calls
    validate storage semantics only; the public CLI remains blocked until a
    real producer/verifier pair is integrated.
    """
    store = _store(runtime_root)
    proposal = store.load(proposal_id)
    if proposal is None:
        raise ValueError("canonical operation was not found")
    parameters = proposal.get("normalized_parameters") or {}
    if (parameters.get("goal_id"), parameters.get("agent_id")) != (
        actor.get("goal_id"),
        actor.get("agent_id"),
    ):
        raise ActionConflictError("operation is not bound to this Goal and Agent")
    with collaboration_goal_scope(
        registry_path,
        goal_id=actor["goal_id"],
        agents=(actor["agent_id"],),
        caller_goal_ref=parameters.get("origin_goal_ref"),
        require_active=action in {"consume", "observe_host_start"},
        # Recovery-owner binding must stay valid through evidence commit too.
        # Use the same Goal -> registry -> action-store ordering as consumption.
        lock_registry=True,
    ) as scope:
        if action in {"consume", "observe_host_start"}:
            decide_collaboration_lifecycle(scope, operation="request_create")
            if goal_is_stopped(scope.goal):
                raise ActionConflictError("confirmed operation Goal is stopped")
        else:
            # Historical result publication cannot re-grant execution.
            ref = {"goal_ref": parameters.get("origin_goal_ref")}
            decide_collaboration_lifecycle(
                scope,
                operation="result_publish" if action == "report" else "history_inspect",
                record=ref,
                route=ref,
            )
        # Keep the existing session owner stable through the action-store commit.
        # Session writes and operation commits share Goal -> registry -> session
        # -> action-store order. No lock is held across a domain effect.
        from ...file_lock import exclusive_file_lock
        from ..turn_driver.codex_cli import _session_path

        executor = parameters["executor"]
        session_paths = set()
        if executor.get("kind") == "managed_turn":
            session_paths.add(
                _session_path(
                    runtime_root,
                    {
                        "goal_id": parameters["goal_id"],
                        "agent_id": parameters["agent_id"],
                        "todo_id": executor["todo_id"],
                    },
                )
            )
        if actor.get("host_surface") == "loopx-managed-codex":
            session_paths.add(
                _session_path(
                    runtime_root,
                    {
                        "goal_id": actor["goal_id"],
                        "agent_id": actor["agent_id"],
                        "todo_id": actor["todo_id"],
                    },
                )
            )
        with ExitStack() as locks:
            # A recovery Turn may have a different Todo. Hold both owners in
            # deterministic order, so replacement revocation cannot race report.
            for path in sorted(session_paths):
                locks.enter_context(exclusive_file_lock(path))
            return _commit_agent_operation(
                store,
                runtime_root,
                registry_path,
                parameters,
                proposal,
                proposal_id,
                actor,
                action,
                consumption_id,
                outcome,
                turn_key,
            )


def _commit_agent_operation(
    store: ChatActionStore,
    runtime_root: Path,
    registry_path: Path,
    parameters: Mapping[str, Any],
    proposal: dict,
    proposal_id: str,
    actor: Mapping[str, Any],
    action: str,
    consumption_id: str | None,
    outcome: Mapping[str, Any] | None,
    turn_key: str | None,
) -> dict[str, Any]:
    current = _binding(registry_path, parameters, runtime_root)
    actor_executor = (
        {
            "kind": "managed_turn",
            "session_id": actor.get("thread_id"),
            "todo_id": actor.get("todo_id"),
            "profile_digest": actor.get("profile_digest"),
            "revision": "managed-turn-handoff-v0",
            "model": actor.get("model"),
            "reasoning_effort": actor.get("reasoning_effort"),
        }
        if actor.get("host_surface") == "loopx-managed-codex"
        else actor
    )
    actor_current = (
        _binding(
            registry_path, {**parameters, "executor": actor_executor}, runtime_root
        )
        if action in {"inspect", "report"}
        else False
    )
    if action == "inspect":
        plan = store._agent_operation_plan(
            proposal,
            action="inspect",
            actor=dict(actor),
            binding_current=current,
            actor_binding_current=actor_current,
        )
        return {
            **plan,
            "binding_current": current,
            "parameters": parameters,
            "confirmation": proposal["operation"].get("confirmation"),
            "consumption": proposal["operation"].get("agent_handoff"),
            "outcome": proposal["operation"].get("outcome"),
            "reconciliation": proposal["operation"].get("reconciliation"),
            "outcome_report": proposal["operation"].get("outcome_report"),
            "reconciliation_report": proposal["operation"].get("reconciliation_report"),
        }
    if action == "observe_host_start":
        return store.record_agent_operation_host_start(
            proposal_id, actor=actor, binding_current=current, turn_key=str(turn_key or ""),
        )
    if action == "consume":
        return store.consume_agent_operation(
            proposal_id,
            actor=actor,
            binding_current=current,
            consumption_id=str(consumption_id or ""),
        )
    if action == "report":
        updated = store.observe_operation_outcome(
            proposal_id,
            outcome=outcome or {},
            agent_actor=actor,
            agent_binding_current=current,
            agent_actor_binding_current=actor_current,
        )
        plan = store._agent_operation_plan(updated, action="project")
        return {
            "ok": True,
            **plan,
            "outcome": updated["operation"].get("reconciliation")
            or updated["operation"]["outcome"],
            "outcome_report": updated["operation"].get("outcome_report"),
            "reconciliation_report": updated["operation"].get("reconciliation_report"),
        }
    raise ValueError("unsupported agent operation action")
