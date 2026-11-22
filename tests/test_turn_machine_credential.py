"""Goal runtimes share machine authentication, never execution grants."""
from __future__ import annotations

import contextlib
import io
import json

import pytest

from loopx.cli import main
from loopx.control_plane import operator_provider as provider
from loopx import paths
from loopx.control_plane.agent_context import project_goal_agent_context
from loopx.control_plane.collaboration.delegation_context import read_delegation_facts
from loopx.control_plane.turn_driver import host_binding
from loopx.dsh_goal_mode import turn_host_adapter
from tests.control_plane.test_delegation_context import _fixture as delegation_fixture
from tests.test_loopx_turn_driver import _write_live_fixture

KEY = "machine-credential-fixture"
LEGACY_KEY = "legacy-machine-credential-fixture"
GOAL_KEY = "goal-must-not-select-this-credential"


@pytest.fixture
def machine(tmp_path, monkeypatch):
    root = tmp_path / "machine-owner"
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", root)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", tmp_path / "absent-legacy-runtime")
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "LOOPX_TURN_HOST"):
        monkeypatch.delenv(name, raising=False)
    # SDK availability is independent of credential ownership; no model is called.
    monkeypatch.setattr(host_binding.importlib.util, "find_spec", lambda _: True)
    provider.write_operator_provider(runtime_root=root, api_key=KEY)
    return root


def turn(project, runtime, registry, *options, action="plan"):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main([
            "--registry", str(registry), "--runtime-root", str(runtime),
            "--format", "json", "turn", action,
            "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
             *options,
        ])
    payload = json.loads(output.getvalue())
    assert KEY not in output.getvalue()
    assert LEGACY_KEY not in output.getvalue()
    assert GOAL_KEY not in output.getvalue()
    return code, payload


def test_two_goal_runtimes_use_machine_credential_for_plan_and_dispatch(tmp_path, machine, monkeypatch):
    received = []
    expected = []

    def sdk_runner(**kwargs):
        received.append(kwargs["env"])
        return json.dumps({
            "result_kind": "iteration_failed", "classification": "fixture_stopped",
            "summary": "Bounded credential propagation check completed.",
            "next_action": "No further model work requested.",
        })

    monkeypatch.setattr(turn_host_adapter, "run_dsh_turn", sdk_runner)
    for name in ("first", "second"):
        project, runtime, registry = _write_live_fixture(tmp_path / name)
        # A conflicting Goal-local store cannot redirect machine authentication.
        provider.write_operator_provider(runtime_root=runtime, api_key=GOAL_KEY)
        code, plan = turn(project, runtime, registry)
        assert code == 0
        assert plan["managed_executor"]["executor"] == "dsh"
        assert plan["managed_executor"]["available"] is True
        code, result = turn(project, runtime, registry, "--project", str(project),
                            "--turn-instance-id", name, "--no-global-sync", "--execute",
                            action="run-once")
        assert code == 0, result
        assert result["result_kind"] == "iteration_failed"
        assert result["effects"]["quota_spent"] is False
        expected.append({
            "DEEPSEEK_API_KEY": KEY,
            "LOOPX_TURN_KEY": result["receipt"]["turn_key"],
            "LOOPX_TURN_GOAL_ID": "loopx-turn-fixture",
            "LOOPX_TURN_AGENT_ID": "codex-fixture",
            "LOOPX_TURN_TODO_ID": "todo_fixture0001",
            "LOOPX_TURN_WORKSPACE": str(project.resolve()),
        })
    assert received == expected


def test_invalid_machine_store_cannot_fall_back_to_goal_or_environment(tmp_path, machine, monkeypatch):
    project, runtime, registry = _write_live_fixture(tmp_path / "goal")
    provider.write_operator_provider(runtime_root=runtime, api_key=GOAL_KEY)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-fixture-key")
    provider.operator_provider_store_path(machine).write_text('{"schema_version":"invalid"}')
    _, plan = turn(project, runtime, registry, "--host", "dsh")
    assert plan["managed_executor"]["available"] is False
    assert plan["managed_executor"]["unavailable_reason"] == "operator_credential_unconfigured"


@pytest.mark.parametrize("alias_parent", [False, True], ids=["direct-parent", "symlink-parent"])
def test_explicit_machine_binding_disambiguates_dual_default_roots(
    tmp_path, machine, monkeypatch, alias_parent,
):
    machine_home = tmp_path / "operator-home"
    current = _write_live_fixture(machine_home / "current")
    legacy = _write_live_fixture(machine_home / "legacy")
    route_home = machine_home
    if alias_parent:
        route_home = tmp_path / "operator-home-link"
        route_home.symlink_to(machine_home, target_is_directory=True)
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", route_home / "current" / "runtime")
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", route_home / "legacy" / "runtime")
    provider.write_operator_provider(runtime_root=current[1], api_key=KEY)
    provider.write_operator_provider(runtime_root=legacy[1], api_key=LEGACY_KEY)
    assert paths.default_runtime_route()["status"] == paths.RuntimeRouteStatus.CONFLICT

    host_options = ("--host", "dsh", "--execution-mode", "isolated-headless")
    for (project, runtime, registry), configured_root, key in (
        (current, paths.DEFAULT_RUNTIME_ROOT, KEY),
        (legacy, paths.LEGACY_RUNTIME_ROOT, LEGACY_KEY),
    ):
        code, plan = turn(project, runtime, registry, *host_options)
        assert code == 0, plan.get("error")
        assert plan["managed_executor"]["available"] is True, plan["managed_executor"]
        credential_environ = provider.operator_provider_environ(
            bound_runtime_root=configured_root, environ={},
        )
        assert credential_environ["DEEPSEEK_API_KEY"] == key

    other_project, other_runtime, other_registry = _write_live_fixture(tmp_path / "other")
    provider.write_operator_provider(runtime_root=other_runtime, api_key=GOAL_KEY)
    _, plan = turn(other_project, other_runtime, other_registry, *host_options)
    assert plan["managed_executor"]["unavailable_reason"] == "operator_credential_unconfigured"


def test_explicit_binding_cannot_select_redirected_default_root(tmp_path, machine):
    project, runtime, registry = _write_live_fixture(tmp_path / "legacy")
    provider.write_operator_provider(runtime_root=runtime, api_key=LEGACY_KEY)
    paths.LEGACY_RUNTIME_ROOT.symlink_to(runtime, target_is_directory=True)
    assert paths.default_runtime_route()["status"] == paths.RuntimeRouteStatus.INVALID

    code, plan = turn(
        project, runtime, registry, "--host", "dsh", "--execution-mode", "isolated-headless",
    )
    assert code == 0, plan.get("error")
    assert plan["managed_executor"]["unavailable_reason"] == "operator_credential_unconfigured"


def test_explicit_codex_selection_survives_machine_credential(tmp_path, machine):
    project, runtime, registry = _write_live_fixture(tmp_path / "goal")
    _, plan = turn(project, runtime, registry, "--host", "codex-cli")
    assert plan["managed_executor"]["executor"] == "codex-cli"


def test_delegation_uses_machine_readiness_without_expanding_requesters(tmp_path, machine):
    project, registry, runtime = delegation_fixture(tmp_path / "goal")
    config = project / ".loopx/config/delegations.json"
    data = json.loads(config.read_text())
    data["bindings"][0]["host_args"] = ["--host", "dsh"]
    config.write_text(json.dumps(data))
    goal = json.loads(registry.read_text())["goals"][0]
    goal["spawn_policy"] = {"mode": "multi_subagent", "allowed": True, "max_children": 2,
                            "execution_config": ".loopx/config/delegations.json"}
    for requester, count in (("coordinator", 1), ("worker", 0)):
        facts = read_delegation_facts(
            runtime_root=runtime, registry_path=registry, goal_id="goal-a",
            agent_id=requester, project=project,
            execution_config=".loopx/config/delegations.json",
        )
        assert KEY not in json.dumps(facts)
        context = project_goal_agent_context(
            phase="before_plan", scope={"goal_id": "goal-a", "agent_id": requester, "todo_id": None},
            goal=goal, registry_path=registry, runtime_root=runtime,
        )
        packet = context["contributions"][0]["facts"]["delegation_context"]
        assert packet["authorized_count"] == count
        if count:
            route = packet["routes"][0]
            assert route["runtime_readiness"] == "ready"
            assert route["readiness"] == "unknown"
            assert packet["preflight"] == "required"
        assert KEY not in json.dumps(packet)
    assert not provider.operator_provider_store_path(runtime).exists()
