"""Disposable original-owner protocol peer shared with the HTTP regression."""

import json
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import threading

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from loopx.chat_server import ChatRequestHandler, default_chat_assets_dir

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_attached_peer import append_message, fixture, provider

stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
with (
    tempfile.TemporaryDirectory(prefix="lx-ui-", dir="/tmp") as directory,
    ExitStack() as patches,
):
    with fixture(Path(directory), goal_id="product-release") as (
        request,
        rollout,
        _methods,
        _inputs,
        registry,
        _home,
    ):
        launches = []
        patches.enter_context(
            patch.object(provider, "sys", SimpleNamespace(platform="darwin"))
        )
        fail_next_open = False

        def open_spy(argv, **options):
            global fail_next_open
            if fail_next_open:
                fail_next_open = False
                raise OSError("synthetic unavailable App")
            launches.append((argv, options))

        patches.enter_context(
            patch.object(
                provider,
                "subprocess",
                SimpleNamespace(
                    run=open_spy, SubprocessError=subprocess.SubprocessError
                ),
            )
        )

        class Handler(ChatRequestHandler):
            def do_GET(self):
                if self.path == "/fixture/opened":
                    self._send_json({"ok": True, "count": len(launches)})
                else:
                    super().do_GET()

            def do_POST(self):
                global fail_next_open
                if self.path == "/fixture/append":
                    append_message(
                        rollout, "assistant", "原 Agent 的新结果：已保留原会话。"
                    )
                    self._send_json({"ok": True})
                elif self.path == "/fixture/rebind":
                    value = json.loads(registry.read_text())
                    value["goals"][0]["coordination"]["thread_agent_bindings"][0][
                        "thread_id"
                    ] = "22222222-2222-4222-8222-222222222222"
                    registry.write_text(json.dumps(value))
                    self._send_json({"ok": True})
                elif self.path == "/fixture/fail-open":
                    fail_next_open = True
                    self._send_json({"ok": True})
                else:
                    super().do_POST()

        request.server.RequestHandlerClass = Handler
        request.server.assets_dir = default_chat_assets_dir()
        print(
            json.dumps(
                {"url": f"http://127.0.0.1:{request.server.server_address[1]}/chat/"}
            ),
            flush=True,
        )
        stop.wait()
