"""Original accounting remains visible before destination execution is admitted.

Real CLI-created File/SQLite Goals, protected transfer and native replay are
used here. A retained history is not a grant to dispatch or spend at the target.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, restart_effect_runtime
from loopx.control_plane.goals.planned_transfer_io import (
    create_planned_transfer_recipient, export_registered_goal_transfer,
    preview_registered_goal_transfer, stage_protected_goal_transfer, read_staged_goal_transfer_runs,
)
from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path
from loopx.control_plane.quota.spend_commit import (
    record_quota_slot_spend_from_preview, replay_quota_spend_by_effect_ref,
)
from loopx.control_plane.quota.void_commit import commit_quota_slot_void
from loopx.control_plane.runtime.time import now_local_iso
from loopx.file_lock import exclusive_run_index_lock
from loopx.history import append_run_index_record, collect_history, write_reserved_run_artifacts
from tests.control_plane.test_planned_transfer_cancellation import source as source
from tests.control_plane.test_quota_spend_commit_runtime import _preview as quota_preview


def _stage(source: dict, tmp_path: Path, target_provider: str) -> tuple[dict, Path, Path]:
    registry = source["registry"]
    recipient = create_planned_transfer_recipient(destination=tmp_path / "recipient")
    preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-accounting")
    exported = export_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-accounting",
        expected_preview_sha256=preview["preview_sha256"], recipient=recipient, destination=tmp_path / "portable", materials=[])
    target, target_runtime = tmp_path / "destination", tmp_path / "destination-runtime"
    target.mkdir()
    staged = stage_protected_goal_transfer(directory=tmp_path / "portable", private_key=tmp_path / "recipient/private-key.pem",
        expected_artifact_sha256=exported["protected_artifact"]["artifact_sha256"],
        project=target, runtime_root=target_runtime, provider=target_provider)
    target_registry = target / ".loopx/registry.json"
    return staged, target_runtime, target_registry


@pytest.mark.parametrize("target_provider", ["file", "sqlite"])
@pytest.mark.parametrize("void_original", [False, True])
def test_target_history_and_original_spend_replay_use_the_retained_accounting(
    source: dict, tmp_path: Path, target_provider: str, void_original: bool,
) -> None:
    runtime, registry = source["runtime"], source["registry"]
    index = runtime / "goals/report/runs/index.jsonl"
    # The existing read model merges duplicate historical rows. Transfer must
    # preserve those bytes and the same one-debit interpretation.
    with exclusive_run_index_lock(index, operation="fixture-original-duplicate"):
        append_run_index_record(index, json.loads(index.read_text().splitlines()[0]))
    record = {"generated_at": now_local_iso(), "goal_id": "report", "classification": "input_inspected",
        "agent_id": "agent-a", "recommended_action": "Continue the original unfinished task"}
    write_reserved_run_artifacts(runs_dir=index.parent, generated_at=record["generated_at"],
        record=record, index_record=dict(record), payload=dict(record), render_markdown=lambda _payload: "# Original input observation")
    if void_original:
        corrected = commit_quota_slot_void({"runtime_root": str(runtime)}, goal_id="report",
            voided_run_generated_at=source["debit"]["generated_at"], before=quota_preview()["after"],
            effect_id="original-correction", reason_summary="Original accepted correction", execute=True, registry_path=registry)
        assert corrected["appended"] is True
    expected_spent = 0 if void_original else 1
    original = collect_history(registry_path=registry, runtime_root=runtime, goal_id="report", limit=50)
    assert original["goals"][0]["quota"]["spent_slots"] == expected_spent
    staged, target_runtime, target_registry = _stage(source, tmp_path, target_provider)
    retained = Path(staged["original_runtime_history_root"])
    original_bytes = {p.relative_to(retained): p.read_bytes() for p in retained.rglob("*") if p.is_file()}
    # The actual source is unavailable, so neither the accounting view nor a
    # replay may quietly read the old absolute artifact paths.
    restart_effect_runtime(wait_for_exit=True)
    hidden_project, hidden_runtime = tmp_path / "source-project-offline", tmp_path / "source-runtime-offline"
    source["project"].rename(hidden_project)
    runtime.rename(hidden_runtime)
    try:
        history = collect_history(registry_path=target_registry, runtime_root=target_runtime, goal_id="report", limit=50)
        goal = history["goals"][0]
        assert goal["quota"]["spent_slots"] == expected_spent
        assert goal["quota"]["spend_event_count"] == 1
        assert goal["quota"].get("void_event_count", 0) == int(void_original)
        assert goal["raw_index_records"] == original["goals"][0]["raw_index_records"]
        assert goal["unique_runs"] == original["goals"][0]["unique_runs"]
        spent = [run for run in goal["latest_runs"] if run["classification"] == "quota_slot_spent"]
        assert len(spent) == 1
        assert Path(spent[0]["json_path"]).is_relative_to(retained)
        assert json.loads(Path(spent[0]["json_path"]).read_text())["quota_event"]["slots"] == 1
        assert goal["activation_state"] == "stopped"
        assert goal["latest_status_run"]["classification"] == "input_inspected"
        assert Path(goal["latest_status_run"]["json_path"]).is_relative_to(retained)
        native_history = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
            "--registry", str(target_registry), "--runtime-root", str(target_runtime),
            "history", "--goal-id", "report", "--limit", "1"], capture_output=True, text=True, timeout=60)
        assert native_history.returncode == 0, native_history.stdout + native_history.stderr
        assert json.loads(native_history.stdout)["goals"][0]["quota"]["spent_slots"] == expected_spent
        for read_only in (True, False):
            replay = replay_quota_spend_by_effect_ref(target_runtime, goal_id="report",
                effect_ref=source["debit"]["effect_id"], agent_id="agent-a", read_only=read_only, registry_path=target_registry)
            assert replay["ok"] is True and replay["replay_found"] is True
            assert replay["appended"] is False and replay["idempotent_replay"] is True
        wrong_agent = replay_quota_spend_by_effect_ref(target_runtime, goal_id="report",
            effect_ref=source["debit"]["effect_id"], agent_id="another-agent", read_only=True, registry_path=target_registry)
        assert wrong_agent["ok"] is False
        # Even a matching old request cannot be repaired/committed as a fresh
        # transaction in the stopped destination.
        with pytest.raises((ValueError, EffectRuntimeRemoteError), match="prepared for transfer"):
            record_quota_slot_spend_from_preview(quota_preview(goal_id="report", agent_id="agent-a",
                effect_ref=source["debit"]["effect_id"]), {"runtime_root": str(target_runtime)},
                goal_id="report", execute=True, registry_path=target_registry)
        assert not (target_runtime / "goals/report/runs/index.jsonl").exists()
        assert original_bytes == {p.relative_to(retained): p.read_bytes() for p in retained.rglob("*") if p.is_file()}
        restart_effect_runtime(wait_for_exit=True)
        assert collect_history(registry_path=target_registry, runtime_root=target_runtime, goal_id="report", limit=1)["goals"][0]["quota"]["spent_slots"] == expected_spent
    finally:
        hidden_project.rename(source["project"])
        hidden_runtime.rename(runtime)


def test_corrupt_or_unbound_target_history_never_falls_back_to_zero_or_the_source(source: dict, tmp_path: Path) -> None:
    staged, runtime, registry = _stage(source, tmp_path, source["provider"])
    retained = Path(staged["original_runtime_history_root"])

    def inspect():
        return collect_history(registry_path=registry, runtime_root=runtime, goal_id="report", limit=10)

    def replay():
        return replay_quota_spend_by_effect_ref(runtime, goal_id="report", effect_ref=source["debit"]["effect_id"],
            agent_id="agent-a", read_only=True, registry_path=registry)

    assert inspect()["goals"][0]["quota"]["spent_slots"] == 1

    receipt = next((retained / "goals/report/runs/.transactions/quota-spend").glob("*.json"))
    original = receipt.read_bytes()
    receipt.write_bytes(original + b"\n")
    for read in (inspect, replay):
        with pytest.raises((ValueError, EffectRuntimeRemoteError), match="history changed"):
            read()
    assert receipt.read_bytes() == original + b"\n"
    receipt.unlink()
    receipt.symlink_to(Path(source["debit"]["json_path"]))
    for read in (inspect, replay):
        with pytest.raises((ValueError, EffectRuntimeRemoteError), match="linked"):
            read()
    receipt.unlink()
    receipt.write_bytes(original)
    fence = legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="report")
    binding = fence.read_bytes()
    fence.unlink()
    for read in (inspect, replay):
        with pytest.raises((ValueError, EffectRuntimeRemoteError), match="binding is unavailable"):
            read()
    fence.write_bytes(binding)
    with pytest.raises(EffectRuntimeRemoteError, match="binding is unavailable"):
        read_staged_goal_transfer_runs(runtime_root=runtime, goal_id="report", operation_id="another-operation")
    live = runtime / "goals/report/runs/index.jsonl"
    live.parent.mkdir(parents=True, exist_ok=True)
    later = Path(source["debit"]["index_path"]).read_bytes()
    live.write_bytes(later)
    for read in (inspect, replay):
        with pytest.raises((ValueError, EffectRuntimeRemoteError), match="outside its original owner"):
            read()
    assert live.read_bytes() == later
    live.unlink()
    assert inspect()["goals"][0]["quota"]["spent_slots"] == 1


def test_existing_target_accounting_is_rejected_before_staging(source: dict, tmp_path: Path) -> None:
    runtime = tmp_path / "destination-runtime"
    written = record_quota_slot_spend_from_preview(quota_preview(goal_id="report", agent_id="agent-a",
        effect_ref="already-owned-target#quota_spend"), {"runtime_root": str(runtime)}, goal_id="report", execute=True)
    assert written["appended"] is True
    original = {p: p.read_bytes() for p in (runtime / "goals/report/runs").rglob("*") if p.is_file()}
    with pytest.raises(EffectRuntimeRemoteError, match="runtime accounting is occupied"):
        _stage(source, tmp_path, source["provider"])
    assert original == {p: p.read_bytes() for p in original}
    assert not (tmp_path / "destination/.loopx/registry.json").exists()
    assert not legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="report").exists()
