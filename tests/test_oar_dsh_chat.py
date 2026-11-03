"""Real DSH SDK cancellation through the default Chat -> OAR transport."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

from loopx.chat import CHAT_REVIEW_CLOSE_TAG, CHAT_REVIEW_OPEN_TAG
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_dsh import DshChatAdapter, MANAGED_HOST_CHAT_FAILED
from loopx.control_plane.turn_driver.host_process_transport import (
    HOST_PROCESS_DRAINED,
    HOST_PROCESS_RECORD_ENV,
    execution_host_drain,
    host_process_drain_supported,
)
from loopx.hosts.oar import bridge, installation


def test_default_dsh_chat_interrupt_drains_native_work_and_releases_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("deepseek_harness")
    if not host_process_drain_supported():
        pytest.skip("This process-tree assertion requires POSIX process groups")
    try:
        installation.node_executable()
        installation.runtime_directory()
    except RuntimeError as exc:
        pytest.skip(f"Optional pinned OAR runtime is required for this integration: {exc}")

    received = threading.Event()
    disconnected = threading.Event()
    shutdown = threading.Event()
    requests: list[dict[str, Any]] = []
    envelope = CHAT_REVIEW_OPEN_TAG + json.dumps({
        "message": "fresh fixture answer", "proposals": [], "protected_action": None,
        "context_handoff": None, "gate": None,
    }) + CHAT_REVIEW_CLOSE_TAG

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.flush()
            if len(requests) == 1:
                # The real native model request remains in flight until its
                # owning runtime closes the socket. No artificial SDK runner
                # or injected cancellation result participates in this path.
                received.set()
                self.connection.settimeout(0.1)
                while not shutdown.is_set():
                    try:
                        if self.connection.recv(1) == b"":
                            disconnected.set()
                            return
                    except socket.timeout:
                        continue
                    except ConnectionResetError:
                        disconnected.set()
                        return
                return
            chunk = {"choices": [{"delta": {"role": "assistant", "content": envelope}}]}
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(
                b'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    record_path = tmp_path / "host-process.json"
    monkeypatch.setenv(HOST_PROCESS_RECORD_ENV, str(record_path))
    for key in ("DSH_CWD", "DSH_HOME", "DSH_SESSION_ROOT"):
        monkeypatch.delenv(key, raising=False)
    adapter = DshChatAdapter(
        objective="Answer from the fixture context only.", work_dir=tmp_path,
        provider="deepseek-official", model="deepseek-flash", reasoning_effort="high",
        timeout_sec=25,
        credential={
            "DEEPSEEK_API_KEY": "fixture-operator-credential",
            "DEEPSEEK_BASE_URL": f"http://127.0.0.1:{server.server_port}",
        },
    )
    chat_binding_id = adapter.upstream_thread_id
    observations: list[dict[str, Any]] = []
    run_oar_turn = bridge.run_oar_turn

    def observe_transport(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # Observe the real transport receipt and stream without changing its
        # subprocess, native SDK, endpoint, cancellation, or return value.
        observation: dict[str, Any] = {"frames": []}
        observations.append(observation)
        callback = kwargs.get("on_record")

        def on_record(record: dict[str, Any]) -> None:
            observation["frames"].append(record)
            if callback is not None:
                callback(record)

        observed = run_oar_turn(*args, **{**kwargs, "on_record": on_record})
        observation["result"] = observed
        return observed

    monkeypatch.setattr(bridge, "run_oar_turn", observe_transport)
    failures: list[Exception] = []
    events: list[str] = []

    def start() -> None:
        try:
            adapter.start_turn("stalled fixture request", lambda kind, _payload: events.append(kind))
        except Exception as exc:
            failures.append(exc)

    caller = threading.Thread(target=start, daemon=True)
    caller.start()
    try:
        assert received.wait(15), "the actual SDK did not reach the local model fixture"
        slot = adapter._slot
        assert slot is not None and slot.thread is not None and slot.thread.is_alive()
        adapter.interrupt_turn()
        caller.join(timeout=10)
        assert not caller.is_alive(), "interrupt did not release the Chat caller"
        assert not slot.thread.is_alive(), "the native segment worker is still running"
        assert disconnected.wait(1), "the native SDK left its model request alive"
        assert execution_host_drain(record_path) == HOST_PROCESS_DRAINED
        observed = observations[0]["result"]
        assert observed["outcome"] == "cancelled"
        assert observed["oar_result"]["cleanupConfirmed"] is True
        assert observed["final_response"] is None
        assert len(failures) == 1 and isinstance(failures[0], CodexChatAgentError)
        assert failures[0].error_code == MANAGED_HOST_CHAT_FAILED
        assert adapter.history == []
        assert not any(kind.startswith("answer.") for kind in events)
        cancelled_frame_count = len(observations[0]["frames"])

        # A new real segment proves the slot was released. The cancelled
        # stream remains closed and cannot add late text or native events.
        try:
            response = adapter.start_turn("fresh fixture request", lambda kind, _payload: events.append(kind))
        except CodexChatAgentError:
            native_errors = [
                row["frame"]["body"].get("native")
                for row in observations[-1]["frames"]
                if row.get("type") == "native"
                and row.get("frame", {}).get("body", {}).get("type") == "dsh/error"
            ]
            pytest.fail(f"the next native segment failed: {native_errors}")
        assert response["message"] == "fresh fixture answer"
        assert [row["content"] for row in adapter.history] == ["fresh fixture request", "fresh fixture answer"]
        assert len(requests) == 2
        assert len(observations[0]["frames"]) == cancelled_frame_count
        assert observations[0]["result"]["session_id"] != observations[1]["result"]["session_id"]
        assert adapter.upstream_thread_id == chat_binding_id
        assert execution_host_drain(record_path) == HOST_PROCESS_DRAINED
    finally:
        adapter.close_session()
        caller.join(timeout=10)
        shutdown.set()
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
