"""Canonical callers must not load unpromoted Todo writers or capture producers."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from test_quota_authority_settlement_journey import _source
import test_quota_settlement_cli as settlement


@pytest.mark.parametrize("module", ["mutation_api", "legacy_mutation"])
def test_todo_mutation_import_does_not_load_status(module):
    result = subprocess.run(
        [sys.executable, "-c",
         "import importlib,sys;sys.modules['loopx.status']=None;"
         f"owner=importlib.import_module('loopx.control_plane.todos.{module}');"
         "assert owner.ARCHIVE_COMPLETED_DEFAULT_MAX_ACTIVE_DONE == 10"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture(scope="session")
def retirement_wheel():
    # Supply a normally built artifact: unit runs must not build the frontend
    # or silently fabricate a package when distribution validation is missing.
    artifact = Path(os.environ["LOOPX_TODO_RETIREMENT_WHEEL"]).resolve()
    assert artifact.is_file() and artifact.suffix == ".whl", artifact
    return artifact


@pytest.fixture(
    params=["source"] + (["wheel"] if "LOOPX_TODO_RETIREMENT_WHEEL" in os.environ else []),
    ids=lambda arm: "installed-wheel" if arm == "wheel" else "source",
)
def isolated_todo_distribution(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    package_root = tmp_path / "package"
    package = package_root / "loopx"
    command = [sys.executable, "-m", "loopx.cli"]
    if request.param == "source":
        shutil.copytree(Path(__file__).resolve().parents[2] / "loopx", package,
                        ignore=shutil.ignore_patterns("__pycache__"))
    else:
        installed = subprocess.run(
            ["uv", "pip", "install", "--no-deps", "--python", sys.executable,
             "--target", str(package_root), str(request.getfixturevalue("retirement_wheel"))],
            cwd=tmp_path, capture_output=True, text=True, timeout=120,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        entry = package_root / "bin/loopx"
        assert entry.is_file()
        command = [sys.executable, str(entry)]
    monkeypatch.setenv("PYTHONPATH", str(package_root))
    provenance = json.loads(subprocess.check_output(
        [sys.executable, "-c", "import json,loopx; from importlib import metadata; "
         "print(json.dumps({'module':loopx.__file__, "
         "'distribution':str(metadata.distribution('loopx').locate_file(''))}))"],
        cwd=tmp_path, text=True,
    ))
    assert Path(provenance["module"]) == package / "__init__.py"
    if request.param == "wheel":
        assert Path(provenance["distribution"]) == package_root
        # The packaged typed owner must be shipped, not borrowed from checkout.
        assert (package / "control_plane/todos/succession.ts").is_file()
        assert (package / "control_plane/effect_runtime_server.ts").is_file()
    return package, command


@pytest.fixture
def without_source_todo_writers(isolated_todo_distribution):
    package, command = isolated_todo_distribution
    _remove_source_todo_writers(package)
    return command


def _remove_source_todo_writers(package):
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


@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
@pytest.mark.parametrize("corrupt_cursor", [False, True], ids=["recover", "reject-unproved-cursor"])
def test_pending_source_outbox_survives_producer_retirement(
    tmp_path, isolated_todo_distribution, window, corrupt_cursor,
):
    from shadow_e2e_fixture import workspace

    package, command = isolated_todo_distribution
    w = workspace(tmp_path / "shadow", bootstrap=False)
    w.command, w.cwd, w.package = tuple(command), tmp_path, package
    assert w.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    original = w.add("Already delivered source work")["todo_id"]
    w.crash(window, "todo", "add", "--role", "agent", "--text", "Pending source work")

    def history():
        result = subprocess.run(
            [sys.executable, "-c",
             "import json,sys;from pathlib import Path;"
             "from loopx.control_plane.coordination.local_authority_shadow_adapter "
             "import read_local_authority_shadow;"
             "print(json.dumps(read_local_authority_shadow(runtime_root=Path(sys.argv[1]),"
             "goal_id=sys.argv[2],scan_limit=10000)))", str(w.runtime), w.goal],
            cwd=tmp_path, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    before = history()
    assert len(before["proof"]["transactions"]) == (2 if window == "before_commit" else 3)
    assert original in {todo["todo_id"] for todo in before["head"]["todos"]}
    # Use the existing partition owner rather than assuming its on-disk layout.
    from loopx.control_plane.coordination.local_authority_shadow_outbox import partition_directory
    directory = partition_directory(w.runtime, w.goal, "todos")
    assert len(list(directory.glob("*.prepared.json"))) == 1
    assert len(list(directory.glob("*.committed.json"))) == 1
    source_bytes, registry_bytes = w.state.read_bytes(), w.registry.read_bytes()
    _remove_source_todo_writers(package)

    if corrupt_cursor:
        cursor_path = directory / "drain-cursor.json"
        cursor = json.loads(cursor_path.read_text())
        cursor["last_partition_digest"] = "sha256:" + "f" * 64
        cursor_path.write_text(json.dumps(cursor))
        pending_bytes = {f.name: f.read_bytes() for f in directory.iterdir() if f.is_file()}
        result = w.drain()
        assert result["ok"] is False, result
        assert result["reason_code"] == "outbox_cursor_unproved", result
        assert {f.name: f.read_bytes() for f in directory.iterdir() if f.is_file()} == pending_bytes
        assert history()["proof"]["transactions"] == before["proof"]["transactions"]
    else:
        result = w.drain()
        assert result["ok"] is True, result
        assert result["delivered"] == (1 if window == "before_commit" else 0)
        assert result["replayed"] == (0 if window == "before_commit" else 1)
        recovered = history()
        transactions = recovered["proof"]["transactions"]
        assert transactions[:len(before["proof"]["transactions"])] == before["proof"]["transactions"]
        assert len(transactions) == 3
        assert transactions[-1]["receipts"][0]["write_class"] == "todo_add"
        assert transactions[-1]["receipts"][0]["seq"] == 2
        assert {todo["text"] for todo in recovered["head"]["todos"]} == {
            "Already delivered source work", "Pending source work"}
        assert json.loads((directory / "drain-cursor.json").read_text())["last_seq"] == 2
        assert sorted(f.name for f in directory.iterdir()) == ["drain-cursor.json"]
        assert w.drain()["outcome"] == "nothing_pending"
        assert history()["proof"]["transactions"] == transactions
    assert w.state.read_bytes() == source_bytes
    assert w.registry.read_bytes() == registry_bytes


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_cli_lifecycle_without_source_todo_writers(tmp_path, provider, without_source_todo_writers):
    registry, _runtime, state = promoted_create_fixture(tmp_path, provider=provider)

    def cli(*args):
        actor = ["--agent-id", "agent-a"] if args[0] in ("update", "complete", "supersede") else []
        result = subprocess.run(
            [*without_source_todo_writers, "--format", "json",
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
            [*without_source_todo_writers, "--format", "json", "--registry", str(registry),
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
            [*without_source_todo_writers, "--format", "json",
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
    tmp_path, request, provider, material_change, source_writers_present, isolated_todo_distribution,
):
    from test_leased_monitor_poll import LEASE, PROOF
    from test_monitor_followthrough_contract import GOAL_ID, AGENT_ID
    from test_native_monitor_poll import _canonical

    _package, command = isolated_todo_distribution
    if not source_writers_present:
        command = request.getfixturevalue("without_source_todo_writers")
    registry, runtime, display, monitor = _canonical(
        tmp_path, native=True, provider=provider, lease=LEASE,
    )
    display.unlink()

    def cli(*args, succeeds=True):
        result = subprocess.run(
            [*command, "--format", "json",
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
