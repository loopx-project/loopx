"""A copied goal runs directory must still feed the Python spend ledger.

Spend and void index rows store an absolute ``json_path`` and leave the event
in the JSON artifact. After ``goals/<goalId>/runs`` is copied to another
runtime, that stored path still names the old tree. The ledger, history, and
usage summary have to read the same-named regular file in the current runs
directory without rewriting the index row or opening an unrelated path.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loopx.control_plane.quota.ledger_readback import (
    QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING,
    QUOTA_EVENT_UNREADABLE_EVENT_INVALID,
    load_quota_event_from_run,
    quota_slot_observation,
)
from loopx.control_plane.quota.usage_summary import build_usage_summary
from loopx.history import collect_history
from loopx.quota import goal_quota_with_spend_ledger, quota_status
from loopx.status import build_status_runtime_summaries


GOAL_ID = "quota-ledger-relocated"
REQUEST_DIGEST = "digest-spend-original"
VOID_DIGEST = "digest-void-original"


def _goal() -> dict[str, Any]:
    return {
        "id": GOAL_ID,
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": 60,
        },
    }


def _parse_timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _commit(schema: str, effect_id: str, digest: str) -> dict[str, str]:
    return {
        "schema_version": schema,
        "effect_id": effect_id,
        "request_digest": digest,
    }


def _write_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"quota_event": event}), encoding="utf-8")


def _write_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _row(
    path: Path,
    *,
    generated_at: datetime,
    classification: str,
    commit_field: str | None = None,
    commit: dict[str, str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "generated_at": generated_at.isoformat(),
        "goal_id": GOAL_ID,
        "classification": classification,
        "json_path": str(path),
    }
    if commit_field is not None and commit is not None:
        row[commit_field] = commit
    row.update(extra)
    return row


def _copy_runs(runtime_a: Path, runtime_b: Path) -> tuple[Path, bytes]:
    source = runtime_a / "goals" / GOAL_ID / "runs"
    target = runtime_b / "goals" / GOAL_ID / "runs"
    shutil.copytree(source, target)
    return target, (target / "index.jsonl").read_bytes()


def test_copied_runtime_spend_and_void_stay_in_the_ledger(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    spend_at = now - timedelta(minutes=20)
    void_at = now - timedelta(minutes=10)
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    runs_a = runtime_a / "goals" / GOAL_ID / "runs"
    spend_path = runs_a / "2026-03-01T11-40-00Z-quota-spend.json"
    void_path = runs_a / "2026-03-01T11-50-00Z-quota-void.json"
    spend_commit = _commit(
        "quota_spend_commit_receipt_v0",
        "effect-spend",
        REQUEST_DIGEST,
    )
    void_commit = _commit(
        "quota_void_commit_receipt_v0",
        "effect-void",
        VOID_DIGEST,
    )
    spend_event = {
        "event_type": "quota_slot_spent",
        "slots": 2,
        "run_generated_at": spend_at.isoformat(),
    }
    void_event = {
        "event_type": "quota_slot_voided",
        "slots": 1,
        "voided_run_generated_at": spend_at.isoformat(),
    }
    _write_record(
        spend_path,
        {
            "generated_at": spend_at.isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_spent",
            "quota_event": spend_event,
            "quota_spend_commit": spend_commit,
        },
    )
    _write_record(
        void_path,
        {
            "generated_at": void_at.isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_voided",
            "quota_event": void_event,
            "quota_void_commit": void_commit,
        },
    )
    spend_bytes = spend_path.read_bytes()
    void_bytes = void_path.read_bytes()
    rows = [
        _row(
            spend_path,
            generated_at=spend_at,
            classification="quota_slot_spent",
            commit_field="quota_spend_commit",
            commit=spend_commit,
        ),
        _row(
            void_path,
            generated_at=void_at,
            classification="quota_slot_voided",
            commit_field="quota_void_commit",
            commit=void_commit,
        ),
    ]
    index_path = runs_a / "index.jsonl"
    index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    goal = _goal()

    # The original tree is already inside its own runs directory.
    original = goal_quota_with_spend_ledger(goal, rows, now=now, runs_dir=runs_a)
    assert original["spent_slots"] == 1
    assert original["spend_event_count"] == 1
    assert original["void_event_count"] == 1
    assert "unreadable_event_count" not in original

    runs_b, index_bytes = _copy_runs(runtime_a, runtime_b)
    shutil.rmtree(runtime_a)
    copied_rows = [
        json.loads(line)
        for line in (runs_b / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert copied_rows[0]["json_path"] == str(spend_path)
    assert copied_rows[1]["json_path"] == str(void_path)
    assert not spend_path.exists()

    spend_observation, spend_reason = quota_slot_observation(
        copied_rows[0],
        runs_dir=runs_b,
        goal_id=GOAL_ID,
    )
    void_observation, void_reason = quota_slot_observation(
        copied_rows[1],
        runs_dir=runs_b,
        goal_id=GOAL_ID,
    )
    assert spend_reason is None
    assert void_reason is None
    assert spend_observation == ("spent", spend_at.isoformat(), 2)
    assert void_observation == ("voided", spend_at.isoformat(), 1)
    loaded = load_quota_event_from_run(
        copied_rows[0],
        runs_dir=runs_b,
        goal_id=GOAL_ID,
    )
    assert loaded == spend_event

    ledger = goal_quota_with_spend_ledger(goal, copied_rows, now=now, runs_dir=runs_b)
    assert ledger["spent_slots"] == 1
    assert ledger["spend_event_count"] == 1
    assert ledger["void_event_count"] == 1
    assert "unreadable_event_count" not in ledger
    status = quota_status(
        {**goal, "quota": {**goal["quota"], **ledger}},
        waiting_on="codex",
    )
    assert status["state"] == "eligible"

    summary = build_usage_summary(
        {"runs": copied_rows},
        parse_timestamp=_parse_timestamp,
        runtime_root=runtime_b,
    )
    assert summary["totals"]["quota_spend_slots_24h"] == 1
    assert summary["totals"]["quota_spend_slots_7d"] == 1
    assert "unreadable_event_count_24h" not in summary["totals"]
    assert "unreadable_event_count_7d" not in summary["totals"]
    assert summary["goals"][0]["quota_spend_slots_24h"] == 1

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"goals": [goal]}), encoding="utf-8")
    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_b,
        goal_id=GOAL_ID,
        limit=10,
    )
    history_quota = history["goals"][0]["quota"]
    assert history_quota["spent_slots"] == 1
    assert history_quota["spend_event_count"] == 1
    assert history_quota["void_event_count"] == 1
    assert "unreadable_event_count" not in history_quota

    status_usage = build_status_runtime_summaries(
        history=history,
        queue={"items": []},
        runtime_root=runtime_b,
        goal_id_filter=GOAL_ID,
        display_limit=10,
        todo_index_limit=10,
    )["usage_summary"]
    assert status_usage["totals"]["quota_spend_slots_24h"] == 1
    assert "unreadable_event_count_24h" not in status_usage["totals"]

    # Read-time remap leaves the copied index, digests, and artifact bytes alone.
    assert (runs_b / "index.jsonl").read_bytes() == index_bytes
    assert copied_rows[0]["json_path"] == str(spend_path)
    assert copied_rows[0]["quota_spend_commit"]["request_digest"] == REQUEST_DIGEST
    assert copied_rows[1]["quota_void_commit"]["request_digest"] == VOID_DIGEST
    assert (runs_b / spend_path.name).read_bytes() == spend_bytes
    assert (runs_b / void_path.name).read_bytes() == void_bytes


def test_relocated_reader_rejects_paths_that_are_not_same_goal_run_files(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    spend_at = now - timedelta(minutes=10)
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    runs_b = runtime_b / "goals" / GOAL_ID / "runs"
    runs_b.mkdir(parents=True)
    event = {
        "event_type": "quota_slot_spent",
        "slots": 50,
        "run_generated_at": spend_at.isoformat(),
    }

    def rejected(stored: Path, *, decoy_name: str | None = None) -> None:
        if decoy_name is not None:
            _write_event(runs_b / decoy_name, event)
        row = _row(
            stored,
            generated_at=spend_at,
            classification="quota_slot_spent",
            commit_field="quota_spend_commit",
            commit=_commit(
                "quota_spend_commit_receipt_v0",
                "effect-spend",
                REQUEST_DIGEST,
            ),
        )
        contribution, reason = quota_slot_observation(
            row,
            runs_dir=runs_b,
            goal_id=GOAL_ID,
        )
        assert contribution is None
        assert reason == QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING
        ledger = goal_quota_with_spend_ledger(
            _goal(),
            [row],
            now=now,
            runs_dir=runs_b,
        )
        assert ledger["spent_slots"] == 0
        assert ledger["unreadable_event_count"] == 1
        assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING]

    outside = tmp_path / "outside.json"
    _write_event(outside, event)
    rejected(outside, decoy_name="outside.json")

    other_goal = runtime_a / "goals" / "other-goal" / "runs" / "foreign.json"
    rejected(other_goal, decoy_name="foreign.json")

    traversed = runtime_a / "goals" / GOAL_ID / "runs" / ".." / "secret.json"
    rejected(traversed, decoy_name="secret.json")

    outside_target = tmp_path / "symlink-target.json"
    _write_event(outside_target, event)
    link = runs_b / "linked.json"
    link.symlink_to(outside_target)
    stored_link = runtime_a / "goals" / GOAL_ID / "runs" / "linked.json"
    rejected(stored_link)
    assert link.is_symlink()


def test_copied_commit_digest_must_match_before_the_slots_count(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    spend_at = now - timedelta(minutes=15)
    other_at = now - timedelta(minutes=5)
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    runs_a = runtime_a / "goals" / GOAL_ID / "runs"
    matched_path = runs_a / "matched-spend.json"
    conflicted_path = runs_a / "conflicted-spend.json"
    matched_commit = _commit(
        "quota_spend_commit_receipt_v0",
        "effect-matched",
        "digest-matched",
    )
    conflicted_commit = _commit(
        "quota_spend_commit_receipt_v0",
        "effect-conflicted",
        "digest-original",
    )
    matched_event = {
        "event_type": "quota_slot_spent",
        "slots": 2,
        "run_generated_at": spend_at.isoformat(),
    }
    conflicted_event = {
        "event_type": "quota_slot_spent",
        "slots": 2,
        "run_generated_at": other_at.isoformat(),
    }
    _write_record(
        matched_path,
        {
            "generated_at": spend_at.isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_spent",
            "quota_event": matched_event,
            "quota_spend_commit": matched_commit,
        },
    )
    _write_record(
        conflicted_path,
        {
            "generated_at": other_at.isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_spent",
            "quota_event": conflicted_event,
            "quota_spend_commit": conflicted_commit,
        },
    )
    rows = [
        _row(
            matched_path,
            generated_at=spend_at,
            classification="quota_slot_spent",
            commit_field="quota_spend_commit",
            commit=matched_commit,
        ),
        _row(
            conflicted_path,
            generated_at=other_at,
            classification="quota_slot_spent",
            commit_field="quota_spend_commit",
            commit=conflicted_commit,
        ),
    ]
    index_path = runs_a / "index.jsonl"
    index_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    runs_b, index_bytes = _copy_runs(runtime_a, runtime_b)
    shutil.rmtree(runtime_a)
    # The copied index keeps the original digest. The same-named artifact does not.
    conflicted_copy = runs_b / conflicted_path.name
    conflicted_record = json.loads(conflicted_copy.read_text(encoding="utf-8"))
    conflicted_record["quota_spend_commit"] = {
        **conflicted_commit,
        "request_digest": "digest-other",
    }
    conflicted_copy.write_text(json.dumps(conflicted_record), encoding="utf-8")
    copied_rows = [
        json.loads(line)
        for line in (runs_b / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert copied_rows[1]["quota_spend_commit"]["request_digest"] == "digest-original"

    matched, matched_reason = quota_slot_observation(
        copied_rows[0],
        runs_dir=runs_b,
        goal_id=GOAL_ID,
    )
    conflicted, conflicted_reason = quota_slot_observation(
        copied_rows[1],
        runs_dir=runs_b,
        goal_id=GOAL_ID,
    )
    assert matched_reason is None
    assert matched == ("spent", spend_at.isoformat(), 2)
    assert conflicted is None
    assert conflicted_reason == QUOTA_EVENT_UNREADABLE_EVENT_INVALID
    assert load_quota_event_from_run(
        copied_rows[1],
        runs_dir=runs_b,
        goal_id=GOAL_ID,
    ) is None

    goal = _goal()
    ledger = goal_quota_with_spend_ledger(goal, copied_rows, now=now, runs_dir=runs_b)
    assert ledger["spent_slots"] == 2
    assert ledger["spend_event_count"] == 1
    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_EVENT_INVALID]
    status = quota_status(
        {**goal, "quota": {**goal["quota"], **ledger}},
        waiting_on="codex",
    )
    assert status["state"] == "blocked_health"

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"goals": [goal]}), encoding="utf-8")
    history_quota = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_b,
        goal_id=GOAL_ID,
        limit=10,
    )["goals"][0]["quota"]
    assert history_quota["spent_slots"] == 2
    assert history_quota["unreadable_event_count"] == 1
    assert (runs_b / "index.jsonl").read_bytes() == index_bytes


def test_copied_historical_artifact_without_commit_metadata_stays_readable(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    spend_at = now - timedelta(minutes=10)
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    spend_path = runtime_a / "goals" / GOAL_ID / "runs" / "historical-spend.json"
    event = {
        "event_type": "quota_slot_spent",
        "slots": 1,
        "run_generated_at": spend_at.isoformat(),
    }
    _write_event(spend_path, event)
    rows = [
        _row(
            spend_path,
            generated_at=spend_at,
            classification="quota_slot_spent",
        )
    ]
    index_path = spend_path.parent / "index.jsonl"
    index_path.write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    runs_b, _index_bytes = _copy_runs(runtime_a, runtime_b)
    shutil.rmtree(runtime_a)
    copied_rows = [
        json.loads(line)
        for line in (runs_b / "index.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    ledger = goal_quota_with_spend_ledger(
        _goal(),
        copied_rows,
        now=now,
        runs_dir=runs_b,
    )

    assert ledger["spent_slots"] == 1
    assert "unreadable_event_count" not in ledger


def test_explicit_effect_ref_conflict_on_a_copied_artifact_is_unreadable(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    spend_at = now - timedelta(minutes=10)
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    spend_path = runtime_a / "goals" / GOAL_ID / "runs" / "identity-spend.json"
    commit = _commit(
        "quota_spend_commit_receipt_v0",
        "effect-identity",
        "digest-identity",
    )
    _write_record(
        spend_path,
        {
            "generated_at": spend_at.isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_spent",
            "effect_ref": "turn-b",
            "quota_event": {
                "event_type": "quota_slot_spent",
                "slots": 2,
                "run_generated_at": spend_at.isoformat(),
                "effect_ref": "turn-b",
            },
            "quota_spend_commit": commit,
        },
    )
    row = _row(
        spend_path,
        generated_at=spend_at,
        classification="quota_slot_spent",
        commit_field="quota_spend_commit",
        commit=commit,
        effect_ref="turn-a",
    )
    (spend_path.parent / "index.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    runs_b, _index_bytes = _copy_runs(runtime_a, runtime_b)
    shutil.rmtree(runtime_a)
    copied = json.loads((runs_b / "index.jsonl").read_text(encoding="utf-8"))

    contribution, reason = quota_slot_observation(
        copied,
        runs_dir=runs_b,
        goal_id=GOAL_ID,
    )

    assert contribution is None
    assert reason == QUOTA_EVENT_UNREADABLE_EVENT_INVALID
