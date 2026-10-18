"""Disposable read-only HTTP owner for a captured Explore event log.

The default is three real EdgeBench research checkpoints. The history route
and pagination are production code; execution state is never restored and
model dispatch is never called. Explicit fixture paths support regressions.
"""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import tempfile
from urllib.parse import urlparse

from loopx.capabilities.explore.result_log import validate_explore_result_event
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_server import (
    ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir, resolve_lark_cli_for_runtime,
    DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH, DEFAULT_EXTENSION_PROJECTION_PATH,
)
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.effect_runtime import restart_effect_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--fixture", type=Path, action="append",
        help="Captured-event wrapper; repeat for independently selectable Goals.")
    args = parser.parse_args()
    fixtures = Path(__file__).resolve().parents[2] / "apps/presentation/dashboard/src/data/fixtures"
    sources = args.fixture or [fixtures / name for name in (
        "edgebench-research-history.json", "edgebench-graph-checkpoint-history.json",
        "edgebench-ad-checkpoint-history.json")]
    captures = []
    original = {}
    for source in sources:
        source_bytes = source.read_bytes()
        capture = json.loads(source_bytes)
        capture["events"] = [validate_explore_result_event(event) for event in capture["events"]]
        assert all(event["goal_id"] == capture["goal_id"] for event in capture["events"])
        assert capture["goal_id"] not in {item["goal_id"] for item in captures}, "Duplicate captured Goal"
        captures.append(capture)
        original[source] = source_bytes
    goal_id = captures[0]["goal_id"]
    source_digests = {capture["goal_id"]: hashlib.sha256(original[source]).hexdigest()
        for source, capture in zip(sources, captures)}
    with tempfile.TemporaryDirectory(prefix="loopx-research-display-") as directory:
        root = Path(directory)
        runtime = root / "runtime"
        goals = []
        for capture in captures:
            captured_goal = capture["goal_id"]
            state = root / f"{captured_goal}.md"
            state.write_text("# Read-only captured research history\n\n## Agent Todo\n\n## Completed Work Archive\n")
            goals.append({"id": captured_goal,
                **({"parent_goal_id": capture["parent_goal_id"]} if capture.get("parent_goal_id") else {}),
                "display_name": capture.get("display_name", "Captured research history"),
                "repo": str(root), "state_file": state.name,
                "domain": "project-goal-control-plane",
                "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"}})
            log = runtime / "goals" / captured_goal / "explore-result-log.jsonl"
            log.parent.mkdir(parents=True)
            log.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in capture["events"]))
            original.update({path: path.read_bytes() for path in (state, log)})
        registry = root / "registry.json"
        registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": goals}))
        original[registry] = registry.read_bytes()
        metrics = {"history_reads": 0, "mutation_attempts": 0}

        class Handler(ChatRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/fixture/readback":
                    return self._send_json({"ok": True, **metrics,
                        "source_unchanged": all(path.read_bytes() == value for path, value in original.items()),
                        "fixture_sha256": source_digests[goal_id], "fixtures": source_digests})
                if path == "/api/chat/explore-results":
                    metrics["history_reads"] += 1
                if path in {"/healthz", "/status.json", "/api/chat/explore-results",
                    "/api/chat/capabilities", "/api/chat/endpoints", "/api/chat/sessions",
                    "/api/actions", "/api/chat/goals/contexts", "/api/chat/goal-results",
                    "/api/chat/sidebar-goal-order", "/api/chat/usage-statistics",
                    "/api/chat/delivery-review", "/api/chat/goal-acceptance", "/api/chat/projects",
                    DEFAULT_EXTENSION_PRESENTATION_SURFACES_PATH, DEFAULT_EXTENSION_PROJECTION_PATH,
                } or path.startswith("/chat/"):
                    return super().do_GET()
                self._send_error("This display fixture does not configure execution services.", status=503)

            def do_POST(self):
                metrics["mutation_attempts"] += 1
                self._send_error("The research display fixture is read-only.", status=405)

            do_PUT = do_POST
            do_DELETE = do_POST

        server = ChatHTTPServer(("127.0.0.1", args.port), Handler)
        server.registry_path, server.runtime_root = registry, runtime
        server.runtime_root_override, server.selected_goal_id = str(runtime), None
        server.scan_roots, server.limit, server.verbose = [], 10, False
        server.assets_dir = default_chat_assets_dir()
        # Native empty stores keep ordinary read-only workspace hydration real.
        # No captured execution state or machine runtime is admitted here.
        server.chat_store = ChatSessionStore(runtime)
        server.action_store = ChatActionStore(runtime / "chat/actions")
        unavailable = str(root / "no-execution-binary")
        server.runtime_controller = ChatRuntimeController(store=server.chat_store, registry_path=registry,
            codex_bin=unavailable, claude_bin=unavailable, kiro_cli_bin=unavailable)
        server.action_service = ChatActionService(store=server.action_store, registry_path=registry)
        server.goal_subagent_configuration_enabled = False
        server.lark_cli_resolution = resolve_lark_cli_for_runtime(runtime_root=runtime, explicit=unavailable)
        def terminate(*_):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
        print(json.dumps({"port": server.server_port, "goal_id": goal_id,
            "goal_ids": [item["goal_id"] for item in captures],
            "event_count": sum(len(item["events"]) for item in captures),
            "url": f"http://127.0.0.1:{server.server_port}/chat/?goalId={goal_id}&view=overview"}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            server.runtime_controller.close()
            restart_effect_runtime()


if __name__ == "__main__":
    main()
