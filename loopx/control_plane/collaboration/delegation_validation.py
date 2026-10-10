"""Host IO for delegation's single typed acceptance/validation plan.

The canonical reader resolves Goal scope; TypeScript selects validation effects.
Python resolves private declarations and executes only those authorized effects.
No validator output or successful declaration read completes a canonical Todo.
"""

from pathlib import Path

from ...agent_registry import load_goal_from_registry
from ...materials import goal_state_path
from ..agents.workspace_guard import capture_delivery_workspace
from ..effect_runtime import effect_runtime_result
from ..goals.acceptance import (
    inspect_goal_acceptance,
    run_goal_acceptance_validation_effect,
    validation_effect_files_current,
)
from ..todos.completion_validation import resolve_private_completion_validation_declaration


def capture(service, binding: dict) -> dict:
    basis = inspect_goal_acceptance(
        registry_path=service.registry, runtime_root=str(service.root),
        goal_id=service.goal_id, agent_id=binding["agent_id"], todo_id=binding["todo_id"],
    )
    todo = basis.get("todo")
    if not isinstance(todo, dict):
        raise ValueError("delegation canonical Todo unavailable")
    goal = load_goal_from_registry(service.registry, service.goal_id)
    state_file = goal_state_path(goal) if goal is not None else None
    if state_file is None:
        raise ValueError("delegation validation workspace unavailable")
    declaration = resolve_private_completion_validation_declaration(
        canonical_todo=todo, state_file=state_file, runtime_root=service.root,
        registry_path=service.registry, goal_id=service.goal_id,
        todo_id=binding["todo_id"], role=todo.get("role"), persist_if_resolved=False,
    )
    plan = effect_runtime_result("collaboration.delegation.validation_plan", {
        "binding": {key: binding[key] for key in ("id", "agent_id", "todo_id")},
        "basis": basis, "declaration": declaration,
    })
    # The configured worker worktree is the host-owned execution context. The
    # typed plan still owns which validators run; a path-free snapshot lets the
    # ordinary completion runner verify cross-repository Todo validators.
    workspace = Path(binding["workspace"])
    delivery_workspace = (
        capture_delivery_workspace(workspace, peer_independent_worktree_required=True)
        if any(effect.get("task_repository") for effect in plan["effects"])
        else None
    )
    files_current = plan["state"] == "ready" and validation_effect_files_current(
        effects=plan["effects"], registry_path=service.registry, goal_id=service.goal_id,
        delivery_workspace=delivery_workspace, validation_workspace_path=workspace,
    )
    return {"basis": basis, "plan": plan, "files_current": files_current,
            "delivery_workspace": delivery_workspace}


def validate(service, binding: dict) -> dict:
    before = capture(service, binding)
    if before["plan"]["state"] != "ready" or not before["files_current"]:
        raise ValueError("delegation task acceptance rejected")
    results = [run_goal_acceptance_validation_effect(
        effect=effect, registry_path=service.registry, goal_id=service.goal_id,
        delivery_workspace=before["delivery_workspace"],
        validation_workspace_path=Path(binding["workspace"]),
    ) for effect in before["plan"]["effects"]]
    after = capture(service, binding)
    # The TS plan binds this task's work, claim/lifecycle and current rules.
    # A whole-Goal provider revision may advance for unrelated peer progress.
    if (after["plan"] != before["plan"] or not after["files_current"]
            or after["delivery_workspace"] != before["delivery_workspace"]):
        raise ValueError("delegation task acceptance changed during validation")
    if not all(result["passed"] for result in results):
        raise ValueError("delegation task acceptance rejected")
    return after
