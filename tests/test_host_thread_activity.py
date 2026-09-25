from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import loopx.chat_status_api as chat_status_api
import loopx.codex_app_thread_activity as codex_activity
from loopx.codex_app_thread_activity import codex_homes, codex_thread_observers, observe_codex_threads
from loopx.control_plane.agents.host_thread_activity import (
    HostThreadActivity,
    HostThreadState,
    HostThreadUnknownReason,
    attach_host_thread_activity,
)

T0 = "2026-09-25T10:00:00.000Z"
T1 = "2026-09-25T10:05:00.000Z"
T2 = "2026-09-25T10:06:00.000Z"
T3 = "2026-09-25T10:07:00.000Z"


def _event(timestamp: str, kind: str, **payload: Any) -> dict[str, Any]:
    return {"timestamp": timestamp, "type": "event_msg", "payload": {"type": kind, **payload}}


def _item(timestamp: str) -> dict[str, Any]:
    return {"timestamp": timestamp, "type": "response_item", "payload": {"type": "message", "content": "x"}}


class CodexHome:
    """A disposable Codex home with the store shape the adapter depends on."""

    def __init__(self, root: Path, *, columns: str = "id TEXT PRIMARY KEY, rollout_path TEXT, archived INTEGER") -> None:
        self.root = root
        (root / "sessions").mkdir(parents=True)
        self.db = root / "state_5.sqlite"
        with sqlite3.connect(self.db) as connection:
            connection.execute(f"CREATE TABLE threads ({columns})")

    def thread(self, thread_id: str, records: list[dict[str, Any]], *, archived: int = 0, tail: str = "", rollout: Path | None = None) -> Path:
        path = rollout or self.root / "sessions" / f"rollout-{thread_id}.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records) + tail, encoding="utf-8")
        with sqlite3.connect(self.db) as connection:
            connection.execute("INSERT INTO threads VALUES (?, ?, ?)", (thread_id, str(path), archived))
        return path


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    codex_activity._rollout_cache.clear()


@pytest.fixture
def home(tmp_path: Path) -> CodexHome:
    return CodexHome(tmp_path / ".codex")


def _observe(home: CodexHome, *thread_ids: str) -> dict[str, HostThreadActivity]:
    return observe_codex_threads(thread_ids, homes=[home.root])


def test_turn_without_end_marker_is_open(home: CodexHome) -> None:
    home.thread("t", [
        _event(T0, "task_started", turn_id="a"), _event(T1, "task_complete", turn_id="a"),
        _event(T2, "task_started", turn_id="b"), _item(T3),
    ])
    assert _observe(home, "t")["t"] == HostThreadActivity(
        state=HostThreadState.TURN_OPEN, turn_started_at=T2, last_event_at=T3,
    )


@pytest.mark.parametrize("end", ["task_complete", "turn_aborted"])
def test_completed_or_aborted_turn_is_idle(home: CodexHome, end: str) -> None:
    home.thread("t", [_event(T0, "task_started"), _item(T1), _event(T2, end), _event(T3, "token_count")])
    assert _observe(home, "t")["t"] == HostThreadActivity(
        state=HostThreadState.IDLE, last_turn_ended_at=T2, last_event_at=T3,
    )


def test_archived_thread_is_not_read_as_open(home: CodexHome) -> None:
    home.thread("t", [_event(T0, "task_started")], archived=1)
    assert _observe(home, "t")["t"].state is HostThreadState.ARCHIVED


def test_line_being_appended_is_ignored(home: CodexHome, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_activity, "_TAIL_CHUNK_BYTES", 16)
    home.thread("t", [_event(T0, "task_started"), _item(T1)], tail='{"timestamp": "' + "x" * 80)
    assert _observe(home, "t")["t"] == HostThreadActivity(
        state=HostThreadState.TURN_OPEN, turn_started_at=T0, last_event_at=T1,
    )


def test_lines_spanning_chunks_are_reassembled(home: CodexHome, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codex_activity, "_TAIL_CHUNK_BYTES", 7)
    home.thread("t", [_event(T0, "task_complete"), _event(T1, "task_started"), _item(T2)])
    assert _observe(home, "t")["t"].turn_started_at == T1


def test_new_records_invalidate_the_cached_scan(home: CodexHome) -> None:
    path = home.thread("t", [_event(T0, "task_started")])
    assert _observe(home, "t")["t"].state is HostThreadState.TURN_OPEN
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_event(T1, "task_complete")) + "\n")
    assert _observe(home, "t")["t"].state is HostThreadState.IDLE


@pytest.mark.parametrize(
    ("records", "tail_limit", "reason"),
    [
        ([_event(T0, "task_started"), {"unexpected": True}], None, HostThreadUnknownReason.RECORD_UNRECOGNIZED),
        ([], None, HostThreadUnknownReason.RECORD_UNRECOGNIZED),
        ([_event(T0, "task_started"), *[_item(T1)] * 20], 400, HostThreadUnknownReason.NO_TURN_MARKER),
    ],
    ids=["unrecognized-last-record", "empty-rollout", "no-marker-in-tail"],
)
def test_unrecognized_rollouts_are_unknown(
    home: CodexHome, monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]], tail_limit: int | None, reason: HostThreadUnknownReason,
) -> None:
    if tail_limit:
        monkeypatch.setattr(codex_activity, "_TAIL_LIMIT_BYTES", tail_limit)
    home.thread("t", records)
    assert _observe(home, "t")["t"] == HostThreadActivity.unknown(reason)


def test_rollout_outside_the_home_is_not_read(home: CodexHome, tmp_path: Path) -> None:
    home.thread("t", [_event(T0, "task_started")], rollout=tmp_path / "elsewhere.jsonl")
    assert _observe(home, "t")["t"] == HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)


def test_store_without_required_columns_is_unrecognized(tmp_path: Path) -> None:
    home = CodexHome(tmp_path / ".codex", columns="id TEXT PRIMARY KEY, rollout_path TEXT, hidden INTEGER")
    assert _observe(home, "t")["t"] == HostThreadActivity.unknown(HostThreadUnknownReason.RECORD_UNRECOGNIZED)


def test_unreadable_store_and_missing_thread_are_distinguished(tmp_path: Path) -> None:
    broken = tmp_path / ".codex-broken"
    broken.mkdir()
    (broken / "state_5.sqlite").write_bytes(b"not a database")
    healthy = CodexHome(tmp_path / ".codex")
    assert observe_codex_threads(["t"], homes=[healthy.root])["t"].reason is HostThreadUnknownReason.THREAD_NOT_FOUND
    assert observe_codex_threads(["t"], homes=[broken, healthy.root])["t"].reason is HostThreadUnknownReason.STORE_UNAVAILABLE


def test_wal_store_without_shared_memory_file_is_readable(tmp_path: Path) -> None:
    home = CodexHome(tmp_path / ".codex")
    home.thread("t", [_event(T0, "task_complete")])
    connection = sqlite3.connect(home.db)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    assert not Path(f"{home.db}-wal").exists() and not Path(f"{home.db}-shm").exists()
    assert _observe(home, "t")["t"].state is HostThreadState.IDLE


def test_threads_resolve_across_homes_and_explicit_homes_disable_discovery(tmp_path: Path) -> None:
    default = CodexHome(tmp_path / ".codex")
    secondary = CodexHome(tmp_path / ".codex-secondary")
    default.thread("a", [_event(T0, "task_complete")])
    secondary.thread("b", [_event(T0, "task_started")])
    homes = codex_homes(env={}, user_home=tmp_path)
    assert homes == [default.root, secondary.root]
    observed = observe_codex_threads(["a", "b"], homes=homes)
    assert (observed["a"].state, observed["b"].state) == (HostThreadState.IDLE, HostThreadState.TURN_OPEN)
    assert codex_homes(env={"LOOPX_CODEX_HOMES": str(secondary.root)}, user_home=tmp_path) == [secondary.root]


def test_unknown_state_requires_exactly_one_reason() -> None:
    with pytest.raises(ValueError):
        HostThreadActivity(state=HostThreadState.UNKNOWN)
    with pytest.raises(ValueError):
        HostThreadActivity(state=HostThreadState.IDLE, reason=HostThreadUnknownReason.THREAD_NOT_FOUND)


def _status(*bindings: dict[str, str]) -> dict[str, Any]:
    return {"run_history": {"goals": [
        {"id": "bound", "coordination": {"thread_agent_bindings": list(bindings)}},
        {"id": "unbound", "coordination": {"registered_agents": ["lead"]}},
    ]}}


def test_attach_reports_each_binding_without_thread_ids() -> None:
    calls: list[list[str]] = []

    def observe(thread_ids: Any) -> dict[str, HostThreadActivity]:
        calls.append(list(thread_ids))
        return {"t-open": HostThreadActivity(state=HostThreadState.TURN_OPEN, turn_started_at=T0, last_event_at=T1)}

    payload = _status(
        {"agent_id": "a", "host_surface": "codex-app", "thread_id": "t-open"},
        {"agent_id": "b", "host_surface": "codex-app", "thread_id": "t-gone"},
        {"agent_id": "c", "host_surface": "codex-app-ssh", "thread_id": "t-remote"},
    )
    attach_host_thread_activity(payload, observers={"codex-app": observe})
    bound, unbound = payload["run_history"]["goals"]
    assert calls == [["t-gone", "t-open"]]
    assert bound["host_thread_activity"]["threads"] == [
        {"agent_id": "a", "host_surface": "codex-app", "state": "turn_open", "turn_started_at": T0, "last_event_at": T1},
        {"agent_id": "b", "host_surface": "codex-app", "state": "unknown", "reason": "thread_not_found"},
        {"agent_id": "c", "host_surface": "codex-app-ssh", "state": "unknown", "reason": "unsupported_host"},
    ]
    assert "t-open" not in json.dumps(bound["host_thread_activity"])
    assert "host_thread_activity" not in unbound


def test_app_status_route_attaches_codex_thread_activity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = CodexHome(tmp_path / ".codex")
    home.thread("t-open", [_event(T0, "task_started"), _item(T1)])
    monkeypatch.setenv("LOOPX_CODEX_HOMES", str(home.root))
    monkeypatch.setattr(chat_status_api, "collect_status", lambda **_kwargs: _status(
        {"agent_id": "a", "host_surface": "codex-app", "thread_id": "t-open"},
    ))
    sent: list[dict[str, Any]] = []

    class Handler(chat_status_api.ChatStatusRequestMixin):
        path = "/status.json"
        server = SimpleNamespace(
            selected_goal_id=None, registry_path=tmp_path / "registry.json", runtime_root_override=None,
            scan_roots=[], limit=10, goal_subagent_configuration_enabled=False,
        )

        def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
            sent.append(payload)

        def _send_error(self, message: str, **kwargs: Any) -> None:
            raise AssertionError(message)

    Handler()._status()
    threads = sent[0]["run_history"]["goals"][0]["host_thread_activity"]["threads"]
    assert threads == [{"agent_id": "a", "host_surface": "codex-app", "state": "turn_open", "turn_started_at": T0, "last_event_at": T1}]


def test_remote_codex_surfaces_have_no_local_observer() -> None:
    assert "codex-app-ssh" not in codex_thread_observers()
    assert {"codex-app", "codex-cli-tui", "codex-ide-plugin"} <= set(codex_thread_observers())
