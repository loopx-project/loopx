#!/usr/bin/env python3
"""Smoke-test goal_channel_projection_v0 in the status JSON feed."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.status import collect_status  # noqa: E402


GOAL_ID = "goal-channel-status-export"
PRIVATE_LOG_PATH = "/" + "Users/example/private-run.log"


def write_state(project: Path) -> str:
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    path = project / state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-06-20T08:00:00+00:00\n"
        "---\n\n"
        "# Goal Channel Status Export\n\n"
        "## Next Action\n\n"
        "[P2] Export the read-only goal channel projection through status.\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "- [ ] [P0] Decide whether the gated route can continue.\n"
        "  <!-- loopx:todo todo_id=todo_user_gate status=open task_class=user_gate -->\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P2] Publish the status-feed projection without write authority.\n"
        "  <!-- loopx:todo todo_id=todo_agent_projection status=open task_class=advancement_task action_kind=goal_channel_status_export claimed_by=codex-side-bypass -->\n",
        encoding="utf-8",
    )
    return state_file


def write_registry(project: Path, runtime: Path) -> Path:
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-06-20T08:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "status-export-smoke",
                        "status": "active",
                        "repo": str(project),
                        "state_file": write_state(project),
                        "adapter": {"kind": "smoke_v0", "status": "connected"},
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
    return registry_path


def write_run_history(runtime: Path) -> None:
    run_dir = runtime / "goals" / GOAL_ID / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "goal_id": GOAL_ID,
        "generated_at": "2026-06-20T08:01:00+00:00",
        "classification": "validated_progress",
        "health_check": "status export projection remained read-only",
        "raw_log_path": PRIVATE_LOG_PATH,
    }
    (run_dir / "index.jsonl").write_text(
        json.dumps(run, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-goal-channel-status-") as tmp:
        root = Path(tmp)
        project = root / "project"
        runtime = root / "runtime"
        registry_path = write_registry(project, runtime)
        write_run_history(runtime)
        payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(runtime),
            limit=5,
        )

    assert payload["ok"] is True, payload
    item = payload["attention_queue"]["items"][0]
    projection = item["goal_channel_projection"]
    projection_text = json.dumps(projection, ensure_ascii=False, sort_keys=True)

    assert projection["schema_version"] == "goal_channel_projection_v0", projection
    assert projection["mode"] == "read_only", projection
    assert projection["goal_id"] == GOAL_ID, projection
    assert projection["truth_contract"]["projection_is_writable"] is False, projection
    assert projection["truth_contract"]["event_ledger_is_source_of_truth"] is True, projection
    assert projection["user_todos"][0]["todo_id"] == "todo_user_gate", projection
    assert projection["decision_frame"]["user_action_required"] is True, projection
    assert projection["open_gates"][0]["kind"] == "user_todo", projection
    assert projection["open_gates"][0]["blocks"] == ["todo_user_gate"], projection
    assert projection["agent_todos"][0]["claimed_by"] == "codex-side-bypass", projection
    assert projection["active_leases"][0]["owner_agent"] == "codex-side-bypass", projection
    assert projection["recent_events"][0]["classification"] == "validated_progress", projection
    assert projection["source_warnings"], projection

    assert PRIVATE_LOG_PATH not in projection_text, projection
    assert str(root) not in projection_text, projection
    assert "write_authority" in projection["truth_contract"], projection
    print("goal-channel-status-export-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
