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
