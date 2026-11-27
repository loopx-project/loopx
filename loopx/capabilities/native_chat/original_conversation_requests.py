"""Peer original-conversation request, management, and execution flows."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from ...chat_store import TERMINAL_TURN_STATES
from ...control_plane.collaboration.goal_instance_scope import CollaborationGoalScope
from ...file_lock import exclusive_file_lock
from .conversation_binding_contracts import (
    BindingAuthorityProtocol,
    BindingControllerProtocol,
    BindingPayload,
    BindingPayloadView,
    BindingRowDict,
    BindingSessionStoreProtocol,
    EventSinkFn,
    NativeSessionProtocol,
    as_binding_payload,
    core_effect,
)
from .original_conversation_execution import (
    complete_original_answer_flow,
    execute_original_request_flow,
    validate_current_original_flow,
    validate_original_turn_flow,
)
from .original_conversation_views import (
    as_string_dict,
    configure_original_grant_flow,
    control_original_request_flow,
    discover_original_recipients_flow,
    original_management_view_flow,
)


class OriginalConversationRequests:
    """Chat/source/store IO for the shared original-conversation request profile.

    The existing source policy, peer operation, request, revision, result and
    Session FIFO remain their respective owners. No shadow queue is created.
    """

    def __init__(self, controller: BindingControllerProtocol) -> None:
        self.controller: BindingControllerProtocol = controller
        self.store: BindingSessionStoreProtocol = controller.store
        self.root: Path = controller.coordination_runtime_root
        bindings = controller.project_contexts.conversation_bindings
        if bindings is None:
            raise ValueError("original conversation bindings are unavailable")
        self.bindings: BindingAuthorityProtocol = bindings

    _core = staticmethod(core_effect)

    def _policy(self) -> BindingPayload:
        from ...control_plane.collaboration.inbox import _read, _root

        path = _root(self.root) / "policy.json"
        if path.exists():
            return as_binding_payload(_read(path))
        return {
            "schema_version": "loopx_manager_context_policy_v1",
            "sources": {},
        }

    def _observation(self, session_id: str) -> tuple[BindingRowDict, BindingPayload]:
        session = self.store.load_session(session_id)
        if session is None:
            raise ValueError("original project Session is unavailable")
        raw_saved = session.get("project_context")
        saved = raw_saved if isinstance(raw_saved, Mapping) else {}
        current = self.bindings.read()
        binding = next(
            (
                row
                for row in current["bindings"]
                if row["binding_id"] == saved.get("binding_id")
            ),
            None,
        )
        if binding is None:
            raise ValueError("original conversation grant was revoked")
        observed = dict(self.bindings.observe(binding["transport_ref"]))
        return binding, observed

    def _recipient(
        self,
        session_id: str,
        expected_binding: BindingPayloadView,
        observation: BindingPayloadView,
        policy: BindingPayloadView,
    ) -> BindingPayload:
        current = self.bindings.read()
        if (
            next(
                (
                    row
                    for row in current["bindings"]
                    if row["binding_id"] == expected_binding["binding_id"]
                ),
                None,
            )
            != expected_binding
        ):
            raise ValueError("original conversation binding changed during verification")
        projects = self.bindings.projects
        available_projects = projects.available() if projects is not None else []
        return self._core(
            "collaboration.project.recipient",
            {
                "current": current,
                "binding_id": expected_binding["binding_id"],
                "observation": observation,
                "available_projects": available_projects,
                "session": self.store.load_session(session_id),
                "policy": policy,
                "sessions": [
                    self.store.load_session(str(row["session_id"]))
                    for row in self.store.list_sessions()
                ],
            },
        )

    @contextmanager
    def _source(
        self,
        requester: BindingPayloadView,
        parent_request_id: str,
        *,
        require_active: bool = True,
    ) -> Iterator[tuple[BindingPayload, CollaborationGoalScope]]:
        from ...control_plane.collaboration.goal_instance_scope import (
            collaboration_goal_scope,
        )
        from ...control_plane.collaboration.inbox import _entry
        from ...control_plane.collaboration.peer_context_observation import (
            require_parent_context_access,
        )

        if self.controller.registry_path is None:
            raise ValueError("original conversation registry is unavailable")
        raw_ref = requester.get("goal_ref")
        ref_mapping = raw_ref if isinstance(raw_ref, Mapping) else {}
        goal_id_val = ref_mapping.get("goal_id")
        goal_id = goal_id_val if isinstance(goal_id_val, str) else ""
        raw_agent_id = requester.get("agent_id")
        agent_id = raw_agent_id if isinstance(raw_agent_id, str) else ""
        caller_goal_ref = as_string_dict(raw_ref)
        with collaboration_goal_scope(
            self.controller.registry_path,
            goal_id=goal_id,
            agents=(agent_id,),
            caller_goal_ref=caller_goal_ref,
            require_active=require_active,
        ) as scope:
            if not scope.exact or requester != {
                "goal_ref": scope.caller_goal_ref,
                "agent_id": raw_agent_id,
            }:
                raise ValueError("an exact original requester is required")
            parent = _entry(
                self.root,
                scope.goal_id,
                str(requester["agent_id"]),
                parent_request_id,
                scope=scope,
            )
            if "request_control" in parent and self._view(parent)["control_state"] in {
                "cancelled",
                "cancellation_pending",
            }:
                raise ValueError("the original source request was cancelled")
            access = require_parent_context_access(
                self.root,
                self.controller.registry_path,
                parent,
                str(requester["agent_id"]),
                scope=scope,
            )
            yield (
                {
                    "requester": dict(requester),
                    "parent": parent,
                    "parent_request_id": parent_request_id,
                    "original_source_id": access["source_id"],
                    "parent_access_verified": True,
                },
                scope,
            )

    @contextmanager
    def _fenced(
        self,
        session_id: str,
        requester: BindingPayloadView,
        parent_request_id: str,
    ) -> Iterator[tuple[BindingPayload, CollaborationGoalScope]]:
        from ...control_plane.collaboration.inbox import _root
        from ...control_plane.collaboration.source_policy_fence import (
            source_policy_fence,
        )

        binding, observed = self._observation(session_id)
        # Every writer uses source -> Goal -> binding -> request -> Session.
        # Provider IO precedes these fences; canonical identity is reread inside.
        with (
            source_policy_fence(_root(self.root) / "policy.json"),
            self._source(requester, parent_request_id) as (source, scope),
        ):
            with exclusive_file_lock(
                self.bindings.path, operation="original_conversation_peer"
            ):
                policy = self._policy()
                selected = self._recipient(session_id, binding, observed, policy)
                yield (
                    {
                        **source,
                        "policy": policy,
                        "recipient": selected["recipient"],
                        "current_recipient": selected["recipient"],
                        "policy_sha256": selected["policy_sha256"],
                    },
                    scope,
                )

    def configure(self, session_id: str, body: BindingPayloadView) -> BindingPayload:
        return configure_original_grant_flow(
            root=self.root,
            session_id=session_id,
            body=body,
            policy_fn=self._policy,
            fenced_fn=self._fenced,
            core_fn=self._core,
        )

    def management_view(
        self,
        session_id: str,
        *,
        search: str = "",
        cursor: str | None = None,
    ) -> BindingPayload:
        return original_management_view_flow(
            root=self.root,
            bindings_path=self.bindings.path,
            controller=self.controller,
            session_id=session_id,
            search=search,
            cursor=cursor,
            policy_fn=self._policy,
            observation_fn=self._observation,
            recipient_fn=self._recipient,
            entry_fn=self._entry,
            inbox_fn=self._inbox,
            core_fn=self._core,
        )

    def discover(
        self, requester: BindingPayloadView, parent_request_id: str
    ) -> BindingPayload:
        return discover_original_recipients_flow(
            root=self.root,
            controller=self.controller,
            requester=requester,
            parent_request_id=parent_request_id,
            policy_fn=self._policy,
            source_fn=self._source,
            fenced_fn=self._fenced,
            core_fn=self._core,
        )

    def publish(self, body: BindingPayloadView) -> BindingPayload:
        from ...control_plane.collaboration.inbox import (
            _hash,
            _now,
            _read,
            _request_lock,
            _root,
            _write,
        )
        from ...control_plane.collaboration.peers import _operation_address

        recipient = body["recipient"]
        requester = body["requester"]
        if not isinstance(recipient, Mapping) or not isinstance(requester, Mapping):
            raise ValueError("invalid original conversation request payload")
        session_id = str(recipient["session_id"])
        with self._fenced(
            session_id, requester, str(body["parent_request_id"])
        ) as (facts, scope):
            if recipient != facts["recipient"]:
                raise ValueError("original conversation recipient identity changed")
            source_id, op_path = _operation_address(
                self.root, scope, str(requester["agent_id"]), str(body["operation_id"])
            )
            # Native planning owns identity and replay. The old operation is
            # consulted before any queue input or return route can be written.
            prior = _read(op_path) if op_path.exists() else None
            preview = self._core(
                "collaboration.project.request",
                {
                    **facts,
                    "source_id": source_id,
                    "request": {"recipient": recipient, "brief": body["brief"]},
                    "observed_at": _now(),
                },
            )
            preview_entry = preview["entry"]
            assert isinstance(preview_entry, Mapping)
            request_id = str(preview_entry["request_id"])
            path = (
                _root(self.root)
                / "entries"
                / _hash({"recipient": recipient})
                / (request_id + ".json")
            )
            with _request_lock(self.root, request_id, None, op_path, force_exact=True):
                prior = _read(op_path) if op_path.exists() else prior
                plan = self._core(
                    "collaboration.project.request",
                    {
                        **facts,
                        "source_id": source_id,
                        "request": {"recipient": recipient, "brief": body["brief"]},
                        "observed_at": _now(),
                        "operation": prior,
                        "entry": _read(path) if path.exists() else None,
                    },
                )
                row = plan["entry"]
                route = plan["return_route"]
                assert isinstance(row, dict) and isinstance(route, dict)
                route_path = _root(self.root) / "roundtrips" / (request_id + ".json")
                if route_path.exists() and _read(route_path) != route:
                    raise ValueError(
                        "original requester return route identity conflict"
                    )
                if prior is None:
                    operation_doc = plan["operation"]
                    assert isinstance(operation_doc, dict)
                    _write(op_path, operation_doc)
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
                turn, created = self._enqueue(row)
        recovered = self._resume(session_id, turn, created)
        return {
            "ok": True,
            "request_id": request_id,
            "recipient": recipient,
            "turn_id": turn["turn_id"],
            "status": turn["status"],
            "replayed": not created,
            "goal_created": False,
            "recovered_result": recovered,
        }

    def _enqueue(self, row: BindingPayloadView) -> tuple[BindingPayload, bool]:
        queue = self._core("collaboration.project.continuation", {"entry": row})
        recipient = row["recipient"]
        assert isinstance(recipient, Mapping)
        raw_request = queue["collaboration_request"]
        collaboration_request = (
            as_binding_payload(raw_request)
            if isinstance(raw_request, Mapping)
            else None
        )
        return self.store.create_queued_turn(
            str(recipient["session_id"]),
            client_turn_id=str(queue["client_turn_id"]),
            message=str(queue["message"]),
            origin="peer",
            collaboration_request=collaboration_request,
        )

    def _stop_turns(
        self, session_id: str, client_ids: list[str]
    ) -> BindingPayload | None:
        stopped: BindingPayload | None = None
        for client_id in client_ids:
            turn = self.store.turn_for_client(session_id, client_id)
            if turn and turn["status"] not in TERMINAL_TURN_STATES:
                stopped = self.controller.interrupt_turn(
                    session_id=session_id, turn_id=str(turn["turn_id"])
                )
        return stopped

    def _resume(
        self, session_id: str, turn: BindingPayloadView, created: bool
    ) -> object:
        recovered: object = None
        raw_answer = turn.get("collaboration_answer")
        if not created and raw_answer is not None:
            if not isinstance(raw_answer, Mapping):
                raise ValueError("original conversation answer must be a mapping")
            recovered = self._complete_answer(session_id, turn, raw_answer)[
                "collaboration_result"
            ]
        if turn["status"] not in TERMINAL_TURN_STATES:
            projects = self.controller.project_contexts
            loaded = self.store.load_session(session_id)
            context = projects.session_context(loaded or {})
            self.controller.resume_session_queue(
                session_id=session_id,
                work_dir=Path(str(context["project"])),
                objective=str(context.get("objective") or context["title"]),
            )
        return recovered

    def _entry(
        self, request_id: str, recipient: BindingPayloadView
    ) -> BindingPayload:
        from ...control_plane.collaboration.inbox import _entry

        return as_binding_payload(
            _entry(self.root, None, None, request_id, recipient=dict(recipient))
        )

    def _view(self, row: BindingPayloadView) -> BindingPayload:
        raw_view = self._core(
            "collaboration.request.control",
            {"entry": row, "input": {"operation": "inspect"}, "source": {}},
        )["view"]
        if not isinstance(raw_view, Mapping):
            raise ValueError("invalid original conversation control view")
        return as_binding_payload(raw_view)

    def _inbox(
        self,
        recipient: BindingPayloadView,
        *,
        mark_read: bool,
        request_id: str | None = None,
        cursor: str | None = None,
    ) -> BindingPayload:
        from ...control_plane.collaboration.inbox import _now

        raw_result = self._core(
            "collaboration.inbox.read",
            {
                "runtime_root": str(self.root.resolve()),
                "scope": {
                    "profile_id": "original_conversation_v0",
                    "recipient": recipient,
                },
                "operation": "receiver_read" if mark_read else "pending",
                "request_id": request_id,
                "include_settled": not mark_read,
                "cursor": cursor,
                "observed_at": _now(),
                "read_command": "original project Turn supplied context",
            },
            large_local_snapshot=True,
        )["result"]
        if not isinstance(raw_result, Mapping):
            raise ValueError("invalid original conversation inbox read")
        return as_binding_payload(raw_result)

    def control(self, body: BindingPayloadView) -> BindingPayload:
        return control_original_request_flow(
            root=self.root,
            controller=self.controller,
            body=body,
            entry_fn=self._entry,
            fenced_fn=self._fenced,
            enqueue_fn=self._enqueue,
            resume_fn=self._resume,
            stop_turns_fn=self._stop_turns,
            core_fn=self._core,
        )

    def validate_turn(self, session_id: str, turn: BindingPayloadView) -> None:
        validate_original_turn_flow(
            session_id=session_id,
            turn=turn,
            entry_fn=self._entry,
            fenced_fn=self._fenced,
            validate_current_fn=self._validate_current,
            view_fn=self._view,
            core_fn=self._core,
        )

    def _validate_current(
        self, row: BindingPayloadView, facts: BindingPayloadView
    ) -> None:
        validate_current_original_flow(
            root=self.root, row=row, facts=facts, core_fn=self._core
        )

    def _complete_answer(
        self,
        session_id: str,
        turn: BindingPayloadView,
        answer: BindingPayloadView,
    ) -> BindingPayload:
        return complete_original_answer_flow(
            root=self.root,
            session_id=session_id,
            turn=turn,
            answer=answer,
            entry_fn=self._entry,
            fenced_fn=self._fenced,
            validate_current_fn=self._validate_current,
            view_fn=self._view,
            core_fn=self._core,
        )

    def execute(
        self,
        session_id: str,
        turn_id: str,
        native_session: NativeSessionProtocol,
        event_sink: EventSinkFn,
    ) -> BindingPayload:
        return execute_original_request_flow(
            session_id=session_id,
            turn_id=turn_id,
            native_session=native_session,
            event_sink=event_sink,
            store=self.store,
            validate_turn_fn=self.validate_turn,
            entry_fn=self._entry,
            fenced_fn=self._fenced,
            validate_current_fn=self._validate_current,
            view_fn=self._view,
            inbox_fn=self._inbox,
            complete_answer_fn=self._complete_answer,
            core_fn=self._core,
        )
