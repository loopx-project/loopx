"""Invocation-scoped transport for existing Chat tool handlers.

This bridge translates MCP calls; the bound handler owns audience, arguments,
evidence and effects. Tokens/addresses never enter persisted Chat state.
"""
from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class NativeChatToolBridge:
    def __init__(self, tools: list[dict[str, Any]], handler: Callable[[str, Any], dict[str, Any]]):
        self.tools = [{key: tool[key] for key in ("name", "description", "inputSchema", "annotations")
                       if key in tool} for tool in tools]
        self.handler = handler
        self.token = secrets.token_urlsafe(32)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> NativeChatToolBridge:
        owner = self

        class Request(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                authorization = self.headers.get("Authorization", "")
                if not hmac.compare_digest(authorization, "Bearer " + owner.token):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65_536 or self.path != "/":
                        raise ValueError("invalid request")
                    request = json.loads(self.rfile.read(length))
                    if request.get("method") == "tools/list":
                        result = {"tools": owner.tools}
                    elif request.get("method") == "tools/call":
                        params = request.get("params") or {}
                        name = params.get("name")
                        if name not in {tool["name"] for tool in owner.tools}:
                            raise ValueError("unknown tool")
                        result = owner.handler(name, params.get("arguments"))
                    else:
                        raise ValueError("unknown method")
                except Exception:
                    result = {"ok": False, "error": "chat_tool_unavailable"}
                encoded = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Request)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def mcp_configuration(self) -> dict[str, Any]:
        assert self.server is not None
        return {"mcpServers": {"loopx": {
            "command": sys.executable,
            "args": ["-m", "loopx.chat_native_tool_stdio"],
            "env": {"LOOPX_NATIVE_CHAT_TOOL_URL": f"http://127.0.0.1:{self.server.server_port}/",
                    "LOOPX_NATIVE_CHAT_TOOL_TOKEN": self.token},
        }}}

    def __exit__(self, *_args: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
