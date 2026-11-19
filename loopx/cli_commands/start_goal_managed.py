"""Compose one checked first task through the existing local execution owners."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
from typing import Any

from ..bootstrap import DEFAULT_DOMAIN, bootstrap_project, initialize_first_goal_storage, read_json_if_exists
from ..cli_runtime import add_subcommand_format
from ..control_plane.goals.source_session_registry_state import canonical_digest
from ..control_plane.projects.identity import GoalCreationConflictError, default_goal_id, derive_goal_display_name
from ..control_plane.quota.turn_envelope import COMPLETE_TURN_HOST_REQUEST_SCHEMA
from ..control_plane.todos.mutation_api import add_goal_todo, update_goal_todo
from ..control_plane.turn_driver.command_validation import build_loopx_turn_command_validator, normalize_host_argv
from ..control_plane.turn_driver.journal_store import find_loopx_turn_key_by_instance
from ..kunluncode_goal_mode.guards import native_controller_cli_write_block
from ..paths import resolve_runtime_root
from ..registry import find_registry_goal
from .turn import handle_turn_command
from .turn_host_request import prepare_turn_host_arguments
from .turn_registration import register_turn_commands
from .turn_rendering import render_loopx_turn_execution_markdown


def _first_turn_args(
    args: argparse.Namespace, project: Path, goal_id: str, agent_id: str,
) -> argparse.Namespace:
    if (
        args.host_surface not in {None, "shell"} or args.thread_id or args.new_peer
        or args.available_capabilities or args.capability_route or args.fine_grained
        or args.include_command_pack_detail or args.slash_command_arguments is not None
    ):
        raise ValueError("start-goal --execute takes a local --goal-text and an independent check")
    host = args.execution_host or "generic-cli"
    if not args.validation_command_json:
        raise ValueError("start-goal --execute requires --validation-command-json")
    host_args = []
    if host == "generic-cli":
        if not args.host_adapter_command_json:
            raise ValueError("Select --host codex-cli or supply --host-adapter-command-json for a generic adapter.")
        host_args = [
            "--host-adapter-request-schema", COMPLETE_TURN_HOST_REQUEST_SCHEMA,
            "--host-adapter-command-json", args.host_adapter_command_json,
        ]
    elif args.host_adapter_command_json is not None:
        raise ValueError("An adapter command requires --host generic-cli.")
    parser = argparse.ArgumentParser(prog="loopx", add_help=False)
    register_turn_commands(parser.add_subparsers(dest="command"), add_subcommand_format)
    return parser.parse_args([
        "turn", "run-once", "--goal-id", goal_id, "--agent-id", agent_id,
        "--project", str(project), "--host", host, *host_args,
        "--validation-command-json", args.validation_command_json, "--no-global-sync", "--execute",
    ])


def _first_task_executable_available(program: str, project: Path) -> bool:
    if os.name == "nt":
        # CreateProcess owns search for relative names and can use system
        # locations outside PATH. Check explicit absolute paths here.
        return not Path(program).is_absolute() or shutil.which(program) is not None
    if os.path.dirname(program):
        return shutil.which(os.path.join(project, program)) is not None
    project_path = os.pathsep.join(os.path.join(project, entry) for entry in os.get_exec_path())
    return shutil.which(program, path=project_path) is not None


def _register_first_goal(
    *,
    registry_path: Path,
    runtime_root: Path,
    project: Path,
    goal_id: str,
    agent_id: str,
    objective: str,
    display_name: str | None,
    operation_id: str,
) -> None:
    if not registry_path.exists():
        try:
            receipt = bootstrap_project(
                project=project, registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, objective=objective, display_name=display_name,
                domain=DEFAULT_DOMAIN, role="controller", parent_goal_id=None,
                state_file=None, goal_doc=None, adapter_kind="generic_project_goal_v0",
                adapter_status="connected", next_probe=None, spawn_allowed=False, max_children=0,
                allowed_domains=[], write_scope=[], force=False, dry_run=False, sync_global=False,
                creation_operation_id=operation_id, initial_agent_id=agent_id,
            )
            if not receipt.get("ok"):
                raise RuntimeError(str(receipt.get("error") or "The Goal could not be registered"))
            return
        except GoalCreationConflictError:
            # A concurrent publication may have completed the same operation.
            pass
    registry = read_json_if_exists(registry_path)
    goal = find_registry_goal(registry, goal_id)
    if goal is None or goal.get("creation_operation_id") != operation_id or goal.get("repo") != str(project):
        raise ValueError("This project is already connected. Use its original start request or inspect the existing goal.")
    initialize_first_goal_storage(runtime_root, goal, registry_path=registry_path, objective=objective)


def execute_managed_start(
    args: argparse.Namespace, *, runtime_root_arg: str | None, explicit_registry: Path | None,
) -> dict[str, Any]:
    project = Path(args.project).expanduser().resolve()
    objective = (args.goal_text or "").strip()
    if not project.is_dir() or not objective:
        raise ValueError("A project directory and non-empty goal text are required")
    registry_path = project / ".loopx/registry.json"
    if not registry_path.resolve().is_relative_to(project) or (
        explicit_registry is not None and explicit_registry.resolve() != registry_path.resolve()
    ):
        raise ValueError("start-goal --execute uses the selected project's local registry")
    goal_id = args.goal_id or default_goal_id(project)
    agent_id = args.agent_id or "local-worker"
    turn_args = _first_turn_args(args, project, goal_id, agent_id)
    blocked = native_controller_cli_write_block(turn_args)
    if blocked is not None:
        return blocked
    _, host_argv, _ = prepare_turn_host_arguments(
        turn_args, native_host_runner=None, native_session_binding_resolver=None,
        native_task_validator=None, validator_builder=build_loopx_turn_command_validator,
    )
    host_argv = normalize_host_argv(host_argv) if host_argv is not None else None
    validator_argv = json.loads(args.validation_command_json)
    registry = read_json_if_exists(registry_path)
    if not registry:
        if host_argv is None and shutil.which(turn_args.codex_bin) is None:
            raise ValueError("The Codex CLI executable is unavailable. Put codex on PATH and retry.")
        if host_argv is not None and not _first_task_executable_available(host_argv[0], project):
            raise ValueError("The task runner executable is unavailable. Check --host-adapter-command-json and retry.")
        if not _first_task_executable_available(validator_argv[0], project):
            raise ValueError("The independent checker executable is unavailable. Check --validation-command-json and retry.")
    runtime_root = resolve_runtime_root(
        registry or {"common_runtime_root": str(project / ".loopx/runtime")},
        runtime_root_arg, registry_path=registry_path,
    ).resolve()
    display_name = args.display_name or derive_goal_display_name(objective)
    launch_identity = (
        {"adapter": host_argv} if host_argv is not None
        else {"host": turn_args.host, "binary": turn_args.codex_bin, "sandbox": turn_args.codex_sandbox}
    )
    digest = canonical_digest({
        "project": str(project), "runtime_root": str(runtime_root),
        "goal_id": goal_id, "agent_id": agent_id, "goal": objective, "title": display_name,
        **launch_identity, "validator": validator_argv,
    })
    operation_id = "start-" + digest.removeprefix("sha256:")[:32]
    _register_first_goal(
        registry_path=registry_path, runtime_root=runtime_root, project=project,
        goal_id=goal_id, agent_id=agent_id, objective=objective,
        display_name=display_name, operation_id=operation_id,
    )
    todo = add_goal_todo(
        registry_path=registry_path, runtime_root_arg=str(runtime_root),
        goal_id=goal_id, role="agent", text=objective, priority="P1",
        claimed_by=agent_id, task_class="advancement_task", action_kind="advance",
        operation_id=f"{operation_id}:first-task",
    )
    if not todo.get("ok") or not todo.get("todo_id"):
        raise RuntimeError("The goal was registered. Retry the same start request to recover its first task.")
    todo_id = str(todo["todo_id"])
    resume = find_loopx_turn_key_by_instance(
        runtime_root, goal_id=goal_id, agent_id=agent_id, turn_instance_id=operation_id,
    )
    if resume:
        turn_args.resume_turn_key = resume
    else:
        claimed = update_goal_todo(
            registry_path=registry_path, runtime_root_arg=str(runtime_root),
            goal_id=goal_id, todo_id=todo_id, claimed_by=agent_id, agent_id=agent_id,
            claim_only=True, manage_execution=True, claim_operation_id=f"{operation_id}:first-claim",
        )
        if not claimed.get("ok"):
            raise RuntimeError(str(claimed.get("error") or "The first task could not be claimed"))
        turn_args.todo_id = todo_id
        turn_args.turn_instance_id = operation_id
    result: dict[str, Any] = {}
    handle_turn_command(
        turn_args, registry_path=registry_path, runtime_root_arg=str(runtime_root),
        output_format=lambda _args: "json", print_payload=lambda payload, *_: result.update(payload),
    )
    return {**result, "goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
            "turn_instance_id": operation_id}


def render_managed_start_markdown(payload: dict[str, object]) -> str:
    kind = payload.get("result_kind")
    if payload.get("status") != "committed" or kind not in ("validated_completion", "validated_progress"):
        return render_loopx_turn_execution_markdown(payload)
    line = (
        "Task completed and checked."
        if kind == "validated_completion"
        else "Checked progress saved."
    )
    evidence = payload.get("host_evidence")
    refs = evidence.get("work_evidence") if isinstance(evidence, dict) else None
    if not isinstance(refs, list):
        return line
    return "\n".join([line, "", *(f"- {ref}" for ref in refs if isinstance(ref, str))])
