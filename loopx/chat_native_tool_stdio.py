"""Dependency-free stdio MCP framing for the scoped native Chat bridge.

Only the published read/tool schemas come from the parent. This module owns no
LoopX decisions, state paths, audience rules or tool implementations.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main() -> None:
    url = os.environ["LOOPX_NATIVE_CHAT_TOOL_URL"]
    token = os.environ["LOOPX_NATIVE_CHAT_TOOL_TOKEN"]
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path != "/" or not parsed.port:
        raise ValueError("invalid native Chat bridge address")

    def call(method, params=None):
        request = Request(url, data=json.dumps({"method": method, "params": params}).encode(),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urlopen(request, timeout=60) as response:
            return json.load(response)

    for line in sys.stdin:
        identifier = None
        try:
            if len(line) > 65_536:
                raise ValueError("request too large")
            message = json.loads(line)
            identifier = message.get("id")
            if identifier is None:
                continue
            method = message.get("method")
            if method == "initialize":
                result = {"protocolVersion": message["params"]["protocolVersion"],
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "loopx-native-chat", "version": "1"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = call(method)
            elif method == "tools/call":
                value = call(method, message.get("params"))
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                          "isError": value.get("ok") is not True}
            else:
                raise ValueError("unsupported method")
            response = {"jsonrpc": "2.0", "id": identifier, "result": result}
        except Exception:
            response = {"jsonrpc": "2.0", "id": identifier,
                        "error": {"code": -32603, "message": "Native Chat tool transport unavailable"}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
