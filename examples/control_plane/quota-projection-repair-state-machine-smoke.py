#!/usr/bin/env python3
"""Canary the quota projection-repair state machines."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.quota.projection_repair import (  # noqa: E402
    build_boundary_projection_repair_hint,
    build_state_projection_gap,
    build_state_projection_gap_repair_hint,
)


def state_gap(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "state_projection_gap_v0",
        "kind": "state_projection_gap",
        "severity": "warning",
        "requires_todo_expansion": True,
        "target_roles": sorted(
            {
                str(item.get("target_role") or "").strip()
                for item in evidence
                if str(item.get("target_role") or "").strip()
            }
        ),
        "evidence_count": len(evidence),
        "first_evidence": evidence,
    }


def agent_todo(
    *,
    todo_id: str = "todo_projection",
    required_write_scopes: list[str] | None = None,
    task_class: str = "advancement_task",
) -> dict[str, Any]:
    return {
        "todo_id": todo_id,
        "index": 1,
        "status": "open",
        "task_class": task_class,
        "action_kind": "implement",
        "text": f"[P1] Advance {todo_id}.",
        "required_write_scopes": required_write_scopes or [],
    }


def agent_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "todo_summary_v0",
        "open_count": len(items),
        "first_open_items": items,
        "first_executable_items": items,
        "items": items,
    }


def assert_state_gap_revalidation_drops_stale_user_waits() -> None:
    stale_user_only = state_gap(
        [
            {
                "kind": "next_action_waits_without_user_todo",
                "target_role": "user",
                "section": "Next Action",
                "text": "Extract the quota projection-repair helper.",
            }
        ]
    )
    assert build_state_projection_gap({"state_projection_gap": stale_user_only}, {}) is None

    retained_agent = {
        "kind": "next_action_requires_agent_todo",
        "target_role": "agent",
        "section": "Next Action",
        "text": "Extract the quota projection-repair helper.",
    }
    mixed = state_gap([stale_user_only["first_evidence"][0], retained_agent])
    revised = build_state_projection_gap({}, {"state_projection_gap": mixed})
    assert revised is not None, mixed
    assert revised["target_roles"] == ["agent"], revised
    assert revised["evidence_count"] == 1, revised
    assert revised["first_evidence"] == [retained_agent], revised


def assert_state_gap_repair_requires_empty_todo_projection_or_must_attempt() -> None:
    gap = state_gap(
        [
            {
                "kind": "next_action_requires_agent_todo",
                "target_role": "agent",
                "section": "Next Action",
                "text": "Create the missing agent todo.",
            }
        ]
    )
    repair = build_state_projection_gap_repair_hint(
        gap,
        candidate_should_run=True,
        user_todo_summary={"open_count": 0},
        agent_todo_summary={"open_count": 0},
        work_lane_contract=None,
    )
    assert repair is not None, gap
    assert repair["effective_action"] == "state_projection_gap_repair", repair
    assert build_state_projection_gap_repair_hint(
        gap,
        candidate_should_run=True,
        user_todo_summary={"open_count": 1},
        agent_todo_summary={"open_count": 0},
        work_lane_contract=None,
    ) is None
    must_attempt_repair = build_state_projection_gap_repair_hint(
        gap,
        candidate_should_run=False,
        user_todo_summary={"open_count": 0},
        agent_todo_summary={"open_count": 0},
        work_lane_contract={"must_attempt_work": True},
    )
    assert must_attempt_repair is not None, gap


def assert_boundary_repair_reports_missing_scope_and_authority_lineage() -> None:
    todo = agent_todo(required_write_scopes=["src/runtime/**"])
    repair = build_boundary_projection_repair_hint(
        {
            "write_scope": ["docs/**"],
            "checkpointed_boundary_authority": {
                "schema_version": "checkpointed_boundary_authority_v0",
                "active_count": 0,
                "inactive_count": 1,
                "active_write_scope": [],
                "entries": [
                    {
                        "decision_id": "decision-runtime-expired",
                        "source": "operator_gate_fixture",
                        "active": False,
                        "freshness": "expired",
                        "inactive_reasons": ["expired"],
                        "write_scope": ["src/**"],
                    }
                ],
            },
        },
        agent_summary([todo]),
        candidate_should_run=True,
        capability_gate={"action": "run"},
    )
    assert repair is not None, todo
    assert repair["effective_action"] == "boundary_projection_repair", repair
    assert repair["missing_write_scopes"] == ["src/runtime/**"], repair
    assert repair["allowed_write_scopes"] == ["docs/**"], repair
    assert repair["inactive_authority_candidates"][0]["decision_id"] == (
        "decision-runtime-expired"
    ), repair


def assert_boundary_repair_respects_scope_patterns_and_capability_gate() -> None:
    todo = agent_todo(required_write_scopes=["src/runtime/**"])
    assert build_boundary_projection_repair_hint(
        {"write_scope": ["src/**"]},
        agent_summary([todo]),
        candidate_should_run=True,
        capability_gate={"action": "run"},
    ) is None
    repair = build_boundary_projection_repair_hint(
        {"write_scope": ["docs/**"]},
        agent_summary([todo]),
        candidate_should_run=True,
        capability_gate={"action": "run"},
    )
    assert repair is not None, todo
    assert repair["missing_write_scopes"] == ["src/runtime/**"], repair
    assert build_boundary_projection_repair_hint(
        {"write_scope": ["docs/**"]},
        agent_summary([todo]),
        candidate_should_run=True,
        capability_gate={"action": "ask_owner"},
    ) is None

    for required, covered in [("src/b.ts", False), ("src/c.ts", True),
                              ("src/[b].ts", False), ("src/[ac].ts", True)]:
        todo = agent_todo(required_write_scopes=[required])
        repair = build_boundary_projection_repair_hint(
            {"write_scope": ["src/[z-a!b].ts"]}, agent_summary([todo]),
            candidate_should_run=True, capability_gate={"action": "run"},
        )
        assert (repair is None) is covered, (required, repair)
        if repair is not None:
            assert repair["missing_write_scopes"] == [required], repair


def assert_boundary_repair_prefers_capability_runnable_candidate() -> None:
    blocked_first = agent_todo(
        todo_id="todo_blocked",
        required_write_scopes=["blocked/**"],
    )
    runnable = agent_todo(
        todo_id="todo_runnable",
        required_write_scopes=["scripts/**"],
    )
    summary = agent_summary([blocked_first, runnable])
    repair = build_boundary_projection_repair_hint(
        {"write_scope": ["scripts/**"]},
        summary,
        candidate_should_run=True,
        capability_gate={"action": "run", "runnable_candidates": [runnable]},
    )
    assert repair is None, repair

    repair_without_gate = build_boundary_projection_repair_hint(
        {"write_scope": ["scripts/**"]},
        summary,
        candidate_should_run=True,
    )
    assert repair_without_gate is not None, summary
    assert repair_without_gate["missing_write_scopes"] == ["blocked/**"], repair_without_gate


def main() -> int:
    assert_state_gap_revalidation_drops_stale_user_waits()
    assert_state_gap_repair_requires_empty_todo_projection_or_must_attempt()
    assert_boundary_repair_reports_missing_scope_and_authority_lineage()
    assert_boundary_repair_respects_scope_patterns_and_capability_gate()
    assert_boundary_repair_prefers_capability_runnable_candidate()
    print("quota-projection-repair-state-machine-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
