"""Manager scope, restart migration and per-turn Core evidence contracts."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import loopx.chat_manager_context as context
from loopx.chat_manager import (
    MANAGER_AGENT_GOAL_ID,
    MANAGER_AGENT_OBJECTIVE,
    MANAGER_CONTEXT_VERSION,
    manager_model_config,
    manager_workspace,
)
from loopx.chat_agent import CodexChatAgentError
from loopx.chat_runtime import ChatRuntimeController
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.machine_configuration.builtins import (
    build_builtin_machine_configuration_registry,
)
from loopx.capabilities.machine_configuration.store import (
    configure_machine_configuration,
)


def _apply_manager_runtime_profile(runtime_root, profile):
    configuration = {
        "schema_version": "loopx_machine_configuration_v0",
        "namespaces": {
            "manager_runtime": {
                "schema_version": "manager_runtime_profile_v0",
                "runtime_profile": profile,
            }
        },
    }
    registry = build_builtin_machine_configuration_registry()
    preview = configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=configuration,
        registry=registry,
        execute=False,
    )
    configure_machine_configuration(
        runtime_root=runtime_root,
        configuration=configuration,
        registry=registry,
        execute=True,
        expected_plan_revision=preview["plan_revision"],
    )


def test_manager_defaults_are_independent_of_worker_configuration():
    assert manager_model_config({}) == {
        "model": "gpt-6-astra",
        "reasoning_effort": "high",
    }
    assert manager_model_config(
        {"LOOPX_MANAGER_MODEL": "fixture-model", "LOOPX_MANAGER_REASONING_EFFORT": "low"}
    ) == {
        "model": "fixture-model",
        "reasoning_effort": "low",
    }
    with pytest.raises(ValueError):
        manager_model_config({"LOOPX_MANAGER_REASONING_EFFORT": "typo"})


def test_context_scopes_before_read_and_missing_registry_is_unknown(
    monkeypatch, tmp_path
):
    calls = []

    def collect(**kwargs):
        calls.append(kwargs["goal_ids"])
        return {"goals": [], "coverage": {"discovered": 0, "complete": False}}

    monkeypatch.setattr(context, "build_goal_portfolio", collect)
    context.manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager", "goal_id": "anchor"},
        tmp_path,
    )
    context.manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager.external.fixture", "goal_id": "old-anchor"},
        tmp_path,
        authorized_goal_ids=["allowed"],
    )
    assert calls == [None, ["allowed"]]
    missing = context.manager_turn_context(None, {"channel_id": "manager"}, tmp_path)
    assert missing["coverage"]["discovered"] is None
    assert missing["warnings"] == ["registry_unavailable"]


def test_manager_evidence_carries_the_goal_lifecycle_readback(tmp_path, monkeypatch):
    """Milestones and phase reach the manager, or arrive as a named gap.

    The prompt rows, the read index and the paged portfolio view must describe
    the same Goal lifecycle: the status collector's own projection, never a
    second derivation here, and never an absent field the manager could read as
    "this Goal has no milestones".
    """

    import loopx.goal_portfolio as portfolio
    from loopx.capabilities.manager_context.inspection import (
        ManagerInspection,
        TOOL_NAME,
        manager_index,
    )

    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {"id": g, "coordination": {"registered_agents": ["worker"]}}
                    for g in ("alpha", "beta")
                ]
            }
        ),
        encoding="utf-8",
    )
    projection = {
        "schema_version": "goal_artifact_lifecycle_projection_v0",
        "lifecycle_phase": "qualifying",
        "milestones": [
            {"id": "outcome_progress", "reached": True, "reached_evidence_refs": ["sha256:fixture"]}
        ],
        "guards": [],
        "next_transitions": [],
    }

    def read(**kwargs):
        selected = kwargs.get("goal_id")
        ids = [selected] if selected else ["alpha", "beta"]
        goals = []
        for goal_id in ids:
            entry = {
                "id": goal_id,
                "latest_status_run": {
                    "goal_id": goal_id,
                    "generated_at": "2026-09-10T05:00:00Z",
                },
                "latest_runs": [],
                "semantic_history": {"agents": []},
            }
            if goal_id == "alpha":
                entry["artifact_lifecycle"] = {**projection, "goal_id": goal_id}
            goals.append(entry)
        return {"ok": True, "run_history": {"goals": goals}}

    monkeypatch.setattr(portfolio, "collect_status", read)
    monkeypatch.setattr(
        portfolio,
        "build_quota_should_run",
        lambda *a, **k: {"ok": True, "agent_identity": {"agent_id": "worker"}},
    )
    result = context.manager_turn_context(
        registry_path, {"channel_id": "manager"}, runtime_root, include_details=False
    )
    rows = {row["goal_id"]: row for row in result["goals"]}
    assert rows["alpha"]["goal_lifecycle"]["lifecycle_phase"] == "qualifying"
    assert rows["alpha"]["goal_lifecycle"]["milestones"][0]["reached"] is True
    assert rows["beta"]["goal_lifecycle"] == {
        "schema_version": portfolio.GOAL_LIFECYCLE_READBACK_SCHEMA_VERSION,
        "goal_id": "beta",
        "status": "unavailable",
        "reason": "projection_not_derived",
    }
    index = {row["goal_id"]: row for row in manager_index(result)["goals"]}
    assert index["alpha"]["lifecycle_phase"] == "qualifying"
    assert index["beta"]["lifecycle_phase"] is None
    page = ManagerInspection(
        context=result,
        registry_path=registry_path,
        runtime_root=runtime_root,
        owner_scope=True,
        scope_valid=lambda: True,
        record=lambda _: None,
    ).read(TOOL_NAME, {"view": "portfolio"})
    assert page["ok"] is True
    paged = {row["goal_id"]: row["goal_lifecycle"] for row in page["rows"]}
    assert paged["alpha"]["schema_version"] == "goal_artifact_lifecycle_projection_v0"
    assert paged["beta"]["status"] == "unavailable"


def _write_delivery_index(root, goal_id, rows):
    path = root / "goals" / goal_id / "runs" / "index.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return path


def test_turn_context_reads_a_bounded_window_and_declares_sources(
    monkeypatch, tmp_path
):
    now = datetime.now(timezone.utc)
    # A week question needs more than today; the per-day and total bounds must
    # still hold, and the newest receipt must stay fully readable. The six day
    # buckets are anchored to a fixed UTC time-of-day on the previous UTC day so
    # that the per-row minute offsets cannot cross a date boundary: a wall-clock
    # anchor added a seventh bucket, and failed this assertion, for shards that
    # ran between 00:00 and 00:11 UTC.
    newest = (now - timedelta(days=1)).replace(
        hour=18, minute=0, second=0, microsecond=0
    )
    rows = []
    for days_ago in range(6):
        for index in range(12):
            rows.append(
                {
                    "generated_at": (
                        newest - timedelta(days=days_ago, minutes=index)
                    ).isoformat(),
                    "goal_id": "alpha",
                    "agent_id": "worker",
                    "todo_id": f"todo_{days_ago}_{index}",
                    "classification": "validated_progress",
                    "delivery_outcome": "outcome_progress",
                    "recommended_action": f"follow up {days_ago}-{index}",
                }
            )
    _write_delivery_index(tmp_path, "alpha", rows)
    monkeypatch.setattr(
        context,
        "build_goal_portfolio",
        lambda **_: {
            "goals": [{"goal_id": "alpha", "activation_state": "active"}],
            "coverage": {"discovered": 1},
        },
    )
    monkeypatch.setattr(
        context,
        "read_manager_goal_details",
        lambda *args, **kwargs: {"status": "read", "todos": []},
    )

    result = context.manager_turn_context(
        tmp_path / "registry.json", {"channel_id": "manager"}, tmp_path
    )

    window = result["evidence_window"]
    assert window["schema_version"] == context.MANAGER_EVIDENCE_WINDOW_SCHEMA
    assert window["days"] == context.MANAGER_EVIDENCE_WINDOW_DAYS == 7
    assert window["applies_to"] == "recent_delivery_history"
    assert window["read_status"] == "read"
    assert len(window["matched_by_day"]) == 6
    history = result["goals"][0]["recent_delivery_history"]
    deliveries = history["deliveries"]
    assert len(deliveries) == context.MANAGER_EVIDENCE_TOTAL_LIMIT
    assert len({row["goal_id"] for row in deliveries}) == 1
    assert deliveries[0]["receipt_detail"] == "full"
    assert deliveries[0]["recorded_details"]["source"] == "core_run_index"
    assert {row["receipt_detail"] for row in deliveries[1:]} == {"compact"}
    assert not [row for row in deliveries[1:] if "recorded_details" in row]
    assert history["coverage"]["included"] == context.MANAGER_EVIDENCE_TOTAL_LIMIT
    assert history["coverage"]["omitted"] > 0
    assert history["window_days"] == 7
    # Declared sources are data, not a read: local is available, others are not.
    sources = {source["source_id"]: source for source in window["sources"]}
    assert sources["local"]["status"] == "available"
    assert all(
        source["status"] == "available"
        for source in window["sources"]
        if source["source_id"] == "local"
    )
    assert window["declared_unread_sources"] == [
        source["source_id"]
        for source in window["sources"]
        if source["status"] != "available"
    ]


def test_source_health_rows_type_a_window_that_read_nothing():
    declared = [{"source_id": "local", "source_host": "local", "status": "available"}]

    unread = context._source_health_rows(declared, read_status="not_read")

    assert unread == [
        {
            "source_id": "local",
            "source_host": "local",
            "status": "available",
            "freshness": "unknown",
            "reason": context.MANAGER_LOCAL_SOURCE_NOT_READ_REASON,
            "coverage_effect": context.MANAGER_SOURCE_COVERAGE_EFFECT,
            "next_action": context.MANAGER_LOCAL_SOURCE_NOT_READ_NEXT_ACTION,
        }
    ]
    read = context._source_health_rows(declared, read_status="read")
    assert read[0]["freshness"] == "current"
    assert read[0]["reason"] is None
    assert read[0]["coverage_effect"] is None
    assert read[0]["next_action"] is None


def test_manager_prompt_uses_the_window_source_health_rows_as_guidance():
    """The window publishes source_health rows, so the prompt has to name them.

    The rows ship as data in every Turn; without a matching sentence the answer
    only reads remote_evidence, and a source that was declared but contributed
    nothing stays an unexplained gap. The wording also keeps the coverage effect
    explicitly guidance rather than a machine-checked obligation.
    """

    assert "evidence_window.source_health carries one typed row per declared source" in (
        MANAGER_AGENT_OBJECTIVE
    )
    assert "including the local source" in MANAGER_AGENT_OBJECTIVE
    assert "next_action as the repair" in MANAGER_AGENT_OBJECTIVE
    assert "not a machine-checked obligation" in MANAGER_AGENT_OBJECTIVE


def test_remote_source_rows_are_typed_and_never_read_as_no_progress():
    declared = [
        {"source_id": "local", "source_host": "local", "status": "available"},
        {
            "source_id": "ssh:ark-devbox",
            "source_host": "ark-devbox",
            "status": "not_read",
            "reason": None,
            "scope": "remote_registry",
        },
        {
            "source_id": "ssh:gone",
            "source_host": "gone",
            "status": "not_configured",
            "reason": "ssh_alias_not_configured",
        },
    ]

    health = {
        row["source_id"]: row
        for row in context._source_health_rows(declared, read_status="read")
    }

    assert health["local"]["freshness"] == "current"
    assert health["ssh:ark-devbox"]["status"] == "not_read"
    assert health["ssh:ark-devbox"]["freshness"] == "stale"
    assert health["ssh:ark-devbox"]["reason"] == context.MANAGER_SOURCE_NOT_READ_REASON
    assert health["ssh:gone"]["freshness"] == "unknown"
    assert health["ssh:gone"]["reason"] == "ssh_alias_not_configured"
    assert health["ssh:gone"]["next_action"] == context.MANAGER_SOURCE_UNCONFIGURED_NEXT_ACTION
    for source_id, row in health.items():
        assert row["freshness"] in context.MANAGER_SOURCE_FRESHNESS_VALUES
        if row["freshness"] == "current":
            continue
        # The coverage effect is what stops a reader from reading a source that
        # contributed nothing as "this Goal made no progress".
        assert row["coverage_effect"] == context.MANAGER_SOURCE_COVERAGE_EFFECT
        assert "must not present it as no progress" in row["coverage_effect"]
        assert row["next_action"], source_id


def test_turn_context_reports_health_for_every_declared_source(monkeypatch, tmp_path):
    _write_delivery_index(
        tmp_path,
        "alpha",
        [
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "goal_id": "alpha",
                "agent_id": "worker",
                "todo_id": "todo_1",
                "classification": "validated_progress",
                "delivery_outcome": "outcome_progress",
                "recommended_action": "follow up",
            }
        ],
    )
    monkeypatch.setattr(
        context,
        "build_goal_portfolio",
        lambda **_: {
            "goals": [{"goal_id": "alpha", "activation_state": "active"}],
            "coverage": {"discovered": 1},
        },
    )
    monkeypatch.setattr(
        context,
        "read_manager_goal_details",
        lambda *args, **kwargs: {"status": "read", "todos": []},
    )
    monkeypatch.setattr(
        context,
        "_declared_sources",
        lambda *args, **kwargs: [
            {"source_id": "local", "source_host": "local", "status": "available"},
            {
                "source_id": "ssh:ark-devbox",
                "source_host": "ark-devbox",
                "status": "not_read",
                "reason": None,
            },
        ],
    )

    result = context.manager_turn_context(
        tmp_path / "registry.json", {"channel_id": "manager"}, tmp_path
    )

    window = result["evidence_window"]
    assert window["read_status"] == "read"
    assert [row["source_id"] for row in window["source_health"]] == [
        source["source_id"] for source in window["sources"]
    ]
    health = {row["source_id"]: row for row in window["source_health"]}
    assert health["local"]["freshness"] == "current"
    assert health["ssh:ark-devbox"]["freshness"] == "stale"
    assert health["ssh:ark-devbox"]["coverage_effect"] == (
        context.MANAGER_SOURCE_COVERAGE_EFFECT
    )


def test_evidence_window_is_a_selected_bounded_decision():
    # The window is an explicit operator choice, bounded, and declared with its
    # source: it never follows a discovered environment fact silently.
    assert context.resolve_evidence_window_days({}) == (
        context.MANAGER_EVIDENCE_WINDOW_DAYS,
        context.MANAGER_EVIDENCE_WINDOW_SOURCE_PRODUCT_DEFAULT,
        "",
    )
    assert context.resolve_evidence_window_days(
        {context.MANAGER_EVIDENCE_WINDOW_ENV_VAR: "14"}
    ) == (14, context.MANAGER_EVIDENCE_WINDOW_SOURCE_EXPLICIT_CONFIG, "")
    for rejected in ("0", "31", "-3", "seven", "7.5"):
        days, source, reason = context.resolve_evidence_window_days(
            {context.MANAGER_EVIDENCE_WINDOW_ENV_VAR: rejected}
        )
        # A rejected value keeps the shipped default and names the reason, so a
        # bad setting can neither widen the prompt nor answer a narrower window.
        assert days == context.MANAGER_EVIDENCE_WINDOW_DAYS
        assert source == context.MANAGER_EVIDENCE_WINDOW_SOURCE_PRODUCT_DEFAULT
        assert reason == context.MANAGER_EVIDENCE_WINDOW_REASON_INVALID_EXPLICIT
    with pytest.raises(ValueError):
        context.manager_turn_context(
            Path("/nonexistent/registry.json"),
            {"channel_id": "manager"},
            Path("/nonexistent/runtime"),
            evidence_window_days=31,
        )


def test_window_declares_its_source_and_bounds(monkeypatch, tmp_path):
    monkeypatch.setattr(
        context,
        "build_goal_portfolio",
        lambda **_: {"goals": [], "coverage": {"discovered": 0}},
    )
    monkeypatch.setenv(context.MANAGER_EVIDENCE_WINDOW_ENV_VAR, "21")
    result = context.manager_turn_context(
        tmp_path / "registry.json", {"channel_id": "manager"}, tmp_path
    )
    window = result["evidence_window"]
    assert window["days"] == 21
    assert window["days_source"] == context.MANAGER_EVIDENCE_WINDOW_SOURCE_EXPLICIT_CONFIG
    assert window["days_reason"] == ""
    assert window["days_default"] == context.MANAGER_EVIDENCE_WINDOW_DAYS
    assert window["days_env_var"] == context.MANAGER_EVIDENCE_WINDOW_ENV_VAR
    assert window["days_bounds"] == {"min": 1, "max": context.MANAGER_EVIDENCE_MAX_WINDOW_DAYS}
    # An interactive Turn declares that the sources were not read for it.
    assert window["remote_read"] == context.MANAGER_REMOTE_READ_ON_DEMAND
    assert "remote_evidence" not in result


def test_prompt_only_turn_reads_declared_sources_once_and_declares_freshness(
    monkeypatch, tmp_path
):
    from loopx.capabilities.manager_context.ssh_evidence import configure

    config = tmp_path / "ssh_config"
    config.write_text("Host research-host\n  HostName research-host.invalid\n")
    configure(
        tmp_path,
        channel="manager.external." + "e" * 24,
        host="research-host",
        goal_ids=["remote-goal"],
        execute=True,
        config_path=config,
    )
    monkeypatch.setattr(
        context,
        "build_goal_portfolio",
        lambda **_: {"goals": [], "coverage": {"discovered": 0}},
    )
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "schema_version": "manager_evidence_page_v1",
                    "ok": True,
                    "rows": [{"goal_id": "remote-goal", "quality": "stale"}],
                    "source": {"coverage": {"discovered": 1}},
                }
            ),
        )

    result = context.manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager"},
        tmp_path,
        remote_evidence=True,
        remote_runner=run,
        remote_config_path=config,
    )
    window = result["evidence_window"]
    assert window["remote_read"] == context.MANAGER_REMOTE_READ_INLINE
    # One packet, one SSH config: the declaration reads the same config the
    # source read used, so it cannot call a host unconfigured and read it.
    declared = {source["source_id"]: source for source in window["sources"]}
    assert declared["ssh:research-host"]["status"] == "not_read"
    remote = result["remote_evidence"]
    assert remote["schema_version"] == "manager_remote_evidence_v0"
    assert remote["window_days"] == context.MANAGER_EVIDENCE_WINDOW_DAYS
    assert remote["read_status"] == "read"
    assert remote["sources"][0]["source_id"] == "ssh:research-host"
    assert remote["sources"][0]["fresh"] is True
    assert remote["declared_source_count"] == 1
    assert remote["rows"][0]["goal_id"] == "remote-goal"
    assert remote["rows"][0]["source_freshness"] == "current"
    assert remote["rows"][0]["source_host"] == "research-host"
    assert remote["budget"]["total_seconds"] == 10
    assert len(calls) == 1
    # The next Turn inside the TTL reuses the read instead of dialling again.
    again = context.manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager"},
        tmp_path,
        remote_evidence=True,
        remote_runner=run,
        remote_config_path=config,
    )
    assert len(calls) == 1
    assert again["remote_evidence"]["sources"][0]["status"] == "cached"
    assert again["remote_evidence"]["rows"] == remote["rows"]


def test_a_failed_source_read_reaches_the_turn_context_with_its_cause(
    monkeypatch, tmp_path
):
    """The Turn context itself must carry the cause and the repair to name."""

    from loopx.capabilities.manager_context.ssh_evidence import configure

    config = tmp_path / "ssh_config"
    config.write_text("Host research-host\n  HostName research-host.invalid\n")
    configure(
        tmp_path,
        channel="manager.external." + "f" * 24,
        host="research-host",
        goal_ids=["remote-goal"],
        execute=True,
        config_path=config,
    )
    monkeypatch.setattr(
        context,
        "build_goal_portfolio",
        lambda **_: {"goals": [], "coverage": {"discovered": 0}},
    )

    def run(argv, **kwargs):
        return SimpleNamespace(
            returncode=255,
            stdout="",
            stderr="huangruiteng@203.0.113.7: Permission denied (gssapi-with-mic).",
        )

    result = context.manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager"},
        tmp_path,
        remote_evidence=True,
        remote_runner=run,
        remote_config_path=config,
    )
    remote = result["remote_evidence"]
    assert remote["read_status"] == "unavailable"
    assert remote["rows"] == []
    assert remote["limitations"] == ["remote_source_ssh_auth_required"]
    source = remote["sources"][0]
    assert source["reason_code"] == "ssh_auth_required"
    # An expired Kerberos ticket is the owner's own repair, so the Turn context
    # has to name it instead of reporting an untyped unavailable source.
    assert "kinit" in source["reason"]
    assert "Tell the owner" in source["next_action"]


def test_prompt_only_transport_receives_the_source_read_instead_of_a_tool(
    monkeypatch, tmp_path
):
    """A segment without a read tool is handed the sources by the Turn owner."""

    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    collected = []

    def collect(*args, **kwargs):
        collected.append(kwargs)
        return {
            "schema_version": "manager_turn_context_v1",
            "coverage": {"discovered": 0},
            "goals": [],
        }

    monkeypatch.setattr(context, "collect_manager_turn_context", collect)
    monkeypatch.setattr(runtime, "_start_adapter", lambda **kwargs: Adapter())
    session = store.create_session(
        goal_id=MANAGER_AGENT_GOAL_ID,
        agent_id="dsh",
        adapter_kind="dsh_segment",
        upstream_thread_id="dsh-segment-fixture",
        channel_id="manager",
        upstream_mode="chat",
    )
    runtime.adapters[session["session_id"]] = Adapter()
    turn, _ = runtime.submit_turn(
        session_id=session["session_id"],
        client_turn_id="prompt-only",
        message="What did my hosts do?",
        work_dir=tmp_path,
        objective="manager",
    )
    done = runtime.wait_for_turn(
        session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=10
    )
    assert done["status"] == "completed", done
    # No include_details override: a prompt-only segment receives the details
    # and the declared source read inline.
    assert collected == [{"remote_evidence": True}]


def test_unread_window_keeps_its_bounds_and_unchanged_seam_default(
    monkeypatch, tmp_path
):
    from loopx.chat_manager_history import read_manager_delivery_history

    now = datetime.now(timezone.utc)
    _write_delivery_index(
        tmp_path,
        "alpha",
        [
            {
                "generated_at": (now - timedelta(days=days_ago)).isoformat(),
                "goal_id": "alpha",
                "delivery_outcome": "outcome_progress",
                "todo_id": f"todo_{days_ago}",
            }
            for days_ago in range(3)
        ],
    )
    # The reader keeps its explicit one-day default; the turn context chooses
    # the wider bounded window.
    default_ids = [
        row["todo_id"]
        for row in read_manager_delivery_history(tmp_path, "alpha")["deliveries"]
    ]
    assert "todo_0" in default_ids
    assert "todo_2" not in default_ids
    assert [
        row["todo_id"]
        for row in read_manager_delivery_history(
            tmp_path, "alpha", total_limit=1
        )["deliveries"]
    ] == ["todo_0"]
    with pytest.raises(ValueError):
        read_manager_delivery_history(tmp_path, "alpha", total_limit=501)
    monkeypatch.setattr(
        context,
        "build_goal_portfolio",
        lambda **_: {
            "goals": [{"goal_id": "alpha", "activation_state": "active"}],
            "coverage": {"discovered": 1},
        },
    )
    monkeypatch.setattr(
        context,
        "read_manager_goal_details",
        lambda *args, **kwargs: {"status": "read", "todos": []},
    )
    windowed = context.manager_turn_context(
        tmp_path / "registry.json", {"channel_id": "manager"}, tmp_path
    )
    assert "todo_2" in [
        row["todo_id"]
        for row in windowed["goals"][0]["recent_delivery_history"]["deliveries"]
    ]
    result = context.manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager"},
        tmp_path,
        include_details=False,
    )
    assert result["evidence_window"]["read_status"] == "not_read"
    assert result["evidence_window"]["days"] == 7
    with pytest.raises(ValueError):
        context.manager_turn_context(
            tmp_path / "registry.json",
            {"channel_id": "manager"},
            tmp_path,
            evidence_window_days=0,
        )


class Adapter:
    upstream_thread_id = "fresh-upstream"

    def __init__(self):
        self.messages = []
        self.closed = False

    def healthcheck(self):
        return True

    def start_turn(self, message, sink):
        self.messages.append(message)
        return {"answer": "fixture response"}

    def close_session(self):
        self.closed = True


def test_trusted_manager_profile_drives_host_and_session_readback(
    monkeypatch, tmp_path
):
    import loopx.chat_runtime as runtime_module

    runtime_root = tmp_path / "runtime"
    _apply_manager_runtime_profile(runtime_root, "trusted_owner")
    store = ChatSessionStore(runtime_root)
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    monkeypatch.setattr(
        runtime,
        "capabilities",
        lambda: [
            {
                "agent_id": "codex",
                "available": True,
                "adapter_kind": "codex_app_server",
            }
        ],
    )
    starts = []
    adapter = Adapter()

    def start(**kwargs):
        starts.append(kwargs)
        return adapter

    monkeypatch.setattr(runtime_module.CodexAppServerAdapter, "start", start)
    session, resumed = runtime.open_session(
        goal_id="loopx-manager",
        agent_id="codex",
        work_dir=tmp_path,
        objective="manager",
        mode="new",
        channel_id="manager",
    )

    assert resumed is False
    assert starts[0]["runtime_profile"] == "trusted_owner"
    assert starts[0]["sandbox"] == "danger-full-access"
    assert "normal tools and skills" in starts[0]["objective"]
    assert "Do not inspect arbitrary repositories" not in starts[0]["objective"]
    readback = store.public_session(session)["manager_runtime"]
    assert readback["runtime_profile"] == "trusted_owner"
    assert readback["sandbox"] == "danger-full-access"
    assert readback["standing_grant"] == "machine_configuration"
    assert "shell" in readback["tool_classes"]
    assert "normal tools and skills" in manager_workspace(
        store.root,
        runtime_profile="trusted_owner",
    ).joinpath("AGENTS.md").read_text(encoding="utf-8")


def test_external_manager_channel_remains_restricted_without_scoped_host_grant(
    monkeypatch, tmp_path
):
    import loopx.chat_runtime as runtime_module

    runtime_root = tmp_path / "runtime"
    _apply_manager_runtime_profile(runtime_root, "trusted_owner")
    store = ChatSessionStore(runtime_root)
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    monkeypatch.setattr(
        runtime,
        "capabilities",
        lambda: [
            {
                "agent_id": "codex",
                "available": True,
                "adapter_kind": "codex_app_server",
            }
        ],
    )
    starts = []

    def start(**kwargs):
        starts.append(kwargs)
        return Adapter()

    monkeypatch.setattr(runtime_module.CodexAppServerAdapter, "start", start)
    session, _ = runtime.open_session(
        goal_id="loopx-manager",
        agent_id="codex",
        work_dir=tmp_path,
        objective="manager",
        mode="new",
        channel_id="manager.external.fixture",
    )

    assert starts[0]["runtime_profile"] == "restricted"
    assert starts[0]["sandbox"] == "read-only"
    assert "Do not inspect arbitrary repositories" in starts[0]["objective"]
    readback = store.public_session(session)["manager_runtime"]
    assert readback["runtime_profile"] == "restricted"
    assert readback["status"] == "external_audience_restricted"
    assert readback["standing_grant"] == "none"


def test_trusted_manager_profile_rejects_endpoint_that_cannot_enforce_it(
    tmp_path,
):
    runtime = ChatRuntimeController(
        store=ChatSessionStore(tmp_path / "runtime"),
        codex_bin="codex",
    )
    profile = {
        **runtime.manager_runtime_profile(),
        "runtime_profile": "trusted_owner",
        "sandbox": "danger-full-access",
    }

    with pytest.raises(
        CodexChatAgentError,
        match="requires the Codex endpoint",
    ) as caught:
        runtime._start_adapter(
            agent_id="claude-code",
            work_dir=tmp_path,
            goal_id=MANAGER_AGENT_GOAL_ID,
            objective="manager",
            manager_runtime=profile,
        )

    assert caught.value.error_code == "manager_runtime_endpoint_unsupported"
    assert "Select the Codex Agent" in caught.value.gate["next_action"]


def test_manager_profile_change_rotates_healthy_upstream_without_losing_session(
    monkeypatch, tmp_path
):
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    monkeypatch.setattr(
        runtime,
        "capabilities",
        lambda: [
            {
                "agent_id": "codex",
                "available": True,
                "adapter_kind": "codex_app_server",
            }
        ],
    )
    starts = []

    def start(**kwargs):
        adapter = Adapter()
        adapter.upstream_thread_id = f"upstream-{len(starts)}"
        starts.append((kwargs, adapter))
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start)
    session, _ = runtime.open_session(
        goal_id="loopx-manager",
        agent_id="codex",
        work_dir=tmp_path,
        objective="manager",
        mode="new",
        channel_id="manager",
    )
    assert starts[0][0]["manager_runtime"]["runtime_profile"] == "restricted"

    _apply_manager_runtime_profile(tmp_path / "runtime", "trusted_owner")
    restored = runtime._ensure_adapter(
        session,
        work_dir=tmp_path,
        objective="manager",
    )

    assert starts[0][1].closed is True
    assert restored is starts[1][1]
    assert starts[1][0]["resume_thread_id"] is None
    assert starts[1][0]["manager_runtime"]["runtime_profile"] == "trusted_owner"
    updated = store.load_session(session["session_id"])
    assert updated["manager_runtime_profile"] == "trusted_owner"
    assert updated["upstream_thread_id"] == "upstream-1"


def test_manager_restart_uses_the_session_allocation_instead_of_new_defaults(
    monkeypatch, tmp_path
):
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    starts: list[dict[str, object]] = []

    def runtime() -> ChatRuntimeController:
        controller = ChatRuntimeController(store=store, codex_bin="codex")
        monkeypatch.setattr(
            controller,
            "capabilities",
            lambda: [
                {
                    "agent_id": "codex",
                    "available": True,
                    "adapter_kind": "codex_app_server",
                }
            ],
        )

        def start(**kwargs):
            starts.append(kwargs)
            return Adapter()

        monkeypatch.setattr(controller, "_start_adapter", start)
        return controller

    allocation = {
        "schema_version": "manager_executor_allocation_v0",
        "selection_policy": "preferred",
        "allocation_reason": "configured_preference",
        "executor_endpoint": "codex",
        "executor_endpoint_source": "machine_configuration",
        "executor_endpoint_default_reason": "",
        "configured_endpoint": "codex",
        "eligible_endpoints": [],
        "configuration_revision": "sha256:before",
        "available": True,
        "model": "gpt-session-model",
        "model_source": "machine_configuration",
        "reasoning_effort": "max",
    }
    first = runtime()
    session, _ = first.open_session(
        goal_id=MANAGER_AGENT_GOAL_ID,
        agent_id="codex",
        work_dir=tmp_path,
        objective="manager",
        mode="new",
        channel_id="manager",
        manager_executor_allocation=allocation,
    )
    assert starts[-1]["executor_model"] == {
        "model": "gpt-session-model",
        "reasoning_effort": "max",
    }

    restarted = runtime()
    monkeypatch.setattr(
        restarted,
        "steward_executor_defaults",
        lambda: {
            "executor_endpoint": "codex",
            "executor_model": "gpt-new-default",
            "executor_reasoning_effort": "low",
        },
    )
    restarted._ensure_adapter(session, work_dir=tmp_path, objective="manager")

    assert starts[-1]["executor_model"] == {
        "model": "gpt-session-model",
        "reasoning_effort": "max",
    }


def test_legacy_manager_migrates_without_project_and_refreshes_each_turn(
    monkeypatch, tmp_path
):
    store = ChatSessionStore(tmp_path / "runtime" / "chat")
    runtime = ChatRuntimeController(
        store=store, codex_bin="codex", registry_path=tmp_path / "registry.json"
    )
    monkeypatch.setattr(
        runtime,
        "capabilities",
        lambda: [
            {"agent_id": "codex", "available": True, "adapter_kind": "codex_app_server"}
        ],
    )
    adapter = Adapter()
    starts = []

    def start(**kwargs):
        starts.append(kwargs)
        return adapter

    monkeypatch.setattr(runtime, "_start_adapter", start)
    legacy = store.create_session(
        goal_id="old-project",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="old-project-thread",
        channel_id="manager",
        upstream_mode="chat",
        codex_home=str(runtime.codex_home),
    )
    session, resumed = runtime.open_session(
        goal_id="different-project",
        agent_id="codex",
        work_dir=tmp_path / "project",
        objective="old project instructions",
        mode="resume_latest",
        channel_id="manager",
    )
    assert resumed and session["session_id"] == legacy["session_id"]
    assert session["goal_id"] == "loopx-manager"
    assert session["manager_context_version"] == MANAGER_CONTEXT_VERSION
    assert starts[0]["resume_thread_id"] is None
    assert starts[0]["work_dir"] == manager_workspace(store.root)
    assert "global LoopX manager" in starts[0]["objective"]
    snapshots = []

    def fresh(*args, **kwargs):
        snapshot = {
            "snapshot_id": f"evidence-{len(snapshots)}",
            "coverage": {"discovered": len(snapshots) + 1},
        }
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(context, "manager_turn_context", fresh)
    try:
        for index in range(2):
            turn, created = runtime.submit_turn(
                session_id=session["session_id"],
                client_turn_id=f"request-{index}",
                message="Which Goals?",
                work_dir=tmp_path / "project",
                objective="wrong project",
            )
            assert created
            done = runtime.wait_for_turn(
                session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=5
            )
            assert done["status"] == "completed", done
            events = store.events_after(session["session_id"], turn["turn_id"], None)
            assert any(
                e["kind"] == "manager.context"
                and e["payload"]["snapshot_id"] == f"evidence-{index}"
                for e in events
            )
        assert "evidence-0" in adapter.messages[0]
        assert "evidence-1" in adapter.messages[1]
        assert len(starts) == 1
        # Stored requests remain original; the derived evidence is a separate receipt.
        assert all(
            m.get("text") == "Which Goals?"
            for m in store.messages(session["session_id"])
            if m["role"] == "user"
        )
    finally:
        runtime.close()


def test_external_session_anchor_never_supplies_read_authority(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        context,
        "build_goal_portfolio",
        lambda **kwargs: calls.append(kwargs) or {"goals": []},
    )
    result = context.collect_manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager.external.fixture", "goal_id": "old-private-anchor"},
        tmp_path,
    )
    assert calls == []
    assert result["coverage"]["discovered"] is None
    assert result["warnings"] == ["external_authorization_unavailable"]


def test_external_authority_is_rechecked_after_collection(monkeypatch, tmp_path):
    def collect(**kwargs):
        assert kwargs["goal_ids"] == ["currently-authorized"]
        return {"goals": [{"goal_id": "currently-authorized", "quality": "verified"}]}

    monkeypatch.setattr(context, "build_goal_portfolio", collect)
    grants = iter([["currently-authorized"], []])
    result = context.collect_manager_turn_context(
        tmp_path / "registry.json",
        {"channel_id": "manager.external.fixture", "goal_id": "old-private-anchor"},
        tmp_path,
        lambda session: next(grants),
    )
    assert result["goals"] == []
    assert result["warnings"] == ["external_authorization_changed"]


def test_empty_external_authority_never_reaches_the_model(monkeypatch, tmp_path):
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(
        store=store,
        codex_bin="codex",
        registry_path=tmp_path / "registry.json",
        manager_scope_resolver=lambda _session: [],
    )
    adapter = Adapter()
    session = store.create_session(
        goal_id="previously-authorized",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="old-upstream",
        channel_id="manager.external.fixture",
        upstream_mode="chat",
        codex_home=str(runtime.codex_home),
    )
    runtime.adapters[session["session_id"]] = adapter
    try:
        turn, _ = runtime.submit_turn(
            session_id=session["session_id"],
            client_turn_id="revoked-scope",
            message="What changed?",
            work_dir=tmp_path,
            objective="manager",
        )
        done = runtime.wait_for_turn(
            session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=5
        )
        assert done["status"] == "failed"
        assert done["error_code"] == "manager_authorization_unavailable"
        assert adapter.messages == []
    finally:
        runtime.close()


def test_changed_external_scope_rotates_upstream_before_model_call(
    monkeypatch, tmp_path
):
    store = ChatSessionStore(tmp_path / "runtime")
    runtime = ChatRuntimeController(store=store, codex_bin="codex")
    old_adapter = Adapter()
    replacement = Adapter()
    replacement.upstream_thread_id = "replacement-upstream"
    starts = []
    monkeypatch.setattr(
        context,
        "collect_manager_turn_context",
        lambda *_args, **_kwargs: {
            "schema_version": "manager_turn_context_v1",
            "authorization_scope_id": context.manager_authorization_scope_id(
                ["newly-authorized"]
            ),
            "coverage": {"discovered": 1, "verified": 1, "complete": True},
            "goals": [{"goal_id": "newly-authorized"}],
        },
    )

    def start(**kwargs):
        starts.append(kwargs)
        return replacement

    monkeypatch.setattr(runtime, "_start_adapter", start)
    session = store.create_session(
        goal_id="previously-authorized",
        agent_id="codex",
        adapter_kind="codex_app_server",
        upstream_thread_id="old-upstream",
        channel_id="manager.external.fixture",
        upstream_mode="chat",
        codex_home=str(runtime.codex_home),
    )
    store.update_session(
        session["session_id"],
        manager_authorization_scope_id=context.manager_authorization_scope_id(
            ["previously-authorized"]
        ),
    )
    runtime.adapters[session["session_id"]] = old_adapter
    try:
        turn, _ = runtime.submit_turn(
            session_id=session["session_id"],
            client_turn_id="changed-scope",
            message="What changed?",
            work_dir=tmp_path,
            objective="manager",
        )
        done = runtime.wait_for_turn(
            session_id=session["session_id"], turn_id=turn["turn_id"], timeout_sec=5
        )
        assert done["status"] == "completed"
        assert old_adapter.closed
        assert len(starts) == 1
        assert starts[0]["resume_thread_id"] is None
        assert starts[0]["history"] is None
        assert "newly-authorized" in replacement.messages[0]
        persisted = store.load_session(session["session_id"])
        assert persisted["upstream_thread_id"] == "replacement-upstream"
        assert persisted["manager_authorization_scope_id"] == (
            context.manager_authorization_scope_id(["newly-authorized"])
        )
        retry, _ = runtime.submit_turn(
            session_id=session["session_id"],
            client_turn_id="same-scope-retry",
            message="And now?",
            work_dir=tmp_path,
            objective="manager",
        )
        retried = runtime.wait_for_turn(
            session_id=session["session_id"], turn_id=retry["turn_id"], timeout_sec=5
        )
        assert retried["status"] == "completed"
        assert len(starts) == 1
        assert len(replacement.messages) == 2
    finally:
        runtime.close()


def test_current_external_scope_is_fresh_and_excludes_other_labels(
    monkeypatch, tmp_path
):
    import json
    import loopx.goal_portfolio as portfolio

    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {"id": "old-private-anchor", "display_name": "PRIVATE-OLD-LABEL"},
                    {"id": "currently-authorized", "display_name": "Allowed label"},
                ]
            }
        )
    )
    reads = []

    def read(goal, **kwargs):
        reads.append(goal["id"])
        return {"goal_id": goal["id"], "quality": "verified", "warnings": []}

    monkeypatch.setattr(portfolio, "_read_goal", read)
    grant = ["currently-authorized"]
    session = {
        "channel_id": "manager.external.fixture",
        "goal_id": "old-private-anchor",
    }
    result = context.collect_manager_turn_context(
        registry, session, tmp_path, lambda _: grant
    )
    assert reads == ["currently-authorized"]
    assert [g["goal_id"] for g in result["goals"]] == grant
    assert "PRIVATE-OLD-LABEL" not in json.dumps(result)
    grant.clear()
    revoked = context.collect_manager_turn_context(
        registry, session, tmp_path, lambda _: grant
    )
    assert revoked["goals"] == []
    assert reads == ["currently-authorized"]
