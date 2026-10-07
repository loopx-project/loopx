from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ...file_lock import exclusive_file_lock
from .core import (
    default_registry_path,
    list_connectors,
    load_connector_registry,
    rank_connectors,
    record_connector_use,
    register_connector,
    save_connector_registry,
)


PrintPayload = Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None]
AddFormat = Callable[[argparse.ArgumentParser], None]
FormatSelector = Callable[..., str]


def _render_list_markdown(payload: dict[str, object]) -> str:
    lines = ["# LoopX Connector Registry", ""]
    summary = payload.get("summary", {})
    lines.append(f"- supported={summary.get('supported')} pending={summary.get('pending')} blocked={summary.get('blocked')}")
    lines.append("")
    lines.append("| id | name | layer | kind | status | value | score | ok/fail |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in payload.get("ranked", []):
        u = row.get("usage", {})
        lines.append(
            f"| {row.get('id')} | {row.get('name')} | {row.get('layer')} | "
            f"{row.get('kind')} | {row.get('status')} | {row.get('value_tier')} | "
            f"{row.get('score')} | {u.get('ok', 0)}/{u.get('fail', 0)} |"
        )
    return "\n".join(lines)


def _render_rank_markdown(payload: dict[str, object]) -> str:
    lines = ["# LoopX Connector Value Rank", "", "| # | id | value | score | uses |"]
    lines.append("| --- | --- | --- | --- | --- |")
    for i, row in enumerate(payload.get("ranked", []), start=1):
        u = row.get("usage", {})
        lines.append(
            f"| {i} | {row.get('id')} | {row.get('value_tier')} | {row.get('score')} | {u.get('count', 0)} |"
        )
    return "\n".join(lines)


def _add_registry_path(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", type=Path)


def register_connector_commands(sub, add_subcommand_format: AddFormat) -> None:
    parser = sub.add_parser("connector", help="Unified public-connector registry")
    actions = parser.add_subparsers(dest="connector_action", required=True)

    p_list = actions.add_parser("list", help="List connectors with status, layer, value, usage")
    _add_registry_path(p_list)
    add_subcommand_format(p_list)

    p_rank = actions.add_parser("rank", help="Show the value-priority ranking")
    _add_registry_path(p_rank)
    add_subcommand_format(p_rank)

    p_reg = actions.add_parser("register", help="Register or update a connector")
    p_reg.add_argument("connector_id")
    p_reg.add_argument("--name")
    p_reg.add_argument("--layer", choices=["L0", "L1", "L2", "L3", "L4", "L5"])
    p_reg.add_argument("--kind")
    p_reg.add_argument("--status", choices=["supported", "pending", "blocked"])
    p_reg.add_argument("--credential")
    p_reg.add_argument("--value-tier", choices=["P0", "P1", "P2"])
    p_reg.add_argument("--purpose")
    p_reg.add_argument("--blocker")
    _add_registry_path(p_reg)
    add_subcommand_format(p_reg)

    p_use = actions.add_parser("use", help="Record a connector call")
    p_use.add_argument("connector_id")
    p_use.add_argument("--fail", action="store_true")
    p_use.add_argument("--ms", type=int, default=0,
                       help="Non-negative elapsed time in milliseconds (default: 0)")
    p_use.add_argument("--note")
    _add_registry_path(p_use)
    add_subcommand_format(p_use)


def handle_connector_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "connector":
        return None
    action = getattr(args, "connector_action", None)
    if action == "list":
        state = load_connector_registry(getattr(args, "path", None))
        print_payload(list_connectors(state), output_format(args), _render_list_markdown)
        return 0
    if action == "rank":
        state = load_connector_registry(getattr(args, "path", None))
        print_payload({"schema_version": "connector_registry_v1", "ok": True,
                       "ranked": rank_connectors(state)}, output_format(args), _render_rank_markdown)
        return 0
    if action == "register":
        lock_path = (args.path or default_registry_path()).expanduser().resolve(strict=False)
        with exclusive_file_lock(lock_path, operation="connector-register"):
            state = load_connector_registry(args.path)
            result = register_connector(
                state, args.connector_id, name=args.name, layer=args.layer, kind=args.kind,
                status=args.status, credential=args.credential, value_tier=args.value_tier,
                purpose=args.purpose, blocker=args.blocker,
            )
            merged = {**state, "connectors": result["connectors"], "usage": result["usage_map"],
                      "updated_at": result["updated_at"]}
            save_connector_registry(merged, args.path)
        print_payload(result, output_format(args), lambda p: f"registered: {args.connector_id}")
        return 0
    if action == "use":
        lock_path = (args.path or default_registry_path()).expanduser().resolve(strict=False)
        with exclusive_file_lock(lock_path, operation="connector-use"):
            state = load_connector_registry(args.path)
            try:
                result = record_connector_use(state, args.connector_id, ok=not args.fail,
                                              ms=args.ms, manual_note=args.note)
            except (KeyError, ValueError) as exc:
                print_payload({"ok": False, "error": str(exc)}, output_format(args),
                              lambda p: f"error: {p['error']}")
                return 1
            merged = {**state, "connectors": result["connectors"], "usage": result["usage_map"],
                      "updated_at": result["updated_at"]}
            save_connector_registry(merged, args.path)
        print_payload(result, output_format(args), lambda p: f"recorded {p['connector_id']} ok={not args.fail}")
        return 0
    return None
