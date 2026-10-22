"""Disposable real completed-Todo HTTP backend with one held filesystem read."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlparse

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.todos import list_readback
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "control_plane"))
from canonical_authority_fixture import initialize_canonical_authority


def main():
    with tempfile.TemporaryDirectory(prefix="loopx-completed-pages-") as directory:
        root = Path(directory).resolve()
        registry = root / "registry.json"
        goals = []
        for goal_id in ("slow-goal", "progress-projection"):
            project = root / goal_id
            project.mkdir()
            (project / "state.md").write_text("# Synthetic Goal\n\n## Agent Todo\n" + "".join(
                f"- [x] Retained task {index}\n  <!-- loopx:todo todo_id=todo_retained_{index} status=done task_class=advancement_task -->\n"
                for index in range(85)
            ) + "\n## User Todo\n", encoding="utf-8")
            records = [{"schema_version": "todo_item_v0", "todo_id": f"todo_retained_{index}",
                        "role": "agent", "status": "done", "done": True,
                        "archive_state": "archive", "source_section": "Completed Work Archive",
                        "text": f"Retained task {index}", "task_class": "advancement_task",
                        "completed_at": f"2026-01-01T00:00:00.{index:03d}Z"}
                       for index in range(85)]
            projection = build_todo_runtime_shadow_projection(goal_id=goal_id, todos=records, handoff_mode="soft_claim")
            initialize_canonical_authority(root / "runtime", goal_id, projection,
                                           state_path=project / "state.md", provider="sqlite")
            goals.append({"id": goal_id, "repo": str(project), "state_file": "state.md"})
        registry.write_text(json.dumps({"common_runtime_root": str(root / "runtime"), "goals": goals}))

        def fingerprints():
            return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in root.rglob("*") if path.is_file() and root / "runtime" not in path.parents}

        def authority():
            return {goal["id"]: read_canonical_todos_if_promoted(
                runtime_root=root / "runtime", goal_id=goal["id"])
                for goal in goals}

        before, authority_before = fingerprints(), authority()
        release = threading.Event()
        original = list_readback.list_goal_todos

        def held_read(*args, **kwargs):
            if kwargs.get("goal_id") == "slow-goal":
                print(json.dumps({"event": "slow-read"}), flush=True)
                if not release.wait(30):
                    raise TimeoutError("Slow history fixture was not released")
            return original(*args, **kwargs)

        class Handler(ChatRequestHandler):
            def do_POST(self):
                self.server.posts += 1
                self._send_error("This fixture permits reads only", status=405)

            def _completed_todos(self):
                query = parse_qs(urlparse(self.path).query)
                if self.server.fail_page and query.get("goal_id") == ["progress-projection"] and query.get("cursor"):
                    return self._send_error("Synthetic history read failure", status=503)
                return super()._completed_todos()

        server = ChatHTTPServer(("127.0.0.1", 0), Handler)
        server.registry_path, server.runtime_root_override, server.verbose = registry, None, False
        server.fail_page, server.posts = False, 0
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        list_readback.list_goal_todos = held_read
        print(json.dumps({"event": "ready", "origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "release":
                    release.set()
                elif command in {"fault", "recover"}:
                    server.fail_page = command == "fault"
                elif command == "inspect":
                    print(json.dumps({"event": "inspect", "store_unchanged": fingerprints() == before and authority() == authority_before,
                                      "posts": server.posts}), flush=True)
                    continue
                else:
                    raise ValueError("Unknown completed-history fixture command")
                print(json.dumps({"event": command}), flush=True)
        finally:
            release.set()
            list_readback.list_goal_todos = original
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
