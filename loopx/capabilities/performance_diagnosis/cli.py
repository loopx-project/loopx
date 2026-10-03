"""Filesystem/CLI transport only; no profiler execution or Python diagnosis rules."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ...control_plane.effect_runtime import effect_runtime_result

MAX_INPUT_BYTES = 16 * 1024 * 1024
AddFormat = Callable[[argparse.ArgumentParser], None]
OutputFormat = Callable[..., str]
PrintPayload = Callable[[dict[str, Any], str, Callable[[dict[str, Any]], str]], None]


def _load(path: str) -> Any:
    with Path(path).expanduser().open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("profiling input exceeds 16 MiB; select a shorter capture")
    return json.loads(raw)


def register_performance_diagnosis_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], add_format: AddFormat) -> None:
    parser = subparsers.add_parser("performance-diagnosis", help="Plan local profiling and inspect recorded stacks; no execution or upload.")
    actions = parser.add_subparsers(dest="performance_diagnosis_action", required=True)
    plan = actions.add_parser("plan", help="Return profiler argv for an explicitly owned target.")
    plan.add_argument("--tool", required=True)
    plan.add_argument("--command-json", required=True, help="Local JSON argv array; never shell text.")
    plan.add_argument("--output-directory", required=True)
    plan.add_argument("--profiler-executable", help="Optional py-spy executable path.")
    add_format(plan)
    inspect = actions.add_parser("inspect", help="Read local Speedscope or V8 CPU JSON; keep profiles separate.")
    inspect.add_argument("--profile-json", required=True)
    inspect.add_argument("--top", type=int, default=15)
    add_format(inspect)


def _render(payload: dict[str, Any]) -> str:
    # Use the same bounded typed output in both transports; raw profile data is absent.
    return "# Local performance diagnosis\n\n```json\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n```\n"


def handle_performance_diagnosis_command(args: argparse.Namespace, *, output_format: OutputFormat, print_payload: PrintPayload) -> int | None:
    if args.command != "performance-diagnosis":
        return None
    try:
        if args.performance_diagnosis_action == "plan":
            params = {"tool": args.tool, "command": _load(args.command_json),
                      "output_directory": args.output_directory, "platform": sys.platform}
            if args.profiler_executable:
                params["profiler_executable"] = args.profiler_executable
            payload = effect_runtime_result("performance_diagnosis.plan", params)
        else:
            payload = effect_runtime_result("performance_diagnosis.inspect", {
                "profile": _load(args.profile_json), "top": args.top,
            }, large_local_snapshot=True)
    except (ValueError, OSError, RuntimeError) as error:
        print_payload({"ok": False, "error": str(error)}, output_format(args), _render)
        return 1
    print_payload(payload, output_format(args), _render)
    return 0
