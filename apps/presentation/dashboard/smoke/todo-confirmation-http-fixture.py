"""Packaged Chat confirmation recovery on a disposable authority."""

from pathlib import Path
import json
import socket
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
from loopx.control_plane.todos import provider_projection


class InterruptedConfirmation(BaseException):
    """End the request before Chat can persist a failure response."""


def main(provider, interrupted_step):
    with TemporaryDirectory(prefix="loopx-confirmation-fixture-") as directory, pytest.MonkeyPatch.context() as patch:
        scratch = Path(directory)
        isolate_sqlite_runtime(scratch, patch)
        registry, runtime, _ = promoted_create_fixture(scratch, provider=provider)
        lost = {}
        interrupted = set()
        display = {"hold": False, "drop_reply": None, "gateway_reply": None, "hold_list": False, "hold_detail": False}
        write_state = provider_projection.atomic_write_state_text
        checkpoint = ChatActionStore.save_checkpoint
        for content in ("Review release checklist", "Verify documentation links", "Check failure handling"):
            add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text=content)

        def interrupt(self, proposal_id, *, step, receipt, **kwargs):
            if interrupted_step == "applying" and step == "todo_create_prepared" and proposal_id not in interrupted:
                interrupted.add(proposal_id)
                raise InterruptedConfirmation()
            if step == interrupted_step and proposal_id not in interrupted and not display["hold"]:
                interrupted.add(proposal_id)
                if step == "todo_created":
                    lost[proposal_id] = receipt["canonical_receipt"]
                raise OSError("test: first create checkpoint write interrupted")
            return checkpoint(self, proposal_id, step=step, receipt=receipt, **kwargs)

        def render(path, text, **kwargs):
            if display["hold"]:
                raise PermissionError("test: display disabled after authority commits")
            return write_state(path, text, **kwargs)

        class FixtureHandler(chat_server.ChatRequestHandler):
            def do_POST(self):
                if self.path == "/__confirmation_fixture/display":
                    patch_update = self._read_json()
                    for name in display.keys() & patch_update.keys():
                        display[name] = patch_update[name]
                    self._send_json({"ok": True})
                    return
                if self.path != "/__confirmation_fixture/advance":
                    try:
                        return super().do_POST()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    except InterruptedConfirmation:
                        self.close_connection = True
                        return
                self._read_json()
                add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent", text="Independent authority update")
                self._send_json({"ok": True})

            def do_GET(self):
                if self.path != "/__confirmation_fixture":
                    route = self.path.split("?", 1)[0]
                    if ((display["hold_list"] and route == "/api/actions")
                            or (display["hold_detail"] and route.startswith("/api/actions/"))):
                        self._send_json({"error": "test: list unavailable"}, status=503)
                        return
                    try:
                        return super().do_GET()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                snapshot = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
                self._send_json({"provider_revision": snapshot["provider_revision"],
                                 "ids": sorted(todo["todo_id"] for todo in snapshot["todos"]),
                                 "lost": lost})

            def _send_json(self, payload, *, status=200):
                gateway = display["gateway_reply"]
                if gateway and self.path == f"/api/actions/{gateway}/apply":
                    display["gateway_reply"] = None
                    body = b"<html><title>Bad Gateway</title></html>"
                    self.send_response(502)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                operation = display["drop_reply"]
                if operation and self.path == f"/api/actions/{operation}/apply":
                    display["drop_reply"] = None
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_WR)
                    return
                return super()._send_json(payload, status=status)

        serve = chat_server.ChatHTTPServer.serve_forever

        def ready(server):
            print(json.dumps({"origin": f"http://127.0.0.1:{server.server_address[1]}"}), flush=True)
            return serve(server)

        patch.setattr(chat_server, "ChatRequestHandler", FixtureHandler)
        patch.setattr(chat_server.ChatHTTPServer, "serve_forever", ready)
        patch.setattr(ChatActionStore, "save_checkpoint", interrupt)
        patch.setattr(provider_projection, "atomic_write_state_text", render)
        try:
            chat_server.serve_chat(registry_path=registry, runtime_root_override=runtime, scan_roots=[scratch],
                                   lark_cli_bin="unavailable-test-lark-cli", codex_bin="unavailable-test-codex",
                                   port=0, goal_id="goal-a", assets_dir=Path(sys.argv[2]))
        finally:
            restart_effect_runtime(wait_for_exit=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[3])
