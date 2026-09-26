"""A public lease read must not mix promoted authority with obsolete files."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.work_items.task_lease import inspect_task_lease

GOAL = "goal-lease-reader"
TODO = "todo_current"


def _fixture(root: Path, provider: str, *, retained: bool = True, excluded: bool = False, todo_patch=None, lease_patch=None, peer: bool = False):
    runtime = root / "runtime"
    state = root / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nhandoff_mode: soft_claim\n---\n# Obsolete display\n")
    registry = root / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [{
        "id": GOAL, "repo": str(root), "state_file": str(state),
        "coordination": {"registered_agents": ["agent-a", "agent-b"]}}]}))
    todo = {"schema_version": "todo_item_v0", "source_section": "Agent Todo",
            "todo_id": TODO, "role": "agent", "text": "Verify canonical ownership",
            "status": "open", "done": False, "archive_state": "active", "task_class": "advancement_task",
            "excluded_agents": ["agent-a"] if excluded else []}
    lease = {"schema_version": "task_lease_v0", "goal_id": GOAL, "todo_id": TODO,
             "status": "active", "owner": "agent-a", "idempotency_key": "lease-first",
             "expires_at": "2099-01-01T00:00:00Z", "lease_epoch": 3, "version": 2}
    todo.update(todo_patch or {})
    lease.update(lease_patch or {})
    todos = [todo]
    if peer:
        todos.append({**todo, "todo_id": "todo_peer", "task_repository": "git:github.com/team/b"})
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL, todos=todos,
        leases=[lease] if retained else [], handoff_mode="hard_lease")
    # Preserve retained provider history even when a legacy capture would omit it.
    projection["leases"] = [lease] if retained else []
    initialize_canonical_authority(runtime, GOAL, projection, state_path=state, provider=provider)
    obsolete = runtime / "goals" / GOAL / "task-leases" / f"{TODO}.json"
    obsolete.parent.mkdir(parents=True, exist_ok=True)
    obsolete.write_text(json.dumps({**lease, "owner": "agent-b", "version": 999}))
    return registry, runtime, state, obsolete


def _inspect(registry, runtime):
    return inspect_task_lease(registry_path=registry, runtime_root=runtime, goal_id=GOAL, todo_id=TODO)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("display", ["stale", "missing", "malformed"])
@pytest.mark.parametrize("retained", [False, True])
def test_public_inspect_uses_one_revision_and_never_revives_obsolete_files(tmp_path, monkeypatch, provider, display, retained):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state, obsolete = _fixture(tmp_path, provider, retained=retained)
    if display == "missing":
        state.unlink()
    elif display == "malformed":
        state.write_text("invalid display")
        obsolete.write_text("invalid obsolete lease")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL, include_leases=True)
    obsolete_before = obsolete.read_bytes()
    result = _inspect(registry, runtime)
    assert result["ok"] is True
    assert result["active"] is retained
    assert result["handoff_mode"] == "hard_lease"
    assert result["lease_path"] is None
    assert result["legacy_fallback_used"] is False
    assert result["provider_revision"] == before["provider_revision"]
    if retained:
        assert result["lease"]["owner"] == "agent-a"
        assert result["lease"]["version"] == 2
    else:
        assert result["lease"] is None
    process = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--format", "json", "task-lease", "inspect", "--goal-id", GOAL, "--todo-id", TODO],
        capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr + process.stdout
    assert json.loads(process.stdout) == result
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL, include_leases=True) == before
    assert obsolete.read_bytes() == obsolete_before
    assert state.exists() is (display != "missing")


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_active_but_excluded_owner_is_not_effective(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _, _ = _fixture(tmp_path, provider, excluded=True)
    result = _inspect(registry, runtime)
    assert result["active"] is False
    assert result["lease"]["status"] == "active"
    assert result["executor_constraint"]["reason"] == "owner_excluded_from_todo"


def test_unavailable_provider_fails_instead_of_reading_legacy_lease(tmp_path):
    registry, runtime, _, obsolete = _fixture(tmp_path, "file")
    authority = runtime / "authority" / "file-v0"
    authority.rename(authority.with_name("offline-fixture"))
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as error:
        _inspect(registry, runtime)
    assert getattr(error.value, "code", "").startswith("local_authority_")
    assert json.loads(obsolete.read_text())["owner"] == "agent-b"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_archived_open_record_cannot_make_retained_execution_effective(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _, _ = _fixture(tmp_path, provider,
        todo_patch={"archive_state": "archive", "source_section": "Completed Work Archive"})
    result = _inspect(registry, runtime)
    assert result["active"] is False
    assert result["executor_constraint"] == {"effective": False, "reason": "todo_not_found"}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_corrupt_active_expiry_is_not_reported_as_an_inactive_lease(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _, _ = _fixture(tmp_path, provider, lease_patch={"expires_at": "not-a-date"})
    from loopx.control_plane.work_items.local_lease_record import TaskLeaseError
    with pytest.raises((TaskLeaseError, LocalCoordinationAuthorityUnavailable)):
        _inspect(registry, runtime)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_registration_change_retries_and_uses_new_eligibility(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _, _ = _fixture(tmp_path, provider)
    from loopx.control_plane import effect_runtime
    original = effect_runtime.effect_runtime_result
    calls = []

    def change_registration(method, payload, **kwargs):
        if method == "task_lease.inspect.native":
            calls.append(method)
            if len(calls) == 1:
                updated = json.loads(registry.read_text())
                updated["goals"][0]["coordination"]["registered_agents"] = ["agent-b"]
                registry.write_text(json.dumps(updated))
        return original(method, payload, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", change_registration)
    result = _inspect(registry, runtime)
    assert len(calls) == 2
    assert result["active"] is False
    assert result["executor_constraint"] == {
        "effective": False, "reason": "owner_not_registered", "registered_agents": ["agent-b"],
    }


def test_continuous_source_churn_exhausts_bounded_retry_without_success(tmp_path, monkeypatch):
    registry, runtime, _, _ = _fixture(tmp_path, "file")
    from loopx.control_plane import effect_runtime
    original = effect_runtime.effect_runtime_result
    calls = []

    def change_source(method, payload, **kwargs):
        if method == "task_lease.inspect.native":
            calls.append(method)
            registry.write_text(registry.read_text() + "\n")
        return original(method, payload, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_result", change_source)
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as error:
        _inspect(registry, runtime)
    assert error.value.code == "authority_source_changed"
    assert len(calls) == 3


def test_owner_constraint_requires_the_native_diagnostic_contract(monkeypatch):
    from loopx.control_plane import effect_runtime
    from loopx.control_plane.work_items.task_lease import task_lease_owner_constraint
    monkeypatch.setattr(effect_runtime, "effect_runtime_result", lambda *_: {
        "schema_version": "task_lease_owner_eligibility_v0", "outcome": "apply", "code": "lease_owner_allowed",
    })
    with pytest.raises(RuntimeError, match="constraint shape mismatch"):
        task_lease_owner_constraint({"status": "open"}, owner="agent-a")


def test_inactive_legacy_inspection_does_not_project_todos(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    state = tmp_path / "state.md"
    state.write_text("---\nhandoff_mode: hard_lease\n---\n# Synthetic state\n")
    registry.write_text(json.dumps({"goals": [{"id": GOAL, "repo": str(tmp_path), "state_file": str(state)}]}))
    import loopx.todos
    def forbidden_projection(**_):
        raise AssertionError("inactive inspection must not parse Todo history")
    monkeypatch.setattr(loopx.todos, "list_goal_todos", forbidden_projection)
    runtime = tmp_path / "runtime"
    missing = _inspect(registry, runtime)
    assert missing["active"] is False and missing["lease"] is None
    lease_path = runtime / "goals" / GOAL / "task-leases" / f"{TODO}.json"
    lease_path.parent.mkdir(parents=True)
    lease_path.write_text(json.dumps({"schema_version": "task_lease_v0", "status": "active", "expires_at": "2000-01-01T00:00:00Z"}))
    expired = _inspect(registry, runtime)
    assert expired["active"] is False and expired["handoff_mode"] == "hard_lease"


def test_legacy_inspection_reloads_lease_after_demanding_todo_facts(tmp_path, monkeypatch):
    registry = tmp_path / "registry.json"
    state = tmp_path / "state.md"
    state.write_text("---\nhandoff_mode: hard_lease\n---\n# Synthetic state\n")
    registry.write_text(json.dumps({"goals": [{"id": GOAL, "repo": str(tmp_path), "state_file": str(state),
        "coordination": {"registered_agents": ["agent-a"]}}]}))
    runtime = tmp_path / "runtime"
    lease_path = runtime / "goals" / GOAL / "task-leases" / f"{TODO}.json"
    lease_path.parent.mkdir(parents=True)
    lease = {"schema_version": "task_lease_v0", "status": "active", "owner": "agent-a", "expires_at": "2099-01-01T00:00:00Z"}
    lease_path.write_text(json.dumps(lease))
    import loopx.todos
    calls = []
    def release_during_projection(**_):
        calls.append(True)
        lease_path.write_text(json.dumps({**lease, "status": "released"}))
        return {"todos": [{"todo_id": TODO, "status": "open", "claimed_by": None, "excluded_agents": []}]}
    monkeypatch.setattr(loopx.todos, "list_goal_todos", release_during_projection)
    result = _inspect(registry, runtime)
    assert len(calls) == 1
    assert result["active"] is False
    assert result["lease"]["status"] == "released"
    assert "todo_projection_required" not in result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_repository_scoped_cli_acquire_and_json_markdown_readback(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _, obsolete = _fixture(tmp_path, provider, retained=False, peer=True,
        todo_patch={"task_repository": "git:github.com/team/a", "required_write_scopes": ["tests/**"]})
    obsolete_before = obsolete.read_bytes()

    def cli(action, todo, *args, output="json"):
        process = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--format", output, "task-lease", action, "--goal-id", GOAL, "--todo-id", todo, *args],
            capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr + process.stdout
        return json.loads(process.stdout) if output == "json" else process.stdout

    grants = []
    for todo, agent, name in [(TODO, "agent-a", "a"), ("todo_peer", "agent-b", "b")]:
        acquired = cli("acquire", todo, "--owner", agent, "--idempotency-key", f"repository-{name}",
            "--expected-version", "0", "--ttl-seconds", "120", "--write-scope", "tests/**")
        grants.append((acquired, name))
    for acquired, name in grants:
        assert acquired["lease"]["write_repository"] == f"git:github.com/team/{name}"
        assert acquired["lease"]["write_scopes"] == ["tests/**"]
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL, include_leases=True)
    for todo, name in [(TODO, "a"), ("todo_peer", "b")]:
        observed = cli("inspect", todo)
        assert observed["active"] is True
        assert observed["lease"]["write_repository"] == f"git:github.com/team/{name}"
        assert f"write_repository: `git:github.com/team/{name}`" in cli("inspect", todo, output="markdown")
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL, include_leases=True) == before
    assert obsolete.read_bytes() == obsolete_before
