"""A failed pre-cutover move must not strand the original unfinished work.

Exercise the real CLI, codec and File/SQLite owners. Cancelling preparation
returns the unchanged source to its original owner; cached destination input
still cannot grant execution. No model or active Goal is used by these tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.coordination.shadow_management import shadow_management_directory
from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, effect_runtime_result, restart_effect_runtime
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
from loopx.control_plane.goals.planned_transfer_io import (
    cancel_prepared_goal_transfer, create_planned_transfer_recipient, export_registered_goal_transfer,
    prepare_registered_goal_transfer, preview_registered_goal_transfer,
    read_goal_transfer_context, stage_prepared_goal_transfer,
)
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from loopx.control_plane.projects import registry_codec
from loopx.control_plane.quota.spend_commit import record_quota_slot_spend_from_preview, replay_quota_spend_by_effect_ref
from loopx.control_plane.turn_driver.codex_sessions import _session_path, _store_codex_cli_session, select_codex_cli_session
from loopx.file_lock import exclusive_cross_runtime_file_lock
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_spend_commit_runtime import _preview as quota_preview


@pytest.fixture(params=["file", "sqlite"])
def source(tmp_path: Path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project, worktree, runtime = (tmp_path / name for name in ("project", "agent", "runtime"))
    project.mkdir()
    registry = project / ".loopx/registry.json"
    machine = runtime / "machine/configuration.json"
    machine.parent.mkdir(parents=True)
    machine.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": request.param,
            "canonical_creation": True, "new_goal_handoff_mode": "hard_lease"}}}))

    def git(*arguments: str) -> None:
        subprocess.run(["git", "-C", str(project), *arguments], check=True, capture_output=True)

    git("init", "-q")
    git("remote", "add", "origin", "https://example.org/fixtures/agent-transfer.git")
    (project / "input.txt").write_text("6\n7\n8\n")
    git("add", "input.txt")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.org", "commit", "-qm", "Input")
    git("worktree", "add", "-qb", "agent", str(worktree))

    def cli(*arguments: str, expected_code: int = 0) -> dict:
        result = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(runtime), *arguments],
            cwd=worktree, capture_output=True, text=True, timeout=60)
        assert result.returncode == expected_code, result.stdout + result.stderr
        return json.loads(result.stdout)

    try:
        cli("bootstrap", "--project", str(project), "--goal-id", "report",
            "--objective", "Produce report.txt from input.txt", "--no-global-sync")
        cli("configure-goal", "--goal-id", "report", "--registered-agent", "agent-a", "--write-scope", "report.txt", "--execute")
        todo = cli("todo", "add", "--goal-id", "report", "--role", "agent", "--claimed-by", "agent-a",
            "--text", "Write the input sum to report.txt", "--task-repository", "git:example.org/fixtures/agent-transfer",
            "--required-write-scope", "report.txt", "--operation-id", "pending-report")["todo_id"]

        def acquire(key: str, *, expected_code: int = 0) -> dict:
            return cli("task-lease", "acquire", "--goal-id", "report", "--todo-id", todo,
                "--owner", "agent-a", "--idempotency-key", key, "--write-scope", "report.txt",
                "--write-worktree", str(worktree), "--ttl-seconds", "7200", expected_code=expected_code)

        def release(lease: dict) -> dict:
            return cli("task-lease", "release", "--goal-id", "report", "--todo-id", todo, "--owner", "agent-a",
                "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]))

        original_lease = acquire("original-work")["lease"]
        release(original_lease)
        debit = record_quota_slot_spend_from_preview(
            quota_preview(goal_id="report", agent_id="agent-a", effect_ref="original-effect#quota_spend"),
            {"runtime_root": str(runtime)}, goal_id="report", execute=True, registry_path=registry)
        assert debit["appended"] is True and debit["slots"] == 1
        lineage = {"goal_id": "report", "agent_id": "agent-a", "todo_id": todo}
        admission = FirstPartyHostGoalAdmission.for_plan(registry_path=registry, goal_id="report", planned_goal_ref=None)
        _store_codex_cli_session(runtime, lineage=lineage, session_id="original-context")
        fence = legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="report")
        yield {"cli": cli, "acquire": acquire, "release": release, "runtime": runtime, "registry": registry,
            "project": project, "todo": todo, "debit": debit, "lineage": lineage, "admission": admission,
            "original_fence": json.loads(fence.read_bytes()), "fence": fence, "provider": request.param}
    finally:
        restart_effect_runtime(wait_for_exit=True)


def test_current_host_observation_preserves_checkpoint_source_bytes(source):
    """Checking a current binding must not mutate the source it observes."""
    mutex = shadow_management_directory(source["runtime"], "report") / "maintenance.lock"
    original = mutex.read_bytes()
    source["admission"].require_current()
    selected = select_codex_cli_session(source["runtime"], lineage=source["lineage"],
        goal_admission=source["admission"])
    assert selected["session_id"] == "original-context"
    assert mutex.read_bytes() == original


def operation_directory(source: dict, operation: str) -> Path:
    return shadow_management_directory(source["runtime"], "report") / "planned-transfers" / hashlib.sha256(canonical_bytes(operation)).hexdigest()


def cancel(source: dict, operation: str = "move-a") -> dict:
    return cancel_prepared_goal_transfer(runtime_root=source["runtime"], goal_id="report", operation_id=operation)


def test_old_registry_compensation_cannot_replace_a_prepared_source(source: dict) -> None:
    registry = source["registry"]
    with project_registry_transaction(registry, operation="old_compensation") as old:
        pass
    with project_registry_transaction(registry, operation="reviewed_source_name") as current:
        payload = current.payload_copy()
        payload["goals"][0]["display_name"] = "Reviewed source before transfer"
        current.commit(payload)
    preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a")
    prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a",
        expected_preview_sha256=preview["preview_sha256"])
    before = registry.read_bytes(), source["fence"].read_bytes()
    with pytest.raises(RuntimeError, match="closed"):
        old.restore()
    assert (registry.read_bytes(), source["fence"].read_bytes()) == before
    # The Goal is prepared, not the whole registry. Compensation of an
    # unrelated change remains legal under the original codec transaction.
    with project_registry_transaction(registry, operation="unrelated_compensation") as tx:
        tx.commit({**tx.payload_copy(), "local_display_note": "unrelated"})
        tx.restore()
    assert (registry.read_bytes(), source["fence"].read_bytes()) == before
    assert cancel(source)["readback"]["original_writer_fence_restored"] is True
    assert registry.read_bytes() == before[0]
    lease = source["acquire"]("continue-after-rejected-compensation")["lease"]
    source["release"](lease)


def test_target_registration_readback_failure_retains_the_prepared_route_for_cold_retry(
    source: dict, tmp_path: Path, monkeypatch,
) -> None:
    snapshot = read_goal_transfer_context(registry_path=source["registry"], goal_id="report")
    preview = preview_registered_goal_transfer(registry_path=source["registry"], goal_id="report", operation_id="move-a")
    prepared = prepare_registered_goal_transfer(registry_path=source["registry"], goal_id="report", operation_id="move-a",
        expected_preview_sha256=preview["preview_sha256"])
    project, runtime = tmp_path / "target-project", tmp_path / "target-runtime"
    registry = project / ".loopx/registry.json"
    real_read = registry_codec._read_document
    failed = False

    def fail_publication_readback(path: Path):
        nonlocal failed
        if path == registry and not failed:
            failed = True
            raise OSError("interrupted target registration readback")
        return real_read(path)

    monkeypatch.setattr(registry_codec, "_read_document", fail_publication_readback)

    def stage():
        return stage_prepared_goal_transfer(source_preparation=prepared, source_context=snapshot["source_context"],
            archive=Path(prepared["archive"]), project=project, runtime_root=runtime, provider=source["provider"])

    with pytest.raises(registry_codec.ProjectRegistryRestoreError) as failure:
        stage()
    assert failed and "prepared for transfer" in str(failure.value.__cause__)
    # The native prepared owner, not an earlier Python preimage, determines
    # which registration may remain. Its stopped route is recoverable in place.
    retained = registry.read_bytes()
    assert registry_codec.load_project_registry(registry)["goals"][0]["activation"]["state"] == "stopped"
    fence = legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="report")
    retained_fence = fence.read_bytes()
    restart_effect_runtime(wait_for_exit=True)
    recovered = stage()
    assert recovered["registry_readback_verified"] is True
    assert recovered["target_is_active"] is False and recovered["execution_authority_granted"] is False
    assert registry.read_bytes() == retained and fence.read_bytes() == retained_fence
    with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
        FirstPartyHostGoalAdmission.for_plan(registry_path=registry, goal_id="report", planned_goal_ref=None).require_current()
    assert cancel(source)["readback"]["original_writer_fence_restored"] is True
    lease = source["acquire"]("continue-after-target-registration-failure")["lease"]
    source["release"](lease)


def test_failed_outbound_publication_can_resume_the_original_source(source: dict, tmp_path: Path) -> None:
    registry, runtime = source["registry"], source["runtime"]
    snapshot = read_goal_transfer_context(registry_path=registry, goal_id="report")
    context = snapshot["source_context"]
    preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a")
    assert preview["ready_for_authority_preparation"] is True
    recipient = create_planned_transfer_recipient(destination=tmp_path / "recipient")
    occupied = tmp_path / "occupied-export"
    occupied.mkdir()
    (occupied / "later-user-file.txt").write_text("Keep this unrelated file.")
    before_registry = registry.read_bytes()
    before_index = Path(source["debit"]["index_path"]).read_bytes()
    before_task = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    with pytest.raises(EffectRuntimeRemoteError):
        export_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a",
            expected_preview_sha256=preview["preview_sha256"], recipient=recipient, destination=occupied, materials=[])
    directory = operation_directory(source, "move-a")
    prepared_bytes = (directory / "receipt.json").read_bytes()
    prepared = json.loads(prepared_bytes)
    assert prepared["authority_write_fenced"] is True
    assert prepared["execution_authority_granted"] is False
    assert "prepared for transfer" in source["acquire"]("stranded", expected_code=1)["error"]
    with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
        source["admission"].require_current()

    cancelled = cancel(source)
    assert cancelled["decision"]["preparation_cancelled"] is True
    assert cancelled["decision"]["execution_authority_granted"] is False
    assert cancelled["readback"]["original_writer_fence_restored"] is True
    assert json.loads(source["fence"].read_bytes()) == source["original_fence"]
    assert registry.read_bytes() == before_registry
    assert Path(source["debit"]["index_path"]).read_bytes() == before_index
    assert (directory / "receipt.json").read_bytes() == prepared_bytes
    assert source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"] == before_task
    assert source["cli"]("task-lease", "inspect", "--goal-id", "report", "--todo-id", source["todo"])["provider_revision"] == preview["provider_revision"]
    # A cached direct execution request must also fail, independently of the
    # convenience wrapper's fresh preview. The old source is otherwise exactly
    # unchanged here, so unrelated drift cannot mask an accidental reprepare.
    with pytest.raises(EffectRuntimeRemoteError, match="cancelled"):
        effect_runtime_result("coordination.planned_transfer.prepare_authority", {
            "runtime_root": str(runtime), "goal_id": "report", "operation_id": "move-a",
            "expected_provider_revision": preview["provider_revision"],
            "expected_writer_fence_sha256": preview["original_writer_fence_sha256"],
            "registered_source_preview_sha256": preview["preview_sha256"], "execute": True, **snapshot,
        }, timeout=60, retry_safe=False)
    assert (occupied / "later-user-file.txt").read_text() == "Keep this unrelated file."
    source["admission"].require_current()
    assert source["admission"].accept_result(lambda: "original-owner-result") == "original-owner-result"
    _store_codex_cli_session(runtime, lineage=source["lineage"], session_id="continued-context")
    assert json.loads(_session_path(runtime, source["lineage"]).read_bytes())["session_id"] == "continued-context"
    lease = source["acquire"]("continue-original")["lease"]
    updated = source["cli"]("todo", "update", "--goal-id", "report", "--todo-id", source["todo"],
        "--agent-id", "agent-a", "--task-lease-idempotency-key", lease["idempotency_key"],
        "--task-lease-expected-version", str(lease["version"]), "--note", "Continue the original unfinished work.",
        "--update-operation-id", "continue-original-note", "--execute")
    assert updated["ok"] is True
    original = source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"]
    assert original["status"] == "open" and original["claimed_by"] == "agent-a"
    assert original["note"] == "Continue the original unfinished work."
    assert lease["lease_epoch"] > 1
    source["release"](lease)
    replay = replay_quota_spend_by_effect_ref(runtime, goal_id="report", effect_ref=source["debit"]["effect_id"],
        agent_id="agent-a", read_only=True, registry_path=registry)
    assert replay["replay_found"] is True and replay["appended"] is False
    with project_registry_transaction(registry, operation="later-source-name") as tx:
        changed = tx.payload_copy()
        changed["goals"][0]["display_name"] = "Later source work"
        tx.commit(changed)
    later_registry = registry.read_bytes()
    restart_effect_runtime(wait_for_exit=True)
    assert cancel(source)["decision"] == cancelled["decision"]
    assert registry.read_bytes() == later_registry
    assert source["cli"]("todo", "list", "--goal-id", "report", "--todo-id", source["todo"])["todo"] == original
    with pytest.raises(EffectRuntimeRemoteError, match="cancelled"):
        preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a")

    # A stale carrier can still be inspected/staged, but remains an inert read
    # model. Cancellation never issues destination execution authority.
    target_project, target_runtime = tmp_path / "target-project", tmp_path / "target-runtime"
    staged = stage_prepared_goal_transfer(source_preparation=prepared, source_context=context,
        archive=Path(prepared["archive"]), project=target_project, runtime_root=target_runtime, provider=source["provider"])
    assert staged["target_is_active"] is False and staged["execution_authority_granted"] is False
    target_registry = target_project / ".loopx/registry.json"
    with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
        FirstPartyHostGoalAdmission.for_plan(registry_path=target_registry, goal_id="report", planned_goal_ref=None).require_current()

    next_preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-b")
    prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-b",
        expected_preview_sha256=next_preview["preview_sha256"])
    later_fence = source["fence"].read_bytes()
    historical = cancel(source)
    assert historical["decision"] == cancelled["decision"]
    assert historical["readback"]["original_writer_fence_restored"] is False
    assert source["fence"].read_bytes() == later_fence and registry.read_bytes() == later_registry


def test_process_loss_after_cancellation_decision_recovers_the_original_fence(source: dict) -> None:
    # A real source Host and terminal journal are retained, rather than using a
    # copied PID or a caller-supplied source_closed flag.
    from loopx.control_plane.turn_driver.host_process_transport import run_host_process
    from loopx.control_plane.turn_driver.journal_store import write_turn_journal_checkpoint
    from loopx.control_plane.turn_driver.turn_host_process import turn_host_process_scope
    from tests.control_plane.test_planned_transfer_source_boundary import _original_turn

    runtime, registry = source["runtime"], source["registry"]
    journal = _original_turn("report", source["todo"])
    journal["host_attempt_count"] = 1
    journal_path = runtime / "goals/report/turns" / ("a" * 64 + ".json")
    write_turn_journal_checkpoint(journal_path, journal)
    with turn_host_process_scope(runtime, goal_id="report", turn_key=journal["turn_key"], attempt=1):
        assert run_host_process([sys.executable, "-c", "pass"], project=source["project"],
            input_text="", timeout_seconds=10)["outcome"] == "exited"
    phases = ["host_execute", "typed_result", "validation", "durable_writeback", "quota_spend", "scheduler_apply", "scheduler_ack"]
    for count in (2, 3, 4, 5, 7):
        journal["completed_phases"] = phases[:count]
        if count == 7:
            journal["status"] = "committed"
        write_turn_journal_checkpoint(journal_path, journal)
    preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a")
    assert preview["turn_history"]["closed"] is True and preview["turn_history"]["turn_hosts_closed"] is True
    prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a",
        expected_preview_sha256=preview["preview_sha256"])
    prepared_fence, original_journal = source["fence"].read_bytes(), journal_path.read_bytes()
    decision_path = operation_directory(source, "move-a") / "cancelled.json"
    module = Path(__file__).resolve().parents[2] / "loopx/control_plane/coordination/planned_transfer_authority.ts"
    script = f"import {{cancelSourcePreparation}} from {json.dumps(module.as_uri())};\n" + "\n".join([
        "let input=''; for await(const chunk of process.stdin) input+=chunk;",
        "console.log(JSON.stringify(await cancelSourcePreparation(JSON.parse(input))));",
    ])
    # This process is directly owned by the test. Kill its Popen handle only
    # after the durable decision and before the blocked fence CAS; never signal
    # a discovered runtime or an arbitrary PID.
    with exclusive_cross_runtime_file_lock(source["fence"], operation="fixture_hold_original_fence"):
        process = subprocess.Popen(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "LOOPX_EFFECT_RUNTIME_PYTHON": sys.executable})
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps({"runtime_root": str(runtime), "goal_id": "report", "operation_id": "move-a", "execute": True}).encode())
            process.stdin.close()
            deadline = time.monotonic() + 10
            while not decision_path.is_file() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert decision_path.is_file(), "cancellation did not durably decide before source restoration"
            decision = decision_path.read_bytes()
            assert source["fence"].read_bytes() == prepared_fence
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            assert process.stdout is not None and process.stderr is not None
            process.stdout.close()
            process.stderr.close()
    assert source["fence"].read_bytes() == prepared_fence
    restart_effect_runtime(wait_for_exit=True)
    # A retained decision is not permission to ignore changes made before its
    # interrupted CAS finishes. Alter only this synthetic fixture, prove the
    # refusal, then put back its original bytes for the recovery case.
    original_registry = registry.read_bytes()
    changed = json.loads(original_registry)
    changed["goals"][0]["display_name"] = "Changed during interrupted cancellation"
    registry.write_text(json.dumps(changed))
    with pytest.raises(EffectRuntimeRemoteError, match="intent differs"):
        cancel(source)
    assert source["fence"].read_bytes() == prepared_fence and decision_path.read_bytes() == decision
    registry.write_bytes(original_registry)
    recovered = cancel(source)
    assert recovered["readback"]["original_writer_fence_restored"] is True
    assert decision_path.read_bytes() == decision and journal_path.read_bytes() == original_journal
    assert json.loads(source["fence"].read_bytes()) == source["original_fence"]
    assert cancel(source)["decision"] == recovered["decision"]
    source["admission"].require_current()
    source["release"](source["acquire"]("continue-after-recovery")["lease"])


def test_interrupted_archive_without_receipt_can_be_cancelled(source: dict) -> None:
    preview = preview_registered_goal_transfer(registry_path=source["registry"], goal_id="report", operation_id="move-a")
    directory = operation_directory(source, "move-a")
    occupied = directory / "authority.ndjson"
    occupied.mkdir(parents=True)
    (occupied / "unrelated.txt").write_text("Preserve the occupied path.")
    with pytest.raises(EffectRuntimeRemoteError):
        prepare_registered_goal_transfer(registry_path=source["registry"], goal_id="report", operation_id="move-a",
            expected_preview_sha256=preview["preview_sha256"])
    assert not (directory / "receipt.json").exists()
    assert "prepared for transfer" in source["acquire"]("blocked-without-receipt", expected_code=1)["error"]
    result = cancel(source)
    assert result["decision"]["source_receipt_sha256"] is None
    assert result["readback"]["original_writer_fence_restored"] is True
    assert (occupied / "unrelated.txt").read_text() == "Preserve the occupied path."
    assert not (directory / "receipt.json").exists()
    assert cancel(source)["decision"] == result["decision"]
    source["release"](source["acquire"]("continue-after-archive-failure")["lease"])


def test_cancellation_refuses_changed_context_receipt_fence_and_history(source: dict) -> None:
    registry = source["registry"]
    original = source["fence"].read_bytes()
    with pytest.raises(EffectRuntimeRemoteError, match="retained original registered"):
        cancel(source, "unknown-move")
    assert source["fence"].read_bytes() == original
    preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a")
    prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-a",
        expected_preview_sha256=preview["preview_sha256"])
    directory = operation_directory(source, "move-a")
    frozen_fence, frozen_registry = source["fence"].read_bytes(), registry.read_bytes()
    receipt_path = directory / "receipt.json"
    frozen_receipt = receipt_path.read_bytes()

    # Controlled corruption of this disposable fixture cannot be translated
    # into reopening. Each refused call leaves the observed bytes untouched.
    for path, changed, message in [
        (registry, {**json.loads(frozen_registry), "goals": []}, "codec readback is unavailable"),
        (receipt_path, {**json.loads(frozen_receipt), "execution_authority_granted": True}, "preparation-only receipt"),
        (receipt_path, {**json.loads(frozen_receipt), "runtime_history": {}}, "receipt differs from its original source identity"),
        (source["fence"], {**json.loads(frozen_fence), "operation_id": "other-move"}, "not owned by this prepared operation"),
    ]:
        before = path.read_bytes()
        bad = json.dumps(changed).encode()
        path.write_bytes(bad)
        with pytest.raises(EffectRuntimeRemoteError, match=message):
            cancel(source)
        assert path.read_bytes() == bad and not (directory / "cancelled.json").exists()
        path.write_bytes(before)
    quota = Path(source["debit"]["index_path"])
    quota_bytes = quota.read_bytes()
    quota.write_bytes(quota_bytes + b"\n")
    with pytest.raises(EffectRuntimeRemoteError, match="settlement history changed"):
        cancel(source)
    assert quota.read_bytes() == quota_bytes + b"\n" and source["fence"].read_bytes() == frozen_fence
    quota.write_bytes(quota_bytes)
    linked = directory / "cancelled.json"
    linked.symlink_to(receipt_path)
    with pytest.raises(EffectRuntimeRemoteError, match="linked ancestor"):
        cancel(source)
    assert linked.is_symlink() and source["fence"].read_bytes() == frozen_fence
    linked.unlink()
    assert cancel(source)["readback"]["original_writer_fence_restored"] is True

    # Simulate an unsupported old writer through the actual provider, outside
    # its ordinary fenced business facade. Cancellation must not hide that new
    # native history by restoring the prior execution boundary.
    next_preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-b")
    prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="move-b",
        expected_preview_sha256=next_preview["preview_sha256"])
    module = Path(__file__).resolve().parents[2] / "loopx/control_plane/coordination/local_authority_provider.ts"
    script = f"import {{openLocalAuthorityStoreHandle}} from {json.dumps(module.as_uri())};\n" + "\n".join([
        "let input=''; for await(const chunk of process.stdin) input+=chunk;",
        "const {root}=JSON.parse(input); const {store}=await openLocalAuthorityStoreHandle(root,'report',{}, {existingOnly:true});",
        "const head=await store.loadAuthority(); if(head.status!=='loaded')throw Error('source missing');",
        "const result=await store.commitAuthority({expected_provider_revision:head.provider_revision,operation_id:'unsupported-old-writer',",
        "next_projection:head.head,events:[],receipts:[{result:'later native fact'}]});",
        "if(result.status!=='applied')throw Error(JSON.stringify(result)); console.log(JSON.stringify(result));",
    ])
    result = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
        input=json.dumps({"root": str(source["runtime"])}), capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    later_revision = json.loads(result.stdout)["provider_revision"]
    later_fence = source["fence"].read_bytes()
    with pytest.raises(EffectRuntimeRemoteError, match="Prepared source authority changed"):
        cancel(source, "move-b")
    assert source["fence"].read_bytes() == later_fence
    assert not (operation_directory(source, "move-b") / "cancelled.json").exists()
    assert source["cli"]("task-lease", "inspect", "--goal-id", "report", "--todo-id", source["todo"])["provider_revision"] == later_revision
