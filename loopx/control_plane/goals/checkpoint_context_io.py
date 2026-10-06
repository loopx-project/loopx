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
from ...registry import atomic_write_json
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


def first_delivery_context_enrolled(root: Path, identity: SettlementIdentity) -> bool:
    """Read the persisted protocol selection; enrollment survives caller restart."""
    for purpose in ("first_delivery", "delivery_result"):
        try:
            receipt = _read_context_receipt(_receipt_path(root, identity, purpose))
        except FileNotFoundError:
            continue
        if receipt.get("purpose") == purpose:
            return True
    return False


def _inspect_attempt(root: Path, identity: SettlementIdentity) -> dict[str, Any]:
    # Called with the original index and source locks held. The TS inspector is
    # read-only; only this locked IO adapter can clear a proved absent marker.
    result = _checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
        "runtime_root": str(root.resolve()), "identity": identity.as_dict(), "check_other_attempts": True})
    receipt = result.pop("receipt", None)
    if receipt is not None:
        atomic_write_json(_receipt_path(root, identity), receipt)
    return result


def first_delivery_progress(
    root: Path, readback: Any, *, canonical_todos: list[dict[str, Any]] | None = None,
    observation_unknown: bool = False,
) -> dict[str, Any] | None:
    """Observe protocol receipts for quota consumers; this grants no write authority.

    A snapshot may lag a concurrent writer. Ambiguity is displayed as unknown;
    only the locked replay path can resolve it or admit another operation.
    """
    identity = readback.identity.value
    if identity is None:
        return None
    from ..coordination.local_authority import read_canonical_todos_if_promoted

    committed = False
    unknown = observation_unknown
    try:
        if not first_delivery_context_enrolled(root, identity):
            return None
    except (ValueError, OSError):
        unknown = True
    try:
        if canonical_todos is None:
            canonical = read_canonical_todos_if_promoted(runtime_root=root, goal_id=identity.goal_id)
            canonical_todos = (canonical or {}).get("todos", [])
        selected = next((todo for todo in canonical_todos if todo["todo_id"] == identity.todo_id), {})
        committed = selected.get("status") == "done" and selected.get("completion_turn_key") in {identity.effect_id, identity.turn_instance_id}
        direction_path = _receipt_path(root, identity)
        direction = _read_context_receipt(direction_path) if direction_path.exists() else {}
    except (ValueError, OSError):
        direction = {}
        unknown = True
    return effect_runtime_result("turn.first_delivery.evaluate", {
        "phase": "project", "result_committed": committed,
        "writeback_run": readback.writeback_run, "direction_receipt": direction, "unknown": unknown,
        "quota_spent": readback.spend_run is not None,
        "settlement_complete": readback.terminal_settlement.failure is None,
    })


def pending_first_delivery_progress(root: Path, goal_id: str, agent_id: str | None = None) -> dict[str, Any] | None:
    """Isolate historical observation faults; admission still uses strict reads.

    An unreadable digest cannot be assigned to an Agent. Keep that Goal-scoped
    uncertainty visible without replacing another lane's normal next action.
    """
    from ..coordination.local_authority import read_canonical_todos_if_promoted

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
            canonical = read_canonical_todos_if_promoted(runtime_root=root, goal_id=goal_id)
            canonical_todos = (canonical or {}).get("todos", [])
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
    if not read_context_id:
        try:
            if not first_delivery_context_enrolled(runtime_root, identity):
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
                    facts=_local_source_facts(runtime_root, registry_path, state_file, identity, first_delivery=True),
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
    text = state_file.read_text(encoding="utf-8")
    metadata, body = split_state_frontmatter(text)
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
    purpose: str = "supplement_checkpoint", decision_scope: str = "agent_lane",
) -> dict[str, Any]:
    # Local import avoids a cycle with refresh-state's persistence adapter.
    from ...state_refresh import resolve_goal_state, registered_agents_for_goal

    if purpose != "supplement_checkpoint" and goal_ref is not None:
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
        with _source_guard(root, goal_id, path, registry_path if first_delivery else None):
            if purpose == "first_delivery":
                attempt = _inspect_attempt(root, identity)
                if attempt.get("status") == "committed":
                    return {**attempt, "settlement_identity": identity.as_dict(), "purpose": purpose,
                        "instructions": "Direction already committed. Replay the original refresh or resume this Turn's remaining settlement; do not request another direction."}
            previous_receipt = None
            if first_delivery:
                try:
                    previous_receipt = json.loads(_receipt_path(root, identity, purpose).read_text(encoding="utf-8"))
                except FileNotFoundError:
                    pass
            result = _resolve(root, phase="read", identity=identity.as_dict(), prior=readback.writeback_run,
                purpose=purpose, decision_scope=decision_scope,
                receipt=previous_receipt,
                admitted_turn=readback.heartbeat_receipt is not None,
                read_context_id=uuid4().hex, dependency_todo_ids=dependency_todo_ids or [],
                facts=_local_source_facts(root, registry_path, path, identity, goal_ref, first_delivery=first_delivery))
            receipt = result.pop("receipt")
            atomic_write_json(_receipt_path(root, identity, purpose), receipt)
    return {**result, "read_context_id": receipt["read_context_id"], "settlement_identity": identity.as_dict(),
        "purpose": purpose,
        "instructions": ("Read this basis and validate the candidate. Echo read_context_id as --delivery-read-context in todo complete. "
        if purpose == "delivery_result" else "Read this basis and judge the direction again. Echo read_context_id as --checkpoint-read-context in the direction refresh for this exact Turn. ") +
        "A new checkpoint-context read replaces this receipt; do not run parallel confirmations "
        "for the same Turn. On stale/replaced context, reread and rejudge; do not repeat task mutations or spend."}


@contextmanager
def checkpoint_commit_guard(
    *, runtime_root: Path, registry_path: Path, state_file: Path,
    identity: SettlementIdentity, read_context_id: str | None,
    goal_ref: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Capture/preview under source locks. The native save repeats the check
    under the real provider fence; this preliminary check is not the commit."""
    first_delivery = purpose == "first_delivery"
    with _source_guard(runtime_root, identity.goal_id, state_file, registry_path if first_delivery else None):
        if first_delivery:
            _inspect_attempt(runtime_root, identity)
        try:
            receipt = json.loads(_receipt_path(runtime_root, identity).read_text(encoding="utf-8"))
        except FileNotFoundError:
            receipt = None
        result = _resolve(runtime_root, phase="check", identity=identity.as_dict(), read_context_id=read_context_id,
            purpose=purpose, decision_scope=decision_scope,
            receipt=receipt, facts=_local_source_facts(
                runtime_root,
                registry_path,
                state_file,
                identity,
                goal_ref,
                first_delivery=first_delivery,
            ))
        yield result


def commit_checkpoint_run(
    *, runtime_root: Path, registry_path: Path, state_file: Path, identity: SettlementIdentity,
    refresh_retry: dict[str, Any], record: dict[str, Any], index_record: dict[str, Any], markdown: str,
    goal_ref: Mapping[str, Any] | None = None,
    source_admission: Mapping[str, Any] | None = None,
    purpose: str = "supplement_checkpoint", decision_scope: str | None = None,
) -> dict[str, Any]:
    """Handoff the held locks and parsed bytes to one native save operation."""
    root = runtime_root.resolve()
    index = root / "goals" / identity.goal_id / "runs" / "index.jsonl"
    targets = (index, *((registry_path,) if purpose == "first_delivery" else ()),
               shadow_maintenance_lock_target(root, identity.goal_id),
               legacy_coordination_todo_lock_path(runtime_root=root, goal_id=identity.goal_id), state_file)
    result = _checkpoint_effect("goal.checkpoint_read_context.commit", {
        "runtime_root": str(root), "state_file": str(state_file.resolve()), "identity": identity.as_dict(),
        "purpose": purpose, "decision_scope": decision_scope,
        **({"registry_path": str(registry_path.resolve()),
            "registry_sha256": hashlib.sha256(registry_path.read_bytes()).hexdigest()}
           if purpose == "first_delivery" else {}),
        "locks": [cross_runtime_lock_witness(target) for target in targets],
        "state_sha256": hashlib.sha256(state_file.read_bytes()).hexdigest(),
        "index_sha256": hashlib.sha256(index.read_bytes() if index.exists() else b"").hexdigest(),
        "facts": _local_source_facts(
            root,
            registry_path,
            state_file,
            identity,
            goal_ref,
            first_delivery=purpose == "first_delivery",
        ),
        "refresh_retry": refresh_retry, "record": record, "index_record": index_record, "markdown": markdown,
        "committed_agent_vision": latest_agent_vision_from_runs([index_record], goal_id=identity.goal_id, agent_id=identity.agent_id),
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
    if isinstance(prior.get("vision_checkpoint"), dict) and prior["vision_checkpoint"].get("read_context"):
        _checkpoint_effect("goal.checkpoint_read_context.inspect_replay", {
            "runtime_root": str(runtime_root.resolve()), "goal_id": goal_id, "prior": prior,
        })


def render_checkpoint_context(payload: dict[str, Any]) -> str:
    # The decision basis is private local state, not a public/global projection.
    return "# LoopX Checkpoint Context\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"
