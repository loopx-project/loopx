"""Adapt an existing authorized channel to the steward's read-only synthesis."""
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...capabilities.manager_context.goal_notice import (
    NoticeSynthesizer, synthesize_goal_notice, validated_notice, current_notice_revision,
)
from .goal_channel_contracts import binding_for_goal, read_goal_channel_binding
from .goal_channel_targets import (
    default_goal_channel_target_path, read_goal_channel_targets, goal_channel_target_for_name,
)


def render_channel_notice(
    *, facts: dict[str, Any], synthesizer: NoticeSynthesizer | None,
    registry_path: Path | None, runtime_root: Path | None,
    binding_path: Path, goal_id: str, chat_id: str,
    provider_target: Mapping[str, Any] | None,
    cached_text: str | None = None,
) -> str:
    if synthesizer is not None:
        return validated_notice(cached_text or synthesizer(facts), facts)
    if registry_path is None or runtime_root is None:
        raise ValueError("steward notification requires its owning source runtime")

    def scope_valid() -> bool:
        payload = read_goal_channel_binding(binding_path)
        raw = binding_for_goal(payload, goal_id)
        target_ref = (raw or {}).get("target_ref")
        current_target = goal_channel_target_for_name(
            read_goal_channel_targets(default_goal_channel_target_path(runtime_root)), target_ref,
        ) if target_ref else None
        if target_ref and (not current_target or current_target.get("enabled") is not True):
            return False
        binding = binding_for_goal(read_goal_channel_binding(binding_path), goal_id,
                                   provider_target=current_target)
        return bool(binding and binding.get("enabled") is True
                    and (binding.get("channel") or {}).get("chat_id") == chat_id)

    if cached_text:
        current_notice_revision(registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, facts=facts, scope_valid=scope_valid)
        return validated_notice(cached_text, facts)
    return synthesize_goal_notice(registry_path=registry_path, runtime_root=runtime_root,
        goal_id=goal_id, audience=chat_id, facts=facts, scope_valid=scope_valid)
