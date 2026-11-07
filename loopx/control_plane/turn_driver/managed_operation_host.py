"""Shared IO for the two owned managed-operation transports.

Native acceptance and process ownership stay in each adapter. Collaboration's
existing typed owner still decides scope, wake, consumption and outcome; this
module transports those requests and returns only canonical pending locators.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from ...chat_action_store import ChatActionStore
from ...chat_actions import ChatActionService
from ..collaboration.goal_instance_scope import collaboration_goal_scope
from ..collaboration.operation_handoff import (
    agent_operation_action,
    managed_operation_binding_current,
    pending_operation_handoffs,
)
from ..collaboration.operation_wake import require_current_operation_scope
from ..effect_runtime import EffectRuntimeRejected
from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
from .operation_requests import BoundOperationRequest


def _operation_surface(host: str) -> str:
    if host not in {"codex-cli", "dsh"}:
        raise ValueError("unsupported owned operation host")
    return "loopx-managed-dsh" if host == "dsh" else "loopx-managed-codex"


def operation_executor_route(
    lineage: Mapping[str, str], *, host: str, session_id: str, profile_digest: str,
) -> dict[str, str]:
    return {**lineage, "host_surface": _operation_surface(host),
            "thread_id": session_id, "profile_digest": profile_digest}


REVISION = "managed-turn-handoff-v0"
OPERATION_TOOL = {
    "name": "loopx_operation",
    "description": "Read/prepare a scoped operation, consume exact human approval once, or report original evidence. Identity comes from this native connection, never arguments. A receipt is not permission to retry an effect.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "context",
                    "pending",
                    "prepare",
                    "inspect",
                    "consume",
                    "report",
                ],
            },
            "proposal_id": {"type": "string"},
            "consumption_id": {"type": "string"},
            "request": {"type": "object"},
            "outcome": {"type": "object"},
            "cursor": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}


def operation_tool_handler(
    *,
    runtime_root: Path,
    registry_path: Path,
    lineage: Mapping[str, str],
    session_id: str,
    profile_digest: str,
    model: str,
    reasoning_effort: str,
    source_route: Mapping[str, str] | None = None,
    goal_admission: FirstPartyHostGoalAdmission | None = None,
    host: str = "codex-cli",
    frozen_request: BoundOperationRequest | None = None,
) -> Callable[[str, Any, dict[str, Any]], dict[str, Any]]:
    """Private closure installed only on a process owned by the Turn driver.

    The caller must already have checked native thread/Turn metadata. This is
    not an MCP endpoint or a public actor/proof deserializer.
    """
    executor_route = operation_executor_route(
        lineage, host=host, session_id=session_id, profile_digest=profile_digest,
    )
    executor = {
        "kind": "managed_turn",
        "todo_id": lineage["todo_id"],
        "session_id": session_id,
        "profile_digest": profile_digest,
        "revision": REVISION,
        "model": model,
        "reasoning_effort": reasoning_effort,
        **({"host": "dsh"} if host == "dsh" else {}),
    }

    def handle(tool: str, arguments: Any, native: dict[str, Any]) -> dict[str, Any]:
        if tool != "loopx_operation" or not isinstance(arguments, dict):
            return {"ok": False, "error": "unsupported_operation_tool"}
        if native.get("thread_id") != session_id or not native.get("host_turn_id"):
            return {"ok": False, "error": "tool_turn_mismatch"}
        allowed = {
            "context": {"action"},
            "pending": {"action"},
            "prepare": {"action", "request"},
            "inspect": {"action", "proposal_id"},
            "consume": {"action", "proposal_id", "consumption_id"},
            "report": {"action", "proposal_id", "outcome"},
        }
        action = arguments.get("action")
        keys = set(arguments) - ({"cursor"} if action == "pending" else set())
        if (
            not isinstance(action, str)
            or action not in allowed
            or keys != allowed[action]
        ):
            return {"ok": False, "error": "invalid_operation_arguments"}
        try:
            if goal_admission is not None:
                goal_admission.require_current()
            if action == "context":
                return {
                    "ok": True,
                    **lineage,
                    "executor": executor,
                    "authority": "approval_and_first_consumption_required",
                    "external_write_performed": False,
                    **({"prepared_request": {"ref": frozen_request.ref}}
                       if frozen_request is not None else {}),
                }
            if action == "pending":
                with collaboration_goal_scope(
                    registry_path,
                    goal_id=lineage["goal_id"],
                    agents=(lineage["agent_id"],),
                ) as scope:
                    cursor_scope = hashlib.sha256(
                        json.dumps(
                            ["managed-operation-v0", str(runtime_root.resolve()),
                             scope.target(lineage["agent_id"]), executor],
                            sort_keys=True, separators=(",", ":"),
                        ).encode()
                    ).hexdigest()
                    return {
                        "ok": True,
                        **pending_operation_handoffs(
                            runtime_root,
                            lineage["goal_id"],
                            lineage["agent_id"],
                            registry_path=registry_path,
                            scope=scope,
                            cursor=arguments.get("cursor"),
                            cursor_scope=cursor_scope,
                            executor_route=executor_route,
                        ),
                    }
            if action == "prepare":
                request = (frozen_request.resolve(arguments["request"])
                           if frozen_request is not None else dict(arguments["request"]))
                terms = dict(request.get("normalized_parameters") or {})
                if "source_route" in terms and terms["source_route"] != source_route:
                    raise ValueError("the model cannot retarget the host-selected return audience")
                if source_route is not None:
                    terms["source_route"] = dict(source_route)
                for key, expected in {
                    "goal_id": lineage["goal_id"],
                    "agent_id": lineage["agent_id"],
                    "executor": executor,
                }.items():
                    if key in terms and terms[key] != expected:
                        raise ValueError(
                            "operation request cannot select another execution subject"
                        )
                    terms[key] = expected
                if request.get("action_kind") != "operation.execute":
                    raise ValueError("operation tool prepares only typed operations")
                request["normalized_parameters"] = terms
                service = ChatActionService(
                    store=ChatActionStore(runtime_root / "chat" / "actions"),
                    registry_path=registry_path,
                )
                return {
                    "ok": True,
                    "proposal": service.preview(request),
                    "execution_allowed": False,
                }
            actor = {
                **executor_route,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "host_turn_id": native["host_turn_id"],
            }
            return {
                "ok": True,
                **agent_operation_action(
                    runtime_root,
                    registry_path,
                    proposal_id=arguments["proposal_id"],
                    actor=actor,
                    action=action,
                    consumption_id=arguments.get("consumption_id"),
                    outcome=arguments.get("outcome"),
                ),
            }
        except EffectRuntimeRejected as exc:
            if exc.diagnostic_code == "operation_source_route_ambiguous":
                return {"ok": False, "error": "operation_source_route_ambiguous",
                        "execution_allowed": False,
                        "next_action": (
                            "Select a registered return audience in the owned DSH host source_route; source routing is not executor authority."
                            if host == "dsh" else
                            "Select a registered return audience with --codex-operation-source-route-json in the host invocation; source routing is not executor authority."
                        )}
            return {"ok": False, "error": "operation_admission_rejected", "execution_allowed": False}
        except (ValueError, KeyError, TypeError, RuntimeError):
            # Private payloads, paths and adapter error text never enter the model tool error.
            return {
                "ok": False,
                "error": "operation_admission_rejected",
                "execution_allowed": False,
            }

    return handle


def require_confirmed_operation_resume(
    *, runtime_root: Path, registry_path: Path, request: Mapping[str, Any],
    lineage: Mapping[str, str], binding: Mapping[str, Any] | None, host: str,
    profile_digest: str, model: str, reasoning_effort: str,
    confirmed_operation_id: str | None,
) -> None:
    """Recheck the existing wake authority before either native process starts."""
    if confirmed_operation_id is None:
        return
    store = ChatActionStore(runtime_root / "chat" / "actions")
    proposal = store.load(confirmed_operation_id)
    session_plan = request.get("session") or {}
    if proposal is None or binding is None or session_plan.get("action") != "resume":
        raise ValueError(
            "confirmed operation requires the original DSH session" if host == "dsh"
            else "confirmed operation requires its original resumable session"
        )
    parameters = proposal["normalized_parameters"]
    require_current_operation_scope(registry_path, parameters)
    store._agent_operation_plan(
        proposal, action="wake",
        binding_current=managed_operation_binding_current(runtime_root, parameters),
        launch_context={"host": host, "operation_tools": True,
                        "iteration_context": (session_plan.get("context_policy") or {}).get("mode")},
        executor_route={
            **operation_executor_route(lineage, host=host,
                session_id=binding["session_id"], profile_digest=profile_digest),
            "model": model, "reasoning_effort": reasoning_effort,
        },
    )


def operation_continuations(
    *, runtime_root: Path, registry_path: Path, lineage: Mapping[str, str],
    route: Mapping[str, str], goal_ref: dict[str, str] | None,
) -> list[dict[str, Any]]:
    with collaboration_goal_scope(
        registry_path, goal_id=lineage["goal_id"], agents=(lineage["agent_id"],),
        caller_goal_ref=goal_ref, require_active=True,
    ) as scope:
        pending = pending_operation_handoffs(
            runtime_root, lineage["goal_id"], lineage["agent_id"],
            registry_path=registry_path, scope=scope, executor_route=route,
            cursor_scope=hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest(),
        )
    # Locators grant nothing. Private terms still require authenticated inspect.
    return [
        {key: item[key] for key in ("operation_id", "payload_digest", "confirmation_digest", "claim_id")}
        for item in pending["items"] if item["status"] == "authorized_pending"
    ]


def record_operation_start(
    *, runtime_root: Path, registry_path: Path, continuations: Sequence[Mapping[str, Any]],
    route: Mapping[str, str], model: str, reasoning_effort: str,
    host_turn_id: str | None, turn_key: str,
) -> None:
    """Called only after the adapter authenticates native Turn acceptance."""
    for locator in continuations:
        agent_operation_action(
            runtime_root, registry_path, proposal_id=locator["operation_id"],
            actor={**route, "model": model, "reasoning_effort": reasoning_effort,
                   "host_turn_id": host_turn_id},
            action="observe_host_start", turn_key=turn_key,
        )
