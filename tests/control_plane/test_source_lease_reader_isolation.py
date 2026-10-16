"""Historical source leases retain readback and exact cleanup without the facade."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from shadow_e2e_fixture import workspace
from test_canonical_todo_writer_isolation import (
    isolated_todo_distribution,  # noqa: F401 -- distribution fixture
    retirement_wheel,  # noqa: F401 -- distribution fixtures
)


@pytest.mark.parametrize("facade", ["present", "absent"])
@pytest.mark.parametrize("capture", [False, True], ids=["capture-off", "capture-on"])
@pytest.mark.parametrize("mode", ["legacy", "hard_lease", "soft_claim"])
def test_source_lease_lifecycle_uses_retained_reader(
    tmp_path,
    isolated_todo_distribution,  # noqa: F811
    facade,
    capture,
    mode,  # noqa: F811
):
    package, command = isolated_todo_distribution
    w = workspace(tmp_path / "source", bootstrap=False)
    w.command, w.cwd, w.package = tuple(command), tmp_path, package
    w.state.write_text(
        w.state.read_text().replace("handoff_mode: hard_lease", f"handoff_mode: {mode}")
        + "- [ ] [P1] Preserve source work — 完整内容.\n"
        "  <!-- loopx:todo todo_id=todo_source status=open task_class=advancement_task -->\n"
    )
    config = json.loads(w.registry.read_text())
    config["goals"][0]["coordination"]["runtime_shadow"]["enabled"] = capture
    w.registry.write_text(json.dumps(config))
    if capture:
        assert (
            w.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"][
                "status"
            ]
            == "applied"
        )
    if facade == "absent":
        (package / "todos.py").unlink()
        subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                "from importlib.util import find_spec;assert find_spec('loopx.todos') is None",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    original = w.state.read_bytes(), w.registry.read_bytes()

    def lease(action, *args, success=True):
        return w.cli(
            "task-lease", action, "--todo-id", "todo_source", *args, success=success
        )

    rejected = lease(
        "acquire",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--ttl-seconds",
        "600",
        success=False,
    )
    assert not rejected["ok"]
    assert rejected["error_code"] == "canonical_authority_required"

    lease_path = (
        w.runtime / "goals" / w.goal / "task-leases" / "todo_source.json"
    )
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    historical = {
        "schema_version": "task_lease_v0",
        "goal_id": w.goal,
        "todo_id": "todo_source",
        "status": "active",
        "owner": "agent-a",
        "idempotency_key": "source-a",
        "write_scopes": [],
        "acquire_ttl_seconds": 600,
        "version": 1,
        "lease_epoch": 1,
        "acquired_at": "2026-09-01T00:00:00Z",
        "updated_at": "2026-09-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    lease_path.write_text(json.dumps(historical))
    assert lease("inspect")["lease"] == historical

    renew = lease(
        "renew",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--expected-version",
        "1",
        success=False,
    )
    assert renew["error_code"] == "canonical_authority_required"
    transfer = lease(
        "transfer",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--expected-version",
        "1",
        "--new-owner",
        "agent-b",
        "--new-idempotency-key",
        "source-b",
        success=False,
    )
    assert transfer["error_code"] == "canonical_authority_required"
    assert lease("inspect")["lease"] == historical

    released = lease(
        "release",
        "--owner", "agent-a",
        "--idempotency-key",
        "source-a",
        "--expected-version",
        "1",
    )
    assert released["lease"]["status"] == "released"
    retained = lease("inspect")
    assert not retained["active"] and retained["lease"] == released["lease"]
    assert (w.state.read_bytes(), w.registry.read_bytes()) == original
    if capture:
        # Read the retained journal after the lifecycle; no capture remains pending.
        history = json.loads(subprocess.check_output(
            [sys.executable, "-S", "-c",
             "import json,sys;from pathlib import Path;"
             "from loopx.control_plane.coordination.local_authority_shadow_history "
             "import outbox_summary;"
             "print(json.dumps(outbox_summary(Path(sys.argv[1]),sys.argv[2])))",
             str(w.runtime), w.goal],
            cwd=tmp_path, text=True, timeout=30,
        ))
        assert history["leases"]["prepared_only"] == 0
        assert history["leases"]["committed_pending"] == 0
