#!/usr/bin/env python3
"""Smoke-test todo claim visibility lane projection helpers."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.control_plane.agents.capability_gate import (  # noqa: E402
    _agent_lane_candidate_sort_key,
)
from loopx.control_plane.todos.quota_selection import project_quota_planning  # noqa: E402
from loopx.control_plane.todos.contract import parse_todo_metadata_line  # noqa: E402
from loopx.quota import build_quota_should_run  # noqa: E402
from loopx.control_plane.testing.canary_harness import (  # noqa: E402
    import_fixture_authority, run_json_cli, run_json_cli_result, write_fixture_registry,
)
from work_lane_contract_fixtures import GOAL_ID, status_payload  # noqa: E402


CURRENT_AGENT = "codex-product-capability"
OTHER_AGENT = "codex-main-control"


def todo(
    todo_id: str,
    *,
    index: int,
    text: str,
    task_class: str,
    claimed_by: str | None = None,
    action_kind: str | None = None,
    continuation_policy: str | None = None,
    removed_continuation_policy: str | None = None,
    blocks_agent: str | None = None,
    excluded_agents: list[str] | None = None,
) -> dict:
    item = {
        "index": index,
        "todo_id": todo_id,
        "text": text,
        "status": "open",
        "task_class": task_class,
        "claimed_by": claimed_by,
        "action_kind": action_kind,
        "continuation_policy": continuation_policy,
        "removed_continuation_policy": removed_continuation_policy,
        "blocks_agent": blocks_agent,
        "excluded_agents": excluded_agents,
        "required_write_scopes": [" loopx/** ", "loopx/**"],
        "decision_scope": "direction:action:claim",
    }
    return {key: value for key, value in item.items() if value is not None}


def assert_agent_claim_scope_prefers_current_then_unclaimed() -> None:
    current_p2 = todo(
        "todo_current_p2",
        index=4,
        text="[P2] Current agent lower priority.",
        task_class="advancement_task",
        claimed_by=CURRENT_AGENT,
    )
    current_p0 = todo(
        "todo_current_p0",
        index=5,
        text="[P0] Current agent higher priority.",
        task_class="advancement_task",
        claimed_by=CURRENT_AGENT,
    )
    unclaimed_p0 = todo(
        "todo_unclaimed_p0",
        index=1,
        text="[P0] Unclaimed high priority.",
        task_class="advancement_task",
    )
    other_p0 = todo(
        "todo_other_p0",
        index=2,
        text="[P0] Other agent work.",
        task_class="advancement_task",
        claimed_by=OTHER_AGENT,
    )
    open_items = [unclaimed_p0, other_p0, current_p2, current_p0]

    planning = project_quota_planning(
        {}, all_open_items=open_items, source_open_count=len(open_items),
        agent_identity={
            "agent_id": CURRENT_AGENT,
            "agent_model": "peer_v1",
        },
        filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    selectable = planning["lanes"]["open_items"]
    claim_scope = planning["lanes"]["claim_scope"]
    assert [item["todo_id"] for item in selectable] == [
        "todo_current_p0",
        "todo_current_p2",
        "todo_unclaimed_p0",
    ], selectable
    assert claim_scope is not None, claim_scope
    assert claim_scope["schema_version"] == "agent_claim_scope_v0"
    assert claim_scope["agent_id"] == CURRENT_AGENT
    assert claim_scope["agent_model"] == "peer_v1"
    assert "primary_agent" not in claim_scope
    assert claim_scope["selection_order"] == "current_agent_claimed_then_unclaimed"
    assert claim_scope["selectable_open_count"] == 3
    assert claim_scope["current_agent_claimed_open_count"] == 2
    assert claim_scope["unclaimed_open_count"] == 1
    assert claim_scope["other_agent_claimed_open_count"] == 1
    assert claim_scope["other_agent_claimed_weight"] == "diagnostic_only"
    assert claim_scope["other_agent_claimed_items"][0]["todo_id"] == "todo_other_p0"
    assert claim_scope["blocked_claimed_items"][0]["todo_id"] == "todo_other_p0"


def assert_agent_profile_ranks_only_within_claim_buckets() -> None:
    current_avoided = todo(
        "todo_current_avoided",
        index=1,
        text="[P0] Current claimed but usually avoided.",
        task_class="advancement_task",
        action_kind="docs_cleanup",
        claimed_by=CURRENT_AGENT,
    )
    current_preferred = todo(
        "todo_current_preferred",
        index=2,
        text="[P2] Current claimed and preferred.",
        task_class="advancement_task",
        action_kind="task_lease_repair",
        claimed_by=CURRENT_AGENT,
    )
    unclaimed_preferred = todo(
        "todo_unclaimed_preferred",
        index=3,
        text="[P2] Unclaimed and preferred.",
        task_class="advancement_task",
        action_kind="todo_claim_projection",
    )
    unclaimed_neutral = todo(
        "todo_unclaimed_neutral",
        index=4,
        text="[P0] Unclaimed and neutral.",
        task_class="advancement_task",
        action_kind="runtime_cleanup",
    )
    unclaimed_avoided = todo(
        "todo_unclaimed_avoided",
        index=5,
        text="[P0] Unclaimed and avoided.",
        task_class="advancement_task",
        action_kind="docs_rewrite",
    )
    profile = {
        "schema_version": "agent_profile_v1",
        "agent_id": CURRENT_AGENT,
        "preferred_action_kinds": ["task_lease_*", "todo_claim_*"],
        "avoid_action_kinds": ["docs_*"],
    }
    identity = {
        "agent_id": CURRENT_AGENT,
        "agent_model": "peer_v1",
        "agent_profile": profile,
    }
    planning = project_quota_planning(
        {}, all_open_items=[
            unclaimed_avoided,
            unclaimed_neutral,
            current_avoided,
            unclaimed_preferred,
            current_preferred,
        ],
        agent_identity=identity,
        source_open_count=5, filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    selectable = planning["lanes"]["open_items"]
    claim_scope = planning["lanes"]["claim_scope"]
    assert [item["todo_id"] for item in selectable] == [
        "todo_current_preferred",
        "todo_current_avoided",
        "todo_unclaimed_preferred",
        "todo_unclaimed_neutral",
        "todo_unclaimed_avoided",
    ], selectable
    assert claim_scope is not None
    assert claim_scope["profile_routing"]["within_claim_bucket_only"] is True

    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": [CURRENT_AGENT, OTHER_AGENT],
        "agent_profiles": {CURRENT_AGENT: profile},
    }
    quota_items = [dict(current_avoided), dict(current_preferred)]
    for item in quota_items:
        item.pop("required_write_scopes", None)
    quota = build_quota_should_run(
        status_payload(
            status="agent_profile_advisory_routing",
            next_action=current_avoided["text"],
            coordination=coordination,
            agent_todo_items=quota_items,
        ),
        goal_id=GOAL_ID,
        agent_id=CURRENT_AGENT,
    )
    assert quota["agent_identity"]["agent_profile"] == profile, quota
    assert quota["agent_lane_next_action"]["todo_id"] == (
        "todo_current_preferred"
    ), quota
    assert quota["agent_lane_next_action"]["selected_by"] == (
        "current_agent_claimed_todo"
    ), quota
    active_avoided_key = _agent_lane_candidate_sort_key(
        current_avoided,
        agent_id=CURRENT_AGENT,
        preferred_todo_ids={"todo_current_avoided"},
        agent_profile=profile,
    )
    preferred_key = _agent_lane_candidate_sort_key(
        current_preferred,
        agent_id=CURRENT_AGENT,
        preferred_todo_ids={"todo_current_avoided"},
        agent_profile=profile,
    )
    # Profile and explicit priority buckets are resolved before active-next hints.
    assert preferred_key < active_avoided_key


def assert_executor_exclusion_filters_only_the_named_peer() -> None:
    review = todo(
        "todo_review",
        index=1,
        text="[P0] Review the peer implementation.",
        task_class="advancement_task",
        continuation_policy="independent_handoff",
        excluded_agents=[CURRENT_AGENT],
    )
    fallback = todo(
        "todo_fallback",
        index=2,
        text="[P1] Continue the current peer lane.",
        task_class="advancement_task",
    )

    planning = project_quota_planning(
        {}, all_open_items=[review, fallback], source_open_count=len([review, fallback]),
        agent_identity={"agent_id": CURRENT_AGENT, "agent_model": "peer_v1"},
        filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    blocked_selectable = planning["lanes"]["open_items"]
    blocked_scope = planning["lanes"]["claim_scope"]
    assert [item["todo_id"] for item in blocked_selectable] == ["todo_fallback"]
    assert blocked_scope is not None
    assert blocked_scope["executor_excluded_self_count"] == 1
    assert blocked_scope["executor_excluded_self_items"][0]["todo_id"] == (
        "todo_review"
    )
    assert blocked_scope["executor_exclusion_policy"] == (
        "excluded_agents_cannot_claim_or_execute"
    )

    planning = project_quota_planning(
        {}, all_open_items=[review, fallback], source_open_count=len([review, fallback]),
        agent_identity={"agent_id": OTHER_AGENT, "agent_model": "peer_v1"},
        filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    reviewer_selectable = planning["lanes"]["open_items"]
    reviewer_scope = planning["lanes"]["claim_scope"]
    assert [item["todo_id"] for item in reviewer_selectable] == [
        "todo_review",
        "todo_fallback",
    ]
    assert reviewer_scope is not None
    assert reviewer_scope["executor_excluded_self_count"] == 0

    invalid_claim = dict(review, claimed_by=CURRENT_AGENT)
    planning = project_quota_planning(
        {}, all_open_items=[invalid_claim], source_open_count=len([invalid_claim]),
        agent_identity={"agent_id": CURRENT_AGENT, "agent_model": "peer_v1"},
        filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    invalid_selectable = planning["lanes"]["open_items"]
    invalid_scope = planning["lanes"]["claim_scope"]
    assert invalid_selectable == []
    assert invalid_scope is not None
    assert invalid_scope["executor_excluded_self_count"] == 1


def assert_quota_routes_unclaimed_handoff_to_an_eligible_peer() -> None:
    review = todo(
        "todo_unclaimed_review",
        index=1,
        text="[P0] Review the peer implementation before delivery.",
        task_class="advancement_task",
        continuation_policy="independent_handoff",
        excluded_agents=[CURRENT_AGENT],
    )
    fallback = todo(
        "todo_side_fallback",
        index=2,
        text="[P1] Continue the blocked peer's independent fallback.",
        task_class="advancement_task",
        claimed_by=CURRENT_AGENT,
    )
    for item in (review, fallback):
        item["role"] = "agent"
        item["required_capabilities"] = ["shell"]
    coordination = {
        "agent_model": "peer_v1",
        "registered_agents": [OTHER_AGENT, CURRENT_AGENT],
    }

    def guard(agent_id: str) -> dict:
        return build_quota_should_run(
            status_payload(
                status="executor_exclusion_eligibility_frontier",
                next_action=review["text"],
                coordination=coordination,
                agent_todo_items=[review, fallback],
            ),
            goal_id=GOAL_ID,
            agent_id=agent_id,
        )

    blocked_guard = guard(CURRENT_AGENT)
    assert blocked_guard["agent_lane_next_action"]["todo_id"] == (
        "todo_side_fallback"
    ), blocked_guard
    assert [
        item["todo_id"]
        for item in blocked_guard["capability_gate"]["runnable_candidates"]
    ] == ["todo_side_fallback"], blocked_guard

    reviewer_guard = guard(OTHER_AGENT)
    assert reviewer_guard["agent_lane_next_action"]["todo_id"] == (
        "todo_unclaimed_review"
    ), reviewer_guard
    assert reviewer_guard["recommended_action"] == review["text"], reviewer_guard


def assert_legacy_review_handoff_fails_closed_until_repaired() -> None:
    parsed = parse_todo_metadata_line(
        "  <!-- loopx:todo todo_id=todo_legacy_review status=open "
        "task_class=advancement_task action_kind=review "
        "continuation_policy=review_handoff required_capabilities=shell -->"
    )
    assert parsed is not None
    assert parsed.get("continuation_policy") is None, parsed
    assert parsed["removed_continuation_policy"] == "review_handoff", parsed

    legacy_review = todo(
        "todo_legacy_review",
        index=1,
        text="[P0] Independently review the peer implementation.",
        task_class="advancement_task",
        removed_continuation_policy=parsed["removed_continuation_policy"],
    )
    fallback = todo(
        "todo_legacy_fallback",
        index=2,
        text="[P1] Repair the legacy review handoff metadata.",
        task_class="advancement_task",
        claimed_by=CURRENT_AGENT,
    )
    for item in (legacy_review, fallback):
        item["role"] = "agent"
        item["required_capabilities"] = ["shell"]

    planning = project_quota_planning(
        {}, all_open_items=[legacy_review, fallback], source_open_count=len([legacy_review, fallback]),
        agent_identity={"agent_id": CURRENT_AGENT, "agent_model": "peer_v1"},
        filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    selectable = planning["lanes"]["open_items"]
    claim_scope = planning["lanes"]["claim_scope"]
    assert [item["todo_id"] for item in selectable] == ["todo_legacy_fallback"]
    assert claim_scope is not None
    assert claim_scope["removed_continuation_blocked_count"] == 1, claim_scope
    assert claim_scope["removed_continuation_blocked_items"][0]["todo_id"] == (
        "todo_legacy_review"
    )

    guard = build_quota_should_run(
        status_payload(
            status="legacy_review_handoff_migration",
            next_action=legacy_review["text"],
            coordination={
                "agent_model": "peer_v1",
                "registered_agents": [OTHER_AGENT, CURRENT_AGENT],
            },
            agent_todo_items=[legacy_review, fallback],
        ),
        goal_id=GOAL_ID,
        agent_id=CURRENT_AGENT,
    )
    assert guard["agent_lane_next_action"]["todo_id"] == "todo_legacy_fallback", guard
    assert [
        item["todo_id"] for item in guard["capability_gate"]["runnable_candidates"]
    ] == ["todo_legacy_fallback"], guard

    planning = project_quota_planning(
        {}, all_open_items=[legacy_review], source_open_count=len([legacy_review]),
        agent_identity={"agent_id": OTHER_AGENT, "agent_model": "peer_v1"},
        filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    peer_selectable = planning["lanes"]["open_items"]
    peer_scope = planning["lanes"]["claim_scope"]
    assert peer_selectable == []
    assert peer_scope is not None
    assert peer_scope["removed_continuation_blocked_count"] == 1


def assert_legacy_review_handoff_write_requires_explicit_repair() -> None:
    original_lines = [
        "## Agent Todo",
        "",
        "- [ ] [P0] Independently review the peer implementation.",
        "  <!-- loopx:todo todo_id=todo_legacy_write status=open "
        "task_class=advancement_task action_kind=review "
        "continuation_policy=review_handoff -->",
    ]

    with tempfile.TemporaryDirectory(prefix="loopx-claim-repair-") as tmp:
        root = Path(tmp).resolve()
        state = root / "ACTIVE_GOAL_STATE.md"
        state.write_text("\n".join(original_lines) + "\n", encoding="utf-8")
        registry = root / ".loopx/registry.json"
        write_fixture_registry(
            project=root, runtime_root=root / "runtime", registry_path=registry,
            goal_id=GOAL_ID, domain="claim-repair-fixture",
            adapter_kind="generic_project_goal_v0", state_file=state.name,
            registered_agents=[CURRENT_AGENT, OTHER_AGENT],
        )
        import_fixture_authority(registry, GOAL_ID)
        before = state.read_bytes()
        prefix = ("todo", "update", "--goal-id", GOAL_ID,
                  "--todo-id", "todo_legacy_write", "--agent-id", OTHER_AGENT)
        revision = run_json_cli("todo", "list", "--goal-id", GOAL_ID,
                                registry_path=registry)["authority_read"]["provider_revision"]
        claim = ("todo", "claim", "--goal-id", GOAL_ID,
                 "--todo-id", "todo_legacy_write", "--agent-id", CURRENT_AGENT,
                 "--claimed-by", CURRENT_AGENT)
        for request in (claim, (*prefix, "--note", "Keep this legacy review blocked.")):
            code, rejected = run_json_cli_result(*request, registry_path=registry)
            assert code == 1, rejected
            if request is claim:
                assert rejected["error_code"] == "removed_continuation_policy", rejected
            else:
                assert "repair it" in rejected["error"], rejected
            assert state.read_bytes() == before
            assert run_json_cli("todo", "list", "--goal-id", GOAL_ID,
                                registry_path=registry)["authority_read"]["provider_revision"] == revision
        repaired = run_json_cli(*prefix, "--continuation-policy", "independent_handoff",
                                "--excluded-agent", CURRENT_AGENT, registry_path=registry)
        assert repaired["source_authority"] == "file_v0", repaired
        current = run_json_cli("todo", "list", "--goal-id", GOAL_ID,
                               "--todo-id", "todo_legacy_write", registry_path=registry)["todo"]
        assert current["continuation_policy"] == "independent_handoff", current
        assert current["excluded_agents"] == [CURRENT_AGENT], current
        assert not current.get("removed_continuation_policy"), current
        assert "continuation_policy=review_handoff" not in state.read_text()


def assert_claim_visibility_lanes_split_current_other_and_task_class() -> None:
    open_items = [
        todo(
            "todo_unclaimed_p0",
            index=1,
            text="[P0] Unclaimed high priority.",
            task_class="advancement_task",
        ),
        todo(
            "todo_current_advancement",
            index=2,
            text="[P1] Current advancement.",
            task_class="advancement_task",
            claimed_by=CURRENT_AGENT,
        ),
        todo(
            "todo_current_monitor",
            index=3,
            text="[P1 monitor] Current monitor.",
            task_class="continuous_monitor",
            claimed_by=CURRENT_AGENT,
        ),
        todo(
            "todo_other_advancement",
            index=4,
            text="[P1] Other advancement.",
            task_class="advancement_task",
            claimed_by=OTHER_AGENT,
        ),
    ]

    planning = project_quota_planning(
        {}, all_open_items=open_items, source_open_count=len(open_items),
        agent_identity={"agent_id": CURRENT_AGENT},
        filter_user_gate_blocks_agent=False, available_capabilities=None,
    )
    lanes = planning["claim_visibility"]
    assert lanes["unclaimed_priority_open_items"][0]["todo_id"] == "todo_unclaimed_p0"
    assert [item["todo_id"] for item in lanes["claimed_open_items"]] == [
        "todo_current_advancement",
        "todo_current_monitor",
        "todo_other_advancement",
    ], lanes
    assert lanes["claimed_advancement_open_count"] == 2, lanes
    assert lanes["claimed_monitor_open_count"] == 1, lanes
    assert lanes["current_agent_claimed_open_count"] == 2, lanes
    assert lanes["current_agent_claimed_advancement_count"] == 1, lanes
    assert lanes["current_agent_claimed_monitor_count"] == 1, lanes
    assert lanes["claimed_by_others_count"] == 1, lanes
    current = lanes["current_agent_claimed_advancement_items"][0]
    assert current["required_write_scopes"] == ["loopx/**"], current
    assert current["decision_scope"]["scope_key"] == "claim", current
    assert lanes["claimed_by_others_items"][0]["todo_id"] == "todo_other_advancement"


def main() -> int:
    assert_agent_claim_scope_prefers_current_then_unclaimed()
    assert_agent_profile_ranks_only_within_claim_buckets()
    assert_executor_exclusion_filters_only_the_named_peer()
    assert_quota_routes_unclaimed_handoff_to_an_eligible_peer()
    assert_legacy_review_handoff_fails_closed_until_repaired()
    assert_legacy_review_handoff_write_requires_explicit_repair()
    assert_claim_visibility_lanes_split_current_other_and_task_class()
    print("todo-claim-visibility-lanes-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
