"""Canonical callers must not load unpromoted Todo writers or capture producers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig

import pytest

from canonical_authority_fixture import assert_todo_read_unchanged, isolate_sqlite_runtime, promoted_create_fixture
from test_quota_authority_settlement_journey import _source
import test_quota_settlement_cli as settlement


def test_todo_mutation_import_does_not_load_status():
    result = subprocess.run(
        [sys.executable, "-c",
         "import importlib,sys;sys.modules['loopx.status']=None;"
         "owner=importlib.import_module('loopx.control_plane.todos.mutation_api');"
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
    command = [sys.executable, "-S", "-m", "loopx.cli"]
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
        command = [sys.executable, "-S", str(entry)]
    # Keep dependencies available without processing editable-install .pth files.
    # Their import finder can silently borrow a deleted module from the checkout.
    dependencies = dict.fromkeys(sysconfig.get_paths()[key] for key in ("purelib", "platlib"))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(package_root), *dependencies]))
    provenance = json.loads(subprocess.check_output(
        [sys.executable, "-S", "-c", "import json,loopx; from importlib import metadata; "
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
    _assert_source_todo_writers_absent(package)
    return package, command


@pytest.fixture
def without_source_todo_writers(isolated_todo_distribution):
    _package, command = isolated_todo_distribution
    return command


def _assert_source_todo_writers_absent(package):
    retired = (
        "control_plane/todos/legacy_mutation.py",
        "control_plane/todos/line_update.py",
        "control_plane/scheduler/legacy_monitor_poll.py",
        "control_plane/todos/handoff_mode_source.py",
        "control_plane/coordination/runtime_shadow_writer_adapter.py",
        "control_plane/coordination/handoff_mode_legacy_plan.ts",
    )
    assert all(not (package / relative).exists() for relative in retired)



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

    followthrough = add("agent", "Finish with declared existing work", "followthrough")
    completion_args = ("complete", "--goal-id", "goal-a", "--todo-id", followthrough,
                       "--successor-todo-id", replacement)
    accepted = cli(*completion_args)
    assert accepted["completion_policy"]["linked_successor_id"] == replacement
    cli("complete", "--goal-id", "goal-a", "--todo-id", replacement, "--no-follow-up",
        "--note", "Existing work independently accepted")
    before_replay = cli("list", "--goal-id", "goal-a")
    replay = cli(*completion_args)
    assert replay["idempotent_replay"] is True
    assert replay["completion_policy"] == accepted["completion_policy"]
    assert cli("list", "--goal-id", "goal-a")["authority_read"] == before_replay["authority_read"]
    last = add("agent", "Link already completed work", "last")
    terminal_link = cli("complete", "--goal-id", "goal-a", "--todo-id", last,
                        "--successor-todo-id", replacement)
    assert terminal_link["completion_policy"]["linked_successor_id"] is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_handoff_mode_without_source_todo_writers(tmp_path, provider, without_source_todo_writers):
    registry, runtime, display = promoted_create_fixture(tmp_path, provider=provider)
    config = json.loads(registry.read_text())
    config["common_runtime_root"] = "runtime"
    registry.write_text(json.dumps(config))
    caller = tmp_path / "caller"
    caller.mkdir()

    def cli(*args, succeeds=True, override=False):
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
             *(["--runtime-root", str(runtime)] if override else []), *args, "--goal-id", "goal-a"],
            cwd=caller, capture_output=True, text=True, timeout=45,
        )
        assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
        return json.loads(result.stdout)

    original_display = display.read_bytes()
    before = cli("todo", "list")
    change = ("handoff-mode", "set", "--mode", "hard_lease", "--operation-id", "original-mode")
    assert cli(*change, "--dry-run")["status"] == "planned"
    assert_todo_read_unchanged(cli("todo", "list"), before)
    assert cli(*change)["changed"] is True
    assert cli("handoff-mode", "show", override=True)["handoff_mode"] == "hard_lease"
    assert cli("handoff-mode", "set", "--mode", "soft_claim", "--operation-id", "later-mode")["changed"] is True
    later = cli("todo", "list")
    replay = cli(*change)
    assert replay["status"] == "replayed" and replay["changed"] is False
    assert replay["handoff_mode"] == "hard_lease"
    assert_todo_read_unchanged(cli("todo", "list"), later)
    assert cli("handoff-mode", "show")["handoff_mode"] == "soft_claim"
    mismatch = cli("handoff-mode", "set", "--mode", "soft_claim", "--operation-id", "original-mode", succeeds=False)
    assert mismatch["error_code"] == "coordination_operation_identity_mismatch"
    assert_todo_read_unchanged(cli("todo", "list"), later)
    assert display.read_bytes() == original_display

    backend = runtime / "authority" / f"{provider}-v0"
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    try:
        for args in [("handoff-mode", "show"),
                     ("handoff-mode", "set", "--mode", "hard_lease", "--operation-id", "provider-offline")]:
            failed = cli(*args, succeeds=False)
            assert failed["ok"] is False and failed["error_code"] not in {None, "ImportError"}
            assert not backend.exists() and display.read_bytes() == original_display
    finally:
        offline.rename(backend)
    assert_todo_read_unchanged(cli("todo", "list"), later)


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
    project, runtime, registry, display, prefix = _source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=(
            f"claimed_by={settlement.AGENT_ID} "
            "required_write_scopes=tests/**"
        ),
    )
    config = json.loads(registry.read_text())
    config["goals"][0]["coordination"]["write_scope"] = ["tests/**"]
    registry.write_text(json.dumps(config))
    # Goal intent remains readable while the Markdown Todo display contributes
    # no candidate or lease facts after canonical promotion.
    display.write_text(prefix)

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

    update = ["todo", "update", *identity, "--note", "Canonical delivery retained"]
    # Copy is administrative, while explicitly supplied execution stays fenced.
    rejected = run(*update, "--task-lease-idempotency-key", "not-acquired",
                   "--task-lease-expected-version", "1", succeeds=False)
    assert rejected["error_code"] == "handoff_mode_requires_lease"
    acquired = run("task-lease", "acquire", "--goal-id", settlement.GOAL_ID,
                   "--todo-id", settlement.TODO_ID, "--owner", settlement.AGENT_ID,
                   "--idempotency-key", "delivery", "--expected-version", "0",
                   "--ttl-seconds", "3600", "--write-scope", "tests/**")
    assert acquired["acquired"] is True
    proof = ["--task-lease-idempotency-key", "delivery",
             "--task-lease-expected-version", str(acquired["lease"]["version"])]
    selected = guard(settlement.TURN_ID)
    assert selected["decision"] == "run"
    original = selected["heartbeat_receipt"]["settlement_identity"]
    assert original["todo_id"] == settlement.TODO_ID
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


def test_retired_source_writer_modules_and_exports_are_absent():
    result = subprocess.run(
        [sys.executable, "-c", """
import importlib.util
import sys
import loopx.todos as todos
for module in (
    'loopx.control_plane.todos.line_update',
    'loopx.control_plane.todos.legacy_mutation',
    'loopx.control_plane.scheduler.legacy_monitor_poll',
    'loopx.control_plane.todos.handoff_mode_source',
    'loopx.control_plane.coordination.runtime_shadow_writer_adapter',
):
    assert module not in sys.modules
    assert importlib.util.find_spec(module) is None
for name in (
    'apply_todo_update_to_lines',
    'link_generated_successor_todo_ids',
    'link_superseding_todo_id',
    'successor_add_kwargs',
    'upsert_todo_metadata',
):
    assert not hasattr(todos, name)
"""],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
