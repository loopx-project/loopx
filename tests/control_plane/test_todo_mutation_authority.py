from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.scheduler.monitor_poll_writeback import (
    write_monitor_poll_todo_state,
)
import loopx.control_plane.todos.mutation_authority as mutation_authority_module
import loopx.control_plane.work_items.task_lease as task_lease_module
from loopx.control_plane.work_items.task_lease import (
    TaskLeaseError,
    acquire_task_lease,
    release_task_lease,
)
from loopx.status import parse_active_state_todos
from loopx.todos import (
    add_goal_todo,
    complete_goal_todo,
    supersede_goal_todo,
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
    return registry, state


def _lease_path(tmp_path: Path, todo_id: str) -> Path:
    return tmp_path / "runtime" / "goals" / GOAL_ID / "task-leases" / f"{todo_id}.json"


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


def test_multi_agent_update_requires_actor_and_is_atomic(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires --agent-id"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            note="This must not be written without an attributed actor.",
        )

    assert state.read_text(encoding="utf-8") == before


def test_registered_actor_can_correct_unclaimed_todo_without_claim(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry, claimed_by=None)
    result = update_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT, text="Correct unclaimed copy", note="Correct note",
    )
    assert result["mutation_authority"]["mode"] == "registered_peer_actor"
    corrected = _agent_todo(state, todo["todo_id"])
    assert corrected["text"] == "Correct unclaimed copy"
    assert corrected["note"] == "Correct note"
    assert not corrected.get("claimed_by")


def test_excluded_actor_cannot_mutate_unclaimed_todo(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(
        registry,
        claimed_by=None,
        excluded_agents=[AUTHOR_AGENT],
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="is excluded from mutating"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AUTHOR_AGENT,
            note="An excluded author cannot rewrite review work.",
        )

    assert state.read_text(encoding="utf-8") == before


def test_idempotent_add_preserves_existing_executor_exclusions(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(
        registry,
        text="Independently review one exact PR head.",
        claimed_by=None,
        excluded_agents=[AUTHOR_AGENT],
    )

    replay = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Independently review one exact PR head.",
        task_class="advancement_task",
    )

    assert replay["todo_id"] == todo["todo_id"]
    assert replay["already_exists"] is True
    assert replay["metadata_updated"] is False
    assert replay["excluded_agents"] == [AUTHOR_AGENT]
    assert _agent_todo(state, todo["todo_id"])["excluded_agents"] == [AUTHOR_AGENT]


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


@pytest.mark.parametrize("command", ["update", "complete", "supersede"])
def test_non_owner_cannot_mutate_claimed_todo(
    tmp_path: Path,
    command: str,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry, claimed_by=REVIEW_AGENT)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="claimed_by='codex-review'"):
        if command == "update":
            update_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT,
                note="unauthorized",
            )
        elif command == "complete":
            complete_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT,
                evidence="unauthorized",
            )
        else:
            supersede_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT,
                reason="unauthorized",
            )

    assert state.read_text(encoding="utf-8") == before


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
    assert accepted["self_merged"] is True


def test_completion_policy_errors_do_not_preempt_actor_or_lease_fences(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path)
    claimed_by_other = _add_agent_todo(
        registry,
        text="Keep actor authority ahead of policy diagnostics.",
        claimed_by=REVIEW_AGENT,
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="claimed_by='codex-review'"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=claimed_by_other["todo_id"],
            agent_id=AUTHOR_AGENT,
            self_merged=True,
            evidence="\u0085",
            no_followup=True,
        )
    assert state.read_text(encoding="utf-8") == before

    leased = _add_agent_todo(
        registry,
        text="Keep the task lease ahead of policy diagnostics.",
    )
    lease_key = "policy-priority-instance"
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=leased["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key=lease_key,
        ttl_seconds=600,
    )
    before = state.read_text(encoding="utf-8")
    with pytest.raises(TaskLeaseError) as stale_lease:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=leased["todo_id"],
            agent_id=AUTHOR_AGENT,
            task_lease_idempotency_key=lease_key,
            task_lease_expected_version=0,
            self_merged=True,
            evidence="\u0085",
            no_followup=True,
        )
    assert stale_lease.value.code == "version_mismatch"
    assert state.read_text(encoding="utf-8") == before


def test_owner_actor_update_returns_typed_receipt(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry)

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT,
        note="owner-attributed update",
    )

    assert result["mutation_authority"] == {
        "schema_version": "todo_mutation_authority_v0",
        "command": "update",
        "mode": "registered_peer_actor",
        "actor_agent_id": AUTHOR_AGENT,
        "todo_id": todo["todo_id"],
        "claim_owner": AUTHOR_AGENT,
        "registered_agent_count": 3,
    }
    assert _agent_todo(state, todo["todo_id"])["note"] == "owner-attributed update"


@pytest.mark.parametrize("status", ["deferred", "blocked", "done"])
def test_claim_rejection_preserves_status_suffix(
    tmp_path: Path,
    status: str,
) -> None:
    """Claim rejections keep the legacy todo_id/status diagnostic suffix."""

    registry, state = _write_fixture(tmp_path)
    if status == "deferred":
        todo = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Wait for external capacity before claim.",
            status="deferred",
            resume_when="capacity_available:network",
        )
    else:
        todo = _add_agent_todo(registry)
        if status == "done":
            complete_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT,
                evidence="finished before the contested claim",
            )
        else:
            update_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT,
                status=status,
            )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claim_only=True,
            claimed_by=AUTHOR_AGENT,
            agent_id=AUTHOR_AGENT,
        )

    assert str(excinfo.value) == (
        f"todo claim requires status=open; todo_id '{todo['todo_id']}' "
        f"is status='{status}'"
    )
    assert state.read_text(encoding="utf-8") == before


def test_claim_user_todo_rejection_preserves_binding_hint(
    tmp_path: Path,
) -> None:
    """Claiming a user Todo keeps the legacy --bound-agent remediation hint."""

    registry, state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Owner reviews the delivered change.",
        task_class="user_action",
        bound_agent=AUTHOR_AGENT,
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claim_only=True,
            claimed_by=AUTHOR_AGENT,
            agent_id=AUTHOR_AGENT,
        )

    assert str(excinfo.value) == (
        "claimed_by is execution ownership for agent todos, not a user-todo "
        "binding; use --bound-agent or --goal-bound"
    )
    assert state.read_text(encoding="utf-8") == before


def test_claim_rejection_preserves_removed_policy_repair_hint(
    tmp_path: Path,
) -> None:
    """Claiming a removed-policy Todo keeps the legacy repair instructions."""

    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry, claimed_by=None)
    text = state.read_text(encoding="utf-8")
    marker = f"todo_id={todo['todo_id']} "
    assert marker in text
    state.write_text(
        text.replace(
            marker,
            f"{marker}removed_continuation_policy=primary_review ",
            1,
        ),
        encoding="utf-8",
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claim_only=True,
            claimed_by=AUTHOR_AGENT,
            agent_id=AUTHOR_AGENT,
        )

    assert str(excinfo.value) == (
        f"todo_id '{todo['todo_id']}' uses removed continuation_policy="
        "primary_review; repair it before claiming"
    )
    assert state.read_text(encoding="utf-8") == before


def test_claim_archived_todo_rejection_preserves_not_found_message(
    tmp_path: Path,
) -> None:
    """Archived-Todo claim rejections keep the legacy not-found message."""

    registry, _state = _write_fixture(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        mutation_authority_module.authorize_todo_lifecycle_mutation(
            registry_path=registry,
            goal_id=GOAL_ID,
            command="claim",
            todo={
                "todo_id": "todo_archivedprobe",
                "role": "agent",
                "status": "open",
                "archive_state": "archive",
            },
            actor_agent_id=AUTHOR_AGENT,
            requested_claimed_by=AUTHOR_AGENT,
        )

    assert str(excinfo.value) == (
        "todo_id 'todo_archivedprobe' was not found in active user or agent todos"
    )


def test_claim_role_mismatch_rejection_preserves_not_found_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Role-mismatch claim rejections keep the legacy not-found message."""

    registry, _state = _write_fixture(tmp_path)

    def fake_typescript_decision(_command: str, _payload: dict) -> dict:
        return {
            "status": "rejected",
            "reason_code": "todo_role_mismatch",
            "reason": "Todo does not have the requested role",
        }

    monkeypatch.setattr(
        mutation_authority_module,
        "effect_runtime_result",
        fake_typescript_decision,
    )

    with pytest.raises(ValueError) as excinfo:
        mutation_authority_module.authorize_todo_lifecycle_mutation(
            registry_path=registry,
            goal_id=GOAL_ID,
            command="claim",
            todo={
                "todo_id": "todo_rolemismatchprobe",
                "role": "agent",
                "status": "open",
            },
            actor_agent_id=AUTHOR_AGENT,
            requested_claimed_by=AUTHOR_AGENT,
        )

    assert str(excinfo.value) == (
        "todo_id 'todo_rolemismatchprobe' was not found in active user or agent todos"
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


def test_active_task_lease_fences_same_agent_completion_instance(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    lease_key = "turn-owner-instance"
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key=lease_key,
        ttl_seconds=600,
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(TaskLeaseError) as missing_fence:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            evidence="stale execution instance",
            next_agent_todo="Create a stale successor.",
        )
    assert missing_fence.value.code == "lease_fence_required"
    assert state.read_text(encoding="utf-8") == before

    with pytest.raises(TaskLeaseError) as mismatched_fence:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            task_lease_idempotency_key="other-instance",
            evidence="wrong execution instance",
        )
    assert mismatched_fence.value.code == "lease_cas_mismatch"
    assert state.read_text(encoding="utf-8") == before

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=1,
        evidence="lease owner validated the result",
        next_agent_todo="Create the canonical successor.",
    )
    assert completed["task_lease_fence"] == {
        "schema_version": "task_lease_v0",
        "required": True,
        "active": True,
        "owner": AUTHOR_AGENT,
        "version": 1,
        "lease_epoch": 1,
        "execution_instance_verified": True,
        "released": True,
    }
    assert (
        json.loads(_lease_path(tmp_path, todo["todo_id"]).read_text(encoding="utf-8"))[
            "status"
        ]
        == "released"
    )

    replayed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        evidence="late result from another execution instance",
        next_agent_todo="Create a duplicate stale successor.",
    )
    assert replayed["idempotent_replay"] is True
    assert replayed["changed"] is False
    agent_todos = parse_active_state_todos(state.read_text(encoding="utf-8"))[
        "agent_todos"
    ]["items"]
    todo_titles = [
        str(item.get("title") or item.get("text") or "") for item in agent_todos
    ]
    assert todo_titles.count("Create the canonical successor.") == 1
    assert "Create a duplicate stale successor." not in todo_titles


def test_released_lease_generation_rejects_stale_completion_fences(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    runtime_root = tmp_path / "runtime"
    first = acquire_task_lease(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key="execution-one",
        ttl_seconds=600,
    )
    assert first["lease"]["version"] == 1
    assert first["lease"]["lease_epoch"] == 1

    released = release_task_lease(
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key="execution-one",
        expected_version=1,
        registry_path=registry,
    )
    assert released["lease"]["status"] == "released"
    assert _lease_path(tmp_path, todo["todo_id"]).exists()

    with pytest.raises(TaskLeaseError) as reused_execution:
        acquire_task_lease(
            registry_path=registry,
            runtime_root=runtime_root,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            owner=AUTHOR_AGENT,
            idempotency_key="execution-one",
            ttl_seconds=600,
        )
    assert reused_execution.value.code == "idempotency_key_reuse"

    second = acquire_task_lease(
        registry_path=registry,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key="execution-two",
        ttl_seconds=600,
    )
    assert second["lease"]["version"] == 2
    assert second["lease"]["lease_epoch"] == 2
    before = state.read_text(encoding="utf-8")

    with pytest.raises(TaskLeaseError) as old_key:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            task_lease_idempotency_key="execution-one",
            task_lease_expected_version=1,
            evidence="stale first execution",
            no_followup=True,
        )
    assert old_key.value.code == "lease_cas_mismatch"
    assert state.read_text(encoding="utf-8") == before

    with pytest.raises(TaskLeaseError) as old_version:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            task_lease_idempotency_key="execution-two",
            task_lease_expected_version=1,
            evidence="stale version from the first generation",
            no_followup=True,
        )
    assert old_version.value.code == "version_mismatch"
    assert state.read_text(encoding="utf-8") == before

    with pytest.raises(TaskLeaseError) as missing_version:
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            task_lease_idempotency_key="execution-two",
            evidence="incomplete fence",
            no_followup=True,
        )
    assert missing_version.value.code == "version_required"
    assert state.read_text(encoding="utf-8") == before

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        task_lease_idempotency_key="execution-two",
        task_lease_expected_version=2,
        evidence="current generation completed",
        no_followup=True,
    )
    assert completed["task_lease_fence"]["lease_epoch"] == 2
    tombstone = json.loads(
        _lease_path(tmp_path, todo["todo_id"]).read_text(encoding="utf-8")
    )
    assert tombstone["status"] == "released"
    assert tombstone["version"] == 2
    assert tombstone["lease_epoch"] == 2


def test_completion_turn_key_rejects_cross_turn_replay(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry)

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT,
        evidence="turn A validated the selected Todo",
        completion_turn_key="turn-a",
        no_followup=True,
    )
    assert completed["completed"] is True
    completed_todo = _agent_todo(state, todo["todo_id"])
    assert completed_todo["completion_turn_key"] == "turn-a"
    assert completed_todo["completion_continuation"] == "no_followup"
    completed_state = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="different completion_turn_key"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=AUTHOR_AGENT,
            evidence="turn B must not claim turn A's completion",
            completion_turn_key="turn-b",
            no_followup=True,
        )
    assert state.read_text(encoding="utf-8") == completed_state

    replayed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT,
        evidence="turn A retried after its durable write",
        completion_turn_key="turn-a",
        no_followup=True,
    )
    assert replayed["idempotent_replay"] is True
    assert replayed["changed"] is False


def test_terminal_upgrade_cannot_replace_existing_successor(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        completion_turn_key="turn-a",
        evidence="ordinary completion created the successor",
        next_agent_todo="Continue the durable successor.",
    )
    assert completed["changed"] is True
    assert completed["completion_continuation"] == "successor"

    with pytest.raises(ValueError, match="cannot replace an existing successor"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            completion_turn_key="turn-a",
            evidence="terminal closeout must not erase the successor",
            no_followup=True,
        )


def test_terminal_upgrade_requires_original_completion_turn_key(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        completion_turn_key="turn-a",
        evidence="ordinary completion from the original turn",
    )
    assert completed["changed"] is True
    assert completed["completion_continuation"] == "active_goal"

    with pytest.raises(ValueError, match="requires the original completion_turn_key"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            evidence="an unscoped caller cannot claim terminal closeout",
            no_followup=True,
        )


def test_terminal_upgrade_rejects_untyped_legacy_completion_until_repaired(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    todo_id = todo["todo_id"]

    complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        claimed_by=AUTHOR_AGENT,
        completion_turn_key="turn-a",
        evidence="ordinary completion from the original turn",
    )
    state.write_text(
        state.read_text(encoding="utf-8").replace(
            " completion_continuation=active_goal",
            "",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing completion_continuation"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo_id,
            claimed_by=AUTHOR_AGENT,
            completion_turn_key="turn-a",
            evidence="legacy ambiguity must not be inferred",
            no_followup=True,
        )

    repaired = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        claimed_by=AUTHOR_AGENT,
        completion_turn_key="turn-a",
        evidence="repair explicit completion continuation",
        note="repair explicit completion continuation",
    )
    assert repaired["completion_continuation"] == "active_goal"

    upgraded = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo_id,
        claimed_by=AUTHOR_AGENT,
        completion_turn_key="turn-a",
        evidence="same-turn terminal closeout after explicit repair",
        no_followup=True,
    )
    assert upgraded["completion_continuation"] == "no_followup"
    assert upgraded["completion_recovery"] == "same_turn_terminal_closeout"




def test_unfenced_completion_leaves_no_lease_artifacts(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        evidence="no lease was ever taken",
        no_followup=True,
    )

    assert completed["ok"] is True
    assert completed["task_lease_fence"] == {
        "schema_version": "task_lease_v0",
        "required": False,
        "active": False,
    }
    lease_dir = _lease_path(tmp_path, todo["todo_id"]).parent
    assert list(lease_dir.glob("todo_*.json")) == []


def test_divergent_claim_completion_leaves_foreign_lease_untouched(
    tmp_path: Path,
) -> None:
    registry, _state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry, claimed_by=None)
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key="author-instance",
        ttl_seconds=600,
    )
    update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=REVIEW_AGENT,
        agent_id=REVIEW_AGENT,
        claim_only=True,
    )
    lease_path = _lease_path(tmp_path, todo["todo_id"])
    lease_before = lease_path.read_text(encoding="utf-8")

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=REVIEW_AGENT,
        agent_id=REVIEW_AGENT,
        evidence="split-brain claimer completed without the lease key",
        no_followup=True,
    )

    assert completed["ok"] is True
    assert completed["task_lease_fence"] == {
        "schema_version": "task_lease_v0",
        "required": False,
        "active": False,
    }
    assert lease_path.read_text(encoding="utf-8") == lease_before
    assert json.loads(lease_before)["owner"] == AUTHOR_AGENT


def test_dry_run_completion_does_not_release_lease(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    lease_key = "dry-run-instance"
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key=lease_key,
        ttl_seconds=600,
    )
    before = state.read_text(encoding="utf-8")

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=1,
        evidence="dry run must not touch the lease",
        no_followup=True,
        dry_run=True,
    )

    assert completed["dry_run"] is True
    assert completed["task_lease_fence"]["execution_instance_verified"] is True
    assert "released" not in completed["task_lease_fence"]
    lease_path = _lease_path(tmp_path, todo["todo_id"])
    assert json.loads(lease_path.read_text(encoding="utf-8"))["status"] == "active"
    assert state.read_text(encoding="utf-8") == before


def test_release_failure_after_commit_keeps_completion_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    lease_key = "release-failure-instance"
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key=lease_key,
        ttl_seconds=600,
    )

    native_lifecycle = task_lease_module._execute_native_task_lease_lifecycle

    def _fail_release(**kwargs: object) -> dict:
        if (
            kwargs.get("operation") == "fence_close"
            and kwargs.get("committed") is True
            and kwargs.get("release_lease") is True
        ):
            native_lifecycle(
                **{
                    **kwargs,
                    "committed": False,
                    "release_lease": False,
                }
            )
            raise OSError("simulated terminal write failure")
        return native_lifecycle(**kwargs)

    monkeypatch.setattr(
        task_lease_module,
        "_execute_native_task_lease_lifecycle",
        _fail_release,
    )
    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=1,
        evidence="completion must survive a failed lease release",
        no_followup=True,
    )

    assert completed["ok"] is True
    assert completed["completed"] is True
    assert completed["task_lease_fence"]["execution_instance_verified"] is True
    assert completed["task_lease_fence"]["released"] is False
    lease_path = _lease_path(tmp_path, todo["todo_id"])
    assert json.loads(lease_path.read_text(encoding="utf-8"))["status"] == "active"


def test_reopened_todo_completes_unfenced_after_lease_release(
    tmp_path: Path,
) -> None:
    """Pin the accepted reopen-window divergence of release-on-completion.

    The terminal tombstone retains the generation but is not time-active, so a
    manually reopened todo completes unfenced. The tombstone exists only to
    prevent a later acquire from reusing the old execution generation.
    """

    registry, _state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    lease_key = "reopen-window-instance"
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key=lease_key,
        ttl_seconds=600,
    )

    completed = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=1,
        evidence="first completion releases the verified lease",
        no_followup=True,
    )
    assert completed["task_lease_fence"]["released"] is True
    assert (
        json.loads(_lease_path(tmp_path, todo["todo_id"]).read_text(encoding="utf-8"))[
            "status"
        ]
        == "released"
    )

    reopened = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        status="open",
        note="manual reopen inside the old lease TTL",
    )
    assert reopened["ok"] is True

    recompleted = complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        evidence="reopened todo completes without a lease fence",
        no_followup=True,
    )
    assert recompleted["ok"] is True
    assert recompleted["task_lease_fence"] == {
        "schema_version": "task_lease_v0",
        "required": False,
        "active": False,
    }


def test_cli_release_after_auto_release_replays_terminal_tombstone(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The public CLI replays the retained terminal result, not `missing`."""

    registry, _state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)
    lease_key = "double-release-instance"
    acquire_task_lease(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        owner=AUTHOR_AGENT,
        idempotency_key=lease_key,
        ttl_seconds=600,
    )
    complete_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        task_lease_idempotency_key=lease_key,
        task_lease_expected_version=1,
        evidence="completion already released the lease",
        no_followup=True,
    )
    lease_path = _lease_path(tmp_path, todo["todo_id"])
    tombstone = json.loads(lease_path.read_text(encoding="utf-8"))

    exit_code = cli_main(
        [
            "--registry",
            str(registry),
            "--format",
            "json",
            "task-lease",
            "release",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo["todo_id"],
            "--owner",
            AUTHOR_AGENT,
            "--idempotency-key",
            lease_key,
            "--expected-version",
            "1",
        ]
    )
    released = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert released["ok"] is True
    assert released["action"] == "release"
    assert released["released"] is True
    assert released["idempotent"] is True
    assert released["lease"] == tombstone
    assert released["lease"]["status"] == "released"
    assert released["lease"]["version"] == 1
    assert released["lease"]["lease_epoch"] == 1
    assert "missing" not in released
    assert json.loads(lease_path.read_text(encoding="utf-8")) == tombstone


def test_exception_after_verified_fence_leaves_lease_intact(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
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
    lease_path = _lease_path(tmp_path, todo["todo_id"])
    lease_before = lease_path.read_text(encoding="utf-8")
    state_before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="successor_todo_ids"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            task_lease_idempotency_key=lease_key,
            task_lease_expected_version=1,
            successor_todo_ids=["!!not-a-todo-id!!"],
            evidence="fails after the fence verified",
            no_followup=True,
        )

    assert lease_path.read_text(encoding="utf-8") == lease_before
    assert json.loads(lease_before)["status"] == "active"
    assert state.read_text(encoding="utf-8") == state_before








def test_capability_binding_cannot_be_rebound_by_duplicate_add(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Advance the admitted issue-fix route.",
        task_class="advancement_task",
        capability_binding_ref="issue-fix:feasibility-a1b2c3d4",
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="immutable once set"):
        add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Advance the admitted issue-fix route.",
            task_class="advancement_task",
            capability_binding_ref="issue-fix:feasibility-e5f6a7b8",
        )

    assert state.read_text(encoding="utf-8") == before












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


def test_claim_actor_must_match_requested_owner(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(registry, claimed_by=None)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires --claimed-by to match"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            claimed_by=AUTHOR_AGENT,
            agent_id=REVIEW_AGENT,
            claim_only=True,
        )
    assert state.read_text(encoding="utf-8") == before

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        claimed_by=AUTHOR_AGENT,
        agent_id=AUTHOR_AGENT,
        claim_only=True,
    )
    assert result["mutation_authority"]["command"] == "claim"
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AUTHOR_AGENT


def test_monitor_writeback_propagates_multi_agent_actor(tmp_path: Path) -> None:
    registry, _state = _write_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Poll one public monitor target.",
        task_class="continuous_monitor",
        claimed_by=AUTHOR_AGENT,
        monitor_metadata={
            "target_key": "public-pr:42",
            "cadence": "15m",
            "watch_only": "true",
        },
    )

    result = write_monitor_poll_todo_state(
        registry_path=registry,
        runtime_root=tmp_path / "runtime",
        goal_id=GOAL_ID,
        generated_at="2026-07-18T00:15:00+00:00",
        execute=False,
        todo_id=todo["todo_id"],
        result_hash="unchanged",
        agent_id=AUTHOR_AGENT,
    )

    assert result is not None
    authority = result["todo_update"]["mutation_authority"]
    assert authority["mode"] == "registered_peer_actor"
    assert authority["actor_agent_id"] == AUTHOR_AGENT


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
    assert _agent_todo(state, review["todo_id"])["excluded_agents"] == [
        AUTHOR_AGENT
    ]


def test_exact_user_gate_decision_scope_uses_controller_override(
    tmp_path: Path,
) -> None:
    registry, _state = _write_fixture(tmp_path)
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
    assert authority["mode"] == "exact_user_gate_decision_scope_override"
    assert authority["actor_agent_id"] is None
    assert authority["target_todo_id"] == target["todo_id"]
    assert authority["decision_scope"]["scope_key"] == "publish_release"


def test_exact_user_gate_ignores_unrelated_malformed_lifecycle_grant(
    tmp_path: Path,
) -> None:
    registry, _state = _write_fixture(
        tmp_path,
        lifecycle_authority=[
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": [],
            }
        ],
    )
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

    assert result["mutation_authority"]["mode"] == (
        "exact_user_gate_decision_scope_override"
    )


def test_non_exact_user_gate_cannot_bypass_actor_attribution(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path)
    target = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="agent",
        text="Announce the approved release.",
        task_class="advancement_task",
        claimed_by=AUTHOR_AGENT,
        required_decision_scopes=["direction:action:announce_release"],
    )
    gate = add_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        role="user",
        text="Approve a different action.",
        task_class="user_gate",
        blocks_agent=AUTHOR_AGENT,
        decision_scope=DECISION_SCOPE,
        unblocks_todo_id=target["todo_id"],
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires --agent-id"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=gate["todo_id"],
            role="user",
            decision_outcome="approve",
            evidence="scope mismatch",
        )

    assert state.read_text(encoding="utf-8") == before


def test_single_agent_goal_keeps_lifecycle_compatibility(tmp_path: Path) -> None:
    registry, state = _write_fixture(tmp_path, multi_agent=False)
    todo = _add_agent_todo(registry)

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        note="legacy single-agent update",
    )

    assert result["mutation_authority"]["mode"] == "single_agent_compatibility"
    assert _agent_todo(state, todo["todo_id"])["note"] == "legacy single-agent update"


def test_single_agent_ignores_unrelated_malformed_lifecycle_grant(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(
        tmp_path,
        multi_agent=False,
        lifecycle_authority=[
            {
                "agent_id": REVIEW_AGENT,
                "actions": ["complete"],
            }
        ],
    )
    todo = _add_agent_todo(registry)

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        note="legacy single-agent update",
    )

    assert result["mutation_authority"]["mode"] == "single_agent_compatibility"
    assert _agent_todo(state, todo["todo_id"])["note"] == "legacy single-agent update"


def test_current_owner_ignores_unrelated_malformed_lifecycle_grant(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(
        tmp_path,
        lifecycle_authority=[
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": ["unsupported-action"],
            }
        ],
    )
    todo = _add_agent_todo(registry)

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=AUTHOR_AGENT,
        note="owner-attributed update",
    )

    assert result["mutation_authority"]["mode"] == "registered_peer_actor"
    assert _agent_todo(state, todo["todo_id"])["note"] == "owner-attributed update"


@pytest.mark.parametrize("command", ["complete", "supersede"])
def test_author_cannot_replace_explicit_independent_review(
    tmp_path: Path,
    command: str,
) -> None:
    registry, state = _write_fixture(tmp_path)
    todo = _add_agent_todo(
        registry,
        claimed_by=REVIEW_AGENT,
        excluded_agents=[AUTHOR_AGENT],
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="is excluded from mutating"):
        if command == "complete":
            complete_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT,
                evidence="author cannot self-review",
            )
        else:
            supersede_goal_todo(
                registry_path=registry,
                goal_id=GOAL_ID,
                todo_id=todo["todo_id"],
                agent_id=AUTHOR_AGENT,
                reason="author cannot replace review",
            )

    assert state.read_text(encoding="utf-8") == before


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

    assert result["mutation_authority"] == {
        "schema_version": "todo_mutation_authority_v0",
        "command": "complete",
        "mode": "delegated_orchestration_override",
        "actor_agent_id": ORCHESTRATION_AGENT,
        "todo_id": todo["todo_id"],
        "claim_owner": REVIEW_AGENT,
        "authority_action": "complete",
        "authority_source": "coordination.todo_lifecycle_authority",
        "authority_reason": "Verified the worker result and closed the stalled lane.",
        "requires_reason": True,
        "registered_agent_count": 3,
    }
    assert _agent_todo(state, todo["todo_id"])["status"] == "done"


def test_delegated_orchestrator_requires_reason_before_state_write(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(
        tmp_path,
        lifecycle_authority=[
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": ["complete"],
                "requires_reason": True,
            }
        ],
    )
    todo = _add_agent_todo(registry, claimed_by=REVIEW_AGENT)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="requires --authority-reason"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=ORCHESTRATION_AGENT,
            evidence="must remain atomic",
        )

    assert state.read_text(encoding="utf-8") == before


def test_delegated_orchestration_authority_is_action_scoped(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(
        tmp_path,
        lifecycle_authority=[
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": ["reassign"],
                "requires_reason": True,
            }
        ],
    )
    todo = _add_agent_todo(registry, claimed_by=REVIEW_AGENT)
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="does not grant action='complete'"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=ORCHESTRATION_AGENT,
            authority_reason="This grant only permits reassignment.",
            evidence="unauthorized-action",
        )
    assert state.read_text(encoding="utf-8") == before

    with pytest.raises(ValueError, match="does not grant action='update'"):
        update_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=ORCHESTRATION_AGENT,
            claimed_by=AUTHOR_AGENT,
            status="done",
            authority_reason="A reassign grant cannot carry another mutation.",
        )
    assert state.read_text(encoding="utf-8") == before

    result = update_goal_todo(
        registry_path=registry,
        goal_id=GOAL_ID,
        todo_id=todo["todo_id"],
        agent_id=ORCHESTRATION_AGENT,
        claimed_by=AUTHOR_AGENT,
        authority_reason="Move the stalled work to an available peer.",
    )

    assert result["mutation_authority"]["authority_action"] == "reassign"
    assert _agent_todo(state, todo["todo_id"])["claimed_by"] == AUTHOR_AGENT


def test_delegated_override_never_bypasses_explicit_exclusion(
    tmp_path: Path,
) -> None:
    registry, state = _write_fixture(
        tmp_path,
        lifecycle_authority=[
            {
                "agent_id": ORCHESTRATION_AGENT,
                "actions": ["complete", "supersede"],
                "requires_reason": True,
            }
        ],
    )
    todo = _add_agent_todo(
        registry,
        claimed_by=REVIEW_AGENT,
        excluded_agents=[ORCHESTRATION_AGENT],
    )
    before = state.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="is excluded from mutating"):
        complete_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            todo_id=todo["todo_id"],
            agent_id=ORCHESTRATION_AGENT,
            authority_reason="An explicit exclusion remains authoritative.",
            evidence="must-not-write",
        )

    assert state.read_text(encoding="utf-8") == before
