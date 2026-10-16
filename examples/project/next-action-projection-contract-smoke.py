#!/usr/bin/env python3
"""A bound task step survives history compaction without becoming task authority."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted  # noqa: E402
from loopx.control_plane.scheduler.execution_context import GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT  # noqa: E402
from loopx.control_plane.testing.canary_harness import import_fixture_authority, run_json_cli  # noqa: E402
from loopx.control_plane.todos.mutation_api import complete_goal_todo  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.state_refresh import refresh_state_run  # noqa: E402
from loopx.status import collect_status  # noqa: E402

GOAL_ID = "next-action-projection-goal"
ACTIVE_NEXT_ACTION = "Shared compatibility guidance."
SIDE_AGENT_ACTION = "Evaluate the current artifact."

def write_fixture(root: Path, *, include_next_action: bool = True) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True)
    next_action_section = (
        "## Next Action\n\n"
        f"- {ACTIVE_NEXT_ACTION}\n\n"
        if include_next_action
        else ""
    )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Keep next-action projections explicit."\n'
        "updated_at: 2026-06-22T00:00:00+00:00\n"
        "---\n\n"
        "# Next Action Projection Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Validate the primary public PoC control-plane lane.\n"
        "  <!-- loopx:todo todo_id=todo_primary status=open "
        "task_class=advancement_task claimed_by=codex-main-control -->\n"
        f"- [ ] [P1] {SIDE_AGENT_ACTION}\n"
        "  <!-- loopx:todo todo_id=todo_side status=open "
        "task_class=advancement_task claimed_by=codex-side-bypass -->\n\n"
        f"{next_action_section}"
        "## Progress Ledger\n\n"
        "- Fixture initialized.\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-06-22T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "next-action-projection-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "fixture", "status": "connected-read-only"},
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": ["codex-main-control", "codex-side-bypass"],
                        },
                        "authority_sources": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime, project, state_path


def state_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_state_next_action(path: Path, expected: str) -> None:
    text = state_text(path)
    assert f"- {expected}" in text, text



def assert_bound_projection(provider: str) -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-bound-recommendation-") as directory:
        registry, runtime, project, state = write_fixture(Path(directory))
        import_fixture_authority(registry, GOAL_ID, provider=provider)
        original = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        assert original["source_authority"] == provider + "_v0"
        assert {row["todo_id"] for row in original["todos"]} == {"todo_primary", "todo_side"}
        # Finish the import's display delivery before checking that a task
        # step does not change the already-current Markdown projection.
        run_json_cli("todo", "project-markdown", "--goal-id", GOAL_ID,
            "--provider-revision", original["provider_revision"], "--execute",
            registry_path=registry, runtime_root=runtime, cwd=REPO_ROOT)
        before = state.read_bytes()
        common = dict(registry_path=registry, runtime_root_override=str(runtime),
            goal_id=GOAL_ID, project=project, state_file=None, classification="state_refreshed",
            recommended_action=None, dry_run=False, sync_global=False)
        step = "Evaluate a smaller experiment, retaining the incumbent."
        result = refresh_state_run(**common, agent_id="codex-side-bypass", next_action=step)
        assert result["recommended_action_resolution"]["todo_id"] == "todo_side"
        assert state.read_bytes() == before
        # Later observation-only runs must not evict the actor's current bound
        # receipt from the semantic history retained outside the recent window.
        index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        with index.open("a") as stream:
            for number in range(80):
                stream.write(json.dumps({"goal_id": GOAL_ID, "agent_id": "codex-side-bypass",
                    "generated_at": f"2090-01-01T00:00:{number % 60:02d}+00:00",
                    "classification": "quota_slot_spent"}) + "\n")
        status = collect_status(registry_path=registry, runtime_root_override=str(runtime),
            scan_roots=[project], limit=2)
        decision = build_quota_should_run(status, goal_id=GOAL_ID, agent_id="codex-side-bypass",
            scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT)
        assert decision["should_run"] is True
        lane = decision["agent_lane_next_action"]
        assert lane["todo_id"] == "todo_side" and lane["next_step"] == step
        assert lane["text"] == "[P1] " + SIDE_AGENT_ACTION
        assert decision["recommended_action"] == step
        assert step in decision["interaction_contract"]["agent_channel"]["primary_action"]
        from loopx.extensions.lark.presentation.projection_rows import projection_rows_from_payload
        _, rows, warnings = projection_rows_from_payload(decision,
            goal_id=GOAL_ID, agent_id="codex-side-bypass", source_id="quota",
            include_done=False, limit=100)
        assert not warnings
        assert any(row["text"] == step and row["original_todo_id"] == "todo_side" for row in rows)
        from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
        assert "agent_lane_next_step: " + step in render_quota_should_run_markdown(decision)
        command = [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", "status",
            "--goal-id", GOAL_ID, "--agent-id", "codex-side-bypass"]
        readback = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
        assert readback.returncode == 0, readback.stdout + readback.stderr
        item = next(item for item in json.loads(readback.stdout)["attention_queue"]["items"] if item["goal_id"] == GOAL_ID)
        projected = item.get("agent_lane_next_action") or item["project_asset"]["agent_lane_next_action"]
        assert projected["next_step"] == step
        assert projected["next_action_basis"] == lane["next_action_basis"]
        assert "next_action_basis" not in item  # the selected route owns the basis
        markdown = subprocess.run([*command[:command.index("--format")], "--format", "markdown",
            *command[command.index("--format") + 2:]], cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
        assert markdown.returncode == 0 and "next_step: " + step in markdown.stdout
        # Display metadata cannot complete the authoritative selected task.
        state.write_text(state.read_text().replace("todo_side status=open", "todo_side status=done"))
        assert state.read_bytes() != before
        spoofed = collect_status(registry_path=registry, runtime_root_override=str(runtime), scan_roots=[project], limit=2)
        spoofed_decision = build_quota_should_run(spoofed, goal_id=GOAL_ID, agent_id="codex-side-bypass",
            scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT)
        assert spoofed_decision["agent_lane_next_action"]["todo_id"] == "todo_side"
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID) == original
        state.write_bytes(before)

        # Complete through the real owner: another selected task must not
        # inherit the original task's retained recommendation receipt.
        complete_goal_todo(registry_path=registry, goal_id=GOAL_ID,
            todo_id="todo_side", agent_id="codex-side-bypass", evidence="Scoped fixture work verified.")
        completed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)
        before_rows = {row["todo_id"]: row for row in original["todos"]}
        after_rows = {row["todo_id"]: row for row in completed["todos"]}
        assert after_rows["todo_side"]["status"] == "done"
        assert after_rows["todo_side"]["claimed_by"] == before_rows["todo_side"]["claimed_by"]
        assert after_rows["todo_primary"] == before_rows["todo_primary"]
        changed = collect_status(registry_path=registry, runtime_root_override=str(runtime), scan_roots=[project], limit=2)
        next_decision = build_quota_should_run(changed, goal_id=GOAL_ID, agent_id="codex-side-bypass",
            scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT)
        assert step not in next_decision["interaction_contract"]["agent_channel"]["primary_action"]


def main() -> None:
    for provider in ("file", "sqlite"):
        assert_bound_projection(provider)
    print("next-action-projection-contract-smoke ok")


if __name__ == "__main__":
    main()
