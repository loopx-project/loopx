from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

import loopx.control_plane.projection_envelope_facts as projection_envelope
import loopx.control_plane.quota.should_run as quota_should_run_module
import loopx.control_plane.quota.should_run_packet as should_run_packet
import loopx.global_risks as global_risks
import loopx.global_todos as global_todos
import loopx.summary_all as summary_all
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.control_plane.runtime.status_projection_cache import (
    load_status_projection_cache,
    status_projection_cache_path,
    status_projection_cache_key,
    write_status_projection_cache,
)
from loopx.control_plane.runtime.time import now_utc, utc_isoformat
from loopx.control_plane.scheduler.state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_SURFACE,
    LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
    scheduler_state_path,
)
from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
from loopx.presentation.renderers.status_markdown import render_status_markdown
from loopx.quota import build_quota_should_run
from loopx.status import collect_status


def _registry(tmp_path: Path, goal_ids: tuple[str, ...] = ("goal-a", "goal-b")) -> Path:
    registry_path = tmp_path / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    (tmp_path / "ACTIVE_GOAL_STATE.md").write_text("# Goal\n", encoding="utf-8")
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": goal_id,
                        "objective": "Project envelope fixture.",
                        "domain": "software-development",
                        "repo": str(tmp_path),
                        "state_file": str(tmp_path / "ACTIVE_GOAL_STATE.md"),
                        "adapter": {"kind": "read_only_project_map_v0"},
                    }
                    for goal_id in goal_ids
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry_path


def _status(registry_path: Path, **kwargs):
    return collect_status(
        registry_path=registry_path,
        runtime_root_override=None,
        scan_roots=[],
        limit=5,
        include_public_boundary_scan=False,
        **kwargs,
    )


def _sources(envelope: dict) -> dict[str, dict]:
    return {
        (f"{row['via']}/" if row.get("via") else "") + row["source_id"]: row
        for row in envelope["sources"]
    }


def test_status_envelope_discloses_reads_and_registry_coverage(tmp_path: Path) -> None:
    payload = _status(_registry(tmp_path))
    envelope = payload["projection_envelope"]

    assert envelope["schema_version"] == projection_envelope.PROJECTION_ENVELOPE_SCHEMA_VERSION
    assert envelope["projection"] == "status"
    assert envelope["served_at"] == envelope["observed_at"]
    assert envelope["coverage"]["scope"] == "registry"
    assert (envelope["coverage"]["expected_count"], envelope["coverage"]["included_count"]) == (2, 2)
    assert envelope["complete"] is True and envelope["fresh"] is True and envelope["alert"] is False
    sources = _sources(envelope)
    assert set(sources) == {
        "registry", "global_registry", "goal_run_indexes", "goal_state_contract", "runtime_projection_routes",
    }
    assert sources["registry"]["item_count"] == 2
    assert sources["global_registry"]["status"] == "missing"
    assert sources["global_registry"]["alert"] is False
    assert sources["goal_run_indexes"]["missing_count"] == 2
    for row in sources.values():
        assert row["last_read_at"] is None or row["last_read_at"] <= envelope["observed_at"]
    assert str(tmp_path) not in json.dumps(envelope), "the envelope must stay path-free"


def test_goal_status_for_an_unknown_goal_is_incomplete(tmp_path: Path) -> None:
    envelope = _status(_registry(tmp_path), goal_id="goal-missing")["projection_envelope"]

    assert envelope["coverage"]["scope"] == "goal"
    assert envelope["coverage"]["omitted"] == [
        {"reason": "goal_not_found", "count": 1, "refs": ["goal-missing"]}
    ]
    assert envelope["complete"] is False
    assert envelope["alert_reasons"] == ["incomplete_coverage"]


def test_goal_status_scopes_coverage_to_the_goal(tmp_path: Path) -> None:
    envelope = _status(_registry(tmp_path), goal_id="goal-a")["projection_envelope"]

    assert (envelope["coverage"]["scope"], envelope["coverage"]["expected_count"]) == ("goal", 1)
    assert envelope["complete"] is True


def test_quota_should_run_inherits_status_and_covers_the_selected_goal(
    tmp_path: Path,
) -> None:
    status = _status(_registry(tmp_path, ("goal-a",)), goal_id="goal-a")

    payload = build_quota_should_run(
        status,
        goal_id="goal-a",
        runtime_root=status["runtime_root"],
    )

    envelope = payload["projection_envelope"]
    sources = _sources(envelope)
    assert envelope["projection"] == "quota_should_run"
    assert envelope["coverage"]["scope"] == "goal"
    assert envelope["coverage"]["expected_count"] == 1
    assert envelope["coverage"]["included_count"] == 1
    assert envelope["coverage"]["omitted"] == []
    assert envelope["coverage"]["complete"] is True
    assert envelope["complete"] is True and envelope["alert"] is False
    assert envelope["upstream"][0]["projection"] == "status"
    assert sources["automation_cadence"]["status"] == "fresh"
    assert str(tmp_path) not in json.dumps(envelope)


def test_quota_should_run_alerts_when_the_status_envelope_is_missing(
    tmp_path: Path,
) -> None:
    status = _status(_registry(tmp_path, ("goal-a",)), goal_id="goal-a")
    status.pop("projection_envelope")

    payload = build_quota_should_run(
        status,
        goal_id="goal-a",
        runtime_root=status["runtime_root"],
    )

    envelope = payload["projection_envelope"]
    assert _sources(envelope)["status"]["status"] == "not_read"
    assert envelope["alert_reasons"] == ["missing_required_sources"]
    markdown = render_quota_should_run_markdown(payload)
    assert markdown.index("projection alerts") < markdown.index("decision:")


def test_quota_should_run_marks_an_unknown_goal_as_incomplete(tmp_path: Path) -> None:
    status = _status(_registry(tmp_path, ("goal-a",)))

    envelope = build_quota_should_run(
        status,
        goal_id="goal-missing",
        runtime_root=status["runtime_root"],
    )["projection_envelope"]

    assert envelope["coverage"]["scope"] == "goal"
    assert envelope["coverage"]["expected_count"] == 1
    assert envelope["coverage"]["included_count"] == 0
    assert envelope["coverage"]["omitted"] == [
        {"reason": "goal_not_found", "count": 1, "refs": ["goal-missing"]}
    ]
    assert envelope["complete"] is False
    assert envelope["alert_reasons"] == ["incomplete_coverage"]


def test_quota_should_run_propagates_unreadable_and_stale_status_sources(
    tmp_path: Path,
) -> None:
    observed_at = projection_envelope.now_utc_iso()
    status = quota_status_payload(
        goal_id="goal-a",
        status="active",
        recommended_action="Advance the goal.",
    )
    status["runtime_root"] = str(tmp_path / "runtime")
    status["projection_envelope"] = projection_envelope.seal_projection_envelope(
        projection="status",
        observed_at=observed_at,
        sources=[
            projection_envelope.source_fact(
                "registry",
                read_status="unreadable",
                last_read_at=observed_at,
            ),
            projection_envelope.source_fact(
                "goal_state_contract",
                last_read_at="2000-01-01T00:00:00Z",
            ),
        ],
        coverage={
            "scope": "goal",
            "expected_count": 1,
            "included_count": 1,
            "omitted": [],
        },
    )

    envelope = build_quota_should_run(
        status,
        goal_id="goal-a",
        runtime_root=status["runtime_root"],
    )["projection_envelope"]

    assert "unreadable_sources" in envelope["alert_reasons"]
    assert "stale_sources" in envelope["alert_reasons"]
    assert envelope["complete"] is True


def test_quota_should_run_seals_paused_and_health_blocked_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = quota_status_payload(
        goal_id="goal-a",
        status="active",
        quota_state="paused",
        recommended_action="Wait for the owner.",
    )
    status["runtime_root"] = str(tmp_path / "runtime")
    status["projection_envelope"] = _status(
        _registry(tmp_path, ("goal-a",)), goal_id="goal-a"
    )["projection_envelope"]

    paused = build_quota_should_run(
        status,
        goal_id="goal-a",
        runtime_root=status["runtime_root"],
    )
    assert paused["state"] == "paused"
    assert paused["projection_envelope"]["projection"] == "quota_should_run"

    monkeypatch.setattr(
        quota_should_run_module,
        "_build_quota_plan_for_goal",
        lambda *_args, **_kwargs: (
            {
                "ok": False,
                "groups": {},
                "health_items": [
                    {
                        "goal_id": "goal-a",
                        "recommended_action": "Repair the goal state.",
                        "status": "invalid",
                        "source": "contract",
                    }
                ],
                "summary": {},
            },
            False,
        ),
    )
    blocked = build_quota_should_run(
        status,
        goal_id="goal-a",
        runtime_root=status["runtime_root"],
    )
    assert blocked["state"] == "blocked_health"
    assert blocked["projection_envelope"]["projection"] == "quota_should_run"


def test_quota_should_run_records_live_workspace_inbox_and_scheduler_reads(
    tmp_path: Path,
) -> None:
    agent_id = "codex-a"
    status = quota_status_payload(
        goal_id="goal-a",
        status="active",
        recommended_action="Advance the goal.",
        agent_todo_items=[
            quota_todo_item(
                todo_id="todo_delivery",
                title="Advance the goal.",
                claimed_by=agent_id,
                required_capabilities=[],
            )
        ],
        coordination={"agent_model": "peer_v1", "registered_agents": [agent_id]},
        goal_extra={
            "repo": str(tmp_path),
            "control_plane": {
                "lark_event_inbox": {
                    "enabled": True,
                    "config_path": ".loopx/config/lark/inbox.json",
                }
            },
        },
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    registry_path = tmp_path / "registry.fixture.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": "goal-a",
                        "status": "active",
                        "repo": str(tmp_path),
                        "control_plane": {
                            "lark_event_inbox": {
                                "enabled": True,
                                "config_path": ".loopx/config/lark/inbox.json",
                            }
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    status["registry"] = str(registry_path)
    status["runtime_root"] = str(runtime_root)
    status["projection_envelope"] = _status(
        _registry(tmp_path, ("goal-a",)), goal_id="goal-a"
    )["projection_envelope"]

    payload = build_quota_should_run(
        status,
        goal_id="goal-a",
        agent_id=agent_id,
        runtime_root=runtime_root,
        scheduler_execution_context={
            "host_surface": "codex_app",
            "scheduler_owner": "host_automation",
            "execution_mode": "hosted_automation",
        },
        operator_inbox_urgency_projector=lambda **_kwargs: {
            "schema_version": "operator_inbox_urgency_v0",
            "enabled": True,
            "pending_count": 0,
            "attention_required_count": 0,
            "reply_due": False,
            "material_review_count": 0,
            "material_attachment_count": 0,
            "material_review_due": False,
            "material_review_drain_limit": 20,
            "local_private_content_returned": False,
        },
    )

    sources = _sources(payload["projection_envelope"])
    assert sources["operator_inbox"]["status"] == "fresh"
    assert sources["delivery_workspace"]["status"] == "fresh"
    assert sources["app_scheduler_state"]["status"] == "fresh"
    assert str(tmp_path) not in json.dumps(payload["projection_envelope"])


def test_quota_should_run_maps_cadence_read_failures_to_source_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = _status(_registry(tmp_path, ("goal-a",)), goal_id="goal-a")
    original_effect_runtime_result = should_run_packet.effect_runtime_result

    def fail_cadence_read(method: str, *args, **kwargs):
        if method == "quota.automation_cadence.manage":
            raise RuntimeError("cadence owner unavailable")
        return original_effect_runtime_result(method, *args, **kwargs)

    monkeypatch.setattr(
        should_run_packet,
        "effect_runtime_result",
        fail_cadence_read,
    )
    failed = build_quota_should_run(
        status,
        goal_id="goal-a",
        runtime_root=status["runtime_root"],
    )
    assert _sources(failed["projection_envelope"])["automation_cadence"][
        "status"
    ] == "unreadable"

    unavailable_status = quota_status_payload(
        goal_id="goal-a",
        status="active",
        recommended_action="Advance the goal.",
    )
    unavailable_status["projection_envelope"] = status["projection_envelope"]
    unavailable = build_quota_should_run(
        unavailable_status,
        goal_id="goal-a",
    )
    assert _sources(unavailable["projection_envelope"])["automation_cadence"][
        "status"
    ] == "not_read"


def test_quota_should_run_inherits_a_replayed_status_envelope(
    tmp_path: Path,
) -> None:
    registry_path = _registry(tmp_path, ("goal-a",))
    status = _status(registry_path)
    args = _cache_args(registry_path)
    write_status_projection_cache(payload=status, max_age_seconds=3600, **args)
    cached, metadata = load_status_projection_cache(
        max_age_seconds=3600,
        **args,
    )

    assert metadata["hit"] is True
    assert cached["projection_envelope"]["served_from_cache"] is True
    payload = build_quota_should_run(
        cached,
        goal_id="goal-a",
        runtime_root=cached["runtime_root"],
    )

    upstream = payload["projection_envelope"]["upstream"]
    assert upstream[0]["projection"] == "status"
    assert (
        upstream[0]["observed_at"]
        == cached["projection_envelope"]["observed_at"]
    )
    assert any(
        row.get("via") == "status"
        for row in payload["projection_envelope"]["sources"]
    )


def test_real_cli_quota_should_run_emits_the_projection_envelope(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    registry_path = _registry(tmp_path, ("goal-a",))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--registry",
            str(registry_path),
            "quota",
            "should-run",
            "--goal-id",
            "goal-a",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["projection_envelope"]["projection"] == "quota_should_run"
    assert payload["projection_envelope"]["coverage"]["included_count"] == 1
    assert payload["projection_envelope"]["upstream"][0]["projection"] == "status"


def test_real_cli_quota_should_run_discloses_unreadable_scheduler_state(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    registry_path = _registry(tmp_path, ("goal-a",))
    runtime_root = tmp_path / "runtime"
    agent_id = "reader-a"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"] = {
        "agent_model": "peer_v1",
        "registered_agents": [agent_id],
    }
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "quota",
        "should-run",
        "--runtime-profile",
        "codex_app_heartbeat",
        "--goal-id",
        "goal-a",
        "--agent-id",
        agent_id,
    ]

    def run_cli() -> dict:
        completed = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return json.loads(completed.stdout)

    missing = _sources(run_cli()["projection_envelope"])["app_scheduler_state"]
    assert missing["read_status"] == "read"
    assert missing["status"] == "fresh"

    paths = [
        scheduler_state_path(
            runtime_root,
            goal_id="goal-a",
            agent_id=agent_id,
            surface=CODEX_APP_SURFACE,
            state_key=state_key,
        )
        for state_key in (
            APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY,
            LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
        )
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{truncated", encoding="utf-8")

    corrupt_payload = run_cli()
    corrupt = _sources(corrupt_payload["projection_envelope"])[
        "app_scheduler_state"
    ]
    assert corrupt["read_status"] == "unreadable"
    assert corrupt["status"] == "unreadable"
    assert corrupt_payload["projection_envelope"]["alert"] is True

    for path in paths:
        path.unlink()
        path.mkdir()
    directory_payload = run_cli()
    directory = _sources(directory_payload["projection_envelope"])[
        "app_scheduler_state"
    ]
    assert directory["read_status"] == "unreadable"
    assert directory["status"] == "unreadable"
    assert directory_payload["projection_envelope"]["alert"] is True

    for path in paths:
        path.rmdir()
    recovered_payload = run_cli()
    recovered = _sources(recovered_payload["projection_envelope"])[
        "app_scheduler_state"
    ]
    assert recovered["read_status"] == "read"
    assert recovered["status"] == "fresh"
    assert recovered_payload["projection_envelope"]["alert"] is False


def _cache_args(registry_path: Path) -> dict:
    return {
        "registry_path": registry_path,
        "runtime_root": registry_path.parents[1] / "runtime",
        "scan_roots": [],
        "limit": 5,
        "include_task_graph": False,
        "goal_id": None,
    }


def test_cached_status_keeps_observed_at_and_restamps_staleness(tmp_path: Path, monkeypatch) -> None:
    registry_path = _registry(tmp_path)
    payload = _status(registry_path)
    write_status_projection_cache(payload=payload, max_age_seconds=3600, **_cache_args(registry_path))
    later = utc_isoformat(now_utc() + timedelta(seconds=900))
    monkeypatch.setattr(projection_envelope, "now_utc_iso", lambda: later)

    cached, metadata = load_status_projection_cache(max_age_seconds=3600, **_cache_args(registry_path))

    assert metadata["hit"] is True
    envelope = cached["projection_envelope"]
    assert envelope["observed_at"] == payload["projection_envelope"]["observed_at"]
    assert envelope["served_at"] == later
    assert envelope["served_from_cache"] is True
    assert envelope["age_seconds"] >= 900
    assert "stale_sources" in envelope["alert_reasons"]
    assert "(cached)" in render_status_markdown(cached)


def test_cache_entry_without_an_envelope_is_a_miss(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)
    args = _cache_args(registry_path)
    payload = _status(registry_path)
    payload.pop("projection_envelope")
    write_status_projection_cache(payload=payload, max_age_seconds=3600, **args)

    cached, metadata = load_status_projection_cache(max_age_seconds=3600, **args)

    assert cached is None
    assert metadata["miss_reason"] == "missing_projection_envelope"


def test_cache_entry_with_a_tampered_envelope_is_a_miss(tmp_path: Path) -> None:
    registry_path = _registry(tmp_path)
    args = _cache_args(registry_path)
    write_status_projection_cache(payload=_status(registry_path), max_age_seconds=3600, **args)
    path = status_projection_cache_path(
        args["runtime_root"],
        status_projection_cache_key(
            **{key: value for key, value in args.items()},
        ),
    )
    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["projection_envelope"]["observed_at"] = "yesterday"
    path.write_text(json.dumps(record), encoding="utf-8")

    cached, metadata = load_status_projection_cache(max_age_seconds=3600, **args)

    assert cached is None
    assert metadata["miss_reason"] == "invalid_projection_envelope"


def _global_status_payload(tmp_path: Path, *, excluded: int) -> dict:
    registry = _registry(tmp_path, ("goal-a",))
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    global_members = json.loads(registry.read_text())
    global_members["goals"].extend(
        {**global_members["goals"][0], "id": f"other-{index}"} for index in range(excluded)
    )
    (runtime / "registry.global.json").write_text(json.dumps(global_members))
    payload = _status(registry)
    payload["ok"] = True
    payload["global_registry"] = {
        "available": True,
        "ok": True,
        "current_registry_is_global": excluded == 0,
        "global_goal_count": 1 + excluded,
        "current_goal_count": 1,
        "current_registry_excluded_goal_count": excluded,
        "current_registry_excluded_goal_ids": [f"other-{index}" for index in range(min(excluded, 8))],
        "findings": [],
    }
    payload["attention_queue"] = {"items": [{"goal_id": "goal-a", "waiting_on": "codex"}], "item_count": 1}
    return payload


def _patch_status(monkeypatch, status_payload: dict) -> None:
    monkeypatch.setattr(summary_all, "collect_status", lambda **_: status_payload)
    monkeypatch.setattr(
        summary_all,
        "build_quota_should_run",
        lambda _status, *, goal_id, agent_id: {"ok": True, "goal_id": goal_id, "state": "eligible"},
    )


def test_global_summary_from_a_project_registry_is_marked_incomplete(tmp_path: Path, monkeypatch) -> None:
    _patch_status(monkeypatch, _global_status_payload(tmp_path, excluded=3))

    payload = summary_all.build_summary_all(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=5,
    )

    envelope = payload["projection_envelope"]
    assert envelope["projection"] == "global_summary"
    assert envelope["coverage"]["scope"] == "global"
    assert (envelope["coverage"]["expected_count"], envelope["coverage"]["included_count"]) == (4, 1)
    assert envelope["coverage"]["omitted"][0]["reason"] == "outside_current_registry"
    assert envelope["complete"] is False
    assert "incomplete_coverage" in envelope["alert_reasons"]
    assert envelope["upstream"][0]["projection"] == "status"
    assert "status/registry" in _sources(envelope)
    assert _sources(envelope)["goal_quota"]["item_count"] == 1
    markdown = summary_all.render_summary_all_markdown(payload)
    assert "🔴 projection alerts: `incomplete_coverage`" in markdown
    assert str(tmp_path) not in json.dumps(envelope)


def test_global_summary_from_the_global_registry_is_complete(tmp_path: Path, monkeypatch) -> None:
    _patch_status(monkeypatch, _global_status_payload(tmp_path, excluded=0))

    payload = summary_all.build_summary_all(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=5,
    )

    assert payload["projection_envelope"]["complete"] is True
    assert payload["projection_envelope"]["alert"] is False


def test_global_gates_counts_unavailable_quota_and_missing_status_envelope(tmp_path: Path, monkeypatch) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=0)
    status_payload.pop("projection_envelope")
    monkeypatch.setattr(summary_all, "collect_status", lambda **_: status_payload)
    monkeypatch.setattr(
        summary_all,
        "build_quota_should_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("quota ledger unreadable")),
    )

    payload = summary_all.build_global_gates(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        limit=5,
    )

    envelope = payload["projection_envelope"]
    sources = _sources(envelope)
    assert sources["goal_quota"]["unreadable_count"] == 1
    assert sources["status"]["status"] == "not_read"
    assert envelope["alert_reasons"] == ["missing_required_sources", "unreadable_sources"]
    assert "🔴" in summary_all.render_global_gates_markdown(payload)


def test_global_todos_discloses_global_membership_and_display_truncation(
    tmp_path: Path, monkeypatch
) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=3)
    monkeypatch.setattr(global_todos, "collect_status", lambda **_: status_payload)
    monkeypatch.setattr(
        global_todos,
        "build_quota_should_run",
        lambda *_args, **_kwargs: {
            "ok": True,
            "normal_delivery_allowed": True,
            "selected_todo": {
                "todo_id": "todo-a",
                "status": "open",
                "priority": "P1",
            },
            "agent_todo_summary": {
                "deferred_resume_candidates": [
                    {"todo_id": "todo-b", "status": "deferred", "priority": "P2"}
                ]
            },
        },
    )

    payload = global_todos.build_global_todos(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        limit=1,
    )

    envelope = payload["projection_envelope"]
    assert envelope["projection"] == "global_todos"
    assert (
        envelope["coverage"]["expected_count"],
        envelope["coverage"]["included_count"],
    ) == (4, 1)
    assert envelope["coverage"]["omitted"][0]["reason"] == "outside_current_registry"
    assert (
        envelope["coverage"]["shown_count"],
        envelope["coverage"]["available_count"],
    ) == (1, 2)
    assert envelope["coverage"]["truncated"] is True
    assert "incomplete_coverage" in envelope["alert_reasons"]
    assert envelope["upstream"][0]["projection"] == "status"
    assert "status/registry" in _sources(envelope)
    assert _sources(envelope)["goal_quota"]["item_count"] == 1
    assert "🔴 projection alerts" in global_todos.render_global_todos_markdown(payload)
    assert str(tmp_path) not in json.dumps(envelope)


def test_global_todos_missing_status_envelope_and_failed_quota_alert(
    tmp_path: Path, monkeypatch
) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=0)
    status_payload.pop("projection_envelope")
    monkeypatch.setattr(global_todos, "collect_status", lambda **_: status_payload)
    monkeypatch.setattr(
        global_todos,
        "build_quota_should_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("quota unavailable")
        ),
    )

    payload = global_todos.build_global_todos(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        limit=5,
    )

    envelope = payload["projection_envelope"]
    assert _sources(envelope)["status"]["status"] == "not_read"
    assert _sources(envelope)["goal_quota"]["unreadable_count"] == 1
    assert envelope["alert_reasons"] == [
        "missing_required_sources",
        "unreadable_sources",
    ]


def test_global_todos_missing_attention_queue_alerts(
    tmp_path: Path, monkeypatch
) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=0)
    status_payload.pop("attention_queue")
    monkeypatch.setattr(global_todos, "collect_status", lambda **_: status_payload)

    envelope = global_todos.build_global_todos(
        registry_path=tmp_path / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        limit=5,
    )["projection_envelope"]

    assert _sources(envelope)["attention_queue"]["status"] == "not_read"
    assert "missing_required_sources" in envelope["alert_reasons"]


def test_global_risks_inherits_status_and_discloses_missing_global_membership(
    tmp_path: Path, monkeypatch
) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=0)
    status_payload.update(
        {"contract": {"error_diagnostics": []}, "run_history": {"goals": []}}
    )
    monkeypatch.setattr(global_risks, "collect_status", lambda **_: status_payload)

    payload = global_risks.build_global_risks(
        registry_path=tmp_path / ".loopx" / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=5,
    )

    envelope = payload["projection_envelope"]
    assert envelope["projection"] == "global_risks"
    assert envelope["complete"] is True and envelope["alert"] is False
    assert envelope["upstream"][0]["projection"] == "status"
    assert "status/registry" in _sources(envelope)
    assert _sources(envelope)["status_contract"]["item_count"] == 0

    status_payload["global_registry"] = {"available": False, "findings": []}
    missing = global_risks.build_global_risks(
        registry_path=tmp_path / ".loopx" / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=5,
    )
    envelope = missing["projection_envelope"]
    assert envelope["coverage"]["expected_count"] is None
    assert envelope["complete"] is False
    assert "missing_required_sources" in envelope["alert_reasons"]
    assert "incomplete_coverage" in envelope["alert_reasons"]
    assert "🔴 projection alerts" in global_risks.render_global_risks_markdown(missing)
    assert str(tmp_path) not in json.dumps(envelope)


def test_global_risks_scan_truncation_is_an_incomplete_coverage_alert(
    tmp_path: Path, monkeypatch
) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=0)
    status_payload["contract"] = {
        "error_diagnostics": [
            {"code": "test_failure", "scope": "global"} for _ in range(41)
        ]
    }
    monkeypatch.setattr(global_risks, "collect_status", lambda **_: status_payload)

    payload = global_risks.build_global_risks(
        registry_path=tmp_path / ".loopx" / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=1,
    )

    envelope = payload["projection_envelope"]
    assert payload["summary"]["source_rows_truncated"] is True
    assert envelope["coverage"]["omitted"][-1]["reason"] == "source_rows_not_scanned"
    assert envelope["coverage"]["truncated"] is True
    assert "incomplete_coverage" in envelope["alert_reasons"]


def test_global_risks_empty_contract_container_is_a_read_source(
    tmp_path: Path, monkeypatch
) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=0)
    status_payload["contract"] = {}
    monkeypatch.setattr(global_risks, "collect_status", lambda **_: status_payload)

    envelope = global_risks.build_global_risks(
        registry_path=tmp_path / ".loopx" / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=5,
    )["projection_envelope"]

    assert _sources(envelope)["status_contract"]["status"] == "fresh"


@pytest.mark.parametrize(
    "receipt",
    [
        "{broken",
        "[]",
        json.dumps({"schema_version": "other"}),
        json.dumps(
            {
                "schema_version": "loopx_host_poll_receipt_v0",
                "goal_id": "goal-a",
                "agent_id": "agent-a",
                "poll_count": 1,
                "last_poll_at": "not-a-timestamp",
                "expected_continuation": True,
                "last_decision_action": "normal_run",
            }
        ),
        json.dumps(
            {
                "schema_version": "loopx_host_poll_receipt_v0",
                "goal_id": "goal-a",
                "agent_id": "agent-a",
                "poll_count": 1,
                "last_poll_at": projection_envelope.now_utc_iso(),
                "expected_continuation": "yes",
                "last_decision_action": "normal_run",
            }
        ),
        json.dumps(
            {
                "schema_version": "loopx_host_poll_receipt_v0",
                "goal_id": "goal-other",
                "agent_id": "agent-a",
                "poll_count": 1,
                "last_poll_at": projection_envelope.now_utc_iso(),
                "expected_continuation": True,
                "last_decision_action": "normal_run",
            }
        ),
    ],
    ids=["malformed-json", "array", "wrong-schema", "invalid-timestamp",
         "invalid-continuation", "wrong-goal"],
)
def test_global_risks_discloses_unreadable_host_poll_receipts_and_recovers(
    tmp_path: Path,
    monkeypatch,
    receipt: str,
) -> None:
    status_payload = _global_status_payload(tmp_path, excluded=0)
    status_payload.update({"contract": {"error_diagnostics": []}, "run_history": {"goals": []}})
    monkeypatch.setattr(global_risks, "collect_status", lambda **_: status_payload)
    receipt_path = tmp_path / ".loopx-host-poll-receipts" / "goal-a.json"
    receipt_path.parent.mkdir()
    receipt_path.write_text(receipt, encoding="utf-8")

    unreadable = global_risks.build_global_risks(
        registry_path=tmp_path / ".loopx" / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=5,
    )["projection_envelope"]

    source = _sources(unreadable)["host_poll_receipts"]
    assert source["read_status"] == "read"
    assert source["unreadable_count"] == 1
    assert unreadable["fresh"] is False
    assert unreadable["complete"] is False
    assert unreadable["alert_reasons"] == ["incomplete_coverage", "unreadable_sources"]
    assert unreadable["coverage"]["omitted"] == [
        {"reason": "host_poll_receipts_unreadable", "count": 1, "refs": ["goal-a"]}
    ]

    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": "loopx_host_poll_receipt_v0",
                "goal_id": "goal-a",
                "agent_id": "agent-a",
                "poll_count": 2,
                "last_poll_at": projection_envelope.now_utc_iso(),
                "expected_continuation": True,
                "last_decision_action": "normal_run",
            }
        ),
        encoding="utf-8",
    )
    recovered = global_risks.build_global_risks(
        registry_path=tmp_path / ".loopx" / "registry.json",
        runtime_root_override=None,
        scan_roots=[],
        agent_id=None,
        time_range="24h",
        limit=5,
    )["projection_envelope"]

    assert _sources(recovered)["host_poll_receipts"]["unreadable_count"] == 0
    assert recovered["complete"] is True
    assert recovered["alert"] is False


def test_receipt_case_collection_does_not_depend_on_the_collecting_process_clock(tmp_path):
    import os
    import subprocess
    import sys

    # Two serial collectors model the independent imports in xdist workers,
    # without starting a second worker pool inside a running test shard.
    (tmp_path / "receipt_collection_clock.py").write_text(
        "import os\n"
        "import loopx.control_plane.projection_envelope_facts as facts\n"
        "def pytest_configure(config):\n"
        "    facts.now_utc_iso = lambda: os.environ['LOOPX_RECEIPT_COLLECTION_CLOCK']\n",
        encoding="utf-8",
    )
    target = (
        "tests/test_projection_envelope.py::"
        "test_global_risks_discloses_unreadable_host_poll_receipts_and_recovers"
    )
    collections = []
    for clock in ("2026-01-01T00:00:00Z", "2026-01-01T00:00:01Z"):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", target, "--collect-only", "-q",
             "-o", "addopts=", "-p", "receipt_collection_clock"],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "LOOPX_RECEIPT_COLLECTION_CLOCK": clock,
                 "PYTHONPATH": os.pathsep.join((str(tmp_path), os.environ.get("PYTHONPATH", "")))},
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        nodeids = [line for line in result.stdout.splitlines() if line.startswith(target + "[")]
        assert len(nodeids) == len(set(nodeids)) == 6
        collections.append(nodeids)
    assert collections[0] == collections[1], "independent worker clocks changed collection identity"


def test_real_cli_global_membership_failure_and_recovery(tmp_path):
    """Global scope must never use local membership as its denominator."""
    import subprocess
    import sys

    registry = _registry(tmp_path, ("goal-a",))
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    global_path = runtime / "registry.global.json"
    global_registry = json.loads(registry.read_text())
    second_state = tmp_path / "SECOND_GOAL_STATE.md"
    second_state.write_text("# Second Goal\n")
    global_registry["goals"].append({**global_registry["goals"][0], "id": "goal-b",
                                     "state_file": str(second_state)})

    def cli(command, registry_path=registry):
        result = subprocess.run(
            [sys.executable, "-c", "from loopx.cli_runtime import main; raise SystemExit(main())",
             "--registry", str(registry_path), "--format", "json", command, "--limit", "1"],
            text=True, capture_output=True, timeout=30,
        )
        assert result.returncode in (0, 1), result.stderr
        return json.loads(result.stdout)


    for content, reason in ((None, "missing_required_sources"), ("{broken", "unreadable_sources")):
        if content is None:
            global_path.unlink(missing_ok=True)
        else:
            global_path.write_text(content)
        for command in (
            "global-summary",
            "global-gates",
            "global-todos",
            "global-risks",
        ):
            envelope = cli(command)["projection_envelope"]
            assert envelope["coverage"]["expected_count"] is None
            assert envelope["complete"] is False and envelope["alert"] is True
            assert reason in envelope["alert_reasons"]
            assert "incomplete_coverage" in envelope["alert_reasons"]
        if content is None:
            local = cli("status")["projection_envelope"]
            assert local["complete"] is True and local["alert"] is False
        global_path.write_text(json.dumps(global_registry))
        for command in (
            "global-summary",
            "global-gates",
            "global-todos",
            "global-risks",
        ):
            local = cli(command)["projection_envelope"]
            assert (local["coverage"]["included_count"], local["coverage"]["expected_count"]) == (1, 2)
            assert local["complete"] is False
            whole = cli(command, global_path)["projection_envelope"]
            assert whole["complete"] is True and whole["alert"] is False
