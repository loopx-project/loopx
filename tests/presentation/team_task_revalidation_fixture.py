"""Disposable production delegation + Chat HTTP for packaged recovery validation."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from test_local_delegation import service, brief, wait
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
            config = root / "project/.loopx/config/delegations.json"
            config.parent.mkdir(parents=True, exist_ok=True)
            catalog = json.loads(runner.config.read_text())
            catalog["bindings"].append({**catalog["bindings"][0], "id": "failure", "agent_id": "reviewer",
                "todo_id": "todo_reviewer-initial", "workspace": str(root / "reviewer/initial")})
            config.write_text(json.dumps(catalog))
            runner.config = config
            task_output = root / "reviewer/initial/output.json"
            task_original = task_output.read_bytes()
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
            print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}", "goal_id": runner.goal_id}), flush=True)
            try:
                for command in sys.stdin:
                    action = command.strip()
                    if action == "task-failure":
                        task_output.write_text("{}")
                        runner.start("failure", "failure-1", brief())
                        assert wait(runner, "failure-1")["status"] == "rejected"
                    elif action == "task-repair":
                        task_output.write_bytes(task_original)
                    elif action == "task-commit-response-lost":
                        cli = runner._cli
                        def lose_response(binding, *args, **kwargs):
                            result = cli(binding, *args, **kwargs)
                            if "--retry-failed-turn" in args:
                                assert result["status"] == "committed"
                                raise subprocess.TimeoutExpired("lost committed response", 1)
                            return result
                        with pytest.MonkeyPatch.context() as failure_patch:
                            failure_patch.setattr(runner, "_cli", lose_response)
                            with pytest.raises(subprocess.TimeoutExpired):
                                runner.revalidate("failure-1")
                        assert runner.read("failure-1")["recovery_required"] is True
                    elif action == "task-inspect":
                        print(json.dumps({"operation": runner.read("failure-1"),
                            "host_calls": (root / "reviewer/initial/host-invocations").read_text()}), flush=True)
                        continue
                    elif action != "inspect":
                        raise ValueError("unknown fixture command")
                    print(json.dumps({"ok": True}), flush=True)
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
