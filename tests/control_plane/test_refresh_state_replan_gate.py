"""Write-time gate: maintenance writebacks are rejected while replan is due."""

from __future__ import annotations

import json
import shlex
from copy import deepcopy
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.control_plane.status.autonomous_replan_projection import (
    AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD,
)
from loopx.control_plane.work_items.semantic_replan_writeback import (
    ReplanWritebackRejected,
    _obligation_was_created_by_current_completion,
    enforce_open_replan_writeback,
    qualify_replan_writeback,
    project_replan_writeback_rejection,
)
from loopx.state_refresh import refresh_state_run
from loopx.todos import add_goal_todo

GOAL_ID = "replan-gate-fixture"
AGENT_ID = "codex-replan-gate-agent"
OTHER_AGENT_ID = "codex-replan-gate-other-agent"
STATE_TEXT = "# Active Goal State\n"


def _completed_advancement_chain_state(count: int = 5) -> str:
    """Completed slices with explicit lineage and no succession warning.

    The final non-advancement anchor keeps this fixture focused on the outcome
    checkpoint rule instead of the independent completed-without-successor
    rule.
    """

    lines = ["## Agent Todo", ""]
    for index in range(count):
        successor_id = (
            f"todo_completed_slice_{index + 1}"
            if index < count - 1
            else "todo_completed_outcome_anchor"
        )
        lines.extend(
            [
                f"- [x] [P1] Completed bounded slice {index}.",
                (
                    "  <!-- loopx:todo "
                    f"todo_id=todo_completed_slice_{index} status=done "
                    "task_class=advancement_task "
                    f"claimed_by={AGENT_ID} successor_todo_ids={successor_id} "
                    f"completed_at=2026-08-13T11%3A{30 + index:02d}%3A00%2B08%3A00 -->"
                ),
            ]
        )
    lines.extend(
        [
            "- [x] [P2] Persist the completed-chain lineage anchor.",
            (
                "  <!-- loopx:todo "
                "todo_id=todo_completed_outcome_anchor status=done "
                "task_class=continuous_monitor "
                f"claimed_by={AGENT_ID} "
                "completed_at=2026-08-13T11%3A35%3A00%2B08%3A00 -->"
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def _completed_advancement_without_successor_state() -> str:
    return f"""\
## Agent Todo

- [x] [P0] Completed a bounded advancement slice.
  <!-- loopx:todo todo_id=todo_unsettled_completion status=done task_class=advancement_task claimed_by={AGENT_ID} no_followup=false completion_continuation=active_goal completion_turn_key=turn-unsettled completed_at=2026-08-13T11%3A30%3A00%2B08%3A00 -->
"""


def _terminal_no_followup_vision() -> dict:
    return {
        "state": "no_followup",
        "vision_patch": {"acceptance_summary": "The bounded goal is complete."},
        "path_delta": {
            "outcome": "stop",
            "evidence_refs": ["evidence:terminal-coverage"],
        },
    }


def _durable_runs(count: int) -> list[dict]:
    return [
        {
            "classification": "source_audit_progress",
            "generated_at": f"2026-08-13T00:{index:02d}:00+00:00",
            "agent_id": AGENT_ID,
            "progress_scope": "agent_lane",
            "delivery_outcome": "surface_only",
        }
        for index in reversed(range(count))
    ]


def _call_gate(
    *,
    runs: list[dict],
    progress_observation: dict | None = None,
) -> None:
    enforce_open_replan_writeback(
        newest_first_runs=runs,
        state_text=STATE_TEXT,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        progress_observation=progress_observation,
    )


def _current_obligation_id(
    runs: list[dict],
    *,
    state_text: str = STATE_TEXT,
) -> str:
    obligation, _ = qualify_replan_writeback(
        newest_first_runs=runs,
        state_text=state_text,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
    )
    assert obligation is not None
    return str(obligation["obligation_id"])


def test_maintenance_writeback_rejected_when_replan_due() -> None:
    with pytest.raises(ValueError) as exc:
        _call_gate(runs=_durable_runs(AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD))
    message = str(exc.value)
    assert "autonomous replan obligation" in message
    assert "typed semantic delta" in message
    assert "Host-projected replan context" in message


def test_typed_surface_delta_satisfies_periodic_obligation() -> None:
    semantic_delta = enforce_open_replan_writeback(
        newest_first_runs=_durable_runs(
            AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD
        ),
        state_text=STATE_TEXT,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "work_item_id": "todo-replan-slice",
            "surface_id": "surface-new",
            "result_class": "advanced",
            "evidence_ids": ["evidence-new-surface"],
        },
    )
    assert semantic_delta is not None
    assert semantic_delta["satisfying_outcomes"] == ["new_surface"]


def test_writeback_allowed_when_replan_not_due() -> None:
    _call_gate(runs=_durable_runs(5))


def test_turn_guard_without_replan_target_defers_new_obligation() -> None:
    runs = _durable_runs(AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD)

    semantic_delta = enforce_open_replan_writeback(
        newest_first_runs=runs,
        state_text=STATE_TEXT,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        guard_scoped=True,
        guard_semantic_replan_obligation_id=None,
    )

    assert semantic_delta is None


def test_turn_guard_keeps_its_exact_replan_target_strict() -> None:
    runs = _durable_runs(AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD)
    obligation_id = _current_obligation_id(runs)

    with pytest.raises(ReplanWritebackRejected):
        enforce_open_replan_writeback(
            newest_first_runs=runs,
            state_text=STATE_TEXT,
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
            guard_scoped=True,
            guard_semantic_replan_obligation_id=obligation_id,
        )


def _typed_repeat_runs() -> list[dict]:
    """Two consecutive identical blocked observations (external-wait stall).

    Mirrors the real external-wait pattern: the agent records the same typed
    ``blocked`` observation twice, which creates a ``typed_progress_repeat``
    replan obligation whose progress_baseline is the repeated observation.
    Newest-first order, like run history.
    """

    observation = {
        "schema_version": "typed_progress_observation_v0",
        "result_class": "blocked",
        "blocker_id": "blocker-external-review",
        "evidence_ids": ["evidence-external-review"],
    }
    return [
        {
            "classification": "state_refreshed",
            "generated_at": f"2026-08-13T12:0{index}:00+08:00",
            "agent_id": AGENT_ID,
            "progress_observation": dict(observation),
        }
        for index in (1, 0)
    ]


def test_repair_delta_claim_alone_cannot_close_typed_repeat_obligation() -> None:
    runs = _typed_repeat_runs()
    # Semantic-gate level: a writeback whose progress observation does not
    # carry a NEW typed semantic delta relative to the obligation baseline is
    # rejected. This pins enforce_open_replan_writeback; the full
    # repair-delta command path (blocker todo + --repair-delta-kind blocker)
    # is covered by the refresh_state_run regressions below.
    with pytest.raises(ValueError) as exc:
        enforce_open_replan_writeback(
            newest_first_runs=runs,
            state_text=STATE_TEXT,
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
        )
    message = str(exc.value)
    assert "autonomous replan obligation" in message
    assert "typed semantic delta" in message
    # Repeating the same baseline observation cannot close the replan either.
    with pytest.raises(ValueError, match="typed semantic delta"):
        enforce_open_replan_writeback(
            newest_first_runs=runs,
            state_text=STATE_TEXT,
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
            progress_observation={
                "schema_version": "typed_progress_observation_v0",
                "result_class": "blocked",
                "blocker_id": "blocker-external-review",
                "evidence_ids": ["evidence-external-review"],
            },
        )


def test_atomic_new_blocker_delta_closes_typed_repeat_obligation() -> None:
    runs = _typed_repeat_runs()
    # Semantic-gate level: a NEW typed blocker relative to the obligation
    # baseline is accepted in the same writeback. The full refresh_state_run
    # path (repair-delta evidence + persisted ack readback) is covered below.
    semantic_delta = enforce_open_replan_writeback(
        newest_first_runs=runs,
        state_text=STATE_TEXT,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "result_class": "blocked",
            "blocker_id": "blocker-new-external-review",
            "evidence_ids": ["evidence-new-external-review"],
        },
    )
    assert semantic_delta is not None
    assert semantic_delta["accepted"] is True
    assert semantic_delta["satisfying_outcomes"] == ["new_concrete_blocker"]


def _state_with_scoped_blocker_todo() -> str:
    return f"""\
## Agent Todo

- [ ] [P1] Blocked on the external review gate.
  <!-- loopx:todo todo_id=todo_scoped_blocker status=open task_class=blocker claimed_by={AGENT_ID} -->
"""


def _write_typed_repeat_runs_index(runs_index: Path) -> list[dict]:
    runs_index.parent.mkdir(parents=True)
    runs = _typed_repeat_runs()
    with runs_index.open("w", encoding="utf-8") as handle:
        for run in runs:
            handle.write(json.dumps(run) + "\n")
    return runs


def _call_refresh_state_run(
    *,
    registry_path: Path,
    runtime_root: Path,
    project: Path,
    progress_observation: dict | None = None,
) -> dict:
    return refresh_state_run(
        registry_path=registry_path,
        runtime_root_override=str(runtime_root),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="state_refreshed",
        recommended_action="Observe the fixture.",
        delivery_batch_scale="single_surface",
        delivery_outcome="surface_only",
        agent_id=AGENT_ID,
        autonomous_replan_recorded=True,
        repair_delta_kinds=["blocker"],
        progress_observation=progress_observation,
        dry_run=False,
        sync_global=False,
    )


def _replan_gate_tmp_fixture(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    project = tmp_path / "project"
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(_state_with_scoped_blocker_todo(), encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runs_index = (
        runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    )
    _write_typed_repeat_runs_index(runs_index)
    return registry_path, project, runtime_root, runs_index


def test_refresh_state_run_repair_delta_alone_rejected_without_new_typed_delta(
    tmp_path: Path,
) -> None:
    registry_path, project, runtime_root, runs_index = _replan_gate_tmp_fixture(
        tmp_path
    )
    before = runs_index.read_text().splitlines()
    # The documented recovery command path, negative side: a scoped blocker
    # todo plus --autonomous-replan-recorded --repair-delta-kind blocker, but
    # no NEW typed observation, must fail closed and must not append a run.
    with pytest.raises(ValueError, match="typed semantic delta"):
        _call_refresh_state_run(
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=project,
        )
    assert runs_index.read_text().splitlines() == before


def test_refresh_state_run_atomic_blocker_delta_persists_repair_and_semantic_evidence(
    tmp_path: Path,
) -> None:
    registry_path, project, runtime_root, runs_index = _replan_gate_tmp_fixture(
        tmp_path
    )
    result = _call_refresh_state_run(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
        progress_observation={
            "schema_version": "typed_progress_observation_v0",
            "result_class": "blocked",
            "blocker_id": "blocker-new-external-review",
            "evidence_ids": ["evidence-new-external-review"],
        },
    )
    assert result.get("ok") is True
    lines = runs_index.read_text().splitlines()
    assert len(lines) == len(_typed_repeat_runs()) + 1
    # Read the persisted run back: the blocker repair delta and the accepted
    # typed semantic delta must both be present in the durable record.
    json_path = Path(result["json_path"])
    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    ack = persisted.get("autonomous_replan_ack") or {}
    assert ack.get("recorded") is True
    assert (ack.get("delta_contract") or {}).get("delta_kinds") == ["blocker"]
    semantic_delta = ack.get("semantic_delta") or {}
    assert semantic_delta.get("accepted") is True
    assert semantic_delta.get("satisfying_outcomes") == ["new_concrete_blocker"]


def test_refresh_state_run_rejects_maintenance_writeback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    state = (
        project
        / ".codex"
        / "goals"
        / GOAL_ID
        / "ACTIVE_GOAL_STATE.md"
    )
    state.parent.mkdir(parents=True)
    state.write_text(STATE_TEXT, encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runs_index = (
        runtime_root
        / "goals"
        / GOAL_ID
        / "runs"
        / "index.jsonl"
    )
    runs_index.parent.mkdir(parents=True)
    with runs_index.open("w", encoding="utf-8") as handle:
        for run in _durable_runs(AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD):
            handle.write(json.dumps(run) + "\n")

    with pytest.raises(ValueError) as exc:
        refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            goal_id=GOAL_ID,
            project=project,
            state_file=None,
            classification="source_audit_progress",
            recommended_action="Observe the fixture.",
            delivery_batch_scale="single_surface",
            delivery_outcome="surface_only",
            agent_id=AGENT_ID,
            vision_unchanged_reason="无新攻击面，审计维持",
            dry_run=False,
            sync_global=False,
        )
    message = str(exc.value)
    assert "typed semantic delta" in message


def _prior_periodic_ack() -> dict:
    return {
        "classification": "autonomous_replan_recorded",
        "generated_at": "2026-08-13T11:26:03+08:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "semantic_delta": {
                "schema_version": "replan_semantic_delta_v0",
                "accepted": True,
                "outcomes": ["new_runnable_successor"],
                "satisfying_outcomes": ["new_runnable_successor"],
                "required_any_of": ["new_runnable_successor"],
                "obligation_id": "replan-85f352144255e4d9",
            },
        },
    }


def _open_vision_after_prior_ack() -> dict:
    return {
        "classification": "goal_vision_checkpoint",
        "generated_at": "2026-08-13T11:27:00+08:00",
        "agent_id": AGENT_ID,
        "agent_vision": {
            "schema_version": "goal_vision_replan_contract_v0",
            "agent_id": AGENT_ID,
            "state": "active",
            "vision_patch": {
                "vision_summary": "Qualify the active outcome.",
                "acceptance_summary": "Close the current outcome with evidence.",
                "advancement_policy": "repeat_until_closed",
                "replan_trigger_summary": "The current outcome path has drifted.",
            },
            "todo_delta": [],
        },
        "vision_checkpoint": {
            "schema_version": "vision_checkpoint_v0",
            "agent_id": AGENT_ID,
            "required": True,
            "satisfied": True,
            "decision": "unchanged",
            "triggers": [
                {
                    "kind": "material_delivery_outcome",
                    "delivery_outcome": "outcome_progress",
                }
            ],
        },
    }


def _state_with_other_agent_user_gate() -> str:
    return f"""\
## User Todo / Owner Review Reading Queue

- [ ] [P0] Review the other agent's independent delivery.
  <!-- loopx:todo todo_id=todo_other_agent_review status=open task_class=user_action bound_agent={OTHER_AGENT_ID} -->

## Agent Todo

- [ ] [P0] Continue the current agent's bounded implementation.
  <!-- loopx:todo todo_id=todo_current_agent_slice status=open task_class=advancement_task claimed_by={AGENT_ID} -->
"""


def _rotated_vision_runs() -> list[dict]:
    return [
        _open_vision_after_prior_ack(),
        _prior_periodic_ack(),
        *_durable_runs(AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD),
    ]


def test_writeback_scopes_other_agent_user_gate_like_quota() -> None:
    """A peer's user gate must not hide this agent's vision obligation."""

    obligation, semantic_delta = qualify_replan_writeback(
        newest_first_runs=[_open_vision_after_prior_ack()],
        state_text=_state_with_other_agent_user_gate(),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        registry_goal={
            "id": GOAL_ID,
            "status": "active",
            "coordination": {
                "agent_model": "peer_v1",
                "registered_agents": [AGENT_ID, OTHER_AGENT_ID],
            },
        },
    )

    assert obligation is not None
    assert semantic_delta is not None
    assert semantic_delta["accepted"] is False
    assert [trigger["kind"] for trigger in obligation["triggers"]] == [
        "vision_acceptance_gap",
        "vision_outcome_checkpoint_required",
    ]


def test_current_completion_source_identity_exempts_new_todo_obligation() -> None:
    obligation, semantic_delta = qualify_replan_writeback(
        newest_first_runs=[],
        state_text=_completed_advancement_without_successor_state(),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        completion_todo_id="todo_unsettled_completion",
        completion_turn_key="turn-unsettled",
    )

    assert obligation is None
    assert semantic_delta is None


def test_current_completion_exemption_rejects_peer_owned_source_item() -> None:
    assert not _obligation_was_created_by_current_completion(
        {
            "triggers": [
                {
                    "kind": "completed_advancement_without_successor",
                    "todo_id": "todo_peer_completion",
                }
            ]
        },
        agent_todo_items=[
            {
                "todo_id": "todo_peer_completion",
                "status": "done",
                "claimed_by": OTHER_AGENT_ID,
                "completion_turn_key": "turn-peer",
            }
        ],
        agent_id=AGENT_ID,
        completion_todo_id="todo_peer_completion",
        completion_turn_key="turn-peer",
    )


def test_rotated_vision_obligation_rejects_first_maintenance_writeback() -> None:
    """#3155: a prior periodic ACK cannot hide a new vision/frontier duty."""

    with pytest.raises(ValueError, match="required vision outcome"):
        enforce_open_replan_writeback(
            newest_first_runs=_rotated_vision_runs(),
            state_text=_completed_advancement_chain_state(),
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
        )


def test_rotated_vision_obligation_contains_all_three_acceptance_gaps() -> None:
    """The write gate sees quota's vision, checkpoint, and completed-chain truth."""

    obligation, semantic_delta = qualify_replan_writeback(
        newest_first_runs=_rotated_vision_runs(),
        state_text=_completed_advancement_chain_state(),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
    )

    assert semantic_delta is not None
    assert semantic_delta["accepted"] is False
    assert semantic_delta["outcomes"] == []
    assert obligation is not None
    assert [trigger["kind"] for trigger in obligation["triggers"]] == [
        "vision_acceptance_gap",
        "vision_outcome_checkpoint_required",
        "vision_outcome_checkpoint_required",
    ]
    assert obligation["triggers"][1]["text"].startswith(
        "a material milestone closed without a fresh evidence-linked"
    )
    assert obligation["triggers"][2]["text"].startswith(
        "a completed advancement Todo chain"
    )
    assert obligation["triggers"][2]["completed_todo_count"] == 5
    assert obligation["triggers"][2]["completed_todo_threshold"] == 5


def test_refresh_state_run_rejects_maintenance_after_vision_obligation_rotation(
    tmp_path: Path,
) -> None:
    """#3155 end to end: quota/frontier truth also governs physical writes."""

    project = tmp_path / "project"
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    state.write_text(_completed_advancement_chain_state(), encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state.relative_to(project)),
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runs_index = runtime_root / "goals" / GOAL_ID / "runs" / "index.jsonl"
    runs_index.parent.mkdir(parents=True)
    with runs_index.open("w", encoding="utf-8") as handle:
        for run in reversed(_rotated_vision_runs()):
            handle.write(json.dumps(run) + "\n")

    before = state.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="required vision outcome"):
        refresh_state_run(
            registry_path=registry_path,
            runtime_root_override=str(runtime_root),
            goal_id=GOAL_ID,
            project=project,
            state_file=None,
            classification="source_audit_progress",
            recommended_action="Observe the same frontier.",
            delivery_batch_scale="single_surface",
            delivery_outcome="surface_only",
            agent_id=AGENT_ID,
            dry_run=False,
            sync_global=False,
        )
    assert state.read_text(encoding="utf-8") == before
    assert not list(runs_index.parent.glob("*.json"))


def test_rotated_vision_obligation_rejects_successor_only_delta() -> None:
    with pytest.raises(ValueError, match="required vision outcome"):
        enforce_open_replan_writeback(
            newest_first_runs=_rotated_vision_runs(),
            state_text=STATE_TEXT,
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
        )


def _state_with_replan_successor(
    obligation_id: str,
    *,
    status: str = "open",
) -> str:
    marker = " " if status == "open" else "x"
    return _completed_advancement_chain_state() + "\n" + "\n".join(
        [
            f"- [{marker}] [P0] Execute the newly selected bounded direction.",
            (
                "  <!-- loopx:todo todo_id=todo_rotated_replan_successor "
                f"status={status} task_class=advancement_task "
                f"claimed_by={AGENT_ID} "
                "action_kind=inspect target_key=surface%3Aselected-bounded-slice "
                f"replan_obligation_id={obligation_id} "
                "updated_at=2026-08-13T11%3A28%3A00%2B08%3A00 -->"
            ),
        ]
    ) + "\n"


def test_current_obligation_runnable_successor_is_the_semantic_receipt() -> None:
    """The #3155 vision/frontier duty closes without a second ACK ritual."""

    from loopx.control_plane.work_items.semantic_replan_writeback import (
        qualify_replan_writeback,
    )

    obligation, _ = qualify_replan_writeback(
        newest_first_runs=_rotated_vision_runs(),
        state_text=_completed_advancement_chain_state(),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
    )
    assert obligation is not None

    after_transition, semantic_delta = qualify_replan_writeback(
        newest_first_runs=_rotated_vision_runs(),
        state_text=_state_with_replan_successor(obligation["obligation_id"]),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
    )

    assert after_transition is None
    assert semantic_delta["accepted"] is True
    assert semantic_delta["obligation_id"] == obligation["obligation_id"]


def test_rearmed_vision_obligation_accepts_only_its_fresh_successor_id() -> None:
    """Quota/write-time reduction agrees after an older generation was ACKed."""

    state_text = _completed_advancement_chain_state()
    original_vision = _open_vision_after_prior_ack()
    original_id = _current_obligation_id(
        [original_vision],
        state_text=state_text,
    )
    prior_ack = {
        "classification": "bounded_replan_progress",
        "generated_at": "2026-08-13T11:28:00+08:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "fixture",
            "semantic_delta": {
                "schema_version": "replan_semantic_delta_v0",
                "accepted": True,
                "outcomes": ["new_runnable_successor"],
                "satisfying_outcomes": ["new_runnable_successor"],
                "required_any_of": ["new_runnable_successor"],
                "obligation_id": original_id,
            },
        },
    }
    rearmed_vision = deepcopy(original_vision)
    rearmed_vision["generated_at"] = "2026-08-13T11:29:00+08:00"
    rearmed_runs = [rearmed_vision, prior_ack, original_vision]

    rearmed_id = _current_obligation_id(
        rearmed_runs,
        state_text=state_text,
    )
    remaining, semantic_delta = qualify_replan_writeback(
        newest_first_runs=rearmed_runs,
        state_text=_state_with_replan_successor(rearmed_id),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
    )

    assert rearmed_id != original_id
    assert remaining is None
    assert semantic_delta["accepted"] is True
    assert semantic_delta["obligation_id"] == rearmed_id


def test_completed_or_wrong_obligation_successor_cannot_close_rotated_duty() -> None:
    from loopx.control_plane.work_items.semantic_replan_writeback import (
        qualify_replan_writeback,
    )

    for state_text in (
        _state_with_replan_successor("replan-0000000000000000"),
        _state_with_replan_successor(
            "replan-0000000000000000",
            status="done",
        ),
    ):
        obligation, semantic_delta = qualify_replan_writeback(
            newest_first_runs=_rotated_vision_runs(),
            state_text=state_text,
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
        )
        assert obligation is not None
        assert semantic_delta is not None
        assert semantic_delta["accepted"] is False


def test_rotated_vision_obligation_accepts_fresh_evidence_linked_path() -> None:
    semantic_delta = enforce_open_replan_writeback(
        newest_first_runs=_rotated_vision_runs(),
        state_text=_completed_advancement_chain_state(),
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        agent_vision={
            "vision_patch": {
                "acceptance_summary": "Close the current outcome with evidence."
            },
            "path_delta": {
                "outcome": "replan",
                "evidence_refs": ["evidence:current-outcome"],
            },
        },
    )
    assert semantic_delta is not None
    assert semantic_delta["satisfying_outcomes"] == [
        "fresh_vision_path_outcome"
    ]


@pytest.mark.parametrize(
    ("progress_observation", "agent_vision", "expected_outcome"),
    [
        (
            {
                "schema_version": "typed_progress_observation_v0",
                "result_class": "blocked",
                "blocker_id": "blocker-current-path",
                "evidence_ids": ["evidence-current-blocker"],
            },
            None,
            "new_concrete_blocker",
        ),
        (
            {
                "schema_version": "typed_progress_observation_v0",
                "result_class": "exploration_exhausted",
                "coverage_scope_id": "coverage-current-goal",
                "coverage_complete": True,
                "evidence_ids": ["evidence-current-coverage"],
            },
            None,
            "coverage_backed_exploration_exhausted",
        ),
        (
            {
                "schema_version": "typed_progress_observation_v0",
                "result_class": "no_followup",
                "coverage_scope_id": "coverage-current-goal",
                "evidence_ids": ["evidence-current-coverage"],
            },
            _terminal_no_followup_vision(),
            "coverage_backed_no_followup",
        ),
    ],
)
def test_rotated_vision_obligation_accepts_terminal_progress(
    progress_observation: dict,
    agent_vision: dict | None,
    expected_outcome: str,
) -> None:
    runs = _rotated_vision_runs()
    state_text = _completed_advancement_chain_state()
    semantic_delta = enforce_open_replan_writeback(
        newest_first_runs=runs,
        state_text=state_text,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
        progress_observation=progress_observation,
        agent_vision=agent_vision,
    )

    assert semantic_delta is not None
    assert semantic_delta["satisfying_outcomes"] == [expected_outcome]


def test_writeback_gate_rejects_known_blocker_from_projected_history() -> None:
    observation = {
        "schema_version": "typed_progress_observation_v0",
        "result_class": "blocked",
        "surface_id": "surface-current-path",
        "work_item_id": "todo-current-path",
        "blocker_id": "blocker-current-path",
        "evidence_ids": ["evidence-current-blocker"],
    }
    runs = [{
        "agent_id": AGENT_ID,
        "generated_at": "2026-08-13T11:28:00+08:00",
        "progress_observation": observation,
    }, *_rotated_vision_runs()]
    with pytest.raises(ReplanWritebackRejected, match="blocker already recorded"):
        enforce_open_replan_writeback(
            newest_first_runs=runs,
            state_text=_completed_advancement_chain_state(),
            agent_id=AGENT_ID, goal_id=GOAL_ID,
            progress_observation={
                **observation, "work_item_id": "replan-0123456789abcdef",
                "evidence_ids": ["evidence-new-readback"],
            },
        )


def test_semantic_no_followup_cannot_replace_todo_lifecycle_settlement() -> None:
    with pytest.raises(ValueError, match="first run loopx todo complete"):
        enforce_open_replan_writeback(
            newest_first_runs=[],
            state_text=_completed_advancement_without_successor_state(),
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
            progress_observation={
                "schema_version": "typed_progress_observation_v0",
                "result_class": "no_followup",
                "coverage_scope_id": "coverage-current-goal",
                "evidence_ids": ["evidence-current-coverage"],
            },
            agent_vision=_terminal_no_followup_vision(),
        )


def test_todo_lifecycle_rejection_projects_exact_direct_settlement() -> None:
    with pytest.raises(ReplanWritebackRejected) as captured:
        enforce_open_replan_writeback(
            newest_first_runs=[],
            state_text=_completed_advancement_without_successor_state(),
            agent_id=AGENT_ID,
            goal_id=GOAL_ID,
        )

    transition = project_replan_writeback_rejection(
        captured.value,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )

    assert transition["resolution_mode"] == "todo_lifecycle_settlement"
    assert transition["triggers"] == [
        {
            "kind": "completed_advancement_without_successor",
                "todo_id": "todo_unsettled_completion",
                "completion_turn_key": "turn-unsettled",
                "completion_identity_source": "turn_settlement",
            }
    ]
    actions = transition["next_cli_actions"]
    assert "--todo-id todo_unsettled_completion" in actions[0]
    assert "--turn-instance-id turn-unsettled" in actions[0]
    assert "--agent-id codex-replan-gate-agent" in actions[0]
    assert all("refresh-state" not in action for action in actions)
    assert all("spend-slot" not in action for action in actions)


def test_todo_complete_then_refresh_cli_projects_direct_settlement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "\n".join(
            [
                "---",
                f"goal_id: {GOAL_ID}",
                "updated_at: 2026-08-22T00:00:00+00:00",
                "---",
                "",
                "## User Todo / Owner Review Reading Queue",
                "",
                "## Agent Todo",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(runtime_root),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": state.name,
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert cli_main(
        [
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--completion-identity-key",
            "local_completion_00000000000000000000000000000000",
        ]
    ) == 1
    unsupported_identity = json.loads(capsys.readouterr().out)
    assert unsupported_identity["error"] == (
        "--completion-identity-key is supported only by todo complete reentry"
    )
    todos = [
        add_goal_todo(
            registry_path=registry_path,
            goal_id=GOAL_ID,
            role="agent",
            text=f"Complete terminal slice {index}.",
            task_class="advancement_task",
            claimed_by=AGENT_ID,
        )
        for index in (1, 2)
    ]

    for todo in todos:
        assert cli_main(
            [
                "--registry",
                str(registry_path),
                "--format",
                "json",
                "todo",
                "complete",
                "--goal-id",
                GOAL_ID,
                "--todo-id",
                str(todo["todo_id"]),
                "--role",
                "agent",
                "--agent-id",
                AGENT_ID,
                "--evidence",
                f"validated terminal slice {todo['todo_id']}",
            ]
        ) == 0
        capsys.readouterr()

    exit_code = cli_main(
        [
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--progress-scope",
            "agent_lane",
            "--classification",
            "terminal_delivery_closeout",
            "--delivery-batch-scale",
            "single_surface",
            "--delivery-outcome",
            "surface_only",
            "--no-global-sync",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    transition = payload["replan_transition"]
    assert transition["resolution_mode"] == "todo_lifecycle_settlement"
    assert "Required transition:" in payload["error"]
    projected_ids = {trigger["todo_id"] for trigger in transition["triggers"]}
    expected_ids = {str(todo["todo_id"]) for todo in todos}
    assert projected_ids == expected_ids
    assert all(
        trigger["completion_identity_source"] == "unscoped_completion"
        and str(trigger["completion_turn_key"]).startswith("local_completion_")
        for trigger in transition["triggers"]
    )
    actions = transition["next_cli_actions"]
    for todo_id in expected_ids:
        assert any(f"--todo-id {todo_id}" in action for action in actions)
        assert f"--todo-id {todo_id}" in payload["error"]
    lifecycle_actions = [
        action for action in actions if "loopx todo complete" in action
    ]
    assert len(lifecycle_actions) == 2
    assert all("--completion-identity-key" in action for action in lifecycle_actions)
    assert all("--turn-instance-id" not in action for action in lifecycle_actions)
    assert all("refresh-state" not in action for action in actions)
    assert all("spend-slot" not in action for action in actions)

    for action in lifecycle_actions:
        tokens = shlex.split(action)
        loopx_index = tokens.index("loopx")
        assert cli_main(
            [
                "--registry",
                str(registry_path),
                "--format",
                "json",
                *tokens[loopx_index + 1 :],
            ]
        ) == 0
        settled = json.loads(capsys.readouterr().out)
        assert settled["completion_continuation"] == "no_followup"
        assert settled["completion_recovery"] == (
            "lifecycle_reentry_terminal_closeout"
        )

    quota_action = next(action for action in actions if "quota should-run" in action)
    quota_tokens = shlex.split(quota_action)
    loopx_index = quota_tokens.index("loopx")
    terminal_exit = cli_main(
        [
            "--registry",
            str(registry_path),
            *quota_tokens[loopx_index + 1 :],
        ]
    )
    terminal_quota = json.loads(capsys.readouterr().out)
    assert terminal_exit == 1
    assert terminal_quota["should_run"] is False
    assert terminal_quota["effective_action"] == "terminal_no_followup"


def test_legacy_unscoped_completion_projects_executable_identity_repair(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\n"
        f"goal_id: {GOAL_ID}\n"
        "updated_at: 2026-08-22T00:00:00+00:00\n"
        "---\n\n"
        "## User Todo / Owner Review Reading Queue\n\n"
        "## Agent Todo\n\n"
        "- [x] [P0] Complete legacy terminal slice.\n"
        "  <!-- loopx:todo todo_id=todo_legacy_unscoped status=done "
        "task_class=advancement_task "
        f"claimed_by={AGENT_ID} no_followup=false "
        "completion_continuation=active_goal "
        "completed_at=2026-08-22T00%3A00%3A00%2B00%3A00 -->\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "common_runtime_root": str(tmp_path / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": state.name,
                        "adapter": {
                            "kind": "fixture_connected_delivery_v0",
                            "status": "connected-delivery",
                        },
                        "coordination": {
                            "agent_model": "peer_v1",
                            "registered_agents": [AGENT_ID],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert cli_main(
        [
            "--registry",
            str(registry_path),
            "--format",
            "json",
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--progress-scope",
            "agent_lane",
            "--classification",
            "terminal_delivery_closeout",
            "--delivery-batch-scale",
            "single_surface",
            "--delivery-outcome",
            "surface_only",
            "--no-global-sync",
        ]
    ) == 1
    rejection = json.loads(capsys.readouterr().out)
    transition = rejection["replan_transition"]
    assert transition["triggers"] == [
        {
            "kind": "completed_advancement_without_successor",
            "todo_id": "todo_legacy_unscoped",
            "completion_turn_key": transition["triggers"][0][
                "completion_turn_key"
            ],
            "completion_identity_source": "unscoped_completion",
        }
    ]
    completion_key = transition["triggers"][0]["completion_turn_key"]
    assert str(completion_key).startswith("local_completion_")
    action = next(
        item for item in transition["next_cli_actions"] if "loopx todo complete" in item
    )
    action_tokens = shlex.split(action)
    loopx_index = action_tokens.index("loopx")
    assert cli_main(
        [
            "--registry",
            str(registry_path),
            "--format",
            "json",
            *action_tokens[loopx_index + 1 :],
        ]
    ) == 0
    repaired = json.loads(capsys.readouterr().out)

    assert repaired["completion_continuation"] == "no_followup"
    assert repaired["completion_recovery"] == (
        "lifecycle_reentry_terminal_closeout"
    )
    assert f"completion_turn_key={completion_key}" in state.read_text(encoding="utf-8")


def test_persisted_semantic_no_followup_ack_cannot_retire_todo_gap() -> None:
    state_text = _completed_advancement_without_successor_state()
    obligation_id = _current_obligation_id([], state_text=state_text)
    semantic_ack = {
        "classification": "bounded_replan_terminal",
        "generated_at": "2026-08-13T12:00:00+08:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "refresh_state_semantic_delta",
            "semantic_delta": {
                "schema_version": "replan_semantic_delta_v0",
                "accepted": True,
                "outcomes": ["coverage_backed_no_followup"],
                "satisfying_outcomes": ["coverage_backed_no_followup"],
                "required_any_of": ["coverage_backed_no_followup"],
                "obligation_id": obligation_id,
            },
        },
    }

    remaining, _ = qualify_replan_writeback(
        newest_first_runs=[semantic_ack],
        state_text=state_text,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
    )

    assert remaining is not None
    assert remaining["triggers"][0]["kind"] == (
        "completed_advancement_without_successor"
    )
    assert "loopx todo complete --no-follow-up" in remaining["recommended_action"]
    assert "do not invent a user gate" in remaining["recommended_action"]


@pytest.mark.parametrize(
    "outcome",
    [
        "fresh_vision_path_outcome",
        "new_concrete_blocker",
        "coverage_backed_exploration_exhausted",
        "coverage_backed_no_followup",
    ],
)
def test_matching_non_successor_ack_clears_rotated_vision_obligation(
    outcome: str,
) -> None:
    runs = _rotated_vision_runs()
    state_text = _completed_advancement_chain_state()
    obligation_id = _current_obligation_id(runs, state_text=state_text)
    semantic_ack = {
        "classification": "bounded_replan_progress",
        "generated_at": "2026-08-13T12:00:00+08:00",
        "agent_id": AGENT_ID,
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "refresh_state_semantic_delta",
            "semantic_delta": {
                "schema_version": "replan_semantic_delta_v0",
                "accepted": True,
                "outcomes": [outcome],
                "satisfying_outcomes": [outcome],
                "required_any_of": [
                    "fresh_vision_path_outcome",
                    "new_runnable_successor",
                    "new_concrete_blocker",
                    "coverage_backed_exploration_exhausted",
                    "coverage_backed_no_followup",
                ],
                "obligation_id": obligation_id,
            },
        },
    }

    remaining, _ = qualify_replan_writeback(
        newest_first_runs=[semantic_ack, *runs],
        state_text=state_text,
        agent_id=AGENT_ID,
        goal_id=GOAL_ID,
    )

    assert remaining is None


@pytest.mark.parametrize("threshold", [2, 3])
def test_writeback_uses_configured_completion_cadence(threshold: int) -> None:
    state = _completed_advancement_chain_state(threshold)
    arguments = {
        "newest_first_runs": [], "state_text": state,
        "agent_id": AGENT_ID, "goal_id": GOAL_ID,
    }
    default_obligation, _ = qualify_replan_writeback(**arguments)
    assert default_obligation is None
    goal = {"execution_profile": {"replan_after_completed_todos": threshold}}
    obligation, _ = qualify_replan_writeback(**arguments, registry_goal=goal)
    assert obligation is not None
    assert any(
        trigger.get("kind") == "vision_outcome_checkpoint_required"
        and trigger.get("completed_todo_count") == threshold
        and trigger.get("completed_todo_threshold") == threshold
        for trigger in obligation["triggers"]
    )
    with pytest.raises(ReplanWritebackRejected):
        enforce_open_replan_writeback(**arguments, registry_goal=goal)
