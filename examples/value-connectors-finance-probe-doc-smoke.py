#!/usr/bin/env python3
"""Prove the retired Finance connector returns extension migration guidance."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DOC = (
    ROOT
    / "loopx"
    / "capabilities"
    / "value_connectors"
    / "docs"
    / "finance-market-snapshot-probe.md"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def main() -> int:
    cases = (
        ("source-map", "--connector"),
        ("install-check", "--connector"),
        ("plan", "--connector-id"),
    )
    for command, selector in cases:
        completed = _run(
            "value-connectors",
            command,
            selector,
            "finance_market_snapshot",
        )
        assert completed.returncode == 0, completed.stderr
        packet = json.loads(completed.stdout)
        assert packet["ok"] is True, packet
        if command == "source-map":
            profiles = packet["source_profiles"]
        elif command == "install-check":
            profiles = packet["checks"]
        else:
            profiles = None
        if profiles is not None:
            assert len(profiles) == 1, profiles
            profile = profiles[0]
            assert profile["connector_id"] == "finance_market_snapshot", profile
            assert profile["status"] == "migrated_to_extension", profile
            migration = profile["migration"]
        else:
            migration = packet
        assert migration["schema_version"] == "value_connector_extension_migration_v0", migration
        assert migration["status"] == "migrated_to_extension", migration
        assert migration["replacement_extension_id"] == "loopx-finance-value-discovery", migration
        assert migration["replacement_capability_id"] is None, migration
        truth = migration["truth_contract"]
        assert truth["legacy_connector_executes_finance"] is False, truth
        assert truth["external_reads_performed"] is False, truth
        assert truth["external_writes_performed"] is False, truth

    doc = " ".join(DOC.read_text(encoding="utf-8").lower().split())
    for marker in (
        "value_connector_extension_migration_v0",
        "provider source required",
        "must not recreate the old connector",
    ):
        assert marker in doc, marker

    print("value-connectors-finance-probe-doc-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
