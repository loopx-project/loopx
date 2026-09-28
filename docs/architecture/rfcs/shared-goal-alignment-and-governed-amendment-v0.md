# RFC: Shared Goal Alignment and Governed Amendment Protocol (v0)

- Status: Accepted
- Supersedes / closes: none
- Tracking issue: [#3836](https://github.com/huangruiteng/loopx/issues/3836)
- Date: 2026-09-02
- Last updated: 2026-09-16
- Scope: peer Agents collaborating around one shared Goal while preserving
  canonical intent, per-Agent execution frontiers, claim/lease ownership, and
  auditable replan/amendment decisions
- Related contracts:
  [Goal Vision and Replan](../../reference/protocols/goal-vision-replan-contract-v0.md),
  [Shared Control-Plane Authority and Pluggable State Providers](./shared-goal-authority-state-provider-v0.md),
  and [Decision Context](../../reference/protocols/decision-context-architecture-v0.md)
- Language note: the
  [Chinese version](./shared-goal-alignment-and-governed-amendment-v0.zh-CN.md)
  and this English version are semantic mirrors. A difference between them is
  a defect.

---

## 1. Summary and decision

Implementation checkpoint (Stages 1/2 only): alignment and amendment admission
share one full Todo/lease source snapshot. Before promotion this remains a
legacy read; afterwards canonical empty state and provider failures never fall
back to Markdown or per-Todo lease files. Typed selection excludes non-open,
archived and unsatisfied-wait work; Agent eligibility also honors exclusions,
while amendment impact may include peer-held or executor-excluded open work.
The source digest binds the canonical provider revision in `source_basis.todo_basis`.
With a state event log, `revision_basis=state_event_log` still names only that
event axis. Without one, promoted reads use `canonical_todo_snapshot` with
event sequence 0 and an unbound Agent frontier. A changed canonical digest
requires `needs_rebase` even at sequence 0. This does not version the full Goal
intent envelope, infer Agent acknowledgement, or turn admission into an approval
or CAS commit; Stage 3 must still revalidate its own exact commit-time basis.

LoopX will distinguish four kinds of state that must not collapse into one
mutable plan:

1. a **canonical shared Goal intent envelope**;
2. a **shared eligible work graph**;
3. one **per-Agent frontier** for each registered peer; and
4. **proposal and receipt records** for governed shared amendments.

An Agent may correct its own frontier while remaining inside canonical intent.
It may propose a shared amendment when evidence invalidates shared assumptions.
The proposal does not change the Goal, block unrelated work, or grant the
proposer unilateral commit authority. A shared amendment becomes effective
only after LoopX's `GoalAmendmentAuthority` validates it against the pre-authorized
policy and exact base revision, commits it with compare-and-set (CAS), and
produces a durable receipt. Every Agent then rebinds its frontier to the new
canonical revision.

The normal path is automated. It does not require every peer to vote or a human
to approve routine Goal evolution. `peer_v1` describes equal execution rank,
not universal authority to escape the root user intent or acquire new
permissions. Goal creation freezes a `root_intent` and an amendment-policy
envelope. Inside that envelope, policy checks and, for higher-risk classes, an
independent verifier Agent authorize automatic commit. Outside it, the old Goal
remains effective and the proposal is rejected or structurally blocked; human
escalation occurs only when the Goal explicitly configured it.

```text
canonical shared Goal intent envelope
  (objective, non-goals, acceptance, permissions, stop conditions, revision)
                 |
                 v
shared eligible work graph
                 |
        +--------+--------+
        |                 |
        v                 v
per-Agent frontier A   per-Agent frontier B
        |                 |
claim + lease/fence    claim + lease/fence
        |                 |
bounded evidence       bounded evidence
        +--------+--------+
                 |
       lane replan or amendment proposal
                 |
     automated policy + optional verifier
                 |
       base-revision CAS commit
                 |
 committed shared amendment + receipt
                 |
       every frontier rebases or gates
```

- Checkpoint moved to the execution ledger: [Verified delivery and manager integration checkpoint (2026-09-13)](ledger/shared-goal-alignment-and-governed-amendment-v0/2026-09-13-verified-delivery-and-manager-integration.md).
- Checkpoint moved to the execution ledger: [Owner-authorized acceptance checkpoint](ledger/shared-goal-alignment-and-governed-amendment-v0/2026-09-17-owner-authorized-acceptance.md).

## 2. Problem and current boundary

LoopX already coordinates execution usefully:

- registered peer identities and Agent-scoped Todo lanes;
- visible/selectable unclaimed Todos with claim-before-work guidance;
- soft claims plus hard lease/fence ownership;
- per-Agent vision and checkpoint state;
- typed autonomous-replan obligations and settlement;
- action-scoped cross-owner Todo lifecycle grants; and
- provider-neutral coordination CAS and receipt foundations.

[#3693](https://github.com/huangruiteng/loopx/pull/3693) is a positive bounded
fix in this layer. It prevents shared `Next Action` prose from shadowing an
exact settlement-bound or current-Agent Todo. It does not define shared Goal
alignment or shared semantic amendment authority.

The missing seam appears when multiple Agents independently discover that the
shared plan or acceptance boundary is wrong. Per-Agent vision can remain
internally consistent while the combined work no longer proves the original
Goal. Conversely, letting every Agent rewrite shared prose turns the latest
writer into accidental authority.

No type system can prove that arbitrary natural-language changes preserve the
user's intent. LoopX can instead make silent reinterpretation impossible: an
amendment must name what is retained, changed, and stopped; cite evidence; bind
the exact base revision and digest; pass an explicit authority policy; and
leave a recoverable receipt.

## 3. State partition and invariants

### 3.1 Canonical intent envelope

`shared_goal_intent_v0` contains:

- `goal_id`, `goal_revision`, and `intent_digest`;
- objective and non-goals;
- acceptance conditions;
- permission/write scope;
- stop and terminal conditions; and
- the root intent that Agents cannot amend;
- the authority policy that governs each amendment class; and
- the configured disposition for out-of-policy proposals (`reject`, `block`,
  or explicit `human_escalation`).

This is semantic authority, not a status projection. `Next Action`, Agent
vision, chat messages, scheduler hints, and provider heads cannot overwrite it.

### 3.2 Shared work graph

The shared work graph contains Todos, dependencies, eligibility, blocking
gates, and lifecycle state. It describes candidate work, not who may currently
execute it. Work-graph edits must remain traceable to the canonical intent
revision they are intended to advance.

### 3.3 Per-Agent frontier

Each registered Agent receives a bounded `shared_goal_alignment_v0` projection:

- canonical Goal revision/digest;
- that Agent's frontier and `based_on_goal_revision`;
- its claims and lease/fence facts;
- eligible unclaimed work;
- open lane replan or shared amendment obligations; and
- conflicts or stale-basis facts.

An Agent may replan its own route without a shared amendment when the change
stays within current objective, non-goals, acceptance, permissions, and stop
conditions and does not mutate another Agent's claimed work.

### 3.4 Proposals and receipts

Proposals are advisory, durable inputs. Receipts prove canonical transitions.
Neither is a substitute for the other. A pending or approved proposal has no
effect until a successful commit receipt names the new Goal revision.

### 3.5 Host-session locators and advisory context

A task deep link can make entry into this protocol precise without becoming a
fifth kind of shared state. For Codex, `codex://threads/<thread-id>` identifies
a local chat. LoopX may resolve that locator through the current project
registry and bind the source session to an existing Agent and Goal identity.
The returned provider-neutral `host-session:codex:<thread-id>` scope can then
select that session in an explicitly enabled Decision Context provider.

This is an optional, transient **advisory context input**. It remains outside
`shared_goal_intent_v0`, `goal_amendment_proposal_v0`,
`goal_amendment_receipt_v0`, and the provider CAS head. It helps peers:

- identify the exact task in which a gap or evidence pointer was discovered;
- recall a bounded set of source-task messages while reviewing current facts;
- route an amendment proposal to an independent verifier or affected peer; and
- return to the relevant task after commit for receipt readback and frontier
  reconciliation.

```text
host task deep link -> project-local binding -> normalized host-session scope
        | explicitly configured, read-only ContextProvider
        v
local-private transient recall -> verify against current authority sources
        | explicit promotion to durable typed evidence
        | base Goal revision + intent digest
        v
governed amendment proposal -> authority decision -> canonical receipt
```

The ordering is normative. A deep link is not an `evidence_ref`, a recalled
message is not an amendment decision, and the extension lifecycle revision is
not `base_goal_revision`, `authority_revision`, `provider_generation`, or
`lease_epoch`. Any session-derived conclusion needed by the amendment must
first be checked against current authority and promoted to the existing Todo
evidence, Agent evidence log, or registered material owner. The proposal then
cites those durable typed references and independently binds the current Goal
revision and intent digest.

The locator also grants no read access, permission, claim, lease, lifecycle
authority, verifier independence, or amendment commit authority. If the link is
unresolvable, inaccessible, or its extension is disabled or unavailable, only
the optional context-enrichment step fails open; the canonical Goal and
unrelated work remain valid. Decision Context records the provider degradation
and continues from available authority sources. Receipt recovery uses `operation_id`
and `readReceipt`, so losing a host session cannot make a committed amendment
unrecoverable. A cross-Goal rendezvous may help two peers coordinate, but each
Goal still requires its own proposal, policy decision, CAS commit, and receipt.

Core parses host-specific deep-link syntax exactly once and exposes only the
normalized scope to providers. The optional `loopx-obelisk` extension maps that
scope to Obelisk's public read-only query interface. It does not read Obelisk's
storage schema, build or attune the index, or open, resume, or message a live
task. Other harnesses can implement the same Decision Context provider protocol
without adding host syntax or transcript storage to the Goal authority.

### 3.6 Peer agent directory and bounded observation

A per-Agent frontier tells one Agent about its own route. Peers also need the
same three abilities about each other, and the steward needs them about every
Agent it is asked about: discover which Agents exist and which are running,
observe one of them within bounds, and hand one of them a bounded request. That
reusable contract is
[`peer_agent_directory_v0`](../../reference/protocols/peer-agent-directory-and-observation-v0.md).

It adds no sixth kind of shared state. Identity, work, claims, leases and the
canonical revision stay exactly where this document already put them; the
contract contributes an Agent-facing *view* plus the rules for reading and
delivering. Three of those rules carry the weight here:

- **Presence is advisory and provider-scoped.** A live session never creates an
  identity, and an Agent with no live session is still registered, still owns
  its claims and is still a delivery target. A provider reports its own
  locations with session-scoped handles and its own liveness vocabulary; a
  reader that cannot classify a target reports `unknown` and names the coverage
  gap rather than inferring completion or absence of progress.
- **Observation and delivery grant nothing.** Reading a peer, or handing it
  context, is not a claim, a lease, a priority, a plan change or an amendment.
  Delivery stays `context_handoff`; what the Goal asks for still changes only
  through `GoalAmendmentAuthority`, and work state still changes only through
  the canonical Todo, quota and lane owners.
- **Terminal-space providers are providers, not the contract.** A host surface
  that owns terminals may supply presence and bounded live output, and must
  declare how a caller proves it is inside the space, what survives a detach or
  restart, and what it cannot recover. With no such provider the directory
  degenerates to registered identity plus durable work state, which is the
  normal case for a prompt-only transport.

Three rules generalize the same contract beyond one provider and beyond the
steward, and they are what make it reusable instead of host-specific:

- **One space, three layers, two audiences.** The space is this Goal's execution
  space, and a host surface is a transport inside it. The contract is reachable
  as typed state and governed commands, as the in-space skill a running Agent
  loads, and as a provider surface that supplies presence only; a layer may
  narrow authority, never widen it. The steward (manager channel) and a peer
  Agent (`peer_v1`) are two callers of the one contract, scoped by the channel's
  Goal binding and by the Goal's registered Agents respectively, so a caller
  resolves itself from the binding it arrived on and its targets from the
  directory.
- **Bounded waits pin identity and require forward movement.** A wait, or the
  readback that a delivery produced a turn, pins the resolved Agent, work
  identity and provider location, so a replacement occupant of the same location
  cannot satisfy it, and it requires that the observed state moved after the
  request began, so a stale re-read proves nothing. This is the binding shape
  this document already requires of a governed write's settlement.
- **An attention rollup is typed, and assigns nothing.** A "who needs a decision
  now" view may order and annotate rows from typed state. It creates no claim, no
  lease and no priority, and it is not an input to automatic assignment.

## 4. Authority matrix

### 4.1 What `GoalAmendmentAuthority` means

`GoalAmendmentAuthority` is not a person, a leader Agent, a model, or a storage
service. It is LoopX's single typed write boundary for canonical Goal
amendments. A concrete implementation is expected to separate:

```text
proposal + current Goal + policy + lease impact + optional verifier decision
                                  |
                                  v
                 GoalAmendmentAuthority.decide()
                       reject | needs_rebase | commit
                                  |
                                  v
                  provider CAS + canonical receipt
```

The decision reducer enforces deterministic policy, identities, digests,
revisions, and impact rules. An optional verifier Agent supplies a typed input
for semantic questions, but cannot commit. The transaction executor persists an
accepted decision through the provider-neutral store, but cannot widen it.
Calling this boundary an authority means all canonical writers must pass
through it; it does not mean that one privileged Agent decides for its peers.

### 4.2 Amendment classes

| Amendment class | Example | Proposal authority | Automated commit rule | Default effect while pending |
| --- | --- | --- | --- | --- |
| `lane_route` | reorder one Agent's unclaimed local steps | owning Agent | deterministic lane policy | none outside that lane |
| `shared_work_graph` | add a Todo or dependency that preserves intent | registered Agent | policy validation plus impact check | unrelated work continues |
| `shared_acceptance` | refine an acceptance condition or non-goal inside root intent | registered Agent | policy validation plus independent verifier Agent | affected acceptance path is gated |
| `protected_authority` | acquire new permission or escape root intent | registered Agent | never auto-commit unless the immutable envelope already delegates the exact class | affected work fails closed |

`GoalAmendmentAuthority` is the normal commit boundary. A verifier Agent returns a
typed, evidence-bound decision; it does not become a durable leader and cannot
edit the proposal it verifies. Proposer and verifier identities must differ for
classes whose policy requires independence. Deterministic checks remain the
first gate; model judgment cannot override permissions, scope, stop conditions,
or a stale base.

Being a scheduler, Supervisor, latest writer, lease holder, or provider
operator does not grant semantic commit authority. Out-of-policy proposals are
rejected or left structurally blocked while the old Goal continues. They ask a
human only when `human_escalation` was explicitly enabled, rather than turning
every Goal change into an approval queue.

## 5. Amendment lifecycle: how a proposal becomes effective

```text
draft -> submitted -> admitted -> policy_check -> verified -> committing
  |          |            |             |             |
  +--------> rejected <----+-------------+-------------+
                             stale/conflict -> needs_rebase

committing --CAS success--> committed + receipt -> frontier reconciliation
          \--unknown------> ambiguous -> readReceipt/reconcile
          \--CAS conflict-> needs_rebase
```

The effective path is:

1. **Propose.** Any authorized proposer submits
   `goal_amendment_proposal_v0`, including the base revision/digest, amendment
   class, retained/changed/stopped intent, evidence references, affected Todos,
   and linked replan obligation. A request-derived proposal also binds the
   immutable source request id and revision. An optional host-session rendezvous
   may help discover or review the gap, but only promoted durable evidence enters
   the proposal.
2. **Admit.** LoopX validates schema, actor identity, bounded evidence pointers,
   amendment class, and impact scope. A host locator cannot prove actor identity
   or count as evidence. Admission does not approve or apply the proposal.
3. **Policy decision and optional verification.** LoopX evaluates deterministic
   invariants and the pre-authorized amendment envelope. A higher-risk but
   in-envelope class may invoke an independent verifier Agent that returns a
   typed decision bound to the exact proposal digest. The policy may reject or
   request rebase. A verifier decision cannot be reused for edited content.
4. **Impact decision.** Before commit, the authority decides how in-flight
   claimed/leased Todos are handled: unaffected, allowed to finish under the
   old revision, explicitly cancelled with a new fence epoch, or blocked by
   policy. A semantic amendment cannot silently invalidate work already
   authorized by a lease.
5. **Commit.** The `GoalAmendmentAuthority` transaction submits the policy-authorized digest
   with an `operation_id`, expected `base_goal_revision`, and
   `base_intent_digest`, then revalidates policy and performs one CAS. For a
   request-derived proposal, require the exact source reservation and terminal
   operation protocol in §5.1; a remote liveness read followed by Goal CAS is
   insufficient. Stale Goal bases and unreserved superseded source revisions
   fail closed. Routine in-envelope amendments do not wait for a human.
6. **Receipt.** The same transaction records the proposal digest, actor,
   authority source, old/new revisions, retained/changed/stopped delta,
   evidence references, affected Todos, lease disposition, and exact replan
   obligation settlement.
7. **Reconcile.** Projections rotate to the new revision. Every Agent either
   rebinds its frontier, opens a lane replan, or is gated if its current work is
   incompatible. Old-revision semantic writes are rejected.

Only step 5 makes the amendment canonical. Step 6 makes that fact recoverable
when a response is lost; step 7 makes it operational for all peers.

### 5.1 Source-request reservation and cancellation ordering

This is a proposed qualification requirement for request-derived Stage 3
commits, not a shipped API or a new distributed transaction. Reuse the
[collaboration request fence](capable-manager-semantic-handoff-v0.md#510-minimum-contract-and-legal-observations)
and the qualified amendment owner's operation/receipt transaction. Keep request
and Goal state under their separate owners.

1. **Reserve at the request owner.** In one request transaction, validate the
   live source revision and its authority, acquire its exclusive effectful
   attempt fence, and persist a reservation binding request/revision,
   attempt/fence epoch, target Goal and authority source, proposal digest,
   expected Goal basis, actor and `operation_id`. The amendment owner must
   authenticate this reservation; a caller-supplied token is not authority.
   Reservation replay returns the same binding; changing any bound input
   conflicts. Reservation and effective cancellation/supersession use the same
   request-owner CAS over source revision, lifecycle and fence epoch; whichever
   wins determines eligibility. Recording a later correction does not revoke
   the already reserved operation or make its immutable source binding stale.
2. **Order later control requests.** Once reserved, cancellation or correction
   can be recorded immediately but cannot revoke that in-flight operation by
   changing only the request store. Mark it pending settlement, prevent further
   effects/reassignment, and ask the amendment owner to abort that exact
   operation. The reservation covers only this immutable operation, never the
   rest of a superseded request or a replacement proposal. Authorization and
   policy checks at the actual effect owner still apply. Abort requires a
   request-owner cancellation/recovery receipt bound to the reservation, reason
   and operation; knowing an operation ID is insufficient authority.
3. **Settle at the Goal owner.** An authenticated commit or abort competes for
   one durable terminal operation record at `GoalAmendmentAuthority`. Commit
   validates the reservation, current policy and expected Goal basis, then
   atomically writes the Goal delta and `committed` receipt. Abort atomically
   writes an `aborted` no-effect receipt only if that operation has not committed.
   Both use the same operation identity and serialization boundary; abort is a
   terminal tombstone, not a separate retry identity. A committed operation
   cannot be undone by abort, and an aborted operation can never commit. Replays
   read the original outcome; digest/binding drift conflicts. Definitive policy
   or basis rejection also closes the operation without a Goal mutation. Validate
   the authenticated binding inside this serialization boundary. A fresh
   operation ID requires a new reservation and cannot bypass an old tombstone.
   Terminal receipts discriminate `committed`, `aborted` and `rejected`, carry
   the reservation/attempt reference, and state whether this operation changed
   the Goal. No-effect receipts have no resulting Goal revision and say nothing
   about other external effects in the broader request.
4. **Recover before releasing.** Link the exact terminal Goal-owner receipt to
   the immutable request attempt before releasing its fence or acknowledging
   cancellation. This linking/settlement is a separate idempotent request-owner
   transaction: settle the exact attempt, apply pending control changes for
   remaining work, then release. It never rolls back a committed Goal delta.
   A missing receipt, timeout or expired worker lease proves
   nothing: read back or race a conditional abort against commit under the same
   operation identity. If the owner is unavailable, retain pending/unknown and
   allow unrelated work; do not reassign the effect. Retain the terminal record
   until stale attempts are provably unable to submit, including after restart
   or source migration. A delayed worker must hit that durable boundary, not
   merely a token TTL. Reservations do not expire independently of settlement;
   deadlines trigger recovery, not permission to forget an unresolved operation.
   Unsupported profiles cannot commit request-derived
   amendments; they may still admit proposals and continue independent work.

Reservation CAS orders source eligibility; the Goal-owner terminal transaction
orders the reserved commit versus abort. These are two explicit local decisions,
not a claim that a source read and Goal write are atomic. The first Stage 3
class remains `shared_work_graph`; this protocol adds no amendment permission.

The combined manager A7/A16 fixture must exercise these interleavings through
both owners, not two independent unit suites:

| Interleaving | Required outcome |
| --- | --- |
| Cancellation/correction wins before reservation | No reservation and no Goal mutation from the obsolete proposal |
| Reservation exists; cancellation's abort wins at Goal owner | One `aborted` receipt; delayed original commit rejected; cancellation can settle |
| Reserved commit wins before abort | One `committed` receipt; cancellation reports the already committed effect and stops remaining work, without claiming rollback |
| Crash after reservation/check but before a known Goal outcome | Fence remains; same-operation recovery/conditional abort yields exactly one terminal outcome, even if the old worker resumes |
| Goal CAS succeeds but its response or request-side linkage is lost | Read back the original committed receipt, attach it to the old attempt, and never reapply the delta |
| Lease expires or host restarts while outcome is unknown | No replacement effect on timeout alone; terminal abort blocks late commit, or the existing commit is reconciled |

Frontend, Lark and CLI project the same distinction between cancellation
requested, pending settlement and settled-with/without-an-already-committed
effect. A timeout must not display “cancelled, no change.” Implementing PRs must
qualify this path on the selected authority profile before enabling it.

## 6. Proposed schemas

Illustrative `goal_amendment_proposal_v0`:

```json
{
  "schema_version": "goal_amendment_proposal_v0",
  "proposal_id": "gap_...",
  "goal_id": "goal-1",
  "proposer_agent_id": "agent-a",
  "amendment_class": "shared_acceptance",
  "base_goal_revision": 17,
  "base_intent_digest": "sha256:...",
  "retained": ["original outcome remains unchanged"],
  "changed": ["acceptance now requires the recovered receipt"],
  "stopped": [],
  "evidence_refs": ["evidence:..."],
  "affected_todo_ids": ["todo-a", "todo-b"],
  "replan_obligation_id": "replan:...",
  "source_request_ref": {"request_id": "req_...", "revision": 1}
}
```

Illustrative `goal_amendment_receipt_v0` adds:

```json
{
  "schema_version": "goal_amendment_receipt_v0",
  "operation_id": "op_...",
  "proposal_id": "gap_...",
  "proposal_digest": "sha256:...",
  "decision": "committed",
  "authority_actor_id": "goal-amendment-authority",
  "authority_source": "goal_amendment_policy_v0",
  "verifier_decision_digest": "sha256:...",
  "previous_goal_revision": 17,
  "new_goal_revision": 18,
  "new_intent_digest": "sha256:...",
  "lease_dispositions": [],
  "settled_replan_obligation_id": "replan:..."
}
```

The read-only `shared_goal_alignment_v0` projection must identify pending,
approved, conflicting, and committed proposals without treating any pre-commit
state as canonical intent.

## 7. Concurrency, recovery, and multi-Agent behavior

Multiple proposals from one base may coexist. Policy/verifier decisions and
commit are bound to the exact proposal digest. Canonical commit is serialized by Goal revision:
after one proposal commits, another proposal from the old base becomes
`needs_rebase`; it is never silently merged or applied last-writer-wins.

Independent lane work continues while a proposal is pending unless a typed
impact gate names that Todo or acceptance path. An Agent may claim eligible
unclaimed work only through the existing atomic claim and, when configured,
lease/fence acquisition. Proposal authorship does not reserve a Todo, and Todo
claim ownership does not authorize a Goal amendment.

If provider commit succeeds but the response is lost, the caller does not
blindly retry with a new operation identity. It calls `readReceipt` using the
same `operation_id`. A found receipt proves the canonical revision. An absent
receipt plus a changed head requires reconciliation; ambiguity is not treated
as failure. Provider-specific file, NoKV, or PostgreSQL behavior remains behind
the provider-neutral authority store contract.

## 8. Replan integration

Replan classifies a discovered gap before choosing a writer:

- a route correction wholly inside canonical intent opens or settles an
  Agent-scoped replan obligation;
- a cross-lane dependency/work-graph gap opens a shared amendment obligation;
- evidence that changes acceptance, non-goals, or the operational objective
  inside the root-intent envelope opens an automatically governed amendment
  obligation; and
- a change outside delegated permissions or root intent is rejected or remains
  structurally blocked under the configured escalation disposition.

Each obligation has a stable id. Proposal ACK alone does not settle it.
Settlement requires either a committed receipt for that exact obligation, a
rejected/no-change decision with a structured rationale accepted by policy, or
a superseding obligation that explicitly preserves the causal chain.

After commit, an Agent whose `based_on_goal_revision` is stale may observe but
cannot make controlled semantic writes until it rebases or receives an explicit
grandfathered-work disposition. This connects shared change to existing
per-Agent Goal Vision without turning one Agent's vision into peer authority.

## 9. Provider and projection boundaries

The `GoalAmendmentAuthority` decides whether a proposal is legal and may commit.
File, NoKV, and PostgreSQL providers persist normalized transactions, CAS
heads, and receipts; they do not interpret Goal prose or choose amendment
policy.

This RFC does not expand the current coordination aggregate in its first
implementation slice. The existing shared-authority RFC continues to own
Todo/claim/lease/receipt persistence. Goal semantic amendment first ships as a
read-only projection and proposal contract. Mapping its commit into a
provider-neutral aggregate requires a separate reviewed transaction boundary.

`Next Action` remains compatibility prose and a read projection. It is never a
claim, lease, Goal amendment, replan settlement, or authority decision.

### 9.1 Semantic handoff and execution-route integration

Use the existing alignment projection to supply a receiver's actual work
basis. In-intent lane replanning stays in the receiver's Vision/Replan path;
shared changes use this RFC's classification and admission. Stage 2 may
retain a request-derived proposal, with source/context references and an
explicit unresolved obligation, but cannot report that the shared Goal changed.
Missing full intent authority is not repaired by synthesizing a revision from
the Todo provider head or the event sequence.

The next amendment implementation remains **one bounded Stage 3 work-graph
commit class**, not broad acceptance/permission rewriting. It must first
establish the actual canonical intent/policy basis and reviewed transaction
mapping, then prove exact-basis admission, lease impact, CAS receipt recovery
and peer frontier rebase. Reuse the [shared authority](shared-goal-authority-state-provider-v0.md)
storage guarantees and [TS transaction migration](typescript-control-plane-migration-v0.md)
owner; neither currently grants amendment semantics just by being available.

Manager M1 and ordinary M2 handoff can ship before that commit class. Until
then, expose proposal/admission and the unavailable-commit boundary while
unrelated work continues. After qualification, the manager invokes the future
Stage 3 `GoalAmendmentAuthority` commit owner under its existing policy; no permanent manager-superuser role, mandatory peer
vote or repeated owner confirmation is added. The manager's result links the
committed amendment receipt and peer/in-flight disposition. Cross-Goal handoff
does not merge distinct Goals' intents or authorize either Goal's amendment.

The manager RFC's A16 reuses the existing alignment/amendment fixtures for
lane/proposal/stale-basis negatives and later qualifies the supported commit
path. This RFC retains Stage 3–5 implementation and promotion ownership;
manager readiness cannot silently mark those stages done.

### Steward execution integration (2026-09-16)

[Roadmap](loopx-overall-roadmap-v0.md) R1 repairs team-plan source-basis preconditions; R4 supplies the product continuation for Stage 3–5. The current team receipt `intent_basis` only reuses `source_basis_digest`, a source-facts summary that does not cover the full objective/non-goals/acceptance/permissions/stop envelope. It may be absent and is not a CAS precondition. Do not claim full canonical intent binding.

First connect existing commitments/work basis to commit-time validation. Then version intent in this owner and implement one intent-preserving work-graph amendment class with policy/verifier, lease impact, conflicts and receipt recovery. Ordinary Todo edits retain their writer rather than being forced into amendments. A steward may organize and synthesize peer work without gaining leader write authority. Stage 1/2 and the local 24-row directory exist; Stage 3, presence/lease epoch and pagination gaps remain explicit.

## 10. Staged delivery

1. **Stage 0 — characterization and RFC.** Record own-lane, unclaimed,
   peer-claimed, replan, concurrent proposal, and in-flight lease scenarios.
2. **Stage 1 — read-only alignment.** Add `shared_goal_alignment_v0` with
   canonical revision binding, per-Agent frontier basis, unclaimed work, and
   drift/conflict facts. An optional Decision Context extension may pair an
   exact host-session scope with bounded advisory recall. No writer changes.
3. **Stage 2 — proposal only.** Validate and retain
   `goal_amendment_proposal_v0`; proposals have no canonical effect.
4. **Stage 3 — one bounded commit class.** Implement governed commit for a
   shared work-graph amendment that preserves root intent, including automated
   policy commit, CAS, receipt, replan settlement, and lease impact handling.
5. **Stage 4 — provider-neutral shadow/parity.** Map the reviewed transaction
   to the file reference provider and optional NoKV/PostgreSQL candidates;
   compare projections and recovery without changing default authority.
6. **Stage 5 — TEST ONLY shared canary.** Exercise two peers, concurrent
   proposals, unclaimed claim, response-loss recovery, stale bases, and
   protected changes before any authority-source promotion.

Acceptance and operational-objective commits are not the first runtime slice.
They require demonstrated need and a separately reviewed automated policy and
verifier contract. Permission expansion or escape from root intent cannot be
auto-committed unless Goal creation explicitly delegated that exact class.

## 11. Validation matrix

At minimum, tests must prove:

- own-lane replan cannot change canonical intent;
- unclaimed work is visible but cannot execute before claim/lease;
- a pending proposal does not affect unrelated peers;
- policy and verifier decisions are bound to the exact proposal digest;
- two conflicting proposals from one base yield at most one canonical commit;
- stale revision/digest commits fail closed;
- response-loss recovery returns the original receipt;
- protected changes cannot be committed by proposer, scheduler, lease holder,
  verifier, or provider operator identity alone;
- routine in-envelope amendments complete without human approval, while an
  out-of-policy proposal never silently expands authority;
- in-flight leased work receives an explicit disposition; and
- all Agent projections rotate or gate after a canonical revision changes;
- a host-task locator resolves only through the current project binding and
  grants no claim, lease, lifecycle, verifier, or amendment authority;
- disabling or removing the advisory provider does not block authority-source
  collection, while amendment submission still independently requires the
  current Goal revision and intent digest.

Existing Goal-amendment and authority-store conformance tests remain the owners
of durable proposal, receipt recovery, and cross-Goal commit isolation. The
optional locator/provider tests must not duplicate those state-machine suites.

## 12. Non-goals

This version does not define automatic voting or consensus, CRDT/offline
multi-writer merge, an omniscient planner, a permanent leader, direct Agent
writes to storage providers, broad migration of LoopX state, or autonomous
escape from the immutable root user intent. Human approval is not a required
step in the normal amendment lifecycle. Host-session locators, deep links, and
transcripts are also not part of the Goal aggregate or a durable evidence store.

The smallest useful outcome is a legible, read-only alignment projection and a
proposal that is visibly non-authoritative. Runtime commit follows only after
that boundary proves useful in real multi-Agent work.

## Appendix A: Execution ledger

Dated checkpoints for this RFC live in the
[per-entry ledger](ledger/shared-goal-alignment-and-governed-amendment-v0/), one
file per measured slice. Sections 1–12 above stay the normative contract; a
ledger entry records what shipped and what it did not establish without
changing that contract.
