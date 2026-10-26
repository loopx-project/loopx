"""Private IO for Core-owned conversation context and audience grants.

Provider observations contain opaque App/owner references, not credentials.
The typed owner validates configuration, replacement, revocation, and use.
This file stores no conversation text and creates no Goal or model thread.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid
import hashlib

from ...chat_store import TERMINAL_TURN_STATES, _atomic_write_json, _read_json
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ...file_lock import exclusive_file_lock

if TYPE_CHECKING:
    from ...chat_store import ChatSessionStore


class ChatConversationBindings:
    def __init__(self, *, root: Path, project_contexts: Any,
                 observe: Callable[[str], dict[str, Any]],
                 observe_group: Callable[[str], dict[str, Any]] | None = None) -> None:
        self.path = root / "conversation-bindings.json"
        self.projects = project_contexts
        self._observe_owner = observe
        self._observe_group = observe_group
        self.controller: Any | None = None

    def observe(self, transport_ref: str, *, audience: str | None = None) -> dict[str, Any]:
        row: dict[str, Any] = next((item for item in self.read()["bindings"] if item["transport_ref"] == transport_ref), {})
        if audience is None:
            audience = row.get("audience", "owner")
        if audience == "group" and self._observe_group is not None:
            observed = self._observe_group(transport_ref)
            # Earlier group grants used the verified user's principal. Keep
            # that exact identity/home until an explicit disconnect/reconnect;
            # changing it during a poll would invalidate resumed Sessions.
            if (row.get("audience") == "group" and row.get("provider_ref") == observed["provider_ref"]
                    and row.get("operator_ref") != observed["operator_ref"]):
                return self._observe_owner(transport_ref)
            return observed
        return self._observe_owner(transport_ref)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "loopx_chat_conversation_bindings_v0", "revision": 0, "bindings": []}
        # An unreadable existing grant is not an empty configuration to overwrite.
        return _read_json(self.path)

    @staticmethod
    def _core(operation: str, params: dict[str, Any], *, large_local_snapshot: bool = False) -> dict[str, Any]:
        try:
            result = effect_runtime_result(operation, params, large_local_snapshot=large_local_snapshot)
        except EffectRuntimeRejected as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(result, dict):
            raise ValueError("conversation authority returned an invalid result")
        return result

    def configure(self, *, transport_ref: str, project_ref: str,
                  executor_endpoint_id: str, context_kind: str = "project",
                  project_grant: str | None = None, goal_scope: str | None = None,
                  audience: str | None = None, group_refs: list[str] | None = None,
                  available_group_refs: list[str] | None = None,
                  expected_provider_ref: str | None = None,
                  expected_operator_ref: str | None = None) -> dict[str, Any]:
        if goal_scope is not None and (context_kind != "steward" or goal_scope not in {"selected", "all_registered"}):
            raise ValueError("select a supported steward Goal scope")
        if project_grant is None:
            project_grant = self.projects.workspace_grant if context_kind == "project" and executor_endpoint_id == "codex" else "workspace_read"
        if project_grant not in {"workspace_read", "workspace_write"} or (context_kind != "project" and project_grant != "workspace_read"):
            raise ValueError("workspace write authorization is only available for project Chat")
        observation = self.observe(transport_ref, audience=audience or "owner")
        if expected_provider_ref is not None and observation["provider_ref"] != expected_provider_ref:
            raise ValueError("the selected App changed before binding publication; verify it again")
        if expected_operator_ref is not None and observation["operator_ref"] != expected_operator_ref:
            raise ValueError("the selected owner changed before binding publication; verify it again")
        candidate = {
            "schema_version": "loopx_chat_conversation_binding_v0",
            "binding_id": uuid.uuid4().hex[:24], "transport_ref": transport_ref,
            "provider_ref": observation["provider_ref"], "operator_ref": observation["operator_ref"],
            "context_kind": context_kind, "project_ref": project_ref,
            "executor_endpoint_id": executor_endpoint_id,
            "grant": project_grant if context_kind == "project" else "portfolio_read", "enabled": True,
            **({"goal_ids": [], "goal_scope": goal_scope or "all_registered"} if context_kind == "steward" else {}),
            **({"audience": audience} if audience is not None else {}),
            **({"group_refs": group_refs} if group_refs is not None else {}),
        }
        with exclusive_file_lock(self.path, operation="configure_chat_conversation_binding"):
            current = self.read()
            previous = next((row for row in current["bindings"] if row["transport_ref"] == transport_ref), None)
            if previous and all(previous.get(key) == candidate.get(key) for key in ["context_kind", "project_ref", "executor_endpoint_id", "provider_ref", "operator_ref", "grant", "audience", "group_refs"]):
                if previous.get("agent_targets"):
                    candidate["agent_targets"] = previous["agent_targets"]
            if (context_kind == "steward" and previous and all(previous.get(key) == candidate.get(key)
                    for key in ["context_kind", "project_ref", "executor_endpoint_id", "provider_ref", "operator_ref"])):
                candidate["goal_ids"] = previous["goal_ids"]
                # Existing installations retain their exact audience and scope
                # until the local operator explicitly changes it.
                if goal_scope is None:
                    candidate.pop("goal_scope", None)
                    if "goal_scope" in previous:
                        candidate["goal_scope"] = previous["goal_scope"]
                candidate["binding_id"] = previous["binding_id"]
                if previous.get("managed_actor") is not None:
                    candidate["managed_actor"] = previous["managed_actor"]
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current.get("revision"), "operation": "configure",
                "binding": candidate, "observation": observation, "available_projects": self.projects.available(),
                **({"available_group_refs": available_group_refs} if audience == "group" else {}),
            })
            proposed = next(row for row in result["state"]["bindings"] if row["transport_ref"] == transport_ref)
            channels = self.delivery_channels(proposed) if goal_scope is not None else []
            for channel in channels:
                self._ensure_delivery_scope({"binding": proposed, "channel_id": channel}, execute=False)
            # Publish the advertised scope only after every known source policy
            # has applied and verified. On IO failure the old binding remains;
            # retrying the explicit scope operation converges any earlier source
            # writes without making ordinary admission undo a manual restriction.
            for channel in channels:
                self._ensure_delivery_scope({"binding": proposed, "channel_id": channel}, if_absent=False)
            if previous and previous["binding_id"] != proposed["binding_id"]:
                self._revoke_delivery_sources(previous["binding_id"])
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            readback = self.read()
            if readback != result["state"]:
                raise OSError("conversation binding publication did not verify")
            binding: dict[str, Any] = next(row for row in readback["bindings"] if row["transport_ref"] == transport_ref)
        if previous and previous["binding_id"] != binding["binding_id"]:
            self._stop_revoked_binding(previous["binding_id"])
        return binding

    def disconnect(self, binding_id: str, *, expected_revision: int) -> dict[str, Any]:
        with exclusive_file_lock(self.path, operation="disconnect_chat_conversation_binding"):
            result = self._core("collaboration.conversation.binding", {
                "current": self.read(), "expected_revision": expected_revision,
                "operation": "disconnect", "binding_id": binding_id,
            })
            self._revoke_delivery_sources(binding_id)
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            if self.read() != result["state"]:
                raise OSError("conversation binding revocation did not verify")
        self._stop_revoked_binding(binding_id)
        return dict(result["state"])

    def select_app_steward(self, binding_id: str | None, *, expected_revision: int) -> dict[str, Any]:
        """Select one existing source for the App entry without changing grants."""
        row = next((item for item in self.read()["bindings"] if item["binding_id"] == binding_id), None)
        observation = self.observe(row["transport_ref"]) if row is not None else None
        with exclusive_file_lock(self.path, operation="select_app_steward"):
            result = self._core("collaboration.conversation.binding", {
                "current": self.read(), "expected_revision": expected_revision,
                "operation": "select_app_steward", "binding_id": binding_id,
                "observation": observation, "available_projects": self.projects.available() if binding_id is not None else [],
            })
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            if self.read() != result["state"]:
                raise OSError("App steward selection did not verify")
            return dict(result["state"])

    def app_steward_target(self) -> dict[str, Any]:
        """Discover within the selected source; opening creates no execution."""
        current = self.read()
        selected = current.get("app_steward_binding_id")
        if selected is None:
            # Validate legacy state through the same pure owner plan. An
            # unreadable grant must not masquerade as an unselected entry.
            self._core("collaboration.conversation.binding", {"current": current,
                "expected_revision": current.get("revision"), "operation": "select_app_steward", "binding_id": None})
            return {"binding_id": None, "session_id": None}
        sessions = self.local_sessions(selected, app_steward_entry=True)
        if not sessions:
            raise ValueError("the original steward Session is unavailable")
        session = max(sessions, key=lambda item: (item.get("last_activity_at") or "", item["session_id"]))
        with exclusive_file_lock(self.path, operation="read_app_steward_target"):
            if self.read().get("app_steward_binding_id") != selected:
                raise ValueError("App steward selection changed during discovery")
            return {"binding_id": selected, "session_id": session["session_id"]}

    def _revoke_delivery_sources(self, binding_id: str) -> None:
        if self.controller is not None:
            from ..manager_context import revoke_conversation_sources
            revoke_conversation_sources(self.controller.coordination_runtime_root, binding_id)

    def _stop_revoked_binding(self, binding_id: str) -> None:
        """Apply the committed grant decision through existing exact Turn control.

        Never wait for a worker while holding the grant publication fence. Drop
        its queued work before stopping the active Turn; other audiences and
        local Turns in a shared attached Session retain their own authority.
        Retrying disconnect with the current revision retries an unconfirmed stop.
        """
        if self.controller is None:
            return
        store = self.controller.store
        failures = []
        for row in store.list_sessions():
            session = store.load_session(row["session_id"])
            if session is None:
                continue
            bound = any(isinstance(session.get(key), dict)
                and session[key].get("binding_id") == binding_id
                for key in ("project_context", "steward_context"))
            turns = store.queued_turns(session["session_id"])
            active = store.load_turn(session["session_id"], session["active_turn_id"]) if session.get("active_turn_id") else None
            if active:
                turns.append(active)
            for turn in turns:
                target = turn.get("external_agent_target") or {}
                if (not bound and (target.get("context") or {}).get("binding_id") != binding_id
                        or turn.get("status") in TERMINAL_TURN_STATES):
                    continue
                try:
                    stopped = self.controller.interrupt_turn(session_id=session["session_id"], turn_id=turn["turn_id"])
                    if stopped.get("status") not in TERMINAL_TURN_STATES:
                        raise RuntimeError("Turn stop did not settle")
                except Exception as exc:
                    # Revocation remains published even when a host lacks stop
                    # control. Continue stopping other exact targets, then report
                    # failure without exposing private transport diagnostics.
                    failures.append(exc)
        if failures:
            raise OSError("Conversation grant revoked; execution stop is not confirmed. Retry stop for this binding.") from failures[0]

    def resolve(self, *, binding_id: str, source_ref: str, sender_ref: str,
                private_human_message: bool, session_context: dict[str, Any] | None = None,
                group_ref: str | None = None, topic_ref: str | None = None,
                group_human_message: bool = False, include_observation: bool = False,
                origin: str | None = None, session: dict[str, Any] | None = None) -> dict[str, Any]:
        current = self.read()
        row = next((item for item in current.get("bindings", []) if item.get("binding_id") == binding_id), None)
        if row is None:
            raise ValueError("conversation binding is no longer authorized")
        observation = dict(self.observe(row["transport_ref"]))
        # Provider verification can wait on IO. It cannot authorize a binding
        # that was revoked or replaced while that observation was in flight.
        # Unrelated binding changes do not invalidate this exact source.
        current = self.read()
        if next((item for item in current.get("bindings", [])
                 if item.get("binding_id") == binding_id), None) != row:
            raise ValueError("conversation binding changed during identity verification")
        selected = self._resolve_observed(current, row, observation, {
            "source_ref": source_ref, "sender_ref": sender_ref, "private_human_message": private_human_message,
            **({"origin": origin} if origin is not None else {}),
            **({"session_context": session_context} if session_context is not None else {}),
            **({"session": self._session_observation(session)} if session is not None else {}),
            **({"group_ref": group_ref, "topic_ref": topic_ref,
                "group_human_message": group_human_message} if group_ref is not None or topic_ref is not None or group_human_message else {}),
        })
        # Presentation may need the name from this exact verified read. Keep it
        # outside the persisted context and opt-in; it is never a cached grant
        # or a substitute for the next admission/delivery owner's fresh check.
        return {**selected, "observation": observation} if include_observation else selected

    def _resolve_observed(self, current: dict[str, Any], row: dict[str, Any],
                          observation: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        return self._core("collaboration.conversation.bound_context", {
            "current": current, "binding_id": row["binding_id"], "observation": observation,
            "available_projects": self.projects.available(), **source,
            **({"available_goal_ids": self.goal_scope_ids(row)} if row.get("goal_scope") == "all_registered" else {}),
        })

    @contextmanager
    def input_context(self, saved: dict[str, Any], *, origin: str, session: dict[str, Any],
                      store: ChatSessionStore) -> Iterator[dict[str, Any]]:
        """Observe outside the publication fence, then retain it through enqueue.

        Saved fields select an identity; fresh Core checks grant it. Local Web
        continuation keeps its actual origin and never becomes a provider event.
        """
        if origin == "web":
            source = {"source_ref": saved["source_ref"], "sender_ref": saved["operator_ref"],
                      "private_human_message": False, "origin": "web", "session_context": saved}
        elif origin in {"lark", "external"}:
            source = self._session_source(saved)
        else:
            raise ValueError("unsupported bound conversation input origin")
        observed = self.resolve(binding_id=saved["binding_id"], include_observation=True, session=session, **source)
        with exclusive_file_lock(self.path, operation="admit_bound_input"):
            current = self.read()
            if next((row for row in current.get("bindings", [])
                     if row.get("binding_id") == saved["binding_id"]), None) != observed["binding"]:
                raise ValueError("conversation binding changed before input admission")
            # The provider observation may have waited. Re-read canonical
            # identity under the binding fence before Core authorizes input.
            fresh = store.load_session(session["session_id"])
            if fresh is None:
                raise KeyError("chat session was not found")
            yield self._resolve_observed(current, observed["binding"], observed["observation"],
                {**source, "session": self._session_observation(fresh)})

    @staticmethod
    def _session_observation(session: dict[str, Any]) -> dict[str, Any]:
        return {key: session.get(key) for key in (
            "session_id", "goal_id", "agent_id", "executor_endpoint_id", "channel_id",
            "session_mode", "status", "project_context", "steward_context")}

    def local_sessions(self, binding_id: str, *, app_steward_entry: bool = False) -> list[dict[str, Any]]:
        return self.local_session_snapshot(binding_id, app_steward_entry=app_steward_entry)["sessions"]

    def local_session_snapshot(self, binding_id: str, *, app_steward_entry: bool = False) -> dict[str, Any]:
        """Read original targets; metadata hints never replace Core's grant.

        Provider IO and store inventory stay outside the publication fence.
        Discovery neither restores a thread nor creates a Session or Turn.
        """
        if self.controller is None:
            raise ValueError("the bound conversation store is unavailable")
        row = next((item for item in self.read()["bindings"] if item["binding_id"] == binding_id), None)
        if row is None:
            raise ValueError("conversation binding is no longer authorized")
        observation = dict(self.observe(row["transport_ref"]))
        store = self.controller.store
        sessions: list[dict[str, Any]] = []
        for summary in store.list_sessions():
            session = store.load_session(summary["session_id"])
            # Narrow the IO payload only. Core independently validates every
            # matching context, audience and canonical Session identity.
            if session and any(isinstance(session.get(key), dict)
                    and session[key].get("binding_id") == binding_id
                    for key in ("project_context", "steward_context")):
                sessions.append(session)
        with exclusive_file_lock(self.path, operation="read_local_bound_sessions"):
            current = self.read()
            if next((item for item in current["bindings"] if item["binding_id"] == binding_id), None) != row:
                raise ValueError("conversation binding changed during identity verification")
            projects = self.projects.available()
            result = self._core("collaboration.conversation.local_sessions", {
                "current": current, "binding_id": binding_id, "observation": observation,
                "available_projects": projects,
                "sessions": [self._session_observation(session) for session in sessions],
                **({"app_steward_entry": True} if app_steward_entry else {}),
                **({"available_goal_ids": self.goal_scope_ids(row)} if row.get("goal_scope") == "all_registered" else {}),
            })
            # Build the lookup only after Core has rejected duplicate identity;
            # an IO dictionary must not silently turn ambiguity into one target.
            by_id = {session["session_id"]: session for session in sessions}
            project = next(project for project in projects if project["project_ref"] == row["project_ref"])
            # Project this exact Core-verified source under the same fence.
            # Opening a conversation must not enumerate settings, unrelated
            # Agent candidates, listener health or stop-recovery inventories.
            return {"sessions": [store.public_session(by_id[sid]) for sid in result["session_ids"]],
                    "conversation_source": {"binding_id": binding_id, "app_ref": row["transport_ref"],
                        "context_kind": row["context_kind"], "audience": row.get("audience", "owner"),
                        "project_title": Path(project["workspace_path"]).name, "context_available": True,
                        "executor_endpoint_id": row["executor_endpoint_id"]}}

    def goal_scope_ids(self, binding: dict[str, Any]) -> list[str]:
        if binding.get("goal_scope") != "all_registered":
            return list(binding.get("goal_ids", []))
        from ...control_plane.projects.registry_codec import load_registry
        if self.controller is None or self.controller.registry_path is None:
            raise ValueError("the steward registry is unavailable")
        return [goal["id"] for goal in load_registry(self.controller.registry_path).get("goals", [])]

    def delivery_channels(self, binding: dict[str, Any]) -> list[str]:
        if self.controller is None:
            return []
        from ..manager_context import POLICY_SCHEMA, _root
        path = _root(self.controller.coordination_runtime_root) / "policy.json"
        if not path.exists():
            return []
        policy = _read_json(path)
        if policy.get("schema_version") != POLICY_SCHEMA or not isinstance(policy.get("sources"), dict):
            raise ValueError("invalid manager policy")
        prefix = f"manager.external.native.{binding['binding_id']}."
        return [channel for channel, source in policy["sources"].items()
                if channel.startswith(prefix) and isinstance(source, dict)
                and binding["operator_ref"] in source.get("sender_ids", [])]

    def ensure_delivery_scope(self, selected: dict[str, Any], *, if_absent: bool = True, execute: bool = True) -> None:
        if selected["binding"].get("goal_scope") not in {"all_registered", "selected"} or self.controller is None:
            return
        # Match configuration's binding -> source-policy order. An observation
        # from before disconnect must never recreate its retired source grant.
        with exclusive_file_lock(self.path, operation="configure_bound_delivery_scope"):
            if selected["binding"] not in self.read()["bindings"]:
                raise ValueError("conversation binding is no longer authorized")
            self._ensure_delivery_scope(selected, if_absent=if_absent, execute=execute)

    def _ensure_delivery_scope(self, selected: dict[str, Any], *, if_absent: bool = True, execute: bool = True) -> None:
        """The caller retains the binding publication fence, including configure."""
        binding = selected["binding"]
        if binding.get("goal_scope") not in {"all_registered", "selected"} or self.controller is None:
            return
        from ..manager_context import configure_delivery_scope
        configure_delivery_scope(self.controller.coordination_runtime_root,
            channel=selected["channel_id"], local_delivery_scope=binding["goal_scope"],
            sender_id=binding["operator_ref"], execute=execute, if_absent=if_absent)

    @staticmethod
    def _session_source(saved: dict[str, Any]) -> dict[str, Any]:
        if saved.get("audience") == "bound_group":
            return {"source_ref": saved["source_ref"], "sender_ref": saved["operator_ref"],
                    "private_human_message": False, "group_ref": saved["group_ref"],
                    "topic_ref": saved["topic_ref"], "group_human_message": True, "session_context": saved}
        return {"source_ref": saved["source_ref"], "sender_ref": saved["operator_ref"],
                "private_human_message": True, "session_context": saved}

    def session_context(self, saved: dict[str, Any], *, session: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.resolve(binding_id=saved["binding_id"], session=session, **self._session_source(saved))

    def manager_runtime_owner(self, saved: dict[str, Any], channel_id: str) -> bool:
        """Fresh Core audience proof; saved Session text is never authority."""
        selected = self.session_context(saved)
        if selected["channel_id"] != channel_id:
            raise ValueError("bound manager audience changed")
        return selected["owner_manager_audience"] is True

    def steward_scope(self, session: dict[str, Any]) -> list[str] | None:
        saved = session.get("steward_context")
        if not isinstance(saved, dict):
            return None
        selected = self.session_context(saved)
        if session.get("goal_id") != "loopx-manager" or session.get("channel_id") != selected["channel_id"]:
            raise ValueError("bound steward audience changed")
        return list(selected["context"]["goal_ids"])

    def adopt_created_goal(self, *, binding_id: str, source: dict[str, Any], proposal: dict[str, Any],
                          goal: dict[str, Any]) -> None:
        selected = self.resolve(binding_id=binding_id, **source)
        with exclusive_file_lock(self.path, operation="adopt_steward_created_goal"):
            current = self.read()
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current["revision"], "operation": "adopt_created_goal",
                "binding_id": binding_id, "context": selected["context"], "proposal": proposal,
                "goal": {"goal_id": goal["id"], "workspace_path": str(Path(goal["repo"]).resolve()),
                         "creation_operation_id": goal.get("creation_operation_id")},
            })
            if result["changed"]:
                _atomic_write_json(self.path, result["state"])
            if self.read() != result["state"]:
                raise OSError("steward scope publication did not verify")

    def agent_observation(self, session_id: str) -> dict[str, Any]:
        """Read only the explicitly selected canonical attached Session/Goal.

        No host history, remote thread database or prior identity is discovered.
        """
        from ...attached_session import require_current_attached_session, _require_bound_host
        from ...control_plane.projects.registry_codec import load_registry
        from ...registry import find_registry_goal
        if self.controller is None or self.controller.registry_path is None:
            raise ValueError("the attached Session registry is unavailable")
        session = require_current_attached_session(store=self.controller.store,
            registry_path=self.controller.registry_path, session_id=session_id)
        registry = load_registry(self.controller.registry_path)
        goal = find_registry_goal(registry, str(session.get("goal_id") or ""))
        if goal is None or not goal.get("repo"):
            raise ValueError("the exact Agent workspace is unavailable")
        _require_bound_host(goal=goal, agent_id=session["agent_id"], host_surface=session["host_surface"],
                            host_session_id=session["upstream_thread_id"])
        return {"session": {key: session.get(key) for key in ["session_id", "goal_id", "goal_instance_id",
                    "agent_id", "executor_endpoint_id", "session_mode", "status", "external_conversation_binding_id"]},
                "goal": {"goal_id": goal["id"], "workspace_path": str(Path(goal["repo"]).resolve()),
                    "registered_agents": (goal.get("coordination") or {}).get("registered_agents", [])},
                "host_binding_verified": True, "host_ref": _host_ref(session),
                "host_audience_binding_ids": _host_audiences(self.controller.store, session)}

    def agent_candidates(self, binding_id: str) -> list[dict[str, Any]]:
        row = next(item for item in self.read()["bindings"] if item["binding_id"] == binding_id)
        if row["context_kind"] != "project" or row.get("audience") == "group" or self.controller is None:
            return []
        project = next((item for item in self.projects.available() if item["project_ref"] == row["project_ref"]), None)
        if project is None:
            return []
        candidates = []
        for session in self.controller.store.list_sessions():
            if session.get("session_mode") != "attached_host" or session.get("status") == "closed":
                continue
            try:
                observed = self.agent_observation(session["session_id"])
                if (observed["session"]["status"] in {"ready", "busy"}
                        and observed["goal"]["workspace_path"] == project["workspace_path"]
                        and observed["session"].get("external_conversation_binding_id") in {None, binding_id}
                        and all(owner == binding_id for owner in observed["host_audience_binding_ids"])):
                    candidates.append(observed["session"])
            except (ValueError, OSError, KeyError):
                continue
        return candidates[:16]

    def change_agent_target(self, *, binding_id: str, expected_revision: int,
                            session_id: str | None = None, target_ref: str | None = None) -> dict[str, Any]:
        row = next((item for item in self.read()["bindings"] if item["binding_id"] == binding_id), None)
        if row is None:
            raise ValueError("conversation binding is no longer authorized")
        # Provider IO must not prevent disconnect from publishing revocation.
        # Recheck the exact binding under its fence before using this observation.
        observation = self.observe(row["transport_ref"])
        with exclusive_file_lock(self.path, operation="configure_conversation_agent_target"):
            current = self.read()
            if next((item for item in current["bindings"] if item["binding_id"] == binding_id), None) != row:
                raise ValueError("conversation binding changed during identity verification")
            params = {"current": current, "expected_revision": expected_revision, "binding_id": binding_id,
                      "observation": observation, "available_projects": self.projects.available()}
            if session_id is not None:
                observed = self.agent_observation(session_id)
                target = {key: observed["session"].get(key) for key in ["session_id", "goal_id", "goal_instance_id",
                    "agent_id", "executor_endpoint_id"]}
                params.update(operation="grant_agent_target", target={"target_ref": uuid.uuid4().hex[:24], "host_ref": observed["host_ref"], **target},
                              target_observation=observed)
            else:
                params.update(operation="revoke_agent_target", target_ref=target_ref)
            result = self._core("collaboration.conversation.binding", params)
            if result["changed"]:
                if session_id is not None:
                    # agent_observation already requires this exact local controller.
                    assert self.controller is not None
                    self.controller.store.update_session(session_id, external_conversation_binding_id=binding_id)
                _atomic_write_json(self.path, result["state"])
            if self.read() != result["state"]:
                raise OSError("Agent target grant publication did not verify")
            state: dict[str, Any] = result["state"]
            return state

    def resolve_agent_target(self, selected: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
        return self._core("collaboration.conversation.agent_target", {"binding": selected["binding"],
            "context": selected["context"], "target": target,
            "target_observation": self.agent_observation(target["session_id"])})


    def stop_recoveries(self, current: dict[str, Any]) -> list[dict[str, Any]]:
        """Project revoked execution from canonical Turns, never recreate a grant.

        Session and external-target context retain the exact binding identity
        across restart. No second stop ledger or provider diagnostics are needed.
        A recovery disappears only when the existing Turn owner settles it.
        """
        if self.controller is None:
            return []
        authorized = {row["binding_id"] for row in current["bindings"]}
        store = self.controller.store
        pending: dict[str, dict[str, Any]] = {}
        for row in store.list_sessions():
            session = store.load_session(row["session_id"])
            if session is None:
                continue
            turns = store.queued_turns(session["session_id"])
            active = store.load_turn(session["session_id"], session["active_turn_id"]) if session.get("active_turn_id") else None
            if active:
                turns.append(active)
            for turn in turns:
                if turn.get("status") in TERMINAL_TURN_STATES:
                    continue
                target = turn.get("external_agent_target") or {}
                context = target.get("context") or session.get("project_context") or session.get("steward_context") or {}
                binding_id = context.get("binding_id")
                if not binding_id or binding_id in authorized:
                    continue
                recovery = pending.setdefault(binding_id, {"binding_id": binding_id,
                    "project_ref": context.get("project_ref", ""), "pending_count": 0})
                recovery["pending_count"] += 1
        return [pending[key] for key in sorted(pending)]


def validate_external_agent_turn(*, store: Any, session: dict[str, Any],
                                 goal: dict[str, Any] | None, turn: dict[str, Any]) -> None:
    """Called under the grant and canonical queue fences before host claim.

    The owner-local grant persists independently of a live provider connection.
    Admission and result delivery also verify the App/owner with the provider.
    """
    saved = turn.get("external_agent_target")
    if saved is None:
        return
    from ...attached_session import _require_bound_host
    if not isinstance(saved, dict) or turn.get("origin") not in {"lark", "external"} or goal is None:
        raise ValueError("the attached external audience is unavailable")
    context, target = saved["context"], saved["target"]
    if not Path(goal["repo"]).is_dir():
        raise ValueError("the attached Agent workspace is unavailable")
    state = _read_json(store.root / "conversation-bindings.json")
    selected = next((row for row in state["bindings"] if row["binding_id"] == context["binding_id"]), None)
    if selected is None:
        raise ValueError("the Agent audience grant was revoked")
    _require_bound_host(goal=goal, agent_id=session["agent_id"], host_surface=session["host_surface"],
                        host_session_id=session["upstream_thread_id"])
    ChatConversationBindings._core("collaboration.conversation.agent_target", {"binding": selected,
        "context": context, "target": target, "target_observation": {"session": session,
            "goal": {"goal_id": goal["id"], "workspace_path": str(Path(goal["repo"]).resolve()),
                     "registered_agents": (goal.get("coordination") or {}).get("registered_agents", [])},
            "host_binding_verified": True, "host_ref": _host_ref(session),
            "host_audience_binding_ids": _host_audiences(store, session)}})


def _host_ref(session: dict[str, Any]) -> str:
    return hashlib.sha256(f"{session['host_surface']}\0{session['upstream_thread_id']}".encode()).hexdigest()[:24]


def _host_audiences(store: Any, session: dict[str, Any]) -> list[str]:
    """Content-free stamps in this canonical store, including closed Sessions."""
    owners = set()
    for row in store.list_sessions():
        candidate = store.load_session(row["session_id"])
        if (candidate and candidate.get("host_surface") == session["host_surface"]
                and candidate.get("upstream_thread_id") == session["upstream_thread_id"]
                and candidate.get("external_conversation_binding_id")):
            owners.add(candidate["external_conversation_binding_id"])
    return sorted(owners)


class OriginalConversationRequests:
    """Chat/source/store IO for the shared original-conversation request profile.

    The existing source policy, peer operation, request, revision, result and
    Session FIFO remain their respective owners. No shadow queue is created.
    """

    def __init__(self, controller):
        self.controller = controller
        self.store = controller.store
        self.root = controller.coordination_runtime_root
        self.bindings = controller.project_contexts.conversation_bindings
        if self.bindings is None:
            raise ValueError("original conversation bindings are unavailable")

    _core = staticmethod(ChatConversationBindings._core)

    def _policy(self):
        from ...control_plane.collaboration.inbox import _read, _root
        path = _root(self.root) / "policy.json"
        return _read(path) if path.exists() else {"schema_version": "loopx_manager_context_policy_v1", "sources": {}}

    def _observation(self, session_id):
        session = self.store.load_session(session_id)
        if session is None:
            raise ValueError("original project Session is unavailable")
        saved = session.get("project_context") or {}
        current = self.bindings.read()
        binding = next((row for row in current["bindings"] if row["binding_id"] == saved.get("binding_id")), None)
        if binding is None:
            raise ValueError("original conversation grant was revoked")
        observed = dict(self.bindings.observe(binding["transport_ref"]))
        return binding, observed

    def _recipient(self, session_id, expected_binding, observation, policy):
        current = self.bindings.read()
        if next((row for row in current["bindings"] if row["binding_id"] == expected_binding["binding_id"]), None) != expected_binding:
            raise ValueError("original conversation binding changed during verification")
        return self._core("collaboration.project.recipient", {"current": current,
            "binding_id": expected_binding["binding_id"], "observation": observation,
            "available_projects": self.bindings.projects.available(),
            "session": self.store.load_session(session_id), "policy": policy,
            "sessions": [self.store.load_session(row["session_id"]) for row in self.store.list_sessions()]})

    @contextmanager
    def _source(self, requester, parent_request_id, *, require_active=True):
        from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
        from ...control_plane.collaboration.inbox import _entry
        from ...control_plane.collaboration.peer_context_observation import require_parent_context_access
        ref = requester.get("goal_ref") or {}
        with collaboration_goal_scope(self.controller.registry_path, goal_id=ref.get("goal_id"),
                agents=(requester.get("agent_id"),), caller_goal_ref=ref, require_active=require_active) as scope:
            if not scope.exact or requester != {"goal_ref": scope.caller_goal_ref, "agent_id": requester.get("agent_id")}:
                raise ValueError("an exact original requester is required")
            parent = _entry(self.root, scope.goal_id, requester["agent_id"], parent_request_id, scope=scope)
            if "request_control" in parent and self._view(parent)["control_state"] in {"cancelled", "cancellation_pending"}:
                raise ValueError("the original source request was cancelled")
            access = require_parent_context_access(self.root, self.controller.registry_path, parent,
                requester["agent_id"], scope=scope)
            yield {"requester": requester, "parent": parent, "parent_request_id": parent_request_id,
                "original_source_id": access["source_id"], "parent_access_verified": True}, scope

    @contextmanager
    def _fenced(self, session_id, requester, parent_request_id):
        from ...control_plane.collaboration.source_policy_fence import source_policy_fence
        from ...control_plane.collaboration.inbox import _root
        binding, observed = self._observation(session_id)
        # Every writer uses source -> Goal -> binding -> request -> Session.
        # Provider IO precedes these fences; canonical identity is reread inside.
        with source_policy_fence(_root(self.root) / "policy.json"), self._source(requester, parent_request_id) as (source, scope):
            with exclusive_file_lock(self.bindings.path, operation="original_conversation_peer"):
                policy = self._policy()
                selected = self._recipient(session_id, binding, observed, policy)
                yield {**source, "policy": policy, "recipient": selected["recipient"],
                    "current_recipient": selected["recipient"], "policy_sha256": selected["policy_sha256"]}, scope

    def configure(self, session_id, body):
        from ...control_plane.collaboration.inbox import _root, _write, _read
        from ...control_plane.collaboration.source_policy_fence import source_policy_fence
        enable = body.get("grant")
        if not isinstance(enable, bool):
            raise ValueError("grant must be an explicit boolean")

        def apply(facts):
            plan = self._core("collaboration.source.original_conversation_grant", {
                **facts, "grant": enable, "expected_policy_sha256": body.get("expected_policy_sha256")})
            path = _root(self.root) / "policy.json"
            if body.get("execute") is True and plan["would_change"]:
                _write(path, plan["policy"])
                if _read(path) != plan["policy"]:
                    raise OSError("original conversation source grant did not verify")
            return {"ok": True, "executed": body.get("execute") is True,
                **{key: value for key, value in plan.items() if key != "policy"}}

        if enable:
            with self._fenced(session_id, body["requester"], body["parent_request_id"]) as (facts, _):
                return apply(facts)
        # An owner may revoke a retained grant even while its provider or source
        # is unavailable. This does not authorize reading or starting its work.
        with source_policy_fence(_root(self.root) / "policy.json"):
            recipient = body["recipient"]
            if recipient.get("session_id") != session_id:
                raise ValueError("original conversation revocation scope mismatch")
            return apply({"policy": self._policy(), "recipient": recipient,
                "requester": body["requester"], "parent_request_id": body["parent_request_id"],
                "original_source_id": body["source_id"]})

    def management_view(self, session_id, *, search="", cursor=None):
        from ...control_plane.collaboration.inbox import _root, _read
        from ...control_plane.collaboration.source_policy_fence import source_policy_fence
        with source_policy_fence(_root(self.root) / "policy.json"):
            policy = self._policy()
            view = self._core("collaboration.source.original_conversation_grants", {"policy": policy, "session_id": session_id})
        for grant in view["grants"]:
            requester = grant["requester"]
            address = {"goal_ref": requester["goal_ref"], "agent_id": requester["agent_id"]}
            try:
                from ...control_plane.collaboration.inbox import _hash
                parent = _read(_root(self.root) / "entries" / _hash(address) / (grant["parent_request_id"] + ".json"))
                if parent.get("goal_ref") != requester["goal_ref"] or parent.get("agent_id") != requester["agent_id"]:
                    raise ValueError("source grant parent identity changed")
                grant["source_purpose"] = (parent.get("brief") or {}).get("purpose", parent.get("message", ""))
            except (ValueError, OSError):
                grant["source_purpose"] = None
        try:
            binding, observed = self._observation(session_id)
            with exclusive_file_lock(self.bindings.path, operation="original_conversation_inventory"):
                view.update(self._recipient(session_id, binding, observed, policy), available=True)
        except (ValueError, KeyError, OSError) as exc:
            return {"ok": True, **view, "available": False, "error": str(exc), "candidates": [], "requests": []}
        candidates, unreadable, overflow = [], 0, False
        # Content is projected only to the trusted local owner. A later grant
        # still rechecks the exact registration, parent lineage and source.
        for path in sorted((_root(self.root) / "entries").glob("*/*.json")):
            try:
                row = _read(path)
                if row.get("schema_version") != "loopx_manager_context_entry_v2" or not row.get("goal_ref"):
                    continue
                purpose = (row.get("brief") or {}).get("purpose", row.get("message", ""))
                if search.strip().casefold() not in " ".join((row["goal_ref"]["goal_id"], row["agent_id"], purpose)).casefold():
                    continue
                candidates.append({"requester": {"goal_ref": row["goal_ref"], "agent_id": row["agent_id"]},
                    "parent_request_id": row["request_id"], "purpose": (row.get("brief") or {}).get("purpose", row.get("message", ""))})
                if len(candidates) == 128:
                    overflow = True
                    break
            except (ValueError, OSError, KeyError):
                unreadable += 1
        view.update(candidates=candidates, coverage={"unreadable": unreadable, "overflow": overflow})
        page = self._inbox(view["recipient"], mark_read=False, cursor=cursor)
        view["requests"], view["requests_has_more"] = page["items"], page["has_more"]
        view["requests_next_cursor"] = page["next_cursor"]
        from ...control_plane.collaboration.inbox import result_paths, inspect_result_identity, reply_status
        for item in view["requests"]:
            results = []
            statuses = {row.get("result_key", row["phase"]): row["status"] for row in reply_status(self.root, item)}
            turn = self.store.turn_for_client(session_id, "peer-" + item["request_id"])
            item["queue_status"] = turn.get("status") if turn else None
            for path in result_paths(_root(self.root) / "replies" / item["request_id"]):
                reply = _read(path)
                if inspect_result_identity(reply, self._entry(item["request_id"], item["recipient"]), path):
                    results.append({"result_key": path.stem, "text": reply["text"], "decision": reply["decision"],
                        "request_revision": reply.get("request_revision", 1), "delivery_status": statuses.get(path.stem, "queued")})
            item["returns"] = results
        return {"ok": True, **view}

    def discover(self, requester, parent_request_id):
        from ...control_plane.collaboration.inbox import _root
        from ...control_plane.collaboration.source_policy_fence import source_policy_fence
        with source_policy_fence(_root(self.root) / "policy.json"), self._source(requester, parent_request_id) as (source, _):
            grants = self._core("collaboration.source.original_conversation_grants", {"policy": self._policy()})["grants"]
        recipients, unavailable = [], 0
        for grant in grants:
            if grant["requester"] != requester or grant["parent_request_id"] != parent_request_id:
                continue
            try:
                with self._fenced(grant["recipient"]["session_id"], requester, parent_request_id) as (facts, _):
                    self._core("collaboration.source.original_conversation_access", facts)
                    if facts["recipient"] != grant["recipient"]:
                        raise ValueError("original conversation recipient changed")
                    session = self.store.load_session(facts["recipient"]["session_id"])
                    recipients.append({"recipient": facts["recipient"], "title": session.get("title") or "Project conversation"})
            except (ValueError, KeyError, OSError):
                unavailable += 1
        return {"ok": True, "recipients": recipients, "unavailable": unavailable}

    def publish(self, body):
        from ...control_plane.collaboration.inbox import _hash, _now, _read, _request_lock, _root, _write
        from ...control_plane.collaboration.peers import _operation_address
        recipient = body["recipient"]
        session_id = recipient["session_id"]
        with self._fenced(session_id, body["requester"], body["parent_request_id"]) as (facts, scope):
            if recipient != facts["recipient"]:
                raise ValueError("original conversation recipient identity changed")
            source_id, op_path = _operation_address(self.root, scope, body["requester"]["agent_id"], body["operation_id"])
            # Native planning owns identity and replay. The old operation is
            # consulted before any queue input or return route can be written.
            prior = _read(op_path) if op_path.exists() else None
            preview = self._core("collaboration.project.request", {**facts, "source_id": source_id,
                "request": {"recipient": recipient, "brief": body["brief"]}, "observed_at": _now()})
            request_id = preview["entry"]["request_id"]
            path = _root(self.root) / "entries" / _hash({"recipient": recipient}) / (request_id + ".json")
            with _request_lock(self.root, request_id, None, op_path, force_exact=True):
                prior = _read(op_path) if op_path.exists() else prior
                plan = self._core("collaboration.project.request", {**facts, "source_id": source_id,
                    "request": {"recipient": recipient, "brief": body["brief"]}, "observed_at": _now(),
                    "operation": prior, "entry": _read(path) if path.exists() else None})
                row = plan["entry"]
                route = plan["return_route"]
                route_path = _root(self.root) / "roundtrips" / (request_id + ".json")
                if route_path.exists() and _read(route_path) != route:
                    raise ValueError("original requester return route identity conflict")
                if prior is None:
                    _write(op_path, plan["operation"])
                if not route_path.exists():
                    _write(route_path, route)
                if _read(route_path) != route:
                    raise OSError("original requester return route did not verify")
                if not plan["replayed"]:
                    _write(path, row)
                if _read(path) != row:
                    raise OSError("original conversation request did not verify")
                view = self._view(row)
                if view["control_state"] == "cancelled":
                    raise ValueError("original conversation request is cancelled")
                prior_turn = self.store.turn_for_client(session_id, "peer-" + request_id)
                turn, created = self.store.create_queued_turn(session_id, client_turn_id="peer-" + request_id,
                    message=prior_turn["message"] if prior_turn else row["message"], origin="peer",
                    collaboration_request={"request_id": request_id, "recipient": recipient})
        recovered = None
        if not created and turn.get("collaboration_answer") is not None:
            recovered = self._complete_answer(session_id, turn, turn["collaboration_answer"])["collaboration_result"]
        if turn["status"] not in TERMINAL_TURN_STATES:
            context = self.controller.project_contexts.session_context(self.store.load_session(session_id))
            self.controller.resume_session_queue(session_id=session_id, work_dir=Path(context["project"]), objective=str(context.get("objective") or context["title"]))
        return {"ok": True, "request_id": request_id, "recipient": recipient, "turn_id": turn["turn_id"],
            "status": turn["status"], "replayed": not created, "goal_created": False, "recovered_result": recovered}

    def _entry(self, request_id, recipient):
        from ...control_plane.collaboration.inbox import _entry
        return _entry(self.root, None, None, request_id, recipient=recipient)

    def _view(self, row):
        return self._core("collaboration.request.control", {"entry": row, "input": {"operation": "inspect"}, "source": {}})["view"]

    def _inbox(self, recipient, *, mark_read, request_id=None, cursor=None):
        from ...control_plane.collaboration.inbox import _now
        result = self._core("collaboration.inbox.read", {"runtime_root": str(self.root.resolve()),
            "scope": {"profile_id": "original_conversation_v0", "recipient": recipient},
            "operation": "receiver_read" if mark_read else "pending", "request_id": request_id,
            "include_settled": not mark_read,
            "cursor": cursor,
            "observed_at": _now(), "read_command": "original project Turn supplied context"}, large_local_snapshot=True)
        return result["result"]

    def control(self, body):
        from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
        from ...control_plane.collaboration.source_policy_fence import source_policy_fence
        from ...control_plane.collaboration.inbox import _now, _root, _request_lock, _hash, _write, _read
        requester, recipient = body["requester"], body["recipient"]
        operation, parameters = body["control"], body["parameters"]
        if operation not in {"inspect", "revise", "cancel"} or not isinstance(parameters, dict):
            raise ValueError("unsupported original conversation source control")
        row = self._entry(body["request_id"], recipient)
        if row["requester"] != requester:
            raise ValueError("original requester identity changed")
        if operation == "revise":
            fence = self._fenced(recipient["session_id"], requester, row["parent_request_id"])
        else:
            # Revocation or a closed recipient must not remove the original
            # source's ability to inspect/cancel its retained request.
            @contextmanager
            def retained_source():
                with source_policy_fence(_root(self.root) / "policy.json"), collaboration_goal_scope(
                        self.controller.registry_path, goal_id=requester["goal_ref"]["goal_id"],
                        agents=(requester["agent_id"],), caller_goal_ref=requester["goal_ref"]) as scope:
                    if not scope.exact:
                        raise ValueError("original requester exact instance is unavailable")
                    yield {}, scope
            fence = retained_source()
        with fence as (facts, _), _request_lock(self.root, row["request_id"], None, Path(), force_exact=True):
            current = self._entry(row["request_id"], recipient)
            route = _read(_root(self.root) / "roundtrips" / (row["request_id"] + ".json"))
            self._core("collaboration.project.return_route", {"entry": current, "route": route})
            if operation == "revise":
                if recipient != facts["recipient"]:
                    raise ValueError("original conversation recipient changed")
                self._core("collaboration.source.original_conversation_access", facts)
            plan = self._core("collaboration.request.control", {"entry": current,
                "input": {**parameters, "operation": operation},
                "source": {"actor": "source", "actor_agent_id": requester["agent_id"],
                    "actor_requester": requester,
                    "recipient_active": operation == "revise", "observed_at": _now()}})
            if plan["changed"]:
                _write(_root(self.root) / "entries" / _hash({"recipient": recipient}) / (row["request_id"] + ".json"), plan["entry"])
        stop = None
        if operation == "cancel":
            turn = self.store.turn_for_client(recipient["session_id"], "peer-" + row["request_id"])
            if turn and turn["status"] not in TERMINAL_TURN_STATES:
                stop = self.controller.interrupt_turn(session_id=recipient["session_id"], turn_id=turn["turn_id"])
        return {"ok": True, "request_control": plan["view"], "replayed": plan.get("replayed", False), "turn_stop": stop}

    def validate_turn(self, session_id, turn):
        if turn.get("origin") != "peer" and "collaboration_request" not in turn:
            return
        reference = turn.get("collaboration_request")
        if turn.get("origin") != "peer" or not isinstance(reference, dict):
            raise ValueError("peer Turn provenance is unavailable")
        row = self._entry(reference["request_id"], reference["recipient"])
        if (row["recipient"]["session_id"] != session_id or turn.get("session_id") != session_id
                or turn.get("client_turn_id") != "peer-" + row["request_id"]):
            raise ValueError("peer Turn does not belong to this original Session")
        with self._fenced(session_id, row["requester"], row["parent_request_id"]) as (facts, _):
            self._validate_current(row, facts)
            if self._view(row)["control_state"] == "cancelled":
                raise ValueError("original conversation request was cancelled")

    def _validate_current(self, row, facts):
        from ...control_plane.collaboration.inbox import _read, _root
        self._core("collaboration.project.request", {**facts, "source_id": row["source_id"],
            "request": {"recipient": row["recipient"], "brief": row["brief"]}, "entry": row,
            "operation": None, "observed_at": row["delivered_at"],
            "route": _read(_root(self.root) / "roundtrips" / (row["request_id"] + ".json"))})

    def _complete_answer(self, session_id, turn, answer):
        from ...control_plane.collaboration.inbox import _now, _request_lock, _root, _hash, _write, record_result
        from ...chat import CHAT_AGENT_RESPONSE_SCHEMA_VERSION
        if set(answer) != set(self._core("collaboration.project.response_schema", {})["required"]):
            raise ValueError("original conversation answer has unsupported fields")
        ref = turn["collaboration_request"]
        row = self._entry(ref["request_id"], ref["recipient"])
        with self._fenced(session_id, row["requester"], row["parent_request_id"]) as (facts, _):
            self._validate_current(row, facts)
            self._core("collaboration.source.original_conversation_access", facts)
            if facts["recipient"] != row["recipient"]:
                raise ValueError("original conversation recipient changed")
            with _request_lock(self.root, row["request_id"], None, Path(), force_exact=True):
                row = self._entry(row["request_id"], row["recipient"])
                view = self._view(row)
                if (view["control_state"] == "cancelled" or answer["request_revision"] != view["revision"]
                        or answer["request_digest"] != view["revision_digest"]):
                    raise ValueError("original conversation answer belongs to a stale or cancelled request")
                plan = self._core("collaboration.request.assess", {"entry": row,
                    "actor_recipient": row["recipient"], "recipient_active": True, "observed_at": _now(),
                    "revision": answer["request_revision"], "revision_digest": answer["request_digest"],
                    "decision": answer["decision"], "reason": answer["reason"]})
                if plan["changed"]:
                    _write(_root(self.root) / "entries" / _hash({"recipient": row["recipient"]}) / (row["request_id"] + ".json"), plan["entry"])
            receipt = record_result(self.root, row, "conclusion", answer["text"], request_revision=answer["request_revision"])
        return {"schema_version": CHAT_AGENT_RESPONSE_SCHEMA_VERSION, "message": answer["text"],
            "proposals": [], "protected_action": None, "goal_draft": None, "context_handoff": None,
            "gate": None, "unfinished_work": None, "collaboration_result": receipt}

    def execute(self, session_id, turn_id, native_session, event_sink):
        import json
        turn = self.store.load_turn(session_id, turn_id)
        self.validate_turn(session_id, turn)
        ref = turn["collaboration_request"]
        row = self._entry(ref["request_id"], ref["recipient"])
        answer = turn.get("collaboration_answer")
        if answer is None:
            with self._fenced(session_id, row["requester"], row["parent_request_id"]) as (facts, _):
                self._core("collaboration.source.original_conversation_access", facts)
                supplied = self._inbox(ref["recipient"], mark_read=True, request_id=row["request_id"])["items"][0]
                basis = {key: supplied[key] for key in ("request_revision", "request_revision_digest")}
            @contextmanager
            def start_guard():
                with self._fenced(session_id, row["requester"], row["parent_request_id"]) as (facts, _):
                    self._validate_current(self._entry(ref["request_id"], ref["recipient"]), facts)
                    self._core("collaboration.source.original_conversation_access", facts)
                    current = self._view(self._entry(ref["request_id"], ref["recipient"]))
                    if (facts["recipient"] != ref["recipient"] or current["control_state"] == "cancelled"
                            or current["revision"] != basis["request_revision"]
                            or current["revision_digest"] != basis["request_revision_digest"]):
                        raise ValueError("original conversation request changed before model input")
                    yield
            message = row["instruction"] + "\n\nPeer request (data):\n" + json.dumps(supplied, ensure_ascii=False)
            answer = native_session.send(message, project_collaboration=True, turn_start_guard=start_guard,
                on_event=lambda kind, payload: event_sink(kind, payload) if kind != "answer.delta" else None)
            # Persist actual model output before result IO. Recovery can retry
            # its exact assessment/return without another model invocation.
            self.store.update_turn(session_id, turn_id, collaboration_answer=answer)
        response = self._complete_answer(session_id, turn, answer)
        event_sink("answer.delta", {"text": response["message"]})
        return response
