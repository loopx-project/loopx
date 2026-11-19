"""Causal feedback must use the carrier's declared order, never its timestamps."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from loopx.control_plane.work_items.progress_observation import build_replan_context
from loopx.control_plane.work_items.replan_context_codec import project_replan_context
from loopx.history import append_run_index_record, reserve_unique_run_paths
from test_replan_context_evidence import _settled_pair


def _rows(*, goal_id="evidence-goal", agent_id="agent-a"):
    def decision(turn, timestamp):
        work, _ = _settled_pair(goal_id=goal_id, agent_id=agent_id, turn_instance_id=turn)
        return {**work, "generated_at": timestamp,
            "agent_vision": {"schema_version": "goal_vision_replan_contract_v0", "agent_id": agent_id,
                "path_delta": {"schema_version": "goal_path_delta_v0", "outcome": "replan",
                    "prior_assumption": "The current route could explain the gap.",
                    "observed_reality": "The gap remains unexplained.", "changed": ["Compare another route."],
                    "evidence_refs": ["evidence:route-basis"]}},
            "autonomous_replan_ack": {"schema_version": "autonomous_replan_ack_v0", "recorded": True,
                "semantic_delta": {"schema_version": "replan_semantic_delta_v0", "accepted": True,
                    "satisfying_outcomes": ["fresh_vision_path_outcome"]}}}

    older = decision("turn-old", "2026-08-18T03:00:00Z")
    latest = decision("turn-latest", "2026-08-18T01:00:00Z")
    work, _ = _settled_pair(goal_id=goal_id, agent_id=agent_id, turn_instance_id="turn-probe")
    later = {**work, "generated_at": "2026-08-18T00:00:00Z",
        "progress_observation": {**work["progress_observation"], "surface_id": "surface-probe"}}
    return [older, latest, later]


def _assert_followthrough(context):
    follow = context["decision_followthrough"]
    assert follow["previous_decision"]["turn_instance_id"] == "turn-latest"
    assert follow["recorded_progress_count"] == 1
    assert follow["observations"][0]["generated_at"] == "2026-08-18T00:00:00Z"
    assert follow["execution_and_goal_gain"] == "unknown"
    return follow


@pytest.mark.parametrize("shape", ["runs", "goals"])
def test_compact_status_newest_first_returns_latest_decision_and_later_work(shape):
    rows = list(reversed(_rows()))
    history = {"runs": rows} if shape == "runs" else {"goals": [{"id": "evidence-goal", "latest_runs": rows}]}
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=(),
        source_status={"run_history": history}, stride_strategy="evidence_conditioned_v1")
    assert _assert_followthrough(context)["source_history_complete"] is False


def test_named_newest_first_builder_preserves_order_without_a_status_source():
    context = build_replan_context({"obligation_id": "replan-fixture"}, goal_id="evidence-goal",
        agent_id="agent-a", newest_first_runs=reversed(_rows()), stride_strategy="evidence_conditioned_v1")
    _assert_followthrough(context)


@pytest.mark.parametrize("typed", [False, True])
def test_declared_results_survive_busy_work_through_the_source_codec(typed):
    _, decision, work = _rows()
    decision["agent_vision"]["todo_delta"] = ["retain:todo-busy", "retain:todo-comparison"]

    def result(todo, index):
        settled, _ = _settled_pair(todo_id=todo, turn_instance_id=f"turn-work-{index}")
        item = {**settled, "generated_at": work["generated_at"], "health_check": f"Independent measured result {index}."}
        if typed:
            item["progress_observation"] = {**work["progress_observation"], "work_item_id": todo,
                "surface_id": f"surface-{index}"}
        else:
            item.pop("progress_observation", None)
        return item

    rows = [decision, result("todo-comparison", 0), *[result("todo-busy", i + 1) for i in range(12)]]
    for strategy in ("off", "evidence_conditioned_v1"):
        context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows,
            history_newest_first=False, stride_strategy=strategy)
        follow = context["decision_followthrough"]
        observed = follow["observations"] if typed else follow["ordinary_work"]["evidence"]
        assert len(observed) == 6
        assert {row["todo_id"] for row in observed} == {"todo-busy", "todo-comparison"}
        planned = next(work for work in follow["planned_work"] if work["todo_id"] == "todo-comparison")
        assert len(planned["observation_refs"] if typed else planned["ordinary_evidence_refs"]) == 1
        assert follow["execution_and_goal_gain"] == "unknown"


def test_real_append_index_ignores_compact_display_order_and_clock_skew(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "evidence-goal", "repo": str(tmp_path)}]}))
    index = tmp_path / "goals/evidence-goal/runs/index.jsonl"
    index.parent.mkdir(parents=True)
    index.write_text("".join(json.dumps(row) + "\n" for row in _rows()))
    before = index.read_bytes()
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=(),
        source_status={"registry": str(registry), "runtime_root": str(tmp_path),
            "run_history": {"runs": [_rows()[0]]}}, stride_strategy="evidence_conditioned_v1")
    assert _assert_followthrough(context)["source_history_complete"] is True
    assert index.read_bytes() == before


def test_same_second_work_references_resolve_through_the_history_cli(tmp_path):
    _, decision, work = _rows()
    decision["agent_vision"]["todo_delta"] = ["retain:todo-a"]
    work["todo_id"] = "todo-a"
    work["delivery_outcome"] = "outcome_progress"
    work.pop("progress_observation")
    rows = [decision]
    for turn in ("turn-negative-a", "turn-negative-b"):
        recorded = {**work, "turn_instance_id": turn, "settlement_identity": {
            **work["settlement_identity"], "todo_id": "todo-a", "turn_instance_id": turn,
            "effect_id": f"evidence-goal:agent-a:todo-a:{turn}",
        }}
        rows.append(recorded)
    first = rows[1]
    identity = first["settlement_identity"]
    rows.extend([dict(first), {**first, "settlement_identity": {
        **identity, "effect_id": "evidence-goal:agent-a:todo-a:turn-unrelated",
    }}, {**first, "settlement_identity": {key: value for key, value in identity.items() if key != "effect_id"}}])
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "evidence-goal", "repo": str(tmp_path)}]}))
    index = tmp_path / "goals/evidence-goal/runs/index.jsonl"
    index.parent.mkdir(parents=True)
    for position, row in enumerate(rows):
        artifact = index.parent / f"run-{position}.json"
        row["json_path"] = str(artifact)
        artifact.write_text(json.dumps(row))
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    before = index.read_bytes()
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=(),
        source_status={"registry": str(registry), "runtime_root": str(tmp_path),
            "run_history": {"runs": []}})
    followthrough = context["decision_followthrough"]
    evidence = followthrough["ordinary_work"]
    refs = {row["evidence_ref"] for row in evidence["evidence"]}
    assert evidence["recorded_count"] == len(refs) == 2
    assert followthrough["work_result_use"]["unresolved_count"] == 2
    legacy = {key: value for key, value in work.items() if key != "settlement_identity"}
    old_context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=[legacy])
    refs.add(old_context["evidence"][0]["evidence_ref"])
    for ref in refs:
        cli = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
            "--registry", str(registry), "--runtime-root", str(tmp_path), "history",
            "--goal-id", "evidence-goal", "--agent-id", "agent-a", "--evidence-ref", ref],
            text=True, capture_output=True, timeout=30)
        assert cli.returncode == 0, cli.stdout + cli.stderr
        result = json.loads(cli.stdout)
        assert result["evidence"]["evidence_ref"] == ref
        assert result["evidence"]["health_check"] == work["health_check"]
    assert index.read_bytes() == before


def _append_runs(index, *rows):
    for row in rows:
        artifact, report = reserve_unique_run_paths(index.parent, row["generated_at"])
        record = {**row, "json_path": str(artifact), "markdown_path": str(report)}
        artifact.write_text(json.dumps(record), encoding="utf-8")
        append_run_index_record(index, record)


def _vision_review_request(index):
    source = json.loads(index.read_text(encoding="utf-8").splitlines()[-1])
    # Request a fresh vision review after the observed decisions and work.
    # This triggers the ordinary quota projection without claiming work itself.
    return {key: source[key] for key in ("agent_id", "agent_vision")} | {
        "generated_at": "2026-08-18T09:00:00Z", "classification": "vision_review_requested",
    }


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("outer_goal", [True, False], ids=["explicit-goal", "index-goal"])
def test_nested_only_turn_work_survives_quota_and_history_readback(tmp_path, monkeypatch, provider, outer_goal):
    from test_replan_successor_guard_reentry import _fixture, _guard, AGENT, GOAL

    call, _, index = _fixture(tmp_path, monkeypatch, provider)
    trigger = _vision_review_request(index)
    _, decision, work = _rows(goal_id=GOAL, agent_id=AGENT)
    decision["agent_vision"]["todo_delta"] = ["retain:todo-a"]
    work["delivery_outcome"] = "outcome_progress"
    work.pop("progress_observation")
    work.pop("turn_instance_id")
    if not outer_goal:
        work.pop("goal_id")
    conflicted = {**work, "turn_instance_id": "turn-unrelated"}
    foreign = {**work, "goal_id": "other-goal", "health_check": "Work from a different Goal."}
    _append_runs(index, decision, work, conflicted, foreign, trigger)
    before = index.read_bytes()

    obligation = _guard(call, "turn-nested-history")["autonomous_replan_obligation"]
    follow = obligation["replan_context"]["decision_followthrough"]
    ordinary = follow["ordinary_work"]
    assert ordinary["recorded_count"] == 1
    assert follow["work_result_use"]["unresolved_count"] == 1
    work_ref = ordinary["evidence"][0]["evidence_ref"]
    assert follow["work_result_use"]["unresolved"][0]["evidence_ref"] == work_ref
    # References from the original display-based format must also resolve.
    historical = {key: value for key, value in work.items() if key != "settlement_identity"}
    context = project_replan_context(goal_id=GOAL, agent_id=AGENT, runs=[historical])
    old_ref = context["evidence"][0]["evidence_ref"]
    assert work_ref != old_ref
    for ref in (work_ref, old_ref):
        evidence = call("history", "--goal-id", GOAL, "--agent-id", AGENT, "--evidence-ref", ref)["evidence"]
        assert evidence["evidence_ref"] == ref
        assert evidence["health_check"] == work["health_check"]
    assert index.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("identity_case", [
    "decision-effect", "missing-schema", "unknown-schema", "malformed-identity", "non-todo-binding", "legacy-null",
    "v1-todo", "v1-conflicting-binding-id",
])
def test_settlement_identity_controls_work_attribution_in_quota(tmp_path, monkeypatch, provider, identity_case):
    from loopx.control_plane.effect_program import SettlementIdentity

    from test_replan_successor_guard_reentry import _fixture, _guard, AGENT, GOAL

    call, _, index = _fixture(tmp_path, monkeypatch, provider)
    trigger = _vision_review_request(index)
    original, later_decision, work = _rows(goal_id=GOAL, agent_id=AGENT)
    original["agent_vision"]["todo_delta"] = ["retain:todo-a"]
    later_decision["agent_vision"]["todo_delta"] = ["retain:todo-b"]
    work["delivery_outcome"] = "outcome_progress"
    work.pop("progress_observation")
    _append_runs(index, original, work, trigger)
    first = _guard(call, "turn-result-baseline")["autonomous_replan_obligation"]
    pending_ref = first["replan_context"]["decision_followthrough"]["work_result_use"]["unresolved"][0]["evidence_ref"]
    later_decision["agent_vision"]["path_delta"]["evidence_refs"] = [pending_ref]
    subsequent, _ = _settled_pair(goal_id=GOAL, agent_id=AGENT, todo_id="todo-b", turn_instance_id="turn-b")
    subsequent.pop("progress_observation")
    subsequent["delivery_outcome"] = "outcome_progress"

    control = project_replan_context(goal_id=GOAL, agent_id=AGENT,
        runs=[original, work, later_decision, subsequent], history_newest_first=False)
    control_follow = control["decision_followthrough"]
    assert control_follow["work_result_use"]["recorded_followthrough_count"] == 1

    if identity_case == "decision-effect":
        # The effect is a private source fact and does not change the decision reference.
        reference = control_follow["previous_decision"]["evidence_ref"]
        later_decision["settlement_identity"] = {**later_decision["settlement_identity"],
            "effect_id": f"other-goal:{AGENT}:todo-a:turn-latest"}
    else:
        # Resolve work with no usable Todo identity through the historical display-based reference.
        legacy = {key: value for key, value in subsequent.items() if key != "settlement_identity"}
        reference = project_replan_context(goal_id=GOAL, agent_id=AGENT, runs=[legacy])["evidence"][0]["evidence_ref"]
        identity = {**subsequent["settlement_identity"], "effect_id": f"other-goal:{AGENT}:todo-b:turn-b"}
        if identity_case == "missing-schema":
            identity.pop("schema_version")
        elif identity_case == "unknown-schema":
            identity["schema_version"] = "unsupported"
        elif identity_case == "malformed-identity":
            identity = identity["effect_id"]
        elif identity_case == "non-todo-binding":
            identity = SettlementIdentity(GOAL, AGENT, None, "turn-b", replan_obligation_id="replan-other").as_dict()
        elif identity_case in {"v1-todo", "v1-conflicting-binding-id"}:
            identity = {**subsequent["settlement_identity"], "schema_version": "quota_settlement_identity_v1",
                "binding_kind": "todo", "binding_id": "todo-b" if identity_case == "v1-todo" else "other-todo"}
        else:
            identity = None
        subsequent["settlement_identity"] = identity
    _append_runs(index, later_decision, subsequent, trigger)
    before = index.read_bytes()

    obligation = _guard(call, "turn-conflicting-settlement")["autonomous_replan_obligation"]
    follow = obligation["replan_context"]["decision_followthrough"]
    assert follow["previous_decision"]["turn_instance_id"] == (
        "turn-old" if identity_case == "decision-effect" else "turn-latest"
    )
    accepted = identity_case in {"legacy-null", "v1-todo"}
    assert follow.get("ordinary_work", {}).get("recorded_count", 0) == (
        1 if accepted or identity_case == "decision-effect" else 0
    )
    use = follow["work_result_use"]
    assert use["recorded_followthrough_count"] == int(accepted)
    if accepted:
        link = use["recorded_followthrough"][0]
        assert link["result_evidence_ref"] == pending_ref
        followup_ref = link["work_evidence_ref"]
        assert (followup_ref == reference) == (identity_case == "legacy-null")
        assert [result["evidence_ref"] for result in use["unresolved"]] == [followup_ref]
        credited = call("history", "--goal-id", GOAL, "--agent-id", AGENT,
            "--evidence-ref", followup_ref)["evidence"]
        assert credited["evidence_ref"] == followup_ref
    else:
        assert [result["evidence_ref"] for result in use["unresolved"]] == [pending_ref]
    historical = call("history", "--goal-id", GOAL, "--agent-id", AGENT,
        "--evidence-ref", reference)["evidence"]
    assert historical["evidence_ref"] == reference
    if identity_case == "decision-effect":
        assert historical["turn_instance_id"] == "turn-latest"
    else:
        assert historical["todo_id"] == "todo-b"
        assert historical["health_check"] == subsequent["health_check"]
    assert index.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("binding", [
    "todo-v1-decision", "typed-todo", "typed-legacy", "typed-autonomous", "other-obligation", "legacy-accounting",
])
def test_projected_work_uses_the_planned_todos_own_settlement(tmp_path, monkeypatch, provider, binding):
    from loopx.control_plane.effect_program import SettlementIdentity

    from test_replan_successor_guard_reentry import _fixture, _guard, AGENT, GOAL

    call, _, index = _fixture(tmp_path, monkeypatch, provider)
    trigger = _vision_review_request(index)
    original, decision, earlier = _rows(goal_id=GOAL, agent_id=AGENT)
    original["agent_vision"]["todo_delta"] = ["retain:todo-a"]
    decision["agent_vision"]["todo_delta"] = ["retain:todo-b"]
    earlier["delivery_outcome"] = "outcome_progress"
    earlier.pop("progress_observation")
    _append_runs(index, original, earlier, trigger)
    first = _guard(call, "turn-first-attribution")["autonomous_replan_obligation"]
    pending = first["replan_context"]["decision_followthrough"]["work_result_use"]["unresolved"][0]["evidence_ref"]
    decision["agent_vision"]["path_delta"]["evidence_refs"] = [pending]
    later, _ = _settled_pair(goal_id=GOAL, agent_id=AGENT, todo_id="todo-b", turn_instance_id="turn-observed")
    later["delivery_outcome"] = "outcome_progress"
    if binding == "todo-v1-decision":
        decision["settlement_identity"].update(schema_version="quota_settlement_identity_v1",
            binding_kind="todo", binding_id=decision["todo_id"])
    else:
        later.pop("todo_id")
    if binding in {"typed-autonomous", "other-obligation"}:
        later["settlement_identity"] = SettlementIdentity(
            GOAL, AGENT, None, "turn-observed", replan_obligation_id="replan-other",
        ).as_dict()
        if binding == "other-obligation":
            later["progress_observation"]["work_item_id"] = "replan-other"
    elif binding in {"typed-legacy", "legacy-accounting"}:
        later.pop("settlement_identity")
        if binding == "legacy-accounting":
            later["classification"] = "quota_slot_spent"
    _append_runs(index, decision, later, trigger)
    before = index.read_bytes()

    obligation = _guard(call, "turn-projected-attribution")["autonomous_replan_obligation"]
    follow = obligation["replan_context"]["decision_followthrough"]
    previous = follow["previous_decision"]
    assert previous["turn_instance_id"] == "turn-latest"
    assert previous["todo_id"] == decision["todo_id"]
    assert "replan_obligation_id" not in previous
    accepted = binding in {"todo-v1-decision", "typed-todo", "typed-legacy"}
    use = follow["work_result_use"]
    assert use["recorded_followthrough_count"] == int(accepted)
    assert (use["unresolved"][0]["evidence_ref"] != pending) == accepted
    assert follow["recorded_progress_count"] == int(binding not in {"typed-autonomous", "legacy-accounting"})
    planned, = follow["planned_work"]
    assert len(planned["observation_refs"]) == int(accepted)
    legacy = {key: value for key, value in later.items() if key != "settlement_identity"}
    ref = project_replan_context(goal_id=GOAL, agent_id=AGENT, runs=[legacy])["evidence"][0]["evidence_ref"]
    assert call("history", "--goal-id", GOAL, "--agent-id", AGENT, "--evidence-ref", ref)["evidence"]["evidence_ref"] == ref
    assert index.read_bytes() == before


@pytest.mark.parametrize("rows", [_rows(), list(reversed(_rows()))])
def test_unknown_order_does_not_fabricate_a_previous_decision_or_later_work(rows):
    context = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows,
        stride_strategy="evidence_conditioned_v1")
    assert "decision_followthrough" not in context
    assert context["evidence_count"] == 3


def test_carrier_metadata_preserves_feature_off_parity_and_rejects_non_boolean_order():
    rows = list(reversed(_rows()))
    ordinary = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows)
    declared = project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows,
        history_newest_first=True, stride_strategy="off")
    assert "decision_followthrough" not in ordinary
    _assert_followthrough(declared)
    assert "stride_strategy" not in declared
    for invalid in [0, 1, "newest_first"]:
        with pytest.raises(ValueError, match="boolean or unknown"):
            project_replan_context(goal_id="evidence-goal", agent_id="agent-a", runs=rows,
                history_newest_first=invalid)
