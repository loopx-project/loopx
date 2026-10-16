from __future__ import annotations

import json
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityRejection, LocalCoordinationAuthorityUnavailable,
)
from loopx.control_plane.scheduler.monitor_poll_writeback import (
    write_monitor_poll_todo_state,
)
from loopx.status import parse_active_state_todos
import loopx.control_plane.todos.provider_terminal_lifecycle as terminal_provider
from loopx.control_plane.work_items.task_lease import (
    acquire_task_lease, inspect_task_lease,
)
from loopx.todos import list_goal_todos, archive_completed_todos
from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.todos import (
    add_goal_todo,
    complete_goal_todo,
    update_goal_todo,
)

GOAL_ID = "todo-mutation-authority"
AUTHOR_AGENT = "codex-author"
REVIEW_AGENT = "codex-review"
ORCHESTRATION_AGENT = "codex-orchestrator"
DECISION_SCOPE = "direction:action:publish_release"


def _write_fixture(
    tmp_path: Path,
    *,
    multi_agent: bool = True,
    lifecycle_authority: list[dict] | None = None,
    handoff_mode: str = "soft_claim",
    provider: str = "file",
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-07-18T00:00:00+00:00",
                "---",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    agents = (
        [AUTHOR_AGENT, REVIEW_AGENT, ORCHESTRATION_AGENT]
        if multi_agent
        else [AUTHOR_AGENT]
    )
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": agents,
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
    initialize_canonical_authority(
        tmp_path / "runtime",
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID,
            todos=[],
            handoff_mode=handoff_mode,
        ),
        state_path=state,
        provider=provider,
    )
    return registry, state


def _agent_todo(state: Path, todo_id: str) -> dict:
    todos = parse_active_state_todos(state.read_text(encoding="utf-8"))
    return next(
        item for item in todos["agent_todos"]["items"] if item["todo_id"] == todo_id
    )


def _add_agent_todo(
    registry: Path,
    *,
    text: str = "Deliver one bounded control-plane change.",
    claimed_by: str | None = AUTHOR_AGENT,
    excluded_agents: list[str] | None = None,
) -> dict:
    return add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text=text,
        task_class="advancement_task",
        claimed_by=claimed_by,
        excluded_agents=excluded_agents,
    )


def test_continuous_monitor_requires_explicit_boundedness(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    common = {
        "registry_path": registry,
        "goal_id": GOAL_ID,
        "role": "agent",
        "text": "Watch one public dependency.",
        "task_class": "continuous_monitor",
        "action_kind": "monitor",
        "claimed_by": AUTHOR_AGENT,
        "monitor_metadata": {
            "target_key": "public-dependency",
            "cadence": "30m",
            "next_due_at": "2026-08-12T00:00:00Z",
        },
    }

    with pytest.raises(ValueError, match="requires one of: --expires-at"):
        add_goal_todo(**common)

    result = add_goal_todo(
        **{
            **common,
            "monitor_metadata": {
                **common["monitor_metadata"],
                "watch_only": "true",
            },
        }
    )
    item = _agent_todo(state, str(result["todo_id"]))
    assert item["watch_only"] == "true"


def test_update_to_continuous_monitor_requires_explicit_boundedness(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires one of: --expires-at"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=str(todo["todo_id"]),
            agent_id=AUTHOR_AGENT,
            task_class="continuous_monitor",
            monitor_metadata={
                "target_key": "public-dependency",
                "cadence": "30m",
                "next_due_at": "2026-08-12T00:00:00Z",
            },
        )

    assert state.read_text(encoding="utf-8") == before


def test_continuous_monitor_accepts_resume_condition_bound(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    result = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Watch until the prerequisite PR merges.",
        task_class="continuous_monitor",
        action_kind="monitor",
        claimed_by=AUTHOR_AGENT,
        resume_when="pr_merged:#3083",
        monitor_metadata={
            "target_key": "pr-3083",
            "cadence": "30m",
            "next_due_at": "2026-08-12T00:00:00Z",
        },
    )
    item = _agent_todo(state, str(result["todo_id"]))
    assert item["resume_when"] == "pr_merged:#3083"


def test_unresolved_decision_scope_is_not_a_local_claim_gate(tmp_path: Path) -> None:
    """Characterize the local writer before shared revision publishers exist.

    The shared-authority RFC names dependency and decision-gate revisions as
    command preconditions.  The current Markdown writer stores the scopes but
    has no authoritative publisher for their resolution, so Stage 1 must not
    silently invent a gate and change local claim behavior.
    """

    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Wait for one exact release decision.",
        task_class="advancement_task",
        required_decision_scopes=[DECISION_SCOPE],
    )

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=str(todo["todo_id"]),
        claimed_by=AUTHOR_AGENT,
        agent_id=AUTHOR_AGENT,
        claim_only=True,
    )

    assert result["claimed_by"] == AUTHOR_AGENT
    item = _agent_todo(state, str(todo["todo_id"]))
    assert item["required_decision_scopes"] == [
        {
            "schema_version": "decision_scope_v0",
            "kind": "direction",
            "granularity": "action",
            "scope_key": "publish_release",
        }
    ]
    assert item["claimed_by"] == AUTHOR_AGENT


def test_completion_policy_unicode_parity_through_public_facade(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry, text="Continue with Python whitespace parity.")

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT,
        claimed_by=AUTHOR_AGENT,
        evidence="validated parity",
        next_agent_todo="Run the next bounded parity check.",
        next_continuation_policy="\u0085same_agent_non_delivery\u0085",
    )

    successor = _agent_todo(state, result["next_todos"][0]["todo_id"])
    assert successor["claimed_by"] == AUTHOR_AGENT

    blank_evidence = _add_agent_todo(
        registry,
        text="Reject Python-blank self-merge evidence.",
    )
    before = state.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="--self-merged requires --evidence"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=blank_evidence["todo_id"],
            agent_id=AUTHOR_AGENT,
            self_merged=True,
            evidence="\u0085",
            no_followup=True,
        )
    assert state.read_text(encoding="utf-8") == before

    bom_evidence = _add_agent_todo(
        registry,
        text="Accept evidence retained by Python strip.",
    )
    accepted = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=bom_evidence["todo_id"],
        agent_id=AUTHOR_AGENT,
        self_merged=True,
        evidence="\ufeff",
        no_followup=True,
    )
    assert accepted["completion_policy"]["self_merged"] is True


def test_completion_policy_errors_do_not_preempt_actor_or_lease_fences(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path, handoff_mode="hard_lease")
    claimed_by_other = _add_agent_todo(
        registry,
        text="Keep actor authority ahead of policy diagnostics.",
        claimed_by=REVIEW_AGENT,
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(LocalCoordinationAuthorityRejection) as denied:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=claimed_by_other["todo_id"],
            agent_id=AUTHOR_AGENT,
            self_merged=True,
            evidence="\u0085",
            no_followup=True,
        )
    assert denied.value.code == "claim_owner_mismatch"
    assert state.read_text(encoding="utf-8") == before

    leased = _add_agent_todo(
        registry,
        text="Keep the task lease ahead of policy diagnostics.",
        claimed_by=None,
    )
    before = state.read_text(encoding="utf-8")
    with pytest.raises(LocalCoordinationAuthorityRejection) as missing_lease:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=leased["todo_id"],
            agent_id=AUTHOR_AGENT,
            self_merged=True,
            evidence="\u0085",
            no_followup=True,
        )
    assert missing_lease.value.code == "handoff_mode_requires_lease"
    assert state.read_text(encoding="utf-8") == before


def test_capability_binding_cannot_be_rebound_by_duplicate_add(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    original = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Advance the admitted issue-fix route.",
        task_class="advancement_task",
        capability_binding_ref="issue-fix:feasibility-a1b2c3d4",
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(
        Exception,
        match="different capability_binding_ref",
    ):
        add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Advance the admitted issue-fix route.",
            task_class="advancement_task",
            capability_binding_ref="issue-fix:feasibility-e5f6a7b8",
        )

    assert state.read_text(encoding="utf-8") == before
    assert _agent_todo(state, original["todo_id"])["capability_binding_ref"] == (
        "issue-fix:feasibility-a1b2c3d4"
    )


def test_advancement_todo_preserves_public_target_key(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)

    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Advance the admitted issue-fix route.",
        task_class="advancement_task",
        action_kind="issue_fix_branch_validation",
        claimed_by=AUTHOR_AGENT,
        monitor_metadata={"target_key": "issue-fix:owner/repo:issue_42"},
    )

    projected = _agent_todo(state, todo["todo_id"])
    assert projected["action_kind"] == "issue_fix_branch_validation"
    assert projected["target_key"] == "issue-fix:owner/repo:issue_42"


def test_capability_binding_follows_generated_agent_successor(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Advance the admitted issue-fix route.",
        task_class="advancement_task",
        action_kind="issue_fix_branch_validation",
        capability_binding_ref="issue-fix:feasibility-a1b2c3d4",
        claimed_by=AUTHOR_AGENT,
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT,
        evidence="bounded validation passed",
        next_agent_todo="Open the validated issue-fix review packet.",
        next_action_kind="issue_fix_reviewer_request",
        next_claimed_by=AUTHOR_AGENT,
        next_continuation_policy="same_agent_non_delivery",
    )

    successor = _agent_todo(state, result["next_todos"][0]["todo_id"])
    assert successor["capability_binding_ref"] == ("issue-fix:feasibility-a1b2c3d4")


def test_duplicate_todo_add_rejects_invalid_task_domain_atomically(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Validate one adaptive lane.",
        task_class="advancement_task",
        task_domain="validation",
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="task_domain"):
        add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Validate one adaptive lane.",
            task_class="advancement_task",
            task_domain="../private",
        )

    assert state.read_text(encoding="utf-8") == before
    assert _agent_todo(state, todo["todo_id"])["task_domain"] == "validation"


def test_todo_update_rejects_invalid_task_domain_atomically(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Validate one adaptive lane.",
        task_class="advancement_task",
        task_domain="validation",
        claimed_by=AUTHOR_AGENT,
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="task_domain"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            task_domain="../private",
            agent_id=AUTHOR_AGENT,
        )

    assert state.read_text(encoding="utf-8") == before
    assert _agent_todo(state, todo["todo_id"])["task_domain"] == "validation"


def test_monitor_schedule_fields_remain_monitor_only(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="monitor schedule metadata requires"):
        add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Do not attach cadence to advancement work.",
            task_class="advancement_task",
            monitor_metadata={
                "target_key": "issue-fix:owner/repo:issue_42",
                "cadence": "15m",
            },
        )


def test_monitor_successor_replay_preserves_independent_reviewer_exclusion(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    monitor = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Poll one exact PR head.",
        task_class="continuous_monitor",
        action_kind="monitor_pr",
        task_repository="git:github.com/example/project",
        claimed_by=AUTHOR_AGENT,
        monitor_metadata={
            "target_key": "public-pr:42",
            "cadence": "15m",
            "watch_only": "true",
        },
    )
    review_text = "Independently review the exact PR head."
    review = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text=review_text,
        task_class="advancement_task",
        action_kind="review_and_merge",
        task_repository="git:github.com/example/project",
        continuation_policy="independent_handoff",
        required_capabilities=["network"],
        excluded_agents=[AUTHOR_AGENT],
        unblocks_todo_id=monitor["todo_id"],
        monitor_metadata={"target_key": "public-pr:42:review"},
    )

    result = write_monitor_poll_todo_state(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        generated_at="2026-07-18T00:15:00+00:00",
        execute=True,
        todo_id=monitor["todo_id"],
        result_hash="all-checks-green",
        material_change=True,
        next_agent_todo=review_text,
        next_action_kind="review_and_merge",
        next_task_repository="git:github.com/example/project",
        next_required_capabilities=["network"],
        next_continuation_policy="independent_handoff",
        next_target_key="public-pr:42:review",
        agent_id=AUTHOR_AGENT,
    )

    assert result is not None
    assert result["successor_receipts"][0]["todo_id"] == review["todo_id"]
    assert _agent_todo(state, review["todo_id"])["excluded_agents"] == [AUTHOR_AGENT]


def test_exact_user_gate_decision_scope_uses_controller_override(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    target = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Publish the approved release.",
        task_class="advancement_task",
        claimed_by=AUTHOR_AGENT,
        required_decision_scopes=[DECISION_SCOPE],
    )
    gate = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Approve the exact release publication.",
        task_class="user_gate",
        blocks_agent=AUTHOR_AGENT,
        decision_scope=DECISION_SCOPE,
        unblocks_todo_id=target["todo_id"],
    )

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=gate["todo_id"],
        role="user",
        decision_outcome="approve",
        evidence="owner approved the exact decision scope",
    )

    authority = result["mutation_authority"]
    assert authority["authority_mode"] == "exact_user_gate_decision_scope_override"
    assert authority["code"] == "terminal_transition"
    assert _agent_todo(state, target["todo_id"]).get(
        "required_decision_scopes", []
    ) == []


def test_delegated_orchestrator_can_complete_claimed_todo_with_reason(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(
        tmp_path,
        lifecycle_authority=[
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": ["complete", "reassign", "supersede"],
                "requires_reason": True,
            }
        ],
    )
    todo = _add_agent_todo(registry, claimed_by=REVIEW_AGENT)

    result = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=ORCHESTRATION_AGENT,
        authority_reason="Verified the worker result and closed the stalled lane.",
        evidence="validation://orchestrator-closeout",
    )

    authority = result["mutation_authority"]
    assert authority["schema_version"] == (
        "loopx_coordination_todo_terminal_decision_result_v0"
    )
    assert authority["authority_mode"] == "delegated_orchestration_override"
    assert authority["code"] == "terminal_transition"
    assert _agent_todo(state, todo["todo_id"])["status"] == "done"


def _canonical_todo(registry: Path, todo_id: str) -> dict:
    return list_goal_todos(registry_path=registry, goal_id=GOAL_ID, todo_id=todo_id)["todo"]


def _canonical_snapshot(registry: Path, state: Path) -> tuple:
    result = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)
    return result["authority_read"]["provider_revision"], result["todos"], state.read_bytes()


def _canonical_lease(registry: Path, tmp_path: Path, todo_id: str) -> dict | None:
    result = inspect_task_lease(
        registry_path=registry, runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID, todo_id=todo_id,
    )
    assert result["source_authority"] in {"file_v0", "sqlite_v0"}
    assert result["legacy_fallback_used"] is False
    return result.get("lease")


def _lease_path(tmp_path: Path, todo_id: str) -> Path:
    return tmp_path / "runtime" / "goals" / GOAL_ID / "task-leases" / f"{todo_id}.json"


@pytest.mark.parametrize("status", ["deferred", "blocked", "done"])
def test_canonical_claim_rejects_nonopen_status_atomically(
    tmp_path: Path, canonical_workspace, status: str,
) -> None:
    registry, state = canonical_workspace(tmp_path)
    if status == "deferred":
        todo = add_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, role="agent",
            text="Wait for external capacity before claim.", status="deferred",
            resume_when="capacity_available:network",
        )
    else:
        todo = _add_agent_todo(registry)
        if status == "done":
            complete_goal_todo(
                registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT, evidence="finished before the contested claim",
            )
        else:
            update_goal_todo(
                registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT, status=status,
            )
    before = _canonical_snapshot(registry, state)
    with pytest.raises(LocalCoordinationAuthorityRejection) as error:
        update_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
            claim_only=True, claimed_by=AUTHOR_AGENT, agent_id=AUTHOR_AGENT,
        )
    assert error.value.code == "todo_not_open"
    assert error.value.payload["todo_status"] == status
    assert _canonical_snapshot(registry, state) == before
    assert _canonical_lease(registry, tmp_path, todo["todo_id"]) is None


def test_canonical_claim_rejects_user_todo_atomically(tmp_path: Path, canonical_workspace) -> None:
    registry, state = canonical_workspace(tmp_path)
    todo = add_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, role="user",
        text="Owner reviews the delivered change.", task_class="user_action",
        bound_agent=AUTHOR_AGENT,
    )
    before = _canonical_snapshot(registry, state)
    with pytest.raises(LocalCoordinationAuthorityRejection) as error:
        update_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
            claim_only=True, claimed_by=AUTHOR_AGENT, agent_id=AUTHOR_AGENT,
        )
    assert error.value.code == "todo_not_agent"
    assert _canonical_snapshot(registry, state) == before
    assert _canonical_todo(registry, todo["todo_id"])["bound_agent"] == AUTHOR_AGENT


def test_canonical_claim_cannot_reactivate_archived_work(tmp_path: Path, canonical_workspace) -> None:
    registry, state = canonical_workspace(tmp_path)
    todo = _add_agent_todo(registry)
    complete_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT, evidence="finished before archival",
    )
    archive_completed_todos(
        registry_path=registry, goal_id=GOAL_ID, max_active_done=0, dry_run=False,
    )
    assert _canonical_todo(registry, todo["todo_id"])["archive_state"] == "archive"
    before = _canonical_snapshot(registry, state)
    with pytest.raises(LocalCoordinationAuthorityRejection) as error:
        update_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
            claim_only=True, claimed_by=AUTHOR_AGENT, agent_id=AUTHOR_AGENT,
        )
    # Status eligibility precedes archive eligibility in the shared claim owner.
    assert error.value.code == "todo_not_open"
    assert _canonical_snapshot(registry, state) == before


def test_canonical_claim_rejects_role_mismatch_atomically(tmp_path: Path, canonical_workspace) -> None:
    registry, state = canonical_workspace(tmp_path)
    todo = _add_agent_todo(registry, claimed_by=None)
    before = _canonical_snapshot(registry, state)
    with pytest.raises(LocalCoordinationAuthorityRejection) as error:
        update_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"], role="user",
            claim_only=True, claimed_by=AUTHOR_AGENT, agent_id=AUTHOR_AGENT,
        )
    assert error.value.code == "todo_role_mismatch"
    assert error.value.payload["requested_role"] == "user"
    assert error.value.payload["todo_role"] == "agent"
    assert _canonical_snapshot(registry, state) == before


def test_completion_and_lease_release_recover_after_response_loss(
    tmp_path: Path, canonical_workspace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Canonical completion releases its lease in the same commit; there is no
    # independent post-commit release call to fail. Lose the real response instead.
    registry, state = canonical_workspace(tmp_path, multi_agent=False, handoff_mode="hard_lease")
    todo = _add_agent_todo(registry)
    lease_key = "response-loss-instance"
    acquire_task_lease(
        registry_path=registry, runtime_root=tmp_path / "runtime", goal_id=GOAL_ID,
        todo_id=todo["todo_id"], owner=AUTHOR_AGENT, idempotency_key=lease_key, ttl_seconds=600,
    )
    request = dict(
        registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"], agent_id=AUTHOR_AGENT,
        task_lease_idempotency_key=lease_key, task_lease_expected_version=1,
        completion_turn_key="response-loss-turn", evidence="Original completion committed once.",
        no_followup=True,
    )
    native_effect = terminal_provider.effect_runtime_result
    committed = []
    def lose_response(method, payload, **kwargs):
        result = native_effect(method, payload, **kwargs)
        if method == "coordination.local_authority.todo_terminal" and not committed:
            assert result["status"] == "applied"
            committed.append(result)
            raise OSError("response lost after real canonical commit")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(terminal_provider, "effect_runtime_result", lose_response)
        with pytest.raises(OSError, match="response lost"):
            complete_goal_todo(**request)
    assert len(committed) == 1
    assert _canonical_todo(registry, todo["todo_id"])["status"] == "done"
    lease_after_commit = _canonical_lease(registry, tmp_path, todo["todo_id"])
    assert lease_after_commit["status"] == "released"
    replay = complete_goal_todo(**request)
    assert replay["ok"] is True and replay["idempotent_replay"] is True
    assert _canonical_lease(registry, tmp_path, todo["todo_id"]) == lease_after_commit
    after_recovery = _canonical_snapshot(registry, state)
    assert complete_goal_todo(**request)["idempotent_replay"] is True
    assert _canonical_snapshot(registry, state) == after_recovery


def test_completed_todo_rejects_metadata_reopen_and_new_execution(
    tmp_path: Path, canonical_workspace,
) -> None:
    registry, state = canonical_workspace(tmp_path, multi_agent=False, handoff_mode="hard_lease")
    todo = _add_agent_todo(registry)
    acquire_task_lease(
        registry_path=registry, runtime_root=tmp_path / "runtime", goal_id=GOAL_ID,
        todo_id=todo["todo_id"], owner=AUTHOR_AGENT, idempotency_key="first-instance", ttl_seconds=600,
    )
    complete_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"], agent_id=AUTHOR_AGENT,
        task_lease_idempotency_key="first-instance", task_lease_expected_version=1,
        completion_turn_key="first-turn", evidence="First completion releases its lease.",
    )
    released = _canonical_lease(registry, tmp_path, todo["todo_id"])
    assert released["status"] == "released"
    before = _canonical_snapshot(registry, state)
    # A completed ordinary Todo has no metadata-reopen contract. Keep terminal
    # history intact rather than silently resurrecting a retired execution.
    with pytest.raises(LocalCoordinationAuthorityRejection) as rejected:
        update_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"], agent_id=AUTHOR_AGENT,
            status="open", note="Metadata cannot reopen a completed execution.",
        )
    assert rejected.value.code == "unsupported_todo_update_target"
    assert _canonical_snapshot(registry, state) == before
    assert _canonical_lease(registry, tmp_path, todo["todo_id"]) == released
    with pytest.raises((ValueError, LocalCoordinationAuthorityRejection)):
        complete_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"], agent_id=AUTHOR_AGENT,
            task_lease_idempotency_key="first-instance", task_lease_expected_version=1,
            completion_turn_key="second-turn", evidence="Released proof cannot authorize new execution.",
        )
    assert _canonical_snapshot(registry, state) == before
    assert _canonical_lease(registry, tmp_path, todo["todo_id"]) == released


def test_invalid_completion_keeps_existing_lease_and_state(
    tmp_path: Path, canonical_workspace,
) -> None:
    registry, state = canonical_workspace(tmp_path, multi_agent=False, handoff_mode="hard_lease")
    todo = _add_agent_todo(registry)
    lease_key = "post-fence-failure-instance"
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key=lease_key,
        ttl_seconds=600,
    )
    lease_before = _canonical_lease(registry, tmp_path, todo["todo_id"])
    state_before = _canonical_snapshot(registry, state)

    with pytest.raises(ValueError, match="successor_todo_ids"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AUTHOR_AGENT,
            task_lease_idempotency_key=lease_key,
            task_lease_expected_version=1,
            successor_todo_ids=["!!not-a-todo-id!!"],
            evidence="invalid successor must leave state and lease unchanged",
            no_followup=True,
        )

    assert _canonical_lease(registry, tmp_path, todo["todo_id"]) == lease_before
    assert lease_before["status"] == "active"
    assert _canonical_snapshot(registry, state) == state_before


@pytest.mark.parametrize("command", ["update", "complete"])
def test_canonical_single_agent_owned_todo_requires_explicit_actor(
    tmp_path: Path, canonical_workspace, command: str,
) -> None:
    registry, state = canonical_workspace(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    before = _canonical_snapshot(registry, state)
    with pytest.raises(LocalCoordinationAuthorityRejection) as rejected:
        if command == "update":
            update_goal_todo(registry_path=registry, goal_id=GOAL_ID,
                todo_id=todo["todo_id"], note="Actorless owned mutation is forbidden")
        else:
            complete_goal_todo(registry_path=registry, goal_id=GOAL_ID,
                todo_id=todo["todo_id"], evidence="Actorless owned mutation is forbidden")
    assert rejected.value.code == "actor_required"
    assert _canonical_snapshot(registry, state) == before


def test_canonical_update_revision_conflict_is_typed_without_actor_mutation(
    tmp_path: Path, canonical_workspace,
) -> None:
    registry, state = canonical_workspace(tmp_path)
    todo = _add_agent_todo(registry)
    before = _canonical_snapshot(registry, state)
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as rejected:
        update_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
            agent_id=AUTHOR_AGENT, note="A stale writer cannot commit",
            update_expected_provider_revision="stale-revision",
        )
    assert rejected.value.code == "provider_revision_mismatch"
    assert isinstance(rejected.value, LocalCoordinationAuthorityRejection)
    assert rejected.value.payload["failure_kind"] == "decision_rejection"
    assert _canonical_snapshot(registry, state) == before


@pytest.fixture(params=["file", "sqlite"])
def canonical_workspace(request, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)

    def create(root, **kwargs):
        return _write_fixture(root, provider=request.param, **kwargs)

    return create
