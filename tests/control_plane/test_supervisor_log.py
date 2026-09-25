"""Real file publication, concurrency and preview guarantees of the experimental log."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import json

import pytest

from loopx.control_plane.agents import supervisor_event_log as log
from loopx.control_plane.agents.supervisor_events import record_supervisor_proposal, record_supervisor_receipt

GOAL = "supervisor-log-fixture"
SUPERVISOR = {"enabled": True, "agent_id": "agent-a", "supervised_agents": ["agent-b"]}
DECISION = {"decision_id": "inject-1", "kind": "inject", "target_agent_id": "agent-b",
    "message": "Check the validation evidence.", "reason_codes": ["evidence-gap"], "evidence_refs": ["effect:1"]}

def _receipt(path: Path, receipt_id: str):
    try:
        result = record_supervisor_receipt(log_path=path, goal_id=GOAL, execute=True,
            host_capabilities=["session_message_injection"], receipt={"receipt_id": receipt_id, "decision_id": "inject-1",
                "adapter_id": "fixture-host", "outcome": "executed", "authority_ref": "owner:1",
                "rollback_boundary": {"mode": "compensating_action", "ref": "policy:1", "automatic": False},
                "evidence_refs": ["effect:1"], "reason_codes": ["adapter-success"]})
        return result["appended"]
    except ValueError as exc:
        return str(exc)

def _proposal(path: Path, *, execute=True, goal_id=GOAL):
    return record_supervisor_proposal(log_path=path, goal_id=goal_id,
        supervisor=SUPERVISOR, decision=DECISION, execute=execute)

def test_competing_executed_receipts_are_serialized(tmp_path):
    path = tmp_path / "supervisor-events.jsonl"
    _proposal(path)
    with ProcessPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_receipt, [path, path], ["receipt-a", "receipt-b"]))
    assert results.count(True) == 1, results
    assert sum(isinstance(row, str) and "already has an executed receipt" in row for row in results) == 1
    assert len(log.SupervisorEventStore(path).load()) == 2

def test_preview_does_not_publish_or_sync_and_checks_full_identity(tmp_path, monkeypatch):
    path = tmp_path / "supervisor-events.jsonl"
    _proposal(path)
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError("preview attempted durable IO")
    monkeypatch.setattr(log, "atomic_write_state_text", forbidden)
    monkeypatch.setattr(log, "verify_state_text_durable", forbidden)
    assert _proposal(path, execute=False)["appended"] is False
    with pytest.raises(log.SupervisorEventConflictError, match="another Goal"):
        _proposal(path, execute=False, goal_id="other-goal")
    assert path.read_bytes() == before

def test_commit_unknown_retry_recovers_same_record(tmp_path, monkeypatch):
    path = tmp_path / "supervisor-events.jsonl"
    write = log.atomic_write_state_text
    def uncertain(*args, **kwargs):
        write(*args, **kwargs)
        raise OSError("simulated acknowledgement loss")
    with monkeypatch.context() as patch:
        patch.setattr(log, "atomic_write_state_text", uncertain)
        with pytest.raises(log.SupervisorEventCommitUnknownError):
            _proposal(path)
    assert _proposal(path)["appended"] is False
    assert len(log.SupervisorEventStore(path).load()) == 1

@pytest.mark.parametrize("row", ["{broken", json.dumps({"schema_version": "loopx_state_event_v0"})])
def test_invalid_or_retired_schema_is_not_empty_history(tmp_path, row):
    path = tmp_path / "supervisor-events.jsonl"
    path.write_text(row + "\n")
    before = path.read_bytes()
    with pytest.raises(log.SupervisorEventError):
        _proposal(path)
    assert path.read_bytes() == before
