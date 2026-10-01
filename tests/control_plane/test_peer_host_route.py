"""An Agent name is not a host address; select and observe the exact binding."""

from __future__ import annotations

import json
import contextlib
import io
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.agents.host_thread_activity import (
    HostThreadActivity,
    HostThreadState,
    HostThreadUnknownReason,
)
from loopx.control_plane.collaboration.peer_host_route import resolve_peer_host_route


def _registry(tmp_path: Path, bindings: list[dict[str, str]]) -> Path:
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal",
                        "repo": str(tmp_path),
                        "coordination": {
                            "registered_agents": ["builder", "reviewer"],
                            "thread_agent_bindings": bindings,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _binding(thread_id: str, agent_id: str = "reviewer", *, host_surface: str = "codex-app") -> dict[str, str]:
    return {"agent_id": agent_id, "host_surface": host_surface, "thread_id": thread_id}


def _observer(state: HostThreadState):
    return {
        "codex-app": lambda ids: {item: HostThreadActivity(state=state) for item in ids}
    }


@pytest.mark.parametrize("current", [HostThreadState.IDLE, HostThreadState.TURN_OPEN])
def test_archived_history_does_not_require_the_owner_to_find_a_task_link(tmp_path, current):
    bindings = [_binding(f"old-{i}") for i in range(4)] + [_binding("current")]
    registry = _registry(tmp_path, bindings)
    observed = []

    def observe(ids):
        observed.append(set(ids))
        return {item: HostThreadActivity(state=current if item == "current" else HostThreadState.ARCHIVED)
                for item in ids}

    route = resolve_peer_host_route(registry, goal_id="goal", agent_id="reviewer",
                                    observers={"codex-app": observe})
    assert route["status"] == "resolved"
    assert route["selected_route"]["thread_id"] == "current"
    assert route["candidate_count"] == 5 and len(route["candidates"]) == 3
    assert route["host_observation"]["state"] == current.value
    assert route["host_delivery"] == "not_attempted"
    assert observed == [{b["thread_id"] for b in bindings}]


@pytest.mark.parametrize("other", ["missing", "unsupported", "failed", "idle"])
def test_unknown_or_another_readable_binding_never_becomes_archived(tmp_path, other):
    registry = _registry(tmp_path, [_binding("current"), _binding("other", host_surface=other)])

    def fail(ids):
        raise OSError("private host failure")

    observers = _observer(HostThreadState.IDLE)
    if other != "unsupported":
        observers[other] = fail if other == "failed" else (
            lambda ids: {} if other == "missing" else {i: HostThreadActivity(state=HostThreadState.IDLE) for i in ids}
        )
    route = resolve_peer_host_route(registry, goal_id="goal", agent_id="reviewer", observers=observers)
    assert route["status"] == "ambiguous"
    assert route["selected_route"] is None and route["host_delivery"] == "not_attempted"
    assert "private host failure" not in json.dumps(route)


def test_all_archived_is_unavailable_but_explicit_archived_link_stays_exact(tmp_path):
    registry = _registry(tmp_path, [_binding("old"), _binding("current")])
    archived = resolve_peer_host_route(registry, goal_id="goal", agent_id="reviewer",
                                       observers=_observer(HostThreadState.ARCHIVED))
    assert archived["status"] == "unavailable" and archived["reason"] == "host_thread_archived"
    assert archived["selected_route"] is None
    explicit = resolve_peer_host_route(registry, goal_id="goal", agent_id="reviewer",
        thread_link="codex://threads/old", observers={"codex-app": lambda ids: {
            i: HostThreadActivity(state=HostThreadState.ARCHIVED if i == "old" else HostThreadState.IDLE) for i in ids}})
    assert explicit["status"] == "unavailable" and explicit["selected_route"] is None


def test_observation_cannot_rebind_the_selected_peer(tmp_path):
    registry = _registry(tmp_path, [_binding("old"), _binding("current")])

    def observe(ids):
        registry.write_text(json.dumps({"goals": [{"id": "goal", "coordination": {
            "registered_agents": ["reviewer", "builder"],
            "thread_agent_bindings": [_binding("current", "builder")],
        }}]}))
        return {i: HostThreadActivity(state=HostThreadState.ARCHIVED if i == "old" else HostThreadState.IDLE) for i in ids}

    route = resolve_peer_host_route(registry, goal_id="goal", agent_id="reviewer", observers={"codex-app": observe})
    assert route["status"] == "ambiguous" and route["reason"] == "binding_identity_conflict"
    assert route["selected_route"] is None


def test_withheld_and_over_budget_candidates_cannot_hide_a_second_binding(tmp_path):
    private_id = "ghp_" + "1234567890abcdefghijklmnopqrstuvwxyz1234"
    registry = _registry(tmp_path, [_binding("current"), _binding(private_id)])
    result = resolve_peer_host_route(registry, goal_id="goal", agent_id="reviewer",
                                     observers=_observer(HostThreadState.IDLE))
    assert result["status"] == "ambiguous" and result["selected_route"] is None
    assert result["withheld_candidate_count"] == 1 and private_id not in json.dumps(result)
    registry = _registry(tmp_path, [_binding(f"task-{i}") for i in range(33)])

    def forbidden(ids):
        pytest.fail("over-budget inventory must remain ambiguous without an unbounded host read")

    result = resolve_peer_host_route(registry, goal_id="goal", agent_id="reviewer", observers={"codex-app": forbidden})
    assert result["status"] == "ambiguous" and result["candidate_count"] == 33
    assert result["selected_route"] is None


def test_real_cli_uses_read_only_host_store_and_pins_one_request_after_archived_history(tmp_path):
    registry = _registry(tmp_path, [_binding("old"), _binding("current")])
    home = tmp_path / "codex-home"
    home.mkdir()
    rollout = home / "current.jsonl"
    rollout.write_text(json.dumps({"timestamp": "2026-01-01T00:00:00Z", "type": "event_msg",
                                  "payload": {"type": "task_complete"}}) + "\n")
    db = home / "state_5.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, archived INTEGER)")
        conn.executemany("INSERT INTO threads VALUES(?,?,?)", [("old", "", 1), ("current", str(rollout), 0)])
    before = (registry.read_bytes(), db.read_bytes(), rollout.read_bytes())
    brief = tmp_path / "brief.json"
    brief.write_text(json.dumps({"schema_version": "collaboration_brief_v0", "purpose": "Check the draft",
        "context": "Independent review", "constraints": ["Do not publish"], "inputs": [],
        "acceptance": ["Return findings"], "return_requirement": "Report to requester"}))
    env = {**os.environ, "LOOPX_CODEX_HOMES": str(home)}

    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(tmp_path / "runtime"), "--format", "json", *args],
            env=env, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    preview = cli("resolve-peer-route", "--goal-id", "goal", "--agent-id", "reviewer")
    assert preview["status"] == "resolved" and preview["selected_route"]["thread_id"] == "current"
    args = ("manager-inbox", "request", "--goal-id", "goal", "--agent-id", "builder",
            "--peer-agent-id", "reviewer", "--operation-id", "review-draft", "--brief-file", str(brief), "--require-host-route")
    requested, replay = cli(*args), cli(*args)
    assert requested["request_id"] == replay["request_id"] and replay["replayed"] is True
    assert requested["host_delivery"]["thread_id"] == "current"
    assert requested["host_delivery"]["status"] == "not_attempted"
    assert before == (registry.read_bytes(), db.read_bytes(), rollout.read_bytes())


def test_named_peer_with_two_readable_bindings_needs_an_exact_selection(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, [_binding("older"), _binding("current")])
    route = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="reviewer",
        observers=_observer(HostThreadState.IDLE),
    )
    assert route["status"] == "ambiguous"
    assert route["candidate_count"] == 2
    assert route["selected_route"] is None

    selected = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="reviewer",
        thread_link="codex://threads/current",
        observers=_observer(HostThreadState.IDLE),
    )
    assert selected["status"] == "resolved"
    assert selected["selected_route"] == {
        "host_surface": "codex-app",
        "thread_id": "current",
    }
    assert selected["host_delivery"] == "not_attempted"


def test_wrong_peer_or_archived_thread_cannot_become_a_route(tmp_path: Path) -> None:
    registry = _registry(
        tmp_path, [_binding("reviewer-thread"), _binding("builder-thread", "builder")]
    )
    wrong = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="reviewer",
        thread_link="codex://threads/builder-thread",
        observers=_observer(HostThreadState.IDLE),
    )
    assert wrong["status"] == "not_authorized"
    assert wrong["selected_route"] is None

    archived = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="reviewer",
        observers=_observer(HostThreadState.ARCHIVED),
    )
    assert archived["status"] == "unavailable"
    assert archived["reason"] == "host_thread_archived"
    assert archived["selected_route"] is None


def test_unknown_host_observation_is_not_a_verified_delivery(tmp_path: Path) -> None:
    registry = _registry(tmp_path, [_binding("reviewer-thread")])
    route = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="reviewer",
        observers={
            "codex-app": lambda ids: {
                item: HostThreadActivity.unknown(
                    HostThreadUnknownReason.THREAD_NOT_FOUND
                )
                for item in ids
            }
        },
    )
    assert route["status"] == "unavailable"
    assert route["reason"] == "thread_not_found"
    assert route["host_delivery"] == "not_attempted"


def test_unregistered_peer_gets_scope_gap_without_candidate_disclosure(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path, [_binding("reviewer-thread")])
    route = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="outsider",
        observers=_observer(HostThreadState.IDLE),
    )
    assert route["status"] == "not_authorized"
    assert route["candidate_count"] == 0
    assert route["candidates"] == []


def test_private_looking_route_is_withheld_even_from_exact_selection(
    tmp_path: Path,
) -> None:
    secret = "ghp_" + "1234567890abcdefghijklmnopqrstuvwxyz1234"
    registry = _registry(tmp_path, [_binding(secret)])
    route = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="reviewer",
        thread_link=f"codex://threads/{secret}",
        observers=_observer(HostThreadState.IDLE),
    )
    assert route["status"] == "unavailable"
    assert route["reason"] == "route_candidate_withheld"
    assert route["selected_route"] is None
    assert route["candidate_count"] == 1
    assert route["candidates"] == []


def test_same_task_bound_to_another_goal_is_ambiguous(tmp_path: Path) -> None:
    registry = _registry(tmp_path, [_binding("shared")])
    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["goals"].append(
        {
            "id": "other-goal",
            "coordination": {
                "registered_agents": ["other-agent"],
                "thread_agent_bindings": [_binding("shared", "other-agent")],
            },
        }
    )
    registry.write_text(json.dumps(payload), encoding="utf-8")
    route = resolve_peer_host_route(
        registry,
        goal_id="goal",
        agent_id="reviewer",
        observers=_observer(HostThreadState.IDLE),
    )
    assert route["status"] == "ambiguous"
    assert route["reason"] == "binding_identity_conflict"
    assert route["selected_route"] is None


def test_cli_previews_exact_route_without_writing_or_claiming_delivery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = _registry(tmp_path, [_binding("older"), _binding("current")])
    before = registry.read_bytes()
    monkeypatch.setattr(
        "loopx.control_plane.collaboration.peer_host_route.codex_thread_observers",
        lambda: _observer(HostThreadState.IDLE),
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main(
            [
                "--registry",
                str(registry),
                "--format",
                "json",
                "resolve-peer-route",
                "--goal-id",
                "goal",
                "--agent-id",
                "reviewer",
                "--thread-link",
                "codex://threads/current",
            ]
        )
    assert code == 0
    packet = json.loads(output.getvalue())
    assert packet["status"] == "resolved"
    assert packet["selected_route"]["thread_id"] == "current"
    assert packet["host_delivery"] == "not_attempted"
    assert registry.read_bytes() == before


def test_peer_request_requires_exact_observed_task_before_inbox_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = _registry(tmp_path, [_binding("older"), _binding("current")])
    brief = tmp_path / "review.json"
    brief.write_text(
        json.dumps(
            {
                "schema_version": "collaboration_brief_v0",
                "purpose": "Review the exact candidate revision",
                "context": "Review the proposed change independently.",
                "constraints": ["Do not merge"],
                "inputs": [],
                "acceptance": ["Return a verdict and findings"],
                "return_requirement": "Report the result to the requester",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "loopx.control_plane.collaboration.peer_host_route.codex_thread_observers",
        lambda: _observer(HostThreadState.IDLE),
    )

    def invoke(*extra: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(tmp_path),
                    "manager-inbox",
                    "request",
                    "--goal-id",
                    "goal",
                    "--agent-id",
                    "builder",
                    "--peer-agent-id",
                    "reviewer",
                    "--operation-id",
                    "exact-review",
                    "--brief-file",
                    str(brief),
                    "--require-host-route",
                    *extra,
                ]
            )
        return code, json.loads(output.getvalue())

    code, ambiguous = invoke()
    assert code == 1
    assert "ambiguous" in ambiguous["error"]
    assert not (tmp_path / "manager-context" / "peer-operations").exists()

    code, recorded = invoke("--peer-thread-link", "codex://threads/current")
    assert code == 0
    assert recorded["host_delivery"]["status"] == "not_attempted"
    assert recorded["host_delivery"]["thread_id"] == "current"
    assert recorded["request_id"] in recorded["host_delivery"]["message"]
    code, replay = invoke("--peer-thread-link", "codex://threads/current")
    assert code == 0
    assert replay["replayed"] is True
    assert replay["request_id"] == recorded["request_id"]
    code, retarget = invoke("--peer-thread-link", "codex://threads/older")
    assert code == 1
    assert "identity conflict" in retarget["error"]

    def inbox(agent_id: str, action: str, *extra: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main(
                [
                    "--registry",
                    str(registry),
                    "--runtime-root",
                    str(tmp_path),
                    "manager-inbox",
                    action,
                    "--goal-id",
                    "goal",
                    "--agent-id",
                    agent_id,
                    *extra,
                ]
            )
        assert code == 0
        return json.loads(output.getvalue())

    received = inbox("reviewer", "read")
    assert [item["request_id"] for item in received["items"]] == [
        recorded["request_id"]
    ]
    inbox(
        "reviewer",
        "acknowledge",
        "--request-id",
        recorded["request_id"],
        "--decision",
        "adopt",
        "--reason",
        "Review accepted",
    )
    inbox(
        "reviewer",
        "report",
        "--request-id",
        recorded["request_id"],
        "--reply-text",
        "The exact candidate passes the independent review.",
    )
    returned = inbox("builder", "read")["peer_returns"]["items"]
    assert len(returned) == 1
    assert returned[0]["request_id"] == recorded["request_id"]
