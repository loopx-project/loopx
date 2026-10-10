"""A provider rollback retains acknowledged work, not just a pre-upgrade backup.

Use fresh production CLI processes and both physical providers. Fixture creation
is separate from legacy capture qualification and from App/Host migration.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as settlement
from test_quota_authority_settlement_journey import _source


REPO = Path(__file__).resolve().parents[2]


def test_file_sqlite_file_retains_new_todos_results_and_settled_turn(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, state, _ = _source(
        tmp_path, provider="file", handoff_mode="hard_lease",
        extra=f"claimed_by={settlement.AGENT_ID}",
    )
    goal, actor, todo, turn = (
        settlement.GOAL_ID, settlement.AGENT_ID, settlement.TODO_ID, settlement.TURN_ID,
    )

    def run(*args, expected=0):
        process = subprocess.run(
            [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", *args],
            cwd=project, env={**os.environ, "PYTHONPATH": str(REPO)},
            capture_output=True, text=True, timeout=90, check=False,
        )
        assert process.returncode == expected, process.stdout + process.stderr
        return json.loads(process.stdout)

    def listed():
        return run("todo", "list", "--goal-id", goal)

    def plan(provider, name):
        path = tmp_path / f"{name}.json"
        planned = run("authority-archive", "plan-migration", "--goal-id", goal,
                      "--provider", provider, "--plan", str(path))
        return ("authority-archive", "migrate", "--goal-id", goal,
                "--plan", str(path), "--plan-sha256", planned["plan_sha256"])

    def history(name):
        path = tmp_path / f"{name}.jsonl"
        result = run("authority-archive", "export", "--goal-id", goal, "--archive", str(path))
        # Physical revisions and archive signatures change with the provider.
        # Domain operation ids, events, receipts and every state delta must not.
        rows = [{k: row[k] for k in ("cursor", "operation_id", "events", "receipts", "delta")}
                for line in path.read_text().splitlines()
                if (row := json.loads(line))["kind"] == "transaction"]
        return result["archive"], rows

    guard_args = ("quota", "should-run", "--codex-app", "--goal-id", goal,
                  "--agent-id", actor, "--todo-id", todo, "--turn-instance-id", turn,
                  "--scan-path", str(project))
    lease_args = ("--goal-id", goal, "--todo-id", todo, "--owner", actor,
                  "--idempotency-key", "retained-work")
    try:
        original = listed()
        backup, original_history = history("before-upgrade")
        migration = plan("sqlite", "to-sqlite")
        assert run(*migration)["selected_provider"] == "file"
        assert listed()["authority_read"]["source_authority"] == "file_v0"
        assert run(*migration, "--execute")["selected_provider"] == "sqlite"
        state.unlink()  # Neither restart nor rollback may restore old display truth.

        add_args = ("todo", "add", "--goal-id", goal, "--role", "agent",
                    "--text", "Validate work added after the upgrade", "--priority", "P0",
                    "--task-class", "advancement_task", "--action-kind", "validate",
                    "--claimed-by", actor, "--operation-id", "after-upgrade-add")
        # A response may be lost after the accepted add. Retry its original id.
        added = run(*add_args)
        added_again = run(*add_args)
        assert added_again["todo_id"] == added["todo_id"]
        assert added_again["status"] == "replayed"

        guard = run(*guard_args)
        assert guard["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
        identity = guard["heartbeat_receipt"]["settlement_identity"]
        acquired = run("task-lease", "acquire", *lease_args, "--expected-version", "0",
                       "--ttl-seconds", "3600", "--write-scope", "tests/**")
        version = str(acquired["lease"]["version"])
        proof = ("--task-lease-idempotency-key", "retained-work",
                 "--task-lease-expected-version", version)
        note = "New result after SQLite upgrade; 中文说明与原操作回执必须保留。"
        update_args = ("todo", "update", "--goal-id", goal, "--todo-id", todo,
                       "--agent-id", actor, "--text", "Validated after SQLite upgrade",
                       "--note", note, "--update-operation-id", "after-upgrade-update",
                       "--update-expected-provider-revision", acquired["provider_revision"], *proof)
        run(*update_args)
        assert run(*update_args)["status"] == "replayed"

        rejected_plan = tmp_path / "unsettled.json"
        rejected = run("authority-archive", "plan-migration", "--goal-id", goal,
                       "--provider", "file", "--plan", str(rejected_plan), expected=1)
        assert rejected["authority_changed"] is False
        assert "settled task leases" in rejected["reason"]
        assert not rejected_plan.exists()
        assert listed()["authority_read"]["source_authority"] == "sqlite_v0"

        complete_args = ("todo", "complete", "--goal-id", goal, "--todo-id", todo,
                         "--agent-id", actor, "--turn-instance-id", turn,
                         "--evidence", "validation://synthetic-delivery", *proof)
        completed = run(*complete_args)
        assert completed["ok"] is True
        refreshed = run(
            "refresh-state", "--goal-id", goal, "--agent-id", actor, "--todo-id", todo,
            "--turn-instance-id", turn, "--classification", "validated_progress",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--vision-state", "vision_on_track", "--vision-summary", "Continue the new work.",
            "--vision-acceptance", "Retain the completed work and continue the remaining Todos.",
            "--no-global-sync", "--suppress-external-sinks",
        )
        spend_args = shlex.split(refreshed["settlement_owed"]["command"])[1:]
        spent = run(*spend_args)
        assert spent["appended"] is True
        assert spent["settlement_progress"]["state"] == "settled"

        # Completion and spend receipts are historical facts, not permission to
        # repeat the work. Separate process readback observes the latest provider.
        current = listed()
        tasks = current["agent_todos"]["items"]
        finished = next(row for row in tasks if row["todo_id"] == todo)
        assert finished["status"] == "done" and finished["note"] == note
        assert any(row["todo_id"] == added["todo_id"] for row in tasks)
        assert len(tasks) == len(original["agent_todos"]["items"]) + 1
        current_archive, current_history = history("new-sqlite-work")
        assert current_history[:len(original_history)] == original_history
        assert current_archive["commits"] != backup["commits"]
        audit_args = ("authority-archive", "audit", "--goal-id", goal,
                      "--archive", str(tmp_path / "before-upgrade.jsonl"),
                      "--archive-sha256", backup["archive_sha256"])
        assert run(*audit_args, expected=1)["status"] == "failed"
        assert run(*audit_args, "--allow-newer-head")["audit"]["status"] == "matched"
        assert run(*migration, "--execute")["status"] == "already_applied"
        assert listed()["agent_todos"]["items"] == tasks

        rollback = plan("file", "to-file-with-new-writes")
        assert run(*rollback)["selected_provider"] == "sqlite"
        assert run(*rollback, "--execute")["selected_provider"] == "file"
        assert run(*rollback, "--execute")["status"] == "already_applied"
        reopened = listed()
        assert reopened["authority_read"]["source_authority"] == "file_v0"
        assert reopened["agent_todos"]["items"] == tasks
        restored_archive, restored_history = history("returned-to-file")
        assert restored_history == current_history
        assert restored_archive["source_store_identity"] == backup["source_store_identity"]
        assert restored_archive["projection_sha256"] == current_archive["projection_sha256"]

        # Read and replay the original accepted operations across the cutover.
        assert run(*add_args)["todo_id"] == added["todo_id"]
        assert run(*update_args)["status"] == "replayed"
        assert run(*complete_args)["ok"] is True
        assert run(*spend_args)["appended"] is False
        changed_add = list(add_args)
        changed_add[changed_add.index("--text") + 1] = "Different intent under the old operation id"
        assert run(*changed_add, expected=1)["ok"] is False
        replay = run(*guard_args)
        assert replay["effective_action"] == "heartbeat_settled_skip"
        assert replay["heartbeat_receipt"]["settlement_identity"] == identity
        assert listed()["agent_todos"]["items"] == tasks
        _, after_replay = history("after-original-operation-retries")
        assert after_replay == current_history
        assert settlement._spend_run_count(runtime) == 1
        # Storage continuity does not certify the final Goal. This fixture has
        # closed work without a qualified final-outcome path decision: the next
        # wake must request that replan, rather than lose the settled Turn or
        # silently treat the added Todo as proof of Goal acceptance.
        next_work = run("quota", "should-run", "--codex-app", "--goal-id", goal,
                        "--agent-id", actor, "--turn-instance-id", "turn-after-provider-rollback",
                        "--scan-path", str(project))
        assert next_work["decision"] == "autonomous_replan_required"
        assert next_work["effective_action"] != "unsettled_host_turn_recovery"
        assert next_work["replan_action_packet"]["obligation_id"]
    finally:
        subprocess.run(
            [sys.executable, "-c", "from loopx.control_plane.effect_runtime import restart_effect_runtime; restart_effect_runtime()"],
            cwd=REPO, capture_output=True, text=True, timeout=30, check=True,
        )
