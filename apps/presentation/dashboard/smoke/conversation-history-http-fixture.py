"""Disposable real Chat HTTP/store fixture; the fault changes reads only."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore


class HistoryReadFault(ChatRequestHandler):
    def _session_snapshot(self, session_id: str) -> None:
        query = parse_qs(urlparse(self.path).query)
        if (session_id == self.server.unavailable_session_id
                or (session_id == "old" and self.server.unavailable_message_pages and "message_cursor" in query)):
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
            if "--manager" in sys.argv:
                channel = "manager" if channel == "goal.research" else "goal.unrelated"
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
        server.unavailable_message_pages = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            for command in sys.stdin:
                command = command.strip()
                if command == "recover":
                    server.unavailable_session_id = ""
                    server.unavailable_message_pages = False
                    print(json.dumps({"recovered": True}), flush=True)
                elif command == "history":
                    for index in range(450):
                        store.append_message("old", role="agent", message_id=f"history-{index}",
                            text="Original full body. " * 600 if index == 201 else f"Paged original row {index}")
                    before = {str(path.relative_to(store.sessions_root)): path.read_bytes()
                              for path in store.sessions_root.rglob("*") if path.is_file()}
                    print(json.dumps({"history_rows": len(store.messages("old"))}), flush=True)
                elif command == "page-fault":
                    server.unavailable_session_id = ""
                    server.unavailable_message_pages = True
                    print(json.dumps({"unavailable_message_pages": True}), flush=True)
                elif command == "fault":
                    server.unavailable_session_id = "old"
                    print(json.dumps({"unavailable": "old"}), flush=True)
                elif command in {"result", "revision"}:
                    heading = "First checked result" if command == "result" else "Revised checked result"
                    store.append_message("old", role="agent", origin="manager_followup",
                                         message_id=f"handoff.{command}",
                                         text=f"{heading}\n\nForm: https://example.org/forms/{command}。")
                    # Fixture writes are intentional. Subsequent browser reads
                    # must neither mutate this state nor create an execution.
                    before = {str(path.relative_to(store.sessions_root)): path.read_bytes()
                              for path in store.sessions_root.rglob("*") if path.is_file()}
                    print(json.dumps({"published": command}), flush=True)
                elif command == "inspect":
                    after = {str(path.relative_to(store.sessions_root)): path.read_bytes()
                             for path in store.sessions_root.rglob("*") if path.is_file()}
                    print(json.dumps({"store_unchanged": before == after,
                                      "turn_count": sum(1 for _ in store.sessions_root.glob("*/turns/*.json"))}), flush=True)
                else:
                    raise ValueError("Unknown fixture command")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            runtime.close()


if __name__ == "__main__":
    main()
