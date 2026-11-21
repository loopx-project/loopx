"""CLI transport for the optional Workbench's TypeScript lifecycle owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from ..control_plane.runtime.node_probe import probe_node


def register_workbench_command(subparsers, add_format) -> None:
    parser = subparsers.add_parser("workbench", help="Start, inspect or stop the optional LoopX Workbench.")
    actions = parser.add_subparsers(dest="workbench_action", required=True)
    for action in ("start", "status", "stop"):
        child = actions.add_parser(action)
        child.add_argument("--state-dir", help="Reuse a Workbench state directory (or WORKBENCH_STATE_DIR).")
        if action == "start":
            child.add_argument("--autostart", action="store_true", help="Enable current-user login startup and failure recovery (macOS/Linux).")
            child.add_argument("--workbench-dir", help="Built apps/workbench directory (or LOOPX_WORKBENCH_DIR).")
            child.add_argument("--env-file", help="Existing Workbench .env; defaults to STATE/.env.")
            child.add_argument("--port", type=int, help="HTTP port on first start (default: WORKBENCH_PORT or 8796).")
        if action == "stop":
            child.add_argument("--disable-autostart", action="store_true", help="Stop and remove this instance's login-start registration; keep its data.")
            child.add_argument("--force", action="store_true", help="Also interrupt running agent turns.")
        add_format(child)


def render_workbench(payload: dict) -> str:
    lines = [f"LoopX Workbench: {payload.get('state', 'unavailable')}"]
    for field in ("url", "access_url", "state_dir", "data_dir", "log", "error", "next_action"):
        if payload.get(field):
            lines.append(f"{field}: {payload[field]}")
    core = payload.get("core")
    if core:
        lines.append(f"Core: {'reachable' if core['reachable'] else 'unavailable (Goal features need Core)'} — {core['url']}")
    if auto := payload.get("autostart"):
        state = "unknown" if auto.get("enabled") is None else "enabled" if auto["enabled"] else "disabled"
        lines.append(f"Login autostart: {state}" + (f" ({auto['manager']})" if auto.get("manager") else ""))
        if auto.get("error"):
            lines.append(f"Autostart: {auto['error']}")
    if payload.get("access_url"):
        lines.append("The login link is private; do not share it in public logs.")
    return "\n".join(lines)


def handle_workbench_command(args, *, print_payload, output_format) -> int:
    probe = probe_node()
    if not probe.ready:
        payload = {"ok": False, "error": probe.failure_message, "next_action": probe.recommended_action}
    else:
        root = Path(__file__).resolve().parents[2]
        request = {
            "action": args.workbench_action,
            "stateDir": args.state_dir,
            "appDir": getattr(args, "workbench_dir", None),
            "envFile": getattr(args, "env_file", None),
            "port": getattr(args, "port", None),
            "force": getattr(args, "force", False),
            "autostart": getattr(args, "autostart", False),
            "disableAutostart": getattr(args, "disable_autostart", False),
            "sourceRoot": str(root),
        }
        entry = root / "loopx/control_plane/runtime/workbench.ts"
        try:
            result = subprocess.run(
                [probe.executable, "--no-warnings", "--experimental-strip-types", str(entry)],
                input=json.dumps(request), capture_output=True, text=True, encoding="utf-8",
                timeout=65, env=os.environ.copy(), check=False,
            )
            payload = json.loads(result.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            payload = {"ok": False, "error": f"Workbench lifecycle could not answer: {type(exc).__name__}"}
    print_payload(payload, output_format(args), render_workbench)
    return 0 if payload.get("ok") else 1
