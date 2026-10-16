"""Migration must compose admission closure and drain, not reinterpret Goal stop.

These are real CLI/provider checks of the existing owners and the source
authority preparation under development. They do not implement destination
admission or qualify Host drain or a complete Agent transfer.
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
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


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
        request = {"runtime_root": str(runtime), "goal_id": "report", "operation_id": "prepared-move",
            "expected_provider_revision": released["provider_revision"],
            "expected_writer_fence_sha256": hashlib.sha256(canonical_bytes(original_fence)).hexdigest(),
            "execute": True}
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
            assert inspect()["provider_revision"] == released["provider_revision"]
            assert cli("todo", "list", "--goal-id", "report", "--todo-id", todo)["todo"]["note"] == retained["note"]
    finally:
        restart_effect_runtime(wait_for_exit=True)
