from __future__ import annotations

import json
from pathlib import Path

from loopx.control_plane.agent_context import project_goal_agent_context
from loopx.control_plane.collaboration import delegation_context
from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision
from loopx.control_plane.testing.quota_fixtures import quota_status_payload


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    config_dir = project / ".loopx" / "config"
    config_dir.mkdir(parents=True)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": "goal-a",
                        "repo": str(project),
                        "status": "active",
                        "coordination": {
                            "registered_agents": ["coordinator", "worker"]
                        },
                    }
                ],
            }
        )
    )
    config = config_dir / "delegations.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "loopx_local_delegation_v0",
                "bindings": [
                    {
                        "id": "independent-review",
                        "agent_id": "worker",
                        "todo_id": "todo-review",
                        "requesters": ["coordinator"],
                        "workspace": str(tmp_path / "worker"),
                        "host_args": ["--host", "generic-cli"],
                        "timeout_seconds": 60,
                        "output_refs": ["result.json"],
                    }
                ],
            }
        )
    )
    return project, registry, tmp_path / "runtime"


def test_projects_authorized_route_without_private_binding_material(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    from loopx.collaboration_mcp import Delegations

    monkeypatch.setattr(
        Delegations,
        "operations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("planning must not read operation inventory")
        ),
    )
    monkeypatch.setattr(
        delegation_context,
        "managed_executor_binding_from_host_args",
        lambda *_args, **_kwargs: {
            "executor": "managed-runtime",
            "executor_kind": "managed",
            "execution_profile": "model-a@high",
            "available": True,
            "unavailable_reason": None,
        },
    )
    packet = delegation_context.project_delegation_context(
        runtime_root=runtime,
        registry_path=registry,
        goal_id="goal-a",
        agent_id="coordinator",
        project=project,
        execution_config=".loopx/config/delegations.json",
    )
    assert packet["configuration_state"] == "ready"
    assert packet["authorized_count"] == packet["projected_count"] == 1
    assert "operation_receipts" not in packet
    assert packet["routes"] == [
        {
            "binding_id": "independent-review",
            "agent_id": "worker",
            "todo_id": "todo-review",
            "runtime_id": "managed-runtime",
            "executor_kind": "managed",
            "readiness": "ready",
            "execution_profile": "model-a@high",
        }
    ]
    encoded = json.dumps(packet)
    assert str(project) not in encoded
    assert "host_args" not in encoded
    assert "output_refs" not in encoded


def test_operation_receipts_require_explicit_result_phase_read(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    from loopx.collaboration_mcp import Delegations

    calls = []

    def operations(_self, *, limit, cursor=None):
        calls.append({"limit": limit, "cursor": cursor})
        return {
            "items": [
                {"status": "running"},
                {"status": "rejected", "recovery_required": True},
            ],
            "has_more": True,
        }

    monkeypatch.setattr(Delegations, "operations", operations)
    packet = delegation_context.project_delegation_context(
        runtime_root=runtime,
        registry_path=registry,
        goal_id="goal-a",
        agent_id="coordinator",
        project=project,
        execution_config=".loopx/config/delegations.json",
        include_operation_receipts=True,
    )

    assert calls == [{"limit": 10, "cursor": None}]
    assert packet["operation_receipts"] == {
        "observed": 2,
        "running": 1,
        "rejected": 1,
        "recovery_required": 1,
        "has_more": True,
    }


def test_repeated_live_quota_planning_never_reads_operation_inventory(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    payload = json.loads(registry.read_text())
    policy = {
        "mode": "multi_subagent",
        "allowed": True,
        "max_children": 2,
        "execution_config": ".loopx/config/delegations.json",
    }
    payload["goals"][0]["spawn_policy"] = policy
    registry.write_text(json.dumps(payload))
    from loopx.collaboration_mcp import Delegations

    monkeypatch.setattr(
        Delegations,
        "operations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("quota planning must not read operation inventory")
        ),
    )
    status = quota_status_payload(
        goal_id="goal-a",
        status="active",
        recommended_action="Inspect delegated evidence",
        coordination={"registered_agents": ["coordinator", "worker"]},
        agent_todo_items=[
            {
                "todo_id": "todo_review0001",
                "index": 1,
                "text": "Inspect delegated evidence",
                "status": "open",
                "priority": "P1",
                "role": "agent",
                "task_class": "advancement_task",
            }
        ],
        goal_extra={"repo": str(project), "spawn_policy": policy},
    )
    kwargs = {
        "goal_id": "goal-a",
        "agent_id": "coordinator",
        "available_capabilities": ["shell", "subagent_spawn"],
        "include_scheduler_detail": False,
        "codex_app_current_rrule": None,
        "registry_path": registry,
        "runtime_root": runtime,
        "scheduler_execution_context": {
            "host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop",
            "execution_mode": "interactive",
        },
    }

    for _ in range(2):
        packet = build_live_quota_should_run_decision(status, **kwargs)
        facts = packet["interaction_contract"]["agent_context"]["contributions"][
            0
        ]["facts"]
        assert facts["delegation_context"]["projected_count"] == 1
        assert "operation_receipts" not in facts["delegation_context"]


def test_missing_config_is_blocked_observation_not_empty_success(tmp_path: Path) -> None:
    project, registry, runtime = _fixture(tmp_path)
    packet = delegation_context.project_delegation_context(
        runtime_root=runtime,
        registry_path=registry,
        goal_id="goal-a",
        agent_id="coordinator",
        project=project,
        execution_config=".loopx/config/missing.json",
    )
    assert packet["configuration_state"] == "blocked"
    assert packet["reason_code"] == "delegation_context_unavailable"
    assert packet["routes"] == []


def test_symlinked_config_is_blocked_even_when_target_is_inside_project(
    tmp_path: Path,
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    config = project / ".loopx" / "config" / "delegations.json"
    real_config = config.with_name("delegations-real.json")
    config.rename(real_config)
    config.symlink_to(real_config)

    packet = delegation_context.project_delegation_context(
        runtime_root=runtime,
        registry_path=registry,
        goal_id="goal-a",
        agent_id="coordinator",
        project=project,
        execution_config=".loopx/config/delegations.json",
    )

    assert packet["configuration_state"] == "blocked"
    assert packet["reason_code"] == "delegation_context_unavailable"


def test_disabled_capability_does_not_read_retained_execution_config(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    monkeypatch.setattr(
        delegation_context,
        "project_delegation_context",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not read")),
    )

    context = project_goal_agent_context(
        phase="before_plan",
        scope={"goal_id": "goal-a", "agent_id": "coordinator", "todo_id": None},
        goal={
            "id": "goal-a",
            "repo": str(project),
            "spawn_policy": {
                "mode": "multi_subagent",
                "allowed": False,
                "max_children": 2,
                "execution_config": ".loopx/config/delegations.json",
            },
        },
        registry_path=registry,
        runtime_root=runtime,
    )

    assert context is None


def test_enabled_capability_without_execution_config_keeps_existing_context_shape(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    monkeypatch.setattr(
        delegation_context,
        "project_delegation_context",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not read")),
    )

    context = project_goal_agent_context(
        phase="before_plan",
        scope={"goal_id": "goal-a", "agent_id": "coordinator", "todo_id": None},
        goal={
            "id": "goal-a",
            "repo": str(project),
            "spawn_policy": {
                "mode": "multi_subagent",
                "allowed": True,
                "max_children": 2,
            },
        },
        registry_path=registry,
        runtime_root=runtime,
    )

    assert context is not None
    facts = context["contributions"][0]["facts"]
    assert "delegation_context" not in facts
