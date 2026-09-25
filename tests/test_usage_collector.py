"""Run the usage collector's node:test suite from the Python test lane."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTOR_TEST = REPO_ROOT / "apps" / "usage-collector" / "test" / "collector.test.mjs"


def _node_with_sqlite() -> str | None:
    node = shutil.which("node")
    if node is None:
        return None
    probe = subprocess.run([node, "-e", "require('node:sqlite')"], capture_output=True, text=True)
    return node if probe.returncode == 0 else None


def test_usage_collector_node_suite_passes():
    node = _node_with_sqlite()
    if node is None:
        pytest.skip("node with node:sqlite is required for the collector suite")
    result = subprocess.run(
        [node, "--no-warnings", "--test", str(COLLECTOR_TEST)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
