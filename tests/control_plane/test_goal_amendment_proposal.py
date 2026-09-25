"""Stage 2 governed goal amendment proposal admission tests.

Stage 2 is proposal only: admission validates and retains
``goal_amendment_proposal_v0`` with zero canonical effect — no goal state
write, no frontier change, no append into the canonical state event log.
The tests below prove both directions: valid proposals are retained
(append-only journal, idempotent replay), and every admission-blocking
defect fails closed without retaining anything.

Causal binding coverage: the fixture state carries real parsed Todo rows
and the fixture replan ids are derived through
``ensure_replan_novelty_policy``, so an admission never succeeds on a
merely well-shaped id — it must name an actual open obligation of the
same Goal and actual open Todos of that Goal.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.effect_runtime import (
    EffectRuntimeRejected,
    effect_runtime_result,
)
from loopx.control_plane.goals.goal_amendment_proposal import (
    admit_goal_amendment_proposal,
    read_goal_amendment_proposal_journal,
)
from loopx.control_plane.goals.shared_goal_alignment import (
    project_shared_goal_alignment,
)
from loopx.control_plane.runtime.time import chronology_key
from loopx.control_plane.status.autonomous_replan_projection import (
    autonomous_replan_obligation_from_runs,
)
from loopx.control_plane.todos.active_state_todo_parser import (
    parse_active_state_todos,
)
from loopx.history import load_index

GOAL_ID = "goal-stage2"
OTHER_GOAL_ID = "goal-stage2-peer"
AGENTS = ("agent-a", "agent-b")
EVENT_LOG_NAME = "events.jsonl"
MISMATCHED_DIGEST = "sha256:" + "a" * 64
RUNS_LEDGER_RELATIVE = Path("goals") / GOAL_ID / "runs" / "index.jsonl"
_DEFAULT_RUNS: Any = object()
_UNSET: Any = object()

STATE_HEADER_LINES = [
    "---",
    "status: active",
    "updated_at: 2026-09-01T00:00:00+00:00",
    "---",
    "",
    "# Stage 2 Amendment Fixture",
    "",
    "## Agent Todo",
    "",
]


def _todo_lines(specs: list[dict[str, str]]) -> list[str]:
    lines: list[str] = []
    for spec in specs:
        tokens = " ".join(f"{key}={value}" for key, value in spec.items())
        lines.append(
            f"- [{'x' if spec.get('done') else ' '}] [{spec.get('priority', 'P1')}] {spec['text']}"
        )
        lines.append(f"  <!-- loopx:todo {tokens} -->")
    return lines


def _default_todo_specs() -> list[dict[str, str]]:
    return [
        {
            "todo_id": "todo_stage2_a",
            "text": "Continue the agent-a claimed amendment-preamble slice.",
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "run",
            "claimed_by": "agent-a",
            "priority": "P0",
        },
        {
            "todo_id": "todo_stage2_b",
            "text": "Pick the amendment's unclaimed acceptance slice after claiming it.",
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "test",
            "priority": "P1",
        },
        {
            "todo_id": "todo_stage2_peer",
            "text": "Peer-held slice an amendment may legitimately affect.",
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "fix",
            "claimed_by": "agent-b",
            "priority": "P1",
        },
        {
            "todo_id": "todo_stage2_done",
            "text": "Delivered slice stays outside the open causal inventory.",
            "status": "done",
            "task_class": "advancement_task",
            "action_kind": "run",
            "claimed_by": "agent-b",
            "priority": "P1",
            "done": "true",
        },
    ]


def _other_goal_todo_specs() -> list[dict[str, str]]:
    return [
        {
            "todo_id": "todo_stage2_other",
            "text": "Belongs to the sibling Goal, never to this Goal's inventory.",
            "status": "open",
            "task_class": "advancement_task",
            "action_kind": "run",
            "claimed_by": "agent-b",
            "priority": "P2",
        },
    ]


def _stall_runs(
    agent_id: str | None = "agent-a",
    *,
    count: int = 2,
    start_minute: int = 0,
    goal_id: str = GOAL_ID,
    hypothesis: str = "hypothesis-stage2",
) -> list[dict[str, object]]:
    """Typed blocked progress runs on one agent lane — the real producer shape.

    Each run carries the typed ``progress_observation`` the quota run
    ledger persists (same normalization contract as
    ``test_progress_observation``): two consecutive equivalent blocked
    observations are exactly what ``typed_progress_repeat_trigger`` needs,
    so the derived obligation id is the one the status/quota producers
    would project for this history. ``hypothesis`` shifts the observation
    fingerprint when two goals must not derive the same obligation id
    (obligation identity is fingerprint-scoped, not goal-id-scoped).
    """

    runs: list[dict[str, object]] = []
    for index in range(count):
        run: dict[str, object] = {
            "generated_at": (f"2026-09-01T00:{start_minute + index:02d}:00+00:00"),
            "goal_id": goal_id,
            "classification": "bounded_replan_progress",
            "turn_instance_id": f"turn_stage2_{start_minute + index}",
            "progress_observation": {
                "schema_version": "typed_progress_observation_v0",
                "result_class": "blocked",
                "surface_id": "surface-stage2",
                "hypothesis_id": hypothesis,
                "probe_kind": "probe-stage2",
                "evidence_ids": ["evidence-stage2"],
            },
        }
        if agent_id:
            run["agent_id"] = agent_id
        runs.append(run)
    return runs


def _ack_run(
    obligation_id: str,
    *,
    generated_at: str = "2026-09-01T01:00:00+00:00",
    agent_id: str | None = "agent-a",
) -> dict[str, object]:
    """A settlement ack run — the real product of a refresh-state close.

    The replanning refresh records the accepted semantic delta naming the
    settled obligation id, and its own progress observation moves to the
    freshly replanned hypothesis (a new fingerprint): the old stall streak
    no longer repeats, and the monitor/periodic scans stop at the ack, so
    the derived obligation disappears exactly as it does in production
    after a real replan settlement.
    """

    run: dict[str, object] = {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": "autonomous_replan_recorded",
        "turn_instance_id": "turn_stage2_ack",
        "autonomous_replan_ack": {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "refresh_state",
            "semantic_delta": {
                "schema_version": "replan_semantic_delta_v0",
                "accepted": True,
                "obligation_id": obligation_id,
                "outcomes": ["new_runnable_successor"],
            },
        },
        "progress_observation": {
            "schema_version": "typed_progress_observation_v0",
            "result_class": "advanced",
            "surface_id": "surface-stage2",
            "hypothesis_id": "hypothesis.stage2.replanned",
            "probe_kind": "probe-stage2",
            "evidence_ids": ["evidence-stage2-successor"],
        },
    }
    if agent_id:
        run["agent_id"] = agent_id
    return run


def _append_runs(
    paths: dict[str, Path],
    runs: list[dict[str, object]],
    *,
    goal_id: str = GOAL_ID,
) -> None:
    ledger = paths["runtime"] / "goals" / goal_id / "runs" / "index.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as stream:
        for run in runs:
            stream.write(json.dumps(run, sort_keys=True) + "\n")


def _newest_first_runs(
    paths: dict[str, Path],
    *,
    goal_id: str = GOAL_ID,
) -> list[dict[str, Any]]:
    runs, _ = load_index(paths["runtime"] / "goals" / goal_id / "runs" / "index.jsonl")
    return [
        run
        for _, run in sorted(
            enumerate(runs),
            key=lambda item: (
                *chronology_key(item[1].get("generated_at")),
                item[0],
            ),
            reverse=True,
        )
    ]


def _derived_obligation(
    paths: dict[str, Path],
    *,
    agent_id: str | None = "agent-a",
    goal_id: str = GOAL_ID,
) -> dict[str, Any]:
    """Derive the open obligation straight from the fixture run history.

    This is the same read-only bound status-projection entry point the
    adapter uses, so the expected id is not hand-minted: admission must
    bind to exactly what the quota run-history ledger derives.
    """

    obligation = autonomous_replan_obligation_from_runs(
        _newest_first_runs(paths, goal_id=goal_id),
        agent_todos=None,
        agent_id=agent_id,
    )
    assert isinstance(obligation, dict), (
        "fixture run history must derive an open obligation; check the "
        "typed progress observation fields are still intact"
    )
    return obligation


def _goal_state_text(specs: list[dict[str, str]]) -> str:
    return "\n".join([*STATE_HEADER_LINES, *_todo_lines(specs)]) + "\n"


def _write_fixture(
    root: Path,
    *,
    with_other_goal: bool = False,
    runs: Any = _DEFAULT_RUNS,
    other_goal_runs: Any = _DEFAULT_RUNS,
) -> dict[str, Path]:
    project = root / "project"
    runtime = root / "runtime"

    def _goal_state(goal_id: str, specs: list[dict[str, str]]) -> tuple[Path, str]:
        state_relative = Path(".codex") / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
        state_file = project / state_relative
        state_file.parent.mkdir(parents=True)
        state_file.write_text(_goal_state_text(specs), encoding="utf-8")
        _assert_fixture_todos_parsed(state_file, specs)
        return state_file, str(state_relative)

    state_file, state_relative = _goal_state(GOAL_ID, _default_todo_specs())
    goals = [
        {
            "id": GOAL_ID,
            "domain": "shared-goal-alignment-stage2",
            "status": "active",
            "repo": str(project),
            "state_file": state_relative,
            "quota": {"compute": 1.0, "window_hours": 24},
            "coordination": {
                "agent_model": "peer_v1",
                "registered_agents": list(AGENTS),
            },
        }
    ]
    other_state_file: Path | None = None
    if with_other_goal:
        other_state_file, other_state_relative = _goal_state(
            OTHER_GOAL_ID, _other_goal_todo_specs()
        )
        goals.append(
            {
                "id": OTHER_GOAL_ID,
                "domain": "shared-goal-alignment-stage2-peer",
                "status": "active",
                "repo": str(project),
                "state_file": other_state_relative,
                "quota": {"compute": 1.0, "window_hours": 24},
                "coordination": {
                    "agent_model": "peer_v1",
                    "registered_agents": list(AGENTS),
                },
            }
        )

    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "common_runtime_root": str(runtime),
                "goals": goals,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    runtime.mkdir(parents=True, exist_ok=True)

    # The quota run-history ledger is the causal authority: by default the
    # fixture carries a real open obligation (typed stall runs), and each
    # test opts into empty, closed, or forged histories explicitly.
    if runs is _DEFAULT_RUNS:
        runs = _stall_runs()
    assert isinstance(runs, list)
    effective_runs = runs
    ledger = runtime / RUNS_LEDGER_RELATIVE
    if effective_runs:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("w", encoding="utf-8") as stream:
            for run in effective_runs:
                stream.write(json.dumps(run, sort_keys=True) + "\n")
    if with_other_goal and other_goal_runs is _DEFAULT_RUNS:
        other_goal_runs = _stall_runs(goal_id=OTHER_GOAL_ID)
    if with_other_goal and other_goal_runs:
        assert isinstance(other_goal_runs, list)
        other_ledger = runtime / "goals" / OTHER_GOAL_ID / "runs" / "index.jsonl"
        other_ledger.parent.mkdir(parents=True, exist_ok=True)
        with other_ledger.open("w", encoding="utf-8") as stream:
            for run in other_goal_runs:
                stream.write(json.dumps(run, sort_keys=True) + "\n")

    paths = {
        "project": project,
        "runtime": runtime,
        "registry": registry_path,
        "state_file": state_file,
    }
    if other_state_file is not None:
        paths["other_state_file"] = other_state_file
    return paths


def _assert_fixture_todos_parsed(
    state_file: Path,
    todo_specs: list[dict[str, str]],
) -> None:
    parsed = parse_active_state_todos(
        state_file.read_text(encoding="utf-8"),
        item_limit=None,
    )
    items = parsed.get("agent_todos", {}).get("items", [])
    parsed_ids = {item.get("todo_id") for item in items}
    for spec in todo_specs:
        assert spec.get("todo_id") in parsed_ids, (
            f"fixture todo {spec.get('todo_id')} was silently ignored by the "
            "parser; check every metadata token exists in the schema"
        )




def _derived_source_basis(paths: dict[str, Path]) -> dict[str, object]:
    """Project the live Stage 1 source basis the proposal binds against."""

    alignment = project_shared_goal_alignment(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        project=paths["project"],
    )
    basis = alignment["source_basis"]
    assert isinstance(basis, dict)
    return basis


def _proposal(
    paths: dict[str, Path],
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    # Default base binds to the live derived basis: equal sequence AND equal
    # source basis digest, exactly like a proposer that just re-read the
    # Stage 1 projection before proposing. The replan id defaults to the
    # real one derived from the fixture run history by the status
    # projection entry point — the same id the adapter re-derives at
    # submit time. Tests that forge or blank the id pass it as an override
    # so the default derivation is skipped (it would assert on histories
    # that legitimately derive nothing).
    overrides = dict(overrides) if overrides else {}
    replan_id = overrides.pop("replan_obligation_id", _UNSET)
    if replan_id is _UNSET:
        replan_id = _derived_obligation(paths)["obligation_id"]
    basis = _derived_source_basis(paths)
    proposal: dict[str, object] = {
        "schema_version": "goal_amendment_proposal_v0",
        "proposal_id": "gap_stage2_001",
        "goal_id": GOAL_ID,
        "proposer_agent_id": "agent-a",
        "amendment_class": "shared_acceptance",
        # The proposal declares the basis type it was produced against — a
        # real proposer reads this verbatim from the Stage 1 projection, and
        # the reducer validates sequence producibility against this claim
        # (not against the goal's current derived basis).
        "base_revision_basis": basis["revision_basis"],
        "base_state_event_basis_sequence": basis["state_event_basis_sequence"],
        "base_source_basis_digest": basis["source_basis_digest"],
        "retained": ["original outcome remains unchanged"],
        "changed": ["acceptance now requires the recovered receipt"],
        "stopped": [],
        "evidence_refs": ["evidence:evt_stage2_001"],
        "affected_todo_ids": ["todo_stage2_a", "todo_stage2_b"],
        "replan_obligation_id": replan_id,
    }
    proposal.update(overrides)
    return proposal


def _admit(
    paths: dict[str, Path],
    proposal: dict[str, object],
):
    return admit_goal_amendment_proposal(
        proposal=proposal,
        project=paths["project"],
    )


def _canonical_tree_snapshot(paths: dict[str, Path]) -> dict[str, bytes]:
    """Snapshot every runtime byte except the proposal journal sidecars.

    Admission must leave the state event log (the basis sequence carrier),
    the goal state file, and every other runtime artifact untouched; only
    ``amendment-proposals/`` and the advisory lock sidecars may move.
    """

    snapshot: dict[str, bytes] = {}
    for candidate in sorted(paths["runtime"].rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(paths["runtime"]).as_posix()
        if "/amendment-proposals/" in relative:
            continue
        snapshot[relative] = candidate.read_bytes()
    return snapshot


def _proposal_journal(paths: dict[str, Path]) -> Path:
    return (
        paths["runtime"] / "goals" / GOAL_ID / "amendment-proposals" / "journal.jsonl"
    )


def _journal_rows(paths: dict[str, Path]) -> list[dict[str, object]]:
    return read_goal_amendment_proposal_journal(
        runtime_root=paths["runtime"],
        goal_id=GOAL_ID,
    )


def test_admits_and_retains_a_well_formed_proposal(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    record = _admit(paths, _proposal(paths))

    assert record["schema_version"] == "goal_amendment_proposal_admission_v0"
    assert record["proposal_id"] == "gap_stage2_001"
    assert record["goal_id"] == GOAL_ID
    assert record["proposer_agent_id"] == "agent-a"
    assert record["amendment_class"] == "shared_acceptance"
    assert record["admission"] == "admitted"
    assert record["admission_facts"] == ["base_source_basis_unverifiable"]
    assert record["canonical_effect"] == "none"
    assert record["journal_append_sequence"] == 1
    assert record["recorded_at"]
    rows = _journal_rows(paths)
    assert [row["proposal_id"] for row in rows] == ["gap_stage2_001"]


def test_admission_binds_the_authority_derived_obligation_id(
    tmp_path: Path,
) -> None:
    # The retained replan id is the one the run-history derivation
    # produced — never a free-standing well-shaped string. The derivation
    # helper used here is the same read-only status projection entry point
    # the adapter calls at submit time.
    paths = _write_fixture(tmp_path)

    record = _admit(paths, _proposal(paths))

    expected_id = _derived_obligation(paths)["obligation_id"]
    assert record["replan_obligation_id"] == expected_id
    assert record["admission"] == "admitted"


def test_unscoped_goal_obligation_folds_into_a_deterministic_peer_lane(
    tmp_path: Path,
) -> None:
    # A goal-level (unattributed) stall history derives one unscoped
    # obligation. The Python authority folds autonomous_replan_scope_
    # decision into the typed inventory: an unscoped obligation is
    # deterministically assigned to exactly one registered peer — one
    # lane admits, the other fails closed.
    paths = _write_fixture(
        tmp_path,
        runs=_stall_runs(agent_id=None),
    )
    unscoped = _derived_obligation(paths, agent_id=None)
    assert not unscoped.get("agent_id")

    unscoped_id = unscoped["obligation_id"]
    statuses = []
    for agent_id in AGENTS:
        proposal = _proposal(
            paths,
            {
                "proposal_id": f"gap_stage2_unscoped_{agent_id[-1]}",
                "replan_obligation_id": unscoped_id,
                "proposer_agent_id": agent_id,
            },
        )
        try:
            _admit(paths, proposal)
            statuses.append("admitted")
        except ValueError as exc:
            assert "bound to another agent lane" in str(exc)
            statuses.append("rejected")
    assert sorted(statuses) == ["admitted", "rejected"]


def test_nonexistent_replan_obligation_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="does not match an open replan obligation"):
        _admit(
            paths,
            _proposal(
                paths,
                {"replan_obligation_id": "replan-deadbeefdeadbeef"},
            ),
        )

    assert _journal_rows(paths) == []


def test_settlement_ack_run_closes_the_derived_obligation(
    tmp_path: Path,
) -> None:
    # The real close path: refresh-state appends an autonomous_replan_ack
    # run into the same ledger. Derivation stops at the ack, the open
    # inventory empties, and a proposal naming the previously open id
    # fails closed with nothing retained.
    paths = _write_fixture(tmp_path)
    proposal = _proposal(paths)
    obligation_id = _derived_obligation(paths)["obligation_id"]

    _append_runs(paths, [_ack_run(obligation_id)])
    assert (
        autonomous_replan_obligation_from_runs(
            _newest_first_runs(paths), agent_todos=None, agent_id="agent-a"
        )
        is None
    )

    with pytest.raises(ValueError, match="does not match an open replan obligation"):
        _admit(paths, proposal)

    assert _journal_rows(paths) == []


def test_later_utc_ack_closes_obligation_across_offsets(
    tmp_path: Path,
) -> None:
    stalled_runs = _stall_runs()
    stalled_runs[0]["generated_at"] = "2026-09-01T08:00:00+08:00"
    stalled_runs[1]["generated_at"] = "2026-09-01T08:01:00+08:00"
    paths = _write_fixture(
        tmp_path,
        runs=stalled_runs,
    )
    proposal = _proposal(paths)
    obligation_id = _derived_obligation(paths)["obligation_id"]

    _append_runs(
        paths,
        [_ack_run(obligation_id, generated_at="2026-09-01T01:00:00Z")],
    )

    with pytest.raises(ValueError, match="does not match an open replan obligation"):
        _admit(paths, proposal)

    assert _journal_rows(paths) == []


def test_cross_goal_replan_obligation_fails_closed(tmp_path: Path) -> None:
    # The sibling Goal's run ledger never contributes to this Goal's
    # obligation inventory: an id derived on the peer Goal's run history
    # does not resolve here, even though its payload would otherwise be
    # valid.
    paths = _write_fixture(
        tmp_path,
        with_other_goal=True,
        other_goal_runs=_stall_runs(
            goal_id=OTHER_GOAL_ID, hypothesis="hypothesis-stage2-peer"
        ),
    )
    peer_goal_obligation = _derived_obligation(
        paths, agent_id=None, goal_id=OTHER_GOAL_ID
    )

    with pytest.raises(ValueError, match="does not match an open replan obligation"):
        _admit(
            paths,
            _proposal(
                paths,
                {"replan_obligation_id": peer_goal_obligation["obligation_id"]},
            ),
        )

    assert _journal_rows(paths) == []


def test_mismatched_agent_lane_fails_closed(tmp_path: Path) -> None:
    # The run history stalls on agent-b's lane, so the derived obligation
    # is agent-scoped to agent-b and agent-a's proposal fails closed.
    paths = _write_fixture(
        tmp_path,
        runs=_stall_runs(agent_id="agent-b"),
    )

    peer_lane_id = _derived_obligation(paths, agent_id="agent-b")["obligation_id"]
    with pytest.raises(ValueError, match="bound to another agent lane"):
        _admit(
            paths,
            _proposal(paths, {"replan_obligation_id": peer_lane_id}),
        )

    assert _journal_rows(paths) == []


def test_missing_run_history_fails_closed(tmp_path: Path) -> None:
    # No run ledger at all: the obligation inventory is empty and
    # admission must not trust a causal chain on string shape alone.
    paths = _write_fixture(tmp_path, runs=[])

    with pytest.raises(ValueError, match="does not match an open replan obligation"):
        _admit(
            paths,
            _proposal(paths, {"replan_obligation_id": "replan-0123456789abcdef"}),
        )

    assert _journal_rows(paths) == []


def test_incomplete_run_rows_derive_no_obligation(tmp_path: Path) -> None:
    # Forging authority by appending plain rows fails: rows without a
    # typed progress observation (and without the structured monitor
    # fields the monitor state machine requires) derive no obligation, so
    # a proposal naming any well-shaped replan id fails closed.
    forged_rows = [
        {
            "generated_at": "2026-09-01T00:00:00+00:00",
            "goal_id": GOAL_ID,
            "agent_id": "agent-a",
            "classification": "bounded_replan_progress",
            "turn_instance_id": "turn_stage2_forged_0",
        },
        {
            "generated_at": "2026-09-01T00:01:00+00:00",
            "goal_id": GOAL_ID,
            "agent_id": "agent-a",
            "classification": "quota_monitor_poll",
            "turn_instance_id": "turn_stage2_forged_1",
            "progress_observation": {"result_class": "blocked"},
        },
    ]
    paths = _write_fixture(tmp_path, runs=forged_rows)

    assert (
        autonomous_replan_obligation_from_runs(
            _newest_first_runs(paths), agent_todos=None, agent_id="agent-a"
        )
        is None
    )
    with pytest.raises(ValueError, match="does not match an open replan obligation"):
        _admit(
            paths,
            _proposal(paths, {"replan_obligation_id": "replan-0123456789abcdef"}),
        )

    assert _journal_rows(paths) == []


def test_untyped_run_row_interrupting_the_streak_derives_no_obligation(
    tmp_path: Path,
) -> None:
    # The typed repeat trigger requires consecutive equivalent typed rows:
    # one untyped row between the two blocked observations breaks the
    # streak and derives nothing.
    interrupted = [
        _stall_runs()[0],
        {
            "generated_at": "2026-09-01T00:00:30+00:00",
            "goal_id": GOAL_ID,
            "agent_id": "agent-a",
            "classification": "bounded_replan_progress",
            "turn_instance_id": "turn_stage2_gap",
        },
        _stall_runs()[1],
    ]
    paths = _write_fixture(tmp_path, runs=interrupted)

    assert (
        autonomous_replan_obligation_from_runs(
            _newest_first_runs(paths), agent_todos=None, agent_id="agent-a"
        )
        is None
    )
    with pytest.raises(ValueError, match="does not match an open replan obligation"):
        _admit(
            paths,
            _proposal(paths, {"replan_obligation_id": "replan-0123456789abcdef"}),
        )

    assert _journal_rows(paths) == []


def test_module_exposes_no_obligation_writer_api() -> None:
    # Protocol-surface regression: the proposal module owns no obligation
    # writer, closer, or receipt store any more. Authority is a read-time
    # derivation from the quota run-history ledger, so a caller cannot
    # mint an "open" obligation through this module.
    import loopx.control_plane.goals.goal_amendment_proposal as module

    for removed in (
        "record_replan_obligation_receipt",
        "close_or_rotate_replan_obligation_receipt",
        "read_latest_open_replan_obligation_envelope",
        "read_replan_obligation_receipt_journal",
        "replan_obligation_receipt_journal_path",
        "build_replan_obligation_authority_envelope",
        "validate_replan_obligation_authority_envelope",
        "compute_replan_obligation_receipt_digest",
        "REPLAN_OBLIGATION_AUTHORITY_ENVELOPE_SCHEMA_VERSION",
        "REPLAN_OBLIGATION_RECEIPT_SCHEMA_VERSION",
        "REPLAN_OBLIGATION_RECEIPT_DIRNAME",
        "REPLAN_OBLIGATION_RECEIPT_BASENAME",
    ):
        assert not hasattr(module, removed), removed


def test_legacy_receipt_journal_is_inert(tmp_path: Path) -> None:
    # The retired receipts.jsonl path is read by nothing: appending a
    # self-minted "open" receipt row there does not change admission.
    paths = _write_fixture(tmp_path)
    legacy = (
        paths["runtime"] / "goals" / GOAL_ID / "replan-obligations" / "receipts.jsonl"
    )
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(
        json.dumps(
            {
                "schema_version": "replan_obligation_authority_envelope_v0",
                "goal_id": GOAL_ID,
                "status": "open",
                "receipt": {"receipt_id": "rcpt_forged", "status": "open"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    record = _admit(paths, _proposal(paths))

    assert record["admission"] == "admitted"
    assert record["replan_obligation_id"] == _derived_obligation(paths)["obligation_id"]


def test_peer_claimed_affected_todo_still_admits(tmp_path: Path) -> None:
    # Shared amendments legitimately affect peer-claimed work: admission
    # checks goal membership and openness, not proposer ownership.
    paths = _write_fixture(tmp_path)

    record = _admit(
        paths,
        _proposal(paths, {"affected_todo_ids": ["todo_stage2_peer"]}),
    )

    assert record["admission"] == "admitted"
    assert record["canonical_effect"] == "none"


def test_nonexistent_affected_todo_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="not open on goal"):
        _admit(
            paths,
            _proposal(paths, {"affected_todo_ids": ["todo_missing"]}),
        )

    assert _journal_rows(paths) == []


def test_done_affected_todo_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="not open on goal"):
        _admit(
            paths,
            _proposal(paths, {"affected_todo_ids": ["todo_stage2_done"]}),
        )

    assert _journal_rows(paths) == []


def test_cross_goal_affected_todo_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path, with_other_goal=True)

    with pytest.raises(ValueError, match="not open on goal"):
        _admit(
            paths,
            _proposal(paths, {"affected_todo_ids": ["todo_stage2_other"]}),
        )

    assert _journal_rows(paths) == []


def test_proposal_digest_matches_the_python_canonical_recipe(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)
    proposal = _proposal(paths)

    record = _admit(paths, proposal)

    encoded = json.dumps(
        proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
    assert record["proposal_digest"] == expected








def test_replan_obligation_ids_follow_the_todo_contract(
    tmp_path: Path,
) -> None:
    # Python -> TS regression: the authority is
    # normalize_todo_replan_obligation_id's "replan-<16 lowercase hex>"
    # (real values such as the derived fixture id); the colon namespace
    # must be rejected end to end through the managed TS runtime.
    paths = _write_fixture(tmp_path)

    record = _admit(paths, _proposal(paths))  # default uses the derived id

    assert record["replan_obligation_id"] == _derived_obligation(paths)["obligation_id"]
    assert record["admission"] == "admitted"
    with pytest.raises(ValueError, match=r"replan-<16 lowercase hex>"):
        _admit(
            paths,
            _proposal(
                paths,
                {
                    "proposal_id": "gap_stage2_replan",
                    "replan_obligation_id": "replan:stage2-001",
                },
            ),
        )


def test_unknown_amendment_class_is_rejected_without_retention(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="amendment class is unsupported"):
        _admit(paths, _proposal(paths, {"amendment_class": "emergency_powers"}))

    assert _journal_rows(paths) == []


def test_evidence_refs_over_budget_are_rejected(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="exceeds 8 pointers"):
        _admit(
            paths,
            _proposal(
                paths,
                {
                    "evidence_refs": [
                        f"evidence:evt_stage2_{index}" for index in range(9)
                    ]
                },
            ),
        )

    assert _journal_rows(paths) == []


def test_unregistered_proposer_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="not registered"):
        _admit(paths, _proposal(paths, {"proposer_agent_id": "agent-z"}))


def test_unknown_goal_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="not registered"):
        _admit(paths, _proposal(paths, {"goal_id": "goal-unknown"}))


def test_journal_is_append_only_and_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)
    journal = _proposal_journal(paths)

    _admit(paths, _proposal(paths))
    first_snapshot = journal.read_text(encoding="utf-8")

    replay = _admit(paths, _proposal(paths))
    assert replay["proposal_id"] == "gap_stage2_001"
    assert journal.read_text(encoding="utf-8") == first_snapshot

    # RFC §7: one obligation can carry multiple proposals — same causal
    # chain, different proposal ids.
    _admit(
        paths,
        _proposal(paths, {"proposal_id": "gap_stage2_002"}),
    )
    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0] == first_snapshot.strip(), "append-only: row 1 unchanged"
    assert [row["journal_append_sequence"] for row in _journal_rows(paths)] == [1, 2]


def test_conflicting_proposal_id_replay_fails_closed(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    _admit(paths, _proposal(paths))

    with pytest.raises(ValueError, match="conflicting proposal_id"):
        _admit(
            paths,
            _proposal(paths, {"changed": ["a different amendment content"]}),
        )

    rows = _journal_rows(paths)
    assert len(rows) == 1
    assert rows[0]["changed"] == ["acceptance now requires the recovered receipt"]


def test_markdown_basis_zero_from_real_projection_admits(tmp_path: Path) -> None:
    # Review round 6 counterexample (P2-2): a Goal without an event log gets
    # state_event_basis_sequence=0 from the real Stage 1 projection. The
    # proposal must be able to consume that real basis verbatim — a decoder
    # demanding a positive integer here forces proposers to fabricate
    # history. (_proposal binds the live derived basis, so sequence is 0.)
    paths = _write_fixture(tmp_path)

    proposal = _proposal(paths)
    assert proposal["base_state_event_basis_sequence"] == 0

    record = _admit(paths, proposal)

    assert record["admission"] == "admitted"
    assert record["admission_facts"] == ["base_source_basis_unverifiable"]
    assert record["canonical_effect"] == "none"
    assert record["base_state_event_basis_sequence"] == 0


def test_markdown_basis_with_fabricated_positive_sequence_fails_closed(
    tmp_path: Path,
) -> None:
    # The inverse counterexample: with sequence fabricated to 5 the old
    # decoder happily admitted the proposal as unverifiable. 0 is the only
    # markdown basis the Stage 1 producer can emit, so any other value is
    # not a producible base and must fail closed instead of being retained.
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="markdown_active_state"):
        _admit(paths, _proposal(paths, {"base_state_event_basis_sequence": 5}))

    assert _journal_rows(paths) == []


def test_event_log_basis_rejects_zero_sequence(tmp_path: Path) -> None:
    # Event-log bases stay strictly positive: an append sequence of 0 cannot
    # exist under revision_basis=state_event_log, so it is a schema-level
    # rejection, not a "behind the head" needs_rebase retention.
    paths = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="state_event_log"):
        _admit(paths, _proposal(paths, {"base_revision_basis": "state_event_log", "base_state_event_basis_sequence": 0}))

    assert _journal_rows(paths) == []




def test_admission_has_zero_canonical_effect(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    event_log = paths["state_file"].with_name(EVENT_LOG_NAME)
    before_state = paths["state_file"].read_bytes()
    assert not event_log.exists()
    before_registry = paths["registry"].read_bytes()

    _admit(paths, _proposal(paths))
    _admit(paths, _proposal(paths, {"proposal_id": "gap_stage2_002"}))

    assert paths["state_file"].read_bytes() == before_state
    assert not event_log.exists()
    assert paths["registry"].read_bytes() == before_registry
    assert _journal_rows(paths)[0]["canonical_effect"] == "none"


def test_registered_effect_method_rejects_an_illegal_request() -> None:
    # Direct call through the managed TS runtime: a digest that violates
    # the sha256 contract is a request rejection, not an admission record.
    # The proposal is built inline — this test needs no fixture on disk.
    proposal: dict[str, object] = {
        "schema_version": "goal_amendment_proposal_v0",
        "proposal_id": "gap_stage2_001",
        "goal_id": GOAL_ID,
        "proposer_agent_id": "agent-a",
        "amendment_class": "shared_acceptance",
        "base_revision_basis": "state_event_log",
        "base_state_event_basis_sequence": 3,
        "base_source_basis_digest": "md5:zz",
        "retained": ["original outcome remains unchanged"],
        "changed": ["acceptance now requires the recovered receipt"],
        "stopped": [],
        "evidence_refs": ["evidence:evt_stage2_001"],
        "affected_todo_ids": ["todo_stage2_a"],
        "replan_obligation_id": "replan-0123456789abcdef",
    }
    with pytest.raises(EffectRuntimeRejected) as excinfo:
        effect_runtime_result(
            "goal.amendment_proposal.admit",
            {
                "schema_version": "goal_amendment_proposal_request_v0",
                "proposal": proposal,
                "derived_basis": {
                    "state_event_basis_sequence": 3,
                    "revision_basis": "state_event_log",
                    "source_basis_digest": "sha256:" + "b" * 64,
                },
                "open_replan_obligations": [],
                "goal_todo_inventory": [],
            },
        )
    assert excinfo.value.error_kind == "request_rejected"


def test_concurrent_admissions_serialize_journal_appends(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)
    repo_root = Path(__file__).resolve().parents[2]
    child_code = """
import json
import sys
from pathlib import Path

from loopx.control_plane.goals.goal_amendment_proposal import (
    admit_goal_amendment_proposal,
)

record = admit_goal_amendment_proposal(
    proposal=json.loads(sys.argv[2]),
    project=Path(sys.argv[1]),
)
print(record["proposal_id"], record["journal_append_sequence"])
"""
    env = dict(os.environ)
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(repo_root) + (
        os.pathsep + existing_pythonpath if existing_pythonpath else ""
    )
    # Same fixture obligation (same causal chain), different proposal ids
    # — RFC §7 allows multiple proposals per obligation to coexist.
    overrides = (
        {"proposal_id": "gap_stage2_p1"},
        {"proposal_id": "gap_stage2_p2"},
    )
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                child_code,
                str(paths["project"]),
                json.dumps(_proposal(paths, proposal_overrides)),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        for proposal_overrides in overrides
    ]
    try:
        for child in children:
            _, stderr = child.communicate(timeout=60)
            assert child.returncode == 0, stderr
    finally:
        for child in children:
            if child.poll() is None:  # pragma: no cover - failure cleanup
                child.kill()
                child.wait()

    rows = _journal_rows(paths)
    # The cross-process lock must serialize both appends: exactly one row
    # per proposal, no interleaved JSON, and unique sequence numbers.
    assert sorted(row["proposal_id"] for row in rows) == [
        "gap_stage2_p1",
        "gap_stage2_p2",
    ]
    assert sorted(row["journal_append_sequence"] for row in rows) == [1, 2]


def test_corrupt_journal_line_fails_closed_and_retains_nothing(
    tmp_path: Path,
) -> None:
    paths = _write_fixture(tmp_path)
    _admit(paths, _proposal(paths))
    journal = _proposal_journal(paths)
    with journal.open("a", encoding="utf-8") as stream:
        stream.write('{"schema_version": "goal_amendment_proposal_admis\n')
    corrupted = journal.read_bytes()

    with pytest.raises(ValueError, match="invalid proposal journal JSONL"):
        _admit(
            paths,
            _proposal(paths, {"proposal_id": "gap_stage2_002"}),
        )
    with pytest.raises(ValueError, match="invalid proposal journal JSONL"):
        read_goal_amendment_proposal_journal(
            runtime_root=paths["runtime"],
            goal_id=GOAL_ID,
        )

    assert journal.read_bytes() == corrupted


def test_retention_does_not_advance_the_derived_head(tmp_path: Path) -> None:
    paths = _write_fixture(tmp_path)
    before_tree = _canonical_tree_snapshot(paths)

    first = _admit(paths, _proposal(paths))
    second = _admit(
        paths,
        _proposal(paths, {"proposal_id": "gap_stage2_002"}),
    )

    assert first["admission"] == "admitted"
    assert first["admission_facts"] == ["base_source_basis_unverifiable"]
    # The first journal append must not move the canonical head the second
    # proposal reports against: retention lives outside the revision
    # carrier, so the same base stays fresh (not needs_rebase) and every
    # non-journal runtime byte is unchanged.
    assert second["admission"] == "admitted"
    assert second["admission_facts"] == ["base_source_basis_unverifiable"]
    assert _canonical_tree_snapshot(paths) == before_tree
