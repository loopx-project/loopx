"""Contract, efficiency, and origin/main parity tests for autonomous_replan_ack."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
import re
import subprocess
import types
from types import MappingProxyType

import pytest

from loopx.control_plane.work_items import (
    autonomous_replan_ack as ack_mod,
    autonomous_replan_ack_history as history_mod,
)

GOAL_ID = "goal-ack-contract"
AGENT_ID = "agent-codex"
OBLIGATION_ID = "replan-0123456789abcdef"
FRONTIER_REV = "todo_frontier_revision_v0:0123456789abcdef01234567"
GINST_A = "ginst_" + "a" * 32
GINST_B = "ginst_" + "b" * 32


def _load_baseline_ack_module() -> types.ModuleType | None:
    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        ["git", "show", "origin/main:loopx/control_plane/work_items/autonomous_replan_ack.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    mod = types.ModuleType("_baseline_autonomous_replan_ack")
    mod.__package__ = "loopx.control_plane.work_items"
    mod.__file__ = str(repo_root / "loopx/control_plane/work_items/autonomous_replan_ack.py")
    exec(compile(proc.stdout, mod.__file__, "exec"), mod.__dict__)
    return mod


def test_autonomous_replan_ack_modules_zero_any_and_budget() -> None:
    root = Path(__file__).resolve().parents[2]
    limits = (
        (root / "loopx/control_plane/work_items/autonomous_replan_ack.py", 550),
        (root / "loopx/control_plane/work_items/autonomous_replan_ack_history.py", 750),
        (Path(__file__).resolve(), 750),
    )
    for path, max_loc in limits:
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= max_loc, f"{path} exceeded {max_loc} LOC"
        assert re.search(r"#\s*type:\s*ignore\b", source) is None, f"{path} has type: ignore"
        tree = ast.parse(source)
        any_refs = sum(
            1
            for node in ast.walk(tree)
            if (isinstance(node, ast.Name) and node.id == "Any")
            or (isinstance(node, ast.Attribute) and node.attr == "Any")
            or (isinstance(node, ast.alias) and node.name == "Any")
        )
        assert any_refs == 0, f"{path} has {any_refs} AST Any references"

    expected_exports = (
        "AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW",
        "AUTONOMOUS_REPLAN_ACK_RECOVERY_CANDIDATE_LIMIT",
        "AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK",
        "SettlementHistoryQualification",
        "qualify_settlement_history",
        "accepted_blocked_replan_history_indexes",
        "ack_binds_trigger_checkpoints",
        "normalize_projected_autonomous_replan_ack",
        "autonomous_replan_ack_recorded",
        "watch_lane_continuation_todo_ids",
        "compact_autonomous_replan_ack",
        "autonomous_replan_ack_candidates_for_obligation",
        "drop_unowned_recorded_replan_acks",
        "attach_historical_ack_writeback_order",
        "historical_ack_candidates_with_writeback_order",
        "load_receipt_replan_ack_candidates",
        "latest_blocked_successor_frontier_identity",
        "latest_monitor_replan_frontier_identity",
        "autonomous_replan_ack_matches_frontier",
        "autonomous_replan_ack_matches_agent",
        "latest_autonomous_replan_ack_for_projection",
        "effect_runtime_result",
    )
    for name in expected_exports:
        assert hasattr(ack_mod, name)
    assert history_mod.attach_historical_ack_writeback_order is ack_mod.attach_historical_ack_writeback_order


def _sample_ack(
    *,
    obligation_id: str = OBLIGATION_ID,
    checkpoints: bool = True,
    accepted: bool = True,
    recorded: bool = True,
    todo_source: Mapping[str, object] | None = None,
) -> dict[str, object]:
    delta: dict[str, object] = {
        "schema_version": "replan_semantic_delta_v0",
        "accepted": accepted,
        "outcomes": ["fresh_vision_path_outcome"],
        "satisfying_outcomes": [],
        "required_any_of": ["fresh_vision_path_outcome"],
        "trigger_kinds": ["long_todo_chain"],
        "obligation_id": obligation_id,
        "reason": "",
    }
    if checkpoints:
        delta["trigger_checkpoints"] = [
            {"kind": "long_todo_chain", "frontier_revision": FRONTIER_REV}
        ]
    ack: dict[str, object] = {
        "schema_version": "autonomous_replan_ack_v0",
        "recorded": recorded,
        "source": "refresh_state",
        "frontier_identity": "f-1",
        "semantic_delta": delta,
        "delta_contract": {
            "schema_version": "repair_delta_v0",
            "delta_present": False,
            "delta_kinds": ["", "watch_lane_continuation"],
            "auto_evidence": [
                {"kind": "watch_lane_continuation", "todo_ids": ["t1", " t1 ", "t2"]},
                *({"kind": f"extra_{idx}"} for idx in range(8)),
            ],
        },
    }
    if todo_source is not None:
        ack["todo_source"] = dict(todo_source)
    return ack


def _sample_run(
    index: int,
    *,
    goal_ref: dict[str, str] | None = None,
    include_null_goal_ref: bool = False,
    todo_source_present_none: bool = False,
    path_outcome: str = "replan",
) -> dict[str, object]:
    run: dict[str, object] = {
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "turn_instance_id": f"turn:{index}",
        "generated_at": f"2026-08-19T12:{index:02d}:00Z",
        "classification": "bounded_progress",
        "agent_vision": {"path_delta": {"outcome": path_outcome}},
        "autonomous_replan_ack": _sample_ack(todo_source={"provider": "ack-src"}),
    }
    if goal_ref is not None:
        run["goal_ref"] = dict(goal_ref)
    elif include_null_goal_ref:
        run["goal_ref"] = None
    if todo_source_present_none:
        run["todo_source"] = None
    return run


def test_paired_origin_main_behavioral_parity(tmp_path: Path) -> None:
    base = _load_baseline_ack_module()
    if base is None:
        pytest.skip("git origin/main unavailable in this checkout")

    ack = _sample_ack()
    run_plain = _sample_run(1)
    run_null_source = _sample_run(2, todo_source_present_none=True)
    run_unaccepted = _sample_run(3)
    run_unaccepted["autonomous_replan_ack"] = _sample_ack(accepted=False)

    for r in (run_plain, run_null_source, run_unaccepted, {}):
        assert ack_mod.autonomous_replan_ack_recorded(r) == base.autonomous_replan_ack_recorded(r)
        assert ack_mod.compact_autonomous_replan_ack(r) == base.compact_autonomous_replan_ack(r)
    assert ack_mod.compact_autonomous_replan_ack(None) == base.compact_autonomous_replan_ack(None)
    assert "todo_source" in (ack_mod.compact_autonomous_replan_ack(run_plain) or {})
    assert "todo_source" not in (ack_mod.compact_autonomous_replan_ack(run_null_source) or {})

    for a in (ack, _sample_ack(checkpoints=False), {"semantic_delta": {"trigger_checkpoints": [{"kind": " ", "frontier_revision": "x"}]}}, None):
        assert ack_mod.ack_binds_trigger_checkpoints(a) == base.ack_binds_trigger_checkpoints(a)
        assert ack_mod.normalize_projected_autonomous_replan_ack(a) == base.normalize_projected_autonomous_replan_ack(a)
        assert ack_mod.autonomous_replan_ack_matches_agent(a, agent_id=AGENT_ID) == base.autonomous_replan_ack_matches_agent(a, agent_id=AGENT_ID)

    dc = ack.get("delta_contract")
    assert isinstance(dc, dict)
    assert ack_mod.watch_lane_continuation_todo_ids(dc) == base.watch_lane_continuation_todo_ids(dc) == ["t1", "t2"]

    newest_first = [_sample_run(idx) for idx in range(12, 0, -1)]
    newest_first.append(_sample_run(12))
    assert (
        ack_mod.autonomous_replan_ack_candidates_for_obligation(
            newest_first, goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=OBLIGATION_ID,
        )
        == base.autonomous_replan_ack_candidates_for_obligation(
            newest_first, goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=OBLIGATION_ID,
        )
    )
    assert ack_mod.autonomous_replan_ack_candidates_for_obligation(
        newest_first, goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=OBLIGATION_ID, limit=0,
    ) == []

    ref_a = {"goal_id": GOAL_ID, "goal_instance_id": GINST_A}
    ref_b = {"goal_id": GOAL_ID, "goal_instance_id": GINST_B}
    mixed_runs = [_sample_run(1), _sample_run(2, include_null_goal_ref=True), _sample_run(3, goal_ref=ref_a), _sample_run(4, goal_ref=ref_b)]
    for owner_ref in (None, ref_a):
        assert (
            ack_mod.drop_unowned_recorded_replan_acks(mixed_runs, goal_id=GOAL_ID, goal_ref=owner_ref)
            == base.drop_unowned_recorded_replan_acks(mixed_runs, goal_id=GOAL_ID, goal_ref=owner_ref)
        )
    with pytest.raises(ValueError, match="does not match goal_id"):
        ack_mod.drop_unowned_recorded_replan_acks([], goal_id="other", goal_ref=ref_a)

    # Frontier matching and monitor poll identity parity
    obl = {"frontier_identity": "f-1", "triggers": [{"latest_generated_at": "2026-08-19T12:00:00Z"}]}
    assert ack_mod.autonomous_replan_ack_matches_frontier({"frontier_identity": "f-1", "generated_at": "2026-08-19T12:00:00Z"}, obl) == base.autonomous_replan_ack_matches_frontier({"frontier_identity": "f-1", "generated_at": "2026-08-19T12:00:00Z"}, obl) is True
    assert ack_mod.autonomous_replan_ack_matches_frontier({"frontier_identity": "f-1", "generated_at": "2026-08-19T11:59:59Z"}, obl) == base.autonomous_replan_ack_matches_frontier({"frontier_identity": "f-1", "generated_at": "2026-08-19T11:59:59Z"}, obl) is False

    poll_runs: list[dict[str, object]] = [
        {"agent_id": AGENT_ID, "classification": "bounded_progress"},
        {"agent_id": AGENT_ID, "classification": "quota_monitor_poll", "monitor_target": {"monitor_mode": "monitor_quiet_until_material_transition", "target_id": "watch-1"}},
    ]
    assert ack_mod.latest_blocked_successor_frontier_identity(poll_runs, agent_id=AGENT_ID) == base.latest_blocked_successor_frontier_identity(poll_runs, agent_id=AGENT_ID) is None
    assert ack_mod.latest_monitor_replan_frontier_identity(poll_runs, agent_id=AGENT_ID, watch_todo_ids=["todo-1"]) == base.latest_monitor_replan_frontier_identity(poll_runs, agent_id=AGENT_ID, watch_todo_ids=["todo-1"]) == "watch-1"
    assert ack_mod.latest_monitor_replan_frontier_identity(poll_runs, agent_id=AGENT_ID, watch_todo_ids=["todo-1", "todo-2"]) is None

    # Projection lookback window parity (legacy expires at 20, checkpoint stays visible, neutral ignored)
    neutral: list[dict[str, object]] = [{"classification": "quota_slot_spent", "agent_id": AGENT_ID}] * 25
    material: list[dict[str, object]] = [{"classification": f"step_{i}", "agent_id": AGENT_ID} for i in range(20)]
    legacy_run = {**_sample_run(7), "autonomous_replan_ack": _sample_ack(checkpoints=False)}
    for hist in (
        [*neutral, legacy_run],
        [*material, legacy_run],
        [*material, _sample_run(8)],
        [_sample_run(9)],
    ):
        assert (
            ack_mod.latest_autonomous_replan_ack_for_projection(hist, neutral_classifications={"quota_slot_spent"})
            == base.latest_autonomous_replan_ack_for_projection(hist, neutral_classifications={"quota_slot_spent"})
        )

    # Rollout index + writeback order parity via load_receipt_replan_ack_candidates
    from loopx.rollout_event_log import build_rollout_event

    idx_dir = tmp_path / "goals" / GOAL_ID / "runs"
    idx_dir.mkdir(parents=True)
    run_row = {**_sample_run(5, goal_ref=ref_b), "todo_id": "todo-5", "settlement_identity": {"todo_id": "todo-5", "effect_id": "eff-5"}}
    (idx_dir / "index.jsonl").write_text(json.dumps(run_row) + "\n", encoding="utf-8")
    events = [
        build_rollout_event(goal_id=GOAL_ID, event_kind="quota_should_run", goal_ref=ref_b, agent_id=AGENT_ID, run_id="guard-1", recorded_at="2026-08-19T12:00:00Z"),
        build_rollout_event(goal_id=GOAL_ID, event_kind="refresh_state", goal_ref=ref_b, agent_id=AGENT_ID, run_id="turn:5", todo_id="todo-5", recorded_at="2026-08-19T12:00:01Z", details={"autonomous_replan_recorded": True, "replan_obligation_id": None, "settlement_effect_id": "eff-5"}),
    ]
    (tmp_path / "goals" / GOAL_ID / "rollout-event-log.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    head_loaded = ack_mod.load_receipt_replan_ack_candidates(tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=OBLIGATION_ID, guard_recorded_at="2026-08-19T12:00:00Z", guard_turn_instance_id="guard-1", goal_ref=ref_b)
    base_loaded = base.load_receipt_replan_ack_candidates(tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=OBLIGATION_ID, guard_recorded_at="2026-08-19T12:00:00Z", guard_turn_instance_id="guard-1", goal_ref=ref_b)
    assert head_loaded == base_loaded and len(head_loaded) == 1 and head_loaded[0]["writeback_follows_admission"] is True


def test_writeback_order_linear_scan_efficiency_and_readonly_mappings() -> None:
    # Single-pass indexing avoids O(candidates * events^2) ownership rescans.
    proxy_run = MappingProxyType({
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "turn_instance_id": "turn:1",
        "generated_at": "2026-08-19T12:00:00Z",
        "autonomous_replan_ack": MappingProxyType(_sample_ack()),
    })
    assert ack_mod.autonomous_replan_ack_recorded(proxy_run) is True
    assert ack_mod.compact_autonomous_replan_ack(proxy_run) is not None

    ref = {"goal_id": GOAL_ID, "goal_instance_id": GINST_A}

    class _CountingEvent(dict[str, object]):
        reads = 0

        def get(self, key: str, default: object = None) -> object:
            if key == "event_kind":
                _CountingEvent.reads += 1
            return super().get(key, default)

    events: list[Mapping[str, object]] = [
        _CountingEvent({
            "event_kind": "quota_should_run",
            "event_id": "evt-adm",
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "run_id": "guard:0",
            "recorded_at": "2026-08-19T12:00:00Z",
            "goal_ref": ref,
            "todo_id": "todo-1",
            "details": {"settlement_effect_id": "eff-1"},
        })
    ]
    for i in range(60):
        events.append(_CountingEvent({
            "event_kind": "refresh_state",
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "run_id": f"turn:{i % 4}",
            "todo_id": f"todo-{i}",
            "recorded_at": f"2026-08-19T12:01:{i:02d}Z",
            "goal_ref": ref,
            "details": {"autonomous_replan_recorded": True, "replan_obligation_id": None, "settlement_effect_id": f"eff-{i}"},
        }))
    candidates: list[dict[str, object]] = [{"turn_instance_id": f"turn:{i}"} for i in range(4)]
    runs = [{"goal_id": GOAL_ID, "agent_id": AGENT_ID, "turn_instance_id": f"turn:{i}", "goal_ref": ref, "todo_id": "todo-0", "settlement_identity": {"todo_id": "todo-0", "effect_id": "eff-0"}, "autonomous_replan_ack": {"semantic_delta": {"obligation_id": OBLIGATION_ID}}} for i in range(4)]
    ack_mod.attach_historical_ack_writeback_order(
        candidates, events, goal_id=GOAL_ID, agent_id=AGENT_ID, obligation_id=OBLIGATION_ID,
        guard_recorded_at=None, guard_turn_instance_id="guard:0", admission_event_id="evt-adm", runs=runs, goal_ref=ref,
    )
    assert _CountingEvent.reads == len(events)
    assert candidates[0]["writeback_follows_admission"] is True


def test_settlement_qualification_and_malformed_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_runtime(method: str, params: Mapping[str, object], **_: object) -> dict[str, object]:
        calls.append(method)
        selected_runs = params.get("runs")
        assert isinstance(selected_runs, Sequence) and len(selected_runs) == 1
        return {"accepted_indexes": [0], "active_retry_indexes": []}

    monkeypatch.setattr(ack_mod, "effect_runtime_result", fake_runtime)
    assert ack_mod.qualify_settlement_history([{"classification": "neutral"}]) == (frozenset(), frozenset())
    assert calls == []
    assert ack_mod.accepted_blocked_replan_history_indexes([{"replan_obligation_id": OBLIGATION_ID}]) == frozenset({0})
    assert calls == ["quota.settlement.read"]

    for bad in ([True], [-1], [1], "bad"):
        monkeypatch.setattr(ack_mod, "effect_runtime_result", lambda *_a, _b=bad, **_k: {"accepted_indexes": _b, "active_retry_indexes": []})
        with pytest.raises(RuntimeError, match="valid indexes"):
            ack_mod.qualify_settlement_history([{"blocked_retry": {}}], observed_at="2026-08-19T12:00:00Z")

    assert ack_mod.watch_lane_continuation_todo_ids({"auto_evidence": "not-a-list"}) == []
    assert ack_mod.latest_monitor_replan_frontier_identity([{"agent_id": AGENT_ID, "classification": "quota_monitor_poll", "monitor_target": "bad"}], agent_id=AGENT_ID) is None
    assert ack_mod.autonomous_replan_ack_matches_frontier(_sample_ack(), {"frontier_identity": "f-1", "triggers": "bad"}) is True
