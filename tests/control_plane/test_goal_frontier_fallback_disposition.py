from __future__ import annotations

from typing import Any

import pytest

from loopx.control_plane.goals.goal_frontier import (
    VISION_FRONTIER_TODO_DELTA_ACTIONS,
    agent_scoped_selectable_advancement_todo_ids,
    build_goal_frontier_projection_context_from_status,
)
from loopx.control_plane.goals.goal_vision import (
    compact_goal_vision_packet,
    normalize_goal_vision_packet,
)
from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
    quota_todo_summary,
)
from loopx.control_plane.todos.projection import todo_advancement_frontier_counts
from loopx.control_plane.todos.summary_item import todo_planning_source_items
from loopx.quota import build_quota_should_run

GOAL_ID = "vision-fallback-disposition-fixture"
AGENT_ID = "codex-fallback-agent"
PRIMARY_AGENT = "codex-primary-agent"
PREREQ_ID = "todo_primary_prereq"
PRIMARY_WAIT_ID = "todo_primary_successor"
FALLBACK_ID = "todo_declared_fallback"
DECLARED_FALLBACK_ACCEPTANCE = (
    "Deliver the primary successor; if the primary stays blocked, "
    "deliver the declared fallback direction instead."
)


@pytest.mark.parametrize("resolution", ["waiting", "runnable"])
@pytest.mark.parametrize("reverse", [False, True])
def test_one_resolved_alternative_discharges_only_its_declaration(
    resolution: str, reverse: bool,
) -> None:
    from loopx.control_plane.goals.goal_frontier.fallback_disposition import (
        declared_fallback_gap_from_agent_vision,
    )

    ready = quota_todo_item(todo_id="todo_ready", index=1, text="Deliver alternative")
    source = [ready]
    if resolution == "waiting":
        ready.update(status="deferred", resume_when="todo_done:todo_dependency")
        source.append(quota_todo_item(todo_id="todo_dependency", index=2, text="Dependency"))
    else:
        source.append(quota_todo_item(
            todo_id="todo_uncertain", index=2, text="Other alternative",
            status="deferred", resume_when="capacity_available:delivery",
        ))
    alternatives = ["todo_missing" if resolution == "waiting" else "todo_uncertain", "todo_ready"]
    if reverse:
        alternatives.reverse()
    vision = {"state": "vision_drift_detected", "fallback_declarations": [{
        "declaration_id": "direction", "target_todo_id": alternatives[0],
        "successor_todo_id": alternatives[1],
    }]}
    assert declared_fallback_gap_from_agent_vision(
        vision, agent_todo_summary={"current_agent_blocker_items": [{}]},
        agent_id=AGENT_ID, agent_todo_source_items=source,
    ) is None


def test_loaded_writeback_source_is_bounded_before_effect_transport(monkeypatch) -> None:
    from loopx.control_plane.goals.goal_frontier import fallback_disposition as module

    source = [quota_todo_item(todo_id=f"todo_unrelated_{index}", index=index + 1,
                             text="Unrelated work") for index in range(4096)]
    source.append(quota_todo_item(todo_id=FALLBACK_ID, index=4097, text="Fallback"))
    original = module.effect_runtime_result
    counts = []

    def capture(method, request):
        counts.append(len(request["items"]))
        return original(method, request)

    monkeypatch.setattr(module, "effect_runtime_result", capture)
    assert module.declared_fallback_gap_from_agent_vision(
        {"fallback_declarations": [{"declaration_id": "direction", "target_todo_id": FALLBACK_ID}]},
        agent_todo_summary={"current_agent_blocker_items": [{}]},
        agent_id=AGENT_ID, agent_todo_source_items=source,
    ) is None
    assert counts == [1]


def _fallback_vision_run(
    *,
    state: str = "vision_drift_detected",
    todo_delta: list[str] | None = None,
    acceptance_summary: str = DECLARED_FALLBACK_ACCEPTANCE,
    path_outcome: str | None = None,
    fallback_declarations: list[Any] | None = None,
) -> dict:
    """Persist a caller packet through the production write/readback chain.

    The caller packet goes through the real TS ``goal.vision_checkpoint``
    prepare (the executor entry) and the compact read-model projection (the
    status/shared-runtime entry) before it becomes a run-history record, so
    the tests can only pass when the typed declaration survives the same
    chain a real caller uses.
    """

    packet: dict = {
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "state": state,
        "todo_delta": todo_delta
        if todo_delta is not None
        else [f"retain:{PRIMARY_WAIT_ID}"],
        "vision_patch": {
            "acceptance_summary": acceptance_summary,
            "replan_trigger_summary": "The primary acceptance remains open.",
            "advancement_policy": "repeat_until_closed",
        },
    }
    if fallback_declarations is not None:
        packet["fallback_declarations"] = fallback_declarations
    elif acceptance_summary == DECLARED_FALLBACK_ACCEPTANCE:
        packet["fallback_declarations"] = [
            {
                "declaration_id": "declared_fallback_direction",
                "target_todo_id": FALLBACK_ID,
                "successor_todo_id": FALLBACK_ID,
            }
        ]
    if path_outcome is not None:
        packet["path_delta"] = {
            "outcome": path_outcome,
            "prior_assumption": "The primary route stays viable after the "
            "prerequisite clears.",
            "observed_reality": "The declared fallback direction remains the "
            "bounded alternative path.",
            "stopped": ["Continue only the primary route."],
        }
    prepared = normalize_goal_vision_packet(packet, goal_id=GOAL_ID, agent_id=AGENT_ID)
    compact = compact_goal_vision_packet(prepared)
    assert compact is not None
    return {
        "classification": "vision_fallback_disposition_fixture",
        "generated_at": "2026-09-05T00:00:00+00:00",
        "agent_id": AGENT_ID,
        "progress_scope": "agent_lane",
        "agent_vision": compact,
    }


def _agent_todos(*, fallback_runnable: bool) -> dict:
    prereq = quota_todo_item(
        todo_id=PREREQ_ID,
        index=1,
        text="[P0] Complete the primary prerequisite.",
        claimed_by=PRIMARY_AGENT,
    )
    waiting = quota_todo_item(
        todo_id=PRIMARY_WAIT_ID,
        index=2,
        text="[P0] Resume the primary successor.",
        status="deferred",
        claimed_by=AGENT_ID,
        resume_when=f"todo_done:{PREREQ_ID}",
    )
    items = [prereq, waiting]
    if fallback_runnable:
        items.append(
            quota_todo_item(
                todo_id=FALLBACK_ID,
                index=3,
                text="[P1] Deliver the declared fallback direction.",
                claimed_by=AGENT_ID,
            )
        )
    return quota_todo_summary(items, role="agent")


def _status_payload(
    *,
    fallback_runnable: bool,
    latest_runs: list[dict],
) -> dict:
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Resolve the declared fallback direction.",
        agent_todos=_agent_todos(fallback_runnable=fallback_runnable),
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [PRIMARY_AGENT, AGENT_ID],
        },
        latest_runs=latest_runs,
    )


def _frontier_projection(payload: dict, *, include_source: bool = True) -> dict:
    item = payload["attention_queue"]["items"][0]
    source_items = (
        todo_planning_source_items(item["agent_todos"]) if include_source else None
    )
    context = build_goal_frontier_projection_context_from_status(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        status_payload=payload,
        item=item,
        project_asset=item["project_asset"],
        user_todo_summary=item["user_todos"],
        agent_todo_summary=item["agent_todos"],
        agent_todo_source_items=source_items,
        fallback_todo_source_items=source_items,
        work_lane_contract=None,
        neutral_replan_ack_classifications=set(),
        registered_agent_ids=[PRIMARY_AGENT, AGENT_ID],
        goal_status="active",
    )
    return context["goal_frontier_projection"]


def test_blocked_primary_with_runnable_fallback_projects_todo_selectable() -> None:
    payload = _status_payload(
        fallback_runnable=True,
        latest_runs=[_fallback_vision_run(todo_delta=[f"retain:{FALLBACK_ID}"])],
    )

    frontier = _frontier_projection(payload)
    assert "fallback_gaps" not in frontier
    assert "vision_wait_state" not in frontier
    remaining = frontier["remaining_advancement_frontier"]
    assert remaining["current_agent_claimed_advancement_count"] == 1
    assert remaining["unclaimed_advancement_count"] == 0

    decision = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT),
    )
    assert decision["decision"] == "run"
    assert decision["should_run"] is True
    assert decision["selected_todo"]["todo_id"] == FALLBACK_ID
    assert "fallback_gaps" not in decision["goal_frontier_projection"]


def test_declared_fallback_survives_prepare_compact_and_readback() -> None:
    # The typed declaration must survive the real TS prepare, the compact
    # read model, and the history readback before any gap can be projected.
    run = _fallback_vision_run(
        todo_delta=[f"retain:{PRIMARY_WAIT_ID}", f"retain:{FALLBACK_ID}"]
    )
    vision = run["agent_vision"]

    assert vision["fallback_declarations"] == [
        {
            "declaration_id": "declared_fallback_direction",
            "target_todo_id": FALLBACK_ID,
            "successor_todo_id": FALLBACK_ID,
        }
    ]
    from loopx.control_plane.goals.goal_frontier.semantic_history import (
        latest_agent_vision_from_runs,
    )

    readback = latest_agent_vision_from_runs([run], goal_id=GOAL_ID, agent_id=AGENT_ID)
    assert readback is not None
    assert readback["fallback_declarations"] == vision["fallback_declarations"]


def test_uncovered_causal_todo_requires_replan_independently_of_fallback_advice() -> None:
    # The structured declaration links the fallback direction to a Todo id,
    # but no runnable Todo with that id exists on this agent's frontier.
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[
            _fallback_vision_run(
                todo_delta=[f"retain:{PRIMARY_WAIT_ID}", f"retain:{FALLBACK_ID}"]
            )
        ],
    )

    frontier = _frontier_projection(payload)

    # The ordinary acceptance links both routes. A wait for the primary alone
    # cannot cover the other causal Todo; the fallback field stays advisory.
    assert [gap["kind"] for gap in frontier["acceptance_gaps"]] == ["vision_acceptance_gap"]
    assert "vision_wait_state" not in frontier
    gaps = frontier["fallback_gaps"]
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["kind"] == "vision_fallback_unresolved"
    assert gap["reason_code"] == "declared_fallback_without_runnable_or_terminal"
    assert gap["agent_id"] == AGENT_ID
    assert gap["unresolved_todo_ids"] == [FALLBACK_ID]
    assert "fallback" in gap["recommended_action"]
    assert "do not invent a user gate" in gap["recommended_action"]
    assert frontier["replan_required"] is True

    decision = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=(GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT),
    )
    quota_projection = decision["goal_frontier_projection"]
    gap = quota_projection["fallback_gaps"][0]
    assert gap["kind"] == "vision_fallback_lookup_uncertain"
    assert gap["lookup_uncertain_todo_ids"] == [FALLBACK_ID]

    authoritative_decision = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        authoritative_fallback_todo_items=[],
        scheduler_execution_context=(GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT),
    )
    authoritative_gap = authoritative_decision["goal_frontier_projection"][
        "fallback_gaps"
    ][0]
    assert authoritative_gap["kind"] == "vision_fallback_unresolved"
    assert authoritative_gap["unresolved_todo_ids"] == [FALLBACK_ID]


def test_missing_authoritative_source_projects_uncertainty_not_absence() -> None:
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[_fallback_vision_run(todo_delta=[f"retain:{FALLBACK_ID}"])],
    )

    frontier = _frontier_projection(payload, include_source=False)

    gap = frontier["fallback_gaps"][0]
    assert gap["kind"] == "vision_fallback_lookup_uncertain"
    assert gap["reason_code"] == (
        "declared_fallback_authoritative_lookup_unavailable"
    )
    assert gap["lookup_uncertain_todo_ids"] == [FALLBACK_ID]
    assert "unresolved_todo_ids" not in gap


def test_legacy_omitted_source_retains_positive_display_evidence() -> None:
    payload = _status_payload(
        fallback_runnable=True,
        latest_runs=[_fallback_vision_run()],
    )

    assert "fallback_gaps" not in _frontier_projection(payload, include_source=False)


def test_retaining_only_the_blocked_primary_successor_is_no_declaration() -> None:
    # The primary successor is the wait state's own object; retaining it does
    # not declare a fallback direction, so no gap is invented.
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[
            _fallback_vision_run(
                todo_delta=[f"retain:{PRIMARY_WAIT_ID}"],
                fallback_declarations=[],
            )
        ],
    )

    frontier = _frontier_projection(payload)
    assert "fallback_gaps" not in frontier


@pytest.mark.parametrize(
    "acceptance_summary",
    [
        # Owner probe 1: explicit negation must not project a fallback gap.
        "No fallback is authorized; wait for the primary prerequisite.",
        # Owner probe 2: a non-English prose declaration has no structured
        # declaration channel, so the conservative projection yields no gap.
        "主路径阻塞时，执行已声明的备用方案。",
        # English prose mentioning a fallback is equally non-declarative.
        "Deliver the primary successor after its prerequisite clears; "
        "the fallback wording lives in prose only.",
    ],
    ids=["negated-english", "chinese-prose", "english-prose"],
)
def test_prose_text_alone_never_declares_a_fallback(
    acceptance_summary: str,
) -> None:
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[
            _fallback_vision_run(
                acceptance_summary=acceptance_summary,
                todo_delta=[f"retain:{PRIMARY_WAIT_ID}"],
                fallback_declarations=[],
            )
        ],
    )

    frontier = _frontier_projection(payload)

    assert "fallback_gaps" not in frontier


def test_other_agent_primary_todo_without_fallback_declaration_generates_no_gap() -> (
    None
):
    # Maintainer blocker 2: generic primary-path retain (e.g. peer-held prerequisite)
    # is not a fallback declaration; without a structured declaration, no gap is invented.
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[
            _fallback_vision_run(
                todo_delta=[f"retain:{PRIMARY_WAIT_ID}", f"retain:{PREREQ_ID}"],
                fallback_declarations=[],
            )
        ],
    )

    frontier = _frontier_projection(payload)

    assert "fallback_gaps" not in frontier


def test_other_agent_primary_todo_does_not_resolve_the_gap() -> None:
    # A peer-claimed prerequisite retained on the primary path is not on this
    # agent's selectable frontier and is not linked to the declared fallback,
    # so the declared fallback gap survives for the declared fallback Todo.
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[
            _fallback_vision_run(
                todo_delta=[f"retain:{PRIMARY_WAIT_ID}", f"retain:{PREREQ_ID}"],
                fallback_declarations=[
                    {
                        "declaration_id": "declared_fallback_direction",
                        "target_todo_id": FALLBACK_ID,
                        "successor_todo_id": FALLBACK_ID,
                    }
                ],
            )
        ],
    )

    frontier = _frontier_projection(payload)

    assert frontier["acceptance_gaps"] == []
    gaps = frontier["fallback_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["unresolved_todo_ids"] == [FALLBACK_ID]


@pytest.mark.parametrize(
    "ownership_metadata",
    [
        {"claimed_by": PRIMARY_AGENT},
        {"excluded_agents": [AGENT_ID]},
    ],
    ids=["peer-claimed", "current-agent-excluded"],
)
def test_authoritative_fallback_keeps_existing_ownership_and_exclusion_semantics(
    ownership_metadata: dict[str, Any],
) -> None:
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[_fallback_vision_run(todo_delta=[f"retain:{FALLBACK_ID}"])],
    )
    fallback = quota_todo_item(
        todo_id=FALLBACK_ID,
        index=3,
        text="[P1] Deliver the declared fallback direction.",
        **ownership_metadata,
    )

    decision = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        authoritative_fallback_todo_items=[fallback],
        scheduler_execution_context=(GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT),
    )

    gap = decision["goal_frontier_projection"]["fallback_gaps"][0]
    assert gap["kind"] == "vision_fallback_unresolved"
    assert gap["unresolved_todo_ids"] == [FALLBACK_ID]


def test_declared_fallback_linked_to_monitor_todo_keeps_the_gap() -> None:
    # A linked Todo that is not advancement work is not a runnable fallback
    # successor, so the declaration stays unresolved.
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[_fallback_vision_run(todo_delta=[f"retain:{FALLBACK_ID}"])],
    )
    item = payload["attention_queue"]["items"][0]
    monitor = quota_todo_item(
        todo_id=FALLBACK_ID,
        index=3,
        text="[P2] Watch the declared fallback direction.",
        claimed_by=AGENT_ID,
    )
    monitor["task_class"] = "continuous_monitor"
    summary = _agent_todos(fallback_runnable=False)
    for slot in ("executable_backlog_items", "backlog_items"):
        summary[slot] = list(summary.get(slot) or []) + [monitor]
    summary["monitor_open_items"] = list(summary.get("monitor_open_items") or []) + [
        monitor
    ]
    item["agent_todos"] = summary

    frontier = _frontier_projection(payload)

    gaps = frontier["fallback_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["unresolved_todo_ids"] == [FALLBACK_ID]


@pytest.mark.parametrize(
    (
        "resume_monitor_generation",
        "monitor_generation",
        "monitor_status",
        "expected_gap_kind",
    ),
    [
        (3, 3, "open", None),
        (None, 3, "open", "vision_fallback_unresolved"),
        (3, 3, "done", "vision_fallback_unresolved"),
        (3, 4, "done", None),
    ],
    ids=[
        "valid-pending-monitor-wait",
        "invalid-missing-generation-baseline",
        "completed-monitor-cannot-remain-pending",
        "changed-generation-remains-runnable-after-monitor-completes",
    ],
)
def test_fallback_monitor_wait_uses_typed_resume_evaluator(
    resume_monitor_generation: int | None,
    monitor_generation: int,
    monitor_status: str,
    expected_gap_kind: str | None,
) -> None:
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[_fallback_vision_run(todo_delta=[f"retain:{FALLBACK_ID}"])],
    )
    fallback = quota_todo_item(
        todo_id=FALLBACK_ID,
        index=3,
        text="[P1] Resume the declared fallback after observation changes.",
        claimed_by=AGENT_ID,
        resume_when="monitor_changed:todo_fallback_monitor",
        resume_monitor_generation=resume_monitor_generation,
    )
    monitor = quota_todo_item(
        todo_id="todo_fallback_monitor",
        index=4,
        text="[P2] Observe the fallback dependency.",
        task_class="continuous_monitor",
        claimed_by=AGENT_ID,
        status=monitor_status,
        material_change_generation=monitor_generation,
    )

    decision = build_quota_should_run(
        payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        authoritative_fallback_todo_items=[fallback, monitor],
        scheduler_execution_context=(GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT),
    )

    gaps = decision["goal_frontier_projection"].get("fallback_gaps", [])
    if expected_gap_kind is None:
        assert gaps == []
    else:
        assert gaps[0]["kind"] == expected_gap_kind
        assert gaps[0]["unresolved_todo_ids"] == [FALLBACK_ID]


@pytest.mark.parametrize(
    ("state", "path_outcome"),
    [
        ("no_followup", None),
        ("vision_drift_detected", "stop"),
    ],
    ids=["closed-state", "terminal-path-outcome"],
)
def test_terminal_disposition_closes_fallback_gap_without_regenerating(
    state: str,
    path_outcome: str | None,
) -> None:
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[_fallback_vision_run(state=state, path_outcome=path_outcome)],
    )

    first = _frontier_projection(payload)
    assert "fallback_gaps" not in first
    assert first["acceptance_gaps"] == []

    second = _frontier_projection(payload)
    assert "fallback_gaps" not in second
    assert second["acceptance_gaps"] == []


def test_declared_bounded_successor_delta_resolves_the_gap() -> None:
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[_fallback_vision_run(todo_delta=[f"create:{FALLBACK_ID}"])],
    )

    frontier = _frontier_projection(payload)

    assert "fallback_gaps" not in frontier


def test_unrelated_create_does_not_resolve_fallback_gap() -> None:
    # Maintainer blocker 1: An unrelated create/reopen action must not
    # resolve the declared fallback direction.
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[
            _fallback_vision_run(
                todo_delta=[
                    f"retain:{PRIMARY_WAIT_ID}",
                    "create:todo_unrelated_maintenance",
                ],
                fallback_declarations=[
                    {
                        "declaration_id": "fallback_direction",
                        "target_todo_id": FALLBACK_ID,
                        "successor_todo_id": FALLBACK_ID,
                    }
                ],
            )
        ],
    )

    frontier = _frontier_projection(payload)

    gaps = frontier["fallback_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["unresolved_todo_ids"] == [FALLBACK_ID]


def test_typed_declaration_successor_relation_resolves_gap() -> None:
    # A typed declaration-to-successor relation resolves when its declared
    # bounded successor is created in todo_delta.
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[
            _fallback_vision_run(
                todo_delta=[f"retain:{PRIMARY_WAIT_ID}", "create:todo_typed_successor"],
                fallback_declarations=[
                    {
                        "declaration_id": "fallback_direction",
                        "successor_todo_id": "todo_typed_successor",
                    }
                ],
            )
        ],
    )

    frontier = _frontier_projection(payload)

    assert "fallback_gaps" not in frontier


def test_legacy_alias_shapes_do_not_declare_a_fallback() -> None:
    # Unsupported alias shapes (string arrows, patch-level fields, renamed
    # keys) have no production author; the reader must not resurrect them.
    run = _fallback_vision_run(
        todo_delta=[f"retain:{PRIMARY_WAIT_ID}"],
        fallback_declarations=[],
    )
    vision = dict(run["agent_vision"])
    vision["fallback_relationships"] = [
        {"fallback_id": FALLBACK_ID, "successor_id": FALLBACK_ID}
    ]
    vision["vision_patch"] = {
        **vision["vision_patch"],
        "fallback_declarations": [f"{FALLBACK_ID}->{FALLBACK_ID}"],
    }
    payload = _status_payload(
        fallback_runnable=False,
        latest_runs=[{**run, "agent_vision": vision}],
    )

    frontier = _frontier_projection(payload)

    assert "fallback_gaps" not in frontier


def test_compact_read_model_mirrors_the_bounded_declaration_contract() -> None:
    # Compaction is the defensive mirror of the TS prepare contract: at most
    # four entries, one per unique declaration_id, typed fields only, and
    # anything past the bound is truncated exactly like the write contract
    # rejects it.
    compact = compact_goal_vision_packet(
        {
            "schema_version": "goal_vision_replan_contract_v0",
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "state": "vision_drift_detected",
            "vision_patch": {"vision_summary": "Bounded route."},
            "fallback_declarations": [
                {
                    "declaration_id": "first_direction",
                    "target_todo_id": FALLBACK_ID,
                    "legacy_field": "dropped",
                },
                {"declaration_id": "first_direction", "target_todo_id": "todo_dup"},
                {"target_todo_id": "todo_missing_id"},
                "not-an-object",
                {"declaration_id": "truncated_direction"},
            ],
        }
    )

    assert compact is not None
    assert compact["fallback_declarations"] == [
        {"declaration_id": "first_direction", "target_todo_id": FALLBACK_ID},
    ]


def test_selectable_frontier_ids_mirror_the_authoritative_counts() -> None:
    # The completion-evidence id set must be the same agent-scoped frontier
    # the authoritative advancement counter projects.
    summary = _agent_todos(fallback_runnable=True)
    peer_only = quota_todo_item(
        todo_id="todo_peer_owned_direction",
        index=4,
        text="[P1] Advance the peer-owned direction.",
        claimed_by=PRIMARY_AGENT,
    )
    for slot in ("executable_backlog_items", "backlog_items"):
        summary[slot] = list(summary.get(slot) or []) + [peer_only]

    selectable_ids = agent_scoped_selectable_advancement_todo_ids(
        summary,
        agent_id=AGENT_ID,
    )
    counts = todo_advancement_frontier_counts(summary, agent_id=AGENT_ID)

    assert selectable_ids == {FALLBACK_ID}
    assert counts["current_agent_claimed_advancement_count"] == 1
    assert counts["unclaimed_advancement_count"] == 0
    assert counts["other_agent_claimed_advancement_count"] == 2
    assert PREREQ_ID not in selectable_ids
    assert PRIMARY_WAIT_ID not in selectable_ids


def test_vision_todo_delta_actions_contract_stays_the_shared_owner() -> None:
    # Both the acceptance-gap projection and the fallback disposition must
    # consume one action contract; create/reopen stay the successor subset.
    assert VISION_FRONTIER_TODO_DELTA_ACTIONS == frozenset(
        {"activate", "create", "reopen", "resume", "retain"}
    )
