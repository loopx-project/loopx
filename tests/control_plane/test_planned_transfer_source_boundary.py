"""Migration must compose admission closure and drain, not reinterpret Goal stop.

These are real CLI/provider checks of the source preparation and registered,
stopped destination staging under development. Original Task/history readback
must work without fresh creation or execution grants. Destination admission,
Host drain and the complete packaged Agent journey remain separate acceptance.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import effect_runtime_result, restart_effect_runtime
from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.goals.planned_transfer_io import read_goal_transfer_context, stage_prepared_goal_transfer
from loopx.control_plane.projects.registry_codec import load_project_registry, project_registry_transaction
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_project_registry_codec import _strict


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
        request = {"runtime_root": str(runtime), "goal_id": "report", "operation_id": "prepared-move",
            "expected_provider_revision": released["provider_revision"],
            "expected_writer_fence_sha256": hashlib.sha256(canonical_bytes(original_fence)).hexdigest(),
            "execute": True, **read_goal_transfer_context(registry_path=registry, goal_id="report")}
        prepared = effect_runtime_result("coordination.planned_transfer.prepare_authority", request,
            timeout=45, retry_safe=False)
        assert prepared["authority_write_fenced"] is True
        assert prepared["execution_authority_granted"] is False
        assert prepared["host_drain_qualified"] is False
        for cold in (False, True):
            if cold:
                restart_effect_runtime(wait_for_exit=True)
            assert effect_runtime_result("coordination.planned_transfer.prepare_authority", request,
                timeout=45, retry_safe=False) == prepared
            rejected = update(f"prepared-write-{cold}", "Must not overwrite prepared work.", expected_code=1)
            assert rejected["reason_code"] == "canonical_authority_transfer_prepared"
            acquired = cli("task-lease", "acquire", "--goal-id", "report", "--todo-id", todo,
                "--owner", "agent-a", "--idempotency-key", f"fresh-after-preparation-{cold}",
                "--write-scope", "report.txt", "--write-worktree", str(worktree), expected_code=1)
            assert "prepared for transfer" in acquired["error"]

        # Populated history enters the destination through its Goal staging
        # owner, never a new-Goal bootstrap. Ordinary target reads must see the
        # original pending Task, archived work and unchanged historical receipts.
        original_goal = load_project_registry(registry)["goals"][0]
        source_state = project / original_goal["state_file"]
        context = request["source_context"]
        for target_provider in ("file", "sqlite"):
            destination = tmp_path / f"target-{target_provider}"
            target_project, target_runtime = destination / "project", destination / "runtime"
            target_registry = target_project / ".loopx/registry.json"
            if target_provider == "sqlite":
                target_registry.parent.mkdir(parents=True)
                target_registry.write_text(json.dumps(_strict({"schema_version": "0.1", "common_runtime_root": str(target_runtime), "goals": []})))
            request = dict(source_preparation=prepared, source_context=context,
                archive=Path(prepared["archive"]), project=target_project, runtime_root=target_runtime, provider=target_provider)
            if target_provider == "file":
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
                        stage_prepared_goal_transfer(**request)
                concurrent = load_project_registry(target_registry)
                assert concurrent["retained_local_note"] == "An unrelated edit must survive transfer recovery"
                assert concurrent.get("goals", []) == []
            staged = stage_prepared_goal_transfer(**request)
            assert staged["execution_authority_granted"] is False
            assert staged["target_is_active"] is False and staged["fresh_admission_required"] is True
            assert staged["authority_readback"]["status"] == "matched"
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
            assert (target_project / target_goal["state_file"]).read_bytes() == source_state.read_bytes()

            def target_cli(*arguments: str, expected_code: int = 0) -> dict:
                result = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json",
                    "--registry", str(target_registry), "--runtime-root", str(target_runtime), *arguments],
                    cwd=target_project, capture_output=True, text=True, timeout=60)
                assert result.returncode == expected_code, result.stdout + result.stderr
                return json.loads(result.stdout)

            target_task = target_cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]
            assert target_task == cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]
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
            assert stage_prepared_goal_transfer(**request) == staged
            assert target_registry.read_bytes() == original_registry
            assert target_cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"] == target_task
            assert inspect()["provider_revision"] == released["provider_revision"]
            assert cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]["note"] == retained["note"]
    finally:
        restart_effect_runtime(wait_for_exit=True)
