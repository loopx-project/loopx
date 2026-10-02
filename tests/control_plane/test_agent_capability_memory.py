"""Separate CLI processes must share only the registered agent's local observations."""
import json

from loopx.cli import main

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _run_cli, _write_fixture,
)


def test_next_turn_reuses_declared_runtime_capability(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    before = registry.read_bytes()
    args = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, first = _run_cli(registry, runtime, *args, "--available-capability", "network")
    assert rc == 0, first
    dispatch = first["turn_start_capability_hook_dispatch"]
    memory_result = next(
        item
        for item in dispatch["results"]
        if item["hook_id"] == "agent.capability_memory"
    )
    assert memory_result["local_private_state_mutated"] is True
    assert memory_result["external_writes_performed"] is False
    rc, next_turn = _run_cli(registry, runtime, *args)
    assert rc == 0, next_turn
    assert next_turn["capability_gate"]["action"] == "run"
    assert registry.read_bytes() == before


def test_goal_inheritance_local_failure_recovery_and_forget(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["available_capabilities"] = ["network"]
    data["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    registry.write_text(json.dumps(data))
    before = registry.read_bytes()
    inspect = ("agent-capabilities", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    quota = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, preview = _run_cli(registry, runtime, *inspect, "--unavailable-capability", "network")
    assert rc == 0, preview
    assert preview["availability"]["effective"] == ["network"]
    assert preview["agent_capabilities"]["proposed"]["unavailable"] == ["network"]
    assert not (runtime / "agent-runtime-capabilities").exists()
    rc, changed = _run_cli(registry, runtime, *inspect, "--unavailable-capability", "network", "--execute")
    assert rc == 0 and changed["agent_capabilities"]["written"], changed
    rc, blocked = _run_cli(registry, runtime, *quota)
    assert rc == 0 and blocked["capability_gate"]["action"] == "repair_bridge", blocked
    rc, peer = _run_cli(registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", "agent-b")
    assert rc == 0 and peer["capability_gate"]["action"] == "run", peer
    rc, recovered = _run_cli(registry, runtime, *quota, "--available-capability", "network")
    assert rc == 0 and recovered["capability_gate"]["action"] == "run", recovered
    rc, forgotten = _run_cli(registry, runtime, *inspect, "--forget-capability", "network", "--execute")
    assert rc == 0, forgotten
    assert forgotten["agent_capabilities"]["available"] == []
    assert forgotten["availability"]["effective"] == ["network"]
    assert registry.read_bytes() == before


def test_owner_authority_and_optional_enablement_are_not_remembered(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="credentials")
    args = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, first = _run_cli(registry, runtime, *args, "--available-capability", "credentials",
                         "--available-capability", "material_lifecycle")
    assert rc == 0, first
    assert not (runtime / "agent-runtime-capabilities").exists()
    rc, next_turn = _run_cli(registry, runtime, *args)
    assert rc == 0 and next_turn["capability_gate"]["action"] == "ask_owner", next_turn


def test_observation_does_not_cross_registry_or_agent(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append("agent-b")
    registry.write_text(json.dumps(data))
    args = ("quota", "should-run", "--goal-id", GOAL_ID)
    rc, first = _run_cli(registry, runtime, *args, "--agent-id", AGENT_ID, "--available-capability", "network")
    assert rc == 0, first
    rc, peer = _run_cli(registry, runtime, *args, "--agent-id", "agent-b")
    assert rc == 0 and peer["capability_gate"]["action"] == "repair_bridge", peer
    second_registry = registry.with_name("second.json")
    second_registry.write_bytes(registry.read_bytes())
    rc, other = _run_cli(second_registry, runtime, *args, "--agent-id", AGENT_ID)
    assert rc == 0 and other["capability_gate"]["action"] == "repair_bridge", other


def test_turn_plan_stays_read_only_and_settlement_rechecks_memory(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, plan = _run_cli(registry, runtime, "turn", "plan", *scope, "--available-capability", "network")
    assert rc == 0, plan
    assert not (runtime / "agent-runtime-capabilities").exists()
    rc, first = _run_cli(registry, runtime, "quota", "should-run", *scope, "--available-capability", "network")
    assert rc == 0, first
    _, spend = _run_cli(registry, runtime, "quota", "spend-slot", *scope)
    # Accounting still needs its ordinary delivery evidence; capability availability survives.
    assert spend["before"]["capability_gate"]["action"] == "run", spend
    assert spend["appended"] is False
    rc, cached = _run_cli(registry, runtime, "quota", "should-run", *scope, "--write-projection-cache")
    assert rc == 0, cached
    rc, unavailable = _run_cli(registry, runtime, "agent-capabilities", *scope,
                              "--unavailable-capability", "network", "--execute")
    assert rc == 0, unavailable
    rc, fresh = _run_cli(registry, runtime, "quota", "should-run", *scope, "--use-projection-cache")
    assert rc == 0 and fresh["capability_gate"]["action"] == "repair_bridge", fresh


def test_executing_managed_turn_remembers_through_turn_start_hook(
    tmp_path, capsys, monkeypatch
):
    project, runtime, registry = _write_fixture(
        tmp_path, required_capability="network"
    )

    def completed_turn(*args, **kwargs):
        return {
            "ok": True,
            "schema_version": "loopx_turn_execution_v0",
            "effects": {
                "host_invoked": False,
                "state_written": False,
                "scheduler_acknowledged": False,
                "quota_spent": False,
            },
        }

    monkeypatch.setattr(
        "loopx.cli_commands.turn_run_once.run_loopx_turn_once",
        completed_turn,
    )
    code = main(
        [
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            "turn",
            "run-once",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--scan-path",
            str(project),
            "--project",
            str(project),
            "--host",
            "generic-cli",
            "--execution-mode",
            "isolated-headless",
            "--host-adapter-command-json",
            '["/usr/bin/true"]',
            "--available-capability",
            "network",
            "--execute",
        ]
    )
    assert code == 0, capsys.readouterr().out
    capsys.readouterr()

    rc, next_turn = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    assert rc == 0, next_turn
    assert next_turn["capability_gate"]["action"] == "run"


def test_agent_alias_uses_existing_identity_normalization(tmp_path):
    _, runtime, registry = _write_fixture(tmp_path, required_capability="network")
    rc, first = _run_cli(registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID,
                         "--agent-id", AGENT_ID.upper(), "--available-capability", "network")
    assert rc == 0, first
    rc, state = _run_cli(registry, runtime, "agent-capabilities", "--goal-id", GOAL_ID,
                         "--agent-id", AGENT_ID)
    assert rc == 0, state
    assert state["agent_capabilities"]["available"] == ["network"]
