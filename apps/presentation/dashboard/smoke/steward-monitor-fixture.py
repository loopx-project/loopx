"""Serve packaged UI over real native APIs/Core/Todo storage for manual QA.

Only the original transport and model are synthetic. Run from the repository
with the test extra; no real account, listener, business request or paid call is
used. The normal host wake service owns deadline dispatch. Use --baseline-assets
for a second view of the same synthetic state with the previous UI bundle.
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--baseline-assets", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[4]
    sys.path[:0] = [str(repo), str(repo / "tests")]
    from test_native_steward_private import steward
    from test_native_steward_managed_mode import original, storage
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_loopx_mode import DelegationWakeService
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    from loopx.presentation.codex_accounts_api import CodexAccountService

    args.output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="loopx-steward-monitor-qa-") as directory:
        fixture = steward.__wrapped__(Path(directory))
        data = next(fixture)
        store, runtime, provider, transport, binding, capture, workspace = data
        sid, managed = original(data)
        storage(runtime, "file")
        plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
        managed.prepare(binding["binding_id"], session_id=sid, execute=True, expected_plan_id=plan["plan_id"])
        original_session = store.load_session(sid)
        def model_requests():
            return [row for line in capture.read_text().splitlines()
                if (row := json.loads(line)).get("method") in {"turn/start", "thread/goal/set"}]
        before_calls = model_requests()
        servers = []
        for assets in [args.assets, *([args.baseline_assets] if args.baseline_assets else [])]:
            server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
            server.chat_store, server.runtime_controller = store, runtime
            server.registry_path, server.runtime_root_override = runtime.registry_path, str(store.root.parent)
            server.runtime_root = runtime.coordination_runtime_root
            server.scan_roots, server.limit, server.selected_goal_id = [workspace], 20, None
            server.assets_dir, server.verbose = assets.resolve(), False
            server.codex_bin, server.ssh_config_path = runtime.codex_bin, None
            server.goal_subagent_configuration_enabled = False
            server.codex_account_service = CodexAccountService(runtime.coordination_runtime_root)
            server.action_service, server.action_store = transport.core.actions, transport.core.actions.store
            server.lark_cli_resolution = LarkCliResolution(command="lark-cli", available=True,
                source="explicit", version="synthetic-browser-fixture", error_code=None)
            server.lark_runner, server.lark_private_conversations = provider, transport
            server.lark_goal_topic_runtime = SimpleNamespace(health_snapshot=lambda: {
                "steward-app": {"status": "listening"}, "notes-app": {"status": "stopped"}}, close=lambda: None)
            server.lark_app_setup_manager = SimpleNamespace(session=None, close=lambda: None)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            servers.append(server)
        service = DelegationWakeService(runtime, interval=.1).start()
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        ready = {"urls": [f"http://127.0.0.1:{server.server_port}/chat/?settings=lark" for server in servers],
            "binding_id": binding["binding_id"], "session_id": sid, "synthetic_only": True}
        (args.output / "ready.json").write_text(json.dumps(ready))
        print(json.dumps(ready), flush=True)
        try:
            while not stop.wait(.5):
                provider.owner_ready = not (args.output / "provider-unavailable").exists()
                (args.output / "observation.json").write_text(json.dumps({
                    # Opening the web workspace may prepare its own ordinary
                    # manager thread. That is distinct from a model Turn or
                    # replacement of the original private steward Session.
                    "no_model_call": model_requests() == before_calls,
                    "same_original_session": all(store.load_session(sid).get(key) == original_session.get(key)
                        for key in ("session_id", "channel_id", "upstream_thread_id", "steward_context")),
                    "total_sessions": len(store.list_sessions()),
                    "identity": managed.health(binding["binding_id"]),
                    "goals": json.loads(runtime.registry_path.read_text())["goals"]}))
        finally:
            service.close()
            for server in servers:
                server.shutdown()
                server.server_close()
            try:
                next(fixture)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
