"""Canonical callers must not load unpromoted Todo writers or capture producers."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from test_quota_authority_settlement_journey import _source
import test_quota_settlement_cli as settlement


@pytest.fixture
def without_source_todo_writers(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    package_root = tmp_path / "package"
    package = package_root / "loopx"
    shutil.copytree(Path(__file__).resolve().parents[2] / "loopx", package,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (package / "control_plane/todos/line_update.py").unlink()
    (package / "control_plane/todos/legacy_mutation.py").unlink(missing_ok=True)
    capture_adapter = package / "control_plane/coordination/runtime_shadow_writer_adapter.py"
    source = capture_adapter.read_text()
    lines = source.splitlines(keepends=True)
    producers = {"begin_todo_runtime_shadow_capture", "require_runtime_shadow_capture_prepared",
                 "write_captured_todo_state", "settle_todo_runtime_shadow_capture"}
    definitions = [node for node in ast.parse(source).body
                   if isinstance(node, ast.FunctionDef) and node.name in producers]
    assert {node.name for node in definitions} == producers
    for node in reversed(definitions):
        del lines[node.lineno - 1:node.end_lineno]
    capture_adapter.write_text("".join(lines))
    monkeypatch.setenv("PYTHONPATH", str(package_root))
    provenance = subprocess.check_output(
        [sys.executable, "-c", "import loopx; print(loopx.__file__)"],
        cwd=tmp_path, text=True,
    ).strip()
    assert Path(provenance) == package / "__init__.py"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_cli_lifecycle_without_source_todo_writers(tmp_path, provider, without_source_todo_writers):
    registry, _runtime, state = promoted_create_fixture(tmp_path, provider=provider)

    def cli(*args):
        actor = ["--agent-id", "agent-a"] if args[0] in ("update", "complete", "supersede") else []
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json",
             "--registry", str(registry), "todo", *args, *actor],
            cwd=tmp_path, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["ok"], payload
        return payload

    def add(role, text, operation):
        return cli("add", "--goal-id", "goal-a", "--role", role,
                   "--text", text, "--operation-id", operation,
                   *(["--task-class", "user_action"] if role == "user" else []))["todo_id"]

    original = add("agent", "Original work", "original")
    replacement = add("agent", "Replacement work", "replacement")
    user = add("user", "User action", "user")
    listing = cli("list", "--goal-id", "goal-a")
    updated = cli("update", "--goal-id", "goal-a", "--todo-id", original,
                  "--note", "Retain source identity", "--update-operation-id", "update",
                  "--update-expected-provider-revision", listing["authority_read"]["provider_revision"])
    assert updated["todo_id"] == original
    updated_record = cli("list", "--goal-id", "goal-a", "--todo-id", original)["todo"]
    assert updated_record["todo_id"] == original
    assert updated_record["note"] == "Retain source identity"
    completed = cli("complete", "--goal-id", "goal-a", "--todo-id", user,
                    "--no-follow-up", "--note", "Independent user action accepted")
    assert completed["completed"] and completed["todo_id"] == user
    superseded = cli("supersede", "--goal-id", "goal-a", "--todo-id", original,
                     "--successor-todo-id", replacement)
    assert superseded["superseded"] and superseded["todo_id"] == original
    archived = cli("archive-completed", "--goal-id", "goal-a", "--role", "user",
                   "--max-active-done", "0", "--execute")
    assert archived["moved_todo_ids"] == [user]
    final = cli("list", "--goal-id", "goal-a", "--role", "agent")
    records = {todo["todo_id"]: todo for todo in final["todos"]}
    assert records[original]["status"] == "done"
    assert records[original]["superseded_by"] == replacement
    assert records[replacement]["status"] == "open"
    assert "Original work" in state.read_text()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_new_goal_and_original_creation_recovery_without_source_todo_writers(tmp_path, provider, without_source_todo_writers):
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    registry = project / ".loopx/registry.json"
    config = runtime / "machine/configuration.json"
    config.parent.mkdir(parents=True)

    def configure(selected_provider):
        config.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": selected_provider,
                             "canonical_creation": True, "new_goal_handoff_mode": "soft_claim"}}}))

    def cli(*args, succeeds=True):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
             "--runtime-root", str(runtime), *args],
            cwd=tmp_path, capture_output=True, text=True, timeout=45,
        )
        assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
        return json.loads(result.stdout)

    def bootstrap():
        return cli("bootstrap", "--project", str(project), "--goal-id", "new-goal",
                   "--objective", "Preserve canonical creation and recovery", "--no-global-sync")

    configure(provider)
    created = bootstrap()
    assert created["storage_selection"]["authority_initialized"] is True
    added = cli("todo", "add", "--goal-id", "new-goal", "--role", "agent",
                "--text", "Retain work added after original creation", "--operation-id", "later-work")
    before = cli("todo", "list", "--goal-id", "new-goal")
    assert before["todos"][0]["todo_id"] == added["todo_id"]
    state = Path(created["state_file"])
    state.unlink()
    configure("sqlite" if provider == "file" else "file")
    recovered = bootstrap()
    selection = recovered["storage_selection"]
    assert selection["provider"] == provider
    assert selection["creation_operation_id"] == created["storage_selection"]["creation_operation_id"]
    assert selection["provider_revision"] == created["storage_selection"]["provider_revision"]
    assert selection["legacy_fallback_used"] is False
    assert not state.exists()
    after = cli("todo", "list", "--goal-id", "new-goal")
    assert after["todos"] == before["todos"]
    assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]

    backend = runtime / "authority" / f"{provider}-v0"
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    try:
        rejected = cli("bootstrap", "--project", str(project), "--goal-id", "new-goal",
                       "--objective", "Preserve canonical creation and recovery", "--no-global-sync", succeeds=False)
        assert rejected["ok"] is False
        assert "restore" in rejected["error"]
        assert not state.exists() and not backend.exists()
    finally:
        offline.rename(backend)
    restored = cli("todo", "list", "--goal-id", "new-goal")
    assert restored["todos"] == before["todos"]
    assert restored["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_leased_delivery_settlement_and_recovery_without_source_todo_writers(tmp_path, provider, without_source_todo_writers):
    project, runtime, registry, display, _ = _source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=f"claimed_by={settlement.AGENT_ID}",
    )
    display.unlink()

    def run(*args, succeeds=True):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json",
             "--registry", str(registry), "--runtime-root", str(runtime), *args],
            cwd=project, capture_output=True, text=True, timeout=45,
        )
        assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
        return json.loads(result.stdout)

    identity = ["--goal-id", settlement.GOAL_ID, "--agent-id", settlement.AGENT_ID,
                "--todo-id", settlement.TODO_ID]

    def guard(turn):
        return run("quota", "should-run", *identity, "--codex-app", "--turn-instance-id", turn)

    selected = guard(settlement.TURN_ID)
    assert selected["decision"] == "run"
    original = selected["heartbeat_receipt"]["settlement_identity"]
    assert original["todo_id"] == settlement.TODO_ID
    update = ["todo", "update", *identity, "--note", "Canonical delivery retained"]
    rejected = run(*update, succeeds=False)
    assert rejected["error_code"] == "handoff_mode_requires_lease"
    acquired = run("task-lease", "acquire", "--goal-id", settlement.GOAL_ID,
                   "--todo-id", settlement.TODO_ID, "--owner", settlement.AGENT_ID,
                   "--idempotency-key", "delivery", "--expected-version", "0",
                   "--ttl-seconds", "3600", "--write-scope", "tests/**")
    assert acquired["acquired"] is True
    proof = ["--task-lease-idempotency-key", "delivery",
             "--task-lease-expected-version", str(acquired["lease"]["version"])]
    assert run(*update, *proof)["ok"] is True
    before = run("todo", "list", *identity)["todo"]
    assert before["note"] == "Canonical delivery retained"
    projected_display = display.read_bytes()

    # A committed update can rebuild its display. That display cannot become
    # fallback authority or create a new admission when the provider fails.
    backend = runtime / "authority" / f"{provider}-v0"
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    try:
        failed = run("quota", "should-run", *identity, "--codex-app",
                     "--turn-instance-id", "provider-unavailable", succeeds=False)
        assert not failed.get("selected_todo")
        assert settlement._heartbeat_receipt_count(runtime, "provider-unavailable") == 0
        assert settlement._spend_run_count(runtime) == 0
        assert not backend.exists() and display.read_bytes() == projected_display
    finally:
        offline.rename(backend)

    refreshed = run("refresh-state", *identity, "--turn-instance-id", settlement.TURN_ID,
                    "--classification", "validated_progress", "--delivery-batch-scale", "implementation",
                    "--delivery-outcome", "outcome_progress", "--no-global-sync", "--suppress-external-sinks",
                    "--vision-state", "vision_on_track", "--vision-summary", "Continue canonical delivery.",
                    "--vision-acceptance", "Keep current work and settle its admitted Turn once; subsequent work remains open.")
    assert refreshed["vision_checkpoint"]["satisfied"] is True
    command = shlex.split(refreshed["settlement_owed"]["command"])
    assert command[0] == "loopx"
    assert command[command.index("--registry") + 1] == str(registry)
    assert command[command.index("--runtime-root") + 1] == str(runtime)
    spent = run(*command[1:])
    assert spent["appended"] is True and spent["settlement_progress"]["state"] == "settled"
    replay = run(*command[1:])
    assert replay["appended"] is False
    assert settlement._spend_run_count(runtime) == 1
    settled = guard(settlement.TURN_ID)
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled["heartbeat_receipt"]["settlement_identity"] == original
    assert settled["interaction_contract"]["agent_channel"]["must_attempt"] is False
    assert run("todo", "list", *identity)["todo"] == before
    assert run("task-lease", "release", "--goal-id", settlement.GOAL_ID,
               "--todo-id", settlement.TODO_ID, "--owner", settlement.AGENT_ID,
               "--idempotency-key", "delivery", "--expected-version", proof[-1])["released"] is True
    # Settling this Turn does not settle the vision. A fresh wake discovers
    # current work or its required replan instead of reusing the old selection.
    fresh = run("quota", "should-run", "--goal-id", settlement.GOAL_ID,
                "--agent-id", settlement.AGENT_ID, "--codex-app", "--turn-instance-id", "next-delivery")
    assert fresh["decision"] in {"run", "autonomous_replan_required"}
    if fresh["decision"] == "autonomous_replan_required":
        assert fresh["replan_action_packet"]["obligation_id"]
    assert fresh["effective_action"] != "unsettled_host_turn_recovery"
    assert settlement._spend_run_count(runtime) == 1


def test_explicit_source_writer_import_preserves_the_existing_seam(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
import loopx.todos as todos
assert 'loopx.control_plane.todos.line_update' not in sys.modules
assert 'loopx.control_plane.todos.legacy_mutation' not in sys.modules
from loopx.todos import apply_todo_update_to_lines
from loopx.control_plane.todos import line_update
assert apply_todo_update_to_lines is line_update.apply_todo_update_to_lines
for name in ('link_generated_successor_todo_ids', 'link_superseding_todo_id', 'upsert_todo_metadata'):
    assert getattr(todos, name) is getattr(line_update, name)
try:
    getattr(todos, 'unknown_writer_export')
except AttributeError:
    pass
else:
    raise AssertionError('unknown export must remain unavailable')
"""],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("material_change", [False, True])
@pytest.mark.parametrize("source_writers_present", [False, True], ids=["absent", "present"])
def test_monitor_recovery_with_source_writer_isolation(
    tmp_path, monkeypatch, request, provider, material_change, source_writers_present,
):
    from test_leased_monitor_poll import LEASE, PROOF
    from test_monitor_followthrough_contract import GOAL_ID, AGENT_ID
    from test_native_monitor_poll import _canonical

    if source_writers_present:
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    else:
        request.getfixturevalue("without_source_todo_writers")
    registry, runtime, display, monitor = _canonical(
        tmp_path, native=True, provider=provider, lease=LEASE,
    )
    display.unlink()

    def cli(*args, succeeds=True):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json",
             "--registry", str(registry), "--runtime-root", str(runtime), *args],
            cwd=tmp_path, capture_output=True, text=True, timeout=45,
        )
        assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
        return json.loads(result.stdout)

    def current():
        return cli("todo", "list", "--goal-id", GOAL_ID)

    identity = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
                "--runtime-profile", "generic_cli", "--turn-instance-id", "monitor-retirement",
                "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    guard = cli("quota", "should-run", *identity)
    assert guard["selected_todo"]["todo_id"] == monitor["todo_id"]
    before = current()
    poll = ["quota", "monitor-poll", *identity, "--todo-id", monitor["todo_id"],
            "--result-hash", "observed-revision", "--use-current-task-lease", "--execute"]
    if material_change:
        poll += ["--material-change", "--next-agent-todo", "Validate the observed revision",
                 "--next-action-kind", "validate"]

    # A missing provider must not use the display or create another authority.
    backend = runtime / "authority" / f"{provider}-v0"
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    try:
        failed = cli(*poll, succeeds=False)
        assert failed["ok"] is False
        assert not backend.exists() and not display.exists()
    finally:
        offline.rename(backend)
    assert current()["todos"] == before["todos"]

    committed = cli(*poll)
    assert committed["todo_writeback"]["lease_proof"] == PROOF
    assert committed["todo_writeback"]["material_change_generation"] == int(material_change)
    after = current()
    assert len(after["todos"]) == len(before["todos"]) + int(material_change)
    observed = next(t for t in after["todos"] if t["todo_id"] == monitor["todo_id"])
    assert observed["result_hash"] == "observed-revision"
    assert int(observed["consecutive_no_change"]) == int(not material_change)
    assert display.exists()

    # Recover the same observation after the execution lease is released.
    assert cli("task-lease", "release", "--goal-id", GOAL_ID,
               "--todo-id", monitor["todo_id"], "--owner", AGENT_ID,
               "--idempotency-key", PROOF["idempotency_key"],
               "--expected-version", str(PROOF["expected_version"]))["released"]
    assert cli(*poll)["replayed"] is True
    assert current()["todos"] == after["todos"]
    rows = [json.loads(line) for line in
            (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    assert sum(row.get("classification") == "quota_monitor_poll" for row in rows) == 1
    assert all(row.get("classification") != "quota_slot_spend" for row in rows)
