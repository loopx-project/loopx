"""Host command formatting and Goal admission for native Inbox observations."""
import shlex
from pathlib import Path

from .goal_instance_scope import collaboration_goal_scope
from .inbox import read_inbox_transaction


def delegation_read_command(root, registry, goal_id, agent_id):
    return shlex.join(["loopx", "--registry", str(registry), "--runtime-root", str(root),
        "manager-inbox", "read", "--goal-id", goal_id, "--agent-id", agent_id])


def read_delegation_turn_context(root: Path, registry: Path, goal_id: str, agent_id: str,
                                 *, read_command: str | None = None) -> dict:
    with collaboration_goal_scope(registry, goal_id=goal_id, agents=(agent_id,)) as scope:
        return read_inbox_transaction(root, {
            "operation": "turn_context", "goal_id": goal_id, "agent_id": agent_id,
            "read_command": read_command or delegation_read_command(root, registry, goal_id, agent_id),
        }, scope=scope)
