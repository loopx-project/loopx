from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.feedback import append_human_reward, select_run


def _run(generated_at: str | None, classification: str) -> dict[str, object]:
    return {
        "generated_at": generated_at,
        "classification": classification,
        "json_path": f"runs/{classification}.json",
        "markdown_path": f"runs/{classification}.md",
    }


def test_select_run_uses_utc_instant_for_mixed_offsets() -> None:
    runs = [
        _run("2026-08-31T01:00:00+08:00", "older-utc"),
        _run("2026-08-31T00:30:00Z", "newer-utc"),
    ]

    assert select_run(runs, None)["classification"] == "newer-utc"


def test_select_run_prefers_valid_timestamps_over_legacy_values() -> None:
    runs = [
        _run(None, "missing-time"),
        _run("not-a-timestamp", "malformed-time"),
        _run("2026-08-30T23:59:59Z", "valid-time"),
    ]

    assert select_run(runs, None)["classification"] == "valid-time"


def test_select_run_has_deterministic_equal_instant_tie_break() -> None:
    runs = [
        _run("2026-08-31T01:00:00+08:00", "offset-form"),
        _run("2026-08-30T17:00:00Z", "utc-form"),
    ]

    assert select_run(runs, None)["classification"] == "offset-form"


def test_append_human_reward_binds_default_overlay_to_latest_utc_run(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir()
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime),
                "goals": [{"id": "fixture-goal", "repo": str(project)}],
            }
        ),
        encoding="utf-8",
    )
    runs_dir = runtime / "goals" / "fixture-goal" / "runs"
    runs_dir.mkdir(parents=True)
    rows = [
        _run("2026-08-31T01:00:00+08:00", "older-utc"),
        _run("2026-08-31T00:30:00Z", "newer-utc"),
    ]
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    result = append_human_reward(
        registry_path=registry_path,
        runtime_root_override=None,
        goal_id="fixture-goal",
        run_generated_at=None,
        actor_kind="owner",
        reward={
            "recorded_at": "2026-08-31T02:00:00Z",
            "decision": "continue",
            "reward": "positive",
            "reason_summary": "fixture accepted",
        },
    )

    assert result["selected_run"]["classification"] == "newer-utc"
    assert result["index_record"]["classification"] == "newer-utc"
    assert result["human_reward"]["actor_kind"] == "owner"


@pytest.mark.parametrize(
    "run_generated_at",
    ["2026-08-31T01:00:00+08:00", "2026-08-31T00:30:00Z"],
)
def test_select_run_keeps_explicit_timestamp_matching_exact(
    run_generated_at: str,
) -> None:
    runs = [
        _run("2026-08-31T01:00:00+08:00", "older-utc"),
        _run("2026-08-31T00:30:00Z", "newer-utc"),
    ]

    assert select_run(runs, run_generated_at)["generated_at"] == run_generated_at
