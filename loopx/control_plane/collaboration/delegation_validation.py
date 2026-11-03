"""Host IO for delegation's single typed acceptance/validation plan.

The canonical reader resolves Goal scope; TypeScript selects validation effects.
Python resolves private declarations and executes only those authorized effects.
Original failure and validation-to-settlement recovery use the same Turn journal
and typed decisions; the service retains operation admission, locking and effects.
No validator output or successful declaration read completes a canonical Todo.
"""

import json
import stat
import subprocess
from pathlib import Path

from ...agent_registry import load_goal_from_registry
from ...materials import goal_state_path
from ..agents.workspace_guard import capture_delivery_workspace
from ..effect_runtime import effect_runtime_result
from ..coordination.local_authority import local_authority_is_promoted
from ..turn_driver.lane_fence import turn_lane_liveness
from .inbox import _hash
from . import delegation_results
from ..turn_driver.journal_store import (
    find_loopx_turn_key_by_settlement_identity, load_turn_journal, turn_journal_path,
)
from ..goals.acceptance import (
    inspect_goal_acceptance,
    run_goal_acceptance_validation_effect,
    validation_effect_files_current,
)
from ..todos.completion_validation import resolve_private_completion_validation_declaration


def _observe_bound_workspace(path: Path) -> tuple[str, tuple[int, int, Path] | None]:
    """Observe a directory and its target identity without exposing its path.

    The binding is operator-owned, but a worktree or symlink can disappear or
    change targets while a read-only Turn preview is running.  Identity is a
    host fact; the shared TypeScript preflight still owns the readiness rule.
    """

    try:
        observed = path.stat()
        if not stat.S_ISDIR(observed.st_mode):
            return "not_directory", None
        resolved = path.resolve(strict=True)
        target = resolved.stat()
        if (observed.st_dev, observed.st_ino) != (target.st_dev, target.st_ino):
            return "unavailable", None
        return "available", (observed.st_dev, observed.st_ino, resolved)
    except FileNotFoundError:
        return "missing", None
    except NotADirectoryError:
        return "not_directory", None
    except (OSError, RuntimeError):
        return "unavailable", None


def inspect_binding(service, binding_id: str, *, source_request_id: str | None = None) -> dict[str, object]:
    binding = service.binding(binding_id, require_active=True)
    # Host filesystem facts only; the shared TS owner projects readiness.
    # Do not expose a path/error body or probe authority in a missing cwd.
    workspace_path = Path(binding["workspace"])
    workspace_state, workspace_identity = _observe_bound_workspace(workspace_path)

    def workspace_fault(state: str) -> dict[str, object]:
        return effect_runtime_result("collaboration.delegation.preflight", {
            "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
            "workspace": {"state": state},
            "authority": None, "preview": None, "acceptance": None,
            "validation_files_current": False,
        })

    def recheck_workspace() -> dict[str, object] | None:
        if service.binding(binding_id, require_active=True) != binding:
            raise ValueError("delegation preflight source changed; retry inspection")
        current_state, current_identity = _observe_bound_workspace(workspace_path)
        if current_state == "available" and current_identity == workspace_identity:
            return None
        # A replacement directory is not the directory whose authority
        # and acceptance were observed at entry.  No path or error leaks.
        return workspace_fault(
            current_state if current_state != "available" else "unavailable"
        )

    def authority_fault(exc: OSError | ValueError) -> dict[str, object]:
        fault = recheck_workspace()
        if fault is not None:
            return fault
        # Authority admission is a readiness observation, not a reason for
        # inspection to invent a provider launch or collapse into a raw CLI error.
        try:
            promoted = local_authority_is_promoted(
                runtime_root=service.root,
                goal_id=service.goal_id,
            )
        except (OSError, RuntimeError):
            # A mode readback failure is an unavailable authority, never a
            # reason to suggest that a fresh promotion should be attempted.
            promoted = True
        return effect_runtime_result("collaboration.delegation.preflight", {
            "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
            "workspace": {"state": workspace_state},
            "authority": {
                "ready": False,
                "reason": str(exc),
                "state": "unavailable" if promoted else "promotion_required",
                "next_action": (
                    "repair_canonical_authority"
                    if promoted
                    else "preview_reviewed_goal_authority_promotion"
                ),
            },
            "preview": None, "acceptance": None, "validation_files_current": False,
        })

    if workspace_state != "available":
        if service.binding(binding_id, require_active=True) != binding:
            raise ValueError("delegation preflight source changed; retry inspection")
        return workspace_fault(workspace_state)
    assert workspace_identity is not None
    try:
        acceptance = capture(service, binding)
    except (OSError, ValueError) as exc:
        return authority_fault(exc)
    fault = recheck_workspace()
    if fault is not None:
        return fault
    operation = "inspect-" + _hash(binding_id)[:32]
    arguments = ["turn", "run-once", "--goal-id", service.goal_id,
                 "--agent-id", binding["agent_id"],
                 *(["--todo-id", binding["todo_id"]] if binding["todo_id"] is not None else []),
                 "--turn-instance-id", operation, *service._execution_arguments(binding, operation)]
    if binding["todo_id"] is None:
        if source_request_id is None:
            return effect_runtime_result("collaboration.delegation.preflight", {
                "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
                "workspace": {"state": workspace_state}, "authority": {"ready": True},
                "request_required": True})
        arguments += ["--delegation-context-json", json.dumps({
            "requester_agent_id": service.agent_id, "execution_config": str(service.config),
            "source_request_id": source_request_id, "binding_id": binding["id"]})]
    # Host arguments are operator-owned, but inspection must stay read-only
    # even when they contain an abbreviated execution flag or a selector.
    from ...cli_runtime import _build_selected_parser, _SelectedParseRejected

    try:
        selected = _build_selected_parser("turn").parse_args(arguments)
    except (SystemExit, _SelectedParseRejected) as exc:
        raise ValueError("invalid delegation Turn arguments") from exc
    workspace = workspace_identity[2]
    selected_project = Path(selected.project)
    if not selected_project.is_absolute():
        selected_project = workspace / selected_project
    if (selected.execute or selected.resume_turn_key
            or (selected.goal_id, selected.agent_id, selected.todo_id, selected.turn_instance_id)
            != (service.goal_id, binding["agent_id"], binding["todo_id"], operation)
            or selected_project.resolve() != workspace):
        fault = recheck_workspace()
        if fault is not None:
            return fault
        raise ValueError("delegation inspection cannot execute or retarget bound work")
    try:
        preview = service._cli(binding, *arguments)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        fault = recheck_workspace()
        if fault is not None:
            return fault
        raise
    fault = recheck_workspace()
    if fault is not None:
        return fault
    if preview.get("status") != "preview" and "selection_rejection" not in preview:
        raise ValueError(f"delegation Turn preflight unavailable: {preview.get('error') or preview.get('status')}")
    try:
        current = capture(service, binding)
    except (OSError, ValueError) as exc:
        # Do not return the first acceptance or an already-read preview
        # when current authority cannot be confirmed at the final fence.
        return authority_fault(exc)
    fault = recheck_workspace()
    if fault is not None:
        return fault
    if acceptance != current or service.binding(binding_id, require_active=True) != binding:
        raise ValueError("delegation preflight source changed; retry inspection")
    return effect_runtime_result("collaboration.delegation.preflight", {
        "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
        "workspace": {"state": workspace_state},
        "authority": {"ready": True, "reason": None},
        "preview": preview, "acceptance": acceptance["plan"],
        "validation_files_current": acceptance["files_current"],
        "lane": {"state": turn_lane_liveness(service._lane_target(binding))["state"]},
    })



def capture(service, binding: dict) -> dict:
    basis = inspect_goal_acceptance(
        registry_path=service.registry, runtime_root=str(service.root),
        goal_id=service.goal_id, agent_id=binding["agent_id"], todo_id=binding["todo_id"],
        validation_workspace_path=Path(binding["workspace"]),
    )
    if binding["todo_id"] is None:
        plan = effect_runtime_result("collaboration.delegation.validation_plan", {
            "binding": binding, "basis": basis, "declaration": None,
        })
    else:
        todo = basis.get("todo")
        if not isinstance(todo, dict):
            raise ValueError("delegation canonical Todo unavailable")
        goal = load_goal_from_registry(service.registry, service.goal_id)
        state_file = goal_state_path(goal) if goal is not None else None
        if state_file is None:
            raise ValueError("delegation validation workspace unavailable")
        declaration = resolve_private_completion_validation_declaration(
            canonical_todo=todo, state_file=state_file, runtime_root=service.root,
            registry_path=service.registry, goal_id=service.goal_id,
            todo_id=binding["todo_id"], role=todo.get("role"), persist_if_resolved=False,
        )
        plan = effect_runtime_result("collaboration.delegation.validation_plan", {
            "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
            "basis": basis, "declaration": declaration,
        })
    # The configured worker worktree is the host-owned execution context. The
    # typed plan still owns which validators run; a path-free snapshot lets the
    # ordinary completion runner verify cross-repository Todo validators.
    workspace = Path(binding["workspace"])
    delivery_workspace = (
        capture_delivery_workspace(workspace, peer_independent_worktree_required=True)
        if any(effect.get("task_repository") for effect in plan["effects"])
        else None
    )
    files_current = plan["state"] == "ready" and validation_effect_files_current(
        effects=plan["effects"], registry_path=service.registry, goal_id=service.goal_id,
        delivery_workspace=delivery_workspace, validation_workspace_path=workspace,
    )
    return {"basis": basis, "plan": plan, "files_current": files_current,
            "delivery_workspace": delivery_workspace}


def validate(service, binding: dict) -> dict:
    before = capture(service, binding)
    if before["plan"]["state"] != "ready" or not before["files_current"]:
        raise ValueError("delegation task acceptance rejected")
    if binding["todo_id"] is None:
        from ..runtime.validation_command import run_caller_validation
        results = [run_caller_validation(Path(binding["workspace"]),
            validation_argv=effect["validation_argv"],
            validation_command=effect["validation_command"],
            validation_label=effect["validation_label"],
            timeout_seconds=effect["validation_timeout_seconds"])
            for effect in before["plan"]["effects"]]
    else:
        results = [run_goal_acceptance_validation_effect(
            effect=effect, registry_path=service.registry, goal_id=service.goal_id,
            delivery_workspace=before["delivery_workspace"],
            validation_workspace_path=Path(binding["workspace"]),
        ) for effect in before["plan"]["effects"]]
    after = capture(service, binding)
    # The TS plan binds this task's work, claim/lifecycle and current rules.
    # A whole-Goal provider revision may advance for unrelated peer progress.
    if (after["plan"] != before["plan"] or not after["files_current"]
            or after["delivery_workspace"] != before["delivery_workspace"]):
        raise ValueError("delegation task acceptance changed during validation")
    if not all(result["passed"] for result in results):
        raise ValueError("delegation task acceptance rejected")
    return after


def task_failure(service, row, binding):
    turn_key = matching_turn_key(service, row, binding)
    if not turn_key:
        return None
    journal = load_turn_journal(turn_journal_path(service.root, goal_id=service.goal_id, turn_key=turn_key))
    if not journal or journal.get("result_kind") != "validation_failed":
        return None
    return effect_runtime_result("turn.task_validation_failure", {
        "result_kind": journal.get("result_kind"), "status": journal.get("status"),
        "receipt": journal.get("receipt"), "validation": journal.get("task_validation"),
        **({"validation_stage": journal["validation_stage"]} if "validation_stage" in journal else {}),
    })["failure"]


def committed_revalidation_key(service, row, binding):
    """A retained recheck intent can outlive the original failed journal."""
    intent = row.get("validation_failure")
    if not isinstance(intent, dict):
        return None
    turn_key = matching_turn_key(service, row, binding)
    if not turn_key or intent.get("turn_key") != turn_key:
        return None
    journal = load_turn_journal(turn_journal_path(service.root, goal_id=service.goal_id, turn_key=turn_key))
    if journal and journal.get("status") == "committed" and journal.get("result_kind") == "validated_progress":
        return turn_key
    return None


def record_revalidation_result(service, path, row, binding, result):
    from ..turn_driver.loop_controller import ValidatedTurnReceipt
    from ..effect_program import SettlementIdentity
    subject = ordinary_request_subject(service, row, binding)
    identity = (SettlementIdentity(goal_id=service.goal_id, agent_id=binding["agent_id"],
        todo_id="", request_subject=subject, turn_instance_id=service._turn_instance_id(row))
        if subject is not None else None)
    receipt = ValidatedTurnReceipt.from_execution(result, request_identity=identity)
    intent = row.get("validation_failure") or {}
    decision = effect_runtime_result("collaboration.delegation.revalidated", {
        "from": row["status"],
        "original_task_failure": intent.get("stage") == "task_postcondition"
            and intent.get("turn_key") == receipt.turn_key
            and receipt.turn_key == matching_turn_key(service, row, binding)
            and receipt.lineage == {"goal_id": service.goal_id, "agent_id": binding["agent_id"],
                "todo_id": binding["todo_id"] or "",
                **({"request_binding_id": identity.binding_id} if identity is not None else {})},
        "committed_progress": receipt.result_kind.value == "validated_progress",
        "host_reinvoked": result.get("effects", {}).get("host_invoked"),
    })
    service._record_turn_result(path, row, result, publish=False)
    row["status"] = decision["status"]
    row.pop("error", None)
    service._fenced_write(path, row)


def record_turn_result(
    service, path: Path, row: dict, result: dict, *, publish: bool = True,
    already_locked: bool = False, unstarted_turn: bool = False,
) -> None:
    """Persist native result facts through the existing typed observation/fence."""
    turn_key = result.get("resume_turn_key")
    if turn_key:
        # Validate the public shape before persisting an address supplied
        # by the CLI boundary.
        turn_journal_path(service.root, goal_id=service.goal_id, turn_key=turn_key)
    if publish:
        # Only native receipt facts needed by the typed status owner cross
        # this boundary. Evidence, settlement details and Host configuration
        # remain with their original result instead of entering another RPC.
        observation = {key: result.get(key) for key in (
            "schema_version", "mode", "ok", "dry_run", "replayed", "status",
            "reason", "result_kind", "quota_slot_spend_count", "effects",
        )}
        host = result.get("host")
        observation["host"] = {"executable": host.get("executable")} if isinstance(host, dict) else None
        admission = result.get("admission")
        if isinstance(admission, dict):
            observation["admission"] = {key: admission.get(key) for key in (
                "admitted", "reserved", "resumed", "reason", "next_eligible_at_ms",
                "min_interval_minutes", "pre_model_admission",
            )}
        decision = effect_runtime_result("collaboration.delegation.observe", {
            "from": row["status"], "to": "turn_returned",
            "turn_execution": observation, "unstarted_turn": unstarted_turn,
        })
        row["status"] = decision["status"]
        if row["status"] == "prepared":
            row["error"] = result["reason"]
    if turn_key and (not publish or row["status"] != "prepared"):
        row["turn_key"] = turn_key
    row["turn_result"] = {
        key: result.get(key)
        for key in (
            "status",
            "result_kind",
            "resume_turn_key",
            "reason",
            "host_failure",
            "error",
        )
    }
    if already_locked:
        service._fenced_write_locked(path, row)
    else:
        service._fenced_write(path, row)


def ordinary_request_subject(service, row: dict, binding: dict, *, require_active: bool = True) -> dict | None:
    if binding["todo_id"] is not None:
        return None
    identity = row["identity"]
    from .inbox import _entry
    from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle
    with collaboration_goal_scope(service.registry, goal_id=service.goal_id, agents=(binding["agent_id"],),
            caller_goal_ref=service._caller_goal_ref(), require_active=require_active) as scope:
        try:
            entry = _entry(service.root, service.goal_id, binding["agent_id"],
                           identity.get("source_request_id") or identity["request_id"], scope=scope)
        except OSError as exc:
            raise ValueError("delegation request source unavailable for the captured Goal instance") from exc
        decide_collaboration_lifecycle(scope, operation="read_record" if require_active else "history_inspect", record=entry)
        return effect_runtime_result("collaboration.delegation.request_subject", {
            "entry": entry, "binding": binding,
            **({"expected": identity["request_subject"]} if "request_subject" in identity else {}),
        })


def matching_turn_key(service, row: dict, binding: dict) -> str | None:
    """Find only the journal bound to this operation's settlement identity."""

    return find_loopx_turn_key_by_settlement_identity(
        service.root,
        goal_id=service.goal_id,
        agent_id=binding["agent_id"],
        todo_id=binding["todo_id"],
        turn_instance_id=service._turn_instance_id(row),
        request_subject=ordinary_request_subject(service, row, binding, require_active=False),
    )


def validated_turn_journal(service, row: dict, binding: dict) -> dict | None:
    turn_key = matching_turn_key(service, row, binding)
    if turn_key is None:
        return None
    journal = load_turn_journal(
        turn_journal_path(service.root, goal_id=service.goal_id, turn_key=turn_key)
    )
    if journal is None:
        return None
    phases = journal.get("completed_phases")
    validation = journal.get("task_validation")
    host_result = journal.get("host_result")
    if (
        journal.get("status") != "in_progress"
        or journal.get("result_kind") != "validated_progress"
        or phases != ["host_execute", "typed_result", "validation"]
        or not isinstance(validation, dict)
        or validation.get("ok") is not True
        or not isinstance(host_result, dict)
        or host_result.get("turn_key") != turn_key
        or host_result.get("result_kind") != "validated_progress"
    ):
        return None
    row["turn_key"] = turn_key
    return journal


def recover_validated_settlement(
    service, path: Path, row: dict, binding: dict
) -> bool:
    """Reopen only an exact, independently validated settlement boundary."""

    committed_key = committed_revalidation_key(service, row, binding)
    if committed_key is not None:
        service._bound(row, require_active=True)
        delegation_results.require_dependencies(service, binding, delegation_results.operation_brief(service, row))
        # Read the original committed result through the Turn replay owner;
        # do not fabricate durable effect flags from a saved status string.
        result = service._cli(binding, "turn", "run-once", "--goal-id", service.goal_id,
            "--agent-id", binding["agent_id"], "--resume-turn-key", committed_key,
            *service._execution_arguments(binding, row["identity"]["operation_id"]), "--execute",
            timeout=binding["timeout_seconds"] + 60,
            host_record=service._host_process_record(path))
        record_revalidation_result(service, path, row, binding, result)
        return True
    journal = validated_turn_journal(service, row, binding)
    if journal is None:
        return False
    decision = effect_runtime_result(
        "collaboration.delegation.recover_validated_settlement",
        {
            "from": row["status"],
            "identity_matched": True,
            "journal_status": journal.get("status"),
            "result_kind": journal.get("result_kind"),
            "completed_phases": journal.get("completed_phases"),
            "task_validation_passed": (
                isinstance(journal.get("task_validation"), dict)
                and journal["task_validation"].get("ok") is True
            ),
        },
    )
    row["status"] = decision["status"]
    row["turn_result"] = {
        "status": journal.get("status"),
        "result_kind": journal.get("result_kind"),
        "resume_turn_key": row["turn_key"],
        "reason": "validated Turn settlement requires same-operation recovery",
        "host_failure": None,
        "error": None,
    }
    row.pop("error", None)
    service._fenced_write(path, row)
    return True
