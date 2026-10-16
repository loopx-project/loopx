"""Disposable read-only HTTP owner for the redacted EdgeBench display capture.

The history route and pagination are production code. No captured runtime,
benchmark score, Agent authority or model dispatch is imported.
"""
import argparse
import hashlib
import json
from pathlib import Path
import signal
import tempfile
from urllib.parse import urlparse

from loopx.capabilities.explore.result_log import validate_explore_result_event
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir
from loopx.control_plane.effect_runtime import restart_effect_runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[2] / "apps/presentation/dashboard/src/data/fixtures/edgebench-research-history.json"
    source_bytes = source.read_bytes()
    capture = json.loads(source_bytes)
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    events = [validate_explore_result_event(event) for event in capture["events"]]
    goal_id = capture["goal_id"]
    assert all(event["goal_id"] == goal_id for event in events)
    with tempfile.TemporaryDirectory(prefix="loopx-research-display-") as directory:
        root = Path(directory)
        runtime = root / "runtime"
        state = root / "state.md"
        state.write_text("# Redacted research display capture\n\n## Agent Todo\n\n## Completed Work Archive\n")
        registry = root / "registry.json"
        registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
            "id": goal_id, "display_name": "EdgeBench · 脱敏研究历史", "repo": str(root),
            "state_file": state.name,
            "domain": "project-goal-control-plane",
            "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
        }]}))
        log = runtime / "goals" / goal_id / "explore-result-log.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events))
        original = {path: path.read_bytes() for path in (registry, state, log)}
        metrics = {"history_reads": 0, "mutation_attempts": 0}

        class Handler(ChatRequestHandler):
            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/fixture/readback":
                    return self._send_json({"ok": True, **metrics,
                        "source_unchanged": all(path.read_bytes() == value for path, value in original.items()),
                        "fixture_sha256": source_sha256})
                if path == "/api/chat/explore-results":
                    metrics["history_reads"] += 1
                if path in {"/healthz", "/status.json", "/api/chat/explore-results"} or path.startswith("/chat/"):
                    return super().do_GET()
                self._send_error("This display fixture does not configure execution services.", status=503)

            def do_POST(self):
                metrics["mutation_attempts"] += 1
                self._send_error("The research display fixture is read-only.", status=405)

            do_PUT = do_POST
            do_DELETE = do_POST

        server = ChatHTTPServer(("127.0.0.1", args.port), Handler)
        server.registry_path, server.runtime_root = registry, runtime
        server.runtime_root_override, server.selected_goal_id = str(runtime), goal_id
        server.scan_roots, server.limit, server.verbose = [], 10, False
        server.assets_dir = default_chat_assets_dir()
        def terminate(*_):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
        print(json.dumps({"port": server.server_port, "goal_id": goal_id, "event_count": len(events),
            "url": f"http://127.0.0.1:{server.server_port}/chat/?goalId={goal_id}&view=overview"}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            restart_effect_runtime()


if __name__ == "__main__":
    main()
