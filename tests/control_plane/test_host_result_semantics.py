"""A real DSH subprocess preserves the result that native settlement validates."""
from __future__ import annotations

import json
import sys

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from tests.control_plane.test_turn_host_decision_capture import _fixture_module


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("candidate_fields,error", [
    ({"next_action": "Review the evidence. " * 65 + "Stop before publication; approval is still required."},
     "next_action exceeds 1200 characters"),
    ({"next_action": {"action": "Continue", "constraint": "Stop before publication."}},
     "next_action must be a string"),
    ({"next_action": False}, "next_action must be a string"),
    ({"delivery_batch_scale": "single_segment"}, "legacy alias 'single_segment' is read-only"),
    ({"delivery_outcome": False}, "delivery_outcome must be one of"),
    ({"delivery_outcome": "surface_only"}, "cannot settle a Turn"),
    ({"delivery_outcome": "outcome_gap"}, "blocked"),
], ids=["overlimit", "object", "false", "legacy-scale", "invalid-outcome", "surface-only", "untyped-gap"])
def test_native_turn_rejects_invalid_candidate_before_writeback(
    tmp_path, monkeypatch, provider, candidate_fields, error,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    f = _fixture_module()
    project, runtime, workspace, registry = f._write_fixture(tmp_path, turn_count=2)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    code, listed = f._run_cli([*prefix, "todo", "list", "--goal-id", f.GOAL_ID])
    assert code == 0, listed
    state = project / ".codex/goals" / f.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(runtime, f.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=f.GOAL_ID, handoff_mode="soft_claim", todos=listed["todos"]),
        state_path=state, provider=provider)
    candidate = {
        "result_kind": "validated_progress", "classification": "artifact_checked",
        "summary": "The isolated artifact was checked.",
        "recommended_action": "Continue the accepted task.",
        "next_action": "Continue the accepted task.",
        "vision_unchanged_reason": "The accepted Goal remains unchanged.",
        **candidate_fields,
    }
    (workspace / "candidate.json").write_text(json.dumps(candidate))
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import json\nfrom pathlib import Path\n"
        "def run_dsh_turn(*, prompt, workspace, **kwargs):\n"
        "    root = Path(workspace)\n"
        "    with (root / 'invocations.txt').open('a') as out: out.write('host\\n')\n"
        "    (root / 'received.txt').write_text(prompt)\n"
        "    return (root / 'candidate.json').read_text()\n"
    )
    validator = [sys.executable, "-c",
        "import json,pathlib,sys; json.load(sys.stdin); "
        "pathlib.Path('validator-ran').write_text('verified')"]
    base = [*prefix, "turn", "run-once", "--goal-id", f.GOAL_ID, "--agent-id", f.AGENT_ID,
        "--host", "generic-cli", "--execution-mode", "isolated-headless", "--project", str(workspace),
        "--host-command-json", json.dumps([sys.executable, "-m", "loopx.dsh_goal_mode",
            "--workspace", str(workspace), "--dsh-home", str(tmp_path / "dsh-home"), "--dsh-runner", str(runner)]),
        "--validation-command-json", json.dumps(validator),
        "--no-global-sync", "--timeout-seconds", "30"]
    code, failed = f._run_cli([*base, "--turn-instance-id", "invalid-result", "--execute"])
    assert code == 1 and not failed["ok"], failed
    assert error in json.dumps(failed)
    assert failed["schema_version"] == "loopx_turn_execution_v0"
    assert failed["status"] == "failed"
    assert failed["result_kind"] == "validation_failed"
    assert failed["validation_stage"] == "host_result_contract"
    assert failed["effects"] == {
        "host_invoked": True, "state_written": False,
        "quota_spent": False, "scheduler_acknowledged": False,
    }
    resume_key = failed["resume_turn_key"]
    journal_path, = (runtime / "goals" / f.GOAL_ID / "turns").glob("*.json")
    journal = json.loads(journal_path.read_text())
    assert journal["status"] == "failed"
    assert journal["completed_phases"] == ["host_execute", "typed_result"]
    assert not journal.get("effect_attempts")
    assert "writeback" not in journal and "quota_spend" not in journal
    assert not (workspace / "validator-ran").exists()
    assert f._quota_spend_count(runtime) == 0
    code, current = f._run_cli([*prefix, "todo", "list", "--goal-id", f.GOAL_ID])
    assert code == 0 and current["todos"] == listed["todos"]

    # A correction is a fresh admitted Turn, not a replay of an invalid result.
    candidate.update({
        "next_action": "Stop before publication; approval is still required.",
        "delivery_batch_scale": "implementation", "delivery_outcome": "outcome_progress",
    })
    (workspace / "candidate.json").write_text(json.dumps(candidate))
    code, repeated = f._run_cli([*base, "--resume-turn-key", resume_key, "--execute"])
    assert code == 1 and not repeated["ok"], repeated
    assert repeated["status"] == "failed" and not any(repeated["effects"].values())
    assert f._quota_spend_count(runtime) == 0
    assert (workspace / "invocations.txt").read_text() == "host\n"
    assert not (workspace / "validator-ran").exists()
    code, recovered = f._run_cli([*base, "--turn-instance-id", "corrected-result", "--execute"])
    assert code == 0 and recovered["status"] == "committed", recovered
    assert (workspace / "validator-ran").read_text() == "verified"
    assert f._quota_spend_count(runtime) == 1
    rows = [json.loads(line) for line in
        (runtime / "goals" / f.GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    progress = next(row for row in rows if row.get("classification") == "artifact_checked")
    assert progress["delivery_batch_scale"] == "implementation"
    assert progress["delivery_outcome"] == "outcome_progress"
    code, replay = f._run_cli([*base, "--resume-turn-key", recovered["resume_turn_key"], "--execute"])
    assert code == 0 and replay["ok"], replay
    assert not any(replay["effects"].values())
    assert f._quota_spend_count(runtime) == 1
    assert (workspace / "invocations.txt").read_text() == "host\nhost\n"
