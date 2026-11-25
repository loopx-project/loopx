from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.control_plane.quota.heartbeat_receipt import (
    bind_replan_successor_heartbeat_receipt,
    find_heartbeat_receipt,
    heartbeat_receipt_view,
    requalify_bound_heartbeat_receipt,
    retain_pending_heartbeat_action_selection,
    upgrade_identityless_heartbeat_receipt,
)
from loopx.control_plane.quota.error_codes import HeartbeatReceiptIdentityConflictError
from loopx.control_plane.quota.settlement import read_heartbeat_settlement
from loopx.rollout_event_log import (
    append_rollout_event,
    build_rollout_event,
    load_rollout_events,
    rollout_event_log_path,
)

GOAL_ID = "heartbeat-receipt-fixture"
AGENT_ID = "codex-heartbeat-receipt"
TURN_ID = "turn-heartbeat-receipt-1"
TODO_ID = "todo_heartbeat_receipt"
EFFECT_ID = f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{TURN_ID}"


@pytest.mark.parametrize("todo_id", ["todo_BAD_ID", "not_a_todo", "todo_a", "todo_" + "x" * 65])
def test_receipt_reader_rejects_the_same_illegal_binding_as_settlement(
    tmp_path: Path, todo_id: str,
) -> None:
    _append_receipt(tmp_path, todo_id=todo_id, effect_id=f"{GOAL_ID}:{AGENT_ID}:{todo_id}:{TURN_ID}")
    path = rollout_event_log_path(tmp_path, GOAL_ID)
    original = path.read_bytes()
    typed = read_heartbeat_settlement(
        tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=None,
        turn_instance_id=TURN_ID, resolve_original_binding=True,
    )
    assert typed is not None and typed.identity.failure is not None
    reason = "heartbeat receipt declares a Todo binding that is not a legal Todo id"
    assert typed.identity.failure.reason == reason
    with pytest.raises(HeartbeatReceiptIdentityConflictError, match="not a legal Todo id") as rejected:
        find_heartbeat_receipt(tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID, turn_instance_id=TURN_ID)
    assert str(rejected.value) == reason
    assert path.read_bytes() == original


def test_binding_projection_does_not_cache_or_roundtrip_receipt_details(tmp_path: Path) -> None:
    # Two fresh logs have identical binding facts and different unconsumed
    # details. A pure selection may be reused; the event must be read afresh.
    for ordinal in (1, 2):
        runtime = tmp_path / str(ordinal)
        event = build_rollout_event(
            goal_id=GOAL_ID, event_kind="quota_should_run", agent_id=AGENT_ID,
            run_id=TURN_ID, status="normal_run", details={
                "todo_id": TODO_ID, "settlement_effect_id": EFFECT_ID,
                "opaque_observation": ordinal, "exact_integer": 2**60 + ordinal,
            },
        )
        recorded = append_rollout_event(rollout_event_log_path(runtime, GOAL_ID), event)
        read = find_heartbeat_receipt(runtime, goal_id=GOAL_ID, agent_id=AGENT_ID, turn_instance_id=TURN_ID)
        assert read == recorded
        assert read["details"]["opaque_observation"] == ordinal
        assert read["details"]["exact_integer"] == 2**60 + ordinal


@pytest.mark.parametrize("binding_details", [
    {"replan_obligation_id": "replan-2222222222222222"},
    {"request_id": "a" * 64, "request_revision": 1,
     "request_digest": "b" * 64, "execution_scope_sha256": "c" * 64},
])
def test_shared_binding_outranks_a_later_unbound_receipt(
    tmp_path: Path, binding_details: dict[str, object],
) -> None:
    path = rollout_event_log_path(tmp_path, GOAL_ID)
    bound = append_rollout_event(path, build_rollout_event(
        goal_id=GOAL_ID, event_kind="quota_should_run", agent_id=AGENT_ID,
        run_id=TURN_ID, status="normal_run", details=binding_details,
    ))
    _append_receipt(tmp_path)
    before = path.read_bytes()
    assert find_heartbeat_receipt(
        tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID, turn_instance_id=TURN_ID,
    ) == bound
    assert path.read_bytes() == before


def _append_receipt(
    runtime_root: Path,
    *,
    todo_id: str = "",
    effect_id: str = "",
) -> dict[str, object]:
    details: dict[str, object] = {"stall_observation": "not_applicable"}
    if todo_id:
        details["todo_id"] = todo_id
    if effect_id:
        details["settlement_effect_id"] = effect_id
    event = build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="quota_should_run",
        agent_id=AGENT_ID,
        todo_id=todo_id or None,
        run_id=TURN_ID,
        status="normal_run",
        summary="heartbeat receipt fixture",
        details=details,
    )
    return append_rollout_event(
        rollout_event_log_path(runtime_root, GOAL_ID),
        event,
    )


def _upgrade(runtime_root: Path, *, todo_id: str = TODO_ID) -> tuple[dict[str, object], bool]:
    effect_id = f"{GOAL_ID}:{AGENT_ID}:{todo_id}:{TURN_ID}"
    return upgrade_identityless_heartbeat_receipt(
        runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=todo_id,
        settlement_effect_id=effect_id,
        status="normal_run",
        summary="heartbeat receipt upgraded",
        details={"stall_observation": "not_applicable", "todo_id": todo_id},
    )


def test_identityless_receipt_upgrade_is_append_only_and_idempotent(
    tmp_path: Path,
) -> None:
    original = _append_receipt(tmp_path)

    corrected, appended = _upgrade(tmp_path)
    replay, replay_appended = _upgrade(tmp_path)

    assert appended is True
    assert replay_appended is False
    assert corrected["event_id"] != original["event_id"]
    assert replay["event_id"] == corrected["event_id"]
    assert corrected["causality"]["source_event_id"] == original["event_id"]
    assert corrected["causality"]["caused_by"] == original["event_id"]
    assert corrected["details"]["todo_id"] == TODO_ID
    assert corrected["details"]["settlement_effect_id"] == EFFECT_ID
    assert corrected["details"]["settlement_receipt_revision"] == "identity_upgrade"
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL_ID))) == 2


def test_identityless_receipt_upgrade_rejects_conflicting_selected_todo(
    tmp_path: Path,
) -> None:
    _append_receipt(tmp_path)
    _upgrade(tmp_path)

    with pytest.raises(ValueError, match="conflicts with the current selected Todo"):
        _upgrade(tmp_path, todo_id="todo_conflicting")

    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL_ID))) == 2


def test_replan_binding_rechecks_identity_inside_the_lock(
    tmp_path: Path,
) -> None:
    _append_receipt(tmp_path, todo_id=TODO_ID, effect_id=EFFECT_ID)

    with pytest.raises(ValueError):
        bind_replan_successor_heartbeat_receipt(
            tmp_path,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            turn_instance_id=TURN_ID,
            replan_obligation_id="replan-2222222222222222",
        )

    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL_ID))) == 1


def test_replan_binding_preserves_concurrent_effective_details(
    tmp_path: Path,
) -> None:
    obligation_id = "replan-2222222222222222"
    original = build_rollout_event(
        goal_id=GOAL_ID,
        event_kind="quota_should_run",
        agent_id=AGENT_ID,
        run_id=TURN_ID,
        status="autonomous_replan_required",
        summary="identity-less autonomous replan receipt",
        details={
            "interaction_mode": "autonomous_replan",
            "ok": True,
            "should_run": True,
            "must_attempt_work": True,
            "delivery_allowed": False,
            "quiet_noop_allowed": False,
            "semantic_replan_obligation_id": obligation_id,
        },
    )
    log_path = rollout_event_log_path(tmp_path, GOAL_ID)
    append_rollout_event(log_path, original)
    retained, retained_appended = retain_pending_heartbeat_action_selection(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id="todo_concurrent_choice",
        reason="owner_selected_candidate",
    )
    assert retained_appended is True

    bound, appended = bind_replan_successor_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        replan_obligation_id=obligation_id,
    )

    assert appended is True
    assert retained["details"]["pending_action_selection_todo_id"] == (
        "todo_concurrent_choice"
    )
    assert bound["details"]["pending_action_selection_todo_id"] == (
        "todo_concurrent_choice"
    )
    assert bound["details"]["replan_obligation_id"] == obligation_id


def test_the_receipt_view_says_when_the_turn_still_owes_its_binding(
    tmp_path: Path,
) -> None:
    """A guard that ran before selection may not read as a settled guard.

    The documented wake order commits the receipt first, so the view has to name
    the debt: without it the caller only finds out when the closeout refuses, and
    the turn's work stays unaccounted.
    """

    identityless = _append_receipt(tmp_path)
    owed = heartbeat_receipt_view(
        identityless,
        turn_instance_id=TURN_ID,
        status="committed",
    )

    assert owed["settlement_binding_owed"] is True
    assert "settlement_identity" not in owed

    # A receipt that names its work item and effect no longer owes anything, and
    # the flag is absent rather than false so a bound receipt keeps its shape.
    bound = _append_receipt(tmp_path, todo_id=TODO_ID, effect_id=EFFECT_ID)
    served = heartbeat_receipt_view(
        bound,
        turn_instance_id=TURN_ID,
        status="upgraded",
    )

    assert "settlement_binding_owed" not in served
    assert served["settlement_identity"]["todo_id"] == TODO_ID

    # A half-written binding (a work item with no effect id) cannot settle
    # either, so it still counts as owed instead of reading as bound.
    half = _append_receipt(tmp_path, todo_id=TODO_ID)
    assert heartbeat_receipt_view(
        half,
        turn_instance_id=TURN_ID,
        status="committed",
    )["settlement_binding_owed"] is True


def test_concurrent_identityless_receipt_upgrades_append_one_correction(
    tmp_path: Path,
) -> None:
    _append_receipt(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: _upgrade(tmp_path), range(16)))

    assert sum(1 for _, appended in results if appended) == 1
    assert {event["event_id"] for event, _ in results} == {
        results[0][0]["event_id"]
    }
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL_ID))) == 2


def test_concurrent_work_requalification_appends_one_same_binding_receipt(tmp_path: Path) -> None:
    proof = {"ok": True, "should_run": True, "must_attempt_work": True,
             "delivery_allowed": True, "quiet_noop_allowed": False,
             "todo_id": TODO_ID, "selected_todo_id": TODO_ID, "settlement_effect_id": EFFECT_ID,
             "admission_resume_when": "resume_at:2000-01-01T00:00:00Z", "admission_resume_ready": True}
    original = build_rollout_event(goal_id=GOAL_ID, event_kind="quota_should_run",
        agent_id=AGENT_ID, todo_id=TODO_ID, run_id=TURN_ID,
        status="successor_replan_required", summary="delivery denied",
        details={**proof, "delivery_allowed": False,
                 "admission_resume_when": None, "admission_resume_ready": None})
    log_path = rollout_event_log_path(tmp_path, GOAL_ID)
    append_rollout_event(log_path, original)

    def qualify(_: int) -> tuple[dict[str, object], bool]:
        return requalify_bound_heartbeat_receipt(tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID,
            turn_instance_id=TURN_ID, todo_id=TODO_ID, replan_obligation_id=None,
            status="normal_run", details=proof, registry_path=None, goal_ref=None)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(qualify, range(8)))
    assert sum(appended for _, appended in results) == 1
    assert len({event["event_id"] for event, _ in results}) == 1
    events = load_rollout_events(log_path)
    assert len(events) == 2 and events[0] == original
    assert events[1]["details"]["settlement_effect_id"] == EFFECT_ID
    assert events[1]["details"]["admission_resume_when"] == proof["admission_resume_when"]
    assert events[1]["details"]["admission_resume_ready"] is True
    assert events[1]["causality"]["caused_by"] == original["event_id"]
    replay, appended = requalify_bound_heartbeat_receipt(tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID,
        turn_instance_id=TURN_ID, todo_id=TODO_ID, replan_obligation_id=None,
        status="normal_run", details={**proof, "admission_resume_when": "resume_at:2099-01-01T00:00:00Z"},
        registry_path=None, goal_ref=None)
    assert appended is False
    assert replay == events[1]


def test_legacy_todo_only_receipt_upgrades_derived_effect_identity(
    tmp_path: Path,
) -> None:
    original = _append_receipt(tmp_path, todo_id=TODO_ID)

    corrected, appended = _upgrade(tmp_path)

    assert appended is True
    assert corrected["event_id"] != original["event_id"]
    assert corrected["details"]["settlement_effect_id"] == EFFECT_ID
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL_ID))) == 2


def test_effect_without_todo_identity_fails_closed(tmp_path: Path) -> None:
    _append_receipt(tmp_path, effect_id=EFFECT_ID)

    with pytest.raises(ValueError, match="effect identity without a Todo"):
        find_heartbeat_receipt(
            tmp_path,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            turn_instance_id=TURN_ID,
        )
    with pytest.raises(ValueError, match="effect identity without a Todo"):
        _upgrade(tmp_path)

    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL_ID))) == 1


def test_effective_receipt_does_not_downgrade_after_identity_correction(
    tmp_path: Path,
) -> None:
    _append_receipt(tmp_path)
    corrected, _ = _upgrade(tmp_path)
    _append_receipt(tmp_path)

    effective = find_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
    )

    assert effective is not None
    assert effective["event_id"] == corrected["event_id"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("preexisting", [True, False], ids=["already-due", "introduced-later"])
def test_cli_reentry_distinguishes_admission_timer_from_later_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, preexisting: bool,
) -> None:
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor, GOAL_ID as goal, TODO_ID as work, ALTERNATIVE_TODO_ID as successor,
        _configure_selectable_alternative, _initialize_fixture_authority, _run_cli,
        _run_generated_cli, _spend_run_count, _write_fixture,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state = project / f".codex/goals/{goal}/ACTIVE_GOAL_STATE.md"
    resume = "resume_at:2000-01-01T00:00:00Z"
    if preexisting:
        state.write_text(state.read_text().replace(
            f"todo_id={work} status=open",
            f"todo_id={work} status=open resume_when={resume} successor_todo_ids={successor}",
        ))
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "admission-timer-reentry"
    binding = ("--goal-id", goal, "--agent-id", actor, "--todo-id", work,
               "--turn-instance-id", turn)
    guard = ("quota", "should-run", "--codex-app", *binding)
    rc, admitted = _run_cli(registry, runtime, *guard)
    assert rc == 0, admitted
    assert admitted["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
    identity = admitted["heartbeat_receipt"]["settlement_identity"]
    if not preexisting:
        # The new wait is already due too. Its authoring after admission, not
        # the deadline alone, requires closing the original admitted result.
        rc, updated = _run_cli(registry, runtime, "todo", "update", "--goal-id", goal,
            "--agent-id", actor, "--todo-id", work, "--resume-when", resume,
            "--successor-todo-id", successor, "--reason", "Return the verified original result.")
        assert rc == 0, updated

    def rows():
        return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)["todos"]

    before = rows()
    rc, reentry = _run_cli(registry, runtime, *guard)
    assert rc == 0, reentry
    assert reentry["heartbeat_receipt"]["settlement_identity"] == identity
    if preexisting:
        assert reentry["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
        assert reentry["selected_todo"]["todo_id"] == work
    else:
        assert reentry["unsettled_host_turn_recovery"]["repair"] == "durable_writeback"
        assert reentry["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    receipt = find_heartbeat_receipt(runtime, goal_id=goal, agent_id=actor, turn_instance_id=turn)
    assert receipt is not None
    assert receipt["details"]["admission_resume_when"] == (resume if preexisting else None)
    if preexisting:
        assert receipt["details"]["admission_resume_ready"] is True
    assert rows() == before
    assert _spend_run_count(runtime) == 0

    # Both routes still owe the same result, writeback and exactly one debit.
    (project / "validated-result.txt").write_text("Synthetic result validated before closeout.\n")
    plan = reentry["interaction_contract"]["cli_channel"]["settlement_plan"]
    assert plan["schema_version"] == "quota_settlement_plan_v2"
    command = next(s["command_template"] for s in plan["ordered_steps"] if s["kind"] == "durable_writeback")
    command = command.replace("<validated_progress>", "admission_timer_validated").replace(
        "<scale>", "single_surface").replace("<outcome>", "outcome_progress")
    command += (" --delivery-boundary in_flight_continuation --progress-result-class advanced"
                f" --progress-work-item-id {work} --progress-evidence-id evidence:synthetic-result"
                " --no-global-sync --suppress-external-sinks")
    for attempt in range(2):
        rc, written = _run_generated_cli(command, registry_path=registry, cwd=project)
        assert rc == 0, written
        assert written["settlement_result"]["ok"] is True
        assert written["settlement_progress"]["state"] == "settled"
        assert [row["step_kind"] for row in written["settlement_result"]["receipts"]] == [
            "validation", "durable_writeback", "quota_spend",
        ]
        assert written.get("idempotent_replay", False) is bool(attempt)
    assert _spend_run_count(runtime) == 1
    rc, replay = _run_cli(registry, runtime, *guard)
    assert rc == 0, replay
    assert replay["effective_action"] == "heartbeat_settled_skip"
    assert replay["heartbeat_receipt"]["settlement_identity"] == identity
    assert rows() == before


def _digest_review_details(obligation_id: str) -> dict[str, object]:
    import hashlib
    from loopx.control_plane.work_items.autonomous_replan_obligation import (
        AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION,
        AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID,
        AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION,
    )

    return {
        "semantic_replan_obligation_id": obligation_id,
        "semantic_replan_review_kind": "periodic_review_due",
        "semantic_replan_review_stop_condition": AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION,
        "semantic_replan_review_stop_condition_sha256": hashlib.sha256(
            AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION.encode("utf-8")
        ).hexdigest(),
        "semantic_replan_review_stop_contract_id": AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID,
    }


def _capability_guard_details(obligation_id: str) -> dict[str, object]:
    return {
        "semantic_replan_obligation_id": obligation_id,
        "semantic_replan_capability_id": "explore",
        "semantic_replan_gap_id": "research-composition-0123456789abcdef",
        "semantic_replan_frontier_revision": "research-composition-v0:0123456789abcdef",
    }


def test_identity_upgrade_clearing_semantic_replan_strips_stale_review_fields(
    tmp_path: Path,
) -> None:
    append_rollout_event(
        rollout_event_log_path(tmp_path, GOAL_ID),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="quota_should_run",
            agent_id=AGENT_ID,
            run_id=TURN_ID,
            status="normal_run",
            details={
                "stall_observation": "not_applicable",
                **_digest_review_details("replan-1111111111111111"),
            },
        ),
    )

    upgraded, appended = upgrade_identityless_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=EFFECT_ID,
        status="normal_run",
        summary="heartbeat quota receipt upgraded after periodic review cleared",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            "semantic_replan_obligation_id": "",
        },
    )

    assert appended is True
    for field in (
        "semantic_replan_review_kind",
        "semantic_replan_review_stop_condition",
        "semantic_replan_review_stop_condition_sha256",
        "semantic_replan_review_stop_contract_id",
    ):
        assert field not in upgraded["details"]

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback is not None
    assert readback.identity.failure is None
    assert readback.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": None,
    }


def test_identity_upgrade_clearing_semantic_replan_strips_stale_capability_fields(
    tmp_path: Path,
) -> None:
    append_rollout_event(
        rollout_event_log_path(tmp_path, GOAL_ID),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="quota_should_run",
            agent_id=AGENT_ID,
            run_id=TURN_ID,
            status="normal_run",
            details={
                "stall_observation": "not_applicable",
                **_capability_guard_details("replan-1111111111111111"),
            },
        ),
    )

    upgraded, appended = upgrade_identityless_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=EFFECT_ID,
        status="normal_run",
        summary="heartbeat quota receipt upgraded after capability guard cleared",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            "semantic_replan_obligation_id": "",
        },
    )

    assert appended is True
    for field in (
        "semantic_replan_capability_id",
        "semantic_replan_gap_id",
        "semantic_replan_frontier_revision",
    ):
        assert field not in upgraded["details"]

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback is not None
    assert readback.identity.failure is None
    assert readback.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": None,
    }


def test_identity_upgrade_replan_guard_rotation_and_review_kind_switch(
    tmp_path: Path,
) -> None:
    cap_to_review = tmp_path / "cap-to-review"
    append_rollout_event(
        rollout_event_log_path(cap_to_review, GOAL_ID),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="quota_should_run",
            agent_id=AGENT_ID,
            run_id=TURN_ID,
            status="normal_run",
            details={
                "stall_observation": "not_applicable",
                **_capability_guard_details("replan-1111111111111111"),
            },
        ),
    )
    switched, appended = upgrade_identityless_heartbeat_receipt(
        cap_to_review,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=EFFECT_ID,
        status="normal_run",
        summary="capability guard replaced by review guard",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            "semantic_replan_obligation_id": "replan-2222222222222222",
            "semantic_replan_review_kind": "goal_outcome_uncovered",
            "semantic_replan_review_stop_condition": "Stop at owner-only decisions.",
        },
    )
    assert appended is True
    for field in (
        "semantic_replan_capability_id",
        "semantic_replan_gap_id",
        "semantic_replan_frontier_revision",
    ):
        assert field not in switched["details"]
    readback_cap = read_heartbeat_settlement(
        cap_to_review,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback_cap is not None
    assert readback_cap.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": "replan-2222222222222222",
        "selected_review_source": {
            "kind": "goal_outcome_uncovered",
            "stop_condition": "Stop at owner-only decisions.",
        },
    }

    digest_to_inline = tmp_path / "digest-to-inline"
    append_rollout_event(
        rollout_event_log_path(digest_to_inline, GOAL_ID),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="quota_should_run",
            agent_id=AGENT_ID,
            run_id=TURN_ID,
            status="normal_run",
            details={
                "stall_observation": "not_applicable",
                **_digest_review_details("replan-1111111111111111"),
            },
        ),
    )
    inline, appended_inline = upgrade_identityless_heartbeat_receipt(
        digest_to_inline,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=EFFECT_ID,
        status="normal_run",
        summary="digest review guard replaced by short inline review guard",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            "semantic_replan_obligation_id": "replan-2222222222222222",
            "semantic_replan_review_kind": "goal_outcome_uncovered",
            "semantic_replan_review_stop_condition": "Stop at owner-only decisions.",
        },
    )
    assert appended_inline is True
    assert "semantic_replan_review_stop_condition_sha256" not in inline["details"]
    assert "semantic_replan_review_stop_contract_id" not in inline["details"]
    readback_inline = read_heartbeat_settlement(
        digest_to_inline,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback_inline is not None
    assert readback_inline.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": "replan-2222222222222222",
        "selected_review_source": {
            "kind": "goal_outcome_uncovered",
            "stop_condition": "Stop at owner-only decisions.",
        },
    }


def test_replan_binding_preserves_existing_review_guard_when_details_empty(
    tmp_path: Path,
) -> None:
    obligation_id = "replan-2222222222222222"
    review_details = _digest_review_details(obligation_id)
    append_rollout_event(
        rollout_event_log_path(tmp_path, GOAL_ID),
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="quota_should_run",
            agent_id=AGENT_ID,
            run_id=TURN_ID,
            status="autonomous_replan_required",
            summary="identity-less autonomous replan receipt with review source",
            details={
                "interaction_mode": "autonomous_replan",
                "ok": True,
                "should_run": True,
                "must_attempt_work": True,
                "delivery_allowed": False,
                "quiet_noop_allowed": False,
                **review_details,
            },
        ),
    )

    bound, appended = bind_replan_successor_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        replan_obligation_id=obligation_id,
    )

    assert appended is True
    for key, value in review_details.items():
        assert bound["details"][key] == value

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=None,
        turn_instance_id=TURN_ID,
        replan_obligation_id=obligation_id,
    )
    assert readback is not None
    assert readback.identity.failure is None
    assert readback.semantic_replan_guard is not None
    assert readback.semantic_replan_guard["selected_obligation_id"] == obligation_id
    assert readback.semantic_replan_guard["selected_review_source"] == {
        "kind": "periodic_review_due",
        "stop_condition": review_details["semantic_replan_review_stop_condition"],
        "stop_condition_sha256": review_details["semantic_replan_review_stop_condition_sha256"],
        "stop_contract_id": review_details["semantic_replan_review_stop_contract_id"],
    }


def test_identity_upgrade_rejects_contradictory_semantic_replan_details_without_appending(
    tmp_path: Path,
) -> None:
    log_path = rollout_event_log_path(tmp_path, GOAL_ID)
    append_rollout_event(
        log_path,
        build_rollout_event(
            goal_id=GOAL_ID,
            event_kind="quota_should_run",
            agent_id=AGENT_ID,
            run_id=TURN_ID,
            status="normal_run",
            details={
                "stall_observation": "not_applicable",
                "semantic_replan_obligation_id": "replan-1111111111111111",
            },
        ),
    )
    before = log_path.read_bytes()

    with pytest.raises(HeartbeatReceiptIdentityConflictError, match="selected obligation"):
        upgrade_identityless_heartbeat_receipt(
            tmp_path,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            turn_instance_id=TURN_ID,
            todo_id=TODO_ID,
            settlement_effect_id=EFFECT_ID,
            status="normal_run",
            summary="invalid empty obligation with review field",
            details={
                "semantic_replan_obligation_id": "",
                "semantic_replan_review_kind": "periodic_review_due",
            },
        )
    with pytest.raises(HeartbeatReceiptIdentityConflictError, match="capability guard"):
        upgrade_identityless_heartbeat_receipt(
            tmp_path,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            turn_instance_id=TURN_ID,
            todo_id=TODO_ID,
            settlement_effect_id=EFFECT_ID,
            status="normal_run",
            summary="invalid simultaneous review and capability fields",
            details={
                "semantic_replan_obligation_id": "replan-1111111111111111",
                "semantic_replan_review_kind": "periodic_review_due",
                "semantic_replan_capability_id": "explore",
            },
        )

    assert log_path.read_bytes() == before


def test_quota_rollout_details_capability_guard_fallback_boundaries() -> None:
    from argparse import Namespace
    from loopx.control_plane.quota.settlement_cli import quota_rollout_details

    cap_guard = {
        "schema_version": "semantic_replan_capability_guard_v0",
        "capability_id": "explore",
        "gap_id": "research-composition-0123456789abcdef",
        "frontier_revision": "research-composition-v0:0123456789abcdef",
    }
    args = Namespace(quota_command="should-run", agent_id=AGENT_ID)
    stale_receipt = {
        "semantic_replan_obligation_id": "replan-1111111111111111",
        "semantic_replan_capability_guard": cap_guard,
    }

    # 1. Packet has no obligation -> stale capability guard must NOT be copied.
    cleared = quota_rollout_details(
        {"heartbeat_receipt": stale_receipt, "replan_action_packet": None},
        args,
        todo_id=TODO_ID,
    )
    assert cleared["semantic_replan_obligation_id"] == ""
    assert "semantic_replan_capability_id" not in cleared

    # 2. Packet rotates to a different obligation -> stale capability guard must NOT be copied.
    rotated = quota_rollout_details(
        {
            "heartbeat_receipt": stale_receipt,
            "replan_action_packet": {"obligation_id": "replan-2222222222222222"},
        },
        args,
        todo_id=None,
        replan_obligation_id="replan-2222222222222222",
    )
    assert "semantic_replan_capability_id" not in rotated

    # 3. Same obligation populates a review kind -> capability guard must NOT mix with review fields.
    review_same = quota_rollout_details(
        {
            "heartbeat_receipt": stale_receipt,
            "replan_action_packet": {"obligation_id": "replan-1111111111111111"},
            "autonomous_replan_obligation": {
                "schema_version": "autonomous_replan_obligation_v0",
                "required": True,
                "agent_id": AGENT_ID,
                "obligation_id": "replan-1111111111111111",
                "trigger_count": 1,
                "triggers": [{"kind": "periodic_review_due"}],
                "stop_condition": "Stop at owner-only decisions.",
            },
        },
        args,
        todo_id=None,
        replan_obligation_id="replan-1111111111111111",
    )
    assert review_same["semantic_replan_review_kind"] == "periodic_review_due"
    assert "semantic_replan_capability_id" not in review_same

    # 4. Matching non-review obligation without inline capability_guard -> preserve receipt fallback.
    preserved = quota_rollout_details(
        {
            "heartbeat_receipt": stale_receipt,
            "replan_action_packet": {"obligation_id": "replan-1111111111111111"},
        },
        args,
        todo_id=None,
        replan_obligation_id="replan-1111111111111111",
    )
    assert preserved["semantic_replan_capability_id"] == "explore"
    assert preserved["semantic_replan_gap_id"] == cap_guard["gap_id"]
    assert preserved["semantic_replan_frontier_revision"] == cap_guard["frontier_revision"]


def test_turn_guard_rejects_capability_guard_without_obligation_before_lock_and_before_bind(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.quota.effect_program import SettlementIdentity
    from loopx.control_plane.quota.heartbeat_receipt import (
        ensure_turn_heartbeat_settlement_receipt,
    )

    called: list[bool] = []
    identity = SettlementIdentity(GOAL_ID, AGENT_ID, TODO_ID, TURN_ID)
    cap_guard = {
        "schema_version": "semantic_replan_capability_guard_v0",
        "capability_id": "explore",
        "gap_id": "research-composition-0123456789abcdef",
        "frontier_revision": "research-composition-v0:0123456789abcdef",
    }

    with pytest.raises(ValueError, match="selected obligation"):
        ensure_turn_heartbeat_settlement_receipt(
            tmp_path,
            identity,
            semantic_replan_guard_scoped=True,
            semantic_replan_obligation_id=None,
            semantic_replan_capability_guard=cap_guard,
            before_bind=lambda: called.append(True),
        )

    assert called == []
    assert not rollout_event_log_path(tmp_path, GOAL_ID).exists()


def _append_legacy_main_orphaned_identity_upgrade_receipt(
    runtime_root: Path,
    *,
    goal_id: str,
    agent_id: str,
    todo_id: str,
    turn_instance_id: str,
    companion_details: dict[str, object],
    upgrade_overlay: dict[str, object] | None = None,
    bind_parent: bool = False,
    break_lineage: bool = False,
) -> tuple[dict[str, object], dict[str, object], str]:
    from loopx.control_plane.quota.effect_program import SettlementIdentity

    identity = SettlementIdentity(
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
    )
    log_path = rollout_event_log_path(runtime_root, goal_id)
    base_details: dict[str, object] = {
        "command": "quota",
        "quota_command": "should-run",
        "ok": True,
        "should_run": True,
        "must_attempt_work": True,
        "delivery_allowed": True,
        "quiet_noop_allowed": False,
        "interaction_mode": "execution",
        "closeout_required": True,
        "stall_observation": "not_applicable",
        "quota_spend_source": "heartbeat",
        "selected_todo_id": todo_id,
        "todo_id": todo_id if bind_parent else "",
        "replan_obligation_id": "",
        "settlement_effect_id": identity.effect_id if bind_parent else "",
    }
    parent_details = {**base_details, **companion_details}
    parent = append_rollout_event(
        log_path,
        build_rollout_event(
            goal_id=goal_id,
            event_kind="quota_should_run",
            agent_id=agent_id,
            run_id=turn_instance_id,
            status="should-run",
            summary="legacy identity-less receipt with semantic replan guard",
            details=parent_details,
        ),
    )
    orphaned_source = (
        None if break_lineage else str(parent["event_id"])
    )
    upgraded_details: dict[str, object] = {
        **parent_details,
        "turn_instance_id": turn_instance_id,
        "todo_id": todo_id,
        "semantic_replan_obligation_id": "",
        "settlement_effect_id": identity.effect_id,
        "settlement_receipt_revision": "identity_upgrade",
    }
    if upgrade_overlay is not None:
        upgraded_details.update(upgrade_overlay)
    bad_upgrade = append_rollout_event(
        log_path,
        build_rollout_event(
            goal_id=goal_id,
            event_kind="quota_should_run",
            agent_id=agent_id,
            todo_id=todo_id,
            run_id=turn_instance_id,
            status="should-run",
            summary=f"heartbeat quota receipt upgraded for turn={turn_instance_id}",
            source_event_id=orphaned_source,
            caused_by=orphaned_source,
            details=upgraded_details,
        ),
    )
    return parent, bad_upgrade, identity.effect_id


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_recovers_legacy_main_orphaned_identity_upgrade_receipt_and_settles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor,
        GOAL_ID as goal,
        TODO_ID as work,
        _initialize_fixture_authority,
        _run_cli,
        _run_generated_cli,
        _spend_run_count,
        _write_fixture,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "recover-legacy-main-identity-upgrade"
    _, bad_upgrade, expected_effect_id = (
        _append_legacy_main_orphaned_identity_upgrade_receipt(
            runtime,
            goal_id=goal,
            agent_id=actor,
            todo_id=work,
            turn_instance_id=turn,
            companion_details=_digest_review_details("replan-1111111111111111"),
        )
    )
    log_path = rollout_event_log_path(runtime, goal)
    before_bytes = log_path.read_bytes()

    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        goal,
        "--agent-id",
        actor,
        "--todo-id",
        work,
        "--turn-instance-id",
        turn,
    )

    # Dry-run stays strictly read-only even when the historical receipt is broken.
    _run_cli(registry, runtime, *guard_args, "--dry-run")
    assert log_path.read_bytes() == before_bytes

    rc1, retry1 = _run_cli(registry, runtime, *guard_args)
    assert rc1 == 0, retry1
    assert retry1["ok"] is True
    assert retry1["state"] == "eligible"
    assert retry1["heartbeat_receipt"]["status"] == "upgraded"
    assert (
        retry1["heartbeat_receipt"]["settlement_identity"]["effect_id"]
        == expected_effect_id
    )
    events_after_r1 = load_rollout_events(log_path)
    assert len(events_after_r1) == 3
    repaired_event = events_after_r1[-1]
    assert repaired_event["causality"]["caused_by"] == bad_upgrade["event_id"]
    for field in (
        "semantic_replan_review_kind",
        "semantic_replan_review_stop_condition",
        "semantic_replan_review_stop_condition_sha256",
        "semantic_replan_review_stop_contract_id",
    ):
        assert field not in repaired_event["details"]

    readback = read_heartbeat_settlement(
        runtime,
        goal_id=goal,
        agent_id=actor,
        todo_id=work,
        turn_instance_id=turn,
        registry_path=registry,
    )
    assert readback is not None
    assert readback.identity.failure is None
    assert readback.admission_event_id == bad_upgrade["event_id"]
    assert readback.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": None,
    }

    bytes_after_r1 = log_path.read_bytes()
    rc2, retry2 = _run_cli(registry, runtime, *guard_args)
    assert rc2 == 0, retry2
    assert retry2["ok"] is True
    assert retry2["state"] == "eligible"
    assert retry2["heartbeat_receipt"]["status"] == "replayed"
    assert (
        retry2["heartbeat_receipt"]["settlement_identity"]
        == retry1["heartbeat_receipt"]["settlement_identity"]
    )
    assert log_path.read_bytes() == bytes_after_r1

    (project / "validated-result.txt").write_text(
        "Validated delivery after identity_upgrade companion cleanup.\n"
    )
    plan = retry2["interaction_contract"]["cli_channel"]["settlement_plan"]
    command = next(
        s["command_template"]
        for s in plan["ordered_steps"]
        if s["kind"] == "durable_writeback"
    )
    command = (
        command.replace("<validated_progress>", "recovered_identity_upgrade")
        .replace("<scale>", "single_surface")
        .replace("<outcome>", "outcome_progress")
    )
    command += (
        " --delivery-boundary in_flight_continuation"
        " --progress-result-class advanced"
        f" --progress-work-item-id {work}"
        " --progress-evidence-id evidence:recovered-result"
        " --no-global-sync --suppress-external-sinks"
    )
    rc_wb, written = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc_wb == 0, written
    assert written["settlement_progress"]["state"] == "settled"
    assert _spend_run_count(runtime) == 1

    rc3, settled = _run_cli(registry, runtime, *guard_args)
    assert rc3 == 0, settled
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert (
        settled["heartbeat_receipt"]["settlement_identity"]
        == retry1["heartbeat_receipt"]["settlement_identity"]
    )


def test_cli_recovers_prior_turn_orphaned_identity_upgrade_without_blocked_health(
    tmp_path: Path,
) -> None:
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor,
        GOAL_ID as goal,
        TODO_ID as work,
        _initialize_fixture_authority,
        _run_cli,
        _write_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider="file")
    prior_turn = "prior-turn-main-identity-upgrade"
    _, _, expected_effect_id = _append_legacy_main_orphaned_identity_upgrade_receipt(
        runtime,
        goal_id=goal,
        agent_id=actor,
        todo_id=work,
        turn_instance_id=prior_turn,
        companion_details=_digest_review_details("replan-1111111111111111"),
    )

    rc, result = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        goal,
        "--agent-id",
        actor,
        "--turn-instance-id",
        "fresh-followup-turn",
    )
    assert rc == 0, result
    assert result["state"] != "blocked_health"
    assert (
        result["unsettled_host_turn_recovery"]["prior_turn_instance_id"]
        == prior_turn
    )
    repaired_prior = find_heartbeat_receipt(
        runtime, goal_id=goal, agent_id=actor, turn_instance_id=prior_turn
    )
    assert repaired_prior is not None
    assert repaired_prior["details"]["settlement_effect_id"] == expected_effect_id
    assert "semantic_replan_review_kind" not in repaired_prior["details"]


@pytest.mark.parametrize(
    ("companion_builder", "stripped_fields"),
    [
        (
            _digest_review_details,
            (
                "semantic_replan_review_kind",
                "semantic_replan_review_stop_condition",
                "semantic_replan_review_stop_condition_sha256",
                "semantic_replan_review_stop_contract_id",
            ),
        ),
        (
            _capability_guard_details,
            (
                "semantic_replan_capability_id",
                "semantic_replan_gap_id",
                "semantic_replan_frontier_revision",
            ),
        ),
    ],
    ids=["review-companions", "capability-companions"],
)
def test_upgrade_api_recovers_existing_bound_identity_upgrade_idempotently(
    tmp_path: Path,
    companion_builder: Callable[[str], dict[str, object]],
    stripped_fields: tuple[str, ...],
) -> None:
    companion_details = companion_builder("replan-1111111111111111")
    _, _, expected_effect_id = _append_legacy_main_orphaned_identity_upgrade_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
        companion_details=companion_details,
    )

    repaired, appended = upgrade_identityless_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=expected_effect_id,
        status="normal_run",
        summary="recover bound identity_upgrade receipt",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            "semantic_replan_obligation_id": "",
        },
    )
    assert appended is True
    assert repaired["details"]["settlement_effect_id"] == expected_effect_id
    for field in stripped_fields:
        assert field not in repaired["details"]

    log_path = rollout_event_log_path(tmp_path, GOAL_ID)
    after_first = log_path.read_bytes()
    replayed, appended_again = upgrade_identityless_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=expected_effect_id,
        status="normal_run",
        summary="replay after repair",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            "semantic_replan_obligation_id": "",
        },
    )
    assert appended_again is False
    assert replayed == repaired
    assert log_path.read_bytes() == after_first

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback is not None
    assert readback.identity.failure is None
    assert readback.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": None,
    }


def test_orphaned_identity_upgrade_without_causal_lineage_fails_closed(
    tmp_path: Path,
) -> None:
    from loopx.control_plane.quota.heartbeat_receipt import (
        repair_orphaned_identity_upgrade_heartbeat_receipt,
    )
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor,
        GOAL_ID as goal,
        TODO_ID as work,
        _initialize_fixture_authority,
        _run_cli,
        _write_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider="file")
    turn = "parentless-bad-upgrade"
    _, bad_upgrade, _ = _append_legacy_main_orphaned_identity_upgrade_receipt(
        runtime,
        goal_id=goal,
        agent_id=actor,
        todo_id=work,
        turn_instance_id=turn,
        companion_details=_digest_review_details("replan-1111111111111111"),
        break_lineage=True,
    )
    log_path = rollout_event_log_path(runtime, goal)
    before = log_path.read_bytes()

    effective, repaired = repair_orphaned_identity_upgrade_heartbeat_receipt(
        runtime,
        goal_id=goal,
        agent_id=actor,
        turn_instance_id=turn,
    )
    assert repaired is False
    assert effective == bad_upgrade
    assert log_path.read_bytes() == before

    rc, failed = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        goal,
        "--agent-id",
        actor,
        "--todo-id",
        work,
        "--turn-instance-id",
        turn,
    )
    assert rc == 1
    assert failed["state"] == "blocked_health"
    assert log_path.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_recovers_legacy_main_rotated_digest_to_inline_review_and_settles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
) -> None:
    from loopx.control_plane.quota.error_codes import MalformedSettlementStateError
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor,
        GOAL_ID as goal,
        TODO_ID as work,
        _initialize_fixture_authority,
        _run_cli,
        _run_generated_cli,
        _spend_run_count,
        _write_fixture,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = "recover-rotated-digest-to-inline"
    rotated_obligation = "replan-2222222222222222"
    rotated_stop = "Stop after validating rotated obligation B."
    _, bad_upgrade, expected_effect_id = (
        _append_legacy_main_orphaned_identity_upgrade_receipt(
            runtime,
            goal_id=goal,
            agent_id=actor,
            todo_id=work,
            turn_instance_id=turn,
            companion_details=_digest_review_details("replan-1111111111111111"),
            upgrade_overlay={
                "semantic_replan_obligation_id": rotated_obligation,
                "semantic_replan_review_kind": "goal_outcome_uncovered",
                "semantic_replan_review_stop_condition": rotated_stop,
            },
        )
    )
    log_path = rollout_event_log_path(runtime, goal)
    before_bytes = log_path.read_bytes()

    with pytest.raises(MalformedSettlementStateError) as exc_info:
        read_heartbeat_settlement(
            runtime,
            goal_id=goal,
            agent_id=actor,
            todo_id=work,
            turn_instance_id=turn,
            registry_path=registry,
        )
    assert exc_info.value.diagnostic_code == "malformed_settlement_state"

    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        goal,
        "--agent-id",
        actor,
        "--todo-id",
        work,
        "--turn-instance-id",
        turn,
    )

    _run_cli(registry, runtime, *guard_args, "--dry-run")
    assert log_path.read_bytes() == before_bytes

    rc1, retry1 = _run_cli(registry, runtime, *guard_args)
    assert rc1 == 0, retry1
    assert retry1["ok"] is True
    assert retry1["state"] == "eligible"
    assert retry1["heartbeat_receipt"]["status"] == "upgraded"
    assert (
        retry1["heartbeat_receipt"]["settlement_identity"]["effect_id"]
        == expected_effect_id
    )
    events_after_r1 = load_rollout_events(log_path)
    assert len(events_after_r1) == 3
    repaired_event = events_after_r1[-1]
    assert repaired_event["causality"]["caused_by"] == bad_upgrade["event_id"]
    assert repaired_event["details"]["semantic_replan_obligation_id"] == rotated_obligation
    assert repaired_event["details"]["semantic_replan_review_stop_condition"] == rotated_stop
    assert "semantic_replan_review_stop_condition_sha256" not in repaired_event["details"]
    assert "semantic_replan_review_stop_contract_id" not in repaired_event["details"]

    readback = read_heartbeat_settlement(
        runtime,
        goal_id=goal,
        agent_id=actor,
        todo_id=work,
        turn_instance_id=turn,
        registry_path=registry,
    )
    assert readback is not None
    assert readback.identity.failure is None
    assert readback.admission_event_id == bad_upgrade["event_id"]
    assert readback.semantic_replan_guard == {
        "schema_version": "semantic_replan_guard_v0",
        "scope": "turn_guard",
        "selected_obligation_id": rotated_obligation,
        "selected_review_source": {
            "kind": "goal_outcome_uncovered",
            "stop_condition": rotated_stop,
        },
    }

    bytes_after_r1 = log_path.read_bytes()
    rc2, retry2 = _run_cli(registry, runtime, *guard_args)
    assert rc2 == 0, retry2
    assert retry2["ok"] is True
    assert retry2["state"] == "eligible"
    assert retry2["heartbeat_receipt"]["status"] == "replayed"
    assert (
        retry2["heartbeat_receipt"]["settlement_identity"]
        == retry1["heartbeat_receipt"]["settlement_identity"]
    )
    assert log_path.read_bytes() == bytes_after_r1

    (project / "validated-rotated-result.txt").write_text(
        "Validated delivery after rotated identity_upgrade cleanup.\n"
    )
    plan = retry2["interaction_contract"]["cli_channel"]["settlement_plan"]
    command = next(
        s["command_template"]
        for s in plan["ordered_steps"]
        if s["kind"] == "durable_writeback"
    )
    command = (
        command.replace("<validated_progress>", "recovered_rotated_identity_upgrade")
        .replace("<scale>", "single_surface")
        .replace("<outcome>", "outcome_progress")
    )
    command += (
        " --delivery-boundary semantic_closeout"
        " --autonomous-replan-recorded"
        " --repair-delta-kind successor_or_supersede"
        " --progress-result-class advanced"
        f" --progress-work-item-id {work}"
        " --progress-surface-id surface:rotated-review"
        " --progress-hypothesis-id hypothesis:rotated-review"
        " --progress-probe-kind probe:rotated-review"
        " --progress-evidence-id evidence:recovered-rotated-result"
        " --no-global-sync --suppress-external-sinks"
    )
    rc_wb, written = _run_generated_cli(command, registry_path=registry, cwd=project)
    assert rc_wb == 0, written
    if written["settlement_progress"]["state"] != "settled":
        spend_command = next(
            s["command_template"]
            for s in plan["ordered_steps"]
            if s["kind"] == "quota_spend"
        )
        rc_sp, spent = _run_generated_cli(
            spend_command, registry_path=registry, cwd=project
        )
        assert rc_sp == 0, spent
        assert spent["settlement_progress"]["state"] == "settled"
    assert _spend_run_count(runtime) == 1

    rc3, settled = _run_cli(registry, runtime, *guard_args)
    assert rc3 == 0, settled
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert (
        settled["heartbeat_receipt"]["settlement_identity"]
        == retry1["heartbeat_receipt"]["settlement_identity"]
    )


@pytest.mark.parametrize(
    ("parent_builder", "overlay_builder", "removed_fields", "expected_source_key"),
    [
        (
            _digest_review_details,
            lambda ob_id: {
                "semantic_replan_obligation_id": ob_id,
                "semantic_replan_review_kind": "periodic_review_due",
                "semantic_replan_review_stop_condition_sha256": "b" * 64,
            },
            ("semantic_replan_review_stop_contract_id",),
            "selected_review_source",
        ),
        (
            _digest_review_details,
            _capability_guard_details,
            (
                "semantic_replan_review_kind",
                "semantic_replan_review_stop_condition",
                "semantic_replan_review_stop_condition_sha256",
                "semantic_replan_review_stop_contract_id",
            ),
            "selected_capability_guard",
        ),
        (
            _capability_guard_details,
            _digest_review_details,
            (
                "semantic_replan_capability_id",
                "semantic_replan_gap_id",
                "semantic_replan_frontier_revision",
            ),
            "selected_review_source",
        ),
    ],
    ids=["default-digest-to-custom-digest", "review-to-capability", "capability-to-review"],
)
def test_upgrade_api_recovers_rotated_identity_upgrade_idempotently(
    tmp_path: Path,
    parent_builder: Callable[[str], dict[str, object]],
    overlay_builder: Callable[[str], dict[str, object]],
    removed_fields: tuple[str, ...],
    expected_source_key: str,
) -> None:
    rotated_obligation = "replan-2222222222222222"
    overlay = overlay_builder(rotated_obligation)
    _, _, expected_effect_id = _append_legacy_main_orphaned_identity_upgrade_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
        companion_details=parent_builder("replan-1111111111111111"),
        upgrade_overlay=overlay,
    )

    repaired, appended = upgrade_identityless_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=expected_effect_id,
        status="normal_run",
        summary="recover rotated identity_upgrade receipt",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            **overlay,
        },
    )
    assert appended is True
    assert repaired["details"]["settlement_effect_id"] == expected_effect_id
    assert repaired["details"]["semantic_replan_obligation_id"] == rotated_obligation
    for field in removed_fields:
        assert field not in repaired["details"]

    log_path = rollout_event_log_path(tmp_path, GOAL_ID)
    after_first = log_path.read_bytes()
    replayed, appended_again = upgrade_identityless_heartbeat_receipt(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_instance_id=TURN_ID,
        todo_id=TODO_ID,
        settlement_effect_id=expected_effect_id,
        status="normal_run",
        summary="replay after rotated repair",
        details={
            "stall_observation": "not_applicable",
            "todo_id": TODO_ID,
            **overlay,
        },
    )
    assert appended_again is False
    assert replayed == repaired
    assert log_path.read_bytes() == after_first

    readback = read_heartbeat_settlement(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        todo_id=TODO_ID,
        turn_instance_id=TURN_ID,
    )
    assert readback is not None
    assert readback.identity.failure is None
    assert readback.semantic_replan_guard is not None
    assert readback.semantic_replan_guard["selected_obligation_id"] == rotated_obligation
    assert expected_source_key in readback.semantic_replan_guard


def test_cli_recovers_prior_turn_rotated_digest_to_inline_without_blocked_health(
    tmp_path: Path,
) -> None:
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor,
        GOAL_ID as goal,
        TODO_ID as work,
        _initialize_fixture_authority,
        _run_cli,
        _write_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider="file")
    prior_turn = "prior-turn-rotated-digest-inline"
    rotated_obligation = "replan-2222222222222222"
    rotated_stop = "Stop after validating rotated prior Turn."
    _, _, expected_effect_id = _append_legacy_main_orphaned_identity_upgrade_receipt(
        runtime,
        goal_id=goal,
        agent_id=actor,
        todo_id=work,
        turn_instance_id=prior_turn,
        companion_details=_digest_review_details("replan-1111111111111111"),
        upgrade_overlay={
            "semantic_replan_obligation_id": rotated_obligation,
            "semantic_replan_review_kind": "periodic_review_due",
            "semantic_replan_review_stop_condition": rotated_stop,
        },
    )

    rc, result = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        goal,
        "--agent-id",
        actor,
        "--turn-instance-id",
        "fresh-followup-after-rotated",
    )
    assert rc == 0, result
    assert result["state"] != "blocked_health"
    assert (
        result["unsettled_host_turn_recovery"]["prior_turn_instance_id"]
        == prior_turn
    )
    repaired_prior = find_heartbeat_receipt(
        runtime, goal_id=goal, agent_id=actor, turn_instance_id=prior_turn
    )
    assert repaired_prior is not None
    assert repaired_prior["details"]["settlement_effect_id"] == expected_effect_id
    assert repaired_prior["details"]["semantic_replan_obligation_id"] == rotated_obligation
    assert repaired_prior["details"]["semantic_replan_review_stop_condition"] == rotated_stop
    assert "semantic_replan_review_stop_condition_sha256" not in repaired_prior["details"]


@pytest.mark.parametrize(
    ("case_id", "overlay_overrides", "bind_parent", "break_lineage"),
    [
        (
            "broken-lineage",
            {
                "semantic_replan_obligation_id": "replan-2222222222222222",
                "semantic_replan_review_kind": "goal_outcome_uncovered",
                "semantic_replan_review_stop_condition": "Short clause without causal proof.",
            },
            False,
            True,
        ),
        (
            "unattributable-digest",
            {
                "semantic_replan_obligation_id": "replan-2222222222222222",
                "semantic_replan_review_kind": "goal_outcome_uncovered",
                "semantic_replan_review_stop_condition": "Short clause with foreign digest.",
                "semantic_replan_review_stop_condition_sha256": "c" * 64,
            },
            False,
            False,
        ),
        (
            "invalid-post-repair-stop-length",
            {
                "semantic_replan_obligation_id": "replan-2222222222222222",
                "semantic_replan_review_kind": "goal_outcome_uncovered",
                "semantic_replan_review_stop_condition": "x" * 241,
            },
            False,
            False,
        ),
        (
            "already-bound-parent",
            {
                "semantic_replan_obligation_id": "replan-2222222222222222",
                "semantic_replan_review_kind": "goal_outcome_uncovered",
                "semantic_replan_review_stop_condition": "Short clause from bound parent.",
            },
            True,
            False,
        ),
    ],
)
def test_rotated_identity_upgrade_with_insufficient_proof_fails_closed(
    tmp_path: Path,
    case_id: str,
    overlay_overrides: dict[str, object],
    bind_parent: bool,
    break_lineage: bool,
) -> None:
    from loopx.control_plane.quota.heartbeat_receipt import (
        repair_orphaned_identity_upgrade_heartbeat_receipt,
    )
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor,
        GOAL_ID as goal,
        TODO_ID as work,
        _initialize_fixture_authority,
        _run_cli,
        _write_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider="file")
    turn = f"rotated-fail-closed-{case_id}"
    _, bad_upgrade, _ = _append_legacy_main_orphaned_identity_upgrade_receipt(
        runtime,
        goal_id=goal,
        agent_id=actor,
        todo_id=work,
        turn_instance_id=turn,
        companion_details=_digest_review_details("replan-1111111111111111"),
        upgrade_overlay=overlay_overrides,
        bind_parent=bind_parent,
        break_lineage=break_lineage,
    )
    log_path = rollout_event_log_path(runtime, goal)
    before = log_path.read_bytes()

    effective, repaired = repair_orphaned_identity_upgrade_heartbeat_receipt(
        runtime,
        goal_id=goal,
        agent_id=actor,
        turn_instance_id=turn,
    )
    assert repaired is False
    assert effective == bad_upgrade
    assert log_path.read_bytes() == before

    rc, failed = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        goal,
        "--agent-id",
        actor,
        "--todo-id",
        work,
        "--turn-instance-id",
        turn,
    )
    assert rc == 1
    assert failed["state"] == "blocked_health"
    assert log_path.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    ("astral_chars", "expect_recoverable"),
    [(120, True), (121, False)],
    ids=["utf16-240", "utf16-242"],
)
def test_cli_rotated_stop_condition_utf16_boundary_matches_ts_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    astral_chars: int,
    expect_recoverable: bool,
) -> None:
    from loopx.control_plane.quota.heartbeat_receipt import (
        repair_orphaned_identity_upgrade_heartbeat_receipt,
    )
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID as actor,
        GOAL_ID as goal,
        TODO_ID as work,
        _initialize_fixture_authority,
        _run_cli,
        _write_fixture,
    )

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _initialize_fixture_authority(project, runtime, provider=provider)
    turn = f"rotated-utf16-{provider}-{astral_chars * 2}"
    rotated_obligation = "replan-2222222222222222"
    stop_clause = "\U00020000" * astral_chars
    assert len(stop_clause) == astral_chars
    assert len(stop_clause.encode("utf-16-le")) // 2 == astral_chars * 2

    _, bad_upgrade, expected_effect_id = (
        _append_legacy_main_orphaned_identity_upgrade_receipt(
            runtime,
            goal_id=goal,
            agent_id=actor,
            todo_id=work,
            turn_instance_id=turn,
            companion_details=_digest_review_details("replan-1111111111111111"),
            upgrade_overlay={
                "semantic_replan_obligation_id": rotated_obligation,
                "semantic_replan_review_kind": "goal_outcome_uncovered",
                "semantic_replan_review_stop_condition": stop_clause,
            },
        )
    )
    log_path = rollout_event_log_path(runtime, goal)
    before = log_path.read_bytes()
    assert len(load_rollout_events(log_path)) == 2

    guard_args = (
        "quota",
        "should-run",
        "--codex-app",
        "--goal-id",
        goal,
        "--agent-id",
        actor,
        "--todo-id",
        work,
        "--turn-instance-id",
        turn,
    )

    if expect_recoverable:
        rc1, retry1 = _run_cli(registry, runtime, *guard_args)
        assert rc1 == 0, retry1
        assert retry1["state"] == "eligible"
        assert retry1["heartbeat_receipt"]["status"] == "upgraded"
        assert (
            retry1["heartbeat_receipt"]["settlement_identity"]["effect_id"]
            == expected_effect_id
        )
        assert len(load_rollout_events(log_path)) == 3
        readback = read_heartbeat_settlement(
            runtime,
            goal_id=goal,
            agent_id=actor,
            todo_id=work,
            turn_instance_id=turn,
            registry_path=registry,
        )
        assert readback is not None
        assert readback.identity.failure is None
        assert readback.admission_event_id == bad_upgrade["event_id"]
        assert readback.semantic_replan_guard == {
            "schema_version": "semantic_replan_guard_v0",
            "scope": "turn_guard",
            "selected_obligation_id": rotated_obligation,
            "selected_review_source": {
                "kind": "goal_outcome_uncovered",
                "stop_condition": stop_clause,
            },
        }
        bytes_after_r1 = log_path.read_bytes()
        rc2, retry2 = _run_cli(registry, runtime, *guard_args)
        assert rc2 == 0, retry2
        assert retry2["heartbeat_receipt"]["status"] == "replayed"
        assert log_path.read_bytes() == bytes_after_r1
    else:
        effective, repaired = repair_orphaned_identity_upgrade_heartbeat_receipt(
            runtime,
            goal_id=goal,
            agent_id=actor,
            turn_instance_id=turn,
        )
        assert repaired is False
        assert effective == bad_upgrade
        assert log_path.read_bytes() == before

        for _ in range(2):
            rc_fail, failed_cli = _run_cli(registry, runtime, *guard_args)
            assert rc_fail == 1
            assert failed_cli["state"] == "blocked_health"
            assert log_path.read_bytes() == before
            assert len(load_rollout_events(log_path)) == 2
