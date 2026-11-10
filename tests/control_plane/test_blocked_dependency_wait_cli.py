"""Native quiet-wait coverage for a blocked peer prerequisite and clocks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_source_fixture
from tests.control_plane.test_dated_vision_wait_cli import (
    AGENT, GOAL, TODO, cli, fixture,
    isolated_effect as isolated_effect,
)


def mixed_wait_fixture(root: Path, *, optional_notice: bool = False, target_status: str | None = "blocked"):
    dependency = "todo_peer_dependency"
    registry, runtime, state = fixture(root, f"todo_done:{dependency}")
    waiting_ids = [TODO, *(f"todo_clock_{index}" for index in range(6))]
    with state.open("a", encoding="utf-8") as output:
        for index, todo_id in enumerate(waiting_ids[1:]):
            output.write(
                f"\n- [ ] [P1] Retain scheduled work {todo_id}.\n"
                f"  <!-- loopx:todo todo_id={todo_id} status=deferred claimed_by={AGENT} "
                f"task_class=advancement_task action_kind=validate "
                f"resume_when=resume_at:2099-01-0{6 - index}T00:00:00Z -->\n"
            )
        if target_status:
            output.write(
                "\n- [ ] [P1] Deliver the peer prerequisite.\n"
                f"  <!-- loopx:todo todo_id={dependency} status={target_status} claimed_by=agent-peer "
                "task_class=advancement_task action_kind=validate -->\n"
            )
        if optional_notice:
            output.write(
                "\n## User Todo / Owner Review Reading Queue\n\n"
                "- [ ] [P2] Review the optional public draft.\n"
                f"  <!-- loopx:todo todo_id=todo_optional_draft status=open "
                f"task_class=user_action bound_agent={AGENT} -->\n"
            )
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["registered_agents"].append("agent-peer")
    registry.write_text(json.dumps(config), encoding="utf-8")
    vision = runtime / f"goals/{GOAL}/runs/vision.json"
    run = json.loads(vision.read_text())
    run["agent_vision"]["todo_delta"] = [f"retain:{todo_id}" for todo_id in waiting_ids]
    vision.write_text(json.dumps(run), encoding="utf-8")
    (vision.parent / "index.jsonl").write_text(
        json.dumps({**run, "json_path": str(vision)}) + "\n", encoding="utf-8",
    )
    return registry, runtime, state, waiting_ids


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("optional_notice", [False, True])
def test_blocked_peer_and_clock_waits_remain_quiet_without_polling(
    tmp_path, isolated_effect, provider, optional_notice,
):
    registry, runtime, state, waiting_ids = mixed_wait_fixture(tmp_path, optional_notice=optional_notice)
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider=provider)
    original = state.read_bytes()
    args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", "mixed-pending-waits")
    for expected_status in ("committed", "replayed"):
        rc, guard = cli(registry, runtime, *args)
        assert rc == 0, guard
        assert guard["effective_action"] == "agent_scope_wait"
        wait = guard["vision_wait_state"]
        assert wait["waiting_todo_count"] == len(waiting_ids)
        assert wait["pending_resume_count"] == len(waiting_ids), wait
        assert len(wait["waiting_todo_ids"]) < len(waiting_ids)
        # A dependency must keep periodic source rechecks, rather than sleeping
        # until one of the distant clock-only successors is due.
        assert wait["selected_todo_id"] == TODO
        assert wait["resume_condition"]["target_status"] == "blocked"
        assert guard["heartbeat_receipt"]["status"] == expected_status
        assert "settlement_identity" not in guard["heartbeat_receipt"]
        contract = guard["interaction_contract"]
        assert contract["agent_channel"]["must_attempt"] is False, contract
        assert contract["agent_channel"]["quiet_noop_allowed"] is True
        assert contract["agent_channel"]["delivery_allowed"] is False
        assert contract["user_channel"]["action_required"] is False
        assert not any("monitor-poll" in action for action in contract["cli_channel"]["next_cli_actions"])
        assert guard["scheduler_hint"]["action"] == "backoff_until_state_change"
    rc, poll = cli(registry, runtime, "quota", "monitor-poll", "--execute", "--codex-app",
                   "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", args[-1])
    assert rc == 1, poll
    assert poll["error_code"] == "heartbeat_receipt_identity_conflict"
    assert state.read_bytes() == original
    rc, listed = cli(registry, runtime, "todo", "list", "--goal-id", GOAL)
    assert rc == 0, listed
    rows = {todo["todo_id"]: todo for todo in listed["todos"]}
    assert rows["todo_peer_dependency"]["status"] == "blocked"
    assert all(rows[todo_id]["status"] == "deferred" for todo_id in waiting_ids)
    runs = runtime / f"goals/{GOAL}/runs/index.jsonl"
    assert not any(json.loads(line).get("classification") in ("quota_monitor_poll", "quota_slot_spent")
                   for line in runs.read_text().splitlines())


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("extra_work", ["advancement_task", "continuous_monitor"])
def test_pending_peer_dependency_does_not_hide_independent_or_due_work(
    tmp_path, isolated_effect, provider, extra_work,
):
    registry, runtime, state, _ = mixed_wait_fixture(tmp_path)
    with state.open("a", encoding="utf-8") as output:
        output.write(
            "\n- [ ] [P1] Perform independent work.\n"
            f"  <!-- loopx:todo todo_id=todo_independent status=open claimed_by={AGENT} "
            f"task_class={extra_work} action_kind=validate "
            + ("cadence=360m next_due_at=2026-01-01T00:00:00Z " if extra_work == "continuous_monitor" else "")
            + "-->\n"
        )
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider=provider)
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
                    "--agent-id", AGENT, "--turn-instance-id", "mixed-independent-work")
    assert rc == 0, guard
    assert guard["selected_todo"]["todo_id"] == "todo_independent", guard
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_missing_peer_source_cannot_qualify_a_quiet_wait(tmp_path, isolated_effect, provider):
    registry, runtime, state, waiting_ids = mixed_wait_fixture(tmp_path, target_status=None)
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider=provider)
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
                    "--agent-id", AGENT, "--turn-instance-id", "mixed-unknown-source")
    assert rc == 0, guard
    wait = guard["vision_wait_state"]
    assert wait["waiting_todo_count"] == len(waiting_ids)
    assert wait["pending_resume_count"] == len(waiting_ids) - 1
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert guard["interaction_contract"]["agent_channel"]["quiet_noop_allowed"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_completed_peer_dependency_restores_explicit_resume(tmp_path, isolated_effect, provider):
    registry, runtime, state, _ = mixed_wait_fixture(tmp_path, target_status="done")
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider=provider)
    original = state.read_bytes()
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
                    "--agent-id", AGENT, "--turn-instance-id", "mixed-completed-dependency")
    assert rc == 0, guard
    assert guard["effective_action"] == "successor_replan_required", guard
    assert guard["selected_todo"]["todo_id"] == TODO
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert any("--clear-resume-when" in action
               for action in guard["interaction_contract"]["cli_channel"]["next_cli_actions"])
    assert state.read_bytes() == original
