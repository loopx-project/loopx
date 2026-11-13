"""A deferred selection must expose a runnable recovery before settlement."""
import json
from pathlib import Path
import shlex
import subprocess

import pytest
from canonical_authority_fixture import advance_canonical_fixture_authority, read_fixture_todos
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, TODO_ID,
    _configure_autonomous_replan_fixture, _configure_selected_todo_replan_fixture,
    _configure_selectable_alternative, _heartbeat_receipt_count,
    _initialize_fixture_authority, _initialize_git_checkout, _projected_cli_args, _run_cli, _run_generated_cli,
    _spend_run_count, _write_fixture,
)


def _advance_frontier(project, runtime, operation_id):
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    advance_canonical_fixture_authority(
        runtime, GOAL_ID, build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=read_fixture_todos(state), handoff_mode="soft_claim",
        ), operation_id=operation_id,
    )


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_mcp_replan_reentry_reuses_turn_start_observation(tmp_path, monkeypatch, provider):
    import asyncio
    import os
    import sys

    import loopx
    from loopx.claude_goal_mode.scripts.goalmode_cmd import write_loop_md
    from loopx.control_plane.host_adapter_settlement import (
        HostTodoSettlementRequest, host_adapter_turn_instance_id,
    )
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.host_process_fixture import write_python_host_fixture

    pytest.importorskip("mcp.server.fastmcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_git_checkout(project)
    (project / ".gitignore").write_text(".loopx/\n")
    _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
    _initialize_fixture_authority(project, runtime, provider=provider)
    write_loop_md(project, GOAL_ID, AGENT_ID)
    code, installed = _run_cli(registry, runtime, "extension", "install",
                               "--bundled", "loopx-lark", "--execute")
    assert code == 0, installed

    registered = json.loads(registry.read_text())
    registered["goals"][0].setdefault("control_plane", {})["lark_event_inbox"] = {
        "enabled": True, "config_path": ".loopx/collector.json",
    }
    registry.write_text(json.dumps(registered))
    (project / ".loopx/collector.json").write_text(json.dumps({
        "schema_version": "lark_event_collector_config_v1",
        "enabled": True, "service_name": "loopx-fixture-inbox", "supervisor": "systemd",
        "consume_timeout": "30m", "profile": "fixture-bot", "turn_start_sync": {"enabled": True},
        "routes": [{"route_key": "requirements", "chat_id": "oc_fixture",
                    "event_inbox_config": ".loopx/inbox.json"}],
    }))
    (project / ".loopx/inbox.json").write_text(json.dumps({
        "schema_version": "lark_event_inbox_config_v0", "enabled": True,
        "inbox_dir": ".loopx/inbox/requirements", "capture_scope": "configured_chat_all",
        "material_review": {"enabled": True, "drain_limit": 20}, "reply": {"enabled": False},
    }))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "provider-calls"
    write_python_host_fixture(bin_dir / "lark-cli", f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

assert "+chat-messages-list" in sys.argv
calls = Path({str(calls)!r})
previous = calls.exists()
with calls.open("a", encoding="utf-8") as log:
    log.write("history\\n")
if previous:
    print(json.dumps({{"ok": False, "error": {{"code": 429}}}}))
    sys.exit(1)
print(json.dumps({{"ok": True, "data": {{"messages": [], "has_more": False}}}}))
""")
    # Keep both the stdio tool and the same-package quota subprocess real.
    launcher = """
import sys
from loopx.claude_goal_mode.mcp.loopx_mcp import control, mcp

run_cli = control.run_cli
control.run_cli = lambda args: run_cli(["--runtime-root", sys.argv[1], *args])
mcp.run()
"""
    params = StdioServerParameters(
        command=sys.executable, args=["-c", launcher, str(runtime)], cwd=str(project),
        env={"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", os.defpath),
             "PYTHONPATH": str(Path(loopx.__file__).resolve().parents[1]),
             "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path),
             "NODE_OPTIONS": "--experimental-sqlite", "LOOPX_USAGE_PING": "0"},
    )

    async def exercise():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for count, arguments, expected_status in (
                    (1, {"todo_id": SELECTED_REPLAN_TODO_ID}, "empty"),
                    (2, {}, "unavailable"),
                ):
                    response = await session.call_tool("should_run", arguments)
                    assert not response.isError, response.content
                    decision = json.loads(response.structuredContent["result"])
                    assert decision["effective_action"] == "autonomous_replan_required"
                    assert not decision["normal_delivery_allowed"]
                    assert "heartbeat_receipt" not in decision
                    dispatch = decision["turn_start_capability_hook_dispatch"]
                    results = [item for item in dispatch["results"]
                               if item["hook_id"] == "lark.turn_start_inbox_sync"]
                    assert len(results) == 1, dispatch
                    result, = results
                    assert (calls.read_text().splitlines(), result["status"]) == (
                        ["history"] * count, expected_status,
                    )
                    assert result["external_reads_performed"] is True

    asyncio.run(exercise())
    turn_id = host_adapter_turn_instance_id(HostTodoSettlementRequest(
        goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=SELECTED_REPLAN_TODO_ID,
        runtime_profile="claude_code", completion_args=(),
    ))
    assert _heartbeat_receipt_count(runtime, turn_id) == 0
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize(("binding", "initial_replan"), [
    ("todo", False), ("autonomous_replan", False), ("todo", True),
])
def test_replan_selection_recovers_inline_or_refuses_ineligible_choice_and_settles_once(tmp_path, binding, initial_replan):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    if initial_replan:
        _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    turn = "turn-selection-preempted"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn, )
    rc, first = _run_cli(registry, runtime, *guard)
    assert rc == 0, first
    assert first["decision"] == ("autonomous_replan_required" if initial_replan else "run")
    assert first["interaction_contract"]["cli_channel"]["selection_required"]
    assert "settlement_identity" not in first["heartbeat_receipt"]
    if initial_replan:
        # This test exercises selection recovery from an existing replan.
        # Periodic review has its own settlement-qualified coverage; these
        # setup runs are not settled Turns and must not trigger its cadence.
        assert "long_todo_chain" in {
            trigger["kind"] for trigger in first["autonomous_replan_obligation"]["triggers"]
        }
    if binding == "todo":
        _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
        selected_id = SELECTED_REPLAN_TODO_ID
    else:
        _configure_autonomous_replan_fixture(project, runtime, registry, stride_strategy="off")
        selected_id = TODO_ID
    _advance_frontier(project, runtime, "fixture-frontier-after-selection")
    rc, selected = _run_cli(registry, runtime, *guard, "--todo-id", selected_id)
    selection_deferred = binding == "todo"
    expected_before_resume = 2 if selection_deferred else 1
    if selection_deferred:
        # The CLI retains the choice and performs exactly one fresh admission.
        assert rc == 0 and selected["should_run"], selected
        assert selected["interaction_contract"]["mode"] == "autonomous_replan"
        assert selected["normal_delivery_allowed"] is False
        resumed = selected
    else:
        # A candidate removed by the changed frontier is still a caller-visible
        # rejection, with no receipt mutation or automatic retry.
        assert rc == 1 and not selected["should_run"], selected
        assert selected["action_selection_qualification"]["state"] == "rejected"
        assert selected["heartbeat_receipt"]["event_id"] == first["heartbeat_receipt"]["event_id"]
        channel = selected["interaction_contract"]["cli_channel"]
        assert "settlement_plan" not in channel
        assert "replan_settlement_contract" not in channel
        [command] = channel["next_cli_actions"]
        argv = shlex.split(command)
        assert argv[argv.index("--turn-instance-id") + 1] == turn
        assert "--todo-id" not in argv and "--replan-obligation-id" not in argv
        assert _heartbeat_receipt_count(runtime, turn) == 1
        rc, resumed = _run_generated_cli(
            shlex.join([*argv, "--context-delivery", "off"]), registry_path=registry,
        )
        assert rc == 0, resumed
    receipt = resumed["heartbeat_receipt"]
    assert receipt["status"] == "upgraded"
    identity = receipt["settlement_identity"]
    assert identity["turn_instance_id"] == turn
    assert ("todo_id" in identity) == (binding == "todo")
    if selection_deferred:
        assert receipt["pending_action_selection"]["todo_id"] == selected_id
        assert receipt["pending_action_selection"]["settlement_bound"] is (
            binding == "todo"
        )
    else:
        assert "pending_action_selection" not in receipt
    expected_after_resume = expected_before_resume + 1
    assert _heartbeat_receipt_count(runtime, turn) == expected_after_resume
    cli = resumed["interaction_contract"]["cli_channel"]
    assert cli["settlement_plan"]["identity"] == identity
    if binding == "todo":
        semantic_id = receipt["semantic_replan_obligation_id"]
        assert cli["replan_settlement_contract"]["settlement_binding"] == {
            "kind": "todo", "id": selected_id, "cli_argument": "--todo-id",
        }
        assert cli["replan_settlement_contract"]["semantic_obligation"] == {
            "kind": "autonomous_replan", "id": semantic_id,
            "settlement_bound": False, "discharge": "todo_bound_writeback",
        }
        # Recovery before any writeback must keep the same causal Todo and
        # semantic review. Re-reading a receipt grants no replacement identity.
        rc, replay = _run_cli(registry, runtime, *guard, "--todo-id", selected_id)
        assert rc == 0 and replay["should_run"], replay
        assert replay["selected_todo"]["todo_id"] == selected_id
        assert replay["heartbeat_receipt"]["settlement_identity"] == identity
        replay_cli = replay["interaction_contract"]["cli_channel"]
        assert replay_cli["settlement_plan"]["identity"] == identity
        assert replay_cli["replan_settlement_contract"] == cli["replan_settlement_contract"]
        assert _heartbeat_receipt_count(runtime, turn) == expected_after_resume
    refresh = next(c for c in cli["next_cli_actions"] if "refresh-state" in c)
    if binding == "todo":
        decision = tmp_path / "selection-replan-vision.json"
        decision.write_text(
            json.dumps(
                {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "state": "vision_patch_proposed",
                    "vision_patch": {
                        "vision_summary": (
                            "Validate the existing bounded slices in dependency order."
                        ),
                        "acceptance_summary": (
                            "Each slice has independent validation before dependent "
                            "work proceeds."
                        ),
                        "advancement_policy": "as_needed",
                    },
                    "path_delta": {
                        "schema_version": "goal_path_delta_v0",
                        "outcome": "replan",
                        "prior_assumption": (
                            "The long chain needed a bounded review."
                        ),
                        "observed_reality": (
                            "The reviewed chain has a runnable validation slice."
                        ),
                        "retained": ["Existing acceptance boundaries"],
                        "changed": ["Proceed with the first validation slice"],
                        "evidence_refs": ["evidence:selection-replan"],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        refresh = refresh.replace(
            "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>",
            str(decision),
        )
    for key, value in {"<advanced|blocked|exploration_exhausted|no_followup>": "advanced",
                       "<surface-id>": "accepted-artifact", "<hypothesis-id>": "adoption",
                       "<probe-kind>": "acceptance", "<evidence-id>": "evidence:readback"}.items():
        refresh = refresh.replace(key, value)
    rc, result = _run_cli(registry, runtime, *_projected_cli_args(refresh, turn_instance_id=turn), cwd=project)
    assert rc == 0, result
    assert result["settlement_result"]["ok"]
    spend = next(c for c in cli["next_cli_actions"] if "spend-slot" in c)
    spend_args = _projected_cli_args(spend, turn_instance_id=turn)
    for replay in (False, True):
        rc, result = _run_cli(registry, runtime, *spend_args,  cwd=project)
        assert rc == 0, result
        assert result["settlement_result"]["ok"]
        if replay:
            assert result["idempotent_replay"] and not result["appended"]
    assert _spend_run_count(runtime) == 1
    rc, settled = _run_cli(registry, runtime, *guard)
    assert rc == 0
    if binding == "autonomous_replan":
        assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled["heartbeat_receipt"]["settlement_identity"] == identity
    rc, conflict = _run_cli(registry, runtime, *guard, "--todo-id", "todo_another_selection")
    assert rc == 1 and conflict["ok"] is False
    if binding == "autonomous_replan":
        facts = conflict["action_selection_conflict"]
        assert facts["receipt_replan_obligation_id"] == identity[
            "replan_obligation_id"
        ]
        if selection_deferred:
            assert facts["retained_selection"] is True
            assert facts["retained_selection_todo_id"] == selected_id
        else:
            assert "retained_selection" not in facts
            assert "retained_selection_todo_id" not in facts
            assert facts["qualification_state"] is None
            assert "no pending Todo selection" in conflict["reason"]
            assert "selection the Turn retains" not in conflict["recommended_action"]
    assert _heartbeat_receipt_count(runtime, turn) == expected_after_resume
    assert _spend_run_count(runtime) == 1


@pytest.mark.parametrize(("initial_replan", "discovery"), [(False, True), (True, True), (True, False)])
def test_reentry_never_replaces_retained_selection_with_recommended_todo(tmp_path, initial_replan, discovery):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    if initial_replan:
        _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    turn = "turn-selection-recommendation-drift"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn, )
    if discovery:
        rc, first = _run_cli(registry, runtime, *guard)
        assert rc == 0, first
        assert first["interaction_contract"]["cli_channel"]["selection_required"]
        assert "settlement_identity" not in first["heartbeat_receipt"]
    rc, preferences = _run_cli(
        registry, runtime, "semantic-preference", "agent", "read",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
    )
    assert rc == 0, preferences
    assert _heartbeat_receipt_count(runtime, turn) == int(discovery)

    # The original explicit choice leaves the refreshed frontier while a
    # different recommended Todo becomes visible under a hard replan.
    _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
    _advance_frontier(project, runtime, "fixture-recommendation-drift")
    retained_todo_id = "todo_chain_000000000001"
    rc, resumed = _run_cli(
        registry, runtime, *guard, "--todo-id", retained_todo_id
    )
    assert rc == 0, resumed
    assert resumed["normal_delivery_allowed"] is False
    assert resumed["decision"] == "autonomous_replan_required"
    assert resumed.get("selected_todo") is None
    retained = resumed["retained_action_selection"]
    assert retained["disposition"] == "bind_autonomous_replan"
    assert retained["retained_todo_id"] == retained_todo_id
    assert retained["projected_todo_id"] == SELECTED_REPLAN_TODO_ID
    identity = resumed["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    assert "todo_id" not in identity
    assert resumed["heartbeat_receipt"]["pending_action_selection"] == {
        "todo_id": retained_todo_id,
        "state": "deferred_to_fresh_turn",
        "reason": "autonomous_replan_preemption",
        "settlement_bound": False,
    }
    assert _heartbeat_receipt_count(runtime, turn) == (3 if discovery else 1)
    plan = resumed["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["identity"] == identity
    assert all(
        "--replan-obligation-id" in action and "--todo-id" not in action
        for action in resumed["interaction_contract"]["cli_channel"]["next_cli_actions"]
    )
    actions = resumed["interaction_contract"]["cli_channel"]["next_cli_actions"]
    refresh = next(c for c in actions if "refresh-state" in c)
    decision = tmp_path / "recommendation-drift-replan.json"
    decision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0",
        "state": "vision_patch_proposed",
        "vision_patch": {
            "vision_summary": "Review the chain before selecting the next delivery.",
            "acceptance_summary": "Independent validation still precedes dependent work.",
            "advancement_policy": "as_needed",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "replan",
            "prior_assumption": "A projected recommendation represented the explicit choice.",
            "observed_reality": "The explicit choice and recommendation are different Todos.",
            "retained": ["Existing acceptance and delivery boundaries"],
            "changed": ["Complete the review before a fresh delivery Turn"],
            "evidence_refs": ["evidence:recommendation-drift"],
        },
    }) + "\n", encoding="utf-8")
    refresh = refresh.replace("<path-to-evidence-linked-goal-vision-replan-contract-v0.json>", str(decision))
    for key, value in {"<advanced|blocked|exploration_exhausted|no_followup>": "advanced",
                       "<surface-id>": "accepted-artifact", "<hypothesis-id>": "adoption",
                       "<probe-kind>": "acceptance", "<evidence-id>": "evidence:readback"}.items():
        refresh = refresh.replace(key, value)
    rc, result = _run_cli(registry, runtime, *_projected_cli_args(refresh, turn_instance_id=turn), cwd=project)
    assert rc == 0 and result["settlement_result"]["ok"], json.dumps(result, indent=2)
    spend = next(c for c in actions if "spend-slot" in c)
    for replay in (False, True):
        rc, result = _run_cli(registry, runtime, *_projected_cli_args(spend, turn_instance_id=turn), cwd=project)
        assert rc == 0 and result["settlement_result"]["ok"], result
        if replay:
            assert result["idempotent_replay"] and not result["appended"]
    assert _spend_run_count(runtime) == 1
    rc, conflict = _run_cli(
        registry, runtime, *guard, "--todo-id", SELECTED_REPLAN_TODO_ID
    )
    assert rc == 1, conflict
    facts = conflict["action_selection_conflict"]
    assert facts["retained_selection"] is True
    assert facts["retained_selection_todo_id"] == retained_todo_id
    assert facts["receipt_replan_obligation_id"] == identity["replan_obligation_id"]


@pytest.mark.parametrize("write_scope", [["src/**"], []], ids=["covered", "missing"])
def test_replan_preempts_unselected_todo_guards_on_selection_reentry(tmp_path, write_scope):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(
        state.read_text(encoding="utf-8").replace(
            "task_class=advancement_task action_kind=validate ",
            "task_class=advancement_task action_kind=implement "
            "task_repository=git:github.com/example/product "
            "required_write_scopes=src/** ",
        ),
        encoding="utf-8",
    )
    registry_data = json.loads(registry.read_text(encoding="utf-8"))
    registry_data["goals"][0]["coordination"]["registered_agents"].append(
        "codex-peer"
    )
    registry_data["goals"][0]["coordination"]["write_scope"] = write_scope
    registry.write_text(json.dumps(registry_data) + "\n", encoding="utf-8")
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    _initialize_git_checkout(project)

    turn = "turn-selection-replan-workspace-guard"
    guard = (
        "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", turn,
    )
    rc, first = _run_cli(registry, runtime, *guard, cwd=project)
    assert rc == 0, first
    assert first["decision"] == "autonomous_replan_required"
    assert "workspace_guard" not in first
    assert "boundary_projection_gap" not in first
    assert first["self_repair_allowed"] is False

    retained_todo_id = "todo_chain_000000000001"
    rc, resumed = _run_cli(
        registry, runtime, *guard, "--todo-id", retained_todo_id, cwd=project
    )

    assert rc == 0, resumed
    assert resumed["decision"] == "autonomous_replan_required"
    assert resumed["effective_action"] == "autonomous_replan_required"
    assert "workspace_guard" not in resumed
    assert "boundary_projection_gap" not in resumed
    assert resumed["self_repair_allowed"] is False
    assert resumed.get("selected_todo") is None
    assert resumed["retained_action_selection"]["disposition"] == (
        "bind_autonomous_replan"
    )
    assert resumed["heartbeat_receipt"]["pending_action_selection"] == {
        "todo_id": retained_todo_id,
        "state": "deferred_to_fresh_turn",
        "reason": "autonomous_replan_preemption",
        "settlement_bound": False,
    }
    identity = resumed["heartbeat_receipt"]["settlement_identity"]
    assert identity["binding_kind"] == "autonomous_replan"
    assert "todo_id" not in identity


@pytest.mark.parametrize("changed_gate", ["user_gate", "quota"])
@pytest.mark.parametrize(("inline", "discovery"), [(True, True), (False, True), (True, False)])
def test_inline_reentry_reads_gates_changed_after_choice_receipt(
    tmp_path, monkeypatch, capsys, changed_gate, inline, discovery,
):
    from loopx.cli import main
    from loopx.cli_commands import quota as quota_cli
    from test_quota_settlement_cli import _append_blocking_user_gate

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    turn = "turn-inline-reentry-new-gate"
    guard = ("quota", "should-run", "--context-delivery", "off", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn,
             )
    if discovery:
        rc, first = _run_cli(registry, runtime, *guard)
        assert rc == 0 and first["interaction_contract"]["cli_channel"]["selection_required"]
    original = quota_cli.reconcile_requested_quota_action_selection
    calls = []

    def reconcile_then_change_gate(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(kwargs["selection"].requested_todo_id)
        if len(calls) == 1:
            assert result.rejected
            assert result.receipt_status == ("selection_retained" if discovery else "replayed")
            if changed_gate == "user_gate":
                _append_blocking_user_gate(project, global_gate=True)
                _advance_frontier(project, runtime, "fixture-new-user-gate")
            else:
                data = json.loads(registry.read_text())
                data["goals"][0]["quota"]["compute"] = 0
                registry.write_text(json.dumps(data))
        return result

    monkeypatch.setattr(quota_cli, "reconcile_requested_quota_action_selection", reconcile_then_change_gate)
    if not inline:
        # Characterize the existing manual reentry through the same real guard.
        monkeypatch.setattr(quota_cli, "inline_action_selection_reentry_args", lambda *a, **kw: None)
    main(["--registry", str(registry), "--runtime-root", str(runtime),
          "--format", "json", *guard, "--todo-id", SELECTED_REPLAN_TODO_ID])
    result = json.loads(capsys.readouterr().out)
    if not inline:
        assert result["error_code"] == "quota_action_selection_deferred"
        main(["--registry", str(registry), "--runtime-root", str(runtime),
              "--format", "json", *guard])
        result = json.loads(capsys.readouterr().out)
    assert calls == [SELECTED_REPLAN_TODO_ID, None]
    assert result["should_run"] is False
    assert result["normal_delivery_allowed"] is False
    assert not result["interaction_contract"]["agent_channel"]["delivery_allowed"]
    assert result["heartbeat_receipt"].get("closeout_required") is not True
    assert result["heartbeat_receipt"]["pending_action_selection"]["todo_id"] == SELECTED_REPLAN_TODO_ID
    identity = result["heartbeat_receipt"].get("settlement_identity")
    if identity is not None:
        # A causal identity can persist under a denied gate; it is not admission.
        assert identity["todo_id"] == SELECTED_REPLAN_TODO_ID
    assert not result["interaction_contract"]["cli_channel"]["spend_allowed_now"]
    rc, spend = _run_cli(registry, runtime, "quota", "spend-slot",
                         "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                         "--todo-id", SELECTED_REPLAN_TODO_ID,
                         "--turn-instance-id", turn, "--source", "heartbeat",
                         "--slots", "1", "--execute", )
    assert rc == 1 and spend["ok"] is False, spend
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("discovery", [False, True])
@pytest.mark.parametrize("context_delivery", ["off", "on"])
def test_first_inline_result_has_matching_turn_envelope(tmp_path, discovery, context_delivery):
    from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    turn = "turn-inline-envelope"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn, "--context-delivery", context_delivery,
             )
    if discovery:
        rc, first = _run_cli(registry, runtime, *guard)
        first = read_cli_policy_payload(json.dumps(first), returncode=rc)
        assert rc == 0 and first["interaction_contract"]["cli_channel"]["selection_required"]
    requested_view = "--turn-envelope" if context_delivery == "off" else "--complete-turn"
    capture = tmp_path / "original-bound-capture"
    rc, envelope = _run_cli(registry, runtime, *guard,
                            "--todo-id", SELECTED_REPLAN_TODO_ID, requested_view,
                            "--decision-output-dir", str(capture))
    assert rc == 0, envelope
    from loopx.control_plane.quota.turn_envelope import _turn_envelope_result, build_turn_envelope
    if context_delivery == "on":
        shown = read_cli_policy_payload(json.dumps(envelope), returncode=rc)
        original = json.loads((capture / "decision.json").read_bytes())
        assert shown["heartbeat_receipt"] == original["heartbeat_receipt"]
        assert _turn_envelope_result("complete_consumer", payload=original)["ok"]
        envelope = build_turn_envelope(original)
    else:
        # Explicit off retains the requested bounded compatibility view.
        assert envelope["schema_version"] == "loopx_turn_envelope_v0"
    assert envelope["action_signature"]["matches"] is True
    contract = envelope["contract_capsule"]["interaction_contract"]
    assert contract["mode"] == "autonomous_replan"
    assert envelope["action"]["must_attempt"] is True
    assert envelope["contract_capsule"]["execution_obligation"]["kind"] == "autonomous_replan_required"
    binding = envelope["writeback"]["replan_settlement_contract"]["settlement_binding"]
    assert binding == {"kind": "todo", "id": SELECTED_REPLAN_TODO_ID, "cli_argument": "--todo-id"}
    assert envelope["writeback"]["spend_allowed_now"] is False
    assert _heartbeat_receipt_count(runtime, turn) == (3 if discovery else 1)
    assert _spend_run_count(runtime) == 0


def test_first_reentry_does_not_overwrite_concurrently_created_receipt(tmp_path, monkeypatch, capsys):
    from loopx.cli import main
    from loopx.cli_commands import quota as quota_cli

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    turn = "turn-first-selection-race"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn)
    original = quota_cli.reconcile_requested_quota_action_selection

    def concurrent_receipt(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.rejected and result.receipt is None
        rc, competing = _run_cli(registry, runtime, *guard)
        assert rc == 0 and competing["heartbeat_receipt"]["status"] == "committed"
        return result

    monkeypatch.setattr(quota_cli, "reconcile_requested_quota_action_selection", concurrent_receipt)
    main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
          *guard, "--todo-id", SELECTED_REPLAN_TODO_ID])
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "receipt appeared" in result["reason"]
    assert _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("complete", [False, True], ids=["ordinary", "complete"])
def test_native_first_selection_publishes_only_final_bound_capture(tmp_path, monkeypatch, provider, complete):
    from hashlib import sha256

    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "turn-native-first-selection"
    capture = tmp_path / "capture"
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "first-selection-session")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    codex_context_hook(str(native), dict(session_id="first-selection-session", cwd=str(project),
        hook_event_name="SessionStart", source="startup"))
    rc, presented = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn,
        "--todo-id", "todo_chain_000000000001", "--context-delivery", "on",
        "--codex-context-dir", str(native), "--decision-output-dir", str(capture),
        *(["--complete-turn"] if complete else []),
        "--max-page-output-bytes", "4096")
    assert rc == 0 and presented["ok"], presented
    assert len(json.dumps(presented, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 4096
    original_path = capture / "decision.json"
    original_bytes = original_path.read_bytes()
    original = json.loads(original_bytes)
    assert original.get("schema_version") == ("loopx_turn_envelope_v1" if complete else None)
    assert original["ok"] and original["effective_action"] == "autonomous_replan_required"
    assert original["heartbeat_receipt"]["status"] == "committed"
    assert original["heartbeat_receipt"]["settlement_identity"]["binding_kind"] == "autonomous_replan"
    assert original["heartbeat_receipt"]["pending_action_selection"]["todo_id"] == "todo_chain_000000000001"
    assert not original["interaction_contract"]["cli_channel"].get("selection_required")
    assert _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 0
    # The bounded reference reaches the original receipt without another
    # admission. Reading a selected section does not certify the whole input.
    from loopx.control_plane.turn_driver.decision_capture import (
        CONTEXT_CAPTURE_PAGE_GUIDANCE, read_context_read_reference,
    )

    reader = shlex.split(presented["recovery"]["command"])
    result = subprocess.run(reader, cwd=project, capture_output=True, text=True, check=True, timeout=60)
    first = json.loads(result.stdout)
    assert first["ok"] and first["offset"] == 0
    assert first["read_instruction"] == CONTEXT_CAPTURE_PAGE_GUIDANCE
    assert len(json.dumps(first, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 4096
    result = subprocess.run(
        [*reader, "--select", "/heartbeat_receipt"],
        cwd=project, capture_output=True, text=True, check=True, timeout=60,
    )
    read = json.loads(result.stdout)
    assert read["ok"] and read["eof"]
    # Native model output uses opaque reader references. Compare their complete
    # machine-owned source identity, not a digest omitted from the model view.
    _, original_reference = read_context_read_reference(presented["recovery"]["reference_file"])
    _, selected_reference = read_context_read_reference(read["reference_file"])
    assert selected_reference["path"] == original_reference["path"]
    assert selected_reference["sha256"] == original_reference["sha256"]
    saved_capture = Path(selected_reference["path"])
    saved_view = json.loads(saved_capture.read_bytes())
    assert saved_view["heartbeat_receipt"] == original["heartbeat_receipt"]
    assert saved_view.get("schema_version") == original.get("schema_version")
    # Either presentation may stage a separate view. The journal must still
    # retain the source digest and the read must preserve the committed receipt.
    assert json.loads((native / "session.json").read_bytes())["source_recovery"]["sha256"] == (
        sha256(original_bytes).hexdigest()
    )
    assert original_path.read_bytes() == original_bytes
    assert json.loads(read["content"])["selections"][0]["value"] == original["heartbeat_receipt"]
    # Identical JSON semantics with different original bytes still fail closed;
    # a reader failure cannot allocate another admission or spend.
    saved_capture.write_bytes(saved_capture.read_bytes() + b" ")
    refusal = subprocess.run(
        [*reader, "--select", "/heartbeat_receipt"],
        cwd=project, capture_output=True, text=True, check=False, timeout=60,
    )
    rejected = json.loads(refusal.stdout)
    assert refusal.returncode != 0 and rejected["ok"] is False and "content" not in rejected
    assert _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 0
