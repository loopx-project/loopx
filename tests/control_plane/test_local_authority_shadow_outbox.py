from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from loopx.control_plane.coordination import local_authority_shadow_outbox as outbox
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    ProjectionValueError,
    canonical_bytes,
    lease_partition_projection,
    partition_digest,
    sha256_digest,
    text_digest,
    todo_partition_projection,
)
from loopx.control_plane.coordination.runtime_shadow import (
    bootstrap_coordination_runtime_shadow,
    build_runtime_shadow_source_snapshot,
)
from loopx.control_plane.coordination.shadow_management import require_shadow_primary_write_allowed
from loopx.file_lock import exclusive_file_lock
from loopx.history import load_registry
from loopx.registry import find_registry_goal


GOAL_ID = "goal-outbox"


def _fixture(tmp_path: Path, *, bootstrap: bool = True) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        "handoff_mode: hard_lease\n"
        "updated_at: 2026-09-03T00:00:00+00:00\n"
        "---\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["agent-a"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    if bootstrap:
        goal = find_registry_goal(load_registry(registry), GOAL_ID)
        projection, snapshot = build_runtime_shadow_source_snapshot(
            goal=goal, runtime_root=runtime_root, state_path=state, registry_path=registry,
        )
        enabled_goal = {**goal, "coordination": {**goal["coordination"], "runtime_shadow": {
            "schema_version": "loopx_coordination_runtime_shadow_config_v0",
            "enabled": True, "provider": "file_v0",
        }}}
        result = bootstrap_coordination_runtime_shadow(
            goal=enabled_goal, runtime_root=runtime_root, goal_id=GOAL_ID,
            operation_id="bootstrap:outbox-test", source_version="source:initial",
            projection=projection, source_snapshot=snapshot,
        )
        # The managed Effect runtime may lose the first response after the
        # durable bootstrap commit and retry the same operation. Windows CI is
        # slow enough to exercise that path, so the public success contract is
        # applied/recovered/replayed rather than applied-only.
        assert result["status"] in {"applied", "recovered", "replayed"}, result
        assert result["operation_id"] == "bootstrap:outbox-test", result
        assert result["cursor"] == "1", result
        assert result["provider_revision"] == result["bootstrap_provider_revision"], result
        binding = require_shadow_primary_write_allowed(runtime_root, GOAL_ID)
        assert binding is not None, result
        assert binding["bootstrap_operation_id"] == result["operation_id"], result
        assert binding["bootstrap_provider_revision"] == result["provider_revision"], result
    return registry, state, runtime_root


def _capture(
    registry: Path,
    state: Path,
    runtime_root: Path,
    *,
    original_text: str,
    enabled: bool = True,
    write_class: str = "todo_add",
) -> outbox.TodoPartitionCapture:
    goal = find_registry_goal(load_registry(registry), GOAL_ID)
    return outbox.TodoPartitionCapture.begin(
        enabled=enabled,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        state_path=state,
        write_class=write_class,
        original_text=original_text,
        projector=adapter.todo_partition_projector(goal, state_path=state),
    )


def _planned_todo(original: str, text: str) -> tuple[str, str]:
    """Build source bytes for a low-level capture test; do not run another writer."""
    todo_id = f"todo_{uuid.uuid4().hex[:12]}"
    return todo_id, original + (
        f"- [ ] {text}\n"
        f"  <!-- loopx:todo todo_id={todo_id} status=open claimed_by=agent-a -->\n"
    )


def _todo_dir(runtime_root: Path) -> Path:
    return outbox.partition_directory(runtime_root, GOAL_ID, "todos")


def _drain(registry: Path, runtime_root: Path) -> adapter.DrainResult:
    original = registry.read_bytes()
    data = json.loads(original)
    data["goals"][0]["coordination"]["runtime_shadow"] = {
        "schema_version": "loopx_coordination_runtime_shadow_config_v0",
        "enabled": True, "provider": "file_v0",
    }
    registry.write_text(json.dumps(data))
    try:
        return adapter.drain_local_authority_shadow_outbox(
            registry_path=registry, runtime_root=runtime_root, goal_id=GOAL_ID,
            max_entries=10, budget_seconds=10, lock_timeout_seconds=2,
        )
    finally:
        registry.write_bytes(original)


def _record_change(registry: Path, state: Path, runtime_root: Path, text: str) -> outbox.TodoPartitionCapture:
    original = state.read_text()
    _, proposed = _planned_todo(original, text)
    capture = _capture(registry, state, runtime_root, original_text=original)
    capture.prepare(proposed)
    state.write_text(proposed)
    capture.committed()
    assert capture.outcome.failure is None, capture.outcome.failure
    return capture


def _files(directory: Path) -> dict[str, bytes]:
    return {str(path.relative_to(directory)): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def test_capture_records_prepared_then_committed_and_skips_prose_only_writes(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    original = state.read_text(encoding="utf-8")
    todo_id, new_text = _planned_todo(original, "Bind the shadow to the primary transaction.")

    capture = _capture(registry, state, runtime_root, original_text=original)
    capture.prepare(new_text)
    names = sorted(path.name for path in _todo_dir(runtime_root).iterdir())
    assert len(names) == 1
    assert names[0].endswith(".prepared.json")
    assert capture.outcome.entry_id is not None
    assert capture.outcome.seq == 1
    assert capture.outcome.source_bytes_digest == text_digest(new_text)
    assert capture.outcome.entry_id == outbox.entry_identity(
        goal_id=GOAL_ID, partition="todos", seq=1, source_ref=text_digest(new_text),
        capture_lineage_id=require_shadow_primary_write_allowed(runtime_root, GOAL_ID)["capture_lineage_id"],
        source_root_digest=outbox.runtime_root_digest(runtime_root),
    )

    state.write_text(new_text)
    capture.committed()
    entries = outbox.list_entries(_todo_dir(runtime_root))
    assert [entry.is_committed for entry in entries] == [True]
    entry = entries[0]
    assert entry.prepared["schema_version"] == outbox.OUTBOX_ENTRY_SCHEMA
    assert entry.committed is not None
    assert entry.committed["schema_version"] == outbox.OUTBOX_COMMIT_SCHEMA
    assert entry.prepared["source"] == {
        "kind": "markdown_active_state",
        "previous_bytes_digest": text_digest(original),
        "previous_partition_digest": partition_digest({"handoff_mode": "hard_lease", "todos": []}),
        "bytes_digest": text_digest(new_text),
        "lease": None,
        "event_id": None,
    }
    assert entry.prepared["writer"] == {
        "runtime": "python",
        "write_class": "todo_add",
        "operation_id": None,
    }
    projection = entry.projection()
    assert projection is not None
    assert projection["handoff_mode"] == "hard_lease"
    assert [item["todo_id"] for item in projection["todos"]] == [todo_id]
    assert entry.recorded_partition_digest() == partition_digest(projection)
    assert str(runtime_root) not in json.dumps(entry.prepared)

    prose_only = new_text + "\nOperator note that changes no coordination fact.\n"
    prose = _capture(registry, state, runtime_root, original_text=new_text, write_class="todo_update")
    prose.prepare(prose_only)
    prose.committed()
    assert prose.outcome.entry_id is None
    assert prose.outcome.skipped_reason == "partition_unchanged"
    assert len(outbox.list_entries(_todo_dir(runtime_root))) == 1

    _, third_text = _planned_todo(new_text, "Second coordination fact.")
    third = _capture(registry, state, runtime_root, original_text=new_text)
    third.prepare(third_text)
    state.write_text(third_text)
    third.committed()
    assert third.outcome.seq == 2
    assert [entry.seq for entry in outbox.list_entries(_todo_dir(runtime_root))] == [1, 2]


def test_capture_and_drain_use_the_active_binding_digest_through_a_root_alias(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")
    registry, state, runtime_root = _fixture(alias)

    capture = _record_change(
        registry, state, runtime_root, "Capture through the runtime alias."
    )
    binding = require_shadow_primary_write_allowed(runtime_root, GOAL_ID)
    assert binding is not None
    [entry] = outbox.list_entries(_todo_dir(runtime_root))
    assert entry.prepared["source_root_digest"] == binding["source_root_digest"]

    drained = _drain(registry, runtime_root)
    assert drained.outcome == "drained"
    assert drained.delivered == 1
    assert capture.outcome.entry_id == drained.entries[0]["entry_id"]


def test_disabled_capture_creates_nothing(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path, bootstrap=False)
    capture = _capture(registry, state, runtime_root, original_text="", enabled=False)
    capture.prepare("# anything")
    capture.committed()
    assert capture.outcome.skipped_reason == "shadow_disabled"
    assert capture.outcome.failure is None
    assert not (runtime_root / "authority-shadow").exists()




def test_cursor_allocation_hint_does_not_authorize_a_gap_or_candidate_write(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    directory = _todo_dir(runtime_root)
    directory.mkdir(parents=True)
    outbox.write_cursor(directory, partition="todos", last_seq=5,
                        last_entry_id="local-shadow-tx-" + "a" * 64,
                        last_partition_digest=None, last_cursor="5", last_provider_revision="rev-5")
    assert outbox.next_seq(directory) == 6
    first = _record_change(registry, state, runtime_root, "Recorded after the cursor hint.")
    second = _record_change(registry, state, runtime_root, "Another source transaction.")
    assert (first.outcome.seq, second.outcome.seq) == (6, 7)
    assert [entry.seq for entry in outbox.list_entries(directory)] == [6, 7]
    summary = outbox.outbox_summary(runtime_root, GOAL_ID)
    assert summary["todos"]["committed_pending"] == 2
    assert summary["todos"]["cursor_last_seq"] == 5
    assert summary["leases"] == {
        "committed_pending": 0, "prepared_only": 0, "retired_residue": 0, "next_seq": 0,
        "cursor_last_seq": None, "cursor_last_entry_id": None, "invalid": None,
    }
    before = _files(runtime_root / "authority-shadow")
    result = _drain(registry, runtime_root)
    assert result.reason_code == "outbox_cursor_unproved"
    assert result.delivered == 0
    assert _files(runtime_root / "authority-shadow") == before


def test_canonical_projection_rejects_floats_and_bad_lease_identity() -> None:
    with pytest.raises(ProjectionValueError):
        canonical_bytes({"version": 1.0})
    with pytest.raises(ProjectionValueError):
        todo_partition_projection(handoff_mode="hard_lease", todos=[{"todo_id": "a", "status": 2.5}])
    assert canonical_bytes({"b": 1, "a": [True, None, "\u00e9"]}) == '{"a":[true,null,"\u00e9"],"b":1}'.encode("utf-8")
    assert sha256_digest({"b": [1, None], "a": "x"}) == sha256_digest({"a": "x", "b": [1, None]})
    with pytest.raises(ProjectionValueError):
        lease_partition_projection([("todo-a", {"goal_id": "other", "todo_id": "todo-a"})], goal_id=GOAL_ID)
    projection = lease_partition_projection(
        [("todo-b", {"goal_id": GOAL_ID, "todo_id": "todo-b", "version": 1, "extra": "retained"}),
         ("todo-a", {"goal_id": GOAL_ID, "todo_id": "todo-a", "version": 2, "status": "active"})],
        goal_id=GOAL_ID,
    )
    assert [lease["todo_id"] for lease in projection["leases"]] == ["todo-a", "todo-b"]
    assert projection["leases"][1]["extra"] == "retained"


def test_capture_failure_is_typed_and_preserves_the_primary_result(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    original = state.read_text()
    _, new_text = _planned_todo(original, "Primary survives an unavailable outbox directory.")
    blocker = _todo_dir(runtime_root)
    blocker.write_text("not a directory")
    capture = _capture(registry, state, runtime_root, original_text=original)
    capture.prepare(new_text)
    state.write_text(new_text)
    capture.committed()
    assert capture.outcome.entry_id is None
    assert capture.outcome.failure is not None
    assert capture.outcome.failure["reason_code"] == "outbox_prepare_failed"
    assert state.read_text() == new_text


def test_primary_lock_probe_reports_held_locks(tmp_path: Path) -> None:
    target = tmp_path / "ACTIVE_GOAL_STATE.md"
    target.write_text("", encoding="utf-8")
    assert adapter.primary_lock_is_free(target) is True
    with exclusive_file_lock(target, timeout_seconds=1.0, operation="test_hold"):
        assert adapter.primary_lock_is_free(target) is False
    assert adapter.primary_lock_is_free(target) is True


def test_prepared_records_must_bind_their_directory_identity_and_source(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    original = state.read_text(encoding="utf-8")
    _, new_text = _planned_todo(original, "Bound to this goal and partition.")
    capture = _capture(registry, state, runtime_root, original_text=original)
    capture.prepare(new_text)
    state.write_text(new_text)
    capture.committed()
    directory = _todo_dir(runtime_root)
    [entry] = outbox.list_entries(directory)
    assert entry.prepared["goal_id"] == GOAL_ID
    assert entry.prepared["partition"] == "todos"
    assert entry.prepared["source_root_digest"] == outbox.runtime_root_digest(runtime_root)
    assert outbox.record_source_ref(entry.prepared) == text_digest(state.read_text(encoding="utf-8"))

    def tampered(**changes: object) -> None:
        record = dict(entry.prepared)
        for key, value in changes.items():
            if isinstance(value, dict) and isinstance(record.get(key), dict):
                record[key] = {**record[key], **value}
            else:
                record[key] = value
        outbox.durable_write_json(entry.prepared_path, record)
        with pytest.raises(outbox.OutboxError) as raised:
            outbox.list_entries(directory)
        assert raised.value.reason_code == "outbox_file_invalid"

    tampered(goal_id="goal-other")
    tampered(partition="leases")
    tampered(source={"bytes_digest": text_digest("some other bytes")})
    tampered(source_root_digest="not-a-digest")
    tampered(writer={"runtime": "ruby"})
    tampered(source={"kind": "unknown_source"})
    outbox.durable_write_json(entry.prepared_path, entry.prepared)
    assert len(outbox.list_entries(directory)) == 1

    # A valid-looking root/lineage from another generation cannot reuse this ID.
    tampered(capture_lineage_id="another-lineage")
    tampered(source_root_digest="sha256:" + "1" * 64)
    tampered(schema_version="loopx_local_authority_shadow_outbox_entry_v0")
    outbox.durable_write_json(entry.prepared_path, entry.prepared)
    assert entry.committed_path is not None and entry.committed is not None
    outbox.durable_write_json(entry.committed_path, {**entry.committed, "capture_lineage_id": "another-lineage"})
    with pytest.raises(outbox.OutboxError) as invalid_marker:
        outbox.list_entries(directory)
    assert invalid_marker.value.reason_code == "outbox_file_invalid"
    outbox.durable_write_json(entry.committed_path, entry.committed)
    assert len(outbox.list_entries(directory)) == 1


def test_cursor_residue_is_diagnostic_and_requires_an_exact_receipt(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    _record_change(registry, state, runtime_root, "First source transaction.")
    _record_change(registry, state, runtime_root, "Second source transaction.")
    directory = _todo_dir(runtime_root)
    first, second = outbox.list_entries(directory)
    assert outbox.retired_residue(directory) == []
    outbox.write_cursor(directory, partition="todos", last_seq=first.seq,
                        last_entry_id=first.entry_id, last_partition_digest=first.recorded_partition_digest(),
                        last_cursor="2", last_provider_revision="file:2:" + "1" * 24)
    assert [path.name for path in outbox.retired_residue(directory)] == sorted(
        [first.committed_path.name, first.prepared_path.name]
    )
    # Hiding entries below an unproved watermark would lose the only delivery evidence.
    assert [entry.seq for entry in outbox.list_entries(directory)] == [first.seq, second.seq]
    assert outbox.next_seq(directory) == 3
    before = _files(runtime_root / "authority-shadow")
    result = _drain(registry, runtime_root)
    assert result.reason_code == "outbox_cursor_unproved"
    assert _files(runtime_root / "authority-shadow") == before


def test_exact_receipts_allow_cursor_then_residue_cleanup(tmp_path: Path) -> None:
    registry, state, runtime_root = _fixture(tmp_path)
    _record_change(registry, state, runtime_root, "An exact transaction to deliver.")
    directory = _todo_dir(runtime_root)
    original = _files(directory)
    delivered = _drain(registry, runtime_root)
    assert delivered.delivered == 1, delivered.reason_code
    cursor = outbox.read_cursor(directory)
    assert cursor is not None and cursor["last_seq"] == 1
    assert outbox.list_entries(directory) == []
    for name, raw in original.items():
        (directory / name).write_bytes(raw)
    assert len(outbox.retired_residue(directory)) == 2
    recovered = _drain(registry, runtime_root)
    assert recovered.reason_code is None, recovered.reason_code
    assert outbox.list_entries(directory) == []
    assert outbox.read_cursor(directory) == cursor


def test_reclaim_validates_the_complete_batch_before_any_unlink(tmp_path: Path) -> None:
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_bytes(b"first exact receipt bytes")
    second.write_bytes(b"second changed after proof")
    proof = [(first, outbox.raw_bytes_digest(first.read_bytes())), (second, outbox.raw_bytes_digest(b"different bytes"))]
    with pytest.raises(outbox.OutboxError) as raised:
        outbox.reclaim_verified_files(proof)
    assert raised.value.reason_code == "outbox_file_changed"
    assert first.exists() and second.exists()
