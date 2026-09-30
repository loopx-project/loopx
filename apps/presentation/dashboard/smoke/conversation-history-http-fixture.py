"""Disposable real Chat HTTP/store fixture; the fault changes reads only."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore


class HistoryReadFault(ChatRequestHandler):
    def _session_snapshot(self, session_id: str) -> None:
        if session_id == self.server.unavailable_session_id:
            self._send_error("Synthetic history read unavailable", status=503)
        else:
            super()._session_snapshot(session_id)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-history-http-") as directory:
        root = Path(directory)
        registry = root / "registry.json"
        registry.write_text(json.dumps({"schema_version": "0.1", "goals": [
            {"id": "research", "repo": str(root), "status": "active"},
        ]}), encoding="utf-8")
        store = ChatSessionStore(root / "runtime")
        runtime = ChatRuntimeController(store=store, codex_bin="missing-codex", registry_path=registry)
        for session_id, channel, text in [
            ("old", "goal.research", "Earlier public report"),
            ("current", "goal.research", "Current public report"),
            ("other-channel", "manager", "Unrelated conversation"),
        ]:
            store.create_session(goal_id="research", agent_id="codex", adapter_kind="codex_app_server",
                                 upstream_thread_id=session_id, channel_id=channel, session_id=session_id)
            store.append_message(session_id, role="agent", text=text, message_id="answer")
        before = {str(path.relative_to(store.sessions_root)): path.read_bytes()
                  for path in store.sessions_root.rglob("*") if path.is_file()}
        server = ChatHTTPServer(("127.0.0.1", 0), HistoryReadFault)
        server.verbose = False
        server.registry_path = registry
        server.runtime_root = root / "runtime"
        server.chat_store = store
        server.runtime_controller = runtime
        server.unavailable_session_id = "old"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            if sys.stdin.readline().strip() != "recover":
                raise ValueError("Expected read recovery")
            server.unavailable_session_id = ""
            print(json.dumps({"recovered": True}), flush=True)
            if sys.stdin.readline().strip() != "inspect":
                raise ValueError("Expected final inspection")
            after = {str(path.relative_to(store.sessions_root)): path.read_bytes()
                     for path in store.sessions_root.rglob("*") if path.is_file()}
            print(json.dumps({"store_unchanged": before == after,
                              "turn_count": sum(1 for _ in store.sessions_root.glob("*/turns/*.json"))}), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            runtime.close()


if __name__ == "__main__":
    main()
