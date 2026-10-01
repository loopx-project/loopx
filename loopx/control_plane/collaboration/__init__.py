"""Provider-neutral collaboration contracts owned by the typed control plane."""

from __future__ import annotations

from typing import Any, cast

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


def conversation_trigger(mode: str | None = None, **evidence: bool) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], effect_runtime_result("collaboration.conversation.trigger", {
            "mode": mode, **evidence,
        }))
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc


def conversation_scope(session: dict[str, Any], *, origin: str | None = None) -> dict[str, Any]:
    return effect_runtime_result("collaboration.conversation.scope", {
        "channel_id": session.get("channel_id"), "goal_id": session.get("goal_id"),
        **({"origin": origin} if origin is not None else {}),
    })


def conversation_reply_context(route: dict[str, Any]) -> dict[str, Any]:
    return effect_runtime_result(
        "collaboration.conversation.reply_context",
        {
            "message_id": route.get("message_id"),
            "parent_id": route.get("parent_id"),
            "conversation_id": route.get("source_conversation_id"),
            "reply_context": route.get("reply_context"),
        },
    )
