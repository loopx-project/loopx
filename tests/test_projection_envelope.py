from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import loopx.control_plane.projection_envelope_facts as projection_envelope
import loopx.summary_all as summary_all
from loopx.control_plane.runtime.status_projection_cache import (
    load_status_projection_cache,
    status_projection_cache_path,
    status_projection_cache_key,
    write_status_projection_cache,
)
from loopx.control_plane.runtime.time import now_utc, utc_isoformat
from loopx.presentation.renderers.status_markdown import render_status_markdown
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
    payload = _status(_registry(tmp_path, ("goal-a",)))
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
