# Reward Memory Architecture v0

[中文版](README.zh-CN.md)

LoopX separates feedback evidence, policy content, and action authority so a
useful judgment does not silently become a universal personal profile or create
permissions that the actor never held. Feedback from a verified repository
owner or core contributor may still derive durable policy content inside that
actor's independently verified repository scope. The distinction is between
inferring what the contributor wants and inventing what the contributor is
authorized to permit.

This contract defines five memory classes, guarded precedence, and the
pilot/meta delegation boundary. The architecture stages are: Stage 1 for the
corpus registry and health read model, Stage 2 for the stateless
candidate/review seam, and Stage 3 for explicit recall/application. The
post-outcome utility stages use the existing observation contract: utility
Stage 1 provides observations and utility Stage 2 provides the idempotent
reducer and read-only projection. The minimal ingest loop only composes the
existing seams into one corpus-owner-authorized provider write and exact
readback. It adds no second memory store, candidate scheduler, semantic router,
evaluation harness, or rollout. The opt-in runtime hooks reuse these seams at
module-owned boundaries; they do not create a background learner.

The machine-readable contract is available through:

```bash
loopx reward-memory architecture --format json
loopx reward-memory candidate-review --case issue-fix-verified-contributor --decision accept --format json
loopx reward-memory utility-project --input utility-observations.json --format json
loopx reward-memory ingest-event --input full-public-fixture.json --format json
```

## Experimental activation

Reward Memory is a provider-neutral, default-off experimental capability. It is
enabled for a named Agent inside one Goal, not for a whole Goal or LoopX
install:

```bash
# Preview first; add --execute only after checking the boundary change.
loopx configure-goal --goal-id <goal> \
  --reward-memory-config .loopx/config/reward-memory/experiment.json \
  --reward-memory-agent <registered-agent>

loopx reward-memory experiment-status \
  --goal-id <goal> --agent-id <registered-agent> --format json
```

The registry retains `enabled`, `experimental`, an ignored repo-relative config
pointer and digest, the explicit Agent allowlist, and a public-safe per-Agent
enablement receipt. Provider-specific choices stay local and private. A
verified receipt proves that the exact route accepted a fresh non-recallable
canary write and returned the same bytes. A missing receipt or config digest
drift makes automatic use unavailable without blocking ordinary Goal work.
OpenViking is the first provider used by the Issue Fix pilot, but it is not a
global LoopX feature flag or mandatory dependency; another provider can
satisfy the same binding contract.

### Recovering a changed binding

Editing the ignored config invalidates its old enablement receipt. `enablement_stale`
and `enablement_unverified` now return a shared `repair` plan: reuse the invoked
registry and existing Agent allowlist with `configure-goal`, inspect the config
change, preview, apply within existing authorization, then verify `available`.
Commands are explicitly marked templates with a required `<invoked-registry>`
binding. Bind it to the exact invocation registry before execution; an omitted
registry must never silently select a default. The private registry path, config
pointer and provider scopes are not published. The config pointer is retained. Apply re-runs provider write/exact-readback and
synchronizes the source/global binding. Never repair drift by copying a digest
into an old receipt. Disabled capabilities offer no re-enable plan.

The configuration catalog checks the live config digest and reuses runtime
admission validation rather than presenting a cached receipt as current verification. It separates `desired_automation` and
`recorded_verified_agents` from `binding_status`, `effective_available` and
effective automation. The existing settings summary renders these same fields.

The same plan is carried through Turn recall, quota and agent status/Markdown;
the explicit recall CLI preserves the actual failure instead of labelling every
unavailable configuration `disabled`. The existing capability editor can preview
and reapply the retained pointer/Agent list through the same owner. No automatic
configuration acceptance or new provider permissions are introduced. Recovery of
enablement must still be followed by a real qualified experience write, exact
readback and destination recall before claiming useful memory is available.

### OpenViking v0.4.19 identity boundary

LoopX currently assumes one Agent belongs to exactly one Goal, while a Goal may
contain several Agents. Agent names are only Goal-local. The durable runtime
identity is therefore `(goal_id, agent_id)`, and LoopX derives a deterministic,
OpenViking-safe peer token from that pair. Reusing `explorer` in another Goal
produces another peer token, even when both routes use the same authenticated
OpenViking user.

New private writes must use
`viking://user/{user_id}/peers/{canonical_peer}/memories/...`; the request's
`actor_peer_id` must equal the URI peer before any provider call. LoopX rejects
`viking://agent/...` as a Reward Memory write target because OpenViking v0.4.19
keeps durable memories in the current User or actor-bound Peer namespace; the
Agent scope is not the durable per-peer memory root. LoopX also rejects
user-private paths without an actor-bound peer for an Agent-private corpus. Private peer
reads/writes require CLI `>=0.4.18` and server `>=0.4.19`. See OpenViking's
[multi-tenant model](https://github.com/volcengine/OpenViking/blob/main/docs/en/concepts/11-multi-tenant.md)
and [context types](https://github.com/volcengine/OpenViking/blob/main/docs/en/concepts/02-context-types.md).

Config v1 can bind one private Goal-scoped Agent only. Supplying several Agents
with one private provider binding is rejected instead of silently sharing a
peer. Account/public resources remain an explicit shared mode. A future
same-Goal `goal_shared` memory must be a separate corpus with its own owner,
reader allowlist, write/promotion policy, and receipt; runtime recall may then
federate the Agent-private and Goal-shared corpora explicitly. Private memory is
never promoted or made visible to a sibling Agent merely because both Agents
belong to the same Goal.

### Lifecycle completion boundary

Provider readiness and lifecycle automation are separate status dimensions. A
verified storage receipt does not by itself prove that automatic recall and
writeback are connected to every host. Complete lifecycle enablement means the
supported planning/decision entry points perform bounded recall, and real
evidence-backed outcome reviews perform idempotent writeback without another
hidden opt-in. No new evidence means no new memory. A missing identity never
falls back to a shared corpus; provider degradation stays visible while base
Goal work continues. Each host must report its actual coverage rather than
generalizing from a direct CLI or helper test.

Config v1 declares one `project_provider_binding`, its exact per-corpus scope
references, the project corpus set, module-owned surfaces, and an automation
policy. A surface explicitly lists its compatible `corpus_ids`, selects one
`ingest_corpus_id`, and owns its `recall_profile`. LoopX never discovers routes
by scanning all corpora. Corpora assigned to one surface must have the same
memory class, authority, privacy, freshness, and lifecycle. Scope digests and
provider/corpus identity are still checked independently for every corpus.

```json
{
  "schema_version": "reward_memory_experiment_config_v1",
  "project_provider_binding": {
    "provider_id": "openviking",
    "namespace": "reward_memory",
    "corpus_scopes": [
      {"corpus_id": "review_policy", "scope_ref": "viking://.../review-policy"}
    ]
  },
  "corpora": [
    {"corpus": {"corpus_id": "review_policy"}, "standing_policy": {}}
  ],
  "surfaces": [
    {
      "surface_id": "reviewer_artifact.summary",
      "adapter": "scoped_feedback",
      "corpus_ids": ["review_policy"],
      "ingest_corpus_id": "review_policy",
      "recall_profile": {
        "profile_id": "review_summary_v1",
        "mode": "function_boundary",
        "max_queries": 1,
        "limit": 4
      }
    }
  ],
  "automation": {
    "automatic_recall": true,
    "automatic_ingest": true,
    "fail_open": true
  }
}
```

The abbreviated corpus and standing-policy objects above represent the full
existing record contracts. These `true` values show the new-enable default;
an explicit `false` remains the supported per-hook opt-out. `configure-goal`
preview now calls the provider
preflight and reports `preflight_ready`, `preflight_incomplete`, or
`unavailable`; it never reports a provider write as merely `planned`. Preview
does not prove writability. Apply must complete the fresh canary write and
exact readback before it commits the registry binding. `experiment-status`
reports the v1 config schema,
corpus/surface counts, recall-profile ids, and the effective automatic policy
without exposing scope refs. Agent-scoped `quota should-run` and `status
--agent-id` resolve that policy through the same invoked registry and config
reader; they do not copy automation flags into registry summaries. Their compact
`config_runtime_route` names the registry role and project/shared runtime scope,
and marks exact config readback without exposing either local path. The runtime accepts only
`reward_memory_experiment_config_v1`; local ignored configs must be migrated
explicitly before rollout. Setting a flag only authorizes a compatible runtime
hook; it does not create a scheduler, infer a query, widen authority, or bypass
the exact surface/corpus guards.

`automatic_recall=true` lets a predeclared module boundary call the shared
runtime hook. The module still supplies its surface query, current-artifact
checks, and reasoning callback. The hook follows the configured corpus order,
stops at the first exact readback hit, allows exactly one query at a
`function_boundary` or at most three at a bounded-agentic boundary, and emits
provider-call telemetry plus an application receipt. A provider or application
failure preserves the module's base output and is never a user gate.

`automatic_ingest=true` lets a module pass one already-distilled compact event
to the configured adapter and ingest corpus. The adapter and standing policy
remain responsible for exact actor/project/surface/action scope. The shared
hook reuses deterministic candidate identity, activation, provider sync, exact
readback, and an ingest receipt. It does not collect chats, parse tool logs,
store raw content, or infer new authority. Repeated events remain idempotent.
When either automation field is omitted from a newly enabled v1 config it
defaults to `true`; an explicit `false` remains disabled and is projected with
`explicit` intent provenance. Existing false values are never silently
reinterpreted. The explicit `ingest-event` command remains an operator/caller
path rather than a compatibility fallback.

The production Codex CLI Turn performs recall after quota/Todo admission and
accepts outcome ingestion only after independent validation, durable writeback,
and quota settlement. A reflection must use `turn_reward_memory_reflection_v1`
and include an exact configured surface, a distinct research/simulation/real/
engineering source kind, and opaque evidence refs; an ordinary Turn summary is
not evidence. It also carries `procedural_experience_contract_v0` with
applicability, observed outcome, attribution, complete future behavior,
limitations, and the same evidence refs. Legacy v0 reflections remain
audit-only and cannot become durable memory. Ambiguous provider commits and
unverified readbacks remain in a
mode-0600 Goal+Agent+event sidecar. The next executing Turn retries the same
deterministic event before recall, so the provider can deduplicate it and LoopX
can require exact readback. Explicit disable suppresses reconciliation and all
provider calls.

The Codex App uses the same settlement boundary without copying the raw
reflection into run indexes, rollout events, or public projections. For a
Todo-bound accountable refresh, the caller may add
`--reward-memory-reflection-json <turn_reward_memory_reflection_v1 JSON>`.
LoopX stores that candidate only in a mode-0600 Goal+Agent+candidate sidecar and
runs the exact completion-validation command already declared by that Todo. The
validator must return `reward_memory_reflection_validation_v0` with the exact
reflection digest and evidence references; an ordinary successful validation
exit is insufficient. The later matching `quota spend-slot --execute` finalizes
ingestion only after exact refresh/writeback and spend readback. A missing,
failed, or non-attesting validation remains `awaiting_evidence_validation` and
makes zero provider calls. The App does not need a separate manual
`reward-memory ingest-event` command for this lifecycle. DSH currently carries
recall context but does not claim this post-settlement ingest boundary.

Dashboard, CLI/status, and Lark projections reuse the same capability owner and
public receipt. Dashboard writes only an ignored config pointer and registered
Goal-local Agent allowlist through the existing preview/apply/readback
transaction. It returns an opaque binding revision, effective automation and
intent provenance, never the local-private path or provider scope.

An allowlisted agent supplies only the compact event at runtime:

```bash
loopx reward-memory ingest-event \
  --goal-id <goal> --agent-id <registered-agent> \
  --input compact-event.json --execute --format json
```

Real provider writes require this configured goal-and-agent route. The legacy
full-packet form remains available only as a no-write evaluation fixture.
Config loading alone does not capture feedback or authenticate a provider.
Automatic hooks run only when both the flag and a real module-owned callsite
are present. Issue Fix currently has two independently configured recall
callsites: `reviewer_artifact.summary` applies a concise reviewer-facing
summary, while `reviewer_notification.before_send` may apply one verified
structured hard-policy delivery window immediately before the existing
secondary sink. The latter reuses the sink's queue/dedupe/readback path and is
unrestricted when neither recalled nor explicit sink policy exists; it is not
a generic router. Other surfaces remain explicit until separately wired and
verified. Issue Fix continues normally when the experiment is disabled,
unavailable, rejected by guards, or fails exact readback. Invalid or non-v1
configuration resolves unavailable with both automatic flags false.

### Inbox feedback review (explicit ingestion)

When a registry-routed `loopx lark-inbox drain --goal-id <goal> --agent-id
<agent>` returns messages, it also emits `reward_memory_feedback_review` if
Reward Memory is enabled for that agent and has an active, writable
`scoped_feedback` route with an enabled standing policy and exact
`peer_ref=agent:<agent>`. The hint lists configured destinations and a preview
command bound to the same registry, Goal and Agent. It is advisory: there is no
provider call, automatic candidate creation, new permission, or ACK gate.
Both JSON and Markdown drains expose it. Empty/disabled inboxes, disabled or
invalid memory configurations, unconfigured agents, incompatible routes, and
explicit `--config`/`--project` overrides retain their previous output.

The agent reviews the conversation before choosing what, if anything, to learn:

1. Verify the source actor and existing authority, current evidence, conflicts
   and applicability. A policy's allowed roles are not proof of a sender's role.
   A disagreement is not a universal ban, and one-off task state belongs in
   Todo/vision rather than durable preference memory.
2. Distill only confirmed reusable feedback into an applicable configured
   `soft_preference`, `procedural_experience`, or independently authorized
   `hard_policy` route. Never widen the route or enable automation to make an
   event pass. Keep raw chat and credentials out of the event.
3. Prepare `{adapter, event, observed_at}` using the
   [scoped feedback fixture](../../../examples/fixtures/reward-memory-scoped-feedback-ingest.public.json)
   for field shape only. The event uses
   `schema_version=scoped_feedback_reward_memory_event_v0`, a stable
   `feedback_ref`, actual `source`, `reasoning`, `guard_context`, compact
   `content_summary`, `target_class`, and exact identity/surface/revision/action
   scope. `procedural_experience` additionally requires a typed
   `procedural_experience_contract_v0`: applicability, observed outcome,
   attribution, future behavior (`trigger`, `action`, `validation`, and
   `stop_condition`), limitations, and opaque evidence refs. A fact recap does
   not satisfy this contract. Advisory classes require empty
   `requested_action_scopes`; allowed
   policy scopes do not grant advisory memory action authority. Do not copy the
   fixture's actor or verified-guard assertions.
4. Replace the hint's input placeholder and preview `ingest-event` without
   `--execute`. Inspect its guards. Only then execute within the standing
   policy and verify `exact_readback_verified` and
   `memory_available_for_recall`; a preview is not a learned memory.
5. Finish normal reply/material-review/ACK. No reusable feedback or unavailable
   memory is an honest no-memory outcome, not a reason to block the inbox or
   repeatedly ask the user for permission. Actual write failures remain visible
   in the existing ingestion receipt.

`automatic_ingest=false` does **not** prohibit this explicit workflow. Enabling
it also does **not** wire raw inbox messages into ingestion. Issue Fix's compact
feedback adapter and recall hooks share the same core but are not a general
inbox-to-memory feedback loop. Disable the hint by disabling Reward Memory for
the agent (or its applicable route); no extra store, queue or scheduler exists.

## Five first-class classes

| Class | Source and scope | Authority and use | Lifecycle |
| --- | --- | --- | --- |
| `run_bound_reward` | Explicit human judgment attached to one exact goal/run. | Evidence about that outcome only. Future influence requires compact candidate derivation and an activation policy; the overlay itself is not a standing instruction. | Append-only overlay; corrections and revocations append references instead of rewriting the judged run. |
| `hard_policy` | Explicit user/repository/operator authority, or policy content inferred from verified owner/core-contributor evidence and bound to an existing project/action authority scope. | Constraint or veto inside the verified scope. Reasoning may infer policy meaning from rewards, preferences, current-artifact-verified experience, selected options, accepted/rejected outcomes, and maintainer corrections; it may not infer credentials, new publish/production scope, or cross-user/repository authority. | Active records retain actor, evidence, scope, and derivation provenance until superseded, revoked, or expired; temporary or weakly reinforced inference should expire or return to review. |
| `soft_preference` | Explicit feedback, selected options, or later reviewed candidates scoped to a workspace/project and module-owned surface. | Advisory ranking or rewrite only. It cannot grant publish, merge, write, credential, or production authority. | Durable only after explicit review; editable, rejectable, supersedable, revocable, and retireable. |
| `procedural_experience` | Revision-stamped trajectories, distilled experiences, maintainer corrections, accepted/rejected changes, and reviewed architectural learning. The typed experience contract preserves applicability, observed outcome, attribution, future behavior, limitations, and evidence refs. | Advisory diagnosis, scope, routing, or validation guidance only after current-artifact verification and experience-quality qualification. A fact-only summary or successful provider write is not experience evidence. Retrieval alone has zero patch authority. | New source truth or application evidence can mark an experience stale, harmful, refuted, superseded, or retired. Initial qualification means structurally reusable and evidence-bound; value remains unproven until application/outcome attribution. |
| `working_context` | Either fresh execution state (`fresh_execution_context`) or a revisioned session-continuation summary (`session_working_memory`). | Supports only the current execution/session continuation. Neither subtype becomes reusable policy or grants action authority. Fresh source-of-truth reads outrank recalled material. | `fresh_execution_context` already exists in LoopX registry/state/todo/quota/checkout observations and is reused, not rebuilt. Session context remains bound to its session/archive revision. |

Every durable record must name `source`, `scope`, `authority`, `confidence`,
`lifecycle_state`, `supersession`, `revocation`, `expiry`, and `privacy` in
addition to the class. Confidence describes evidence quality; it never
increases authority. Confidence is `low`, `medium`, or `high` with a required
basis; source names kind/ref/actor/time, scope names user/workspace,
project/repository, module/surface, and revision/time boundaries. Lifecycle
records state plus supersession, revocation, expiry, and retirement references.
Privacy names visibility, retention class, and whether raw content was captured.

## Policy content versus authority

Hard policy has two independent questions:

1. **What is the policy?** LoopX may infer compact policy content from explicit
   feedback, reviewed preferences, current-artifact-verified experience,
   selected options, repeated accepted or rejected outcomes, and maintainer
   corrections.
2. **Where does that policy have force?** Actor identity and repository/action
   authority must come from an independently verified source. Memory confidence
   cannot create or widen that scope.

For a verified repository owner or core contributor, an unambiguous inferred
policy may become active without asking the same question after every run when
all of these hold: actor and authority scope are verified, provenance is
compact and inspectable, no higher-authority source conflicts, and the record
is reversible through edit, supersede, revoke, retire, or expiry. Ambiguous
meaning, unclear scope, identity uncertainty, or a conflict returns the item to
review. Inference never creates credentials, external-write capability,
production permission, cross-agent authority, or authority in another
repository.

Inference may derive a reusable boundary or gate policy, but it cannot fabricate
the current state transition of a concrete operator gate or authority
checkpoint. A current approve/reject/consume receipt still comes from that
gate's source of truth.

## Guarded precedence and model reasoning

The following order is a safety and attention envelope, not an exhaustive
decision table:

1. explicit action authority and privacy boundaries;
2. active in-scope hard policy;
3. fresh working context and current source of truth;
4. current-artifact-verified procedural experience;
5. active in-scope soft preference;
6. run-bound reward as evidence only.

Deterministic code should reject illegal states: unverified authority, wrong
project or surface, revoked/expired material, privacy violations, unresolved
same-authority conflicts, and missing current-artifact verification. Within the
remaining allowed action set, the model keeps responsibility for interpreting
feedback, judging relevance and evidence sufficiency, balancing trade-offs,
and deciding to apply, ignore, or seek more evidence. Its compact receipt names
the reasoning summary, memory references, artifact verification, and
authority/scope check.

Prefer explicit provenance when evidence is otherwise equal, but do not turn
that preference into a hard-coded router that suppresses useful inference. Raw
chat, transcripts, tool logs, credentials, and local paths are not
reward-memory records.

`loopx reward-memory route-check` is a deterministic regression fixture for
obvious safety/escalation conditions such as PR #3237. It is not the live Issue
Fix decision engine and does not replace model reasoning.

## Architecture layers and reuse

```mermaid
flowchart TD
  OV["OpenViking memory substrate<br/>AGFS source truth, index, scoped retrieval,<br/>preferences, trajectories, experiences, session memory"]
  CTX["Existing LoopX fresh execution context<br/>registry, active state, todo/quota, checkout, bounded observations"]
  CORE["LoopX reward-memory core<br/>classes, scope/authority, corpus health,<br/>candidate and activation decisions"]
  IF["Issue Fix adapter<br/>run outcome and maintainer feedback in;<br/>routing/validation influence receipt out"]
  OTHER["Later module adapters<br/>same core contract, module-owned surfaces"]

  OV --> CORE
  CTX --> CORE
  CORE --> IF
  CORE --> OTHER
```

OpenViking owns memory storage, indexing, scoped retrieval, and session-memory
building blocks. LoopX owns action semantics: class, scope, authority,
lifecycle, candidate derivation, activation, and application receipts. The
Issue Fix adapter maps issue/PR evidence into the shared core and consumes its
decisions; it must not grow a parallel memory store, policy schema, or ranking
pipeline. Other modules join only after this reuse seam is proven.

### Issue Fix as the first adapter

Issue Fix contributes only domain mapping:

- exact run reward, maintainer correction, selected fix direction, and durable
  issue/PR outcome become compact inputs to the shared candidate contract;
- repository identity, contributor role, current checkout, issue/PR state, and
  active LoopX gates supply current authority and execution context;
- OpenViking supplies scoped preference or experience retrieval when the
  module asks for it;
- the shared core returns apply, ignore, seek-evidence, or review, together
  with a compact influence receipt;
- Issue Fix still verifies any recalled technical claim against current code
  and tests before it can affect a patch.

The adapter does not own candidate lifecycle, contributor-policy semantics,
retrieval health, or provider persistence. Those stay reusable core concerns.
This keeps the issue-fix scenario valuable without letting it define the whole
memory product.

## Implemented Stage-2 seam

Stage 2 accepts a model-proposed compact candidate: target class, content
summary, source actor and evidence reference, workspace/project/surface scope,
reasoning summary, confidence, and any requested action scopes. The model owns
interpretation and the proposed policy meaning. Deterministic code only checks
the public-safe shape, scope binding, raw-content boundary, source freshness,
unresolved conflicts, current-artifact proof where required, and authority
checkpoint.

For `hard_policy`, the checkpoint must independently bind the same actor, role,
project, and action scopes. A verified core-contributor correction can
therefore produce an activation-ready policy candidate directly, but only for
the subset of action scopes already present in that checkpoint. An unverified,
mismatched, or wider request remains inspectable as `guard_blocked`; an
attempted `accept` or `edit` becomes `no_write` instead of gaining authority.
Advisory preference and experience candidates cannot request action authority.

The review contract exposes five decisions:

- `accept` emits an active record;
- `edit` emits a revised candidate linked to the prior candidate;
- `reject` closes the candidate as rejected;
- `retire` closes an already active reviewed record;
- `no_write` records that no provider write should occur.

These are decision records, not persistence operations. `accept` and `retire`
only return a next-step instruction for the caller to use the declared corpus
write authority and verify readback. The seam itself writes no LoopX state,
OpenViking corpus, index, receipt, or external system. It also does not ingest
raw chat or tool transcripts.

`issue_fix_reward_memory_candidate_adapter_v0` is deliberately a field-mapping
adapter. It maps a compact issue reference, repository revision, module-owned
surface, contributor evidence, and model reasoning into
`reward_memory_candidate_v0`; all guards and lifecycle decisions remain in the
shared core. This is the first reuse proof, not an Issue Fix-specific memory
implementation.

## Minimal ingest loop

`loopx reward-memory ingest-event` is a thin atomic orchestration, not another
memory product layer. The configured experiment explicitly selects an adapter.
`issue_fix_maintainer_feedback` remains the Issue Fix compatibility adapter;
`scoped_feedback` accepts a generic `scoped_feedback_reward_memory_event_v0`
for any module-qualified surface. Both adapters only map strict compact fields
into the same `reward_memory_candidate_v0`; neither owns a second lifecycle,
store, scheduler, recall path, or semantic router. LoopX neither retains raw
feedback bodies nor uses keywords to decide which feedback deserves memory.
The model or calling module first distils an event containing only a source
reference, verified actor/role, exact workspace/project/surface/revision,
compact summary, reasoning, and current-artifact evidence.

A `reward_memory_standing_policy_v0` predeclares the corpus owner, reviewer,
authority source, exact project/surfaces, one memory class, source kinds,
verified actor roles, and action scopes. This replaces per-comment approval
with one approval of an exact boundary. It cannot create credentials,
repository write authority, publish/production scope, or cross-project
authority. Out-of-scope, conflicted, stale, raw, or unmodelled input is
`guard_blocked` before any provider call.

For `procedural_experience`, deterministic guards first require the complete
typed experience contract. Missing or malformed future behavior, limitations,
or evidence binding is rejected before the provider is called. The complete
contract participates in `candidate_ref`, survives the active envelope, and is
injected into recall guidance. Legacy fact-only procedural records are not
recallable. This is a qualification gate, not a claim of proven utility:
application receipts and verified outcome attribution still decide whether an
experience was helpful, neutral, harmful, or should be retired.

The command then composes deterministic `candidate_ref` deduplication, standing
policy acceptance, active-envelope construction, declared-provider `sync`, and
one exact-corpus/surface function-boundary readback. For
`procedural_experience`, it also performs a destination-surface
`business_recall` using the applicability and future-behavior fields, without
the candidate id in the query. The recalled record must match both the
candidate and experience digests. This prevents an exact-id storage check from
being mistaken for evidence that a lesson can be found where decisions use it. A
`reward_memory_ingest_receipt_v0` reports `activated` and
`memory_available_for_recall=true` only when resource ref, candidate ref, and
canonical content digest all match, the class-specific experience-quality gate
passes, and any required destination recall succeeds. A destination miss stays
`recall_unverified` for bounded reconciliation rather than becoming active.
`exact_readback_verified=true` alone proves persistence, not experience quality,
discoverability, or utility. Provider unavailability, pending commit, readback
mismatch, or destination-recall miss fails open and does not block the caller's
normal work.
`observed_at` is the immutable first-observed event timestamp and must be reused
on retries. The provider target binds both standing-policy and candidate
digests so a policy revision cannot silently reuse an older activation.
`--execute` is off by default; dry-run returns only `planned`. Execute also
requires the goal id and allowlisted agent id, so a caller cannot bypass the
default-off experiment policy by supplying a provider binding directly.

The caller still explicitly invokes its existing Stage-3 function-boundary
recall hook. Issue Fix keeps `run_issue_fix_patch_planning_reward_memory`; other
modules use their own surface-owned hook. The model decides apply, ignore, or
refute and reuses the shared application receipt. The ingest seam adds no
deterministic semantic routing or background scheduler.

## OpenViking alignment

The five classes are provider-neutral, but the Stage-0 boundary was checked
against OpenViking's current public architecture and code:

- OpenViking is a context database, not an action-authority system. AGFS
  content is its source of truth; the vector index stores retrieval references.
- OpenViking `preferences` can supply reviewed `soft_preference` candidates.
  They never become permission.
- OpenViking `trajectories` are add-only operation contracts distilled from one
  execution. OpenViking `experiences` are upserted, executable-looking
  generalizations that may explicitly `supersede` an older experience. Both map
  to advisory `procedural_experience`, subject to current-revision verification.
- OpenViking `cases` explicitly define a task and rubric for training or
  evaluation; they are not experience instructions and cannot be injected as
  policy.
- OpenViking Working Memory is a seven-section archive overview used for
  session continuation. It maps to `working_context/session_working_memory`,
  not long-term policy. LoopX's fresh registry/todo/checkout observations map to
  the separate `fresh_execution_context` subtype.
- OpenViking `soul.md` or another provider record may contain policy evidence,
  but it becomes LoopX `hard_policy` only when the actor and repository/action
  authority scope are independently verified. The content may be inferred; the
  authority may not.
- Account, user, peer, session, and repository-revision boundaries remain part
  of scope and privacy. A peer label does not grant cross-user or cross-agent
  authority.

Provider health is intentionally decomposed into `corpus_present`,
`index_present`, `retrieval_query_succeeded`, `result_readback_verified`, and
`memory_applied_with_receipt`. These states must not be collapsed. In
particular, the current OpenViking Codex auto-recall path configures the
`experiences` quota to zero, so an experience corpus can exist without being
automatically recalled. Stage 1 owns that inventory and health proof; Stage 0
does not claim it.

Grounding references: OpenViking
[architecture](https://docs.openviking.ai/en/concepts/01-architecture),
[session management](https://docs.openviking.ai/en/concepts/08-session),
[multi-tenant and peer isolation](https://docs.openviking.ai/en/concepts/11-multi-tenant),
and source revision
[`ba46491`](https://github.com/volcengine/OpenViking/tree/ba46491af0a79467ea268ef370e35b68f86abf73).

## Pilot/meta delegation

The pilot may take a fix only when behavior is a confirmed bug, scope is one
bounded surface, the change does not alter a semantic contract or place
product-specific policy in a generic boundary, reproduction and validation are
named, edge-case complexity is low or medium, and all relevant evidence is
present. Meta design review is required for by-design or uncertain semantics,
a semantic-contract change, cross-surface change, generic-boundary leakage, or
high edge-case complexity.

Evidence requirements are relevance-gated instead of using a blanket
"core-component" rule: effect evidence is always required; UX evidence is
required for a user-visible behavior change; performance evidence is required
for a hot-path or storage-behavior change; benchmark evidence is required only
when retrieval or memory quality is claimed. Missing required evidence without
a meta trigger produces `hold_for_evidence`. This allows a bounded bug inside a
core module to remain pilot-sized while still escalating a deceptively small
change that alters a public or storage contract.

This is guarded routing, not cross-agent authority. The live agent still
reasons about semantics and evidence inside the guards. The meta lane does not
edit or claim the pilot's todos, and the pilot cannot bypass the design gate
with a memory hit.

## PR #3237 regression

[OpenViking PR #3237](https://github.com/volcengine/OpenViking/pull/3237) is the
negative regression. It tried to make generic directory listing reflect
session-specific activity across backend and Web Studio surfaces even though
the maintained directory-mtime behavior was by design. The resulting patch
changed a generic filesystem/session contract for one product-specific edge
case, crossed backend and Web Studio surfaces, and added metadata reads on a
listing/storage path. It lacked product-effect, UX, and performance evidence.
Benchmark evidence is not required by this regression because it made no
retrieval or memory-quality claim.

The stable expectation is `meta_design_gate`, not `pilot_fix`. Meta may narrow
the product behavior to a session-specific presentation boundary or close the
change; a prior memory result cannot authorize the generic-layer patch.

```bash
loopx reward-memory route-check --case pr-3237 --format json
```

## Staged ownership

- Stage 0: this classification, precedence, and delegation contract.
- Stage 1: the implemented provider-neutral
  [corpus registry and health contract](../../../docs/reference/protocols/reward-memory-corpus-registry-v0.md),
  including ownership, authority, freshness, retirement, scope isolation, and
  retrieval-health distinctions. Its `fresh_execution_context` entry describes
  an existing LoopX capability; it is not a request for another context system.
- Stage 2: the implemented stateless candidate and activation-decision seam
  over existing LoopX/OpenViking evidence. It adds no second store, scheduler,
  automatic recall, or raw-content retention. Issue Fix is the first adapter
  and reuses the generic record/decision shape.
- Stage 3: the implemented opt-in cross-module recall/application seam. Model
  reasoning stays inside deterministic scope, authority, privacy, freshness,
  and conflict guards; Issue Fix patch planning and the non-Issue-Fix semantic
  preference module share the same core and compact application receipt. The
  minimal ingest seam reuses Stages 2/3 and the declared provider to atomically
  write and exactly read back compact events inside a standing-policy boundary;
  it does not choose the event, corpus, or consumer module.
- Stage 4: evaluation harness and release gate.
- Stage 5: bounded cross-module dogfood, optional post-outcome utility
  attribution, and operator edit/retire controls.

The post-outcome utility stages are deliberately separate from the architecture
stage numbers above. Utility Stage 1 validates and emits
`memory_utility_observation_v0`; utility Stage 2 reduces a complete observation
stream into [`memory_utility_projection_v0`](../../../docs/reference/protocols/reward-memory-utility-projection-v0.md).
The reducer is pure and default-off: it deduplicates semantic retries,
quarantines conflicting deliveries through a rejection-level
`quarantine_proposed` marker, applies typed evidence precedence, and keeps
item, set, and unresolved lineage subjects separate. A highest-tier `unknown`
also blocks weaker directional inference. It never changes
retrieval ranking, provider state, memory lifecycle, authority, quota,
scheduler, or the main work lane.

Later stages must extend this contract rather than collapsing these classes,
duplicating existing context/provider capabilities, or turning provider
availability into a user gate. Stage 1 remains a stateless read model and
performs no provider or external write.

## Stage 3 recall and application seam

Stage 3 accepts only an explicit `reward_memory_recall_request_v0` naming one
registered corpus and one module-owned surface. The request carries a matching
read-authority checkpoint and current freshness/conflict observations. A
project, surface, authority, revision, lifecycle, or provider-binding mismatch
stops before the provider is called. This is deterministic safety validation,
not a semantic router.

The caller/model owns the query and interpretation. `function_boundary` allows
one query at a named function boundary. `bounded_agentic_search` allows at most
three caller/model-authored queries. LoopX does not choose a module, infer a
corpus from similarity, scan every corpus, schedule a later recall, or grant
action authority from a hit.

An accepted `reward_memory_candidate_review_v0` may be wrapped as a
`reward_memory_active_record_v0`; only the declared corpus owner may persist
that envelope. Recall accepts only active envelopes from the exact selected
corpus and surface. Private summaries remain transient in-process. Public
packets expose opaque provider references and compact lineage; application
receipts contain hashed memory references, the model-owned reasoning summary,
and current-artifact verification, never raw provider content.

Recall packets also separate a provider that returned no items from provider
items rejected by the exact-record gates. `provider_item_count`,
`filtered_item_count`, and `filtered_reason_counts` expose only bounded counts
for contract, scope, lifecycle, expiry, quality, and legacy-contract filters;
they never expose provider content. A fact-only procedural record is reported
as `legacy_contract_missing` while remaining non-recallable. Its maintenance
projection offers only two owner-governed paths: ingest a separately validated
replacement and then retire the legacy record, or retire it directly through
the corpus's declared retirement authority. Both paths require provider write
readback. Recall itself performs no migration or retirement write.

Provider unavailability returns setup guidance and preserves the base output.
It is an agent/runtime condition, not a user gate. Invalid or failed model
application also preserves the base output. An `applied` receipt requires both
attribution to an item returned by this recall and current-artifact
verification. Issue Fix uses the fixed `issue_fix.patch_planning` surface;
`semantic_preference` is the second, non-Issue-Fix module consumer.
An OpenViking binding whose scope is under `/peers/<peer>/` must carry that
exact `actor_peer_id`. LoopX forwards it only to scoped provider operations and
never infers an actor identity from an arbitrary target URI.

## Stage 4 evaluation and release gate

Stage 4 is one bounded contract suite over the existing shared core. It does
not add another evaluator, store, provider, scheduler, or semantic router:

```bash
loopx reward-memory evaluate --format json
```

The runner executes the real candidate, recall, application, Issue Fix adapter,
and route-guard code for eight cases: compact/restart survival; project and
module isolation; supersede/revoke rejection; stale-source rejection;
multi-person authority matching; gate non-override; verified candidate-ranking
influence; and protection against a large patch for the PR #3237 edge case.

`evaluation.py` owns case orchestration, assertions, metrics, and the release
gate. Reusable setup and provider doubles live in `evaluation_fixtures.py` with
neutral fixture identities; OpenViking appears there only through the explicitly
named PR #3237 Issue Fix regression fixture. Project identity is fixture data,
not evaluator policy.

`reward_memory_evaluation_v0` reports task outcome plus exact local runner
latency, public evidence bytes, model-token count, provider/storage writes,
false applications, maintainer interruptions, and user gates. Zero model tokens
means this deterministic contract suite did not invoke a model; it is not a
token-cost estimate for later dogfood. The release gate passes only when every
case passes and all write, false-application, interruption, and user-gate counts
remain zero.

A pass yields `ready_for_bounded_dogfood`, not production release. It proves
core contract invariants only, does not claim semantic uplift, and does not
authorize production rollout. Stage 5 must use a corpus-owner-approved record,
exact provider readback, and real module outcomes before making an uplift claim.

## Stage 5 dogfood receipts, utility attribution, and operator controls

Stage 5 adds one thin evidence layer over the Stage 3 application receipt. It
does not add a store, scheduler, semantic router, automatic recall path, or
ranking behavior. A caller supplies a compact real-module observation whose
artifact reference matches the application receipt:

```bash
loopx reward-memory dogfood-evaluate \
  --input compact-observations.json --format json
```

`reward_memory_dogfood_receipt_v1` records `application_disposition` as
`applied`, `not_applied`, or `refuted`. This is application coverage, not a
causal utility judgment. An applied or refuted disposition is invalid unless
the selected provider result was read back exactly and the current artifact
was verified. The receipt retains the opaque digest `application_receipt_id`
for the exact Stage 3 application receipt, plus only opaque or hashed memory
references, a compact verified outcome reference and summary, latency,
model-token and provider-call counts, intervention count, and optional compact
bot feedback.
It retains no raw provider content and grants no new action authority.

The former `reward_memory_dogfood_receipt_v0` used `hit`, `miss`, and `refute`
for this same application-coverage distinction. In particular, its `hit`
never established that a memory was `helpful`. The v1 receipt supersedes that
ambiguous naming instead of silently changing the v0 contract.

Post-outcome utility is a separate `memory_utility_observation_v0` with one of
`helpful`, `harmful`, `neutral`, or `unknown`. The optional evaluator is
default-off and proposal-only. LoopX validates its proposal against separately
trusted agent, project, corpus, and surface scope; the verified outcome ref;
and the retrieval and policy snapshot refs supplied by the execution boundary.
Snapshot freshness is owned by the execution or provider adapter that supplies
the trusted attribution context. When a compatible application receipt also
carries snapshot refs, the validator cross-checks them exactly. A stale or
mismatched evaluator echo is rejected. `applied` plus a successful outcome
remains `unknown` without independent attribution evidence. When several
memories are involved, attribution defaults to `set`; set-level credit is never
copied to individual items.

Evidence basis is typed rather than inferred from prose. `owner_correction`,
`controlled_replay`, and `deterministic_effect` are stronger bases and require
at least one opaque `evidence_ref` in the evaluator proposal, even when the
label remains `unknown`. `evaluator_inference` is weaker and `insufficient` is
lineage-only. Stage 1 preserves that typed distinction; the Stage 2 reducer
owns precedence between weak and strong observations, and a highest-tier
`unknown` blocks weaker directional inference.

The public observation contains only opaque references, canonical memory
digests, typed reason codes, evaluator/version identity, and compact public-safe
evidence. URLs, local paths, raw-content fields, and any proposal to grant
action authority or perform a write are rejected. Provider adapters must digest
exact private provider references before constructing this public contract. An
absent, timed-out, or malformed evaluator fails open: the main result,
application settlement, and dogfood readiness remain unchanged. The attribution
subject, evidence, evaluator identity, and evaluation version produce a stable
observation id for replay identity. A different judgment under the same key is
a conflicting delivery, not additional support; a correction must cite new
evidence or advance the evaluation version. Utility Stage 2 reduces the complete
stream into a bounded projection and treats exact semantic retries as a no-op.
Conflicting deliveries remain outside subject state and carry a rejection-level
quarantine proposal; a highest-tier `unknown` cannot be overridden by weaker
directional inference.
The Stage 5 batch rejects duplicate
`application_receipt_id` values and counts each application settlement once.
The settlement `receipt_id` deliberately excludes evaluator status and
`observation_id`; utility retries and new evidence use the observation identity
instead. A new utility observation for the same settlement belongs in the later
append-only utility ledger and must not duplicate disposition or cost metrics.

`reward_memory_dogfood_batch_v1` becomes
`ready_for_bounded_issue_fix_pilot` only when the Stage 4 gate still passes and
the bounded batch contains at least one Issue Fix result, two distinct LoopX
domain results, all three `applied`/`not_applied`/`refuted` application
dispositions, and both operator controls. Utility observations do not affect
this readiness decision. This is a trial-readiness statement; semantic uplift
and production rollout remain false. Utility Stage 2 does not add ranking
influence or OpenViking writeback.

The edit/retire control is similarly narrow:

```bash
loopx reward-memory operator-control \
  --input reviewed-record.json --action retire \
  --control-ref control:example:retire \
  --reasoning-summary 'Current source truth supersedes this record.' \
  --format json
```

An edit checkpoint must match the corpus owner; a retirement checkpoint must
match `maintenance.retirement_authority`. Both checkpoints are also bound to
the exact corpus, project, and action. Edit produces a replacement
candidate linked to the active record. Retire produces a retired decision.
Neither command writes provider state. The declared corpus owner still performs
the write and exact readback, so operator control cannot silently become a
publish, production, or cross-project authority expansion.

## Outbound communication

The opt-in [outbound guidance integration](OUTBOUND.md) ([中文](OUTBOUND.zh-CN.md)) recalls reviewed
preferences at the actual goal/agent-bound Lark inbox send and reply boundary.
It returns guidance for agent review without granting send authority.
