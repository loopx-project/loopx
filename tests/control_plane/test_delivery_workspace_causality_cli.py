"""Exercise workspace causality through the ordinary CLI and both local stores."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    _classification_count,
    _initialize_fixture_authority,
    _initialize_git_checkout,
    _run_cli,
    _spend_run_count,
    _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_read_only_verification_settles_in_registered_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry_path = _write_fixture(tmp_path)
    _initialize_git_checkout(project)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.com",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "Verification fixture",
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )
    registry = json.loads(registry_path.read_text())
    registry["goals"][0]["coordination"]["registered_agents"].append("peer-agent")
    registry_path.write_text(json.dumps(registry))
    _initialize_fixture_authority(
        project, runtime, provider=provider, handoff_mode="hard_lease"
    )

    def cli(*args: str, cwd: Path = project) -> tuple[int, dict]:
        return _run_cli(registry_path, runtime, *args, cwd=cwd)

    rc, created = cli(
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--claimed-by",
        AGENT_ID,
        "--text",
        "Verify the immutable checkout revision and record the observation.",
        "--action-kind",
        "verify",
        "--task-class",
        "advancement_task",
        "--required-capability",
        "filesystem_read",
        "--required-capability",
        "shell",
    )
    assert rc == 0 and created["added"], created
    todo_id = created["todo_id"]
    binding = (
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        todo_id,
        "--turn-instance-id",
        f"turn-read-only-verification-{provider}",
    )
    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        *binding,
        "--available-capability",
        "filesystem_read",
        "--available-capability",
        "shell",
    )
    rc, guard = cli(*guard_args)
    assert rc == 0, guard
    assert (
        guard["interaction_contract"]["cli_channel"]["execution_preparation"]["state"]
        == "claim_required"
    )
    rc, claim = cli(
        "todo",
        "claim",
        "--goal-id",
        GOAL_ID,
        "--todo-id",
        todo_id,
        "--agent-id",
        AGENT_ID,
        "--claimed-by",
        AGENT_ID,
    )
    assert rc == 0, claim
    rc, ready = cli(*guard_args)
    assert rc == 0 and ready["normal_delivery_allowed"], ready

    head = subprocess.check_output(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=project, text=True
    ).strip()
    assert subprocess.check_output(
        ["git", "log", "-1", "--format=%s", head], cwd=project, text=True
    ).strip() == ("Verification fixture")
    evidence = f"evidence:fixture-head-{head}"
    rc, completed = cli(
        "todo",
        "complete",
        *binding,
        "--claim-operation-id",
        claim["claim_operation_id"],
        "--evidence",
        evidence,
    )
    assert rc == 0, completed

    classification = "registered_checkout_verification"
    refresh = (
        "refresh-state",
        *binding,
        "--classification",
        classification,
        "--delivery-batch-scale",
        "test_only",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-boundary",
        "semantic_closeout",
        "--vision-state",
        "vision_closed",
        "--vision-summary",
        "Verified the checkout revision through ordinary read commands.",
        "--vision-acceptance",
        "The verified revision is recorded in the Todo evidence.",
        "--progress-result-class",
        "advanced",
        "--progress-evidence-id",
        evidence,
        "--no-global-sync",
        "--suppress-external-sinks",
        "--settle-turn",
        "--settlement-receipt",
    )
    rc, preview = cli(*refresh, "--dry-run")
    assert rc == 0, preview.get("error") or preview
    assert (
        ready["delivery_workspace_isolation"]["peer_independent_worktree_required"]
        is False
    )
    assert (
        "registered Goal workspace"
        in (
            ready["interaction_contract"]["cli_channel"][
                "delivery_workspace_causality"
            ]["refresh"]
        )
    )
    assert preview["delivery_workspace"]["workspace_kind"] == "canonical_checkout"
    assert (
        preview["delivery_workspace_causality"]["reason"]
        == "declared_read_only_verification"
    )
    assert not preview["delivery_workspace"]["peer_independent_worktree_required"]

    # A different canonical checkout cannot supply the registered Goal's receipt.
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _initialize_git_checkout(foreign)
    subprocess.run(
        [
            "git",
            "remote",
            "set-url",
            "origin",
            "https://github.com/example/foreign.git",
        ],
        cwd=foreign,
        check=True,
        capture_output=True,
    )
    rc, wrong = cli(*refresh, "--delivery-workspace-path", str(foreign), "--dry-run")
    assert rc != 0 and "registered Goal repository" in wrong["error"], wrong
    assert _spend_run_count(runtime) == 0

    rc, written = cli(*refresh)
    assert rc == 0 and written["settlement_result"]["ok"], written
    assert written["delivery_workspace"]["workspace_kind"] == "canonical_checkout"
    assert (
        written["settlement_identity"]
        == ready["heartbeat_receipt"]["settlement_identity"]
    )
    rc, replay = cli(*refresh)
    assert rc == 0 and replay["idempotent_replay"], replay
    assert _spend_run_count(runtime) == 1
    assert _classification_count(runtime, classification) == 1
    worktrees = subprocess.check_output(
        ["git", "worktree", "list", "--porcelain"], cwd=project, text=True
    )
    assert sum(line.startswith("worktree ") for line in worktrees.splitlines()) == 1


def test_read_only_verification_respects_explicit_goal_workspace_isolation(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _initialize_git_checkout(project)
    registry = json.loads(registry_path.read_text())
    goal = registry["goals"][0]
    goal["coordination"]["registered_agents"].append("peer-agent")
    goal["workspace_guard_policy"] = {"peer_independent_worktree_required": True}
    registry_path.write_text(json.dumps(registry))
    _initialize_fixture_authority(project, runtime)
    rc, created = _run_cli(
        registry_path,
        runtime,
        "todo",
        "add",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--claimed-by",
        AGENT_ID,
        "--text",
        "Verify a checkout under the Goal's explicit isolation policy.",
        "--action-kind",
        "verify",
        "--task-class",
        "advancement_task",
        "--required-capability",
        "filesystem_read",
        cwd=project,
    )
    assert rc == 0 and created["added"], created
    rc, guard = _run_cli(
        registry_path,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        created["todo_id"],
        "--available-capability",
        "filesystem_read",
        "--turn-instance-id",
        "turn-explicit-verification-isolation",
        cwd=project,
    )
    assert rc == 0, guard
    assert guard["effective_action"] == "agent_workspace_repair", guard
    assert guard["normal_delivery_allowed"] is False
    assert (
        guard["delivery_workspace_isolation"]["peer_independent_worktree_required"]
        is True
    )
