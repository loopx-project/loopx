"""Qualify the ordinary authority lifecycle with original-Turn result and direction reads."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.control_plane.canonical_authority_fixture import assert_todo_read_unchanged, isolate_sqlite_runtime


def _retain_result_then_record_direction(command, args, identity):
    vision_at = args.index("--vision-state")
    writeback_args = args[:vision_at]
    binding = [token for key in ("--goal-id", "--agent-id", "--todo-id", "--turn-instance-id")
               for token in (key, args[args.index(key) + 1])]
    result_basis = command("checkpoint-context", "--purpose", "delivery_result", *binding)
    assert result_basis["settlement_identity"] == identity
    assert result_basis["basis"]["todo"]["note"].startswith(("Validated ordinary work", "Keep the latest note"))
    retained = command(*writeback_args, "--delivery-read-context", result_basis["read_context_id"])
    assert retained["appended"] is True
    assert retained["settlement_identity"] == identity
    context = command("checkpoint-context", "--purpose", "supplement_checkpoint", *binding)
    assert context["settlement_identity"] == identity
    assert context["basis"]["todo"]["note"].startswith(("Validated ordinary work", "Keep the latest note"))
    supplemented = command(*args, "--checkpoint-read-context", context["read_context_id"])
    assert supplemented["vision_checkpoint"]["satisfied"] is True, supplemented
    assert supplemented["settlement_identity"] == identity
    return supplemented


@pytest.mark.parametrize("bridge_modules_present", [True, False])
def test_new_default_goal_settles_once_and_returns_current_writes_to_file(
    tmp_path, monkeypatch, bridge_modules_present,
):
    """Join the shipped creation, ordinary work and reversible-exit owners."""
    from loopx.control_plane.effect_runtime import restart_effect_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    registry = project / ".loopx/registry.json"
    goal, agent, turn = "trial-goal", "trial-agent", "trial-turn"
    package = Path(os.environ.get("LOOPX_COLD_IMPORT_PACKAGE", str(Path(__file__).resolve().parents[2] / "loopx")))
    receiver = tmp_path / "receiver"
    shutil.copytree(package, receiver / "loopx", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert not (
        receiver / "loopx/control_plane/coordination/runtime_shadow_writer_adapter.py"
    ).exists()
    if not bridge_modules_present:
        for name in ("runtime_shadow.py", "local_authority_shadow_adapter.py",
                     "local_authority_shadow_outbox.py"):
            (receiver / "loopx/control_plane/coordination" / name).unlink()
    # Isolated interpreter, real full entrypoint, and verified copied-package
    # provenance on every command; no fallback to the test runner's checkout.
    entry = (
        f"import sys; sys.path.insert(0, {str(receiver)!r}); import loopx; "
        f"assert loopx.__file__ == {str(receiver / 'loopx/__init__.py')!r}; "
        "from loopx.entrypoint import main; raise SystemExit(main())"
    )

    def command(*args, expected_code=0):
        result = subprocess.run(
            [sys.executable, "-I", "-c", entry, "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", *args],
            cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        assert result.returncode == expected_code, result.stdout + result.stderr
        return json.loads(result.stdout)

    def todos():
        return command("todo", "list", "--goal-id", goal)

    try:
        created = command("bootstrap", "--project", str(project), "--goal-id", goal,
                          "--objective", "Validate reversible ordinary work", "--no-global-sync")
        assert created["storage_selection"]["provider"] == "sqlite"
        assert created["storage_selection"]["handoff_mode"] == "hard_lease"
        command("configure-goal", "--goal-id", goal, "--registered-agent", agent,
                "--quota-compute", "1", "--execute")
        task = command("todo", "add", "--goal-id", goal, "--role", "agent",
                       "--claimed-by", agent, "--text", "Check ordinary work",
                       "--priority", "P1", "--task-class", "advancement_task",
                       "--action-kind", "validate", "--note", "Retain exact metadata",
                       "--operation-id", "trial-create-todo")
        todo = task["todo_id"]
        lease = command("task-lease", "acquire", "--goal-id", goal, "--todo-id", todo,
                        "--owner", agent, "--idempotency-key", "trial-work")
        proof = ["--task-lease-idempotency-key", "trial-work",
                 "--task-lease-expected-version", str(lease["lease"]["version"])]
        guard_args = ["quota", "should-run", "--context-delivery", "off",
                      "--goal-id", goal, "--agent-id", agent,
                      "--todo-id", todo, "--codex-app", "--turn-instance-id", turn,
                      ]
        guard = command(*guard_args)
        assert guard["normal_delivery_allowed"] is True
        identity = guard["heartbeat_receipt"]["settlement_identity"]
        update = ["todo", "update", "--goal-id", goal, "--todo-id", todo,
                  "--agent-id", agent, "--note", "Validated ordinary work; retain on exit",
                  "--update-operation-id", "trial-update", *proof]
        assert command(*update)["source_authority"] == "sqlite_v0"
        refresh_args = ["refresh-state", "--goal-id", goal, "--agent-id", agent,
                        "--todo-id", todo, "--turn-instance-id", turn,
                        "--classification", "validated_progress", "--delivery-batch-scale", "test_only",
                        "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks",
                        "--vision-state", "vision_on_track", "--vision-summary", "Continue reversible qualification.",
                        "--vision-acceptance", "Preserve later writes and exact once-only settlement."]
        refresh = _retain_result_then_record_direction(command, refresh_args, identity)
        # Consume the original owner-generated continuation, not reconstructed spend flags.
        spend_args = shlex.split(refresh["settlement_owed"]["command"])[1:]
        spent = command(*spend_args)
        assert spent["settlement_progress"]["state"] == "settled"
        restart_effect_runtime()
        assert command(*update)["status"] == "replayed"
        replay = command(*spend_args)
        assert replay["idempotent_replay"] is True and replay["appended"] is False
        settled = command(*guard_args)
        assert settled["heartbeat_receipt"]["settlement_identity"] == identity
        assert settled["normal_delivery_allowed"] is False
        assert settled["interaction_contract"]["cli_channel"]["spend_after_validation"] is False
        # Pausing turns is not lease settlement; exit must refuse the live grant.
        command("configure-goal", "--goal-id", goal, "--quota-compute", "0", "--execute")
        refused_plan = tmp_path / "unsettled-exit-plan.json"
        before_refusal = todos()
        refusal = command("authority-archive", "plan-migration", "--goal-id", goal,
                          "--provider", "file", "--plan", str(refused_plan), expected_code=1)
        assert "settled task leases" in refusal["reason"]
        assert not refused_plan.exists()
        # The projection envelope records when each read happened; the refused
        # exit must leave every other read fact unchanged.
        assert_todo_read_unchanged(todos(), before_refusal)
        command("task-lease", "release", "--goal-id", goal, "--todo-id", todo,
                "--owner", agent, "--idempotency-key", "trial-work",
                "--expected-version", str(lease["lease"]["version"]))
        before_exit = todos()
        assert before_exit["authority_read"]["source_authority"] == "sqlite_v0"
        assert before_exit["authority_read"]["legacy_fallback_used"] is False
        rows = before_exit["todos"]
        assert len(rows) == 1 and rows[0]["todo_id"] == todo
        assert rows[0]["note"] == "Validated ordinary work; retain on exit"
        assert rows[0]["claimed_by"] == agent and rows[0]["priority"] == "P1"

        plan = tmp_path / "current-exit-plan.json"
        preview = command("authority-archive", "plan-migration", "--goal-id", goal,
                          "--provider", "file", "--plan", str(plan))
        apply = ["authority-archive", "migrate", "--goal-id", goal, "--plan", str(plan),
                 "--plan-sha256", preview["plan_sha256"], "--execute"]
        command(*apply)
        restart_effect_runtime()
        assert command(*apply)["status"] == "already_applied"
        returned = todos()
        assert returned["authority_read"]["source_authority"] == "file_v0"
        assert returned["authority_read"]["legacy_fallback_used"] is False
        assert returned["todos"] == rows
        # Restore the reviewed prior quota after readback, so a paused lane
        # cannot conceal a lost settlement behind quota_skip.
        command("configure-goal", "--goal-id", goal, "--quota-compute", "1", "--execute")
        after_exit = command(*guard_args)
        assert after_exit["effective_action"] == "heartbeat_settled_skip"
        assert after_exit["heartbeat_receipt"]["settlement_identity"] == identity
        assert after_exit["interaction_contract"]["cli_channel"]["spend_after_validation"] is False
        runs = [json.loads(line) for line in
                (runtime / "goals" / goal / "runs/index.jsonl").read_text().splitlines()]
        assert sum(row.get("classification") == "quota_slot_spent" for row in runs) == 1
        assert sum(row.get("classification") == "validated_progress" for row in runs) == 1
    finally:
        restart_effect_runtime()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_installed_ordinary_lifecycle_keeps_archived_work_and_receipts_on_exit(
    tmp_path, monkeypatch, provider,
):
    """Use one ordinary command journey for the candidate and File control.

    This is bounded synthetic qualification, not elapsed usage or a model Turn.
    A historical add/update/terminal retry must not resurrect archived work,
    repeat settlement or replace newer metadata after a provider conversion.
    """
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    registry = project / ".loopx/registry.json"
    goal, agent, turn = "ordinary-lifecycle", "trial-agent", "ordinary-turn"
    package = Path(os.environ.get("LOOPX_COLD_IMPORT_PACKAGE", str(Path(__file__).resolve().parents[2] / "loopx")))
    receiver = tmp_path / "receiver"
    shutil.copytree(package, receiver / "loopx", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    entry = (
        f"import sys; sys.path.insert(0, {str(receiver)!r}); import loopx; "
        f"assert loopx.__file__ == {str(receiver / 'loopx/__init__.py')!r}; "
        "from loopx.entrypoint import main; raise SystemExit(main())"
    )

    def command(*args, expected_code=0):
        result = subprocess.run(
            [sys.executable, "-I", "-c", entry, "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", *args],
            cwd=project, capture_output=True, text=True, encoding="utf-8", timeout=90,
        )
        assert result.returncode == expected_code, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload.get("ok") is (expected_code == 0), payload
        return payload

    def listed(*archived_ids):
        payload = command("todo", "list", "--goal-id", goal, "--role", "agent")
        assert not set(archived_ids).intersection(row["todo_id"] for row in payload["todos"])
        # Normal lists stay active-only; historical records require exact reads.
        for archived_id in archived_ids:
            exact = command("todo", "list", "--goal-id", goal, "--role", "agent", "--todo-id", archived_id)
            assert exact["todo"]["todo_id"] == archived_id
            payload["todos"].append(exact["todo"])
        return payload

    if provider == "file":
        config = tmp_path / "storage-defaults.json"
        config.write_text(json.dumps({"schema_version": "loopx_goal_storage_defaults_v1",
            "new_goal_provider": "file", "canonical_creation": True,
            "new_goal_handoff_mode": "hard_lease"}))
        preview = command("machine-config", "preview", "--namespace", "goal_storage", "--config-json", str(config))
        command("machine-config", "apply", "--namespace", "goal_storage", "--config-json", str(config),
                "--expected-plan-revision", str(preview["plan_revision"]), "--execute")
    created = command("bootstrap", "--project", str(project), "--goal-id", goal,
                      "--objective", "Qualify ordinary lifecycle and reversible exit", "--no-global-sync")
    assert created["storage_selection"]["provider"] == provider
    assert created["storage_selection"]["handoff_mode"] == "hard_lease"
    command("configure-goal", "--goal-id", goal, "--registered-agent", agent,
            "--quota-compute", "1", "--execute")
    add = ["todo", "add", "--goal-id", goal, "--role", "agent", "--text", "Complete a task 完整 🧭",
           "--priority", "P1", "--task-class", "advancement_task", "--action-kind", "validate",
           "--required-capability", "filesystem_read", "--operation-id", "ordinary-add"]
    todo = command(*add)["todo_id"]
    claim = ["todo", "claim", "--goal-id", goal, "--todo-id", todo, "--agent-id", agent,
             "--claimed-by", agent, "--claim-operation-id", "ordinary-claim"]
    accepted = command(*claim)
    assert accepted["execution_managed"] is True
    proof = ["--claim-operation-id", accepted["claim_operation_id"]]
    guard = ["quota", "should-run", "--context-delivery", "off", "--goal-id", goal, "--agent-id", agent,
             "--todo-id", todo, "--codex-app", "--turn-instance-id", turn,
             "--available-capability", "filesystem_read", ]
    admitted = command(*guard)
    assert admitted["normal_delivery_allowed"] is True
    update = ["todo", "update", "--goal-id", goal, "--todo-id", todo, "--agent-id", agent,
              "--note", "Keep the latest note 完整 🧭", "--update-operation-id", "ordinary-update", *proof]
    command(*update)
    refresh_args = ("refresh-state", "--goal-id", goal, "--agent-id", agent,
        "--todo-id", todo, "--turn-instance-id", turn, "--classification", "validated_progress",
        "--delivery-batch-scale", "test_only", "--delivery-outcome", "outcome_progress",
        "--no-global-sync", "--suppress-external-sinks", "--vision-state", "vision_on_track",
        "--vision-summary", "Continue the ordinary lifecycle.",
        "--vision-acceptance", "Retain current work and original receipts through exit.")
    refreshed = _retain_result_then_record_direction(
        command, refresh_args, admitted["heartbeat_receipt"]["settlement_identity"])
    spend = shlex.split(refreshed["settlement_owed"]["command"])[1:]
    assert command(*spend)["settlement_progress"]["state"] == "settled"
    complete = ["todo", "complete", "--goal-id", goal, "--todo-id", todo,
                "--agent-id", agent, "--turn-instance-id", turn,
                "--evidence", "synthetic:ordinary-lifecycle-validated", *proof]
    completion_basis = command("checkpoint-context", "--purpose", "delivery_result", "--goal-id", goal,
        "--agent-id", agent, "--todo-id", todo, "--turn-instance-id", turn)
    assert completion_basis["settlement_identity"] == admitted["heartbeat_receipt"]["settlement_identity"]
    assert completion_basis["basis"]["todo"]["note"] == "Keep the latest note 完整 🧭"
    complete.extend(["--delivery-read-context", completion_basis["read_context_id"]])
    command(*complete)
    command(*complete, "--no-follow-up")  # Same original managed claim; no copied released proof.
    # Replanning is a normal caller, too: replace another task through its owner,
    # then retain both the archived predecessor and the new successor on exit.
    obsolete = command("todo", "add", "--goal-id", goal, "--role", "agent", "--claimed-by", agent,
                       "--text", "Replace the obsolete task", "--operation-id", "ordinary-obsolete")["todo_id"]
    other = command("task-lease", "acquire", "--goal-id", goal, "--todo-id", obsolete,
                    "--owner", agent, "--idempotency-key", "ordinary-other")
    command("todo", "supersede", "--goal-id", goal, "--todo-id", obsolete, "--agent-id", agent,
            "--reason", "Continue with the replacement", "--next-agent-todo", "Replacement remains runnable",
            "--next-claimed-by", agent, "--task-lease-idempotency-key", "ordinary-other",
            "--task-lease-expected-version", str(other["lease"]["version"]))
    command("todo", "archive-completed", "--goal-id", goal, "--max-active-done", "0", "--execute")
    before = listed(todo, obsolete)
    rows = before["todos"]
    completed = next(row for row in rows if row["todo_id"] == todo)
    assert completed["status"] == "done" and completed["archive_state"] == "archive"
    assert completed["claimed_by"] == agent and completed["priority"] == "P1"
    assert completed["note"] == "Keep the latest note 完整 🧭"
    assert completed["required_capabilities"] == ["filesystem_read"]
    predecessor = next(row for row in rows if row["todo_id"] == obsolete)
    assert predecessor["status"] == "done" and predecessor["archive_state"] == "archive"
    replacement = next(row for row in rows if row["todo_id"] in predecessor["successor_todo_ids"])
    assert replacement["status"] == "open" and replacement["claimed_by"] == agent
    assert before["authority_read"]["source_authority"] == f"{provider}_v0"
    command("configure-goal", "--goal-id", goal, "--quota-compute", "0", "--execute")
    target = "file" if provider == "sqlite" else "sqlite"
    plan = tmp_path / "current-exit.json"
    preview = command("authority-archive", "plan-migration", "--goal-id", goal,
                      "--provider", target, "--plan", str(plan))
    migration = ["authority-archive", "migrate", "--goal-id", goal, "--plan", str(plan),
                 "--plan-sha256", preview["plan_sha256"], "--execute"]
    command(*migration)
    assert command(*migration)["status"] == "already_applied"
    after = listed(todo, obsolete)
    assert after["authority_read"]["source_authority"] == f"{target}_v0"
    assert after["authority_read"]["legacy_fallback_used"] is False
    assert after["todos"] == rows
    # Every invocation reopens the copied package's real backend. Original
    # command identities survive conversion and cannot unarchive older state.
    for args in (add, update, complete, spend):
        command(*args)
    replayed = listed(todo, obsolete)
    assert replayed["todos"] == rows
    assert replayed["authority_read"]["provider_revision"] == after["authority_read"]["provider_revision"]
    command("configure-goal", "--goal-id", goal, "--quota-compute", "1", "--execute")
    assert command(*guard)["effective_action"] == "heartbeat_settled_skip"
    runs = [json.loads(line) for line in (runtime / "goals" / goal / "runs/index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in runs) == 1
    assert sum(row.get("classification") == "validated_progress" for row in runs) == 1
