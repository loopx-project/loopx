from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..capabilities.benchmark_toolkit import (
    BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
    benchmark_experiment_board_row_key,
    build_benchmark_experiment_board,
    default_benchmark_experiment_board_path,
    preview_benchmark_experiment_board_reconcile,
    preview_benchmark_experiment_board_upsert,
    read_benchmark_experiment_board_rows,
    render_benchmark_experiment_board_markdown,
    upsert_benchmark_experiment_board_row,
)

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]], None
]
OutputFormat = Callable[[argparse.Namespace], str]

BENCHMARK_EXPERIMENT_BOARD_COMMANDS = {
    "experiment-board-show",
    "experiment-board-reconcile",
    "experiment-board-upsert",
}


def _row_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    key = benchmark_experiment_board_row_key(row)
    return (
        key["benchmark_id"],
        key["study_id"],
        key["case_id"],
        key["run_id"],
    )


def _read_json_object(path_text: str) -> dict[str, Any]:
    raw = (
        sys.stdin.read()
        if path_text == "-"
        else Path(path_text).expanduser().read_text(encoding="utf-8")
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("benchmark experiment board JSON input must be an object")
    return payload


def _add_board_location_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--goal-id", required=True)
    parser.add_argument(
        "--project",
        help="Explicit project root. Defaults to the registry goal repository.",
    )
    parser.add_argument("--benchmark-id")
    parser.add_argument("--study-id")
    parser.set_defaults(benchmark_experiment_board_parser=parser)


def register_benchmark_experiment_board_commands(
    benchmark_subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    show_parser = benchmark_subparsers.add_parser(
        "experiment-board-show",
        help="Read the project-local public-safe benchmark experiment board.",
    )
    add_subcommand_format(show_parser)
    _add_board_location_arguments(show_parser)
    show_parser.add_argument(
        "--four-arm-contract-json",
        help=(
            "Optional compact benchmark_four_arm_contract_v0 JSON. When present, "
            "project qualified conditional effects and difference-in-differences."
        ),
    )

    upsert_parser = benchmark_subparsers.add_parser(
        "experiment-board-upsert",
        help="Preview or upsert one compact benchmark run row into the experiment board.",
    )
    add_subcommand_format(upsert_parser)
    _add_board_location_arguments(upsert_parser)
    upsert_parser.add_argument(
        "--row-json",
        required=True,
        help="Compact benchmark_experiment_board_row_v0 JSON path, or - for stdin.",
    )
    upsert_parser.add_argument(
        "--include-board",
        action="store_true",
        help=(
            "Return the full board projection (legacy response) instead of the "
            "default affected row and write receipt. Board filters apply to this projection."
        ),
    )
    upsert_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write project-local domain state. Without this flag, preview only.",
    )

    reconcile_parser = benchmark_subparsers.add_parser(
        "experiment-board-reconcile",
        help="Preview or reconcile provider board shards into the canonical board.",
    )
    add_subcommand_format(reconcile_parser)
    _add_board_location_arguments(reconcile_parser)
    reconcile_parser.add_argument(
        "--source-ledger",
        action="append",
        required=True,
        help=(
            "Provider-owned public-safe experiment-board JSONL path. Repeat for "
            "multiple shards."
        ),
    )
    reconcile_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write project-local domain state. Without this flag, preview only.",
    )


def _board_payload(
    *,
    rows: list[dict[str, Any]],
    benchmark_id: str | None,
    study_id: str | None,
    four_arm_contract: dict[str, Any] | None,
) -> dict[str, Any]:
    return build_benchmark_experiment_board(
        rows,
        benchmark_id=benchmark_id,
        study_id=study_id,
        four_arm_contract=four_arm_contract,
    )


def _render_upsert_receipt(payload: dict[str, object]) -> str:
    return "\n".join([
        "# Benchmark experiment row",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2),
        "```",
    ])


def _reconcile_experiment_board(
    args: argparse.Namespace,
    *,
    ledger_path: Path,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source_rows = [
        row
        for source_path in args.source_ledger
        for row in read_benchmark_experiment_board_rows(source_path)
    ]
    reconciled_rows, reconciliation = preview_benchmark_experiment_board_reconcile(
        rows,
        source_rows,
    )
    if not args.execute:
        return reconciled_rows, {
            **reconciliation,
            "status": "preview_reconciled",
            "write_performed": False,
            "row_count": len(reconciled_rows),
            "path_recorded": False,
            "dry_run": True,
        }

    existing_by_key = {_row_key(row): row for row in rows}
    for row in reconciled_rows:
        if existing_by_key.get(_row_key(row)) != row:
            upsert_benchmark_experiment_board_row(ledger_path, row)
    written_rows = read_benchmark_experiment_board_rows(ledger_path)
    return written_rows, {
        **reconciliation,
        "status": "reconciled",
        "write_performed": bool(
            reconciliation["inserted"] or reconciliation["updated"]
        ),
        "row_count": len(written_rows),
        "path_recorded": False,
        "dry_run": False,
    }


def handle_benchmark_experiment_board_command(
    args: argparse.Namespace,
    *,
    project: Path | None,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.benchmark_command not in BENCHMARK_EXPERIMENT_BOARD_COMMANDS:
        return None

    try:
        if project is None:
            raise ValueError("benchmark project route is required")
        ledger_path = default_benchmark_experiment_board_path(
            project=project,
            goal_id=args.goal_id,
        )
        rows = read_benchmark_experiment_board_rows(ledger_path)
        write: dict[str, Any] | None = None
        if args.benchmark_command == "experiment-board-upsert":
            row = _read_json_object(args.row_json)
            if args.execute:
                receipt = upsert_benchmark_experiment_board_row(ledger_path, row)
                affected_row = receipt["row"]
                if args.include_board:
                    rows = read_benchmark_experiment_board_rows(ledger_path)
                write = {
                    **receipt["write"],
                    "dry_run": False,
                }
            else:
                rows, preview_status = preview_benchmark_experiment_board_upsert(
                    rows, row
                )
                affected_row = next(item for item in rows if _row_key(item) == _row_key(row))
                write = {
                    "status": f"preview_{preview_status}",
                    "write_performed": False,
                    "row_count": len(rows),
                    "path_recorded": False,
                    "dry_run": True,
                }
            if not args.include_board:
                payload = {
                    "ok": True,
                    "schema_version": BENCHMARK_EXPERIMENT_BOARD_ROW_SCHEMA_VERSION,
                    "goal_id": args.goal_id,
                    "path_recorded": False,
                    "row": affected_row,
                    "write": write,
                    "read_hint": (
                        "Use this row and receipt for the update decision. For comparisons, "
                        "use experiment-board-show with the same route and relevant "
                        "--benchmark-id/--study-id; --include-board restores the full upsert response."
                    ),
                }
                print_payload(payload, output_format(args), _render_upsert_receipt)
                return 0
        elif args.benchmark_command == "experiment-board-reconcile":
            rows, write = _reconcile_experiment_board(
                args,
                ledger_path=ledger_path,
                rows=rows,
            )
        payload = _board_payload(
            rows=rows,
            benchmark_id=args.benchmark_id,
            study_id=args.study_id,
            four_arm_contract=(
                _read_json_object(args.four_arm_contract_json)
                if getattr(args, "four_arm_contract_json", None)
                else None
            ),
        )
        payload["goal_id"] = args.goal_id
        payload["path_recorded"] = False
        if write is not None:
            payload["write"] = write
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        args.benchmark_experiment_board_parser.error(
            "invalid benchmark experiment board input"
        )
    print_payload(
        payload, output_format(args), render_benchmark_experiment_board_markdown
    )
    return 0
