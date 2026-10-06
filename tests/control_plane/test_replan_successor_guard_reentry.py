"""A canonical successor changes the frontier, never the original Turn binding."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import loopx

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_replan_successor_durable_ack import AGENT, GOAL, history

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals.goal_vision import compact_goal_vision_packet, normalize_goal_vision_packet


TURN = "turn-periodic-successor-review"
ROOT = Path(loopx.__file__).resolve().parents[1]


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, *, later_vision: bool = False, future_monitor: bool = True):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n# Synthetic Goal\n\n## Objective\nDeliver the independently accepted source outcome.\n\n## Agent Todo\n")
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    evidence = index.parent / "synthetic-artifact.json"
    report = index.parent / "synthetic-artifact.md"
    evidence.write_text(json.dumps({"ok": True, "fixture": "public-safe-replan"}))
    report.write_text("# Synthetic source audit\n")
    runs = history()
    for row in runs:
        row.update(json_path=str(evidence), markdown_path=str(report))
    runs[0]["agent_vision"] = compact_goal_vision_packet(normalize_goal_vision_packet({
        "goal_id": GOAL, "agent_id": AGENT, "state": "vision_drift_detected",
        "vision_patch": {"acceptance_summary": "Independently validate a source artifact.",
                         "advancement_policy": "repeat_until_closed"},
    }, goal_id=GOAL, agent_id=AGENT))
    if later_vision:
        # A settled periodic review is older than the new acceptance gap.
        # The real guard must not turn its vision-patch label into a waiver.
        old = {**runs[1], "generated_at": "2026-08-01T00:29:00Z",
            "autonomous_replan_ack": {
                "schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "delta_contract": {"delta_kinds": ["goal_vision_patch"]},
                "semantic_delta": {"accepted": True, "outcomes": ["fresh_vision_path_outcome"],
                    "satisfying_outcomes": ["fresh_vision_path_outcome"],
                    "trigger_kinds": ["periodic_review_due"], "trigger_checkpoints": [],
                    "obligation_id": "replan-1111111111111111"}}}
        runs = [{**runs[0], "generated_at": "2026-08-01T00:30:00Z"}, old]
    index.write_text("".join(json.dumps(row) + "\n" for row in reversed(runs)))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": "active", "repo": str(project), "state_file": state.name,
        "domain": "synthetic-replan",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT, "different-agent"]},
    }]}))
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=[], handoff_mode="soft_claim", leases=[],
    ), state_path=state, provider=provider)

    def call(*args: str, expected_code: int = 0) -> dict:
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args], cwd=ROOT,
            capture_output=True, text=True, timeout=60)
        payload = json.loads(result.stdout)
        assert result.returncode == expected_code, {k: payload.get(k) for k in ["error", "reason", "autonomous_replan_obligation", "heartbeat_receipt", "goal_frontier_projection"]}
        return payload

    if later_vision and future_monitor:
        call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
            "--text", "Observe the independent external gate", "--task-class", "continuous_monitor",
            "--action-kind", "monitor", "--target-key", "external-gate",
            "--cadence", "30m", "--next-due-at", "2099-01-01T00:00:00Z",
            "--expires-at", "2099-01-02T00:00:00Z")
    return call, runtime, index


def _guard(call, turn: str = TURN, *, expected_code: int = 0):
    return call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                "--agent-id", AGENT, "--turn-instance-id", turn,
                "--codex-app-current-rrule", "FREQ=MINUTELY;INTERVAL=3", expected_code=expected_code)


def _add(call, obligation_id: str):
    return call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-source-artifact",
        "--operation-id", "periodic-source-successor", "--replan-obligation-id", obligation_id)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("ack_at,gap_at,replan", [
    ("2026-08-01T00:30:00.000100Z", "2026-08-01T00:30:00.000900Z", True),
    ("2026-08-01T00:30:00.000100Z", "2026-08-01T00:30:00.000100Z", False),
    ("2026-08-01T08:30:00.000100+08:00", "2026-08-01T00:30:00.000100Z", False),
    ("2026-02-30T00:00:00Z", "2026-02-28T00:00:00Z", True),
])
def test_real_guard_uses_strict_microsecond_vision_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    ack_at: str, gap_at: str, replan: bool,
) -> None:
    call, _, index = _fixture(tmp_path, monkeypatch, provider, later_vision=True)
    # Author only this disposable fixture's history, before its first guard.
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    for row in rows:
        row["generated_at"] = ack_at if row.get("autonomous_replan_ack") else gap_at
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    invalid_history = ack_at == "2026-02-30T00:00:00Z"
    guarded = _guard(call, expected_code=1 if invalid_history else 0)
    if invalid_history:
        # Rejecting the ACK exposes the invalid durable source to context
        # validation. Fail closed before committing any settlement receipt.
        assert guarded["should_run"] is False
        assert guarded["heartbeat_receipt"]["status"] == "write_failed"
        return
    assert guarded["goal_frontier_projection"]["acceptance_gaps"]
    assert bool(guarded.get("autonomous_replan_obligation")) is replan
    assert guarded["should_run"] is replan
    assert guarded["decision"] == ("autonomous_replan_required" if replan else "skip")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("later_vision", [False, True])
def test_successor_guard_returns_original_settlement_not_repeated_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, later_vision: bool,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, later_vision=later_vision)
    original = _guard(call)
    if later_vision:
        assert original["goal_frontier_projection"]["normalized_progress"]["agent_monitor_open_count"] == 1
        assert original["decision"] == "autonomous_replan_required"
        assert original["execution_obligation"]["must_attempt_work"] is True
        assert original["autonomous_replan_obligation"]["triggers"][0]["kind"] == "vision_acceptance_gap"
    core_goal = original["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert core_goal["objective"] == "Deliver the independently accepted source outcome."
    assert core_goal["objective_source"] == "active_state"
    identity = original["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    obligation_id = identity["replan_obligation_id"]
    added = _add(call, obligation_id)
    assert added["replan_transition"]["recorded"] is True
    assert added["replan_transition"]["outcome"] == "new_runnable_successor"

    # This same-Turn read is a closeout query, not permission for the new work.
    reread = _guard(call)
    assert reread["heartbeat_receipt"]["settlement_identity"] == identity
    assert reread.get("selected_todo") is None
    assert reread["autonomous_replan_obligation"]["resolution_mode"] == "receipt_bound_replan_settlement"
    packet = reread["replan_action_packet"]
    assert packet["obligation_id"] == obligation_id
    assert packet["successor_todo_id"] == added["todo_id"]
    assert packet["writeback_contract"]["preferred_input"] == "canonical_successor_transition"
    actions = reread["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert len(actions) == 2
    assert all("todo add" not in command for command in actions)
    assert all("--replan-obligation-id " + obligation_id in command for command in actions)
    assert all("--turn-instance-id " + TURN in command for command in actions)
    envelope = call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                    "--agent-id", AGENT, "--turn-instance-id", TURN, "--turn-envelope")
    assert envelope["replan_action_packet"]["settlement_only"] is True
    assert envelope["writeback"]["next_cli_actions"] == actions
    assert envelope["action_signature"]["matches"] is True
    premature_spend = call(*shlex.split(actions[1])[1:], expected_code=1)
    assert premature_spend.get("appended") is not True
    assert not any(json.loads(line).get("classification") == "quota_slot_spent"
                   for line in index.read_text().splitlines())

    # Execute the returned commands unchanged; no invented progress fields,
    # successor completion or unverified spend admission are supplied by us.
    refreshed = call(*shlex.split(actions[0])[1:], "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    persisted = json.loads(Path(refreshed["json_path"]).read_text())
    assert persisted["autonomous_replan_ack"]["semantic_delta"]["obligation_id"] == obligation_id
    pending = _guard(call)
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    assert pending["settlement_progress"]["state"] == "spend_required"
    spend_actions = pending["interaction_contract"]["cli_channel"]["next_cli_actions"]
    assert spend_actions == [actions[1]]
    pending_envelope = call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
        "--agent-id", AGENT, "--turn-instance-id", TURN, "--turn-envelope")
    assert pending_envelope["writeback"]["next_cli_actions"] == spend_actions
    assert pending_envelope["action_signature"]["matches"] is True
    spent = call(*shlex.split(spend_actions[0])[1:])
    assert spent["settlement_progress"]["state"] == "settled"
    assert spent["appended"] is True
    assert call(*shlex.split(actions[1])[1:])["appended"] is False
    settled = _guard(call)
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled.get("selected_todo") is None
    hint = settled["scheduler_hint"]
    assert hint["action"] == "preserve_current_schedule"
    assert hint["app_automation"]["host_action"] == "none"
    assert "recommended_rrule" not in hint["app_automation"]
    assert "ack_hint" not in hint["app_automation"]
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    todo = call("todo", "list", "--goal-id", GOAL, "--todo-id", added["todo_id"])["todo"]
    assert todo["status"] == "open"
    # A later display/priority head must not displace the accepted new route.
    competing = call("todo", "add", "--goal-id", GOAL, "--role", "agent",
        "--claimed-by", AGENT, "--text", "[P0] Validate the older independent route",
        "--task-class", "advancement_task", "--action-kind", "validate",
        "--operation-id", "competing-prior-route")
    fresh = _guard(call, "turn-independent-vision-review")
    assert fresh["effective_action"] != "heartbeat_settled_skip"
    assert fresh["scheduler_hint"]["action"] == "run_now"
    assert fresh["scheduler_hint"]["app_automation"]["recommended_interval_minutes"] == 3
    assert fresh["goal_frontier_projection"]["acceptance_gaps"]
    assert fresh["goal_frontier_projection"]["vision_continuation_audit"]["decision"] == "acceptance_gap_open"
    assert fresh["selected_todo"]["todo_id"] == added["todo_id"]
    assert fresh["agent_lane_next_action"]["todo_id"] == added["todo_id"]
    portfolio = fresh["action_portfolio"]
    assert portfolio["primary"]["todo_id"] == added["todo_id"]
    assert any(row["todo_id"] == competing["todo_id"] and row["selection_role"] == "alternative"
               for row in portfolio["suggested_actions"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("invalidation", ["wrong_owner", "deferred", "unclaimed"])
@pytest.mark.parametrize("later_vision", [False, True])
def test_invalidated_successor_cannot_rebind_or_close_original_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, invalidation: str, later_vision: bool,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, later_vision=later_vision)
    original = _guard(call)
    identity = original["heartbeat_receipt"]["settlement_identity"]
    added = _add(call, identity["replan_obligation_id"])
    prior_actions = _guard(call)["interaction_contract"]["cli_channel"]["next_cli_actions"]
    edit = {"wrong_owner": ["--claimed-by", "different-agent"],
            "deferred": ["--status", "deferred", "--resume-when", "resume_at:2099-01-01T00:00:00Z"],
            "unclaimed": ["--clear-claim"]}[invalidation]
    call("todo", "update", "--goal-id", GOAL, "--agent-id", AGENT,
         "--todo-id", added["todo_id"], *edit)
    # Invalidation may leave the original duty open, which is a legal guard
    # read. It must not grant successor settlement from a stale creation ACK.
    guarded = _guard(call, expected_code=1 if later_vision and invalidation == "unclaimed" else 0)
    assert guarded["heartbeat_receipt"]["settlement_identity"] == identity
    assert guarded.get("selected_todo") is None
    assert (guarded.get("autonomous_replan_obligation") or {}).get("resolution_mode") != "receipt_bound_replan_settlement"
    assert (guarded.get("replan_action_packet") or {}).get("settlement_only") is not True
    denied = call(*shlex.split(prior_actions[0])[1:], "--no-global-sync",
                  "--suppress-external-sinks", expected_code=1)
    assert denied.get("appended") is not True
    denied_spend = call(*shlex.split(prior_actions[1])[1:], expected_code=1)
    assert denied_spend.get("appended") is not True
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_later_vision_replan_can_close_covered_frontier_and_settle_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    call, _, index = _fixture(tmp_path, monkeypatch, provider, later_vision=True, future_monitor=False)
    state = tmp_path / "project" / "ACTIVE_GOAL_STATE.md"
    # Terminal convergence requires explicit complete sources, never absence.
    state.write_text(state.read_text() + "\n## User Todo / Owner Review Reading Queue\n")
    covered = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Observe the now covered source", "--task-class", "continuous_monitor",
        "--action-kind", "monitor", "--target-key", "covered-source", "--cadence", "30m",
        "--next-due-at", "2099-01-01T00:00:00Z", "--expires-at", "2099-01-02T00:00:00Z")
    call("todo", "complete", "--goal-id", GOAL, "--todo-id", covered["todo_id"],
        "--agent-id", AGENT, "--no-follow-up", "--evidence", "Declared source coverage is complete.")
    guard = _guard(call)
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    assert guard["autonomous_replan_obligation"]["triggers"][0]["kind"] == "vision_acceptance_gap"
    binding = ("--goal-id", GOAL, "--agent-id", AGENT,
               "--turn-instance-id", TURN, "--replan-obligation-id", identity["replan_obligation_id"])
    vision = tmp_path / "covered-vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "no_followup",
        "vision_patch": {"acceptance_summary": "The independently accepted source outcome is covered."},
        "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "stop",
            "prior_assumption": "The source frontier still needs another bounded probe.",
            "observed_reality": "The declared coverage is complete and no successor remains.",
            "retained": ["the independently accepted source contract"],
            "evidence_refs": ["evidence:source-coverage"]},
    }))
    refreshed = call("refresh-state", *binding,
        "--classification", "covered_frontier_closeout", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(vision),
        "--progress-result-class", "no_followup", "--progress-coverage-scope-id", "source-coverage",
        "--progress-coverage-complete", "--progress-evidence-id", "evidence:source-coverage",
        "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    pending = _guard(call)
    assert pending["heartbeat_receipt"]["settlement_identity"] == identity
    assert pending["settlement_progress"]["state"] == "spend_required"
    assert pending.get("selected_todo") is None
    spent = call("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
    assert spent["appended"] is True
    assert spent["settlement_progress"]["state"] == "settled"
    assert call("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")["appended"] is False
    terminal = _guard(call, "turn-after-covered-vision-closeout")
    assert terminal["ok"] is True
    assert terminal["effective_action"] == "terminal_no_followup"
    assert terminal["should_run"] is False
    assert terminal.get("autonomous_replan_obligation") is None
    # A terminal execution frontier does not silently declare the Goal achieved.
    assert json.loads((tmp_path / "registry.json").read_text())["goals"][0]["status"] == "active"
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
