"""Local research is independently accepted delivery, not non-delivery work."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
import test_quota_authority_settlement_journey as journey
import test_quota_settlement_cli as cli
from canonical_authority_fixture import isolate_sqlite_runtime


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_bound_repository_is_checked_from_the_complete_todo_source(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _, _ = journey._source(
        tmp_path, provider=provider,
        extra=f"claimed_by={cli.AGENT_ID} task_repository=git:github.com/example/right",
    )
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("peer-researcher")
    registry.write_text(json.dumps(data))

    def worktree(name):
        repository, checkout = tmp_path / name, tmp_path / f"{name}-worktree"
        repository.mkdir()

        def git(*args):
            subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)

        git("init", "--quiet")
        git("config", "remote.origin.url", f"https://github.com/example/{name}.git")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
            "commit", "--quiet", "--allow-empty", "--signoff", "-m", "Synthetic fixture")
        git("worktree", "add", "--quiet", "-b", "codex/fixture", str(checkout))
        return checkout

    wrong, right = worktree("wrong"), worktree("right")
    code, guard = journey._guard(project, runtime, registry)
    assert code == 0 and guard["selected_todo"]["task_repository"] == "git:github.com/example/right", guard

    def refresh(checkout):
        return cli._run_cli(
            registry, runtime, "refresh-state", "--goal-id", cli.GOAL_ID,
            "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
            "--turn-instance-id", cli.TURN_ID, "--delivery-boundary", "in_flight_continuation",
            "--delivery-outcome", "outcome_progress", "--delivery-workspace-path", str(checkout),
            "--no-global-sync", "--suppress-external-sinks", cwd=checkout,
        )

    code, rejected = refresh(wrong)
    assert code != 0 and not rejected["ok"] and not rejected["appended"], rejected
    assert cli._spend_run_count(runtime) == 0
    code, recovered = refresh(right)
    assert code == 0 and recovered["ok"], recovered
    assert recovered["delivery_workspace"]["task_repository"] == "git:github.com/example/right"
    code, spent = journey._execute(recovered["settlement_owed"]["command"], right, runtime, registry)
    assert code == 0 and spent["settlement_progress"]["state"] == "settled", spent
    code, retry = journey._execute(recovered["settlement_owed"]["command"], right, runtime, registry)
    assert code == 0 and not retry["appended"], retry
    assert cli._spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_peer_delivery_completes_and_settles_without_git(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _, _ = journey._source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=f"claimed_by={cli.AGENT_ID}",
    )
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("peer-researcher")
    registry.write_text(json.dumps(data))

    def run(*args, cwd=project):
        return cli._run_cli(registry, runtime, *args, "--goal-id", cli.GOAL_ID, cwd=cwd)

    code, guard = journey._guard(project, runtime, registry)
    assert code == 0 and guard["decision"] == "run", guard
    key = "local-result-execution"
    code, claimed = run("todo", "claim", "--todo-id", cli.TODO_ID,
                        "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID,
                        "--task-lease-idempotency-key", key)
    assert code == 0 and claimed["ok"], claimed
    proof = ("--task-lease-idempotency-key", key,
             "--task-lease-expected-version", str(claimed["lease"]["version"]))
    # A successful caller preflight cannot substitute for controller validation.
    # The approved validator independently checks the actual result artifact.
    result = project / "review.md"
    command = [sys.executable, "-c", "from pathlib import Path; Path('validation-ran').touch(); assert Path('review.md').read_text() == 'accepted local research\\n'"]
    code, listed = run("todo", "list", "--todo-id", cli.TODO_ID)
    assert code == 0, listed
    code, revised = run("todo", "update", "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
                        "--update-operation-id", "local-result-validator",
                        "--update-expected-provider-revision", listed["authority_read"]["provider_revision"],
                        *proof, "--validation-command-json", json.dumps(command),
                        "--validation-timeout-seconds", "20")
    assert code == 0 and revised["ok"], revised
    complete = ("todo", "complete", "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
                "--turn-instance-id", cli.TURN_ID, *proof,
                "--evidence", "review.md")
    result.write_text("accepted local research\n")
    code, unbound_result = run(*complete, "--result-file", str(result))
    assert not unbound_result["ok"] and unbound_result["reason_code"] == "completion_result_rejected", unbound_result
    assert "Goal acceptance criteria" in unbound_result["reason"]
    assert not (project / "validation-ran").exists()
    result.write_text("unvalidated local research\n")
    code, rejected = run(*complete)
    assert not rejected["ok"] and rejected.get("validation_blocked_completion"), rejected
    assert rejected["validation_failure"]["validation_receipt"]["exit_code"] == 1
    assert run("todo", "list", "--todo-id", cli.TODO_ID)[1]["todo"]["status"] == "open"

    result.write_text("accepted local research\n")
    code, completed = run(*complete)
    assert code == 0 and completed["ok"], completed
    current = run("todo", "list", "--todo-id", cli.TODO_ID)[1]["todo"]
    assert current["status"] == "done" and current["completion_validation_required"]
    assert current["evidence"] == "review.md" and not current.get("completion_result")

    outside = tmp_path / "outside"
    outside.mkdir()
    code, wrong_workspace = run("refresh-state", "--agent-id", cli.AGENT_ID,
                                "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID,
                                "--delivery-outcome", "outcome_progress", "--no-global-sync", cwd=outside)
    assert not wrong_workspace["ok"] and not wrong_workspace["appended"], wrong_workspace
    code, refreshed = journey._refresh(project, runtime, registry)
    assert code == 0 and refreshed["ok"], json.dumps(refreshed)
    assert refreshed["delivery_workspace"]["identity_kind"] == "local_goal"
    assert refreshed["delivery_workspace"]["workspace_identity"] == f"loopx:{cli.GOAL_ID}"
    assert refreshed["delivery_workspace"]["peer_independent_worktree_required"] is False
    spend = refreshed["settlement_owed"]["command"]
    code, spent = journey._execute(spend, project, runtime, registry)
    assert code == 0 and spent["settlement_progress"]["state"] == "settled", spent
    code, retry = journey._execute(spend, project, runtime, registry)
    assert code == 0 and not retry["appended"], retry
    assert cli._spend_run_count(runtime) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("release_execution_lease", [False, True])
def test_local_in_flight_progress_keeps_completion_independent(tmp_path, monkeypatch, provider, release_execution_lease):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _, _ = journey._source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=f"claimed_by={cli.AGENT_ID}",
    )
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("peer-researcher")
    registry.write_text(json.dumps(data))
    code, guard = journey._guard(project, runtime, registry)
    assert code == 0 and guard["decision"] == "run", guard
    code, claimed = cli._run_cli(registry, runtime, "todo", "claim",
                                "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID,
                                "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID,
                                "--task-lease-idempotency-key", "local-in-flight", cwd=project)
    assert code == 0 and claimed["ok"], claimed
    if release_execution_lease:
        code, released = cli._run_cli(
            registry, runtime, "task-lease", "release", "--goal-id", cli.GOAL_ID,
            "--todo-id", cli.TODO_ID, "--owner", cli.AGENT_ID,
            "--idempotency-key", "local-in-flight",
            "--expected-version", str(claimed["lease"]["version"]), cwd=project,
        )
        assert code == 0 and released["ok"], released
    code, refreshed = cli._run_cli(
        registry, runtime, "refresh-state", "--goal-id", cli.GOAL_ID,
        "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
        "--turn-instance-id", cli.TURN_ID, "--delivery-boundary", "in_flight_continuation",
        "--delivery-outcome", "outcome_progress", "--delivery-batch-scale", "multi_surface",
        "--no-global-sync", "--suppress-external-sinks", cwd=project,
    )
    assert code == 0 and refreshed["ok"], json.dumps(refreshed)
    assert refreshed["delivery_workspace"]["workspace_kind"] == "local_goal_workspace"
    code, spent = journey._execute(refreshed["settlement_owed"]["command"], project, runtime, registry)
    assert code == 0 and spent["settlement_progress"]["state"] == "settled", spent
    code, listed = cli._run_cli(registry, runtime, "todo", "list", "--goal-id", cli.GOAL_ID,
                               "--todo-id", cli.TODO_ID, cwd=project)
    assert code == 0 and listed["todo"]["status"] == "open", listed
    assert not listed["todo"].get("completion_result")
    code, next_guard = journey._guard(project, runtime, registry, turn_id="next-local-in-flight")
    assert code == 0 and next_guard["selected_todo"]["todo_id"] == cli.TODO_ID, next_guard
    assert cli._spend_run_count(runtime) == 1


def test_local_receipt_cannot_satisfy_an_explicit_git_task(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _, _ = journey._source(
        tmp_path, provider="sqlite", handoff_mode="hard_lease",
        extra=f"claimed_by={cli.AGENT_ID} task_repository=git:github.com/example/delivery",
    )
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("peer-researcher")
    registry.write_text(json.dumps(data))
    code, guard = journey._guard(project, runtime, registry)
    assert code == 0 and guard["decision"] == "run", guard
    code, rejected = cli._run_cli(
        registry, runtime, "refresh-state", "--goal-id", cli.GOAL_ID,
        "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
        "--turn-instance-id", cli.TURN_ID, "--delivery-boundary", "in_flight_continuation",
        "--delivery-outcome", "outcome_progress", "--no-global-sync", cwd=project,
    )
    assert not rejected["ok"] and not rejected["appended"], rejected
    assert "independent git worktree" in rejected["error"]
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_blocked_writeback_preserves_retry_and_spends_nothing(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _, _ = journey._source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=f"claimed_by={cli.AGENT_ID}",
    )
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("peer-researcher")
    registry.write_text(json.dumps(data))

    def run(*args):
        return cli._run_cli(registry, runtime, *args, "--goal-id", cli.GOAL_ID, cwd=project)

    code, guard = journey._guard(project, runtime, registry)
    assert code == 0 and guard["decision"] == "run", guard
    code, claimed = run("todo", "claim", "--todo-id", cli.TODO_ID,
                        "--agent-id", cli.AGENT_ID, "--claimed-by", cli.AGENT_ID,
                        "--task-lease-idempotency-key", "local-blocked")
    assert code == 0 and claimed["ok"], claimed
    due = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    code, listed = run("todo", "list", "--todo-id", cli.TODO_ID)
    assert code == 0, listed
    code, wait = run("todo", "update", "--todo-id", cli.TODO_ID, "--agent-id", cli.AGENT_ID,
                     "--update-operation-id", "local-blocked-retry",
                     "--update-expected-provider-revision", listed["authority_read"]["provider_revision"],
                     "--task-lease-idempotency-key", "local-blocked",
                     "--task-lease-expected-version", str(claimed["lease"]["version"]),
                     "--resume-when", f"resume_at:{due}")
    assert code == 0 and wait["ok"], wait
    code, refreshed = run("refresh-state", "--agent-id", cli.AGENT_ID,
                           "--todo-id", cli.TODO_ID, "--turn-instance-id", cli.TURN_ID,
                           "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
                           "--progress-blocker-id", "blocker:runtime-boundary",
                           "--progress-evidence-id", "evidence:runtime-boundary",
                           "--no-global-sync", "--suppress-external-sinks")
    assert code == 0 and refreshed["ok"], json.dumps(refreshed)
    assert refreshed["delivery_workspace"]["identity_kind"] == "local_goal"
    assert refreshed["settlement_progress"]["state"] == "settled"
    assert refreshed["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
    assert refreshed.get("settlement_owed") is None
    assert refreshed["blocked_retry"]["resume_when"] == f"resume_at:{due}"
    todo = run("todo", "list", "--todo-id", cli.TODO_ID)[1]["todo"]
    assert todo["status"] == "open" and not todo["resume_ready"]
    assert not todo.get("completion_result")
    assert cli._spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("explicit_repository", [False, True])
def test_originless_local_git_settles_without_fabricating_repository(tmp_path, monkeypatch, provider, explicit_repository):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    extra = f"claimed_by={cli.AGENT_ID}"
    if explicit_repository:
        extra += " task_repository=git:github.com/example/required"
    project, runtime, registry, _, _ = journey._source(tmp_path, provider=provider, extra=extra)
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True)
    code, guard = journey._guard(project, runtime, registry)
    assert code == 0, guard
    code, refreshed = cli._run_cli(
        registry, runtime, "refresh-state", "--goal-id", cli.GOAL_ID,
        "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
        "--turn-instance-id", cli.TURN_ID, "--delivery-boundary", "in_flight_continuation",
        "--delivery-outcome", "outcome_progress", "--delivery-batch-scale", "multi_surface",
        "--no-global-sync", "--suppress-external-sinks", cwd=project,
    )
    if explicit_repository:
        assert code != 0 and not refreshed["ok"] and not refreshed["appended"], refreshed
        assert cli._spend_run_count(runtime) == 0
        return
    assert code == 0 and refreshed["ok"], refreshed
    assert refreshed["delivery_workspace"]["workspace_identity"] == f"loopx:{cli.GOAL_ID}"
    code, spent = journey._execute(refreshed["settlement_owed"]["command"], project, runtime, registry)
    assert code == 0 and spent["settlement_progress"]["state"] == "settled", spent
    code, repeated = journey._execute(refreshed["settlement_owed"]["command"], project, runtime, registry)
    assert code == 0 and not repeated["appended"], repeated
    assert cli._spend_run_count(runtime) == 1
