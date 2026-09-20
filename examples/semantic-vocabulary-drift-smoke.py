#!/usr/bin/env python3
"""Guard the repository's registered vocabularies against silent drift.

``loopx/semantics/vocabulary_v0.json`` names each kernel and cross-runtime
vocabulary, the exact ``module::Symbol`` allowed to define it, how vocabularies
relate, and the budgets the repository ratchets down. The inventory is computed
from the complete tracked tree on each run, never loaded from a report file.
Vocabulary changes, forks and registry weakening remain checked; ordinary
carrier edits require no generated snapshot commit. Only tracked sources are
read, and no private data is printed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.semantics.inventory import (  # noqa: E402
    INVENTORY_SCHEMA_VERSION,
    SourceFile,
    build_inventory,
    render_inventory,
    collect_string_constants,
    load_sources,
    python_facts,
    string_constant_definitions,
    typescript_facts,
)

from loopx.semantics.production import (  # noqa: E402
    collect_production, validate_production, INPUT_WITNESSES, quota_action_domain, collect_literal_uses,
    PRODUCER_FILES, PRODUCER_ROOTS,
)
from loopx.semantics.python_production import scan_python_production  # noqa: E402
from loopx.semantics.field_use import (  # noqa: E402
    FACTS, ROLES, field_use_summary, lexical_module_count, render_field_uses, scan_field_uses,
)
from scripts.generate_semantic_bindings import build_artifacts  # noqa: E402
from loopx.canary.maintainability_ratchet import evaluate_maintainability_findings  # noqa: E402

REGISTRY_PATH = REPO_ROOT / "loopx" / "semantics" / "vocabulary_v0.json"
REGISTRY_SCHEMA_VERSION = "loopx_semantic_vocabulary_v0"
VALUE_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")
SYMBOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OWNER_SHAPE = re.compile(r"^[A-Za-z0-9_./-]+\.(py|ts)::[A-Za-z_][A-Za-z0-9_]*$")

REGISTRY_KEYS = {
    "schema_version", "rfc", "policy", "coverage_floor", "vocabularies", "relations",
    "projections", "schema_versions", "retirement_ledger", "dual_runtime_twins", "inventory_ratchets",
    "formal_model", "scope_declarations",
}
VOCABULARY_KEYS = {"meaning", "tier", "status", "owners", "values"}
VOCABULARY_OPTIONAL_KEYS = {
    "literal_scan",
    "variable_sourced_values",
    "value_notes",
    "deprecated_values",
    "producers",
    "compatibility_only",
    "return_producers",
    "return_paths",
    "call_producers",
    "input_producer",
}
TIERS = {"kernel", "cross_runtime", "cross_module"}
STATUSES = {"canonical", "legacy", "merge_candidate"}
FORMAL_MODEL_KEYS = {
    "schema_version", "universes", "roles", "role_hierarchy", "relations", "invariants", "proof_boundary",
    "enforcement_policy", "candidate_decisions",
}
FORMAL_MODEL_SCHEMA_VERSION = "loopx_semantic_formal_model_v0"
FORMAL_UNIVERSE_KEYS = {"vocabularies", "values", "sites", "scopes", "roles"}
FORMAL_ROLES = {"owner", "producer", "consumer", "interpreter", "pass_through"}
FORMAL_RELATIONS = {"defines", "produces", "consumes", "interprets", "passes_through", "projects", "persists"}
FORMAL_INVARIANTS = {
    "F1_producer_closedness",
    "F2_canonical_value_liveness",
    "F3_consumer_domain_closedness",
    "F4_scope_separation",
    "F5_projection_totality",
    "F6_persistence_version_compatibility",
}
FORMAL_ENFORCEMENT = {"m0", "m0_5", "m1", "advisory", "unproved"}
# Each formal invariant names the set it quantifies over. The selector is code
# owned, so registry data cannot invent a domain, and both counts are derived
# from the registry on every run rather than trusted: a declared size that no
# longer matches the tree fails in the same diff that changed the tree.
# ``verified`` is the sub-domain the invariant's enforcement stage actually
# walks; ``registered`` is the whole population of the same unit. An advisory or
# unproved stage walks nothing, so its ``verified`` count must be 0 -- that is
# what those stages mean, and it is checked here instead of asserted in prose.
FORMAL_DOMAIN_KEYS = {"quantifies_over", "verified", "registered", "evidence_bound"}
FORMAL_DOMAIN_SELECTORS: dict[str, Callable[[dict[str, Any]], tuple[int, int]]] = {
    # ``check_producers`` walks exactly the vocabularies that declare producers.
    # Today that predicate selects the kernel tier and nothing else, so F1/F2
    # quantify over 6 of 26 vocabularies, not over V.
    "vocabularies[tier=kernel].producers": lambda registry: (
        sum(1 for entry in registry["vocabularies"].values()
            if entry["tier"] == "kernel" and "producers" in entry),
        len(registry["vocabularies"]),
    ),
    "vocabularies[*]": lambda registry: (
        len(registry["vocabularies"]), len(registry["vocabularies"]),
    ),
    "scope_declarations[*].contexts": lambda registry: (
        sum(len(entry["contexts"]) for entry in registry["scope_declarations"].values()),
        sum(len(entry["contexts"]) for entry in registry["scope_declarations"].values()),
    ),
    # ``verified`` counts the projections ``check_projections`` actually imports
    # and executes, not the registry's own row count. A registry entry is a
    # declaration; counting it as its own evidence let a projection whose owner
    # function does not exist report itself verified. An unexecuted entry now
    # raises ``registered`` without raising ``verified``, the same way F1
    # reports 6 of 26.
    "projections[*]": lambda registry: (
        sum(1 for name in registry["projections"] if name in EXECUTED_PROJECTIONS),
        len(registry["projections"]),
    ),
    # No site declares a persists edge, so F6 has an empty domain, not a small one.
    "persists_edges[*]": lambda registry: (0, 0),
}
# The projections F5 executes, and the owner each one must name. Pinned in code
# for the same reason as COVERAGE_ANCHOR and FORMAL_DOMAIN_ANCHOR: a data-only
# edit to the registry must not be able to widen what the invariant claims to
# have verified, and the owner string has to stay tied to the function the check
# below imports rather than being prose the registry can restate.
EXECUTED_PROJECTIONS = {
    "turn_route_to_loop_disposition":
        "loopx/control_plane/turn_driver/turn_contract_generated.py::project_turn_route",
}
FORMAL_ENFORCED_STAGES = frozenset(FORMAL_ENFORCEMENT - {"advisory", "unproved"})
# What a verified sub-domain rests on, and which stages may claim it. The first
# three describe evidence something actually walked, so only an enforced stage
# can hold one; the last two say nothing is walked and belong to one stage each.
FORMAL_EVIDENCE_BOUNDS: dict[str, frozenset[str]] = {
    "producer_scan_reach": FORMAL_ENFORCED_STAGES,
    "declared_defining_modules": FORMAL_ENFORCED_STAGES,
    "executable_owner_function": FORMAL_ENFORCED_STAGES,
    "inventory_only": frozenset({"advisory"}),
    "unmodelled": frozenset({"unproved"}),
}
# Which set each invariant is about, pinned in code for the same reason as
# COVERAGE_ANCHOR and BUDGET_ANCHOR: the registry value must equal the anchor, so
# an invariant cannot quietly widen its own claim by choosing a looser selector
# in a data-only edit. Restating an invariant over a different domain is a
# normative change and edits this literal in the same diff.
FORMAL_DOMAIN_ANCHOR = {
    "F1_producer_closedness": ("vocabularies[tier=kernel].producers", "producer_scan_reach"),
    "F2_canonical_value_liveness": ("vocabularies[tier=kernel].producers", "producer_scan_reach"),
    "F3_consumer_domain_closedness": ("vocabularies[*]", "inventory_only"),
    "F4_scope_separation": ("scope_declarations[*].contexts", "declared_defining_modules"),
    "F5_projection_totality": ("projections[*]", "executable_owner_function"),
    "F6_persistence_version_compatibility": ("persists_edges[*]", "unmodelled"),
}
FORMAL_POLICY_KEYS = {"blocking_now", "blocking_next", "advisory", "unproved"}
FORMAL_CANDIDATE_DECISIONS = {
    "reuse_existing",
    "extend_vocabulary",
    "create_vocabulary",
    "local_only",
    "external_input",
    "compatibility_only",
    "unknown",
}

# Hard ceiling on the registry's own floors and budgets, kept in code rather than
# in the registry so one single-diff edit to ``vocabulary_v0.json`` cannot relax
# the ratchet that guards it. Same anchor pattern as
# ``tests/control_plane/test_m6_quality_gates.py::RFC_MODULE_BUDGETS``: the
# registry value must equal the anchor, so tightening a budget edits this literal
# and the JSON in one diff, and a later PR cannot raise the JSON back toward a stale
# anchor. A `<=` comparison would let every tightening below the anchor be undone
# silently; that is the gap the anchor exists to close.
COVERAGE_ANCHOR = {
    "vocabularies": 26,
    "owner_symbols": 51,
    "literal_scan_fields": 1,
    "projections": 1,
    "relations": 9,
    "schema_versions": 1,
}
COVERAGE_SUFFIX_ANCHOR = (".py", ".ts")
LITERAL_SCAN_ROOTS = ["loopx"]
# Input producers with an executable witness in loopx.semantics.production.
INPUT_PRODUCER_ANCHOR = {
    "turn_result_kind": "loopx/control_plane/turn_driver/transaction.py::_result_kind",
    "loop_disposition": "loopx/control_plane/turn_driver/loop_controller.py::decide_loop_disposition",
}
PRODUCER_VOCABULARY_ANCHOR = {
    "effective_action", "turn_route", "loop_disposition", "agent_scope_frontier_action", "turn_result_kind", "lease_action",
}
RETURN_PRODUCER_ANCHOR = {
    "turn_route": {"loopx/control_plane/turn_driver/driver.py::_typed_route", "loopx/control_plane/turn_driver/loop_controller.py::_envelope_route", "loopx/control_plane/turn_driver/driver.py::build_loopx_turn_plan"},
    "loop_disposition": {"loopx/control_plane/turn_driver/turn_contract_generated.py::project_turn_route"},
    "effective_action": {"loopx/control_plane/quota/decision_summary.py::quota_effective_action", "loopx/control_plane/quota/decision_summary.py::_task_orchestration_effective_action"},
    "turn_result_kind": {"loopx/control_plane/turn_driver/executor.py::_task_validation_receipt"},
}
# Reviewed output arguments/paths replace the old any-enum-use heuristic. These
# anchors retain that coverage if registry metadata is accidentally removed.
CALL_PRODUCER_ANCHOR = {
    "loop_disposition": {"loopx/control_plane/turn_driver/loop_controller.py::_disposition": ["disposition"]},
    "agent_scope_frontier_action": {"loopx/control_plane/agents/agent_scope_frontier.py::build_agent_scope_frontier_payload": ["action"]},
    "turn_result_kind": {
        "loopx/control_plane/turn_driver/executor.py::_host_failure": ["kind"],
        "loopx/control_plane/turn_driver/executor.py::_task_validation_receipt": ["recovery_kind"],
    },
}
RETURN_PATH_ANCHOR = {
    "effective_action": {"loopx/control_plane/quota/decision_summary.py::_task_orchestration_effective_action": [0]},
    "turn_route": {"loopx/control_plane/turn_driver/driver.py::build_loopx_turn_plan": ["route", "kind"]},
    "turn_result_kind": {"loopx/control_plane/turn_driver/executor.py::_task_validation_receipt": ["recovery_kind"]},
}
TWIN_ROOT_ANCHOR = "loopx/control_plane"
TWIN_BUDGET_ANCHOR = 43
BUDGET_ANCHOR = {
    "same_runtime_forks": 18,
    "same_runtime_fork_definitions": 41,
    "conflicting_values": 16,
    "conflicting_definitions": 55,
    "schema_version_same_runtime_forks": 7,
    "multi_value_twins": 13,
    "multi_value_forks": 2,
    "multi_value_forks_semantic": 1,
    "multi_value_fork_definitions": 6,
    "same_runtime_forks_semantic": 11,
    "conflicting_values_semantic": 0,
}
# Budgets for the legacy should-run decision fields, anchored the same way so a
# single diff cannot widen a retirement budget to keep a field alive.
RETIREMENT_ANCHOR = {
    "execution_obligation": (20, 1),
    "heartbeat_recommendation": (17, 1),
    "work_lane_contract": (29, 3),
    "external_evidence_observation": (8, 1),
    "goal_boundary": (30, 2),
    "protocol_action_packet": (5, 2),
}
# B3 migration surface: modules that read, write or bind the legacy field, plus
# the modules holding its name unresolved, which have to be investigated before
# anyone can say the field is gone. Anchored like RETIREMENT_ANCHOR so the
# registry and this literal move in one diff. This does not replace the token
# budget above; Q11 owns that decision, and until it lands both are checked.
MIGRATION_SURFACE_ANCHOR = {
    "execution_obligation": (15, 1),
    "heartbeat_recommendation": (13, 1),
    "work_lane_contract": (29, 3),
    "external_evidence_observation": (7, 1),
    "goal_boundary": (16, 1),
    "protocol_action_packet": (5, 2),
}
RETIREMENT_FIELD_KEYS = {
    "python_module_budget", "typescript_module_budget",
    "python_migration_surface", "typescript_migration_surface",
}
RATCHET_KEYS = (
    "same_runtime_forks",
    "same_runtime_fork_definitions",
    "conflicting_values",
    "conflicting_definitions",
    "schema_version_same_runtime_forks",
    "multi_value_twins",
    "multi_value_forks",
    "multi_value_forks_semantic",
    "multi_value_fork_definitions",
    "same_runtime_forks_semantic",
    "conflicting_values_semantic",
)

# Q7 shares the existing lifecycle, not canary's findings, targets or waivers.
# No current inventory overrun justifies a reviewed exception.
REVIEWED_SEMANTIC_INVENTORY_EXCEPTIONS: dict[str, dict[str, Any]] = {}



class Drift(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Drift(message)


# --- registry shape -----------------------------------------------------------------


def load_registry() -> dict[str, Any]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    require(set(registry) == REGISTRY_KEYS, f"registry keys must be exactly {sorted(REGISTRY_KEYS)}")
    require(registry["schema_version"] == REGISTRY_SCHEMA_VERSION, f"registry schema_version must be {REGISTRY_SCHEMA_VERSION}")
    require((REPO_ROOT / registry["rfc"]).is_file(), f"registry must point at an existing RFC: {registry['rfc']}")
    for name, vocabulary in registry["vocabularies"].items():
        require(VALUE_SHAPE.match(name) is not None, f"vocabulary name must be lower snake_case: {name}")
        keys = set(vocabulary)
        require(VOCABULARY_KEYS <= keys <= VOCABULARY_KEYS | VOCABULARY_OPTIONAL_KEYS, f"{name}: unexpected keys {sorted(keys ^ VOCABULARY_KEYS)}")
        require(vocabulary["tier"] in TIERS, f"{name}: tier must be one of {sorted(TIERS)}")
        require(vocabulary["status"] in STATUSES, f"{name}: status must be one of {sorted(STATUSES)}")
        owners = vocabulary["owners"]
        require(set(owners) == {"python", "typescript"}, f"{name}: owners must name python and typescript, each module::Symbol or null")
        for runtime, owner in owners.items():
            if owner is None:
                continue
            require(OWNER_SHAPE.match(owner) is not None, f"{name}: {runtime} owner must be module::Symbol, got {owner!r}")
            require(owner.endswith(".py::" + owner.split("::")[1]) if runtime == "python" else owner.split("::")[0].endswith(".ts"), f"{name}: {runtime} owner has the wrong suffix: {owner}")
            require((REPO_ROOT / owner.split("::")[0]).is_file(), f"{name}: owner module does not exist: {owner}")
        require(any(owners.values()) or "literal_scan" in vocabulary, f"{name}: a vocabulary with no owner symbol must declare a literal_scan")
        values = vocabulary["values"]
        require(isinstance(values, list) and values, f"{name}: values must be a non-empty list")
        require(len(values) == len(set(values)), f"{name}: values repeat")
        malformed = [value for value in values if not VALUE_SHAPE.match(value)]
        require(not malformed, f"{name}: values must be lower snake_case: {malformed}")
        for key in ("value_notes", "variable_sourced_values"):
            extra = set(vocabulary.get(key, {})) - set(values)
            require(not extra, f"{name}: {key} names unregistered values {sorted(extra)}")
        require(set(vocabulary.get("deprecated_values", [])) <= set(values), f"{name}: deprecated_values must be a subset of values")
        producers = vocabulary.get("producers")
        if producers is not None:
            require(isinstance(producers, list), f"{name}: producers must be a list")
            require(bool(producers) or set(vocabulary.get('compatibility_only', {})) == set(values), f"{name}: empty producers require every value to be compatibility-only")
            require(all(isinstance(site, str) and OWNER_SHAPE.match(site) for site in producers), f"{name}: producers must be module::Symbol sites")
        if 'input_producer' in vocabulary:
            require(vocabulary['input_producer'] == INPUT_PRODUCER_ANCHOR.get(name), f"{name}: unrecognised input producer")
        returns = vocabulary.get("return_producers", [])
        require(isinstance(returns, list) and all(isinstance(site, str) and OWNER_SHAPE.match(site) for site in returns), f"{name}: return_producers must be module::Symbol sites")
        require(set(returns) <= set(producers or []), f"{name}: return_producers must also be registered producers")
        paths = vocabulary.get('return_paths', {})
        require(isinstance(paths, dict) and set(paths) <= set(returns), f"{name}: return_paths must name declared return producers")
        require(all(isinstance(path, list) and path and all(type(key) in (str, int) for key in path)
                    for path in paths.values()), f"{name}: return_paths must select literal fields or tuple indexes")
        calls = vocabulary.get('call_producers', {})
        require(isinstance(calls, dict), f"{name}: call_producers must be a builder-to-parameters object")
        require(all(isinstance(site, str) and OWNER_SHAPE.match(site) and isinstance(names, list) and names
                    and all(isinstance(arg, str) and arg.isidentifier() for arg in names)
                    and len(names) == len(set(names)) for site, names in calls.items()),
                f"{name}: call_producers must name qualified builders and distinct parameters")
        compatibility = vocabulary.get("compatibility_only")
        if compatibility is not None:
            require(isinstance(compatibility, dict), f"{name}: compatibility_only must be an object")
            for value, metadata in compatibility.items():
                require(isinstance(metadata, dict) and set(metadata) == {"reason", "retirement"}, f"{name}: compatibility_only.{value} needs reason and retirement")
                require(all(isinstance(item, str) and item.strip() for item in metadata.values()), f"{name}: compatibility_only.{value} metadata must be non-empty text")
        scan = vocabulary.get("literal_scan")
        if scan is not None:
            require(set(scan) == {"field", "roots", "suffixes"}, f"{name}: literal_scan keys must be field, roots, suffixes")
            require(VALUE_SHAPE.match(scan["field"]) is not None, f"{name}: literal_scan.field must be an identifier")
    # Last: the formal model derives its domain sizes from the sets checked above.
    check_formal_model(registry["formal_model"], registry)
    return registry


def check_formal_model(model: dict[str, Any], registry: dict[str, Any]) -> None:
    """Validate the formal vocabulary model's finite signature and proof ledger.

    This is deliberately a schema check, not a claim that the current scanner
    proves every property. Each property carries an enforcement stage, a declared
    domain whose size is derived from ``registry`` rather than trusted, and the
    proof boundary records what remains unproved. The registry is required, not
    optional: a domain nobody counts is the defect this field exists to prevent.
    """
    require(set(model) == FORMAL_MODEL_KEYS, f"formal_model keys must be exactly {sorted(FORMAL_MODEL_KEYS)}")
    require(model["schema_version"] == FORMAL_MODEL_SCHEMA_VERSION, "formal_model schema_version drift")
    require(set(model["universes"]) == FORMAL_UNIVERSE_KEYS, "formal_model universes must name the declared sets")
    require(set(model["roles"]) == FORMAL_ROLES, "formal_model roles must include the consumer role and its subroles")
    require(model["role_hierarchy"] == {"consumer": ["interpreter", "pass_through"]},
            "formal_model role_hierarchy must classify interpreter and pass_through as consumers")
    require(set(model["relations"]) == FORMAL_RELATIONS, "formal_model relations must be the declared edge kinds")
    invariants = model["invariants"]
    require(isinstance(invariants, list), "formal_model invariants must be a list")
    invariant_ids = [item.get("id") for item in invariants]
    require(set(invariant_ids) == FORMAL_INVARIANTS, "formal_model invariants must cover exactly F1-F6")
    # Every dict below is built by id, so a repeated entry is silently reduced to
    # its last occurrence: two entries for one id would both validate while only
    # one of them is reported, and a reader could not tell which statement,
    # evidence boundary or stage the smoke actually walked.
    require(len(invariant_ids) == len(set(invariant_ids)),
            "formal_model invariants must state each of F1-F6 exactly once")
    require(set(FORMAL_DOMAIN_ANCHOR) == FORMAL_INVARIANTS,
            "FORMAL_DOMAIN_ANCHOR must pin a domain for every formal invariant")
    for item in invariants:
        require(set(item) == {"id", "statement", "enforcement", "evidence", "domain"},
                f"formal invariant {item.get('id')} has an invalid shape")
        require(item["enforcement"] in FORMAL_ENFORCEMENT,
                f"formal invariant {item['id']} has unknown enforcement stage")
        require(item["statement"].strip() and item["evidence"].strip(),
                f"formal invariant {item['id']} needs a statement and evidence boundary")
        check_invariant_domain(item, registry)
    policy = model["enforcement_policy"]
    require(set(policy) == FORMAL_POLICY_KEYS,
            "formal_model enforcement_policy must separate current, next, advisory, and unproved checks")
    policy_ids = [item_id for ids in policy.values() for item_id in ids]
    require(set(policy_ids) == FORMAL_INVARIANTS and len(policy_ids) == len(set(policy_ids)),
            "formal_model enforcement_policy must partition all invariants exactly once")
    stage_for_policy = {
        "blocking_now": "m0",
        "blocking_next": "m0_5",
        "advisory": "advisory",
        "unproved": "unproved",
    }
    stages = {item["id"]: item["enforcement"] for item in invariants}
    for policy_name, ids in policy.items():
        require(all(stages[item_id] == stage_for_policy[policy_name] for item_id in ids),
                f"formal_model policy lane {policy_name} disagrees with invariant enforcement stage")
    candidates = model["candidate_decisions"]
    require(set(candidates) == {"values", "default", "meaning"},
            "formal_model candidate_decisions must define values, default, and meaning")
    require(candidates["values"] == sorted(FORMAL_CANDIDATE_DECISIONS),
            "formal_model candidate_decisions must be a stable exhaustive classification")
    require(candidates["default"] == "unknown",
            "formal_model candidate_decisions must default unresolved candidates to unknown")
    require(candidates["meaning"].strip(), "formal_model candidate_decisions needs a meaning")
    boundary = model["proof_boundary"]
    require(set(boundary) == {"established", "bounded", "unknown", "unproved"},
            "formal_model proof_boundary must separate established, bounded, unknown, and unproved claims")
    for key in boundary:
        require(isinstance(boundary[key], list) and all(isinstance(value, str) and value.strip() for value in boundary[key]),
                f"formal_model proof_boundary.{key} must contain non-empty claim names")


def check_invariant_domain(invariant: dict[str, Any], registry: dict[str, Any]) -> None:
    """Require a declared invariant domain to match the set actually walked.

    An unconditional statement over a domain the checker never visits is the
    defect this field exists to catch: the quantifier in ``statement`` has to be
    bounded by the set counted here. Both counts are derived from the registry,
    so widening the registry without widening the claim -- or the reverse -- is a
    one-diff failure rather than silent rot.
    """
    name = invariant["id"]
    domain = invariant["domain"]
    require(isinstance(domain, dict) and set(domain) == FORMAL_DOMAIN_KEYS,
            f"formal invariant {name} domain keys must be exactly {sorted(FORMAL_DOMAIN_KEYS)}")
    selector = domain["quantifies_over"]
    require(selector in FORMAL_DOMAIN_SELECTORS,
            f"formal invariant {name} quantifies over an unknown domain {selector!r}; "
            f"selectors are code owned, not registry data: {sorted(FORMAL_DOMAIN_SELECTORS)}")
    bound = domain["evidence_bound"]
    require(bound in FORMAL_EVIDENCE_BOUNDS,
            f"formal invariant {name} has an unknown evidence bound {bound!r}; "
            f"bounds are code owned: {sorted(FORMAL_EVIDENCE_BOUNDS)}")
    require((selector, bound) == FORMAL_DOMAIN_ANCHOR[name],
            f"formal invariant {name} declares domain {(selector, bound)} but FORMAL_DOMAIN_ANCHOR "
            f"pins {FORMAL_DOMAIN_ANCHOR[name]}; restating an invariant over another domain is a "
            "normative change and moves the anchor in the same diff")
    stage = invariant["enforcement"]
    allowed = FORMAL_EVIDENCE_BOUNDS[bound]
    require(stage in allowed,
            f"formal invariant {name} claims evidence bound {bound}, which only "
            f"{sorted(allowed)} may hold; its stage is {stage}")
    require(all(type(domain[key]) is int and domain[key] >= 0 for key in ("verified", "registered")),
            f"formal invariant {name} domain sizes must be non-negative integers")
    walked, population = FORMAL_DOMAIN_SELECTORS[selector](registry)
    require(domain["registered"] == population,
            f"formal invariant {name} declares {domain['registered']} registered members of "
            f"{selector}; the registry holds {population}")
    if stage not in FORMAL_ENFORCED_STAGES:
        require(domain["verified"] == 0,
                f"formal invariant {name} is {stage} but claims {domain['verified']} verified "
                f"members of {selector}; an unenforced stage walks nothing")
    else:
        require(domain["verified"] == walked,
                f"formal invariant {name} claims {domain['verified']} verified members of "
                f"{selector}; the {stage} check walks {walked}")
        require(domain["verified"] > 0,
                f"formal invariant {name} is enforced at {stage} over an empty domain")
    require(domain["verified"] <= domain["registered"],
            f"formal invariant {name} cannot verify more members than the registry holds")


def check_coverage_floor(registry: dict[str, Any]) -> str:
    try:
        quota_action_domain(registry)
    except ValueError as error:
        raise Drift(str(error)) from error
    for name, site in INPUT_PRODUCER_ANCHOR.items():
        require(registry['vocabularies'][name].get('input_producer') == site,
                f"{name}: input producer coverage must retain the anchored site {site}")
        require(site in INPUT_WITNESSES, f"{name}: anchored input producer has no executable witness")
    for name in PRODUCER_VOCABULARY_ANCHOR:
        require("producers" in registry["vocabularies"][name], f"{name}: producer coverage dropped below PRODUCER_VOCABULARY_ANCHOR")
    for name, required in RETURN_PRODUCER_ANCHOR.items():
        actual_returns = set(registry['vocabularies'][name].get('return_producers', []))
        require(required <= actual_returns, f"{name}: return producer coverage dropped below RETURN_PRODUCER_ANCHOR")
    for metadata, anchor in (("call_producers", CALL_PRODUCER_ANCHOR), ("return_paths", RETURN_PATH_ANCHOR)):
        for name, vocabulary in registry['vocabularies'].items():
            require(vocabulary.get(metadata, {}) == anchor.get(name, {}),
                    f"{name}: {metadata} must retain anchored output evidence exactly (default empty)")
    for vocabulary in registry["vocabularies"].values():
        if scan := vocabulary.get("literal_scan"):
            require(scan["roots"] == LITERAL_SCAN_ROOTS, "literal_scan roots must cover loopx")
            require(set(scan["suffixes"]) == set(COVERAGE_SUFFIX_ANCHOR), "literal_scan must cover both Python and TypeScript")
    floor = registry["coverage_floor"]
    actual = {
        "vocabularies": len(registry["vocabularies"]),
        "owner_symbols": sum(1 for v in registry["vocabularies"].values() for o in v["owners"].values() if o),
        "literal_scan_fields": sum(1 for v in registry["vocabularies"].values() if "literal_scan" in v),
        "projections": len(registry["projections"]),
        "relations": sum(len(group) for group in registry["relations"].values()),
        "schema_versions": len(registry["schema_versions"]),
    }
    for key, count in actual.items():
        require(count >= floor[key], f"coverage_floor.{key} is {floor[key]} but the registry now has {count}; coverage may only grow")
    declared_suffixes = {s for v in registry["vocabularies"].values() for s in v.get("literal_scan", {}).get("suffixes", [])}
    require(set(floor["literal_scan_suffixes"]) <= declared_suffixes, f"literal scans must still cover {floor['literal_scan_suffixes']}; declared {sorted(declared_suffixes)}")
    for key, anchored in COVERAGE_ANCHOR.items():
        require(
            floor[key] == anchored,
            f"coverage_floor.{key} is {floor[key]} but COVERAGE_ANCHOR pins {anchored}; "
            "the registry and the anchor move together in one diff (see COVERAGE_ANCHOR in this smoke)",
        )
    for suffix in COVERAGE_SUFFIX_ANCHOR:
        require(suffix in set(floor["literal_scan_suffixes"]), f"coverage_floor.literal_scan_suffixes dropped the anchored suffix {suffix}")
    return "coverage=" + ",".join(f"{key}:{count}/{floor[key]}" for key, count in actual.items())


# --- owners and closed sets -----------------------------------------------------------


def source(path: str) -> SourceFile:
    file = REPO_ROOT / path
    return SourceFile(path=path, suffix=file.suffix, text=file.read_text(encoding="utf-8", errors="replace"))


def owner_values(owner: str) -> list[str]:
    module, symbol = owner.split("::")
    facts = python_facts(source(module)) if module.endswith(".py") else typescript_facts(source(module))
    sections = ("enums", "closed_sets", "literal_aliases") if module.endswith(".py") else ("const_arrays",)
    for section in sections:
        for entry in facts[section]:
            if entry["name"] == symbol:
                return list(entry["values"])
    raise Drift(f"{module} does not define a string enum, closed set, Literal alias, or as-const array named {symbol}")


def assert_closed_set(label: str, actual: list[str], expected: list[str]) -> None:
    require(len(actual) == len(set(actual)), f"{label} repeats a value: {actual}")
    missing = sorted(set(expected) - set(actual))
    unregistered = sorted(set(actual) - set(expected))
    require(not missing and not unregistered, f"{label} drifted from the registry; missing={missing} unregistered={unregistered}")


def check_owned_vocabularies(registry: dict[str, Any], inventory: dict[str, Any]) -> None:
    defined_in: dict[str, set[str]] = {}
    for section in ("python_enums", "python_closed_sets", "python_literal_aliases", "typescript_const_arrays"):
        for entry in inventory[section]:
            defined_in.setdefault(entry["name"], set()).add(entry["module"])
    problems: list[str] = []
    for name, vocabulary in registry["vocabularies"].items():
        owners = [owner for owner in vocabulary["owners"].values() if owner]
        for owner in owners:
            try:
                assert_closed_set(f"{name} owner {owner}", owner_values(owner), vocabulary["values"])
            except Drift as error:
                problems.append(str(error))
        # I1: a registered symbol name is defined only in its owner modules.
        for owner in owners:
            _module, symbol = owner.split("::")
            others = sorted(defined_in.get(symbol, set()) - {o.split("::")[0] for o in owners})
            if others:
                problems.append(f"{name}: {symbol} is also defined in {others}; only the registered owners may define it")
    require(not problems, "owned vocabularies drifted:\n  " + "\n  ".join(problems))


# --- literal scan -------------------------------------------------------------------


def scan_literals(field: str, roots: list[str], suffixes: list[str], sources: list[SourceFile]) -> dict[str, set[str]]:
    selected = [source for source in sources if source.suffix in suffixes
                and any(source.path.startswith(root.rstrip('/') + '/') for root in roots)]
    return collect_literal_uses(REPO_ROOT, field, selected)


def check_literal_vocabularies(registry: dict[str, Any], sources: list[SourceFile]) -> None:
    for name, vocabulary in registry["vocabularies"].items():
        scan = vocabulary.get("literal_scan")
        if not scan:
            continue
        observed = scan_literals(scan["field"], scan["roots"], scan["suffixes"], sources)
        expected = quota_action_domain(registry) if name == 'effective_action' else set(vocabulary["values"])
        unregistered = {value: sorted(files) for value, files in observed.items() if value not in expected}
        require(not unregistered, f"{name}: literals not in the registry (register them or use a registered value): {unregistered}")
        if name == 'effective_action':
            require(not observed,
                    f"{name}: bare action literals outside the owner: "
                    f"{ {value: sorted(paths) for value, paths in observed.items()} }; "
                    "import EffectiveAction or AgentScopeFrontierAction instead")
        variable_sourced = vocabulary.get("variable_sourced_values", {})
        for value, producer in variable_sourced.items():
            text = (REPO_ROOT / producer).read_text(encoding="utf-8", errors="replace")
            require(f'"{value}"' in text, f"{name}: variable-sourced value {value} is no longer produced by {producer}")
        owner_values_seen = {
            value
            for owner in vocabulary["owners"].values()
            if owner
            for value in owner_values(owner)
        }
        unused = sorted(
            set(vocabulary['values']) - set(observed) - set(variable_sourced) - owner_values_seen
        )
        require(not unused, f"{name}: registry lists values no module carries: {unused}")


# --- bounded producer scan ----------------------------------------------------------


def _producer_literals(field: str, source: SourceFile) -> set[str]:
    # Compatibility helper for direct-form mutation fixtures. No enum definitions
    # are supplied, so these tests cannot accidentally count owners as producers.
    if source.suffix != '.py':
        return set()
    rows = scan_python_production(source, field=field, enums={})
    return set().union(*(row.values for row in rows))


# The blocker labels ``summarise_blockers`` explains as unable to become evidence,
# named once so the report can count them instead of restating the rule. They are
# the floor under the unresolved total, not a backlog anyone can work down.
PERMANENTLY_UNRESOLVABLE_BLOCKERS = ('annotation_only', 'argument_name_only')


def blocker_label(site: str) -> str:
    return site.rpartition('[')[2].rstrip(']') or 'other'


def count_permanently_unresolvable(sites: list[str]) -> int:
    """Count reported sites that can never become evidence, however wide the scan."""
    return sum(1 for site in sites if blocker_label(site) in PERMANENTLY_UNRESOLVABLE_BLOCKERS)


def producer_scan_reach(sources: list[SourceFile]) -> tuple[int, int]:
    """Files the F1/F2 producer scan reaches, out of the tracked ``loopx/`` tree.

    Derived on every run, never pinned in the registry: the denominator moves
    with any new module, so a literal here would fail diffs that have nothing to
    do with semantics. This reach is the evidence bound F1 and F2 declare, so the
    report states it instead of leaving the bound implicit.
    """
    scanned = sum(1 for source in sources
                  if source.path in PRODUCER_FILES
                  or any(source.path.startswith(root + '/') for root in PRODUCER_ROOTS))
    return scanned, len(sources)


def summarise_blockers(sites: list[str]) -> str:
    """Count reported sites by blocker so the total is actionable, not opaque.

    `argument_name_only` and `annotation_only` can never become evidence: the
    first is a field-named keyword argument, the second a bare declaration.
    Counting them with resolvable paths would make the total look reducible.
    """

    counts: dict[str, int] = {}
    for site in sites:
        label = blocker_label(site)
        counts[label] = counts.get(label, 0) + 1
    return ','.join(f"{label}={counts[label]}" for label in sorted(counts))


def summarise_formal_domains(registry: dict[str, Any], sources: list[SourceFile]) -> str:
    """Print how much each formal invariant actually quantifies over.

    The sizes are the ones ``check_invariant_domain`` derived, so the report and
    the registry cannot disagree. The scan reach is measured here because it is
    a property of the tree rather than of the registry.
    """
    invariants = registry['formal_model']['invariants']
    sizes = ','.join(
        f"{item['id'].split('_')[0]}:{item['domain']['verified']}/{item['domain']['registered']}"
        for item in sorted(invariants, key=lambda item: item['id'])
    )
    vocabularies = registry['vocabularies']
    kernel = [name for name, v in vocabularies.items() if v['tier'] == 'kernel']
    cross_runtime = [name for name, v in vocabularies.items() if v['tier'] == 'cross_runtime']
    covered = [name for name in kernel if 'producers' in vocabularies[name]]
    unverified = [name for name in cross_runtime if 'producers' not in vocabularies[name]]
    scanned, tracked = producer_scan_reach(sources)
    # Both sides of these ratios came from one expression, so they printed 100%
    # by construction: ``projections={len(registry['projections'])}`` over
    # itself reported full coverage however many registered projections the
    # check never executed. The pair now comes from the same code-owned
    # selector ``check_invariant_domain`` validates the declared domain
    # against, so the detail line cannot disagree with the invariant above it.
    projections_walked, projections_registered = FORMAL_DOMAIN_SELECTORS["projections[*]"](registry)
    # F4's selector still derives both sides from the declaration list. That is
    # not a second self-satisfying ratio but a fail-closed count:
    # ``check_scope_declarations`` validates every declared context against the
    # modules the inventory really found, or raises. The ratio therefore reads
    # 100% whenever the smoke gets far enough to print it, and what it reports
    # is how many contexts that check had to clear.
    contexts_walked, contexts_registered = FORMAL_DOMAIN_SELECTORS["scope_declarations[*].contexts"](registry)
    return (
        f"formal_domain={sizes} (verified/registered)\n"
        f"  formal_domain_bounds: kernel_with_producers={len(covered)}/{len(kernel)}"
        f" cross_runtime_unverified={len(unverified)}/{len(cross_runtime)}"
        f" producer_scan_reach={scanned}/{tracked}_files"
        f" projections={projections_walked}/{projections_registered}"
        f" scope_declarations={len(registry['scope_declarations'])}"
        f" declared_contexts={contexts_walked}/{contexts_registered}"
    )


def check_producers(registry: dict[str, Any], sources: list[SourceFile]) -> list[str]:
    unknown: list[str] = []
    for name, vocabulary in registry['vocabularies'].items():
        if 'producers' not in vocabulary:
            # Skipped: the whole cross_runtime tier, which declares no producers.
            # F1/F2 therefore hold over the kernel tier only, which is the domain
            # the registry's formal_model states -- not an unconditional claim.
            continue
        try:
            rows = collect_production(REPO_ROOT, vocabulary, sources)
            field_domain = quota_action_domain(registry) if name == 'effective_action' else None
            unknown.extend(validate_production(name, vocabulary, rows, field_domain=field_domain))
        except ValueError as error:
            raise Drift(str(error)) from error
    return sorted(set(unknown))


# --- relations, projections, schema versions ----------------------------------------


def check_relations(registry: dict[str, Any]) -> None:
    vocabularies = registry["vocabularies"]

    def resolve(member: str) -> None:
        vocabulary, _, value = member.partition(".")
        require(vocabulary in vocabularies, f"relation member names unknown vocabulary: {member}")
        require(value in vocabularies[vocabulary]["values"], f"relation member does not resolve: {member}")

    for group in registry["relations"]["same_concept"]:
        require(len(group["members"]) >= 2, f"same_concept {group['concept']} needs two members")
        for member in group["members"]:
            resolve(member)
    for shared in registry["relations"]["shared_field_names"]:
        for slot in shared["slots"]:
            if "vocabularies" in slot:
                require(shared['field'] == 'effective_action' and slot['slot'] == 'should_run.effective_action',
                        'only the anchored should-run field has a composed action domain')
                quota_action_domain(registry)
            elif "vocabulary" in slot:
                require(slot["vocabulary"] in vocabularies, f"shared field slot names unknown vocabulary {slot['vocabulary']}")
            else:
                for value in slot["values"]:
                    resolve(f"{shared['field']}.{value}")
    for subset in registry["relations"]["subsets"]:
        require(subset["superset"] in vocabularies, f"subset {subset['name']} names unknown superset {subset['superset']}")
        superset = set(vocabularies[subset["superset"]]["values"])
        expected = superset - set(subset["excluded"])
        require(set(subset["excluded"]) <= superset, f"subset {subset['name']} excludes values outside {subset['superset']}")
        for owner in subset["owners"].values():
            if owner:
                assert_closed_set(f"subset {subset['name']} owner {owner}", owner_values(owner), sorted(expected))


def check_projections(registry: dict[str, Any]) -> None:
    for name, owner in EXECUTED_PROJECTIONS.items():
        require(name in registry["projections"],
                f"projection {name} is executed by this check but is not registered")
        declared = registry["projections"][name].get("owner")
        require(declared == owner,
                f"projection {name} names owner {declared}; this check executes {owner}, "
                "so the registry would be crediting a function it does not run")
    projection = registry["projections"]["turn_route_to_loop_disposition"]
    from loopx.control_plane.turn_driver.driver import LoopXTurnRoute
    from loopx.control_plane.turn_driver.turn_contract_generated import LoopDisposition, project_turn_route

    mapping = projection["mapping"]
    routes = registry["vocabularies"]["turn_route"]["values"]
    require(sorted(mapping) == sorted(routes), "projection must name every turn_route exactly once")
    for route, expected in mapping.items():
        try:
            actual = project_turn_route(LoopXTurnRoute(route))
        except ValueError:
            require(route == "contract_error" and expected is None, f"route {route} is rejected by the controller but the registry maps it to {expected}")
            continue
        require(expected is not None, f"route {route} is registered as rejected but projects {actual.value}")
        require(actual is LoopDisposition(expected), f"route {route} projects {actual.value}, registry says {expected}")


def check_schema_version_owners(registry: dict[str, Any], sources: list[SourceFile]) -> None:
    definitions = string_constant_definitions(collect_string_constants(sources))
    for name, entry in registry["schema_versions"].items():
        defining = definitions.get(entry["constant"], [])
        modules = sorted(item["module"] for item in defining)
        require(modules == sorted(entry["owner_modules"]), f"schema version {name} is defined in {modules}; registry owners are {entry['owner_modules']}")
        values = {item["value"] for item in defining}
        require(values == {entry["value"]}, f"schema version {name} carries {sorted(values)}; registry says {entry['value']}")


def check_scope_declarations(registry: dict[str, Any], inventory: dict[str, Any]) -> int:
    """Validate explicit bounded-context exceptions and return semantic fork count.

    The raw inventory remains unchanged. A declaration can remove a known,
    reviewed bounded-context reuse from the semantic budget only when every
    defining module is named explicitly. Spelling or directory proximity never
    infers a scope.
    """
    declarations = registry["scope_declarations"]
    forks = {entry["name"]: entry for entry in inventory["duplicate_definitions"]["multi_value_forks"]}
    for name, declaration in declarations.items():
        require(SYMBOL_NAME.match(name) is not None, f"scope declaration name must be an identifier: {name}")
        require(set(declaration) == {"kind", "contexts"}, f"{name}: scope declaration keys must be kind and contexts")
        require(declaration["kind"] == "bounded_context", f"{name}: only bounded_context is supported")
        require(name in forks, f"{name}: scope declaration does not resolve to a multi-value fork")
        contexts = declaration["contexts"]
        require(isinstance(contexts, list) and contexts, f"{name}: contexts must be a non-empty list")
        context_ids: set[str] = set()
        owner_modules: set[str] = set()
        for context in contexts:
            require(set(context) == {"id", "owner"}, f"{name}: each context must have id and owner")
            context_id = context["id"]
            require(isinstance(context_id, str) and VALUE_SHAPE.match(context_id) is not None,
                    f"{name}: context id must be lower snake_case: {context_id!r}")
            require(context_id not in context_ids, f"{name}: duplicate context id {context_id}")
            context_ids.add(context_id)
            owner = context["owner"]
            require(isinstance(owner, str) and OWNER_SHAPE.match(owner) is not None,
                    f"{name}: context owner must be module::Symbol: {owner!r}")
            module, symbol = owner.split("::")
            require(symbol == name, f"{name}: context owner symbol must be {name}, got {symbol}")
            owner_modules.add(module)
        require(len(owner_modules) == len(contexts), f"{name}: each context must have a distinct owner module")
        defining_modules = {item["module"] for item in forks[name]["definitions"]}
        require(owner_modules == defining_modules,
                f"{name}: contexts must name every defining module exactly once; "
                f"declared={sorted(owner_modules)} actual={sorted(defining_modules)}")
    undeclared = set(forks) - set(declarations)
    return len(undeclared)


# --- ratchets -----------------------------------------------------------------------


def check_retirement_budgets(registry: dict[str, Any], sources: list[SourceFile]) -> list[str]:
    report: list[str] = []
    ledger = registry["retirement_ledger"]["should_run_legacy_decision_fields"]["fields"]
    require(set(ledger) == set(RETIREMENT_ANCHOR), f"retirement ledger fields are {sorted(ledger)}; the anchored set is {sorted(RETIREMENT_ANCHOR)}")
    for field, budgets in ledger.items():
        require(
            set(budgets) == RETIREMENT_FIELD_KEYS,
            f"retirement ledger {field} carries {sorted(budgets)}; expected {sorted(RETIREMENT_FIELD_KEYS)}",
        )
        for suffix, key, anchored in (
            (".py", "python_module_budget", RETIREMENT_ANCHOR[field][0]),
            (".ts", "typescript_module_budget", RETIREMENT_ANCHOR[field][1]),
        ):
            actual = count_identifier_modules(field, suffix, sources)
            require(actual <= budgets[key], f"legacy field {field} grew to {actual} {suffix} modules; budget is {budgets[key]}")
            require(
                budgets[key] == anchored,
                f"legacy field {field} {suffix} budget is {budgets[key]} but RETIREMENT_ANCHOR pins {anchored}; "
                "the registry and the anchor move together in one diff (see RETIREMENT_ANCHOR in this smoke)",
            )
            report.append(f"{field}{suffix}={actual}/{budgets[key]}")
    return report


def count_identifier_modules(field: str, suffix: str, sources: list[SourceFile]) -> int:
    """Count modules containing the standalone field token.

    This is intentionally a conservative lexical metric. It removes the known
    ``goal_boundary_repair`` false positive without claiming to prove that every
    remaining occurrence is a reader or that computed accesses are absent.
    ``check_reader_metric`` splits this same population by syntactic role.
    """
    return lexical_module_count(field, suffix, sources)


def check_reader_metric(registry: dict[str, Any], sources: list[SourceFile]) -> tuple[list[str], list[str]]:
    """Check the B3 syntactic metric against the ledger, and report its roles.

    Four obligations, all measured rather than assumed:

    * the five roles partition the token count exactly, so the new metric is a
      reclassification of the same modules and not a different population that
      happens to be smaller. The partition assigns each module its first
      matching role, so it answers "what is this module mainly", not "who
      writes this field": the fact counts beside it overlap on purpose and are
      the ones a retirement reads to find every producer;
    * the five orthogonal fact sets cover that same population. They overlap,
      so they are not required to sum to it; what is required is that every
      module carrying the token owes at least one fact and that no module is
      invented. A module that both reads and writes is in both counts, which
      the single role label hides;
    * the migration surface stays within its anchored budget, so a new reader
      of a legacy field fails the PR path that adds it;
    * the unresolved populations stay visible, and the two kinds stay apart. A
      module holding the field name unresolved, or one the scan could not
      parse, is that field's own work and is inside its surface.
      ``dynamic_mapping_key_sites`` and ``typescript_dynamic_member_sites`` are
      not: they count computed-key accesses anywhere under ``loopx/``, belong
      to no field, and while either is nonzero a field measured at zero readers
      is not thereby proven dead. The smoke says so in its own output.
    """
    ledger = registry["retirement_ledger"]["should_run_legacy_decision_fields"]["fields"]
    summary = field_use_summary(ledger, sources)
    report: list[str] = []
    detail: list[str] = []
    for field in sorted(ledger):
        entry = summary["fields"][field]
        for suffix, runtime, anchored in (
            (".py", "python", MIGRATION_SURFACE_ANCHOR[field][0]),
            (".ts", "typescript", MIGRATION_SURFACE_ANCHOR[field][1]),
        ):
            classified = sum(entry[f"{runtime}_{role}_modules"] for role in ROLES)
            carriers = entry[f"{runtime}_token_modules"]
            require(
                classified == carriers,
                f"{field}{suffix}: roles classify {classified} modules but the token metric finds {carriers}; "
                "the syntactic metric must reclassify the same modules, not a smaller population",
            )
            covered = entry[f"{runtime}_classified_modules"]
            facts = sum(entry[f"{runtime}_{label}_modules"] for _, label in FACTS)
            require(
                covered == carriers,
                f"{field}{suffix}: the fact sets cover {covered} modules but the token metric finds "
                f"{carriers}; every module carrying the token owes at least one fact",
            )
            require(
                facts >= covered,
                f"{field}{suffix}: {facts} facts over {covered} modules; the fact sets overlap by "
                "construction and can never total less than the population they cover",
            )
            budget = ledger[field][f"{runtime}_migration_surface"]
            actual = entry[f"{runtime}_migration_surface"]
            require(
                actual <= budget,
                f"legacy field {field} now needs {actual} {suffix} modules migrated; budget is {budget}",
            )
            require(
                budget == anchored,
                f"legacy field {field} {suffix} migration surface budget is {budget} but "
                f"MIGRATION_SURFACE_ANCHOR pins {anchored}; the registry and the anchor move together in one diff",
            )
            detail.append(
                f"{field}{suffix} surface={actual}/{budget} "
                + " ".join(f"{role}={entry[f'{runtime}_{role}_modules']}" for role in ROLES)
                + " | " + " ".join(
                    f"{label}={entry[f'{runtime}_{label}_modules']}" for _, label in FACTS
                )
                + f" carriers={carriers}"
            )
    report.append(
        f"dynamic_mapping_key_sites={summary['dynamic_mapping_key_sites']} "
        f"typescript_dynamic_member_sites={summary['typescript_dynamic_member_sites']} "
        "(computed keys, unattributable)"
    )
    return report, detail


def check_dual_runtime_twins(registry: dict[str, Any]) -> str:
    entry = registry["dual_runtime_twins"]
    require(entry["root"] == TWIN_ROOT_ANCHOR, "dual_runtime_twins root differs from TWIN_ROOT_ANCHOR")
    require(entry["module_budget"] == TWIN_BUDGET_ANCHOR, "dual_runtime_twins budget differs from TWIN_BUDGET_ANCHOR")
    paths = {file.path for file in load_sources(REPO_ROOT, entry["root"])}
    twins = sorted(path for path in paths if path.endswith(".py") and not path.endswith("/__init__.py") and path[:-3] + ".ts" in paths)
    from scripts.generate_turn_contract import verified_generated_paths
    generated = verified_generated_paths()
    generated_twins = [path for path in twins if path in generated and path[:-3] + '.ts' in generated]
    maintained = len(twins) - len(generated_twins)
    require(maintained <= entry['module_budget'], f"{maintained} independently maintained py/ts twins; budget is {entry['module_budget']}")
    return f"twins_raw={len(twins)} generated_verified={len(generated_twins)} independently_maintained={maintained}/{entry['module_budget']}"


def evaluate_inventory_budget_findings(
    summary: dict[str, Any], semantic_multi_value_forks: int, ratchets: dict[str, Any],
) -> dict[str, Any]:
    """Adapt anchored inventory overruns to the existing reviewed lifecycle."""
    findings = []
    for key in RATCHET_KEYS:
        actual = semantic_multi_value_forks if key == "multi_value_forks_semantic" else summary[key]
        require(
            ratchets[key] == BUDGET_ANCHOR[key],
            f"inventory {key} budget is {ratchets[key]} but BUDGET_ANCHOR pins {BUDGET_ANCHOR[key]}; "
            "the registry and the anchor move together in one diff (see BUDGET_ANCHOR in this smoke)",
        )
        if actual > ratchets[key]:
            findings.append({
                "id": f"semantic_inventory_budget:{key}",
                "category": "semantic_inventory_budget",
                "budget": ratchets[key],
                "metrics": {key: actual},
            })
    return evaluate_maintainability_findings(
        findings, reviewed_exceptions=REVIEWED_SEMANTIC_INVENTORY_EXCEPTIONS,
    )


def check_inventory(registry: dict[str, Any], sources: list[SourceFile]) -> tuple[dict[str, Any], str]:
    inventory = build_inventory(REPO_ROOT, sources=sources)
    require(inventory["schema_version"] == INVENTORY_SCHEMA_VERSION, "inventory schema drift")
    semantic_multi_value_forks = check_scope_declarations(registry, inventory)
    ratchets = registry["inventory_ratchets"]
    summary = inventory["summary"]
    review = evaluate_inventory_budget_findings(summary, semantic_multi_value_forks, ratchets)
    failures = [
        f"inventory {key} grew to {actual}; budget is {finding['budget']} (unreviewed)"
        for finding in review['unreviewed_findings'] for key, actual in finding['metrics'].items()
    ]
    failures += [f"invalid inventory exception: {key}" for key in review['invalid_exceptions']]
    failures += [f"stale inventory exception: {item['id']}" for item in review['stale_exceptions']]
    failures += [f"inventory exception magnitude exceeded: {item['id']} {item['metric_regressions']}"
                 for item in review['magnitude_regressions']]
    require(review['ok'], '; '.join(failures))
    parts = []
    slack = []
    for key in RATCHET_KEYS:
        actual = semantic_multi_value_forks if key == "multi_value_forks_semantic" else summary[key]
        parts.append(f"{key}={actual}/{ratchets[key]}")
        if ratchets[key] > actual:
            # Disclose unlocked headroom so a merge that reverts a tightened
            # budget shows up as new slack in this line instead of passing
            # silently (the guard only fails on overflow, never on slack).
            slack.append(f"{key}={ratchets[key] - actual}")
    if slack:
        parts.append("slack=" + ",".join(slack))
    if review['reviewed_exception_count']:
        parts.append(f"reviewed_inventory_exceptions={review['reviewed_exception_count']}")
    return inventory, " ".join(parts)


def main() -> int:
    registry = load_registry()
    coverage = check_coverage_floor(registry)
    sources = load_sources(REPO_ROOT)
    inventory, ratchets = check_inventory(registry, sources)
    check_owned_vocabularies(registry, inventory)
    for path, expected in build_artifacts().items():
        require(
            path.is_file() and path.read_text(encoding="utf-8") == expected,
            f"{path.relative_to(REPO_ROOT)} is stale; "
            "run uv run python scripts/generate_semantic_bindings.py and commit the result",
        )
    check_literal_vocabularies(registry, sources)
    unknown_producers = check_producers(registry, sources)
    check_relations(registry)
    check_projections(registry)
    check_schema_version_owners(registry, sources)
    budgets = check_retirement_budgets(registry, sources)
    reader_metric, reader_detail = check_reader_metric(registry, sources)
    twins = check_dual_runtime_twins(registry)
    print("semantic-vocabulary-drift-smoke: ok")
    print("  " + coverage)
    print("  " + ratchets)
    print("  " + " ".join(budgets))
    print("  " + " ".join(reader_metric))
    print("  " + twins)
    permanent = count_permanently_unresolvable(unknown_producers)
    print(f"  unresolved_producer_sites={len(unknown_producers)} (not proven safe; "
          f"{permanent} can never become evidence)")
    print("  unresolved_producer_blockers=" + summarise_blockers(unknown_producers))
    uncovered = [name for name, v in registry['vocabularies'].items() if v['tier'] == 'kernel' and 'producers' not in v]
    print(f"  kernel_producer_coverage_pending={','.join(uncovered)}")
    print("  " + summarise_formal_domains(registry, sources))
    if '--report' in sys.argv[1:]:
        for site in unknown_producers:
            print(f"  unknown_producer: {site}")
        for line in reader_detail:
            print(f"  retirement_role: {line}")
        ledger_fields = registry["retirement_ledger"]["should_run_legacy_decision_fields"]["fields"]
        uses, _ = scan_field_uses(ledger_fields, sources)
        for line in render_field_uses(uses):
            print(f"  field_use: {line}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Drift as error:
        print(f"semantic-vocabulary-drift-smoke: FAIL\n  {error}", file=sys.stderr)
        raise SystemExit(1)
