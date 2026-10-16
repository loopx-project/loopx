#!/usr/bin/env python3
"""Canary existing-successor completion across peer task domains."""

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
    run_json_cli_result,
    write_fixture_registry,
)


GOAL_ID = "auto-research-role-successor-fixture"
CURATOR = "research-curator"
PROPOSER = "hypothesis-proposer"
EXECUTOR = "research-executor"
PROPOSER_TODO = "todo_auto_research_propose"
EXECUTOR_TODO = "todo_auto_research_execute"


def write_fixture(root: Path, *, auto_research: bool) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    registry_path = project / ".loopx" / "registry.json"
    state_path = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Exercise role successor completion."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Auto-Research Role Successor Fixture\n\n"
        "## Objective\n\n"
        "Exercise role successor completion.\n\n"
        "## Next Action\n\n"
        "- Let auto-research roles advance through existing successors.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Propose a public-safe research hypothesis.\n"
        f"  <!-- loopx:todo todo_id={PROPOSER_TODO} status=open "
        "task_class=advancement_task action_kind=auto_research_hypothesis_proposal "
        f"claimed_by={PROPOSER} -->\n"
        "- [ ] [P1] Execute the selected public-safe hypothesis.\n"
        f"  <!-- loopx:todo todo_id={EXECUTOR_TODO} status=open "
        "task_class=advancement_task action_kind=auto_research_executor_attempt "
        f"claimed_by={EXECUTOR} resume_when={PROPOSER_TODO}:done -->\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="auto-research-demo" if auto_research else "side-agent-handoff-fixture",
        adapter_kind=(
            "auto_research_demo_local_queue"
            if auto_research
            else "side_agent_handoff_fixture_v0"
        ),
        adapter_status="connected",
        registered_agents=[CURATOR, PROPOSER, EXECUTOR],
        peer_independent_worktree_required=False,
    )
    import_fixture_authority(registry_path, GOAL_ID)
    return project, runtime, registry_path


def complete_proposer(
    registry_path: Path,
    runtime: Path,
    *,
    actor_agent_id: str | None = PROPOSER,
) -> tuple[int, dict]:
    cli_args = [
        "todo",
        "complete",
        "--goal-id",
        GOAL_ID,
        "--role",
        "agent",
        "--todo-id",
        PROPOSER_TODO,
        "--claimed-by",
        PROPOSER,
    ]
    if actor_agent_id is not None:
        cli_args.extend(["--agent-id", actor_agent_id])
    cli_args.extend(
        [
            "--evidence",
            "fixture produced a public-safe hypothesis packet",
            "--successor-todo-id",
            EXECUTOR_TODO,
        ]
    )
    return run_json_cli_result(
        *cli_args,
        registry_path=registry_path,
        runtime_root=runtime,
        cwd=REPO_ROOT,
    )


def assert_actor_attribution_fails_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-role-successor-actor-") as tmp:
        project, runtime, registry_path = write_fixture(Path(tmp), auto_research=True)
        state_path = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
        before = state_path.read_text(encoding="utf-8")

        missing_code, missing = complete_proposer(
            registry_path,
            runtime,
            actor_agent_id=None,
        )
        assert missing_code == 1, missing
        assert missing["error_code"] == "actor_required", missing
        assert state_path.read_text(encoding="utf-8") == before

        wrong_code, wrong = complete_proposer(
            registry_path,
            runtime,
            actor_agent_id=CURATOR,
        )
        assert wrong_code == 1, wrong
        assert wrong["error_code"] == "claim_owner_mismatch", wrong
        assert state_path.read_text(encoding="utf-8") == before


def assert_auto_research_role_successor_allowed() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-auto-research-role-successor-") as tmp:
        _project, runtime, registry_path = write_fixture(Path(tmp), auto_research=True)
        returncode, completed = complete_proposer(registry_path, runtime)
        assert returncode == 0, completed
        assert completed["ok"] is True, completed
        assert completed["status"] == "done", completed
        assert completed["successor_todo_ids"] == [EXECUTOR_TODO], completed
        assert completed["next_todos"] == [], completed

        successor_lookup = run_json_cli(
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            EXECUTOR_TODO,
            registry_path=registry_path,
            runtime_root=runtime,
            cwd=REPO_ROOT,
        )
        assert successor_lookup["matched"] is True, successor_lookup
        assert successor_lookup["todo"]["status"] == "open", successor_lookup
        assert successor_lookup["todo"]["claimed_by"] == EXECUTOR, successor_lookup


def assert_generic_peer_route_links_existing_successor() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-generic-peer-successor-") as tmp:
        _project, runtime, registry_path = write_fixture(Path(tmp), auto_research=False)
        returncode, completed = complete_proposer(registry_path, runtime)
        assert returncode == 0, completed
        assert completed["ok"] is True, completed
        assert completed["successor_todo_ids"] == [EXECUTOR_TODO], completed


def main() -> None:
    assert_actor_attribution_fails_closed()
    assert_auto_research_role_successor_allowed()
    assert_generic_peer_route_links_existing_successor()
    print("auto-research-role-successor-completion-smoke ok")


if __name__ == "__main__":
    main()
