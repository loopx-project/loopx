# RFC: Agent Loop Effect Interpreter

| Field | Value |
|---|---|
| Status | Accepted |
| Supersedes / closes | none |
| Date | 2026-08-08 |
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

### What Is Missing

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
