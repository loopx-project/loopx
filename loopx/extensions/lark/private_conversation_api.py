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
        composed = getattr(self.server, "conversation_transports", None)
        lark_profiles = set(health) - set(composed.transports if composed is not None else {})
        deliveries = self.server.lark_private_conversations.health()
        self._send_json({"ok": True, "revision": current["revision"],
            "app_steward_binding_id": current.get("app_steward_binding_id"), "connections": [
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
             **({"ingress": self.server.lark_private_conversations.ingress.health(row["binding_id"])}
                if row.get("audience", "owner") == "owner"
                and row["transport_ref"] in lark_profiles
                and hasattr(self.server.lark_private_conversations, "ingress") else {}),
             **({"owner_attention": self.server.lark_private_conversations.owner_attention.health(row["binding_id"])}
                if row["context_kind"] == "steward" and row.get("audience", "owner") == "owner" else {}),
             **({"managed_steward": self.server.lark_private_conversations.managed_steward.health(row["binding_id"])}
                if row["context_kind"] == "steward" and row.get("audience", "owner") == "owner"
                and row["executor_endpoint_id"] == "codex"
                and hasattr(self.server.lark_private_conversations, "managed_steward") else {}),
             **deliveries.get(row["binding_id"], {"pending_count": 0, "recovery_count": 0})}
            for row in current["bindings"]],
            "stop_recoveries": [{**row,
                "project_title": titles.get(row["project_ref"], "Unavailable workspace")}
                for row in bindings.stop_recoveries(current)]})

    def _app_steward(self) -> None:
        try:
            target = self.server.runtime_controller.project_contexts.conversation_bindings.app_steward_target()
        except (ValueError, KeyError, OSError, RuntimeError):
            self._send_error("The selected original steward is unavailable. Check its connection in Settings and retry.", status=409)
            return
        self._send_json({"ok": True, **target})

    def _select_app_steward(self) -> None:
        try:
            body = self._read_json()
            if (set(body) != {"binding_id", "revision"} or type(body["revision"]) is not int
                    or (body["binding_id"] is not None and not isinstance(body["binding_id"], str))):
                raise ValueError("select an existing owner steward at the current revision")
            self.server.runtime_controller.project_contexts.conversation_bindings.select_app_steward(
                body["binding_id"], expected_revision=body["revision"])
        except (ValueError, KeyError, OSError, RuntimeError):
            self._send_error("App steward selection could not be changed. Refresh the connection and retry.", status=409)
            return
        self._private_conversations()

    def _private_conversation_connect(self) -> None:
        from .goal_topic_runtime import _active_profile_configs
        from ...chat_lark_api import build_lark_goal_topic_runtime_snapshot
        try:
            body = self._read_json()
            if set(body) - {"context_kind", "project_grant", "goal_scope", "audience", "group_ids"} != {"app_ref", "project_ref", "executor_endpoint_id"}:
                raise ValueError("select an App, authorized workspace and executor")
            profile = requested_profile = str(body["app_ref"])
            snapshot = build_lark_goal_topic_runtime_snapshot(
                registry_path=self.server.registry_path, runtime_root_override=self.server.runtime_root_override)
            existing = _active_profile_configs(snapshot)
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
            group_apps = {other_profile: _app_identity_for_private_guard(
                other_profile, self._lark_runner(), self._require_lark_cli())
                for other_profile in existing}
            shared_group = existing.get(profile)
            if shared_group is not None:
                if body.get("audience", "owner") != "owner" or identity_ref(
                    str(shared_group.get("bot_app_id") or _app_identity_for_private_guard(
                        profile, self._lark_runner(), self._require_lark_cli()))
                ) != observed["provider_ref"]:
                    raise ValueError("shared listener requires the same verified App and a private owner audience")
            else:
                shared_profiles = [name for name, app in group_apps.items()
                                   if identity_ref(app) == observed["provider_ref"]]
                if shared_profiles:
                    if len(shared_profiles) != 1 or body.get("audience", "owner") != "owner":
                        raise ValueError("select one existing listener for the verified App and a private owner audience")
                    # Settings lists profile names; older group connections may
                    # retain an App-ID selector. Reuse that consumer identity.
                    profile = shared_profiles[0]
            # Compose the same transport configuration used by refresh before
            # publishing a binding. Failure must leave the Core grant untouched.
            _active_profile_configs({**snapshot, "private_profiles": {
                profile: {**observed, "audience": body.get("audience", "owner")}}})
            endpoint = str(body["executor_endpoint_id"])
            if not any(row["agent_id"] == endpoint and row["available"] for row in self.server.runtime_controller.capabilities()):
                raise ValueError("the selected executor is unavailable")
            if profile != requested_profile:
                current = self.server.runtime_controller.project_contexts.conversation_bindings.observe(requested_profile)
                if any(current[key] != observed[key] for key in ("provider_ref", "operator_ref")):
                    raise ValueError("the selected App or owner changed before binding publication; verify it again")
            self.server.runtime_controller.project_contexts.conversation_bindings.configure(
                transport_ref=profile, project_ref=str(body["project_ref"]), executor_endpoint_id=endpoint,
                context_kind=str(body.get("context_kind", "project")),
                project_grant=str(body["project_grant"]) if "project_grant" in body else None,
                goal_scope=str(body["goal_scope"]) if "goal_scope" in body else None,
                audience=body.get("audience"), group_refs=group_refs, available_group_refs=available_group_refs,
                expected_provider_ref=observed["provider_ref"],
                expected_operator_ref=observed.get("operator_ref"))
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

    def _private_conversation_managed_steward(self) -> None:
        try:
            body = self._read_json()
            if (set(body) - {"binding_id", "session_id", "plan_id", "execute"}
                    or not isinstance(body.get("binding_id"), str)
                    or not isinstance(body.get("execute", False), bool)
                    or ("session_id" in body and not isinstance(body["session_id"], str))
                    or ("plan_id" in body and not isinstance(body["plan_id"], str))):
                raise ValueError("use the original steward's current preparation preview")
            result = self.server.lark_private_conversations.managed_steward.prepare(
                body["binding_id"], session_id=body.get("session_id"),
                expected_plan_id=body.get("plan_id"), execute=body.get("execute", False))
        except (ValueError, KeyError, OSError) as exc:
            self._send_error(str(exc), status=409, error_code="managed_steward_preparation_unavailable")
            return
        self._send_json(result)

    def _private_conversation_steward_monitor(self) -> None:
        from ...capabilities.native_chat.steward_monitor import StewardMonitor
        try:
            body = self._read_json()
            if (set(body) - {"binding_id", "operation", "cadence_minutes", "plan_id", "execute"}
                    or not isinstance(body.get("binding_id"), str)
                    or not isinstance(body.get("operation"), str)
                    or body.get("operation") not in {"enable", "pause"}
                    or type(body.get("cadence_minutes", 30)) is not int
                    or type(body.get("execute", False)) is not bool
                    or ("plan_id" in body and not isinstance(body["plan_id"], str))):
                raise ValueError("use the original steward monitor setting")
            result = StewardMonitor(self.server.runtime_controller).configure(body["binding_id"],
                operation=body["operation"], cadence_minutes=body.get("cadence_minutes", 30),
                expected_plan_id=body.get("plan_id"), execute=body.get("execute", False))
        except (ValueError, KeyError, OSError, RuntimeError):
            self._send_error("Native monitoring could not be changed. Refresh the original connection; existing work and receipts are retained.",
                status=409, error_code="managed_steward_monitor_unavailable")
            return
        self._send_json(result)

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

    def _private_conversation_steward_report(self) -> None:
        from ...capabilities.native_chat.steward_reports import PendingStewardReportError, StewardReports
        try:
            body = self._read_json()
            if (set(body) - {"binding_id", "operation", "schedule", "plan_id", "execute"}
                    or not isinstance(body.get("binding_id"), str)
                    or body.get("operation") not in {"enable", "pause", "cancel"}
                    or type(body.get("execute", False)) is not bool
                    or "plan_id" in body and not isinstance(body["plan_id"], str)
                    or "schedule" in body and (not isinstance(body["schedule"], dict) or body["operation"] != "enable")):
                raise ValueError("use the original steward report setting")
            result = StewardReports(self.server.runtime_controller).configure(body["binding_id"],
                operation=body["operation"], schedule=body.get("schedule"),
                expected_plan_id=body.get("plan_id"), execute=body.get("execute", False))
        except PendingStewardReportError:
            self._send_error("本期汇报已冻结，投递尚未核验；请先完成本期投递，再修改计划。可暂停、取消或恢复原计划，原回执继续保留。The frozen report still awaits verified delivery; finish it before changing its schedule.",
                status=409, error_code="managed_steward_report_pending")
            return
        except (ValueError, KeyError, OSError, RuntimeError, TypeError):
            self._send_error("The original report setting is unavailable. Refresh the original connection; existing windows, failed attempts and delivery receipts are retained.",
                status=409, error_code="managed_steward_report_unavailable")
            return
        self._send_json(result)

    def _private_conversation_owner_notifications(self) -> None:
        try:
            body = self._read_json()
            if set(body) != {"binding_id", "binding_revision", "revision", "enabled"}:
                raise ValueError("select the current private steward notification setting")
            self.server.lark_private_conversations.owner_attention.configure(**body)
        except (ValueError, OSError, KeyError, TypeError, RuntimeError):
            # Source/provider errors may contain private paths or credentials.
            self._send_error("Notification settings could not be changed. Refresh the connection and verify the original private Chat.", status=400)
            return
        self._private_conversations()
