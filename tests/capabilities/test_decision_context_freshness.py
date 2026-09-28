from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from loopx.capabilities.decision_context import DecisionEvidenceRecords
from loopx.capabilities.decision_context.capture import capture_profile_sources
from loopx.capabilities.decision_context.cli import _render
from loopx.capabilities.decision_context.freshness import (
    FRESHNESS_ALERT_MARKER,
    capture_host_diagnostics_detail,
    collect_capture_host_diagnostics,
)
from loopx.capabilities.decision_context.providers import (
    LocalFileDecisionSourceProvider,
)
from loopx.capabilities.decision_context.runtime import (
    assemble_profile_decision_evidence,
)
from test_decision_context_profile import profile_payload

BASELINE = "source:authority:baseline"
ON_DEMAND = "source:authority:on-demand"


class FailingProvider(LocalFileDecisionSourceProvider):
    def scan(self, **kwargs):
        raise RuntimeError("private-secret-must-not-leak")


def _failing():
    return {
        "local-authority": FailingProvider(
            provider_id="local-authority", max_bytes=4096
        )
    }


@pytest.fixture
def setup(tmp_path):
    authority = tmp_path / "authority.txt"
    authority.write_text("private-body-not-for-output")
    payload = profile_payload(authority)
    on_demand = dict(payload["sources"][0])
    on_demand.update(source_id=ON_DEMAND, scan_mode="on_demand", priority="p1")
    payload["sources"].append(on_demand)
    payload["automation"].update(automatic_capture=True, source_ids=[BASELINE])
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(payload))
    args = dict(
        goal_id=payload["goal_id"],
        agent_id="example-agent",
        profile_path=profile,
        spool_path=tmp_path / "spool.sqlite",
        cursor_path=tmp_path / "reviewed.json",
    )
    return args, payload, tmp_path / "runtime"


def _row(report, source_id):
    return next(row for row in report["sources"] if row["source_id"] == source_id)


def test_assembly_reports_every_enabled_source_and_alerts_unscanned(setup):
    args, _, _ = setup
    now = datetime.now(timezone.utc).isoformat()
    _, assembly = assemble_profile_decision_evidence(
        goal_id=args["goal_id"],
        agent_id=args["agent_id"],
        profile_path=args["profile_path"],
        decision_id="decision:freshness",
        observed_at=now,
        before=now,
        rebase=lambda _collection: DecisionEvidenceRecords(),
    )
    report = assembly.public_packet()["source_freshness"]
    assert report["schema_version"] == "decision_source_freshness_v0"
    assert report["enabled_state_implies_freshness"] is False
    assert _row(report, BASELINE)["status"] == "fresh"
    assert _row(report, BASELINE)["last_read_at"] == now
    unscanned = _row(report, ON_DEMAND)
    assert unscanned["status"] == "not_scanned"
    assert unscanned["alert"] is True
    assert report["alert_source_ids"] == [ON_DEMAND]
    assert report["all_fresh"] is False
    rendered = _render({"status": "available", "assembly": assembly.public_packet()})
    assert f"{FRESHNESS_ALERT_MARKER} `{ON_DEMAND}`" in rendered
    assert f"{FRESHNESS_ALERT_MARKER} `{BASELINE}`" not in rendered


def test_assembly_failed_source_is_never_reported_fresh(setup):
    args, _, _ = setup
    now = datetime.now(timezone.utc).isoformat()
    _, assembly = assemble_profile_decision_evidence(
        goal_id=args["goal_id"],
        agent_id=args["agent_id"],
        profile_path=args["profile_path"],
        decision_id="decision:freshness",
        observed_at=now,
        before=now,
        source_ids=[BASELINE, ON_DEMAND],
        rebase=lambda _collection: DecisionEvidenceRecords(),
        source_provider_overrides=_failing(),
    )
    report = assembly.public_packet()["source_freshness"]
    row = _row(report, BASELINE)
    assert row["status"] == "never_read"
    assert row["last_read_at"] is None
    assert any(reason.startswith("last_attempt_") for reason in row["alert_reasons"])
    assert "private-secret" not in json.dumps(report)


def test_capture_failure_cannot_refresh_last_read(setup):
    args, _, _ = setup
    failed = capture_profile_sources(
        **args, execute=True, source_provider_overrides=_failing()
    )
    row = failed["sources"][0]
    assert row["status"] == "provider_failed"
    assert row["last_checked_at"] is not None
    assert row["last_read_at"] is None
    assert row["freshness"] == "never_read"
    assert failed["source_freshness"]["alert_source_ids"] == [BASELINE]

    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("UPDATE sources SET checked_at=NULL")
    ok = capture_profile_sources(**args, execute=True)
    read_at = ok["sources"][0]["last_read_at"]
    assert ok["sources"][0]["freshness"] == "fresh"
    assert ok["sources"][0]["failure_streak"] == 0
    assert ok["source_freshness"]["all_fresh"] is True

    for expected_streak in (1, 2):
        with sqlite3.connect(args["spool_path"]) as db:
            db.execute("UPDATE sources SET checked_at=NULL")
        again = capture_profile_sources(
            **args, execute=True, source_provider_overrides=_failing()
        )
        row = again["sources"][0]
        assert row["last_read_at"] == read_at
        assert row["failure_streak"] == expected_streak
        assert "consecutive_failures" in _row(
            again["source_freshness"], BASELINE
        )["alert_reasons"]

    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("UPDATE sources SET last_success_at=?", (old,))
    status = capture_profile_sources(**args)
    assert status["executed"] is False
    assert status["sources"][0]["freshness"] == "stale"
    assert status["source_freshness"]["stale_source_ids"] == [BASELINE]
    assert f"{FRESHNESS_ALERT_MARKER} `{BASELINE}`" in _render(status)


def test_legacy_spool_is_readable_and_migrated(setup):
    args, _, _ = setup
    checked = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(args["spool_path"]) as db:
        db.executescript("""
            CREATE TABLE identity (goal TEXT, agent TEXT);
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL,
                cursor TEXT, checked_at TEXT, status TEXT NOT NULL);
            CREATE TABLE batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT NOT NULL,
                cursor_before TEXT, cursor_after TEXT NOT NULL,
                before_time TEXT NOT NULL, receipt TEXT NOT NULL);
        """)
        db.execute(
            "INSERT INTO identity VALUES (?, ?)", (args["goal_id"], args["agent_id"])
        )
        db.execute(
            "INSERT INTO sources VALUES (?, 'legacy', NULL, ?, 'completed')",
            (BASELINE, checked),
        )
    preview = capture_profile_sources(**args)
    assert preview["sources"][0]["last_read_at"] == checked
    assert preview["sources"][0]["freshness"] == "fresh"

    capture_profile_sources(**args, execute=True)
    with sqlite3.connect(args["spool_path"]) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(sources)")}
        assert {"last_success_at", "failure_streak"} <= columns
        assert db.execute("SELECT last_success_at FROM sources").fetchone()[0] == checked


def test_doctor_host_diagnostics_detect_silent_and_failing_hosts(setup):
    args, payload, runtime_root = setup
    assert collect_capture_host_diagnostics(runtime_root)["host_count"] == 0

    capture_profile_sources(**args, execute=True, health_runtime_root=runtime_root)
    now = datetime.now(timezone.utc)
    healthy = collect_capture_host_diagnostics(runtime_root, now=now)
    assert healthy["healthy"] is True
    assert healthy["private_spools_opened"] is False
    assert str(args["spool_path"]) not in capture_host_diagnostics_detail(healthy)

    interval = payload["automation"].get("interval_seconds", 900)
    silent = collect_capture_host_diagnostics(
        runtime_root, now=now + timedelta(seconds=3 * interval)
    )
    host = silent["hosts"][0]
    assert "host_heartbeat_stale" in host["alert_reasons"]
    assert FRESHNESS_ALERT_MARKER in capture_host_diagnostics_detail(silent)

    much_later = collect_capture_host_diagnostics(
        runtime_root, now=now + timedelta(days=4)
    )
    assert much_later["hosts"][0]["alert_source_ids"] == [BASELINE]

    payload["enabled_agents"].append("other-agent")
    args["profile_path"].write_text(json.dumps(payload))
    for _ in range(2):
        with pytest.raises(ValueError, match="goal/agent mismatch"):
            capture_profile_sources(
                **(args | {"agent_id": "other-agent"}),
                execute=True,
                health_runtime_root=runtime_root,
            )
    records = sorted(
        (runtime_root / "decision-context" / "capture-hosts").glob("*.json")
    )
    assert len(records) == 1
    failing = collect_capture_host_diagnostics(runtime_root)["hosts"][0]
    assert failing["last_tick_status"] == "failed"
    assert failing["consecutive_tick_failures"] == 2
    assert "last_tick_failed" in failing["alert_reasons"]

    args["spool_path"].unlink()
    missing = collect_capture_host_diagnostics(runtime_root)["hosts"][0]
    assert "spool_missing" in missing["alert_reasons"]
