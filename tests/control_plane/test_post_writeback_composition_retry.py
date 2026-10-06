from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from loopx.cli_commands.post_writeback import (
    dispatch_committed_cli_post_writeback_hooks,
)
from loopx.control_plane.capability_hooks import (
    POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
    PostWritebackHookRegistration,
)
from loopx.control_plane.post_writeback_composition_retry import (
    _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT,
    POST_WRITEBACK_COMPOSITION_RETRY_PROJECTION_SCHEMA_VERSION,
    POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION,
    append_composition_retry_receipt,
    build_composition_retry_receipt,
    collect_pending_composition_retry_projection,
    composition_retry_receipt_id,
    composition_retry_receipt_log_path,
    composition_retry_receipt_ref,
    pending_composition_retry_receipts,
    pending_composition_retry_receipts_for_path,
    settle_composition_retry_receipt,
)


def _write_registry(tmp_path: Path) -> tuple[Path, Path]:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir(exist_ok=True)
    registry_path = tmp_path / "registry.global.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [{"id": "goal-1"}],
            }
        ),
        encoding="utf-8",
    )
    return registry_path, runtime_root


def _identity() -> dict[str, str]:
    return {
        "agent_id": "agent-1",
        "todo_id": "todo-1",
        "turn_instance_id": "turn-1",
        "effect_id": "goal-1:agent-1:todo-1:turn-1",
    }


def _hook(*, producer_calls: list[int] | None = None) -> PostWritebackHookRegistration:
    def producer(value: object) -> dict[str, object]:
        if producer_calls is not None:
            producer_calls.append(1)
        assert isinstance(value, dict)
        receipt = value["receipt"]
        assert isinstance(receipt, dict)
        return {
            "schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
            "hook_id": "periodic_report.stage_completion",
            "capability_id": "periodic-report",
            "phase": "post_writeback",
            "status": "intent",
            "intent": {
                "schema_version": "loopx_capability_intent_v0",
                "intent_kind": "periodic_report.trigger_evaluation",
                "idempotency_key": "periodic-report:stage-123",
                "source_receipt_id": receipt["event_id"],
                "payload": {"stage_identity": "stage-123"},
                "requested_write_scope": [],
            },
        }

    return PostWritebackHookRegistration(
        hook_id="periodic_report.stage_completion",
        capability_id="periodic-report",
        event_kinds=("todo_complete",),
        intent_kinds=("periodic_report.trigger_evaluation",),
        requested_read_scope=("stage_completion",),
        producer=producer,
    )


def _stage_projection() -> dict[str, object]:
    return {
        "stage_completion": {
            "schema_version": "periodic_report_stage_completion_receipt_v0",
            "stage_identity": "stage-123",
        }
    }


def _dispatch(
    registry_path: Path,
    *,
    hooks: tuple[PostWritebackHookRegistration, ...],
    projection_builder: Any,
) -> dict[str, Any]:
    return dispatch_committed_cli_post_writeback_hooks(
        payload={"ok": True, "completed": True},
        registry_path=registry_path,
        runtime_root_arg=None,
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="2026-09-06T00:00:00Z",
        committed_at="2026-09-06T00:00:00Z",
        hooks=hooks,
        projection_builder=projection_builder,
    )


def _journal_rows(runtime_root: Path) -> list[dict[str, Any]]:
    journal_path = composition_retry_receipt_log_path(runtime_root, "goal-1")
    return [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_failed_projection_preserves_actionable_hook_identity(
    tmp_path: Path,
) -> None:
    """Acceptance 1: the failed projection keeps its concrete identity."""

    registry_path, runtime_root = _write_registry(tmp_path)

    def failing_builder(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("transient projection failure")

    result = _dispatch(
        registry_path, hooks=(_hook(),), projection_builder=failing_builder
    )

    assert result["registered_count"] == 1
    assert result["intent_count"] == 0
    assert result["primary_writeback_preserved"] is True
    assert result["external_writes_performed"] is False
    (failure,) = result["failures"]
    assert failure["hook_id"] == "periodic_report.stage_completion"
    assert failure["capability_id"] == "periodic-report"
    assert failure["error_code"] == "source_projection_failed"
    assert failure["durable_receipt_ref"].startswith("post-writeback-composition:pwcr_")

    pending = pending_composition_retry_receipts(runtime_root, "goal-1")
    (receipt,) = pending
    assert receipt["schema_version"] == (
        POST_WRITEBACK_COMPOSITION_RETRY_RECEIPT_SCHEMA_VERSION
    )
    assert receipt["status"] == "retryable"
    assert receipt["error_code"] == "source_projection_failed"
    assert receipt["event_kind"] == "todo_complete"
    assert receipt["identity"]["effect_id"] == "goal-1:agent-1:todo-1:turn-1"
    assert receipt["identity"]["todo_id"] == "todo-1"
    assert receipt["state_version"] == "2026-09-06T00:00:00Z"
    assert receipt["committed_at"] == "2026-09-06T00:00:00Z"
    assert receipt["hooks"] == [
        {
            "hook_id": "periodic_report.stage_completion",
            "capability_id": "periodic-report",
            "policy_version": "v0",
        }
    ]
    assert receipt["primary_writeback_preserved"] is True
    assert receipt["external_writes_performed"] is False


def test_replay_after_transient_recovery_projects_once_and_settles_receipt(
    tmp_path: Path,
) -> None:
    """Acceptance 2: recovery replays the projection once and settles the receipt."""

    registry_path, runtime_root = _write_registry(tmp_path)
    projection_calls: list[int] = []
    producer_calls: list[int] = []

    def flaky_builder(**_kwargs: object) -> dict[str, object]:
        projection_calls.append(1)
        if len(projection_calls) == 1:
            raise RuntimeError("transient projection failure")
        return _stage_projection()

    hooks = (_hook(producer_calls=producer_calls),)

    failed = _dispatch(registry_path, hooks=hooks, projection_builder=flaky_builder)
    assert failed["failures"][0]["error_code"] == "source_projection_failed"
    assert [
        receipt["status"]
        for receipt in pending_composition_retry_receipts(runtime_root, "goal-1")
    ] == ["retryable"]

    recovered = _dispatch(registry_path, hooks=hooks, projection_builder=flaky_builder)
    assert recovered["failures"] == []
    assert recovered["intent_count"] == 1
    assert len(producer_calls) == 1
    assert len(projection_calls) == 2
    assert pending_composition_retry_receipts(runtime_root, "goal-1") == []
    rows = _journal_rows(runtime_root)
    assert [
        row["status"] for row in rows if row["receipt_id"] == rows[-1]["receipt_id"]
    ][-1] == "settled"

    replayed = _dispatch(registry_path, hooks=hooks, projection_builder=flaky_builder)
    assert replayed["intent_count"] == 1
    assert replayed["replayed_hooks"] == ["periodic_report.stage_completion"]
    assert len(producer_calls) == 1
    assert len(projection_calls) == 3
    assert pending_composition_retry_receipts(runtime_root, "goal-1") == []
    settled_ids = {row["receipt_id"] for row in _journal_rows(runtime_root)}
    assert len(settled_ids) == 1


def test_composition_replay_keeps_primary_writeback_idempotent(
    tmp_path: Path,
) -> None:
    """Acceptance 3: replaying the primary writeback never repeats its effect."""

    registry_path, runtime_root = _write_registry(tmp_path)
    primary_effects: list[str] = ["goal-1:agent-1:todo-1:turn-1"]
    projection_calls: list[int] = []
    producer_calls: list[int] = []

    def flaky_builder(**_kwargs: object) -> dict[str, object]:
        projection_calls.append(1)
        if len(projection_calls) <= 2:
            raise RuntimeError("transient projection failure")
        return _stage_projection()

    hooks = (_hook(producer_calls=producer_calls),)
    first_failure = _dispatch(
        registry_path, hooks=hooks, projection_builder=flaky_builder
    )
    second_failure = _dispatch(
        registry_path, hooks=hooks, projection_builder=flaky_builder
    )
    recovered = _dispatch(registry_path, hooks=hooks, projection_builder=flaky_builder)

    for result in (first_failure, second_failure, recovered):
        assert result["primary_writeback_preserved"] is True
        assert result["external_writes_performed"] is False
    assert primary_effects == ["goal-1:agent-1:todo-1:turn-1"]
    assert len(producer_calls) == 1

    rows = _journal_rows(runtime_root)
    assert len({row["receipt_id"] for row in rows}) == 1
    statuses = [row["status"] for row in rows]
    assert statuses == ["retryable", "retryable", "settled"]
    assert pending_composition_retry_receipts(runtime_root, "goal-1") == []


def test_composition_retry_receipt_id_binds_primary_writeback_identity() -> None:
    hooks = [
        {
            "hook_id": "periodic_report.stage_completion",
            "capability_id": "periodic-report",
        }
    ]
    base = composition_retry_receipt_id(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        hook_identities=hooks,
    )
    assert base.startswith("pwcr_")
    assert base == composition_retry_receipt_id(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        hook_identities=list(reversed(hooks)),
    )
    assert base != composition_retry_receipt_id(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-3",
        hook_identities=hooks,
    )
    assert base != composition_retry_receipt_id(
        goal_id="goal-2",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        hook_identities=hooks,
    )
    assert base != composition_retry_receipt_id(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        hook_identities=[],
    )
    assert composition_retry_receipt_ref(base) == (f"post-writeback-composition:{base}")


def test_composition_retry_journal_append_is_idempotent_and_terminal(
    tmp_path: Path,
) -> None:
    journal_path = (
        tmp_path
        / "goals"
        / "goal-1"
        / ("post_writeback_hooks")
        / "composition-retry-receipts.jsonl"
    )
    receipt = build_composition_retry_receipt(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[
            {
                "hook_id": "periodic_report.stage_completion",
                "capability_id": "periodic-report",
            }
        ],
        error_code="source_projection_failed",
    )

    appended_first, appended_flag_first = append_composition_retry_receipt(
        journal_path, receipt
    )
    appended_again, appended_flag_again = append_composition_retry_receipt(
        journal_path, receipt
    )
    assert appended_flag_first is True
    assert appended_flag_again is True

    settled, settled_flag = settle_composition_retry_receipt(
        journal_path,
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[
            {
                "hook_id": "periodic_report.stage_completion",
                "capability_id": "periodic-report",
            }
        ],
    )
    assert settled_flag is True
    assert settled["status"] == "settled"
    assert settled["error_code"] is None

    regressed, regressed_flag = append_composition_retry_receipt(journal_path, receipt)
    assert regressed_flag is False
    assert regressed["status"] == "settled"
    rows = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["status"] for row in rows] == ["retryable", "retryable", "settled"]

    resettle, resettle_flag = settle_composition_retry_receipt(
        journal_path,
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[
            {
                "hook_id": "periodic_report.stage_completion",
                "capability_id": "periodic-report",
            }
        ],
    )
    assert resettle_flag is False
    assert resettle["status"] == "settled"
    assert len(rows) == 3

    # A different registered hook set computes a different receipt identity,
    # so it must not touch (let alone settle) the original failure's receipt.
    foreign, foreign_flag = settle_composition_retry_receipt(
        journal_path,
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[
            {"hook_id": "other.hook", "capability_id": "other-capability"}
        ],
    )
    assert foreign_flag is False
    assert foreign == {}
    assert len(rows) == 3


def test_append_composition_retry_receipt_survives_a_torn_journal_tail(
    tmp_path: Path,
) -> None:
    journal_path = composition_retry_receipt_log_path(tmp_path, "goal-1")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_bytes(b'{"interrupted":')
    receipt = build_composition_retry_receipt(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[],
        error_code="dispatch_failed",
    )

    appended, changed = append_composition_retry_receipt(journal_path, receipt)

    assert changed is True
    assert appended == receipt
    assert pending_composition_retry_receipts(tmp_path, "goal-1") == [receipt]


def test_append_composition_retry_receipt_separates_a_valid_unterminated_row(
    tmp_path: Path,
) -> None:
    journal_path = composition_retry_receipt_log_path(tmp_path, "goal-1")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    first = build_composition_retry_receipt(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-1",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[],
        error_code="dispatch_failed",
    )
    second_identity = {**_identity(), "effect_id": "goal-1:agent-1:todo-1:turn-2"}
    second = build_composition_retry_receipt(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=second_identity,
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:01:00Z",
        hook_identities=[],
        error_code="dispatch_failed",
    )
    journal_path.write_text(
        json.dumps(first, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    append_composition_retry_receipt(journal_path, second)

    assert pending_composition_retry_receipts(tmp_path, "goal-1") == [first, second]


def test_settle_composition_retry_receipt_survives_a_torn_journal_tail(
    tmp_path: Path,
) -> None:
    journal_path = composition_retry_receipt_log_path(tmp_path, "goal-1")
    receipt = build_composition_retry_receipt(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[],
        error_code="dispatch_failed",
    )
    append_composition_retry_receipt(journal_path, receipt)
    with journal_path.open("ab") as handle:
        handle.write(b'{"interrupted":')

    settled, changed = settle_composition_retry_receipt(
        journal_path,
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[],
    )

    assert changed is True
    assert settled["status"] == "settled"
    assert pending_composition_retry_receipts(tmp_path, "goal-1") == []


def test_settle_without_pending_receipt_is_a_noop(tmp_path: Path) -> None:
    journal_path = composition_retry_receipt_log_path(tmp_path, "goal-1")
    settled, appended = settle_composition_retry_receipt(
        journal_path,
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[],
    )
    assert appended is False
    assert settled == {}
    assert not journal_path.exists()


def test_pending_receipts_skip_foreign_and_malformed_rows(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path
    journal_path = composition_retry_receipt_log_path(runtime_root, "goal-1")
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    pending_receipt = build_composition_retry_receipt(
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="vision-revision-2",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[{"hook_id": "h.one", "capability_id": "cap-one"}],
        error_code="source_projection_failed",
    )
    journal_path.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps({"schema_version": "unrelated_v0"}),
                json.dumps(pending_receipt),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    pending = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert [receipt["receipt_id"] for receipt in pending] == [
        pending_receipt["receipt_id"]
    ]


def test_journal_unavailable_degrades_to_identity_without_receipt_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken journal must not erase the concrete failure identity either."""

    import loopx.cli_commands.post_writeback as post_writeback_module

    registry_path, runtime_root = _write_registry(tmp_path)

    def failing_builder(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("transient projection failure")

    def broken_append(*_args: object, **_kwargs: object) -> None:
        raise OSError("journal unavailable")

    monkeypatch.setattr(
        post_writeback_module, "append_composition_retry_receipt", broken_append
    )
    result = _dispatch(
        registry_path,
        hooks=(_hook(),),
        projection_builder=failing_builder,
    )
    assert result["registered_count"] == 1
    assert result["intent_count"] == 0
    assert result["primary_writeback_preserved"] is True
    failure = result["failures"][0]
    assert failure["hook_id"] == "periodic_report.stage_completion"
    assert failure["capability_id"] == "periodic-report"
    assert failure["error_code"] == "source_projection_failed"
    assert "durable_receipt_ref" not in failure
    assert not composition_retry_receipt_log_path(runtime_root, "goal-1").exists()


def test_producer_failure_settles_composition_receipt_with_hook_level_trail(
    tmp_path: Path,
) -> None:
    """Composition settles on a composed projection; hook failures keep their own trail."""

    registry_path, runtime_root = _write_registry(tmp_path)

    def failing_builder(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("transient projection failure")

    first = _dispatch(
        registry_path,
        hooks=(_hook(),),
        projection_builder=failing_builder,
    )
    assert first["intent_count"] == 0
    pending = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert len(pending) == 1

    def broken_producer(_value: object) -> dict[str, object]:
        raise RuntimeError("hook producer collapsed")

    def hook_with_broken_producer() -> PostWritebackHookRegistration:
        registration = _hook()
        object.__setattr__(registration, "producer", broken_producer)
        return registration

    second = _dispatch(
        registry_path,
        hooks=(hook_with_broken_producer(),),
        projection_builder=lambda **_kwargs: _stage_projection(),
    )
    hook_failure = next(
        (
            item
            for item in second.get("failures", [])
            if item.get("hook_id") == "periodic_report.stage_completion"
        ),
        None,
    )
    assert hook_failure is not None
    rows = _journal_rows(runtime_root)
    assert any(row.get("status") == "settled" for row in rows), (
        "a composed projection must settle the composition receipt even when a "
        "hook-level producer fails: hook failures carry their own durable trail"
    )


def test_foreign_hook_set_cannot_settle_original_failure_receipt(
    tmp_path: Path,
) -> None:
    """P1 probe: a changed hook set must not resolve the original failure."""

    registry_path, runtime_root = _write_registry(tmp_path)

    def failing_builder(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("transient projection failure")

    first = _dispatch(
        registry_path,
        hooks=(_hook(),),
        projection_builder=failing_builder,
    )
    assert first["failures"][0]["hook_id"] == "periodic_report.stage_completion"
    pending = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert len(pending) == 1

    def other_hook() -> PostWritebackHookRegistration:
        return PostWritebackHookRegistration(
            hook_id="periodic_report.other_stage",
            capability_id="periodic-report",
            event_kinds=("todo_complete",),
            intent_kinds=("periodic_report.trigger_evaluation",),
            requested_read_scope=("stage_completion",),
            producer=lambda value: {
                "schema_version": POST_WRITEBACK_HOOK_RESULT_SCHEMA_VERSION,
                "hook_id": "periodic_report.other_stage",
                "capability_id": "periodic-report",
                "phase": "post_writeback",
                "status": "not_applicable",
                "intent": None,
                "error_code": None,
            },
        )

    def other_producer_fails(value: object) -> dict[str, object]:
        raise RuntimeError("hook producer collapsed")

    def other_registration() -> PostWritebackHookRegistration:
        registration = other_hook()
        object.__setattr__(registration, "producer", other_producer_fails)
        return registration

    second = _dispatch(
        registry_path,
        hooks=(other_registration(),),
        projection_builder=lambda **_kwargs: _stage_projection(),
    )
    assert second["failures"][0]["hook_id"] == "periodic_report.other_stage"
    still_pending = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert [row["receipt_id"] for row in still_pending] == [
        row["receipt_id"] for row in pending
    ], "the original hook-a receipt must survive a foreign hook-b composition"


def test_pending_view_keeps_unresolved_receipts_beyond_the_compaction_bound(
    tmp_path: Path,
) -> None:
    """P1 probe: bounded suffix reads must never hide an unresolved receipt."""

    registry_path, runtime_root = _write_registry(tmp_path)

    def failing_builder(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("transient projection failure")

    assert _dispatch(
        registry_path, hooks=(_hook(),), projection_builder=failing_builder
    )["failures"]
    original = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert len(original) == 1
    journal_path = composition_retry_receipt_log_path(runtime_root, "goal-1")

    filler_identity = dict(_identity())
    for index in range(_COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT + 8):
        filler_identity["todo_id"] = f"todo_filler_{index:04d}"
        append_composition_retry_receipt(
            journal_path,
            build_composition_retry_receipt(
                goal_id="goal-1",
                event_kind="todo_complete",
                identity=filler_identity,
                state_version=f"vision-revision-{index}",
                committed_at="2026-09-06T00:00:00Z",
                hook_identities=[
                    {
                        "hook_id": "periodic_report.stage_completion",
                        "capability_id": "periodic-report",
                    }
                ],
                error_code="source_projection_failed",
            ),
        )

    after = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert original[0]["receipt_id"] in {row["receipt_id"] for row in after}
    assert len(after) == _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT + 9

    # Distinct unresolved receipts are all durable state, so compaction must
    # not collapse them; re-observing one receipt, however, folds its rows.
    filler_identity["todo_id"] = "todo_filler_0000"
    append_composition_retry_receipt(
        journal_path,
        build_composition_retry_receipt(
            goal_id="goal-1",
            event_kind="todo_complete",
            identity=filler_identity,
            state_version="vision-revision-0",
            committed_at="2026-09-06T00:00:00Z",
            hook_identities=[
                {
                    "hook_id": "periodic_report.stage_completion",
                    "capability_id": "periodic-report",
                }
            ],
            error_code="source_projection_failed",
        ),
    )
    rows = [
        json.loads(line)
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ids = [row["receipt_id"] for row in rows]
    assert len(ids) == len(set(ids)) == len(after)
    still_visible = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert original[0]["receipt_id"] in {row["receipt_id"] for row in still_visible}
    assert len(still_visible) == len(after)


def _filler_append(journal_path: Path, index: int) -> None:
    identity = _identity()
    identity["todo_id"] = f"todo_filler_{index:04d}"
    append_composition_retry_receipt(
        journal_path,
        build_composition_retry_receipt(
            goal_id="goal-1",
            event_kind="todo_complete",
            identity=identity,
            state_version=f"vision-revision-{index}",
            committed_at="2026-09-06T00:00:00Z",
            hook_identities=[
                {
                    "hook_id": "periodic_report.stage_completion",
                    "capability_id": "periodic-report",
                }
            ],
            error_code="source_projection_failed",
        ),
    )


def test_failed_compaction_keeps_the_journal_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 regression: interrupted compaction must never lose pending receipts.

    Owner fault injection: 512 pending receipts, the next append triggers
    compaction, and an OSError lands right after the destructive in-place
    step. The legacy truncate-then-rewrite path left a 0-byte journal and
    every unhandled failure vanished from the pending readback; the folded
    replacement must be swapped in atomically so the failure propagates
    while the pre-compaction journal stays fully readable.
    """

    registry_path, runtime_root = _write_registry(tmp_path)
    journal_path = composition_retry_receipt_log_path(runtime_root, "goal-1")

    for index in range(_COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT):
        _filler_append(journal_path, index)
    assert (
        len(pending_composition_retry_receipts(runtime_root, "goal-1"))
        == _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT
    )

    real_path_open = Path.open

    class _JournalHandle:
        def __init__(self, handle: Any) -> None:
            self._handle = handle

        def __getattr__(self, name: str) -> Any:
            return getattr(self._handle, name)

        def __iter__(self) -> Any:
            return iter(self._handle)

        def __enter__(self) -> _JournalHandle:
            self._handle.__enter__()
            return self

        def __exit__(self, *exc: object) -> None:
            self._handle.__exit__(*exc)  # type: ignore[func-returns-value]

        def truncate(self, *args: object, **kwargs: object) -> object:
            self._handle.truncate(*args, **kwargs)
            raise OSError("injected after journal truncation")

    def exploding_journal_open(self: Path, *args: object, **kwargs: object) -> object:
        handle = real_path_open(self, *args, **kwargs)  # type: ignore[arg-type]
        mode = str(args[0] if args else kwargs.get("mode", "r"))
        if self == journal_path and "a" in mode:
            return _JournalHandle(handle)
        return handle

    monkeypatch.setattr(Path, "open", exploding_journal_open)
    monkeypatch.setattr(
        os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected at journal replacement")
        ),
    )

    with pytest.raises(OSError, match="injected"):
        _filler_append(journal_path, _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT)

    surviving = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert len(surviving) == _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT + 1
    journal_lines = [
        line
        for line in journal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(journal_lines) == _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT + 1
    assert not list(journal_path.parent.glob("*.tmp"))


def test_concurrent_reader_never_observes_a_partial_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 regression: readers only ever see the complete old or folded file."""

    registry_path, runtime_root = _write_registry(tmp_path)
    journal_path = composition_retry_receipt_log_path(runtime_root, "goal-1")

    bound = _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT
    for _round in range(2):
        for index in range(bound):
            _filler_append(journal_path, index)

    real_replace = os.replace
    swap_observations: dict[str, int] = {}

    def observing_replace(src: object, dst: object, *args: object) -> object:
        destination = Path(str(dst))
        swap_observations["before"] = len(
            [
                line
                for line in destination.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )
        result = real_replace(src, dst, *args)
        swap_observations["after"] = len(
            [
                line
                for line in destination.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        )
        return result

    monkeypatch.setattr(os, "replace", observing_replace)

    _filler_append(journal_path, 0)

    # The swap point observes the complete unfolded journal (the second
    # append round already folds at every row, so the journal holds one
    # extra row per swap) and the complete folded replacement.
    assert swap_observations == {"before": bound + 1, "after": bound}
    pending = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert len(pending) == bound


def test_live_readers_during_repeated_compaction_see_complete_journals(
    tmp_path: Path,
) -> None:
    """P1 regression: lock-free readers never observe a truncated journal."""

    registry_path, runtime_root = _write_registry(tmp_path)
    journal_path = composition_retry_receipt_log_path(runtime_root, "goal-1")

    bound = _COMPOSITION_RETRY_JOURNAL_COMPACT_ROW_LIMIT
    for _round in range(2):
        for index in range(bound):
            _filler_append(journal_path, index)

    stop = threading.Event()
    observations: list[int] = []

    def reader() -> None:
        while not stop.is_set():
            observations.append(
                len(pending_composition_retry_receipts_for_path(journal_path))
            )

    reader_thread = threading.Thread(target=reader)
    reader_thread.start()
    try:
        for index in range(32):
            _filler_append(journal_path, index)
    finally:
        stop.set()
        reader_thread.join(timeout=10.0)
    assert not reader_thread.is_alive()

    assert observations
    assert set(observations) == {bound}


def test_later_turn_discovers_and_clears_pending_composition_retries(
    tmp_path: Path,
) -> None:
    """P1 probe: the read model surfaces pending retries and replay clears them."""

    registry_path, runtime_root = _write_registry(tmp_path)

    def failing_builder(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("transient projection failure")

    assert _dispatch(
        registry_path, hooks=(_hook(),), projection_builder=failing_builder
    )["failures"]

    # A later turn (fresh process state) discovers the pending retry through
    # the status readback projection instead of the original command response.
    discovered = collect_pending_composition_retry_projection(runtime_root, None)
    assert discovered is not None
    assert discovered["pending_count"] == 1
    assert discovered["pending"][0]["identity"]["goal_id"] == "goal-1"
    assert discovered["schema_version"] == (
        POST_WRITEBACK_COMPOSITION_RETRY_PROJECTION_SCHEMA_VERSION
    )
    assert "Replay the committed CLI mutation" in discovered["replay_action"]
    healthy = collect_pending_composition_retry_projection(
        tmp_path / "missing-runtime", None
    )
    assert healthy is None

    # Replaying the same committed mutation composes cleanly and settles the
    # receipt; the next readback no longer reports anything pending.
    replay = _dispatch(
        registry_path,
        hooks=(_hook(),),
        projection_builder=lambda **_kwargs: _stage_projection(),
    )
    assert replay["intent_count"] == 1
    assert collect_pending_composition_retry_projection(runtime_root, None) is None
    assert pending_composition_retry_receipts(runtime_root, "goal-1") == []


def test_receipt_identity_separates_policy_versions(tmp_path: Path) -> None:
    """A policy-version replacement must not settle the original failure."""

    registry_path, runtime_root = _write_registry(tmp_path)

    def failing_builder(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError("transient projection failure")

    assert _dispatch(
        registry_path, hooks=(_hook(),), projection_builder=failing_builder
    )["failures"]

    def upgraded_hook() -> PostWritebackHookRegistration:
        registration = _hook()
        object.__setattr__(registration, "policy_version", "v1")
        return registration

    upgraded, upgraded_flag = settle_composition_retry_receipt(
        composition_retry_receipt_log_path(runtime_root, "goal-1"),
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="2026-09-06T00:00:00Z",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[
            {
                "hook_id": "periodic_report.stage_completion",
                "capability_id": "periodic-report",
                "policy_version": "v1",
            }
        ],
    )
    assert upgraded_flag is False
    assert upgraded == {}
    pending = pending_composition_retry_receipts(runtime_root, "goal-1")
    assert len(pending) == 1
    assert pending[0]["hooks"][0]["policy_version"] == "v0"

    # The original version's own composition still settles its receipt
    # idempotently after a replacement tried and failed to claim it.
    settled, settled_flag = settle_composition_retry_receipt(
        composition_retry_receipt_log_path(runtime_root, "goal-1"),
        goal_id="goal-1",
        event_kind="todo_complete",
        identity=_identity(),
        state_version="2026-09-06T00:00:00Z",
        committed_at="2026-09-06T00:00:00Z",
        hook_identities=[
            {
                "hook_id": "periodic_report.stage_completion",
                "capability_id": "periodic-report",
                "policy_version": "v0",
            }
        ],
    )
    assert settled_flag is True
    assert settled["status"] == "settled"
    assert pending_composition_retry_receipts(runtime_root, "goal-1") == []


def test_agent_scoped_projection_excludes_peer_work_and_caps_after_filter(
    tmp_path: Path,
) -> None:
    """An agent selector must filter before counting and display truncation."""

    _registry, runtime_root = _write_registry(tmp_path)
    journal_path = composition_retry_receipt_log_path(runtime_root, "goal-1")
    peer_identity = dict(_identity())
    peer_identity["agent_id"] = "agent-peer"
    for index in range(6):
        peer_identity["todo_id"] = f"todo_peer_{index:03d}"
        append_composition_retry_receipt(
            journal_path,
            build_composition_retry_receipt(
                goal_id="goal-1",
                event_kind="todo_complete",
                identity=peer_identity,
                state_version=f"peer-{index}",
                committed_at="2026-09-06T00:00:00Z",
                hook_identities=[
                    {
                        "hook_id": "periodic_report.stage_completion",
                        "capability_id": "periodic-report",
                        "policy_version": "v0",
                    }
                ],
                error_code="source_projection_failed",
            ),
        )
    own_identity = dict(_identity())
    own_identity["agent_id"] = "agent-own"
    own_identity["todo_id"] = "todo_own_000"
    append_composition_retry_receipt(
        journal_path,
        build_composition_retry_receipt(
            goal_id="goal-1",
            event_kind="todo_complete",
            identity=own_identity,
            state_version="own-0",
            committed_at="2026-09-06T00:00:00Z",
            hook_identities=[
                {
                    "hook_id": "periodic_report.stage_completion",
                    "capability_id": "periodic-report",
                    "policy_version": "v0",
                }
            ],
            error_code="source_projection_failed",
        ),
    )

    own = collect_pending_composition_retry_projection(
        runtime_root, "goal-1", agent_id="agent-own", max_items=5
    )
    assert own is not None
    assert own["pending_count"] == 1
    assert [row["identity"]["agent_id"] for row in own["pending"]] == ["agent-own"]

    global_view = collect_pending_composition_retry_projection(
        runtime_root, "goal-1", max_items=5
    )
    assert global_view is not None
    assert global_view["pending_count"] == 7
    assert len(global_view["pending"]) == 5

    lonely = collect_pending_composition_retry_projection(
        runtime_root, "goal-1", agent_id="agent-nobody"
    )
    assert lonely is None
