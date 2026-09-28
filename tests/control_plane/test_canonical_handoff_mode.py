"""Public mode commands must use the selected canonical snapshot after promotion."""
import json
import subprocess
import sys

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
        {"id": "mode-goal", "repo": str(tmp_path), "state_file": state.name}]}))
    projection = build_todo_runtime_shadow_projection(goal_id="mode-goal", handoff_mode="soft_claim", todos=[])
    initialize_canonical_authority(runtime, "mode-goal", projection, state_path=state, provider=request.param)
    def cli(*args):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--format", "json", "handoff-mode", *args, "--goal-id", "mode-goal"],
            capture_output=True, text=True, timeout=90)
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
    code, preview = cli("set", "--mode", "legacy", "--dry-run", "--operation-id", "mode-intent")
    assert code == 0 and preview["status"] == "planned", preview
    assert read() == before
    code, changed = cli("set", "--mode", "legacy", "--operation-id", "mode-intent")
    assert code == 0 and changed["changed"] is True, changed
    after = read()
    assert after["handoff_mode"] == "legacy"
    assert after["todos"] == before["todos"] and after["leases"] == before["leases"]
    assert cli("set", "--mode", "hard_lease", "--operation-id", "later-mode")[0] == 0
    later = read()
    code, replay = cli("set", "--mode", "legacy", "--operation-id", "mode-intent")
    assert code == 0 and replay["status"] == "replayed" and replay["changed"] is False, replay
    assert read() == later
    code, mismatch = cli("set", "--mode", "soft_claim", "--operation-id", "mode-intent")
    assert code == 1 and mismatch["error_code"] == "coordination_operation_identity_mismatch", mismatch
    assert state.read_bytes() == original
    state.unlink()
    assert cli("set", "--mode", "soft_claim", "--operation-id", "without-display")[0] == 0
    assert cli("show")[1]["handoff_mode"] == "soft_claim"
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
