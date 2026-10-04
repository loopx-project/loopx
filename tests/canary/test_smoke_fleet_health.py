from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from loopx.canary.smoke_health import build_smoke_fleet_health
from loopx.cli import main as cli_main


def _passing_receipt(scripts: list[str]) -> dict[str, object]:
    return {
        "schema_version": "canary_smoke_suite_run_v0",
        "suite": "full-public",
        "timeout_seconds": 120.0,
        "failure_count": 0,
        "timeout_count": 0,
        "selected_checks": [
            {
                "normalized": {"script": script},
                "status": "passed",
                "ok": True,
                "duration_seconds": float((index % 7) + 1),
            }
            for index, script in enumerate(scripts)
        ],
    }


def test_static_health_is_compact_and_classifies_cadence() -> None:
    payload = build_smoke_fleet_health()

    assert payload["ok"] is True
    assert payload["ready"] is False
    assert payload["inventory_count"] > 0
    assert payload["cadence_counts"]["daily_full_public"] == payload["inventory_count"]
    assert payload["cadence_counts"]["pr_fast"] == 1
    assert payload["cadence_counts"]["catalog_canary"] > 0
    assert payload["cadence_counts"]["release_gate"] > 0
    assert payload["targeted_owner_count"] > 0
    assert payload["owner_gap_count"] > 0
    assert payload["workflow_contract"]["missing_scripts"] == []
    assert payload["contract_reuse"]["semantic_duplicate_inference"] == "manual_review_required"
    assert "inventory" not in payload
    assert len(json.dumps(payload, ensure_ascii=False)) < 30_000


def test_receipts_prove_complete_health_without_copying_raw_output(tmp_path: Path) -> None:
    inventory_payload = build_smoke_fleet_health(include_inventory=True)
    scripts = [entry["script"] for entry in inventory_payload["inventory"]]
    receipt = _passing_receipt(scripts)
    receipt["selected_checks"][0]["stdout_tail"] = "private-looking raw output"
    receipt["selected_checks"][0]["stderr_tail"] = "/tmp/local-path"
    receipt_path = tmp_path / "full-public-shard.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    payload = build_smoke_fleet_health(receipt_paths=[receipt_path])

    assert payload["ok"] is True
    assert payload["ready"] is True
    assert payload["receipt_health"]["observed_script_count"] == len(scripts)
    assert payload["receipt_health"]["missing_script_count"] == 0
    assert payload["receipt_health"]["failure_count"] == 0
    rendered = json.dumps(payload, ensure_ascii=False)
    assert "private-looking raw output" not in rendered
    assert "/tmp/local-path" not in rendered
    assert str(tmp_path) not in rendered


def test_failed_and_invalid_receipts_remain_distinct(tmp_path: Path) -> None:
    inventory_payload = build_smoke_fleet_health(include_inventory=True)
    scripts = [entry["script"] for entry in inventory_payload["inventory"]]
    receipt = _passing_receipt(scripts)
    receipt["failure_count"] = 1
    receipt["selected_checks"][0].update({"status": "failed", "ok": False})
    failed_path = tmp_path / "failed.json"
    failed_path.write_text(json.dumps(receipt), encoding="utf-8")

    failed = build_smoke_fleet_health(receipt_paths=[failed_path])
    assert failed["ok"] is True
    assert failed["ready"] is False
    assert failed["receipt_health"]["failure_count"] == 1

    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{}", encoding="utf-8")
    invalid = build_smoke_fleet_health(receipt_paths=[invalid_path])
    assert invalid["ok"] is False
    assert invalid["ready"] is False
    assert invalid["warnings"][0]["kind"] == "unsupported_receipt"


def _run_smoke_health_cli(receipt_dir: Path) -> tuple[int, dict[str, object]]:
    # Mirrors the full-public workflow step: global --format json, then the
    # shard directory as one --receipt argument.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            ["--format", "json", "canary", "smoke-health", "--receipt", str(receipt_dir)]
        )
    return exit_code, json.loads(output.getvalue())


@pytest.mark.parametrize("failing_shard", [False, True], ids=["all_pass", "one_failure"])
def test_cli_merges_a_shard_directory_into_the_workflow_ready_gate(
    tmp_path: Path, failing_shard: bool
) -> None:
    scripts = [
        entry["script"]
        for entry in build_smoke_fleet_health(include_inventory=True)["inventory"]
    ]
    half = len(scripts) // 2
    receipt_dir = tmp_path / "smoke-results"
    receipt_dir.mkdir()
    first = _passing_receipt(scripts[:half])
    second = _passing_receipt(scripts[half:])
    if failing_shard:
        second["failure_count"] = 1
        second["selected_checks"][0].update({"status": "failed", "ok": False})
    (receipt_dir / "shard-0.json").write_text(json.dumps(first), encoding="utf-8")
    (receipt_dir / "shard-1.json").write_text(json.dumps(second), encoding="utf-8")

    exit_code, payload = _run_smoke_health_cli(receipt_dir)

    assert exit_code == 0
    receipt_health = payload["receipt_health"]
    assert receipt_health["accepted_receipt_count"] == 2
    assert receipt_health["observed_script_count"] == len(scripts)
    assert receipt_health["missing_script_count"] == 0
    assert payload["ready"] is (not failing_shard)
    assert "inventory" not in payload
    assert str(tmp_path) not in json.dumps(payload, ensure_ascii=False)
