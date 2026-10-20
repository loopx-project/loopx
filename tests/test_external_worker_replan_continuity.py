"""A process exit is not new authority to retry an unresolved research decision."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_external_scheduler_worker import _args, _hint_payload, worker
from loopx.extensions.process_runtime import CappedProcessResult
from loopx.file_lock import try_exclusive_file_lock
from loopx.control_plane.quota.effect_program import SettlementIdentity, SettlementResult


GOAL_REF = {"goal_id": "goal", "goal_instance_id": "ginst_" + "a" * 32}
OBLIGATION = "replan-" + "1" * 16


def replan_packet(obligation_id=OBLIGATION):
    packet = _hint_payload(should_run=True)
    packet.update(
        goal_ref=deepcopy(GOAL_REF),
        agent_identity={"agent_id": "agent"},
        effective_action="autonomous_replan_required",
        autonomous_replan_obligation={
            "schema_version": "autonomous_replan_obligation_v0",
            "required": True, "agent_id": "agent", "obligation_id": obligation_id,
        },
    )
    return packet


def result(code=0, failure=None):
    return CappedProcessResult(returncode=code, stdout=b"", failure_kind=failure)


def setup_worker(tmp_path, monkeypatch, packet, wake_result=None):
    calls = []
    packet.setdefault("runtime_root", str(tmp_path / "runtime"))
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: deepcopy(packet))
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: calls.append(1) or (wake_result or result()))
    args = _args(fake_cli=tmp_path / "unused", state_file=tmp_path / "state.json", wake_cmd="unused")
    return args, calls


def test_successful_exit_and_restart_cannot_retry_same_unresolved_replan(tmp_path, monkeypatch, capsys):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet)
    assert worker.run_worker(args) == 0
    # New process invocation, new log/context ids and scheduler cadence token
    # carry no new work or evidence. The original obligation remains unresolved.
    packet["scheduler_hint"]["reset_policy"]["reset_token"] = "changed-host-cadence"
    packet["replan_action_packet"] = {"context_id": "fresh-observation-time"}
    packet["autonomous_replan_obligation"]["recommended_action"] = "Consider again."
    for _ in range(100):
        assert worker.run_worker(args) == 0
    assert len(calls) == 1
    assert "status=replan_paused" in capsys.readouterr().out


def test_agent_case_alias_preserves_state_route_and_unknown_attempt(tmp_path, monkeypatch):
    args, calls = setup_worker(tmp_path, monkeypatch, replan_packet(), result(1))
    lower_route = worker._default_state_path(args)
    args.agent_id = "AGENT"
    assert worker._default_state_path(args) == lower_route
    assert worker.run_worker(args) != 0
    saved = json.loads(Path(args.state_file).read_text())
    assert saved["replan_attempts"][0]["agent_id"] == "agent"
    args.agent_id = "agent"
    assert worker.run_worker(args) != 0
    assert calls == [1]


def test_independent_work_and_temporary_wait_do_not_erase_original_attempt(tmp_path, monkeypatch):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet)
    assert worker.run_worker(args) == 0
    original = deepcopy(packet)
    packet.update(effective_action="normal_run", selected_todo={"todo_id": "todo_independent"})
    packet.pop("autonomous_replan_obligation")
    # Real admitted long work may use more than one wake without changing Todo id.
    for _ in range(3):
        assert worker.run_worker(args) == 0
    packet.update(_hint_payload(should_run=False))
    assert worker.run_worker(args) == 0
    packet.clear()
    packet.update(original)
    assert worker.run_worker(args) == 0
    assert len(calls) == 4


def test_new_owner_obligation_and_new_goal_lifetime_have_separate_attempts(tmp_path, monkeypatch):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet)
    assert worker.run_worker(args) == 0
    packet["autonomous_replan_obligation"]["obligation_id"] = "replan-" + "2" * 16
    assert worker.run_worker(args) == 0
    packet["autonomous_replan_obligation"]["obligation_id"] = OBLIGATION
    assert worker.run_worker(args) == 0
    packet["goal_ref"]["goal_instance_id"] = "ginst_" + "b" * 32
    assert worker.run_worker(args) == 0
    assert len(calls) == 3


@pytest.mark.parametrize("exit_code,failure", [(1, None), (-9, "timeout"), (-9, "output_limit")])
def test_failed_replan_is_unknown_even_when_next_obligation_id_changes(tmp_path, monkeypatch, exit_code, failure):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet, result(exit_code, failure))
    assert worker.run_worker(args) != 0
    packet["autonomous_replan_obligation"]["obligation_id"] = "replan-" + "2" * 16
    assert worker.run_worker(args) != 0
    packet.update(effective_action="normal_run", selected_todo={"todo_id": "todo_new"})
    assert worker.run_worker(args) != 0
    assert len(calls) == 1


def test_dispatch_intent_survives_crash_without_claiming_a_result(tmp_path, monkeypatch):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet)

    def interrupted(*a, **kw):
        assert Path(args.state_file).exists(), "intent must be durable before invoking the Host"
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "_run_wake", interrupted)
    with pytest.raises(KeyboardInterrupt):
        worker.run_worker(args)
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: calls.append(1) or result())
    assert worker.run_worker(args) != 0
    assert calls == []


@pytest.mark.parametrize("contents", ["{broken", "[]", '{"unchanged_count": -1}',
    '{"replan_attempts": "invalid"}', '{"replan_attempts": [{"status": []}]}'])
def test_damaged_persistent_state_never_becomes_a_fresh_attempt(tmp_path, monkeypatch, contents):
    args, calls = setup_worker(tmp_path, monkeypatch, replan_packet())
    state = Path(args.state_file)
    state.write_text(contents)
    assert worker.run_worker(args) != 0
    assert calls == []
    assert state.read_text() == contents


@pytest.mark.parametrize("patch", [
    {"goal_ref": {"goal_id": "goal", "goal_instance_id": "invalid"}},
    {"goal_ref": {"goal_id": "other-goal", "goal_instance_id": "ginst_" + "a" * 32}},
    {"agent_identity": {"agent_id": "other-agent"}},
    {"autonomous_replan_obligation": {"required": True}},
])
def test_automatic_replan_requires_original_identity(tmp_path, monkeypatch, patch):
    packet = replan_packet()
    packet.update(patch)
    args, calls = setup_worker(tmp_path, monkeypatch, packet)
    assert worker.run_worker(args) != 0
    assert not calls


def test_observe_only_does_not_consume_a_replan_attempt(tmp_path, monkeypatch):
    args, calls = setup_worker(tmp_path, monkeypatch, replan_packet())
    args.wake_cmd = None
    assert worker.run_worker(args) == 0
    args.wake_cmd = "unused"
    assert worker.run_worker(args) == 0
    assert len(calls) == 1


def test_same_state_route_has_one_live_consumer(tmp_path, monkeypatch):
    args, calls = setup_worker(tmp_path, monkeypatch, replan_packet())
    with try_exclusive_file_lock(Path(args.state_file)) as lock:
        assert lock is not None
        assert worker.run_worker(args) == 0
    assert not calls
    assert worker.run_worker(args) == 0
    assert len(calls) == 1


def test_explicit_stop_still_wins_over_a_previous_unknown_attempt(tmp_path, monkeypatch):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet, result(1))
    assert worker.run_worker(args) != 0
    packet.update(_hint_payload(should_run=False, local_scheduler_directive="stop"))
    assert worker.run_worker(args) == 0
    assert len(calls) == 1
    state = json.loads(Path(args.state_file).read_text())
    assert state["replan_attempts"], "a stop must not erase unresolved original work"


def test_legacy_executable_goal_keeps_its_real_scope_without_inventing_lifetime(tmp_path, monkeypatch):
    packet = replan_packet()
    packet.pop("goal_ref")
    args, calls = setup_worker(tmp_path, monkeypatch, packet)
    for _ in range(3):
        assert worker.run_worker(args) == 0
    state = json.loads(Path(args.state_file).read_text())
    assert len(calls) == 1
    assert state["replan_attempts"][0]["goal_ref"] is None
    assert state["replan_attempts"][0]["registry"] == str(Path(args.registry).resolve())


@pytest.mark.parametrize("initial_exact", [True, False])
@pytest.mark.parametrize("exit_code", [0, 1])
def test_missing_or_new_exact_identity_cannot_reset_original_attempt(tmp_path, monkeypatch, initial_exact, exit_code):
    packet = replan_packet()
    if not initial_exact:
        packet.pop("goal_ref")
    args, calls = setup_worker(tmp_path, monkeypatch, packet, result(exit_code))
    worker.run_worker(args)
    packet["goal_ref"] = None if initial_exact else deepcopy(GOAL_REF)
    worker.run_worker(args)
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["todo_lifecycle_settlement", "receipt_bound_replan_settlement"])
def test_original_settlement_is_not_a_second_semantic_replan_attempt(tmp_path, monkeypatch, mode):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet)
    assert worker.run_worker(args) == 0
    packet["autonomous_replan_obligation"].update(resolution_mode=mode, selection_binding="heartbeat_receipt")
    assert worker.run_worker(args) == 0
    assert len(calls) == 2
    assert len(json.loads(Path(args.state_file).read_text())["replan_attempts"]) == 1


@pytest.mark.parametrize("phase", ["open", "settlement_pending", "settled"])
def test_lost_response_resolves_only_after_exact_original_settlement_readback(tmp_path, monkeypatch, phase):
    packet = replan_packet()
    packet.pop("goal_ref")
    original = SettlementIdentity(goal_id="goal", agent_id="agent", todo_id=None,
                                  turn_instance_id="original-turn", replan_obligation_id=OBLIGATION)
    packet["heartbeat_receipt"] = {"settlement_identity": original.as_dict()}
    args, calls = setup_worker(tmp_path, monkeypatch, packet, result(1))
    assert worker.run_worker(args) != 0
    persisted = json.loads(Path(args.state_file).read_text())
    persisted["replan_attempts"][0].pop("goal_ref")
    Path(args.state_file).write_text(json.dumps(persisted))
    observed = []

    def read_original(runtime_root, **kwargs):
        observed.append(kwargs)
        return SimpleNamespace(identity=SettlementResult.pure(original), replay_phase=phase,
                               settlement=SettlementResult.pure({"verified": True}))

    monkeypatch.setattr(worker, "read_heartbeat_settlement", read_original)
    packet.pop("heartbeat_receipt")
    packet.pop("autonomous_replan_obligation")
    packet.update(effective_action="normal_run", selected_todo={"todo_id": "todo_next"})
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: calls.append(1) or result())
    code = worker.run_worker(args)
    assert observed[0]["turn_instance_id"] == "original-turn"
    assert observed[0]["replan_obligation_id"] == OBLIGATION
    assert observed[0]["resolve_original_binding"] is True
    assert len(calls) == (2 if phase == "settled" else 1)
    assert (code == 0) == (phase == "settled")


def test_settled_other_turn_cannot_clear_original_unknown(tmp_path, monkeypatch):
    packet = replan_packet()
    packet.pop("goal_ref")
    original = SettlementIdentity(goal_id="goal", agent_id="agent", todo_id=None,
                                  turn_instance_id="original-turn", replan_obligation_id=OBLIGATION)
    packet["heartbeat_receipt"] = {"settlement_identity": original.as_dict()}
    args, calls = setup_worker(tmp_path, monkeypatch, packet, result(1))
    assert worker.run_worker(args) != 0
    other = SettlementIdentity(goal_id="goal", agent_id="agent", todo_id=None,
                               turn_instance_id="later-turn", replan_obligation_id=OBLIGATION)
    monkeypatch.setattr(worker, "read_heartbeat_settlement", lambda *a, **kw: SimpleNamespace(
        identity=SettlementResult.pure(other), replay_phase="settled",
        settlement=SettlementResult.pure({"verified": True})))
    assert worker.run_worker(args) != 0
    assert len(calls) == 1


@pytest.mark.parametrize("mismatch", ["agent", "goal", "runtime"])
def test_canonical_agent_spelling_does_not_waive_admission_scope(tmp_path, monkeypatch, mismatch):
    packet = replan_packet()
    args, calls = setup_worker(tmp_path, monkeypatch, packet)
    args.agent_id = "AGENT"
    if mismatch == "agent":
        packet["agent_identity"]["agent_id"] = "other-agent"
    elif mismatch == "goal":
        packet["goal_id"] = "other-goal"
    else:
        args.runtime_root = str(tmp_path / "different-runtime")
    assert worker.run_worker(args) == 2
    assert not calls


@pytest.mark.parametrize("source_changed", [False, True])
def test_legacy_observation_upgrades_only_against_its_original_registry_view(tmp_path, source_changed):
    from loopx.control_plane.quota.host_poll_receipts import read_host_worker_observations, receipt_path_for_goal, record_host_poll_receipt
    from loopx.global_registry import sanitize_goal_for_global
    from loopx.history import goal_registry_digest, host_worker_goal_observation_digest

    args = _args(fake_cli=tmp_path / "unused", state_file=tmp_path / "worker.json")
    runtime, state = tmp_path / "runtime", tmp_path / "state.md"
    goal = {"id": args.goal_id, "repo": str(tmp_path), "state_file": state.name, "quota": {"compute": 1}}
    source = Path(args.registry)
    source.write_text(json.dumps({"goals": [goal]}))
    old_digest = goal_registry_digest(goal)
    original = {"obligation_id": OBLIGATION, "runtime_root": str(runtime), "registry_goal_digest": old_digest}
    record_host_poll_receipt(goal, state, agent_id=args.agent_id, decision={"should_run": True},
        worker_observation={**original, "status": "replan_outcome_unknown", "turn_instance_id": None})
    initial = read_host_worker_observations(goal, state, registry_goal_digest=old_digest, runtime_root=runtime)
    assert initial["read_status"] == "read"
    if source_changed:
        goal["quota"]["compute"] = 0
        source.write_text(json.dumps({"goals": [goal]}))
    before = deepcopy(original)
    worker._observe_worker_replan({"should_run": True}, args, original, "replan_outcome_unknown")
    assert original == before  # Compatibility never rewrites the durable attempt.
    row = json.loads(receipt_path_for_goal(goal, state).read_text())["worker_observations"][args.agent_id]
    assert ("source_goal_observation_digest" in row) is (not source_changed)
    mirror_goal = sanitize_goal_for_global(goal, source_registry=source, synced_at="current")
    observed = read_host_worker_observations(mirror_goal, state, registry_goal_digest=goal_registry_digest(mirror_goal),
        source_goal_observation_digest=host_worker_goal_observation_digest(mirror_goal, registry_path=tmp_path / "global.json"),
        runtime_root=runtime)
    assert observed["read_status"] == ("stale" if source_changed else "read")
