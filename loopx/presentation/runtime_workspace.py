"""Owner-only read transport from pinned containers to the existing Goal UI.

The source's native CLI owns status; the Explore owner validates its log. This
adapter never connects, creates or copies an execution Goal/registry. Each
physical source has its own loopback origin, including when Goal ids coincide.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import subprocess
from threading import Lock, Thread
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlparse

from ..capabilities.explore.result_log import validate_explore_result_event
from ..chat_completed_todos import CompletedTodoPages
from ..status_server import cors_response_headers, is_loopback_host, is_loopback_origin
from .chat_bundle import validate_bundle
from .explore_results_api import explore_history_rows


# Only this specific log is read, using stdlib in the source's Python. Paths
# come from an owner-prepared manifest, never an HTTP query or browser script.
_READ_LOG = """
import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
path = (root / 'goals' / sys.argv[2] / 'explore-result-log.jsonl').resolve()
path.relative_to(root)
if path.exists():
    with path.open('rb') as f:
        data = f.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024: raise ValueError('log too large')
    sys.stdout.buffer.write(data)
"""


@dataclass(frozen=True)
class ContainerSource:
    id: str
    label: str
    container_id: str
    goal_id: str
    registry: str
    runtime_root: str
    cli: str
    python: str
    path: str

    @classmethod
    def parse(cls, raw):
        source = cls(**raw)
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}", source.id):
            raise ValueError("Invalid source id")
        if not re.fullmatch(r"[0-9a-f]{64}", source.container_id):
            raise ValueError("A full pinned container id is required")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}", source.goal_id):
            raise ValueError("Invalid source Goal id")
        if not source.label.strip() or len(source.label) > 48:
            raise ValueError("Source label must contain 1–48 characters")
        if any(not PurePosixPath(value).is_absolute() for value in
               (source.registry, source.runtime_root, source.cli, source.python)):
            raise ValueError("Source paths must be absolute")
        if not source.path or "\x00" in source.path:
            raise ValueError("Source PATH is required")
        return source

    def read(self):
        prefix = ["docker", "exec", "-e", "LOOPX_USAGE_PING=0", "-e", f"PATH={self.path}", self.container_id]
        status = json.loads(_run(prefix + [self.cli, "--registry", self.registry,
            "--runtime-root", self.runtime_root, "--format", "json", "status", "--goal-id", self.goal_id]))
        if not isinstance(status, dict) or not isinstance(status.get("run_history"), dict):
            raise ValueError("Invalid native status")
        goals = status.get("run_history", {}).get("goals", [])
        if (status.get("ok") is not True or not isinstance(goals, list) or len(goals) != 1
                or not isinstance(goals[0], dict) or goals[0].get("id") != self.goal_id):
            raise ValueError("The source no longer exposes the selected Goal")
        log = _run(prefix + [self.python, "-c", _READ_LOG, self.runtime_root, self.goal_id])
        events = []
        for line in log.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("Invalid native Explore record")
            events.append(validate_explore_result_event(event, expected_goal_id=self.goal_id))
        return status, explore_history_rows(events)


def _run(argv):
    # The timeout bounds dead/disconnected sources. Never return stderr, which
    # may contain native private paths or command diagnostics, over HTTP.
    result = subprocess.run(argv, capture_output=True, check=True, timeout=25)
    if len(result.stdout) > 16 * 1024 * 1024:
        raise ValueError("Source read exceeds the display limit")
    return result.stdout.decode("utf-8")


class RuntimeWorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, source, assets_dir, *, port=0):
        super().__init__(("127.0.0.1", port), RuntimeWorkspaceHandler)
        self.source = source
        self.assets_dir = Path(assets_dir).resolve()
        self.pages = CompletedTodoPages()
        self.catalog = []
        self._lock = Lock()
        self._snapshot = None
        self._read_at = 0.0

    @property
    def status_url(self):
        return f"http://127.0.0.1:{self.server_port}/status.json"

    def snapshot(self):
        with self._lock:
            if self._snapshot is None or monotonic() - self._read_at >= 2:
                # A failed new read must never fall back to the older snapshot.
                self._snapshot = None
                self._snapshot = self.source.read()
                self._read_at = monotonic()
            return self._snapshot


class RuntimeWorkspaceHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in cors_response_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self):
        try:
            host = urlparse("//" + self.headers.get("Host", "")).hostname or ""
        except ValueError:
            host = ""
        if not is_loopback_host(host) or not is_loopback_origin(self.headers.get("Origin")):
            self._json({"ok": False, "error": "Loopback owner access required."}, 403)
            return False
        return True

    def do_POST(self):
        if self._allowed():
            self._json({"ok": False, "error": "This source is read-only."}, 405)

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def do_OPTIONS(self):
        if self._allowed():
            self._json({"ok": True})

    def do_GET(self):
        if not self._allowed():
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path.startswith("/chat/") or parsed.path in {"/chat", "/"}:
            self._asset(parsed.path)
            return
        goal_id = query.get("goal_id", [self.server.source.goal_id])[0]
        if goal_id != self.server.source.goal_id:
            self._json({"ok": False, "error": "Goal is not in this source."}, 404)
            return
        if parsed.path == "/status.json" and query.get("view") == ["workspace-directory"]:
            # One pinned Goal needs no fan-out. Reuse the existing full-status
            # compatibility path, preserving its native metadata and read APIs.
            self._json({"ok": False, "error": "Use the scoped native status read."}, 404)
            return
        try:
            if parsed.path == "/status.json":
                status, _events = self.server.snapshot()
                status = deepcopy(status)
                status["local_dashboard_api"] = {"source": "runtime-workspace",
                    "status_url": self.server.status_url, "read_only_sources": self.server.catalog,
                    "control_plane_write_enabled": False, "reward_write_enabled": False}
                self._json(status)
            elif parsed.path == "/api/chat/explore-results" and query.get("view") == ["history"]:
                cursor = query.get("cursor", [""])[0]
                page = self.server.pages.page(scope=(self.server.source.id, goal_id), cursor=cursor,
                    load=lambda: self.server.snapshot()[1])
                self._json({**page, "goal_id": goal_id})
            elif parsed.path == "/api/chat/research-analysis":
                self._json({"ok": False, "error": "此运行来源尚未绑定分析包。"}, 503)
            else:
                self._json({"ok": False, "error": "Read endpoint is not available for this source."}, 404)
        except ValueError as exc:
            expired = str(exc) == "history_cursor_expired"
            self._json({"ok": False, "error": "history_cursor_expired" if expired else
                "原运行来源暂不可读，请刷新重试；未替换来源。"}, 409 if expired else 503)
        except (OSError, subprocess.SubprocessError):
            self._json({"ok": False, "error": "原运行来源暂不可读，请刷新重试；未替换来源。"}, 503)

    def _asset(self, path):
        relative = "index.html" if path in {"/", "/chat", "/chat/"} else path.removeprefix("/chat/")
        target = (self.server.assets_dir / relative).resolve()
        if not target.is_relative_to(self.server.assets_dir) or not target.is_file():
            self._json({"ok": False, "error": "Asset not found."}, 404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True, help="Owner-prepared container source manifest")
    parser.add_argument("--assets-dir", type=Path, help="Explicit custom/prototype assets, as in loopx chat")
    parser.add_argument("--port", type=int, default=0, help="First loopback port; default allocates temporary ports")
    args = parser.parse_args()
    assets = (args.assets_dir or Path(__file__).resolve().parents[1] / "web" / "chat").expanduser().resolve()
    if args.assets_dir is None:
        try:
            validate_bundle(assets, source_root=Path(__file__).resolve().parents[2])
        except RuntimeError as exc:
            parser.error(str(exc))
    if not (assets / "index.html").is_file():
        parser.error("Build the packaged Chat assets first or supply --assets-dir")
    raw = json.loads(args.sources.read_text())
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        parser.error("Supply 1–12 scoped sources")
    sources = [ContainerSource.parse(item) for item in raw]
    if args.port != 0 and not 1024 <= args.port <= 65535 - len(sources) + 1:
        parser.error("Choose unprivileged ports for all readers")
    if len({s.id for s in sources}) != len(sources):
        parser.error("Source ids must be unique")
    servers = [RuntimeWorkspaceServer(source, assets, port=args.port + i if args.port else 0)
               for i, source in enumerate(sources)]
    catalog = [{"id": s.source.id, "label": s.source.label, "status_url": s.status_url} for s in servers]
    threads = []
    for server in servers:
        server.catalog = catalog
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        threads.append(thread)
    print(json.dumps({"ok": True, "read_only": True, "sources": [{**item,
        "url": item["status_url"].replace("/status.json", "/chat/?") + urlencode({
            "goalId": server.source.goal_id, "statusUrl": item["status_url"], "view": "overview"})}
        for item, server in zip(catalog, servers)]}), flush=True)
    try:
        threads[0].join()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
