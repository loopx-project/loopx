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


def test_fresh_install_previews_its_first_goal_before_any_registry_exists(tmp_path):
    # An installed LoopX has no registry until the first Goal is created, and
    # the first-Goal home invites exactly that creation. The proposal must be
    # reviewable then: the absent registry is a stable fact to fingerprint, a
    # registry that appears afterwards reads as a change, and an unreadable
    # registry still refuses.
    project = tmp_path / "project"
    project.mkdir()
    registry = project / ".loopx" / "registry.json"
    service = ChatActionService(store=ChatActionStore(tmp_path / "actions"),
        registry_path=registry, workspace_roots=[project])

    proposal = preview(service, "current", key="first")

    assert proposal["action_kind"] == "goal.create"
    assert proposal["workspace_binding"]["label"] == "project"
    assert proposal["expected_state_fingerprint"] == "registry-absent"
    assert service._registry_fingerprint() == "registry-absent", "still absent at apply time: the review stands"
    registry.parent.mkdir()
    registry.write_text(json.dumps({"schema_version": 1, "goals": []}))
    assert service._registry_fingerprint() != "registry-absent", "a registry that appeared in between is a change"
    service.registry_path = registry.parent  # a directory, not a file: unreadable, not absent
    with pytest.raises(ValueError, match="registry is unavailable"):
        service._registry_fingerprint()


def test_first_creation_persists_under_the_serving_chat_runtime_root(tmp_path, monkeypatch):
    # A fresh install has no registry, so a creation has no recorded runtime
    # root to follow. It must persist where the serving Chat keeps its own
    # state (its selected runtime root), never in the implicit default beside
    # it; replaying the original operation keeps that root, and a registry
    # that appears between preview and apply goes stale instead of applying.
    import http.client
    import runpy
    import threading
    from pathlib import Path

    from loopx import paths
    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.chat_actions import goal_workspace_ref
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    implicit_default = tmp_path / "implicit-default"
    monkeypatch.setattr(paths, "select_default_runtime_root", lambda: implicit_default)
    selected = tmp_path / "selected-runtime"
    project = tmp_path / "project"
    project.mkdir()
    registry = project / ".loopx" / "registry.json"
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/loopx-chat-actions-smoke.py"))
    sessions = ChatSessionStore(selected)
    controller = fixture["FakeRuntimeController"](sessions)
    controller.project_contexts = ChatProjectContexts([project])
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.runtime_root_override = registry, selected, str(selected)
    server.action_store = ChatActionStore(selected / "chat/actions")
    server.action_service = ChatActionService(store=server.action_store, registry_path=registry,
        chat_store=sessions, runtime_controller=controller, workspace_roots=[project],
        runtime_root_override=str(selected))
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

    def create(key, goal_id):
        return {"action_kind": "goal.create", "summary": "Create a Goal", "idempotency_key": key,
                "context": {"kind": "manager", "goal_id": None},
                "normalized_parameters": {"goal_id": goal_id, "title": "First Goal", "objective": "Start here",
                                          "agent_id": "codex", "workspace_ref": goal_workspace_ref(project)}}

    def entries(root):
        return sorted(str(path.relative_to(root)) for path in root.rglob("*")) if root.exists() else []

    try:
        assert not registry.exists()
        status, previewed = request("/api/actions/preview", create("first", "goal-first"))
        assert status == 201, previewed
        assert previewed["proposal"]["expected_state_fingerprint"] == "registry-absent"
        proposal_id = previewed["proposal"]["proposal_id"]
        status, applied = request(f"/api/actions/{proposal_id}/apply", {})
        assert status in {200, 202}, applied

        recorded = json.loads(registry.read_text())
        assert [goal["id"] for goal in recorded["goals"]] == ["goal-first"]
        assert Path(str(recorded["common_runtime_root"])).resolve() == selected.resolve(), recorded
        assert any("goal-first" in entry for entry in entries(selected)), "canonical Goal state lives under the selected root"
        assert entries(implicit_default) == [], "nothing lands in the implicit default runtime root"

        # Replaying the original operation keeps one Goal under the same root.
        status, replayed = request(f"/api/actions/{proposal_id}/apply", {})
        assert status in {200, 202, 409}, replayed
        again = json.loads(registry.read_text())
        assert [goal["id"] for goal in again["goals"]] == ["goal-first"]
        assert Path(str(again["common_runtime_root"])).resolve() == selected.resolve()
        assert entries(implicit_default) == []

        # A registry that appears between preview and apply is a change: the
        # proposal goes stale rather than creating against stale facts.
        registry.unlink()
        status, second = request("/api/actions/preview", create("second", "goal-second"))
        assert status == 201, second
        assert second["proposal"]["expected_state_fingerprint"] == "registry-absent"
        registry.write_text(json.dumps(recorded), encoding="utf-8")
        status, stale = request(f"/api/actions/{second['proposal']['proposal_id']}/apply", {})
        assert stale["proposal"]["status"] != "applied", stale
        assert [goal["id"] for goal in json.loads(registry.read_text())["goals"]] == ["goal-first"]
        assert entries(implicit_default) == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_first_goal_preview_refuses_an_unavailable_agent_as_a_typed_gate(tmp_path):
    # A fresh install often has no usable Agent yet. "Create a Goal" must not
    # fail with a protocol sentence: the preview answers with a typed gate that
    # names the Agent and the catalog's own reason, writes nothing, and the same
    # request previews once the Agent is available.
    import http.client
    import runpy
    import threading
    from pathlib import Path

    from loopx.capabilities.native_chat.project_context import ChatProjectContexts
    from loopx.chat_actions import goal_workspace_ref
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/loopx-chat-actions-smoke.py"))
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    registry = project / ".loopx" / "registry.json"
    sessions = ChatSessionStore(runtime)
    controller = fixture["FakeRuntimeController"](sessions)
    controller.project_contexts = ChatProjectContexts([project])
    catalog = {"rows": [{"agent_id": "codex", "available": False, "tool_calls": True, "trust_scope": "read_only",
                         "unavailable_reason": "executable_not_found", "executable": "/opt/bins/codex"}]}
    controller.capabilities = lambda: list(catalog["rows"])
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.runtime_root_override = registry, runtime, str(runtime)
    server.action_store = ChatActionStore(runtime / "chat/actions")
    server.action_service = ChatActionService(store=server.action_store, registry_path=registry,
        chat_store=sessions, runtime_controller=controller, workspace_roots=[project])
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

    create = {"action_kind": "goal.create", "summary": "Create a Goal", "idempotency_key": "no-agent",
              "context": {"kind": "manager", "goal_id": None},
              "normalized_parameters": {"goal_id": "goal-first", "title": "First Goal", "objective": "Start here",
                                        "agent_id": "codex", "workspace_ref": goal_workspace_ref(project)}}
    try:
        status, gated = request("/api/actions/preview", create)
        assert status == 409, gated
        assert gated["error_code"] == "action_preview_gate" and gated["write_attempted"] is False
        gate = gated["gate"]
        assert gate["kind"] == "agent_unavailable" and gate["agent_id"] == "codex" and gate["registered"] is True
        assert gate["unavailable_reason"] == "executable_not_found" and gate["executable"] == "/opt/bins/codex"
        assert not registry.exists(), "a refused preview writes nothing"
        assert request("/api/actions")[1]["proposals"] == [], "a refused preview stores no proposal"

        catalog["rows"] = []
        status, unregistered = request("/api/actions/preview", create)
        assert status == 409 and unregistered["gate"]["kind"] == "agent_unavailable"
        assert unregistered["gate"]["registered"] is False and "unavailable_reason" not in unregistered["gate"]

        # With a usable Agent the same request previews. (A registry is written
        # here only because this check is about the Agent gate, not about the
        # fresh-install fingerprint that a separate change covers.)
        catalog["rows"] = [{"agent_id": "codex", "available": True, "tool_calls": True, "trust_scope": "read_only"}]
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({"schema_version": 1, "goals": []}), encoding="utf-8")
        status, previewed = request("/api/actions/preview", create)
        assert status == 201, previewed
        assert previewed["proposal"]["action_kind"] == "goal.create"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("restriction,message", [
    ("tools", "selected Agent endpoint cannot execute Goal tools"),
    ("trust", "selected Agent endpoint has an incompatible trust scope"),
    ("mapping", "remote Agent endpoint needs a workspace mapping"),
])
def test_available_first_agent_retains_its_real_eligibility_refusal(tmp_path, restriction, message):
    import http.client
    import sys
    import threading

    from loopx.chat_actions import goal_workspace_ref
    from loopx.chat_endpoints import AgentEndpointRegistry
    from loopx.chat_runtime import ChatRuntimeController
    from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
    from loopx.chat_store import ChatSessionStore

    project = tmp_path / "project"
    project.mkdir()
    registry = project / ".loopx/registry.json"
    runtime = tmp_path / "runtime"
    sessions = ChatSessionStore(runtime)
    endpoints = AgentEndpointRegistry(sessions.root)
    endpoint = {"agent_id": "remote-first", "display_name": "Remote first Agent",
                "command": [sys.executable], "location": "remote"}
    endpoints.upsert(endpoint)
    controller = ChatRuntimeController(store=sessions, endpoint_registry=endpoints,
        codex_bin=str(tmp_path / "no-codex"), claude_bin=str(tmp_path / "no-claude"),
        kiro_cli_bin=str(tmp_path / "no-kiro"))
    native_capabilities = controller.capabilities
    def capabilities():
        rows = native_capabilities()
        for row in rows:
            if row["agent_id"] == "remote-first":
                if restriction == "tools":
                    row["tool_calls"] = False
                if restriction == "trust":
                    row["trust_scope"] = "incompatible"
        return rows
    controller.capabilities = capabilities
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root, server.runtime_root_override = registry, runtime, str(runtime)
    server.action_store = ChatActionStore(runtime / "chat/actions")
    server.action_service = ChatActionService(store=server.action_store, registry_path=registry,
        chat_store=sessions, runtime_controller=controller, workspace_roots=[project])
    server.chat_store, server.runtime_controller, server.verbose = sessions, controller, False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = {"action_kind": "goal.create", "summary": "Create a Goal", "idempotency_key": "same-first-request",
               "context": {"kind": "manager", "goal_id": None},
               "normalized_parameters": {"goal_id": "goal-first", "title": "First Goal", "objective": "Start here",
                                         "agent_id": "remote-first", "workspace_ref": goal_workspace_ref(project)}}
    encoded = json.dumps(request)
    def preview():
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
        try:
            connection.request("POST", "/api/actions/preview", encoded, {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        assert endpoints.get("remote-first").available()
        status, rejected = preview()
        assert status == 400, rejected
        assert rejected["error"] == message and "gate" not in rejected
        assert not registry.exists() and server.action_store.list() == []
        controller.capabilities = native_capabilities
        endpoints.upsert({**endpoint, "workspace_mapping": [
            {"local_root": str(project), "agent_root": "/synthetic/workspace"}]})
        status, recovered = preview()
        assert status == 201, recovered
        assert recovered["proposal"]["normalized_parameters"]["agent_id"] == "remote-first"
        assert len(server.action_store.list()) == 1 and not registry.exists()
        assert sessions.list_sessions() == []
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
