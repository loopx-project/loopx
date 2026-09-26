from __future__ import annotations

from typing import Any


REWARD_MEMORY_CATALOG_ENTRY: dict[str, Any] = {
    "id": "reward-memory",
    "origin": "builtin",
    "visibility": "public",
    "provider_id": "loopx-core",
    "documentation": {
        "source_root": "loopx/capabilities/reward_memory",
        "site_root": "capabilities/reward-memory",
        "canonical": "README.md",
        "aliases": [
            {
                "source": "README.md",
                "site": "reference/protocols/reward-memory-architecture-v0.md",
            },
            {
                "source": "README.zh-CN.md",
                "site": "reference/protocols/reward-memory-architecture-v0.zh-CN.md",
            },
        ],
    },
    "title": "Reward-memory candidate, recall, application, and utility-attribution foundation",
    "status": "active-preview",
    "real_world_anchor": (
        "typed feedback memory, provider-owned corpus health, and pilot/meta delegation"
    ),
    "user_value": (
        "Keep run judgments, policies, preferences, reusable experience, and "
        "working context distinct; derive policy content from verified contributor "
        "signals without inventing or widening authority."
    ),
    "entry_command": "loopx reward-memory architecture --format json",
    "commands": [
        {
            "command": "loopx reward-memory architecture --format json",
            "purpose": "Render the five-class Stage-0 architecture, lifecycle, precedence, and staged ownership contract.",
            "write_boundary": "stateless contract output only; no memory, provider, state, or external write",
        },
        {
            "command": "loopx reward-memory route-check --case pr-3237 --format json",
            "purpose": "Exercise deterministic safety guards for the public PR #3237 regression; this fixture is not the live reasoning router.",
            "write_boundary": "stateless public guard fixture only; no issue body, memory content, repository, provider, or external write",
        },
        {
            "command": "loopx reward-memory corpus-registry --format json",
            "purpose": "Render the Stage-1 corpus ownership, authority, scope, freshness, lifecycle, and maintenance registry.",
            "write_boundary": "stateless read model only; provider content stays at its source of truth",
        },
        {
            "command": "loopx reward-memory health-check --case wrong-project --format json",
            "purpose": "Exercise deterministic project, surface, freshness, index, retrieval, readback, and application health states.",
            "write_boundary": "public fixture classification only; no memory, index, receipt, state, or external write",
        },
        {
            "command": "loopx reward-memory candidate-review --case issue-fix-verified-contributor --decision accept --format json",
            "purpose": "Exercise the Stage-2 shared candidate/review seam through the mapping-only Issue Fix adapter.",
            "write_boundary": "stateless decision output only; persistence stays with the declared corpus owner and this command performs no provider or external write",
        },
        {
            "command": "loopx reward-memory evaluate --format json",
            "purpose": "Run the Stage-4 provider-neutral core-contract suite and compact release gate over the real recall/application seam.",
            "write_boundary": "bounded local fixture reads only; no provider, memory, repository, state, model API, or external write",
        },
        {
            "command": "loopx reward-memory dogfood-evaluate --input <compact-observations.json> --format json",
            "purpose": "Bind verified Issue Fix and LoopX module outcomes to compact application-disposition coverage, cost, intervention, bot-feedback, and optional post-outcome utility observations after the Stage-4 gate.",
            "write_boundary": "utility attribution is default-off, proposal-only, and fail-open; this command reads compact receipts only and performs no ranking, authority, memory, operator, provider, or external write",
        },
        {
            "command": "loopx reward-memory utility-project --input <observations.json> --format json",
            "purpose": "Reduce schema-valid Stage-1 utility observations into a deterministic, bounded, read-only utility projection.",
            "write_boundary": "projection and reducer are local pure computation only; no provider, memory, ranking, authority, scheduler, or external write",
        },
        {
            "command": "loopx reward-memory operator-control --input <reviewed-record.json> --action edit --control-ref <ref> --reasoning-summary <summary> --edited-content-summary <summary> --format json",
            "purpose": "Prepare an authority-matched edit or retirement decision for a reviewed active record.",
            "write_boundary": "returns a control decision and next-step receipt only; the declared corpus owner must perform and exactly read back any provider write",
        },
    ],
    "implemented_protocols": [
        {
            "schema_version": "reward_memory_architecture_v0",
            "module": "loopx.capabilities.reward_memory.architecture",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_pilot_meta_route_v0",
            "module": "loopx.capabilities.reward_memory.architecture",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_corpus_registry_v0",
            "module": "loopx.capabilities.reward_memory.registry",
            "doc": "docs/reference/protocols/reward-memory-corpus-registry-v0.md",
        },
        {
            "schema_version": "reward_memory_corpus_health_v0",
            "module": "loopx.capabilities.reward_memory.health",
            "doc": "docs/reference/protocols/reward-memory-corpus-registry-v0.md",
        },
        {
            "schema_version": "reward_memory_candidate_v0",
            "module": "loopx.capabilities.reward_memory.candidate_review",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_candidate_review_v0",
            "module": "loopx.capabilities.reward_memory.candidate_review",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "issue_fix_reward_memory_candidate_adapter_v0",
            "module": "loopx.capabilities.reward_memory.candidate_review",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "scoped_feedback_reward_memory_event_v0",
            "module": "loopx.capabilities.reward_memory.scoped_feedback",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": (
                "scoped_feedback_reward_memory_candidate_adapter_v0"
            ),
            "module": "loopx.capabilities.reward_memory.scoped_feedback",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_active_record_v0",
            "module": "loopx.capabilities.reward_memory.application",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_recall_request_v0",
            "module": "loopx.capabilities.reward_memory.application",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_recall_v0",
            "module": "loopx.capabilities.reward_memory.application",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_application_receipt_v0",
            "module": "loopx.capabilities.reward_memory.application",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "issue_fix_reward_memory_application_v0",
            "module": "loopx.capabilities.issue_fix.reward_memory",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": ("semantic_preference_reward_memory_application_v0"),
            "module": "loopx.capabilities.semantic_preference.reward_memory",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_evaluation_v0",
            "module": "loopx.capabilities.reward_memory.evaluation",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_release_gate_v0",
            "module": "loopx.capabilities.reward_memory.evaluation",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "memory_utility_observation_v0",
            "module": "loopx.capabilities.reward_memory.memory_utility",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "memory_utility_projection_v0",
            "module": "loopx.capabilities.reward_memory.utility_reducer",
            "doc": "docs/reference/protocols/reward-memory-utility-projection-v0.md",
        },
        {
            "schema_version": "reward_memory_dogfood_receipt_v1",
            "module": "loopx.capabilities.reward_memory.dogfood",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_dogfood_batch_v1",
            "module": "loopx.capabilities.reward_memory.dogfood",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
        {
            "schema_version": "reward_memory_operator_control_v0",
            "module": "loopx.capabilities.reward_memory.dogfood",
            "doc": "loopx/capabilities/reward_memory/README.md",
        },
    ],
    "smokes": [
        "python3 examples/reward-memory-architecture-smoke.py",
        "python3 examples/reward-memory-corpus-registry-smoke.py",
        "python3 examples/reward-memory-candidate-review-smoke.py",
        "python3 examples/reward-memory-recall-application-smoke.py",
        "python3 examples/reward-memory-evaluation-smoke.py",
        "python3 examples/reward-memory-dogfood-smoke.py",
        "python3 examples/reward-memory-utility-reducer-smoke.py",
    ],
    "docs": [
        "loopx/capabilities/reward_memory/README.md",
        "docs/reference/protocols/reward-memory-corpus-registry-v0.md",
        "docs/reference/protocols/reward-memory-utility-projection-v0.md",
    ],
    "boundaries": [
        "Run-bound reward is outcome evidence; future influence requires compact candidate derivation and an activation policy.",
        "Verified owner or core-contributor feedback may derive hard-policy content only inside independently verified repository and action authority scope.",
        "Deterministic code enforces authority, scope, privacy, freshness, verification, and conflict guards; model reasoning owns interpretation and trade-offs inside those guards.",
        "Retrieval without current-artifact verification has zero patch authority.",
        "OpenViking cases are evaluation fixtures, provider soul text is not action authority, and session working memory remains continuation context.",
        "LoopX fresh execution context already exists and Stage 2 must reuse it rather than add another context system.",
        "Corpus presence, index presence, retrieval success, readback, and applied receipts are distinct health states.",
        "Project or surface mismatch fails closed before provider availability can influence application.",
        "Stages 0-1 write no corpus, candidate, provider memory, evaluation result, or external artifact.",
        "Stage 2 returns inspectable candidate/review decisions only; accept or retire still requires the caller to use the corpus owner's declared write authority and verify readback.",
        "Issue Fix and generic scoped-feedback adapters map compact domain evidence into the shared core and own no parallel lifecycle, store, scheduler, or recall path.",
        "Stage 3 recalls only after an explicit exact-corpus, module-surface request and a matching read-authority checkpoint; it never scans every corpus automatically.",
        "Function-boundary recall permits one caller-owned query; bounded agentic search permits at most three caller/model-owned queries and contains no semantic router.",
        "Provider and application failures preserve the base output, emit compact receipts or setup hints, and never become user gates automatically.",
        "Private corpus summaries stay transient in-process; public packets retain opaque references and compact lineage only.",
        "Stage 4 proves bounded core-contract invariants only; its fixture pass does not claim semantic uplift or authorize production rollout.",
        "Stage 5 keeps application disposition separate from post-outcome utility: applied, not_applied, and refuted describe coverage, while helpful, harmful, neutral, and unknown require an independently bound utility observation.",
        "Applied plus a successful outcome remains unknown without independent attribution evidence; evaluator scope, outcome, retrieval snapshot, and policy snapshot must match trusted execution context, with receipt-carried snapshots cross-checked when present.",
        "Utility attribution is default-off, proposal-only, and fail-open; absent or malformed evaluator output leaves the main result, application settlement, and trial readiness unchanged.",
        "Stage 5 trial readiness requires Issue Fix, two distinct LoopX domains, applied/not_applied/refuted coverage, and authority-matched edit/retire controls; utility does not affect readiness or authorize ranking, lifecycle, provider, or external writes.",
        "Utility-attribution Stage 1 provides stable observation identity; Stage 2 adds only the bounded idempotent reducer and read-only projection, with no ranking influence or OpenViking writeback.",
    ],
    "next_real_step": (
        "Feed one corpus-owner-approved, exactly read-back record through the "
        "bounded OpenViking Issue Fix pilot and write back compact bot feedback "
        "without widening the Stage-5 trial-only claim."
    ),
}
