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
from ...extensions.codex_native_child import native_child_observer
from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
from . import managed_operation_host as operations
from .codex_cli import (
    _prompt,
    normalize_codex_stdio_mcp_server,
    codex_cli_result_schema,
)
from .codex_sessions import _lineage, select_managed_operation_session
from .executor import LOOPX_TURN_HOST_REQUEST_SCHEMA_VERSION
from .host_failure import BuiltInHostError
from .operation_requests import (
    operation_request_source, request_reference_instruction,
)

TRANSPORT = "app-server-operation-tools-v0"


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
    timeout_seconds: float | None = None,
    goal_admission: FirstPartyHostGoalAdmission | None = None,
    confirmed_operation_id: str | None = None,
    operation_request_file: Path | None = None,
    operation_request_sha256: str | None = None,
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
    request_source = operation_request_source(operation_request_file, operation_request_sha256)
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
    if request_source is not None:
        profile["operation_request"] = request_source.profile(source_route)
    launch_scope = FirstPartyHostGoalAdmission.launch_scope_for_plan(
        admission=goal_admission, registry_path=registry_path,
        goal_id=lineage["goal_id"], planned_goal_ref=request.get("goal_ref"),
    )
    profile_digest = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    selection = select_managed_operation_session(
        runtime_root, request=request, lineage=lineage, host="codex-cli",
        transport=TRANSPORT, profile_digest=profile_digest, model=model,
        reasoning_effort=reasoning_effort, goal_admission=goal_admission,
    )
    binding = selection.binding
    operations.require_confirmed_operation_resume(
        runtime_root=runtime_root, registry_path=registry_path, request=request,
        lineage=lineage, binding=binding, host="codex-cli", profile_digest=profile_digest,
        model=model, reasoning_effort=reasoning_effort,
        confirmed_operation_id=confirmed_operation_id,
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
            dynamic_tools=[operations.OPERATION_TOOL],
            host_config=host_config,
            launch_guard=lambda: launch_scope("codex_operation_process_launch"),
            response_timeout_sec=30 if timeout_seconds is None else min(timeout_seconds, 30),
            hard_timeout_sec=timeout_seconds,
            idle_timeout_sec=180 if timeout_seconds is None else min(timeout_seconds, 180),
        )

        selection.store(session.thread_id)
        session.bound_tool_handler = operations.operation_tool_handler(
            runtime_root=runtime_root,
            registry_path=registry_path,
            lineage=lineage,
            session_id=session.thread_id,
            profile_digest=profile_digest,
            model=model,
            reasoning_effort=reasoning_effort,
            source_route=source_route,
            goal_admission=goal_admission,
            frozen_request=(request_source.bind(request, lineage=lineage,
                session_id=session.thread_id, profile_digest=profile_digest)
                if request_source is not None else None),
        )
        route = operations.operation_executor_route(
            lineage, host="codex-cli", session_id=session.thread_id, profile_digest=profile_digest,
        )
        continuations = (
            operations.operation_continuations(
                runtime_root=runtime_root, registry_path=registry_path, lineage=lineage,
                route=route, goal_ref=request.get("goal_ref"),
            ) if (runtime_root / "chat" / "actions" / "actions.json").is_file() else []
        )

        def on_event(kind: str, event: dict[str, Any]) -> None:
            if kind != "turn.started":
                return
            # Only the authenticated native turn/start response reaches here.
            try:
                operations.record_operation_start(
                    runtime_root=runtime_root, registry_path=registry_path,
                    continuations=continuations, route=route, model=model,
                    reasoning_effort=reasoning_effort,
                    host_turn_id=event.get("upstream_turn_id"), turn_key=request["turn_key"],
                )
            except (ValueError, KeyError, TypeError, RuntimeError) as exc:
                raise BuiltInHostError(
                    "codex_operation_host_start_unrecorded", failure_kind="unknown",
                    recovery_kind="resume_session",
                ) from exc

        child_observer = native_child_observer(request, runtime_root=runtime_root, lineage=lineage,
            registry_path=goal_admission.registry_path if goal_admission is not None else None)

        def observe_child(item: Mapping[str, Any]) -> None:
            if child_observer is None:
                return
            def record_child() -> None:
                child_observer.observe(item, session_id=session.thread_id,
                                       invocation_id=session.current_turn_id)

            if goal_admission is None:
                record_child()
            else:
                goal_admission.accept_result(record_child)

        result = session.send(
            _prompt(request)
            + "\nUse loopx_operation for context/pending/prepare/inspect/consume/report. "
            "Source conversations are not executor identity. context/pending/inspect do not require consumption. "
            "When the admitted task authorizes proposal preparation and supplies its terms, prepare may run before human confirmation or consume; "
            "prepare writes a canonical proposal, not a domain/external effect. execution_allowed=false is expected for prepare, not a reason to refuse it. "
            "Use pending/inspect to reconcile an existing proposal before preparing another; do not invent missing terms or repeat an already granted preparation approval. "
            "Only domain/external effects require the first successful consume receipt with execution_allowed=true. "
            "Preparation or waiting for human confirmation is not task completion. Report outcomes only with original execution evidence. "
            "Already consumed/unknown effects require evidence reconciliation, never retry. Never treat final-answer prose as an outcome receipt."
            + request_reference_instruction(request_source is not None)
            + ("\nCanonical confirmed operation continuations for this exact binding: "
               + json.dumps(continuations, sort_keys=True)
               + ". Inspect these locators through loopx_operation and consume once before any effect."
               if continuations else ""),
            output_schema=codex_cli_result_schema(request),
            on_event=on_event,
            turn_start_guard=lambda: launch_scope("codex_operation_turn_start"),
            on_native_item=observe_child if child_observer is not None else None,
        )
        return result
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
