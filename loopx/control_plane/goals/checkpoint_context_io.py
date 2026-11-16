"""Source/receipt I/O only; checkpoint_read_context.ts owns decision semantics."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import ExitStack, contextmanager, nullcontext
import hashlib
import json
import shlex
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ...file_lock import cross_runtime_lock_witness, exclusive_cross_runtime_file_lock, exclusive_run_index_lock
from ...history import load_index, load_registry
from ...paths import resolve_runtime_root
from ..runtime.document_io import atomic_write_private_json as atomic_write_json
from ...runtime import validate_goal_id_path_segment
from ..coordination.legacy_writer_fence import legacy_coordination_todo_lock_path
from ..coordination.shadow_management import shadow_maintenance_lock_target, require_shadow_primary_write_allowed
from ..effect_runtime import effect_runtime_result, EffectRuntimeRejected
from ..quota.accounting_admission import quota_accounting_admission
from ..quota.settlement import QuotaSettlementReadback, SettlementIdentity, read_heartbeat_settlement
from ..runtime.time import chronology_key
from ..todos.active_state_todo_parser import parse_todo_source
from ..todos.machine_region import find_todo_source_regions
from .active_state_metadata import split_state_frontmatter
from .goal_frontier import latest_agent_vision_from_runs
from .first_party_host_admission import load_first_party_registry_metadata


class CheckpointReadContextRejected(ValueError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(result["error"])
        self.code = result["error_code"]
        self.payload = {"checkpoint_read_context": result}


def _checkpoint_effect(method: str, request: dict[str, Any]) -> Any:
    try:
        # The complete Goal prose and archived Todo basis can exceed the 2 MiB
        # RPC wire. Only this locked local checkpoint path opts into the exact,
        # digest-bound same-UID snapshot transport; default effects stay bounded.
        small_policy = method == "goal.checkpoint_read_context.work_binding" and request.get("phase") in (
            "admission", "policy", "direction_policy", "source_recovery_admission")
        return effect_runtime_result(method, request,
            large_local_snapshot=not small_policy and method not in ("goal.checkpoint_read_context.inspect_attempt",
                                                "goal.checkpoint_read_context.inspect_history",
                                                "turn.first_delivery.observe"))
    except EffectRuntimeRejected as error:
        raise CheckpointReadContextRejected({"ok": False, "error": str(error),
            "error_code": error.diagnostic_code, "reread_required": False}) from error


def _resolve(runtime_root: Path, **request: Any) -> dict[str, Any]:
    result = _checkpoint_effect("goal.checkpoint_read_context.resolve", {
        "runtime_root": str(runtime_root.resolve()), **request,
    })
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise RuntimeError("invalid typed checkpoint read context result")
    if not result["ok"]:
        raise CheckpointReadContextRejected(result)
    return result


def _receipt_path(root: Path, identity: SettlementIdentity, purpose: str = "supplement_checkpoint") -> Path:
    key = identity.effect_id + (":delivery_result" if purpose == "delivery_result" else "")
    digest = hashlib.sha256(key.encode()).hexdigest()
    return root / "goals" / identity.goal_id / "checkpoint-contexts" / f"{digest}.json"


def _read_context_receipt(path: Path) -> dict[str, Any]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError("checkpoint receipt must be an object")
    return receipt


def require_checkpoint_source_recovery(readback: Any) -> None:
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "source_recovery_admission", "heartbeat_receipt": readback.heartbeat_receipt,
    })
    if result.get("ok") is not True:
        raise CheckpointReadContextRejected(result)


def default_result_freshness_policy(root: Path, goal_id: str, todo_id: str | None,
                                   goal_ref: Mapping[str, Any] | None = None) -> str | None:
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "admission", "todo_id": todo_id, "goal_ref": goal_ref,
        "runtime_root": str(root.expanduser().resolve()), "goal_id": goal_id,
    })
    if "source_failure" in result:
        from ..coordination.local_authority import validate_canonical_todo_read

        validate_canonical_todo_read(result["source_failure"], goal_id=goal_id)
    return result["policy"]


def result_work_required(readback: Any) -> bool:
    return _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "policy", "heartbeat_receipt": readback.heartbeat_receipt,
    })["required"]


def checkpoint_continuation_required(readback: Any) -> bool:
    return _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "direction_policy", "heartbeat_receipt": readback.heartbeat_receipt,
    })["required"]


def replan_checkpoint_cli_actions(actions: list[str], readback: Any, *, command_prefix: str) -> list[str]:
    """Render the admitted checkpoint phases; the typed receipt owns policy.

    The generic replan command describes semantic authoring, but a default Turn
    must retain its result before reading and authoring that direction. Historical
    receipts and lifecycle reentry commands keep their original projection.
    """
    if not actions:
        return actions
    tokens = shlex.split(actions[0])
    prefix = [*shlex.split(command_prefix), "--format", "json"]
    command_at = len(prefix)
    if tokens[:command_at + 1] != [*prefix, "refresh-state"] or not checkpoint_continuation_required(readback):
        return actions
    arguments = tokens[command_at + 1:]
    initial = []
    context = [*prefix, "checkpoint-context", "--purpose", "supplement_checkpoint",
               "--max-page-output-bytes", "64000"]
    context_options = {"--goal-id", "--goal-instance-id", "--agent-id", "--todo-id",
                       "--replan-obligation-id", "--turn-instance-id", "--project", "--state-file"}
    decision_options = {"--agent-vision-json", "--vision-unchanged-reason"}
    at = 0
    while at < len(arguments):
        option = arguments[at]
        if option in context_options | decision_options:
            if at + 1 == len(arguments):
                raise ValueError(f"checkpoint command requires a value for {option}")
            pair = arguments[at:at + 2]
            if option in context_options:
                context.extend(pair)
                initial.extend(pair)
            at += 2
        else:
            initial.append(option)
            at += 1
    direction = [*tokens, "--checkpoint-read-context", "<read-context-id>"]
    if not any(option in arguments for option in decision_options):
        direction.extend(["--agent-vision-json",
                          "<path-to-evidence-linked-goal-vision-replan-contract-v0.json>"])
    return [shlex.join([*prefix, "refresh-state", *initial]), shlex.join(context),
            shlex.join(direction), *actions[1:]]


def _render_checkpoint_host_command(argv: list[str]) -> str:
    from ...project_prompt import host_command, shell_arg_for_host

    program, *args = argv
    return " ".join([host_command(program), *(shell_arg_for_host(arg) for arg in args)])


def pending_checkpoint_cli_actions(readback: Any, *, command_prefix: str,
                                   goal_ref: Mapping[str, Any] | None = None,
                                   sync_global: bool = True,
                                   suppress_external_sinks: bool = False,
                                   host_shell: bool = False) -> list[str]:
    """Render the typed owner's pending direction through existing CLI phases.

    Omitted mutation/progress options retain the immutable original result.
    Only the post-result read and the Agent's fresh decision are new work.
    """
    from ..quota.effect_program import (
        SettlementStepKind, build_turn_scoped_cli_settlement_plan, settlement_step_command,
    )

    identity = readback.identity.value
    plan = build_turn_scoped_cli_settlement_plan(
        goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
        replan_obligation_id=identity.replan_obligation_id, turn_instance_id=identity.turn_instance_id,
        command_prefix=command_prefix, scoped_cli_args=f" --agent-id {shlex.quote(identity.agent_id)}",
        lifecycle_actor_args="", goal_ref=goal_ref,
        quota_spend_source=readback.progress["quota_spend_source"],
    ).as_dict()
    # A supplement need not repeat the original outcome's optional fields.
    # Fill required command fields from that verified run, never new defaults.
    run = readback.writeback_run
    writeback = settlement_step_command(plan, SettlementStepKind.DURABLE_WRITEBACK)
    template = shlex.split(writeback)
    scale = run.get("delivery_batch_scale")
    if scale is None:
        at = template.index("--delivery-batch-scale")
        del template[at:at + 2]
    tokens = [str({"<validated_progress>": run["classification"], "<scale>": scale,
                   "<outcome>": run["delivery_outcome"]}.get(token, token)) for token in template]
    runtime_projection = run.get("runtime_projection_route")
    if not sync_global or goal_ref is not None or (
        isinstance(runtime_projection, Mapping) and runtime_projection.get("projection_enabled") is False
    ):
        tokens.append("--no-global-sync")
    if (suppress_external_sinks or goal_ref is not None or
            (readback.external_delivery or {}).get("error_code") == "external_delivery_resume_required"):
        tokens.append("--suppress-external-sinks")
    actions = replan_checkpoint_cli_actions([
        shlex.join(tokens), settlement_step_command(plan, SettlementStepKind.QUOTA_SPEND),
    ], readback, command_prefix=command_prefix)[1:]
    if host_shell:
        return [_render_checkpoint_host_command(shlex.split(action)) for action in actions]
    return actions


def project_completed_first_delivery_cli(channel: dict[str, Any], identity: SettlementIdentity, *,
                                         direction_context: dict[str, object], read_context_id: str) -> None:
    """Project an already issued post-result read through the original settlement plan."""
    from ..quota.effect_program import SettlementStepKind, settlement_step_command

    plan = channel.get("settlement_plan")
    writeback = settlement_step_command(plan, SettlementStepKind.DURABLE_WRITEBACK)
    actions = channel["next_cli_actions"]
    if not writeback or plan["identity"] != identity.as_dict() or writeback not in actions:
        raise ValueError("First delivery recovery requires the original settlement writeback command")
    writeback_step = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    tokens = shlex.split(writeback_step.pop("semantic_closeout_command_template", None) or writeback)
    if "--agent-vision-json" in tokens:
        at = tokens.index("--agent-vision-json")
        if tokens[at + 1] == "<file>":
            del tokens[at:at + 2]
    if "--delivery-boundary" in tokens:
        tokens[tokens.index("--delivery-boundary") + 1] = "semantic_closeout"
    else:
        tokens.extend(["--delivery-boundary", "semantic_closeout"])
    tokens.extend(["--first-delivery", "--checkpoint-read-context", read_context_id])
    command = shlex.join(tokens)
    writeback_step["command_template"] = command
    writeback_step["checkpoint_context"] = direction_context
    writeback_step["precondition"] = (
        "The original Todo completion is verified. Read this step's checkpoint_context, including "
        "any deferred basis in result_details, before judging the direction. Supply a fresh decision "
        "with --agent-vision-json or the inline vision options. Keep the original result and Turn."
    )
    channel["next_cli_actions"] = [command if action == writeback else action for action in actions]


def _persist_result_work_witness(root: Path, identity: SettlementIdentity, receipt: Mapping[str, Any]) -> None:
    """Prepare private bytes; only the native owner can select their digest."""
    if receipt.get("result_only") is not True:
        return
    read_id = receipt.get("read_context_id")
    if not isinstance(read_id, str) or not read_id:
        raise ValueError("original work snapshot requires its read identity")
    name = hashlib.sha256(f"{identity.effect_id}:work-witness:{read_id}".encode()).hexdigest()
    path = _receipt_path(root, identity, "delivery_result").parent / "work-witnesses" / f"{name}.json"
    # Validation can finish before completion. Once an attempt is published it
    # must never be replaced by a snapshot from before that attempt.
    if path.exists() and _read_context_receipt(path).get("commit_attempt") is not None:
        return
    atomic_write_json(path, dict(receipt))


def _recover_result_work_receipt(root: Path, identity: SettlementIdentity,
                                 goal_ref: Mapping[str, Any] | None = None, *,
                                 index_locked: bool = False) -> dict[str, Any]:
    path = _receipt_path(root, identity, "delivery_result")
    try:
        return _read_context_receipt(path)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    index = root / "goals" / identity.goal_id / "runs" / "index.jsonl"
    with (nullcontext() if index_locked else
          exclusive_run_index_lock(index, operation="recover-original-result-work")):
        with exclusive_cross_runtime_file_lock(path, operation="recover-original-result-work"):
            # A valid current file is authoritative even if a stale backup exists.
            try:
                return _read_context_receipt(path)
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            restored = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
                "phase": "recover", "runtime_root": str(root.resolve()), "identity": identity.as_dict(),
                **({"goal_ref": dict(goal_ref)} if goal_ref is not None else {}),
            })
            if restored.get("ok") is not True:
                raise CheckpointReadContextRejected(restored)
            atomic_write_json(path, restored["receipt"])
            return restored["receipt"]


def read_retained_result_for_replay(
    runtime_root: Path, identity: SettlementIdentity, *,
    index_sha256: str, goal_ref: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project the original indexed result under the refresh settlement lock."""
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "recover", "runtime_root": str(runtime_root.resolve()), "identity": identity.as_dict(),
        "replay_index_sha256": index_sha256,
        **({"goal_ref": dict(goal_ref)} if goal_ref is not None else {}),
    })
    if result.get("ok") is not True:
        raise CheckpointReadContextRejected(result)
    return result["replay_result"]


def result_work_read_id(root: Path, identity: SettlementIdentity) -> str:
    """Restore the admitted work token; a writeback must never mint a new read."""
    try:
        # The sole caller is refresh-state, inside quota_accounting_admission.
        receipt = _recover_result_work_receipt(root, identity, index_locked=True)
        token = receipt.get("read_context_id")
        if isinstance(token, str) and token:
            return token
    except (OSError, ValueError):
        pass
    raise CheckpointReadContextRejected({
        "ok": False, "error_code": "delivery_work_context_required", "reread_required": True,
        "error": "Restore delivery_result context for the original Turn and revalidate the retained progress before retrying.",
    })


def result_work_context_exists(root: Path, identity: SettlementIdentity) -> bool:
    return _receipt_path(root, identity, "delivery_result").exists()


def restore_default_turn_id(*, runtime_root: Path, registry_path: Path, goal_id: str,
                            agent_id: str, todo_id: str | None, pending_only: bool) -> str | None:
    from .first_party_host_admission import capture_first_party_host_goal_ref
    from ..quota.settlement import QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA
    todo = None
    if not pending_only and todo_id:
        from ...todos import list_goal_todos

        todo = list_goal_todos(registry_path=registry_path, goal_id=goal_id, todo_id=todo_id,
            runtime_root_arg=str(runtime_root)).get("todo")
    goal_ref = capture_first_party_host_goal_ref(registry_path=registry_path, goal_id=goal_id)
    with quota_accounting_admission(runtime_root=runtime_root, registry_path=registry_path,
        goal_id=goal_id, goal_ref=goal_ref, operation="default-result-work-read", lock_legacy_index=False) as admission:
        request = {
            "schema_version": QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA, "runtime_root": str(runtime_root.resolve()),
            "goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id, "turn_instance_id": None,
            "infer_turn_instance_id": False, "allow_unbound_binding": False, "pending_only": pending_only, "todo": todo,
            **({"goal_ref": goal_ref, "source_admission": admission} if goal_ref is not None else {}),
        }
        try:
            result = effect_runtime_result("quota.settlement.default_result_work", request)
        except EffectRuntimeRejected as error:
            raise CheckpointReadContextRejected({"ok": False, "error": str(error),
                "error_code": error.diagnostic_code, "reread_required": False}) from error
    if result.get("ok") is not True:
        raise CheckpointReadContextRejected(result)
    return result["turn_instance_id"]


def result_work_context(*, registry_path: Path, runtime_root: Path,
                        identity: SettlementIdentity, reread: bool = False,
                        goal_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Restore original work input; only explicit revalidation may replace it."""
    path = _receipt_path(runtime_root, identity, "delivery_result")
    receipt = None
    if not reread and path.exists():
        receipt = _recover_result_work_receipt(runtime_root, identity, goal_ref)
    if receipt is None:
        try:
            # A normal fresh admission uses the original read owner directly.
            # It cannot mint a replacement after an original Todo completion.
            return read_checkpoint_context(registry_path=registry_path, runtime_root_override=str(runtime_root),
                goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
                turn_instance_id=identity.turn_instance_id, purpose="delivery_result", goal_ref=goal_ref,
                restore_issued_for=identity)
        except CheckpointReadContextRejected as error:
            if reread or error.code != "delivery_result_already_committed":
                raise
            receipt = _recover_result_work_receipt(runtime_root, identity, goal_ref)
    if receipt.get("commit_attempt") is not None:
        index = runtime_root / "goals" / identity.goal_id / "runs/index.jsonl"
        with exclusive_run_index_lock(index, operation="restore-result-work"):
            _checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
                "runtime_root": str(runtime_root.resolve()), "identity": identity.as_dict(),
                "purpose": "delivery_result", "goal_ref": goal_ref,
            })
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "restore", "identity": identity.as_dict(), "receipt": receipt, "goal_ref": goal_ref,
    })
    if result.get("ok") is not True:
        raise CheckpointReadContextRejected(result)
    return result


def check_result_work_context(*, registry_path: Path, runtime_root: Path,
                             identity: SettlementIdentity, context: Mapping[str, Any],
                             goal_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from ...state_refresh import resolve_goal_state

    registry = (load_first_party_registry_metadata(registry_path, goal_ref)
        if goal_ref is not None else load_registry(registry_path))
    _, _, state_file = resolve_goal_state(registry=registry, goal_id=identity.goal_id,
        project_override=None, state_file_override=None)
    with ExitStack() as locks:
        source_admission = None
        if goal_ref is not None:
            source_admission = locks.enter_context(quota_accounting_admission(
                runtime_root=runtime_root, registry_path=registry_path,
                goal_id=identity.goal_id, goal_ref=goal_ref, operation="check-result-work",
                handoff_legacy_index=True))
            readback = read_heartbeat_settlement(runtime_root, registry_path=registry_path,
                goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
                turn_instance_id=identity.turn_instance_id, replan_obligation_id=identity.replan_obligation_id,
                goal_ref=goal_ref, source_admission=source_admission, borrow_source_admission=True)
            if readback is None:
                raise RuntimeError("Exact result-work admission returned not-found")
            require_checkpoint_source_recovery(readback)
        else:
            locks.enter_context(exclusive_cross_runtime_file_lock(
                runtime_root / "goals" / identity.goal_id / "runs/index.jsonl", operation="check-result-work"))
        with _source_guard(runtime_root, identity.goal_id, state_file, registry_path):
            return _resolve(runtime_root, phase="check", purpose="delivery_result", identity=identity.as_dict(),
                read_context_id=context.get("read_context_id"),
                receipt=_read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result")),
                facts=_local_source_facts(runtime_root, registry_path, state_file, identity, goal_ref,
                    source_admission=source_admission))


def bind_result_validation(*, runtime_root: Path, identity: SettlementIdentity, candidate: Mapping[str, Any],
                           validation: Mapping[str, Any], context: Mapping[str, Any], binding=None) -> dict[str, Any]:
    receipt = _read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result"))
    if receipt.get("read_context_id") != context.get("read_context_id"):
        raise CheckpointReadContextRejected({"ok": False, "error_code": "delivery_validation_binding_mismatch",
            "error": "Candidate validation belongs to a replaced work basis; revalidate the retained candidate."})
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "bind_validation" if binding is None else "check_validation", "identity": identity.as_dict(),
        "receipt": receipt, "candidate": dict(candidate), "validation": dict(validation), "binding": binding,
    })
    if result.get("ok") is not True:
        raise CheckpointReadContextRejected(result)
    return result


def bind_terminal_result_validation(*, runtime_root: Path, request: Mapping[str, Any],
                                   context: Mapping[str, Any]) -> dict[str, Any]:
    identity = SettlementIdentity.from_runtime_payload(context["identity"])
    receipt = _read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result"))
    if receipt.get("read_context_id") != context.get("read_context_id"):
        raise CheckpointReadContextRejected({"ok": False, "error_code": "delivery_validation_binding_mismatch",
            "error": "The work basis changed during validation; retain the candidate and revalidate."})
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "bind_validation", "identity": identity.as_dict(), "receipt": receipt,
        "terminal_input": dict(request),
    })
    if result.get("ok") is not True:
        raise CheckpointReadContextRejected(result)
    return result["binding"]


def terminal_validation_effect_io(*, runtime_root: Path, request: Mapping[str, Any],
                                  context: Mapping[str, Any], execution_result: Mapping[str, Any],
                                  updates: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Persist only the existing typed owner's admitted validation effect state."""
    identity = SettlementIdentity.from_runtime_payload(context["identity"])
    path = _receipt_path(runtime_root, identity, "delivery_result")
    with exclusive_cross_runtime_file_lock(path, operation="terminal-validation-effect"):
        receipt = _read_context_receipt(path)
        if receipt.get("read_context_id") != context.get("read_context_id"):
            raise CheckpointReadContextRejected({"ok": False, "error_code": "delivery_validation_binding_mismatch",
                "error": "Validation belongs to a replaced work basis; retain the original candidate."})
        result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
            "phase": "begin_terminal_validation" if updates is None else "finish_terminal_validation",
            "identity": identity.as_dict(), "receipt": receipt,
            "terminal_input": dict(request), "execution_result": dict(execution_result),
            **({"updates": dict(updates)} if updates is not None else {}),
        })
        if result.get("ok") is not True:
            raise CheckpointReadContextRejected(result)
        if "receipt" in result:
            atomic_write_json(path, result["receipt"])
        return result


def completion_identity_for_original_turn(*, todo: Mapping[str, Any], identity: SettlementIdentity,
                                         goal_id: str, agent_id: str, todo_id: str,
                                         candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Forward the admitted identity and actual Todo to the existing typed owner."""
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "completion_identity", "todo": dict(todo), "identity": identity.as_dict(),
        "goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
        "candidate": dict(candidate),
    })
    key = result.get("completion_turn_key")
    if not isinstance(key, str) or not key:
        raise RuntimeError("Typed original completion identity is unavailable")
    replay = result.get("replay")
    if replay is not None and (not isinstance(replay, dict) or replay.get("ok") is not True
                               or replay.get("completed") is not True or replay.get("changed") is not False):
        raise RuntimeError("Typed original completion replay is invalid")
    return dict(result)


class TurnResultWork:
    """Managed Turn IO for the existing typed work-basis owner."""

    def __init__(self, registry_path: Path, runtime_root: Path, identity: SettlementIdentity,
                 goal_ref: Mapping[str, Any] | None = None):
        self.registry_path, self.runtime_root, self.identity = registry_path, runtime_root, identity
        self.goal_ref = goal_ref

    def result_committed(self) -> bool:
        from ..coordination.local_authority import read_canonical_todos_if_promoted
        from ..todos.completion_transaction import reduce_todo_completion_transaction

        canonical = read_canonical_todos_if_promoted(
            runtime_root=self.runtime_root, goal_id=self.identity.goal_id)
        todo = next((row for row in canonical["todos"] if row.get("todo_id") == self.identity.todo_id), None)
        if todo is None:
            raise ValueError(f"durable completion todo_id {self.identity.todo_id!r} was not found in canonical provider state")
        # Managed journals use the Turn id; the CLI/MCP settlement adapter uses
        # its fully scoped effect id. Both name this identity, never another Turn.
        completion_key = (self.identity.effect_id if todo.get("completion_turn_key") == self.identity.effect_id
            else self.identity.turn_instance_id)
        # The current coarse owner includes the same fence. A dry reduction
        # observes its decision without issuing validation or committing work.
        transaction = reduce_todo_completion_transaction(todo=todo, projection_source="materialized",
            completion_turn_key=completion_key, no_followup=False,
            goal_id=self.identity.goal_id, todo_id=self.identity.todo_id,
            completion_identity_source="turn_settlement", requested_has_successor=False, dry_run=True)
        fence = transaction["fence"]
        return fence["outcome"] == "replay" and fence["status"] == "done"

    def prepare_request(self, journal: dict[str, Any], request: dict[str, Any], *,
                        post_result_checkpoint: Callable[[], bool] | None,
                        persist_journal: Callable[[Mapping[str, Any]], None]) -> bool:
        """Persist the original work basis before dispatch or exact cold recovery."""
        original_result_committed = (isinstance(journal.get("result_work_context"), Mapping) and (
                ("durable_writeback" in journal.get("completed_phases", []) and
                    post_result_checkpoint is not None and post_result_checkpoint()) or self.result_committed()))
        if not original_result_committed:
            revalidation = journal.get("result_revalidation")
            context = self.read()
            if revalidation is not None and (context == revalidation.get("original_context") or revalidation.get("reread_required") is True):
                context = self.read(reread=True)
            if context is not None:
                previous = journal.get("result_work_context")
                if previous is not None and revalidation is None and previous != context:
                    raise ValueError("Original Turn work input was replaced; preserve its candidate and reconcile")
                journal["result_work_context"] = context
                request["delivery_result_context"] = context
                if revalidation is not None:
                    request["result_revalidation"] = revalidation
                    revalidation["reread_required"] = False
                # Durable work input precedes dispatch, including cold retries.
                persist_journal(journal)
        return original_result_committed

    def read(self, *, reread: bool = False) -> dict[str, Any] | None:
        readback = read_heartbeat_settlement(self.runtime_root, goal_id=self.identity.goal_id,
            agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
            turn_instance_id=self.identity.turn_instance_id, registry_path=self.registry_path, goal_ref=self.goal_ref)
        if not result_work_required(readback):
            return None
        return result_work_context(registry_path=self.registry_path, runtime_root=self.runtime_root,
            identity=self.identity, reread=reread, goal_ref=self.goal_ref)

    def check(self, context: Mapping[str, Any]) -> None:
        if self.goal_ref is None:
            read_heartbeat_settlement(self.runtime_root, goal_id=self.identity.goal_id,
                agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
                turn_instance_id=self.identity.turn_instance_id, registry_path=self.registry_path, goal_ref=self.goal_ref)
        # Scoped checks adopt the original receipt inside their source locks;
        # a separate, released pre-read cannot protect the facts that follow.
        # A writeback may precede a terminal Todo commit. Only the typed owner
        # can establish that the original result is already committed.
        check_result_work_context(registry_path=self.registry_path, runtime_root=self.runtime_root,
            identity=self.identity, context=context, goal_ref=self.goal_ref)

    def bind(self, context: Mapping[str, Any], candidate: Mapping[str, Any],
             validation: Mapping[str, Any], binding=None) -> dict[str, Any]:
        return bind_result_validation(runtime_root=self.runtime_root, identity=self.identity,
            candidate=candidate, validation=validation, context=context, binding=binding)


def capture_legacy_result_work(**request: Any) -> dict[str, Any] | None:
    # Optional/damaged history cannot precede exact successful result recovery.
    # A new effect still reaches the locked typed rejection below.
    try:
        return _capture_legacy_result_work(**request)
    except (ValueError, OSError) as error:
        return {"error": {"ok": False, "error_code": "delivery_work_context_unknown", "error": str(error)}}


def _capture_legacy_result_work(*, runtime_root: Path, registry_path: Path, goal_id: str, todo_id: str, agent_id: str | None,
                               completion_turn_key: str | None, identity_payload: Mapping[str, Any] | None,
                               read_context_id: str | None) -> dict[str, Any] | None:
    """Read IO before validation; enforce after historical replay under writer locks."""
    from ...rollout_event_log import rollout_event_log_path
    from .first_party_host_admission import capture_first_party_host_goal_ref

    goal_ref = capture_first_party_host_goal_ref(registry_path=registry_path, goal_id=goal_id)

    path = rollout_event_log_path(runtime_root, goal_id)
    content = path.read_bytes() if path.exists() else b""
    if content and not content.endswith(b"\n"):
        raise ValueError("result work admission log has an incomplete tail")
    events = [json.loads(line) for line in content.splitlines() if line.strip()]
    admission = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "legacy_admission", "events": events, "todo_id": todo_id, "agent_id": agent_id,
        "completion_turn_key": completion_turn_key, "identity": identity_payload,
        "goal_ref": goal_ref,
    })
    if admission.get("required") is False:
        return None
    if admission.get("ok") is False:
        return {"error": admission}
    identity = SettlementIdentity.from_runtime_payload(identity_payload)
    try:
        context = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
            "phase": "restore", "identity": identity.as_dict(),
            "receipt": _read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result")),
            "goal_ref": goal_ref,
        })
        if context.get("ok") is not True:
            return {"error": context}
        if read_context_id and context["read_context_id"] != read_context_id:
            return {"error": {"ok": False, "error_code": "checkpoint_read_context_unknown_or_replaced",
                "error": "The candidate refers to a replaced work read; revalidate on the original Turn."}}
        return context
    except (ValueError, OSError) as error:
        return {"error": {"ok": False, "error_code": "delivery_work_context_unknown", "error": str(error)}}


@contextmanager
def legacy_result_source_locks(root: Path, registry_path: Path, goal_id: str, *, protected: bool) -> Iterator[None]:
    with ExitStack() as stack:
        if protected:
            for target in (root / "goals" / goal_id / "runs/index.jsonl", registry_path,
                           shadow_maintenance_lock_target(root, goal_id)):
                stack.enter_context(exclusive_cross_runtime_file_lock(target, operation="result-work-commit"))
        yield


def legacy_result_commit_policy(transaction: Mapping[str, Any], *, runtime_root: Path,
                                goal_id: str, todo_id: str, agent_id: str | None,
                                todo: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Project the locked typed completion's IO requirements; no admission decision here."""
    result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "legacy_commit_policy", "transaction": dict(transaction),
        "runtime_root": str(runtime_root), "goal_id": goal_id, "todo_id": todo_id, "agent_id": agent_id,
        "todo": dict(todo), "candidate": dict(candidate),
    })
    if result.get("ok") is False:
        raise CheckpointReadContextRejected(result)
    return result


def check_locked_legacy_result_work(*, runtime_root: Path, registry_path: Path, state_file: Path,
                                    context: Mapping[str, Any] | None, candidate: Mapping[str, Any],
                                    validation: Mapping[str, Any], commit_policy: Mapping[str, Any]) -> None:
    if context is None or commit_policy.get("required") is False:
        return
    if context.get("error"):
        raise CheckpointReadContextRejected(dict(context["error"]))
    identity = SettlementIdentity.from_runtime_payload(context["settlement_identity"])
    from .first_party_host_admission import capture_first_party_host_goal_ref
    goal_ref = capture_first_party_host_goal_ref(registry_path=registry_path, goal_id=identity.goal_id)
    bind_result_validation(runtime_root=runtime_root, identity=identity,
        context=context, candidate=candidate, validation={"ok": True, "native_validation": validation})
    _resolve(runtime_root, phase="check", purpose="delivery_result", identity=identity.as_dict(),
        read_context_id=context["read_context_id"],
        receipt=_read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result")),
        facts=_local_source_facts(runtime_root, registry_path, state_file, identity, goal_ref))


def first_delivery_context_enrolled(root: Path, identity: SettlementIdentity, *, result_only: bool = False) -> bool:
    """Read the persisted protocol selection; enrollment survives caller restart."""
    for purpose in ("first_delivery", "delivery_result"):
        # Default result protection has its own persisted admission policy.
        # Its work receipt cannot enroll the optional direction protocol.
        if result_only and purpose == "delivery_result":
            continue
        try:
            receipt = _read_context_receipt(_receipt_path(root, identity, purpose))
        except FileNotFoundError:
            continue
        if receipt.get("purpose") == purpose:
            if purpose == "delivery_result" and receipt.get("result_only") is True:
                continue
            return True
    return False


def _inspect_attempt(root: Path, identity: SettlementIdentity, purpose: str = "first_delivery",
                     *, goal_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    # Called with the original index and source locks held. The TS inspector is
    # read-only; only this locked IO adapter can clear a proved absent marker.
    result = _checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
        "runtime_root": str(root.resolve()), "identity": identity.as_dict(), "purpose": purpose,
        "check_other_attempts": True, **({"goal_ref": dict(goal_ref)} if goal_ref is not None else {})})
    receipt = result.pop("receipt", None)
    if receipt is not None:
        atomic_write_json(_receipt_path(root, identity, purpose), receipt)
    return result


def first_delivery_progress(
    root: Path, readback: Any, *, canonical_todos: list[dict[str, Any]] | None = None,
    observation_unknown: bool = False, registry_path: Path | None = None, verified_direction: bool = False,
) -> dict[str, Any] | None:
    """Observe protocol receipts for quota consumers; this grants no write authority.

    A snapshot may lag a concurrent writer. Ambiguity is displayed as unknown;
    only the locked replay path can resolve it or admit another operation.
    """
    identity = readback.identity.value
    if identity is None:
        return None
    committed = False
    selected: dict[str, Any] = {}
    unknown = observation_unknown
    read_unavailable = False
    try:
        if not verified_direction and not first_delivery_context_enrolled(root, identity) and not checkpoint_continuation_required(readback):
            return None
    except (ValueError, OSError):
        read_unavailable = True
    try:
        if canonical_todos is None:
            canonical_todos = _delivery_observation_todos(root, identity.goal_id, registry_path)
        selected = next((todo for todo in canonical_todos if todo["todo_id"] == identity.todo_id), {})
        committed = selected.get("status") == "done" and selected.get("completion_turn_key") in {identity.effect_id, identity.turn_instance_id}
    except (ValueError, OSError):
        unknown = True
    try:
        direction_path = _receipt_path(root, identity)
        direction = _read_context_receipt(direction_path) if direction_path.exists() else {}
    except (ValueError, OSError):
        direction = {}
        read_unavailable = True
    if read_unavailable:
        try:
            proof = _checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
                "runtime_root": str(root.resolve()), "identity": identity.as_dict(), "purpose": "first_delivery"})
            unknown = unknown or proof.get("status") != "committed"
        except (ValueError, OSError):
            unknown = True
    progress = effect_runtime_result("turn.first_delivery.evaluate", {
        "phase": "project", "result_committed": committed,
        "checkpoint_continuation": checkpoint_continuation_required(readback),
        "work_authority_hold": (readback.progress or {}).get("work_authority_hold"),
        "completion_event": readback.completion_event,
        # Observation consumes stage markers, not the complete Agent input or
        # an append's projected index record. Keep the ordinary RPC bounded.
        "writeback_run": {key: (readback.writeback_run or {}).get(key) for key in (
            "goal_id", "agent_id", "todo_id", "turn_instance_id", "delivery_outcome", "vision_checkpoint")},
        "direction_receipt": {
            "direction_stopped": direction.get("direction_stopped"),
            "direction_host_failure": direction.get("direction_host_failure"),
            "commit_attempt": {} if direction.get("commit_attempt") is not None else None,
        },
        "unknown": unknown,
        "quota_spent": readback.spend_run is not None,
        "settlement_complete": readback.settlement.failure is None,
        "terminal_settlement_complete": readback.terminal_settlement.failure is None,
        "identity": identity.as_dict(),
        "completion_todo": {key: selected.get(key) for key in (
            "status", "no_followup", "completion_continuation",
            "completion_requested_continuation", "completion_turn_key")},
    })
    return {**progress, "settlement_identity": identity.as_dict()}


def _delivery_observation_todos(root: Path, goal_id: str, registry_path: Path | None) -> list[dict[str, Any]]:
    from ..coordination.local_authority import read_canonical_todos_if_promoted
    canonical = read_canonical_todos_if_promoted(runtime_root=root, goal_id=goal_id)
    if canonical is not None:
        return canonical.get("todos", [])
    if registry_path is None:
        return []
    from ...todos import resolve_todo_state_path
    _, state = resolve_todo_state_path(registry_path=registry_path, goal_id=goal_id, project=None, state_file=None)
    active, archived, _ = parse_todo_source(state.read_text(encoding="utf-8"))
    return [*active["user"], *active["agent"], *archived]


def pending_first_delivery_progress(root: Path, goal_id: str, agent_id: str | None = None,
                                   *, registry_path: Path | None = None) -> dict[str, Any] | None:
    """Read provider facts once; the typed owners observe all historical Turns.

    This remains an alias observation, as before. A Todo source path does not
    grant exact GoalRef admission or authorize historical receipt repair.
    """
    try:
        todos = [{key: todo.get(key) for key in (
            "todo_id", "status", "no_followup", "completion_continuation",
            "completion_requested_continuation", "completion_turn_key")}
            for todo in _delivery_observation_todos(root, goal_id, registry_path)]
    except (ValueError, OSError):
        todos = None
    return _checkpoint_effect("turn.first_delivery.observe", {
        "runtime_root": str(root.resolve()), "goal_id": goal_id,
        "agent_id": agent_id, "todos": todos,
        # Keep the caller's historical scan order across Python/Node platforms.
        "receipt_names": [path.name for path in (root / "goals" / goal_id / "checkpoint-contexts").glob("*.json")],
    })


def delivery_result_context_input(
    *, runtime_root: Path, registry_path: Path, state_file: Path,
    identity: SettlementIdentity, read_context_id: str | None,
) -> dict[str, Any] | None:
    """Capture IO for the native Todo owner; never refresh the Agent's token.

    The native owner recovers a historical receipt before checking capture
    errors. Every validation continuation recaptures sources under short locks.
    """
    result: dict[str, Any] = {"identity": identity.as_dict(),
        "read_context_id": read_context_id or "missing", "state_file": str(state_file.resolve())}
    from .first_party_host_admission import capture_first_party_host_goal_ref
    goal_ref = capture_first_party_host_goal_ref(registry_path=registry_path, goal_id=identity.goal_id)
    if not read_context_id:
        try:
            readback = read_heartbeat_settlement(runtime_root, goal_id=identity.goal_id,
                agent_id=identity.agent_id, todo_id=identity.todo_id,
                turn_instance_id=identity.turn_instance_id, registry_path=registry_path, goal_ref=goal_ref)
            if readback is not None and result_work_required(readback):
                receipt = _read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result"))
                read_context_id = receipt.get("read_context_id")
                result["read_context_id"] = read_context_id or "missing"
            elif not first_delivery_context_enrolled(runtime_root, identity):
                return None
        except (ValueError, OSError) as error:
            # Preserve the ordinary request fingerprint for receipt-first
            # recovery. Only the native owner can prove an unreadable shared
            # supplement optional; this is not evidence of non-enrollment.
            return {**result, "read_context_id": None, "capture_error": str(error)}
    index = runtime_root / "goals" / identity.goal_id / "runs" / "index.jsonl"
    try:
        with exclusive_cross_runtime_file_lock(index, operation="delivery-result-capture"):
            with _source_guard(runtime_root, identity.goal_id, state_file, registry_path):
                work_path = _receipt_path(runtime_root, identity, "delivery_result")
                if work_path.exists():
                    _persist_result_work_witness(runtime_root, identity, _read_context_receipt(work_path))
                result.update(
                    facts=_local_source_facts(runtime_root, registry_path, state_file, identity, goal_ref),
                    state_sha256=hashlib.sha256(state_file.read_bytes()).hexdigest(),
                    index_sha256=hashlib.sha256(index.read_bytes() if index.exists() else b"").hexdigest(),
                )
    except (ValueError, OSError) as error:
        result["capture_error"] = str(error)
    return result


def require_complete_checkpoint_index(index: Path) -> None:
    """Framing check before replay too; typed settlement validates the rows."""
    try:
        content = index.read_bytes()
    except FileNotFoundError:
        return
    if content and not content.endswith(b"\n"):
        raise CheckpointReadContextRejected({
            "ok": False, "error_code": "checkpoint_commit_unknown", "reread_required": False,
            "error": "checkpoint index has an incomplete tail; inspect the original Turn before retrying",
        })


@contextmanager
def _source_guard(root: Path, goal_id: str, state_file: Path, registry_path: Path | None = None) -> Iterator[None]:
    """Caller holds runs/index first. Match promotion's M -> Todo -> state order.

    M prevents source cutover; it does not exclude canonical provider commits.
    The native commit additionally fences the real provider through its append.
    Do not run projection sync or a new state mutation inside this guard.
    """
    with ExitStack() as locks:
        if registry_path is not None:
            locks.enter_context(exclusive_cross_runtime_file_lock(registry_path, operation="checkpoint-first-delivery"))
        for target in (
            shadow_maintenance_lock_target(root, goal_id),
            legacy_coordination_todo_lock_path(runtime_root=root, goal_id=goal_id),
            state_file,
        ):
            locks.enter_context(exclusive_cross_runtime_file_lock(target, operation="checkpoint-read-context"))
        require_shadow_primary_write_allowed(root, goal_id)
        yield


def _local_source_facts(
    root: Path, registry_path: Path, state_file: Path, identity: SettlementIdentity,
    goal_ref: Mapping[str, Any] | None = None,
    *, source_admission: Mapping[str, Any] | None = None,
    runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        text = state_file.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise CheckpointReadContextRejected({
            "ok": False, "error_code": "checkpoint_goal_source_unavailable",
            "reread_required": False,
            "error": "The authoritative Goal document is missing; native Todo records cannot restore its objective or acceptance prose.",
            "recommended_action": "Use loopx-project to locate and restore the Goal owner's actual document or backup. Retry quota should-run for the original Agent/Turn; preserve its admitted receipt and do not execute or spend before the Goal source is restored.",
        }) from error
    metadata, body = split_state_frontmatter(text)
    from ..todos.projection_document import is_legacy_todo_projection_skeleton

    if set(metadata) == {"goal_id"} and is_legacy_todo_projection_skeleton(text, metadata["goal_id"]):
        metadata["goal_source_completeness"] = "todo_projection_only"
    lines = body.splitlines()
    regions = find_todo_source_regions(lines)
    owned = {i for region in regions for i in range(region.start, region.end)}
    prose = "\n".join(line for i, line in enumerate(lines) if i not in owned).strip()
    active, archived, _ = parse_todo_source(text)
    todos = [*active["user"], *active["agent"], *archived]
    if runs is None:
        runs, _ = load_index(root / "goals" / identity.goal_id / "runs" / "index.jsonl")
    runs = [
        run
        for run in runs
        if (
            run.get("goal_ref") == dict(goal_ref)
            if goal_ref is not None
            else "goal_ref" not in run
        )
    ]
    newest = [run for _, run in sorted(enumerate(runs),
        key=lambda pair: (str(pair[1].get("generated_at") or ""), pair[0]), reverse=True)]
    facts = {
        "todos": todos, "frontmatter": metadata, "goal_prose": prose, "acceptance": None,
        "agent_vision": latest_agent_vision_from_runs(newest, goal_id=identity.goal_id, agent_id=identity.agent_id),
        "source": {"state_file": str(state_file.resolve()), "runtime_root": str(root.resolve()),
                   "authority": "legacy_markdown"},
    }
    from ...state_refresh import resolve_goal_state, registered_agents_for_goal

    # A scoped read receipt grants no generic capture or mutation authority.
    # Only the reader holding quota's exact-source admission takes this path.
    registry = (load_first_party_registry_metadata(registry_path, goal_ref)
        if source_admission is not None else load_registry(registry_path))
    goal, _, current_path = resolve_goal_state(registry=registry, goal_id=identity.goal_id,
        project_override=None, state_file_override=None)
    if current_path.resolve() != state_file.resolve() or identity.agent_id not in registered_agents_for_goal(goal):
        raise ValueError("Checkpoint source or Agent registration changed; reread the original Turn")
    facts["source"].update(registry_path=str(registry_path.resolve()), goal_ref=goal_ref,
        registry_goal=goal)
    facts["checkpoint_identity"] = identity.as_dict()
    return facts


def _pending_replan_authoring(root: Path, registry_path: Path, state_path: Path,
                              goal: dict[str, Any], readback: QuotaSettlementReadback,
                              identity: SettlementIdentity, context: Mapping[str, Any],
                              runs: list[dict[str, Any]],
                              goal_ref: Mapping[str, Any] | None,
                              source_admission: dict[str, Any] | None) -> dict[str, Any]:
    """Read the current authoring basis through the original writeback owner."""
    guard = readback.semantic_replan_guard or {}
    obligation_id = guard.get("selected_obligation_id")
    if guard.get("scope") != "turn_guard" or not obligation_id or guard.get("selected_capability_guard"):
        return {}

    from ...capabilities.explore.turn_context import read_replan_explore_context
    from ...capabilities.progress_review.context import external_progress_review_context
    from ...capabilities.todo_replan_cadence.machine_defaults import resolve_todo_replan_cadence_goal
    from ..quota.heartbeat_receipt import guard_cadence, guard_source_for_turn
    from ..work_items.progress_observation import replan_writeback_requirements
    from ..work_items.refresh_recommendation import load_refresh_planning_source
    from ..work_items.replan_history_codec import effective_turn_cadence_context
    from ..work_items.semantic_replan_writeback import qualify_replan_writeback

    planning = load_refresh_planning_source(root, identity.goal_id, state_path,
                                           require_display=False, registry_path=registry_path)
    if planning.canonical_snapshot.get("provider_revision") != context.get("provider_revision"):
        raise CheckpointReadContextRejected({"ok": False, "error_code": "checkpoint_read_context_stale",
            "reread_required": True, "changed_components": ["replan_context"],
            "error": "The provider changed while reading the original replan basis. Reread this same Turn."})
    source = guard_source_for_turn(root, goal_id=identity.goal_id, agent_id=identity.agent_id,
                                   turn_instance_id=identity.turn_instance_id, goal_ref=goal_ref)
    retained = source["obligation"] if source and source["cadence"] == guard_cadence(
        registry_path, root, identity.goal_id) else None
    route = {"registry": str(registry_path), "runtime_root": str(root),
             **({"goal_ref": goal_ref} if goal_ref is not None else {})}
    rows = [row for row in runs if (row.get("goal_ref") == goal_ref
                                  if goal_ref is not None else "goal_ref" not in row)]
    newest = [row for _, row in sorted(enumerate(rows),
        key=lambda item: (*chronology_key(item[1].get("generated_at")), item[0]), reverse=True)]
    effective_goal = resolve_todo_replan_cadence_goal(goal, root)
    obligation, _ = qualify_replan_writeback(newest_first_runs=newest, state_text=planning.state_text,
        agent_id=identity.agent_id, goal_id=identity.goal_id, registry_goal=effective_goal,
        todo_fields=planning.todo_fields, external_progress_review=external_progress_review_context(goal, root),
        effective_turn_cadence=effective_turn_cadence_context(effective_goal, root,
            registry_path=registry_path, goal_ref=dict(goal_ref) if goal_ref is not None else None,
            source_admission=source_admission),
        guard_scoped=True, guard_semantic_replan_obligation_id=obligation_id,
        receipt_bound_guard_recorded_at=(readback.heartbeat_receipt or {}).get("recorded_at"),
        receipt_bound_replan_review=guard.get("selected_review_source"), receipt_bound_source=retained,
        replan_source_route=route, replan_explore_reader=lambda gid, aid: read_replan_explore_context(
            source_status=route, goal_id=gid, agent_id=aid))
    if obligation is None or not obligation.get("replan_context"):
        return {}
    requirements = replan_writeback_requirements(obligation)
    return {"replan_context": obligation["replan_context"],
            **({"stride_control": requirements["stride_control"]} if "stride_control" in requirements else {})}


def read_checkpoint_context(
    *, registry_path: Path, runtime_root_override: str | None, goal_id: str,
    agent_id: str, todo_id: str | None, turn_instance_id: str,
    replan_obligation_id: str | None = None, project: Path | None = None,
    state_file: Path | None = None, dependency_todo_ids: list[str] | None = None,
    goal_ref: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
    include_retained_content: bool = False,
    restore_issued_for: SettlementIdentity | None = None,
    reuse_current_first_delivery: bool = False,
) -> dict[str, Any]:
    # Local import avoids a cycle with refresh-state's persistence adapter.
    from ...state_refresh import resolve_goal_state, registered_agents_for_goal

    if restore_issued_for is not None and purpose != "delivery_result":
        raise ValueError("only an issued result work read can be restored")
    goal_id = validate_goal_id_path_segment(goal_id)
    registry = (load_first_party_registry_metadata(registry_path, goal_ref)
        if goal_ref is not None else load_registry(registry_path))
    root = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    goal, _, path = resolve_goal_state(registry=registry, goal_id=goal_id,
        project_override=project, state_file_override=state_file)
    if agent_id not in registered_agents_for_goal(goal):
        raise ValueError("checkpoint-context requires a registered Agent")
    with quota_accounting_admission(
        runtime_root=root,
        registry_path=registry_path,
        goal_id=goal_id,
        goal_ref=goal_ref,
        operation="checkpoint-context",
        handoff_legacy_index=True,
    ) as source_admission:
        require_complete_checkpoint_index(root / "goals" / goal_id / "runs" / "index.jsonl")
        readback = read_heartbeat_settlement(root, goal_id=goal_id, agent_id=agent_id,
            todo_id=todo_id, turn_instance_id=turn_instance_id,
            replan_obligation_id=replan_obligation_id,
            registry_path=registry_path, goal_ref=goal_ref,
            source_admission=source_admission,
            borrow_source_admission=source_admission is not None)
        first_delivery = purpose != "supplement_checkpoint"
        if readback is None or readback.identity.value is None or (
            not first_delivery and readback.writeback_run is None
        ):
            raise ValueError("checkpoint-context requires the original committed Turn writeback")
        identity = readback.identity.value
        if restore_issued_for is not None and identity != restore_issued_for:
            raise CheckpointReadContextRejected({
                "ok": False, "error_code": "delivery_work_identity_mismatch",
                "reread_required": True, "changed_components": [],
                "error": "The issued result work read belongs to a different Turn.",
            })
        continuation = checkpoint_continuation_required(readback)
        if decision_scope is None:
            decision_scope = "agent_lane"
        with _source_guard(root, goal_id, path, registry_path):
            prior = readback.writeback_run
            prior_checkpoint = prior.get("vision_checkpoint") if isinstance(prior, dict) else None
            # The typed read model names an indexed direction artifact. Verify
            # it before parsing a disposable read or capturing fresh Goal facts.
            indexed_direction = isinstance(prior_checkpoint, dict) and isinstance(prior_checkpoint.get("read_context"), dict)
            if purpose == "first_delivery" or (purpose == "supplement_checkpoint" and continuation and indexed_direction):
                attempt = _inspect_attempt(root, identity, purpose, goal_ref=goal_ref)
                if attempt.get("status") == "committed":
                    return {**attempt, "settlement_identity": identity.as_dict(), "purpose": purpose,
                        "instructions": "Direction already committed. Replay the original refresh or resume this Turn's remaining settlement; do not request another direction."}
            with ExitStack() as read_locks:
                if purpose == "delivery_result":
                    read_locks.enter_context(exclusive_cross_runtime_file_lock(
                        _receipt_path(root, identity, purpose), operation="result-work-read"))
                previous_receipt = None
                if first_delivery or continuation:
                    try:
                        previous_receipt = json.loads(_receipt_path(root, identity, purpose).read_text(encoding="utf-8"))
                    except FileNotFoundError:
                        pass
                    except (ValueError, OSError):
                        if purpose == "supplement_checkpoint" and continuation:
                            _inspect_attempt(root, identity, purpose, goal_ref=goal_ref)
                        raise
                if purpose == "delivery_result" and (previous_receipt or {}).get("commit_attempt") is not None:
                    # An indexed progress writeback may precede an uncommitted
                    # terminal result. Inspect its artifacts first; the typed
                    # owner then distinguishes that proof from a done Todo.
                    attempt = _inspect_attempt(root, identity, purpose, goal_ref=goal_ref)
                    previous_receipt = _read_context_receipt(_receipt_path(root, identity, purpose))
                    if attempt.get("status") == "committed":
                        # Inspection verified this exact row against the index
                        # and artifacts; a later direction row is a different effect.
                        prior = previous_receipt["commit_attempt"]["index_record"]
                replan_pending = (purpose == "supplement_checkpoint" and continuation
                    and (prior_checkpoint or {}).get("replan_stage") == "awaiting_direction")
                runs = load_index(root / "goals" / goal_id / "runs" / "index.jsonl")[0] if replan_pending else None
                facts = _local_source_facts(root, registry_path, path, identity, goal_ref,
                                            source_admission=source_admission, runs=runs)
                reuse_read = False
                read_id = uuid4().hex
                if reuse_current_first_delivery and purpose == "first_delivery" and previous_receipt is not None:
                    try:
                        _resolve(root, phase="check", identity=identity.as_dict(), purpose=purpose,
                            checkpoint_continuation=continuation, read_context_id=previous_receipt.get("read_context_id"),
                            receipt=previous_receipt, facts=facts)
                    except CheckpointReadContextRejected as error:
                        if error.code != "checkpoint_read_context_stale":
                            raise
                    else:
                        reuse_read = True
                        read_id = previous_receipt["read_context_id"]
                        decision_scope = previous_receipt["decision_scope"]
                        dependency_todo_ids = previous_receipt["dependency_todo_ids"]
                result = _resolve(root, phase="read", identity=identity.as_dict(), prior=prior,
                    **({"include_retained_content": True} if include_retained_content else {}),
                    **({"restore_issued_work": True} if restore_issued_for is not None else {}),
                    writeback_committed=readback.writeback.failure is None,
                    quota_spend_committed=readback.spend_run is not None or readback.spend_event is not None,
                    semantic_replan_guard=readback.semantic_replan_guard,
                    purpose=purpose, decision_scope=decision_scope,
                    receipt=previous_receipt,
                    **({"heartbeat_receipt": readback.heartbeat_receipt} if purpose == "delivery_result" else {}),
                    checkpoint_continuation=continuation,
                    settlement_complete=readback.settlement.failure is None,
                    quota_spent=readback.spend_run is not None,
                    full_frontier=continuation and purpose != "delivery_result",
                    admitted_turn=readback.heartbeat_receipt is not None,
                    read_context_id=read_id, dependency_todo_ids=dependency_todo_ids or [],
                    facts=facts)
                if result.get("status") == "committed":
                    return {**result, "settlement_identity": identity.as_dict(), "purpose": purpose,
                        "instructions": "Direction already committed. Recover this original Turn's remaining settlement; do not request another judgment."}
                receipt = result.pop("receipt")
                if runs is not None and not receipt.get("direction_stopped"):
                    authoring = _pending_replan_authoring(root, registry_path, path,
                        facts["source"]["registry_goal"], readback, identity, result, runs, goal_ref, source_admission)
                    if authoring:
                        result.update(authoring)
                        result["instructions"] = (
                            "Read replan_context for the original obligation and follow stride_control when present. "
                            + (result.get("instructions") or "")
                        )
                receipt_path = _receipt_path(root, identity, purpose)
                if not reuse_read:
                    atomic_write_json(receipt_path, receipt)
                if restore_issued_for is not None:
                    expected = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                    if receipt_path.read_bytes() != expected:
                        raise CheckpointReadContextRejected({
                            "ok": False, "error_code": "delivery_work_basis_corrupt",
                            "reread_required": True, "changed_components": [],
                            "error": "The issued result work receipt differs from its durable readback.",
                        })
                    return result
    return {**result, "read_context_id": receipt["read_context_id"], "settlement_identity": identity.as_dict(),
        "purpose": purpose,
        "instructions": ("Read this basis and revalidate the retained candidate or in-flight progress. Retry the original submission. Use --delivery-read-context with this read ID to bind refresh-state; todo complete requires the flag. This read does not complete the Todo. "
        if purpose == "delivery_result" else "Read this basis and judge the direction again. Echo read_context_id as --checkpoint-read-context in the direction refresh for this exact Turn. ") +
        "A new checkpoint-context read replaces this receipt; do not run parallel confirmations "
        "for the same Turn. On stale/replaced context, reread and rejudge; do not repeat task mutations or spend."
        + (" " + result["instructions"] if result.get("instructions") else "")}


def check_direction_launch_context(
    *, runtime_root: Path, registry_path: Path, state_file: Path,
    identity: SettlementIdentity, context: Mapping[str, Any] | None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only typed check, repeated inside the Host's launch transaction.

    Do not take the registry lock recursively, issue a new read, or substitute
    a new context. The final checkpoint commit retains its provider fence.
    """
    from .first_party_host_admission import FirstPartyHostRuntimeRejected

    try:
        receipt = _read_context_receipt(_receipt_path(runtime_root, identity))
        _resolve(runtime_root, phase="check", identity=identity.as_dict(),
            purpose=(context or {}).get("purpose"), checkpoint_continuation=True,
            read_context_id=(context or {}).get("read_context_id"), receipt=receipt,
            facts=_local_source_facts(runtime_root, registry_path, state_file, identity, goal_ref))
        try:
            work = _read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result"))
        except FileNotFoundError:
            work = None
        checked = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
            "phase": "direction_launch_context", "identity": identity.as_dict(),
            "receipt": receipt, "work_receipt": work,
            **({"retained_result_content": context["retained_result_content"]}
               if context is not None and "retained_result_content" in context else {})})
        if not isinstance(checked, dict):
            raise RuntimeError("invalid typed direction launch check")
        if checked.get("ok") is not True:
            raise CheckpointReadContextRejected(checked)
        return checked
    except CheckpointReadContextRejected as error:
        raise FirstPartyHostRuntimeRejected(error.code) from error


def _attach_result_read_recovery(
    rejection: CheckpointReadContextRejected, *, runtime_root: Path, registry_path: Path,
    state_file: Path, identity: SettlementIdentity, goal_ref: Mapping[str, Any] | None,
    retry_workspace_path: Path | None = None,
) -> None:
    checkpoint = rejection.payload["checkpoint_read_context"]
    if not identity.todo_id or rejection.code not in {
        "checkpoint_read_context_stale", "checkpoint_read_context_unknown_or_replaced",
    } or checkpoint.get("reread_required") is not True:
        return
    from ..work_items.action_selection_contract import render_cli_command_prefix

    route = ["checkpoint-context", "--purpose", "delivery_result", "--goal-id", identity.goal_id,
             "--agent-id", identity.agent_id, "--todo-id", identity.todo_id,
             "--turn-instance-id", identity.turn_instance_id, "--state-file", str(state_file.resolve())]
    if goal_ref is not None:
        route.extend(["--goal-instance-id", goal_ref["goal_instance_id"]])
    prefix = render_cli_command_prefix(registry_path=str(registry_path.resolve()), runtime_root=str(runtime_root.resolve()))
    # Render for the shell that receives the command, including a pinned
    # PowerShell launcher whose path requires the call operator.
    read_command = _render_checkpoint_host_command([*shlex.split(prefix), "--format", "json", *route])
    instructions = checkpoint.get("instructions") or (
        "Execute read_command. Revalidate the original result and its evidence against the returned basis. "
        "Retry the original refresh-state with the revalidated result options and retry_args, replacing any previous "
        "--delivery-read-context with the returned read_context_id. Preserve the Turn identity."
    )
    retry_args = ["--delivery-read-context", "<read_context_id>", *checkpoint.get("retry_args", [])]
    if retry_workspace_path is not None:
        # The path routes the local retry; it is not part of the durable workspace snapshot.
        retry_args.extend(["--delivery-workspace-path", str(retry_workspace_path)])
        instructions += " Use the supplied --delivery-workspace-path, replacing any previous value."
    rejection.payload["result_read_recovery"] = {
        "settlement_identity": identity.as_dict(),
        "read_command": read_command,
        "retry_args": retry_args,
        **({"remove_flags": checkpoint["remove_flags"]} if checkpoint.get("remove_flags") else {}),
        "instructions": instructions,
    }


@contextmanager
def checkpoint_commit_guard(
    *, runtime_root: Path, registry_path: Path, state_file: Path,
    identity: SettlementIdentity, read_context_id: str | None,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
    blocked_wait: Mapping[str, Any] | None = None,
    retry_workspace_path: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Capture/preview under source locks. The native save repeats the check
    under the real provider fence; this preliminary check is not the commit."""
    protected = purpose != "supplement_checkpoint"
    with _source_guard(runtime_root, identity.goal_id, state_file, registry_path):
        readback = read_heartbeat_settlement(runtime_root, goal_id=identity.goal_id,
            agent_id=identity.agent_id, todo_id=identity.todo_id, turn_instance_id=identity.turn_instance_id,
            replan_obligation_id=identity.replan_obligation_id, registry_path=registry_path, goal_ref=goal_ref,
            source_admission=source_admission,
            borrow_source_admission=source_admission is not None)
        if protected:
            _inspect_attempt(runtime_root, identity, purpose, goal_ref=goal_ref)
        try:
            receipt = json.loads(_receipt_path(runtime_root, identity, purpose).read_text(encoding="utf-8"))
        except FileNotFoundError:
            receipt = None
        try:
            result = _resolve(runtime_root, phase="check", identity=identity.as_dict(), read_context_id=read_context_id,
                checkpoint_continuation=readback is not None and checkpoint_continuation_required(readback),
                purpose=purpose, decision_scope=decision_scope,
                **({"blocked_wait": dict(blocked_wait)} if blocked_wait is not None else {}),
                receipt=receipt, facts=_local_source_facts(
                    runtime_root,
                    registry_path,
                    state_file,
                    identity,
                    goal_ref,
                    source_admission=source_admission,
                ))
        except CheckpointReadContextRejected as error:
            if purpose == "delivery_result":
                _attach_result_read_recovery(error, runtime_root=runtime_root, registry_path=registry_path,
                    state_file=state_file, identity=identity, goal_ref=goal_ref,
                    retry_workspace_path=retry_workspace_path)
            raise
        if purpose == "delivery_result" and isinstance(receipt, Mapping):
            _persist_result_work_witness(runtime_root, identity, receipt)
        yield result


def commit_checkpoint_run(
    *, runtime_root: Path, registry_path: Path, state_file: Path, identity: SettlementIdentity,
    refresh_retry: dict[str, Any], record: dict[str, Any], index_record: dict[str, Any], markdown: str,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
    read_context_id: str | None = None, blocked_wait: Mapping[str, Any] | None = None,
    retry_workspace_path: Path | None = None,
) -> dict[str, Any]:
    """Handoff the held locks and parsed bytes to one native save operation."""
    root = runtime_root.resolve()
    index = root / "goals" / identity.goal_id / "runs" / "index.jsonl"
    targets = (index, registry_path,
               shadow_maintenance_lock_target(root, identity.goal_id),
               legacy_coordination_todo_lock_path(runtime_root=root, goal_id=identity.goal_id), state_file)
    result = _checkpoint_effect("goal.checkpoint_read_context.commit", {
        "runtime_root": str(root), "state_file": str(state_file.resolve()), "identity": identity.as_dict(),
        "purpose": purpose, "decision_scope": decision_scope, "read_context_id": read_context_id,
        "registry_path": str(registry_path.resolve()),
        "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest(),
        "locks": [cross_runtime_lock_witness(target) for target in targets],
        "state_sha256": hashlib.sha256(state_file.read_bytes()).hexdigest(),
        "index_sha256": hashlib.sha256(index.read_bytes() if index.exists() else b"").hexdigest(),
        "facts": _local_source_facts(
            root,
            registry_path,
            state_file,
            identity,
            goal_ref,
            source_admission=source_admission,
        ),
        "refresh_retry": refresh_retry, "record": record, "index_record": index_record, "markdown": markdown,
        **({"blocked_wait": dict(blocked_wait)} if blocked_wait is not None else {}),
        **({"goal_ref": dict(goal_ref)} if goal_ref is not None else {}),
        **(
            {"source_admission": dict(source_admission)}
            if source_admission is not None
            else {}
        ),
    })
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise RuntimeError("invalid typed checkpoint commit result")
    if not result["ok"]:
        error = CheckpointReadContextRejected(result)
        if purpose == "delivery_result":
            _attach_result_read_recovery(error, runtime_root=root, registry_path=registry_path,
                state_file=state_file, identity=identity, goal_ref=goal_ref,
                retry_workspace_path=retry_workspace_path)
        raise error
    return result


def inspect_checkpoint_replay(runtime_root: Path, goal_id: str, prior: dict[str, Any]) -> None:
    if prior.get("result_work_context") is not None or (
        isinstance(prior.get("vision_checkpoint"), dict) and prior["vision_checkpoint"].get("read_context")
    ):
        _checkpoint_effect("goal.checkpoint_read_context.inspect_replay", {
            "runtime_root": str(runtime_root.resolve()), "goal_id": goal_id, "prior": prior,
        })


def render_checkpoint_context(payload: dict[str, Any]) -> str:
    # The decision basis is private local state, not a public/global projection.
    return "# LoopX Checkpoint Context\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"
