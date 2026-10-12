"""Disposable production delegation + Chat HTTP for packaged recovery validation."""
from pathlib import Path
import json
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from test_local_delegation import service
from test_delegation_result_use import accepted_chain
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore


def main():
    cleanups = []
    patch = pytest.MonkeyPatch()
    with tempfile.TemporaryDirectory(prefix="loopx-result-use-") as directory:
        try:
            root, runner = service.__wrapped__(Path(directory),
                SimpleNamespace(param="file", addfinalizer=cleanups.append), patch)
            accepted_chain(root, runner)
            config = root / "project/.loopx/config/delegations.json"
            config.parent.mkdir(parents=True, exist_ok=True)
            catalog = json.loads(runner.config.read_text())
            config.write_text(json.dumps(catalog))
            runner.config = config
            historical = {op: runner.path(op).read_bytes() for op in ("analysis-1", "middle-1", "synthesis-1")}
            store = ChatSessionStore(runner.root)
            controller = ChatRuntimeController(store=store, codex_bin="missing-codex", registry_path=runner.registry)
            store.create_session(goal_id=runner.goal_id, agent_id="codex", channel_id=f"goal.{runner.goal_id}",
                                 upstream_thread_id="fixture", session_id="recovery", adapter_kind="codex_app_server", upstream_mode="chat")
            store.update_session("recovery", native_goal={"status": "active"}, loopx_mode={"settings": {
                "agent_id": "lead", "execution_config": ".loopx/config/delegations.json"}})
            server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
            server.verbose = False
            server.registry_path = runner.registry
            server.runtime_root = runner.root
            server.chat_store = store
            server.runtime_controller = controller
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            source_input = root / "analyst/corrected/declared-input.json"
            original = source_input.read_bytes()
            print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}", "goal_id": runner.goal_id}), flush=True)
            try:
                for command in sys.stdin:
                    action = command.strip()
                    if action == "withdraw":
                        source_input.write_text("{}")
                    elif action == "restore":
                        source_input.write_bytes(original)
                    elif action != "inspect":
                        raise ValueError("unknown fixture command")
                    print(json.dumps({"operation": runner.read("synthesis-1"),
                        "historical_unchanged": all(runner.path(op).read_bytes() == value for op, value in historical.items()), "host_calls": [(root / path / "host-invocations").read_text()
                        for path in ("analyst/initial", "analyst/corrected", "reviewer/corrected")]}), flush=True)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                controller.close()
        finally:
            for cleanup in reversed(cleanups):
                cleanup()
            patch.undo()


if __name__ == "__main__":
    main()
