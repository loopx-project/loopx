"""Source/receipt I/O only; checkpoint_read_context.ts owns decision semantics."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ...file_lock import exclusive_cross_runtime_file_lock, exclusive_run_index_lock, cross_runtime_lock_witness
from ...history import load_index, load_registry
from ...paths import resolve_runtime_root
from ...registry import atomic_write_json
from ...runtime import validate_goal_id_path_segment
from ..coordination.legacy_writer_fence import legacy_coordination_todo_lock_path
from ..coordination.shadow_management import shadow_maintenance_lock_target, require_shadow_primary_write_allowed
from ..effect_runtime import effect_runtime_result, EffectRuntimeRejected
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
        return effect_runtime_result(method, request, large_local_snapshot=True)
    except EffectRuntimeRejected as error:
        raise CheckpointReadContextRejected({"ok": False, "error": str(error),
            "error_code": error.diagnostic_code, "reread_required": False}) from error


def _evaluate(**request: Any) -> dict[str, Any]:
    result = _checkpoint_effect("goal.checkpoint_read_context.evaluate", request)
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise RuntimeError("invalid typed checkpoint read context result")
    if not result["ok"]:
        raise CheckpointReadContextRejected(result)
    return result


def _receipt_path(root: Path, identity: SettlementIdentity) -> Path:
    digest = hashlib.sha256(identity.effect_id.encode()).hexdigest()
    return root / "goals" / identity.goal_id / "checkpoint-contexts" / f"{digest}.json"


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
def _source_guard(root: Path, goal_id: str, state_file: Path) -> Iterator[None]:
    """Caller holds runs/index first. Match promotion's M -> Todo -> state order.

    M prevents source cutover; it does not exclude canonical provider commits.
    The native commit additionally fences the real provider through its append.
    Do not run projection sync or a new state mutation inside this guard.
    """
    with ExitStack() as locks:
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
    newest = [run for _, run in sorted(enumerate(runs),
        key=lambda pair: (str(pair[1].get("generated_at") or ""), pair[0]), reverse=True)]
    return {
        "todos": todos, "frontmatter": metadata, "goal_prose": prose, "acceptance": None,
        "agent_vision": latest_agent_vision_from_runs(newest, goal_id=identity.goal_id, agent_id=identity.agent_id),
        "source": {"state_file": str(state_file.resolve()), "runtime_root": str(root.resolve()),
                   "authority": "legacy_markdown"},
    }


def _source_facts(root: Path, registry_path: Path, state_file: Path, identity: SettlementIdentity) -> dict[str, Any]:
    # The typed owner derives Todo and complete acceptance from one head and
    # fails closed after cutover. Local parsed Markdown cannot override it.
    return _checkpoint_effect("goal.checkpoint_read_context.source", {
        "runtime_root": str(root.resolve()), "goal_id": identity.goal_id,
        "facts": _local_source_facts(root, registry_path, state_file, identity),
    })


def read_checkpoint_context(
    *, registry_path: Path, runtime_root_override: str | None, goal_id: str,
    agent_id: str, todo_id: str | None, turn_instance_id: str,
    replan_obligation_id: str | None = None, project: Path | None = None,
    state_file: Path | None = None, dependency_todo_ids: list[str] | None = None,
) -> dict[str, Any]:
    # Local import avoids a cycle with refresh-state's persistence adapter.
    from ...state_refresh import resolve_goal_state, registered_agents_for_goal

    goal_id = validate_goal_id_path_segment(goal_id)
    registry = load_registry(registry_path)
    root = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    goal, _, path = resolve_goal_state(registry=registry, goal_id=goal_id,
        project_override=project, state_file_override=state_file)
    if agent_id not in registered_agents_for_goal(goal):
        raise ValueError("checkpoint-context requires a registered Agent")
    with exclusive_run_index_lock(root / "goals" / goal_id / "runs" / "index.jsonl", operation="checkpoint-context"):
        require_complete_checkpoint_index(root / "goals" / goal_id / "runs" / "index.jsonl")
        readback = read_heartbeat_settlement(root, goal_id=goal_id, agent_id=agent_id,
            todo_id=todo_id, turn_instance_id=turn_instance_id, replan_obligation_id=replan_obligation_id)
        if readback is None or readback.identity.value is None or readback.writeback_run is None:
            raise ValueError("checkpoint-context requires the original committed Turn writeback")
        identity = readback.identity.value
        with _source_guard(root, goal_id, path):
            result = _evaluate(phase="read", identity=identity.as_dict(), prior=readback.writeback_run,
                read_context_id=uuid4().hex, dependency_todo_ids=dependency_todo_ids or [],
                facts=_source_facts(root, registry_path, path, identity))
            receipt = result.pop("receipt")
            atomic_write_json(_receipt_path(root, identity), receipt)
    return {**result, "read_context_id": receipt["read_context_id"], "settlement_identity": identity.as_dict(),
        "instructions": "Read this basis and judge the direction again. Echo read_context_id as "
        "--checkpoint-read-context in the checkpoint-only refresh for this exact Turn. "
        "A new checkpoint-context read replaces this receipt; do not run parallel confirmations "
        "for the same Turn. On stale/replaced context, reread and rejudge; do not repeat task mutations or spend."}


@contextmanager
def checkpoint_commit_guard(
    *, runtime_root: Path, registry_path: Path, state_file: Path,
    identity: SettlementIdentity, read_context_id: str | None,
) -> Iterator[dict[str, Any]]:
    """Capture/preview under source locks. The native save repeats the check
    under the real provider fence; this preliminary check is not the commit."""
    with _source_guard(runtime_root, identity.goal_id, state_file):
        try:
            receipt = json.loads(_receipt_path(runtime_root, identity).read_text(encoding="utf-8"))
        except FileNotFoundError:
            receipt = None
        result = _evaluate(phase="check", identity=identity.as_dict(), read_context_id=read_context_id,
            receipt=receipt, facts=_source_facts(runtime_root, registry_path, state_file, identity))
        yield result


def commit_checkpoint_run(
    *, runtime_root: Path, registry_path: Path, state_file: Path, identity: SettlementIdentity,
    refresh_retry: dict[str, Any], record: dict[str, Any], index_record: dict[str, Any], markdown: str,
) -> dict[str, Any]:
    """Handoff the held locks and parsed bytes to one native save operation."""
    root = runtime_root.resolve()
    index = root / "goals" / identity.goal_id / "runs" / "index.jsonl"
    targets = (index, shadow_maintenance_lock_target(root, identity.goal_id),
               legacy_coordination_todo_lock_path(runtime_root=root, goal_id=identity.goal_id), state_file)
    result = _checkpoint_effect("goal.checkpoint_read_context.commit", {
        "runtime_root": str(root), "state_file": str(state_file.resolve()), "identity": identity.as_dict(),
        "locks": [cross_runtime_lock_witness(target) for target in targets],
        "state_sha256": hashlib.sha256(state_file.read_bytes()).hexdigest(),
        "index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        "facts": _local_source_facts(root, registry_path, state_file, identity),
        "refresh_retry": refresh_retry, "record": record, "index_record": index_record, "markdown": markdown,
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
