"""Exact Goal identity across the real CLI, DSH producer and legacy files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.capabilities.reliability_diagnostics import ledger_path, run_dsh_fixture

ROOT = Path(__file__).resolve().parents[2]
GOALS = ("goal:a", "goal_a", "Goal-A", "goal-a")
PRODUCER = """
import {pathToFileURL} from 'node:url';
const {ShadowObserver} = await import(pathToFileURL(process.argv[1]));
const results = [];
for (const goalId of JSON.parse(process.argv[3])) {
  const warnings = [];
  const config = {
    goalId, sessionId: 'session-fixture', ledgerDir: process.argv[2], bufferBound: 4,
    runIdentity: Object.fromEntries(['worker_id', 'model_id', 'task_id', 'environment_id',
      'tools_id', 'budget_id', 'adapter_revision', 'observer_revision'].map(k => [k, 'fixture'])),
  };
  const observer = new ShadowObserver({config, observerId: 'observer-fixture',
    now: () => 1788256800000, warn: message => warnings.push(message)});
  observer.observeSessionCreated({id: config.sessionId});
  await observer.dispose();
  results.push({path: observer.path, stats: observer.stats(), warnings});
}
console.log(JSON.stringify(results));
"""


def records(goal):
    return [{**row, "goal_id": goal} for row in run_dsh_fixture()["ledger_records"]]


def cli(runtime, goal, command="receipt", *, rows=None, check=True):
    args = [sys.executable, "-m", "loopx.cli", "--registry", str(runtime / "registry.json"),
            "--runtime-root", str(runtime), "--format", "json", "reliability-diagnostics",
            command, "--goal-id", goal]
    if rows is not None:
        args.extend(["--input", "-"])
    result = subprocess.run(
        args, input="\n".join(json.dumps(row) for row in rows) if rows is not None else None,
        cwd=ROOT, env={**os.environ, "LOOPX_USAGE_PING": "0"}, capture_output=True,
        text=True, encoding="utf-8", timeout=30,
    )
    if not check:
        return result
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def producer(runtime, goals):
    result = subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
         PRODUCER, str(ROOT / "packages/dsh-loopx-plugin/src/observer.ts"),
         str(runtime / "reliability_diagnostics"), json.dumps(goals)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def legacy_file(runtime, goal, *, rows=None, raw=None):
    path = runtime / "reliability_diagnostics" / (goal.replace(":", "_") + ".ndjson")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw if raw is not None else "\n".join(
        json.dumps(row) for row in (records(goal) if rows is None else rows)
    ) + "\n", encoding="utf-8")
    return path


def test_real_cli_keeps_aliases_and_case_distinct(tmp_path):
    refs = []
    for goal in GOALS:
        refs.append(cli(tmp_path, goal, "ingest", rows=records(goal))["ledger_ref"])
    assert len({ref.casefold() for ref in refs}) == len(GOALS)
    for goal in GOALS:
        receipt = cli(tmp_path, goal)["receipt"]
        assert receipt["status"] == "degraded"
        assert receipt["ledger_invalid_record_count"] == 0
    assert len(list(tmp_path.rglob("*.ndjson"))) == len(GOALS)


def test_real_dsh_writes_match_cli_paths_and_readback(tmp_path):
    produced = producer(tmp_path, GOALS)
    assert len({row["path"].casefold() for row in produced}) == len(GOALS)
    for goal, row in zip(GOALS, produced, strict=True):
        assert Path(row["path"]) == ledger_path(tmp_path, goal)
        assert row["stats"]["observer_failure_count"] == 0
        receipt = cli(tmp_path, goal)["receipt"]
        assert receipt["status"] == "valid"
        assert receipt["persisted_event_count"] == 1
        assert receipt["ledger_invalid_record_count"] == 0


@pytest.mark.parametrize("state", ["single", "mixed", "malformed"])
def test_legacy_readback_is_read_only_and_never_hides_invalid_evidence(tmp_path, state):
    goal = "goal:a"
    rows = records(goal) + (records("goal_a") if state == "mixed" else [])
    path = legacy_file(tmp_path, goal, rows=rows, raw="not-json\n" if state == "malformed" else None)
    before = path.read_bytes()
    result = cli(tmp_path, goal)
    assert result["ledger_ref"] == path.relative_to(tmp_path).as_posix()
    assert result["receipt"]["status"] == ("degraded" if state == "single" else "invalid")
    assert path.read_bytes() == before
    assert list(tmp_path.rglob("*.ndjson")) == [path]


def test_legacy_blocks_both_writers_without_changing_bytes(tmp_path):
    goal = "goal:a"
    legacy = legacy_file(tmp_path, goal)
    before = legacy.read_bytes()
    result = cli(tmp_path, goal, "ingest", rows=records(goal), check=False)
    assert result.returncode == 2
    assert "legacy diagnostic ledger" in result.stderr.lower()
    assert "offline" in result.stderr.lower()
    [row] = producer(tmp_path, [goal])
    assert row["stats"]["observer_failure_count"] == 1
    assert row["warnings"]
    assert legacy.read_bytes() == before
    assert not ledger_path(tmp_path, goal).exists()


def test_two_layouts_require_reconciliation_instead_of_hiding_history(tmp_path):
    goal = "goal:a"
    cli(tmp_path, goal, "ingest", rows=records(goal))
    legacy = legacy_file(tmp_path, goal)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*.ndjson")}
    result = cli(tmp_path, goal, check=False)
    assert result.returncode == 2
    assert "legacy" in result.stderr.lower()
    assert "offline" in result.stderr.lower()
    assert legacy.exists()
    assert all(path.read_bytes() == content for path, content in before.items())


def test_offline_copy_preserves_receipt_and_allows_future_append(tmp_path):
    goal = "goal:a"
    legacy = legacy_file(tmp_path, goal)
    before = cli(tmp_path, goal)["receipt"]
    canonical = ledger_path(tmp_path, goal)
    assert canonical != legacy
    canonical.parent.mkdir(parents=True, exist_ok=True)
    with canonical.open("xb") as handle:
        handle.write(legacy.read_bytes())
    legacy.rename(tmp_path / "retained-original.ndjson")
    assert cli(tmp_path, goal)["receipt"] == before
    cli(tmp_path, "goal_a", "ingest", rows=records("goal_a"))
    assert cli(tmp_path, goal)["receipt"] == before
    assert cli(tmp_path, "goal_a")["receipt"]["ledger_invalid_record_count"] == 0
