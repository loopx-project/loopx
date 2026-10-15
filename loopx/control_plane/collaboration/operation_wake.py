"""Callback IO into the existing operator-granted delegation/Turn owner.

The callback confirms terms; configuration separately grants this one launch.
No new queue, scheduler, approval, result store or public identity issuer.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...chat_action_store import ChatActionStore
from ..turn_driver.codex_cli import load_codex_cli_session
from ..turn_driver.host_binding import turn_host_arg_option
from ..turn_driver.execution_profile import managed_execution_profile
from ..operator_provider import operator_provider_environ
from .operation_handoff import managed_operation_binding_current
from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle


def _original_operation_turn(
    proposal: Mapping[str, Any], *, runtime_root: Path, configuration: Mapping[str, Any],
    binding: Mapping[str, Any], config_digest: str,
) -> dict[str, Any]:
    """Use the normal Turn journal for the original owner's confirmed input.

    This adapter owns no task validation/acceptance or retry queue. In
    particular, a typed wait or an operation outcome does not complete the
    broader Todo. The native host still authenticates and consumes once.
    """
    from ...collaboration_mcp import _pinned_module_command, _pinned_release_environment
    from ...cli_runtime import _build_selected_parser

    parameters = proposal["normalized_parameters"]
    executor = parameters["executor"]
    host_args = binding.get("host_args")
    if (len(json.dumps(binding).encode()) > 16000 or not isinstance(host_args, list)
            or not host_args or not all(isinstance(arg, str) and 0 < len(arg) <= 4096 for arg in host_args)):
        raise ValueError("original operation requires operator host arguments")
    timeout = binding.get("timeout_seconds")
    workspace = Path(binding["workspace"]).resolve(strict=True)
    if (type(timeout) is not int or not 1 <= timeout <= 600
            or not Path(binding["workspace"]).is_absolute() or not workspace.is_dir()):
        raise ValueError("original operation requires a bounded existing workspace")
    turn_id = "operation-wake-" + hashlib.sha256(str(proposal["proposal_id"]).encode()).hexdigest()
    arguments = ["turn", "run-once", "--goal-id", parameters["goal_id"],
        "--agent-id", parameters["agent_id"], "--todo-id", executor["todo_id"],
        "--turn-instance-id", turn_id, "--project", str(workspace), "--scan-root", str(workspace),
        "--timeout-seconds", str(timeout), *host_args,
        "--confirmed-operation-id", str(proposal["proposal_id"]),
        # An authenticated human confirmation is manual input, not a scheduled
        # heartbeat. Keep the configured automatic interval unchanged.
        "--manual-interval-bypass-reason", "Authenticated confirmation of this exact canonical operation",
        "--execute"]
    try:
        selected = _build_selected_parser("turn").parse_args(arguments)
    except SystemExit as exc:
        raise ValueError("invalid original operation Turn arguments") from exc
    if ((selected.goal_id, selected.agent_id, selected.todo_id, selected.turn_instance_id)
            != (parameters["goal_id"], parameters["agent_id"], executor["todo_id"], turn_id)
            or Path(selected.project).resolve() != workspace
            or Path(selected.scan_root).resolve() != workspace or selected.resume_turn_key
            or selected.retry_failed_turn or selected.iteration_context == "fresh"
            or selected.timeout_seconds != timeout or selected.dsh_runner is not None
            or selected.host != executor.get("host", "codex-cli")):
        raise ValueError("original operation launch cannot retarget or replace its Turn")
    host = selected.host
    session = load_codex_cli_session(runtime_root, lineage={
        "goal_id": parameters["goal_id"], "agent_id": parameters["agent_id"],
        "todo_id": executor["todo_id"],
    }, host=host) or {}
    profile = managed_execution_profile(operator_provider_environ(),
        provider=selected.dsh_provider, model=selected.dsh_model,
        reasoning_effort=selected.dsh_reasoning_effort) if host == "dsh" else {
            "model": selected.codex_model, "reasoning_effort": selected.codex_reasoning_effort}
    store = ChatActionStore(runtime_root / "chat" / "actions")
    with collaboration_goal_scope(Path(configuration["registry_path"]),
            goal_id=parameters["goal_id"], agents=(parameters["agent_id"],),
            caller_goal_ref=parameters.get("origin_goal_ref"), require_active=True) as scope:
        decide_collaboration_lifecycle(scope, operation="request_create")
        plan = store.reserve_agent_operation_turn(proposal["proposal_id"],
            binding_current=managed_operation_binding_current(runtime_root, parameters),
            requester_agent_id=configuration["requester_agent_id"], operator_binding=dict(binding),
            turn_launch={"turn_instance_id": turn_id, "binding_id": binding["id"],
                         "configuration_digest": config_digest},
            launch_context={"host": host,
                "operation_tools": bool(selected.dsh_operation_module) if host == "dsh" else selected.codex_operation_tools,
                "iteration_context": selected.iteration_context},
            executor_route={"goal_id": parameters["goal_id"], "agent_id": parameters["agent_id"],
                "todo_id": executor["todo_id"], "host_surface": "loopx-managed-dsh" if host == "dsh" else "loopx-managed-codex",
                "thread_id": session.get("session_id"), "profile_digest": session.get("operation_profile_digest"),
                "model": profile["model"], "reasoning_effort": profile["reasoning_effort"]})
    result = {"execution_allowed": False, "external_write_performed": False,
              "native_start_verified": False, "turn_instance_id": turn_id}
    if not plan["launch_new_turn"]:
        return {**result, "state": "existing_turn"}
    try:
        subprocess.Popen([*_pinned_module_command("loopx.cli"),
            "--registry", configuration["registry_path"], "--runtime-root", str(runtime_root),
            "--format", "json", *arguments], cwd=workspace,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, env=_pinned_release_environment())
    except OSError:
        return {**result, "state": "launch_uncertain", "recovery_required": True}
    return {**result, "state": "turn_requested"}


def require_current_operation_scope(registry_path: Path, parameters: Mapping[str, Any]) -> None:
    """Reuse the Goal lifetime owner before either queueing or native resume."""
    with collaboration_goal_scope(registry_path, goal_id=parameters["goal_id"],
                                  agents=(parameters["agent_id"],), require_active=True) as scope:
        decision = decide_collaboration_lifecycle(
            scope, operation="inbox_observe", record={"goal_ref": parameters.get("origin_goal_ref")}
        )
        if decision.get("kind") == "omit":
            raise ValueError("operation belongs to a different Goal lifetime")


def dispatch_confirmed_operation_wake(
    proposal: Mapping[str, Any], *, runtime_root: Path,
    configuration: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Request one durable delegation; accepted native start is separate evidence.

    Read/compare failures remain a pending canonical authorization. Never resume
    an uncertain launch from a callback replay or include private argv/errors.
    """
    base = {"execution_allowed": False, "external_write_performed": False,
            "native_start_verified": False}
    if configuration is None:
        return {**base, "state": "not_configured"}
    try:
        from ...collaboration_mcp import Delegations
        from .delegation_context import _configuration_path

        parameters = proposal["normalized_parameters"]
        if parameters["goal_id"] != configuration["goal_id"]:
            return {**base, "state": "out_of_scope"}
        config_path = _configuration_path(Path(configuration["project"]), configuration["execution_config"])
        raw_config = json.loads(config_path.read_text())
        if raw_config.get("schema_version") != "loopx_local_delegation_v0":
            raise ValueError("unsupported operation launch configuration")
        bindings = raw_config.get("bindings")
        if not isinstance(bindings, list) or len(bindings) > 100 or any(not isinstance(row, dict) for row in bindings):
            raise ValueError("bounded operation launch bindings required")
        if len({row.get("id") for row in bindings}) != len(bindings):
            raise ValueError("duplicate operation launch binding")
        selected = next((row for row in bindings if row.get("id") == configuration["binding_id"]), None)
        if selected and selected.get("continuation_kind") == "original_operation":
            if config_path.is_relative_to(Path(selected["workspace"]).resolve()):
                raise ValueError("operation launch configuration must be outside the worker workspace")
            return _original_operation_turn(proposal, runtime_root=runtime_root,
                configuration=configuration, binding=selected,
                config_digest=hashlib.sha256(json.dumps({"binding": selected,
                    "registry_path": configuration["registry_path"],
                    "requester_agent_id": configuration["requester_agent_id"]},
                    sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        service = Delegations(
            runtime_root, Path(configuration["registry_path"]),
            configuration["goal_id"], configuration["requester_agent_id"],
            config_path,
        )
        binding = service.binding(configuration["binding_id"], require_active=True)
        require_current_operation_scope(service.registry, parameters)
        if service.config.is_relative_to(Path(binding["workspace"]).resolve()):
            raise ValueError("operation wake configuration must be outside the worker workspace")
        operation_id = "operation-wake-" + hashlib.sha256(
            str(proposal["proposal_id"]).encode()
        ).hexdigest()
        if service.path(operation_id).is_file():
            # Start's lost ACK is resolved by readback, never another spawn.
            # Do not run artifact validators on the callback's latency path or
            # describe a stored accepted bit as current acceptance. CLI read
            # owns result qualification/recovery after this bounded locator.
            from .inbox import _read

            row = _read(service.path(operation_id))
            service._bound(row, require_active=True)
            if row["identity"].get("confirmed_operation_id") != proposal["proposal_id"]:
                raise ValueError("operation wake identity drifted")
            return {**base, "state": "existing_delegation", "operation_id": operation_id}
        projection = ChatActionStore._agent_operation_plan(proposal, action="project")
        if projection["status"] != "authorized_pending" or projection["host_start"] is not None:
            return {**base, "state": "no_new_launch"}
        argv = binding["host_args"]
        host = turn_host_arg_option(argv, "--host")
        dsh = host == "dsh"
        session = load_codex_cli_session(runtime_root, lineage={
            "goal_id": service.goal_id, "agent_id": binding["agent_id"],
            "todo_id": binding["todo_id"],
        }, host=host) or {}
        profile = managed_execution_profile(
            operator_provider_environ(),
            provider=turn_host_arg_option(argv, "--dsh-provider"),
            model=turn_host_arg_option(argv, "--dsh-model"),
            reasoning_effort=turn_host_arg_option(argv, "--dsh-reasoning-effort"),
        ) if dsh else {
            "model": turn_host_arg_option(argv, "--codex-model"),
            "reasoning_effort": turn_host_arg_option(argv, "--codex-reasoning-effort"),
        }
        ChatActionStore._agent_operation_plan(
            proposal, action="wake",
            binding_current=managed_operation_binding_current(runtime_root, parameters),
            launch_context={"host": host,
                            "operation_tools": bool(turn_host_arg_option(argv, "--dsh-operation-module")) if dsh else "--codex-operation-tools" in argv,
                            "iteration_context": turn_host_arg_option(argv, "--iteration-context")},
            executor_route={"goal_id": service.goal_id, "agent_id": binding["agent_id"],
                            "todo_id": binding["todo_id"], "host_surface": "loopx-managed-dsh" if dsh else "loopx-managed-codex",
                            "thread_id": session.get("session_id"),
                            "profile_digest": session.get("operation_profile_digest"),
                            "model": profile["model"],
                            "reasoning_effort": profile["reasoning_effort"]},
        )
        brief = {
            "schema_version": "collaboration_brief_v0",
            "purpose": "Resume the exact human-confirmed canonical operation",
            "context": f"Canonical operation locator: {proposal['proposal_id']}. Read through loopx_operation in the original managed session.",
            "constraints": ["Inspect current canonical terms and consume once before any effect.",
                            "Consumed or unknown results require reconciliation, never resubmission.",
                            "The source conversation is not executor authentication."],
            "inputs": [], "acceptance": ["Preserve the pinned Todo validation and original evidence."],
            "return_requirement": "Report the exact operation outcome through loopx_operation; startup/adoption is not a domain result.",
        }
        result = service.start(configuration["binding_id"], operation_id, brief,
                               confirmed_operation_id=proposal["proposal_id"])
        return {**base, "state": "delegation_requested", "operation_id": operation_id,
                "delegation_status": result["status"],
                "recovery_required": result["recovery_required"]}
    except Exception:  # authorization remains recorded even if launch/readback fails
        return {**base, "state": "blocked", "reason_code": "operation_wake_admission_or_dispatch_failed"}
