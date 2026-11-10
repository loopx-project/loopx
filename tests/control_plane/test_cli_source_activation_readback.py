"""Use real CLI authority receipts; native metadata is a separate transport gate."""
import pytest

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.quota.settlement import _read_heartbeat_settlement_payload
from loopx.control_plane.projects.registry_codec import load_registry
from loopx.control_plane.goals.activation import goal_is_stopped
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    GOAL_ID, AGENT_ID, TODO_ID, TURN_ID, _write_fixture, _configure_read_only_todo,
    _initialize_fixture_authority, _run_cli, _run_generated_cli, _spend_run_count,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_activation_reads_the_original_real_settlement_without_writing_or_replaying_it(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path / "original")
    _configure_read_only_todo(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    rc, packet = _run_cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID)
    assert rc == 0, packet
    scope = {"activation_id": "a" * 32, "container_id": "b" * 64, "started_at": "original-start",
        "goal_id": GOAL_ID, "agent_id": AGENT_ID, "todo_id": TODO_ID, "turn_instance_id": TURN_ID,
        "session_id": "original-session", "registry_path": str(registry), "launcher": "/profile/bin/loopx", "launcher_uid": 1000, "launcher_gid": 1000,
        "old_target": "/profile/releases/old/scripts/loopx", "new_target": "/profile/releases/new/scripts/loopx",
        "release_root": "/profile/releases", "target_revision": "c" * 40,
        "target_archive_sha256": "d" * 64, "worker_lock": "/controller/worker"}

    def decision():
        goal = next(g for g in load_registry(registry)["goals"] if g["id"] == GOAL_ID)
        readback = _read_heartbeat_settlement_payload(runtime, goal_id=GOAL_ID,
            fields={"agent_id": AGENT_ID, "todo_id": TODO_ID, "turn_instance_id": TURN_ID,
                    "replan_obligation_id": None, "infer_turn_instance_id": False, "allow_unbound_binding": False},
            registry_path=registry, goal_ref=None)
        return effect_runtime_result("runtime.cli_source_activation", {"operation": "qualify", "scope": scope,
            "observation": {"container_id": scope["container_id"], "started_at": scope["started_at"],
                "running": True, "paused": True, "owned_barrier": True, "control": "continue",
                "source_control": "stop" if goal_is_stopped(goal) else "continue",
                "ready_present": False, "launcher_target": scope["old_target"], "launcher_uid": 1000,
                "launcher_gid": 1000, "launcher_mode": 0o777, "probe_status": "ready", "unknown_process_ids": [],
                "scheduler": {"host_pending": False, "host_attempt": None, "last_wake_status": "wake_ok", "last_wake_rc": 0},
                "settlement": readback, "source_witness_before": "e" * 64, "source_witness_after": "e" * 64,
                "native": {"execution_authority_granted": False, "model_calls": 0, "quiescent_readback": True,
                    "session_id": scope["session_id"], "turn_ids": ["completed-native-turn"],
                    "completed_turn_id": "completed-native-turn", "visible_history_sha256": "f" * 64}}})

    assert decision()["source_change_allowed"] is False
    assert _spend_run_count(runtime) == 0
    plan = packet["interaction_contract"]["cli_channel"]["settlement_plan"]
    writeback = plan["ordered_steps"][1]["command_template"]
    for old, new in (("<validated_progress>", "verified_exclusion"), ("<scale>", "single_surface"),
                     ("<outcome>", "outcome_progress")):
        writeback = writeback.replace(old, new)
    rc, written = _run_generated_cli(writeback + " --progress-result-class advanced --progress-evidence-id exclusion-proof"
        " --delivery-boundary in_flight_continuation --no-global-sync --suppress-external-sinks", registry_path=registry)
    assert rc == 0, written
    assert decision()["source_change_allowed"] is False
    rc, spent = _run_generated_cli(written["settlement_owed"]["command"], registry_path=registry)
    assert rc == 0, spent
    result = decision()
    assert result["source_change_allowed"] is True
    assert result["original_settlement_identity"]["turn_instance_id"] == TURN_ID
    assert decision() == result
    assert _spend_run_count(runtime) == 1
    stop = ("goal-lifecycle", "--goal-id", GOAL_ID, "--operation", "stop")
    rc, preview = _run_cli(registry, runtime, *stop, cwd=project)
    assert rc == 0, preview
    rc, stopped = _run_cli(registry, runtime, *stop, "--actor-kind", "owner",
        "--expected-state-fingerprint", preview["observed_state_fingerprint"], "--execute", cwd=project)
    assert rc == 0 and stopped["readback"]["verified"], stopped
    denied = decision()
    assert denied["source_change_allowed"] is False and denied["reason"] == "stop_requested"
    assert _spend_run_count(runtime) == 1
