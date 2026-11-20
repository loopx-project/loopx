"""A lost execution generation can close only its original Turn, without spend."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest
import loopx
from loopx.agent_registry import registered_agent_ids_for_goal

from canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime
from codex_checkpoint_fixture import read_cli_policy_payload
from loopx.cli import build_parser, main as cli_main
from loopx.cli_commands.project_lifecycle_inputs import inline_progress_observation
from loopx.control_plane.coordination import local_authority
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.goals.source_session_registration import (
    FreshSourceSessionRegistration,
    configure_source_creation_goal,
    register_fresh_source_session_project,
)
from loopx.control_plane.quota import settlement
from loopx.control_plane.projects.registry_codec import load_project_registry, ProjectRegistryProtocolError
from loopx.control_plane.quota.effect_program import SettlementIdentity
from loopx.control_plane.quota.execution_preparation import prepared_lease_receipt_fields
from loopx.control_plane.quota.heartbeat_receipt import (
    HeartbeatReceiptIdentityConflictError,
    ensure_turn_heartbeat_settlement_receipt,
    requalify_bound_heartbeat_receipt,
    retain_pending_heartbeat_action_selection,
    upgrade_identityless_heartbeat_receipt,
)
from loopx.control_plane.runtime import time as runtime_time
from loopx.control_plane.todos.completion_validation_projection import (
    completion_validation_declaration,
    project_completion_validation_authority,
)
from loopx.control_plane.todos.completion_validation_store import (
    persist_completion_validation_declaration,
    prepare_completion_validation_declaration,
)
from loopx.control_plane.work_items import task_lease
from loopx.control_plane.work_items.progress_observation import normalize_progress_observation
from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path
from tests.test_attached_session_goal_instance import _register
from tests.control_plane.test_explore_result_writeback import ATTACHMENT
from tests.control_plane.test_quota_settlement_cli import _write_fixture as write_legacy_fixture


GOAL = "execution-loss-closeout"
OWNER = "original-worker"
PEER = "replacement-worker"
TURN = "original-prepared-turn"


def _registered_agents(context):
    goal = next(goal for goal in load_project_registry(context["registry"])["goals"]
                if goal["id"] == context["goal_id"])
    return registered_agent_ids_for_goal(goal)


def _source_transaction(context, method, schema, *, operation_id, **intent):
    registry_digest = sha256(context["registry"].read_bytes()).hexdigest()
    result = effect_runtime_result(method, {
        "schema_version": schema, "runtime_root": str(context["runtime"].resolve()),
        "goal_id": context["goal_id"], "operation_id": operation_id,
        "expected_provider_revision": _canonical(context)["provider_revision"],
        "expected_registry_sha256": registry_digest,
        "registry_source": {"path": str(context["registry"].resolve()), "sha256": registry_digest},
        "registered_agents": _registered_agents(context), "dry_run": False,
        "observed_at": runtime_time.now_local_iso(), **intent,
    })
    assert result["status"] == "applied", result
    assert result["decision_read_from_provider"] is True
    assert result["legacy_fallback_used"] is False
    return result


def _source_create_todo(context, validation_argv):
    operation = "execution-loss-task"
    todo_id = "todo_" + sha256(operation.encode()).hexdigest()[:24]
    fields = {"schema_version": "todo_domain_record_v0", "todo_id": todo_id,
              "role": "agent", "text": "Validate the original task before declaring completion.",
              "status": "open", "done": False, "archive_state": "active",
              "task_class": "advancement_task", "action_kind": "validate",
              "continuation_policy": "same_agent_non_delivery", "claimed_by": OWNER,
              "validation_command_argv": validation_argv}
    declaration = completion_validation_declaration(fields)
    assert declaration is not None
    prepare_completion_validation_declaration(runtime_root=context["runtime"],
        goal_id=context["goal_id"], declaration=declaration)
    result = _source_transaction(context, "coordination.local_authority.todo_create",
        "loopx_local_coordination_todo_create_request_v3", operation_id=operation,
        actor_agent_id=OWNER, todo=project_completion_validation_authority(fields))
    assert result["todo_id"] == todo_id
    assert result["todo"]["completion_validation_required"] is True
    persist_completion_validation_declaration(runtime_root=context["runtime"],
        goal_id=context["goal_id"], todo_id=todo_id, declaration=declaration)
    return todo_id


def _claim(context, owner, operation):
    if context["source_profile"]:
        return _source_transaction(context, "coordination.local_authority.todo_claim",
            "loopx_local_coordination_todo_claim_request_v3", operation_id=operation,
            todo_id=context["todo"], role="agent", actor_agent_id=owner, claimed_by=owner,
            manage_execution=True, lease_request=None)
    code, claimed = context["cli"]("todo", "claim", "--goal-id", context["goal_id"],
        "--todo-id", context["todo"], "--agent-id", owner, "--claimed-by", owner)
    assert code == 0, claimed
    return claimed


def _source_reservation(context):
    # SourceSession's generic work producer is intentionally unavailable. Model
    # stored reservation history through the existing append/binding owners;
    # TS alone derives work_admission and closeout_required below.
    fields = {"ok": True, "should_run": False, "must_attempt_work": False,
              "delivery_allowed": False, "quiet_noop_allowed": True,
              "prepared_lease_required": True, "semantic_replan_obligation_id": "",
              "stall_observation": "not_applicable"}
    reserved = append_rollout_event(rollout_event_log_path(context["runtime"], context["goal_id"]),
        build_rollout_event(goal_id=context["goal_id"], goal_ref=context["goal_ref"],
            event_kind="quota_should_run", agent_id=OWNER, run_id=TURN,
            status="turn_reservation", details=fields))
    retained, appended = retain_pending_heartbeat_action_selection(context["runtime"],
        goal_id=context["goal_id"], goal_ref=context["goal_ref"], agent_id=OWNER,
        turn_instance_id=TURN, todo_id=context["todo"], reason="claim_required")
    assert appended is True and retained["event_id"] != reserved["event_id"]
    identity = SettlementIdentity(goal_id=context["goal_id"], agent_id=OWNER,
        todo_id=context["todo"], turn_instance_id=TURN)
    bound, appended = upgrade_identityless_heartbeat_receipt(context["runtime"],
        goal_id=context["goal_id"], goal_ref=context["goal_ref"], agent_id=OWNER,
        turn_instance_id=TURN, todo_id=context["todo"], settlement_effect_id=identity.effect_id,
        status="turn_reservation", summary="Retain the original Todo while execution is prepared.", details=fields)
    assert appended is True and bound["details"]["settlement_receipt_revision"] == "identity_upgrade"
    ensured = ensure_turn_heartbeat_settlement_receipt(context["runtime"], identity,
        semantic_replan_guard_scoped=True, semantic_replan_obligation_id=None,
        prepared_lease_required=True, goal_ref=context["goal_ref"])
    assert ensured == bound
    context["reservation_details"] = {key: value for key, value in bound["details"].items()
        if key not in {"settlement_receipt_revision", "closeout_required"}}


def _source_requalify(context, lease):
    fields = {**context["reservation_details"], "ok": True, "should_run": True,
              "must_attempt_work": True, "delivery_allowed": True, "quiet_noop_allowed": False,
              "selected_todo_id": context["todo"]}
    if lease is not None:
        fields.update(prepared_lease_receipt_fields({"idempotency_key": lease["idempotency_key"],
            "expected_version": lease["version"]}, lease_epoch=lease["lease_epoch"]))
    return requalify_bound_heartbeat_receipt(context["runtime"],
        goal_id=context["goal_id"], goal_ref=context["goal_ref"], agent_id=OWNER,
        turn_instance_id=TURN, todo_id=context["todo"], replan_obligation_id=None,
        status="normal_run", details=fields, registry_path=context["registry"])


def _fixture(tmp_path, monkeypatch, capsys, provider, *, prepared=True,
             replace=True, replacement_owner=OWNER, source_profile=True):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    registry = project / ".loopx" / "registry.json"
    state = project / "GOAL.md"
    if source_profile:
        project.mkdir()
        goal = {"id": GOAL, "project_id": "project", "title": "Recover original Turn",
                "status": "active", "repo": str(project), "state_file": state.name}
        configure_source_creation_goal(goal, [OWNER, PEER], provider, "register-execution-loss-goal")
        created = register_fresh_source_session_project(FreshSourceSessionRegistration(
            registry_path=registry, runtime_root=runtime,
            operation_id="register-execution-loss-goal", project_id="project", goal_id=GOAL,
            objective="Retain the original work while closing its interrupted Turn.",
            non_goals=["Do not complete the task or credit delivery."],
            acceptance=["Original Turn recovery preserves the current work owner."],
            unknowns=[], next_effect="Inspect the interrupted execution.",
            stop_condition="Stop when the original execution authority is unavailable.",
            project_record={"id": "project", "kind": "work", "path": str(project)},
            goal_record=goal, state_file=state,
        ))
        assert created["ok"] is True, created
        goal_ref, goal_id = created["goal_ref"], GOAL
    else:
        project, runtime, registry = write_legacy_fixture(tmp_path)
        configuration = json.loads(registry.read_text())
        goal = configuration["goals"][0]
        goal["coordination"]["registered_agents"] = [OWNER, PEER]
        registry.write_text(json.dumps(configuration))
        goal_id, goal_ref = goal["id"], None
        state = project / goal["state_file"]
        state.write_text(state.read_text().split("## Agent Todo")[0] + "## Agent Todo\n\n")
        initialize_canonical_source_fixture(
            runtime, goal_id, state_path=state, provider=provider, handoff_mode="hard_lease",
        )

    def cli(*argv):
        # This callable always exercises the real public parser and dispatcher.
        with monkeypatch.context() as context:
            context.chdir(project)
            code = cli_main([
                "--registry", str(registry), "--runtime-root", str(runtime),
                "--format", "json", *argv,
            ])
        return code, read_cli_policy_payload(capsys.readouterr().out, returncode=code)

    context = {"cli": cli, "project": project, "runtime": runtime, "registry": registry,
               "state": state, "goal_id": goal_id, "goal_ref": goal_ref, "source_profile": source_profile}
    validation_argv = [sys.executable, "-c",
        "from pathlib import Path; Path('completion-validator-ran').write_text('ran')"]
    if source_profile:
        todo = _source_create_todo(context, validation_argv)
    else:
        code, added = cli("todo", "add", "--goal-id", goal_id, "--role", "agent",
            "--text", "Validate the original task before declaring completion.",
            "--action-kind", "validate", "--continuation-policy", "same_agent_non_delivery",
            "--claimed-by", OWNER, "--operation-id", "execution-loss-task", "--execute",
            "--validation-command-json", json.dumps(validation_argv))
        assert code == 0, json.dumps(added, ensure_ascii=False)
        todo = added["todo_id"]
    context["todo"] = todo
    if source_profile:
        _source_reservation(context)
    if not prepared:
        if source_profile:
            pending, appended = _source_requalify(context, None)
            assert appended is True and "prepared_lease_key_sha256" not in pending["details"]
        else:
            code, pending = cli(*_guard_args(context))
            assert code == 0 and pending["execution_preparation"]["state"] == "claim_required", pending
    claim = _claim(context, OWNER, "execution-loss-original-claim")
    original_lease = _lease(context)
    context["original_lease"] = original_lease
    if prepared:
        if source_profile:
            admitted, appended = _source_requalify(context, original_lease)
            assert appended is True
            assert admitted["details"]["settlement_receipt_revision"] == "work_admission"
            assert admitted["details"]["closeout_required"] is True
            assert admitted["details"]["prepared_lease_epoch"] == original_lease["lease_epoch"]
        else:
            code, admitted = cli(*_guard_args(context))
            assert code == 0 and admitted["normal_delivery_allowed"] is True, admitted
            assert admitted["heartbeat_receipt"]["closeout_required"] is True
            assert admitted["execution_preparation"]["lease_epoch"] == original_lease["lease_epoch"]
        context["admitted"] = admitted
    context["receipt_lines"] = _receipt_lines(context)
    assert context["receipt_lines"], "the original receipt must be production-generated"
    if replace:
        after_expiry = datetime.fromisoformat(
            original_lease["expires_at"].replace("Z", "+00:00"),
        ).replace(microsecond=0) + timedelta(seconds=2)

        class AfterExpiryDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return (after_expiry.astimezone(tz) if tz
                        else after_expiry.astimezone().replace(tzinfo=None))

        # Registered transport clock seams, never edits to persisted lease bytes.
        monkeypatch.setattr(runtime_time, "datetime", AfterExpiryDatetime)
        monkeypatch.setattr(local_authority, "now_local", lambda: after_expiry.isoformat())
        monkeypatch.setattr(task_lease, "now_utc", lambda: after_expiry.astimezone(timezone.utc))
        assert runtime_time.now_utc() == after_expiry.astimezone(timezone.utc)
        replacement = _claim(context, replacement_owner, "execution-loss-replacement-claim")
        assert replacement["claim_operation_id"] != claim["claim_operation_id"]
        current = _lease(context)
        assert current["owner"] == replacement_owner
        assert current["lease_epoch"] > original_lease["lease_epoch"]
        assert current["version"] > original_lease["version"]
        assert current["idempotency_key"] != original_lease["idempotency_key"]
    context["current_lease"] = _lease(context)
    assert _receipt_lines(context) == context["receipt_lines"]
    return context


def _binding(context):
    return ["--goal-id", context["goal_id"],
            *(["--goal-instance-id", context["goal_ref"]["goal_instance_id"]] if context["goal_ref"] else []),
            "--agent-id", OWNER, "--todo-id", context["todo"], "--turn-instance-id", TURN]


def _guard_args(context):
    return ["quota", "should-run", "--codex-app", "--context-delivery", "off", *_binding(context)]


def _proof(lease):
    return ["--task-lease-idempotency-key", lease["idempotency_key"],
            "--task-lease-expected-version", str(lease["version"])]


def _closeout_args(context, *, lease=None, execution_loss=True):
    return ["refresh-state", *_binding(context),
            *(["--execution-loss-closeout"] if execution_loss else []),
            "--classification", "execution_loss_closeout", "--delivery-batch-scale", "single_surface",
            "--delivery-outcome", "outcome_gap", "--delivery-boundary", "semantic_closeout",
            "--progress-result-class", "blocked", "--progress-work-item-id", context["todo"],
            "--progress-blocker-id", "original-execution-lost",
            "--progress-evidence-id", "evidence:original-lease-expired",
            *(["--vision-unchanged-reason", "The original work remains open; close only its interrupted Turn."]
              if not execution_loss else []),
            "--no-global-sync", "--suppress-external-sinks",
            *_proof(lease or context["current_lease"])]


def _lease(context):
    if context["source_profile"]:
        inspected = effect_runtime_result("task_lease.inspect.native", {
            "schema_version": "loopx_task_lease_inspect_request_v0", "source": "canonical",
            "runtime_root": str(context["runtime"].resolve()), "goal_id": context["goal_id"],
            "todo_id": context["todo"], "authority": {"handoff_mode": "hard_lease", "todos": [],
                "registered_agent_candidates": [_registered_agents(context)],
                "source_receipts": [{"source_id": "registry", "path": str(context["registry"].resolve()),
                    "state": "file", "sha256": sha256(context["registry"].read_bytes()).hexdigest()}]},
        })
        assert inspected["ok"] is True and inspected["source_authority"] in {"file_v0", "sqlite_v0"}, inspected
        assert inspected["provider_revision"] == _canonical(context)["provider_revision"]
        assert inspected["legacy_fallback_used"] is False
        assert isinstance(inspected["lease"], dict)
        return inspected["lease"]
    code, inspected = context["cli"](
        "task-lease", "inspect", "--goal-id", context["goal_id"], "--todo-id", context["todo"],
    )
    assert code == 0, inspected
    assert isinstance(inspected["lease"], dict)
    return inspected["lease"]


def _readback(context):
    return settlement.read_heartbeat_settlement(
        context["runtime"], goal_id=context["goal_id"], agent_id=OWNER, todo_id=context["todo"],
        turn_instance_id=TURN, registry_path=context["registry"], goal_ref=context["goal_ref"],
    )


def _receipt_lines(context):
    path = rollout_event_log_path(context["runtime"], context["goal_id"])
    if not path.exists():
        return []
    return [line for line in path.read_bytes().splitlines(keepends=True)
            if (event := json.loads(line)).get("event_kind") == "quota_should_run"
            and event.get("run_id") == TURN
            and (event.get("goal_ref") == context["goal_ref"] if context["goal_ref"] else "goal_ref" not in event)]


def _runs(context):
    directory = context["runtime"] / "goals" / context["goal_id"] / "runs"
    return {path.name: path.read_bytes() for path in directory.glob("*.json")}


def _canonical(context):
    payload = local_authority.read_canonical_todos_if_promoted(
        runtime_root=context["runtime"], goal_id=context["goal_id"], include_leases=True,
    )
    assert payload is not None
    return {key: payload[key] for key in ("store_identity", "provider_revision", "todos", "leases")}


def _unchanged_state(context):
    log = rollout_event_log_path(context["runtime"], context["goal_id"])
    return (_canonical(context), context["state"].read_bytes(), _runs(context), log.read_bytes())


def _assert_no_completion_or_debit(context):
    if context["source_profile"]:
        todo = next(row for row in _canonical(context)["todos"] if row["todo_id"] == context["todo"])
    else:
        code, listed = context["cli"]("todo", "list", "--goal-id", context["goal_id"], "--todo-id", context["todo"])
        assert code == 0, listed
        todo = listed["todo"]
    assert todo["status"] == "open" and todo.get("completion_validation_required") is True
    assert not (context["project"] / "completion-validator-ran").exists()
    index = context["runtime"] / "goals" / context["goal_id"] / "runs" / "index.jsonl"
    records = [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
    assert not any(record.get("classification") == "quota_slot_spent" for record in records)


def _assert_original_prepared_generation(context, readback):
    original, current = context["original_lease"], context["current_lease"]
    assert readback.prepared_lease == {
        "prepared_lease_key_sha256": sha256(original["idempotency_key"].encode()).hexdigest(),
        "prepared_lease_version": original["version"],
        "prepared_lease_epoch": original["lease_epoch"],
    }
    if current["lease_epoch"] != original["lease_epoch"]:
        assert readback.prepared_lease["prepared_lease_key_sha256"] != sha256(
            current["idempotency_key"].encode(),
        ).hexdigest()


def _reject_without_effect(context, argv, *, expected_code=None):
    before = _unchanged_state(context)
    code, rejected = context["cli"](*argv)
    assert code != 0 and rejected["ok"] is False, rejected
    if expected_code is not None:
        assert rejected.get("error_code") == expected_code, json.dumps(rejected, ensure_ascii=False)
    assert _unchanged_state(context) == before
    _assert_no_completion_or_debit(context)
    return rejected


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_direct_refresh_sparse_effect_map_cannot_hide_business_requests(
    tmp_path, monkeypatch, capsys, provider,
):
    from loopx.state_refresh import refresh_state_run

    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    args = build_parser().parse_args(_closeout_args(context))
    request = {
        "registry_path": context["registry"], "runtime_root_override": str(context["runtime"]),
        "goal_id": context["goal_id"], "project": None, "state_file": None,
        "classification": "execution_loss_closeout", "recommended_action": None,
        "agent_id": OWNER, "todo_id": context["todo"], "turn_instance_id": TURN,
        "delivery_outcome": "outcome_gap", "delivery_boundary": "semantic_closeout",
        "progress_observation": normalize_progress_observation(inline_progress_observation(args)),
        "task_lease_proof": {"idempotency_key": context["current_lease"]["idempotency_key"],
                             "expected_version": context["current_lease"]["version"]},
        "execution_loss_closeout": True, "execution_loss_requested_effects": {}, "dry_run": False,
        "sync_global": False,
    }
    before = _unchanged_state(context)
    for ordinary in (
        {"recommended_action": "Continue business work."},
        {"next_action": "Continue business work."},
        {"agent_vision_packet": {"state": "active"}},
        {"vision_unchanged_reason": "Record a normal checkpoint."},
        {"explore_result": ATTACHMENT},
        {"completion_todo_id": context["todo"]},
        {"usage_measurement": {"input_tokens": 1}},
        {"usage_codex_session": tmp_path / "private-session.jsonl"},
        {"project": context["project"]},
        {"state_file": context["state"]},
        {"delivery_workspace_path": tmp_path / "business-workspace"},
        {"external_delivery": {"suppress": True}},
        {"sync_global": True},
    ):
        with pytest.raises(ValueError) as rejected:
            refresh_state_run(**{**request, **ordinary})
        assert getattr(rejected.value, "code", None) == "refresh_task_lease_unavailable", ordinary
        assert _unchanged_state(context) == before
        _assert_no_completion_or_debit(context)
    default_sync = dict(request)
    default_sync.pop("sync_global")
    with pytest.raises(ValueError) as rejected:
        refresh_state_run(**default_sync)
    assert getattr(rejected.value, "code", None) == "refresh_task_lease_unavailable"
    assert _unchanged_state(context) == before
    closed = refresh_state_run(**request)
    assert closed["appended"] is True and closed["settlement_progress"]["state"] == "settled"
    _assert_no_completion_or_debit(context)


def _replace_argument(argv, flag, value):
    result = list(argv)
    result[result.index(flag) + 1] = value
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("source_profile", [True, False], ids=["source-session", "legacy-alias"])
def test_current_control_writer_closes_lost_original_turn_without_business_authority(
    tmp_path, monkeypatch, capsys, provider, source_profile,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=source_profile)
    original = _readback(context)
    assert original is not None and original.identity.failure is None
    assert not original.writeback.receipts and not original.spend.receipts
    _assert_original_prepared_generation(context, original)
    if source_profile:
        before_requalification = _unchanged_state(context)
        with pytest.raises(HeartbeatReceiptIdentityConflictError,
                           match="work requalification must preserve this Turn's original task lease generation"):
            _source_requalify(context, context["current_lease"])
        assert _unchanged_state(context) == before_requalification
        _assert_no_completion_or_debit(context)
    else:
        rejected = _reject_without_effect(context, _guard_args(context),
                                         expected_code="heartbeat_receipt_identity_conflict")
        pure = rejected["original_settlement_readback"]
        assert pure["settlement_identity"] == original.identity.value.as_dict()
        assert pure["settlement_identity"]["todo_id"] == context["todo"]
        assert pure["settlement_identity"]["turn_instance_id"] == TURN
        assert pure["prepared_lease"] == original.prepared_lease
        assert pure["settlement_progress"]["state"] == "writeback_required"
        assert "refresh-state --execution-loss-closeout" in rejected["recommended_action"]
        assert "Rerun quota should-run" not in rejected["recommended_action"]
        # Valid blocked intent reaches the original first-writeback fence;
        # neither the expired proof nor its replacement may pass that gate.
        for lease in (context["original_lease"], context["current_lease"]):
            _reject_without_effect(context, _closeout_args(context, lease=lease, execution_loss=False),
                                   expected_code="refresh_task_lease_unavailable")
    before_authority, before_state, before_runs = (
        _canonical(context), context["state"].read_bytes(), _runs(context),
    )
    argv = _closeout_args(context)
    code, closed = context["cli"](*argv)
    assert code == 0 and closed["appended"] is True, closed
    assert closed["settlement_identity"] == original.identity.value.as_dict()
    assert closed["settlement_progress"]["state"] == "settled"
    assert closed["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
    assert "settlement_owed" not in closed
    assert _canonical(context) == before_authority
    assert context["state"].read_bytes() == before_state
    assert _receipt_lines(context) == context["receipt_lines"]
    after_runs = _runs(context)
    assert {key: after_runs[key] for key in before_runs} == before_runs
    new_names = after_runs.keys() - before_runs.keys()
    assert len(new_names) == 1
    run = json.loads(after_runs[new_names.pop()])
    assert run["progress_observation"]["result_class"] == "blocked"
    assert run["delivery_outcome"] == "outcome_gap"
    assert run["execution_loss_closeout"]["schema_version"] == "quota_execution_loss_closeout_v0"
    qualification = run["execution_loss_closeout"]
    assert qualification["original_prepared"] == original.prepared_lease
    current = context["current_lease"]
    assert qualification["current_control_writer"] == {
        "lease_key_sha256": sha256(current["idempotency_key"].encode()).hexdigest(),
        "version": current["version"], "lease_epoch": current["lease_epoch"], "owner": OWNER,
        "store_identity": before_authority["store_identity"],
        "provider_revision": before_authority["provider_revision"],
    }
    for forbidden in ("agent_vision", "autonomous_replan_ack", "explore_result", "usage"):
        assert forbidden not in run
    settled = _readback(context)
    assert settled.replay_phase == settlement.ReceiptBoundReplayPhase.SETTLED
    assert settled.prepared_lease == original.prepared_lease
    _assert_original_prepared_generation(context, settled)
    assert settled.heartbeat_receipt == original.heartbeat_receipt
    assert settled.completion_event is None
    assert _lease(context) == context["current_lease"]
    _assert_no_completion_or_debit(context)
    before_replay = _unchanged_state(context)
    code, replay = context["cli"](*argv)
    assert code == 0 and replay["idempotent_replay"] is True and replay["appended"] is False, replay
    assert replay["settlement_result"] == closed["settlement_result"]
    assert _unchanged_state(context) == before_replay
    if not source_profile:
        code, spent = context["cli"](
            "quota", "spend-slot", *_binding(context), "--slots", "1", "--source", "heartbeat", "--execute",
        )
        assert code == 0 and spent["ok"] is True and spent["appended"] is False, spent
        assert spent["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
        assert spent["settlement_progress"]["no_spend"] is True
        assert spent["reason"] == "exact Turn closeout replayed without a quota debit"
        assert _unchanged_state(context) == before_replay
        _assert_no_completion_or_debit(context)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_original_turn_rejects_stale_missing_or_foreign_current_writer_proof(
    tmp_path, monkeypatch, capsys, provider,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider)
    argv = _closeout_args(context)
    no_proof = argv[:-4]
    for candidate, expected in (
        (_closeout_args(context, lease=context["original_lease"]), "refresh_task_lease_unavailable"),
        # The shared proof decoder rejects an absent wire before lease matching.
        (no_proof, "invalid_request"),
        (_replace_argument(argv, "--task-lease-expected-version", str(context["current_lease"]["version"] - 1)),
         "refresh_task_lease_unavailable"),
        (_replace_argument(argv, "--task-lease-idempotency-key", "foreign-control-writer"),
         "refresh_task_lease_unavailable"),
    ):
        _reject_without_effect(context, candidate, expected_code=expected)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("source_profile", [True, False], ids=["source-session", "legacy-alias"])
def test_replacement_owner_cannot_close_another_agents_original_turn(
    tmp_path, monkeypatch, capsys, provider, source_profile,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider,
                       replacement_owner=PEER, source_profile=source_profile)
    assert context["current_lease"]["owner"] == PEER
    _reject_without_effect(context, _closeout_args(context), expected_code="refresh_task_lease_unavailable")
    foreign_binding = _replace_argument(_closeout_args(context), "--agent-id", PEER)
    _reject_without_effect(context, foreign_binding)


def test_closeout_rejects_wrong_identity_and_extra_work_intent(tmp_path, monkeypatch, capsys):
    context = _fixture(tmp_path, monkeypatch, capsys, "file")
    argv = _closeout_args(context)
    other_source = tmp_path / "other-source"
    other_source.mkdir()
    _other_registry, _other_runtime, _other_store, other_instance = _register(other_source)
    assert other_instance != context["goal_ref"]["goal_instance_id"]
    # One provider is enough for CLI identity/intent transport; both storage owners
    # independently qualify the control-writer and missing-readiness cases above/below.
    identities = (
        ("--goal-id", "another-goal"), ("--goal-instance-id", other_instance),
        ("--agent-id", PEER), ("--todo-id", "todo_another"), ("--turn-instance-id", "another-turn"),
    )
    for flag, value in identities:
        _reject_without_effect(context, _replace_argument(argv, flag, value))
    for extra in (
        ["--next-action", "Continue replacement business work."],
        ["--explore-result-json", json.dumps(ATTACHMENT)],
        ["--progress-surface-id", "replacement-business-surface"],
        ["--progress-hypothesis-id", "replacement-business-hypothesis"],
        ["--completion-todo-id", context["todo"]],
        ["--vision-state", "active", "--vision-summary", "Change the original work route."],
    ):
        _reject_without_effect(context, [*argv, *extra])
    progress = _replace_argument(argv, "--progress-result-class", "advanced")
    _reject_without_effect(context, progress)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("removed", ["execution_loss_closeout", "execution_loss_commit", "both"])
def test_administrative_subtype_without_complete_proof_cannot_settle(
    tmp_path, monkeypatch, capsys, provider, removed,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    code, closed = context["cli"](*_closeout_args(context))
    assert code == 0 and closed["settlement_progress"]["state"] == "settled", closed
    index = context["runtime"] / "goals" / context["goal_id"] / "runs" / "index.jsonl"
    records = [json.loads(line) for line in index.read_text().splitlines()]
    record = next(row for row in records if "execution_loss_closeout" in row)
    for field in ("execution_loss_closeout", "execution_loss_commit"):
        if removed in (field, "both"):
            del record[field]
    # Keep the matching refresh event and the shaped blocked_retry. Neither
    # may certify no-spend when the administrative qualification is absent.
    Path(record["json_path"]).write_text(json.dumps(record) + "\n")
    index.write_text("".join(json.dumps(row) + "\n" for row in records))
    before = _unchanged_state(context)
    with pytest.raises(ValueError, match="administrative execution-loss receipt differs from original admission"):
        _readback(context)
    assert _unchanged_state(context) == before
    _assert_no_completion_or_debit(context)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_original_generation_keeps_ordinary_blocked_closeout_semantics(
    tmp_path, monkeypatch, capsys, provider,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, replace=False, source_profile=False)
    assert context["current_lease"] == context["original_lease"]
    _reject_without_effect(context, _closeout_args(context), expected_code="refresh_task_lease_unavailable")
    before = _canonical(context)
    before_runs = _runs(context)
    code, closed = context["cli"](*_closeout_args(context, execution_loss=False))
    assert code == 0, closed
    assert "quota_execution_loss_closeout_v0" not in json.dumps(closed)
    after_runs = _runs(context)
    new_names = after_runs.keys() - before_runs.keys()
    assert len(new_names) == 1
    run = json.loads(after_runs[new_names.pop()])
    assert "execution_loss_closeout" not in run
    assert run["blocked_retry"]["schema_version"] == "quota_blocked_retry_v0"
    assert _canonical(context) == before
    assert _lease(context) == context["original_lease"]
    assert _receipt_lines(context) == context["receipt_lines"]
    _assert_no_completion_or_debit(context)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_source_profile_keeps_ordinary_public_refresh_restriction(tmp_path, monkeypatch, capsys, provider):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, replace=False)
    before = _unchanged_state(context)
    with pytest.raises(ProjectRegistryProtocolError,
                       match="^generic registry read rejects lifecycle-only profile source_session_v1; "
                             "use project lifecycle commands$"):
        context["cli"](*_closeout_args(context, execution_loss=False))
    capsys.readouterr()
    assert _unchanged_state(context) == before
    _assert_no_completion_or_debit(context)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unprepared_original_receipt_cannot_certify_execution_loss(tmp_path, monkeypatch, capsys, provider):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, prepared=False)
    readback = _readback(context)
    assert readback is not None and readback.prepared_lease_required is True
    assert readback.prepared_lease is None
    assert not readback.writeback.receipts and not readback.spend.receipts
    _reject_without_effect(context, _closeout_args(context), expected_code="refresh_task_lease_unavailable")


def test_malformed_original_prepared_receipt_or_mixed_profile_cannot_certify_execution_loss(
    tmp_path, monkeypatch, capsys,
):
    context = _fixture(tmp_path, monkeypatch, capsys, "sqlite")
    before = _unchanged_state(context)
    readback = _readback(context)
    source = _canonical(context)
    receipt = json.loads(context["receipt_lines"][-1])
    assert receipt == readback.heartbeat_receipt
    args = build_parser().parse_args(_closeout_args(context))
    pure_input = {
        "schema_version": "execution_loss_closeout_qualification_v0",
        "receipt": receipt,
        "current_todo": next(todo for todo in source["todos"] if todo["todo_id"] == context["todo"]),
        "current_lease": context["current_lease"], "goal_ref": context["goal_ref"],
        "identity": readback.identity.value.as_dict(),
        "control_writer_proof": {"idempotency_key": context["current_lease"]["idempotency_key"],
                                 "expected_version": context["current_lease"]["version"]},
        "progress_observation": normalize_progress_observation(inline_progress_observation(args)),
        "registered_agents": _registered_agents(context),
        "observed_at": runtime_time.now_local_iso(timespec="microseconds"),
        "provider_identity": source["store_identity"], "provider_revision": source["provider_revision"],
    }
    malformed = deepcopy(pure_input)
    original_epoch = malformed["receipt"]["details"]["prepared_lease_epoch"]
    malformed["receipt"]["details"]["prepared_lease_epoch"] = 0
    restored = deepcopy(malformed)
    restored["receipt"]["details"]["prepared_lease_epoch"] = original_epoch
    assert restored == pure_input
    mixed_profile = deepcopy(pure_input)
    mixed_profile.pop("goal_ref")
    module = Path(loopx.__file__).resolve().parent / "control_plane" / "work_items" / "execution_preparation.ts"
    script = (
        "import {qualifyReceiptBoundExecutionLossCloseout as qualify} from "
        + json.dumps(module.as_uri()) + ";"
        "let input='';for await(const part of process.stdin)input+=part;"
        "const variants=JSON.parse(input);const results=variants.map(request=>{"
        "try{return {status:'qualified',qualification:qualify(request)};}"
        "catch(error){return {status:'rejected',diagnostic_code:error.code};}});"
        "process.stdout.write(JSON.stringify(results));"
    )
    child = subprocess.run(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps([pure_input, malformed, mixed_profile]), capture_output=True, text=True,
        check=False, timeout=45,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    accepted, rejected, mixed_rejected = json.loads(child.stdout)
    assert accepted["status"] == "qualified", accepted
    assert accepted["qualification"]["schema_version"] == "quota_execution_loss_closeout_v0"
    assert rejected == {"status": "rejected", "diagnostic_code": "malformed_settlement_state"}
    assert mixed_rejected == {"status": "rejected", "diagnostic_code": "refresh_task_lease_unavailable"}
    assert _unchanged_state(context) == before
