"""Quota's deferred-resume command must consume the existing narrow lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
import shlex

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.cli import main as cli_main
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.todos.todo_summary import (
    canonical_todo_read_record,
    structured_todo_item,
)
from test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    _spend_run_count,
    _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fallback", [False, True])
def test_generated_deferred_resume_preserves_execution_lease_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    fallback: bool,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    peer = "registered-peer"
    registry_data = json.loads(registry.read_text())
    registry_data["goals"][0]["coordination"]["registered_agents"].append(peer)
    registry_data["goals"][0]["spawn_policy"] = {
        "mode": "multi_subagent", "allowed": True, "max_children": 2,
    }
    registry.write_text(json.dumps(registry_data))
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    text = (
        state.read_text()
        .replace("- [ ] [P1]", "- [-] [P1]")
        .replace(
            "status=open task_class=advancement_task action_kind=validate",
            "status=deferred task_class=advancement_task action_kind=validate "
            f"claimed_by={AGENT_ID} resume_when=resume_at:2000-01-01T00:00:00Z "
            "continuation_policy=same_agent_non_delivery required_capabilities=shell%2Cfilesystem_read",
        )
    )
    if fallback:
        text += (
            "\n- [ ] [P2] Validate independent retained work.\n"
            f"  <!-- loopx:todo todo_id=todo_fallback status=open claimed_by={AGENT_ID} "
            "task_class=advancement_task action_kind=validate "
            "continuation_policy=same_agent_non_delivery "
            "required_capabilities=shell%2Cfilesystem_read -->\n"
        )
    state.write_text(text)
    todos = parse_active_state_todos(text, item_limit=None)["agent_todos"]["items"]
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=[
                canonical_todo_read_record(
                    structured_todo_item(
                        row,
                        role="agent",
                        source_section="Agent Todo",
                    )
                )
                for row in todos
            ],
            handoff_mode="hard_lease",
            leases=[],
        ),
        state_path=state,
        provider=provider,
    )

    def cli(*args: str) -> tuple[int, dict]:
        with monkeypatch.context() as context:
            context.chdir(project)
            rc = cli_main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(runtime),
                    "--format",
                    "json",
                    *args,
                ]
            )
            return rc, json.loads(capsys.readouterr().out)

    rc, guard = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "resume-command-turn",
        "--scan-path",
        str(project),
    )
    assert rc == 0 and guard["effective_action"] == "successor_replan_required", guard
    assert guard["selected_todo"]["todo_id"] == TODO_ID
    generated = guard["interaction_contract"]["cli_channel"]["next_cli_actions"][0]
    assert generated.startswith("loopx ")
    resume = shlex.split(
        generated.replace(
            "<public-safe successor replan reason>",
            "Original gate is satisfied; resume unchanged work before acquiring a fresh lease.",
        )
    )[1:]
    # The fixture's global runtime options are already passed by cli().
    if "--runtime-root" in resume:
        index = resume.index("--runtime-root")
        del resume[index : index + 2]
    before = state.read_bytes()

    rc, premature = cli(
        "task-lease",
        "acquire",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--owner",
        AGENT_ID,
        "--idempotency-key",
        "fresh-after-resume",
    )
    assert rc == 1 and premature["error_code"] == "todo_not_open", premature
    for extra, actor in (
        (["--note", "This is an execution edit."], AGENT_ID),
        ([], peer),
        (
            [
                "--task-lease-idempotency-key",
                "old-proof",
                "--task-lease-expected-version",
                "1",
            ],
            AGENT_ID,
        ),
    ):
        narrow = [
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            TODO_ID,
            "--agent-id",
            actor,
            "--status",
            "open",
            "--clear-resume-when",
            "--reason",
            "Resume original work.",
            *extra,
        ]
        rc, rejected = cli(*narrow)
        assert rc == 1 and rejected["ok"] is False, rejected
        assert state.read_bytes() == before

    # Execute the actual quota output; the old --note template fails here.
    rc, resumed = cli(*resume)
    assert rc == 0 and resumed["status"] == "applied", json.dumps(resumed, indent=2)
    assert "--reason" in resume and "--note" not in resume
    transition = resumed["deferred_resume_transition"]
    assert transition["execution_authority_granted"] is False
    assert transition["next_execution"] == "acquire_fresh_lease"
    rc, absent = cli(
        "task-lease", "inspect", "--goal-id", GOAL_ID, "--todo-id", TODO_ID
    )
    assert rc == 0 and absent["active"] is False, absent
    rc, acquired = cli(
        "task-lease",
        "acquire",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        TODO_ID,
        "--owner",
        AGENT_ID,
        "--idempotency-key",
        "fresh-after-resume",
    )
    assert rc == 0 and acquired["lease"]["status"] == "active", acquired
    rc, reentry = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        "resume-command-turn",
        "--todo-id",
        TODO_ID,
        "--scan-path",
        str(project),
    )
    assert rc == 0 and reentry["normal_delivery_allowed"], reentry
    assert (
        reentry["heartbeat_receipt"]["settlement_identity"]
        == guard["heartbeat_receipt"]["settlement_identity"]
    )
    assert reentry["heartbeat_receipt"]["status"] == "upgraded"
    assert reentry["heartbeat_receipt"]["event_id"] != guard["heartbeat_receipt"]["event_id"]
    log_path = rollout_event_log_path(runtime, GOAL_ID)
    events = load_rollout_events(log_path)
    original = next(row for row in events if row["event_id"] == guard["heartbeat_receipt"]["event_id"])
    assert original["details"]["delivery_allowed"] is False
    qualified = next(row for row in events if row["event_id"] == reentry["heartbeat_receipt"]["event_id"])
    assert qualified["causality"]["source_event_id"] == original["event_id"]
    assert qualified["details"]["delivery_allowed"] is True
    rc, cold = cli("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", "resume-command-turn",
        "--todo-id", TODO_ID, "--scan-path", str(project))
    assert rc == 0 and cold["heartbeat_receipt"]["status"] == "replayed", cold
    assert cold["heartbeat_receipt"]["event_id"] == qualified["event_id"]
    assert load_rollout_events(log_path) == events
    child = ("native-child", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--turn-instance-id", "resume-command-turn")
    rc, decision = cli(*child, "record", "--operation-id", "resumed-audit",
        "--stage", "decision", "--operation", "followup", "--outcome", "started",
        "--entrypoint-id", "generic_host", "--execute")
    assert rc == 0 and decision["appended"], decision
    rc, replay = cli(*child, "record", "--operation-id", "resumed-audit",
        "--stage", "decision", "--operation", "followup", "--outcome", "started",
        "--entrypoint-id", "generic_host", "--execute")
    assert rc == 0 and not replay["appended"]
    rc, result = cli(*child, "record", "--operation-id", "resumed-audit",
        "--stage", "result", "--outcome", "completed", "--execute")
    assert rc == 0 and result["appended"], result
    rc, reviewed = cli(*child, "record", "--operation-id", "resumed-audit",
        "--stage", "review", "--outcome", "accepted", "--evidence-ref", "synthetic-audit",
        "--validation-ref", "synthetic-validation", "--execute")
    assert rc == 0 and reviewed["native_child_activity"]["parent_accepted_count"] == 1
    assert _spend_run_count(runtime) == 0
