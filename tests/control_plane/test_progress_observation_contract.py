"""Contract, Zero-Any, and malformed-input fail-closed tests for progress_observation."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import tokenize
from types import MappingProxyType
from typing import cast

import pytest

from loopx.control_plane.effect_runtime import EffectRuntimeRejected
from loopx.control_plane.work_items import progress_observation, replan_context_codec

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ast_any_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "Any"]


def _suppression_lines(path: Path) -> list[int]:
    lines: list[int] = []
    with path.open("r", encoding="utf-8") as handle:
        for tok in tokenize.generate_tokens(handle.readline):
            if tok.type == tokenize.COMMENT and ("type: ignore" in tok.string or "noqa" in tok.string):
                lines.append(tok.start[0])
    return lines


def test_progress_observation_meets_ceiling_zero_any_and_pin_invariants() -> None:
    module_path = REPO_ROOT / "loopx/control_plane/work_items/progress_observation.py"
    test_path = Path(__file__).resolve()

    assert len(module_path.read_text(encoding="utf-8").splitlines()) <= 550
    assert len(test_path.read_text(encoding="utf-8").splitlines()) < 500
    for path in (module_path, test_path):
        assert _ast_any_lines(path) == [], str(path)
        assert _suppression_lines(path) == [], str(path)

    pins = json.loads(
        (REPO_ROOT / "tests/architecture/package_kernel_import_pins.json").read_text(encoding="utf-8")
    )
    assert "loopx.control_plane.work_items.progress_observation" in pins["imports"]["loopx-jev"]

    for name in (
        "REPLAN_ACTION_PACKET_SCHEMA_VERSION",
        "PROGRESS_REPEAT_TRIGGER_KIND",
        "PROGRESS_REPEAT_THRESHOLD",
        "MAX_PROGRESS_EVIDENCE_IDS",
        "SEMANTIC_DIMENSIONS",
        "TERMINAL_RESULT_CLASSES",
        "FRESH_VISION_PATH_DISPOSITIONS",
        "normalize_progress_observation",
        "progress_observation_from_run",
        "_progress_turn_instance_id",
        "typed_progress_repeat_trigger",
        "_normalized_window",
        "replan_writeback_requirements",
        "required_semantic_outcomes",
        "replan_successor_transition_allowed",
        "guarded_replan_transition_delta",
        "replan_obligation_trigger_kinds",
        "replan_obligation_trigger_checkpoints",
        "semantic_delta_from_writeback",
        "build_replan_context",
        "build_replan_action_packet",
    ):
        assert hasattr(progress_observation, name), name


def test_normalize_progress_observation_validation_dedup_and_historical_guard() -> None:
    with pytest.raises(TypeError, match="must be an object"):
        progress_observation.normalize_progress_observation(cast(dict[str, object], ["bad"]))

    with pytest.raises(ValueError, match="progress observation must use"):
        progress_observation.normalize_progress_observation(
            {"schema_version": "legacy_v0", "result_class": "advanced", "surface_id": "s1"}
        )

    with pytest.raises(ValueError, match="result_class must be one of"):
        progress_observation.normalize_progress_observation({"result_class": "invalid", "surface_id": "s1"})

    with pytest.raises(ValueError, match="must be a boolean"):
        progress_observation.normalize_progress_observation(
            {"result_class": "advanced", "surface_id": "s1", "coverage_complete": "yes"}
        )

    with pytest.raises(ValueError, match="requires at least one stable"):
        progress_observation.normalize_progress_observation({"result_class": "advanced"})

    with pytest.raises(ValueError, match="public-safe identifier"):
        progress_observation.normalize_progress_observation({"result_class": "advanced", "surface_id": "bad id / space"})

    too_many = [f"ev-{i}" for i in range(progress_observation.MAX_PROGRESS_EVIDENCE_IDS + 1)]
    with pytest.raises(ValueError, match="at most 12 identifiers"):
        progress_observation.normalize_progress_observation({"result_class": "advanced", "evidence_ids": too_many})

    first = progress_observation.normalize_progress_observation(
        MappingProxyType({
            "result_class": "advanced",
            "surface_id": " s1 ",
            "evidence_ids": ["ev-b", "ev-a", "ev-b"],
            "coverage_complete": False,
        }),
        work_item_id="todo_1",
    )
    second = progress_observation.normalize_progress_observation(
        {
            "result_class": "advanced",
            "surface_id": "s1",
            "work_item_id": "todo_1",
            "evidence_ids": ["ev-a", "ev-b"],
            "coverage_complete": False,
        }
    )
    assert first == second
    assert first["evidence_ids"] == ["ev-a", "ev-b"]
    assert len(str(first["fingerprint"])) == 16

    assert progress_observation.progress_observation_from_run({"progress_observation": "raw"}) is None
    assert progress_observation.progress_observation_from_run({"progress_observation": {"result_class": "bad"}}) is None
    assert (
        progress_observation.progress_observation_from_run(
            {"progress_observation": {"result_class": "blocked", "blocker_id": "b1"}}
        )
        is not None
    )

    assert progress_observation._progress_turn_instance_id(
        {"turn_instance_id": "turn_1", "settlement_identity": {"turn_instance_id": "turn_2"}}
    ) is None
    assert progress_observation._progress_turn_instance_id({"turn_instance_id": "turn_1"}) == "turn_1"


def test_replan_semantics_wrappers_and_error_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    def _reject(*_a: object, **_kw: object) -> dict[str, object]:
        raise EffectRuntimeRejected("denied by ts", diagnostic_code="rejected")

    monkeypatch.setattr(progress_observation, "project_replan_semantics", _reject)
    with pytest.raises(ValueError, match="denied by ts"):
        progress_observation.replan_writeback_requirements({})
    with pytest.raises(ValueError, match="denied by ts"):
        progress_observation.replan_successor_transition_allowed({})
    with pytest.raises(ValueError, match="denied by ts"):
        progress_observation.guarded_replan_transition_delta(
            guard_scoped=True, selected_obligation_id="ob-1", transition_candidates=[]
        )

    monkeypatch.setattr(progress_observation, "project_replan_semantics", lambda _p: "bad")
    with pytest.raises(RuntimeError, match="requirements must be an object"):
        progress_observation.replan_writeback_requirements({})
    with pytest.raises(RuntimeError, match="eligibility shape mismatch"):
        progress_observation.replan_successor_transition_allowed({})
    with pytest.raises(RuntimeError, match="transition shape mismatch"):
        progress_observation.guarded_replan_transition_delta(
            guard_scoped=False, selected_obligation_id=None, transition_candidates=[]
        )

    monkeypatch.setattr(progress_observation, "project_replan_semantics", lambda _p: {"semantic_delta": "bad"})
    with pytest.raises(RuntimeError, match="object or null"):
        progress_observation.guarded_replan_transition_delta(
            guard_scoped=False, selected_obligation_id=None, transition_candidates=[]
        )

    monkeypatch.setattr(
        progress_observation,
        "project_replan_semantics",
        lambda _p: {"required_any_of": ["semantic_delta", "vision_update"]},
    )
    assert progress_observation.required_semantic_outcomes({}) == ["semantic_delta", "vision_update"]

    assert progress_observation.replan_obligation_trigger_kinds(
        {"triggers": [{"kind": "repeat"}, {"kind": "repeat"}, {"kind": "blocker"}]}
    ) == ["repeat", "blocker"]
    assert progress_observation.replan_obligation_trigger_checkpoints({"triggers": []}) == []

    monkeypatch.setattr(
        progress_observation,
        "effect_runtime_result",
        lambda *_a, **_kw: {"trigger_checkpoints": "bad"},
    )
    with pytest.raises(TypeError, match="must be a list"):
        progress_observation.replan_obligation_trigger_checkpoints({"triggers": [{"kind": "repeat"}]})


def test_semantic_delta_writeback_and_action_packet_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        replan_context_codec,
        "validate_replan_context",
        lambda ctx: {
            "obligation_id": "ob-ctx",
            "goal_id": "g-1",
            "agent_id": "a-1",
            "coverage_ledger": [{"result_class": "advanced", "surface_id": "s-cov"}],
            **(dict(ctx) if isinstance(ctx, dict) else {}),
        },
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        progress_observation.semantic_delta_from_writeback(
            obligation={"obligation_id": "ob-other", "replan_context": {}},
            progress_observation=None,
        )

    with pytest.raises(ValueError, match="original replan context and full history"):
        progress_observation.semantic_delta_from_writeback(
            obligation={"obligation_id": "ob-1"},
            progress_observation=None,
            recovering_writeback_path="runs/1.json",
        )

    with pytest.raises(ValueError, match="complete evidence history"):
        progress_observation.semantic_delta_from_writeback(
            obligation={"obligation_id": "ob-ctx", "replan_context": {"coverage_truncated": True}},
            progress_observation=None,
        )

    with pytest.raises(ValueError, match="not unique in replan history"):
        progress_observation.semantic_delta_from_writeback(
            obligation={"obligation_id": "ob-ctx", "replan_context": {}},
            progress_observation=None,
            history_runs=[{"json_path": "runs/other.json"}],
            recovering_writeback_path="runs/missing.json",
        )

    captured_calls: list[dict[str, object]] = []

    def _qualify(params: dict[str, object]) -> dict[str, object]:
        captured_calls.append(params)
        if params["operation"] == "qualify":
            return {"accepted": True, "outcomes": ["semantic_delta"]}
        return {
            "planning_guidance": "Pick another surface",
            "writeback_contract": {"successor_command": "base"},
            "stride_control": {"max_steps": 3},
        }

    monkeypatch.setattr(progress_observation, "project_replan_semantics", _qualify)
    monkeypatch.setattr(
        replan_context_codec,
        "replan_evidence_rows",
        lambda runs, **_kw: [
            {"progress_observation": {"result_class": "advanced", "surface_id": str(r.get("s") or "")}}
            for r in runs
        ],
    )
    monkeypatch.setattr(
        replan_context_codec,
        "replay_stride_review_evidence",
        lambda **_kw: {"cited": True},
    )
    monkeypatch.setattr(
        progress_observation,
        "effect_runtime_result",
        lambda *_a, **_kw: {"trigger_checkpoints": [{"kind": "repeat", "revision": "r1"}]},
    )

    gen_runs = (
        row
        for row in (
            {"json_path": "runs/skip.json", "s": "s-skip"},
            {"json_path": "runs/keep.json", "s": "s-keep"},
        )
    )
    qual = progress_observation.semantic_delta_from_writeback(
        obligation={
            "obligation_id": "ob-ctx",
            "replan_context": {"decision_followthrough": True},
            "triggers": [
                {
                    "kind": "repeat",
                    "progress_baseline": {"result_class": "advanced", "surface_id": "s-base"},
                    "progress_window": [{"result_class": "advanced", "surface_id": "s-win"}],
                }
            ],
        },
        progress_observation={"result_class": "advanced", "surface_id": "s-curr"},
        agent_vision={"state": "active", "stride_decision": {"previous_review": {"verdict": "ok"}}},
        history_runs=gen_runs,
        recovering_writeback_path="runs/skip.json",
    )
    assert qual["accepted"] is True
    assert qual["trigger_kinds"] == ["repeat"]
    assert qual["trigger_checkpoints"] == [{"kind": "repeat", "revision": "r1"}]
    qual_source = captured_calls[0]["progress_source"]
    assert isinstance(qual_source, dict)
    window_surfaces = [item["surface_id"] for item in qual_source["window"]]
    assert window_surfaces == ["s-win", "s-keep"]
    assert captured_calls[0]["stride_review_evidence"] == {"cited": True}

    shortcut = progress_observation.build_replan_action_packet({"settlement_action_packet": {"decision": "cached"}})
    assert shortcut == {"decision": "cached"}

    packet = progress_observation.build_replan_action_packet(
        {
            "obligation_id": "ob-ctx",
            "agent_id": "agent-1",
            "replan_context": {"uncovered_frontier": ["f1"]},
            "todo_actions": [{"action": "add", "role": "agent", "text": "next step", "priority": "P0"}],
            "capability_guard": {"kind": "require_evidence"},
        },
        goal_id="goal-1",
        bounded_research_frontier={
            "selected_gap": {
                "schema_version": "gap_v0",
                "gap_id": "gap-1",
                "successor_summary": "Run fresh probe",
                "successor_binding": {
                    "action_kind": "verify",
                    "task_domain": "software",
                    "target_key": "target:1",
                    "explore_result_node_refs": ["node-1"],
                },
            }
        },
    )
    assert packet["allowed_terminal"] == []
    assert packet["capability_guard"] == {"kind": "require_evidence"}
    assert packet["stride_control"] == {"max_steps": 3}
    assert packet["bounded_frontier"] == {"schema_version": "gap_v0", "gap_id": "gap-1"}
    wb = packet["writeback_contract"]
    assert isinstance(wb, dict)
    assert "--goal-id goal-1" in str(wb["successor_command"])
    assert "--explore-result-node-ref node-1" in str(wb["successor_command"])
