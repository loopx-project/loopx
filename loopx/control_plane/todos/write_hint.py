from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STANDARD_TODO_WRITE_HINT_RULE = (
    "Verified owner-only blocker: add or reuse a user_gate this turn, even if noted "
    "in Next Action. Split mixed work; link the smallest blocked Todo. Give the owner "
    "the action and verification check. Before yielding, reuse a runnable independent "
    "Todo or check Goal acceptance for a useful independent successor to record. "
    "Settle any bound Turn, then rerun admission. Non-blocking requests use user_action."
)

# Non-command Worker disposition requirements preserved when standard Todo CLI
# authoring rules remain with the outer executor.
WORKER_OWNER_BLOCKER_DISPOSITION_RULE = (
    "Verified owner-only blocker: report it for a user_gate even if noted in Next Action; "
    "non-blocking requests belong in user_action. Split mixed work, isolate the smallest "
    "blocked Todo, and give the owner the exact blocking action and verification check. "
    "Before yielding, continue runnable independent work or identify a useful independent "
    "successor from Goal acceptance; the outer executor records Todos, settles the Turn, "
    "and reruns admission."
)


def worker_owner_blocker_instruction(source: bool | Mapping[str, Any] | None) -> str:
    """Return built-in Worker owner-blocker rules only when TS validated them."""
    if isinstance(source, bool):
        enabled = source
    elif isinstance(source, Mapping):
        flag = (
            source["_owner_blocker_guidance"]
            if "_owner_blocker_guidance" in source
            else source.get("owner_blocker_guidance")
        )
        enabled = flag is True
    else:
        return ""
    return WORKER_OWNER_BLOCKER_DISPOSITION_RULE if enabled else ""


def build_todo_write_hint(goal_id: str) -> dict[str, str]:
    return {
        "rule": STANDARD_TODO_WRITE_HINT_RULE,
        "user_gate_command_template": (
            f"loopx todo add --goal-id {goal_id} --role user "
            "--task-class user_gate --blocks-agent <agent-id> "
            "--unblocks-todo-id <todo-id> --text '<blocking user action and verification>'"
        ),
        "user_action_command_template": (
            f"loopx todo add --goal-id {goal_id} --role user "
            "--task-class user_action --bound-agent <id> --text '<action>'"
        ),
        "agent_todo_command_template": (
            f"loopx todo add --goal-id {goal_id} --role agent --priority P1 --text '<agent action>'"
        ),
        "section": "User Todo / Owner Review Reading Queue",
    }


def build_capability_resolution_writeback_actions(
    capability_gate: Any,
    *,
    goal_id: str,
    agent_id: str | None,
    limit: int = 3,
) -> list[str]:
    if not isinstance(capability_gate, dict):
        return []
    bindings = capability_gate.get("resolution_bindings")
    if not isinstance(bindings, list):
        return []
    actions: list[str] = []
    agent = agent_id or "<registered-agent>"
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        owner = str(binding.get("owner") or "").strip()
        capability = str(binding.get("capability") or "").strip()
        todo_id = str(binding.get("primary_blocked_todo_id") or "").strip()
        priority = str(binding.get("priority") or "P1").strip().upper()
        if not capability or not todo_id:
            continue
        if priority not in {"P0", "P1", "P2"}:
            priority = "P1"
        if owner == "user":
            text = (
                f"[{priority}-user] Provide or authorize capability {capability} "
                f"required by {todo_id}."
            )
            actions.append(
                f"loopx todo add --goal-id {goal_id} --role user "
                "--task-class user_gate --action-kind provide_capability "
                f"--target-capability {capability} --blocks-agent {agent} "
                f"--unblocks-todo-id {todo_id} --text '{text}'"
            )
        if len(actions) >= limit:
            break
    return actions
