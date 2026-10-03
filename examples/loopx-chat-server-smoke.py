#!/usr/bin/env python3
"""Smoke-test the loopx chat command, HTTP API, and preview-locked Todo write."""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "loopx-chat-server-smoke"
SECOND_GOAL_ID = "loopx-chat-server-second-goal"
TODO_TEXT = "[P0] Implement the approved Goal Studio review card."
BOUNDED_TODO_TEXT = "[P0] Keep the bounded Chat status projection responsive."

FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialized":
        continue
    if method == "initialize":
        result = {"serverInfo": {"name": "fake-codex"}}
    elif method in {"thread/start", "thread/resume"}:
        result = {"thread": {"id": "thread-chat-server"}}
    elif method in {"thread/goal/set", "thread/goal/get"}:
        print(json.dumps({
            "id": request_id,
            "error": {"code": -32601, "message": "Goal mode must not be used for Chat"},
        }), flush=True)
        continue
    elif method == "turn/start":
        turn_id = "turn-chat-server"
        force_failure = "force session failure" in json.dumps(message.get("params") or {})
        print(json.dumps({
            "method": "turn/started",
            "params": {"threadId": "thread-chat-server", "turn": {"id": turn_id}},
        }), flush=True)
        print(json.dumps({
            "id": request_id,
            "result": {"turn": {"id": turn_id, "status": "running"}},
        }), flush=True)
        if force_failure:
            print(json.dumps({
                "method": "error",
                "params": {"threadId": "thread-chat-server", "turnId": turn_id},
            }), flush=True)
            continue
        response = (
            'Visible streaming answer.\n'
            '<loopx-review-json>'
            + json.dumps({
                "schema_version": "loopx_chat_agent_response_v0",
                "message": "I found one reviewable step.",
                "proposals": [{
                    "kind": "todo",
                    "text": "[P0] Implement the approved Goal Studio review card.",
                    "priority": "P0",
                    "rationale": "It closes the visible approval loop.",
                }],
                "gate": None,
            })
            + '</loopx-review-json>'
        )
        print(json.dumps({
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "thread-chat-server",
                "turnId": turn_id,
                "delta": response,
            },
        }), flush=True)
        print(json.dumps({
            "method": "turn/completed",
            "params": {"threadId": "thread-chat-server", "turn": {"id": turn_id}},
        }), flush=True)
        continue
    elif method == "turn/interrupt":
        result = {}
    else:
        result = {}
    print(json.dumps({"id": request_id, "result": result}), flush=True)
'''


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# LoopX Chat Server Smoke\n\n"
        "## Objective\n\n"
        "Keep the local review loop explicit.\n\n"
        "## Next Action\n\n"
        "- Ask the agent for one bounded proposal.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {BOUNDED_TODO_TEXT}\n"
        "  <!-- loopx:todo todo_id=todo_bounded_chat_status status=open "
        "task_class=advancement_task action_kind=repair_loopx_chat_status_projection -->\n",
        encoding="utf-8",
    )
    second_state_file = project / ".codex" / "goals" / SECOND_GOAL_ID / "ACTIVE_GOAL_STATE.md"
    second_state_file.parent.mkdir(parents=True)
    second_state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Keep every connected Goal visible to Chat.\n",
        encoding="utf-8",
    )
    registry = project / ".loopx" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "domain": "product-engineering",
                        "status": "active",
                        "adapter": {"kind": "read_only_project_map_v0", "status": "connected"},
                    },
                    {
                        "id": SECOND_GOAL_ID,
                        "repo": str(project),
                        "state_file": f".codex/goals/{SECOND_GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "domain": "product-engineering",
                        "status": "active",
                        "adapter": {"kind": "read_only_project_map_v0", "status": "connected"},
                    },
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry, state_file


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    origin: str = "http://127.0.0.1",
) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Origin": origin},
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def wait_for_json(url: str) -> dict:
    deadline = time.time() + 8
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            status, payload = request_json(url)
            if status == 200:
                return payload
        except Exception as exc:  # pragma: no cover - bounded startup retry.
            last_error = exc
        time.sleep(0.1)
    raise RuntimeError(f"timed out waiting for LoopX Chat: {last_error}")


def main() -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from loopx.chat_server import (
        ChatHTTPServer,
        ChatRequestHandler,
        build_bounded_chat_status_projection,
    )
    from loopx.chat_endpoints import AgentEndpointRegistry
    from loopx.chat_manager import manager_executor_endpoint_default

    try:
        ChatHTTPServer(("127.0.0.1", 70_000), ChatRequestHandler)
    except Exception as exc:
        assert "sessions_lock" not in str(exc), exc
    else:  # pragma: no cover - the platform must reject an invalid TCP port.
        raise AssertionError("invalid TCP port unexpectedly created a chat server")

    with tempfile.TemporaryDirectory(prefix="loopx-chat-server-") as raw_tmp:
        root = Path(raw_tmp)
        registry, state_file = write_fixture(root)
        bounded_status = build_bounded_chat_status_projection(
            registry=json.loads(registry.read_text(encoding="utf-8")),
            selected_goal_id=GOAL_ID,
        )
        assert bounded_status["goal_count"] == 2, bounded_status
        selected_goal = next(
            goal for goal in bounded_status["goals"] if goal["goal_id"] == GOAL_ID
        )
        assert selected_goal["top_todo"]["text"] == BOUNDED_TODO_TEXT, bounded_status
        second_goal = next(
            goal for goal in bounded_status["goals"] if goal["goal_id"] == SECOND_GOAL_ID
        )
        assert second_goal["title"] == SECOND_GOAL_ID, bounded_status
        assets = REPO_ROOT / "loopx" / "web" / "chat"
        assert (assets / "index.html").is_file(), assets
        fake_codex = root / "codex"
        fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
        fake_codex.chmod(fake_codex.stat().st_mode | stat.S_IXUSR)
        AgentEndpointRegistry(root / "runtime" / "chat").upsert(
            {
                "agent_id": "fixture-acp",
                "display_name": "Fixture ACP",
                "command": [str(fake_codex), "--private-binding"],
                "location": "remote",
            }
        )
        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        command = [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(root / "runtime"),
            "chat",
            "--goal-id",
            GOAL_ID,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--assets-dir",
            str(assets),
            "--codex-bin",
            str(fake_codex),
            "--scan-path",
            str(assets / "index.html"),
            "--no-open",
        ]
        disabled_server = subprocess.Popen(
            command,
            cwd=root / "project",
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            disabled_capabilities = wait_for_json(
                f"{base_url}/api/chat/capabilities"
            )
            assert "goal_subagent_configuration" not in disabled_capabilities
            disabled_status = wait_for_json(f"{base_url}/status.json")
            assert all(
                "spawn_policy" not in goal
                for goal in disabled_status["run_history"]["goals"]
            ), disabled_status
            for route in (
                "/api/chat/goal-subagents/dry-run",
                "/api/chat/goal-subagents/apply",
            ):
                code, disabled_route = request_json(
                    f"{base_url}{route}",
                    method="POST",
                    body={"goal_id": GOAL_ID, "enabled": False},
                )
                assert code == 404, disabled_route
        finally:
            disabled_server.terminate()
            try:
                disabled_server.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup.
                disabled_server.kill()
                disabled_server.wait(timeout=5)

        port = free_port()
        base_url = f"http://127.0.0.1:{port}"
        command[command.index("--port") + 1] = str(port)
        command.append("--enable-goal-subagent-configuration")
        server = subprocess.Popen(
            command,
            cwd=root / "project",
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            capabilities = wait_for_json(f"{base_url}/api/chat/capabilities")
            assert capabilities["schema_version"] == "loopx_chat_capabilities_v1", capabilities
            assert capabilities["streaming"] is True, capabilities
            assert capabilities["resume"] is True, capabilities
            assert capabilities["interrupt"] is True, capabilities
            assert capabilities["goal_subagent_configuration"] == "preview_locked", capabilities
            assert capabilities["adapters"][0]["agent_id"] == "codex", capabilities
            endpoints = wait_for_json(f"{base_url}/api/chat/endpoints")
            fixture_endpoint = next(
                item for item in endpoints["endpoints"] if item["agent_id"] == "fixture-acp"
            )
            assert fixture_endpoint["location"] == "remote", endpoints
            assert "command" not in fixture_endpoint, endpoints
            assert "private-binding" not in json.dumps(endpoints), endpoints

            with urllib.request.urlopen(f"{base_url}/chat/", timeout=4) as response:
                html = response.read().decode("utf-8")
            assert "LoopX 个人 Agent 工作区" in html, html
            stylesheet_match = re.search(r'href="(/chat/assets/[^"]+\.css)"', html)
            assert stylesheet_match, html
            with urllib.request.urlopen(f"{base_url}{stylesheet_match.group(1)}", timeout=4) as response:
                stylesheet = response.read().decode("utf-8")
            assert ".personal-workspace" in stylesheet, "packaged workspace stylesheet is incomplete"

            code, targets_payload = request_json(f"{base_url}/api/chat/goal-channel/targets")
            assert code == 200 and targets_payload["ok"] is True, targets_payload
            assert targets_payload["targets"] == [], targets_payload

            code, setup_invalid = request_json(
                f"{base_url}/api/chat/goal-channel/setup",
                method="POST",
                body={"goal_id": GOAL_ID, "target": "ops", "bogus": True},
            )
            assert code == 400, setup_invalid
            assert setup_invalid["error_code"] == "invalid_goal_channel_setup", setup_invalid

            code, setup_blocked = request_json(
                f"{base_url}/api/chat/goal-channel/setup",
                method="POST",
                body={"goal_id": GOAL_ID, "target": "ops"},
            )
            assert code == 400, setup_blocked
            assert setup_blocked["error_code"] in {
                "extension_unavailable",
                "provider_target_missing",
            }, setup_blocked

            code, configure_missing = request_json(
                f"{base_url}/api/chat/goal-channel/configure",
                method="POST",
                body={"goal_id": GOAL_ID, "auto_notify_human_gates": False},
            )
            assert code == 400, configure_missing
            assert configure_missing["ok"] is False, configure_missing
            assert configure_missing["blocker"] == "channel_binding_missing", configure_missing

            code, configure_invalid = request_json(
                f"{base_url}/api/chat/goal-channel/configure",
                method="POST",
                body={"goal_id": GOAL_ID, "auto_notify_human_gates": "yes"},
            )
            assert code == 400, configure_invalid
            assert configure_invalid["error_code"] == "invalid_goal_channel_configure", configure_invalid

            code, blocked_notice_missing = request_json(
                f"{base_url}/api/chat/goal-channel/configure",
                method="POST",
                body={"goal_id": GOAL_ID, "auto_notify_blocked_notices": False},
            )
            assert code == 400, blocked_notice_missing
            assert blocked_notice_missing["blocker"] == "channel_binding_missing", blocked_notice_missing

            code, configure_conflict = request_json(
                f"{base_url}/api/chat/goal-channel/configure",
                method="POST",
                body={"goal_id": GOAL_ID, "auto_notify_human_gates": True,
                      "auto_notify_blocked_notices": True},
            )
            assert code == 400, configure_conflict
            assert configure_conflict["error_code"] == "invalid_goal_channel_configure", configure_conflict

            registry_before_subagent_preview = registry.read_text(encoding="utf-8")
            code, invalid_domain = request_json(
                f"{base_url}/api/chat/goal-subagents/dry-run",
                method="POST",
                body={
                    "goal_id": GOAL_ID,
                    "enabled": True,
                    "max_children": 2,
                    "allowed_domains": ["../private"],
                },
            )
            assert code == 400, invalid_domain
            assert (
                invalid_domain["error_code"] == "invalid_goal_subagent_configuration"
            ), invalid_domain

            unrestricted_subagent_request = {
                "goal_id": GOAL_ID,
                "enabled": True,
                "max_children": 2,
            }
            code, unrestricted_preview = request_json(
                f"{base_url}/api/chat/goal-subagents/dry-run",
                method="POST",
                body=unrestricted_subagent_request,
            )
            assert code == 200 and unrestricted_preview["preview_id"], (
                unrestricted_preview
            )
            assert unrestricted_preview["dry_run"] is True, unrestricted_preview
            assert unrestricted_preview["written"] is False, unrestricted_preview
            assert unrestricted_preview["after"]["orchestration"].get(
                "allowed_domains", []
            ) == [], unrestricted_preview
            assert unrestricted_preview["feature_summary"]["multi_subagent"] == (
                "enabled"
            ), unrestricted_preview
            assert (
                registry.read_text(encoding="utf-8") == registry_before_subagent_preview
            )

            code, stale_subagent_apply = request_json(
                f"{base_url}/api/chat/goal-subagents/apply",
                method="POST",
                body={**unrestricted_subagent_request, "preview_id": "stale-preview"},
            )
            assert code == 409, stale_subagent_apply
            assert (
                stale_subagent_apply["error_code"] == "stale_goal_subagent_preview"
            ), stale_subagent_apply

            code, unrestricted_applied = request_json(
                f"{base_url}/api/chat/goal-subagents/apply",
                method="POST",
                body={
                    **unrestricted_subagent_request,
                    "preview_id": unrestricted_preview["preview_id"],
                },
            )
            assert code == 200 and unrestricted_applied["written"] is True, (
                unrestricted_applied
            )
            assert (
                unrestricted_applied["global_sync"]["readback"]["verified"] is True
            ), unrestricted_applied
            unrestricted_goal = next(
                item
                for item in json.loads(registry.read_text(encoding="utf-8"))["goals"]
                if item["id"] == GOAL_ID
            )
            assert unrestricted_goal["spawn_policy"] == {
                "mode": "multi_subagent",
                "allowed": True,
                "max_children": 2,
                "allowed_domains": [],
            }, unrestricted_goal

            unrestricted_status = wait_for_json(f"{base_url}/status.json")
            unrestricted_projected_goal = next(
                item
                for item in unrestricted_status["run_history"]["goals"]
                if item["id"] == GOAL_ID
            )
            assert unrestricted_projected_goal["spawn_policy"]["spawn_allowed"] is True
            assert unrestricted_projected_goal["spawn_policy"]["max_children"] == 2
            assert (
                unrestricted_projected_goal["spawn_policy"].get("allowed_domains", []) == []
            ), unrestricted_projected_goal

            restricted_subagent_request = {
                "goal_id": GOAL_ID,
                "enabled": True,
                "max_children": 2,
                "allowed_domains": ["code", "validation"],
            }
            code, restricted_preview = request_json(
                f"{base_url}/api/chat/goal-subagents/dry-run",
                method="POST",
                body=restricted_subagent_request,
            )
            assert code == 200 and restricted_preview["preview_id"], restricted_preview
            assert restricted_preview["written"] is False, restricted_preview

            code, subagent_applied = request_json(
                f"{base_url}/api/chat/goal-subagents/apply",
                method="POST",
                body={
                    **restricted_subagent_request,
                    "preview_id": restricted_preview["preview_id"],
                },
            )
            assert code == 200, subagent_applied
            assert subagent_applied["written"] is True, subagent_applied
            assert subagent_applied["global_sync"]["readback"]["verified"] is True, (
                subagent_applied
            )
            configured_goal = next(
                item
                for item in json.loads(registry.read_text(encoding="utf-8"))["goals"]
                if item["id"] == GOAL_ID
            )
            assert configured_goal["spawn_policy"] == {
                "mode": "multi_subagent",
                "allowed": True,
                "max_children": 2,
                "allowed_domains": ["code", "validation"],
            }, configured_goal

            status_payload = wait_for_json(f"{base_url}/status.json")
            assert status_payload["ok"] is True, status_payload
            assert status_payload["goal_count"] == 1, status_payload
            assert status_payload["goal_filter"] == GOAL_ID, status_payload
            assert status_payload["global_registry"]["ok"] is True, status_payload
            assert str(root) not in json.dumps(status_payload), status_payload
            projected_goal = next(
                item
                for item in status_payload["run_history"]["goals"]
                if item["id"] == GOAL_ID
            )
            assert projected_goal["spawn_policy"]["spawn_allowed"] is True, projected_goal
            assert projected_goal["spawn_policy"]["max_children"] == 2, projected_goal
            assert projected_goal["spawn_policy"]["allowed_domains"] == [
                "code",
                "validation",
            ], projected_goal

            disable_subagents = {"goal_id": GOAL_ID, "enabled": False}
            code, disable_preview = request_json(
                f"{base_url}/api/chat/goal-subagents/dry-run",
                method="POST",
                body=disable_subagents,
            )
            assert code == 200 and disable_preview["preview_id"], disable_preview
            code, disable_applied = request_json(
                f"{base_url}/api/chat/goal-subagents/apply",
                method="POST",
                body={**disable_subagents, "preview_id": disable_preview["preview_id"]},
            )
            assert code == 200 and disable_applied["written"] is True, disable_applied
            assert disable_applied["global_sync"]["readback"]["verified"] is True, (
                disable_applied
            )
            disabled_status = wait_for_json(f"{base_url}/status.json")
            disabled_goal = next(
                item
                for item in disabled_status["run_history"]["goals"]
                if item["id"] == GOAL_ID
            )
            assert disabled_goal["spawn_policy"] == {
                "mode": "default",
                "spawn_allowed": False,
                "max_children": 0,
            }, disabled_goal

            for model_config in (
                {"model": "gpt-5.6-luna", "reasoning_effort": "max"},
                {"model": "gpt-5.6-luna"},
                None,
            ):
                model_request = {"goal_id": GOAL_ID, "enabled": False, "model_config": model_config}
                before_model_preview = registry.read_bytes()
                code, model_preview = request_json(
                    f"{base_url}/api/chat/goal-subagents/dry-run", method="POST", body=model_request,
                )
                assert code == 200, model_preview
                assert registry.read_bytes() == before_model_preview
                code, tampered_model = request_json(
                    f"{base_url}/api/chat/goal-subagents/apply", method="POST",
                    body={**model_request, "model_config": {"model": "another-model"}, "preview_id": model_preview["preview_id"]},
                )
                assert code == 409, tampered_model
                assert registry.read_bytes() == before_model_preview
                code, model_applied = request_json(
                    f"{base_url}/api/chat/goal-subagents/apply", method="POST",
                    body={**model_request, "preview_id": model_preview["preview_id"]},
                )
                assert code == 200, model_applied
                assert model_applied["after"]["orchestration"].get("model_config") == model_config
                model_status = wait_for_json(f"{base_url}/status.json")
                model_goal = next(item for item in model_status["run_history"]["goals"] if item["id"] == GOAL_ID)
                assert model_goal["spawn_policy"].get("model_config") == model_config
                assert model_goal["spawn_policy"]["spawn_allowed"] is False

            code, created = request_json(
                f"{base_url}/api/chat/sessions",
                method="POST",
                body={"goal_id": GOAL_ID},
            )
            assert code == 201 and created["session_id"], created
            session_id = created["session_id"]

            code, manager_created = request_json(
                f"{base_url}/api/chat/sessions",
                method="POST",
                body={"context_kind": "manager"},
            )
            # The steward channel owns its executor default. This client sends
            # no pick, so the session must land on whatever the channel resolves
            # on this machine -- never on an executor the caller's client
            # happens to ship with.
            expected_manager_endpoint = manager_executor_endpoint_default()
            assert code == 201, manager_created
            assert manager_created["agent_id"] == expected_manager_endpoint, manager_created
            assert (
                manager_created["session"]["executor_endpoint_id"]
                == expected_manager_endpoint
            ), manager_created
            assert manager_created["session"]["channel_id"] == "manager", manager_created
            assert manager_created["session_id"] != session_id, manager_created
            code, manager_resumed = request_json(
                f"{base_url}/api/chat/sessions",
                method="POST",
                body={"goal_id": SECOND_GOAL_ID, "context_kind": "manager"},
            )
            assert code == 200 and manager_resumed["resumed"] is True, manager_resumed
            assert manager_resumed["session_id"] == manager_created["session_id"], manager_resumed
            assert manager_resumed["goal_id"] == "loopx-manager", manager_resumed
            # An explicit pick still travels, and it resumes the same channel
            # conversation instead of silently opening a second one.
            code, manager_explicit = request_json(
                f"{base_url}/api/chat/sessions",
                method="POST",
                body={"context_kind": "manager", "agent_id": expected_manager_endpoint},
            )
            assert code == 200 and manager_explicit["resumed"] is True, manager_explicit
            assert manager_explicit["session_id"] == manager_created["session_id"], manager_explicit
            listed_manager = wait_for_json(
                f"{base_url}/api/chat/sessions?channel_id=manager"
            )
            assert [item["session_id"] for item in listed_manager["sessions"]] == [
                manager_created["session_id"]
            ], listed_manager

            code, projection_exchange = request_json(
                f"{base_url}/api/chat/projection-messages",
                method="POST",
                body={
                    "answer": "当前没有阻塞。",
                    "context_kind": "manager",
                    "question": "现在该做什么？",
                },
            )
            assert code == 201 and projection_exchange["session_id"], projection_exchange
            projection_history = wait_for_json(
                f"{base_url}/api/chat/sessions?agent_id=status-only&channel_id=manager"
            )
            assert len(projection_history["sessions"]) == 1, projection_history
            projection_snapshot = wait_for_json(
                f"{base_url}/api/chat/sessions/{projection_exchange['session_id']}"
            )
            assert [message["text"] for message in projection_snapshot["messages"]] == [
                "现在该做什么？",
                "当前没有阻塞。",
            ], projection_snapshot
            assert [message["message_id"] for message in projection_snapshot["messages"]] == [
                projection_exchange["user_message_id"],
                projection_exchange["answer_message_id"],
            ], projection_snapshot

            code, turn = request_json(
                f"{base_url}/api/chat/sessions/{session_id}/turns",
                method="POST",
                body={"message": "user message: propose the next step"},
            )
            assert code == 200, turn
            assert turn["response"]["proposals"][0]["text"] == TODO_TEXT, turn

            code, accepted = request_json(
                f"{base_url}/api/chat/sessions/{session_id}/turns",
                method="POST",
                body={"message": "stream this answer", "client_turn_id": "client-stream-one"},
            )
            assert code == 202 and accepted["turn_id"], accepted
            turn_id = accepted["turn_id"]
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"{base_url}{accepted['events_url']}",
                    headers={"Accept": "text/event-stream", "Origin": "http://127.0.0.1:5173"},
                ),
                timeout=4,
            ) as response:
                event_stream = response.read().decode("utf-8")
            assert "event: answer.delta" in event_stream, event_stream
            assert "Visible streaming answer." in event_stream, event_stream
            assert "I found one reviewable step." in event_stream, event_stream
            assert "<loopx-review-json>" not in event_stream, event_stream
            assert "event: proposal.ready" in event_stream, event_stream
            assert "event: turn.completed" in event_stream, event_stream
            assert event_stream.index("Visible streaming answer.") < event_stream.index(
                "event: turn.completed"
            ), event_stream
            first_event_id = next(
                line.split(":", 1)[1].strip()
                for line in event_stream.splitlines()
                if line.startswith("id:")
            )
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"{base_url}{accepted['events_url']}",
                    headers={
                        "Accept": "text/event-stream",
                        "Last-Event-ID": first_event_id,
                        "Origin": "http://127.0.0.1:5173",
                    },
                ),
                timeout=4,
            ) as response:
                replay_stream = response.read().decode("utf-8")
            assert f"id: {first_event_id}\n" not in replay_stream, replay_stream
            assert "event: turn.completed" in replay_stream, replay_stream
            code, duplicate = request_json(
                f"{base_url}/api/chat/sessions/{session_id}/turns",
                method="POST",
                body={"message": "stream this answer", "client_turn_id": "client-stream-one"},
            )
            assert code == 202 and duplicate["turn_id"] == turn_id, duplicate
            assert duplicate["created"] is False, duplicate

            listed = wait_for_json(
                f"{base_url}/api/chat/sessions?goal_id={GOAL_ID}&agent_id=codex"
            )
            assert listed["sessions"][0]["session_id"] == session_id, listed
            assert "upstream_thread_id" not in json.dumps(listed), listed
            snapshot = wait_for_json(f"{base_url}/api/chat/sessions/{session_id}")
            assert snapshot["session"]["resumable"] is True, snapshot
            assert [item["role"] for item in snapshot["messages"]][-2:] == ["user", "agent"], snapshot

            code, failure_session = request_json(
                f"{base_url}/api/chat/sessions",
                method="POST",
                body={"goal_id": GOAL_ID, "mode": "new"},
            )
            assert code == 201 and failure_session["session_id"], failure_session
            failed_session_id = failure_session["session_id"]
            code, failed_turn = request_json(
                f"{base_url}/api/chat/sessions/{failed_session_id}/turns",
                method="POST",
                body={"message": "force session failure"},
            )
            assert code == 424, failed_turn
            assert "session_invalidated" not in failed_turn, failed_turn
            assert "turn_replay_safe" not in failed_turn, failed_turn
            code, recovered_session = request_json(
                f"{base_url}/api/chat/sessions/{failed_session_id}/turns",
                method="POST",
                body={"message": "retry in the same durable session"},
            )
            assert code == 200, recovered_session

            original = state_file.read_text(encoding="utf-8")
            code, preview = request_json(
                f"{base_url}/api/chat/todo/dry-run",
                method="POST",
                body={"goal_id": GOAL_ID, "text": TODO_TEXT},
            )
            assert code == 200 and preview["preview_id"], preview
            assert state_file.read_text(encoding="utf-8") == original

            code, blocked_origin = request_json(
                f"{base_url}/api/chat/todo/apply",
                method="POST",
                body={
                    "goal_id": GOAL_ID,
                    "text": TODO_TEXT,
                    "preview_id": preview["preview_id"],
                },
                origin="https://example.com",
            )
            assert code == 403 and blocked_origin["ok"] is False, blocked_origin
            assert state_file.read_text(encoding="utf-8") == original

            code, stale = request_json(
                f"{base_url}/api/chat/todo/apply",
                method="POST",
                body={"goal_id": GOAL_ID, "text": TODO_TEXT, "preview_id": "stale"},
            )
            assert code == 409 and stale["ok"] is False, stale
            no_write_receipt = stale["todo_receipt"]
            assert no_write_receipt["schema_version"] == "loopx_chat_todo_no_write_receipt_v0", stale
            assert no_write_receipt["status"] == "not_applied", stale
            assert no_write_receipt["outcome"] == "preview_stale", stale
            assert no_write_receipt["write_attempted"] is False, stale
            assert no_write_receipt["goal_id"] == GOAL_ID, stale
            assert no_write_receipt["receipt_id"], stale
            assert str(root) not in json.dumps(no_write_receipt), stale
            assert state_file.read_text(encoding="utf-8") == original

            code, applied = request_json(
                f"{base_url}/api/chat/todo/apply",
                method="POST",
                body={
                    "goal_id": GOAL_ID,
                    "text": TODO_TEXT,
                    "preview_id": preview["preview_id"],
                },
            )
            assert code == 200 and applied["applied"] is True, applied
            receipt = applied["receipt"]
            assert receipt["schema_version"] == "loopx_chat_todo_receipt_v0", receipt
            assert receipt["preview_id"] == preview["preview_id"], receipt
            assert receipt["todo_id"] == applied["todo"]["todo_id"], receipt
            assert receipt["outcome"] == "todo_added", receipt
            assert str(root) not in json.dumps(receipt), receipt
            assert state_file.read_text(encoding="utf-8").count(TODO_TEXT) == 1
            refreshed_status = wait_for_json(f"{base_url}/status.json")
            refreshed_goal = next(
                item
                for item in refreshed_status["attention_queue"]["items"]
                if item["goal_id"] == GOAL_ID
            )
            projected_todos = [
                todo
                for todo in refreshed_goal["project_asset"]["agent_todos"]["items"]
                if todo["todo_id"] == receipt["todo_id"]
            ]
            assert len(projected_todos) == 1, refreshed_status
            assert projected_todos[0]["text"] == TODO_TEXT, projected_todos

            code, retry_preview = request_json(
                f"{base_url}/api/chat/todo/dry-run",
                method="POST",
                body={"goal_id": GOAL_ID, "text": TODO_TEXT},
            )
            assert code == 200 and retry_preview["already_exists"] is True, retry_preview
            code, retry_applied = request_json(
                f"{base_url}/api/chat/todo/apply",
                method="POST",
                body={
                    "goal_id": GOAL_ID,
                    "text": TODO_TEXT,
                    "preview_id": retry_preview["preview_id"],
                },
            )
            assert code == 200 and retry_applied["applied"] is True, retry_applied
            retry_receipt = retry_applied["receipt"]
            assert retry_receipt["outcome"] == "todo_already_exists", retry_receipt
            assert retry_receipt["already_exists"] is True, retry_receipt
            assert retry_receipt["todo_id"] == receipt["todo_id"], retry_receipt
            assert state_file.read_text(encoding="utf-8").count(TODO_TEXT) == 1

            code, closed = request_json(
                f"{base_url}/api/chat/sessions/{session_id}",
                method="DELETE",
            )
            assert code == 200 and closed["closed"] is True, closed
            code, closed_session_turn = request_json(
                f"{base_url}/api/chat/sessions/{session_id}/turns",
                method="POST",
                body={"message": "a closed browser session must restart before retry"},
            )
            assert code == 404, closed_session_turn
            assert closed_session_turn["session_invalidated"] is True, closed_session_turn
            assert closed_session_turn["turn_replay_safe"] is True, closed_session_turn
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive cleanup.
                server.kill()
                server.wait(timeout=5)

    print("loopx-chat-server-smoke: ok")


if __name__ == "__main__":
    main()
