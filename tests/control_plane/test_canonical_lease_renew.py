"""Real CLI renewal of provider-owned leases, including stale/missing display."""
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

from loopx.control_plane.coordination.coordination_state_contract import (
    TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
    TODO_DOMAIN_RECORD_FIELDS,
)
from loopx.control_plane.coordination.local_authority_shadow_projection import (
    canonical_bytes,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.work_items.task_lease import renew_task_lease

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("display", ["missing", "invalid_mode", "malformed_yaml"])
def test_public_renew_preserves_canonical_authority_and_historical_receipt(tmp_path, monkeypatch, provider, display):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal = "renew-goal"
    state.write_text("# Synthetic renewal\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a", "agent-b"],
                         "runtime_shadow": {"enabled": True, "provider": "file_v0"}},
    }]}))
    now = datetime.now(UTC).replace(microsecond=0)
    def iso(value):
        return value.isoformat().replace("+00:00", "Z")
    lease = {"schema_version": "task_lease_v0", "goal_id": goal, "todo_id": "todo_renew",
             "owner": "agent-a", "idempotency_key": "execution-a", "version": 1, "lease_epoch": 7,
             "status": "active", "write_scopes": ["src/**"], "acquire_ttl_seconds": 600,
             "acquired_at": iso(now - timedelta(seconds=60)), "updated_at": iso(now - timedelta(seconds=60)),
             "expires_at": iso(now + timedelta(seconds=300))}
    projection = build_todo_runtime_shadow_projection(goal_id=goal, handoff_mode="hard_lease", leases=[lease], todos=[{
        "schema_version": "todo_item_v0", "todo_id": "todo_renew", "role": "agent", "status": "open", "done": False,
        "text": "Canonical renewal", "archive_state": "active", "source_section": "Agent Todo", "index": 1,
        "claimed_by": "agent-a", "task_class": "advancement_task",
    }])
    for todo in projection["todos"]:
        todo["schema_version"] = "todo_domain_record_v0"
        todo.pop("index")
        todo.pop("source_section")
    projection["todo_read_model"] = {"schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS), "todo_count": 1,
        "records_sha256": hashlib.sha256(canonical_bytes(projection["todos"])).hexdigest()}
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    if display == "missing":
        state.unlink()
    elif display == "malformed_yaml":
        state.write_text("---\nhandoff_mode: [broken\n---\n\n## Agent Todo\n")
    else:
        state.write_text("---\nhandoff_mode: invalid_stale_display\n---\n\n## Agent Todo\n")
    display_before = state.read_bytes() if state.exists() else None

    def read_head():
        module = (REPO / "loopx/control_plane/coordination/local_authority_provider.ts").as_uri()
        script = f"import {{openLocalAuthorityStore}} from {json.dumps(module)};const s=await openLocalAuthorityStore(process.argv[1],process.argv[2]);console.log(JSON.stringify(await s.loadAuthority()));"
        child = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script, str(runtime), goal],
                               capture_output=True, text=True, timeout=30, check=True)
        return json.loads(child.stdout)

    def outside_provider():
        return {str(path.relative_to(runtime)): path.read_bytes() for path in runtime.rglob("*")
                if path.is_file() and path.relative_to(runtime).parts[:2] not in {
                    ("authority", "file-v0"), ("authority", "sqlite-v0")}}

    def renew(version, ttl=600, expected_exit=0):
        child = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            "task-lease", "renew", "--goal-id", goal, "--todo-id", "todo_renew", "--owner", "agent-a",
            "--idempotency-key", "execution-a", "--expected-version", str(version), "--ttl-seconds", str(ttl)],
            capture_output=True, text=True, timeout=60, check=False)
        assert child.returncode == expected_exit, child.stdout + child.stderr
        return json.loads(child.stdout)

    before = outside_provider()
    try:
        first = renew(1)
        assert first["ok"] and first["source_authority"] == provider + "_v0"
        assert first["decision_read_from_provider"] is True and first["legacy_fallback_used"] is False
        assert "lease_path" not in first and "projection_delivery" not in first and "authority_shadow" not in first
        assert first["lease"]["version"] == 2 and first["lease"]["lease_epoch"] == 7
        assert first["lease"]["write_scopes"] == lease["write_scopes"]
        second = renew(2)
        assert second["lease"]["version"] == 3
        # Guard the actual source IO, not the name of a retired capture helper.
        # Both read_bytes and read_text enter Path.open, even for missing files.
        display_path, original_open = state.resolve(), Path.open
        def forbidden_display_open(path, *args, **kwargs):
            if path.resolve() == display_path:
                raise AssertionError("canonical renew accessed the Goal display")
            return original_open(path, *args, **kwargs)
        with monkeypatch.context() as guard:
            guard.setattr(Path, "open", forbidden_display_open)
            third = renew_task_lease(registry_path=registry, runtime_root=runtime, goal_id=goal,
                todo_id="todo_renew", owner="agent-a", idempotency_key="execution-a", expected_version=3, ttl_seconds=600)
        assert third["lease"]["version"] == 4
        current = read_head()
        inspected = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
            "task-lease", "inspect", "--goal-id", goal, "--todo-id", "todo_renew"], capture_output=True, text=True, timeout=60, check=True)
        observed = json.loads(inspected.stdout)
        assert observed["source_authority"] == provider + "_v0" and observed["active"] is True
        assert observed["lease"] == third["lease"] and observed["lease_path"] is None
        replay = renew(1)
        assert replay["idempotent"] is True and replay["status"] == "replayed"
        assert replay["lease"] == first["lease"] and replay["original_receipt"] == first["original_receipt"]
        assert read_head() == current
        changed = renew(1, 900, expected_exit=1)
        assert changed["error_code"] == "coordination_operation_identity_mismatch"
        assert read_head() == current
        assert outside_provider() == before
        assert (state.read_bytes() if state.exists() else None) == display_before
        assert current["cursor"] == "4" and current["head"]["todos"] == projection["todos"]
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
                       capture_output=True, text=True, timeout=30, check=True)
