"""Parse the existing CLI Host request before any Turn execution.

This is CLI argument transport and host-adapter binding, not a decision owner.
The Turn driver retains admission, effects and settlement.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from ..control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission


def controller_worker_launch(args, registry: Path, runtime: Path) -> tuple[list[str], str]:
    """Bind the product's isolated native provider to the caller's Turn configuration."""
    import hashlib
    import math
    import os
    import sys

    if args.timeout_seconds is None or not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        raise ValueError("controller-worker execution requires a finite positive --timeout-seconds")
    project = Path(args.project).expanduser().resolve()
    argv = [sys.executable, "-P", "-m", "loopx.control_plane.turn_driver.controller_worker",
            "--project", str(project), "--registry", str(registry.resolve()), "--runtime", str(runtime.resolve()),
            "--codex-bin", args.codex_bin, "--sandbox", args.codex_sandbox]
    for option, value in (("--model", args.codex_model), ("--effort", args.codex_reasoning_effort)):
        if value is not None:
            argv.extend([option, value])
    task_material = getattr(args, "task_material", None)
    material = task_material or getattr(args, "controller_material", None)
    if material is not None:
        from ..control_plane.turn_driver.controller_worker import read_material

        source = material.expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError("Role material must be a regular UTF-8 file")
        data = read_material(source)
        if task_material and not data.strip():
            raise ValueError("Worker task material is empty")
        argv.extend(["--material", str(source), "--material-sha256", hashlib.sha256(data).hexdigest()])
        if task_material:
            argv.append("--dispatch-task-material")
    # The deadline may shrink on recovery. Model, permissions, workspace and
    # private binding paths belong to the original admission.
    home = str(Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve())
    key = hashlib.sha256(json.dumps([home, *argv]).encode()).hexdigest()
    return [*argv, "--timeout", str(args.timeout_seconds)], key


def prepare_turn_host_arguments(
    args: argparse.Namespace, *,
    native_host_runner: Callable[..., dict[str, Any]] | None,
    native_session_binding_resolver: Callable[[Mapping[str, Any]], dict[str, Any]] | None,
    native_task_validator: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]] | None,
    validator_builder: Callable[..., Callable[..., Mapping[str, Any]]],
) -> tuple[Path, list[str] | None, Callable[..., Mapping[str, Any]] | None]:
    """Validate transport arguments before preparation can write authority state."""
    project = Path(args.project).expanduser().resolve()
    if args.host == "codex-app-server" and (
        native_host_runner is None or native_session_binding_resolver is None
    ):
        raise ValueError("app-server execution requires its original in-process Chat connection")
    if args.host != "codex-app-server" and (
        native_host_runner is not None or native_session_binding_resolver is not None
    ):
        raise ValueError("a native Chat connection cannot replace a selected CLI host")
    if args.host == "generic-cli":
        if not args.host_command_json:
            raise ValueError(
                "generic-cli requires --host-adapter-command-json "
                "(alias --host-command-json)"
            )
        raw_argv = json.loads(args.host_command_json)
        if not isinstance(raw_argv, list) or not all(
            isinstance(item, str) for item in raw_argv
        ):
            raise ValueError(
                "--host-adapter-command-json must be a JSON string array"
            )
    else:
        if args.host_command_json:
            raise ValueError(f"{args.host} does not accept --host-command-json")
        if getattr(args, "host_adapter_request_schema", None):
            raise ValueError("--host-adapter-request-schema is only for generic-cli")
        raw_argv = None
    if native_task_validator is not None and args.host != "codex-app-server":
        raise ValueError("a native task validator cannot replace a selected CLI validator")
    task_validator = native_task_validator
    if args.validation_command_json:
        raw_validation_argv = json.loads(args.validation_command_json)
        if not isinstance(raw_validation_argv, list) or not all(
            isinstance(item, str) for item in raw_validation_argv
        ):
            raise ValueError(
                "--validation-command-json must be a JSON string array"
            )
        if task_validator is None:
            task_validator = validator_builder(
                raw_validation_argv,
                project=project,
                timeout_seconds=args.validation_timeout_seconds,
                failure_recovery_kind=args.validation_failure_kind,
            )
    return project, raw_argv, task_validator


def prepare_turn_host_request(
    args: argparse.Namespace, payload: Mapping[str, Any], *,
    native_host_runner: Callable[..., dict[str, Any]] | None,
    native_session_binding_resolver: Callable[[Mapping[str, Any]], dict[str, Any]] | None,
    native_task_validator: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]] | None,
    validator_builder: Callable[..., Callable[..., Mapping[str, Any]]],
) -> tuple[Path, list[str] | None, Callable[..., Mapping[str, Any]] | None, dict[str, Any]]:
    planned_host = (
        cast(dict[str, Any], payload.get("host")) if isinstance(payload.get("host"), dict) else {}
    )
    if planned_host.get("kind") != args.host:
        raise ValueError("--host must match the journaled LoopX Turn plan")
    project, raw_argv, task_validator = prepare_turn_host_arguments(
        args, native_host_runner=native_host_runner,
        native_session_binding_resolver=native_session_binding_resolver,
        native_task_validator=native_task_validator, validator_builder=validator_builder,
    )
    from ..control_plane.turn_driver.driver import read_turn_execution_context

    return project, raw_argv, task_validator, read_turn_execution_context(payload)


def build_codex_cli_host_runner(
    args: argparse.Namespace, *, runtime_root: Path, project: Path,
    registry_path: Path, goal_ref: Mapping[str, object] | None,
    goal_admission: FirstPartyHostGoalAdmission | None,
    current_launch_decision: Callable[[Path], Mapping[str, Any]],
    run_codex_cli_host: Callable[..., dict[str, Any]],
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Bind the existing Codex adapter; resolve options/admission only at launch."""
    def run_built_in_host(
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        options = {
            "runtime_root": runtime_root,
            "project": project,
            "codex_bin": args.codex_bin,
            "sandbox": args.codex_sandbox,
            "model": args.codex_model,
            "reasoning_effort": args.codex_reasoning_effort,
            "mcp_server": args.codex_mcp_server_json,
            "timeout_seconds": args.timeout_seconds,
        }
        options["goal_admission"] = (
            goal_admission or FirstPartyHostGoalAdmission.for_plan(
                registry_path=registry_path, goal_id=args.goal_id,
                planned_goal_ref=goal_ref, agent_id=args.agent_id,
            )
        ).with_launch_decision(current_launch_decision)
        if getattr(args, "codex_operation_tools", False):
            from ..control_plane.turn_driver.codex_operation_host import (
                run_codex_operation_host,
            )

            if args.timeout_seconds is not None:
                options["timeout_seconds"] = max(1.0, args.timeout_seconds - 5.0)
            return run_codex_operation_host(
                request,
                registry_path=registry_path,
                confirmed_operation_id=getattr(
                    args, "codex_confirmed_operation_id", None
                ),
                source_route=getattr(
                    args, "codex_operation_source_route_json", None
                ),
                operation_request_file=(Path(args.operation_request_file)
                    if getattr(args, "operation_request_file", None) else None),
                operation_request_sha256=getattr(args, "operation_request_sha256", None),
                **options,
            )
        return run_codex_cli_host(request, registry_path=registry_path, **options)

    return run_built_in_host
