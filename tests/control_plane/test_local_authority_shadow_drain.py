# Native persistence-window regressions live in shadow_drain.test.ts and the real-process CLI suite.
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    head_digest,
    partition_digest,
    text_digest,
)
from loopx.file_lock import exclusive_file_lock, exclusive_cross_runtime_file_lock
from loopx.control_plane.coordination.shadow_management import require_shadow_primary_write_allowed
from shadow_e2e_fixture import workspace
from loopx.history import load_registry
from loopx.registry import find_registry_goal
from loopx.todos import list_goal_todos


GOAL_ID = "goal-e2e"


def test_shadow_parity_ignores_only_resume_evaluation_observation_clock() -> None:
    head = {
        "handoff_mode": "hard_lease",
        "todos": [{
            "todo_id": "todo-a",
            "resume_ready": False,
            "resume_condition": {
                "evaluated_at": "2026-09-20T00:00:00Z",
                "satisfied": False,
                "availability_reason": "resume_condition_pending",
            },
        }],
        "leases": [],
    }
    later_observation = json.loads(json.dumps(head))
    later_observation["todos"][0]["resume_condition"]["evaluated_at"] = (
        "2026-09-21T00:00:00Z"
    )

    assert head_digest(head) == head_digest(later_observation)
    assert partition_digest({
        "handoff_mode": head["handoff_mode"],
        "todos": head["todos"],
    }) == partition_digest({
        "handoff_mode": later_observation["handoff_mode"],
        "todos": later_observation["todos"],
    })

    changed_decision = json.loads(json.dumps(later_observation))
    changed_decision["todos"][0]["resume_condition"]["satisfied"] = True
    assert head_digest(head) != head_digest(changed_decision)
    assert partition_digest({
        "handoff_mode": head["handoff_mode"],
        "todos": head["todos"],
    }) != partition_digest({
        "handoff_mode": changed_decision["handoff_mode"],
        "todos": changed_decision["todos"],
    })


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    real = workspace(tmp_path / "repo")
    return real.registry, real.state, real.runtime


def _record_todo_write(
    registry: Path,
    state: Path,
    runtime_root: Path,
    text: str,
    *,
    mark_committed: bool = True,
    write_file: bool = True,
) -> outbox.TodoPartitionCapture:
    """Prepare a source transaction without invoking a second capture-enabled writer."""

    original = state.read_text(encoding="utf-8")
    goal = find_registry_goal(load_registry(registry), GOAL_ID)
    capture = outbox.TodoPartitionCapture.begin(
        enabled=True,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        state_path=state,
        write_class="todo_add",
        original_text=original,
        projector=adapter.todo_partition_projector(goal, state_path=state),
    )
    todo_id = f"todo_{uuid.uuid4().hex[:12]}"
    new_text = original + (
        f"- [ ] {text}\n"
        f"  <!-- loopx:todo todo_id={todo_id} status=open claimed_by=agent-a -->\n"
    )
    capture.prepare(new_text)
    assert capture.outcome.failure is None
    assert capture.outcome.entry_id is not None
    if write_file:
        state.write_text(new_text, encoding="utf-8")
    if mark_committed:
        capture.committed()
    return capture


def _drain(registry: Path, runtime_root: Path, **overrides: object) -> adapter.DrainResult:
    limits = {"max_entries": 20, "budget_seconds": 10, **overrides}
    return adapter.drain_local_authority_shadow_outbox(
        registry_path=registry, runtime_root=runtime_root, goal_id=GOAL_ID,
        **limits,  # type: ignore[arg-type]
    )


def _todo_dir(runtime_root: Path) -> Path:
    return outbox.partition_directory(runtime_root, GOAL_ID, "todos")


def test_drain_delivers_each_committed_entry_once_in_order_and_verifies_readback(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    captures = [
        _record_todo_write(registry, state, runtime_root, f"Fact {index}") for index in range(3)
    ]
    assert [capture.outcome.seq for capture in captures] == [1, 2, 3]

    result = _drain(registry, runtime_root)

    assert result.ok is True
    assert result.outcome == "drained"
    assert result.config_enabled is True
    assert (result.delivered, result.replayed, result.no_op) == (3, 0, 0)
    assert result.pending_after == 0
    assert result.prepared_only_after == 0
    assert result.budget_exhausted is False
    assert result.candidate_readback_verified is True
    assert result.last_cursor == "4"
    assert (result.cursor_before, result.cursor_after, result.drained_count) == ("1", "4", 3)
    payload = result.to_payload()
    assert payload["ok"] is True
    assert payload["drained_count"] == 3
    assert payload["cursor_after"] == "4"
    assert [item["outcome"] for item in result.entries] == ["delivered"] * 3
    assert [item["cursor"] for item in result.entries] == ["2", "3", "4"]
    assert [item["entry_id"] for item in result.entries] == [
        capture.outcome.entry_id for capture in captures
    ]
    assert result.store_identity is not None
    assert result.store_identity.startswith("file:")
    assert list(_todo_dir(runtime_root).iterdir()) == [_todo_dir(runtime_root) / "drain-cursor.json"]
    cursor = outbox.read_cursor(_todo_dir(runtime_root))
    assert cursor is not None
    assert cursor["last_seq"] == 3
    assert cursor["last_entry_id"] == captures[-1].outcome.entry_id
    assert cursor["last_partition_digest"] == captures[-1].outcome.partition_digest

    view = adapter.read_local_authority_shadow(runtime_root=runtime_root, goal_id=GOAL_ID, scan_limit=10)
    assert view["status"] == "loaded"
    head = view["head"]
    assert head["schema_version"] == "loopx_coordination_runtime_shadow_projection_v0"
    assert head["partitions"]["todos"] == {
        "seq": 3,
        "partition_digest": captures[-1].outcome.partition_digest,
    }
    assert head["partitions"]["leases"] is None
    listed = list_goal_todos(registry_path=registry, goal_id=GOAL_ID, runtime_root_arg=str(runtime_root))
    assert [todo["todo_id"] for todo in head["todos"]] == sorted(
        todo["todo_id"] for todo in listed["todos"]
    )
    assert view["head_digest"] == head_digest(head) == result.head_digest
    assert [tx["operation_id"] for tx in view["scan"]["transactions"][1:]] == [
        capture.outcome.entry_id for capture in captures
    ]
    receipts = [tx["receipts"][0] for tx in view["scan"]["transactions"][1:]]
    assert all(receipt["source_transaction_correlated"] is True for receipt in receipts)
    assert all(receipt["durable_source_outbox"] is True for receipt in receipts)
    assert all(receipt["parity_verdict"] == "not_evaluated" for receipt in receipts)
    assert [receipt["source_bytes_digest"] for receipt in receipts] == [
        capture.outcome.source_bytes_digest for capture in captures
    ]
    assert str(runtime_root) not in json.dumps(view)

    again = _drain(registry, runtime_root)
    assert again.outcome == "nothing_pending"
    assert again.ok is True


def test_drain_defers_when_another_drainer_holds_the_lock(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    _record_todo_write(registry, state, runtime_root, "Pending behind a drainer")
    with exclusive_cross_runtime_file_lock(
        outbox.drain_lock_target(runtime_root, GOAL_ID), timeout_seconds=1.0, operation="test_hold"
    ):
        result = _drain(registry, runtime_root, lock_timeout_seconds=0.05)
    assert result.outcome == "drain_deferred"
    assert result.reason_code == "drain_lock_busy"
    assert result.ok is False
    assert result.pending_after == 1
    assert not (runtime_root / "authority-shadow" / "file").exists()


def test_drain_batch_is_bounded_and_reports_what_it_left(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    for index in range(3):
        _record_todo_write(registry, state, runtime_root, f"Bounded {index}")

    first = _drain(registry, runtime_root, max_entries=2)
    assert first.outcome == "drained"
    assert first.ok is True
    assert first.delivered == 2
    assert first.budget_exhausted is True
    assert first.pending_after == 1
    assert first.candidate_readback_verified is True

    second = _drain(registry, runtime_root)
    assert second.delivered == 1
    assert second.pending_after == 0
    assert (second.cursor_before, second.cursor_after) == ("3", "4")
    view = adapter.read_local_authority_shadow(runtime_root=runtime_root, goal_id=GOAL_ID)
    assert view["cursor"] == "4"
    assert view["head"]["partitions"]["todos"]["seq"] == 3


def test_prepared_only_entries_resolve_only_under_a_free_primary_lock(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    proven = _record_todo_write(registry, state, runtime_root, "Marker lost after write", mark_committed=False)
    with exclusive_file_lock(state, timeout_seconds=1.0, operation="writer_in_flight"):
        busy = _drain(registry, runtime_root)
    assert busy.outcome == "stopped"
    assert busy.reason_code == "primary_writer_busy"
    assert busy.prepared_only_after == 1
    assert busy.delivered == 0

    result = _drain(registry, runtime_root)
    assert result.ok is True
    assert result.delivered == 1
    assert result.no_op == 0
    assert result.entries[0]["resolution"] == "committed_proven_by_readback"
    assert result.entries[0]["entry_id"] == proven.outcome.entry_id
    view = adapter.read_local_authority_shadow(runtime_root=runtime_root, goal_id=GOAL_ID, scan_limit=5)
    assert view["head"]["partitions"]["todos"]["partition_digest"] == proven.outcome.partition_digest
    receipt = view["scan"]["transactions"][1]["receipts"][0]
    assert receipt["resolution"] == "committed_proven_by_readback"

    abandoned = _record_todo_write(
        registry, state, runtime_root, "Never landed", mark_committed=False, write_file=False
    )
    result = _drain(registry, runtime_root)
    assert result.delivered == 1
    assert result.no_op == 1
    assert result.entries[0]["resolution"] == "abandoned"
    assert result.entries[0]["entry_id"] == abandoned.outcome.entry_id
    view = adapter.read_local_authority_shadow(runtime_root=runtime_root, goal_id=GOAL_ID, scan_limit=5)
    assert view["cursor"] == "3"
    assert view["head"]["partitions"]["todos"]["seq"] == 1
    assert view["scan"]["transactions"][2]["events"][0]["kind"] == "source_transaction_abandoned"


def test_unexplained_prepared_entry_holds_without_reseed_or_deletion(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    _record_todo_write(registry, state, runtime_root, "Baseline")
    assert _drain(registry, runtime_root).delivered == 1
    stale = _record_todo_write(registry, state, runtime_root, "Half-recorded", mark_committed=False)
    state.write_text(state.read_text() + "\nExternal source change after the writer stopped.\n")
    before = {str(path.relative_to(runtime_root)): path.read_bytes()
              for path in (runtime_root / "authority-shadow").rglob("*") if path.is_file()}
    result = _drain(registry, runtime_root)
    assert result.outcome == "stopped"
    assert result.reason_code == "outbox_source_unproved"
    assert result.delivered == result.no_op == 0
    assert result.entries == []
    assert [entry.entry_id for entry in outbox.list_entries(_todo_dir(runtime_root))] == [stale.outcome.entry_id]
    assert {str(path.relative_to(runtime_root)): path.read_bytes()
            for path in (runtime_root / "authority-shadow").rglob("*") if path.is_file()} == before


def test_lease_partition_entries_retain_complete_records_at_drain(tmp_path: Path) -> None:
    registry, _state, runtime_root = _fixture(tmp_path)
    directory = outbox.partition_directory(runtime_root, GOAL_ID, "leases")
    record = {
        "goal_id": GOAL_ID,
        "todo_id": "todo_00000000000a",
        "owner": "agent-a",
        "idempotency_key": "k1",
        "write_scopes": ["loopx/**"],
        "version": 2,
        "lease_epoch": 1,
        "acquired_at": "2026-09-03T00:00:00+00:00",
        "updated_at": "2026-09-03T00:01:00+00:00",
        "expires_at": "2026-09-03T00:31:00+00:00",
        "released_at": None,
        "status": "active",
        "lease_path": "/should/never/be/compared",
        "acquire_ttl_seconds": 1800,
    }
    bytes_digest = text_digest(json.dumps(record, indent=2) + "\n")
    binding = require_shadow_primary_write_allowed(runtime_root, GOAL_ID)
    assert binding is not None
    entry_id = outbox.entry_identity(goal_id=GOAL_ID, partition="leases", seq=1, source_ref=bytes_digest,
                                    capture_lineage_id=binding["capture_lineage_id"], source_root_digest=binding["source_root_digest"])
    outbox.durable_write_json(
        directory / outbox.entry_file_name(1, entry_id, "prepared"),
        {
            "schema_version": outbox.OUTBOX_ENTRY_SCHEMA,
            "goal_id": GOAL_ID,
            "capture_lineage_id": binding["capture_lineage_id"],
            "partition": "leases",
            "seq": 1,
            "entry_id": entry_id,
            "writer": {"runtime": "typescript", "write_class": "task_lease_acquire", "operation_id": "op-1"},
            "source": {
                "kind": "task_lease_record",
                "previous_bytes_digest": None,
                "previous_partition_digest": partition_digest({"leases": []}),
                "bytes_digest": bytes_digest,
                "lease": {"todo_id": record["todo_id"], "version": 2, "lease_epoch": 1, "status": "active", "updated_at": record["updated_at"]},
                "previous_lease": None,
                "event_id": None,
            },
            "source_root_digest": outbox.runtime_root_digest(runtime_root),
            "projection": {"leases": [{"file_stem": record["todo_id"], "record": record}]},
            "partition_digest": None,
            "prepared_at": "2026-09-03T00:01:00.000Z",
        },
    )
    outbox.durable_write_json(
        directory / outbox.entry_file_name(1, entry_id, "committed"),
        {"schema_version": outbox.OUTBOX_COMMIT_SCHEMA, "entry_id": entry_id, "capture_lineage_id": binding["capture_lineage_id"], "committed_at": "2026-09-03T00:01:00.100Z"},
    )

    primary_lease = outbox.lease_directory(runtime_root, GOAL_ID) / (record["todo_id"] + ".json")
    primary_lease.parent.mkdir(parents=True, exist_ok=True)
    primary_lease.write_text(json.dumps(record, indent=2) + "\n")
    result = _drain(registry, runtime_root)

    assert result.ok is True
    assert result.delivered == 1
    view = adapter.read_local_authority_shadow(runtime_root=runtime_root, goal_id=GOAL_ID, scan_limit=5)
    head = view["head"]
    assert head["todos"] == []
    assert head["handoff_mode"] == "hard_lease"
    assert head["leases"] == [record]
    expected_digest = partition_digest({"leases": head["leases"]})
    assert head["partitions"]["leases"] == {"seq": 1, "partition_digest": expected_digest}
    assert result.entries[0]["partition_digest"] == expected_digest
    assert "/should/never/be/compared" in json.dumps(view)


def test_status_reports_backlog_candidate_and_growth_facts(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    empty = adapter.local_authority_shadow_status(registry_path=registry, runtime_root=runtime_root, goal_id=GOAL_ID)
    assert empty["ok"] is True
    assert empty["config"]["status"] == "disabled"
    assert empty["candidate"]["status"] == "loaded"
    assert empty["candidate"]["cursor"] == "1"
    assert empty["store_bytes"] > 0
    assert empty["retention_pressure"] is False
    assert str(runtime_root) not in json.dumps(empty)

    _record_todo_write(registry, state, runtime_root, "Status fact")
    pending = adapter.local_authority_shadow_status(registry_path=registry, runtime_root=runtime_root, goal_id=GOAL_ID)
    assert pending["outbox"]["todos"]["committed_pending"] == 1
    assert _drain(registry, runtime_root).delivered == 1

    drained = adapter.local_authority_shadow_status(registry_path=registry, runtime_root=runtime_root, goal_id=GOAL_ID)
    assert drained["outbox"]["todos"] == {
        "committed_pending": 0,
        "prepared_only": 0,
        "retired_residue": 0,
        "next_seq": 0,
        "cursor_last_seq": 1,
        "cursor_last_entry_id": outbox.read_cursor(_todo_dir(runtime_root))["last_entry_id"],
        "invalid": None,
    }
    assert drained["candidate"]["status"] == "loaded"
    assert drained["candidate"]["cursor"] == "2"
    assert drained["candidate"]["codec_agreement"] is True
    assert drained["candidate"]["head_schema_version"] == "loopx_coordination_runtime_shadow_projection_v0"
    assert drained["store_bytes"] > 0


def test_capture_evidence_v1_reports_measured_facts_only(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    capture = _record_todo_write(registry, state, runtime_root, "Evidence fact")
    drain = _drain(registry, runtime_root)
    evidence = adapter.capture_evidence(goal_id=GOAL_ID, capture=capture.outcome, drain=drain)
    assert adapter.valid_evidence_v1(evidence, goal_id=GOAL_ID)
    assert evidence["outcome"] == "delivered"
    assert evidence["entry"]["entry_id"] == capture.outcome.entry_id
    assert evidence["source_transaction_correlated"] is True
    assert evidence["durable_source_outbox"] is True
    assert evidence["source_candidate_compared"] is False
    assert evidence["parity_verdict"] == "not_evaluated"
    assert evidence["drain"]["candidate_readback_verified"] is True

    deferred = adapter.DrainResult(goal_id=GOAL_ID, outcome="drain_deferred", reason_code="drain_lock_busy")
    assert adapter.capture_evidence(goal_id=GOAL_ID, capture=capture.outcome, drain=deferred)["outcome"] == "drain_deferred"
    pending = adapter.capture_evidence(goal_id=GOAL_ID, capture=capture.outcome, drain=None)
    assert pending["outcome"] == "pending"
    assert pending["drain"] is None

    for skipped_reason in ("partition_unchanged", "shadow_disabled"):
        skipped = outbox.CaptureOutcome(partition="todos", skipped_reason=skipped_reason)
        no_transaction = adapter.capture_evidence(goal_id=GOAL_ID, capture=skipped, drain=None)
        assert no_transaction["outcome"] == "no_transaction"
        assert no_transaction["reason_code"] == skipped_reason
        # Nothing was recorded, so nothing durable or correlated may be claimed.
        assert no_transaction["durable_source_outbox"] is False
        assert no_transaction["source_transaction_correlated"] is False
        assert adapter.valid_evidence_v1(no_transaction, goal_id=GOAL_ID)
    failed = outbox.CaptureOutcome(partition="todos", failure={"reason_code": "outbox_prepare_failed", "error_class": "OSError"})
    failed_evidence = adapter.capture_evidence(goal_id=GOAL_ID, capture=failed, drain=None)
    assert failed_evidence["outcome"] == "capture_failed"
    assert failed_evidence["durable_source_outbox"] is False
    assert failed_evidence["source_transaction_correlated"] is False
    assert adapter.valid_evidence_v1(failed_evidence, goal_id=GOAL_ID)


def test_an_orphan_marker_above_the_cursor_is_still_corruption(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    capture = _record_todo_write(registry, state, runtime_root, "Marker without its prepared file")
    (_todo_dir(runtime_root) / outbox.entry_file_name(1, str(capture.outcome.entry_id), "prepared")).unlink()
    with pytest.raises(outbox.OutboxError) as raised:
        outbox.list_entries(_todo_dir(runtime_root))
    assert raised.value.reason_code == "outbox_file_invalid"
    result = _drain(registry, runtime_root)
    assert result.outcome == "stopped"
    assert result.reason_code == "outbox_file_invalid"
    status = adapter.local_authority_shadow_status(registry_path=registry, runtime_root=runtime_root, goal_id=GOAL_ID)
    assert status["ok"] is False
    assert status["outbox"]["todos"]["invalid"] == "outbox_file_invalid"


def test_drain_fails_closed_on_an_entry_recorded_for_another_runtime_root(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    capture = _record_todo_write(registry, state, runtime_root, "Written under a foreign root")
    entry_id = str(capture.outcome.entry_id)
    prepared_path = _todo_dir(runtime_root) / outbox.entry_file_name(1, entry_id, "prepared")
    record = json.loads(prepared_path.read_text())
    record["source_root_digest"] = outbox.runtime_root_digest(tmp_path / "elsewhere")
    outbox.durable_write_json(prepared_path, record)
    before = prepared_path.read_bytes()
    result = _drain(registry, runtime_root)
    assert result.outcome == "stopped"
    assert result.reason_code == "outbox_file_invalid"
    assert result.delivered == 0
    assert prepared_path.read_bytes() == before
    with pytest.raises(outbox.OutboxError):
        outbox.list_entries(_todo_dir(runtime_root))
