"""Disposable packaged UI + native HTTP/Core fixture; model/provider are synthetic.

Run with the project's development dependencies and --output-dir outside Git.
The two control files change synthetic provider readiness or start the existing
host wake/outbound owners. No real account, Goal, workspace or listener is used.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-assets-dir", type=Path)
    parser.add_argument("--invalid-editorial", action="store_true", help="Return a known invalid synthetic report to inspect owner cancellation and retained failure.")
    parser.add_argument("--managed-session", action="store_true", help="Exercise ordinary Goal Session native work with the existing task validator, instead of a scheduled report.")
    parser.add_argument("--managed-report", action="store_true", help="Configure a calendar report for an existing local Agent Session through the packaged Goal settings.")
    args = parser.parse_args()
    if args.managed_session and args.managed_report:
        parser.error("select one managed Session fixture journey")
    repo = Path(__file__).resolve().parents[2]
    sys.path[:0] = [str(repo), str(repo / "tests")]
    from test_native_steward_private import steward
    from test_native_steward_managed_mode import original, storage
    from test_native_steward_reports import install_model
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_loopx_mode import DelegationWakeService
    from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService
    from loopx.extensions.lark.cli_resolution import LarkCliResolution
    from loopx.presentation.codex_accounts_api import CodexAccountService
    from loopx.capabilities.native_chat.steward_reports import StewardReports

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="loopx-report-qa-") as directory:
        root = Path(directory)
        fixture = steward.__wrapped__(root)
        data = next(fixture)
        store, runtime, provider, transport, binding, capture, workspace = data
        managed_session = None
        if args.managed_report:
            import pytest
            from test_native_steward_reports import configured_local_report
            from loopx.configure_goal import configure_goal
            monkeypatch = pytest.MonkeyPatch()
            managed_session, local_reports, configuration, managed_todo_id = configured_local_report(
                data, root, monkeypatch, "sqlite", start_date="2999-01-01")
            sid = managed_session["session_id"]
            actor = local_reports.selected(sid)["actor"]
            managed = transport.managed_steward
            configure_goal(registry_path=runtime.registry_path, goal_id=actor["goal_id"],
                periodic_report_configuration={**configuration, "enabled": False}, execute=True)
            install_model(data, root, sid)
        elif args.managed_session:
            import pytest
            from test_native_steward_turn_host import managed_fixture
            monkeypatch = pytest.MonkeyPatch()
            _, managed_session, actor, managed_todo_id, artifact, _ = managed_fixture(data, root, monkeypatch)
            sid, managed = actor["session_id"], transport.managed_steward
        else:
            sid, managed = original(data)
            storage(runtime, "file")
            plan = managed.prepare(binding["binding_id"], session_id=sid)["plan"]
            actor = managed.prepare(binding["binding_id"], session_id=sid, execute=True,
                expected_plan_id=plan["plan_id"])["actor"]
            install_model(data, root, sid)
        transport.reconcile()
        if args.invalid_editorial:
            helper = root / "report_model.py"
            helper.write_text(helper.read_text().replace('"request_digest": request["request_digest"]', '"request_digest": "known-invalid"'))
        before_calls, before_messages = capture.read_bytes(), store.messages(sid)
        server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
        server.chat_store, server.runtime_controller = store, runtime
        server.registry_path, server.runtime_root_override = runtime.registry_path, str(store.root.parent)
        server.runtime_root = runtime.coordination_runtime_root
        server.scan_roots, server.limit, server.selected_goal_id = [workspace], 20, None
        current_assets = repo / "loopx/web/chat"
        baseline_assets = args.baseline_assets_dir.resolve() if args.baseline_assets_dir else current_assets
        if not (baseline_assets / "index.html").is_file():
            raise ValueError("baseline assets must be an existing packaged Chat bundle")
        server.assets_dir, server.verbose = baseline_assets, False
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
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        (output / "ready.json").write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/chat/",
            "fixture_pid": os.getpid(),
            "binding_id": binding["binding_id"], "session_id": sid,
            **({"managed_session_id": managed_session["session_id"], "goal_id": actor["goal_id"],
                "managed_todo_id": managed_todo_id} if managed_session else {})}))
        wake, outbound, delivery = None, None, None
        try:
            while not stop.wait(.2):
                server.assets_dir = current_assets if (output / "use-current-assets").exists() else baseline_assets
                provider.owner_ready = not (output / "provider-unavailable").exists()
                if wake is None and (output / "start-existing-owners").exists():
                    wake = DelegationWakeService(runtime, interval=.1).start()
                    if not args.managed_report:
                        outbound = LarkGoalTopicRuntimeService(snapshot_provider=lambda: {},
                            runtime_root=runtime.coordination_runtime_root, runtime_controller=runtime,
                            private_conversations=transport)
                        delivery = threading.Thread(target=outbound._reconcile_private)
                        delivery.start()
                (output / "observed.json").write_text(json.dumps({
                    "report": local_reports.health(sid) if args.managed_report else StewardReports(runtime).health(binding["binding_id"]),
                    "same_actor": local_reports.selected(sid)["actor"] == actor if args.managed_report else managed.health(binding["binding_id"])["session_id"] == actor["session_id"],
                    "history_prefix_retained": store.messages(sid)[:len(before_messages)] == before_messages,
                    "model_call_count": sum(json.loads(line).get("method") == "turn/start"
                        for line in capture.read_bytes()[len(before_calls):].splitlines()),
                    "provider_writes": len(provider.writes),
                    **({"managed_source_present": artifact.exists(),
                        "managed_session": store.load_session(managed_session["session_id"])} if args.managed_session else {}),
                    **({"managed_report": True,
                        "managed_session": store.load_session(managed_session["session_id"])} if args.managed_report else {}),
                    "synthetic_only": True}, ensure_ascii=False))
        finally:
            if wake:
                wake.close()
            if outbound:
                outbound.close()
                delivery.join(5)
            server.shutdown()
            server.server_close()
            if managed_session:
                monkeypatch.undo()
            try:
                next(fixture)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
