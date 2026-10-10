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
            config.write_text(json.dumps(catalog))
            runner.config = config
            registry = json.loads(runner.registry.read_text())
            goal = next(row for row in registry["goals"] if row["id"] == runner.goal_id)
            goal.setdefault("control_plane", {})["progress_review"] = {"mode": "shadow"}
            runner.registry.write_text(json.dumps(registry))
            from tests.capabilities.test_progress_review import receipt
            from loopx.capabilities.progress_review.receipt import write_progress_review_receipt
            from loopx.control_plane.effect_runtime import effect_runtime_result
            from loopx.control_plane.goals.acceptance import inspect_goal_acceptance
            requirements = inspect_goal_acceptance(registry_path=runner.registry, runtime_root=str(runner.root),
                goal_id=runner.goal_id, agent_id="analyst", todo_id="todo_analyst-initial")["completion_requirements"]
            binding = effect_runtime_result("progress_review.criterion_basis", {
                "requirements": requirements, "acceptance": [], "criterion_ids": ["analyst-initial"],
                "goal_id": runner.goal_id, "agent_id": "analyst",
            })["binding"]
            review = receipt(goal_id=runner.goal_id,
                judgments={"choice": {"relation": "off_goal", "increment": "new_evidence"}, "noul": {
                    "behavior_change": 0.9, "serves_acceptance": 0.1, "evidence_increment": 0.9}},
                drift_signal={"noul": False, "choice": False},
                evidence_scope={"criterion_binding": binding, "coverage": "declared_file_net_change", "files": ["code.ts"]})
            review["run"].update(agent_id="analyst", todo_id="todo_analyst-initial")
            review_path = write_progress_review_receipt(runner.root, runner.goal_id, review)
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
                    if action == "review-missing":
                        review_path.unlink()
                    elif action == "review-task-edit":
                        changed = runner._cli(runner.binding("analysis"), "todo", "update", "--goal-id", runner.goal_id,
                            "--agent-id", "analyst", "--todo-id", "todo_analyst-initial", "--text", "Changed declared scope",
                            "--update-operation-id", "review-task-scope-edit")
                        assert changed["ok"] is True, changed
                    elif action == "review-restore":
                        write_progress_review_receipt(runner.root, runner.goal_id, review)
                    else:
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
