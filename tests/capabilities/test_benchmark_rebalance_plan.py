from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.benchmark_toolkit import build_benchmark_rebalance_plan


REPO_ROOT = Path(__file__).resolve().parents[2]


def _candidate(
    candidate_id: str,
    run_ids: list[str],
    *,
    priority: int,
    state: str = "active",
    admitted_at: str = "2026-10-24T00:00:00Z",
) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "run_ids": run_ids,
        "reclaim_priority": priority,
        "state": state,
        "admitted_at": admitted_at,
    }


def test_lower_priority_number_is_reclaimed_before_protected_cohort() -> None:
    plan = build_benchmark_rebalance_plan(
        [
            _candidate(
                "primary-pair",
                ["native-goal", "codex"],
                priority=20,
                admitted_at="2026-10-24T00:03:00Z",
            ),
            _candidate(
                "supplementary-newest",
                ["external-newest"],
                priority=0,
                admitted_at="2026-10-24T00:02:00Z",
            ),
            _candidate(
                "supplementary-oldest",
                ["external-oldest"],
                priority=0,
                admitted_at="2026-10-24T00:01:00Z",
            ),
        ]
    )

    assert plan["action"] == "stop_candidate"
    assert plan["selected_candidate"]["candidate_id"] == "supplementary-newest"
    assert plan["selected_candidate"]["run_ids"] == ["external-newest"]
    assert plan["reason_codes"] == ["lowest_reclaim_priority_newest_first"]


def test_pending_lower_priority_closeout_blocks_higher_priority_reclaim() -> None:
    plan = build_benchmark_rebalance_plan(
        [
            _candidate("supplementary-stopping", ["external"], priority=0,
                       state="stop_requested"),
            _candidate("primary-pair", ["native-goal", "codex"], priority=20),
        ]
    )

    assert plan["action"] == "await_candidate_closeout"
    assert plan["selected_candidate"] is None
    assert plan["blocking_candidate_ids"] == ["supplementary-stopping"]
    assert plan["reason_codes"] == ["lower_reclaim_priority_closeout_pending"]


def test_another_active_candidate_at_same_priority_can_be_selected() -> None:
    plan = build_benchmark_rebalance_plan(
        [
            _candidate("supplementary-stopping", ["external-a"], priority=0,
                       state="stop_requested"),
            _candidate("supplementary-active", ["external-b"], priority=0,
                       admitted_at="2026-10-24T00:01:00Z"),
            _candidate("primary-pair", ["native-goal", "codex"], priority=20),
        ]
    )

    assert plan["action"] == "stop_candidate"
    assert plan["selected_candidate"]["candidate_id"] == "supplementary-active"


def test_candidate_run_membership_must_not_overlap() -> None:
    with pytest.raises(ValueError, match="cannot overlap"):
        build_benchmark_rebalance_plan(
            [
                _candidate("first", ["same-run"], priority=0),
                _candidate("second", ["same-run"], priority=10),
            ]
        )


def test_cli_returns_public_safe_plan_without_mutating_inventory(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    payload = {
        "schema_version": "benchmark_rebalance_inventory_v0",
        "candidates": [
            _candidate("supplementary", ["external"], priority=0),
            _candidate("primary", ["native", "codex"], priority=20),
        ],
    }
    inventory.write_text(json.dumps(payload), encoding="utf-8")
    before = inventory.read_text(encoding="utf-8")

    completed = subprocess.run(
        [
            str(REPO_ROOT / "scripts/loopx"),
            "benchmark",
            "rebalance-plan",
            "--inventory-json",
            str(inventory),
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(completed.stdout)

    assert result["action"] == "stop_candidate"
    assert result["selected_candidate"]["run_ids"] == ["external"]
    assert result["write_performed"] is False
    assert result["process_signal_sent"] is False
    assert str(inventory) not in completed.stdout
    assert inventory.read_text(encoding="utf-8") == before
