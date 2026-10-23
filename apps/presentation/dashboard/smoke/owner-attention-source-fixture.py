"""Packaged owner-notification QA over real HTTP, native authority and worker.

Only provider/model transport is synthetic. The source outage is an actual
unmigrated disposable Goal; the recovery marker initializes its native source.
No production registry, account, listener or paid call is used. Baseline assets
show the previous UI over the same current partial-source state.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
import threading
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--baseline-assets", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    sys.path[:0] = [str(repo), str(repo / "tests")]
    import pytest
    from test_chat_ordinary_project import ordinary
    from test_lark_owner_attention import owner
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    from loopx.presentation.codex_accounts_api import CodexAccountService
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    import loopx.chat_manager_context as manager
    collect_manager_turn_context = manager.collect_manager_turn_context

    args.output.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    with TemporaryDirectory(prefix="loopx-owner-source-qa-") as directory, pytest.MonkeyPatch.context() as patch:
        root = Path(directory)
        ordinary_fixture = ordinary.__wrapped__(root, patch)
        data = next(ordinary_fixture)
        patch.setattr(manager, "collect_manager_turn_context", collect_manager_turn_context)
        owner_fixture = owner.__wrapped__(data, root)
        o = next(owner_fixture)
        native_todos = o.attention._goal_todos
        def source(goal_id):
            if goal_id == "unavailable" and (args.output / "source-unavailable").exists():
                raise OSError("injected disposable source outage")
            return native_todos(goal_id)
        patch.setattr(o.attention, "_goal_todos", source)
        def provider(args, cwd=None, timeout=None):
            if "+chat-messages-list" in args:
                chat = args[args.index("--chat-id") + 1]
                return {"returncode": 0, "stderr": "", "stdout": json.dumps({"ok": True, "data": {
                    "items": [row for row in list(o.provider.messages.values()) if row["chat_id"] == chat],
                    "has_more": False}})}
            return o.provider(args, cwd, timeout)
        o.transport.runner = provider
        o.runtime.manager_scope_resolver = o.transport.bindings.steward_scope
        o.transport.core.actions = ChatActionService(store=ChatActionStore(o.store.root / "actions"),
            registry_path=o.runtime.registry_path, chat_store=o.store, runtime_controller=o.runtime,
            workspace_roots=[data[-1]])
        o.configure()
        o.tasks([{"id": "todo_healthy", "text": "Review the public preview and its complete acceptance evidence."}], goal="release")
        o.tasks([{"id": "todo_missing", "text": "Keep this source unread until its native authority is restored."}],
                goal="unavailable", publish=False)
        servers = []
        for assets in [args.assets, *([args.baseline_assets] if args.baseline_assets else [])]:
            server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
            server.chat_store, server.runtime_controller = o.store, o.runtime
            server.registry_path, server.runtime_root_override = o.runtime.registry_path, str(o.store.root.parent)
            server.runtime_root = o.runtime.coordination_runtime_root
            server.scan_roots, server.limit, server.selected_goal_id = [data[-1]], 20, None
            server.assets_dir, server.verbose = assets.resolve(), False
            server.codex_bin, server.ssh_config_path = o.runtime.codex_bin, None
            server.goal_subagent_configuration_enabled = False
            server.codex_account_service = CodexAccountService(o.runtime.coordination_runtime_root)
            server.action_service, server.action_store = o.transport.core.actions, o.transport.core.actions.store
            server.lark_cli_resolution = LarkCliResolution(command="lark-cli", available=True,
                source="explicit", version="synthetic-browser-fixture", error_code=None)
            server.lark_runner, server.lark_private_conversations = provider, o.transport
            server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {
                "steward-app": {"status": "listening"}, "notes-app": {"status": "stopped"}}, close=lambda: None)
            server.lark_app_setup_manager = SimpleNamespace(session=None, close=lambda: None)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        service = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {}, runtime_root=o.store.root.parent,
            runtime_controller=o.runtime, private_conversations=o.transport)
        worker = threading.Thread(target=service._reconcile_private, daemon=True)
        worker.start()
        ready = {"urls": [f"http://127.0.0.1:{s.server_port}/chat/?settings=lark" for s in servers],
                 "binding_id": o.binding["binding_id"], "synthetic_only": True}
        (args.output / "ready.json").write_text(json.dumps(ready))
        print(json.dumps(ready), flush=True)
        recovered = False
        try:
            while not stop.wait(.5):
                o.provider.owner_ready = not (args.output / "provider-unavailable").exists()
                if not recovered and (args.output / "recover-source").exists():
                    o.tasks([{"id": "todo_missing", "text": "Keep this source unread until its native authority is restored."}], goal="unavailable")
                    recovered = True
                methods = [json.loads(line).get("method") for line in o.capture.read_text().splitlines()] if o.capture.exists() else []
                (args.output / "observation.json").write_text(json.dumps({
                    "health": o.attention.health(o.binding["binding_id"]), "provider_writes": len(o.provider.writes),
                    "model_calls": methods.count("turn/start"), "recovered": recovered,
                    "worker_alive": worker.is_alive(),
                    "receipts": [json.loads(p.read_text()) for p in (o.attention.root / "receipts" / o.binding["binding_id"]).glob("*.json")]}))
        finally:
            service._closed.set()
            worker.join(10)
            for server in servers:
                server.shutdown()
                server.server_close()
            for fixture in (owner_fixture, ordinary_fixture):
                try:
                    next(fixture)
                except StopIteration:
                    pass


if __name__ == "__main__":
    main()
