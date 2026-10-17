"""Disposable real completed-Todo HTTP backend with one held filesystem read."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import parse_qs, urlparse

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


def main():
    with tempfile.TemporaryDirectory(prefix="loopx-completed-pages-") as directory:
        root = Path(directory)
        registry = root / "registry.json"
        goals = []
        for goal_id in ("slow-goal", "progress-projection"):
            project = root / goal_id
            project.mkdir()
            (project / "state.md").write_text("# Synthetic Goal\n\n## Agent Todo\n" + "".join(
                f"- [x] Retained task {index}\n  <!-- loopx:todo todo_id=todo_retained_{index} status=done task_class=advancement_task -->\n"
                for index in range(85)
            ) + "\n## User Todo\n", encoding="utf-8")
            goals.append({"id": goal_id, "repo": str(project), "state_file": "state.md"})
        registry.write_text(json.dumps({"common_runtime_root": str(root / "runtime"), "goals": goals}))

        def fingerprints():
            return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in root.rglob("*") if path.is_file()}

        before = fingerprints()
        release = threading.Event()
        original = Path.read_text

        def held_read(path, *args, **kwargs):
            if path == root / "slow-goal" / "state.md":
                print(json.dumps({"event": "slow-read"}), flush=True)
                if not release.wait(30):
                    raise TimeoutError("Slow history fixture was not released")
            return original(path, *args, **kwargs)

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
        Path.read_text = held_read
        print(json.dumps({"event": "ready", "origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "release":
                    release.set()
                elif command in {"fault", "recover"}:
                    server.fail_page = command == "fault"
                elif command == "inspect":
                    print(json.dumps({"event": "inspect", "store_unchanged": fingerprints() == before,
                                      "posts": server.posts}), flush=True)
                    continue
                else:
                    raise ValueError("Unknown completed-history fixture command")
                print(json.dumps({"event": command}), flush=True)
        finally:
            release.set()
            Path.read_text = original
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    main()
