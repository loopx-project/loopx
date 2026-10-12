"""Disposable production inbox/store; the UI fixture supplies team evidence only."""
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


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-team-feedback-") as directory:
        root = Path(directory)
        registry = root / "registry.json"
        registry.write_text(json.dumps({"schema_version": "0.1", "goals": [
            {"id": "research", "repo": str(root), "status": "active"},
        ]}), encoding="utf-8")
        store = ChatSessionStore(root / "runtime")
        runtime = ChatRuntimeController(store=store, codex_bin="missing-codex", registry_path=registry)
        store.create_session(goal_id="research", agent_id="codex", channel_id="goal.research",
                             upstream_thread_id="fixture", session_id="feedback",
                             adapter_kind="codex_app_server", upstream_mode="chat")
        # Establish local admission facts without dispatching a model or worker.
        accepted = store.accept_managed_turn("feedback", client_turn_id="fixture-turn",
                                             message="Review the report", loopx_execution=True)
        store.update_session("feedback", active_turn_id=accepted.turn["turn_id"],
                             loopx_mode={"enabled": True, "paused": False})
        server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
        server.verbose = False
        server.registry_path = registry
        server.runtime_root = root / "runtime"
        server.chat_store = store
        server.runtime_controller = runtime
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            for command in sys.stdin:
                if command.strip() == "pause":
                    store.update_session("feedback", loopx_mode={"enabled": True, "paused": True})
                    print(json.dumps({"paused": True}), flush=True)
                elif command.strip() == "inspect":
                    print(json.dumps({"ingress": store.loopx_ingress("feedback"),
                                      "messages": store.messages("feedback"),
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
