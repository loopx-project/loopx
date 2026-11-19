from __future__ import annotations

import argparse
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from ..control_plane.operator_provider import operator_provider_environ
from ..paths import select_default_runtime_root


DEFAULT_DSH_HOME = Path.home() / ".local" / "state" / "loopx" / "dsh-home"


def register_dsh_launch_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_format: object,
) -> None:
    del add_format
    parser = subparsers.add_parser(
        "dsh-launch",
        help=(
            "Launch DeepSeek Harness with this machine's stored LoopX provider "
            "credential and no secret in argv or output."
        ),
    )
    parser.add_argument(
        "--dsh-bin",
        help="DSH executable to launch. Defaults to DSH_BIN, then `dsh` on PATH.",
    )
    parser.add_argument(
        "--dsh-home",
        help=(
            "DSH home. Defaults to DSH_HOME, then "
            "~/.local/state/loopx/dsh-home."
        ),
    )
    parser.add_argument(
        "--dsh-agents-home",
        help="Optional DSH_AGENTS_HOME override for an isolated profile or skills root.",
    )
    parser.add_argument(
        "--runtime-root",
        # The main parser owns the same destination. Keep this alias for
        # `dsh-launch --runtime-root ...`, but an omitted alias must not replace
        # a value selected before the subcommand with None.
        default=argparse.SUPPRESS,
        help=(
            "LoopX machine runtime root that owns the provider credential. "
            "Defaults to this machine's selected runtime root."
        ),
    )
    parser.add_argument(
        "dsh_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to DSH. Defaults to `web`.",
    )


def resolve_dsh_launch(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, list[str], dict[str, str]]:
    source_environment = os.environ if environ is None else environ
    configured_bin = str(
        args.dsh_bin or source_environment.get("DSH_BIN") or ""
    ).strip()
    dsh_bin = (
        shutil.which(configured_bin)
        if configured_bin
        else shutil.which("dsh")
    )
    if not dsh_bin:
        raise FileNotFoundError(
            "DSH executable not found; install @deepseek-ai/dsh or pass --dsh-bin"
        )

    dsh_home = Path(
        args.dsh_home
        or source_environment.get("DSH_HOME")
        or DEFAULT_DSH_HOME
    ).expanduser()
    runtime_root = (
        Path(args.runtime_root).expanduser()
        if args.runtime_root
        else select_default_runtime_root()
    )
    environment = operator_provider_environ(
        runtime_root,
        environ=source_environment,
    )
    environment["DSH_HOME"] = str(dsh_home)
    if args.dsh_agents_home:
        environment["DSH_AGENTS_HOME"] = str(
            Path(args.dsh_agents_home).expanduser()
        )

    dsh_args = list(args.dsh_args)
    if dsh_args[:1] == ["--"]:
        dsh_args = dsh_args[1:]
    if not dsh_args:
        dsh_args = ["web"]
    return dsh_bin, dsh_args, environment


def handle_dsh_launch_command(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
    exec_environment: Callable[[str, list[str], Mapping[str, str]], object] | None = None,
) -> int:
    try:
        dsh_bin, dsh_args, environment = resolve_dsh_launch(
            args,
            environ=environ,
        )
    except (FileNotFoundError, OSError, TypeError, ValueError) as error:
        print(f"loopx dsh-launch: {error}", file=sys.stderr)
        return 2

    if exec_environment is None:
        os.execvpe(dsh_bin, [dsh_bin, *dsh_args], environment)
        raise AssertionError("os.execvpe returned unexpectedly")

    exec_environment(dsh_bin, [dsh_bin, *dsh_args], environment)
    return 0
