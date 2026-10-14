# RFC: Goal Direction Baseline (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Partial (M1 and explicit local M2 candidates; entry and general activation unqualified)
- **Authors / owners:** LoopX maintainers and contributors
- **Created:** 2026-09-10
- **Last normative revision:** 2026-10-13
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

RFC maturity and delivery maturity are independent. The candidate M1 TypeScript builder
implements `goal_direction_baseline_v0` over supplied canonical inputs. The explicit local candidate in §12.5 adds source-backed read offers, same-Agent
acknowledgement and cold operator/Agent readback. It does not qualify general
runtime activation or live model consumption; schema validity alone remains
insufficient. M1 entry qualification remains pending owner approval of the
Section 12 capacity and receipt decisions; M2 consumer/recovery UX approval
is a separate pending gate. Implementation and tests do not grant either approval.

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

M1 is a pure read model only. It introduces no writer, scheduler, state
authority, automatic Vision update or runtime gate. M2 retains its separate
consumer and recovery qualification.

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

The M1 builder now binds declared direction materials to one Agent's supplied
receipts. The §12.5 local source profile implements declaration authoring and read/ack
provenance; other producer profiles and general activation remain M2 dependencies. Shared-alignment source digests and Markdown state are not
substitutes for that provenance.

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

The M1 candidate is implemented in
[`direction_baseline.ts`](../../../loopx/control_plane/goals/direction_baseline.ts).
The existing [F2 smoke](../../../examples/goal-direction-baseline-f2-smoke.py)
now exercises the actual TypeScript tests and two source mutations; its old
fixture-local reducer is retired. The explicit local M2 candidate below calls it through a cold Goal operation.
Polling, scheduling and quota paths still do not call this builder.

## 12. Open decisions

The following recommendations are **pending owner decisions**, not runtime
authorization. They refine the existing three decisions using the source audit
and synthetic measurements in Appendix F. Implementation evidence below does
not approve these recommendations.

### 12.1 Schema capacity — Goal authority owner

The current Material Frontier bounds individual text and handoff refs, **not
the full material or receipt count**. There is no existing numeric full-frontier
limit to inherit. Proposed M1 envelope for one selected Goal/Agent:

| Dimension | Proposed maximum | Reason / required qualification |
| --- | --- | --- |
| Declaration rows | 64 | Bounds owner-authored requirements; topic expansion remains independently bounded. |
| Distinct resolved materials, including optional items | 256 | Includes all declared topic members and direct refs, with every binding retained. |
| Supplied receipt rows | 4,096 | Bounds history inspection; do not choose an arbitrary recent suffix that hides a relevant receipt. |
| Complete input, compact UTF-8 JSON bytes | 2 MiB | Includes the full supplied authority registry, declarations and receipts; count limits alone do not bound strings or topic fan-out. |
| Complete projection, compact UTF-8 JSON bytes | 256 KiB | Includes all bindings and reason codes; count limits do not guarantee output fits. |

These limits apply independently; their maxima need not fit simultaneously.
Compact JSON means no insignificant whitespace and literal UTF-8 strings.
Field validation remains necessary. Over-limit input or output must return a
typed capacity refusal, with no usable partial baseline or `current` result.
Do not truncate materials, bindings or receipts, substitute a compact authority
summary, or increase a limit silently. The existing authority owner must supply
a complete bounded read or refuse; adding an index is a separate, evidenced
optimization, not permission to discard history.

M1 entry approval would authorize these as candidate implementation limits.
Before M1 exit, qualify exact-limit and one-over cases, maximum field widths,
overlapping topics, duplicate bindings, invalid inputs and combined bounds
against the TypeScript builder. Appendix F motivates this starting envelope;
it does not establish production scale, an SLO or a validated maximum. A
production capacity freeze still requires its own qualification.

### 12.2 Receipt outcomes and provenance — Goal Vision owner

Recommend preserving `read`, `used` and `verified` as eligible outcomes for an
exact-revision receipt. `unavailable` remains inaccessible. None of these
outcomes proves agreement, semantic understanding, accepted work or write
authority. Tightening to `used`/`verified` requires migration evidence.

The M1 decision must also pin selection and rejection behavior:

- validate schema, identity, outcome, revision and timestamp before selection;
  malformed or unknown input must not disappear behind an older passing row;
- consider only the selected Goal, Agent and applicable Todo scope; a peer's
  valid receipt cannot satisfy or be copied into the selected Agent's result;
- select the latest eligible observation by timestamp; deduplicate identical
  replay ids, and use receipt-id order only when tied observations agree on
  scope, outcome and revision. Conflicting duplicate ids or equally recent
  contradictory observations must refuse instead of choosing a passing row;
- a later `unavailable` observation must not fall back to an earlier `read`;
  a revision is opaque identity, never a sortable version number; and
- preserve replay identity and input immutability, including reordered input.

At the audited baseline there is a receipt reader and synthetic fixtures, but
**no production `material_usage_receipt_v0` writer**. A schema-valid caller row
alone does not prove a real source read. M1 may use synthetic receipts; M2
requires a separately qualified producer through the existing material-read
owner, binding an actual authorized read to the exact material revision and
selected Agent. Reuse the existing Goal-instance identity boundary when
binding its storage/readback: a reused `goal_id` or restored archive alone
cannot establish receipt lifetime. Unprovable legacy provenance stays
unqualified. Do not invent a second receipt store or silently extend the v0
schema in the pure builder.

### 12.3 First consumer and recovery — control-plane owner

Recommend the existing **agent-management cold read** reached through explicit
Goal/Agent-scoped status, with the same projection in the packaged Goal detail
interaction. The current management layer only consumes prebuilt
`status.agent_material_frontiers` behind the `material_lifecycle` envelope;
it does not load full authority, produce receipts or render direction details.
Those are named M2 dependencies, not delivered behavior.

Keep direction loading opt-in and outside ordinary status polling, quota and
scheduler admission. Reuse Goal/Agent identity and the existing capability
owner; do not add a separate capability just for the projection. The existing
`agent-context` command supplies coordinator lifecycle guidance and is not a
general material-read consumer. Peer discovery is not that consumer either.

The proposed M2 journey is: select Goal and Agent → inspect current material
basis and missing/stale reasons → perform an already-authorized source read
through its owner → refresh the same Agent's result. Packaged validation must
include revision drift, unavailable source, wrong-Agent receipt, reload and
retry, with unchanged Vision/Todo/lease/route state. A current baseline means
only that its material-read basis is current; it must not make whole-Goal
acceptance green. Direction-to-acceptance linkage must bind the observed basis
through the existing acceptance owner rather than rewrite historical results.
CLI and frontend must share that result; Lark delivery remains separately
unqualified until its actual interaction is tested.

Maintainers can approve or adjust the capacity and receipt decisions for the
TypeScript-only M1 pure builder while explicitly deferring M2. No consumer,
writer or recovery UX is approved by that narrower decision. Section 11 still
requires explicit M2 approval and real producer/consumer qualification.

### 12.4 Candidate M1 implementation profile

The pure builder implements the proposed §12.1 envelope and §12.2 receipt
rules. Source/tests do not satisfy the §11 entry gate: capacity, receipt
eligibility and the bounded metadata profile still need their named owners'
approval. Overflow returns `blocked` / `direction_capacity_exceeded`, empty
items and a null digest; counts do not describe partial authority. Text bounds
are 180 Unicode characters for ids/revisions, 120 for topics, 220 for purposes
and 80 for available boundaries. Invalid caller identity/time is a typed input
error. Receipt time ordering retains nanosecond precision; agreeing ties use
the smallest receipt id.

The candidate does not infer authority from prose: only
`owner_status=reviewed`, `conflict_rule=owner_wins`, `freshness=current` and
`gate_status=ready|registered|readable` qualify. Missing/unknown metadata or
boundary blocks; public/public_safe or explicitly available boundaries permit
this read without granting access. Material role is informational. Complete
`project_materials` and `topic_authority` are required; map/list identities and
duplicate declarations must agree. Optional items stay visible, but only
required items contribute to aggregate state/reasons. Malformed full input
refuses even for an optional item or peer. The §12.3 consumer/receipt-provenance
requirements remain separate; this builder adds no receipt store or v0 fields.

### 12.5 Explicit local M2 candidate and review boundary

This candidate makes the §12.3 consumer/recovery proposal concrete for maintainer
review under the existing §11 M2 entry gate. Implemented source and isolated
validation do not approve activation on a live Goal. Its production entrypoints
are `goal-direction` and the existing Goal settings disclosure. TypeScript
`goals/direction_authority.ts` owns decisions and canonical writes; the Python
`direction_runtime` adapter owns source observation, locking and bounded file IO.
This is Goal authority, not material-catalog migration/reranking policy, and it
creates no new capability or store. The existing local Decision Context provider
and this transport share the same descriptor-bounded file-snapshot helper.

The first profile requires an active exact `source_session_v1` Goal, registered
peer ids and an existing File/SQLite canonical authority. Fresh creation uses
`project register --canonical-storage file|sqlite`; it is not migration of an
existing Goal. Existing registry-side material authority must be explicitly
migrated by its owner before this profile is used; the candidate refuses a
second authority. Under this profile `authority_registry` is the same logical
Goal authority object in the canonical Goal head, with an exact-lifetime scope.
Other Goal state, requirements, work, leases and acceptance remain unchanged.

The typed owner checks the live lifetime, unique authority and provider basis
before the operator configuration reads source bytes as well. A compact legacy
authority summary is not treated as empty; migration is required. Disabling the
reader remains available to the live owner without granting new source access.

The operator declares a complete list of local Markdown sources, each with
stable material id, purpose, topic refs, required/optional flag and an explicit
public/public-safe/local-private boundary. The source path is transient input,
resolved only inside the existing Goal repo/state/runtime roots. Relative paths
are pinned to the repository; a missing file cannot fall through to a same-named
state/runtime copy. Other authorized roots require an explicit absolute path. Its raw locator
and body never enter the authority or receipt journal. A pending operator form
retains its private path only in that tab's local recovery state until resolved. Actual bounded bytes
supply the content revision; only the locator digest is retained. Declaration
and apply bind a provider revision; the UI also pins the reviewed authority
digest, so editing the file between preview/apply refuses. Unknown/private
remote providers are unsupported. This is explicit owner-declared revision
tracking, not a filesystem watcher: updating a source file requires the owner
to review/update its declared revision, and a read against mismatching actual
bytes records unavailability rather than a passing old receipt.

Usage has two distinct steps:

1. **Read.** The typed owner checks exact instance, registered Agent, current
   declaration, source digest, revision and boundary before IO. Actual bounded
   source bytes are returned transiently with an original-operation offer and
   delivery digest. The offer is durable but creates no usage receipt: a lost
   response cannot mark an Agent current. Retry the same read operation to
   recover its offer only while the same revision can still be read.
2. **Acknowledge.** That Agent explicitly acknowledges the exact offer/digest.
   The existing canonical transaction adds one `material_usage_receipt_v0`
   with `outcome=read`, inside an instance-bound provenance record. It cannot
   accept an arbitrary supplied receipt, reuse another Agent's offer, or
   acknowledge after a later availability observation invalidated the offer.
   This proves a source-backed read plus declared acknowledgement, not human
   or model understanding, actual route adoption, `used`, or `verified`.

A later read failure records `unavailable` through the same owner and blocks
an earlier passing receipt. The producer keeps `recorded_at` strictly increasing
in canonical append order even if the host clock repeats or moves backward;
this is ordering evidence, not an independent wall-clock attestation. Preview
acknowledgements explicitly keep `receipt_recorded=false` and write no record. Repair the source and perform a new read/ack;
never edit a receipt to make the projection current. Recreated/retired source
lifetimes fence new operations including a delayed RPC after caller timeout.
Original operation receipts remain historical; replay grants no current access
or execution. A same-Agent exact-revision receipt permits a current projection;
a peer's receipt is nontransferable. Combined authority/receipt input is bounded
before commit; no partial projection is persisted to fit the budget.

At 4,096 retained usage records, a new observation may replace one older
record for the **same Agent/material only**. The displaced complete provenance
record is retained as `archived_usage_record` in the same atomic operation
receipt/journal; `receipt` with that operation id reads it back. Original
operation receipts remain unchanged. This bounds the current projection, not
the store's journal history or its separate provider capacity. A full store with
no record for this pair refuses the new observation; it cannot evict another
pair, and the unread pair remains blocked. New offers bind the complete retained
same-pair usage digest, so an unavailable observation invalidates old offers even
when the row count is unchanged; unrelated pairs remain valid. A monotonic scope
`usage_generation` fences pre-upgrade offers that only carried a row count after
the first replacement. A new read and acknowledgement recover normally without
raising the limit. Keep compatible readers after such records exist; reverting
to a reader that cannot interpret the generation is not a supported rollback.


The existing settings path **Goal settings → Task ownership → Direction
materials and revisions** lets the owner review/apply declarations, inspect
registered Agents independently, recover unknown replies and disable the reader.
The browser cannot read/ack as a peer or submit usage receipts. Agent CLI uses
these operations, with writes explicitly enabled:

```sh
loopx --registry REGISTRY --format json goal-direction \
  --goal-id GOAL --goal-instance-id INSTANCE --agent-id AGENT --operation inspect
loopx --registry REGISTRY --format json goal-direction \
  --goal-id GOAL --goal-instance-id INSTANCE --agent-id AGENT \
  --operation read --request-json read.json --execute
loopx --registry REGISTRY --format json goal-direction \
  --goal-id GOAL --goal-instance-id INSTANCE --agent-id AGENT \
  --operation acknowledge --request-json acknowledgement.json --execute
```

Synthetic packaged-interface examples: [review declaration](../../assets/goal-direction/declaration-preview.png),
[revision drift](../../assets/goal-direction/revision-drift.png), and
[narrow Agent readback](../../assets/goal-direction/peer-readback.png).
These show operator/CLI qualification, not live model understanding.

The Agent must already have the authorized local path from its task/provider
context; this profile does not resolve a private locator from material id alone.
That explicit-read boundary is separate from autonomous source discovery/handoff.
`read.json` contains `operation_id`, `material_id`, the transient `source_path`,
`required_revision` and `authority_digest` from inspect. The acknowledgement
contains its own `operation_id`, the returned `offer.read_operation_id` and
`delivery_digest`. To configure through CLI, omit Agent id and supply `materials`
plus `expected_provider_revision` and `operation_id`; preview is the default.
Each material contains `material_id`, `source_path`, `purpose`, `topics`,
`required` and `boundary`. Include the preview's `expected_authority_digest`
when applying a reviewed declaration. `receipt` takes `receipt_operation_id`
and the original actor/lifetime. `disable` is an operator operation with current
provider revision and its own operation id; it retains declarations and receipts.

Cold inspect returns the M1 projection and a typed Agent-scoped `vision_gap`.
Its `acceptance_basis` links the material baseline to the actually observed
canonical acceptance contract revision/digest via its existing typed reader,
explicitly `not_assessed`. An absent, disabled or non-instance-bound contract
projects no linked revision. Configuring/verifying acceptance through the generic
source-session CLI remains outside this candidate; no such writer gate is waived.
It never marks whole-Goal acceptance green or writes a Vision/checkpoint,
Todo, route, lease, scheduler or quota decision. Actual route changes retain
`goal_path_delta_v0`; generic runtime and Lark activation are still unqualified.
The local operator/registered-actor transport is the existing trust boundary,
not a new authentication or independent-worker-attestation scheme.

The candidate tests use real native registration, File/SQLite storage, local
files and HTTP, plus the packaged settings interaction and separate Agent CLI.
They qualify revision/peer/lifetime isolation, source-first IO, unknown response,
replay, unavailable-source recovery, corruption and retained work. Synthetic
actors/files do not demonstrate actual model reading or general autonomous
collaboration. M2 promotion still needs the named consumer/UX review and any
additional profile's own acceptance; an opt-in declaration is not that promotion.

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

### 2026-10-04 — F2 synthetic fixture (pre-M1)

- **Baseline:** `99839ae`
- **Delivered:** the Appendix D F2 row as a checked-in public-safe smoke with
  its mutation arm (`examples/goal-direction-baseline-f2-smoke.py`); the
  Section 11 note records why M1 stays closed
- **Evidence:** the smoke passes locally and `loopx check --scan-path
  docs/architecture/rfcs` is clean; PR validation pending
- **Known gaps:** no runtime builder, writer, or consumer; F1 and F3-F8
  remain unchecked
- **Effect on normative design:** none — pins Appendix D F2 only

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-09-10 | Accept M0 as a documentation-only helper scope; repository docs are one provider and revision drift stays advisory. | Maintainer scope on [#2831](https://github.com/huangruiteng/loopx/issues/2831#issuecomment-5224703471) | Runtime implementation or repository-specific writer | Initial Sections 1-12 |

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | Current Material Frontier semantics were audited. | `41a95d9` | [Agent Material Frontier](../../reference/protocols/agent-material-frontier-v0.md) | documented | Public contract only; no live materials. |
| E2 | Route-change ownership remains existing. | `41a95d9` | [Goal Vision and Replan](../../reference/protocols/goal-vision-replan-contract-v0.md) | documented | Does not prove the proposed baseline. |
| E3 | Current RFC mirrors remain public-safe and consistent with documentation governance. | `c464cf308` plus Section 12 proposal | `loopx check` scoped to both RFC mirrors; `uv run --extra test python examples/docs-governance-smoke.py` | passed | Two-file public scan; documentation scope only. |
| E4 | The F2 revision-drift invariant is executable and mutation-checked. | `c464cf308`; fixture introduced at `99839ae` | `uv run --extra test python examples/goal-direction-baseline-f2-smoke.py` | passed, including mutation arm | Fixture-local synthetic builder only; no runtime builder or live materials. |
| E5 | Existing frontier costs and missing runtime owners inform Section 12 recommendations. | `c464cf308`, Python 3.11.7, macOS arm64 | Appendix F recipe; `material_frontier.py`, `management_projection.py`, `agent_context.py` | Five synthetic shapes measured; source boundaries inspected. | Existing Python substrate only; no M1 builder, receipt writer, SLO or runtime approval. |

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
path was never exercised. F2 is checked in at
`examples/goal-direction-baseline-f2-smoke.py` with its mutation arm; the
remaining cases stay open until M1.

## Appendix E: Rejected or superseded alternatives

Section 6 records the currently rejected designs. Evidence that a provider-
specific owner can preserve the same authority, privacy, and no-mutation
invariants may justify a profile, but not a fork of the core contract.

## Appendix F: Entry-decision evidence (non-normative)

At `c464cf308`, `build_agent_material_frontier` in
`loopx/control_plane/agents/material_frontier.py` scans supplied receipts for
each resolved material. The following observations describe that existing
Python call, not future TypeScript direction-builder performance:

| Materials | Receipts | Topics | Input bytes | Output bytes | Median ms | Observed range ms |
| --- | --- | --- | --- | --- | --- | --- |
| 16 | 16 | 16 | 8,153 | 5,988 | 0.783 | 0.679–1.032 |
| 64 | 64 | 64 | 31,961 | 22,404 | 3.193 | 3.013–3.419 |
| 256 | 256 | 64 | 122,741 | 88,226 | 19.286 | 18.857–19.675 |
| 256 | 4,096 | 64 | 1,059,587 | 88,482 | 184.327 | 177.839–200.030 |
| 512 | 8,192 | 64 | 2,119,171 | 176,546 | 670.294 | 658.070–688.730 |

Reproduction recipe: Python 3.11.7 on macOS arm64; fixed Goal
`direction-fixture`, Agent `agent-a`, ids `material-{i:04d}` and revision
`revision-1`. Each authority row contains its id, that revision,
`boundary=public_safe`, `gate_status=ready`, `freshness=current`,
`role=direction_source`, `owner_status=reviewed` and `conflict_rule=owner_wins`.
Map `topic-{i:02d}` to material ids at indices `i::64`, for
`i < min(64, material_count)`; the Agent profile contains the same `agent_id`
and requests every topic. Generate
one receipt per material in the first three cases and sixteen in the last two,
with the existing schema, Goal/Agent/material ids, observed revision,
`outcome=read`, id `receipt-{i}-{j}` and timestamp
`2026-10-12T00:{j:02d}:00Z`. Set `generated_at=2026-10-12T00:30:00Z`.

Call the existing builder once for warmup, then seven times with identical
inputs using `time.perf_counter_ns`; report median and min/max. Serialize the
complete arguments and result with sorted keys and compact JSON separators for
byte counts. Assert unchanged serialized inputs, exact emitted item count and
all items current. No provider, network or model is involved. A separate
`tracemalloc` call observed 474,642 bytes peak at 256/4,096 and 973,250 bytes at
512/8,192; those are traced Python allocations during the call, not RSS or
total-process memory. Timing excludes fixture construction and serialization.

The observations show why both material count and receipt history matter.
They do not qualify the proposed declaration schema, maximum-width strings,
conflicts, combined worst cases, end-to-end latency or workload prevalence.
Keep raw measurements outside the public repository; the fixture recipe and
bounded aggregate observations are the review evidence.

## M1 candidate source checkpoint

Source validation does not replace the §11 M1 entry approval; the §12 owner
decisions remain pending.

The optional pure TypeScript builder replaces the synthetic decision owner.
Validation covers complete registry resolution, exact Agent/Todo/revision,
conflicts, timestamp order, immutable inputs, input/output limits and public-safe
projection. Run `node --experimental-strip-types --test tests/control_plane_ts/direction_baseline.test.ts`
and `uv run --extra test python examples/goal-direction-baseline-f2-smoke.py` with
Node >=22.22.3. Run the existing `vision_checkpoint.test.ts` for route authority.
No parallel Python reducer or Python/TypeScript consuming pair is introduced.
The §12.5 candidate qualifies an explicit local read/ack and instance-bound
CLI/settings path. Live model consumption, other providers, production capacity
promotion and Lark remain unqualified. Remove the unactivated candidate to roll
back; after use, disable the reader and retain its canonical history and fences.
