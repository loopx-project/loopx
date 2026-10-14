"""Real heartbeat coverage for an unbound, automatically scheduled Vision wait."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from loopx.control_plane.effect_runtime import restart_effect_runtime

REPO = Path(__file__).resolve().parents[2]
GOAL = "scheduled-vision-fixture"
AGENT = "agent-scheduled"
TODO = "todo_scheduled_delivery"
FUTURE = "resume_at:2099-01-01T00:00:00Z"


@pytest.fixture
def isolated_effect(tmp_path: Path, monkeypatch):
    # Runtime-root alone does not isolate a managed Effect process.
    for variable in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(variable, str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    yield
    restart_effect_runtime()


def fixture(root: Path, resume: str = FUTURE) -> tuple[Path, Path, Path]:
    project, runtime = root / "project", root / "runtime"
    state = project / f".codex/goals/{GOAL}/ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\nowner_mode: goal\n"
        'objective: "Deliver the scheduled public fixture."\n'
        "updated_at: 2026-01-01T00:00:00Z\n---\n\n# Scheduled fixture\n\n"
        "## Objective\n\nDeliver the scheduled public fixture.\n\n"
        "## Next Action\n\n- Resume at the scheduled instant.\n\n"
        "## Agent Todo\n\n- [ ] [P1] Deliver the scheduled fixture.\n"
        f"  <!-- loopx:todo todo_id={TODO} status=deferred claimed_by={AGENT} "
        f"task_class=advancement_task action_kind=validate resume_when={resume} -->\n",
        encoding="utf-8",
    )
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir()
    registry.write_text(json.dumps({
        "schema_version": "0.1", "common_runtime_root": str(runtime),
        "goals": [{"id": GOAL, "repo": str(project), "status": "active",
                   "domain": "scheduled-vision-fixture",
                   "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
                   "authority_sources": [],
                   "state_file": str(state.relative_to(project)),
                   "coordination": {"registered_agents": [AGENT], "agent_model": "peer_v1"},
                   "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 20}}],
    }), encoding="utf-8")
    runs = runtime / f"goals/{GOAL}/runs"
    runs.mkdir(parents=True)
    run = {"goal_id": GOAL, "agent_id": AGENT,
           "classification": "scheduled_vision_fixture", "generated_at": "2026-01-01T00:00:00Z",
           "progress_scope": "agent_lane", "agent_vision": {
               "schema_version": "goal_vision_replan_contract_v0", "agent_id": AGENT,
               "state": "vision_drift_detected", "todo_delta": [f"activate:{TODO}"],
               "vision_patch": {"acceptance_summary": "Deliver this same scheduled fixture.",
                                "advancement_policy": "as_needed",
                                "replan_trigger_summary": "The scheduled acceptance remains open."}}}
    (runs / "vision.json").write_text(json.dumps(run), encoding="utf-8")
    (runs / "index.jsonl").write_text(json.dumps({**run, "json_path": str(runs / "vision.json")}) + "\n", encoding="utf-8")
    return registry, runtime, state


def cli(registry: Path, runtime: Path, *args: str) -> tuple[int, dict]:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
         "--runtime-root", str(runtime), "--format", "json", *args],
        cwd=REPO, env={**os.environ, "PYTHONPATH": str(REPO), "LOOPX_USAGE_PING": "0"},
        capture_output=True, text=True, check=False, timeout=90,
    )
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_future_vision_wait_is_quiet_without_binding_or_poll(tmp_path, isolated_effect, monkeypatch, provider):
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    registry, runtime, state = fixture(tmp_path)
    if provider != "legacy":
        if provider == "sqlite":
            monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite")
        rc, listed = cli(registry, runtime, "todo", "list", "--goal-id", GOAL)
        assert rc == 0, listed
        projection = build_todo_runtime_shadow_projection(goal_id=GOAL, handoff_mode="soft_claim", todos=listed["todos"])
        initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=provider)
    original_state = state.read_bytes()
    args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL,
            "--agent-id", AGENT, "--turn-instance-id", "scheduled-before-due")
    rc, first = cli(registry, runtime, *args)
    assert rc == 0, json.dumps(first)
    assert first["decision"] == "agent_scope_wait"
    assert first["heartbeat_receipt"]["status"] == "committed"
    assert "settlement_identity" not in first["heartbeat_receipt"]
    # An explicit unbound poll still fails closed; the repair changes guidance,
    # not the command's identity qualification.
    poll_rc, poll = cli(registry, runtime, "quota", "monitor-poll", "--execute",
                        "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
                        "--turn-instance-id", "scheduled-before-due")
    assert poll_rc == 1, poll
    assert poll["error_code"] == "heartbeat_receipt_identity_conflict"
    contract = first["interaction_contract"]
    assert contract["agent_channel"]["must_attempt"] is False
    assert contract["agent_channel"]["quiet_noop_allowed"] is True
    assert contract["agent_channel"]["delivery_allowed"] is False
    assert not any("monitor-poll" in action for action in contract["cli_channel"]["next_cli_actions"])
    assert first["vision_wait_state"]["resume_when"] == FUTURE
    assert first["goal_frontier_projection"]["replan_required"] is False
    assert first["heartbeat_receipt"].get("closeout_required") is not True
    rc, replay = cli(registry, runtime, *args)
    assert rc == 0, replay
    assert replay["heartbeat_receipt"]["status"] == "replayed"
    assert replay["interaction_contract"]["agent_channel"]["must_attempt"] is False
    for turn in ("scheduled-next-wake", "scheduled-third-wake"):
        rc, later = cli(registry, runtime, *args[:-1], turn)
        assert rc == 0, later
        assert later["effective_action"] == "agent_scope_wait"
        assert later["interaction_contract"]["agent_channel"]["must_attempt"] is False
        assert later["vision_wait_state"]["resume_when"] == FUTURE
        assert later["goal_frontier_projection"]["replan_required"] is False
    assert state.read_bytes() == original_state
    rc, listed = cli(registry, runtime, "todo", "list", "--goal-id", GOAL)
    assert rc == 0, listed
    scheduled = next(todo for todo in listed["todos"] if todo["todo_id"] == TODO)
    assert scheduled["status"] == "deferred"
    assert scheduled["resume_when"] == FUTURE
    runs = [json.loads(line) for line in (runtime / f"goals/{GOAL}/runs/index.jsonl").read_text().splitlines()]
    assert not any(run.get("classification") in ("quota_monitor_poll", "quota_slot_spent") for run in runs)


def test_due_vision_wait_restores_explicit_resume(tmp_path, isolated_effect):
    registry, runtime, state = fixture(tmp_path, "resume_at:2026-01-01T00:00:00Z")
    original = state.read_bytes()
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app",
                    "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "scheduled-due")
    assert rc == 0, guard
    assert guard["effective_action"] == "successor_replan_required"
    assert guard["selected_todo"]["todo_id"] == TODO
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert any("todo update" in action and "--clear-resume-when" in action
               for action in guard["interaction_contract"]["cli_channel"]["next_cli_actions"])
    assert "vision_wait_state" not in guard
    assert state.read_bytes() == original


def test_independent_work_is_not_silenced_by_scheduled_wait(tmp_path, isolated_effect):
    registry, runtime, state = fixture(tmp_path)
    with state.open("a", encoding="utf-8") as output:
        output.write("\n- [ ] [P2] Validate independent public evidence.\n"
                     f"  <!-- loopx:todo todo_id=todo_independent status=open claimed_by={AGENT} "
                     "task_class=advancement_task action_kind=validate -->\n")
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app",
                    "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "independent-work")
    assert rc == 0, guard
    assert guard["selected_todo"]["todo_id"] == "todo_independent"
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is True


def test_bound_monitor_keeps_exact_turn_and_agent_fences(tmp_path, isolated_effect):
    registry, runtime, state = fixture(tmp_path)
    with state.open("a", encoding="utf-8") as output:
        output.write("\n- [ ] [P1-monitor] Observe a public result.\n"
                     f"  <!-- loopx:todo todo_id=todo_result_monitor status=open claimed_by={AGENT} "
                     "task_class=continuous_monitor action_kind=observe target_key=public-result "
                     "cadence=10m next_due_at=2026-01-01T00:00:00Z -->\n")
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app",
                    "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "bound-monitor")
    assert rc == 0, guard
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == "todo_result_monitor"
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    original = state.read_bytes()
    for turn, agent in [("stale-monitor-turn", AGENT), ("bound-monitor", "agent-foreign")]:
        rc, refused = cli(registry, runtime, "quota", "monitor-poll", "--execute", "--codex-app",
                          "--goal-id", GOAL, "--agent-id", agent, "--turn-instance-id", turn,
                          "--todo-id", "todo_result_monitor", "--result-hash", "public-result-1")
        assert rc == 1, refused
        assert refused["error_code"] == "heartbeat_receipt_identity_conflict"
        assert state.read_bytes() == original
    rc, poll = cli(registry, runtime, "quota", "monitor-poll", "--execute", "--codex-app",
                   "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "bound-monitor",
                   "--todo-id", "todo_result_monitor", "--result-hash", "public-result-1")
    assert rc == 0, poll
    assert poll["appended"] is True
    assert poll["material_change"] is False
    rc, after = cli(registry, runtime, "quota", "should-run", "--codex-app",
                    "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "after-monitor")
    assert rc == 0, after
    assert after["effective_action"] != "unsettled_host_turn_recovery"
    assert after["interaction_contract"]["agent_channel"]["must_attempt"] is False
