"""Registered local writes reuse Goal identity and baseline scope matching."""

import json
import subprocess

import pytest

from loopx.control_plane.agents.workspace_guard import (
    build_agent_workspace_guard, observe_goal_local_workspace,
)
from loopx.control_plane.quota.projection_repair import (
    build_boundary_projection_repair_hint,
)
from loopx.control_plane.quota.settlement_workspace_causality import (
    project_goal_write_scopes,
)


def declaration():
    return {
        "todo_id": "todo_local_material", "role": "agent", "status": "open",
        "task_class": "advancement_task", "action_kind": "validate_material",
        "required_write_scopes": ["materials/run/**", "reports/result.md"],
    }


def scope_admitted(root, todo, grants):
    projected = project_goal_write_scopes(root, grants)["allowed_write_scopes"]
    return build_boundary_projection_repair_hint(
        {"write_scope": [*grants, *projected]},
        {"first_executable_items": [todo]}, candidate_should_run=True,
        selected_todo=todo,
    ) is None


def test_scope_projection_keeps_baseline_relative_and_glob_matching(tmp_path):
    root = str(tmp_path)
    todo = declaration()
    for grants in [
        ["materials/**", "reports/result.md"],
        [f"{root}/materials/**", f"{root}/reports/result.md"],
        ["material*/**", "reports/*.md"],
        [f"{root}/material*/**", f"{root}/reports/*.md"],
    ]:
        assert scope_admitted(root, todo, grants) is True
    for grants in [[], [f"{root}-other/**"], [f"{root}/../elsewhere/**"], ["other/**"]]:
        assert scope_admitted(root, todo, grants) is False
    assert scope_admitted(root, {**todo, "required_write_scopes": ["reports/other.txt"]}, ["reports/*.md"]) is False


@pytest.mark.parametrize("metadata", [{}, {"task_domain": "code"}, {"continuation_policy": "independent_handoff"}])
def test_local_identity_avoids_git_requirement_without_special_task_flags(tmp_path, metadata):
    root = tmp_path / "local"
    root.mkdir()
    goal = {"id": "local-work", "repo": str(root), "coordination": {"write_scope": ["materials/**", "reports/*.md"]}}
    todo = {**declaration(), **metadata}
    identity = {"agent_id": "worker", "registered_agents": ["worker", "peer"]}
    local = observe_goal_local_workspace(goal, todo)
    assert local["workspace"]["identity_kind"] == "local_goal"
    # Caller cwd does not rebase the declared output target or demand a repo.
    assert build_agent_workspace_guard(goal, identity, selected_todo=todo, current_path=tmp_path, local_workspace=local) is None
    assert build_agent_workspace_guard({**goal, "workspace_guard_policy": {"peer_independent_worktree_required": False}}, identity, selected_todo=todo, current_path=tmp_path) is None
    assert build_agent_workspace_guard({**goal, "workspace_guard_policy": {"peer_independent_worktree_required": True}}, identity, selected_todo=todo, current_path=root)["blocks_delivery"] is True
    assert observe_goal_local_workspace(goal, {**todo, "task_repository": "git:github.com/example/project"}) == {}


def test_registered_local_identity_reuses_originless_owner_and_literal_grants(tmp_path):
    physical = tmp_path / "physical"
    physical.mkdir()
    subprocess.run(["git", "init", "-q", str(physical)], check=True)
    registered = tmp_path / "registered"
    registered.symlink_to(physical, target_is_directory=True)
    goal = {"id": "local-alias", "repo": str(registered), "coordination": {"write_scope": [f"{registered}/materials/**"]}}
    local = observe_goal_local_workspace(goal, declaration())
    assert local["workspace"]["identity_kind"] == "local_goal"
    assert local["allowed_write_scopes"] == ["materials/**"]
    goal["coordination"]["write_scope"] = [f"{physical}/materials/**"]
    assert observe_goal_local_workspace(goal, declaration())["allowed_write_scopes"] == []
    subprocess.run(["git", "-C", str(physical), "remote", "add", "origin", "https://github.com/example/project.git"], check=True)
    assert observe_goal_local_workspace(goal, declaration()) == {}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("agent_count", [1, 2])
@pytest.mark.parametrize("isolation_policy", [None, False])
@pytest.mark.parametrize("scope_style", ["absolute", "relative_glob"])
def test_real_cli_local_write_guard_replay_and_causal_settlement(
    tmp_path, monkeypatch, provider, agent_count, isolation_policy, scope_style
):
    from canonical_authority_fixture import (
        initialize_canonical_authority,
        isolate_sqlite_runtime,
    )
    from test_quota_settlement_cli import (
        _write_fixture,
        _run_cli,
        _spend_run_count,
        GOAL_ID,
        AGENT_ID,
        TODO_ID,
    )
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from loopx.control_plane.todos.active_state_todo_parser import (
        parse_active_state_todos,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    goal = config["goals"][0]
    goal["coordination"].update(
        registered_agents=[AGENT_ID, "other-worker"][:agent_count],
        write_scope=[f"{project}/materials/**", f"{project}/reports/result.md"],
    )
    if isolation_policy is not None:
        goal["workspace_guard_policy"] = {
            "peer_independent_worktree_required": isolation_policy,
        }
    if scope_style == "relative_glob":
        goal["coordination"]["write_scope"] = ["material*/**", "reports/*.md"]
    registry.write_text(json.dumps(config))
    state = project / goal["state_file"]
    todos = parse_active_state_todos(state.read_text(), item_limit=None)["agent_todos"][
        "items"
    ]
    todos[0].update(
        {
            **declaration(),
            "todo_id": TODO_ID,
            "claimed_by": AGENT_ID,
            "required_capabilities": ["filesystem_read", "filesystem_write", "shell"],
        }
    )
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=todos, handoff_mode="soft_claim"
        ),
        state_path=state,
        provider=provider,
    )
    caps = [
        "--available-capability",
        "filesystem_read",
        "--available-capability",
        "filesystem_write",
        "--available-capability",
        "shell",
    ]
    turn = f"local-writes-{provider}"
    guard_args = [
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
        "--turn-instance-id",
        turn,
        *caps,
    ]
    rc, outside = _run_cli(registry, runtime, *guard_args, cwd=tmp_path)
    assert rc == 0 and outside["normal_delivery_allowed"] is True, outside
    assert not outside.get("workspace_guard"), outside
    assert outside["selected_todo"]["required_write_scopes"] == todos[0]["required_write_scopes"]
    hint = outside["interaction_contract"]["cli_channel"]["delivery_workspace_causality"]["refresh"]
    assert "--delivery-workspace-path" in hint
    assert _spend_run_count(runtime) == 0
    rc, guard = _run_cli(registry, runtime, *guard_args, cwd=project)
    assert rc == 0 and guard["normal_delivery_allowed"] is True, guard
    assert (
        guard["selected_todo"]["required_write_scopes"]
        == todos[0]["required_write_scopes"]
    )
    rc, replay = _run_cli(registry, runtime, *guard_args, cwd=project)
    assert (
        rc == 0
        and replay["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
        == guard["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"]
    )
    binding = [
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--todo-id",
        TODO_ID,
        "--turn-instance-id",
        turn,
        *caps,
    ]
    artifact = project / "reports" / "result.md"
    artifact.parent.mkdir()
    artifact.write_text("Validated local output.\n")
    rc, writeback = _run_cli(
        registry,
        runtime,
        "refresh-state",
        *binding,
        "--classification",
        "local_material_validated",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "outcome_progress",
        "--delivery-boundary",
        "in_flight_continuation",
        "--progress-result-class",
        "advanced",
        "--progress-surface-id",
        "material:source-review",
        "--delivery-workspace-path",
        str(project),
        cwd=tmp_path,
    )
    assert rc == 0 and writeback["appended"] is True, writeback
    assert writeback["delivery_workspace"]["identity_kind"] == "local_goal"
    assert str(project) not in json.dumps(writeback["delivery_workspace"])
    spend = [
        "quota",
        "spend-slot",
        *binding,
        "--slots",
        "1",
        "--source",
        "heartbeat",
        "--execute",
    ]
    rc, settled = _run_cli(registry, runtime, *spend, cwd=tmp_path)
    assert rc == 0 and settled["ok"] is True, settled
    rc, repeated = _run_cli(registry, runtime, *spend, cwd=tmp_path)
    assert rc == 0 and repeated["ok"] is True, repeated
    assert repeated["idempotent_replay"] is True
    assert repeated["appended"] is False
    assert _spend_run_count(runtime) == 1
