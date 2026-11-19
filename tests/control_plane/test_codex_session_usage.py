"""End-to-end producer regression for the GH-C95 cost ledger.

Proves that a shipped host measurement source (a Codex session rollout) lands
as a typed ``run_usage_v0`` row on the real ``refresh-state`` run-write path,
that the goal's ``usage_summary`` becomes non-zero, that replaying the same
cumulative snapshot never double-counts, and that a grown rollout books only
the non-negative increment.
"""

from __future__ import annotations

import json
import threading
import tracemalloc
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from canonical_authority_fixture import promoted_create_fixture

import loopx.state_refresh as state_refresh_module
from loopx.control_plane.quota.codex_session_usage import (
    CodexSessionUsageError,
    book_codex_session_usage,
    read_codex_session_usage,
    session_usage_baseline,
)
from loopx.control_plane.quota.usage_collector import (
    UsageRowError,
    ingest_usage_into_run_record,
)
from loopx.control_plane.quota.usage_summary import build_usage_summary
from loopx.control_plane.runtime.time import parse_timestamp
from loopx.state_refresh import refresh_state_run

GOAL_ID = "codex-usage-fixture"
SESSION_ID = "019f0000-aaaa-bbbb-cccc-000000000001"
OTHER_SESSION_ID = "019f0000-aaaa-bbbb-cccc-000000000002"
MODEL = "gpt-fixture-1"


def _rollout_event(
    timestamp: str,
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> str:
    totals = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": 0,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": 0,
        "total_tokens": input_tokens + output_tokens,
    }
    return json.dumps(
        {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": totals,
                    "last_token_usage": totals,
                    "model_context_window": 400000,
                },
            },
        }
    )


def _rollout_header(session_id: str = SESSION_ID) -> list[str]:
    return [
        json.dumps(
            {
                "timestamp": "2026-08-26T01:00:00.000Z",
                "type": "session_meta",
                "payload": {
                    "session_id": session_id,
                    "timestamp": "2026-08-26T01:00:00.000Z",
                    "cwd": "/tmp/fixture-project",
                },
            }
        ),
        json.dumps(
            {
                "timestamp": "2026-08-26T01:00:01.000Z",
                "type": "turn_context",
                "payload": {"turn_id": "turn-1", "model": MODEL},
            }
        ),
    ]


def _rollout_model_context(model: str, *, turn_id: str = "turn-2") -> str:
    return json.dumps(
        {
            "timestamp": "2026-08-26T01:06:00.000Z",
            "type": "turn_context",
            "payload": {"turn_id": turn_id, "model": model},
        }
    )


def _write_rollout(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fixture_rollout(tmp_path: Path) -> Path:
    lines = [
        *_rollout_header(),
        _rollout_event(
            "2026-08-26T01:05:00.000Z",
            input_tokens=1200,
            cached_input_tokens=200,
            output_tokens=300,
        ),
    ]
    return _write_rollout(tmp_path / "rollout-fixture.jsonl", lines)


def test_read_codex_session_usage_extracts_cumulative_totals(tmp_path: Path) -> None:
    observation = read_codex_session_usage(_fixture_rollout(tmp_path))
    assert observation["input_tokens"] == 1200
    assert observation["output_tokens"] == 300
    assert observation["cache_tokens"] == 200
    assert observation["provider"] == "codex"
    assert observation["model"] == MODEL
    assert observation["session_id"] == SESSION_ID
    assert observation["source_snapshot_id"] == (
        f"codex:{SESSION_ID}:2026-08-26T01:05:00.000Z"
    )
    assert observation["measurement_kind"] == "absolute"
    assert observation["duration_ms"] == 300_000
    assert "cost_usd" not in observation  # unmeasured stays unknown, not zero


@pytest.mark.parametrize("position", [0, 2, 3])
def test_read_codex_session_usage_rejects_conflicting_session_metadata(
    tmp_path: Path, position: int,
) -> None:
    lines = _fixture_rollout(tmp_path).read_text(encoding="utf-8").splitlines()
    lines.insert(position, _rollout_header(OTHER_SESSION_ID)[0])
    rollout = _write_rollout(tmp_path / "rollout-mixed-sessions.jsonl", lines)

    with pytest.raises(CodexSessionUsageError, match="conflicting session_meta ids"):
        read_codex_session_usage(rollout)


@pytest.mark.parametrize("id_key", ["id", "session_id"])
def test_read_codex_session_usage_retains_first_same_session_clock(
    tmp_path: Path, id_key: str,
) -> None:
    rollout = _fixture_rollout(tmp_path)
    original = read_codex_session_usage(rollout)
    lines = rollout.read_text(encoding="utf-8").splitlines()
    repeated = json.loads(_rollout_header()[0])
    repeated["payload"].pop("session_id")
    repeated["payload"][id_key] = SESSION_ID
    repeated["payload"]["timestamp"] = "2026-08-26T01:06:00.000Z"
    lines.append(json.dumps(repeated))
    _write_rollout(rollout, lines)

    assert read_codex_session_usage(rollout) == original


def test_read_codex_session_usage_rejects_unidentified_metadata(tmp_path: Path) -> None:
    lines = _fixture_rollout(tmp_path).read_text(encoding="utf-8").splitlines()
    lines.insert(0, json.dumps({"type": "session_meta", "payload": {}}))
    rollout = _write_rollout(tmp_path / "rollout-unidentified-session.jsonl", lines)

    with pytest.raises(CodexSessionUsageError, match="no session_meta id"):
        read_codex_session_usage(rollout)


def test_read_codex_session_usage_requires_metadata_before_counters(tmp_path: Path) -> None:
    lines = _fixture_rollout(tmp_path).read_text(encoding="utf-8").splitlines()
    rollout = _write_rollout(
        tmp_path / "rollout-late-session.jsonl", [lines[1], lines[2], lines[0]]
    )

    with pytest.raises(CodexSessionUsageError, match="usage before session_meta"):
        read_codex_session_usage(rollout)


def test_read_codex_session_usage_binds_model_when_token_snapshot_is_observed(
    tmp_path: Path,
) -> None:
    lines = [
        *_rollout_header(),
        _rollout_event(
            "2026-08-26T01:05:00.000Z",
            input_tokens=1200,
            cached_input_tokens=200,
            output_tokens=300,
        ),
        _rollout_model_context("gpt-fixture-2"),
    ]
    rollout = _write_rollout(tmp_path / "rollout-model-switch.jsonl", lines)

    first = read_codex_session_usage(rollout)
    second = read_codex_session_usage(rollout)

    assert first["model"] == MODEL
    assert second == first


def test_read_codex_session_usage_updates_model_with_new_token_snapshot(
    tmp_path: Path,
) -> None:
    lines = [
        *_rollout_header(),
        _rollout_event(
            "2026-08-26T01:05:00.000Z",
            input_tokens=1200,
            cached_input_tokens=200,
            output_tokens=300,
        ),
        _rollout_model_context("gpt-fixture-2"),
        _rollout_event(
            "2026-08-26T01:10:00.000Z",
            input_tokens=2000,
            cached_input_tokens=350,
            output_tokens=450,
        ),
    ]
    rollout = _write_rollout(
        tmp_path / "rollout-model-switch-new-usage.jsonl", lines
    )

    observation = read_codex_session_usage(rollout)

    assert observation["model"] == "gpt-fixture-2"
    assert observation["input_tokens"] == 2000


def test_read_codex_session_usage_requires_model_before_token_snapshot(
    tmp_path: Path,
) -> None:
    lines = [
        json.dumps(
            {
                "timestamp": "2026-08-26T01:00:00.000Z",
                "type": "session_meta",
                "payload": {"session_id": SESSION_ID},
            }
        ),
        _rollout_event(
            "2026-08-26T01:05:00.000Z",
            input_tokens=1200,
            cached_input_tokens=200,
            output_tokens=300,
        ),
        _rollout_model_context("gpt-fixture-2"),
    ]
    rollout = _write_rollout(tmp_path / "rollout-late-model.jsonl", lines)

    with pytest.raises(CodexSessionUsageError, match="no turn_context model"):
        read_codex_session_usage(rollout)


def test_read_codex_session_usage_tolerates_torn_trailing_line(tmp_path: Path) -> None:
    lines = [
        *_rollout_header(),
        _rollout_event(
            "2026-08-26T01:05:00.000Z",
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=5,
        ),
        '{"timestamp":"2026-08-26T01:06:00.000Z","type":"event_msg","payl',
    ]
    observation = read_codex_session_usage(
        _write_rollout(tmp_path / "rollout-torn.jsonl", lines)
    )
    assert observation["input_tokens"] == 10


def test_read_codex_session_usage_fails_closed_on_corrupt_middle_line(
    tmp_path: Path,
) -> None:
    # A malformed line followed by valid events is file damage, not a torn
    # tail: parsing on could silently accept a stale cumulative snapshot.
    lines = [
        *_rollout_header(),
        _rollout_event(
            "2026-08-26T01:05:00.000Z",
            input_tokens=100,
            cached_input_tokens=0,
            output_tokens=10,
        ),
        '{"timestamp":"2026-08-26T01:06:00.000Z","type":"event_msg","payl',
        _rollout_event(
            "2026-08-26T01:07:00.000Z",
            input_tokens=200,
            cached_input_tokens=0,
            output_tokens=20,
        ),
    ]
    path = _write_rollout(tmp_path / "rollout-corrupt-middle.jsonl", lines)
    with pytest.raises(CodexSessionUsageError, match="line 4 is corrupt"):
        read_codex_session_usage(path)


def test_read_codex_session_usage_fails_closed_without_token_counts(
    tmp_path: Path,
) -> None:
    path = _write_rollout(tmp_path / "rollout-empty.jsonl", _rollout_header())
    with pytest.raises(CodexSessionUsageError, match="no token_count"):
        read_codex_session_usage(path)


def test_read_codex_session_usage_fails_closed_without_session_meta(
    tmp_path: Path,
) -> None:
    lines = [
        _rollout_event(
            "2026-08-26T01:05:00.000Z",
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=5,
        )
    ]
    path = _write_rollout(tmp_path / "rollout-no-meta.jsonl", lines)
    with pytest.raises(CodexSessionUsageError, match="session_meta"):
        read_codex_session_usage(path)


def test_read_codex_session_usage_fails_closed_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(CodexSessionUsageError, match="cannot read"):
        read_codex_session_usage(tmp_path / "missing.jsonl")


def _index_row(
    *,
    generated_at: str,
    source_snapshot_id: str,
    input_tokens: int,
    output_tokens: int,
    measurement_kind: str,
    cache_tokens: int | None = None,
    duration_ms: int | None = None,
) -> str:
    usage: dict[str, Any] = {
        "schema_version": "run_usage_v0",
        "measurement_kind": measurement_kind,
        "source_snapshot_id": source_snapshot_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "provider": "codex",
        "model": MODEL,
    }
    if cache_tokens is not None:
        usage["cache_tokens"] = cache_tokens
    if duration_ms is not None:
        usage["duration_ms"] = duration_ms
    return json.dumps(
        {
            "generated_at": generated_at,
            "classification": "state_refreshed",
            "json_path": f"runs/{generated_at}.json",
            "markdown_path": f"runs/{generated_at}.md",
            "usage": usage,
        }
    )


def test_session_usage_baseline_missing_index_means_first_observation(
    tmp_path: Path,
) -> None:
    assert session_usage_baseline(tmp_path / "index.jsonl", SESSION_ID) is None


@pytest.mark.parametrize("padding", ["", "\u0085", "\u2028"])
def test_session_usage_baseline_telescopes_only_its_own_session_rows(
    tmp_path: Path, padding: str,
) -> None:
    index_path = tmp_path / "index.jsonl"
    index_path.write_text(
        (padding + "\n").join(
            [
                _index_row(
                    generated_at="2026-08-26T01:05:00",
                    source_snapshot_id=f"codex:{SESSION_ID}:t1",
                    measurement_kind="absolute",
                    input_tokens=100,
                    output_tokens=10,
                    cache_tokens=20,
                    duration_ms=1000,
                ),
                _index_row(
                    generated_at="2026-08-26T01:06:00",
                    source_snapshot_id=f"codex:{OTHER_SESSION_ID}:t1",
                    measurement_kind="absolute",
                    input_tokens=50,
                    output_tokens=5,
                ),
                _index_row(
                    generated_at="2026-08-26T01:07:00",
                    source_snapshot_id=f"codex:{SESSION_ID}:t2",
                    measurement_kind="delta",
                    input_tokens=40,
                    output_tokens=4,
                    cache_tokens=10,
                    duration_ms=500,
                ),
                json.dumps({"generated_at": "2026-08-26T01:08:00"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    basis = session_usage_baseline(index_path, SESSION_ID)
    assert basis is not None
    assert basis["input_tokens"] == 140
    assert basis["output_tokens"] == 14
    assert basis["cache_tokens"] == 30
    assert basis["duration_ms"] == 1500
    assert basis["cost_usd"] is None
    assert basis["source_snapshot_id"] == f"codex:{SESSION_ID}:t2"
    assert basis["provider"] == "codex"
    assert basis["model"] == MODEL

    other = session_usage_baseline(index_path, OTHER_SESSION_ID)
    assert other is not None
    assert other["input_tokens"] == 50


def test_session_usage_baseline_fails_closed_on_corrupt_index_row(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "index.jsonl"
    index_path.write_text('{"generated_at": "2026-08-26T01:05:00"\n', encoding="utf-8")
    with pytest.raises(CodexSessionUsageError, match="row 1 is corrupt"):
        session_usage_baseline(index_path, SESSION_ID)


def test_booking_reads_the_complete_ledger_without_buffering_other_history(
    tmp_path: Path,
) -> None:
    index_path = tmp_path / "index.jsonl"
    with index_path.open("w", encoding="utf-8") as stream:
        stream.write(_index_row(
            generated_at="2026-08-26T01:01:00", source_snapshot_id=f"codex:{SESSION_ID}:t1",
            input_tokens=700, output_tokens=100, cache_tokens=0,
            measurement_kind="absolute",
        ) + "\n")
        unrelated = json.dumps({"recommended_action": "x" * 8192}) + "\n"
        for _ in range(512):
            stream.write(unrelated)
        stream.write(_index_row(
            generated_at="2026-08-26T01:02:00", source_snapshot_id=f"codex:{SESSION_ID}:t2",
            input_tokens=100, output_tokens=50, cache_tokens=0,
            measurement_kind="delta",
        ))
    rollout = _fixture_rollout(tmp_path)
    record: dict[str, Any] = {}
    index_record: dict[str, Any] = {}
    tracemalloc.start()
    try:
        book_codex_session_usage(record, rollout, index_path, index_record=index_record)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024
    assert record["usage"]["measurement_kind"] == "delta"
    assert record["usage"]["input_tokens"] == 400
    assert record["usage"]["output_tokens"] == 150
    assert record["usage"] == index_record["usage"]


def test_index_read_failure_does_not_publish_a_partial_usage_booking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    index_path = tmp_path / "index.jsonl"
    index_path.write_text(_index_row(
        generated_at="2026-08-26T01:01:00", source_snapshot_id=f"codex:{SESSION_ID}:t1",
        input_tokens=700, output_tokens=100, measurement_kind="absolute",
    ) + "\n", encoding="utf-8")
    rollout = _fixture_rollout(tmp_path)
    original_open = Path.open

    class FailedIndex:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, *args):
            raise PermissionError("fixture index read failed")

        def __iter__(self):
            yield next(self.stream)
            raise PermissionError("fixture index read failed")

    def failed_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return FailedIndex(stream) if path == index_path else stream

    monkeypatch.setattr(Path, "open", failed_open)
    record: dict[str, Any] = {}
    index_record: dict[str, Any] = {}
    with pytest.raises(CodexSessionUsageError, match="cannot read run index"):
        book_codex_session_usage(record, rollout, index_path, index_record=index_record)
    assert record == index_record == {}


@pytest.mark.parametrize(
    ("mutation", "error_match"),
    [
        ({"input_tokens": 1200.5}, "input_tokens must be a whole number"),
        ({"duration_ms": 300_000.5}, "duration_ms must be a whole number"),
        ({"schema_version": None}, "schema_version"),
        ({"measurement_kind": "estimate"}, "measurement_kind"),
        ({"provider": "other-provider"}, "provider must be codex"),
    ],
    ids=[
        "fractional-token",
        "fractional-duration",
        "missing-schema",
        "illegal-measurement-kind",
        "wrong-provider",
    ],
)
def test_book_codex_session_usage_fails_closed_on_invalid_baseline_without_appending(
    tmp_path: Path,
    mutation: dict[str, Any],
    error_match: str,
) -> None:
    rollout = _fixture_rollout(tmp_path)
    index_path = tmp_path / "index.jsonl"
    persisted_row = json.loads(
        _index_row(
            generated_at="2026-08-26T01:05:00",
            source_snapshot_id=f"codex:{SESSION_ID}:2026-08-26T01:05:00.000Z",
            measurement_kind="absolute",
            input_tokens=1200,
            output_tokens=300,
            cache_tokens=200,
            duration_ms=300_000,
        )
    )
    persisted_row["usage"].update(mutation)
    if "schema_version" in mutation and mutation["schema_version"] is None:
        persisted_row["usage"].pop("schema_version", None)
    index_path.write_text(json.dumps(persisted_row) + "\n", encoding="utf-8")
    index_before_retry = index_path.read_bytes()
    record: dict[str, Any] = {}
    index_record: dict[str, Any] = {}

    with pytest.raises(CodexSessionUsageError, match=error_match):
        book_codex_session_usage(
            record,
            rollout,
            index_path,
            index_record=index_record,
        )

    assert index_path.read_bytes() == index_before_retry
    assert "usage" not in record
    assert "usage" not in index_record


def _goal_fixture(tmp_path: Path, *, provider: str = "sqlite") -> tuple[Path, Path, Path]:
    registry_path, runtime_root, _ = promoted_create_fixture(
        tmp_path, provider=provider, goal_id=GOAL_ID, registered_agents=[],
    )
    return registry_path, tmp_path / "project", runtime_root


def _refresh(
    *,
    registry_path: Path,
    runtime_root: Path,
    project: Path,
    usage_codex_session: Path | None = None,
    usage_measurement: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="state_refreshed",
        recommended_action="Observe the usage fixture.",
        usage_codex_session=usage_codex_session,
        usage_measurement=usage_measurement,
        dry_run=False,
        sync_global=False,
    )


def _index_runs(runtime_root: Path) -> list[dict[str, Any]]:
    index_path = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    return [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _summary_totals(runtime_root: Path) -> dict[str, Any]:
    runs = _index_runs(runtime_root)
    for run in runs:
        run.setdefault("goal_id", GOAL_ID)
    summary = build_usage_summary({"runs": runs}, parse_timestamp=parse_timestamp)
    return summary["totals"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_refresh_state_codex_session_produces_non_zero_usage_summary(
    tmp_path: Path, provider: str,
) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path, provider=provider)
    rollout = _fixture_rollout(tmp_path)

    payload = _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )
    assert payload["ok"] is True
    assert payload["usage"]["schema_version"] == "run_usage_v0"

    runs = _index_runs(runtime_root)
    assert len(runs) == 1
    usage = runs[0]["usage"]
    assert usage["input_tokens"] == 1200
    assert usage["output_tokens"] == 300
    assert usage["cache_tokens"] == 200
    assert usage["provider"] == "codex"
    assert usage["model"] == MODEL

    totals = _summary_totals(runtime_root)
    assert totals["input_tokens_24h"] == 1200
    assert totals["output_tokens_24h"] == 300
    assert totals["cache_tokens_24h"] == 200
    assert totals["duration_ms_24h"] == 300_000


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_refresh_state_rejects_session_rebinding_and_recovers_without_rebooking(
    tmp_path: Path, provider: str,
) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path, provider=provider)
    rollout = _fixture_rollout(tmp_path)
    lines = rollout.read_text(encoding="utf-8").splitlines()
    _refresh(
        registry_path=registry_path, runtime_root=runtime_root,
        project=project, usage_codex_session=rollout,
    )
    index_path = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    before = index_path.read_bytes()
    totals = _summary_totals(runtime_root)
    _write_rollout(rollout, [*lines, _rollout_header(OTHER_SESSION_ID)[0]])

    with pytest.raises(CodexSessionUsageError, match="conflicting session_meta ids"):
        _refresh(
            registry_path=registry_path, runtime_root=runtime_root,
            project=project, usage_codex_session=rollout,
        )

    assert index_path.read_bytes() == before
    assert _summary_totals(runtime_root) == totals
    assert session_usage_baseline(index_path, OTHER_SESSION_ID) is None

    repeated = json.loads(_rollout_header()[0])
    repeated["payload"]["timestamp"] = "2026-08-26T01:06:00.000Z"
    _write_rollout(rollout, [*lines, json.dumps(repeated)])
    result = _refresh(
        registry_path=registry_path, runtime_root=runtime_root,
        project=project, usage_codex_session=rollout,
    )

    assert result["ok"] is True
    assert result["usage"]["measurement_kind"] == "delta"
    for field in ("input_tokens", "output_tokens", "cache_tokens", "duration_ms"):
        assert result["usage"][field] == 0
    assert len(_index_runs(runtime_root)) == 2
    assert _summary_totals(runtime_root)["input_tokens_24h"] == totals["input_tokens_24h"]


def test_refresh_state_replaying_same_snapshot_does_not_double_count(
    tmp_path: Path,
) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    rollout = _fixture_rollout(tmp_path)

    for _ in range(2):
        payload = _refresh(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            usage_codex_session=rollout,
        )
        assert payload["ok"] is True

    runs = _index_runs(runtime_root)
    assert len(runs) == 2
    replay_usage = runs[1]["usage"]
    assert replay_usage["measurement_kind"] == "delta"
    assert replay_usage["input_tokens"] == 0
    assert replay_usage["output_tokens"] == 0

    totals = _summary_totals(runtime_root)
    assert totals["input_tokens_24h"] == 1200
    assert totals["output_tokens_24h"] == 300
    # The appended index row is the basis advancement itself: there is no
    # second snapshot state file that a crash between writes could leave stale.
    runs_dir = runtime_root / "goals" / GOAL_ID / "runs"
    assert not (runs_dir / "usage_snapshot.json").exists()


def test_refresh_state_replays_snapshot_after_model_context_change(
    tmp_path: Path,
) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    rollout = _fixture_rollout(tmp_path)
    _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )
    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(_rollout_model_context("gpt-fixture-2") + "\n")

    payload = _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )

    assert payload["usage"]["model"] == MODEL
    runs = _index_runs(runtime_root)
    assert runs[1]["usage"]["measurement_kind"] == "delta"
    assert runs[1]["usage"]["input_tokens"] == 0
    assert runs[1]["usage"]["model"] == MODEL

    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            _rollout_event(
                "2026-08-26T01:10:00.000Z",
                input_tokens=2000,
                cached_input_tokens=350,
                output_tokens=450,
            )
            + "\n"
        )
    payload = _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )

    assert payload["usage"]["model"] == "gpt-fixture-2"
    runs = _index_runs(runtime_root)
    increment = runs[2]["usage"]
    assert increment["measurement_kind"] == "delta"
    assert increment["input_tokens"] == 800
    assert increment["output_tokens"] == 150
    assert increment["cache_tokens"] == 150
    assert increment["model"] == "gpt-fixture-2"

    payload = _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )

    assert payload["usage"]["model"] == "gpt-fixture-2"
    runs = _index_runs(runtime_root)
    replay = runs[3]["usage"]
    assert replay["measurement_kind"] == "delta"
    assert replay["input_tokens"] == 0
    assert replay["output_tokens"] == 0
    assert replay["model"] == "gpt-fixture-2"


def test_refresh_state_books_only_the_cumulative_increment(tmp_path: Path) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    rollout = _fixture_rollout(tmp_path)
    _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )

    with rollout.open("a", encoding="utf-8") as handle:
        handle.write(
            _rollout_event(
                "2026-08-26T01:10:00.000Z",
                input_tokens=2000,
                cached_input_tokens=350,
                output_tokens=450,
            )
            + "\n"
        )
    _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )

    runs = _index_runs(runtime_root)
    increment = runs[1]["usage"]
    assert increment["measurement_kind"] == "delta"
    assert increment["input_tokens"] == 800
    assert increment["output_tokens"] == 150
    assert increment["cache_tokens"] == 150
    assert increment["duration_ms"] == 300_000

    totals = _summary_totals(runtime_root)
    assert totals["input_tokens_24h"] == 2000
    assert totals["output_tokens_24h"] == 450


def test_refresh_state_interleaved_sessions_do_not_double_count(
    tmp_path: Path,
) -> None:
    """A returning session must rebase against its own baseline (A, B, A)."""
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    rollout_a = _write_rollout(
        tmp_path / "rollout-a.jsonl",
        [
            *_rollout_header(),
            _rollout_event(
                "2026-08-26T01:05:00.000Z",
                input_tokens=100,
                cached_input_tokens=0,
                output_tokens=10,
            ),
        ],
    )
    rollout_b = _write_rollout(
        tmp_path / "rollout-b.jsonl",
        [
            *_rollout_header(session_id=OTHER_SESSION_ID),
            _rollout_event(
                "2026-08-26T01:06:00.000Z",
                input_tokens=50,
                cached_input_tokens=0,
                output_tokens=5,
            ),
        ],
    )

    for rollout in (rollout_a, rollout_b):
        _refresh(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            usage_codex_session=rollout,
        )
    with rollout_a.open("a", encoding="utf-8") as handle:
        handle.write(
            _rollout_event(
                "2026-08-26T01:10:00.000Z",
                input_tokens=150,
                cached_input_tokens=0,
                output_tokens=15,
            )
            + "\n"
        )
    _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout_a,
    )

    runs = _index_runs(runtime_root)
    assert len(runs) == 3
    returning = runs[2]["usage"]
    assert returning["measurement_kind"] == "delta"
    assert returning["input_tokens"] == 50  # 150 cumulative - 100 own baseline
    assert returning["output_tokens"] == 5

    totals = _summary_totals(runtime_root)
    assert totals["input_tokens_24h"] == 200  # 150 (A) + 50 (B), never 300
    assert totals["output_tokens_24h"] == 20


def test_refresh_state_concurrent_same_session_bookings_do_not_double_count(
    tmp_path: Path,
) -> None:
    """The booking lock serializes basis read + append: exactly one non-zero booking."""
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    rollout = _fixture_rollout(tmp_path)
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def _book() -> None:
        try:
            barrier.wait(timeout=10)
            _refresh(
                registry_path=registry_path,
                runtime_root=runtime_root,
                project=project,
                usage_codex_session=rollout,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced via assertion below
            errors.append(exc)

    workers = [threading.Thread(target=_book) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert errors == []

    runs = _index_runs(runtime_root)
    assert len(runs) == 2
    booked_inputs = sorted(run["usage"]["input_tokens"] for run in runs)
    assert booked_inputs == [0, 1200]  # one absolute intake, one zero-delta replay

    totals = _summary_totals(runtime_root)
    assert totals["input_tokens_24h"] == 1200
    assert totals["output_tokens_24h"] == 300


def test_refresh_state_never_persists_non_finite_usage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Durable run rows are strict JSON even if a future producer bug slips NaN through."""
    registry_path, project, runtime_root = _goal_fixture(tmp_path)

    def _inject_nan(
        record: dict[str, Any],
        measurement: dict[str, Any] | None = None,
        *,
        previous_snapshot: Any = None,
        index_record: dict[str, Any] | None = None,
    ) -> None:
        poisoned = {
            "schema_version": "run_usage_v0",
            "measurement_kind": "absolute",
            "source_snapshot_id": "poisoned",
            "input_tokens": 1,
            "output_tokens": 1,
            "provider": "codex",
            "model": MODEL,
            "cost_usd": float("nan"),
        }
        record["usage"] = poisoned
        if index_record is not None:
            index_record["usage"] = dict(poisoned)

    monkeypatch.setattr(
        state_refresh_module, "ingest_usage_into_run_record", _inject_nan
    )
    with pytest.raises(ValueError, match="JSON compliant"):
        _refresh(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            usage_measurement={"input_tokens": 1, "output_tokens": 1},
        )
    index_path = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    assert not index_path.exists()


def test_refresh_state_rejects_non_finite_measurement(tmp_path: Path) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    with pytest.raises(UsageRowError, match="finite"):
        _refresh(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            usage_measurement={
                "input_tokens": 1,
                "output_tokens": 1,
                "provider": "fixture-host",
                "model": "fixture-model",
                "source_snapshot_id": "host-measured-nan",
                "cost_usd": float("nan"),
            },
        )
    index_path = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    assert not index_path.exists()


def test_refresh_state_without_usage_flags_keeps_usage_unknown(
    tmp_path: Path,
) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    payload = _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
    )
    assert payload["ok"] is True
    assert "usage" not in payload
    runs = _index_runs(runtime_root)
    assert "usage" not in runs[0]


def test_refresh_state_accepts_typed_host_measurement(tmp_path: Path) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    payload = _refresh(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_measurement={
            "input_tokens": 42,
            "output_tokens": 7,
            "provider": "fixture-host",
            "model": "fixture-model",
            "source_snapshot_id": "host-measured-1",
        },
    )
    assert payload["ok"] is True
    runs = _index_runs(runtime_root)
    assert runs[0]["usage"]["input_tokens"] == 42
    assert runs[0]["usage"]["provider"] == "fixture-host"


def test_refresh_state_rejects_combined_usage_inputs(tmp_path: Path) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    with pytest.raises(ValueError, match="cannot be combined"):
        _refresh(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            usage_codex_session=_fixture_rollout(tmp_path),
            usage_measurement={"input_tokens": 1, "output_tokens": 1},
        )


def test_refresh_state_fails_closed_on_malformed_rollout_without_appending(
    tmp_path: Path,
) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    bad_rollout = _write_rollout(
        tmp_path / "rollout-bad.jsonl", _rollout_header()
    )
    with pytest.raises(CodexSessionUsageError, match="no token_count"):
        _refresh(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
            usage_codex_session=bad_rollout,
        )
    index_path = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    assert not index_path.exists()


def _legacy_book_codex_usage(record, rollout_path, index_path, *, index_record=None):
    """Frozen pre-fix producer semantics for valid upgrade fixtures.

    The old reader selected cumulative totals independently of the last model
    context in the entire file. Keep this independent of the current reader.
    """
    for event in map(json.loads, rollout_path.read_text().splitlines()):
        payload = event["payload"]
        if event["type"] == "session_meta":
            session_id = payload["session_id"]
            started = datetime.fromisoformat(payload["timestamp"])
        elif event["type"] == "turn_context":
            model = payload["model"]
        elif payload.get("type") == "token_count":
            totals = payload["info"]["total_token_usage"]
            observed = event["timestamp"]
    ingest_usage_into_run_record(
        record,
        {
            "input_tokens": totals["input_tokens"],
            "output_tokens": totals["output_tokens"],
            "cache_tokens": totals["cached_input_tokens"],
            "duration_ms": int(
                (datetime.fromisoformat(observed) - started).total_seconds() * 1000
            ),
            "provider": "codex",
            "model": model,
            "source_snapshot_id": f"codex:{session_id}:{observed}",
            "measurement_kind": "absolute",
        },
        previous_snapshot=session_usage_baseline(index_path, session_id),
        index_record=index_record,
    )


def test_refresh_state_upgrades_legacy_model_binding_without_rebooking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, project, runtime_root = _goal_fixture(tmp_path)
    rollout = _fixture_rollout(tmp_path)
    with rollout.open("a") as handle:
        handle.write(_rollout_model_context("gpt-fixture-2") + "\n")
    kwargs = dict(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        usage_codex_session=rollout,
    )
    with monkeypatch.context() as legacy:
        legacy.setattr(
            state_refresh_module, "book_codex_session_usage", _legacy_book_codex_usage
        )
        _refresh(**kwargs)
    index = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    original_bytes = index.read_bytes()
    original = _index_runs(runtime_root)[0]
    original_record = Path(original["json_path"]).read_bytes()
    assert original["usage"]["model"] == "gpt-fixture-2"
    assert "codex_usage_binding" not in original
    before = _summary_totals(runtime_root)

    for _ in range(2):
        payload = _refresh(**kwargs)
        assert payload["usage"]["model"] == MODEL
        assert (
            payload["usage"]["source_snapshot_id"]
            == original["usage"]["source_snapshot_id"]
        )
        assert payload["usage"]["measurement_kind"] == "delta"
        for field in ("input_tokens", "output_tokens", "cache_tokens", "duration_ms"):
            assert payload["usage"][field] == 0
        assert "cost_usd" not in payload["usage"]
    rows = _index_runs(runtime_root)
    assert index.read_bytes().startswith(original_bytes)
    assert Path(original["json_path"]).read_bytes() == original_record
    assert rows[1]["codex_usage_binding"]["legacy_model"] == "gpt-fixture-2"
    assert rows[1]["codex_usage_binding"]["model"] == MODEL
    assert "legacy_model" not in rows[2]["codex_usage_binding"]
    assert (
        json.loads(Path(rows[1]["json_path"]).read_text())["codex_usage_binding"]
        == rows[1]["codex_usage_binding"]
    )
    after = _summary_totals(runtime_root)
    for window in ("24h", "7d"):
        for metric in (
            "input_tokens",
            "output_tokens",
            "cache_tokens",
            "duration_ms",
            "cost_usd",
        ):
            key = f"{metric}_{window}"
            assert after.get(key) == before.get(key)

    with rollout.open("a") as handle:
        handle.write(
            _rollout_event(
                "2026-08-26T01:10:00.000Z",
                input_tokens=2000,
                cached_input_tokens=350,
                output_tokens=450,
            )
            + "\n"
        )
    increment = _refresh(**kwargs)["usage"]
    assert (
        increment["input_tokens"],
        increment["output_tokens"],
        increment["cache_tokens"],
    ) == (800, 150, 150)
    assert increment["model"] == "gpt-fixture-2"
    assert _refresh(**kwargs)["usage"]["input_tokens"] == 0
    assert _summary_totals(runtime_root)["input_tokens_7d"] == 2000


@pytest.mark.parametrize(
    "conflict",
    [
        "model",
        "input_tokens",
        "output_tokens",
        "cache_tokens",
        "duration_ms",
        "cost_usd",
        "provider",
        "modern_binding",
    ],
)
def test_legacy_binding_reconciliation_rejects_other_conflicts(
    tmp_path: Path,
    conflict: str,
) -> None:
    rollout = _fixture_rollout(tmp_path)
    with rollout.open("a") as handle:
        handle.write(_rollout_model_context("gpt-fixture-2") + "\n")
    index = tmp_path / "index.jsonl"
    record: dict[str, Any] = {"generated_at": "2026-08-26T01:07:00.000Z"}
    _legacy_book_codex_usage(record, rollout, index)
    if conflict == "modern_binding":
        record["codex_usage_binding"] = {"schema_version": "codex_usage_binding_v1"}
    elif conflict in ("model", "provider"):
        record["usage"][conflict] = "unrelated"
    elif conflict == "cache_tokens":
        record["usage"].pop(conflict)
    else:
        record["usage"][conflict] = record["usage"].get(conflict, 0) + 1
    index.write_text(json.dumps(record) + "\n")
    before = index.read_bytes()
    target: dict[str, Any] = {}
    target_index: dict[str, Any] = {}
    with pytest.raises(UsageRowError):
        book_codex_session_usage(target, rollout, index, index_record=target_index)
    assert target == target_index == {}
    assert index.read_bytes() == before


@pytest.mark.parametrize(
    "evidence",
    [
        "missing_booking",
        "invalid_booking",
        "naive_booking",
        "late_context",
        "missing_context_time",
        "nonmonotonic",
        "different_final_model",
        "no_trailing_context",
    ],
)
def test_legacy_binding_requires_historical_context_evidence(
    tmp_path: Path, evidence: str
) -> None:
    rollout = _fixture_rollout(tmp_path)
    with rollout.open("a") as handle:
        handle.write(_rollout_model_context("gpt-fixture-2") + "\n")
    index = tmp_path / "index.jsonl"
    record: dict[str, Any] = {"generated_at": "2026-08-26T01:07:00.000Z"}
    _legacy_book_codex_usage(record, rollout, index)
    events = list(map(json.loads, rollout.read_text().splitlines()))
    if evidence == "missing_booking":
        record.pop("generated_at")
    elif evidence == "invalid_booking":
        record["generated_at"] = "not-a-timestamp"
    elif evidence == "naive_booking":
        record["generated_at"] = "2026-08-26T01:07:00"
    elif evidence == "late_context":
        record["generated_at"] = "2026-08-26T01:05:30.000Z"
    elif evidence == "missing_context_time":
        events[-1].pop("timestamp")
    elif evidence == "nonmonotonic":
        events[-1]["timestamp"] = "2026-08-26T01:04:00.000Z"
    elif evidence == "different_final_model":
        events.append(json.loads(_rollout_model_context("gpt-fixture-3")))
        events[-1]["timestamp"] = "2026-08-26T01:06:30.000Z"
    else:
        events.pop()
    _write_rollout(rollout, list(map(json.dumps, events)))
    index.write_text(json.dumps(record) + "\n")
    with pytest.raises(UsageRowError, match="model"):
        book_codex_session_usage({}, rollout, index)


def test_legacy_binding_uses_first_booking_not_later_replay_time(
    tmp_path: Path,
) -> None:
    rollout = _fixture_rollout(tmp_path)
    with rollout.open("a") as handle:
        handle.write(_rollout_model_context("gpt-fixture-2") + "\n")
    index = tmp_path / "index.jsonl"
    first: dict[str, Any] = {"generated_at": "2026-08-26T01:05:30.000Z"}
    _legacy_book_codex_usage(first, rollout, index)
    index.write_text(json.dumps(first) + "\n")
    replay: dict[str, Any] = {"generated_at": "2026-08-26T01:07:00.000Z"}
    _legacy_book_codex_usage(replay, rollout, index)
    with index.open("a") as handle:
        handle.write(json.dumps(replay) + "\n")
    with pytest.raises(UsageRowError, match="model"):
        book_codex_session_usage({}, rollout, index)


def test_legacy_binding_uses_selected_snapshot_booking_bound(tmp_path: Path) -> None:
    rollout = _fixture_rollout(tmp_path)
    index = tmp_path / "index.jsonl"
    first: dict[str, Any] = {"generated_at": "2026-08-26T01:05:30.000Z"}
    _legacy_book_codex_usage(first, rollout, index)
    index.write_text(json.dumps(first) + "\n")
    with rollout.open("a") as handle:
        handle.write(
            _rollout_event(
                "2026-08-26T01:06:00.000Z",
                input_tokens=2000,
                cached_input_tokens=350,
                output_tokens=450,
            )
            + "\n"
        )
        context = json.loads(_rollout_model_context("gpt-fixture-2"))
        context["timestamp"] = "2026-08-26T01:06:30.000Z"
        handle.write(json.dumps(context) + "\n")
    second: dict[str, Any] = {"generated_at": "2026-08-26T01:07:00.000Z"}
    _legacy_book_codex_usage(second, rollout, index)
    with index.open("a") as handle:
        handle.write(json.dumps(second) + "\n")
    with rollout.open("a") as handle:
        context = json.loads(_rollout_model_context("gpt-fixture-3"))
        context["timestamp"] = "2026-08-26T01:08:00.000Z"
        handle.write(json.dumps(context) + "\n")
    corrected: dict[str, Any] = {}
    book_codex_session_usage(corrected, rollout, index)
    assert corrected["usage"]["input_tokens"] == 0
    assert corrected["usage"]["model"] == MODEL
    assert (
        corrected["usage"]["source_snapshot_id"]
        == second["usage"]["source_snapshot_id"]
    )
    assert corrected["codex_usage_binding"]["legacy_model"] == "gpt-fixture-2"
