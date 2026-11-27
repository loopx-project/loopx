from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import loopx.history as history_module
from loopx.history import collect_history


def _write_history_fixture(
    tmp_path: Path,
    runs_by_goal: dict[str, list[dict[str, Any]]],
) -> tuple[Path, Path]:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{}\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"

    for goal_id, runs in runs_by_goal.items():
        runs_dir = runtime_root / "goals" / goal_id / "runs"
        runs_dir.mkdir(parents=True)
        rows = []
        for position, run in enumerate(runs):
            row = dict(run)
            row.setdefault("json_path", f"artifacts/{goal_id}-{position}.json")
            row.setdefault("markdown_path", f"artifacts/{goal_id}-{position}.md")
            rows.append(json.dumps(row))
        (runs_dir / "index.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    return registry_path, runtime_root


def test_collect_history_orders_goal_runs_by_utc_instant(tmp_path: Path) -> None:
    registry_path, runtime_root = _write_history_fixture(
        tmp_path,
        {
            "goal-a": [
                {
                    "classification": "offset-earlier",
                    "generated_at": "2026-08-19T00:30:00+08:00",
                    "agent_id": "agent-a",
                    "agent_vision": {"agent_id": "agent-a", "revision": "earlier"},
                },
                {
                    "classification": "utc-later",
                    "generated_at": "2026-08-18T17:00:00Z",
                    "agent_id": "agent-a",
                    "agent_vision": {"agent_id": "agent-a", "revision": "later"},
                },
            ]
        },
    )

    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-a",
        limit=10,
    )

    goal = history["goals"][0]
    assert [run["classification"] for run in goal["latest_runs"]] == [
        "utc-later",
        "offset-earlier",
    ]
    assert goal["latest_status_run"]["classification"] == "utc-later"
    semantic_agent = goal["semantic_history"]["agents"][0]
    assert semantic_agent["latest_agent_vision_run"]["classification"] == "utc-later"


def test_collect_history_orders_runs_across_goals_by_utc_instant(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root = _write_history_fixture(
        tmp_path,
        {
            "goal-a": [
                {
                    "classification": "offset-earlier",
                    "generated_at": "2026-08-19T00:30:00+08:00",
                }
            ],
            "goal-b": [
                {
                    "classification": "utc-later",
                    "generated_at": "2026-08-18T17:00:00Z",
                }
            ],
        },
    )

    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=None,
        limit=1,
    )

    assert [run["classification"] for run in history["runs"]] == ["utc-later"]


def test_collect_history_does_not_resort_unbounded_global_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runs = [
        {
            "classification": f"run-{index}",
            "generated_at": f"2026-08-18T17:00:{index:02d}Z",
        }
        for index in range(50)
    ]
    registry_path, runtime_root = _write_history_fixture(
        tmp_path,
        {"goal-a": runs, "goal-b": runs},
    )
    chronology_key = history_module._chronology_key
    chronology_calls = 0

    def count_chronology(value: Any):
        nonlocal chronology_calls
        chronology_calls += 1
        return chronology_key(value)

    monkeypatch.setattr(history_module, "_chronology_key", count_chronology)
    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=None,
        limit=1,
    )

    assert history["run_count"] == 100
    assert len(history["runs"]) == 1
    assert chronology_calls < 150


def test_collect_history_filters_stopped_goals_before_reading_run_indexes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry_path, runtime_root = _write_history_fixture(
        tmp_path,
        {
            "goal-active": [{"classification": "active", "generated_at": "2026-08-18T17:00:00Z"}],
            "goal-legacy-runtime": [{"classification": "legacy", "generated_at": "2026-08-18T19:00:00Z"}],
            "goal-stopped": [{"classification": "stopped", "generated_at": "2026-08-18T18:00:00Z"}],
        },
    )
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {"id": "goal-active"},
                    {
                        "id": "goal-stopped",
                        "activation": {
                            "schema_version": "loopx_goal_activation_v1",
                            "state": "stopped",
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded_indexes: list[str] = []
    original_load_index_snapshot = history_module.load_index_snapshot

    def record_load_index_snapshot(path: Path, **kwargs: Any):
        loaded_indexes.append(path.parts[-3])
        return original_load_index_snapshot(path, **kwargs)

    monkeypatch.setattr(
        history_module,
        "load_index_snapshot",
        record_load_index_snapshot,
    )

    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=None,
        limit=10,
        activation_state_filter="active",
    )

    assert [goal["id"] for goal in history["goals"]] == ["goal-active"]
    assert loaded_indexes == ["goal-active"]

    loaded_indexes.clear()
    full_history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=None,
        limit=10,
    )
    assert {goal["id"] for goal in full_history["goals"]} == {
        "goal-active",
        "goal-legacy-runtime",
        "goal-stopped",
    }
    assert "goal-legacy-runtime" in loaded_indexes


def test_collect_history_keeps_invalid_legacy_timestamps_below_valid_runs(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root = _write_history_fixture(
        tmp_path,
        {
            "goal-a": [
                {
                    "classification": "malformed",
                    "generated_at": "not-a-time",
                },
                {
                    "classification": "overflow",
                    "generated_at": "0001-01-01T00:00:00+14:00",
                },
                {
                    "classification": "same-instant-z",
                    "generated_at": "2026-01-01T00:00:00Z",
                },
                {
                    "classification": "same-instant-offset",
                    "generated_at": "2026-01-01T08:00:00+08:00",
                },
                {
                    "classification": "missing",
                    "generated_at": None,
                },
            ]
        },
    )

    history = collect_history(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id="goal-a",
        limit=10,
    )

    classifications = [
        run["classification"] for run in history["goals"][0]["latest_runs"]
    ]
    assert classifications == [
        "same-instant-offset",
        "same-instant-z",
        "malformed",
        "overflow",
        "missing",
    ]


def test_load_index_reuses_artifact_existence_checks_for_duplicate_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    index_path = tmp_path / "index.jsonl"
    row = {
        "generated_at": "2026-01-01T00:00:00Z",
        "json_path": "artifacts/run.json",
        "markdown_path": "artifacts/run.md",
    }
    index_path.write_text(
        "".join(json.dumps(row) + "\n" for _ in range(100)),
        encoding="utf-8",
    )
    calls: list[tuple[Any, Path | None]] = []
    original = history_module._indexed_artifact_exists

    def count_exists(
        value: Any,
        *,
        artifact_root: Path | None,
        runs_dir: Path | None = None,
        goal_id: str | None = None,
        extension: str | None = None,
    ) -> bool:
        calls.append((value, artifact_root))
        return original(
            value,
            artifact_root=artifact_root,
            runs_dir=runs_dir,
            goal_id=goal_id,
            extension=extension,
        )

    monkeypatch.setattr(history_module, "_indexed_artifact_exists", count_exists)

    records, raw_count = history_module.load_index(
        index_path,
        artifact_root=tmp_path,
    )

    assert raw_count == 100
    assert len(records) == 1
    assert calls == [
        ("artifacts/run.json", tmp_path),
        ("artifacts/run.md", tmp_path),
    ]
    assert records[0]["json_exists"] is False
    assert records[0]["markdown_exists"] is False


def test_load_index_snapshot_rejects_comma_multi_object_and_preserves_u2028_and_time_parity(
    tmp_path: Path,
) -> None:
    from datetime import datetime, timezone
    from loopx.control_plane.runtime.time import chronology_key, parse_timestamp

    index_path = tmp_path / "index.jsonl"
    # A valid row with literal UTF-8 U+2028 inside JSON string, followed by a
    # single malformed line with comma-joined JSON objects (must count invalid=1).
    u2028_row = json.dumps(
        {
            "generated_at": "2026-11-27T23:23:55.123456Z",
            "json_path": "artifacts/u2028.json",
            "markdown_path": "artifacts/u2028.md",
            "note": "line\u2028break",
        },
        ensure_ascii=False,
    )
    index_path.write_text(
        f"{u2028_row}\n"
        + '{"generated_at":"2026-11-27T23:24:00Z"},{"generated_at":"2026-11-27T23:25:00Z"}\n',
        encoding="utf-8",
    )
    snap = history_module.load_index_snapshot(index_path, include_artifact_status=False)
    assert snap.raw_count == 2
    assert snap.invalid_count == 1
    assert len(snap.records) == 1
    assert snap.records[0]["note"] == "line\u2028break"

    assert parse_timestamp("2026-11-27T23:23:55Z") == datetime(
        2026, 11, 27, 23, 23, 55, tzinfo=timezone.utc
    )
    assert parse_timestamp("2026-11-27T23:23:55.123456Z") == datetime(
        2026, 11, 27, 23, 23, 55, 123456, tzinfo=timezone.utc
    )
    assert parse_timestamp("2026-11-27T24:00:00Z") is None
    assert chronology_key("2026-11-27T23:23:55.000000Z")[0] == 1
