"""Guard the guided flow when Goal state outlives its registry entry (#4801).

A reset that removes a Goal from ``.loopx/registry.json`` but leaves its
``ACTIVE_GOAL_STATE.md`` behind is not ordinary absence. Continuing with the
normal bootstrap, agent-registration, Todo, quota, or host-activation
continuation would let a fresh lane write over the orphaned state under the same
human-readable id, and ``diagnose`` would then report a healthy Goal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.bootstrap_command_pack import (
    build_loopx_bootstrap_command_pack,
    build_start_goal_guided_packet,
    inspect_bootstrap_connection,
    render_loopx_bootstrap_command_pack_message,
)
from loopx.control_plane.goals.orphaned_goal_state import (
    GOAL_STATE_ROOTS,
    ORPHANED_GOAL_STATE_CONNECTION,
    orphaned_goal_state_routes,
)
from loopx.control_plane.testing.continuation_verb_guard import assert_no_continuation_verb
from loopx.control_plane.testing.onboarding_model_behavior_qualification import (
    onboarding_entry_contract_violations,
    onboarding_entry_semantic_contract,
)

ORPHANED_GOAL_ID = "reset-goal"
REGISTERED_GOAL_ID = "live-goal"
GOAL_TEXT = "Continue the interrupted refactor."


def _project(
    root: Path,
    *,
    orphaned_state_dirs: tuple[str, ...] = (),
    orphaned_goal_id: str = ORPHANED_GOAL_ID,
    registry: str = "registered",
) -> Path:
    """Build a project carrying an orphaned state file for ``orphaned_goal_id``.

    ``registry`` selects how far the reset got: ``registered`` keeps the other
    Goal's entry, ``empty`` keeps the file but declares no Goal, ``missing``
    removes the project registry entirely, and ``invalid`` leaves a file that no
    longer parses.
    """

    project = root / "project"
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    entries = (
        [
            {
                "id": REGISTERED_GOAL_ID,
                "status": "active",
                "repo": str(project),
                "state_file": f".codex/goals/{REGISTERED_GOAL_ID}/ACTIVE_GOAL_STATE.md",
                "coordination": {
                    "agent_model": "peer_v1",
                    "registered_agents": ["codex-live"],
                },
            }
        ]
        if registry == "registered"
        else []
    )
    if registry == "invalid":
        registry_path.write_text('{"schema_version": "0.1", "goals": [', encoding="utf-8")
    elif registry != "missing":
        registry_path.write_text(
            json.dumps({"schema_version": "0.1", "goals": entries,
                        "common_runtime_root": str(root / "runtime")}, indent=2) + "\n",
            encoding="utf-8",
        )
    registered_state = project / ".codex" / "goals" / REGISTERED_GOAL_ID
    registered_state.mkdir(parents=True)
    (registered_state / "ACTIVE_GOAL_STATE.md").write_text(
        "# Live goal state\n", encoding="utf-8"
    )
    for state_dir in orphaned_state_dirs:
        orphan = project / state_dir / orphaned_goal_id
        orphan.mkdir(parents=True)
        (orphan / "ACTIVE_GOAL_STATE.md").write_text(
            "# Orphaned goal state written by a retired lane\n", encoding="utf-8"
        )
    if registry == "registered":
        from canonical_authority_fixture import initialize_canonical_authority
        from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

        initialize_canonical_authority(root / "runtime", REGISTERED_GOAL_ID,
            build_todo_runtime_shadow_projection(goal_id=REGISTERED_GOAL_ID, todos=[]),
            state_path=registered_state / "ACTIVE_GOAL_STATE.md")
    return project


def _guided(project: Path, goal_id: str = ORPHANED_GOAL_ID) -> dict[str, Any]:
    return build_start_goal_guided_packet(
        project=project,
        goal_id=goal_id,
        agent_id=None,
        cli_bin="loopx",
        host_surface="codex-app",
        goal_text=GOAL_TEXT,
        available_capabilities=["network"],
    )


def _command_pack(project: Path, goal_id: str = ORPHANED_GOAL_ID) -> dict[str, Any]:
    return build_loopx_bootstrap_command_pack(
        project=project,
        goal_id=goal_id,
        agent_id=None,
        cli_bin="loopx",
        host_surface="codex-app",
        goal_text=GOAL_TEXT,
        available_capabilities=["network"],
    )


# ---- the detected fact -------------------------------------------------------


def test_orphan_detection_covers_current_and_legacy_routes(tmp_path: Path) -> None:
    roots = tuple("/".join(root) for root in GOAL_STATE_ROOTS)
    project = _project(tmp_path, orphaned_state_dirs=roots)

    assert orphaned_goal_state_routes(project, ORPHANED_GOAL_ID) == [
        f"{root}/{ORPHANED_GOAL_ID}/ACTIVE_GOAL_STATE.md" for root in roots
    ]


def test_inspection_separates_orphaned_state_from_plain_absence(
    tmp_path: Path,
) -> None:
    orphaned = inspect_bootstrap_connection(
        _project(tmp_path / "orphaned", orphaned_state_dirs=(".codex/goals",)),
        goal_id=ORPHANED_GOAL_ID,
    )
    assert orphaned["connection_state"] == ORPHANED_GOAL_STATE_CONNECTION
    assert orphaned["goal_found"] is False
    assert orphaned["bootstrap_continuation_allowed"] is False
    assert orphaned["orphaned_goal_state"]["state_file_routes"] == [
        f".codex/goals/{ORPHANED_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    ]

    absent = inspect_bootstrap_connection(
        _project(tmp_path / "absent"),
        goal_id=ORPHANED_GOAL_ID,
    )
    assert absent["connection_state"] == "registry_without_goal"
    assert "orphaned_goal_state" not in absent


def test_candidate_matching_is_scoped_to_the_requested_goal(tmp_path: Path) -> None:
    # Only ``live-goal`` has state, and it is registered, so nothing is orphaned.
    project = _project(tmp_path)

    assert orphaned_goal_state_routes(project, ORPHANED_GOAL_ID) == []
    assert (
        inspect_bootstrap_connection(project, goal_id=ORPHANED_GOAL_ID)[
            "connection_state"
        ]
        == "registry_without_goal"
    )
    assert (
        inspect_bootstrap_connection(project, goal_id=REGISTERED_GOAL_ID)[
            "connection_state"
        ]
        == "connected"
    )


# ---- the fence: no activation path over orphaned state -----------------------


def test_guided_packet_offers_only_previews_over_orphaned_state(
    tmp_path: Path,
) -> None:
    payload = _guided(_project(tmp_path, orphaned_state_dirs=(".codex/goals",)))
    transaction = payload["guided_transaction"]

    assert transaction["blocked_by"] == ORPHANED_GOAL_STATE_CONNECTION
    assert transaction["writes_now"] is False
    assert transaction["spends_quota_now"] is False
    assert [step["id"] for step in transaction["ordered_steps"]] == [
        "inspect_connection",
        "resolve_orphaned_goal_state",
    ]
    gate = transaction["orphaned_goal_state_gate"]
    assert gate["schema_version"] == "loopx_orphaned_goal_state_gate_v0"
    assert gate["forbidden_until_resolved"] == [
        "bootstrap",
        "agent_registration",
        "todo_write",
        "quota_spend",
        "host_loop_activation",
    ]
    assert [route["route"] for route in gate["resolution_routes"]] == [
        "inspect_registry_and_state",
        "preview_state_backup",
    ]
    for route in gate["resolution_routes"]:
        assert route["mutates"] is False
        assert "--execute" not in route["command"]
        assert route["command"].splitlines()[-1].startswith("loopx "), route
    contract = payload["safety_contract"]
    assert contract["writes_state_file"] is False
    assert contract["spends_quota"] is False
    assert contract["force_bootstrap_allowed"] is False
    assert contract["mutation_commands_are_previewed"] is True


def test_guided_packet_carries_no_bootstrap_or_todo_authoring_continuation(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, orphaned_state_dirs=(".codex/goals",))
    payload = _guided(project)
    commands = payload["command_pack"]["commands"]

    assert commands["goal_start_connect_if_needed"] is None
    assert commands["bootstrap_after_user_confirmation"] is None
    assert commands["goal_start_plan_prompt"] is None
    assert payload["recommended_next_step"]["kind"] == "resolve_orphaned_goal_state"
    assert payload["recommended_next_step"]["requires_user_confirmation"] is True
    assert "identity_selection_gate" not in payload["guided_transaction"]

    message = payload["message"]
    assert "Orphaned Goal State Gate" in message
    assert "todo add" not in message
    assert not [
        line
        for line in message.splitlines()
        if line.lstrip().startswith("`loopx ") and "--execute" in line
    ], message
    # The orphan routes stay project-relative; a projected absolute path would
    # carry the operator's filesystem into host-facing artifacts. The preview
    # commands still `cd` into the resolved project, as every packet command does.
    assert str(project) not in json.dumps(
        payload["guided_transaction"]["orphaned_goal_state_gate"]["state_file_routes"]
    )


def test_command_pack_fence_matches_the_guided_packet(tmp_path: Path) -> None:
    payload = _command_pack(_project(tmp_path, orphaned_state_dirs=(".claude/goals",)))

    assert payload["orphaned_goal_state"]["state_file_routes"] == [
        f".claude/goals/{ORPHANED_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    ]
    contract = payload["safety_contract"]
    assert contract["orphaned_goal_state_blocks_continuation"] is True
    assert contract["explicit_goal_start_may_write_project_local_state"] is False
    assert contract["host_loop_activation_allowed"] is False
    assert contract["mutation_requires_user_confirmation"] is True


# ---- the fence did not widen: untouched routes keep their old behavior -------


def test_plain_absence_keeps_the_connect_continuation(tmp_path: Path) -> None:
    payload = _guided(_project(tmp_path))
    transaction = payload["guided_transaction"]

    assert transaction.get("blocked_by") != ORPHANED_GOAL_STATE_CONNECTION
    step_ids = [step["id"] for step in transaction["ordered_steps"]]
    assert step_ids[:2] == ["inspect_connection", "connect_if_needed"]
    assert payload["command_pack"]["commands"]["goal_start_connect_if_needed"]
    assert payload["safety_contract"]["orphaned_goal_state_blocks_continuation"] is False


def test_connected_goal_packet_is_unchanged(tmp_path: Path) -> None:
    payload = _guided(
        _project(tmp_path, orphaned_state_dirs=(".codex/goals",)),
        goal_id=REGISTERED_GOAL_ID,
    )

    assert payload["project_connection"]["connection_state"] == "connected"
    assert "orphaned_goal_state_gate" not in payload["guided_transaction"]
    assert [step["id"] for step in payload["guided_transaction"]["ordered_steps"]][0] == (
        "inspect_connection"
    )


# ---- the shipped onboarding qualifier classifies the fence as a stop --------


def test_obeying_agent_has_no_actionable_command_at_the_fence(tmp_path: Path) -> None:
    payload = _guided(_project(tmp_path / "fence", orphaned_state_dirs=(".codex/goals",)))
    contract = onboarding_entry_semantic_contract(payload)

    assert contract["route"] == "stop"
    assert contract["action_command_ids"] == []
    assert contract["writes_now"] is False
    assert contract["spends_quota_now"] is False
    assert onboarding_entry_contract_violations(contract) == []

    unblocked = onboarding_entry_semantic_contract(
        _guided(_project(tmp_path / "clear"))
    )
    assert unblocked["route"] == "select_agent_identity"
    assert unblocked["action_command_ids"]


# ---- no consumption surface may keep a continuation verb ---------------------


def test_every_fenced_consumption_surface_is_read_only(tmp_path: Path) -> None:
    project = _project(tmp_path, orphaned_state_dirs=(".codex/goals",))

    guided = _guided(project)
    command_pack = _command_pack(project)

    assert_no_continuation_verb(guided, source="guided_packet")
    assert_no_continuation_verb(command_pack, source="standalone_command_pack")
    assert_no_continuation_verb(
        render_loopx_bootstrap_command_pack_message(command_pack),
        source="standalone_rendered_message",
    )
    # The guard above must not be able to pass on a packet that has nothing left
    # to tell the operator: the read-only half survives the rebuild.
    assert command_pack["commands"]["status"]
    assert [
        route["route"]
        for route in guided["guided_transaction"]["orphaned_goal_state_gate"]["resolution_routes"]
    ] == ["inspect_registry_and_state", "preview_state_backup"]


# ---- the same invariant holds whatever shape the reset left the registry in --


def _assert_fenced(payload: dict[str, Any]) -> None:
    transaction = payload["guided_transaction"]

    assert transaction["blocked_by"] == ORPHANED_GOAL_STATE_CONNECTION, transaction
    assert [step["id"] for step in transaction["ordered_steps"]] == [
        "inspect_connection",
        "resolve_orphaned_goal_state",
    ], transaction
    commands = payload["command_pack"]["commands"]
    for key in (
        "goal_start_connect_if_needed",
        "goal_start_refresh_state",
        "goal_start_host_loop_activation",
        "goal_start_quota_should_run",
        "goal_start_plan_prompt",
    ):
        assert commands[key] is None, key
    assert onboarding_entry_semantic_contract(payload)["action_command_ids"] == []


@pytest.mark.parametrize(
    ("registry", "absence_connection"),
    [
        ("missing", "not_connected"),
        ("empty", "registry_without_goal"),
        ("invalid", "registry_invalid"),
    ],
)
def test_reset_without_a_registry_still_fences_surviving_state(
    tmp_path: Path, registry: str, absence_connection: str
) -> None:
    project = _project(
        tmp_path / registry,
        orphaned_state_dirs=(".codex/goals",),
        registry=registry,
    )

    connection = inspect_bootstrap_connection(project, goal_id=ORPHANED_GOAL_ID)
    assert connection["connection_state"] == ORPHANED_GOAL_STATE_CONNECTION, connection
    assert connection["orphaned_goal_state"]["state_file_routes"] == [
        f".codex/goals/{ORPHANED_GOAL_ID}/ACTIVE_GOAL_STATE.md"
    ], connection
    # The fence must not erase which absence it was reached through: a registry
    # that cannot be parsed is a different operator action from a deleted entry.
    assert connection["absent_connection_state"] == absence_connection, connection
    assert connection["absent_reason"], connection
    assert connection["reason"] != connection["absent_reason"], connection
    _assert_fenced(_guided(project))


@pytest.mark.parametrize(
    ("registry", "absence_connection"),
    [
        ("missing", "not_connected"),
        ("empty", "registry_without_goal"),
        ("invalid", "registry_invalid"),
    ],
)
def test_reset_without_orphaned_state_is_still_ordinary_onboarding(
    tmp_path: Path, registry: str, absence_connection: str
) -> None:
    project = _project(tmp_path / f"clear-{registry}", registry=registry)

    connection = inspect_bootstrap_connection(project, goal_id=ORPHANED_GOAL_ID)
    assert connection["connection_state"] == absence_connection, connection
    assert connection["registry_exists"] is (registry != "missing"), connection
    assert "orphaned_goal_state" not in connection, connection

    payload = _guided(project)
    transaction = payload["guided_transaction"]
    assert transaction.get("blocked_by") != ORPHANED_GOAL_STATE_CONNECTION, transaction
    assert "orphaned_goal_state_gate" not in transaction, transaction
    assert [step["id"] for step in transaction["ordered_steps"]][1] == (
        "connect_if_needed"
    )
    assert payload["command_pack"]["commands"]["goal_start_connect_if_needed"]
