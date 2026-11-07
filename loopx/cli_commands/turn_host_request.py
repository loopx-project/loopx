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


def prepare_turn_host_request(
    args: argparse.Namespace, payload: Mapping[str, Any], *,
    native_host_runner: Callable[..., dict[str, Any]] | None,
    native_session_binding_resolver: Callable[[Mapping[str, Any]], dict[str, Any]] | None,
    native_task_validator: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]] | None,
    validator_builder: Callable[..., Callable[..., Mapping[str, Any]]],
) -> tuple[Path, list[str] | None, Callable[..., Mapping[str, Any]] | None, dict[str, Any]]:
    project = Path(args.project).expanduser().resolve()
    planned_host = (
        cast(dict[str, Any], payload.get("host")) if isinstance(payload.get("host"), dict) else {}
    )
    if planned_host.get("kind") != args.host:
        raise ValueError("--host must match the journaled LoopX Turn plan")
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
    envelope = (
        cast(dict[str, Any], payload.get("turn_envelope"))
        if isinstance(payload.get("turn_envelope"), dict)
        else {}
    )
    return project, raw_argv, task_validator, envelope


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
            "timeout_seconds": None if args.timeout_seconds is None else max(1.0, args.timeout_seconds - 5.0),
        }
        options["goal_admission"] = (
            goal_admission or FirstPartyHostGoalAdmission.for_plan(
                registry_path=registry_path, goal_id=args.goal_id,
                planned_goal_ref=goal_ref,
            )
        ).with_launch_decision(current_launch_decision)
        if getattr(args, "codex_operation_tools", False):
            from ..control_plane.turn_driver.codex_operation_host import (
                run_codex_operation_host,
            )

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
