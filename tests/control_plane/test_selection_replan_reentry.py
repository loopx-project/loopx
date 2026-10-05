"""A deferred selection must expose a runnable recovery before settlement."""
import json
import shlex

import pytest

from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, SELECTED_REPLAN_TODO_ID, TODO_ID,
    AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD, _append_surface_only_runs,
    _configure_autonomous_replan_fixture, _configure_selected_todo_replan_fixture,
    _configure_selectable_alternative, _heartbeat_receipt_count, _projected_cli_args,
    _run_cli, _run_generated_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize(("binding", "initial_replan"), [
    ("todo", False), ("autonomous_replan", False), ("todo", True),
])
def test_replan_selection_recovers_inline_or_refuses_ineligible_choice_and_settles_once(tmp_path, binding, initial_replan):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    if initial_replan:
        _configure_selected_todo_replan_fixture(project, registry)
        _append_surface_only_runs(runtime, count=AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD)
    turn = "turn-selection-preempted"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn, "--scan-path", str(project))
    rc, first = _run_cli(registry, runtime, *guard)
    assert rc == 0, first
    assert first["decision"] == ("autonomous_replan_required" if initial_replan else "run")
    assert first["interaction_contract"]["cli_channel"]["selection_required"]
    assert "settlement_identity" not in first["heartbeat_receipt"]
    if initial_replan:
        assert "periodic_review_due" in {
            trigger["kind"] for trigger in first["autonomous_replan_obligation"]["triggers"]
        }
    if binding == "todo":
        _configure_selected_todo_replan_fixture(project, registry)
        selected_id = SELECTED_REPLAN_TODO_ID
    else:
        _configure_autonomous_replan_fixture(project, runtime, registry)
        selected_id = TODO_ID
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
        rc, resumed = _run_generated_cli(command, registry_path=registry)
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
        rc, result = _run_cli(registry, runtime, *spend_args, "--scan-path", str(project), cwd=project)
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


@pytest.mark.parametrize("initial_replan", [False, True])
def test_reentry_never_replaces_retained_selection_with_recommended_todo(tmp_path, initial_replan):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    if initial_replan:
        _configure_selected_todo_replan_fixture(project, registry)
    turn = "turn-selection-recommendation-drift"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn, "--scan-path", str(project))
    rc, first = _run_cli(registry, runtime, *guard)
    assert rc == 0, first
    assert first["interaction_contract"]["cli_channel"]["selection_required"]
    assert "settlement_identity" not in first["heartbeat_receipt"]
    rc, preferences = _run_cli(
        registry, runtime, "semantic-preference", "agent", "read",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
    )
    assert rc == 0, preferences
    assert _heartbeat_receipt_count(runtime, turn) == 1

    # The original explicit choice leaves the refreshed frontier while a
    # different recommended Todo becomes visible under a hard replan.
    _configure_selected_todo_replan_fixture(project, registry)
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


@pytest.mark.parametrize("changed_gate", ["user_gate", "quota"])
@pytest.mark.parametrize("inline", [True, False])
def test_inline_reentry_reads_gates_changed_after_choice_receipt(
    tmp_path, monkeypatch, capsys, changed_gate, inline,
):
    from loopx.cli import main
    from loopx.cli_commands import quota as quota_cli
    from test_quota_settlement_cli import _append_blocking_user_gate

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    turn = "turn-inline-reentry-new-gate"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn,
             "--scan-path", str(project))
    rc, first = _run_cli(registry, runtime, *guard)
    assert rc == 0 and first["interaction_contract"]["cli_channel"]["selection_required"]
    original = quota_cli.reconcile_requested_quota_action_selection
    calls = []

    def reconcile_then_change_gate(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(kwargs["selection"].requested_todo_id)
        if len(calls) == 1:
            assert result.rejected and result.receipt_status == "selection_retained"
            if changed_gate == "user_gate":
                _append_blocking_user_gate(project)
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
                         "--slots", "1", "--execute", "--scan-path", str(project))
    assert rc == 1 and spend["ok"] is False, spend
    assert _spend_run_count(runtime) == 0


def test_first_inline_result_has_matching_turn_envelope(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    turn = "turn-inline-envelope"
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID, "--turn-instance-id", turn,
             "--scan-path", str(project))
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
    assert _heartbeat_receipt_count(runtime, turn) == 3
    assert _spend_run_count(runtime) == 0
