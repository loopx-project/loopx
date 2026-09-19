#!/usr/bin/env python3
"""Runtime fixture for active-state user-todo gate notification.

This smoke covers the public CLI path rather than only helper functions:

1. A sanitized registered goal has a refreshed runtime record.
2. Registry attention projects an owner/controller gate from the active state.
3. The active state exposes one open user todo.
4. `quota should-run` emits a no-spend operator-gate notification signal.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "focus-wait-owner-blocker"
USER_TODO = "Provide new owner evidence, a clean baseline, or external eval before delivery resumes."


def write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    runtime_root = root / "runtime"
    project = root / "project"
    state_file = project / "ACTIVE_GOAL_STATE.md"
    registry = root / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "\n".join(
            [
                "---",
                "status: active-read-only",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "# Focus Wait Owner Blocker",
                "",
                "## Objective",
                "",
                "Keep this fixture public-safe.",
                "",
                "## User Todo / Owner Review Reading Queue",
                "",
                f"- [ ] {USER_TODO}",
                "  <!-- loopx:todo todo_id=todo_owner_evidence_gate status=open task_class=user_gate action_kind=owner_evidence_gate -->",
                "",
                "## Next Action",
                "",
                "- Stay quiet until owner evidence changes.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    registry.write_text(
        json.dumps(
            {
                "runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "focus-wait-blocker-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": "ACTIVE_GOAL_STATE.md",
                        "waiting_on": "codex",
                        "attention_status": "focus_wait",
                        "recommended_action": "Stay quiet until owner evidence changes.",
                        "next_handoff_condition": (
                            "resume only after owner evidence, a clean baseline, or external eval changes"
                        ),
                        "adapter": {"kind": "generic_project_goal_v0", "status": "connected-read-only"},
                        "quota": {"compute": 1.0},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    append_state_refreshed_run(runtime_root)
    return registry, runtime_root, project, state_file


def append_state_refreshed_run(runtime_root: Path) -> None:
    run_dir = runtime_root / "goals" / GOAL_ID / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    generated_at = "2026-01-01T00:01:00+00:00"
    compact_time = generated_at.replace("-", "").replace(":", "")
    json_path = run_dir / f"{compact_time}-state-refreshed.json"
    markdown_path = run_dir / f"{compact_time}-state-refreshed.md"
    record = {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": "state_refreshed",
        "recommended_action": "Stay quiet until owner evidence changes.",
        "health_check": "fixture state refresh",
    }
    json_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text("# Fixture focus-wait state refresh\n", encoding="utf-8")
    with (run_dir / "index.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    **record,
                    "json_path": str(json_path),
                    "markdown_path": str(markdown_path),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def run_cli(registry: Path, runtime_root: Path, *args: str) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-blocker-push-runtime-") as tmp:
        root = Path(tmp)
        registry, runtime_root, project, state_file = write_fixture(root)

        status_payload = run_cli(registry, runtime_root, "status", "--scan-root", str(project))
        items = status_payload["attention_queue"]["items"]
        assert len(items) == 1, items
        item = items[0]
        assert item["goal_id"] == GOAL_ID, item
        assert item["status"] == "active_state_user_gate", item
        assert item["waiting_on"] == "controller", item
        assert item["severity"] == "action", item
        assert item["recommended_action"] == USER_TODO, item
        project_asset = item["project_asset"]
        assert project_asset["gate"] == "active_state_user_gate", item
        assert project_asset["support_mode"] == "decision_support", item
        assert project_asset["quota"]["state"] == "operator_gate", item
        assert item["user_todos"]["open_count"] == 1, item
        assert item["user_todos"]["items"][0]["text"] == USER_TODO, item

        decision = run_cli(
            registry,
            runtime_root,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--scan-root",
            str(project),
        )
        assert decision["decision"] == "skip", decision
        assert decision["should_run"] is False, decision
        assert decision["state"] == "operator_gate", decision
        assert decision["effective_action"] == "operator_gate_notify", decision
        assert decision["requires_user_action"] is True, decision
        assert "notify_user_on_open_todo" not in decision, decision
        assert decision["notify_user_on_gate"] is True, decision
        assert decision["open_todo_notification_policy"] == "repeat_until_resolved", decision
        assert "owner_evidence_gate" in decision["open_todo_notify_reason"], decision
        assert decision["heartbeat_recommendation"]["recommended_mode"] == "ask_operator_gate", decision
        assert decision["heartbeat_recommendation"]["notify"] == "NOTIFY", decision
        assert decision["interaction_contract"]["mode"] == "user_gate", decision
        assert decision["interaction_contract"]["user_channel"]["action_required"] is True, decision
        assert decision["interaction_contract"]["user_channel"]["notify"] == "NOTIFY", decision
        assert decision["interaction_contract"]["agent_channel"]["must_attempt"] is False, decision
        assert decision["interaction_contract"]["agent_channel"]["delivery_allowed"] is False, decision
        assert decision["interaction_contract"]["cli_channel"]["spend_policy"] == (
            "no spend for gate or blocker push"
        ), decision
        assert decision["user_todo_summary"]["open_count"] == 1, decision
        assert decision["user_todo_summary"]["first_open_items"][0]["text"] == USER_TODO, decision
        assert decision["safe_bypass_allowed"] is True, decision
        assert decision["blocked_action_scope"] == "gated_delivery", decision
        assert "agent_command" not in decision, decision

        prompt = run_cli(
            registry,
            runtime_root,
            "heartbeat-prompt",
            "--goal-id",
            GOAL_ID,
            "--active-state",
            str(state_file),
        )["task_body"]
        compact_prompt = " ".join(prompt.split())
        assert "state=operator_gate" not in compact_prompt, prompt
        assert "Normal turns use CLI `interaction_contract`" in compact_prompt, prompt
        assert "`user_channel.notify` controls OUTPUT only" in compact_prompt, prompt
        assert "NOTIFY=向用户输出动作; DONT_NOTIFY=安静输出" in compact_prompt, prompt
        assert "Due/peer非用户动作" in compact_prompt, prompt
        assert "NOTIFY缺动作→具体user todo未投影" in compact_prompt, prompt
        # The bootstrap rule is shared from heartbeat.rules after #4201; assert the
        # current compact sentence instead of the retired per-shell phrasing.
        assert "reuse the value on retries" in compact_prompt, prompt
        assert "guard; 2 stalls->replan" in compact_prompt, prompt
        assert "no-change=surface_only/no spend" in compact_prompt, prompt
        # The shipped writeback sentence moved to the mixed-language form in the
        # same change that updated examples/control_plane/heartbeat-prompt-smoke.py;
        # this assertion keeps the blocker-runtime path pinned to the same text.
        assert "unchanged→真实--vision-unchanged-reason" in compact_prompt, prompt

    print("blocker-push-runtime-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
