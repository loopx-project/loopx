"""History retains control evidence without probing every discarded artifact."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

import loopx.history as history_module
from loopx.history import collect_history, load_index_snapshot


@pytest.fixture
def history_case(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    registry.write_text("{}\n")
    runtime = tmp_path / "runtime"
    index = runtime / "goals" / "example" / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    now = start + timedelta(seconds=1001)
    monkeypatch.setattr(
        "loopx.control_plane.runtime.run_context_retention.now_utc_iso",
        lambda: now.isoformat(),
    )
    rows = []
    for n in range(1000):
        rows.append({
            "generated_at": (start + timedelta(seconds=n)).isoformat(),
            "run_id": f"run-{n}", "classification": "progress",
            "agent_id": "worker", "json_path": f"artifacts/{n}.json",
            "markdown_path": f"artifacts/{n}.md",
            # Persisted booleans are deliberately wrong: each read observes files.
            "json_exists": True, "markdown_exists": True,
        })
    rows[0]["agent_vision"] = {"agent_id": "worker", "revision": "retained"}
    rows[1]["human_reward"] = {"decision": "revise", "reason_summary": "Keep evidence"}
    rows[990].update({
        "todo_id": "todo_blocked_work", "turn_instance_id": "turn-1",
        "delivery_outcome": "outcome_gap",
        "progress_observation": {"result_class": "blocked"},
        "blocked_retry": {
            "schema_version": "quota_blocked_retry_v0", "source": "turn_settlement",
            "todo_id": "todo_blocked_work", "observed_at": rows[990]["generated_at"],
            "due_at": (start + timedelta(seconds=1290)).isoformat(),
            "resume_when": f"resume_at:{(start + timedelta(seconds=1290)).isoformat()}",
        },
    })
    rows[995]["agent_id"] = "quiet-worker"
    # Recent neutral records must not hide the latest status-relevant run.
    rows[999]["classification"] = "quota_slot_spent"
    rows[998]["classification"] = "quota_slot_spent"
    # Last duplicate replaces only supplied fields, without another unique Run.
    duplicate = {**rows[0], "annotation": "merged duplicate"}
    index.write_text("\n".join(json.dumps(row) for row in [*rows, duplicate]) + "\nnot-json\n[]\n\n")
    for n in (0, 1, 990, 995, 997, 999):
        (artifacts / f"{n}.json").write_text("{}\n")
    return registry, runtime, index, artifacts


def _collect(case, *, limit=1, agent_lane_id=None):
    registry, runtime, _, _ = case
    return collect_history(registry_path=registry, runtime_root=runtime,
                           goal_id="example", limit=limit, agent_lane_id=agent_lane_id)


@pytest.mark.parametrize("limit", [0, 1, 3])
def test_history_observes_retained_evidence_after_full_reduction(history_case, monkeypatch, limit):
    _, _, index, _ = history_case
    calls = []
    original = history_module._indexed_artifact_exists

    def observe(value, *, artifact_root):
        calls.append(value)
        return original(value, artifact_root=artifact_root)

    monkeypatch.setattr(history_module, "_indexed_artifact_exists", observe)
    result = _collect(history_case, limit=limit)
    goal = result["goals"][0]
    assert result["run_count"] == goal["unique_runs"] == 1000
    assert goal["raw_index_records"] == 1003
    assert goal["index_digest"] == f"sha256:{hashlib.sha256(index.read_bytes()).hexdigest()}"
    assert len(result["runs"]) == len(goal["latest_runs"]) == limit
    semantic = goal["semantic_history"]
    vision = semantic["agents"][0]["latest_agent_vision_run"]
    correction = semantic["latest_owner_correction_run"]
    retry = semantic["active_blocked_retry_runs"][0]
    assert vision["run_id"] == "run-0"
    assert vision["annotation"] == "merged duplicate"
    assert correction["run_id"] == "run-1"
    assert retry["run_id"] == "run-990"
    assert goal["latest_status_run"]["run_id"] == "run-997"
    for row in [vision, correction, retry, goal["latest_status_run"]]:
        assert row["json_exists"] is True
        assert row["markdown_exists"] is False
    # Counts depend on retained output, never the 1,000-row historical population.
    expected = {0, 1, 990, 997, *range(1000 - limit, 1000)}
    assert set(calls) == {f"artifacts/{n}.{ext}" for n in expected for ext in ("json", "md")}
    assert len(calls) == len(set(calls))


def test_lane_window_artifacts_remain_observed(history_case):
    result = _collect(history_case, agent_lane_id="quiet-worker")
    runs = result["goals"][0]["latest_runs"]
    assert [run["run_id"] for run in runs] == ["run-999", "run-995"]
    assert all(run["json_exists"] and not run["markdown_exists"] for run in runs)


def test_unchanged_index_does_not_cache_artifact_existence(history_case):
    _, _, index, artifacts = history_case
    before = index.read_bytes()
    initial = _collect(history_case)
    assert initial["runs"][0]["json_exists"] is True
    assert initial["runs"][0]["markdown_exists"] is False
    (artifacts / "999.json").unlink()
    (artifacts / "999.md").write_text("# Delivered\n")
    (artifacts / "0.json").unlink()
    reread = _collect(history_case)
    assert reread["runs"][0]["json_exists"] is False
    assert reread["runs"][0]["markdown_exists"] is True
    assert reread["goals"][0]["semantic_history"]["agents"][0]["latest_agent_vision_run"]["json_exists"] is False
    assert index.read_bytes() == before
    assert reread["goals"][0]["index_digest"] == initial["goals"][0]["index_digest"]


def test_full_index_reader_still_observes_every_row(history_case):
    registry, _, index, artifacts = history_case
    (artifacts / "500.md").write_text("# Older artifact\n")
    (artifacts / "501.json").symlink_to(artifacts / "absent.json")
    snapshot = load_index_snapshot(index, artifact_root=registry.parent)
    assert len(snapshot.records) == 1000
    assert snapshot.records[500]["markdown_exists"] is True
    assert snapshot.records[501]["json_exists"] is False
    assert snapshot.records[502]["json_exists"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_history_and_status_keep_artifacts_with_canonical_todos(history_case, monkeypatch, tmp_path, provider):
    from tests.control_plane.canonical_authority_fixture import (
        initialize_canonical_authority, isolate_sqlite_runtime,
    )
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.effect_runtime import restart_effect_runtime
    from loopx.control_plane.testing.canary_harness import write_fixture_registry

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _, _ = history_case
    state = tmp_path / "state.md"
    state.write_text("---\nstatus: active\n---\n# Example\n\n## Agent Todo\n")
    write_fixture_registry(project=tmp_path, registry_path=registry, runtime_root=runtime,
                           goal_id="example", domain="engineering", state_file=str(state),
                           adapter_kind="generic_project_goal_v0")
    initialize_canonical_authority(
        runtime, "example", build_todo_runtime_shadow_projection(goal_id="example", todos=[]),
        state_path=state, provider=provider,
    )
    try:
        for command in ("history", "status"):
            result = subprocess.run(
                [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
                 "--runtime-root", str(runtime), "--format", "json", command,
                 "--goal-id", "example", "--limit", "1"],
                capture_output=True, text=True, timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            payload = json.loads(result.stdout)
            history = payload if command == "history" else payload["run_history"]
            goal = history["goals"][0]
            assert goal["unique_runs"] == 1000
            assert goal["latest_status_run"]["run_id"] == "run-997"
            assert goal["latest_status_run"]["json_exists"] is True
            assert goal["latest_status_run"]["markdown_exists"] is False
    finally:
        restart_effect_runtime()
