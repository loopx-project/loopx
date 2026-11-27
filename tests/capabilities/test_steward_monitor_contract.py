"""Contract, parity, efficiency, and failure-mode tests for Steward monitor modules."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import cast

import pytest

from loopx.capabilities.native_chat import (
    steward_monitor,
    steward_monitor_config,
    steward_monitor_pump,
    steward_monitor_signals,
    steward_monitor_turns,
)
from loopx.capabilities.native_chat.steward_monitor import (
    ADVANCEMENT_ADMISSION_RETRY_SUFFIX,
    ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS,
    ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX,
    FOLLOWUP_TARGET_PREFIX,
    STEWARD_BACKGROUND_CHANNEL_PREFIX,
    STEWARD_BACKGROUND_SESSION_PREFIX,
    TARGET,
    StewardHostProtocol,
    StewardMonitor,
    StewardObservationAdapter,
    _accepts_backfill_comments,
    _github_repo_slug,
    _signal_scope_uses_stalled_age,
    actor_steward_binding_id,
    advancement_turn_id,
    ensure_steward_background_session,
    is_exhausted_zero_write_followup_todo,
    load_steward_monitor_turn_by_id,
    lookup_steward_monitor_turn,
    monitor_turn_id,
    normalize_steward_monitor_scope,
    pump_managed_session_monitors,
    pump_steward_monitors,
    read_steward_monitor_scope,
    resolve_advancement_turn_id,
    steward_background_channel_id,
    steward_background_session_id,
    steward_monitor_delivery_dir,
    steward_monitor_scope_path,
    steward_monitor_snapshot_path,
    write_steward_monitor_scope,
)
from loopx.capabilities.native_chat.steward_managed_mode import StewardManagedMode
from loopx.capabilities.native_chat.steward_monitor_turns import (
    StewardControllerProtocol,
    StewardSessionStoreProtocol,
    StewardTodoRecord,
)
from loopx.capabilities.native_chat.steward_turn import native_steward_client_id
from loopx.chat_store import ChatSessionStore
from loopx.control_plane.runtime.effect_client_errors import EffectRuntimeRejected


class _ClosedFlag:
    def __init__(self, closed: bool = False) -> None:
        self._closed = closed

    def is_set(self) -> bool:
        return self._closed


class _StubBindings:
    def __init__(
        self,
        path: Path,
        *,
        core_responses: dict[str, dict[str, object]] | None = None,
        bindings_rows: list[dict[str, object]] | None = None,
    ) -> None:
        self.path = path
        self.core_responses = dict(core_responses or {})
        self.bindings_rows = list(bindings_rows or [])
        self.core_calls: list[tuple[str, dict[str, object]]] = []

    def resolve(
        self,
        *,
        binding_id: str,
        session: dict[str, object],
        include_observation: bool,
        origin: str,
        source_ref: object,
        sender_ref: object,
        private_human_message: bool,
        session_context: object,
    ) -> dict[str, object]:
        return {}

    def read(self) -> dict[str, object]:
        return {"bindings": list(self.bindings_rows)}

    def _core(self, command: str, payload: dict[str, object]) -> dict[str, object]:
        self.core_calls.append((command, payload))
        return dict(self.core_responses.get(command, {}))

    def steward_scope(self, session: dict[str, object]) -> list[str] | None:
        raw = session.get("steward_scope_goal_ids")
        return [str(item) for item in raw] if isinstance(raw, list) else None


class _StubProjectContexts:
    def __init__(self, bindings: _StubBindings) -> None:
        self.conversation_bindings = bindings


class _StubController:
    def __init__(self, tmp_path: Path, *, bindings: _StubBindings | None = None) -> None:
        self.registry_path = tmp_path / "registry.json"
        self.coordination_runtime_root = tmp_path / "runtime"
        self.coordination_runtime_root.mkdir(parents=True, exist_ok=True)
        self.store = ChatSessionStore(tmp_path / "chat")
        self.lock = nullcontext()
        self.closed = _ClosedFlag(False)
        self.project_contexts = _StubProjectContexts(
            bindings or _StubBindings(tmp_path / "bindings.json")
        )


def _as_controller(controller: _StubController) -> StewardControllerProtocol:
    return cast(StewardControllerProtocol, controller)


class _CountingTurnLookupStore:
    """Thin wrapper over ChatSessionStore that counts turn_for_client calls."""

    def __init__(self, inner: ChatSessionStore) -> None:
        self._inner = inner
        self.turn_for_client_calls: list[tuple[str, str]] = []

    def _locked_session_path(
        self, session_id: str, operation: str
    ) -> AbstractContextManager[Path]:
        return self._inner._locked_session_path(session_id, operation)

    def create_session(
        self,
        *,
        goal_id: str | None,
        agent_id: str,
        adapter_kind: str,
        upstream_thread_id: str,
        channel_id: str | None = None,
        session_id: str | None = None,
        executor_endpoint_id: str | None = None,
        steward_context: None = None,
    ) -> object:
        return self._inner.create_session(
            goal_id=goal_id, agent_id=agent_id, adapter_kind=adapter_kind,
            upstream_thread_id=upstream_thread_id, channel_id=channel_id,
            session_id=session_id, executor_endpoint_id=executor_endpoint_id,
            steward_context=steward_context,
        )

    def load_session(self, session_id: str) -> dict[str, object] | None:
        loaded = self._inner.load_session(session_id)
        return dict(loaded) if loaded is not None else None

    def turn_for_client(
        self, session_id: str, client_turn_id: str
    ) -> dict[str, object] | None:
        self.turn_for_client_calls.append((session_id, client_turn_id))
        loaded = self._inner.turn_for_client(session_id, client_turn_id)
        return dict(loaded) if loaded is not None else None

    def load_turn(self, session_id: str, turn_id: str) -> dict[str, object] | None:
        loaded = self._inner.load_turn(session_id, turn_id)
        return dict(loaded) if loaded is not None else None

    def queued_turns(self, session_id: str) -> list[dict[str, object]]:
        return [dict(row) for row in self._inner.queued_turns(session_id)]

    def list_sessions(self) -> list[dict[str, object]]:
        return [dict(row) for row in self._inner.list_sessions()]

    def append_event(
        self, session_id: str, turn_id: str, *, kind: str, payload: dict[str, object]
    ) -> object:
        return self._inner.append_event(session_id, turn_id, kind=kind, payload=payload)


def test_steward_monitor_module_budgets_and_import_dag() -> None:
    root = Path(steward_monitor.__file__).resolve().parents[3]
    module_paths = [
        root / "loopx/capabilities/native_chat/steward_monitor.py",
        root / "loopx/capabilities/native_chat/steward_monitor_turns.py",
        root / "loopx/capabilities/native_chat/steward_monitor_pump.py",
        root / "loopx/capabilities/native_chat/steward_monitor_signals.py",
        root / "loopx/capabilities/native_chat/steward_monitor_config.py",
    ]
    internal_edges: dict[str, set[str]] = {}
    for path in module_paths:
        src = path.read_text(encoding="utf-8")
        lines = src.splitlines()
        limit = 650 if path.name == "steward_monitor.py" else 750
        assert len(lines) <= limit, f"{path.name} exceeded {limit} LOC ({len(lines)})"
        assert not re.search(r"#\s*type:\s*ignore", src), f"type ignore in {path.name}"
        tree = ast.parse(src, filename=str(path))
        assert not any(isinstance(n, ast.Name) and n.id == "Any" for n in ast.walk(tree))
        internal_edges[path.stem] = {
            n.module
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("steward_monitor")
        }

    assert not (
        internal_edges["steward_monitor_turns"]
        & {"steward_monitor", "steward_monitor_pump", "steward_monitor_signals", "steward_monitor_config"}
    )
    assert "steward_monitor" not in internal_edges["steward_monitor_signals"]
    assert "steward_monitor" not in internal_edges["steward_monitor_config"]

    reexport_pairs = [
        (TARGET, steward_monitor_turns.TARGET),
        (FOLLOWUP_TARGET_PREFIX, steward_monitor_turns.FOLLOWUP_TARGET_PREFIX),
        (STEWARD_BACKGROUND_SESSION_PREFIX, steward_monitor_turns.STEWARD_BACKGROUND_SESSION_PREFIX),
        (STEWARD_BACKGROUND_CHANNEL_PREFIX, steward_monitor_turns.STEWARD_BACKGROUND_CHANNEL_PREFIX),
        (
            ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS,
            steward_monitor_turns.ADVANCEMENT_PRECOMMIT_RETRY_COOLDOWN_SECONDS,
        ),
        (ADVANCEMENT_ADMISSION_RETRY_SUFFIX, steward_monitor_turns.ADVANCEMENT_ADMISSION_RETRY_SUFFIX),
        (ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX, steward_monitor_turns.ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX),
        (StewardObservationAdapter, steward_monitor_turns.StewardObservationAdapter),
        (pump_steward_monitors, steward_monitor_pump.pump_steward_monitors),
        (pump_managed_session_monitors, steward_monitor_pump.pump_managed_session_monitors),
        (actor_steward_binding_id, steward_monitor_turns.actor_steward_binding_id),
        (steward_background_session_id, steward_monitor_turns.steward_background_session_id),
        (steward_background_channel_id, steward_monitor_turns.steward_background_channel_id),
        (monitor_turn_id, steward_monitor_turns.monitor_turn_id),
        (advancement_turn_id, steward_monitor_turns.advancement_turn_id),
        (lookup_steward_monitor_turn, steward_monitor_turns.lookup_steward_monitor_turn),
        (load_steward_monitor_turn_by_id, steward_monitor_turns.load_steward_monitor_turn_by_id),
        (ensure_steward_background_session, steward_monitor_turns.ensure_steward_background_session),
        (resolve_advancement_turn_id, steward_monitor_turns.resolve_advancement_turn_id),
        (is_exhausted_zero_write_followup_todo, steward_monitor_turns.is_exhausted_zero_write_followup_todo),
        (steward_monitor_delivery_dir, steward_monitor_turns.steward_monitor_delivery_dir),
        (steward_monitor_scope_path, steward_monitor_turns.steward_monitor_scope_path),
        (steward_monitor_snapshot_path, steward_monitor_turns.steward_monitor_snapshot_path),
        (normalize_steward_monitor_scope, steward_monitor_turns.normalize_steward_monitor_scope),
        (write_steward_monitor_scope, steward_monitor_turns.write_steward_monitor_scope),
        (read_steward_monitor_scope, steward_monitor_turns.read_steward_monitor_scope),
        (_github_repo_slug, steward_monitor_signals._github_repo_slug),
        (_signal_scope_uses_stalled_age, steward_monitor_signals._signal_scope_uses_stalled_age),
        (_accepts_backfill_comments, steward_monitor_signals._accepts_backfill_comments),
    ]
    for exported, canonical in reexport_pairs:
        assert exported is canonical
    assert callable(steward_monitor_config.configure_steward_monitor)


def test_background_session_and_immutable_scope_parity(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "chat")
    proto_store = cast(StewardSessionStoreProtocol, store)
    assert actor_steward_binding_id(
        MappingProxyType({"schema_version": "loopx_steward_managed_actor_v0", "goal_id": "steward-agent-b1"})
    ) == "b1"
    assert actor_steward_binding_id(MappingProxyType({"goal_id": "steward-agent-b1"})) is None
    assert actor_steward_binding_id(
        MappingProxyType({"schema_version": "loopx_steward_managed_actor_v0", "goal_id": "steward-agent-"})
    ) is None

    parent_base = {"session_id": "sess-parent", "agent_id": "agent-1", "adapter_kind": "codex",
                   "steward_context": MappingProxyType({"binding_id": "b1"})}
    bg = ensure_steward_background_session(
        proto_store, "b1", MappingProxyType({**parent_base, "manager_authorization_scope_id": "scope-a"})
    )
    assert bg["session_id"] == steward_background_session_id("b1")
    assert bg["channel_id"] == steward_background_channel_id("b1")
    assert bg["manager_authorization_scope_id"] == "scope-a"

    # Empty string retains existing carrier value; explicit None removes the key.
    bg_empty = ensure_steward_background_session(
        proto_store, "b1", MappingProxyType({**parent_base, "manager_authorization_scope_id": ""})
    )
    assert bg_empty["manager_authorization_scope_id"] == "scope-a"
    bg_cleared = ensure_steward_background_session(
        proto_store, "b1", MappingProxyType({**parent_base, "manager_authorization_scope_id": None})
    )
    assert "manager_authorization_scope_id" not in bg_cleared

    assert normalize_steward_monitor_scope(None) is None
    default_scope = write_steward_monitor_scope(
        tmp_path / "runtime", "goal-1", "gh-watch",
        MappingProxyType({"schema_version": "steward_signal_monitor_scope_v0"}),
    )
    assert default_scope == {"schema_version": "steward_signal_monitor_scope_v0",
                             "include_portfolio": True, "include_inbox": True}
    assert read_steward_monitor_scope(tmp_path / "runtime", "goal-1", "gh-watch") == default_scope
    assert read_steward_monitor_scope(tmp_path / "runtime", "goal-1", "missing") is None

    stalled_scope = write_steward_monitor_scope(
        tmp_path / "runtime", "goal-1", "pr-watch",
        MappingProxyType({"schema_version": "steward_signal_monitor_scope_v0",
                          "include_portfolio": False, "pr_filter": {"min_stalled_hours": 12}}),
    )
    assert _signal_scope_uses_stalled_age(stalled_scope) is True
    assert _signal_scope_uses_stalled_age(MappingProxyType({"pr_filter": MappingProxyType({})})) is False
    assert _accepts_backfill_comments(lambda slug, backfill_comments=False: {}) is True
    assert _accepts_backfill_comments(lambda slug: {}) is False
    assert _github_repo_slug("  git:github.com/owner/repo  ") == "owner/repo"
    assert _github_repo_slug("https://github.com/owner/repo") is None


def test_advancement_retry_exhaustion_and_lookup_cache_efficiency(tmp_path: Path) -> None:
    store = ChatSessionStore(tmp_path / "chat")
    counting_store = _CountingTurnLookupStore(store)
    proto_store = cast(StewardSessionStoreProtocol, counting_store)
    fg = store.create_session(
        session_id="sess-fg", goal_id="loopx-manager", agent_id="steward",
        adapter_kind="codex", upstream_thread_id="thread-fg",
    )
    actor = {"schema_version": "loopx_steward_managed_actor_v0", "goal_id": "steward-agent-b1",
             "agent_id": "steward", "session_id": fg["session_id"]}
    todo = MappingProxyType({"todo_id": "todo-adv-1", "status": "open", "task_class": "advancement_task",
                             "target_key": f"{FOLLOWUP_TARGET_PREFIX}mon-1", "updated_at": "2026-11-26T00:00:00+00:00"})
    base_token = advancement_turn_id(todo)
    assert monitor_turn_id({"todo_id": "m1", "next_due_at": "2026-11-26T00:00:00+00:00"}).startswith("monitor.")

    def _record(token: str, completed_at: str, response: dict[str, object]) -> None:
        turn, _ = store.create_turn(str(fg["session_id"]), client_turn_id=native_steward_client_id(actor, token),
                                    message="follow up")
        assert store.update_turn(str(fg["session_id"]), str(turn["turn_id"]), status="completed",
                                 completed_at=completed_at, response=response) is not None

    precommit_resp: dict[str, object] = {"native_loopx": {"ok": False, "status": "failed",
        "result_kind": "host_failure", "receipt": {"commit_eligibility": {"writeback": False}},
        "effects": {"state_written": False}}}
    deferred_resp: dict[str, object] = {"native_loopx": {"ok": False,
        "error_code": "turn_todo_selection_deferred", "selection_rejection": {"state": "deferred"},
        "effects": {"host_invoked": False, "state_written": False,
                    "quota_spent": False, "scheduler_acknowledged": False}}}
    now_dt = datetime(2026, 11, 26, 1, 0, 0, tzinfo=timezone.utc)
    old = (now_dt - timedelta(seconds=120)).isoformat()

    _record(base_token, (now_dt - timedelta(seconds=10)).isoformat(), precommit_resp)
    assert resolve_advancement_turn_id(proto_store, actor, todo, now=now_dt.timestamp()) is None

    base_row = store.turn_for_client(str(fg["session_id"]), native_steward_client_id(actor, base_token))
    assert base_row is not None
    store.update_turn(str(fg["session_id"]), str(base_row["turn_id"]), completed_at=old, response=deferred_resp)
    adm_token = resolve_advancement_turn_id(proto_store, actor, todo, now=now_dt.timestamp())
    assert adm_token == base_token + ADVANCEMENT_ADMISSION_RETRY_SUFFIX

    assert adm_token is not None
    _record(adm_token, old, precommit_resp)
    pre_token = adm_token + ADVANCEMENT_PRECOMMIT_RETRY_SUFFIX
    assert resolve_advancement_turn_id(proto_store, actor, todo, now=now_dt.timestamp()) == pre_token
    _record(pre_token, old, deferred_resp)
    final_token = pre_token + ADVANCEMENT_ADMISSION_RETRY_SUFFIX
    assert resolve_advancement_turn_id(proto_store, actor, todo, now=now_dt.timestamp()) == final_token
    _record(final_token, old, precommit_resp)

    counting_store.turn_for_client_calls.clear()
    assert is_exhausted_zero_write_followup_todo(proto_store, actor, todo, now=now_dt.timestamp()) is True
    client_ids = [cid for _, cid in counting_store.turn_for_client_calls]
    assert len(client_ids) == len(set(client_ids))

    rearmed_token = resolve_advancement_turn_id(
        proto_store, actor, {**todo, "note": "rearm-followup:poll-1"}, now=now_dt.timestamp()
    )
    assert rearmed_token is not None and rearmed_token != final_token

    found = lookup_steward_monitor_turn(proto_store, "b1", str(fg["session_id"]),
                                        native_steward_client_id(actor, base_token))
    assert found is not None
    loaded = load_steward_monitor_turn_by_id(proto_store, actor, str(found["turn_id"]))
    assert loaded is not None and loaded["turn_id"] == found["turn_id"]


def test_due_monitor_pump_tries_earliest_utc_deadline_and_preserves_original_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.capabilities.native_chat.steward_turn import StewardTurnHost

    controller = _StubController(tmp_path)
    ctrl_proto = _as_controller(controller)
    session = controller.store.create_session(
        goal_id="loopx-manager", agent_id="steward",
        adapter_kind="codex", upstream_thread_id="fixture-thread",
    )
    actor = {"goal_id": "steward-agent-b-order", "agent_id": "steward", "session_id": session["session_id"]}
    controller.project_contexts.conversation_bindings.bindings_rows = [{
        "binding_id": "b-order", "enabled": True, "managed_actor": actor,
    }]
    earliest = "2000-02-03T10:00:00Z"
    todos: list[StewardTodoRecord] = [
        {"todo_id": name, "task_class": "continuous_monitor", "status": "open",
         "claimed_by": "steward", "target_key": "watch-" + name, "next_due_at": stamp}
        for name, stamp in [
            ("earlier-z", earliest),
            ("later", "2000-02-03T10:00:00.000001Z"),
            ("equal-offset", "2000-02-03T18:00:00+08:00"),
            ("earlier-a", earliest),
            ("future", "2999-01-01T00:00:00Z"),
        ]
    ]
    first_due = todos[3]
    todos.extend([
        {**first_due, "todo_id": "expired", "target_key": "watch-expired", "expires_at": "2000-02-04T00:00:00Z"},
        {**first_due, "todo_id": "foreign", "target_key": "watch-foreign", "claimed_by": "peer"},
        {**first_due, "todo_id": "managed", "target_key": "managed-session-watch.fixture"},
        {**first_due, "todo_id": "followup", "target_key": FOLLOWUP_TARGET_PREFIX + "fixture"},
    ])
    attempts: list[tuple[str, str, str, str, str]] = []

    def capture_submit(_host, binding_id, *, turn_instance_id, todo_id, work_kind, monitor_deadline):
        attempts.append((binding_id, turn_instance_id, todo_id, work_kind, monitor_deadline))
        return {"todo_id": todo_id, "status": "queued"}, True

    monkeypatch.setattr(steward_monitor, "list_goal_todos", lambda **_: {"todos": todos})
    monkeypatch.setattr(StewardTurnHost, "submit", capture_submit)
    ordered = StewardMonitor(ctrl_proto).due_monitors(actor, todos=todos)
    queued = pump_steward_monitors(ctrl_proto)

    assert ([row["todo_id"] for row in ordered], attempts) == (
        ["earlier-a", "earlier-z", "equal-offset", "later"],
        [("b-order", monitor_turn_id(first_due), "earlier-a", "monitor", earliest)],
    )
    assert queued == [{"todo_id": "earlier-a", "status": "queued"}]


def test_due_monitors_and_pump_single_todo_read_efficiency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = _StubController(tmp_path)
    ctrl_proto = _as_controller(controller)
    fg = controller.store.create_session(
        session_id="sess-fg", goal_id="loopx-manager", agent_id="steward",
        adapter_kind="codex", upstream_thread_id="thread-fg",
    )
    controller.project_contexts.conversation_bindings.bindings_rows = [{
        "binding_id": "b-eff", "enabled": True,
        "managed_actor": {"schema_version": "loopx_steward_managed_actor_v0",
                          "goal_id": "steward-agent-b-eff", "agent_id": "steward", "session_id": fg["session_id"]},
    }]
    future_due = (datetime.now(timezone.utc) + timedelta(minutes=25)).isoformat()
    past_due = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    sample_todos: list[dict[str, object]] = [
        {"todo_id": "t-target", "task_class": "continuous_monitor", "status": "open",
         "claimed_by": "steward", "target_key": TARGET, "next_due_at": future_due},
        {"todo_id": "t-due-b", "task_class": "continuous_monitor", "status": "open",
         "claimed_by": "steward", "target_key": "watch-b", "next_due_at": past_due},
        {"todo_id": "t-due-a", "task_class": "continuous_monitor", "status": "open",
         "claimed_by": "steward", "target_key": "watch-a", "next_due_at": past_due},
        {"todo_id": "t-managed", "task_class": "continuous_monitor", "status": "open",
         "claimed_by": "steward", "target_key": "managed-session-watch.s1", "next_due_at": past_due},
    ]
    read_calls = 0

    def fake_list_goal_todos(**_kwargs: object) -> dict[str, object]:
        nonlocal read_calls
        read_calls += 1
        return {"todos": list(sample_todos)}

    monkeypatch.setattr(steward_monitor, "list_goal_todos", fake_list_goal_todos)
    monitor = StewardMonitor(ctrl_proto)
    due = monitor.due_monitors({"goal_id": "steward-agent-b-eff", "agent_id": "steward"})
    assert [row["todo_id"] for row in due] == ["t-due-a", "t-due-b"]
    assert read_calls == 1

    read_calls = 0
    for row in sample_todos:
        row["next_due_at"] = future_due
    pump_steward_monitors(ctrl_proto)
    assert read_calls == 1


def test_collect_signal_monitor_key_presence_and_quiet_zero_todo_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    core_responses: dict[str, dict[str, object]] = {
        "collaboration.steward.monitor.observation": {"result_hash": "p-hash", "material_change": False},
        "collaboration.steward.monitor.signal_observation": {"result_hash": "sig-quiet", "material_change": False},
    }
    bindings = _StubBindings(tmp_path / "bindings.json", core_responses=core_responses)
    controller = _StubController(tmp_path, bindings=bindings)
    fg = controller.store.create_session(
        session_id="sess-fg", goal_id="loopx-manager", agent_id="steward",
        adapter_kind="codex", upstream_thread_id="thread-fg",
    )
    actor = {"goal_id": "steward-agent-b2", "agent_id": "steward", "session_id": fg["session_id"]}
    todo = {"todo_id": "todo-sig-1", "target_key": "repo-prs", "text": "Watch repo PRs",
            "task_repository": "git:github.com/org/repo", "priority": "P1", "result_hash": "prev-hash"}

    subprocess_calls: list[list[str]] = []

    def fake_subproc_run(argv: list[str], **_kwargs: object) -> object:
        subprocess_calls.append(list(argv))
        return type("Res", (), {"returncode": 0, "stdout": "[]", "stderr": ""})()

    monkeypatch.setattr(steward_monitor.subprocess, "run", fake_subproc_run)
    steward_monitor._observe_github_repository_prs("org/repo")
    assert subprocess_calls

    monkeypatch.setattr(
        steward_monitor, "_observe_github_repository_prs",
        lambda slug, backfill_comments=False: {"repository": slug, "backfill": backfill_comments,
                                               "items": [{"number": 11}, {"number": 12}]},
    )
    monitor = StewardMonitor(_as_controller(controller))
    open_todos: list[dict[str, object]] = []
    todo_read_count = 0

    def counting_todos(_actor: object) -> list[dict[str, object]]:
        nonlocal todo_read_count
        todo_read_count += 1
        return list(open_todos)

    monkeypatch.setattr(monitor, "todos", counting_todos)
    obs, args = monitor._collect_signal_monitor(actor, {}, todo)
    assert obs["material_change"] is False and args == [] and todo_read_count == 0
    snap_path = steward_monitor_snapshot_path(
        controller.coordination_runtime_root, "steward-agent-b2", "todo-sig-1"
    )
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    assert "signal_scope" not in snap
    assert snap["repository_snapshot"]["items"] == [{"number": 11}, {"number": 12}]

    saved_scope = write_steward_monitor_scope(
        controller.coordination_runtime_root, "steward-agent-b2", "repo-prs",
        {"schema_version": "steward_signal_monitor_scope_v0"},
    )
    # Valid sequence scoped_repository_items filters persisted items; None fails closed like baseline.
    bindings.core_responses["collaboration.steward.monitor.signal_observation"] = {
        "result_hash": "sig-changed", "material_change": True, "scoped_repository_items": [{"number": 11}],
    }
    obs2, args2 = monitor._collect_signal_monitor(actor, {}, todo)
    assert obs2["material_change"] is True and todo_read_count == 1
    assert "--next-target-key" in args2 and f"{FOLLOWUP_TARGET_PREFIX}todo-sig-1" in args2
    snap2 = json.loads(snap_path.read_text(encoding="utf-8"))
    assert snap2["signal_scope"] == saved_scope and snap2["repository_snapshot"]["items"] == [{"number": 11}]

    bindings.core_responses["collaboration.steward.monitor.signal_observation"] = {
        "result_hash": "sig-bad", "material_change": True, "scoped_repository_items": None,
    }
    with pytest.raises(TypeError):
        monitor._collect_signal_monitor(actor, {}, todo)

    bindings.core_responses["collaboration.steward.monitor.signal_observation"] = {
        "result_hash": "sig-changed", "material_change": True, "scoped_repository_items": [],
    }

    # Pre-existing open follow-up suppresses duplicate --next-target-key emission.
    open_todos.append({"target_key": f"{FOLLOWUP_TARGET_PREFIX}todo-sig-1", "status": "open"})
    _, args3 = monitor._collect_signal_monitor(actor, {}, todo)
    assert args3 == [] and todo_read_count == 2

    # Monkeypatching steward_monitor.native_command intercepts _rearm_exhausted_followup_if_needed.
    seen_rearm_cmds: list[list[str]] = []

    def fake_native_command(_ctrl: object, cmd_args: list[str]) -> dict[str, object]:
        seen_rearm_cmds.append(list(cmd_args))
        return {"ok": True}

    monkeypatch.setattr(
        steward_monitor_signals, "is_exhausted_zero_write_followup_todo", lambda *_a, **_kw: True
    )
    monkeypatch.setattr(
        monitor, "_acquire", lambda *_a, **_kw: {"lease": {"version": 3, "idempotency_key": "k3"}}
    )
    monkeypatch.setattr(monitor, "_release", lambda *_a, **_kw: None)
    monkeypatch.setattr(steward_monitor, "native_command", fake_native_command)
    open_todos[-1] = {
        "todo_id": "followup-1", "target_key": f"{FOLLOWUP_TARGET_PREFIX}todo-sig-1", "status": "open", "note": ""
    }
    assert monitor._rearm_exhausted_followup_if_needed(actor, todo, "poll-seam-1") is True
    assert any("--note" in cmd and "rearm-followup:poll-seam-1" in cmd for cmd in seen_rearm_cmds)


def test_steward_monitor_failure_modes_and_negative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bindings = _StubBindings(
        tmp_path / "bindings.json",
        core_responses={"collaboration.steward.monitor.signal_observation": {
            "result_hash": "err", "material_change": False, "observation_incomplete": True}},
    )
    controller = _StubController(tmp_path, bindings=bindings)
    monitor = StewardMonitor(_as_controller(controller))

    with pytest.raises(ValueError, match="unavailable"):
        monitor.observe(
            host=cast(StewardHostProtocol, object()),
            request={"actor": {"goal_id": "g1", "agent_id": "a1"}, "turn_instance_id": "t1"},
            turn={"session_id": "s1", "turn_id": "t1"}, context={}, todo=None,
        )

    ambiguous = [{"todo_id": "1", "target_key": TARGET}, {"todo_id": "2", "target_key": TARGET}]
    monkeypatch.setattr(steward_monitor, "list_goal_todos", lambda **_kwargs: {"todos": ambiguous})
    with pytest.raises(ValueError, match="ambiguous"):
        monitor.current({"goal_id": "g1", "agent_id": "a1"})
    with pytest.raises(ValueError, match="ambiguous"):
        monitor.due_monitors({"goal_id": "g1", "agent_id": "a1"})

    with pytest.raises(ValueError, match="missing|unavailable"):
        monitor._session_scope_for_actor({"session_id": "missing-session"})
    with pytest.raises(ValueError, match="do not invoke a model"):
        StewardObservationAdapter("thread-1").start_turn("hello")

    for bad_goal in ("", "../escape", "bad/slash"):
        with pytest.raises(ValueError):
            steward_monitor_scope_path(tmp_path, bad_goal, "valid-key")
        with pytest.raises(ValueError):
            steward_monitor_delivery_dir(tmp_path, bad_goal)
    with pytest.raises(ValueError):
        steward_monitor_scope_path(tmp_path, "goal-1", "bad key!")

    scope_path = steward_monitor_scope_path(tmp_path, "goal-1", "bad-json")
    scope_path.parent.mkdir(parents=True, exist_ok=True)
    scope_path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid persisted"):
        read_steward_monitor_scope(tmp_path, "goal-1", "bad-json")
    with pytest.raises(EffectRuntimeRejected, match="schema_version"):
        normalize_steward_monitor_scope({})

    fg = controller.store.create_session(
        session_id="sess-fg", goal_id="loopx-manager", agent_id="steward",
        adapter_kind="codex", upstream_thread_id="thread-fg",
    )
    snap_path = steward_monitor_snapshot_path(
        controller.coordination_runtime_root, "steward-agent-b3", "todo-inc"
    )
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        monitor._collect_signal_monitor(
            {"goal_id": "steward-agent-b3", "agent_id": "steward", "session_id": fg["session_id"]},
            {}, {"todo_id": "todo-inc", "target_key": "inc-watch", "text": "watch"},
        )
    assert not snap_path.exists()


class _StubManaged:
    def __init__(self, tmp_path: Path, session: dict[str, object]) -> None:
        self._tmp_path = tmp_path
        self._sess = session

    def _session(self, binding_id: str, session_id: str | None) -> dict[str, object]:
        return dict(self._sess)

    def _workspace(self, session: Mapping[str, object]) -> Path:
        return self._tmp_path

    def _goal(self, goal_id: str) -> dict[str, object]:
        return {"goal_id": goal_id}


def test_configure_steward_monitor_rejects_non_mapping_plan_todo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding_row: dict[str, object] = {
        "binding_id": "bind-cfg",
        "managed_actor": {"goal_id": "steward-agent-cfg", "agent_id": "steward"},
    }
    session_row: dict[str, object] = {
        "session_id": "sess-cfg",
        "steward_context": {"source_ref": "src-cfg", "operator_ref": "op-cfg"},
    }

    class _ConfigBindings(_StubBindings):
        def resolve(self, **_kwargs: object) -> dict[str, object]:
            return {"binding": dict(binding_row), "observation": {}}

    bindings = _ConfigBindings(tmp_path / "bindings.json", bindings_rows=[binding_row])
    controller = _StubController(tmp_path, bindings=bindings)
    monkeypatch.setattr(steward_monitor_config, "load_goal_from_registry", lambda *_a: {"status": "active"})
    managed = cast(StewardManagedMode, _StubManaged(tmp_path, session_row))

    writes: list[list[str]] = []

    def fake_run(controller: StewardControllerProtocol, argv: list[str]) -> dict[str, object]:
        del controller
        writes.append(list(argv))
        return {"ok": True, "written": True}

    def fake_current(
        actor: Mapping[str, object], *, todos: Sequence[StewardTodoRecord] | None = None
    ) -> StewardTodoRecord | None:
        del actor, todos
        return None

    def fake_acquire(
        actor: Mapping[str, object],
        todo: Mapping[str, object],
        key: str,
        *,
        expected_version: int | None = None,
    ) -> dict[str, object]:
        del actor, todo, key, expected_version
        return {"lease": {"version": 1}}

    def run_cfg(operation: str, plan_todo: object) -> dict[str, object]:
        bindings.core_responses["collaboration.steward.monitor.plan"] = {
            "plan_id": "plan-1",
            "todo": plan_todo,
        }
        return steward_monitor_config.configure_steward_monitor(
            _as_controller(controller),
            managed=managed,
            binding_id="bind-cfg",
            operation=operation,
            expected_plan_id="plan-1",
            execute=True,
            read_todos=lambda _actor: [],
            current_monitor=fake_current,
            acquire_lease=fake_acquire,
            release_lease=lambda _actor, _todo, _lease: None,
            clear_next_check_hint=lambda _binding_id: None,
            run_command=fake_run,
        )

    for bad_todo in ("bad-non-mapping", "", ["bad"], 123, False):
        for op in ("pause", "enable"):
            with pytest.raises(TypeError, match="mapping or None"):
                run_cfg(op, bad_todo)
    assert writes == []

    # Explicit None remains the only representation of "no monitor Todo" on pause.
    none_pause = run_cfg("pause", None)
    assert none_pause == {"ok": True, "applied": True, "plan": {"plan_id": "plan-1", "todo": None}}
    assert writes == []


def test_steward_pump_failure_backoff_dedup_and_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import errno
    import logging
    from loopx.capabilities.native_chat import steward_reports as reports_mod

    clock = {"now": 1000.0}
    monkeypatch.setattr(steward_monitor_pump.time, "monotonic", lambda: clock["now"])
    base_utc = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    class _FakeDateTime:
        @staticmethod
        def now(tz: timezone | None = None) -> datetime:
            dt = base_utc + timedelta(seconds=clock["now"] - 1000.0)
            return dt if tz is not None else dt.replace(tzinfo=None)

        @staticmethod
        def fromisoformat(value: str) -> datetime:
            return datetime.fromisoformat(value)

    monkeypatch.setattr(steward_monitor_pump, "datetime", _FakeDateTime)

    store = ChatSessionStore(tmp_path / "chat")
    session = store.create_session(
        goal_id="steward-agent-b-backoff",
        agent_id="steward",
        adapter_kind="codex-app-server",
        upstream_thread_id="th-backoff",
    )
    binding = {
        "binding_id": "b-backoff",
        "enabled": True,
        "managed_actor": {"goal_id": "steward-agent-b-backoff", "agent_id": "steward", "session_id": session["session_id"]},
    }
    bindings = _StubBindings(tmp_path / "bindings.json", bindings_rows=[binding])
    controller = _StubController(tmp_path, bindings=bindings)
    controller.store = store

    monkeypatch.setattr(
        steward_monitor_pump,
        "_report_config_for_goal",
        lambda _ctrl, _gid: {"enabled": True, "profile_preset": "portfolio-progress"},
    )

    frontier_calls: list[float] = []
    report_mode: dict[str, object] = {
        "exc": PermissionError(
            errno.EACCES,
            "Permission denied",
            "/Users/alice/dir with spaces/secret file.json",
        ),
    }

    class FakeReports:
        def __init__(self, _ctrl: object) -> None:
            pass

        def frontier(self, _binding: Mapping[str, object]) -> tuple[dict[str, object] | None, bool]:
            frontier_calls.append(clock["now"])
            exc = report_mode["exc"]
            if isinstance(exc, BaseException):
                raise exc
            return None, False

    monkeypatch.setattr(reports_mod, "StewardReports", FakeReports)
    soon_due = (base_utc + timedelta(seconds=15)).isoformat()
    todo_row: StewardTodoRecord = {
        "todo_id": "todo-soon",
        "task_class": "continuous_monitor",
        "status": "open",
        "target_key": TARGET,
        "next_due_at": soon_due,
    }
    monkeypatch.setattr(StewardMonitor, "todos", lambda _self, _actor: [todo_row])
    monkeypatch.setattr(StewardMonitor, "due_monitors", lambda _self, _actor, *, todos=None: [])
    monkeypatch.setattr(StewardMonitor, "runnable_advancement_todos", lambda _self, _actor, *, todos=None: [])

    caplog.set_level(logging.WARNING, logger=steward_monitor_pump.__name__)
    # First failure at t=1000 logs once with allowlisted diagnostics (no path text even with spaces)
    # and sets 60s report backoff (until 1060).
    # _schedule_soonest_monitor_backoff sets binding next-check to t=1015 (15s remaining).
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert frontier_calls == [1000.0]
    assert len(caplog.records) == 1
    msg0 = caplog.records[0].getMessage()
    assert "An original steward report is unavailable" in msg0
    assert "stage=steward_report" in msg0
    assert "error=PermissionError" in msg0
    assert "errno=EACCES" in msg0
    for leaked in ("Users", "alice", "dir with spaces", "secret", "Permission denied", "/"):
        assert leaked not in msg0

    # Simulate human busy -> idle flip (`fg_checked=False`) at t=1010 AND soon-due wake at t=1020:
    # foreground fast-path and _schedule_soonest_monitor_backoff must NOT bypass report failure backoff.
    controller._steward_monitor_fg_idle_checked["b-backoff"] = False  # type: ignore[attr-defined]
    clock["now"] = 1010.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    clock["now"] = 1020.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert frontier_calls == [1000.0]
    assert len(caplog.records) == 1

    # At t=1081 (past binding check 1020+60 and report 60s backoff), frontier retries -> backs off 120s (until 1201),
    # and identical exception signature emits zero duplicate WARNING logs.
    clock["now"] = 1081.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert frontier_calls == [1000.0, 1081.0]
    assert len(caplog.records) == 1

    # Even with fg_checked=False flip inside 120s window (t=1150 -> binding check 1210), frontier stays backed off.
    clock["now"] = 1150.0
    controller._steward_monitor_fg_idle_checked["b-backoff"] = False  # type: ignore[attr-defined]
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert frontier_calls == [1000.0, 1081.0]

    # At t=1211 (past 120s backoff 1201 and binding check 1210), a different exception emits one transition warning (240s -> 1451).
    report_mode["exc"] = RuntimeError("sensitive text /tmp/never logged/here")
    clock["now"] = 1211.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert frontier_calls == [1000.0, 1081.0, 1211.0]
    assert len(caplog.records) == 2
    msg1 = caplog.records[1].getMessage()
    assert "stage=steward_report, error=RuntimeError" in msg1
    assert "sensitive text" not in msg1
    assert "never logged" not in msg1

    clock["now"] = 1440.0
    controller._steward_monitor_fg_idle_checked["b-backoff"] = False  # type: ignore[attr-defined]
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert frontier_calls == [1000.0, 1081.0, 1211.0]

    # Explicit clear resets backoff & dedup signature; recovery also clears for next transition.
    steward_monitor_pump.clear_steward_monitor_check_hints(_as_controller(controller), "b-backoff")
    report_mode["exc"] = None
    clock["now"] = 1441.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert frontier_calls == [1000.0, 1081.0, 1211.0, 1441.0]

    report_mode["exc"] = ValueError("provider verification failed at /Users/alice/.loopx/private-state")
    steward_monitor_pump.clear_steward_monitor_check_hints(_as_controller(controller), "b-backoff")
    clock["now"] = 1442.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert len(caplog.records) == 3


def test_steward_pump_monitor_outer_and_managed_session_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    from loopx.capabilities.native_chat import (
        session_calendar_tasks as cal_mod,
        steward_managed_mode as managed_mod,
        steward_reports as reports_mod,
        steward_turn as turn_mod,
    )

    clock = {"now": 2000.0}
    monkeypatch.setattr(steward_monitor_pump.time, "monotonic", lambda: clock["now"])

    store = ChatSessionStore(tmp_path / "chat")
    s_bad = store.create_session(
        goal_id="steward-agent-b-bad", agent_id="steward", adapter_kind="codex-app-server", upstream_thread_id="th-bad"
    )
    s_good = store.create_session(
        goal_id="steward-agent-b-good", agent_id="steward", adapter_kind="codex-app-server", upstream_thread_id="th-good"
    )
    s_managed = store.create_session(
        goal_id="goal-managed-1", agent_id="codex", adapter_kind="codex-app-server",
        upstream_thread_id="th-managed",
    )
    monkeypatch.setattr(
        store,
        "list_sessions",
        lambda: [s_bad, s_good, {**s_managed, "managed_agent_id": "agent-1"}],
    )
    bindings = _StubBindings(
        tmp_path / "bindings.json",
        bindings_rows=[
            {"binding_id": "b-bad", "enabled": True, "managed_actor": {"goal_id": "steward-agent-b-bad", "agent_id": "steward", "session_id": s_bad["session_id"]}},
            {"binding_id": "b-good", "enabled": True, "managed_actor": {"goal_id": "steward-agent-b-good", "agent_id": "steward", "session_id": s_good["session_id"]}},
        ],
    )
    controller = _StubController(tmp_path, bindings=bindings)
    controller.store = store

    due_fail: StewardTodoRecord = {
        "todo_id": "m-fail", "task_class": "continuous_monitor", "status": "open",
        "target_key": TARGET, "next_due_at": "2020-01-01T00:00:00Z",
    }
    due_ok: StewardTodoRecord = {
        "todo_id": "m-ok", "task_class": "continuous_monitor", "status": "open",
        "target_key": TARGET, "next_due_at": "2020-01-01T00:00:00Z",
    }
    managed_soon = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    managed_watch: StewardTodoRecord = {
        "todo_id": "w-1", "task_class": "continuous_monitor", "status": "open",
        "target_key": f"managed-session-watch.{s_managed['session_id']}", "next_due_at": managed_soon,
    }

    todo_calls_bad: list[float] = []

    def fake_todos(_self: object, actor: Mapping[str, object]) -> list[StewardTodoRecord]:
        gid = str(actor.get("goal_id") or "")
        if gid == "steward-agent-b-bad":
            todo_calls_bad.append(clock["now"])
            raise RuntimeError("CanonicalAuthorityRequired: migrate goal")
        if gid == "goal-managed-1":
            return [managed_watch]
        return [due_fail, due_ok]

    submit_calls: list[str] = []

    class FakeHost:
        def __init__(self, _ctrl: object) -> None:
            pass

        @staticmethod
        def background_session_id(bid: str) -> str:
            return f"steward.background.{bid}"

        def submit(self, _bid: str, *, todo_id: str, **_kw: object) -> tuple[dict[str, object], bool]:
            submit_calls.append(todo_id)
            if todo_id == "m-fail":
                raise ValueError("monitor identity unavailable at /tmp/private/sub")
            return {"turn_id": f"turn-{todo_id}", "status": "queued"}, True

    monkeypatch.setattr(StewardMonitor, "todos", fake_todos)
    monkeypatch.setattr(StewardMonitor, "due_monitors", lambda _self, _actor, *, todos=None: [due_fail, due_ok])
    monkeypatch.setattr(StewardMonitor, "runnable_advancement_todos", lambda _self, _actor, *, todos=None: [])
    monkeypatch.setattr(turn_mod, "StewardTurnHost", FakeHost)

    monkeypatch.setattr(
        steward_monitor_pump,
        "_report_config_for_goal",
        lambda _ctrl, gid: (
            {"enabled": True, "profile_preset": "weekly-progress", "route_ref": f"managed-session:{s_managed['session_id']}"}
            if gid == "goal-managed-1" else {}
        ),
    )

    class FakeManagedTurn:
        def __init__(self, _ctrl: object) -> None:
            pass

        def _request(self, sid: str, aid: str, _op: str) -> dict[str, object]:
            return {"actor": {"goal_id": "goal-managed-1", "agent_id": aid, "session_id": sid}}

        def admit(self, _req: Mapping[str, object]) -> None:
            return None

    managed_report_calls: list[float] = []

    class FakeManagedReports:
        def __init__(self, _ctrl: object) -> None:
            pass

        def frontier(self, _sid: str) -> tuple[dict[str, object] | None, bool]:
            managed_report_calls.append(clock["now"])
            raise ValueError("managed session report auth expired")

    monkeypatch.setattr(managed_mod, "ManagedSessionTurn", FakeManagedTurn)
    monkeypatch.setattr(reports_mod, "ManagedSessionReports", FakeManagedReports)
    monkeypatch.setattr(cal_mod, "SessionCalendarTasks", FakeManagedReports)
    monkeypatch.setattr(steward_monitor_pump, "monitor_todo_is_due", lambda _todo: False)

    caplog.set_level(logging.WARNING, logger=steward_monitor_pump.__name__)
    out = steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    # Healthy second binding's second due monitor still ran even though b-bad & m-fail failed!
    assert [row["turn_id"] for row in out] == ["turn-m-ok"]
    assert todo_calls_bad == [2000.0]
    assert submit_calls == ["m-fail", "m-ok"]
    assert managed_report_calls == [2000.0]
    assert len(caplog.records) == 3

    # Clear b-good's normal check cache at t=2006s and simulate b-good background now idle, plus check b-bad/m-fail/managed backoff:
    # Even at t=2006s (past the 5s managed watch remaining window), managed_report_calls stays blocked for 60s!
    controller._steward_monitor_next_check.pop("b-good", None)  # type: ignore[attr-defined]
    clock["now"] = 2006.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert todo_calls_bad == [2000.0]
    assert submit_calls == ["m-fail", "m-ok", "m-ok"]
    assert managed_report_calls == [2000.0]
    assert len(caplog.records) == 3

    # After 60s backoff expires (t=2062), failing lanes retry once without duplicate warnings, doubling to 120s.
    controller._steward_monitor_next_check.pop("b-good", None)  # type: ignore[attr-defined]
    clock["now"] = 2062.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(controller))
    assert todo_calls_bad == [2000.0, 2062.0]
    assert submit_calls == ["m-fail", "m-ok", "m-ok", "m-fail", "m-ok"]
    assert managed_report_calls == [2000.0, 2062.0]
    assert len(caplog.records) == 3


def test_managed_session_watch_failure_does_not_starve_report_and_witness_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    from loopx.capabilities.native_chat import (
        steward_managed_mode as managed_mod,
        steward_reports as reports_mod,
    )

    clock = {"now": 1000.0}
    monkeypatch.setattr(steward_monitor_pump.time, "monotonic", lambda: clock["now"])

    store = ChatSessionStore(tmp_path / "chat")
    s_managed = store.create_session(
        goal_id="goal-managed-watch", agent_id="codex", adapter_kind="codex-app-server",
        upstream_thread_id="th-watch",
    )
    monkeypatch.setattr(
        store,
        "list_sessions",
        lambda: [{**s_managed, "managed_agent_id": "agent-1"}],
    )
    bindings = _StubBindings(tmp_path / "bindings.json", bindings_rows=[])
    controller = _StubController(tmp_path, bindings=bindings)
    controller.store = store

    monkeypatch.setattr(
        steward_monitor_pump,
        "_report_config_for_goal",
        lambda _ctrl, _gid: {
            "enabled": True,
            "profile_preset": "weekly-progress",
            "route_ref": f"managed-session:{s_managed['session_id']}",
        },
    )

    watch_todo: StewardTodoRecord = {
        "todo_id": "w-due", "task_class": "continuous_monitor", "status": "open",
        "target_key": f"managed-session-watch.{s_managed['session_id']}",
        "next_due_at": "2020-01-01T00:00:00Z",
    }
    monkeypatch.setattr(StewardMonitor, "todos", lambda _self, _actor: [watch_todo])
    monkeypatch.setattr(steward_monitor_pump, "monitor_todo_is_due", lambda _todo: True)

    watch_calls: list[float] = []
    report_calls: list[float] = []
    report_ready = {"created": False}

    class FakeManagedTurn:
        def __init__(self, _ctrl: object) -> None:
            pass

        def _request(self, sid: str, aid: str, _op: str) -> dict[str, object]:
            return {"actor": {"goal_id": "goal-managed-watch", "agent_id": aid, "session_id": sid}}

        def admit(self, _req: Mapping[str, object]) -> None:
            return None

        def submit(self, _sid: str, **_kw: object) -> tuple[dict[str, object], bool]:
            watch_calls.append(clock["now"])
            raise RuntimeError("watch submission unavailable /Users/private/dir with spaces")

    class FakeManagedReports:
        def __init__(self, _ctrl: object) -> None:
            pass

        def frontier(self, _sid: str) -> tuple[dict[str, object] | None, bool]:
            report_calls.append(clock["now"])
            if report_ready["created"]:
                return {"turn_id": "report-turn-1122"}, True
            return None, False

    monkeypatch.setattr(managed_mod, "ManagedSessionTurn", FakeManagedTurn)
    monkeypatch.setattr(reports_mod, "ManagedSessionReports", FakeManagedReports)

    caplog.set_level(logging.WARNING, logger=steward_monitor_pump.__name__)
    # t=1000: watch fails -> 60s watch backoff (until 1060).
    assert steward_monitor_pump.pump_managed_session_monitors(_as_controller(controller)) == []
    assert watch_calls == [1000.0]
    assert report_calls == [1000.0]
    assert len(caplog.records) == 1
    assert "stage=managed_session_watch, error=RuntimeError" in caplog.records[0].getMessage()
    assert "dir with spaces" not in caplog.records[0].getMessage()

    # t=1061: watch retries and fails -> 120s watch backoff (until 1181), while session normal check is +60s (1121).
    clock["now"] = 1061.0
    assert steward_monitor_pump.pump_managed_session_monitors(_as_controller(controller)) == []
    assert watch_calls == [1000.0, 1061.0]
    assert report_calls == [1000.0, 1061.0]
    assert len(caplog.records) == 1

    # t=1122: healthy weekly-progress report becomes ready inside watch's 120s backoff and MUST still submit!
    report_ready["created"] = True
    clock["now"] = 1122.0
    out = steward_monitor_pump.pump_managed_session_monitors(_as_controller(controller))
    assert [row["turn_id"] for row in out] == ["report-turn-1122"]
    assert watch_calls == [1000.0, 1061.0]
    assert report_calls == [1000.0, 1061.0, 1122.0]

    # Verify outer binding storage witness: corrupted session stays backed off while untouched,
    # then recovers immediately on repair without clearing _steward_monitor_next_check.
    session_path = store._session_path(s_managed["session_id"])
    original_bytes = session_path.read_bytes()
    b_sid = store.create_session(
        goal_id="steward-agent-b-witness", agent_id="steward", adapter_kind="codex-app-server",
        upstream_thread_id="th-witness",
    )
    b_path = store._session_path(b_sid["session_id"])
    b_original = b_path.read_bytes()
    b_bindings = _StubBindings(
        tmp_path / "b-bindings.json",
        bindings_rows=[{
            "binding_id": "b-witness",
            "enabled": True,
            "managed_actor": {"goal_id": "steward-agent-b-witness", "agent_id": "steward", "session_id": b_sid["session_id"]},
        }],
    )
    b_ctrl = _StubController(tmp_path, bindings=b_bindings)
    b_ctrl.store = store
    monkeypatch.setattr(store, "list_sessions", lambda: [])
    b_path.write_bytes(b"\xff")
    load_calls: list[str] = []
    orig_load = store.load_session

    def tracked_load(sid: str) -> dict[str, object] | None:
        load_calls.append(sid)
        return orig_load(sid)

    monkeypatch.setattr(store, "load_session", tracked_load)
    monkeypatch.setattr(StewardMonitor, "todos", lambda _self, _actor: [])
    monkeypatch.setattr(StewardMonitor, "due_monitors", lambda _self, _actor, *, todos=None: [])
    monkeypatch.setattr(StewardMonitor, "runnable_advancement_todos", lambda _self, _actor, *, todos=None: [])

    clock["now"] = 2000.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(b_ctrl))
    assert load_calls == [b_sid["session_id"]]
    # Same tick/unchanged corrupted file at t=2003 stays backed off without re-reading disk JSON.
    clock["now"] = 2003.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(b_ctrl))
    assert load_calls == [b_sid["session_id"]]
    # Repairing the file on disk invalidates the witness immediately at t=2004 without waiting 60s.
    b_path.write_bytes(b_original)
    clock["now"] = 2004.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(b_ctrl))
    assert len(load_calls) >= 2
    assert session_path.read_bytes() == original_bytes


def test_witness_backoff_stat_count_stays_o1_across_historical_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(steward_monitor_pump.time, "monotonic", lambda: clock["now"])
    orig_stat = Path.stat
    orig_scandir = os.scandir

    turn_dir_stat_counts: list[int] = []
    total_stat_counts: list[int] = []
    for history_count in (32, 128, 512):
        clock["now"] += 100.0
        root = tmp_path / f"hist-{history_count}"
        bindings = _StubBindings(root / "bindings.json", bindings_rows=[])
        ctrl = _StubController(root, bindings=bindings)
        store = ctrl.store
        created = store.create_session(
            goal_id="steward-goal-o1", agent_id="steward", adapter_kind="codex-app-server",
            upstream_thread_id=f"th-{history_count}",
        )
        sid = str(created["session_id"])
        turns_dir = store._session_dir(sid) / "turns"
        turns_dir.mkdir(parents=True, exist_ok=True)
        for idx in range(history_count):
            tid = f"turn-hist-{idx:04d}"
            (turns_dir / f"{tid}.json").write_text(
                json.dumps({
                    "schema_version": "loopx_chat_turn_state_v1",
                    "session_id": sid, "turn_id": tid, "status": "completed",
                }),
                encoding="utf-8",
            )
            (turns_dir / f"{tid}.events.json").write_text("[]", encoding="utf-8")

        queued, _ = store.create_queued_turn(
            sid, client_turn_id=f"q-{history_count}", message="keep queued", origin="web"
        )
        q_path = store._turn_path(sid, str(queued["turn_id"]))
        orig_q_bytes = q_path.read_bytes()
        q_path.write_bytes(b"\xff")

        bindings.bindings_rows = [{
            "binding_id": f"b-o1-{history_count}", "enabled": True,
            "managed_actor": {"goal_id": "steward-goal-o1", "agent_id": "steward", "session_id": sid},
        }]
        monkeypatch.setattr(store, "list_sessions", lambda: [])
        monkeypatch.setattr(StewardMonitor, "todos", lambda _self, _actor: [])
        monkeypatch.setattr(StewardMonitor, "due_monitors", lambda _self, _actor, *, todos=None: [])
        monkeypatch.setattr(StewardMonitor, "runnable_advancement_todos", lambda _self, _actor, *, todos=None: [])

        load_calls: list[str] = []
        orig_load = store.load_session

        def tracked_load(target_sid: str) -> dict[str, object] | None:
            load_calls.append(target_sid)
            return cast("dict[str, object] | None", orig_load(target_sid))

        monkeypatch.setattr(store, "load_session", tracked_load)

        # Initial failure populates O(1) witness (no scandir of historical turns).
        steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
        initial_loads = len(load_calls)
        assert initial_loads >= 1

        stat_paths: list[Path] = []
        scandir_targets: list[object] = []

        def tracked_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
            stat_paths.append(self)
            return orig_stat(self, follow_symlinks=follow_symlinks)

        def tracked_scandir(path: str | bytes | os.PathLike[str] | os.PathLike[bytes] = ".") -> object:
            scandir_targets.append(path)
            return orig_scandir(path)

        with monkeypatch.context() as mc:
            mc.setattr(Path, "stat", tracked_stat)
            mc.setattr(os, "scandir", tracked_scandir)
            clock["now"] += 3.0
            steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))

        assert len(load_calls) == initial_loads
        assert scandir_targets == []
        turn_stats = [p for p in stat_paths if p == turns_dir or p.parent == turns_dir]
        assert q_path in turn_stats
        assert not any(p.name.startswith("turn-hist-") for p in turn_stats)
        turn_dir_stat_counts.append(len(turn_stats))
        total_stat_counts.append(len(stat_paths))

        # In-place repair of ONLY the broken queued turn clears backoff immediately.
        q_path.write_bytes(orig_q_bytes)
        clock["now"] += 1.0
        steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
        assert len(load_calls) > initial_loads

    assert len(set(turn_dir_stat_counts)) == 1
    assert len(set(total_stat_counts)) == 1


def _write_test_registry_envelope(path: Path, goals: list[dict[str, object]]) -> None:
    payload: dict[str, object] = {"goals": goals}
    canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope: list[object] = [
        {
            "schema_version": "loopx_project_registry_envelope_v1",
            "minimum_writer_protocol": "goal_instance_v1",
            "payload_sha256": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        },
        payload,
    ]
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_witness_backoff_ignores_unrelated_goal_and_binding_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(steward_monitor_pump.time, "monotonic", lambda: clock["now"])

    store = ChatSessionStore(tmp_path / "chat")
    created = store.create_session(
        goal_id="steward-goal-target", agent_id="steward", adapter_kind="codex-app-server",
        upstream_thread_id="th-target",
    )
    sid = str(created["session_id"])
    monkeypatch.setattr(store, "list_sessions", lambda: [])

    target_goal: dict[str, object] = {"id": "steward-goal-target", "description": "target-v1"}
    unrelated_goal: dict[str, object] = {"id": "steward-goal-other", "description": "other-v1"}
    b_bad: dict[str, object] = {
        "binding_id": "b-bad", "enabled": True,
        "managed_actor": {"goal_id": "steward-goal-target", "agent_id": "steward", "session_id": sid},
    }
    b_other: dict[str, object] = {
        "binding_id": "b-other", "enabled": False,
        "managed_actor": {"goal_id": "steward-goal-other", "agent_id": "steward", "session_id": "s-other"},
    }
    bindings = _StubBindings(tmp_path / "bindings.json", bindings_rows=[b_bad, b_other])
    ctrl = _StubController(tmp_path, bindings=bindings)
    ctrl.store = store
    _write_test_registry_envelope(ctrl.registry_path, [target_goal, unrelated_goal])

    todos_calls: list[float] = []

    def failing_todos(_self: StewardMonitor, _actor: Mapping[str, object]) -> list[StewardTodoRecord]:
        todos_calls.append(clock["now"])
        raise RuntimeError("transient-store-err")

    monkeypatch.setattr(StewardMonitor, "todos", failing_todos)

    # t=1000: failure enters 60s backoff until t=1060.
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert todos_calls == [1000.0]

    # t=1003: edit an unrelated Goal in registry.json AND an unrelated binding -> backoff MUST remain active.
    unrelated_goal["description"] = "other-v2"
    _write_test_registry_envelope(ctrl.registry_path, [target_goal, unrelated_goal])
    b_other["notes"] = "unrelated-binding-edited"
    clock["now"] = 1003.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert todos_calls == [1000.0]

    # t=1004: edit ONLY the target Goal in registry.json -> witness invalidates backoff immediately.
    target_goal["description"] = "target-v2"
    _write_test_registry_envelope(ctrl.registry_path, [target_goal, unrelated_goal])
    clock["now"] = 1004.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert todos_calls == [1000.0, 1004.0]

    # t=1005: another unrelated binding edit stays ignored.
    b_other["notes"] = "unrelated-binding-edited-again"
    clock["now"] = 1005.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert todos_calls == [1000.0, 1004.0]

    # t=1006: editing the target binding itself invalidates backoff immediately.
    b_bad["notes"] = "target-binding-updated"
    clock["now"] = 1006.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert todos_calls == [1000.0, 1004.0, 1006.0]


def test_registry_goal_witness_cache_reuses_snapshot_across_four_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    clock = {"now": 1000.0}
    monkeypatch.setattr(time, "monotonic", lambda: clock["now"])

    bindings = _StubBindings(tmp_path / "bindings.json", bindings_rows=[])
    ctrl = _StubController(tmp_path, bindings=bindings)
    store = ctrl.store
    monkeypatch.setattr(store, "list_sessions", lambda: [])

    target_goals: list[dict[str, object]] = []
    unrelated_goals: list[dict[str, object]] = [
        {"id": f"goal-extra-{idx:03d}", "description": f"extra-{idx}"}
        for idx in range(64)
    ]
    corrupt_sids: list[str] = []
    for idx in range(4):
        gid = f"goal-target-{idx}"
        target_goals.append({"id": gid, "description": f"target-{idx}-v1"})
        created = store.create_session(
            goal_id=gid, agent_id="steward", adapter_kind="codex-app-server",
            upstream_thread_id=f"th-r4-{idx}",
        )
        sid = str(created["session_id"])
        corrupt_sids.append(sid)
        store._session_path(sid).write_bytes(b"\xff")
        bindings.bindings_rows.append({
            "binding_id": f"b-r4-{idx}",
            "enabled": True,
            "managed_actor": {"goal_id": gid, "agent_id": "steward", "session_id": sid},
        })

    _write_test_registry_envelope(ctrl.registry_path, [*target_goals, *unrelated_goals])

    load_calls: list[str] = []
    orig_load = store.load_session

    def tracked_load(target_sid: str) -> dict[str, object] | None:
        load_calls.append(target_sid)
        return cast("dict[str, object] | None", orig_load(target_sid))

    monkeypatch.setattr(store, "load_session", tracked_load)

    reg_read_bytes: list[int] = []
    orig_read_bytes = Path.read_bytes

    def tracked_read_bytes(self: Path) -> bytes:
        data = orig_read_bytes(self)
        if self == ctrl.registry_path:
            reg_read_bytes.append(len(data))
        return data

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    # Initial failing tick across 4 corrupt sessions reads registry.json at most once.
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert load_calls == corrupt_sids
    assert len(reg_read_bytes) == 1

    # Over 4 unchanged idle ticks, registry.json is read 0 times (0 bytes) with 0 premature retries.
    reg_read_bytes.clear()
    before_idle = len(load_calls)
    for _ in range(4):
        clock["now"] += 3.0
        steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert reg_read_bytes == []
    assert len(load_calls) == before_idle

    # Mutating an unrelated Goal reloads registry.json at most once and preserves all 4 active backoffs.
    unrelated_goals[0]["description"] = "extra-0-edited"
    _write_test_registry_envelope(ctrl.registry_path, [*target_goals, *unrelated_goals])
    clock["now"] += 3.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert len(reg_read_bytes) == 1
    assert len(load_calls) == before_idle
    clock["now"] += 3.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert len(reg_read_bytes) == 1
    assert len(load_calls) == before_idle

    # Mutating target Goal #2 reloads once and immediately invalidates ONLY binding #2's witness.
    reg_read_bytes.clear()
    target_goals[2]["description"] = "target-2-edited"
    _write_test_registry_envelope(ctrl.registry_path, [*target_goals, *unrelated_goals])
    clock["now"] += 3.0
    steward_monitor_pump.pump_steward_monitors(_as_controller(ctrl))
    assert len(reg_read_bytes) == 1
    assert load_calls[before_idle:] == [corrupt_sids[2]]
