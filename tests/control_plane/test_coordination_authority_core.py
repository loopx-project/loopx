from __future__ import annotations

from dataclasses import fields, replace

import pytest

from loopx.control_plane.coordination.authority_core import (
    CoordinationSnapshot,
    DecisionOutcome,
    HandoffMode,
    LeaseAction,
    LeaseFence,
    LeaseModeGateCommand,
    LeaseSnapshot,
    LifecycleGrant,
    OwnershipGate,
    TodoAction,
    TodoMutationCommand,
    TodoSnapshot,
    decide,
    ownership_gate_requirement,
)


AGENT_A = "agent-a"
AGENT_B = "agent-b"
ORCHESTRATOR = "orchestrator"
SCOPE = ("direction", "action", "publish-release")


def todo(**overrides: object) -> TodoSnapshot:
    values: dict[str, object] = {
        "todo_id": "todo_target",
        "status": "open",
        "role": "agent",
    }
    values.update(overrides)
    return TodoSnapshot(**values)  # type: ignore[arg-type]


def lease(**overrides: object) -> LeaseSnapshot:
    values: dict[str, object] = {
        "present": True,
        "active": True,
        "status": "active",
        "owner": AGENT_A,
        "idempotency_key": "execution-a",
        "version": 3,
        "lease_epoch": 7,
        "write_scopes": ("src/",),
        "acquire_ttl_seconds": 300,
    }
    values.update(overrides)
    return LeaseSnapshot(**values)  # type: ignore[arg-type]


def snapshot(**overrides: object) -> CoordinationSnapshot:
    values: dict[str, object] = {
        "handoff_mode": HandoffMode.LEGACY,
        "registered_agents": (AGENT_A, AGENT_B, ORCHESTRATOR),
        "todo": todo(),
    }
    values.update(overrides)
    return CoordinationSnapshot(**values)  # type: ignore[arg-type]


def claim(**overrides: object) -> TodoMutationCommand:
    values: dict[str, object] = {
        "action": TodoAction.CLAIM,
        "actor_agent_id": AGENT_A,
        "requested_claimed_by": AGENT_A,
        "ownership_mutation": True,
    }
    values.update(overrides)
    return TodoMutationCommand(**values)  # type: ignore[arg-type]


def terminal(
    action: TodoAction = TodoAction.COMPLETE,
    **overrides: object,
) -> TodoMutationCommand:
    values: dict[str, object] = {
        "action": action,
        "actor_agent_id": AGENT_A,
    }
    values.update(overrides)
    return TodoMutationCommand(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", list(HandoffMode))
@pytest.mark.parametrize("ownership", [False, True])
def test_update_authority_keeps_claim_neutral_edits_separate_from_ownership(
    mode, ownership
):
    state = snapshot(handoff_mode=mode)
    command = TodoMutationCommand(
        action=TodoAction.UPDATE,
        actor_agent_id=AGENT_A,
        requested_claimed_by=AGENT_A if ownership else None,
        ownership_mutation=ownership,
    )
    result = decide(state, command)
    if mode is HandoffMode.HARD_LEASE and ownership:
        assert result.code == "handoff_mode_requires_lease"
        assert result.next_snapshot is None
    else:
        assert result.outcome is DecisionOutcome.APPLY
        assert result.next_snapshot.todo.claimed_by == (AGENT_A if ownership else None)
        assert result.next_snapshot.lease is None
        assert result.authority_mode == "registered_peer_actor"


@pytest.mark.parametrize("clear", [False, True])
def test_delegated_update_requires_the_actual_action_and_never_releases_holder(clear):
    state = snapshot(
        handoff_mode=HandoffMode.HARD_LEASE,
        todo=todo(claimed_by=AGENT_B),
        lease=lease(owner=AGENT_B),
        lifecycle_grants=(LifecycleGrant(AGENT_A, frozenset({"reassign"})),),
    )
    command = TodoMutationCommand(
        action=TodoAction.UPDATE,
        actor_agent_id=AGENT_A,
        authority_action="reassign",
        authority_reason="recover abandoned work",
        ownership_mutation=True,
        requested_claimed_by=AGENT_A,
        clear_claim=clear,
    )
    result = decide(state, command)
    assert result.outcome is DecisionOutcome.APPLY
    assert result.ownership_gate is OwnershipGate.DELEGATED_OVERRIDE
    assert result.next_snapshot.todo.claimed_by == (None if clear else AGENT_A)
    assert result.next_snapshot.lease == state.lease
    rejected = decide(state, replace(command, authority_action="update"))
    assert rejected.code == "delegation_action_not_granted"
    assert rejected.next_snapshot is None


def test_decision_is_deterministic_and_does_not_mutate_input() -> None:
    base = snapshot()
    command = claim()
    first = decide(base, command)
    assert first == decide(base, command)
    assert first.outcome is DecisionOutcome.APPLY
    assert first.next_snapshot is not None
    assert first.next_snapshot.todo.todo_id == base.todo.todo_id
    assert first.next_snapshot.todo.claimed_by == AGENT_A
    assert base.todo.claimed_by is None


@pytest.mark.parametrize(
    ("state", "command", "code"),
    [
        (snapshot(), claim(actor_agent_id=None), "actor_required"),
        (snapshot(), claim(actor_agent_id="unknown"), "actor_not_registered"),
        (
            snapshot(todo=todo(excluded_agents=frozenset({AGENT_A}))),
            claim(),
            "actor_excluded",
        ),
        (
            snapshot(todo=todo(role="user", bound_agent=AGENT_B)),
            terminal(),
            "bound_agent_mismatch",
        ),
        (
            snapshot(todo=todo(claimed_by=AGENT_B)),
            terminal(),
            "claim_owner_mismatch",
        ),
        (
            snapshot(),
            claim(requested_claimed_by=AGENT_B),
            "claim_actor_mismatch",
        ),
    ],
)
def test_todo_authority_rejections_are_typed(
    state: CoordinationSnapshot,
    command: TodoMutationCommand,
    code: str,
) -> None:
    plan = decide(state, command)
    assert plan.outcome is DecisionOutcome.REJECTED
    assert plan.code == code
    assert plan.next_snapshot is None


def test_single_agent_compatibility_and_delegated_authority_are_explicit() -> None:
    single = snapshot(registered_agents=(AGENT_A,))
    single_plan = decide(single, terminal(actor_agent_id=None))
    assert single_plan.outcome is DecisionOutcome.APPLY
    assert single_plan.authority_mode == "single_agent_compatibility"

    claimed = snapshot(
        todo=todo(claimed_by=AGENT_B),
        lifecycle_grants=(
            LifecycleGrant(
                agent_id=ORCHESTRATOR,
                actions=frozenset({"complete"}),
                requires_reason=True,
            ),
        ),
    )
    missing_reason = decide(
        claimed,
        terminal(actor_agent_id=ORCHESTRATOR),
    )
    assert missing_reason.code == "delegation_reason_required"

    delegated = decide(
        claimed,
        terminal(
            actor_agent_id=ORCHESTRATOR,
            authority_reason="Recover the stalled owner.",
        ),
    )
    assert delegated.outcome is DecisionOutcome.APPLY
    assert delegated.authority_mode == "delegated_orchestration_override"


def test_exact_user_gate_override_requires_the_exact_linked_scope() -> None:
    gate = todo(
        todo_id="todo_gate",
        role="user",
        task_class="user_gate",
        decision_scope=SCOPE,
        unblocks_todo_id="todo_target",
    )
    target = todo(required_decision_scopes=frozenset({SCOPE}))
    state = snapshot(todo=gate, decision_target=target)
    command = terminal(actor_agent_id=None, decision_outcome="approve")

    plan = decide(state, command)
    assert plan.outcome is DecisionOutcome.APPLY
    assert plan.authority_mode == "exact_user_gate_decision_scope_override"

    wrong_scope = replace(
        state,
        decision_target=replace(target, required_decision_scopes=frozenset()),
    )
    rejected = decide(wrong_scope, command)
    assert rejected.outcome is DecisionOutcome.REJECTED
    assert rejected.code == "actor_required"


@pytest.mark.parametrize(
    ("mode", "current_lease", "expected", "gate"),
    [
        (HandoffMode.LEGACY, None, DecisionOutcome.APPLY, OwnershipGate.NOT_REQUIRED),
        (
            HandoffMode.SOFT_CLAIM,
            None,
            DecisionOutcome.APPLY,
            OwnershipGate.NOT_REQUIRED,
        ),
        (
            HandoffMode.HARD_LEASE,
            None,
            DecisionOutcome.REJECTED,
            OwnershipGate.REQUIRE_HOLDER,
        ),
        (
            HandoffMode.HARD_LEASE,
            lease(),
            DecisionOutcome.APPLY,
            OwnershipGate.REQUIRE_HOLDER,
        ),
    ],
)
def test_ownership_handoff_preserves_the_three_modes(
    mode: HandoffMode,
    current_lease: LeaseSnapshot | None,
    expected: DecisionOutcome,
    gate: OwnershipGate,
) -> None:
    plan = decide(snapshot(handoff_mode=mode, lease=current_lease), claim())
    assert plan.outcome is expected
    assert plan.ownership_gate is gate


def test_delegated_hard_lease_ownership_change_uses_the_only_override() -> None:
    state = snapshot(
        handoff_mode=HandoffMode.HARD_LEASE,
        todo=todo(claimed_by=AGENT_B),
        lifecycle_grants=(
            LifecycleGrant(
                agent_id=ORCHESTRATOR,
                actions=frozenset({"reassign"}),
            ),
        ),
    )
    plan = decide(
        state,
        TodoMutationCommand(
            action=TodoAction.UPDATE,
            actor_agent_id=ORCHESTRATOR,
            requested_claimed_by=ORCHESTRATOR,
            authority_action="reassign",
            authority_reason="Owner handoff.",
            ownership_mutation=True,
        ),
    )
    assert plan.outcome is DecisionOutcome.APPLY
    assert plan.ownership_gate is OwnershipGate.DELEGATED_OVERRIDE


@pytest.mark.parametrize("action", [TodoAction.COMPLETE, TodoAction.SUPERSEDE])
def test_terminal_verbs_share_one_lease_fence(action: TodoAction) -> None:
    legacy = decide(snapshot(), terminal(action))
    assert legacy.outcome is DecisionOutcome.APPLY
    assert legacy.lease_fence is LeaseFence.NOT_REQUIRED

    hard_missing = decide(
        snapshot(handoff_mode=HandoffMode.HARD_LEASE),
        terminal(action),
    )
    assert hard_missing.outcome is DecisionOutcome.REJECTED
    assert hard_missing.code == "handoff_mode_requires_lease"

    hard_verified = decide(
        snapshot(handoff_mode=HandoffMode.HARD_LEASE, lease=lease()),
        terminal(
            action,
            lease_idempotency_key="execution-a",
            lease_expected_version=3,
        ),
    )
    assert hard_verified.outcome is DecisionOutcome.APPLY
    assert hard_verified.lease_fence is LeaseFence.REQUIRED


def test_exact_user_gate_can_plan_auto_acquire_but_never_displaces_a_live_lease() -> None:
    user_gate = todo(role="user", task_class="user_gate")
    auto = decide(
        snapshot(
            handoff_mode=HandoffMode.HARD_LEASE,
            registered_agents=(AGENT_A,),
            todo=user_gate,
        ),
        terminal(allow_user_gate_auto_acquire=True),
    )
    assert auto.outcome is DecisionOutcome.APPLY
    assert auto.lease_fence is LeaseFence.AUTO_ACQUIRE

    foreign_live = decide(
        snapshot(
            handoff_mode=HandoffMode.HARD_LEASE,
            registered_agents=(AGENT_A, AGENT_B),
            todo=user_gate,
            lease=lease(owner=AGENT_B, idempotency_key="foreign"),
        ),
        terminal(),
    )
    assert foreign_live.outcome is DecisionOutcome.REJECTED
    assert foreign_live.code == "lease_fence_required"


def test_mode_gate_is_explicit_instead_of_a_synthetic_lease_command() -> None:
    state = snapshot(handoff_mode=HandoffMode.SOFT_CLAIM)
    for action in (LeaseAction.ACQUIRE, LeaseAction.RENEW, LeaseAction.TRANSFER):
        plan = decide(state, LeaseModeGateCommand(action=action))
        assert plan.outcome is DecisionOutcome.REJECTED
        assert plan.code == "handoff_mode_forbids_lease"

    release = decide(state, LeaseModeGateCommand(action=LeaseAction.RELEASE))
    assert release.outcome is DecisionOutcome.APPLY


def test_core_has_no_storage_or_receipt_version_domain() -> None:
    names = {
        field.name
        for model in (CoordinationSnapshot, TodoSnapshot, LeaseSnapshot)
        for field in fields(model)
    }
    assert "provider_generation" not in names
    assert "operation_id" not in names
    assert "receipt_index" not in names
    assert "path" not in names
    assert {"version", "lease_epoch"}.issubset(names)


@pytest.mark.parametrize(
    ("handoff_mode", "ownership_mutation", "authority_mode", "expected"),
    [
        (HandoffMode.LEGACY, True, "registered_peer_actor", OwnershipGate.NOT_REQUIRED),
        (HandoffMode.SOFT_CLAIM, True, "registered_peer_actor", OwnershipGate.NOT_REQUIRED),
        (HandoffMode.HARD_LEASE, False, "registered_peer_actor", OwnershipGate.NOT_REQUIRED),
        (
            HandoffMode.HARD_LEASE,
            True,
            "delegated_orchestration_override",
            OwnershipGate.DELEGATED_OVERRIDE,
        ),
        (HandoffMode.HARD_LEASE, True, "registered_peer_actor", OwnershipGate.REQUIRE_HOLDER),
        (HandoffMode.HARD_LEASE, True, "single_agent_compatibility", OwnershipGate.REQUIRE_HOLDER),
        (HandoffMode.HARD_LEASE, True, None, OwnershipGate.REQUIRE_HOLDER),
    ],
)
def test_ownership_gate_requirement_is_the_single_routing_rule(
    handoff_mode: HandoffMode,
    ownership_mutation: bool,
    authority_mode: str | None,
    expected: OwnershipGate,
) -> None:
    """Writers route the hard-lease holder gate through this one core rule."""

    assert (
        ownership_gate_requirement(
            handoff_mode=handoff_mode,
            ownership_mutation=ownership_mutation,
            authority_mode=authority_mode,
        )
        is expected
    )
