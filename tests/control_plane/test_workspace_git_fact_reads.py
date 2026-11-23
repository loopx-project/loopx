"""Real Git layout reads stay fresh without repeating a root within a capture."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.agents import workspace_guard as owner
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_source_fixture,
    isolate_sqlite_runtime,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def workspaces(tmp_path):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    git(canonical, "init", "-q")
    git(canonical, "config", "user.name", "Git fact fixture")
    git(canonical, "config", "user.email", "fixture@example.test")
    git(canonical, "remote", "add", "origin", "https://github.com/example/workspace.git")
    git(canonical, "commit", "-q", "--allow-empty", "-m", "Initial fixture")
    peer = tmp_path / "peer"
    git(canonical, "worktree", "add", "-q", "-b", "peer", str(peer))
    nested = peer / "nested"
    nested.mkdir()
    return canonical, peer, nested


def observe_reads(monkeypatch):
    original = owner._git_command_output
    calls = []

    def observed(path, *args):
        calls.append((path, args))
        return original(path, *args)

    monkeypatch.setattr(owner, "_git_command_output", observed)
    return calls


def test_capture_resolves_nested_root_once_but_reads_all_original_facts(workspaces, monkeypatch):
    canonical, peer, nested = workspaces
    calls = observe_reads(monkeypatch)
    snapshot = owner.capture_delivery_workspace(nested)
    assert snapshot["workspace_kind"] == "independent_git_worktree"
    assert snapshot["task_repository"] == "git:github.com/example/workspace"
    assert [args for _, args in calls] == [
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "--git-common-dir"),
        ("rev-parse", "--git-dir"),
        ("config", "--get", "remote.origin.url"),
        ("rev-parse", "HEAD"),
    ]
    assert calls[1][0] == calls[2][0] == peer.resolve()
    assert owner.capture_delivery_workspace(canonical)["workspace_kind"] == "canonical_checkout"


def test_single_agent_guidance_does_not_impose_multi_peer_workspace_gate(workspaces):
    canonical, peer, _ = workspaces
    todo = {"task_repository": "git:github.com/example/workspace", "required_write_scopes": ["src/**"]}
    # Real Git facts: one agent may use this checkout; shared peer writes still
    # require the independent worktree and retain the same repository binding.
    identity = {"agent_id": "peer", "registered_agents": ["peer"]}
    assert owner.build_agent_workspace_guard({}, identity, selected_todo=todo, current_path=canonical) is None
    identity["registered_agents"] = ["lead", "peer"]
    assert owner.build_agent_workspace_guard({}, identity, selected_todo=todo, current_path=canonical)["blocks_delivery"] is True
    assert owner.build_agent_workspace_guard({}, identity, selected_todo=todo, current_path=peer) is None


def test_next_capture_rechecks_head_and_origin_without_cross_request_cache(workspaces, monkeypatch):
    _, peer, nested = workspaces
    calls = observe_reads(monkeypatch)
    first = owner.capture_delivery_workspace(nested)
    git(peer, "commit", "-q", "--allow-empty", "-m", "Next fixture revision")
    second = owner.capture_delivery_workspace(nested)
    assert first["workspace_revision_digest"] != second["workspace_revision_digest"]
    git(peer, "remote", "set-url", "origin", "https://github.com/example/other.git")
    third = owner.capture_delivery_workspace(nested)
    assert third["task_repository"] == "git:github.com/example/other"
    assert sum(args == ("rev-parse", "--show-toplevel") for _, args in calls) == 3


def test_peer_guard_reuses_only_current_root_and_rechecks_foreign_origin(workspaces, monkeypatch):
    canonical, peer, nested = workspaces
    calls = observe_reads(monkeypatch)
    identity = {"agent_id": "peer", "registered_agents": ["lead", "peer"]}
    todo = {"task_repository": "git:github.com/example/workspace", "required_write_scopes": ["src/**"]}

    def guard(path):
        return owner.build_agent_workspace_guard({}, identity, selected_todo=todo, current_path=path)

    assert guard(nested) is None
    assert len(calls) == 4
    assert sum(args == ("rev-parse", "--show-toplevel") for _, args in calls) == 1
    assert guard(canonical)["current_workspace"] == "canonical_checkout"
    git(peer, "remote", "set-url", "origin", "https://github.com/example/other.git")
    assert guard(nested)["current_workspace"] == "foreign_git_worktree"


@pytest.mark.parametrize("failed_arg", ["--git-common-dir", "--git-dir"])
def test_layout_failure_still_refuses_delivery(workspaces, monkeypatch, failed_arg):
    _, _, nested = workspaces
    original = owner._git_command_output

    def failed(path, *args):
        return None if failed_arg in args else original(path, *args)

    monkeypatch.setattr(owner, "_git_command_output", failed)
    assert owner.capture_delivery_workspace(nested) is None
    guard = owner.build_agent_workspace_guard(
        {}, {"agent_id": "peer", "registered_agents": ["lead", "peer"]},
        selected_todo={"task_repository": "git:github.com/example/workspace", "required_write_scopes": ["src/**"]},
        current_path=nested,
    )
    assert guard["blocks_delivery"] is True


def test_goal_repository_fallback_resolves_each_worktree_once(workspaces, monkeypatch):
    canonical, _, nested = workspaces
    calls = observe_reads(monkeypatch)
    assert owner.build_agent_workspace_guard(
        {"repo": str(canonical)}, {"agent_id": "peer", "registered_agents": ["lead", "peer"]},
        selected_todo={"required_write_scopes": ["src/**"]}, current_path=nested,
    ) is None
    assert sum(args == ("rev-parse", "--show-toplevel") for _, args in calls) == 2
    guard = owner.build_agent_workspace_guard(
        {"repo": str(canonical)}, {"agent_id": "peer", "registered_agents": ["lead", "peer"]},
        selected_todo={"action_kind": "validate", "required_write_scopes": ["src/**"]},
        current_path=canonical,
    )
    assert guard is not None and guard["current_workspace"] == "canonical_checkout"


def test_foreign_worktree_explains_missing_cross_repository_binding(workspaces, tmp_path):
    canonical, _, _ = workspaces
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git(foreign, "init", "-q")
    git(foreign, "remote", "add", "origin", "https://github.com/example/foreign.git")
    identity = {"agent_id": "peer", "registered_agents": ["lead", "peer"]}

    guard = owner.build_agent_workspace_guard(
        {"repo": str(canonical)}, identity,
        selected_todo={"action_kind": "implement"}, current_path=foreign,
    )

    assert guard["current_workspace"] == "foreign_git_worktree"
    assert guard["repository_source"] == "goal.repo"
    assert "loopx todo update --task-repository <credential-free-git-identity>" in guard["required_action"]
    assert "Goal repository" in guard["required_action"]


def test_foreign_worktree_with_explicit_mismatched_task_repository_keeps_move_guidance(workspaces, tmp_path):
    canonical, _, _ = workspaces
    foreign = tmp_path / "foreign-bound"
    foreign.mkdir()
    git(foreign, "init", "-q")
    git(foreign, "remote", "add", "origin", "https://github.com/example/foreign.git")
    identity = {"agent_id": "peer", "registered_agents": ["lead", "peer"]}

    guard = owner.build_agent_workspace_guard(
        {"repo": str(canonical)},
        identity,
        selected_todo={
            "action_kind": "implement",
            "task_repository": "git:github.com/example/other",
        },
        current_path=foreign,
    )

    assert guard["current_workspace"] == "foreign_git_worktree"
    assert guard["repository_source"] == "selected_todo.task_repository"
    assert "--task-repository" not in guard["required_action"]
    assert "create or switch to an independent git worktree/branch" in guard["required_action"]


@pytest.mark.parametrize("task_repository", [None, "git:github.com/example/other"])
def test_foreign_worktree_progressive_initial_read_surfaces_missing_binding_guidance(
    tmp_path, monkeypatch, task_repository,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project = tmp_path / "goal-repo"
    project.mkdir()
    git(project, "init", "-q")
    git(project, "config", "user.name", "Git fact fixture")
    git(project, "config", "user.email", "fixture@example.test")
    git(project, "remote", "add", "origin", "https://github.com/example/workspace.git")
    git(project, "commit", "-q", "--allow-empty", "-m", "Initial goal commit")

    foreign = tmp_path / "foreign-repo"
    foreign.mkdir()
    git(foreign, "init", "-q")
    git(foreign, "config", "user.name", "Git fact fixture")
    git(foreign, "config", "user.email", "fixture@example.test")
    git(foreign, "remote", "add", "origin", "https://github.com/example/foreign.git")
    git(foreign, "commit", "-q", "--allow-empty", "-m", "Initial foreign commit")

    runtime = tmp_path / "runtime"
    goal_id = "foreign-repo-hint-fixture"
    agent_id = "peer"
    todo_id = "todo_foreign_repo_hint"
    state_rel = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_rel
    state_path.parent.mkdir(parents=True, exist_ok=True)
    repo_attr = f" task_repository={task_repository}" if task_repository else ""
    state_path.write_text(
        "---\n"
        "status: active-read-only\n"
        "owner_mode: goal\n"
        'objective: "Verify foreign worktree remediation guidance."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Foreign Worktree Guidance Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Update cross-repository target in independent worktree.\n"
        f"  <!-- loopx:todo todo_id={todo_id} status=open claimed_by={agent_id} "
        f"task_class=advancement_task action_kind=implement{repo_attr} -->\n",
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
                        "id": goal_id,
                        "domain": "workspace-guard-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_rel,
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": ["lead", agent_id],
                            "agent_model": "peer_v1",
                        },
                        "authority_sources": [],
                        "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 4},
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    initialize_canonical_source_fixture(
        runtime,
        goal_id,
        state_path=state_path,
        provider="sqlite",
    )

    def run_entry(*args: str, cwd: Path) -> dict:
        proc = subprocess.run(
            [sys.executable, "-m", "loopx.entrypoint", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
        return json.loads(proc.stdout)

    nav = run_entry(
        "--registry", str(registry_path),
        "--runtime-root", str(runtime),
        "--format", "json",
        "quota", "should-run",
        "--codex-app",
        "--goal-id", goal_id,
        "--agent-id", agent_id,
        "--turn-instance-id", "turn-foreign-initial-read",
        "--context-delivery", "on",
        cwd=foreign,
    )
    first_tokens = shlex.split(nav["first_read_command"])
    quota_idx = first_tokens.index("quota")
    page = run_entry("--format", "json", *first_tokens[quota_idx:], cwd=foreign)
    selections = {row["pointer"]: row["value"] for row in json.loads(page["content"])["selections"]}

    assert selections["/effective_action"] == "agent_workspace_repair"
    guard = selections["/workspace_guard"]
    assert guard["current_workspace"] == "foreign_git_worktree"
    obligation = selections["/execution_obligation"]
    primary_action = selections["/interaction_contract/agent_channel/primary_action"]
    cli_next = selections.get("/interaction_contract/cli_channel/next_cli_actions") or selections[
        "/interaction_contract/cli_channel"
    ]["next_cli_actions"]
    cli_actions = " ".join(cli_next)

    if task_repository is None:
        assert guard["repository_source"] == "goal.repo"
        assert "loopx todo update --task-repository <credential-free-git-identity>" in guard["required_action"]
        assert "--task-repository <credential-free-git-identity>" in obligation["contract_obligation"]
        assert "--task-repository <credential-free-git-identity>" in primary_action
        assert "todo update" in cli_actions
        assert "--task-repository <credential-free-git-identity>" in cli_actions
    else:
        assert guard["repository_source"] == "selected_todo.task_repository"
        assert "--task-repository" not in guard["required_action"]
        assert "--task-repository" not in obligation["contract_obligation"]
        assert "independent worktree/branch" in primary_action
        assert "--task-repository" not in cli_actions
