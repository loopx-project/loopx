"""Claude CLI transport for the existing governed Turn transaction.

This provider executes the signed authority and returns a candidate. It never
claims/completes Todos, spends quota or owns cadence. The shared TS process
supervisor and Turn executor retain lifecycle, validation and settlement.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..goals.first_party_host_admission import FirstPartyHostGoalAdmission
from .claude_cli_session import lineage, read_session, store_session
from .host_candidate import (
    ACCEPTED_RESULT_KINDS,
    LOOPX_TURN_HOST_REQUEST_SCHEMA,
    TEXT_LIMITS,
    build_result,
    extract_turn_authority,
    parse_model_json,
    render_prompt,
)
from .host_failure import BuiltInHostError
from .host_process_transport import HostOutputLines, run_host_process


def candidate_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "result_kind": {"type": "string", "enum": sorted(ACCEPTED_RESULT_KINDS)},
            **{
                key: {"type": "string", "maxLength": limit}
                for key, limit in TEXT_LIMITS.items()
            },
        },
        "required": [
            "result_kind",
            "summary",
            "classification",
            "next_action",
            "vision_unchanged_reason",
        ],
        "additionalProperties": False,
    }


def run_claude_cli_host(
    request: Mapping[str, Any],
    *,
    runtime_root: Path,
    project: Path,
    claude_bin: str = "claude",
    model: str | None = None,
    reasoning_effort: str | None = None,
    writable: bool = False,
    timeout_seconds: float = 115,
    goal_admission: FirstPartyHostGoalAdmission | None = None,
) -> dict[str, Any]:
    if request.get("schema_version") != LOOPX_TURN_HOST_REQUEST_SCHEMA:
        raise ValueError("unsupported LoopX Turn host request schema")
    authority = extract_turn_authority(request)
    envelope = request["turn_envelope"]
    identity = lineage(envelope)
    planned_session = request.get("session") or {}
    fresh = (planned_session.get("context_policy") or {}).get("mode") == "fresh"
    binding = read_session(runtime_root, identity, goal_admission)
    binding = None if fresh else binding
    action = planned_session.get("action")
    if action not in {"resume", "start_new"} or (action == "resume") != bool(binding):
        raise ValueError("Claude CLI session binding changed after planning")
    resolved = shutil.which(claude_bin)
    if not resolved:
        raise BuiltInHostError(
            "claude_cli_unavailable", failure_kind="contract_rejected"
        )
    session_id = binding["session_id"] if binding else str(uuid.uuid4())
    tools = "Read,Glob,Grep,Bash,Edit,Write" if writable else "Read,Glob,Grep"
    command = [
        resolved,
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits" if writable else "plan",
        "--tools",
        tools,
        "--allowedTools",
        tools,
        "--disable-slash-commands",
        # A Turn does not inherit another project's MCP writers or native /loop.
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--json-schema",
        json.dumps(candidate_schema(), separators=(",", ":")),
        "--resume" if binding else "--session-id",
        session_id,
    ]
    if model:
        command.extend(["--model", model])
    if reasoning_effort:
        command.extend(["--effort", reasoning_effort])
    environment = {
        **os.environ,
        "LOOPX_TURN_GOAL_ID": identity["goal_id"],
        "LOOPX_TURN_AGENT_ID": identity["agent_id"],
        "LOOPX_TURN_TODO_ID": identity["todo_id"],
        "LOOPX_TURN_WORKSPACE": str(project.resolve()),
    }
    result: dict[str, Any] | None = None
    session_seen = False
    malformed = False

    def consume(line: str) -> None:
        nonlocal result, session_seen, malformed
        try:
            event = json.loads(line)
        except ValueError:
            malformed = True
            return
        if not isinstance(event, dict):
            malformed = True
            return
        observed_id = event.get("session_id")
        if observed_id is not None and observed_id != session_id:
            malformed = True
        elif observed_id == session_id:
            session_seen = True
        if event.get("type") == "result":
            if result is not None:
                malformed = True
            result = event

    lines = HostOutputLines(consume)
    observed = run_host_process(
        command,
        project=project,
        input_text=render_prompt(authority)
        + "\nLoopX owns validation and settlement. Return the candidate only; do not mutate LoopX state or spend quota.\n",
        timeout_seconds=timeout_seconds,
        stdout_limit_bytes=4_194_304,
        on_stdout=lines.feed,
        environment=environment,
    )
    lines.finish()
    if session_seen and not malformed:
        goal_ref = request.get("goal_ref")
        store_session(
            runtime_root,
            identity,
            session_id=session_id,
            goal_ref=goal_ref if isinstance(goal_ref, Mapping) else None,
            goal_admission=goal_admission,
        )
    if observed["outcome"] == "timeout":
        raise BuiltInHostError(
            "claude_cli_timeout",
            failure_kind="executor_timeout",
            recovery_kind="resume_session" if session_seen and not malformed else None,
        )
    if observed["outcome"] == "output_limit":
        raise BuiltInHostError(
            "claude_cli_output_limit", failure_kind="output_budget_exhausted"
        )
    if malformed or not lines.complete:
        raise BuiltInHostError(
            "claude_cli_output_rejected", failure_kind="contract_rejected"
        )
    if not observed["output_complete"]:
        raise BuiltInHostError(
            "claude_cli_output_incomplete",
            failure_kind="transport_lost",
            recovery_kind="resume_session" if session_seen else None,
        )
    if (
        observed["outcome"] != "exited"
        or observed["returncode"] != 0
        or not result
        or result.get("is_error") is not False
    ):
        raise BuiltInHostError("claude_cli_execution_failed")
    candidate = result.get("structured_output")
    if not isinstance(candidate, dict):
        candidate = parse_model_json(str(result.get("result") or ""))
    return build_result(request, candidate, host_name="Claude CLI")
