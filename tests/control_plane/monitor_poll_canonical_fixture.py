"""Canonical Monitor poll fixtures shared by recovery tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.testing.canary_harness import (
    read_run_index,
    write_fixture_registry,
)


GOAL = "synthetic-monitor-recovery"
AGENT = "codex-quality-qualification"
MONITOR_TARGET = "synthetic-target"
AGENT_TASK = "Validate observed synthetic head"
USER_TASK = "Review observed synthetic head"
REPO = Path(__file__).resolve().parents[2]

_QUOTA_COMMIT_FAULT_WRAPPER = """
import os, runpy, sys
import loopx.control_plane.quota.monitor_poll as monitor
original = monitor._native_result
def interrupted_commit(request):
    if request.get('phase') == 'commit' and request.get('provider_receipt'):
        os._exit(73)
    return original(request)
monitor._native_result = interrupted_commit
sys.argv = ['loopx.cli', *sys.argv[1:]]
runpy.run_module('loopx.cli', run_name='__main__')
"""


def _cli(
    registry: Path,
    runtime: Path,
    *args: str,
    fault: str | None = None,
) -> tuple[int, dict | None]:
    launcher = (
        [sys.executable, "-c", _QUOTA_COMMIT_FAULT_WRAPPER]
        if fault == "quota_commit"
        else [sys.executable, "-m", "loopx.cli"]
    )
    result = subprocess.run(
        [
            *launcher,
            "--registry",
            str(registry),
            "--format",
            "json",
            "--runtime-root",
            str(runtime),
            *args,
        ],
        cwd=REPO,
        env={**os.environ, "LOOPX_USAGE_PING": "0"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return (
        result.returncode,
        json.loads(result.stdout) if result.stdout.strip() else None,
    )


def _ok(registry: Path, runtime: Path, *args: str) -> dict:
    code, result = _cli(registry, runtime, *args)
    assert code == 0, result
    assert isinstance(result, dict)
    return result


def _fixture(
    tmp_path: Path,
    *,
    provider: str,
) -> tuple[Path, Path, Path, str, tuple[str, ...]]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state = project / ".codex" / "goals" / GOAL / "ACTIVE_GOAL_STATE.md"
    registry = project / ".loopx" / "registry.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\n---\n\n# Active Goal State\n\n## Agent Todo\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry,
        goal_id=GOAL,
        domain="loopx-platform",
        adapter_kind="harness_self_improvement",
        registered_agents=[AGENT],
        quota_allowed_slots=None,
    )
    monitor = {
        "schema_version": "todo_item_v0",
        "todo_id": "todo_synthetic_monitor",
        "role": "agent",
        "status": "open",
        "done": False,
        "text": "Observe public synthetic target",
        "archive_state": "active",
        "source_section": "Agent Todo",
        "index": 1,
        "task_class": "continuous_monitor",
        "claimed_by": AGENT,
        "target_key": MONITOR_TARGET,
        "cadence": "1h",
        "next_due_at": "2000-01-01T00:00:00+00:00",
        "watch_only": "true",
    }
    initialize_canonical_authority(
        runtime,
        GOAL,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL,
            todos=[monitor],
            handoff_mode="soft_claim",
        ),
        state_path=state,
        provider=provider,
    )
    turn = "synthetic-monitor-turn-1"
    _ok(
        registry,
        runtime,
        "quota",
        "should-run",
        "--goal-id",
        GOAL,
        "--agent-id",
        AGENT,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn,
    )
    poll = (
        "quota",
        "monitor-poll",
        "--goal-id",
        GOAL,
        "--agent-id",
        AGENT,
        "--runtime-profile",
        "generic_cli",
        "--turn-instance-id",
        turn,
        "--todo-id",
        monitor["todo_id"],
        "--target-key",
        MONITOR_TARGET,
        "--result-hash",
        "synthetic-head-1",
        "--material-change",
        "--next-agent-todo",
        AGENT_TASK,
        "--next-action-kind",
        "validate",
        "--next-user-todo",
        USER_TASK,
        "--next-user-task-class",
        "user_action",
        "--execute",
    )
    return registry, runtime, state, monitor["todo_id"], poll


def _poll_events(runtime: Path) -> list[dict]:
    return [
        row
        for row in read_run_index(runtime, GOAL)
        if row.get("classification") == "quota_monitor_poll"
    ]


def _quota_receipt(runtime: Path) -> Path:
    directory = (
        runtime
        / "goals"
        / GOAL
        / "runs"
        / ".transactions"
        / "quota-monitor-poll"
    )
    (receipt,) = directory.glob("*.json")
    return receipt
