from __future__ import annotations

from typing import Any


def build_todo_write_hint(goal_id: str) -> dict[str, str]:
    return {
        "rule": (
            "When verified evidence shows only the owner can unblock an action, "
            "record or reuse a User Todo gate this turn, even if Next Action "
            "already mentions it. Link the smallest blocked Todo; split it out "
            "if its parent still has independent work. Tell the owner what to "
            "do and how to verify it. Before yielding, reuse a runnable independent "
            "Todo or check the Goal's acceptance for a useful independent successor "
            "to record. Settle any bound Turn and rerun normal admission to continue. "
            "Use a user_action for non-blocking requests."
        ),
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
