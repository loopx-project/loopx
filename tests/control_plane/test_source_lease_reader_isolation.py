"""Supported source leases retain readback without the historic mutation facade."""

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

    first = lease(
        "acquire",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--ttl-seconds",
        "600",
        success=mode != "soft_claim",
    )
    if mode == "soft_claim":
        assert not first["ok"] and first["error_code"] == "handoff_mode_forbids_lease"
        assert lease("inspect")["lease"] is None
        assert (w.state.read_bytes(), w.registry.read_bytes()) == original
        return

    assert first["ok"] and first["acquired"] and first["lease"]["owner"] == "agent-a"
    assert first["lease"]["version"] == first["lease"]["lease_epoch"] == 1
    assert lease("inspect")["active"] is True
    retry = lease(
        "acquire",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--ttl-seconds",
        "600",
    )
    assert retry["idempotent"] and retry["lease"] == first["lease"]
    denied = lease(
        "acquire", "--owner", "agent-b", "--idempotency-key", "other", success=False
    )
    assert not denied["ok"] and denied["error_code"] == "todo_lease_conflict"
    renewed = lease(
        "renew",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--expected-version",
        "1",
    )
    assert renewed["lease"]["version"] == 2 and renewed["lease"]["lease_epoch"] == 1
    # The exact original renewal is receipt recovery, not a stale new write.
    replay = lease(
        "renew",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--expected-version",
        "1",
    )
    assert replay["idempotent"] and replay["lease"] == renewed["lease"]
    assert lease("inspect")["lease"] == renewed["lease"]
    transferred = lease(
        "transfer",
        "--owner",
        "agent-a",
        "--idempotency-key",
        "source-a",
        "--expected-version",
        "2",
        "--new-owner",
        "agent-b",
        "--new-idempotency-key",
        "source-b",
    )
    assert (
        transferred["lease"]["version"] == 3
        and transferred["lease"]["lease_epoch"] == 2
    )
    assert transferred["lease"]["owner"] == "agent-b"
    stale = lease(
        "renew",
        "--owner",
        "agent-b",
        "--idempotency-key",
        "source-b",
        "--expected-version",
        "2",
        success=False,
    )
    assert not stale["ok"] and stale["error_code"] == "version_mismatch"
    assert lease("inspect")["lease"] == transferred["lease"]
    released = lease(
        "release",
        "--owner",
        "agent-b",
        "--idempotency-key",
        "source-b",
        "--expected-version",
        "3",
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
