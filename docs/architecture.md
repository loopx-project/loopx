# Architecture

LoopX has six durable control-plane layers, plus an optional probe surface
whose execution policy requires read-only observation; it is not a peer layer.

1. **Registry**: lists known goals, their repos, adapters, authority sources,
   status, and guards.
2. **Goal state**: the active state file for one goal.
3. **Run log**: JSON and Markdown reports saved per goal.
4. **Run history**: compact indexes consumed by agents, heartbeats, and UI.
5. **Status / attention queue**: first-screen summary of who needs to act next.
6. **Compute quota**: local policy for how much automatic agent compute each
   goal may consume.

**Optional probe surface (not a seventh layer):** goals may register a
project-specific `next_probe` command via bootstrap `--next-probe`. Registration
stores the command as free-form text and does not validate that it is read-only;
heartbeat and operator policy require any executed observation to be read-only.
The `pre-tick-runnable` adapter status is declared separately and is not inferred
from `next_probe`. There is no shipped `pre_tick` module or adapter pre-tick
package, so keep probe registration, execution policy, and adapter status as
separate contracts.

```text
project goal state
  + private registry
  + optional next_probe
        |
        v
shared runtime root
        |
        v
loopx history/check
        |
        v
loopx status
        |
        v
quota-aware agent tick / heartbeat / future UI
```

The core repository intentionally avoids domain logic. A data experiment goal,
a note-maintenance goal, and a harness self-improvement goal should share the
same runtime and contract, but use different adapters.

## Control Plane As Effect Interpreter

The six layers are not only storage surfaces. Together they interpret one
effect request per loop iteration:

```text
model -> effect request -> harness interprets effect -> observation -> model
```

The read model is the current state (`A`): registry, goal state, and run
history. The projection is the observation (`F[B]`): status, attention queue,
and compact run summaries. The decision is the effect interpreter
(`A => F[QuotaDecision]`): quota, interaction contract, capability gates,
work-lane routing, and scheduler hints decide whether and how the next effect
may run. The data-encoded handler is the `next_effect` in the quota packet:
CLI actions, scheduler ACK/failure hints, writeback, and spend.

This is the same lens as the
[Agent Loop Effect Interpreter RFC](architecture/rfcs/agent-loop-effect-interpreter-v0.md)
and
[Harness Is the Effectful Program](development/control-plane-course/01-agent-loop-effectful-program.md).
The public framing comes from 齐梦星空,
[主线一：Agent Loop 是 effectful program(1)](https://www.xiaohongshu.com/discovery/item/6a01d501000000003700c5de?source=webshare&xhsshare=pc_web&xsec_token=ABqpNuladcxhev099wLKw8M3ilhKBua0BQXNpxnBZEGkc=&xsec_source=pc_share).

## Guided Autonomy, Not Recommendation Lock-In

LoopX separates **hard boundaries** from **steering guidance**. Claims, scope,
capability readiness, gates, quota, public/private policy, and destructive or
production-effect rules define what an agent may do. Priorities,
recommendations, bounded suggestions, and `next_cli_actions` help the agent
choose efficiently inside that legal set; they do not silently shrink it.

Projections therefore optimize for readability without pretending to be
exhaustive. An agent may choose a currently authoritative eligible action that was
not displayed among the first suggestions; a typed discovery route should expose
the authoritative queue when the bounded view is insufficient. The agent must
bring that choice back
through the same typed preflight, receipt binding, validation, writeback, and
spend path. This preserves process guidance and auditability while keeping the
agent autonomous where no real boundary requires a single route.

A field that is advisory must say so in its schema. If a controller intends a
whitelist, exclusive lease, owner gate, or other machine-enforced restriction,
that restriction belongs in a typed authority or transition contract rather
than being inferred from list position or prompt wording.

## Turn Decision Vocabulary

Operator-facing docs and heartbeat prompts often summarize a turn as deliver,
wait, ask, replan, repair, or stay quiet. That shorthand describes interaction
intent; it is not the typed packet vocabulary carried by the Turn contracts.

The executable vocabulary lives with its owning contracts:

| Contract | When | Authoritative definition |
| --- | --- | --- |
| `LoopXTurnRoute` | Before host execution | [`driver.py`](../loopx/control_plane/turn_driver/driver.py), enum `LoopXTurnRoute` |
| `TurnResultKind` | After host execution | [`settlement.ts`](../loopx/control_plane/turn_driver/settlement.ts), `TURN_RESULT_KINDS`; Python adapter: [`transaction.py`](../loopx/control_plane/turn_driver/transaction.py), enum `LoopXTurnResultKind` |

Read the complete member lists in those definitions; this page explains their
meaning without maintaining another exhaustive enumeration.

- Prose "deliver" splits into `validated_progress` versus `validated_completion`.
- Execution failures are first-class typed results. In particular,
  `terminal_closeout_failed` means terminal closeout failed after durable
  writeback and quota spend. Recovery retries the same Turn's closeout without
  repeating those committed effects; it must not treat the failure as unspent.
  The [Turn executor recovery tests](../tests/test_loopx_turn_executor.py)
  exercise this boundary.
- "Stay quiet" is notification / monitor behavior (for example
  `monitor_quiet_skip` or heartbeat `DONT_NOTIFY`), not a member of either enum.

Quota `interaction_contract` and heartbeat guidance may still use the operator
shorthand. Turn adapters use the executable definitions linked above.

## Runtime Responsibility Model

The six durable layers above describe control-plane surfaces. They do not
describe who performs each step of a turn. For runtime ownership, use four
responsibilities:

| Responsibility | Owns | Must not own |
| --- | --- | --- |
| **Agent** | Planning, analysis, tool use, and one bounded execution through a host/runtime | Durable goal lifecycle or unscoped effect authority |
| **Provider** | External calls and bounded observations, effect results, and readback | Domain transition policy or LoopX todo state |
| **Capability** | The caller-facing outcome contract, domain policy, observation normalization, validation, and typed transition proposals | Durable scheduling, claims, gates, or direct lifecycle writes |
| **LoopX Kernel** | Goal, todo, claim, gate, monitor, quota, accepted writeback, recovery, and scheduling | Domain-specific reasoning or provider implementation details |

The request and result paths therefore run in opposite directions:

```text
Agent -> Capability -> Provider -> external system
external observation / effect readback -> Provider -> Capability
typed transition proposal -> LoopX Kernel -> next todo / gate / monitor / turn
```

An observation is not a transition, and a provider receipt is not accepted
progress until the capability validates it and the Kernel commits the resulting
state change. Domain state, evidence, and receipts are artifacts exchanged
between these responsibilities, not additional runtime owners.

The host/runtime carries the Agent's session, tools, and invocation. It is a
replaceable execution boundary, not a fifth domain decision owner.

An **extension** is a separate delivery and lifecycle axis. It may install an
optional provider, while a built-in capability may use a core provider. The
extension does not become a fifth runtime responsibility and does not acquire
Kernel authority. This boundary is represented directly by
`CapabilityRegistry`, which registers providers, capability contracts, and
their implementations separately.

### Agent-Native Kanban Is A Projection

LoopX state can be rendered as an agent-native Kanban: todos are cards, logical
lanes are derived views, and card moves are validated transitions. The metaphor
does not introduce another state owner. Canonical todo/event/state contracts
remain authoritative; dashboards and collaboration boards consume public-safe
projections.

Capabilities may project domain lanes such as Issue Fix
`feasibility -> patch -> checks -> review -> merge` without adding those labels
to the Kernel lifecycle. Providers supply the external facts behind the lane,
capabilities validate them and propose typed transitions, and the Kernel owns
claim, gate, monitor, quota, writeback, recovery, and terminal closure. See the
[concept primer](development/control-plane-course/00-concept-primer.md) and
[state substrate lecture](development/control-plane-course/04-state-substrate.md).

## Current Dependency Budget

The executable boundary policy lives in
[`test_control_plane_import_boundaries.py`](../tests/architecture/test_control_plane_import_boundaries.py):

- `test_control_plane_has_no_unreviewed_outward_dependencies` rejects
  control-plane imports of presentation, CLI, capability, or benchmark-adapter
  layers unless the existing maintainability policy records the exact edge,
  its review reason, and its retirement plan.
- `test_status_has_no_forbidden_outward_dependencies` rejects status imports of
  benchmark-adapter or presentation layers.
- `test_quota_markdown_is_owned_by_the_presentation_layer` protects the renderer
  ownership and CLI composition boundary.

The control-plane check shares
[`REVIEWED_MAINTAINABILITY_EXCEPTIONS`](../loopx/canary/maintainability_ratchet.py)
with the maintainability ratchet. Its retained legacy Todo writer bridge keeps
the existing Explore completion-evidence guard until that writer retires.
Exceptions match complete source/target identities; neighboring modules and
targets remain rejected, and a retired edge makes its exception stale. This
replaces the former unconditional rejection that contradicted the already
reviewed migration debt. It adds no exception or import to the runtime.

The status and quota-Markdown checks remain zero-exception: their former
verifier-bootstrap and renderer edges have been removed. Run the linked
architecture test file for current violations and the ratchet for reviewed
debt, invalid or stale exceptions, and growth beyond unchanged ceilings.

Adapter-specific enrichment belongs behind application/plugin composition
rather than in the status core. Move an edge only after characterization
parity exists. Hiding an adapter dependency inside a function or dynamic import
does not count as architectural separation; the current AST checks inspect
static imports, including function-local imports, but do not prove the absence
of dynamic imports.

### Status And Quota Facades

`loopx.status` and `loopx.quota` remain compatibility entry points for their
facade-owned behavior. Imported implementations belong to their canonical
bounded modules; the only compatibility-only re-exports are the keys in each
facade's `_PUBLIC_COMPAT_REEXPORTS` map, whose values name the canonical owner.

New repository code must import the canonical owner. A compatibility-only
entry may remain while a public example, regression, or documented import
contract consumes it. Zero-consumer, undocumented entries are removed instead
of becoming permanent accidental API. Removing a retained entry requires an
explicit migration or deprecation step, and status/quota CLI JSON parity must
stay characterized independently of the compatibility binding.

Capabilities and extensions are also orthogonal: a capability is a product
contract, while an extension is an independently managed delivery unit that
may install providers for one or more capabilities. The provider-aware
registration and manifest boundary is documented in
[extensions.md](reference/extensions.md).

Operator-inbox urgency is one such inward contract. Quota consumes the
content-free `operator_inbox_urgency_v0` read model from the control plane; it
does not import the Lark provider. The existing Lark config, event files,
capability key, lane names, and CLI remain compatibility surfaces while the
provider delegates urgency projection to that contract. Provider files should
move only after this read-model and work-lane parity remains characterized.

LoopX should still absorb field-tested project-control mechanisms such
as authority registries, current-belief TODOs, managed external-source
manifests, experiment boards, validation surface maps, and gated handoff
packets. See [field-derived-patterns.md](concepts/field-derived-patterns.md).

LoopX should also expose a human-friendly frontstage without moving the
source of truth into chat. A goal can project as a channel, agents can project
as workspace members, and task ownership can project as explicit leases; the
registry, active state, run history, quota, gates, and lease events remain the
backstage ledger. See
[frontstage-channel-lease-roadmap.md](product/roadmaps/frontstage-channel-lease-roadmap.md).

LoopX should also grow a narrow host-integration surface. CLI commands
remain the compatibility baseline, but long-running agent hosts benefit when
the same state is available through hook/MCP/server adapters:

- hook activation should only route the host toward the current LoopX
  contract; it must not embed a second scheduler or stale project policy;
- MCP/server tools should expose lifecycle reads, todo/gate/lease writes, and
  compact status projections without requiring the host to parse Markdown;
- host adapters should isolate platform details while preserving the same
  registry, event-ledger, quota, public/private boundary, and lease semantics;
- task graphs should be optional projections over LoopX state, not a
  replacement for the event ledger or active goal truth.

This keeps LoopX portable across Codex, local CLI loops, dashboards, and
future agent hosts while avoiding a forked control plane per host.
The v0 protocol contract is
[`host-integration-surface-v0`](reference/protocols/host-integration-surface-v0.md):
hook activation stays thin, lifecycle reads and todo/gate/lease writes map to
CLI-equivalent operations, compact status projections exclude raw/private
material, optional derived projections remain read-only, and CLI fallback
remains available when an adapter is absent.

## Lifetime Goal Invariant

LoopX should optimize for **lifetime goals**: durable intentions that
may outlive a single thread, executor, project phase, or plan. This is a
product invariant, not an additional storage layer.

A lifetime goal must be stable enough that a future human or agent can recover
what the goal is, what currently defines it, who may change it, and what the
next safe transition is. It must also stay narrow enough that automation can
make one bounded, verifiable move instead of claiming open-ended authority.

The architecture maps that invariant onto the existing layers:

- the registry gives the lifetime goal a stable identity, repo boundary,
  adapter status, guards, and authority-source list;
- active goal state records the current belief, priority stack, non-goals, and
  next action without becoming a complete diary;
- authority sources replace implicit model memory with reviewable context and
  conflict rules;
- run history preserves the compact evidence trail across sessions and agents;
- todos turn the lifetime goal into bounded user and agent obligations;
- gates, reward, and quota keep human judgment, course correction, and compute
  spend attached to concrete transitions.

The result should preserve continuity without claiming open-ended autonomy: a
goal can live for years, but every agent turn still has to pass through current
authority, boundary, quota, validation, and writeback before it can count as
progress.

For session-runtime platforms that already own agent definitions, session
events, tool execution, permissions, billing, and product frontstage, Goal
Harness should integrate as the goal-level control projection rather than as a
second runtime. The read-only adapter path is: ingest compact session, event,
approval, outcome, and artifact summaries; produce `goal_state`,
`run_projection`, `operator_gate`, `human_reward`, `work_lane_contract`,
`quota_decision`, `handoff_packet`, and `dreaming_proposal` projections; then
let the product surface display those projections. See
[session-runtime-control-plane-adapter.md](integrations/session-runtime-control-plane-adapter.md).

## Local Server / Daemon Roadmap

The CLI remains the compatibility baseline. A future local server should be an
optional control-plane coordinator over the same registry, active state, run
history, quota, todo, and boundary contracts, not a replacement state machine.

In the current control plane, a **goal** is the stable `goal_id` boundary: one
registry entry, active-state file, quota lane, run-history stream, and status
projection. A **todo** is a structured active-state checkbox inside that goal,
addressed by `todo_id` and projected as an agent or user work item. There is no
separate issue object in the LoopX runtime model.

The server path should land in layers:

1. **Writer correctness before a server**: make existing CLI writers safe under
   concurrency with per-goal locks, idempotency keys, and optimistic revision
   checks. `todo`, `refresh-state`, reward writeback, quota spend, and history
   append paths should fail closed on stale revision or overlapping write scope.
2. **Lease adoption**: the optional local `task_lease_v0` CLI already provides
   owner, TTL, write scope, idempotency, conflict, transfer, and release
   semantics. Released generations remain as inactive per-todo tombstones, so
   a later acquire advances both the CAS version and authority-owned
   `lease_epoch`; terminal writeback uses the returned key/version pair. Keep
   `claimed_by` as the default soft route and adopt hard leases
   only for hosts with a demonstrated concurrent-write problem.
   The pending/lease key should be per todo: `(goal_id, todo_id)` is the
   contention unit, not the whole goal or project. Different todos under the
   same goal may proceed in parallel when their write scopes and gates allow
   it; competing claims on the same todo fail closed or renew.
   Status currently exposes capability availability; quota does not enforce or
   consume hard leases. A later host integration may project active lease rows
   after its adoption contract and fallback behavior are validated.
3. **Loopback coordinator**: extend the existing local status server into a
   loopback-only coordinator that can centralize per-goal locks, leases, quota
   decisions, compact status projection, and heartbeat scheduling. It must bind
   locally, keep raw/private evidence out of compact responses, and preserve
   CLI fallbacks for every write.
4. **Heartbeat scheduler**: move recurring heartbeat bookkeeping behind the
   coordinator only after quota/spend idempotency is proven. Scheduler output
   should consume the structured `quota should-run` / `interaction_contract`
   output, with `scheduler_hint` owning cadence; historical
   `protocol_action_packet` summaries are compatibility observations only.
5. **Planning and dreaming queues**: let background planning produce ranked
   todo proposals, evidence probes, and refactor warnings as advisory records.
   These queues must not execute protected work, read private material, or
   spend delivery quota without a later normal `quota should-run` decision and
   goal-boundary approval. The compact contract is
   `server_managed_planning_contract_v0`; see
   [dreaming-exploration-lane.md](product/roadmaps/dreaming-exploration-lane.md).
6. **Host adapters**: expose the same contracts through MCP, hooks, or a small
   local HTTP API for Codex-like hosts. Host adapters should route agents to
   current state and valid writes; they should not embed stale project policy or
   create a second scheduler.

Acceptance criteria for the first server-backed milestone:

- the same action can be completed through CLI-only mode after the daemon is
  stopped;
- a duplicate heartbeat, duplicate quota spend, or stale todo update becomes an
  explicit no-op or conflict, not a second delivery event;
- status shows the active lease and current owner without making the lease the
  source of project truth;
- all compact server responses pass the public/private boundary scan;
- tests cover one concurrent writer conflict and one daemon-down fallback.

Before the server-backed lease exists, LoopX keeps a lighter shared-control-plane
contract: todo metadata may include `claimed_by`, written by the todo CLI under
the active-state file lock. That field is a soft owner for visibility only, and
the CLI accepts it only when the id is registered in
`coordination.registered_agents`. Registered identities are peers. Work authority
comes from explicit claims, task leases, goal/write boundaries, and typed
continuation policy rather than a durable leader role. Any peer doing repository
work follows the same workspace-isolation rule when the selected task writes;
repository maintainer policy determines whether it may self-merge. A future
server lease should stay per todo and add TTL, idempotency keys, stale-claim
detection, overlap warnings, and compare-and-swap conflict responses.

## State Interaction Model

LoopX has four product actors:

- the **goal**, which owns durable objective, state, guards, run history, and
  reward overlays;
- the **Codex App executor**, which performs bounded transitions but should not
  be the long-term source of truth;
- the **user**, who supplies operator intent, approval, and high-quality reward
  signals;
- the **dashboard**, which visualizes derived status and should remain
  read-mostly unless an explicit local write boundary is enabled.

This actor model is the design gate for future commands and dashboard work. A
new capability should name the state it reads, the state it writes, the owner
of that write, and how the dashboard proves the transition happened.

See [state-interaction-model.md](state-interaction-model.md).

## Peer Task Coordination

For parallel work, every registered LoopX agent has equal identity authority.
Each peer owns only the work it has claimed or leased, within the current goal
boundary. A peer may:

- inspect or claim an eligible todo;
- advance one bounded implementation, validation, monitor, or repair slice;
- create an ordinary independent successor, optionally with executor exclusions;
- write back evidence for its own accepted task outcome.

When bounded orchestration is enabled, LoopX deterministically selects a
temporary coordinator for one task bundle. That coordinator may activate or
resume eligible peer lanes and aggregate accepted bundle evidence. It does not
become a durable leader and gains no implicit review, merge, publication, or
replan authority over other identities.

LoopX does not replace the operating-system scheduler or Codex App
executor. It should, however, own the simple compute quota that those executors
read before running more work. Timer cadence is an execution mechanism, not the
product source of truth for project priority.

See [quota-allocation.md](quota-allocation.md).

See [peer-agent-runtime-v1.md](reference/protocols/peer-agent-runtime-v1.md).

## Status / Attention Queue

The status layer derives a compact queue from registry, run history, and
contract health. It should be the first thing a controller or future UI reads:

- contract failures block adapter work,
- goals waiting on user/controller opt-in are surfaced explicitly,
- goals ready for Codex work are separated from external evidence watches,
- already-connected read-only goals with valid runs do not keep demanding
  redundant review.

See [attention-queue.md](operations/attention-queue.md).

The JSON export is the boundary for dashboards, heartbeat summaries, and future
UI work. See [status-data-contract.md](status-data-contract.md). The product
dashboard frontend should follow
[dashboard-frontend-selection.md](product/roadmaps/dashboard-frontend-selection.md); the
single-file HTML renderer remains a fallback for smoke tests and offline
inspection.
