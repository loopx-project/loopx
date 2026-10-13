"""Explicit creation destinations must not be overridden by conversation routing."""
import hashlib
import json

import pytest

from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService, ProtectedActionGate


@pytest.fixture
def workspace_service(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "goals": [
        {"id": "first-goal", "repo": str(first)},
        {"id": "second-goal", "repo": str(second)},
    ]}))
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"),
        registry_path=registry, workspace_roots=[first, second])
    return service, registry, first, second


def resolve(service, workspace_ref, context_goal=None):
    return service._project_for_goal_create({
        "normalized_parameters": {"workspace_ref": workspace_ref},
        "context": {"kind": "manager", "goal_id": context_goal},
    })


def test_explicit_goal_workspace_wins_over_conversation(workspace_service):
    service, _, _, second = workspace_service
    project, source = resolve(service, "second-goal", "first-goal")
    assert project == second
    assert source["id"] == "second-goal"


@pytest.mark.parametrize("single_goal", [False, True])
def test_configured_workspace_wins_over_context_and_single_goal(workspace_service, single_goal):
    service, registry, _, second = workspace_service
    if single_goal:
        data = json.loads(registry.read_text())
        data["goals"] = data["goals"][:1]
        registry.write_text(json.dumps(data))
    token = "workspace-" + hashlib.sha256(str(second).encode()).hexdigest()[:12]
    project, source = resolve(service, token, "first-goal" if not single_goal else None)
    assert project == second
    assert source["id"] == ""


def test_unknown_selection_never_falls_back_to_context(workspace_service):
    service, _, _, _ = workspace_service
    with pytest.raises(ProtectedActionGate) as error:
        resolve(service, "missing-goal", "first-goal")
    assert error.value.gate["kind"] == "workspace_selection_required"


def test_unavailable_selected_workspace_never_falls_back(workspace_service):
    service, _, _, second = workspace_service
    second.rmdir()
    with pytest.raises(ProtectedActionGate) as error:
        resolve(service, "second-goal", "first-goal")
    assert error.value.gate["kind"] == "workspace_unavailable"


def test_current_retains_existing_context_routing(workspace_service):
    service, _, first, _ = workspace_service
    project, source = resolve(service, "current", "first-goal")
    assert project == first
    assert source["id"] == "first-goal"


def test_bound_steward_rejects_goal_workspace_redirect(workspace_service):
    service, _, _, _ = workspace_service
    with pytest.raises(ValueError, match="bound steward workspace is unavailable"):
        service._project_for_goal_create({
            "normalized_parameters": {"workspace_ref": "second-goal"},
            "context": {"kind": "manager", "goal_id": "first-goal", "binding_id": "steward-binding"},
        })


def preview(service, workspace_ref, context_goal=None, key="create"):
    return service.preview({
        "action_kind": "goal.create", "summary": "Create a Goal", "idempotency_key": key,
        "context": {"kind": "manager", "goal_id": context_goal},
        "normalized_parameters": {"goal_id": f"goal-{key}", "title": "New Goal",
                                  "objective": "Ship it", "workspace_ref": workspace_ref},
    })


@pytest.mark.parametrize("route", ["context_goal", "single_goal", "single_root"])
def test_default_preview_binds_the_workspace_it_resolved(workspace_service, route):
    # "Default workspace" is resolved once, shown to the owner by name, and the
    # confirmed proposal carries the explicit reference apply will resolve, so
    # confirmation cannot land in a different directory than the one reviewed.
    service, registry, first, _ = workspace_service
    if route != "context_goal":
        data = json.loads(registry.read_text())
        data["goals"] = data["goals"][:1] if route == "single_goal" else []
        registry.write_text(json.dumps(data))
    if route == "single_root":
        service.workspace_roots = (first,)
    proposal = preview(service, "current", "first-goal" if route == "context_goal" else None)

    binding = proposal["workspace_binding"]
    assert binding["label"] == "first"
    assert binding["selection"] == "default"
    assert proposal["normalized_parameters"]["workspace_ref"] == binding["workspace_ref"]
    if route == "single_root":
        assert binding["source"] == "configured_workspace" and "goal_id" not in binding
        assert binding["workspace_ref"] == "workspace-" + hashlib.sha256(str(first).encode()).hexdigest()[:12]
    else:
        assert binding["source"] == "goal_workspace"
        assert binding["workspace_ref"] == binding["goal_id"] == "first-goal"
    assert resolve(service, binding["workspace_ref"])[0] == first
    assert str(first) not in json.dumps(binding)


def test_explicit_configured_workspace_preview_names_that_workspace(workspace_service):
    service, _, _, second = workspace_service
    token = "workspace-" + hashlib.sha256(str(second).encode()).hexdigest()[:12]
    binding = preview(service, token, "first-goal")["workspace_binding"]
    assert binding == {
        "schema_version": "loopx_goal_workspace_binding_v0",
        "workspace_ref": token, "label": "second",
        "source": "configured_workspace", "selection": "explicit",
    }


def test_workspace_choices_are_named_by_directory_not_token(tmp_path):
    roots = [tmp_path / "team-a" / "app", tmp_path / "team-b" / "app", tmp_path / "docs"]
    for root in roots:
        root.mkdir(parents=True)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "goals": []}))
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"),
        registry_path=registry, workspace_roots=roots)
    with pytest.raises(ProtectedActionGate) as error:
        preview(service, "current")
    gate = error.value.gate
    assert [candidate["label"] for candidate in gate["candidates"]] == ["team-a/app", "team-b/app", "docs"]
    assert "--scan-path" in gate["next_action"]


def test_same_named_goal_directories_review_apart(tmp_path):
    # Two existing Goals in .../first/project and .../second/project, plus a
    # configured workspace: each review names its own directory distinctly.
    first, second, docs = tmp_path / "first" / "project", tmp_path / "second" / "project", tmp_path / "docs-site"
    for directory in (first, second, docs):
        directory.mkdir(parents=True)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "goals": [
        {"id": "first-goal", "repo": str(first)}, {"id": "second-goal", "repo": str(second)},
    ]}))
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"),
        registry_path=registry, workspace_roots=[docs])
    bindings = [preview(service, goal_id, key=goal_id)["workspace_binding"] for goal_id in ("first-goal", "second-goal")]
    assert [binding["label"] for binding in bindings] == ["first/project", "second/project"]
    assert [resolve(service, binding["workspace_ref"])[0] for binding in bindings] == [first, second]
    assert service.workspace_labels()[docs] == "docs-site"


def test_labels_add_parents_only_until_directories_differ(tmp_path):
    from loopx.chat_actions import goal_workspace_labels

    a, b, c = tmp_path / "a" / "x" / "app", tmp_path / "b" / "x" / "app", tmp_path / "solo"
    labels = goal_workspace_labels([a, b, c, a])
    assert labels == {a: "a/x/app", b: "b/x/app", c: "solo"}
    assert not any(label.startswith(("/", "\\")) for label in labels.values())


def test_project_list_survives_unreadable_goal_registry_while_creation_refuses(tmp_path):
    # The granted project list never depended on the Goal registry. Naming it
    # alongside Goal directories must not make a corrupt registry disconnect
    # ordinary project conversations; Goal creation still refuses that source
    # and works again once the registry is restored.
    import http.client
    import runpy
    import threading
    from pathlib import Path

    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.chat_actions import goal_workspace_ref
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/loopx-chat-actions-smoke.py"))
    registry, _ = fixture["write_registry_fixture"](tmp_path / "source")
    runtime = Path(json.loads(registry.read_text())["common_runtime_root"])
    docs = (tmp_path / "docs-site").resolve()
    docs.mkdir()
    sessions = ChatSessionStore(runtime)
    controller = fixture["FakeRuntimeController"](sessions)
    controller.project_contexts = ChatProjectContexts([docs])
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = registry, runtime
    server.action_store = ChatActionStore(runtime / "chat/actions")
    server.action_service = ChatActionService(store=server.action_store, registry_path=registry,
        chat_store=sessions, runtime_controller=controller, workspace_roots=[docs])
    server.chat_store, server.runtime_controller, server.verbose = sessions, controller, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        try:
            connection.request("GET" if body is None else "POST", path,
                None if body is None else json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    create = {"action_kind": "goal.create", "summary": "Create a Goal", "idempotency_key": "registry-recovery",
              "context": {"kind": "manager", "goal_id": None},
              "normalized_parameters": {"goal_id": "goal-docs", "title": "Docs", "objective": "Check the docs",
                                        "agent_id": "codex", "workspace_ref": goal_workspace_ref(docs)}}
    try:
        original = registry.read_bytes()
        registry.write_text("{not json", encoding="utf-8")
        status, listed = request("/api/chat/projects")
        assert status == 200
        assert [(project["title"], project["goal_workspace_ref"]) for project in listed["projects"]] == [
            ("docs-site", goal_workspace_ref(docs))]
        status, refused = request("/api/actions/preview", create)
        assert status == 400 and refused["ok"] is False

        registry.write_bytes(original)
        status, previewed = request("/api/actions/preview", create)
        assert status == 201, previewed
        assert previewed["proposal"]["workspace_binding"]["label"] == "docs-site"
        proposal_id = previewed["proposal"]["proposal_id"]
        status, applied = request(f"/api/actions/{proposal_id}/apply", {})
        assert status in {200, 202}, applied
        created = next(goal for goal in json.loads(registry.read_text())["goals"] if goal["id"] == "goal-docs")
        assert Path(created["repo"]).resolve() == docs
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
