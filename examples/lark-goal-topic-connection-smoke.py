#!/usr/bin/env python3
"""End-to-end smoke for local Lark App and Goal Topic connection APIs."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Standalone smoke imports follow the repository-path bootstrap above.
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler  # noqa: E402
from loopx.chat_runtime import ChatRuntimeController  # noqa: E402
from loopx.chat_store import ChatSessionStore  # noqa: E402
from loopx.capabilities.native_chat.project_context import ChatProjectContexts  # noqa: E402
from loopx.extensions.lark.goal_channel_contracts import binding_for_goal, read_goal_channel_binding  # noqa: E402
from loopx.extensions.lark.goal_channel_targets import read_goal_channel_targets  # noqa: E402


APP_ID = "cli_public_fixture"
CHAT_ID = "oc_public_fixture"


class FakeLarkAppSetupManager:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict[str, Any]] = {}

    def start(self, *, app_ref: str, brand: str) -> dict[str, Any]:
        setup_id = f"lark_setup_{len(self.snapshots) + 1}"
        snapshot = {
            "setup_id": setup_id,
            "status": "waiting_for_feishu",
            "app_ref": app_ref,
            "verification_url": f"https://open.feishu.cn/page/cli?user_code={setup_id}",
            "error": None,
        }
        self.snapshots[setup_id] = snapshot
        return dict(snapshot)

    def snapshot(self, setup_id: str) -> dict[str, Any]:
        snapshot = dict(self.snapshots[setup_id])
        snapshot["status"] = "ready"
        return snapshot

    def cancel(self, setup_id: str) -> dict[str, Any]:
        self.snapshots[setup_id]["status"] = "cancelled"
        return dict(self.snapshots[setup_id])

    def close(self) -> None:
        return


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "codex/fixture", str(project)], check=True)
    subprocess.run(
        ["git", "-C", str(project), "remote", "add", "origin", "https://github.com/loopx-ai/loopx.git"],
        check=True,
    )
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": "goal-alpha",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-alpha/ACTIVE_GOAL_STATE.md",
                        "domain": "product-engineering",
                        "status": "active",
                        "objective": "Alpha delivery",
                        "adapter": {"kind": "generic_project_goal_v0"},
                        "coordination": {"registered_agents": ["agent-alpha"]},
                    },
                    {
                        "id": "goal-beta",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-beta/ACTIVE_GOAL_STATE.md",
                        "domain": "product-engineering",
                        "status": "active",
                        "objective": "Beta delivery",
                        "adapter": {"kind": "generic_project_goal_v0"},
                        "coordination": {"registered_agents": ["agent-alpha"]},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, runtime, registry_path


def fake_lark_runner(state: dict[str, Any]):
    def run(args: list[str], _cwd: object, _timeout: object) -> dict[str, Any]:
        state.setdefault("calls", []).append(list(args))
        if args[-2:] == ["profile", "list"]:
            payload: Any = [{"name": "mew", "appId": APP_ID, "brand": "feishu", "active": True}]
        elif "auth" in args and "status" in args:
            payload = {
                "appId": APP_ID,
                "identities": {
                    "bot": {"available": True, "verified": True, "appName": "LoopX Mew"}
                },
            }
        elif "+chat-list" in args or "+chat-search" in args:
            payload = {"data": {"chats": [{"chat_id": CHAT_ID, "name": "Product group"}]}}
        elif "chats" in args and "get" in args:
            payload = {"data": {"chat_id": CHAT_ID, "name": "Product group"}}
        elif "+chat-members-list" in args:
            payload = {"data": {"chats": [{"app_id": APP_ID}]}}
        elif "+messages-send" in args:
            text = args[args.index("--text") + 1]
            message_id = "om_topic_alpha" if "goal-alpha" in text else "om_topic_beta"
            state.setdefault("messages", {})[message_id] = text
            payload = {"data": {"message_id": message_id}}
        elif "+messages-mget" in args:
            message_id = args[args.index("--message-ids") + 1]
            payload = {
                "data": {
                    "chats": [
                        {
                            "message_id": message_id,
                            "chat_id": CHAT_ID,
                            "body": {"content": state.get("messages", {}).get(message_id, "")},
                        }
                    ]
                }
            }
        else:
            return {"returncode": 1, "stdout": "", "stderr": "unexpected command"}
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}

    return run


def request(base: str, path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Origin": base},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _project, runtime, registry_path = write_fixture(Path(tmp))
        state: dict[str, Any] = {}
        port = free_port()
        server = ChatHTTPServer(("127.0.0.1", port), ChatRequestHandler)
        server.registry_path = registry_path
        server.runtime_root_override = str(runtime)
        server.chat_store = ChatSessionStore(runtime)
        server.runtime_controller = ChatRuntimeController(
            store=server.chat_store, registry_path=registry_path, codex_bin="codex",
            project_contexts=ChatProjectContexts([Path(tmp)]),
        )
        server.lark_runner = fake_lark_runner(state)
        server.lark_app_setup_manager = FakeLarkAppSetupManager()
        server.verbose = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            contexts = request(base, "/api/chat/goals/contexts")
            assert contexts["goals"][0]["repository"]["read_only"] is True, contexts
            assert str(Path(tmp)) not in json.dumps(contexts), contexts

            apps = request(base, "/api/chat/lark/apps")
            assert apps["apps"] == [
                {
                    "app_ref": "mew",
                    "label": "LoopX Mew",
                    "brand": "feishu",
                    "active": True,
                    "ready": True,
                    "reply_ready": True,
                    "health_error_code": None,
                }
            ], apps

            setup = request(
                base,
                "/api/chat/lark/app-setups",
                method="POST",
                body={"app_ref": "loopx-workspace-bot", "brand": "feishu"},
            )
            assert setup["status"] == "waiting_for_feishu", setup
            assert setup["verification_url"].startswith("https://open.feishu.cn/page/cli?"), setup
            setup_readback = request(base, f"/api/chat/lark/app-setups/{setup['setup_id']}")
            assert setup_readback["status"] == "ready", setup_readback

            cancelled_setup = request(
                base,
                "/api/chat/lark/app-setups",
                method="POST",
                body={"app_ref": "loopx-cancelled-bot", "brand": "feishu"},
            )
            cancelled = request(
                base,
                f"/api/chat/lark/app-setups/{cancelled_setup['setup_id']}",
                method="DELETE",
            )
            assert cancelled["status"] == "cancelled", cancelled

            chats = request(base, "/api/chat/lark/chats?app_ref=mew&query=product")
            assert chats["chats"] == [{"chat_id": CHAT_ID, "chat_name": "Product group"}], chats

            for goal_id in ("goal-alpha", "goal-beta"):
                body = {
                    "app_ref": "mew",
                    "chat_id": CHAT_ID,
                    "chat_name": "Product group",
                    "goal_id": goal_id,
                    "incoming_mode": "mentions",
                    "agent_id": "agent-alpha",
                }
                preview = request(base, "/api/chat/lark/connections", method="POST", body={**body, "execute": False})
                assert preview["status"] == "preview_ready", preview
                connected = request(base, "/api/chat/lark/connections", method="POST", body={**body, "execute": True})
                assert connected["status"] == "connected" and connected["readback_verified"] is True, connected
                reconnected = request(base, "/api/chat/lark/connections", method="POST", body={**body, "execute": True})
                assert reconnected["status"] == "connected" and reconnected["readback_verified"] is True, reconnected
                assert reconnected["external_write_performed"] is False, reconnected

            connections = request(base, "/api/chat/lark/connections")
            assert len(connections["connections"]) == 2, connections
            assert {row["goal_id"] for row in connections["connections"]} == {"goal-alpha", "goal-beta"}
            assert {row["agent_id"] for row in connections["connections"]} == {"agent-alpha"}
            assert {row["ingress_mode"] for row in connections["connections"]} == {"async_inbox"}
            assert "oc_" not in json.dumps(connections) and "om_" not in json.dumps(connections)

            target_path = runtime / "goal-channel-targets.json"
            binding_path = registry_path.parent / "goal-channel.json"
            assert len(read_goal_channel_targets(target_path)["targets"]) == 1
            payload = read_goal_channel_binding(binding_path)
            bindings = {goal_id: binding_for_goal(payload, goal_id, agent_id="agent-alpha") for goal_id in ("goal-alpha", "goal-beta")}
            assert bindings["goal-alpha"]["topic"]["root_message_id"] != bindings["goal-beta"]["topic"]["root_message_id"]

            disconnected = request(
                base,
                "/api/chat/lark/connections?goal_id=goal-alpha&connection_id=" + bindings["goal-alpha"]["connection_id"],
                method="DELETE",
            )
            assert disconnected["status"] == "disconnected", disconnected
            remaining = request(base, "/api/chat/lark/connections")
            assert [row["goal_id"] for row in remaining["connections"]] == ["goal-beta"], remaining
        finally:
            server.runtime_controller.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    print("lark-goal-topic-connection-smoke: ok")


if __name__ == "__main__":
    main()
