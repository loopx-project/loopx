"""Per-goal handoff-mode gate over the soft-claim / hard-lease split brain.

Modes (goal front-matter ``handoff_mode``):

* absent / ``legacy``: today's dual behavior, byte-for-byte. The split-brain
  hole stays open BY DESIGN; the legacy tests below are characterization pins
  that document it, not aspirations.
* ``soft_claim``: markdown claim is the only ownership record; task-lease
  acquire/renew/transfer are typed-rejected, release/inspect stay allowed.
* ``hard_lease``: ownership changes on an existing todo require the actor to
  hold that todo's time-active lease; completion fence is mandatory and the
  legacy self-disarm state becomes a loud typed error. The delegated
  ``todo_lifecycle_authority`` override is the one audited door through the
  gate.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.todos.contract import (
    TODO_TASK_CLASS_USER_ACTION,
)
from loopx.control_plane.todos.handoff_mode import (
    HANDOFF_MODE_HARD_LEASE,
    HANDOFF_MODE_LEGACY,
    HANDOFF_MODE_SOFT_CLAIM,
    HandoffModeError,
    goal_handoff_mode,
    set_goal_handoff_mode,
)
from loopx.control_plane.work_items import task_lease
from loopx.control_plane.work_items.task_lease import (
    TaskLeaseError,
    acquire_task_lease,
    build_lease,
    hold_handoff_lease_holder_gate,
    inspect_task_lease,
    release_task_lease,
    renew_task_lease,
    task_lease_path,
    transfer_task_lease,
    write_lease,
)
from loopx.status import parse_active_state_todos
from loopx.todos import (
    add_goal_todo,
    complete_goal_todo,
    supersede_goal_todo,
    update_goal_todo,
)

GOAL_ID = "handoff-mode-gate"
AGENT_A = "agent-a"
AGENT_B = "agent-b"
ORCHESTRATOR = "agent-orchestrator"
DECISION_SCOPE = "direction:action:approve_handoff"


def _write_workspace(
    tmp_path: Path,
    *,
    handoff_mode: str | None = None,
    lifecycle_authority: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    state = repo / "ACTIVE_GOAL_STATE.md"
    front_matter = [
        "---",
        f"goal_id: {GOAL_ID}",
        "updated_at: 2026-08-01T00:00:00+00:00",
    ]
    if handoff_mode is not None:
        front_matter.append(f"handoff_mode: {handoff_mode}")
    front_matter.append("---")
    state.write_text(
        "\n".join([*front_matter, "", "## Agent Todo", ""]) + "\n",
        encoding="utf-8",
    )
    coordination: dict[str, Any] = {
        "agent_model": "peer_v1",
        "registered_agents": [AGENT_A, AGENT_B, ORCHESTRATOR],
    }
    if lifecycle_authority is not None:
        coordination["todo_lifecycle_authority"] = lifecycle_authority
    registry = tmp_path / "registry.global.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "harness_self_improvement",
                        "status": "active",
                        "repo": str(repo),
                        "state_file": state.name,
                        "adapter": {"kind": "harness_self_improvement"},
                        "coordination": coordination,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return registry, state


def _set_frontmatter_mode(state: Path, mode: str) -> None:
    """Hand-edit the front-matter mode (simulates the out-of-contract path)."""

    lines = state.read_text(encoding="utf-8").splitlines()
    close = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    for index in range(1, close):
        if lines[index].split(":", 1)[0].strip() == "handoff_mode":
            lines[index] = f"handoff_mode: {mode}"
            break
    else:
        lines.insert(close, f"handoff_mode: {mode}")
    state.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _add_todo(registry: Path, *, claimed_by: str | None = None) -> dict[str, Any]:
    return add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver one bounded control-plane change.",
        task_class="advancement_task",
        claimed_by=claimed_by,
    )


def _agent_todo(state: Path, todo_id: str) -> dict[str, Any]:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    return next(
        item for item in todos["agent_todos"]["items"] if item["todo_id"] == todo_id
    )


def _user_todo(state: Path, todo_id: str) -> dict[str, Any]:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    return next(
        item for item in todos["user_todos"]["items"] if item["todo_id"] == todo_id
    )


def _add_exact_user_gate_pair(
    registry: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Continue after the exact owner decision.",
        task_class="advancement_task",
        claimed_by=AGENT_A,
        required_decision_scopes=[DECISION_SCOPE],
    )
    gate = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Approve the exact handoff decision.",
        task_class="user_gate",
        blocks_agent=AGENT_A,
        decision_scope=DECISION_SCOPE,
        unblocks_todo_id=target["todo_id"],
    )
    return target, gate


def _acquire(
    registry: Path,
    tmp_path: Path,
    todo_id: str,
    *,
    owner: str,
    key: str = "turn-lease-1",
    ttl_seconds: int = 600,
) -> dict[str, Any]:
    return acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo_id,
        owner=owner,
        idempotency_key=key,
        ttl_seconds=ttl_seconds,
    )


def _claim(registry: Path, todo_id: str, agent: str, **kwargs: Any) -> dict[str, Any]:
    return update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        claimed_by=agent,
        agent_id=agent,
        claim_only=True,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Mode reader
# ---------------------------------------------------------------------------


def test_goal_handoff_mode_defaults_to_legacy_when_absent() -> None:
    text = "---\ngoal_id: g\n---\n\n## Agent Todo\n"
    assert goal_handoff_mode(text) == HANDOFF_MODE_LEGACY


def test_goal_handoff_mode_reads_explicit_values() -> None:
    for mode in (HANDOFF_MODE_LEGACY, HANDOFF_MODE_SOFT_CLAIM, HANDOFF_MODE_HARD_LEASE):
        text = f"---\ngoal_id: g\nhandoff_mode: {mode}\n---\n"
        assert goal_handoff_mode(text) == mode


def test_goal_handoff_mode_rejects_unknown_value_loudly() -> None:
    with pytest.raises(HandoffModeError) as error:
        goal_handoff_mode("---\ngoal_id: g\nhandoff_mode: banana\n---\n")

    assert error.value.code == "invalid_handoff_mode"


def test_invalid_goal_handoff_mode_runtime_read_fails_closed_without_mutation(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _set_frontmatter_mode(state, "banana")
    before = state.read_bytes()

    with pytest.raises(HandoffModeError) as error:
        _claim(registry, todo["todo_id"], AGENT_A)

    assert error.value.code == "invalid_handoff_mode"
    assert error.value.payload["handoff_mode"] == "banana"
    assert state.read_bytes() == before


def test_goal_handoff_mode_without_frontmatter_is_legacy() -> None:
    assert goal_handoff_mode("## Agent Todo\n") == HANDOFF_MODE_LEGACY




# ---------------------------------------------------------------------------
# (a) Legacy characterization pins: the split-brain hole stays open, loudly.
# ---------------------------------------------------------------------------


def test_legacy_pin_claim_over_foreign_active_lease_silently_succeeds(
    tmp_path: Path,
) -> None:
    """S1 pin: soft claim never reads the lease store, so two owners coexist."""

    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    result = _claim(registry, todo["todo_id"], AGENT_B)

    assert result["ok"] is True
    assert result["handoff_mode"] == HANDOFF_MODE_LEGACY
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_B
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )
    lease = json.loads(lease_file.read_text(encoding="utf-8"))
    assert lease["owner"] == AGENT_A
    assert lease["status"] == "active"


def test_legacy_pin_foreign_claim_makes_lease_renew_fail_typed(
    tmp_path: Path,
) -> None:
    registry, _state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    _claim(registry, todo["todo_id"], AGENT_B)

    with pytest.raises(TaskLeaseError) as error:
        renew_task_lease(
            registry_path=registry,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            owner=AGENT_A,
            idempotency_key="turn-lease-1",
            expected_version=1,
        )

    assert error.value.code == "owner_conflicts_with_claim"


def test_legacy_pin_fence_self_disarms_and_claimant_completes_keyless(
    tmp_path: Path,
) -> None:
    """The completion fence disarms itself in the split-brain state: the
    markdown claimant completes WITHOUT any lease credentials while the
    foreign lease sits on disk status=active."""

    registry, _state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    _claim(registry, todo["todo_id"], AGENT_B)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_B,
        evidence="done without lease credentials",
    )

    assert result["ok"] is True
    assert result["task_lease_fence"] == {
        "schema_version": "task_lease_v0",
        "required": False,
        "active": False,
    }
    assert result["handoff_mode"] == HANDOFF_MODE_LEGACY
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )
    assert json.loads(lease_file.read_text(encoding="utf-8"))["status"] == "active"


def test_legacy_pin_lease_holder_cannot_complete_after_foreign_claim(
    tmp_path: Path,
) -> None:
    registry, _state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    _claim(registry, todo["todo_id"], AGENT_B)

    with pytest.raises(ValueError, match="cannot complete"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_A,
            task_lease_idempotency_key="turn-lease-1",
        )


def test_legacy_lease_verbs_report_handoff_mode(tmp_path: Path) -> None:
    registry, _state = _write_workspace(tmp_path)
    todo = _add_todo(registry)

    acquired = _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    assert acquired["handoff_mode"] == HANDOFF_MODE_LEGACY
    assert "settlement" not in acquired

    inspected = inspect_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )
    assert inspected["handoff_mode"] == HANDOFF_MODE_LEGACY


# ---------------------------------------------------------------------------
# (b) soft_claim: markdown claim only; lease mutations typed-rejected.
# ---------------------------------------------------------------------------


def test_soft_claim_rejects_lease_acquire(tmp_path: Path) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_SOFT_CLAIM)
    todo = _add_todo(registry)

    with pytest.raises(TaskLeaseError) as error:
        _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    assert error.value.code == "handoff_mode_forbids_lease"
    assert error.value.payload["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM


def test_soft_claim_rejects_renew_and_transfer_of_legacy_leftover(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    _set_frontmatter_mode(state, HANDOFF_MODE_SOFT_CLAIM)

    with pytest.raises(TaskLeaseError) as renew_error:
        renew_task_lease(
            registry_path=registry,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            owner=AGENT_A,
            idempotency_key="turn-lease-1",
        )
    assert renew_error.value.code == "handoff_mode_forbids_lease"

    with pytest.raises(TaskLeaseError) as transfer_error:
        transfer_task_lease(
            registry_path=registry,
            runtime_root=tmp_path / "runtime",
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            owner=AGENT_A,
            idempotency_key="turn-lease-1",
            new_owner=AGENT_B,
            new_idempotency_key="turn-lease-2",
        )
    assert transfer_error.value.code == "handoff_mode_forbids_lease"


def test_soft_claim_allows_release_and_inspect_of_legacy_leftover(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    _set_frontmatter_mode(state, HANDOFF_MODE_SOFT_CLAIM)

    inspected = inspect_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )
    assert inspected["ok"] is True
    assert inspected["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM

    released = release_task_lease(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AGENT_A,
        idempotency_key="turn-lease-1",
        expected_version=1,
        registry_path=registry,
    )
    assert released["ok"] is True
    assert released["released"] is True


def test_soft_claim_todo_verbs_behave_like_legacy(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_SOFT_CLAIM)
    todo = _add_todo(registry)

    claim = _claim(registry, todo["todo_id"], AGENT_B)
    assert claim["ok"] is True
    assert claim["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_B

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_B,
        evidence="soft mode keyless completion",
    )
    assert result["ok"] is True
    assert result["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM
    assert result["task_lease_fence"]["required"] is False


@pytest.mark.parametrize(
    ("configured_mode", "expected_mode"),
    [
        (None, HANDOFF_MODE_LEGACY),
        (HANDOFF_MODE_SOFT_CLAIM, HANDOFF_MODE_SOFT_CLAIM),
    ],
)
def test_local_terminal_replay_matches_completion_response_shape_without_lease(
    tmp_path: Path,
    configured_mode: str | None,
    expected_mode: str,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=configured_mode)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    turn_key = "turn-terminal-response-parity"

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        evidence="complete the lease-free handoff-mode case",
        completion_turn_key=turn_key,
        no_followup=True,
    )
    completed_state = state.read_text(encoding="utf-8")
    replayed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        evidence="retry the same local completion turn",
        completion_turn_key=turn_key,
        no_followup=True,
    )

    assert completed["handoff_mode"] == expected_mode
    assert replayed["handoff_mode"] == expected_mode
    assert replayed["completed"] is True
    assert replayed["changed"] is False
    assert replayed["idempotent_replay"] is True
    assert state.read_text(encoding="utf-8") == completed_state


def test_local_terminal_replay_reports_current_mode_not_original_result_proof(
    tmp_path: Path,
) -> None:
    """Replay parity reads current goal mode; it is not an original receipt."""

    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    turn_key = "turn-current-mode-parity"

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        evidence="complete before a legal quiescent mode transition",
        completion_turn_key=turn_key,
        no_followup=True,
    )
    assert completed["handoff_mode"] == HANDOFF_MODE_LEGACY
    changed_mode = set_goal_handoff_mode(
        registry_path=registry,
        goal_id=GOAL_ID,
        mode=HANDOFF_MODE_SOFT_CLAIM,
    )
    assert changed_mode["changed"] is True
    mode_changed_state = state.read_text(encoding="utf-8")

    replayed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        evidence="retry after the goal mode changed",
        completion_turn_key=turn_key,
        no_followup=True,
    )

    assert replayed["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM
    assert replayed["changed"] is False
    assert replayed["idempotent_replay"] is True
    assert state.read_text(encoding="utf-8") == mode_changed_state


def test_local_terminal_replay_fails_closed_on_current_invalid_mode(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    turn_key = "turn-invalid-mode-replay"
    complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        completion_turn_key=turn_key,
        no_followup=True,
    )
    _set_frontmatter_mode(state, "banana")
    invalid_state = state.read_text(encoding="utf-8")

    with pytest.raises(HandoffModeError) as error:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_A,
            completion_turn_key=turn_key,
            no_followup=True,
        )

    assert error.value.code == "invalid_handoff_mode"
    assert state.read_text(encoding="utf-8") == invalid_state


# ---------------------------------------------------------------------------
# (c) hard_lease: ownership mutations require the actor to hold the lease.
# ---------------------------------------------------------------------------


def test_hard_lease_claim_without_lease_is_typed_rejected(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(TaskLeaseError) as error:
        _claim(registry, todo["todo_id"], AGENT_B)

    assert error.value.code == "handoff_mode_requires_lease"
    assert error.value.payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert state.read_text(encoding="utf-8") == before


def test_hard_lease_claim_by_non_owner_is_typed_rejected(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(TaskLeaseError) as error:
        _claim(registry, todo["todo_id"], AGENT_B)

    assert error.value.code == "handoff_mode_requires_lease"
    assert error.value.payload["lease_owner"] == AGENT_A
    assert state.read_text(encoding="utf-8") == before


def test_hard_lease_holder_claim_succeeds(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    result = _claim(registry, todo["todo_id"], AGENT_A)

    assert result["ok"] is True
    assert result["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_A


def test_hard_lease_holder_can_handoff_claim_and_peer_can_finish(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    acquired_by_a = _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )

    handed_off = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AGENT_B,
        agent_id=AGENT_A,
    )

    assert handed_off["ok"] is True
    assert handed_off["mutation_authority"]["actor_agent_id"] == AGENT_A
    assert handed_off["task_lease_holder_gate"]["owner"] == AGENT_A
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_B
    lease_held_by_a = json.loads(lease_file.read_text(encoding="utf-8"))
    assert lease_held_by_a["owner"] == AGENT_A
    assert lease_held_by_a["status"] == "active"

    state_during_handoff = state.read_text(encoding="utf-8")
    with pytest.raises(TaskLeaseError) as claim_error:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_B,
            clear_claim=True,
        )
    assert claim_error.value.code == "handoff_mode_requires_lease"
    assert claim_error.value.payload["actor_agent_id"] == AGENT_B
    assert claim_error.value.payload["lease_owner"] == AGENT_A
    assert state.read_text(encoding="utf-8") == state_during_handoff
    assert json.loads(lease_file.read_text(encoding="utf-8")) == lease_held_by_a

    with pytest.raises(TaskLeaseError) as completion_error:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_B,
            evidence="peer cannot complete before acquiring the hard lease",
        )
    assert completion_error.value.code == "handoff_mode_lease_claim_divergence"
    assert completion_error.value.payload["lease_owner"] == AGENT_A
    assert state.read_text(encoding="utf-8") == state_during_handoff
    assert json.loads(lease_file.read_text(encoding="utf-8")) == lease_held_by_a

    released_by_a = release_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AGENT_A,
        idempotency_key="turn-lease-1",
        expected_version=acquired_by_a["lease"]["version"],
    )
    assert released_by_a["released"] is True
    assert json.loads(lease_file.read_text(encoding="utf-8"))["status"] == "released"

    acquired_by_b = _acquire(
        registry,
        tmp_path,
        todo["todo_id"],
        owner=AGENT_B,
        key="turn-lease-2",
    )
    completed_by_b = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_B,
        task_lease_idempotency_key="turn-lease-2",
        task_lease_expected_version=acquired_by_b["lease"]["version"],
        evidence="peer completed after acquiring the hard lease",
    )

    assert completed_by_b["ok"] is True
    assert completed_by_b["task_lease_fence"]["execution_instance_verified"] is True
    assert completed_by_b["task_lease_fence"]["released"] is True
    assert _agent_todo(state, todo["todo_id"])["status"] == "done"
    assert json.loads(lease_file.read_text(encoding="utf-8"))["status"] == "released"


def test_hard_lease_blocks_todo_add_reassigning_a_leased_todo(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )
    state_before = state.read_text(encoding="utf-8")
    lease_before = json.loads(lease_file.read_text(encoding="utf-8"))

    with pytest.raises(TaskLeaseError) as add_error:
        _add_todo(registry, claimed_by=AGENT_B)

    assert add_error.value.code == "handoff_mode_requires_lease"
    assert add_error.value.payload["actor_agent_id"] == AGENT_B
    assert add_error.value.payload["lease_owner"] == AGENT_A
    assert state.read_text(encoding="utf-8") == state_before
    assert json.loads(lease_file.read_text(encoding="utf-8")) == lease_before


def test_hard_lease_allows_todo_add_creating_a_claimed_todo(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)

    created = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Draft the follow-up migration note.",
        task_class="advancement_task",
        claimed_by=AGENT_A,
    )

    assert created["ok"] is True
    assert created["added"] is True
    assert created["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert "task_lease_holder_gate" not in created
    assert _agent_todo(state, created["todo_id"])["claimed_by"] == AGENT_A


def test_hard_lease_allows_todo_add_repeating_the_existing_claim(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    repeated = _add_todo(registry, claimed_by=AGENT_A)

    assert repeated["ok"] is True
    assert repeated["already_exists"] is True
    assert repeated["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert "task_lease_holder_gate" not in repeated
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_A


def test_hard_lease_lease_holder_may_reassign_through_todo_add(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    handed_off = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Deliver one bounded control-plane change.",
        task_class="advancement_task",
        claimed_by=AGENT_B,
        agent_id=AGENT_A,
    )

    assert handed_off["ok"] is True
    assert handed_off["already_exists"] is True
    assert handed_off["task_lease_holder_gate"]["owner"] == AGENT_A
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_B


def test_legacy_pin_todo_add_reassigns_a_leased_todo(tmp_path: Path) -> None:
    """Characterizes today's behavior: the add path is ungated in legacy mode."""

    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )

    reassigned = _add_todo(registry, claimed_by=AGENT_B)

    assert reassigned["ok"] is True
    assert reassigned["already_exists"] is True
    assert reassigned["handoff_mode"] == HANDOFF_MODE_LEGACY
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_B
    assert json.loads(lease_file.read_text(encoding="utf-8"))["owner"] == AGENT_A


def test_hard_lease_expired_lease_does_not_authorize_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A, ttl_seconds=60)
    real_now = task_lease.now_utc()
    monkeypatch.setattr(task_lease, "now_utc", lambda: real_now + timedelta(hours=2))

    with pytest.raises(TaskLeaseError) as error:
        _claim(registry, todo["todo_id"], AGENT_A)

    assert error.value.code == "handoff_mode_requires_lease"


def test_hard_lease_clear_claim_requires_lease(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)

    with pytest.raises(TaskLeaseError) as error:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_A,
            clear_claim=True,
        )
    assert error.value.code == "handoff_mode_requires_lease"

    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        clear_claim=True,
    )
    assert result["ok"] is True
    assert not _agent_todo(state, todo["todo_id"]).get("claimed_by")


def test_hard_lease_claimed_by_update_requires_lease(tmp_path: Path) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)

    with pytest.raises(TaskLeaseError) as error:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AGENT_A,
            agent_id=AGENT_A,
            note="keep ownership, add note",
        )

    assert error.value.code == "handoff_mode_requires_lease"


def test_hard_lease_non_ownership_update_needs_no_lease(tmp_path: Path) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_B,
        note="metadata-only update",
    )

    assert result["ok"] is True
    assert result["handoff_mode"] == HANDOFF_MODE_HARD_LEASE


def test_hard_lease_creation_time_assignment_stays_allowed(tmp_path: Path) -> None:
    """A fresh todo id can never hold a lease; add --claimed-by is a
    deliberate boundary that stays open in hard_lease mode."""

    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)

    todo = _add_todo(registry, claimed_by=AGENT_A)

    assert todo["ok"] is True
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_A


def test_hard_lease_completion_successor_assignment_stays_allowed(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
        evidence="done with verified lease key",
        next_agent_todo="Follow-up slice for the peer lane.",
        next_claimed_by=AGENT_B,
    )

    assert result["ok"] is True
    successor_id = result["next_todos"][0]["todo_id"]
    assert _agent_todo(state, successor_id)["claimed_by"] == AGENT_B


def test_hard_lease_completion_without_lease_is_typed_rejected(
    tmp_path: Path,
) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)

    with pytest.raises(TaskLeaseError) as error:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_A,
            evidence="no lease exists",
        )

    assert error.value.code == "handoff_mode_requires_lease"
    assert error.value.payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE


def test_hard_lease_completion_with_verified_key_succeeds(tmp_path: Path) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
        evidence="done with verified lease key",
    )

    assert result["ok"] is True
    assert result["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert result["task_lease_fence"]["required"] is True
    assert result["task_lease_fence"]["execution_instance_verified"] is True


def test_hard_lease_non_gate_user_todo_completion_without_lease_is_typed_rejected(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
    action = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Owner follow-up action.",
        task_class=TODO_TASK_CLASS_USER_ACTION,
        bound_agent=AGENT_A,
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(TaskLeaseError) as error:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=action["todo_id"],
            role="user",
            agent_id=AGENT_A,
            evidence="non-gate user todos must still cross the lease fence",
        )

    assert error.value.code == "handoff_mode_requires_lease"
    assert state.read_text(encoding="utf-8") == before


def test_hard_lease_exact_user_gate_completion_uses_fence_and_releases_lease(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
    target, gate = _add_exact_user_gate_pair(registry)
    _acquire(registry, tmp_path, gate["todo_id"], owner=AGENT_A)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        decision_outcome="approve",
        agent_id=AGENT_A,
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
        evidence="lease holder approved the exact decision scope",
    )

    assert result["ok"] is True
    assert result["mutation_authority"]["mode"] == (
        "exact_user_gate_decision_scope_override"
    )
    assert "handoff_gate_overridden" not in result
    assert result["task_lease_fence"] == {
        "schema_version": "task_lease_v0",
        "required": True,
        "active": True,
        "owner": AGENT_A,
        "version": 1,
        "lease_epoch": 1,
        "execution_instance_verified": True,
        "released": True,
    }
    assert json.loads(lease_file.read_text(encoding="utf-8"))["status"] == "released"
    assert _user_todo(state, gate["todo_id"])["status"] == "done"
    target_after = _agent_todo(state, target["todo_id"])
    assert target_after["status"] == "open"
    assert not target_after.get("required_decision_scopes")
    assert result["decision_scope_resolution"]["state"] == "resolved"
    assert (
        result["decision_scope_resolution"]["remaining_required_decision_scopes"] == []
    )


@pytest.mark.parametrize(
    ("lease_owner", "actor_agent_id", "completion_key"),
    [
        (AGENT_A, AGENT_A, "wrong-turn-key"),
        (AGENT_B, AGENT_A, "turn-lease-1"),
    ],
    ids=("wrong-key", "foreign-holder"),
)
def test_hard_lease_exact_user_gate_rejects_invalid_lease_fence_without_state_change(
    tmp_path: Path,
    lease_owner: str,
    actor_agent_id: str,
    completion_key: str,
) -> None:
    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
    _target, gate = _add_exact_user_gate_pair(registry)
    _acquire(registry, tmp_path, gate["todo_id"], owner=lease_owner)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(TaskLeaseError) as error:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=gate["todo_id"],
            role="user",
            decision_outcome="approve",
            agent_id=actor_agent_id,
            task_lease_idempotency_key=completion_key,
            task_lease_expected_version=1,
            evidence="invalid execution instance must not consume the gate",
        )

    assert error.value.code == "lease_cas_mismatch"
    assert state.read_text(encoding="utf-8") == before
    persisted_lease = json.loads(lease_file.read_text(encoding="utf-8"))
    assert persisted_lease["status"] == "active"
    assert persisted_lease["owner"] == lease_owner


def test_hard_lease_user_gate_completion_auto_acquires_key(tmp_path: Path) -> None:
    """A user-gate decision completes without caller-supplied lease credentials.

    The completion fence mints the key under the per-goal lease lock, verifies
    it, and releases it after the committed writeback - the human decision is
    the authority, so the presenter never needs to hold a key across the wait.
    """

    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
    target, gate = _add_exact_user_gate_pair(registry)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        decision_outcome="approve",
        agent_id=AGENT_A,
        evidence="owner decision recorded under an auto-acquired completion key",
    )

    assert result["ok"] is True
    assert result["task_lease_fence"]["auto_acquired"] is True
    assert result["task_lease_fence"]["required"] is True
    assert result["task_lease_fence"]["execution_instance_verified"] is True
    assert result["task_lease_fence"]["released"] is True
    assert json.loads(lease_file.read_text(encoding="utf-8"))["status"] == "released"
    assert _user_todo(state, gate["todo_id"])["status"] == "done"
    target_after = _agent_todo(state, target["todo_id"])
    assert target_after["status"] == "open"
    assert not target_after.get("required_decision_scopes")


def test_hard_lease_user_gate_auto_acquire_does_not_displace_existing_lease(
    tmp_path: Path,
) -> None:
    """An active lease held by another agent is never displaced automatically."""

    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
    )
    _target, gate = _add_exact_user_gate_pair(registry)
    _acquire(registry, tmp_path, gate["todo_id"], owner=AGENT_B, key="turn-lease-b")
    before = state.read_text(encoding="utf-8")

    with pytest.raises(TaskLeaseError) as error:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=gate["todo_id"],
            role="user",
            decision_outcome="approve",
            agent_id=AGENT_A,
            evidence="agent A must not displace B's completion key",
        )

    assert error.value.code == "lease_fence_required"
    assert state.read_text(encoding="utf-8") == before


def test_hard_lease_local_terminal_replay_keeps_response_shape_after_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-turn local replay does not re-enter lease authority or mutate state."""

    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    lease_key = "turn-hard-lease-instance"
    turn_key = "turn-hard-lease-completion"
    _acquire(
        registry,
        tmp_path,
        todo["todo_id"],
        owner=AGENT_A,
        key=lease_key,
    )

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=1,
        completion_turn_key=turn_key,
        evidence="complete under the held hard lease",
        no_followup=True,
    )
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )
    assert completed["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert completed["task_lease_fence"]["released"] is True
    terminal_lease = lease_file.read_text(encoding="utf-8")
    assert json.loads(terminal_lease)["status"] == "released"
    completed_state = state.read_text(encoding="utf-8")

    def unexpected_lease_effect(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("terminal replay must not re-enter the task-lease effect path")

    monkeypatch.setattr(
        "loopx.todos.hold_task_lease_mutation_fence",
        unexpected_lease_effect,
    )
    monkeypatch.setattr(
        "loopx.todos.release_verified_task_lease_fence",
        unexpected_lease_effect,
    )
    replayed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=1,
        completion_turn_key=turn_key,
        evidence="retry the same completion after lease release",
        no_followup=True,
    )

    assert replayed["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert replayed["completed"] is True
    assert replayed["changed"] is False
    assert replayed["idempotent_replay"] is True
    assert state.read_text(encoding="utf-8") == completed_state
    assert lease_file.read_text(encoding="utf-8") == terminal_lease


def test_hard_lease_self_disarm_state_becomes_loud_typed_error(
    tmp_path: Path,
) -> None:
    """Legacy remnants or manual edits can leave claimed_by diverged from a
    time-active lease. In hard_lease mode that must fail loudly instead of
    silently completing keyless."""

    registry, state = _write_workspace(tmp_path)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    _claim(registry, todo["todo_id"], AGENT_B)
    _set_frontmatter_mode(state, HANDOFF_MODE_HARD_LEASE)

    with pytest.raises(TaskLeaseError) as error:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_B,
            evidence="split-brain keyless completion attempt",
        )

    assert error.value.code == "handoff_mode_lease_claim_divergence"
    assert error.value.payload["lease_owner"] == AGENT_A


# ---------------------------------------------------------------------------
# (d) the door: delegated todo_lifecycle_authority passes the hard gate.
# ---------------------------------------------------------------------------


def _orchestrator_grant() -> list[dict[str, Any]]:
    return [
        {
            "agent_id": ORCHESTRATOR,
            "actions": ["reassign", "update", "complete"],
            "requires_reason": True,
        }
    ]


def test_hard_lease_delegated_reassign_passes_gate_with_marker(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
        lifecycle_authority=_orchestrator_grant(),
    )
    todo = _add_todo(registry, claimed_by=AGENT_A)

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AGENT_B,
        agent_id=ORCHESTRATOR,
        authority_reason="peer lane is stalled; reassigning",
    )

    assert result["ok"] is True
    assert result["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert result["handoff_gate_overridden"] is True
    assert result["mutation_authority"]["mode"] == "delegated_orchestration_override"
    assert (
        result["mutation_authority"]["authority_reason"]
        == "peer lane is stalled; reassigning"
    )
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_B


def test_hard_lease_delegated_complete_passes_gate_with_marker(
    tmp_path: Path,
) -> None:
    registry, _state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
        lifecycle_authority=_orchestrator_grant(),
    )
    todo = _add_todo(registry, claimed_by=AGENT_A)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=ORCHESTRATOR,
        authority_reason="closing stalled item for the goal owner",
        evidence="delegated completion",
    )

    assert result["ok"] is True
    assert result["handoff_gate_overridden"] is True
    assert result["task_lease_fence"]["required"] is False


def test_delegated_terminal_replay_parity_excludes_original_gate_effect_marker(
    tmp_path: Path,
) -> None:
    """Current mode is replayable; an original gate override effect is not."""

    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
        lifecycle_authority=_orchestrator_grant(),
    )
    todo = _add_todo(registry, claimed_by=AGENT_A)
    turn_key = "turn-delegated-terminal-replay"
    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=ORCHESTRATOR,
        authority_reason="close the item through delegated authority",
        completion_turn_key=turn_key,
        no_followup=True,
    )
    completed_state = state.read_text(encoding="utf-8")

    replayed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=ORCHESTRATOR,
        authority_reason="retry the delegated completion turn",
        completion_turn_key=turn_key,
        no_followup=True,
    )

    assert completed["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert completed["handoff_gate_overridden"] is True
    assert replayed["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert replayed["mutation_authority"]["mode"] == (
        "delegated_orchestration_override"
    )
    assert "handoff_gate_overridden" not in replayed
    assert replayed["changed"] is False
    assert replayed["idempotent_replay"] is True
    assert state.read_text(encoding="utf-8") == completed_state


def test_hard_lease_gate_has_no_untyped_bypass_without_grant(
    tmp_path: Path,
) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)

    with pytest.raises(ValueError):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AGENT_B,
            agent_id=ORCHESTRATOR,
            authority_reason="no grant exists for this actor",
        )


# ---------------------------------------------------------------------------
# (f) identity normalization on both sides of the holder check.
# ---------------------------------------------------------------------------


def test_hard_lease_holder_check_normalizes_both_sides(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=" Agent-A ",
        agent_id="AGENT-A",
        claim_only=True,
    )

    assert result["ok"] is True
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_A


def test_holder_gate_normalizes_lease_owner_case_variants(tmp_path: Path) -> None:
    registry, _state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
    )
    now = task_lease.now_utc()
    write_lease(
        lease_file,
        build_lease(
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            owner="Agent-A",
            idempotency_key="turn-lease-1",
            write_scopes=[],
            acquire_ttl_seconds=600,
            version=1,
            lease_epoch=1,
            acquired_at=task_lease.isoformat(now),
            updated_at=task_lease.isoformat(now),
            expires_at=task_lease.isoformat(now + timedelta(seconds=600)),
        ),
    )

    with ExitStack() as stack:
        gate = stack.enter_context(
            hold_handoff_lease_holder_gate(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                actor_agent_id=" agent-a ",
            )
        )
        assert gate["active"] is True
        assert gate["owner"] == AGENT_A


# ---------------------------------------------------------------------------
# supersede is a completion-equivalent transition (status -> done) and must
# cross the same lease fence as complete
# ---------------------------------------------------------------------------


def test_hard_lease_supersede_without_lease_is_typed_rejected(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    before = state.read_bytes()

    with pytest.raises(TaskLeaseError) as error:
        supersede_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_A,
            reason="no lease exists",
        )

    assert error.value.code == "handoff_mode_requires_lease"
    assert error.value.payload["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert state.read_bytes() == before


def test_hard_lease_supersede_by_non_holder_without_key_is_typed_rejected(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, todo_id=todo["todo_id"]
    )
    before = state.read_bytes()

    with pytest.raises(TaskLeaseError) as error:
        supersede_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_B,
            reason="peer supersedes without any key",
        )

    assert error.value.code == "lease_fence_required"
    assert state.read_bytes() == before
    assert _agent_todo(state, todo["todo_id"])["done"] is False
    assert lease_file.exists()


def test_hard_lease_supersede_by_holder_without_key_is_typed_rejected(
    tmp_path: Path,
) -> None:
    """The fence is keyed to the execution instance, not to the agent identity."""

    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    with pytest.raises(TaskLeaseError) as error:
        supersede_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_A,
            reason="holder forgot the key",
        )

    assert error.value.code == "lease_fence_required"
    assert _agent_todo(state, todo["todo_id"])["done"] is False


def test_hard_lease_supersede_with_verified_key_succeeds_and_releases_lease(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, todo_id=todo["todo_id"]
    )

    result = supersede_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        reason="superseded with the verified key",
        next_agent_todo="Continue the superseding work.",
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
    )

    assert result["ok"] is True
    assert result["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    assert result["task_lease_fence"]["required"] is True
    assert result["task_lease_fence"]["execution_instance_verified"] is True
    assert result["task_lease_fence"]["released"] is True
    assert _agent_todo(state, todo["todo_id"])["done"] is True
    assert json.loads(lease_file.read_text(encoding="utf-8"))["status"] == "released"
    assert result["next_todos"][0]["added"] is True


def test_hard_lease_supersede_dry_run_keeps_lease_and_state(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    lease_file = task_lease_path(
        runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, todo_id=todo["todo_id"]
    )
    before = state.read_bytes()

    result = supersede_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        reason="preview only",
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["task_lease_fence"]["execution_instance_verified"] is True
    assert result["task_lease_fence"].get("released") is not True
    assert lease_file.exists()
    assert state.read_bytes() == before


def test_hard_lease_delegated_supersede_passes_gate_with_marker(tmp_path: Path) -> None:
    """The delegated door mirrors complete: it opens the claim gate, never an
    effective lease. Without a lease the fence is optional and the marker rides."""

    registry, state = _write_workspace(
        tmp_path,
        handoff_mode=HANDOFF_MODE_HARD_LEASE,
        lifecycle_authority=[
            {
                "agent_id": ORCHESTRATOR,
                "actions": ["supersede"],
                "requires_reason": True,
            }
        ],
    )
    todo = _add_todo(registry, claimed_by=AGENT_A)

    result = supersede_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=ORCHESTRATOR,
        authority_reason="retire stale work",
        reason="delegated supersede",
    )

    assert result["ok"] is True
    assert result["handoff_gate_overridden"] is True
    assert result["task_lease_fence"]["required"] is False
    assert _agent_todo(state, todo["todo_id"])["done"] is True


def test_legacy_pin_supersede_ignores_missing_lease_but_fences_effective_lease(
    tmp_path: Path,
) -> None:
    """Legacy parity with complete since the completion fence landed: no lease
    means no fence; an effective foreign lease requires the key."""

    registry, state = _write_workspace(tmp_path)
    free_todo = _add_todo(registry, claimed_by=AGENT_A)
    free = supersede_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=free_todo["todo_id"],
        agent_id=AGENT_A,
        reason="legacy supersede without any lease",
    )
    assert free["ok"] is True
    assert free["handoff_mode"] == HANDOFF_MODE_LEGACY
    assert free["task_lease_fence"]["required"] is False

    leased_todo = _add_todo(registry)
    _acquire(registry, tmp_path, leased_todo["todo_id"], owner=AGENT_A)
    with pytest.raises(TaskLeaseError) as error:
        supersede_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=leased_todo["todo_id"],
            agent_id=AGENT_B,
            reason="legacy peer supersedes a leased todo",
        )
    assert error.value.code == "lease_fence_required"
    assert _agent_todo(state, leased_todo["todo_id"])["done"] is False


# ---------------------------------------------------------------------------
# force bootstrap rewrites the state file: the declared mode must travel with it
# ---------------------------------------------------------------------------


def _force_bootstrap(
    registry: Path, state: Path, tmp_path: Path, **overrides: Any
) -> dict[str, Any]:
    from loopx.bootstrap import bootstrap_project

    options: dict[str, Any] = dict(
        project=state.parent,
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        objective="Rebuild the active state without changing the handoff contract.",
        domain="harness_self_improvement",
        role="owner",
        parent_goal_id=None,
        state_file=state,
        goal_doc=None,
        adapter_kind="harness_self_improvement",
        adapter_status="active",
        next_probe=None,
        spawn_allowed=False,
        max_children=0,
        allowed_domains=None,
        write_scope=None,
        preserve_todos=False,
        force=True,
        dry_run=False,
        sync_global=False,
    )
    options.update(overrides)
    return bootstrap_project(**options)


def test_force_bootstrap_replace_preserves_declared_handoff_mode(
    tmp_path: Path,
) -> None:
    """Appendix B rule 5: a mode change needs quiescence; a forced state rebuild
    is not a mode change and must not silently fall back to legacy."""

    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_HARD_LEASE)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)

    payload = _force_bootstrap(registry, state, tmp_path)

    assert payload["state_action"] == "replaced"
    assert payload["force_bootstrap_warning"]["handoff_mode"] == HANDOFF_MODE_HARD_LEASE
    rebuilt = state.read_text(encoding="utf-8")
    assert goal_handoff_mode(rebuilt) == HANDOFF_MODE_HARD_LEASE
    assert todo["todo_id"] not in rebuilt


def test_force_bootstrap_replace_keeps_legacy_default_absent(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path)

    payload = _force_bootstrap(registry, state, tmp_path)

    assert payload["state_action"] == "replaced"
    assert payload["force_bootstrap_warning"]["handoff_mode"] == HANDOFF_MODE_LEGACY
    assert "handoff_mode:" not in state.read_text(encoding="utf-8")
    assert goal_handoff_mode(state.read_text(encoding="utf-8")) == HANDOFF_MODE_LEGACY


def test_force_bootstrap_dry_run_reports_preserved_mode_without_writing(
    tmp_path: Path,
) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode=HANDOFF_MODE_SOFT_CLAIM)
    before = state.read_bytes()

    payload = _force_bootstrap(registry, state, tmp_path, dry_run=True)

    assert payload["force_bootstrap_warning"]["handoff_mode"] == HANDOFF_MODE_SOFT_CLAIM
    assert state.read_bytes() == before


def test_force_bootstrap_replace_refuses_invalid_declared_mode(tmp_path: Path) -> None:
    registry, state = _write_workspace(tmp_path, handoff_mode="banana")
    before = state.read_bytes()

    with pytest.raises(HandoffModeError) as error:
        _force_bootstrap(registry, state, tmp_path)

    assert error.value.code == "invalid_handoff_mode"
    assert state.read_bytes() == before
