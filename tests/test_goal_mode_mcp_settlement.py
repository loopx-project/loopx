from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane import host_adapter_settlement
from loopx.control_plane.host_adapter_settlement import (
    HostTodoSettlementRequest,
    host_adapter_turn_instance_id,
    settle_host_todo_completion,
)
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from loopx.status import parse_active_state_todos
from loopx.todos import add_goal_todo


GOAL_ID = "mcp-settlement-fixture"
AGENT_ID = "claude"


def test_host_settlement_adapter_uses_two_runtime_crossings_and_stops_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = HostTodoSettlementRequest(
        goal_id="goal",
        agent_id="agent",
        todo_id="todo_abc123",
        runtime_profile="claude_code",
        legacy_host_surface="claude_code",
        scheduler_owner="agent_cli_loop",
        execution_mode="interactive",
        completion_args=("todo", "complete", "todo_abc123"),
    )
    identity = {
        "schema_version": "quota_settlement_identity_v0",
        "effect_id": "goal:agent:todo_abc123:mcp-turn",
        "goal_id": "goal",
        "agent_id": "agent",
        "todo_id": "todo_abc123",
        "turn_instance_id": "mcp-turn",
    }
    runtime_phases: list[str] = []

    def fake_runtime(_method: str, params: dict[str, object]) -> dict[str, object]:
        runtime_phases.append(str(params["phase"]))
        if params["phase"] == "prepare":
            return {
                "schema_version": "loopx_host_todo_completion_reduction_v0",
                "phase": "prepare",
                "decision": "execute",
                "identity": identity,
                "provider_effect": {
                    "provider_id": "loopx_cli",
                    "kind": "ordered_cli_sequence",
                    "steps": [
                        {
                            "step_kind": "guard",
                            "args": ["guard"],
                            "legacy_args": None,
                            "continue_when": {
                                "kind": "equals",
                                "path": ["ok"],
                                "value": True,
                            },
                        },
                        {
                            "step_kind": "lifecycle_completion",
                            "args": ["complete"],
                            "legacy_args": None,
                            "continue_when": {
                                "kind": "equals",
                                "path": ["ok"],
                                "value": True,
                            },
                        },
                        {
                            "step_kind": "durable_writeback",
                            "args": ["writeback"],
                            "legacy_args": None,
                            "continue_when": {
                                "kind": "equals",
                                "path": ["ok"],
                                "value": True,
                            },
                        },
                        {
                            "step_kind": "quota_spend",
                            "args": ["spend"],
                            "legacy_args": None,
                            "continue_when": None,
                        },
                    ],
                },
                "result": None,
            }
        assert params["phase"] == "finalize"
        outcomes = params["provider_outcomes"]
        assert isinstance(outcomes, list)
        assert [item["step_kind"] for item in outcomes] == [
            "guard",
            "lifecycle_completion",
        ]
        return {
            "schema_version": "loopx_host_todo_completion_reduction_v0",
            "phase": "finalize",
            "decision": "provider_result",
            "identity": identity,
            "provider_effect": None,
            "result": json.loads(str(outcomes[-1]["output"])),
        }

    provider_calls: list[list[str]] = []

    def fake_provider(args: list[str], *, legacy_args: list[str] | None = None) -> str:
        del legacy_args
        provider_calls.append(args)
        return '{"ok": true}' if args == ["guard"] else '{"ok": false}'

    monkeypatch.setattr(host_adapter_settlement, "effect_runtime_result", fake_runtime)

    result = json.loads(settle_host_todo_completion(request, run_cli=fake_provider))

    assert result == {"ok": False}
    assert runtime_phases == ["prepare", "finalize"]
    assert provider_calls == [["guard"], ["complete"]]


def test_typescript_provider_plan_short_circuits_typed_completion_rejection() -> None:
    request = HostTodoSettlementRequest(
        goal_id="goal",
        agent_id="agent",
        todo_id="todo_abc123",
        runtime_profile="claude_code",
        legacy_host_surface="claude_code",
        scheduler_owner="agent_cli_loop",
        execution_mode="interactive",
        completion_args=("todo", "complete", "todo_abc123"),
    )
    provider_calls: list[list[str]] = []
    rejection = {
        "ok": False,
        "completed": False,
        "status": "open",
        "reason": "validation failed",
    }

    def provider(args: list[str], *, legacy_args: list[str] | None = None) -> str:
        del legacy_args
        provider_calls.append(args)
        if args[:2] == ["quota", "should-run"]:
            return json.dumps(
                {
                    "ok": True,
                    "selected_todo": {"todo_id": request.todo_id.upper()},
                }
            )
        return json.dumps(rejection)

    result = json.loads(settle_host_todo_completion(request, run_cli=provider))

    assert result == rejection
    assert len(provider_calls) == 2
    assert provider_calls[0][:2] == ["quota", "should-run"]
    assert provider_calls[1][:2] == ["todo", "complete"]


def _write_fixture(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "status: active",
                "updated_at: 2026-08-21T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file.name,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, state_file


def _run_cli(registry: Path, *args: str) -> tuple[int, dict[str, object]]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(result.stdout)


def _control(registry: Path) -> GoalModeMCPControlPlane:
    control = GoalModeMCPControlPlane(
        GoalModeMCPConfig(
            server_name="loopx-test",
            runtime_profile="claude_code",
            legacy_host_surface="claude_code",
        ),
        lambda: {
            "goal_id": GOAL_ID,
            "registry": str(registry),
            "agent_id": AGENT_ID,
        },
    )
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]
    return control


def test_real_mcp_unconnected_delivery_is_rejected_before_settlement(tmp_path: Path) -> None:
    registry, state_file = _write_fixture(tmp_path)
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["adapter"].pop("status")
    registry.write_text(json.dumps(registration), encoding="utf-8")
    added = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Deliver the bounded fixture.", task_class="advancement_task", claimed_by=AGENT_ID)
    before = state_file.read_bytes()

    result = json.loads(_control(registry).complete_task(added["todo_id"], AGENT_ID, "Verified fixture."))

    assert result["ok"] is False
    assert result["settlement"]["failed_stage"] == "guard"
    assert "delivery_not_allowed" in result["settlement"]["reason"]
    assert state_file.read_bytes() == before
    index = registry.parent / "runtime" / "goals" / GOAL_ID / "runs" / "index.jsonl"
    assert not index.exists() or not index.read_bytes()


def test_real_mcp_settlement_rejects_missing_writeback_then_commits_same_identity(
    tmp_path: Path,
) -> None:
    registry, state_file = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver the bounded fixture change.",
        task_class="advancement_task",
        claimed_by=AGENT_ID,
        continuation_policy="same_agent_non_delivery",
    )
    todo_id = str(added["todo_id"])
    request = HostTodoSettlementRequest(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=todo_id,
        runtime_profile="claude_code",
        legacy_host_surface="claude_code",
        scheduler_owner="agent_cli_loop",
        execution_mode="interactive",
        completion_args=(),
    )
    turn_instance_id = host_adapter_turn_instance_id(request)

    guard_rc, guard = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo_id,
        "--turn-instance-id",
        turn_instance_id,
        "--runtime-profile",
        "claude_code",
    )
    assert guard_rc == 0, guard
    assert guard["selected_todo"]["todo_id"] == todo_id  # type: ignore[index]

    premature_rc, premature = _run_cli(
        registry,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo_id,
        "--turn-instance-id",
        turn_instance_id,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
    )
    assert premature_rc == 1
    assert premature["ok"] is False
    assert "accountable refresh-state receipt is missing" in str(
        premature.get("reason") or premature.get("error")
    )

    control = _control(registry)
    output = control.complete_task(
        todo_id,
        AGENT_ID,
        "focused fixture validation passed",
        next_agent_todo="Run the successor fixture check.",
    )
    result = json.loads(output)

    assert result["ok"] is True, json.dumps(result, indent=2, sort_keys=True)
    identity = result["settlement_identity"]
    assert identity["todo_id"] == todo_id
    assert identity["turn_instance_id"] == turn_instance_id
    settlement = result["settlement"]
    assert settlement["durable_writeback"]["ok"] is True
    assert settlement["lifecycle_completion"]["ok"] is True
    assert settlement["quota_spend"]["ok"] is True
    assert settlement["quota_spend"]["appended"] is True

    status_rc, status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "claude_code",
    )
    assert status_rc == 0, status
    assert status["quota"]["spent_slots"] == 1  # type: ignore[index]
    successor_id = status["selected_todo"]["todo_id"]  # type: ignore[index]
    assert successor_id != todo_id
    assert settlement["quota_spend"]["settlement_identity"]["todo_id"] == todo_id

    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    todo_by_id = {
        str(item["todo_id"]): item for item in todos["agent_todos"]["items"]
    }
    assert todo_by_id[todo_id]["status"] == "done"
    assert todo_by_id[str(successor_id)]["status"] == "open"

    replay = json.loads(
        control.complete_task(
            todo_id,
            AGENT_ID,
            "focused fixture validation passed",
            next_agent_todo="Run the successor fixture check.",
        )
    )
    assert replay["ok"] is True, json.dumps(replay, indent=2, sort_keys=True)
    assert replay["settlement_identity"] == identity
    assert replay["settlement"]["quota_spend"]["appended"] is False
    assert replay["settlement"]["quota_spend"]["idempotent_replay"] is True

    replay_status_rc, replay_status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "claude_code",
    )
    assert replay_status_rc == 0, replay_status
    assert replay_status["quota"]["spent_slots"] == 1  # type: ignore[index]


def test_real_mcp_terminal_completion_closes_out_after_spend(
    tmp_path: Path,
) -> None:
    registry, state_file = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Finish the bounded non-delivery fixture.",
        task_class="advancement_task",
        continuation_policy="same_agent_non_delivery",
        claimed_by=AGENT_ID,
    )
    todo_id = str(added["todo_id"])

    result = json.loads(
        _control(registry).complete_task(
            todo_id,
            AGENT_ID,
            "terminal fixture validation passed",
            no_follow_up=True,
        )
    )

    assert result["ok"] is True, json.dumps(result, indent=2, sort_keys=True)
    identity = result["settlement_identity"]
    assert identity["todo_id"] == todo_id
    settlement = result["settlement"]
    assert settlement["lifecycle_completion"]["completion_continuation"] == (
        "active_goal"
    )
    assert settlement["quota_spend"]["appended"] is True
    terminal = settlement["terminal_closeout"]
    assert terminal["completion_continuation"] == "no_followup"
    assert terminal["completion_recovery"] == "same_turn_terminal_closeout"
    assert [
        receipt["step_kind"]
        for receipt in terminal["settlement_result"]["receipts"]
    ] == [
        "validation",
        "durable_writeback",
        "quota_spend",
        "terminal_closeout",
    ]

    status_rc, status = _run_cli(
        registry,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        "claude_code",
    )
    assert status_rc == 0, status
    assert status["quota"]["spent_slots"] == 1  # type: ignore[index]

    todos = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
    completed = next(
        item
        for item in todos["agent_todos"]["items"]
        if item["todo_id"] == todo_id
    )
    assert completed["status"] == "done"
    assert "no_followup=true" in state_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("lost_after", ["lifecycle", "writeback", "spend"])
def test_real_mcp_completion_recovers_a_lost_mutation_response(
    tmp_path: Path,
    lost_after: str,
) -> None:
    """The real write commits, but its caller sees a failure: retry must not pay twice."""
    registry, _ = _write_fixture(tmp_path)
    added = add_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Validate response-loss recovery.", task_class="advancement_task",
        claimed_by=AGENT_ID,
    )
    control = _control(registry)
    original = control.run_cli
    injected = False
    bound_identity = None

    def lose_once(args, **kwargs):
        nonlocal injected, bound_identity
        output = original(args, **kwargs)
        step = {"lifecycle": ["todo", "complete"], "writeback": ["refresh-state"],
                "spend": ["quota", "spend-slot"]}[lost_after]
        if not injected and args[:len(step)] == step and json.loads(output).get("ok"):
            injected = True
            bound_identity = json.loads(output)["settlement_identity"]
            return json.dumps({"ok": False, "error": "synthetic_response_lost_after_commit"})
        return output

    control.run_cli = lose_once
    first = json.loads(control.complete_task(
        added["todo_id"], AGENT_ID, "recovery fixture check passed", no_follow_up=True,
    ))
    assert injected and first["ok"] is False
    from loopx.control_plane.quota.settlement import read_heartbeat_settlement
    readback = read_heartbeat_settlement(
        tmp_path / "runtime", goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=added["todo_id"], turn_instance_id=bound_identity["turn_instance_id"],
    )
    assert readback.replay_phase == ("settled" if lost_after == "spend" else "settlement_pending")
    if lost_after != "spend":
        rc, terminal = _run_cli(
            registry, "todo", "complete", "--goal-id", GOAL_ID,
            "--todo-id", added["todo_id"], "--agent-id", AGENT_ID,
            "--turn-instance-id", bound_identity["turn_instance_id"],
            "--no-follow-up", "--evidence", "synthetic terminal intent",
        )
        assert rc != 0 and terminal["settlement_blocked_completion"] is True
    replay = json.loads(control.complete_task(
        added["todo_id"], AGENT_ID, "recovery fixture check passed", no_follow_up=True,
    ))
    assert replay["ok"] is True
    again = json.loads(control.complete_task(
        added["todo_id"], AGENT_ID, "recovery fixture check passed", no_follow_up=True,
    ))
    assert again["ok"] is True
    assert again["settlement_identity"] == replay["settlement_identity"]
    assert again["settlement"]["quota_spend"]["appended"] is False
    status = json.loads(control.should_run())
    assert status["quota"]["spent_slots"] == 1


def test_real_mcp_links_existing_successor_without_creating_another_todo(
    tmp_path: Path,
) -> None:
    registry, state_file = _write_fixture(tmp_path)
    ids = [str(add_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, role="agent", text=text,
        task_class="advancement_task",
        claimed_by=AGENT_ID,
    )["todo_id"]) for text in ("First accepted step.", "Already planned follow-up.")]
    control = _control(registry)
    before = state_file.read_bytes()
    rejected = json.loads(control.complete_task(
        ids[0], AGENT_ID, "synthetic check passed", successor_todo_ids=[ids[1]], no_follow_up=True,
    ))
    assert rejected["ok"] is False
    assert state_file.read_bytes() == before
    first = json.loads(control.complete_task(
        ids[0], AGENT_ID, "synthetic check passed", successor_todo_ids=[ids[1]],
    ))
    assert first["ok"] is True, first
    replay = json.loads(control.complete_task(
        ids[0], AGENT_ID, "synthetic check passed", successor_todo_ids=[ids[1]],
    ))
    assert replay["ok"] is True
    assert replay["settlement_identity"] == first["settlement_identity"]
    todos = parse_active_state_todos(state_file.read_text())["agent_todos"]["items"]
    by_id = {row["todo_id"]: row for row in todos}
    assert set(by_id) == set(ids)
    assert by_id[ids[0]]["status"] == "done" and by_id[ids[1]]["status"] == "open"
    assert by_id[ids[0]]["successor_todo_ids"] == [ids[1]]
    assert json.loads(control.should_run())["quota"]["spent_slots"] == 1
