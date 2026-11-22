"""A lost execution generation can close only its original Turn, without spend."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import sys

import pytest
from loopx.agent_registry import registered_agent_ids_for_goal

from canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime
from codex_checkpoint_fixture import read_cli_policy_payload
from loopx.cli import main as cli_main
from loopx.control_plane.coordination import local_authority
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.control_plane.goals.source_session_registration import (
    FreshSourceSessionRegistration,
    configure_source_creation_goal,
    register_fresh_source_session_project,
)
from loopx.control_plane.quota import settlement
from loopx.control_plane.projects.registry_codec import load_project_registry
from loopx.control_plane.quota.effect_program import SettlementIdentity
from loopx.control_plane.quota.execution_preparation import prepared_lease_receipt_fields
from loopx.control_plane.quota.heartbeat_receipt import (
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
from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path
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
@pytest.mark.parametrize("source_profile", [False, True], ids=["legacy", "exact-source"])
def test_closeout_preserves_the_original_turn_and_releases_the_next_guard(
    tmp_path, monkeypatch, capsys, provider, source_profile,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=source_profile)
    original = _readback(context)
    _assert_original_prepared_generation(context, original)
    assert not original.writeback.receipts and not original.spend.receipts
    if not source_profile:
        # A fresh guard recognizes the generation change before attempting to
        # re-enter the original guard. It offers only the administrative route.
        code, recovery = context["cli"](
            "quota", "should-run", "--context-delivery", "off", "--codex-app",
            "--goal-id", context["goal_id"], "--agent-id", OWNER, "--turn-instance-id", "needs-recovery",
        )
        assert code == 0, recovery
        debt = recovery["unsettled_host_turn_recovery"]
        assert debt["repair"] == "execution_loss_closeout"
        assert debt["prior_turn_instance_id"] == TURN
        assert debt["execution_loss"]["original_prepared_epoch"] == context["original_lease"]["lease_epoch"]
        assert debt["execution_loss"]["current_lease_epoch"] == context["current_lease"]["lease_epoch"]
        assert recovery["execution_obligation"]["delivery_allowed"] is False
        cli_channel = recovery["interaction_contract"]["cli_channel"]
        assert cli_channel["spend_allowed_now"] is False
        assert any("--execution-loss-closeout" in cmd and "--dry-run" in cmd
                   for cmd in cli_channel["next_cli_actions"])
    argv = _closeout_args(context)
    before = _unchanged_state(context)
    code, preview = context["cli"](*argv, "--dry-run")
    assert code == 0 and preview["would_close_without_spend"] is True, preview
    assert preview["appended"] is False and preview["replay_phase"] == "open"
    assert _unchanged_state(context) == before

    code, closed = context["cli"](*argv)
    assert code == 0 and closed["appended"] is True, closed
    assert closed["settlement_identity"] == original.identity.value.as_dict()
    assert closed["settlement_progress"]["state"] == "settled"
    assert closed["settlement_progress"]["no_spend"] is True
    assert _canonical(context) == before[0] and context["state"].read_bytes() == before[1]
    assert _receipt_lines(context) == context["receipt_lines"]
    assert _readback(context).heartbeat_receipt == original.heartbeat_receipt
    _assert_no_completion_or_debit(context)

    before = _unchanged_state(context)
    code, replay = context["cli"](*argv)
    assert code == 0 and replay["idempotent_replay"] is True and replay["appended"] is False, replay
    assert _unchanged_state(context) == before
    if not source_profile:
        fresh_args = [*(_guard_args(context))]
        fresh_args[fresh_args.index("--turn-instance-id") + 1] = "next-real-turn"
        code, fresh = context["cli"](*fresh_args)
        assert code == 0 and fresh["normal_delivery_allowed"] is True, fresh
        assert fresh.get("unsettled_host_turn_recovery") is None
        code, debited = context["cli"](
            "quota", "spend-slot", *_binding(context), "--slots", "1", "--source", "heartbeat", "--execute",
        )
        assert code == 0 and debited["appended"] is False, debited
        _assert_no_completion_or_debit(context)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_rejects_same_generation_and_business_side_effects(tmp_path, monkeypatch, capsys, provider):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, replace=False, source_profile=False)
    code, recovery = context["cli"](
        "quota", "should-run", "--context-delivery", "off", "--codex-app",
        "--goal-id", context["goal_id"], "--agent-id", OWNER, "--turn-instance-id", "same-generation",
    )
    assert code == 0, recovery
    assert recovery["unsettled_host_turn_recovery"]["repair"] == "resume_prior_turn"
    args = _closeout_args(context)
    _reject_without_effect(context, args, expected_code="refresh_task_lease_unavailable")
    _reject_without_effect(context, [*args, "--next-action", "Promote the replacement's business work."])
    _reject_without_effect(context, [*args, "--completion-todo-id", context["todo"]])
    _reject_without_effect(context, [*args, "--vision-state", "active"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_rejects_another_owner_and_stale_proof(tmp_path, monkeypatch, capsys, provider):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    _reject_without_effect(context, _closeout_args(context, lease=context["original_lease"]),
                           expected_code="refresh_task_lease_unavailable")
    args = _closeout_args(context)
    args[args.index("--agent-id") + 1] = PEER
    _reject_without_effect(context, args)
