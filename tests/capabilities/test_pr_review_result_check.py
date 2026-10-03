from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from loopx.capabilities.pr_review_queue import review_contract
from loopx.capabilities.pr_review_queue.result_check import check_review_result
from loopx.capabilities.pr_review_queue.review_contract import (
    build_review_execution_contract,
    build_review_plan,
)
from loopx.cli import main


def _review(*, area="product_runtime"):
    item = {
        "number": 42,
        "head_oid": "a" * 40,
        "areas": {area: 1},
        "review_action_kind": "review_pull_request_exact_head",
    }
    result = build_review_plan(item)["result_template"]
    requirements = {
        row["evidence_id"]: row
        for row in build_review_execution_contract()["evidence_requirements"]
    }
    for key, row in result["evidence"].items():
        requirement = requirements[key]
        row.update(
            status="verified",
            evidence="Synthetic consistency fixture, not a real review.",
            **{
                field: "Synthetic consistency fixture, not a real review."
                for field in requirement.get("fields", [])
            },
        )
        if "verdict_values" in requirement:
            row["verdict"] = requirement["verdict_values"][0]
        if key == "problem_context":
            row["spec_basis"] = {
                "decision": "no_spec",
                "spec_source": "none",
                "reason": "Synthetic formatter fixture has no written specification.",
            }
            row["outcome_impact"] = {
                dimension: {"decision": "not_applicable",
                            "reason": "Synthetic internal formatter fixture has no durable work or user journey.",
                            "inspected_path": "Synthetic formatter and its sole internal caller."}
                for dimension in ("long_horizon", "user_experience")
            }
        if key == "observable_semantics":
            row["scope_coverage"] = {"decision": "not_applicable",
                "reason": "Synthetic local formatter has no eligibility gate or covered subjects."}
        if key == "code_volume":
            row["compatibility_assessment"] = {
                "decision": "not_applicable",
                "reason": "Local formatting fixture; no protocol, adapter or persisted format change.",
            }
        if key == "semantic_alignment":
            row.update(
                checked_scope="Changed helper and its callers; no shared state writes.",
                impact_reason="Local formatting only; no shared contract changes.",
                verdict="not_applicable",
            )
        if "items_field" in requirement:
            item_fields = requirement.get("item_fields", [])
            if "required_cases" in requirement:
                case_ids = [case["case_id"] for case in requirement["required_cases"]]
            else:
                count = requirement.get("item_count", {}).get("minimum", 1)
                case_ids = [f"synthetic_{index}" for index in range(count)]
            row[requirement["items_field"]] = [
                {
                    field: (
                        case_id
                        if field == "case_id"
                        else "passed"
                        if field == "status"
                        else True
                        if field == "required"
                        else "Synthetic consistency fixture, not a real review."
                    )
                    for field in item_fields
                }
                for case_id in case_ids
            ]
        for shape in ("positive", "negative"):
            fields = requirement.get(f"{shape}_fields")
            if fields:
                row[shape] = {
                    field: "Synthetic consistency fixture, not a real review."
                    for field in fields
                }
    result["verdict"] = "APPROVE"
    result["reviewer"] = {
        "actor_kind": "model_agent",
        "declaration_source": "self_reported",
        "declared_model": "Example Model 1",
        "declared_provider": "Example Provider",
    }
    body = (Path(__file__).parents[2] / "examples/fixtures/pr-review.body.md").read_text()
    result["review_body"] = (
        f"{REVIEWER_LINE}\n\n"
        + body.replace("HEAD_OID", "a" * 40).replace("VERDICT", "APPROVE")
    )
    result["evidence"]["problem_context"].update(
        affected_caller_or_operator="维护者需要阅读一份能单独理解的评审。",
        before_after_scenario="此前仅有格式检查，解释可能留在内部证据中；现在正文展示场景和前后变化，减少反复询问。",
        observable_outcome="此合成用例检查已声明解释与公开正文的一致性，不代表真实 PR 的验证结果。",
        non_goals="检查的范围是正文可见性和一致性；理解是否正确仍由审查者判断。",
    )
    _publish_problem_explanation(result)
    return {"pull_requests": [item]}, result


REVIEWER_LINE = "Reviewer: model_agent · Example Model 1 · Example Provider"


def _publish_problem_explanation(result):
    context = result["evidence"]["problem_context"]
    fields = ["affected_caller_or_operator", "before_after_scenario",
              "observable_outcome", "non_goals"]
    if context["verdict"] == "justified_increment":
        fields.append("remaining_gap")
    body = result["review_body"]
    start, end = body.index("## 动机"), body.index("## 改动思路")
    explanation = "\n\n".join(context[field] for field in fields)
    result["review_body"] = body[:start] + "## 动机\n\n" + explanation + "\n\n" + body[end:]


def _reader_review():
    packet, result = _review()
    result["evidence"]["problem_context"].update(
        affected_caller_or_operator="维护者在导出中断后重新执行同一条命令。",
        before_after_scenario="以前响应丢失后，重试会重复导出数据；修复后读回上次结果，避免重复数据和人工清理。",
        observable_outcome="本 PR 让原命令在重试时返回已经保存的导出结果，失败时给出可继续处理的错误。",
        non_goals="本次范围是已有命令的重试，不增加自动调度；完成范围内修复不等于完成整个产品计划。",
    )
    _publish_problem_explanation(result)
    return packet, result


@pytest.mark.parametrize("field", ["affected_caller_or_operator", "before_after_scenario",
                                  "observable_outcome", "non_goals"])
@pytest.mark.parametrize("placement", ["evidence_only", "comment", "code", "later_section"])
def test_problem_explanation_must_reach_visible_motivation(field, placement):
    packet, result = _reader_review()
    assert check_review_result(packet, result)["ok"]
    text = result["evidence"]["problem_context"][field]
    replacement = {"evidence_only": "", "comment": f"<!-- {text} -->",
                   "code": f"```text\n{text}\n```", "later_section": ""}[placement]
    result["review_body"] = result["review_body"].replace(text, replacement)
    if placement == "later_section":
        result["review_body"] += "\n\n" + text
    checked = check_review_result(packet, result)
    assert f"review_body:problem_explanation_not_published:{field}" in checked["errors"]
    assert not checked["approval_consistent"]


def test_markdown_emphasis_and_line_wrapping_preserve_published_explanation():
    packet, result = _reader_review()
    text = result["evidence"]["problem_context"]["before_after_scenario"]
    result["review_body"] = result["review_body"].replace(text, "**" + text[:10] + "**\n" + text[10:])
    assert check_review_result(packet, result)["ok"]


def test_reader_explanation_shape_does_not_certify_comprehension():
    packet, result = _reader_review()
    checked = check_review_result(packet, result)
    assert checked["ok"]
    assert not checked["evidence_truth_verified"]


@pytest.mark.parametrize("value", [None, {}, ["hidden explanation"], " \n "])
def test_reader_explanation_requires_public_prose(value):
    packet, result = _reader_review()
    result["evidence"]["problem_context"]["before_after_scenario"] = value
    checked = check_review_result(packet, result)
    assert "review_body:problem_explanation_not_text:before_after_scenario" in checked["errors"]


def test_partial_delivery_must_publish_remaining_gap():
    packet, result = _reader_review()
    result["evidence"]["problem_context"].update(
        verdict="justified_increment", remaining_gap="自动重试仍需已有调度任务接入。",
        next_step="Existing task #43 owns scheduled retries.",
        boundary_reason="The retry command can be independently verified and reverted.")
    assert "review_body:problem_explanation_not_published:remaining_gap" in check_review_result(packet, result)["errors"]
    _publish_problem_explanation(result)
    assert check_review_result(packet, result)["ok"]


def _mapped_spec_basis():
    return {
        "decision": "mapped",
        "spec_source": "accepted_rfc",
        "reason": "The change implements an accepted RFC section.",
        "spec_ref": "docs/architecture/rfcs/example-v0.md#acceptance",
        "spec_revision": "b" * 40,
        "criteria": [
            {
                "criterion_id": "EX-1",
                "requirement": "Readback reports the persisted value.",
                "disposition": "implemented",
                "symbol_or_path": "loopx/example.py::read_back",
                "validation_ref": "tests/test_example.py::test_read_back",
            },
            {
                "criterion_id": "EX-2",
                "requirement": "Migration of old rows.",
                "disposition": "deferred",
                "reason": "Old rows are retired by the successor task.",
                "successor_or_gap": "The linked migration task.",
            },
        ],
    }


def _publish_spec_references(result):
    # The published body carries the immutable revision a reader on another host
    # needs to open the same specification text, not only its moving path.
    result["review_body"] += (
        "\n\nSpec basis: docs/architecture/rfcs/example-v0.md#acceptance"
        " @ revision " + "b" * 40 + " — EX-1 implemented; EX-2 deferred.\n"
    )


def test_result_check_is_not_semantic_or_merge_authority():
    packet, result = _review()
    checked = check_review_result(packet, result)
    assert checked["ok"] and checked["approval_consistent"]
    assert not checked["evidence_truth_verified"]
    assert not checked["remote_head_verified"]
    assert not checked["external_writes_performed"]


def _required_red_review():
    packet, result = _review()
    row = next(item for item in result["evidence"]["validation_matrix"]["items"]
               if item["case_id"] == "repository_required_checks")
    row.update(status="failed", result="The same maintained-twin rule fails at base and head.",
               skip_or_failure_reason="The same rule_a/rule_b pair remains over the 43 limit at both revisions.")
    return packet, result, row


def test_unrelated_baseline_red_check_does_not_force_request_changes():
    packet, result, row = _required_red_review()
    row["failure_attribution"] = {
        "disposition": "pre_existing_unrelated",
        "causal_scope_analysis": "The reviewed change edits a presentation helper, not the vocabulary scanner or its inputs.",
        "affected_invariant_evidence": "Focused presentation tests pass at the exact head.",
        "base_revision": "b" * 40,
        "head_revision": "a" * 40,
        "same_command": "python examples/semantic-vocabulary-drift-smoke.py",
        "baseline_observation": "Exit 1: maintained twin rule_a/rule_b, budget 44/43.",
        "head_observation": "Exit 1: maintained twin rule_a/rule_b, budget 44/43.",
        "baseline_failure_signature": "semantic-vocabulary-drift: rule_a/rule_b: maintained twin 44/43",
        "head_failure_signature": "semantic-vocabulary-drift: rule_a/rule_b: maintained twin 44/43",
    }
    checked = check_review_result(packet, result)
    assert checked["ok"] and checked["approval_consistent"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace(
        "English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert "request_changes_without_blocker" in check_review_result(packet, result)["errors"]


def test_required_red_check_needs_causal_attribution_and_unchanged_failure():
    packet, result, row = _required_red_review()
    checked = check_review_result(packet, result)
    assert "validation_matrix:repository_required_checks:failure_attribution_missing" in checked["approval_blockers"]
    row["failure_attribution"] = {
        "disposition": "pre_existing_unrelated",
        "causal_scope_analysis": "The changed code does not feed this check.",
        "affected_invariant_evidence": "Focused affected-path test passes.",
        "base_revision": "b" * 40,
        "head_revision": "a" * 40,
        "same_command": "python examples/semantic-vocabulary-drift-smoke.py",
        "baseline_observation": "Exit 1 with failure A.",
        "head_observation": "Exit 1 with failure B.",
        "baseline_failure_signature": "failure A",
        "head_failure_signature": "failure B",
    }
    assert "validation_matrix:repository_required_checks:failure_signature_changed" in check_review_result(packet, result)["approval_blockers"]
    row["failure_attribution"]["disposition"] = "pr_regression"
    assert "validation_matrix:repository_required_checks:attributable_or_unresolved_failure" in check_review_result(packet, result)["approval_blockers"]


def test_equal_red_count_with_different_failure_identity_still_blocks_approval():
    packet, result, row = _required_red_review()
    row["failure_attribution"] = {
        "disposition": "pre_existing_unrelated",
        "causal_scope_analysis": "The reviewed change does not edit the vocabulary scanner.",
        "affected_invariant_evidence": "Focused changed-path tests pass.",
        "base_revision": "b" * 40,
        "head_revision": "a" * 40,
        "same_command": "python examples/semantic-vocabulary-drift-smoke.py",
        "baseline_observation": "Exit 1: maintained twin rule_a/rule_b, budget 44/43.",
        "head_observation": "Exit 1: maintained twin rule_c/rule_d, budget 44/43.",
        "baseline_failure_signature": "semantic-vocabulary-drift: rule_a/rule_b: maintained twin 44/43",
        "head_failure_signature": "semantic-vocabulary-drift: rule_c/rule_d: maintained twin 44/43",
    }
    checked = check_review_result(packet, result)
    assert not checked["approval_consistent"]
    assert "validation_matrix:repository_required_checks:failure_signature_changed" in checked["approval_blockers"]


def test_malformed_required_validation_status_is_reported_not_raised():
    packet, result, row = _required_red_review()
    row["status"] = {"unexpected": "object"}
    assert "validation_matrix:repository_required_checks:invalid_status" in check_review_result(packet, result)["approval_blockers"]


def test_external_required_failure_is_review_only_when_independently_attributed():
    packet, result, row = _required_red_review()
    row["failure_attribution"] = {
        "disposition": "external_unrelated",
        "causal_scope_analysis": "The provider outage predates this head; changed behavior has separate coverage.",
        "affected_invariant_evidence": "Focused real-path test passes at this head.",
        "independent_evidence": "Provider status incident and retry on unchanged base fail identically.",
        "retry_or_recovery_owner": "CI operator retries after provider recovery; merge remains held.",
    }
    assert check_review_result(packet, result)["approval_consistent"]
    del row["failure_attribution"]["independent_evidence"]
    assert not check_review_result(packet, result)["approval_consistent"]


def _outcome_review(dimension):
    packet, result = _review()
    impact = result["evidence"]["problem_context"]["outcome_impact"][dimension]
    impact.update(
        decision="preserved",
        reason="The accepted journey remains available across the changed boundary.",
        inspected_path="Public command -> persisted checkpoint -> next invocation and user readback.",
        before_after="A repeat invocation keeps completed work and offers the next authorized action.",
        evidence_refs=["walkthroughs.positive", "validation_matrix:synthetic-continuation"],
    )
    return packet, result, impact


@pytest.mark.parametrize("dimension", ["long_horizon", "user_experience"])
@pytest.mark.parametrize("decision", ["regression", "not_yet_proven"])
def test_local_goal_achievement_cannot_hide_material_outcome_impact(dimension, decision):
    packet, result, impact = _outcome_review(dimension)
    assert result["evidence"]["problem_context"]["verdict"] == "goal_achieved"
    impact.update(decision=decision, minimum_repair="Prove the next authorized action through the affected entrypoint.")
    checked = check_review_result(packet, result)
    assert f"problem_context:outcome_impact:{dimension}:blocking_decision" in checked["approval_blockers"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


@pytest.mark.parametrize("dimension", ["long_horizon", "user_experience"])
def test_legitimate_wait_needs_acceptance_basis_and_bounded_recovery(dimension):
    packet, result, impact = _outcome_review(dimension)
    impact["decision"] = "accepted_tradeoff"
    assert not check_review_result(packet, result)["ok"]
    impact.update(
        acceptance_basis="Existing owner policy requires confirmation before this destructive effect.",
        bounded_cost_and_recovery="Only that effect waits; explicit confirmation resumes once or cancellation closes it safely.",
    )
    assert check_review_result(packet, result)["ok"]


def test_preserved_experience_needs_evidence_not_just_a_delivery_label():
    packet, result, impact = _outcome_review("user_experience")
    assert check_review_result(packet, result)["ok"]
    impact["evidence_refs"] = []
    assert not check_review_result(packet, result)["ok"]


def test_docs_inapplicability_still_names_the_inspected_path():
    packet, result = _review(area="public_docs")
    del result["evidence"]["problem_context"]["outcome_impact"]["user_experience"]["inspected_path"]
    assert not check_review_result(packet, result)["ok"]


def _scoped_review():
    packet, result = _review()
    coverage = {
        "decision": "verified", "reason": "The owner selected one existing job for this gate.",
        "authorized_scope": "Only job A; unrelated job B and future job C are excluded.",
        "scope_source": "Owner configuration selects the immutable job A id.",
        "enforcement_selector": "Shared gate checks coverage before readiness; job A stays held if unbound.",
        "recovery_owner": "Owner repairs the selected job binding; worker cannot edit acceptance scope.",
        "cases": [
            {"case_id": case_id, "status": "passed", "input_and_authority": source,
             "expected_outcome": expected, "observed_outcome": expected,
             "entrypoint_and_evidence": "Synthetic real-entrypoint receipt reference for checker fixture."}
            for case_id, source, expected in [
                ("covered_subject", "Selected A has no binding", "A is held"),
                ("uncovered_same_container", "Existing B is not selected", "B retains baseline admission"),
                ("new_subject_after_activation", "Create C after scope activation", "C retains baseline admission"),
                ("scope_escape_attempt", "Change A's editable role", "A stays held"),
                ("recovery_to_progress", "Owner fixes A's binding", "A resumes through its real command"),
            ]
        ],
    }
    result["evidence"]["observable_semantics"]["scope_coverage"] = coverage
    return packet, result, coverage


def test_enabled_but_uncovered_and_future_subjects_cannot_be_omitted():
    packet, result, coverage = _scoped_review()
    assert check_review_result(packet, result)["approval_consistent"]
    coverage["cases"] = [case for case in coverage["cases"] if case["case_id"] != "new_subject_after_activation"]
    checked = check_review_result(packet, result)
    assert "observable_semantics:scope_coverage:missing_or_duplicate_case:new_subject_after_activation" in checked["approval_blockers"]


@pytest.mark.parametrize("decision", ["overbroad", "not_yet_proven"])
def test_correct_implementation_cannot_approve_wrong_or_unproven_scope(decision):
    packet, result, coverage = _scoped_review()
    coverage["decision"] = decision
    assert not check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize("status", ["failed", "unverified"])
def test_blocker_record_without_proven_recovery_cannot_approve(status):
    packet, result, coverage = _scoped_review()
    coverage["cases"][-1]["status"] = status
    coverage["cases"][-1]["observed_outcome"] = "Blocker recorded but the affected work is still rejected."
    assert "observable_semantics:scope_coverage:case_not_proven:recovery_to_progress" in check_review_result(packet, result)["approval_blockers"]


def test_case_inapplicability_needs_a_reason():
    packet, result, coverage = _scoped_review()
    coverage["cases"][-1]["status"] = "not_applicable"
    assert not check_review_result(packet, result)["approval_consistent"]
    coverage["cases"][-1]["reason"] = "Read-only diagnostic change; recovery is unchanged and outside its accepted outcome."
    assert check_review_result(packet, result)["approval_consistent"]


def test_verified_scope_needs_a_real_covered_subject():
    packet, result, coverage = _scoped_review()
    coverage["cases"][0]["status"] = "not_applicable"
    coverage["cases"][0]["reason"] = "No selected job was exercised."
    assert "observable_semantics:scope_coverage:covered_subject_not_proven" in check_review_result(packet, result)["approval_blockers"]


def test_final_body_cannot_drop_the_risk_explanation_or_change_verdict():
    packet, result = _review()
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert "review_body:verdict_mismatch" in check_review_result(packet, result)["errors"]
    result["review_body"] = ""
    assert "review_body:missing_section:对主干的风险" in check_review_result(packet, result)["errors"]


def test_final_body_cannot_hide_the_entire_review_in_html_comment():
    packet, result = _review()
    result["review_body"] = "<!--\n" + result["review_body"] + "\n-->"
    errors = check_review_result(packet, result)["errors"]
    assert "review_body:missing_section:对主干的风险" in errors
    assert "review_body:missing_exact_head" in errors
    assert "review_body:missing_english_verdict" in errors


@pytest.mark.parametrize(
    ("candidate_decision", "verdict", "blocker"),
    [
        ("made_up", "aligned", "semantic_alignment:invalid_candidate_decision"),
        ("unknown", "aligned", "semantic_alignment:unknown_cannot_claim_alignment"),
        ("unknown", "not_applicable", "semantic_alignment:unknown_cannot_claim_alignment"),
        ("extend_vocabulary", "not_applicable", "semantic_alignment:contract_change_requires_evidence"),
        ("create_vocabulary", "not_applicable", "semantic_alignment:contract_change_requires_evidence"),
    ],
)
def test_semantic_alignment_cannot_hide_unknown_or_invalid_candidate(
    candidate_decision: str, verdict: str, blocker: str
) -> None:
    packet, result = _review()
    row = result["evidence"]["semantic_alignment"]
    row["candidate_decision"] = candidate_decision
    row["verdict"] = verdict
    checked = check_review_result(packet, result)

    assert blocker in checked["approval_blockers"]
    assert not checked["approval_consistent"]


def test_no_candidate_exits_after_scope_and_reason() -> None:
    packet, result = _review()
    result["evidence"]["semantic_alignment"] = {
        "status": "verified",
        "checked_scope": "Formatting helper and unchanged callers.",
        "impact_reason": "No shared field, state value or persistence change.",
        "verdict": "not_applicable",
    }
    assert check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize("missing", ["checked_scope", "impact_reason"])
def test_no_impact_still_needs_a_bounded_reason(missing: str) -> None:
    packet, result = _review()
    del result["evidence"]["semantic_alignment"][missing]
    assert not check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize("verdict", ["aligned", "new_semantics_justified"])
def test_contract_change_reuses_existing_review_evidence(verdict: str) -> None:
    packet, result = _review()
    row = result["evidence"]["semantic_alignment"]
    row.update(
        verdict=verdict,
        candidate_decision="extend_vocabulary",
        affected_contract="TaskState shared enum and reader compatibility.",
        evidence_refs=["repository_reuse", "observable_semantics", "validation_matrix"],
    )
    assert check_review_result(packet, result)["approval_consistent"]
    del row["affected_contract"]
    assert not check_review_result(packet, result)["approval_consistent"]


def test_scanner_blind_spot_is_reported_without_claiming_safety_or_blocking() -> None:
    packet, result = _review()
    row = result["evidence"]["semantic_alignment"]
    row.update(
        verdict="advisory",
        candidate_decision="unknown",
        impact_reason="Existing required checks pass; no changed contract lacks required evidence.",
        analysis_limit="Unchanged external pass-through is outside the bounded scanner.",
    )
    assert check_review_result(packet, result)["approval_consistent"]
    del row["analysis_limit"]
    assert not check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize("verdict", ["not_yet_proven", "violated"])
def test_contract_blocker_requires_actionable_repair(verdict: str) -> None:
    packet, result = _review()
    row = result["evidence"]["semantic_alignment"]
    row.update(
        verdict=verdict,
        candidate_decision="compatibility_only",
        affected_contract="Persisted TaskState values must remain readable.",
        trigger="PR deletes a persisted enum member.",
        observed_evidence="Old-state readback is missing or fails in validation_matrix.",
        minimum_repair="Restore decoding or add a tested migration.",
        validation_commands="pytest tests/test_state_readback.py",
    )
    assert not check_review_result(packet, result)["approval_consistent"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]
    del row["minimum_repair"]
    assert "semantic_alignment:missing_field:minimum_repair" in (
        check_review_result(packet, result)["approval_blockers"]
    )


def test_advisory_cannot_override_a_concrete_blocking_finding() -> None:
    packet, result = _review()
    result["evidence"]["semantic_alignment"].update(
        verdict="advisory", candidate_decision="unknown", analysis_limit="Scanner limit."
    )
    result["findings"] = [{"severity": "P2", "blocking": True}]
    assert not check_review_result(packet, result)["approval_consistent"]


def test_optional_semantic_evidence_cannot_hide_a_docs_contract_violation() -> None:
    packet, result = _review()
    packet["pull_requests"][0]["areas"] = {"public_docs": 1}
    result["evidence"]["semantic_alignment"].update(verdict="violated")
    assert not check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize(
    "kind", ["missing", "unverified", "empty", "blocking", "finding", "unknown_verdict"]
)
def test_approval_cannot_hide_missing_or_contradictory_evidence(kind):
    packet, result = _review()
    row = result["evidence"]["change_proportionality"]
    if kind == "missing":
        del result["evidence"]["change_proportionality"]
    elif kind == "unverified":
        row["status"] = "unverified"
    elif kind == "empty":
        for field in list(row):
            if field not in {"status", "verdict"}:
                del row[field]
    elif kind == "blocking":
        row["verdict"] = "disproportionate"
    elif kind == "unknown_verdict":
        row["verdict"] = "looks_good"
    else:
        result["findings"] = [{"severity": "P1", "blocking": False}]
    # Caller cannot weaken the policy by changing its saved plan/contract.
    packet["pull_requests"][0]["review_plan"] = {"required_evidence_ids": []}
    packet["agent_response_contract"] = {"review_execution_contract": {}}
    checked = check_review_result(packet, result)
    assert not checked["ok"]
    assert "approval_contradicts_evidence" in checked["errors"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


def test_nonblocking_suggestion_does_not_force_rejection():
    packet, result = _review()
    result["findings"] = [{"severity": "P2", "blocking": False}]
    assert check_review_result(packet, result)["approval_consistent"]


def test_review_must_say_which_model_wrote_it_in_the_published_body():
    packet, result = _review()
    assert check_review_result(packet, result)["ok"]
    result["review_body"] = result["review_body"].replace(REVIEWER_LINE + "\n\n", "")
    assert "reviewer:body_line_missing_or_ambiguous" in check_review_result(packet, result)["errors"]
    # A declaration hidden from readers is not a declaration.
    result["review_body"] = f"<!-- {REVIEWER_LINE} -->\n\n" + result["review_body"]
    assert "reviewer:body_line_missing_or_ambiguous" in check_review_result(packet, result)["errors"]


def test_published_reviewer_line_must_match_the_declaration():
    packet, result = _review()
    result["reviewer"]["declared_model"] = "Another Model 2"
    checked = check_review_result(packet, result)
    assert not checked["ok"]
    assert "reviewer:body_line_disagrees_with_declaration" in checked["errors"]


@pytest.mark.parametrize("field, value, reason", [
    ("actor_kind", "agent", "reviewer:invalid_actor_kind"),
    ("declaration_source", "guessed", "reviewer:invalid_declaration_source"),
    ("declared_model", "", "reviewer:missing_field:declared_model"),
    ("declared_provider", "  ", "reviewer:missing_field:declared_provider"),
    ("declared_model", "https://gateway.example/v1", "reviewer:not_a_product_family_name:declared_model"),
    ("declared_provider", "ops@example", "reviewer:not_a_product_family_name:declared_provider"),
])
def test_reviewer_declaration_rejects_unknown_or_infrastructure_values(field, value, reason):
    packet, result = _review()
    result["reviewer"][field] = value
    assert reason in check_review_result(packet, result)["errors"]


def test_missing_reviewer_declaration_blocks_publication_of_any_verdict():
    packet, result = _review()
    del result["reviewer"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace(
        "English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    checked = check_review_result(packet, result)
    assert "reviewer:missing_declaration" in checked["errors"]
    assert not checked["ok"]


def test_human_operator_needs_no_model_or_provider():
    packet, result = _review()
    result["reviewer"] = {"actor_kind": "human_operator", "declaration_source": "self_reported"}
    result["review_body"] = result["review_body"].replace(REVIEWER_LINE, "Reviewer: human_operator")
    assert check_review_result(packet, result)["ok"]


def _recorded_reviewer():
    packet, result = _review()
    execution = {"status": "runtime_reported", "model": "example-model-2", "provider": "OpenAI",
                 "reasoning_effort": "xhigh", "observation_id": "b" * 64}
    packet["reviewer_execution"] = execution
    result["reviewer"] = {"actor_kind": "model_agent", "declaration_source": "runtime_reported",
                          "declared_model": execution["model"], "declared_provider": execution["provider"],
                          "declared_reasoning_effort": execution["reasoning_effort"],
                          "execution_observation_id": execution["observation_id"]}
    result["review_body"] = result["review_body"].replace(
        REVIEWER_LINE, "Reviewer: model_agent · example-model-2 · OpenAI · runtime_reported · xhigh")
    return packet, result


@pytest.mark.parametrize("field,value", [
    ("declared_model", "Old Model"), ("declared_provider", "Other Provider"),
    ("declared_reasoning_effort", "low"), ("execution_observation_id", "c" * 64),
])
def test_runtime_attribution_rejects_stale_template_even_when_body_agrees(field, value):
    packet, result = _recorded_reviewer()
    assert check_review_result(packet, result)["ok"]
    previous = result["reviewer"][field]
    result["reviewer"][field] = value
    result["review_body"] = result["review_body"].replace(previous, value)
    assert f"reviewer:runtime_mismatch:{field}" in check_review_result(packet, result)["errors"]


def test_runtime_switch_and_packet_tampering_require_fresh_attribution():
    packet, result = _recorded_reviewer()
    original = copy.deepcopy(packet["reviewer_execution"])
    assert check_review_result(packet, result, current_execution=original)["ok"]
    switched = dict(original, model="example-model-3", observation_id="c" * 64)
    assert "reviewer:runtime_observation_changed" in check_review_result(
        packet, result, current_execution=switched)["errors"]
    packet["reviewer_execution"]["model"] = "Old Model"
    result["reviewer"]["declared_model"] = "Old Model"
    result["review_body"] = result["review_body"].replace("example-model-2", "Old Model")
    assert "reviewer:runtime_mismatch:declared_model" in check_review_result(
        packet, result, current_execution=original)["errors"]
    del packet["reviewer_execution"]
    assert "reviewer:runtime_mismatch:declared_model" in check_review_result(
        packet, result, current_execution=original)["errors"]


def test_missing_runtime_is_self_reported_not_fabricated_or_a_pr_blocker():
    packet, result = _review()
    packet["reviewer_execution"] = {"status": "unavailable", "reason": "session_not_bound"}
    result["review_body"] = result["review_body"].replace(REVIEWER_LINE, REVIEWER_LINE + " · self_reported")
    assert check_review_result(packet, result)["ok"]
    result["reviewer"]["declaration_source"] = "runtime_reported"
    assert "reviewer:runtime_observation_missing" in check_review_result(packet, result)["errors"]
    assert not check_review_result(packet, result)["approval_blockers"]


def test_runtime_effort_and_basis_must_be_visible_in_the_published_line():
    packet, result = _recorded_reviewer()
    result["review_body"] = result["review_body"].replace(" · xhigh", "")
    assert "reviewer:runtime_basis_not_published" in check_review_result(packet, result)["errors"]


def test_mapped_spec_basis_binds_criteria_to_the_head_and_the_published_body():
    packet, result = _review()
    result["evidence"]["problem_context"]["spec_basis"] = _mapped_spec_basis()
    unpublished = check_review_result(packet, result)
    assert not unpublished["ok"]
    assert "review_body:spec_reference_not_published:EX-1" in unpublished["errors"]
    assert ("review_body:spec_reference_not_published:docs/architecture/rfcs/example-v0.md#acceptance"
            in unpublished["errors"])
    _publish_spec_references(result)
    checked = check_review_result(packet, result)
    assert checked["ok"] and checked["approval_consistent"]


def test_unmet_specification_criterion_cannot_be_approved():
    packet, result = _review()
    basis = _mapped_spec_basis()
    basis["criteria"][1] = {
        "criterion_id": "EX-2",
        "requirement": "Migration of old rows.",
        "disposition": "not_met",
        "observed_gap": "Old rows keep the previous shape.",
        "minimum_repair": "Migrate old rows before readback.",
    }
    result["evidence"]["problem_context"]["spec_basis"] = basis
    _publish_spec_references(result)
    checked = check_review_result(packet, result)
    assert "problem_context:spec_basis:unmet_criterion:EX-2" in checked["approval_blockers"]
    assert "approval_contradicts_evidence" in checked["errors"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace(
        "English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


@pytest.mark.parametrize("mutation, blocker", [
    ("unknown_disposition", "problem_context:spec_basis:invalid_disposition:EX-1"),
    ("implemented_without_symbol", "problem_context:spec_basis:EX-1:missing_field:symbol_or_path"),
    ("deferred_without_successor", "problem_context:spec_basis:EX-2:missing_field:successor_or_gap"),
    ("duplicate_criterion", "problem_context:spec_basis:duplicate_criterion:EX-1"),
    ("no_revision", "problem_context:spec_basis:missing_field:spec_revision"),
    ("mapped_without_source", "problem_context:spec_basis:mapped_without_spec_source"),
    ("unread_spec", "problem_context:spec_basis:blocking_decision"),
    ("no_spec_with_source", "problem_context:spec_basis:no_spec_cannot_cite_a_source"),
    ("missing", "problem_context:spec_basis:value_not_object"),
])
def test_spec_basis_cannot_approve_with_incomplete_or_contradictory_mapping(mutation, blocker):
    packet, result = _review()
    basis = _mapped_spec_basis()
    if mutation == "unknown_disposition":
        basis["criteria"][0]["disposition"] = "mostly_done"
    elif mutation == "implemented_without_symbol":
        del basis["criteria"][0]["symbol_or_path"]
    elif mutation == "deferred_without_successor":
        del basis["criteria"][1]["successor_or_gap"]
    elif mutation == "duplicate_criterion":
        basis["criteria"][1]["criterion_id"] = "EX-1"
    elif mutation == "no_revision":
        del basis["spec_revision"]
    elif mutation == "mapped_without_source":
        basis["spec_source"] = "none"
    elif mutation == "unread_spec":
        basis = {"decision": "not_yet_proven", "spec_source": "accepted_rfc",
                 "reason": "The cited RFC revision could not be opened."}
    elif mutation == "no_spec_with_source":
        basis = {"decision": "no_spec", "spec_source": "accepted_rfc", "reason": "x"}
    if mutation == "missing":
        del result["evidence"]["problem_context"]["spec_basis"]
    else:
        result["evidence"]["problem_context"]["spec_basis"] = basis
    _publish_spec_references(result)
    checked = check_review_result(packet, result)
    assert blocker in checked["approval_blockers"]
    assert not checked["ok"]


@pytest.mark.parametrize("revision", [None, 0, True, "1", 5, 999])
def test_old_or_invalid_policy_cannot_certify_current_approval(revision):
    packet, result = _review()
    result["review_policy_revision"] = revision
    checked = check_review_result(packet, result)
    assert "review_policy_revision:stale_or_missing" in checked["approval_blockers"]
    assert not checked["ok"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


def test_pinned_result_is_rejected_after_installed_policy_bump(monkeypatch):
    packet, result = _review()
    pinned_revision = result["review_policy_revision"]
    monkeypatch.setattr(
        review_contract,
        "REVIEW_POLICY_REVISION",
        pinned_revision + 1,
    )

    checked = check_review_result(packet, result)

    assert "review_policy_revision:stale_or_missing" in checked["approval_blockers"]
    assert not checked["ok"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


def test_verified_label_and_generic_prose_do_not_replace_rule_ownership():
    packet, result = _review()
    del result["evidence"]["repository_reuse"]["rule_ownership"]
    result["evidence"]["repository_reuse"]["evidence"] = "All providers passed."
    checked = check_review_result(packet, result)
    assert (
        "repository_reuse:missing_field:rule_ownership" in checked["approval_blockers"]
    )
    assert not checked["ok"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


@pytest.mark.parametrize(
    "evidence_id", ["symbol_map", "walkthroughs", "validation_matrix"]
)
def test_generic_prose_cannot_replace_structured_evidence(evidence_id):
    packet, result = _review()
    result["evidence"][evidence_id] = {
        "status": "verified",
        "evidence": "Generic prose only.",
    }

    checked = check_review_result(packet, result)

    assert not checked["approval_consistent"]
    assert any(
        blocker.startswith(f"{evidence_id}:")
        for blocker in checked["approval_blockers"]
    )
    assert "approval_contradicts_evidence" in checked["errors"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


def test_structured_evidence_enforces_count_fields_and_required_cases():
    packet, result = _review()
    result["evidence"]["symbol_map"]["items"] = [
        result["evidence"]["symbol_map"]["items"][0]
    ]
    del result["evidence"]["walkthroughs"]["negative"]["error_or_retry_owner"]
    result["evidence"]["validation_matrix"]["items"] = [
        item
        for item in result["evidence"]["validation_matrix"]["items"]
        if item["case_id"] != "repository_required_checks"
    ]

    checked = check_review_result(packet, result)

    assert "symbol_map:too_few_items" in checked["approval_blockers"]
    assert (
        "walkthroughs:negative:missing_field:error_or_retry_owner"
        in checked["approval_blockers"]
    )
    assert (
        "validation_matrix:missing_required_case:repository_required_checks"
        in checked["approval_blockers"]
    )


@pytest.mark.parametrize("mutation", ["head", "duplicate", "shape"])
def test_saved_head_must_match_exactly_once(mutation):
    packet, result = _review()
    if mutation == "head":
        result["target_exact_head"] = "42@" + "b" * 40
    elif mutation == "duplicate":
        packet["pull_requests"].append(copy.deepcopy(packet["pull_requests"][0]))
    else:
        packet["pull_requests"] = {}
    with pytest.raises((ValueError, TypeError)):
        check_review_result(packet, result)


def test_inventory_only_head_cannot_certify_a_new_review() -> None:
    packet, result = _review()
    packet["pull_requests"][0]["review_action_kind"] = None

    with pytest.raises(ValueError, match="inventory-only exact head"):
        check_review_result(packet, result)


@pytest.mark.parametrize(
    ("semantic_verdict", "expected_exit"),
    [("not_applicable", 0), ("new_semantics_justified", 0), ("advisory", 0),
     ("not_yet_proven", 1), ("violated", 1)],
)
def test_public_cli_checks_semantic_boundaries_without_github_or_checkpoint_effects(
    tmp_path, monkeypatch, capsys, semantic_verdict: str, expected_exit: int
):
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    packet, result = _review()
    semantic = result["evidence"]["semantic_alignment"]
    semantic["verdict"] = semantic_verdict
    if semantic_verdict == "new_semantics_justified":
        semantic.update(
            candidate_decision="extend_vocabulary",
            affected_contract="Shared TaskState enum.",
            evidence_refs=["repository_reuse", "observable_semantics", "validation_matrix"],
        )
    elif semantic_verdict == "advisory":
        semantic.update(candidate_decision="unknown", analysis_limit="Dynamic pass-through.")
    elif semantic_verdict in ("not_yet_proven", "violated"):
        semantic.update(
            candidate_decision="compatibility_only",
            affected_contract="Persisted TaskState compatibility.",
            trigger="Deleted state member.",
            observed_evidence="Old-state readback is missing or fails.",
            minimum_repair="Restore decoding or validate migration.",
            validation_commands="pytest tests/test_state_readback.py",
        )
    packet_path, result_path = tmp_path / "packet.json", tmp_path / "result.json"
    packet_path.write_text(json.dumps(packet))
    result_path.write_text(json.dumps(result))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(
        "loopx.cli_commands.pr_review.resolve_current_github_repository",
        lambda: pytest.fail("result check must not discover GitHub"),
    )
    argv = [
        "--format",
        "json",
        "pr-review",
        "--check-result",
        str(result_path),
        "--packet",
        str(packet_path),
    ]
    assert main(argv) == expected_exit
    assert json.loads(capsys.readouterr().out)["approval_consistent"] is (expected_exit == 0)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    result["evidence"]["failure_analysis"]["status"] = "unverified"
    result_path.write_text(json.dumps(result))
    assert main(argv) == 1
    assert not json.loads(capsys.readouterr().out)["approval_consistent"]


def test_unreadable_check_input_does_not_expose_local_path(tmp_path, capsys):
    path = tmp_path / "not-present.json"
    assert (
        main(
            [
                "--format",
                "json",
                "pr-review",
                "--check-result",
                str(path),
                "--packet",
                str(path),
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert str(tmp_path) not in output
    assert "unreadable" in json.loads(output)["error"]


def _delivery_review(*, area="product_runtime", verdict="goal_achieved"):
    packet, result = _review(area=area)
    result["evidence"]["problem_context"].update(
        goal_basis="Public issue #42: interrupted exports must resume without duplicate rows.",
        author_claim="Repair export retry on the existing command.",
        before_after_scenario="After a lost response, retry returns the original export receipt.",
        observable_outcome="The real command reuses the committed receipt; regression fails on base.",
        verdict=verdict,
    )
    if verdict == "justified_increment":
        result["evidence"]["problem_context"]["remaining_gap"] = "Scheduled retries remain in the owning scheduler task."
    _publish_problem_explanation(result)
    return packet, result


@pytest.mark.parametrize("area", ["product_runtime", "public_docs", "test_or_example"])
@pytest.mark.parametrize("verdict", ["off_goal", "fragmented", "not_yet_proven"])
def test_green_review_cannot_approve_unjustified_delivery(area, verdict):
    packet, result = _delivery_review(area=area, verdict=verdict)
    result["evidence"]["problem_context"].update(
        reason="The change adds a receipt field but does not repair export retry.",
        minimum_repair="Wire and validate the existing retry path in this slice.",
    )
    checked = check_review_result(packet, result)
    assert not checked["approval_consistent"]
    assert "problem_context:blocking_verdict" in checked["approval_blockers"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


@pytest.mark.parametrize("verdict", [None, "looks_useful", "not_applicable"])
def test_delivery_judgment_cannot_be_omitted_or_invented(verdict):
    packet, result = _delivery_review(verdict=verdict)
    checked = check_review_result(packet, result)
    assert not checked["approval_consistent"]
    assert "problem_context:missing_or_invalid_verdict" in checked["approval_blockers"]


def test_goal_basis_is_required_even_for_small_docs_changes():
    packet, result = _delivery_review(area="public_docs")
    del result["evidence"]["problem_context"]["goal_basis"]
    checked = check_review_result(packet, result)
    assert "problem_context:missing_field:goal_basis" in checked["approval_blockers"]


@pytest.mark.parametrize("area", ["product_runtime", "public_docs", "test_or_example"])
def test_qualified_increment_does_not_have_to_finish_the_parent_goal(area):
    packet, result = _delivery_review(area=area, verdict="justified_increment")
    result["evidence"]["problem_context"].update(
        goal_basis="Accepted export-recovery contract; no LoopX roadmap id required.",
        observable_outcome="The real writer now preserves the operation id across restart.",
        remaining_gap="Automatic retries still need the scheduler integration.",
        next_step="Existing recovery task #43 consumes this writer; its owner retains scheduling.",
        boundary_reason="Durability is independently testable and revertible; coupling scheduler behavior would obscure this contract.",
    )
    _publish_problem_explanation(result)
    assert check_review_result(packet, result)["approval_consistent"]
    for field in ("remaining_gap", "next_step", "boundary_reason"):
        incomplete = copy.deepcopy(result)
        del incomplete["evidence"]["problem_context"][field]
        checked = check_review_result(packet, incomplete)
        assert f"problem_context:missing_field:{field}" in checked["approval_blockers"]


def test_completed_scoped_task_does_not_require_invented_followup():
    packet, result = _delivery_review(area="public_docs")
    result["evidence"]["problem_context"].update(
        goal_basis="Maintainer request to correct a broken installation command.",
        observable_outcome="The documented command succeeds on the supported release.",
        non_goals="No claim of implementing the broader product roadmap.",
    )
    _publish_problem_explanation(result)
    assert check_review_result(packet, result)["approval_consistent"]
    assert not check_review_result(packet, result)["evidence_truth_verified"]


def test_cli_rejects_goal_incomplete_approval_without_mutating_packet(tmp_path, capsys):
    packet, result = _delivery_review(verdict="fragmented")
    result["evidence"]["problem_context"].update(
        reason="The new serializer has no production consumer and leaves retry broken.",
        minimum_repair="Complete the owning retry transaction or remove the unused serializer.",
    )
    packet_path, result_path = tmp_path / "packet.json", tmp_path / "review.json"
    packet_path.write_text(json.dumps(packet))
    result_path.write_text(json.dumps(result))
    before = result_path.read_bytes()
    main(
        [
            "--format",
            "json",
            "pr-review",
            "--check-result",
            str(result_path),
            "--packet",
            str(packet_path),
        ]
    )
    checked = json.loads(capsys.readouterr().out)
    assert not checked["approval_consistent"]
    assert "problem_context:blocking_verdict" in checked["approval_blockers"]
    assert not checked["external_writes_performed"]
    assert result_path.read_bytes() == before


def _compatibility(*, decision="retain", boundary="independent"):
    return {
        "decision": decision,
        "reason": "Keep the old reader until supported offline clients complete their upgrade window.",
        "consumer_inventory": "cli/request.py and supported mobile client releases call server/decode.py.",
        "deployment_boundary": boundary,
        "persisted_contract": "Receipt ids and digests remain stable; requests are not stored.",
        "simpler_alternative": "One live wire version cannot yet serve the deployed offline clients.",
        "validation_evidence": "validation_matrix: old/new client and immutable receipt readback cases.",
    }


def test_receipt_compatibility_prose_alone_cannot_justify_parallel_wires():
    packet, result = _review()
    row = result["evidence"]["code_volume"]
    row.pop("compatibility_assessment")
    row["compatibility_or_migration_need"] = "Keep v2 so old receipts remain recoverable."
    checked = check_review_result(packet, result)
    assert not checked["approval_consistent"]
    assert "code_volume:compatibility_assessment:value_not_object" in checked["approval_blockers"]


@pytest.mark.parametrize("missing", [
    "consumer_inventory", "deployment_boundary", "persisted_contract",
    "simpler_alternative", "validation_evidence", "reason",
])
def test_compatibility_retention_needs_separate_evidence(missing):
    packet, result = _review()
    assessment = _compatibility()
    del assessment[missing]
    result["evidence"]["code_volume"]["compatibility_assessment"] = assessment
    checked = check_review_result(packet, result)
    assert not checked["approval_consistent"]
    assert f"code_volume:compatibility_assessment:missing_field:{missing}" in checked["approval_blockers"]


@pytest.mark.parametrize("boundary", ["independent", "co_deployed", "persisted_only", "mixed"])
def test_real_compatibility_obligations_may_be_retained(boundary):
    packet, result = _review()
    assessment = _compatibility(boundary=boundary)
    if boundary != "independent":
        assessment.update(
            consumer_inventory="recovery/replay.py reads durable pending requests after restart.",
            persisted_contract="Historical request bytes are stored, not just result receipts.",
            simpler_alternative="Consolidate current writers but retain the durable request decoder.",
            reason="Old pending requests must remain readable until explicit migration drains them.",
        )
    result["evidence"]["code_volume"]["compatibility_assessment"] = assessment
    assert check_review_result(packet, result)["approval_consistent"]


def test_optional_simplification_can_approve_with_a_concrete_nonblocking_followup():
    packet, result = _review()
    assessment = _compatibility(decision="follow_up", boundary="co_deployed")
    assessment.update(
        simpler_alternative="Replace the two harmless local wrappers with one named intent argument.",
        reason="P2: consolidate wrappers next time their owner changes; neither duplicates decision rules.",
    )
    result["evidence"]["code_volume"]["compatibility_assessment"] = assessment
    result["findings"] = [{"severity": "P2", "blocking": False}]
    assert check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize("decision", ["simplify_now", "not_yet_proven"])
def test_required_simplification_or_material_unknown_cannot_claim_approval(decision):
    packet, result = _review()
    result["evidence"]["code_volume"]["compatibility_assessment"] = _compatibility(decision=decision)
    checked = check_review_result(packet, result)
    assert not checked["approval_consistent"]
    assert "code_volume:compatibility_assessment:blocking_decision" in checked["approval_blockers"]
    result["verdict"] = "REQUEST_CHANGES"
    result["review_body"] = result["review_body"].replace("English verdict: APPROVE", "English verdict: REQUEST_CHANGES")
    assert check_review_result(packet, result)["ok"]


@pytest.mark.parametrize("patch,blocker", [
    ({"decision": "looks_good"}, "invalid_decision"),
    ({"deployment_boundary": "maybe"}, "invalid_deployment_boundary"),
    ({"deployment_boundary": "unknown"}, "unknown_boundary_cannot_justify_decision"),
])
def test_unknown_compatibility_cannot_be_relabelled_as_verified(patch, blocker):
    packet, result = _review()
    result["evidence"]["code_volume"]["compatibility_assessment"] = {**_compatibility(), **patch}
    assert f"code_volume:compatibility_assessment:{blocker}" in check_review_result(packet, result)["approval_blockers"]


def test_unrelated_change_needs_only_a_scoped_compatibility_reason():
    packet, result = _review(area="public_docs")
    assessment = result["evidence"]["code_volume"]["compatibility_assessment"]
    assert set(assessment) == {"decision", "reason"}
    assert check_review_result(packet, result)["approval_consistent"]
    del assessment["reason"]
    assert not check_review_result(packet, result)["approval_consistent"]


def test_projected_compatibility_rules_cannot_mutate_the_checker():
    packet, result = _review()
    contract = build_review_execution_contract()
    rule = next(row for row in contract["evidence_requirements"] if row["evidence_id"] == "code_volume")
    rule["compatibility_assessment"]["blocking_decisions"].clear()
    rule["compatibility_assessment"]["applicable_fields"].clear()
    packet["agent_response_contract"] = {"review_execution_contract": contract}
    result["evidence"]["code_volume"]["compatibility_assessment"] = _compatibility(decision="simplify_now")
    assert not check_review_result(packet, result)["approval_consistent"]


@pytest.mark.parametrize("identity", [True, False, ["EX-1"], {"id": "EX-1"}, 1, ""])
def test_a_criterion_identity_must_be_published_text(identity):
    """A criterion that cannot be named in the body is not a criterion.

    Bools hash and are non-empty, and non-strings silently fall out of the
    published-body check, so the type is validated where membership, duplicate
    detection and publication all assume it — not left to a later set or dict
    operation that would either pass or fail with a command-level error.
    """
    packet, result = _review()
    basis = _mapped_spec_basis()
    basis["criteria"][0]["criterion_id"] = identity
    result["evidence"]["problem_context"]["spec_basis"] = basis
    _publish_spec_references(result)
    checked = check_review_result(packet, result)
    assert "problem_context:spec_basis:invalid_criterion_id" in checked["approval_blockers"]
    assert not checked["ok"]


@pytest.mark.parametrize("field", ["spec_ref", "spec_revision"])
@pytest.mark.parametrize("value", [True, {"path": "x"}, ["b" * 40], 1, "   "])
def test_a_mapped_specification_reference_must_be_published_text(field, value):
    """A reference the body cannot carry cannot pin the reviewed specification.

    Non-string values pass the non-empty field check and then fall out of the
    published-body comparison, so an approval would cite nothing a reader on
    another host could open.
    """
    packet, result = _review()
    basis = _mapped_spec_basis()
    basis[field] = value
    result["evidence"]["problem_context"]["spec_basis"] = basis
    _publish_spec_references(result)
    checked = check_review_result(packet, result)
    assert f"problem_context:spec_basis:invalid_{field}" in checked["approval_blockers"]
    assert not checked["ok"] and not checked["approval_consistent"]


@pytest.mark.parametrize(("criterion_id", "spec_revision", "line", "unpublished"), [
    # A criterion named only inside a longer criterion is not published.
    ("EX-1", "b" * 40, "EX-10 implemented; EX-2 deferred.", "EX-1"),
    # A short identity hidden inside the pinned commit id is not published.
    ("bbb", "b" * 40, "EX-2 deferred.", "bbb"),
])
def test_a_specification_reference_must_appear_as_a_whole_token(
    criterion_id, spec_revision, line, unpublished,
):
    """Another operator reads the body, so a substring of another word names nothing."""
    packet, result = _review()
    basis = _mapped_spec_basis()
    basis["criteria"][0]["criterion_id"] = criterion_id
    basis["spec_revision"] = spec_revision
    result["evidence"]["problem_context"]["spec_basis"] = basis
    result["review_body"] += (
        "\n\nSpec basis: docs/architecture/rfcs/example-v0.md#acceptance @ "
        + spec_revision + " — " + line + "\n"
    )
    checked = check_review_result(packet, result)
    assert f"review_body:spec_reference_not_published:{unpublished}" in checked["errors"]
    assert not checked["ok"] and not checked["approval_consistent"]

    result["review_body"] += f"{criterion_id} implemented.\n"
    assert check_review_result(packet, result)["ok"]


def test_reviewer_line_must_name_each_declared_value_as_a_whole_token():
    packet, result = _review()
    result["reviewer"]["declared_provider"] = "AI"
    result["review_body"] = result["review_body"].replace(
        REVIEWER_LINE, "Reviewer: model_agent · Example Model 1 · OpenAI")
    checked = check_review_result(packet, result)
    assert "reviewer:body_line_disagrees_with_declaration" in checked["errors"]

    result["review_body"] = result["review_body"].replace(
        "· OpenAI", "· AI (self-reported family)")
    assert check_review_result(packet, result)["ok"]


@pytest.mark.parametrize("revision", ["main", "v1.2.0", "b" * 12, "B" * 39])
def test_a_repository_specification_is_pinned_by_a_full_commit_id(revision):
    """A branch, tag or abbreviated id cannot name the text the review judged."""
    packet, result = _review()
    basis = _mapped_spec_basis()
    basis["spec_revision"] = revision
    result["evidence"]["problem_context"]["spec_basis"] = basis
    result["review_body"] += (
        "\n\nSpec basis: docs/architecture/rfcs/example-v0.md#acceptance @ "
        + revision + " — EX-1 implemented; EX-2 deferred.\n"
    )
    checked = check_review_result(packet, result)
    assert "problem_context:spec_basis:spec_revision_not_a_commit_id" in checked["approval_blockers"]
    assert not checked["ok"]

    # A linked task or review thread has no commit; its revision stays free text.
    basis["spec_source"] = "linked_issue_or_task"
    assert check_review_result(packet, result)["ok"]


def test_a_published_mapping_must_carry_its_immutable_revision():
    """Another host can only open the same text if the body pins the revision.

    The path moves with the branch, so publishing the path alone lets a reader
    find a different document than the one the review judged against.
    """
    packet, result = _review()
    result["evidence"]["problem_context"]["spec_basis"] = _mapped_spec_basis()
    revision = "b" * 40
    result["review_body"] += (
        "\n\nSpec basis: docs/architecture/rfcs/example-v0.md#acceptance"
        " — EX-1 implemented; EX-2 deferred.\n"
    )
    assert revision not in result["review_body"]
    checked = check_review_result(packet, result)
    # A body-level gap blocks publication rather than scoring the evidence.
    assert f"review_body:spec_reference_not_published:{revision}" in checked["errors"]
    assert not checked["ok"] and not checked["approval_consistent"]

    # Publishing the same mapping with its revision closes the gap.
    result["review_body"] += "Spec revision: " + revision + "\n"
    assert check_review_result(packet, result)["ok"]
