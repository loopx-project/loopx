"""Host I/O guards for the delegation facts reader.

Classification of these facts into ``loopx_delegation_context_v0`` is owned by
``loopx/control_plane/subagent_context.ts`` and covered by
``tests/control_plane_ts/agent_context.test.ts``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.agent_context import project_goal_agent_context
from loopx.control_plane.collaboration import delegation_context
from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision
from loopx.control_plane.testing.quota_fixtures import quota_status_payload

POLICY = {
    "mode": "multi_subagent",
    "allowed": True,
    "max_children": 2,
    "execution_config": ".loopx/config/delegations.json",
}


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


def _facts(project: Path, registry: Path, runtime: Path, **overrides):
    return delegation_context.read_delegation_facts(
        runtime_root=runtime, registry_path=registry, goal_id="goal-a",
        agent_id="coordinator", project=project,
        execution_config=".loopx/config/delegations.json", **overrides,
    )


def _goal(project: Path, **policy) -> dict:
    return {"id": "goal-a", "repo": str(project), "spawn_policy": {**POLICY, **policy}}


def _delegation_context(project: Path, registry: Path, runtime: Path, phase: str) -> dict:
    context = project_goal_agent_context(
        phase=phase,
        scope={"goal_id": "goal-a", "agent_id": "coordinator", "todo_id": None},
        goal=_goal(project), registry_path=registry, runtime_root=runtime,
    )
    facts = context["contributions"][0]["facts"]
    return facts["delegation_context"] if phase != "after_delegate_result" else facts["delegation_receipts"]


def test_planning_reads_raw_facts_without_operation_inventory(tmp_path: Path, monkeypatch) -> None:
    from loopx.collaboration_mcp import Delegations

    monkeypatch.setattr(
        Delegations,
        "operations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("planning must not read operation inventory")
        ),
    )
    calls = []

    def binding(host_args, **_kwargs):
        calls.append(list(host_args))
        return {"executor": "managed-runtime", "available": True}

    monkeypatch.setattr(delegation_context, "managed_executor_binding_from_host_args", binding)
    facts = _facts(*_fixture(tmp_path))
    assert facts["config_error"] is None
    assert facts["config"]["bindings"][0]["id"] == "independent-review"
    assert facts["registered_agent_ids"] == ["coordinator", "worker"]
    assert calls == [["--host", "generic-cli"]]
    assert facts["executors"] == [
        {"host_args": ["--host", "generic-cli"], "executor": {"executor": "managed-runtime", "available": True}}
    ]
    assert facts["operation_inventory"] is None


def test_reader_probes_each_distinct_host_argv_once_within_the_route_budget(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    config = project / ".loopx" / "config" / "delegations.json"
    data = json.loads(config.read_text())
    template = data["bindings"][0]
    data["bindings"] = [
        {**template, "id": f"route-{index}",
         "host_args": ["--host", "generic-cli"] if index % 2 else ["--host", "codex-cli"]}
        for index in range(9)
    ] + [{**template, "id": "foreign", "requesters": ["someone-else"], "host_args": ["--host", "dsh"]}]
    config.write_text(json.dumps(data))
    calls = []
    monkeypatch.setattr(
        delegation_context, "managed_executor_binding_from_host_args",
        lambda host_args, **_kwargs: calls.append(list(host_args)) or {"executor": host_args[1]},
    )
    facts = _facts(project, registry, runtime)
    assert calls == [["--host", "codex-cli"], ["--host", "generic-cli"]]
    assert [row["executor"]["executor"] for row in facts["executors"]] == ["codex-cli", "generic-cli"]


def test_operation_inventory_is_read_once_only_for_the_result_phase(
    tmp_path: Path, monkeypatch
) -> None:
    from loopx.collaboration_mcp import Delegations

    calls = []

    def operations(_self, *, limit, cursor=None):
        calls.append({"limit": limit, "cursor": cursor})
        return {"items": [{"status": "running"}], "has_more": True, "private": "detail"}

    monkeypatch.setattr(Delegations, "operations", operations)
    facts = _facts(*_fixture(tmp_path), include_operation_inventory=True)
    assert calls == [{"limit": 10, "cursor": None}]
    assert facts["operation_inventory"] == {"items": [{"status": "running"}], "has_more": True}


def test_repeated_live_quota_planning_never_reads_operation_inventory(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    payload = json.loads(registry.read_text())
    payload["goals"][0]["spawn_policy"] = POLICY
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
        goal_extra={"repo": str(project), "spawn_policy": POLICY},
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
    (project / ".loopx" / "config" / "delegations.json").unlink()
    assert _facts(project, registry, runtime)["config_error"] == "unavailable"
    context = project_goal_agent_context(
        phase="before_plan",
        scope={"goal_id": "goal-a", "agent_id": "coordinator", "todo_id": None},
        goal=_goal(project, execution_config=".loopx/config/missing.json"),
        registry_path=registry, runtime_root=runtime,
    )
    packet = context["contributions"][0]["facts"]["delegation_context"]
    assert packet["configuration_state"] == "blocked"
    assert packet["reason_code"] == "delegation_context_unavailable"
    assert packet["routes"] == []


@pytest.mark.parametrize("phase", ["before_plan", "after_delegate_result"])
def test_symlinked_config_is_blocked_even_when_target_is_inside_project(
    tmp_path: Path, phase: str
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    config = project / ".loopx" / "config" / "delegations.json"
    real_config = config.with_name("delegations-real.json")
    config.rename(real_config)
    config.symlink_to(real_config)

    facts = _facts(project, registry, runtime,
                   include_operation_inventory=phase == "after_delegate_result")
    assert facts["config"] is None
    assert facts["config_error"] == "unavailable"
    assert facts["operation_inventory"] is None

    packet = _delegation_context(project, registry, runtime, phase)
    assert packet["configuration_state"] == "blocked"
    if phase == "after_delegate_result":
        assert packet["operation_receipts"] == {"observed": 0}
    else:
        assert packet["reason_code"] == "delegation_context_unavailable"


def test_disabled_capability_does_not_read_retained_execution_config(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    monkeypatch.setattr(
        delegation_context,
        "read_delegation_facts",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not read")),
    )

    context = project_goal_agent_context(
        phase="before_plan",
        scope={"goal_id": "goal-a", "agent_id": "coordinator", "todo_id": None},
        goal=_goal(project, allowed=False),
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
        "read_delegation_facts",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not read")),
    )
    goal = _goal(project)
    del goal["spawn_policy"]["execution_config"]

    context = project_goal_agent_context(
        phase="before_plan",
        scope={"goal_id": "goal-a", "agent_id": "coordinator", "todo_id": None},
        goal=goal,
        registry_path=registry,
        runtime_root=runtime,
    )

    assert context is not None
    facts = context["contributions"][0]["facts"]
    assert "delegation_context" not in facts


def test_oversized_config_is_blocked_not_an_unbounded_read(tmp_path: Path) -> None:
    project, registry, runtime = _fixture(tmp_path)
    config = project / ".loopx" / "config" / "delegations.json"
    payload = json.loads(config.read_text())
    payload["padding"] = "x" * 129_000
    config.write_text(json.dumps(payload))
    facts = _facts(project, registry, runtime)
    assert facts["config"] is None
    assert facts["config_error"] == "unavailable"
    packet = _delegation_context(project, registry, runtime, "before_plan")
    assert packet["configuration_state"] == "blocked"
    assert packet["reason_code"] == "delegation_context_unavailable"


def test_missing_registry_marks_registration_unknown_without_blocking_config(
    tmp_path: Path,
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    registry.unlink()
    facts = _facts(project, registry, runtime)
    assert facts["config_error"] is None
    assert facts["config"] is not None
    assert facts["registered_agent_ids"] is None


def test_caller_supplied_delegation_observation_skips_host_reads(
    tmp_path: Path, monkeypatch
) -> None:
    project, registry, runtime = _fixture(tmp_path)
    monkeypatch.setattr(
        delegation_context,
        "read_delegation_facts",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not read")),
    )
    signed = {
        "schema_version": "loopx_delegation_context_v0",
        "configuration_state": "ready",
        "observed_at": "2026-09-19T00:00:00+00:00",
        "authorized_count": 0,
        "projected_count": 0,
        "routes": [],
    }
    context = project_goal_agent_context(
        phase="before_delegate",
        scope={"goal_id": "goal-a", "agent_id": "coordinator", "todo_id": None},
        goal=_goal(project),
        registry_path=registry,
        runtime_root=runtime,
        observations={"delegation_context": signed},
    )
    facts = context["contributions"][0]["facts"]
    assert facts["delegation_context"]["configuration_state"] == "ready"


def test_source_session_registry_blocks_delegation_context(tmp_path: Path) -> None:
    from loopx.control_plane.projects.registry_codec import (
        source_session_registry_transaction,
    )

    project, _, runtime = _fixture(tmp_path)
    registry = tmp_path / "source" / "registry.json"
    payload = {
        "profile_id": "source_session_v1",
        "schema_version": 1,
        "goals": [
            {
                "id": "goal-a",
                "repo": str(project),
                "status": "active",
                "coordination": {"registered_agents": ["coordinator", "worker"]},
            }
        ],
        "session_bindings": [],
        "session_receipts": [],
        "lifetime_receipts": [],
    }
    with source_session_registry_transaction(
        registry,
        operation="test_source_session_delegation_context",
        create=lambda: payload,
    ) as transaction:
        transaction.commit(transaction.payload_copy())
    packet = _delegation_context(project, registry, runtime, "before_plan")
    assert packet["configuration_state"] == "blocked"
    assert packet["reason_code"] == "delegation_context_unavailable"
    assert _facts(project, registry, runtime)["config_error"] == "unavailable"


def test_registry_path_under_a_file_is_unavailable_not_empty(tmp_path: Path) -> None:
    project, _, runtime = _fixture(tmp_path)
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    registry = blocker / "registry.json"
    assert _facts(project, registry, runtime)["config_error"] == "unavailable"
