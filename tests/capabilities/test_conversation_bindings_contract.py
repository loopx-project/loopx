"""Contract, key-presence, malformed-input, and single-read regressions for bindings."""
from __future__ import annotations

import ast
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
import json
from pathlib import Path
import re
import sys

import pytest

from loopx import chat_store
from loopx.capabilities.native_chat import (
    conversation_binding_agents,
    conversation_binding_contracts,
    conversation_binding_delivery,
    conversation_binding_flows,
    conversation_bindings,
    original_conversation_execution,
    original_conversation_requests,
    original_conversation_views,
)
from loopx.capabilities.native_chat.conversation_binding_contracts import (
    AppStewardTargetDict,
    BindingAuthorityProtocol,
    BindingControllerProtocol,
    BindingPayload,
    BindingPayloadView,
    BindingRowDict,
    BindingStateDict,
    EventSinkFn,
    LocalSessionSnapshotDict,
    ProjectContextsProtocol,
    ResolvedBindingDict,
    as_binding_payload,
    as_binding_row,
    as_binding_state,
)
from loopx.capabilities.native_chat.conversation_bindings import (
    ChatConversationBindings,
    OriginalConversationRequests,
)
from loopx.capabilities.native_chat.original_conversation_execution import (
    complete_original_answer_flow,
    execute_original_request_flow,
    validate_original_turn_flow,
)
from loopx.capabilities.native_chat.original_conversation_views import (
    original_management_view_flow,
)
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.collaboration import inbox
from loopx.control_plane.collaboration.goal_instance_scope import CollaborationGoalScope

CONTRACT_MODULES = (
    conversation_binding_contracts,
    conversation_binding_agents,
    conversation_binding_flows,
    conversation_binding_delivery,
    original_conversation_views,
    original_conversation_execution,
    original_conversation_requests,
    conversation_bindings,
)


class _FakeProjects:
    workspace_grant = "workspace_read"
    conversation_bindings: BindingAuthorityProtocol | None = None

    def available(self) -> Sequence[BindingPayloadView]:
        return [{"project_ref": "proj-1", "workspace_path": "/workspace/proj-1"}]

    def session_context(self, session: BindingPayload, /) -> BindingPayload:
        del session
        return {}


class _DummyStore:
    def __init__(
        self,
        root: Path,
        session: BindingPayload | None = None,
        turn_doc: BindingPayload | None = None,
    ) -> None:
        self.root = root
        self._session = session or {"session_id": "sess-1", "title": "Project Chat"}
        self._turn_doc = turn_doc
        self.updated_turns: list[tuple[str, str, dict[str, object]]] = []

    def list_sessions(
        self,
        *,
        goal_id: str | None = None,
        agent_id: str | None = None,
        channel_id: str | None = None,
    ) -> Sequence[BindingPayload]:
        del goal_id, agent_id, channel_id
        return [dict(self._session)]

    def load_session(self, session_id: str, /) -> BindingPayload | None:
        return dict(self._session) if session_id == self._session["session_id"] else None

    def load_turn(self, session_id: str, turn_id: str, /) -> BindingPayload | None:
        del session_id, turn_id
        return dict(self._turn_doc) if self._turn_doc is not None else None

    def queued_turns(
        self,
        session_id: str,
        *,
        strict: bool = False,
        include_reserved: bool = False,
        capacity_at: str | None = None,
    ) -> Sequence[BindingPayload]:
        del session_id, strict, include_reserved, capacity_at
        return []

    def select_session(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        work_dir: Path,
        mode: str = "resume_latest",
        session_id: str | None = None,
        channel_id: str | None = None,
        executor_endpoint_id: str | None = None,
        strict: bool = False,
        include_reserved: bool = False,
        capacity_at: str | None = None,
    ) -> Sequence[BindingPayload]:
        del (
            goal_id,
            agent_id,
            work_dir,
            mode,
            session_id,
            channel_id,
            executor_endpoint_id,
            strict,
            include_reserved,
            capacity_at,
        )
        return []

    def update_session(
        self,
        session_id: str,
        *,
        expected_statuses: set[str] | None = None,
        expected_upstream_thread_id: str | None = None,
        metadata_review: bool = False,
        quota_recovery: bool = False,
        answer_review_admission: bool = False,
        **changes: object,
    ) -> BindingPayload:
        del (
            expected_statuses,
            expected_upstream_thread_id,
            metadata_review,
            quota_recovery,
            answer_review_admission,
        )
        return {"session_id": session_id, **changes}

    @contextmanager
    def read_session_fenced(self, session_id: str, /) -> Iterator[BindingPayload | None]:
        yield self.load_session(session_id)

    def iter_session_metadata(
        self,
        *,
        goal_id: str | None = None,
        agent_id: str | None = None,
        channel_id: str | None = None,
    ) -> Iterator[tuple[Path, BindingPayload]]:
        del goal_id, agent_id, channel_id
        return iter(())

    def public_session(self, payload: BindingPayload, /) -> BindingPayload:
        return dict(payload)

    def turn_for_client(
        self, session_id: str, client_turn_id: str, /
    ) -> BindingPayload | None:
        del session_id, client_turn_id
        return {"turn_id": "turn-1", "status": "completed"}

    def create_queued_turn(
        self,
        session_id: str,
        *,
        client_turn_id: str,
        message: str,
        origin: str = "external",
        collaboration_request: BindingPayload | None = None,
    ) -> tuple[BindingPayload, bool]:
        del session_id, client_turn_id, message, origin, collaboration_request
        return ({"turn_id": "t-new", "status": "queued"}, True)

    def update_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        expected_statuses: set[str] | None = None,
        **changes: object,
    ) -> BindingPayload | None:
        del expected_statuses
        self.updated_turns.append((session_id, turn_id, dict(changes)))
        return {"turn_id": turn_id, **changes}

    def resumable_session_candidates(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        channel_id: str | None = None,
    ) -> Sequence[BindingPayload]:
        del goal_id, agent_id, channel_id
        return []


class _DummyController:
    def __init__(self, root: Path, store: ChatSessionStore) -> None:
        self.registry_path: Path | None = root / "registry.json"
        self.coordination_runtime_root: Path = root
        self.store: ChatSessionStore = store
        self.project_contexts: ProjectContextsProtocol = _FakeProjects()

    def interrupt_turn(self, *, session_id: str, turn_id: str) -> BindingPayload:
        return {"session_id": session_id, "turn_id": turn_id, "status": "stopped"}

    def resume_session_queue(
        self, *, session_id: str, work_dir: Path, objective: str
    ) -> None:
        del session_id, work_dir, objective


def _make_bindings(
    tmp_path: Path, *, projects: ProjectContextsProtocol | None = None
) -> ChatConversationBindings:
    return ChatConversationBindings(
        root=tmp_path,
        project_contexts=projects,
        observe=lambda ref: {
            "transport_ref": ref,
            "provider_ref": "prov-1",
            "operator_ref": "op-1",
            "verified": True,
        },
    )


def test_conversation_binding_modules_keep_size_zero_any_and_suppression_budgets() -> None:
    root = Path(conversation_bindings.__file__).resolve().parent
    assert (
        len(Path(conversation_bindings.__file__).read_text(encoding="utf-8").splitlines())
        <= 650
    )
    paths = [Path(mod.__file__ or "").resolve() for mod in CONTRACT_MODULES] + [
        Path(__file__).resolve()
    ]
    for idx, mod_file in enumerate(paths):
        if idx < len(CONTRACT_MODULES):
            assert mod_file.parent == root
        src = mod_file.read_text(encoding="utf-8")
        assert len(src.splitlines()) < 750, mod_file.name
        tree = ast.parse(src)
        anys = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "Any"
        ]
        assert anys == [], f"{mod_file.name} unexpectedly uses Any at lines {anys}"
        assert not re.search(r"#\s*type:\s*ignore|\bnoqa\b", src), mod_file.name


def test_conversation_bindings_preserves_reexports_and_monkeypatch_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        conversation_bindings.OriginalConversationRequests
        is OriginalConversationRequests
    )
    assert conversation_bindings._atomic_write_json is chat_store._atomic_write_json
    assert callable(conversation_bindings.exclusive_file_lock)
    assert callable(conversation_bindings._read_json)
    assert callable(conversation_bindings._host_ref)
    assert callable(conversation_bindings._host_audiences)
    assert callable(ChatConversationBindings._core)
    assert callable(ChatConversationBindings.stop_recoveries)

    seen_ops: list[str] = []
    real_lock = conversation_bindings.exclusive_file_lock

    @contextmanager
    def wrapped_lock(path: Path, *, operation: str = "lock") -> Iterator[Path]:
        seen_ops.append(operation)
        with real_lock(path, operation=operation) as held:
            yield held

    monkeypatch.setattr(conversation_bindings, "exclusive_file_lock", wrapped_lock)
    bindings = _make_bindings(tmp_path, projects=None)
    bindings.select_app_steward(None, expected_revision=0)
    assert seen_ops == ["select_app_steward"]

    # Immediate caller frame seen inside bindings.observe() during resolve() must be "resolve".
    caller_frames: list[str] = []
    inner_observe = bindings.observe

    def framed_observe(
        transport_ref: str, *, audience: str | None = None
    ) -> BindingPayload:
        caller_frames.append(sys._getframe(1).f_code.co_name)
        return inner_observe(transport_ref, audience=audience)

    monkeypatch.setattr(bindings, "observe", framed_observe)
    monkeypatch.setattr(
        bindings,
        "read",
        lambda: BindingStateDict(
            {"revision": 1, "bindings": [BindingRowDict({"binding_id": "b1", "transport_ref": "t1"})]}
        ),
    )
    monkeypatch.setattr(bindings, "_resolve_observed", lambda _c, _r, _o, _s: {"ok": True})
    bindings.resolve(
        binding_id="b1",
        source_ref="s1",
        sender_ref="op-1",
        private_human_message=True,
    )
    assert caller_frames == ["resolve"]


def test_typed_contract_wrappers_preserve_dict_json_roundtrip() -> None:
    row = as_binding_row({"binding_id": "b1", "transport_ref": "t1"})
    state = as_binding_state(
        {"schema_version": "v0", "revision": 1, "bindings": [row]}
    )
    payload = as_binding_payload({"key": "val"})
    assert payload == {"key": "val"}
    assert isinstance(row, BindingRowDict) and isinstance(row, dict)
    assert isinstance(state, BindingStateDict) and isinstance(state, dict)
    assert json.loads(json.dumps(state))["bindings"][0]["binding_id"] == "b1"
    assert isinstance(
        ResolvedBindingDict(
            {
                "binding": row,
                "channel_id": "c1",
                "context": {},
                "owner_manager_audience": True,
            }
        ),
        dict,
    )
    assert isinstance(
        AppStewardTargetDict({"binding_id": None, "session_id": None}), dict
    )
    assert isinstance(
        LocalSessionSnapshotDict({"sessions": [], "conversation_source": {}}), dict
    )


def test_legacy_missing_files_and_unselected_steward_without_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings = _make_bindings(tmp_path, projects=None)
    state = bindings.read()
    assert state == {
        "schema_version": "loopx_chat_conversation_bindings_v0",
        "revision": 0,
        "bindings": [],
    }
    updated = bindings.select_app_steward(None, expected_revision=0)
    assert updated["bindings"] == []

    monkeypatch.setattr(bindings, "read", lambda: BindingStateDict({"revision": 0}))
    with pytest.raises(ValueError, match="no longer authorized"):
        bindings.resolve(
            binding_id="missing",
            source_ref="s1",
            sender_ref="op-1",
            private_human_message=True,
        )


def test_configure_preserves_goal_scope_key_presence_vs_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings = _make_bindings(tmp_path, projects=_FakeProjects())
    existing_row = {
        "binding_id": "b-keep",
        "transport_ref": "app-1",
        "provider_ref": "prov-1",
        "operator_ref": "op-1",
        "context_kind": "steward",
        "project_ref": "proj-1",
        "executor_endpoint_id": "codex",
        "grant": "portfolio_read",
        "enabled": True,
        "goal_ids": ["g1"],
    }
    captured_candidates: list[BindingPayload] = []

    def fake_core(
        op: str, params: BindingPayloadView, **_kwargs: object
    ) -> BindingPayload:
        assert op == "collaboration.conversation.binding"
        binding = params["binding"]
        assert isinstance(binding, Mapping)
        captured_candidates.append(dict(binding))
        return {
            "changed": False,
            "binding": dict(binding),
            "state": dict(params["current"]) if isinstance(params["current"], Mapping) else {},
        }

    monkeypatch.setattr(bindings, "_core", fake_core)

    monkeypatch.setattr(
        bindings,
        "read",
        lambda: BindingStateDict(
            {
                "schema_version": "loopx_chat_conversation_bindings_v0",
                "revision": 1,
                "bindings": [BindingRowDict(dict(existing_row))],
            }
        ),
    )
    bindings.configure(
        transport_ref="app-1",
        project_ref="proj-1",
        executor_endpoint_id="codex",
        context_kind="steward",
        goal_scope=None,
    )
    assert "goal_scope" not in captured_candidates[-1]

    with_scope = {**existing_row, "goal_scope": "selected"}
    monkeypatch.setattr(
        bindings,
        "read",
        lambda: BindingStateDict(
            {
                "schema_version": "loopx_chat_conversation_bindings_v0",
                "revision": 1,
                "bindings": [BindingRowDict(with_scope)],
            }
        ),
    )
    bindings.configure(
        transport_ref="app-1",
        project_ref="proj-1",
        executor_endpoint_id="codex",
        context_kind="steward",
        goal_scope=None,
    )
    assert captured_candidates[-1]["goal_scope"] == "selected"


def test_malformed_inputs_and_turn_key_presence_fail_closed(tmp_path: Path) -> None:
    bindings = _make_bindings(tmp_path, projects=_FakeProjects())
    with pytest.raises(ValueError, match="steward Goal scope"):
        bindings.configure(
            transport_ref="app-1",
            project_ref="proj-1",
            executor_endpoint_id="codex",
            context_kind="project",
            goal_scope="selected",
        )
    with pytest.raises(ValueError, match="workspace write authorization"):
        bindings.configure(
            transport_ref="app-1",
            project_ref="proj-1",
            executor_endpoint_id="codex",
            context_kind="steward",
            project_grant="workspace_write",
        )
    with pytest.raises(ValueError, match="unsupported bound conversation input origin"):
        with bindings.input_context(
            {"binding_id": "b1"},
            origin="email",
            session={"session_id": "s1"},
            store=_DummyStore(tmp_path),
        ):
            pass

    @contextmanager
    def unused_fence(
        _sid: str, _req: BindingPayloadView, _pid: str
    ) -> Iterator[tuple[BindingPayload, CollaborationGoalScope]]:
        if True:
            raise AssertionError("fence must not run for invalid turn")
        yield (
            {},
            CollaborationGoalScope(
                registry_path=tmp_path / "registry.json",
                goal_id="g1",
                goal={},
                profile_id=None,
                caller_goal_ref=None,
                current_goal_ref=None,
            ),
        )

    validate_original_turn_flow(
        session_id="sess-1",
        turn={"origin": "web"},
        entry_fn=lambda _rid, _rec: {},
        fenced_fn=unused_fence,
        validate_current_fn=lambda _row, _facts: None,
        view_fn=lambda _row: {},
        core_fn=lambda _op, _params, **_kw: {},
    )
    with pytest.raises(ValueError, match="peer Turn provenance is unavailable"):
        validate_original_turn_flow(
            session_id="sess-1",
            turn={"origin": "web", "collaboration_request": None},
            entry_fn=lambda _rid, _rec: {},
            fenced_fn=unused_fence,
            validate_current_fn=lambda _row, _facts: None,
            view_fn=lambda _row: {},
            core_fn=lambda _op, _params, **_kw: {},
        )
    with pytest.raises(ValueError, match="unsupported fields"):
        complete_original_answer_flow(
            root=tmp_path,
            session_id="sess-1",
            turn={"origin": "peer"},
            answer={"text": "x"},
            entry_fn=lambda _rid, _rec: {},
            fenced_fn=unused_fence,
            validate_current_fn=lambda _row, _facts: None,
            view_fn=lambda _row: {},
            core_fn=lambda _op, _params, **_kw: {"required": ["text", "decision"]},
        )


def test_management_view_reads_entry_once_per_request_with_multiple_replies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "coord"
    replies_dir = inbox._root(root) / "replies" / "req-1"
    replies_dir.mkdir(parents=True)
    for idx, name in enumerate(("conclusion.json", "conclusion-00000001.json", "decision-00000002.json")):
        (replies_dir / name).write_text(
            json.dumps(
                {
                    "text": f"result-{idx}",
                    "decision": "adopt",
                    "request_revision": idx + 1,
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(inbox, "reply_status", lambda _root, _item: [])
    monkeypatch.setattr(inbox, "inspect_result_identity", lambda _r, _e, _p: True)

    chat_store_instance = ChatSessionStore(tmp_path / "chat")
    monkeypatch.setattr(
        chat_store_instance,
        "turn_for_client",
        lambda _sid, _cid: {"turn_id": "turn-1", "status": "completed"},
    )
    controller: BindingControllerProtocol = _DummyController(root, chat_store_instance)
    entry_calls: list[str] = []

    def count_entry(request_id: str, recipient: BindingPayloadView) -> BindingPayload:
        del recipient
        entry_calls.append(request_id)
        return {"request_id": request_id, "recipient": {"session_id": "sess-1"}}

    def fake_core(
        op: str, params: BindingPayloadView, **_kwargs: object
    ) -> BindingPayload:
        del params
        if op == "collaboration.source.original_conversation_grants":
            return {"grants": []}
        if op == "collaboration.project.continuation":
            return {"client_turn_id": "ct-1"}
        raise AssertionError(f"unexpected op: {op}")

    view = original_management_view_flow(
        root=root,
        bindings_path=tmp_path / "bindings.json",
        controller=controller,
        session_id="sess-1",
        search="",
        cursor=None,
        policy_fn=lambda: {
            "schema_version": "loopx_manager_context_policy_v1",
            "sources": {},
        },
        observation_fn=lambda _sid: (BindingRowDict({"binding_id": "b1"}), {}),
        recipient_fn=lambda _sid, _b, _o, _p: {"recipient": {"session_id": "sess-1"}},
        entry_fn=count_entry,
        inbox_fn=lambda _rec, **_kw: {
            "items": [{"request_id": "req-1", "recipient": {"session_id": "sess-1"}}],
            "has_more": False,
            "next_cursor": None,
        },
        core_fn=fake_core,
    )
    assert entry_calls == ["req-1"]
    requests = view["requests"]
    assert isinstance(requests, list) and len(requests[0]["returns"]) == 3


def test_execute_start_guard_reads_guarded_entry_once(tmp_path: Path) -> None:
    store = _DummyStore(
        tmp_path,
        turn_doc={
            "turn_id": "t1",
            "origin": "peer",
            "collaboration_request": {
                "request_id": "req-1",
                "recipient": {"session_id": "sess-1"},
            },
        },
    )
    entry_calls: list[str] = []

    def count_entry(request_id: str, recipient: BindingPayloadView) -> BindingPayload:
        del recipient
        entry_calls.append(request_id)
        return {
            "request_id": request_id,
            "parent_request_id": "p-1",
            "requester": {"agent_id": "builder", "goal_ref": {"goal_id": "g1"}},
            "recipient": {"session_id": "sess-1"},
            "instruction": "Verify",
        }

    @contextmanager
    def fake_fence(
        _sid: str, _req: BindingPayloadView, _pid: str
    ) -> Iterator[tuple[BindingPayload, CollaborationGoalScope]]:
        yield (
            {"recipient": {"session_id": "sess-1"}},
            CollaborationGoalScope(
                registry_path=tmp_path / "registry.json",
                goal_id="g1",
                goal={},
                profile_id=None,
                caller_goal_ref=None,
                current_goal_ref=None,
            ),
        )

    class _FakeNativeSession:
        def send(
            self,
            user_message: str,
            /,
            *,
            project_collaboration: bool = False,
            turn_start_guard: Callable[[], AbstractContextManager[None]] | None = None,
            on_event: EventSinkFn | None = None,
        ) -> BindingPayload:
            del user_message, project_collaboration, on_event
            assert turn_start_guard is not None
            with turn_start_guard():
                pass
            return {
                "text": "done",
                "decision": "adopt",
                "request_revision": 1,
                "request_digest": "d1",
            }

    events: list[tuple[str, BindingPayload]] = []
    res = execute_original_request_flow(
        session_id="sess-1",
        turn_id="t1",
        native_session=_FakeNativeSession(),
        event_sink=lambda kind, payload: events.append((kind, payload)),
        store=store,
        validate_turn_fn=lambda _sid, _t: None,
        entry_fn=count_entry,
        fenced_fn=fake_fence,
        validate_current_fn=lambda _row, _facts: None,
        view_fn=lambda _row: {
            "control_state": "active",
            "revision": 1,
            "revision_digest": "d1",
        },
        inbox_fn=lambda _rec, **_kw: {
            "items": [{"request_revision": 1, "request_revision_digest": "d1"}]
        },
        complete_answer_fn=lambda _sid, _t, _a: {
            "message": "done",
            "collaboration_result": {},
        },
        core_fn=lambda _op, _p, **_kw: {},
    )
    assert res["message"] == "done"
    # Read 1: initial row; Read 2: fenced inbox fetch; Read 3: single read in start_guard.
    assert len(entry_calls) == 3
    assert events == [("answer.delta", {"text": "done"})]
