"""Private IO for Core-owned conversation context and audience grants.

Provider observations contain opaque App/owner references, not credentials.
The typed owner validates configuration, replacement, revocation, and use.
This file stores no conversation text and creates no Goal or model thread.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ...chat_store import (
    _atomic_write_json as _atomic_write_json,
    _read_json as _read_json,
)
from ...control_plane.effect_runtime import effect_runtime_request_scope
from ...file_lock import exclusive_file_lock as exclusive_file_lock
from .conversation_binding_agents import (
    agent_candidates_flow,
    agent_observation_flow,
    reconcile_steward_executor_flow,
    reconcile_steward_executors_flow,
    stop_recoveries_flow,
    stop_revoked_binding_flow,
    validate_external_agent_turn_flow,
)
from .conversation_binding_contracts import (
    AppStewardTargetDict as AppStewardTargetDict,
    BindingAuthorityProtocol as BindingAuthorityProtocol,
    BindingControllerProtocol as BindingControllerProtocol,
    BindingPayload as BindingPayload,
    BindingPayloadView as BindingPayloadView,
    BindingRowDict as BindingRowDict,
    BindingSessionStoreProtocol as BindingSessionStoreProtocol,
    BindingStateDict as BindingStateDict,
    LocalSessionSnapshotDict as LocalSessionSnapshotDict,
    ObserveFn as ObserveFn,
    ProjectContextsProtocol as ProjectContextsProtocol,
    ResolvedBindingDict as ResolvedBindingDict,
    as_binding_payload,
    as_binding_state,
    core_effect,
    host_audiences,
    host_ref,
    session_observation,
)
from .conversation_binding_delivery import (
    app_steward_session_flow,
    app_steward_target_flow,
    apply_delivery_scope_flow,
    delivery_channels_flow,
    ensure_delivery_scope_flow,
    goal_scope_ids_flow,
    input_context_flow,
    session_source,
)
from .conversation_binding_flows import (
    adopt_created_goal_flow,
    build_resolved_binding,
    change_agent_target_flow,
    configure_binding_flow,
    disconnect_binding_flow,
    find_binding_row,
    observe_local_sessions_flow,
    select_app_steward_flow,
)
from .original_conversation_requests import (
    OriginalConversationRequests as OriginalConversationRequests,
)


def _host_ref(session: BindingPayloadView) -> str:
    return host_ref(session)


def _host_audiences(
    store: BindingSessionStoreProtocol, session: BindingPayloadView
) -> list[str]:
    return host_audiences(store, session)


class ChatConversationBindings:
    """Durable store and boundary orchestrator for bound Chat conversations."""

    def __init__(
        self,
        *,
        root: Path,
        project_contexts: ProjectContextsProtocol | None,
        observe: ObserveFn,
        observe_group: ObserveFn | None = None,
    ) -> None:
        self.path: Path = root / "conversation-bindings.json"
        self.projects: ProjectContextsProtocol | None = project_contexts
        self._observe_owner: ObserveFn = observe
        self._observe_group: ObserveFn | None = observe_group
        self.controller: BindingControllerProtocol | None = None

    def _require_projects(self) -> ProjectContextsProtocol:
        if self.projects is None:
            raise ValueError("bound conversation project contexts are unavailable")
        return self.projects

    def observe(
        self,
        transport_ref: str,
        *,
        audience: str | None = None,
    ) -> BindingPayload:
        row: BindingPayloadView = next(
            (
                item
                for item in self.read()["bindings"]
                if item["transport_ref"] == transport_ref
            ),
            {},
        )
        if audience is None:
            raw_audience = row.get("audience", "owner")
            audience = raw_audience if isinstance(raw_audience, str) else None
        if audience == "group" and self._observe_group is not None:
            observed = as_binding_payload(self._observe_group(transport_ref))
            # Earlier group grants used the verified user's principal. Keep
            # that exact identity/home until an explicit disconnect/reconnect;
            # changing it during a poll would invalidate resumed Sessions.
            if (
                row.get("audience") == "group"
                and row.get("provider_ref") == observed["provider_ref"]
                and row.get("operator_ref") != observed["operator_ref"]
            ):
                return as_binding_payload(self._observe_owner(transport_ref))
            return observed
        return as_binding_payload(self._observe_owner(transport_ref))

    def read(self) -> BindingStateDict:
        if not self.path.exists():
            return BindingStateDict(
                {
                    "schema_version": "loopx_chat_conversation_bindings_v0",
                    "revision": 0,
                    "bindings": [],
                }
            )
        # An unreadable existing grant is not an empty configuration to overwrite.
        return as_binding_state(_read_json(self.path))

    @staticmethod
    def _core(
        operation: str,
        params: BindingPayloadView,
        *,
        large_local_snapshot: bool = False,
    ) -> BindingPayload:
        return core_effect(
            operation, params, large_local_snapshot=large_local_snapshot
        )

    def configure(
        self,
        *,
        transport_ref: str,
        project_ref: str,
        executor_endpoint_id: str,
        context_kind: str = "project",
        project_grant: str | None = None,
        goal_scope: str | None = None,
        audience: str | None = None,
        group_refs: list[str] | None = None,
        available_group_refs: list[str] | None = None,
        expected_provider_ref: str | None = None,
        expected_operator_ref: str | None = None,
    ) -> BindingRowDict:
        return configure_binding_flow(
            bindings_path=self.path,
            projects=self.projects,
            transport_ref=transport_ref,
            project_ref=project_ref,
            executor_endpoint_id=executor_endpoint_id,
            context_kind=context_kind,
            project_grant=project_grant,
            goal_scope=goal_scope,
            audience=audience,
            group_refs=group_refs,
            available_group_refs=available_group_refs,
            expected_provider_ref=expected_provider_ref,
            expected_operator_ref=expected_operator_ref,
            read_fn=self.read,
            observe_fn=self.observe,
            core_fn=self._core,
            write_json_fn=_atomic_write_json,
            delivery_channels_fn=self.delivery_channels,
            ensure_delivery_scope_fn=self._ensure_delivery_scope,
            revoke_delivery_sources_fn=self._revoke_delivery_sources,
            stop_revoked_binding_fn=self._stop_revoked_binding,
            lock_fn=exclusive_file_lock,
        )

    def disconnect(
        self, binding_id: str, *, expected_revision: int
    ) -> BindingStateDict:
        return disconnect_binding_flow(
            bindings_path=self.path,
            binding_id=binding_id,
            expected_revision=expected_revision,
            read_fn=self.read,
            core_fn=self._core,
            write_json_fn=_atomic_write_json,
            revoke_delivery_sources_fn=self._revoke_delivery_sources,
            stop_revoked_binding_fn=self._stop_revoked_binding,
            lock_fn=exclusive_file_lock,
        )

    def select_app_steward(
        self, binding_id: str | None, *, expected_revision: int
    ) -> BindingStateDict:
        """Select one existing source for the App entry without changing grants."""
        return select_app_steward_flow(
            bindings_path=self.path,
            binding_id=binding_id,
            expected_revision=expected_revision,
            projects=self.projects,
            read_fn=self.read,
            observe_fn=self.observe,
            core_fn=self._core,
            write_json_fn=_atomic_write_json,
            lock_fn=exclusive_file_lock,
        )

    def reconcile_steward_executors(self) -> None:
        """Publish a previously adopted machine executor at Chat startup."""
        reconcile_steward_executors_flow(
            controller=self.controller,
            read_fn=self.read,
            reconcile_one_fn=self._reconcile_steward_executor,
        )

    def _reconcile_steward_executor(
        self, row: BindingPayloadView, defaults: BindingPayloadView
    ) -> None:
        if self.controller is None:
            return
        reconcile_steward_executor_flow(
            bindings_path=self.path,
            controller=self.controller,
            projects=self._require_projects(),
            row=row,
            defaults=defaults,
            read_fn=self.read,
            observe_fn=self.observe,
            core_fn=self._core,
            write_json_fn=_atomic_write_json,
            goal_scope_ids_fn=self.goal_scope_ids,
            lock_fn=exclusive_file_lock,
        )

    def app_steward_target(self) -> AppStewardTargetDict:
        return app_steward_target_flow(self.app_steward_session)

    def app_steward_session(self) -> BindingPayload | None:
        """Read the Session within the selected source without starting work."""
        return app_steward_session_flow(
            controller=self.controller,
            read_fn=self.read,
            core_fn=self._core,
            observe_local_sessions_fn=lambda bid, app_entry: self._observe_local_sessions(
                bid, app_steward_entry=app_entry
            ),
            recheck_local_bindings_fn=self._recheck_local_bindings,
        )

    def _revoke_delivery_sources(self, binding_id: str) -> None:
        if self.controller is not None:
            from ..manager_context import revoke_conversation_sources

            revoke_conversation_sources(
                self.controller.coordination_runtime_root, binding_id
            )

    def _stop_revoked_binding(self, binding_id: str) -> None:
        """Apply the committed grant decision through existing exact Turn control."""
        stop_revoked_binding_flow(self.controller, binding_id)

    def resolve(
        self,
        *,
        binding_id: str,
        source_ref: str,
        sender_ref: str,
        private_human_message: bool,
        session_context: BindingPayloadView | None = None,
        group_ref: str | None = None,
        topic_ref: str | None = None,
        group_human_message: bool = False,
        include_observation: bool = False,
        origin: str | None = None,
        session: BindingPayloadView | None = None,
    ) -> ResolvedBindingDict:
        row = find_binding_row(self.read(), binding_id)
        if row is None:
            raise ValueError("conversation binding is no longer authorized")
        observation = dict(self.observe(str(row["transport_ref"])))
        # Provider verification can wait on IO. It cannot authorize a binding
        # that was revoked or replaced while that observation was in flight.
        # Unrelated binding changes do not invalidate this exact source.
        current = self.read()
        if find_binding_row(current, binding_id) != row:
            raise ValueError("conversation binding changed during identity verification")
        return build_resolved_binding(
            current=current,
            row=row,
            observation=observation,
            resolve_observed_fn=self._resolve_observed,
            source_ref=source_ref,
            sender_ref=sender_ref,
            private_human_message=private_human_message,
            session_context=session_context,
            group_ref=group_ref,
            topic_ref=topic_ref,
            group_human_message=group_human_message,
            include_observation=include_observation,
            origin=origin,
            session=session,
        )

    def _resolve_observed(
        self,
        current: BindingPayloadView,
        row: BindingPayloadView,
        observation: BindingPayloadView,
        source: BindingPayloadView,
    ) -> BindingPayload:
        projects = self._require_projects()
        return self._core(
            "collaboration.conversation.bound_context",
            {
                "current": current,
                "binding_id": row["binding_id"],
                "observation": observation,
                "available_projects": projects.available(),
                **source,
                **(
                    {"available_goal_ids": self.goal_scope_ids(row)}
                    if row.get("goal_scope") == "all_registered"
                    else {}
                ),
            },
        )

    def _resolve_source_mapping(
        self,
        *,
        binding_id: str,
        source: BindingPayloadView,
        include_observation: bool = False,
        session: BindingPayloadView | None = None,
        origin: str | None = None,
    ) -> ResolvedBindingDict:
        raw_session_ctx = source.get("session_context")
        session_context = raw_session_ctx if isinstance(raw_session_ctx, Mapping) else None
        raw_group_ref = source.get("group_ref")
        group_ref = str(raw_group_ref) if raw_group_ref is not None else None
        raw_topic_ref = source.get("topic_ref")
        topic_ref = str(raw_topic_ref) if raw_topic_ref is not None else None
        raw_origin = source.get("origin")
        resolved_origin = (
            origin
            if origin is not None
            else (str(raw_origin) if raw_origin is not None else None)
        )
        return self.resolve(
            binding_id=binding_id,
            source_ref=str(source["source_ref"]),
            sender_ref=str(source["sender_ref"]),
            private_human_message=bool(source["private_human_message"]),
            session_context=session_context,
            group_ref=group_ref,
            topic_ref=topic_ref,
            group_human_message=bool(source.get("group_human_message", False)),
            include_observation=bool(
                source.get("include_observation", include_observation)
            ),
            origin=resolved_origin,
            session=session,
        )

    @contextmanager
    def input_context(
        self,
        saved: BindingPayloadView,
        *,
        origin: str,
        session: BindingPayloadView,
        store: BindingSessionStoreProtocol,
    ) -> Iterator[BindingPayload]:
        """Observe outside the publication fence, then retain it through enqueue."""
        with input_context_flow(
            bindings_path=self.path,
            saved=saved,
            origin=origin,
            session=session,
            store=store,
            session_source_fn=self._session_source,
            resolve_source_fn=self._resolve_source_mapping,
            read_fn=self.read,
            resolve_observed_fn=self._resolve_observed,
            lock_fn=exclusive_file_lock,
        ) as resolved:
            yield resolved

    _session_observation = staticmethod(session_observation)

    def local_sessions(
        self, binding_id: str, *, app_steward_entry: bool = False
    ) -> list[BindingPayload]:
        return self.local_session_snapshot(
            binding_id, app_steward_entry=app_steward_entry
        )["sessions"]

    def local_session_snapshot(
        self, binding_id: str, *, app_steward_entry: bool = False
    ) -> LocalSessionSnapshotDict:
        if self.controller is None:
            raise ValueError("the bound conversation store is unavailable")
        current, sessions, source = self._observe_local_sessions(
            binding_id, app_steward_entry=app_steward_entry
        )
        payload = LocalSessionSnapshotDict(
            {
                "sessions": [
                    self.controller.store.public_session(session) for session in sessions
                ],
                "conversation_source": source,
            }
        )
        self._recheck_local_bindings(current)
        return payload

    def _recheck_local_bindings(self, observed: BindingPayloadView) -> None:
        if self.read() != observed:
            raise ValueError("conversation binding changed during discovery")

    def _observe_local_sessions(
        self, binding_id: str, *, app_steward_entry: bool
    ) -> tuple[BindingStateDict, list[BindingPayload], BindingPayload]:
        """Read original targets; metadata hints never replace Core's grant."""
        return observe_local_sessions_flow(
            binding_id=binding_id,
            app_steward_entry=app_steward_entry,
            controller=self.controller,
            projects=self.projects,
            read_fn=self.read,
            observe_fn=self.observe,
            core_fn=self._core,
            goal_scope_ids_fn=self.goal_scope_ids,
        )

    def goal_scope_ids(self, binding: BindingPayloadView) -> list[str]:
        return goal_scope_ids_flow(self.controller, binding)

    def delivery_channels(self, binding: BindingPayloadView) -> list[str]:
        return delivery_channels_flow(
            self.controller, binding, read_json_fn=_read_json
        )

    def ensure_delivery_scope(
        self,
        selected: BindingPayloadView,
        *,
        if_absent: bool = True,
        execute: bool = True,
    ) -> None:
        ensure_delivery_scope_flow(
            bindings_path=self.path,
            controller=self.controller,
            selected=selected,
            if_absent=if_absent,
            execute=execute,
            read_fn=self.read,
            inner_ensure_fn=self._ensure_delivery_scope,
            lock_fn=exclusive_file_lock,
        )

    def _ensure_delivery_scope(
        self,
        selected: BindingPayloadView,
        *,
        if_absent: bool = True,
        execute: bool = True,
    ) -> None:
        """The caller retains the binding publication fence, including configure."""
        apply_delivery_scope_flow(
            self.controller, selected, if_absent=if_absent, execute=execute
        )

    _session_source = staticmethod(session_source)

    def session_context(
        self,
        saved: BindingPayloadView,
        *,
        session: BindingPayloadView | None = None,
    ) -> ResolvedBindingDict:
        return self._resolve_source_mapping(
            binding_id=str(saved["binding_id"]),
            source=self._session_source(saved),
            session=session,
        )

    def manager_runtime_owner(
        self, saved: BindingPayloadView, channel_id: str
    ) -> bool:
        """Fresh Core audience proof; saved Session text is never authority."""
        selected = self.session_context(saved)
        if selected["channel_id"] != channel_id:
            raise ValueError("bound manager audience changed")
        return selected["owner_manager_audience"] is True

    def steward_scope(self, session: BindingPayloadView) -> list[str] | None:
        saved = session.get("steward_context")
        if not isinstance(saved, dict):
            return None
        selected = self.session_context(saved)
        if (
            session.get("goal_id") != "loopx-manager"
            or session.get("channel_id") != selected["channel_id"]
        ):
            raise ValueError("bound steward audience changed")
        raw_goal_ids = selected["context"]["goal_ids"]
        return (
            [str(item) for item in raw_goal_ids]
            if isinstance(raw_goal_ids, list)
            else []
        )

    def adopt_created_goal(
        self,
        *,
        binding_id: str,
        source: BindingPayloadView,
        proposal: BindingPayloadView,
        goal: BindingPayloadView,
    ) -> None:
        adopt_created_goal_flow(
            bindings_path=self.path,
            binding_id=binding_id,
            source=source,
            proposal=proposal,
            goal=goal,
            resolve_source_fn=lambda bid, src: self._resolve_source_mapping(
                binding_id=bid, source=src
            ),
            read_fn=self.read,
            core_fn=self._core,
            write_json_fn=_atomic_write_json,
            lock_fn=exclusive_file_lock,
        )

    def agent_observation(self, session_id: str) -> BindingPayload:
        """Read only the explicitly selected canonical attached Session/Goal."""
        return agent_observation_flow(self.controller, session_id)

    def agent_candidates(self, binding_id: str) -> list[BindingPayload]:
        return agent_candidates_flow(
            binding_id=binding_id,
            controller=self.controller,
            projects=self.projects,
            read_fn=self.read,
            observe_agent_fn=self.agent_observation,
        )

    def change_agent_target(
        self,
        *,
        binding_id: str,
        expected_revision: int,
        session_id: str | None = None,
        target_ref: str | None = None,
    ) -> BindingStateDict:
        return change_agent_target_flow(
            bindings_path=self.path,
            binding_id=binding_id,
            expected_revision=expected_revision,
            session_id=session_id,
            target_ref=target_ref,
            controller=self.controller,
            projects=self.projects,
            read_fn=self.read,
            observe_fn=self.observe,
            observe_agent_fn=self.agent_observation,
            core_fn=self._core,
            write_json_fn=_atomic_write_json,
            lock_fn=exclusive_file_lock,
        )

    def resolve_agent_target(
        self, selected: BindingPayloadView, target: BindingPayloadView
    ) -> BindingPayload:
        return self._core(
            "collaboration.conversation.agent_target",
            {
                "binding": selected["binding"],
                "context": selected["context"],
                "target": target,
                "target_observation": self.agent_observation(str(target["session_id"])),
            },
        )

    @effect_runtime_request_scope()
    def stop_recoveries(self, current: BindingPayloadView) -> list[BindingPayload]:
        """Project revoked execution from canonical Turns, never recreate a grant."""
        return stop_recoveries_flow(self.controller, current)


def validate_external_agent_turn(
    *,
    store: BindingSessionStoreProtocol,
    session: BindingPayloadView,
    goal: BindingPayloadView | None,
    turn: BindingPayloadView,
) -> None:
    """Validate attached external agent turn under grant and queue fences."""
    validate_external_agent_turn_flow(
        store=store,
        session=session,
        goal=goal,
        turn=turn,
        read_json_fn=_read_json,
        core_fn=ChatConversationBindings._core,
    )
