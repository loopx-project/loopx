"""Disposable accepted report owner for packaged download qualification; no model calls."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "managed-research-team"))
import research_team as demo
from synthetic_fixture import fixture
from acceptance import canonical_tasks
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler


def main():
    with tempfile.TemporaryDirectory(prefix="loopx-report-download-") as directory:
        root = Path(directory) / "team"
        demo.prepare(root, "sqlite")
        fixture(root)
        for actor, revision in (("analyst", "initial"), ("analyst", "corrected"),
                                ("reviewer", "initial"), ("reviewer", "corrected"), ("lead", "report")):
            demo.complete(root, actor, revision)
        read = demo.cli(root, "todo", "result-read", "--goal-id", demo.GOAL, "--todo-id", "todo_lead-report")
        digest = read["result"]["sha256"]
        result_path = root / "runtime/goals" / demo.GOAL / "result-objects" / digest
        original = result_path.read_bytes()
        before = canonical_tasks(root)

        class Handler(ChatRequestHandler):
            def do_POST(self):
                self.server.posts += 1
                self._send_error("Read-only download fixture", status=405)

        server = ChatHTTPServer(("127.0.0.1", 0), Handler)
        server.registry_path = root / "registry.json"
        server.runtime_root_override = str(root / "runtime")
        server.verbose, server.posts = False, 0
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"event": "ready", "origin": f"http://127.0.0.1:{server.server_port}",
                          "goal_id": demo.GOAL, "sha256": digest, "text": read["text"]}), flush=True)
        try:
            for line in sys.stdin:
                action = line.strip()
                if action == "withdraw":
                    result_path.write_text("changed source")
                elif action == "restore":
                    result_path.write_bytes(original)
                elif action != "inspect":
                    raise ValueError("Unknown fixture command")
                print(json.dumps({"event": action, "posts": server.posts,
                                  "authority_unchanged": canonical_tasks(root) == before,
                                  "sha256": hashlib.sha256(result_path.read_bytes()).hexdigest()}), flush=True)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
