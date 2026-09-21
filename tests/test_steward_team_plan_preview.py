"""The steward team preview validates without ever becoming an effect."""

from __future__ import annotations

import pytest

from loopx.control_plane.work_items.governed_transition_proposal import (
    _SETTLEMENT_PHASE_BY_PROPOSAL_KIND,
    GovernedTransitionSettlementPhase,
    STEWARD_TEAM_PLAN_PREVIEW_KIND,
    STEWARD_TEAM_PLAN_PREVIEW_SCHEMA_VERSION,
    validate_steward_team_plan_preview,
)


def _plan(**overrides: object) -> dict[str, object]:
    plan: dict[str, object] = {
        "schema_version": STEWARD_TEAM_PLAN_PREVIEW_SCHEMA_VERSION,
        "kind": STEWARD_TEAM_PLAN_PREVIEW_KIND,
        "goal_id": "team-plan-fixture",
        "objective": "Ship the intake lane",
        "quota_envelope": {"slots_per_day": 4},
        "stop_condition": "Stop when the owner withdraws the request",
        "lanes": [
            {
                "lane_id": "lane-alpha",
                "agent_id": "agent-alpha",
                "acceptance": "The lane's first Todo is delivered with evidence",
                "first_todo": {
                    "text": "Advance the intake contract",
                    "priority": "P1",
                    "task_class": "advancement_task",
                    "action_kind": "implement",
                },
            }
        ],
    }
    plan.update(overrides)
    return plan


def _validate(plan: object) -> dict:
    return validate_steward_team_plan_preview(
        plan,
        registered_agent_ids=["agent-alpha"],
        supported_action_kinds=["implement"],
    )


def test_a_staffed_lane_becomes_a_preview_that_cannot_apply() -> None:
    preview = _validate(_plan())

    assert preview["applies"] is False
    assert preview["goal_id"] == "team-plan-fixture"
    assert preview["gaps"] == []
    assert preview["lanes"][0]["staffing"] == "ready"
    assert preview["lanes"][0]["first_todo"]["priority"] == "P1"
    assert preview["quota_envelope"] == {"slots_per_day": 4}


def test_the_preview_must_name_the_goal_it_staffs() -> None:
    """Admission and settlement both need one named Goal's facts."""

    without_goal = _plan()
    del without_goal["goal_id"]
    with pytest.raises(ValueError, match="goal_id must be a non-empty string"):
        _validate(without_goal)

    # A Goal id is an exact registry id, not free text: a path, a sentence or an
    # unbounded string cannot become the Goal a settlement materializes into.
    for invalid in ("", "   ", "../escape", "goal with spaces", "x" * 161):
        with pytest.raises(ValueError):
            _validate(_plan(goal_id=invalid))
    with pytest.raises(ValueError, match="requires an exact Goal id"):
        _validate(_plan(goal_id="../escape"))


def test_an_unregistered_agent_becomes_a_gap_instead_of_being_invented() -> None:
    plan = _plan()
    plan["lanes"][0]["agent_id"] = "agent-not-registered"  # type: ignore[index]

    preview = _validate(plan)

    lane = preview["lanes"][0]
    assert lane["staffing"] == "gap"
    assert lane["gap_reason_code"] == "agent_not_registered"
    # The work the owner asked for is kept with the gap, never silently dropped.
    assert lane["declined_first_todo"]["text"] == "Advance the intake contract"
    assert "first_todo" not in lane
    assert preview["gaps"] == [
        {"lane_id": "lane-alpha", "reason_code": "agent_not_registered"}
    ]
    assert preview["applies"] is False


def test_a_declared_gap_keeps_its_reason_and_carries_no_work() -> None:
    plan = _plan()
    lane = plan["lanes"][0]  # type: ignore[index]
    lane.pop("first_todo")
    lane["staffing_gap"] = {
        "reason_code": "capability_not_granted",
        "note": "The reviewer capability is not granted on this machine",
    }

    preview = _validate(plan)

    assert preview["gaps"] == [
        {"lane_id": "lane-alpha", "reason_code": "capability_not_granted"}
    ]
    with pytest.raises(ValueError, match="may not declare work"):
        _validate(
            _plan(
                lanes=[
                    {
                        **lane,
                        "first_todo": _plan()["lanes"][0]["first_todo"],  # type: ignore[index]
                    }
                ]
            )
        )


def _lane(
    *,
    lane_id: str,
    action_kind: str,
    text: str = "Advance the intake contract",
    agent_id: str = "agent-alpha",
) -> dict:
    return {
        "lane_id": lane_id,
        "agent_id": agent_id,
        "acceptance": "The lane's first Todo is delivered with evidence",
        "first_todo": {
            "text": text,
            "priority": "P1",
            "task_class": "advancement_task",
            "action_kind": action_kind,
        },
    }


def test_an_unsupported_action_kind_gaps_that_lane_instead_of_refusing_the_plan() -> None:
    """The host cannot staff one lane's *kind*; the other lanes are still the plan.

    Live evidence: a steward answered a one-sentence team request correctly, and
    one lane asked for an action kind this host does not ship. The preview was
    refused whole as a malformed payload, so the owner read a complete plan with
    nothing to confirm. The *lane* is what cannot be staffed, so the lane -- not
    the plan -- becomes the typed gap, exactly as an unregistered Agent already
    does.
    """

    preview = _validate(
        _plan(
            lanes=[
                _lane(lane_id="lane-alpha", action_kind="implement"),
                _lane(
                    lane_id="lane-beta",
                    action_kind="public_smoke_quality_repair",
                    text="Repair the public smoke",
                ),
            ]
        )
    )

    assert preview["applies"] is False
    ready, gap = preview["lanes"]
    assert ready["staffing"] == "ready"
    assert ready["first_todo"]["action_kind"] == "implement"
    assert gap["staffing"] == "gap"
    assert gap["gap_reason_code"] == "action_kind_not_supported"
    assert "first_todo" not in gap
    # The work the owner asked for is kept with the gap, including the kind that
    # could not be staffed, so the next plan can be corrected instead of guessed.
    assert gap["declined_first_todo"]["text"] == "Repair the public smoke"
    assert gap["declined_first_todo"]["action_kind"] == "public_smoke_quality_repair"
    assert preview["gaps"] == [
        {"lane_id": "lane-beta", "reason_code": "action_kind_not_supported"}
    ]


def test_only_the_host_reports_the_staffability_reasons_it_owns() -> None:
    """A plan may declare its own gap, but never the host's verdict about a lane."""

    from loopx.control_plane.work_items.governed_transition_proposal import (
        STEWARD_TEAM_PLAN_GAP_REASONS,
        STEWARD_TEAM_PLAN_HOST_GAP_REASONS,
        STEWARD_TEAM_PLAN_UNSUPPORTED_ACTION_KIND,
    )

    assert STEWARD_TEAM_PLAN_UNSUPPORTED_ACTION_KIND in STEWARD_TEAM_PLAN_HOST_GAP_REASONS
    assert not set(STEWARD_TEAM_PLAN_HOST_GAP_REASONS) & set(
        STEWARD_TEAM_PLAN_GAP_REASONS
    )

    plan = _plan()
    lane = plan["lanes"][0]  # type: ignore[index]
    lane.pop("first_todo")
    lane["staffing_gap"] = {
        "reason_code": STEWARD_TEAM_PLAN_UNSUPPORTED_ACTION_KIND,
        "note": "The lane's kind is not shipped on this machine",
    }
    with pytest.raises(ValueError, match="staffing_gap reason_code is invalid"):
        _validate(plan)


def test_an_admitted_preview_validates_to_itself() -> None:
    """The confirmed payload is re-validated by the apply, so it must be stable.

    A surface stores the validated preview and the apply validates that same
    payload again with the host's own facts. If re-reading a gap lane demanded a
    ``first_todo`` it does not have -- or re-derived it from the host's *current*
    facts -- the owner's confirmed plan would be unappliable, or would staff a
    lane the preview reported as unstaffed.
    """

    declared_gap_plan = _plan()
    declared_gap_lane = declared_gap_plan["lanes"][0]  # type: ignore[index]
    declared_gap_lane.pop("first_todo")
    declared_gap_lane["staffing_gap"] = {
        "reason_code": "capability_not_granted",
        "note": "The reviewer capability is not granted on this machine",
    }

    admitted = _validate(
        _plan(
            lanes=[
                _lane(lane_id="lane-ready", action_kind="implement"),
                _lane(
                    lane_id="lane-kind",
                    action_kind="public_smoke_quality_repair",
                    text="Repair the public smoke",
                ),
                _lane(
                    lane_id="lane-agent",
                    agent_id="agent-not-registered",
                    action_kind="implement",
                    text="Work that cannot be staffed",
                ),
                declared_gap_plan["lanes"][0],  # type: ignore[index]
            ]
        )
    )

    assert [lane["staffing"] for lane in admitted["lanes"]] == [
        "ready",
        "gap",
        "gap",
        "gap",
    ]
    assert _validate(admitted) == admitted
    assert admitted["gaps"] == [
        {"lane_id": "lane-kind", "reason_code": "action_kind_not_supported"},
        {"lane_id": "lane-agent", "reason_code": "agent_not_registered"},
        {"lane_id": "lane-alpha", "reason_code": "capability_not_granted"},
    ]


def test_a_lane_without_work_must_keep_why_it_is_a_gap() -> None:
    """A gap lane with no reason and no evidence fails closed."""

    plan = _plan()
    lane = plan["lanes"][0]  # type: ignore[index]
    lane.pop("first_todo")

    with pytest.raises(ValueError, match="lane gap_reason_code is invalid"):
        _validate(plan)

    lane["gap_reason_code"] = "agent_not_registered"
    with pytest.raises(ValueError, match="must keep why it is a gap"):
        _validate(plan)

    lane["gap_reason_code"] = "action_kind_not_supported"
    lane["declined_first_todo"] = _plan()["lanes"][0]["first_todo"]  # type: ignore[index]
    assert _validate(plan)["lanes"][0]["gap_reason_code"] == (
        "action_kind_not_supported"
    )


@pytest.mark.parametrize(
    "mutation,match",
    [
        ({"kind": "something_else"}, "kind is invalid"),
        ({"schema_version": "v0"}, "schema_version is invalid"),
        ({"lanes": []}, "requires 1..8 lanes"),
        ({"stop_condition": ""}, "stop_condition must be a non-empty string"),
        ({"quota_envelope": {}}, "requires a quota envelope"),
    ],
)
def test_a_malformed_preview_fails_closed(mutation: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _validate(_plan(**mutation))


def test_the_preview_kind_is_settled_only_at_pre_settlement() -> None:
    """The kind applies through one phase of the canonical owner, and nowhere else."""

    assert _SETTLEMENT_PHASE_BY_PROPOSAL_KIND[STEWARD_TEAM_PLAN_PREVIEW_KIND] is (
        GovernedTransitionSettlementPhase.PRE_SETTLEMENT
    )


def test_the_chat_normalizer_admits_a_preview_only_with_host_facts() -> None:
    """The production caller: one malformed or unproven preview never surfaces."""

    from loopx.chat import normalize_agent_response

    envelope = {
        "message": "Here is the plan",
        "proposals": [_plan()],
        "context_handoff": None,
        "protected_action": None,
        "gate": None,
    }
    context = {
        "registered_agents_by_goal": {"team-plan-fixture": ["agent-alpha"]},
        "supported_action_kinds": ["implement"],
    }

    surfaced = normalize_agent_response(envelope, team_plan_context=context)
    proposals = surfaced["proposals"]
    assert [item["kind"] for item in proposals] == [
        "steward_team_plan_preview"
    ]
    assert proposals[0]["preview"]["applies"] is False
    assert proposals[0]["preview"]["lanes"][0]["staffing"] == "ready"

    # Without the host facts the preview cannot be validated, so it is not
    # surfaced at all rather than admitted half-checked.
    assert normalize_agent_response(envelope)["proposals"] == []

    # A plan for a Goal this Turn was not given facts for is dropped as well:
    # another Goal's Agents must not validate it.
    other_goal = {**envelope, "proposals": [_plan(goal_id="some-other-goal")]}
    assert normalize_agent_response(other_goal, team_plan_context=context)[
        "proposals"
    ] == []

    # A host with a large Goal set resolves on demand, for the named Goal only.
    looked_up: list[str] = []

    def resolve(goal_id: str) -> list[str] | None:
        looked_up.append(goal_id)
        # ``None`` means "this host cannot describe that Goal", which drops the
        # preview; an empty list is a real answer and becomes typed gaps.
        return ["agent-alpha"] if goal_id == "team-plan-fixture" else None

    on_demand_context = {
        "resolve_registered_agents": resolve,
        "supported_action_kinds": ["implement"],
    }
    on_demand = normalize_agent_response(
        envelope, team_plan_context=on_demand_context
    )
    assert [item["kind"] for item in on_demand["proposals"]] == [
        "steward_team_plan_preview"
    ]
    assert looked_up == ["team-plan-fixture"]
    assert normalize_agent_response(
        other_goal, team_plan_context=on_demand_context
    )["proposals"] == []

    unresolved = normalize_agent_response(
        envelope, team_plan_context={**on_demand_context}
    )
    assert [item["kind"] for item in unresolved["proposals"]] == [
        "steward_team_plan_preview"
    ]
    gaps_only = normalize_agent_response(
        other_goal,
        team_plan_context={
            "registered_agents_by_goal": {"some-other-goal": []},
            "supported_action_kinds": ["implement"],
        },
    )
    # A Goal the host says has no registered Agents is a fact: the lane becomes
    # a typed gap instead of the preview disappearing without explanation.
    assert gaps_only["proposals"][0]["preview"]["gaps"] == [
        {"lane_id": "lane-alpha", "reason_code": "agent_not_registered"}
    ]

    # A malformed preview is dropped like any other proposal the normalizer
    # cannot accept, and the owner's answer text still arrives.
    malformed = {**envelope, "proposals": [_plan(kind="not_a_plan")]}
    dropped = normalize_agent_response(malformed, team_plan_context=context)
    assert dropped["proposals"] == []
    assert dropped["message"] == "Here is the plan"

    # The plain Todo proposals keep their existing behaviour.
    todo = {
        **envelope,
        "proposals": [
            {"kind": "todo", "text": "Do one thing", "priority": "P2", "rationale": "why"}
        ],
    }
    assert normalize_agent_response(todo)["proposals"] == [
        {"kind": "todo", "text": "Do one thing", "priority": "P2", "rationale": "why"}
    ]


def test_the_steward_turn_resolves_admission_facts_per_goal(tmp_path) -> None:
    """The Turn owner hands the segment a lookup, not one Goal's facts."""

    import json as _json

    from loopx.capabilities.manager_context.team_plan import (
        team_plan_admission_context,
    )

    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        _json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "goals": [
                    {
                        "id": goal_id,
                        "domain": goal_id,
                        "status": "active-read-only",
                        "coordination": {"registered_agents": [agent_id]},
                    }
                    for goal_id, agent_id in (
                        ("authorized-goal", "agent-alpha"),
                        ("other-goal", "agent-beta"),
                    )
                ],
            }
        ),
        encoding="utf-8",
    )
    def context(session: dict) -> dict | None:
        return team_plan_admission_context(
            registry_path=registry_path,
            session=session,
            manager_scope_resolver=lambda _session: ["authorized-goal"],
        )

    # Only the manager channel proposes teams; every other channel is untouched.
    assert context({"channel_id": "lark:topic"}) is None

    # The owner's own channel is not scoped to a subset of Goals.
    owner_context = context({"channel_id": "manager"})
    resolve = owner_context["resolve_registered_agents"]
    assert resolve("authorized-goal") == ["agent-alpha"]
    assert resolve("other-goal") == ["agent-beta"]
    assert resolve("no-such-goal") is None
    assert "implement" in owner_context["supported_action_kinds"]

    # An external manager channel resolves only the Goals it is bound to, so a
    # plan naming any other Goal cannot be validated at all.
    external = context({"channel_id": "manager.external." + "a" * 24})
    external_resolve = external["resolve_registered_agents"]
    assert external_resolve("authorized-goal") == ["agent-alpha"]
    assert external_resolve("other-goal") is None


def test_the_owner_channel_projects_an_admitted_preview_onto_the_action_surface(
    tmp_path,
) -> None:
    """Admission answers; the projection is what offers the card to confirm.

    The surfaces list typed actions, so a Turn proposal is invisible until the
    channel hands it to the owner of that store. Local and remote manager
    channels both project there; a remote audience renders the same proposal
    through its provider surface instead of constructing a second action.
    """

    from loopx.capabilities.manager_context.team_plan import (
        offer_team_plan_confirmation,
    )
    from loopx.chat_store import ChatSessionStore

    store = ChatSessionStore(tmp_path / "runtime")
    preview = {"goal_id": "authorized-goal", "lanes": [{"lane_id": "lane-alpha"}]}
    answer = "Here is the plan"
    response = {
        "message": answer,
        "proposals": [{"kind": "steward_team_plan_preview", "preview": preview}],
        "gate": None,
    }
    projected: list[dict] = []
    projector = lambda value: (  # noqa: E731 - one inline surface owner
        projected.append(dict(value)) or {"proposal_id": "proposal-team-plan"}
    )

    def offer(session: dict, *, projector=None, turn_id: str = "turn-1") -> dict:
        return offer_team_plan_confirmation(
            store=store,
            session=session,
            session_id="session-1",
            turn_id=turn_id,
            response=response,
            projector=projector,
        )

    # A Goal channel has no team plan to offer, so its answer is untouched even
    # when a surface owner is available.
    untouched = offer({"channel_id": "goal.authorized-goal"}, projector=projector)
    assert untouched == response
    assert projected == []

    # The owner's own channel stores exactly one card and says where it lives.
    owner = offer({"channel_id": "manager"}, projector=projector)
    assert projected == [preview]
    assert owner["message"].startswith(answer)
    assert "authorized-goal" in owner["message"]
    assert "确认前不会创建任何 lane" in owner["message"]
    assert owner["proposals"] == response["proposals"]

    # A remote manager audience projects the same admitted preview, so its Lark
    # surface can bind the canonical proposal identity rather than rebuilding it.
    remote = offer(
        {"channel_id": "manager.external." + "a" * 24},
        projector=projector,
        turn_id="turn-2",
    )
    assert projected == [preview, preview]
    assert remote["message"].startswith(answer)
    assert "authorized-goal" in remote["message"]

    # A surface that cannot store the card does not fail the answer: the gap is
    # typed on the Turn instead, and the pointer still names the Goal.
    def refuse(_value: object) -> dict:
        raise ValueError("the typed action store is unavailable")

    refused = offer({"channel_id": "manager"}, projector=refuse, turn_id="turn-3")
    assert "authorized-goal" in refused["message"]
    assert [
        event["kind"] for event in store.events_after("session-1", "turn-1", None)
    ] == ["team_plan.projected"]
    assert [
        event["kind"] for event in store.events_after("session-1", "turn-3", None)
    ] == ["team_plan.projection_failed"]
    projected_event = store.events_after("session-1", "turn-1", None)[0]
    assert projected_event["payload"] == {
        "goal_id": "authorized-goal",
        "proposal_id": "proposal-team-plan",
    }


def test_an_answer_without_a_team_plan_is_never_annotated() -> None:
    """The pointer belongs to a team plan, not to every manager answer."""

    from loopx.capabilities.manager_context.team_plan import (
        offer_team_plan_confirmation,
    )

    class _Store:
        def append_event(self, *_args, **_kwargs):  # pragma: no cover - not reached
            raise AssertionError("no event is written without a team plan")

    response = {
        "message": "只回答问题的普通回复。",
        "proposals": [{"kind": "todo", "text": "Do one thing"}],
        "gate": None,
    }
    assert offer_team_plan_confirmation(
        store=_Store(),
        session={"channel_id": "manager"},
        session_id="session-1",
        turn_id="turn-1",
        response=response,
        projector=lambda _preview: {"proposal_id": "unused"},
    ) == response
