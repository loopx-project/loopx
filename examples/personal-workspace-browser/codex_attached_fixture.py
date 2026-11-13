"""Disposable original-owner protocol peer shared with the HTTP regression."""

import json
from pathlib import Path
import sys
import signal
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from loopx.chat_server import ChatRequestHandler, default_chat_assets_dir

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_attached_peer import (
    THREAD,
    add_conversation,
    append_message,
    fixture,
    provider,
)

stop = threading.Event()
signal.signal(signal.SIGTERM, lambda *_: stop.set())
with (
    tempfile.TemporaryDirectory(prefix="lx-ui-", dir="/tmp") as directory,
    ExitStack() as patches,
):
    with fixture(
        Path(directory), goal_id="product-release", uncertain="--uncertain" in sys.argv
    ) as (
        request,
        rollout,
        _methods,
        _inputs,
        registry,
        _home,
    ):
        # The directory includes Agents without this provider or without a binding.
        value = json.loads(registry.read_text())
        coordination = value["goals"][0]["coordination"]
        coordination["registered_agents"] += ["other-worker", "remote-worker"]
        coordination["thread_agent_bindings"].append(
            {
                "agent_id": "remote-worker",
                "host_surface": "cursor",
                "thread_id": "synthetic-cursor-thread",
            }
        )
        registry.write_text(json.dumps(value))
        launches = []
        patches.enter_context(
            patch.object(provider, "sys", SimpleNamespace(platform="darwin"))
        )
        fail_next_open = False
        live_revision = 0
        live_updates = 0
        body_fault = None
        content_release = threading.Event()

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
                global body_fault
                if self.path == "/fixture/opened":
                    self._send_json({"ok": True, "count": len(launches)})
                elif self.path == "/fixture/inputs":
                    self._send_json({"ok": True, "count": len(_inputs)})
                elif self.path == "/fixture/following":
                    self._send_json({"events": request.follow_events})
                elif (
                    body_fault
                    and self.path.startswith("/api/chat/codex-attached?")
                    and (
                        "before" in parse_qs(urlsplit(self.path).query)
                        if body_fault == "history"
                        else "entry_id" in parse_qs(urlsplit(self.path).query)
                        if body_fault in {"content", "content-denied", "content-held"}
                        else "agent_id" not in parse_qs(urlsplit(self.path).query)
                    )
                ):
                    # Send actual headers, then end the connection short of the
                    # declared body length (or let the browser's read time out).
                    mode, body_fault = body_fault, None
                    status = {
                        "timeout": 503,
                        "drop": 403,
                        "history": 200,
                        "content": 200,
                        "content-denied": 403,
                        "content-held": 200,
                    }[mode]
                    self.send_response(status)
                    self.send_header(
                        "Content-Type",
                        "application/json"
                        if status == 200
                        else "text/html; charset=utf-8",
                    )
                    self.send_header("Content-Length", "200")
                    self.end_headers()
                    self.wfile.write(
                        b'{"ok":'
                        if status == 200
                        else b"<html><body>Service unavailable"
                    )
                    self.wfile.flush()
                    if mode == "timeout":
                        time.sleep(7.5)
                    elif mode == "content-held":
                        content_release.wait(7.5)
                    self.close_connection = True
                else:
                    super().do_GET()

            def do_POST(self):
                global fail_next_open, live_revision, live_updates, body_fault
                if self.path == "/fixture/second-conversation":
                    add_conversation(
                        registry,
                        _home,
                        agent_id="worker",
                        thread_id="22222222-2222-4222-8222-222222222222",
                        text="Separate investigation",
                    )
                    self._send_json({"ok": True})
                    return
                faults = {
                    "/fixture/discovery-body-timeout": "timeout",
                    "/fixture/discovery-body-drop": "drop",
                    "/fixture/history-body-drop": "history",
                    "/fixture/content-body-drop": "content",
                    "/fixture/content-body-denied": "content-denied",
                    "/fixture/content-body-hold": "content-held",
                }
                if self.path in faults:
                    if self.path == "/fixture/content-body-hold":
                        content_release.clear()
                    body_fault = faults[self.path]
                    self._send_json({"ok": True})
                    return
                if self.path == "/fixture/content-body-release":
                    content_release.set()
                    self._send_json({"ok": True})
                    return
                if self.path == "/fixture/work-records":
                    # Genuine, already public-safe display data; only the host
                    # protocol and carrier messages are synthetic. Keep one
                    # non-BMP prefix to exercise character-based detail offsets.
                    reference = (
                        Path(__file__).resolve().parents[2]
                        / "apps/presentation/dashboard/src/data/fixtures"
                        / "edgebench-grid-context-on-20261103-history.json"
                    )
                    records = [
                        {
                            "type": "function_call",
                            "name": "read_file",
                            "call_id": "work-1",
                            "arguments": json.dumps({"path": reference.name}),
                        },
                        {
                            "type": "function_call_output",
                            "call_id": "work-1",
                            "output": [
                                {
                                    "type": "text",
                                    "text": "🧭\n" + reference.read_text().strip(),
                                }
                            ],
                        },
                    ]
                    with rollout.open("a") as stream:
                        for item in records:
                            stream.write(
                                json.dumps({"type": "response_item", "payload": item})
                                + "\n"
                            )
                    append_message(rollout, "assistant", "工作结果已返回。")
                    self._send_json({"ok": True})
                elif self.path == "/fixture/tool-heavy":
                    append_message(
                        rollout, "user", "请核对这些结果，并说明未通过的检查。"
                    )
                    with rollout.open("a") as stream:
                        for index in range(200):
                            for item in (
                                {
                                    "type": "function_call",
                                    "name": "validate",
                                    "call_id": f"check-{index}",
                                    "arguments": f"Check artifact {index}",
                                },
                                {
                                    "type": "function_call_output",
                                    "call_id": f"check-{index}",
                                    "output": f"Saved output {index}",
                                },
                            ):
                                stream.write(
                                    json.dumps(
                                        {"type": "response_item", "payload": item}
                                    )
                                    + "\n"
                                )
                        stream.write(
                            json.dumps(
                                {
                                    "type": "event_msg",
                                    "payload": {
                                        "type": "error",
                                        "message": "A check is unavailable; no acceptance has been recorded.",
                                    },
                                }
                            )
                            + "\n"
                        )
                    append_message(
                        rollout,
                        "assistant",
                        "检查记录已保存，尚有一项无法确认。\n\n"
                        + "这是完整的说明，应直接阅读。\n" * 120
                        + "LONG DIALOGUE END",
                    )
                    self._send_json({"ok": True})
                elif self.path == "/fixture/metadata-gap":
                    # A real rollout page with no displayable rows, between two
                    # visible records. Boundaries belong to the production reader.
                    with rollout.open("a") as stream:
                        for _ in range(5):
                            stream.write(
                                json.dumps(
                                    {
                                        "type": "response_item",
                                        "payload": {
                                            "type": "reasoning",
                                            "encrypted_content": "x" * (1024 * 1024),
                                        },
                                    }
                                )
                                + "\n"
                            )
                    self._send_json({"ok": True})
                elif self.path == "/fixture/tool-envelope":
                    with rollout.open("a") as stream:
                        stream.write(
                            json.dumps(
                                {
                                    "type": "response_item",
                                    "payload": {
                                        "type": "custom_tool_call_output",
                                        "output": [
                                            {
                                                "type": "input_text",
                                                "text": json.dumps(
                                                    {
                                                        "output": "Readable shell result\nSecond line",
                                                        "exit_code": 1,
                                                        "wall_time_seconds": 0.25,
                                                        "chunk_id": "synthetic",
                                                    }
                                                ),
                                            }
                                        ],
                                    },
                                }
                            )
                            + "\n"
                        )
                    self._send_json({"ok": True})
                elif self.path == "/fixture/burst":
                    for index in range(330):
                        append_message(rollout, "assistant", f"Activity {index}")
                    self._send_json({"ok": True})
                elif self.path == "/fixture/append":
                    append_message(
                        rollout, "assistant", "原 Agent 的新结果：已保留原会话。"
                    )
                    with rollout.open("a") as stream:
                        stream.write(
                            json.dumps(
                                {
                                    "type": "event_msg",
                                    "timestamp": "2026-01-01T00:00:03Z",
                                    "payload": {"type": "task_complete"},
                                }
                            )
                            + "\n"
                        )
                    self._send_json({"ok": True})
                elif self.path == "/fixture/busy":
                    with rollout.open("a") as stream:
                        stream.write(
                            json.dumps(
                                {
                                    "type": "event_msg",
                                    "timestamp": "2026-01-01T00:00:02Z",
                                    "payload": {"type": "task_started"},
                                }
                            )
                            + "\n"
                        )
                    self._send_json({"ok": True})
                elif self.path in {"/fixture/rebind", "/fixture/restore-binding"}:
                    value = json.loads(registry.read_text())
                    binding = value["goals"][0]["coordination"][
                        "thread_agent_bindings"
                    ][0]
                    binding["thread_id"] = (
                        THREAD
                        if self.path == "/fixture/restore-binding"
                        else "22222222-2222-4222-8222-222222222222"
                    )
                    registry.write_text(json.dumps(value))
                    self._send_json({"ok": True})
                elif self.path in {
                    "/fixture/live-start",
                    "/fixture/live-update",
                    "/fixture/live-finish",
                }:
                    live_revision += 1
                    finished = self.path == "/fixture/live-finish"
                    if self.path == "/fixture/live-start":
                        text = "原宿主实时草稿"
                    elif self.path == "/fixture/live-update":
                        # Distinct drafts let the browser regression tell a stale
                        # frozen projection from the newer buffered one.
                        live_updates += 1
                        text = (
                            "原宿主实时草稿，已持续更新。"
                            if live_updates == 1
                            else f"原宿主实时草稿，已持续更新 {live_updates}。"
                        )
                    else:
                        text = "原宿主实时草稿，已持续更新。"
                    if self.path == "/fixture/live-start" or finished:
                        with rollout.open("a") as stream:
                            stream.write(
                                json.dumps(
                                    {
                                        "type": "event_msg",
                                        "timestamp": "2026-01-01T00:00:05Z"
                                        if finished
                                        else "2026-01-01T00:00:04Z",
                                        "payload": {
                                            "type": "task_complete"
                                            if finished
                                            else "task_started",
                                            "turn_id": "live-workspace-fixture",
                                        },
                                    }
                                )
                                + "\n"
                            )
                            if not finished:
                                stream.write(
                                    json.dumps(
                                        {
                                            "type": "turn_context",
                                            "timestamp": "2026-01-01T00:00:04Z",
                                            "payload": {
                                                "turn_id": "live-workspace-fixture",
                                                "model": "codex-synthetic-attached-workspace-verification-model",
                                                "model_provider": "openai",
                                                "effort": "max",
                                            },
                                        }
                                    )
                                    + "\n"
                                )
                    if finished:
                        append_message(rollout, "assistant", text)
                    request.publish_live(
                        {
                            "type": "snapshot",
                            "revision": live_revision,
                            "conversationState": {
                                "id": "11111111-1111-4111-8111-111111111111",
                                "turns": [
                                    {
                                        "turnId": "live-example",
                                        "status": "completed"
                                        if finished
                                        else "inProgress",
                                        "completedItemIds": [],
                                        "items": [
                                            {
                                                "id": "live-answer",
                                                "type": "agentMessage",
                                                "phase": "commentary",
                                                "text": text,
                                            },
                                            {
                                                "id": "hidden-reasoning",
                                                "type": "reasoning",
                                                "content": "HIDDEN REASONING MUST NOT APPEAR",
                                            },
                                            {
                                                "id": "live-tool",
                                                "type": "mcpToolCall",
                                                "status": "inProgress",
                                                "server": "synthetic",
                                                "tool": "verify_artifact",
                                                "arguments": {
                                                    "artifact": "release candidate"
                                                },
                                            },
                                        ],
                                    }
                                ],
                            },
                        }
                    )
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
