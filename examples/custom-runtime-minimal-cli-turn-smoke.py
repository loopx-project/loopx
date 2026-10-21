#!/usr/bin/env python3
"""Mainstream custom-runtime contract: skill + host wake + one LoopX CLI turn.

This is the lightweight cooperative-agent path described in issue #2835:
host-owned wake, a short re-entry instruction (skill), and LoopX CLI for
status/quota/claim/execute/validate/writeback/refresh/spend. It is not a
typed Turn adapter; that advanced path is covered separately by
``examples/loopx-turn-fake-host-walkthrough-smoke.py``.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GOAL_ID = "custom-runtime-min"
AGENT_ID = "custom-host-agent"
TURN_ID = "custom-runtime-fixture-turn"
MARKER_NAME = "public-marker.txt"
MARKER_BODY = "ok-from-custom-runtime\n"
ADVANCED_TURN_SMOKE = (
    REPO_ROOT / "examples" / "loopx-turn-fake-host-walkthrough-smoke.py"
)

# Host-owned wake trigger: cron, scheduler callback, visible /goal, or a human.
WAKE_TRIGGER = "host_scheduler_tick"

# Lightweight skill / re-entry instruction injected by the host. It must not
# cache previous packets or invent LoopX state stores.
REENTRY_INSTRUCTION = """
1. Read fresh `loopx status` and `loopx quota should-run` for this goal/agent.
2. Obey user gates; claim one eligible agent todo before write-capable work.
3. Execute one bounded slice; validate the real postcondition yourself.
4. Write back todo/evidence via LoopX CLI, then `refresh-state`.
5. `quota spend-slot --execute` only after validated durable writeback.
6. Apply any scheduler_hint on the host; start the next wake from step 1.
""".strip()


def run_cli(registry_path: Path, *args: str) -> dict[str, Any]:
    """Drive the shipped LoopX CLI entrypoint (not a reimplemented client)."""
    from loopx.cli import main as loopx_cli_main
    from loopx.control_plane.testing.canary_harness import runtime_root_from_registry

    runtime_root = runtime_root_from_registry(registry_path)
    argv = [
        "--registry",
        str(registry_path),
        "--runtime-root",
        str(runtime_root),
        "--format",
        "json",
        *args,
    ]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = loopx_cli_main(argv)
    raw = buf.getvalue().strip()
    payload: dict[str, Any] = json.loads(raw) if raw else {}
    if code != 0:
        raise AssertionError(
            f"loopx {' '.join(args)} failed ({code}): "
            f"{json.dumps(payload, ensure_ascii=False)[:1200]}"
        )
    return payload


def write_project_fixture(root: Path) -> tuple[Path, Path, Path]:
    from loopx.control_plane.testing.canary_harness import write_fixture_registry

    project = root / "project"
    runtime = root / "runtime"
    project.mkdir()
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Prove the mainstream custom-runtime CLI turn contract.\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="custom-runtime-fixture",
        adapter_kind="generic_project_goal_v0",
        registered_agents=[AGENT_ID],
        quota_allowed_slots=5,
    )
    return project, registry_path, state_file


def assert_mainstream_cli_turn(provider: str) -> None:
    from loopx.status import parse_active_state_todos
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from loopx.control_plane.testing.canary_harness import import_fixture_authority

    assert WAKE_TRIGGER, "host must own a wake trigger"
    assert "quota should-run" in REENTRY_INSTRUCTION
    assert "spend-slot" in REENTRY_INSTRUCTION
    assert ADVANCED_TURN_SMOKE.is_file(), ADVANCED_TURN_SMOKE

    with tempfile.TemporaryDirectory(prefix="loopx-custom-runtime-min-") as tmp:
        project, registry_path, state_file = write_project_fixture(Path(tmp))
        import_fixture_authority(registry_path, GOAL_ID, provider=provider)

        status = run_cli(registry_path, "status", )
        assert status.get("ok") is True, status

        added = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Write one synthetic public marker file.",
        )
        todo_id = added["todo_id"]
        assert todo_id.startswith("todo_"), added

        claimed = run_cli(
            registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--claimed-by",
            AGENT_ID,
            "--agent-id",
            AGENT_ID,
        )
        assert claimed.get("changed") is True, claimed
        assert claimed.get("claimed_by") == AGENT_ID, claimed

        guard = run_cli(
            registry_path,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--runtime-profile",
            "generic_cli",
            "--todo-id", todo_id,
            "--turn-instance-id", TURN_ID,
        )
        assert guard.get("should_run") is True, guard
        assert guard.get("state") == "eligible", guard

        # Host/agent executes one bounded slice outside LoopX stores.
        marker = project / MARKER_NAME
        marker.write_text(MARKER_BODY, encoding="utf-8")
        # Independent validation: real artifact, not the agent's claim alone.
        assert marker.read_text(encoding="utf-8") == MARKER_BODY

        completed = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--claimed-by",
            AGENT_ID,
            "--agent-id",
            AGENT_ID,
            "--turn-instance-id", TURN_ID,
            "--evidence",
            "public marker written under project root",
        )
        assert completed.get("changed") is True, completed
        assert completed["completion_continuation"] == "active_goal", completed
        assert completed["successor_todo_ids"] == [], completed

        summary = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        item = next(
            row
            for row in summary["agent_todos"]["items"]
            if row.get("todo_id") == todo_id
        )
        assert item.get("done") is True, item
        canonical = read_canonical_todos_if_promoted(
            runtime_root=Path(tmp) / "runtime", goal_id=GOAL_ID,
        )
        stored = next(row for row in canonical["todos"] if row["todo_id"] == todo_id)
        assert stored["status"] == "done" and stored["claimed_by"] == AGENT_ID, stored

        refreshed = run_cli(
            registry_path,
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--agent-id", AGENT_ID,
            "--agent-lane", AGENT_ID,
            "--progress-scope", "agent_lane",
            "--todo-id", todo_id,
            "--turn-instance-id", TURN_ID,
            "--classification", "validated_progress",
            "--delivery-batch-scale", "multi_surface",
            "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation",
            "--progress-result-class", "advanced",
            "--progress-surface-id", "custom-runtime-cli",
            "--progress-work-item-id", todo_id,
            "--progress-hypothesis-id", "marker-terminal-single-debit",
            "--progress-probe-kind", "real-cli-artifact-readback",
            "--progress-coverage-scope-id", "canonical-cli-turn",
            "--progress-evidence-id", "artifact:public-marker",
            "--no-global-sync",
        )
        assert refreshed.get("ok") is True, refreshed
        assert refreshed.get("appended") is True, refreshed

        spent = run_cli(
            registry_path,
            "quota",
            "spend-slot",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--todo-id", todo_id,
            "--turn-instance-id", TURN_ID,
            "--slots",
            "1",
            "--source",
            "heartbeat",
            "--execute",
        )
        assert spent.get("ok") is True, spent
        assert spent.get("appended") is True, spent
        after = spent["quota_event"]["after"]
        assert after["spent_slots"] == 1, spent
        assert after["allowed_slots"] == 5, spent
        closed = run_cli(
            registry_path, "todo", "complete", "--goal-id", GOAL_ID,
            "--todo-id", todo_id, "--claimed-by", AGENT_ID, "--agent-id", AGENT_ID,
            "--turn-instance-id", TURN_ID,
            "--evidence", "public marker written under project root", "--no-follow-up",
        )
        assert closed["completion_continuation"] == "no_followup", closed
        replay = run_cli(
            registry_path, "quota", "spend-slot", "--goal-id", GOAL_ID,
            "--agent-id", AGENT_ID, "--todo-id", todo_id,
            "--turn-instance-id", TURN_ID, "--slots", "1", "--source", "heartbeat",
            "--execute",
        )
        assert replay["appended"] is False and replay["idempotent_replay"] is True, replay


def main() -> int:
    for provider in ("file", "sqlite"):
        assert_mainstream_cli_turn(provider)
    print("custom-runtime-minimal-cli-turn-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
