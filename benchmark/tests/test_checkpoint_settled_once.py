"""An explicit handoff does not change periodic capture or reuse a missed point."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from benchmark.runtime import checkpoint


def declaration(tmp_path):
    return {"trigger": "fresh_replan", "destination": str(tmp_path / "checkpoints"),
        "components": [{"id": "workspace", "source_path": str(tmp_path / "workspace")}],
        "point_metadata": {"purpose": "Retained operator metadata"},
        "settled_work_capture_once": {"destination": str(tmp_path / "checkpoints" / "handoff"),
            "purpose": "Explicit upgrade handoff"}}


@pytest.mark.parametrize("change", [
    {"trigger": None}, {"settled_work_capture_once": None},
    {"settled_work_capture_once": {"destination": "/outside/point", "purpose": "handoff"}},
    {"settled_work_capture_once": {"destination": "/logs/checkpoints/point", "purpose": " "}},
    {"point_metadata": {"settled_work_handoff": "Do not overwrite me"}},
])
def test_explicit_handoff_rejects_ambiguous_declarations(tmp_path, change):
    with pytest.raises(ValueError):
        checkpoint.parse_checkpoint_request(json.dumps({**declaration(tmp_path), **change}).encode())


def test_handoff_inherits_all_declared_participants_without_changing_default(tmp_path):
    request = {**declaration(tmp_path), "native_session": True, "loopx_state": True,
        "evaluation_opportunity": True, "controller_handoff": True}
    assert checkpoint.parse_checkpoint_request(json.dumps(request).encode()) == request
    del request["settled_work_capture_once"]
    assert checkpoint.parse_checkpoint_request(json.dumps(request).encode()) == request


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    request = declaration(tmp_path)
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    env = {"LOOPX_CHECKPOINT_REQUEST": str(path), "LOOPX_WAKE_LOG_DIR": str(tmp_path / "wakes"),
        "LOOPX_RUNTIME_ROOT": str(tmp_path / "runtime"), "LOOPX_REGISTRY": str(tmp_path / "registry"),
        "LOOPX_GOAL_ID": "source", "LOOPX_AGENT_ID": "agent"}
    gate_calls, settlements = [], []
    def gate(*args, **kwargs):
        gate_calls.append(kwargs)
        return {"capture_allowed": True}
    def settled(*args, **kwargs):
        settlements.append(kwargs["turn_instance_id"])
        return SimpleNamespace(progress={"state": "settled"},
            identity=SimpleNamespace(value=SimpleNamespace(effect_id=kwargs["turn_instance_id"])))
    monkeypatch.setattr("benchmark.runtime.checkpoint_continuation.restored_capture_gate", gate)
    monkeypatch.setattr(checkpoint, "read_heartbeat_settlement", settled)
    def receipt(turn="first", **extra):
        return {"ok": True, "stage": "execute", "mode": "heartbeat", "turn_id": turn,
            "checkpoint": {"phase": "before_replan", "captured": False, "reason": "no_fresh_replan"},
            "checkpoint_handoff": {"pending": True, "turn_id": turn,
                "destination": request["settled_work_capture_once"]["destination"],
                "request_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, **extra}
    return request, path, env, receipt, gate_calls, settlements


def test_default_and_unsuccessful_wakes_add_no_io_or_settlement(prepared):
    _, _, env, receipt, gates, settlements = prepared
    for extra in ({"checkpoint_handoff": None}, {"ok": False},
                  {"budget_exhausted": True}, {"stage": "plan"}):
        current = receipt(**extra)
        original = dict(current["checkpoint"])
        checkpoint.capture_after_wake(env, current)
        assert current["checkpoint"] == original
    assert not gates and not settlements


@pytest.mark.parametrize("mutation", ["request", "turn"])
def test_changed_request_or_turn_cannot_capture(prepared, mutation):
    _, path, env, receipt, gates, settlements = prepared
    current = receipt()
    if mutation == "request":
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        current["turn_id"] = "later"
    checkpoint.capture_after_wake(env, current)
    assert current["checkpoint_handoff"]["reason"] == "capture_failed"
    assert not gates
    assert settlements == (["first"] if mutation == "request" else [])


def test_failed_attempt_is_consumed_without_later_source_read(prepared, monkeypatch, tmp_path):
    request, _, env, receipt, gates, settlements = prepared
    request["loopx_state"] = True
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    attempts = []
    def unavailable(*args):
        attempts.append(True)
        raise ValueError("Synthetic source unavailable before sealing")
    monkeypatch.setattr(checkpoint, "research_participants", unavailable)
    current = receipt()
    original = dict(current["checkpoint"])
    checkpoint.capture_after_wake(env, current)
    assert current["checkpoint_handoff"]["reason"] == "capture_failed"
    assert current["ok"] and current["checkpoint"] == original
    assert not (tmp_path / "checkpoints" / "handoff").exists()
    witness = (tmp_path / "wakes/first/checkpoint-completed-request.json").read_bytes()
    for turn in ("first", "later"):
        later = receipt(turn)
        checkpoint.capture_after_wake(env, later)
        assert later["checkpoint_handoff"]["reason"] == "destination_already_attempted_no_replay"
        assert later["checkpoint"] == original
    assert attempts == [True] and len(gates) == len(settlements) == 1
    assert (tmp_path / "wakes/first/checkpoint-completed-request.json").read_bytes() == witness


def test_restore_gate_still_blocks_explicit_handoff(prepared, monkeypatch):
    _, _, env, receipt, _, settlements = prepared
    monkeypatch.setattr("benchmark.runtime.checkpoint_continuation.restored_capture_gate",
        lambda *args, **kwargs: {"capture_allowed": False, "reason": "restored_replan_pending"})
    current = receipt()
    checkpoint.capture_after_wake(env, current)
    assert current["checkpoint_handoff"]["reason"] == "restored_replan_pending"
    assert settlements == ["first"]


def test_unsettled_work_does_not_consume_a_handoff(prepared, monkeypatch, tmp_path):
    _, _, env, receipt, gates, _ = prepared
    monkeypatch.setattr(checkpoint, "read_heartbeat_settlement", lambda *args, **kwargs: None)
    reads = []
    monkeypatch.setattr(checkpoint, "read_checkpoint_request", lambda *args: reads.append(True))
    current = receipt()
    checkpoint.capture_after_wake(env, current)
    assert current["checkpoint_handoff"]["reason"] == "owned_turn_not_settled"
    assert not reads and not gates
    assert not (tmp_path / "wakes").exists()


def test_handoff_without_original_destination_fails_before_declaration_io(prepared, monkeypatch):
    _, _, env, receipt, gates, settlements = prepared
    current = receipt()
    del current["checkpoint_handoff"]["destination"]
    reads = []
    monkeypatch.setattr(checkpoint, "read_checkpoint_request", lambda *args: reads.append(True))
    checkpoint.capture_after_wake(env, current)
    assert current["checkpoint_handoff"]["reason"] == "capture_failed"
    assert not reads and not gates and not settlements


@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_lost_declaration_cannot_backfill_the_original_point(prepared, tmp_path, mutation):
    _, path, env, receipt, _, _ = prepared
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "candidate.txt"
    source.write_text("original settled candidate")
    raw = path.read_bytes()
    first = receipt()
    original_periodic = dict(first["checkpoint"])
    if mutation == "missing":
        path.unlink()
    else:
        path.write_bytes(raw + b"\n")
    checkpoint.capture_after_wake(env, first)
    assert first["checkpoint_handoff"]["reason"] == "capture_failed"
    assert first["ok"] and first["checkpoint"] == original_periodic

    # Restoring the same declaration must not label subsequent work as the
    # missed point, even when real artifact probe/capture would now succeed.
    path.write_bytes(raw)
    source.write_text("later candidate must not replace the missed point")
    later = receipt("later")
    checkpoint.capture_after_wake(env, later)
    assert later["checkpoint_handoff"].get("captured") is False
    assert later["checkpoint_handoff"]["reason"] == "destination_already_attempted_no_replay"
    assert later["checkpoint"] == original_periodic and later["ok"]
    assert not (tmp_path / "checkpoints" / "handoff").exists()
    witness = json.loads((tmp_path / "wakes/first/checkpoint-completed-request.json").read_text())
    assert witness["request_sha256"] == hashlib.sha256(raw).hexdigest()
    assert witness["settlement_effect_id"] == "first"


@pytest.mark.parametrize("deadline", ["1", "nan", "inf"])
def test_closed_original_phase_adds_no_handoff_reads(prepared, monkeypatch, deadline):
    _, _, env, receipt, gates, settlements = prepared
    current = receipt()
    original_periodic = dict(current["checkpoint"])
    env["LOOPX_PHASE_DEADLINE_EPOCH"] = deadline
    reads = []
    def forbidden_read(*args):
        reads.append(True)
        raise AssertionError("closed phase must not read a declaration")
    monkeypatch.setattr(checkpoint, "read_checkpoint_request", forbidden_read)
    checkpoint.capture_after_wake(env, current)
    assert not reads and not gates and not settlements
    expected = "capture_window_closed" if deadline == "1" else "capture_failed"
    assert current["checkpoint_handoff"]["reason"] == expected
    assert current["checkpoint"] == original_periodic and current["ok"]


def test_phase_expiring_during_settlement_read_stops_before_declaration(prepared, monkeypatch):
    _, _, env, receipt, gates, settlements = prepared
    current = receipt()
    env["LOOPX_PHASE_DEADLINE_EPOCH"] = "2"
    now = [1.0]
    monkeypatch.setattr(checkpoint.time, "time", lambda: now[0])
    read_settlement = checkpoint.read_heartbeat_settlement
    def settle_then_expire(*args, **kwargs):
        result = read_settlement(*args, **kwargs)
        now[0] = 3.0
        return result
    monkeypatch.setattr(checkpoint, "read_heartbeat_settlement", settle_then_expire)
    reads = []
    monkeypatch.setattr(checkpoint, "read_checkpoint_request", lambda *args: reads.append(True))
    checkpoint.capture_after_wake(env, current)
    assert current["checkpoint_handoff"]["reason"] == "capture_window_closed"
    assert settlements == ["first"] and not reads and not gates
