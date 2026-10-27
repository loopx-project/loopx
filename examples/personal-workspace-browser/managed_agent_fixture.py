"""Real SQLite/Core/Chat with an isolated synthetic native model protocol peer."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
import socket
import sys
import tempfile
import threading

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "tests/control_plane")]
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.chat_server import serve_chat


class SyntheticManagedSource:
    transport_ref = "synthetic-managed"
    cancelled = staticmethod(lambda: False)
    close = staticmethod(lambda: None)
    health_snapshot = staticmethod(lambda: {"status": "listening"})

    def __init__(self, root, registry):
        self.root, self.registry = root, registry

    def start(self, server):
        runtime, store = server.runtime_controller, server.chat_store
        runtime.codex_home = self.root / "synthetic-model-home"
        runtime.codex_home.mkdir()
        original_registry = self.registry.read_text()
        sessions = []
        for agent in ("worker", "worker", "peer"):
            session, _ = runtime.open_session(goal_id="product-release", managed_agent_id=agent,
                agent_id="codex", objective="Review synthetic research evidence", work_dir=self.root / "project",
                mode="new", agent_model_id="gpt-6.1-sol", agent_reasoning_effort="xhigh")
            sessions.append(session)
            number = len(sessions)
            store.append_message(session["session_id"], role="user", text=f"Original managed question {number}")
            store.append_message(session["session_id"], role="agent", text=f"Original managed result {number}\n\n"
                "Evidence and uncertainty remain in this original Session.\n\n"
                "https://example.org/research/" + "long-identifier-" * 15 + f"?session={number}")
        legacy, _ = runtime.open_session(goal_id="product-release", agent_id="codex", objective="Unmapped history",
            work_dir=self.root / "project", mode="new")
        store.append_message(legacy["session_id"], role="agent", text="Unmapped ordinary Goal history stays separate.")
        before = self.calls()
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}",
            "sessions": [row["session_id"] for row in sessions], "threads": [row["upstream_thread_id"] for row in sessions],
            "legacy": legacy["session_id"]}), flush=True)

        def control():
            for line in sys.stdin:
                command = line.strip()
                if command == "inspect":
                    result = {"calls_after_setup": self.calls()[len(before):], "sessions": len(store.list_sessions()),
                        "messages": {row["session_id"]: store.messages(row["session_id"]) for row in sessions}}
                elif command == "revoke":
                    value = json.loads(original_registry); value["goals"][0]["coordination"]["registered_agents"] = ["peer"]
                    self.registry.write_text(json.dumps(value)); result = {"revoked": True}
                elif command == "recover":
                    self.registry.write_text(original_registry); result = {"recovered": True}
                elif command == "append":
                    store.append_message(sessions[0]["session_id"], role="agent", text="Late original result for Session 1")
                    result = {"appended": True}
                elif command == "observer-session":
                    session, _ = runtime.open_session(goal_id="product-release", managed_agent_id="worker",
                        agent_id="codex", objective="Verify the release handoff", work_dir=self.root / "project",
                        mode="new", agent_model_id="gpt-6.1-sol", agent_reasoning_effort="xhigh")
                    sessions.append(session)
                    store.append_message(session["session_id"], role="user", text="Review the release checklist before the next handoff.")
                    store.append_message(session["session_id"], role="agent", text="The checklist is ready for verification. I will keep the result and remaining questions in this conversation.")
                    result = {"session_id": session["session_id"]}
                elif command == "stop":
                    server.shutdown(); return
                else:
                    result = {"unknown": command}
                print(json.dumps(result), flush=True)
        threading.Thread(target=control, daemon=True).start()

    def calls(self):
        path = self.root / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


with tempfile.TemporaryDirectory(prefix="loopx-managed-ui-") as directory, pytest.MonkeyPatch.context() as patch:
    root = Path(directory)
    isolate_sqlite_runtime(root, patch)
    patch.setenv("CODEX_HOME", str(root / "synthetic-model-home"))
    registry, _, _ = promoted_create_fixture(root, provider="sqlite", goal_id="product-release", registered_agents=["worker", "peer"])
    value = json.loads(registry.read_text()); value["goals"][0]["status"] = "active"; registry.write_text(json.dumps(value))
    fake = root / "synthetic-codex"
    source = runpy.run_path(str(REPO / "examples/loopx-chat-runtime-smoke.py"))["FAKE_CODEX"]
    source = source.replace('active_turn = None', 'active_turn = None\nnative_thread_id = None', 1)
    source = source.replace('    method = request.get("method")',
        f'    with open({str(root / "calls.jsonl")!r}, "a") as output:\n        output.write(json.dumps(request) + "\\n")\n'
        '    method = request.get("method")\n'
        '    if method == "thread/start":\n        import uuid\n        native_thread_id = uuid.uuid4().hex\n'
        '    elif method in {"thread/resume", "thread/read"}:\n        native_thread_id = request["params"]["threadId"]')
    source = source.replace('"durable-thread"', 'native_thread_id')
    source = source.replace('result = {"thread": {"id": native_thread_id}}',
        'result = {"thread": {"id": native_thread_id}, "model": request["params"].get("model", "gpt-6.1-sol"), "reasoningEffort": request["params"].get("reasoningEffort", "xhigh")}')
    fake.write_text(source); fake.chmod(0o700)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    serve_chat(registry_path=registry, runtime_root_override=root / "runtime", scan_roots=[root / "project"],
        codex_bin=str(fake), lark_cli_bin="missing-synthetic-lark", port=port,
        external_conversation_factories=(lambda _: SyntheticManagedSource(root, registry),))
