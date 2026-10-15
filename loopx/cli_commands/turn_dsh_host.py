from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..dsh_goal_mode.turn_host_adapter import DshHostConfig, run_dsh_host
from ..control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from ..control_plane.turn_driver.execution_profile import managed_execution_profile
from ..control_plane.turn_driver.host_binding import dsh_output_token_budget
from ..dsh_goal_mode.turn_host_adapter import resolve_dsh_home
from ..control_plane.operator_credential import (
    OPERATOR_CREDENTIAL_ENV_VARS,
    OPERATOR_ENDPOINT_ENV_VAR,
)


DshHostRunner = Callable[[Mapping[str, Any]], dict[str, Any]]


def build_dsh_host_runner(
    args: argparse.Namespace,
    *,
    workspace: Path,
    environ: Mapping[str, str],
) -> DshHostRunner:
    """Bind CLI-owned DSH options to the in-process Turn host adapter."""
    # The Turn planner and this host launch receive the same already-resolved
    # environment. Forward only the operator provider pair: unrelated service
    # variables are not part of the child host's credential contract.
    credential = {
        name: str(environ[name])
        for name in (*OPERATOR_CREDENTIAL_ENV_VARS, OPERATOR_ENDPOINT_ENV_VAR)
        if environ and environ.get(name)
    }
    config = DshHostConfig(
        workspace=workspace,
        env=credential,
        **{
            key: value
            for key, value in {
                "provider": args.dsh_provider,
                "model": args.dsh_model,
                "reasoning_effort": args.dsh_reasoning_effort,
                "max_tokens": args.dsh_max_tokens,
                "dsh_home": Path(args.dsh_home) if args.dsh_home else None,
                "cordis": Path(args.dsh_cordis) if args.dsh_cordis else None,
                "runtime_bin": args.dsh_runtime_bin,
                "request_timeout_seconds": None if args.timeout_seconds is None else max(1.0, args.timeout_seconds - 5.0),
                "dsh_runner": Path(args.dsh_runner) if args.dsh_runner else None,
            }.items()
            if value is not None
        },
    )

    def run(request: Mapping[str, Any]) -> dict[str, Any]:
        return run_dsh_host(request, config=config)

    return run


def build_dsh_operation_host_runner(
    args: argparse.Namespace,
    *,
    runtime_root: Path,
    registry_path: Path,
    workspace: Path,
    environ: Mapping[str, str],
    goal_admission: FirstPartyHostGoalAdmission | None,
) -> DshHostRunner:
    """Bind explicit native transport options; no runner or ambient actor hook."""
    from ..control_plane.turn_driver.dsh_operation_host import run_dsh_operation_host

    profile = managed_execution_profile(
        environ, provider=args.dsh_provider, model=args.dsh_model,
        reasoning_effort=args.dsh_reasoning_effort,
    )
    credential = {
        name: str(environ[name])
        for name in (*OPERATOR_CREDENTIAL_ENV_VARS, OPERATOR_ENDPOINT_ENV_VAR)
        if environ.get(name)
    }
    options = {
        "runtime_root": runtime_root, "registry_path": registry_path,
        "project": workspace,
        "dsh_home": resolve_dsh_home(workspace, Path(args.dsh_home) if args.dsh_home else None),
        "operation_module": Path(args.dsh_operation_module),
        "provider": profile["provider"], "model": profile["model"],
        "reasoning_effort": profile["reasoning_effort"],
        "max_tokens": dsh_output_token_budget(args.dsh_max_tokens)["max_tokens"],
        "timeout_seconds": 120 if args.timeout_seconds is None else max(1.0, args.timeout_seconds - 5.0),
        "runtime_bin": Path(args.dsh_runtime_bin) if args.dsh_runtime_bin else None,
        "cordis": Path(args.dsh_cordis) if args.dsh_cordis else None,
        "source_route": args.codex_operation_source_route_json,
        "confirmed_operation_id": args.codex_confirmed_operation_id,
        "goal_admission": goal_admission, "env": credential,
    }

    def run(request: Mapping[str, Any]) -> dict[str, Any]:
        return run_dsh_operation_host(request, **options)

    return run
