from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.capabilities.goal_inspection import (
    inspect_goal_capabilities,
    render_goal_capabilities,
)
from loopx.chat_goal_configuration_api import GoalConfigurationRequestMixin
from loopx.cli import main


def fixture_registry(tmp_path: Path, *, enabled: bool = True) -> Path:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "fixture-goal",
                        "repo": str(tmp_path),
                        "status": "active",
                        "registered_agents": ["coordinator"],
                        "spawn_policy": {
                            "mode": "multi_subagent" if enabled else "single_agent",
                            "spawn_allowed": enabled,
                            "max_children": 2 if enabled else 0,
                        },
                    }
                ],
            }
        )
    )
    return registry


class SettingsHandler(GoalConfigurationRequestMixin):
    def __init__(self, registry, runtime_root):
        self.server = SimpleNamespace(registry_path=registry, runtime_root=runtime_root)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "phase", ["before_plan", "before_delegate", "after_delegate_result"]
)
def test_real_cli_matches_dashboard_and_does_not_write(
    tmp_path, capsys, enabled, phase
):
    registry = fixture_registry(tmp_path, enabled=enabled)
    before = registry.read_bytes()
    assert (
        main(
            [
                "--registry",
                str(registry),
                "--format",
                "json",
                "capability",
                "inspect",
                "--goal-id",
                "fixture-goal",
                "--agent-id",
                "coordinator",
                "--phase",
                phase,
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    settings = SettingsHandler(
        registry, tmp_path / "runtime"
    )._read_public_goal_configuration("fixture-goal")
    assert result["configuration"] == settings
    assert result["read_only"] is True
    assert result["authority"] == "configuration_and_guidance_only"
    assert (
        result["read_consistency"]
        == "independent_owner_reads_not_an_execution_snapshot"
    )
    assert result["not_observed"] == [
        "invocation_readiness",
        "execution",
        "adoption",
        "outcome_utility",
    ]
    assert result["agent_context_status"] == (
        "projected" if enabled else "no_contribution"
    )
    if enabled:
        context = result["agent_context"]
        assert context["phase"] == phase
        assert context["scope"] == {
            "goal_id": "fixture-goal",
            "agent_id": "coordinator",
            "todo_id": None,
        }
        assert context["contributions"][0]["capability_id"] == "multi_subagent"
    else:
        assert result["agent_context"] is None
    assert registry.read_bytes() == before
    assert not (tmp_path / "runtime").exists()
    assert "not readiness, execution, adoption or utility" in render_goal_capabilities(
        result
    )


def test_configuration_only_does_not_call_context_owner(tmp_path, monkeypatch):
    from loopx.capabilities import goal_inspection

    registry = fixture_registry(tmp_path)

    def unexpected(**kwargs):
        raise AssertionError("Explicit phase required; no automatic context inspection")

    monkeypatch.setattr(goal_inspection, "project_goal_agent_context", unexpected)
    result = inspect_goal_capabilities(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
    )
    assert result["agent_context_status"] == "not_requested"
    assert result["agent_context"] is None


@pytest.mark.parametrize(
    "arguments",
    [
        ["--agent-id", "coordinator"],
        ["--phase", "before_plan"],
        ["--agent-id", "stranger", "--phase", "before_plan"],
    ],
)
def test_invalid_scope_is_not_an_empty_success(tmp_path, capsys, arguments):
    registry = fixture_registry(tmp_path)
    assert (
        main(
            [
                "--registry",
                str(registry),
                "--format",
                "json",
                "capability",
                "inspect",
                "--goal-id",
                "fixture-goal",
                *arguments,
            ]
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["read_only"] is True


def test_stale_global_projection_does_not_choose_context_or_membership(tmp_path):
    registry = fixture_registry(tmp_path)
    projection = tmp_path / "registry.global.json"
    projection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "registry_role": "global-local",
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": "fixture-goal",
                        "repo": str(tmp_path),
                        "status": "active",
                        "source_registry": str(registry),
                        "registered_agents": ["stale-agent"],
                        "spawn_policy": {
                            "mode": "single_agent",
                            "spawn_allowed": False,
                            "max_children": 0,
                        },
                    }
                ],
            }
        )
    )
    result = inspect_goal_capabilities(
        registry_path=projection,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="coordinator",
        phase="before_plan",
    )
    assert result["agent_context_status"] == "projected"
    assert result["configuration"] == SettingsHandler(
        projection, tmp_path / "runtime"
    )._read_public_goal_configuration("fixture-goal")
    with pytest.raises(ValueError, match="not registered"):
        inspect_goal_capabilities(
            registry_path=projection,
            runtime_root=tmp_path / "runtime",
            goal_id="fixture-goal",
            agent_id="stale-agent",
            phase="before_plan",
        )


def test_context_failure_is_visible_and_not_changed_to_disabled(
    tmp_path, monkeypatch, capsys
):
    from loopx.capabilities import goal_inspection

    registry = fixture_registry(tmp_path)

    def unavailable(**kwargs):
        raise RuntimeError("context runtime unavailable")

    monkeypatch.setattr(goal_inspection, "project_goal_agent_context", unavailable)
    assert (
        main(
            [
                "--registry",
                str(registry),
                "--format",
                "json",
                "capability",
                "inspect",
                "--goal-id",
                "fixture-goal",
                "--agent-id",
                "coordinator",
                "--phase",
                "before_plan",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error"] == "context runtime unavailable"


def test_live_machine_defaults_and_goal_override_keep_dashboard_revision(tmp_path):
    from loopx.capabilities.machine_configuration.builtins import (
        build_builtin_machine_configuration_registry,
    )
    from loopx.capabilities.machine_configuration.store import (
        configure_machine_configuration,
    )
    from loopx.configure_goal import configure_goal

    registry = fixture_registry(tmp_path)
    runtime_root = tmp_path / "runtime"

    def inspect():
        return inspect_goal_capabilities(
            registry_path=registry, runtime_root=runtime_root, goal_id="fixture-goal"
        )["configuration"]

    def cadence(packet):
        return next(
            item
            for item in packet["capability_catalog"]["capabilities"]
            if item["capability_id"] == "todo_replan_cadence"
        )["effective_configuration"]

    initial = inspect()
    machine_registry = build_builtin_machine_configuration_registry()
    configuration = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "todo_replan_cadence": {
                "schema_version": "todo_replan_cadence_machine_defaults_v0",
                "completed_todos": 2,
            }
        },
    }
    preview = configure_machine_configuration(
        runtime_root=runtime_root,
        registry=machine_registry,
        configuration=configuration,
    )
    configure_machine_configuration(
        runtime_root=runtime_root,
        registry=machine_registry,
        configuration=configuration,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )
    inherited = inspect()
    assert cadence(inherited)["source"] == "machine_default"
    assert cadence(inherited)["configuration"]["completed_todos"] == 2
    # The Goal revision fences overrides; each effective revision also includes
    # live machine defaults. Do not invent a second revision policy here.
    assert (
        cadence(inherited)["effective_revision"]
        != cadence(initial)["effective_revision"]
    )
    configure_goal(
        registry_path=registry,
        goal_id="fixture-goal",
        execution_replan_after_todos=3,
        execute=True,
    )
    overridden = inspect()
    assert cadence(overridden)["source"] == "goal_override"
    assert cadence(overridden)["configuration"]["completed_todos"] == 3
    assert overridden["revision"] != inherited["revision"]
    assert overridden == SettingsHandler(
        registry, runtime_root
    )._read_public_goal_configuration("fixture-goal")


def test_partial_context_provider_failure_is_not_hidden(tmp_path, monkeypatch):
    from loopx.capabilities import goal_inspection

    registry = fixture_registry(tmp_path)
    context = {"contributions": [], "failures": [{"reason": "provider_failed"}]}
    monkeypatch.setattr(
        goal_inspection, "project_goal_agent_context", lambda **kwargs: context
    )
    result = inspect_goal_capabilities(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id="fixture-goal",
        agent_id="coordinator",
        phase="before_plan",
    )
    assert result["agent_context"] == context
    assert "failures are present" in render_goal_capabilities(result)
