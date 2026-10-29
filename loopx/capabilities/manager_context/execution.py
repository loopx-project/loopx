"""Chat selection over explicit operator bindings and the existing Turn owner.

This adapter provisions no work, identity, grants, host profile or scheduler.
Context receipt, launch observation, receiver adoption and return stay separate.
"""

from pathlib import Path
from time import monotonic
from collections.abc import Callable, Iterator
from typing import Any, TYPE_CHECKING, cast
import logging

if TYPE_CHECKING:
    from ...chat_store import ChatSessionStore

from ...agent_registry import load_goal_from_registry
from ...collaboration_mcp import Delegations
from ...control_plane.collaboration import conversation_scope
from ...control_plane.collaboration.inbox import _root
from ...control_plane.content_digest import BARE_SHA256_PATTERN
from ...control_plane.collaboration.source_grant_observation import (
    read_delegation_source_execution, source_execution_bindings,
)
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...orchestration import compact_orchestration_policy, normalize_subagent_execution_config


def _grants(root: Path, registry: Path, session: dict[str, Any], turn: dict[str, Any]) -> list[dict[str, Any]]:
    if conversation_scope(session, origin=turn.get("origin", "unknown"))["kind"] != "external_audience":
        return []
    result = source_execution_bindings(root, registry, session, turn)
    return list(result["bindings"])


def _service(root: Path, registry: Path, grant: dict[str, Any]) -> tuple[Delegations, dict[str, Any]]:
    goal = load_goal_from_registry(registry, grant["goal_id"])
    if not goal:
        raise ValueError("execution Goal unavailable")
    configured = compact_orchestration_policy(goal.get("spawn_policy")).get("execution_config")
    if not configured:
        raise ValueError("Goal execution configuration unavailable")
    relative = Path(normalize_subagent_execution_config(configured))
    workspace = Path(goal["repo"]).resolve()
    config_root = workspace / ".loopx" / "config"
    config = (workspace / relative).resolve()
    if not config.is_relative_to(config_root) or not config.is_file() or config.stat().st_size > 1_000_000:
        raise ValueError("Goal execution configuration unavailable")
    service = Delegations(root, registry, grant["goal_id"], grant["requester_agent_id"], config)
    binding = service.binding(grant["binding_id"], require_active=True)
    if binding["agent_id"] != grant["agent_id"]:
        raise ValueError("execution binding recipient changed")
    return service, binding


def catalog(root: Path, registry: Path, session: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    """Expose only exact task choices, never commands, paths or host credentials."""
    try:
        rows = []
        for grant in _grants(root, registry, session, turn):
            _, binding = _service(root, registry, grant)
            rows.append({key: grant[key] for key in ("goal_id", "agent_id", "binding_id")}
                        | {"todo_id": binding["todo_id"]})
        return {"available": True, "bindings": rows}
    except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRejected):
        return {"available": False, "bindings": [], "reason": "execution_bindings_unavailable"}


def dispatch(root: Path, registry: Path, *, session: dict[str, Any], turn: dict[str, Any],
             request: dict[str, Any], receipt: dict[str, Any],
             execution_allowed: Callable[[], bool]) -> dict[str, Any]:
    """Submit this delivered brief to exactly one separately granted binding.

    Stable operation identity uses the original inbox receipt. No retry invents
    another operation, and no completed/stopped binding is reset or replaced.
    The independent receiver must adopt and report through the existing inbox.
    """
    selected = request.get("execution_binding_id")
    if selected is None:
        return {"submitted": False}
    try:
        if not execution_allowed():
            raise ValueError("source Turn is no longer active")
        target = {key: request[key] for key in ("goal_id", "agent_id")}
        if receipt.get("status") != "delivered" or any(receipt.get(key) != value for key, value in target.items()):
            raise ValueError("original handoff receipt mismatch")
        grant = next((row for row in _grants(root, registry, session, turn)
                      if all(row[key] == value for key, value in target.items())
                      and row["binding_id"] == selected), None)
        if grant is None:
            raise ValueError("execution binding not granted to this source")
        service, binding = _service(root, registry, grant)
        operation = "context-" + receipt["request_id"]
        replayed = service.path(operation).exists()
        saved = {}
        if replayed:
            row = read_delegation_source_execution(service, selected, operation,
                source_request_id=receipt["request_id"], brief=request["brief"],
                conversation={"session_id": session["session_id"], "turn_id": turn["turn_id"]})
            saved = {"operation_id": operation, "todo_id": binding["todo_id"],
                     "status": row["status"], "replayed": True}
            # Only a still-prepared operation can submit a worker. Other
            # states remain observations, including an accepted operation
            # whose now-completed Todo would refuse a new admission.
            if row["status"] != "prepared":
                return {"submitted": True, **saved}
        # A crash before spawn leaves the original prepared operation pending.
        # Its recovery needs the same fresh native preflight as first dispatch;
        # otherwise a busy/unknown lane can strand it in a premature rejection.
        preflight = service.inspect(selected, **({"source_request_id": receipt["request_id"]} if binding["todo_id"] is None else {}))
        # Match the existing start owner: an unprobed configured runtime may
        # attempt bounded execution, but is never presented as ready/running.
        # Known refusal, missing canonical acceptance or an unavailable runtime
        # must stop before dispatch. Actual launch and acceptance remain owned
        # by the governed Turn, not this point-in-time preview.
        if preflight.get("dispatch_allowed") is not True:
            if not replayed and preflight.get("queueable") is True:
                if not execution_allowed() or grant not in _grants(root, registry, session, turn):
                    raise ValueError("source execution grant changed before queueing")
                result = service.start(selected, operation, request["brief"],
                    conversation={"session_id": session["session_id"], "turn_id": turn["turn_id"]},
                    source_request_id=receipt["request_id"], _defer_worker=True)
                saved = {"operation_id": operation, "todo_id": binding["todo_id"],
                         "status": result["status"], "replayed": False}
            return {"submitted": False, **saved, "preflight": preflight, "reason": "execution_not_launchable"}
        # Re-read the source and operator selection after the potentially slow
        # preview. Delegations.start itself rechecks the exact binding and Turn.
        if not execution_allowed() or grant not in _grants(root, registry, session, turn):
            raise ValueError("source execution grant changed before launch")
        if replayed:
            # Keep the prepared-source CAS through worker startup. A delayed
            # child must not retry an operation that has already advanced.
            result = service.resume(operation, prepared_source_request_id=receipt["request_id"])
        else:
            result = service.start(selected, operation, request["brief"],
                                   conversation={"session_id": session["session_id"], "turn_id": turn["turn_id"]},
                                   source_request_id=receipt["request_id"])
        return {"submitted": True, "operation_id": operation, "todo_id": binding["todo_id"],
                "status": result["status"], "replayed": replayed,
                **({} if replayed else {"runtime_readiness": preflight["state"]})}
    except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRejected):
        return {"submitted": False, "reason": "execution_binding_or_admission_unavailable"}


class QueuedSourcePump:
    """Bounded recovery inside the existing Chat service, not a work scheduler.

    Only already-persisted Chat execution intents are candidates. A context-only
    Inbox row, operator/MCP operation or uncertain started Turn cannot enroll.
    The iterator and retry delay are host IO state; admission/once-only effects
    remain with the existing source, preflight and native Turn owners.
    """

    def __init__(self, root: Path, registry: Path, store: "ChatSessionStore") -> None:
        self.root, self.registry, self.store = root, registry, store
        self.paths: Iterator[Path] | None = None
        self.next_probe = 0.0

    def tick(self, *, cancelled: Callable[[], bool] = lambda: False) -> None:
        if cancelled() or monotonic() < self.next_probe:
            return
        self.next_probe = monotonic() + 30
        if self.paths is None:
            self.paths = (_root(self.root) / "executions").glob("*/*.json")
        # Advance the cursor past refused/unavailable rows so one request cannot
        # starve another. At most 20 local records and one full native preflight.
        for _ in range(20):
            if cancelled():
                break
            path = next(self.paths, None)
            if path is None:
                self.paths = None
                break
            try:
                if self._resume(path, cancelled):
                    break
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                # Keep the original intent. Unknown/revoked sources neither
                # become empty nor create another request or execution.
                continue

    def _resume(self, path: Path, cancelled: Callable[[], bool]) -> bool:
        from ...control_plane.collaboration.inbox import _entry, _read
        from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
        from ...control_plane.collaboration.source_chat_observation import is_source_chat_host, observe_source_chat
        from ...control_plane.turn_driver.lane_fence import turn_lane_liveness

        row = _read(path)
        identity = row.get("identity", {})
        source_id = identity.get("source_request_id") if isinstance(identity, dict) else None
        if (row.get("status") != "prepared" or not isinstance(source_id, str)
                or not BARE_SHA256_PATTERN.fullmatch(source_id) or path.is_symlink()
                or not BARE_SHA256_PATTERN.fullmatch(path.stem)):
            return False
        route = _read(_root(self.root) / "roundtrips" / (source_id + ".json"))
        if not is_source_chat_host(self.root, route, self.store):
            return False
        session, turn = observe_source_chat(self.root, route)
        target = {key: route[key] for key in ("goal_id", "agent_id")}
        for grant in _grants(self.root, self.registry, session, turn):
            if (any(grant[key] != value for key, value in target.items())
                    or grant["binding_id"] != identity["binding"]["id"]):
                continue
            service, binding = _service(self.root, self.registry, grant)
            operation = "context-" + source_id
            if service.path(operation) != path or service.read(operation)["worker_active"]:
                continue
            with collaboration_goal_scope(self.registry, goal_id=target["goal_id"], agents=(),
                    caller_goal_ref=route.get("goal_ref")) as scope:
                original = _entry(self.root, target["goal_id"], target["agent_id"], source_id, scope=scope)
            conversation = {"session_id": session["session_id"], "turn_id": turn["turn_id"]}
            def observe() -> dict[str, Any]:
                return cast(dict[str, Any], read_delegation_source_execution(service, binding["id"], operation,
                    source_request_id=source_id, brief=original["brief"], conversation=conversation))
            if observe()["status"] != "prepared":
                return False
            # A cheap scoped observation avoids native CLI/model polling while
            # another Turn still owns this recipient's lane, or it is unknown.
            lane = turn_lane_liveness(service._lane_target(binding))
            if effect_runtime_result("collaboration.delegation.lane_readiness", {"lane": {"state": lane["state"]}})["available"] is not True:
                return False
            if cancelled():
                return False
            try:
                preflight = service.inspect(binding["id"], **({"source_request_id": source_id} if binding["todo_id"] is None else {}))
                if preflight.get("dispatch_allowed") is True and not cancelled() and observe()["status"] == "prepared":
                    service.resume(operation, prepared_source_request_id=source_id)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                # A failed full preview still consumes this probe's one
                # attempt. Keep its intent and let the next cursor run proceed.
                logging.getLogger(__name__).warning("Queued source preflight unavailable; retaining original intent")
            return True
        return False


def handoff_message(
    receipt: dict[str, Any],
    execution: dict[str, Any],
    *,
    brief: dict[str, Any] | None = None,
) -> str:
    """Summarize delivered context; execution facts come only from dispatch.

    Display limits do not change the full normalized brief in the inbox.
    The sender's free-form message cannot establish delivery or completion.
    """
    blocks = [f"**已转交给 `{receipt['agent_id']}`。**"]

    def preview(value: str, limit: int) -> str:
        # One display item stays one line. This is not a semantic parser and
        # never changes the complete brief delivered to the receiver.
        text = " ".join(value.split())
        return text if len(text) <= limit else text[:limit] + "…"

    if brief:
        blocks.append(preview(brief["purpose"], 160))
        for key, label, count, limit in (
            ("acceptance", "期待的结果", 3, 100),
            ("constraints", "执行边界", 2, 90),
        ):
            items = brief[key]
            if items:
                lines = [f"**{label}**", ""]
                lines.extend("- " + preview(item, limit) for item in items[:count])
                if len(items) > count:
                    lines.append(f"- 另有 {len(items) - count} 项，已随完整简报交接。")
                blocks.append("\n".join(lines))
        blocks.append("**回传要求**：" + preview(brief["return_requirement"], 120))
        # Selection evidence, internal operation ids and historical context
        # belong to the full brief/readback, not the default chat preview.
        blocks.append("完整简报已投递；上面是摘要。")
    if execution.get("submitted"):
        state = "已提交执行，尚未确认完成。"
    elif execution.get("reason"):
        state = "交接已保存，本次未提交受控执行；是否已开始处理尚未核实。"
    else:
        state = "已送达；是否已开始处理尚未核实。"
    blocks.append(state + "处理结论会回到本次对话。")
    return "\n\n".join(blocks)


def handoff_response(
    root: Path,
    registry: Path,
    *,
    session: dict[str, Any],
    turn: dict[str, Any],
    response: dict[str, Any],
    source_authorized: Callable[[], bool],
    execution_allowed: Callable[[], bool],
    source_store: "ChatSessionStore | None" = None,
) -> dict[str, Any]:
    """Append receipt-backed status to the existing audience-processed answer.

    This presentation adapter does not reinterpret model prose as execution
    evidence or replace the answer already shown by the Chat stream.
    """
    from . import deliver

    answer = response.get("message")
    answer = answer if isinstance(answer, str) and answer.strip() else ""

    def present(notice: str) -> str:
        return answer + "\n\n" + notice if answer else notice

    try:
        if not source_authorized():
            raise ValueError("manager connection authority is no longer available")
        receipt = deliver(root, registry, session=session, turn=turn,
                          request=response["context_handoff"], source_store=source_store)
        execution = dispatch(root, registry, session=session, turn=turn,
                             request=response["context_handoff"], receipt=receipt,
                             execution_allowed=execution_allowed)
        return {**response, "proposals": [], "gate": None,
                "context_handoff_receipt": receipt, "context_execution": execution,
                "message": present(handoff_message(receipt, execution,
                    brief=None if answer else response["context_handoff"].get("brief")))}
    except (OSError, ValueError):
        return {**response, "proposals": [], "gate": None,
                "message": present("材料尚未转交：目标绑定、来源授权或持久收件回读未通过。需要修复交接链路；没有改动任务或优先级。")}
