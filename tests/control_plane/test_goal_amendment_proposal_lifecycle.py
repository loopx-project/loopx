"""Producer-to-admission lifecycle e2e for governed goal amendment proposals.

Review round 6 asked for the real quota-producer-to-admission chain on an
isolated, healthy synthetic Goal instead of hand-written run rows and a
helper-computed expectation: production commands only, end to end.

The chain proven here:

1. Two production ``refresh-state`` calls record typed blocked progress
   observations (the exact ``--classification bounded_replan_progress``
   command the obligation's own recommended action prescribes).
2. A production ``quota should-run`` decision returns the open replan
   obligation — ``replan_action_packet.obligation_id`` — and that id is
   handed to the proposal verbatim, never recomputed by the test.
3. The proposal binds the real Stage 1 basis (``shared-goal-alignment``
   projection output; this fixture has no event log, so the real basis is
   the markdown sequence 0) and submits through the production CLI.
4. The legal settlement runs the quota-returned ``next_cli_actions``
   verbatim (refresh-state writeback + quota spend-slot) — no hand-written
   ACK row is ever appended.
5. After settlement, the settled obligation can no longer anchor a
   proposal: a new proposal naming it fails closed with nothing retained.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "amendment-lifecycle-fixture"
AGENT_ID = "codex-amendment-lifecycle"
TODO_ID = "todo_lifecycle_amendment_slice"
TURN_ID = "turn-amendment-lifecycle-1"


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    """One isolated, healthy synthetic Goal: active, one registered agent,
    one open Todo, an empty runtime (no event log, no run rows)."""

    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Settle one amendment lifecycle replan."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Amendment Lifecycle Fixture\n\n"
        "## Objective\n\n"
        "Settle one amendment lifecycle replan.\n\n"
        "## Next Action\n\n"
        "- Advance the bounded slice.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Advance the bounded amendment slice.\n"
        f"  <!-- loopx:todo todo_id={TODO_ID} status=open "
        "task_class=advancement_task action_kind=run "
        f"claimed_by={AGENT_ID} -->\n",
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "amendment-lifecycle-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                        "authority_sources": [],
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "allowed_slots": 2,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, runtime, registry_path


def _run_cli(
    registry_path: Path,
    runtime: Path,
    *args: str,
) -> tuple[int, dict[str, Any]]:
    """Run one production CLI invocation in a clean subprocess."""

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    return result.returncode, json.loads(result.stdout)


def _record_stall_observation(
    registry_path: Path,
    runtime: Path,
) -> dict[str, Any]:
    """The production stall command the obligation's own recommended action
    prescribes: a typed blocked progress observation on the agent lane."""

    rc, payload = _run_cli(
        registry_path,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--classification",
        "bounded_replan_progress",
        "--progress-scope",
        "agent_lane",
        "--agent-id",
        AGENT_ID,
        "--progress-result-class",
        "blocked",
        "--progress-surface-id",
        "surface-lifecycle",
        "--progress-hypothesis-id",
        "hypothesis-lifecycle",
        "--progress-probe-kind",
        "probe-lifecycle",
        "--progress-evidence-id",
        "evidence-lifecycle",
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert rc == 0, payload
    return payload


def _projected_cli_args(command: str) -> tuple[str, ...]:
    tokens = shlex.split(command)
    command_names = {"refresh-state", "quota"}
    command_index = next(
        index for index, token in enumerate(tokens) if token in command_names
    )
    return tuple(
        TURN_ID if token == "${LOOPX_TURN:?}" else token
        for token in tokens[command_index:]
    )


def test_production_quota_obligation_survives_the_full_amendment_lifecycle(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)

    # 1. Production stall observations: two equivalent typed blocked runs on
    #    the agent lane (nothing hand-written — refresh-state owns the rows).
    for _ in range(2):
        _record_stall_observation(registry_path, runtime)
    ledger = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    stall_rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(stall_rows) == 2
    assert {row["progress_observation"]["result_class"] for row in stall_rows} == {
        "blocked"
    }

    # 2. The quota producer returns the open obligation this history derives.
    guard_rc, guard = _run_cli(
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
        TURN_ID,
        "--scan-path",
        str(project),
    )
    assert guard_rc == 0, guard
    assert guard["decision"] == "autonomous_replan_required", guard
    obligation_id = guard["replan_action_packet"]["obligation_id"]
    assert obligation_id, guard
    settlement_identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert settlement_identity["turn_instance_id"] == TURN_ID
    assert settlement_identity["todo_id"] == TODO_ID

    # 3. The proposal binds the real Stage 1 basis (markdown goal, so the
    #    real sequence is 0) and the quota-returned obligation, verbatim.
    align_rc, alignment = _run_cli(
        registry_path,
        runtime,
        "shared-goal-alignment",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--project",
        str(project),
    )
    assert align_rc == 0, alignment
    basis = alignment["source_basis"]
    assert basis["revision_basis"] == "markdown_active_state"
    assert basis["state_event_basis_sequence"] == 0

    proposal = {
        "schema_version": "goal_amendment_proposal_v0",
        "proposal_id": "gap_lifecycle_001",
        "goal_id": GOAL_ID,
        "proposer_agent_id": AGENT_ID,
        "amendment_class": "lane_route",
        "base_revision_basis": basis["revision_basis"],
        "base_state_event_basis_sequence": basis["state_event_basis_sequence"],
        "base_source_basis_digest": basis["source_basis_digest"],
        "retained": ["the settled objective stays unchanged"],
        "changed": ["the lane reroutes around the blocked surface"],
        "stopped": [],
        "evidence_refs": ["evidence:lifecycle-stall"],
        "affected_todo_ids": [TODO_ID],
        "replan_obligation_id": obligation_id,
    }
    proposal_json = tmp_path / "proposal.json"
    proposal_json.write_text(json.dumps(proposal), encoding="utf-8")

    submit_rc, submit = _run_cli(
        registry_path,
        runtime,
        "goal-amendment-proposal",
        "--proposal-json",
        str(proposal_json),
        "--project",
        str(project),
    )
    assert submit_rc == 0, submit
    assert submit["ok"] is True
    assert submit["admission"] == "admitted"
    assert submit["admission_facts"] == ["base_source_basis_unverifiable"]
    assert submit["canonical_effect"] == "none"
    assert submit["replan_obligation_id"] == obligation_id

    list_rc, listed = _run_cli(
        registry_path,
        runtime,
        "goal-amendment-proposal",
        "--list",
        "--goal-id",
        GOAL_ID,
    )
    assert list_rc == 0, listed
    assert listed["count"] == 1
    assert listed["rows"][0]["proposal_id"] == "gap_lifecycle_001"
    assert listed["rows"][0]["replan_obligation_id"] == obligation_id

    # 4. Legal settlement: run the quota-returned next_cli_actions verbatim
    #    (refresh-state writeback with the typed semantic successor, then
    #    the quota spend) — the actual close flow, never a hand-written ACK.
    cli_channel = guard["interaction_contract"]["cli_channel"]
    refresh_command = next(
        action
        for action in cli_channel["next_cli_actions"]
        if "refresh-state" in action
    )
    spend_command = next(
        action for action in cli_channel["next_cli_actions"] if "spend-slot" in action
    )
    for command in (refresh_command, spend_command):
        assert f"--turn-instance-id {TURN_ID}" in command
        assert "--todo-id" in command or "--replan-obligation-id" in command

    refresh_command = (
        refresh_command.replace(
            "<advanced|blocked|exploration_exhausted|no_followup>",
            "advanced",
        )
        .replace("<surface-id>", "surface-lifecycle-successor")
        .replace("<hypothesis-id>", "hypothesis-lifecycle-successor")
        .replace("<probe-kind>", "probe-lifecycle-successor")
        .replace("<evidence-id>", "evidence-lifecycle-successor")
    )
    refresh_rc, refresh = _run_cli(
        registry_path,
        runtime,
        *_projected_cli_args(refresh_command),
        "--no-global-sync",
        "--suppress-external-sinks",
    )
    assert refresh_rc == 0, refresh
    assert refresh["settlement_result"]["ok"] is True, refresh

    spend_rc, spend = _run_cli(
        registry_path,
        runtime,
        *_projected_cli_args(spend_command),
        "--scan-path",
        str(project),
    )
    assert spend_rc == 0, spend
    assert spend["settlement_result"]["ok"] is True, spend

    # The ledger now carries the real close artifact: a refresh-state run
    # whose autonomous_replan_ack settles this exact obligation.
    settled_rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    acks = [
        row.get("autonomous_replan_ack")
        for row in settled_rows
        if row.get("autonomous_replan_ack")
    ]
    assert any(
        ack.get("semantic_delta", {}).get("obligation_id") == obligation_id
        and ack.get("semantic_delta", {}).get("accepted") is True
        for ack in acks
    ), settled_rows

    # 5. Quota confirms the obligation is gone, and the settled obligation
    #    can no longer anchor a proposal.
    settled_guard_rc, settled_guard = _run_cli(
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
        "turn-amendment-lifecycle-2",
        "--scan-path",
        str(project),
    )
    assert settled_guard_rc == 0, settled_guard
    assert settled_guard.get("replan_action_packet") is None, settled_guard

    stale_proposal = {
        **proposal,
        "proposal_id": "gap_lifecycle_002",
    }
    stale_json = tmp_path / "proposal-stale.json"
    stale_json.write_text(json.dumps(stale_proposal), encoding="utf-8")
    stale_rc, stale = _run_cli(
        registry_path,
        runtime,
        "goal-amendment-proposal",
        "--proposal-json",
        str(stale_json),
        "--project",
        str(project),
    )
    assert stale_rc == 1, stale
    assert stale["ok"] is False
    assert "does not match an open replan obligation" in stale["error"]

    final_rc, final = _run_cli(
        registry_path,
        runtime,
        "goal-amendment-proposal",
        "--list",
        "--goal-id",
        GOAL_ID,
    )
    assert final_rc == 0, final
    assert [row["proposal_id"] for row in final["rows"]] == ["gap_lifecycle_001"]
