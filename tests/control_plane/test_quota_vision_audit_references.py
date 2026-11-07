"""Whole audit sharing preserves evidence, decisions and recovery obligations."""

from copy import deepcopy
import json

import pytest

from loopx.control_plane.quota.cli_projection import (
    compact_quota_should_run_cli_payload,
)
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope,
)
from test_replan_host_context_projection import _quota_payload


def packet():
    source = _quota_payload()
    audit = {
        "schema_version": "vision_continuation_audit_v0",
        "required": True,
        "decision": "continue",
        "agent_id": "worker",
        "trigger_count": 1,
        "required_before_closeout": [
            "inspect_original_evidence",
            "preserve_unmet_requirements",
        ],
        "vision_gap_judge": {
            "done": False,
            "decision": "continue",
            "agent_judge_instruction": "Keep original requirements, negative evidence and stop conditions. "
            * 20,
            "done_only_when": ["authoritative_evidence_satisfies_acceptance"],
            "continue_when": ["evidence_is_missing_weak_or_stale"],
        },
        "acceptance_gaps": [
            {"requirement": "Original acceptance", "evidence": "fixture:negative"}
        ],
        "optional_future_fact": {"revision": 2, "negative": True},
    }
    source["interaction_contract"]["agent_channel"]["work_context"] = {
        "complete": True,
        "sources": [],
    }
    source["vision_continuation_audit"] = audit
    source["goal_frontier_projection"] = {"vision_continuation_audit": deepcopy(audit)}
    for channel in ("agent_channel", "cli_channel"):
        source["interaction_contract"][channel]["vision_continuation_audit"] = deepcopy(
            audit
        )
    return source


def nested(source):
    return [
        source["goal_frontier_projection"],
        source["interaction_contract"]["agent_channel"],
        source["interaction_contract"]["cli_channel"],
    ]


@pytest.fixture
def share():
    return compact_quota_should_run_cli_payload


def test_exact_audit_is_retained_once_with_complete_judge_and_history(share):
    source = packet()
    original = deepcopy(source)
    shared = share(source)
    assert source == original
    assert share(shared) == shared
    audit = shared["vision_continuation_audit"]
    assert {k: v for k, v in audit.items() if k != "payload_compaction"} == source[
        "vision_continuation_audit"
    ]
    for carrier in nested(shared):
        assert carrier["vision_continuation_audit"] == {
            "schema_version": "vision_continuation_audit_v0",
            "required": True,
            "decision": "continue",
            "vision_gap_judge": {
                "done": False,
                "decision": "continue",
                "projection_ref": "$.vision_continuation_audit.vision_gap_judge",
            },
            "projection_ref": "$.vision_continuation_audit",
        }
    assert (
        json.dumps(shared).count(audit["vision_gap_judge"]["agent_judge_instruction"])
        == 1
    )
    historical = build_turn_envelope(shared)
    assert (
        historical["contract_capsule"]["vision_continuation_audit"]["vision_gap_judge"]
        == audit["vision_gap_judge"]
    )
    assert historical["action_signature"]["matches"] is True
    assert "projection_ref" not in json.dumps(
        historical["contract_capsule"]["vision_continuation_audit"]
    )


@pytest.mark.parametrize(
    "difference",
    ["requirement", "type", "version", "negative", "unknown", "missing-owner"],
)
def test_different_audit_is_never_replaced_by_root_summary(share, difference):
    source = packet()
    for carrier in nested(source):
        audit = carrier["vision_continuation_audit"]
        if difference == "requirement":
            audit["required_before_closeout"].append("wait_for_review")
        elif difference == "type":
            audit["required"] = 1
        elif difference == "version":
            audit["schema_version"] = "future_audit_v1"
        elif difference == "negative":
            audit["acceptance_gaps"][0]["evidence"] = "fixture:other"
        elif difference == "unknown":
            audit["optional_future_fact"]["revision"] = 3
    if difference == "missing-owner":
        source.pop("vision_continuation_audit")
    before = [deepcopy(c["vision_continuation_audit"]) for c in nested(source)]
    assert [c["vision_continuation_audit"] for c in nested(share(source))] == before


@pytest.mark.parametrize("left,right", [(1, 1.0), (False, 0), (-0.0, 0),
    (2**60, 2**60 + 1), (2**60, float(2**60)), (1.0, 1.0), (2**60 + 1, 2**60 + 1)])
def test_numeric_audit_identity_preserves_each_original_carrier(left, right, share):
    source = packet()
    source["vision_continuation_audit"]["future_numeric_fact"] = left
    for carrier in nested(source):
        carrier["vision_continuation_audit"]["future_numeric_fact"] = right
    original = json.dumps(source)
    expected = [json.dumps(carrier["vision_continuation_audit"]) for carrier in nested(source)]
    shared = share(source)
    assert json.dumps(source) == original
    assert json.dumps(shared["vision_continuation_audit"]["future_numeric_fact"]) == json.dumps(left)
    for index, carrier in enumerate(nested(shared)):
        audit = carrier["vision_continuation_audit"]
        if json.dumps(left) == json.dumps(right):
            assert audit["projection_ref"] == "$.vision_continuation_audit"
        else:
            assert json.dumps(audit) == expected[index]
            assert "projection_ref" not in audit
    assert json.dumps(share(shared)) == json.dumps(shared)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_guard_capture_and_recovery_preserve_vision_before_effects(
    tmp_path, monkeypatch, provider
):
    from canonical_authority_fixture import (
        initialize_canonical_authority,
        isolate_sqlite_runtime,
        read_fixture_todos,
    )
    from loopx.control_plane.coordination.runtime_shadow import (
        build_todo_runtime_shadow_projection,
    )
    from test_quota_settlement_cli import (
        AGENT_ID,
        GOAL_ID,
        _run_cli,
        _spend_run_count,
        _write_fixture,
        _configure_autonomous_replan_fixture,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_autonomous_replan_fixture(project, runtime, registry)
    runs = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    prior = {
        "generated_at": "2026-01-01T00:03:00+00:00",
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "classification": "prior_progress",
        "agent_vision": {
            "agent_id": AGENT_ID,
            "state": "vision_active",
            "vision_patch": {
                "vision_summary": "Verify the original outcome.",
                "acceptance_summary": "Keep evidence for all original requirements.",
            },
            "path_delta": {"outcome": "continue", "evidence_refs": ["evidence:prior"]},
        },
    }
    runs.write_text(runs.read_text() + json.dumps(prior) + "\n")
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=read_fixture_todos(state), handoff_mode="soft_claim"
        ),
        state_path=state,
        provider=provider,
    )
    original_state = state.read_bytes()
    capture = tmp_path / "captured"
    code, full = _run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--codex-app",
        "--turn-instance-id",
        "vision-read",
        "--context-delivery", "off",
        "--decision-output-dir",
        str(capture),
        cwd=project,
    )
    assert code == 0 and full["should_run"], full
    assert "context_delivery" not in full
    saved = json.loads((capture / "decision.json").read_text())
    assert "projection_ref" not in saved["vision_continuation_audit"]
    audit = full["vision_continuation_audit"]
    assert "projection_ref" not in audit
    assert (
        full["goal_frontier_projection"]["vision_continuation_audit"]["projection_ref"]
        == "$.vision_continuation_audit"
    )
    assert (
        build_turn_envelope(saved)["contract_capsule"]["vision_continuation_audit"][
            "vision_gap_judge"
        ]
        == audit["vision_gap_judge"]
    )
    assert state.read_bytes() == original_state and _spend_run_count(runtime) == 0
    invalid = deepcopy(full)
    invalid["goal_frontier_projection"]["vision_continuation_audit"][
        "required"
    ] = not audit["required"]
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text(json.dumps(invalid))
    rc, refused = _run_cli(
        registry,
        runtime,
        "quota",
        "replan-view",
        "--decision-file",
        str(invalid_path),
        "--source-hash",
        build_turn_envelope(invalid)["action_signature"]["source_decision_hash"],
        "--replan-stage",
        "review",
        cwd=project,
    )
    assert rc != 0 and "Vision audit reference" in json.dumps(refused)
    assert state.read_bytes() == original_state and _spend_run_count(runtime) == 0


def test_review_accepts_a_valid_historical_audit_and_refuses_a_conflicting_stop():
    from loopx.control_plane.quota.turn_envelope import _turn_envelope_result

    source = compact_quota_should_run_cli_payload(packet())

    def review():
        digest = build_turn_envelope(source)["action_signature"]["source_decision_hash"]
        return _turn_envelope_result("replan_view", payload=source, source_hash=digest, stage="review")

    assert review()["core_goal"]["objective"] == source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["objective"]
    nested(source)[1]["vision_continuation_audit"]["vision_gap_judge"]["done"] = True
    with pytest.raises(RuntimeError, match="Vision audit reference"):
        review()


def test_display_shares_audit_in_one_rpc_and_keeps_unrelated_sources_local(monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    source = packet()
    observation = {"source": "future_observation", "content": {"counter": 2**60 + 1}}
    source["interaction_contract"]["agent_channel"]["work_context"]["sources"] = [observation]
    operations = []
    evaluate = turn_envelope.effect_runtime_projection_result

    def observe(method, params, **kwargs):
        operations.append(params["operation"])
        assert "future_observation" not in json.dumps(params)
        return evaluate(method, params, **kwargs)

    monkeypatch.setattr(turn_envelope, "effect_runtime_projection_result", observe)
    shared = compact_quota_should_run_cli_payload(source)
    assert operations.count("reference_context") == 1
    assert "reference_vision_continuation" not in operations
    assert shared["interaction_contract"]["agent_channel"]["work_context"]["sources"] == [observation]
    assert nested(shared)[0]["vision_continuation_audit"]["projection_ref"] == "$.vision_continuation_audit"


def test_short_audit_remains_inline_without_reference_overhead(share):
    source = packet()
    source["vision_continuation_audit"] = {"required": False}
    for carrier in nested(source):
        carrier["vision_continuation_audit"] = {"required": False}
    shared = share(source)
    assert [c["vision_continuation_audit"] for c in nested(shared)] == [
        {"required": False}
    ] * 3


@pytest.mark.parametrize("interaction", [None, [], "historical-unavailable"])
def test_legacy_sharing_does_not_manufacture_absent_interaction(interaction):
    source = packet()
    source["interaction_contract"] = interaction
    shared = compact_quota_should_run_cli_payload(source)
    assert shared["interaction_contract"] == interaction
