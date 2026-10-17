"""Native App recovery across immutable refresh and spend receipts."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from loopx.rollout_event_log import rollout_event_log_path
from tests.capabilities.test_reward_memory_codex_app_outcome import _reflection
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _initialize_fixture_authority, _run_cli,
    _spend_run_count, _write_fixture,
)


def _prepare(tmp_path: Path):
    project, runtime, registry = _write_fixture(tmp_path)
    reflection = _reflection()
    expected = project / "approved-reflection.json"
    expected.write_text(reflection)
    validator = project / "validate.py"
    validator.write_text(
        "import hashlib,json,sys\nfrom pathlib import Path\n"
        f"expected=json.loads(Path({str(expected)!r}).read_text())\n"
        "text=sys.stdin.read()\n"
        "if text:\n"
        " r=json.loads(text)\n"
        " digest='sha256:'+hashlib.sha256(json.dumps(expected,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()\n"
        " assert r['reflection']==expected and r['reflection_digest']==digest\n"
        " print(json.dumps({'schema_version':'reward_memory_reflection_validation_v0','status':'validated','reflection_digest':digest,'evidence_refs':expected['evidence_refs']}))\n"
    )
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    argv = quote(json.dumps([sys.executable, str(validator)], separators=(",", ":")), safe="")
    state.write_text(state.read_text().replace("action_kind=validate -->", f"action_kind=validate validation_command_argv={argv} -->"))
    _initialize_fixture_authority(project, runtime)
    binding = ("--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "turn-app-recovery")

    def cli(*args):
        rc, payload = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, payload
        return payload

    cli("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID, *binding)
    cli("todo", "complete", "--goal-id", GOAL_ID, *binding, "--claimed-by", AGENT_ID,
        "--evidence", "fixture independently validated", "--next-agent-todo", "Continue fixture", "--next-claimed-by", AGENT_ID, "--next-action-kind", "implement")
    refresh = ("refresh-state", "--goal-id", GOAL_ID, "--classification", "app_recovery_verified",
               "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
               *binding, "--no-global-sync", "--suppress-external-sinks")
    spend = ("quota", "spend-slot", "--goal-id", GOAL_ID, "--slots", "1", "--source", "heartbeat", "--execute", *binding)
    return runtime, cli, refresh, spend, reflection


@pytest.mark.parametrize("after_spend", [False, True])
def test_native_replay_recovers_failed_stage_without_rewriting_or_respending(tmp_path, after_spend):
    runtime, cli, refresh, spend, reflection = _prepare(tmp_path)
    first = cli(*refresh, "--reward-memory-reflection-json", "not-inline-json.json")
    assert first["appended"] is True
    assert first["reward_memory_outcome_candidate"]["status"] == "runtime_unavailable"
    rollout = rollout_event_log_path(runtime, GOAL_ID)
    original_events = rollout.read_bytes()
    original_runs = (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_bytes()
    if after_spend:
        assert "reward_memory_ingest" not in cli(*spend)
    recovered = cli(*refresh, "--reward-memory-reflection-json", reflection)
    assert recovered["idempotent_replay"] is True and recovered["appended"] is False
    assert recovered["rollout_event"]["event_id"] == first["rollout_event"]["event_id"]
    assert recovered["rollout_event"]["appended"] is False
    assert recovered["reward_memory_outcome_candidate"]["status"] == "validation_bound"
    finalized = recovered if after_spend else cli(*spend)
    # No experiment was enabled in this fixture: the hook is reached, yet the
    # existing default-off owner performs no external effect.
    assert finalized["reward_memory_ingest"]["status"] == "disabled"
    assert finalized["reward_memory_ingest"]["external_writes_performed"] is False
    assert rollout.read_bytes().startswith(original_events)
    runs = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    assert runs.read_bytes().startswith(original_runs)
    assert _spend_run_count(runtime) == 1
    settled_runs = runs.read_bytes()
    settled_events = rollout.read_bytes()
    repeated = cli(*refresh, "--reward-memory-reflection-json", reflection)
    assert repeated["reward_memory_ingest"]["external_writes_performed"] is False
    assert cli(*spend)["idempotent_replay"] is True
    assert runs.read_bytes() == settled_runs
    assert rollout.read_bytes() == settled_events


def test_native_late_reflection_can_follow_a_refresh_without_memory(tmp_path):
    runtime, cli, refresh, spend, reflection = _prepare(tmp_path)
    cli(*refresh)
    assert "reward_memory_ingest" not in cli(*spend)
    recovered = cli(*refresh, "--reward-memory-reflection-json", reflection)
    assert recovered["idempotent_replay"] is True
    assert recovered["reward_memory_outcome_candidate"]["validation_bound"] is True
    assert recovered["reward_memory_ingest"]["status"] == "disabled"
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize("replacement", ["malformed-json", "changed_reflection"])
def test_failed_late_stage_cannot_finalize_an_earlier_candidate(tmp_path, replacement):
    runtime, cli, refresh, spend, reflection = _prepare(tmp_path)
    cli(*refresh, "--reward-memory-reflection-json", reflection)
    cli(*spend)
    if replacement == "changed_reflection":
        changed = json.loads(reflection)
        changed["content_summary"] = "A different proposal for this exact settlement."
        replacement = json.dumps(changed)
    failed = cli(*refresh, "--reward-memory-reflection-json", replacement)
    assert failed["reward_memory_outcome_candidate"]["validation_bound"] is False
    assert "reward_memory_ingest" not in failed
    assert _spend_run_count(runtime) == 1
