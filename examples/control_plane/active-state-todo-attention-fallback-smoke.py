#!/usr/bin/env python3
"""Smoke-test active-state todo attention fallback for passive adapter runs."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import loopx.state_refresh as state_refresh  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.status import collect_status  # noqa: E402


GOAL_ID = "active-state-todo-attention-fallback-goal"
USER_TODO = (
    "Review OpenViking PR #2792; CI is green/skipped as expected, "
    "and it is intentionally not merged until owner review."
)
PRIORITIZED_USER_TODO = f"[P1] {USER_TODO}"


def write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "owner_mode: goal\n"
        'objective: "Keep user todo gates visible after passive refresh runs."\n'
        "updated_at: 2026-06-23T00:00:00+00:00\n"
        "---\n\n"
        "# Active State Todo Attention Fallback Fixture\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        f"- [ ] [P1] {USER_TODO}\n"
        "  <!-- loopx:todo todo_id=todo_review status=open task_class=user_gate -->\n\n"
        "## Agent Todo\n\n"
        "- [x] Push and merge the internal ovtest follow-up.\n"
        "  <!-- loopx:todo todo_id=todo_internal status=done task_class=advancement_task -->\n\n"
        "## Next Action\n\n"
        f"- {USER_TODO}\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-06-23T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "loopx",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "lark-kanban", "status": "connected-read-only"},
                        "authority_sources": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime, project, state_path


def main() -> None:
    original_now_local = state_refresh.now_local
    try:
        with tempfile.TemporaryDirectory(prefix="loopx-active-state-todo-fallback-") as raw_tmp:
            registry_path, runtime, project, _state_path = write_fixture(Path(raw_tmp))

            state_refresh.now_local = lambda: "2026-06-23T00:01:00+00:00"
            payload = state_refresh.refresh_state_run(
                registry_path=registry_path,
                runtime_root_override=str(runtime),
                goal_id=GOAL_ID,
                project=project,
                state_file=None,
                classification="state_refreshed",
                recommended_action="Run the next passive read-only adapter tick.",
                dry_run=False,
                sync_global=False,
            )
            assert payload["classification"] == "state_refreshed", payload

            status = collect_status(
                registry_path=registry_path,
                runtime_root_override=str(runtime),
                limit=5,
            )
            items = [
                item
                for item in status["attention_queue"]["items"]
                if item.get("goal_id") == GOAL_ID
            ]
            assert len(items) == 1, status["attention_queue"]
            item = items[0]
            assert item["source"] == "active_state", item
            assert item["status"] == "active_state_user_gate", item
            assert item["waiting_on"] == "controller", item
            assert item["recommended_action"] == PRIORITIZED_USER_TODO, item
            assert item["active_state_next_action"] == USER_TODO, item
            assert item["latest_run_recommended_action"] == "Run the next passive read-only adapter tick.", item
            assert item["user_todos"]["open_count"] == 1, item
            assert item["user_todos"]["first_open_items"][0]["todo_id"] == "todo_review", item
            assert item["user_todos"]["first_open_items"][0]["text"] == PRIORITIZED_USER_TODO, item
            assert item["project_asset"]["user_todos"]["open"] == 1, item

            indexed = [
                todo
                for todo in status["todo_index"]["items"]
                if todo.get("goal_id") == GOAL_ID and todo.get("todo_id") == "todo_review"
            ]
            assert len(indexed) == 1, status["todo_index"]
            assert indexed[0]["status"] == "open", indexed[0]
            assert indexed[0]["source"] == "attention_queue", indexed[0]

            decision = build_quota_should_run(status, goal_id=GOAL_ID)
            assert decision["requires_user_action"] is True, decision
            assert decision["interaction_contract"]["user_channel"]["action_required"] is True, decision
            assert decision["user_todo_summary"]["open_count"] == 1, decision
    finally:
        state_refresh.now_local = original_now_local

    print("active-state-todo-attention-fallback-smoke ok")


if __name__ == "__main__":
    main()
