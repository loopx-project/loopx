"""Packaged Chat confirmation recovery on a disposable authority."""

from pathlib import Path
import json
import sys
from tempfile import TemporaryDirectory

import pytest

REPO = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(REPO), str(REPO / "tests/control_plane")]

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx import chat_server
from loopx.chat_action_store import ChatActionStore
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.todos.mutation_api import add_goal_todo


def main(provider):
    with TemporaryDirectory(prefix="loopx-confirmation-fixture-") as directory, pytest.MonkeyPatch.context() as patch:
        scratch = Path(directory)
        isolate_sqlite_runtime(scratch, patch)
        registry, runtime, _ = promoted_create_fixture(scratch, provider=provider)
        lost = {}
        checkpoint = ChatActionStore.save_checkpoint
        for content in ("Review release checklist", "Verify documentation links", "Check failure handling"):
            add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text=content)

        def interrupt(self, proposal_id, *, step, receipt, **kwargs):
            if step == "todo_created" and proposal_id not in lost:
                lost[proposal_id] = receipt["canonical_receipt"]
                raise OSError("test: first create checkpoint write interrupted")
            return checkpoint(self, proposal_id, step=step, receipt=receipt, **kwargs)

        class FixtureHandler(chat_server.ChatRequestHandler):
            def do_POST(self):
                if self.path != "/__confirmation_fixture/advance":
                    return super().do_POST()
                self._read_json()
                add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text="Independent authority update")
                self._send_json({"ok": True})

            def do_GET(self):
                if self.path != "/__confirmation_fixture":
                    try:
                        return super().do_GET()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
                self._send_json({"provider_revision": snapshot["provider_revision"],
                                 "ids": sorted(todo["todo_id"] for todo in snapshot["todos"]),
                                 "lost": lost})

        serve = chat_server.ChatHTTPServer.serve_forever

        def ready(server):
            print(json.dumps({"origin": f"http://127.0.0.1:{server.server_address[1]}"}), flush=True)
            return serve(server)

        patch.setattr(chat_server, "ChatRequestHandler", FixtureHandler)
        patch.setattr(chat_server.ChatHTTPServer, "serve_forever", ready)
        patch.setattr(ChatActionStore, "save_checkpoint", interrupt)
        try:
            chat_server.serve_chat(registry_path=registry, runtime_root_override=runtime, scan_roots=[scratch],
                                   lark_cli_bin="unavailable-test-lark-cli", codex_bin="unavailable-test-codex",
                                   port=0, goal_id="goal-a", assets_dir=Path(sys.argv[2]))
        finally:
            restart_effect_runtime(wait_for_exit=True)


if __name__ == "__main__":
    main(sys.argv[1])
