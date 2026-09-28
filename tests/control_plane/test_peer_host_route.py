"""An Agent name is not a host address; select and observe the exact binding."""

from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path

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


def _binding(thread_id: str, agent_id: str = "reviewer") -> dict[str, str]:
    return {"agent_id": agent_id, "host_surface": "codex-app", "thread_id": thread_id}


def _observer(state: HostThreadState):
    return {
        "codex-app": lambda ids: {item: HostThreadActivity(state=state) for item in ids}
    }


def test_named_peer_with_several_bindings_needs_an_exact_selection(
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
