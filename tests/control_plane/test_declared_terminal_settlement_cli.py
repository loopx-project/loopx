"""A declared, leased deliverable can settle without inventing a successor."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import test_quota_settlement_cli as cli
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.control_plane.todos.markdown import render_todo_markdown


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, *, historical_terminals: int = 0):
    # Scope managed Effect discovery as well as provider data for both arms.
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry_path = cli._write_fixture(tmp_path)
    state = cli._configure_repository_write_todo(project)
    state.write_text(state.read_text().replace(
        "required_capabilities=filesystem_write", "required_capabilities=shell%2Cfilesystem_write "
        f"claimed_by={cli.AGENT_ID} required_write_scopes=src/**",
    ))
    # Initial synthetic authority, never edits to an admitted or active Goal.
    # These same-Agent terminal rows are absent from the later admission source.
    state.write_text(state.read_text() + "".join(
        f"\n- [x] Previously accepted source {index}.\n"
        f"  <!-- loopx:todo todo_id=todo_historical_{index} status=done "
        f"task_class=advancement_task claimed_by={cli.AGENT_ID} "
        f"last_actor_agent_id={cli.AGENT_ID} no_followup=true "
        "completion_continuation=no_followup "
        "completion_recovery=lifecycle_reentry_terminal_closeout "
        f"completion_turn_key=historical-completion-{index} "
        "completed_at=2001-01-01T00%3A00%3A00Z "
        "updated_at=2001-01-01T00%3A00%3A01Z -->\n"
        for index in range(historical_terminals)
    ))
    registry = json.loads(registry_path.read_text())
    registry["goals"][0]["coordination"]["write_scope"] = ["src/**"]
    registry_path.write_text(json.dumps(registry))
    cli._initialize_git_checkout(project)
    # The private registry must not be tracked or made push-allowed just to
    # admit the synthetic Goal. Only the public-safe state fixture is committed.
    subprocess.run(["git", "add", ".codex"], cwd=project, check=True, capture_output=True)
    subprocess.run([
        "git", "-c", "user.name=LoopX Test", "-c", "user.email=loopx-test@example.invalid",
        "commit", "--quiet", "-m", "settlement fixture",
    ], cwd=project, check=True, capture_output=True)
    workspace = tmp_path / "independent-worktree"
    subprocess.run(["git", "worktree", "add", "--quiet", "--detach", str(workspace)],
                   cwd=project, check=True, capture_output=True)

    def run(*args: str) -> tuple[int, dict[str, Any]]:
        return cli._run_cli(registry_path, runtime, *args, cwd=workspace)

    projection = build_todo_runtime_shadow_projection(
        goal_id=cli.GOAL_ID, todos=read_fixture_todos(state), handoff_mode="hard_lease", leases=[],
    )
    # Actor audit is canonical-only; legacy Markdown import cannot author it.
    historical = [row for row in projection["todos"] if row["todo_id"].startswith("todo_historical_")]
    assert len(historical) == historical_terminals
    for row in historical:
        row["last_actor_agent_id"] = cli.AGENT_ID
    projection["todo_read_model"]["records_sha256"] = sha256(canonical_bytes(projection["todos"])).hexdigest()
    initialize_canonical_authority(
        runtime, cli.GOAL_ID, projection, state_path=state, provider=provider,
    )
    code, acquired = run(
        "task-lease", "acquire", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
        "--owner", cli.AGENT_ID, "--idempotency-key", "declared-terminal-fixture",
        "--expected-version", "0", "--ttl-seconds", "600", "--write-scope", "src/**",
    )
    assert code == 0, acquired
    lease_args = ("--task-lease-idempotency-key", acquired["lease"]["idempotency_key"],
                  "--task-lease-expected-version", str(acquired["lease"]["version"]))
    marker, fail = tmp_path / "validator-runs", tmp_path / "validator-fails"
    argv = [sys.executable, "-c", (
        "from pathlib import Path; "
        f"p=Path({str(marker)!r}); p.write_text((p.read_text() if p.exists() else '')+'run\\n'); "
        f"raise SystemExit(4 if Path({str(fail)!r}).exists() else 0)"
    )]
    code, bound = run(
        "todo", "update", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
        "--agent-id", cli.AGENT_ID, "--validation-command-json", json.dumps(argv),
        "--validation-label", "Original declared deliverable check", *lease_args,
        "--update-operation-id", "original-declaration-fixture",
        "--update-expected-provider-revision", acquired["provider_revision"],
    )
    assert code == 0, bound.get("error") or bound
    code, original = run("todo", "list", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)
    assert code == 0, original
    return project, workspace, runtime, registry_path, run, lease_args, marker, fail, original["todo"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("closeout_source", ["turn", "lifecycle"])
def test_declared_leased_completion_writeback_spend_terminal_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, closeout_source: str,
) -> None:
    project, workspace, runtime, registry, run, lease, marker, _, original = _fixture(
        tmp_path, monkeypatch, provider,
    )
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
               "--turn-instance-id", cli.TURN_ID)
    guard_args = ("quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
                   *binding,
                  "--available-capability", "shell", "--available-capability", "filesystem_write")
    code, guard = run(*guard_args)
    assert code == 0, guard.get("error") or guard.get("reason") or guard
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == cli.TODO_ID
    complete_args = ("todo", "complete", "--goal-id", cli.GOAL_ID, *binding, *lease,
                     "--project", str(project), "--state-file", str(project / f".codex/goals/{cli.GOAL_ID}/ACTIVE_GOAL_STATE.md"),
                     "--evidence", "The original declared deliverable check passed.")
    code, premature = run(*complete_args, "--no-follow-up")
    assert code == 1, premature
    assert premature["settlement_blocked_completion"] is True
    assert not marker.exists()
    assert cli._spend_run_count(runtime) == 0
    projected = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert projected["ordered_steps"][0]["command_condition"] == "todo_deliverable_complete"
    recovery = premature["settlement_plan"]
    assert recovery["identity"] == premature["settlement_identity"]
    completion_command = recovery["ordered_steps"][0]["command_template"]
    assert "--project" in recovery["ordered_steps"][1]["command_template"]
    assert "--state-file" in recovery["ordered_steps"][1]["command_template"]
    assert "--project" not in recovery["ordered_steps"][2]["command_template"]
    assert "--no-follow-up" not in shlex.split(completion_command)
    assert completion_command in render_todo_markdown(premature)
    completion_command = completion_command.replace("<validated evidence>", complete_args[-1])

    # Execute the returned route/binding/lease argv, not an independently fixed
    # recipe. Replacing the evidence placeholder does not grant execution.
    code, completed = run(*shlex.split(completion_command)[1:])
    assert code == 0, completed
    assert completed["status"] == "done"
    assert completed["completion_continuation"] == "active_goal"
    assert completed["validation_receipt"]["passed"] is True
    assert completed["validation_receipt"]["validation_declaration_sha256"] == original["completion_validation_sha256"]
    assert marker.read_text() == "run\n"
    assert not completed.get("successor_todo_ids")
    assert cli._spend_run_count(runtime) == 0
    # Ordinary completion leaves a genuine lineage gap. Shared read guidance
    # must not convert that diagnostic into mandatory terminal mutation.
    code, ordinary = run("todo", "list", "--goal-id", cli.GOAL_ID)
    assert code == 0, ordinary
    warning = ordinary["agent_todos"]["todo_succession_warning"]
    assert warning["count"] == 1
    assert "ordinary Todo completion needs no artificial successor" in warning["recommended_action"]
    assert "terminal_closure_proof" not in ordinary["agent_todos"]
    code, early_reentry = run("todo", "complete", "--goal-id", cli.GOAL_ID,
        "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
        "--completion-identity-key", completed["completion_identity_key"],
        "--no-follow-up")
    assert code == 1, early_reentry
    code, continuing_guard = run(*guard_args)
    assert code == 0, continuing_guard
    assert continuing_guard["agent_todo_summary"]["todo_succession_warning"]["recommended_action"] == warning["recommended_action"]
    code, unchanged = run("todo", "list", "--goal-id", cli.GOAL_ID)
    assert code == 0, unchanged
    assert unchanged["todos"] == ordinary["todos"]
    assert marker.read_text() == "run\n"
    assert cli._spend_run_count(runtime) == 0
    readback = read_heartbeat_settlement(runtime, goal_id=cli.GOAL_ID, agent_id=cli.AGENT_ID,
                                       todo_id=cli.TODO_ID, turn_instance_id=cli.TURN_ID)
    assert readback is not None
    assert readback.settlement.failure is not None

    refresh_args = (
        "refresh-state", "--goal-id", cli.GOAL_ID, *binding,
        "--classification", "declared_deliverable_complete", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--vision-state", "vision_closed",
        "--vision-summary", "The bounded declared deliverable has passed its original check.",
        "--vision-acceptance", "The original validation, writeback and accounting receipts are verified.",
        "--no-global-sync", "--suppress-external-sinks",
    )
    code, refreshed = run(*refresh_args)
    assert code == 0, refreshed
    assert refreshed["delivery_workspace"]["workspace_kind"] == "independent_git_worktree"
    assert refreshed["delivery_workspace"]["task_repository"] == original["task_repository"]
    spend_args = ("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding,
                  "--slots", "1", "--source", "heartbeat", "--execute", )
    code, spent = run(*spend_args)
    assert code == 0, spent
    assert spent["appended"] is True
    terminal_args = (*complete_args, "--no-follow-up") if closeout_source == "turn" else (
        "todo", "complete", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
        "--agent-id", cli.AGENT_ID, "--completion-identity-key", completed["completion_identity_key"],
        "--no-follow-up")
    code, terminal = run(*terminal_args)
    assert code == 0, terminal.get("error") or terminal.get("reason") or terminal
    assert terminal["completion_continuation"] == "no_followup"
    assert terminal["completion_recovery"] == (
        "same_turn_terminal_closeout" if closeout_source == "turn" else "lifecycle_reentry_terminal_closeout")
    assert terminal["changed"] is True
    assert terminal["idempotent_replay"] is False
    if closeout_source == "turn":
        assert [r["step_kind"] for r in terminal["settlement_result"]["receipts"]] == [
            "validation", "durable_writeback", "quota_spend", "terminal_closeout",
        ]
    for args in (complete_args, refresh_args, spend_args, terminal_args):
        code, replay = run(*args)
        assert code == 0, replay
        assert replay["idempotent_replay"] is True
    assert marker.read_text() == "run\n"
    assert cli._spend_run_count(runtime) == 1
    code, listed = run("todo", "list", "--goal-id", cli.GOAL_ID)
    assert code == 0, listed
    assert len(listed["todos"]) == 1
    assert listed["todos"][0]["completion_validation_sha256"] == original["completion_validation_sha256"]
    code, settled = run(*guard_args)
    assert code == 0, settled
    assert settled["should_run"] is False
    assert settled["effective_action"] == "heartbeat_settled_skip"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_lease_and_failed_validator_remain_required_before_accounting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    project, _, runtime, _, run, lease, marker, fail, original = _fixture(tmp_path, monkeypatch, provider)
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
               "--turn-instance-id", cli.TURN_ID)
    code, guard = run("quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
                       *binding)
    assert code == 0, guard.get("error") or guard.get("reason")
    command = ("todo", "complete", "--goal-id", cli.GOAL_ID, *binding,
               "--evidence", "Only the original controller check can accept this deliverable.")
    code, invalid_lease = run(*command, *lease[:2], "--task-lease-expected-version", "999")
    assert code == 1, invalid_lease
    assert not marker.exists(), "lease rejection precedes effects"
    fail.touch()
    code, rejected = run(*command, *lease)
    assert code == 1, rejected
    assert rejected["validation_blocked_completion"] is True
    assert marker.read_text() == "run\n"
    code, state = run("todo", "list", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)
    assert code == 0, state
    assert state["todo"]["status"] == "open"
    assert state["todo"]["completion_validation_sha256"] == original["completion_validation_sha256"]
    code, no_spend = run("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding,
                         "--source", "heartbeat", "--slots", "1", "--execute", )
    assert code == 1, no_spend
    assert cli._spend_run_count(runtime) == 0
    fail.unlink()
    code, accepted = run(*command, *lease)
    assert code == 0, accepted
    assert accepted["validation_receipt"]["passed"] is True
    assert accepted["validation_receipt"]["validation_declaration_sha256"] == original["completion_validation_sha256"]
    assert marker.read_text() == "run\nrun\n", "one failed attempt, then one actual successful check"
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("historical_terminals", [0, 1, 4])
def test_no_followup_transition_recovers_bound_review_through_writeback_and_single_debit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, historical_terminals: int,
) -> None:
    project, _, runtime, _, run, lease, marker, _, _ = _fixture(
        tmp_path, monkeypatch, provider, historical_terminals=historical_terminals,
    )
    def call(*args: str, expected: int = 0):
        code, payload = run(*args)
        assert code == expected, json.dumps(payload, ensure_ascii=False, indent=2)
        return payload
    binding = ("--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
               "--turn-instance-id", cli.TURN_ID)
    capabilities = ("--available-capability", "shell", "--available-capability", "filesystem_write")
    call("quota", "should-run", "--codex-app", "--goal-id", cli.GOAL_ID,
          *binding, *capabilities)
    done = call("todo", "complete", "--goal-id", cli.GOAL_ID, *binding, *lease,
                "--evidence", "Original declared source check passed.")
    before = call("todo", "list", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)["todo"]
    call("refresh-state", "--goal-id", cli.GOAL_ID, *binding,
         "--classification", "declared_deliverable_complete", "--delivery-batch-scale", "implementation",
         "--delivery-outcome", "outcome_progress", "--vision-state", "vision_closed",
         "--vision-summary", "The original source deliverable passed.",
         "--vision-acceptance", "Its original check, writeback and accounting are independently verified.",
         "--no-global-sync", "--suppress-external-sinks")
    call("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *binding,
         "--slots", "1", "--source", "heartbeat", "--execute", )
    turn = "turn-original-lifecycle-review"
    guard = ("quota", "should-run", "--runtime-profile", "generic_cli", "--goal-id", cli.GOAL_ID,
             "--agent-id", cli.AGENT_ID, "--turn-instance-id", turn,  *capabilities)
    admitted = call(*guard)
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    assert admitted["autonomous_replan_obligation"]["triggers"][0]["todo_id"] == cli.TODO_ID
    assert len(admitted["autonomous_replan_obligation"]["triggers"]) == 1
    call("todo", "complete", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
         "--agent-id", cli.AGENT_ID, "--completion-identity-key", done["completion_identity_key"],
         "--no-follow-up")
    recovered = call(*guard)
    assert recovered["heartbeat_receipt"]["settlement_identity"] == identity
    assert recovered.get("selected_todo") is None
    assert recovered["replan_action_packet"]["settlement_only"] is True
    assert recovered["replan_action_packet"]["writeback_contract"]["preferred_input"] == "evidence_linked_vision_path"
    replan_binding = ("--agent-id", cli.AGENT_ID, "--replan-obligation-id", identity["replan_obligation_id"],
                      "--turn-instance-id", turn)
    spend = ("quota", "spend-slot", "--goal-id", cli.GOAL_ID, *replan_binding,
             "--slots", "1", "--source", "heartbeat", "--execute", )
    assert call(*spend, expected=1).get("appended") is not True
    # A no-follow-up transition alone must not waive the acceptance checkpoint.
    refresh = ("refresh-state", "--goal-id", cli.GOAL_ID, *replan_binding,
               "--classification", "lifecycle_review", "--delivery-batch-scale", "implementation",
               "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks")
    refused = call(*refresh, expected=1)
    assert refused["ok"] is False
    assert "vision" in str(refused).lower()
    vision = {"state": "vision_closed", "summary": "Original outcome independently checked.",
              "vision_patch": {"acceptance_summary": "Original source and declared check remain valid."},
              "path_delta": {"outcome": "no_change", "prior_assumption": "Original source acceptance requires validation.",
                             "observed_reality": "The declared validator and original settlement passed.",
                             "retained": ["The independently validated source outcome"],
                             "evidence_refs": ["original-source-validation"]}}
    vision_file = tmp_path / "current-acceptance.json"
    vision_file.write_text(json.dumps(vision))
    refreshed = call(*refresh, "--agent-vision-json", str(vision_file))
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    pending = call(*guard)
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    assert pending["settlement_progress"]["state"] == "spend_required"
    assert call(*spend)["appended"] is True
    assert call(*spend)["appended"] is False
    assert call(*guard)["effective_action"] == "heartbeat_settled_skip"
    after = call("todo", "list", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)["todo"]
    for field in ("completed_at", "completion_turn_key", "completion_receipt_id", "completion_validation_sha256"):
        assert before[field] == after[field]
    assert marker.read_text() == "run\n"
    assert cli._spend_run_count(runtime) == 2  # Original delivery plus the independently admitted review.
