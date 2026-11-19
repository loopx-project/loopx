"""`loopx dash` — start the live single-page session dash panel.

Run ``loopx dash`` inside a project directory to open one single-page panel
that tracks agent session task progress/status from public-safe projections and
auto-refreshes in place. ``loopx dash generate`` remains available for a
one-shot static HTML snapshot.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..dash_server import (
    DEFAULT_DASH_HOST,
    DEFAULT_DASH_PORT,
    DEFAULT_DASH_REFRESH_SECONDS,
    serve_dash,
)
from ..presentation.projections.session_dash import (
    build_session_dash_projection,
)
from ..presentation.renderers.session_dash_html import render_session_dash_html
from ..presentation.public_safety import scan_public_boundary_text
from ..status import collect_status


PrintPayload = Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]

DASH_COMMAND = "dash"


def _render_markdown(payload: dict[str, object]) -> str:
    lines = ["# LoopX Session Dash", ""]
    for key in (
        "ok",
        "mode",
        "focus_goal_id",
        "url",
        "refresh_seconds",
        "html_path",
        "html_bytes",
        "boundary_ok",
        "write_performed",
    ):
        if key in payload:
            lines.append(f"- **{key}**: `{payload[key]}`")
    projection = payload.get("projection")
    if isinstance(projection, dict):
        overview = projection.get("overview") or {}
        lines.extend(
            [
                f"- **schema_version**: `{projection.get('schema_version')}`",
                f"- **mode**: `{projection.get('mode')}`",
                f"- **sessions**: `{overview.get('session_count', 0)}`",
                f"- **goals**: `{overview.get('goal_count', 0)}`",
                f"- **goals_by_status**: `{overview.get('goals_by_status')}`",
                f"- **open_agent_todos**: `{overview.get('open_agent_todos', 0)}`",
                f"- **open_user_todos**: `{overview.get('open_user_todos', 0)}`",
                f"- **done_todos**: `{overview.get('done_todos', 0)}`",
                f"- **runs_24h**: `{overview.get('runs_24h', 0)}`",
            ]
        )
    return "\n".join(lines) + "\n"


def register_dash_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        DASH_COMMAND,
        help="Start the live single-page session dash panel (or generate a static snapshot).",
    )
    parser.add_argument(
        "--goal-id",
        default=None,
        help="Narrow the panel to one goal. Defaults to the whole fleet.",
    )
    parser.add_argument("--host", default=DEFAULT_DASH_HOST, help="Loopback bind host.")
    parser.add_argument("--port", type=int, default=DEFAULT_DASH_PORT)
    parser.add_argument("--refresh-seconds", type=int, default=DEFAULT_DASH_REFRESH_SECONDS)
    parser.add_argument("--verbose", action="store_true", help="Print HTTP request logs.")
    sub = parser.add_subparsers(dest="dash_command")

    serve = sub.add_parser(
        "serve",
        help="Start the loopback single-page panel; auto-refreshes in place.",
    )
    add_subcommand_format(serve)
    # Inherit options parsed by `dash`; only explicit child options override them.
    serve.add_argument(
        "--goal-id",
        default=argparse.SUPPRESS,
        help="Narrow the panel to one goal. Defaults to the whole fleet.",
    )
    serve.add_argument("--host", default=argparse.SUPPRESS, help="Loopback bind host.")
    serve.add_argument("--port", type=int, default=argparse.SUPPRESS)
    serve.add_argument("--refresh-seconds", type=int, default=argparse.SUPPRESS)
    serve.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS, help="Print HTTP request logs.")

    generate = sub.add_parser(
        "generate",
        help="Render one self-contained HTML snapshot from public-safe projections.",
    )
    add_subcommand_format(generate)
    generate.add_argument(
        "--goal-id",
        default=argparse.SUPPRESS,
        help="Narrow the snapshot to one goal. Defaults to the whole fleet.",
    )
    generate.add_argument("--out", default=None, help="Output HTML path. Prints to stdout when omitted.")
    generate.add_argument(
        "--no-boundary-scan",
        action="store_true",
        help="Skip the public/private boundary scan before reporting success.",
    )


def handle_dash_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int | None:
    if args.command != DASH_COMMAND:
        return None
    command = str(args.dash_command or "serve")
    if command == "serve":
        return _handle_serve(args, registry_path=registry_path, runtime_root_arg=runtime_root_arg, print_payload=print_payload, output_format=output_format)
    if command == "generate":
        return _handle_generate(args, registry_path=registry_path, runtime_root_arg=runtime_root_arg, print_payload=print_payload, output_format=output_format)
    print_payload(
        {"ok": False, "error": f"unknown dash command: {command}"},
        output_format(args),
        _render_markdown,
    )
    return 1


def _handle_serve(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int:
    try:
        serve_dash(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            host=args.host,
            port=args.port,
            goal_id=args.goal_id,
            refresh_seconds=args.refresh_seconds,
            verbose=bool(args.verbose),
        )
    except Exception as exc:
        print_payload(
            {
                "ok": False,
                "registry": str(registry_path),
                "error": str(exc),
            },
            output_format(args),
            _render_markdown,
        )
        return 1
    return 0


def _handle_generate(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int:
    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_arg,
        limit=20,
        goal_id=args.goal_id,
    )
    projection = build_session_dash_projection(
        status_payload=status_payload,
        run_history=_as_mapping(status_payload.get("run_history")),
        todo_index=_as_mapping(status_payload.get("todo_index")),
        agent_management=_as_mapping(status_payload.get("agent_management_projection")),
        usage_summary=_as_mapping(status_payload.get("usage_summary")),
        focus_goal_id=args.goal_id,
        generated_at=_as_mapping(status_payload.get("usage_summary") or {}).get(
            "generated_at"
        ),
    )
    if not projection.get("sessions") and not projection.get("unassigned_goals"):
        print_payload(
            {"ok": False, "error": "no active goals found; pass --goal-id"},
            output_format(args),
            _render_markdown,
        )
        return 1
    html = render_session_dash_html(projection)

    boundary_ok = True
    boundary_warnings: list[str] = []
    if not args.no_boundary_scan:
        boundary = scan_public_boundary_text(html)
        boundary_ok = boundary.get("ok", False)
        boundary_warnings = [str(w) for w in boundary.get("warnings", [])]

    if not boundary_ok:
        # Gate before any output is produced: the scan runs before payload
        # assembly or file creation, so a private marker can never reach the
        # --out file or leak through the html/html_path/projection fields.
        print_payload(
            {
                "ok": False,
                "mode": "generate",
                "focus_goal_id": projection.get("focus_goal_id"),
                "generated_at": projection.get("generated_at"),
                "boundary_ok": False,
                "boundary_warnings": boundary_warnings,
                "write_performed": False,
                "error": "public/private boundary scan failed; output withheld",
            },
            output_format(args),
            _render_markdown,
        )
        return 1

    payload: dict[str, object] = {
        "ok": True,
        "mode": "generate",
        "focus_goal_id": projection.get("focus_goal_id"),
        "generated_at": projection.get("generated_at"),
        "projection": projection,
        "html_bytes": len(html.encode("utf-8")),
        "boundary_ok": True,
        "boundary_warnings": boundary_warnings,
    }

    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        payload["html_path"] = str(out_path)
        payload["write_performed"] = True
    else:
        payload["html"] = html
        payload["write_performed"] = False

    print_payload(payload, output_format(args), _render_markdown)
    return 0


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
