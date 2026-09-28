"""Heartbeat agent identity helpers inside the heartbeat bounded context."""

from __future__ import annotations

import shlex
from typing import Any

from ..agents.profile import AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS
from ..agents.runtime_model import PEER_AGENT_PROFILE_SCHEMA_VERSION


def normalize_agent_scope(value: Any) -> str | None:
    candidate = " ".join(str(value or "").strip().split())
    if not candidate:
        return None
    if len(candidate) > AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS:
        raise ValueError(
            "agent scope must be at most "
            f"{AGENT_PROFILE_SCOPE_SUMMARY_MAX_CHARS} characters"
        )
    if any(char in candidate for char in "<>"):
        raise ValueError("agent scope must be compact text without angle brackets")
    return candidate


def normalize_agent_scopes(values: list[str] | tuple[str, ...] | None) -> list[str]:
    scopes: list[str] = []
    for value in values or []:
        scope = normalize_agent_scope(value)
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes


def agent_profile_scopes(profile: dict[str, Any] | None) -> list[str]:
    if not isinstance(profile, dict):
        return []
    raw_scopes: list[Any] = []
    for key in ("scope_summary", "default_scope", "scope"):
        value = profile.get(key)
        if isinstance(value, list):
            raw_scopes.extend(value)
        elif value:
            raw_scopes.append(value)
    for key in ("scope_summaries", "default_scopes", "scopes"):
        value = profile.get(key)
        if isinstance(value, list):
            raw_scopes.extend(value)
    return normalize_agent_scopes(raw_scopes)


def agent_profile_prompt_projection(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(profile, dict):
        return None
    public_keys = {
        "schema_version",
        "agent_id",
        "profile_role",
        "scope_summary",
        "default_scope",
        "scope",
        "scope_summaries",
        "default_scopes",
        "scopes",
        "default_task_classes",
        "vision_requirement",
        "preferred_action_kinds",
        "avoid_action_kinds",
    }
    projection = {key: value for key, value in profile.items() if key in public_keys}
    projection["schema_version"] = PEER_AGENT_PROFILE_SCHEMA_VERSION
    return projection or None


def agent_prompt_command_args(*, agent_id: str | None, agent_scopes: list[str]) -> str:
    parts: list[str] = []
    if agent_id:
        parts.extend(["--agent-id", agent_id])
    for scope in agent_scopes:
        parts.extend(["--agent-scope", scope])
    return "".join(f" {shlex.quote(part)}" for part in parts)


def build_peer_identity_required_error(
    *,
    goal_id: str,
    cli_bin: str,
    active_state_arg: str,
    full: bool,
    compact: bool,
    brief: bool,
    thin: bool,
    registered_agents: list[str],
) -> str:
    mode_arg = (
        " --thin"
        if thin
        else " --brief"
        if brief
        else " --compact"
        if compact
        else " --full"
        if full
        else ""
    )
    base = (
        f"{cli_bin} heartbeat-prompt{mode_arg} "
        f"--goal-id {shlex.quote(goal_id)}{active_state_arg}"
    )
    examples = "; ".join(
        f"`{base} --agent-id {shlex.quote(agent)} "
        "--agent-scope 'peer task claims and leases'`"
        for agent in registered_agents[:2]
    )
    return (
        "identity-aware peer heartbeat prompt required: "
        f"coordination.registered_agents is configured for goal_id={goal_id!r}, "
        "so automation prompts without --agent-id are not accepted. Regenerate each "
        f"installed automation with its registered identity. Examples: {examples}."
    )


def render_peer_agent_scope_instruction(
    *,
    goal_id: str,
    agent_id: str | None,
    agent_scopes: list[str],
    cli_bin: str,
    compact: bool = False,
    thin: bool = False,
) -> str:
    if not agent_id and not agent_scopes:
        return ""
    identity = agent_id or "<registered-agent-id>"
    scope_text = "; ".join(agent_scopes) if agent_scopes else "registered peer lane"
    scope_text = scope_text.rstrip(".!?")
    claim_command = (
        f"{cli_bin} todo claim --goal-id {goal_id} --todo-id <todo_id> "
        f"--claimed-by {agent_id} --agent-id {agent_id}"
        if agent_id
        else f"{cli_bin} todo claim --goal-id {goal_id} --todo-id <todo_id> "
        "--claimed-by <agent_id> --agent-id <agent_id>"
    )
    peer_rule = (
        "You are an equal peer agent: claim or lease in-scope work; use an independent worktree "
        "for repository writes; follow todo continuation policy. PR review is "
        "user_action with a runnable successor; gate only exact merge/release/launch "
        "authority. Task-scoped coordination grants no authority over other agents."
    )
    if thin:
        return (
            f"Equal peer `{identity}` (peer_v1); scope: {scope_text}. Claim/lease first; "
            "independent repo worktree; todo continuation; no cross-agent authority; "
            "no scope in todo metadata."
        )
    if compact:
        return (
            f"Agent identity and scope: agent_id `{identity}`; model: peer_v1; "
            f"scope: {scope_text}. {peer_rule} Claim: `{claim_command}`. "
            "Do not write scope into todo metadata."
        )
    return f"""Agent identity and scope:

- agent_id: `{identity}`
- agent_model: `peer_v1`
- scope: {scope_text}

{peer_rule}

Before delivery, claim an in-scope open todo:

```bash
{claim_command}
```

If a todo is claimed or leased by another peer, choose another in-scope item or
record no in-scope work internally. Only `NOTIFY` reports it; `DONT_NOTIFY` stays quiet.
Scope belongs in the heartbeat prompt, not todo metadata.
"""
