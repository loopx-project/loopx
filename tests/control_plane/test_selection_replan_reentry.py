"""A deferred selection must expose a runnable recovery before settlement."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
from canonical_authority_fixture import advance_canonical_fixture_authority, isolate_sqlite_runtime, read_fixture_todos
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


def _settle_replan_from_captured_actions(project, registry, runtime, turn, actions, identity):
    refresh = next(c for c in actions if "refresh-state" in c)
    decision = project.parent / "retained-selection-replan.json"
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
    assert result["settlement_identity"] == identity
    spend = next(c for c in actions if "spend-slot" in c)
    for replay in (False, True):
        rc, result = _run_cli(registry, runtime, *_projected_cli_args(spend, turn_instance_id=turn), cwd=project)
        assert rc == 0 and result["settlement_result"]["ok"], result
        if replay:
            assert result["idempotent_replay"] and not result["appended"]
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
    _settle_replan_from_captured_actions(
        project, registry, runtime, turn,
        resumed["interaction_contract"]["cli_channel"]["next_cli_actions"], identity,
    )
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
def test_first_inline_result_has_matching_turn_envelope(tmp_path, discovery):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    _initialize_fixture_authority(project, runtime, provider="sqlite")
    turn = "turn-inline-envelope"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn,
             )
    if discovery:
        rc, first = _run_cli(registry, runtime, *guard)
        assert rc == 0 and first["interaction_contract"]["cli_channel"]["selection_required"]
    rc, envelope = _run_cli(registry, runtime, *guard,
                            "--todo-id", SELECTED_REPLAN_TODO_ID, "--turn-envelope")
    assert rc == 0, envelope
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


def _read_recovery_on_limited_host(reader, fields, *, project, limit):
    """Run the emitted reader and every cursor under the original output cap."""
    data = bytearray()
    command = [*reader, *fields]
    for _ in range(256):
        result = subprocess.run(command, cwd=project, capture_output=True, timeout=30)
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert len(result.stdout) <= limit
        page = json.loads(result.stdout[:limit])
        assert page["ok"] and page["offset"] == len(data)
        data.extend(page["content"].encode())
        if page["eof"]:
            assert len(data) == page["total_bytes"]
            return json.loads(data)
        assert page["next_offset"] == len(data)
        command = shlex.split(page["next_command"])
    pytest.fail("reader did not reach EOF")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(("failure_stage", "limit"), [
    ("collection", 4096), ("capture", 4096), ("registration", 4096),
    ("publication", 4096), ("publication", 900), ("console", 900), ("wrapper", 900),
    ("long_route", 900), ("long_route", 650), ("long_route", 550), ("long_route", 256),
    ("long_no_capture", 650), ("long_named", 650), ("long_named", 900),
    ("long_reference_io", 900), ("unresolved_cli", 900),
    ("long_diagnostic_reference_io", 650), ("long_diagnostic_reference_io", 900),
    ("direct_reference_io", 900),
], ids=["collection", "capture", "registration", "publication", "tight-opening", "installed-console",
        "explicit-wrapper", "strict-900", "strict-650", "no-page-room", "no-room", "strict-before-capture",
        "named-650", "named-900", "short-reference-unwritable", "unresolved-cli",
        "diagnostic-reference-io-650", "diagnostic-reference-io-900", "direct-reference-io-900"])
def test_inline_replan_failure_reports_the_actual_receipt(
    tmp_path, monkeypatch, capsys, provider, failure_stage, limit,
):
    from loopx.cli import main
    from loopx.cli_commands import quota
    from loopx.control_plane.turn_driver import codex_context, decision_capture
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "turn-inline-replan-publication"
    retained = "todo_chain_000000000001"
    capture = tmp_path / "original"
    cli_prefix = []
    if failure_stage == "console":
        launcher = Path(sys.executable).with_name("loopx")
        if os.name != "posix" or not launcher.is_file():
            pytest.skip("Requires the console script installed with this test interpreter")
        monkeypatch.delenv("LOOPX_RELEASE_ROOT", raising=False)
        cli_prefix = ["--cli-bin", str(launcher)]
    if failure_stage == "wrapper":
        if os.name != "posix":
            pytest.skip("Requires a POSIX shell")
        launcher = tmp_path / "wrapper with spaces"
        release = Path(__file__).resolve().parents[2] / "scripts" / "loopx"
        launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(str(release))} "$@"\n')
        launcher.chmod(0o700)
        monkeypatch.setenv("LOOPX_PYTHON", sys.executable)
        cli_prefix = ["--cli-bin", str(launcher)]
    if failure_stage.startswith("long_"):
        if os.name != "posix":
            pytest.skip("Exercises a POSIX launcher with spaces")
        release_prefix = tmp_path / "release "
        missing = max(1, 227 - len(os.fsencode(release_prefix / "scripts" / "loopx")))
        launcher = release_prefix.with_name(release_prefix.name + "a" * missing) / "scripts" / "loopx"
        launcher.parent.mkdir(parents=True)
        release = Path(__file__).resolve().parents[2] / "scripts" / "loopx"
        if failure_stage in {"long_reference_io", "long_diagnostic_reference_io"} and limit >= 900:
            launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(str(release))} "$@"\n')
            launcher.chmod(0o700)
        else:
            launcher.symlink_to(release)
        monkeypatch.setenv("LOOPX_PYTHON", sys.executable)
        cli_prefix = ["--cli-bin", str(launcher)]
        capture = tmp_path / ("capture " + "x" * 150) / ("nested " + "x" * 120) / "original"
        capture.parent.mkdir(parents=True)
    if failure_stage == "direct_reference_io":
        if os.name != "posix":
            pytest.skip("Exercises a POSIX launcher with spaces")
        release = Path(__file__).resolve().parents[2] / "scripts" / "loopx"
        prefix = tmp_path / "wrapper with spaces "
        launcher = prefix.with_name(prefix.name + "a" * max(1, 127 - len(os.fsencode(prefix))))
        launcher.write_text(f'#!/bin/sh\nexec {shlex.quote(str(release))} "$@"\n')
        launcher.chmod(0o700)
        monkeypatch.setenv("LOOPX_PYTHON", sys.executable)
        cli_prefix = ["--cli-bin", str(launcher)]
    if failure_stage == "long_named":
        launcher = launcher.rename(launcher.with_name("lxqa"))
        original_path = os.environ["PATH"]
        monkeypatch.setenv("PATH", str(launcher.parent) + os.pathsep + original_path)
        cli_prefix = ["--cli-bin", launcher.name]
    if failure_stage == "unresolved_cli":
        cli_prefix = ["--cli-bin", str(tmp_path / "missing-launcher")]
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "fixture-inline-replan")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    codex_context.codex_context_hook(str(native), {
        "session_id": "fixture-inline-replan", "cwd": str(project),
        "hook_event_name": "SessionStart", "source": "startup",
    })
    observed: list[bytes] = []

    def observe_capture():
        original = (capture / "decision.json").read_bytes()
        receipt = json.loads(original)["heartbeat_receipt"]
        assert receipt["status"] == "committed"
        assert receipt["settlement_identity"]["binding_kind"] == "autonomous_replan"
        assert receipt["pending_action_selection"]["todo_id"] == retained
        assert _heartbeat_receipt_count(runtime, turn) == 1
        observed.append(original)

    def fail_publication(_payload, **_kwargs):
        observe_capture()
        raise RuntimeError("fixture private publication detail")

    def fail_registration(_payload, digest, **_kwargs):
        observe_capture()
        assert sha256(observed[0]).hexdigest() == digest
        raise OSError("fixture private registration detail")

    def fail_collection(*_args, **_kwargs):
        raise RuntimeError("fixture private collection detail")

    def fail_capture(payload, *_args, **_kwargs):
        assert payload["heartbeat_receipt"]["status"] == "committed"
        assert _heartbeat_receipt_count(runtime, turn) == 1
        raise RuntimeError("fixture private capture detail")

    with monkeypatch.context() as patch:
        if failure_stage in {"long_reference_io", "long_diagnostic_reference_io", "direct_reference_io"}:
            def refuse_short_reference(*args, reference_file=None, **kwargs):
                if reference_file is not None and reference_file.name == "r":
                    raise PermissionError("fixture short reference cannot be written")
                return write_reference(*args, reference_file=reference_file, **kwargs)

            write_reference = decision_capture.write_context_read_reference
            patch.setattr(decision_capture, "write_context_read_reference", refuse_short_reference)
        if failure_stage in {"publication", "console", "wrapper", "long_route", "long_named", "long_reference_io", "direct_reference_io"}:
            patch.setattr(codex_context, "prepare_codex_context", fail_publication)
        elif failure_stage == "registration":
            patch.setattr(codex_context, "record_native_claim_source", fail_registration)
        elif failure_stage in {"capture", "long_no_capture", "long_diagnostic_reference_io"}:
            patch.setattr(quota, "_prepare_quota_result_context", fail_capture)
        else:
            patch.setattr(quota, "build_live_quota_should_run_decision", fail_collection)
        code = main([*cli_prefix, "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "quota", "should-run", "--codex-app", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--turn-instance-id", turn, "--todo-id", retained, "--context-delivery", "on",
            "--codex-context-dir", str(native), "--decision-output-dir", str(capture),
            "--max-page-output-bytes", str(limit)])
    stdout = capsys.readouterr().out.encode()
    assert len(stdout) <= limit and b"fixture private" not in stdout
    reply = json.loads(stdout[:limit])
    assert code == 1 and reply["ok"] is False
    saved = capture / "decision.json"
    assert _spend_run_count(runtime) == 0
    if limit in {256, 550} or failure_stage == "unresolved_cli":
        assert "no guard" in reply["reason"].lower()
        assert _heartbeat_receipt_count(runtime, turn) == 0 and not saved.exists()
        return
    if failure_stage == "collection":
        assert reply["error_code"] == "quota_unexpected_collection_error"
        assert reply["heartbeat_receipt"]["status"] == "write_failed"
        assert "event_id" not in reply["heartbeat_receipt"]
        assert not observed and _heartbeat_receipt_count(runtime, turn) == 0
        return

    assert reply["error_code"] == "quota_decision_transport_failed"
    if failure_stage == "capture":
        receipt = reply["heartbeat_receipt"]
        assert receipt["status"] == "committed"
        assert receipt["pending_action_selection"]["todo_id"] == retained
        assert "captured_decision" not in reply and not saved.exists()
        assert _heartbeat_receipt_count(runtime, turn) == 1
        return

    if failure_stage == "long_no_capture":
        assert not saved.exists() and _heartbeat_receipt_count(runtime, turn) == 1
        reader = shlex.split(reply["command"])
        assert reader[0] == cli_prefix[1]
        assert "--reference-file" in reader and "Do not rerun" in reply["instruction"]
        recovered = _read_recovery_on_limited_host(reader, ["--select", "/heartbeat_receipt"],
            project=project, limit=limit)
        receipt = recovered["selections"][0]["value"]
        assert receipt["status"] == "committed"
        assert receipt["settlement_identity"]["turn_instance_id"] == turn
        return

    if failure_stage == "long_diagnostic_reference_io":
        assert not saved.exists()
        locator = reply["captured_decision"]
        diagnostic = Path(locator["path"])
        assert diagnostic.name == "failure.json"
        assert "command" not in reply and "command" not in locator
        assert "Do not rerun admission" in reply["instruction"]
        recovered = _read_recovery_on_limited_host(
            [str(launcher), "--format", "json", "quota", "context-read",
             "--capture-file", str(diagnostic), "--capture-sha256", locator["sha256"],
             "--max-page-output-bytes", "4096"], ["--select", "/heartbeat_receipt"],
            project=project, limit=4096)
        receipt = recovered["selections"][0]["value"]
        assert receipt["settlement_identity"]["turn_instance_id"] == turn
        assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 0
        return

    [original] = observed
    receipt = json.loads(original)["heartbeat_receipt"]
    assert saved.read_bytes() == original
    if failure_stage in {"long_reference_io", "direct_reference_io"}:
        locator = reply["captured_decision"]
        assert locator["path"] == str(saved)
        assert locator["sha256"] == sha256(original).hexdigest()
        assert "Do not rerun admission." in reply["instruction"]
        assert "command" not in reply and "command" not in locator
        assert "larger output" in reply["instruction"]
        # Read the original by its write-time digest through an output channel
        # large enough for the explicit capture path.
        recovered = _read_recovery_on_limited_host(
            [str(launcher), "--format", "json", "quota", "context-read", "--capture-file", locator["path"],
             "--capture-sha256", locator["sha256"], "--max-page-output-bytes", "4096"],
            ["--select", "/heartbeat_receipt"], project=project, limit=4096)
        assert recovered["selections"][0]["value"] == receipt
        assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 0
        return
    if limit == 4096:
        assert reply["heartbeat_receipt"] == receipt
        reader = shlex.split(reply["captured_decision"]["command"])
    else:
        reader = shlex.split(reply["command"])
    if "--reference-file" in reader:
        from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference

        ref_path = reader[reader.index("--reference-file") + 1]
        _, reference = read_context_read_reference(ref_path)
        assert reference["path"] == str(saved)
        assert reference["sha256"] == sha256(original).hexdigest()
    else:
        assert reader[reader.index("--capture-sha256") + 1] == sha256(original).hexdigest()
    fields = ["--select", "/heartbeat_receipt", "--select", "/interaction_contract/cli_channel/next_cli_actions"]
    if cli_prefix:
        assert reader[0] == str(launcher)
        assert "--reference-file" in reader and "Do not rerun" in reply["instruction"]
    if failure_stage == "long_named":
        monkeypatch.setenv("PATH", original_path)
    selection = _read_recovery_on_limited_host(reader, fields, project=project, limit=limit)
    values = {item["pointer"]: item["value"] for item in selection["selections"]}
    assert values["/heartbeat_receipt"] == receipt
    actions = values["/interaction_contract/cli_channel/next_cli_actions"]
    assert saved.read_bytes() == original
    assert _heartbeat_receipt_count(runtime, turn) == 1

    # Use the captured settlement, without a replacement admission.
    _settle_replan_from_captured_actions(project, registry, runtime, turn, actions, receipt["settlement_identity"])
    assert _heartbeat_receipt_count(runtime, turn) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_native_first_selection_publishes_only_final_bound_capture(tmp_path, monkeypatch, provider):
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
        "--max-page-output-bytes", "4096")
    assert rc == 0 and presented["ok"], presented
    assert len(json.dumps(presented, ensure_ascii=False, separators=(",", ":")).encode()) + 1 <= 4096
    original = json.loads((capture / "decision.json").read_text())
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
    _, source_reference = read_context_read_reference(presented["recovery"]["reference_file"])
    _, selected_reference = read_context_read_reference(read["reference_file"])
    assert selected_reference["path"] == source_reference["path"]
    assert selected_reference["sha256"] == source_reference["sha256"]
    assert json.loads(read["content"])["selections"][0]["value"] == original["heartbeat_receipt"]
    assert _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_captured_direct_replan_command_composes_semantic_writeback_once(tmp_path, monkeypatch, provider):
    from loopx.control_plane.turn_driver.codex_context import codex_context_hook

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry, stride_strategy="off")
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "turn-captured-composed-replan"
    retained = "todo_chain_000000000001"
    capture = tmp_path / "capture"
    native = tmp_path / "native"
    native.mkdir(mode=0o700)
    monkeypatch.setenv("CODEX_THREAD_ID", "captured-composed-replan")
    monkeypatch.delenv("CODEX_ROLLOUT_TRACE_ROOT", raising=False)
    codex_context_hook(str(native), dict(session_id="captured-composed-replan", cwd=str(project),
        hook_event_name="SessionStart", source="startup"))
    cli = str(Path(sys.executable).with_name("loopx"))
    rc, shown = _run_cli(registry, runtime, "--cli-bin", cli, "quota", "should-run", "--codex-app", "--complete-turn",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", turn,
        "--todo-id", retained, "--context-delivery", "on", "--codex-context-dir", str(native),
        "--decision-output-dir", str(capture), "--max-page-output-bytes", "64000", cwd=project)
    assert rc == 0 and shown["ok"], shown
    path = capture / "decision.json"
    raw = path.read_bytes()
    original = json.loads(raw)
    assert original["schema_version"] == "loopx_turn_envelope_v1"
    receipt = original["heartbeat_receipt"]
    identity = receipt["settlement_identity"]
    assert receipt["status"] == "committed" and identity["binding_kind"] == "autonomous_replan"
    assert receipt["pending_action_selection"]["todo_id"] == retained
    assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 0

    def run(command, expected=0):
        result = subprocess.run(shlex.split(command) if isinstance(command, str) else command,
            cwd=project, capture_output=True, check=False, timeout=30)
        assert result.returncode == expected, (result.stdout, result.stderr)
        return json.loads(result.stdout)

    # This mainline reader accepts an explicitly captured complete decision;
    # newer C1 hosts generate the same review route in their first-read view.
    source_hash = original["contract_binding"]["content_hash"]
    review = run([cli, "--cli-bin", cli, "--format", "json", "quota", "replan-view",
        "--decision-file", str(path), "--source-hash", source_hash, "--replan-stage", "review"])
    writer = run(review["next"]["writeback"])
    assert writer["context_id"] == review["context_id"]
    assert writer["writeback"]["settlement_plan"] == original["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert writer["writeback"]["settlement_plan"]["identity"] == identity
    assert _spend_run_count(runtime) == 0 and path.read_bytes() == raw
    vision = tmp_path / "measured decision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {"acceptance_summary": "Validate the new evidence before dependent work."},
        "path_delta": {"outcome": "replan", "prior_assumption": "The existing route was supported.",
            "observed_reality": "The validation found an independent alternate route.",
            "changed": ["Review before a fresh delivery."], "evidence_refs": ["evidence:independent-validation"]},
    }))
    placeholder = "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>"
    args = [str(vision) if token == placeholder else token for token in shlex.split(writer["settlement_command"])]
    assert str(vision) in args and "--settlement-receipt" in args
    args += ["--no-global-sync", "--suppress-external-sinks"]

    # Missing semantic evidence must never trigger the composed debit.
    withheld = [token if token != str(vision) else str(vision.with_name("missing decision.json")) for token in args]
    refusal = run(withheld, expected=1)
    assert not refusal["ok"] and _spend_run_count(runtime) == 0
    settled = run(args)
    assert settled["settlement_progress"]["state"] == "settled"
    assert settled["settlement_identity"] == identity and settled["quota_spend"]["appended"]
    assert settled["autonomous_replan_ack"]["semantic_delta"]["accepted"]
    assert "settlement_owed" not in settled
    assert _spend_run_count(runtime) == 1
    replay = run(args)
    assert replay["idempotent_replay"] and not replay["appended"]
    assert replay["settlement_result"] == settled["settlement_result"]
    assert _heartbeat_receipt_count(runtime, turn) == 1 and _spend_run_count(runtime) == 1
    assert path.read_bytes() == raw
