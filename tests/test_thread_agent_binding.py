from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.global_registry import global_registry_path
from loopx.control_plane.runtime.public_safety import validate_public_safe_value
from loopx.thread_agent_binding import (
    ROUTE_MULTIPLE_CANDIDATES,
    ROUTE_NO_CANDIDATE,
    ROUTE_SINGLE_CANDIDATE,
    ThreadBindingRequestError,
    bind_thread_agent_in_registry,
    codex_thread_deep_link_locator,
    normalize_thread_id,
    resolve_registry_thread_agent_binding,
    resolve_thread_agent_binding,
    summarize_agent_binding_routes,
    unbind_thread_agent_in_registry,
)


def _registry(tmp_path: Path, agents: list[str]) -> Path:
    path = tmp_path / ".loopx" / "registry.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": "goal",
                        "coordination": {"registered_agents": agents},
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_thread_id_is_bounded_and_opaque() -> None:
    assert normalize_thread_id(" thread-1 ") == "thread-1"
    assert normalize_thread_id(None) is None
    with pytest.raises(ValueError):
        normalize_thread_id("thread with spaces")
    with pytest.raises(ValueError):
        normalize_thread_id("x" * 129)


def test_codex_deep_link_is_a_locator_not_authority() -> None:
    assert codex_thread_deep_link_locator(
        "codex://threads/019fd0fe-42e0-7b21-accc-045333a7def9"
    ) == {
        "schema_version": "loopx_host_session_locator_v0",
        "kind": "codex_deep_link",
        "status": "parsed",
        "host_family": "codex",
        "thread_id": "019fd0fe-42e0-7b21-accc-045333a7def9",
        "deep_link": "codex://threads/019fd0fe-42e0-7b21-accc-045333a7def9",
        "context_scope_ref": (
            "host-session:codex:019fd0fe-42e0-7b21-accc-045333a7def9"
        ),
        "authority": "locator_only",
    }


@pytest.mark.parametrize(
    "thread_link",
    [
        "https://example.com/threads/thread-a",
        "codex://settings",
        "codex://threads/new",
        "codex://threads/thread-a/extra",
        "codex://threads/thread-a?host=other",
        "codex://threads/thread%2Fa",
        "codex://threads/thread:a",
        "codex://threads/线程-a",
    ],
)
def test_codex_deep_link_rejects_noncanonical_addresses(thread_link: str) -> None:
    with pytest.raises(ValueError):
        codex_thread_deep_link_locator(thread_link)


def test_binding_lookup_is_fail_closed_without_thread_id() -> None:
    goal = {
        "coordination": {
            "registered_agents": ["agent-a", "agent-b"],
            "thread_agent_bindings": [
                {
                    "thread_id": "thread-a",
                    "host_surface": "codex-app",
                    "agent_id": "agent-a",
                }
            ],
        }
    }
    assert (
        resolve_thread_agent_binding(goal, host_surface="codex-app", thread_id=None)[
            "status"
        ]
        == "unavailable"
    )
    assert (
        resolve_thread_agent_binding(
            goal, host_surface="codex-app", thread_id="thread-a"
        )["agent_id"]
        == "agent-a"
    )


def test_project_binding_resolution_returns_one_exact_goal_and_agent(
    tmp_path: Path,
) -> None:
    path = _registry(tmp_path, ["agent-a"])
    assert bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="deepseek-harness-native",
        thread_id="dsh-session-1",
        agent_id="agent-a",
        execute=True,
    )["ok"] is True

    resolved = resolve_registry_thread_agent_binding(
        registry_path=path,
        host_surface="deepseek-harness-native",
        thread_id="dsh-session-1",
    )
    assert resolved == {
        "ok": True,
        "schema_version": "loopx_thread_agent_binding_resolution_v0",
        "host_surface": "deepseek-harness-native",
        "thread_id": "dsh-session-1",
        "status": "bound",
        "goal_id": "goal",
        "agent_id": "agent-a",
        "matches": [{"goal_id": "goal", "agent_id": "agent-a"}],
    }
    missing = resolve_registry_thread_agent_binding(
        registry_path=path,
        host_surface="deepseek-harness-native",
        thread_id="another-session",
    )
    assert missing["ok"] is True
    assert missing["status"] == "missing"


def test_resolve_agent_thread_cli_is_read_only_and_path_free(tmp_path: Path) -> None:
    path = _registry(tmp_path, ["agent-a"])
    assert bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="deepseek-harness-native",
        thread_id="dsh-session-1",
        agent_id="agent-a",
        execute=True,
    )["ok"] is True
    before = path.read_bytes()
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(path),
                "--format",
                "json",
                "resolve-agent-thread",
                "--host-surface",
                "deepseek-harness-native",
                "--thread-id",
                "dsh-session-1",
            ]
        )

    assert exit_code == 0
    payload = json.loads(output.getvalue())
    assert payload["status"] == "bound"
    assert payload["goal_id"] == "goal"
    assert payload["agent_id"] == "agent-a"
    assert "registry" not in payload
    assert str(tmp_path) not in output.getvalue()
    assert path.read_bytes() == before

    link_output = io.StringIO()
    with contextlib.redirect_stdout(link_output):
        link_exit = cli_main(
            [
                "--registry",
                str(path),
                "--format",
                "json",
                "resolve-agent-thread",
                "--thread-link",
                "codex://threads/dsh-session-1",
            ]
        )
    assert link_exit == 0
    linked = json.loads(link_output.getvalue())
    assert linked["status"] == "missing"
    assert linked["thread_id"] == "dsh-session-1"
    assert linked["host_surface"] is None
    assert linked["host_family"] == "codex"
    assert linked["session_locator"]["authority"] == "locator_only"
    assert path.read_bytes() == before

    markdown = io.StringIO()
    with contextlib.redirect_stdout(markdown):
        markdown_exit = cli_main(
            [
                "--registry",
                str(path),
                "resolve-agent-thread",
                "--host-surface",
                "deepseek-harness-native",
                "--thread-id",
                "dsh-session-1",
            ]
        )
    assert markdown_exit == 0
    assert markdown.getvalue().startswith("# LoopX Host Thread Binding\n")
    assert "Agent Registration" not in markdown.getvalue()


def test_resolve_codex_deep_link_confirms_exact_bound_goal_and_agent(
    tmp_path: Path,
) -> None:
    path = _registry(tmp_path, ["agent-a"])
    assert bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-a",
        agent_id="agent-a",
        execute=True,
    )["ok"] is True

    resolved = resolve_registry_thread_agent_binding(
        registry_path=path,
        host_surface="codex-app",
        thread_link="codex://threads/thread-a",
    )

    assert resolved["status"] == "bound"
    assert resolved["goal_id"] == "goal"
    assert resolved["agent_id"] == "agent-a"
    assert resolved["session_locator"] == {
        "schema_version": "loopx_host_session_locator_v0",
        "kind": "codex_deep_link",
        "status": "parsed",
        "host_family": "codex",
        "thread_id": "thread-a",
        "deep_link": "codex://threads/thread-a",
        "context_scope_ref": "host-session:codex:thread-a",
        "authority": "locator_only",
    }

    markdown = io.StringIO()
    with contextlib.redirect_stdout(markdown):
        markdown_exit = cli_main(
            [
                "--registry",
                str(path),
                "resolve-agent-thread",
                "--thread-link",
                "codex://threads/thread-a",
            ]
        )
    assert markdown_exit == 0
    assert "- locator_authority: `locator_only`" in markdown.getvalue()


def test_resolve_codex_deep_link_searches_codex_host_family(
    tmp_path: Path,
) -> None:
    path = _registry(tmp_path, ["agent-a"])
    assert bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app-ssh",
        thread_id="thread-a",
        agent_id="agent-a",
        execute=True,
    )["ok"] is True

    resolved = resolve_registry_thread_agent_binding(
        registry_path=path,
        thread_link="codex://threads/thread-a",
    )

    assert resolved["status"] == "bound"
    assert resolved["host_surface"] is None
    assert resolved["host_family"] == "codex"
    assert resolved["matched_host_surfaces"] == ["codex-app-ssh"]
    assert resolved["matches"] == [
        {
            "goal_id": "goal",
            "agent_id": "agent-a",
            "host_surface": "codex-app-ssh",
        }
    ]


def test_resolve_codex_deep_link_fails_closed_for_other_host_surfaces(
    tmp_path: Path,
) -> None:
    path = _registry(tmp_path, ["agent-a"])
    with pytest.raises(ThreadBindingRequestError):
        resolve_registry_thread_agent_binding(
            registry_path=path,
            host_surface="deepseek-harness-native",
            thread_link="codex://threads/thread-a",
        )


def test_resolve_codex_deep_link_cli_rejects_invalid_link_without_echoing_it(
    tmp_path: Path,
) -> None:
    path = _registry(tmp_path, ["agent-a"])
    invalid_link = "codex://threads/thread-a?private=value"
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(path),
                "--format",
                "json",
                "resolve-agent-thread",
                "--thread-link",
                invalid_link,
            ]
        )

    assert exit_code == 1
    payload = json.loads(output.getvalue())
    assert payload["error_kind"] == "thread_agent_binding_invalid_request"
    assert payload["session_locator"] is None
    assert invalid_link not in output.getvalue()
    assert "private=value" not in output.getvalue()


def test_resolve_agent_thread_cli_failure_is_bounded_and_path_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loopx.cli_commands import registry_admin_thread_resolution

    def fail_resolution(**_kwargs: object) -> dict[str, object]:
        raise OSError(f"private registry failure at {tmp_path}")

    monkeypatch.setattr(
        registry_admin_thread_resolution,
        "resolve_registry_thread_agent_binding",
        fail_resolution,
    )
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(tmp_path / "registry.json"),
                "--format",
                "json",
                "resolve-agent-thread",
                "--host-surface",
                "deepseek-harness-native",
                "--thread-id",
                "dsh-session-1",
            ]
        )

    assert exit_code == 1
    payload = json.loads(output.getvalue())
    assert payload["error_kind"] == "thread_agent_binding_resolution_failed"
    assert payload["error"] == "thread binding authority could not be read"
    assert str(tmp_path) not in output.getvalue()


def test_resolve_agent_thread_reports_corrupt_authority_as_read_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "registry.json"
    path.write_text("{not-json\n", encoding="utf-8")
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(path),
                "--format",
                "json",
                "resolve-agent-thread",
                "--host-surface",
                "deepseek-harness-native",
                "--thread-id",
                "dsh-session-1",
            ]
        )

    assert exit_code == 1
    payload = json.loads(output.getvalue())
    assert payload["error_kind"] == "thread_agent_binding_resolution_failed"
    assert payload["host_surface"] == "deepseek-harness-native"
    assert payload["thread_id"] == "dsh-session-1"


def test_resolve_agent_thread_cli_rejects_invalid_identity_without_echoing_it(
    tmp_path: Path,
) -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(tmp_path / "registry.json"),
                "--format",
                "json",
                "resolve-agent-thread",
                "--host-surface",
                "deepseek-harness-native",
                "--thread-id",
                "invalid session id",
            ]
        )

    assert exit_code == 1
    payload = json.loads(output.getvalue())
    assert payload["error_kind"] == "thread_agent_binding_invalid_request"
    assert payload["error"] == "thread binding request is invalid"
    assert "invalid session id" not in output.getvalue()


def test_resolve_agent_thread_never_falls_back_to_the_global_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    global_path = global_registry_path(runtime_root)
    global_path.parent.mkdir(parents=True)
    global_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "goals": [
                    {
                        "id": "global-goal",
                        "coordination": {
                            "registered_agents": ["global-agent"],
                            "thread_agent_bindings": [
                                {
                                    "host_surface": "deepseek-harness-native",
                                    "thread_id": "same-session",
                                    "agent_id": "global-agent",
                                }
                            ],
                        },
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    output = io.StringIO()

    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--runtime-root",
                str(runtime_root),
                "--format",
                "json",
                "resolve-agent-thread",
                "--host-surface",
                "deepseek-harness-native",
                "--thread-id",
                "same-session",
            ]
        )

    assert exit_code == 0
    payload = json.loads(output.getvalue())
    assert payload["status"] == "missing"
    assert payload["matches"] == []


def test_project_binding_resolution_fails_closed_across_goals(tmp_path: Path) -> None:
    path = _registry(tmp_path, ["agent-a"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["goals"] = [
        {
            "id": "goal-a",
            "coordination": {
                "registered_agents": ["agent-a"],
                "thread_agent_bindings": [
                    {
                        "host_surface": "deepseek-harness-native",
                        "thread_id": "same-session",
                        "agent_id": "agent-a",
                    }
                ],
            },
        },
        {
            "id": "goal-b",
            "coordination": {
                "registered_agents": ["agent-b"],
                "thread_agent_bindings": [
                    {
                        "host_surface": "deepseek-harness-native",
                        "thread_id": "same-session",
                        "agent_id": "agent-b",
                    }
                ],
            },
        },
    ]
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    resolved = resolve_registry_thread_agent_binding(
        registry_path=path,
        host_surface="deepseek-harness-native",
        thread_id="same-session",
    )
    assert resolved["ok"] is False
    assert resolved["status"] == "ambiguous"
    assert resolved["error_kind"] == "thread_agent_binding_ambiguous"
    assert resolved["goal_id"] is None
    assert resolved["agent_id"] is None


def test_binding_is_idempotent_and_conflicts_fail_closed(tmp_path: Path) -> None:
    path = _registry(tmp_path, ["agent-a", "agent-b"])
    first = bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-a",
        agent_id="agent-a",
        execute=True,
    )
    assert first["ok"] is True
    assert first["written"] is True
    second = bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-a",
        agent_id="agent-a",
        execute=True,
    )
    assert second["ok"] is True
    assert second["changed"] is False
    conflict = bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-a",
        agent_id="agent-b",
        execute=True,
    )
    assert conflict["ok"] is False
    assert conflict["error_kind"] == "thread_agent_binding_conflict"
    payload = json.loads(path.read_text(encoding="utf-8"))
    bindings = payload["goals"][0]["coordination"]["thread_agent_bindings"]
    assert bindings == [
        {"thread_id": "thread-a", "host_surface": "codex-app", "agent_id": "agent-a"}
    ]


def test_unbind_removes_only_the_exact_thread_and_preserves_agent_registration(
    tmp_path: Path,
) -> None:
    path = _registry(tmp_path, ["agent-a", "agent-b"])
    for thread_id in ("thread-current", "thread-other"):
        bound = bind_thread_agent_in_registry(
            registry_path=path,
            goal_id="goal",
            host_surface="codex-app",
            thread_id=thread_id,
            agent_id="agent-b",
            execute=True,
        )
        assert bound["ok"] is True

    before_preview = path.read_bytes()
    preview = unbind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-current",
        agent_id="agent-b",
        execute=False,
    )
    assert preview["ok"] is True
    assert preview["changed"] is True
    assert preview["written"] is False
    assert path.read_bytes() == before_preview

    unbound = unbind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-current",
        agent_id="agent-b",
        execute=True,
    )

    assert unbound["ok"] is True
    assert unbound["changed"] is True
    assert unbound["written"] is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    goal = payload["goals"][0]
    assert goal["coordination"]["registered_agents"] == ["agent-a", "agent-b"]
    assert goal["coordination"]["thread_agent_bindings"] == [
        {
            "thread_id": "thread-other",
            "host_surface": "codex-app",
            "agent_id": "agent-b",
        }
    ]
    assert resolve_thread_agent_binding(
        goal,
        host_surface="codex-app",
        thread_id="thread-current",
    )["status"] == "missing"
    assert resolve_thread_agent_binding(
        goal,
        host_surface="codex-app",
        thread_id="thread-other",
    )["agent_id"] == "agent-b"

    rebound = bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-current",
        agent_id="agent-a",
        execute=True,
    )
    assert rebound["ok"] is True


def test_unbind_is_idempotent_and_expected_agent_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    path = _registry(tmp_path, ["agent-a", "agent-b"])
    assert bind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-current",
        agent_id="agent-b",
        execute=True,
    )["ok"] is True
    before = path.read_bytes()

    mismatch = unbind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-current",
        agent_id="agent-a",
        execute=True,
    )
    assert mismatch["ok"] is False
    assert mismatch["error_kind"] == "thread_agent_binding_agent_mismatch"
    assert path.read_bytes() == before

    missing = unbind_thread_agent_in_registry(
        registry_path=path,
        goal_id="goal",
        host_surface="codex-app",
        thread_id="thread-missing",
        agent_id="agent-b",
        execute=True,
    )
    assert missing["ok"] is True
    assert missing["changed"] is False
    assert path.read_bytes() == before


def _binding(
    agent_id: str,
    thread_id: str,
    host_surface: str = "codex-app",
) -> dict[str, str]:
    return {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "host_surface": host_surface,
    }


def _goal(*bindings: dict[str, str]) -> dict[str, object]:
    return {"coordination": {"thread_agent_bindings": [dict(b) for b in bindings]}}


def _routes(bindings: list[dict[str, str]], agent_id: str) -> dict[str, object]:
    return summarize_agent_binding_routes([_goal(*bindings)], agent_id=agent_id)


def test_route_summary_reports_a_single_candidate_and_keeps_full_identity() -> None:
    summary = _routes([_binding("peer", "thread-one")], "peer")

    assert summary["outcome"] == ROUTE_SINGLE_CANDIDATE
    assert summary["candidate_count"] == 1
    assert summary["candidates"] == [
        {"thread_id": "thread-one", "host_surface": "codex-app"}
    ]
    assert summary["address_shared"] is False
    assert summary["scope"] == "goals_supplied"


def test_route_summary_reports_several_candidates_rather_than_choosing_one() -> None:
    summary = _routes(
        [
            _binding("peer", "thread-old"),
            _binding("peer", "thread-new", host_surface="codex-cli"),
        ],
        "peer",
    )

    assert summary["outcome"] == ROUTE_MULTIPLE_CANDIDATES
    assert summary["candidate_count"] == 2
    assert [item["thread_id"] for item in summary["candidates"]] == [
        "thread-old",
        "thread-new",
    ]


def test_route_summary_counts_a_republished_binding_once() -> None:
    summary = summarize_agent_binding_routes(
        [
            _goal(_binding("peer", "thread-shared")),
            _goal(_binding("peer", "thread-shared"), _binding("peer", "thread-extra")),
        ],
        agent_id="peer",
    )

    assert summary["candidate_count"] == 2
    assert summary["outcome"] == ROUTE_MULTIPLE_CANDIDATES
    assert [item["thread_id"] for item in summary["candidates"]] == [
        "thread-shared",
        "thread-extra",
    ]


def test_route_summary_never_caps_the_internal_view() -> None:
    """Size is a publication budget, so the owner keeps every candidate."""

    bindings = [_binding("peer", f"thread-{index}") for index in range(5)]

    summary = _routes(bindings, "peer")

    assert summary["candidate_count"] == 5
    assert len(summary["candidates"]) == 5


def test_route_summary_keeps_other_agents_out_of_the_candidates() -> None:
    summary = _routes(
        [_binding("peer", "thread-peer"), _binding("reviewer", "thread-reviewer")],
        "peer",
    )

    assert summary["candidate_count"] == 1
    assert summary["outcome"] == ROUTE_SINGLE_CANDIDATE


@pytest.mark.parametrize("agent_id", ["agent-absent", "", None, 42, "x" * 400])
def test_route_summary_reports_no_candidate_for_any_unaddressable_agent(
    agent_id: object,
) -> None:
    summary = summarize_agent_binding_routes(
        [_goal(_binding("peer", "thread-peer"))], agent_id=agent_id
    )

    assert summary["outcome"] == ROUTE_NO_CANDIDATE
    assert summary["candidate_count"] == 0
    assert summary["candidates"] == []


def test_one_candidate_shared_by_two_agents_is_not_called_unique() -> None:
    """The reverse view must not contradict the forward resolver on the same data.

    One host thread bound to two Agents is exactly the registry conflict
    `resolve_thread_agent_binding` answers `conflict` for. Per-agent counts here
    stay at one, so the label has to be `single_candidate` plus an explicit
    shared-address fact rather than a resolved route.
    """

    bindings = [
        _binding("peer", "thread-shared"),
        _binding("reviewer", "thread-shared"),
    ]
    forward = resolve_thread_agent_binding(
        _goal(*bindings), host_surface="codex-app", thread_id="thread-shared"
    )

    for agent_id in ("peer", "reviewer"):
        summary = _routes(bindings, agent_id)
        assert summary["outcome"] == ROUTE_SINGLE_CANDIDATE
        assert summary["candidate_count"] == 1
        assert summary["address_shared"] is True

    assert forward["status"] == "conflict"
    assert sorted(item["agent_id"] for item in forward["matches"]) == [
        "peer",
        "reviewer",
    ]


def test_agent_directory_withholds_a_credential_shaped_thread_from_the_packet(
    tmp_path,
) -> None:
    """A binding the registry accepts can still be unfit to publish.

    Registered through the real write entry point, then read back through the
    real CLI. The published packet must not carry the value, must not pretend
    the binding is gone, and must announce the withholding.
    """

    registry = _registry(tmp_path, ["agent-a"])
    secret_thread = "ghp_" + "1234567890abcdefghijklmnopqrstuvwxyz1234"
    assert (
        bind_thread_agent_in_registry(
            registry_path=registry,
            goal_id="goal",
            host_surface="codex-app",
            thread_id=secret_thread,
            agent_id="agent-a",
            execute=True,
        )["ok"]
        is True
    )

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = cli_main(
            [
                "--registry",
                str(registry),
                "--format",
                "json",
                "agent-directory",
                "--goal-id",
                "goal",
                "--agent-id",
                "agent-a",
                "--scan-path",
                str(tmp_path),
            ]
        )

    published = output.getvalue()
    assert exit_code == 0
    assert secret_thread not in published
    packet = json.loads(published)
    row = next(row for row in packet["rows"] if row["agent_id"] == "agent-a")
    assert row["peer_route"]["candidates"] == []
    assert row["peer_route"]["candidate_count"] == 1
    assert row["peer_route"]["withheld_candidate_count"] == 1
    assert "route_candidate_withheld" in packet["limitations"]
    validate_public_safe_value(packet, path="peer_agent_directory")
