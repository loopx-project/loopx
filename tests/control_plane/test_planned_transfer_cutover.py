"""Source-online local cutover continues the original Task through real owners.

These synthetic two-environment CLI drills do not qualify a normal installed
App, cross-machine authorization, a model session or the complete user profile.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, effect_runtime_result, restart_effect_runtime
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.goals.planned_transfer_io import (
    cancel_prepared_goal_transfer, create_planned_transfer_recipient, export_registered_goal_transfer,
    local_goal_transfer_cutover, preview_protected_goal_transfer_target, preview_registered_goal_transfer,
    stage_protected_goal_transfer,
)
from loopx.control_plane.goals import planned_transfer_io
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from loopx.control_plane.turn_driver.codex_sessions import _session_path, _store_codex_cli_session
from loopx.control_plane.quota.spend_commit import record_quota_slot_spend_from_preview, replay_quota_spend_by_effect_ref
from loopx.history import collect_history
from tests.control_plane.test_planned_transfer_cancellation import source as source_fixture, operation_directory
from tests.control_plane.test_quota_spend_commit_runtime import _preview as quota_preview
from tests.control_plane.test_planned_transfer_operation import _http

source = source_fixture
OPERATION = "move-original-task"


def _staged(source: dict, root: Path, provider: str):
    recipient = create_planned_transfer_recipient(destination=root / "recipient")
    preview = preview_registered_goal_transfer(registry_path=source["registry"], goal_id="report", operation_id=OPERATION)
    material = source["project"] / "input.txt"
    exported = export_registered_goal_transfer(registry_path=source["registry"], goal_id="report", operation_id=OPERATION,
        expected_preview_sha256=preview["preview_sha256"], recipient=recipient, destination=root / "portable",
        materials=[(material, hashlib.sha256(material.read_bytes()).hexdigest())])
    project, runtime = root / "destination", root / "destination-runtime"
    project.mkdir()
    artifact = exported["protected_artifact"]["artifact_sha256"]
    arguments = dict(directory=root / "portable", private_key=root / "recipient/private-key.pem",
        expected_artifact_sha256=artifact, project=project, runtime_root=runtime, provider=provider)
    workspace = preview_protected_goal_transfer_target(**arguments)
    staged = stage_protected_goal_transfer(**arguments, expected_workspace_preview_sha256=workspace["preview_sha256"])
    registry = project / ".loopx/registry.json"

    def cli(*arguments: str, expected_code: int | tuple[int, ...] = 0):
        result = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
            "--runtime-root", str(runtime), *arguments], cwd=project, text=True, capture_output=True, timeout=90)
        allowed = expected_code if isinstance(expected_code, tuple) else (expected_code,)
        assert result.returncode in allowed, result.stdout + result.stderr
        return json.loads(result.stdout)

    return {"project": project, "runtime": runtime, "registry": registry, "cli": cli, "staged": staged}


def _preview(source: dict, target: dict):
    return source["cli"]("goal-transfer", "cutover-preview", "--goal-id", "report", "--operation-id", OPERATION,
        "--destination-registry", str(target["registry"]))


def _cutover(source: dict, target: dict, preview: dict, *, expected_code: int = 0):
    return source["cli"]("goal-transfer", "cutover", "--goal-id", "report", "--operation-id", OPERATION,
        "--destination-registry", str(target["registry"]), "--expected-preview-sha256", preview["preview_sha256"],
        "--execute", expected_code=expected_code)


def _target_worktree(target: dict):
    project = target["project"]
    def git(*arguments: str):
        subprocess.run(["git", "-C", str(project), *arguments], check=True, capture_output=True)
    git("init", "-q")
    git("remote", "add", "origin", "https://example.org/fixtures/agent-transfer.git")
    git("add", "input.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.org", "commit", "-qm", "Original transferred input")
    worktree = project.parent / "destination-agent"
    git("worktree", "add", "-qb", "continue-original", str(worktree))
    return worktree


@pytest.mark.parametrize("target_provider", ["file", "sqlite"])
def test_exact_cutover_then_original_task_result_and_accounting(source: dict, tmp_path: Path, target_provider: str):
    target = _staged(source, tmp_path, target_provider)
    original = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    source_bytes = source["registry"].read_bytes(), source["fence"].read_bytes()
    preview = _preview(source, target)
    assert preview["source_final"] is False and preview["target_task_admitted"] is False
    assert not (operation_directory(source, OPERATION) / "cutover.json").exists()
    assert source_bytes == (source["registry"].read_bytes(), source["fence"].read_bytes())
    assert _cutover(source, target, preview)["target_task_admitted"] is True
    # The selected profile has qualified only native Task/accounting owners.
    # It must not silently widen that result into Host, session or effect grants.
    with pytest.raises(EffectRuntimeRemoteError, match="no Host/session/effect admission"):
        _store_codex_cli_session(target["runtime"], lineage=source["lineage"], session_id="copied-session-is-not-admission")
    assert not _session_path(target["runtime"], lineage=source["lineage"]).exists()
    decision = (operation_directory(source, OPERATION) / "cutover.json").read_bytes()
    # The source retains its prepared fence permanently; no old session, lease
    # or runtime writer can become current again after destination admission.
    assert source_bytes == (source["registry"].read_bytes(), source["fence"].read_bytes())
    with pytest.raises(EffectRuntimeRemoteError, match="cutover is final"):
        cancel_prepared_goal_transfer(runtime_root=source["runtime"], goal_id="report", operation_id=OPERATION)
    assert "prepared for transfer" in source["acquire"]("stale-source", expected_code=1)["error"]
    with pytest.raises((ValueError, EffectRuntimeRemoteError), match="prepared for transfer"):
        source["admission"].require_current()
    assert target["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"] == original
    worktree = _target_worktree(target)
    old = target["cli"]("task-lease", "acquire", "--goal-id", "report", "--todo-id", source["todo"], "--owner", "agent-a",
        "--idempotency-key", "original-work", "--write-scope", "report.txt", "--write-worktree", str(worktree), expected_code=1)
    assert old["ok"] is False
    lease = target["cli"]("task-lease", "acquire", "--goal-id", "report", "--todo-id", source["todo"], "--owner", "agent-a",
        "--idempotency-key", "continue-original", "--write-scope", "report.txt", "--write-worktree", str(worktree))["lease"]
    assert lease["lease_epoch"] > 1 and lease["status"] == "active"
    old_release = target["cli"]("task-lease", "release", "--goal-id", "report", "--todo-id", source["todo"], "--owner", "agent-a",
        "--idempotency-key", "original-work", "--expected-version", "1", expected_code=(0, 1))
    if old_release.get("ok"):
        assert old_release["lease"]["idempotency_key"] == "original-work"
        assert old_release["lease"]["status"] == "released"
    # A historical readback or a stale-proof refusal are both legal. Neither
    # may release the new epoch; completion below must accept its unchanged proof.
    # A deterministic Agent stand-in performs the actual original request under
    # the new target lease. The independent expected result predates transfer.
    report = worktree / "report.txt"
    report.write_text(f"{sum(map(int, (worktree / 'input.txt').read_text().split()))}\n")
    assert report.read_text() == "21\n"
    done = target["cli"]("todo", "complete", "--goal-id", "report", "--todo-id", source["todo"], "--agent-id", "agent-a",
        "--task-lease-idempotency-key", lease["idempotency_key"], "--task-lease-expected-version", str(lease["version"]),
        "--evidence", "report.txt", "--note", "Original sum independently checked as 21")
    assert done["ok"] is True
    current = target["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    assert current["done"] is True and current["text"] == original["text"]
    assert current["todo_id"] == source["todo"] and current["required_write_scopes"] == original["required_write_scopes"]
    # The initiating source entry follows the native destination. It does not
    # mistake its own still-open historical Task for the destination result.
    observed = source["cli"]("goal-transfer", "inspect", "--goal-id", "report")
    assert observed["source_state"] == "cutover_recorded" and observed["cancel_available"] is False
    assert observed["local_cutover"]["target_task_admitted"] is True and observed["continuation_available"] is False
    assert next(row for row in observed["work"]["todos"] if row["todo_id"] == source["todo"])["done"] is True
    server, thread, call = _http(source)
    try:
        assert call({"action": "inspect", "goal_id": "report"}) == (200, observed)
        assert call({"action": "cancel", "goal_id": "report", "operation_id": OPERATION})[0] == 400
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    replay = replay_quota_spend_by_effect_ref(target["runtime"], goal_id="report", effect_ref=source["debit"]["effect_id"],
        agent_id="agent-a", read_only=False, registry_path=target["registry"])
    assert replay["replay_found"] is True and replay["appended"] is False
    spend = record_quota_slot_spend_from_preview(quota_preview(goal_id="report", agent_id="agent-a", effect_ref="continued-work#quota_spend"),
        {"runtime_root": str(target["runtime"])}, goal_id="report", execute=True, registry_path=target["registry"])
    assert spend["appended"] is True
    history = collect_history(registry_path=target["registry"], runtime_root=target["runtime"], goal_id="report", limit=50)["goals"][0]
    assert history["quota"]["spent_slots"] == 2 and history["quota"]["spend_event_count"] == 2
    assert history["planned_transfer_history"]["runtime_accounting_adopted"] is True
    with project_registry_transaction(target["registry"], operation="later-display-change") as transaction:
        payload = transaction.payload_copy()
        payload["goals"][0]["display_name"] = "Later destination name"
        transaction.commit(payload)
    restart_effect_runtime(wait_for_exit=True)
    # Duplicate confirmation is pure readback even after real target writes.
    before_retry = target["registry"].read_bytes(), report.read_bytes()
    assert _cutover(source, target, preview)["replayed"] is True
    assert before_retry == (target["registry"].read_bytes(), report.read_bytes())
    assert (operation_directory(source, OPERATION) / "cutover.json").read_bytes() == decision
    hidden_target = tmp_path / "temporarily-unavailable-target"
    target["runtime"].rename(hidden_target)
    failed_read = source["cli"]("goal-transfer", "inspect", "--goal-id", "report", expected_code=1)
    assert failed_read["ok"] is False
    assert not target["runtime"].exists(), "inspection must not create lock directories in a missing target"
    hidden_target.rename(target["runtime"])
    assert source["cli"]("goal-transfer", "inspect", "--goal-id", "report")["local_cutover"]["target_task_admitted"] is True
    # Destination execution and historical accounting no longer depend on the
    # source's absolute paths after its original native admission is complete.
    source["runtime"].rename(tmp_path / "source-offline")
    assert source["cli"]("goal-transfer", "inspect", "--goal-id", "report", expected_code=1)["ok"] is False
    assert not source["runtime"].exists(), "inspection must not initialize a missing original source"
    assert collect_history(registry_path=target["registry"], runtime_root=target["runtime"], goal_id="report", limit=1)["goals"][0]["quota"]["spent_slots"] == 2
    with pytest.raises(EffectRuntimeRemoteError, match="no Host/session/effect admission"):
        FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()
    assert effect_runtime_result("coordination.planned_transfer.finish_local_cutover", {"runtime_root": str(target["runtime"]),
        "goal_id": "report", "operation_id": OPERATION})["replayed"] is True


def test_target_material_change_refuses_before_source_final(source: dict, tmp_path: Path):
    target = _staged(source, tmp_path, source["provider"])
    preview = _preview(source, target)
    failure = _cutover(source, target, {"preview_sha256": "0" * 64}, expected_code=1)
    assert "preview changed" in failure["error"]
    (target["project"] / "input.txt").write_text("Changed required input\n")
    before = target["registry"].read_bytes(), source["fence"].read_bytes()
    failure = _cutover(source, target, preview, expected_code=1)
    assert "material changed" in failure["error"]
    assert before == (target["registry"].read_bytes(), source["fence"].read_bytes())
    assert not (operation_directory(source, OPERATION) / "cutover.json").exists()
    assert cancel_prepared_goal_transfer(runtime_root=source["runtime"], goal_id="report", operation_id=OPERATION)["readback"]["original_writer_fence_restored"] is True


def test_final_source_and_lost_target_finish_recover_forward(source: dict, tmp_path: Path, monkeypatch):
    target = _staged(source, tmp_path, source["provider"])
    preview = _preview(source, target)
    real = planned_transfer_io.effect_runtime_result
    def interrupted(method, params, **kwargs):
        if method == "coordination.planned_transfer.finish_local_cutover":
            raise OSError("lost before target admission")
        return real(method, params, **kwargs)
    monkeypatch.setattr(planned_transfer_io, "effect_runtime_result", interrupted)
    with pytest.raises(OSError, match="lost before"):
        local_goal_transfer_cutover(registry_path=source["registry"], goal_id="report", operation_id=OPERATION,
            destination_registry=target["registry"], expected_preview_sha256=preview["preview_sha256"])
    assert (operation_directory(source, OPERATION) / "cutover.json").exists()
    with pytest.raises(EffectRuntimeRemoteError, match="cutover is final"):
        cancel_prepared_goal_transfer(runtime_root=source["runtime"], goal_id="report", operation_id=OPERATION)
    with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
        FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()
    monkeypatch.setattr(planned_transfer_io, "effect_runtime_result", real)
    restart_effect_runtime(wait_for_exit=True)
    assert _cutover(source, target, preview)["target_task_admitted"] is True
    assert _cutover(source, target, preview)["replayed"] is True


def test_cancel_and_final_cutover_are_mutually_exclusive(source: dict, tmp_path: Path):
    target = _staged(source, tmp_path, source["provider"])
    preview = _preview(source, target)
    def finalize():
        return local_goal_transfer_cutover(registry_path=source["registry"], goal_id="report", operation_id=OPERATION,
            destination_registry=target["registry"], expected_preview_sha256=preview["preview_sha256"])
    def cancel():
        return cancel_prepared_goal_transfer(runtime_root=source["runtime"], goal_id="report", operation_id=OPERATION)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(action) for action in (finalize, cancel)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except EffectRuntimeRemoteError as error:
                outcomes.append(str(error))
    directory = operation_directory(source, OPERATION)
    assert (directory / "cutover.json").exists() != (directory / "cancelled.json").exists(), outcomes
    assert sum(isinstance(outcome, dict) for outcome in outcomes) == 1, outcomes
    if (directory / "cutover.json").exists():
        assert _cutover(source, target, preview)["target_task_admitted"] is True
    else:
        with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
            FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()


def test_admission_corruption_and_metadata_redirect_do_not_create_a_new_grant(source: dict, tmp_path: Path):
    target = _staged(source, tmp_path, source["provider"])
    preview = _preview(source, target)
    assert _cutover(source, target, preview)["target_task_admitted"] is True
    before_registry = target["registry"].read_bytes()
    with pytest.raises(EffectRuntimeRemoteError, match="cannot be removed or redirected"):
        with project_registry_transaction(target["registry"], operation="forged-redirect") as transaction:
            payload = transaction.payload_copy()
            payload["goals"][0]["repo"] = str(tmp_path / "elsewhere")
            transaction.commit(payload)
    assert target["registry"].read_bytes() == before_registry
    fence = legacy_coordination_writer_fence_path(runtime_root=target["runtime"], goal_id="report")
    original_fence = json.loads(fence.read_text())
    directory = Path(target["staged"]["original_runtime_history_root"]).parent
    admission = directory / "admission-receipt.json"
    original = admission.read_bytes()
    changed = json.loads(original)
    changed["goal"]["display_name"] = "Forged admission"
    admission.write_text(json.dumps(changed))
    with pytest.raises(EffectRuntimeRemoteError, match="admission changed"):
        FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()
    with pytest.raises((ValueError, EffectRuntimeRemoteError), match="admission changed"):
        record_quota_slot_spend_from_preview(quota_preview(goal_id="report", agent_id="agent-a", effect_ref="not-authorized#quota_spend"),
            {"runtime_root": str(target["runtime"])}, goal_id="report", execute=True, registry_path=target["registry"])
    assert not (target["runtime"] / "goals/report/runs/index.jsonl").exists()
    # Restore only this disposable fixture's damaged evidence, then verify the
    # supported writer is live and provider replacement refuses before IO.
    admission.write_bytes(original)
    with pytest.raises(EffectRuntimeRemoteError, match="no Host/session/effect admission"):
        FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()
    migration = target["cli"]("authority-archive", "plan-migration", "--goal-id", "report", "--provider",
        "sqlite" if source["provider"] == "file" else "file", "--plan", str(tmp_path / "migration.json"), expected_code=1)
    assert migration["ok"] is False and "does not cover provider/policy" in migration["reason"]
    assert not (tmp_path / "migration.json").exists()
    assert json.loads(fence.read_text()) == original_fence
    assert target["registry"].read_bytes() == before_registry
    restart_effect_runtime(wait_for_exit=True)
    retained_runtime = tmp_path / "retained-admitted-runtime"
    target["runtime"].rename(retained_runtime)
    shutil.copytree(retained_runtime, target["runtime"])
    with pytest.raises(EffectRuntimeRemoteError, match="another host or recreated runtime"):
        FirstPartyHostGoalAdmission.for_plan(registry_path=target["registry"], goal_id="report", planned_goal_ref=None).require_current()


def test_required_capability_stays_with_its_owner_before_final_cutover(source: dict, tmp_path: Path):
    source["cli"]("todo", "add", "--goal-id", "report", "--role", "agent", "--claimed-by", "agent-a",
        "--text", "Keep the required external evidence dependency", "--required-capability", "external_evidence_poll",
        "--operation-id", "actual-required-dependency")
    target = _staged(source, tmp_path, source["provider"])
    rejected = source["cli"]("goal-transfer", "cutover-preview", "--goal-id", "report", "--operation-id", OPERATION,
        "--destination-registry", str(target["registry"]), expected_code=1)
    assert "unsupported capability or Agent binding" in rejected["error"]
    assert not (operation_directory(source, OPERATION) / "cutover.json").exists()
    assert json.loads(target["registry"].read_text())["goals"][0]["activation"]["state"] == "stopped"
