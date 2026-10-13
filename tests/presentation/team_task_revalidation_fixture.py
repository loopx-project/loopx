"""Disposable production delegation + Chat HTTP for packaged recovery validation."""
from pathlib import Path
import hashlib
from contextlib import contextmanager
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
from loopx.control_plane.runtime.file_paths import windows_extended_path


@contextmanager
def fixture_workspace():
    temporary = tempfile.TemporaryDirectory(prefix="lx-rv-")
    directory = Path(temporary.name)
    assert directory.resolve().parent == Path(tempfile.gettempdir()).resolve()
    # Keep every registered path/opaque identity unchanged. Only recursive
    # teardown needs extended syntax to see Windows lock sidecars beyond MAX_PATH.
    temporary.name = str(windows_extended_path(directory))
    with temporary:
        yield directory


def drain_fixture_workers(workers, *, timeout=30):
    """Wait on this fixture's actual process handles, never infer exit from status."""
    for worker in workers:
        code = worker.wait(timeout=timeout)
        if code != 0:
            raise RuntimeError(f"fixture worker exited with status {code}")


def fixture_http_server(address, handler):
    server = ChatHTTPServer(address, handler)
    # A closed browser can leave an admitted HTTP request running. Join those
    # handlers before their disposable backend is removed; production stays unchanged.
    server.daemon_threads = False
    return server


def main():
    cleanups = []
    patch = pytest.MonkeyPatch()
    with fixture_workspace() as directory:
        try:
            root, runner = service.__wrapped__(Path(directory),
                SimpleNamespace(param="file", addfinalizer=cleanups.append), patch)
            workers = []

            class FixtureProcess(subprocess.Popen):
                def __init__(self, argv, *args, **kwargs):
                    super().__init__(argv, *args, **kwargs)
                    if (isinstance(argv, list) and "--delegation-action" in argv
                            and argv[argv.index("--delegation-action") + 1] == "worker"
                            and "--runtime-root" in argv
                            and argv[argv.index("--runtime-root") + 1] == str(runner.root)):
                        workers.append(self)

            # Keep the production spawn argv/IO intact; retain only our workers'
            # handles so accepted/rejected observations cannot race directory removal.
            patch.setattr(subprocess, "Popen", FixtureProcess)
            config = root / "project/.loopx/config/delegations.json"
            config.parent.mkdir(parents=True, exist_ok=True)
            catalog = json.loads(runner.config.read_text())
            catalog["bindings"].append({**catalog["bindings"][0], "id": "failure", "agent_id": "reviewer",
                "todo_id": "todo_reviewer-initial", "workspace": str(root / "reviewer/initial")})
            config.write_text(json.dumps(catalog))
            runner.config = config
            task_output = root / "reviewer/initial/output.json"
            task_original = task_output.read_bytes()
            terminal_written = threading.Event()
            release_worker = threading.Event()
            worker_errors = []
            held_worker = None
            observe = runner._observe
            def observe_before_release(path, row, status, **facts):
                observe(path, row, status, **facts)
                if status == "rejected" and not release_worker.is_set():
                    terminal_written.set()
                    if not release_worker.wait(120):
                        raise RuntimeError("fixture worker release timed out")
            patch.setattr(runner, "_observe", observe_before_release)
            store = ChatSessionStore(runner.root)
            controller = ChatRuntimeController(store=store, codex_bin="missing-codex", registry_path=runner.registry)
            store.create_session(goal_id=runner.goal_id, agent_id="codex", channel_id=f"goal.{runner.goal_id}",
                                 upstream_thread_id="fixture", session_id="recovery", adapter_kind="codex_app_server", upstream_mode="chat")
            store.update_session("recovery", native_goal={"status": "active"}, loopx_mode={"settings": {
                "agent_id": "lead", "execution_config": ".loopx/config/delegations.json"}})
            server = fixture_http_server(("127.0.0.1", 0), ChatRequestHandler)
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
                    if action == "task-failure-active":
                        task_output.write_text("{}")
                        def execute():
                            try:
                                runner.execute("failure-1")
                            except BaseException as error:
                                worker_errors.append(error)
                        def spawn(operation_id):
                            nonlocal held_worker
                            assert operation_id == "failure-1"
                            held_worker = threading.Thread(target=execute, daemon=True)
                            held_worker.start()
                        with pytest.MonkeyPatch.context() as worker_patch:
                            worker_patch.setattr(runner, "_spawn", spawn)
                            runner.start("failure", "failure-1", brief())
                        assert terminal_written.wait(120), worker_errors
                        current = runner.read("failure-1")
                        assert current["status"] == "rejected" and current["worker_active"]
                    elif action == "task-release":
                        release_worker.set()
                        held_worker.join(timeout=30)
                        assert not held_worker.is_alive() and not worker_errors, worker_errors
                        assert not wait(runner, "failure-1")["worker_active"]
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
                            "record_sha256": hashlib.sha256(runner.path("failure-1").read_bytes()).hexdigest(),
                            "host_calls": (root / "reviewer/initial/host-invocations").read_text()}), flush=True)
                        continue
                    elif action != "inspect":
                        raise ValueError("unknown fixture command")
                    print(json.dumps({"ok": True}), flush=True)
            finally:
                release_worker.set()
                if held_worker is not None:
                    held_worker.join(timeout=30)
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                controller.close()
                assert workers, "fixture did not observe its own launched worker"
                drain_fixture_workers(workers)
        finally:
            for cleanup in reversed(cleanups):
                cleanup()
            patch.undo()


if __name__ == "__main__":
    main()
