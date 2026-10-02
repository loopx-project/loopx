from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .review_body import REQUIRED_FINAL_SECTIONS, review_body_requirements
from .approval_closeout import approval_closeout_contract

# Increment when review requirements change without changing the packet shape.
REVIEW_POLICY_REVISION = 14

# A red check is an observation, not evidence that the reviewed PR caused it.
# This contract belongs to review judgment; merge readiness still owns whether
# an unresolved required check permits integration.
VALIDATION_FAILURE_ATTRIBUTION = {
    "dispositions": [
        "pr_regression", "pre_existing_unrelated", "external_unrelated", "unresolved",
    ],
    "non_blocking_dispositions": ["pre_existing_unrelated", "external_unrelated"],
    "common_fields": ["disposition", "causal_scope_analysis", "affected_invariant_evidence"],
    "pre_existing_fields": [
        "base_revision", "head_revision", "same_command",
        "baseline_observation", "head_observation",
        "baseline_failure_signature", "head_failure_signature",
    ],
    "external_fields": ["independent_evidence", "retry_or_recovery_owner"],
    "rule": (
        "Classify every required failed or skipped validation before choosing a review verdict. "
        "A pre-existing failure is non-blocking for review only when the same check on an "
        "immutable base and exact head has the same normalized failing identity and detail, "
        "the PR does not alter that failure's causal path, and the changed invariant has "
        "independent passing evidence. Equal aggregate counts alone are insufficient. "
        "An external failure needs independent outage or infrastructure evidence, a recovery "
        "owner, and separate coverage of the changed invariant. Otherwise classify it as "
        "pr_regression or unresolved and request changes. Report unrelated red checks and "
        "their recovery separately from the PR verdict: APPROVE may be correct while merge "
        "readiness remains on hold. Never relax a hard limit or required check to make it green."
    ),
}

OUTCOME_IMPACT_ASSESSMENT = {
    "dimensions": ["long_horizon", "user_experience"],
    "decision_values": ["preserved", "improved", "accepted_tradeoff", "regression", "not_yet_proven", "not_applicable"],
    "fields": ["decision", "reason", "inspected_path"],
    "applicable_fields": ["before_after", "evidence_refs"],
    "blocking_decisions": ["regression", "not_yet_proven"],
    "rule": (
        "Judge whether the whole PR preserves sustained useful work and the user's ability to reach "
        "the intended outcome, even when its local feature works. For long_horizon, follow the "
        "affected entrypoint through action, durable result and later continuation: repeated turns, "
        "retry/restart, accumulated state, scheduling fairness or dependency return as applicable. "
        "Look for starvation, endless replan/retry, lost commitments, duplicated effects and growing "
        "cost without progress. A successful single call or blocker receipt is insufficient. "
        "For user_experience, compare the real affected CLI, UI or messaging journey: setup and "
        "repeated intervention, truthful state/readback, actionable failure, correction/cancel and "
        "recovery. Inspect existing companion surfaces; a backend success is not a usable journey. "
        "For an affected user journey, establish the ordinary task and expected result before "
        "reading the implementation path. In existing before_after and walkthrough evidence, "
        "compare required navigation, re-entry of known information, clarification, confirmation "
        "and recovery steps with the shortest valid path through the existing authority boundary. "
        "Explain what new information or authority each mandatory step supplies; a form that "
        "works can still be redundant. Optional editing is not a mandatory decision. Do not "
        "remove necessary scoped consent or correctness checks to minimize clicks. "
        "For visible changes inspect the rendered whole viewport with representative populated "
        "data, not only component presence: target/owner, hierarchy, truthful freshness/order "
        "and the next action must be understandable together. Follow one risk-relevant "
        "correction, cancel, reload or unavailable path through state readback. Identify source "
        "revision, built/installed surface and any mocked boundary in existing evidence refs. "
        "A fixture that supplies fresh sorted data cannot prove production freshness or sorting; "
        "a screenshot cannot prove persistence or adoption. Reuse unchanged evidence with an "
        "invalidation check; do not demand every viewport/state or paid model calls per PR. "
        "Reuse concrete walkthrough and validation references; select bounded cases by changed "
        "risk rather than requiring a long soak or every surface for every PR. Derive expected "
        "outcomes from the accepted product contract, not from the patch. A deliberate safety, "
        "budget or external-dependency wait is valid when its owner, release condition and resume "
        "or terminal route are explicit; do not remove safeguards merely to keep running. "
        "accepted_tradeoff requires an independent acceptance_basis and bounded_cost_and_recovery; "
        "author intent alone cannot justify hidden friction or waive authority. For regression or "
        "not_yet_proven name minimum_repair. For not_applicable identify the inspected path and "
        "why it cannot materially affect this dimension. These declarations do not prove truth."
    ),
}

# One bounded replacement for the former free-text compatibility justification.
COMPATIBILITY_ASSESSMENT = {
    "decision_values": ["not_applicable", "retain", "simplify_now", "follow_up", "not_yet_proven"],
    "fields": ["decision", "reason"],
    "applicable_fields": [
        "consumer_inventory", "deployment_boundary", "persisted_contract",
        "simpler_alternative", "validation_evidence",
    ],
    "deployment_boundary_values": ["co_deployed", "independent", "persisted_only", "mixed", "unknown"],
    "blocking_decisions": ["simplify_now", "not_yet_proven"],
    "rule": (
        "Assess compatibility added, retained or removed at the touched boundary. "
        "Name real callers/readers with source references and their upgrade boundary; "
        "separate transient request decoding from persisted data/receipt/replay contracts. "
        "Old receipts do not by themselves require an old request decoder; co-deployment "
        "does not permit dropping persisted formats. Compare consolidation into one current "
        "contract with explicit semantic variants against parallel version branches. "
        "Check implicit null/absent/version-selected modes for a named typed alternative. "
        "Re-review this tradeoff even after the previous correctness bug is fixed. "
        "Retain compatibility for evidenced independent consumers or persisted obligations, "
        "with its retirement/migration condition in reason. If a materially cheaper equivalent "
        "design is required in this PR, choose simplify_now and request that bounded repair; "
        "for a non-blocking improvement choose follow_up and name the concrete suggestion "
        "and why deferral is safe. Do not require a new tracking task or delete versions "
        "merely to reduce their count. Use not_yet_proven for a material unknown and "
        "not_applicable plus a scoped reason when this boundary has no compatibility change. "
        "Reuse walkthrough and validation references, including historical readback and "
        "mixed-version cases where applicable; the checker validates declarations, not truth."
    ),
}

SCOPE_COVERAGE_ASSESSMENT = {
    "decision_values": ["not_applicable", "verified", "overbroad", "not_yet_proven"],
    "fields": ["decision", "reason"],
    "applicable_fields": ["authorized_scope", "scope_source", "enforcement_selector", "recovery_owner", "cases"],
    "case_ids": ["covered_subject", "uncovered_same_container", "new_subject_after_activation",
                 "scope_escape_attempt", "recovery_to_progress"],
    "case_fields": ["case_id", "status", "input_and_authority", "expected_outcome",
                    "observed_outcome", "entrypoint_and_evidence"],
    "case_statuses": ["passed", "failed", "unverified", "not_applicable"],
    "rule": (
        "For an added, widened or retained gate on the touched caller path, separate authorization "
        "to enable it, the subjects it covers, and whether each covered subject is ready/bound. "
        "Goal/project activation alone does not prove authority over every current or future item. "
        "Establish scope from owner intent or the accepted contract, never from the selector being reviewed. "
        "Run covered, uncovered-in-the-same-container and newly-created-subject counterfactuals through "
        "the real entrypoint; also test that mutable fields cannot let covered work escape. "
        "For explicitly authorized global scope, assert that future subjects are intentionally covered. "
        "Trace refusal through its authorized recovery owner to renewed useful work; a blocker receipt, "
        "replan ACK or retry recommendation alone is not recovery. Share existing walkthrough/validation "
        "references. Each inapplicable case needs a scoped reason. If the boundary contains no gate or "
        "coverage decision, use not_applicable plus the inspected path. Retest the opposite failure "
        "direction after a bypass or overblocking fix; fixing the latest finding is not whole-PR proof."
    ),
}

# A published review speaks for an agent or a person, and a reader from another
# operator has no other way to weigh it. This is provenance carried by the
# result itself, not review evidence: no reviewer can "verify" its own identity,
# so a gap here makes the result unpublishable rather than blocking the author.
REVIEWER_DECLARATION = {
    "actor_kinds": ["model_agent", "human_operator"],
    "declaration_sources": ["runtime_reported", "self_reported"],
    "fields": ["actor_kind", "declaration_source"],
    "model_agent_fields": ["declared_model", "declared_provider"],
    "body_marker": "Reviewer:",
    "rule": (
        "Every result names who wrote it. actor_kind is model_agent or human_operator. "
        "A model_agent gives declared_model and declared_provider in ordinary "
        "product-family wording, and the published body repeats actor_kind, model and "
        "provider on exactly one visible `Reviewer:` line, so a reader on another host "
        "or organization can tell a model wrote the review and weigh it without private "
        "context. declaration_source records whether the host runtime reported the "
        "identity or the reviewer stated it; a reviewer that cannot observe its exact "
        "build names the family it knows instead of inventing a version. The declaration "
        "is provenance, not a credential: it authenticates nothing, authorizes no private "
        "access and gives the verdict no extra weight. Never put an endpoint, gateway, "
        "account, credential or router-qualified identifier in it."
    ),
}

# Reviewers from different operators share no context or memory; the accepted
# specification is the one reference both sides can open independently. Map the
# head onto it criterion by criterion instead of onto the author's narrative.
SPEC_BASIS_ASSESSMENT = {
    "decision_values": ["mapped", "no_spec", "not_yet_proven"],
    "blocking_decisions": ["not_yet_proven"],
    "fields": ["decision", "spec_source", "reason"],
    "spec_source_values": [
        "accepted_rfc",
        "accepted_contract_doc",
        "linked_issue_or_task",
        "review_thread",
        "none",
    ],
    "mapped_fields": ["spec_ref", "spec_revision", "criteria"],
    "published_text_fields": ["spec_ref", "spec_revision"],
    # The repository's own documents are pinned by a full commit id; a branch or
    # tag moves, so it cannot name the text the review judged against.
    "commit_pinned_spec_sources": ["accepted_rfc", "accepted_contract_doc"],
    "criterion_fields": ["criterion_id", "requirement", "disposition"],
    "disposition_fields": {
        "implemented": ["symbol_or_path", "validation_ref"],
        "deferred": ["reason", "successor_or_gap"],
        "out_of_scope": ["reason"],
        "not_met": ["observed_gap", "minimum_repair"],
    },
    "blocking_dispositions": ["not_met"],
    "rule": (
        "Read the target repository's own accepted specification for the touched surface "
        "before the implementation: an accepted RFC, an accepted contract or protocol "
        "document, the linked issue or task, then a maintainer-agreed frame in the review "
        "thread. For mapped, give spec_ref as a public path or link and spec_revision as "
        "the immutable revision judged against, so a reviewer from another operator opens "
        "the same text, and one criteria row per material acceptance criterion with the "
        "specification's own identifier when it has one, the requirement as written and "
        "its disposition at this exact head. implemented names a real symbol or path and "
        "the validation that exercises it; deferred names the reason and the successor or "
        "remaining gap; out_of_scope cites the specification or accepted task boundary, "
        "not author preference; not_met names the observed gap and minimum repair and "
        "blocks approval. Publish spec_ref, spec_revision and every criterion_id in the "
        "review body: the body is the only part another operator reads, and a path alone "
        "moves with the branch while the revision pins the text the review judged against. "
        "Give spec_ref, spec_revision and criterion_id as non-empty strings: each is "
        "published and matched against the body as a whole token, so EX-1 inside EX-10 "
        "or C1 inside a commit id is not publication. For accepted_rfc and "
        "accepted_contract_doc, spec_revision is the full commit id; a branch or tag "
        "name moves and cannot pin the text. "
        "Derive requirements from the "
        "specification, never from the patch. When the change edits the specification it "
        "cites, judge the criteria as accepted before this change and treat the edit as a "
        "finding to justify; a specification rewritten to match its implementation is not "
        "an independent reference. Do not invent criteria, promote a future or aspirational "
        "property to a current obligation, or impose this repository's roadmap on another "
        "repository. With no written specification use no_spec, spec_source none and a "
        "reason; problem_context remains the delivery judgment. An unread or unavailable "
        "specification is not_yet_proven."
    ),
}

CODE_AREAS = {
    "product_runtime",
    "app_or_ui_surface",
    "ci_or_release",
    "build_or_config",
}

EXAMPLE_OR_SMOKE_AREAS = {"test_or_example"}

BEHAVIORAL_POLICY_AREAS = {"public_entry_or_policy", "agent_instruction_surface"}

NEGATIVE_PATH_AREAS = CODE_AREAS | BEHAVIORAL_POLICY_AREAS

SEMANTIC_CANDIDATE_DECISIONS = (
    "reuse_existing",
    "extend_vocabulary",
    "create_vocabulary",
    "local_only",
    "external_input",
    "compatibility_only",
    "unknown",
)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def _line_churn(item: Mapping[str, Any]) -> int:
    try:
        return int(item.get("additions") or 0) + int(item.get("deletions") or 0)
    except (TypeError, ValueError):
        return 0


def _review_order(
    key_files: Sequence[Mapping[str, Any]], *, limit: int = 5
) -> list[str]:
    ranked = sorted(key_files, key=_line_churn, reverse=True)
    return [str(item.get("path") or "") for item in ranked[:limit] if item.get("path")]


def _section(label: str, minimum: int, instruction: str) -> dict[str, Any]:
    return {
        "label": label,
        "word_hint": f"至少 {minimum} 个正文字符；不含标题、链接地址、代码和重复行",
        "minimum_prose_characters": minimum,
        "content": "",
        "agent_instruction": instruction,
    }


def build_review_template(item: Mapping[str, Any]) -> dict[str, Any]:
    floors = review_body_requirements(behavior_bearing=bool(
        set(_as_mapping(item.get("areas"))) & (CODE_AREAS | BEHAVIORAL_POLICY_AREAS)))
    key_files = [
        candidate
        for candidate in _as_sequence(item.get("key_files"))
        if isinstance(candidate, Mapping)
    ]
    return {
        "schema_version": "pr_review_five_block_template_v0",
        "purpose": "Empty scaffold only; fill it from the verified review evidence, not PR metadata.",
        "sections": [
            _section(
                "动机",
                floors["动机"],
                "Use `problem_context`: verified goal basis, old behavior, before/after outcome and delivery verdict. Cite `spec_basis.spec_ref` and map each criterion_id to implemented/deferred/out_of_scope/not_met at this head, or state that no written specification exists. Explain outcome_impact on sustained progress and the user journey, including accepted tradeoffs or scoped inapplicability. Distinguish completing the scoped goal from a justified increment; explain why this is a complete useful slice, not just why the code works.",
            ),
            _section(
                "改动思路",
                floors["改动思路"],
                "Use `architecture_flow`, `repository_reuse`, and `walkthroughs`: entry point, authoritative state, decision boundary, positive path, existing implementation comparison, and ownership trade-off. For introduced or newly enforced state, explain derivation versus irreducible intent and the real producer/trigger, not just its serializer.",
            ),
            _section(
                "具体改动",
                floors["具体改动"],
                "Use `changed_line_classification` and `symbol_map`. Code changes require `### 关键代码讲解` for 2-5 behavior-bearing exact-head symbols; docs-only changes use `### 关键内容讲解`.",
            ),
            _section(
                "对主干的风险",
                floors["对主干的风险"],
                "Use `failure_analysis`, `walkthroughs.negative`, and `validation_matrix`; trace each finding from triggering state to observed outcome and minimum repair. When `scope_fit` applies, name the active production caller or explicitly record a coverage-only boundary. When `change_proportionality` applies, compare verified problem impact with mechanism and maintenance cost; a resolved implementation blocker does not justify approval when the full exact-head scope remains disproportionate. For opt-in changes, prove disabled-path parity through `default_off_isolation`; do not infer isolation from an absent feature object. Use `authority_semantics` to verify that public protocol names do not claim a broader actor lifecycle or authority model than the implementation provides. For a `semantic_alignment` contract impact or finding, include a concise `### 语义与 CI 对齐` subsection; ordinary `not_applicable` triage needs no separate subsection. For a blocker, name the current obligation, triggering change, observed evidence, minimum repair and rerun command. Surface typed-state-rule, domain-neutrality, behavior-change-disclosure, and guidance-vs-obligation findings when their evidence applies.",
            ),
            _section(
                "我的整体评价",
                floors["我的整体评价"],
                "State the `problem_context.outcome_impact` decisions for long_horizon and user_experience, including material tradeoffs and unresolved evidence. Use `observable_semantics` to report baseline/head comparisons and remaining compatibility gaps; equal decision codes are insufficient. Use `code_volume` (including its compatibility assessment and bounded simplification decision), `change_proportionality`, `default_off_isolation`, `authority_semantics`, validation results, residual risk, and exact-head freshness to state the verdict and the evidence needed for re-review. For semantic or constraint-related changes, state whether the PR reuses an existing vocabulary, extends one, creates one, stays local, or remains unknown, and link any required registry/RFC/CI repair.",
            ),
        ],
        "review_order": _review_order(key_files),
        "output_hint": (
            "Render the verified structured result using the five sections. "
            "Open the body with one `Reviewer:` line (reviewer_declaration.body_marker) "
            "carrying result.reviewer's actor_kind, model and provider. "
            "The capability-owned review_execution_contract is the evidence and completeness authority. Save the exact final Markdown in result.review_body before check-result; publish that checked body and read it back. Section floors reject empty shells, not certify reasoning. Explain concrete paths and counterexamples; do not pad or duplicate evidence to meet a floor."
        ),
    }


def build_review_execution_contract(*, wait_for_ci: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "pull_request_review_execution_contract_v2",
        "policy_revision": REVIEW_POLICY_REVISION,
        "approval_closeout": approval_closeout_contract(),
        "purpose": (
            "Define the evidence that must exist before a detailed review verdict; "
            "host skills route this contract but must not reimplement it."
        ),
        "evidence_status_values": ["verified", "unverified", "not_applicable"],
        # Provenance and spec binding travel with every result, so a host skill
        # routes them from here instead of inventing its own wording.
        "reviewer_declaration": deepcopy(REVIEWER_DECLARATION),
        "spec_basis_assessment": deepcopy(SPEC_BASIS_ASSESSMENT),
        "decision_procedure": {
            "order": [
                "establish_goal",
                "challenge_design",
                "falsify_claims",
                "inspect_implementation",
                "reconcile_verdict",
            ],
            "establish_goal": (
                "Resolve the current requested outcome from the user request, issue/task, "
                "accepted contract or demonstrated regression. Check changed direction "
                "and existing related work before accepting the author's narrowed frame. "
                "Use problem_context for one delivery judgment, referencing existing "
                "walkthrough/validation evidence rather than another report. A roadmap "
                "id is optional; never impose this repository's roadmap on another repo "
                "or copy private goals into public review."
            ),
            "challenge_design": (
                "Before explaining how the patch works, make the strongest evidence-backed "
                "case for not shipping it. Compare doing nothing, a smaller fix in the existing "
                "owner, and the proposed design against sustained useful work and the user journey. "
                "A locally correct feature can still strand later work or impose unjustified user "
                "intervention; assess both dimensions in problem_context.outcome_impact against the "
                "accepted product contract. Read the target repository's architecture "
                "and contribution rules: identify canonical state, decision/effect owner, "
                "and capability/provider placement. A new CLI calling a new helper proves "
                "reachability, not demand or correct ownership. Prefer derived state over "
                "manual synchronization and deletion/relocation over a second authority. "
                "Use code_volume.compatibility_assessment to challenge assumed compatibility "
                "needs before accepting additional protocol branches. Simplification includes "
                "deletion and consolidation, not only helper extraction. Do not impose "
                "LoopX-specific architecture on other repositories."
            ),
            "falsify_claims": (
                "Choose the strongest material promise, not the easiest failing input. "
                "Ask what could still be false when the author's tests pass, then probe "
                "that counterexample through the owning real boundary. Parent exit does "
                "not prove descendants drained; receipt/hash existence does not prove "
                "authentic execution; feature-on success does not prove baseline parity. A gate may prevent bypass and still wrongly capture independent work: prove its enabled-but-out-of-scope and future-subject behavior as well. A recorded blocker is not restored progress. "
                "If a mock supplies the very postcondition under review, it is not proof. "
                "Inspect related open/merged changes sharing the contract, not only files "
                "that conflict textually. Bound the search to shared callers/owners; do "
                "not require an unrelated whole-queue audit."
            ),
            "inspect_implementation": (
                "Run the applicable evidence plan for the whole base-to-head PR. On every "
                "re-review, separately record last-review-to-head fixes and the current "
                "whole-PR judgment. Reuse prior evidence only with its source revision, "
                "still-valid assumptions, and an explicit invalidation check for changed "
                "base, callers, platform, dependencies, or claims. Never inherit APPROVE."
            ),
            "reconcile_verdict": (
                "Approve only when positive value, architecture fit, and applicable "
                "evidence are established. A long_horizon or user_experience regression blocks approval "
                "even when the requested local feature is delivered and CI passes. No reproduced bug is not proof of a good design. "
                "Unresolved material evidence means hold/request changes with the exact "
                "missing observation, not an invented defect. Reject a mechanism when a "
                "smaller boundary solves the demonstrated problem; do not keep adding "
                "machinery to satisfy each review round. Conversely, approve justified "
                "cohesive changes: no rejection quota, line-count cutoff, author/model "
                "reputation rule, compulsory TS rewrite, or speculative edge-case veto."
            ),
        },
        "evidence_requirements": [
            {
                "evidence_id": "problem_context",
                "outcome_impact": OUTCOME_IMPACT_ASSESSMENT,
                "required_when": "always",
                "verdict_values": [
                    "goal_achieved",
                    "justified_increment",
                    "off_goal",
                    "fragmented",
                    "not_yet_proven",
                ],
                "fields": [
                    "goal_basis",
                    "author_claim",
                    "old_behavior",
                    "affected_caller_or_operator",
                    "compounding_cost",
                    "before_after_scenario",
                    "smaller_fix_analysis",
                    "observable_outcome",
                    "outcome_impact",
                    "spec_basis",
                    "non_goals",
                ],
                "fields_by_verdict": {
                    "justified_increment": [
                        "remaining_gap", "next_step", "boundary_reason",
                    ],
                    "off_goal": ["reason", "minimum_repair"],
                    "fragmented": ["reason", "minimum_repair"],
                    "not_yet_proven": ["reason", "minimum_repair"],
                },
                "rule": (
                    "Judge the delta against the verified requested outcome, not file/PR/test "
                    "counts or the author's completion label. goal_achieved closes the named "
                    "task's acceptance, not an unimplemented parent roadmap. justified_increment "
                    "requires a real useful delta, the remaining gap, an existing or concrete "
                    "scoped successor with its owner/dependency, and why this boundary is "
                    "independently reviewable, testable and reversible. Reuse observable_outcome, "
                    "walkthroughs and validation_matrix; do not duplicate their evidence. "
                    "Valid prerequisites, characterization, research findings, documentation "
                    "and maintenance can qualify without shipping an entire feature or "
                    "inventing follow-up work for a completed task. Fragmentation means an "
                    "avoidable stop before the accepted slice's useful outcome, not a small "
                    "diff. Where applicable, follow dependencies, peer handoff, artifact "
                    "acceptance and result return through the actual user entrypoints. "
                    "Missing evidence is not_yet_proven; name the minimum repair. "
                    "These are reviewer judgments, not automatic semantic detection, Goal "
                    "settlement, a mandatory roadmap schema or a minimum batch-size policy."
                ),
            },
            {
                "evidence_id": "architecture_flow",
                "required_when": "always",
                "fields": [
                    "entry_point",
                    "authoritative_input_or_state",
                    "decision_owner",
                    "side_effect_or_transition",
                    "receipt_or_consumer",
                    "failure_or_retry_owner",
                ],
            },
            {
                "evidence_id": "semantic_alignment",
                "required_when": "semantic_alignment_required",
                "verdict_values": [
                    "aligned",
                    "new_semantics_justified",
                    "not_applicable",
                    "advisory",
                    "not_yet_proven",
                    "violated",
                ],
                "fields": ["checked_scope", "impact_reason", "verdict"],
                "fields_by_verdict": {
                    "not_applicable": [],
                    "aligned": ["candidate_decision", "affected_contract", "evidence_refs"],
                    "new_semantics_justified": ["candidate_decision", "affected_contract", "evidence_refs"],
                    "advisory": ["candidate_decision", "analysis_limit"],
                    "not_yet_proven": [
                        "candidate_decision", "affected_contract", "trigger",
                        "observed_evidence", "minimum_repair", "validation_commands",
                    ],
                    "violated": [
                        "candidate_decision", "affected_contract", "trigger",
                        "observed_evidence", "minimum_repair", "validation_commands",
                    ],
                },
                "candidate_decisions": list(SEMANTIC_CANDIDATE_DECISIONS),
                "rule": (
                    "Start with bounded triage of the full diff and relevant definitions/callers. "
                    "Record checked_scope and impact_reason. If no shared contract is affected, "
                    "use not_applicable and stop; no candidate_decision or full RFC read is required. "
                    "File previews and unchanged registry paths do not prove absence of impact. "
                    "For shared values, owners, consumers, projections or persistence changes, "
                    "read only affected base/head contracts and reuse repository_reuse, "
                    "observable_semantics and validation_matrix evidence through evidence_refs. "
                    "Resolve CI obligations from the target repository's current policy; "
                    "observed check names alone do not establish which checks are required. "
                    "unknown due solely to bounded analysis is advisory: state analysis_limit "
                    "and why no affected current obligation lacks required evidence. It is not "
                    "proof of safety. Use not_yet_proven for missing required evidence on an "
                    "affected current contract, or violated for a concrete violation. Both block "
                    "approval and must name the contract, PR trigger, observed evidence, minimum "
                    "repair and rerun command. Advisory cannot override a required CI failure "
                    "or concrete blocking finding. Do not promote future/advisory RFC properties "
                    "to current obligations. For budget failures or changes, distinguish "
                    "hard limits from regression budgets and presentation caps. Reuse "
                    "validation_matrix and observable_semantics for base/head measurements under the same workload and metric; "
                    "assess consumer value, true redundancy, compatibility cost and headroom. "
                    "Evidence-backed budget increases are valid when the owning contract and "
                    "tests change together; compare them with compaction or retaining the limit. "
                    "Preserve the original failure and any remaining evidence gaps. Hard limits "
                    "still require their owner's authority. Frozen experiment or promotion thresholds "
                    "cannot be relaxed to relabel an existing result as passing. "
                    "Reject hiding a failure through unjustified budget increases, "
                    "deleting decision semantics, cosmetic renaming, narrowing the scan root or "
                    "comparison workload, or registering an unrelated value. "
                    "Docs-only reviews may supply this same row when contract impact is found."
                ),
            },
            {
                "evidence_id": "repository_reuse",
                "required_when": "behavior_bearing_change",
                "verdict_values": [
                    "reused",
                    "separation_justified",
                    "no_existing_candidate",
                    "unjustified_duplication",
                    "not_yet_proven",
                ],
                "fields": [
                    "searched_revisions",
                    "queries_and_paths",
                    "existing_candidates",
                    "semantic_comparison",
                    "reuse_or_separation_reason",
                    "state_model_assessment",
                    "rule_ownership",
                    "validation_evidence",
                    "verdict",
                ],
                "comparison_dimensions": [
                    "resource_and_caller",
                    "data_scope_and_filters",
                    "ordering_and_pagination",
                    "authority_and_sanitization",
                    "state_retry_and_failure_owner",
                ],
                "rule_ownership": {
                    "required_when": "retained_or_parallel_implementations",
                    "row_fields": [
                        "business_rule",
                        "baseline_owner",
                        "head_owner",
                        "retained_path_and_caller",
                        "retention_reason",
                        "deleted_rule_or_exit_condition",
                        "validation",
                    ],
                    "rule": (
                        "For migrations, fallback paths, dual providers or old/new entrypoints, "
                        "map each touched business rule across BOTH reachable paths, including "
                        "unchanged files. Separate storage parsing/writing and host effects from "
                        "eligibility, ordering, retention and successor derivation. A required "
                        "legacy writer does not justify a second decision owner. Ask where the "
                        "next policy change must be edited: tests comparing two new providers "
                        "do not prove the old path shares their rule. Name a bounded shared "
                        "owner and actual deletion where appropriate; justify deliberate "
                        "independent semantics instead of forcing abstraction. Record no "
                        "parallel path with caller-search evidence when not applicable. "
                        "Moved lines are not retired knowledge, and net-negative LOC is not "
                        "an acceptance quota. Missing analysis is not_yet_proven; an unjustified "
                        "duplicate rule is unjustified_duplication under repository_reuse."
                    ),
                },
                "state_model_assessment": {
                    "required_when": "introduced_or_newly_enforced_state",
                    "classification_values": [
                        "authoritative_fact",
                        "irreducible_intent",
                        "derived_projection",
                        "diagnostic_hint",
                    ],
                    "item_fields": [
                        "field_or_relation",
                        "classification",
                        "existing_canonical_sources",
                        "derivation_or_irreducibility_evidence",
                        "producer_and_trigger",
                        "authoring_discovery_path",
                        "update_retire_and_replay_owner",
                        "missing_stale_or_conflicting_value_behavior",
                        "source_completeness",
                        "counterfactual_validation",
                        "decision",
                    ],
                    "rule": (
                        "Before accepting each added or newly enforced declaration, flag, "
                        "relation or journal field, classify it and trace existing canonical "
                        "facts/relations that could derive the required outcome. Prefer a "
                        "projection over a second manually synchronized state. Identify the "
                        "real producer, triggering workflow, authoring discovery path and "
                        "update/retirement/replay owner. A generic JSON writer, schema "
                        "acceptance, reader call site or hand-filled fixture proves transport, "
                        "not that an ordinary agent/user will author the fact when needed. "
                        "Do not invent intent: a system cannot infer an alternative, consent "
                        "or commitment from unrelated records; genuinely irreducible intent "
                        "needs an explicit scoped authoring contract. Trace any diagnostic "
                        "slice back to its completeness boundary: absence from a bounded "
                        "view is not proof of absence in canonical state. Record no applicable "
                        "state with a reason, not a fabricated producer. Unjustified duplicate "
                        "state makes repository_reuse unjustified_duplication; missing "
                        "derivation, producer or completeness proof makes it not_yet_proven. "
                        "These are reviewer evidence, not automatic semantic detection."
                    ),
                },
                "rule": (
                    "Before judging the proposed architecture, search the base and "
                    "exact-head repository by caller outcome, resource, routes and "
                    "symbols, not only new filenames. Record immutable searched "
                    "revisions, queries, paths and candidate definitions/callers, "
                    "including unchanged siblings outside the diff. Compare the "
                    "applicable dimensions for each candidate and explain why the "
                    "nearest existing owner can be reused or extended, or why an "
                    "independent boundary is necessary. Same-resource views must "
                    "retain the same semantics unless a deliberate difference is "
                    "disclosed and validated; exercise switching consumers and "
                    "concurrent updates when state or paging is involved. Green CI, "
                    "shared low-level readers, different names, and conflict-free "
                    "coexistence do not justify a second contract or state owner. "
                    "A no_existing_candidate verdict requires a bounded negative "
                    "search with its scope and limitations, not an empty candidate "
                    "list. Similar code alone is not duplication: preserve distinct "
                    "invariants or compatibility boundaries when evidence justifies "
                    "separation. After base integration or head changes, repeat the "
                    "comparison rather than inheriting an earlier approval. Missing "
                    "or unverified evidence is not_yet_proven and cannot approve. "
                    "This is a reviewer-executed evidence requirement, not an "
                    "automatic similarity detector or proof that a search ran."
                ),
            },
            {
                "evidence_id": "observable_semantics",
                "scope_coverage": SCOPE_COVERAGE_ASSESSMENT,
                "required_when": "behavior_bearing_change",
                "verdict_values": [
                    "equivalent",
                    "intentional_change_validated",
                    "unintended_drift",
                    "not_yet_proven",
                ],
                "fields": [
                    "baseline_revision",
                    "reviewed_head",
                    "caller_branch_inventory",
                    "comparison_rows",
                    "execution_receipts",
                    "normalization_rules",
                    "intentional_deltas",
                    "regression_sensitivity",
                    "state_projection_counterfactuals",
                    "scope_coverage",
                    "unverified_dimensions",
                    "verdict",
                ],
                "state_projection_counterfactuals": {
                    "required_when": "state_or_projection_drives_behavior",
                    "cases": [
                        "same_canonical_state_without_redundant_annotation",
                        "unrelated_items_beyond_display_limit",
                        "equivalent_pagination_or_display_order",
                        "completed_superseded_or_archived_reference",
                        "incomplete_source_is_not_proven_absence",
                    ],
                    "rule": (
                        "Run applicable counterfactuals through the real public caller, "
                        "using comparison_rows and execution_receipts. Hold authoritative "
                        "facts fixed when removing redundant annotations or changing only "
                        "display order, pagination or unrelated-item count beyond a cap; "
                        "these must not change admission or obligations. Derive the oracle "
                        "from an independent invariant, not the new helper. Do not remove "
                        "genuine user intent or change semantic priority and demand parity. "
                        "For reference lifecycle changes assert the documented new outcome; "
                        "done is not automatically proof of goal acceptance. Explain cases "
                        "that do not apply; an untested applicable case is not_yet_proven."
                    ),
                },
                "comparison_dimensions": [
                    "accepted_inputs_and_defaults",
                    "eligibility_and_rejection_precedence",
                    "full_diagnostics_and_remediation",
                    "argument_to_persistence_readback",
                    "state_receipts_and_no_effects",
                    "replay_and_concurrent_updates",
                ],
                "row_fields": [
                    "input_and_pre_state",
                    "entrypoint_and_backend",
                    "baseline_observation",
                    "head_observation",
                    "expected_invariant_source",
                    "validation_evidence",
                ],
                "execution_receipt_fields": [
                    "revision",
                    "command",
                    "public_entrypoint",
                    "backend",
                    "fixture_fingerprint",
                    "exit_status",
                    "observation_fingerprint",
                    "public_safe_artifact_reference_or_inline_observation",
                ],
                "regression_sensitivity_fields": [
                    "invariant",
                    "historical_defect_or_deliberate_mutation",
                    "command",
                    "expected_failure",
                    "observed_failure",
                    "passing_head_receipt",
                ],
                "rule": (
                    "Inventory changed and bypassed caller branches from the immutable "
                    "pre-change baseline, not just the new helper or PR title. Compare "
                    "identical synthetic inputs through the real public entrypoint and "
                    "affected backend at baseline and exact head. Include legal paths "
                    "as well as rejection paths: stricter eligibility is not inherently "
                    "compatible. For migrations compare before/after promotion, not "
                    "only multiple providers sharing the new decision. Trace supplied, "
                    "omitted, empty and clear argument intent through dispatch, mutation, "
                    "persistence and a separate readback; a success payload or parser "
                    "acceptance does not prove a note was saved or ownership unchanged. "
                    "Compare exception/exit type, full diagnostic details and remediation, "
                    "not just reason codes, prefixes or 'both reject'. Exercise overlapping "
                    "invalid conditions to check rejection precedence and assert no "
                    "unintended state/receipt/ownership effects. Mark non-applicable "
                    "dimensions with reasons. Derive expected invariants from the public "
                    "contract and legacy callers, never from the replacement output; "
                    "baseline bugs require explicitly disclosed, justified changes rather "
                    "than blind byte parity. Show a regression failing on the old defect "
                    "or a deliberate semantic mutation (dropped argument/detail, stronger "
                    "precondition), then passing on the fix. Each baseline/head execution "
                    "receipt must record a replayable command or bounded script invocation, "
                    "the public entrypoint, real affected backend, immutable fixture "
                    "fingerprint, exit status, and normalized observation fingerprint. Use "
                    "the same harness and fixture at both revisions unless an intentional "
                    "delta explains the difference. Mutation evidence must execute that "
                    "public path and make an independent oracle fail before the fixed-head "
                    "receipt passes; prose, helper-only unit coverage, parser acceptance, or "
                    "provider conformance against only the new rule is insufficient. "
                    "Normalize only documented "
                    "nondeterminism, never away a semantic delta. Re-review the full "
                    "inventory after fixes, not only the last finding. Missing baseline "
                    "or real-path evidence is not_yet_proven, not equivalent. This is a "
                    "reviewer-executed evidence gate, not automated execution or proof "
                    "of equivalence from CI, metadata, or a completed review template."
                ),
            },
            {
                "evidence_id": "changed_line_classification",
                "required_when": "always",
                "categories": [
                    "production",
                    "tests_or_fixtures",
                    "docs",
                    "generated",
                    "mechanical_moves",
                ],
                "fields": ["files", "additions", "deletions", "behavior_role"],
                "rule": "Classify the exact base..head diff before judging code volume.",
            },
            {
                "evidence_id": "scope_fit",
                "required_when": "behavior_bearing_change",
                "fields": [
                    "shipped_behavior",
                    "active_call_sites",
                    "instruction_install_or_load_path",
                    "target_audience_and_scope",
                    "caller_path_or_none",
                    "coverage_only_declared",
                    "scope_fit_verdict",
                ],
                "rule": (
                    "For every added or moved production module, adapter, CLI "
                    "command, control-plane contract, or agent instruction surface, "
                    "verify a shipped behavior and at least one active production "
                    "call site or automatic installation/loading path in the reviewed "
                    "branch. For instructions, verify the audience and activation "
                    "scope reached by that path. A consumer or serializer alone does not "
                    "prove the producer/trigger in repository_reuse.state_model_assessment. "
                    "Test-only coverage of an unused "
                    "module or uninstalled instruction is not a shipped behavior; "
                    "name the gap and treat it as blocking unless an explicit, owner-accepted "
                    "coverage-only boundary is recorded."
                ),
            },
            {
                "evidence_id": "symbol_map",
                "required_when": "code_change",
                "items_field": "items",
                "item_count": {"minimum": 2, "maximum": 5},
                "item_fields": [
                    "path",
                    "line",
                    "symbol",
                    "responsibility",
                    "before_after_behavior",
                    "input_or_pre_state",
                    "critical_branch_or_invariant",
                    "callee_or_side_effect",
                    "return_or_consumer",
                    "failure_fallback_or_retry_owner",
                    "caller_evidence",
                ],
                "source_rule": (
                    "Use exact-head definitions plus unchanged surrounding callers; "
                    "select symbols by behavior, not diff size."
                ),
            },
            {
                "evidence_id": "walkthroughs",
                "required_when": "always",
                "positive_field": "positive",
                "negative_field": "negative",
                "positive_fields": [
                    "trigger",
                    "ordered_symbols_or_steps",
                    "state_transitions",
                    "observable_result",
                ],
                "negative_required_when": (
                    "runtime, selector, policy, authority, lifecycle, installer, bridge, "
                    "or public-entry contract changes"
                ),
                "negative_fields": [
                    "triggering_input_or_state",
                    "ordered_symbols_or_steps",
                    "rejection_fallback_or_wrong_outcome",
                    "error_or_retry_owner",
                ],
            },
            {
                "evidence_id": "validation_matrix",
                "ci_policy": "required" if wait_for_ci else "not_consulted",
                "wait_for_ci": wait_for_ci,
                "validation_source": (
                    "Repository-native local validation and final CI observation are required. "
                    "Attribute failed checks before judging the PR; an unrelated red check "
                    "may hold merging without requiring code changes on this PR."
                    if wait_for_ci else
                    "Repository-native local validation at the reviewed head. "
                    "Do not fetch, poll, or wait for GitHub CI. Missing, pending, "
                    "or failed remote CI is not a review evidence gap. Attribute local "
                    "failures against the base and changed invariant before judging the PR."
                ),
                "failure_attribution": VALIDATION_FAILURE_ATTRIBUTION,
                "required_when": "always",
                "items_field": "items",
                "item_fields": [
                    "case_id",
                    "invariant_or_case",
                    "command_or_check",
                    "status",
                    "result",
                    "required",
                    "skip_or_failure_reason",
                ],
                "required_cases": [
                    {
                        "case_id": "changed_invariant_positive",
                        "required_when": "always",
                    },
                    {
                        "case_id": "material_negative_or_failure",
                        "required_when": "negative_walkthrough_required",
                    },
                    {
                        "case_id": "repository_required_checks",
                        "required_when": "always",
                    },
                ],
            },
            {
                "evidence_id": "failure_analysis",
                "required_when": "always",
                "fields": [
                    "strongest_regression_scenario",
                    "triggering_state",
                    "permitting_or_preventing_code_path",
                    "blast_radius",
                    "observability",
                    "rollback_or_recovery",
                    "minimum_repair",
                    "regression_test",
                    "claim_under_test",
                    "counterexample_when_existing_tests_pass",
                    "real_boundary_and_mock_limits",
                    "related_contract_changes",
                ],
            },
            {
                "evidence_id": "code_volume",
                "required_when": "always",
                "verdict_values": ["necessary", "partly_avoidable", "not_yet_proven"],
                "fields": [
                    "changed_line_shape",
                    "largest_production_hotspots",
                    "active_call_site_evidence",
                    "compatibility_assessment",
                    "verdict",
                    "highest_value_simplification",
                    "behavior_preserving_validation",
                ],
                "compatibility_assessment": deepcopy(COMPATIBILITY_ASSESSMENT),
            },
            {
                "evidence_id": "change_proportionality",
                "required_when": "code_change",
                "verdict_values": [
                    "proportionate",
                    "disproportionate",
                    "not_yet_proven",
                ],
                "fields": [
                    "original_problem",
                    "frequency_or_severity",
                    "affected_users_or_operators",
                    "failure_cost_and_recovery",
                    "smallest_viable_fix",
                    "production_mechanism_cost",
                    "new_state_contract_or_cli_surface",
                    "maintenance_and_migration_cost",
                    "alternatives_considered",
                    "scope_growth_since_initial_review",
                    "repository_architecture_constraints",
                    "strongest_case_against_shipping",
                    "why_smaller_or_existing_owner_is_insufficient",
                    "verdict",
                ],
                "rule": (
                    "Judge the full exact-head change against the original "
                    "user-visible problem, not against how completely the proposed "
                    "mechanism is implemented. Compare verified frequency, severity, "
                    "blast radius, and recovery cost with production code, new state, "
                    "schema, CLI, caller, migration, and long-term maintenance surface. "
                    "Reuse code_volume.compatibility_assessment rather than equating historical "
                    "receipt recovery with a need to preserve every request version. "
                    "Correctness, green CI, and resolution of earlier review findings "
                    "are necessary but do not prove positive value. Treat "
                    "`disproportionate` and `not_yet_proven` as blocking; request the "
                    "smallest viable fix, deletion, split, or hold. On every materially "
                    "expanded re-review, reset this assessment from the original problem "
                    "instead of treating reviewer-requested additions as progress toward "
                    "approval."
                ),
            },
            {
                "evidence_id": "default_off_isolation",
                "required_when": "behavior_bearing_change",
                "verdict_values": [
                    "isolated",
                    "not_isolated",
                    "not_applicable",
                    "not_yet_proven",
                ],
                "fields": [
                    "opt_in_or_default_off_claim",
                    "authoritative_gate",
                    "declared_activation_scope",
                    "enabled_subjects",
                    "default_state",
                    "availability_signals_that_do_not_activate",
                    "disabled_entry_path",
                    "enabled_entry_path",
                    "shared_changed_surfaces",
                    "installed_or_auto_loaded_instruction_surfaces",
                    "disabled_schema_or_required_fields",
                    "disabled_prompt_or_guidance",
                    "disabled_accepted_inputs",
                    "disabled_state_or_projection",
                    "disabled_scheduling_quota_or_side_effects",
                    "disabled_user_experience_parity",
                    "paired_counterfactual_validation",
                    "verdict",
                ],
                "rule": (
                    "When a change is claimed to be opt-in, feature-gated, or "
                    "default-off, trace every changed production surface on both "
                    "sides of the authoritative gate. With the feature disabled, "
                    "preserve the previous observable contract: schema and required "
                    "fields, prompts and guidance, accepted host inputs, persisted "
                    "or journal projections, scheduling and quota decisions, and "
                    "external effects. Treat installed or automatically loaded "
                    "skills, system or agent instructions, prompt templates, help "
                    "text, schemas, and provider setup guidance as production "
                    "surfaces when they can change model or user behavior. A runtime "
                    "default of false does not isolate guidance that is already "
                    "present on one of those baseline surfaces. Distinguish "
                    "availability from activation: installation, discovery, provider "
                    "readiness, accepted input, locator resolution, or CLI presence "
                    "does not enable a capability. For goal-, project-, tenant-, "
                    "user-, or agent-scoped activation, prove that the authoritative "
                    "gate binds the intended scope and every required subject before "
                    "any capability-specific instruction, action, state mutation, or "
                    "user-facing concept is projected. The absence of a topology, operation list, "
                    "or feature object proves only that one enabled path did not run; "
                    "it does not prove isolation of shared builders or serializers. "
                    "Run a paired counterfactual using otherwise identical inputs "
                    "with the gate disabled and enabled, and compare the disabled "
                    "result with the pre-change contract. Use `not_applicable` only "
                    "after verifying that the PR makes no opt-in or default-off claim. "
                    "Treat `not_isolated` and `not_yet_proven` as blocking."
                ),
            },
            {
                "evidence_id": "authority_semantics",
                "required_when": "code_change",
                "verdict_values": [
                    "aligned",
                    "misleading",
                    "not_applicable",
                    "not_yet_proven",
                ],
                "fields": [
                    "public_protocol_ids_and_symbols",
                    "actual_actor_lifecycle",
                    "authority_granted",
                    "authority_explicitly_excluded",
                    "nearest_existing_domain_term",
                    "name_scope_match",
                    "compatibility_or_migration_cost",
                    "verdict",
                ],
                "rule": (
                    "Public schema ids, version names, payload fields, CLI options, "
                    "and exported symbols must not claim a broader actor lifecycle "
                    "or authority model than the implementation provides. Distinguish "
                    "an ephemeral sub-agent or child execution from a registered peer "
                    "with independent authority and from durable multi-agent "
                    "coordination. Prefer the nearest established domain term. If a "
                    "new v0 protocol has not shipped, rename it before compatibility "
                    "cost accumulates. Treat `misleading` and `not_yet_proven` as "
                    "blocking when public protocol naming is in scope; use "
                    "`not_applicable` only when the exact-head change adds or changes "
                    "no actor, authority, or coordination semantics."
                ),
            },
            {
                "evidence_id": "typed_state_rule",
                "required_when": "code_change",
                "fields": [
                    "state_classification_rule",
                    "matching_mechanism",
                    "typed_contract_or_enum",
                    "false_positive_or_negative_risk",
                    "migration_or_followup",
                ],
                "rule": (
                    "State-classification and delivery-semantics rules must be typed "
                    "enums, schemas, or transition helpers. Flag substring denylists, "
                    "scattered booleans, and prose-only assumptions as findings with "
                    "the concrete misclassification risk and the typed alternative."
                ),
            },
            {
                "evidence_id": "domain_neutrality",
                "required_when": "product_runtime_or_policy_contract",
                "fields": [
                    "obligation_or_error_text",
                    "domain_specific_language",
                    "affected_goal_types",
                    "neutral_alternative",
                ],
                "rule": (
                    "Generic control-plane obligations and error text must stay "
                    "domain-neutral. Flag product- or benchmark-specific wording in "
                    "core work-lane, quota, todo, or settlement contracts and propose "
                    "a goal-agnostic alternative."
                ),
            },
            {
                "evidence_id": "behavior_change_disclosure",
                "required_when": "behavior_bearing_change",
                "fields": [
                    "previous_default",
                    "new_default",
                    "affected_callers_or_lanes",
                    "disclosure_surface",
                ],
                "rule": (
                    "Default behavior changes in runtime or automatically loaded "
                    "instruction surfaces must be disclosed through renamed smokes, "
                    "docs, or release notes. Flag silent behavior changes that alter "
                    "existing "
                    "automation or ordinary agent behavior without naming the old and "
                    "new default."
                ),
            },
            {
                "evidence_id": "guidance_vs_obligation",
                "required_when": "work_lane_or_obligation_contract",
                "fields": [
                    "advisory_or_enforced",
                    "machine_flag",
                    "consequence_when_ignored",
                    "documented_semantics",
                ],
                "rule": (
                    "Advisory versus machine-enforced semantics must be explicit. "
                    "Flag text that calls a hard obligation 'guidance' when a "
                    "contract flag such as must_attempt_work forces a turn, or that "
                    "leaves the enforcement consequence undocumented."
                ),
            },
            {
                "evidence_id": "durable_smoke_value",
                "required_when": "smoke_or_example_only",
                "fields": [
                    "shipped_behavior_or_boundary_guarded",
                    "active_call_site_or_consumer",
                    "existing_coverage_scan",
                    "duplication_verdict",
                    "same_author_batch_scan",
                    "consolidation_or_thinning_recommendation",
                    "real_product_or_repo_value_verdict",
                    "repeat_offender_escalation",
                ],
                "rule": (
                    "For example, walkthrough, or smoke-only PRs, prove the added "
                    "artifact delivers real, durable value to the repository or "
                    "product: it guards shipped behavior or a real boundary, "
                    "improves maintainability, or reduces a concrete maintenance "
                    "cost. Running successfully, being deterministic, and being "
                    "public-safe are necessary but not sufficient. Name the "
                    "existing-coverage scan and reject one-off scaffolding, "
                    "same-shape batch farming, or duplication of existing smokes."
                    " Repeated same-author violations after a REQUEST_CHANGES "
                    "warning escalate to a contribution-restriction "
                    "recommendation: the owner blocks that account from further "
                    "PR submissions. The warning must name this consequence."
                ),
            },
        ],
        "finding_contract": {
            "findings_first": True,
            "required_fields": [
                "severity",
                "trigger",
                "code_path",
                "incorrect_or_risky_outcome",
                "location",
                "minimum_repair",
                "regression_test",
            ],
            "no_finding_rule": (
                "Say no blocking finding explicitly, then name residual risk and the "
                "strongest missing validation."
            ),
        },
        "completion_gate": {
            "metadata_only_verdict_allowed": False,
            "required_status": "Every applicable evidence item is verified or explicitly unverified with a reason.",
            "approval_requires": (
                "All required evidence verified with substantive supporting detail, no "
                "blocking evidence verdict, and no unresolved blocking finding. An "
                "unverified result may explain REQUEST_CHANGES but never APPROVE. "
                "Run pr-review --check-result RESULT --packet PACKET before publishing; "
                "this checks consistency, not evidence truth or remote freshness."
            ),
            "code_change_symbol_minimum": 2,
            "exact_head_recheck_required": True,
            "stale_head_verdict_allowed": False,
            "blocking_evidence_verdicts": {
                "problem_context": ["off_goal", "fragmented", "not_yet_proven"],
                "repository_reuse": ["unjustified_duplication", "not_yet_proven"],
                "observable_semantics": ["unintended_drift", "not_yet_proven"],
                "change_proportionality": [
                    "disproportionate",
                    "not_yet_proven",
                ],
                "default_off_isolation": [
                    "not_isolated",
                    "not_yet_proven",
                ],
                "authority_semantics": [
                    "misleading",
                    "not_yet_proven",
                ],
                "semantic_alignment": ["not_yet_proven", "violated"],
            },
            "required_final_sections": REQUIRED_FINAL_SECTIONS,
        },
        "verdict_policy": {
            "open_pr_blocking_finding": "REQUEST_CHANGES",
            "unrelated_validation_failure": (
                "APPROVE when a required red check is independently attributed to an unchanged "
                "pre-existing failure or external infrastructure, and the PR's changed "
                "invariant is covered. Record the separate merge-readiness hold; do not ask "
                "this PR to repair unrelated code or budgets. Unattributed, introduced, or "
                "worsened failures still block approval."
            ),
            "open_pr_unjustified_delivery": (
                "REQUEST_CHANGES when problem_context is off_goal, fragmented or "
                "not_yet_proven. Green checks cannot replace an evidenced goal delta; "
                "a justified bounded increment need not complete its parent goal."
            ),
            "open_pr_unresolved_semantics": (
                "REQUEST_CHANGES when observable_semantics is unintended_drift or "
                "not_yet_proven; equal decision codes, green suites, stricter checks "
                "and resolution of the previous finding cannot establish compatibility"
            ),
            "open_pr_unresolved_reuse": (
                "REQUEST_CHANGES when repository_reuse is unjustified_duplication "
                "or not_yet_proven, including missing/unverified search evidence; "
                "request reuse, an evidence-backed separation, or further investigation; "
                "this includes unproved state derivation, producer/trigger and "
                "projection completeness, even when the new reader is typed and tested"
            ),
            "open_pr_unresolved_proportionality": (
                "REQUEST_CHANGES when change_proportionality is disproportionate "
                "or not_yet_proven; correctness, green CI, and resolved earlier "
                "findings cannot override this gate"
            ),
            "open_pr_unresolved_semantic_alignment": (
                "REQUEST_CHANGES for semantic_alignment not_yet_proven or violated: "
                "name the affected current contract, PR trigger, observed evidence, "
                "minimum repair and rerun command. A bounded-analysis advisory alone "
                "does not block approval or override other required checks."
            ),
            "materially_expanded_rereview": (
                "Reset change_proportionality from the original problem and review "
                "the full exact head; do not inherit an approval trajectory from "
                "resolved prior findings"
            ),
            "open_pr_non_blocking_finding": "APPROVE",
            "open_pr_no_finding": "APPROVE",
            "author_owned_no_blocker_fallback": (
                "COMMENTED review titled 'Approval conclusion (author-owned PR; "
                "GitHub blocks formal self-approval)'"
            ),
            "author_owned_blocker_fallback": (
                "COMMENTED review titled 'Request changes conclusion (author-owned "
                "PR; GitHub blocks formal self-review)'"
            ),
            "merged_pr": "POST_MERGE_AUDIT_COMMENT when a new actionable finding exists",
            "publication_exceptions": [
                "explicit local-only request",
                "private or security-sensitive finding",
            ],
        },
    }


def build_review_plan(item: Mapping[str, Any]) -> dict[str, Any]:
    areas = {
        str(area) for area, count in _as_mapping(item.get("areas")).items() if count
    }
    code_change = bool(areas & CODE_AREAS)
    behavioral_policy_change = bool(areas & BEHAVIORAL_POLICY_AREAS)
    behavior_bearing_change = code_change or behavioral_policy_change
    docs_only = bool(areas) and areas <= {"public_docs", "public_entry_or_policy"}
    smoke_or_example_only = bool(areas & EXAMPLE_OR_SMOKE_AREAS) and not (
        code_change or behavioral_policy_change
    )
    semantic_alignment_required = behavior_bearing_change
    required_evidence = [
        "problem_context",
        "architecture_flow",
        "changed_line_classification",
        "walkthroughs",
        "validation_matrix",
        "failure_analysis",
        "code_volume",
    ]
    if code_change:
        required_evidence.insert(3, "symbol_map")
        required_evidence.append("scope_fit")
        required_evidence.append("change_proportionality")
        required_evidence.append("authority_semantics")
        required_evidence.append("typed_state_rule")
    if behavior_bearing_change:
        required_evidence.insert(3, "repository_reuse")
        required_evidence.append("observable_semantics")
        if "scope_fit" not in required_evidence:
            required_evidence.append("scope_fit")
        required_evidence.append("default_off_isolation")
        required_evidence.append("behavior_change_disclosure")
    if areas & {
        "product_runtime",
        "public_entry_or_policy",
        "agent_instruction_surface",
    }:
        required_evidence.append("domain_neutrality")
        required_evidence.append("guidance_vs_obligation")
    if smoke_or_example_only:
        required_evidence.append("durable_smoke_value")
    if semantic_alignment_required:
        required_evidence.append("semantic_alignment")
    number = item.get("number")
    head_oid = str(item.get("head_oid") or "").strip()
    target_key = f"{number}@{head_oid}" if number and head_oid else None
    return {
        "schema_version": "pull_request_review_plan_v1",
        "contract_ref": "agent_response_contract.review_execution_contract",
        "target": {
            "number": number,
            "base_ref": item.get("base_ref"),
            "head_oid": head_oid or None,
            "exact_head_key": target_key,
        },
        "applicability": {
            "areas": sorted(areas),
            "code_change": code_change,
            "behavioral_policy_change": behavioral_policy_change,
            "behavior_bearing_change": behavior_bearing_change,
            "docs_only": docs_only,
            "symbol_map_required": code_change,
            "scope_fit_required": behavior_bearing_change,
            "repository_reuse_required": behavior_bearing_change,
            "observable_semantics_required": behavior_bearing_change,
            "change_proportionality_required": code_change,
            "default_off_isolation_required": behavior_bearing_change,
            "authority_semantics_required": code_change,
            "negative_walkthrough_required": bool(areas & NEGATIVE_PATH_AREAS),
            "typed_state_rule_required": code_change,
            "behavior_change_disclosure_required": behavior_bearing_change,
            "domain_neutrality_required": bool(
                areas
                & {
                    "product_runtime",
                    "public_entry_or_policy",
                    "agent_instruction_surface",
                }
            ),
            "guidance_vs_obligation_required": bool(
                areas
                & {
                    "product_runtime",
                    "public_entry_or_policy",
                    "agent_instruction_surface",
                }
            ),
            "smoke_or_example_only": smoke_or_example_only,
            "durable_smoke_value_required": smoke_or_example_only,
            "duplication_scan_required": smoke_or_example_only,
            "batch_pattern_scan_required": smoke_or_example_only,
            "semantic_alignment_required": semantic_alignment_required,
        },
        "required_evidence_ids": required_evidence,
        "result_template": {
            "schema_version": "pull_request_review_result_v1",
            "review_policy_revision": REVIEW_POLICY_REVISION,
            "target_exact_head": target_key,
            "evidence": {
                evidence_id: {"status": "unverified"}
                for evidence_id in required_evidence
            },
            "findings": [],
            "residual_risk": "",
            "review_body": "",
            "reviewer": {field: "" for field in (
                *REVIEWER_DECLARATION["fields"], *REVIEWER_DECLARATION["model_agent_fields"],
            )},
            "verdict": "unverified",
        },
    }


def build_agent_response_contract(*, wait_for_ci: bool = True) -> dict[str, Any]:
    return {
        "schema_version": "pr_review_agent_response_contract_v0",
        "table_only_response_allowed": False,
        "slash_prefix_dominates_intent": True,
        "stats_only_requires_explicit_opt_out": True,
        "queue_table_role": "preface_only",
        "default_review_scope": (
            "Follow scheduling_policy and its ranked actionable review_sequence. An explicit "
            "request-scoped PR selection may override ordering only; it does not override "
            "the selected row's review_action_kind or exact-head idempotency."
        ),
        "selection_execution_contract": {
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
        },
        "required_packet_fields_to_preserve": [
            "agent_response_contract",
            "agent_response_contract.review_execution_contract",
            "result_completeness",
            "scheduling_policy",
            "review_groups",
            "pull_requests[review_action_kind!=null].review_plan",
            "pull_requests[review_action_kind!=null].review_template",
            "pull_requests[review_action_kind!=null].evidence_commands",
        ],
        "stats_only_opt_out_examples": [
            "只统计",
            "只列出",
            "stats only",
            "list only",
            "不要 review",
            "不用分析",
        ],
        "required_final_sections": REQUIRED_FINAL_SECTIONS,
        "review_execution_contract": build_review_execution_contract(wait_for_ci=wait_for_ci),
        "explanation_depth_contract": {
            "schema_version": "pr_review_explanation_depth_v0",
            "authority": "agent_response_contract.review_execution_contract",
            "reader_profile": "A technically curious reader who may not know this PR or subsystem.",
            "verdict_preface": "Lead with one evidence-based verdict and the highest-severity reason.",
            "freshness": "Record and recheck the remote head SHA; do not publish a stale verdict.",
        },
        "instructions": [
            "Use scheduling_policy plus review_groups as the queue and require result_completeness.complete=true for exhaustive review.",
            "Start with review_execution_contract.decision_procedure, before implementation narration or prior-comment closure.",
            "Follow the capability-ranked actionable review_sequence; an explicit request-scoped PR selection may override ordering only, while Todo or monitor prose must not replace the stable policy.",
            "Before evidence commands, obey pull_requests[].review_action_kind. A null action stays in pull_requests inventory but is excluded from review_sequence, carries no execution artifacts, and remains readback-only; generic re-review wording selects the PR but does not force duplicate evidence for an already concluded or merged no-action row.",
            "Execute each non-null pull_requests[].review_plan against the shared review_execution_contract before drafting prose.",
            "Do not infer verified evidence from title, labels, changed-file counts, metadata_risk_hint, or green CI alone.",
            ("Observe final CI in addition to repository-native local validation, then attribute red checks before judging this PR; review approval and merge readiness are separate." if wait_for_ci else "Do not fetch, poll, or wait for CI for review or merge readiness. repository_required_checks means repository-native local validation; attribute base-equivalent failures and keep missing affected-invariant evidence blocking."),
            "Recheck the exact remote head before verdict and publication.",
            "After publishing and reading back APPROVE, execute review_execution_contract.approval_closeout; approval alone does not clear another reviewer's effective blocking review.",
            "Render the verified result through a non-null pull_requests[].review_template; host skills must not maintain a competing depth checklist.",
        ],
    }
