"""A confirmed team plan creates its lanes' first Todos and nothing else."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loopx.control_plane.work_items.governed_transition_proposal import (
    GovernedTransitionSettlementPhase,
    settle_governed_transition_proposals,
    validate_governed_transition_receipts,
)

GOAL_ID = "team-plan-apply-fixture"
AGENT_ID = "agent-alpha"


def _fixture(
    tmp_path: Path, *, agents: tuple[str, ...] = (AGENT_ID,)
) -> tuple[Path, Path]:
    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active-read-only\n"
        "owner_mode: goal\n"
        'objective: "Stand up one digital team."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Team Plan Apply Fixture\n\n"
        "## Next Action\n\n"
        "- Confirm the team plan.\n\n"
        "## Agent Todo\n\n",
        encoding="utf-8",
    )
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "team-plan-apply-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": list(agents),
                            "agent_model": "peer_v1",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project, registry_path


def _proposal(*, agent_id: str = AGENT_ID, extra_lane: dict | None = None) -> dict:
    lanes = [
        {
            "lane_id": "lane-alpha",
            "agent_id": agent_id,
            "acceptance": "The lane's first Todo is delivered with evidence",
            "first_todo": {
                "text": "Advance the intake contract",
                "priority": "P1",
                "task_class": "advancement_task",
                "action_kind": "implement",
            },
        }
    ]
    if extra_lane is not None:
        lanes.append(extra_lane)
    return {
        "schema_version": "steward_team_plan_preview_v0",
        "kind": "steward_team_plan_preview",
        "goal_id": GOAL_ID,
        "proposal_id": "proposal-team-plan",
        "objective": "Stand up the intake lane",
        "quota_envelope": {"slots_per_day": 4},
        "stop_condition": "Stop when the owner withdraws the request",
        "lanes": lanes,
    }


def _settle(registry_path: Path, proposal: dict) -> list[dict]:
    return settle_governed_transition_proposals(
        registry_path=registry_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        effect_id="effect-team-plan",
        proposals=[proposal],
        existing_receipts=[],
        checkpoint=lambda _receipts: None,
        phase=GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
    )


def _todos(project: Path) -> str:
    return (project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md").read_text(
        encoding="utf-8"
    )


def test_a_confirmed_plan_creates_each_ready_lane_first_todo(tmp_path: Path) -> None:
    project, registry_path = _fixture(tmp_path)

    receipts = _settle(registry_path, _proposal())

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["kind"] == "steward_team_plan_preview"
    assert receipt["status"] == "committed"
    assert receipt["action"] == "created"
    assert receipt["todo_id"].startswith("todo_")
    state = _todos(project)
    assert "Advance the intake contract" in state
    assert f"claimed_by={AGENT_ID}" in state
    # One lane, one Todo: no monitor rows, no extra work.
    assert state.count("loopx:todo ") == 1


def test_a_gap_lane_creates_nothing_and_a_replay_adds_no_second_row(
    tmp_path: Path,
) -> None:
    """A plan with one staffable lane applies it and leaves the gap a gap."""

    project, registry_path = _fixture(tmp_path)
    gap_lane = {
        "lane_id": "lane-beta",
        "agent_id": "agent-not-registered",
        "acceptance": "Never reached",
        "first_todo": {
            "text": "Work that cannot be staffed",
            "priority": "P1",
            "task_class": "advancement_task",
            "action_kind": "implement",
        },
    }

    first = _settle(registry_path, _proposal(extra_lane=gap_lane))

    assert first[0]["action"] == "created"
    state = _todos(project)
    assert "Work that cannot be staffed" not in state
    assert state.count("loopx:todo ") == 1

    # The canonical Todo owner decides reuse, so a replayed settlement does not
    # duplicate the lane it already created.
    replay = _settle(registry_path, _proposal(extra_lane=gap_lane))
    assert replay[0]["action"] == "reused"
    # The receipt still names the same lane Todo, and the gap lane stays absent.
    assert replay[0]["todo_id"] == first[0]["todo_id"]
    assert replay[0]["proposal_digest"] == first[0]["proposal_digest"]
    assert _todos(project).count("loopx:todo ") == 1


def test_a_lane_whose_kind_the_host_does_not_ship_creates_nothing(
    tmp_path: Path,
) -> None:
    """A confirmable plan may contain a lane this host cannot start.

    The plan itself is admitted -- its other lanes are the owner's request -- and
    the unstaffable lane is a typed gap, so confirming the plan creates exactly
    the lanes that can run instead of failing the whole confirmation.
    """

    project, registry_path = _fixture(tmp_path)
    unsupported_kind_lane = {
        "lane_id": "lane-beta",
        "agent_id": AGENT_ID,
        "acceptance": "Never reached",
        "first_todo": {
            "text": "Repair the public smoke",
            "priority": "P1",
            "task_class": "advancement_task",
            "action_kind": "public_smoke_quality_repair",
        },
    }

    receipts = _settle(registry_path, _proposal(extra_lane=unsupported_kind_lane))

    assert receipts[0]["action"] == "created"
    assert receipts[0]["lane_todo_ids"] == [receipts[0]["todo_id"]]
    state = _todos(project)
    assert "Advance the intake contract" in state
    assert "Repair the public smoke" not in state
    assert state.count("loopx:todo ") == 1


def test_an_unknown_goal_is_refused_before_any_todo(tmp_path: Path) -> None:
    project, registry_path = _fixture(tmp_path)
    unknown = _proposal()
    unknown["goal_id"] = "goal-that-does-not-exist"

    with pytest.raises(ValueError, match="unknown Goal"):
        settle_governed_transition_proposals(
            registry_path=registry_path,
            goal_id="goal-that-does-not-exist",
            agent_id=AGENT_ID,
            effect_id="effect-team-plan",
            proposals=[unknown],
            existing_receipts=[],
            checkpoint=lambda _receipts: None,
            phase=GovernedTransitionSettlementPhase.PRE_SETTLEMENT,
        )

    assert "loopx:todo " not in _todos(project)


def test_a_plan_cannot_be_retargeted_to_another_goal(tmp_path: Path) -> None:
    """Admission facts and the applied Goal have to be the same Goal."""

    project, registry_path = _fixture(tmp_path)
    retargeted = _proposal()
    retargeted["goal_id"] = "some-other-goal"

    with pytest.raises(ValueError, match="different Goal than its settlement"):
        _settle(registry_path, retargeted)

    # Nothing was created, and the named Goal's own plan still applies.
    assert "loopx:todo " not in _todos(project)
    assert _settle(registry_path, _proposal())[0]["action"] == "created"


def _second_lane() -> dict:
    return {
        "lane_id": "lane-beta",
        "agent_id": AGENT_ID,
        "acceptance": "The second lane's first Todo is delivered with evidence",
        "first_todo": {
            "text": "Read back the second lane's bounded first turn",
            "priority": "P2",
            "task_class": "advancement_task",
            "action_kind": "implement",
        },
    }


def test_the_receipt_names_every_lane_todo_it_created(tmp_path: Path) -> None:
    """One readback has to say what exists now, not only where it started."""

    project, registry_path = _fixture(tmp_path, agents=(AGENT_ID, "agent-beta"))

    receipts = _settle(registry_path, _proposal(extra_lane=_second_lane()))

    receipt = receipts[0]
    lane_todo_ids = receipt["lane_todo_ids"]
    assert len(lane_todo_ids) == 2 and len(set(lane_todo_ids)) == 2
    # The first lane Todo is still the receipt's own identity, so a reader that
    # only knows the older field keeps working.
    assert receipt["todo_id"] == lane_todo_ids[0]
    assert _todos(project).count("loopx:todo ") == 2
    # Both the plan readback and the older receipt shape validate, which is what
    # a settlement journal does with its stored receipts.
    assert len(validate_governed_transition_receipts(receipts)) == 1

    # A replayed settlement reports the same lanes instead of an empty readback.
    replay = _settle(registry_path, _proposal(extra_lane=_second_lane()))
    assert replay[0]["action"] == "reused"
    assert replay[0]["lane_todo_ids"] == lane_todo_ids
    assert _todos(project).count("loopx:todo ") == 2


def _receipt(**overrides) -> dict:
    receipt = {
        "schema_version": "loopx_governed_transition_proposal_receipt_v0",
        "proposal_id": "proposal-receipt-fixture",
        "proposal_digest": "sha256:" + "a" * 64,
        "kind": "continuous_monitor_upsert",
        "monitor_key": "monitor-key-1",
        "action": "updated",
        "todo_id": "todo_1",
        "status": "committed",
        "target_key": None,
    }
    receipt.update(overrides)
    return receipt


def test_the_lane_readback_is_optional_bounded_and_additive() -> None:
    """An older receipt stays valid; the new field is the only addition."""

    assert len(validate_governed_transition_receipts([_receipt()])) == 1
    assert len(
        validate_governed_transition_receipts(
            [_receipt(lane_todo_ids=["todo_1", "todo_2"])]
        )
    ) == 1

    for invalid_readback in (
        [],
        ["todo_1", "todo_1"],
        ["todo_1", "not-a-todo-id"],
        ["todo_1", "todo_" + "a" * 41],
        ["todo_1"] * 9,
        "todo_1",
    ):
        with pytest.raises(ValueError, match="lane_todo_ids is invalid"):
            validate_governed_transition_receipts(
                [_receipt(lane_todo_ids=invalid_readback)]
            )
    # The field set stays closed: the readback is the only thing that may be
    # added, and a monitor receipt still has to name its own key.
    with pytest.raises(ValueError, match="receipt fields are invalid"):
        validate_governed_transition_receipts([_receipt(unexpected_field=1)])
    with pytest.raises(ValueError, match="monitor_key is invalid"):
        validate_governed_transition_receipts([_receipt(monitor_key=None)])


def test_the_receipt_records_the_intent_basis_it_was_applied_against(
    tmp_path: Path,
) -> None:
    """A work-graph edit is traceable to the canonical basis it advanced."""

    from loopx.control_plane.goals.shared_goal_alignment import (
        project_shared_goal_alignment,
    )

    project, registry_path = _fixture(tmp_path)

    def basis() -> str:
        return project_shared_goal_alignment(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            project=project,
            registry_path=registry_path,
        )["source_basis"]["source_basis_digest"]

    before = basis()
    receipts = _settle(registry_path, _proposal())
    recorded = receipts[0]["intent_basis"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", recorded)
    # The receipt names the revision the edit was applied against, not the one
    # the edit itself produced, and it is the canonical basis rather than a
    # digest this module invented.
    assert recorded == before
    assert basis() != before
    assert len(validate_governed_transition_receipts(receipts)) == 1

    for malformed in ("sha256:short", "2836abc7", "sha256:" + "A" * 64):
        with pytest.raises(ValueError, match="intent_basis is invalid"):
            validate_governed_transition_receipts([_receipt(intent_basis=malformed)])
    assert len(
        validate_governed_transition_receipts(
            [_receipt(intent_basis="sha256:" + "a" * 64)]
        )
    ) == 1


def test_a_team_plan_receipt_must_not_invent_a_monitor_key() -> None:
    """A plan is not a monitor, so its receipt carries no monitor identity."""

    team_plan = _receipt(kind="steward_team_plan_preview", monitor_key=None)

    assert len(validate_governed_transition_receipts([team_plan])) == 1
    with pytest.raises(ValueError, match="monitor_key is invalid"):
        validate_governed_transition_receipts(
            [{**team_plan, "monitor_key": "monitor-key-1"}]
        )


def test_a_confirmed_lane_keeps_the_priority_the_owner_confirmed(
    tmp_path: Path,
) -> None:
    """The priority in the confirmed plan is the priority of the work it starts.

    The plan the owner reviewed declares P0 and the canonical Todo readers read
    a priority from the row's own label, so a materialized lane that arrives
    without one is a different commitment from the one that was confirmed.
    """

    from loopx.todos import list_goal_todos

    project, registry_path = _fixture(tmp_path)
    proposal = _proposal()
    proposal["lanes"][0]["first_todo"]["priority"] = "P0"

    _settle(registry_path, proposal)

    items = list_goal_todos(registry_path=registry_path, goal_id=GOAL_ID)["todos"]
    assert len(items) == 1
    assert items[0]["text"].startswith("[P0] ")
    assert items[0]["text"] == "[P0] Advance the intake contract"
    assert "[P0] Advance the intake contract" in _todos(project)


def test_a_lane_with_conflicting_priority_declarations_is_refused(
    tmp_path: Path,
) -> None:
    """A plan cannot silently choose between two conflicting priorities."""

    project, registry_path = _fixture(tmp_path)
    proposal = _proposal()
    proposal["lanes"][0]["first_todo"]["text"] = "[P2] Advance the intake contract"
    proposal["lanes"][0]["first_todo"]["priority"] = "P0"

    with pytest.raises(ValueError, match="priority conflicts"):
        _settle(registry_path, proposal)

    assert "loopx:todo " not in _todos(project)


def test_the_receipt_retains_each_lanes_acceptance_beside_its_todo(
    tmp_path: Path,
) -> None:
    """What a lane was meant to end on survives the answer that offered it."""

    _project, registry_path = _fixture(tmp_path)
    proposal = _proposal()
    proposal["lanes"][0]["acceptance"] = "The intake contract is merged"

    receipts = _settle(registry_path, proposal)

    settlements = receipts[0]["lane_settlements"]
    assert len(settlements) == 1
    assert settlements[0]["lane_id"] == "lane-alpha"
    assert settlements[0]["agent_id"] == AGENT_ID
    assert settlements[0]["acceptance"] == "The intake contract is merged"
    assert settlements[0]["priority"] == "P1"
    assert settlements[0]["disposition"] == "created"
    assert settlements[0]["todo_id"] == receipts[0]["todo_id"]
    assert validate_governed_transition_receipts(receipts) == receipts


def test_a_plan_that_can_staff_no_lane_reports_that_instead_of_reuse(
    tmp_path: Path,
) -> None:
    """A settlement that staffed nothing is not a reuse of existing work.

    A lane Todo only exists here because a lane was staffed, so "reused" for a
    plan whose every lane is a gap names work that the readback cannot find.
    """

    project, registry_path = _fixture(tmp_path)
    plan = _proposal()
    plan["lanes"] = [
        {
            "lane_id": "lane-beta",
            "agent_id": "agent-not-registered",
            "acceptance": "Never reached",
            "first_todo": {
                "text": "Work that cannot be staffed",
                "priority": "P2",
                "task_class": "advancement_task",
                "action_kind": "implement",
            },
        }
    ]

    receipts = _settle(registry_path, plan)

    assert receipts[0]["action"] == "unstaffed"
    assert receipts[0]["todo_id"] == ""
    assert "lane_todo_ids" not in receipts[0]
    assert "lane_settlements" not in receipts[0]
    assert "loopx:todo " not in _todos(project)


def test_batch_write_failure_leaves_every_lane_unwritten(tmp_path: Path, monkeypatch) -> None:
    from loopx.control_plane.work_items import team_plan_adapter
    project, registry_path = _fixture(tmp_path, agents=(AGENT_ID, "agent-beta"))
    before = _todos(project)
    def fail(*args, **kwargs):
        raise OSError("batch write unavailable")
    monkeypatch.setattr(team_plan_adapter, "write_captured_todo_state", fail)
    with pytest.raises(OSError, match="batch write unavailable"):
        _settle(registry_path, _proposal(extra_lane=_second_lane()))
    assert _todos(project) == before


def test_a_lane_failure_receipt_must_use_the_typed_vocabulary() -> None:
    """A reader acts on the code, so an unreadable one is refused."""

    base = _receipt(kind="steward_team_plan_preview", monitor_key=None)
    base["lane_failure"] = {"lane_id": "lane-beta", "reason_code": "lane_write_failed"}
    assert len(validate_governed_transition_receipts([base])) == 1

    for malformed in (
        {"lane_id": "lane-beta", "reason_code": "something_went_wrong"},
        {"lane_id": "lane-beta"},
        {"lane_id": "lane-beta", "reason_code": "lane_write_failed", "note": "x"},
    ):
        with pytest.raises(ValueError, match="lane_failure is invalid"):
            validate_governed_transition_receipts([{**base, "lane_failure": malformed}])
