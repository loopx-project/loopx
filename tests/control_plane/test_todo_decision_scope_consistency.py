from __future__ import annotations

import pytest

from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.control_plane.todos.decision_scope import (
    build_required_decision_scope_consistency,
    build_required_decision_scope_repair_hint,
    standing_decision_authority_for_agent,
)
from loopx.control_plane.todos.standing_decision import standing_decision_source_items
from loopx.quota import build_quota_should_run

AGENT_ID = "codex-quality-qualification"
SCOPE = {
    "schema_version": "decision_scope_v0",
    "kind": "direction",
    "granularity": "action",
    "scope_key": "publish_quality_contract",
}


def project_standing_authority(items, *, legacy_source_order=True, canonical_records=False):
    """Exercise the production source decoder and composed Todo read transaction."""
    from loopx.control_plane.todos.goal_todo_projection import source_todo_fields

    return source_todo_fields(
        [], lineage=[],
        standing_items=standing_decision_source_items(items, canonical_records=canonical_records),
        legacy_source_order=legacy_source_order and not canonical_records,
    ).get("standing_decision_authority")


def test_explicit_gate_scope_is_not_cancelled_by_another_claimant() -> None:
    gate = {"todo_id": "todo_scoped_gate", "status": "open", "task_class": "user_gate",
            "blocks_agent": AGENT_ID, "claimed_by": "agent-b", "decision_scope": SCOPE}
    result = build_required_decision_scope_consistency(
        _agent_summary(), _user_summary(gate), agent_id=AGENT_ID,
        registered_agent_ids=[AGENT_ID, "agent-b"],
    )
    assert result["ok"] is True
    assert result["errors"] == []


def test_exact_link_to_other_work_cannot_satisfy_a_required_scope() -> None:
    gate = {"todo_id": "todo_scoped_gate", "status": "open", "task_class": "user_gate",
            "blocks_agent": AGENT_ID, "unblocks_todo_id": "todo_different_work", "decision_scope": SCOPE}
    result = build_required_decision_scope_consistency(
        _agent_summary(), _user_summary(gate), agent_id=AGENT_ID,
    )
    assert result["ok"] is False
    assert result["errors"][0]["reason_code"] == "required_decision_scope_target_mismatch"
    hint = build_required_decision_scope_repair_hint(result)
    assert "owner intent" in hint["repair_focus"]
    assert "invent approval" in hint["repair_focus"]


def test_standing_scope_legacy_codec_is_retained() -> None:
    authority = {"entries": [{"active": True, "global_gate": "yes", "decision_scope": "direction:action:publish_quality_contract"}]}
    scoped = standing_decision_authority_for_agent(authority, agent_id=AGENT_ID)
    assert scoped["active_count"] == 1
    assert build_required_decision_scope_consistency(
        _agent_summary(), {}, agent_id=AGENT_ID, standing_decision_authority=authority,
    )["standing_authority_match_count"] == 1


def _agent_summary() -> dict:
    return {
        "first_open_items": [
            {
                "todo_id": "todo_agent_delivery",
                "status": "open",
                "task_class": "advancement_task",
                "claimed_by": AGENT_ID,
                "required_decision_scopes": [SCOPE],
            }
        ]
    }


def _user_summary(*items: dict) -> dict:
    return {
        "first_open_items": list(items),
        "backlog_items": list(items),
    }


@pytest.mark.parametrize(
    "gate",
    [
        {
            "todo_id": "todo_current_gate",
            "status": "open",
            "task_class": "user_gate",
            "blocks_agent": AGENT_ID,
            "decision_scope": SCOPE,
        },
        {
            "todo_id": "todo_global_gate",
            "status": "open",
            "task_class": "user_gate",
            "global_gate": True,
            "decision_scope": SCOPE,
        },
    ],
)
def test_required_scope_resolves_only_to_compatible_blocking_gate(gate: dict) -> None:
    result = build_required_decision_scope_consistency(
        _agent_summary(),
        _user_summary(gate),
        agent_id=AGENT_ID,
    )

    assert result["ok"] is True
    assert result["checked_required_scope_count"] == 1
    assert result["errors"] == []


@pytest.mark.parametrize(
    ("user_item", "reason_code"),
    [
        (
            {
                "todo_id": "todo_nonblocking_action",
                "status": "open",
                "task_class": "user_action",
                "decision_scope": SCOPE,
            },
            "non_blocking_user_action_scope_collision",
        ),
        (
            {
                "todo_id": "todo_other_agent_gate",
                "status": "open",
                "task_class": "user_gate",
                "blocks_agent": "codex-other-agent",
                "decision_scope": SCOPE,
            },
            "required_decision_scope_gate_owner_mismatch",
        ),
        (None, "dangling_required_decision_scope"),
    ],
)
def test_invalid_required_scope_projects_bounded_repair(
    user_item: dict | None,
    reason_code: str,
) -> None:
    result = build_required_decision_scope_consistency(
        _agent_summary(),
        _user_summary(*([user_item] if user_item else [])),
        agent_id=AGENT_ID,
    )
    repair = build_required_decision_scope_repair_hint(result)

    assert result["ok"] is False
    assert result["errors"][0]["reason_code"] == reason_code
    assert repair is not None
    assert repair["effective_action"] == "todo_decision_scope_projection_repair"
    assert repair["allowed"] is True


def test_unrelated_gate_has_no_authority_over_independent_agent_todo() -> None:
    agent_summary = _agent_summary()
    agent_summary["first_open_items"][0]["required_decision_scopes"] = []
    unrelated_gate = {
        "todo_id": "todo_other_agent_gate",
        "status": "open",
        "task_class": "user_gate",
        "blocks_agent": "codex-other-agent",
        "decision_scope": SCOPE,
    }

    result = build_required_decision_scope_consistency(
        agent_summary,
        _user_summary(unrelated_gate),
        agent_id=AGENT_ID,
    )

    assert result["ok"] is True
    assert result["checked_required_scope_count"] == 0


def test_multi_agent_unscoped_user_gate_projects_bounded_repair() -> None:
    unscoped_gate = {
        "todo_id": "todo_ambiguous_gate",
        "status": "open",
        "task_class": "user_gate",
    }

    result = build_required_decision_scope_consistency(
        {"first_open_items": []},
        _user_summary(unscoped_gate),
        agent_id=AGENT_ID,
        registered_agent_ids=[AGENT_ID, "codex-other-agent"],
    )
    repair = build_required_decision_scope_repair_hint(result)

    assert result["ok"] is False
    assert result["errors"] == [
        {
            "reason_code": "multi_agent_user_gate_missing_scope",
            "user_todo_id": "todo_ambiguous_gate",
            "registered_agent_ids": ["codex-other-agent", AGENT_ID],
        }
    ]
    assert repair is not None
    assert repair["trigger"] == "user_gate_scope_projection_drift"
    assert repair["effective_action"] == "todo_decision_scope_projection_repair"
    assert repair["notify"] == "DONT_NOTIFY"


def test_explicit_global_gate_is_valid_in_multi_agent_projection() -> None:
    global_gate = {
        "todo_id": "todo_explicit_global_gate",
        "status": "open",
        "task_class": "user_gate",
        "global_gate": True,
    }

    result = build_required_decision_scope_consistency(
        {"first_open_items": []},
        _user_summary(global_gate),
        agent_id=AGENT_ID,
        registered_agent_ids=[AGENT_ID, "codex-other-agent"],
    )

    assert result["ok"] is True
    assert result["errors"] == []


def test_single_agent_unscoped_gate_preserves_legacy_scope() -> None:
    unscoped_gate = {
        "todo_id": "todo_single_agent_gate",
        "status": "open",
        "task_class": "user_gate",
    }

    result = build_required_decision_scope_consistency(
        {"first_open_items": []},
        _user_summary(unscoped_gate),
        agent_id=AGENT_ID,
        registered_agent_ids=[AGENT_ID],
    )

    assert result["ok"] is True
    assert result["errors"] == []


def test_unscoped_diagnostic_uses_claimed_agent_as_effective_owner() -> None:
    other_agent_gate = {
        "todo_id": "todo_other_agent_gate",
        "status": "open",
        "task_class": "user_gate",
        "blocks_agent": "codex-other-agent",
        "decision_scope": SCOPE,
    }

    result = build_required_decision_scope_consistency(
        _agent_summary(),
        _user_summary(other_agent_gate),
        agent_id=None,
    )

    assert result["ok"] is False
    assert (
        result["errors"][0]["reason_code"]
        == "required_decision_scope_gate_owner_mismatch"
    )


def test_complete_source_items_take_precedence_over_hot_path_summary() -> None:
    hidden_agent_item = {
        "todo_id": "todo_hidden_scope",
        "status": "open",
        "task_class": "advancement_task",
        "claimed_by": AGENT_ID,
        "required_decision_scopes": [SCOPE],
    }

    result = build_required_decision_scope_consistency(
        {"first_open_items": []},
        {"first_open_items": []},
        agent_id=AGENT_ID,
        agent_source_items=[hidden_agent_item],
        user_source_items=[],
    )

    assert result["ok"] is False
    assert result["checked_agent_todo_count"] == 1
    assert result["checked_required_scope_count"] == 1
    assert result["errors"][0]["reason_code"] == "dangling_required_decision_scope"


def test_standing_approval_satisfies_matching_agent_scope() -> None:
    standing_scope = {
        "schema_version": "decision_scope_v0",
        "kind": "write_scope",
        "granularity": "goal",
        "scope_key": "benchmark_pr_self_merge",
    }
    authority = project_standing_authority(
        [
            {
                "todo_id": "todo_standing_approval",
                "status": "done",
                "done": True,
                "task_class": "user_gate",
                "blocks_agent": AGENT_ID,
                "decision_scope": standing_scope,
                "decision_outcome": "approve",
            }
        ]
    )
    agent_item = {
        "todo_id": "todo_benchmark_pr",
        "status": "open",
        "task_class": "advancement_task",
        "claimed_by": AGENT_ID,
        "required_decision_scopes": [standing_scope],
    }

    result = build_required_decision_scope_consistency(
        None,
        None,
        agent_id=AGENT_ID,
        registered_agent_ids=[AGENT_ID, "codex-other-agent"],
        agent_source_items=[agent_item],
        user_source_items=[],
        standing_decision_authority=authority,
    )

    assert result["ok"] is True
    assert result["standing_authority_match_count"] == 1
    assert standing_decision_authority_for_agent(
        authority,
        agent_id="codex-other-agent",
    ) is None


def test_latest_rejection_revokes_exact_standing_approval() -> None:
    base = {
        "status": "done",
        "done": True,
        "task_class": "user_gate",
        "blocks_agent": AGENT_ID,
        "decision_scope": "write_scope:goal:benchmark_pr_self_merge",
    }
    authority = project_standing_authority(
        [
            {
                **base,
                "todo_id": "todo_standing_approval",
                "decision_outcome": "approve",
            },
            {
                **base,
                "todo_id": "todo_standing_rejection",
                "decision_outcome": "reject",
            },
        ]
    )

    assert authority is not None
    assert authority["active_count"] == 0
    assert authority["inactive_count"] == 1
    assert authority["entries"][0]["source_todo_id"] == "todo_standing_rejection"
    assert authority["entries"][0]["active"] is False


@pytest.mark.parametrize(
    "invalid_item",
    [
        {
            "todo_id": "todo_user_action",
            "status": "done",
            "task_class": "user_action",
            "blocks_agent": AGENT_ID,
            "decision_scope": "write_scope:goal:benchmark_pr_self_merge",
            "decision_outcome": "approve",
        },
        {
            "todo_id": "todo_one_shot_gate",
            "status": "done",
            "task_class": "user_gate",
            "blocks_agent": AGENT_ID,
            "decision_scope": "write_scope:goal:benchmark_pr_self_merge",
            "decision_outcome": "approve",
            "unblocks_todo_id": "todo_benchmark_pr",
        },
        {
            "todo_id": "todo_deferred_gate",
            "status": "deferred",
            "task_class": "user_gate",
            "blocks_agent": AGENT_ID,
            "decision_scope": "write_scope:goal:benchmark_pr_self_merge",
            "decision_outcome": "approve",
        },
    ],
)
def test_non_authoritative_completed_items_do_not_become_standing(
    invalid_item: dict,
) -> None:
    assert project_standing_authority([invalid_item]) is None


@pytest.mark.parametrize("decision_outcome", ["reject", "cancel"])
def test_terminal_non_approval_is_a_valid_blocked_scope_state(
    decision_outcome: str,
) -> None:
    agent_summary = _agent_summary()
    agent_item = agent_summary["first_open_items"][0]
    agent_item["status"] = "blocked"
    agent_item["decision_scope_outcomes"] = [
        {
            "outcome": decision_outcome,
            "decision_scope": SCOPE,
            "source_todo_id": "todo_terminal_gate",
        }
    ]

    result = build_required_decision_scope_consistency(
        agent_summary,
        _user_summary(),
        agent_id=AGENT_ID,
    )

    assert result["ok"] is True
    assert result["terminal_outcome_count"] == 1
    assert result["errors"] == []


def test_terminal_non_approval_cannot_leave_target_executable() -> None:
    agent_summary = _agent_summary()
    agent_summary["first_open_items"][0]["decision_scope_outcomes"] = [
        {
            "outcome": "reject",
            "decision_scope": SCOPE,
            "source_todo_id": "todo_terminal_gate",
        }
    ]

    result = build_required_decision_scope_consistency(
        agent_summary,
        _user_summary(),
        agent_id=AGENT_ID,
    )

    assert result["ok"] is False
    assert result["errors"] == [
        {
            "reason_code": "terminal_decision_outcome_target_not_blocked",
            "agent_todo_id": "todo_agent_delivery",
            "required_scope": "direction:action:publish_quality_contract",
            "related_user_todo_ids": ["todo_terminal_gate"],
        }
    ]


def test_quota_checks_scope_item_beyond_hot_path_backlog_limit() -> None:
    items = [
        quota_todo_item(
            todo_id=f"todo_unclaimed_{index}",
            index=index,
            text=f"[P1] Independent item {index}.",
            action_kind=f"independent_{index}",
            required_decision_scopes=[SCOPE] if index == 9 else [],
        )
        for index in range(1, 10)
    ]
    status = quota_status_payload(
        goal_id="scope-beyond-hot-path",
        status="active",
        recommended_action="Run the first independent item.",
        agent_todos=quota_todo_summary(items, role="agent"),
        user_todos=quota_todo_summary([], role="user"),
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT_ID]},
    )

    decision = build_quota_should_run(
        status,
        goal_id="scope-beyond-hot-path",
        agent_id=AGENT_ID,
    )

    assert decision["decision"] == "self_repair"
    assert decision["effective_action"] == "todo_decision_scope_projection_repair"
    assert decision["todo_decision_scope_consistency"]["errors"] == [
        {
            "reason_code": "dangling_required_decision_scope",
            "agent_todo_id": "todo_unclaimed_9",
            "required_scope": "direction:action:publish_quality_contract",
            "related_user_todo_ids": [],
        }
    ]
