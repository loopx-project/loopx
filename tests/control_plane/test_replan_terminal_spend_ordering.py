"""Regression coverage for issue #4501.

An autonomous replan whose accepted semantic outcome is a coverage-backed
``no_followup`` derives ``terminal_no_followup`` from its durable writeback.
The strict terminal guard must keep rejecting new and unbound spends, but it
must not reject the remaining quota spend of the very settlement that produced
the terminal frontier.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    _configure_autonomous_replan_fixture,
    _projected_cli_args,
    _run_cli,
    _spend_run_count,
    _write_fixture,
)

TURN_INSTANCE_ID = "turn-terminal-replan-1"

TERMINAL_STATE = "terminal_no_followup"


def _write_terminal_frontier_state(project: Path) -> None:
    """Close every Todo source so a coverage-backed replan resolves terminal."""

    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Settle one coverage-backed terminal replan."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Terminal Replan Settlement Fixture\n\n"
        "## Objective\n\n"
        "Settle one coverage-backed terminal replan.\n\n"
        "## Next Action\n\n"
        "- Close the covered frontier.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "- [x] [P1-monitor] Observe the stable public fixture.\n"
        "  <!-- loopx:todo todo_id=todo_replan_monitor status=done "
        "task_class=continuous_monitor action_kind=observe "
        f"claimed_by={AGENT_ID} target_key=replan-settlement-fixture "
        "cadence=1d next_due_at=2999-01-01T00%3A00%3A00Z "
        "no_followup=true evidence=covered -->\n",
        encoding="utf-8",
    )


def _coverage_backed_no_followup_refresh_args(
    command: str,
    *,
    vision_path: Path,
) -> tuple[str, ...]:
    """Fill the projected refresh template with a coverage-backed no_followup."""

    command = (
        command.replace(
            "<advanced|blocked|exploration_exhausted|no_followup>",
            "no_followup",
        )
        .replace("<surface-id>", "surface-terminal")
        .replace("<hypothesis-id>", "hypothesis-terminal")
        .replace("<probe-kind>", "probe-terminal")
        .replace("<evidence-id>", "evidence-terminal")
    )
    command += " --progress-coverage-scope-id coverage-terminal"
    command += " --progress-coverage-complete"
    command += f" --agent-vision-json {shlex.quote(str(vision_path))}"
    return _projected_cli_args(command, turn_instance_id=TURN_INSTANCE_ID)


def _write_no_followup_vision(root: Path) -> Path:
    vision = {
        "schema_version": "goal_vision_replan_contract_v0",
        "agent_id": AGENT_ID,
        "state": "no_followup",
        "vision_patch": {
            "acceptance_summary": "The covered frontier is complete.",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0",
            "outcome": "stop",
            "prior_assumption": "The frontier still needs another probe.",
            "observed_reality": "Coverage is complete and no successor exists.",
            "retained": ["bounded coverage read model"],
            "changed": ["frontier resolution"],
            "stopped": ["repeat probe of the covered frontier"],
            "evidence_refs": ["evidence-terminal"],
        },
    }
    vision_path = root / "coverage-backed-no-followup-vision.json"
    vision_path.write_text(json.dumps(vision), encoding="utf-8")
    return vision_path


def _should_run(
    registry_path: Path,
    runtime: Path,
    project: Path,
) -> dict[str, Any]:
    rc, payload = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--turn-instance-id",
        TURN_INSTANCE_ID,
    )
    assert rc == 0, payload
    return payload


def _dry_run_spend_preview(
    registry_path: Path,
    runtime: Path,
    project: Path,
    *,
    spend_command: str,
) -> dict[str, Any]:
    """Read the decision state the bound spend would settle from.

    Dropping ``--execute`` keeps the read non-mutating, so the terminal state
    the writeback produced can be observed without consuming the slot.
    """

    args = [
        arg
        for arg in _projected_cli_args(
            spend_command,
            turn_instance_id=TURN_INSTANCE_ID,
        )
        if arg != "--execute"
    ]
    rc, preview = _run_cli(
        registry_path,
        runtime,
        *args,
    )
    assert rc == 0, preview
    return preview


def test_coverage_backed_no_followup_replan_does_not_strand_its_quota_spend(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_autonomous_replan_fixture(project, runtime, registry_path)
    _write_terminal_frontier_state(project)
    vision_path = _write_no_followup_vision(tmp_path)

    guard = _should_run(registry_path, runtime, project)
    assert guard["decision"] == "autonomous_replan_required", guard
    obligation_id = guard["replan_action_packet"]["obligation_id"]
    assert guard["heartbeat_receipt"]["settlement_identity"]["binding_kind"] == (
        "autonomous_replan"
    )
    actions = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    refresh_command = next(action for action in actions if "refresh-state" in action)
    spend_command = next(action for action in actions if "spend-slot" in action)
    for command in (refresh_command, spend_command):
        assert f"--replan-obligation-id {obligation_id}" in command
        assert f"--turn-instance-id {TURN_INSTANCE_ID}" in command
        assert "--todo-id" not in command

    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        *_coverage_backed_no_followup_refresh_args(
            refresh_command,
            vision_path=vision_path,
        ),
    )
    assert refresh_rc == 0, refresh
    assert refresh["settlement_result"]["ok"] is True
    assert [
        receipt["step_kind"] for receipt in refresh["settlement_result"]["receipts"]
    ] == ["validation", "durable_writeback"]

    # The writeback makes the Goal terminal before the spend of the same
    # settlement has been recorded. That is the #4501 ordering window.
    preview = _dry_run_spend_preview(
        registry_path,
        runtime,
        project,
        spend_command=spend_command,
    )
    assert preview["before"]["state"] == TERMINAL_STATE, preview
    assert preview["before"]["effective_action"] == TERMINAL_STATE, preview

    spend_args = _projected_cli_args(
        spend_command,
        turn_instance_id=TURN_INSTANCE_ID,
    )
    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        *spend_args,
    )
    assert spend_rc == 0, spend
    assert spend["settlement_result"]["ok"] is True
    assert spend["appended"] is True
    receipts = spend["settlement_result"]["receipts"]
    assert [receipt["step_kind"] for receipt in receipts] == [
        "validation",
        "durable_writeback",
        "quota_spend",
    ]
    effect_ids = {receipt["effect_id"] for receipt in receipts}
    assert len(effect_ids) == 1
    assert obligation_id in next(iter(effect_ids))

    replay_rc, replay = _run_cli(
        registry_path,
        runtime,
        *spend_args,
    )
    assert replay_rc == 0, replay
    assert replay["idempotent_replay"] is True
    assert replay["appended"] is False
    assert _spend_run_count(runtime) == 1


def test_unbound_terminal_spend_stays_rejected(tmp_path: Path) -> None:
    """The terminal guard keeps rejecting a spend that owns no settlement."""

    project, runtime, registry_path = _write_fixture(tmp_path)
    _configure_autonomous_replan_fixture(project, runtime, registry_path)
    _write_terminal_frontier_state(project)
    vision_path = _write_no_followup_vision(tmp_path)

    guard = _should_run(registry_path, runtime, project)
    actions = guard["interaction_contract"]["cli_channel"]["next_cli_actions"]
    refresh_command = next(action for action in actions if "refresh-state" in action)
    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        *_coverage_backed_no_followup_refresh_args(
            refresh_command,
            vision_path=vision_path,
        ),
    )
    assert refresh_rc == 0, refresh

    rc, spend = _run_cli(
        registry_path,
        runtime,
        "quota",
        "spend-slot",
        "--goal-id",
        GOAL_ID,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
    )
    assert spend["ok"] is False
    assert spend["appended"] is False
    # The Goal is terminal; the guard still rejects a spend that owns no
    # settlement binding of its own.
    assert spend["before"]["state"] == TERMINAL_STATE, spend
    assert _spend_run_count(runtime) == 0
    assert rc in {0, 1}
