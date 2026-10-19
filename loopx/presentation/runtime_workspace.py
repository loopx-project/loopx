"""Existing Goal UI over prepared, read-only Goal source access.

The source's native CLI owns status; the Explore owner validates its log. This
adapter never connects, creates or copies an execution Goal/registry. Each
physical source has its own loopback origin, including when Goal ids coincide.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import subprocess
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlparse

from ..capabilities.explore.result_log import explore_history_rows
from ..control_plane.status.goal_source import GoalSource, GoalSourceReader, SourceScopeError
from ..status_server import cors_response_headers, is_loopback_host, is_loopback_origin
from .chat_bundle import validate_bundle


class RuntimeWorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, source, assets_dir, *, port=0):
        super().__init__(("127.0.0.1", port), RuntimeWorkspaceHandler)
        self.source = source
        self.reader = GoalSourceReader(source, history_projector=explore_history_rows)
        self.assets_dir = Path(assets_dir).resolve()
        self.catalog = []

    @property
    def status_url(self):
        return f"http://127.0.0.1:{self.server_port}/status.json?source_namespace={self.source.namespace}"


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
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path.startswith("/chat/") or parsed.path in {"/chat", "/"}:
            self._asset(parsed.path)
            return
        if any(len(query.get(key, [])) > 1 for key in ("goal_id", "source_namespace", "cursor", "view")):
            self._json({"ok": False, "error": "Source selectors must have one value."}, 400)
            return
        if "source_namespace" in query and query["source_namespace"] != [self.server.source.namespace]:
            self._json({"ok": False, "error": "Source identity changed; keep the original source and retry."}, 409)
            return
        goal_id = query.get("goal_id", [self.server.source.goal_id])[0]
        if parsed.path == "/status.json" and query.get("view") == ["workspace-directory"]:
            # Native status already supplies the scoped Goal list. The UI's
            # existing full-status fallback preserves its metadata/read APIs.
            self._json({"ok": False, "error": "Use the scoped native status read."}, 404)
            return
        try:
            if parsed.path == "/api/goal-source":
                self._json(self.server.reader.describe())
            elif parsed.path.startswith("/api/goal-source/"):
                if query.get("source_namespace") != [self.server.source.namespace]:
                    self._json({"ok": False, "error": "Source identity changed; read its description again."}, 409)
                elif parsed.path == "/api/goal-source/goals":
                    self._json(self.server.reader.goals(goal_id))
                elif parsed.path == "/api/goal-source/status":
                    self._json({"ok": True, "source": self.server.source.describe(),
                                "status": self.server.reader.status(goal_id)})
                elif parsed.path == "/api/goal-source/history":
                    self._json(self.server.reader.history(goal_id, query.get("cursor", [""])[0]))
                else:
                    self._json({"ok": False, "error": "Read endpoint is not available for this source."}, 404)
            elif parsed.path == "/status.json":
                status = self.server.reader.status(goal_id)
                status = deepcopy(status)
                status["local_dashboard_api"] = {"source": "runtime-workspace",
                    "status_url": self.server.status_url, "read_only_sources": self.server.catalog,
                    "control_plane_write_enabled": False, "reward_write_enabled": False}
                self._json(status)
            elif parsed.path == "/api/chat/explore-results" and query.get("view") == ["history"]:
                cursor = query.get("cursor", [""])[0]
                self._json(self.server.reader.history(goal_id, cursor))
            elif parsed.path == "/api/chat/research-analysis":
                self.server.source.check_access(goal_id)
                self._json({"ok": False, "error": "此运行来源尚未绑定分析包。"}, 503)
            else:
                self._json({"ok": False, "error": "Read endpoint is not available for this source."}, 404)
        except SourceScopeError:
            self._json({"ok": False, "error": "Goal or connection is not available in this source."}, 404)
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
    parser.add_argument("--sources", type=Path, required=True, help="Owner-prepared Goal source manifest")
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
    raw = json.loads(args.sources.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        parser.error("Supply 1–12 scoped sources")
    try:
        sources = [GoalSource.parse(item) for item in raw]
    except (ValueError, TypeError) as exc:
        parser.error(str(exc))
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
        "namespace": server.source.namespace,
        "access_url": f"http://127.0.0.1:{server.server_port}/api/goal-source",
        "url": f"http://127.0.0.1:{server.server_port}/chat/?" + urlencode({
            **({"goalId": server.source.goal_id} if server.source.goal_id else {}),
            "statusUrl": item["status_url"], "view": "overview"})}
        for item, server in zip(catalog, servers)]}), flush=True)
    try:
        threads[0].join()
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
