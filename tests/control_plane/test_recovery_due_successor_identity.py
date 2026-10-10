"""A new due successor cannot acquire an interrupted Turn's settlement identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.todo_summary import (
    canonical_todo_read_record,
    structured_todo_item,
)
from test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    _configure_read_only_todo,
    _spend_run_count,
    _write_fixture,
)


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("crowded", [False, True])
def test_interrupted_binding_survives_new_due_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    provider: str,
    crowded: bool,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    peer_agent = "codex-peer"
    registry_payload = json.loads(registry.read_text())
    registry_payload["goals"][0]["coordination"]["registered_agents"].append(peer_agent)
    registry.write_text(json.dumps(registry_payload))
    state = _configure_read_only_todo(project)
    state.write_text(
        state.read_text().replace(
            "action_kind=validate ",
            f"action_kind=validate claimed_by={AGENT_ID} ",
        )
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

    rc, vision = cli(
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--classification",
        "fixture_vision",
        "--delivery-outcome",
        "surface_only",
        "--vision-summary",
        "Preserve identities and evaluate fixed deadlines.",
        "--vision-acceptance",
        "Exact binding, legal recovery and independently runnable due work.",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert rc == 0, json.dumps(vision, indent=2)

    if crowded:
        state.write_text(
            state.read_text()
            + "".join(
                f"\n- [x] [P4] Retained completed task {index}.\n"
                f"  <!-- loopx:todo todo_id=todo_retained_{index:03d} status=done "
                f"task_class=advancement_task action_kind=validate claimed_by={AGENT_ID} "
                "continuation_policy=same_agent_non_delivery "
                "required_capabilities=shell%2Cfilesystem_read -->\n"
                for index in range(80)
            )
        )

    if provider != "legacy":
        rc, listed = cli("todo", "list", "--goal-id", GOAL_ID, "--limit", "200")
        assert rc == 0, listed
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
                    for row in listed["todos"]
                ],
                handoff_mode="hard_lease",
                leases=[],
            ),
            state_path=state,
            provider=provider,
        )

    old_turn, new_turn = "interrupted-original", "current-after-interruption"
    old_binding = (
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        old_turn,
    )
    rc, admitted = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *old_binding,
        "--scan-path",
        str(project),
    )
    assert rc == 0 and admitted["normal_delivery_allowed"], json.dumps(
        admitted, indent=2
    )
    identity = admitted["heartbeat_receipt"]["settlement_identity"]

    # This timer did not exist when the original Turn was admitted.
    rc, added = cli(
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        *(("--operation-id", "add-new-due-successor") if provider != "legacy" else ()),
        "--text",
        "[P0] Evaluate the fixed deadline once runnable.",
        "--priority",
        "P0",
        "--status",
        "deferred",
        "--task-class",
        "advancement_task",
        "--claimed-by",
        AGENT_ID,
        "--resume-when",
        "resume_at:2000-01-01T00:00:00Z",
    )
    assert rc == 0, json.dumps(added, indent=2)
    due_id = added["todo_id"]
    rc, recovering = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        new_turn,
        "--scan-path",
        str(project),
    )
    assert (
        rc == 0 and recovering["effective_action"] == "unsettled_host_turn_recovery"
    ), recovering
    assert recovering["unsettled_host_turn_recovery"]["binding_id"] == TODO_ID

    rc, reentry = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        *old_binding,
        "--scan-path",
        str(project),
    )
    assert rc == 0, reentry
    assert reentry["heartbeat_receipt"]["settlement_identity"] == identity
    assert reentry["selected_todo"]["todo_id"] == TODO_ID
    assert (
        reentry["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
        == identity
    )
    assert reentry["effective_action"] != "successor_replan_required"
    assert _spend_run_count(runtime) == 0

    # Keeping the original binding does not grant a peer its owner's Todo.
    rc, peer = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        peer_agent,
        "--turn-instance-id",
        old_turn,
        "--todo-id",
        TODO_ID,
        "--scan-path",
        str(project),
    )
    assert rc == 1, peer
    assert peer["error_code"] == "quota_action_selection_rejected"
    assert peer["heartbeat_receipt"]["status"] == "not_committed"
    assert _spend_run_count(runtime) == 0

    if provider == "legacy":
        # A legacy writer has no promoted Turn-owned retry closeout.
        return
    before = state.read_bytes()
    refresh_args = (
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        *old_binding,
        "--classification",
        "local_stop_closeout",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_gap",
        "--progress-result-class",
        "blocked",
        "--progress-blocker-id",
        "blocker:local-stop",
        "--progress-evidence-id",
        "evidence:original-local-stop",
        "--vision-unchanged-reason",
        "Original acceptance remains unproven.",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    for replay in (False, True):
        rc, closed = cli(*refresh_args)
        assert rc == 0, closed
        assert closed["settlement_progress"]["state"] == "settled"
        assert closed["settlement_identity"] == identity
        assert {r["effect_id"] for r in closed["settlement_result"]["receipts"]} == {
            identity["effect_id"]
        }
        assert (
            closed["settlement_progress"]["closeout_kind"]
            == "typed_blocked_writeback_no_spend"
        )
        assert closed["blocked_retry"]["source"] == "turn_settlement"
        if replay:
            assert closed["idempotent_replay"] is True
    assert _spend_run_count(runtime) == 0
    assert state.read_bytes() == before
    rc, fresh = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        new_turn,
        "--scan-path",
        str(project),
    )
    assert rc == 0, fresh
    assert fresh["effective_action"] != "unsettled_host_turn_recovery"
    assert (
        due_id
        in fresh["goal_frontier_projection"]["deferred_successors"]["ready_todo_ids"]
    )
    # Consume the due declaration through the existing deferred lifecycle;
    # doing so must not settle or rebind the original Turn.
    rc, resumed = cli(
        "todo",
        "update",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        due_id,
        "--agent-id",
        AGENT_ID,
        "--status",
        "open",
        "--clear-resume-when",
    )
    assert rc == 0, json.dumps(resumed, indent=2)
    rc, selected = cli(
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        new_turn,
        "--todo-id",
        due_id,
        "--scan-path",
        str(project),
    )
    assert rc == 0, selected
    new_identity = selected["heartbeat_receipt"]["settlement_identity"]
    assert (
        new_identity["todo_id"] == due_id
        and new_identity["turn_instance_id"] == new_turn
    )
    assert (
        selected["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
        == new_identity
    )
    assert _spend_run_count(runtime) == 0
