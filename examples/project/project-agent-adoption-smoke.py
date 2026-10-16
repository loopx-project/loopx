#!/usr/bin/env python3
"""Smoke-test the project-agent LoopX adoption path.

This fixture proves the executor-facing loop, not just isolated helpers:

1. `quota should-run` exposes a concrete todo write hint.
2. A project agent can use typed `loopx todo add --role user`.
3. `status` projects that user todo into the attention queue.
4. After an approved operator gate, `review-packet` gives only the short,
   goal-guarded approved handoff to the target agent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402

GOAL_ID = "adoption-main-control"
USER_TODO = "[P1] Review the P0 owner-risk checklist before approving delivery."
AGENT_TODO = "Run the read-only map dry-run after the operator approval is recorded."
APPROVED_COMMAND = f"loopx read-only-map --goal-id {GOAL_ID} --dry-run"
QUOTA_EXECUTION_CONTEXT = (
    "--host-surface",
    "generic_cli",
    "--scheduler-owner",
    "outer_controller",
    "--execution-mode",
    "isolated_headless",
)


def write_planned_fixture(root: Path) -> Path:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: planned-high-complexity\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Adoption Main Control\n\n"
        "## Objective\n\n"
        "Keep this fixture public-safe.\n\n"
        "## Next Action\n\n"
        "- Analyze the P0 owner-risk scene.\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {AGENT_TODO}\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "adoption-fixture",
                        "status": "planned-high-complexity",
                        "repo": str(project),
                        "state_file": f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md",
                        "adapter": {
                            "kind": "complex_project_read_only_map_v0",
                            "status": "planned",
                        },
                        "authority_sources": [],
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
    return registry_path


def run_cli(root: Path, registry_path: Path, *args: str, check: bool = True) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(root / "runtime"),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def quota_should_run(
    root: Path,
    registry_path: Path,
    project: Path,
    *,
    bind_execution_context: bool = True,
) -> dict:
    context_args = QUOTA_EXECUTION_CONTEXT if bind_execution_context else ()
    return run_cli(
        root,
        registry_path,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        *context_args,
        "--scan-root",
        str(project),
    )


def attention_item(status_payload: dict) -> dict:
    items = status_payload["attention_queue"]["items"]
    assert len(items) == 1, items
    return items[0]


def assert_adoption(provider: str) -> None:
    with tempfile.TemporaryDirectory(prefix=f"loopx-project-agent-adoption-{provider}-") as tmp:
        root = Path(tmp)
        registry_path = write_planned_fixture(root)
        project = root / "project"

        missing_context_quota = quota_should_run(
            root,
            registry_path,
            project,
            bind_execution_context=False,
        )
        missing_hint = missing_context_quota["scheduler_hint"]
        assert missing_hint["action"] == "repair_scheduler_execution_context", missing_hint
        assert missing_hint["execution_context"]["valid"] is False, missing_hint
        assert missing_hint["execution_context"]["errors"] == [
            "missing required field: host_surface",
            "missing required field: scheduler_owner",
            "missing required field: execution_mode",
        ], missing_hint

        first_quota = quota_should_run(root, registry_path, project)
        assert first_quota["state"] == "operator_gate", first_quota
        assert first_quota["should_run"] is False, first_quota
        first_context = first_quota["scheduler_hint"]["execution_context"]
        assert first_context["valid"] is True, first_context
        assert first_context["host_surface"] == "generic_cli", first_context
        assert first_context["scheduler_owner"] == "outer_controller", first_context
        assert first_context["execution_mode"] == "isolated_headless", first_context
        assert first_quota["scheduler_hint"].get("action") != (
            "repair_scheduler_execution_context"
        ), first_quota
        assert first_quota["todo_write_hint"]["section"] == "User Todo / Owner Review Reading Queue", first_quota
        assert first_quota["todo_write_hint"]["user_gate_command_template"] == (
            f"loopx todo add --goal-id {GOAL_ID} --role user --task-class user_gate "
            "--blocks-agent <agent-id> --text '<blocking user decision>'"
        ), first_quota
        assert first_quota["todo_write_hint"]["user_action_command_template"] == (
            f"loopx todo add --goal-id {GOAL_ID} --role user --task-class user_action "
            "--bound-agent <id> --text '<action>'"
        ), first_quota
        assert first_quota["agent_todo_summary"]["open_count"] == 1, first_quota
        assert first_quota["selected_todo"]["text"] == AGENT_TODO, first_quota

        add_gate = (
            "todo", "add", "--goal-id", GOAL_ID, "--role", "user",
            "--task-class", "user_gate", "--global-gate", "--text", USER_TODO,
        )
        state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
        before_state, before_registry = state.read_bytes(), registry_path.read_bytes()
        refused = run_cli(root, registry_path, *add_gate, check=False)
        assert refused["ok"] is False, refused
        assert refused["error_code"] == "canonical_authority_required", refused
        assert state.read_bytes() == before_state
        assert registry_path.read_bytes() == before_registry
        import_fixture_authority(registry_path, GOAL_ID, provider=provider)
        imported = run_cli(root, registry_path, "todo", "list", "--goal-id", GOAL_ID)
        assert imported["authority_read"]["source_authority"] == f"{provider}_v0", imported
        assert imported["authority_read"]["legacy_fallback_used"] is False, imported
        assert len(imported["todos"]) == 1, imported
        original = imported["todos"][0]
        assert original["text"] == AGENT_TODO and original["status"] == "open", original
        todo_payload = run_cli(root, registry_path, *add_gate)
        assert todo_payload["ok"] is True, todo_payload
        assert todo_payload["added"] is True, todo_payload
        gate_id = todo_payload["todo_id"]
        gate = run_cli(root, registry_path, "todo", "list", "--goal-id", GOAL_ID,
                       "--todo-id", gate_id)["todo"]
        assert gate["text"] == USER_TODO and gate["global_gate"] is True, gate

        status_after_todo = run_cli(root, registry_path, "status", "--scan-root", str(project))
        item_after_todo = attention_item(status_after_todo)
        assert item_after_todo["waiting_on"] == "controller", item_after_todo
        assert item_after_todo["user_todos"]["open_count"] == 1, item_after_todo
        assert item_after_todo["user_todos"]["items"][0]["text"] == USER_TODO, item_after_todo
        assert item_after_todo["agent_todos"]["open_count"] == 1, item_after_todo
        assert item_after_todo["agent_todos"]["items"][0]["text"] == AGENT_TODO, item_after_todo

        quota_after_todo = quota_should_run(root, registry_path, project)
        assert quota_after_todo["user_todo_summary"]["open_count"] == 1, quota_after_todo
        assert quota_after_todo["agent_todo_summary"]["open_count"] == 1, quota_after_todo
        assert USER_TODO in quota_after_todo["gate_prompt"], quota_after_todo

        gate_payload = run_cli(
            root,
            registry_path,
            "operator-gate",
            "--goal-id",
            GOAL_ID,
            "--decision",
            "approve",
            "--reason-summary",
            f"同意 {GOAL_ID} 先做 read-only map dry-run，不授权写入或生产动作",
            "--no-global-sync",
        )
        assert gate_payload["ok"] is True, gate_payload
        assert gate_payload["appended"] is True, gate_payload
        assert gate_payload["operator_gate"]["agent_command"] == APPROVED_COMMAND, gate_payload

        pending_quota = quota_should_run(root, registry_path, project)
        assert pending_quota["should_run"] is False, pending_quota
        assert pending_quota["state"] == "operator_gate", pending_quota
        assert pending_quota["user_todo_summary"]["open_count"] == 1, pending_quota
        assert pending_quota["effective_action"] == "operator_gate_notify", pending_quota

        complete_payload = run_cli(
            root,
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_payload["todo_id"],
            "--decision-outcome",
            "approve",
            "--evidence",
            "operator approval recorded for read-only dry-run",
        )
        assert complete_payload["changed"] is True, complete_payload

        approved_quota = quota_should_run(root, registry_path, project)
        assert approved_quota["should_run"] is True, approved_quota
        assert approved_quota["state"] == "eligible", approved_quota
        assert approved_quota["agent_command"] == APPROVED_COMMAND, approved_quota
        assert approved_quota["todo_write_hint"]["agent_todo_command_template"].startswith(
            f"loopx todo add --goal-id {GOAL_ID} --role agent "
        ), approved_quota
        assert "--priority P1" in approved_quota["todo_write_hint"]["agent_todo_command_template"], approved_quota

        packet = run_cli(root, registry_path, "review-packet", "--goal-id", GOAL_ID, "--scan-root", str(project))
        assert packet["ok"] is True, packet
        assert packet["kind"] == "codex", packet
        assert packet["operator_gate_approved_handoff"] is True, packet
        assert packet["project_agent_command"] == APPROVED_COMMAND, packet
        assert "operator gate 已记录为 approve" in packet["project_agent_handoff"], packet
        assert "不要从旧聊天或旧 packet 拼当前状态" in packet["project_agent_handoff"], packet
        assert "【用户本地 Gate 记录草稿】" not in packet["packet"], packet


def main() -> int:
    for provider in ("file", "sqlite"):
        assert_adoption(provider)
    print("project-agent-adoption-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
