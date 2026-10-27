"""Whole audit sharing preserves evidence, decisions and recovery obligations."""

from copy import deepcopy
import json

import pytest

from loopx.control_plane.quota.cli_projection import (
    compact_quota_should_run_cli_payload,
)
from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result,
    build_turn_envelope,
    compose_turn_decision,
    prepare_context_delivery,
    restore_context_delivery,
)
from test_complete_turn_decision import _canonical_digest, _resign_contract
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


@pytest.fixture(
    params=[compact_quota_should_run_cli_payload, compose_turn_decision],
    ids=["legacy", "complete"],
)
def share(request):
    return request.param


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


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "empty",
        "malformed",
        "wrong-owner",
        "chain",
        "judge-chain",
        "conflict",
        "type-conflict",
        "extra-clause",
        "judge-only",
    ],
)
def test_dangling_or_conflicting_audit_reference_is_rejected_with_valid_hash(mutation):
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    contract = compose_turn_decision(packet())
    envelope = build_turn_envelope(contract)
    audit = nested(contract)[1]["vision_continuation_audit"]
    if mutation == "missing":
        contract.pop("vision_continuation_audit")
    elif mutation == "empty":
        contract["vision_continuation_audit"] = {}
    elif mutation == "malformed":
        audit["projection_ref"] = {"path": "$.vision_continuation_audit"}
    elif mutation == "wrong-owner":
        audit["projection_ref"] = "$.another_audit"
    elif mutation == "chain":
        contract["vision_continuation_audit"]["projection_ref"] = "$.another_audit"
    elif mutation == "judge-chain":
        contract["vision_continuation_audit"]["vision_gap_judge"]["projection_ref"] = (
            "$.another_judge"
        )
    elif mutation == "conflict":
        audit["vision_gap_judge"]["done"] = True
    elif mutation == "type-conflict":
        audit["required"] = 1
    elif mutation == "extra-clause":
        audit["required_before_closeout"] = ["skip_review"]
    else:
        audit.pop("projection_ref")
    contract, _ = _resign_contract(contract)
    for read in (
        lambda: compose_turn_decision(contract),
        lambda: build_turn_envelope(contract),
        lambda: _prompt({"work_contract": contract, "turn_envelope": envelope}),
        lambda: _turn_envelope_result(
            "replan_view",
            payload=contract,
            source_hash=_canonical_digest(contract),
            stage="review",
        ),
    ):
        with pytest.raises(RuntimeError, match="Vision audit reference"):
            read()


@pytest.mark.parametrize(
    "changed, complete", [(False, True), (True, True), (True, False)]
)
def test_audit_survives_delta_full_and_lost_baseline_recovery(changed, complete):
    source = packet()
    source["interaction_contract"]["agent_channel"]["work_context"]["complete"] = (
        complete
    )
    before = compose_turn_decision(source)
    if changed:
        source["vision_continuation_audit"]["vision_gap_judge"][
            "agent_judge_instruction"
        ] += " Wait for new evidence."
        for carrier in nested(source):
            carrier["vision_continuation_audit"] = deepcopy(
                source["vision_continuation_audit"]
            )
    else:
        source["reason"] = "A new observation"
    after = compose_turn_decision(source)
    scope = dict(
        goal_id="fixture", agent_id="worker", session_id="session", context_epoch="one"
    )
    baseline = prepare_context_delivery(
        before, scope=scope, capture_id="one", surface="complete_turn"
    )
    full = prepare_context_delivery(
        after, scope=scope, capture_id="two", surface="complete_turn"
    )
    delta = prepare_context_delivery(
        after,
        scope=scope,
        capture_id="two",
        surface="complete_turn",
        baseline=baseline,
        retained={"complete": True, "target": baseline["target"]},
    )
    assert delta["mode"] == ("delta" if complete else "full")
    for retained in (baseline, None):
        restored = restore_context_delivery(delta, baseline=retained, current=full)[
            "context"
        ]
        assert restored == after
        assert (
            restored["vision_continuation_audit"] == source["vision_continuation_audit"]
        )


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
        "--decision-output-dir",
        str(capture),
        cwd=project,
    )
    assert code == 0 and full["should_run"], full
    assert "context_delivery" not in full
    saved = json.loads((capture / "decision.json").read_text())
    assert saved == full
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
    invalid = deepcopy(saved)
    invalid["goal_frontier_projection"]["vision_continuation_audit"][
        "required"
    ] = not audit["required"]
    invalid, _ = _resign_contract(invalid)
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
        _canonical_digest(invalid),
        "--replan-stage",
        "review",
        cwd=project,
    )
    assert rc != 0 and "Vision audit reference" in json.dumps(refused)
    assert state.read_bytes() == original_state and _spend_run_count(runtime) == 0


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
