# RFC: Goal Direction Baseline (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal
- **Authors / owners:** LoopX maintainers and contributors
- **Created:** 2026-09-10
- **Last normative revision:** 2026-09-10
- **Implementation baseline:** `41a95d9`
- **Tracking issue:** [#2831](https://github.com/huangruiteng/loopx/issues/2831)
- **Related contracts:**
  [Agent Material Frontier](../../reference/protocols/agent-material-frontier-v0.md),
  [Goal Vision and Replan](../../reference/protocols/goal-vision-replan-contract-v0.md),
  and
  [Shared Goal Alignment and Governed Amendment](./shared-goal-alignment-and-governed-amendment-v0.md)
- **Language mirror:**
  [Chinese](./goal-direction-baseline-v0.zh-CN.md); a semantic difference
  between the two versions is a defect

## Document map and maintenance contract

Sections 1-10 are the durable design and acceptance contract. Section 11 is
the normative delivery plan. Section 12 contains decisions that must be made
before runtime delivery; recommendations there are not approval. The
appendices are non-normative execution, decision, evidence, and fixture-plan
records.

RFC maturity and delivery maturity are independent. This proposal does not
claim that `goal_direction_baseline_v0`, its declaration, or a runtime
consumer exists on `main`.

The separate [acceptance contract v0](../../reference/goal-acceptance-observations.md#owner-authorized-contract-v0)
does not implement this RFC's material declarations or usage receipts.
#2831's next slice is **direction-material revision to acceptance basis linkage**,
qualified by the same-Agent/current-revision fixtures below.

---

## 1. Decision summary

This RFC proposes a provider-neutral, read-only
`goal_direction_baseline_v0` projection. An opted-in Goal declares which
owner-controlled materials and topics define its direction. For one Agent,
the projection resolves those declarations through the existing canonical
Goal authority registry and verifies that the same Agent has a matching
`material_usage_receipt_v0` for every current material revision.

A material revision change produces an Agent-scoped direction
re-evaluation signal. It does not rewrite Agent Vision, create work, change
the shared route, or amend Goal intent. If re-evaluation actually changes an
Agent's route, the existing `goal_path_delta_v0` remains the only route-change
record. Missing authority, inaccessible material, unread material, stale
receipts, and conflicts fail closed.

Repository owner documentation is one possible material provider. It does not
become a write authority, and repository paths are not the protocol. Private
wikis, external document systems, or other registered providers may satisfy
the same contract without exposing raw content.

This RFC approves documentation and a synthetic fixture plan only. It does
not approve a new writer, scheduler, state authority, automatic Vision update,
or runtime gate.

## 2. Problem and motivation

LoopX can currently detect execution drift: stale Todo bases, exhausted
frontiers, open replan obligations, and lease conflicts. The existing Agent
Material Frontier can also prove whether an Agent read a registered material
revision. The two facts are not yet joined into an explicit answer to this
question:

> Is this Agent's current direction based on the revisions that the Goal owner
> currently designates as authoritative?

For example, a maintainer may revise an architecture contract while an Agent
continues from an older understanding. The Todo graph can remain valid and no
lease conflict need occur, yet the Agent's route may now be semantically
misaligned. Treating repository prose as an executable command would be worse:
the latest document writer would silently become Goal authority.

The smallest useful boundary is therefore an auditable read model. It binds a
Goal-owned declaration, current provider-neutral material metadata, and the
current Agent's own usage receipts. It exposes drift without deciding how the
Agent should respond.

### Invariants

1. Goal authority remains the single owner of material ids, topic bindings,
   boundaries, gates, freshness, conflicts, and revisions.
2. A provider resolves registered material; it does not grant Goal authority
   or write Agent Vision.
3. Only a receipt for the same Goal, Agent, material, and required revision can
   make that material current. A peer's receipt never transfers.
4. Missing, inaccessible, unread, stale, ambiguous, or conflicted inputs never
   project a current direction baseline.
5. Revision drift is Agent-scoped and observational. It creates no Todo,
   lease, scheduler wake, Goal amendment, or Vision rewrite by itself.
6. Actual route changes continue to use `goal_path_delta_v0`; canonical Goal
   changes continue to use their existing governed authority path.
7. Projections and fixtures contain no raw document body, prompt, reasoning,
   transcript, trajectory, credential, private URL, or local absolute path.
8. Goals that do not opt in preserve current behavior.

## 3. Scope and non-goals

### In scope

- a Goal-owned declaration of direction material ids and topics;
- deterministic resolution through `authority_registry.project_materials`
  and `authority_registry.topic_authority`;
- one Agent-scoped, read-only baseline projection;
- typed current, re-evaluation-required, and blocked outcomes;
- exact-revision matching against existing Agent material usage receipts;
- the relationship to Agent Vision, `goal_path_delta_v0`, and shared Goal
  amendment; and
- a public-safe synthetic drift fixture plan.

### Non-goals

- parsing document prose into Goal intent or executable instructions;
- making a repository, wiki, connector, or provider authoritative by itself;
- introducing a document writer, crawler, cache, scheduler, or second material
  registry;
- copying raw source bodies or provider locators into control-plane state;
- inferring that an Agent read a material from a chat, run log, evidence row,
  or another Agent's receipt;
- automatically patching Vision, creating Todos, rerouting work, or committing
  a Goal amendment; and
- replacing `agent_material_frontier_v0`, `goal_path_delta_v0`, or governed
  Goal amendment contracts.

## 4. Current-system contract

The implementation baseline already provides these relevant owners:

- `authority_registry.project_materials` owns canonical material metadata;
- `authority_registry.topic_authority` resolves topic names to material ids;
- `agent_material_frontier_v0` combines Goal authority, Agent/Todo/Vision/
  handoff requirements, boundaries, gates, and
  `material_usage_receipt_v0` rows into a read-only Agent projection;
- a matching receipt is Agent- and, when applicable, Todo-scoped; a handoff
  transfers bounded material references but never a predecessor's receipt;
- `goal_path_delta_v0` records an Agent's retained, changed, and stopped route
  facts when its actual execution path changes; and
- `shared_goal_alignment_v0` currently binds event-log or canonical Todo
  projection facts, not a typed canonical Goal-intent revision.

No current type declares which authority materials define Goal direction. No
current reducer binds such a set to one Agent's current usage receipts. This
RFC does not relabel shared-alignment source digests or Markdown state as that
missing semantic identity.

## 5. Proposed architecture

### 5.1 Ownership and authority

An opted-in Goal adds `authority_registry.goal_direction_requirements`. This
is a logical field of the existing Goal-owned authority registry, not a new
registry. It is written only through the authority owner's existing governed
write boundary. A provider cannot add itself to the declaration.

Each declaration row has:

- `requirement_id`: stable within the Goal;
- exactly one of `material_id` or `topic`;
- `purpose`: bounded, public-safe context for why the source governs direction;
  and
- `required`: boolean, defaulting to `true`.

A direct `material_id` resolves against `project_materials`. A `topic` resolves
through `topic_authority`, then every referenced id resolves against
`project_materials`. Resolution uses a deterministic union ordered by
`material_id`; duplicate ids retain all `bound_by` requirement refs. Missing
or malformed registries, missing ids, empty topic mappings, and contradictory
bindings fail closed. A compact authority summary is never sufficient.

Material metadata remains owned by the existing registry. The baseline reads
only these public-safe fields:

- stable `material_id` and resolved topics;
- `role`, `owner_status`, and `conflict_rule`;
- `boundary` and `gate_status`;
- `freshness`; and
- opaque `revision`.

The revision is a provider-neutral identity, not a sortable counter. A
provider may derive it from a commit, ETag, version id, or content digest, but
the provider-specific form stays outside core semantics. A direction material
without a non-empty canonical revision cannot become current.

### 5.2 Read model

The proposed projection is Agent-scoped:

```json
{
  "schema_version": "goal_direction_baseline_v0",
  "goal_id": "example-goal",
  "agent_id": "agent-reviewer",
  "generated_at": "2026-09-10T00:00:00Z",
  "baseline_digest": "sha256:<canonical-resolution-digest>",
  "direction_state": "re_evaluation_required",
  "reason_codes": ["material_revision_changed"],
  "summary": {
    "required_count": 2,
    "current_count": 1,
    "stale_count": 1,
    "blocked_count": 0
  },
  "items": [
    {
      "material_id": "architecture-owner-contract",
      "bound_by": ["direction:architecture"],
      "required_revision": "rev-7",
      "observed_revision": "rev-6",
      "state": "stale",
      "receipt_ref": "material_receipt:receipt-17"
    }
  ],
  "advisory": {
    "kind": "agent_vision_re_evaluation",
    "creates_work": false,
    "rewrites_vision": false,
    "changes_goal_route": false
  },
  "truth_contract": {
    "authority_is_goal_owned": true,
    "projection_is_read_only": true,
    "receipt_is_agent_scoped": true,
    "provider_is_not_write_authority": true,
    "raw_source_body_recorded": false
  }
}
```

`baseline_digest` is a deterministic SHA-256 digest over the schema version,
Goal id, ordered requirement bindings, and ordered authority-owned metadata
used by the projection. It excludes `agent_id`, receipts, timestamps, source
bodies, and provider locators. Two Agents reading the same Goal authority can
therefore share the same baseline identity while retaining independent receipt
state. The digest proves basis equality, not semantic correctness or content
consumption.

Each item reuses the Agent Material Frontier state vocabulary:

1. absent declaration target or absent revision: `missing`;
2. disallowed boundary, blocked gate, or unavailable receipt: `inaccessible`;
3. no matching receipt for this Agent: `required_unread`;
4. stale authority freshness, conflicted ownership, or revision mismatch:
   `stale`; and
5. accessible, conflict-free authority plus an exact-revision matching receipt:
   `current`.

The projection may include compact `role`, `owner_status`, `conflict_rule`,
`boundary`, `gate_status`, and `freshness` values when public-safe. It never
includes a raw locator, body, excerpt, diff, or expanded receipt event.

### 5.3 Aggregate drift signal

`direction_state` is derived by precedence:

1. `blocked` if any required item is `missing`, `inaccessible`,
   `required_unread`, freshness-stale, ambiguous, or conflicted;
2. `re_evaluation_required` if no item is blocked and at least one receipt's
   observed revision differs from the current authority revision; and
3. `current` only when every required item is `current`.

Optional requirements may be reported but cannot make a required baseline
current. Unknown states and unknown conflict policies fail closed as `blocked`.
`reason_codes` are typed tokens such as `authority_registry_missing`,
`direction_requirement_unresolved`, `authority_revision_missing`,
`material_inaccessible`, `material_required_unread`,
`material_revision_changed`, and `authority_conflict`. Consumers must not
parse prose to decide drift.

A revision mismatch is the only v0 condition that emits the advisory
`agent_vision_re_evaluation` kind. The signal belongs to the selected
`agent_id`; it does not imply that any peer is stale. A later exact-revision
receipt from that Agent allows a fresh projection to become current. Receipt
replay remains governed by the existing receipt identity and cannot be copied
between Agents.

### 5.4 Relationship to Vision and route changes

The baseline is an input to review, not a Vision writer. A future consumer may
surface the advisory signal as an Agent-scoped Vision acceptance gap, but it
must not synthesize a patch or mark the gap resolved from scheduler activity.
The Agent either records an existing compliant Vision checkpoint or writes a
bounded Vision patch through the current Vision write boundary.

Reading a changed revision and retaining the same route does not require a
path delta. If the review changes the route, the write must include the
existing `goal_path_delta_v0` with its prior assumption, observed reality,
retained/changed/stopped facts, and public-safe evidence refs. The direction
baseline neither embeds nor replaces that record.

If the material reveals that canonical shared Goal intent itself must change,
the Agent may submit the existing governed Goal-amendment proposal. The
baseline cannot approve or commit it.

### 5.5 Provider contract

Core consumes canonical authority metadata and receipts, not provider APIs.
A provider profile may explain how it obtains a stable revision and evaluates
availability, but it must return the same logical fields and respect the same
boundary and gate decisions.

Repository documentation is therefore one profile:

- a repository owner registers a stable material id and topic mapping;
- the profile resolves a commit/blob-derived opaque revision;
- access is evaluated before a receipt is accepted; and
- the projection stores neither the checkout path nor document body.

The same model applies to a private wiki or external document service. Provider
failure cannot fall back to an unrelated local file, Markdown status, chat
text, or cached prose and still report `current`.

## 6. Alternatives and design choices

### Parse repository owner docs directly

Rejected. File paths and Markdown conventions would become accidental
authority, private layouts would leak into core, and non-repository providers
would need special cases.

### Store a copy of direction prose in Agent Vision

Rejected. Copies drift independently, expand the hot path, and make it unclear
whether the owner material or the latest Agent writer is authoritative.

### Share one receipt across all Agents

Rejected. One Agent's read cannot prove another Agent consumed the same
revision, and handoff would silently transfer understanding and permissions.

### Automatically rewrite Vision when a revision changes

Rejected. Revision identity proves change, not its semantic consequence.
Automatic rewriting would turn an observational contract into a planner and
write authority.

### Reuse shared-alignment source digests as direction identity

Rejected. Those digests currently identify event-log or Todo projection facts,
not the Goal's owner-designated semantic materials.

## 7. Safety, privacy, and compatibility

- The feature is opt-in per Goal. No declaration means no baseline projection
  and preserves existing behavior.
- The builder is pure and read-only. It grants no permission, material access,
  claim, lease, scheduler budget, or amendment authority.
- Boundary and gate checks occur before receipt currency is accepted. Missing
  boundary information is inaccessible, not public.
- Only public-safe ids, metadata, digests, typed states, and compact receipt
  refs may be emitted. Source bodies, locators, local paths, credentials,
  prompts, reasoning, transcripts, trajectories, and raw run logs are excluded.
- Mixed-version readers must ignore the absent optional projection. Writers
  must not create or mutate baseline state because an older reader omitted it.
- Unknown schema versions, states, conflict rules, or malformed receipt fields
  fail closed and must never be coerced to `current`.

## 8. Migration and rollback

M0 changes documentation only and requires no migration or rollback beyond
reverting the documents.

A future implementation must remain default-off and introduce the declaration
through the existing Goal authority owner. Admission must validate every
requirement against a full canonical registry read before enabling the
projection. Rollback disables the reader and declaration authoring together;
it must not delete registry materials or receipts. Because the projection owns
no canonical state, disabling it cannot roll back or rewrite Agent Vision,
Todos, leases, or Goal intent.

No implementation milestone may promote until mixed-version behavior and
provider failure are proven fail-closed without changing the default scheduler
or quota path.

## 9. Validation and acceptance

| Claim | Test or evidence | Required result | Boundary / exclusions |
| --- | --- | --- | --- |
| The proposal is public-safe and internally consistent. | `loopx check --scan-path docs/architecture/rfcs --scan-path docs/development/contributor-tasks.md` | Pass with no private-data or contract finding. | Does not prove runtime behavior. |
| Exact current revisions are Agent-scoped. | Synthetic cases F1 and F3 in Appendix D. | Only the selected Agent's matching receipt yields `current`. | No cross-Agent receipt transfer. |
| Revision changes expose drift without mutation. | Synthetic case F2. | `re_evaluation_required`; inputs, Vision, Todos, leases, and Goal route are byte-identical. | No automatic semantic interpretation. |
| Missing and inaccessible authority fail closed. | Synthetic cases F4 and F5. | `blocked` with typed reason codes and no mutation. | Provider availability is simulated. |
| Aggregate state is deterministic. | Synthetic cases F6 and F7. | One stale required item dominates current items; unchanged input is stable. | Performance is not qualified. |
| Route changes retain their existing owner. | Synthetic case F8 plus Goal Vision contract tests in a future implementation. | No `goal_path_delta_v0` for unchanged route; an actual route change is accepted only through that contract. | Does not approve the route change itself. |
| No raw source material is projected. | Fixture key allowlist and public-boundary scan. | No body, locator, URL, local path, prompt, reasoning, transcript, trajectory, credential, or run-log field. | Opaque ids and digests are allowed. |

Unimplemented or skipped rows are not green. Live provider qualification and
production promotion are outside this RFC's current delivery maturity.

## 10. Operational contract

M0 has no runtime operational surface. A future implementation may expose only
the compact `direction_state`, typed reason codes, counts, baseline digest, and
bounded item refs. Provider errors must be observable as typed blocked reasons,
not swallowed or expanded into source content. Operators may retry a read or
repair canonical authority; they may not override drift by editing the
projection.

The read model has bounded size. A future schema must define maximum
requirements and emitted items before implementation. Overflow fails closed
with a typed capacity reason rather than silently truncating required
materials.

## 11. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | Bilingual public design note and synthetic fixture plan only. | Maintainer-approved helper scope on #2831. | Documentation check and review of F1-F8. | Revert docs. |
| M1 | Optional pure builder over full Goal authority plus receipts; no consumer or writer. | Approval of Section 12 decisions and schema limits. | Deterministic fixtures, mutation checks, Python/TypeScript parity if both runtimes consume it, and public-boundary scan. | Remove the default-off builder and projection. |
| M2 | One read-only Agent-scoped Vision-gap consumer. | M1 conformance plus explicit maintainer approval of the consumer and recovery UX. | End-to-end proof that drift is surfaced, same-revision replay is quiet, and no Vision/Todo/route mutation occurs. | Disable the consumer; retain canonical registry and receipts. |

Later milestones are not authorized by merging M0.

## 12. Open decisions

1. **Schema capacity.** Goal authority owners must choose maximum declaration
   rows and projected items before M1. Recommendation: use the existing bounded
   material-frontier limits where possible. Evidence needed: production-scale
   synthetic registry fixtures.
2. **Required receipt outcome.** Goal Vision owners must decide whether `read`,
   `used`, and `verified` all satisfy a direction requirement or whether v0
   should require `used`/`verified`. Recommendation: preserve current Material
   Frontier semantics for M1 and tighten only with migration evidence.
3. **First consumer.** Control-plane owners must approve whether M2 surfaces in
   Goal Frontier, status, or another existing Agent-scoped view. Recommendation:
   choose one cold-path read surface and avoid quota/scheduler promotion.

---

## Appendix A: Execution ledger (non-normative)

### 2026-09-10 — M0 proposal

- **Baseline:** `41a95d9`
- **Delivered:** bilingual RFC plus synthetic fixture plan
- **Evidence:** documentation and public-boundary checks are pending PR
  validation
- **Known gaps:** no schema implementation, writer, consumer, or live provider
  qualification
- **Effect on normative design:** initial proposal

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-09-10 | Accept M0 as a documentation-only helper scope; repository docs are one provider and revision drift stays advisory. | Maintainer scope on [#2831](https://github.com/huangruiteng/loopx/issues/2831#issuecomment-5224703471) | Runtime implementation or repository-specific writer | Initial Sections 1-12 |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | Current Material Frontier semantics were audited. | `41a95d9` | [Agent Material Frontier](../../reference/protocols/agent-material-frontier-v0.md) | documented | Public contract only; no live materials. |
| E2 | Route-change ownership remains existing. | `41a95d9` | [Goal Vision and Replan](../../reference/protocols/goal-vision-replan-contract-v0.md) | documented | Does not prove the proposed baseline. |
| E3 | M0 repository docs remain public-safe. | PR validation | `loopx check --scan-path docs/architecture/rfcs --scan-path docs/development/contributor-tasks.md` | pending | Documentation scope only. |

## Appendix D: Synthetic drift fixture plan

All fixtures use invented ids and revisions. The harness deep-copies inputs,
runs the pure builder, and asserts byte-for-byte input immutability plus the
absence of any Vision, Todo, lease, scheduler, Goal-amendment, or route-write
effect.

| Case | Input variation | Expected projection | Independent negative assertion |
| --- | --- | --- | --- |
| F1 exact Agent receipt | Two required materials; selected Agent has exact-revision receipts. | `current`; stable digest; both items current. | A receipt with a different Goal, Agent, material, or revision does not satisfy either item. |
| F2 revision drift | One authority revision changes after the Agent's receipt. | `re_evaluation_required`; `material_revision_changed`; changed item stale. | No Vision patch, Todo, wake, lease, amendment, or path delta is emitted. |
| F3 peer receipt | Only another Agent has the new-revision receipt. | Selected Agent item is `required_unread` or remains stale from its own older receipt; aggregate is blocked or re-evaluation-required by the precedence rules. | The peer receipt ref is absent from the projection. |
| F4 missing authority | Full `project_materials` is absent, a declared id is absent, or its revision is empty. | `blocked` with the precise missing reason. | A compact count/summary cannot be treated as an empty or current registry. |
| F5 inaccessible material | Boundary is unavailable or gate is blocked. | `blocked`; item inaccessible. | A matching revision receipt cannot override boundary or gate state. |
| F6 mixed required set | One item current and one exact revision-mismatch item. | `re_evaluation_required`; counts are deterministic. | Current items cannot mask revision drift in required material. |
| F7 replay stability | Same canonical inputs and same receipts are reordered and projected twice. | Identical normalized items, digest, state, and reason codes apart from explicit `generated_at`. | Receipt order and declaration duplication cannot create churn. |
| F8 route ownership | Agent reads the changed revision, then either retains or changes its route. | Fresh receipt returns the baseline to current. | Retained route emits no path delta; a changed route is accepted only with a valid existing `goal_path_delta_v0`. |

Before M1, turn this table into a checked-in public-safe fixture and mutation
test. The mutation arm must deliberately relax Agent or revision matching and
prove that F2 or F3 fails, so the test cannot pass merely because the happy
path was never exercised.

## Appendix E: Rejected or superseded alternatives

Section 6 records the currently rejected designs. Evidence that a provider-
specific owner can preserve the same authority, privacy, and no-mutation
invariants may justify a profile, but not a fork of the core contract.
