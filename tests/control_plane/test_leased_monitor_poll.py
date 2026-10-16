"""Public CLI proof transport and recovery across business/quota authority."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_native_monitor_poll import _canonical
from test_monitor_followthrough_contract import GOAL_ID, AGENT_ID, _write_fixture
from loopx.control_plane.coordination.local_authority import (
    CanonicalAuthorityRequired,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.quota.error_codes import QuotaCommandValidationError
from loopx.control_plane.quota.monitor_poll_lease_transport import current_monitor_lease_proof
from loopx.control_plane.scheduler.monitor_poll_writeback import write_monitor_poll_todo_state
from loopx.control_plane.testing.canary_harness import run_json_cli, run_json_cli_result


LEASE = {"status": "active", "idempotency_key": "monitor-execution", "version": 3,
         "lease_epoch": 2, "acquired_at": "2026-01-01T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z"}
PROOF = {"idempotency_key": "monitor-execution", "expected_version": 3}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_rejected_missing_proof_can_retry_same_turn_with_valid_lease(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = _canonical(tmp_path, provider=provider, lease=LEASE)
    turn = ["--turn-instance-id", "monitor-rejected-proof",
        "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    guard = run_json_cli("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", *turn, registry_path=registry, runtime_root=runtime)
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)
    args = arguments(monitor)
    start = args.index("--task-lease-idempotency-key")
    missing_proof = args[:start] + args[start + 4:]
    code, rejected = run_json_cli_result(*missing_proof, *turn, registry_path=registry, runtime_root=runtime)
    assert code != 0
    assert "lease proof" in json.dumps(rejected)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True) == before
    pending = runtime / "goals" / GOAL_ID / "runs" / ".transactions" / "quota-monitor-poll"
    assert not list(pending.glob("*.json")), "definitively rejected provider request must not reserve the Turn effect"
    result = run_json_cli(*args, *turn, registry_path=registry, runtime_root=runtime)
    assert result["ok"] is True
    assert result["todo_writeback"]["lease_proof"] == PROOF
    assert run_json_cli(*args, *turn, registry_path=registry, runtime_root=runtime)["replayed"] is True
    after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)
    assert after["leases"] == before["leases"]
    assert len(after["todos"]) == len(before["todos"]) + 1
    records = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_monitor_poll" for row in records) == 1
    assert all(row.get("classification") != "quota_slot_spend" for row in records)


def arguments(monitor):
    return ["quota", "monitor-poll", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--todo-id", monitor["todo_id"],
        "--result-hash", "observed-a", "--material-change", "--next-agent-todo", "Validate changed evidence",
        "--next-action-kind", "validate", "--task-lease-idempotency-key", PROOF["idempotency_key"],
        "--task-lease-expected-version", str(PROOF["expected_version"]), "--execute"]


def automatic_arguments(monitor, *, turn_id="leased-monitor-automatic"):
    explicit = arguments(monitor)
    key_index = explicit.index("--task-lease-idempotency-key")
    del explicit[key_index:key_index + 4]
    return [*explicit, "--turn-instance-id", turn_id, "--use-current-task-lease"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native", [False, True])
def test_leased_monitor_public_cli_settles_once_without_display(tmp_path, monkeypatch, provider, native):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, monitor = _canonical(tmp_path, provider=provider, native=native, lease=LEASE)
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)
    state.unlink()
    result = run_json_cli(*arguments(monitor), registry_path=registry, runtime_root=runtime)
    assert result["ok"] is True
    assert result["todo_writeback"]["lease_proof"] == PROOF
    assert result["todo_writeback"]["material_change_generation"] == 1
    assert len(result["todo_writeback"]["next_todos"]) == 1
    after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)
    assert after["leases"] == before["leases"]
    assert len(after["todos"]) == len(before["todos"]) + 1
    assert state.exists()
    records = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_monitor_poll" for row in records) == 1
    assert all(row.get("classification") != "quota_slot_spend" for row in records)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_lease_cli_transport_replays_after_release(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = _canonical(tmp_path, provider=provider, lease=LEASE)
    args = automatic_arguments(monitor)
    guard = run_json_cli("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", "leased-monitor-automatic",
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        registry_path=registry, runtime_root=runtime)
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    args.extend(["--available-capability", "network", "--available-capability", "external_evidence_poll"])
    first = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert first["ok"] is True
    assert first["todo_writeback"]["lease_proof"] == PROOF
    run_json_cli("task-lease", "release", "--goal-id", GOAL_ID, "--todo-id", monitor["todo_id"],
        "--owner", AGENT_ID, "--idempotency-key", PROOF["idempotency_key"], "--expected-version", "3",
        registry_path=registry, runtime_root=runtime)
    replay = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert replay["replayed"] is True
    records = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_monitor_poll" for row in records) == 1
    assert all(row.get("classification") != "quota_slot_spend" for row in records)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_lease_cli_transport_rejects_expired_lease_without_pending(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = _canonical(tmp_path, provider=provider,
        lease={**LEASE, "expires_at": "2026-01-01T01:00:00Z"})
    guard = run_json_cli("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", "leased-monitor-automatic",
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        registry_path=registry, runtime_root=runtime)
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)
    code, failure = run_json_cli_result(*automatic_arguments(monitor), registry_path=registry, runtime_root=runtime)
    assert code != 0
    assert failure["ok"] is False
    assert "current active task lease" in str(failure.get("reason"))
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True) == before
    pending = runtime / "goals" / GOAL_ID / "runs" / ".transactions" / "quota-monitor-poll"
    assert not pending.exists() or not list(pending.glob("*.json"))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_lease_cli_transport_rejects_foreign_owner_without_pending(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = _canonical(
        tmp_path, provider=provider, lease={**LEASE, "owner": "another-agent"},
    )
    # The public command also requires a committed same-Turn receipt first;
    # exercise the read-only transport directly to prove its owner fence.
    with pytest.raises(QuotaCommandValidationError, match="owned by --agent-id"):
        current_monitor_lease_proof(
            runtime_root=runtime, goal_id=GOAL_ID, todo_id=monitor["todo_id"],
            agent_id=AGENT_ID, effect_id="foreign-owner-monitor-poll",
        )
    pending = runtime / "goals" / GOAL_ID / "runs" / ".transactions" / "quota-monitor-poll"
    assert not pending.exists() or not list(pending.glob("*.json"))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_lease_cli_transport_keeps_soft_claim_compatible(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = _canonical(tmp_path, provider=provider)
    turn_id = "soft-claim-monitor-automatic"
    guard = run_json_cli("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn_id,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        registry_path=registry, runtime_root=runtime)
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    args = [*automatic_arguments(monitor, turn_id=turn_id),
        "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    result = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert result["ok"] is True
    assert "lease_proof" not in result["todo_writeback"]
    replay = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert replay["replayed"] is True


def test_existing_hard_lease_receipt_cannot_lose_its_proof(tmp_path):
    registry, runtime, _state, monitor = _canonical(tmp_path, lease=LEASE)
    turn_id = "hard-lease-proof-retained"
    args = [*automatic_arguments(monitor, turn_id=turn_id),
        "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    run_json_cli("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn_id,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        registry_path=registry, runtime_root=runtime)
    assert run_json_cli(*args, registry_path=registry, runtime_root=runtime)["ok"] is True
    receipt_dir = runtime / "goals" / GOAL_ID / "runs" / ".transactions" / "quota-monitor-poll"
    receipt_path, = receipt_dir.glob("*.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["record"]["monitor_event"]["todo_writeback"].pop("lease_proof")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(QuotaCommandValidationError, match="no lease proof"):
        current_monitor_lease_proof(
            runtime_root=runtime, goal_id=GOAL_ID, todo_id=monitor["todo_id"],
            agent_id=AGENT_ID, effect_id=receipt["effect_id"],
        )


def test_current_lease_cli_transport_rejects_ambiguous_proof_arguments(tmp_path):
    registry, runtime, _state, monitor = _canonical(tmp_path, lease=LEASE)
    code, failure = run_json_cli_result(*automatic_arguments(monitor),
        "--task-lease-idempotency-key", PROOF["idempotency_key"],
        "--task-lease-expected-version", "3", registry_path=registry, runtime_root=runtime)
    assert code != 0
    assert failure["error_code"] == "QUOTA_VALIDATION_FAILED"
    assert "cannot be combined" in failure["reason"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_old_observation_time_cannot_revive_expired_proof(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = _canonical(tmp_path, provider=provider,
        lease={**LEASE, "expires_at": "2026-01-01T01:00:00Z"})
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    with pytest.raises(RuntimeError, match="current lease proof"):
        write_monitor_poll_todo_state(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
            execute=True, todo_id=monitor["todo_id"], agent_id=AGENT_ID,
            generated_at="2026-01-01T00:30:00Z", result_hash="old-but-once-valid", material_change=False,
            task_lease_idempotency_key=PROOF["idempotency_key"], task_lease_expected_version=3)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == before


def test_explicit_proof_requires_canonical_authority(tmp_path):
    registry, runtime, state = _write_fixture(tmp_path, canonical=False)
    state.write_text(
        state.read_text(encoding="utf-8")
        + "\n- [ ] [P1] Observe public progress.\n"
        + "  <!-- loopx:todo todo_id=todo_unpromoted_monitor status=open "
        + "task_class=continuous_monitor claimed_by=codex-quality-qualification "
        + "target_key=watch cadence=1h next_due_at=2000-01-01T00:00:00Z -->\n",
        encoding="utf-8",
    )
    before = state.read_bytes()
    with pytest.raises(CanonicalAuthorityRequired):
        write_monitor_poll_todo_state(registry_path=registry, runtime_root=runtime, goal_id=GOAL_ID,
            execute=True, todo_id="todo_unpromoted_monitor", agent_id=AGENT_ID,
            generated_at="2026-09-01T00:00:00Z", result_hash="a", material_change=False,
            task_lease_idempotency_key=PROOF["idempotency_key"], task_lease_expected_version=3)
    assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("automatic", [False, True])
def test_process_death_after_business_commit_recovers_after_lease_release(tmp_path, monkeypatch, provider, automatic):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state, monitor = _canonical(tmp_path, provider=provider, lease=LEASE)
    turn_id = "leased-monitor-crash"
    guard = run_json_cli("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn_id,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        registry_path=registry, runtime_root=runtime)
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    proof_args = automatic_arguments(monitor, turn_id=turn_id) if automatic else [
        *arguments(monitor), "--turn-instance-id", turn_id,
    ]
    args = [*proof_args,
        "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    # Kill the actual CLI process after the authority transaction returned its
    # receipt, before the separate quota transaction can settle it.
    script = """
import os, sys
from loopx.cli import main
import loopx.control_plane.quota.monitor_poll as monitor
original = monitor._native_result
def crash(request):
    if request.get('phase') == 'commit' and request.get('provider_receipt'):
        os._exit(73)
    return original(request)
monitor._native_result = crash
main(sys.argv[1:])
"""
    process = subprocess.run([sys.executable, "-c", script, "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", *args], capture_output=True, text=True, timeout=60)
    assert process.returncode == 73, process.stderr + process.stdout
    committed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
    assert len(committed["todos"]) == 2
    run_json_cli("task-lease", "release", "--goal-id", GOAL_ID, "--todo-id", monitor["todo_id"],
        "--owner", AGENT_ID, "--idempotency-key", PROOF["idempotency_key"], "--expected-version", "3",
        registry_path=registry, runtime_root=runtime)
    retired = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True)
    assert retired["leases"][0]["status"] == "released"
    # An old pending receipt cannot invent the missing admission snapshot.
    pending_path, = (runtime / "goals" / GOAL_ID / "runs" / ".transactions" / "quota-monitor-poll").glob("*.json")
    pending_bytes = pending_path.read_bytes()
    legacy = json.loads(pending_bytes)
    legacy["schema_version"] = "quota_monitor_poll_commit_receipt_v0"
    del legacy["admitted_decision"]
    pending_path.write_text(json.dumps(legacy))
    code, failure = run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)
    assert code != 0
    assert failure["error_code"] == "legacy_monitor_admission_unavailable"
    assert json.loads(pending_path.read_text()) == legacy
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True) == retired
    pending_path.write_bytes(pending_bytes)
    recovered = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert recovered["ok"] is True
    assert recovered["todo_writeback"]["lease_proof"] == PROOF
    again = run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    assert again["replayed"] is True
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID, include_leases=True) == retired
    records = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_monitor_poll" for row in records) == 1
