"""Real Git layout reads stay fresh without repeating a root within a capture."""
import subprocess

import pytest

from loopx.control_plane.agents import workspace_guard as owner


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
