from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..dsh_goal_mode.turn_host_adapter import DshHostConfig, run_dsh_host
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
                "request_timeout_seconds": max(1.0, args.timeout_seconds - 5.0),
                "dsh_runner": Path(args.dsh_runner) if args.dsh_runner else None,
            }.items()
            if value is not None
        },
    )

    def run(request: Mapping[str, Any]) -> dict[str, Any]:
        return run_dsh_host(request, config=config)

    return run
