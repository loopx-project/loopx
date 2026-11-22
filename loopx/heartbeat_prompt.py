"""Public heartbeat prompt compatibility surface."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from .control_plane.heartbeat.agent import (
    agent_prompt_command_args,
    agent_profile_prompt_projection,
    agent_profile_scopes,
    build_peer_identity_required_error,
    normalize_agent_scope,
    normalize_agent_scopes,
    render_peer_agent_scope_instruction,
)
from .control_plane.heartbeat.agent_input import (
    HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION,
    project_heartbeat_agent_input,
)
from .control_plane.heartbeat.budget import (
    INTERFACE_BUDGET_CHARS,
    NATIVE_GOAL_HOST_MAX_CHARS,
    REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS,
    build_interface_budget,
    heartbeat_prompt_mode,
    prompt_budget_text,
)
from .control_plane.heartbeat.host import (
    uses_ark_managed_agent_goal_host,
    uses_native_goal_host_loop,
)
from .control_plane.heartbeat.rules import (
    CODEX_NATIVE_GOAL_UNCHANGED_WAIT_RULE,
    DEFAULT_MATERIAL_QUEUE_RULE,
    DEFAULT_PERMISSION_RULE,
    HEARTBEAT_NOTIFICATION_RULE_SHORT,
    HEARTBEAT_VISION_WRITEBACK_RULE_SHORT,
    RUNTIME_CAPABILITY_PROJECTION_THIN_RULE,
    RUNTIME_EXECUTION_ROUTING_RULE,
    SCHEDULER_HINT_APPLICATION_RULE,
    SCHEDULER_HINT_COMPACT_RULE,
    SCHEDULER_HINT_THIN_RULE,
    USER_TODO_FINAL_MESSAGE_RULE,
)
from .control_plane.heartbeat.visible_goal import (
    VISIBLE_GOAL_HEARTBEAT_ONLY_POLICY_PATTERNS,
    VISIBLE_GOAL_HOST_CONTROL_CAPABILITIES,
    VISIBLE_GOAL_INITIAL_RUNTIME_CAPABILITY_LIMIT,
    VISIBLE_GOAL_INITIAL_RUNTIME_CAPABILITY_PROJECTION_SCHEMA_VERSION,
    build_visible_goal_initial_runtime_capability_projection,
    validate_visible_goal_policy_rule,
)

__all__ = [
    "CODEX_NATIVE_GOAL_UNCHANGED_WAIT_RULE",
    "DEFAULT_MATERIAL_QUEUE_RULE",
    "DEFAULT_PERMISSION_RULE",
    "HEARTBEAT_NOTIFICATION_RULE_SHORT",
    "HEARTBEAT_AGENT_INPUT_SCHEMA_VERSION",
    "HEARTBEAT_VISION_WRITEBACK_RULE_SHORT",
    "INTERFACE_BUDGET_CHARS",
    "NATIVE_GOAL_HOST_MAX_CHARS",
    "REWARD_MEMORY_OUTCOME_PROMPT_HEADROOM_CHARS",
    "RUNTIME_CAPABILITY_PROJECTION_THIN_RULE",
    "RUNTIME_EXECUTION_ROUTING_RULE",
    "SCHEDULER_HINT_APPLICATION_RULE",
    "SCHEDULER_HINT_COMPACT_RULE",
    "SCHEDULER_HINT_THIN_RULE",
    "USER_TODO_FINAL_MESSAGE_RULE",
    "VISIBLE_GOAL_HEARTBEAT_ONLY_POLICY_PATTERNS",
    "VISIBLE_GOAL_HOST_CONTROL_CAPABILITIES",
    "VISIBLE_GOAL_INITIAL_RUNTIME_CAPABILITY_LIMIT",
    "VISIBLE_GOAL_INITIAL_RUNTIME_CAPABILITY_PROJECTION_SCHEMA_VERSION",
    "_render_goal_task_body",
    "agent_prompt_command_args",
    "agent_profile_prompt_projection",
    "agent_profile_scopes",
    "build_heartbeat_prompt",
    "build_heartbeat_prompt_error_payload",
    "build_interface_budget",
    "build_peer_identity_required_error",
    "build_visible_goal_initial_runtime_capability_projection",
    "heartbeat_prompt_mode",
    "normalize_agent_scope",
    "normalize_agent_scopes",
    "prompt_budget_text",
    "project_heartbeat_agent_input",
    "render_ark_managed_agent_goal_task_body",
    "render_brief_heartbeat_task_body",
    "render_compact_heartbeat_task_body",
    "render_heartbeat_generator_inputs_markdown",
    "render_heartbeat_prompt_error_markdown",
    "render_heartbeat_prompt_markdown",
    "render_heartbeat_task_body",
    "render_peer_agent_scope_instruction",
    "render_thin_heartbeat_task_body",
    "render_traex_visible_goal_task_body",
    "render_visible_goal_task_body",
    "uses_ark_managed_agent_goal_host",
    "uses_native_goal_host_loop",
    "validate_visible_goal_policy_rule",
]


def build_heartbeat_prompt(**kwargs: object) -> dict[str, object]:
    from .control_plane.heartbeat.builder import build_heartbeat_prompt as _impl
    return cast(Callable[..., dict[str, object]], _impl)(**kwargs)


def build_heartbeat_prompt_error_payload(**kwargs: object) -> dict[str, object]:
    from .control_plane.heartbeat.builder import build_heartbeat_prompt_error_payload as _impl
    return cast(Callable[..., dict[str, object]], _impl)(**kwargs)


def render_heartbeat_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import render_heartbeat_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def render_brief_heartbeat_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import render_brief_heartbeat_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def render_compact_heartbeat_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import render_compact_heartbeat_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def render_visible_goal_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import render_visible_goal_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def render_traex_visible_goal_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import render_traex_visible_goal_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def _render_goal_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import _render_goal_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def render_ark_managed_agent_goal_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import render_ark_managed_agent_goal_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def render_thin_heartbeat_task_body(**kwargs: object) -> str:
    from .control_plane.heartbeat.task_body import render_thin_heartbeat_task_body as _impl
    return cast(Callable[..., str], _impl)(**kwargs)


def render_heartbeat_generator_inputs_markdown(payload: Mapping[str, object]) -> str:
    from .control_plane.heartbeat.task_body import render_heartbeat_generator_inputs_markdown as _impl
    return _impl(dict(payload))


def render_heartbeat_prompt_error_markdown(payload: Mapping[str, object]) -> str:
    from .control_plane.heartbeat.task_body import render_heartbeat_prompt_error_markdown as _impl
    return _impl(dict(payload))


def render_heartbeat_prompt_markdown(payload: Mapping[str, object]) -> str:
    from .control_plane.heartbeat.task_body import render_heartbeat_prompt_markdown as _impl
    return _impl(dict(payload))
