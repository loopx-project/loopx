from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.capabilities.pr_review_queue import (
    build_agent_response_contract,
    build_review_plan,
    build_review_template,
)
from loopx.capabilities.pr_review_queue.review_contract import build_review_execution_contract


def _item(*, areas: dict[str, int]) -> dict[str, object]:
    return {
        "number": 42,
        "base_ref": "main",
        "head_oid": "a" * 40,
        "areas": areas,
        "key_files": [
            {"path": "src/runtime.py", "additions": 20, "deletions": 5},
            {"path": "tests/test_runtime.py", "additions": 30, "deletions": 0},
        ],
    }


def test_execution_contract_owns_deep_review_requirements() -> None:
    response = build_agent_response_contract()

    selection = response["selection_execution_contract"]
    assert selection == {
        "schema_version": "pr_review_selection_execution_contract_v0",
        "explicit_selection_scope": "ordering_only",
        "review_action_authority": "pull_requests[].review_action_kind",
        "review_sequence_membership": "review_action_kind_non_null_only",
        "no_action_inventory_location": "pull_requests",
        "generic_rereview_terms_force_fresh_audit": False,
        "no_action_behavior": "compact_exact_head_conclusion_readback_only",
        "no_action_execution_artifacts": "plan_and_template_null_commands_empty",
        "force_fresh_audit_requires": (
            "An explicit request to rerun evidence despite the unchanged/no-action "
            "exact head, or a concrete new concern or evidence invalidation, encoded "
            "as --fresh-audit-exact-head NUMBER@HEAD_OID."
        ),
    }

    assert response["required_packet_fields_to_preserve"] == [
        "agent_response_contract",
        "agent_response_contract.review_execution_contract",
        "result_completeness",
        "scheduling_policy",
        "review_groups",
        "pull_requests[review_action_kind!=null].review_plan",
        "pull_requests[review_action_kind!=null].review_template",
        "pull_requests[review_action_kind!=null].evidence_commands",
    ]
    contract = response["review_execution_contract"]
    assert contract["schema_version"] == "pull_request_review_execution_contract_v2"
    requirements = {
        item["evidence_id"]: item for item in contract["evidence_requirements"]
    }
    assert set(requirements) == {
        "problem_context",
        "architecture_flow",
        "repository_reuse",
        "observable_semantics",
        "changed_line_classification",
        "scope_fit",
        "symbol_map",
        "walkthroughs",
        "validation_matrix",
        "failure_analysis",
        "code_volume",
        "change_proportionality",
        "default_off_isolation",
        "authority_semantics",
        "typed_state_rule",
        "domain_neutrality",
        "behavior_change_disclosure",
        "guidance_vs_obligation",
        "durable_smoke_value",
        "semantic_alignment",
    }
    assert requirements["symbol_map"]["item_count"] == {
        "minimum": 2,
        "maximum": 5,
    }
    # Every review is bound to the specification it judged, inside the goal
    # judgment that owns it, rather than to its own narrative.
    assert "spec_basis" in requirements["problem_context"]["fields"]
    assert "not_met" in contract["spec_basis_assessment"]["blocking_dispositions"]
    # Provenance is carried by the result, never scored as review evidence.
    assert "reviewer" not in requirements
    assert contract["reviewer_declaration"]["actor_kinds"] == [
        "model_agent",
        "human_operator",
    ]
    assert contract["reviewer_declaration"]["model_agent_fields"] == [
        "declared_model",
        "declared_provider",
    ]
    assert "caller_evidence" in requirements["symbol_map"]["item_fields"]
    assert "negative_fields" in requirements["walkthroughs"]
    assert "regression_test" in requirements["failure_analysis"]["fields"]
    assert requirements["scope_fit"]["required_when"] == "behavior_bearing_change"
    assert "instruction_install_or_load_path" in requirements["scope_fit"]["fields"]
    assert "target_audience_and_scope" in requirements["scope_fit"]["fields"]
    assert requirements["typed_state_rule"]["required_when"] == "code_change"
    assert "substring denylists" in requirements["typed_state_rule"]["rule"]
    assert "domain-neutral" in requirements["domain_neutrality"]["rule"]
    assert requirements["behavior_change_disclosure"]["required_when"] == (
        "behavior_bearing_change"
    )
    assert (
        "silent behavior changes" in requirements["behavior_change_disclosure"]["rule"]
    )
    assert "must_attempt_work" in requirements["guidance_vs_obligation"]["rule"]
    attribution = requirements["validation_matrix"]["failure_attribution"]
    assert attribution["non_blocking_dispositions"] == [
        "pre_existing_unrelated", "external_unrelated",
    ]
    assert "same normalized failing identity" in attribution["rule"]
    assert "merge readiness remains on hold" in attribution["rule"]
    assert "APPROVE when a required red check" in contract["verdict_policy"][
        "unrelated_validation_failure"
    ]
    proportionality = requirements["change_proportionality"]
    assert proportionality["required_when"] == "code_change"
    assert proportionality["verdict_values"] == [
        "proportionate",
        "disproportionate",
        "not_yet_proven",
    ]
    assert "smallest_viable_fix" in proportionality["fields"]
    assert "maintenance_and_migration_cost" in proportionality["fields"]
    assert "green CI" in proportionality["rule"]
    assert "original problem" in proportionality["rule"]
    semantic = requirements["semantic_alignment"]
    assert semantic["required_when"] == "semantic_alignment_required"
    assert set(semantic["candidate_decisions"]) == {
        "reuse_existing",
        "extend_vocabulary",
        "create_vocabulary",
        "local_only",
        "external_input",
        "compatibility_only",
        "unknown",
    }
    assert "unjustified budget increases" in semantic["rule"]
    assert semantic["fields"] == ["checked_scope", "impact_reason", "verdict"]
    assert semantic["fields_by_verdict"]["not_applicable"] == []
    assert "analysis_limit" in semantic["fields_by_verdict"]["advisory"]
    assert "minimum_repair" in semantic["fields_by_verdict"]["not_yet_proven"]
    isolation = requirements["default_off_isolation"]
    assert isolation["required_when"] == "behavior_bearing_change"
    assert isolation["verdict_values"] == [
        "isolated",
        "not_isolated",
        "not_applicable",
        "not_yet_proven",
    ]
    assert "disabled_prompt_or_guidance" in isolation["fields"]
    assert "declared_activation_scope" in isolation["fields"]
    assert "availability_signals_that_do_not_activate" in isolation["fields"]
    assert "installed_or_auto_loaded_instruction_surfaces" in isolation["fields"]
    assert "disabled_user_experience_parity" in isolation["fields"]
    assert "paired_counterfactual_validation" in isolation["fields"]
    assert "absence of a topology" in isolation["rule"]
    assert "automatically loaded" in isolation["rule"]
    assert "availability from activation" in isolation["rule"]
    assert "runtime default of false" in isolation["rule"]
    authority = requirements["authority_semantics"]
    assert authority["verdict_values"] == [
        "aligned",
        "misleading",
        "not_applicable",
        "not_yet_proven",
    ]
    assert "actual_actor_lifecycle" in authority["fields"]
    assert "ephemeral sub-agent" in authority["rule"]
    assert contract["completion_gate"]["metadata_only_verdict_allowed"] is False
    assert contract["completion_gate"]["stale_head_verdict_allowed"] is False
    assert contract["completion_gate"]["blocking_evidence_verdicts"] == {
        "problem_context": ["off_goal", "fragmented", "not_yet_proven"],
        "repository_reuse": ["unjustified_duplication", "not_yet_proven"],
        "observable_semantics": ["unintended_drift", "not_yet_proven"],
        "change_proportionality": ["disproportionate", "not_yet_proven"],
        "default_off_isolation": ["not_isolated", "not_yet_proven"],
        "authority_semantics": ["misleading", "not_yet_proven"],
        "semantic_alignment": ["not_yet_proven", "violated"],
    }
    assert contract["finding_contract"]["findings_first"] is True
    verdict = contract["verdict_policy"]
    assert verdict["open_pr_blocking_finding"] == "REQUEST_CHANGES"
    assert "REQUEST_CHANGES" in verdict["open_pr_unresolved_proportionality"]
    assert "original problem" in verdict["materially_expanded_rereview"]
    assert verdict["open_pr_non_blocking_finding"] == "APPROVE"
    assert verdict["open_pr_no_finding"] == "APPROVE"
    assert "author-owned PR" in verdict["author_owned_no_blocker_fallback"]


def test_runtime_plan_requires_symbol_map_and_negative_walkthrough() -> None:
    plan = build_review_plan(_item(areas={"product_runtime": 1, "test_or_example": 1}))

    assert plan["target"] == {
        "number": 42,
        "base_ref": "main",
        "head_oid": "a" * 40,
        "exact_head_key": f"42@{'a' * 40}",
    }
    assert plan["applicability"]["code_change"] is True
    assert plan["applicability"]["symbol_map_required"] is True
    assert plan["applicability"]["scope_fit_required"] is True
    assert plan["applicability"]["change_proportionality_required"] is True
    assert plan["applicability"]["default_off_isolation_required"] is True
    assert plan["applicability"]["authority_semantics_required"] is True
    assert plan["applicability"]["negative_walkthrough_required"] is True
    assert plan["applicability"]["typed_state_rule_required"] is True
    assert plan["applicability"]["behavior_change_disclosure_required"] is True
    assert plan["applicability"]["domain_neutrality_required"] is True
    assert plan["applicability"]["guidance_vs_obligation_required"] is True
    assert "symbol_map" in plan["required_evidence_ids"]
    assert "scope_fit" in plan["required_evidence_ids"]
    assert "change_proportionality" in plan["required_evidence_ids"]
    assert "default_off_isolation" in plan["required_evidence_ids"]
    assert "authority_semantics" in plan["required_evidence_ids"]
    assert "typed_state_rule" in plan["required_evidence_ids"]
    assert "behavior_change_disclosure" in plan["required_evidence_ids"]
    assert "domain_neutrality" in plan["required_evidence_ids"]
    assert "guidance_vs_obligation" in plan["required_evidence_ids"]
    assert set(plan["result_template"]["evidence"]) == set(
        plan["required_evidence_ids"]
    )
    assert all(
        item["status"] == "unverified"
        for item in plan["result_template"]["evidence"].values()
    )


def test_docs_plan_does_not_invent_code_symbols() -> None:
    item = _item(areas={"public_docs": 2})
    item["key_files"] = [{"path": "docs/design.md", "additions": 15, "deletions": 2}]

    plan = build_review_plan(item)
    template = build_review_template(item)

    assert plan["applicability"]["docs_only"] is True
    assert plan["applicability"]["symbol_map_required"] is False
    assert plan["applicability"]["scope_fit_required"] is False
    assert plan["applicability"]["change_proportionality_required"] is False
    assert plan["applicability"]["default_off_isolation_required"] is False
    assert plan["applicability"]["authority_semantics_required"] is False
    assert plan["applicability"]["typed_state_rule_required"] is False
    assert plan["applicability"]["behavior_change_disclosure_required"] is False
    assert plan["applicability"]["domain_neutrality_required"] is False
    assert plan["applicability"]["guidance_vs_obligation_required"] is False
    assert "symbol_map" not in plan["required_evidence_ids"]
    assert "scope_fit" not in plan["required_evidence_ids"]
    assert "change_proportionality" not in plan["required_evidence_ids"]
    assert "default_off_isolation" not in plan["required_evidence_ids"]
    assert "authority_semantics" not in plan["required_evidence_ids"]
    assert "typed_state_rule" not in plan["required_evidence_ids"]
    assert "domain_neutrality" not in plan["required_evidence_ids"]
    assert "behavior_change_disclosure" not in plan["required_evidence_ids"]
    assert "guidance_vs_obligation" not in plan["required_evidence_ids"]
    concrete = next(
        section for section in template["sections"] if section["label"] == "具体改动"
    )
    assert "### 关键内容讲解" in concrete["agent_instruction"]
    assert template["review_order"] == ["docs/design.md"]


def test_agent_instruction_plan_requires_default_off_user_experience_evidence() -> None:
    item = _item(areas={"agent_instruction_surface": 1})
    item["key_files"] = [
        {"path": "skills/project/SKILL.md", "additions": 20, "deletions": 2}
    ]

    plan = build_review_plan(item)

    assert plan["applicability"]["code_change"] is False
    assert plan["applicability"]["behavioral_policy_change"] is True
    assert plan["applicability"]["behavior_bearing_change"] is True
    assert plan["applicability"]["smoke_or_example_only"] is False
    assert plan["applicability"]["durable_smoke_value_required"] is False
    assert plan["applicability"]["scope_fit_required"] is True
    assert plan["applicability"]["default_off_isolation_required"] is True
    assert plan["applicability"]["negative_walkthrough_required"] is True
    assert plan["applicability"]["behavior_change_disclosure_required"] is True
    assert plan["applicability"]["guidance_vs_obligation_required"] is True
    assert "scope_fit" in plan["required_evidence_ids"]
    assert "default_off_isolation" in plan["required_evidence_ids"]
    assert "behavior_change_disclosure" in plan["required_evidence_ids"]
    assert "guidance_vs_obligation" in plan["required_evidence_ids"]
    assert "symbol_map" not in plan["required_evidence_ids"]


def test_smoke_only_plan_requires_durable_value_evidence() -> None:
    item = _item(areas={"test_or_example": 1})
    item["key_files"] = [
        {"path": "examples/walkthrough-smoke.py", "additions": 500, "deletions": 0}
    ]

    plan = build_review_plan(item)

    assert plan["applicability"]["code_change"] is False
    assert plan["applicability"]["docs_only"] is False
    assert plan["applicability"]["smoke_or_example_only"] is True
    assert plan["applicability"]["durable_smoke_value_required"] is True
    assert plan["applicability"]["duplication_scan_required"] is True
    assert plan["applicability"]["batch_pattern_scan_required"] is True
    assert "durable_smoke_value" in plan["required_evidence_ids"]
    assert set(plan["result_template"]["evidence"]) == set(
        plan["required_evidence_ids"]
    )


def test_runtime_plan_does_not_require_smoke_durability_gate() -> None:
    plan = build_review_plan(_item(areas={"product_runtime": 1}))

    assert plan["applicability"]["smoke_or_example_only"] is False
    assert plan["applicability"]["durable_smoke_value_required"] is False
    assert "durable_smoke_value" not in plan["required_evidence_ids"]


def test_test_only_plan_skips_runtime_lenses() -> None:
    plan = build_review_plan(_item(areas={"test_or_example": 2}))

    assert plan["applicability"]["code_change"] is False
    assert plan["applicability"]["typed_state_rule_required"] is False
    assert plan["applicability"]["behavior_change_disclosure_required"] is False
    assert plan["applicability"]["domain_neutrality_required"] is False
    assert plan["applicability"]["guidance_vs_obligation_required"] is False
    assert "typed_state_rule" not in plan["required_evidence_ids"]
    assert "domain_neutrality" not in plan["required_evidence_ids"]


@pytest.mark.parametrize(
    "area",
    [
        "product_runtime",
        "app_or_ui_surface",
        "ci_or_release",
        "build_or_config",
        "agent_instruction_surface",
        "public_entry_or_policy",
    ],
)
def test_behavior_review_requires_repository_reuse_even_with_green_checks(
    area: str,
) -> None:
    item = _item(areas={area: 1, "test_or_example": 1})
    # A narrow changed-file list and passing CI cannot establish that an
    # unchanged sibling already implements the same caller outcome.
    item["key_files"] = [{"path": "src/history_list.py", "additions": 80}]
    item["checks"] = {"counts": {"success": 4, "failure": 0}}
    plan = build_review_plan(item)
    assert plan["applicability"]["repository_reuse_required"] is True
    assert "repository_reuse" in plan["required_evidence_ids"]
    assert plan["result_template"]["evidence"]["repository_reuse"] == {
        "status": "unverified"
    }


@pytest.mark.parametrize("area", ["public_docs", "test_or_example"])
def test_non_behavior_review_keeps_existing_coverage_policy(area: str) -> None:
    plan = build_review_plan(_item(areas={area: 1}))
    assert plan["applicability"]["repository_reuse_required"] is False
    assert "repository_reuse" not in plan["required_evidence_ids"]


def test_code_review_requires_only_semantic_triage_before_contract_impact_is_known() -> None:
    plan = build_review_plan(_item(areas={"product_runtime": 1}))

    assert plan["applicability"]["semantic_alignment_required"] is True
    assert "semantic_alignment" in plan["required_evidence_ids"]
    assert "semantic_alignment_context" not in plan["applicability"]


@pytest.mark.parametrize("repo", ["huangruiteng/loopx", "example/service"])
def test_semantic_triage_does_not_inject_repository_facts_or_infer_from_preview(repo: str) -> None:
    item = _item(areas={"product_runtime": 1})
    item["repository"] = repo
    item["key_files"] = [{"path": f"src/file_{i}.py"} for i in range(10)]
    plan = build_review_plan(item)
    item["key_files"].append({"path": "loopx/semantics/vocabulary_v0.json"})
    assert build_review_plan(item) == plan
    semantic = next(
        row for row in build_agent_response_contract()["review_execution_contract"]["evidence_requirements"]
        if row["evidence_id"] == "semantic_alignment"
    )
    assert "Sign-off" not in json.dumps(semantic)
    assert "merge-gate" not in json.dumps(semantic)
    assert "loopx/semantics" not in json.dumps(semantic)


def test_generated_inventory_does_not_create_a_detailed_review_obligation() -> None:
    item = _item(areas={"public_docs": 1})
    item["key_files"] = [{"path": "loopx/semantics/inventory_v0.json"}]
    assert "semantic_alignment" not in build_review_plan(item)["required_evidence_ids"]


def test_reuse_evidence_compares_semantics_beyond_the_diff() -> None:
    contract = build_agent_response_contract()["review_execution_contract"]
    reuse = next(
        row
        for row in contract["evidence_requirements"]
        if row["evidence_id"] == "repository_reuse"
    )
    assert reuse["required_when"] == "behavior_bearing_change"
    assert reuse["verdict_values"] == [
        "reused",
        "separation_justified",
        "no_existing_candidate",
        "unjustified_duplication",
        "not_yet_proven",
    ]
    assert {
        "searched_revisions",
        "queries_and_paths",
        "existing_candidates",
        "semantic_comparison",
        "reuse_or_separation_reason",
        "validation_evidence",
        "verdict",
    } <= set(reuse["fields"])
    assert {
        "resource_and_caller",
        "data_scope_and_filters",
        "ordering_and_pagination",
        "authority_and_sanitization",
        "state_retry_and_failure_owner",
    } <= set(reuse["comparison_dimensions"])
    assert "unchanged" in reuse["rule"]
    assert "negative search" in reuse["rule"]
    assert "coexistence" in reuse["rule"]
    assert "not an automatic similarity detector" in reuse["rule"]
    assert "repository_reuse" in contract["verdict_policy"]["open_pr_unresolved_reuse"]


def test_reuse_requires_state_derivation_and_real_authoring_evidence() -> None:
    """A typed reader plus a generic JSON writer is not a product producer."""
    contract = build_agent_response_contract()["review_execution_contract"]
    reuse = next(
        row for row in contract["evidence_requirements"]
        if row["evidence_id"] == "repository_reuse"
    )
    assert "state_model_assessment" in reuse["fields"]
    assessment = reuse["state_model_assessment"]
    assert assessment["classification_values"] == [
        "authoritative_fact", "irreducible_intent", "derived_projection", "diagnostic_hint"
    ]
    assert {
        "field_or_relation", "classification", "existing_canonical_sources",
        "derivation_or_irreducibility_evidence", "producer_and_trigger",
        "authoring_discovery_path", "update_retire_and_replay_owner",
        "missing_stale_or_conflicting_value_behavior", "source_completeness",
        "counterfactual_validation", "decision",
    } <= set(assessment["item_fields"])
    assert "generic JSON" in assessment["rule"]
    assert "cannot infer" in assessment["rule"]
    assert "not_yet_proven" in assessment["rule"]
    # This extends the existing reuse gate, rather than creating an independent
    # automatic classifier that treats every user-authored intent as redundant.
    assert contract["completion_gate"]["blocking_evidence_verdicts"]["repository_reuse"] == [
        "unjustified_duplication", "not_yet_proven"
    ]


def test_public_cli_delivers_state_review_without_claiming_it_was_performed(capsys) -> None:
    fixture = Path(__file__).resolve().parents[2] / "examples/fixtures/pr-review.public.json"
    assert cli_main(["--format", "json", "pr-review", "--fixture", str(fixture)]) == 0
    packet = json.loads(capsys.readouterr().out)
    contract = packet["agent_response_contract"]["review_execution_contract"]
    requirements = {row["evidence_id"]: row for row in contract["evidence_requirements"]}
    assert requirements["repository_reuse"]["state_model_assessment"]["required_when"] == (
        "introduced_or_newly_enforced_state"
    )
    assert requirements["observable_semantics"]["state_projection_counterfactuals"]["cases"]
    # The shipped packet must support justified growth as well as compaction;
    # neither smaller output nor a passing revised ceiling proves correctness.
    budget_rule = requirements["semantic_alignment"]["rule"]
    for obligation in (
        "hard limits from regression budgets and presentation caps",
        "base/head measurements under the same workload and metric",
        "consumer value, true redundancy, compatibility cost and headroom",
        "Evidence-backed budget increases are valid",
        "deleting decision semantics",
        "Frozen experiment or promotion thresholds",
    ):
        assert obligation in budget_rule
    assert packet["pull_requests"]
    reviewed_code = False
    for item in packet["pull_requests"]:
        if not item["review_action_kind"]:
            assert item["review_plan"] is None
            assert item["review_template"] is None
            assert item["evidence_commands"] == []
            continue
        evidence = item["review_plan"]["result_template"]["evidence"]
        if "repository_reuse" in item["review_plan"]["required_evidence_ids"]:
            reviewed_code = True
            assert evidence["repository_reuse"] == {"status": "unverified"}
            assert evidence["observable_semantics"] == {"status": "unverified"}
            assert evidence["semantic_alignment"] == {"status": "unverified"}
    assert reviewed_code


def test_semantic_review_requires_compaction_and_annotation_counterfactuals() -> None:
    contract = build_agent_response_contract()["review_execution_contract"]
    parity = next(
        row for row in contract["evidence_requirements"]
        if row["evidence_id"] == "observable_semantics"
    )
    assert "state_projection_counterfactuals" in parity["fields"]
    cases = parity["state_projection_counterfactuals"]
    assert cases["required_when"] == "state_or_projection_drives_behavior"
    assert {
        "same_canonical_state_without_redundant_annotation",
        "unrelated_items_beyond_display_limit",
        "equivalent_pagination_or_display_order",
        "completed_superseded_or_archived_reference",
        "incomplete_source_is_not_proven_absence",
    } <= set(cases["cases"])
    assert "independent invariant" in cases["rule"]
    assert "user intent" in cases["rule"]
    assert "real public caller" in cases["rule"]


@pytest.mark.parametrize(
    "area",
    [
        "product_runtime",
        "app_or_ui_surface",
        "ci_or_release",
        "build_or_config",
        "agent_instruction_surface",
        "public_entry_or_policy",
    ],
)
def test_observable_parity_is_required_without_refactor_title_detection(
    area: str,
) -> None:
    item = _item(areas={area: 1})
    item["title"] = "Extract shared decision helper"
    item["checks"] = {"counts": {"success": 51, "failure": 0}}
    plan = build_review_plan(item)
    assert plan["applicability"]["observable_semantics_required"] is True
    assert plan["result_template"]["evidence"]["observable_semantics"] == {
        "status": "unverified"
    }


@pytest.mark.parametrize("area", ["public_docs", "test_or_example"])
def test_non_behavior_changes_do_not_invent_parity_execution(area: str) -> None:
    plan = build_review_plan(_item(areas={area: 1}))
    assert plan["applicability"]["observable_semantics_required"] is False
    assert "observable_semantics" not in plan["required_evidence_ids"]


def test_observable_semantics_covers_diagnostics_and_claim_neutral_note_paths() -> None:
    contract = build_agent_response_contract()["review_execution_contract"]
    parity = next(
        row
        for row in contract["evidence_requirements"]
        if row["evidence_id"] == "observable_semantics"
    )
    assert parity["required_when"] == "behavior_bearing_change"
    assert {
        "baseline_revision",
        "reviewed_head",
        "caller_branch_inventory",
        "comparison_rows",
        "execution_receipts",
        "normalization_rules",
        "intentional_deltas",
        "regression_sensitivity",
        "unverified_dimensions",
        "verdict",
    } <= set(parity["fields"])
    assert {
        "accepted_inputs_and_defaults",
        "eligibility_and_rejection_precedence",
        "full_diagnostics_and_remediation",
        "argument_to_persistence_readback",
        "state_receipts_and_no_effects",
        "replay_and_concurrent_updates",
    } <= set(parity["comparison_dimensions"])
    assert {
        "input_and_pre_state",
        "entrypoint_and_backend",
        "baseline_observation",
        "head_observation",
        "expected_invariant_source",
        "validation_evidence",
    } <= set(parity["row_fields"])
    assert {
        "revision",
        "command",
        "public_entrypoint",
        "backend",
        "fixture_fingerprint",
        "exit_status",
        "observation_fingerprint",
        "public_safe_artifact_reference_or_inline_observation",
    } <= set(parity["execution_receipt_fields"])
    assert {
        "invariant",
        "historical_defect_or_deliberate_mutation",
        "command",
        "expected_failure",
        "observed_failure",
        "passing_head_receipt",
    } <= set(parity["regression_sensitivity_fields"])
    assert parity["verdict_values"] == [
        "equivalent",
        "intentional_change_validated",
        "unintended_drift",
        "not_yet_proven",
    ]
    assert "reviewer-executed" in parity["rule"]
    assert "replayable command" in parity["rule"]
    assert "real affected backend" in parity["rule"]
    assert "independent oracle fail" in parity["rule"]
    assert (
        "observable_semantics"
        in contract["verdict_policy"]["open_pr_unresolved_semantics"]
    )


def test_pr_review_skill_tracks_the_capability_policy_revision() -> None:
    """The host skill must not pin a policy revision the capability owns.

    A hardcoded number here goes stale on the next capability bump and blocks
    reviewers with an unrelated mismatch: the skill text said ``== 6`` while
    the installed contract already emitted 7. The skill has to compare the
    packet's ``review_execution_contract.policy_revision`` with the result's
    ``review_policy_revision`` instead of naming a value itself.
    """

    skill = (
        Path(__file__).resolve().parents[2] / "skills/loopx-pr-review/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "review_execution_contract.policy_revision" in skill
    assert "review_policy_revision" in skill
    assert re.search(r"policy_revision\s*==\s*\d+", skill) is None, skill


def test_motivation_publishes_existing_problem_context_before_implementation():
    contract = build_review_execution_contract()
    context = next(row for row in contract["evidence_requirements"]
                   if row["evidence_id"] == "problem_context")
    publication = context["publication"]
    assert publication["section"] == "动机"
    assert publication["fields"] == ["affected_caller_or_operator", "before_after_scenario",
                                      "observable_outcome", "non_goals"]
    assert set(publication["fields"] + publication["increment_fields"]) <= (
        set(context["fields"]) | set(context["fields_by_verdict"]["justified_increment"]))
    assert "not truth or comprehension" in publication["rule"]
