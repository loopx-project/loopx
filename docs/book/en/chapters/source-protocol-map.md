# Developer contribution map and protocol entrypoints

Contributing to LoopX does not only mean changing the Kernel, and it does not only mean building an
Extension. External developers can improve control-plane rules, Capabilities and Domain State, Providers,
Hosts and Runners, projections and dashboards, documentation and fixtures, or independently distributed
packages. The first decision is not a directory. It is the outcome you intend to ship and the contract
that owns it.

The easiest wrong way to read LoopX source is to open the largest Python module and follow function calls
until the behavior feels familiar. That reveals implementation, but not why the behavior exists or which
consumers must remain compatible after a change.

External contributors need a more durable route:

```text
developer job
  -> contribution outcome and placement
  -> protocol family
  -> invariant owned by that protocol
  -> bounded context
  -> current implementation and validation
```

This chapter is a protocol-first source map. It is not a complete API catalog, and it does not ask you to
memorize current function names. Its job is to help you identify the contract your Issue or PR will change.

## What you should learn

After this chapter, you should be able to:

- decide whether a contribution belongs to the Control Plane, a Capability, a Provider, a Host or Runner,
  a projection or documentation surface, or Extension lifecycle;
- record the capability id, provider id, and built-in or extension-delivered placement;
- place protocol work in the state, work-graph, Turn/Host, or evidence family;
- distinguish a canonical contract, read model, Host adapter, and renderer;
- choose a bounded context by change reason instead of filename;
- turn a public contributor task or Issue into a reviewable slice;
- describe a change with protocols, invariants, and evidence rather than a function inventory.

## Write a protocol card before reading code

Start with a short card:

```text
Reader-visible problem:
Current protocol:
Source of truth:
Invariant at risk:
Allowed transition:
Forbidden outcome:
Expected receipt:
Validation surface:
```

Suppose a nonblocking user notice incorrectly grants publication authority:

```text
Current protocol: decision_scope_v0
Source of truth: typed Gate and Todo requirements
Invariant at risk: a notice cannot grant authority
Allowed transition: a matching approved Gate consumes only covered scope
Forbidden outcome: an unrelated or nonblocking notice unblocks publication
Expected receipt: linked decision and lifecycle event
Validation surface: decision table plus quota integration smoke
```

This card is more useful than “I will modify `quota.py`.” Files can move. The contract and forbidden
outcome remain reviewable.

## Choose the contribution outcome and placement first

Before implementation, record four placement facts:

```text
Capability id:
Provider id:
Delivery: built-in | extension-delivered | standalone package
Why the nearest existing owner is or is not sufficient:
```

Then choose the contribution surface from the caller-visible outcome:

| Surface | Contract it owns | Typical delivery | Must not acquire incidentally |
| --- | --- | --- | --- |
| Kernel / Control Plane | Generic Goal, Todo, Gate, quota, scheduler, and lifecycle invariants | Typed transition, decision rule, recovery repair | All state for one business domain |
| Capability / Domain State | Caller-facing outcome, domain policy, and result lifecycle | Domain command, typed result, admission or read model | Provider credentials or a duplicate control plane |
| Provider / external system | Bounded request, external call, observation, effect, and readback | Built-in or extension-delivered implementation | Goal authority, completion judgment, or replacement service authentication |
| Host / Runner / Session Runtime | Typed execution, visibility, resume handles, and Host-owned effects | Host adapter, Runner, scheduler-owner integration | LoopX canonical state or self-validated completion |
| Projection / Dashboard / Docs / fixtures | Reader-facing models, explanation, and public-safe evidence | CLI renderer, dashboard, protocol documentation, synthetic fixture | Browser write authority or another state machine |
| Extension / package lifecycle | Independent install, activation, doctor, upgrade, rollback, and compatibility | Standalone package or a Capability Provider delivery unit | Capability domain policy or automatic authority |

These surfaces can compose without collapsing into a generic “plugin”:

- define a Capability and Domain State when you introduce a stable caller result, then choose a core or
  Extension-delivered Provider;
- preserve an existing Capability when only the external service implementation changes, and add a
  Provider with the appropriate lifecycle;
- build an operator dashboard from public-safe projections rather than parsing private project files or
  inventing a browser write path;
- keep Host continuation on the existing quota, scheduler, and Turn contracts instead of adding another
  scheduler inside a Runner;
- use a standalone Extension for a deterministic, zero-permission command without inventing a fake
  Capability.

A new module, CLI option, or schema needs a real caller, active call site, or explicit compatibility
contract. Keep hypothetical Providers, Runners, and projections in design or Todo state until the shipped
path exists.

## Five core protocol families

The protocol directory grows with the product. External contributors do not need to read it alphabetically.
Choose a family from the job you are doing.

### 1. State and projection

This family answers:

> Where does a fact live, who may write it, and how is it reconstructed for readers?

Start with:

- [`event_sourced_state_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/event-sourced-state-contract-v0.md)
  for append-only events, replay, idempotency, and privacy partitioning;
- [`active_state_structured_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/active-state-structured-projection-v0.md)
  for the typed read-only projection over the active-state workbench;
- [`task_graph_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)
  for read-only Todo, Gate, dependency, validation, and handoff relations;
- [`local_state_write_correctness_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/local-state-write-correctness-v0.md)
  for revisions, locks, idempotency keys, conflicts, and durable local writes.

Typical jobs:

- status and the event ledger disagree;
- the active-state parser drops a field;
- the task graph loses lineage or truncation diagnostics;
- retry duplicates a lifecycle effect;
- a dashboard needs another field.

The dashboard example begins with “which source owns this field?” It does not begin with a new editable UI
state.

### 2. Work graph, authority, and peers

This family answers:

> Who may perform which work now, what blocks it, and who continues afterward?

Start with:

- [`decision_scope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/decision-scope-v0.md)
  for Gate kind, granularity, coverage, and fail-closed behavior;
- [`goal_vision_replan_contract_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/goal-vision-replan-contract-v0.md)
  for per-Agent Vision, checkpoints, replan, and bounded routing;
- [`peer_agent_runtime_v1`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/peer-agent-runtime-v1.md)
  for equal peer identity, claims, and continuation;
- [`host_integration_surface_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/host-integration-surface-v0.md)
  for Host capabilities, controlled writes, and CLI-equivalent fallback.

Typical jobs:

- one Gate freezes every Agent;
- a claim is treated as a lock or global authority;
- a handoff completes without a successor;
- monitor and advancement precedence is wrong;
- a Host can execute an action but lacks the required decision scope.

Review authority first and implementation branches second. Words such as `approved`, `owner`, or
`waiting for user` do not replace a typed scope relation.

### 3. Quota, interaction, and scheduling

This family answers:

> How are complex facts compiled into user, Agent, and CLI responsibilities for this turn?

Start with:

- [`turn_envelope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/turn-envelope-v0.md)
  for a bounded next-action read model over an already computed quota decision;
- [`protocol_action_packet_decision_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/protocol-action-packet-decision-v0.md)
  for action-packet decision semantics;
- the [Status Data Contract](https://github.com/huangruiteng/loopx/blob/main/docs/status-data-contract.md)
  for status, attention, and operator-facing boundaries;
- [State Machines](https://github.com/huangruiteng/loopx/blob/main/docs/product/core-control-plane/state-machine.md)
  for composition among Todo, Gate, quota, evidence, and scheduler state.

The core contract is not one `should_run` boolean:

```text
source facts
  -> normalized projections
  -> ordered policy
  -> interaction_contract
  -> scheduler_hint
```

A user Gate can require a user response while the Agent channel still requires independent safe work.
Collapsing both channels into a boolean damages interaction and scheduling at the same time.

### 4. Bounded Turn and Host effects

This family answers:

> How is one external action proposed, executed, independently validated, and written back?

Start with:

- [`loopx_turn_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/loopx-turn-v0.md)
  for the experimental decide, execute, validate, writeback, and spend transaction;
- [`session_runtime_loopx_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/session-runtime-loopx-projection-v0.md)
  for the read-only first-screen projection from an external runtime into LoopX;
- [`session_runtime_controlled_writeback_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/session-runtime-controlled-writeback-v0.md)
  for the draft boundary around controlled session-runtime metadata writeback;
- [`host_integration_surface_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/host-integration-surface-v0.md)
  for typed Host requests, results, capabilities, and fallback;
- [`rollback_packet_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/rollback-packet-v0.md)
  for compensation, rollback, and evidence lineage.

Keep three responsibilities separate:

| Responsibility | Owner | Must not be replaced by |
| --- | --- | --- |
| Select the current action | LoopX control plane | Host inference from status prose |
| Execute a bounded effect | Host adapter | LoopX pretending an external action occurred |
| Judge the postcondition | Independent validator | The Host's natural-language success claim |

A session handle, raw stdout, or transcript can help Host recovery. It cannot become Goal authority or
completion proof.

### 5. Evidence, recovery, and quality

This family answers:

> How do we prove a rule, recover from failure, and bind receipts to the current revision?

Start with:

- [`model_behavior_qualification_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/model-behavior-qualification-v0.md)
  for cases where real-model behavior adds signal beyond deterministic checks;
- [Benchmark research RFC](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md)
  for matched research arms and independent outcome evidence;
- [Testing and Quality](https://github.com/huangruiteng/loopx/blob/main/docs/development/testing-and-quality.md)
  for unit, contract, smoke, decision replay, canary, and release Gates;
- [Public/Private Boundary](https://github.com/huangruiteng/loopx/blob/main/docs/public-private-boundary.md)
  for evidence that may enter the public repository.

Quality is not a final testing appendix. The protocol card's forbidden outcomes and expected receipt should
determine the validation design before implementation.

## Map a protocol family to a bounded context

Protocols define cross-module contracts. Bounded contexts identify which change reason owns implementation:

| Context | Primary responsibility |
| --- | --- |
| `goals` | Goal state, Vision, Goal-level planning, and frontier |
| `todos` | Todo lifecycle, scope, resume, monitor, and handoff summaries |
| `agents` | Agent identity, Agent-scoped routing, and capability |
| `quota` | Compile projected facts into the current interaction decision |
| `scheduler` | Cadence, backoff, reset, and acknowledgement |
| `runtime` | Turn/session projection and bounded execution state |
| `handoff` | Cross-runtime handoff, review packets, and owner routes |
| `work_items` | Attention, selection, and operator-facing work read models |

On the `v0.5.4` migration baseline, bounded context and implementation language are separate dimensions.
Complete transactions under `goals`, `todos`, `quota`, `scheduler`, `work_items`, and `turn_driver` now
have TypeScript semantic owners. Examples include Vision refresh, the local task-lease lifecycle, quota
spend/void/monitor-poll commit, and receipt-bound scheduler follow-up. Adjacent Python facade modules may
serve only as CLI transport, legacy projection, an explicit external Provider or Host effect, or
writeback that has not migrated. Read the shipped baseline in the
[TypeScript Control-Plane Migration RFC](/loopx/docs/architecture/rfcs/typescript-control-plane-migration-v0/)
and trace the active request handler and caller before assigning ownership. A Python entrypoint does not
prove that Python still owns the decision.

Ask:

```text
What reason would cause this rule to change?
```

Do not ask:

```text
Which current file already reads a similar field?
```

For example:

- Gate coverage belongs to the authority and Todo contract;
- rendering an arbitrated result belongs to a projection or renderer;
- wake-up timing belongs to the scheduler;
- applying one effect belongs to a Host adapter;
- deciding acceptance belongs to a validator.

One PR may touch several contexts, but every change should serve one coherent protocol chain.

## Map the contribution surface to a repository owner

Treat repository routes as owners, not as automatic placement from a directory name:

| Outcome | Look here first |
| --- | --- |
| Generic control-plane rule | `loopx/control_plane/<bounded-context>/` and the owning protocol or decision table |
| Existing Capability result or Domain State | `loopx/capabilities/<capability>/` |
| Capability and Provider registration | `loopx/capabilities/registry.py`, catalogs, and manifest contracts |
| Generic Extension manifest, readiness, and runtime | `loopx/extensions/` |
| Independently installed package | `packages/<package-id>/` or a separate repository |
| Host or Runner integration | Runtime connector, Turn and Host contracts, and the matching adapter |
| Operator projection | Status, frontstage, or projection owner; a renderer only consumes the typed model |
| Documentation and validation | Owning protocol document, `tests/`, `examples/`, or a public-safe fixture |

Current capability packages own their documentation and register through `catalog_entry.py`. Before
contributing, run `loopx capability list --format json` and
`loopx capability show <capability-id> --format json` to confirm that the capability is registered and to
read its entry commands, Provider boundary, and durable validation. A directory or README alone does not
prove that a capability is shipped.

Code under `loopx/capabilities/<name>/` is not automatically a public Capability; it still needs explicit
registration and a real caller contract. `loopx/extensions/` is not a bucket for every external
integration; it owns generic Extension lifecycle, while an independently versioned Provider belongs in its
delivery package. Keep private helpers with the nearest owner instead of promoting them to a Capability or
Extension merely because they span several files.

## Function names are search anchors, not the curriculum

The official maintainer course and source provide current implementation anchors. Use them with three rules:

1. Read the protocol and decision table before searching for an implementation anchor.
2. Confirm that the symbol still owns the same input, output, and invariant.
3. Cite the contract and invariant in a PR; use symbol names only to help reviewers navigate the diff.

In the current source, you may begin from a quota-decision builder, Turn driver, or task-graph builder.
Those anchors can move into better bounded contexts. Your mental model should survive the move.

If a document needs twenty function names to explain one behavior, it is probably copying implementation
instead of teaching the protocol.

## Choose a public contribution entrypoint

Do not infer public work from maintainer-local state. Use public surfaces:

1. Read
   the [Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md).
2. Choose a `Starter`, `Focused`, or already-agreed design task.
3. Read the protocols and validation named by that task.
4. State the smallest intended slice in the linked Issue.
5. Wait for maintainer direction before a large or behavior-changing implementation.
6. Deliver one independently reviewable and reversible protocol result on a clean branch.

Do not create public tasks from:

- `.loopx/`, `.loopx/goals/`, or live active state;
- private benchmark traces, raw Agent sessions, or verifier output;
- internal documents, production credentials, or machine paths;
- speculative duplication of `Maintainer-owned` live runs.

Public contributions build context from public-safe protocols, Issues, and fixtures.

Contributions do not have to change runtime code. Public tasks can also deliver:

- protocol documentation, migration notes, and contributor walkthroughs;
- deterministic decision tables, negative tests, and public-safe replay fixtures;
- read-only dashboards, accessibility improvements, and operator explanations;
- fake-Host, fake-Provider, and no-sink integration examples;
- Extension scaffolds, manifest compatibility, and lifecycle smokes.

For every artifact, state the reader-visible result, the authority that maintains the fact, and the event
that makes the document, fixture, or compatibility claim stale.

### How a community signal becomes bounded work

This section and its Chinese counterpart are semantic mirrors. A material difference in cases, status,
conclusions, or link targets is a documentation defect.

<!-- community-casebook:signal-to-bounded-work:start -->
<!-- community-casebook:question-before-fix -->

**A question can be a contribution.** In
[“How do I give a task a stopping point?”](https://github.com/huangruiteng/loopx/discussions/3069),
a user reported that a completed task kept spinning. Before declaring a product defect, the community
separated the report into four testable hypotheses: Goal acceptance, terminal closure, quota budget, and
monitor cadence. Useful Q&A turns an imprecise experience into a minimal diagnostic path instead of
guessing a code location.

<!-- community-casebook:user-idea-to-contract -->

**Map a methodology suggestion to existing contracts first.**
[“Look back and retain”](https://github.com/huangruiteng/loopx/issues/2353)
started from long-term user experience: an Agent should explain why its route changed and which earlier
work remains valid. The discussion did not immediately create a second memory system. It first compared
the idea with the evidence log, Vision acceptance, and `goal_path_delta_v0`, then identified the remaining
semantic gap. An Issue like this can improve product direction without shipping code.

<!-- community-casebook:claim-before-code -->

**Narrow authority before claiming implementation.** The
[Pi `task_lease_v0` task](https://github.com/huangruiteng/loopx/issues/3549)
records the existing capability owner, Host facade, in-scope work, non-goals, target base branch, and
validation commands before implementation. It does not turn “Pi needs lease operations” into a new
scheduler, storage system, or automatic lease lifecycle.

These records are learning examples, not copies of current task state. Before participating, reopen the
Issue to confirm that it has not been closed, redirected, or claimed, and use the
[Contributor Task Board](https://github.com/huangruiteng/loopx/blob/main/docs/development/contributor-tasks.md)
as the current public entrypoint. For `Maintainer-owned` work, ask for an independent helper slice instead
of reproducing the active implementation in parallel.
<!-- community-casebook:signal-to-bounded-work:end -->

### RFC Review Lab: establish status before implementation

This section and its Chinese counterpart are semantic mirrors.

<!-- community-casebook:rfc-review-lab:start -->
<!-- community-casebook:rfc-status -->

Start with the
[RFC Index](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/README.md)
and read the formal status. `Accepted`, `Active research`, `Draft`, and `Draft integration proposal`
permit different actions. The existence of an RFC or Discussion does not prove that an implementation is
shipped, and it does not automatically create claimable work.

<!-- community-casebook:rfc-community-proposal -->

For example, community Discussion
[#3157](https://github.com/huangruiteng/loopx/discussions/3157)
proposes an event-driven control plane and unified policy decision. Do not begin review by asking whether
the proposed directory tree looks clean. Ask:

1. Where is the canonical authority and owner for quota, scheduler, event-store, and worker semantics?
2. Does the proposal compose existing rules or create a second decision authority?
3. Does it mistake an event store for an event bus with delivery semantics?
4. Which stage first changes default behavior, and does it have an independent migration gate?
5. What is the smallest verifiable slice, and how does failure return to the current path?

<!-- community-casebook:rfc-output -->

A useful RFC review returns an explicit disposition such as `accept`, `revise`, `require_evidence`, or
`defer`, plus an owner, next artifact, and review condition. Cross-direction questions may enter an
[Open Strategy Review](https://github.com/huangruiteng/loopx/blob/main/docs/community/open-strategy-reviews.md).
A meeting does not replace a versioned RFC, bounded Issue, PR review, or maintainer authority.
<!-- community-casebook:rfc-review-lab:end -->

## Decide whether the slice is right-sized

A coherent slice can usually be described as one protocol result:

> Make `decision_scope_v0` select typed repair when scope relations are missing instead of treating the Gate
> as global authority.

This is usually too broad:

> Refactor status, quota, scheduler, and every test.

Right-sizing is not only reducing line count. Preserve one complete causal chain:

```text
source
  -> invariant
  -> decision
  -> projection or effect
  -> receipt
  -> validation
```

Do not submit only a helper in the middle of the chain. Do not add speculative enums, CLI flags, or adapters
without a real call site.

## Checklist

Before entering the source, confirm:

- [ ] Which contribution surface and caller outcome does this work serve?
- [ ] What are the capability id, provider id, and delivery placement when they apply?
- [ ] Which protocol family owns the problem?
- [ ] What are the canonical source and primary writer?
- [ ] Which invariant is at risk?
- [ ] What are the legal and forbidden transitions?
- [ ] Which bounded context owns the change reason?
- [ ] Which public fixture or smoke proves the shipped path?
- [ ] Is the task public and claimable rather than maintainer-owned live work?
- [ ] Can the PR be described as one complete protocol result?

The next chapter traces one scoped-Gate scenario through source, projection, decision, Turn, receipt, and
fresh replay.
