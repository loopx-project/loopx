"""Public reporting works in a lawful replan without launching or settling work."""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import loopx

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_replan_successor_durable_ack import history

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.goals.goal_vision import compact_goal_vision_packet, normalize_goal_vision_packet
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path


GOAL = "native-child-replan-fixture"
AGENT = "generic-coordinator"
TURN = "turn-native-replan"
TODO = "todo_source_validation"
ROOT = Path(loopx.__file__).resolve().parents[1]


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, todo_bound: bool):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n# Synthetic Goal\n\n## Agent Todo\n" + (
        "\n- [ ] [P1] Validate the original source.\n"
        f"  <!-- loopx:todo todo_id={TODO} status=open task_class=advancement_task "
        f"claimed_by={AGENT} action_kind=validate validation_command=pytest "
        "continuation_policy=same_agent_non_delivery "
        "required_capabilities=shell%2Cfilesystem_read -->\n"
        if todo_bound else ""
    ))
    index = runtime / "goals" / GOAL / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    evidence, report = index.parent / "source.json", index.parent / "source.md"
    evidence.write_text(json.dumps({"ok": True, "fixture": "public-safe-native-replan"}))
    report.write_text("# Synthetic source audit\n")
    runs = history()
    for row in runs:
        row.update(agent_id=AGENT, json_path=str(evidence), markdown_path=str(report))
    runs[0]["agent_vision"] = compact_goal_vision_packet(normalize_goal_vision_packet({
        "goal_id": GOAL, "agent_id": AGENT, "state": "vision_drift_detected",
        "vision_patch": {"acceptance_summary": "Independently validate the source.",
                         "advancement_policy": "repeat_until_closed"},
    }, goal_id=GOAL, agent_id=AGENT))
    index.write_text("".join(json.dumps(row) + "\n" for row in reversed(runs)))
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "status": "active", "repo": str(project), "state_file": state.name,
        "domain": "synthetic-replan",
        "adapter": {"kind": "fixture_connected_delivery_v0", "status": "connected-delivery"},
        "quota": {"compute": 1.0, "window_hours": 24},
        "spawn_policy": {"mode": "multi_subagent", "allowed": True, "max_children": 6},
        "coordination": {"agent_model": "peer_v1", "registered_agents": [AGENT]},
    }]}))
    todos = parse_active_state_todos(state.read_text(), item_limit=None)["agent_todos"]["items"]
    initialize_canonical_authority(runtime, GOAL, build_todo_runtime_shadow_projection(
        goal_id=GOAL, todos=todos, handoff_mode="soft_claim", leases=[],
    ), state_path=state, provider=provider)

    def call(*args: str, expected_code: int = 0) -> dict:
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", *args], cwd=ROOT,
            capture_output=True, text=True, timeout=60)
        assert result.returncode == expected_code, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    return call, runtime, index


def _admitted_guard(call, todo_bound: bool) -> dict:
    guard_args = ("quota", "should-run", "--codex-app", "--goal-id", GOAL,
                  "--agent-id", AGENT, "--turn-instance-id", TURN)
    guard = call(*guard_args)
    if todo_bound:
        # The planning recommendation has no settlement authority. An explicit
        # choice may be retained during hard replan and bound only on reentry.
        assert "settlement_identity" not in guard["heartbeat_receipt"]
        rejected = call("native-child", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", TURN, "record", "--operation-id", "op-before-choice",
            "--stage", "decision", "--operation", "spawn", "--outcome", "started",
            "--entrypoint-id", "generic_host", "--execute", expected_code=1)
        assert "admitted" in rejected["error"]
        deferred = call(*guard_args, "--todo-id", TODO, expected_code=1)
        assert deferred["action_selection_qualification"]["state"] == "deferred"
        assert "settlement_identity" not in deferred["heartbeat_receipt"]
        [reentry] = deferred["interaction_contract"]["cli_channel"]["next_cli_actions"]
        guard = call(*shlex.split(reentry)[1:])
        assert guard["heartbeat_receipt"]["pending_action_selection"]["settlement_bound"] is True
        assert guard["retained_action_selection"]["disposition"] == "preserve_retained_todo"
    return guard


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("todo_bound", [True, False])
def test_legal_replan_reports_native_child_without_settling_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, todo_bound: bool,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, todo_bound)
    guard = _admitted_guard(call, todo_bound)
    assert guard["decision"] == "autonomous_replan_required", guard
    identity = guard["heartbeat_receipt"]["settlement_identity"]
    assert identity.get("todo_id") == (TODO if todo_bound else None)
    assert bool(identity.get("replan_obligation_id")) is not todo_bound
    assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    original_index = index.read_bytes()
    base = ("native-child", "--goal-id", GOAL, "--agent-id", AGENT,
            "--turn-instance-id", TURN)
    decision = (*base, "record", "--operation-id", "op-independent-source",
                "--stage", "decision", "--operation", "spawn", "--outcome", "started",
                "--entrypoint-id", "generic_host", "--execute")
    first = call(*decision)
    assert first["appended"] is True
    replay = call(*decision)
    assert replay["appended"] is False
    assert replay["receipt"]["event_id"] == first["receipt"]["event_id"]
    call(*base, "record", "--operation-id", "op-independent-source",
         "--stage", "result", "--outcome", "completed", "--execute")
    reviewed = call(*base, "record", "--operation-id", "op-independent-source",
        "--stage", "review", "--outcome", "accepted", "--evidence-ref", "evidence-source",
        "--validation-ref", "validation-source", "--execute")
    readback = call(*base, "read")["native_child_activity"]
    assert readback == reviewed["native_child_activity"]
    assert readback["observation"] == "coordinator_reported"
    assert readback["host_attested"] is False
    assert readback["parent_accepted_count"] == 1
    assert readback["quota_spend_slots"] == 0
    context = call("agent-context", "--goal-id", GOAL, "--agent-id", AGENT,
                   "--phase", "after_delegate_result", "--turn-instance-id", TURN)
    assert context["native_child_activity"] == readback
    assert context["host_receipts_observed"] is False
    assert index.read_bytes() == original_index
    events = load_rollout_events(rollout_event_log_path(runtime, GOAL))
    assert sum(row["event_kind"] == "native_child_decision" for row in events) == 1
    assert not any(row["event_kind"] in {"refresh_state", "quota_spend"} for row in events)
