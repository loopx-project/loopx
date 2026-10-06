#!/usr/bin/env python3
"""Validate the GitHub Actions full-public smoke workflow contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.canary.runner import build_canary_smoke_suite_run  # noqa: E402


WORKFLOW = REPO_ROOT / ".github" / "workflows" / "full-public-smokes.yml"
MATRIX_ENTRY_RE = re.compile(
    r"^\s+- shard: (?P<shard>\d+)\n\s+offset: (?P<offset>\d+)$",
    re.MULTILINE,
)
SHARD_LIMIT_RE = re.compile(r'^\s+SHARD_LIMIT: "(?P<limit>\d+)"$', re.MULTILINE)


def main() -> int:
    text = WORKFLOW.read_text(encoding="utf-8")

    for required in [
        "name: Full Public Smokes",
        "workflow_dispatch:",
        "schedule:",
        "permissions:",
        "contents: read",
        "fail-fast: false",
        "max-parallel: 2",
        "needs: chat-bundle",
        "python scripts/chat_bundle.py verify --source",
        "timeout-minutes: 120",
        "actions/setup-python@",
        "python-version: \"3.11\"",
        "actions/setup-node@v6",
        "node-version: \"24\"",
        "python3 examples/run-smokes.py",
        "--suite full-public",
        "--offset \"${{ matrix.offset }}\"",
        "--limit \"${SHARD_LIMIT}\"",
        "--timeout-seconds \"${SMOKE_TIMEOUT_SECONDS}\"",
        "SMOKE_JOBS: \"4\"",
        "--jobs \"${SMOKE_JOBS}\"",
        "--no-execute",
        "--json",
        'python -m pip install --disable-pip-version-check "jsonschema>=4.23,<5"',
        "actions/upload-artifact@",
        "$RUNNER_TEMP/loopx-smoke-results/full-public-shard-${{ matrix.shard }}.json",
        "${{ runner.temp }}/loopx-smoke-results/full-public-shard-${{ matrix.shard }}.json",
    ]:
        assert required in text, required

    assert "push:" not in text
    assert "pull_request:" not in text
    assert "contents: write" not in text
    assert "pull-requests: write" not in text
    assert "sec" + "rets." not in text
    assert "mkdir -p smoke-results" not in text

    producer = text.split("  chat-bundle:\n", 1)[1].split("  full-public-smokes:\n", 1)[0]
    consumers = text.split("  full-public-smokes:\n", 1)[1].split("  smoke-fleet-health:\n", 1)[0]
    assert text.count("chat_bundle.py build --install") == 1
    assert producer.index("chat_bundle.py verify --source") < producer.index("actions/upload-artifact")
    assert "if-no-files-found: error" in producer
    assert "chat_bundle.py build" not in consumers
    assert "npm --prefix apps/presentation/dashboard ci --ignore-scripts" in consumers
    assert "actions/download-artifact@" in consumers
    for job in (producer, consumers):
        assert "name: full-public-chat-${{ github.sha }}" in job
        assert "path: loopx/web/chat/" in job
        assert "chat_bundle.py verify --source" in job

    matrix = [
        (int(match.group("shard")), int(match.group("offset")))
        for match in MATRIX_ENTRY_RE.finditer(text)
    ]
    limit_match = SHARD_LIMIT_RE.search(text)
    assert limit_match is not None
    shard_limit = int(limit_match.group("limit"))
    assert matrix == [
        (shard, shard * shard_limit) for shard in range(len(matrix))
    ], matrix

    inventory = build_canary_smoke_suite_run(suite="full-public", execute=False)
    inventory_count = int(inventory["matched_check_count"])
    capacity = len(matrix) * shard_limit
    assert capacity >= inventory_count, (capacity, inventory_count)
    assert capacity - inventory_count < shard_limit, (capacity, inventory_count)

    print(
        "full-public-smokes-workflow-smoke ok "
        f"inventory={inventory_count} capacity={capacity}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
