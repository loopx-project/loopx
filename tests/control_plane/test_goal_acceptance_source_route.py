"""Global App and CLI follow the source Goal, not a valid shared-runtime decoy."""
from __future__ import annotations

import http.client
import hashlib
import json
import shlex
import threading

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from test_goal_acceptance_cli import acceptance_goal as acceptance_goal
from loopx.chat_action_store import ChatActionStore
from loopx.chat_actions import ChatActionService
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.canary_harness import run_json_cli_result


GOAL = "goal-acceptance"


@pytest.fixture
def source_app(acceptance_goal):
    project, document, source_cli, _ = acceptance_goal
    source_registry = project.parent / "registry.json"
    payload = json.loads(source_registry.read_text())
    payload["goals"][0]["coordination"]["registered_agents"] = ["agent-a", "agent-b"]
    source_registry.write_text(json.dumps(payload))
    state = project / "state.md"
    state.write_text(state.read_text() + "\n## Objective\nMaintain the original export\n")
    shared = project.parent / "shared"
    registry = project.parent / "global.json"
    registry.write_text(json.dumps({"schema_version": 1, "registry_role": "global-local",
        "common_runtime_root": str(shared), "goals": [{**payload["goals"][0],
            "source_registry": str(source_registry)}]}))
    # The wrong runtime is deliberately available, not just an empty directory.
    provider = "sqlite" if source_cli("inspect")[1]["source_authority"] == "sqlite_v0" else "file"
    initialize_canonical_authority(shared, GOAL,
        build_todo_runtime_shadow_projection(goal_id=GOAL, todos=[]),
        state_path=state, provider=provider)
    store = ChatActionStore(shared / "chat/actions")
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.registry_path, server.runtime_root = registry, shared
    server.verbose = False
    server.action_store = store
    server.action_service = ChatActionService(store=store, registry_path=registry,
                                             runtime_root_override=str(shared))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=30)
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        return response.status, value

    def global_cli(*arguments, override=None):
        return run_json_cli_result("goal-acceptance", *arguments, "--goal-id", GOAL,
                                   registry_path=registry, runtime_root=override)
    try:
        yield project, document, source_cli, global_cli, request, source_registry, shared
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_objective_inspection_projects_later_source_peer_through_global_app(source_app):
    project, _, source, global_cli, request, source_registry, _ = source_app
    document = project / "objective.json"
    document.write_text(json.dumps({"objective": "Maintain the original export and recovery",
        "reason": "The export is now maintained", "source_ref": "message:owner"}))
    code, revised = source("revise-objective", "--document", str(document),
        "--operation-id", "source-objective", "--expected-provider-revision",
        source("inspect")[1]["provider_revision"], "--execute")
    assert code == 0, revised
    payload = json.loads(source_registry.read_text())
    payload["goals"][0]["coordination"]["registered_agents"].append("later-source-peer")
    source_registry.write_text(json.dumps(payload))
    global_before = (project.parent / "global.json").read_bytes()

    code, inspected = global_cli("inspect")
    assert code == 0, inspected
    status, app = request("GET", f"/api/chat/goal-acceptance?goal_id={GOAL}")
    assert status == 200, app
    assert app["provider_revision"] == inspected["provider_revision"] == revised["provider_revision"]
    objective = app["goal_acceptance_contract"]["goal_objective"]
    assert objective == inspected["goal_acceptance_contract"]["goal_objective"]
    assert {"agent_id": "later-source-peer", "revision": None, "current": False} in objective["peer_adoption"]
    assert source("inspect", "--agent-id", "later-source-peer")[1]["goal_objective_guard"]["allowed"] is False
    assert (project.parent / "global.json").read_bytes() == global_before


def test_source_read_configure_verify_and_explicit_cli_override(source_app):
    project, document, source, global_cli, request, _, shared = source_app
    before = global_cli("inspect", override=shared)[1]
    source_before = source("inspect")[1]
    assert global_cli("inspect")[1] == source_before
    status, app = request("GET", f"/api/chat/goal-acceptance?goal_id={GOAL}")
    assert status == 200
    assert app["provider_revision"] == source_before["provider_revision"]
    assert app["goal_acceptance_contract"] == source_before["goal_acceptance_contract"]
    status, configured = request("POST", "/api/chat/goal-acceptance", {
        "goal_id": GOAL, "action": "configure", "document": json.loads(document.read_text()),
        "expected_provider_revision": app["provider_revision"], "operation_id": "source-checks"})
    assert status == 200 and configured["status"] == "applied", configured
    (project / "artifact.txt").write_text("accepted")
    status, checked = request("POST", "/api/chat/goal-acceptance", {
        "goal_id": GOAL, "action": "verify", "expected_provider_revision": configured["provider_revision"]})
    assert status == 200 and checked["checks_passed"] is True, checked
    assert source("inspect")[1]["goal_acceptance_contract"]["status"] == "accepted"
    assert global_cli("inspect", override=shared)[1] == before, "shared authority must not be read or written instead"


def test_global_proposal_owner_apply_and_peer_adoption_use_source(source_app):
    project, _, source, global_cli, request, _, shared = source_app
    before = global_cli("inspect", override=shared)[1]
    original = (project / "state.md").read_bytes()
    document = project / "objective.json"
    document.write_text(json.dumps({"objective": "Maintain reliable exports", "reason": "Retain ongoing delivery",
                                    "source_ref": "message:owner"}))
    args = ("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
            "--operation-id", "source-objective", "--execute")
    code, prepared = global_cli(*args)
    assert code == 0, prepared
    proposal = prepared["proposal"]
    assert proposal["status"] == "preview_ready"
    assert global_cli(*args)[1]["proposal"]["proposal_id"] == proposal["proposal_id"]
    status, listed = request("GET", f"/api/actions?goal_id={GOAL}")
    assert status == 200 and [p["proposal_id"] for p in listed["proposals"]] == [proposal["proposal_id"]]
    path = f"/api/actions/{proposal['proposal_id']}/apply"
    status, applied = request("POST", path, {})
    assert status == 200 and applied["proposal"]["status"] == "applied", applied
    current = source("inspect", "--agent-id", "agent-a")[1]
    objective = current["goal_acceptance_contract"]["goal_objective"]
    assert objective["original_objective"] == "Maintain the original export"
    assert objective["objective"] == "Maintain reliable exports"
    assert not any(peer["current"] for peer in objective["peer_adoption"])
    document.write_text(json.dumps({"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": current["objective_work_digest"], "review_summary": "The existing export remains the next work."}))
    code, adopted = global_cli("adopt-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "source-adopt", "--expected-provider-revision", current["provider_revision"], "--execute")
    assert code == 0, adopted
    assert request("POST", path, {})[1]["proposal"]["status"] == "applied"
    status, app = request("GET", f"/api/chat/goal-acceptance?goal_id={GOAL}")
    peers = app["goal_acceptance_contract"]["goal_objective"]["peer_adoption"]
    assert {p["agent_id"]: p["current"] for p in peers} == {"agent-a": True, "agent-b": False}
    assert (project / "state.md").read_bytes() == original
    assert global_cli("inspect", override=shared)[1] == before


def test_returned_cli_proposal_commands_complete_without_app(source_app):
    project, _, source, global_cli, request, source_registry, shared = source_app
    decoy = global_cli("inspect", override=shared)[1]
    original = (project / "state.md").read_bytes()
    document = project / "cli-objective.json"
    document.write_text(json.dumps({"objective": "Maintain exports and recovery", "reason": "The owner requested ongoing recovery",
        "source_ref": "message:owner-direction"}))
    code, prepared = global_cli("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "cli-only-source-direction", "--execute")
    assert code == 0, prepared
    commands = prepared["next_cli_actions"]
    # Execute the actual returned argv through the public CLI; no imports of
    # the proposal owner and no HTTP/App operations complete this workflow.
    registry = source_registry.parent / "global.json"
    code, inspected = run_json_cli_result(*shlex.split(commands[0])[1:], registry_path=registry)
    assert code == 0 and inspected["proposal"] == prepared["proposal"], inspected
    code, applied = run_json_cli_result(*shlex.split(commands[1])[1:], registry_path=registry)
    assert code == 0 and applied["proposal"]["status"] == "applied", applied
    current = source("inspect", "--agent-id", "agent-a")[1]
    objective = current["goal_acceptance_contract"]["goal_objective"]
    assert objective["objective"] == "Maintain exports and recovery"
    assert applied["current"]["provider_revision"] == current["provider_revision"]
    assert applied["current"]["goal_acceptance_contract"] == current["goal_acceptance_contract"]
    document.write_text(json.dumps({"revision": objective["revision"], "digest": objective["digest"],
        "work_digest": current["objective_work_digest"], "review_summary": "My export work includes recovery validation."}))
    code, adopted = global_cli("adopt-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "cli-only-source-adoption", "--expected-provider-revision", current["provider_revision"], "--execute")
    assert code == 0, adopted
    # App is only an independent observer of the CLI's resulting canonical state.
    status, app = request("GET", f"/api/chat/goal-acceptance?goal_id={GOAL}")
    assert status == 200 and app["goal_acceptance_contract"] == source("inspect")[1]["goal_acceptance_contract"]
    assert {p["agent_id"]: p["current"] for p in app["goal_acceptance_contract"]["goal_objective"]["peer_adoption"]} == {"agent-a": True, "agent-b": False}
    assert (project / "state.md").read_bytes() == original
    assert global_cli("inspect", override=shared)[1] == decoy


def test_postgresql_selection_explains_missing_service_without_fallback(source_app):
    project, _, source, global_cli, request, _, shared = source_app
    source_before = source("inspect")[1]
    decoy = global_cli("inspect", override=shared)[1]
    original = (project / "state.md").read_bytes()
    runtime = project.parent / "runtime"
    marker = runtime / "authority" / f"provider-{hashlib.sha256(GOAL.encode()).hexdigest()}.json"
    previous = marker.read_bytes() if marker.exists() else None
    selector = json.dumps({"schema_version": "loopx_local_authority_provider_v0",
        "provider": "postgresql", "goal_id": GOAL, "tenant_id": "fixture-tenant",
        "store_identity": "postgresql:" + "a" * 32}).encode()
    marker.write_bytes(selector)
    document = project / "service-objective.json"
    document.write_text(json.dumps({"objective": "Maintain exports and recovery",
        "reason": "Recorded owner direction", "source_ref": "message:owner"}))
    try:
        for arguments in [("inspect",), ("prepare-objective", "--agent-id", "agent-a",
                "--document", str(document), "--operation-id", "unavailable-service", "--execute")]:
            code, refused = global_cli(*arguments)
            assert code == 1 and refused["error"] == "local_authority_provider_unavailable", refused
            detail = refused.get("error_detail", "")
            assert "PostgreSQL" in detail and "service-owned store factory" in detail, refused
            assert "host" in detail and "File/SQLite" in detail, refused
            assert str(runtime) not in detail and "fixture-tenant" not in detail
        status, app = request("GET", f"/api/chat/goal-acceptance?goal_id={GOAL}")
        assert status == 409 and app["error_code"] == "goal_acceptance_unavailable"
        assert request("GET", f"/api/actions?goal_id={GOAL}")[1]["proposals"] == []
        assert marker.read_bytes() == selector
        assert (project / "state.md").read_bytes() == original
        assert global_cli("inspect", override=shared)[1] == decoy
    finally:
        if previous is None:
            marker.unlink()
        else:
            marker.write_bytes(previous)
    assert source("inspect")[1] == source_before


@pytest.mark.parametrize("failure", ["missing", "unreadable", "removed", "unpromoted", "stopped"])
def test_unavailable_or_stopped_source_never_falls_back_to_shared(source_app, failure):
    project, _, source, global_cli, request, registry, shared = source_app
    before = global_cli("inspect", override=shared)[1]
    document = project / "pending-objective.json"
    document.write_text(json.dumps({"objective": "Maintain reliable export recovery", "reason": "Recorded owner direction includes recovery",
        "source_ref": "message:owner-direction"}))
    code, prepared = global_cli("prepare-objective", "--agent-id", "agent-a", "--document", str(document),
        "--operation-id", "before-source-failure", "--execute")
    assert code == 0, prepared
    revision = source("inspect")[1]["provider_revision"]
    payload = json.loads(registry.read_text())
    if failure == "missing":
        registry.unlink()
    elif failure == "unreadable":
        registry.write_text("{")
    elif failure == "removed":
        payload["goals"] = []
        registry.write_text(json.dumps(payload))
    elif failure == "unpromoted":
        source_runtime = project.parent / "runtime"
        source_runtime.rename(project.parent / "retained-runtime")
    else:
        payload["goals"][0]["activation"] = {"schema_version": "loopx_goal_activation_v1", "state": "stopped"}
        registry.write_text(json.dumps(payload))
    status, _ = request("GET", f"/api/chat/goal-acceptance?goal_id={GOAL}")
    assert status == (200 if failure == "stopped" else 409)
    status, refused = request("POST", "/api/chat/goal-acceptance", {
        "goal_id": GOAL, "action": "refine", "expected_provider_revision": revision, "operation_id": "source-refine",
        "refinement": {"outcome_id": "export", "owner_agent_id": "agent-a", "objective": "Export the result",
                       "requirements": [{"id": "content", "description": "Retain content"}],
                       "source": {"ref": "message:later", "summary": "Owner requested an export"}}})
    assert status == 409 and refused["ok"] is False
    code, refused = global_cli("apply-proposal", "--proposal-id", prepared["proposal"]["proposal_id"], "--execute")
    assert code == 1 and refused["ok"] is False, refused
    assert global_cli("inspect", override=shared)[1] == before


def test_source_check_proposal_and_registered_actor_boundary(source_app):
    project, document, source, global_cli, request, _, shared = source_app
    plan = json.loads(document.read_text())
    plan["criteria"][0]["validation_argv"] = ["python3", "check.py"]
    document.write_text(json.dumps(plan))
    (project / "check.py").write_text(
        "from pathlib import Path\nassert Path('artifact.txt').read_text() == 'accepted'\n"
    )
    before = global_cli("inspect", override=shared)[1]
    code, denied = global_cli("inspect", "--agent-id", "unknown-peer")
    assert code == 1 and "registered Agent" in denied["error"]
    args = ("prepare", "--agent-id", "agent-a", "--document", str(document),
            "--operation-id", "source-prepared-check", "--execute")
    code, prepared = global_cli(*args)
    assert code == 0 and prepared["proposal"]["status"] == "preview_ready", prepared
    assert global_cli(*args)[1]["proposal"]["proposal_id"] == prepared["proposal"]["proposal_id"]
    (project / "artifact.txt").write_text("accepted")
    status, applied = request("POST", f"/api/actions/{prepared['proposal']['proposal_id']}/apply", {})
    assert status == 200 and applied["proposal"]["receipt"]["checks_passed"] is True, applied
    assert source("inspect")[1]["goal_acceptance_contract"]["status"] == "accepted"
    assert global_cli("inspect", override=shared)[1] == before
