"""Receipt-owned waits must survive automatic stall writeback on real stores."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
import test_quota_settlement_cli as cli
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("scenario", ["waiting", "notice_waiting", "notice_independent", "due_monitor", "independent", "no_retry", "earlier_notice", "equal_notice", "earlier_gate", "earlier_independent"])
def test_retry_monitor_guard_and_replay_keep_receipt_and_work_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, scenario: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = cli._write_fixture(tmp_path)
    configuration = json.loads(registry.read_text())
    configuration["goals"][0]["status"] = "active"
    registry.write_text(json.dumps(configuration))
    state = project / configuration["goals"][0]["state_file"]
    state.write_text(state.read_text().replace("active-read-only", "active").replace(
        "- Validate and settle the selected delivery.", "- Wait for the recorded source.",
    ))
    due = "2000-01-01T00:00:00Z" if scenario == "due_monitor" else "2035-01-01T00:00:00Z"
    state.write_text(state.read_text() +
        "\n- [ ] Observe synthetic upstream.\n"
        f"  <!-- loopx:todo todo_id=todo_retry_watch status=open claimed_by={cli.AGENT_ID} "
        "task_class=continuous_monitor action_kind=observe target_key=synthetic-upstream "
        f"watch_only=true cadence=30m next_due_at={quote(due, safe='')} "
        "result_hash=unchanged material_change=false material_change_generation=7 -->\n" + (
            "- [ ] Validate an independent result.\n"
            f"  <!-- loopx:todo todo_id=todo_retry_independent status=open claimed_by={cli.AGENT_ID} "
            "task_class=advancement_task action_kind=validate -->\n"
            if scenario in {"independent", "notice_independent", "earlier_independent"} else ""
        ))

    if scenario in {"notice_waiting", "notice_independent", "earlier_notice", "equal_notice", "earlier_gate", "earlier_independent"}:
        state.write_text(state.read_text() +
            "\n## User Todo\n\n- [ ] Review an independent draft.\n"
            f"  <!-- loopx:todo todo_id=todo_retry_notice status=open bound_agent={cli.AGENT_ID} "
            "task_class=user_action -->\n")

    def run(*args: str):
        return cli._run_cli(registry, runtime, *args, cwd=project)

    code, listed = run("todo", "list", "--goal-id", cli.GOAL_ID)
    assert code == 0, listed
    for todo in listed["todos"]:
        todo["claimed_by"] = cli.AGENT_ID
    initialize_canonical_authority(
        runtime, cli.GOAL_ID, build_todo_runtime_shadow_projection(
            goal_id=cli.GOAL_ID, todos=listed["todos"], handoff_mode="soft_claim", leases=[],
        ), state_path=state, provider=provider,
    )
    code, mapped = run("read-only-map", "--goal-id", cli.GOAL_ID)
    assert code == 0, mapped
    binding = ("--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
               "--todo-id", cli.TODO_ID, "--turn-instance-id", "turn-retry-original")
    if scenario != "no_retry":
        code, first = run("quota", "should-run", "--codex-app", *binding)
        assert code == 0 and first["should_run"], first
        code, blocked = run(
            "refresh-state", *binding, "--progress-scope", "agent_lane",
            "--classification", "unavailable_source", "--delivery-batch-scale", "single_surface",
            "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
            "--progress-blocker-id", "blocker:source", "--progress-evidence-id", "evidence:source",
            "--vision-todo-delta", f"retain:{cli.TODO_ID}",
            "--vision-summary", "The original boundary remains active.",
            "--no-global-sync", "--suppress-external-sinks",
        )
        assert code == 0, blocked
        assert blocked["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
        retry = blocked["blocked_retry"]
        if scenario in {"earlier_notice", "equal_notice", "earlier_gate", "earlier_independent"}:
            due = retry["due_at"] if scenario == "equal_notice" else (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
            code, updated = run("todo", "update", "--goal-id", cli.GOAL_ID,
                "--todo-id", "todo_retry_watch", "--agent-id", cli.AGENT_ID,
                "--next-due-at", due, "--execute")
            assert code == 0 and updated["ok"], updated
            if scenario == "earlier_gate":
                code, updated = run("todo", "update", "--goal-id", cli.GOAL_ID,
                    "--todo-id", "todo_retry_notice", "--agent-id", cli.AGENT_ID,
                    "--task-class", "user_gate", "--blocks-agent", cli.AGENT_ID, "--execute")
                assert code == 0 and updated["ok"], updated

    guards = []
    for _ in range(2):
        code, guard = run("quota", "should-run", "--codex-app",
                          "--goal-id", cli.GOAL_ID, "--agent-id", cli.AGENT_ID,
                          "--turn-instance-id", "turn-retry-next")
        assert code == 0 and guard["ok"], guard
        assert guard["heartbeat_receipt"]["status"] in {"committed", "replayed"}
        if scenario in {"waiting", "notice_waiting", "earlier_notice", "equal_notice"}:
            assert guard["effective_action"] == "monitor_quiet_skip"
            assert guard["execution_obligation"]["must_attempt_work"] is False
            if scenario in {"notice_waiting", "earlier_notice", "equal_notice"}:
                assert guard["execution_obligation"]["kind"] == "blocked_wait"
                assert guard["interaction_contract"]["user_channel"]["non_gating_notice"] is True
                assert guard["scheduler_hint"]["cadence_class"] == "quiet_wait"
            pending = guard["scheduler_hint"]["pending_retry"]
            assert pending["todo_id"] == cli.TODO_ID
            assert pending["due_at"] == retry["due_at"]
        elif scenario == "earlier_gate":
            assert guard["scheduler_hint"]["cadence_class"] == "human_gate"
            assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
            assert not guard["interaction_contract"]["user_channel"].get("non_gating_notice")
        else:
            assert guard["effective_action"] == "normal_run"
            if scenario in {"independent", "notice_independent", "earlier_independent"}:
                assert guard["selected_todo"]["todo_id"] == "todo_retry_independent"
        guards.append(guard)
    assert guards[0]["heartbeat_receipt"]["event_id"] == guards[1]["heartbeat_receipt"]["event_id"]
    code, after = run("todo", "list", "--goal-id", cli.GOAL_ID)
    assert code == 0, after
    original = next(todo for todo in after["todos"] if todo["todo_id"] == cli.TODO_ID)
    assert original["status"] == "open"
    assert not original.get("resume_when")
    watch = next(todo for todo in after["todos"] if todo["todo_id"] == "todo_retry_watch")
    assert watch["next_due_at"] == due
    assert watch["result_hash"] == "unchanged"
    assert watch["material_change_generation"] == 7
    assert cli._spend_run_count(runtime) == 0
