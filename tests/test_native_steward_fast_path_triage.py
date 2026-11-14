from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.capabilities.native_chat import steward_triage
from loopx.capabilities.native_chat.steward_triage import (
    StewardFastPathError,
    WITHHELD_TEXT,
    build_portfolio_triage_projection,
    execute_steward_fast_path,
    parse_steward_fast_path_intent,
    render_portfolio_triage_markdown,
)
from loopx.extensions.lark.presentation.triage_card import build_steward_triage_card


def _todo(todo_id: str, *, role: str = "agent", status: str = "open", text: str = "work",
          priority: str | None = None, task_class: str = "advancement_task", **extra: Any) -> dict[str, Any]:
    row = {"todo_id": todo_id, "role": role, "status": status, "text": text,
           "task_class": task_class, "done": status == "done"}
    if priority:
        row["priority"] = priority
    row.update(extra)
    return row


TODOS: dict[str, list[dict[str, Any]]] = {
    "goal-a": [
        _todo("todo_aaaa00000001", role="user", task_class="user_gate", priority="P2",
              text="Authorize publishing the reviewed head?", unblocks_todo_id="todo_aaaa00000002",
              decision_scope={"kind": "public_claim", "granularity": "action", "scope_key": "pr-1"}),
        _todo("todo_aaaa00000002", priority="P0", text="[P0] Repair the monitor admission"),
        _todo("todo_aaaa00000003", priority="P3", text="Low priority cleanup"),
        _todo("todo_aaaa00000004", status="done", text="Shipped the context delta",
              completed_at="2026-11-13T10:00:00Z"),
        _todo("todo_aaaa00000005", role="user", task_class="user_gate", status="done",
              text="Already settled gate"),
    ],
    "goal-b": [
        _todo("todo_bbbb00000001", status="blocked", text="Waiting on upstream provider"),
        _todo("todo_bbbb00000002", priority="P1",
              text="Read /Users/someone/private/notes.md before continuing"),
        _todo("todo_bbbb00000003", role="user", task_class="user_gate", global_gate=True,
              text="Approve the shared release window?"),
    ],
}


@pytest.fixture
def reads() -> list[str]:
    return []


@pytest.fixture
def portfolio(monkeypatch, tmp_path: Path, reads: list[str]) -> Path:
    calls = reads

    def fake_list(*, registry_path, goal_id, runtime_root_arg=None):
        calls.append(goal_id)
        if goal_id == "goal-broken":
            raise ValueError("unavailable")
        return {"ok": True, "todos": [dict(row) for row in TODOS.get(goal_id, [])]}

    monkeypatch.setattr(steward_triage, "list_goal_todos", fake_list)
    monkeypatch.setattr(steward_triage, "load_registry", lambda _path: {"goals": []})
    monkeypatch.setattr(steward_triage, "resolve_runtime_root",
                        lambda *_a, **_kw: tmp_path / "runtime")
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    return registry


@pytest.mark.parametrize(("text", "focus", "goals"), [
    ("/triage", "all", ()),
    ("/inbox", "all", ()),
    ("/gates", "gates", ()),
    ("/triage todos --goal goal-a", "todos", ("goal-a",)),
    ("/todos goal:goal-b goal:goal-b", "todos", ("goal-b",)),
    ("收件箱", "all", ()),
    ("  Open   Gates ", "gates", ()),
])
def test_parse_fast_path_intents(text, focus, goals):
    intent = parse_steward_fast_path_intent(text)
    assert intent is not None
    assert intent.focus == focus
    assert intent.requested_goal_ids == goals


@pytest.mark.parametrize("text", [
    "", "hello", "please triage my inbox and summarize the PRs", "/triage and then fix #8674",
    "/unknown", "/note remember this",
])
def test_ordinary_messages_fall_through_to_full_turn(text):
    assert parse_steward_fast_path_intent(text) is None


@pytest.mark.parametrize("text", ["/triage --goal", "/triage --goal ../etc", "/gates goal:bad/id"])
def test_malformed_goal_arguments_fail_closed(text):
    with pytest.raises(StewardFastPathError) as exc:
        parse_steward_fast_path_intent(text)
    assert exc.value.code == "invalid_fast_path_arguments"


def test_projection_orders_gates_and_attention_work(portfolio):
    projection = build_portfolio_triage_projection(
        registry_path=portfolio, authorized_goal_ids=["goal-a", "goal-b"])

    assert projection["quiet"] is False
    gates = projection["open_user_gates"]
    # Global gates first, settled gates excluded.
    assert [gate["todo_id"] for gate in gates] == ["todo_bbbb00000003", "todo_aaaa00000001"]
    assert gates[1]["unblocks_todo_id"] == "todo_aaaa00000002"
    assert gates[1]["decision_scope_summary"] == "public_claim:action:pr-1"
    assert gates[1]["state_fingerprint"].startswith("sha256:")

    todos = projection["attention_todos"]
    assert [item["todo_id"] for item in todos] == [
        "todo_aaaa00000002", "todo_bbbb00000002", "todo_bbbb00000001"]
    # Local paths never leak into the digest.
    assert todos[1]["text"] == WITHHELD_TEXT
    assert "/Users/" not in json.dumps(projection)

    assert projection["counts"]["blocked_todos"] == 1
    assert projection["counts"]["high_priority_todos"] == 2
    assert projection["recent_progress"][0]["todo_id"] == "todo_aaaa00000004"
    profile = projection["execution_profile"]
    assert profile["read_only"] and not profile["lease_acquired"] and not profile["quota_spent"]


def test_projection_bounds_focus_and_unavailable_goals(portfolio):
    projection = build_portfolio_triage_projection(
        registry_path=portfolio, authorized_goal_ids=["goal-a", "goal-b", "goal-broken"],
        focus="gates", max_gates=1)

    assert len(projection["open_user_gates"]) == 1
    assert projection["counts"]["omitted_user_gates"] == 1
    assert projection["attention_todos"] == [] and projection["recent_progress"] == []
    assert projection["unavailable_goals"] == [
        {"goal_id": "goal-broken", "error_code": "goal_readback_unavailable"}]


def test_requested_goal_outside_scope_is_rejected_before_any_read(portfolio, reads):
    with pytest.raises(StewardFastPathError) as exc:
        build_portfolio_triage_projection(
            registry_path=portfolio, authorized_goal_ids=["goal-a"],
            requested_goal_ids=["goal-b"])
    assert exc.value.code == "goal_outside_authorized_scope"
    assert reads == []


def test_invalid_focus_and_bounds_are_rejected(portfolio):
    with pytest.raises(StewardFastPathError):
        build_portfolio_triage_projection(
            registry_path=portfolio, authorized_goal_ids=["goal-a"], focus="everything")
    with pytest.raises(StewardFastPathError):
        build_portfolio_triage_projection(
            registry_path=portfolio, authorized_goal_ids=["goal-a"], max_gates=0)


def test_quiet_portfolio_renders_all_clear(portfolio):
    projection = build_portfolio_triage_projection(
        registry_path=portfolio, authorized_goal_ids=["goal-empty"])
    assert projection["quiet"] is True
    assert "All clear" in render_portfolio_triage_markdown(projection)
    card = build_steward_triage_card(projection)
    assert card["header"]["template"] == "green"


class _Bindings:
    def __init__(self, binding: dict[str, Any] | None, context: dict[str, Any] | None = None,
                 error: Exception | None = None) -> None:
        self.binding, self.context, self.error = binding, context, error
        self.calls: list[dict[str, Any]] = []

    def resolve(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"binding": self.binding, "context": self.context}


def _fast(text: str, bindings: _Bindings, registry: Path) -> dict[str, Any] | None:
    return execute_steward_fast_path(
        text=text, bindings=bindings, binding_id="binding-1", source_ref="source-1",
        sender_ref="sender-1", registry_path=registry)


def test_fast_path_uses_steward_scope_and_skips_ordinary_text(portfolio):
    bindings = _Bindings({"enabled": True, "context_kind": "steward", "binding_id": "binding-1"},
                         {"steward_scope": {"goal_ids": ["goal-a"]}})

    assert _fast("please review my PRs", bindings, portfolio) is None
    assert bindings.calls == []  # non-matching text never touches binding authority

    result = _fast("/gates", bindings, portfolio)
    assert result is not None and result["handled"] is True
    assert result["triage"]["scoped_goal_ids"] == ["goal-a"]
    assert result["markdown"].startswith("# Steward Fast-Path Triage")
    assert bindings.calls[0]["private_human_message"] is True


@pytest.mark.parametrize("bindings", [
    _Bindings(None, error=ValueError("conversation binding is no longer authorized")),
    _Bindings({"enabled": False, "context_kind": "steward"}),
    _Bindings({"enabled": True, "context_kind": "project"}),
])
def test_fast_path_fails_closed_without_enabled_steward_binding(portfolio, reads, bindings):
    with pytest.raises(StewardFastPathError):
        _fast("/triage", bindings, portfolio)
    assert reads == []


def test_fast_path_rejects_goal_outside_binding_scope(portfolio):
    bindings = _Bindings({"enabled": True, "context_kind": "steward", "goal_ids": ["goal-a"]}, {})
    with pytest.raises(StewardFastPathError) as exc:
        _fast("/triage --goal goal-b", bindings, portfolio)
    assert exc.value.code == "goal_outside_authorized_scope"


def test_triage_card_shows_results_without_exposing_commands(portfolio):
    projection = build_portfolio_triage_projection(
        registry_path=portfolio, authorized_goal_ids=["goal-a", "goal-b"])
    card = build_steward_triage_card(projection)
    body = json.dumps(card, ensure_ascii=False)

    assert card["header"]["template"] == "orange"
    assert "待我处理" in body and "重点与阻塞任务" in body
    assert "Authorize publishing the reviewed head?" in body
    assert "loopx todo list" not in body
    assert '"tag": "action"' not in body and '"value"' not in body
    assert "/Users/" not in body


@pytest.mark.parametrize("scope", [["goal-broken"], ["goal-broken", "goal-clear"], []])
def test_unread_or_empty_scope_is_never_all_clear(portfolio, scope):
    result = build_portfolio_triage_projection(registry_path=portfolio, authorized_goal_ids=scope)
    assert result["quiet"] is False
    assert "All clear" not in render_portfolio_triage_markdown(result)
    assert build_steward_triage_card(result)["header"]["template"] != "green"


def test_triage_card_rejects_foreign_payload():
    with pytest.raises(ValueError):
        build_steward_triage_card({"schema_version": "something_else"})


def test_native_command_triage_route(portfolio, monkeypatch):
    from loopx.capabilities.native_chat import steward_commands

    monkeypatch.setattr(steward_commands, "resolve_all_registered_goal_ids",
                        lambda _path: ["goal-a", "goal-b"])

    class Controller:
        registry_path = portfolio
        coordination_runtime_root = portfolio.parent / "runtime"

    result = steward_commands.native_command(Controller(), ["triage", "--all-goals", "--focus", "todos"])
    assert result["schema_version"] == "steward_portfolio_triage_v0"
    assert result["focus"] == "todos" and result["open_user_gates"] == []

    with pytest.raises(steward_commands.NativeStewardCommandError) as exc:
        steward_commands.native_command(Controller(), ["triage", "--focus", "bogus"])
    assert exc.value.payload["error_code"] == "invalid_triage_focus"


def test_priority_prefix_is_not_duplicated_in_digest(portfolio):
    projection = build_portfolio_triage_projection(
        registry_path=portfolio, authorized_goal_ids=["goal-a"])
    item = next(row for row in projection["attention_todos"] if row["todo_id"] == "todo_aaaa00000002")
    assert item["text"] == "Repair the monitor admission"
    assert "[P0] [P0]" not in render_portfolio_triage_markdown(projection)
