# RFC: <Decision or Capability Name> (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none | <links to the RFCs this one replaces or closes>
- **Superseded by:** <link> (only when the status is Superseded)
- **Delivery maturity:** Proposal | Experiment | Partial | Implemented | Promoted
- **Authors / owners:** <public identities or roles>
- **Created:** YYYY-MM-DD
- **Last normative revision:** YYYY-MM-DD
- **Implementation baseline:** `<commit>` or not applicable
- **Related contracts:** <links>
- **Language mirror:** [中文版](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/<same-basename>.zh-CN.md)

## Document map and maintenance contract

State which sections are normative, which are current implementation facts,
and which are historical evidence. Use this default. Every new RFC must ship an
English document and a `<same-basename>.zh-CN.md` semantic mirror; a language
link is required in both documents. Keep the two versions synchronized when
normative sections change:

- Sections 1-10 are the durable design and acceptance contract.
- Section 11 is the normative delivery plan.
- Section 12 contains unresolved decisions; proposed answers are not approval.
- Appendices contain the non-normative execution ledger, decision log, evidence
  registry, rejected alternatives, and incident lessons.

Merge accepts an active RFC as a qualified, claimable design basis. Do not keep
Draft or Under review as merged lifecycle states. Design acceptance and delivery
maturity are independent: implementation, live qualification, default changes
and promotion still require their own evidence and authorization. Historical
dispositions are Superseded, Retired and Rejected. Dated progress entries do
not amend normative sections. Dated checkpoints live in
[`ledger/<rfc-slug>/YYYY-MM-DD-slug.md`](ledger/README.md), never as a
dated execution entry above the first appendix; the execution-ledger appendix only
points at that directory. Lifecycle state is read from the header above into
the generated [`STATUS.md`](STATUS.md): the value must be one of the
lifecycle states, optionally followed by punctuation and a note, `Supersedes / closes` is mandatory (`none` is an explicit
answer), and a `Superseded` RFC must link its successor that reciprocally names
its predecessor. Normative checkpoint persistence/recovery headings and
historical appendix records are legal.

---

## 1. Decision summary

Lead with the smallest set of decisions a maintainer must understand. State:

1. what becomes authoritative or changes behavior;
2. what remains unchanged;
3. the default and opt-in boundary;
4. the principal safety or compatibility constraint;
5. what this RFC still does not approve.

## 2. Problem and motivation

Describe the user/operator failure, not only the implementation gap. Include a
concrete example and explain why the current owner cannot solve it locally.

### Invariants

List properties that every implementation must preserve. Prefer observable
semantics over mechanism names.

## 3. Scope and non-goals

### In scope

- <behavior, owner, or contract>

### Non-goals

- <nearby system intentionally unchanged>

## 4. Current-system contract

Record the audited current behavior and its owners. Distinguish facts on the
named implementation baseline from proposed behavior. Link stable protocol or
code ownership surfaces; do not paste execution logs into this section.

## 5. Proposed architecture

### Ownership and authority

Name the single decision owner, storage/provider boundary, identities,
transactions, and forbidden alternate authorities.

### State model and schema

Define canonical records, version manifests, required/optional fields, explicit
clear/delete semantics, ordering, and serialization. Default to preserving
legally stored fields. Any reduction must enumerate affected fields, producer /
reader / writer research, historical and external compatibility, migration,
rollback, and semantic-equivalence evidence, with explicit maintainer approval.

### Command or event lifecycle

Describe legal transitions, idempotency identity, preconditions, receipts,
replay, ambiguity reconciliation, and fail-closed behavior.

### Provider or extension contract

Keep logical semantics provider-neutral. Put provider-specific storage layouts,
limits, authentication, and operational details in named profiles.

## 6. Alternatives and design choices

Compare viable alternatives against the invariants. Keep the final choice and
its trade-off in the normative body; retain superseded detail in Appendix D.

## 7. Safety, privacy, and compatibility

Cover as applicable:

- default-off and feature-off parity;
- authorization, tenancy, and credential boundaries;
- public/private data boundaries;
- legacy readers/writers and downgrade behavior;
- partial rollout, mixed versions, and split-brain prevention;
- capacity, availability, and fail-closed/fail-open choices.

## 8. Migration and rollback

Define admission, preflight, quiescence, cutover, readback, rollback, and the
point after which rollback requires export or migration. Every destructive or
irreversible step needs an explicit gate and recovery path.

## 9. Validation and acceptance

Express each claim as a reproducible acceptance row:

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| <claim> | <command, test, or artifact> | <typed verdict> | <what it does not prove> |

Separate deterministic conformance, live qualification, performance evidence,
and production promotion. An unverified or skipped row is not green.

## 10. Operational contract

Describe observability, typed failures, capacity limits, backup/recovery,
upgrade/downgrade, on-call or operator actions, and user-visible status. Omit
this section only when the RFC cannot affect a running system, and say why.

## 11. Normative delivery plan

Use cohesive milestones with explicit entry and exit gates. An Accepted RFC
may still have unimplemented or unqualified milestones.

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | <smallest useful slice> | <precondition> | <acceptance rows> | <path> |

Keep progress percentages and dated status reports out of this section.

## 12. Open decisions

Number each unresolved decision. For each, name the decision owner, options,
recommendation, evidence needed, and deadline or dependent milestone. A
recommendation remains non-authoritative until the decision log records
approval.

---

## Appendix A: Execution ledger (non-normative)

Dated entries live in [`ledger/<rfc-slug>/`](ledger/README.md), one file per
measured slice named `YYYY-MM-DD-slug.md` with a `.zh-CN.md` mirror; this
appendix only links the directory. Do not rewrite history to resemble the
current plan. Each entry states the exact implementation baseline and claim
boundary:

- **Baseline:** `<commit>` / PR
- **Delivered:** <observable behavior>
- **Evidence:** <tests or artifacts>
- **Known gaps:** <unmet rows>
- **Effect on normative design:** none | <linked decision/change>

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| YYYY-MM-DD | <decision> | <public approval link or role> | <summary> | <sections> |

Do not infer approval from implementation progress, silence, or a proposed
answer. Schema/field removal entries name every removed field explicitly.

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | <claim> | <versioned facts> | <public-safe locator> | pass/fail/unverified | <boundary> |

Never commit credentials, private links, raw transcripts, local paths, or
unredacted production evidence.

## Appendix D: Rejected or superseded alternatives

Preserve enough detail to prevent the same dead end from being rediscovered.
State why it failed an invariant and what evidence could reopen the decision.

## Appendix E: Incident and review lessons

Record generalized, public-safe lessons that changed an invariant, acceptance
row, or migration rule. Operational timelines and private incident material
belong outside the public RFC.
