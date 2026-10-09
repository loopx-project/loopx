"""Chat selection over explicit operator bindings and the existing Turn owner.

This adapter provisions no work, identity, grants, host profile or scheduler.
Context receipt, launch observation, receiver adoption and return stay separate.
"""

from pathlib import Path
from collections.abc import Callable
from typing import Any

from ...agent_registry import load_goal_from_registry
from ...collaboration_mcp import Delegations
from ...control_plane.collaboration import conversation_scope
from ...control_plane.collaboration.source_grant_observation import (
    source_execution_bindings,
)
from ...control_plane.effect_runtime import EffectRuntimeRejected
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
        # Recover an existing operation instead of probing a now-completed Todo
        # and misreporting an accepted result as a refused new launch.
        if service.path(operation).exists():
            row = service.read(operation)
            return {"submitted": True, "operation_id": operation, "todo_id": binding["todo_id"],
                    "status": row["status"], "replayed": True}
        preflight = service.inspect(selected)
        # Match the existing start owner: an unprobed configured runtime may
        # attempt bounded execution, but is never presented as ready/running.
        # Known refusal, missing canonical acceptance or an unavailable runtime
        # must stop before dispatch. Actual launch and acceptance remain owned
        # by the governed Turn, not this point-in-time preview.
        if (preflight["state"] not in {"launchable", "runtime_unverified"}
                or not all(preflight.get(key) is True for key in
                           ("turn_eligible", "acceptance_ready", "authority_ready"))):
            return {"submitted": False, "preflight": preflight, "reason": "execution_not_launchable"}
        # Re-read the source and operator selection after the potentially slow
        # preview. Delegations.start itself rechecks the exact binding and Turn.
        if not execution_allowed() or grant not in _grants(root, registry, session, turn):
            raise ValueError("source execution grant changed before launch")
        result = service.start(selected, operation, request["brief"],
                               conversation={"session_id": session["session_id"], "turn_id": turn["turn_id"]},
                               source_request_id=receipt["request_id"])
        return {"submitted": True, "operation_id": operation, "todo_id": binding["todo_id"],
                "status": result["status"], "runtime_readiness": preflight["state"], "replayed": False}
    except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRejected):
        return {"submitted": False, "reason": "execution_binding_or_admission_unavailable"}


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
    source_store=None,
) -> dict[str, Any]:
    """Present context delivery and separately admitted execution on one path."""
    from . import deliver

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
                "message": handoff_message(receipt, execution,
                                           brief=response["context_handoff"].get("brief"))}
    except (OSError, ValueError):
        return {**response, "proposals": [], "gate": None,
                "message": "材料尚未转交：目标绑定、来源授权或持久收件回读未通过。需要修复交接链路；没有改动任务或优先级。"}
