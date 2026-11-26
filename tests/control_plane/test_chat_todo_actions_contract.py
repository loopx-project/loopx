"""Contract, negative-path, and parity tests for Zero-Any Chat Todo action modules."""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import importlib.util
import io
from pathlib import Path
import tokenize
from types import MappingProxyType

import pytest

from loopx.chat import todo_action_contracts, todo_action_create, todo_action_tasks, todo_action_updates, todo_actions
from loopx.control_plane.collaboration.action_store_common import ActionConflictError, _canonical_digest
from loopx.chat.todo_action_contracts import (
    TodoRuntimeControllerProtocol,
    TodoTurnLoaderProtocol,
)
from loopx.chat.todo_actions import ChatTodoActionMixin, _decision_readback
from loopx.chat.todo_source import ChatTodoSource, ChatTodoSourceChanged
from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
from loopx.control_plane.todos.completion_validation import COMPLETION_VALIDATION_IN_PROGRESS

_MODULE_PATHS = (
    "loopx/chat/todo_actions.py",
    "loopx/chat/todo_action_contracts.py",
    "loopx/chat/todo_action_tasks.py",
    "loopx/chat/todo_action_create.py",
    "loopx/chat/todo_action_updates.py",
)


def _as_map(value: object) -> Mapping[str, object]:
    assert isinstance(value, Mapping)
    return value


@dataclass
class _StubActionStore:
    applied: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)
    checkpoints: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)
    failed: list[tuple[str, str, str, dict[str, object]]] = field(default_factory=list)

    def apply(
        self,
        proposal_id: str,
        *,
        current_state_fingerprint: str,
        receipt: Mapping[str, object],
    ) -> dict[str, object]:
        payload = dict(receipt)
        self.applied.append((proposal_id, current_state_fingerprint, payload))
        return {
            "proposal_id": proposal_id,
            "status": "applied" if payload else "stale",
            "expected_state_fingerprint": current_state_fingerprint,
            "receipt": payload,
        }

    def save_checkpoint(
        self,
        proposal_id: str,
        *,
        step: str,
        receipt: Mapping[str, object],
    ) -> dict[str, object]:
        payload = dict(receipt)
        self.checkpoints.append((proposal_id, step, payload))
        return {"proposal_id": proposal_id, "step": step, "receipt": payload}

    def mark_failed(
        self,
        proposal_id: str,
        *,
        error_code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        detail_dict = dict(details or {})
        self.failed.append((proposal_id, error_code, message, detail_dict))
        return {
            "proposal_id": proposal_id,
            "status": "failed",
            "failure": {"error_code": error_code, "message": message, "details": detail_dict},
        }


class _StubRuntimeController:
    def __init__(self, store: TodoTurnLoaderProtocol | None = None) -> None:
        self.store = store

    def capabilities(self) -> Sequence[Mapping[str, object]]:
        return ()

    def open_session(
        self,
        *,
        goal_id: str,
        goal_instance_id: str | None = None,
        agent_id: str,
        work_dir: Path,
        objective: str,
        mode: str,
        session_id: str | None = None,
        channel_id: str,
        agent_goal_id: str,
        managed_agent_id: str = "",
    ) -> tuple[dict[str, object], bool]:
        return {"session_id": session_id or "sess-stub"}, True

    def submit_turn(
        self,
        *,
        session_id: str,
        client_turn_id: str,
        message: str,
        work_dir: Path,
        objective: str,
        display_message: str = "",
    ) -> tuple[dict[str, object], bool]:
        return {"session_id": session_id, "turn_id": "turn-stub"}, True

    def resume_session_queue(self, *, session_id: str, work_dir: Path, objective: str) -> object:
        return None


class _StubTodoHost(ChatTodoActionMixin):
    def __init__(
        self,
        *,
        registry_path: Path,
        store: _StubActionStore | None = None,
        chat_store: TodoTurnLoaderProtocol | None = None,
        runtime_controller: TodoRuntimeControllerProtocol | None = None,
        monitor_runner: Callable[..., Mapping[str, object]] | None = None,
    ) -> None:
        self.store = store or _StubActionStore()
        self.registry_path = registry_path
        self.chat_store = chat_store
        self.runtime_controller = runtime_controller
        self.runtime_root_override: str | None = None
        self.monitor_runner = monitor_runner
        self.fingerprint = "fp-1"

    def _goal_state_fingerprint(self, goal_id: str) -> str:
        return self.fingerprint

    def _agent_eligibility(
        self,
        agent_id: str,
        *,
        project: Path | None = None,
        requires_tools: bool = True,
        require_healthy: bool = True,
    ) -> dict[str, object]:
        return {"agent_id": agent_id, "available": True}

    def _resolve_goal_agent(
        self,
        goal_id: str,
        endpoint_id: str,
        *,
        goal: dict[str, object] | None = None,
    ) -> str:
        return endpoint_id

    def _turn_from_receipt(self, receipt: object) -> dict[str, object] | None:
        return {"turn_id": "turn-from-receipt"} if isinstance(receipt, Mapping) else None

    def _run_monitor_update(
        self,
        parameters: dict[str, object],
        *,
        dry_run: bool,
        basis: dict[str, object] | None = None,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        if self.monitor_runner is not None:
            return dict(self.monitor_runner(parameters, dry_run=dry_run, basis=basis, operation_id=operation_id))
        raise AssertionError("monitor runner was not configured")


def test_chat_todo_actions_modularization_and_layout_pin() -> None:
    root = Path(__file__).resolve().parents[2]
    all_paths = (*_MODULE_PATHS, "tests/control_plane/test_chat_todo_actions_contract.py")
    for rel_path in all_paths:
        text = (root / rel_path).read_text(encoding="utf-8")
        lines = text.splitlines()
        cap = 550 if rel_path == "loopx/chat/todo_actions.py" else 650
        assert len(lines) <= cap, f"{rel_path} exceeded LOC budget ({len(lines)} > {cap})"
        comments = [
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(text).readline)
            if tok.type == tokenize.COMMENT
        ]
        assert not any("type: ignore" in comment for comment in comments), f"{rel_path} must stay free of type: ignore"
        assert not any("noqa" in comment for comment in comments), f"{rel_path} must stay free of noqa"
        tree = ast.parse(text)
        any_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Any"]
        assert any_nodes == [], f"{rel_path} must stay Zero-Any"
        if rel_path.startswith("loopx/chat/"):
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    span = (node.end_lineno or node.lineno) - node.lineno + 1
                    assert span <= 85, f"{rel_path}:{node.name} span too large ({span})"

    assert ChatTodoActionMixin.__module__ == "loopx.chat.todo_actions"
    assert importlib.util.find_spec("loopx.chat_todo_actions") is None
    assert callable(_decision_readback)
    for attr in (
        "_todo_checkpoints",
        "_apply_todo_create",
        "_apply_created_todo_assignment",
        "_confirmed_task_commit",
        "_read_task_start",
        "_pending_applied_task_receipt",
        "project_task_dispatch",
        "_recover_applied_task",
        "_read_confirmed_task",
        "_task_turn_dispatched",
        "_start_confirmed_task",
        "_run_todo_update",
        "_run_gate_resolve",
        "_reviewed_terminal_options",
        "_reviewed_update_options",
        "_canonical_update_basis",
        "_apply_reviewed_todo_edit",
        "_apply_todo_update",
    ):
        assert hasattr(ChatTodoActionMixin, attr), f"Missing ChatTodoActionMixin.{attr}"


def test_option_parsers_readback_and_coercions() -> None:
    assert _decision_readback({"decision_outcome": "approved", "unblock_resume": {"state": "resumed"}}) == {
        "decision_outcome": "approved",
        "unblock_resume_state": "resumed",
    }
    assert _decision_readback(MappingProxyType({"decision_outcome": "rejected", "unblock_resume": "invalid"})) == {
        "decision_outcome": "rejected",
        "unblock_resume_state": None,
    }
    assert ChatTodoActionMixin._reviewed_terminal_options(None, "op-1") == {}
    assert ChatTodoActionMixin._reviewed_update_options(None, "op-1") == {}

    terminal_basis = {
        "schema_version": todo_action_contracts.CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION,
        "provider_revision": "rev-9",
        "registry_sha256": "abc123",
    }
    assert ChatTodoActionMixin._reviewed_terminal_options(terminal_basis, "op-t") == {
        "completion_turn_key": "op-t",
        "terminal_review_basis": {"provider_revision": "rev-9", "registry_sha256": "abc123"},
    }
    update_basis = {
        "schema_version": todo_action_contracts.CANONICAL_UPDATE_BASIS_SCHEMA_VERSION,
        "provider_revision": "rev-8",
        "registry_sha256": "def456",
    }
    assert ChatTodoActionMixin._reviewed_update_options(update_basis, "op-u") == {
        "update_operation_id": "op-u",
        "update_expected_provider_revision": "rev-8",
        "update_expected_registry_sha256": "def456",
    }
    with pytest.raises(ValueError, match="unsupported canonical terminal review basis"):
        ChatTodoActionMixin._reviewed_terminal_options(update_basis, "op-1")
    with pytest.raises(ValueError, match="unsupported canonical update review basis"):
        ChatTodoActionMixin._reviewed_update_options(terminal_basis, "op-1")

    assert todo_action_contracts.as_optional_int(None, field="v") is None
    assert todo_action_contracts.as_optional_int(7, field="v") == 7
    with pytest.raises(ValueError, match="v must be an integer"):
        todo_action_contracts.as_optional_int(True, field="v")
    with pytest.raises(ValueError, match="v must be an integer"):
        todo_action_contracts.as_optional_int("7", field="v")

    assert todo_action_contracts.as_optional_str_list(None, field="ids") is None
    assert todo_action_contracts.as_optional_str_list(("t1", 2), field="ids") == ["t1", "2"]
    with pytest.raises(ValueError, match="ids must be a sequence"):
        todo_action_contracts.as_optional_str_list("t1", field="ids")

    assert ChatTodoActionMixin._task_turn_dispatched({"status": "queued"}) is False
    assert ChatTodoActionMixin._task_turn_dispatched({"status": "queued", "started_at": "2026-01-01T00:00:00Z"}) is True
    assert ChatTodoActionMixin._task_turn_dispatched({"status": "completed"}) is True
    assert todo_action_contracts.require_str("ok", error="bad") == "ok"
    with pytest.raises(ValueError, match="bad"):
        todo_action_contracts.require_str("", error="bad")


def test_pending_applied_task_dispatch_boundaries(tmp_path: Path) -> None:
    turn_record: dict[str, object] = {
        "session_id": "sess-1",
        "turn_id": "turn-1",
        "client_turn_id": "task-start-prop-1",
        "status": "queued",
    }

    class _TurnStore:
        def __init__(self, *, raise_error: Exception | None = None) -> None:
            self.raise_error = raise_error

        def load_turn(self, session_id: str, turn_id: str) -> dict[str, object] | None:
            if self.raise_error is not None:
                raise self.raise_error
            return turn_record

    base_proposal: dict[str, object] = {
        "proposal_id": "prop-1",
        "status": "applied",
        "action_kind": "todo.create",
        "permission_classification": "durable_write",
        "expected_state_fingerprint": "prev-1",
        "context": {"kind": "goal"},
        "normalized_parameters": {"goal_id": "goal-a", "start_execution": True},
        "receipt": {
            "outcome": "task_execution_started",
            "projection_verified": True,
            "resource_ids": {
                "goal_id": "goal-a",
                "todo_id": "todo-1",
                "session_id": "sess-1",
                "turn_id": "turn-1",
            },
            "canonical_receipt": {
                "goal_id": "goal-a",
                "todo_id": "todo-1",
                "status": "applied",
                "preview_id": "prev-1",
            },
        },
    }

    host = _StubTodoHost(registry_path=tmp_path / "registry.json", chat_store=_TurnStore())
    projected = host.project_task_dispatch(MappingProxyType(base_proposal))
    assert projected["task_execution_dispatch_pending"] is True

    # Controller store overrides fallback chat_store when present.
    ctrl_host = _StubTodoHost(
        registry_path=tmp_path / "registry.json",
        chat_store=None,
        runtime_controller=_StubRuntimeController(store=_TurnStore()),
    )
    assert ctrl_host._pending_applied_task_receipt(base_proposal) == {
        "goal_id": "goal-a",
        "todo_id": "todo-1",
        "session_id": "sess-1",
        "turn_id": "turn-1",
    }

    # Dispatched / non-matching cases fail closed cleanly.
    turn_record = {**turn_record, "upstream_turn_id": "up-1"}
    assert host._pending_applied_task_receipt(base_proposal) is None
    assert "task_execution_dispatch_pending" not in host.project_task_dispatch(base_proposal)

    turn_record = {"session_id": "sess-1", "turn_id": "turn-1", "client_turn_id": "task-start-prop-1", "status": "queued"}
    for bad in (
        {**base_proposal, "context": {"binding_id": "bind-1"}},
        {**base_proposal, "normalized_parameters": {"goal_id": "goal-a", "start_execution": False}},
        {**base_proposal, "expected_state_fingerprint": "stale-fp"},
        {**base_proposal, "permission_classification": "protected"},
    ):
        assert host._pending_applied_task_receipt(bad) is None

    err_host = _StubTodoHost(
        registry_path=tmp_path / "registry.json",
        chat_store=_TurnStore(raise_error=ValueError("corrupt")),
    )
    assert err_host._pending_applied_task_receipt(base_proposal) is None


def test_canonical_basis_toctou_and_reviewed_edit_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_a = ChatTodoSource(tmp_path / "reg.json", tmp_path, "src-1", "sha-1", {"id": "goal-a"})
    source_b = ChatTodoSource(tmp_path / "reg.json", tmp_path, "src-2", "sha-1", {"id": "goal-a"})
    sources = iter([source_a, source_b])
    monkeypatch.setattr(todo_action_updates, "resolve_chat_todo_source", lambda host, gid: next(sources))
    monkeypatch.setattr(
        todo_action_updates,
        "read_canonical_todos_if_promoted",
        lambda **kwargs: {"provider_revision": "r1", "source_authority": "sqlite", "todos": []},
    )
    host = _StubTodoHost(registry_path=tmp_path / "reg.json")
    with pytest.raises(ValueError, match="registration changed while reading"):
        host._canonical_update_basis("goal-a")

    monkeypatch.setattr(todo_action_updates, "resolve_chat_todo_source", lambda host, gid: source_a)
    monkeypatch.setattr(
        todo_action_updates,
        "read_canonical_todos_if_promoted",
        lambda **kwargs: {
            "provider_revision": "r2",
            "source_authority": "sqlite",
            "todos": [{"todo_id": "t-user", "role": "user"}, {"todo_id": "t-agent", "role": "agent"}],
        },
    )
    user_basis = _as_map(host._canonical_update_basis("goal-a", completion_todo_id="t-user"))
    assert user_basis["schema_version"] == todo_action_contracts.CANONICAL_UPDATE_BASIS_SCHEMA_VERSION
    agent_basis = _as_map(host._canonical_update_basis("goal-a", completion_todo_id="t-agent"))
    assert agent_basis["schema_version"] == todo_action_contracts.CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION
    dec_basis = _as_map(host._canonical_update_basis("goal-a", completion_todo_id="t-user", decision=True))
    assert dec_basis["schema_version"] == todo_action_contracts.CANONICAL_TERMINAL_BASIS_SCHEMA_VERSION

    # Malformed non-None basis must fail closed instead of falling back to None.
    with pytest.raises(ValueError, match="canonical update review basis is malformed"):
        host._apply_reviewed_todo_edit(
            "prop-bad",
            {"action_kind": "todo.update", "expected_state_fingerprint": "fp", "canonical_update_basis": "bad"},
            {"goal_id": "goal-a", "todo_id": "t-1"},
        )

    proposal: dict[str, object] = {
        "action_kind": "todo.update",
        "expected_state_fingerprint": "fp-orig",
        "canonical_update_basis": {
            "schema_version": todo_action_contracts.CANONICAL_UPDATE_BASIS_SCHEMA_VERSION,
            "provider_revision": "r1",
            "registry_sha256": "sha-1",
            "source_authority": "sqlite",
        },
    }
    params: dict[str, object] = {"goal_id": "goal-a", "todo_id": "todo-1", "operation": "edit"}

    def raise_source_changed(*args: object, **kwargs: object) -> dict[str, object]:
        raise ChatTodoSourceChanged("moved source")

    monkeypatch.setattr(host, "_run_todo_update", raise_source_changed)
    res_changed = host._apply_reviewed_todo_edit("prop-1", proposal, params)
    assert _as_map(_as_map(res_changed["proposal"])["failure"])["error_code"] == "canonical_update_source_changed"

    def raise_in_progress(*args: object, **kwargs: object) -> dict[str, object]:
        raise LocalCoordinationAuthorityUnavailable("busy", code=COMPLETION_VALIDATION_IN_PROGRESS, payload={})

    monkeypatch.setattr(host, "_run_todo_update", raise_in_progress)
    with pytest.raises(ActionConflictError, match="busy"):
        host._apply_reviewed_todo_edit("prop-2", proposal, params)

    def raise_validation_source_changed(*args: object, **kwargs: object) -> dict[str, object]:
        raise LocalCoordinationAuthorityUnavailable(
            "changed",
            code="authority_source_changed",
            payload={"completion_validation_executed": True},
        )

    monkeypatch.setattr(host, "_run_todo_update", raise_validation_source_changed)
    with pytest.raises(ValueError, match="during completion validation"):
        host._apply_reviewed_todo_edit("prop-3", proposal, {**params, "operation": "complete"})
    assert host.store.failed[-1][1] == "canonical_update_validation_source_changed"

    def raise_conflict(*args: object, **kwargs: object) -> dict[str, object]:
        raise LocalCoordinationAuthorityUnavailable("stale", code="provider_revision_mismatch", payload={})

    monkeypatch.setattr(host, "_run_todo_update", raise_conflict)
    stale_res = host._apply_reviewed_todo_edit("prop-4", proposal, params)
    stale_prop = _as_map(stale_res["proposal"])
    assert stale_prop["status"] == "stale"
    assert stale_prop["expected_state_fingerprint"] == _canonical_digest(
        {"expected": "fp-orig", "conflict": "provider_revision_mismatch"}
    )

    monkeypatch.setattr(host, "_run_todo_update", lambda *a, **kw: {"ok": False, "reason_code": "test_fail"})
    val_fail = host._apply_reviewed_todo_edit("prop-5", proposal, params)
    assert _as_map(_as_map(val_fail["proposal"])["failure"])["error_code"] == "canonical_update_validation_failed"

    monkeypatch.setattr(
        host,
        "_run_todo_update",
        lambda *a, **kw: {"ok": True, "todo_id": "todo-1", "changed": True, "projection_delivery": "pending", "status": "open"},
    )
    proj_pending = host._apply_reviewed_todo_edit("prop-6", proposal, params)
    assert _as_map(_as_map(proj_pending["proposal"])["failure"])["error_code"] == "canonical_update_projection_pending"
    assert host.store.checkpoints[-1][1] == "canonical_update"

    host.monitor_runner = lambda *a, **kw: {
        "ok": True,
        "todo_id": "mon-1",
        "original_receipt": {"changed": True},
        "projection_delivery": "delivered",
        "provider_status": "paused",
    }
    mon_res = host._apply_reviewed_todo_edit(
        "prop-7",
        {**proposal, "action_kind": "monitor.update"},
        {"goal_id": "goal-a", "todo_id": "mon-1", "operation": "pause"},
    )
    assert _as_map(_as_map(mon_res["proposal"])["receipt"])["outcome"] == "monitor_paused"

    monkeypatch.setattr(
        host,
        "_run_gate_resolve",
        lambda *a, **kw: {
            "ok": True,
            "todo_id": "gate-1",
            "changed": True,
            "projection_delivery": "current",
            "status": "done",
            "decision_outcome": "approved",
            "unblock_resume": {"state": "queued"},
        },
    )
    gate_res = host._apply_reviewed_todo_edit(
        "prop-8",
        {**proposal, "action_kind": "gate.resolve"},
        {"goal_id": "goal-a", "todo_id": "gate-1", "decision": "approved"},
    )
    gate_receipt = _as_map(_as_map(gate_res["proposal"])["receipt"])
    assert gate_receipt["outcome"] == "gate_resolved"
    assert gate_receipt["decision_outcome"] == "approved"
    assert gate_receipt["unblock_resume_state"] == "queued"


def test_read_task_start_and_recover_applied_task_negatives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SessionFake:
        def __init__(
            self,
            sessions: dict[str, dict[str, object]],
            turns: dict[tuple[str, str], dict[str, object]],
        ) -> None:
            self.sessions = sessions
            self.turns = turns

        def load_session(self, session_id: str) -> dict[str, object] | None:
            return self.sessions.get(session_id)

        def load_turn(self, session_id: str, turn_id: str) -> dict[str, object] | None:
            for item in self.turns.values():
                if item.get("session_id") == session_id and item.get("turn_id") == turn_id:
                    return item
            return None

        def turn_for_client(self, session_id: str, client_turn_id: str) -> dict[str, object] | None:
            return self.turns.get((session_id, client_turn_id))

        def session_candidates(
            self,
            *,
            goal_id: str | None,
            agent_id: str,
            channel_id: str | None = None,
        ) -> list[dict[str, object]]:
            return [
                row
                for row in self.sessions.values()
                if row.get("goal_id") == goal_id and row.get("agent_id") == agent_id and row.get("channel_id") == channel_id
            ]

    chat = _SessionFake(
        sessions={
            "s1": {"session_id": "s1", "goal_id": "goal-a", "agent_id": "codex", "channel_id": "task.todo-1"},
            "s2": {"session_id": "s2", "goal_id": "goal-a", "agent_id": "codex", "channel_id": "task.todo-1"},
        },
        turns={("s2", "task-start-p1"): {"session_id": "s2", "turn_id": "t2", "message": "do task"}},
    )
    with pytest.raises(ValueError, match="original Task Session is unavailable"):
        ChatTodoActionMixin._read_task_start(
            chat,
            goal_id="goal-a",
            agent_id="other-agent",
            channel_id="task.todo-1",
            client_turn_id="task-start-p1",
            checkpoint={"session_id": "s1"},
        )
    sess, turn = ChatTodoActionMixin._read_task_start(
        chat,
        goal_id="goal-a",
        agent_id="codex",
        channel_id="task.todo-1",
        client_turn_id="task-start-p1",
        checkpoint=None,
    )
    assert sess is not None and sess["session_id"] == "s2" and turn is not None and turn["turn_id"] == "t2"

    # Ambiguous multi-session candidate set with zero client turn matches fails closed.
    chat.turns.clear()
    with pytest.raises(ValueError, match="original Task Session is ambiguous"):
        ChatTodoActionMixin._read_task_start(
            chat,
            goal_id="goal-a",
            agent_id="codex",
            channel_id="task.todo-1",
            client_turn_id="task-start-missing",
            checkpoint=None,
        )

    # read_confirmed_task detects turn message mutation and missing execution turn.
    chat.turns[("s1", "task-start-p1")] = {"session_id": "s1", "turn_id": "t1", "message": "tampered"}
    host = _StubTodoHost(registry_path=tmp_path / "reg.json", chat_store=chat)
    with pytest.raises(ValueError, match="original Task Turn request changed"):
        host._read_confirmed_task(
            "p1",
            {"goal_id": "goal-a", "agent_id": "codex", "text": "do task"},
            "todo-1",
            {"task_session_opened": {"session_id": "s1"}},
        )

    # recover_applied_task falls back cleanly to _turn_from_receipt when not pending dispatch.
    assert host._recover_applied_task({"status": "applied", "receipt": {"turn": "x"}}) == {"turn_id": "turn-from-receipt"}

    # Mismatched todo_created checkpoint triggers explicit failure instead of guessing.
    queued_turn: dict[str, object] = {
        "session_id": "s1",
        "turn_id": "t1",
        "client_turn_id": "task-start-p1",
        "status": "queued",
    }
    chat.turns[("s1", "task-start-p1")] = queued_turn
    monkeypatch.setattr(
        todo_action_tasks,
        "resolve_chat_todo_source",
        lambda *a, **kw: ChatTodoSource(tmp_path / "reg.json", tmp_path, "src-1", "sha-1", {"id": "goal-a"}),
    )
    bad_recovery_proposal: dict[str, object] = {
        "proposal_id": "p1",
        "status": "applied",
        "action_kind": "todo.create",
        "permission_classification": "durable_write",
        "expected_state_fingerprint": "prev-1",
        "context": {},
        "normalized_parameters": {"goal_id": "goal-a", "agent_id": "codex", "start_execution": True},
        "checkpoint": {"steps": {"todo_created": {"todo_id": "wrong-todo", "canonical_receipt": {}}}},
        "receipt": {
            "outcome": "task_execution_started",
            "projection_verified": True,
            "resource_ids": {"goal_id": "goal-a", "todo_id": "todo-1", "session_id": "s1", "turn_id": "t1"},
            "canonical_receipt": {"goal_id": "goal-a", "todo_id": "todo-1", "status": "applied", "preview_id": "prev-1"},
        },
    }
    with pytest.raises(ValueError, match="creation checkpoint changed"):
        host._recover_applied_task(bad_recovery_proposal)
    assert callable(todo_action_create.apply_todo_create)
    assert callable(todo_actions._decision_readback)
