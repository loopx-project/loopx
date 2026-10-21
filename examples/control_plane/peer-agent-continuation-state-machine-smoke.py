#!/usr/bin/env python3
"""Exercise peer continuation and generic executor separation."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    import_fixture_authority,
    run_json_cli,
    write_fixture_registry,
)


GOAL_ID = "peer-agent-continuation-fixture"
PEER_ALPHA = "codex-alpha"
PEER_BETA = "codex-beta"
START_TODO_ID = "todo_peer_start"
REVIEW_TODO_ID = "todo_peer_review"
NEXT_TODO_TEXT = "Continue the same peer lane with scheduler coverage."


def run_cli(*args: str, registry_path: Path, runtime: Path, cwd: Path) -> dict:
    return run_json_cli(
        *args,
        registry_path=registry_path,
        runtime_root=runtime,
        cwd=REPO_ROOT,
    )


def write_fixture(root: Path, *, include_review: bool = False) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    state_path = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True)
    review = (
        f"- [ ] [P1] Review the completed peer delivery.\n"
        f"  <!-- loopx:todo todo_id={REVIEW_TODO_ID} status=open "
        f"task_class=advancement_task action_kind=review "
        f"continuation_policy=independent_handoff claimed_by={PEER_BETA} "
        f"excluded_agents={PEER_ALPHA} unblocks_todo_id={START_TODO_ID} -->\n"
        if include_review
        else ""
    )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Exercise peer continuation semantics."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Peer Continuation Fixture\n\n"
        "## Next Action\n\n"
        "- Keep the goal route stable while peer task lanes advance.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Build the first peer continuation slice.\n"
        f"  <!-- loopx:todo todo_id={START_TODO_ID} status=open "
        "task_class=advancement_task action_kind=implementation "
        f"claimed_by={PEER_ALPHA} -->\n"
        f"{review}",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="peer-agent-continuation-fixture",
        adapter_kind="peer_agent_continuation_fixture_v1",
        adapter_status="connected-read-only",
        registered_agents=[PEER_ALPHA, PEER_BETA],
        quota_allowed_slots=5,
        peer_independent_worktree_required=False,
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return project, runtime, registry_path


def should_run(project: Path, runtime: Path, registry_path: Path, agent_id: str) -> dict:
    return run_cli(
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        agent_id,
        "--host-surface", "generic_cli", "--scheduler-owner", "none", "--execution-mode", "interactive",
        registry_path=registry_path,
        runtime=runtime,
        cwd=project,
    )


def assert_same_peer_continuation() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-peer-continuation-") as tmp:
        project, runtime, registry_path = write_fixture(Path(tmp))
        first = should_run(project, runtime, registry_path, PEER_ALPHA)
        assert first["decision"] == "run", first
        assert first["agent_identity"]["agent_model"] == "peer_v1", first
        assert "role" not in first["agent_identity"], first
        assert "primary_agent" not in first["agent_identity"], first
        assert first["agent_lane_next_action"]["todo_id"] == START_TODO_ID, first

        completed = run_cli(
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--todo-id",
            START_TODO_ID,
            "--claimed-by",
            PEER_ALPHA,
            "--agent-id",
            PEER_ALPHA,
            "--self-merged",
            "--evidence",
            "focused peer continuation validation passed",
            "--next-agent-todo",
            NEXT_TODO_TEXT,
            "--next-claimed-by",
            PEER_ALPHA,
            "--next-task-class",
            "advancement_task",
            "--next-action-kind",
            "implementation",
            registry_path=registry_path,
            runtime=runtime,
            cwd=project,
        )
        assert completed["completion_policy"]["self_merged"] is True, completed
        successor = completed["next_todos"][0]
        assert successor["claimed_by"] == PEER_ALPHA, successor
        second = should_run(project, runtime, registry_path, PEER_ALPHA)
        assert second["agent_lane_next_action"]["todo_id"] == successor["todo_id"], second


def assert_review_is_action_semantics_over_generic_handoff() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-peer-review-handoff-") as tmp:
        project, runtime, registry_path = write_fixture(Path(tmp), include_review=True)
        completed = run_cli(
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--todo-id",
            START_TODO_ID,
            "--claimed-by",
            PEER_ALPHA,
            "--agent-id",
            PEER_ALPHA,
            "--evidence",
            "peer delivery ready for independent review",
            "--successor-todo-id",
            REVIEW_TODO_ID,
            registry_path=registry_path,
            runtime=runtime,
            cwd=project,
        )
        assert completed["completion_policy"]["self_merged"] is False, completed
        assert completed["completion_policy"]["linked_successor_id"] == REVIEW_TODO_ID, completed

        beta = should_run(project, runtime, registry_path, PEER_BETA)
        assert beta["decision"] == "run", beta
        assert beta["agent_lane_next_action"]["todo_id"] == REVIEW_TODO_ID, beta
        assert beta["agent_lane_next_action"]["continuation_policy"] == (
            "independent_handoff"
        ), beta
        assert beta["agent_lane_next_action"]["action_kind"] == "review", beta
        assert beta["agent_lane_next_action"]["excluded_agents"] == [PEER_ALPHA], beta
        assert "primary_agent" not in beta, beta


def main() -> int:
    assert_same_peer_continuation()
    assert_review_is_action_semantics_over_generic_handoff()
    print("peer-agent-continuation-state-machine-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
