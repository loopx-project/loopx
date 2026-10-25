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
import sys
from urllib.parse import parse_qs, urlencode, urlparse

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
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.capabilities.benchmark_toolkit.trajectory_assessment_store import DEFAULT_CONFIG, register_pack, inspect_pack, execute_pack

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "control_plane"))
from canonical_authority_fixture import initialize_canonical_authority


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--assets-dir", type=Path, help="Frozen packaged UI for before/after inspection.")
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
            initialize_canonical_authority(runtime, captured_goal, build_todo_runtime_shadow_projection(
                goal_id=captured_goal, todos=capture.get("todos", []), handoff_mode="soft_claim"), state_path=state, provider="sqlite")
            original.update({path: path.read_bytes() for path in (state, log)})
        registry = root / "registry.json"
        registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": goals}))
        original[registry] = registry.read_bytes()
        # Optional synthetic analysis uses the real pack verifier, process/job
        # owner and HTTP reader. It never enables model dispatch from the page.
        fixture_config = {**DEFAULT_CONFIG, "research_synthesis": False}
        fixture_config.pop("research_synthesis_revision", None)
        for capture in captures:
            for index, analysis in enumerate(capture.get("analyses", [])):
                source_root = root / f"analysis-{capture['goal_id']}-{index}"
                source_root.mkdir()
                for name, text in analysis["artifacts"].items():
                    path = source_root / name
                    assert path.parent == source_root, "Fixture artifacts must be flat"
                    path.write_text(text, encoding="utf-8")
                input_path = source_root / "input.json"
                input_path.write_text(json.dumps(analysis["input"], ensure_ascii=False))
                scope = dict(runtime_root=runtime, owner_goal_id=capture["goal_id"])
                registered = register_pack(**scope, input_path=input_path, source_root=source_root, title=analysis["title"],
                                           config=analysis.get("config", fixture_config),
                                           native_outcomes_json=analysis.get("native_outcomes_json"))
                for language, output in analysis["outputs"].items():
                    provider = root / f"analysis-provider-{index}-{language}"
                    provider.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                        f"sys.stdin.read()\nPath(sys.argv[sys.argv.index('-o')+1]).write_bytes({json.dumps(output, ensure_ascii=False).encode()!r})\n"
                        "print('{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":10,\"output_tokens\":5}}')\n")
                    provider.chmod(0o700)
                    selected = inspect_pack(**scope, pack_id=registered["pack_id"], output_language=language)
                    execute_pack(**scope, pack_id=registered["pack_id"], analysis_key=selected["analysis_key"], output_language=language, codex=str(provider))
        metrics = {"history_reads": 0, "mutation_attempts": 0}

        class Handler(ChatRequestHandler):
            def _send_json(self, payload, *, status=200):
                parsed = urlparse(self.path)
                if status == 200 and parsed.path == "/status.json" and "run_history" in payload:
                    goal = parse_qs(parsed.query).get("goal_id", [""])[0]
                    query = "?" + urlencode({"goal_id": goal}) if goal else ""
                    payload = {**payload, "local_dashboard_api": {
                        **(payload.get("local_dashboard_api") or {}),
                        "read_only_sources": [{"id": "research-preview", "label": "Research preview",
                            "status_url": f"http://127.0.0.1:{self.server.server_port}/status.json{query}"}],
                    }}
                return super()._send_json(payload, status=status)

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/status.json" and parse_qs(parsed.query).get("view") == ["workspace-directory"]:
                    # Like the runtime workspace reader, use the full-status
                    # fallback: directory-only hydration omits source identity.
                    return self._send_error("Use the captured source's full status read.", status=404)
                if path == "/fixture/readback":
                    return self._send_json({"ok": True, **metrics,
                        "source_unchanged": all(path.read_bytes() == value for path, value in original.items()),
                        "fixture_sha256": source_digests[goal_id], "fixtures": source_digests})
                if path == "/api/chat/explore-results":
                    metrics["history_reads"] += 1
                if path in {"/healthz", "/status.json", "/api/chat/explore-results",
                    "/api/chat/capabilities", "/api/chat/endpoints", "/api/chat/sessions", "/api/chat/goal-configuration",
                    "/api/chat/research-analysis", "/api/chat/research-analysis/citation",
                    "/api/actions", "/api/chat/goals/contexts", "/api/chat/goal-results",
                    "/api/chat/sidebar-goal-order", "/api/chat/usage-statistics", "/api/chat/todo/detail",
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
        server.assets_dir = args.assets_dir or default_chat_assets_dir()
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
