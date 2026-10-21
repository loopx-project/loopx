"""Live single-page session dash server.

`loopx dash` starts this loopback HTTP server so an operator can open one
single-page panel that tracks agent session task progress/status and refreshes
itself in place. The page is read-only: it renders public-safe projections and
exposes no write controls.

Routes:
- ``GET /`` — full single-page panel with an in-place auto-refresh script.
- ``GET /panel`` — fresh ``<main>`` fragment for the refresh script.
- ``GET /status.json`` — the compact session dash projection as JSON.
- ``GET /healthz`` — health probe.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .presentation.projections.session_dash import (
    build_session_dash_projection,
)
from .presentation.renderers.session_dash_html import (
    render_session_dash_html,
    render_session_dash_main,
)
from .presentation.public_safety import scan_public_boundary_text
from .status import collect_status


DEFAULT_DASH_HOST = "127.0.0.1"
DEFAULT_DASH_PORT = 8767
DEFAULT_DASH_REFRESH_SECONDS = 10
DASH_PANEL_PATH = "/panel"


class DashHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    registry_path: Path
    runtime_root_override: str | None
    goal_id: str | None
    refresh_seconds: int
    verbose: bool


class DashRequestHandler(BaseHTTPRequestHandler):
    server: DashHTTPServer

    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, body: bytes, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _build_projection(self) -> dict[str, Any]:
        status_payload = collect_status(
            registry_path=self.server.registry_path,
            runtime_root_override=self.server.runtime_root_override,
            limit=20,
            goal_id=self.server.goal_id,
        )
        projection = build_session_dash_projection(
            status_payload=status_payload,
            run_history=_as_mapping(status_payload.get("run_history")),
            todo_index=_as_mapping(status_payload.get("todo_index")),
            agent_management=_as_mapping(
                status_payload.get("agent_management_projection")
            ),
            usage_summary=_as_mapping(status_payload.get("usage_summary")),
            focus_goal_id=self.server.goal_id,
            generated_at=_as_mapping(status_payload.get("usage_summary") or {}).get(
                "generated_at"
            ),
        )
        return projection

    def _withhold_boundary(
        self, boundary: dict[str, object], *, endpoint: str
    ) -> None:
        self._send_json(
            {
                "ok": False,
                "error": (
                    "public/private boundary scan failed; "
                    f"{endpoint} withheld"
                ),
                "warnings": boundary.get("warnings"),
            },
            status=500,
        )

    def _serve_html_with_boundary(
        self, html: str, *, endpoint: str
    ) -> None:
        boundary = scan_public_boundary_text(html)
        if not boundary.get("ok"):
            self._withhold_boundary(boundary, endpoint=endpoint)
            return
        self._send_html(html.encode("utf-8"))

    def _serve_json_with_boundary(
        self, payload: dict[str, Any], *, endpoint: str
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        boundary = scan_public_boundary_text(body)
        if not boundary.get("ok"):
            self._withhold_boundary(boundary, endpoint=endpoint)
            return
        self._send_json(payload)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        try:
            projection = self._build_projection()
        except Exception as exc:  # noqa: BLE001 - HTTP layer preserves diagnostics.
            self._send_json(
                {
                    "ok": False,
                    "goal_id": self.server.goal_id,
                    "error": str(exc),
                },
                status=500,
            )
            return
        if path == DASH_PANEL_PATH:
            self._serve_html_with_boundary(
                render_session_dash_main(projection), endpoint="panel"
            )
            return
        if path == "/status.json":
            self._serve_json_with_boundary(projection, endpoint="status.json")
            return
        if path in {"", "/"}:
            self._serve_html_with_boundary(
                render_session_dash_html(
                    projection,
                    live_refresh_seconds=self.server.refresh_seconds,
                ),
                endpoint="panel",
            )
            return
        self._send_json(
            {
                "ok": False,
                "error": f"unknown path: {path}",
                "panel_path": DASH_PANEL_PATH,
            },
            status=404,
        )

    def log_message(self, format: str, *args: object) -> None:
        if self.server.verbose:
            super().log_message(format, *args)


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def serve_dash(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    host: str,
    port: int,
    goal_id: str | None = None,
    refresh_seconds: int = DEFAULT_DASH_REFRESH_SECONDS,
    verbose: bool = False,
) -> None:
    """Start the loopback single-page session dash server (blocks)."""

    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("loopx dash only binds to a loopback host such as 127.0.0.1")
    server = DashHTTPServer((host, port), DashRequestHandler)
    server.registry_path = registry_path
    server.runtime_root_override = runtime_root_override
    server.goal_id = goal_id
    server.refresh_seconds = max(1, int(refresh_seconds))
    server.verbose = verbose
    print(f"LoopX session dash: http://{host}:{port}/", flush=True)
    print(f"Panel fragment: http://{host}:{port}{DASH_PANEL_PATH}", flush=True)
    print(f"Projection JSON: http://{host}:{port}/status.json", flush=True)
    print(
        f"Auto-refresh: every {server.refresh_seconds}s "
        "(read-only; no browser write authority)",
        flush=True,
    )
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping LoopX session dash server")
    finally:
        server.server_close()
