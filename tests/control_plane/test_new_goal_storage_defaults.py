"""New-Goal defaults are creation-time intent, never live provider inheritance."""
import hashlib
import http.client
import json
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from loopx.capabilities.machine_configuration.builtins import build_builtin_machine_configuration_registry
from loopx.capabilities.configuration_ui import build_capability_configuration_catalog
from loopx.capabilities.machine_configuration.goal_storage import normalize_goal_storage_defaults
from loopx.control_plane.effect_runtime import restart_effect_runtime
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


@pytest.fixture
def environment(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    config = runtime / "machine/configuration.json"
    config.parent.mkdir(parents=True)
    def configure(provider, *, mode=None):
        defaults = {"schema_version": "loopx_goal_storage_defaults_v0", "new_goal_provider": provider}
        if mode is not None:
            defaults.update(schema_version="loopx_goal_storage_defaults_v1", canonical_creation=True, new_goal_handoff_mode=mode)
        config.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "goal_storage": defaults}}))
    def bootstrap(goal="first", *extra, expected_code=0):
        result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(project / ".loopx/registry.json"),
            "--runtime-root", str(runtime), "--format", "json", "bootstrap", "--project", str(project), "--goal-id", goal,
            "--objective", "Validate a new project", "--no-global-sync", *extra], capture_output=True, text=True, timeout=60)
        assert result.returncode == expected_code, result.stdout + result.stderr
        return json.loads(result.stdout)
    def marker(goal="first"):
        return runtime / "authority" / f"provider-{hashlib.sha256(goal.encode()).hexdigest()}.json"
    yield configure, bootstrap, marker, project, runtime
    restart_effect_runtime()


def test_creation_freezes_target_and_reconnect_does_not_follow_changed_defaults(environment):
    configure, bootstrap, marker, _, _ = environment
    configure("sqlite")
    preview = bootstrap("first", "--dry-run")
    assert preview["storage_target"]["provider"] == "sqlite"
    assert not marker().exists()
    actual = bootstrap()
    assert actual["storage_selection"]["promotion_performed"] is False
    assert json.loads(marker().read_text())["provider"] == "sqlite"
    configure("file")
    assert bootstrap()["storage_selection"]["provider"] == "sqlite"
    assert bootstrap("second")["storage_selection"]["provider"] == "file"
    assert not marker("second").exists()  # An explicit File selector requires a committed head.


def test_existing_implicit_file_goal_is_not_retargeted(environment):
    configure, bootstrap, marker, project, _ = environment
    # A supported historical Goal has no creation target, independently of the
    # current release's default for a new Goal.
    configure("file")
    bootstrap()
    registry = project / ".loopx/registry.json"
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"].pop("storage_target")
    registry.write_text(json.dumps(data))
    configure("sqlite")
    assert bootstrap("first", "--dry-run")["storage_target"] is None
    assert bootstrap()["storage_selection"] is None
    assert not marker().exists()


@pytest.mark.parametrize("configuration_present", [False, True])
def test_unconfigured_new_goal_defaults_to_canonical_sqlite_and_retains_later_writes(environment, configuration_present):
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    configure, bootstrap, _, project, runtime = environment
    configuration = runtime / "machine/configuration.json"
    if configuration_present:
        configuration.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "todo_replan_cadence": {"schema_version": "todo_replan_cadence_machine_defaults_v1", "count_unit": "completed_todos", "count": 5}}}))
    target = {"schema_version": "loopx_new_goal_storage_target_v1", "provider": "sqlite", "handoff_mode": "hard_lease"}
    assert bootstrap("first", "--dry-run")["storage_target"] == target
    assert not (runtime / "authority-transition").exists()
    created = bootstrap()
    assert created["storage_target"] == target
    assert created["storage_selection"]["authority_initialized"] is True
    added = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(project / ".loopx/registry.json"),
        "--runtime-root", str(runtime), "--format", "json", "todo", "add", "--goal-id", "first", "--role", "agent",
        "--text", "Retain the default Goal's later work"], capture_output=True, text=True, timeout=60)
    assert added.returncode == 0, added.stdout + added.stderr
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="first", include_leases=True)
    assert before["source_authority"] == "sqlite_v0"
    assert before["handoff_mode"] == "hard_lease"
    assert len(before["todos"]) == 1
    configure("file", mode="soft_claim")
    Path(created["state_file"]).unlink()
    restart_effect_runtime()
    retried = bootstrap()
    assert retried["storage_selection"]["creation_operation_id"] == created["storage_selection"]["creation_operation_id"]
    assert retried["storage_target"] == target
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="first", include_leases=True) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_explicit_canonical_creation_disabled_remains_target_only(environment, provider):
    _, bootstrap, _, _, runtime = environment
    configuration = runtime / "machine/configuration.json"
    configuration.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": provider,
                         "canonical_creation": False, "new_goal_handoff_mode": "soft_claim"}}}))
    created = bootstrap()
    assert created["storage_target"] == {"schema_version": "loopx_new_goal_storage_target_v0", "provider": provider}
    assert created["storage_selection"]["promotion_performed"] is False
    assert "authority_initialized" not in created["storage_selection"]


@pytest.mark.parametrize("invalid_configuration", ["invalid_json", "invalid_namespace", "directory", "broken_symlink"])
def test_invalid_configuration_does_not_become_an_unconfigured_default(environment, invalid_configuration):
    _, bootstrap, _, project, runtime = environment
    configuration = runtime / "machine/configuration.json"
    if invalid_configuration == "directory":
        configuration.mkdir()
    elif invalid_configuration == "broken_symlink":
        configuration.symlink_to(configuration.parent / "missing-configuration.json")
    else:
        configuration.write_text("{" if invalid_configuration == "invalid_json" else json.dumps({
            "schema_version": "loopx_machine_configuration_v0", "namespaces": {"goal_storage": None}}))
    rejected = bootstrap(expected_code=1)
    assert rejected["ok"] is False
    assert not (project / ".loopx/registry.json").exists()
    assert not (runtime / "authority-transition").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["soft_claim", "hard_lease"])
def test_opted_in_creation_has_complete_canonical_authority_and_frozen_policy(environment, provider, mode):
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    configure, bootstrap, _, _, runtime = environment
    configuration = runtime / "machine/configuration.json"
    configuration.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
        "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v1", "new_goal_provider": provider,
                         "canonical_creation": True, "new_goal_handoff_mode": mode}}}))
    preview = bootstrap("native", "--dry-run")
    assert not (runtime / "authority-transition").exists()
    actual = bootstrap("native")
    source = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="native", include_leases=True)
    assert source is not None
    assert source["source_authority"] == f"{provider}_v0"
    assert source["handoff_mode"] == mode
    assert source["todos"] == []
    assert actual["storage_selection"]["authority_initialized"] is True
    assert actual["storage_target"] == preview["storage_target"]
    configure("file" if provider == "sqlite" else "sqlite")
    restart_effect_runtime()
    reconnect = bootstrap("native")
    assert reconnect["storage_selection"]["authority_initialized"] is True
    assert reconnect["storage_selection"]["handoff_mode"] == mode


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("mode", ["soft_claim", "hard_lease"])
@pytest.mark.parametrize("compatibility_source", ["missing", "unreadable"])
def test_completed_cli_creation_replays_without_compatibility_source(environment, provider, mode, compatibility_source):
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

    configure, bootstrap, _, project, runtime = environment
    configure(provider, mode=mode)
    created = bootstrap()
    registry = project / ".loopx/registry.json"
    added = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "todo", "add", "--goal-id", "first",
        "--role", "agent", "--text", "Preserve a later native Todo"], capture_output=True, text=True, timeout=60)
    assert added.returncode == 0, added.stdout + added.stderr
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="first", include_leases=True)
    assert len(before["todos"]) == 1
    state = Path(created["state_file"])
    if compatibility_source == "missing":
        state.unlink()
    else:
        state.write_bytes(b"\xff")
    configure("file" if provider == "sqlite" else "sqlite", mode="hard_lease" if mode == "soft_claim" else "soft_claim")
    restart_effect_runtime()
    recovered = bootstrap()
    selection = recovered["storage_selection"]
    assert selection["creation_operation_id"] == created["storage_selection"]["creation_operation_id"]
    assert selection["provider_revision"] == created["storage_selection"]["provider_revision"]
    assert selection["handoff_mode"] == mode
    assert selection["provider"] == provider
    assert selection["legacy_fallback_used"] is False
    assert recovered["state_action"] == "kept-existing"
    assert next(action for action in recovered["actions"] if action["path"] == str(state))["action"] == "kept-existing"
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="first", include_leases=True) == before
    assert not state.exists() if compatibility_source == "missing" else state.read_bytes() == b"\xff"
    assert bootstrap("first", "--dry-run")["state_action"] == "kept-existing"
    rejected = bootstrap("first", "--force", expected_code=1)
    assert "cannot rebuild" in rejected["error"]
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="first", include_leases=True) == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("failure", ["missing_authority", "changed_operation", "changed_source"])
def test_cli_creation_recovery_cannot_recreate_or_adopt_authority(environment, provider, failure):
    configure, bootstrap, _, project, runtime = environment
    configure(provider, mode="hard_lease")
    created = bootstrap()
    restart_effect_runtime()
    state = Path(created["state_file"])
    state.unlink()
    digest = hashlib.sha256(b"first").hexdigest()
    authority = runtime / "authority" / f"{provider}-v0" / (
        f"authority-{digest}.sqlite" if provider == "sqlite" else f"authority-store-{digest[:16]}.json")
    before = authority.read_bytes()
    if failure == "missing_authority":
        authority.rename(authority.with_suffix(".unavailable"))
    elif failure == "changed_operation":
        registry = project / ".loopx/registry.json"
        data = json.loads(registry.read_text())
        data["goals"][0]["creation_operation_id"] = "another-creation-operation"
        registry.write_text(json.dumps(data))
    wrong_source = project / "another-source.md"
    rejected = bootstrap("first", *( ["--state-file", str(wrong_source)] if failure == "changed_source" else []), expected_code=1)
    assert rejected["ok"] is False
    expected_error = {"missing_authority": "restore", "changed_operation": "different operation", "changed_source": "not bound"}[failure]
    assert expected_error in rejected["error"], rejected
    assert not authority.exists() if failure == "missing_authority" else authority.read_bytes() == before
    assert not state.exists()
    assert not wrong_source.exists()


def test_pending_creation_uses_frozen_intent_after_machine_default_changes(environment):
    configure, bootstrap, marker, project, _ = environment
    # Independently model the durable boundary: registry/state published, selector absent.
    configure("file")
    bootstrap()
    registry = project / ".loopx/registry.json"
    data = json.loads(registry.read_text())
    data["goals"][0].setdefault("coordination", {})["storage_target"] = {
        "schema_version": "loopx_new_goal_storage_target_v0", "provider": "sqlite"}
    registry.write_text(json.dumps(data))
    configure("file")
    assert bootstrap()["storage_selection"]["provider"] == "sqlite"
    assert json.loads(marker().read_text())["provider"] == "sqlite"


@pytest.fixture
def app(environment):
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler

    _, bootstrap, _, project, runtime = environment
    bootstrap("workspace")
    registry = project / ".loopx/registry.json"
    store = ChatActionStore(runtime / "actions")
    service = ChatActionService(store=store, registry_path=registry)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.action_store = store
    server.action_service = service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body):
        connection = http.client.HTTPConnection(*server.server_address, timeout=30)
        try:
            connection.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    try:
        yield store, request
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("relative_runtime", [False, True])
@pytest.mark.parametrize("mode", [None, "soft_claim", "hard_lease"])
def test_app_creation_retries_storage_before_reporting_success(environment, app, monkeypatch, provider, relative_runtime, mode):
    from loopx.capabilities.machine_configuration import goal_storage
    from loopx.todos import add_goal_todo

    configure, _, marker, project, runtime = environment
    store, request = app
    configure(provider, mode=mode)
    registry = project / ".loopx/registry.json"
    # Registry-relative runtime routing must agree with CLI bootstrap, regardless
    # of the HTTP server process's working directory.
    if relative_runtime:
        data = json.loads(registry.read_text())
        data["common_runtime_root"] = "../runtime"
        from loopx.paths import resolve_runtime_root
        assert resolve_runtime_root(data, registry_path=registry).resolve() == runtime.resolve()
        registry.write_text(json.dumps(data))
    unavailable = True
    selections = []
    effect = goal_storage.effect_runtime_result

    def storage_effect(method, payload):
        if payload.get("action") == "initialize":
            if unavailable:
                raise TimeoutError("storage initialization interrupted")
            result = effect(method, payload)
            selections.append(result)
            return result
        return effect(method, payload)

    monkeypatch.setattr(goal_storage, "effect_runtime_result", storage_effect)
    code, preview = request("/api/actions/preview", {
        "action_kind": "goal.create", "summary": "Create a Goal",
        "normalized_parameters": {
            "goal_id": "recovery", "title": "Creation recovery",
            "objective": "Resume interrupted storage initialization",
            "workspace_ref": "current", "agent_id": "codex",
            "heartbeat": {"enabled": False}, "initial_todos": ["Verify recovery"],
        },
        "context": {"kind": "goal_channel", "goal_id": "workspace"},
        "idempotency_key": "storage-recovery",
    })
    assert code == 201, preview
    proposal_id = preview["proposal"]["proposal_id"]
    apply_path = f"/api/actions/{proposal_id}/apply"
    with patch("loopx.chat_actions.add_goal_todo", wraps=add_goal_todo) as add:
        for _ in range(2):
            code, failure = request(apply_path, {})
            assert code == 424, failure
            assert failure["error_code"] == "canonical_action_failed"
            proposal = store.load(proposal_id)
            assert proposal["status"] == "failed"
            assert "goal_bootstrapped" not in proposal["checkpoint"]["steps"]
            assert not marker("recovery").exists(), failure
            add.assert_not_called()

        goals = json.loads(registry.read_text())["goals"]
        assert any(g["id"] == "recovery" for g in goals), failure
        goal = next(g for g in goals if g["id"] == "recovery")
        assert goal["coordination"]["storage_target"]["provider"] == provider
        assert goal["creation_operation_id"] == proposal_id
        configure("file" if provider == "sqlite" else "sqlite")
        unavailable = False
        code, recovered = request(apply_path, {})
        assert code == 200, recovered
        assert recovered["proposal"]["status"] == "applied"
        assert recovered["proposal"]["receipt"]["outcome"] == "goal_created"
        assert selections[-1]["provider"] == provider
        assert selections[-1]["promotion_performed"] is False
        if mode is not None:
            from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
            source = read_canonical_todos_if_promoted(runtime_root=project.parent / "runtime", goal_id="recovery", include_leases=True)
            assert source["handoff_mode"] == mode
            assert len(source["todos"]) == 1
            assert source["todos"][0]["text"] == "Verify recovery"
            assert source["source_authority"] == f"{provider}_v0"
            assert selections[-1]["authority_initialized"] is True
        if provider == "sqlite":
            assert json.loads(marker("recovery").read_text())["provider"] == provider
        else:
            assert not marker("recovery").exists()
        assert add.call_count == 1
        assert request(apply_path, {})[1]["proposal"]["receipt"] == recovered["proposal"]["receipt"]
        assert add.call_count == 1
        assert len(selections) == (1 if mode is None else 2)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_canonical_creation_force_rebuild_is_rejected_without_changing_todos(environment, provider):
    configure, bootstrap, _, project, runtime = environment
    configure(provider, mode="hard_lease")
    created = bootstrap()
    state = Path(created["state_file"])
    before = state.read_bytes()
    rejected = bootstrap("first", "--force", expected_code=1)
    assert "cannot rebuild" in rejected["error"]
    assert state.read_bytes() == before
    assert created["storage_selection"]["legacy_writer_fenced"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("collision", ["other_workspace", "same_workspace", "during_bootstrap"])
def test_app_creation_cannot_adopt_a_competing_goal(environment, app, monkeypatch, provider, collision):
    from loopx import chat_actions

    configure, bootstrap, marker, project, _ = environment
    store, request = app
    registry = project / ".loopx/registry.json"
    other_project = project if collision == "same_workspace" else project.parent / "other"
    other_project.mkdir(exist_ok=True)
    code, preview = request("/api/actions/preview", {
        "action_kind": "goal.create", "summary": "Create a Goal",
        "normalized_parameters": {
            "goal_id": "competing", "title": "Requested Goal",
            "workspace_ref": "current", "agent_id": "codex",
            "initial_todos": ["Only the original creation may add this Todo"],
        },
        "context": {"kind": "goal_channel", "goal_id": "workspace"},
        "idempotency_key": "competing-creation",
    })
    assert code == 201, preview
    proposal_id = preview["proposal"]["proposal_id"]
    before = {}

    def create_competitor():
        # Real CLI registration, then model its durable pre-initialization
        # boundary independently. No fake success receipt or provider is used.
        configure("file")
        bootstrap("competing", "--project", str(other_project))
        data = json.loads(registry.read_text())
        goal = next(g for g in data["goals"] if g["id"] == "competing")
        goal.setdefault("coordination", {})["storage_target"] = {
            "schema_version": "loopx_new_goal_storage_target_v0", "provider": provider}
        registry.write_text(json.dumps(data))
        before["registry"] = registry.read_bytes()
        before["files"] = {str(p): p.read_bytes() for p in other_project.rglob("*.md")}

    if collision == "during_bootstrap":
        real_bootstrap = chat_actions.bootstrap_project
        def race(**kwargs):
            create_competitor()
            return real_bootstrap(**kwargs)
        monkeypatch.setattr(chat_actions, "bootstrap_project", race)
    else:
        create_competitor()

    with patch("loopx.chat_actions.initialize_goal_storage_target", wraps=chat_actions.initialize_goal_storage_target) as initialize, patch("loopx.chat_actions.add_goal_todo", wraps=chat_actions.add_goal_todo) as add:
        code, rejected = request(f"/api/actions/{proposal_id}/apply", {})
        assert code == 409, rejected
        assert rejected["proposal"]["gate"]["kind"] == "goal_id_conflict"
        initialize.assert_not_called()
        add.assert_not_called()
    assert registry.read_bytes() == before["registry"]
    assert {str(p): p.read_bytes() for p in other_project.rglob("*.md")} == before["files"]
    assert not marker("competing").exists()
    assert not (project / ".codex/goals/competing").exists() or collision == "same_workspace"
    checkpoint = store.load(proposal_id).get("checkpoint") or {}
    assert "goal_bootstrapped" not in checkpoint.get("steps", {})
    if collision != "during_bootstrap":
        assert not checkpoint  # Reject before even a workspace success checkpoint.


def test_machine_editor_is_machine_only_and_configuration_rejects_activation():
    namespaces = build_builtin_machine_configuration_registry().public_catalog()["namespaces"]
    catalog = build_capability_configuration_catalog(machine_namespaces=namespaces)
    item = next(row for row in catalog["capabilities"] if row["capability_id"] == "goal_storage")
    assert item["available_scopes"] == ["machine"]
    assert item["configuration_editor"]["fields"][0]["options"] == ["file", "sqlite"]
    with pytest.raises(ValueError):
        normalize_goal_storage_defaults({"schema_version": "loopx_goal_storage_defaults_v0", "new_goal_provider": "sqlite", "promote": True})


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_created_target_is_used_by_reviewed_promotion(environment, provider):
    from tests.control_plane.shadow_e2e_fixture import ShadowWorkspace
    configure, bootstrap, _, project, runtime = environment
    configure(provider)
    bootstrap()
    registry = project / ".loopx/registry.json"
    state = project / ".codex/goals/first/ACTIVE_GOAL_STATE.md"
    ws = ShadowWorkspace(registry, runtime, state, "first")
    configured = ws.cli("configure-goal", "--coordination-runtime-shadow-file", "--execute")
    assert configured["ok"] is True
    assert ws.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    for n in range(3):
        assert ws.add(f"Qualify creation target {n}")["ok"] is True
    assert ws.drain(budget_seconds="60")["ok"] is True
    assert bootstrap()["storage_selection"]["changed"] is False
    preview = ws.cli("coordination-shadow", "promote", "--handoff-mode-migration", "preserve")
    assert preview["promotion"]["status"] == "preview_ready", preview
    plan = project / "reviewed.json"
    plan.write_text(json.dumps(preview))
    promoted = ws.cli("coordination-shadow", "promote", "--reviewed-plan", str(plan), "--execute")
    assert promoted["promotion"]["canonical_authority"] == f"{provider}_v0", promoted
    created = ws.add("Continue on selected provider")
    assert ws.cli("todo", "list", "--todo-id", created["todo_id"])["authority_read"]["source_authority"] == f"{provider}_v0"
    # The initializer must not override a promoted selection. The separate legacy
    # bootstrap command still rejects a fenced Goal before reaching this helper.
    from loopx.capabilities.machine_configuration.goal_storage import initialize_goal_storage_target
    goal = json.loads(registry.read_text())["goals"][0]
    assert initialize_goal_storage_target(runtime, goal)["status"] == "existing_authority_preserved"
