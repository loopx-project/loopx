#!/usr/bin/env python3
"""Smoke-test the owner-local typed Chat action lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.chat_action_store import ActionConflictError, ChatActionStore  # noqa: E402
from loopx.chat_actions import ChatActionService  # noqa: E402
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler  # noqa: E402
from loopx.chat_store import ChatSessionStore  # noqa: E402
from loopx.history import collect_history  # noqa: E402


def preview_request(
    *,
    idempotency_key: str,
    summary: str = "Create a bounded Goal",
    expected_state_fingerprint: str = "goal-state-r1",
) -> dict[str, object]:
    return {
        "action_kind": "goal.create",
        "summary": summary,
        "normalized_parameters": {
            "goal_id": "agent-control-plane",
            "agent_id": "codex",
            "heartbeat": {"cadence": "daily", "hour": 9},
        },
        "context": {"kind": "manager", "goal_id": None},
        "expected_state_fingerprint": expected_state_fingerprint,
        "permission_classification": "durable_write",
        "validation_evidence": ["Agent endpoint is healthy", "Goal id is available"],
        "available_transitions": ["apply", "cancel"],
        "idempotency_key": idempotency_key,
    }


def receipt(receipt_id: str = "receipt-goal-create-1") -> dict[str, object]:
    return {
        "receipt_id": receipt_id,
        "outcome": "goal_created",
        "projection_verified": True,
        "resource_ids": {"goal_id": "agent-control-plane"},
    }


def assert_owner_only_tree(root: Path) -> None:
    assert stat.S_IMODE(root.stat().st_mode) == 0o700, oct(root.stat().st_mode)
    for path in root.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            assert mode == 0o700, (path, oct(mode))
        else:
            assert mode == 0o600, (path, oct(mode))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    url: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
    origin: str = "http://127.0.0.1:5173",
) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "Origin": origin},
    )
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class FakeRuntimeController:
    def __init__(self, chat_store: ChatSessionStore) -> None:
        self.chat_store = chat_store
        self.opened_sessions: list[dict[str, object]] = []
        self.submissions: list[dict[str, object]] = []

    def open_session(self, **kwargs: object) -> tuple[dict[str, object], bool]:
        self.opened_sessions.append(dict(kwargs))
        existing = self.chat_store.latest_session(
            goal_id=str(kwargs["goal_id"]),
            agent_id=str(kwargs["agent_id"]),
            channel_id=str(kwargs["channel_id"]),
        )
        if existing is not None:
            return existing, True
        session = self.chat_store.create_session(
            goal_id=str(kwargs["goal_id"]),
            agent_id=str(kwargs["agent_id"]),
            adapter_kind="fake",
            upstream_thread_id="thread-goal-create",
            channel_id=str(kwargs["channel_id"]),
        )
        return session, False

    def submit_turn(self, **kwargs: object) -> tuple[dict[str, object], bool]:
        self.submissions.append(dict(kwargs))
        return ({"turn_id": "turn-correction-1", "status": "queued"}, True)

    def capabilities(self) -> list[dict[str, object]]:
        return [
            {
                "agent_id": "codex",
                "available": True,
                "tool_calls": True,
                "trust_scope": "read_only",
                "adapter_kind": "fake",
            },
            {
                "agent_id": "claude-review",
                "available": True,
                "tool_calls": True,
                "trust_scope": "read_only",
                "adapter_kind": "fake",
            },
            {
                "agent_id": "offline-agent",
                "available": False,
                "tool_calls": True,
                "trust_scope": "read_only",
                "adapter_kind": "fake",
            },
        ]

    def close(self) -> None:
        return


class FailingRuntimeController(FakeRuntimeController):
    def submit_turn(self, **kwargs: object) -> tuple[dict[str, object], bool]:
        raise RuntimeError("temporary runtime failure")


def write_registry_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state = project / ".codex" / "goals" / "goal-one" / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00Z\n---\n\n"
        "# Active Goal State\n\n## Objective\n\nKeep action writes explicit.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n## Agent Todo\n",
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
                        "id": "goal-one",
                        "repo": str(project),
                        "state_file": ".codex/goals/goal-one/ACTIVE_GOAL_STATE.md",
                        "domain": "product-engineering",
                        "status": "active",
                        "adapter": {"kind": "read_only_project_map_v0", "status": "connected"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["codex"],
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return registry, state


def assert_http_action_api(root: Path) -> None:
    registry_path, state_path = write_registry_fixture(root)
    runtime_root = root / "runtime"
    action_store = ChatActionStore(runtime_root / "chat" / "actions")
    chat_store = ChatSessionStore(runtime_root)
    session = chat_store.create_session(
        goal_id="goal-one",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="thread-one",
    )
    runtime_controller = FakeRuntimeController(chat_store)
    service = ChatActionService(
        store=action_store,
        registry_path=registry_path,
        chat_store=chat_store,
        runtime_controller=runtime_controller,
    )
    registry_fixture = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_fixture["goals"][0]["coordination"]["registered_agents"] = [
        "codex-loopx-development"
    ]
    registry_path.write_text(json.dumps(registry_fixture) + "\n", encoding="utf-8")
    aliased = service._normalize(
        "todo.create",
        {
            "goal_id": "goal-one",
            "text": "Resolve the Endpoint to the Goal peer identity",
            "endpoint_id": "codex",
        },
    )
    assert aliased["endpoint_id"] == "codex", aliased
    assert aliased["agent_id"] == "codex-loopx-development", aliased
    registry_fixture["goals"][0]["coordination"]["registered_agents"] = ["codex"]
    registry_path.write_text(json.dumps(registry_fixture) + "\n", encoding="utf-8")
    server = ChatHTTPServer(("127.0.0.1", free_port()), ChatRequestHandler)
    server.registry_path = registry_path
    server.action_store = action_store
    server.action_service = service
    server.chat_store = chat_store
    server.runtime_controller = runtime_controller
    server.verbose = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        original_state = state_path.read_text(encoding="utf-8")
        preview_bodies = {
            "todo.create": {
                "goal_id": "goal-one",
                "text": "[P1] Verify the typed action API",
                "agent_id": "codex",
                "start_execution": True,
            },
            "run.correct": {
                "goal_id": "goal-one",
                "session_id": session["session_id"],
                "message": "Keep the correction bounded and verify the result.",
            },
            "goal.create": {
                "goal_id": "new-goal",
                "title": "New Goal",
                "objective": "Deliver a bounded verified result.",
                "agent_id": "codex",
                "workspace_ref": "current",
                "heartbeat": {"enabled": False},
                "initial_todos": ["Verify the new Goal projection"],
            },
            "agent.bind": {"goal_id": "goal-one", "agent_id": "claude-review"},
            "heartbeat.bind": {
                "goal_id": "goal-one",
                "agent_id": "codex",
                "cadence": "daily",
                "timezone": "Asia/Shanghai",
                "stop_condition": "goal_complete",
            },
            "monitor.create": {
                "goal_id": "goal-one",
                "agent_id": "codex",
                "target": "Pull request 3559",
                "target_key": "pr-3559",
                "cadence": "every_30_minutes",
                "timezone": "Asia/Shanghai",
                "stop_condition": "pr_merged:example/loopx#3559",
            },
        }
        previews: dict[str, dict[str, object]] = {}
        for index, (action_kind, parameters) in enumerate(preview_bodies.items()):
            code, payload = request_json(
                f"{base_url}/api/actions/preview",
                method="POST",
                body={
                    "action_kind": action_kind,
                    "summary": f"Preview {action_kind}",
                    "normalized_parameters": parameters,
                    "context": {"kind": "goal", "goal_id": parameters.get("goal_id")},
                    "idempotency_key": f"http-preview-{index}",
                },
            )
            assert code == 201, (action_kind, payload)
            proposal = payload["proposal"]
            assert proposal["action_kind"] == action_kind, proposal
            assert proposal["status"] == "preview_ready", proposal
            assert str(root) not in json.dumps(payload), payload
            previews[action_kind] = proposal
        assert state_path.read_text(encoding="utf-8") == original_state

        code, restored = request_json(
            f"{base_url}/api/actions?goal_id=goal-one&context_kind=goal&status=preview_ready"
        )
        assert code == 200, restored
        assert restored["schema_version"] == "loopx_chat_action_list_v1", restored
        restored_kinds = {proposal["action_kind"] for proposal in restored["proposals"]}
        assert restored_kinds == {
            "todo.create",
            "run.correct",
            "agent.bind",
            "heartbeat.bind",
            "monitor.create",
        }, restored
        assert all(proposal["status"] == "preview_ready" for proposal in restored["proposals"])
        assert str(root) not in json.dumps(restored), restored
        reopened_store = ChatActionStore(runtime_root / "chat" / "actions")
        assert {
            proposal["proposal_id"]
            for proposal in reopened_store.list(
                goal_id="goal-one",
                context_kind="goal",
                status="preview_ready",
            )
        } == {proposal["proposal_id"] for proposal in restored["proposals"]}

        todo_proposal = previews["todo.create"]
        code, loaded = request_json(
            f"{base_url}/api/actions/{todo_proposal['proposal_id']}"
        )
        assert code == 200 and loaded["proposal"] == todo_proposal, loaded

        code, applied = request_json(
            f"{base_url}/api/actions/{todo_proposal['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 202 and applied["proposal"]["status"] == "applied", applied
        assert applied["proposal"]["receipt"]["projection_verified"] is True, applied
        todo_resources = applied["proposal"]["receipt"]["resource_ids"]
        assert todo_resources["session_id"], todo_resources
        assert todo_resources["turn_id"], todo_resources
        assert applied["turn"]["session_id"] == todo_resources["session_id"], applied
        assert runtime_controller.opened_sessions[-1]["channel_id"] == f"task.{todo_resources['todo_id']}"
        assert state_path.read_text(encoding="utf-8").count("Verify the typed action API") == 1
        assert "claimed_by=codex" in state_path.read_text(encoding="utf-8")
        code, repeated = request_json(
            f"{base_url}/api/actions/{todo_proposal['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 202 and repeated["proposal"] == applied["proposal"], repeated
        assert repeated["turn"]["session_id"] == todo_resources["session_id"], repeated
        assert state_path.read_text(encoding="utf-8").count("Verify the typed action API") == 1

        correction = previews["run.correct"]
        code, correction_applied = request_json(
            f"{base_url}/api/actions/{correction['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 202, correction_applied
        assert correction_applied["proposal"]["status"] == "applied", correction_applied
        assert correction_applied["turn"]["turn_id"] == "turn-correction-1", correction_applied
        assert runtime_controller.submissions[1]["session_id"] == session["session_id"]

        goal_proposal = previews["goal.create"]
        code, goal_applied = request_json(
            f"{base_url}/api/actions/{goal_proposal['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 202, goal_applied
        assert goal_applied["proposal"]["receipt"]["outcome"] == "goal_created", goal_applied
        assert goal_applied["proposal"]["receipt"]["projection_verified"] is True, goal_applied
        goal_resources = goal_applied["proposal"]["receipt"]["resource_ids"]
        assert goal_resources["session_id"], goal_resources
        assert goal_resources["turn_id"], goal_resources
        assert runtime_controller.opened_sessions[-1]["goal_id"] == "new-goal"
        assert runtime_controller.submissions[-1]["session_id"] == goal_resources["session_id"]
        assert "Verify the new Goal projection" in runtime_controller.submissions[-1]["message"]
        registry_after_goal = json.loads(registry_path.read_text(encoding="utf-8"))
        new_goal = next(item for item in registry_after_goal["goals"] if item["id"] == "new-goal")
        assert new_goal["display_name"] == "New Goal", new_goal
        history_after_goal = collect_history(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id="new-goal",
            limit=5,
        )
        assert history_after_goal["goals"][0]["display_name"] == "New Goal", history_after_goal
        assert new_goal["coordination"]["registered_agents"] == ["codex"], new_goal
        assert new_goal["state_file"] == ".loopx/goals/new-goal/ACTIVE_GOAL_STATE.md", new_goal
        new_state = registry_path.parent.parent / new_goal["state_file"]
        assert new_state.exists(), new_state
        new_state_text = new_state.read_text(encoding="utf-8")
        assert new_state_text.count("Verify the new Goal projection") == 1
        assert "Run `loopx check` against the project registry" not in new_state_text
        code, goal_repeated = request_json(
            f"{base_url}/api/actions/{goal_proposal['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 202 and goal_repeated["proposal"] == goal_applied["proposal"], goal_repeated
        assert goal_repeated["turn"]["created"] is False, goal_repeated
        assert new_state.read_text(encoding="utf-8").count("Verify the new Goal projection") == 1
        assert len(runtime_controller.opened_sessions) == 2

        code, heartbeat_goal_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "goal.create",
                "summary": "Create a Goal with a heartbeat child Gate",
                "normalized_parameters": {
                    "goal_id": "scheduled-goal",
                    "title": "Scheduled Goal",
                    "objective": "Create the Goal before requesting host scheduling.",
                    "agent_id": "codex",
                    "workspace_ref": "current",
                    "heartbeat": {
                        "enabled": True,
                        "cadence": "daily",
                        "timezone": "Asia/Shanghai",
                    },
                    "stop_condition": "goal_complete",
                    "initial_todos": ["Verify scheduled Goal readiness"],
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-goal-with-heartbeat",
            },
        )
        assert code == 201, heartbeat_goal_preview
        code, heartbeat_goal_applied = request_json(
            f"{base_url}/api/actions/{heartbeat_goal_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code in {200, 202}, heartbeat_goal_applied
        assert heartbeat_goal_applied["proposal"]["status"] == "applied", heartbeat_goal_applied
        assert heartbeat_goal_applied["proposal"]["receipt"]["child_gate"]["kind"] == "host_activation_required"
        steps = heartbeat_goal_applied["proposal"]["receipt"]["step_receipts"]
        assert "goal_bootstrapped" in steps and "heartbeat_gate_ready" in steps, steps
        assert any(goal["id"] == "scheduled-goal" for goal in json.loads(registry_path.read_text())["goals"])

        code, unavailable_agent = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "agent.bind",
                "summary": "Reject an unavailable Agent",
                "normalized_parameters": {"goal_id": "goal-one", "agent_id": "offline-agent"},
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-offline-agent",
            },
        )
        assert code == 400, unavailable_agent
        assert "not healthy" in str(unavailable_agent.get("error") or ""), unavailable_agent

        failing_service = ChatActionService(
            store=action_store,
            registry_path=registry_path,
            chat_store=chat_store,
            runtime_controller=FailingRuntimeController(chat_store),
        )
        server.action_service = failing_service
        code, failure_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "run.correct",
                "summary": "Persist a temporary correction failure",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "session_id": session["session_id"],
                    "message": "Retry this correction safely.",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-failed-correction",
            },
        )
        assert code == 201, failure_preview
        code, failed_apply = request_json(
            f"{base_url}/api/actions/{failure_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 424, failed_apply
        failed_stored = action_store.load(str(failure_preview["proposal"]["proposal_id"]))
        assert failed_stored["status"] == "failed", failed_stored
        assert failed_stored["failure"]["retry_safe"] is True, failed_stored
        server.action_service = service

        code, fresh_agent_payload = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "agent.bind",
                "summary": "Bind a second Agent",
                "normalized_parameters": preview_bodies["agent.bind"],
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-agent-fresh",
            },
        )
        assert code == 201, fresh_agent_payload
        agent_proposal = fresh_agent_payload["proposal"]
        code, agent_applied = request_json(
            f"{base_url}/api/actions/{agent_proposal['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, agent_applied
        assert agent_applied["proposal"]["receipt"]["outcome"] == "agent_bound", agent_applied
        configured = json.loads(registry_path.read_text(encoding="utf-8"))
        goal_one = next(item for item in configured["goals"] if item["id"] == "goal-one")
        assert goal_one["coordination"]["registered_agents"] == ["claude-review", "codex"], goal_one

        heartbeat_proposal = previews["heartbeat.bind"]
        code, heartbeat_gate = request_json(
            f"{base_url}/api/actions/{heartbeat_proposal['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 409, heartbeat_gate
        assert heartbeat_gate["gate"]["kind"] == "host_activation_required", heartbeat_gate
        assert heartbeat_gate["gate"]["activation_packet"]["host_surface"] == "codex_app_heartbeat_automation"
        assert heartbeat_gate["write_attempted"] is False, heartbeat_gate
        heartbeat_stored = action_store.load(str(heartbeat_proposal["proposal_id"]))
        assert heartbeat_stored["status"] == "gated", heartbeat_stored
        assert heartbeat_stored["gate"]["kind"] == "host_activation_required", heartbeat_stored

        for index, operation in enumerate(("edit", "pause", "resume", "stop")):
            code, heartbeat_lifecycle = request_json(
                f"{base_url}/api/actions/preview",
                method="POST",
                body={
                    "action_kind": "heartbeat.bind",
                    "summary": f"{operation.title()} heartbeat",
                    "normalized_parameters": {
                        **preview_bodies["heartbeat.bind"],
                        "operation": operation,
                    },
                    "context": {"kind": "goal", "goal_id": "goal-one"},
                    "idempotency_key": f"http-heartbeat-{operation}-{index}",
                },
            )
            assert code == 201, heartbeat_lifecycle
            code, lifecycle_gate = request_json(
                f"{base_url}/api/actions/{heartbeat_lifecycle['proposal']['proposal_id']}/apply",
                method="POST",
                body={},
            )
            assert code == 409, lifecycle_gate
            assert lifecycle_gate["gate"]["operation"] == operation, lifecycle_gate

        monitor_proposal = previews["monitor.create"]
        # Agent binding legitimately changed the registry after this preview.
        code, monitor_stale = request_json(
            f"{base_url}/api/actions/{monitor_proposal['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 409 and monitor_stale["proposal"]["status"] == "stale", monitor_stale
        code, regenerated = request_json(
            f"{base_url}/api/actions/{monitor_proposal['proposal_id']}/regenerate",
            method="POST",
            body={},
        )
        assert code == 201, regenerated
        assert regenerated["proposal"]["status"] == "preview_ready", regenerated
        assert regenerated["proposal"]["proposal_id"] != monitor_proposal["proposal_id"], regenerated
        assert regenerated["proposal"]["regenerated_from"] == monitor_proposal["proposal_id"], regenerated
        code, monitor_preview_payload = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.create",
                "summary": "Monitor the pull request until merge",
                "normalized_parameters": preview_bodies["monitor.create"],
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-fresh",
            },
        )
        assert code == 201, monitor_preview_payload
        fresh_monitor = monitor_preview_payload["proposal"]
        code, monitor_applied = request_json(
            f"{base_url}/api/actions/{fresh_monitor['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, monitor_applied
        monitor_receipt = monitor_applied["proposal"]["receipt"]
        assert monitor_receipt["outcome"] in {"monitor_created", "monitor_already_exists"}, monitor_receipt
        assert monitor_receipt["projection_verified"] is True, monitor_receipt
        state_after_monitor = state_path.read_text(encoding="utf-8")
        assert "task_class=continuous_monitor" in state_after_monitor, state_after_monitor
        assert "cadence=30m" in state_after_monitor, state_after_monitor
        assert "next_due_at=" in state_after_monitor, state_after_monitor
        assert "pr_merged" in state_after_monitor, state_after_monitor

        monitor_todo_id = monitor_receipt["resource_ids"]["todo_id"]
        lifecycle_actions = [
            ("edit", {"cadence": "every_2_hours", "target": "Pull request 3559 lifecycle"}),
            ("pause", {}),
            ("resume", {}),
        ]
        for index, (operation, extras) in enumerate(lifecycle_actions):
            code, lifecycle_preview = request_json(
                f"{base_url}/api/actions/preview",
                method="POST",
                body={
                    "action_kind": "monitor.update",
                    "summary": f"{operation.title()} monitor",
                    "normalized_parameters": {
                        "goal_id": "goal-one",
                        "todo_id": monitor_todo_id,
                        "agent_id": "codex",
                        "operation": operation,
                        **extras,
                    },
                    "context": {"kind": "goal", "goal_id": "goal-one"},
                    "idempotency_key": f"http-monitor-{operation}-{index}",
                },
            )
            assert code == 201, lifecycle_preview
            code, lifecycle_applied = request_json(
                f"{base_url}/api/actions/{lifecycle_preview['proposal']['proposal_id']}/apply",
                method="POST",
                body={},
            )
            assert code == 200, lifecycle_applied
            expected_outcome = {
                "edit": "monitor_updated",
                "pause": "monitor_paused",
                "resume": "monitor_resumed",
            }[operation]
            assert lifecycle_applied["proposal"]["receipt"]["outcome"] == expected_outcome, lifecycle_applied
        lifecycle_state = state_path.read_text(encoding="utf-8")
        assert "Pull request 3559 lifecycle" in lifecycle_state, lifecycle_state
        assert "cadence=2h" in lifecycle_state, lifecycle_state
        assert "status=open" in lifecycle_state, lifecycle_state

        # Exclusive typed stop-condition transitions:
        # 1. prose -> ISO timestamp
        code, edit_ts_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.update",
                "summary": "Switch stop condition to timestamp",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": monitor_todo_id,
                    "agent_id": "codex",
                    "operation": "edit",
                    "stop_condition": "2026-08-25T12:00:00Z",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-edit-ts",
            },
        )
        assert code == 201, edit_ts_preview
        code, edit_ts_applied = request_json(
            f"{base_url}/api/actions/{edit_ts_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, edit_ts_applied
        state_after_ts = state_path.read_text(encoding="utf-8")
        assert "expires_at=2026-08-25T12:00:00Z" in state_after_ts, state_after_ts
        assert "resume_when=" not in state_after_ts, state_after_ts
        assert "watch_only=" not in state_after_ts, state_after_ts

        # 2. ISO timestamp -> watch_only
        code, edit_watch_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.update",
                "summary": "Switch stop condition to watch only",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": monitor_todo_id,
                    "agent_id": "codex",
                    "operation": "edit",
                    "stop_condition": "watch_only",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-edit-watch",
            },
        )
        assert code == 201, edit_watch_preview
        code, edit_watch_applied = request_json(
            f"{base_url}/api/actions/{edit_watch_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, edit_watch_applied
        state_after_watch = state_path.read_text(encoding="utf-8")
        assert "watch_only=true" in state_after_watch, state_after_watch
        assert "expires_at=" not in state_after_watch, state_after_watch
        assert "resume_when=" not in state_after_watch, state_after_watch

        # 3. watch_only -> prose (pr_merged:example/loopx#3560)
        code, edit_prose_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.update",
                "summary": "Switch stop condition to prose pr_merged",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": monitor_todo_id,
                    "agent_id": "codex",
                    "operation": "edit",
                    "stop_condition": "pr_merged:example/loopx#3560",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-edit-prose",
            },
        )
        assert code == 201, edit_prose_preview
        code, edit_prose_applied = request_json(
            f"{base_url}/api/actions/{edit_prose_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, edit_prose_applied
        state_after_prose = state_path.read_text(encoding="utf-8")
        assert "resume_when=pr_merged:example%2Floopx%233560" in state_after_prose, state_after_prose
        assert "expires_at=" not in state_after_prose, state_after_prose
        assert "watch_only=" not in state_after_prose, state_after_prose

        # 4. Cadence-only edit preserves existing resume_when without adding competing fields
        code, edit_cadence_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.update",
                "summary": "Edit cadence only",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": monitor_todo_id,
                    "agent_id": "codex",
                    "operation": "edit",
                    "cadence": "45m",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-edit-cadence",
            },
        )
        assert code == 201, edit_cadence_preview
        code, edit_cadence_applied = request_json(
            f"{base_url}/api/actions/{edit_cadence_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, edit_cadence_applied
        state_after_cadence = state_path.read_text(encoding="utf-8")
        assert "resume_when=pr_merged:example%2Floopx%233560" in state_after_cadence, state_after_cadence
        assert "cadence=45m" in state_after_cadence, state_after_cadence
        assert "expires_at=" not in state_after_cadence, state_after_cadence
        assert "watch_only=" not in state_after_cadence, state_after_cadence

        code, run_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.update",
                "summary": "Run the monitor now",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": monitor_todo_id,
                    "agent_id": "codex",
                    "operation": "run_now",
                    "session_id": session["session_id"],
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-run-now",
            },
        )
        assert code == 201, run_preview
        code, run_applied = request_json(
            f"{base_url}/api/actions/{run_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 202, run_applied
        assert run_applied["proposal"]["receipt"]["outcome"] == "monitor_turn_created", run_applied
        assert runtime_controller.submissions[-1]["session_id"] == session["session_id"]

        code, fresh_run_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.update",
                "summary": "Run the monitor in its own task Session",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": monitor_todo_id,
                    "agent_id": "codex",
                    "endpoint_id": "codex",
                    "operation": "run_now",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-run-now-own-session",
            },
        )
        assert code == 201, fresh_run_preview
        code, fresh_run_applied = request_json(
            f"{base_url}/api/actions/{fresh_run_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 202, fresh_run_applied
        fresh_resources = fresh_run_applied["proposal"]["receipt"]["resource_ids"]
        assert fresh_resources["session_id"], fresh_run_applied
        assert runtime_controller.opened_sessions[-1]["channel_id"] == f"task.{monitor_todo_id}"
        assert runtime_controller.submissions[-1]["session_id"] == fresh_resources["session_id"]

        code, stop_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "monitor.update",
                "summary": "Stop the monitor",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": monitor_todo_id,
                    "agent_id": "codex",
                    "operation": "stop",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-monitor-stop",
            },
        )
        assert code == 201, stop_preview
        code, stop_applied = request_json(
            f"{base_url}/api/actions/{stop_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, stop_applied
        assert stop_applied["proposal"]["receipt"]["outcome"] == "monitor_stopped", stop_applied
        code, stop_repeated = request_json(
            f"{base_url}/api/actions/{stop_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200 and stop_repeated["proposal"] == stop_applied["proposal"], stop_repeated

        current_todo_id = applied["proposal"]["receipt"]["resource_ids"]["todo_id"]
        code, todo_update_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "todo.update",
                "summary": "Clarify the Todo",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": current_todo_id,
                    "text": "Verify typed action lifecycle and receipts",
                    "agent_id": "codex",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-todo-update",
            },
        )
        assert code == 201, todo_update_preview
        code, todo_updated = request_json(
            f"{base_url}/api/actions/{todo_update_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, todo_updated
        assert todo_updated["proposal"]["receipt"]["outcome"] == "todo_updated", todo_updated
        assert "Verify typed action lifecycle and receipts" in state_path.read_text(encoding="utf-8")

        todo_state_before_defer = state_path.read_text(encoding="utf-8")
        code, unsupported_defer = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "todo.update",
                "summary": "Defer without an evaluable condition",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": current_todo_id,
                    "operation": "defer",
                    "resume_when": "owner_resume",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-todo-defer-unsupported",
            },
        )
        assert code == 400, unsupported_defer
        assert unsupported_defer["error_code"] == "invalid_action_preview", unsupported_defer
        assert "supported condition" in unsupported_defer["error"], unsupported_defer
        assert not any(
            proposal.get("idempotency_key") == "http-todo-defer-unsupported"
            for proposal in service.store.list()
        ), service.store.list()
        assert state_path.read_text(encoding="utf-8") == todo_state_before_defer

        code, valid_defer_preview = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "todo.update",
                "summary": "Defer until another Todo is done",
                "normalized_parameters": {
                    "goal_id": "goal-one",
                    "todo_id": current_todo_id,
                    "operation": "defer",
                    "agent_id": "codex",
                    "resume_when": f"todo_done:{current_todo_id}",
                },
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-todo-defer-supported",
            },
        )
        assert code == 201, valid_defer_preview
        assert valid_defer_preview["proposal"]["validation_evidence"] == [
            "Canonical LoopX Todo dry-run validated the requested transition."
        ], valid_defer_preview
        assert state_path.read_text(encoding="utf-8") == todo_state_before_defer
        code, valid_defer_applied = request_json(
            f"{base_url}/api/actions/{valid_defer_preview['proposal']['proposal_id']}/apply",
            method="POST",
            body={},
        )
        assert code == 200, valid_defer_applied
        assert valid_defer_applied["proposal"]["receipt"]["outcome"] == "todo_updated", valid_defer_applied
        assert f"resume_when=todo_done:{current_todo_id}" in state_path.read_text(encoding="utf-8")

        for index, parameters in enumerate(
            (
                {"operation": "reassign", "agent_id": "codex"},
                {"operation": "block", "note": "Waiting for owner evidence", "agent_id": "codex"},
                {"operation": "defer", "resume_when": f"todo_done:{current_todo_id}", "agent_id": "codex"},
                {"operation": "successor", "successor_todo_ids": [current_todo_id], "agent_id": "codex"},
            )
        ):
            code, transition_preview = request_json(
                f"{base_url}/api/actions/preview",
                method="POST",
                body={
                    "action_kind": "todo.update",
                    "summary": f"Todo transition {index}",
                    "normalized_parameters": {
                        "goal_id": "goal-one",
                        "todo_id": current_todo_id,
                        **parameters,
                    },
                    "context": {"kind": "goal", "goal_id": "goal-one"},
                    "idempotency_key": f"http-todo-transition-{index}",
                },
            )
            assert code == 201, transition_preview

        # A decision outcome is only valid on a User gate; the canonical owner
        # rejects it before any proposal is stored.
        code, misdirected_decision = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "gate.resolve",
                "summary": "Approve an Agent Todo",
                "normalized_parameters": {"goal_id": "goal-one", "todo_id": current_todo_id, "decision": "approve"},
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-misdirected-decision",
            },
        )
        assert code == 400, misdirected_decision
        assert "decision_outcome is only valid" in misdirected_decision["error"], misdirected_decision

        for index, (action_kind, params, gate_kind) in enumerate(
            [
                ("goal.update", {"goal_id": "goal-one", "objective": "A revised objective"},
                 "canonical_authority_required"),
                (
                    "gate.resolve",
                    {"goal_id": "goal-one", "todo_id": current_todo_id, "decision": "defer"},
                    "decision_outcome_required",
                ),
            ]
        ):
            code, protected_preview = request_json(
                f"{base_url}/api/actions/preview",
                method="POST",
                body={
                    "action_kind": action_kind,
                    "summary": f"Preview {action_kind}",
                    "normalized_parameters": params,
                    "context": {"kind": "goal", "goal_id": "goal-one"},
                    "idempotency_key": f"http-protected-{index}",
                },
            )
            assert code == 201, protected_preview
            code, protected_gate = request_json(
                f"{base_url}/api/actions/{protected_preview['proposal']['proposal_id']}/apply",
                method="POST",
                body={},
            )
            assert code == 409, protected_gate
            assert protected_gate["gate"]["kind"] == gate_kind, protected_gate

        persisted_payload = action_store.path.read_text(encoding="utf-8")
        assert str(root) not in persisted_payload, persisted_payload
        assert str(project := registry_path.parent.parent) not in persisted_payload, persisted_payload

        cancellable_code, cancellable = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "todo.create",
                "summary": "Cancel a Todo preview",
                "normalized_parameters": {"goal_id": "goal-one", "text": "Do not write"},
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-cancel",
            },
        )
        assert cancellable_code == 201, cancellable
        proposal_id = cancellable["proposal"]["proposal_id"]
        code, cancelled = request_json(
            f"{base_url}/api/actions/{proposal_id}/cancel",
            method="POST",
            body={},
        )
        assert code == 200 and cancelled["proposal"]["status"] == "cancelled", cancelled

        for index, transition in enumerate(("defer", "reject")):
            transition_code, transition_payload = request_json(
                f"{base_url}/api/actions/preview",
                method="POST",
                body={
                    "action_kind": "todo.create",
                    "summary": f"{transition.title()} a Todo preview",
                    "normalized_parameters": {
                        "goal_id": "goal-one",
                        "text": f"Do not write transition {index}",
                    },
                    "context": {"kind": "goal", "goal_id": "goal-one"},
                    "idempotency_key": f"http-{transition}",
                },
            )
            assert transition_code == 201, transition_payload
            code, transitioned = request_json(
                f"{base_url}/api/actions/{transition_payload['proposal']['proposal_id']}/{transition}",
                method="POST",
                body={},
            )
            assert code == 200 and transitioned["proposal"]["status"] == (
                "deferred" if transition == "defer" else "rejected"
            ), transitioned

        code, unsafe = request_json(
            f"{base_url}/api/actions/preview",
            method="POST",
            body={
                "action_kind": "todo.create",
                "summary": "Unsafe",
                "normalized_parameters": {"goal_id": "goal-one", "api_key": "sk-private"},
                "context": {"kind": "goal", "goal_id": "goal-one"},
                "idempotency_key": "http-unsafe",
            },
        )
        assert code == 400 and unsafe["ok"] is False, unsafe
        assert "private" not in json.dumps(unsafe), unsafe
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def assert_zero_goal_workspace_selection(root: Path) -> None:
    project = root / "empty-goal-workspace"
    (project / ".git").mkdir(parents=True)
    registry_path = root / "empty-registry.json"
    registry_path.write_text(
        json.dumps({"schema_version": 1, "goals": []}) + "\n",
        encoding="utf-8",
    )
    service = ChatActionService(
        store=ChatActionStore(root / "empty-action-store"),
        registry_path=registry_path,
        workspace_roots=[project],
    )
    selected, source_goal = service._project_for_goal_create({
        "normalized_parameters": {"workspace_ref": "current"},
        "context": {"kind": "manager", "goal_id": None},
    })
    assert selected == project.resolve()
    assert source_goal["id"] == ""
    assert source_goal["adapter"]["kind"] == "generic_project_goal_v0"

    second = root / "second-empty-workspace"
    (second / ".git").mkdir(parents=True)
    multi = ChatActionService(
        store=ChatActionStore(root / "multi-action-store"),
        registry_path=registry_path,
        workspace_roots=[project, second],
    )
    proposal = {
        "normalized_parameters": {"workspace_ref": "current"},
        "context": {"kind": "manager", "goal_id": None},
    }
    try:
        multi._project_for_goal_create(proposal)
    except Exception as exc:
        gate = getattr(exc, "gate", {})
        assert gate.get("kind") == "workspace_selection_required", gate
        candidates = gate.get("candidates")
        assert isinstance(candidates, list) and len(candidates) == 2, gate
        assert all(set(item) == {"workspace_ref", "label"} for item in candidates), candidates
        assert str(root) not in json.dumps(candidates), candidates
        selected, _ = multi._project_for_goal_create(
            {
                "normalized_parameters": {"workspace_ref": candidates[0]["workspace_ref"]},
                "context": {"kind": "manager", "goal_id": None},
            }
        )
        assert selected in {project.resolve(), second.resolve()}
    else:
        raise AssertionError("multiple workspaces must require a public-safe selection")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-chat-actions-") as raw_tmp:
        root = Path(raw_tmp) / "runtime" / "actions"
        store = ChatActionStore(root)

        created = store.create_preview(**preview_request(idempotency_key="create-goal-1"))
        assert created["status"] == "preview_ready", created
        assert created["action_kind"] == "goal.create", created
        assert created["receipt"] is None, created
        loaded = store.load(created["proposal_id"])
        assert loaded == created, (loaded, created)

        duplicate = store.create_preview(**preview_request(idempotency_key="create-goal-1"))
        assert duplicate == created, (duplicate, created)
        try:
            store.create_preview(
                **preview_request(
                    idempotency_key="create-goal-1",
                    summary="Create a different Goal",
                )
            )
        except ActionConflictError as exc:
            assert "idempotency" in str(exc).lower(), exc
        else:
            raise AssertionError("an idempotency key was reused for a different preview")

        cancellable = store.create_preview(
            **preview_request(idempotency_key="cancel-goal-1", summary="Cancel this preview")
        )
        cancelled = store.cancel(cancellable["proposal_id"])
        assert cancelled["status"] == "cancelled", cancelled
        assert store.cancel(cancellable["proposal_id"]) == cancelled
        try:
            store.apply(
                cancellable["proposal_id"],
                current_state_fingerprint="goal-state-r1",
                receipt=receipt("receipt-cancelled"),
            )
        except ActionConflictError as exc:
            assert "cancelled" in str(exc).lower(), exc
        else:
            raise AssertionError("a cancelled proposal was applied")

        applied = store.apply(
            created["proposal_id"],
            current_state_fingerprint="goal-state-r1",
            receipt=receipt(),
        )
        assert applied["status"] == "applied", applied
        assert applied["receipt"]["receipt_id"] == "receipt-goal-create-1", applied
        assert applied["receipt"]["projection_verified"] is True, applied
        repeated_apply = store.apply(
            created["proposal_id"],
            current_state_fingerprint="goal-state-r1",
            receipt=receipt("a-later-duplicate-receipt"),
        )
        assert repeated_apply == applied, (repeated_apply, applied)

        stale_candidate = store.create_preview(
            **preview_request(idempotency_key="stale-goal-1", summary="Update a Goal")
        )
        stale = store.apply(
            stale_candidate["proposal_id"],
            current_state_fingerprint="goal-state-r2",
            receipt=receipt("receipt-must-not-be-written"),
        )
        assert stale["status"] == "stale", stale
        assert stale["receipt"] is None, stale
        assert stale["stale"]["expected_state_fingerprint"] == "goal-state-r1", stale
        assert stale["stale"]["current_state_fingerprint"] == "goal-state-r2", stale
        assert store.load(stale_candidate["proposal_id"]) == stale

        gated_candidate = store.create_preview(
            **preview_request(idempotency_key="gated-goal-1", summary="Gate this Goal")
        )
        applying = store.start_apply(gated_candidate["proposal_id"])
        assert applying["status"] == "applying", applying
        gated = store.mark_gated(
            gated_candidate["proposal_id"],
            gate={"kind": "owner_approval", "summary": "Owner approval required"},
        )
        assert gated["status"] == "gated", gated
        assert store.start_apply(gated_candidate["proposal_id"])["status"] == "applying"
        failed = store.mark_failed(
            gated_candidate["proposal_id"],
            error_code="temporary_failure",
            message="Temporary canonical service failure",
        )
        assert failed["status"] == "failed", failed
        assert store.start_apply(gated_candidate["proposal_id"])["status"] == "applying"

        unsafe_requests = [
            {"normalized_parameters": {"api_key": "sk-private-value"}},
            {"normalized_parameters": {"workspace": "/Users/example/private/repo"}},
            {
                "context": {
                    "kind": "goal",
                    "auth" + "orization": "Bear" + "er private-value",
                }
            },
            {"validation_evidence": ["raw provider payload follows"]},
        ]
        for index, unsafe in enumerate(unsafe_requests):
            request = preview_request(idempotency_key=f"unsafe-{index}")
            request.update(unsafe)
            try:
                store.create_preview(**request)
            except ValueError as exc:
                assert "sensitive" in str(exc).lower(), exc
            else:
                raise AssertionError(f"sensitive preview {index} was persisted")

        safe_receipt_target = store.create_preview(
            **preview_request(idempotency_key="unsafe-receipt-target")
        )
        try:
            store.apply(
                safe_receipt_target["proposal_id"],
                current_state_fingerprint="goal-state-r1",
                receipt={"receipt_id": "bad", "raw_tool_output": "private material"},
            )
        except ValueError as exc:
            assert "sensitive" in str(exc).lower(), exc
        else:
            raise AssertionError("a sensitive apply receipt was persisted")
        assert store.load(safe_receipt_target["proposal_id"])["status"] == "preview_ready"

        assert_owner_only_tree(root)
        assert str(Path.home()) not in root.read_text(encoding="utf-8") if root.is_file() else True
        assert os.environ.get("HOME", "") not in "\n".join(
            path.read_text(encoding="utf-8")
            for path in root.rglob("*")
            if path.is_file()
        )

    with tempfile.TemporaryDirectory(prefix="loopx-chat-action-http-") as raw_tmp:
        root = Path(raw_tmp)
        assert_http_action_api(root)
        assert_zero_goal_workspace_selection(root)

    print("loopx-chat-actions-smoke: ok")


if __name__ == "__main__":
    main()
