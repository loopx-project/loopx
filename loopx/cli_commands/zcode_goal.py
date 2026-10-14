from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from ..zcode_goal_mode.bridge import ZCODE_GOAL_ACTIONS, zcode_goal_operation


AddFormat = Callable[[argparse.ArgumentParser], None]
PrintPayload = Callable[[dict[str, Any], str, Callable[[dict[str, Any]], str]], None]
OutputFormat = Callable[[argparse.Namespace], str]


def register_zcode_goal_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser], add_format: AddFormat) -> None:
    parser = subparsers.add_parser("zcode-goal", help="Explicitly bind and operate one managed ZCode CLI native Goal session.")
    add_format(parser)
    parser.add_argument("action", choices=(*ZCODE_GOAL_ACTIONS, "select-model"))
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--project", help="Assert the canonical Goal project directory.")
    parser.add_argument("--provider-id", help="Existing ZCode provider id for select-model.")
    parser.add_argument("--model-id", help="Existing ZCode model id for select-model.")
    parser.add_argument("--reasoning-level", help="Existing model reasoning level for select-model.")
    parser.add_argument("--zcode-cli", help="ZCode executable or JS bundle; accepted only for bind. Desktop attachment is unsupported.")


def render_zcode_goal_markdown(payload: dict[str, Any]) -> str:
    native = payload.get("native") or {}
    lines = ["# LoopX ZCode native Goal", "", f"- ok: `{payload.get('ok')}`", f"- available: `{payload.get('available', False)}`"]
    for key in ("goal_id", "agent_id", "identity_scope"):
        if payload.get(key):
            lines.append(f"- {key}: `{payload[key]}`")
    if payload.get("error") or payload.get("reason"):
        lines.append(str(payload.get("error") or payload.get("reason")))
    if native:
        lines.extend([f"- native status: `{native.get('status')}`", f"- running: `{native.get('running')}`", f"- session: `{native.get('session_id')}`"])
    if isinstance(native.get("selected_model"), dict):
        selection = native["selected_model"]
        lines.append(f"- selected model: `{selection.get('providerId')}/{selection.get('modelId')}`")
    if payload.get("actions"):
        lines.append("- available actions: " + ", ".join(payload["actions"]))
    return "\n".join(lines)


def handle_zcode_goal_command(args: argparse.Namespace, *, registry_path: Path, print_payload: PrintPayload, output_format: OutputFormat) -> int:
    try:
        action = "select_model" if args.action == "select-model" else args.action
        model_selection: dict[str, Any] | None = None
        if any((args.provider_id, args.model_id, args.reasoning_level)):
            if action != "select_model":
                raise ValueError("Model flags are supported only by select-model.")
            model_selection = {"providerId": args.provider_id, "modelId": args.model_id}
            if args.reasoning_level:
                model_selection["options"] = {"reasoningLevel": args.reasoning_level}
        payload = zcode_goal_operation(
            action=action, registry_path=registry_path, goal_id=args.goal_id, agent_id=args.agent_id,
            project=args.project, cli_path=args.zcode_cli, runtime_root=args.runtime_root, model_selection=model_selection,
        )
    except (ValueError, OSError) as exc:
        payload = {"ok": False, "error": str(exc), "error_code": getattr(exc, "code", "invalid_zcode_goal_request")}
    print_payload(payload, output_format(args), render_zcode_goal_markdown)
    return 0 if payload.get("ok") else 1
