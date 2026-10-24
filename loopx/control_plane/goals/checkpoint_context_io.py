"""Source/receipt I/O only; checkpoint_read_context.ts owns decision semantics."""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ...file_lock import cross_runtime_lock_witness, exclusive_cross_runtime_file_lock
from ...history import load_index, load_registry
from ...paths import resolve_runtime_root
from ..runtime.document_io import atomic_write_private_json as atomic_write_json
from ...runtime import validate_goal_id_path_segment
from ..coordination.legacy_writer_fence import legacy_coordination_todo_lock_path
from ..coordination.shadow_management import shadow_maintenance_lock_target, require_shadow_primary_write_allowed
from ..effect_runtime import effect_runtime_result, EffectRuntimeRejected
from ..quota.accounting_admission import quota_accounting_admission
from ..quota.settlement import SettlementIdentity, read_heartbeat_settlement
from ..todos.active_state_todo_parser import parse_todo_source
from ..todos.machine_region import find_todo_source_regions
from .active_state_metadata import split_state_frontmatter
from .goal_frontier import latest_agent_vision_from_runs


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
        return effect_runtime_result(method, request,
            large_local_snapshot=method != "goal.checkpoint_read_context.inspect_attempt")
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


def default_result_freshness_policy(root: Path, goal_id: str, todo_id: str | None,
                                   goal_ref: Mapping[str, Any] | None = None) -> str | None:
    from ..coordination.local_authority import read_canonical_todos_if_promoted

    canonical = read_canonical_todos_if_promoted(runtime_root=root, goal_id=goal_id)
    return _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "admission", "todo_id": todo_id, "goal_ref": goal_ref,
        "source_authority": (canonical or {}).get("source_authority", "legacy_markdown"),
    })["policy"]


def result_work_required(readback: Any) -> bool:
    return _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "policy", "heartbeat_receipt": readback.heartbeat_receipt,
    })["required"]


def checkpoint_continuation_required(readback: Any) -> bool:
    return _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
        "phase": "direction_policy", "heartbeat_receipt": readback.heartbeat_receipt,
    })["required"]


def result_work_read_id(root: Path, identity: SettlementIdentity) -> str:
    """Restore the admitted work token; a writeback must never mint a new read."""
    try:
        receipt = _read_context_receipt(_receipt_path(root, identity, "delivery_result"))
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
    if not reread and path.exists():
        result = _checkpoint_effect("goal.checkpoint_read_context.work_binding", {
            "phase": "restore", "identity": identity.as_dict(), "receipt": _read_context_receipt(path),
            "goal_ref": goal_ref,
        })
        if result.get("ok") is not True:
            raise CheckpointReadContextRejected(result)
        return result
    read_checkpoint_context(registry_path=registry_path, runtime_root_override=str(runtime_root),
        goal_id=identity.goal_id, agent_id=identity.agent_id, todo_id=identity.todo_id,
        turn_instance_id=identity.turn_instance_id, purpose="delivery_result", goal_ref=goal_ref)
    return result_work_context(registry_path=registry_path, runtime_root=runtime_root, identity=identity, goal_ref=goal_ref)


def check_result_work_context(*, registry_path: Path, runtime_root: Path,
                             identity: SettlementIdentity, context: Mapping[str, Any],
                             goal_ref: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from ...state_refresh import resolve_goal_state

    _, _, state_file = resolve_goal_state(registry=load_registry(registry_path), goal_id=identity.goal_id,
        project_override=None, state_file_override=None)
    with exclusive_cross_runtime_file_lock(runtime_root / "goals" / identity.goal_id / "runs/index.jsonl",
                                          operation="check-result-work"):
        with _source_guard(runtime_root, identity.goal_id, state_file, registry_path):
            return _resolve(runtime_root, phase="check", purpose="delivery_result", identity=identity.as_dict(),
                read_context_id=context.get("read_context_id"),
                receipt=_read_context_receipt(_receipt_path(runtime_root, identity, "delivery_result")),
                facts=_local_source_facts(runtime_root, registry_path, state_file, identity, goal_ref, first_delivery=True))


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
        from ...state_refresh import resolve_goal_state
        from ..todos.durable_completion import read_persisted_todo_record_with_source
        from ..todos.completion_fence import evaluate_todo_completion_fence

        _, _, state = resolve_goal_state(registry=load_registry(self.registry_path), goal_id=self.identity.goal_id,
            project_override=None, state_file_override=None)
        todo, _, source = read_persisted_todo_record_with_source(state, todo_id=self.identity.todo_id,
            registry_path=self.registry_path, goal_id=self.identity.goal_id, runtime_root=self.runtime_root)
        # Managed journals use the Turn id; the CLI/MCP settlement adapter uses
        # its fully scoped effect id. Both name this identity, never another Turn.
        completion_key = (self.identity.effect_id if todo.get("completion_turn_key") == self.identity.effect_id
            else self.identity.turn_instance_id)
        fence = evaluate_todo_completion_fence(todo=todo, projection_source=source,
            completion_turn_key=completion_key, no_followup=False,
            goal_id=self.identity.goal_id, todo_id=self.identity.todo_id,
            completion_identity_source="turn_settlement")
        return fence["outcome"] == "replay" and fence["status"] == "done"

    def read(self, *, reread: bool = False) -> dict[str, Any] | None:
        readback = read_heartbeat_settlement(self.runtime_root, goal_id=self.identity.goal_id,
            agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
            turn_instance_id=self.identity.turn_instance_id, registry_path=self.registry_path, goal_ref=self.goal_ref)
        if not result_work_required(readback):
            return None
        return result_work_context(registry_path=self.registry_path, runtime_root=self.runtime_root,
            identity=self.identity, reread=reread, goal_ref=self.goal_ref)

    def check(self, context: Mapping[str, Any]) -> None:
        read_heartbeat_settlement(self.runtime_root, goal_id=self.identity.goal_id,
            agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
            turn_instance_id=self.identity.turn_instance_id, registry_path=self.registry_path, goal_ref=self.goal_ref)
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
        facts=_local_source_facts(runtime_root, registry_path, state_file, identity, goal_ref, first_delivery=True))


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


def _inspect_attempt(root: Path, identity: SettlementIdentity, purpose: str = "first_delivery") -> dict[str, Any]:
    # Called with the original index and source locks held. The TS inspector is
    # read-only; only this locked IO adapter can clear a proved absent marker.
    result = _checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
        "runtime_root": str(root.resolve()), "identity": identity.as_dict(), "purpose": purpose, "check_other_attempts": True})
    receipt = result.pop("receipt", None)
    if receipt is not None:
        atomic_write_json(_receipt_path(root, identity, purpose), receipt)
    return result


def first_delivery_progress(
    root: Path, readback: Any, *, canonical_todos: list[dict[str, Any]] | None = None,
    observation_unknown: bool = False, registry_path: Path | None = None,
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
    try:
        if not first_delivery_context_enrolled(root, identity) and not checkpoint_continuation_required(readback):
            return None
    except (ValueError, OSError):
        unknown = True
    try:
        if canonical_todos is None:
            canonical_todos = _delivery_observation_todos(root, identity.goal_id, registry_path)
        selected = next((todo for todo in canonical_todos if todo["todo_id"] == identity.todo_id), {})
        committed = selected.get("status") == "done" and selected.get("completion_turn_key") in {identity.effect_id, identity.turn_instance_id}
        direction_path = _receipt_path(root, identity)
        direction = _read_context_receipt(direction_path) if direction_path.exists() else {}
    except (ValueError, OSError):
        direction = {}
        unknown = True
    return effect_runtime_result("turn.first_delivery.evaluate", {
        "phase": "project", "result_committed": committed,
        "checkpoint_continuation": checkpoint_continuation_required(readback),
        "completion_event": readback.completion_event,
        # Observation consumes stage markers, not the complete Agent input or
        # an append's projected index record. Keep the ordinary RPC bounded.
        "writeback_run": {"vision_checkpoint": (readback.writeback_run or {}).get("vision_checkpoint")},
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
    """Isolate historical observation faults; admission still uses strict reads.

    An unreadable digest cannot be assigned to an Agent. Keep that Goal-scoped
    uncertainty visible without replacing another lane's normal next action.
    """
    identities = {}
    errors = []
    for path in (root / "goals" / goal_id / "checkpoint-contexts").glob("*.json"):
        try:
            receipt = _read_context_receipt(path)
        except (ValueError, OSError):
            errors.append({"scope": "goal", "code": "checkpoint_receipt_unavailable", "receipt": path.stem})
            continue
        binding = receipt.get("identity", {})
        if receipt.get("purpose") not in ("first_delivery", "delivery_result"):
            continue
        observed_agent = binding.get("agent_id") if isinstance(binding, dict) else None
        if isinstance(observed_agent, str) and observed_agent and agent_id is not None and observed_agent != agent_id:
            continue
        try:
            identity = SettlementIdentity.from_runtime_payload(binding)
            if (receipt.get("schema_version") != "checkpoint_read_context_v2" or identity.goal_id != goal_id
                    or path != _receipt_path(root, identity, receipt["purpose"])):
                raise ValueError("checkpoint receipt binding mismatch")
        except (ValueError, RuntimeError):
            errors.append({"scope": "goal", "code": "checkpoint_receipt_invalid", "receipt": path.stem})
            continue
        identities[identity.effect_id] = identity.as_dict()
    # Share the complete current authority snapshot across observed identities.
    # Do not cap the historical scan or silently lose older pending work.
    canonical_todos = None
    if identities:
        try:
            canonical_todos = _delivery_observation_todos(root, goal_id, registry_path)
        except (ValueError, OSError):
            errors.append({"scope": "goal", "code": "checkpoint_authority_unavailable"})
    for binding in reversed(list(identities.values())):
        try:
            identity = SettlementIdentity.from_runtime_payload(binding)
            observation_unknown = any(error.get("receipt") in {
                _receipt_path(root, identity, purpose).stem for purpose in ("first_delivery", "delivery_result")
            } for error in errors)
            readback = read_heartbeat_settlement(root, goal_id=goal_id, agent_id=binding["agent_id"],
                todo_id=binding.get("todo_id"), replan_obligation_id=binding.get("replan_obligation_id"),
                turn_instance_id=binding["turn_instance_id"])
            if readback is None or canonical_todos is None:
                raise ValueError("enrolled checkpoint readback unavailable")
            progress = first_delivery_progress(root, readback, canonical_todos=canonical_todos,
                observation_unknown=observation_unknown)
        except (ValueError, OSError):
            progress = effect_runtime_result("turn.first_delivery.evaluate", {"phase": "project", "unknown": True})
        if progress is not None and progress["stage"] != "settled":
            return {**progress, "settlement_identity": binding, **({"observation_errors": errors} if errors else {})}
    if errors:
        return {**effect_runtime_result("turn.first_delivery.evaluate", {
            "phase": "project", "observation_unavailable": True}),
            "goal_id": goal_id, "agent_id": agent_id, "observation_errors": errors}
    return None


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
                result.update(
                    facts=_local_source_facts(runtime_root, registry_path, state_file, identity, goal_ref, first_delivery=True),
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
    *, first_delivery: bool = False,
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
    if first_delivery:
        from ...state_refresh import resolve_goal_state, registered_agents_for_goal

        registry = load_registry(registry_path)
        goal, _, current_path = resolve_goal_state(registry=registry, goal_id=identity.goal_id,
            project_override=None, state_file_override=None)
        if current_path.resolve() != state_file.resolve() or identity.agent_id not in registered_agents_for_goal(goal):
            raise ValueError("first delivery source or Agent registration changed; reread the original Turn")
        facts["source"].update(registry_path=str(registry_path.resolve()), goal_ref=goal_ref,
            registry_goal=goal)
        facts["checkpoint_identity"] = identity.as_dict()
    return facts


def read_checkpoint_context(
    *, registry_path: Path, runtime_root_override: str | None, goal_id: str,
    agent_id: str, todo_id: str | None, turn_instance_id: str,
    replan_obligation_id: str | None = None, project: Path | None = None,
    state_file: Path | None = None, dependency_todo_ids: list[str] | None = None,
    goal_ref: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
) -> dict[str, Any]:
    # Local import avoids a cycle with refresh-state's persistence adapter.
    from ...state_refresh import resolve_goal_state, registered_agents_for_goal

    if purpose == "first_delivery" and goal_ref is not None:
        raise ValueError("First delivery freshness currently supports the local-registry Goal profile; source-session GoalRef admission is not qualified.")
    goal_id = validate_goal_id_path_segment(goal_id)
    registry = load_registry(registry_path)
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
        continuation = checkpoint_continuation_required(readback)
        if continuation and purpose != "delivery_result" and decision_scope is None:
            decision_scope = "goal"
        elif decision_scope is None:
            decision_scope = "agent_lane"
        with _source_guard(root, goal_id, path, registry_path if first_delivery else None):
            if purpose == "first_delivery":
                attempt = _inspect_attempt(root, identity)
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
                if purpose == "delivery_result" and (previous_receipt or {}).get("commit_attempt") is not None:
                    attempt = _inspect_attempt(root, identity, purpose)
                    if attempt.get("status") == "committed":
                        raise CheckpointReadContextRejected({"ok": False,
                            "error_code": "delivery_result_already_committed", "reread_required": False,
                            "error": "Progress is already committed. Replay its original writeback; do not replace its work basis."})
                    previous_receipt = _read_context_receipt(_receipt_path(root, identity, purpose))
                result = _resolve(root, phase="read", identity=identity.as_dict(), prior=readback.writeback_run,
                    writeback_committed=readback.writeback.failure is None,
                    quota_spend_committed=readback.spend_run is not None or readback.spend_event is not None,
                    semantic_replan_guard=readback.semantic_replan_guard,
                    purpose=purpose, decision_scope=decision_scope,
                    receipt=previous_receipt,
                    result_only=purpose == "delivery_result" and result_work_required(readback),
                    checkpoint_continuation=continuation,
                    full_frontier=continuation and purpose != "delivery_result",
                    admitted_turn=readback.heartbeat_receipt is not None,
                    read_context_id=uuid4().hex, dependency_todo_ids=dependency_todo_ids or [],
                    facts=_local_source_facts(root, registry_path, path, identity, goal_ref, first_delivery=first_delivery))
                receipt = result.pop("receipt")
                atomic_write_json(_receipt_path(root, identity, purpose), receipt)
    return {**result, "read_context_id": receipt["read_context_id"], "settlement_identity": identity.as_dict(),
        "purpose": purpose,
        "instructions": ("Read this basis and revalidate the retained candidate or in-flight progress. Retry the original submission; todo complete requires --delivery-read-context. This read does not complete the Todo. "
        if purpose == "delivery_result" else "Read this basis and judge the direction again. Echo read_context_id as --checkpoint-read-context in the direction refresh for this exact Turn. ") +
        "A new checkpoint-context read replaces this receipt; do not run parallel confirmations "
        "for the same Turn. On stale/replaced context, reread and rejudge; do not repeat task mutations or spend."}


@contextmanager
def checkpoint_commit_guard(
    *, runtime_root: Path, registry_path: Path, state_file: Path,
    identity: SettlementIdentity, read_context_id: str | None,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Capture/preview under source locks. The native save repeats the check
    under the real provider fence; this preliminary check is not the commit."""
    protected = purpose != "supplement_checkpoint"
    with _source_guard(runtime_root, identity.goal_id, state_file, registry_path if protected else None):
        readback = read_heartbeat_settlement(runtime_root, goal_id=identity.goal_id,
            agent_id=identity.agent_id, todo_id=identity.todo_id, turn_instance_id=identity.turn_instance_id,
            replan_obligation_id=identity.replan_obligation_id, registry_path=registry_path, goal_ref=goal_ref,
            source_admission=source_admission,
            borrow_source_admission=source_admission is not None)
        if protected:
            _inspect_attempt(runtime_root, identity, purpose)
        try:
            receipt = json.loads(_receipt_path(runtime_root, identity, purpose).read_text(encoding="utf-8"))
        except FileNotFoundError:
            receipt = None
        result = _resolve(runtime_root, phase="check", identity=identity.as_dict(), read_context_id=read_context_id,
            checkpoint_continuation=readback is not None and checkpoint_continuation_required(readback),
            purpose=purpose, decision_scope=decision_scope,
            receipt=receipt, facts=_local_source_facts(
                runtime_root,
                registry_path,
                state_file,
                identity,
                goal_ref,
                first_delivery=protected,
            ))
        yield result


def commit_checkpoint_run(
    *, runtime_root: Path, registry_path: Path, state_file: Path, identity: SettlementIdentity,
    refresh_retry: dict[str, Any], record: dict[str, Any], index_record: dict[str, Any], markdown: str,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
    read_context_id: str | None = None,
) -> dict[str, Any]:
    """Handoff the held locks and parsed bytes to one native save operation."""
    root = runtime_root.resolve()
    index = root / "goals" / identity.goal_id / "runs" / "index.jsonl"
    protected = purpose != "supplement_checkpoint"
    targets = (index, *((registry_path,) if protected else ()),
               shadow_maintenance_lock_target(root, identity.goal_id),
               legacy_coordination_todo_lock_path(runtime_root=root, goal_id=identity.goal_id), state_file)
    result = _checkpoint_effect("goal.checkpoint_read_context.commit", {
        "runtime_root": str(root), "state_file": str(state_file.resolve()), "identity": identity.as_dict(),
        "purpose": purpose, "decision_scope": decision_scope, "read_context_id": read_context_id,
        **({"registry_path": str(registry_path.resolve()),
            "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest()}
           if protected else {}),
        "locks": [cross_runtime_lock_witness(target) for target in targets],
        "state_sha256": hashlib.sha256(state_file.read_bytes()).hexdigest(),
        "index_sha256": hashlib.sha256(index.read_bytes() if index.exists() else b"").hexdigest(),
        "facts": _local_source_facts(
            root,
            registry_path,
            state_file,
            identity,
            goal_ref,
            first_delivery=protected,
        ),
        "refresh_retry": refresh_retry, "record": record, "index_record": index_record, "markdown": markdown,
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
        raise CheckpointReadContextRejected(result)
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
