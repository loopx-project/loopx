"""Real quota reads preserve requirement/target semantics across authority sources."""
import json

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
from loopx.control_plane.coordination.local_authority import CanonicalAuthorityRequired, read_canonical_todos_if_promoted
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result


@pytest.mark.parametrize("provider", ["markdown", "file", "sqlite"])
@pytest.mark.parametrize("network_available", [False, True])
def test_real_quota_capability_gate_does_not_enable_repair_outputs(tmp_path, monkeypatch, provider, network_available):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    state, runtime, registry = tmp_path / "STATE.md", tmp_path / "runtime", tmp_path / "registry.json"
    state.write_text(
        "# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P0] Inspect external evidence\n"
        "  <!-- loopx:todo todo_id=todo_external role=agent status=open task_class=advancement_task claimed_by=agent-a required_capabilities=network -->\n"
        "- [ ] [P1] Repair the bridge\n"
        "  <!-- loopx:todo todo_id=todo_repair role=agent status=open task_class=advancement_task claimed_by=agent-a required_capabilities=network target_capabilities=network -->\n"
        "- [ ] [P2] Inspect protected evidence\n"
        "  <!-- loopx:todo todo_id=todo_owner role=agent status=open task_class=advancement_task claimed_by=agent-a required_capabilities=credentials -->\n"
        "\n## User Todo / Owner Review Reading Queue\n\n"
    )
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry, goal_id="goal-a",
                           domain="software", adapter_kind="generic_project_goal_v0", state_file=str(state),
                           registered_agents=["agent-a"], quota_allowed_slots=None)
    if provider != "markdown":
        goal = json.loads(registry.read_text())["goals"][0]
        projection, _ = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime,
                                                             state_path=state, registry_path=registry)
        initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
    before = state.read_bytes()
    args = ["quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
            "--include-detail", "agent-todos", ]
    if network_available:
        args += ["--available-capability", "network"]
    if provider == "markdown":
        # Markdown is authored source, no longer an ordinary authority backend.
        # Availability of a host capability cannot bypass that migration gate.
        with pytest.raises(CanonicalAuthorityRequired):
            read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
        code, rejected = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
        assert code == 1 and rejected["error_code"] == "canonical_authority_required", rejected
        assert rejected["write_admitted"] is False
        assert rejected["decision_read_from_provider"] is False
        assert rejected["legacy_fallback_used"] is False
        assert state.read_bytes() == before
        assert not (runtime / "authority").exists()
        return
    authority = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    code, packet = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    gate = packet["capability_gate"]
    assert gate["action"] == "run"
    assert gate["owner_missing"] == ["credentials"]
    assert ("network" in gate["available"]) is network_available
    ids = [row["todo_id"] for row in gate["runnable_candidates"]]
    assert ids == (["todo_external", "todo_repair"] if network_available else ["todo_repair"])
    assert gate["repair_candidate_count"] == (0 if network_available else 1)
    assert state.read_bytes() == before

    # Capability availability cannot replace the Goal's missing intent and
    # stop conditions. Canonical Todos remain readable without granting work.
    state.unlink()
    code, missing = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
    assert code == 1, missing
    assert missing["status_health_ok"] is False
    assert missing["should_run"] is False
    assert missing["normal_delivery_allowed"] is False
    assert missing["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert not state.exists(), "observation must not reconstruct the Goal document"
    if provider != "markdown":
        code, listed = run_json_cli_result("todo", "list", "--goal-id", "goal-a",
                                          registry_path=registry, runtime_root=runtime)
        assert code == 0, listed
        assert {row["todo_id"] for row in listed["todos"]} == {"todo_external", "todo_repair", "todo_owner"}
    state.write_bytes(before)
    code, recovered = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
    assert code == 0, recovered
    assert [row["todo_id"] for row in recovered["capability_gate"]["runnable_candidates"]] == ids
    assert state.read_bytes() == before
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == authority
