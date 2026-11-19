# RFC: Agent Loop Effect Interpreter

| Field | Value |
|---|---|
| Status | Accepted |
| Supersedes / closes | none |
| Date | 2026-08-08 |
| Last normative revision | 2026-10-27 |
| Author | LoopX maintainers |
| Scope | Public control-plane docs, packet contracts, refactor direction, test strategy |

> Language note: the
> [Chinese version](./agent-loop-effect-interpreter-v0.zh-CN.md) and this
> English version are semantic mirrors. A difference between them is a defect.

## Summary

LoopX harness should be explained, designed, and tested as **the effectful
program around an agent loop**, not as a collection of disconnected state
machines.

The canonical shape is:

```text
model -> effect request -> harness interprets effect -> observation -> model
```

The agent loop is the loop. The harness is the effectful program that
interprets each effect request and returns an observation to the next model
step.

The framing builds on the public lecture series by 齐梦星空:
[主线一：Agent Loop 是 effectful program(1)](https://www.xiaohongshu.com/discovery/item/6a01d501000000003700c5de?source=webshare&xhsshare=pc_web&xsec_token=ABqpNuladcxhev099wLKw8M3ilhKBua0BQXNpxnBZEGkc=&xsec_source=pc_share),
[主线一：Tool Calling 是 Kleisli arrow(2)](https://www.xiaohongshu.com/discovery/item/6a02f388000000003502b2d6?source=webshare&xhsshare=pc_web&xsec_token=ABHcIpzpd2RlhAaRr9sZZ-q1OIfRgt7rvG2jn7GUO3tNo=&xsec_source=pc_share)
and
[主线一：Agent Loop 里的小魔法：函数的组合(3)](https://www.xiaohongshu.com/discovery/item/6a057524000000003701f6aa?source=webshare&xhsshare=pc_web&xsec_token=AB43lNCJ5ULmfTrGfeTLWd2-jQ6q8nFMGyNAd-tlXJ1uw=&xsec_source=pc_share).

LoopX's job is the middle two steps: it receives an effect request from an
agent or host, decides whether and how to interpret it, writes back an
observation, and returns control to the next loop iteration.

This RFC establishes the mental model, defines canonical packet semantics,
and gives a milestone plan for aligning documentation, code, and tests with
that model over time.

## Milestone Status

| Milestone | Status |
|---|---|
| M0 RFC and Lecture 0 | Merged (#2905, #2906, #2908) |
| M1 Canonical packet example | Merged (#2907, #2910) |
| M1.5 Composition lens | Merged (#2911) |
| M2 Bounded context alignment | Merged/Complete (#2912-#2915, #2919, #2926, #2933, #2963-#2982) |
| M3 Focused test families | Merged/Complete (#2916-#2918, #2925, #2929, #2984) |
| M4 Architecture documentation | Merged/Complete (#2921, #2923, #2924, #2985) |
| M5 Steady-state review | Merged/Complete (#2922, #2931, #2984, #2985) |
| M6 General effect-program abstraction | Narrow gate complete (#2963-#2987); qualitative transformation requires M7 |
| M7.1 Causal characterization | Merged/Complete (#2994, #2998, #3009, #3022, #3026) |
| M7.2 Typed settlement runtime | Merged/Complete (#3016, #3020, #3023, #3024, #3033-#3036) |
| M7.3 Shared executor decision | Closed with no follow-up: the adapters share algebra, not execution ownership |
| M7.4 Bounded core-path adoption | First non-Turn adoption landed for task lease (#3091, #3095); continue only where a typed effect removes duplicate runtime truth |

## Why This Matters

Today, LoopX has many correct but hard-to-explain pieces:

- todo lifecycle and handoff state;
- quota decision and spend state;
- scheduler and heartbeat state;
- capability gates and user gates;
- vision, monitor, and replan state;
- evidence and run history.

Each piece has a state machine. The difficulty is not that these state
machines exist. It is that a reader cannot immediately see what effect each
state machine interprets, what observation it produces, and how that
observation returns to the next loop.

The agent-loop-as-effectful-program lens fixes this by asking the same
question everywhere:

> Who interprets this effect request, and what observation comes back?

## Core Mental Model

### Agent Loop

The underlying loop is:

```text
model -> effect request -> harness interprets effect -> observation -> model
```

The model proposes the next action. The harness decides whether the action is
allowed, how to execute it, how to handle failure, and how to encode the
result for the next model step.

### Effectful Program

A pure computation is:

```text
A => B
```

An effectful computation is:

```text
A => F[B]
```

`F` captures the external world: persistence, permissions, budgets, timing,
notifications, scheduling, evidence, and failure.

LoopX harness is best understood as that `F` around a long-running agent loop:

```text
GoalState => F[QuotaDecision]
```

## Mapping LoopX Concepts

| Article concept | LoopX equivalent |
|---|---|
| Agent loop | Every automation heartbeat, PR monitor, and sustained refactor turn |
| Effect request | `todo add`, `quota spend`, `refresh-state`, `notify`, `monitor poll`, `bind-agent-thread` |
| Harness interprets effect | `quota should-run` + `interaction_contract` + `capability_gate` + `work_lane_contract` + `scheduler_hint` |
| Observation | Quota packet, run history, evidence log, state writeback |
| Middleware mount points | User gate, capability bridge, scheduler ACK, cooldown, external evidence poll |
| `A => B` | Idealized `GoalState => GoalState` |
| `A => F[B]` | Real `GoalState => F[QuotaDecision]` |

## Canonical Packet Semantics

Every important control-plane packet should be explainable through four
semantic slots:

1. `effect_request`
2. `interpretation`
3. `observation`
4. `next_effect`

Example for `quota should-run`:

```json
{
  "effect_request": "agent proposes next bounded turn",
  "interpretation": {
    "route": "advancement_task",
    "capability_gate": "repair_bridge",
    "scheduler_hint": "active_work"
  },
  "observation": {
    "decision": "run",
    "recommended_action": "...",
    "state_writeback": "validated_progress"
  },
  "next_effect": "execute bounded turn, then refresh-state"
}
```

These slots should not be a second schema. They are a documentation and
naming discipline over existing packet fields. A new packet may add an
`effect_interpretation` envelope only when a real caller needs one canonical
place to read all four slots.

## Composition And Around Semantics

The canonical loop is one effectful step:

```text
GoalState => F[QuotaDecision]
```

The public lecture series distinguishes three layers of composition:

| Composition | Shape | LoopX counterpart |
|---|---|---|
| Function composition | `A => B`, `B => C` | Read model -> projection -> decision |
| Kleisli composition | `A => F[B]`, `B => F[C]` | One bounded turn, host effect, validated writeback |
| Middleware composition | `(A => F[B]) => (A => F[B])` | Around decisions in `capability_gate`, `interaction_contract`, `work_lane_contract`, `scheduler_hint` |

LoopX does not expose a generic Python middleware registry. Its around
semantics are declarative and packet-shaped.

### Bounded Kleisli Runtime Decision

M7 uses Kleisli composition as an execution requirement, not as decorative
terminology. The selected turn-closeout slice should be explainable as a
sequence of typed steps:

```text
A => F[B]
B => F[C]
A => F[C]
```

For this slice, `F` must preserve a receipt-bearing result with explicit
cancellation, permission-denial, budget-rejection, and settlement outcomes.
Composition may be implemented with a closeout-local `bind`, `flat_map`, or
`and_then` seam, but M7.2 must prove the semantics rather than standardize one
method name. Its focused tests must cover:

- identity: adding the typed no-op step does not change receipts or effects;
- associativity: regrouping the same ordered steps does not change their
  receipts, short-circuit point, or externally visible effect sequence;
- ordered short-circuit: a typed failure prevents later effects without
  erasing the failure kind;
- replay: a durable receipt skips an already committed effect; and
- non-commutativity: writeback, spend, and host handoff may not be reordered.

The runtime algebra now has three first-class adapters. The default Codex App path
settles a normal LoopX turn through data-encoded CLI effects across agent and
host boundaries. The isolated turn driver executes the same settlement shape
through in-process callbacks. Task-lease acquisition composes validation and
durable lease write through the same algebra while its bounded context retains
owner eligibility, conflict, lock, and CAS rules. The adapters share plan,
receipt, effect identity, and failure semantics, but they do not share one
executor because their authority boundaries differ. A generic `Kleisli`, middleware stack,
executor registry, or general `Effect` monad remains premature until shared
execution ownership, not just similar packet fields, is proven.

The shared settlement algebra is owned by the core `effect_program` module.
Quota supplies the Codex App/CLI plan builder and compatibility re-exports;
each runtime adapter composes the core algebra instead of inheriting a domain
program or moving its execution authority into a generic base class.

### Handler Is Data, Not a Callable

Runtime middleware receives a `handler` callable and decides whether to call
it, call it once, retry, fallback, or short-circuit. LoopX cannot receive a
model or host callable across context and session boundaries. Instead, the
interpreter returns a `next_effect` in the packet: CLI actions, scheduler
ACK, and failure hint. The host or the next automation turn invokes that
data-encoded handler.

This keeps the power of around style while making the handler durable and
replayable:

- short-circuit: `decision` and `effective_action` can say `skip`, `wait`,
  `monitor_quiet_skip`, `repair_bridge`, or `ask_owner` without pretending
  the original effect ran;
- rewrite: `work_lane_contract` can preempt ordinary advancement with a due
  monitor or Lark inbox, and `capability_gate` can rewrite the next effect to
  materialize the missing capability first;
- settle: `scheduler_hint.ack_hint` and `failure_hint` tell the host how to
  commit success or failure, while `unchanged_poll` bounds repeated attempts.

Failure, cancellation, permission, and budget stay visible in typed packet
fields instead of being swallowed by a catch-all wrapper:

| Around layer | Packet field | Short-circuit examples | Rewrite examples |
|---|---|---|---|
| Capability | `capability_gate` | `ask_owner`, `repair_bridge`, `unsupported` | Repair todo and CLI actions for the missing capability |
| Interaction | `interaction_contract` | User channel `action_required`, `mode` | Primary action, protocol action, next CLI actions |
| Work lane | `work_lane_contract` | Monitor or inbox preemption, `must_attempt_work=false` | Selected lane, obligation, `next_lane` |
| Scheduler | `scheduler_hint` | Pause/delete heartbeat, no-spend quiet | RRULE, cadence class, stateful backoff |

The order of these around layers is a contract, not an implementation detail.
Changing the order changes which gate is observed first, which monitor can
preempt ordinary work, and whether an ACK is still expected after a failed
host update. Such changes need parity fixtures and focused tests.

Review a LoopX around decision with the same questions the lecture asks of a
middleware stack:

1. Which effect request is being interpreted?
2. Which around layer owns the decision, and what observation does it emit?
3. Can it short-circuit without pretending the effect ran?
4. Where is the data-encoded handler (`next_effect`)?
5. Are failure, cancellation, permission, and budget structured or swallowed?
6. Is the around-layer order explicit and tested?
7. Does evidence, trace, and budget continuity survive the host effect
   through writeback, ACK, and spend?

### CLI Is a Higher-Density Effect

A single tool call is `ToolInput => F[ToolOutput]`. A LoopX CLI packet is a
higher-density effect: one command can carry permission, budget, parameter
validation, external execution, failure semantics, scheduler ACK, and
writeback in the same request. The model still only proposes effect requests;
the harness interprets them into CLI actions.

If a vendor API later supports serial tool calls or interleaved reasoning,
that does not change the LoopX shape. It becomes an execution mode inside the
interpreter:

- serial, parallel, and interleaved are execution strategies, not new state
  machines;
- `effect_request -> interpretation -> observation -> next_effect` stays
  stable;
- `next_effect` changes from one CLI command to an ordered effect program.

## General Effect-Program Abstraction

The current `EffectTurn` lens is intentionally read-only and quota-specific.
It gives LoopX a stable vocabulary, a canonical read model, and around
semantics over one real packet. It is not yet a general effect-program
abstraction.

Refactoring alone will not create that abstraction. It creates the bounded
contexts where a shared abstraction can safely live. The two tracks are
parallel and equally important:

- refactor: keep each state family in its owning bounded context;
- generalize: extract the shared effect shape only when real runtime callers
  need it.

### Boundary With Goal Replan

Effect execution and goal replan are adjacent but different control-plane
problems:

| Plane | Question | Authoritative state |
|---|---|---|
| Goal path | Why continue, what outcome is still missing, and which path should run next? | Vision, acceptance evidence, path delta, Todo frontier |
| Effect runtime | How should one selected path execute, fail, resume, and settle? | Effect plan, host execution receipts, observation, writeback |

The effect runtime must not decide whether a milestone still serves the final
goal. Conversely, goal replan must not duplicate permission, idempotency,
failure, or settlement semantics from the effect runtime. A more general
effect interpreter does not by itself improve long-horizon goal alignment.

### Product Outcome Contract

M7 is justified only if it produces at least one of these end effects:

1. Remove a competing source of transition or command truth from a real host
   path.
2. Make partial execution recoverable through stable effect ids, explicit
   authority, idempotency, and typed receipts.
3. Let a second runtime caller reuse the same execution contract with less
   orchestration code and no loss of domain invariants.

The following are supporting evidence, not product outcomes by themselves:

- a protocol or dataclass exists;
- `EffectTurn` is constructed earlier in a packet builder;
- another packet can be mapped onto the same four nouns;
- module line budgets and parity tests pass; or
- more Todo, monitor, or gate families sit behind one interface.

The first M7 vertical slice must satisfy all of these acceptance checks:

- one real path owns `request -> plan -> host execution -> receipt -> reduce`;
- at least one previous command builder, settlement branch, or parallel
  runtime path is deleted;
- fault injection proves retry/resume does not duplicate an external effect,
  ACK, writeback, or spend;
- permission denial, cancellation, budget rejection, and partial completion
  remain distinguishable;
- public packets, CLI budgets, and existing domain transition invariants stay
  compatible; and
- a second caller is identified before a shared interpreter protocol is
  extracted.

Stop or narrow M7 when any kill criterion holds:

- the new layer primarily passes raw mappings or CLI strings through another
  object without owning execution semantics;
- production code grows while no prior source of truth is removed;
- the proposed executor crosses a model, user, or host ownership boundary it
  cannot settle itself;
- parity cannot attribute changed behavior to the new path; or
- a second real caller does not need the proposed shared protocol.

### What Exists Today

- `EffectRequest`, `EffectInterpretation`, `EffectObservation`, `EffectNext`,
  and `EffectTurn` as canonical slots.
- A core-owned settlement algebra: `SettlementIdentity`, `SettlementPlan`,
  `SettlementReceipt`, typed failure kinds, and receipt-preserving
  `SettlementResult.bind`.
- The default Codex App / CLI quota path builds one typed settlement plan and
  binds validation, durable writeback, quota spend, and conditional terminal
  closeout to the original turn effect identity. Final `no_followup` is a
  post-spend effect; ordinary successor completion remains Todo-lifecycle
  work (#3016, #3033, #3034).
- The isolated turn driver consumes the same plan, identity, receipt, failure,
  replay, and short-circuit algebra through its local callback executor
  (#3020, #3023). It journals terminal closeout separately so a failed closeout
  retries without repeating writeback or spend. Its loop controller derives
  continuation from the committed receipt chain rather than a second
  settlement truth (#3024).
- Task-lease acquisition is the first bounded non-Turn core adoption. Its
  adapter binds validation to the existing atomic lease write while pure
  eligibility, conflict, file-lock, and CAS rules remain task-lease-owned
  (#3091, #3095).
- Quota-spend callback failures use the optional `quota_rejection_action` fact,
  copied from the existing effective-action owner only when quota admission
  itself refuses the spend. `throttled_skip` maps to `budget_rejected` in the
  TypeScript settlement owner. A nested quota snapshot is not that fact;
  writeback and terminal refusals retain their own failure kinds. Missing,
  malformed or unknown actions retain `quota_spend_rejected`, even when the
  message contains "budget". This changes the former text-based diagnostic
  classification, not admission, charging or retry authority. Previously stored
  failure receipts are read unchanged; new callbacks must not rely on message
  wording to request a budget classification.
- Scheduler apply, ACK, failure writeback, and cadence remain data-encoded host
  handoffs outside agent-owned settlement.
- `interpret_quota_should_run_packet` and `interpret_turn_result_packet` remain
  packet lenses, while `EffectProgram` and
  `effect_program_from_ordered_steps` still serve compatible ordered-step
  readers for bootstrap and local scheduler construction.
- Outcome-continuity waits are causal. An `unchanged_with_reason` checkpoint
  without a material trigger and fresh evidence-linked path decision does not
  clear an earlier material checkpoint or a five-Todo completion-chain gap.
  This is intentional qualification behavior, not a watch-ACK integration
  regression (#2998, #3009, #3022).
- Formal tests now cover legal phase prefixes, failure short-circuit, replay,
  exactly-once effect identity, cross-adapter conformance, semantic mutation
  sentinels, and public-safe incident replays (#3026, #3032, #3035, #3036).
- R1 replacement: bootstrap guided rendering reads `ordered_steps` through
  `EffectProgram` (#2955).
- R2 replacement: turn executor resolves result kind through
  `interpret_turn_result_packet` (#2956).
- R3 replacement: Codex CLI local scheduler commands are built through
  `EffectProgram` (#2957).
- R5 replacement: quota should-run TurnEnvelope derives its canonical action,
  writeback, and scheduler slots through `interpret_quota_should_run_packet`.
- around semantics encoded in `capability_gate`, `interaction_contract`,
  `work_lane_contract`, and `scheduler_hint`.
- focused tests and docs that pin the lens.

The quota closeout adapter now consumes receipt-derived settlement progress
from the TS readback instead of independently treating a spend run as settled.
Normal refresh, replay and spend responses share that projection; executable
commands bind the original actor and route. Receipt repair reuses the existing
idempotent writer. This is a bounded M7.4 adoption with no shared executor or
new authority store; it does not certify terminal Todo or Goal acceptance.

Prior-host-Turn recovery uses the shared TS candidate owner in both the guard
and settlement readback. A replayed Turn may depend only on Turns first
recorded before it in the same admitted receipt history. A later Turn cannot
block recovery of its predecessor; delayed replay or binding upgrade does not
change that order. Turn identifiers and wall clocks are not ordering authority.
Persisted identity conflicts still fail closed, and a fresh Turn still requires
all outstanding closeouts. Real File/SQLite CLI coverage includes reopening a
previously blocked task after another Turn was admitted, original-binding
recovery, a failed-check closeout without spend, and one debit across successor
replay. This fixes a local recovery cycle; installed-host and wider collaboration
qualification remain separate.

The R5 compact projection also retains the existing CLI settlement plan intact,
including its effect identity, ordered conditional steps and host handoff. Action
signature coverage v5 detects removal or mutation of that plan; packets without
one retain their historical coverage. Real CLI validation exercises premature
spend rejection and one original-Turn writeback/spend with idempotent replay.
This closes a projection omission, not the short-context rollout: same-Turn
cached detail delivery, normal/replan context selection and measured model
behavior remain unqualified. The 8 KiB target and delivery growth checks stay
unchanged. See [TurnEnvelope](../../reference/protocols/turn-envelope-v0.md).

The shared settlement command renderer now includes global JSON output before
all generated step subcommands, including writeback recovery. This removes
caller-side flag insertion for App heartbeat, external CLI and visible Goal
lanes; real CLI tests cover full/envelope parity, rejection before writeback and
one debit across replay. It is a transport correction, not closure of R5 model
context-efficiency acceptance or a change to the typed settlement rules.

The existing R5 CLI captures full decisions before projection, with a private directory per invocation and readback that does not rerun admission. Default-off heartbeat renderer/shared-worker adoption now covers explicit host-owned Turns: selection retains the capture route, and real CLI tests exercise reentry and exactly-once settlement. Python adapts filesystem/command transport over the TypeScript decision owner. Native Goal begin-Turn, installed App/Lark/UI adoption, model token/IO cost and decision quality remain unqualified; existing trials are not changed. See the [TurnEnvelope capture contract](../../reference/protocols/turn-envelope-v0.md).

### What Is Missing

#### Heartbeat and Turn Envelope convergence

Under existing M7.4 and roadmap S2/S3/S6/S8, converge heartbeat and Turn
execution into **one governed work contract**, while keeping host effect
ownership explicit. The [controller/executor RFC](controller-executor-unified-contract-v0.md)
owns the complete schema/consumer migration and optional model-session split.
Its target replaces permanent quota/Turn views with one schema, full/delta
delivery and immutable recovery; this does not claim that cutover has shipped.
When an admitted Todo later waits on a timer, PR or capability, the typed
receipt owner preserves its original writeback and settlement route. Reentry
projects only that closeout, including for deferred work and after the wait
becomes due; it cannot resume business work, select a successor or invent a
retry clock. Recovery reuses the committed receipt's positive work admission
and exact settlement binding. Workspace repair alone has no business result;
the same Turn must first requalify freshly selected work on that exact Todo
through the normal guard. Broad compatibility flags without that selection
cannot turn a retained binding into work admission. A Turn with
an original semantic replan keeps that decision instead of being mistaken for
an existing business result. Real File/SQLite CLI coverage exercises generated commands, refused
rebinding, one writeback/debit, preserved initial replan and settled replay.
This source-path repair does not qualify installed host adoption or
complete-cost acceptance.
An explicitly selected CLI executable now remains in generated selection,
required-read, scheduler and settlement/recovery routes through the shared
invocation transport. The default route is unchanged; choosing an executable
adds no admission or execution authority. Candidate installation and actual
receiver adoption remain separate from this command-route qualification.
Neither today's large quota packet nor the smaller TurnEnvelope is a target
shape merely because of its size. Optional memory participation now has one
compact, signed envelope projection; the Codex CLI adapter preserves default-off
isolation. This does not qualify installed heartbeat/App adoption or model value.

Memory participation now uses a registered post-selection context contribution,
composed in the capability layer for native Turn and executing quota/heartbeat
guards. Codex, DSH and generic CLI hosts transport the same bounded context,
instructions and optional result fields. The capability owns binding/mode checks;
adapters retain only host formatting. TypeScript bounds contributions, prevents
authority/result-field replacement and binds them to the existing governing
signature document plus their source readback. Adding the original capture's
location does not invalidate an unchanged decision; changed actors, authority,
configuration readback or contributions do invalidate optional context.

The generic Python contribution IO lives with the existing registrations and
dispatchers in `loopx.control_plane.capability_hooks`; there is no parallel Python
Turn-context module. It calls the TypeScript decoder for validation and source
binding. Host adapters consume that same hook-owned projection.

Registered `PostSettlement` callbacks call the existing ingest owner after
independent exact-reflection attestation and accountable writeback/spend. The
manual-host sidecar adapter uses that registration too. `post_writeback` remains
effect-free. Per-hook receipts preserve completed work while an uncertain peer
hook recovers; the provider owner retains write deduplication and reconciliation.
Terminal replay preserves the original journal/receipt and returns recovered
owner readback as `post_settlement_recovery`, without repeating settlement.
File/SQLite native host and isolated provider cases qualify this source path.
Installed App/Lark adoption, model benefit, complete costs and the three
convergence acceptances below remain open.

Pre-registration journal recovery retains the already admitted bounded
reflection and independent validation digest without changing the saved plan,
reinvoking the Host/validator or activating legacy input for new work. Provider
effects still use the current ingest policy and original settlement owner;
cached content alone does not authorize a write.

PR dependency observation now uses the existing registered TurnStart lifecycle
shared by executing quota guards, native Turns and compatibility prequota.
Permission comes from the typed Goal/Agent capability owner, including remembered
host observations and revocation; callers need no repeated flag when permission
already exists. Capability-owned diagnostics preserve domain policy outside core
Turn code. The existing complete-source TypeScript plan, merge-fact owner and
normal execution admission remain authoritative. Goal-local single flight and
durable per-dependency cadence prevent duplicate concurrent/due reads.
File/SQLite qualification covers paused/stopped business, generated full/thin
heartbeat commands, cross-entrypoint observation, revocation, unknown effects,
unrelated validated work after provider failure and original-journal replay.
App/Lark's shared context backend consumes canonical readiness without polling.
Plans, read-only/status/context paths, missing permission and recovery add no
observation. Installed executor adoption, legitimate recovery of existing live
owners, sustained scheduling, model outcomes and the three convergence
acceptances remain open. Installed memory lifecycle adoption remains a separate
acceptance; post-writeback hooks retain their effect-free contract.

The same captured decision now retains the selected Task's existing continuation
condition and readiness through the full selection projection, signed envelope
and actual protocol-host input. File/SQLite qualification distinguishes two
repositories' identically numbered PRs, preserves the original event time and
long requirement tail, validates subsequent work, and rejects proof tampering.
Read-only managed-step and original-journal replay add no observation or debit.
This closes that fact-delivery omission; installed adoption and model outcomes
remain outside this qualification.

Bounded planning context now remains inline instead of forcing a full-decision
read under envelope budget pressure. Its existing owner retains the 3 KiB bound;
the shared DSH/cloud adapter transports the signed contribution and, for saved
historical references, the original capture route and hash. The thin renderer
requires capability detail only for an actual `agent_context.detail_ref`, since
`delivery=projected` also describes complete context. Source and actual Host-input
oracles cover enabled/off, historical references and signature refusal through
File/SQLite CLI and a protocol-only DSH subprocess. Keep the 8 KiB warning and
count packet plus necessary detail/source IO before choosing a default. Live
model behavior, installed adoption and the three acceptances below remain open.

A bound native replan exposed a further shared Host adapter omission: it kept
the generic action but dropped the signed replan packet and its evidence route.
The DSH/cloud adapter now forwards both, retaining the existing full Goal read
and same-capture replan context. File/SQLite selection-to-replan qualification
checks actual DSH provider input with a protocol-only runner, signed mutation
refusal and unchanged source/state; it does not invoke a model. The outer Turn
executor retains writeback/quota/scheduler ownership. Ordinary requests retain
their previous input. This qualifies that transport boundary only; installed
adoption, subsequent model decisions, complete costs and the three acceptances
below remain open.

Current source-path audit for the first convergence slice:

| Execution fact | Existing owner / projection | Remaining boundary |
|---|---|---|
| Goal/Agent/Todo identity | Quota selection and receipt; envelope actor, selected Todo and signed settlement identity | Capture identity and mutation-time validation remain necessary; display identity is not an execution grant |
| Full requirements | Authoritative Agent-channel work context plus remaining reads; effective Todo, acceptance and scoped User obligations, with the full Goal on its progressive read | Registered source readers fulfill current task reads inline in both transports; the mixed Goal remains mandatory on its full read path; source failure holds dependent delivery. Live model adoption and packaged host journeys remain unqualified |
| Capability refusal | Existing quota `capability_gate_v0`; compact boundary now retains exact `required`/`missing` arrays and historical source names | This repairs omitted facts, not readiness policy or capability activation |
| Selection / claim / lease | Selected Todo, action portfolio and current owning transactions | Compact selected ownership is not a fresh lease; retain the captured source and revalidate at the owning mutation |
| Replan / Goal closure | Replan action packet and the existing TS capsule preserve full audit steps/guidance, current closure assessment (judge) and evidence/non-proof conditions | Same captured File/SQLite decisions qualify clause retention and signature mutation; full evidence stays on authorized detail paths. Host/model adoption remains open; Todo completion is not Goal completion |
| Settlement / scheduler | Intact typed settlement plan and explicit host-owned scheduler projection; omitted scheduler argv references resolve the same captured full decision | Unbound detail commands are retired in this projection. Actual host readback, stale mutation refusal and exactly-once recovery still need host adoption qualification |
| Optional memory | Verified boundary participation plus fresh host binding | Off/recall/ingest/stale/provider-failure isolation remains mandatory; transport parity is not model value |

The boundary projection now retains complete scope, approval, guard and stop
clauses instead of applying display limits. The shared DSH/cloud candidate
adapter also transports the signed restrictions that it previously omitted,
keeping scope/workspace compatibility fields once. Independent source oracles
and real File/SQLite CLI plus a protocol-only Host subprocess expose the old
loss and exercise retention, tamper refusal and no-debit replay. This repairs
one existing caller family's boundary delivery; it adds no enforcement owner.
Keep the 8 KiB warning, disclose the cost of restored clauses, and qualify live
model behavior, installed adoption and total costs separately before rollout.

The capability-fact correction is a read-model change in the established
TypeScript owner. Real captured File/SQLite decisions cover refusal and admission
with memory off, recall-only, ingest-only and invalidated configuration. New
projections sign those retained facts without rewriting saved signatures; no
new coverage version, admission rule or Python policy is introduced. Host
provider-failure and settlement cases remain covered separately, and do not
qualify installed App convergence or model costs. Keep all three Todos below
open until their own acceptance is met; this inventory is not blanket permission
to delete the remaining Python IO adapters.

The continuation-audit projection also retains every ordered closeout step and
the complete current closure assessment (judge). Earlier capsules dropped those fields while signing
their already reduced projection, and clipped the continuation guidance to 48
characters. Captured File/SQLite work and replan paths now retain those clauses
and reject signature equivalence after a step/order/judge mutation. The existing
8 KiB diagnostic remains unchanged; required text can exceed it with a warning.
This closes that transport omission, not the three convergence acceptances or
permission to retire their active Python/host adapters.

Following the capsule through the actual shared Host input exposed a second
loss: DSH/cloud retained only its unavailable-context field, so a valid signed
audit never reached the provider. That adapter now forwards the existing audit
and original capture route/hash. Real File/SQLite normal/replan captures through
a protocol-only DSH subprocess cover ordered obligations, the continuing judge,
insufficient-proof conditions, signature refusal and unchanged source/state.
The same audit also found work qualification missing: the capsule filtered out
outcome-floor/degradation rules, and that Host omitted the whole existing
execution profile. The typed projection now retains it intact and the Host
forwards it. A new material result carrying that profile returns typed,
public-safe `work_evidence` and `validation_evidence` arrays and preserves its
actual delivery scale/outcome; the outer executor validates and retains both.
Native source oracles and signed mutations cover these rules. Inputs without
either contract retain their prior prompt, and older journal replay remains
readable. The outer executor keeps all state/quota/scheduler effects. This
qualifies clause delivery at that caller, not model adherence, installed
adoption or complete-cost improvement. Count the cost of restored requirements;
the earlier incomplete input is not an accepted efficiency baseline.

The complete execution profile adds 138–142 JSON characters/UTF-8 bytes and no
lines to ordinary Turn plans on the unchanged 96-row public CLI fixture. The
original base/head differential fails its 64/70-character and 128-byte growth
allowances; this remains a recorded failure. Qualify the restored outcome-floor
and degradation fields with a 160-character/byte one-time allowance only when
the old four-field profile becomes complete, with unchanged signature coverage
and no other JSON-shape change. Already complete, partial or unrelated changes
receive no allowance. Keep absolute budgets, line limits, fixture populations,
complete source/Host clause checks and the production 8 KiB diagnostic. This
explains the required presentation cost, not model tokens or efficiency gains.

中文：本批修复验收步骤、继续说明和当前判断在 capsule 中丢失的问题；签名一致曾只
比较已经裁剪过的投影。File/SQLite 同次捕获验证完整条款和篡改后的签名差异；
8 KiB 警告线保留。安装态 host、模型效果及整体统一仍按原三个 Todo 验收。

The unified required-work context proposal now returns full current task sources in
ordinary Heartbeat/quota packets as well as signed envelopes, without enabling
TurnEnvelope. The existing TypeScript interaction owner selects and fulfills
reads; registered Python adapters check the Goal source and deliver enabled
acceptance, effective work, scoped open User obligations and preference/Explore
context. The mixed Goal document retains its mandatory full progressive read;
no prose classifier infers current intent from historical sections. Unsupported
provider reads remain explicit obligations. Failed or changed sources hold
dependent delivery rather than granting authority from a summary. One Agent
carrier preserves explicit empty lists, full requirement tails and hook identity;
exact Todo detail returns one record and inventory views remain bounded.
Legacy/File/SQLite CLI qualification covers full readback, source recovery,
scoped gates and signed content mutation. See
[required work context](../../reference/required-work-context.md). This changes
the ordinary context-delivery default, not the transport selection default.
Live model outcomes, packaged App/Lark operation and overall efficiency remain
separate acceptance; active Python IO adapters are retained.

统一工作上下文提案现在让普通 Heartbeat/quota 与签名 envelope 都直接返回完整当前任务来源，
无需启用 TurnEnvelope。已有 TS interaction owner 决定读取和满足义务，注册的 Python
适配器核验 Goal 来源，返回启用的验收、实际工作项、当前 Agent 相关的开放 User 事项，以及
偏好和 Explore 上下文；混合 Goal 文件保留为必须完成的渐进式全文读取，不能按标题裁剪
后冒充原始目标。尚无适配器的来源保留必读命令。来源失败或变化会阻止依赖它的交付。
真实 Legacy/File/SQLite CLI 验证覆盖全文尾部、用户 gate、恢复和签名内容篡改。
这是普通上下文交付默认值的变化；模型效果、安装后的 App/Lark 旅程及总体效率仍待验证。

The no-write model-behavior safety adapter now recognizes the existing native
scheduler route prefix (`--registry` / `--runtime-root`) before the matching
ACK/failure command. Qualification exercises the actual binder and TurnEnvelope
projection, not only unbound synthetic argv. The adapter decodes bounded wire
only for recursive confidentiality scanning; route values, decoded extensions
and unrelated aliases remain subject to that scan, and the original packet and
wire are forwarded unchanged. Unknown or malformed prefixes and mismatched
hint/command pairs still refuse. This removes a transport false rejection; it
changes no scheduler admission, execution grant, default or host effect.
Real packaged transport qualification remains separate from live model outcomes,
App adoption and the three convergence acceptances below.

The requirement-delivery (#5844), complete closeout-clause (#6337) and original
Host decision-capture (#6350) source repairs have merged. Preserve those results;
their completion does not close M7.4, R5 or the three acceptance streams below.
Continue in this order using the existing owners and checkpoints:

| Todo | Observable outcome and decisive acceptance |
|---|---|
| Reconcile execution/context requirements across heartbeat and TurnEnvelope | Same captured authoritative decision preserves actor/Goal/Todo, required full reads, claim/lease, action selection, replan/closure, conditional settlement and scheduler ownership. Inventory omitted/duplicated facts before deleting render branches. Include optional capabilities off, recall-only, ingest-only, stale binding and provider failure; private detail is accessed only through authorized references. |
| Adopt one contract in real host entrypoints | Heartbeat full/thin and Turn host consume the same schema and per-Turn capture/detail route, with no permanent format-specific semantic views. Keep host-specific notification and scheduler transport explicit. Real File/SQLite CLI plus packaged Codex App tests cover reentry, source loss, refusal before required reads, late results, backoff and exactly-once settlement; no second admission from a detail read. Retire the replaced projection only after its last caller moves. |
| Qualify the context shape and migration default | Compare the same normal, replan, wait/recovery and optional-capability workloads against both current full and compact paths. Measure payload/model tokens, detail IO, latency, resource growth, omissions and decision/outcome quality. Keep data loss, duplicate effects, identity and settlement errors as hard constraints. Preserve supported saved prompts/receipts and reversible rollout; change budgets or defaults only with that evidence. |

**Context cost and projection convergence: next implementation slice**

The current full-source delivery closes requirement omissions, but sharing
`work_context` alone does not make either transport compact. Measure the same
captured decision through both production projections before changing a default.
Separate packet bytes from model input and distinguish operator/control-plane
trajectories from solver trajectories. A large operator observation is not
evidence that a solver consumed that observation. Count the initial response,
truncation recovery and necessary detail reads together; cached input still
occupies context. Historical incomplete inputs are not an efficiency baseline.

The next slice stays within the existing three convergence acceptances above:

1. **Make full-first, subtree-delta delivery the convergence target.** Prioritize
   ordinary `quota should-run` input actually consumed by benchmark case solvers;
   operator/control-plane and personal maintenance packets are secondary. The
   shared projection delivers complete context on first use and exact changed
   subtrees on supported warm turns. Solvers retain their product entrypoint;
   both old packet formats migrate to the single contract.
   In-packet deduplication and cold-start reduction complement this work; a
   succession of small cleanups must not substitute for qualifying the repeated
   input path. A smaller stored capture or import cleanup proves no model saving.
2. **One contract and one delivery mechanism across hosts.** Extend the existing
   typed interaction/work-context owner for full and delta context. Replace
   quota and TurnEnvelope output schemas with the unified schema; retain old
   decoders only for identified migration readers and historical receipt replay.
   Different host wrappers do not create different contract views. Preserve
   original capture, required-source checks,
   invocation, selection, settlement and detail routes. Python retains source IO;
   hosts retain their effects and context-continuity observation. No independent
   admission service, diff daemon or Python context-decision owner is needed.
   Characterize callers and remove replaced projections after adoption. A host
   transport acknowledgement before output is released proves neither complete
   model delivery nor retained context. Existing capture/handoff is an integration
   seam, not an already implemented continuity contract.
3. **Retain full current authority and cold-start requirements.** Every response
   supplies fresh identity, action, admission, scope/approval/stop conditions,
   cancellation, selection, claim/lease, unresolved reads and settlement/scheduler
   obligations. Registered complete source and packet projections are eligible for exact reuse;
   current control results remain inline even when their bytes are unchanged.
   Reused requirement text remains binding, including its textual stop clauses;
   it is neither a summary nor optional background. Preserve complete original
   requirements in the reconstructed context. Treat
   mixed Todo history and acceptance explanations at their structured owner
   before changing mandatory reads. Age, owner, status, headings or length cannot
   establish obsolescence. Moving mandatory text behind a reference and reading
   it all back is not a saving; replacing accumulated notes changes state rather
   than compressing delivery.
4. **Automatic full fallback, with no new human gate.** The host supplies an
   explicit context-continuity basis for the previously completely delivered
   projection. Bind it to Goal/Agent, consumer session and context epoch,
   projection version, source capture and projection digest. A file, matching
   hash or past receipt alone is insufficient. Compaction, reset, restart without
   proven continuity, delivery truncation, corruption, identity/version mismatch
   or unknown retention selects the complete current projection automatically.
   No approval, confirmation question, extra quota admission or quota debit is
   introduced. Existing genuine stop and unavailable-source rules still apply.

**Accepted incremental delivery design (implementation pending).** Phase one
includes the shared projection, both the benchmark solver and productized Codex
App consumers, and their full fallback. A standalone diff helper is insufficient.

| Boundary | Proposed behavior |
|---|---|
| Baseline owner | The host tracks complete model-input delivery and context continuity. The typed projection validates the supplied binding and computes the delta; it cannot infer retention from storage. Unsupported hosts continue full delivery. No model self-attestation is required. |
| Full capture | Retain the immutable complete current decision for audit, signatures and same-capture recovery. A delta is a presentation, never a new authority store or mutation patch against Goal state. |
| Delta unit | Compare complete registered JSON subtrees exactly, preserving scalar types, missing versus null, empty values and full strings. Arrays use edits at indices in the exact hashed baseline, including length changes; indices are transport coordinates, never inferred entity identities. Reorders remain exact or select full when smaller. Do not normalize timestamps/revisions or infer equivalence from prose. Use a hierarchical change tree with complete values, object changes/removals and array items/length, avoiding repeated long path prefixes. |
| Consumer recovery | Missing or mismatched baseline, skipped/out-of-order delta or truncated delivery returns the full current projection from the original capture, without rerunning admission. A lost delta is never acknowledged as the next baseline. Reset the baseline after full delivery; bound incremental ancestry with a full refresh rather than an ever-growing patch chain. Source freshness and authority are still checked before execution; presentation recovery cannot revive a stale or cancelled decision. |
| CLI adoption | Keep the ordinary quota entrypoint and existing capture/detail route. A host that can observe the active model context passes the optional baseline binding at that transport boundary. A bare CLI with no continuity observer returns full context; it must not silently trust a previous output file. Exact argument/wire shape follows caller characterization, not a new command family. |
| Turn adoption | Consume the same full/delta context owner while retaining Turn signatures and outer-executor effects. Host-only settlement execution remains distinct from a manual CLI agent's executable settlement obligations. |
| Choice of representation | Emit a delta only when it is smaller after binding/removal metadata. Otherwise emit full context. Do not truncate values or change source selection to meet a byte target. |

The surface-specific bindings below describe the existing delivery/migration
contract. They remain mandatory while those readers are active; C1–C9 of the
controller/executor proposal replaces both emitted formats. Historical captures
keep their original version and may never reuse a new-schema baseline.

**Source and change boundaries.** The shared `context_delivery_v1` presentation
covers whole quota packets, whole Turn envelopes and their common work context.
The surface is part of the existing Goal/Agent/session/epoch/capture binding, so
one surface cannot borrow another surface's baseline. This extends presentation,
not admission, persistence or settlement authority. Registered complete packet
projections can reuse unchanged subtrees. Unknown root fields stay fresh.

Current identity, action, admission, scope/approval/stops, cancellation, claims,
source health, pending reads, quota and scheduler facts stay inline. Settlement
identity, commands, step order, conditions and attachment requirements also stay
inline. Static preconditions, authoring schemas and optional attachment guidance
can be reused exactly; they remain binding, not optional because omitted from a
warm wire. The same rule covers existing vision-audit instructions while its
current judgment stays inline. Unknown fields within these control owners stay
fresh. There is no second copy of the fresh controls in the delta.

Work context retains registered complete Goal narrative, canonical acceptance
bodies and selected Todo text/notes. The Explore owner registers exact evidence,
guidance and authoring metadata; orchestration gates, branch selection, pagination,
current source revisions and unknown fields remain fresh. Source explanations,
context-read instructions and the Todo read-model's field-name descriptor may be
reused without dropping the actual Todo fields, current revision or provenance.
An unknown or mixed source without a qualified split stays fresh; ambiguous source
identities and incomplete sources select full. Changed strings remain complete,
including late constraints. Evidence statuses and timestamps are never ignored.

The previous body-only split, atomic changed arrays and per-leaf path lists were
an initial implementation, not a limit on the accepted efficiency goal. The
current codec forces fresh fields into one hierarchical change tree and computes
exact edits for the rest. Array growth, shrinkage, reorder, duplicates and null
are structural changes against that specific baseline, with no fuzzy matching.
The owner validates the expected tree and reconstructs the exact immutable target
before delivery. Omitting an unchanged live control is rejected even if the
remaining patch could otherwise reconstruct correctly. Corrupt, lost, mismatched
or more expensive deltas use the same-capture full representation automatically.

Count cold full envelopes, bindings, repeated controls, change-tree metadata,
changed values and recovery reads in the comparison. A narrow-body percentage
cannot estimate whole-packet reuse; offline retained-baseline bytes still do not
establish actual solver input savings or model outcomes. The existing projection
RPC is the only preparation/recovery interface; both transports use the same TS
codec, and Python remains a thin adapter. Production host adoption stays open.

**First real consumer and the compaction race.** The current native Codex CLI
session adapter observes session identity; its capture/handoff release occurs
before stdout delivery. Neither supplies the required model-input continuity
proof. Reading saved native history or observing compaction after the model
request is also too late. The proposed first consumer therefore needs an
adapter at the existing host's model-input assembly boundary, with these
properties before incremental delivery is enabled:

1. Invalidate the context epoch on compaction, pruning, restore, fork or unknown
   continuity, and observe complete output delivery rather than file creation.
2. Bind the candidate presentation to the original capture and expected baseline.
   Immediately before submitting model input, recheck that epoch and baseline
   under the same ordering boundary. If either moved, substitute the full
   projection from that capture. A CLI-output callback alone is insufficient.
3. Advance the baseline only after that exact presentation has entered the
   active model input intact. Failure or uncertain acknowledgement leaves the
   previous baseline unconfirmed; the next request sends full context. This
   transport acknowledgement proves delivery, never model understanding.

This is a host-adapter qualification, not an Agent or owner question. Until the
actual host exposes that boundary, its ordinary CLI stays full automatically;
do not claim that an all-full pilot validates incremental savings. Do not disable
native compaction to manufacture continuity or add a second model runner merely
for this optimization. Use a paired host-boundary prototype to establish whether
the existing transport can support the adapter; a negative result keeps basic
packet reductions useful and records the concrete missing integration.

| Situation | Automatic representation and effect behavior |
|---|---|
| First call or unsupported host | Full current control and complete context; establish no trusted warm baseline from an output file alone. |
| Same epoch, confirmed baseline, smaller delta | Fresh control plus changed source subtrees and removals; unchanged requirements retain their original force. |
| Reset/compaction between guard and model dispatch | Invalidate and replace the candidate delta with the same-capture full projection before dispatch; no new admission or spend. |
| Missing, corrupt, truncated or out-of-order delivery | Invalidate the affected baseline and recover full from the original capture. If the host cannot deliver that body intact, use the existing complete-read path and do not acknowledge a warm baseline. |
| Source unavailable or authority changed | Preserve the existing refusal/fresh-guard rule; a stored full capture cannot manufacture current permission. |
| Delta metadata costs more than full | Send full; do not clip values, enlarge limits or ask the owner to choose. |

**Phase-one consumers and delivery order.** Independently reviewable basic
packet reductions feed the existing integration test first; that test does not
wait for the new protocol. The accepted design must then qualify both consumers
below in phase one, using the same typed projection and fallback contract.
Benchmark adoption may land first, but does not close the phase while the
productized Codex App path remains unqualified.

| Consumer | Phase-one acceptance |
|---|---|
| Benchmark solver | The existing ordinary quota path delivers full first, then smaller exact subtree changes on a confirmed warm baseline. Measure all initial/follow-up model input and detail reads, tool calls, subsequent decisions and accepted outcomes in a matched authorized test. Management packets or offline byte counts do not substitute for solver adoption. |
| Productized Codex App | The shipped heartbeat/bootstrap and quota continuation paths use the same owner, with actual packaged and installed readback. Demonstrate a warm incremental delivery and useful subsequent action, plus first use, restart/resume, compaction/pruning, lost baseline and same-capture full recovery. Inspect the existing product entrypoint and visible error/recovery result; a CLI fixture alone does not establish App adoption. |

Each host must establish the model-input ordering and continuity boundary above;
qualify its real adapter rather than transferring proof from the other host.
If Codex App lacks that integration, record the concrete missing host boundary,
continue safe full delivery and keep its phase-one acceptance open. An all-full
App pilot is a correctness fallback, not completed incremental productization.
This is an engineering evidence gap, never a runtime owner question or an extra
admission gate. Preserve existing notification, scheduler, cancellation, claim,
settlement and recovery ownership; do not create a second runner or authority.

Reuse the owner in Turn transport and retire replaced projection branches only
after their callers adopt it. Frozen campaigns retain their source/treatment;
qualify new delivery on replay and an explicitly admitted subsequent run.
Design acceptance does not itself authorize merges, releases, installations or
additional benchmark runs. Runtime fallback remains automatic.

**Native input-boundary characterization (partial, not enabled).** The opt-in
`tests/control_plane/test_codex_context_boundary.py` exercises the existing
Codex `exec` and `app-server` binaries against a deterministic loopback Responses
provider. On `0.163.0-alpha.11.1`, both paths retain a complete first source plus
a literal warm change, then deliver the complete target source in the first
model request after automatic compaction through `SessionStart(compact)`.
The oracle inspects actual outgoing model input, not the hook log or stdout.
This qualifies a transport primitive only: the fixture does not implement the
shared subtree owner, run a solver, or establish packaged/installed App adoption.

The paired negative cases expose why this hook is not yet a sufficient adapter:

- Default hook output spills to a short preview. Seeing both the first and last
  requirement is insufficient proof that the middle reached the model.
- Untrusted handlers do not run. Configuring a hook, or passing the CLI trust
  bypass to an app-server launcher, is not delivery evidence. Tests trust only
  their own isolated synthetic handlers; product installation must preserve the
  host's existing trust policy.
- When the compact recovery handler exits unsuccessfully, both tested hosts
  still issue the next model request without the complete required source. The
  qualification records this as an expected failure, not a passing recovery.

The app-server's keyed `turn/start.additionalContext` is also tested without
hooks, for both `untrusted` and `application` fragments. A small complete source
reaches the initial and warm requests but is absent after automatic compaction.
A large source is already incomplete in the initial request. An opaque source
key therefore supplies neither an intact-delivery receipt nor a resident source
that survives compaction. These cases remain expected qualification failures;
they do not qualify a fallback or justify promoting source data to instructions.
No current adapter may infer continuity from that field or repeatedly retry
these unsupported routes as if a host boundary had become available.

The [native hook contract](https://learn.chatgpt.com/docs/hooks) provides useful
ordering and an explicit intact-output option, but a successful hook callback
alone cannot establish the fail-safe input boundary. Keep production delivery
full until the host-owned integration can verify the candidate after hook/output
processing and automatically substitute the same-capture full source on failure,
loss or epoch change. Hook trust, timeout, truncation and process failure must be
covered by that fallback; do not compensate with a new Agent/owner question or
by disabling compaction. The next integration proof belongs at the existing
host's final input assembly boundary for each consumer, with the shared typed
projection following that qualified seam. This checkpoint keeps phase one open
and does not reinterpret synthetic usage counters as token or outcome savings.

**Small lifecycle hooks and capture-file recovery (architecture refinement for review).**
Keep the negative native-hook results above as evidence about bulk hook injection,
not a dependency that blocks the shared implementation. A lifecycle event can
invalidate a baseline without transporting a large requirement document. The
proposed recovery route is an ordinary complete read of the immutable capture;
a recovery control message may therefore precede the complete source read.
Dependent business work still requires the complete current source. This refines
the earlier requirement to place all recovered text in the first post-compaction
model request; it adds no owner question, admission or debit.

| Owner | Responsibility and boundary |
|---|---|
| Existing quota/Turn producer | Compute the full authoritative result once and persist the immutable capture before publishing a presentation. Preserve the existing required-source, signature, selection and settlement owners. |
| Shared TypeScript context projector | Bind Goal/Agent/session/epoch, capture and digest; compare registered source bodies; keep current control/source facts fresh; choose smaller exact delta or full. Reconstruct against the complete target before release. |
| Codex lifecycle adapter | Observe startup/resume/fork/compaction and invalidate prior continuity observations. Resume may keep an unconfirmed candidate only; fresh actual-input evidence is required for reuse. Carry a small event and capture recovery reference, never the complete packet. Hook configuration/trust and event coverage must be qualified on each actual host. |
| Existing local capture/detail IO | Read the original capture in bounded pages, preserving complete text and validating target identity/digest. A preview or successful file write does not establish complete consumption. No new guard or quota operation is needed to recover presentation. |

**Sequence and failure handling.** First use or unknown continuity selects full.
Only an intact, observed delivery in the same session/epoch may become the warm
baseline; a stored file alone cannot. Generate the next presentation against that
baseline while retaining the current full capture. If the epoch moves during
preparation, discard the pending delta and use full recovery. If the event arrives
after publication, its small recovery reference points to that same current
capture. A lost/truncated delta does not advance the confirmed baseline. Bound
ancestry and force periodic full refresh; do not accumulate unbounded patch chains.

The recovery wrapper catches primary-handler errors and emits a bounded full-read
reference from the saved capture. Missing event observations or unknown wrapper
health invalidate warm eligibility at the next ordinary entrypoint. Retry only
presentation IO; never repeat admission or business effects. A missing/corrupt
capture retains the existing source-recovery failure rather than fabricating a
baseline. Treat event detection, intact delivery and complete file consumption as
separate evidence. Recovery guidance does not become a new enforced user gate.

**Implementation and qualification checkpoint.** The shared projector now has
full/delta preparation and same-capture reconstruction through the existing Turn
RPC, reused for ordinary quota context. Exact duplicate selected-Todo titles are
elided only when the complete body is identical; distinct titles and source state
remain intact. Real File/SQLite quota tests cover ordinary CLI and Codex App
profiles, shared Turn source content, epoch loss, corrupt delta, unchanged source
state and zero extra spend. This is a shipped-code proposal, not an enabled native
warm adapter or installed App delivery.

The ordinary `quota should-run` capture rendezvous now connects that codec to
JSON stdout, for both quota and `--turn-envelope`. A qualified caller writes a
private `delivery-request.json` beside the held `decision.json`, containing the
existing `scope`, optional `baseline` and `retained` inputs. It opts in by adding
`context_delivery: true` to the existing digest-bound release. The CLI binds the
Goal/Agent to the actual decision, saves immutable `projection.json`, and returns
the shared full/delta presentation with a path and byte digest for full recovery.
Missing, malformed, cross-identity or unavailable optimization input returns full;
it does not rerun admission or change the decision's success status. Legacy
releases and ordinary invocations keep their original output and file behavior.

Real File/SQLite subprocess tests cover cold/warm output, epoch reset, malformed
request, cross-Agent refusal, source preservation, original receipt and zero
spend. Recovery uses the exact saved projection, including after the candidate
delta was prepared. This closes the CLI producer seam only. The rendezvous is
still before native output processing: a release does not ACK model delivery,
and callers without a qualified continuity observer must not opt into warm reuse.
Both actual solver and installed App lifecycle consumers remain unqualified.

The recovery reference now carries a runnable `quota context-read` command.
This is local capture IO under the existing quota entrypoint, not another
projection or decision owner. Pass the original `--capture-file` and
`--capture-sha256`; follow each `next_command` until `eof`, combining `content`
pages in byte-offset order. `--page-bytes` bounds UTF-8 content (256–8192 bytes,
4096 by default); JSON metadata and host wrapping also count toward output cost.
Each page verifies the complete file digest and object before returning content.
Replacement, bad offsets or unavailable captures return an error without partial
context or quota side effects. Parent directory aliases remain usable; substituted
final symlinks and non-regular files are refused. The command works without Goal
or runtime initialization and never executes a guard, admission or debit. Page
EOF means only that file's end, not proof of model consumption or permission to
reuse a baseline. This transport-local `context_capture_page_v1` shape does not
extend control-plane lifecycle state.

The native transport probe uses the actual shared projector, bounded tool reads
and a small `SessionStart(compact)` reference. Its deterministic local provider
requests the reads; it does not prove an autonomous solver follows the recovery
route. Preserve the earlier bulk-hook and fail-open negatives. Qualify complete
first read, warm delta, compaction, primary-handler failure and full target read
from outgoing model inputs. Include every recovery page in cost and call counts;
a smaller hook or packet alone is not a saving. The next integration is the
Codex lifecycle adapter consuming the ordinary quota capture/detail path for
both phase-one consumers, without a second runner or a new approval flow.

The composed native fixture now executes the real `quota should-run` CLI from
Codex tools, using SQLite, its private rendezvous and the shared projector. It
observes complete paged output in actual outgoing model input before supplying
a warm baseline. Codex exec covers ordinary quota; app-server covers the Turn
envelope. Both recover the same immutable CLI projection after automatic
compaction, including primary recovery-handler failure, without a third guard
or quota debit. This closes a producer-to-native transport test gap; the input
oracle independently inspects the deterministic loopback provider's input.
The source text, source state and original captures are
checked independently of the delta. All pages, including recovery, remain part
of the cost boundary. These reads now execute `quota context-read`, including
digest checks, rather than ad-hoc file slicing. Installed adoption and autonomous
outcomes remain open.

The existing CLI rendezvous also accepts an optional `native_input` cross-check:
`trace_bundle`, `tool_call_id`, `capture_file` and `capture_sha256`. Its native
Codex trace-v1 reader verifies the exact full baseline in the input of the
inference that produced the still-active, model-originated `exec_command` call.
It requires a matching session, ordered trace, completed response containing
that call, and either an intact full tool result or every digest-bound context
page. Earlier inference history, partial pages, previews, other item roles,
completed calls and implicit server-side history do not qualify. Code-mode
`exec_command` children additionally require one still-live native cell whose
model-visible call id and exact JavaScript match the current inference response.
The complete body/pages must appear in actual model-visible custom-tool text
blocks, directly or in the native exec result envelope; private child results
and unforwarded output never qualify. Missing, malformed, oversized or
incompatible observations discard the retention claim and choose full automatically; ordinary callers are unchanged.
The reader only consumes already enabled private traces, with the existing
transport size bound. It reads a bounded event-log tail rather than the whole
session; if the active inference is outside that window it selects full. It
does not enable diagnostics or change native sessions.
Python owns this provider-specific IO; the shared TS projector still validates
baseline identity, freshness and exact reconstruction.

This check can narrow a caller's retention claim, never mint one or establish
future dispatch ordering. A full baseline may be reused while every original
byte is observable; this reader does not infer a patch chain from saved files.
The native CLI/SQLite composition uses this reader for warm selection, with
an independent loopback oracle. The production consumer now provides
`quota context-hook --codex-context-dir <private-session-dir>` for native events
on stdin. Ordinary `quota should-run` uses the same `--codex-context-dir` with
its existing capture destination and calls the shared projector directly;
a test-side rendezvous controller is no longer required. The Python module owns
only Codex-specific event/private-file IO, not a new capability, runner, Goal
state or decision owner. TS retains delta, binding and fallback semantics.

The caller prepares a separate private directory per native session/workspace
and registers that hook command for `SessionStart`, `PreToolUse` and `PreCompact`
through the native configuration/trust flow. The CLI does not install hooks,
alter trust or enable tracing. It checks the current `CODEX_THREAD_ID`, working
directory and active call and requires complete input from already enabled
traces; absent, incompatible or uncertain evidence selects full automatically.
Removing the opt-in argument restores the ordinary path. Existing arguments and
legacy rendezvous behavior remain unchanged; selection commands carry the same
transport argument without another human step.

Startup, fork and compact events invalidate the epoch. A same-session/workspace
resume clears the active call but retains an unconfirmed candidate and its epoch;
this is a lookup hint, never a continuity receipt. The new still-active call must
independently prove the entire baseline in its actual model input. Missing or
pruned input still selects full. Multiple native bundles for a resumed session
are resolved only by a unique valid current-call/input observation, never by
mtime or the latest saved file; ambiguous or excessive discovery selects full.
Same-session recovery carries only the latest immutable capture's complete-read command;
a different session never borrows the old reference. Preparation and publication
of the latest recovery reference share a private lock, so post-publication
compaction also recovers that capture. A primary journal write failure still
returns a known recovery reference; a publication that cannot record its reference
returns the original full payload. A candidate file is never an ACK: complete
model input is cross-checked on each reuse. A complete projection read through
`context-read` can establish the baseline body; `1` and `1.0` are both JSON
numbers, while booleans remain distinct. Missing/mismatched baseline, source
permission and freshness rules are unchanged.

Native exec/quota and app-server/Turn tests now execute this production CLI and
hook rather than a test lifecycle controller. They cover complete cold read,
warm delta, automatic post-publication compaction, primary-handler write failure,
same-capture complete recovery and zero extra guard/debit. The deterministic
loopback provider still does not prove an autonomous model follows recovery or
achieves lower total cost. The same production path is also exercised through
native app-server code-mode for both quota and Turn views, including the parent
cell/child binding, forwarded output, compaction and primary-write failure.
Missing, mismatched or ended cells and incomplete custom-tool output remain full.
This qualifies the native code-mode transport, not installed desktop adoption.
The following persistent recovery route qualifies wholly unexecuted/killed hooks;
installed adoption, trace/all-read overhead and real solver outcomes remain separate. No runtime/owner
question is added, and phase one remains open.

The existing benchmark worker now consumes this production path through an
explicit `context_delivery: "on"` heartbeat option (default `"off"`), also exposed by EdgeBench/SForge
as `--context-delivery on`. Its private trial home stages the product hooks and
uses native `hooks/list` to trust only their exact hashes for that process;
unrelated workspace hooks remain untrusted. Opt-in private traces support the
input observer. Setup failure continues ordinary full output; an existing
checkpoint handoff keeps its original capture ownership. Selection appears in
run/install/wake receipts, while setup availability is not a retention receipt.
Default runs add no hooks, trace or trust configuration, and changing treatment
requires a new trial. The product renderer and recovery pages preserve the
original Turn, capture and pinned CLI route. Deterministic native tests drive
the actual worker with both quota and Turn views through cold read, warm delta,
automatic compaction and complete same-capture recovery without extra guard or
debit. A second real worker invocation and a second code-mode app-server process
now resume the same native session, demonstrate intact prior input and select
warm delta without another cold read, for both quota and Turn views. A subsequent
native compaction still recovers the original full capture. Each benchmark wake
uses a private trace subdirectory so earlier wakes do not enter its hot-path
diagnostic scan. These deterministic checks qualify cross-wake transport,
not net solver savings. Harbor/container and Codex App installation and complete solver/cost
qualification remain open; lost-hook recovery is qualified below.
Native discovery, trace IO and all recovery reads belong in that cost comparison.

Matched native input replay now separates the subtree codec from the read policy:
compare full and delta under the same page size, forwarding and batching policy,
then compare read policies separately. Count all repeated model input (including
history and tool definitions), model requests, CLI reads and private trace bytes.
A cold-plus-warm pair can save fewer bytes than a long warm series. Smaller pages
batched into fewer model rounds can increase CLI calls while reducing repeated
input; do not attribute that combined gain to the codec alone. Deterministic
replay over captured inputs qualifies transport cost proxies, not autonomous
solver outcomes, billed tokens, cache savings or installed adoption.

Code-mode callers can forward successful CLI stdout directly without serializing
another exec-result wrapper. They may group bounded reads only when every page
survives the **final model-facing output**, including the host's separate cap.
A larger requested output budget alone is insufficient: the native host can omit
text blocks or truncate inside otherwise valid JSON. Parse success and page EOF
therefore do not establish intact delivery. Verify contiguous UTF-8 offsets and
the original complete digest; reduce a failed batch and reread the same original
capture rather than readmit. No universal batch count or increased default limit
is qualified by a particular replay. Keep truncation negatives alongside the
successful cost comparison, and include recovery in any subsequent promotion.

The production quota recovery instruction and native resume/compact hooks now
share that bounded-read guidance, including direct successful stdout forwarding,
small adaptive batches and same-capture retries. A malformed model-visible page
contributes no retention evidence. It does not permanently poison a subsequent
complete reread still present in the current input. The native observer accepts
overlapping ranges after a page-size change only when each intact range matches
the original digest-bound bytes and their union covers the complete capture.
Gaps, previews alone and a different capture remain insufficient; no history
clearing, new acknowledgement or admission is needed. Composed code-mode tests
exercise batched stdout, overlapping read boundaries, cross-process resume,
warm delta and native compaction recovery for both quota and Turn views.

A lost event handler now has a separate **read-only recovery entry**, not a
second execution/admission path. `quota context-read --codex-context-dir DIR
--goal-id GOAL --agent-id AGENT` selects the latest original capture only when
the private journal matches the ambient native session, current workspace and
requested Goal/Agent. It reads no current Goal, evaluates no guard, spends no
quota and does not change the journal. A missing/foreign/older unscoped journal
refuses lookup; an existing explicit digest-bound reference remains usable.
Lookup starts at offset zero. Every following `next_command` pins the original
path and hash, so a concurrent newer publication cannot mix captures. Missing,
changed or symlinked files retain the ordinary reader's refusal behavior.

The experimental switch is now `--context-delivery {off,on}`, default `off`.
It reuses the existing declared host context: `--codex-app` / the equivalent
`codex_app_heartbeat` profile, or the benchmark worker's existing `generic_cli`
profile plus its explicit `LOOPX_EXECUTION_MODE=heartbeat` and
`LOOPX_CONTEXT_DELIVERY=on` declarations. The worker's existing execution owner
rejects non-heartbeat modes and propagates these settings to native tools; its
scheduler declaration is unchanged. No process or ambient native-session
heuristic authenticates a host. With `on`, unsupported, absent, incomplete or
contradictory run-mode declarations fail the invocation with a nonzero exit and
an actionable reason before admission or transport writes, including hooks.
Correct the declaration or use `off`; this adds no owner question or approval.
Within a supported run mode, missing or invalid retained-input evidence still
automatically restores the same full capture. Hooks, configuration, saved presentation and
reentry preserve this binary selection. Directory options or a host handoff
alone cannot turn on deltas. Full original-capture recovery remains usable when
off. These caller declarations select a supported adapter path, grant no
execution authority and do not replace actual-input retention verification.

`quota context-config --context-delivery on --codex-app` renders the native hook/recovery configuration through
the CLI; the benchmark worker reuses that same provider helper. Shared Codex App
configuration uses `--codex-context-root` to isolate journals by native session,
while dedicated trial homes retain `--codex-context-dir`. Read-only lookup never
creates an unknown session directory. The heartbeat bootstrap preserves the
root/capture options and uses the existing per-wake `LOOPX_TURN` binding instead
of persisting a Turn ID. Native hook trust and private actual-input diagnostics
remain host-owned; missing diagnostics select full. Generated configuration
installs nothing, changes no trust and grants no execution authority. See
[activation/readback/rollback](../../heartbeat-automation-prompt.md#opt-in-native-context-delivery).
This qualifies generated configuration plus native consumption, not installed
desktop adoption or a new cost measurement.

Native context delivery can also share the existing checkpoint handoff capture.
The checkpoint owner holds/verifies the original full decision and releases its
exact digest before native projection. Lost/refused release publishes no optimized
result; interruption retains original full continuation, without another guard.
Native interrupted-cursor verification binds the context route as well as the
existing identity/capture routes. The host-side preflight retains the complete
original capture; the Darwin native-checkpoint limitation is unchanged. Deterministic native tests
cover ordinary held release, warm reuse, lost-hook compaction recovery and
midwake capture/refusal. Resumed worker tests use the latest generated guard and
settle the independently checked prior readback before a new Turn; old history
cannot stand in for a new admission. This is transport composition, not a new
cost/outcome qualification or permission to modify frozen trials.

Saved preflight presentation now uses public `quota context-project`: the host
pins the original path/hash and Goal/Agent/Turn, then the native tool invokes the
same projector/actual-input observer without another guard or debit. A compact
descriptor supplies full/delta read commands, the available target and an
original full fallback. Cold complete context remains verifiable; uncertain
native state or optional cache failure selects full. Source hash/scope failure
refuses before publication. Bounded physical-file/hash IO is shared with the
existing reader. Native tests prove complete warm reconstruction through the
real checkpoint worker's generated saved-read entry across resumed wakes,
without guard replay. Existing
decision freshness, checkpoint ordering and default-off full preloading remain;
total-cost/solver-outcome qualification must include the added descriptor and
tool reads, not only delta bytes.

A matched deterministic native worker diagnostic now includes the generated
preflight, resumed history and every page, using the same synthetic requirement
and default function-tool read policy in both execution orders. Across two
two-wake trials per arm, serialized-input token estimates fall 35.83% and reader
calls fall from 72 to 48. Cold input increases about 11%; savings come from warm
reuse. Elapsed ranges overlap, so this establishes no latency improvement.
Both arms enable diagnostic capture; it does not qualify tracing cost against
uninstrumented execution, autonomous solver outcomes or exact billing. The
separate real-capture replay remains a different workload and read policy.

A subsequent three-arm diagnostic at runtime source `912d56036` compares
ordinary tracing-off, ordinary tracing-on and opt-in delta/tracing-on execution.
Three counterbalanced two-wake trials per arm include generated preflight,
resumed history, descriptors, persistent instructions and every 4096-byte serial
read. Against ordinary tracing-off, delta reduces the summed serialized-input
o200k proxy from 5,482,641 to 3,499,312 (36.17%), whole-request proxy from
5,892,161 to 3,801,600 (35.48%), and requests from 114 to 84. Reader calls fall
from 108 to 72, with six additional projection calls. All trials verify complete
original bytes, independent reconstruction, session continuity and unchanged
guard/debit counts. Tokenization runs outside timing; synthetic loopback usage
does not establish billing or autonomous solver outcomes.

The default-off comparison exposes an IO tradeoff: delta retains about 5.77 MB
of native diagnostics per two-wake trial, versus zero without tracing. Native
background plugin-catalog clones dominate the variable total retained footprint;
endpoint file sizes are not cumulative writes. Timing, CPU and net IO savings
remain unqualified. Hook identity discovery now disables plugin features only
in its temporary native process, avoiding unrelated catalog startup. It still
asks native `hooks/list` for hashes and trusts only the exact owned hooks;
solver configuration, default-off execution and model settings are unchanged.
Real native config readback verifies this isolation, repeated discovery returns
the same trust identities, and discovery sends no inference request. The same
test detects the old helper's enabled plugin features. The cost figures above
precede this helper fix and do not measure its latency or disk savings.

The shared codec also avoids copying unchanged/discarded replacement subtrees
and canonicalizing identical requirement strings. A V8 capture identifies
comparison, sizing and cloning work; a controlled intervention replays 31 fixed
real captures through the loaded owner. Four counterbalanced process pairs,
each with 12 measured replays after warmup, reduce mean prepare/recovery time
from 347.30 to 303.06 ms (12.74%). Every emitted delivery remains byte-identical,
and reconstruction plus detached-value checks preserve both source captures.
This measures the loaded codec on one host, excluding startup, CLI/backend IO,
native host, model and whole-Turn time. It changes neither packet bytes nor the
input-token result; complete-cost and solver-outcome acceptance remain open.

The native adapter now uses the existing Core original-capture IO owner for
shared read guidance and projection, removing the reverse Core-to-CLI dependency.
CLI presentation dispatch remains ahead of authority IO; event recording,
settlement readback and memory ingestion keep their original order. Native
input parsing, call binding and digest-bound page validation have separate
responsibilities without weakening lifecycle or complete-coverage checks.
The existing legacy quota adapter supplies its own field binding to both codec
operations; caller JSON cannot nominate additional reusable controls. Unknown
fields stay fresh. These changes preserve the measured runtime contract rather
than introduce another projection or compatibility reader.

The opt-in benchmark worker carries a compact recovery instruction in native
`developer_instructions`, independently of SessionStart/PreToolUse delivery.
Native code-mode consumers can carry the same product-owned instruction in
their isolated native configuration; append it to existing host instructions
rather than replacing unrelated policy. Its conditional trigger is continuing
a captured decision after context loss, not starting an ordinary new turn.
It directs same-capture reading before dependent work without a user question;
independent authorized work remains possible and recovery renews neither authority
nor freshness. The first lookup page supplies the existing bounded-read guidance.
Default-off workers add no persistent instruction. Deterministic native tests
remove the complete source during real compaction and then omit or kill every
subsequent hook process. The actual worker and code-mode host recover all bytes
through the persistent public CLI route without another guard or debit. Private
files/session identity still establish no retention claim; actual current input
remains the independent reuse criterion. Include the persistent instruction and
all recovery reads in future cost comparisons; prior measurements exclude this
new instruction. This does not claim live desktop installation or autonomous
solver effectiveness.

Capture regression qualification also distinguishes settlement binding from
closeout: an upgraded receipt with an outstanding semantic replan still permits
an inert replan capture and grants no delivery authority. Explicit pending
closeout suppresses that capture. The real CLI fixture and the typed selector
now test both facts instead of inferring closeout from the receipt status.

Qualification compares matched normal, replan and wait/recovery workloads with
optional capability modes unchanged. Include first delivery, warm reuse, reset,
deletion, array reordering, lost/duplicated/out-of-order deltas, partial output,
source/identity mismatch and late requirement changes. Reconstruction must equal
the complete current projection, and fresh control facts must never be omitted.
Measure initial plus
follow-up model input, context occupancy, cache/non-cache and complete cost,
read count, latency and IO, then verify the subsequent action and accepted
outcome. Keep exactly-once effects and required-source refusal as hard constraints.
A serializer roundtrip or offline delta establishes representational feasibility
only; it cannot settle installed adoption or the complete-cost acceptance.
SQLite remains the default local performance qualification; preserve affected
supported-backend correctness/recovery checks. This checkpoint changes no
runtime default, budget, required-read rule, authorization or active experiment.

Native Turn detail adoption now persists the real `run-once` producer's full
observation and binds the Host request to it through the existing TS envelope
owner. Real File/SQLite CLI tests inspect actual subprocess input, change the
canonical Todo source, and verify original detail, retry and no-effect replay.
The model provider is a protocol fixture. Read-only planning and optional quota
capture defaults remain unchanged; native executing Turns now retain private
capture evidence. Missing or mismatched evidence refuses Host reinvocation;
legacy cached-result recovery and terminal readback preserve original receipts.
This closes the producer-to-detail transport gap only. Live models, packaged
App/Lark, later authority checks and the remaining convergence matrix retain
their independent acceptance; no Python projection is retired by this slice.

New quota and native Turn captures now share compact JSON encoding, removing
layout whitespace from stored detail without omitting decision values or string
content. The original atomic publication, source signature and retry owners are
unchanged. File/SQLite native Host qualification covers both new compact and
historical indented observations, including source changes, missing/corrupt
detail and exactly-once recovery. This is a lossless transport cost reduction,
not evidence of model quality, complete Turn cost or a new convergence default.

The shared Host result adapter now retains supplied text for the existing Turn
validator instead of truncating it into a passing candidate. Over-limit next
actions could lose a trailing approval constraint while still being accepted.
JSON booleans, numbers and containers also retain their types for rejection by
that validator; false/zero cannot disappear into sparse-field defaults. Legacy
null/omitted optional text retains its existing absence semantics.
Explicit delivery scale and outcome now retain their values and JSON types;
only omitted metadata uses the existing sparse defaults. Previously a declared
`surface_only`, unqualified `outcome_gap` or invalid value became
`outcome_progress`, bypassing the existing refusal and recording a quota spend.
The shared prompt uses the existing delivery vocabularies. Native File/SQLite
tests preserve valid scale history. New material results also reuse the existing
settlement rule before task validation or effect preparation: `surface_only`
and `outcome_gap` produce a typed `validation_failed` without a prepared
writeback. Previously vocabulary-valid but nonqualifying results reached
refresh-state, raised a generic CLI error and left an in-progress prepared
writeback that repeated the same refusal. The Host contract carries no typed
blocker observation; ordinary CLI blocked settlement is unchanged. Historical
cached results retain provider readback/recovery, including an unknown provider
observation followed by a committed receipt, without relaunching the Host or
repeating writeback. No settlement policy is added here.
File/SQLite native CLI and actual DSH subprocess qualification cover rejection
before task validation/writeback/spend/ACK, failed-result replay, and a corrected
fresh Turn settling once. Boundary-length valid results retain their behavior.
This is result-transport correctness; it does not qualify model adoption, undo
Host work already performed, or close the three convergence streams.

Turn completion now carries an exact text commitment from the admitted full
Todo source to the existing atomic terminal owner. Changed requirements refuse
completion; note-only and unrelated Todo changes remain valid. Provider CAS
fences concurrent replacement, and historical receipt recovery precedes fresh
source checks. Saved Turns without full-source proof refuse new completion;
already accepted legacy receipts retain their fingerprints and recover first.
File/SQLite CLI and shared File/SQLite/PostgreSQL conformance
cover refusal and recovery. Final no-followup retains its existing settlement
order: a terminal refusal preserves any earlier writeback/spend receipts.
This qualifies task-body completion safety, not settlement-wide source atomicity,
installed host adoption, live models or the remaining convergence acceptance.

The first ordinary-CLI consolidation replaces an exactly identical
`heartbeat_recommendation.blocked_priority_fallback` mirror with
`blocked_priority_fallback_ref="$.blocked_priority_fallback"`. The complete root
fact, notification, execution obligation and original capture remain intact;
different mirrors are retained, including Boolean/number differences. The existing
`--include-detail agent-todos` view retains the legacy mirror. File/SQLite CLI
tests cover same-capture action dimensions and no-debit replay. Original source
hashes intentionally distinguish renderings; this does not change signature
coverage or make a rendered view a substitute for the original capture.
Legacy-field access stays in the existing packet owner; the CLI invokes its
projection function without adding another legacy migration consumer.

The acceptance-context adapter now uses the existing exact Todo inspection for
selected work. It retains the complete Goal acceptance basis and cumulative
outcomes, while avoiding a second inventory of unrelated task-binding
observations and a second selected-Todo body. File/SQLite CLI and actual DSH
protocol input cover configured checks and outcomes without checks; Todo-less
inspection still returns its original inventory. This qualifies the scoped IO
boundary, not live model benefit, installed adoption or total Turn cost.

The native acceptance/capture matrix also covers work added or changed after
capture, a future resume condition and explicitly removed checks. The original
synthetic work is validated and settled before a new managed Turn: current
admission rejects unbound/stale work and pending waits, while removing checks
does not create a new owner question. Cumulative requirements and their sources
remain intact; current work status and its derived basis fingerprint may change.
The original detail remains byte-for-byte readable without another admission or
authority revision. These cases qualify the existing boundary, not a new
execution default or any of the three complete convergence streams.

The Goal source context now separates explicitly generated Todo regions using
the existing writer's strict ownership parser. All remaining text is delivered
verbatim through the shared typed context owner, with the full-file hash and
excluded-region lineage. Unmarked/mixed documents retain their progressive read;
malformed ownership fails closed. This changes default context delivery, not
canonical Todo authority, source files or cumulative acceptance. File/SQLite
native guard and signed DSH protocol-input checks qualify this source boundary;
model adoption, installed App/Lark, full Turn cost and all three convergence
streams remain open. Large arbitrary narrative is retained without truncation.

Next, inventory authorized real Goal captures across active work, peer/replan,
wait/recovery and optional-capability shapes, then map each retained clause to
its shared typed owner and actual consumer. Migrate one complete caller family
before retiring its replaced projection. Compare the same captured decision
through full/thin heartbeat and Turn, counting required detail reads, tokens,
serialization/IO, latency and resource growth. Byte savings from this mirror
removal qualify only that rendering delta; model quality, complete costs,
installed App/Lark adoption and migration defaults still require their own
evidence. Keep private source material out of public fixtures and checkpoints.

The earlier cost slice reproduced a regression-budget gap, rather than a
missing fixture alias or permission to remove required context. Comparing
`aa87cc019` with `fc411c878` on the unchanged public CLI fixture, identical
temporary aliases and command arguments produced these JSON stdout costs:

| Public fixture / surface | Base characters | Candidate characters | Base compact JSON | Candidate compact JSON |
|---|---:|---:|---:|---:|
| 36 Todos / 1 Agent / 12 runs, `turn plan` | 16,115 | 17,403 | 12,112 | 13,145 |
| 1 Todo / 1 Agent / 1 run, enabled multi-subagent `quota should-run --turn-envelope` | 10,990 | 11,661 | 8,892 | 9,464 |
| 36 Todos / 1 Agent / 12 runs, `heartbeat-prompt --thin` | 3,122 | 3,122 | 3,069 | 3,069 |

The crowded Turn increase includes 484 compact characters in the envelope and
510 in the newly returned hook-dispatch diagnostic. The envelope now states
which hooks observed empty context, discards cached content for them, and
distinguishes fulfilled pre-work reads from later action-specific freshness.
Those instructions and signed observations carry useful decision semantics.
The hook diagnostic has a separate effect-disclosure role; audit its actual
consumers before moving or removing it. Pretty-print overhead is measured
separately and is not a token, latency or model-quality result.

Under the original guards, crowded `turn plan` exceeded its 16,000
character ceiling, and the enabled multi-subagent runner exceeded its 9,000
character envelope ceiling. Its nested fixture paths emit 11,291 characters
on the base and 11,961 on the candidate; that is a different path workload
from the table. The historical base was already red; those results remain
failures under their original guards.

The current source checkpoint is qualified separately. The merged calibration
at `a855a547e7a3fc4f9b5c3e02e1ea73cf0f591f95` updates the existing regression
guards, with its matched workload and bounded headroom recorded in the
[budget decision guide](../../development/testing-and-quality.md#budget-failure-decisions).
An independent real CLI rerun at `0cd547b0c442a5c0ec7f608827b02dce7aeb0aac`
passes all 96 producer rows with candidate budget enforcement. Its immutable
`2244b96f1e2e5c90bef43ae4c140c0994bfcc07a` comparison passes the existing
semantic differential with 96 rows on each side, no candidate-only rows and
no review-required changes. The fixture still contains 1/36/18 Todos,
1/12/12 runs, full command routes, enabled multi-subagent and blocking gates.
Crowded Turn emits 16,250 characters against 17,000; the enabled envelope
emits 11,426 against 12,000. Thin JSON remains 3,122 characters. Existing
runner tests also reject real stdout inflation without rewriting paths or
shrinking the fixture.

The consumer audit retains both hook views: the typed envelope signs unavailable
context, hook coordinates and dependent-action freshness; the live Turn adapter
also reads hook results to disclose local state writes through `effects` and
`boundary`. Removing that diagnostic as duplicate context would lose effect
disclosure. Compact serialization measures formatting savings separately;
it does not qualify a new host transport. Current source regressions cover
File/SQLite preferences, empty/failed hook invalidation, optional-off isolation,
selected requirements and later reads. This closes the bounded source-budget
gap, not M7.4: packaged host adoption, model token/IO/latency/outcome evidence
and a reversible transport-default decision remain open. Execution quota,
wire limits and frozen promotion criteria are unchanged; no runtime cost
reduction is claimed.

#7161 retires the old `protocol_action_packet` reader format: field selection,
summary reconstruction, residue/opaque fallback and their transport inputs are
removed. Effect/CLI/Envelope/Host consumers explicitly reject old packets and
capsule witnesses before conversion or signature use. Frozen historical records
remain unchanged; packet-free v0 signatures and execution checks retain their
meaning. This bounded cleanup supersedes historical reader compatibility, not
M7.4 or the benchmark/Codex App context-transport acceptance. See the
[format boundary](../../reference/protocols/protocol-action-packet-decision-v0.md).

Do not add a generic executor or lower an acceptance threshold to make a short
packet pass. Preserve unsatisfied requirements and distinguish transport parity,
installed host adoption and useful model outcomes. See the
[current envelope contract](../../reference/protocols/turn-envelope-v0.md#optional-memory-participation).

- A generic shared executor is deliberately absent. The current adapters share
  plan/receipt algebra but have different execution ownership, so M7.3
  is closed with no follow-up rather than filled with a speculative framework.
- Regular LoopX paths still need bounded adoption decisions. A path should use
  the algebra only when it has multi-step external effects, one stable
  identity, durable receipts, replay requirements, and duplicate settlement
  truth that the change can delete.
- Race/CAS qualification remains deferred until a real concurrent execution
  entry point exists. Synchronous adapters do not justify concurrency
  infrastructure or tests by themselves.
- M7.4 remains open as an evidence-driven replacement gate, not a request to
  convert every Todo, gate, monitor, scheduler, or replan rule into a Kleisli
  arrow.

### Core-Path Adoption Matrix

| Core path | Decision | Boundary |
|---|---|---|
| Codex App / CLI normal-turn closeout | Adopted | Core plan/receipt algebra; quota adapter owns CLI binding and durable settlement checks |
| Isolated turn-driver closeout | Adopted | Same algebra; local callback executor and journal remain turn-driver-owned |
| Task-lease acquire | Bounded adoption | Validation and durable write share the core algebra; eligibility, conflicts, locking, CAS, and persistence remain task-lease-owned |
| Turn continuation | Adopted as a consumer | Pure controller reads the committed receipt chain; it does not execute host effects |
| Todo completion, `refresh-state`, quota spend | Bounded adoption | Ordinary completion stays Todo-owned; refresh/spend form the base settlement, and final `no_followup` is a conditional post-spend closeout |
| Goal vision and replan checkpoints | Selective typed qualification | Causal evidence and completion-chain checkpoints are shared invariants; vision policy is not moved into the settlement executor |
| Capability gates, user gates, monitor selection | Keep domain-local | These are decision state machines unless a future change proves duplicated external-effect settlement |
| Scheduler apply, ACK, cadence, failure hint | Outside settlement | Host-owned effects stay data-encoded and are never hidden behind the agent executor |
| Bootstrap and local scheduler command rendering | Read-model reuse only | `EffectProgram` may read ordered steps; no runtime migration without duplicate truth to remove |
| Concurrent/racing settlement | Deferred | Add race/CAS behavior only with a real concurrent caller and authority boundary |

### When To Generalize

Generalize execution only when at least two real runtime paths share both
plan/receipt semantics and execution ownership. The current adapters prove the
algebra but refute a shared executor: one crosses CLI/host boundaries, one owns
in-process callbacks, and one delegates atomic persistence to the task-lease
bounded context. Packet similarity or a common `bind` method does not override
those boundaries.

Before then, keep the abstraction as a documented lens and add tests that
prove each packet maps losslessly. This avoids building a generic `Effect`
framework that no runtime uses.

### Replacement Status

R1, R2, R3, and R5 are complete:

- R1 bootstrap guided rendering through `EffectProgram` (#2955);
- R2 turn executor result-kind resolution through `interpret_turn_result_packet`
  (#2956);
- R3 Codex CLI scheduler command set through `EffectProgram` (#2957).
- R5 quota should-run TurnEnvelope through `interpret_quota_should_run_packet`.

R4's original generic-executor proposal is closed with no follow-up. Reopen it
only when another real caller can delete duplicate orchestration without
crossing an authority boundary.

### Qualitative Change Plan

The current effect abstraction is a read lens plus three small runtime
replacements. M6 must not be called mostly complete until all of the following
are true:

1. Hot modules shrink to bounded sizes:
   - `loopx/quota.py` below 2000 lines (currently 1043);
   - `loopx/status.py` below 2000 lines;
   - `loopx/heartbeat_prompt.py` below 1200 lines.
2. `loopx quota should-run` builds through a bounded `should_run` decision
   module, and `loopx.quota.build_quota_should_run` becomes a thin
   compatibility wrapper.
3. `EffectTurn` and `EffectProgram` are consumed by CLI quota, turn driver,
   and bootstrap construction, not only by tests and renderers.
4. No effect abstraction remains test-only.
5. Maintainability, import-graph, CLI output, and hot-path interface ratchets
   pass without new exceptions.
6. Doubao/model-behavior shadow qualification covers changed agent-facing
   packets.

Phases:

- Q1: Stop milestone claims; keep M6 in progress.
- Q2: Characterize hot modules and capture parity fixtures for
  `quota.py`, `status.py`, and `heartbeat_prompt.py`.
- Q3: Extract the quota `should-run` decision and packet builder into
  bounded modules. Done: `should_run.py` entry decision (#2963),
  `should_run_prepare.py` preparation chain (#2964), and
  `should_run_packet.py` route/packet assembly (#2965).
- Q4: Extract status read models, collection, and presentation into bounded
  modules. Done: bounded status projections (#2967-#2978); `status.py` 1392.
- Q5: Extract heartbeat prompt builders into bounded modules. Done: bounded
  heartbeat task body/builder/support modules (#2979/#2980/#2982);
  `heartbeat_prompt.py` 159.
- Q6: Make CLI quota, turn driver, and bootstrap construction consume
  `EffectTurn` / `EffectProgram`. Done: quota should-run TurnEnvelope consumes
  `interpret_quota_should_run_packet` (#2983); turn driver and bootstrap
  consume `interpret_turn_result_packet` / `effect_program_from_ordered_steps`.
- Q7: Add quality gates and focused tests for each extraction. Done: RFC
  module budgets are ratcheted in `module_metric_baseline.json` and a focused
  M6 quality-gate pytest pins the hot-module ceilings plus the runtime
  `EffectTurn` consumption (#2984).
- Q8: Re-evaluate M6 only after the gates pass. Done: audit evidence below.

### M6 Completion Evidence

- Hot module lines: `loopx/quota.py` 1049, `loopx/status.py` 1392,
  `loopx/heartbeat_prompt.py` 159.
- Maintainability ratchet: `ok=true`, no unreviewed findings, no stale
  exceptions.
- Focused M6 audit suite: 172 passed across quota parity, status re-export,
  heartbeat support, effect interpreter/program/turn families, CLI output
  budget/differential, import boundaries, model-behavior/Doubao shadow, and
  turn driver/executor.
- `loopx canary quality-audit`: `ready=true`, `gap_count=0`, `drift_count=0`.

### M7: Effect Program Runtime

M6 makes the effect lens runtime-consumed but still descriptive: packet
builders compute their decisions and then map them onto `EffectTurn`. M7 must
not react by making every state family implement one protocol. It must first
prove that a typed effect runtime removes one real orchestration split-brain.

M7.0: inventory real multi-step runtime candidates. The selected core is
normal-turn settlement from a stable quota decision through validated
writeback and exactly-once spend. It has two real adapters: the default Codex
App interaction path and the isolated turn driver. Scheduler apply and ACK
remain delegated host handoffs. Guided bootstrap was not selected because some
ordered steps belong to the model, user, or host; quota-to-host scheduling was
not selected because LoopX cannot settle the external automation mutation
itself.

M7.1: characterize the selected vertical slice before adding a protocol.
Capture parity fixtures for legal and illegal transitions, partial execution,
retry, cancellation, permission denial, budget rejection, and settlement. The
durable transfer must include cancellation at writeback and scheduler handoff,
permission denial at host execution and quota spend, and spend-budget
rejection after writeback. This stage preserves current runtime behavior,
including any split projection that M7.2 is expected to repair. It must also
characterize the default Codex App selection-drift seam: after the selected
Todo is completed and writeback advances the frontier, spend must still settle
the original effect identity rather than bind to a newly selected successor.

M7.2: replace the core settlement truth with one typed plan/receipt algebra. A
plan step must carry a stable kind, owner, precondition, idempotency identity,
and expected receipt. The default Codex App path and isolated turn driver bind
validation, durable writeback, quota spend, and conditional terminal closeout
to the original quota-turn effect identity. Ordinary successor completion may
advance the Todo frontier before settlement, but final `no_followup` is applied
only after matching writeback and spend receipts; no terminal-guard exception
is allowed. Each replacement PR must delete its corresponding manual command
or settlement truth. Raw mappings and free-form CLI commands may remain
compatibility payloads, but they are not the semantic execution contract. The
composition must satisfy the identity, associativity, short-circuit, replay,
and ordering properties defined above, keep cancellation, permission denial,
and budget rejection distinct, and leave scheduler apply or ACK outside the
agent-owned settlement boundary.

M7.3: after both M7.2 adapters consume the proven plan and receipt semantics,
compare their execution ownership. The 2026-08-21 cutover qualification found
that settlement identity, bind/short-circuit, replay seeding, next-action
selection, and commit reduction were still duplicated across the adapters.
This reopens M7.3 for one bounded TypeScript Effect runtime. The runtime owns
that shared algebra and the first internal effect, atomic Turn-journal
checkpointing. Its server is only a temporary Python-to-TypeScript transport;
one static typed handler registry routes coarse transactions to domain owners.
It is not a generic composition framework and does not move model, user, host
scheduler, credential, or third-party authority behind a universal executor.
Every replaced Python semantic path is deleted in the same cutover PR.

**2026-10-01 identity-boundary slice:** `effect_program.ts` now owns one decoder
for executable Todo/replan identities consumed by Turn settlement and journal
validation. The internal discriminated union excludes dual targets and mutation;
executable values exclude `unbound`. Supported v0, scoped v1 and schema-less
adapter inputs preserve effect IDs. Non-string IDs, unsupported declared
versions and contradictory binding metadata fail before provider dispatch or
receipt replay. The journal reader reuses this rule instead of maintaining its
own v1 binding comparison. Evidence lives in the existing effect-program,
settlement-parity, journal-effect and inspect-journal CLI tests, including real
File readback. This qualifies the identity boundary only; composite crash/lease
recovery and full provider conformance remain separate acceptance.

The 2026-10-01 Turn recovery cutover moves provider-return, completion-result and readback
classification into the Turn TypeScript owner. Each reduction authorizes one
of prepare/execute, resolve, execute-prepared, checkpoint or abort-prepared;
checkpoint acknowledgement precedes the next provider. Invalid completion or
an explicit mismatched payload effect ref is held before journal advancement.
Unknown readback retains prepared intent; confirmed absence permits execution
with the same ref. Existing explicit failed-turn retry gates still apply.

The transient request/reduction contract advances to v1 and rejects v0 callers
before provider authorization. Upgrade or roll back the Python interpreter and
TypeScript reducer together. Persisted journal and receipt schemas, effect
identity, public CLI flags and legacy provider payloads without an explicit
ref remain compatible. The existing Turn-local provider step/resolution sets
are relocated, and the internal action union is extended; no new shared state
vocabulary is introduced. Fresh two-provider settlement uses five RPCs (preflight,
return admission and persisted acknowledgement per provider), versus three
previously; fully committed replay remains one. This is an explicit correctness
cost while Python hosts IO, not a latency optimization. PR evidence compares the
same base/head workload. See the [bounded recovery checkpoint](composable-state-machines-recovery-verification-v0.md#turn-settlement-qualification-boundary)
for qualification boundaries.

M7.4: expand one bounded family at a time only when it removes duplicate
knowledge and switches a real production caller. Todo, monitor, capability,
scheduler, and gate state machines keep their domain transition invariants.
They may execute through the same managed runtime as they migrate, but they do
not move behind one generic state protocol merely because their packets have
similar fields. After the CLI is native TypeScript, CLI-only execution imports
the kernel in-process; the daemon remains optional for App/multi-client shared
authority rather than a mandatory server per family.

The replan semantic-exit repair in #3208 is an explicit non-candidate:
`refresh-state` already re-derives the current obligation and records a typed
semantic ACK, while the defect was an extra goal-frontier settlement condition
that ignored valid non-successor ACKs when acceptance gaps remained. This is a
domain-local reducer/ACK invariant, not a second multi-step executor. Keep it in
the replan/goal-frontier owner. Revisit Effect Program migration only when a
second real runtime scenario—such as a quota/status read ACK with the same
plan/receipt lifecycle—can replace duplicate orchestration across two adapters.

The earlier R5-R9 list is therefore not an implementation queue:

- the shared `EffectInterpreter` protocol is deferred to M7.3;
- packet-before-view ordering is replaced by one canonical decision-plan
  source;
- guided bootstrap remains one candidate, subject to host-boundary review;
- turn closeout is another candidate and may be the better first vertical
  slice; and
- family-wide alignment is replaced by the duplicate-knowledge gate in M7.4.

M7 completes only when a real vertical slice meets the Product Outcome
Contract, its old path is removed, and a second caller provides evidence for
the abstraction that remains.

### Replacement-First Rule

Every M6 code change must replace an existing real runtime call path, not add
a parallel unused abstraction.

- Before replacement: capture a parity fixture or smoke for the existing
  path.
- Replace: make runtime read/write flow through `EffectTurn` / `EffectProgram`.
- After: delete the old path, or keep a compatibility wrapper only when a real
  external import or persisted contract requires it.
- Test-only additions do not count as M6 progress.

Example replacements:

- `bootstrap_command_pack` should read `ordered_steps` through
  `effect_program_from_ordered_steps` before rendering or validation;
- `turn_driver/executor` should derive result status and next phase through
  `interpret_turn_result_packet` before committing a receipt.

## Semantic control and execution ownership

The optional [controller/executor profile](controller-executor-unified-contract-v0.md)
separates model responsibilities within these owners: the controller is the sole
model-facing Core client; the executor exchanges natural-language work and
artifacts under host-enforced scope. It is not the deterministic Turn Loop
Controller. One Agent may bind those two roles without creating two competing
work executors or a second authority. Single-session behavior remains available;
role binding, result validation and total-cost qualification precede promotion.

LoopX's semantic control plane preserves work meaning across Turns, Agents and
runtimes: intent, ownership, dependencies, authority, evidence and continuation.
Its execution responsibility covers the typed transactions and settlement
steps it actually owns. Agent/capability reasoning proposes domain outcomes;
the kernel checks their binding, admission and lifecycle obligations. Domain
verifiers and users still judge the substance of an outcome.

| Boundary | Owner | Observable commitment |
| --- | --- | --- |
| Domain judgment | Agent and capability | Proposed action/outcome with scoped evidence |
| Control decision | Existing typed domain kernel | Legal next action and required proof from explicit facts |
| Internal execution | Owning transaction/effect adapter | Durable state transition and bound receipt |
| External execution | Host/provider | Its actual model/tool/environment effect and factual readback |
| Presentation | Read-model owner | Evidence-backed state and available actions, with freshness limits |

Sharing an algebra does not transfer execution authority. Host continuation,
sandbox snapshots, model/tool interception and external rollback require their
own supported runtime contracts; an effect plan or transcript does not prove
those capabilities. No generic executor or second permission owner is added.

## Decision replay, effect recovery and simulation

| Operation | Inputs and promise | Limit |
| --- | --- | --- |
| Pure decision replay | Fixed trusted facts, command and rule version reproduce a decision | Does not execute effects or restore current authority |
| Committed-effect recovery | Same logical identity and verified durable receipts skip committed steps and resume the owning protocol | Does not prove an unknown external effect never happened |
| Counterfactual simulation | Explicitly substituted facts and a controlled interpreter compare possible decisions | Not evidence that the real provider or model would produce that outcome |

Record versions, identity, ordering and relevant outcomes at the existing
receipt boundary. Never rerun a model and call its new output historical
replay. `unknown`, permission denial, cancellation, budget rejection and
committed success retain their different recovery meanings. Cancellation does
not erase an in-flight external effect. Retry must retain identity and follow
the provider's guarantees; unsupported readback remains unknown.

Adapter conformance compares observable effect order, short-circuit point,
receipts, authority rejection and recovery, not only equal return values.
Identity and associativity apply to regrouping the same ordered program, not
reordering or speculative parallel execution. Preserve the existing decision
against a universal executor until a real shared authority boundary justifies
one. Use [composition verification](composable-state-machines-recovery-verification-v0.md)
for multi-domain sequences and [provider acceptance](provider-effect-acceptance-v0.md)
for protected external effects; neither contract implies cross-system exactly-once.

## State Machine As Interpretation Table

Instead of teaching state machines as a list of enum values, teach each state
machine as an interpretation table:

```text
Input effect | Interpreter | Decision | Observation | Next effect
```

Example for monitor scheduling:

```text
Monitor cadence or due horizon
  -> scheduler interpreter
  -> host RRULE / initial interval
  -> scheduler_hint packet
  -> next heartbeat or monitor poll
```

This preserves the existing state machines while making their purpose
visible.

## Milestones

### M0: RFC and Lecture 0

**Goal**: Publish this RFC and add a lecture that tells the story before any
state machine detail.

Steps:

1. Merge this RFC.
2. Add `Lecture 0: Harness Is the Effectful Program` to
   `docs/development/control-plane-course/`.
3. Rewrite `docs/product/core-control-plane/state-machine.md` to include an
   interpretation-table section for each state family.
4. Update `docs/README.md` and course navigation to point to the RFC.

Acceptance criteria:

- A new contributor can explain LoopX in one paragraph using the canonical
  loop shape.
- Every existing state machine doc links back to the interpretation-table
  pattern.
- No runtime behavior changes.

### M1: Canonical Packet Example

**Goal**: Pick `quota should-run` as the canonical example and make the four
semantic slots visible in docs and smokes.

Steps:

1. Add a public-safe documentation section describing the four slots for
   `quota should-run` (`docs/reference/effect-interpreter-packet.md`).
2. Add a focused pytest or smoke that asserts the mapping from raw inputs to
   the canonical interpretation fields.
3. Keep the existing payload fields unchanged.

Acceptance criteria:

- A reader can trace one real packet from effect request to observation.
- No CLI output budget regression.
- No new runtime contract without a real caller.

### M1.5: Composition Lens

**Goal**: Make the around semantics visible in the canonical packet lens.

Steps:

1. Document the three composition layers and the data-encoded handler in this
   RFC and Lecture 1.
2. Extend `EffectTurn` with `next_effect` so all four semantic slots are
   represented in code, not only in prose.
3. Add a focused test proving a capability gate is a structured around
   decision: it short-circuits, rewrites the next effect, and keeps
   permission semantics visible.
4. Cite the public Tool Calling and Function Composition sources in public
   docs. Never cite internal lecture material.

Acceptance criteria:

- A reader can answer where `next_effect` is encoded for a real packet.
- The code lens covers `effect_request`, `interpretation`, `observation`, and
  `next_effect`.
- No runtime behavior changes.

### M2: Bounded Context Alignment

**Goal**: Align existing refactors with the effect-interpreter boundary.

Steps:

1. Continue splitting `status.py`, `quota.py`, and `goal_frontier.py` into
   read-model, projection, and decision modules.
2. Name the boundaries in terms of the loop:
   - read model = current `A` (state);
   - projection = observation;
   - decision = effect interpreter.
3. Keep re-export compatibility for existing public imports.
4. Do not create a generic effect abstraction until at least two real
   callers need the same envelope.

Acceptance criteria:

- Module names and docstrings make the effect-interpreter role explicit.
- Public import compatibility tests remain green.
- Maintainability and line-budget smokes remain green.

### M3: Focused Test Families

**Goal**: Convert large control-plane smokes into focused pytest modules by
effect family.

Steps:

1. Create focused pytest modules for:
   - work-lane contract;
   - quota decision;
   - scheduler/monitor interpretation;
   - state-machine interpretation tables.
2. Keep thin end-to-end smokes that prove the CLI still works.
3. Add regression tests for failure, cancellation, gate, and observation
   writeback paths.

Acceptance criteria:

- Each effect family has a focused pytest module.
- No large smoke is deleted before its focused replacement passes.
- Full public smoke suite stays green.

### M4: Architecture Documentation

**Goal**: Update architecture and product docs to use the same story.

Steps:

1. Reframe `docs/architecture.md` around the canonical loop.
2. Update the control-plane course so each lecture references the same
   `effect_request -> interpretation -> observation` flow.
3. Update README product language where it currently says "state machine"
   without explaining the interpretation role.

Acceptance criteria:

- The public docs no longer present LoopX as a pile of unrelated state
  machines.
- Technical readers can identify the loop boundary, effect request,
  interpreter, and observation in each documented workflow.

### M5: Steady-State Review

**Goal**: Keep the RFC as a living contract.

Steps:

1. Add a canary smoke or docs smoke that checks the canonical packet
   documentation exists.
2. Review new state machines and packet fields against the four semantic
   slots.
3. Update this RFC when a new effect family requires a new canonical slot.

Acceptance criteria:

- The RFC is referenced by maintainer docs and course material.
- New control-plane features state which effect they interpret.

### M6: General Effect-Program Abstraction

**Goal**: Move from a quota-only read lens to a shared effect-program
abstraction without speculative framework construction.

Steps:

1. Add a second real interpreter, for example `interpret_turn_result_packet`
   or `interpret_status_packet`, with focused tests that prove `EffectTurn`
   is lossless for that family too.
2. Keep packet interpretation as a read-model seam. Extract a shared runtime
   interpreter or executor protocol only when two execution paths need the
   same plan/receipt semantics. Do not add a registry or generic composition
   framework yet.
3. Do not use replan as a generic read-and-ACK precedent. Replan evidence is
   now host-projected context, and an exact runnable-successor Todo or typed
   progress write is the semantic receipt. Keep that transition in the replan
   domain until a second runtime caller needs the same effect identity,
   freshness, atomic state transition, and turn-boundary semantics. If such a
   caller appears, extract the smallest shared observation/transition receipt;
   do not resurrect a manual evidence-read ACK ritual.
4. Add `execution_mode` to `EffectNext` and document
   `serial` / `parallel` / `interleaved` semantics with focused tests.
5. Introduce a data-encoded ordered effect program shape and a real executor
   seam when one owner can execute and settle multiple steps. Qualify turn
   closeout, guided bootstrap, and quota-to-host scheduling before selecting
   the first slice; an existing ordered list does not establish one executable
   authority boundary.
6. Keep failure, cancellation, permission, and budget semantics structured
   across every interpreter. No catch-all wrapper.

Acceptance criteria:

- At least two packet families produce `EffectTurn`.
- Runtime code, not only tests, consumes the shared shape.
- `next_effect` can express an ordered effect program with an explicit
  execution mode.
- A shared observation/transition receipt contract has at least two runtime
  callers; one domain transition alone remains domain-owned.
- No generic `Effect` monad, registry, or middleware framework is added
  without a second runtime caller.

## Test Strategy

Tests should be organized by effect family, not by source-file size:

```text
effect_request -> interpretation -> observation -> next_effect
```

Each focused pytest module should cover:

- positive routing;
- gate and capability decisions;
- failure and cancellation;
- observation writeback;
- compatibility of public imports.

Large smokes remain only as thin end-to-end checks.

### Runtime Replacement Testing

For every runtime replacement:

- focused pytest covers the new seam and parity with the old path;
- a thin public smoke exercises the real CLI or host path;
- CLI output budget regression stays green;
- model-behavior / Doubao shadow qualification covers agent-facing packet
  changes;
- canary premerge includes `core-control-plane` and `canary-runner` profiles.

## Non-Goals

- Do not merge all state machines into one giant enum.
- Do not create a generic `Effect` abstraction without two real callers.
- Do not count test-only lenses as M6 progress; every M6 change must replace a
  real runtime call path.
- Do not mark M6 mostly complete while `quota.py`, `status.py`, or
  `heartbeat_prompt.py` remain oversized or while effect abstraction is
  test-only.
- Do not treat the current `EffectTurn` lens as a general runtime abstraction
  until a second interpreter and a real executor caller exist.
- Do not rewrite `quota should-run` for the sake of naming.
- Do not use effect-runtime generalization as a substitute for final-goal
  acceptance, evidence, or replan.
- Do not make guided bootstrap executable merely because its ordered steps
  can be rendered as `EffectProgram`; preserve model, user, and host ownership
  boundaries.
- Do not align Todo, monitor, and gate families behind a shared protocol
  without proving duplicate transition knowledge and deleting it.
- Do not remove existing public compatibility routes without a migration
  window.

## Risks

- Naming drift: we may use "effect" as decoration without changing semantics.
  Mitigation: every RFC milestone must produce a real doc or test change.
- Over-abstraction: a generic effect envelope could become unused scaffolding.
  Mitigation: only add a shared envelope when a second caller needs it.
- Decorative naming: docs say "effect program" while runtime still only
  passes CLI strings. Mitigation: M6 requires a second interpreter and a real
  runtime replacement before the RFC claims a general abstraction.
- Test churn: converting large smokes too fast can reduce e2e confidence.
  Mitigation: keep thin e2e until focused tests cover the same behavior.
- Goal/effect conflation: a reliable executor can keep executing the wrong
  milestone. Mitigation: keep goal-path evidence and effect settlement as
  separate contracts, and require both at milestone closeout.
- Executor boundary overreach: ordered steps may belong to different actors.
  Mitigation: select the first vertical slice only after its owner and receipt
  boundaries are explicit.

## Open Questions

- Should `effect_interpretation` be a first-class field in the hot quota
  packet, or only a documented lens?
- Should each capability own an interpretation table, or should the tables
  stay in central docs?
- When should a new state machine be considered a new effect family?
- Which packet family should be the second real `EffectTurn` interpreter:
  turn result, status, or monitor poll?
- At what point should `next_effect` stop being a flat CLI tuple and become an
  ordered effect program with `execution_mode`?
- Which candidate removes the most duplicate orchestration with the narrowest
  authority boundary: turn closeout, guided bootstrap, or quota-to-host
  scheduling?
- What stable effect identity and receipt let that path resume after partial
  execution without duplicate ACK, writeback, spend, or external action?
- Which second runtime caller needs the same proven plan/receipt semantics?
- When should `EffectProgram` become runtime-owned rather than host-driven,
  and which steps must remain model-, user-, or host-owned?

## Success Metrics

- A new technical reader can explain LoopX in one paragraph.
- Each major control-plane packet can be traced through the four semantic
  slots.
- Focused pytest coverage grows while large smoke files shrink.
- Public docs and course material use the same loop vocabulary.
- Existing CLI output budgets and public compatibility contracts remain green.
- At least one M7 vertical slice deletes an old command/settlement source and
  passes retry, partial-failure, permission, cancellation, and budget tests.
- Shared runtime protocol code exists only after two real callers use it.

## Conclusion

LoopX harness is not "a set of state machines". It is the effectful program
and effect interpreter around a long-running agent loop. This RFC makes that
story explicit and gives the refactor and test work a stable target.

## References

- 齐梦星空,
  [*主线一：Agent Loop 是 effectful program(1)*](https://www.xiaohongshu.com/discovery/item/6a01d501000000003700c5de?source=webshare&xhsshare=pc_web&xsec_token=ABqpNuladcxhev099wLKw8M3ilhKBua0BQXNpxnBZEGkc=&xsec_source=pc_share).
- 齐梦星空,
  [*主线一：Tool Calling 是 Kleisli arrow(2)*](https://www.xiaohongshu.com/discovery/item/6a02f388000000003502b2d6?source=webshare&xhsshare=pc_web&xsec_token=ABHcIpzpd2RlhAaRr9sZZ-q1OIfRgt7rvG2jn7GUO3tNo=&xsec_source=pc_share).
- 齐梦星空,
  [*主线一：Agent Loop 里的小魔法：函数的组合(3)*](https://www.xiaohongshu.com/discovery/item/6a057524000000003701f6aa?source=webshare&xhsshare=pc_web&xsec_token=AB43lNCJ5ULmfTrGfeTLWd2-jQ6q8nFMGyNAd-tlXJ1uw=&xsec_source=pc_share).
