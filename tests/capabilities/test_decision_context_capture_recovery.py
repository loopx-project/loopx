from __future__ import annotations

import json
import shutil
import sqlite3

import pytest

from loopx.capabilities.decision_context.capture import (
    capture_profile_sources,
    assemble_captured_decision_evidence,
)
from loopx.capabilities.decision_context.capture_recovery import (
    recover_capture_source,
    diagnose_capture_source,
)
from loopx.capabilities.decision_context.assembler import DecisionEvidenceRecords
from loopx.cli import main
from test_decision_context_capture import setup as capture_setup, settle_batch


@pytest.fixture
def setup(tmp_path):
    return capture_setup.__wrapped__(tmp_path)


def recover(args, source_id, action, **kwargs):
    preview = recover_capture_source(
        **args, source_id=source_id, action=action, **kwargs
    )
    return recover_capture_source(
        **args,
        source_id=source_id,
        action=action,
        execute=True,
        expected_token=preview["preview_token"],
        **kwargs,
    )


def source_id(payload):
    return payload["sources"][0]["source_id"]


def rows(args, table):
    with sqlite3.connect(args["spool_path"]) as db:
        return db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()


def due(args):
    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("UPDATE sources SET checked_at=NULL")


def test_hold_restart_exact_review_preserves_unresolved_history(setup):
    args, payload, authority = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    original = rows(args, "batches")
    authority.write_text("new-private-body")
    assert (
        diagnose_capture_source(**args, source_id=sid)["diagnosis"]
        == "replay_not_checked"
    )
    assert (
        diagnose_capture_source(**args, source_id=sid, probe=True)["diagnosis"]
        == "revision_unavailable"
    )
    held = recover(args, sid, "hold")
    assert held["held_batch_count"] == 1 and held["acquisition_held"]
    assert [r[:-1] for r in rows(args, "held_batches")] == original
    assert not args["cursor_path"].exists()
    assert capture_profile_sources(**args, execute=True)["pending_batch_count"] == 0
    recover(args, sid, "restart")
    captured = capture_profile_sources(**args, execute=True)
    assert captured["pending_batch_count"] == 1 and captured["held_batch_count"] == 1
    assert not args["cursor_path"].exists()
    with pytest.raises(ValueError, match="batch unavailable"):
        assemble_captured_decision_evidence(
            **args,
            batch_id=1,
            decision_id="old",
            rebase=lambda _: DecisionEvidenceRecords(),
        )
    settle_batch(args, captured["sources"][0]["next_batch_id"])
    after = capture_profile_sources(**args, execute=True)
    assert after["pending_batch_count"] == 0 and after["held_batch_count"] == 1
    assert [r[:-1] for r in rows(args, "held_batches")] == original
    assert b"new-private-body" not in args["spool_path"].read_bytes()


def test_one_held_source_no_longer_starves_unrelated_source(setup, tmp_path):
    args, payload, authority = setup
    sid = source_id(payload)
    second_file = tmp_path / "second.txt"
    second_file.write_text("independent")
    second = dict(
        payload["sources"][0], source_id="second", private_locator=str(second_file)
    )
    payload["sources"].append(second)
    payload["automation"].update(source_ids=[sid, "second"], max_pending_batches=1)
    args["profile_path"].write_text(json.dumps(payload))
    captured = capture_profile_sources(**args, execute=True)
    assert captured["sources"][1]["status"] == "backpressure"
    authority.write_text("changed")
    held = recover(args, sid, "hold")
    after = capture_profile_sources(**args, execute=True)
    assert after["pending_batch_count"] == 1 and after["held_batch_count"] == 1
    assert after["sources"][1]["pending_batch_count"] == 1
    assert after["sources"][0]["acquisition_held"]
    assert not args["cursor_path"].exists()
    with pytest.raises(ValueError, match="exceed active queue capacity"):
        recover(args, sid, "rollback", recovery_id=held["recovery_id"])
    # No silent deletion or unbounded second archive when retention is full.
    with pytest.raises(ValueError, match="retained-history capacity"):
        recover(args, "second", "hold")


def test_preview_is_read_only_and_scoped_rollback_restores_exact_rows(setup):
    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    original = rows(args, "batches")
    original_sources = rows(args, "sources")
    before = args["spool_path"].read_bytes()
    preview = recover_capture_source(**args, source_id=sid, action="hold")
    assert before == args["spool_path"].read_bytes()
    assert not args["cursor_path"].with_suffix(".json.lock").exists()
    held = recover_capture_source(
        **args,
        source_id=sid,
        action="hold",
        execute=True,
        expected_token=preview["preview_token"],
    )
    restored = recover(args, sid, "rollback", recovery_id=held["recovery_id"])
    assert not restored["acquisition_held"]
    assert rows(args, "batches") == original
    assert rows(args, "sources") == original_sources
    assert rows(args, "held_batches") == []
    with pytest.raises(ValueError, match="not rollbackable"):
        recover(args, sid, "rollback", recovery_id=held["recovery_id"])


def test_restart_rollback_and_then_hold_rollback(setup):
    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    held = recover(args, sid, "hold")
    restarted = recover(args, sid, "restart")
    recover(args, sid, "rollback", recovery_id=restarted["recovery_id"])
    assert capture_profile_sources(**args)["sources"][0]["acquisition_held"]
    recover(args, sid, "rollback", recovery_id=held["recovery_id"])
    assert len(rows(args, "batches")) == 1


@pytest.mark.parametrize(
    "mutation", ["capture", "review", "aba", "profile", "rebind", "spool"]
)
def test_stale_preview_rejected_without_evidence_loss(setup, mutation, tmp_path):
    args, payload, authority = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    preview = recover_capture_source(**args, source_id=sid, action="hold")
    if mutation == "capture":
        authority.write_text("next")
        due(args)
        capture_profile_sources(**args, execute=True)
    elif mutation in {"review", "aba"}:
        args["cursor_path"].write_text(json.dumps({sid: "B"}))
        if mutation == "aba":
            args["cursor_path"].write_text("{}")
    elif mutation in {"profile", "rebind"}:
        if mutation == "rebind":
            payload["sources"][0]["private_locator"] += ".new"
        else:
            payload["automation"]["interval_seconds"] = 42
        args["profile_path"].write_text(json.dumps(payload))
    else:
        replacement = tmp_path / "replacement.sqlite"
        shutil.copyfile(args["spool_path"], replacement)
        replacement.replace(args["spool_path"])
    before = rows(args, "batches")
    with pytest.raises(ValueError, match="stale"):
        recover_capture_source(
            **args,
            source_id=sid,
            action="hold",
            execute=True,
            expected_token=preview["preview_token"],
        )
    assert rows(args, "batches") == before


def test_profile_and_cursor_aba_returning_to_identical_bytes_is_rejected(setup):
    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    args["cursor_path"].write_text("{}")
    for path in (args["profile_path"], args["cursor_path"]):
        preview = recover_capture_source(**args, source_id=sid, action="hold")
        original = path.read_bytes()
        path.write_text("changed")
        path.write_bytes(original)
        with pytest.raises(ValueError, match="stale"):
            recover_capture_source(
                **args,
                source_id=sid,
                action="hold",
                execute=True,
                expected_token=preview["preview_token"],
            )


def test_duplicate_apply_disabled_profile_and_absent_token_fail_closed(setup):
    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    with pytest.raises(ValueError, match="preview token"):
        recover_capture_source(**args, source_id=sid, action="hold", execute=True)
    preview = recover_capture_source(**args, source_id=sid, action="hold")
    recover_capture_source(
        **args,
        source_id=sid,
        action="hold",
        execute=True,
        expected_token=preview["preview_token"],
    )
    with pytest.raises(ValueError, match="stale"):
        recover_capture_source(
            **args,
            source_id=sid,
            action="hold",
            execute=True,
            expected_token=preview["preview_token"],
        )
    payload["automation"]["automatic_capture"] = False
    args["profile_path"].write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="enabled capture"):
        recover(args, sid, "restart")


@pytest.mark.parametrize("mutation", ["profile", "review", "capture"])
def test_rollback_after_progress_rejected(setup, mutation):
    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    held = recover(args, sid, "hold")
    if mutation == "profile":
        args["profile_path"].write_text(json.dumps(payload, indent=2))
    elif mutation == "review":
        args["cursor_path"].write_text(json.dumps({sid: "new"}))
    else:
        recover(args, sid, "restart")
        capture_profile_sources(**args, execute=True)
    with pytest.raises(ValueError, match="state changed"):
        recover(args, sid, "rollback", recovery_id=held["recovery_id"])
    assert len(rows(args, "held_batches")) == 1


def test_binding_cursor_deletion_and_provider_failure_diagnostics(setup):
    args, payload, authority = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    assert (
        diagnose_capture_source(**args, source_id=sid, probe=True)["diagnosis"]
        == "replayable"
    )
    authority.unlink()
    assert (
        diagnose_capture_source(**args, source_id=sid, probe=True)["diagnosis"]
        == "revision_unavailable"
    )
    args["cursor_path"].write_text(json.dumps({sid: "unknown"}))
    assert (
        diagnose_capture_source(**args, source_id=sid)["diagnosis"] == "cursor_diverged"
    )
    payload["sources"][0]["private_locator"] += ".other"
    args["profile_path"].write_text(json.dumps(payload))
    assert (
        diagnose_capture_source(**args, source_id=sid)["diagnosis"] == "binding_changed"
    )
    recover(args, sid, "hold")
    recover(args, sid, "restart")
    assert (
        capture_profile_sources(**args, execute=True)["sources"][0]["status"]
        != "binding_changed"
    )


def test_transaction_contention_and_mid_apply_mutation_rollback(setup, monkeypatch):
    from loopx.capabilities.decision_context import capture_recovery as module

    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    preview = recover_capture_source(**args, source_id=sid, action="hold")
    with sqlite3.connect(args["spool_path"]) as db:
        db.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            recover_capture_source(
                **args,
                source_id=sid,
                action="hold",
                execute=True,
                expected_token=preview["preview_token"],
            )
    original_schema = module._schema

    def racing_schema(db):
        original_schema(db)
        payload["automation"]["automatic_capture"] = False
        args["profile_path"].write_text(json.dumps(payload))

    monkeypatch.setattr(module, "_schema", racing_schema)
    with pytest.raises(ValueError, match="changed during apply"):
        recover_capture_source(
            **args,
            source_id=sid,
            action="hold",
            execute=True,
            expected_token=preview["preview_token"],
        )
    assert len(rows(args, "batches")) == 1


def test_cli_recovery_preview_apply_diagnose_and_private_boundary(setup, capsys):
    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    common = [
        "--goal-id",
        args["goal_id"],
        "--agent-id",
        args["agent_id"],
        "--profile",
        str(args["profile_path"]),
        "--spool",
        str(args["spool_path"]),
        "--cursor-state",
        str(args["cursor_path"]),
        "--source-id",
        sid,
    ]
    prefix = ["--format", "json", "decision-context"]
    assert main(prefix + ["capture-diagnose", *common, "--probe"]) == 0
    assert json.loads(capsys.readouterr().out)["diagnosis"] == "replayable"
    assert main(prefix + ["capture-recovery", *common, "--action", "hold"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (
        main(
            prefix
            + [
                "capture-recovery",
                *common,
                "--action",
                "hold",
                "--execute",
                "--expected-token",
                preview["preview_token"],
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert json.loads(output)["held_batch_count"] == 1
    assert "private-body" not in output and str(args["profile_path"]) not in output
    assert not args["cursor_path"].exists()


def test_missing_spool_and_wrong_scope_do_not_create_recovery_state(setup):
    args, payload, _ = setup
    sid = source_id(payload)
    with pytest.raises(ValueError, match="existing spool"):
        recover_capture_source(
            **args,
            source_id=sid,
            action="hold",
            execute=True,
            expected_token="not-a-preview",
        )
    assert not args["spool_path"].exists()
    capture_profile_sources(**args, execute=True)
    with pytest.raises(ValueError, match="not enrolled"):
        recover_capture_source(**args, source_id="other", action="hold")
    with pytest.raises(ValueError, match="separate"):
        recover_capture_source(
            **{**args, "cursor_path": args["spool_path"]}, source_id=sid, action="hold"
        )
    assert len(rows(args, "batches")) == 1


def test_review_cursor_lock_excludes_concurrent_settlement(setup):
    import subprocess
    import sys
    from loopx.file_lock import exclusive_file_lock

    args, payload, _ = setup
    sid = source_id(payload)
    capture_profile_sources(**args, execute=True)
    preview = recover_capture_source(**args, source_id=sid, action="hold")
    command = [
        sys.executable,
        "-c",
        "from loopx.cli import main; raise SystemExit(main())",
        "--format",
        "json",
        "decision-context",
        "capture-recovery",
        "--action",
        "hold",
        "--execute",
        "--expected-token",
        preview["preview_token"],
        "--source-id",
        sid,
        "--goal-id",
        args["goal_id"],
        "--agent-id",
        args["agent_id"],
        "--profile",
        str(args["profile_path"]),
        "--spool",
        str(args["spool_path"]),
        "--cursor-state",
        str(args["cursor_path"]),
    ]
    with exclusive_file_lock(args["cursor_path"]):
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode != 0
    assert "lock" in result.stdout + result.stderr
    assert len(rows(args, "batches")) == 1
