"""Real CLI consumers with SQLite authority and no Markdown source."""
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import effect_runtime_result

from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.coordination.coordination_state_contract import TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION, TODO_DOMAIN_RECORD_FIELDS
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes


def test_sqlite_cli_reopens_updates_and_recovers_missing_markdown(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    state.write_text("# Synthetic goal\n\n## Agent Todo\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "sqlite-goal", "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a"]},
    }]}))
    projection = build_todo_runtime_shadow_projection(goal_id="sqlite-goal", handoff_mode="soft_claim", todos=[{
        "schema_version": "todo_item_v0", "todo_id": "todo_sqlite", "role": "agent",
        "status": "open", "done": False, "text": "Original task", "archive_state": "active",
        "source_section": "Agent Todo", "index": 1, "claimed_by": "agent-a", "task_class": "advancement_task",
    }])
    for record in projection["todos"]:
        record["schema_version"] = "todo_domain_record_v0"
        record.pop("index")
        record.pop("source_section")
    projection["todo_read_model"] = {
        "schema_version": TODO_DOMAIN_READ_RECORD_SCHEMA_VERSION,
        "contract_fields": list(TODO_DOMAIN_RECORD_FIELDS),
        "todo_count": 1,
        "records_sha256": hashlib.sha256(canonical_bytes(projection["todos"])).hexdigest(),
    }
    initialize_canonical_authority(runtime, "sqlite-goal", projection, state_path=state, provider="sqlite")
    state.unlink()

    def cli(*args, expected_code=0):
        process = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
            "--format", "json", "todo", *args, "--goal-id", "sqlite-goal"],
            capture_output=True, text=True, timeout=60)
        assert process.returncode == expected_code, process.stdout + process.stderr
        return json.loads(process.stdout)

    assert "Original task" in json.dumps(cli("list"))
    assert not state.exists()
    updated = cli("update", "--role", "agent", "--todo-id", "todo_sqlite", "--agent-id", "agent-a",
        "--text", "Updated through SQLite")
    assert updated["source_authority"] == "sqlite_v0"
    assert updated["projection_delivery"] == "delivered", json.dumps(updated, indent=2)
    assert "Updated through SQLite" in state.read_text()
    state.unlink()
    assert "Updated through SQLite" in json.dumps(cli("list"))
    assert not state.exists()
    assert not list((runtime / "authority" / "file-v0").glob("authority-store-*.json"))
    # Native planning updates must preserve the selected provider through the
    # Python adapter, including dry-run, receipt replay and reopening a wait.
    planning = ("update", "--role", "agent", "--todo-id", "todo_sqlite", "--agent-id", "agent-a",
        "--status", "deferred", "--resume-when", "resume_at:2026-09-14T09:30:00+08:00",
        "--reason", "Await the exact scheduled instant",
        "--update-operation-id", "sqlite-planning")
    assert cli(*planning, "--dry-run")["status"] == "planned"
    assert not state.exists()
    assert cli(*planning)["source_authority"] == "sqlite_v0"
    assert cli(*planning)["status"] == "replayed"
    assert "resume_at:2026-09-14T01:30:00Z" in json.dumps(cli("list"))
    resumed = cli("update", "--role", "agent", "--todo-id", "todo_sqlite", "--agent-id", "agent-a",
        "--status", "open", "--clear-resume-when")
    assert resumed["source_authority"] == "sqlite_v0"
    state.unlink()
    databases = list((runtime / "authority" / "sqlite-v0").glob("*.sqlite"))
    assert len(databases) == 1
    databases[0].rename(databases[0].with_suffix(".saved"))
    for result in (
        cli("list", expected_code=1),
        cli("update", "--role", "agent", "--todo-id", "todo_sqlite", "--agent-id", "agent-a",
            "--text", "Must not commit", expected_code=1),
    ):
        assert result["source_authority"] == "sqlite_v0"
        assert result["error_code"] == "local_authority_provider_missing"
        assert result["legacy_fallback_used"] is False
        assert result["decision_read_from_provider"] is False
    assert not state.exists()
    assert not databases[0].exists()


@pytest.mark.parametrize("fault,reason", [
    ("selector_corrupt", "local_authority_selector_invalid"),
    ("selector_missing", "local_authority_selector_missing"),
    ("database_missing", "local_authority_provider_missing"),
    ("database_corrupt", "local_authority_provider_open_failed"),
])
def test_promotion_failure_evidence_survives_real_python_runtime(tmp_path, monkeypatch, fault, reason):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    # Node resolves module URLs through symlinks; direct-entry detection needs
    # the same physical path in relocated installed-package test layouts.
    selector_cli = (Path(__file__).resolve().parents[2] / "loopx/control_plane/coordination/local_authority_provider.ts").resolve()
    process = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", str(selector_cli),
        "--runtime-root", str(runtime), "--goal-id", "sqlite-goal", "--execute"],
        capture_output=True, text=True, timeout=45)
    assert process.returncode == 0, process.stdout + process.stderr
    marker = next((runtime / "authority").glob("provider-*.json"))
    database = next((runtime / "authority/sqlite-v0").glob("*.sqlite"))
    if fault == "selector_corrupt":
        marker.write_text("{")
    elif fault == "selector_missing":
        marker.unlink()
    elif fault == "database_missing":
        database.rename(database.with_suffix(".saved"))
    else:
        database.write_text("invalid SQLite")
    def durable_bytes():
        return {str(path.relative_to(runtime)): path.read_bytes()
                for path in runtime.rglob("*") if path.is_file()}
    before = durable_bytes()
    result = effect_runtime_result("coordination.local_authority.promote", {
        "schema_version": "loopx_local_coordination_promotion_request_v0",
        "runtime_root": str(runtime), "goal_id": "sqlite-goal", "operation_id": "promotion-negative",
        "canonical_authority": "sqlite_v0",
        "expected_shadow_provider_revision": "file:synthetic:1",
        "expected_shadow_projection_sha256": "a" * 64, "minimum_operations": 1,
        "required_event_kinds": ["todo_claim"], "writer_fence": {
            "schema_version": "loopx_legacy_coordination_writer_fence_v0", "state": "engaged",
            "goal_id": "sqlite-goal", "fence_id": "unverified-fence", "source_version": "state:1",
            "source_projection_sha256": "a" * 64, "expected_shadow_provider_revision": "file:synthetic:1",
        },
    })
    assert result["status"] == "failed" and result["reason_code"] == reason
    assert result["legacy_writer_fenced"] is False
    assert result["source_authority"] == (None if fault.startswith("selector") else "sqlite_v0")
    assert result["decision_read_from_provider"] is False and result["legacy_fallback_used"] is False
    assert durable_bytes() == before
    assert not list((runtime / "authority-transition").rglob("legacy-writer-fence-*.json"))


def test_new_default_goal_settles_once_and_returns_current_writes_to_file(tmp_path, monkeypatch):
    """Join the shipped creation, ordinary work and reversible-exit owners."""
    from loopx.control_plane.effect_runtime import restart_effect_runtime

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    registry = project / ".loopx/registry.json"
    goal, agent, turn = "trial-goal", "trial-agent", "trial-turn"

    def command(*args, expected_code=0):
        result = subprocess.run(
            [sys.executable, "-I", "-m", "loopx.entrypoint", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", *args],
            cwd=project, capture_output=True, text=True, timeout=90,
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
        guard_args = ["quota", "should-run", "--goal-id", goal, "--agent-id", agent,
                      "--todo-id", todo, "--codex-app", "--turn-instance-id", turn,
                      "--scan-path", str(project)]
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
        refresh = command(*refresh_args)
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
        command("authority-archive", "plan-migration", "--goal-id", goal,
                "--provider", "file", "--plan", str(refused_plan), expected_code=1)
        assert not refused_plan.exists() and todos() == before_refusal
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
        assert command(*guard_args)["normal_delivery_allowed"] is False
        runs = [json.loads(line) for line in
                (runtime / "goals" / goal / "runs/index.jsonl").read_text().splitlines()]
        assert sum(row.get("classification") == "quota_slot_spent" for row in runs) == 1
        assert sum(row.get("classification") == "validated_progress" for row in runs) == 1
    finally:
        restart_effect_runtime()
