"""Pinned Python IO adapter; the original CLI/TS owners still decide each read.

The TS Host supervisor owns deadlines, output bounds and cancellation. This
worker accepts only the already bound read-only Turn, never execute/resume.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from ...cli_runtime import _build_selected_parser, main


class _BoundedOutput(io.StringIO):
    def write(self, text: str) -> int:
        if self.tell() + len(text) > 1_048_576:
            raise ValueError("preview output limit")
        return super().write(text)


def serve() -> None:
    bootstrap = argparse.ArgumentParser()
    bootstrap.add_argument("--registry", type=Path, required=True)
    bootstrap.add_argument("--runtime-root", type=Path, required=True)
    bootstrap.add_argument("--goal-id", required=True)
    bootstrap.add_argument("--agent-id", required=True)
    bootstrap.add_argument("--todo-id", required=True)
    bound = bootstrap.parse_args()
    workspace = Path.cwd().resolve()
    parser = _build_selected_parser("turn")
    for line in sys.stdin:
        request = json.loads(line)
        argv = request["argv"]
        full_argv = ["--registry", str(bound.registry), "--runtime-root",
                     str(bound.runtime_root), "--format", "json", *argv]
        args = parser.parse_args(full_argv)
        if (args.command != "turn" or args.turn_command != "run-once"
                or args.execute or args.resume_turn_key
                or Path(args.registry).resolve() != bound.registry.resolve()
                or Path(args.runtime_root).resolve() != bound.runtime_root.resolve()
                or (args.goal_id, args.agent_id, args.todo_id)
                != (bound.goal_id, bound.agent_id, bound.todo_id)
                or Path(args.project).resolve() != workspace
                or Path(args.scan_root).resolve() != workspace):
            raise ValueError("preview cannot execute or retarget bound work")
        with redirect_stdout(_BoundedOutput()) as output:
            code = main(full_argv)
        value = json.loads(output.getvalue())
        print(json.dumps({"kind": "preview", "id": request["id"],
                          "returncode": code, "value": value}), flush=True)


if __name__ == "__main__":
    serve()
