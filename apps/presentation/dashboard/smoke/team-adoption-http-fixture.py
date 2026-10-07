"""Disposable real delegation/HTTP/SQLite owner; scripted host, no paid model."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

def main() -> None:
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore
    from test_local_delegation import brief, service, wait

    with tempfile.TemporaryDirectory(prefix="loopx-team-adoption-") as directory:
        patches = pytest.MonkeyPatch()
        finalizers = []
        server = None
        try:
            root, runner = service.__wrapped__(Path(directory), SimpleNamespace(
                param="sqlite", addfinalizer=finalizers.append), patches)
            config = json.loads(runner.config.read_text())
            config["bindings"].append({**config["bindings"][0], "id": "synthesis", "agent_id": "reviewer",
                "todo_id": "todo_reviewer-corrected", "workspace": str(root / "reviewer/corrected"),
                "output_refs": ["output.json", "report.md"]})
            runner.config.write_text(json.dumps(config))
            runner.start("analysis", "analysis-1", brief())
            source = wait(runner)
            artifact = source["artifacts"][0]
            input_file = root / "reviewer/corrected/accepted-input.json"
            input_file.write_text(artifact["text"])
            # Authored synthetic companion text; the real acceptance rule still
            # checks JSON semantics, while artifact readback checks both versions.
            (root / "reviewer/corrected/report.md").write_text(
                "# Corrected evidence\n\nThe current-period normalized value is 25. "
                "Different periods remain incomparable; the repost adds no independent source.\n")
            runner.start("synthesis", "synthesis-1", {**brief(), "inputs": [{
                "ref": "accepted-input.json", "description": "Accepted input", "sha256": artifact["sha256"],
                "delegation": {"operation_id": "analysis-1", "ref": artifact["ref"], "relation": "uses"}}]})
            assert wait(runner, "synthesis-1")["status"] == "accepted"
            repo = root / "project"
            executor = repo / ".loopx/config/delegations.json"
            executor.parent.mkdir(parents=True)
            executor.write_bytes(runner.config.read_bytes())
            registry = json.loads(runner.registry.read_text())
            registry["goals"][0]["spawn_policy"] = {"mode": "multi_subagent", "allowed": True,
                "max_children": 2, "execution_config": ".loopx/config/delegations.json"}
            runner.registry.write_text(json.dumps(registry))
            store = ChatSessionStore(runner.root)
            runtime = ChatRuntimeController(store=store, codex_bin="unused", registry_path=runner.registry)
            store.create_session(goal_id=runner.goal_id, agent_id="codex", channel_id="goal." + runner.goal_id,
                session_id="adoption", upstream_thread_id="fixture", upstream_mode="chat", adapter_kind="codex_app_server")
            runtime.loopx_mode.apply("adoption", {"operation": "configure", "settings": {
                "agent_id": "lead", "token_budget": 1000}}, work_dir=repo, objective="Read accepted work")
            initial_session = store.load_session("adoption")
            server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
            server.verbose = False
            server.registry_path, server.chat_store, server.runtime_controller = runner.registry, store, runtime
            server.runtime_root_override, server.scan_roots, server.limit = str(runner.root), [], 20
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
            for command in sys.stdin:
                operation = command.strip()
                if operation == "withdraw":
                    input_file.write_text("{}")
                    print(json.dumps({"withdrawn": True}), flush=True)
                elif operation == "restore":
                    input_file.write_text(artifact["text"])
                    print(json.dumps({"restored": True}), flush=True)
                elif operation == "inspect":
                    observed = runner.read("analysis-1")
                    print(json.dumps({"result": observed, "session_unchanged": store.load_session("adoption") == initial_session,
                        "host_invocations": [(root / workspace / "host-invocations").read_text()
                            for workspace in ("analyst/initial", "reviewer/corrected")]}), flush=True)
                else:
                    raise ValueError("Unknown fixture command")
        finally:
            if server:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            for cleanup in finalizers:
                cleanup()
            patches.undo()


if __name__ == "__main__":
    main()
