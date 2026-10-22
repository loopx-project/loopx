#!/usr/bin/env python3
"""Smoke-test the explicit one-shot LoopX state migration path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OLD_GOAL_ID = "goal-harness-meta"
NEW_GOAL_ID = "loopx-meta"
OTHER_GOAL_ID = "agent-harness-side-bypass"


def run_loopx(*args: str, cwd: Path, env: dict[str, str]) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-state-migration-smoke-") as tmp:
        root = Path(tmp)
        legacy_project = root / "legacy-project"
        target_project = root / "target-project"
        legacy_runtime = root / "legacy-runtime"
        target_runtime = root / "target-runtime"
        legacy_project.mkdir()
        target_project.mkdir()
        target_registry = target_project / ".loopx" / "registry.json"
        legacy_registry = legacy_runtime / "registry.global.json"
        legacy_state = legacy_project / ".local" / "goals" / OLD_GOAL_ID / "ACTIVE_GOAL_STATE.md"
        legacy_state.parent.mkdir(parents=True)
        legacy_state.write_text(
            "# Goal Harness State\n\n"
            "Next: run goal-harness quota should-run for goal-harness-meta.\n\n"
            "## Agent Todo\n",
            encoding="utf-8",
        )
        other_legacy_state = legacy_project / ".local" / "goals" / OTHER_GOAL_ID / "ACTIVE_GOAL_STATE.md"
        other_legacy_state.parent.mkdir(parents=True)
        other_legacy_state.write_text("# Other State\n\n## Agent Todo\n", encoding="utf-8")
        legacy_run_dir = legacy_runtime / "goals" / OLD_GOAL_ID / "runs"
        legacy_run_dir.mkdir(parents=True)
        write_json(
            legacy_run_dir / "2026-01-01T00-00-00-legacy.json",
            {
                "goal_id": OLD_GOAL_ID,
                "classification": "legacy_goal_harness_fixture",
                "recommended_action": "continue Goal Harness migration",
            },
        )
        (legacy_runtime / "goals" / OLD_GOAL_ID / "rollout-event-log.jsonl").write_text(
            json.dumps(
                {
                    "schema_version": "loopx_rollout_event_v0",
                    "event_id": "legacy-quota-should-run",
                    "event_kind": "quota_should_run",
                    "recorded_at": "2026-01-01T00:00:00+00:00",
                    "status": "normal_run",
                    "summary": "legacy quota guard completed",
                    "goal_id": OLD_GOAL_ID,
                    "details": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        write_json(
            legacy_registry,
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(legacy_runtime),
                "registry_role": "global-local",
                "goals": [
                    {
                        "id": OLD_GOAL_ID,
                        "domain": "goal-harness-platform",
                        "status": "active-read-only",
                        "role": "controller",
                        "repo": str(legacy_project),
                        "state_file": ".local/goals/goal-harness-meta/ACTIVE_GOAL_STATE.md",
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": ["codex-main-control", "codex-side-bypass"],
                            "agent_model": "peer_v1",
                            "write_scope": [],
                            "claim_ttl_minutes": 30,
                            "requires_parent_approval": ["write", "publish"],
                        },
                        "quota": {"compute": 1.0, "window_hours": 24},
                        "next_probe": "goal-harness --format json quota should-run --goal-id goal-harness-meta",
                        "source_registry": str(legacy_project / ".goal-harness" / "registry.json"),
                    },
                    {
                        "id": OTHER_GOAL_ID,
                        "domain": "agent-harness",
                        "status": "active-read-only",
                        "role": "subagent",
                        "repo": str(legacy_project),
                        "state_file": f".local/goals/{OTHER_GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "adapter": {"kind": "generic", "status": "connected-read-only"},
                    },
                ],
            },
        )

        env = {
            **os.environ,
            "PYTHONPATH": f"{REPO_ROOT}:{os.environ.get('PYTHONPATH', '')}",
        }
        common_args = [
            "--registry",
            str(target_registry),
            "--runtime-root",
            str(target_runtime),
            "migrate-state",
            "--legacy-registry",
            str(legacy_registry),
            "--legacy-runtime-root",
            str(legacy_runtime),
            "--target-runtime-root",
            str(target_runtime),
            "--goal-id",
            OLD_GOAL_ID,
            "--goal-id-map",
            f"{OLD_GOAL_ID}={NEW_GOAL_ID}",
            "--path-map",
            f"{legacy_project}={target_project}",
            "--copy-active-state",
            "--copy-runtime",
        ]
        all_registry = target_project / ".loopx" / "registry-all.json"
        all_preview = run_loopx(
            "--registry",
            str(all_registry),
            "--runtime-root",
            str(target_runtime),
            "migrate-state",
            "--legacy-registry",
            str(legacy_registry),
            "--legacy-runtime-root",
            str(legacy_runtime),
            "--target-runtime-root",
            str(target_runtime),
            "--all-goals",
            "--copy-active-state",
            cwd=target_project,
            env=env,
        )
        assert all_preview["ok"] is True, all_preview
        assert all_preview["dry_run"] is True, all_preview
        assert all_preview["selected_goal_ids"] == [OLD_GOAL_ID, OTHER_GOAL_ID], all_preview
        assert all_preview["migrated_goal_ids"] == [NEW_GOAL_ID, OTHER_GOAL_ID], all_preview
        assert not all_registry.exists(), all_preview

        preview = run_loopx(*common_args, cwd=target_project, env=env)
        assert preview["ok"] is True, preview
        assert preview["dry_run"] is True, preview
        assert preview["migrated_goal_ids"] == [NEW_GOAL_ID], preview
        assert not target_registry.exists(), preview

        executed = run_loopx(*common_args, "--execute", cwd=target_project, env=env)
        assert executed["ok"] is True, executed
        assert executed["dry_run"] is False, executed
        assert executed["wrote_project_registry"] is True, executed
        assert executed["global_sync"]["synced_goal_ids"] == [NEW_GOAL_ID], executed

        registry = json.loads(target_registry.read_text(encoding="utf-8"))
        goal = registry["goals"][0]
        assert goal["id"] == NEW_GOAL_ID, goal
        assert goal["repo"] == str(target_project), goal
        assert goal["state_file"] == ".local/goals/loopx-meta/ACTIVE_GOAL_STATE.md", goal
        assert "goal-harness" not in goal["next_probe"], goal
        assert "loopx-meta" in goal["next_probe"], goal

        migrated_state = target_project / ".local" / "goals" / NEW_GOAL_ID / "ACTIVE_GOAL_STATE.md"
        state_text = migrated_state.read_text(encoding="utf-8")
        assert "LoopX State" in state_text, state_text
        assert OLD_GOAL_ID not in state_text, state_text
        assert "loopx quota should-run" in state_text, state_text

        migrated_run = target_runtime / "goals" / NEW_GOAL_ID / "runs" / "2026-01-01T00-00-00-legacy.json"
        migrated_run_payload = json.loads(migrated_run.read_text(encoding="utf-8"))
        assert migrated_run_payload["goal_id"] == NEW_GOAL_ID, migrated_run_payload
        assert "goal-harness" not in migrated_run_payload["recommended_action"], migrated_run_payload

        status = run_loopx(
            "--registry",
            str(target_registry),
            "--runtime-root",
            str(target_runtime),
            "status",
            cwd=target_project,
            env=env,
        )
        assert status["ok"] is True, status
        status_goal_ids = {
            item.get("goal_id")
            for item in status.get("attention_queue", {}).get("items", [])
            if isinstance(item, dict)
        }
        assert NEW_GOAL_ID in status_goal_ids, status

        quota = run_loopx(
            "--registry",
            str(target_registry),
            "--runtime-root",
            str(target_runtime),
            "quota",
            "should-run",
            "--goal-id",
            NEW_GOAL_ID,
            "--agent-id",
            "codex-side-bypass",
            cwd=target_project,
            env=env,
        )
        assert quota["goal_id"] == NEW_GOAL_ID, quota
        assert quota.get("status") != "goal_not_found", quota

    print("state-migration-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
