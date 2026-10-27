"""Real heartbeat coverage for an unbound, automatically scheduled Vision wait."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest
from canonical_authority_fixture import read_fixture_todos, initialize_canonical_source_fixture

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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_future_vision_wait_is_quiet_without_binding_or_poll(tmp_path, isolated_effect, monkeypatch, provider):
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    registry, runtime, state = fixture(tmp_path)
    if provider == "sqlite":
        monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite")
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL, handoff_mode="soft_claim", todos=read_fixture_todos(state))
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
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider="sqlite")
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
    assert guard["scheduler_hint"]["action"] == "run_now"
    assert all(limit is None for limit in guard["scheduler_hint"]["unchanged_poll"]["limits"].values())
    assert state.read_bytes() == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_cli_worker_keeps_automatic_wait_alive_and_honors_explicit_stop(
    tmp_path, isolated_effect, monkeypatch, provider,
):
    from argparse import Namespace
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import external_scheduler_worker as worker

    registry, runtime, state = fixture(tmp_path)
    if provider == "sqlite":
        monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite")
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, handoff_mode="soft_claim", todos=read_fixture_todos(state),
    ), state_path=state, provider=provider)
    original_state = state.read_bytes()
    packets, sleeps = [], []

    def real_probe(command, **kwargs):
        # Adapt process transport only: every reply is produced by the real CLI
        # against the isolated authority, with a fresh Turn identity.
        rc, packet = cli(registry, runtime, *command[command.index("quota"):],
                         "--turn-instance-id", f"scheduled-worker-{len(packets)}")
        assert rc == 0, packet
        packets.append(packet)
        return packet

    def advance_host(seconds):
        sleeps.append(seconds)
        assert len(sleeps) <= 4, "explicit stop must end the actual worker"
        if len(sleeps) == 4:
            stopped = json.loads(registry.read_text())
            stopped["goals"][0]["activation_state"] = "stopped"
            registry.write_text(json.dumps(stopped))

    monkeypatch.setattr(worker, "run_quota_should_run", real_probe)
    monkeypatch.setattr(worker, "_run_wake", lambda *a, **kw: pytest.fail("a pending wait cannot wake the model"))
    args = Namespace(cli_bin="loopx", registry=str(registry), runtime_root=str(runtime),
        runtime_profile="generic_cli", goal_id=GOAL, agent_id=AGENT,
        state_file=str(tmp_path / "scheduler-state.json"), once=False, wake_cmd="unused",
        error_backoff_seconds=5, quota_timeout_seconds=90, wake_timeout_seconds=None)
    assert worker.run_worker(args, sleep=advance_host) == 0
    assert sleeps == [600, 1200, 1800, 3600]
    assert len(packets) == 5
    for packet in packets[:-1]:
        assert packet["should_run"] is False
        assert packet["vision_wait_state"]["automatic_resume"] is True
        hint = packet["scheduler_hint"]
        assert hint["action"] == "backoff_until_state_change"
        assert all(limit is None for limit in hint["unchanged_poll"]["limits"].values())
        assert set(hint["unchanged_poll"]["after_limits"].values()) == {"continue"}
        assert hint["unchanged_poll"]["final_quota_replan_check_enabled"] is False
    assert packets[-1]["scheduler_hint"]["action"] == "stop_until_explicit_resume"
    assert packets[-1]["scheduler_hint"]["cadence_class"] == "goal_stopped"
    assert state.read_bytes() == original_state
    runs = [json.loads(line) for line in (runtime / f"goals/{GOAL}/runs/index.jsonl").read_text().splitlines()]
    assert not any(run.get("classification") in ("quota_monitor_poll", "quota_slot_spent") for run in runs)


def test_independent_work_is_not_silenced_by_scheduled_wait(tmp_path, isolated_effect):
    registry, runtime, state = fixture(tmp_path)
    with state.open("a", encoding="utf-8") as output:
        output.write("\n- [ ] [P2] Validate independent public evidence.\n"
                     f"  <!-- loopx:todo todo_id=todo_independent status=open claimed_by={AGENT} "
                     "task_class=advancement_task action_kind=validate -->\n")
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider="sqlite")
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app",
                    "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "independent-work")
    assert rc == 0, guard
    assert guard["selected_todo"]["todo_id"] == "todo_independent"
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is True


def test_bound_monitor_keeps_exact_turn_and_agent_fences(tmp_path, isolated_effect):
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    registry, runtime, state = fixture(tmp_path)
    with state.open("a", encoding="utf-8") as output:
        output.write("\n- [ ] [P1-monitor] Observe a public result.\n"
                     f"  <!-- loopx:todo todo_id=todo_result_monitor status=open claimed_by={AGENT} "
                     "task_class=continuous_monitor action_kind=observe target_key=public-result "
                     "cadence=10m next_due_at=2026-01-01T00:00:00Z -->\n")
    initialize_canonical_authority(
        runtime, GOAL,
        build_todo_runtime_shadow_projection(goal_id=GOAL, todos=read_fixture_todos(state), handoff_mode="soft_claim"),
        state_path=state,
    )
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("historical_gap", [False, True])
def test_blocked_replan_closes_without_debit_and_retains_scheduled_work(
    tmp_path, isolated_effect, monkeypatch, provider, historical_gap,
):
    from canonical_authority_fixture import initialize_canonical_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

    registry, runtime, state = fixture(tmp_path)
    with state.open("a", encoding="utf-8") as output:
        output.write(
            "\n- [ ] [P1-monitor] Observe the existing public source.\n"
            f"  <!-- loopx:todo todo_id=todo_review_source status=open claimed_by={AGENT} "
            "task_class=continuous_monitor action_kind=observe target_key=public-source "
            "cadence=30m next_due_at=2099-01-01T00:00:00Z "
            "consecutive_no_change=8 max_no_change_before_replan=5 result_hash=source-v1 -->\n"
        )
        if historical_gap:
            output.write(
                "\n- [x] [P1] Retain an independent completed result.\n"
                f"  <!-- loopx:todo todo_id=todo_prior_result status=done claimed_by={AGENT} "
                "task_class=advancement_task action_kind=validate completion_continuation=active_goal -->\n"
                "\n- [ ] [P1] Keep the peer's independent work.\n"
                "  <!-- loopx:todo todo_id=todo_peer_work status=open claimed_by=peer-worker "
                "task_class=advancement_task action_kind=validate -->\n"
            )
    if historical_gap:
        # Peer work keeps the shared Goal advancing but is not selectable by
        # this Agent; its old lifecycle warning must not veto another duty.
        document = json.loads(registry.read_text())
        document["goals"][0]["coordination"]["registered_agents"].append("peer-worker")
        registry.write_text(json.dumps(document))
    if provider == "sqlite":
        monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite")
    # Provision the synthetic canonical source before exercising real CLI reads;
    # normal readers must not fall back to an unmigrated Markdown source.
    todos = parse_active_state_todos(state.read_text(), item_limit=None)["agent_todos"]["items"]
    initialize_canonical_authority(
        runtime, GOAL,
        build_todo_runtime_shadow_projection(goal_id=GOAL, handoff_mode="soft_claim", todos=todos),
        state_path=state, provider=provider,
    )
    rc, before = cli(registry, runtime, "todo", "list", "--goal-id", GOAL)
    assert rc == 0, before
    if historical_gap:
        assert before["agent_todos"]["completed_without_successor_count"] == 1
    turn = "blocked-source-review"
    rc, guard = cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
                    "--agent-id", AGENT, "--turn-instance-id", turn)
    assert rc == 0, guard
    assert guard["effective_action"] == "autonomous_replan_required"
    assert [row["kind"] for row in guard["autonomous_replan_obligation"]["triggers"]] == [
        "monitor_no_change_streak",
    ]
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    obligation = identity["replan_obligation_id"]
    assert identity["binding_kind"] == "autonomous_replan"
    assert "todo_id" not in identity
    vision = {
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "todo_delta": [f"retain:{TODO}"],
        "vision_patch": {"acceptance_summary": "The scheduled delivery remains unqualified.",
                         "advancement_policy": "as_needed",
                         "replan_trigger_summary": "Retain the pending scheduled work and original acceptance."},
        "path_delta": {"outcome": "continue", "prior_assumption": "The source streak needs review.",
                       "observed_reality": "The canonical delivery still waits at its original deadline.",
                       "retained": ["The original unfinished delivery and source observation."],
                       "evidence_refs": ["evidence:canonical-scheduled-work"]},
    }
    control = guard["replan_action_packet"].get("stride_control")
    if control:
        vision["stride_decision"] = {
            "schema_version": "stride_decision_v2", "strategy_version": "evidence_conditioned_v1",
            "reference_context_id": control["reference_context_id"],
            "reference_obligation_id": obligation, "basis": "uncertain_evidence", "action": "verify",
            "axes": {axis: {"plan": "Verify the existing delivery when its source is ready.",
                            "reason": "A pending dependency does not justify duplicate execution."}
                     for axis in ("work_slice", "route_distance", "validation_scope")},
            "next_probe": {"scope": "The original scheduled delivery.",
                           "prediction": "Resume only after its original condition is ready.",
                           "adoption_criterion": "Current source and original acceptance qualify execution.",
                           "cost_bound": "No source poll or repeated work before the deadline.",
                           "minimum_evidence": "Canonical resume readback and applicable source evidence."},
        }
    packet = tmp_path / "review.json"
    packet.write_text(json.dumps(vision), encoding="utf-8")
    refresh_args = (
        "refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
        "--replan-obligation-id", obligation, "--turn-instance-id", turn,
        "--classification", "blocked_source_review", "--progress-scope", "agent_lane",
        "--delivery-batch-scale", "single_surface", "--delivery-boundary", "semantic_closeout",
        "--delivery-outcome", "outcome_gap", "--progress-result-class", "blocked",
        "--progress-blocker-id", "source-not-ready", "--progress-evidence-id", "evidence:canonical-scheduled-work",
        "--agent-vision-json", str(packet),
    )
    unchanged_state = state.read_bytes()
    for flag, value in (("--turn-instance-id", "unadmitted-turn"), ("--agent-id", "foreign-agent")):
        wrong = list(refresh_args)
        wrong[wrong.index(flag) + 1] = value
        rc, refused = cli(registry, runtime, *wrong)
        assert rc == 1, refused
        assert refused["ok"] is False and not refused.get("appended"), refused
        assert state.read_bytes() == unchanged_state
    rc, refreshed = cli(registry, runtime, *refresh_args)
    assert rc == 0, json.dumps(refreshed)
    assert refreshed["settlement_identity"] == identity
    assert refreshed["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True
    assert refreshed["settlement_progress"]["state"] == "settled", refreshed["settlement_progress"]
    assert refreshed["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
    rc, replay = cli(registry, runtime, *refresh_args)
    assert rc == 0 and replay["appended"] is False, replay
    assert replay["settlement_progress"]["state"] == "settled"
    rc, spent = cli(registry, runtime, "quota", "spend-slot", "--execute", "--goal-id", GOAL,
                    "--agent-id", AGENT, "--replan-obligation-id", obligation, "--turn-instance-id", turn)
    assert rc == 0 and spent["appended"] is False, spent
    rc, settled = cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
                      "--agent-id", AGENT, "--turn-instance-id", turn)
    assert rc == 0 and settled["effective_action"] == "heartbeat_settled_skip", settled
    rc, later = cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
                    "--agent-id", AGENT, "--turn-instance-id", "after-blocked-review")
    assert rc == 0, later
    assert (later.get("autonomous_replan_obligation") or {}).get("obligation_id") != obligation
    assert later["interaction_contract"]["agent_channel"]["must_attempt"] is False
    rc, after = cli(registry, runtime, "todo", "list", "--goal-id", GOAL)
    assert rc == 0, after
    if historical_gap:
        assert after["agent_todos"]["completed_without_successor_count"] == 1
        assert next(row for row in after["todos"] if row["todo_id"] == "todo_peer_work")["claimed_by"] == "peer-worker"
    for original in before["todos"]:
        current = next(row for row in after["todos"] if row["todo_id"] == original["todo_id"])
        for field in ("status", "text", "claimed_by", "resume_when", "consecutive_no_change",
                      "result_hash", "material_change_generation", "next_due_at"):
            assert current.get(field) == original.get(field)
    runs = [json.loads(line) for line in (runtime / f"goals/{GOAL}/runs/index.jsonl").read_text().splitlines()]
    assert not any(run.get("quota_event") for run in runs)
    # A quiet guard may record host liveness under a historical monitor-run
    # classification. Only a committed poll is an observation of the source.
    events = [json.loads(line) for line in (runtime / f"goals/{GOAL}/rollout-event-log.jsonl").read_text().splitlines()]
    assert not any(event.get("event_kind") in ("quota_monitor_poll", "quota_spend") for event in events)
    rc, added = cli(registry, runtime, "todo", "add", "--goal-id", GOAL, "--role", "agent",
                    "--claimed-by", AGENT, "--text", "Validate independent public evidence.",
                    "--task-class", "advancement_task", "--action-kind", "validate", "--execute")
    assert rc == 0 and added["added"] is True, added
    rc, independent = cli(registry, runtime, "quota", "should-run", "--codex-app", "--goal-id", GOAL,
                          "--agent-id", AGENT, "--turn-instance-id", "independent-after-review")
    assert rc == 0, independent
    assert independent["selected_todo"]["todo_id"] == added["todo_id"]
    assert independent["interaction_contract"]["agent_channel"]["must_attempt"] is True
