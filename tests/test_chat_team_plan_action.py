"""A confirmed team plan applies through the Chat action service."""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import pytest

from loopx.chat_action_store import ActionConflictError, ChatActionStore
from loopx.chat_actions import ChatActionService

GOAL_ID = "team-plan-action-fixture"
AGENT_ID = "agent-alpha"
_PREVIEWS = itertools.count(1)


def _fixture(tmp_path: Path, *, agents: tuple[str, ...] = (AGENT_ID,)):
    project = tmp_path / "project"
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
        "# Team Plan Action Fixture\n\n"
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
                "common_runtime_root": str(tmp_path / "runtime"),
                "updated_at": "2026-01-01T00:00:00+00:00",
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": GOAL_ID,
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
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
    service = ChatActionService(
        store=ChatActionStore(tmp_path / "runtime" / "chat" / "actions"),
        registry_path=registry_path,
    )
    return project, registry_path, service


def _plan(*, agent_id: str = AGENT_ID, goal_id: str = GOAL_ID) -> dict:
    return {
        "schema_version": "steward_team_plan_preview_v0",
        "kind": "steward_team_plan_preview",
        "goal_id": goal_id,
        "objective": "Stand up the intake lane",
        "quota_envelope": {"slots_per_day": 4},
        "stop_condition": "Stop when the owner withdraws the request",
        "lanes": [
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
        ],
    }


def _preview(service: ChatActionService, plan: dict | None = None) -> dict:
    return service.preview(
        {
            "action_kind": "team.plan",
            "summary": "Confirm the team plan",
            "normalized_parameters": {"goal_id": GOAL_ID, "plan": plan or _plan()},
            "context": {},
            "idempotency_key": f"team-plan-preview-{next(_PREVIEWS)}",
        }
    )


def _todos(project: Path) -> str:
    return (project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md").read_text(
        encoding="utf-8"
    )


def _card_delivery(*, message_id: str, chat_id: str) -> dict:
    card = {"schema": "2.0", "body": {"elements": []}}
    digest = hashlib.sha256(
        json.dumps(
            card, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return {
        "provider": "lark",
        "message_id": message_id,
        "chat_id": chat_id,
        "app_id": "cli_public_fixture",
        "cli_bin": "lark-cli-fixture",
        "sender_profile": "fixture",
        "binding_digest": "sha256:" + "b" * 64,
        "card_digest": digest,
        "submitted_card": card,
        "delivered_at": "2026-09-20T00:00:00Z",
        "authorized_principal": "lark:ou_owner",
    }


def _rewrite_objective(project: Path, objective: str) -> None:
    """Change only the intent the plan was reviewed against, not the registry."""

    state_path = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    text = state_path.read_text(encoding="utf-8")
    head, _, tail = text.partition("\n---")
    updated = "\n".join(
        f'objective: "{objective}"' if line.startswith("objective:") else line
        for line in head.splitlines()
    )
    state_path.write_text(f"{updated}\n---{tail}", encoding="utf-8")


def _two_lane_plan() -> dict:
    plan = _plan()
    plan["lanes"].append(
        {
            "lane_id": "lane-beta",
            "agent_id": AGENT_ID,
            "acceptance": "The second lane's first Todo is delivered with evidence",
            "first_todo": {
                "text": "Advance the second intake contract",
                "priority": "P2",
                "task_class": "advancement_task",
                "action_kind": "implement",
            },
        }
    )
    return plan


def test_a_confirmed_plan_creates_each_ready_lane_first_todo(tmp_path: Path) -> None:
    project, _registry_path, service = _fixture(tmp_path)

    preview = _preview(service)
    assert preview["action_kind"] == "team.plan"
    assert preview["permission_classification"] == "durable_write"

    applied = service.apply(preview["proposal_id"])
    proposal = applied["proposal"]
    assert proposal["status"] == "applied"
    receipt = proposal["receipt"]
    assert receipt["outcome"] == "team_plan_applied"
    lane_todo_ids = receipt["resource_ids"]["lane_todo_ids"]
    assert len(lane_todo_ids) == 1 and lane_todo_ids[0].startswith("todo_")
    assert receipt["resource_ids"]["todo_id"] == lane_todo_ids[0]
    # The readback names the canonical revision these lanes were created
    # against, so the owner sees what the new work is meant to advance.
    assert receipt["intent_basis"].startswith("sha256:")
    state = _todos(project)
    assert "Advance the intake contract" in state
    assert f"claimed_by={AGENT_ID}" in state
    assert state.count("loopx:todo ") == 1


def test_two_review_audiences_consume_one_team_plan_decision(tmp_path: Path) -> None:
    project, _registry_path, service = _fixture(tmp_path)
    preview = _preview(service)
    proposal_id = preview["proposal_id"]
    fingerprint = preview["expected_state_fingerprint"]
    service.store.prepare_review_card_delivery(
        proposal_id,
        audience_ids=["manager", f"goal:{GOAL_ID}"],
        authorized_principal="lark:ou_owner",
    )
    service.store.record_review_card_delivery(
        proposal_id,
        audience_id="manager",
        delivery=_card_delivery(
            message_id="om_manager_card", chat_id="oc_manager"
        ),
    )
    service.store.record_review_card_delivery(
        proposal_id,
        audience_id=f"goal:{GOAL_ID}",
        delivery=_card_delivery(message_id="om_goal_card", chat_id="oc_goal"),
    )

    decided = service.store.decide_review_card(
        proposal_id,
        decision="confirm",
        confirmation={
            "provider": "lark",
            "event_id": "evt_manager",
            "principal": "lark:ou_owner",
            "message_id": "om_manager_card",
            "chat_id": "oc_manager",
            "app_id": "cli_public_fixture",
            "audience_id": "manager",
            "state_fingerprint": fingerprint,
            "card_digest": _card_delivery(
                message_id="om_manager_card", chat_id="oc_manager"
            )["card_digest"],
            "confirmed_at": "2026-09-20T00:01:00Z",
        },
    )
    assert decided["status"] == "applying"

    replay = service.store.decide_review_card(
        proposal_id,
        decision="confirm",
        confirmation={
            "provider": "lark",
            "event_id": "evt_goal",
            "principal": "lark:ou_owner",
            "message_id": "om_goal_card",
            "chat_id": "oc_goal",
            "app_id": "cli_public_fixture",
            "audience_id": f"goal:{GOAL_ID}",
            "state_fingerprint": fingerprint,
            "card_digest": _card_delivery(
                message_id="om_goal_card", chat_id="oc_goal"
            )["card_digest"],
            "confirmed_at": "2026-09-20T00:01:01Z",
        },
    )
    assert replay["review_card"]["confirmation"]["event_id"] == "evt_manager"

    applied = service.apply(proposal_id)["proposal"]
    assert applied["status"] == "applied"
    assert _todos(project).count("loopx:todo ") == 1


def test_team_plan_cannot_be_decided_before_every_audience_is_delivered(
    tmp_path: Path,
) -> None:
    _project, _registry_path, service = _fixture(tmp_path)
    preview = _preview(service)
    proposal_id = preview["proposal_id"]
    service.store.prepare_review_card_delivery(
        proposal_id,
        audience_ids=["manager", f"goal:{GOAL_ID}"],
        authorized_principal="lark:ou_owner",
    )
    manager_delivery = _card_delivery(
        message_id="om_manager_card", chat_id="oc_manager"
    )
    service.store.record_review_card_delivery(
        proposal_id,
        audience_id="manager",
        delivery=manager_delivery,
    )

    with pytest.raises(
        ActionConflictError, match="audiences are not completely delivered"
    ):
        service.store.decide_review_card(
            proposal_id,
            decision="confirm",
            confirmation={
                "provider": "lark",
                "event_id": "evt_manager",
                "principal": "lark:ou_owner",
                "message_id": "om_manager_card",
                "chat_id": "oc_manager",
                "app_id": "cli_public_fixture",
                "audience_id": "manager",
                "state_fingerprint": preview["expected_state_fingerprint"],
                "card_digest": manager_delivery["card_digest"],
                "confirmed_at": "2026-09-20T00:01:00Z",
            },
        )


def test_review_card_delivery_retry_keeps_the_first_verified_receipt(
    tmp_path: Path,
) -> None:
    _project, _registry_path, service = _fixture(tmp_path)
    preview = _preview(service)
    proposal_id = preview["proposal_id"]
    service.store.prepare_review_card_delivery(
        proposal_id,
        audience_ids=["manager", f"goal:{GOAL_ID}"],
        authorized_principal="lark:ou_owner",
    )
    first = _card_delivery(message_id="om_manager_card", chat_id="oc_manager")
    service.store.record_review_card_delivery(
        proposal_id,
        audience_id="manager",
        delivery=first,
    )
    retry = {**first, "delivered_at": "2026-09-20T00:02:00Z"}

    replay = service.store.record_review_card_delivery(
        proposal_id,
        audience_id="manager",
        delivery=retry,
    )

    assert replay["review_card"]["deliveries"]["manager"] == first


def test_confirming_a_plan_that_staffs_no_lane_is_not_reported_as_success(
    tmp_path: Path,
) -> None:
    """A confirmation that can only create nothing reports exactly that.

    The plan stays visible with its typed gap -- the owner sees what was asked
    for and what is missing -- and confirming it does not produce a receipt that
    reads as an applied plan with a verified projection.
    """

    project, _registry_path, service = _fixture(tmp_path)

    preview = _preview(service, _plan(agent_id="agent-not-registered"))
    applied = service.apply(preview["proposal_id"])
    proposal = applied["proposal"]
    assert proposal["status"] == "failed"
    assert proposal["failure"]["error_code"] == "team_plan_no_staffable_lane"
    assert proposal["failure"]["retry_safe"] is True
    assert "receipt" not in proposal or proposal["receipt"] is None
    assert "loopx:todo " not in _todos(project)


def test_a_plan_for_another_goal_is_refused_at_preview(tmp_path: Path) -> None:
    _project, _registry_path, service = _fixture(tmp_path)

    with pytest.raises(ValueError, match="must match the plan's own Goal"):
        _preview(service, _plan(goal_id="some-other-goal"))


def test_a_changed_registry_makes_the_confirmed_plan_stale(tmp_path: Path) -> None:
    project, registry_path, service = _fixture(tmp_path, agents=(AGENT_ID,))

    preview = _preview(service)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["goals"][0]["coordination"]["registered_agents"] = [AGENT_ID, "agent-beta"]
    registry_path.write_text(json.dumps(registry), encoding="utf-8")

    applied = service.apply(preview["proposal_id"])
    # The Agents a plan was validated against are the state that can invalidate
    # it, so a registration change asks the owner to confirm the current plan.
    assert applied["proposal"]["status"] == "stale"
    assert "loopx:todo " not in _todos(project)


def test_a_changed_objective_makes_the_confirmed_plan_stale(tmp_path: Path) -> None:
    """The intent a plan advances is part of what the owner confirmed.

    Registry bytes do not move when the owner rewrites the Goal's objective, so
    a preview that bound only the registry stayed applicable and turned a plan
    reviewed against one objective into work under another. The intent basis the
    lanes would be created against is a commit precondition, not a receipt
    detail written afterwards.
    """

    project, registry_path, service = _fixture(tmp_path)

    preview = _preview(service)
    # Same registry bytes, different intent.
    _rewrite_objective(project, "Stand up a different team entirely.")

    applied = service.apply(preview["proposal_id"])

    assert applied["proposal"]["status"] == "stale"
    assert applied["proposal"]["stale"]["expected_state_fingerprint"] == (
        preview["expected_state_fingerprint"]
    )
    assert _todos(project).count("loopx:todo ") == 0


def test_an_unchanged_objective_still_confirms_and_records_its_basis(
    tmp_path: Path,
) -> None:
    """Binding the intent must not make an untouched plan unconfirmable."""

    project, _registry_path, service = _fixture(tmp_path)

    preview = _preview(service)
    applied = service.apply(preview["proposal_id"])

    proposal = applied["proposal"]
    assert proposal["status"] == "applied"
    # The receipt names the same canonical basis the preview bound.
    assert proposal["receipt"]["intent_basis"].startswith("sha256:")
    assert _todos(project).count("loopx:todo ") == 1


def test_failed_batch_retries_same_card_without_partial_work(tmp_path: Path, monkeypatch) -> None:
    from loopx.control_plane.work_items import team_plan_adapter
    project, _, service = _fixture(tmp_path)
    before = _todos(project)
    preview = _preview(service, _two_lane_plan())
    def fail(*args, **kwargs):
        raise OSError("batch unavailable")
    monkeypatch.setattr(team_plan_adapter, "write_captured_todo_state", fail)
    failed = service.apply(preview["proposal_id"])["proposal"]
    assert failed["status"] == "failed"
    assert failed["failure"]["error_code"] == "team_plan_commit_failed"
    assert _todos(project) == before
    monkeypatch.undo()
    applied = service.apply(preview["proposal_id"])["proposal"]
    assert applied["status"] == "applied"
    assert len(set(applied["receipt"]["resource_ids"]["lane_todo_ids"])) == 2
    assert _todos(project).count("loopx:todo ") == 2


@pytest.mark.parametrize("has_gap", [False, True])
def test_lost_response_recovers_original_commit_after_work_changes(tmp_path: Path, monkeypatch, has_gap: bool) -> None:
    from loopx.control_plane.work_items import team_plan_adapter
    project, _, service = _fixture(tmp_path)
    plan = _two_lane_plan()
    if has_gap:
        plan["lanes"][1]["agent_id"] = "unregistered-agent"
    preview = _preview(service, plan)
    real = team_plan_adapter.write_captured_todo_state
    def lose_response(*args, **kwargs):
        real(*args, **kwargs)
        raise OSError("response lost after commit")
    monkeypatch.setattr(team_plan_adapter, "write_captured_todo_state", lose_response)
    assert service.apply(preview["proposal_id"])["proposal"]["status"] == "failed"
    assert _todos(project).count("loopx:todo ") == (1 if has_gap else 2)
    monkeypatch.undo()
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    # A receiver's later edit must survive historical commit recovery.
    state.write_text(state.read_text().replace("Advance the intake contract", "Receiver revised the work"))
    before = state.read_bytes()
    recovered = service.apply(preview["proposal_id"])["proposal"]
    assert recovered["status"] == "applied"
    assert recovered["receipt"]["outcome"] == "team_plan_commit_recovered"
    assert recovered["receipt"].get("gap_count", 0) == int(has_gap)
    if has_gap:
        assert recovered["receipt"]["gap_lanes"] == [{
            "lane_id": plan["lanes"][1]["lane_id"],
            "agent_id": "unregistered-agent",
            "reason_code": "agent_not_registered",
        }]
    assert state.read_bytes() == before


def test_equal_text_lanes_keep_distinct_identity_and_owner(tmp_path: Path) -> None:
    from loopx.todos import list_goal_todos
    project, registry, service = _fixture(tmp_path, agents=(AGENT_ID, "agent-beta"))
    plan = _two_lane_plan()
    plan["lanes"][1]["agent_id"] = "agent-beta"
    plan["lanes"][1]["first_todo"] = dict(plan["lanes"][0]["first_todo"])
    preview = _preview(service, plan)
    result = service.apply(preview["proposal_id"])["proposal"]
    assert result["status"] == "applied"
    assert len(set(result["receipt"]["resource_ids"]["lane_todo_ids"])) == 2
    rows = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
    assert len(rows) == 2
    assert {row["claimed_by"] for row in rows} == {AGENT_ID, "agent-beta"}
    assert service.apply(preview["proposal_id"])["proposal"]["receipt"] == result["receipt"]
    assert _todos(project).count("loopx:todo ") == 2


def _validated(preview_plan: dict) -> dict:
    from loopx.control_plane.todos.contract import (
        TODO_ACTION_KIND_ADVANCEMENT_VALUES,
    )
    from loopx.control_plane.work_items.governed_transition_proposal import (
        validate_steward_team_plan_preview,
    )

    return validate_steward_team_plan_preview(
        preview_plan,
        registered_agent_ids=[AGENT_ID],
        supported_action_kinds=sorted(TODO_ACTION_KIND_ADVANCEMENT_VALUES),
    )


def test_an_admitted_preview_becomes_the_card_the_surfaces_list(
    tmp_path: Path,
) -> None:
    """Admission shows the plan; the owner still confirms it from one card.

    An admitted preview is not yet a confirmation surface: the product lists
    typed actions, so the manager channel projects the preview it validated into
    exactly one `team.plan` proposal. The projection creates no work, is scoped
    to the Goal the plan names, and is idempotent per plan, so a replayed Turn
    reuses the card instead of stacking a second one.
    """

    project, _registry_path, service = _fixture(tmp_path)
    plan = _plan()
    plan["lanes"].append(
        {
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
    )

    proposal = service.project_team_plan_preview(_validated(plan))

    assert proposal["action_kind"] == "team.plan"
    assert proposal["context"] == {"kind": "manager", "goal_id": GOAL_ID}
    assert proposal["status"] == "preview_ready"
    stored_plan = proposal["normalized_parameters"]["plan"]
    assert stored_plan["applies"] is False
    assert [lane["staffing"] for lane in stored_plan["lanes"]] == ["ready", "gap"]
    assert stored_plan["lanes"][1]["gap_reason_code"] == "action_kind_not_supported"
    # The projection itself created nothing.
    assert "loopx:todo " not in _todos(project)

    replay = service.project_team_plan_preview(_validated(plan))
    assert replay["proposal_id"] == proposal["proposal_id"]
    assert len(service.store.list(context_kind="manager")) == 1

    applied = service.apply(proposal["proposal_id"])
    receipt = applied["proposal"]["receipt"]
    # One lane became work and one stayed a gap, so this is a partial
    # application: the readback names the gap rather than reporting a full
    # success for a plan the host could only partly staff.
    assert receipt["outcome"] == "team_plan_partially_applied"
    assert receipt["gap_count"] == 1
    # Confirming the card creates the lane that can run and not the one whose
    # kind this host does not ship.
    assert len(receipt["resource_ids"]["lane_todo_ids"]) == 1
    assert receipt["lanes"][0]["lane_id"] == "lane-alpha"
    assert receipt["lanes"][0]["acceptance"] == (
        "The lane's first Todo is delivered with evidence"
    )
    state = _todos(project)
    assert "Advance the intake contract" in state
    assert "Repair the public smoke" not in state
