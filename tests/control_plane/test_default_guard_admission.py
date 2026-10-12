"""First automatic admission, selection and gates on isolated owning stores."""
from __future__ import annotations

import shlex
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from tests.control_plane.test_cli_output_budget import (
    AGENT_IDS, GOAL_ID, SCENARIOS, Scenario, _write_fixture, _invoke_cli,
)
from tests.control_plane.test_quota_settlement_cli import _run_cli, _spend_run_count


def _fixture(provider, tmp_path, monkeypatch, *, crowded=False, gated=False, peer=False, replan=False):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    scenario = Scenario("two-peers", todo_count=2, agent_count=2, run_count=1) if peer else SCENARIOS[1] if replan else (
        Scenario("ordinary-selection", todo_count=6, agent_count=1, run_count=1)
        if crowded else SCENARIOS[0])
    project, runtime, registry, state = _write_fixture(tmp_path, scenario)
    if gated:
        state.write_text(state.read_text(encoding="utf-8").replace("## Agent Todo",
            "## User Todo\n\n- [ ] [P0-user] Approve this scoped delivery.\n"
            "  <!-- loopx:todo todo_id=todo_user_gate_001 status=open task_class=user_gate "
            "action_kind=approve_output_release blocks_agent=codex-alpha "
            "decision_scope=release:action:quota-output priority=P0-USER -->\n\n## Agent Todo"), encoding="utf-8")
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"), item_limit=None)
    assert len(todos["agent_todos"]["items"]) == scenario.todo_count
    if provider != "legacy":
        initialize_canonical_authority(runtime, GOAL_ID,
            build_todo_runtime_shadow_projection(goal_id=GOAL_ID,
                todos=todos["agent_todos"]["items"] + todos.get("user_todos", {}).get("items", []), handoff_mode="soft_claim"),
            state_path=state, provider=provider)
    args = ("quota", "should-run", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_IDS[0], "--scan-root", str(project))
    return project, runtime, registry, state, args


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("selector", [(), ("--include-detail", "agent-todos"),
    ("--include-detail", "user-todos"), ("--include-detail", "goal-boundary"),
    ("--include-detail", "scheduler"), ("--include-detail", "vision"),
    ("--include-scheduler-detail",), ("--include-detail", "all")])
def test_first_detail_selector_admits_and_cold_replays_original_work(provider, selector, tmp_path, monkeypatch):
    project, runtime, registry, _, args = _fixture(provider, tmp_path, monkeypatch)
    code, first = _run_cli(registry, runtime, *args, *selector, cwd=project)
    assert code == 0 and first["should_run"] is True, first
    context = first["delivery_result_context"]
    identity = context["settlement_identity"]
    assert identity["goal_id"] == GOAL_ID and identity["agent_id"] == AGENT_IDS[0]
    assert identity["todo_id"] == context["basis"]["todo"]["todo_id"] == "todo_fixture_000"
    assert first["heartbeat_receipt"]["turn_instance_id"] == identity["turn_instance_id"]
    assert first["work_input_restored"] is False
    # _run_cli creates a new process: no inherited caller state or copied token.
    code, restored = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and restored["delivery_result_context"] == context, restored
    assert restored["work_input_restored"] is True
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("replan", [False, True])
def test_unbound_concurrent_reads_return_selectable_identities_then_bind_exact_turn(provider, replan, tmp_path, monkeypatch):
    project, runtime, registry, _, args = _fixture(provider, tmp_path, monkeypatch, crowded=True, replan=replan)
    if replan:
        from loopx.todos import list_goal_todos
        owned = list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime),
            goal_id=GOAL_ID, role="agent", limit=None)
        items = owned["todos"]
        assert {item["todo_id"] for item in items} == {f"todo_fixture_{i:03}" for i in range(36)}
        assert all(item["status"] == "open" and item["task_class"] == "advancement_task"
                   and item["claimed_by"] == AGENT_IDS[0] for item in items)
    barrier = Barrier(2)
    def read():
        barrier.wait()
        return _run_cli(registry, runtime, *args, cwd=project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: read(), range(2)))
    ids = []
    for code, payload in results:
        assert code == 0, payload
        receipt = payload["heartbeat_receipt"]
        assert receipt["settlement_binding_owed"] is True
        assert "settlement_identity" not in receipt and "delivery_result_context" not in payload
        selection = payload["interaction_contract"]["cli_channel"]["selection_command"]
        assert receipt["turn_instance_id"] in selection["command_args_template"]
        ids.append(receipt["turn_instance_id"])
    assert len(set(ids)) == 2, "Unselected observations are not one admitted Todo work"
    assert _spend_run_count(runtime) == 0
    # Use the returned exact selection command, not a test-created replacement ID.
    selection = results[0][1]["interaction_contract"]["cli_channel"]["selection_command"]
    selected_args = shlex.split(selection["command_args_template"].replace("{todo_id}", "todo_fixture_000"))
    code, selected = _run_cli(registry, runtime, *selected_args, "--scan-root", str(project), cwd=project)
    assert code == 0, json.dumps(selected, indent=2)
    identity = selected["heartbeat_receipt"]["settlement_identity"]
    assert identity["turn_instance_id"] == ids[0] and identity["todo_id"] == "todo_fixture_000"
    if replan:
        assert selected["normal_delivery_allowed"] is False
        assert selected["interaction_contract"]["mode"] == "autonomous_replan"
    # Read evidence is not normal-delivery permission. A required replan also
    # protects any later result on this newly admitted Todo identity.
    assert selected["delivery_result_context"]["settlement_identity"] == identity
    code, replay = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and replay["heartbeat_receipt"]["settlement_identity"] == identity, replay
    assert replay.get("delivery_result_context") == selected.get("delivery_result_context")
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_user_gate_receipt_does_not_authorize_delivery_or_spend(provider, tmp_path, monkeypatch):
    project, runtime, registry, _, args = _fixture(provider, tmp_path, monkeypatch, gated=True)
    for selector in [(), ("--include-detail", "all")]:
        code, guarded = _run_cli(registry, runtime, *args, *selector, cwd=project)
        assert code == 0 and guarded["should_run"] is False, guarded
        channel = guarded["interaction_contract"]
        assert channel["agent_channel"]["delivery_allowed"] is False
        assert channel["cli_channel"]["spend_allowed_now"] is False
        receipt = guarded["heartbeat_receipt"]
        code, rejected = _run_cli(registry, runtime, "quota", "spend-slot", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_IDS[0], "--todo-id", "todo_fixture_000",
            "--turn-instance-id", receipt["turn_instance_id"], "--slots", "1",
            "--source", "heartbeat", "--execute", "--scan-root", str(project), cwd=project)
        assert code == 1, rejected
        assert _spend_run_count(runtime) == 0
    assert not list((runtime / "goals" / GOAL_ID / "turns").glob("*.json"))


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_another_agent_cannot_restore_the_original_work(provider, tmp_path, monkeypatch):
    project, runtime, registry, _, args = _fixture(provider, tmp_path, monkeypatch, peer=True)
    code, first = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0, first
    peer_args = tuple(AGENT_IDS[1] if value == AGENT_IDS[0] else value for value in args)
    code, peer = _run_cli(registry, runtime, *peer_args, cwd=project)
    assert code == 0 and peer["should_run"] is True, peer
    assert peer["delivery_result_context"]["basis"]["todo"]["todo_id"] == "todo_fixture_001"
    assert peer.get("delivery_result_context") != first["delivery_result_context"]
    if "settlement_identity" in peer.get("heartbeat_receipt", {}):
        assert peer["heartbeat_receipt"]["settlement_identity"]["agent_id"] == AGENT_IDS[1]
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_admission_policy_failure_cannot_bind_an_unbound_turn(provider, tmp_path, monkeypatch):
    project, runtime, registry, _, args = _fixture(provider, tmp_path, monkeypatch, crowded=True)
    code, first = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and first["heartbeat_receipt"]["settlement_binding_owed"] is True, first
    selection = first["interaction_contract"]["cli_channel"]["selection_command"]
    log = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    original = log.read_bytes()
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Result work admission source unavailable")
    monkeypatch.setattr("loopx.control_plane.goals.checkpoint_context_io.default_result_freshness_policy", unavailable)
    command = shlex.split(selection["route_prefix"] + " " +
        selection["command_args_template"].replace("{todo_id}", "todo_fixture_000"))[1:]
    code, text = _invoke_cli(["--format", "json", *command, "--scan-root", str(project)])
    rejected = json.loads(text)
    assert code == 1 and rejected["should_run"] is False, rejected
    assert rejected["ok"] is False and rejected["recommended_action"]
    assert "delivery_result_context" not in rejected
    assert "settlement_identity" not in rejected.get("heartbeat_receipt", {})
    assert log.read_bytes() == original and _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["legacy"])
def test_malformed_first_read_fails_closed_then_repairs_without_moving_tokens(provider, tmp_path, monkeypatch):
    project, runtime, registry, state, args = _fixture(provider, tmp_path, monkeypatch)
    original = state.read_bytes()
    state.write_text(state.read_text(encoding="utf-8").replace("status=open", "status=unsupported"), encoding="utf-8")
    code, failed = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 1 and failed["status_health_ok"] is False and failed["should_run"] is False, failed
    assert failed["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert "delivery_result_context" not in failed and _spend_run_count(runtime) == 0
    state.write_bytes(original)
    code, admitted = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and admitted["delivery_result_context"]["basis"]["todo"]["todo_id"] == "todo_fixture_000", admitted
    code, restored = _run_cli(registry, runtime, *args, cwd=project)
    assert code == 0 and restored["delivery_result_context"] == admitted["delivery_result_context"], restored
    assert _spend_run_count(runtime) == 0
