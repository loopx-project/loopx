from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.runtime.time import now_utc, utc_isoformat
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.quota.execution_preparation import (
    apply_selected_todo_execution_preparation,
)


GOAL = "goal-execution-preparation"
TODO = "todo_execution_preparation"
AGENT = "agent-a"


def _todo() -> dict[str, object]:
    return {
        "schema_version": "todo_item_v0",
        "todo_id": TODO,
        "role": "agent",
        "source_section": "Agent Todo",
        "archive_state": "active",
        "text": "Deliver one validated synthetic change",
        "status": "open",
        "done": False,
        "task_class": "advancement_task",
        "priority": "P1",
        "claimed_by": AGENT,
        "index": 1,
    }


def _lease(*, owner: str = AGENT, status: str = "active", expired: bool = False) -> dict[str, object]:
    at = now_utc()
    return {
        "schema_version": "task_lease_v0",
        "goal_id": GOAL,
        "todo_id": TODO,
        "owner": owner,
        "idempotency_key": f"{owner}-execution",
        "write_scopes": [],
        "acquire_ttl_seconds": 600,
        "version": 2,
        "lease_epoch": 1,
        "status": status,
        "acquired_at": utc_isoformat(at - timedelta(seconds=120)),
        "updated_at": utc_isoformat(at - timedelta(seconds=120)),
        "expires_at": utc_isoformat(at - timedelta(seconds=60) if expired else at + timedelta(seconds=600)),
        **({"released_at": utc_isoformat(at)} if status == "released" else {}),
    }


def _run(
    tmp_path,
    monkeypatch,
    provider: str,
    leases: list[dict[str, object]],
    *guard_args: str,
    todos: list[dict[str, object]] | None = None,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    state = tmp_path / "state.md"
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n\n## Objective\n"
        "Deliver one validated synthetic change.\n\n## Next Action\n"
        "Run the selected Todo.\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=tmp_path,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL,
        domain="execution-preparation",
        adapter_kind="generic_project_goal_v0",
        state_file=str(state),
        registered_agents=[AGENT, "agent-b"],
        quota_allowed_slots=None,
        extra_goal_fields={"coordination": {
            "registered_agents": [AGENT, "agent-b"],
            "handoff_mode": "hard_lease",
        }},
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id=GOAL,
        todos=[_todo()] if todos is None else todos,
        leases=leases,
        handoff_mode="hard_lease",
    )
    initialize_canonical_authority(
        runtime, GOAL, projection, state_path=state, provider=provider,
    )
    code, packet = run_json_cli_result(
        "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--context-delivery", "off",
        *guard_args,
        registry_path=registry, runtime_root=runtime,
    )
    assert code == 0, json.dumps(packet, ensure_ascii=False, indent=2)
    return packet


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "leases,reason_code",
    [
        ([], "task_lease_missing"),
        ([_lease(status="released")], "task_lease_released"),
        ([_lease(expired=True)], "task_lease_expired"),
    ],
)
def test_should_run_requires_one_atomic_claim_before_delivery(
    tmp_path, monkeypatch, provider, leases, reason_code,
):
    packet = _run(tmp_path, monkeypatch, provider, leases)
    preparation = packet["execution_preparation"]
    assert preparation["state"] == "claim_required"
    assert preparation["reason_code"] == reason_code
    assert preparation["claim"]["host_execution_allowed"] is True
    assert preparation["claim"]["command_line"].startswith("loopx todo claim ")
    assert packet["should_run"] is True
    assert packet["normal_delivery_allowed"] is False
    assert packet["selected_todo"]["claim_required_before_work"] is True
    contract = packet["interaction_contract"]
    assert contract["agent_channel"]["must_attempt"] is True
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["cli_channel"]["next_cli_actions"][0] == preparation["claim"]["command_line"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_matching_active_lease_preserves_delivery(tmp_path, monkeypatch, provider):
    packet = _run(tmp_path, monkeypatch, provider, [_lease()])
    assert packet["execution_preparation"]["state"] == "ready"
    assert packet["normal_delivery_allowed"] is True
    assert packet["interaction_contract"]["agent_channel"]["delivery_allowed"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_projected_claim_prepares_the_same_todo_for_a_fresh_guard(
    tmp_path, monkeypatch, provider,
):
    before = _run(tmp_path, monkeypatch, provider, [])
    claim_args = before["execution_preparation"]["claim"]["cli_args"]

    claim_code, claim = run_json_cli_result(
        *claim_args,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert claim_code == 0, json.dumps(claim, ensure_ascii=False, indent=2)

    guard_code, after = run_json_cli_result(
        "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "--context-delivery", "off",
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0, json.dumps(after, ensure_ascii=False, indent=2)
    assert after["selected_todo"]["todo_id"] == TODO
    assert after["execution_preparation"]["state"] == "ready"
    assert after["normal_delivery_allowed"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("multiple_candidates", [False, True])
def test_projected_claim_prepares_the_same_receipt_bound_turn(
    tmp_path, monkeypatch, provider, multiple_candidates,
):
    unbound = _run(
        tmp_path,
        monkeypatch,
        provider,
        [],
        "--runtime-profile",
        "codex_app_ssh_goal",
        "--begin-turn",
        todos=[_todo(), {**_todo(), "todo_id": "todo_other_candidate", "index": 2}]
        if multiple_candidates else None,
    )
    assert "heartbeat_receipt" in unbound, json.dumps(
        unbound, ensure_ascii=False, indent=2,
    )
    turn = unbound["heartbeat_receipt"]["turn_instance_id"]
    if multiple_candidates:
        assert unbound["interaction_contract"]["cli_channel"]["selection_required"] is True
    guard_code, before = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0, json.dumps(before, ensure_ascii=False, indent=2)
    assert before["execution_preparation"]["state"] == "claim_required"
    assert before["heartbeat_receipt"]["turn_instance_id"] == turn
    assert before["selected_todo"]["todo_id"] == TODO
    assert before["selected_todo"]["selection_binding"] == "heartbeat_receipt"
    assert before["normal_delivery_allowed"] is False
    contract = before["interaction_contract"]
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert contract["cli_channel"]["next_cli_actions"] == [
        before["execution_preparation"]["claim"]["command_line"],
    ]
    assert "settlement_plan" not in contract["cli_channel"]
    assert not read_canonical_todos_if_promoted(
        runtime_root=tmp_path / "runtime", goal_id=GOAL, include_leases=True,
    )["leases"]

    claim_code, claim = run_json_cli_result(
        *before["execution_preparation"]["claim"]["cli_args"],
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert claim_code == 0, json.dumps(claim, ensure_ascii=False, indent=2)

    guard_code, after = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0, json.dumps(after, ensure_ascii=False, indent=2)
    assert after["heartbeat_receipt"]["turn_instance_id"] == turn
    assert after["selected_todo"]["todo_id"] == TODO
    assert after["execution_preparation"]["state"] == "ready"
    assert after["normal_delivery_allowed"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_foreign_active_lease_blocks_without_claim_action(tmp_path, monkeypatch, provider):
    packet = _run(tmp_path, monkeypatch, provider, [_lease(owner="agent-b")])
    preparation = packet["execution_preparation"]
    assert preparation["state"] == "blocked_by_conflict"
    assert preparation["reason_code"] == "active_lease_owned_by_other_agent"
    assert "claim" not in preparation
    assert packet["should_run"] is False
    assert packet["normal_delivery_allowed"] is False


def test_preparation_does_not_replace_explicit_action_selection(monkeypatch):
    from loopx.control_plane.quota import execution_preparation as module

    monkeypatch.setattr(
        module,
        "project_selected_todo_execution_preparation",
        lambda **_kwargs: pytest.fail("preparation must wait for the current binding"),
    )
    payload = {
        "should_run": True,
        "selected_todo": {"todo_id": TODO},
    }
    payload["action_portfolio"] = {
        "selection_policy": {"requires_explicit_turn_binding": True},
    }
    apply_selected_todo_execution_preparation(
        payload,
        runtime_root="/synthetic/runtime",
        goal_id=GOAL,
        agent_id=AGENT,
    )
    assert "execution_preparation" not in payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("lease_state", ["missing", "expired"])
def test_codex_lifecycle_hook_uses_captured_authority_from_another_project(
    tmp_path, monkeypatch, provider, lease_state,
):
    from loopx.control_plane.turn_driver.codex_context import (
        codex_context_hook,
        project_codex_context,
    )

    before = _run(
        tmp_path,
        monkeypatch,
        provider,
        [] if lease_state == "missing" else [_lease(expired=True)],
        "--runtime-profile", "codex_app_ssh_goal",
        "--begin-turn",
    )
    turn = before["heartbeat_receipt"]["turn_instance_id"]
    context_root = tmp_path / "context"
    capture_root = tmp_path / "capture"
    context_root.mkdir(mode=0o700)
    capture_root.mkdir(mode=0o700)
    foreign_workspace = tmp_path / "project-b"
    foreign_workspace.mkdir()
    monkeypatch.setenv("CODEX_THREAD_ID", "codex-hook-session")
    monkeypatch.chdir(foreign_workspace)
    event = {
        "session_id": "codex-hook-session",
        "cwd": str(foreign_workspace.resolve()),
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    assert codex_context_hook(str(context_root), event) == {}
    project_codex_context(
        before,
        directory=str(context_root),
        capture_directory=capture_root,
        goal_id=GOAL,
        agent_id=AGENT,
        surface="quota_packet",
        capture_id="claim-required",
    )

    command = [
        sys.executable, "-m", "loopx.entrypoint",
        # The lifecycle process may be rooted in another project.  These
        # deliberately wrong invocation routes must not replace the route
        # retained with the captured preparation action.
        "--registry", str(foreign_workspace / ".loopx" / "registry.json"),
        "--runtime-root", str(foreign_workspace / ".loopx" / "runtime"),
        "--format", "json",
        "quota", "context-hook",
        "--context-delivery", "on",
        "--runtime-profile", "codex_app_ssh_goal",
        "--codex-context-dir", str(context_root),
    ]
    post_tool = {**event, "hook_event_name": "PostToolUse", "tool_response": "{}"}
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

    # A subagent may share its parent's native session and cwd, but it must not
    # execute or receive the parent's claim action.
    child = subprocess.run(
        command,
        input=json.dumps({**post_tool, "agent_id": "child-agent"}),
        cwd=foreign_workspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {}
    child_guard_code, child_guard = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert child_guard_code == 0
    assert child_guard["execution_preparation"]["state"] == "claim_required"

    prepared = subprocess.run(
        command,
        input=json.dumps(post_tool),
        cwd=foreign_workspace,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert prepared.returncode == 0, prepared.stderr
    hook = json.loads(prepared.stdout)
    additional = hook["hookSpecificOutput"]["additionalContext"]
    assert "automatically restored" in additional
    assert '"state":"ready"' in additional
    assert turn in additional

    guard_code, after = run_json_cli_result(
        "quota", "should-run",
        "--runtime-profile", "codex_app_ssh_goal",
        "--context-delivery", "off",
        "--goal-id", GOAL,
        "--agent-id", AGENT,
        "--turn-instance-id", turn,
        "--todo-id", TODO,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
    )
    assert guard_code == 0
    assert after["execution_preparation"]["state"] == "ready"

    repeated = subprocess.run(
        command,
        input=json.dumps(post_tool),
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert repeated.returncode == 0 and json.loads(repeated.stdout) == {}
