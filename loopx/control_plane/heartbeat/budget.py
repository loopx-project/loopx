"""Heartbeat prompt budget helpers inside the heartbeat bounded context."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from ...project_prompt import shell_arg_for_host


# Presentation regression budgets, not external transport or execution limits.
# Keep useful admission and authority instructions readable; do not abbreviate
# them solely to fit a historical character ceiling.
INTERFACE_BUDGET_CHARS = {
    # Timed-wait guidance replaces unconditional keep-open advice. With the
    # same 320-char scope, explicit registry and no reward memory, normalized
    # base -> head sizes are 12,731 -> 12,982; 7,601 -> 7,861;
    # 5,355 -> 5,615; 4,057 -> 4,378. Preserve evidence, independent work,
    # original settlement and fresh admission rather than abbreviating them.
    "full": 13_100,
    "compact": 8_000,
    # The same generic CLI + 320-char scope with explicit runtime also needs
    # 5,715 chars; retain those routes instead of shortening the instructions.
    "brief": 5_800,
    # Full runtime capability flags plus an explicit CLI route and 320-char
    # scope emit 4,851 normalized chars with unchanged guidance. Keep that
    # supported combination; this is not a transport or execution limit.
    "thin": 4_900,
    "visible_goal": 4_000,
}
NATIVE_GOAL_HOST_MAX_CHARS = INTERFACE_BUDGET_CHARS["visible_goal"]

# Opt-in captured envelopes retain transport routes and same-capture recovery.
# Keep captured-envelope transport headroom separate from ordinary thin.
CAPTURED_ENVELOPE_PROMPT_HEADROOM_CHARS = 400

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
    runtime_root: str | Path | None = None,
) -> str:
    normalized = text
    # Compare authored guidance independently of the host's selected path length.
    # char_count below still reports the actual transport/body size.
    routes = (
        (registry_path, "<REGISTRY_PATH>"), (active_state, "<ACTIVE_STATE>"),
        (str(runtime_root) if runtime_root is not None else None, "<RUNTIME_ROOT>"),
    )
    # A route may contain the goal id or live below another selected route.
    # Normalize complete, shell-quoted paths before their substrings. Bootstrap
    # loaders use POSIX quoting even when host commands use PowerShell quoting.
    for route, marker in sorted(routes, key=lambda pair: len(pair[0] or ""), reverse=True):
        if route:
            for spelling in dict.fromkeys((shell_arg_for_host(route), shlex.quote(route), route)):
                normalized = normalized.replace(spelling, marker)
    return normalized.replace(goal_id, "<GOAL_ID>")


def build_interface_budget(
    *,
    task_body: str,
    goal_id: str,
    active_state: str,
    registry_path: str | None = None,
    runtime_root: str | Path | None = None,
    full: bool = False,
    compact: bool = False,
    brief: bool = False,
    thin: bool = False,
    native_goal_host: bool = False,
    captured_envelope: bool = False,
) -> dict[str, Any]:
    mode = (
        "visible_goal"
        if native_goal_host
        else heartbeat_prompt_mode(full=full, compact=compact, brief=brief, thin=thin)
    )
    budget_text = prompt_budget_text(
        task_body, goal_id=goal_id, active_state=active_state,
        registry_path=registry_path, runtime_root=runtime_root,
    )
    budget_chars = len(budget_text)
    reward_memory_headroom = (
        REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS
        if mode != "visible_goal" and _REWARD_MEMORY_PROMPT_MARKER in task_body
        else 0
    )
    captured_envelope_headroom = (
        CAPTURED_ENVELOPE_PROMPT_HEADROOM_CHARS
        if mode == "thin" and captured_envelope
        else 0
    )
    max_chars = (
        INTERFACE_BUDGET_CHARS[mode]
        + reward_memory_headroom
        + captured_envelope_headroom
    )
    return {
        "mode": mode,
        "char_count": len(task_body),
        "line_count": len(task_body.splitlines()),
        "budget_char_count": budget_chars,
        "max_chars": max_chars,
        "reward_memory_headroom_chars": reward_memory_headroom,
        "within_budget": budget_chars <= max_chars,
    }
