"""Public mode commands must use the selected canonical snapshot after promotion."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection


@pytest.fixture(params=["file", "sqlite"])
def canonical_mode(tmp_path, monkeypatch, request):
    if request.param == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    state = tmp_path / "state.md"
    state.write_text("---\nhandoff_mode: hard_lease\n---\n\n## Agent Todo\n")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "mode-goal", "repo": str(tmp_path), "state_file": state.name,
         "coordination": {"registered_agents": ["agent-a", "agent-b"]}}]}))
    projection = build_todo_runtime_shadow_projection(goal_id="mode-goal", handoff_mode="soft_claim", todos=[])
    initialize_canonical_authority(runtime, "mode-goal", projection, state_path=state, provider=request.param)
    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--format", "json", "handoff-mode", *args, "--goal-id", "mode-goal"],
            capture_output=True, text=True, timeout=90)
        assert result.stdout, result.stderr
        return result.returncode, json.loads(result.stdout)
    return state, runtime, cli


def test_canonical_show_ignores_stale_or_missing_display(canonical_mode):
    state, _, cli = canonical_mode
    before = state.read_bytes()
    code, result = cli("show")
    assert code == 0 and result["handoff_mode"] == "soft_claim", result
    assert result["source"] == "canonical_provider"
    assert state.read_bytes() == before
    state.unlink()
    code, result = cli("show")
    assert code == 0 and result["handoff_mode"] == "soft_claim", result
    assert not state.exists()


def test_public_set_preview_replay_and_later_mode_preserve_display(canonical_mode):
    state, runtime, cli = canonical_mode
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    def read():
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True)
    original = state.read_bytes()
    before = read()
    code, preview = cli("set", "--mode", "hard_lease", "--dry-run", "--operation-id", "mode-intent")
    assert code == 0 and preview["status"] == "planned", preview
    assert read() == before
    code, changed = cli("set", "--mode", "hard_lease", "--operation-id", "mode-intent")
    assert code == 0 and changed["changed"] is True, changed
    after = read()
    assert after["handoff_mode"] == "hard_lease"
    assert after["todos"] == before["todos"] and after["leases"] == before["leases"]
    assert cli("set", "--mode", "soft_claim", "--operation-id", "later-mode")[0] == 0
    later = read()
    code, replay = cli("set", "--mode", "hard_lease", "--operation-id", "mode-intent")
    assert code == 0 and replay["status"] == "replayed" and replay["changed"] is False, replay
    assert read() == later
    code, mismatch = cli("set", "--mode", "soft_claim", "--operation-id", "mode-intent")
    assert code == 1 and mismatch["error_code"] == "coordination_operation_identity_mismatch", mismatch
    assert state.read_bytes() == original
    state.unlink()
    assert cli("set", "--mode", "hard_lease", "--operation-id", "without-display")[0] == 0
    assert cli("show")[1]["handoff_mode"] == "hard_lease"
    assert not state.exists()


def test_canonical_mode_does_not_resurrect_legacy_lease(canonical_mode):
    state, runtime, cli = canonical_mode
    from loopx.control_plane.work_items.task_lease import task_lease_dir
    lease_dir = task_lease_dir(runtime_root=runtime, goal_id="mode-goal")
    lease_dir.mkdir(parents=True, exist_ok=True)
    stale = lease_dir / "todo_legacy.json"
    stale.write_text(json.dumps({"schema_version": "task_lease_v0", "todo_id": "todo_legacy",
        "status": "active", "expires_at": "2099-01-01T00:00:00Z", "owner": "agent-a"}))
    before = stale.read_bytes(), state.read_bytes()
    code, result = cli("set", "--mode", "hard_lease")
    assert code == 0, result
    assert (stale.read_bytes(), state.read_bytes()) == before


def test_public_cli_recovers_retired_mode_receipt_without_reapplying_it(canonical_mode):
    state, runtime, cli = canonical_mode
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    # Persist a pre-retirement receipt through the real selected store. This is
    # historical contract input, not a new legacy intent accepted by today's rule.
    coordination = Path(__file__).resolve().parents[2] / "loopx/control_plane/coordination"
    script = f"""
      import {{openLocalAuthorityStore}} from {json.dumps((coordination / 'local_authority_provider.ts').as_uri())};
      import {{canonicalAuthoritySha256}} from {json.dumps((coordination / 'authority_store_codec.ts').as_uri())};
      const store = await openLocalAuthorityStore(process.argv[1], 'mode-goal');
      const before = await store.loadAuthority();
      if (before.status !== 'loaded') throw new Error(JSON.stringify(before));
      const decision = {{goal_id: 'mode-goal', operation_id: 'historical-legacy',
        previous_mode: 'soft_claim', previous_mode_valid: true, handoff_mode: 'legacy', changed: true}};
      const result = await store.commitAuthority({{operation_id: decision.operation_id,
        expected_provider_revision: before.provider_revision,
        next_projection: {{...before.head, handoff_mode: 'legacy'}},
        events: [{{schema_version: 'loopx_handoff_mode_changed_v0', ...decision}}],
        receipts: [{{schema_version: 'loopx_coordination_handoff_mode_receipt_v0',
          goal_id: decision.goal_id, operation_id: decision.operation_id,
          request_sha256: canonicalAuthoritySha256({{goal_id: decision.goal_id, requested_mode: 'legacy'}}), decision}}]}});
      if (result.status !== 'applied') throw new Error(JSON.stringify(result));
    """
    seeded = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module",
                             "-e", script, str(runtime)], capture_output=True, text=True, timeout=45)
    assert seeded.returncode == 0, seeded.stderr
    original_display = state.read_bytes()
    retry = ("set", "--mode", "legacy", "--operation-id", "historical-legacy")
    code, original = cli(*retry)
    assert code == 0 and original["status"] == "replayed" and original["changed"] is False, original
    assert original["handoff_mode"] == "legacy"
    assert cli("set", "--mode", "hard_lease", "--operation-id", "later-hard")[0] == 0
    later = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True)
    code, replay = cli(*retry)
    assert code == 0 and replay["status"] == "replayed" and replay["changed"] is False, replay
    assert replay["operation_id"] == "historical-legacy"
    assert replay["handoff_mode"] == "legacy" and replay["previous_mode"] == "soft_claim"
    assert (replay["provider_revision"], replay["cursor"]) == (original["provider_revision"], original["cursor"])
    for args in [
        ("set", "--mode", "legacy"),
        ("set", "--mode", "legacy", "--operation-id", "new-legacy"),
        (*retry, "--dry-run"),
    ]:
        code, rejected = cli(*args)
        assert code == 1 and rejected["changed"] is False, rejected
        assert rejected["error_code"] == "handoff_mode_retired", rejected
    code, rejected = cli("plan-migration", "--mode", "legacy", "--plan", str(runtime.parent / "retired-plan.json"))
    assert code == 1 and rejected["changed"] is False, rejected
    assert rejected["reason_code"] == "handoff_mode_migration_rejected", rejected
    assert not (runtime.parent / "retired-plan.json").exists()
    code, mismatch = cli("set", "--mode", "soft_claim", "--operation-id", "historical-legacy")
    assert code == 1 and mismatch["error_code"] == "coordination_operation_identity_mismatch", mismatch
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True) == later
    assert cli("show")[1]["handoff_mode"] == "hard_lease"
    assert state.read_bytes() == original_display


def test_reviewed_cli_migration_preserves_busy_assignment_and_backs_up(canonical_mode):
    state, runtime, cli = canonical_mode
    from loopx.todos import add_goal_todo
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    registry = runtime.parent / "registry.json"
    todo = add_goal_todo(registry_path=registry, goal_id="mode-goal", role="agent",
                         text="Deliver reviewed work", priority="P1", claimed_by="agent-a",
                         task_class="advancement_task", required_write_scopes=["src/**"],
                         note="Retain this metadata through migration")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True)
    code, blocked = cli("set", "--mode", "hard_lease")
    assert code == 1 and blocked["error_code"] == "handoff_mode_not_quiescent", blocked
    plan = runtime.parent / "reviewed.json"
    code, preview = cli("plan-migration", "--mode", "hard_lease", "--plan", str(plan))
    assert code == 0 and preview["status"] == "planned", preview
    assert preview["preserved_claim_count"] == 1
    assert not Path(str(plan) + ".backup.jsonl").exists()
    digest = preview["plan_sha256"]
    code, applied = cli("migrate", "--plan", str(plan), "--plan-sha256", digest, "--execute")
    assert code == 0 and applied["status"] == "applied", applied
    assert Path(applied["backup_path"]).is_file()
    assert applied["execution_authority_granted"] is False
    after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True)
    assert after["handoff_mode"] == "hard_lease"
    assert after["todos"] == before["todos"] and after["leases"] == before["leases"]
    assert after["todos"][0]["todo_id"] == todo["todo_id"]
    code, replay = cli("migrate", "--plan", str(plan), "--plan-sha256", digest, "--execute")
    assert code == 0 and replay["status"] == "replayed", replay
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True) == after

    def work(*args, expected_exit=0):
        child = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            *args, "--goal-id", "mode-goal", "--todo-id", todo["todo_id"]], capture_output=True, text=True, timeout=90)
        assert child.returncode == expected_exit, child.stdout + child.stderr
        return json.loads(child.stdout)
    # Policy migration preserves assignment, but does not authorize an execution.
    rejected = work("todo", "update", "--agent-id", "agent-a", "--text", "Continued work", expected_exit=1)
    assert rejected["error_code"] == "handoff_mode_requires_lease"
    acquired = work("task-lease", "acquire", "--owner", "agent-a", "--idempotency-key", "continued-execution",
                    "--expected-version", "0", "--ttl-seconds", "600", "--write-scope", "src/**")
    assert acquired["acquired"] is True
    proof = str(acquired["lease"]["version"])
    assert work("todo", "update", "--agent-id", "agent-a", "--text", "Continued work",
                "--task-lease-idempotency-key", "continued-execution", "--task-lease-expected-version", proof)["ok"] is True
    assert work("task-lease", "release", "--owner", "agent-a", "--idempotency-key", "continued-execution",
                "--expected-version", proof)["released"] is True
    continued = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True)
    assert continued["todos"][0]["text"] == "[P1] Continued work"
    assert continued["todos"][0]["priority"] == "P1"
    assert continued["todos"][0]["note"] == "Retain this metadata through migration"
    assert continued["todos"][0]["required_write_scopes"] == ["src/**"]


def test_reviewed_cli_registration_change_rejects_without_backup_or_policy_write(canonical_mode):
    _, runtime, cli = canonical_mode
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    plan = runtime.parent / "registration-plan.json"
    code, preview = cli("plan-migration", "--mode", "hard_lease", "--plan", str(plan))
    assert code == 0, preview
    registry = runtime.parent / "registry.json"
    value = json.loads(registry.read_text())
    value["goals"][0]["coordination"]["registered_agents"] = ["agent-a"]
    registry.write_text(json.dumps(value))
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True)
    code, rejected = cli("migrate", "--plan", str(plan), "--plan-sha256", preview["plan_sha256"], "--execute")
    assert code == 1 and rejected["status"] == "failed", rejected
    assert "registered agents changed" in rejected["reason"]
    assert not Path(str(plan) + ".backup.jsonl").exists()
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="mode-goal", include_leases=True) == before


def test_provider_failure_is_not_a_legacy_fallback(canonical_mode, monkeypatch):
    state, runtime, _ = canonical_mode
    from loopx.control_plane.todos import provider_handoff_mode
    from loopx.control_plane.coordination.local_authority import LocalCoordinationAuthorityUnavailable
    monkeypatch.setattr(provider_handoff_mode, "effect_runtime_result", lambda *_args, **_kwargs: {
        "status": "unavailable", "reason_code": "synthetic_provider_down", "reason": "Unavailable"})
    before = state.read_bytes()
    with pytest.raises(LocalCoordinationAuthorityUnavailable, match="Unavailable"):
        provider_handoff_mode.set_canonical_handoff_mode(runtime_root=runtime, goal_id="mode-goal",
            mode="hard_lease", operation_id="unavailable", dry_run=False)
    assert state.read_bytes() == before
