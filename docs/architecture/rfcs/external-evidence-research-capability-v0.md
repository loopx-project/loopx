# RFC: External Evidence Research Capability v0

- Status: Accepted
- Supersedes / closes: none
- Scope: provider-neutral research planning, provenance admission, projection,
  and retirement
- Roadmap: S8 capabilities and domain integration
- Language note: [中文版](external-evidence-research-capability-v0.zh-CN.md) is a semantic mirror; drift is a defect.

## Problem

LoopX currently has useful but separate pieces: host research methods,
connector inventory, provider lifecycle, managed Turn contracts, and downstream
evidence consumers. A registry row can say `supported` without proving that the
provider is installed, enabled, ready, called, or accepted. Conversely, a host
research method can produce good evidence without a typed receipt that other
LoopX callers can inspect.

The product needs one outcome capability, not a generic connector executor:
turn a decision-bound research question into compact evidence whose provenance,
admission, use, and retirement are observable.

## Decision

Add capability `external-evidence-research` with protocol
`external_evidence_research_v0` and lifecycle:

`discover → select → provider execute → provenance receipt → parent admit/reject → downstream projection → retire`.

The request must name object, user activity, decision, evidence kinds, and
constraints. A provider is selectable only when current readback says all four
of `declared`, `installed`, `enabled`, and `ready`. Provider kinds are `method`
and `connector`; their execution remains with their existing owner.

Discovery is a read-only typed projection. It reports method/connector counts,
ready and unavailable counts, and ready provider ids. It also carries an
explicit truth contract: registry presence and `supported` status are not
readiness, and discovery itself observes neither provider execution nor
evidence coverage.

Provider execution stays with the existing method or connector owner. Core's
`receipt` boundary validates the returned identity and provenance against the
exact ready plan and records that a caller-presented receipt was observed. It
does not attest that provider execution occurred, or claim evidence
completeness, admission, or automatic promotion. Failure and empty evidence
remain fail-open to the caller's original-source path.

The plan carries a content-addressed `plan_id` over its normalized request,
provider candidates, selected ready provider, and execution envelope. The
provider receipt must echo that `plan_id`; Core reconstructs the canonical plan
and verifies the digest before it can report the receipt observation or
admission. The digest detects semantic plan mutation but grants no provider
authority and is not a provider attestation.

The receipt binds that exact plan, completion time, and a digest over the
complete receipt. Each admitted source has a direct non-file
reference, source family, evidence basis (`stated`,
`observed`, `tested`, or `inferred`), finding, limitation, relevant dates, and a
content digest. Raw provider content is never part of the Core projection.

The parent agent explicitly admits or rejects evidence. Rejection can retire;
admission remains retained until downstream readback covers every admitted
source reference. The admission id content-addresses the complete normalized
admission and downstream projection; retirement reconstructs and verifies that
identity before evaluating coverage.

## Ownership and TypeScript migration

This slice follows the TypeScript migration RFC without claiming a whole
control-plane promotion. TypeScript owns the pure typed decisions and is exposed
through the existing effect runtime. Python owns only CLI parsing, local JSON
input, and transport. The PR deletes no active connector path and creates no
second persisted authority.

Connector registry remains inventory and telemetry. `supported` never maps to
`ready=true`; explicit provider lifecycle observation may override the
inventory-only row for the same provider id.

## Product surfaces

- CLI: `external-evidence discover|plan|receipt|admit|retire`.
- Managed Turn: the same five effect-runtime methods.
- Frontend/Lark: not changed in this Core slice. A companion slice should render
  the same typed plan/admission projection and readback; it must not invent a
  second registry or lifecycle.

## Acceptance

- inventory-only connectors cannot be selected;
- discovery distinguishes empty, inventory-only, and ready inventories without
  claiming execution or evidence coverage;
- method and connector providers use one protocol and receipt contract;
- an observed provider receipt is bound to the exact plan without implying
  authenticated execution, evidence coverage, admission, or promotion;
- mutation of the request objective, decision, constraints, provider readiness,
  or execution envelope fails canonical `plan_id` verification;
- stale request/provider identity, file provenance, and unsupported evidence
  basis fail closed;
- admitted source refs are a subset of receipt sources;
- retirement rejects mutated disposition, source, or downstream projection
  fields whose complete admission identity no longer matches;
- retirement waits for downstream coverage of every admitted source;
- CLI and effect-runtime TypeScript tests pass from the source checkout.

## Non-goals

- a universal browser/search engine;
- provider credential storage;
- raw page or transcript persistence;
- automatic evidence admission;
- trading, publishing, or other downstream effect authority;
- treating registration or usage counters as proof of evidence quality.
