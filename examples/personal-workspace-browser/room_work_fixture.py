"""Synthetic Lark transport around a disposable real room-work HTTP backend."""
import json
import os
import signal
from pathlib import Path
import sys
import tempfile

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.extensions.lark import goal_channel_work as work
from loopx.extensions.lark.goal_channel_contracts import write_goal_channel_binding

ROOT = Path(__file__).resolve().parents[2]


class SyntheticRoom:
    def __init__(self):
        self.messages = []
        self.fail_send = False

    def __call__(self, args, cwd, timeout):
        payload = {}
        if "auth" in args:
            payload = {"appId": "cli_room_fixture", "identities": {"bot": {"available": True, "verified": True, "appName": "Fixture Bot"}}}
        elif "+chat-members-list" in args:
            payload = {"data": {"bots": [{"app_id": "cli_room_fixture"}]}}
        elif "+messages-send" in args:
            card = json.loads(args[args.index("--content") + 1])
            message = {"message_id": f"om_room_{len(self.messages)}", "chat_id": "oc_room_fixture",
                "sender": {"sender_type": "app", "id": "cli_room_fixture"}, "body": {"content": json.dumps(card)}}
            self.messages.append(message)
            if self.fail_send:
                self.fail_send = False
                return {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True}
            payload = {"data": {"message_id": message["message_id"]}}
        elif "+messages-mget" in args or "+chat-messages-list" in args:
            payload = {"data": {"items": self.messages, "has_more": False}}
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}


def serve(root):
    sys.path.insert(0, str(ROOT / "tests/control_plane"))
    from canonical_authority_fixture import initialize_canonical_authority

    runtime, registry, state = root / "runtime", root / ".loopx/registry.json", root / "state.md"
    registry.parent.mkdir()
    state.write_text("## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{"id": "product-release", "repo": str(root),
        "state_file": str(state), "coordination": {"registered_agents": ["agent-a", "agent-b"]}}]}))
    todos = [{"schema_version": "todo_item_v0", "todo_id": "todo_room_fixture", "role": "agent", "status": "open", "done": False,
        "text": "Synthetic private task text", "archive_state": "active", "source_section": "Agent Todo", "index": 1, "task_class": "advancement_task"}]
    projection = build_todo_runtime_shadow_projection(goal_id="product-release", todos=todos, handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "product-release", projection, state_path=state)
    binding_path = registry.parent / "goal-channel.json"
    binding = {"schema_version": "loopx_goal_channel_lark_binding_v0", "bindings": {"product-release": {
        "schema_version": "loopx_goal_channel_connection_set_v0", "connections": {actor: {"goal_id": "product-release", "agent_id": actor,
            "provider": "lark", "enabled": True, "channel": {"chat_id": "oc_room_fixture"}, "identity": {"mode": "project_bot",
                "sender_identity": "bot", "sender_profile": "room-fixture", "bot_app_id": "cli_room_fixture", "bot_display_name": "Fixture Bot", "cli_bin": "fixture-cli"}}
            for actor in ["agent-a", "agent-b"]}}}}
    write_goal_channel_binding(binding_path, binding)
    room = SyntheticRoom()
    # The fixture explicitly enables its synthetic extension; no real profile is changed.
    work.resolve_extension_activation = lambda *a, **k: {"enabled": True}

    class Handler(ChatRequestHandler):
        def do_GET(self):
            if self.path == "/fixture/readback":
                return self._send_json({"snapshot": read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="product-release"),
                    "messages": len(room.messages)})
            return super().do_GET()

        def do_POST(self):
            if self.path == "/fixture/lose-message-response":
                room.fail_send = True
                return self._send_json({"ok": True})
            return super().do_POST()

    server = ChatHTTPServer(("127.0.0.1", 0), Handler)
    server.registry_path = registry
    server.runtime_root = runtime
    server.runtime_root_override = str(runtime)
    server.lark_runner = room
    server.verbose = False
    print(json.dumps({"port": server.server_port, "registry": str(registry), "module": work.__file__}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    finally:
        from loopx.control_plane.effect_runtime import effect_runtime_result
        effect_runtime_result("runtime.shutdown", {}, retry_safe=False)
        server.server_close()


if __name__ == "__main__":
    root = Path(sys.argv[1])
    scratch = root / "tmp"
    scratch.mkdir()
    os.environ["TMPDIR"] = str(scratch)
    tempfile.tempdir = str(scratch)
    serve(root)
