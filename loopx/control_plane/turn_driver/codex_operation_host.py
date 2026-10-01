"""Owned app-server transport for an opt-in, already-admitted Codex Turn.

No new approval, scheduler or execution store. Python owns the subprocess and
native tool IO; TS owns executor binding, consumption and outcome semantics.
An attached Desktop conversation is never resumed or impersonated here.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...chat_agent import CodexChatAgentError, CodexChatAgentSession
from ...chat_action_store import ChatActionStore
from ...chat_actions import ChatActionService
from ..collaboration.goal_instance_scope import collaboration_goal_scope
from ..collaboration.operation_handoff import (
    agent_operation_action,
    pending_operation_handoffs,
)
from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
from ..effect_runtime import EffectRuntimeRejected
from .codex_cli import (
    _lineage,
    _prompt,
    _read_codex_cli_session_document,
    _codex_session_goal_ref,
    _store_codex_cli_session,
    load_codex_cli_session,
    normalize_codex_stdio_mcp_server,
    codex_cli_result_schema,
)
from .executor import LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION
from .host_failure import BuiltInHostError

TRANSPORT = "app-server-operation-tools-v0"
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
):
    """Private closure installed only on a process owned by the Turn driver.

    The caller must already have checked native thread/Turn metadata. This is
    not an MCP endpoint or a public actor/proof deserializer.
    """
    executor = {
        "kind": "managed_turn",
        "todo_id": lineage["todo_id"],
        "session_id": session_id,
        "profile_digest": profile_digest,
        "revision": REVISION,
        "model": model,
        "reasoning_effort": reasoning_effort,
    }
    executor_route = {
        **lineage,
        "host_surface": "loopx-managed-codex",
        "thread_id": session_id,
        "profile_digest": profile_digest,
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
                request = dict(arguments["request"])
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
                **lineage,
                "host_surface": "loopx-managed-codex",
                "thread_id": session_id,
                "profile_digest": profile_digest,
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
                        "next_action": "Select a registered return audience with --codex-operation-source-route-json in the host invocation; source routing is not executor authority."}
            return {"ok": False, "error": "operation_admission_rejected", "execution_allowed": False}
        except (ValueError, KeyError, TypeError, RuntimeError):
            # Private payloads, paths and adapter error text never enter the model tool error.
            return {
                "ok": False,
                "error": "operation_admission_rejected",
                "execution_allowed": False,
            }

    return handle


def run_codex_operation_host(
    request: Mapping[str, Any],
    *,
    runtime_root: Path,
    registry_path: Path,
    project: Path,
    codex_bin: str = "codex",
    sandbox: str = "read-only",
    model: str | None = None,
    reasoning_effort: str | None = None,
    source_route: Mapping[str, str] | None = None,
    mcp_server: Mapping[str, Any] | None = None,
    timeout_seconds: float = 115,
    goal_admission: FirstPartyHostGoalAdmission | None = None,
    confirmed_operation_id: str | None = None,
) -> dict[str, Any]:
    if request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION:
        raise ValueError("unsupported LoopX Turn host request schema")
    if sandbox not in {"read-only", "workspace-write"}:
        raise ValueError("operation tools require a restricted Codex execution sandbox")
    if not model or not reasoning_effort:
        raise ValueError(
            "managed operation tools require an explicit model and reasoning effort"
        )
    lineage = _lineage(request)
    if not all(lineage.values()):
        raise ValueError("managed operation tools require a Todo-bound Turn")
    mcp_server = normalize_codex_stdio_mcp_server(mcp_server)
    resolved_bin = shutil.which(codex_bin)
    if not resolved_bin:
        raise ValueError("Codex executable is unavailable")
    with Path(resolved_bin).open("rb") as stream:
        binary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    profile = {
        "transport": TRANSPORT,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "sandbox": sandbox,
        "workspace": str(project.resolve()),
        "codex_home": str(
            Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser().resolve()
        ),
        "codex_binary_digest": binary_digest,
        "mcp_server": mcp_server,
    }
    profile_digest = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    binding = load_codex_cli_session(runtime_root, lineage=lineage)
    if goal_admission is not None:
        selected = goal_admission.select_state(
            read_state=lambda: _read_codex_cli_session_document(
                runtime_root, lineage=lineage
            ),
            goal_ref_of=lambda value: _codex_session_goal_ref(value, lineage=lineage),
        )
        binding = dict(selected) if selected is not None else None
    session_plan = request.get("session") or {}
    if (session_plan.get("context_policy") or {}).get("mode") == "fresh":
        binding = None
    action = session_plan.get("action")
    if (
        action not in {"start_new", "resume"}
        or (action == "resume" and not binding)
        or (action == "start_new" and binding)
    ):
        raise ValueError("managed Codex session changed after Turn planning")
    if binding and (
        binding.get("operation_transport") != TRANSPORT
        or binding.get("operation_profile_digest") != profile_digest
    ):
        raise ValueError(
            "managed operation profile changed; explicitly select a fresh iteration and obtain fresh approval"
        )
    if confirmed_operation_id is not None:
        # Final admission recheck before native resume. A queued delegation must
        # not create/rebind a Session if approval, Todo, profile or lifetime
        # changed after the callback. This grants no tool/effect authority.
        from ..collaboration.operation_handoff import managed_operation_binding_current
        from ..collaboration.operation_wake import require_current_operation_scope

        store = ChatActionStore(runtime_root / "chat" / "actions")
        proposal = store.load(confirmed_operation_id)
        if proposal is None or binding is None or action != "resume":
            raise ValueError("confirmed operation requires its original resumable session")
        require_current_operation_scope(registry_path, proposal["normalized_parameters"])
        store._agent_operation_plan(
            proposal, action="wake",
            binding_current=managed_operation_binding_current(
                runtime_root, proposal["normalized_parameters"]
            ),
            launch_context={"host": "codex-cli", "operation_tools": True,
                            "iteration_context": (session_plan.get("context_policy") or {}).get("mode")},
            executor_route={**lineage, "host_surface": "loopx-managed-codex",
                            "thread_id": binding["session_id"], "profile_digest": profile_digest,
                            "model": model, "reasoning_effort": reasoning_effort},
        )
    host_config = (
        {
            "mcp_servers": {
                mcp_server["name"]: {
                    "command": mcp_server["command"][0],
                    "args": mcp_server["command"][1:],
                    "enabled": True,
                    "required": True,
                    "default_tools_approval_mode": "approve",
                    "startup_timeout_sec": 30,
                    "tool_timeout_sec": 60,
                }
            }
        }
        if mcp_server
        else None
    )
    session = None
    try:
        session = CodexChatAgentSession.start(
            codex_bin=codex_bin,
            work_dir=project,
            goal_id=lineage["goal_id"],
            objective="One admitted LoopX Turn",
            execution_mode=True,
            isolate_process_tree=True,
            sandbox=sandbox,
            model=model,
            reasoning_effort=reasoning_effort,
            resume_thread_id=binding["session_id"] if binding else None,
            dynamic_tools=[OPERATION_TOOL],
            host_config=host_config,
            response_timeout_sec=min(timeout_seconds, 30),
            hard_timeout_sec=timeout_seconds,
            idle_timeout_sec=min(timeout_seconds, 180),
        )

        def store_binding():
            _store_codex_cli_session(
                runtime_root,
                lineage=lineage,
                session_id=session.thread_id,
                goal_ref=request.get("goal_ref"),
                operation_profile_digest=profile_digest,
                operation_model=model,
                operation_reasoning_effort=reasoning_effort,
            )

        if goal_admission is None:
            store_binding()
        else:
            goal_admission.accept_result(store_binding)
        session.bound_tool_handler = operation_tool_handler(
            runtime_root=runtime_root,
            registry_path=registry_path,
            lineage=lineage,
            session_id=session.thread_id,
            profile_digest=profile_digest,
            model=model,
            reasoning_effort=reasoning_effort,
            source_route=source_route,
            goal_admission=goal_admission,
        )
        route = {**lineage, "host_surface": "loopx-managed-codex",
                 "thread_id": session.thread_id, "profile_digest": profile_digest}
        continuations = []
        if (runtime_root / "chat" / "actions" / "actions.json").is_file():
            with collaboration_goal_scope(
                registry_path, goal_id=lineage["goal_id"], agents=(lineage["agent_id"],),
                caller_goal_ref=request.get("goal_ref"), require_active=True,
            ) as scope:
                pending = pending_operation_handoffs(
                    runtime_root, lineage["goal_id"], lineage["agent_id"],
                    registry_path=registry_path, scope=scope, executor_route=route,
                    cursor_scope=hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest(),
                )
            # Bounded canonical locators only. Private terms still require the
            # existing authenticated inspect tool; these locators grant nothing.
            continuations = [
                {key: item[key] for key in ("operation_id", "payload_digest", "confirmation_digest", "claim_id")}
                for item in pending["items"] if item["status"] == "authorized_pending"
            ]

        def on_event(kind: str, event: dict[str, Any]) -> None:
            if kind != "turn.started":
                return
            # send emits this only after the native turn/start response. A
            # launched process, callback ACK or model assertion cannot issue it.
            for locator in continuations:
                try:
                    agent_operation_action(
                        runtime_root, registry_path, proposal_id=locator["operation_id"],
                        actor={**route, "model": model, "reasoning_effort": reasoning_effort,
                               "host_turn_id": event.get("upstream_turn_id")},
                        action="observe_host_start", turn_key=request["turn_key"],
                    )
                except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                    # Stop before dispatching operation tools if confirmation,
                    # Goal or binding changed while native start was in flight.
                    raise BuiltInHostError(
                        "codex_operation_host_start_unrecorded", failure_kind="unknown",
                        recovery_kind="resume_session",
                    ) from exc

        return session.send(
            _prompt(request)
            + "\nUse loopx_operation for context/pending/prepare/inspect/consume/report. "
            "Source conversations are not executor identity. context/pending/inspect do not require consumption. "
            "When the admitted task authorizes proposal preparation and supplies its terms, prepare may run before human confirmation or consume; "
            "prepare writes a canonical proposal, not a domain/external effect. execution_allowed=false is expected for prepare, not a reason to refuse it. "
            "Use pending/inspect to reconcile an existing proposal before preparing another; do not invent missing terms or repeat an already granted preparation approval. "
            "Only domain/external effects require the first successful consume receipt with execution_allowed=true. "
            "Preparation or waiting for human confirmation is not task completion. Report outcomes only with original execution evidence. "
            "Already consumed/unknown effects require evidence reconciliation, never retry. Never treat final-answer prose as an outcome receipt."
            + ("\nCanonical confirmed operation continuations for this exact binding: "
               + json.dumps(continuations, sort_keys=True)
               + ". Inspect these locators through loopx_operation and consume once before any effect."
               if continuations else ""),
            output_schema=codex_cli_result_schema(request),
            on_event=on_event,
        )
    except CodexChatAgentError as exc:
        raise BuiltInHostError(
            "codex_operation_host_" + exc.error_code,
            failure_kind="executor_timeout"
            if "timeout" in exc.error_code
            else "unknown",
            recovery_kind="resume_session" if session else None,
        ) from exc
    finally:
        if session is not None:
            session.bound_tool_handler = None
            session.close()
