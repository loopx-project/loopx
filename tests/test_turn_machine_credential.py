"""Goal runtimes share machine authentication, never execution grants."""
from __future__ import annotations

import contextlib
import io
import json

import pytest

from loopx.cli import main
from loopx.control_plane import operator_provider as provider
from loopx import paths
from loopx.control_plane.collaboration.delegation_context import project_delegation_context
from loopx.control_plane.turn_driver import host_binding
from loopx.dsh_goal_mode import turn_host_adapter
from tests.control_plane.test_delegation_context import _fixture as delegation_fixture
from tests.test_loopx_turn_driver import _write_live_fixture

KEY = "machine-credential-fixture"
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
            "--scan-root", str(project), *options,
        ])
    payload = json.loads(output.getvalue())
    assert KEY not in output.getvalue()
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
        expected.append({
            "DEEPSEEK_API_KEY": KEY,
            "LOOPX_TURN_GOAL_ID": "loopx-turn-fixture",
            "LOOPX_TURN_AGENT_ID": "codex-fixture",
            "LOOPX_TURN_TODO_ID": "todo_fixture0001",
            "LOOPX_TURN_WORKSPACE": str(project.resolve()),
        })
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
    assert received == expected


def test_invalid_machine_store_cannot_fall_back_to_goal_or_environment(tmp_path, machine, monkeypatch):
    project, runtime, registry = _write_live_fixture(tmp_path / "goal")
    provider.write_operator_provider(runtime_root=runtime, api_key=GOAL_KEY)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-fixture-key")
    provider.operator_provider_store_path(machine).write_text('{"schema_version":"invalid"}')
    _, plan = turn(project, runtime, registry, "--host", "dsh")
    assert plan["managed_executor"]["available"] is False
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
    for requester, count in (("coordinator", 1), ("worker", 0)):
        packet = project_delegation_context(
            runtime_root=runtime, registry_path=registry, goal_id="goal-a",
            agent_id=requester, project=project,
            execution_config=".loopx/config/delegations.json",
        )
        assert packet["authorized_count"] == count
        if count:
            route = packet["routes"][0]
            assert route["runtime_readiness"] == "ready"
            assert route["readiness"] == "unknown"
            assert packet["preflight"] == "required"
        assert KEY not in json.dumps(packet)
    assert not provider.operator_provider_store_path(runtime).exists()
