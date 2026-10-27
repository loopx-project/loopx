# RFC: Controller / Executor Sessions and One Governed Work Contract (v0)

- **RFC status:** Accepted (design acceptance takes effect on merge)
- **Supersedes / closes:** none
- **Delivery maturity:** Proposal; no new runtime, schema or default is shipped by this document.
- **Authors / owners:** LoopX maintainers; control-plane and runtime owners
- **Created:** 2026-10-27
- **Last normative revision:** 2026-10-27
- **Implementation baseline:** `fe2b8dcbcb998647edb4bafb51932988bd7fdaa6`
- **Related contracts:** [Effect interpreter](agent-loop-effect-interpreter-v0.md), [Session execution modes](agent-session-execution-modes-v0.md), [Semantic handoff](capable-manager-semantic-handoff-v0.md), [Overall roadmap](loopx-overall-roadmap-v0.md)
- **Language mirror:** [中文版](controller-executor-unified-contract-v0.zh-CN.md)

## Document map and maintenance contract

The English and Chinese documents are semantic mirrors; a difference is a defect.

Section 4 records source facts at the named baseline. Sections 1–3 and 5–11
define the proposed decision and acceptance; section 12 records unresolved
choices. Before merge this is a review proposal. Merge accepts the design,
not implementation, installation, experiments or default promotion. Historical
evidence belongs in a dated ledger when an implementation first produces it.

## 1. Decision summary

1. Converge quota execution packets and TurnEnvelope into **one versioned
   governed work contract**: one schema, one semantic producer, one capture,
   one delivery/recovery mechanism and one settlement identity. Different hosts
   carry that same contract; permanent quota/Turn compatibility views are not
   the target. Combine quota packet's decision-necessary information with
   TurnEnvelope's delivery efficiency; replace both and qualify the net change
   against the original quota-packet path. Existing domain owners still decide
   their own facts.
2. In the optional two-session profile, the **controller model session is the
   sole model-facing Core client and an intelligent mediator of control work**.
   It reads the contract, reasons about work,
   talks to the executor and submits explicit typed Core operations. Core owns
   legal transitions and receipts; the controller is not a second authority.
3. The executor receives a natural-language task conversation, original source
   material and ordinary host tools. It returns findings, artifacts, evidence
   and questions. It neither consumes a second Core packet view nor authors
   quota, Vision, lease or settlement fields. A small machine carrier records
   routing and causality outside the task prose.
4. Full-first delivery, exact differential context and automatic full recovery
   are capabilities of the single contract. Controller and executor sessions
   have independent continuity evidence; one cannot borrow the other's baseline.
5. Unification first qualifies the existing single-session path. Two-session
   execution remains opt-in until matched outcome and complete-cost evidence
   justifies adoption. This RFC launches no benchmark or privileged operation.

## 2. Problem and motivation

A task can enter through App heartbeat, an external worker or `loopx turn`, yet
its context and result obligations are presented differently. Shared decision
facts already exist, but callers still choose packet formats, reconstruct
prompts and adapt model-authored bookkeeping. Adding another controller-specific
packet would multiply these paths.

For example, an executor asked to repair a failing export needs the original
requirements, the failure, permitted workspace and independent acceptance. It
does not need to learn how to encode a Vision delta or debit a LoopX Turn.
Separating that work may reduce distraction and repeated protocol input, but
an extra controller can also lose constraints, add latency or over-coordinate.
These are hypotheses to test, not benefits established by session separation.

### Invariants

- Preserve complete accepted requirements, revisions, authority, unresolved
  reads and evidence provenance; a shorter prompt is not permission to omit them.
- Exactly one current control owner and one admitted executor attempt for the
  selected work. Dialogue, routing ACKs and model completion are not receipts.
- Notifications do not govern execution. A quiet notification result cannot
  cancel admitted work; a message cannot override a genuine stop or budget gate.
- A governed Turn is a bounded work/settlement unit, not a wake, model response
  or controller/executor exchange. Do not charge a new Turn for each message.
- Preserve original identity through retry, source correction and closeout;
  never settle an old result against a newly selected Todo.

## 3. Scope and non-goals

In scope: contract convergence, context delivery, role-specific session binding,
controller/executor communication, shared lifecycle reuse, host adapters,
benchmark adoption, retirement and observable product acceptance.

Non-goals: a new Goal store, workflow DSL, generic effect executor, permission
engine, scheduler, mandatory two-Agent team, automatic host switching or changes
to benchmark scoring. Peer teams retain existing delegation/acceptance contracts.
This proposal does not move business reasoning into deterministic Core rules.

## 4. Current-system contract

| Existing concept | Actual role / source | Consequence for the target |
| --- | --- | --- |
| Quota packet / TurnEnvelope | [`quota.py`](../../../loopx/cli_commands/quota.py) projects a decided payload through separate ordinary/envelope branches; [`turn_envelope.ts`](../../../loopx/control_plane/quota/turn_envelope.ts) supplies bounded sections and action binding | Migrate both sets of obligations into one schema, then retire both old output paths |
| Work context | [`interaction_contract.ts`](../../../loopx/control_plane/work_items/interaction_contract.ts) carries typed execution/context requirements | Extend the existing owner; do not create a controller decision engine |
| Thin heartbeat | [`task_body.py`](../../../loopx/control_plane/heartbeat/task_body.py) renders product entry instructions with format-dependent branches | Keep the bootstrap/host entry; converge its consumed contract |
| `loopx turn` | [`turn.py`](../../../loopx/cli_commands/turn.py), [`executor.py`](../../../loopx/control_plane/turn_driver/executor.py), [`settlement.py`](../../../loopx/control_plane/turn_driver/settlement.py) implement governed invocation and receipt-bound closeout | Reuse the lifecycle; a CLI entry is not another product runtime |
| Managed Codex | [`codex_agent_session.py`](../../../loopx/control_plane/turn_driver/codex_agent_session.py) and [`codex_sessions.py`](../../../loopx/control_plane/turn_driver/codex_sessions.py) implement native session execution/resume | Runtime adapter, not a competing control plane; current Agent-scoped storage lacks the proposed role distinction |
| Executor result | [`codex_cli.py`](../../../loopx/control_plane/turn_driver/codex_cli.py) asks for a schema-constrained result including delivery and Vision/progress fields | Move Core authoring responsibility to the controller in the two-session profile; preserve validation |
| External worker | [`external_scheduler_worker.py`](../../../scripts/external_scheduler_worker.py) owns outer wake/command transport | Preserve that ownership; remove duplicate semantic orchestration where demonstrated |
| Benchmark runner | [`worker.py`](../../../benchmark/runtime/worker.py) uses the product thin body for heartbeat and public Turn CLI for Turn mode; [`sessions.py`](../../../benchmark/runtime/sessions.py) reuses the native session adapter | Evaluation harness around product profiles, not a separate implementation target |
| Turn Loop Controller | [Stable protocol](../../reference/protocols/turn-loop-controller-v0.md) defines a pure disposition function | Keep it deterministic; it is not the proposed controller model session |

Execution mode (`managed_runtime` / `attached_host`), runtime provider, wake
source, single/two-session organization and evaluation arm are independent
axes. Not every combination is supported. Native/plain benchmark controls may
intentionally have no LoopX Core and must remain uncontaminated controls.

### C1 source audit: shared owners and the remaining split

The source already shares more than the output names suggest. `_emit_quota_result`
captures the complete decision **before** `_project_quota_cli_payload` chooses
ordinary CLI compaction or `buildTurnEnvelope`. The latter delegates to the
TypeScript owner; its Python module is a transport adapter. Both quota capture
and Turn journal capture call `write_decision_capture` in
[`decision_capture.py`](../../../loopx/control_plane/turn_driver/decision_capture.py).
Native context delivery and explicit host handoff also reuse that module and
the existing context-delivery owner. C1 must converge the remaining composition
and consumer assumptions, rather than introduce another capture service or
rewrite already shared delta/native-resume logic.

| Current source / obligation | Present envelope treatment | Required C1 disposition |
| --- | --- | --- |
| `interaction_contract.agent_channel.work_context` and pending required reads | `work_context` plus `required_reads`; identical inline material can be referenced | One complete requirement/evidence source with resolvable references; preserve pending reads and their completion conditions |
| Selected Todo, recommended action, action portfolio and planning horizon | `action`; reference relocation and text compaction occur in `turnActionProjection` | One selected work/action identity; preserve complete actionable clauses and distinguish summaries from executable instructions |
| `interaction_contract.cli_channel.settlement_plan` | Copied into `writeback.settlement_plan`, including original effect identity and conditional steps | Reuse the settlement owner unchanged; neither controller dialogue nor a new capture creates another debit identity |
| User/agent interaction channels, execution policy and task orchestration | Separate `user`, `execution_policy`, `boundary` and orchestration sections | Preserve notification versus execution obligations, authority and stop gates; do not derive them from prose |
| Replan packet, required reads and staged writeback | Dedicated replan projection and deferred writeback | Preserve fact/source access, policy-specific triggers and ordered mutations; no blanket activation of stride behavior |
| Scheduler context and execution ownership | Scheduler projection with caller execution context | Carry the actual host owner and ACK obligations; a common schema does not transfer scheduler authority |
| Complete saved decision, action signature and journal binding | Source hash, projected action hash and captured-decision reference are distinct | Preserve the distinction between semantic integrity, exact transport bytes and receipt identity; no hash substitution |

This is an owner-and-obligation map, **not an exhaustive field inventory or a
parity result**. Current projections have bounded text, allowlists and relocated
references. Matching their action signatures alone cannot prove preservation of
all source clauses or unknown fields. C1 implementation must enumerate those
fields and callers against its exact base/head; anything unclassified holds
cutover, rather than disappearing into an assumed compatibility view.

A synthetic probe of the named baseline demonstrates the limit: two long
`recommended_action` strings with identical prefixes but different tails produce
the same action-signature document and both report `matches=true`, while their
`source_decision_hash` values differ. An unregistered top-level source field has
the same distinction. Conversely, a complete registered settlement plan survives
projection, and changing its Turn identity changes the action signature. These
observations reject promoting the existing bounded envelope unchanged into the
complete contract. They do **not** establish a current authority-loss bug: the
recommendation is diagnostic, the unknown field is synthetic, and the complete
source commitment still distinguishes both inputs. No model benefit is measured.

## 5. Proposed architecture

### Ownership and one contract

```text
Core owners <-> unified governed work contract <-> controller session
                                                       |
                                      task / findings / artifacts
                                                       |
                                                 executor session
                                                       |
                                              scoped host tools

Host adapters enforce dispatch, session fences, interruption and transport.
Core owners validate operations and commit existing effect/settlement receipts.
```

Use the existing TypeScript interaction/work-context and effect owners. The
composition owner captures their current facts once; it does not reimplement
quota, replan, Todo or lease decisions. Python may collect sources and adapt
native transport. Host-specific execution ownership remains explicit in the
same contract, including delegated scheduler effects and their ACK obligations.

The schema name and exact field migration require the task C1 inventory below.
The following are required semantic groups, **not additional nested protocols**:

| Group | Preserved capabilities |
| --- | --- |
| Identity and current decision | Contract version, capture/revision, Goal/Agent/work/Turn binding, selected action, admission, claim/lease, scope, approvals, cancellation and stop conditions |
| Complete work context | Original intent and requirements, unresolved obligations, accepted evidence, current sources/revisions, required-read completion and authorized detail access |
| Context delivery | Full/delta encoding, exact base/target digests, consumer session/role/epoch and immutable capture recovery |
| Operations and closeout | Available typed operations, action binding/integrity, conditional ordering, validation and writeback/debit identity, scheduler and notification ownership |

There is one semantic instance and one schema across CLI, heartbeat, managed
Turn and evaluation. Full versus delta is an encoding choice for that instance;
pagination is a transport limit. Neither creates a quota schema and a Turn
schema. Native tool wrappers may carry provider-required envelopes without
renaming, omitting or independently deciding contract fields. Human UI read
models remain presentation sinks, not alternate executable contracts.

The composition boundary is **after current domain decisions and host-operation
binding, before immutable capture and context encoding**. All live writers use
that boundary. They must not first compact a quota packet into an envelope and
then label the result the complete unified contract. Required source material
may use authorized detail references, but those references, revisions and read
obligations must be complete and recoverable from that same semantic capture.

“One capture” means one immutable semantic observation for an invocation, not
one file for every purpose. Keep the complete canonical instance, encoded
full/delta/page artifacts and the Turn journal's binding distinguishable. Exact
file-byte digests protect transport; canonical hashes protect semantic content;
the existing effect identity protects once-only settlement. None substitutes
for another or grants current authority. Reserve an identity/location before
serialization when recovery references need it; do not require a document to
contain the digest of its own final bytes. Encoding, pagination, native resume
and recovery derive from the saved instance without another guard. A legitimate
new selection or authority check may produce a new capture; it must not rewrite
an earlier observation or silently rebind an outstanding result.

### Controller intelligence and semantic compression

The controller session itself supplies intelligence. Its architectural role is
to turn the control plane's complete facts and obligations into useful work for
the executor, and interpret the executor's findings back into justified Core
operations. “Intelligent compressor” describes this mediation of control work;
fewer queries or shorter text alone do not implement it. The existing summary
already assigns reading, reasoning and typed operations to the controller. The
additional design choice here is **where decisions and attention are spent**:

| Decision | Controller responsibility | Retained boundary |
| --- | --- | --- |
| What to do next | Compare the current Goal gap, evidence, viable work and complete costs; continue a useful route or change it when warranted | Core still decides legal admission, claims, leases, budgets and transitions; controller judgment grants no authority |
| What to delegate together | Choose a coherent, verifiable task and decide which control concerns to absorb itself | The executor keeps local tool/step choice within scope; it does not request approval for each tool call or author Core bookkeeping |
| What the executor must know | Deliver the task's full constraints, relevant contrary evidence, original materials and unresolved reads; explain the decision in task language | A summary is not an alternate contract or proof of source reading. Unknown relevance requires more material before dependent work |
| What a result changes | Distinguish a finding, verified artifact, blocked attempt and accepted outcome; decide whether to continue, investigate or replan | Independent validation and current Core receipts, rather than either session's narrative, determine acceptance and settlement |

Keep three operations separate. The deterministic composition owner combines
domain facts without inventing policy; the existing codec encodes an exactly
recoverable full/delta input; the controller performs task-dependent semantic
selection and reasoning. A natural-language task can omit irrelevant control
syntax and cannot promise bit-for-bit equivalence. Its adequacy is tested by
preserved constraints, actual executor decisions and accepted outcomes, with
the original authorized material reachable. Do not replace these operations
with a second schema, model-authored state store or keyword-based relevance
classifier. Controller notes are working context, not a new durable authority.

For example, after an export repair produces a valid artifact but a packaging
check fails, the controller can keep the useful repair, delegate the evidenced
packaging correction and retain the original unsettled identity. It must not
reduce the result to “done”, discard the failed check or ask the executor to
learn writeback syntax. If the same evidence shows that the repair violates a
shared requirement, it must revise the task and deliver that requirement before
dependent work. This is a decision consequence, not a target compression ratio.

Amortization is a hypothesis. A controller can absorb several control-heavy
interactions while an executor continues ordinary authorized work, but every
new selection, permission change, cancellation and closeout still follows its
existing owner and freshness rules. Do not add a Core round trip or a new Turn
for each local tool step; do not cache an old permission to avoid a required
check. Small tasks, rapidly changing shared constraints and frequent controller
recovery can make the extra session cost more than it saves.

Controller failure must not make the executor a fallback Core client. Pause
dependent dispatch, recover the original capture and session/attempt records,
read current authority where required, and reconcile any already admitted
result through the original identity. An executor summary cannot reconstruct
missing control obligations. Conflicting or inaccessible required material
holds dependent work; independent authorized work keeps its existing route.

### Review freshness and recovery

A complete capture proves what was observed; it does not freeze write-time
validity. Keep semantic content identity, review relevance and settlement
identity distinct. A supporting outcome owned by another agent may still
constrain the shared Goal: owner labels alone cannot remove its acceptance or
requirement-status changes from the review basis. Narrow invalidation requires
an explicit dependency-scoping rule and tests for shared constraints, not a
role-name filter or an allowlist inferred from prose.

When the same admitted obligation rejects a stale review, its current typed
owner should return the complete current review basis and applicable control
instructions. The consumer reads and reassesses that basis, then retries the
original writeback with its original identities. Do not send it an unbound
write command or require another guard merely to retrieve facts already used
by the writer. A changed basis still rejects; returning context does not accept
a decision or authorize spending. This recovery belongs to the existing
composition/writeback boundary and must survive the C1 writer transition.
It is not a claim that cross-agent invalidation has been narrowed or that the
complete unified contract is delivered.

### Executor input and output

Choose **natural-language work exchange with runtime-owned metadata**, rather
than an executor-facing Core view or an untracked chat. The controller explains
the objective, relevant facts, boundaries and acceptance in task-appropriate
prose; it can steer, answer questions and revise a request. The host carries
opaque request/revision, attempt, session/generation, causal sequence and
artifact references using existing request/dispatch records. The model need
not reproduce those identifiers in prose. No new independent message ledger
is introduced.

Original requirements and authorized evidence are attached or accessible through
host material readers. Core capture and access checks are performed by the
controller/runtime; the executor does not call Core to retrieve them. Preserve
mandatory source reading through the actual material delivery/read mechanism;
controller paraphrase or a routing ACK is insufficient. Required material that
cannot be delivered blocks dependent execution; independent work may continue.
Untrusted source text never acquires instruction or tool authority.

The executor chooses its local tools and steps within the delegated scope. It
does not ask the controller before every tool use. Tool permission enforcement
belongs to the host/provider, including scoped filesystem/process access:
omitting a Core tool name from the prompt is not isolation. An unrestricted shell
with Core credentials would violate this profile. Unsupported isolation is
reported as unsupported, without silently claiming a qualified two-session mode.

Outputs preserve the requested deliverable, artifact references, observed
validation, failures and uncertainty. The controller interprets these results,
performs or requests independent verification and submits explicit typed Core
commands. Core never infers completion, permission or replan from substrings in
the executor prose. Host termination is not business completion. A failed
validation can lead to feedback and further work within the same admitted scope;
a material scope/revision change needs a newly qualified dispatch.

**Length is language-neutral and task-dependent.** Preserve independently
executable meaning before minimizing size. Initial experimental targets are
200–800 model tokens for an ordinary brief, 800–2000 for a complex handoff and
150–600 for a result summary. These are unvalidated starting points, not schema
limits, defaults, pass criteria or truncation rules. Count with the actual model
tokenizer; record language/content mix. Code, mathematics, tables, source material
and full requested deliverables retain their natural format. Measure summaries,
attachments, rereads and all exchanges together; moving text behind a reference
does not remove its cost. Small same-session corrections carry only the change
when continuity is proven, with no minimum length requirement.

For example, the controller can ask: “Repair the export failure shown in the
attached reproduction; preserve the original ordering requirement and stay in
the assigned workspace. Validate the regression and report remaining gaps.”
The response can explain the change and link the patch and independent test
result. Neither sentence is a substitute for original requirements, a permission
grant or a canonical completion receipt.

### Sessions, lifecycle and recovery

Start with one logical work Agent and two explicitly bound native session
roles. Do not create a second Goal/Todo/quota identity merely to split cognition.
Extend the existing session binding owner with role and generation, separate
ordered queues and separate context baselines; do not write two native IDs into
the current single Agent-scoped slot. One controller owns control dialogue;
at most one executor attempt owns the selected work. The controller role is not
another simultaneous business executor. Peer or parallel execution still needs
its existing independent claim/lease/handoff contract.

1. The existing entrypoint obtains fresh admission or resumes an owed receipt.
   It supplies the captured unified contract to the controller.
2. The controller reasons from the Goal, unmet requirements, results and feasible
   work; Core validates its chosen operation. Replan policy still controls when
   review is required. Fact-first reasoning does not turn a stride-only trigger
   into the default policy or require a gratuitous route change.
3. Before dispatch, the host binds work/request revision, attempt, session role,
   generation, scope and budget. Record intent before invoking the executor.
4. The executor works and returns observations/artifacts. Readback distinguishes
   delivery, execution, result availability, independent validation and accepted
   writeback. Dialogue can span several exchanges without multiple quota debits.
5. The controller proposes acceptance or revision; the existing effect owner
   validates and settles the original Turn exactly once. A fresh decision then
   continues runnable work, waits on the recorded event/time or stops.

| Failure | Required handling |
| --- | --- |
| Controller crashes after dispatch | Recover the persisted attempt and executor state/result before deciding to resend; do not start a duplicate executor |
| Result/settlement response is lost | Read existing receipts by original idempotency identity; retry only the unresolved effect |
| Executor restarts or context is compacted | Fence the old generation, requalify continuity and deliver complete current material if unknown |
| Cancellation, changed requirements, expired lease or late result | Revalidate current authority/revision before every owned dispatch/write; retain evidence, reject stale acceptance and reconcile admitted effects |
| Stop while a tool is running | Host interrupts within its supported contract; report acknowledgement and actual termination separately. Preserve unknown/in-flight effects; never claim physical rollback from a stop receipt |
| Controller is unavailable | Existing host/Core stop and budget mechanisms remain effective; hold dependent work and recover, without asking the executor to become Core owner |

### Differential context

Reuse the existing context-delivery owner and immutable capture mechanism.
Deliver complete context first. Send exact registered-subtree deltas only with
proven complete delivery and retained context in the exact consumer session,
role, epoch, schema and source capture. Preserve deletion, array order, null,
empty values and complete strings. Unknown fields remain fully delivered until
their reuse semantics are registered; send full if delta plus metadata is larger.

Current authority, admission, cancellation, unresolved-read, settlement and
scheduler facts stay fresh and inline even if unchanged. A warm transport never
relaxes the underlying instructions. Missing/out-of-order deltas, truncation,
compaction, corrupt or unknown baselines select full recovery from the same
capture automatically, without another admission/debit. Execution still checks
current source validity and authority; recovering an old capture cannot revive
cancelled work. The executor's task conversation is a separate consumer of
semantic material, not a relabeled delta of the controller's Core packet.

For cold delivery, use the current Agent's outcomes as the default expanded
scope. Other Agents' independent outcomes remain discoverable in an index with
identity, owner, objective, revision, work links, unmet requirement identities,
current review/check status and exact captured detail references. Global Goal
intent, shared requirements, permissions and stops always remain in the required
input. Expand cross-Agent dependencies, shared acceptance and material blockers
or conflicting evidence before the affected decision, regardless of ownership.
Goal-wide replanning and final acceptance widen the scope to every affected
outcome; a local delivery slice need not reread unrelated historical evidence.
Ownership is a default, not a permission boundary or sufficient relevance test.
Unbound work, unknown relations or unsupported source shapes retain complete
delivery until the existing typed owner can prove the narrower requirement set.

The canonical contract still retains complete requirements and evidence. Reuse
the existing exact-capture outcome indexing and staged replan reader as the
starting point for one ordinary/replan/detail pipeline, extending its index with
current decision facts rather than merely outcome counts. Preserve full text
for the required scope; defer supporting provenance and review detail through
integrity-bound reads with explicit ordering. A dependent decision must consume
its required detail first. Keep current conclusions, freshness and unresolved
requirements inline; an Agent review must not become an independent check.
Interface folding alone does not reduce model input. The delivery projection,
required-read owner and host recovery contract must migrate together; existing
captures retain their original complete-read obligations. Missing detail or
uncertain retention selects the original capture's supported recovery, never a
new admission or an assumed read receipt. Do not shorten retained requirements,
prune declaration origins or infer relevance from prose to meet a byte target.

Qualify this C2 change on the same cold, retained warm and context-loss sources:
default own work, cross-Agent dependency/constraint, unbound work, changed peer
evidence, broad replan and Goal acceptance. Count actual model input, paging and
detail round trips, recovery, latency and complete outcome cost. A smaller first
page is not a benefit when required follow-up reads or missed constraints erase
the saving. This is proposed delivery behavior; the current complete-source
reader remains authoritative until replacement and rollback are qualified.

The first reader prerequisite now has an actual ordinary CLI caller. With the
original capture file and its byte SHA-256, `quota context-read --outcome-ref`
pages an outcome index; supplying the exact returned reference pages that whole
outcome. Ordinary and staged replan reads use the same TypeScript registry and
index owner. The index includes owner/work and groups requirement identities by
identical current check/planning/review facts; missing facts remain unknown.
Equal bodies share a reference, while changed same-id revisions remain separate.
Full detail retains original descriptions, origins, review evidence and unknown
fields. Every page verifies the original file again. The returned continuation
also binds the selected JSON byte digest, refusing changed renderings under the
same original-file hash. Its cursor addresses the selected JSON, and it cannot
establish full-capture retention. This is a read
prerequisite, not the default own-Agent cutover: original full-read obligations,
host continuity and Goal authority remain unchanged.

The existing canonical acceptance read also supplies a versioned candidate
reading plan from the same complete work snapshot, without another authority
read or RPC. It includes the Agent's outcomes, selected work, transitive typed
dependencies/lineage, shared work/check scope, exact declared sources or review
evidence, and failed/stale observations with their related work. Unbound work,
unavailable relationships/lifetime and Goal-wide checks retain all outcomes.
The captured index labels `required_in_scope` only when the plan matches the
Goal/Agent/Todo, provider revision and complete acceptance-source digest. Changed
oracle facts, additional carrier versions or unsupported plans widen to full.
The original full-capture reader still returns `scope_applied=false`: its index
does not change that capture's full-read obligations. Ordinary acceptance
inspection retains its existing complete format.

The opt-in native transport now applies that plan jointly to required reads,
delivery and recovery. `outcome_context_projection_v0` identifies a derived
model presentation, never a partial canonical decision or another decision
owner. The original complete capture stays immutable and digest-bound. The
presentation expands all required outcomes and keeps every global field, plus
the complete current outcome index and exact original-source references.
Unknown optional fields, unresolved acceptance reads or unrecognized source
guidance retain full delivery. The existing interaction owner changes only its
exact fulfilled outcome-reading instruction; objective adoption, original and
cumulative requirements, work reconciliation, independent-check distinctions,
freshness, permissions and stops retain their obligations. No prose relevance
classifier is introduced. Registered relation closure still cannot prove
arbitrary semantic independence: read complete optional detail before relying
on it, and widen to the original full source when relevance is unknown or a
Goal-wide decision is needed.

The native candidate and compaction recovery point to the exact required model
artifact, rather than the machine's full canonical source. The existing delta
codec therefore delivers a newly required body's actual text; it cannot borrow
an unread optional body from a hidden baseline. The current full-source recovery
reference is published before optional scope composition, then replaced with
the exact required artifact before encoding. An interruption during composition
therefore cannot leave the preceding capture selected for recovery. Full page
coverage remains an
explicit native-hook continuity assumption, not model-retention proof. Optional
detail resolves through the same original-source registry, revalidating the
file and presentation; it does not acknowledge full-capture retention. Ordinary
transport-off and old captures retain their original behavior. Other Host
adapters, installed App adoption, actual model decisions and matched
accepted-outcome/full-cost qualification remain open.

On one 24-outcome / 253-requirement synthetic snapshot, 61 alternating warm
real-provider owner calls against the prior implementation measured File median
5.890→6.317 ms and SQLite 2.239→2.521 ms; p95 was 15.267→17.580 and
6.504→9.412 ms respectively. All prior response fields were identical; plan
metadata added 405/415 serialized bytes. This isolates the acceptance owner in
one TypeScript process, excluding bridge, CLI, cold startup and model costs.
That foundation added cost before the native scope cutover; it was not an
efficiency win. The native presentation additionally allocates an exact required
artifact and calls the same typed owner once before the existing codec. These
costs must be included with page, detail and recovery costs; a smaller first
input does not establish a net benefit.

The native slice passes 406 real-entry Python tests (five explicit native/SDK
cases unqualified), 169 TypeScript tests and four actual Codex
`0.163.0-alpha.11.1` deterministic-loopback checks. The latter inspect actual
model-request bytes for cold/full, retained delta and same-capture recovery;
they do not establish free-form model behavior. Four new File/SQLite scope
assertions fail at `1ac29089f`. An integration recovery-reference failure was
repaired by replacing metadata only for explicitly scoped artifacts, preserving
ordinary original-file byte identity.
An interruption regression first failed with the preceding capture still
selected; publishing current recovery before the optional scope call fixes it.
At runtime `e1c69289a`, standard premerge failed two checks also reproduced on `8b37000b3`:
the existing oversized `run_loopx_turn_once` decision function and stale
`turn_route` producer coverage after the earlier owner convergence. The native
increment did not change those files; that historical result is not a pass or
an exception. The integration was rebased onto `b69021498`, then onto
`f63647e2e` with the target-main native output-budget behavior retained. It incorporates
the actual TypeScript producer/scanner repair from [#7520](https://github.com/loopx-project/loopx/pull/7520).
It also isolates original-capture restoration from run-once orchestration at
`fec9cd3af`, preserving the existing capture owner and session recovery. The
size thresholds remain unchanged. Current combined qualification belongs to
the exact integration head's PR evidence, not the old native-slice test counts.

Final measurements include the recovery-publication fix at runtime commit
`e1c69289a`. Against immutable `8b37000b3` (the preceding C1 plus the same target-main large
page profile), three warmed cohorts per side supply 21 samples per provider and
phase on a 24-outcome / 253-requirement source. The warm case adds a real typed
dependency and verifies delivery of its previously unread body. Complete
serialized page output for File cold/warm/recovery is
105,380/40,473/103,372→37,908/33,495/38,594 bytes. Median latency is:

| Phase | File, before→after ms | SQLite, before→after ms |
| --- | ---: | ---: |
| Cold full input | 72.856→48.749 | 67.237→46.477 |
| Warm scope expansion | 38.744→51.944 | 35.953→52.165 |
| Lost-context full recovery | 109.126→61.425 | 109.725→56.777 |
| Index plus one optional complete detail | 96.918→108.557 | 92.460→109.251 |

The additional scope RPC remains: warm bridge calls rise from two to three.
File warm artifact files total 131,020→160,192 serialized bytes; the index/detail
operation still uses two calls and now revalidates the derived presentation.
Cold and recovery use one page instead of two. These measurements cover Python
native projection, pages and lifecycle with warmed runtime; machine load was
not controlled. They exclude initial acceptance collection, process startup,
child CPU, model/cache tokens, billing, human/setup work and accepted outcomes.
Cold/recovery transport improves while warm and detail medians regress. This
is not the original-quota comparison or a positive complete-cost verdict.

Small sources provide a counterexample to size savings: four real-entry
File/SQLite fixtures with two outcomes and four requirements grow from
36,293–40,548 to 37,948–42,203 bytes, exactly 1,655 bytes each. Index and scope
metadata exceed their short optional bodies. The proposal does not derive a
semantic omission rule or production size threshold from the larger fixture.

On the same 24-outcome / 253-requirement synthetic source, rendering the former
count-only index shape allocates 8,190 bytes; the staged state groups allocate
18,979 bytes. The old 12 KiB index budget fails. Retaining these decision facts
justifies a separate 20 KiB index ceiling; history, horizon, whole-envelope and
production performance limits stay unchanged. Grouping exact states avoids
repeating the same status fields for each requirement. These are serialized
bytes, not a token/latency/outcome benefit or an immutable base/head comparison.
The source-path scope checks and transport costs above do not qualify general
semantic independence or matched accepted-outcome/full-cost benefit.

## 6. Alternatives and design choices

| Alternative | Disposition |
| --- | --- |
| Keep quota and TurnEnvelope as permanent compatible views | Reject as the end state: duplicate selection/rendering and field-drift remain; retain only bounded migration decoders |
| Give the executor a compact Core packet view | Reject as the default two-session design: it retains protocol authoring load and two model-facing Core clients |
| Pure natural-language chat with no runtime binding | Reject: stale/duplicate results, cancellation and restart cannot be reliably reconciled |
| Always use two sessions | Reject: controller cost, latency and lossy delegation may exceed the benefit; keep a qualified single-session profile |
| One universal runtime/effect executor | Reject: host, scheduler, transaction and provider permissions differ; share semantics and receipts, not ownership |

## 7. Safety, privacy and compatibility

Controller reasoning grants no extra authority. Runtime credentials and Core
write capabilities remain inaccessible to the executor; scoped work artifacts
are delivered under current access rules. Permission revocation and cancellation
are enforced outside model memory. Sensitive source material stays within its
authorized audience and retention boundary, including model-provider selection.

Existing single-session behavior and optional-capability-off behavior need
parity. Unsupported profiles fail explicitly. Old persisted captures and receipts
retain their original version/signature and replay identity; never rewrite them
to resemble the new schema. Mixed-version live writers are fenced, not merged
by best-effort prose conversion. Schema removal requires the exact field inventory,
lossless mapping and explicit maintainer approval required by the RFC index.

## 8. Migration and rollback

### C1 wire-layout recommendation

The target combines quota packet's decision-necessary information with
TurnEnvelope's efficiency and replaces both. “Complete” means complete necessary
semantics, not every duplicated diagnostic in today's packet permanently inline.
The lossless root-preserving candidate below is an intermediate migration oracle:
it exposes omissions and reference breakage while consumers move. Renaming that
candidate or forwarding all old fields does not satisfy the final design.

Inventory each field and instruction clause with its owning rule and consumer.
Keep current task, original requirements and revisions, relevant evidence,
authority, required reads, execution/stop conditions and original settlement
obligations available before their dependent decision. Reuse typed ownership to
remove proven duplicate representations and unrelated diagnostic noise. Stable
material can be referenced or differentially delivered only with verified
retention or a resolvable, integrity-bound source and explicit read ordering;
missing context must recover before dependent work. A reference alone does not
prove that the model received its constraints. Unknown fields cannot be silently
classified as optional or dropped by a prose heuristic.

Within one canonical contract, absorb the useful envelope properties: clear
execution identity and action organization, a single machine-owned binding,
less duplicate instruction text, bounded delivery and reliable detail/recovery.
Full/delta/detail are encodings of that instance, not permanent competing semantic
views. Preserve raw source evidence in its existing owner without emitting a
second executable packet. The final wire layout may change where its mapped
references, consumers and measured benefit justify it; root preservation is not
an immutable product constraint.

| Source or concern | Candidate mapping and qualification |
| --- | --- |
| Current decision fields during migration | Preserve path/value in the lossless oracle; enumerate all modes and classify necessary semantics, proven duplication and diagnostic-only material before the final mapping. Do not infer optionality from one observed packet. |
| Work context, required reads and settlement | Keep their existing `interaction_contract` paths and owners. Existing root-relative references retain their meaning; any intentional relocation requires explicit reference and reader migration. Never rewrite prose or command strings by pattern. |
| Host-operation binding | Compose from the actual caller execution context. Validate consistency with source authority; reject conflicting bindings rather than choosing whichever representation is convenient. |
| Content and transport integrity | Commit the entire semantic instance independently of the former compact action digest. Preserve exact capture-byte verification and original settlement identity as separate checks; changing a transport encoding does not create a new effect. |
| Historical receipts and readers | Decode the recorded version for outstanding work. A temporary internal adapter may serve an existing effect interpreter, but must not become a second emitted contract or a compact replacement for the model's required input. |

A synthetic layout probe used real isolated File/SQLite quota captures and the
existing TypeScript context codec. Preserving source paths permitted warm delta
encoding; moving all facts under a new `facts` key while retaining the old
`quota_packet` surface fell back to full with `delta_not_smaller`. Both layouts
reconstructed exactly, and an epoch change required full delivery. Retention was
synthetically asserted; this measures codec behavior, not host observation,
model comprehension or outcome/cost benefit. It rejects **unmapped root relocation
under the existing surface**, not nesting in general. A redesigned nested layout
remains possible if its mapping and consumers justify the migration cost.

Therefore C1/C2 must explicitly version the context surface and its fresh/reuse
paths with the wire transition. Do not relabel an old surface or inherit its
baseline by Turn identity. The first delivery after transition is full; reuse
requires a verified compatible retained baseline. Qualification includes fields
outside the old action projection, missing baselines, session/epoch changes and
same-capture recovery. This resolves a concrete layout risk; shared writer and
final-consumer replacement remain the next implementation, not another layout
framework or a declaration that C1 is delivered.

The unfinished implementation candidate [#7430](https://github.com/loopx-project/loopx/pull/7430)
now composes a complete source in the existing TypeScript owner before both
quota and native Turn capture, and passes it to actual Codex stdin. Isolated
File/SQLite CLI tests with a synthetic executable cover source integrity,
failed-attempt recovery and once-only settlement. This advances the two-writer
and final-input replacement; it does not qualify C1 cutover. The candidate
now routes capability reentry through the canonical CLI channel and preserves
original hash-bound review/writeback readers in complete-contract Codex input.
Synthetic File/SQLite execution qualifies those readers for fresh obligation-bound
and prior Todo-bound replans. Native launch now derives its one causal binding
from the recorded settlement identity; an observed semantic replan obligation
cannot replace an existing Todo binding. Source-quota and health refusals still
prevent host launch. Release fixture setup now imports its final source once;
these executable checks do not qualify a real model or installed host.
The structured-host renderer now shares complete-input transport with Codex,
including original hash-bound material readers. Actual isolated File/SQLite
Turn-to-DSH runner paths receive that same captured source; historical request
prompts retain their exact content. Structured DSH still has Todo-scoped
sessions: an obligation-only replan is rejected before dispatch, rather than
inventing a Todo binding. Full-source input deliberately retains inactive
capability configuration and diagnostics; those facts do not activate recall
instructions or reflection result fields. This is a disclosed presentation
change, with activation and historical parity checked separately.
The Markdown quota entrypoints now render the same complete contract rather
than selecting a second subset of facts. Actual File/SQLite CLI captures retain
every field and instruction under both the ordinary and former envelope flag;
JSON types and arbitrary source text survive rendering. External-worker
File/SQLite admission, unresolved-attempt recovery and restart checks pass with
the complete producer output, but an opaque shell wake still does not prove
delivery of that original contract to an executor model.
The real benchmark checkpoint preflight now verifies complete source equality
for File/SQLite, replan/non-replan and context delivery on/off. Its optional
full recovery retains that same source, with one initial admission and no
reader writes. This qualifies transport to the checkpoint's model-input file,
not native session capture or a benchmark/model outcome. A matching outer
source hash alone previously admitted an unsupported version or invalid inner
binding through `replan-view`; the reader now uses the existing complete-contract
validator, while unversioned historical reads retain their decoder.
Successor authoring is now bound to the already-decided Turn before complete
capture, using the same formatting owner as historical staged reads. The complete
contract includes original Turn/Goal binding and the obligation to finish the
creation receipt's original settlement before ending, without executing the
successor. A later modern material read does not append another binding. Actual
File/SQLite App-heartbeat and generic-CLI tests create the successor from this
inline contract, reject premature spend, settle the original identity once and
leave the successor open, without another guard or a staged reader dependency.
This changes complete-input successor guidance; historical unversioned reads
retain their old behavior. Settlement-only, capability-intent, unbound and
mismatched-context inputs retain their authoring unchanged. The checkpoint's
legacy-reader tests alone do not establish this lifecycle result. Remaining
material-reader retirement and other C1 consumers must not restore a second
compact executable fact view merely to satisfy historical assertions.
The complete-contract boundary now rejects unknown, empty or mistyped root
execution actions, using the existing decision/frontier vocabularies both
before capture and on later reads. Valid hashes do not make unknown execution
semantics supported. Optional evidence remains intact, including unfamiliar
versions and action-like field names; historical unversioned action routing is
unchanged. This qualifies the root action boundary, not every required semantic
field. A real File/SQLite native-CLI producer fault is rejected before capture,
dispatch or spend. The preceding candidate reached a later launch refusal;
these probes do not show prior unguarded execution. Selected-work tests now consume the complete interaction context and
use the actual native `turn plan` entrypoint for host input. Real File/SQLite
checks retain full task text, canonical notes, scoped gates, fresh requirement
reads and source-loss recovery without rebinding an App capture to a native
host. The six earlier failures at obsolete root-layout assertions remain
historical failures; their downstream semantic checks now execute.
The six earlier staged-reader failures are retained as historical evidence.
The complete composer now reuses Explore's existing applicability correction
before capture: a proven pure replan without a Todo does not advertise the
Todo-bound result attachment. Evidence, independent graph commands and all
before-work obligations remain; unfamiliar/custom instructions and ambiguous
bindings stay intact. Supported Todo-bound attachment authoring remains fully
inline. This is an applicability correction, not an authority change or a new
compact fact view. Real File/SQLite native host inputs qualify both bindings.
The staged tests now consume the complete CLI input and exercise the historical
material-reader codec against that same immutable capture, including a hostile
PATH, unavailable registry, tampered identity/acceptance and the actual writer.
They compare all successor-binding instructions rather than excluding fields
that the complete input now owns. The attachment refusal fixture supplies a
valid explicit finding status so it reaches the real missing-Todo identity gate.
All 23 staged checks and four native host cases passed; these overlapping scopes
do not establish full consumer retirement or packaged-host qualification.
The native context fixture now binds both CLI spellings to `complete_turn` and
compares reconstructed facts with the original captures. Its four initial
failures came from constructing the restoration oracle with retired surfaces;
a Unix rendezvous check had masked the same mismatch by accepting full fallback.
The corrected checks require the original target and actual delta mode. Ten
cases pass through real Codex exec/app-server/code-mode and worker resume with
isolated SQLite and deterministic loopback inference, including compaction,
process resume, replaced history and an unqualified replay profile. No paid
model or benchmark experiment ran. The current full quota-settlement CLI module
passes 158 checks, including the historical cache-parity case; the earlier
intermittent mismatch is not causally explained or relabeled as a pass.
The File/SQLite context-to-DSH subprocess checks now use the complete capture and
production request builder instead of treating a current CLI response as a legacy
action projection. Normal capability-on/off inputs retain every source fact;
bound replan uses the production capture binder and executes the delivered
original-evidence readers. Altered source facts, schema versions and replan
obligations reject before the runner seam. The explicit historical reference
reader remains covered. Eight focused cases pass with an injected recorder,
not a live DSH model; runtime code is unchanged by this test migration.
The complete-contract boundary now also reuses the existing interaction decoder
for its required version, channel types, notification policy and execution
consistency. Four negative cases previously passed composition despite invalid
semantics; they now reject even when a producer supplies correct integrity
hashes. The original object and unknown optional facts remain intact rather than
being replaced with normalized decoder output. File/SQLite native input checks
reject an unsupported inner version before Codex prompt execution or DSH runner
loading, without spend. The complete-contract/context/DSH/stdio selection passes
352 cases; TypeScript checking and focused lint pass. This qualifies these
interaction semantics, not every required field or packaged-host adoption.
At this historical checkpoint, cache consistency investigation, other old reader expectations, staged instruction parity and
actual optional/external consumers, version admission and packaged host
continuity remain unfinished. Operation-SDK checks without an explicit native
runtime are untested, not qualified. The default writer change remains
unshipped; the PR is Draft for unfinished author work, not merely pending review. No model or cost benefit is
established. Historical effect decoding is temporary; it must not become a
second permanent executable view.

The external scheduler candidate now validates a versioned complete input through
`assert_complete_turn_decision` and the existing TypeScript complete-reader owner
before reading cadence or wake permission. This is a pure reader: it neither
recomposes the captured host binding nor creates a legacy executor view. Missing
bindings, unsupported versions/required semantics, altered content and invalid
same-capture references fail before the wake command. Unversioned ordinary quota
packets retain their existing compatibility path; retiring that path requires
reconciling supported deployed workers. File/SQLite source-CLI-to-worker tests use
local child markers, not model execution. This qualifies that input boundary,
not attached-host task delivery, settlement or accepted-outcome/full-cost benefit.

The optional Ark adapter reuses `extract_turn_authority` / `render_prompt` from
that same host-input owner; no optional-package contract builder is needed.
File/SQLite `turn plan --host generic-cli --iteration-context fresh` now feeds
its complete request through the real Ark SDK with a synthetic HTTP transport.
Checks compare the actual user-message contract with the original CLI source,
recover a persisted running receipt without a new input, replay completion
without provider calls, and reject altered source/version/binding or explicitly null
complete input before provider or receipt creation. Core state and spend remain
unchanged. Historical Ark request and recovery tests remain. This qualifies the
SDK wire boundary, not a deployed cloud session, model outcomes or Core settlement.
Ark and MemT now share the real File/SQLite CLI request fixture. MemT rejects
that complete input before provider IO because the executable legacy registry
supplies no exact `goal_ref`. The existing `source_session_v1` profile carries
an exact lifetime but is lifecycle-only: the real generic Turn planning entrypoint
rejects it before capture or execution. Supplying a synthetic reference after
planning would hide this producer/consumer gap. Complete-input corruption also
rejects before provider or receipt creation; historical MemT SDK/snapshot tests
remain, but they do not qualify end-to-end complete-input delivery. The owning
change on the MemT path must carry an admitted exact lifetime from an executable registry
through the shared Turn request and recovery owners, preserving recreation and
stale-instance rejection. Do not relax MemT's isolation or enable the
lifecycle-only profile as an adapter workaround. This is a demonstrated adoption
gap, not a qualified native-memory path. The operation-SDK native runtime and
packaged App adoption also remain unqualified. The internal effect envelope and historical request decoder
still serve settlement/result/session callers; remove them only after those
callers and outstanding receipts use the replacement, not merely because model
input no longer contains the old envelope.

New Codex/DSH requests now read their shared dispatch session lineage from the
complete contract, reusing the TS complete-input validator and adaptive primary
work owner. Model-input validation uses the same owner and rejects mismatched
Goal/Agent/Todo identities between complete input and the internal effect adapter,
even when both digests are independently valid. The complete path no longer
repeats Python version/signature validation; historical requests keep their
original decoder, persisted session schema, path and scope. Real File/SQLite CLI
capture, failed-attempt recovery and once-only settlement replay pass. Ordinary
and adaptive work restore existing Codex/DSH sessions under Todo and Agent
scopes. The same fixture preserves legal baseline paths but fails the added
identity-conflict cases before migration and passes on the candidate. Native
operation SDK environment tests still include skips; its runtime is not qualified.
Session identity migration does not retire all result qualification and settlement
readers of the internal envelope. Full retirement and net-cost
benefit remain unproven. The MemT lifetime gap blocks its dependent path only,
not these independent migrations.

Planning and failed-attempt session lookup now pass the original complete contract
through the same identity owner, including journal inspection. An omitted contract
still decodes historical records; an explicitly null contract rejects. Live and
cached vision-result normalization also use this shared identity read instead of
rebuilding Goal/Agent from the effect adapter. Session scope/lifetime fences and
the native app-server callback remain with their existing owners. This removes
those identity-only reads, not the full internal adapter: execution routing,
result evidence/profile interpretation and settlement still require migration.
Adaptive recovery fixtures are composed before signing: historical unverified
selections preserve primary-Todo writeback, while a conflicting verified task
source rejects before host execution or quota spend. The source fence is retained.
Completion-recovery qualification checks the original durable completion and spend
receipts without resubmitting completion. Four obsolete replay-call assertions
also failed on the prior baseline; their repaired oracle retains changed-task-text
and historical pre-source acceptance cases on real File/SQLite stores.

Completion task-body resolution now lives with the existing TypeScript interaction
source owner. New `run-once` requests validate the complete capture against the
internal adapter, then resolve and hash the complete contract's admitted source;
they no longer reconstruct that source from the adapter. Historical journals use
the same resolver through a thin Python transport. Exact Unicode/whitespace bytes,
missing/ambiguous-source refusals and accepted-receipt recovery remain covered.
This retires one Python semantic algorithm without introducing a second packet
view. The old 48-case File/SQLite capture/completion selection passes at
`599cf1f72`; candidate complete-source checks also reject altered bindings and
explicit null inputs. This adds a TypeScript boundary call where the historical
resolver was local Python; net full-path cost remains unmeasured. Result profiles,
evidence interpretation and other settlement consumers still need migration.

The Codex result schema and shared candidate adapter now use one checked
complete-contract profile read for evidence applicability. They no longer decide
that requirement from the internal capsule on new requests. The existing
TypeScript integrity/source/lineage validator serves both this read and model
input validation; no new packet view, wire field or per-evidence-field call is
introduced. Omitted contracts retain historical capsule decoding; explicit null
or altered contracts reject. Empty/non-object profiles preserve their previous
non-applicability, stop results still need no evidence, and supplied evidence is
passed unchanged to the existing result validator. This migrates applicability,
not evidence-content validation, execution routing or all settlement consumers.
Each schema/material-candidate read adds one checked boundary call; this is a
semantic convergence step, not an established performance improvement.
In a 31-pair warm-boundary measurement using one real SQLite CLI host request
(45,405 JSON bytes, synthetic provider), schema construction plus material-result
conversion rose from a median 0.013 ms at `68223e337` to 19.6 ms in the candidate
(p95 0.019/22.8 ms); legitimate outputs matched. This local added cost is retained
as negative evidence. It excludes cold startup, model work and the full Turn;
overall outcome/cost qualification remains open. Checks cover real File/SQLite
capture/recovery plus Codex/shared-host consumers; they do not prove packaged App
adoption or a model-outcome improvement.

The execution entry now checks complete-contract identity once before dispatch
or journal recovery. Settlement-plan fallback uses that checked identity, and
the recovery assessment reuses it within the same invocation instead of reading
the adapter's Agent again. Existing settlement plans and receipts are never
rebuilt, and omitted historical contracts retain their exact decoder. The value
is not cached across host attempts, source replacement or later invocations.
This also closes a recovery bypass: an existing settlement plan previously
skipped source validation, allowing a null/altered complete request to return an
existing journal outcome. On real File/SQLite CLI journals the new refusals leave
journal bytes and spend counts unchanged and invoke no host/settlement callbacks.
Ten targeted cases fail the new refusal assertions at `ce996ea0f`; the candidate's
32 capture/recovery cases and broader 605-case regression pass (overlapping).
The entry adds one checked boundary call, reused for both consumers. On the same
45,405-byte real SQLite CLI request, 31 alternating warm samples of the existing
settlement-plan read plus recovery identity preserve legitimate output parity:
median 0.016 to 12.382 ms, p95 0.024 to 14.883 ms. This is added local validation
cost, not an efficiency win; it excludes cold startup, full Turn and model costs.
Full-path performance and remaining routing/settlement retirement stay open.

Codex CLI prompt construction now delegates valid complete requests directly to
that existing complete validator, removing its redundant standalone envelope
version call. Historical requests retain their version check; a failed complete
validation still checks the version to preserve diagnostic precedence when both
inputs are malformed. No validation cache, new wire field or second owner is
introduced, and prompt text is unchanged. This is a call-boundary consolidation,
not retirement of the remaining driver routing or settlement adapter.

Against `e1dcd95dd`, complete and historical prompt bytes match, and six negative
input cases retain the same refusal diagnostics. On one 45,429-byte real SQLite
CLI request producing a 34,043-byte prompt, 61 alternating warm samples reduce
median prompt construction from 24.229 to 14.819 ms and p95 from 30.189 to
17.854 ms; bridge tracing confirms two calls become one. This local reduction
is about 39%, excluding cold startup, model execution and full-Turn cost. It does
not cancel the separately measured profile/settlement overhead or qualify the
original-quota outcome comparison. An earlier pre-diagnostic-refinement sample
was 21.279/12.994 ms median and 29.729/17.321 ms p95; retain it as earlier evidence,
not a second independent final qualification.

Planning now receives the complete contract before route/session/transaction
construction, rather than attaching it after the plan is built. Planning and
failed-session reconciliation share the existing TypeScript complete-source
validator through one coarse lineage operation. It selects Goal/Agent/work
identity and returns the action-signature preimage in the same boundary call;
it replaces the previous signature-only call, without a per-field RPC or cache.
Explicit null, unsupported or altered complete contracts reject, including an
already-resumed request. Omitted historical contracts keep their exact decoder.
The receipt owner still supplies Todo-less replan identity. The route and launch
increments below describe the subsequent consumer migration.

The Python JSON/hash transport codec remains deliberately unchanged. A direct
JavaScript hash trial failed numeric/exponent preimage parity and would have
changed persisted Turn keys; File/SQLite characterization now protects those
values, Unicode, adaptive work selection and resume idempotency. On one real
SQLite CLI request (45,380 JSON bytes), 31 alternating warm samples against
`088867421` preserve complete/historical plan and failed-session output parity.
Planning median/p95 changes from 39.632/47.964 to 42.608/53.270 ms; recovery changes
from 12.330/20.858 to 18.136/23.242 ms. These are local added validation costs,
not a full-Turn or original-quota outcome comparison. No model, installed App
or PostgreSQL authority qualification is implied. Remaining route/material
adapter retirement and end-to-end cost qualification keep C1 open.

The live Host launch recheck now composes the receipt-bound live observation
through the same complete-contract owner as initial planning, then supplies that
contract to the shared planner. This replaces its raw-quota-to-historical-plan
shortcut. Stop and changed-action refusals still precede composition; the live
observation neither replaces the original captured model input nor mints another
receipt. Remaining effect/material adapter retirement is still migration work.

The route classifier now lives in the existing TypeScript envelope owner. The
explicit registered action/frontier table is exhaustive at compile time; unknown
historical actions still reach the Host. Signature refusal, attempt, replan before
ordinary-delivery gating, capability intent identity, repair, user action and
quiet wait keep their ordering. Python retains a thin controller transport and
historical scalar-text encoding, not a second classification table. Complete
validation ignores historical decoding hints. The persisted Python action-hash
codec and historical selected-work reader are unchanged.

Planning receives the route in its existing coarse lineage/signature call;
complete and historical ordinary plans still use two bridge calls. Against the
previous Python classifier, 1,152 action/gate combinations agree; complete and
historical plan outputs also agree on a fixed real SQLite CLI fixture. These
planner comparisons use the same current TypeScript endpoint and therefore are
not an exact old/head end-to-end latency experiment. The standalone controller
route now needs one bridge call: 61 alternating warm samples on a 44,891-byte
request increase median/p95 from 0.004792/0.010125 to 13.252792/15.855042 ms.
That is a measured regression for this caller, not an efficiency gain. It remains
an explicit cost gap for controller boundary consolidation; no cache or duplicate
classifier is introduced to hide it. File/SQLite entry, recovery and once-only
settlement qualification, remaining adapter retirement, packaged adoption and
original-quota outcome/full-cost qualification remain separate acceptance work.

The production `turn managed-step` caller now combines envelope construction and
that same route evaluation in one TypeScript request. The Python adapter returns
a local immutable envelope snapshot paired with its route; this is neither a new
agent wire layout nor a second decision owner. Decoding or editing a returned
dictionary cannot change that snapshot or attach its qualification to another
wire dictionary. The controller retains its existing user-action precedence,
receipt/predecessor checks and journal-owned retry budget. Plain historical or
external envelopes still use the existing route reader and signature refusal.
The construction adapter shares host-binding and capture-argument preparation
with the ordinary envelope builder; the managed caller's redundant intermediate
builder has been removed. The standalone-reader regression above remains a
historical measurement; only the fresh production construction/consumption pair
can avoid that extra request. Remaining selected-work/effect/material decoding,
large cold-context recovery and packaged/outcome qualification remain open.
The local snapshot retires when this caller consumes the unified typed
controller result directly; it adds no persisted schema or compatibility wire.

On one fixed 31,088-byte synthetic source, 61 alternating warm samples compare
the previous Python producer/controller functions at `ddf96b79c` with the fused
call, using the same current TypeScript server and unchanged build/route rules.
The final-source component median/p95 is 22.477/25.417 versus 13.739/17.479 ms. A separate
public `managed-step` CLI comparison uses real isolated File/SQLite stores and
21 alternating warm samples per arm/provider: median is 135.989 versus 137.316 ms
for File and 149.310 versus 146.081 ms for SQLite; p95 is 209.730 versus 192.544 ms
and 222.299 versus 240.848 ms respectively. A separate transport-count probe
observes 70 versus 69 total bridge requests on each provider; this removes one
route request, not the other live-decision calls. Complete outputs agree, the failed
journal remains byte-identical and neither arm spends quota. Request count
improves; mixed whole-CLI medians and tails do not establish a speedup. Small local samples and
shared host resources limit tail inference. These are source-bound observations,
not an entire immutable old-tree comparison, cold-start, installed App, model
outcome or original-quota full-cost qualification.

Complete readers now also compare the adapter's signed action document with the
existing producer projection of the admitted complete source. Independent source
and adapter hashes previously proved only their separate integrity: a rehashed
adapter could retain Goal/Agent/Todo while changing execution or writeback facts.
The shared check covers the producer's existing signature dimensions without a
second field list, new RPC or wire view. Historical-only inputs keep their old
reader. Existing complete requests whose profile adapter disagrees with their
source now reject instead of silently reading the source profile; this deliberate
refusal change preserves the historical profile behavior separately.

Against `b9e2c5120`, File/SQLite planning refusal assertions fail before the fix.
Six independently rehashed variants cover run eligibility, effective action,
delivery permission, user intervention, execution policy and spend permission;
the candidate rejects them before host/writeback/spend callbacks and leaves no
Turn journal or settlement receipt. An existing lane lock can still be allocated.
On a 44,891-byte real SQLite CLI fixture, 101 alternating warm in-process
TypeScript samples preserve legitimate lineage output; median validation changes
from 4.864 to 5.163 ms and p95 from 5.570 to 6.010 ms. These are added local costs,
excluding bridge transport, live composition, cold startup, models and full Turn
cost. They do not qualify the original-quota outcome comparison or C1 cutover.

### C1 caller replacement and cutover unit

The cutover unit is a supported entrypoint **and its downstream consumers**,
not an output flag. Source inspection at `99921829f2c5` identifies the following
live seams. This supplements the earlier baseline audit; it is a migration
design, not a claim that these paths have changed.

| Live seam | Concrete replacement and retained owner |
| --- | --- |
| `quota.py::_emit_quota_result` / `_project_quota_cli_payload` | Compose the complete contract before capture; replace the ordinary/envelope execution-format branch. Retain admission, capability hooks, capture IO and host poll receipts with their current owners. A diagnostic status view is not an executable contract. |
| `turn.py` after `build_fresh_turn_decision_owner().resolve()` | Call the same composition owner with the actual scheduler execution context. This entry calls `build_turn_envelope` directly; changing quota CLI emission alone does not migrate Turn. Retain the Turn plan, journal and receipt executor. |
| `heartbeat/task_body.py::render_thin_heartbeat_task_body` | Replace the `captured_envelope` instruction split with one contract-reading instruction. Preserve exact selection/reentry transport, notification independence, required reads and original settlement. The saved bootstrap still resolves current instructions on wake. |
| `turn_driver/codex_cli.py::_prompt` and `host_candidate.py::extract_turn_authority` / `render_prompt` | Replace format-specific model inputs at the actual prompt boundary. Today ordinary Codex receives the envelope, staged replan uses extracted authority, and structured hosts use extracted authority. Shared source bytes or signatures do not prove these final inputs preserve the same obligations. Keep provider result encoding and outer effect ownership. |
| `turn_driver/decision_capture.py` and `cli_commands/quota_capture.py` | Reuse immutable capture, validated readers and full/delta delivery. Fresh delivery and saved-capture recovery must reconstruct the same contract; do not reuse a continuity baseline for a different projection merely because its Turn identity matches. |
| `benchmark/runtime/worker.py`, `benchmark/runtime/sessions.py` and `scripts/external_scheduler_worker.py` | Migrate the product entry they actually invoke. Keep evaluation setup and outer wake ownership; do not add benchmark-only conversion or count a managed-host test as attached-host acceptance. |

Before replacing any seam, its implementation PR records source path, field or
instruction clause, current readers, target owner, representation, recovery and
retirement condition. Relocation must include reference resolution; an omitted
diagnostic needs evidence that it carries no action, authority, required read or
acceptance obligation. Unknown data cannot silently disappear. Unknown required
execution semantics cannot be made safe merely by forwarding their JSON: the
dependent operation stays unavailable until a compatible consumer understands
them. Do not classify obligations by substring matching.

Use **one explicit version transition**, not automatic format negotiation per
wake. New admission for a migrated binding requires compatible composition,
delivery and consuming readers. An unsupported consumer receives an actionable
unavailable result before dispatch, without creating a second live writer.
Already admitted work continues its recorded version and original receipts;
retain its historical decoder until that work and the supported recovery window
are reconciled. Rollback likewise separates outstanding work from new admission.
A version transition does not rewrite saved prompts, journals or source hashes
as if they had always used the new contract.

Code convergence is part of each migration slice, not only the final C9 cleanup.
Both entrypoints must call the same semantic implementation for composition,
reference validation, context reconstruction and settlement. Keep genuinely
host-specific launch/observation mechanics in thin adapters; do not force their
different lifecycles into a new general-purpose runner. The existing typed owners
remain responsible for their respective decisions.

For each slice, record the before/after callsites, shared owner, removed duplicate
algorithm and remaining compatibility caller with its removal condition. Qualify
both routes with the same independently authored semantic cases and their real
transport/recovery tests. A wrapper that only delegates can be transitional;
copying a rule into a newly named common module while leaving two implementations
is not convergence. The next change to a shared rule should be made and tested in
one owner and reach both routes. Do not make declining line counts an acceptance
metric or remove unique host coverage to meet one.

The first C1 PR must include the two production composition callsites above and
at least one complete producer-to-final-input journey, with an explicit list of
remaining readers. Qualifying that bounded slice does not enable the new writer
for an unqualified host or close C1. The final cutover removes active format
choice only after all supported readers qualify. Temporary decoders have named
callers and removal conditions; they are not user-facing compatibility views.

For each normal, selection, replan, wait, recovery and capability-off fixture,
check independently authored obligations at **three boundaries**: composed
contract, reconstructed delivery, and final model input plus required material
reads. Include a source change outside the old action signature, a missing
required source, an incompatible consumer and a lost settlement response.
Reconstruction equality alone misses prompt-layer omission; a complete prompt
alone misses stale authority or a duplicate debit. These are integration
requirements, not authorization to run a model or launch an experiment.

1. Inventory actual producers, readers, writers, saved prompts, captures and
   external integrations. Map each old field and prose obligation to its retained
   owner/field, authoritative derivation or explicitly approved removal. No
   blanket claim of “same facts” suffices. Keep original failure fixtures.
2. Qualify the unified contract in single-session execution through real quota,
   thin-heartbeat and Turn entrypoints. Switch one writer per binding; old
   readers are explicit version-pinned migration adapters with an identified
   caller and removal condition. They do not become permanent product views.
3. Add role-aware sessions, shared lifecycle reuse and the opt-in two-session
   profile. Reuse existing same-session/native-resume and effect recovery code.
4. Adopt the shared product profile in benchmark workers and the packaged App
   heartbeat journey. Each host proves its own observation, stop and recovery
   boundaries. Neither a CLI test nor a model simulation qualifies installed App.
5. Remove old producers, format selectors, duplicate renderers and dual-format
   tests after the last supported active reader migrates. Keep only historical
   version decoders for supported receipt replay. End-state acceptance requires
   one emitted contract and no choice between quota and Turn packet formats.

Rollback disables new admission for the affected profile, drains or interrupts
its attempts, reconciles receipts and restores the prior qualified profile on
the next admitted work. Do not silently replace native sessions or reinterpret
pending receipts. Before field/decoder removal, demonstrate export, old-version
readback and downgrade behavior; retain the old reader until the supported
retention/upgrade boundary is satisfied. No destructive migration is authorized
by this document alone.

## 9. Validation and acceptance

| Claim | Evidence and required result | Boundary |
| --- | --- | --- |
| One complete contract | Field/clause inventory; same captured normal, replan, wait, recovery and capability-off workloads through all active entrypoints; identical reconstructed semantic instance | One serializer unit test is insufficient; malformed/unsupported versions fail explicitly |
| Lossless context delivery | Full/delta/recovery equivalence, missing/late/truncated pages, role/session mismatch and real native compaction/resume | Include all read/recovery costs; no second admission or spend |
| Role isolation and recovery | Controller/executor crash windows, duplicate dispatch, late result, revision change, lease expiry and stop under actual host tools | No executor Core access; no stale result adoption or hidden business executor |
| Semantic adoption | Original requirements → controller decision → actual executor work → independent verification → accepted result and original-audience return | Read receipt, valid JSON or successful replan alone cannot pass |
| Intelligent mediation | Coherent delegation, changed/contrary evidence and result interpretation across ordinary continuation, shared-constraint change, controller loss and a small-task counterexample | Explain the actual decision consequence and retained obligations; fewer queries or a shorter handoff cannot pass |
| Shared production paths | Real CLI and supported File/SQLite paths; real isolated PostgreSQL suite when authority persistence changes; packaged App heartbeat and benchmark worker reuse | No live Goal corruption; unsupported/unrun hosts stay unqualified |
| Outcome and cost benefit | Matched current single-session, unified single-session and unified two-session arms with fixed task/model/policy/budget/evaluator | Report uncertainty, failures and counterexamples; no claim from prompt bytes alone |

The primary C1 comparison is **original quota packet versus the fused contract
in the same single-session product path**. Keep host, task, original facts,
model/policy, evaluator and available budget matched. Compare cold full input,
ordinary warm continuation, replan, missing baseline and interrupted recovery;
include long/repeated history and capability-off cases. The old envelope may be
a diagnostic reference arm, not a second permanent destination. Compare the new
full encoding and its delta encoding separately so transport reuse cannot hide
a semantic regression. Controller/executor separation is a later independent
comparison and cannot explain away a C1 regression.

### C1 baseline boundary and reuse inventory

The baseline is the **original caller-visible quota delivery**, not the raw
internal decision. In `cli_commands/quota.py`, the original emitter captures the
source, optionally applies `replan_cli_view` when a capture exists, then applies
`compact_quota_should_run_cli_payload`. Native context delivery adds its own
full/delta transport afterward. Record the exact revision, host flags, capture
availability and detail options for both arms. Comparing a compact candidate
against the larger internal source would overstate savings; comparing a full
candidate against only a deferred entry would omit baseline follow-up costs.

The existing paths already contain useful efficiency behavior. C1 should absorb
its qualified semantics into the shared TypeScript composition/context owners,
then retire the old caller-specific producer. Do not call the whole legacy
compactor as a shortcut: exact sharing, omission and deferred reads have different
information and failure consequences.

| Existing information / transformation | Fusion disposition and decisive check |
| --- | --- |
| Selected work, original requirements, acceptance, evidence, permissions and stops | Available before the dependent decision. Keep distinct revisions and contradictory evidence; neither lane labels nor action signatures establish completeness. |
| Identical acceptance copies, nested vision audits, authoring and optional guidance | Prefer one complete in-contract owner plus an exact reference where smaller. Compare JSON types and every clause; different/missing owners remain distinct. No extra read or new decision authority. Existing projection tests provide characterization, not new-consumer qualification. |
| Bounded Todo lanes, shortened suggestions and omitted boundary/history entries | Treat as deliberate omission, not lossless deduplication. Retain completeness/count facts and same-capture recovery; prove the omitted item cannot change the current decision, or load it before deciding. Never inherit the old display limits as semantic limits. |
| Captured replan review, acceptance detail and writeback readers | Separate decision facts from later authoring format while retaining the original identity and all required meaning. Bound reads to the same capture; count every required stage, error and recovery. A missing/stale reference must not trigger a new guard or authorize work. |
| Full/delta/resume delivery | Reuse the existing continuity owner after composition. Establish exact semantic reconstruction and required material availability before action; charge cold fallback and lost-baseline recovery. |
| Scheduler, admission and settlement identities | One authoritative instance before capture; transport must preserve host ownership, original Turn and once-only effects. Repeated representations do not justify weakening their checks. |

A fixed-source synthetic replan audit of candidate `895f38fc5` illustrates why
the boundary matters. With stride off and no capture staging, the raw source was
21,461 compact UTF-8 JSON bytes, the original quota CLI projection 20,543, and
the complete candidate 21,738: **+5.82% against CLI**, versus +1.29% against raw
source. With capture staging, entry/review/writeback totaled 21,882 bytes versus
21,821 for the candidate. With `evidence_conditioned_v1`, those staged bytes
totaled 28,558 versus 33,368 for the candidate. These use the existing synthetic
replan fixture and production projection/read functions with a synthetic capture
route; they are not executed model trajectories or an installed-host trial.
Extra acceptance/Explore reads, actual request framing, cache behavior and
outcome differences are unmeasured. The legacy receipt toggle produced identical
data and does not add an independent workload. No net-benefit conclusion follows.

The acceptance-sharing increment in #7430 moves the original Python display
algorithm into the existing TypeScript composition owner, before hashing/capture;
the old display helper now delegates to that same implementation. Complete readers
and historical staged reads use one reference resolver. Equal contracts retain
one full owner; different revisions, JSON types, provenance and negative evidence
remain distinct. Existing references follow relocated owners. Dangling, cyclic
or unavailable references reject even with valid integrity hashes. Small objects
stay inline when a reference would cost more.

Isolated File/SQLite quota and native-host fixtures cover complete acceptance at
the actual synthetic Codex subprocess input, failed-attempt recovery and original
once-only settlement; shared full/delta/lost-baseline reconstruction is also
exercised. These qualify transport semantics, not model compliance. The legacy
display adapter adds a transport call; complete composition shares inside its
existing call. No latency improvement is claimed.

A fixed-source acceptance fixture with four identical carriers produces 23,432
compact UTF-8 JSON bytes for the fused contract versus 22,237 for the original
non-staged CLI (+5.37%). With capture staging, fused bytes are 23,515 versus
24,509 for original entry/review/writeback together, excluding extra material
reads. The original display-sharing helper is frozen at `895f38fc5` for this
comparison. The acceptance-free workload above remains unchanged. This absorbs
an existing quota efficiency into the common path; it does not establish an
advantage over quota or full-cost benefit. The subsequent guidance increment below narrows that gap; next qualify the remaining
vision sharing in its existing owner after the title increment below, before deferred-detail choices. Keep cold and staged totals separate and retain negative results; do
not force a byte target by dropping unique instructions.

The next #7430 increment shares replan vision-authoring instructions through
that same TypeScript composition boundary. Its retained owner is the existing
durable-writeback settlement step. The legacy Python display helper delegates
sharing and only adds its historical presentation metadata. The complete
reader and historical unstaged/staged adapters resolve the same exact reference;
no second authoring policy is introduced. JSON type, revision or instruction
differences stay inline, and a reference must be smaller. Conflicting inline/ref
values, wrong step kinds, missing targets and chains reject even if hashes match.
The settlement plan, all original instructions and the example remain intact in
the capture; the historical writeback stage retains its existing example omission.

Two old CLI spellings now expose the same shared complete contract; material
readback, rejection before effects and native input are qualified separately.
The exercised native outer-controller inputs lack an inline settlement authoring
owner, so their authoring
remains inline; sharing never invents that owner or transfers settlement authority.
At the authoring checkpoint, four legacy projection tests expected multiline-title
or Explore-guidance sharing absent from the complete candidate and failed at both
the preceding and authoring heads. The guidance increment below resolves two;
the title increment below resolves the other two. Historical failures remain recorded, not waived or relabeled.
These slices do not qualify installed App adoption.

For the fixed synthetic authoring fixture, the original non-staged CLI is 22,062
compact UTF-8 JSON bytes and the fused contract is 23,266 (+5.46%). With capture
staging, original entry/review/writeback total 24,311 versus 23,349 fused bytes.
The original CLI projection module is frozen at `895f38fc5`; inputs and projected
read stages are held fixed. Additional reads, request framing, tokens/cache,
latency and model outcomes remain unmeasured. Sharing eliminates a duplicate
implementation and duplicate instructions in the candidate, but does not prove
an improvement over quota's existing sharing or a positive full-cost diff.


The Explore guidance increment removes the Python exact-match loop and reuses the
same TypeScript composition boundary for complete and legacy display callers.
Only identical instructions with an existing durable-writeback Explore attachment
share that same-capture owner when the reference is smaller. Unique/short text and
missing owners stay inline. Invalid, conflicting or chained references reject;
no instruction is shortened and an optional attachment gains no new obligation.
Historical staged and unstaged readers resolve the reference before moving context,
then reuse Explore's existing domain projection. Independent Explore reads still
return full guidance. The compatibility helper retires with the legacy display
callers; it owns no separate decision or sharing algorithm.

Real isolated File/SQLite CLI captures now satisfy the two previously failing
Explore-sharing cases, including standalone material comparison, historical
readback and invalid-capture refusal without state/spend changes. Existing staged
CLI tests still exercise unavailable-registry reads, tamper refusal and actual
writeback/settlement. Delta reconstruction preserves changed instructions and
lost-baseline recovery; incomplete context falls back to full delivery. These are
transport and local integration results, not installed App or model-outcome proof.


Broader caller qualification also migrates old test assumptions to the admitted
complete contract: native subprocess capture/retry compares the entire original
contract, and generated settlement commands still settle only the original Turn.
Historical capability readers retain explicit canonical-fixture initialization,
refusal facts and scheduler information without granting execution authority.
Rejected capability admissions still emit their typed diagnostic/historical
projection, whereas admitted calls emit the complete contract. This remaining
failure-path migration belongs to C1 before full cutover; it is not a permanent
second contract. Synthetic native/stdio providers do not qualify model benefit.

With the same original projection frozen at `895f38fc5`, the synthetic guidance
fixture uses 22,848 compact UTF-8 JSON bytes on the original non-staged path and
24,320 fused (+6.44%). Captured original entry/review/writeback total 26,081 versus
24,403 fused bytes. The guidance-free fixture remains 20,543 versus 21,738 bytes
non-staged. Required extra reads, framing, tokens/cache, latency and accepted model
outcomes remain unmeasured. Shared code and restored quota information efficiency
do not establish a positive complete-cost diff; C1 remains open.

The current-Task title increment removes the Python exact-source matching algorithm.
Complete composition and the legacy display delegate now share its TypeScript
owner. Only an exact duplicate of `text` or `title` in the unique, verified,
complete current selected Task can become a same-capture `title_ref`; the full
original declaration and its provenance stay inline. Missing, differing,
incomplete, ambiguous, failed, done or archived sources do not qualify. The
historical character threshold, including Unicode code points, stays unchanged.
The old display-only label-prefix omission remains local and is not applied to
the complete contract. This delegate retires with the legacy display callers.

Complete readers validate identity and reference ownership again, rejecting
conflicting inline values, chains, malformed paths and unavailable targets even
with valid hashes. Historical envelopes retain the full Task source, without a
relocated dangling lane reference. Real File/SQLite CLI with differential delivery
off now passes the two previous multiline-title cases; captured variants also
verify exact readback and refusal before state mutation or spend. Delta and
lost-baseline/full recovery preserve changed requirements. Existing independent
Todo detail still returns the full declaration. This qualifies local transport
semantics, not installed App adoption or model behavior.

For a fixed synthetic current-Task fixture, the original non-staged CLI frozen
at `895f38fc5` is 28,585 compact UTF-8 JSON bytes and the fused contract is 29,780
(+4.18%). Captured original entry/review/writeback total 29,906 versus 29,863 fused
bytes. The no-current-Task control remains 20,543 versus 21,738 non-staged bytes.
These totals exclude request framing, extra material reads, tokens/cache, latency
and accepted outcomes. They restore quota's existing sharing in the common path;
they do not establish positive full-cost benefit.

The vision-audit increment now moves the nested exact-match algorithm from Python
into that same TypeScript owner. Complete and historical display callers share
only an identical full audit, including unknown facts, negative evidence and
ordered obligations, when its reference is smaller. Different JSON types are not
equal: the old Python `True == 1` comparison could erase a channel difference;
that case now stays inline. Short audits also stay inline. The root retains the
complete audit and judge; legacy display metadata remains in its adapter.

Complete recovery rejects missing, malformed, chained or conflicting audit/judge
references even with recomputed valid hashes. Existing historical envelopes still
obtain their judge from the inline root, so no moved reference dangles; their
existing projection limits are unchanged. Real isolated File/SQLite CLI captures
with an active prior vision verify the full judge, exact capture readback and
rejection before state mutation or spend, without differential delivery enabled.
Delta/full and lost-baseline recovery retain changed requirements. Fixtures with
no active vision correctly emit no audit and cannot qualify this path.

With the original CLI projection frozen at `895f38fc5`, the fixed synthetic audit
fixture uses 22,337 non-staged compact UTF-8 JSON bytes versus 23,333 fused (+4.46%).
Captured original entry/review/writeback total 23,658 versus 23,416 fused bytes.
The no-audit control remains 20,543 versus 21,738 non-staged bytes. These are not
tokens/cache, latency, extra reads, framing or accepted outcomes. Restored sharing
and fewer rule owners do not prove positive full-cost benefit. The legacy audit
delegate retires with its display callers; remaining consumer retirement and
packaged/outcome qualification still keep C1 open.

Rejected admission now uses the same complete composer before immutable capture
when the decision has an interaction contract, including an unhealthy paused
source. A false `ok` still prevents host handoff and native context-baseline
publication and retains the failing exit status. Composition grants no delivery
or settlement authority. Invalid complete sources fail validation before capture.
Errors before a work contract exists retain their original typed diagnostic and
recovery facts, with either CLI flag spelling; they do not acquire a manufactured
work contract or pass through the historical envelope renderer.

Isolated real File/SQLite CLI checks cover missing work, unavailable capability
and selection-required reentry under both App-heartbeat and generic profiles.
They preserve the original receipt on reentry and create no spend, baseline or
unauthorized state mutation. Capture consumers are being moved to exact complete
readback while keeping explicit historical long-route recovery coverage. This
qualifies local transport and refusal semantics, not installed App adoption or
all-provider/full-path delivery. Remaining consumer retirement and packaged
single-session outcome/cost qualification still keep C1 open.

Subsequent observation qualification separates two issues. #7464 repairs the
cache test's comparison of independent live observation clocks while retaining
all other facts. #7468 preserves the existing authority owner's typed rejection
and backup/migration recovery in `quota status/plan`, whose early failure return
had hidden that information. Unmigrated ordinary reads remain refused under the
accepted #7032 cutoff; File/SQLite retain their scope-isolation checks. The new
recovery assertions fail on the previous implementation and the corrected
scope/diagnostic/authority selection passes 156 tests. These independently
reviewable fixes are absorbed by the integration candidate, not evidence of
installed adoption or improved outcomes/cost. Earlier failed runs remain
historical evidence; these diagnosed failures are no longer open investigations.

For an unhealthy-paused synthetic source, the original CLI projection frozen at
`895f38fc5` uses 6,325 compact UTF-8 JSON bytes and the complete contract uses
6,639 (+4.96%). The extra binding preserves common validation; this is no efficiency
win. Tokens/cache, latency, extra reads, framing and accepted outcomes remain
unmeasured for this case. No live model experiment is implied by these checks.

The shared-owner prerequisite is independently reviewable in #7457. Its
Task-title and Explore operations use one coarse legacy projection call; complete
composition reuses those same functions inside its existing call. The integration
branch absorbs the prerequisite commits rather than carrying a copied owner.
New `should-run` emission accepts the already-composed contract or original
predecision diagnostic only; it no longer falls back to legacy packet/envelope
projection. Historical capture recovery remains explicit. Component measurements
show reduced bridge cost versus two calls, but higher cost than the original
Python projection; this is not evidence of a positive full-path quota comparison.

Report the field/clause diff and its decision consequence together with actual
model input, cached/uncached tokens, added reads/round trips, reconstruction,
latency, failures/retries, verification and human intervention. A smaller first
packet with more total reads can be negative. Judge positive only with preserved
required semantics and comparable accepted outcomes at lower complete cost, or
better accepted outcomes at comparable budget. Mixed results remain a tradeoff;
transport-only passes and unmeasured outcomes remain inconclusive. Set numeric
criteria before running matched experiments, using the existing measurement
contract rather than inventing an arbitrary compression threshold. C1 cutover
requires this quota-baseline comparison; a serializer or byte reduction alone
cannot close it.

Use the [existing measurement model](long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model).
Include both sessions' input/output/cached tokens, tool and material reads,
planning/replan/recovery, wall time, verification, human attention and provider
cost; unknown usage is not zero. Persist causal attribution without duplicating
cost when an artifact is forwarded. Compare accepted outcomes under comparable
budgets, not only replan counts or executor speed. Freeze criteria before any
separately authorized experiment; this RFC specifies no automatic winner.

For the later two-session comparison, attribute controller reasoning and all
handoff/material, validation and recovery costs as well as executor work. The
break-even question is whether those **added complete costs** are outweighed by
avoided control/bookkeeping work, repeated execution or human intervention,
while preserving comparable accepted outcomes. Account for actual repeated
model consumption even when both sessions read the same artifact; forwarding
one usage receipt must not double-count that receipt. Report workload-specific
tradeoffs and unknown billing/cache coverage. No universal overhead threshold,
compression ratio or per-message token ceiling is proposed. This comparison
does not replace the prior original-quota versus unified single-session gate.

## 10. Operational contract

Existing Goal Chat, settings and work readback must expose the selected profile,
actual controller/executor availability, current work, meaningful progress,
unavailable/error state, scope of Stop and returned artifact. Avoid exposing
internal packet selection as a user task. Reuse shared typed projections and
configuration precedence; do not create an independent controller console or
duplicate provider/model settings.

Observe captures, request/attempt/role/generation, current revisions, effect
receipts and cost coverage in existing diagnostics. Give operators actionable
recovery without raw private transcripts. Notifications remain separate from
work continuation; event/time waits and backoff use the current scheduler owner.
No model busy-polling loop or new recurring automation follows from this RFC.

## 11. Normative delivery plan and refactor tasks

These tasks extend roadmap S2/S4/S6/S7/S11 and the effect interpreter's existing
convergence work, not a new roadmap. Reconcile related active tasks/PRs before
claiming; reuse qualified fixes rather than reimplementing them. Task IDs below
are stable design references, not claims that a canonical Todo is complete.

| Task / dependency | Owner and concrete replacement | Decisive exit / rollback |
| --- | --- | --- |
| **C1 — Unified contract; first** | Interaction/work-context + quota/Turn owners: inventory fields and instruction clauses, capture one schema, migrate actual ordinary quota/Turn producers and thin renderer | Single-session real-entry parity with necessary information, action binding and once-only settlement, plus the quota-baseline effectiveness/full-cost comparison in §9; retain version-pinned readers until cutover qualifies |
| **C2 — One context-delivery pipeline; C1** | Context owner + host observers: absorb full/delta/detail/recovery into the canonical contract; default to own outcomes plus global constraints and relevant cross-Agent material, with an exact detail index; remove packet-format branches and duplicate context copies | Cold/warm/context-loss and normal/replan/resume preserve required text, cross-Agent adoption and exact role/session continuity at measured complete cost; unsupported scope falls back to full without new admission; rollback encoding to full |
| **C3 — Session binding and native lifecycle; C1** | Session/runtime owner: extend existing binding for role/generation; reuse native create/resume/observe/interrupt and ordered queues | Two roles cannot overwrite the Agent native ID, cross-read baselines or race execution; recovery observes existing attempts before retry; disable new role admission to roll back |
| **C4 — Shared governed lifecycle; C1** | Effect/Turn owner + outer worker adapters: converge duplicated prepare/dispatch/observe/validate/writeback/settle orchestration around existing typed operations and receipts | App/Turn/external worker agree on admission, original result and single debit under partial failure; scheduler effects retain their true host owner; switch callers back only after receipt reconciliation |
| **C5 — Replan and result authoring boundary; C1** | Goal/Vision and interaction owners: expose current facts and typed controller operations; remove ordinary executor obligations to manufacture Core writeback fields in the two-session profile | Fact-first decision preserves default/stride policy differences, all evidence and required validation; invalid proposed writes reject; single-session authoring remains qualified during rollout |
| **C6 — Controller/executor exchange; C2–C5** | Manager/request + host owners: reuse versioned semantic requests and result return; replace schema-constrained executor work with natural-language work/material exchange | Complete source delivery, local executor autonomy, enforced Core isolation, corrective exchange and independently accepted outcome; return to qualified single-session profile for new work on rollback |
| **C7 — Benchmark reuse and full-cost attribution; C4/C6** | Benchmark toolkit/execution owner: thin adapters select the same product profiles; consolidate duplicated launch/session settings and track both sessions without changing frozen trials | Native/plain controls unchanged; matched-arm configuration/replay and complete cost coverage; no jobs launched merely by merging helpers |
| **C8 — Product configuration and operational readback; C3/C6** | Existing App/settings/Chat projections: one effective configuration owner, role-aware activity, failure/recovery, Stop and artifact return | Packaged managed and supported attached heartbeat journeys, including unavailable host and late results; installed qualification separate; no new packet-format toggle |
| **C9 — Retire old paths and qualify adoption; C1–C8** | Contract/runtime maintainers: remove superseded producers, format switches, duplicated prompts and tests; update protocols, guides and RFC delivery facts | Caller/field/decoder retirement inventory, historical replay and rollback evidence; App + benchmark acceptance and matched benefit before any default promotion |

C1's inventory is part of its usable replacement, not a standalone architecture
framework. C3/C4/C5 can proceed independently once C1's contract is stable;
C7 can prepare measurements before C6 but cannot claim two-session outcomes.
Implementation PRs must name the replaced caller, remaining gap, actual tests
and rollback. No full language migration or unrelated manager rewrite is a
prerequisite. No task is complete merely because a new abstraction compiles.

C1 is one acceptance target, not one mandatory PR. Independently useful
prerequisites may land first when existing callers adopt the shared owner, their
entrypoint contracts are qualified, and rollback is local. For example, move
exact context-reference composition and recovery into the existing TypeScript
owner while the current quota and Turn entries keep their schemas. Name the
remaining cutover consumer; do not add unused infrastructure or a second ledger.
Separate shared-owner prerequisites, producer/capture cutover, reader recovery
and retirement, and adoption/full-cost qualification where each boundary is
independently reviewable. Record dependencies in the existing tasks and PRs.
Splitting delivery does not close C1 or waive §9's original quota-baseline test.

The current author split keeps [#7517](https://github.com/loopx-project/loopx/pull/7517)
as an independent bilingual design refinement and
[#7520](https://github.com/loopx-project/loopx/pull/7520) as an independently
usable route-owner/caller refactor on main. Existing independent proposals
[#7457](https://github.com/loopx-project/loopx/pull/7457) and
[#7468](https://github.com/loopx-project/loopx/pull/7468) retain context-reference
and observation-diagnostic ownership; the cache-clock fix
[#7464](https://github.com/loopx-project/loopx/pull/7464) is already in the
named target main. These boundaries have local rollback; the design change
has no runtime dependency. [#7430](https://github.com/loopx-project/loopx/pull/7430)
remains an unfinished integration candidate and includes prerequisite content
for combination testing. After prerequisites enter main, rebase it again to
remove overlapping diff before cutover handoff. Do not merge two implementations
or present this staging arrangement as complete C1 delivery.

The first **cutover** slice pairs the complete field/clause migration with a
real single-session producer and consumer replacement. Start at the
shared quota emission boundary and trace both attached heartbeat delivery and
the Turn adapter; retain their actual host ownership. Do not build an unused
third serializer while both active writers keep their own semantics. Existing
`test_quota_decision_capture.py` and `test_turn_host_decision_capture.py` provide
capture/recovery coverage to extend, not proof of the proposed schema. Qualify
normal work, selection, replan, wait, unsettled-Turn recovery, unavailable detail
and capability-off behavior through the real entries on isolated fixtures.
Each case must check complete obligations and original settlement identity as
well as reconstructed data. The oracle must include source-different but
projection-equal cases; comparing two outputs of the same projection is circular
evidence for completeness. Native-resume/context work remains with its existing
owner; adopt its qualified changes and add only the integration coverage missing
at the new composition boundary. Keep C1 open until the replacement works and
the remaining supported readers have explicit cutover/retirement conditions.

### Responsibility convergence and contributor handoff

The [source-backed refactor handoff](ledger/controller-executor-unified-contract-v0/2026-10-27-refactor-handoff.md)
decomposes C3/C4/C7/C8/C9 into concrete simplification candidates. It belongs to
this plan, not another roadmap or evidence of shipped implementation. The pinned
audit separates duplicated decisions, misplaced shared code, existing shared
owners and hypotheses requiring semantic comparison.

Keep three responsibilities clear:

1. **Core semantic owners:** decide legal work/session/effect transitions and
   validate original authority and receipts in the existing typed domains.
2. **Shared managed execution:** drive those decisions through session binding,
   native invocation, observations and recovery. Reuse existing Turn/effect
   owners; this is not another permission engine or scheduler.
3. **Host adapters:** implement native SDK/CLI transport, event translation,
   supported resume and resource cleanup. Preserve real host differences rather
   than hiding unsupported behavior behind a nominal common interface.

Select work from the overall roadmap, current user outcome, demonstrated
bottlenecks, open delivery and canonical work. Package IDs and table order are
references, not global priority, an Agent's fixed agenda or reserved ownership.
C1/C2 composition/capture/final-input work and these simplifications are options
within that same frontier, not competing permanent Agent mandates. Owners name
code boundaries, not assignments or execution grants; recheck target main,
open PRs and canonical work before claiming one.

| Existing task / package | Simplification result | Start condition |
| --- | --- | --- |
| **C3.1 Managed-operation protocol** | Codex and DSH adopt one operation-session flow; remove duplicated binding/profile checks and handoff/start orchestration, and DSH's dependency on a Codex-owned generic handler | When this duplication is the selected bottleneck: existing single-session behavior and two real callers; retain native differences |
| **C3.2 Session-binding callers** | Reuse shared persistence; eliminate caller-side binding reconstruction and private cross-host imports where a cohesive existing operation can replace them | Pair with C3.1 if a separate change would leave only a rename/facade; role/generation expansion waits for stable C1 |
| **C4.1 Receipt-to-continuation** | Adopt a qualified typed Turn observation/continuation boundary; retire replaced Python receipt reconstruction and decisions | Keep journal recovery distinct from next-work decisions; integrate C1 and existing recovery fixes |
| **C4.2 Outer-worker adoption** | Consume qualified semantic results instead of reconstructing common retry/continuation policy | C4.1; preserve wake ownership, original receipts and opaque-command uncertainty |
| **C4.3 Settlement-adapter convergence** | Reuse settlement primitives; retire a second step/condition interpreter only where obligations coincide | Candidate: compare Host Todo completion and Turn settlement; coordinate active settlement PRs |
| **C7.1 Worker/process adapter cleanup** | Reuse applicable launch/drain/cleanup helpers and remove proven duplicated mechanics | Candidate: match ownership, signal/deadline and control-arm semantics first |
| **C8.1 Operational readback** | App/Chat/CLI consume the same typed work/session facts; retire superseded caller classifications | Candidate: role facts available and duplicate classification demonstrated; retain unknown/coverage and audience boundaries |
| **C9.1 Lease/source-path retirement** | Remove obsolete normal-writer routing/wrappers after caller migration; retain required release, fence-close and original-receipt recovery | Cross-RFC candidate: shared-authority/TS acceptance, not a controller-launch prerequisite |
| **C9.2 Contract-test consolidation** | Shared invariant cases and thin native-adapter tests replace duplicated complete-flow fixtures | Companion to the owning replacement; preserve unique integration and negative cases |

These are nine investigation/delivery packages, **not nine promised PRs**, a
fixed remaining count or mandatory work before C1 lands. Do not select C3.1
merely because it appears first, or prefer visible code deletion over a more
valuable current correctness, contract-delivery or authority-retirement outcome.
If selected, C3.2 may share C3.1's reversible PR. Only actual semantic dependencies
constrain order. Existing-behavior repairs and proven local simplifications can
land before C1; contract-dependent cutovers retain that dependency. Similar-looking
files do not qualify a conditional candidate.

Record why the chosen slice advances the current global outcome, which higher-
value alternatives or in-flight deliveries were considered, and its actual
dependency. Reassess after delivery or new evidence; this inventory neither
exhausts useful work nor obliges an Agent to finish one lane before changing
focus. Priority advice creates no new admission or approval gate.

Each claimed package carries its source owners/callers, target boundary,
specific deleted rules/branches, real-path oracle, dependencies and rollback
in the existing Todo and PR. An audit assigns no peer work and creates no parallel
task store. Complete caller replacement and deletion in the usable slice; keep a
wrapper only for an identified external or recovery contract. Measure total
production changes across languages, semantic owners, complete-transaction
runtime crossings and the next representative rule's change sites. Python LOC
reduction or a new shared class alone does not establish simplification; no
arbitrary net-negative LOC quota replaces correctness or required validation.

Reuse [TypeScript change-locality payoff](typescript-control-plane-migration-v0.md#28-change-locality-as-migration-payoff)
and [smoke consolidation policy](../../development/good-smokes.md). Internal
behavior-preserving replacements need real caller/backend evidence, not new
settings screens. Changed caller-facing operations/readback retain C8's packaged
frontend failure/recovery journey and first-screen evidence. C1/default-adoption
complete-cost and benefit qualification remain unchanged; a bounded refactor is
not full RFC acceptance.

## 12. Open decisions

1. **Exact wire name and field mapping — contract owner, before C1 cutover.**
   Prefer evolving one existing registered vocabulary/version over a parallel
   protocol family. Section 8 recommends preserving source-root paths; finalize
   the version and host-binding mapping after the complete producer/reader/field inventory;
   maintainer approval is required for removal, not for preserving old meaning.
2. **First qualified two-session host — runtime owner, before C6 dispatch.**
   Choose from actual session control and isolation capabilities. Managed Codex
   may be an implementation vehicle; App heartbeat is a required product
   qualification. Neither entry is presumed to prove the other or benchmark use.
3. **Resource allocation and default — product/evaluation owners, after C7–C8.**
   Test same-model controller/executor first to isolate architecture effects;
   cheaper controller models and different budgets are separate experiments.
   Keep the profile opt-in until full outcome/cost evidence supports a decision.

## Appendix A: Execution ledger

Source audits and subsequent delivery evidence live in
[`ledger/controller-executor-unified-contract-v0/`](ledger/controller-executor-unified-contract-v0/).
The directory is the index. A planning/source-inspection entry does not establish
runtime delivery; live priorities and claims remain in the roadmap and canonical
work. Keep historical entries append-only and refine the current normative plan
here rather than treating a dated audit as a permanent execution queue.
