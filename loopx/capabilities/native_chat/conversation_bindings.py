"""Private IO for Core-owned conversation context and audience grants.

Provider observations contain opaque App/owner references, not credentials.
The typed owner validates configuration, replacement, revocation, and use.
This file stores no conversation text and creates no Goal or model thread.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeAlias, TypedDict, TypeVar, cast
import uuid

from ...chat_store import TERMINAL_TURN_STATES, _atomic_write_json, _read_json
from ...control_plane.collaboration import inbox
from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
from ...control_plane.collaboration.source_policy_fence import source_policy_fence
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_request_scope, effect_runtime_result
from ...file_lock import exclusive_file_lock

JsonDict: TypeAlias = dict[str, object]
JsonMap: TypeAlias = Mapping[str, object]
_T = TypeVar("_T")

if TYPE_CHECKING:
    from ...chat_store import ChatSessionStore
    from ...control_plane.collaboration.goal_instance_scope import CollaborationGoalScope
    class _ProjectContexts(Protocol):
        workspace_grant: str
        conversation_bindings: ChatConversationBindings | None
        def available(self) -> Sequence[JsonMap]: ...
        def session_context(self, session: JsonDict) -> JsonDict: ...
    class _BindingController(Protocol):
        store: ChatSessionStore
        coordination_runtime_root: Path
        registry_path: Path | None
        @property
        def project_contexts(self) -> _ProjectContexts: ...
        def interrupt_turn(self, *, session_id: str, turn_id: str) -> JsonDict: ...
        def resume_session_queue(self, *, session_id: str, work_dir: Path, objective: str) -> None: ...
    class _NativePeerSession(Protocol):
        def send(self, user_message: str, *, project_collaboration: bool = False,
                 turn_start_guard: Callable[[], AbstractContextManager[None]] | None = None,
                 on_event: Callable[[str, JsonDict], None] | None = None) -> JsonDict: ...
    class _RequestLockFn(Protocol):
        def __call__(self, root: Path, request_id: str, scope: object, legacy_path: Path, *, force_exact: bool = False) -> AbstractContextManager[None]: ...
    class _RecordResultFn(Protocol):
        def __call__(self, root: Path, row: JsonMap, phase: str, text: str, *, request_revision: int | None = None) -> JsonDict: ...
    class _AgentObservation(TypedDict):
        session: JsonDict
        goal: JsonDict
        host_binding_verified: bool
        host_ref: str
        host_audience_binding_ids: list[str]
    _EventSink: TypeAlias = Callable[[str, JsonDict], None]


def _need(value: object, kind: type[_T], message: str = "unexpected value type") -> _T:
    if not isinstance(value, kind):
        raise ValueError(message)
    return value


def _as_dict(value: object, message: str = "expected JSON object") -> JsonDict:
    return cast("JsonDict", _need(value, dict, message))


def _dict_or_empty(value: object) -> JsonDict:
    return cast("JsonDict", value) if isinstance(value, dict) else {}


def _dict_list(value: object, message: str = "expected list of objects") -> list[JsonDict]:
    return [_as_dict(item, message) for item in _need(value, list, message)]


def _find_binding(state: JsonMap, key: str, value: object, *, default_empty: bool = False) -> JsonDict | None:
    rows = state.get("bindings", []) if default_empty else state["bindings"]
    if not isinstance(rows, list):
        raise ValueError("invalid conversation bindings state")
    return next((cast("JsonDict", row) for row in rows if isinstance(row, dict) and row.get(key) == value), None)


def _require_binding(state: JsonMap, key: str, value: object) -> JsonDict:
    if (row := _find_binding(state, key, value)) is None:
        raise ValueError("conversation binding is no longer authorized")
    return row


def _session_mentions_binding(session: JsonMap, binding_id: str) -> bool:
    return any(isinstance(ctx := session.get(k), dict) and ctx.get("binding_id") == binding_id for k in ("project_context", "steward_context"))


def _iter_open_session_turns(store: ChatSessionStore) -> Iterator[tuple[JsonDict, JsonDict]]:
    for row in store.list_sessions():
        if (session := store.load_session(_need(row["session_id"], str))) is None:
            continue
        session_id = _need(session["session_id"], str)
        turns = store.queued_turns(session_id)
        if isinstance(active_id := session.get("active_turn_id"), str) and active_id and (active := store.load_turn(session_id, active_id)) is not None:
            turns.append(active)
        for turn in turns:
            if turn.get("status") not in TERMINAL_TURN_STATES:
                yield session, turn


def _host_observation_fields(store: ChatSessionStore, session: JsonMap, goal: JsonMap) -> JsonDict:
    return {"goal": {"goal_id": goal["id"], "workspace_path": str(Path(_need(goal["repo"], str)).resolve()),
                     "registered_agents": _dict_or_empty(goal.get("coordination")).get("registered_agents", [])},
            "host_binding_verified": True, "host_ref": _host_ref(session), "host_audience_binding_ids": _host_audiences(store, session)}


class ChatConversationBindings:
    def __init__(self, *, root: Path, project_contexts: _ProjectContexts | None,
                 observe: Callable[[str], JsonDict], observe_group: Callable[[str], JsonDict] | None = None) -> None:
        self.path, self.projects = root / "conversation-bindings.json", project_contexts
        self._observe_owner, self._observe_group = observe, observe_group
        self.controller: _BindingController | None = None

    def _require_projects(self) -> _ProjectContexts:
        if self.projects is None:
            raise ValueError("workspace grants unavailable")
        return self.projects

    def _require_controller(self) -> _BindingController:
        if self.controller is None:
            raise ValueError("the bound conversation store is unavailable")
        return self.controller

    def _publish_state(self, result: JsonMap, *, error_message: str, preserve_mode: bool = False) -> JsonDict:
        state = _as_dict(result["state"])
        if result["changed"]:
            _atomic_write_json(self.path, state, preserve_mode=True) if preserve_mode else _atomic_write_json(self.path, state)
        if (readback := self.read()) != state:
            raise OSError(error_message)
        return readback

    def _verify_unchanged(self, key: str, value: object, expected: JsonMap, *,
                          message: str = "conversation binding changed during identity verification", default_empty: bool = False) -> JsonDict:
        current = self.read()
        if _find_binding(current, key, value, default_empty=default_empty) != expected:
            raise ValueError(message)
        return current

    def _resolve_source(self, binding_id: str, source: JsonMap, *, include_observation: bool = False, session: JsonMap | None = None) -> JsonDict:
        session_context, group_ref, topic_ref, origin = source.get("session_context"), source.get("group_ref"), source.get("topic_ref"), source.get("origin")
        return self.resolve(
            binding_id=binding_id, source_ref=_need(source["source_ref"], str), sender_ref=_need(source["sender_ref"], str),
            private_human_message=_need(source["private_human_message"], bool),
            session_context=_as_dict(session_context) if session_context is not None else None,
            group_ref=_need(group_ref, str) if group_ref is not None else None, topic_ref=_need(topic_ref, str) if topic_ref is not None else None,
            group_human_message=_need(source.get("group_human_message", False), bool), include_observation=include_observation,
            origin=_need(origin, str) if origin is not None else None, session=session,
        )

    def observe(self, transport_ref: str, *, audience: str | None = None) -> JsonDict:
        row = _find_binding(self.read(), "transport_ref", transport_ref) or {}
        if audience is None:
            audience = raw if isinstance(raw := row.get("audience"), str) else "owner"
        if audience == "group" and self._observe_group is not None:
            observed = self._observe_group(transport_ref)
            # Earlier group grants used the verified user's principal. Keep
            # that exact identity/home until an explicit disconnect/reconnect;
            # changing it during a poll would invalidate resumed Sessions.
            if row.get("audience") == "group" and row.get("provider_ref") == observed["provider_ref"] and row.get("operator_ref") != observed["operator_ref"]:
                return self._observe_owner(transport_ref)
            return observed
        return self._observe_owner(transport_ref)

    def read(self) -> JsonDict:
        if not self.path.exists():
            return {"schema_version": "loopx_chat_conversation_bindings_v0", "revision": 0, "bindings": []}
        # An unreadable existing grant is not an empty configuration to overwrite.
        return cast("JsonDict", _read_json(self.path))

    @staticmethod
    def _core(operation: str, params: JsonMap, *, large_local_snapshot: bool = False) -> JsonDict:
        try:
            result = effect_runtime_result(operation, params, large_local_snapshot=large_local_snapshot)
        except EffectRuntimeRejected as exc:
            raise ValueError(str(exc)) from exc
        if not isinstance(result, dict):
            raise ValueError("conversation authority returned an invalid result")
        return cast("JsonDict", result)

    def configure(self, *, transport_ref: str, project_ref: str, executor_endpoint_id: str,
                  context_kind: str = "project", project_grant: str | None = None, goal_scope: str | None = None,
                  audience: str | None = None, group_refs: list[str] | None = None, available_group_refs: list[str] | None = None,
                  expected_provider_ref: str | None = None, expected_operator_ref: str | None = None) -> JsonDict:
        if goal_scope is not None and (context_kind != "steward" or goal_scope not in {"selected", "all_registered"}):
            raise ValueError("select a supported steward Goal scope")
        projects = self._require_projects()
        if project_grant is None:
            project_grant = projects.workspace_grant if context_kind == "project" and executor_endpoint_id == "codex" else "workspace_read"
        if project_grant not in {"workspace_read", "workspace_write"} or (context_kind != "project" and project_grant != "workspace_read"):
            raise ValueError("workspace write authorization is only available for project Chat")
        observation = self.observe(transport_ref, audience=audience or "owner")
        for key, expected, label in (("provider_ref", expected_provider_ref, "App"), ("operator_ref", expected_operator_ref, "owner")):
            if expected is not None and observation[key] != expected:
                raise ValueError(f"the selected {label} changed before binding publication; verify it again")
        candidate: JsonDict = {
            "schema_version": "loopx_chat_conversation_binding_v0", "binding_id": uuid.uuid4().hex[:24],
            "transport_ref": transport_ref, "provider_ref": observation["provider_ref"], "operator_ref": observation["operator_ref"],
            "context_kind": context_kind, "project_ref": project_ref, "executor_endpoint_id": executor_endpoint_id,
            "grant": project_grant if context_kind == "project" else "portfolio_read", "enabled": True,
            **({"goal_ids": [], "goal_scope": goal_scope or "all_registered"} if context_kind == "steward" else {}),
            **({"audience": audience} if audience is not None else {}), **({"group_refs": group_refs} if group_refs is not None else {}),
        }
        with exclusive_file_lock(self.path, operation="configure_chat_conversation_binding"):
            current = self.read()
            previous = _find_binding(current, "transport_ref", transport_ref)
            if previous is not None and all(previous.get(k) == candidate.get(k) for k in ("context_kind", "project_ref", "executor_endpoint_id", "provider_ref", "operator_ref")):
                if all(previous.get(k) == candidate.get(k) for k in ("grant", "audience", "group_refs")) and previous.get("agent_targets"):
                    candidate["agent_targets"] = previous["agent_targets"]
                if context_kind == "steward":
                    candidate.update(goal_ids=previous["goal_ids"], binding_id=previous["binding_id"])
                    # Existing installations retain their exact audience and scope
                    # until the local operator explicitly changes it.
                    if goal_scope is None:
                        candidate.pop("goal_scope", None)
                        if "goal_scope" in previous:
                            candidate["goal_scope"] = previous["goal_scope"]
                    if previous.get("managed_actor") is not None:
                        candidate["managed_actor"] = previous["managed_actor"]
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current.get("revision"), "operation": "configure", "binding": candidate,
                "observation": observation, "available_projects": projects.available(), **({"available_group_refs": available_group_refs} if audience == "group" else {}),
            })
            proposed = _require_binding(_as_dict(result["state"]), "transport_ref", transport_ref)
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
                self._revoke_delivery_sources(_need(previous["binding_id"], str))
            binding = _require_binding(self._publish_state(result, error_message="conversation binding publication did not verify"), "transport_ref", transport_ref)
        if previous and previous["binding_id"] != binding["binding_id"]:
            self._stop_revoked_binding(_need(previous["binding_id"], str))
        return binding

    def disconnect(self, binding_id: str, *, expected_revision: int) -> JsonDict:
        with exclusive_file_lock(self.path, operation="disconnect_chat_conversation_binding"):
            result = self._core("collaboration.conversation.binding", {"current": self.read(), "expected_revision": expected_revision, "operation": "disconnect", "binding_id": binding_id})
            self._revoke_delivery_sources(binding_id)
            state = self._publish_state(result, error_message="conversation binding revocation did not verify")
        self._stop_revoked_binding(binding_id)
        return dict(state)

    def select_app_steward(self, binding_id: str | None, *, expected_revision: int) -> JsonDict:
        """Select one existing source for the App entry without changing grants."""
        row = _find_binding(self.read(), "binding_id", binding_id) if binding_id is not None else None
        observation = self.observe(_need(row["transport_ref"], str)) if row is not None else None
        with exclusive_file_lock(self.path, operation="select_app_steward"):
            result = self._core("collaboration.conversation.binding", {
                "current": self.read(), "expected_revision": expected_revision, "operation": "select_app_steward",
                "binding_id": binding_id, "observation": observation, "available_projects": self._require_projects().available() if binding_id is not None else [],
            })
            return dict(self._publish_state(result, error_message="App steward selection did not verify"))

    def reconcile_steward_executors(self) -> None:
        """Publish a previously adopted machine executor at Chat startup."""
        from ...chat_manager import steward_machine_defaults

        if (controller := self.controller) is None:
            return
        defaults = steward_machine_defaults(controller)
        if (not defaults or defaults.get("status") != "ready" or defaults.get("source") != "machine_configuration"
                or defaults.get("selection_policy") not in {"preferred", "pinned"} or defaults.get("executor_endpoint") not in {"antigravity", "codex"}):
            return
        try:
            if not isinstance(rows := self.read()["bindings"], list):
                raise ValueError("invalid conversation bindings state")
        except (OSError, ValueError, KeyError) as exc:
            logging.getLogger(__name__).warning("Steward bindings unavailable for reconciliation (%s)", type(exc).__name__)
            return
        for row in rows:
            if (not isinstance(row, dict) or row.get("context_kind") != "steward" or row.get("audience", "owner") != "owner"
                    or row.get("enabled") is not True or row.get("executor_endpoint_id") not in {"antigravity", "codex"}
                    or row.get("executor_endpoint_id") == defaults["executor_endpoint"]):
                continue
            try:
                self._reconcile_steward_executor(row, defaults)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                logging.getLogger(__name__).warning("Steward executor reconciliation held (%s)", type(exc).__name__)

    def _reconcile_steward_executor(self, row: JsonDict, defaults: JsonMap) -> None:
        from ...chat_manager import manager_executor_allocation, steward_machine_defaults
        from ..machine_configuration import builtins as _mc_builtins, contract as _mc_contract, store as _mc_store
        from ..steward_executor import allocation as _alloc, machine_defaults as _md

        controller = self._require_controller()
        store, binding_id, endpoint = controller.store, _need(row["binding_id"], str), defaults["executor_endpoint"]
        prefix = f"manager.external.native.{binding_id}."
        candidates = [s for s in store.list_sessions() if str(s.get("channel_id") or "").startswith(prefix) and s.get("status") != "closed"]
        if len(candidates) != 1 or candidates[0].get("agent_id") != endpoint or candidates[0].get("status") != "ready":
            return  # Defaults alone do not retarget an existing session.
        observation, session_id = self.observe(_need(row["transport_ref"], str)), _need(candidates[0]["session_id"], str)
        with exclusive_file_lock(self.path, operation="reconcile_steward_executor"):
            current = self.read()
            if row not in _dict_list(current["bindings"]) or steward_machine_defaults(controller) != defaults or not (session := store.load_session(session_id)):
                return
            saved, stored = session.get("steward_context"), session.get("manager_executor_allocation")
            if (not isinstance(saved, dict) or not isinstance(stored, dict) or session.get("status") != "ready"
                    or session.get("active_turn_id") is not None or session.get("agent_id") != endpoint or session.get("executor_endpoint_id") != endpoint
                    or stored.get("selection_policy") != defaults["selection_policy"] or store.queued_turns(session_id, strict=True, include_reserved=True)):
                return
            allocation: JsonDict | None = None
            if stored.get("configuration_revision") == defaults.get("configuration_revision"):
                try:
                    allocation = _alloc.normalize_manager_executor_allocation(stored)
                except (TypeError, ValueError):
                    pass
            else:
                # A machine configuration transaction records the revision of
                # the whole document. Match that exact basis before projecting
                # it to the narrower revision used by steward allocations.
                configuration = _mc_store.read_machine_configuration(store.root.parent, registry=_mc_builtins.build_builtin_machine_configuration_registry())
                if (not configuration or _mc_contract.machine_configuration_revision(configuration) != stored.get("configuration_revision")
                        or _md.effective_steward_executor_defaults(configuration) != defaults):
                    return
            if allocation is None:
                allocation = manager_executor_allocation(controller, machine_defaults=defaults)
            expected: JsonDict = {**self._session_observation(session), "manager_executor_allocation": allocation,
                                  "active_turn_id": session.get("active_turn_id"), "agent_model_id": session.get("agent_model_id"),
                                  "agent_reasoning_effort": session.get("agent_reasoning_effort")}
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current["revision"], "operation": "reconcile_steward_executor", "binding_id": binding_id,
                "observation": observation, "available_projects": self._require_projects().available(), "available_goal_ids": self.goal_scope_ids(row),
                "machine_defaults": dict(defaults), "session": expected, "queue_empty": True,
            })
            if stored != allocation:
                upstream = session.get("upstream_thread_id")
                store.update_session(session_id, expected_statuses={"ready"}, expected_upstream_thread_id=upstream if isinstance(upstream, str) else None,
                                     manager_executor_allocation=allocation)
            fresh = store.load_session(session_id)
            if (not fresh or any(fresh.get(k) != v for k, v in expected.items())
                    or store.queued_turns(session_id, strict=True, include_reserved=True) or steward_machine_defaults(controller) != defaults):
                return
            self._publish_state(result, error_message="steward executor binding publication did not verify", preserve_mode=True)

    def app_steward_target(self) -> dict[str, str | None]:
        if (session := self.app_steward_session()) is None:
            return {"binding_id": None, "session_id": None}
        return {"binding_id": _need(_as_dict(session["steward_context"])["binding_id"], str), "session_id": _need(session["session_id"], str)}

    def app_steward_session(self) -> JsonDict | None:
        """Read the Session within the selected source without starting work."""
        current = self.read()
        if (selected := current.get("app_steward_binding_id")) is None:
            # Validate legacy state through the same pure owner plan. An
            # unreadable grant must not masquerade as an unselected entry.
            self._core("collaboration.conversation.binding", {"current": current, "expected_revision": current.get("revision"), "operation": "select_app_steward", "binding_id": None})
            return None
        current, sessions, _ = self._observe_local_sessions(_need(selected, str), app_steward_entry=True)
        if not sessions:
            raise ValueError("the original steward Session is unavailable")
        session = max(sessions, key=lambda item: (str(item.get("last_activity_at") or ""), _need(item["session_id"], str)))
        # Close uses the same Session fences. Keep it from committing between
        # this read and the final grant decision; grant revocation still races
        # freely and is detected by the version check. All provider/Core IO has
        # already completed, and background grant inspection remains nonblocking.
        with self._require_controller().store.read_session_fenced(_need(session["session_id"], str)) as fresh:
            if fresh is None or fresh.get("status") == "closed" or self._session_observation(fresh) != {**self._session_observation(session), "status": fresh.get("status")}:
                raise ValueError("the original steward Session is unavailable")
            self._recheck_local_bindings(current)
            return fresh

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
        if (controller := self.controller) is None:
            return
        failures: list[Exception] = []
        for session, turn in _iter_open_session_turns(controller.store):
            if not _session_mentions_binding(session, binding_id) and _dict_or_empty(_dict_or_empty(turn.get("external_agent_target")).get("context")).get("binding_id") != binding_id:
                continue
            try:
                if controller.interrupt_turn(session_id=_need(session["session_id"], str), turn_id=_need(turn["turn_id"], str)).get("status") not in TERMINAL_TURN_STATES:
                    raise RuntimeError("Turn stop did not settle")
            except Exception as exc:
                # Revocation remains published even when a host lacks stop
                # control. Continue stopping other exact targets, then report
                # failure without exposing private transport diagnostics.
                failures.append(exc)
        if failures:
            raise OSError("Conversation grant revoked; execution stop is not confirmed. Retry stop for this binding.") from failures[0]

    def resolve(self, *, binding_id: str, source_ref: str, sender_ref: str, private_human_message: bool,
                session_context: JsonMap | None = None, group_ref: str | None = None, topic_ref: str | None = None,
                group_human_message: bool = False, include_observation: bool = False, origin: str | None = None, session: JsonMap | None = None) -> JsonDict:
        if (row := _find_binding(self.read(), "binding_id", binding_id, default_empty=True)) is None:
            raise ValueError("conversation binding is no longer authorized")
        observation = dict(self.observe(_need(row["transport_ref"], str)))
        # Provider verification can wait on IO. It cannot authorize a binding
        # that was revoked or replaced while that observation was in flight.
        # Unrelated binding changes do not invalidate this exact source.
        current = self._verify_unchanged("binding_id", binding_id, row, default_empty=True)
        selected = self._resolve_observed(current, row, observation, {
            "source_ref": source_ref, "sender_ref": sender_ref, "private_human_message": private_human_message,
            **({"origin": origin} if origin is not None else {}), **({"session_context": session_context} if session_context is not None else {}),
            **({"session": self._session_observation(session)} if session is not None else {}),
            **({"group_ref": group_ref, "topic_ref": topic_ref, "group_human_message": group_human_message} if group_ref is not None or topic_ref is not None or group_human_message else {}),
        })
        # Presentation may need the name from this exact verified read. Keep it
        # outside the persisted context and opt-in; it is never a cached grant
        # or a substitute for the next admission/delivery owner's fresh check.
        return {**selected, "observation": observation} if include_observation else selected

    def _resolve_observed(self, current: JsonMap, row: JsonMap, observation: JsonMap, source: JsonMap) -> JsonDict:
        return self._core("collaboration.conversation.bound_context", {
            "current": current, "binding_id": row["binding_id"], "observation": observation, "available_projects": self._require_projects().available(), **source,
            **({"available_goal_ids": self.goal_scope_ids(row)} if row.get("goal_scope") == "all_registered" else {}),
        })

    @contextmanager
    def input_context(self, saved: JsonMap, *, origin: str, session: JsonMap, store: ChatSessionStore) -> Iterator[JsonDict]:
        """Observe outside the publication fence, then retain it through enqueue.

        Saved fields select an identity; fresh Core checks grant it. Local Web
        continuation keeps its actual origin and never becomes a provider event.
        """
        if origin == "web":
            source: JsonDict = {"source_ref": saved["source_ref"], "sender_ref": saved["operator_ref"], "private_human_message": False, "origin": "web", "session_context": saved}
        elif origin in {"lark", "external"}:
            source = self._session_source(saved)
        else:
            raise ValueError("unsupported bound conversation input origin")
        binding_id = _need(saved["binding_id"], str)
        observed = self._resolve_source(binding_id, source, include_observation=True, session=session)
        with exclusive_file_lock(self.path, operation="admit_bound_input"):
            current = self._verify_unchanged("binding_id", binding_id, _as_dict(observed["binding"]), message="conversation binding changed before input admission", default_empty=True)
            # The provider observation may have waited. Re-read canonical
            # identity under the binding fence before Core authorizes input.
            if (fresh := store.load_session(_need(session["session_id"], str))) is None:
                raise KeyError("chat session was not found")
            yield self._resolve_observed(current, _as_dict(observed["binding"]), _as_dict(observed["observation"]), {**source, "session": self._session_observation(fresh)})

    @staticmethod
    def _session_observation(session: JsonMap) -> JsonDict:
        return {key: session.get(key) for key in ("session_id", "goal_id", "agent_id", "executor_endpoint_id", "channel_id",
                                                  "session_mode", "status", "project_context", "steward_context", "manager_executor_allocation", "upstream_context_history")}

    def local_sessions(self, binding_id: str, *, app_steward_entry: bool = False) -> list[JsonDict]:
        return cast("list[JsonDict]", self.local_session_snapshot(binding_id, app_steward_entry=app_steward_entry)["sessions"])

    def local_session_snapshot(self, binding_id: str, *, app_steward_entry: bool = False) -> JsonDict:
        current, sessions, source = self._observe_local_sessions(binding_id, app_steward_entry=app_steward_entry)
        store = self._require_controller().store
        payload: JsonDict = {"sessions": [store.public_session(dict(s)) for s in sessions], "conversation_source": source}
        self._recheck_local_bindings(current)
        return payload

    def _recheck_local_bindings(self, observed: JsonMap) -> None:
        if self.read() != observed:
            raise ValueError("conversation binding changed during discovery")

    def _observe_local_sessions(self, binding_id: str, *, app_steward_entry: bool) -> tuple[JsonDict, list[JsonDict], JsonDict]:
        """Read original targets; metadata hints never replace Core's grant.

        This read-only snapshot does not wait for a mutation fence. Core checks
        the observed grant, and a final canonical read rejects concurrent grant
        changes. actual
        submissions retain their fresh identity and effect-time grant fences.
        """
        store = self._require_controller().store
        row = _require_binding(self.read(), "binding_id", binding_id)
        observation = dict(self.observe(_need(row["transport_ref"], str)))
        # Keep aliases in the candidate set even when their own metadata is
        # unrelated. Core must still see duplicate canonical Session identities.
        hints = (s for path, s in store.iter_session_metadata() if path.parent.name != s["session_id"] or _session_mentions_binding(s, binding_id))
        sessions = [s for h in sorted(hints, key=lambda item: str(item.get("updated_at") or ""), reverse=True)
                    if (s := store.load_session(_need(h["session_id"], str))) and _session_mentions_binding(s, binding_id)]
        current, projects = self._verify_unchanged("binding_id", binding_id, row), self._require_projects().available()
        result = self._core("collaboration.conversation.local_sessions", {
            "current": current, "binding_id": binding_id, "observation": observation, "available_projects": projects,
            "sessions": [self._session_observation(s) for s in sessions], **({"app_steward_entry": True} if app_steward_entry else {}),
            **({"available_goal_ids": self.goal_scope_ids(row)} if row.get("goal_scope") == "all_registered" else {}),
        })
        # Build the lookup only after Core has rejected duplicate identity;
        # an IO dictionary must not silently turn ambiguity into one target.
        by_id = {_need(s["session_id"], str): s for s in sessions}
        project = next(p for p in projects if p["project_ref"] == row["project_ref"])
        source: JsonDict = {"binding_id": binding_id, "app_ref": row["transport_ref"], "context_kind": row["context_kind"], "audience": row.get("audience", "owner"),
                            "project_title": Path(_need(project["workspace_path"], str)).name, "context_available": True, "executor_endpoint_id": row["executor_endpoint_id"]}
        # Bind the projection to the exact atomic configuration Core checked.
        # This is not an execution grant or a cached provider observation.
        return current, [by_id[_need(sid, str)] for sid in _need(result["session_ids"], list)], source

    def goal_scope_ids(self, binding: JsonMap) -> list[str]:
        if binding.get("goal_scope") != "all_registered":
            return [_need(item, str) for item in raw_ids] if isinstance(raw_ids := binding.get("goal_ids", []), list) else []
        from ...control_plane.projects.registry_codec import load_registry
        if self.controller is None or self.controller.registry_path is None:
            raise ValueError("the steward registry is unavailable")
        return [_need(goal["id"], str) for goal in load_registry(self.controller.registry_path).get("goals", []) if isinstance(goal, dict)]

    def delivery_channels(self, binding: JsonMap) -> list[str]:
        if self.controller is None:
            return []
        from ..manager_context import POLICY_SCHEMA, _root
        if not (path := _root(self.controller.coordination_runtime_root) / "policy.json").exists():
            return []
        policy = _read_json(path)
        if policy.get("schema_version") != POLICY_SCHEMA or not isinstance(sources := policy.get("sources"), dict):
            raise ValueError("invalid manager policy")
        prefix = f"manager.external.native.{binding['binding_id']}."
        return [str(ch) for ch, src in sources.items() if str(ch).startswith(prefix) and isinstance(src, dict) and binding["operator_ref"] in (src.get("sender_ids") or [])]

    def ensure_delivery_scope(self, selected: JsonMap, *, if_absent: bool = True, execute: bool = True) -> None:
        if (binding := _as_dict(selected["binding"])).get("goal_scope") not in {"all_registered", "selected"} or self.controller is None:
            return
        # Match configuration's binding -> source-policy order. An observation
        # from before disconnect must never recreate its retired source grant.
        with exclusive_file_lock(self.path, operation="configure_bound_delivery_scope"):
            if binding not in _dict_list(self.read()["bindings"]):
                raise ValueError("conversation binding is no longer authorized")
            self._ensure_delivery_scope(selected, if_absent=if_absent, execute=execute)

    def _ensure_delivery_scope(self, selected: JsonMap, *, if_absent: bool = True, execute: bool = True) -> None:
        """The caller retains the binding publication fence, including configure."""
        binding = _as_dict(selected["binding"])
        if (goal_scope := binding.get("goal_scope")) not in {"all_registered", "selected"} or self.controller is None:
            return
        from ..manager_context import configure_delivery_scope
        configure_delivery_scope(self.controller.coordination_runtime_root, channel=_need(selected["channel_id"], str),
                                 local_delivery_scope=_need(goal_scope, str), sender_id=_need(binding["operator_ref"], str), execute=execute, if_absent=if_absent)

    @staticmethod
    def _session_source(saved: JsonMap) -> JsonDict:
        base: JsonDict = {"source_ref": saved["source_ref"], "sender_ref": saved["operator_ref"], "private_human_message": True, "session_context": saved}
        if saved.get("audience") == "bound_group":
            return {**base, "private_human_message": False, "group_ref": saved["group_ref"], "topic_ref": saved["topic_ref"], "group_human_message": True}
        return base

    def session_context(self, saved: JsonMap, *, session: JsonMap | None = None) -> JsonDict:
        return self._resolve_source(_need(saved["binding_id"], str), self._session_source(saved), session=session)

    def manager_runtime_owner(self, saved: JsonMap, channel_id: str) -> bool:
        """Fresh Core audience proof; saved Session text is never authority."""
        if (selected := self.session_context(saved))["channel_id"] != channel_id:
            raise ValueError("bound manager audience changed")
        return selected["owner_manager_audience"] is True

    def steward_scope(self, session: JsonMap) -> list[str] | None:
        if not isinstance(saved := session.get("steward_context"), dict):
            return None
        selected = self.session_context(saved)
        if session.get("goal_id") != "loopx-manager" or session.get("channel_id") != selected["channel_id"]:
            raise ValueError("bound steward audience changed")
        goal_ids = _as_dict(selected["context"])["goal_ids"]
        return [_need(item, str) for item in goal_ids] if isinstance(goal_ids, list) else []

    def adopt_created_goal(self, *, binding_id: str, source: JsonMap, proposal: JsonMap, goal: JsonMap) -> None:
        selected = self._resolve_source(binding_id, source)
        with exclusive_file_lock(self.path, operation="adopt_steward_created_goal"):
            current = self.read()
            result = self._core("collaboration.conversation.binding", {
                "current": current, "expected_revision": current["revision"], "operation": "adopt_created_goal",
                "binding_id": binding_id, "context": selected["context"], "proposal": proposal,
                "goal": {"goal_id": goal["id"], "workspace_path": str(Path(_need(goal["repo"], str)).resolve()), "creation_operation_id": goal.get("creation_operation_id")},
            })
            self._publish_state(result, error_message="steward scope publication did not verify")

    def agent_observation(self, session_id: str) -> _AgentObservation:
        """Read only the explicitly selected canonical attached Session/Goal.

        No host history, remote thread database or prior identity is discovered.
        """
        from ... import attached_session as _attached, registry as _registry
        from ...control_plane.projects.registry_codec import load_registry

        if self.controller is None or self.controller.registry_path is None:
            raise ValueError("the attached Session registry is unavailable")
        store = self.controller.store
        session = _attached.require_current_attached_session(store=store, registry_path=self.controller.registry_path, session_id=session_id)
        goal = _registry.find_registry_goal(load_registry(self.controller.registry_path), str(session.get("goal_id") or ""))
        if goal is None or not goal.get("repo"):
            raise ValueError("the exact Agent workspace is unavailable")
        _attached._require_bound_host(goal=goal, agent_id=session["agent_id"], host_surface=session["host_surface"], host_session_id=session["upstream_thread_id"])
        host = _host_observation_fields(store, session, goal)
        keys = ("session_id", "goal_id", "goal_instance_id", "agent_id", "executor_endpoint_id", "session_mode", "status", "external_conversation_binding_id")
        return {"session": {k: session.get(k) for k in keys}, "goal": _as_dict(host["goal"]), "host_binding_verified": True,
                "host_ref": _need(host["host_ref"], str), "host_audience_binding_ids": [_need(i, str) for i in cast("list[object]", host["host_audience_binding_ids"])]}

    def agent_candidates(self, binding_id: str) -> list[JsonDict]:
        row = _require_binding(self.read(), "binding_id", binding_id)
        if row["context_kind"] != "project" or row.get("audience") == "group" or self.controller is None:
            return []
        if (project := next((item for item in self._require_projects().available() if item["project_ref"] == row["project_ref"]), None)) is None:
            return []
        candidates: list[JsonDict] = []
        for session in self.controller.store.list_sessions():
            if session.get("session_mode") != "attached_host" or session.get("status") == "closed":
                continue
            try:
                observed = self.agent_observation(_need(session["session_id"], str))
                if (observed["session"]["status"] in {"ready", "busy"} and observed["goal"]["workspace_path"] == project["workspace_path"]
                        and observed["session"].get("external_conversation_binding_id") in {None, binding_id}
                        and all(owner == binding_id for owner in observed["host_audience_binding_ids"])):
                    candidates.append(observed["session"])
            except (ValueError, OSError, KeyError):
                continue
        return candidates[:16]

    def change_agent_target(self, *, binding_id: str, expected_revision: int, session_id: str | None = None, target_ref: str | None = None) -> JsonDict:
        row = _require_binding(self.read(), "binding_id", binding_id)
        # Provider IO must not prevent disconnect from publishing revocation.
        # Recheck the exact binding under its fence before using this observation.
        observation = self.observe(_need(row["transport_ref"], str))
        with exclusive_file_lock(self.path, operation="configure_conversation_agent_target"):
            params: JsonDict = {"current": self._verify_unchanged("binding_id", binding_id, row), "expected_revision": expected_revision,
                                "binding_id": binding_id, "observation": observation, "available_projects": self._require_projects().available()}
            if session_id is not None:
                observed = self.agent_observation(session_id)
                target = {k: observed["session"].get(k) for k in ("session_id", "goal_id", "goal_instance_id", "agent_id", "executor_endpoint_id")}
                params.update(operation="grant_agent_target", target_observation=observed, target={"target_ref": uuid.uuid4().hex[:24], "host_ref": observed["host_ref"], **target})
            else:
                params.update(operation="revoke_agent_target", target_ref=target_ref)
            result = self._core("collaboration.conversation.binding", params)
            if result["changed"] and session_id is not None:
                # agent_observation already requires this exact local controller.
                assert self.controller is not None
                self.controller.store.update_session(session_id, external_conversation_binding_id=binding_id)
            return self._publish_state(result, error_message="Agent target grant publication did not verify")

    def resolve_agent_target(self, selected: JsonMap, target: JsonMap) -> JsonDict:
        return self._core("collaboration.conversation.agent_target", {
            "binding": selected["binding"], "context": selected["context"], "target": target,
            "target_observation": self.agent_observation(_need(target["session_id"], str)),
        })

    @effect_runtime_request_scope()
    def stop_recoveries(self, current: JsonMap) -> list[JsonDict]:
        """Project revoked execution from canonical Turns, never recreate a grant.

        Session and external-target context retain the exact binding identity
        across restart. No second stop ledger or provider diagnostics are needed.
        A recovery disappears only when the existing Turn owner settles it.
        Share the runtime source observation, while reading each queue afresh.
        """
        if self.controller is None:
            return []
        authorized = {row["binding_id"] for row in _dict_list(current["bindings"])}
        counts: dict[str, int] = {}
        project_refs: dict[str, str] = {}
        for session, turn in _iter_open_session_turns(self.controller.store):
            context = _dict_or_empty(_dict_or_empty(turn.get("external_agent_target")).get("context")) or _dict_or_empty(session.get("project_context")) or _dict_or_empty(session.get("steward_context"))
            if not isinstance(binding_id := context.get("binding_id"), str) or not binding_id or binding_id in authorized:
                continue
            project_refs.setdefault(binding_id, str(context.get("project_ref") or ""))
            counts[binding_id] = counts.get(binding_id, 0) + 1
        return [{"binding_id": bid, "project_ref": project_refs[bid], "pending_count": counts[bid]} for bid in sorted(counts)]


def validate_external_agent_turn(*, store: ChatSessionStore, session: JsonMap, goal: JsonMap | None, turn: JsonMap) -> None:
    """Called under the grant and canonical queue fences before host claim.

    The owner-local grant persists independently of a live provider connection.
    Admission and result delivery also verify the App/owner with the provider.
    """
    if (saved := turn.get("external_agent_target")) is None:
        return
    from ...attached_session import _require_bound_host
    if not isinstance(saved, dict) or turn.get("origin") not in {"lark", "external"} or goal is None:
        raise ValueError("the attached external audience is unavailable")
    context, target = _as_dict(saved["context"]), saved["target"]
    if not Path(_need(goal["repo"], str)).is_dir():
        raise ValueError("the attached Agent workspace is unavailable")
    if (selected := _find_binding(_read_json(store.root / "conversation-bindings.json"), "binding_id", context["binding_id"])) is None:
        raise ValueError("the Agent audience grant was revoked")
    _require_bound_host(goal=dict(goal), agent_id=_need(session["agent_id"], str),
                        host_surface=_need(session["host_surface"], str), host_session_id=_need(session["upstream_thread_id"], str))
    ChatConversationBindings._core("collaboration.conversation.agent_target", {
        "binding": selected, "context": context, "target": target,
        "target_observation": {"session": session, **_host_observation_fields(store, session, goal)},
    })


def _host_ref(session: JsonMap) -> str:
    return hashlib.sha256(f"{session['host_surface']}\0{session['upstream_thread_id']}".encode()).hexdigest()[:24]


def _host_audiences(store: ChatSessionStore, session: JsonMap) -> list[str]:
    """Content-free stamps in this canonical store, including closed Sessions."""
    return sorted({
        candidate["external_conversation_binding_id"]
        for row in store.list_sessions()
        if (candidate := store.load_session(_need(row["session_id"], str)))
        and candidate.get("host_surface") == session["host_surface"]
        and candidate.get("upstream_thread_id") == session["upstream_thread_id"]
        and isinstance(candidate.get("external_conversation_binding_id"), str) and candidate["external_conversation_binding_id"]
    })


class OriginalConversationRequests:
    """Chat/source/store IO for the shared original-conversation request profile.

    The existing source policy, peer operation, request, revision, result and
    Session FIFO remain their respective owners. No shadow queue is created.
    """

    def __init__(self, controller: _BindingController) -> None:
        self.controller, self.store, self.root = controller, controller.store, controller.coordination_runtime_root
        if (bindings := controller.project_contexts.conversation_bindings) is None:
            raise ValueError("original conversation bindings are unavailable")
        self.bindings = bindings

    _core = staticmethod(ChatConversationBindings._core)

    def _path(self, *parts: str) -> Path:
        return inbox._root(self.root).joinpath(*parts)

    def _entry_path(self, target: JsonMap, request_id: str) -> Path:
        return self._path("entries", inbox._hash(dict(target)), f"{request_id}.json")

    def _lock_request(self, request_id: str, legacy_path: Path | None = None) -> AbstractContextManager[None]:
        return cast("_RequestLockFn", inbox._request_lock)(self.root, request_id, None, legacy_path or Path(), force_exact=True)

    @staticmethod
    def _write_verified(path: Path, payload: JsonMap, error_message: str, *, write: bool = True) -> None:
        if write:
            inbox._write(path, dict(payload))
        if inbox._read(path) != payload:
            raise OSError(error_message)

    def _policy(self) -> JsonDict:
        return cast("JsonDict", inbox._read(path)) if (path := self._path("policy.json")).exists() else {"schema_version": "loopx_manager_context_policy_v1", "sources": {}}

    def _observation(self, session_id: str) -> tuple[JsonDict, JsonDict]:
        if (session := self.store.load_session(session_id)) is None:
            raise ValueError("original project Session is unavailable")
        if (binding := _find_binding(self.bindings.read(), "binding_id", _dict_or_empty(session.get("project_context")).get("binding_id"))) is None:
            raise ValueError("original conversation grant was revoked")
        return binding, dict(self.bindings.observe(_need(binding["transport_ref"], str)))

    def _recipient(self, session_id: str, expected_binding: JsonMap, observation: JsonMap, policy: JsonMap) -> JsonDict:
        current = self.bindings._verify_unchanged("binding_id", expected_binding["binding_id"], expected_binding, message="original conversation binding changed during verification")
        return self._core("collaboration.project.recipient", {
            "current": current, "binding_id": expected_binding["binding_id"], "observation": observation,
            "available_projects": self.bindings._require_projects().available(), "session": self.store.load_session(session_id), "policy": policy,
            "sessions": [self.store.load_session(_need(row["session_id"], str)) for row in self.store.list_sessions()],
        })

    @contextmanager
    def _source(self, requester: JsonMap, parent_request_id: str, *, require_active: bool = True) -> Iterator[tuple[JsonDict, CollaborationGoalScope]]:
        from ...control_plane.collaboration.peer_context_observation import require_parent_context_access

        if (registry_path := self.controller.registry_path) is None:
            raise ValueError("the steward registry is unavailable")
        ref, agent_id = requester.get("goal_ref"), requester.get("agent_id")
        if not isinstance(ref, Mapping) or not isinstance(ref.get("goal_id"), str) or not isinstance(agent_id, str) or not all(isinstance(k, str) and isinstance(v, str) for k, v in ref.items()):
            raise ValueError("an exact original requester is required")
        with collaboration_goal_scope(registry_path, goal_id=ref["goal_id"], agents=(agent_id,), caller_goal_ref={str(k): str(v) for k, v in ref.items()}, require_active=require_active) as scope:
            if not scope.exact or requester != {"goal_ref": scope.caller_goal_ref, "agent_id": agent_id}:
                raise ValueError("an exact original requester is required")
            parent = inbox._entry(self.root, scope.goal_id, agent_id, parent_request_id, scope=scope)
            if "request_control" in parent and self._view(parent)["control_state"] in {"cancelled", "cancellation_pending"}:
                raise ValueError("the original source request was cancelled")
            access = require_parent_context_access(self.root, registry_path, parent, agent_id, scope=scope)
            yield {"requester": requester, "parent": parent, "parent_request_id": parent_request_id, "original_source_id": access["source_id"], "parent_access_verified": True}, scope

    @contextmanager
    def _fenced(self, session_id: str, requester: JsonMap, parent_request_id: str) -> Iterator[tuple[JsonDict, CollaborationGoalScope]]:
        binding, observed = self._observation(session_id)
        # Every writer uses source -> Goal -> binding -> request -> Session.
        # Provider IO precedes these fences; canonical identity is reread inside.
        with source_policy_fence(self._path("policy.json")), self._source(requester, parent_request_id) as (source, scope), exclusive_file_lock(self.bindings.path, operation="original_conversation_peer"):
            policy = self._policy()
            selected = self._recipient(session_id, binding, observed, policy)
            yield {**source, "policy": policy, "recipient": selected["recipient"], "current_recipient": selected["recipient"], "policy_sha256": selected["policy_sha256"]}, scope

    def configure(self, session_id: str, body: JsonMap) -> JsonDict:
        if not isinstance(enable := body.get("grant"), bool):
            raise ValueError("grant must be an explicit boolean")
        execute = body.get("execute") is True

        def apply(facts: JsonMap) -> JsonDict:
            plan = self._core("collaboration.source.original_conversation_grant", {**facts, "grant": enable, "expected_policy_sha256": body.get("expected_policy_sha256")})
            if execute and plan["would_change"]:
                self._write_verified(self._path("policy.json"), _as_dict(plan["policy"]), "original conversation source grant did not verify")
            return {"ok": True, "executed": execute, **{k: v for k, v in plan.items() if k != "policy"}}

        requester, parent_request_id = _as_dict(body["requester"]), _need(body["parent_request_id"], str)
        if enable:
            with self._fenced(session_id, requester, parent_request_id) as (facts, _):
                return apply(facts)
        # An owner may revoke a retained grant even while its provider or source
        # is unavailable.
        with source_policy_fence(self._path("policy.json")):
            if (recipient := _as_dict(body["recipient"])).get("session_id") != session_id:
                raise ValueError("original conversation revocation scope mismatch")
            return apply({"policy": self._policy(), "recipient": recipient, "requester": requester, "parent_request_id": parent_request_id, "original_source_id": body["source_id"]})

    def management_view(self, session_id: str, *, search: str = "", cursor: str | None = None) -> JsonDict:
        with source_policy_fence(self._path("policy.json")):
            policy = self._policy()
            view = self._core("collaboration.source.original_conversation_grants", {"policy": policy, "session_id": session_id})
        for grant in _dict_list(view["grants"]):
            requester = _as_dict(grant["requester"])
            try:
                parent = inbox._read(self._entry_path({"goal_ref": requester["goal_ref"], "agent_id": requester["agent_id"]}, _need(grant["parent_request_id"], str)))
                if parent.get("goal_ref") != requester["goal_ref"] or parent.get("agent_id") != requester["agent_id"]:
                    raise ValueError("source grant parent identity changed")
                grant["source_purpose"] = _dict_or_empty(parent.get("brief")).get("purpose", parent.get("message", ""))
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
        for path in sorted(self._path("entries").glob("*/*.json")):
            try:
                row = inbox._read(path)
                if row.get("schema_version") != "loopx_manager_context_entry_v2" or not row.get("goal_ref"):
                    continue
                purpose = str(_dict_or_empty(row.get("brief")).get("purpose", row.get("message", "")))
                if search.strip().casefold() not in " ".join((str(row["goal_ref"]["goal_id"]), str(row["agent_id"]), purpose)).casefold():
                    continue
                candidates.append({"requester": {"goal_ref": row["goal_ref"], "agent_id": row["agent_id"]}, "parent_request_id": row["request_id"], "purpose": purpose})
                if len(candidates) == 128:
                    overflow = True
                    break
            except (ValueError, OSError, KeyError, TypeError):
                unreadable += 1
        view.update(candidates=candidates, coverage={"unreadable": unreadable, "overflow": overflow})
        page = self._inbox(_as_dict(view["recipient"]), mark_read=False, cursor=cursor)
        requests = _dict_list(page["items"])
        view["requests"], view["requests_has_more"], view["requests_next_cursor"] = requests, page["has_more"], page["next_cursor"]
        for item in requests:
            request_id = _need(item["request_id"], str)
            entry = self._entry(request_id, _as_dict(item["recipient"]))
            statuses = {str(r.get("result_key", r["phase"])): r["status"] for r in cast("Callable[[Path, JsonMap], list[JsonDict]]", inbox.reply_status)(self.root, item)}
            turn = self.store.turn_for_client(session_id, _need(self._core("collaboration.project.continuation", {"entry": entry})["client_turn_id"], str))
            item["queue_status"] = turn.get("status") if turn else None
            item["returns"] = [
                {"result_key": path.stem, "text": reply["text"], "decision": reply["decision"],
                 "request_revision": reply.get("request_revision", 1), "delivery_status": statuses.get(path.stem, "queued")}
                for path in cast("Callable[[Path], list[Path]]", inbox.result_paths)(self._path("replies", request_id))
                if cast("Callable[[JsonMap, JsonMap, Path], object]", inbox.inspect_result_identity)(reply := inbox._read(path), entry, path)
            ]
        return {"ok": True, **view}

    def discover(self, requester: JsonMap, parent_request_id: str) -> JsonDict:
        with source_policy_fence(self._path("policy.json")), self._source(requester, parent_request_id):
            grants = _dict_list(self._core("collaboration.source.original_conversation_grants", {"policy": self._policy()})["grants"])
        recipients, unavailable = [], 0
        for grant in grants:
            if grant["requester"] != requester or grant["parent_request_id"] != parent_request_id:
                continue
            try:
                grant_recipient = _as_dict(grant["recipient"])
                with self._fenced(_need(grant_recipient["session_id"], str), requester, parent_request_id) as (facts, _):
                    self._core("collaboration.source.original_conversation_access", facts)
                    if facts["recipient"] != grant_recipient:
                        raise ValueError("original conversation recipient changed")
                    facts_recipient = _as_dict(facts["recipient"])
                    session = self.store.load_session(_need(facts_recipient["session_id"], str))
                    recipients.append({"recipient": facts_recipient, "title": (session or {}).get("title") or "Project conversation"})
            except (ValueError, KeyError, OSError):
                unavailable += 1
        return {"ok": True, "recipients": recipients, "unavailable": unavailable}

    def publish(self, body: JsonMap) -> JsonDict:
        from ...control_plane.collaboration.peers import _operation_address

        recipient, requester = _as_dict(body["recipient"]), _as_dict(body["requester"])
        session_id = _need(recipient["session_id"], str)
        with self._fenced(session_id, requester, _need(body["parent_request_id"], str)) as (facts, scope):
            if recipient != facts["recipient"]:
                raise ValueError("original conversation recipient identity changed")
            source_id, op_path = _operation_address(self.root, scope, _need(requester["agent_id"], str), _need(body["operation_id"], str))
            # Native planning owns identity and replay. The old operation is
            # consulted before any queue input or return route can be written.
            prior = inbox._read(op_path) if op_path.exists() else None
            req_payload: JsonDict = {**facts, "source_id": source_id, "request": {"recipient": recipient, "brief": body["brief"]}}
            request_id = _need(_as_dict(self._core("collaboration.project.request", {**req_payload, "observed_at": inbox._now()})["entry"])["request_id"], str)
            path = self._entry_path({"recipient": recipient}, request_id)
            with self._lock_request(request_id, op_path):
                prior = inbox._read(op_path) if op_path.exists() else prior
                plan = self._core("collaboration.project.request", {**req_payload, "observed_at": inbox._now(), "operation": prior, "entry": inbox._read(path) if path.exists() else None})
                row, route, route_path = _as_dict(plan["entry"]), _as_dict(plan["return_route"]), self._path("roundtrips", f"{request_id}.json")
                if route_path.exists() and inbox._read(route_path) != route:
                    raise ValueError("original requester return route identity conflict")
                if prior is None:
                    inbox._write(op_path, _as_dict(plan["operation"]))
                self._write_verified(route_path, route, "original requester return route did not verify", write=not route_path.exists())
                self._write_verified(path, row, "original conversation request did not verify", write=not plan["replayed"])
                if self._view(row)["control_state"] == "cancelled":
                    raise ValueError("original conversation request is cancelled")
                turn, created = self._enqueue(row)
        return {"ok": True, "request_id": request_id, "recipient": recipient, "turn_id": turn["turn_id"], "status": turn["status"],
                "replayed": not created, "goal_created": False, "recovered_result": self._resume(session_id, turn, created)}

    def _enqueue(self, row: JsonMap) -> tuple[JsonDict, bool]:
        queue = self._core("collaboration.project.continuation", {"entry": row})
        return self.store.create_queued_turn(_need(_as_dict(row["recipient"])["session_id"], str), client_turn_id=_need(queue["client_turn_id"], str),
                                             message=_need(queue["message"], str), origin="peer", collaboration_request=_as_dict(queue["collaboration_request"]))

    def _stop_turns(self, session_id: str, client_ids: Sequence[str]) -> JsonDict | None:
        stopped: JsonDict | None = None
        for client_id in client_ids:
            if (turn := self.store.turn_for_client(session_id, client_id)) and turn["status"] not in TERMINAL_TURN_STATES:
                stopped = self.controller.interrupt_turn(session_id=session_id, turn_id=_need(turn["turn_id"], str))
        return stopped

    def _resume(self, session_id: str, turn: JsonMap, created: bool) -> JsonDict | None:
        recovered = _as_dict(self._complete_answer(session_id, turn, _as_dict(answer))["collaboration_result"]) if not created and (answer := turn.get("collaboration_answer")) is not None else None
        if turn["status"] not in TERMINAL_TURN_STATES:
            if (session := self.store.load_session(session_id)) is None:
                raise ValueError("original project Session is unavailable")
            context = self.controller.project_contexts.session_context(session)
            project = context["project"]
            self.controller.resume_session_queue(session_id=session_id, work_dir=project if isinstance(project, Path) else Path(_need(project, str)), objective=str(context.get("objective") or context["title"]))
        return recovered

    def _entry(self, request_id: str, recipient: JsonMap) -> JsonDict:
        return inbox._entry(self.root, None, None, request_id, recipient=dict(recipient))

    def _view(self, row: JsonMap) -> JsonDict:
        return _as_dict(self._core("collaboration.request.control", {"entry": row, "input": {"operation": "inspect"}, "source": {}})["view"])

    def _inbox(self, recipient: JsonMap, *, mark_read: bool, request_id: str | None = None, cursor: str | None = None) -> JsonDict:
        return _as_dict(self._core("collaboration.inbox.read", {
            "runtime_root": str(self.root.resolve()), "scope": {"profile_id": "original_conversation_v0", "recipient": recipient},
            "operation": "receiver_read" if mark_read else "pending", "request_id": request_id, "include_settled": not mark_read,
            "cursor": cursor, "observed_at": inbox._now(), "read_command": "original project Turn supplied context",
        }, large_local_snapshot=True)["result"])

    def control(self, body: JsonMap) -> JsonDict:
        requester, recipient = _as_dict(body["requester"]), _as_dict(body["recipient"])
        operation, parameters = body["control"], body["parameters"]
        if operation not in {"inspect", "revise", "cancel"} or not isinstance(parameters, dict):
            raise ValueError("unsupported original conversation source control")
        row = self._entry(_need(body["request_id"], str), recipient)
        if row["requester"] != requester:
            raise ValueError("original requester identity changed")
        request_id, session_id = _need(row["request_id"], str), _need(recipient["session_id"], str)

        @contextmanager
        def retained_source() -> Iterator[tuple[JsonDict, CollaborationGoalScope]]:
            if (registry_path := self.controller.registry_path) is None:
                raise ValueError("the steward registry is unavailable")
            goal_ref = _as_dict(requester["goal_ref"])
            with source_policy_fence(self._path("policy.json")), collaboration_goal_scope(
                registry_path, goal_id=_need(goal_ref["goal_id"], str), agents=(_need(requester["agent_id"], str),), caller_goal_ref={k: _need(v, str) for k, v in goal_ref.items()},
            ) as scope:
                if not scope.exact:
                    raise ValueError("original requester exact instance is unavailable")
                yield {}, scope

        # Revocation or a closed recipient must not remove the original
        # source's ability to inspect/cancel its retained request.
        fence = self._fenced(session_id, requester, _need(row["parent_request_id"], str)) if operation == "revise" else retained_source()
        with fence as (facts, _), self._lock_request(request_id):
            current = self._entry(request_id, recipient)
            self._core("collaboration.project.return_route", {"entry": current, "route": inbox._read(self._path("roundtrips", f"{request_id}.json"))})
            if operation == "revise":
                if recipient != facts["recipient"]:
                    raise ValueError("original conversation recipient changed")
                self._core("collaboration.source.original_conversation_access", facts)
            plan = self._core("collaboration.request.control", {
                "entry": current, "input": {**_as_dict(parameters), "operation": operation},
                "source": {"actor": "source", "actor_agent_id": requester["agent_id"], "actor_requester": requester, "recipient_active": operation == "revise", "observed_at": inbox._now()},
            })
            if plan["changed"]:
                inbox._write(self._entry_path({"recipient": recipient}, request_id), _as_dict(plan["entry"]))
            if operation == "revise":
                turn, created = self._enqueue(_as_dict(plan["entry"]))
        stop: JsonDict | None = None
        if operation == "revise":
            self._resume(session_id, turn, created)
        elif operation == "cancel":
            queue = self._core("collaboration.project.continuation", {"entry": plan["entry"]})
            superseded = queue["superseded_client_turn_ids"]
            client_ids = [*(_need(cid, str) for cid in superseded)] if isinstance(superseded, list) else []
            stop = self._stop_turns(session_id, [*client_ids, _need(queue["client_turn_id"], str)])
        return {"ok": True, "request_control": plan["view"], "replayed": plan.get("replayed", False), "turn_stop": stop}

    def validate_turn(self, session_id: str, turn: JsonMap) -> None:
        if turn.get("origin") != "peer" and "collaboration_request" not in turn:
            return
        if turn.get("origin") != "peer" or not isinstance(reference := turn.get("collaboration_request"), dict):
            raise ValueError("peer Turn provenance is unavailable")
        row = self._entry(_need(reference["request_id"], str), _as_dict(reference["recipient"]))
        if _as_dict(row["recipient"])["session_id"] != session_id:
            raise ValueError("peer Turn does not belong to this original Session")
        self._core("collaboration.project.continuation", {"entry": row, "turn": turn})
        with self._fenced(session_id, _as_dict(row["requester"]), _need(row["parent_request_id"], str)) as (facts, _):
            self._validate_current(row, facts)
            if self._view(row)["control_state"] == "cancelled":
                raise ValueError("original conversation request was cancelled")

    def _validate_current(self, row: JsonMap, facts: JsonMap, *, check_access: bool = False) -> None:
        self._core("collaboration.project.request", {
            **facts, "source_id": row["source_id"], "request": {"recipient": row["recipient"], "brief": row["brief"]},
            "entry": row, "operation": None, "observed_at": row["delivered_at"], "route": inbox._read(self._path("roundtrips", f"{_need(row['request_id'], str)}.json")),
        })
        if check_access:
            self._core("collaboration.source.original_conversation_access", facts)

    def _complete_answer(self, session_id: str, turn: JsonMap, answer: JsonMap) -> JsonDict:
        from ...chat import CHAT_AGENT_RESPONSE_SCHEMA_VERSION

        if not isinstance(required := self._core("collaboration.project.response_schema", {})["required"], list) or set(answer) != set(required):
            raise ValueError("original conversation answer has unsupported fields")
        ref = _as_dict(turn["collaboration_request"])
        request_id = _need(ref["request_id"], str)
        row = self._entry(request_id, _as_dict(ref["recipient"]))
        with self._fenced(session_id, _as_dict(row["requester"]), _need(row["parent_request_id"], str)) as (facts, _):
            self._validate_current(row, facts, check_access=True)
            if facts["recipient"] != row["recipient"]:
                raise ValueError("original conversation recipient changed")
            with self._lock_request(request_id):
                row = self._entry(request_id, _as_dict(row["recipient"]))
                self._core("collaboration.project.continuation", {"entry": row, "turn": turn})
                view = self._view(row)
                if view["control_state"] == "cancelled" or answer["request_revision"] != view["revision"] or answer["request_digest"] != view["revision_digest"]:
                    raise ValueError("original conversation answer belongs to a stale or cancelled request")
                plan = self._core("collaboration.request.assess", {
                    "entry": row, "actor_recipient": row["recipient"], "recipient_active": True, "observed_at": inbox._now(),
                    "revision": answer["request_revision"], "revision_digest": answer["request_digest"], "decision": answer["decision"], "reason": answer["reason"],
                })
                if plan["changed"]:
                    inbox._write(self._entry_path({"recipient": row["recipient"]}, request_id), _as_dict(plan["entry"]))
            receipt = cast("_RecordResultFn", inbox.record_result)(
                self.root, row, "conclusion", _need(answer["text"], str),
                request_revision=raw if isinstance(raw := answer["request_revision"], int) and not isinstance(raw, bool) else None,
            )
        return {"schema_version": CHAT_AGENT_RESPONSE_SCHEMA_VERSION, "message": answer["text"], "proposals": [],
                "protected_action": None, "goal_draft": None, "context_handoff": None, "gate": None, "unfinished_work": None, "collaboration_result": receipt}

    def execute(self, session_id: str, turn_id: str, native_session: _NativePeerSession, event_sink: _EventSink) -> JsonDict:
        if (turn := self.store.load_turn(session_id, turn_id)) is None:
            raise KeyError("chat turn was not found")
        self.validate_turn(session_id, turn)
        ref = _as_dict(turn["collaboration_request"])
        request_id, ref_recipient = _need(ref["request_id"], str), _as_dict(ref["recipient"])
        row = self._entry(request_id, ref_recipient)
        requester, parent_request_id = _as_dict(row["requester"]), _need(row["parent_request_id"], str)
        if (raw_answer := turn.get("collaboration_answer")) is None:
            with self._fenced(session_id, requester, parent_request_id) as (facts, _):
                self._core("collaboration.source.original_conversation_access", facts)
                self._core("collaboration.project.continuation", {"entry": self._entry(request_id, ref_recipient), "turn": turn})
                supplied = _as_dict(cast("list[object]", self._inbox(ref_recipient, mark_read=True, request_id=request_id)["items"])[0])
                basis = {key: supplied[key] for key in ("request_revision", "request_revision_digest")}

            @contextmanager
            def start_guard() -> Iterator[None]:
                with self._fenced(session_id, requester, parent_request_id) as (facts, _):
                    guarded_row = self._entry(request_id, ref_recipient)
                    self._validate_current(guarded_row, facts, check_access=True)
                    self._core("collaboration.project.continuation", {"entry": guarded_row, "turn": turn})
                    current = self._view(guarded_row)
                    if (facts["recipient"] != ref_recipient or current["control_state"] == "cancelled"
                            or current["revision"] != basis["request_revision"] or current["revision_digest"] != basis["request_revision_digest"]):
                        raise ValueError("original conversation request changed before model input")
                    yield

            answer: JsonMap = native_session.send(
                _need(row["instruction"], str) + "\n\nPeer request (data):\n" + json.dumps(supplied, ensure_ascii=False),
                project_collaboration=True, turn_start_guard=start_guard,
                on_event=lambda kind, payload: event_sink(kind, payload) if kind != "answer.delta" else None,
            )
            # Persist actual model output before result IO. Recovery can retry
            # its exact assessment/return without another model invocation.
            self.store.update_turn(session_id, turn_id, collaboration_answer=answer)
        else:
            answer = _as_dict(raw_answer)
        response = self._complete_answer(session_id, turn, answer)
        event_sink("answer.delta", {"text": response["message"]})
        return response
