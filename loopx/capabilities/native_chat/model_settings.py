"""Provider-neutral model values persisted by native Chat Sessions.

These are bounded visible provider values, not LoopX entity or file names.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from ...chat_runtime import ChatRuntimeController, ChatRuntimeAdapter


_AGENT_MODEL_ID_MAX_LENGTH = 160
MODEL_SELECTION_UNSET = object()


def native_adapter_configuration(controller: ChatRuntimeController, *, agent_id: str,
        goal_id: str | None, objective: str, manager_runtime: Mapping[str, Any] | None,
        executor_model: Mapping[str, Any] | None, agent_config: Mapping[str, str] | None,
        execution_mode: bool) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    """Share native-provider selection; the existing manager owner decides policy."""
    from ...chat_manager import MANAGER_AGENT_GOAL_ID, manager_agent_objective, manager_model_config
    profile = (dict(manager_runtime or controller.manager_runtime_profile())
               if goal_id == MANAGER_AGENT_GOAL_ID else None)
    if profile is not None:
        objective = manager_agent_objective(str(profile["runtime_profile"]))
    model = (executor_model or manager_model_config(
        endpoint=agent_id, machine_defaults=controller.steward_executor_defaults())
        if goal_id == MANAGER_AGENT_GOAL_ID and not execution_mode else executor_model or {})
    return profile, objective, {**model, **{
        key: agent_config[key] for key in ("model", "reasoning_effort", "service_tier")
        if agent_config and agent_config.get(key)}}


def session_model_settings_supported(session: dict[str, Any]) -> bool:
    """Project the same current-Session boundary used by the save operation.

    Host-owned, native Goal and legacy migration Sessions keep their existing
    configuration owner. A model choice must never trigger their thread replay.
    Imports are local because the Session store also uses these normalizers.
    """
    from ...chat.coordination import PROJECT_CONTEXT_VERSION
    from ...chat_manager import is_manager_channel
    from ...chat_store import CHAT_SESSION_MODE_MANAGED

    return bool(
        session.get("agent_id") == "codex"
        and session.get("session_mode") == CHAT_SESSION_MODE_MANAGED
        and session.get("upstream_mode") == "chat"
        and session.get("status") in {"ready", "busy", "resuming", "resume_failed"}
        and session.get("managed_agent_id") is None
        and not is_manager_channel(session.get("channel_id"))
        and session.get("steward_context") is None
        and (session.get("project_context") or {}).get("audience") not in {"bound_owner", "bound_group"}
        and (session.get("goal_id") is None
             or session.get("coordination_context_version") == PROJECT_CONTEXT_VERSION)
    )


def normalize_agent_model_id(value: Any) -> str:
    """Keep a provider model value without applying file-name rules.

    ACP option values are protocol strings rather than LoopX entity IDs. They
    are persisted in JSON only, so retain a bounded printable value while
    rejecting controls that would make logs or projections unsafe.
    """
    token = str(value or "")
    if not token.strip() or len(token) > _AGENT_MODEL_ID_MAX_LENGTH or any(ord(character) < 32 for character in token):
        raise ValueError("agent_model_id must be a bounded model value")
    return token


def normalize_agent_reasoning_effort(value: Any) -> str:
    """Preserve an exact provider option; admission still belongs to the host."""
    if not isinstance(value, str):
        raise ValueError("agent_reasoning_effort must be a bounded provider value")
    try:
        return normalize_agent_model_id(value)
    except ValueError as exc:
        raise ValueError("agent_reasoning_effort must be a bounded provider value") from exc


def normalize_session_model_settings(
    *, model: Any, effort: Any, tier: Any,
    requested_model: Any = MODEL_SELECTION_UNSET,
    requested_effort: Any = MODEL_SELECTION_UNSET,
) -> dict[str, Any]:
    """Normalize before persistence; absent requests must not become user pins."""
    fields = {"agent_model_id": normalize_agent_model_id(model) if model else None,
              "agent_reasoning_effort": normalize_agent_model_id(effort) if effort else None,
              "agent_service_tier": normalize_agent_model_id(tier) if tier else None}
    if requested_model is not MODEL_SELECTION_UNSET:
        fields["requested_agent_model_id"] = normalize_agent_model_id(requested_model) if requested_model else None
    if requested_effort is not MODEL_SELECTION_UNSET:
        fields["requested_agent_reasoning_effort"] = normalize_agent_reasoning_effort(requested_effort) if requested_effort is not None else None
    return fields


def inspect_agent_model_options(controller: ChatRuntimeController, agent_id: str, *, work_dir: Path, goal_id: str | None = None,
                                agent_model_id: str | None = None) -> dict[str, Any]:
    """Read model choices from one selected, available ACP endpoint.

    This is intentionally not a PATH scan: the controller launches only an
    endpoint already present in the built-in catalog or owner-local registry.
    The temporary upstream session is closed before returning.
    """
    from ...chat_agent import CodexChatAgentError, agent_endpoint_error
    from ... import chat_providers as providers
    from .machine_defaults import codex_profile_home, load_codex_chat_defaults
    capability = next((row for row in controller.capabilities() if row["agent_id"] == agent_id), None)
    if capability is None or not capability.get("available"):
        raise agent_endpoint_error(agent_id, reason=str((capability or {}).get("unavailable_reason") or ""))
    if agent_id == "codex":
        defaults = load_codex_chat_defaults(controller.store.root.parent) if goal_id else {}
        home = codex_profile_home(controller.codex_home, defaults)
        models = providers.codex_model_options(controller.codex_bin, codex_home=home)
        if models is None:
            raise CodexChatAgentError("The selected Codex account did not return model choices. Check Chat account settings, then retry; existing conversations are unchanged.",
                                      error_code="provider_unavailable", gate=None)
        return {"agent_id": agent_id, "model": models, "supported": True}
    if agent_id == "claude-code":
        return {
            "agent_id": agent_id,
            "supported": True,
            "model": {
                "id": "model", "label": "Model", "selected": "sonnet",
                "options": [
                    {"value": "sonnet", "label": "Sonnet"},
                    {"value": "opus", "label": "Opus"},
                    {"value": "fable", "label": "Fable"},
                ],
            },
        }
    if agent_id == "antigravity":
        from .antigravity_adapter import ANTIGRAVITY_DEFAULT_MODEL, ANTIGRAVITY_MODEL_OPTIONS
        return {
            "agent_id": agent_id,
            "supported": True,
            "model": {
                "id": "model",
                "label": "Model",
                "selected": ANTIGRAVITY_DEFAULT_MODEL,
                "options": [
                    {
                        "value": str(item["id"]),
                        "label": str(item["display_name"]),
                        "description": str(item.get("description") or ""),
                        "is_default": bool(item.get("is_default")),
                        "default_reasoning_effort": str(item.get("default_reasoning_effort") or "high"),
                        "reasoning_efforts": [
                            str(effort["reasoning_effort"])
                            for effort in (item.get("supported_reasoning_efforts") or [])
                            if isinstance(effort, dict) and effort.get("reasoning_effort")
                        ],
                    }
                    for item in ANTIGRAVITY_MODEL_OPTIONS
                ],
            },
        }
    if str(capability.get("adapter_kind")) != "acp":
        return {"agent_id": agent_id, "model": None, "supported": False}
    adapter = controller._start_adapter(
        agent_id=agent_id,
        work_dir=work_dir,
        goal_id=None,
        objective="",
        agent_config={"model": agent_model_id} if agent_model_id else None,
    )
    try:
        return {"agent_id": agent_id, "model": adapter.model_options(),
                "reasoning_effort": adapter.reasoning_options(), "supported": True}
    finally:
        adapter.close_session()


def configure_session_model(controller: ChatRuntimeController, session_id: str, *, model: str,
                            effort: str | None = None) -> dict[str, Any]:
    """Save choices for this ordinary Session's next Turn, never replace it.

    The active Turn keeps its adapter. Between Turns the existing resume
    owner confirms the requested settings on the same upstream thread.
    """
    from ...chat_agent import CodexChatAgentError
    from ... import chat_providers as providers
    from . import codex_context
    model = normalize_agent_model_id(model)
    if effort is not None:
        effort = normalize_agent_reasoning_effort(effort)
    with controller._session_adapter_lock(session_id):
        session = controller.store.load_session(session_id)
        if session is None or session.get("status") == "closed":
            raise KeyError("chat session was not found")
        if not session_model_settings_supported(session):
            raise CodexChatAgentError("Change this conversation's configuration in its owning host or settings.",
                                      error_code="unsupported_configuration", gate=None)
        if session.get("project_context") is not None:
            controller.project_contexts.session_context(session)
        home = codex_context.checked_session_home(controller.codex_home, session)
        catalog = providers.codex_model_options(controller.codex_bin, codex_home=home)
        if catalog is None:
            raise CodexChatAgentError("The selected Codex account did not return model choices; retry before changing settings.",
                                      error_code="provider_unavailable", gate=None)
        option = next((row for row in (catalog or {}).get("options", []) if row["value"] == model), None)
        if option is None:
            raise CodexChatAgentError("The selected account did not offer this model; the saved configuration is unchanged.",
                                      error_code="unsupported_configuration", gate=None)
        selected_effort = effort or session.get("requested_agent_reasoning_effort") or session.get("agent_reasoning_effort")
        if selected_effort and selected_effort not in option.get("reasoning_efforts", []):
            raise CodexChatAgentError("Choose a supported reasoning effort for this model; the saved configuration is unchanged.",
                                      error_code="unsupported_configuration", gate=None)
        if session.get("project_context") is not None:
            controller.project_contexts.session_context(session)
        return controller.store.update_session(session_id, expected_statuses={"ready", "busy", "resuming", "resume_failed"}, requested_agent_model_id=model,
                                         requested_agent_reasoning_effort=selected_effort)


def observed_model_settings(adapter: ChatRuntimeAdapter, requested_effort: str | None = None,
                             requested_model: str | None = None) -> dict[str, Any]:
    from ...chat_acp import ACPStdioAdapter
    from .antigravity_adapter import AntigravityAdapter
    from .codex_adapter import CodexAppServerAdapter
    from ...chat_agent import CodexChatAgentError
    settings = adapter.model_settings if isinstance(adapter, (ACPStdioAdapter, CodexAppServerAdapter, AntigravityAdapter)) else {}
    confirmed_model = requested_model
    if isinstance(adapter, AntigravityAdapter) and requested_model:
        from .antigravity_adapter import resolve_antigravity_model_placeholder
        confirmed_model, _ = resolve_antigravity_model_placeholder(requested_model, requested_effort)
    if confirmed_model and isinstance(adapter, (ACPStdioAdapter, CodexAppServerAdapter, AntigravityAdapter)) and adapter.current_model_id != confirmed_model:
        raise CodexChatAgentError("The Agent did not confirm the requested model; no message was sent.",
                                  error_code="unsupported_configuration", gate=None)
    if requested_effort and settings.get("agent_reasoning_effort") != requested_effort:
        raise CodexChatAgentError(
            "The Agent did not confirm the requested reasoning effort; no message was sent.",
            error_code="unsupported_configuration", gate=None,
        )
    return settings


def session_model_settings_pending(session: dict[str, Any]) -> bool:
    return bool(session_model_settings_supported(session) and any(
        session.get(requested) is not None and session[requested] != session.get(observed)
        for requested, observed in (("requested_agent_model_id", "agent_model_id"),
                                    ("requested_agent_reasoning_effort", "agent_reasoning_effort"))))


def normalize_session_model_changes(changes: dict[str, object]) -> None:
    """Normalize the existing mutable store fields through their shared grammar."""
    if "agent_model_id" in changes:
        changes["agent_model_id"] = normalize_agent_model_id(changes["agent_model_id"]) if changes["agent_model_id"] else None
    for field in ("requested_agent_model_id", "requested_agent_reasoning_effort"):
        if field in changes:
            changes[field] = normalize_agent_model_id(changes[field]) if changes[field] is not None else None
    for key in ("agent_reasoning_effort", "agent_service_tier"):
        if key in changes:
            changes[key] = normalize_agent_model_id(changes[key]) if changes[key] else None
