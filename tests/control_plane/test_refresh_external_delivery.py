"""Exercise legacy, local and failed-persistence paths through the real backend."""
import json
from types import SimpleNamespace

import pytest

from loopx import cli
from loopx.control_plane.quota import refresh_external_delivery as bridge
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.state_refresh import refresh_state_run
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _write_fixture,
)


@pytest.fixture
def session(tmp_path, monkeypatch, capsys):
    project, runtime, registry = _write_fixture(tmp_path)
    monkeypatch.setenv("LOOPX_RUNTIME_ROOT", str(tmp_path / "shared"))
    monkeypatch.chdir(project)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime)]
    binding = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID]

    def run(args, expected=0):
        rc = cli.main(["--format", "json", *prefix, *args])
        stdout = capsys.readouterr().out
        assert rc == expected, stdout
        return json.loads(stdout)

    assert run(["quota", "should-run", "--codex-app", *binding,
                "--scan-path", str(project)])["decision"] == "run"
    args = ["refresh-state", *binding, "--classification", "validated_change",
            "--delivery-outcome", "outcome_progress", "--no-global-sync"]
    journal = runtime / "goals" / GOAL_ID / "rollout-event-log.jsonl"
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    return project, runtime, registry, args, run, journal, index


def test_unstated_direct_caller_and_legacy_receipt_repair_remain_usable(session):
    project, runtime, registry, args, run, journal, _ = session
    local = refresh_state_run(
        registry_path=registry, runtime_root_override=str(runtime), goal_id=GOAL_ID,
        project=project, state_file=None, classification="validated_change",
        recommended_action=None, delivery_outcome="outcome_progress", todo_id=TODO_ID,
        turn_instance_id=TURN_ID, agent_id=AGENT_ID, dry_run=False, sync_global=False,
    )
    assert local["ok"] and local["appended"]
    assert local["external_sink_delivery_authorized"] is False
    assert "refresh_external_delivery" not in journal.read_text(encoding="utf-8")
    repaired = run(args)  # Real old-style writeback lacking both pause and CLI receipt.
    assert repaired["receipt_repaired"] is True
    assert repaired["external_sink_delivery_authorized"] is False
    replay = run(args)
    assert replay["idempotent_replay"] is True
    assert replay["external_sink_delivery_authorized"] is True
    assert replay["external_delivery"]["reason"] == "legacy_per_call"


def test_dry_run_cannot_pause_or_resume_a_committed_operation(session):
    _, _, _, args, run, journal, index = session
    run(args)
    before = journal.read_bytes(), index.read_bytes()
    run([*args, "--suppress-external-sinks", "--dry-run"])
    assert (journal.read_bytes(), index.read_bytes()) == before
    assert run(args)["external_sink_delivery_authorized"] is True
    run([*args, "--suppress-external-sinks"])
    denied = run(args, expected=1)
    assert denied["settlement_progress"]["state"] == "spend_required"
    assert "settlement_owed" not in denied  # Rejected recovery is not a spend instruction.
    key = denied["external_delivery"]["resume_key"]
    before = journal.read_bytes(), index.read_bytes()
    run([*args, "--resume-external-sinks", key, "--dry-run"])
    assert (journal.read_bytes(), index.read_bytes()) == before
    assert run(args, expected=1)["error_code"] == "external_delivery_resume_required"


def test_pause_persistence_failure_stops_before_business_writeback(session, monkeypatch):
    _, _, _, args, run, journal, index = session
    before = journal.read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("fixture journal unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(bridge, "append_rollout_event", fail)
        failed = run([*args, "--suppress-external-sinks"], expected=1)
    assert "fixture journal unavailable" in failed["error"]
    assert journal.read_bytes() == before
    assert not index.exists()
    assert run([*args, "--suppress-external-sinks"])["ok"]
    assert run(args, expected=1)["error_code"] == "external_delivery_resume_required"


def test_receipt_only_repair_preserves_pause_without_requiring_confirmation(session):
    _, _, _, args, run, journal, index = session
    run([*args, "--suppress-external-sinks"])
    rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    # Synthetic crash fixture: writeback exists, its CLI receipt is absent.
    journal.write_text("".join(json.dumps(row) + "\n" for row in rows
                               if row["event_kind"] != "refresh_state"), encoding="utf-8")
    before = index.read_bytes()
    assert run(args)["receipt_repaired"] is True
    assert index.read_bytes() == before
    assert run(args, expected=1)["error_code"] == "external_delivery_resume_required"


def test_exact_external_delivery_transition_persists_goal_ref(tmp_path):
    goal_ref = {
        "goal_id": GOAL_ID,
        "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    }
    identity = SettlementIdentity(
        GOAL_ID,
        AGENT_ID,
        TODO_ID,
        TURN_ID,
    )
    readback = SimpleNamespace(
        identity=SimpleNamespace(value=identity),
        external_delivery={
            "schema_version": "refresh_external_delivery_v0",
            "authorized": False,
            "transition": {
                "state": "paused",
                "resume_key": "resume-fixture",
            },
        },
    )
    result = bridge.finish_external_delivery_refresh(
        {"ok": True},
        readback,
        tmp_path,
        dry_run=False,
        goal_ref=goal_ref,
    )
    events = json.loads(
        (tmp_path / "goals" / GOAL_ID / "rollout-event-log.jsonl")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert events["goal_ref"] == goal_ref
    assert result["external_sink_delivery_authorized"] is False
