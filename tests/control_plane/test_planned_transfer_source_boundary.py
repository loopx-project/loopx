"""Migration must compose admission closure and drain, not reinterpret Goal stop.

These are real CLI/provider checks of the source preparation and registered,
stopped destination staging under development. Original Task/history readback
must work without fresh creation or execution grants. Destination admission,
Host drain and the complete packaged Agent journey remain separate acceptance.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRemoteError, effect_runtime_result, restart_effect_runtime
from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.goals.planned_transfer_io import (
    create_planned_transfer_recipient, export_registered_goal_transfer,
    prepare_registered_goal_transfer, preview_registered_goal_transfer,
    preview_protected_goal_transfer, preview_protected_goal_transfer_target,
    read_goal_transfer_context, stage_protected_goal_transfer,
    inspect_staged_goal_transfer_history,
)
from loopx.control_plane.projects.registry_codec import load_project_registry, project_registry_transaction
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_project_registry_codec import _strict
from loopx.global_registry import sync_project_registry_to_global
from loopx.paths import global_registry_path
from loopx.control_plane.turn_driver.journal_store import write_turn_journal_checkpoint
from loopx.control_plane.quota.spend_commit import record_quota_slot_spend_from_preview, replay_quota_spend_by_effect_ref
from tests.control_plane.test_quota_spend_commit_runtime import _preview as quota_preview
from loopx.control_plane.turn_driver.turn_host_process import turn_host_process_scope
from loopx.control_plane.turn_driver.host_process_transport import run_host_process
from loopx.control_plane.turn_driver.codex_sessions import _session_path, _store_codex_cli_session, _discard_codex_cli_session
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission


def _original_turn(goal: str, todo: str) -> dict:
    key = "sha256:" + "a" * 64
    return {"schema_version": "loopx_turn_journal_v0", "goal_id": goal, "turn_key": key,
        "status": "in_progress", "completed_phases": [], "plan": {
            "turn_envelope": {"goal_id": goal, "agent_id": "agent-a", "action": {"selected_todo": {"todo_id": todo}}},
            "transaction": {"turn_key": key, "settlement_plan": {"schema_version": "quota_settlement_plan_v1",
                "identity": {"schema_version": "quota_settlement_identity_v0", "goal_id": goal, "agent_id": "agent-a",
                    "todo_id": todo, "turn_instance_id": key, "effect_id": f"{goal}:agent-a:{todo}:{key}"}}}}}


@contextmanager
def _running_turn_host(runtime: Path, project: Path, journal: dict):
    """An owned real group, including a child that ignores graceful stop."""
    ready = threading.Event()
    release = project / "finish-original-host"
    command = "\n".join([
        "import subprocess,sys,time;from pathlib import Path",
        "subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)",
        "print('ready',flush=True)",
        "while not Path(sys.argv[1]).exists(): time.sleep(.01)",
    ])

    def launch():
        with turn_host_process_scope(runtime, goal_id="report", turn_key=journal["turn_key"], attempt=1):
            return run_host_process([sys.executable, "-c", command, str(release)], project=project,
                input_text="", timeout_seconds=30, on_stdout=lambda _text: ready.set())

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(launch)
        try:
            assert ready.wait(10), "the original real Host did not start"
            yield
        finally:
            release.touch()
            observed = future.result(timeout=15)
            assert observed["outcome"] == "exited"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_stop_withdrawal_and_exact_release_have_distinct_authority(
    tmp_path: Path, monkeypatch, provider: str,
) -> None:
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
        "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1",
                         "new_goal_provider": provider, "canonical_creation": True,
                         "new_goal_handoff_mode": "hard_lease"}}}))

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

    def inspect() -> dict:
        return cli("task-lease", "inspect", "--goal-id", "report", "--todo-id", todo)

    def update(operation: str, note: str, *, expected_code: int = 0) -> dict:
        return cli("todo", "update", "--goal-id", "report", "--todo-id", todo,
            "--agent-id", "agent-a", "--task-lease-idempotency-key", lease["idempotency_key"],
            "--task-lease-expected-version", str(lease["version"]), "--note", note,
            "--update-operation-id", operation, "--execute", expected_code=expected_code)

    hidden_source: list[tuple[Path, Path]] = []
    try:
        cli("bootstrap", "--project", str(project), "--goal-id", "report",
            "--objective", "Produce report.txt from input.txt", "--no-global-sync")
        cli("configure-goal", "--goal-id", "report", "--registered-agent", "agent-a",
            "--write-scope", "report.txt", "--execute")
        accepted = cli("todo", "add", "--goal-id", "report", "--role", "user", "--task-class", "user_action",
            "--text", "Keep the input and original reporting constraints", "--operation-id", "accepted-context")["todo_id"]
        cli("todo", "complete", "--goal-id", "report", "--role", "user", "--todo-id", accepted,
            "--agent-id", "agent-a", "--note", "Accepted original context", "--evidence", "input.txt")
        cli("todo", "archive-completed", "--goal-id", "report", "--role", "user", "--max-active-done", "0", "--execute")
        created = cli("todo", "add", "--goal-id", "report", "--role", "agent",
            "--claimed-by", "agent-a", "--text", "Write the input sum to report.txt",
            "--task-repository", "git:example.org/fixtures/agent-transfer",
            "--required-write-scope", "report.txt", "--operation-id", "pending-report")
        todo = created["todo_id"]
        acquired = cli("task-lease", "acquire", "--goal-id", "report", "--todo-id", todo,
            "--owner", "agent-a", "--idempotency-key", "source-work", "--write-scope", "report.txt",
            "--write-worktree", str(worktree), "--ttl-seconds", "7200")
        lease = acquired["lease"]
        assert acquired["source_authority"] == f"{provider}_v0"
        assert inspect()["active"] is True
        source_preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move")
        assert source_preview["original_registration_verified"] is True
        assert source_preview["ready_for_authority_preparation"] is False
        assert source_preview["retained_leases"] == [lease]
        assert source_preview["host_drain_qualified"] is False and source_preview["execution_authority_granted"] is False
        assert inspect()["lease"] == lease

        # The accepted stop operation controls automatic advancement. It must
        # not silently revoke an admitted executor's lease or discard its work.
        before_preview = registry.read_bytes()
        preview = cli("goal-lifecycle", "--goal-id", "report", "--operation", "stop")
        assert registry.read_bytes() == before_preview
        stopped = cli("goal-lifecycle", "--goal-id", "report", "--operation", "stop",
            "--actor-kind", "owner", "--expected-state-fingerprint",
            preview["observed_state_fingerprint"], "--execute")
        assert stopped["after_state"] == "stopped" and stopped["readback"]["verified"] is True
        assert inspect()["lease"] == lease
        assert inspect()["active"] is True
        assert update("finish-source-note", "Preserve the source continuation.")["ok"] is True

        # Withdrawn membership blocks fresh writes and acquisitions. The native
        # effective=false projection is not a release of the stored lease tuple.
        cli("configure-goal", "--goal-id", "report", "--clear-registered-agents", "--execute")
        withdrawn = inspect()
        assert withdrawn["active"] is False and withdrawn["lease"] == lease
        assert withdrawn["executor_constraint"]["reason"] == "owner_not_registered"
        for cold in (False, True):
            if cold:
                restart_effect_runtime(wait_for_exit=True)
            refusal = update(f"withdrawn-write-{cold}", "Must not replace the source note.", expected_code=1)
            assert refusal["reason_code"] == "actor_not_registered"
            refused = cli("task-lease", "acquire", "--goal-id", "report", "--todo-id", todo,
                "--owner", "agent-a", "--idempotency-key", f"withdrawn-acquire-{cold}",
                "--write-scope", "report.txt", "--write-worktree", str(worktree), expected_code=1)
            assert "owner_not_registered" in refused["error"]
            assert inspect()["provider_revision"] == withdrawn["provider_revision"]
        retained = cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]
        assert retained["note"] == "Preserve the source continuation."
        assert retained["claimed_by"] == "agent-a" and retained["status"] == "open"

        # Membership restoration alone restores eligibility for the unreleased
        # tuple. A transfer cannot use temporary withdrawal as its final fence.
        cli("configure-goal", "--goal-id", "report", "--registered-agent", "agent-a", "--execute")
        assert inspect()["active"] is True and inspect()["lease"] == lease
        cli("configure-goal", "--goal-id", "report", "--clear-registered-agents", "--execute")

        # Exact cleanup remains possible after withdrawal. Re-registering an
        # actor for recovery must not turn the released original proof live.
        cli("task-lease", "release", "--goal-id", "report", "--todo-id", todo, "--owner", "agent-a",
            "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]))
        assert inspect()["lease"]["status"] == "released"
        cli("configure-goal", "--goal-id", "report", "--registered-agent", "agent-a", "--execute")
        released = inspect()
        assert released["active"] is False and released["lease"]["status"] == "released"
        assert update("released-proof-write", "Must not revive released work.", expected_code=1)["ok"] is False
        assert inspect()["provider_revision"] == released["provider_revision"]
        assert cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]["note"] == retained["note"]

        # Establish the actual counterfactual: with registration retained, a
        # new ordinary execution can still be admitted before preparation.
        fresh_before = cli("task-lease", "acquire", "--goal-id", "report", "--todo-id", todo,
            "--owner", "agent-a", "--idempotency-key", "fresh-before-preparation",
            "--write-scope", "report.txt", "--write-worktree", str(worktree))["lease"]
        cli("task-lease", "release", "--goal-id", "report", "--todo-id", todo,
            "--owner", "agent-a", "--idempotency-key", fresh_before["idempotency_key"],
            "--expected-version", str(fresh_before["version"]))
        released = inspect()

        # Unlike temporary membership withdrawal, the authority preparation
        # survives retained registration, fresh execution keys and a cold Host.
        # It only fences the authority: no source Host drain or target admission
        # is inferred from this real production-entrypoint check.
        original_fence = json.loads(legacy_coordination_writer_fence_path(
            runtime_root=runtime, goal_id="report").read_text())
        if provider == "sqlite":
            # The existing registry codec owns this ordinary strict v1 fixture.
            # Goal transfer must preserve its semantics without adopting the
            # separate lifecycle-only v2 source-session profile.
            registry.write_text(json.dumps(_strict(load_project_registry(registry))))
        # The product caller can start at the normal shared App/CLI registry.
        # It resolves the original project through the existing route owner;
        # stale/sanitized global fields and the global runtime are not authority.
        sync_project_registry_to_global(registry_path=registry, runtime_root_override=str(runtime), goal_id="report")
        global_registry = global_registry_path(runtime)
        global_bytes = global_registry.read_bytes()
        projected = load_project_registry(global_registry)
        projected["common_runtime_root"] = str(tmp_path / "unrelated-shared-runtime")
        projected["goals"][0]["display_name"] = "Stale shared read model"
        global_registry.write_text(json.dumps(projected))
        assert read_goal_transfer_context(registry_path=global_registry, goal_id="report") == read_goal_transfer_context(registry_path=registry, goal_id="report")
        preview = preview_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move")
        assert preview["ready_for_authority_preparation"] is True
        assert preview["provider_revision"] == released["provider_revision"]
        assert preview["original_writer_fence_sha256"] == hashlib.sha256(canonical_bytes(original_fence)).hexdigest()

        # Same alias, same runtime and copied Goal metadata do not bind another
        # registration to the original creation receipt. Source fencing must not
        # occur on either this refusal or a stale reviewed source context.
        copied_registry = tmp_path / "copied-registration.json"
        copied_registry.write_bytes(registry.read_bytes())
        with pytest.raises(EffectRuntimeRemoteError, match="original registered creation"):
            preview_registered_goal_transfer(registry_path=copied_registry, goal_id="report", operation_id="prepared-move")
        with project_registry_transaction(registry, operation="update_source_before_transfer") as tx:
            changed = tx.payload_copy()
            changed["goals"][0]["display_name"] = "Current original Goal"
            tx.commit(changed)
        with pytest.raises(EffectRuntimeRemoteError, match="preview changed"):
            prepare_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move",
                expected_preview_sha256=preview["preview_sha256"])
        assert json.loads(legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="report").read_text()) == original_fence
        assert inspect()["provider_revision"] == released["provider_revision"]
        preview = preview_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move")
        assert preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move") == preview

        # The real journal/checkpoint and quota facades serialize against the
        # preparation owner. Terminal metadata is not physical Host drain. An
        # actual supervised synthetic command supplies that separate evidence;
        # no model or external service is invoked.
        journal = _original_turn("report", todo)
        journal["host_attempt_count"] = 1
        journal_path = runtime / "goals/report/turns" / ("a" * 64 + ".json")
        write_turn_journal_checkpoint(journal_path, journal)
        active_turn = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move")
        assert active_turn["ready_for_authority_preparation"] is False
        assert active_turn["turn_history"]["closed"] is False
        with pytest.raises(EffectRuntimeRemoteError, match="Turn effects are not closed"):
            prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move",
                expected_preview_sha256=active_turn["preview_sha256"])
        with _running_turn_host(runtime, project, journal):
            phases = ["host_execute", "typed_result", "validation", "durable_writeback", "quota_spend", "scheduler_apply", "scheduler_ack"]
            for count in (2, 3, 4, 5, 7):
                journal["completed_phases"] = phases[:count]
                if count == 7:
                    journal["status"] = "committed"
                write_turn_journal_checkpoint(journal_path, journal)
            # Deliberately contradictory cross-owner timing: even a terminal
            # journal must not make this actually running group disappear.
            terminal_preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move")
            assert terminal_preview["turn_history"]["closed"] is True
            assert terminal_preview["turn_history"]["turn_hosts_closed"] is False
            assert terminal_preview["turn_history"]["hosts"][0]["drain"] == "draining"
            with pytest.raises(EffectRuntimeRemoteError, match="Hosts are not proven drained"):
                prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move",
                    expected_preview_sha256=terminal_preview["preview_sha256"])
        with pytest.raises(EffectRuntimeRemoteError, match="preview changed"):
            prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move",
                expected_preview_sha256=preview["preview_sha256"])
        preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move")
        debit_preview = quota_preview(goal_id="report", agent_id="agent-a", effect_ref="original-boundary-effect#quota_spend")
        debit = record_quota_slot_spend_from_preview(debit_preview, {"runtime_root": str(runtime)},
            goal_id="report", execute=True, registry_path=registry)
        assert debit["appended"] is True and debit["slots"] == 1
        with pytest.raises(EffectRuntimeRemoteError, match="preview changed"):
            prepare_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move",
                expected_preview_sha256=preview["preview_sha256"])
        preview = preview_registered_goal_transfer(registry_path=registry, goal_id="report", operation_id="prepared-move")
        assert preview["turn_history"]["closed"] is True and preview["quota_history"]["closed"] is True
        assert len(preview["quota_history"]["receipts"]) == 1
        assert preview["turn_history"]["turn_hosts_closed"] is True
        lineage = {"goal_id": "report", "agent_id": "agent-a", "todo_id": todo}
        warm_admission = FirstPartyHostGoalAdmission.for_plan(registry_path=registry, goal_id="report", planned_goal_ref=None)
        _store_codex_cli_session(runtime, lineage=lineage, session_id="original-source-context")
        session_path = _session_path(runtime, lineage)
        original_session = session_path.read_bytes()
        assert json.loads(legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="report").read_text()) == original_fence
        request = {"runtime_root": str(runtime), "goal_id": "report", "operation_id": "prepared-move",
            "expected_provider_revision": released["provider_revision"],
            "expected_writer_fence_sha256": hashlib.sha256(canonical_bytes(original_fence)).hexdigest(),
            "registered_source_preview_sha256": preview["preview_sha256"],
            "execute": True, **read_goal_transfer_context(registry_path=global_registry, goal_id="report")}
        recipient_root = tmp_path / "recipient"
        recipient = create_planned_transfer_recipient(destination=recipient_root)
        bundle = tmp_path / "source-export"
        material_hash = hashlib.sha256((project / "input.txt").read_bytes()).hexdigest()
        linked_input = project / "linked-input"
        linked_input.symlink_to(project / "input.txt")
        for invalid in [[(project / "input.txt", "0" * 64)], [(registry, material_hash)],
                        [(machine, material_hash)], [(project / ".git/config", material_hash)],
                        [(linked_input, material_hash)], [(project / "input.txt", material_hash)] * 2]:
            with pytest.raises(EffectRuntimeRemoteError, match="material|linked ancestor"):
                export_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move",
                    expected_preview_sha256=preview["preview_sha256"], recipient=recipient, destination=bundle, materials=invalid)
            assert json.loads(legacy_coordination_writer_fence_path(runtime_root=runtime, goal_id="report").read_text()) == original_fence
            assert not bundle.exists()
        prepared = prepare_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move",
            expected_preview_sha256=preview["preview_sha256"])
        assert prepared["authority_write_fenced"] is True
        assert prepared["original_registration_verified"] is True
        assert prepared["execution_authority_granted"] is False
        assert prepared["host_drain_qualified"] is False
        frozen_registry, frozen_journal = registry.read_bytes(), journal_path.read_bytes()
        frozen_index = Path(debit["index_path"]).read_bytes()
        for cold in (False, True):
            if cold:
                restart_effect_runtime(wait_for_exit=True)
            with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
                write_turn_journal_checkpoint(journal_path, journal)
            with pytest.raises(ValueError, match="prepared for transfer"):
                record_quota_slot_spend_from_preview(debit_preview, {"runtime_root": str(runtime)},
                    goal_id="report", execute=True, registry_path=registry)
            readback = replay_quota_spend_by_effect_ref(runtime, goal_id="report",
                effect_ref=debit["effect_id"], agent_id="agent-a", read_only=True, registry_path=registry)
            assert readback["replay_found"] is True and readback["appended"] is False
            rejected_metadata = cli("configure-goal", "--goal-id", "report", "--registered-agent", "agent-b", "--execute", expected_code=1)
            assert "prepared for transfer" in rejected_metadata["error"]
            action = cli("goal-lifecycle", "--goal-id", "report", "--operation", "resume")
            refused_resume = cli("goal-lifecycle", "--goal-id", "report", "--operation", "resume",
                "--actor-kind", "owner", "--expected-state-fingerprint", action["observed_state_fingerprint"],
                "--execute", expected_code=1)
            assert "prepared for transfer" in refused_resume["error"]
            assert registry.read_bytes() == frozen_registry and journal_path.read_bytes() == frozen_journal
            for callback in (
                warm_admission.require_current,
                lambda: warm_admission.accept_result(lambda: pytest.fail("prepared source result must not be accepted")),
                lambda: _store_codex_cli_session(runtime, lineage=lineage, session_id="late-source-context"),
                lambda: _discard_codex_cli_session(runtime, lineage=lineage),
            ):
                with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
                    callback()
            assert session_path.read_bytes() == original_session
            marker = project / f"late-host-{cold}"
            with turn_host_process_scope(runtime, goal_id="report", turn_key=journal["turn_key"], attempt=2):
                with pytest.raises(EffectRuntimeRemoteError, match="prepared for transfer"):
                    run_host_process([sys.executable, "-c", "from pathlib import Path;import sys;Path(sys.argv[1]).touch()", str(marker)],
                        project=project, input_text="", timeout_seconds=5)
            assert not marker.exists()
            assert Path(debit["index_path"]).read_bytes() == frozen_index
        for cold in (False, True):
            if cold:
                restart_effect_runtime(wait_for_exit=True)
            assert preview_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move") == preview
            assert prepare_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move",
                expected_preview_sha256=preview["preview_sha256"]) == prepared
            assert effect_runtime_result("coordination.planned_transfer.prepare_authority", request,
                timeout=45, retry_safe=False) == prepared
            rejected = update(f"prepared-write-{cold}", "Must not overwrite prepared work.", expected_code=1)
            assert rejected["reason_code"] == "canonical_authority_transfer_prepared"
            acquired = cli("task-lease", "acquire", "--goal-id", "report", "--todo-id", todo,
                "--owner", "agent-a", "--idempotency-key", f"fresh-after-preparation-{cold}",
                "--write-scope", "report.txt", "--write-worktree", str(worktree), expected_code=1)
            assert "prepared for transfer" in acquired["error"]
        # Broken global source routes fail instead of falling back to a shared
        # empty store. Restore only this synthetic read model, never source data.
        broken_projection = load_project_registry(global_registry)
        broken_projection["goals"][0]["source_registry"] = str(tmp_path / "missing-source-registry.json")
        global_registry.write_text(json.dumps(broken_projection))
        with pytest.raises(ValueError, match="source_registry is missing"):
            preview_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move")
        del broken_projection["goals"][0]["source_registry"]
        global_registry.write_text(json.dumps(broken_projection))
        with pytest.raises(EffectRuntimeRemoteError, match="global registry is a read model"):
            preview_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move")
        global_registry.write_bytes(global_bytes)

        # Populated history enters the destination through its Goal staging
        # owner, never a new-Goal bootstrap. Ordinary target reads must see the
        # original pending Task, archived work and unchanged historical receipts.
        original_goal = load_project_registry(registry)["goals"][0]
        source_state = project / original_goal["state_file"]
        context = request["source_context"]
        exported = export_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move",
            expected_preview_sha256=preview["preview_sha256"], recipient=recipient, destination=bundle,
            materials=[(project / "input.txt", material_hash)])
        assert {key: value for key, value in exported.items() if key != "protected_artifact"} == prepared
        protected = exported["protected_artifact"]
        assert protected["material_count"] == 1 and protected["privacy_certified"] is False
        assert protected["portable_migration_qualified"] is False
        assert export_registered_goal_transfer(registry_path=global_registry, goal_id="report", operation_id="prepared-move",
            expected_preview_sha256=preview["preview_sha256"], recipient=recipient, destination=bundle,
            materials=[(project / "input.txt", material_hash)]) == exported
        runtime_files = {
            row["relative_path"]: (runtime / "goals/report" / row["relative_path"]).read_bytes()
            for owner in (preview["turn_history"], preview["quota_history"]) for row in owner["files"]
        }
        assert journal_path.relative_to(runtime / "goals/report").as_posix() in runtime_files
        assert "runs/index.jsonl" in runtime_files
        assert len([name for name in runtime_files if "/quota-spend/" in name]) == 1
        incoming = tmp_path / "target-local-container"
        shutil.copytree(bundle, incoming)
        shutil.rmtree(bundle)
        source_state_bytes = source_state.read_bytes()
        source_task = cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]
        # The target has no shared source registry, archive, private export key,
        # original material or workspace. Only the encrypted directory travels.
        restart_effect_runtime(wait_for_exit=True)
        for path in (project, runtime):
            hidden = path.with_name(path.name + "-source-unavailable")
            path.rename(hidden)
            hidden_source.append((path, hidden))
        protected_input = dict(directory=incoming, private_key=recipient_root / "private-key.pem",
            expected_artifact_sha256=protected["artifact_sha256"])
        observed = preview_protected_goal_transfer(**protected_input)
        assert observed["goal_id"] == "report" and observed["display_name"] == "Current original Goal"
        assert observed["original_authority"] == prepared["authority_archive"]
        assert observed["execution_authority_granted"] is False
        assert observed["original_runtime_history"]["turn_history"] == preview["turn_history"]
        assert observed["original_runtime_history"]["quota_history"] == preview["quota_history"]
        assert observed["materials"] == [{"id": "00000002", "source_relative_path": "input.txt", "sha256": material_hash, "bytes": 6}]
        with pytest.raises(EffectRuntimeRemoteError, match="same source and destination package version"):
            effect_runtime_result("coordination.planned_transfer.preview_protected", {
                "directory": str(incoming), "private_key_path": str(recipient_root / "private-key.pem"),
                "expected_artifact_sha256": protected["artifact_sha256"], "target_package_version": "incompatible"},
                timeout=60, retry_safe=False)
        retained_only = tmp_path / "retain-only"
        legacy_stage = stage_protected_goal_transfer(**protected_input, project=retained_only / "project",
            runtime_root=retained_only / "runtime", provider="file")
        assert legacy_stage["authority_readback"]["status"] == "matched"
        assert "workspace_materials_readback_verified" not in legacy_stage
        assert not (retained_only / "project/input.txt").exists()
        for target_provider in ("file", "sqlite"):
            destination = tmp_path / f"target-{target_provider}"
            target_project, target_runtime = destination / "project", destination / "runtime"
            target_registry = target_project / ".loopx/registry.json"
            if target_provider == "sqlite":
                target_registry.parent.mkdir(parents=True)
                target_registry.write_text(json.dumps(_strict({"schema_version": "0.1", "common_runtime_root": str(target_runtime), "goals": []})))
            target_input = target_project / "input.txt"
            before_input = None
            if target_provider == "sqlite":
                target_input.write_bytes(b"6\n7\n8\n")
                before_input = target_input.stat()
            target_request = dict(**protected_input, project=target_project, runtime_root=target_runtime, provider=target_provider)
            target_review = preview_protected_goal_transfer_target(**target_request)
            assert target_review["ready_for_workspace_staging"] is True
            assert target_review["execution_authority_granted"] is False
            assert target_review["materials"][0]["presence"] == ("matched" if before_input else "missing")
            assert target_review["workspace_destinations"] == [{"id": "00000002", "relative_path": "input.txt"}]
            request = dict(**target_request, expected_workspace_preview_sha256=target_review["reviewed_preview_sha256"])
            if target_provider == "file":
                for path in ("../outside", ".loopx/registry.json", ".git/config"):
                    with pytest.raises(EffectRuntimeRemoteError, match="destination|overlaps"):
                        preview_protected_goal_transfer_target(**target_request, material_destinations={"00000002": path})
                target_project.mkdir(parents=True, exist_ok=True)
                target_input.write_bytes(b"A later destination file")
                occupied = preview_protected_goal_transfer_target(**target_request)
                assert occupied["ready_for_workspace_staging"] is False
                assert occupied["materials"][0]["presence"] == "occupied"
                with pytest.raises(EffectRuntimeRemoteError, match="preview changed"):
                    stage_protected_goal_transfer(**request)
                assert target_input.read_bytes() == b"A later destination file"
                assert not target_registry.exists()
                target_input.unlink()
                # Inject a real registry edit after native staging but before
                # codec publication. The owning CAS must preserve it, and the
                # same operation must recover without another source export.
                from loopx.control_plane.goals import planned_transfer_io
                native = planned_transfer_io.effect_runtime_result

                def concurrent_registry_edit(method, payload, **kwargs):
                    result = native(method, payload, **kwargs)
                    with project_registry_transaction(target_registry, operation="synthetic_concurrent_edit", create=dict) as tx:
                        changed = tx.payload_copy()
                        changed["retained_local_note"] = "An unrelated edit must survive transfer recovery"
                        tx.commit(changed)
                    return result

                with monkeypatch.context() as interrupted:
                    interrupted.setattr(planned_transfer_io, "effect_runtime_result", concurrent_registry_edit)
                    with pytest.raises(ValueError, match="registry changed before publication"):
                        stage_protected_goal_transfer(**request)
                concurrent = load_project_registry(target_registry)
                assert concurrent["retained_local_note"] == "An unrelated edit must survive transfer recovery"
                assert concurrent.get("goals", []) == []
                resumed = preview_protected_goal_transfer_target(**target_request)
                assert resumed["resuming_original_operation"] is True
                assert resumed["reviewed_preview_sha256"] == request["expected_workspace_preview_sha256"]
                assert resumed["preview_sha256"] != target_review["preview_sha256"]
            staged = stage_protected_goal_transfer(**request)
            assert staged["execution_authority_granted"] is False
            assert staged["target_is_active"] is False and staged["fresh_admission_required"] is True
            assert staged["authority_readback"]["status"] == "matched"
            assert staged["runtime_history_readback_verified"] is True
            history_root = Path(staged["original_runtime_history_root"])
            history_goal = history_root / "goals/report"
            assert {path.relative_to(history_goal).as_posix(): path.read_bytes()
                    for path in history_goal.rglob("*") if path.is_file()} == runtime_files
            assert not (target_runtime / "goals/report/runs/index.jsonl").exists()
            assert not (target_runtime / "goals/report/turns").exists()
            assert not list(history_root.rglob("*.hosts"))
            history_request = dict(runtime_root=target_runtime, goal_id="report", operation_id="prepared-move")
            readback = inspect_staged_goal_transfer_history(**history_request)
            assert readback["quota_history"] == preview["quota_history"]
            assert readback["turn_history"] == {key: preview["turn_history"][key] for key in ("turns", "files", "closed")}
            assert readback["historical_only"] is True
            assert readback["runtime_accounting_adopted"] is False
            assert readback["execution_authority_granted"] is False
            # Exercise the ordinary original quota replay reader over these
            # retained bytes with the original source unavailable. It must not
            # debit, repair receipts, or try to open their old source paths.
            original_replay = replay_quota_spend_by_effect_ref(history_root, goal_id="report",
                effect_ref=debit["effect_id"], agent_id="agent-a", read_only=True)
            assert original_replay["ok"] is True and original_replay["appended"] is False
            assert original_replay["idempotent_replay"] is True
            assert staged["material_readback_verified"] is True and staged["original_material_count"] == 1
            assert staged["workspace_materials_readback_verified"] is True
            assert target_input.read_bytes() == b"6\n7\n8\n"
            if before_input:
                after_input = target_input.stat()
                assert (after_input.st_ino, after_input.st_mode, after_input.st_mtime_ns) == (
                    before_input.st_ino, before_input.st_mode, before_input.st_mtime_ns)
            target_goal = load_project_registry(target_registry)["goals"][0]
            if target_provider == "sqlite":
                assert json.loads(target_registry.read_text())[0]["minimum_writer_protocol"] == "goal_instance_v1"
            if target_provider == "file":
                assert load_project_registry(target_registry)["retained_local_note"] == concurrent["retained_local_note"]
            assert target_goal["activation"]["state"] == "stopped"
            assert target_goal["coordination"]["registered_agents"] == []
            assert "creation_operation_id" not in target_goal
            assert "storage_target" not in target_goal["coordination"]
            assert json.loads(Path(staged["original_context_path"]).read_text()) == context
            assert (target_project / target_goal["state_file"]).read_bytes() == source_state_bytes
            retained_material = (target_project / target_goal["state_file"]).parent / "transfer-materials/00000002"
            assert retained_material.read_bytes() == b"6\n7\n8\n"
            manifest = json.loads(Path(staged["original_material_manifest"]).read_text())
            assert manifest == {"artifact_sha256": protected["artifact_sha256"], "materials": observed["materials"],
                "workspace_destinations": [{"id": "00000002", "relative_path": "input.txt"}]}
            assert retained_material.stat().st_ino != target_input.stat().st_ino

            def target_cli(*arguments: str, expected_code: int = 0) -> dict:
                result = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
                    "--registry", str(target_registry), "--runtime-root", str(target_runtime), *arguments],
                    cwd=target_project, capture_output=True, text=True, timeout=60)
                assert result.returncode == expected_code, result.stdout + result.stderr
                return json.loads(result.stdout)

            target_task = target_cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]
            assert target_task == source_task
            staged_registry = target_registry.read_bytes()
            refused_binding = target_cli("configure-goal", "--goal-id", "report", "--registered-agent", "agent-a", "--execute", expected_code=1)
            assert "prepared for transfer" in refused_binding["error"]
            assert target_registry.read_bytes() == staged_registry
            # The existing ordinary reconnect may read this registered Goal;
            # it must not mint a creation receipt or reset imported work to an
            # empty new Goal, even if a caller supplies creation-style inputs.
            before_reconnect = load_project_registry(target_registry)["goals"][0]
            target_cli("bootstrap", "--project", str(target_project), "--goal-id", "report",
                "--objective", "Reconnect the existing transferred Goal", "--no-global-sync")
            assert load_project_registry(target_registry)["goals"][0] == before_reconnect
            assert target_cli("task-lease", "inspect", "--goal-id", "report", "--todo-id", todo)["provider_revision"] == staged["authority_readback"]["captured_target_provider_revision"]
            assert target_cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"] == target_task
            archived = target_cli("todo", "list", "--goal-id", "report", "--role", "user", "--todo-id", accepted)["todo"]
            assert archived["archive_state"] == "archive" and archived["status"] == "done"
            assert archived["note"] == "Accepted original context"
            assert target_cli("task-lease", "inspect", "--goal-id", "report", "--todo-id", todo)["lease"] == released["lease"]
            assert target_cli("task-lease", "acquire", "--goal-id", "report", "--todo-id", todo,
                "--owner", "agent-a", "--idempotency-key", "must-not-admit-staged-history",
                "--write-scope", "report.txt", "--write-worktree", str(worktree), expected_code=1)["ok"] is False
            restart_effect_runtime(wait_for_exit=True)
            original_registry = target_registry.read_bytes()
            assert stage_protected_goal_transfer(**request) == staged
            assert target_registry.read_bytes() == original_registry
            assert target_cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"] == target_task
            current_preview = preview_protected_goal_transfer_target(**target_request)
            assert current_preview["resuming_original_operation"] is True
            assert current_preview["reviewed_preview_sha256"] == request["expected_workspace_preview_sha256"]
            retained_journal = history_goal / journal_path.relative_to(runtime / "goals/report")
            original_journal = retained_journal.read_bytes()
            retained_journal.write_bytes(original_journal + b"\n")
            with pytest.raises(EffectRuntimeRemoteError, match="runtime history changed"):
                inspect_staged_goal_transfer_history(**history_request)
            with pytest.raises(EffectRuntimeRemoteError, match="runtime history changed"):
                stage_protected_goal_transfer(**request)
            assert retained_journal.read_bytes() == original_journal + b"\n"
            assert target_registry.read_bytes() == original_registry
            retained_journal.write_bytes(original_journal)
            retained_journal.unlink()
            with pytest.raises(EffectRuntimeRemoteError, match="runtime history is missing"):
                stage_protected_goal_transfer(**request)
            assert not retained_journal.exists()
            retained_journal.write_bytes(original_journal)
            assert inspect_staged_goal_transfer_history(**history_request) == readback
            target_input.write_bytes(b"Later workspace result must survive")
            with pytest.raises(EffectRuntimeRemoteError, match="workspace material changed or is occupied"):
                stage_protected_goal_transfer(**request)
            assert target_input.read_bytes() == b"Later workspace result must survive"
            assert retained_material.read_bytes() == b"6\n7\n8\n"
            assert target_registry.read_bytes() == original_registry
            target_input.unlink()
            with pytest.raises(EffectRuntimeRemoteError, match="attempted workspace material is missing"):
                stage_protected_goal_transfer(**request)
            assert not target_input.exists()
            assert target_registry.read_bytes() == original_registry
            retained_material.write_bytes(b"Later destination work must survive")
            with pytest.raises(EffectRuntimeRemoteError, match="material is occupied or changed"):
                stage_protected_goal_transfer(**request)
            assert retained_material.read_bytes() == b"Later destination work must survive"
            assert target_registry.read_bytes() == original_registry
            retained_material.unlink()
            with pytest.raises(EffectRuntimeRemoteError, match="material is missing"):
                stage_protected_goal_transfer(**request)
            assert not retained_material.exists()
            assert target_registry.read_bytes() == original_registry
        for path, hidden in reversed(hidden_source):
            hidden.rename(path)
        hidden_source.clear()
        assert inspect()["provider_revision"] == released["provider_revision"]
        assert cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]["note"] == retained["note"]
    finally:
        restart_effect_runtime(wait_for_exit=True)
        for path, hidden in reversed(hidden_source):
            hidden.rename(path)
