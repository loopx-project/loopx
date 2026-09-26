"""Remote reports select real sources and preserve scoped Core evidence."""

import json
import subprocess
import shlex
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from loopx.capabilities.manager_context.ssh_evidence import (
    MANAGER_REMOTE_EVIDENCE_TTL_SECONDS,
    configure,
    grants,
    remote_evidence,
    sources,
)
from loopx.capabilities.manager_context.inspection import ManagerInspection, TOOL_NAME
from loopx.capabilities.manager_context.evidence_export import export_page
from loopx.chat_manager_context import manager_authorization_scope_id
from loopx.chat_manager_history import read_manager_delivery_history


@pytest.fixture
def remote(tmp_path):
    config = tmp_path / "ssh_config"
    config.write_text(
        "Host research-host\n  HostName example.invalid\nHost unrelated\n  HostName other.invalid\n"
    )
    channel = "manager.external." + "a" * 24
    configure(
        tmp_path,
        channel=channel,
        host="research-host",
        goal_ids=["remote-goal"],
        execute=True,
        config_path=config,
    )
    calls, records = [], []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "schema_version": "manager_evidence_page_v1",
                    "ok": True,
                    "rows": [{"goal_id": "remote-goal", "title": "Actual remote task"}],
                    "source": {"coverage": {"discovered": 1}},
                    "next_offset": None,
                }
            ),
        )

    inspection = ManagerInspection(
        context={"goals": [{"goal_id": "local-goal", "host_id": None}]},
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        owner_scope=False,
        channel_id=channel,
        scope_valid=lambda: True,
        record=records.append,
        remote_runner=run,
        ssh_config_path=config,
    )
    return inspection, calls, records, channel, config


def test_remote_source_is_discovered_and_read_without_local_host_metadata(remote):
    tool, calls, records, _, _ = remote
    sources = tool.read(TOOL_NAME, {"view": "sources"})
    assert [r["source_id"] for r in sources["rows"]] == ["local", "ssh:research-host"]
    assert not calls
    result = tool.read(
        TOOL_NAME,
        {"view": "todos", "source_id": "ssh:research-host", "goal_id": "remote-goal"},
    )
    assert result["ok"] and result["rows"][0]["title"] == "Actual remote task"
    assert result["source_host"] == result["rows"][0]["source_host"] == "research-host"
    argv, opts = calls[0]
    assert (
        argv[-2] == "research-host"
        and "--registry" not in argv[-1]
    )
    assert "--manager-view todos" in argv[-1] and "--goal-id remote-goal" in argv[-1]
    assert "BatchMode=yes" in argv and opts["timeout"] == 45
    assert records[-1] == result


@pytest.mark.parametrize(
    "query",
    [
        {"view": "todos", "source_id": "ssh:research-host", "goal_id": "local-goal"},
        {"view": "portfolio", "source_id": "ssh:unrelated"},
        {"view": "portfolio", "source_id": "ssh:unconfigured"},
        {"view": "handoffs", "source_id": "ssh:research-host"},
        {
            "view": "deliveries",
            "source_id": "ssh:research-host",
            "goal_id": "remote-goal",
            "days": 999,
        },
    ],
)
def test_invalid_or_unauthorized_queries_never_connect(remote, query):
    tool, calls, *_ = remote
    assert not tool.read(TOOL_NAME, query)["ok"]
    assert not calls


def test_remote_grant_revocation_discards_inflight_result_and_changes_context_identity(
    remote,
):
    tool, calls, _, channel, config = remote
    old = manager_authorization_scope_id(
        ["local-goal"], runtime_root=tool.runtime_root, channel_id=channel
    )
    original = tool.remote_runner

    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        configure(
            tool.runtime_root,
            channel=channel,
            host="research-host",
            goal_ids=[],
            execute=True,
            config_path=config,
        )
        return result

    tool.remote_runner = revoke
    result = tool.read(
        TOOL_NAME, {"view": "portfolio", "source_id": "ssh:research-host"}
    )
    assert result == {"ok": False, "error": "authorization_changed"}
    assert not grants(tool.runtime_root, channel)
    assert old != manager_authorization_scope_id(
        ["local-goal"], runtime_root=tool.runtime_root, channel_id=channel
    )


def test_old_or_offline_remote_is_unknown_not_empty_healthy(remote):
    tool, *_ = remote

    def unavailable(*_, **__):
        raise subprocess.TimeoutExpired("ssh", 45)

    tool.remote_runner = unavailable
    result = tool.read(
        TOOL_NAME, {"view": "portfolio", "source_id": "ssh:research-host"}
    )
    assert not result["ok"] and result["coverage"] == {
        "discovered": None,
        "complete": False,
    }


def test_export_uses_canonical_todos_and_never_returns_owner_private_continuation(
    tmp_path, monkeypatch
):
    import loopx.chat_manager_details as details

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"goals": [{"id": "remote-goal", "repo": str(tmp_path)}]})
    )
    monkeypatch.setattr(
        details,
        "list_goal_todos",
        lambda **_: {
            "ok": True,
            "source": "canonical",
            "todos": [
                {
                    "todo_id": "t1",
                    "text": "Validate delivery",
                    "status": "open",
                    "note": "Private deliberation",
                }
            ],
        },
    )
    args = SimpleNamespace(
        portfolio_goal_ids=["remote-goal"],
        manager_view="todos",
        limit=8,
        offset=0,
        days=1,
    )
    packet = export_page(registry, str(tmp_path), args)
    assert packet["schema_version"] == "manager_evidence_page_v1"
    assert packet["rows"][0]["goal_id"] == "remote-goal"
    assert packet["rows"][0]["title"] == "Validate delivery"
    assert "Private deliberation" not in json.dumps(packet)


def test_delivery_lookback_can_explain_stale_goals_without_redating_outcomes(tmp_path):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=4)).isoformat()
    index = tmp_path / "goals" / "remote-goal" / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text(
        json.dumps(
            {
                "generated_at": old,
                "goal_id": "remote-goal",
                "agent_id": "worker",
                "todo_id": "t1",
                "delivery_outcome": "outcome_progress",
                "classification": "validated_result",
            }
        )
        + "\n"
    )
    assert not read_manager_delivery_history(tmp_path, "remote-goal", now=now)[
        "deliveries"
    ]
    history = read_manager_delivery_history(
        tmp_path, "remote-goal", now=now, lookback_days=7
    )
    assert len(history["deliveries"]) == 1
    assert old in json.dumps(history["deliveries"])


def test_ssh_wire_arguments_execute_real_remote_cli_projection(remote, tmp_path):
    tool, _, _, _, _ = remote
    registry = tmp_path / "remote-registry.json"
    registry.write_text(
        json.dumps({"goals": [{"id": "remote-goal", "repo": str(tmp_path)}]})
    )

    def execute_cli(argv, **kwargs):
        arguments = shlex.split(argv[-1].split("--format json ", 1)[1])
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--registry",
                str(registry),
                "--runtime-root",
                str(tmp_path / "remote-runtime"),
                "--format",
                "json",
                *arguments,
            ],
            **kwargs,
        )
        assert completed.returncode == 0, completed.stderr
        return completed

    tool.remote_runner = execute_cli
    result = tool.read(
        TOOL_NAME, {"view": "portfolio", "source_id": "ssh:research-host", "limit": 3}
    )
    assert result["ok"], result
    assert result["rows"][0]["goal_id"] == "remote-goal"
    assert result["rows"][0]["source_host"] == "research-host"
    assert result["rows"][0]["quality"] != "verified"


def test_sources_paginate_and_malformed_remote_output_is_unknown(remote):
    tool, _, _, _, _ = remote
    tool.context["goals"].append({"goal_id": "remote-goal", "title": "Local namesake"})
    first = tool.read(TOOL_NAME, {"view": "sources", "limit": 1})
    assert first["next_offset"] == 1 and first["matched"] == 2
    assert (
        tool.read(TOOL_NAME, {"view": "sources", "offset": 1})["rows"][0]["source_id"]
        == "ssh:research-host"
    )
    assert (
        tool.read(
            TOOL_NAME,
            {
                "view": "todos",
                "source_id": "ssh:research-host",
                "goal_id": "remote-goal",
            },
        )["rows"][0]["title"]
        == "Actual remote task"
    )
    tool.remote_runner = lambda *_, **__: SimpleNamespace(returncode=0, stdout="[]")
    assert not tool.read(
        TOOL_NAME, {"view": "portfolio", "source_id": "ssh:research-host"}
    )["ok"]


def _evidence_root(tmp_path, *, registered=("research-host",), aliases=None):
    """One runtime root with a registered evidence host and an SSH config."""

    config = tmp_path / "ssh_config"
    names = list(aliases if aliases is not None else registered) + ["github.com"]
    config.write_text(
        "".join(f"Host {name}\n  HostName {name}.invalid\n" for name in names)
    )
    channel = "manager.external." + "d" * 24
    for host in registered:
        configure(
            tmp_path,
            channel=channel,
            host=host,
            goal_ids=["remote-goal"],
            execute=True,
            config_path=config,
        )
    return config, channel


def _packet_runner(calls, *, goal_ids=("remote-goal",)):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "schema_version": "manager_evidence_page_v1",
                    "ok": True,
                    "rows": [
                        {
                            "goal_id": goal_id,
                            "activation_state": "active",
                            "quality": "stale",
                            "progress": "unknown",
                            "source": {"latest_recorded_at": "2026-09-14T00:00:00+08:00"},
                            "warnings": ["latest_run_projection_stale"],
                        }
                        for goal_id in goal_ids
                    ],
                    "source": {"coverage": {"discovered": len(goal_ids)}},
                    "next_offset": None,
                }
            ),
        )

    return run


def test_only_registered_evidence_hosts_are_declared(tmp_path):
    # Owner scope used to declare every configured alias, which put hosts with
    # no LoopX state (github.com) in front of the model as if they were sources.
    config, _ = _evidence_root(tmp_path)
    assert [row["source_id"] for row in sources(tmp_path, "manager", True, config)] == [
        "local",
        "ssh:research-host",
    ]
    assert [row["source_id"] for row in sources(tmp_path, "manager", False, config)] == [
        "local"
    ]
    # A registered host that lost its alias is drift the answer must name.
    config.write_text("Host github.com\n  HostName github.com\n")
    drift = sources(tmp_path, "manager", True, config)[1]
    assert drift["status"] == "not_configured"
    assert drift["reason"] == "ssh_alias_not_configured"


def test_registered_source_is_read_once_then_served_from_the_cache(tmp_path):
    config, channel = _evidence_root(tmp_path)
    calls = []
    runner = _packet_runner(calls)

    first = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
    )
    assert first["read_status"] == "read"
    assert first["sources"][0]["status"] == "read"
    assert first["sources"][0]["fresh"] is True
    assert [row["goal_id"] for row in first["rows"]] == ["remote-goal"]
    assert first["rows"][0]["source_freshness"] == "current"
    assert len(calls) == 1

    second = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
    )
    # A second Turn inside the TTL reuses the read instead of dialling again.
    assert len(calls) == 1
    assert second["sources"][0]["status"] == "cached"
    assert second["rows"] == first["rows"]

    expired = datetime.now(timezone.utc) + timedelta(
        seconds=MANAGER_REMOTE_EVIDENCE_TTL_SECONDS + 1
    )
    third = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
        now=expired,
    )
    assert len(calls) == 2 and third["sources"][0]["status"] == "read"
    argv, opts = calls[-1]
    assert argv[-2] == "research-host" and opts["timeout"] <= 9
    assert "--manager-view portfolio" in argv[-1] and "--days 7" in argv[-1]
    assert "--goal-id remote-goal" in argv[-1]
    assert calls[-1][0][0] == "ssh"


def test_failed_source_read_is_typed_and_never_claims_no_progress(tmp_path):
    config, channel = _evidence_root(tmp_path)
    calls = []
    working = _packet_runner(calls)
    remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=working,
    )

    def failing(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=255, stdout="")

    later = datetime.now(timezone.utc) + timedelta(
        seconds=MANAGER_REMOTE_EVIDENCE_TTL_SECONDS + 1
    )
    result = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=failing,
        now=later,
    )
    assert result["read_status"] == "unavailable"
    source = result["sources"][0]
    assert source["status"] == "unavailable"
    # The reason now names the side that has to change instead of echoing an
    # internal code; the typed code travels beside it for programmatic readers.
    assert source["reason"] != "remote_evidence_unavailable_or_upgrade_required"
    assert source["reason_code"] == "remote_evidence_unavailable"
    assert source["last_success_at"] and source["stale_rows_included"] is True
    assert source["coverage_effect"] == "remote_goals_may_be_outdated_not_absent"
    assert result["stale_rows_included"] is True
    assert "remote_source_rows_are_stale" in result["limitations"]
    assert result["rows"][0]["source_freshness"] == "stale"


def test_failed_point_read_keeps_the_last_successful_portfolio_visible(tmp_path):
    config, channel = _evidence_root(tmp_path)
    remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=_packet_runner([]),
    )

    def rejected(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            255,
            stdout="",
            stderr="Permission denied (gssapi-with-mic).",
        )

    inspection = ManagerInspection(
        context={"goals": []},
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        owner_scope=False,
        channel_id=channel,
        scope_valid=lambda: True,
        record=lambda _result: None,
        remote_runner=rejected,
        ssh_config_path=config,
    )
    result = inspection.read(
        TOOL_NAME,
        {"view": "portfolio", "source_id": "ssh:research-host"},
    )

    assert result["ok"] is False
    assert result["reason_code"] == "ssh_auth_required"
    assert "kinit" in result["reason"]
    assert result["last_success_at"]
    assert result["cached_window_days"] == 7
    assert result["stale_rows_included"] is True
    assert result["rows"] == []
    assert [row["goal_id"] for row in result["stale_portfolio_rows"]] == [
        "remote-goal"
    ]
    assert result["stale_portfolio_rows"][0]["source_freshness"] == "stale"
    assert result["coverage_effect"] == "remote_goals_may_be_outdated_not_absent"
    assert "do not present" in result["next_action"]


def test_failed_point_read_does_not_reveal_cache_after_scope_revocation(tmp_path):
    config, channel = _evidence_root(tmp_path)
    remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=_packet_runner([]),
    )
    checks = iter([True, True, False])
    inspection = ManagerInspection(
        context={"goals": []},
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path,
        owner_scope=False,
        channel_id=channel,
        scope_valid=lambda: next(checks),
        record=lambda _result: None,
        remote_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            argv,
            255,
            stdout="",
            stderr="Permission denied (gssapi-with-mic).",
        ),
        ssh_config_path=config,
    )

    assert inspection.read(
        TOOL_NAME,
        {"view": "portfolio", "source_id": "ssh:research-host"},
    ) == {"ok": False, "error": "authorization_changed"}


def test_one_dial_per_turn_defers_the_other_declared_source(tmp_path):
    config, channel = _evidence_root(
        tmp_path, registered=("research-host", "second-host")
    )
    calls = []
    result = remote_evidence(
        tmp_path,
        channel,
        True,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=_packet_runner(calls),
    )
    assert len(calls) == 1
    statuses = {row["source_id"]: row["status"] for row in result["sources"]}
    assert sorted(statuses) == ["ssh:research-host", "ssh:second-host"]
    assert "deferred_budget" in statuses.values()
    assert result["read_status"] in {"read", "partial"}
    assert "remote_source_deferred_to_next_turn" in result["limitations"]


def test_the_single_dial_rotates_to_the_source_read_longest_ago(tmp_path):
    """A Turn dials one host, so declaration order must not decide who starves.

    With two registered hosts and a Turn interval longer than the cache TTL, a
    declaration-ordered dial let the first declared host take every read while
    the second could only ever report `deferred_budget`. The dial now rotates to
    the source read longest ago -- or never -- first.
    """

    config, channel = _evidence_root(
        tmp_path, registered=("first-host", "second-host")
    )
    calls = []
    runner = _packet_runner(calls)

    first = remote_evidence(
        tmp_path,
        channel,
        True,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
    )
    # No source has history yet, so the first Turn dials one and defers the
    # other, and the packet still reports both in declaration order.
    assert len(calls) == 1
    assert [row["source_id"] for row in first["sources"]] == [
        "ssh:first-host",
        "ssh:second-host",
    ]
    read_first, deferred_first = (row["status"] for row in first["sources"])
    assert {read_first, deferred_first} == {"read", "deferred_budget"}

    expired = datetime.now(timezone.utc) + timedelta(
        seconds=MANAGER_REMOTE_EVIDENCE_TTL_SECONDS + 1
    )
    second = remote_evidence(
        tmp_path,
        channel,
        True,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
        now=expired,
    )

    # The never-read source takes the dial this time instead of starving behind
    # the expired read that used to win on declaration order alone.
    assert len(calls) == 2
    statuses = {row["source_id"]: row["status"] for row in second["sources"]}
    assert statuses["ssh:second-host"] == "read"
    assert statuses["ssh:first-host"] == "deferred_budget"
    assert second["read_status"] == "partial"
    assert calls[-1][0][-2] == "second-host"
    assert "remote_source_deferred_to_next_turn" in second["limitations"]


def test_grant_change_invalidates_the_cached_source_read(tmp_path):
    config, channel = _evidence_root(tmp_path)
    calls = []
    runner = _packet_runner(calls)
    remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
    )
    configure(
        tmp_path,
        channel=channel,
        host="research-host",
        goal_ids=["remote-goal", "second-goal"],
        execute=True,
        config_path=config,
    )
    result = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
    )
    # A widened grant must not be answered from the narrower cached read.
    assert len(calls) == 2 and result["sources"][0]["status"] == "read"


def test_scope_change_between_reads_refuses_the_packet(tmp_path):
    config, channel = _evidence_root(tmp_path)
    calls = []
    result = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: False,
        config_path=config,
        runner=_packet_runner(calls),
    )
    # An invalid scope refuses before the dial, not after reading remote rows.
    assert not calls
    assert result["sources"][0]["status"] == "unavailable"
    assert result["sources"][0]["reason"] == "source_outside_available_scope"
    assert result["read_status"] == "unavailable"


@pytest.mark.parametrize(
    "stderr,returncode,expected",
    [
        ("huangruiteng@203.0.113.7: Permission denied (gssapi-with-mic).", 255, "ssh_auth_required"),
        # A remote command reporting a bare permission error about its own
        # files is not this machine's rejected credential.
        ("loopx: /home/x/.config: Permission denied", 1, "remote_evidence_unavailable"),
        ("ssh: connect to host 203.0.113.7 port 22: Operation timed out", 255, "ssh_host_unreachable"),
        ("ssh: Could not resolve hostname ark-devbox: Name or service not known", 255, "ssh_host_unreachable"),
        ("bash: line 1: /home/x/.local/bin/loopx: No such file or directory", 127, "remote_cli_missing"),
        ("some other ssh failure", 255, "remote_evidence_unavailable"),
    ],
)
def test_a_failed_read_names_the_side_that_has_to_change(
    stderr: str, returncode: int, expected: str
) -> None:
    from loopx.capabilities.manager_context.ssh_evidence import _remote_read_failure

    code, reason = _remote_read_failure(returncode=returncode, stderr=stderr)

    assert code == expected
    assert reason


def test_an_authentication_failure_is_reported_as_such_not_as_a_dead_host(
    tmp_path,
) -> None:
    """A missing Kerberos ticket must not read as 'the host is down'."""

    config, channel = _evidence_root(tmp_path)

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv,
            255,
            stdout="",
            stderr="huangruiteng@203.0.113.7: Permission denied (gssapi-with-mic).",
        )

    packet = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
    )

    assert packet["read_status"] == "unavailable"
    source = packet["sources"][0]
    assert source["status"] == "unavailable"
    assert source["reason_code"] == "ssh_auth_required"
    # The reason names the repair, not only the symptom, because renewing the
    # credential is the owner's action and the answer has to be able to say so.
    assert "Kerberos" in source["reason"] and "kinit" in source["reason"]
    assert packet["limitations"] == ["remote_source_ssh_auth_required"]
    assert "Tell the owner" in source["next_action"]
    # The compatibility code stays, so older readers still see an unread source.
    assert source["coverage_effect"] == "remote_goals_may_be_outdated_not_absent"


@pytest.mark.parametrize(
    "stderr,returncode,expected_limitation",
    [
        (
            "huangruiteng@203.0.113.7: Permission denied (gssapi-with-mic).",
            255,
            "remote_source_ssh_auth_required",
        ),
        (
            "ssh: connect to host 203.0.113.7 port 22: Operation timed out",
            255,
            "remote_source_ssh_host_unreachable",
        ),
        (
            "bash: line 1: /home/x/.local/bin/loopx: No such file or directory",
            127,
            "remote_source_cli_missing",
        ),
        # A zero exit with an unparseable answer is the remote CLI being older
        # than the read, not a host or a credential problem.
        ("", 0, "remote_source_protocol_unavailable"),
        # A cause outside the classification still declares a source that needs
        # a repair rather than a remote Goal without progress.
        ("some unclassified ssh failure", 255, "remote_source_unavailable"),
    ],
)
def test_every_failed_read_declares_a_bounded_typed_limitation(
    tmp_path, stderr: str, returncode: int, expected_limitation: str
) -> None:
    config, channel = _evidence_root(tmp_path)

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, returncode, stdout="", stderr=stderr
        )

    packet = remote_evidence(
        tmp_path,
        channel,
        False,
        window_days=7,
        scope_valid=lambda: True,
        config_path=config,
        runner=runner,
    )

    assert packet["read_status"] == "unavailable"
    assert packet["sources"][0]["status"] == "unavailable"
    # Exactly one code per cause: the summary teaches the repair without letting
    # a reader mistake an unread source for a remote Goal with no progress.
    assert packet["limitations"] == [expected_limitation]


def test_remote_agent_search_keeps_source_and_audience_scope(remote):
    tool, calls, records, _, _ = remote
    result = tool.read(TOOL_NAME, {"view": "agents", "source_id": "ssh:research-host", "query": "review; $(noop)", "offset": 12})
    assert result["ok"]
    argv = shlex.split(calls[0][0][-1])
    assert argv[argv.index("--manager-view") + 1] == "agents"
    assert argv[argv.index("--query") + 1] == "review; $(noop)"
    assert argv[argv.index("--goal-id") + 1] == "remote-goal"
    assert argv[argv.index("--offset") + 1] == "12"
    assert records[-1]["source_id"] == "ssh:research-host"
