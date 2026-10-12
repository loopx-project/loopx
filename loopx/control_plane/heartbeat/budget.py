"""Heartbeat prompt budget helpers inside the heartbeat bounded context."""

from __future__ import annotations

import shlex
from typing import Any


# Presentation regression budgets, not external transport or execution limits.
# Keep useful admission and authority instructions readable; do not abbreviate
# them solely to fit a historical character ceiling.
INTERFACE_BUDGET_CHARS = {
    "full": 12_000,
    "compact": 6_500,
    "brief": 4_300,
    "thin": 3_000,
    "visible_goal": 4_000,
}
NATIVE_GOAL_HOST_MAX_CHARS = INTERFACE_BUDGET_CHARS["visible_goal"]

# Reward Memory's outcome contract is intentionally readable rather than
# squeezed into the ordinary heartbeat allowance.  Keep this fixed and tied to
# the generated contract marker so feature-off prompts retain the old budget.
REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS = 640
_REWARD_MEMORY_PROMPT_MARKER = "--reward-memory-reflection-json"


def heartbeat_prompt_mode(
    *,
    full: bool = False,
    compact: bool = False,
    brief: bool = False,
    thin: bool = False,
) -> str:
    if full:
        return "full"
    if thin:
        return "thin"
    if brief:
        return "brief"
    if compact:
        return "compact"
    return "thin"


def prompt_budget_text(
    text: str, *, goal_id: str, active_state: str,
    registry_path: str | None = None,
) -> str:
    normalized = text
    # Compare authored guidance independently of the host's selected path length.
    # char_count below still reports the actual transport/body size.
    if registry_path:
        normalized = normalized.replace(shlex.quote(registry_path), "<REGISTRY_PATH>")
        normalized = normalized.replace(registry_path, "<REGISTRY_PATH>")
    return normalized.replace(goal_id, "<GOAL_ID>").replace(active_state, "<ACTIVE_STATE>")


def build_interface_budget(
    *,
    task_body: str,
    goal_id: str,
    active_state: str,
    registry_path: str | None = None,
    full: bool = False,
    compact: bool = False,
    brief: bool = False,
    thin: bool = False,
    native_goal_host: bool = False,
) -> dict[str, Any]:
    mode = (
        "visible_goal"
        if native_goal_host
        else heartbeat_prompt_mode(full=full, compact=compact, brief=brief, thin=thin)
    )
    budget_text = prompt_budget_text(
        task_body, goal_id=goal_id, active_state=active_state, registry_path=registry_path,
    )
    budget_chars = len(budget_text)
    reward_memory_headroom = (
        REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS
        if mode != "visible_goal" and _REWARD_MEMORY_PROMPT_MARKER in task_body
        else 0
    )
    max_chars = INTERFACE_BUDGET_CHARS[mode] + reward_memory_headroom
    return {
        "mode": mode,
        "char_count": len(task_body),
        "line_count": len(task_body.splitlines()),
        "budget_char_count": budget_chars,
        "max_chars": max_chars,
        "reward_memory_headroom_chars": reward_memory_headroom,
        "within_budget": budget_chars <= max_chars,
    }
