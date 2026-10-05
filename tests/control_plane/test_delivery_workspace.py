from __future__ import annotations

import json
import subprocess

import pytest
from pathlib import Path

from loopx.control_plane.agents.workspace_guard import (
    build_delivery_workspace_guard,
    capture_delivery_workspace,
    delivery_workspace_identity,
    delivery_workspace_repository,
)


def test_gitless_single_agent_workspace_uses_stable_goal_identity(
    tmp_path: Path,
) -> None:
    project = tmp_path / "plain-project"
    project.mkdir()

    snapshot = capture_delivery_workspace(
        project,
        local_goal_id="plain-goal",
        local_project_root=project,
    )

    assert snapshot == {
        "schema_version": "delivery_workspace_v1",
        "workspace_identity": "loopx:plain-goal",
        "identity_kind": "local_goal",
        "task_repository": None,
        "repository_source": "goal_id_fallback",
        "workspace_kind": "local_goal_workspace",
        "peer_independent_worktree_required": False,
    }
    assert str(project) not in json.dumps(snapshot)
    assert delivery_workspace_identity(snapshot) == "loopx:plain-goal"
    assert delivery_workspace_repository(snapshot) is None
    assert build_delivery_workspace_guard(
        {"delivery_workspace": snapshot},
        current_path=tmp_path,
    ) is None


def test_gitless_workspace_fails_closed_outside_goal_or_for_peer_writes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "plain-project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()

    assert capture_delivery_workspace(
        outside,
        local_goal_id="plain-goal",
        local_project_root=project,
    ) is None
    assert capture_delivery_workspace(
        project,
        local_goal_id="plain-goal",
        local_project_root=project,
        peer_independent_worktree_required=True,
    ) is None


def test_legacy_git_workspace_remains_accepted() -> None:
    legacy = {
        "schema_version": "delivery_workspace_v0",
        "task_repository": "git:github.com/example/loopx",
        "repository_source": "current_git_origin",
        "workspace_kind": "canonical_checkout",
        "peer_independent_worktree_required": False,
    }

    assert delivery_workspace_identity(legacy) == "git:github.com/example/loopx"
    assert delivery_workspace_repository(legacy) == "git:github.com/example/loopx"


def _originless_repo(path):
    path.mkdir(exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)


def test_originless_git_uses_registered_local_goal_identity(tmp_path):
    project = tmp_path / "local"
    _originless_repo(project)
    nested = project / "results"
    nested.mkdir()
    receipt = capture_delivery_workspace(nested, local_goal_id="local-goal", local_project_root=project)
    assert receipt is not None
    assert receipt["workspace_identity"] == "loopx:local-goal"
    assert receipt["workspace_kind"] == "local_goal_workspace"
    assert receipt["task_repository"] is None
    assert str(project) not in json.dumps(receipt)
    assert capture_delivery_workspace(project, local_goal_id="local-goal") is None
    assert capture_delivery_workspace(project, local_goal_id="local-goal", local_project_root=project,
                                      peer_independent_worktree_required=True) is None


@pytest.mark.parametrize("remote", ["", "not-a-remote", "https://example.test/repo?credential=synthetic"])
def test_invalid_origin_does_not_fall_back_to_local_goal(tmp_path, remote):
    _originless_repo(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "config", "remote.origin.url", remote], check=True)
    assert capture_delivery_workspace(tmp_path, local_goal_id="local-goal", local_project_root=tmp_path) is None


def test_originless_unrelated_or_nested_repository_is_not_the_registered_goal(tmp_path):
    project = tmp_path / "goal"
    project.mkdir()
    for checkout in [tmp_path / "other", project / "nested-repo"]:
        _originless_repo(checkout)
        assert capture_delivery_workspace(checkout, local_goal_id="local-goal", local_project_root=project) is None


def test_originless_linked_worktree_cannot_become_local_goal(tmp_path):
    project = tmp_path / "primary"
    _originless_repo(project)
    subprocess.run(["git", "-C", str(project), "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.test", "commit", "-qm", "fixture", "--allow-empty"], check=True)
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(project), "worktree", "add", "--detach", str(linked)], check=True, capture_output=True)
    assert capture_delivery_workspace(linked, local_goal_id="local-goal", local_project_root=linked) is None


def test_failed_origin_read_cannot_be_interpreted_as_missing_key(tmp_path, monkeypatch):
    from loopx.control_plane.agents import workspace_guard as owner
    _originless_repo(tmp_path)
    original = subprocess.run
    def fail_origin_read(argv, **kwargs):
        if argv[-3:] == ["config", "--get", "remote.origin.url"]:
            return subprocess.CompletedProcess(argv, 128, stdout="", stderr="unreadable config")
        return original(argv, **kwargs)
    monkeypatch.setattr(owner.subprocess, "run", fail_origin_read)
    assert capture_delivery_workspace(tmp_path, local_goal_id="local-goal", local_project_root=tmp_path) is None
