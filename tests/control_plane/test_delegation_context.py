from __future__ import annotations

import json
from pathlib import Path

import pytest

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
                        "todo_id": "todo_review0002",
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


def test_runtime_availability_does_not_admit_uninspected_delegation(
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
    assert packet["preflight"] == "required"
    assert packet["execution_scope"] == "bound_delegation"
    assert packet["authorized_count"] == packet["projected_count"] == 1
    assert "operation_receipts" not in packet
    assert packet["routes"] == [
        {
            "binding_id": "independent-review",
            "agent_id": "worker",
            "todo_id": "todo_review0002",
            "runtime_id": "managed-runtime",
            "executor_kind": "managed",
            "runtime_readiness": "ready",
            "readiness": "unknown",
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


@pytest.mark.parametrize("runner_configured", [False, True])
def test_planning_retains_original_probe_scope_and_remediation_without_admission(
    tmp_path: Path, monkeypatch, runner_configured: bool
) -> None:
    from loopx.control_plane.turn_driver.host_binding import managed_executor_binding

    project, registry, runtime = _fixture(tmp_path)
    calls = []
    executor = managed_executor_binding(
        "dsh", environ={}, module_probe=lambda _: False,
        dsh_runner_configured=runner_configured,
    )

    def binding(*args, **_kwargs):
        calls.append(args)
        return {**executor, "credential_env": "PRIVATE_CREDENTIAL",
                "endpoint_env": "PRIVATE_ENDPOINT"}

    monkeypatch.setattr(delegation_context, "managed_executor_binding_from_host_args", binding)
    packet = delegation_context.project_delegation_context(
        runtime_root=runtime, registry_path=registry, goal_id="goal-a",
        agent_id="coordinator", project=project, execution_config=".loopx/config/delegations.json",
    )
    route = packet["routes"][0]
    assert len(calls) == 1
    assert route["runtime_probe"] == executor["runtime_probe"]
    assert route["unavailable_remediation"] == executor["unavailable_remediation"]
    assert route["runtime_readiness"] == ("ready" if runner_configured else "blocked")
    assert route["readiness"] == ("unknown" if runner_configured else "blocked")
    assert packet["preflight"] == "required"
    assert "operation_receipts" not in packet
    assert "PRIVATE_" not in json.dumps(packet)


def test_unprobed_generic_route_keeps_explicit_null_probe(tmp_path: Path) -> None:
    project, registry, runtime = _fixture(tmp_path)
    packet = delegation_context.project_delegation_context(
        runtime_root=runtime, registry_path=registry, goal_id="goal-a",
        agent_id="coordinator", project=project, execution_config=".loopx/config/delegations.json",
    )
    route = packet["routes"][0]
    assert route["runtime_probe"] is None
    assert route["unavailable_remediation"] == []
    assert route["runtime_readiness"] == route["readiness"] == "unknown"


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
    coordination = {"registered_agents": ["coordinator", "worker"],
                    "peer_task_coordination": {"coordinator_agent_id": "coordinator"}}
    payload["goals"][0]["coordination"] = coordination
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
        coordination=coordination,
        agent_todo_items=[
            {
                "todo_id": "todo_review0001",
                "index": 1,
                "text": "Inspect delegated evidence",
                "status": "open",
                "priority": "P1",
                "role": "agent",
                "task_class": "advancement_task",
            },
            {"todo_id": "todo_old_peer", "status": "open", "priority": "P0",
             "role": "agent", "task_class": "advancement_task", "claimed_by": "worker", "text": "Older still-open task"},
            {"todo_id": "todo_review0002", "status": "open", "priority": "P0",
             "role": "agent", "task_class": "advancement_task", "claimed_by": "worker", "text": "Bound review task"},
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
        route = facts["delegation_context"]["routes"][0]
        assert route["todo_id"] == "todo_review0002"
        assert route["readiness"] == "unknown"
        assert facts["delegation_context"]["preflight"] == "required"
        peer = packet["task_orchestration_contract"]
        assert peer["execution_scope"] == "peer_agent_activation"
        assert peer["execution_state"] == "blocked"
        assert {row["todo_id"] for row in peer["blocked_peer_lanes"]} == {"todo_old_peer", "todo_review0002"}
        assert packet["should_run"] is True
        from loopx.control_plane.quota.turn_envelope import build_turn_envelope
        from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan

        envelope = build_turn_envelope(packet)
        planned = build_loopx_turn_plan(envelope, host="codex-cli", execution_mode="interactive-visible")
        # The parent Turn owns its selected Todo; peer candidates and the bound
        # delegation target must not silently replace the coordinator's identity.
        assert planned["route"]["selected_todo"]["todo_id"] == "todo_review0001"


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
