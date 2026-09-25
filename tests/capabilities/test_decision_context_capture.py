from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from loopx.capabilities.decision_context import (
    DecisionEvidenceRecords,
    settle_profile_decision_review,
)
from loopx.cli import main
from loopx.capabilities.decision_context.capture import (
    assemble_captured_decision_evidence,
    capture_profile_sources,
)
from loopx.capabilities.decision_context.profile import (
    normalize_decision_context_profile,
)
from loopx.capabilities.decision_context.providers import (
    LocalFileDecisionSourceProvider,
)
from test_decision_context_profile import profile_payload


@pytest.fixture
def setup(tmp_path):
    authority = tmp_path / "authority.txt"
    authority.write_text("private-body-not-for-spool")
    payload = profile_payload(authority)
    payload["automation"].update(
        automatic_capture=True, source_ids=[payload["sources"][0]["source_id"]]
    )
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps(payload))
    args = dict(
        goal_id=payload["goal_id"],
        agent_id="example-agent",
        profile_path=profile,
        spool_path=tmp_path / "spool.sqlite",
        cursor_path=tmp_path / "reviewed.json",
    )
    return args, payload, authority


def test_explicit_opt_in_and_read_only_preview(setup):
    args, payload, _ = setup
    assert capture_profile_sources(**args)["status"] == "not_started"
    assert not args["spool_path"].exists()
    payload["automation"]["automatic_capture"] = False
    args["profile_path"].write_text(json.dumps(payload))
    assert capture_profile_sources(**args, execute=True)["status"] == "capture_disabled"
    assert not args["spool_path"].exists()
    payload["automation"].update(automatic_capture=True, source_ids=[])
    with pytest.raises(ValueError, match="allowlist"):
        normalize_decision_context_profile(payload)
    payload["automation"]["source_ids"] = ["unknown"]
    with pytest.raises(ValueError, match="incremental"):
        normalize_decision_context_profile(payload)


def settle_batch(args, batch_id, rebase=None, *, assembly=None):
    assembly = assembly or assemble_captured_decision_evidence(
        **args,
        batch_id=batch_id,
        decision_id=f"review-{batch_id}",
        rebase=rebase or (lambda _: DecisionEvidenceRecords(semantic_no_change=True)),
    )
    assert assembly.proposed_cursors
    settled = settle_profile_decision_review(
        goal_id=args["goal_id"],
        agent_id=args["agent_id"],
        profile_path=args["profile_path"],
        cursor_path=args["cursor_path"],
        assembly=assembly,
        lifecycle_event_log_path=args["spool_path"].parent / "events.jsonl",
        actor_ref="agent:example-agent",
        reason_code="no_material_change",
        summary="The verified change does not alter the current decision.",
        execute=True,
    )
    assert settled["disposition"] == "no_change"


def test_capture_replay_and_review_cursor_separation(setup):
    args, _, _ = setup
    captured = capture_profile_sources(**args, execute=True)
    assert captured["pending_batch_count"] == 1
    assert not args["cursor_path"].exists()
    assert not captured["decision_cursors_mutated"]
    assert b"private-body-not-for-spool" not in args["spool_path"].read_bytes()
    assert "authority.txt" not in json.dumps(captured)
    assert args["spool_path"].stat().st_mode & 0o777 == 0o600
    assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 1
    reads = []

    def rebase(collection):
        reads.extend(item.content for item in collection.authority)
        return DecisionEvidenceRecords(semantic_no_change=True)

    settle_batch(args, 1, rebase)
    assert reads == ["private-body-not-for-spool"]
    assert capture_profile_sources(**args)["pending_batch_count"] == 1
    assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 0


def test_stale_revision_cannot_be_silently_consumed(setup):
    args, _, authority = setup
    capture_profile_sources(**args, execute=True)
    authority.write_text("changed-body")
    with pytest.raises(ValueError, match="revision unavailable"):
        assemble_captured_decision_evidence(
            **args,
            batch_id=1,
            decision_id="review-example",
            rebase=lambda _: DecisionEvidenceRecords(semantic_no_change=True),
        )
    assert capture_profile_sources(**args)["pending_batch_count"] == 1
    assert not args["cursor_path"].exists()


def test_binding_change_holds_existing_state(setup):
    args, payload, _ = setup
    capture_profile_sources(**args, execute=True)
    payload["sources"][0]["private_locator"] += ".other"
    args["profile_path"].write_text(json.dumps(payload))
    status = capture_profile_sources(**args, execute=True)
    assert status["sources"][0]["status"] == "binding_changed"
    assert status["pending_batch_count"] == 1
    with pytest.raises(ValueError, match="binding changed"):
        assemble_captured_decision_evidence(
            **args,
            batch_id=1,
            decision_id="review-example",
            rebase=lambda _: DecisionEvidenceRecords(),
        )


def test_failure_and_backpressure_do_not_advance_cursor(setup):
    args, payload, _ = setup

    class BadProvider(LocalFileDecisionSourceProvider):
        def scan(self, **kwargs):
            raise RuntimeError("private-secret-must-not-leak")

    failed = capture_profile_sources(
        **args,
        execute=True,
        source_provider_overrides={
            "local-authority": BadProvider(
                provider_id="local-authority", max_bytes=4096
            )
        },
    )
    assert failed["sources"][0]["status"] == "provider_failed"
    assert "private-secret" not in json.dumps(failed)
    assert failed["pending_batch_count"] == 0
    with sqlite3.connect(args["spool_path"]) as db:
        assert db.execute("SELECT cursor FROM sources").fetchone()[0] is None
        db.execute("UPDATE sources SET checked_at=NULL")
    assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 1
    payload["automation"]["max_pending_batches"] = 1
    args["profile_path"].write_text(json.dumps(payload))
    with sqlite3.connect(args["spool_path"]) as db:
        cursor = db.execute("SELECT cursor FROM sources").fetchone()[0]
        db.execute("UPDATE sources SET checked_at=NULL")
    result = capture_profile_sources(**args, execute=True)
    assert result["sources"][0]["status"] == "backpressure"
    with sqlite3.connect(args["spool_path"]) as db:
        assert db.execute("SELECT cursor FROM sources").fetchone()[0] == cursor


def test_scan_scope_mismatch_and_agent_scope(setup):
    args, _, _ = setup

    class WrongScope(LocalFileDecisionSourceProvider):
        def scan(self, **kwargs):
            return replace(super().scan(**kwargs), source_id="other-source")

    result = capture_profile_sources(
        **args,
        execute=True,
        source_provider_overrides={
            "local-authority": WrongScope(provider_id="local-authority", max_bytes=4096)
        },
    )
    assert result["pending_batch_count"] == 0
    assert result["sources"][0]["status"] == "provider_failed"
    assert (
        capture_profile_sources(**{**args, "agent_id": "other-agent"}, execute=True)[
            "status"
        ]
        == "capture_disabled"
    )


def test_spool_scope_and_concurrent_tick_are_guarded(setup):
    args, payload, _ = setup
    capture_profile_sources(**args, execute=True)
    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            capture_profile_sources(**args, execute=True)
    payload["enabled_agents"].append("other-agent")
    args["profile_path"].write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="goal/agent mismatch"):
        capture_profile_sources(**{**args, "agent_id": "other-agent"}, execute=True)


def test_capture_cli_and_status_are_public_safe(setup, capsys):
    args, _, _ = setup
    common = [
        "--goal-id",
        args["goal_id"],
        "--agent-id",
        args["agent_id"],
        "--profile",
        str(args["profile_path"]),
        "--spool",
        str(args["spool_path"]),
    ]
    runtime_root = args["spool_path"].parent / "runtime"
    assert (
        main(
            [
                "--format",
                "json",
                "--runtime-root",
                str(runtime_root),
                "decision-context",
                "capture",
                *common,
                "--execute",
            ]
        )
        == 0
    )
    assert len(list((runtime_root / "decision-context" / "capture-hosts").glob("*.json"))) == 1
    captured = capsys.readouterr().out
    assert "private-body" not in captured
    assert json.loads(captured)["pending_batch_count"] == 1
    before = args["spool_path"].read_bytes()
    assert (
        main(["--format", "json", "decision-context", "capture-status", *common]) == 0
    )
    assert json.loads(capsys.readouterr().out)["pending_batch_count"] == 1
    assert args["spool_path"].read_bytes() == before


def test_old_review_cursor_does_not_retire_unreviewed_aba_batches(setup):
    args, _, authority = setup
    original = authority.read_text()
    capture_profile_sources(**args, execute=True)
    settle_batch(args, 1)
    assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 0
    reviewed = args["cursor_path"].read_bytes()
    for body, expected in [("intermediate-change", 1), (original, 2)]:
        authority.write_text(body)
        with sqlite3.connect(args["spool_path"]) as db:
            db.execute("UPDATE sources SET checked_at=NULL")
        assert (
            capture_profile_sources(**args, execute=True)["pending_batch_count"]
            == expected
        )
    for _ in range(2):
        assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 2
    assert args["cursor_path"].read_bytes() == reviewed


@pytest.mark.parametrize("change", ["disable", "binding"])
def test_profile_change_during_activation_is_rejected_before_capture(
    setup, monkeypatch, change
):
    from loopx.capabilities.decision_context import capture

    args, payload, _ = setup
    resolve = capture.resolve_decision_context_activation

    def changing_resolve(**kwargs):
        result = resolve(**kwargs)
        if change == "disable":
            payload["automation"]["automatic_capture"] = False
        else:
            payload["sources"][0]["private_locator"] += ".rebound"
        args["profile_path"].write_text(json.dumps(payload))
        return result

    monkeypatch.setattr(
        capture, "resolve_decision_context_activation", changing_resolve
    )
    with pytest.raises(ValueError, match="profile changed"):
        capture_profile_sources(**args, execute=True)
    assert not args["spool_path"].exists()


def test_review_transitions_retire_one_batch_including_reused_cursor(setup):
    args, _, authority = setup
    original = authority.read_text()
    for batch_id, body in enumerate([original, "intermediate", original], start=1):
        authority.write_text(body)
        if args["spool_path"].exists():
            with sqlite3.connect(args["spool_path"]) as db:
                db.execute("UPDATE sources SET checked_at=NULL")
        assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 1
        settle_batch(args, batch_id)
        assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 0


def test_new_review_retires_only_oldest_duplicate_cursor_batch(setup):
    args, _, authority = setup
    original = authority.read_text()
    capture_profile_sources(**args, execute=True)
    assembly = assemble_captured_decision_evidence(
        **args,
        batch_id=1,
        decision_id="review-1",
        rebase=lambda _: DecisionEvidenceRecords(semantic_no_change=True),
    )
    for body in ["intermediate", original]:
        authority.write_text(body)
        with sqlite3.connect(args["spool_path"]) as db:
            db.execute("UPDATE sources SET checked_at=NULL")
        capture_profile_sources(**args, execute=True)
    settle_batch(args, 1, assembly=assembly)
    assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 2
    with sqlite3.connect(args["spool_path"]) as db:
        assert db.execute("SELECT id FROM batches ORDER BY id").fetchall() == [
            (2,),
            (3,),
        ]


def test_legacy_spool_without_review_observations_retains_batches(setup):
    args, _, _ = setup
    capture_profile_sources(**args, execute=True)
    settle_batch(args, 1)
    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("DROP TABLE review_observations")
    for _ in range(2):
        assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 1


@pytest.mark.parametrize("change", ["disable", "binding"])
def test_profile_change_during_scan_rolls_back_progress(setup, change):
    args, payload, _ = setup

    class ChangingProvider(LocalFileDecisionSourceProvider):
        def scan(self, **kwargs):
            result = super().scan(**kwargs)
            if change == "disable":
                payload["automation"]["automatic_capture"] = False
            else:
                payload["sources"][0]["private_locator"] += ".rebound"
            args["profile_path"].write_text(json.dumps(payload))
            return result

    with pytest.raises(ValueError, match="profile changed"):
        capture_profile_sources(
            **args,
            execute=True,
            source_provider_overrides={
                "local-authority": ChangingProvider(
                    provider_id="local-authority", max_bytes=4096
                )
            },
        )
    with sqlite3.connect(args["spool_path"]) as db:
        for table in ["sources", "batches", "review_observations"]:
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert not args["cursor_path"].exists()
