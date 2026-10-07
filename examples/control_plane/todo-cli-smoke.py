#!/usr/bin/env python3
"""Smoke-test the agent-facing todo add command."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    run_json_cli,
    run_json_cli_result,
    write_fixture_registry,
)
from loopx.status import parse_active_state_todos  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402


GOAL_ID = "todo-cli-goal"
USER_TODO = "Review the owner decision checklist before approving delivery."
SCOPED_USER_GATE = "Choose the side-agent publishing channel before posting externally."
AGENT_TODO = "Summarize the read-only evidence after the user checklist is done."
AGENT_TODO_NOTE = "Start from the public-safe evidence summary."
UPDATED_AGENT_TODO_NOTE = "Preserve the validated summary as the handoff context."
UPDATED_AGENT_TODO = "Publish the compact evidence summary after validation passes."
MONITOR_TODO = "Monitor the release-note draft PR until the next scheduled check."
MONITOR_DUE_AT = "2026-01-02T00:00:00+00:00"
MONITOR_NEXT_DUE_AT = "2026-01-03T00:00:00+00:00"
MONITOR_EXPIRES_AT = "2026-01-04T00:00:00+00:00"


def write_fixture(root: Path, *, register_agents: bool = True) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Active Goal State\n\n"
        "## Objective\n\n"
        "Keep this fixture small.\n\n"
        "## Next Action\n\n"
        "- Choose the next bounded step.\n",
        encoding="utf-8",
    )
    write_fixture_registry(
        project=project,
        runtime_root=runtime,
        registry_path=registry_path,
        goal_id=GOAL_ID,
        domain="todo-cli-fixture",
        adapter_kind="generic_project_goal_v0",
        registered_agents=("codex-main-control", "codex-side-bypass")
        if register_agents
        else None,
        quota_allowed_slots=None,
    )
    return registry_path, state_file


def run_cli(registry_path: Path, *args: str) -> dict:
    return run_json_cli(
        *args,
        registry_path=registry_path,
        include_returncode=False,
    )


def run_cli_error(registry_path: Path, *args: str) -> dict:
    returncode, payload = run_json_cli_result(*args, registry_path=registry_path)
    assert returncode != 0, payload
    payload.pop("_returncode", None)
    return payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-cli-smoke-") as tmp:
        root = Path(tmp)
        registry_path, state_file = write_fixture(root)
        legacy_registry_path, _ = write_fixture(root / "legacy", register_agents=False)
        claim_state_registry_path, claim_state_file = write_fixture(root / "claim-state")
        original = state_file.read_text(encoding="utf-8")

        blocked_todo = run_cli(
            claim_state_registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "Reopen blocked work before assigning a new soft owner.",
            "--status",
            "blocked",
            "--task-class",
            "advancement_task",
        )
        before_rejected_claim = claim_state_file.read_text(encoding="utf-8")
        blocked_claim = run_cli_error(
            claim_state_registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            blocked_todo["todo_id"],
            "--claimed-by",
            "codex-main-control",
            "--agent-id",
            "codex-main-control",
        )
        assert blocked_claim["error"] == (
            f"todo claim requires status=open; todo_id '{blocked_todo['todo_id']}' "
            "is status='blocked'"
        ), blocked_claim
        assert claim_state_file.read_text(encoding="utf-8") == before_rejected_claim

        bare_user_error = run_cli_error(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--text",
            USER_TODO,
            "--dry-run",
        )
        assert "user todo requires explicit --task-class" in bare_user_error["error"], bare_user_error

        agent_user_gate_error = run_cli_error(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--task-class",
            "user_gate",
            "--text",
            AGENT_TODO,
            "--dry-run",
        )
        assert (
            "user_action and user_gate task_class are only valid for --role user"
            in agent_user_gate_error["error"]
        ), agent_user_gate_error

        user_action_gate_error = run_cli_error(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--task-class",
            "user_action",
            "--global-gate",
            "--text",
            USER_TODO,
            "--dry-run",
        )
        assert "user_action is non-blocking" in user_action_gate_error["error"], user_action_gate_error

        dry_run = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--task-class",
            "user_gate",
            "--global-gate",
            "--text",
            USER_TODO,
            "--dry-run",
        )
        assert dry_run["ok"] is True, dry_run
        assert dry_run["added"] is True, dry_run
        assert state_file.read_text(encoding="utf-8") == original

        user_payload = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--task-class",
            "user_gate",
            "--global-gate",
            "--text",
            USER_TODO,
        )
        assert user_payload["added"] is True, user_payload
        after_user = state_file.read_text(encoding="utf-8")
        assert "## User Todo / Owner Review Reading Queue" in after_user, after_user
        assert f"- [ ] {USER_TODO}" in after_user, after_user
        assert after_user.index("## User Todo / Owner Review Reading Queue") < after_user.index("## Next Action")
        assert "updated_at: 2026-01-01T00:00:00+00:00" not in after_user

        duplicate = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--task-class",
            "user_gate",
            "--global-gate",
            "--text",
            USER_TODO,
        )
        assert duplicate["added"] is False, duplicate
        assert duplicate["already_exists"] is True, duplicate
        assert state_file.read_text(encoding="utf-8").count(USER_TODO) == 1

        scoped_gate = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "user",
            "--text",
            SCOPED_USER_GATE,
            "--task-class",
            "user_gate",
            "--action-kind",
            "approve_publish_channel",
            "--agent-id",
            "codex-side-bypass",
        )
        assert scoped_gate["added"] is True, scoped_gate
        assert scoped_gate["agent_id"] == "codex-side-bypass", scoped_gate
        assert scoped_gate["blocks_agent"] == "codex-side-bypass", scoped_gate

        agent_payload = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            AGENT_TODO,
            "--note",
            AGENT_TODO_NOTE,
        )
        assert agent_payload["added"] is True, agent_payload
        assert agent_payload["note"] == AGENT_TODO_NOTE, agent_payload
        added_fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        assert added_fields["agent_todos"]["items"][0]["note"] == AGENT_TODO_NOTE, added_fields
        legacy_agent = run_cli(
            legacy_registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            AGENT_TODO,
        )
        # Omit-note negative (GH-C97): an add without --note must not invent a note.
        assert legacy_agent["added"] is True, legacy_agent
        assert not legacy_agent.get("note"), legacy_agent
        legacy_claim = run_cli_error(
            legacy_registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            legacy_agent["todo_id"],
            "--claimed-by",
            "codex-main-control",
        )
        assert "has no coordination.registered_agents" in legacy_claim["error"], legacy_claim
        assert "Register this peer identity first" in legacy_claim["error"], legacy_claim
        assert "--registered-agent codex-main-control" in legacy_claim["error"], legacy_claim
        wrapped_agent_todo = "- [ ] Summarize the read-only evidence after the user\n  checklist is done."
        state_file.write_text(
            state_file.read_text(encoding="utf-8").replace(f"- [ ] {AGENT_TODO}", wrapped_agent_todo),
            encoding="utf-8",
        )
        metadata_payload = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            AGENT_TODO,
            "--task-class",
            "advancement_task",
            "--action-kind",
            "run_eval",
            "--note",
            UPDATED_AGENT_TODO_NOTE,
            "--continuation-policy",
            "independent_handoff",
            "--required-capability",
            "shell",
            "--required-capability",
            "benchmark_runner",
            "--target-capability",
            "benchmark_runner",
            "--required-decision-scope",
            "direction:action:read_only_evidence_review",
        )
        assert metadata_payload["added"] is False, metadata_payload
        assert metadata_payload["already_exists"] is True, metadata_payload
        assert metadata_payload["metadata_updated"] is True, metadata_payload
        assert metadata_payload["task_class"] == "advancement_task", metadata_payload
        assert metadata_payload["action_kind"] == "run_eval", metadata_payload
        assert metadata_payload["note"] == UPDATED_AGENT_TODO_NOTE, metadata_payload
        assert (
            metadata_payload["continuation_policy"] == "independent_handoff"
        ), metadata_payload
        assert metadata_payload["required_capabilities"] == ["shell", "benchmark_runner"], metadata_payload
        assert metadata_payload["target_capabilities"] == ["benchmark_runner"], metadata_payload
        assert metadata_payload["required_decision_scopes"] == [
            {
                "schema_version": "decision_scope_v0",
                "kind": "direction",
                "granularity": "action",
                "scope_key": "read_only_evidence_review",
            }
        ], metadata_payload
        after_metadata = state_file.read_text(encoding="utf-8")
        assert after_metadata.count("- [ ] Summarize the read-only evidence after the user") == 1, after_metadata
        agent_block_start = after_metadata.index("- [ ] Summarize the read-only evidence after the user")
        agent_metadata_start = after_metadata.index("<!-- loopx:todo", agent_block_start)
        assert after_metadata.index("checklist is done.", agent_block_start) < agent_metadata_start, after_metadata
        assert "status=open task_class=advancement_task action_kind=run_eval" in after_metadata
        assert "continuation_policy=independent_handoff" in after_metadata
        assert "required_capabilities=shell%2Cbenchmark_runner" in after_metadata
        assert "target_capabilities=benchmark_runner" in after_metadata
        assert (
            "required_decision_scopes=direction:action:read_only_evidence_review"
            in after_metadata
        )
        fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        assert fields["user_todos"]["items"][0]["text"] == USER_TODO, fields
        assert fields["user_todos"]["items"][0]["todo_id"].startswith("todo_"), fields
        scoped_user_item = next(
            item for item in fields["user_todos"]["items"] if item["text"] == SCOPED_USER_GATE
        )
        assert scoped_user_item["task_class"] == "user_gate", fields
        assert scoped_user_item["blocks_agent"] == "codex-side-bypass", fields
        assert fields["agent_todos"]["items"][0]["text"] == AGENT_TODO, fields
        assert fields["agent_todos"]["items"][0]["todo_id"].startswith("todo_"), fields
        assert fields["agent_todos"]["items"][0]["task_class"] == "advancement_task", fields
        assert fields["agent_todos"]["items"][0]["action_kind"] == "run_eval", fields
        assert fields["agent_todos"]["items"][0]["note"] == UPDATED_AGENT_TODO_NOTE, fields
        assert fields["agent_todos"]["items"][0]["required_capabilities"] == [
            "shell",
            "benchmark_runner",
        ], fields
        assert fields["agent_todos"]["items"][0]["target_capabilities"] == [
            "benchmark_runner",
        ], fields
        assert fields["agent_todos"]["items"][0]["required_decision_scopes"][0]["scope_key"] == (
            "read_only_evidence_review"
        ), fields
        scoped_gate_scope = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            scoped_gate["todo_id"],
            "--agent-id",
            "codex-side-bypass",
            "--decision-scope",
            "direction:lane:external_publish_channel",
        )
        assert scoped_gate_scope["changed"] is True, scoped_gate_scope
        assert scoped_gate_scope["decision_scope"]["scope_key"] == "external_publish_channel", scoped_gate_scope
        fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        scoped_user_item = next(
            item for item in fields["user_todos"]["items"] if item["text"] == SCOPED_USER_GATE
        )
        assert scoped_user_item["decision_scope"]["granularity"] == "lane", scoped_user_item
        assert fields["user_todos"]["source_section"] == "User Todo / Owner Review Reading Queue", fields
        assert fields["agent_todos"]["source_section"] == "Agent Todo", fields

        default_list = run_cli(registry_path, "todo", "list", "--goal-id", GOAL_ID)
        assert default_list["todo_count"] == 3, default_list
        assert "todo_id_filter" not in default_list, default_list
        assert "matched" not in default_list, default_list
        assert "relations" not in default_list, default_list

        scoped_gate_lookup = run_cli(
            registry_path,
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            scoped_gate["todo_id"],
        )
        assert scoped_gate_lookup["ok"] is True, scoped_gate_lookup
        assert scoped_gate_lookup["todo_id_filter"] == scoped_gate["todo_id"], scoped_gate_lookup
        assert scoped_gate_lookup["todo_count"] == 1, scoped_gate_lookup
        assert scoped_gate_lookup["matched"] is True, scoped_gate_lookup
        assert scoped_gate_lookup["todo"]["todo_id"] == scoped_gate["todo_id"], scoped_gate_lookup
        assert scoped_gate_lookup["relations"]["blocks_agent"] == "codex-side-bypass", scoped_gate_lookup
        assert scoped_gate_lookup["relations"]["decision_scope"]["scope_key"] == (
            "external_publish_channel"
        ), scoped_gate_lookup
        assert scoped_gate_lookup["todo"]["role"] == "user", scoped_gate_lookup
        assert scoped_gate_lookup["todo"]["status"] == "open", scoped_gate_lookup

        agent_lookup = run_cli(
            registry_path,
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--todo-id",
            metadata_payload["todo_id"],
        )
        assert agent_lookup["todo_count"] == 1, agent_lookup
        assert agent_lookup["matched"] is True, agent_lookup
        assert agent_lookup["todo"]["todo_id"] == metadata_payload["todo_id"], agent_lookup
        assert agent_lookup["todo"]["action_kind"] == "run_eval", agent_lookup
        assert agent_lookup["todo"]["note"] == UPDATED_AGENT_TODO_NOTE, agent_lookup
        assert agent_lookup["relations"]["required_capabilities"] == [
            "shell",
            "benchmark_runner",
        ], agent_lookup

        missing_lookup = run_cli(
            registry_path,
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            "todo_missing_lookup_0001",
        )
        assert missing_lookup["ok"] is True, missing_lookup
        assert missing_lookup["todo_count"] == 0, missing_lookup
        assert missing_lookup["matched"] is False, missing_lookup
        assert missing_lookup["todo"] is None, missing_lookup
        assert missing_lookup["relations"] == {}, missing_lookup
        assert missing_lookup["not_found"] is True, missing_lookup

        monitor_payload = run_cli(
            registry_path,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            MONITOR_TODO,
            "--task-class",
            "continuous_monitor",
            "--action-kind",
            "release_note_draft_monitor",
            "--monitor-target-key",
            "release-note-draft-pr",
            "--cadence",
            "1d",
            "--next-due-at",
            MONITOR_DUE_AT,
            "--expires-at",
            MONITOR_EXPIRES_AT,
            "--claimed-by",
            "codex-side-bypass",
        )
        assert monitor_payload["added"] is True, monitor_payload
        assert monitor_payload["target_key"] == "release-note-draft-pr", monitor_payload
        assert monitor_payload["cadence"] == "1d", monitor_payload
        assert monitor_payload["next_due_at"] == MONITOR_DUE_AT, monitor_payload
        assert monitor_payload["expires_at"] == MONITOR_EXPIRES_AT, monitor_payload
        monitor_update = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            monitor_payload["todo_id"],
            "--agent-id",
            "codex-side-bypass",
            "--cadence",
            "2d",
            "--next-due-at",
            MONITOR_NEXT_DUE_AT,
            "--note",
            "Rescheduled after a quiet check.",
        )
        assert monitor_update["changed"] is True, monitor_update
        assert monitor_update["cadence"] == "2d", monitor_update
        assert monitor_update["next_due_at"] == MONITOR_NEXT_DUE_AT, monitor_update
        assert monitor_update["expires_at"] == MONITOR_EXPIRES_AT, monitor_update
        invalid_monitor_schedule = run_cli_error(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            agent_payload["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--cadence",
            "1d",
        )
        assert "monitor schedule metadata requires" in invalid_monitor_schedule["error"], invalid_monitor_schedule
        fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        monitor_item = next(
            item for item in fields["agent_todos"]["items"] if item["text"] == MONITOR_TODO
        )
        assert monitor_item["task_class"] == "continuous_monitor", fields
        assert monitor_item["target_key"] == "release-note-draft-pr", fields
        assert monitor_item["cadence"] == "2d", fields
        assert monitor_item["next_due_at"] == MONITOR_NEXT_DUE_AT, fields
        assert monitor_item["expires_at"] == MONITOR_EXPIRES_AT, fields

        status_payload = {
            "ok": True,
            "goal_count": 1,
            "run_count": 0,
            "attention_queue": {
                "items": [
                    {
                        "goal_id": GOAL_ID,
                        "status": "active_state_user_todo",
                        "waiting_on": "controller",
                        "severity": "action",
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "slot_minutes": 1,
                            "allowed_slots": 1440,
                            "spent_slots": 0,
                            "state": "operator_gate",
                            "reason": "open user gate",
                        },
                        "user_todos": fields["user_todos"],
                        "agent_todos": fields["agent_todos"],
                    }
                ]
            },
            "run_history": {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "registry_member": True,
                        "status": "active",
                        "adapter_kind": "fixture_adapter_v0",
                        "adapter_status": "connected",
                        "coordination": {
                            "registered_agents": ["codex-main-control", "codex-side-bypass"],
                            "agent_model": "peer_v1",
                        },
                        "latest_runs": [],
                    }
                ]
            },
        }
        main_quota = build_quota_should_run(
            status_payload,
            goal_id=GOAL_ID,
            agent_id="codex-main-control",
        )
        assert main_quota["user_todo_summary"]["other_agent_scoped_open_count"] == 1, main_quota
        assert main_quota["user_todo_summary"]["open_count"] == 1, main_quota

        missing_claim = run_cli_error(
            registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
        )
        assert "requires --claimed-by" in missing_claim["error"], missing_claim

        unknown_claim = run_cli_error(
            registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--claimed-by",
            "unregistered-agent",
        )
        assert "is not registered" in unknown_claim["error"], unknown_claim

        claim_rejects_successor_args = run_cli_error(
            registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--claimed-by",
            "codex-main-control",
            "--next-agent-todo",
            "Review claimed work.",
        )
        assert "--next-agent-todo" in claim_rejects_successor_args["error"], claim_rejects_successor_args
        assert "todo claim only accepts" in claim_rejects_successor_args["error"], claim_rejects_successor_args

        claim_payload = run_cli(
            registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--claimed-by",
            "codex-main-control",
            "--agent-id",
            "codex-main-control",
        )
        assert claim_payload["changed"] is True, claim_payload
        assert claim_payload["claimed_by"] == "codex-main-control", claim_payload
        claimed_fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        claimed_item = claimed_fields["agent_todos"]["items"][0]
        assert claimed_item["claimed_by"] == "codex-main-control", claimed_item
        assert claimed_fields["agent_todos"]["claimed_open_count"] == 2, claimed_fields
        assert claimed_fields["agent_todos"]["claimed_advancement_open_count"] == 1, claimed_fields
        assert claimed_fields["agent_todos"]["claimed_monitor_open_count"] == 1, claimed_fields
        assert claimed_fields["agent_todos"]["unclaimed_open_count"] == 0, claimed_fields

        side_agent_list = run_cli(
            registry_path,
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--agent-id",
            "codex-side-bypass",
        )
        assert side_agent_list["ok"] is True, side_agent_list
        assert side_agent_list["agent_id_filter"] == "codex-side-bypass", side_agent_list
        assert side_agent_list["unfiltered_todo_count"] == 2, side_agent_list
        assert side_agent_list["todo_count"] == 1, side_agent_list
        assert side_agent_list["todos"][0]["todo_id"] == monitor_payload["todo_id"], side_agent_list
        assert side_agent_list["todos"][0]["claimed_by"] == "codex-side-bypass", side_agent_list

        main_agent_list = run_cli(
            registry_path,
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--agent-id",
            "codex-main-control",
        )
        assert main_agent_list["ok"] is True, main_agent_list
        assert main_agent_list["todo_count"] == 1, main_agent_list
        assert main_agent_list["todos"][0]["todo_id"] == metadata_payload["todo_id"], main_agent_list
        assert main_agent_list["todos"][0]["claimed_by"] == "codex-main-control", main_agent_list

        preserve_claim_payload = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--note",
            "Claim-preserving progress note.",
        )
        assert preserve_claim_payload["changed"] is True, preserve_claim_payload
        assert preserve_claim_payload["claimed_by"] == "codex-main-control", preserve_claim_payload
        preserved_claim_fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        preserved_claim_item = preserved_claim_fields["agent_todos"]["items"][0]
        assert preserved_claim_item["claimed_by"] == "codex-main-control", preserved_claim_item
        assert preserved_claim_item["task_class"] == "advancement_task", preserved_claim_item
        assert preserved_claim_item["action_kind"] == "run_eval", preserved_claim_item
        assert preserved_claim_item["required_capabilities"] == [
            "shell",
            "benchmark_runner",
        ], preserved_claim_item
        assert preserved_claim_item["target_capabilities"] == [
            "benchmark_runner",
        ], preserved_claim_item

        conflicting_claim = run_cli_error(
            registry_path,
            "todo",
            "claim",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--claimed-by",
            "codex-side-bypass",
            "--agent-id",
            "codex-side-bypass",
        )
        assert "cannot claim" in conflicting_claim["error"], conflicting_claim
        assert "claimed_by='codex-main-control'" in conflicting_claim["error"], conflicting_claim

        clear_claim_payload = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--clear-claim",
        )
        assert clear_claim_payload["changed"] is True, clear_claim_payload
        cleared_fields = parse_active_state_todos(state_file.read_text(encoding="utf-8"))
        assert "claimed_by" not in cleared_fields["agent_todos"]["items"][0], cleared_fields

        stale_continuation = "  Stale continuation after structured metadata."
        state_lines = state_file.read_text(encoding="utf-8").splitlines()
        metadata_index = next(
            index
            for index, line in enumerate(state_lines)
            if f"todo_id={metadata_payload['todo_id']}" in line
        )
        state_lines.insert(metadata_index + 1, stale_continuation)
        state_file.write_text("\n".join(state_lines) + "\n", encoding="utf-8")

        update_payload = run_cli(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--text",
            UPDATED_AGENT_TODO,
            "--evidence",
            "Validated compact evidence summary.",
        )
        assert update_payload["changed"] is True, update_payload
        assert update_payload["text_changed"] is True, update_payload
        assert update_payload["status_changed"] is False, update_payload
        assert update_payload["todo"] == UPDATED_AGENT_TODO, update_payload

        terminal_update = run_cli_error(
            registry_path,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--status",
            "done",
            "--evidence",
            "Validated compact evidence summary.",
        )
        assert "agent todo completion must use" in terminal_update["error"], terminal_update
        assert "loopx todo complete" in terminal_update["error"], terminal_update

        completed_payload = run_cli(
            registry_path,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            metadata_payload["todo_id"],
            "--agent-id",
            "codex-main-control",
            "--evidence",
            "Validated compact evidence summary.",
            "--no-follow-up",
        )
        assert completed_payload["changed"] is True, completed_payload
        assert completed_payload["status_changed"] is True, completed_payload
        after_update = state_file.read_text(encoding="utf-8")
        assert f"- [x] {UPDATED_AGENT_TODO}" in after_update, after_update
        assert "Summarize the read-only evidence after the user" not in after_update, after_update
        assert stale_continuation not in after_update, after_update
        assert "status=done" in after_update, after_update
        assert "Validated%20compact%20evidence%20summary." in after_update, after_update
        updated_fields = parse_active_state_todos(after_update)
        updated_agent_item = next(
            item
            for item in updated_fields["agent_todos"]["items"]
            if item["todo_id"] == metadata_payload["todo_id"]
        )
        assert updated_agent_item["text"] == UPDATED_AGENT_TODO, updated_agent_item
        assert updated_agent_item["status"] == "done", updated_agent_item
        assert updated_agent_item["done"] is True, updated_agent_item

    print("todo-cli-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
