"""Loopback setup companion for native, owner-only external project Chat."""
from __future__ import annotations
from collections.abc import Mapping
import logging
from pathlib import Path
from typing import Any

PRIVATE_CONVERSATIONS_PATH = "/api/chat/lark/private-conversations"


class PrivateConversationRequestMixin:
    server: Any

    def _private_listener_status(self, transport_ref: str, lark_health: Mapping[str, Any]) -> str:
        """Project observations from the exact provider, never infer liveness."""
        composed = getattr(self.server, "conversation_transports", None)
        provider = composed.transports.get(transport_ref) if composed is not None else None
        if provider is not None:
            snapshot = getattr(provider, "health_snapshot", None)
            try:
                health = snapshot() if callable(snapshot) else None
            except Exception as exc:
                # Provider exceptions may include credentials. A failed read
                # must not borrow another listener's possibly stale status.
                logging.getLogger(__name__).warning("Conversation listener health unavailable: %s", type(exc).__name__)
                health = None
        else:
            health = lark_health.get(transport_ref)
        status = health.get("status") if isinstance(health, Mapping) else None
        # Reuse the existing listener presentation vocabulary; project no
        # other provider fields. The UI already labels unknown as unconfirmed.
        return status if isinstance(status, str) and status in ("starting", "listening", "retrying", "stopped", "standby", "inactive") else "unknown"

    def _private_conversations(self) -> None:
        bindings = self.server.runtime_controller.project_contexts.conversation_bindings
        current = bindings.read()
        titles = {row["project_ref"]: Path(row["workspace_path"]).name
                  for row in self.server.runtime_controller.project_contexts.available()}
        health = self.server.lark_goal_topic_runtime.health_snapshot()
        deliveries = self.server.lark_private_conversations.health()
        self._send_json({"ok": True, "revision": current["revision"], "connections": [
            {"binding_id": row["binding_id"], "app_ref": row["transport_ref"], "context_kind": row["context_kind"],
             "project_ref": row["project_ref"], "context_available": row["project_ref"] in titles, "project_title": titles.get(row["project_ref"], "Unavailable workspace"),
             "executor_endpoint_id": row["executor_endpoint_id"], "grant": row["grant"],
             "audience": row.get("audience", "owner"), "group_count": len(row.get("group_refs", [])),
             "goal_count": len(bindings.goal_scope_ids(row)),
             "goal_scope": row.get("goal_scope", "selected") if row["context_kind"] == "steward" else None,
             "agent_candidates": [{key: item.get(key) for key in ["session_id", "goal_id", "agent_id", "executor_endpoint_id"]}
                for item in bindings.agent_candidates(row["binding_id"])],
             "agent_targets": row.get("agent_targets", []),
             "listener_status": self._private_listener_status(row["transport_ref"], health),
             **deliveries.get(row["binding_id"], {"pending_count": 0, "recovery_count": 0})}
            for row in current["bindings"]]})

    def _private_conversation_connect(self) -> None:
        from .goal_topic_runtime import _active_profile_configs
        from ...chat_lark_api import build_lark_goal_topic_runtime_snapshot
        try:
            body = self._read_json()
            if set(body) - {"context_kind", "project_grant", "goal_scope", "audience", "group_ids"} != {"app_ref", "project_ref", "executor_endpoint_id"}:
                raise ValueError("select an App, authorized workspace and executor")
            profile = str(body["app_ref"])
            existing = _active_profile_configs(build_lark_goal_topic_runtime_snapshot(
                registry_path=self.server.registry_path, runtime_root_override=self.server.runtime_root_override))
            from .conversation_identity import identity_ref
            observed = self.server.runtime_controller.project_contexts.conversation_bindings.observe(
                profile, audience=body.get("audience") or "owner")
            group_refs, available_group_refs = None, None
            if body.get("audience") == "group":
                from .goal_topic_connections import list_lark_group_chats, LarkGroupChatLookupError
                requested = body.get("group_ids")
                if not isinstance(requested, list) or not requested or len(requested) > 16 or any(not isinstance(item, str) for item in requested):
                    raise ValueError("select one to sixteen groups visible to this App")
                try:
                    chats = list_lark_group_chats(app_ref=profile, runner=self._lark_runner(), cli_bin=self._require_lark_cli())
                except LarkGroupChatLookupError as exc:
                    raise ValueError("the App's group membership could not be verified") from exc
                available_group_refs = [identity_ref(observed["provider_ref"], item["chat_id"]) for item in chats]
                group_refs = [identity_ref(observed["provider_ref"], chat) for chat in requested]
            elif "group_ids" in body or "audience" in body:
                raise ValueError("unsupported conversation audience")
            from ...chat_lark_api import _app_identity_for_private_guard
            group_apps = [str(config.get("bot_app_id") or _app_identity_for_private_guard(
                other_profile, self._lark_runner(), self._require_lark_cli()))
                for other_profile, config in existing.items()]
            if profile in existing or any(identity_ref(app) == observed["provider_ref"] for app in group_apps):
                raise ValueError("this App already owns a group listener; disconnect it first")
            endpoint = str(body["executor_endpoint_id"])
            if not any(row["agent_id"] == endpoint and row["available"] for row in self.server.runtime_controller.capabilities()):
                raise ValueError("the selected executor is unavailable")
            self.server.runtime_controller.project_contexts.conversation_bindings.configure(
                transport_ref=profile, project_ref=str(body["project_ref"]), executor_endpoint_id=endpoint,
                context_kind=str(body.get("context_kind", "project")),
                project_grant=str(body["project_grant"]) if "project_grant" in body else None,
                goal_scope=str(body["goal_scope"]) if "goal_scope" in body else None,
                audience=body.get("audience"), group_refs=group_refs, available_group_refs=available_group_refs)
            self.server.lark_goal_topic_runtime.refresh()
        except (ValueError, OSError, KeyError) as exc:
            self._send_error(str(exc), status=400)
            return
        self._private_conversations()

    def _private_conversation_disconnect(self) -> None:
        try:
            body = self._read_json()
            if set(body) != {"binding_id", "revision"}:
                raise ValueError("disconnect the exact current binding revision")
            self.server.runtime_controller.project_contexts.conversation_bindings.disconnect(
                str(body["binding_id"]), expected_revision=body["revision"])
            self.server.lark_goal_topic_runtime.refresh()
        except (ValueError, OSError, KeyError) as exc:
            self._send_error(str(exc), status=400)
            return
        self._private_conversations()

    def _private_conversation_agent_target(self) -> None:
        try:
            body = self._read_json()
            if set(body) not in [{"binding_id", "revision", "session_id"}, {"binding_id", "revision", "target_ref"}]:
                raise ValueError("grant an exact existing Session or revoke an exact target revision")
            bindings = self.server.runtime_controller.project_contexts.conversation_bindings
            bindings.change_agent_target(binding_id=str(body["binding_id"]), expected_revision=body["revision"],
                session_id=body.get("session_id"), target_ref=body.get("target_ref"))
        except (ValueError, OSError, KeyError, StopIteration) as exc:
            self._send_error(str(exc), status=400)
            return
        self._private_conversations()
