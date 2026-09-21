# RFC: LoopX Shared Control-Plane Authority and Pluggable State Providers (v0)

- Status: Draft, under maintainer review
- Initially proposed by: NoKV Lab
- Widened by: LoopX maintainers
- Date: 2026-08-05; revised 2026-09-13
- Scope: one provider-neutral LoopX authority contract with built-in file,
  optional NoKV, and optional PostgreSQL provider profiles, complementing
  [`host-integration-surface-v0`](../../reference/protocols/host-integration-surface-v0.md)
- Source baseline: LoopX `a0c20f1779d273e7aaa4bd3ea166d145d466e6d5`
- Provider API baseline: NoKV `7bb3ffd6512fd57d9c0f193aa6d9c5b935d77f30`
  (release 0.11.0, Python API 1, Holt pinned to 0.8.6). The Stage 2A executable
  qualification admits only that SDK contract and this checkout's helper. It
  remains candidate evidence, not a merge gate or authority promotion
- PostgreSQL baseline: the TypeScript Stage 2B candidate implements the store
  contract, transaction-local tenant context, forced row-level security, and
  bounded canonical commit admission, and has passed a real PostgreSQL 16
  transaction matrix. Subsequent delivery adds in-process service admission,
  principal/tenant verification injection points and restore-incarnation rotation.
  Deployed authenticated transport, cross-host runtime callers, measured
  capacity/retention and authority promotion are not established by those seams
- Language note: the
  [Chinese version](./shared-goal-authority-state-provider-v0.zh-CN.md) and this
  English version are semantic mirrors. A difference between them is a defect.

## Persistence route for steward scale (2026-09-16)

[Roadmap](loopx-overall-roadmap-v0.md) R5 reuses D1 projection, D2 real-backend/capacity/applicable ten-day soak and D3 fenced cutover. R6 connects the selected shared profile to authenticated local/cloud execution. R1–R3 can advance on supported profiles without waiting for PostgreSQL or whole-Goal default promotion.

`e94759d88` adds [PostgreSQL service admission](../../reference/postgresql-authority-service-v0.md), with authentication/tenant verification injection and identity rotation. It is an in-process service boundary, not a deployed network service. The P lane should reuse it and finish transport, real identity policy, pool/cancellation/failover and operations qualification rather than rebuilding admission. R7 must separately report registration, active executors and measured capacity. Directory, presence, a plan or one source read grants no shared authority. Existing fail-closed source, receipt/replay and rollback contracts remain.

## Current implementation checkpoint

The local registry witness now spans canonical create/claim/update/Monitor poll
and terminal mutations through one TS owner. File, SQLite and service-injected
PostgreSQL execute the same source checks and preserve historical receipts.
The [witness contract](../../project-agent-todo-contract.md#canonical-registry-source-witnesses)
distinguishes source freshness, operation identity and provider CAS. This is
optimistic source validation, not cross-resource atomicity or provider promotion;
existing L1–L5 qualification and default-cutover owners retain the remaining work.


The machine-owned coordination projection now has one packaged,
provider-neutral record contract shared by Python and TypeScript. File, NoKV,
and PostgreSQL candidates consume the same canonical Todo read shape; a
provider-bound projection rejects an unknown field rather than silently losing
it. Removing a declared field requires explicit compatibility evidence and
maintainer approval, even when the field is stored but not yet read by a
decision path.

The native domain alternative now separates Markdown location metadata while
retaining archival semantics. Appendix C's Todo domain/projection decision
records the compatibility boundary and the next local qualification plan;
the existing v0 capture and persisted heads are not silently migrated.

This does not promote a provider or make the whole active-state Markdown file
generated. Markdown remains canonical in default local mode. In a later,
explicit shared-authority promotion, only sections covered by the typed
contract become deterministic compatibility projections; free-form human
narrative remains outside the coordination head.

### Manager integration checkpoint (2026-09-13)

Source audit at `7eb4b7bb1661bd5eff63a8725a33169792d5964b` confirms the
`AuthorityStore` seam and the transaction/presentation/journal consolidations
in #4280, #4283 and #4287. This updates the integration baseline, not the
qualification evidence or historical provider baselines above. Candidate
SQLite/PostgreSQL paths, provider-specific holds and the D1–D3 plan remain;
neither a default source switch nor a shared service is declared shipped.

The [capable manager and semantic handoff RFC](capable-manager-semantic-handoff-v0.md)
consumes this authority. Its M1 host-tool work and M2 request-ledger refactor
can proceed without provider promotion. Section 1.4 defines their boundary;
the [TS execution cards](typescript-control-plane-migration-v0.md#execution-cards-after-the-current-stack)
still own business-rule consolidation and legacy-caller deletion.

### Local provider opening boundary (2026-09-13)

The local runtime now resolves File, SQLite, and the medium-term PostgreSQL
profile through one typed provider handle. No selector means the explicit File
default; a SQLite selector remains an opt-in local profile; a PostgreSQL
selector is accepted only with a service-owned factory that supplies the
provider-neutral `AuthorityStore`. The selector contains no credentials or
database client and binds the selected store identity before any command runs.

This boundary removes per-command provider construction and corrects the
observable source label for injected PostgreSQL stores. A selected-provider
failure preserves its source and fails closed; it cannot fall back to File or
Markdown. The refactor prepares the File/SQLite default path and a switchable
PostgreSQL deployment without changing promotion, D2 soak/retention, or D3
whole-Goal cutover holds.

## Document map and maintenance contract

This RFC separates durable decisions from delivery evidence:

- Sections 0-10 define the problem, authority contract, provider boundary,
  migration rules, and acceptance criteria.
- Section 11.1 is the normative delivery plan. Section 11.2 is a
  non-normative execution ledger: it records what a dated `main` revision has
  proved, but does not silently amend the contract. Section 11.3 lists the
  remaining qualification and promotion work.
- Section 12 contains unresolved owner decisions. An implementation may not
  infer approval from a proposed answer.
- Appendices A and B retain evidence and decision history; Appendix C contains
  the detailed Stage 2C contract proposed by Section 12. When implementation
  changes, update the ledger; when architecture changes, update the normative
  section and record the decision explicitly.

RFC maturity and delivery maturity are independent. A shipped experiment does
not accept this RFC, and a dated status entry never overrides a normative
invariant. If the execution ledger becomes difficult to review, it moves to a
companion `*-execution.md` document without dropping its evidence links.

---

## 0. An Example to Help Everyone Understand

Example 1: a walkthrough of a real machine -> human -> machine handoff, and why
it wastes time

Suppose my agent on the devbox finishes a Rust PR, and the agent on my laptop is
responsible for review. The handoff looks like this:

- **T0**: the devbox agent finishes the change and opens the PR;
- **T1**: the code is delivered at a pinned head SHA. Git already handles this,
  so moving the code is not the problem;
- **T2**: I manually send the PR, source task, and review instructions to the
  laptop agent so that it knows there is work to do;
- **T3**: the laptop agent takes the work, but if its response is lost, I can
  only infer later from its behavior whether the claim actually succeeded.

T2 and T3 are where the time disappears. One machine has already finished, but
the next machine is still waiting for a person to relay the work. Even after it
takes the job, there is no proof that can be recovered after a crash. A faster
harness or model cannot make up for the hours when the human is away.

The full need clearly includes both "tell the next machine" and "prove that it
really took the work." The first RFC should not swallow messaging, scheduling,
quota, run history, and every LoopX file at once. This version starts with the
hardest small piece: when the laptop claims the review todo, only one endpoint
may win, and that winner must be able to replay its original receipt. Delivery
and wake-up remain with the
[`Agent IM, LoopX, And OpenViking Collaboration v0`](./agent-im-openviking-collaboration-v0.md)
delivery plane.

## 1. What This RFC Chooses

Think of the authority as the only bookkeeper. Endpoints never edit the ledger
directly; they submit a request saying, "I want to claim this work." The
bookkeeper checks the target todo, identity, named dependencies, and gates. If
the request passes, the claim, lease, and receipt are written together. If it
fails, the requester gets an explicit reason.

This time the bookkeeper gets one small ledger. We are not moving every LoopX
file into a remote store, and we are not creating three competing semantic
authorities:

1. one goal that explicitly opts into shared mode has one **canonical
   coordination aggregate**;
2. each successful operation's state transition and original receipt land in
   the **same compare-and-set (CAS)**;
3. the LoopX authority makes decisions, while file, NoKV, or PostgreSQL
   providers only persist reviewed transactions reliably;
4. run history, status, quota, scheduler state, host sessions, and evidence
   bodies stay with their existing owners instead of entering this ledger.

NoKV and PostgreSQL are optional providers behind the bookkeeper. Agents do not
connect directly to either provider for controlled writes, and neither provider
becomes the LoopX control-plane authority. The file provider is the first
deterministic/parity backend; the current local file-based control plane remains
the default authority path until a separately reviewed authority-source
promotion.

```text
Agent client
    |
    v
LoopX authority API / embedded authority
    |
    v
typed LoopX transactions
    |
    v
provider-neutral store contract
    |------------|-------------|
    v            v             v
   file         NoKV       PostgreSQL
```

The invariant is one semantic writer regardless of deployment. A shared
service may host the LoopX authority together with authentication, tenancy,
audit, and a PostgreSQL or NoKV provider. Those are deployment concerns: an
Agent still calls LoopX APIs and cannot bypass the authority by writing tables,
documents, or files directly.

The first runnable slice began with one command, `claim_work`. For an existing
eligible todo, it records the soft claim, lease/fence, and receipt together.
Stage 3 extends that reference contract with renew, release, reclaim, and
completion while keeping the modules coverage-only. The initial slice answers
the two questions most likely to cause an incident—who wins when two endpoints
claim at once, and how a lost response recovers its original receipt.

The contention unit is `(goal_id, todo_id)` plus the preconditions actually
referenced by that todo, not the whole goal. Two endpoints claiming the same
todo yield one winner. Two endpoints claiming independent todos under one goal,
with unchanged target-scoped authorization, dependency, and gate facts, should
both succeed even if their first writes compete for the same aggregate CAS. The
authority reloads, revalidates, and retries internally instead of exposing an
unrelated provider-head advance as a domain conflict. This preserves the
todo-level concurrency shape in LoopX's current public contract in
[`architecture.md`](../../architecture.md).

Here is the failure sequence that matters most. Operation A succeeds with lease
`L1`, epoch `7`, but its response is lost. Operation B then advances another
independent todo in the same goal; B does not take over A's todo. When A
restarts and retries the same request, it must recover its original receipt
field for field. "This was already applied" plus B's current revision is not
enough: without `L1`, epoch `7`, and its expiry, A still cannot prove that it
was authorized to do the work.

### 1.1 The durable abstraction: storage, semantic authority, and reconciliation

The highest-value boundary is not "the LoopX server" versus "the NoKV server"
as two competing databases. It is three separately owned layers that may be
co-deployed while preserving different correctness contracts:

| Layer | Owns | Must not own |
| --- | --- | --- |
| Storage plane | Durable bytes and artifacts, provider generation CAS, snapshots, and provider recovery | Goal/todo meaning, actor eligibility, leases, or authority receipts |
| Semantic authority | Normalized commands, target-scoped preconditions, claims, lease epochs, fencing, revisions, and original receipts | Raw artifact bodies, provider placement, or background scheduling state |
| Reconciliation layer | Observation, expired-lease recovery, wake-request emission, and continuous Supervisor decisions through the same command contract | Direct head mutation, provider bypass, or a second source of coordination truth |

The built-in file backend is the first parity implementation; NoKV is the
initial shared-store candidate; PostgreSQL is the planned service-provider
profile. Each must pass the staged qualification below. The LoopX increment is
the semantic authority that turns an opaque provider transaction into a trusted
`claim_work` result and, later, into recoverable execution ownership.
The reconciliation layer is a client of that authority, not another writer: a
Supervisor observes projections and issues typed commands such as reclaim or
requests a delivery-plane wake, while its scan cursor and scheduling state
remain outside the coordination head. The delivery plane still owns transport
and endpoint reachability; a delivered wake never proves an authority command
committed.

The same NoKV deployment may serve two intentionally separate paths:

- the **coordination path** stores only the canonical head and is writable only
  through authority-owned credentials;
- the **artifact path** may accept scoped checkpoint or evidence publication
  from a runtime, but only an opaque pointer, digest, and privacy class enter a
  reviewed coordination transition.

A recoverable workflow publishes an immutable checkpoint or evidence artifact
first, then commits its pointer with the relevant coordination transition.
Failure before the second step leaves an unreferenced artifact for independent
retention or collection; it must not leave the head pointing at an object that
was never durably published. Sharing a physical provider therefore does not
merge the two ownership contracts or require a cross-domain transaction.

Physical co-location is allowed. One deployment bundle may start a NoKV or
PostgreSQL provider, a LoopX authority endpoint, and a Supervisor worker. Here,
"separate" means separately owned contracts and credentials; it does not
require a separate repository, process, binary, or license in v0. An embedded
authority is sufficient for trusted local qualification. A shared deployment
needs an online authority boundary so that untrusted or stale clients cannot
publish a forged coordination head even when they legitimately hold scoped
artifact credentials.

### 1.2 Capability horizon and deployable product boundary

The layers above create a staged capability horizon without widening the v0
ledger:

| Horizon | Added capability | Proof required before promotion |
| --- | --- | --- |
| Deterministic shared coordination | Provider-neutral authority, state-plus-receipt CAS, replay, and target-scoped rebase | File-backed conformance and the P0 checks in Section 10 |
| Recoverable execution ownership | Renew, release, expired-lease reclaim, stale-fence rejection, and atomic completion with an accepted continuation/evidence pointer | Crash and clock-boundary tests showing that a superseded executor cannot write back |
| Continuous reconciliation | Supervisor observation, reclaim, delivery-plane wake requests, and remote-resume orchestration through authority commands | Restart-safe reconciliation with no direct provider writes or in-memory correctness dependency |
| Service-grade shared control plane | Authenticated principals, tenant-to-goal isolation, audit, bounded capacity, observability, service recovery, and eventually HA | An explicit deployment and migration contract with no authority bypass or silent local fallback |

This horizon does not move quota accounting, run history, raw evidence,
delivery state, or Supervisor runtime state into the coordination aggregate.
Those capabilities keep the owners and ledgers listed in Section 3 and connect
through typed commands, projections, or opaque pointers.

A thin network wrapper around the Apache coordination core is not by itself a
new product boundary. A separately distributed service becomes meaningful only
when it owns the network trust boundary and material authority/reconciliation
capabilities such as authentication, continuous supervision, remote recovery,
migration, audit, multi-tenancy, or HA. If the project later creates a
separately licensed server distribution, that boundary should follow this
deployable semantic-authority and reconciliation surface, not the NoKV adapter
or provider-neutral core. This RFC neither requires a repository split nor
selects separate license terms; the current policy remains
[`LoopX Licensing`](../../project/licensing.md).

### 1.3 Prospective license path by distribution boundary

This subsection is non-normative and does not change the license of any current
LoopX source or release. It records the boundary that a future license RFC
should evaluate if the service-grade horizon becomes a separately shipped
product.

| Distribution boundary | Delivery horizon | Recommended path |
| --- | --- | --- |
| RFCs, schemas, typed commands, receipts, provider-neutral decisions, store contracts/codecs, client SDKs, conformance fixtures, and examples | Stages 1-4 | Remain in the Apache-2.0 open core so runtimes and providers can implement one coordination contract without adopting a server distribution |
| Embedded/local authority, file parity backend, and shared-store adapters for NoKV or PostgreSQL | Stages 1-4 | Remain Apache-2.0; an adapter does not acquire LoopX semantic authority, and the underlying provider keeps its own license |
| Test-only shadowing, canary, migration fixtures, and authority-source promotion proof | Stages 3-4 | Remain Apache-2.0 qualification material; evidence that a deployment is correct is not by itself a separately licensed product |
| Independently versioned shared-authority server that owns authentication, tenant isolation, audit, durable receipt service, migration/promotion, capacity controls, recovery, and HA | Stage 5 | May be evaluated for AGPL-3.0 in a separate license RFC once this is more than a thin wrapper around the Apache core |
| Persistent Supervisor/reconciliation worker shipped as part of that server distribution | Stage 5 | May follow the server's AGPL-3.0 candidate terms when it owns restart-safe observation, reclaim, remote-resume orchestration, and wake requests through authority commands |
| Separately delivered managed operations or enterprise-only modules | After a service boundary exists | May use separate commercial terms when they are not included in the Apache or AGPL distribution and their dependency boundary is explicit |

An illustrative package boundary, if these distributions are later created,
is:

```text
loopx/control_plane/coordination/             Apache-2.0
packages/loopx-authority-client/              Apache-2.0
packages/loopx-authority-provider-*/          Apache-2.0
packages/loopx-shared-authority-server/       AGPL-3.0 candidate
packages/loopx-persistent-supervisor/         AGPL-3.0 candidate, or part of the server
```

The names are illustrative rather than a Stage 0 layout requirement. The
dependency direction is the durable rule: an AGPL server distribution may
consume Apache contracts, core logic, and providers; Apache artifacts must not
import, bundle, or require the AGPL server. The NoKV or PostgreSQL server is
not relicensed by a LoopX adapter, and an adapter must not be used as an
artificial license boundary.

Any Stage 5 license proposal must satisfy all of these gates before changing
the current policy:

1. identify one independently deployable and versioned server artifact;
2. show that the artifact owns a real network trust and reconciliation
   boundary rather than only forwarding Apache-core calls;
3. preserve Apache client, protocol, embedded-mode, provider, and conformance
   paths for interoperable adoption;
4. define package metadata, nested license/NOTICE files, SPDX markings, and
   build checks that prevent cross-license bundling;
5. decide the inbound contribution policy before accepting contributions to
   any AGPL component, especially if commercial dual licensing may be needed;
6. apply any change prospectively through an explicit version boundary without
   narrowing rights already granted by MIT or Apache releases.

The current RFC therefore keeps Stages 1-4 under the repository's Apache-2.0
policy. Stage 5 creates a decision point, not an automatic license transition.
No source path becomes AGPL-3.0 merely because it implements a shared-authority
contract or passes a remote-provider canary.

### 1.4 Manager requests and semantic handoff integration

The [manager/handoff RFC](capable-manager-semantic-handoff-v0.md) owns the
user-facing exchange and general request/assessment/result relations. This
RFC owns the reviewed coordination state and its commit proof. A semantic
brief, conversation, delivery attempt or scheduler cursor does not enter the
v0 coordination head. Relevant evidence is published by its artifact owner
and referenced at an exact revision and disclosure scope. Sharing a physical
provider does not merge these logical state families or their access/retention
contracts.

Each Goal retains one selected authority source. When adoption changes
Todo/lease/Vision state, handoff invokes that existing owner and links its real
receipt; consultation or assessment without a work effect does not. A separate request commit
cannot make that work mutation atomic across stores: persist intent, recover
the original work receipt, then reconcile the pending relation. Cross-Goal
requests likewise preserve per-Goal bases and outcomes; they do not require
a distributed commit or invent one Goal-wide provider revision for both.
Transport acknowledgement never substitutes for a current claim or fence.

Manager reads and receiver writes must preserve canonical empty/failure
semantics after promotion, without old-Markdown or lease-file fallback.
Permanent Markdown presentation remains. The handoff RFC's A15 fixture checks
these integration boundaries against legacy and explicitly configured canonical
sources; it does not repeat or replace this RFC's backend conformance, retention,
recovery, soak or cutover qualification.

The [shared Goal alignment/amendment RFC](shared-goal-alignment-and-governed-amendment-v0.md)
owns intent-change legality and its future governed commit. Its current
proposal admission creates no canonical amendment. Neither the manager nor
the provider becomes that authority. M1–M3 manager work may run in parallel
with T1–T3/D1/D2; changing storage/profile/source or retiring a whole legacy
Goal writer still requires the applicable D3/T4 boundary. A new handoff is
not a provider promotion request.

Hierarchical coordination uses these same boundaries at every hop. Parent/request
lineage is not an authority token, and a child coordinator does not receive a
fresh copy of the team's quota. Admission, reservations, claim/lease and accepted
effects stay with their existing typed owners; provider session status and
inbox/queue/steer receipts remain execution or transport observations. A native
Goal evaluator cannot declare canonical work complete on their behalf.

For the roadmap's first mixed-execution slice, an already supported local
authority may receive scoped commands through a host facade while cloud Agents
execute work. This creates neither a new authority provider nor independent
cloud writers. An outage must preserve the selected-source and stale-fence
rules; it cannot trigger local/cloud authority fallback. Independently scheduling
hosts still require authenticated service transport and applicable D1–D3
qualification. In-process admission code is not evidence of that deployment.

## 2. What We Will Do, and What We Will Not

**What this version will do**

- give one shared goal an online, provider-neutral coordination authority;
- make concurrent claims on the same todo resolve to one accepted owner while
  allowing independent todos to succeed after an internal CAS rebase;
- bind each operation identity to one normalized request digest and reject the
  same id when it is reused with different semantics;
- recover the original receipt after later operations advance the ledger;
- define one semantic authority boundary and the file, NoKV, and PostgreSQL
  provider profiles behind it;
- keep LoopX's default local mode unchanged;
- say how every existing durable state category joins—or stays outside—this
  boundary.

**What this version explicitly will not do**

- build a generic distributed filesystem or database for all LoopX state;
- support offline multi-writer merge or offline controlled writes;
- mix message delivery, wake-up, presence, or an Agent IM protocol into the
  storage contract;
- ship production multi-tenant deployment, authentication, HA, or provider
  failover in the Stage 0 reference slice;
- move quota, scheduler state, run history, raw evidence, host sessions, or
  extension-owned ledgers into the coordination head;
- automatically promote the current event projection or any provider;
- let an agent or extension bypass the authority and connect to storage.

## 3. What LoopX Stores Today, and How Each Part Connects

The owner's key question was that LoopX state already lives across several
files, and not every durable file belongs in one head. Two machines can both
have a correct local route; status is computed; quota accounting is an
append-style ledger. These are different write models.

The table below therefore lays the ledgers out first: who writes each one
today, how it is written, and where it belongs after shared mode exists. It is
organized by logical state and field group rather than a fixed file count. One
physical file may mix several owners, and a new host or extension may add local
artifacts without changing this RFC.

Integration classes used below:

- **shared canonical**: authoritative only after explicit shared-mode opt-in;
- **derived**: recompute from its named source; never accept lifecycle writes;
- **synchronized ledger**: replicate or union by stable identity under its own
  append contract, not through the coordination head;
- **host-local**: valid only for one host, runtime, or checkout;
- **independent ledger**: retains its capability or accounting owner;
- **excluded body**: only a redacted digest or pointer may cross the boundary.

| Logical state / current surface | Current owner and write model | v0 integration strategy |
| --- | --- | --- |
| Todo lifecycle, soft claim, dependency, and gate fields in `ACTIVE_GOAL_STATE.md` | Markdown active state remains the current source of truth. Todo commands use a local file lock and whole-document replacement. A state-event projection is used only when an event log already exists. | Only the normalized fields required to validate the P0 commands become **shared canonical** after opt-in. Markdown remains canonical in default mode and becomes a local projection for migrated fields in shared mode. Private prose stays excluded. |
| Optional hard task leases under `goals/<goal>/task-leases/` | Per-todo JSON is atomically replaced under a goal-local lock; release retains an inactive terminal record so version and `lease_epoch` cannot ABA across re-acquire. Lease effectiveness also reads todo status, soft claim, exclusions, and registered agents. | Fold claim, lease, terminal generation, and fence into the same shared aggregate and authority revision. Do not keep an independently writable lease file in shared mode. |
| Applied-operation receipts | LoopX has scoped receipt precedents, including Turn journals and heartbeat receipts, but no durable shared-goal operation-to-receipt index. | Add the replayable receipt index as **shared canonical**, committed in the same CAS as the state transition. |
| Project-registry logical identity, agent profiles, grants, and policy | Project registry is local configuration written as a JSON replacement. These fields are mixed with routes and private references. | The authority consumes an explicitly versioned compact authorization projection or digest. The whole registry is not stored in the coordination head, and registry mutation is not a P0 command. |
| Project/global registry routes: `source_registry`, repo checkout, state file, runtime root | The global registry is a synchronized host-local route projection and records absolute local paths. | **Host-local**. Share stable goal/repository identity where needed, never route paths. |
| Candidate state events in `events.jsonl` | The current migration bridge keeps Markdown authoritative. Event append uses local locking; multi-event append is not a transaction. | Read-only shadow/canary input. This RFC does not promote it or use it to prove atomic completion. |
| Run JSON/Markdown and raw evidence bodies | Run writers reserve local artifact names and then write detailed records. Content and paths may be private. | **Excluded body** or external artifact-store object. The aggregate may carry only an opaque pointer, digest, privacy class, and exact code revision when a later command requires them. |
| `runs/index.jsonl` run history | A mixed append index references run artifacts and may contain absolute paths. It also carries several classifications, including quota accounting rows. | A future **synchronized ledger** needs stable identities, deduplication, and redaction. It is not part of the coordination head. |
| `rollout-event-log.jsonl` | A mixed public-safe diagnostic stream. Core CLI rollout append is intentionally best-effort and happens after the primary command; ordinary todo events are not keyed by the controlled operation identity. | **Derived** observability projection. A failed rollout append cannot invalidate or prove a coordination commit. |
| Status and attention, including `status-projection-cache/*.json` | Status is derived from registry, active state, run history, leases, and other inputs. Its optional cache is a replaceable host-local snapshot whose key includes local route inputs. | **Derived**; cache remains **host-local** and may be discarded. |
| Agent runtime capability observations | Live quota/Turn entry remembers a bounded set of declared runtime tools; local unavailable observations override inherited Goal declarations. | **Host-local**, scoped by runtime, registry, Goal and registered Agent. Never copied into Goal configuration, shared grants or coordination heads. Read-only planning and explicit correction/forget use the same typed owner. |
| Quota policy | Local policy is configured in registry fields. | Configuration input outside the head. A receipt may reference the policy revision used, but the coordination provider does not own policy. |
| Quota accounting (`quota_slot_spent` / `quota_slot_voided`) | Detailed JSON/Markdown plus `runs/index.jsonl` rows form an append-style accounting history. Current rows have no shared operation identity or cross-artifact transaction. | **Independent ledger**. A distributed implementation needs idempotent debit/void identities and its own retention contract. |
| Quota enforcement and `should-run` decisions | Computed from policy, todo/status projections, run history, scheduler context, and actor scope. A heartbeat receipt is a specialized rollout use. | **Derived decision**. If a future global budget gates claims, it should issue a separate reservation/grant receipt; the head may reference that receipt but must not absorb the quota ledger. |
| Scheduler state, liveness, host backoff, and RRULE observations | Per-goal, per-agent, per-surface JSON reflects the host that owns the scheduler. | **Host-local**. Never resolve two valid host observations by overwriting one global value. |
| Turn journals, `turn-sessions/`, and Pi `.loopx/pi/` bindings | Runtime recovery and session bindings are written for one host/session and may contain local paths or task bodies. | **Host-local**. Turn journals are a receipt-design precedent, not shared coordination state. |
| Supervisor, domain-state, and extension runtime files | Each capability defines its own schema, privacy, append/upsert rules, and effect receipts. | **Independent ledger** or **host-local**, according to that capability contract. No generic import into the head. |

Source anchors for the classifications include
[`architecture.md`](../../architecture.md),
[`event-store-migration-bridge-v0`](../../reference/protocols/event-store-migration-bridge-v0.md),
`loopx/control_plane/work_items/task_lease.py`,
`loopx/cli_rollout.py`,
`loopx/control_plane/runtime/status_projection_cache.py`,
`loopx/control_plane/quota/slot_accounting.py`,
`loopx/global_registry.py`, and the host state in
`loopx/control_plane/scheduler/state.py`,
`loopx/control_plane/turn_driver/codex_cli.py`,
and `loopx/pi_goal_mode/pi-goal-loop-runtime.mjs`.

## 4. What Goes Into This Coordination Ledger

One provider key stores one goal's aggregate (head schema v1). The illustrative shape is:

```json
{
  "schema_version": "loopx_coordination_head_v1",
  "goal_id": "shared-rust-review",
  "store_binding": "nokv:wb-goals:1f2e3d4c...",
  "authority_revision": 43,
  "coordination": {
    "todos": {
      "todo_review": {
        "todo_revision": 9,
        "status": "open",
        "claimed_by": "laptop-reviewer",
        "eligibility": {
          "authorization_projection_revision": 3,
          "authorization_projection_digest": "sha256:...",
          "allowed_agent_ids": ["laptop-reviewer"],
          "dependencies_satisfied": true,
          "dependency_revision": 12,
          "gates_open": true,
          "gate_revision": 5
        },
        "repository": "git:example/repo",
        "code_revision": "0123456789abcdef",
        "last_lease_epoch": 7
      }
    },
    "leases": {
      "todo_review": {
        "lease_id": "lease_...",
        "owner": "laptop-reviewer",
        "lease_epoch": 7,
        "expires_at": "2026-08-06T03:30:00Z",
        "write_scopes": []
      }
    }
  },
  "receipt_index": {
    "op_claim_review_01": {
      "request_digest": "sha256:...",
      "original_receipt": {
        "schema_version": "loopx_authority_receipt_v0",
        "operation_id": "op_claim_review_01",
        "request_digest": "sha256:...",
        "command": "claim_work",
        "actor": {"agent_id": "laptop-reviewer", "device_id": "laptop"},
        "todo_id": "todo_review",
        "accepted_authority_revision": 43,
        "accepted_todo_revision": 9,
        "applied_at": "2026-08-06T03:20:00Z",
        "lease_id": "lease_...",
        "lease_epoch": 7,
        "expires_at": "2026-08-06T03:30:00Z"
      }
    }
  },
  "receipt_retention": {"mode": "retain_all_v0"}
}
```

The schema contains no raw todo body, transcript, credential, absolute path,
or raw evidence. It contains only the facts needed to adjudicate the command
slice and recover its proof. As in current LoopX, a claimed todo remains `open`,
without inventing a `claimed` lifecycle status that local mode does not have.
In the target contract, `claimed_by` carries soft ownership and the lease/fence
carries execution authority. The default `legacy` handoff mode does not honor
this: when the two records diverge, the markdown claimant wins on the write
path. A goal that declares `hard_lease` turns the divergence into a typed
error and requires the key at completion. See Appendix B.

Each eligibility revision or digest is scoped to the snapshot referenced by the
target todo: authorization covers only that todo's actor scope, dependency
covers only its transitive dependency closure, and gate covers only gates that
actually constrain it. These are not goal-wide revisions under new names. The
reference slice fixes `write_scopes=[]`. When non-empty scopes are introduced,
overlap with another active lease is a real cross-todo precondition: an internal
rebase must revalidate it and reject the claim on overlap.

Those target-scoped tokens have a coverage and no-ABA obligation. Any semantic
change to the target's claim state or lease epoch must advance `todo_revision`.
Any change to its allowed actors, dependency closure or satisfaction decision,
or constraining gate set or decision must advance the corresponding revision
and digest where present. A token must never be reused for a different snapshot.
If the authority cannot prove this coverage, it must not internally rebase. The
deterministic reference validates a static bootstrap snapshot; it does not yet
qualify a dynamic publisher for these projections.

The original receipt proves that an operation was accepted at one authority
revision. It does not prove that its lease remains current. A replay response
therefore returns the field-for-field equivalent `original_receipt` plus
separately named current observations such as `observed_authority_revision` and
`authorization_status=active|expired|superseded`.

## 5. How a Command Lands and How a Lost Receipt Comes Back

The request envelope uses `operation_id` to avoid collision with existing CLI
uses of `command_id`:

```json
{
  "schema_version": "loopx_command_v0",
  "operation_id": "op_claim_review_01",
  "actor": {"agent_id": "laptop-reviewer", "device_id": "laptop"},
  "goal_id": "shared-rust-review",
  "command": {
    "type": "claim_work",
    "todo_id": "todo_review",
    "expected_todo_revision": 8,
    "expected_preconditions": {
      "authorization_projection_revision": 3,
      "authorization_projection_digest": "sha256:...",
      "dependency_revision": 12,
      "gate_revision": 5
    },
    "lease_ttl_seconds": 600
  }
}
```

The authority normalizes the complete semantic request and computes
`request_digest`. Actor, goal, command type, target todo revision, named
authorization/dependency/gate preconditions, and command parameters are
covered. Transport retry metadata is not. The goal-wide `authority_revision`
is not a client domain precondition and is not part of the request digest. A
caller may carry a previously observed head revision only as transport
metadata; changing that observation does not create a new semantic operation.

Operation identity names a caller's logical attempt, not the parameter tuple.
Mint an id once outside transport retries and reuse it for that attempt; a UUID
is valid for this purpose. A later independent call may have identical parameters
and still require a new id (for example, setting a value again after another
writer changed it). Hashing parameters forever would replay stale history;
hashing a newly observed provider revision cannot recover the original id after
a commit whose response was lost.

In the shipped claim/update contract, `changed=false` describes Todo/lease state,
not the absence of a storage write: a first accepted named no-change operation
persists its terminal receipt under CAS. Retrying that id after a later state
change must replay the original no-change result, not perform new work. An empty
archive selection has a different, explicit no-transaction contract; do not
generalize it to all verbs. Receipt-only history growth is a real storage cost,
but optimizing it must retain identity consumption, replay and conflict checks.

Current local facades reuse their generated id within managed-runtime retries.
Separate CLI invocations are not implicitly one attempt: claim exposes
`--claim-operation-id` and update exposes `--update-operation-id`; create does not
currently expose an equivalent cross-process recovery key. That is a caller-recovery limitation,
not proof of duplicate business effects or universal exactly-once execution.
Any extension must define the retry boundary and distinguish retries from new
intent before adding keys or durable attempt tracking. Test lost responses and
intervening writes; a source-level ban on UUID construction proves neither.

The canonical Todo commands now share one TS receipt recovery owner. Their
local result contract retains unresolved post-commit readback as `ambiguous`
and names the original operation for recovery; it does not infer no-write from
an unavailable receipt or retry the CAS automatically. See the
[command recovery checkpoint](typescript-control-plane-migration-v0.md#command-receipt-and-recovery-ownership)
for intentional diagnostic changes and the complete fixture matrix. Historical
receipt identity and one-way Markdown delivery remain intact. This is command
recovery qualification, not storage retention, service availability or promotion.

For every request, the authority performs this sequence:

1. load the aggregate and provider generation;
2. look up `operation_id` before applying current-state validation;
3. if the id exists with the same digest, return `already_applied` and the
   stored original receipt without writing;
4. if the id exists with a different digest, return typed
   `operation_identity_mismatch` and do not write;
5. validate actor scope, target todo revision, named preconditions,
   eligibility, claim state, and the empty-scope lease rules implemented by this
   reference slice;
6. compute the next coordination state and original receipt in the authority;
7. add both the transition and receipt-index entry to one deterministic
   envelope and submit one provider CAS;
8. after a conflict or ambiguous provider response, reload and repeat the
   receipt lookup before classifying the result;
9. if no receipt exists and the generation did not advance, fail closed with
   `provider_outcome_unproved`. If the generation advanced, revalidate the
   target todo and named preconditions; when those facts are unchanged, retry
   against the latest head. Receipt absence never proves success: an eventual
   `applied` requires a new successful CAS; and
10. after a CAS miss, stop rebasing when relevant facts no longer permit the
    command. Initial invalid requests still follow ordinary domain validation.
    An unrelated head advance is not a domain conflict. Exhausting the bounded
    contention retry budget returns typed `failed` and creates no receipt.

The API result classes are:

| Result | Meaning |
| --- | --- |
| `applied` | State and original receipt committed together. |
| `already_applied` | The same operation and digest committed earlier; the stored original receipt is returned. |
| `conflict` | No receipt exists for this operation and the target todo or a named precondition is stale. |
| `rejected` | Identity, eligibility, gate, or command validation failed without a state change. |
| `failed` | No accepted result can be proved, or unrelated provider contention exhausted the internal retry budget. Retry only under bounded infrastructure policy. |

`conflict`, `rejected`, and `failed` are not success proofs and do not receive
fabricated applied receipts.

### 5.1 The first command: `claim_work`

- `claim_work`: in one transition, verify an existing runnable todo, set its
  claimant, create a lease, mint the next lease epoch, and store the original
  receipt. Its required fields are `todo_id`, `expected_todo_revision`,
  `expected_preconditions`, and `lease_ttl_seconds`.

An accepted claim advances both `authority_revision` and the target
`todo_revision`. It operates only on a todo installed by explicit
bootstrap/migration and never creates an unknown todo as a side effect. The
deterministic reference eligibility input is the compact tuple
`allowed_agent_ids`, `dependencies_satisfied`, and `gates_open`, bound to the
named authorization, dependency, and gate revisions and digest.

`authority_revision` is the goal-wide commit sequence for accepted commands.
It serves audit, read-model, and receipt ordering; it is not a shared optimistic
concurrency precondition for every command. The aggregate still commits
serially through `provider_generation`. A CAS loser decides whether to rebase
from its target todo and named preconditions, rather than requiring a caller to
mint a new operation merely because an independent todo committed first.

Unknown command types fail closed. Transfer or delegated assignment,
arbitrary todo/gate mutation, quota reservation, and external effects still
require later runtime contracts and qualification. Non-empty write scopes and
cross-todo scope-overlap rejection likewise require a later command contract
and qualification. The recoverable-execution verbs below were called Stage 3
in the historical #3669 implementation sequence. Under the current delivery
sequence in Section 11, that merged work is part of the Stage 0 reference foundation,
not the Stage 3 remote-shadow phase in Section 11. Steps 1 through
4 and 7 through 10 of Section 5 (identity, digest, replay, CAS, reload, rebase,
budget) apply to every verb unchanged, and only the per-verb preconditions and
transition (steps 5 and 6) differ.

### 5.2 `renew_work`

Required fields: `todo_id`, `expected_todo_revision`, `lease_id`,
`expected_lease_epoch`, `lease_ttl_seconds`. The caller presents the fence it
believes it holds; the authority rejects a missing lease, a fence whose
lease id or epoch differs (typed `stale_lease_fence`), a caller that is not
the recorded holder (typed `not_lease_holder`), and a lease the authority's
own clock has already seen expire (typed `lease_not_active`; see Section
6.4). An accepted renewal extends `expires_at` from the authority clock,
keeps the lease id and epoch unchanged, and advances both
`authority_revision` and the target `todo_revision`: the validity interval
is a revision-covered fact, so a reclaim built on pre-renewal observations
conflicts instead of surviving the internal rebase.

### 5.3 `release_work`

Required fields: `todo_id`, `expected_todo_revision`, `lease_id`,
`expected_lease_epoch`. Release is the holder giving up early, so it is
valid only while the lease is still active: the claim is cleared under the
live holder gate and the lease is released in the same transition. The
accepted transition clears `claimed_by`, deletes the lease entry, and
advances both revisions, while the todo's `last_lease_epoch` watermark is
retained unchanged - in the shared aggregate the watermark IS the terminal
record, so the next claim mints strictly above it and release can never
A/B/A. An expired lease is not releasable; its resolution belongs to
reclaim.

### 5.4 `reclaim_work`

Required fields: `todo_id`, `expected_todo_revision`,
`expected_preconditions`, `lease_ttl_seconds`. Reclaim is a standing
delegation to eligible agents: when the authority's own clock has seen the
recorded lease expired for at least the reclaim grace window (Section 6.4),
any agent in the target's `allowed_agent_ids` may take the work over. The
authority adjudicates expiry and synthesizes the delegation; the core then
enforces everything else through the minimal privileged step - clear the
stale claim under the delegation, then run the ordinary claim composition,
so the new lease passes the same holder gate as any first claim. The
accepted transition mints the next lease epoch, sets the reclaiming agent
as claimant, advances both revisions and the watermark, and the receipt
records the superseded owner and epoch. A lease inside its validity or
grace window is typed `lease_not_reclaimable`; an unclaimed todo is typed
`todo_not_claimed` (use `claim_work`).

### 5.5 `complete_work`

Required fields: `todo_id`, `expected_todo_revision`, `lease_id`,
`expected_lease_epoch`, `no_followup`, `successor_todo_ids`, `evidence`.
Completion requires the active lease fence: the core's terminal gate
verifies the caller is the claimant and the holder of the presented fence,
and an expired or superseded fence is typed exactly like every other
stale write. One accepted transition records everything at once: the todo
becomes durably `done` with an explicit `completion_continuation` derived
from the recorded fields (`no_followup` | `successor` | `active_goal`,
with both-set combinations refused exactly like the local write), the
lease retires, declared successors are created as open, unclaimed,
revision-zero todos inheriting the parent's execution context, and the
optional evidence pointer lands on the completed record. Evidence is a
portability boundary, not free text: the `pointer` must use the
provider-neutral `artifact://<public|private>/<opaque-artifact-id>` URI
shape (never a host filesystem path, provider URL, query, or credential),
the URI privacy namespace must equal the sibling `privacy_class`, the
`digest` must be a sha256 content digest, and `privacy_class` is the closed
vocabulary `public | private` - per Section 1.1, never a body. The command
boundary and head validation enforce this through one shared oracle. The
persisted record satisfies the local durable-completion
projection seam field for field, so both worlds read one truth.

### 5.6 The stale-fence rule

Every fence-carrying verb presents `(lease_id, expected_lease_epoch)`. A
presented fence that does not match the recorded lease is a terminal typed
rejection - `stale_lease_fence` - and is never retried past by the internal
rebase: a superseded executor's writes stay rejected no matter how many
unrelated commands advance the aggregate. This, plus epoch minting on every
takeover, is the mechanism behind the Stage 3 horizon proof that a
superseded executor cannot write back.

## 6. Who Decides and Who Stores

### 6.1 The authority is the bookkeeper

The LoopX authority owns:

- request normalization and digesting;
- actor, todo, dependency, gate, and authorization validation;
- domain-conflict decisions over the target todo and named preconditions, plus
  bounded CAS rebase after unrelated head advances;
- the `authority_revision` commit sequence and todo revision transitions;
- time, lease id, lease epoch, and expiry minting;
- receipt contents and replay classification;
- privacy filtering and command-specific invariants.

### 6.2 The provider keeps the ledger durable

The provider contract is intentionally semantic-free:

```text
load()
  -> (aggregate | none, provider_generation)

compare_and_put(
  expected_provider_generation,
  aggregate
)
  -> applied(new_provider_generation)
   | conflict(current_provider_generation)
   | ambiguous
   | failed
```

The provider must serialize the complete aggregate deterministically and
provide atomic conditional replacement, durable success,
same-key read-after-write reconciliation, and a typed ambiguous result when it
cannot prove whether a write committed. It must not parse LoopX commands, mint
clocks or leases, decide eligibility, or synthesize authority receipts. The
domain `operation_id` and request-digest replay contract exist only in the
authority and its atomically stored receipt index; they are not provider API
arguments. A provider may generate a private publication-attempt identifier,
but that identifier has no LoopX authority meaning.

A provider instance or handle is bound to one `goal_id` and provider key before
these methods are called; omitting `goal_id` from the verbs does not make the
key global.

The receipt index cannot be a separately published document under this
two-verb contract. Publishing receipt first can record success for a transition
that never happened; publishing state first can lose the only proof after a
crash. Splitting it later requires a provider-neutral multi-record transaction
or commit-marker protocol and a new reviewed contract.

#### 6.2.1 Three provider profiles, one authority

| Provider profile | Role in this RFC | Promotion requirement |
| --- | --- | --- |
| file | Built-in deterministic and parity backend. Current Markdown, registry JSON, event JSONL, run files, and task-lease files remain canonical until their owning contracts are explicitly promoted. | Prove parity and crash recovery behind the same typed transaction boundary before any file aggregate becomes canonical. |
| NoKV | Optional shared-store provider for online coordination. It contributes generation CAS and store-lineage primitives, not LoopX semantics. | Close recovery/availability blockers, qualify capacity and HA, then pass shadow parity and the bounded canary. |
| PostgreSQL | Optional provider behind an authenticated LoopX authority service for multi-Agent or private deployments. PostgreSQL tables and roles are service implementation details. | Implement the same conformance contract, transaction isolation, fencing, idempotency, cursor, audit, and fail-closed network behavior; no direct Agent database access. |

Physical database or cluster co-location does not merge ownership. A deployment
should isolate LoopX control-plane records from application-domain records with
separate schemas and roles, and relate them only through opaque identities or
digests. Provider-specific payloads do not enter the provider-neutral LoopX
schema.

#### Retained-journal scan contract

`scanCommitted(after_cursor, limit)` binds the head, rows and lookahead to one
read snapshot. The current providers retain a contiguous journal from cursor 1;
`null` is the sole start checkpoint, and every supplied cursor is a positive
canonical decimal string. A positive checkpoint beyond the head, including an
empty store, fails with `scan_cursor_out_of_range` rather than acknowledging
successful exhaustion. Malformed runtime values fail before storage access.

The shared TS scan owner checks the exact requested interval, including the
lookahead row that proves `has_more`. Missing, repeated or reordered rows and a
last transaction inconsistent with the snapshot head fail as protocol violations.
PostgreSQL metadata/head/row reads use repeatable read; File/NoKV validate one
retained envelope and SQLite keeps its existing read transaction. This does not
introduce a snapshot token across pages: later calls may observe later commits.
A page does not certify rows before its checkpoint, arbitrary payload integrity,
or a future compacted/segmented history format.

File and NoKV share journal decoding and append construction in the existing
transaction module, while retaining their own revision digest inputs, identities,
CAS and durability effects. Validation covers all four adapters with the shared
complex fixture, real PostgreSQL concurrent commits and disposable corrupted
rows, plus isolated real-source File/PostgreSQL pagination. No active Goal
migration, default-provider change or D1–D3 qualification is implied.

#### 6.2.2 Target store contract after the reference CAS slice

The current Stage 2/3 reference implementation deliberately uses the smaller
`load` / `compare_and_put` document seam above. It proves the lifecycle and
receipt invariants needed before widening the persistence boundary; it does not
claim to implement the future service-grade transaction API.

The next LoopX-owned TypeScript boundary should express at least these logical
operations (exact names and wire shapes remain implementation decisions):

```text
load_authority(goal_id)
  -> head + provider_revision + cursor

commit_authority(
  expected_provider_revision,
  operation_id,
  events,
  next_projection,
  receipts
)
  -> applied | conflict | ambiguous | failed

read_receipt(operation_id)
scan_committed(after_cursor, limit)
```

This contract owns atomic event/projection/receipt persistence, opaque provider
revisions, cursors, and durable readback. LoopX still owns operation identity,
request normalization, legal transitions, claim/lease fencing, Turn admission,
quota semantics, settlement idempotency, and receipt meaning. In particular,
an adapter must not reinterpret a provider transaction result as a domain
decision.

The Stage 1 TypeScript contract makes read failure classes explicit. A proven
`missing` head is different from `unavailable` storage, while corrupt bytes or
an invalid lineage are `failed`; only proven absence may authorize bootstrap.
Commit has the closed storage outcomes `applied | conflict | ambiguous |
failed`. `ambiguous` means a publish was attempted but durability cannot be
proved from the response, so the authority must reconcile through
`read_receipt` and a fresh head read. A provider exception or human-readable
error string is never itself commit proof.

The three adapters implement those logical verbs through different native
primitives; the contract does not pretend they are interchangeable databases:

| Concern | File Stage 1 | NoKV Stage 2A | PostgreSQL Stage 2B |
| --- | --- | --- | --- |
| conditional revision | revision chain compared under the cross-process document lock | path `generation` passed to compare-and-publish | per-tenant/per-goal head-row revision, checked under a row lock or conditional update |
| atomic event/projection/receipt commit | one complete journal document, file fsync, atomic rename, directory fsync | one generation-CAS envelope; receipt and scan data remain embedded until a qualified multi-record protocol exists | one SQL transaction updating the head and inserting ordered event/receipt rows |
| operation uniqueness | retained journal rejects duplicate `operation_id` | authority receipt index in the CAS envelope | unique `(tenant_id, goal_id, operation_id)` constraint, used as storage fencing rather than a domain decision |
| cursor | document-local monotonically increasing opaque string | embedded journal cursor, still subject to capacity qualification | per-goal sequence allocated in the same transaction; never a global ordering claim |
| lineage | durable directory `store-identity` | workbench plus never-reused `workspace_incarnation_id` | service-managed database incarnation bound to the provider deployment |
| trust boundary | trusted embedded LoopX process | LoopX-authority-owned NoKV credentials | authenticated tenant-scoped LoopX service role; Agents never receive table credentials |

For PostgreSQL, `commit_authority` is one transaction, not a series of
independent repository calls: lock or conditionally update the scoped head,
verify the expected provider revision, allocate the next scoped cursor, insert
the committed transaction/events/receipts, update the projection head, and
commit. A connection failure before any write is a proved `failed` result; a
connection loss after commit may have started is `ambiguous` and is reconciled
by the unique operation row and receipt read. `READ COMMITTED` plus an explicit
per-head row lock/conditional update can implement this contract; choosing
`SERIALIZABLE` is an adapter decision, not a substitute for LoopX CAS,
operation identity, or lease fencing. Authentication, tenant routing, audit,
pool exhaustion, cancellation, timeout, and failover must all fail closed at
the service boundary.

For NoKV, the existing `load` / `compare_and_put` reference already maps
missing, generation conflict, ambiguous publication, and workbench-incarnation
lineage. It does **not** yet prove the widened event/receipt/cursor service
contract. Until capacity, retention, restart/restore, availability, and HA are
qualified, `read_receipt` and `scan_committed` can only be implemented by
reading the retained journal inside the one CAS envelope; they must not be
silently mapped to an eventually consistent listing API.

The file provider is therefore conformance evidence, not a production-scale
event store. Its retained journal intentionally makes atomicity and historical
receipt replay easy to inspect, at the cost of whole-document growth. That
tradeoff is acceptable for Stage 1 and is explicitly not inherited as the
PostgreSQL physical schema or accepted as NoKV capacity proof.

### 6.3 The three version numbers are not the same thing

| Domain | Owner | Meaning | Consumer |
| --- | --- | --- | --- |
| `provider_generation` | Provider | Opaque token for conditional replacement of stored bytes | Authority/provider seam only |
| `authority_revision` | LoopX authority | Per-goal logical commit sequence after an accepted command | Audit, receipts, and read models; not a shared precondition for every domain command |
| `lease_epoch` | LoopX authority | Per-todo fencing generation for ownership; advances on a new lease generation, not ordinary renewal | Executors and accepted writeback |

A backend may often advance its generation once per accepted command, but
numerical equality is never a contract. Migration, repair, or provider metadata
may change provider generation without granting a new LoopX authority revision.

A provider's document generation or database revision implements
`provider_generation` only. The LoopX authority remains responsible for the
other two domains and for the receipt stored inside the transaction.

### 6.4 Expiry adjudication and the binding fence

Wall-clock facts have exactly one judge: the authority that is applying a
command, reading the loaded head's `expires_at` against its own clock. A
caller's opinion of time is request motivation, never evidence; the core
stays clockless and receives only the adjudicated active/expired verdict.
Renewal, release, and completion require the lease to be active at
adjudication time. Reclaim additionally requires the lease to have been
expired for at least a configurable grace window whose lower bound is the
maximum expected clock skew between endpoints in the deployment; the grace
in force is the executor's declared parameter, and the configuration
boundary is itself fail-closed: only a finite, non-negative number is
accepted, because a NaN, negative, or boolean grace would advance a
takeover instead of delaying it. Correctness never
depends on the grace value - even a holder that believes itself alive is
fenced by the epoch the reclaim minted, so skew can only delay a takeover,
never corrupt one. Because renewal advances `todo_revision` (Section 5.2), the validity
interval is also revision-covered, closing the rebase path a reclaim could
otherwise ride across a concurrent renewal.

The store-lineage binding fence closes the restore hazard measured in the
Stage 2 status: `provider_generation` alone cannot carry lifecycle
identity. The provider contract gains one read-only verb,
`store_identity() -> str`, returning a stable identity for the store
lineage: the NoKV adapter binds `workbench` plus the never-reused
`workspace_incarnation_id` the service mints per workbench incarnation; the
file provider serializes creation under its cross-process lock and publishes a
strictly formatted identity through complete temp write, fsync, atomic rename,
and parent-directory fsync.
`bootstrap` embeds the identity in the head (`store_binding`), and the
authority refuses every command - typed `store_lineage_mismatch` - whenever
the loaded head's binding differs from the provider's identity, re-checking
after every reload. A restore therefore preserves frozen bytes and lineage
without granting live authority, exactly as the Stage 1 boundary requires;
promoting restored state needs an explicit, reviewed re-bootstrap that
mints a new binding. Known residual: a byte-for-byte copy of a file-backed
store directory copies the identity file with it, so the file provider's
fence detects relocation only when the identity file is excluded from the
copy; the NoKV incarnation identity has no such gap and is the
authoritative fence for shared deployments.

Adding the binding is a schema change, not a reinterpretation: heads carry
`loopx_coordination_head_v1`, and a legacy `loopx_coordination_head_v0`
document (the Stage 2 shape without `store_binding`) is classified as
`head_schema_migration_required` - a typed failure, never an unclassified
validation crash, and never silently readable as current. The only upgrade
path is the explicit `migrate_head_v0_to_v1` operation: an operator attests
the reviewed store's own identity (`provider.store_identity()` on the store
they have verified is the authoritative lineage) and writes the migrated
head back through the same CAS. The binding is deliberately never inferred
from whichever provider a head happens to be loaded through - an automatic
binding would let any restored copy of a v0 store authorize itself, which
is the exact capture this fence exists to prevent. Migration recognizes only
the Stage 2 writer's actual output subset: `open` todo records and
`claim_work` receipts with the exact v0 field sets. The old v0 validator did
not close the todo status vocabulary; unknown status values and Stage 3-only
fields under a v0 token are therefore treated as corruption requiring manual
repair, not grandfathered into new authority.

## 7. Keep Every Receipt First; Compact Later

v0 uses `retain_all_v0`: no committed receipt-index entry may be garbage
collected, expired, or omitted from a snapshot. This is intentionally a
correctness-first proof boundary, not a production-scale retention claim.

If a receipt-index entry is present but its original receipt is missing or
invalid, the authority fails closed as a provider-protocol violation. It has no
fallback to provider publication history and must not reconstruct a receipt
from the current head.

A bounded retention window, receipt segmentation, or external receipt ledger
requires a later RFC that preserves atomic proof and defines behavior outside
the window. Until then, compaction may rewrite bytes but must carry the complete
receipt index forward.

### 7.1 Proposed amendment: sealed receipt segments (owner decision, Q5)

`retain_all_v0` in one CAS document cannot reach a multi-agent canary: the
Stage 2 measurements showed ~750 bytes per receipt, a ~7x claim-latency
growth across 120 receipts, and quadratic cumulative republish. Stage 3
adds four more receipt-writing verbs, so the growth rate only worsens. The
sentence above deliberately requires a reviewed amendment before receipts
may leave the single document; this subsection is that amendment, proposed
with evidence and NOT yet in force.

Design (measured against two alternatives on a live NoKV stack, 150
transitions each): the head keeps a bounded receipt window; when the window
fills, the authority seals it into an immutable receipt-segment object at a
sibling path (`goals/<goal>/receipts/segment-<seq>.json`), published
create-only through the artifact path BEFORE the CAS that drops the sealed
receipts from the head and appends `{path, sha256, count}` to a segment
chain - the artifact-first order of Section 1.1, so the head never
references bytes that were not durably published. Every committed receipt
stays verifiable: replay looks in the window first, then dereferences the
chain; a missing or digest-mismatched segment is a provider-protocol
violation and fails closed, the segment-level form of the Section 7
fail-closed rule. NoKV's publication semantics make sealing exactly-once
for free: `operation_id` and `artifact_revision_id` are root-scoped-unique
with immutable-input replay, so a seal retried after a crash between the
segment publish and the head CAS replays idempotently (verified live) -
but both ids MUST derive from workbench, path, and content, or a second
lineage reusing the same derivation is rejected as id reuse.

Measured verdict (debug stack, relative numbers): sealed segments keep the
head 19x smaller (5.8 KiB vs 112 KiB at 150 receipts), publish 7.5x fewer
cumulative bytes, and hold per-transition latency flat where retain_all
grows, for +3% provider round trips; recovering an already-sealed receipt
costs ~160 ms versus ~43 ms in-head. The receipt-per-object alternative is
dominated: it pays an extra object publish on every transition (about 2x
latency throughout) and still grows the head linearly through its pointer
index. Window size is a deployment parameter; 16 was measured.

Until the owner accepts this amendment (Section 12 Q5), `retain_all_v0`
remains in force and the Stage 3 verbs ship on it unchanged.

### 7.2 Ten-day goals: local storage qualification target (proposal)

A supported goal must remain executable for **at least ten elapsed days**, across
process restarts, sleeping hosts, delayed receipts, and binary upgrades, without
manual history truncation or a storage-driven goal reset. Ten days is a minimum
qualification horizon, not a receipt expiry or a maximum goal lifetime. This
amendment sets the product target; no current provider is declared qualified by
this text, and `retain_all_v0` remains the shipped rule until a reviewed cutover.

#### Workload and cost model

Elapsed time alone is not a capacity specification. The initial local qualification
matrix uses the following **proposed workloads**, not measured usage or promises:

| Profile | Workload per goal | Qualification horizon |
| --- | --- | --- |
| Minimum continuity | Three continuously leased Todos, TTL 600 s, renewal every 300 s: 864 renewals/day before other writes | 10 days; 8,640 renewals plus lifecycle/receipt traffic |
| Local design target | Eight registered agents; four concurrent writers on distinct Todos plus same-Todo races; up to 1,000 active Todo/lease/gate records; 10,000 committed transactions/day including renewals, receipts and capture | 10 days; 100,000 commits; 30-day / 300,000-commit headroom run |
| Payload and backlog axes | Live projection 8 KiB, 64 KiB and 1 MiB; new event/receipt payload up to 4 KiB per commit; 5:1 reads/writes; 10 commits/s bursts for 60 s; 24 h of projection-consumer lag | Exercise each axis separately and the combined local target; count retries/conflicts separately from commits |

Each fixture declares active versus archived records, serialized sizes, command
mix, index size, and capture fan-out; a model Turn can cause several commits.
Keep live-state size fixed when isolating history growth, then grow live state
separately. No goal-wide unbounded list of completed Todos or receipts may be
hidden inside the supposedly fixed live projection.

For the current `FileAuthorityStore`, a fixed projection of P bytes retained in
each of N transactions costs approximately P*N final history bytes and
P*N*(N+1)/2 cumulative document-publication bytes, before head, event, receipt,
and envelope overhead. Normal reads also decode and validate the full chain.
With P=15 KiB, the renewal-only case gives about **534 GiB** of cumulative
publication at day 10 and **4.69 TiB** at day 30. The former 380 MiB estimate was
only N*P at day 30, not the cumulative rewrite of retained projections. These
are analytical payload estimates, not physical SSD writes or measured latency;
growing receipt indexes inside every projection can make the model worse.

#### Preferred local direction and compatibility boundary

Qualify an **embedded transactional store, with SQLite as the first candidate**,
behind the existing TypeScript `AuthorityStore` owner. A local goal must not
require a PostgreSQL service. The file-v0 provider remains a conformance/import
baseline; no general-purpose ten-day promotion may rely on its full-history
rewrite. [PR #4121](https://github.com/huangruiteng/loopx/pull/4121) supplies an
opt-in SQLite conformance candidate behind that owner; it does not by itself
qualify long-goal durability or change the default. Dependency/package,
Windows/macOS/Linux and supported Node-profile evidence remain explicit gates.
A segmented file log remains the comparison option;
PostgreSQL remains the independent shared-service path.

A database swap alone is insufficient. The complete slice must:

- Atomically publish current state, operation/digest uniqueness, original
  receipts, ordered cursor, and any projection outbox entry. Use indexed
  operation lookup and cursor paging. Preserve the existing ambiguous-commit,
  CAS, lineage, and domain fencing semantics; storage never decides Todo policy.
- Keep live head independent of total history. Use periodic verified checkpoints
  plus committed state deltas or immutable state blocks; retain original receipts
  outside the hot head. Bound both replay tail and index/root metadata. A head
  containing every sealed-segment pointer is still unbounded, and scanning a
  receipt-segment chain for old retries is not an indexed lookup.
- Preserve the logical `scanCommitted` contract: it currently returns a full
  projection for each transaction. Reconstruct the exact version from checkpoint
  and deltas with bounded paging, or version the contract and migrate every
  consumer explicitly. Do not silently remove historical projections. Existing
  events have not been proven sufficient to reconstruct them; canonical state
  deltas need independent equivalence checks.
- Move the authority's growing in-head receipt index through an explicit versioned
  migration, together with provider storage. Preserve request-digest conflict
  detection and byte-equivalent original receipt fields after checkpointing,
  restart, and later unrelated commits. Caching cannot be the correctness path.

This is one local persistence slice with real CLI callers and consumer readback,
not separate leaf-helper ports or a second semantic kernel. It includes the
capture outbox and paginated archived-Todo/history reads needed by status/quota;
other stores and raw artifacts are measured separately, not claimed fixed by it.

#### Proposed acceptance budgets

On a declared local SSD host, report OS/filesystem, CPU/RAM, Node/database versions,
durability settings, sample count, p50/p95/p99, lock wait, RSS, database/WAL/archive
size, and logical bytes written. Include cold CLI startup separately from warm
store service time; never disable fsync/checkpoint safety to pass.

For the 64 KiB live-state axis at 10,000 versus 100,000 commits:

- Warm head load and indexed original-receipt read p95 <= 50 ms; durable commit
  p95 <= 100 ms. History-growth p95 ratio <= 2 with the same live state and load.
- Warm scan of 100 transactions p95 <= 250 ms; cold open plus first authoritative
  read <= 2 s; bounded crash recovery <= 30 s. Full archive integrity audit is
  separate and may be linear, but normal startup may not require it.
- Storage work adds <= 200 ms p95 to a complete CLI mutation against the matched
  10,000-commit baseline. Measure full status/quota latency too; do not hide a
  history scan in a compatibility consumer or amortize process startup away.
- At fixed live state and delta sizes, cumulative logical writes, retained bytes,
  and recovery work must have declared bounds. From 10,000 to 100,000 commits,
  cumulative bytes written must grow <= 15x, not the roughly 100x quadratic
  rewrite; instrument checkpoint, index, WAL and compaction work. Stable-state
  RSS must not scale with the number of historical transactions. Report the
  1 MiB axis and 300,000-commit headroom separately; failures narrow the supported
  profile rather than disappearing into averaged results.

These thresholds are proposed engineering budgets, not current measurements.
Review them against the first matched baseline before activation; do not relax
correctness, silently change the workload, or advertise an unqualified horizon.

#### Retention, recovery and delivery gates

Ten-day operation does not authorize day-11 deletion. Keep operation identity,
digest and original receipts for the goal lifetime under the initial policy,
including at least the day-1 retry after day 10 and day 30. Physical compaction
may remove redundant encodings only when the retained checkpoint/log/index can
reconstruct every promised historical record. Cold archive must remain
addressable and verified; unavailable proof fails closed, never as a fresh
operation. Any later expiry requires a versioned retention contract and explicit
outside-window response; neither a TTL nor a tombstone permits duplicate effects.

Checkpoint/segment publication must be crash-safe with a durable manifest/root
switch. Reclaim old bytes only after durable replacement and every registered
consumer's persisted cursor or explicit full-resync decision; protect lagging
projection outboxes, exports, and backups. Set byte/lag admission budgets and
surface maintenance/backpressure before disk exhaustion. Admission must reserve
recovery space; ENOSPC or an interrupted commit cannot erase proof or count as a
successful write. Total audit storage may grow linearly with useful history;
only the hot path and maintenance batches are bounded.

Sleeping or restarting a host does not preserve an expired lease: resume must
re-read authority and reacquire through normal epoch fencing. Continuity means
the same durable goal resumes safely, not that a process or lease stays alive.

Qualification has two separate exits: accelerated 100,000/300,000-commit tests
prove volume, while an actual >=10-day synthetic-goal soak proves elapsed-time
continuity. Exercise day-1 historical retries, duplicate/different-digest writes,
concurrent CAS, daily reopen, host sleep, lease expiry, 24 h consumer lag, crashes
around append/checkpoint/index publication, disk-full, backup/restore lineage,
and a supported upgrade/rollback. Compare final state, receipts and cursor scan
to an independent reference; no lost acknowledged commit or repeated effect is
acceptable. Use disposable goals, never active user state. Compressed clocks do
not qualify wall-clock endurance; publishing this RFC starts no soak or monitor.

A code PR can land while soak evidence remains pending, with promotion held.
Promotion requires both exits, explicit import/fencing/export rehearsal and
maintainer review. Publish compact reproducible evidence, not raw private logs.

#### SQLite-for-file transition milestones (proposal)

The proposed destination is SQLite as the normal primary store for **qualified
local Goals**, not removal of every file artifact or a replacement for PostgreSQL
shared-service authority. Keep file-v0 as an inspectable reference/import/export
option. Markdown remains a derived display after canonical promotion. Delivery
is gated by evidence below, not by calendar dates or this PR's merge status.

| Milestone | Required evidence / exit | Default and authority boundary |
| --- | --- | --- |
| Candidate conformance | Review #4121's atomic commits, original receipts, cursor/digest integrity, typed provider-open failures, real CLI and OS/runtime tests. | Candidate only. File remains default; no live migration or promotion. |
| Bounded local profile (L) | Meet this section's unchanged workload/budget matrix, including 64 KiB matched 10k/100k runs, 1 MiB and 300k headroom, cold startup, lock wait, RSS and logical write growth. Qualify bounded checkpoints/deltas and receipt lookup while preserving exact historical scans. | No default flip. Keep integrity checks; if their cost grows beyond the profile, fix the design or narrow the explicitly supported profile. |
| Fenced migration and recovery (I/F prerequisites) | On disposable Goals, prove file-to-SQLite import, exact receipt/replay equivalence, consumer cursor/outbox preservation, crash/disk-full recovery and reverse export/rollback. Include the required independent legacy/file/PostgreSQL read-only rehearsal where shared routing or projections change. | Tooling and migration manifest must be reviewed first. Today's empty-goal selector is not an existing-goal migration API. Never test on an active user's Goal. |
| Elapsed qualification and opt-in canary | Complete an actual >=10-day synthetic soak, including the specified restart, sleep, day-1 retry and 24 h consumer-lag cases. Then request separate authorization for a small opt-in operator canary with recorded stop/rollback criteria. | All C/I and selected-provider holds still apply. Evidence from accelerated volume cannot replace elapsed time; a canary does not authorize a general default. |
| New-Goal default decision (F) | Maintainers accept the qualified profile and canary results, operational diagnostics, backup/restore procedure, release instructions and default-disable path. Ship the default change in a separate disclosed release change. | Apply only to newly created eligible local Goals. Existing explicit file selections remain pinned. Unsupported runtimes/filesystems require an explicit supported choice; no silent backend switch on open failure. |
| Existing-Goal migration and file retirement | Migrate opt-in cohorts using the reviewed fenced workflow; reconcile receipts, history, projections and rollback after each cohort. Inventory the last file-primary callers and compatibility windows before removing any path. | Each Goal needs explicit migration authority. Retire file as the ordinary primary only after that evidence; retain reference/import/export support until its own callers and retention duties end. |

**Current evidence position (rechecked 2026-09-15).** #4121 merged as
`bde1632bb6f29aeb9a8b4ac23ead3e98ba2f2f55`, delivering the first candidate
milestone. The bounded retained-storage half of lane L now has a reviewed
candidate: `loopx_sqlite_authority_store_v2` keeps one checkpoint per 64-commit
window plus one exact state delta per commit instead of one full projection copy
per row, proves the live head from the head row, its retained transaction and
the cursor bounds, rebuilds at most one window per historical read, and proves
the complete delta chain through `verifyAuthorityHistory`. Cursors, operation
IDs, commit digests, provider revisions, receipts, events and scan pages are
unchanged, and the shipped version-1 databases migrate through the reviewed
`examples/coordination/sqlite-authority-migration.ts` entry point. Rehearsal
evidence at 1,000 commits/64 KiB now reports 16 checkpoints, a 63-commit replay
budget, one checkpoint history read and 1,048,576 retained projection bytes plus
126,714 delta bytes against 65,536,000 bytes for one copy per commit.

This is still not completion of lane L. File and NoKV continue to retain and
decode their complete journal on every load, so bounded recovery is a property
of the embedded candidate rather than cross-provider parity; the SQLite profile
also still retains receipts and events without pruning. The split
storage-traffic measurements landed with the matched-capacity entrypoint
(#4224 batch 1): the formal 64 KiB 10k/100k profile on the reference runtime
(Node 22.22.3/SQLite 3.51.3, declared local host) measures logical writes at
70,326 vs 70,324 bytes per commit, WAL traffic at 22,611 vs 22,623 bytes per
commit (exact frame counts over pinned-read-mark windows), and an app-observed
lock-wait p95 of 244 ms under a 200 ms held write lock — cumulative growth
ratios of 10.00x and 10.01x against the <=15x budget, with whole-run WAL
totals, pure busy-handler time and physical device writes still unmeasured.
Remaining holds: 1 MiB and 300k headroom, full-domain workload,
large-history recovery, fenced backup/restore, supported upgrade/rollback,
OS/runtime coverage and the >=10-day elapsed soak; runner completion cannot
claim them. See the
[SQLite qualification commands](../../reference/sqlite-authority-store.md#reproduce-validation).
The public minimum remains Node 22.18 for File; SQLite additionally requires
synchronous finalization and the WAL-reset fix, with Node 22.22.3/SQLite 3.51.3
as the reference. Node 24 stays primary and Node 26 stays a non-blocking probe.

**Migration decision points.** Before the first existing-Goal cutover, freeze
one exact source lineage/revision under the authority writer fence, import a
complete authoritative snapshot and retained proof, and independently compare
original receipt fields, operation/digest conflicts and ordered scans. Provider
revision tokens are opaque: any required translation belongs in a versioned
migration manifest, never a reinterpretation of old receipts. Bind the target
incarnation and durably publish the selected-provider change only after
validation and consumer reconciliation. Exactly one provider may accept writes;
keep the source fenced as rollback evidence, not as an active dual writer.

Before the selector switch, abort leaves the source authoritative and discards
only the unpromoted candidate through reviewed tooling. After target commits,
rollback must fence the target and export/reconcile its newer committed proof
before resuming any source writer. Deleting the selector, restoring an old file
snapshot, or enabling fallback is not rollback. Missing proof, wrong incarnation,
failed digest/lineage checks, unexplained parity differences or breached recovery
budgets stop the cohort and hold further default expansion. This proposal creates
no migration command, starts no soak, and grants no live cutover authority.

## 8. Local Mode Stays the Default; Shared Mode Is an Explicit Migration

### Default local mode

- Existing project registry, Markdown active state, run history, optional task
  leases, status, quota, and host behavior remain unchanged.
- Installing a provider does not enable shared authority.
- The current event-store bridge still reports Markdown as source of truth and
  does not allow automatic promotion.

Two deployment shapes share the same semantics:

| Deployment | Authority boundary | Provider |
| --- | --- | --- |
| embedded/local | LoopX authority runs in the trusted local process; existing local writers remain canonical until promotion | file |
| shared service | Agents call an authenticated LoopX authority API; the service owns provider credentials, tenancy, and audit | NoKV or PostgreSQL |

An Agent is an API client in both shapes. Direct provider access never becomes
an alternative shared mode.

### Shared-authority mode

Shared mode is an explicit per-goal choice. A reviewed implementation must:

1. pin the source registry, active state, and privacy boundary;
2. stop or fence local writers during migration;
3. normalize only the scoped coordination fields into an initial aggregate;
4. validate todo/claim/lease/gate parity and an empty receipt index;
5. record the shared-mode declaration and authority endpoint/provider binding;
6. route every P0 write through the online authority;
7. render Markdown, local lease views, rollout rows, and status only as
   projections for the migrated fields.

Bootstrap is a fenced administrative migration before controlled shared writes,
not a P0 agent command: it may create the initial aggregate with the selected
existing todos and an empty receipt index. Its source digest and mode
declaration must be durable so a restart can distinguish bootstrap from an
uninitialized provider.

Before the first shared write, migration may roll back to the untouched local
source. After a shared write, automatic fallback to local writers is forbidden:
it would create two authorities. Recovery must restore the authority or perform
a separately reviewed, fenced export and mode transition.

Provider shadowing and read-only canaries may collect evidence, but neither
changes the source of truth. Promotion is explicit and must follow the existing
fail-closed migration discipline.

## 9. What Happens Offline and What Must Stay Local

When a shared-mode authority is unavailable:

- cached projections may be read only when marked stale;
- no new controlled write is accepted;
- already authorized local computation may continue only under the existing
  lease and effect boundaries;
- there is no automatic local-file write fallback.

This RFC sets no wake latency or heartbeat topology. Delivery may use pull,
push, or an IM daemon under the Agent IM RFC, but a delivered message is never
proof that a coordination command committed.

The shared aggregate and receipts may contain compact public-safe or explicitly
scoped private metadata: stable ids, credential-free repository identity,
exact code revision, digests, gate/dependency refs, claim and lease fields, and
privacy-classified opaque pointers. They must not contain credentials, raw
evidence, raw todo prose, transcripts, raw logs, or local absolute paths.

## 10. How We Accept the First Stage

All checks are machine-verifiable:

1. two actors claiming the same todo from one provider generation yield exactly
   one `applied`; the loser receives a target-specific `conflict` and no lease;
   the winner's todo remains `open`, with ownership expressed by `claimed_by`
   and its lease;
2. two actors claiming two independent todos in the same goal from one provider
   generation, with `write_scopes=[]` and unchanged target-scoped authorization,
   dependency, and gate facts, both receive `applied`; the first CAS loser
   reloads, revalidates its relevant preconditions, and rebases internally,
   while the goal audit sequence, both todo revisions, and both receipts each
   advance only once;
3. immediate replay with the same operation id and digest returns the original
   receipt without changing state;
4. historical A/B/A replay survives reconstruction of the authority handle
   while the same deterministic provider fake retains its aggregate, and
   returns A's original receipt field for field after B advances the head;
5. the same operation id with a different normalized digest is rejected and
   changes neither state nor receipt index;
6. fault injection around the provider CAS never exposes state without its
   receipt or a receipt without its state;
7. ambiguous provider responses reconcile by reloading the receipt index: a
   stored receipt recovers success, same-generation absence fails unproved, and
   absence after a generation advance can only succeed through a new successful
   CAS after revalidation;
8. claim rejects an unknown, stale target/precondition, ineligible,
   dependency-blocked, or gate-blocked todo without creating state or a receipt;
9. sustained unrelated provider contention that exhausts the internal retry
   budget returns typed `failed`, creates no receipt for the operation, and is
   not mislabeled as a domain conflict;
10. retained receipts survive reload fixtures with no receipt GC;
11. provider generation, authority revision, and lease epoch are tested as
   distinct domains;
12. privacy scans find no credential, raw body, transcript, or absolute path;
13. default local mode remains behaviorally unchanged, and shared mode never
    falls back to an unfenced local writer.

The companion provider probes are evidence for a candidate implementation, not
permission to weaken these normative checks. Performance measurements and a
particular deployment topology are deliberately non-normative.

## 11. Staged Delivery

### 11.1 Normative stage plan

The delivery program has one shared foundation and two provider-specific
tracks. Workstream labels are responsibility boundaries, not authority grants:

| Workstream | Primary responsibility |
| --- | --- |
| LoopX core owner | TypeScript-owned semantic transaction boundary; file-provider parity and promotion; migration, local-writer fencing, projection flip, rollback, and the single authority-source decision |
| NoKV provider owner | NoKV adapter; live recovery, capacity, and HA qualification; feedback into the shared store contract without moving semantics into NoKV |
| PostgreSQL provider owner | Generic PostgreSQL provider/service; transaction isolation, authentication, tenancy, audit, and operational deployment contract |
| Joint qualification | Provider conformance matrix, one-way shadow parity, one-Goal/two-Agent TEST ONLY canary, and promotion evidence |

The shared control plane is a composition of independently owned ledgers, not
one large coordination aggregate. Stage 3/4 qualification must preserve these
ownership and proof boundaries:

| Ledger / decision | Authority and stable identity | Failure boundary | Stage 3/4 proof |
| --- | --- | --- | --- |
| Coordination head, Todo/claim, and lease fence | `AuthorityStore`; `(tenant_id, goal_id)`, `operation_id`, authority revision, and lease epoch | provider CAS/transaction plus operation-receipt readback | provider parity for projection, legal claim/lease transitions, fence, receipt, head, and cursor |
| Turn admission and quota | independent Turn/quota ledgers; obligation, admission, debit, and void receipt identities | their own append/idempotency boundary; never absorbed into a coordination commit | end-to-end observation that admitted work references the accepted coordination head and accounts quota once |
| Delivery, inbox, and external effects | independent delivery/inbox/provider ledgers; event cursor, effect identity, and provider receipt | connector/effect ambiguity is reconciled at its owning ledger | end-to-end observation that steering changes a later decision and an effect is not duplicated |
| Settlement and run history | independent settlement journal and run ledger; settlement/phase receipt and run identity | ordered settlement checkpoints and idempotent replay | end-to-end observation of exactly-once settlement across restart, referenced from rather than stored inside coordination state |

Only the first row qualifies an `AuthorityStore` implementation. The remaining
rows qualify control-plane composition through typed references and receipts;
passing them must not be reported as additional state owned or transacted by
the coordination provider.

The sequence is:

1. **Stage 0 - merge the recoverable reference foundation.** Integrate #3669
   with the native TypeScript task-lease acquire boundary, preserve TypeScript as
   the acquire transaction owner, and close file store-identity publication.
   #3806 completes the local lifecycle transaction cutover and makes renew,
   transfer, and release consume one pure TypeScript decision in both the local
   file executor and provider-neutral coordination; Python is a typed adapter,
   not an alternate authority.
2. **Stage 1 - define the provider-neutral transaction boundary.** Express the
   service-grade contract in LoopX-owned TypeScript and make the file provider
   its first conformance backend. Do not recreate a second Python semantic
   authority.
3. **Stage 2A/2B - implement providers in parallel.** The NoKV owner qualifies
   the NoKV adapter and storage envelope; the PostgreSQL owner implements the
   generic service/provider. Both reuse the same LoopX transition and receipt
   semantics.
4. **Stage 2C - qualify and promote the first canonical profile.** First shadow
   the current Markdown/task-lease writers into `FileAuthorityStore` without
   reading it for decisions. Prove parity, crash recovery, migration, and
   one-command rollback; then, in a separately reviewed promotion, make that
   profile the local coordination authority and fence the legacy writers.
   Markdown and task-lease files become projections only after promotion. The
   state machine, complete field manifest, receipts, and acceptance rows remain
   provider-neutral so NoKV and PostgreSQL can qualify through the same route.
   Projection shape or head-digest parity alone is insufficient: promotion
   must prove complete consumer-visible Todo and lease semantics at the exact
   qualified revision.
5. **Stage 3 - one-way remote shadow parity.** The promoted local
   `FileAuthorityStore` remains the only authority while committed observations
   are projected to a NoKV or PostgreSQL candidate. Provider parity compares
   Todo/claim, lease fence, operation
   receipt, projection head, and cursor. Turn admission, quota, settlement,
   inbox, and run history remain independent ledgers: the shadow records only
   typed references needed to verify their end-to-end composition. Do not
   perform bidirectional synchronization or provider-to-file writes.
6. **Stage 4 - TEST ONLY canary.** One Goal and two Agents must show no duplicate
   claim, correct expiry/fencing, restart resume, and fail-closed coordination
   writes during network failure. The same canary separately observes that no
   external effect is duplicated, inbox steering changes a later decision,
   and settlement is idempotent and exactly-once; those are composition proofs
   over their owning ledgers, not `AuthorityStore` conformance claims.
7. **Stage 5 - flip one authority source.** Only after a reviewed promotion,
   make the shared LoopX service the sole writer. Local `.loopx` state becomes
   cache, offline projection, and diagnostic material. Never keep a long-lived
   dual-write or dual-master mode.

### 11.2 Current implementation and evidence ledger (non-normative)

The dated entries below preserve shipped boundaries, experiments, and review
findings. They are evidence for the stage plan, not additional specification.
Later entries supersede earlier status claims only when they identify the
relevant contract, exact implementation boundary, and validation evidence.

| Ledger entry | What it records |
| --- | --- |
| Stage 2C observation foundation | Default-off post-commit capture and its crash window |
| Implementation prerequisite / Stage 1 Part 2 | Provider-neutral decision extraction and remaining Python/TypeScript ownership boundary |
| Stage 2B PostgreSQL candidate | PostgreSQL store/RLS conformance, without runtime promotion |
| Stage 2C runtime shadow | Parity, read-candidate, bootstrap, rollback, cutover kernel, and writer fence |
| Stage 2 slice | Reference aggregate/provider implementation and initial NoKV evidence |
| Stage 1 semantic transaction core (#4280) | Shared strict transaction decode, clone isolation, revision projection, and file/NoKV parity fixture |
| Stage 3 slice | Recoverable lifecycle, retention findings, and live provider limits |
| Stage-ladder evidence | Executable stage claims, environment gates, and pending rows |

#### Stage 1 semantic transaction core (#4280)

The file and NoKV adapters now consume one executable semantic core at
`loopx/control_plane/coordination/authority_store_transactions.ts`. It owns the
exact committed-transaction key set, strict JSON/object-list validation,
canonicalization, explicit structured cloning, and the logical
`transactionForRevision` projection. Provider envelopes, storage generations,
failure mapping, and provider-specific revision salts remain in their owning
adapters. This removes duplicated semantic knowledge without creating another
authority writer or changing the default authority source. SQLite and
PostgreSQL row/envelope migration remain later provider stages.

The public fixture in
`tests/control_plane_ts/authority_store_transactions.test.ts` runs native,
reordered legacy-compatible, unknown-key, malformed-list, malformed-nested,
and non-string-identity records through the shared decoder and both active file
and NoKV read paths. It also proves that scan results are isolated clones and
that provider metadata is absent from the logical revision projection. This is
Stage 1 parity evidence, not provider promotion or a claim that all later
provider profiles are qualified.

#### Stage 2C observation foundation: local post-commit capture

The first half of Stage 2C is an explicit, default-off product path. Preview
and enable it with:

```bash
loopx configure-goal --goal-id GOAL --local-authority-shadow-file
loopx configure-goal --goal-id GOAL --local-authority-shadow-file --execute
```

Todo, handoff-mode, follow-up, and task-lease facades sample the full current
local projection after their primary write returns committed, then ask
`FileAuthorityStore` to retain that snapshot. `observation_trigger` records
why sampling began; it is not the primary transaction identity. A concurrent
primary commit may therefore appear in the sampled snapshot. A `captured` or
`replayed` result proves only the candidate-side observation commit. It does
not compare the source and candidate and carries `parity_verdict=not_evaluated`.

Candidate bytes live under
`authority-shadow/file/` outside the legacy per-Goal runtime tree, so state
migration never copies a store identity or revision; an executed migration
seeds a new target lineage from the migrated local state. Candidate failure is
reported as an observation result but never reverses the completed local write.

Disable the observer in one command with
`loopx configure-goal --goal-id GOAL --clear-local-authority-shadow --execute`.
This is rollback of observation only: the local Markdown and task-lease files
remain canonical throughout. The slice does not read the candidate for a
decision, fence a legacy writer, qualify a remote provider, or complete the
second Stage 2C promotion. A process crash after the local commit but before
the observer call may miss that individual observation; a later committed
write or migration seed refreshes the full current projection, but no durable
shadow outbox or transaction-correlated receipt is claimed here. This plumbing
is not parity evidence and cannot by itself support Stage 2C promotion.

#### Implementation prerequisite: put local file mode behind the same coordination contract

Before wiring a live NoKV or another remote provider, the runtime should first
extract the domain decisions in the current todo/lease write paths into a
provider-neutral coordination core and qualify a file-backed provider against
the same commands, preconditions, receipts, and typed outcomes. That refactor
must begin as a shadow of the current Markdown active state and task-lease
files, proving read/write parity, idempotency, CAS conflicts, crash recovery,
and one-command rollback. Only a separately reviewed promotion may make the
file aggregate locally canonical and turn Markdown/lease files into
projections. NoKV then reuses the same authority and contract while replacing
only the `load` / `compare_and_put` provider. This prerequisite does not create
a generic storage abstraction over registry, run history, quota, scheduler, or
evidence; those ledgers retain the owners defined in Section 3.

#### Stage 1 Part 2 boundary

The provider-neutral pure authority core is merged on `main` through #3410.
This slice is a behavior-preserving extraction of a pure decision core for the
todo lifecycle, task-lease lifecycle, and `handoff_mode` rules that the current
writers already enforce. Markdown goal state and task-lease files remain
canonical. The extraction does not synthesize todo, authorization, dependency,
or gate revisions for domains that have no current revision publisher. It also
does not replace today's separate claim and lease verbs with the atomic
`claim_work` command described above; that command belongs to the future shared
aggregate.

After the task-lease TypeScript cutovers, acquire is owned by
`task_lease_acquire.ts`, while renew, transfer, and release are owned by the
pure seam in `task_lease_lifecycle_decision.ts`. Python `authority_core`
projects normalized snapshots, invokes those decisions, and reconstructs the
provider-neutral `TransitionPlan`. The local lease-file transaction and the
coordination executor therefore consume the same lease decisions; locking,
source revalidation, file persistence, provider CAS, and receipt construction
remain in their respective execution layers. The initial extraction retained
Todo, terminal-fence and handoff-mode decisions in Python; subsequent cutovers
move them to their typed owners. Handoff quiescence now lives in
`handoff_mode_policy.ts`. Local holder/fence-close locks remain execution effects.

Keep three layers distinct as the provider work proceeds:

1. `DomainDecision` is the pure apply/reject/no-change judgment over an explicit
   coordination snapshot and command.
2. Authority execution and its result own locking, revalidation, committing the
   decision, and eventually the durable semantic receipt.
3. The provider result reports storage observations such as
   `loaded | missing | conflict | unavailable | failed` and an opaque provider
   generation; it is not a domain decision or a semantic receipt.

Stage 1 Part 2 does not claim durable semantic receipts or A/B/A replay. Those
require the Stage 2 aggregate and provider shadow. That aggregate must treat
`handoff_mode` as a real, revision-covered decision input. Its provider
contract must never collapse `missing` into `unavailable` or `failed`.
`provider_generation`, `authority_revision`, and `lease_epoch` remain three
independent version domains. Likewise, a restore may preserve frozen bytes and
lineage without granting current authority: promoting restored state to the
live authority head requires an explicit lineage and binding fence.

#### Stage 2B PostgreSQL candidate status (2026-09-02)

The first PostgreSQL candidate now implements the LoopX-owned TypeScript store
contract instead of introducing a second semantic authority. A store handle is
bound to `(tenant_id, goal_id)` and receives only a service-owned database
pool. Its fixed `loopx_control_plane` schema separates the scoped head,
committed operations, ordered events, and ordered receipts. One SQL transaction
creates or locks the scoped head row, checks the opaque provider revision,
fences `operation_id` with a unique constraint, allocates the per-goal cursor,
inserts the commit/events/receipts, advances the projection head, and commits.
An error before `COMMIT` is rolled back and typed `failed`; an error after the
`COMMIT` attempt starts is typed `ambiguous` and can be reconciled only by
receipt readback. Database-incarnation metadata is installed administratively
and cannot be rebound implicitly.

The database trust-boundary slice now gives each provider operation a
transaction-local `loopx.tenant_id` context and enables plus forces PostgreSQL
row-level security on every tenant-scoped table. Reads use a read-only
transaction and roll it back before returning, so a pooled session cannot
retain a previous tenant context. A missing context sees no scoped rows;
`WITH CHECK` rejects a row for any tenant other than the active context. The
qualified restricted-role profile receives only schema usage, metadata read,
and the minimum scoped table privileges, so it cannot rebind
database-incarnation metadata or install schema policy.

This is defense in depth inside the service, not tenant authentication. The
service still owns the database role and chooses the transaction context after
authenticating a principal and authorizing its tenant. An Agent never receives
that role, and RLS does not make a caller-supplied tenant id trustworthy.

This slice also moves strict JSON validation and commit normalization out of
the file implementation into one TypeScript authority-store codec. File and
PostgreSQL now run the same provider-neutral conformance suite for atomic
projection-plus-receipt commit, CAS contention, historical receipt replay,
operation fencing, ordered cursor scans, isolation of returned values, and
pre-write rejection of malformed JSON.

Promoted `hard_lease` authority also supports one optional ownership
transaction through the existing Todo-claim caller contract. When the caller
supplies a task-lease idempotency key and optional expected version, the
TypeScript owner reads the canonical Todo and its required write scopes,
reuses the typed lease-acquire decision, and commits the lease, claim, and
receipt under one provider CAS. Omitting those fields preserves the existing
claim behavior, and an unpromoted goal rejects the atomic-only options rather
than attempting a legacy write. File, NoKV, and real PostgreSQL conformance
includes competing-owner coverage: exactly one complete claim-plus-lease
tuple wins, the loser receives no receipt, and the winner replays by its exact
operation identity. This is a cohesive promotion of the existing contract,
not a second `claim_work` abstraction.

The PostgreSQL adapter also applies one provider-local resource guard before it
opens a connection: a commit whose canonical envelope exceeds the configured
`max_commit_bytes` is rejected as typed `store_capacity_exhausted`. The default
is 16 MiB and a deployment may lower it. This is an admission ceiling for one
atomic operation, not measured throughput evidence and not a retention or
partitioning design; those promotion holds remain open.

Real PostgreSQL qualification starts here, not at shadow or canary. A
PostgreSQL 16 instance passed the shared conformance matrix, same-head
concurrent CAS, tenant-scoped reuse of the same goal and operation ids,
transaction rollback with no visible head or receipt, receipt recovery after a
committed transaction loses its response, database-incarnation rebind refusal,
and a restricted-role two-tenant RLS matrix. The latter proves that missing
transaction context exposes no scoped row, cross-context writes fail, and the
runtime role cannot mutate administrative metadata. A fake can still exercise
adapter branches, but it cannot prove row locking, unique constraints,
rollback, commit visibility, privileges, or RLS; every later PostgreSQL
provider slice must therefore retain a real-database gate.

The candidate remains coverage-only. No production LoopX entry point constructs
it, local mode remains unchanged, and Agents cannot receive the injected pool.
The database runtime-role and RLS behavior within the service trust boundary is
now implemented and qualified. Service API authentication,
principal-to-tenant authorization, the production runtime caller,
restore-incarnation rotation, pool
exhaustion/cancellation/failover, retention/partitioning/measured capacity,
one-way shadow parity, the TEST ONLY canary, and authority-source promotion
remain explicit holds. The next PostgreSQL slice must qualify the authenticated
service/deployment and failure boundary; it must not treat database RLS or the
single-commit admission ceiling as those missing service and capacity layers.

The file-backed provider contract and executor are Stage 2; their first slice
is merged on `main` through #3529, and the evidence behind it is recorded in
the Stage 2 status subsection below. That slice proves the aggregate and
provider boundary, but it is not the Stage 2C production runtime shadow: it
does not hook the legacy Todo or task-lease writers.

#### Stage 2C post-commit runtime-shadow status (2026-09-03)

The first production-path shadow slice is implemented behind this exact,
default-off goal configuration:

```json
{
  "coordination": {
    "runtime_shadow": {
      "enabled": true,
      "schema_version": "loopx_coordination_runtime_shadow_config_v0",
      "provider": "file_v0"
    }
  }
}
```

Activation requires all three values. An absent, disabled, malformed, or
unsupported configuration preserves the legacy result and returns typed
disabled evidence. When enabled, the runtime obeys the following boundary:

- the legacy Markdown Todo writer or task-lease writer commits first and
  remains canonical; only a successful primary mutation dispatches the
  shadow;
- the Python adapter projects the committed Todo view through the same shared
  canonical read-record contract used by `todo list`, retains every
  consumer-visible identity, text, priority, filtering, continuation, resume,
  scheduling, archival, completion, note, and evidence field, sorts records by
  stable Todo identity, and sends that view plus compact leases through the
  existing TypeScript effect runtime;
- the TypeScript owner writes that projection and its operation receipt in one
  `AuthorityStore` transaction. It checks an existing receipt before writing,
  rejects reuse of an operation id with different normalized content, retries
  provider-revision contention only within a fixed bound, and reconciles an
  ambiguous commit only by reading the exact durable receipt;
- an applied result reads the receipt back and verifies the current provider
  head when it has not already been superseded. Every result states
  `decision_read_from_shadow=false`; a disabled, failed, conflicting, or
  ambiguous shadow result cannot reject, roll back, or rewrite the committed
  primary result.

Cross-runtime tests exercise the real Python -> TypeScript ->
`FileAuthorityStore` path from both Todo and task-lease hooks, including
default-off behavior, stable replay, content-drift rejection, ambiguous-commit
recovery, projection read-back, and shadow-failure isolation. This closes the
first runtime-shadow slice. A follow-up typed inspection seam now compares the
current compact legacy projection with the file head and reports `missing`,
`matched`, or `drifted` plus both content digests. It is default-off,
read-only, and always returns `decision_read_from_shadow=false`; this provides
the reusable baseline/parity observation needed by migration without turning
an observation into authority.

The next migration primitive is now also present behind the same explicit
opt-in. `coordination.runtime_shadow.bootstrap` installs one normalized legacy
projection only when the file shadow is uninitialized. Its first committed
event durably binds the source version, source projection digest, and
`legacy_canonical_shadow` mode declaration; it deliberately carries an empty
receipt payload because no agent operation has run. Exact replay is recovered
from that first transaction, including an ambiguous lost response, while a
different existing lineage fails closed. This is the provider-owned bootstrap
effect needed by a later administrative migration command; it still cannot
promote the shadow or make a coordination decision.

The administrative caller is explicit and preview-first:

```bash
loopx configure-goal --goal-id <goal-id> \
  --coordination-runtime-shadow-file --execute
loopx coordination-shadow inspect --goal-id <goal-id>
loopx coordination-shadow bootstrap --goal-id <goal-id>
loopx coordination-shadow bootstrap --goal-id <goal-id> --execute
loopx coordination-shadow qualify --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim \
  --require-event-kind task_lease_acquire
loopx coordination-shadow promote --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim
loopx coordination-shadow promote --goal-id <goal-id> \
  --minimum-operations 3 \
  --require-event-kind todo_claim --execute
loopx coordination-shadow rollback --goal-id <goal-id> \
  --provider-revision <revision-from-inspect> --execute
```

It derives the compact projection from the current canonical Todo and
task-lease views, reports only counts and digests, and requires `--execute`
before invoking bootstrap or promotion. `promote` is effect-free without
`--execute`; its preview returns the exact qualified revision, projection
digest, writer-fence identity, and rollback identity. Apply holds the shared
maintenance and legacy source locks while it revalidates the source snapshot,
qualifies the exact shadow lineage, engages the durable writer fence, commits
the canonical head, and reads back the promotion receipt. v0 rejects a Goal
whose already-qualified mode is not `hard_lease`; promotion never changes that
mode as a side effect. A successful write is immediately read back through
the typed parity inspection. The command remains unavailable unless the exact
goal-level `file_v0` shadow opt-in is active.

Pre-promotion rollback is revision-fenced and non-destructive. TypeScript moves
the exact active file-shadow lineage into a durable quarantine archive; exact
retries replay that archive receipt, revision drift fails closed, and the
legacy Todo/task-lease source remains canonical throughout. A later bootstrap
may therefore reconstruct a fresh shadow without restoring or trusting the
retired lineage.

The read-only `qualify` action turns one-point inspection into a typed sustained
parity report. Its coverage-based policy requires a caller-selected number of
distinct committed operations and any explicitly named Todo/lease mutation
kinds. TypeScript scans the complete bounded lineage, verifies the bootstrap,
every event/receipt/projection identity, the current legacy/file head digest,
and reports `qualified`, `insufficient_evidence`, or `drifted`. Replays do not
increase the operation count, missing coverage fails the gate, and every result
continues to declare `decision_read_from_shadow=false`.

Structural parity is not consumer semantic parity. A promotable projection
therefore also carries a versioned Todo read-model receipt containing the exact
field contract, record count, and canonical record digest. TypeScript validates
that receipt against deterministic provider records during qualification,
promotion, and every provider-first collection read; a missing schema, stale
digest, truncated field contract, count mismatch, or unstable order fails
closed. Provider-first mutations regenerate the receipt atomically with the
head. Adding a Todo consumer field is a contract change and requires a new
qualification rather than a permissive fallback.

Before any real Goal is promoted, qualification must additionally execute the
existing consumer pipeline on both the legacy source and the provider
round-trip at the same revision. The semantic matrix covers user and agent
roles; open, done, blocked, and deferred status; priority and ordering; claim,
exclusion, bound-agent, and global-gate filtering; resume conditions;
successor/continuation behavior; continuous-monitor cadence, due time,
watch-only and material-generation state; notes, evidence, completion, and
archival; and provider-only reads after the Markdown file is unavailable. Any
difference blocks promotion and leaves legacy authority in place. Synthetic
stubs remain useful unit tests but cannot substitute for this real complex-Goal
qualification. Real Goal content stays local; public evidence contains only
redacted coverage, counts, exact revision identifiers, and digests.

The next read-only seam exercises the provider shape needed by the future
read flip without granting it authority:

```bash
loopx coordination-shadow read-candidate \
  --goal-id <goal-id> \
  --todo-id <todo-id>
```

TypeScript loads the file head, requires its digest to match the complete
current legacy coordination projection, validates unique Todo identities, and
returns the exact compact Todo plus the provider revision and cursor. Missing,
drifted, malformed, or duplicate state fails closed. The result deliberately
keeps `decision_read_from_shadow=false`: it proves that a parity-matched
provider can answer an exact Todo read, but no lifecycle or settlement caller
uses that answer yet. Promotion still requires an atomic provider-first read
flip together with legacy-writer fencing; a fallback to Markdown after that
flip would recreate split authority and is forbidden.

The reviewed operator path now makes the provider-first read flip and legacy
writer fence one TypeScript-owned cutover transaction; it is never called from
a read, delegation, frontend, or Lark path. A real Goal still needs its own
exact qualification, quiescent `hard_lease` transition, preview, explicit
apply, restart readback, and managed-worker acceptance before that Goal may be
called promoted. Remote NoKV/PostgreSQL shadowing therefore remains Stage 3
and cannot use this default-off hook as authority.

Read-only consumers now share an explicit authority-transition projection.
Managed-delegation preflight and its packaged Dashboard view distinguish
`promotion_required`, `unavailable`, and `promoted`, and publish a typed next
action without starting a Turn or executor. The Goal Channel projection used
by Lark exposes the same state while its existing `mode=read_only` and truth
contract keep `projection_is_writable=false` and `write_authority=none`. Its
renderer derives the matching next action for operators; only the reviewed
TypeScript preview may prove readiness. This closes the explanatory path
without turning Chat or Lark into a second promotion owner or duplicating the
same source and authority facts in every status payload.

The next Stage 2C implementation slice adds the TypeScript cutover kernel but
does not yet change the default runtime. One pure reducer now derives the
Todo/lease projection, event, and receipt from the same mutation. An explicit
promotion operation requires a qualified shadow at one exact provider revision
and digest, plus an independently persisted legacy-writer fence bound to that
same revision. The fence has a shared fail-closed write-check hook; promotion
is replayable through its operation receipt, and provider-first reads and
mutations never fall back to Markdown. Until the Python Todo and task-lease
entry points call that hook and select the promoted mode, these surfaces remain
cutover machinery rather than a production authority flip. The follow-up must
wire every legacy writer, make configuration and rollback explicit, and prove
default legacy compatibility before the local promotion can be enabled.

The first production fencing integration now closes the write side of that
follow-up. Every Python Todo mutation checks the TypeScript-owned durable fence
while holding the existing active-state mutation lock, and native TypeScript
task-lease acquire/renew/transfer/release check the same fence while holding the
lease lock. The absent-fence path remains a zero-runtime-call compatibility
path; a present, unreadable, or invalid fence fails closed. The promotion
orchestrator must acquire those same two legacy locks before engaging the fence,
so no legacy write can pass its check and commit after cutover. The lock-owning
`coordination-shadow promote` path now lands that slice. The remaining
acceptance work is per-Goal qualification and product projection, not a second
Python promotion state machine; an interrupted fence without exact canonical
readback fails closed for operator recovery rather than silently falling back.

The Todo collection-read slice routes `loopx todo list` to `FileAuthorityStore`
only after the durable fence exists. It reuses the same filtering, ordering,
resume, and summary pipeline as the legacy path, permits provider-only reads
when Markdown is absent, and carries an authority receipt in the response.
Provider missing, corruption, protocol drift, or Todo read-model semantic drift
all fail closed; no post-cutover Markdown fallback is permitted.

Durable completion continuation read-back
(`durable_completion.py`: `read_persisted_todo_record` /
`project_durable_completion_outcome`) is a provider read point: it re-reads
the persisted lifecycle record (Markdown first, event projection fallback)
after the completion write and before settlement. The persisted record
carries an explicit `completion_continuation`; the seam fails closed when a
done record omits it or when it contradicts the recorded successor or
no-follow-up fields, so a provider that holds completion state must store
that field byte for byte. Once a remote provider becomes canonical, this seam
flips to provider-first without changing the typed outcome contract below.

#### Stage 2 slice status (2026-08-23)

The first Stage 2 slice is merged on `main` through #3529, additively:

- `loopx.control_plane.coordination.head`: the `loopx_coordination_head_v0`
  aggregate codec. Validation is closed-set and fail-closed for every field
  the executor later dereferences: todos, lease records, and each
  receipt-index entry (entry shape, digest form, receipt schema, operation
  identity, todo membership, revisions, epochs, and timezone-aware UTC
  timestamps: a naive timestamp reads differently under each host's
  local timezone, so it fails closed),
  including bool-disguised integers; `handoff_mode` is a recorded head
  field, pinned to `hard_lease` in v0 (a `soft_claim` goal fails bootstrap
  closed instead of having its declared semantics silently inverted).
  Canonical bytes are defined as sorted-key, minimal-separator UTF-8 JSON
  with non-finite numbers rejected; digests and provider byte parity are
  defined against exactly that encoding, so "deterministic serialization"
  is a contract term here, not an implementation accident. The Markdown
  shadow constructor lives in its own bridge module
  (`goal_state_shadow`), keeping the codec's import closure inside the
  repository's strict type gate.
- `loopx.control_plane.coordination.file_provider`: one goal, one document.
  Locking goes through `loopx.file_lock`, the repository's one
  cross-platform lock owner, with its bounded deadline (a lock that cannot
  be acquired in time is a typed `failed` because no write was attempted).
  Durability is a fixed commit sequence: write-all of the canonical bytes
  (short writes are continued, never ignored), file fsync, atomic rename,
  then parent-directory fsync on POSIX; `applied` is returned only after
  the whole sequence converges, and any storage fault inside it reports
  `ambiguous`. A head with no faithful strict-JSON form reports typed
  `failed` before any write reaches disk.
- `loopx.control_plane.coordination.executor`: Section 5 steps 1-10 for
  `claim_work`. Every domain decision is delegated to the Stage 1 core; lease
  acquire reaches its canonical TypeScript decision through the typed Python
  adapter, while the claim decision remains in the Python core. The
  composition is lease acquire followed by the hard-lease-gated claim,
  because a claim-first or legacy-mode composition silently bypasses the
  Appendix B holder gate (the tests pin this). `lease_ttl_seconds` is
  bounded by the local task-lease authority's own ceiling, so the shared
  envelope cannot mint a lease the local contract would refuse and an
  unbounded caller value cannot escape as timestamp-arithmetic overflow. A
  provider `failed` verdict is verified against the reloaded receipt index
  rather than trusted, so a provider that misreports a landed write cannot
  manufacture a claim whose caller was told it failed.

Design selection was comparative: three executor candidates (core-delegating,
inline-rules reference, journal-style receipt log inside the same document)
ran one scenario battery. Only core-delegating candidates follow a flipped
core rule without local edits, and the journal codec saves no meaningful
bytes under `retain_all_v0`; the shipped executor is the core-delegating
candidate.

Live qualification ran against a single-node NoKV dev stack at the 0.11.0
tag through its Python SDK: the eight-scenario invariant matrix in
`examples/nokv-shadow-provider/live_e2e.py` passes identically for the file
provider and the NoKV provider (same-todo race with one winner,
independent-todo progress, exact replay, identity mismatch, stale-revision
conflict, lost-response recovery through the receipt index, receipt
retention, authority-revision advancement), and a SIGKILL of the serving
owner followed by an operator-style reopen preserved the head byte for
byte, replayed the original receipt exactly through a fresh executor, and
resumed CAS across all three version domains. Renew/release/reclaim,
retention policy, HA, and multi-node deployments remain unexercised, as the
Later-qualification list already states.

Measured boundaries Stage 3 must respect rather than rediscover:

- `retain_all_v0` costs roughly 750 bytes per receipt inside the one CAS
  document; on the dev stack, claim latency grew about sevenfold across the
  first 120 receipts, and cumulative republish bytes grow quadratically
  (about 84x the final head size after 120 claims). The Section 12 retention
  decision is load-bearing before any production canary.
- Under 12 concurrent independent-todo claims, the internal rebase budget
  admitted 8 and the rest returned typed failures. The Section 1
  independence promise holds at two endpoints and degrades beyond that; the
  supported concurrency envelope and retry budget belong in the acceptance
  checks once a multi-agent canary is scoped.
- The NoKV document generation restarts after remove/recreate and after
  restore into a new workbench lineage. A conditional replace carrying a
  stale generation observation was observed to succeed on a recreated path,
  and a restored line re-reaches generation numbers whose receipts differ
  from the original line. This confirms the boundary sentence above:
  `provider_generation` alone cannot carry restore or lifecycle identity;
  the lineage/binding fence needs an explicit provider-contract field in the
  next stage.
- `provider_outcome_unproved` is a real terminal state in live runs, observed
  without fault injection. Replay through the receipt index makes
  re-submission idempotent, so a bounded re-attempt at the same generation
  would be safe; whether v0 adopts that liveness amendment is an owner
  decision recorded here, not silently implemented.

#### Stage 3 slice status (2026-08-26)

The recoverable-execution horizon row exists on this branch, additively:
the four verbs of Sections 5.2-5.5 with the stale-fence rule of 5.6, the
expiry adjudication and store-lineage binding fence of 6.4, per-verb
receipt schemas, conditional completion validation in the head codec
(status vocabulary pinned to open|done, previously unvalidated), and the
holder gate that refuses a correct fence in the wrong hands. Every domain
decision remains delegated to the Stage 1 core; the reclaim composition
was selected by a three-way battery against the real core (the naive
acquire-first composition dies on owner_conflicts_with_claim; the chosen
form does the minimal delegated unclaim and then the ordinary claim
composition, so the new lease passes the true holder gate).

Live qualification (single-node NoKV dev stack, 0.11.0 release wheel):
twelve shared scenario rows identical across the file and NoKV providers,
including renew, grace-window reclaim with the superseded owner recorded,
the superseded executor's writes terminally fenced, and completion
creating a claimable successor atomically; one NoKV-specific row proves
the binding fence against a REAL commit/snapshot/restore - the restored
workbench refuses every command with store_lineage_mismatch while the
original keeps serving. A SIGKILL of the serving owner mid-lifecycle
recovered in ~61 s (60 s session lease drain): the head byte-identical,
the renewal receipt replayed exactly through a fresh executor, and the
post-crash reclaim/fence/completion chain completed on the reopened
store. Clock-boundary tests pin the grace window edge for edge.

Measured gates, updated:

- Concurrency envelope (grown from the Stage 2 numbers): K independent
  claims all succeed through K=8 on this stack (p50 2.8 s / 10.2 s /
  50.7 s at K=2/4/8); K=16 admits 8 and fails the rest typed on the
  8-attempt budget with ~150 s tails. The supported envelope declaration
  for a canary is K<=8 with the measured latency curve.
- Retention: the Section 7.1 sealed-segment amendment is proposed with
  live comparative evidence (19x smaller head, 7.5x less republish, flat
  latency, +3% round trips, ~160 ms sealed-receipt recovery); the slice
  itself ships on retain_all_v0 until the owner decides Q5.
- Two NoKV storage-plane defects surfaced by the independent reruns and
  are reported upstream rather than worked around silently: (a) reopen
  thrash can wedge logical-shard recovery publication at a dead lease's
  epoch, leaving every later takeover attempt rejected with "stale
  lease" until operator intervention; (b) a SIGKILL landing inside the
  metadata write window can corrupt the store manifest
  (FileBlobStore duplicate slot) so no reopen ever succeeds - the
  coordination layer above stays correct in both cases (no false
  authority), but availability depends on these fixes before any
  production canary.

Review-driven hardening (2026-08-27): the reclaim grace configuration
boundary is fail-closed (Section 6.4; a NaN or negative grace could
previously take an active lease), evidence uses a privacy-bound
`artifact://` URI with a closed vocabulary
(Section 5.5; a host path or privacy mismatch could previously persist into
the shared head), and the head schema is versioned as
`loopx_coordination_head_v1` with the explicit v0 migration path
(Section 6.4; a Stage 2 head previously failed unclassified).
That migration now reconstructs the Stage 2 single-command history from its
retain-all receipts: a live claim must match the retained actor, todo revision,
lease id, epoch, expiry, and contiguous authority revision sequence. A partial
or edited v0 head therefore fails as corruption instead of gaining a new store
binding, reusing an epoch, or authorizing an unproved holder.

Delivery boundary, stated explicitly and accepted by the owner on 2026-09-01:
this slice is the RFC's reference implementation with deterministic and
live-example evidence. No LoopX production entry point constructs the executor
yet - the modules are coverage-only in the visible governance ledger. This
acceptance allows the cohesive reference-contract slice to merge after its
correctness, rebase, and review gates pass; it does not promote it to a shipped
capability. A real caller requires the reviewed shared-mode migration,
local-writer fence, authorization publisher, provider binding, projection
flip, rollback, and retention decisions below - not a diagnostic CLI that
creates a second writer. This status section claims proven contracts, not a
shipped production capability.

#### Stage-ladder end-to-end evidence (2026-09-03)

What exists on this branch is one incremental end-to-end "stage ladder" that
exercises every completed stage claim of this RFC through the real
`python -m loopx.cli` and reports a machine-checkable verdict per row:
`loopx/control_plane/testing/authority_e2e_ladder.py` (row registry, runners,
the `loopx_shared_goal_authority_e2e_report_v0` JSON report, exit policy, and
privacy scan), `loopx/control_plane/testing/authority_e2e_fixtures.py` (goal
workspaces, CLI runners, the observation-lock window, candidate read-back), the
read-only TypeScript probe
`tests/control_plane_ts/authority_store_readback_probe.ts`, the pytest
projection `tests/control_plane/test_shared_goal_authority_e2e.py`, and the
entry point `examples/shared-goal-authority-e2e/ladder.py`.

Per stage, this increment implements:

- Stage 0: `s0.file_matrix_twelve_rows` runs the retained live matrix script
  and requires exactly the twelve shared scenario rows to be true on the file
  provider; `s0.nokv_live_matrix` requires the same rows plus
  `restored_lineage_fails_closed` and identical file/NoKV outcomes on a live
  NoKV stack.
- Stage 1: `s1.cli_document_decodes_through_ts_store` writes three
  observations through the product CLI (`todo add`, `task-lease acquire`,
  `todo update`) and reads them back through `FileAuthorityStore` with
  `loadAuthority`, paged `scanCommitted`, and `readReceipt`: cursor `3`, the
  three operation ids in order, and the first receipt found.
- Stage 2A: `s2a.nokv_live_qualification` runs the merged live qualification
  probe (`examples/nokv-authority-store/live-qualification.ts --execute-live`)
  against an existing workbench with a fresh tenant/goal pair and requires
  `ok=true`, the single-node store-conformance scope, every check passed, NoKV
  SDK `0.11.0` / API `1`, and no promotion or availability claim.
- Stage 2B: `s2b.postgresql_conformance_live` runs the PostgreSQL integration
  test file under node's TAP reporter and requires at least nine passes, zero
  failures, and zero skips.
- Stage 2C observation foundation: seven `s2c1.*` rows port the local-shadow CLI
  E2E and migration assertions and pin the single-lineage guarantee. The configure round trip previews, enables,
  reads back, and disables the observer; every retained writer family (handoff-mode,
  todo add/update/complete/supersede/archive-completed,
  task-lease acquire/renew/transfer) captures with
  `primary_writeback_preserved`, `provider_to_local_writes=false`, and
  `candidate_read_for_decision=false`, while an idempotent re-acquire does not
  observe; default-off goals stay isolated; candidate failure preserves the
  primary commit; a POSIX SIGKILL in the crash gap loses only that
  observation; a `--runtime-root` override that differs from
  `common_runtime_root` keeps two todo adds, task-lease acquire, todo update,
  and a leased completion in one store identity while the
  registry root gains neither a candidate lineage nor lease state; and
  `migrate-state` seeds a fresh lineage without legacy bytes.
- Stage 2C parity half: ten `s2c2.*` rows drive one explicitly enabled
  `coordination.runtime_shadow` goal through the public CLI and assert only
  through `authority-shadow status|drain`, `coordination-shadow
  bootstrap|inspect|qualify|read-candidate|rollback` and `migrate-state`,
  reading history through the retained TypeScript store. A Python Todo writer
  and a TypeScript lease writer leave prepared records with committed markers
  that one drain delivers once; bounded drains are cumulative, an idle drain
  changes nothing and a writer replay mints no entry; a SIGKILL around the
  primary replace settles as `abandoned` or `committed_proven_by_readback`,
  and a SIGKILL inside the inline drain is recovered from exact receipts
  without a second delivery; rollback archives pending entries, holds capture
  as `bootstrap_required`, rebootstraps a fresh lineage and replays; three
  cycles of interleaved writers (add, note update with a no-change repeat,
  explicit exclusion set and clear with a no-change repeat, acquire, renew,
  transfer, leased complete and supersede with their fence closes, and a
  second add) keep every bounded qualification matched with
  `sustained_parity_verdict=not_evaluated`; a
  direct primary edit reports `shadow_projection_drift`, a later write holds
  on `source_partition_continuity_unproved`, and only rollback plus rebootstrap
  recovers; an event-only Todo source holds `inspect`, `qualify` and
  `read-candidate` with `event_log_writer_not_bound` while the primary keeps
  committing; `migrate-state` refuses an active capture source with
  `shadow_source_replacement_requires_rebootstrap` until rollback and disabled
  capture, after which the migrated goal bootstraps a fresh lineage that
  drains; and ten transactions measure file-v0 history growth with complete
  projections retained, per-transaction growth accelerating by at most one
  live record, and no capacity horizon claimed.

Live rows are environment-gated (`LOOPX_TEST_POSTGRES_URL`;
`NOKV_COORDINATION_LIVE=1` plus the `NOKV_*` stack variables;
`LOOPX_NOKV_AUTHORITY_LIVE=1` plus the `LOOPX_NOKV_AUTHORITY_*` inputs).
Without a stack they report `unverified`, and the ladder exits non-zero unless
`--allow-unverified` is passed; an unverified row is never counted as green.
A pending row is an unmet obligation as well: selecting one exits non-zero
unless `--allow-pending` is passed, so a report cannot read as green while it
executed nothing.
The report binds the LoopX commit, tree dirtiness, probe digests, and hashed
connection facts, and its privacy scan turns any leak of a temporary root,
home directory, connection URL, or configuration path into
`fail/privacy_violation`; a leak confined to the bindings block is redacted
and still fails the run through `summary.privacy_violations`, which no flag
relaxes.

Delivery boundary: test-only. No production entry point constructs any store;
the ladder adds no product path and reads the candidate only through the
retained TypeScript store. The Stage 2C parity half executes through the eleven
`s2c2.*` rows above; one declaration stays pending.
`s2c2.archive_after_leased_completion_parity` was declared from the capture gap
the parity row exposed and is now an executable deterministic row: the parity
half folds the Todo partition against the same current-graph rule the source
projection applies, so archiving a Todo that holds a released lease record
keeps the candidate head matched instead of reporting
`shadow_projection_drift`.
`s2c2.sustained_parity_soak` is the >=10-day synthetic-goal soak owned by
Section 7.2 and lane L, and bounded qualification keeps reporting
`sustained_parity_verdict=not_evaluated`. This subsection records executable
evidence for the stages above; it does not promote any provider or complete
the Stage 2C promotion.

### 11.3 Remaining qualification and promotion plan

#### P0: contract and deterministic proof

- this ownership matrix and explicit shared-mode boundary;
- deterministic `loopx_command_v0` normalization and request digest;
- the `claim_work` authority transition over explicitly bootstrapped todos;
- one-head state-plus-receipt CAS;
- target/precondition-scoped conflicts and internal CAS rebase after unrelated
  head advances;
- deterministic and NoKV provider candidates behind the same seam;
- A/B/A, identity-mismatch, crash-window, eligibility, privacy, and no-GC
  checks within the stated evidence boundary.

#### Later runtime promotion and reviewed slices

- the LoopX-owned TypeScript transaction/store boundary and file-provider
  conformance described in Section 6.2;
- NoKV qualification and the remaining PostgreSQL service, failure, and
  promotion holds behind that boundary;
- one-way shadow parity, the one-Goal/two-Agent TEST ONLY canary, and a
  single-source authority flip with no long-lived dual-write or dual-master;
- an explicit shared-mode migration and rollback/export operation, local
  writer fencing, provider binding, a production authorization-projection
  publisher, and provider-first status/completion projections;
- transfer and restricted delegated assignment;
- delivery/wake integration through Agent IM;
- independent run-history synchronization and artifact storage;
- distributed quota reservation/accounting;
- provider promotion, authentication, service recovery, HA, and multi-tenancy;
- receipt retention or segmentation beyond `retain_all_v0`.

#### TypeScript-first burden-reduction order

Prefer preparatory TypeScript work when it removes authority that the next
shared-authority stage would otherwise have to migrate under provider pressure.
The order is deliberately narrow:

1. characterize the caller-observable Python behavior and illegal transitions;
2. move one already-shipped ownership transaction at a time into the existing
   TypeScript boundary, starting with atomic claim-plus-lease, followed by
   completion-plus-successor and the remaining lease lifecycle decisions;
3. qualify that exact transaction against file, NoKV, and a real isolated
   PostgreSQL server before changing provider selection;
4. only then advance binding, migration, canary, and production promotion.

This is not permission for a broad framework rewrite. A preparatory refactor
belongs in this sequence only when the next RFC stage consumes it directly, it
removes duplicate decision authority, and caller-visible parity plus rollback
remain reviewable in the same bounded slice.

## 12. What the Owner Still Needs to Decide

1. Should the next runtime slice first close renew/release/reclaim and stale
   fencing, or qualify that lifecycle together with atomic
   complete-with-successor? *Proposed answer (Stage 3 slice): together,
   sequenced internally - the lifecycle verbs land first in the command
   surface and completion reuses their fence machinery unchanged; the
   completed record must satisfy the local durable-completion projection
   seam, which is already the reviewed contract on the local side.*
2. Which compact project-registry authorization fields form the versioned
   authority input, and who may publish a new authorization projection?
3. What is the reviewed rollback/export procedure after the first shared-mode
   write?
4. Which provider and deployment qualify for the first bounded shared-mode
   canary? Provider selection does not change the authority contract.
5. Before production use, what retention and capacity policy replaces or
   operationalizes `retain_all_v0` without losing historical proof?
6. Does the Stage 4 canary require Host lease liveness before admission, or
   is that a Stage 5 promotion hold? Section 11 asks the canary to observe
   that no external effect is duplicated, but a Host whose runtime exceeds the
   lease TTL can keep producing effects after another Agent reclaims the Todo,
   and a settlement rejected afterwards cannot undo them. The review of #3820
   treated this as a Stage 4 precondition; the RFC must say which mechanism
   is required and where: renewing the lease while the Host runs plus
   cancelling the Host when the fence is lost, an effect-owning fenced commit
   inside the recoverable protocol, or a hard Host duration bound below the
   TTL. *Proposed answer: any canary that runs a real Host must renew during
   Host execution and cancel on fence loss, and its acceptance must include
   the long-Host expiry/reclaim negative test; a bounded fake Host is not
   evidence for this row.*
7. How is the canary's authority provider bound to a Goal? A CLI argument
   that names an arbitrary guard command is a test-harness convenience, not a
   product authority selector. *Proposed answer: the binding is a
   goal-level registry record published by the same owner as the
   authorization projection in question 2, naming the provider kind, the
   store identity or lineage, and an explicit TEST ONLY canary marker; a Goal
   without that marker cannot be admitted by a shared-authority guard, and
   the runtime resolves the provider from the record rather than from argv.*
8. Which head shape does promotion commit, and which fields does the aggregate
   own? `main` now defines `loopx_todo_canonical_read_record_v0` as a versioned,
   complete Todo read-record manifest and makes the TypeScript projection
   reject an upsert that omits fields already present on the stored record.
   *Proposed answer: the promoted head stores the complete normalized Todo and
   lease records, flattened enough for a provider-independent readback. Every
   field that has legally entered a canonical record remains present, including
   `updated_at`, routing, capability, decision, dependency, resume, monitor,
   completion, note/evidence, and archival fields. Omission is never a delete;
   mutation must clear a field explicitly under its schema rule. A new field
   must enter the versioned manifest before qualification, otherwise promotion
   fails closed. "Complete" does not mean copying raw Markdown, host-local
   paths, credentials, the whole registry, or data owned by another ledger.*

   *A later field reduction is a governed schema change even when the field is
   stored but has no known runtime reader. Its PR must include a field inventory,
   producer/reader/writer and static-reference research, historical and external
   compatibility findings, migration and rollback, and proof that behavior is
   preserved. The maintainer must approve the named removal explicitly in the
   RFC decision log or PR review; absence of a discovered consumer is not
   approval.*
9. Does v0 promotion cover only `hard_lease` goals? *Proposed answer: yes. A
   `legacy` or `soft_claim` goal first switches mode under the Appendix B
   quiescence rule; promotion never changes the mode implicitly.*
10. After the provider-first read flip, Markdown and lease files are
    projections and the kernel forbids fallback to them. Which data belongs in
    the head, and how are compatibility views rendered? *Proposed answer:
    every field in the canonical Todo/lease manifests, including monitor,
    dependency, resume, decision, completion, text, note, evidence references,
    and any feedback field admitted by the manifest, persists in the head. A
    transaction-bound projection
    outbox only renders Markdown and lease-file compatibility views; it is not
    a second persistence path for omitted authority fields. Readers compare the
    projection watermark with the head revision and replay the outbox when
    behind. Rendering lag may make a view stale, but must not change a decision.*
11. What declares a promoted goal, and who may write that declaration? The
    merged TypeScript-owned file fence is the first local implementation,
    bound to a fence id, source version, and qualified shadow revision; current
    Todo and task-lease writers check it under their mutation locks. *Proposed
    answer: define one provider-neutral authority binding containing provider
    profile, store identity and lineage, schema manifest, `promoted_at`,
    promotion operation id, source digest, and optional `rolled_back_from`.
    Only the promotion orchestrator and rollback operation may change it. The
    file profile realizes the binding with the durable local fence and registry
    discovery copy; NoKV and PostgreSQL must realize the same logical fence,
    CAS/transaction precondition, and readback receipt in their qualified store
    contract. A provider-specific path or table name is not part of the
    authority protocol. `configure-goal` refuses to edit the binding and
    bootstrap refuses a promoted goal. An endpoint that cannot validate the
    active binding fails closed rather than writing a legacy projection.*
12. Which retention, fast-path, and capacity rules gate promotion, and may a
    retained transaction replace its projection with a digest? *Proposed
    answer: the logical contract is common to file, NoKV, and PostgreSQL: the
    latest complete head, ordered cursor, original operation receipt, segment
    or row-chain integrity, deterministic scan, and recovery readback remain
    available under a declared retention version. A digest may replace an old
    transaction's duplicated projection only when the complete canonical head
    and every field required by replay, audit, parity, and migration remain
    reconstructable and the conformance matrix proves equivalence. Physical
    policy is provider-specific: Section 7.2 prefers an embedded transactional
    local store with segmented files as a comparison candidate; NoKV needs a
    qualified document/segment strategy, and PostgreSQL needs append rows with
    reviewed indexing/partitioning. Section 7.2 also owns the minimum ten-day
    qualification gate; the earlier file-v0 proof is insufficient. Each profile declares measured limits
    and fails closed with `store_capacity_exhausted`; one file-size constant is
    not a cross-provider contract. Host renewals remain authority transactions
    because their rate drives every profile's retention envelope.*
13. What happens to the Python reference executor (`executor.py`,
    `file_provider.py`, `head.py`, `goal_state_shadow.py`)? *Proposed answer:
    keep it coverage-only until the kernel's mutation path is routed from the
    CLI, port its scenario batteries to TypeScript tests, then delete it in
    the promotion PR; two local aggregate formats cannot both be canonical.
    Flipping the file profile's `qualification_holds` to `[]` and its `stage`
    literal happens only inside that PR.*
14. `main` now carries two default-off shadow lineages for the same writers:
    the observation capture of #3818 (`coordination.authority_shadow`,
    `authority-shadow/file/<goal>`, projection v0) and the runtime shadow
    (`coordination.runtime_shadow`, `authority-shadow/file-v0`, projection v0
    with `inspect`, `qualify`, `bootstrap`, `rollback`, and `read-candidate`).
    Both re-sample the source after the primary commit, so both share the
    concurrent-writer and commit-to-dispatch loss windows that the review of
    #3818 named. Which lineage is Stage 2C's, and what closes those windows?
    *Proposed answer: the runtime shadow is the lineage, because the parity
    report, bootstrap, quarantine rollback, read shape, and promotion kernel
    already bind to it. The transaction-bound outbox of the parity half
    (prepared entry inside the writer's own lock, committed marker after the
    primary write, bounded drain with `operation_id = entry id`) becomes the
    durable capture that feeds `coordination.runtime_shadow.commit`, and the
    #3818 observation path retires once that capture is wired. The RFC must
    not keep two shadow record formats.*

---

## Appendix A: What This Evidence Proves

The reference provider and probes live in
`examples/nokv-shadow-provider/`,
with a companion
[`evidence document`](./shared-goal-authority-state-provider-v0-evidence.zh-CN.md).
The deterministic candidate in this PR proves the claim/receipt core plus the
Stage 3 reference lifecycle: same-CAS state plus receipt, competing claims,
A/B/A original-receipt replay, renew/release/expired reclaim, stale-fence
writeback rejection, atomic completion/continuation, request-digest mismatch,
clock boundaries, lineage binding, and distinct version domains. The stated
single-node live scope additionally qualifies file/NoKV parity, restart receipt
replay, and a real restore-lineage fence. It does not implement or qualify a
production authorization-projection publisher, receipt-preserving compaction,
default-mode parity, product shared-mode migration/promotion, service recovery,
or HA. Passing
`python3 examples/nokv-shadow-provider/probes.py contract` is therefore not a
claim that the complete P0 acceptance gate above passes. Historical latency or
fault results are informative only; they are not a durability, recovery, HA,
or production qualification claim.

The additional TEST ONLY Stage 2A probe in
`examples/nokv-authority-store/` opens three independent SDK helper processes
and checks fresh create, exact generation update, reconciliation after an
applied CAS response is deliberately lost, a one-winner/two-contender CAS,
winner/loser receipt behavior, and fresh-process receipt/history readback. Its
executable fixes argv to one absolute Python executable, the interpreter
isolation flag `-I`, and this checkout's reviewed helper, so `PYTHONPATH`
cannot substitute the `nokv` module; the helper fails closed unless the SDK
reports NoKV 0.11.0 and Python API 1, and the report repeats those two
admission constants rather than server-observed values. It validates read metadata against the current workbench
incarnation and validates publish responses against the requested workbench,
path, operation, revision, and generation. The AuthorityStore accepts even a
successful publish response only after a fresh read proves the exact persisted
transaction under the current workbench incarnation. This closes false success
at the LoopX boundary, but NoKV's current Python API does not atomically bind an
expected workspace incarnation into `publish_bytes`; preventing a write after a
concurrent remove/recreate remains an explicit provider-contract hold.
Only a successful live JSON report is evidence for that single-node Stage 2A
store-conformance run; deterministic tests are sequence tests only. This
LoopX-only candidate changes neither NoKV source nor its workbench/artifact data
model, and it does not prove runtime shadow parity, a multi-Agent canary,
authority-source promotion, HA, restart recovery, capacity, or production
routing.

## Appendix B: Handoff-Mode Decision Record (2026-08-10)

This appendix writes down a direction already agreed during the PR #2787
review, as part of the implementation prerequisite. It changes documentation
only; no runtime behavior changes.

### Why

Live CLI verification reproduced a fact: the two claim records can disagree.
One agent acquires a hard lease on a todo; another agent can still soft-claim
the same todo afterwards, and both succeed. What happens after the divergence
matters more:

- The later soft claim invalidates the earlier active lease outright: the
  holder's renew and re-acquire are rejected;
- The completion fence disarms itself in exactly this conflicted state: the
  soft claimant completes without any key;
- The lease holder cannot complete at all: authorization checks the soft
  claim first, so the lease credentials are never even evaluated.

In short, once the two records diverge today, the soft claimant wins
everywhere; the lease does not carry execution authority on its own. The
sentence in section 4 ("the lease/fence carries execution authority")
describes the target contract; the body text has been corrected in this
revision.

### Decision

Do not synchronize the two records against each other. Instead, each goal
declares a handoff mode (`handoff_mode`) that separates the two ways of
taking work. The field lives in the goal state file's front matter for now,
traveling with the file across endpoints; once shared mode ships, the shared
authority owns it. Three values:

- absent or `legacy`: exactly today's behavior, both paths open. The
  divergence hole remains in this mode; that is a deliberate default, not an
  oversight.
- `soft_claim`: this goal uses soft claims only. acquire/renew/transfer are
  rejected; release and inspect remain available to clean up and view
  leftover leases.
- `hard_lease`: changing the claim on an existing todo requires holding its
  active lease; every transition that retires an existing todo as done
  (`complete` and `supersede`) requires the key; the self-disarm-on-conflict
  behavior becomes a loud error. Assigning a claimant at todo creation stays
  allowed: a fresh todo cannot have a lease yet. One harness-owned case keeps
  the keyed invariant without making the presenter hold the lease across a
  human wait: completing a user-role `user_gate` todo without explicit lease
  credentials lets the completion fence mint the key itself under the same
  per-goal lease lock, verify it, and release it on commit; an existing
  time-active lease is never displaced.

Companion rules:

1. Default is `legacy`; existing goals see zero behavior change. The hole
   stays open there, and the implementation PR must say so plainly.
2. Field placement as above; the registry and lease files are per-host, only
   the goal state file travels across endpoints.
3. Cross-endpoint window: the mode propagates by file sync, so two endpoints
   can briefly see different values. Today this can only converge, not be
   eliminated; state it honestly. Shared mode closes it.
4. `hard_lease` keeps one door: the existing delegated authority
   (todo_lifecycle_authority, with a stated reason) may change claims
   without holding the lease, with an explicit, auditable override marker in
   the result. No new bypass switches.
5. Switching modes requires quiescence: refuse while the goal still has open
   claims or active leases, and list what blocks it. No forced switch in v0.

### Status (2026-08-18)

The gate shipped as the `handoff_mode` front-matter field with the
`legacy | soft_claim | hard_lease` semantics above, the quiescence-only
`loopx handoff-mode show|set` switch, and the delegated-authority door with
its `handoff_gate_overridden` marker; the user-gate key minting above landed
as a follow-up. `legacy` remains the default and keeps the divergence hole by
design. Two side doors found after the gate landed are closed alongside this
revision: `supersede` crosses the same lease fence as `complete`, and a forced
state rebuild (`bootstrap --force`) carries the declared mode instead of
resetting it to `legacy`. The Stage 2 provider contract and file backend have
since landed, and the first Stage 2C post-commit runtime shadow now exists
behind an explicit default-off configuration. Local canonical promotion has
not started: the runtime still never reads the shadow for decisions, and the
migration, rollback, parity, read-flip, and legacy-writer fencing gates above
remain open.

### Relation to Staged Delivery

Mapped to the five-stage plan from the #2787 review: the characterization
stage first records today's actual behavior (including the three divergence
facts above) as fixtures; the gate itself is an explicitly declared behavior
change, landing as its own PR before the file provider shadow so divergent
data does not pollute the shadow parity baseline; once local canonical
promotion folds claim and lease into one ledger, this class of divergence
becomes structurally impossible and the gate is absorbed. The read-surface
and hygiene fixes (status showing real leases; releasing the verified lease
on completion) touch no decision semantics and were submitted ahead of this
record.

The characterization stage's negative cases extend the original list: a soft
claim overriding an active lease; the completion fence disarming in the
conflicted state; authorization running before the lease fence; claim-change
entry points not yet gated; the window between the lease acquire's projection
read and the state-file lock.


## Appendix C: Stage 2C Promotion Design (proposal, 2026-09-04)

This appendix resolves questions 8 to 14 as a provider-neutral promotion
contract. It distinguishes logical authority semantics from each provider's
physical retention strategy. Nothing below is implemented by this document;
the unresolved gates remain prerequisites for a real promotion.

### Decision summary

1. Stage 2C promotes one canonical coordination head, not a file format.
   File, NoKV, and PostgreSQL implement the same `AuthorityStore` semantics.
2. Canonical Todo and lease state is complete by default. Promotion may not
   reduce it to the fields currently used by a known consumer.
3. Any later field removal requires field-level compatibility research,
   migration and rollback evidence, and explicit maintainer approval.
4. Markdown and lease files become rendered projections after read flip. They
   never supply missing decision state.
5. Retention is logically common and physically provider-specific. A provider
   may segment or normalize history without changing head/readback semantics.

### Shipped on `main` (2026-09-03)

- The default-off runtime shadow: one `AuthorityStore` transaction per
  committed Todo or task-lease mutation, keyed by the mutation's rollout event
  id and `updated_at`, with receipt replay, content-drift rejection,
  ambiguous-commit reconciliation, and read-back.
- `loopx coordination-shadow inspect | qualify | read-candidate | bootstrap |
  rollback`: typed one-point parity (`missing | matched | drifted` with both
  digests), a coverage-based sustained parity report, the provider-first read
  shape (still `decision_read_from_shadow=false`), the bootstrap of an empty
  shadow from the legacy projection, and a revision-fenced quarantine rollback
  of a pre-promotion lineage.
- The cutover kernel: a pure reducer from mutation to projection, event, and
  receipt; `coordination.local_authority.promote` requiring a qualified shadow
  at one exact revision and digest plus an independently persisted legacy
  writer fence bound to that revision; provider-first `mutate` and
  `todo_read` that never fall back to Markdown.
- The fence integration: every Python Todo mutation and every native task-lease
  acquire, renew, transfer, release, verify, and committed releasing
  fence-close checks the durable fence while holding its own lock; an absent
  fence costs no runtime call; a present, unreadable, or invalid fence fails
  closed. A fenced write is rejected before its first side effect as a
  validation-stage `permission_denied` with no receipt; the shared check owns
  only the typed reason and the fence binding facts, and each caller adapter
  renders one provider-neutral remediation from them and carries the check
  result under `write_check`.
- The complete Todo read model: `loopx_todo_canonical_read_record_v0` publishes
  a versioned field manifest, and the TypeScript projection rejects a
  replacement that drops fields already present on a stored record.

### Normative promotion contract

The promotion state machine is independent of provider kind:

1. Resolve a reviewed goal-level authority binding to a qualified
   `AuthorityStore` profile and exact store lineage.
2. Under the legacy Todo and lease locks, verify sustained parity at one source
   revision, projection digest, field-manifest version, and provider cursor.
3. Engage the provider profile's durable writer fence, then commit the complete
   canonical head and promotion receipt with a compare-and-set or transaction
   precondition. A file marker, NoKV document identity, or PostgreSQL row/table
   layout is an implementation detail.
4. Read back the binding, head, receipt, cursor, manifest, and digest before
   allowing provider-first decisions. Any mismatch fails closed.
5. Render compatibility projections through the transaction-bound outbox. A
   stale projection is repaired from the head; it is never consulted to fill a
   missing canonical field.

The canonical head preserves every field legally present in the normalized
Todo and lease records. The field manifest is part of qualification and parity.
An upsert carries a complete replacement or uses a typed patch whose clear
operations are explicit. Unknown additions fail closed until the manifest is
versioned. A removal proposal must enumerate the field and all producers,
readers, writers, persisted fixtures, static references, historical versions,
and known external consumers; state the migration, downgrade, rollback, and
semantic-equivalence argument; and receive explicit maintainer approval. A
field is not removable merely because code search found no current reader.

This completeness boundary excludes raw Markdown formatting, credentials,
host-local paths, whole registry documents, raw evidence bodies, and state
owned by quota, run-history, settlement, inbox, scheduler, or another ledger.
Those remain references governed by their own contracts.

The common retention contract keeps the latest complete head, ordered cursor,
original operation receipt, integrity chain, deterministic scan, and recovery
readback. Physical profiles may differ:

- **file:** create-only sealed history segments plus a bounded head document;
- **NoKV:** a qualified document/segment layout with lineage-bound conditional
  publication and recovery readback;
- **PostgreSQL:** transactionally appended history/receipt rows plus a current
  head, with reviewed indexes, partitioning, RLS, and tenant context.

All profiles expose the same logical result and field manifest. Each publishes
measured capacity limits and returns typed `store_capacity_exhausted` before a
write that cannot preserve the contract.

### Still missing before promotion

- Provider-first CLI routing and the lock-owning promotion orchestrator (the
  kernel's own next slice): take the Todo and lease legacy locks, require
  `qualify` to be `qualified` at the current revision and digest, engage the
  fence, run `promote`, render the projections, and record the declaration of
  question 11; refuse a rerun whose source digest changed unless the abandoned
  store is explicitly discarded.
- Full transaction-capture qualification (question 14): Todo add, update,
  complete, supersede, and archive plus native lease acquire, renew, transfer,
  release, auto-acquire, and fence-close now emit prepared/committed outbox
  entries around their primary write. The bounded drain commits complete
  versioned records into the existing `coordination.runtime_shadow` file-v0
  lineage; it does not create the former second local-shadow candidate. Before
  promotion, add sustained mixed-writer parity runs, event-only Todo coverage,
  and the selected provider profile's recovery/capacity evidence.
- Completion of the compatibility projection outbox and conformance rows for
  file, NoKV, and PostgreSQL. Provider-first create, claim, narrow update,
  complete, supersede, and role-scoped archive reuse the committed authority
  journal as durable intent and render native active/archive records into
  machine-owned Markdown regions with idempotent replay. Lease-file projection,
  backlog/status readback, the provider-neutral authority binding, and the
  remaining command inventory still need the same contract. Providers do not
  promote together; each profile must pass it before it is eligible.
- Retention, fast path, and measured capacity for the selected first-promotion
  profile; the reference executor's removal and status flips (question 13).
- Post-promotion rollback: the shipped rollback quarantines a pre-promotion
  lineage. After the first authority write (`authority_revision > 0`) the
  return path is question 3's reviewed fenced export: quiescence, an empty
  projection outbox, export of the head into the Markdown coordination fields
  and the final lease records, an `equal` verify, removal of the watermark and
  fence, and retirement of the lineage; never automatic, never during an
  active lease.

### Growth is a promotion prerequisite

The minimum supported horizon is ten elapsed days. The workload, corrected
whole-history cost model, local storage direction and qualification budgets are
owned by [Section 7.2](#72-ten-day-goals-local-storage-qualification-target-proposal).
File-v0 remains a bounded conformance/bootstrap profile; neither successful
bootstrap nor a short microbenchmark proves long-goal capacity. First local
promotion requires a qualified bounded-history hot path and elapsed-time soak,
without waiting for PostgreSQL service readiness.

### Todo domain / projection decision (2026-09-05)

The long-term boundary is semantic, not based on where a field was first
parsed. `TodoDomainRecord` owns identity, role, status, text, task semantics,
and **`archive_state: active | archive`**. Archival is independent of completion:
the handoff gate and succession-tracked completion checks exclude archived
records. `deferred` is a status, not an archive state. A renderer must not invent
or change that decision by moving a heading.

`TodoProjectionMetadata` owns `source_section` and optional `index`. The Markdown
adapter derives them when rendering native records. An imported section name
may be retained as compatibility provenance, but cannot become required input
to provider-origin creation. One caveat is load-bearing: legacy `index` also
breaks priority ties in the consumer pipeline. A migration must preserve that
ordering through an explicit domain ordering policy or qualified compatibility
provenance; deleting it and silently switching to identity order is not parity.
Fresh native collections use deterministic Todo-id order before the existing
priority projection; their display indexes are allocated by the adapter.

The implementation checkpoint introduces the separate
`loopx_todo_domain_read_record_v0` manifest and `todo_domain_record_v0` items.
The existing reducer, file-store mutation path, and collection reader validate
this shape without Markdown fields. Tests cover native insertion, archival,
exact replay, reopen, and rejection of renderer metadata and incomplete
replacement. This proves the storage/read boundary, not an authorized CLI
creation or complete lifecycle transaction.

Compatibility is explicit: `loopx_todo_canonical_read_record_v0` and its
`todo_item_v0` records are unchanged. Existing heads, receipts, field manifests,
and default Markdown capture retain every legal v0 field. No implicit conversion
occurs in reads, mutations, or startup. Mixed native/legacy records under one
manifest fail closed. Downgrading a native head to an older binary fails closed
on its unknown manifest; rollback requires the reviewed export below, not
merely installing the old binary. Historical operation receipts are never
rewritten by a schema upgrade.

Field inventory for this split: `active_state_todo_parser.py` produces section
and index metadata and maps section membership into archival state;
`todo_summary.py`, `handoff_gate.py`, and resume/continuation projections consume
archival state; `todos/projection.py` consumes index for ordering. Existing
runtime-shadow fixtures and the legacy TS insert fixture retain v0 provenance.
The native contract adds an alternative; it removes no persisted v0 field and
makes no claim that unknown external consumers have migrated. A future v0 import
must inventory those consumers and prove render/export/rollback and selection
parity at the same revision before changing a binding or manifest. Questions 8
and 10's completeness rule applies to domain facts and retained compatibility
provenance; it does not require native callers to manufacture Markdown addresses.

### Provider-first terminal lifecycle checkpoint (2026-09-07)

Promoted `complete`, `supersede`, and role-scoped `archive` now use one native
TypeScript transaction across file, NoKV, and PostgreSQL. The authority owner
decides actor/claim/lease admission; derives successor priority, capability and
Agent bindings, exclusions, continuation, and predecessor relations from typed
caller intent; reduces completion policy; commits the Todo/lease/head/outbox
write set with CAS; and persists replay receipts. Python remains an adapter for
registry facts, the caller-approved validation effect, intent/result transport,
and compatibility projection drain; it does not select a different terminal or
successor outcome for a provider. The legacy Markdown and event writers reuse
the same pure TypeScript successor decision before materializing their records.

Validation declarations cross the canonical boundary as a required marker and
SHA-256 digest only. Raw argv stays in a 0600 host-local sidecar and recovery
must prove the digest before executing it. This keeps provider heads portable
and public-safe without turning recovery into a silent validation bypass.
Imported v0 `index` remains the archive-order compatibility fact; native records
fall back to durable completion/update time and Todo identity. Legacy lease
files whose Todo no longer exists in the current canonical collection remain
historical audit material and are excluded from live projection.

Qualification uses one read-only, production-complex snapshot for three arms:
an immutable legacy baseline clone, an isolated file store, and an isolated
real PostgreSQL tenant. The provider heads compare exactly; the legacy result
compares through the declared compatibility projection. Archive comparison
removes provider-retained archived records and their historical leases from the
legacy hot view, and ignores absolute imported indexes only after separately
proving identical per-role relative order. Domain fields, archive selection,
active leases, and non-target records are never normalized; the source snapshot
must remain unchanged. The executable rehearsal is
`examples/control_plane/authority-three-arm-rehearsal.py`. A checked-in,
deterministic, public-safe scale fixture exercises the same distribution and
pressure, including hard-lease fences, across every provider conformance suite. It cannot replace the
read-only three-arm rehearsal because all providers share the new semantic
owner and can therefore agree on the same regression.

Every pull request that claims progress against this RFC follows the
[production-scale fixture stewardship contract](../../development/testing-and-quality.md#production-scale-fixture-stewardship--生产规模-fixture-维护契约).
It declares fixture impact, exercises every affected provider arm, and keeps
the read-only three-arm rehearsal as a separate promotion gate.

Legacy lifecycle field assembly now calls the single TS field planner described
in the [TS retirement checkpoint](typescript-control-plane-migration-v0.md#legacy-field-rule-retirement-checkpoint).
This removes Python decisions without changing the per-goal authority phase:
unpromoted goals still commit through the locked Markdown writer, while promoted
goals retain their existing provider transactions and unsupported-field fences.
The planner neither reads a provider nor grants a lease, CAS receipt, or write
permission. This checkpoint closes one rule owner, not the remaining mutation
inventory or local-store/promotion qualification.

### Cross-RFC semantic and presentation conformance checkpoint (2026-09-12)

The TypeScript migration and this provider RFC now share one explicit Todo
semantic boundary. Python production callers import `todos/todo_semantics.py`
directly; `todos/projection.py` is retained only as an import-compatible facade
for external integrations. This is an ownership cleanup, not a second kernel.
The typed TypeScript `projection_delivery` union also owns the distinction
between mutation intent (`pending`/`not_required`) and provider readback
(`delivered`/`current`); unknown states fail closed before acknowledgement.

Priority intent now follows the same admitted create/update transaction on File,
SQLite and PostgreSQL. Explicit set/clear, omission and conflicting legacy text
are resolved by `todos/priority.ts`; Python reads share the generated grammar.
Markdown remains compatible display, while native records retain matching
priority/title. CLI and reviewed Chat edits preserve CAS and historical retry
identity. Real backend readback and a disposable clone of a long-lived local
Goal qualify this bounded change. See the [caller contract](../../project-agent-todo-contract.md#priority-intent).
This does not change provider defaults or close the remaining promotion gates.

Presentation is canonical at the projection layer, not in the domain record.
`source_section` and `index` are the v0 wire shape's display coordinates, while
native records derive the same display section from role/archive state and use
timestamp plus Todo identity as a deterministic fallback instead of a fake
persistent index. The normalized presentation metadata is therefore one
contract even when the wire shapes differ. The same rule is exercised by the
production-scale fixture and by File, SQLite, and NoKV conformance arms.
Provider revision tokens remain provider-owned and are compared only for the
provider-specific replay rules; they are not normalized into Todo semantics.

This checkpoint changes read/ordering and compatibility-adapter semantics only:
it does not promote a provider, add a writer, alter the transaction decoder
delivered by #4280, or make Markdown a second authority. The shared RFC still
owns durable truth, recovery, cutover, and projection delivery; the TS RFC owns
business-rule ownership and caller deletion.

### Next delivery and parallel provider work

Markdown is a **permanent first-class readable projection**. Retire its database
and business-writer authority, not its human/agent presentation. The
[TS RFC's delivery sequence](typescript-control-plane-migration-v0.md#next-delivery-sequence)
owns business-rule unification and caller deletion; this RFC owns one durable
truth, recovery and cutover. Native CLI conversion and a daemon are not
prerequisites, and PostgreSQL deployment must not hold local adoption hostage.

Delivery history now shares one TS outcome/scale/follow-through read projection
across status and quota, deleting the replaced Python decisions. This advances
the TS RFC independently: it changes no provider, durable history, writer fence
or promotion eligibility. Markdown remains a readable projection; neither it
nor narrative history labels become an additional delivery authority.

```text
CLI / Agent / Dashboard → one TS Todo transaction owner → canonical authority
                                                        ├ structured consumers
                                                        └ Markdown projection
```

There are only two authority phases per goal: before cutover, Markdown feeds
qualified shadow capture; after cutover, the selected canonical provider feeds
one-way projections. Do not add a third TS-Markdown backend, bidirectional
live synchronization, or per-command split authority. Unsupported post-cutover
commands fail closed; they do not fall back to the old writer.

The 2026-09-19 command-retirement checkpoint removes the unconsumed
`coordination.local_authority.mutate` and Todo compatibility-edit execution
wrappers while retaining `prepareCoordinationProjectionCommit` and the shared
reducer used by live domain transactions. It also removes the unrelated public
`todo capture-followups` product command. That command retirement does not
remove, weaken, or rename the runtime shadow-capture mechanism described here.
The standalone `todo suggest` prompt command is also retired; candidate
analysis uses the current agent and existing Todo read/authoring paths, without
adding a discovery wrapper or provider-promotion prerequisite. No stored Todo
or authority history is removed by either public command retirement.

#### Refactoring roadmap overview

The Monitor state owner now lives in TS and is composed with authoring scope,
external-wait validation and field updates by one public update plan. Python
transports the locked compact snapshot instead of sequencing those leaf RPCs;
partial topology edits cannot invalidate retained waits. The legacy writer
still owns admission, the lock and persistence. The typed plan is
not an authority receipt; monitor/successor atomicity, native metadata update,
provider defaults and D1–D3 remain separate, unfinished gates. Permanent
Markdown projection remains part of the target architecture.

Todo authoring scope and terminal successors now share the TS resolved-binding
invariant. Only explicit `global_gate` can widen blocking to all registered
agents; `goal_bound` grants no global-gate semantics. This consolidates T1
admission rules without expanding native update fields, changing provider/profile
defaults, or releasing D1–D3 projection, real-backend, soak or promotion holds.

Shared-goal alignment and amendment admission now consume the same canonical
Todo/lease revision after promotion, including authoritative empty state. Their
old display/lease-file reads remain only before promotion. Proposal source
digests include that revision, without treating it as a Goal intent revision or
an amendment commit receipt. This is a bounded T3 consumer closure; the default
provider, permanent projection, D1–D3 qualification and T1/T2 holds are unchanged.

Standing decisions derive from that complete canonical Todo snapshot before
display indexes or active-only filtering. One TS rule is shared with archive
retention; an archived revocation remains decision history, while ambiguous
contradictory chronology cannot select approval by storage order. This is a
T3 read correction, not a new durable permission ledger or commit receipt.
Providers retain their existing CAS/replay boundaries; legacy source-order
compatibility, permanent Markdown projection and D1–D3 gates remain. Validation
must cover real CLI reads, archive/replay on real providers, and data beyond
display limits, not just a sorted in-memory list.

The full-source/list-filter boundary preserves evaluated resume facts. Bootstrap
and writer-outbox capture share the typed archived-dependency selector, bringing
referenced completion records into canonical state so readers can recompute those
facts independently. New legacy archive moves preserve role; old agent-only class
records allow bounded role reconstruction, never inferred user approval authority.
Duplicate/contradictory identities are rejected, and historical nodes/leases do not
re-enter active lanes. The three-arm rehearsal checks this closure against real
providers; derived readiness is not evidence. General historical import and the
remaining D3 qualification/explicit cutover approval are still separate work.

Quota scope/claim selection and resume planning now share one typed read boundary.
It consumes existing legacy/canonical summaries without a provider-specific rule
fork. User gate scope is distinct from Agent execution ownership, including in
active-next-action projections; see the TS RFC's T3 card for intentional changes
and retired Python selectors. Real FileAuthorityStore CLI tests cover missing and
stale display without writing it back. This is consumer-rule consolidation, not
a transaction/store change, provider qualification or whole-Goal cutover.

Long-chain checkpoint reads now use one typed frontier revision/ACK policy across
legacy and canonical sources (TS RFC T3). The index is built before display limits;
excluded work cannot spuriously rearm another Agent, and an incomplete or ambiguous
checkpoint cannot acknowledge the chain. Python keeps the persisted v0 codec, not
a second revision/threshold policy. This is a consumer change: it adds no provider,
commit receipt, promotion route or Markdown writer. Existing CAS/replay, permanent
projection and D1–D3 qualification remain unchanged.

The original direction remains; execution cards expand these stages rather than cancel them:

1. **Close TS transactions and consumers.** Follow [T0–T3](typescript-control-plane-migration-v0.md#execution-cards-after-the-current-stack) to consolidate rules and delete duplicate decisions.
2. **Permanent projection closure.** D1 below retains Markdown as a long-lived one-way display, never a second business authority.
3. **One qualified local profile and fenced cutover.** D2/D3 require the real backend, capacity, soak and explicit promotion approval.
4. **Retirement with named callers.** T4 removes obsolete business writers after their callers exit; permanent rendering and required import/export remain.

#### Durability execution cards

The task graph's T3 topology consumer now shares the inventory/horizon relation
catalog and consumes one supplied status snapshot. Its missing/truncated metrics
describe read completeness, not canonical validity or promotion qualification.
File/SQLite reader replay with a missing Markdown display must remain read-only;
the graph never repairs display or changes authority. This retires duplicate
Python relationship/traversal knowledge without changing the D1–D3 gates below.

The T3 lease-inspection reader now binds Todo, lease and handoff mode to one
provider revision and never reads obsolete local lease files after promotion.
Its eligibility policy is shared with current acquire/lifecycle rules, including
claim divergence and exclusion; a read result is not a lease grant or a commit
receipt. An empty canonical lease set stays empty. This read closure and removal
of duplicate eligibility rules do not qualify a provider, alter CAS/replay or
relax D1–D3; permanent Markdown display and the remaining roadmap stay intact.
The ownership-edit slice now uses the same typed authoring and lifecycle boundary
after promotion as the existing update transaction. It preserves claim/exclusion
fences and rejects leased ownership rewrites; legacy Markdown writing remains a
compatibility path before promotion. Reviewed Chat Todo/Monitor nonterminal edits
now carry validated lifecycle grants/reasons through that owner instead of
falling into the fenced legacy writer. Preview binds the provider revision and
registry witness; apply uses CAS or recovers the original immutable operation.
Current-head display recovery and reloadable Dashboard retry close this L2/L5
journey. Exclusion/binding and lease restrictions remain independent of a grant.
This removes a duplicate decision route but does not qualify a provider, change
promotion defaults, or relax D1–D3. PostgreSQL exercises the same transaction
through its isolated service/store factory; this is not Python production-route
qualification or a default-provider switch.

Capability-gap consumers now share the TS requirement/resolution owner across
legacy and canonical inputs, including quota's Monitor capability partition.
The old Python missing-set and owner/repair decision builders are removed;
source adaptation and read-only candidate ordering remain. This is T3 read-policy
consolidation with disclosed resolution-priority/empty-source/identity corrections,
not capability enablement, a durable permission receipt or D1–D3 qualification.
The existing permanent Markdown projection and cutover holds remain unchanged.

The T3 decision-dependency read policy now shares one TS owner for scope coverage,
exact links and consistency diagnostics. Explicit gate recipients are independent
of claim attribution; conflicting exact targets request repair rather than grant
approval. This removes duplicate consumer knowledge, not provider transactions.
It does not qualify D1/D2, alter the default provider, or relax D3 promotion holds.
Markdown remains the permanent one-way display; the remaining execution cards
below are unchanged.

Scoped fallback now consumes the same typed decision owner for selection and
gate relations, retiring the Python token-overlap matcher and selection loop.
Exact dependency authority and explicit global gates remain; equal legacy action
keys retain blocking compatibility, while different/missing keys cannot prove independence. This
is a T3 consumer closure with disclosed semantics, not a new provider or a D1–D3
qualification. Source adaptation, permanent projection and all promotion holds
remain unchanged; see the TS card and decision-scope contract for the exact rules.

Use the [TS execution cards](typescript-control-plane-migration-v0.md#execution-cards-after-the-current-stack)
for command inventory, update/monitor transactions and consumer deletion. Do not
repeat that plan in a second implementation or treat a merged read-policy PR
as storage readiness. Its T0 checkpoint is the entry condition for these cards.

Lifecycle admission shares the TS owner across legacy writers and native
transactions. Native text/note updates pass a bounded planning intent that
composes the public TS update owner over the same canonical head before CAS;
terminal transitions and planning updates reuse the preauthorized lease fence
in-process. Resume clearing removes its generation fence atomically and retries
retain intent identity. The replaced Python rules, synthetic Markdown editor,
pre-transaction target read and callerless standalone effect-runtime wire are
removed without changing provider defaults or promotion. This remains a bounded
nonterminal planning transaction, not general native metadata support: active-
lease status changes and Monitor planning/effects remain unsupported. Neither an
admission result nor a lease-fence result is a commit receipt. Keep provider
CAS/replay and existing writer lock lifetimes unchanged while collecting this
deletion payoff.
The same transaction now accepts bounded work-requirement declarations through
the shared public TS planner (field list and intentional rejection changes are
in T1). File, NoKV, SQLite and PostgreSQL conformance exercise aliases, explicit
clear, replay after a later edit, invalid-input atomicity and lease rejection.
The production-scale fixture carries requirements across unrelated lifecycle
operations. This does not qualify a new profile, widen an execution grant, or
change D1–D3/promotion holds; Markdown remains an independent permanent projection.
Waiting/resume lane selection is now one TS read-policy owner shared by quota,
vision-wait, agent-scope and replan. The obsolete Python selector module is
deleted; the adapter accepts the same canonical summary after promotion and
legacy summary before it. Real CLI coverage includes capacity changes and
missing promoted display without writing it. This does not close all quota
source paths, authorize monitor writeback, or change provider/promotion holds.

**D1 — qualify permanent projection delivery; may overlap T1/T2.**

Summary/work-lane counts now remain independent of display limits and retain incomplete-source knowledge through Agent scoping; canonical list acceptance holds match status. This closes one L5 read consumer, not permanent projection freshness or D1–D3. See [count semantics](../../reference/todo-work-counts.md).

The Goal Channel ownership observation consumes one complete provider revision before bounding display. It never repairs Markdown or revives old local leases; provider failures and truncation stay visible. This is a T3 read closure with shared TS interpretation, not D1/D2 qualification or D3 cutover. See [coordination observation](../../reference/coordination-observation.md).

The D1 document-ownership slice gives readers, editors and projection one visible-region
and Todo-block boundary. It fixes fenced examples becoming real tasks, narrative after
an archive end marker entering history, and sparse imported ordinals or archived
priority labels blocking readback. Projection reuses durable atomic state publication;
an equal-byte retry syncs file and directory before reporting `current`. Narrative and
canonical records stay intact. This converges the retained Python presentation/legacy
input adapter; it adds no RPC or business state machine and does not change TS authority
transactions, provider defaults, SQLite D2 or D3 promotion requirements.
Objective examples are now isolated by both document producers, and display
readback shares the Goal codec. Canonical Todo reads remain independent of
malformed display; Objective narrative is not added to the Todo store or its
recovery scope. This is a bounded D1 adapter correction, not D1–D3 qualification.
See the [document boundary](../../reference/protocols/active-state-structured-projection-v0.md#markdown-ownership-boundary).
Canonical handoff-mode show/set no longer depend on Markdown frontmatter or local lease files. One TS transaction binds quiescence, mode and durable operation replay to the same revision, including sealed no-op intents. This adds a provider-neutral command boundary, not a provider default or whole-Goal cutover; frontmatter remains outside the Todo-section renderer. See [operation and recovery](../../reference/handoff-mode.md).

T2 now commits a lease-free native Monitor observation and its independent
successors in one canonical CAS/receipt; the route planner alone still grants
no authority. The CLI delivers committed state through the existing journal/
outbox renderer: display failure is pending, not rollback or successor recreation.
Quota consumes the same v0 business receipt for its separate settlement.
Validation covers the real CLI with missing display, operation replay after a
renderer failure, and complex-data concurrency/lost-acknowledgement recovery on
File, NoKV and isolated real PostgreSQL. The L4 extension below closes retained
Monitor lease proof and crash-safe settlement; cross-owner successor claims
remain explicitly unsupported. This slice changes neither the
provider default, writer fence nor promotion approval, and does not replace the
independent legacy three-arm comparison or D2 soak.

- Start from `loopx/control_plane/todos/provider_projection.py`, the existing
  Todo-section renderer and canonical journal/outbox. #4097 already recovers
  missing Todo sections with `recovery_scope=todo_sections_only`; reuse it.
  It cannot recover lost independent Goal narrative.
  The shipped boundary is the [active-state projection contract](../../reference/protocols/active-state-structured-projection-v0.md).
  Direct Markdown edits must not import themselves into authority; validated edit/import tooling is a separate proposal, never a second writer hidden inside rendering.
- Evaluate #4101's receipt-retention candidate against its actual merged head;
  do not replace it with another delivery queue. A committed business result
  and pending projection delivery must remain separately observable.
- Prove crash/retry, concurrent revisions, absent/stale/malformed display,
  receipt lifetime until delivery, non-owned narrative preservation and
  private-field boundaries. Recovery must not rerun the business operation.
- Delete obsolete projection repair/receipt paths only after their callers
  move. Exit with deterministic freshness/readback and an actionable repair
  path; a successful render once is insufficient.

Continuation and closure readback now uses the same TS relation evidence for
completed-work gaps and handoff states before filtering/capping. Capture v1
retains reachable archived successors and original deferred status; derived
summary evaluations never enter provider records. Completion retries also
recover the matching receipt when a peer commits between receipt and head reads,
without accepting state-only replay with fresh validation evidence. Real CLI and complete-graph
provider conformance cover the consumer family. See [operation and semantic
changes](../../reference/todo-continuation-readback.md). This closes a bounded
L5/L7 gap; permanent projection delivery/recovery, D2 and D3 are still open.

**D2 — qualify exactly one local profile; independent of PostgreSQL deployment.**

- Reconcile the SQLite candidate #4121 with Section 7.2 before adding code.
  Keep it opt-in until qualified and approved. If it does not meet the contract,
  record the concrete gap rather than building a second store or changing
  defaults. Keep File/NoKV as existing conformance references.
- On a disposable real backend, prove atomic head/event/receipt commit,
  concurrent access, historical cursor/replay compatibility, bounded live-head/
  index growth, crash/restore recovery and public CLI readback using the shared
  production-scale fixture and accelerated growth cases.
- Separately obtain the existing >=10-day synthetic-goal soak evidence.
  Accelerated volume is not elapsed time; code may merge with promotion held.
  Starting a scheduled soak or touching live Goals requires separate authority.
- Exit with one exact provider/profile revision and a qualification ledger
  naming passed, failed and missing evidence. NoKV and PostgreSQL retain their
  own qualification; a pass on SQLite cannot waive another affected provider.

**D3 — integrate and request a whole-Goal cutover.**

- Requires T1–T3, D1/D2 and qualified capture; production cutover additionally
  requires explicit approval. Transaction-
  bound capture from merged #3870 is the starting point, not a new subsystem.
  Audit sustained mixed-writer and event-only coverage against the final command
  matrix; unresolved rows block promotion.
- Bind one lineage, source revision, field manifest, digest and cursor; drain
  capture, fence old writers and read back canonical state plus projection.
  Rehearse legacy/canonical/selected-provider parity on disposable inputs,
  including empty state, ordering, archived records, leases and receipts.
- Demonstrate fenced export/rollback. Never simply disable a fence and revive
  an older Markdown snapshot after canonical writes. A failed cutover leaves
  one known authority or an explicit blocked state, never two writable stores.
- Promote only the approved profile/cohort. Keep unsupported commands fail-
  closed. After the declared migration window and final legacy caller close,
  hand off exact retired paths to T4; keep renderer and validated import/export.

#### Execution handoff and integration order

**Local-default delivery program (2026-09-14).** The outcome is a new local
Goal whose normal CLI, Turn and operator actions use one TS-owned canonical
transaction path, with Markdown as a permanent projection. An omitted selector
resolving to File is not this outcome: existing Goals still have a legacy
writer until an explicit whole-Goal cutover.

Qualify **one** long-lived local default profile. SQLite is the current D2
candidate; File remains the real reference/explicit profile and migration
rehearsal backend. Do not publish two ambiguous defaults, declare the current
File history layout long-horizon-qualified, or silently fall back from a
selected SQLite store. The final profile decision must cite its D2 evidence.
PostgreSQL shares the TS semantic contracts but has independent service,
tenant, restore and capacity qualification; its deployment must not delay the
local profile's work.

The reconciled baseline includes #4286 (command receipts/archive), #4289
(typed work/ownership intent), #4292 (decision metadata), #4304 (handoff mode),
#4316 (Goal Channel observation), #4317 (provider opening), #4348 (renew),
#4328 (first SQLite D2 batch) and #4334 (PostgreSQL service admission): all are
merged at the 2026-09-20 checkpoint. Their existence does not qualify the full
cards. Monitor observation/reactivation #4732 and linked User completion #4754
are also merged. #4224 retains contributor ownership of SQLite D2. Re-read
actual heads before work.

The identifiers below are **planned PR packages**, not reserved GitHub numbers.
A package may split at a real effect/compatibility boundary; changing languages
or moving a helper is not by itself a package exit.

| Wave / package | Reviewable delivery and TS ownership payoff | Dependencies and exit evidence |
| --- | --- | --- |
| A / L1: Monitor configuration (this slice) | Existing `todo update` config enters the TS planner/CAS/receipt; delete Python's duplicate intent field catalog. Separate authoring from observed hashes, times and generations. | Ordinary CLI/API, clear/omission, active lease proof, no-op/replay, failed display delivery, complete fixture and real providers. This does not complete delegated Chat or leased polling. |
| A / L2: Complete public mutation admission | User completion updates share the TS edit/terminal transaction and reviewed Chat recovery; linked decision consumption/reject/cancel/resume now commit with the source, replacing Python followthrough rules. Continue the actual CLI/Turn/Chat inventory for remaining effect-owned decisions, delegated owner actions and Monitor lifecycle transitions; [caller contract](../../reference/canonical-todo-completion-update.md). | Build on merged T1 owners, not a generic raw patch. Prove permission rejection and exact caller response; remove replaced Python admission and name every remaining unsupported command. |
| A / L3: Canonical lease lifecycle | Standalone acquire/takeover, atomic claim lease admission and maintenance reuse TS facts/decision/materialization and one provider opening fence. Explicit claimed-work transfer now commits source-authorized Todo ownership and the new lease generation together; canonical request types exclude legacy held-fence fields. Acquire success verifies current execution proof; canonical completion can recover missing display. | Full-head scope conflict, archived/ineffective holders, exact create-CAS retry, stale execution, process loss and real CLI/four-arm rehearsal are covered. [Operation and remaining callers](../../reference/canonical-lease-renew.md). Executor-held external-effect fences remain explicit work; D1–D3/default holds remain. |
| B / L4: Leased Monitor poll and settlement | Current execution proof now binds CLI intent, observation/generation/independent-successor CAS and historical business receipt. Quota pending admission is frozen before the business write; recovery preserves that decision after lease retirement. | Existing L3 lease lifecycle, real File/SQLite/PostgreSQL, mixed fixtures, process death between business/quota commits, competing renewal and unchanged polling. [Operation and snapshot rehearsal](../../reference/protocols/quota-monitor-observation-receipt-v0.md). Ordinary polls leave leases unchanged and spend no quota; separate authorities stay separate. The retained grouped-Monitor observation/reactivation caller now uses Todo update v4 and the shared Monitor planner, with unchanged-group display recovery. Canonical reactivation now atomically retires retained execution and reopens the observation cycle, sharing typed admission with polling; a fresh execution still needs explicit acquisition. Executor acquisition for grouped reconciliation, wider L2 admission and D1–D3/default remain open. |
| B / L5: Consumer and display closure | Reconcile #4316, audit Turn/quota/Dashboard/Chat source reads, and finish D1 freshness/recovery through the existing projection outbox. | CLI, Lark/Chat and packaged frontend read back their affected interactions; absent/stale display, empty canonical state, pending projection and data beyond UI limits. Delete post-promotion legacy fallbacks with each consumer. |
| A–C / L6: Local durability qualification | Continue contributor-owned #4224/#4328 on the selected SQLite profile; reuse File/NoKV references and complete 7.2's ledger. | Capacity, real process/crash/restore/upgrade, retained receipts/scans, consumer lag, supported runtimes/OS and the separately authorized >=10-day synthetic soak. Missing measurements remain holds. |
| A–C / L7: Capture continuity | Reconcile the merged #4315 archive/lease-membership repair; qualify its ladder row/mutant and sustained mixed-writer/event-source matrix rather than reimplementing the closed defect. | Real CLI/File capture, history retained, partial drain unqualified, crash/replay and a new lease after archive/rebootstrap. Keep the legacy migration window provable; T4 cannot be used to skip this row. |
| C / L8: Whole-Goal rehearsal and cohort migration | Integrate one exact revision/profile after L2–L7; drain capture, fence old writers, verify canonical readback and projection, then rehearse fenced export/rollback. | D3 evidence packet binds lineage, cursor, source digest, command coverage and profile. Existing Goal migration requires explicit cohort approval; no per-command split authority or stale Markdown revival. |
| D / L9: New-Goal default and bounded retirement | A dedicated default-change PR makes new-Goal creation/onboarding choose the qualified local profile, including settings/readback, installer and packaged clients. Retire old business writers only as their final callers and migration window close. | L8's integrated product/rollback qualification; distinguish new Goal default from existing Goal migration. Publish compatibility/disable guidance, keep explicit provider choice, permanent rendering and validated import/export. T4 can continue after the default ships. |

**Cadence is evidence-based.** First reconcile the active stack, then deliver A
packages as complete operations while L6/L7 progress independently. B integrates
those contracts into complete user flows; C has one reproducible qualification
checkpoint; D changes the default in its own reviewable PR. After the linked
User completion slice, the 2026-09-20 planning estimate is **5–8 further cohesive
PRs**, conditional on the caller audit finding no additional missing effects:

| Remaining work package | Estimated PRs | Exit |
| --- | --- | --- |
| Remaining L2/L3 caller and executor-effect fences | 1–2 | Actual CLI/Turn/Chat command inventory and external-effect boundary closure. |
| L5 / D1 consumer and projection closure | 1 | Full consumer parity, lag/recovery and packaged client readback. |
| L6 / SQLite D2 | 1–2, contributor-owned #4224 | Capacity, crash/restore and separately authorized elapsed-soak evidence on one profile. |
| L7 capture plus L8 integrated migration | 1–2 | Mixed-writer continuity, fenced whole-Goal rehearsal, export/rollback and cohort evidence. |
| L9 default and bounded retirement | 1 | New-Goal onboarding/settings/install choose the qualified profile; remove final obsolete callers. |

The retained-Monitor cycle slice removes a concrete L4 hold, not an entire
remaining package: the **5–8 PR planning range remains conditional**, rather than
subtracting one for a lifecycle fix. Actual remaining executor/caller coverage,
L5 consumers, contributor-owned D2, integrated migration and default onboarding
still determine the count. Python host execution/rendering is retained; this
slice removes duplicate TS admission knowledge without adding a Python twin.

This counts delivery boundaries, not guaranteed merges or all Python deletion.
Scope may split only where a real effect/compatibility boundary warrants it.
Small Python business-rule deletions can ship with each TS owner; rendering,
private command execution and import/export keep their active adapters.

截至 2026-09-20，关联 User 完成链路补齐后，按以上五类完整交付边界估算还需 **5–8 个 PR**。
Monitor #4732 已合并，SQLite D2 仍归 #4224 contributor；其余顺序是调用方/执行围栏、
消费与投影、capture 与整 Goal 演练，最后独立切换默认值。该估算以未发现更多缺失
effect 为前提，不是合并数承诺，也不要求先删完 Python。TS owner 每收敛一块即可
删除对应旧规则；仍有真实调用方的渲染、私有命令执行和导入导出适配器继续保留。

Avoid concurrent edits to the same transaction owner; share
fixture/contracts early and rebase after the owner lands.

There is no defensible calendar completion date before the L2/L3 command
inventory and L6 missing-evidence ledger close. The >=10-day soak is a real
elapsed-time lower bound **after the measured profile is ready**, not ten days
from this plan. It may overlap compatible work after explicit launch approval;
changes to the qualified durability semantics require an impact-based rerun.
Accelerated fixtures cannot replace elapsed time. Native TS CLI/distribution
cleanup, removal of every Python adapter, and PostgreSQL service deployment are
not prerequisites for this local default.

Every package records actual caller/owner deletion, added bridge LOC and its
exit, request/response counts, real-backend results, baseline parity and disclosed
semantic corrections. A green unit suite, a canonical selector, or a new config
field alone cannot advance a package to default readiness. Planned integration,
soak, release, merge and live promotion retain their respective authorization.

### Parallel delivery plan

| Lane | May start | Scope and exit condition | Dependency |
| --- | --- | --- | --- |
| L. Long-goal local persistence | Now, alongside the Todo caller | Section 7.2: embedded-store candidate, bounded live head and receipt index, historical scan compatibility, crash-safe checkpoints, real CLI readback, accelerated capacity and >=10-day soak. | Reuses the TS authority owner; required for local long-goal promotion, independent of P. |
| P. PostgreSQL provider plane | Now, from current `main` | Keep the existing `AuthorityStore` contract; finish schema migration/install ownership, authenticated service and tenant authorization, restore-incarnation rotation, pool/cancellation/failover behavior, and reviewed indexes, partitioning, retention, and measured capacity. Live PostgreSQL conformance remains mandatory. | Does not depend on #3870 and must not stack on its branch. This lane alone creates no runtime caller or promotion claim. |
| C. Canonical transaction capture | Qualify the implementation merged in #3870 | Transaction-bound outbox capture targets the one `coordination.runtime_shadow` lineage and retains complete versioned Todo/lease records. Finish sustained mixed-writer parity, explicit-clear/omission coverage, and event-only Todo recovery evidence. | Can run in parallel with P, but both C and the selected provider profile must finish before parity or promotion integration. |
| I. Binding and qualification integration | After C and the selected profile's qualification | Bind one exact provider lineage, field manifest, source revision, digest, and cursor; qualify explicit v0 import, ordering/archival/consumer parity, and recovery/capacity without consulting legacy state for missing fields. | Long-goal local integration requires L and does not wait for P. PostgreSQL joins only when its own P holds pass. |
| F. Promotion and cleanup | After I and explicit maintainer approval | Complete provider-first CLI routing, the lock-owning promotion orchestrator, compatibility projection outbox, post-promotion fenced export/rollback, then delete duplicate reference aggregates and flip the reviewed stage/hold declarations. | Each profile must pass C, I, and its own provider qualification; long-goal local promotion additionally requires L, and PostgreSQL requires P. |

## Appendix D: Execution ledger

Delivery records for this RFC are files under
[`ledger/shared-goal-authority-state-provider-v0/`](ledger/shared-goal-authority-state-provider-v0/),
one dated entry per change, named and paired per
[the ledger convention](ledger/README.md). An entry states what the change
measured, what it changed, and what it did not establish.

New records go there instead of into the dated sections of Appendix C. Those
sections stay as they are: append-only history that nobody edits, and rewriting
them into files would produce a large mechanical diff that forces rework on the
open branches it is meant to help, while fixing nothing. The reason is measured,
not assumed — see
[`2026-09-19-shared-goal-authority-entries-get-a-ledger.md`](ledger/shared-goal-authority-state-provider-v0/2026-09-19-shared-goal-authority-entries-get-a-ledger.md).

`examples/docs-governance-smoke.py` checks the entry naming, the Chinese mirror
beside each entry, and that this appendix exists for the directory it names.
