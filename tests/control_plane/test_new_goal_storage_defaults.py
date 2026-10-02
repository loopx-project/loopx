"""New-Goal defaults are creation-time intent, never live provider inheritance."""
import hashlib
import http.client
import json
import subprocess
import sys
import threading
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
    def configure(provider):
        config.write_text(json.dumps({"schema_version": "loopx_machine_configuration_v0", "namespaces": {
            "goal_storage": {"schema_version": "loopx_goal_storage_defaults_v0", "new_goal_provider": provider}}}))
    def bootstrap(goal="first", *extra):
        result = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(project / ".loopx/registry.json"),
            "--runtime-root", str(runtime), "--format", "json", "bootstrap", "--project", str(project), "--goal-id", goal,
            "--objective", "Validate a new project", "--no-global-sync", *extra], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
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
    configure, bootstrap, marker, _, _ = environment
    assert bootstrap()["storage_target"] is None
    configure("sqlite")
    assert bootstrap()["storage_selection"] is None
    assert not marker().exists()


def test_pending_creation_uses_frozen_intent_after_machine_default_changes(environment):
    configure, bootstrap, marker, project, _ = environment
    # Independently model the durable boundary: registry/state published, selector absent.
    bootstrap()
    registry = project / ".loopx/registry.json"
    data = json.loads(registry.read_text())
    data["goals"][0].setdefault("coordination", {})["storage_target"] = {
        "schema_version": "loopx_new_goal_storage_target_v0", "provider": "sqlite"}
    registry.write_text(json.dumps(data))
    configure("file")
    assert bootstrap()["storage_selection"]["provider"] == "sqlite"
    assert json.loads(marker().read_text())["provider"] == "sqlite"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("relative_runtime", [False, True])
def test_app_creation_retries_storage_before_reporting_success(environment, monkeypatch, provider, relative_runtime):
    from loopx.capabilities.machine_configuration import goal_storage
    from loopx.chat_action_store import ChatActionStore
    from loopx.chat_actions import ChatActionService
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.todos import add_goal_todo

    configure, bootstrap, marker, project, runtime = environment
    bootstrap("workspace")
    configure(provider)
    registry = project / ".loopx/registry.json"
    # Registry-relative runtime routing must agree with CLI bootstrap, regardless
    # of the HTTP server process's working directory.
    if relative_runtime:
        data = json.loads(registry.read_text())
        data["common_runtime_root"] = "../runtime"
        registry.write_text(json.dumps(data))
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
    try:
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
                assert not marker("recovery").exists()
                add.assert_not_called()

            goal = next(g for g in json.loads(registry.read_text())["goals"] if g["id"] == "recovery")
            assert goal["coordination"]["storage_target"]["provider"] == provider
            configure("file" if provider == "sqlite" else "sqlite")
            unavailable = False
            code, recovered = request(apply_path, {})
            assert code == 200, recovered
            assert recovered["proposal"]["status"] == "applied"
            assert recovered["proposal"]["receipt"]["outcome"] == "goal_created"
            assert selections[-1]["provider"] == provider
            assert selections[-1]["promotion_performed"] is False
            if provider == "sqlite":
                assert json.loads(marker("recovery").read_text())["provider"] == provider
            else:
                assert not marker("recovery").exists()
            assert add.call_count == 1
            assert request(apply_path, {})[1]["proposal"]["receipt"] == recovered["proposal"]["receipt"]
            assert add.call_count == 1
            assert len(selections) == 1
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


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
