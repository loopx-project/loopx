"""Operator run-history windows filter the full index before display limits."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def history_source(tmp_path: Path):
    runtime = tmp_path / "runtime"
    index = runtime / "goals" / "sample" / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    rows = [
        ("older", "2026-05-31T23:59:59Z", "worker"),
        ("start", "2026-06-01T08:00:00+08:00", "worker"),
        ("inside", "2026-06-01T12:00:00Z", "worker"),
        ("peer", "2026-06-01T13:00:00Z", "peer"),
        ("end", "2026-06-02T00:00:00Z", "worker"),
        ("newer", "2026-06-03T00:00:00Z", "worker"),
        ("undated", "unknown", "worker"),
    ]
    index.write_text(
        "".join(
            json.dumps(
                {
                    "run_id": name,
                    "goal_id": "sample",
                    "generated_at": stamp,
                    "agent_id": agent,
                    "classification": name,
                }
            )
            + "\n"
            for name, stamp, agent in rows
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime),
                "goals": [],
                "projects": [],
            }
        ),
        encoding="utf-8",
    )
    return registry, index


def run_history(source, *args, markdown=False):
    registry, index = source
    original = index.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "markdown" if markdown else "json",
            "history",
            *args,
        ],
        text=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "LOOPX_USAGE_PING": "0"},
    )
    assert index.read_bytes() == original
    return result, result.stdout if markdown else json.loads(result.stdout)


def test_cli_window_precedes_limit_and_combines_with_agent(history_source):
    result, payload = run_history(
        history_source,
        "--goal-id",
        "sample",
        "--agent-id",
        "worker",
        "--since",
        "2026-06-01",
        "--until",
        "2026-06-02",
        "--limit",
        "1",
    )
    assert result.returncode == 0, result.stderr
    assert [run["classification"] for run in payload["runs"]] == ["inside"]
    assert payload["run_count"] == 2
    assert payload["goals"][0]["unique_runs"] == 2
    assert payload["time_filter"] == {
        "since_inclusive": "2026-06-01T00:00:00+00:00",
        "until_exclusive": "2026-06-02T00:00:00+00:00",
        "excluded_undated_runs": 1,
        "quota_scope": "all_goal_runs",
    }


def test_cli_boundaries_offsets_and_default_parity(history_source):
    _, payload = run_history(
        history_source,
        "--since",
        "2026-06-01T02:00:00+02:00",
        "--until",
        "2026-06-02T02:00:00+02:00",
    )
    assert [r["classification"] for r in payload["runs"]] == ["peer", "inside", "start"]
    _, unfiltered = run_history(history_source, "--limit", "20")
    assert unfiltered["run_count"] == 7
    assert "time_filter" not in unfiltered
    _, empty = run_history(history_source, "--since", "2027-01-01")
    assert empty["runs"] == [] and empty["run_count"] == 0
    assert empty["time_filter"]["excluded_undated_runs"] == 1


@pytest.mark.parametrize(
    "args",
    [
        ["--since", "not-a-date"],
        ["--until", ""],
        ["--since", "2026-06-02", "--until", "2026-06-01"],
        ["--since", "2026-06-01", "--until", "2026-06-01"],
        ["inspect-index-duplicates", "--since", "2026-06-01"],
        ["--evidence-ref", "sample", "--until", "2026-06-01"],
    ],
)
def test_cli_rejects_invalid_or_ambiguous_windows(history_source, args):
    result, payload = run_history(history_source, *args)
    assert result.returncode != 0
    assert payload["ok"] is False and payload["error"]


def test_markdown_discloses_window_and_undated_exclusion(history_source):
    result, output = run_history(history_source, "--until", "2026-06-02", markdown=True)
    assert result.returncode == 0
    assert "until (exclusive)" in output
    assert "excluded undated runs: 1" in output
    assert "Quota accounting remains Goal-wide" in output


def test_window_does_not_hide_goal_wide_quota_integrity(history_source):
    from loopx.control_plane.quota.ledger_readback import (
        QUOTA_SLOT_SPENT_CLASSIFICATION,
    )

    registry, index = history_source
    data = json.loads(registry.read_text())
    data["goals"] = [{"id": "sample", "repo": str(registry.parent)}]
    registry.write_text(json.dumps(data))
    # A recent spend without its required readback must stay an integrity
    # warning even when the operator is inspecting an older history window.
    with index.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "goal_id": "sample",
                    "agent_id": "peer",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "classification": QUOTA_SLOT_SPENT_CLASSIFICATION,
                }
            )
            + "\n"
        )
    result, full = run_history(history_source)
    assert result.returncode == 0, result.stderr
    quota = full["goals"][0]["quota"]
    assert quota["unreadable_event_count"] == 1
    result, filtered = run_history(
        history_source,
        "--goal-id",
        "sample",
        "--agent-id",
        "worker",
        "--since",
        "2026-06-01",
        "--until",
        "2026-06-02",
    )
    assert result.returncode == 0, result.stderr
    assert filtered["goals"][0]["quota"] == quota
