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


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n# Synthetic Goal\n\n## Agent Todo\n")
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
        assert result.returncode == expected_code, (payload.get("error"), payload.get("reason"))
        return payload

    return call, runtime, index


def _guard(call, turn: str = TURN):
    return call("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                "--agent-id", AGENT, "--turn-instance-id", turn,
                "--codex-app-current-rrule", "FREQ=MINUTELY;INTERVAL=3")


def _add(call, obligation_id: str):
    return call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently verify the source artifact", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-source-artifact",
        "--operation-id", "periodic-source-successor", "--replan-obligation-id", obligation_id)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_successor_guard_returns_original_settlement_not_repeated_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider)
    original = _guard(call)
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
def test_invalidated_successor_cannot_rebind_or_close_original_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, invalidation: str,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider)
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
    guarded = _guard(call)
    assert guarded["heartbeat_receipt"]["settlement_identity"] == identity
    assert guarded.get("selected_todo") is None
    assert guarded["autonomous_replan_obligation"].get("resolution_mode") != "receipt_bound_replan_settlement"
    assert guarded["replan_action_packet"].get("settlement_only") is not True
    denied = call(*shlex.split(prior_actions[0])[1:], "--no-global-sync",
                  "--suppress-external-sinks", expected_code=1)
    assert denied.get("appended") is not True
    denied_spend = call(*shlex.split(prior_actions[1])[1:], expected_code=1)
    assert denied_spend.get("appended") is not True
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)
