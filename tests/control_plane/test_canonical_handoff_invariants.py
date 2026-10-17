"""Canonical handoff invariants retained from the retired writer proposal.

Both real local providers must enforce one claim/lease owner. Ownership transfer
is atomic; metadata and duplicate create cannot perform the old two-write handoff.
Current transactions use real File/SQLite; no retired producer is restored.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection,
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.todos.handoff_mode import (
    HandoffModeError,
    set_goal_handoff_mode,
)
from loopx.control_plane.work_items import task_lease
from loopx.control_plane.work_items.task_lease import (
    TaskLeaseError,
    acquire_task_lease,
    inspect_task_lease,
    release_task_lease,
    renew_task_lease,
    task_lease_path,
    transfer_task_lease,
)
from loopx.todos import (
    add_goal_todo,
    list_goal_todos,
    complete_goal_todo,
    supersede_goal_todo,
    update_goal_todo,
)
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
)

GOAL_ID = "handoff-mode-gate"
AGENT_A, AGENT_B, ORCHESTRATOR = "agent-a", "agent-b", "agent-orchestrator"
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
    registry = state.parent.parent / "registry.global.json"
    return list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=todo_id)[
        "todo"
    ]


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












@pytest.fixture(params=["file", "sqlite"])
def canonical_workspace(request, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)

    def create(root, *, handoff_mode="soft_claim", retained=None, **kwargs):
        registry, state = _write_workspace(root, handoff_mode=handoff_mode, **kwargs)
        todos, leases = [], []
        if retained:
            # Synthetic imported facts, committed once before any current call.
            # This fixture is consumer-policy evidence, not an import journey.
            todos = [
                {
                    "schema_version": "todo_item_v0",
                    "todo_id": "todo_retained",
                    "role": "agent",
                    "status": "open",
                    "done": False,
                    "text": "Retained work",
                    "archive_state": "active",
                    "source_section": "Agent Todo",
                    "index": 1,
                    "task_class": "advancement_task",
                    **retained.get("todo", {}),
                }
            ]
            now = task_lease.now_utc()
            start = now - timedelta(hours=2) if retained.get("expired") else now
            leases = [
                {
                    "schema_version": "task_lease_v0",
                    "status": "active",
                    "goal_id": GOAL_ID,
                    "todo_id": "todo_retained",
                    "owner": AGENT_A,
                    "idempotency_key": "turn-lease-1",
                    "write_scopes": [],
                    "acquire_ttl_seconds": 600,
                    "version": 1,
                    "lease_epoch": 1,
                    "acquired_at": task_lease.isoformat(start),
                    "updated_at": task_lease.isoformat(start),
                    "expires_at": task_lease.isoformat(start + timedelta(seconds=600)),
                }
            ]
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=todos, leases=leases, handoff_mode=handoff_mode
        )
        initialize_canonical_authority(
            root / "runtime",
            GOAL_ID,
            projection,
            state_path=state,
            provider=request.param,
        )
        assert _snapshot(registry)["source_authority"] == request.param + "_v0"
        return registry, state

    return create


def _snapshot(registry):
    result = read_canonical_todos_if_promoted(
        runtime_root=registry.parent / "runtime", goal_id=GOAL_ID, include_leases=True
    )
    assert result["decision_read_from_provider"] is True
    assert result["legacy_fallback_used"] is False
    return result


def _lease(registry, todo_id):
    result = inspect_task_lease(
        registry_path=registry,
        runtime_root=registry.parent / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo_id,
    )
    assert result["legacy_fallback_used"] is False
    return result["lease"]


def _terminal(registry, todo_id, operation="complete", **kwargs):
    options = {
        "registry_path": registry,
        "goal_id": GOAL_ID,
        "todo_id": todo_id,
        "agent_id": AGENT_A,
        **kwargs,
    }
    if operation == "complete":
        return complete_goal_todo(**options, no_followup=True)
    return supersede_goal_todo(**options, reason="Retire obsolete work")


def _authority_unchanged(registry, before):
    after = _snapshot(registry)
    for field in ("provider_revision", "todos", "leases", "handoff_mode"):
        assert after[field] == before[field], field


def _unchanged(registry, state, before, display):
    _authority_unchanged(registry, before)
    assert state.read_bytes() == display


@pytest.mark.parametrize("operation", ["claim", "replay"])
def test_display_mode_cannot_override_canonical_policy(
    tmp_path, canonical_workspace, operation
):
    registry, state = canonical_workspace(tmp_path)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    if operation == "replay":
        original = _terminal(
            registry, todo["todo_id"], completion_turn_key="display-replay"
        )
    _set_frontmatter_mode(state, "banana")
    if operation == "claim":
        assert _claim(registry, todo["todo_id"], AGENT_A)["ok"]
    else:
        before, display = _snapshot(registry), state.read_bytes()
        replay = _terminal(
            registry, todo["todo_id"], completion_turn_key="display-replay"
        )
        assert replay["idempotent_replay"] and not replay["changed"]
        assert replay["original_receipt"] == original["original_receipt"]
        _unchanged(registry, state, before, display)
    assert _snapshot(registry)["handoff_mode"] == "soft_claim"


@pytest.mark.parametrize("verb", ["acquire", "renew", "transfer"])
def test_soft_claim_forbids_new_lease_effects_even_with_retained_lineage(
    tmp_path, canonical_workspace, verb
):
    registry, state = canonical_workspace(
        tmp_path, retained={"todo": {"claimed_by": AGENT_A}}
    )
    before, display = _snapshot(registry), state.read_bytes()
    with pytest.raises(TaskLeaseError) as error:
        if verb == "acquire":
            _acquire(registry, tmp_path, "todo_retained", owner=AGENT_A)
        elif verb == "renew":
            renew_task_lease(
                registry_path=registry,
                runtime_root=tmp_path / "runtime",
                goal_id=GOAL_ID,
                todo_id="todo_retained",
                owner=AGENT_A,
                idempotency_key="turn-lease-1",
                expected_version=1,
            )
        else:
            transfer_task_lease(
                registry_path=registry,
                runtime_root=tmp_path / "runtime",
                goal_id=GOAL_ID,
                todo_id="todo_retained",
                owner=AGENT_A,
                idempotency_key="turn-lease-1",
                expected_version=1,
                new_owner=AGENT_B,
                new_idempotency_key="peer",
                transfer_claim=True,
            )
    assert error.value.code == (
        "claim_transfer_requires_hard_lease"
        if verb == "transfer"
        else "handoff_mode_forbids_lease"
    )
    _unchanged(registry, state, before, display)


def test_soft_claim_retained_lease_can_be_inspected_and_released(
    tmp_path, canonical_workspace
):
    registry, _ = canonical_workspace(
        tmp_path, retained={"todo": {"claimed_by": AGENT_A}}
    )
    assert _lease(registry, "todo_retained")["status"] == "active"
    result = release_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id="todo_retained",
        owner=AGENT_A,
        idempotency_key="turn-lease-1",
        expected_version=1,
    )
    assert result["released"]
    assert _lease(registry, "todo_retained")["status"] == "released"


@pytest.mark.parametrize("operation", ["complete", "supersede"])
def test_soft_claim_claim_and_terminal_need_no_execution_lease(
    tmp_path, canonical_workspace, operation
):
    registry, state = canonical_workspace(tmp_path)
    todo = _add_todo(registry)
    assert _claim(registry, todo["todo_id"], AGENT_A)["ok"]
    result = _terminal(registry, todo["todo_id"], operation)
    assert result["ok"] and result["task_lease_fence"]["lease_fence"] == "not_required"
    assert _agent_todo(state, todo["todo_id"])["status"] == "done"
    assert _snapshot(registry)["leases"] == []


@pytest.mark.parametrize("mode", ["soft_claim", "hard_lease"])
def test_terminal_replay_preserves_receipt_and_does_not_rewind_policy(
    tmp_path, canonical_workspace, mode
):
    registry, state = canonical_workspace(tmp_path, handoff_mode=mode)
    todo = _add_todo(registry, claimed_by=AGENT_A)
    proof = {}
    if mode == "hard_lease":
        _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
        proof = dict(
            task_lease_idempotency_key="turn-lease-1", task_lease_expected_version=1
        )
    first = _terminal(
        registry, todo["todo_id"], completion_turn_key="terminal-replay", **proof
    )
    target = "hard_lease" if mode == "soft_claim" else "soft_claim"
    assert set_goal_handoff_mode(registry_path=registry, goal_id=GOAL_ID, mode=target)[
        "changed"
    ]
    before = _snapshot(registry)
    replay = _terminal(
        registry, todo["todo_id"], completion_turn_key="terminal-replay", **proof
    )
    assert replay["idempotent_replay"] and not replay["changed"]
    assert replay["original_receipt"] == first["original_receipt"]
    assert _snapshot(registry)["handoff_mode"] == target
    # Replaying may deliver a pending projection at the current revision;
    # the business receipt and canonical state must remain unchanged.
    _authority_unchanged(registry, before)
    assert todo["todo_id"] in state.read_text()


@pytest.mark.parametrize("lineage", ["missing", "foreign", "expired"])
def test_hard_lease_claim_rejects_missing_foreign_or_expired_holder(
    tmp_path, canonical_workspace, lineage
):
    registry, state = canonical_workspace(
        tmp_path,
        handoff_mode="hard_lease",
        retained={"expired": True} if lineage == "expired" else None,
    )
    todo_id = (
        "todo_retained" if lineage == "expired" else _add_todo(registry)["todo_id"]
    )
    if lineage == "foreign":
        _acquire(registry, tmp_path, todo_id, owner=AGENT_B)
    before, display = _snapshot(registry), state.read_bytes()
    with pytest.raises(LocalCoordinationAuthorityRejection) as error:
        _claim(registry, todo_id, AGENT_A)
    assert error.value.code == "handoff_mode_requires_lease"
    _unchanged(registry, state, before, display)


@pytest.mark.parametrize(
    "owner,claimant", [(AGENT_A, AGENT_A), (" Agent-A ", "AGENT-A")]
)
def test_hard_lease_holder_claim_normalizes_identities(
    tmp_path, canonical_workspace, owner, claimant
):
    registry, state = canonical_workspace(tmp_path, handoff_mode="hard_lease")
    todo = _add_todo(registry)
    _acquire(registry, tmp_path, todo["todo_id"], owner=owner)
    assert _claim(registry, todo["todo_id"], claimant)["ok"]
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AGENT_A
    assert _lease(registry, todo["todo_id"])["owner"] == AGENT_A


@pytest.mark.parametrize(
    "ownership",
    [{"clear_claim": True}, {"claimed_by": AGENT_A}, {"claimed_by": AGENT_B}],
)
@pytest.mark.parametrize("leased", [False, True])
def test_metadata_cannot_rewrite_hard_lease_execution_grants(
    tmp_path, canonical_workspace, ownership, leased
):
    grant = [
        {
            "agent_id": ORCHESTRATOR,
            "actions": ["reassign", "update"],
            "requires_reason": True,
        }
    ]
    registry, state = canonical_workspace(
        tmp_path, handoff_mode="hard_lease", lifecycle_authority=grant
    )
    todo = _add_todo(registry, claimed_by=AGENT_A)
    if leased:
        _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    before, display = _snapshot(registry), state.read_bytes()
    # Even explicit orchestration authority cannot split claim and lease owners.
    with pytest.raises(LocalCoordinationAuthorityRejection) as error:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=ORCHESTRATOR,
            authority_reason="Hand over work",
            **ownership,
        )
    assert error.value.code == "update_lease_ownership_transition_unsupported"
    _unchanged(registry, state, before, display)


def test_administrative_copy_preserves_explicit_execution_fence(tmp_path, canonical_workspace):
    registry, state = canonical_workspace(tmp_path, handoff_mode="hard_lease")
    todo = _add_todo(registry, claimed_by=AGENT_A)
    original = _snapshot(registry)
    updated = update_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
        agent_id=AGENT_A, note="Reviewed context",
    )
    assert updated["ok"]
    assert _snapshot(registry)["leases"] == original["leases"]
    before, display = _snapshot(registry), state.read_bytes()
    # Copy edits grant no execution. Supplying proof retains the strict fence.
    with pytest.raises(LocalCoordinationAuthorityRejection):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AGENT_A,
            note="Unqualified execution",
            task_lease_idempotency_key="not-acquired",
            task_lease_expected_version=1,
        )
    _unchanged(registry, state, before, display)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    updated = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        note="Validated progress",
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
    )
    assert updated["ok"]
    assert "Validated progress" in _agent_todo(state, todo["todo_id"])["note"]


def test_create_assigns_new_work_but_duplicate_create_cannot_reassign(
    tmp_path, canonical_workspace
):
    registry, state = canonical_workspace(tmp_path, handoff_mode="hard_lease")
    created = _add_todo(registry, claimed_by=AGENT_A)
    assert created["added"]
    _acquire(registry, tmp_path, created["todo_id"], owner=AGENT_A)
    before, display = _snapshot(registry), state.read_bytes()
    assert _add_todo(registry, claimed_by=AGENT_A)["already_exists"]
    _authority_unchanged(registry, before)
    display = state.read_bytes()
    with pytest.raises(
        LocalCoordinationAuthorityUnavailable, match="different claimed_by"
    ):
        _add_todo(registry, claimed_by=AGENT_B)
    _unchanged(registry, state, before, display)


@pytest.mark.parametrize("operation", ["complete", "supersede"])
@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing", "handoff_mode_requires_lease"),
        ("keyless", "lease_fence_required"),
        ("wrong_key", "lease_cas_mismatch"),
        ("foreign", "lease_cas_mismatch"),
        ("diverged", "handoff_mode_lease_claim_divergence"),
    ],
)
def test_terminal_fence_rejects_invalid_execution_without_mutation(
    tmp_path, canonical_workspace, operation, fault, code
):
    registry, state = canonical_workspace(
        tmp_path,
        handoff_mode="hard_lease",
        retained={"todo": {"claimed_by": AGENT_B}} if fault == "diverged" else None,
    )
    todo_id = (
        "todo_retained"
        if fault == "diverged"
        else _add_todo(registry, claimed_by=None if fault == "foreign" else AGENT_A)[
            "todo_id"
        ]
    )
    if fault not in ("missing", "diverged"):
        _acquire(registry, tmp_path, todo_id, owner=AGENT_A)
    proof = {}
    if fault in ("wrong_key", "foreign"):
        proof = dict(
            task_lease_idempotency_key="wrong"
            if fault == "wrong_key"
            else "turn-lease-1",
            task_lease_expected_version=1,
        )
    before, display = _snapshot(registry), state.read_bytes()
    actor = AGENT_B if fault in ("diverged", "foreign") else AGENT_A
    with pytest.raises(LocalCoordinationAuthorityRejection) as error:
        _terminal(registry, todo_id, operation, agent_id=actor, **proof)
    assert error.value.code == code
    _unchanged(registry, state, before, display)


@pytest.mark.parametrize("operation", ["complete", "supersede"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_terminal_verified_execution_releases_only_on_commit(
    tmp_path, canonical_workspace, operation, dry_run
):
    registry, state = canonical_workspace(tmp_path, handoff_mode="hard_lease")
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    before, display = _snapshot(registry), state.read_bytes()
    result = _terminal(
        registry,
        todo["todo_id"],
        operation,
        dry_run=dry_run,
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
    )
    assert result["ok"] and result["task_lease_fence"]["lease_fence"] == "required"
    if dry_run:
        _unchanged(registry, state, before, display)
    else:
        assert _agent_todo(state, todo["todo_id"])["status"] == "done"
        assert _lease(registry, todo["todo_id"])["status"] == "released"
        assert not task_lease_path(
            runtime_root=tmp_path / "runtime", goal_id=GOAL_ID, todo_id=todo["todo_id"]
        ).exists()


def test_completion_can_assign_successor_without_inventing_its_lease(
    tmp_path, canonical_workspace
):
    registry, state = canonical_workspace(tmp_path, handoff_mode="hard_lease")
    todo = _add_todo(registry, claimed_by=AGENT_A)
    _acquire(registry, tmp_path, todo["todo_id"], owner=AGENT_A)
    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AGENT_A,
        task_lease_idempotency_key="turn-lease-1",
        task_lease_expected_version=1,
        next_agent_todo="Follow-up slice",
        next_claimed_by=AGENT_B,
    )
    successor = result["next_todos"][0]["todo_id"]
    assert _agent_todo(state, successor)["claimed_by"] == AGENT_B
    assert all(lease["todo_id"] != successor for lease in _snapshot(registry)["leases"])


@pytest.mark.parametrize("operation", ["complete", "supersede"])
@pytest.mark.parametrize("authorized", [False, True])
def test_delegated_terminal_authority_requires_real_grant(
    tmp_path, canonical_workspace, operation, authorized
):
    grants = (
        [{"agent_id": ORCHESTRATOR, "actions": [operation], "requires_reason": True}]
        if authorized
        else []
    )
    registry, state = canonical_workspace(
        tmp_path, handoff_mode="hard_lease", lifecycle_authority=grants
    )
    todo = _add_todo(registry, claimed_by=AGENT_A)
    before, display = _snapshot(registry), state.read_bytes()
    if not authorized:
        with pytest.raises(LocalCoordinationAuthorityRejection):
            _terminal(
                registry,
                todo["todo_id"],
                operation,
                agent_id=ORCHESTRATOR,
                authority_reason="Retire stalled item",
            )
        _unchanged(registry, state, before, display)
    else:
        result = _terminal(
            registry,
            todo["todo_id"],
            operation,
            agent_id=ORCHESTRATOR,
            authority_reason="Retire stalled item",
        )
        assert (
            result["mutation_authority"]["authority_mode"]
            == "delegated_orchestration_override"
        )
        assert result["task_lease_fence"]["lease_fence"] == "delegated_override"
        assert _agent_todo(state, todo["todo_id"])["status"] == "done"


@pytest.mark.parametrize(
    "proof", ["auto", "verified", "wrong_key", "foreign", "foreign_keyless"]
)
def test_exact_user_gate_completion_preserves_execution_fence(
    tmp_path, canonical_workspace, proof
):
    registry, state = canonical_workspace(tmp_path, handoff_mode="hard_lease")
    target, gate = _add_exact_user_gate_pair(registry)
    options = dict(role="user", decision_outcome="approve")
    if proof != "auto":
        _acquire(
            registry,
            tmp_path,
            gate["todo_id"],
            owner=AGENT_B if proof.startswith("foreign") else AGENT_A,
        )
        if proof != "foreign_keyless":
            options.update(
                task_lease_idempotency_key="wrong"
                if proof == "wrong_key"
                else "turn-lease-1",
                task_lease_expected_version=1,
            )
    before, display = _snapshot(registry), state.read_bytes()
    if proof in ("wrong_key", "foreign", "foreign_keyless"):
        with pytest.raises(LocalCoordinationAuthorityRejection) as error:
            _terminal(registry, gate["todo_id"], **options)
        assert error.value.code == (
            "lease_fence_required"
            if proof == "foreign_keyless"
            else "lease_cas_mismatch"
        )
        _unchanged(registry, state, before, display)
    else:
        result = _terminal(registry, gate["todo_id"], **options)
        assert (
            result["mutation_authority"]["authority_mode"]
            == "exact_user_gate_decision_scope_override"
        )
        assert result["task_lease_fence"]["lease_fence"] == (
            "auto_acquire" if proof == "auto" else "required"
        )
        assert _lease(registry, gate["todo_id"])["status"] == "released"
        assert _agent_todo(state, gate["todo_id"])["status"] == "done"
        assert not _agent_todo(state, target["todo_id"]).get("required_decision_scopes")
        assert result["decision_scope_resolution"]["state"] == "resolved"


def test_bound_user_action_completes_without_gate_or_execution_claim(
    tmp_path, canonical_workspace
):
    registry, state = canonical_workspace(tmp_path, handoff_mode="hard_lease")
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Owner follow-up",
        task_class="user_action",
        bound_agent=AGENT_A,
    )
    result = _terminal(registry, todo["todo_id"], role="user")
    assert result["task_lease_fence"]["lease_fence"] == "not_required"
    assert _snapshot(registry)["leases"] == []
    assert _agent_todo(state, todo["todo_id"])["status"] == "done"


@pytest.mark.parametrize("mode", ["soft_claim", "hard_lease"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_force_bootstrap_cannot_erase_canonical_policy_or_work(
    tmp_path, canonical_workspace, mode, dry_run
):
    registry, state = canonical_workspace(tmp_path, handoff_mode=mode)
    _add_todo(registry, claimed_by=AGENT_A)
    before, display = _snapshot(registry), state.read_bytes()
    with pytest.raises(
        ValueError, match="Canonical authority cannot rebuild existing state"
    ):
        _force_bootstrap(registry, state, tmp_path, dry_run=dry_run)
    _unchanged(registry, state, before, display)




def test_delegated_terminal_replay_retains_original_authority_receipt(
    tmp_path, canonical_workspace
):
    registry, state = canonical_workspace(
        tmp_path,
        handoff_mode="hard_lease",
        lifecycle_authority=[
            {"agent_id": ORCHESTRATOR, "actions": ["complete"], "requires_reason": True}
        ],
    )
    todo = _add_todo(registry, claimed_by=AGENT_A)
    intent = dict(
        agent_id=ORCHESTRATOR,
        authority_reason="Close stalled work",
        completion_turn_key="delegated-close",
    )
    result = _terminal(registry, todo["todo_id"], **intent)
    before, display = _snapshot(registry), state.read_bytes()
    replay = _terminal(registry, todo["todo_id"], **intent)
    assert replay["idempotent_replay"] and not replay["changed"]
    assert replay["original_receipt"] == result["original_receipt"]
    assert (
        replay["mutation_authority"]["authority_mode"]
        == "delegated_orchestration_override"
    )
    _unchanged(registry, state, before, display)


def test_invalid_requested_policy_leaves_canonical_authority_unchanged(
    tmp_path, canonical_workspace
):
    registry, state = canonical_workspace(tmp_path)
    _add_todo(registry, claimed_by=AGENT_A)
    before, display = _snapshot(registry), state.read_bytes()
    with pytest.raises(HandoffModeError) as error:
        set_goal_handoff_mode(registry_path=registry, goal_id=GOAL_ID, mode="banana")
    assert error.value.code == "invalid_handoff_mode"
    _unchanged(registry, state, before, display)
