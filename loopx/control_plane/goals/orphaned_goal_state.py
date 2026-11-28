"""Guard goal start when Goal state or its shared route outlives its registry entry.

A project ``ACTIVE_GOAL_STATE.md`` for a goal id the project registry no longer
declares is not the ordinary "nothing to connect yet" case: continuing would hand
a fresh lane write authority over state an earlier lane left behind, under the
same human-readable id. The routes below are the project goal-state roots
``loopx.state_backup`` archives, so a reset that moved or kept one of them is
detected rather than silently reconnected.

This module owns the whole fence so ``loopx.bootstrap_command_pack`` keeps only
its wiring: one entry point through which every absence route passes, the
operator-facing gate, the packet fields that must disappear over orphaned state,
and the guided transaction's blocking shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...global_registry import same_source_registry
from ...project_prompt import shell_arg
from ...registry import registry_goals
from ..projects.registry_codec import load_registry

ACTIVE_GOAL_STATE_FILENAME = "ACTIVE_GOAL_STATE.md"

ORPHANED_GOAL_STATE_CONNECTION = "orphaned_goal_state"

ORPHANED_GOAL_STATE_GATE_SCHEMA_VERSION = "loopx_orphaned_goal_state_gate_v0"

# Project-local goal state has been written under each of these roots, so a goal
# absent from the registry can still own durable state in any of them. Ordered
# current route first; every match is reported, never merged or copied.
GOAL_STATE_ROOTS: tuple[tuple[str, ...], ...] = (
    (".loopx", "goals"),
    (".codex", "goals"),
    (".claude", "goals"),
    (".local", "goals"),
)

ORPHANED_GOAL_STATE_REASON = (
    "the project registry has no entry for this goal id, but goal state written for "
    "it still exists; connecting again would create a second authority over that state"
)

FORBIDDEN_UNTIL_RESOLVED = (
    "bootstrap",
    "agent_registration",
    "todo_write",
    "quota_spend",
    "host_loop_activation",
)

# Over orphaned state the packet is rebuilt from what is safe to run rather than
# from verbs to suppress: `status` is the only command-pack entry point left, and
# the resolution gate below is the only other source of runnable commands. A key
# a future builder adds to the pack is then withheld by default instead of having
# to be remembered here.
FENCED_COMMAND_KEYS = ("status",)

# Subtrees whose fields carry a continuation verb -- registration, activation
# input and steps, the heartbeat prompt, and the planner's post-PR routes. They
# are dropped whole; every consumer reads them through an isinstance guard.
# `available_slash_commands` and `onboarding_hint` are dropped for the same
# reason as the rendered text: both spell out the verbs this fence withholds.
FENCED_SUBTREES = (
    "available_slash_commands",
    "host_loop_activation",
    "onboarding_hint",
)

FENCED_CONTRACT_KEYS = ("activation", "domain_route_hints", "execution_invariants")


def orphaned_goal_state_routes(project: Path, goal_id: str) -> list[str]:
    """List existing state files for ``goal_id`` as project-relative routes.

    Routes stay relative because this projection reaches host-facing packets; an
    absolute path would carry the operator's filesystem into those artifacts.
    """

    return [
        path.relative_to(project).as_posix()
        for path in (
            project.joinpath(*root).joinpath(goal_id, ACTIVE_GOAL_STATE_FILENAME)
            for root in GOAL_STATE_ROOTS
        )
        if path.is_file()
    ]


def orphaned_goal_state_projection(project: Path, goal_id: str) -> dict[str, Any] | None:
    """Return the orphaned-state fact for ``goal_id``, or ``None`` when nothing is orphaned."""

    routes = orphaned_goal_state_routes(project, goal_id)
    if not routes:
        return None
    return {
        "schema_version": ORPHANED_GOAL_STATE_GATE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "state_file_routes": routes,
        "reason": ORPHANED_GOAL_STATE_REASON,
    }


def _global_registration_projection(
    shared_registry: Path, source_registry: str, goal_id: str,
) -> dict[str, Any] | None:
    """Project a matching route when the shared registry is readable."""
    try:
        global_payload = load_registry(shared_registry)
    except (OSError, ValueError):
        return None
    source = {"source_registry": source_registry}
    if not any(
        item.get("id") == goal_id and same_source_registry(item, source)
        for item in registry_goals(global_payload)
    ):
        return None
    return {
        "schema_version": ORPHANED_GOAL_STATE_GATE_SCHEMA_VERSION,
        "goal_id": goal_id,
        "state_file_routes": [],
        "reason": (
            "the shared registry still records this Goal for this project. "
            "Restore the original project registry entry before retrying."
        ),
    }


def absent_goal_connection(
    *,
    base_connection: dict[str, Any],
    shared_registry: Path,
    goal_id: str,
    state_file: Path,
    registry_exists: bool,
    absence_connection: str,
    absence_reason: str,
    known_goal_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Project a missing registration, fenced if its state or shared route survives.

    This is the only way the goal-start flow states an absence, so an unparseable
    registry file, a missing or empty one, and a readable registry with no
    matching entry cannot drift apart on the safety question. The fields are the
    ones the packet carried before this fence existed, so an ordinary absence --
    including a fresh project -- keeps its old onboarding continuation.
    """

    connection: dict[str, Any] = {
        **base_connection,
        "registry_exists": registry_exists,
        "goal_id": goal_id,
        "goal_found": False,
        "state_file": str(state_file),
        "state_file_exists": state_file.exists(),
        "mutation_confirmation_required": True,
        "connection_state": absence_connection,
        "reason": absence_reason,
    }
    if known_goal_ids is not None:
        connection["known_goal_ids"] = known_goal_ids
    # An empty project would resolve the state roots against the interpreter's
    # working directory, which is not a project this packet may speak for.
    project = str(base_connection.get("project") or "")
    projection = orphaned_goal_state_projection(Path(project), goal_id) if project else None
    if projection is None:
        projection = _global_registration_projection(shared_registry, str(connection["registry"]), goal_id)
    if projection is None:
        return connection
    return {
        **connection,
        "orphaned_goal_state": projection,
        "connection_state": ORPHANED_GOAL_STATE_CONNECTION,
        # Which absence produced the fence stays attributable: a registry that
        # cannot be parsed is a different operator action from a deleted entry.
        "absent_connection_state": absence_connection,
        "absent_reason": absence_reason,
        "bootstrap_continuation_allowed": False,
        "reason": projection["reason"],
    }


def orphaned_goal_state_gate(
    projection: dict[str, Any],
    *,
    project: str,
    command_prefix: str,
    status_command: str,
) -> dict[str, Any]:
    """Project the next steps orphaned state allows: inspect, then back up."""

    return {
        **projection,
        "resolution_routes": [
            {
                "route": "inspect_registry_and_state",
                "mutates": False,
                "command": status_command,
            },
            {
                "route": "preview_state_backup",
                "mutates": False,
                "command": "\n".join(
                    [
                        f"cd {shell_arg(project)}",
                        f"{command_prefix} backup-state --project . "
                        "--current-project-only",
                    ]
                ),
            },
        ],
        "execution_boundary": (
            "backup-state writes nothing until the operator adds --execute, which is "
            "the auditable archive the resolution depends on"
        ),
        "forbidden_until_resolved": list(FORBIDDEN_UNTIL_RESOLVED),
    }


def fence_command_pack(command_pack: dict[str, Any], *, command_prefix: str) -> None:
    """Leave a command pack over orphaned state with no mutation continuation."""

    projection = command_pack["project_connection"].get("orphaned_goal_state")
    if projection is None:
        return
    gate = orphaned_goal_state_gate(
        projection,
        project=str(command_pack.get("project") or ""),
        command_prefix=command_prefix,
        status_command=str(command_pack["commands"]["status"]),
    )
    commands = command_pack["commands"]
    for key in list(commands):
        if key not in FENCED_COMMAND_KEYS:
            commands[key] = None
    for key in FENCED_SUBTREES:
        command_pack[key] = None
    contract = command_pack.get("goal_start_contract")
    if isinstance(contract, dict):
        for key in FENCED_CONTRACT_KEYS:
            contract.pop(key, None)
    safety = command_pack["safety_contract"]
    safety["orphaned_goal_state_blocks_continuation"] = True
    safety["mutation_requires_user_confirmation"] = True
    safety["explicit_goal_start_may_write_project_local_state"] = False
    safety["explicit_goal_start_must_activate_host_loop"] = False
    safety["host_loop_activation_allowed"] = False
    command_pack["orphaned_goal_state"] = gate
    command_pack["recommended_next_step"] = {
        "kind": "resolve_orphaned_goal_state",
        "requires_user_confirmation": True,
        "summary": gate["reason"],
        "orphaned_goal_state_gate": gate,
    }


def guided_fence(gate: dict[str, Any]) -> dict[str, Any]:
    """The guided-transaction fields that replace every continuation step."""

    return {
        "blocked_by": ORPHANED_GOAL_STATE_CONNECTION,
        "orphaned_goal_state_gate": gate,
        "ordered_steps": [
            {
                "id": "inspect_connection",
                "kind": "read_only",
                "purpose": "resolve the requested project route and confirm the registry and orphaned state disagree",
            },
            {
                "id": "resolve_orphaned_goal_state",
                "kind": "orphaned_goal_state_gate",
                "resolution_routes": gate["resolution_routes"],
                "forbidden_until_resolved": gate["forbidden_until_resolved"],
                "purpose": (
                    "inspect the orphaned state and preview its backup, then stop for "
                    "the operator's explicit resolution; do not bootstrap over it"
                ),
            },
        ],
    }


def _preview_route_lines(gate: dict[str, Any]) -> str:
    return "\n".join(
        f"- `{route.get('route')}` (preview only): "
        f"`{str(route.get('command')).splitlines()[-1]}`"
        for route in gate.get("resolution_routes") or []
        if isinstance(route, dict)
    )


def fenced_standalone_message(command_pack: dict[str, Any]) -> str:
    """Render the standalone command pack for an orphaned Goal.

    The shared renderer's body is connect, plan-write and activation guidance --
    exactly what this fence withholds -- so a fenced pack states its own two
    read-only routes instead of that text with holes punched in it.
    """

    gate = command_pack.get("orphaned_goal_state") or {}
    return (
        "# LoopX Bootstrap Command Pack\n\n"
        f"- project: `{command_pack.get('project')}`\n"
        f"- goal_id: `{command_pack.get('goal_id')}`\n"
        f"- connection_state: `{ORPHANED_GOAL_STATE_CONNECTION}`\n\n"
        f"{gate.get('reason')}\n\n"
        f"- withheld until an operator resolves it: "
        f"{', '.join(str(name) for name in gate.get('forbidden_until_resolved') or [])}\n"
        f"- {gate.get('execution_boundary')}\n\n"
        "## Read-only routes\n\n"
        f"{_preview_route_lines(gate)}\n"
    )


def render_guided_lines(transaction: dict[str, Any]) -> str:
    """Render the orphan gate section of the guided Markdown, or nothing."""

    gate = transaction.get("orphaned_goal_state_gate")
    if not isinstance(gate, dict):
        return ""
    routes = _preview_route_lines(gate)
    state_files = ', '.join(f'`{route}`' for route in gate.get('state_file_routes') or [])
    return (
        "\n## Orphaned Goal State Gate\n\n"
        f"{gate.get('reason')}\n\n"
        + (f"- orphaned state: {state_files}\n" if state_files else "")
        + f"- blocked until resolved: {', '.join(f'`{name}`' for name in gate.get('forbidden_until_resolved') or [])}\n"
        f"- {gate.get('execution_boundary')}\n\n"
        + routes
        + "\n"
    )
