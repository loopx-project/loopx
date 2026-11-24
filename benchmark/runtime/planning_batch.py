"""Benchmark planning-batch CLI reusing LoopX's general planning-batch owner."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import cast

from loopx.control_plane.goals.planning_batch import (
    OBJECTIVE_MAX_CHARS,
    OPAQUE_ID_MAX_CHARS,
    OPAQUE_ID_PATTERN,
    PlanningBatchCommandError,
    REQUIREMENTS_MAX_ITEMS,
    REQUIREMENT_DESCRIPTION_MAX_CHARS,
    SOURCE_SUMMARY_MAX_CHARS,
    TODOS_MAX_ITEMS,
    CliPlanningBatchBackend,
    apply_planning_batch,
    planning_batch_schema as core_planning_batch_schema,
    render_planning_batch_guidance,
    validate_planning_batch_proposal,
)
from loopx.control_plane.todos.contract import TODO_ACTION_KIND_PATTERN

from .planning import task_plan_packet

__all__ = [
    "PlanningBatchCommandError",
    "apply_initial_plan",
    "main",
    "planning_batch_guidance",
    "planning_batch_schema",
]


def planning_batch_schema() -> dict[str, object]:
    """Return the benchmark checkpoint proposal schema backed by the general LoopX owner."""
    return core_planning_batch_schema(
        require_input_digest=True,
        allow_product_identities=False,
    )


def planning_batch_guidance() -> str:
    command = shlex.join([sys.executable, "-m", "benchmark.runtime.planning_batch"])
    return render_planning_batch_guidance(
        command,
        save_location_hint="save a JSON proposal alongside LOOPX_PLANNING_RESULT",
        require_input_digest=True,
    )


def apply_initial_plan(env: dict[str, str], proposal: dict[str, object]) -> dict[str, object]:
    if env.get("LOOPX_TASK_STAGE") != "plan":
        raise ValueError("Planning batch requires the runner's planning stage")
    validated = validate_planning_batch_proposal(
        proposal,
        require_input_digest=True,
        allow_product_identities=False,
    )
    from .worker import loopx_command

    cli = loopx_command(env)
    backend = CliPlanningBatchBackend(
        cli=cli,
        cwd=env["LOOPX_PROJECT"],
        env=env,
        packet_reader=lambda: task_plan_packet(env, cli),
        project=env["LOOPX_PROJECT"],
    )
    result = apply_planning_batch(
        proposal=validated,
        backend=backend,
        execute=True,
        require_input_digest=True,
        allow_product_identities=False,
        id_prefix="benchmark-plan",
    )
    return {
        "input_digest": str(result["input_digest"]),
        "status": str(result["status"]),
        "todo_ids": cast(list[str], result["todo_ids"]),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.runtime.planning_batch",
        description="Submit an initial planning batch proposal (outcome declaration + ordered advancement Todos).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Proposal JSON schema summary (text limits in UTF-16 code units; non-BMP emoji count as 2):\n"
            "  Required root fields: input_digest, expected_provider_revision, outcome, todos\n"
            f"  outcome.objective: non-empty string (max {OBJECTIVE_MAX_CHARS} UTF-16 code units)\n"
            f"  outcome.requirements: 1..{REQUIREMENTS_MAX_ITEMS} items of {{id, description}} "
            f"(description max {REQUIREMENT_DESCRIPTION_MAX_CHARS} UTF-16 code units)\n"
            f"  outcome.source: {{ref, summary}} (summary max {SOURCE_SUMMARY_MAX_CHARS} UTF-16 code units)\n"
            f"  Opaque IDs (outcome.requirements[*].id, outcome.source.ref): 1..{OPAQUE_ID_MAX_CHARS} UTF-16 code units "
            f"matching {OPAQUE_ID_PATTERN.pattern} (no '/' paths or spaces)\n"
            f"  todos: 1..{TODOS_MAX_ITEMS} items of {{text, action_kind, target_key?}}\n"
            f"  action_kind: lowercase ASCII token matching {TODO_ACTION_KIND_PATTERN.pattern}\n\n"
            "Pass --schema to print the full machine-readable JSON schema and example."
        ),
    )
    parser.add_argument(
        "proposal",
        nargs="?",
        type=Path,
        help="Path to the planning proposal JSON file.",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print the complete JSON schema, field limits, and an example proposal, then exit 0.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.schema:
        print(json.dumps(planning_batch_schema(), indent=2))
        return 0
    if args.proposal is None:
        parser.error("the following arguments are required: proposal (or pass --schema)")
    try:
        proposal = json.loads(args.proposal.read_text(encoding="utf-8"))
        result = apply_initial_plan(dict(os.environ), proposal)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        payload: dict[str, object] = {"ok": False, "error": str(exc)}
        if isinstance(exc, PlanningBatchCommandError) and exc.payload:
            payload.update(exc.payload)
        print(json.dumps(payload))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
