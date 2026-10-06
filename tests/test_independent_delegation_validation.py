"""Independent delegation uses the canonical Todo's own validation contract."""

from hashlib import sha256
import json
import subprocess
import sys

import pytest

from loopx.control_plane.goals.acceptance import (
    configure_goal_acceptance,
    inspect_goal_acceptance,
    validation_effect_files_current,
)
from test_delegation_cli import cli
from test_local_delegation import brief, demo, service as delegation_service, wait

service = delegation_service


def independent_binding(service, *, declared=True, task_repository=None, validation_argv=None):
    root, runner = service
    basis = inspect_goal_acceptance(registry_path=runner.registry, goal_id=runner.goal_id,
                                    runtime_root=str(runner.root))
    document = basis["contract"]
    document["scope"] = {"kind": "selected_work", "todo_ids": ["todo_reviewer-initial"]}
    document["bindings"] = [row for row in document["bindings"]
                            if row["todo_id"] == "todo_reviewer-initial"]
    configured = configure_goal_acceptance(registry_path=runner.registry, goal_id=runner.goal_id,
        runtime_root=str(runner.root), document=document,
        expected_provider_revision=basis["provider_revision"], execute=True)
    assert configured["status"] == "applied"
    args = ["todo", "add", "--goal-id", runner.goal_id, "--role", "agent",
            "--text", "Independently validate the assigned artifact", "--claimed-by", "analyst"]
    if task_repository is not None:
        args += ["--task-repository", task_repository]
    if declared:
        args += ["--validation-command-json", json.dumps(validation_argv or [
            sys.executable, "validation/acceptance.py", str(root), "analyst", "initial"])]
    created = demo.cli(root, *args)
    todo_id = created["todo_id"]
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["todo_id"] = todo_id
    runner.config.write_text(json.dumps(config))
    return todo_id


def test_independent_validator_qualifies_preflight_without_owner_rebinding(service):
    todo_id = independent_binding(service)
    status, check = cli(service[1], "inspect", "--binding-id", "analysis")
    assert status == 0, check
    assert check["state"] == "runtime_unverified", check
    assert check["acceptance_ready"] and check["turn_eligible"]
    assert check["binding"]["todo_id"] == todo_id
    assert not any(check["effects"].values())


def test_cross_repository_validator_uses_verified_binding_worktree(service):
    root, runner = service
    repository = root / "separate-repository"
    repository.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", *args],
                       check=True, capture_output=True)

    git("init", "-b", "main")
    (repository / "validator.marker").write_text("ok")
    (repository / ".gitignore").write_text(".local/\n")
    git("add", "validator.marker", ".gitignore")
    git("commit", "-m", "Fixture validator")
    task_repository = "https://example.invalid/synthetic/worker.git"
    git("remote", "add", "origin", task_repository)
    workspace = root / "separate-worker"
    git("worktree", "add", "--detach", str(workspace), "HEAD")
    todo_id = independent_binding(
        service, task_repository=task_repository,
        validation_argv=[sys.executable, "-c",
                         "from pathlib import Path; p = Path('validator.marker'); "
                         "assert p.read_text() == 'ok'; "
                         "p.write_text('changed') if Path('.local/mutate').exists() else None"],
    )
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["workspace"] = str(workspace)
    runner.config.write_text(json.dumps(config))

    binding = runner.binding("analysis", require_active=True)
    result = runner._validate(binding)
    assert result["plan"]["source"] == "todo_validation"
    assert result["delivery_workspace"]["workspace_kind"] == "independent_git_worktree"
    assert result["basis"]["todo"]["todo_id"] == todo_id
    pinned = [{"task_repository": result["basis"]["todo"]["task_repository"],
               "validation_label": "worker marker", "validation_files": [{
                   "path": "validator.marker",
                   "sha256": sha256((workspace / "validator.marker").read_bytes()).hexdigest(),
               }]}]
    assert not validation_effect_files_current(
        effects=pinned, registry_path=runner.registry, goal_id=runner.goal_id)
    assert validation_effect_files_current(
        effects=pinned, registry_path=runner.registry, goal_id=runner.goal_id,
        delivery_workspace=result["delivery_workspace"], validation_workspace_path=workspace)

    (workspace / ".local").mkdir()
    (workspace / ".local" / "mutate").touch()
    with pytest.raises(ValueError, match="changed during validation"):
        runner._validate(binding)
    (workspace / "validator.marker").write_text("ok")
    (workspace / ".local" / "mutate").unlink()

    (workspace / "validator.marker").write_text("changed")
    with pytest.raises(ValueError, match="acceptance rejected"):
        runner._validate(binding)


@pytest.mark.parametrize("extra_file", [None, "DELEGATION.json", "unrelated.txt"])
def test_native_validator_retires_only_its_exact_host_input(service, monkeypatch, extra_file):
    root, runner = service
    repository = root / "validation-repository"
    repository.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", *args],
                       check=True, capture_output=True)
    git("init", "-b", "main")
    (repository / "marker").write_text("ok")
    git("add", "marker")
    git("commit", "-m", "Validation fixture")
    remote = "https://example.invalid/synthetic/validation.git"
    git("remote", "add", "origin", remote)
    workspace = root / "validation-worker"
    git("worktree", "add", "--detach", str(workspace), "HEAD")
    independent_binding(service, task_repository=remote,
                        validation_argv=[sys.executable, "-c",
                                         "from pathlib import Path; assert Path('marker').read_text() == 'ok'"])
    config = json.loads(runner.config.read_text())
    config["bindings"][0]["workspace"] = str(workspace)
    runner.config.write_text(json.dumps(config))
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "validator-host-input", brief())
    row = json.loads(runner.path("validator-host-input").read_text())
    binding = runner.binding("analysis")
    runner._write_delegation_bootstrap(row, binding)
    if extra_file:
        (workspace / extra_file).write_text("Caller-owned content")
    arguments = runner._execution_arguments(binding, "validator-host-input")
    argv = json.loads(arguments[arguments.index("--validation-command-json") + 1])
    result = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=30)
    assert (result.returncode == 0) is (extra_file is None), result.stderr
    assert (workspace / "marker").read_text() == "ok"
    if extra_file:
        assert (workspace / extra_file).read_text() == "Caller-owned content"
    else:
        assert not (workspace / "DELEGATION.json").exists()
    assert not demo.canonical_tasks(root)[binding["todo_id"]]["done"]


@pytest.mark.parametrize("handoff_mode", ["soft_claim", "hard_lease"])
def test_independent_result_reconnects_and_revalidates_without_goal_binding(service, monkeypatch, handoff_mode):
    root, runner = service
    if handoff_mode == "hard_lease":
        for task_id, task in demo.canonical_tasks(root).items():
            demo.cli(root, "todo", "update", "--goal-id", runner.goal_id,
                     "--todo-id", task_id, "--agent-id", task["claimed_by"], "--clear-claim")
        changed = demo.cli(root, "handoff-mode", "set", "--goal-id", runner.goal_id,
                           "--mode", "hard_lease")
        assert changed["ok"], changed
    todo_id = independent_binding(service)
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "independent-1", brief())
    runner.execute("independent-1")
    result = wait(runner, "independent-1")
    assert result["status"] == "accepted", result
    assert demo.canonical_tasks(root)[todo_id]["done"]
    assert demo.canonical_tasks(root)[todo_id]["completion_continuation"] == "active_goal"
    assert not demo.canonical_tasks(root)["todo_reviewer-initial"]["done"]
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    assert runner.read("independent-1")["artifacts"][0]["sha256"]
    runner.resume("independent-1")
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    (root / "analyst" / "initial" / "output.json").write_text("{}")
    with pytest.raises(ValueError, match="acceptance rejected"):
        runner.read("independent-1")


def test_missing_independent_validator_cannot_launch(service, monkeypatch):
    root, runner = service
    independent_binding(service, declared=False)
    status, check = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0 and check["state"] == "acceptance_unavailable", check
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "unvalidated-1", brief())
    runner.execute("unvalidated-1")
    assert runner.read("unvalidated-1")["status"] == "rejected"
    assert not (root / "analyst" / "initial" / "host-invocations").exists()


def test_passing_todo_validator_cannot_waive_failing_owner_criteria(service):
    root, runner = service
    todo_id = independent_binding(service)
    assert runner._validate(runner.binding("analysis", require_active=True))["plan"]["source"] == "todo_validation"
    basis = inspect_goal_acceptance(registry_path=runner.registry, goal_id=runner.goal_id,
                                    runtime_root=str(runner.root))
    document = basis["contract"]
    document["scope"]["todo_ids"].append(todo_id)
    document["bindings"].append({"todo_id": todo_id, "criterion_ids": ["analyst-initial"]})
    for criterion in document["criteria"]:
        if criterion["id"] == "analyst-initial":
            criterion["validation_argv"] = [sys.executable, "-c", "raise SystemExit(1)"]
    configured = configure_goal_acceptance(registry_path=runner.registry, goal_id=runner.goal_id,
        runtime_root=str(runner.root), document=document,
        expected_provider_revision=basis["provider_revision"], execute=True)
    assert configured["status"] == "applied"
    with pytest.raises(ValueError, match="acceptance rejected"):
        runner._validate(runner.binding("analysis", require_active=True))
    assert not demo.canonical_tasks(root)[todo_id]["done"]
    assert not (root / "analyst" / "initial" / "host-invocations").exists()


def test_selected_unbound_work_cannot_fall_back_to_its_own_validator(service, monkeypatch):
    root, runner = service
    todo_id = independent_binding(service)
    basis = inspect_goal_acceptance(registry_path=runner.registry, goal_id=runner.goal_id,
                                    runtime_root=str(runner.root))
    document = basis["contract"]
    document["scope"]["todo_ids"].append(todo_id)
    configure_goal_acceptance(registry_path=runner.registry, goal_id=runner.goal_id,
        runtime_root=str(runner.root), document=document,
        expected_provider_revision=basis["provider_revision"], execute=True)
    status, check = cli(runner, "inspect", "--binding-id", "analysis")
    assert status == 0 and not check["acceptance_ready"], check
    assert "goal_acceptance_unbound" in check["authority_reason"]
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "selected-unbound", brief())
    runner.execute("selected-unbound")
    assert runner.read("selected-unbound")["status"] == "rejected"
    assert not (root / "analyst" / "initial" / "host-invocations").exists()
