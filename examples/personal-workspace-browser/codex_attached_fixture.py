"""Disposable original-owner protocol peer shared with the HTTP regression."""

import json
from pathlib import Path
import runpy
import signal
import tempfile
import threading

import pytest

from loopx.chat_server import ChatRequestHandler, default_chat_assets_dir

root = Path(__file__).resolve().parents[2]
fixture = runpy.run_path(
    str(root / "tests/presentation/test_codex_attached_conversation.py")
)
stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
with (
    tempfile.TemporaryDirectory(prefix="lx-ui-", dir="/tmp") as directory,
    pytest.MonkeyPatch.context() as patches,
):
    with fixture["fixture"](Path(directory), patches, goal_id="product-release") as (
        request,
        rollout,
        _methods,
        _inputs,
        registry,
        _home,
    ):
        launches = fixture["opener_spy"](patches)
        launch = fixture["provider"].subprocess.run
        fail_next_open = False

        def open_spy(argv, **options):
            global fail_next_open
            if fail_next_open:
                fail_next_open = False
                raise OSError("synthetic unavailable App")
            return launch(argv, **options)

        patches.setattr(fixture["provider"].subprocess, "run", open_spy)

        class Handler(ChatRequestHandler):
            def do_GET(self):
                if self.path == "/fixture/opened":
                    self._send_json({"ok": True, "count": len(launches)})
                else:
                    super().do_GET()

            def do_POST(self):
                global fail_next_open
                if self.path == "/fixture/append":
                    fixture["append_message"](
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
