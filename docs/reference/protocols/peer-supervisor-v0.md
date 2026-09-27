# Peer Supervisor v0

## Experimental log boundary

Supervisor is default-off and may change without historical log compatibility.
The log now uses `supervisor_log_event_v0`, with only local-private proposals and
host receipts. It does not use the retired Todo event store or produce Todo
projections. Archive any older experimental log before starting a fresh one;
an unknown schema is refused without modifying the file.

Proposal/receipt admission and publication hold one log lock. Preview does not
write or sync the log. Retry of the same semantic identity returns the original
record; conflicting content is rejected. Competing executed receipts for one
decision are serialized. These guarantees cover the log, not exactly-once host
execution across a crash before receipt publication.

## Status

Experimental and default-off. This protocol adds an observation and proposal
layer over registered `peer_v1` agents. It does not add a new scheduler, create
agent sessions, or grant one identity durable authority over another.

The design is informed by Shepherd's runtime-supervisor experiments, where a
stronger observer can compare concurrent effect streams and choose to inject,
handoff, or discard a branch. LoopX keeps those actions as typed proposals until
a host runtime exposes the required execution capabilities and returns evidence.

## Why A Supervisor

Several equal peers are useful for independent progress, but they give the user
multiple places to inspect. An optional supervisor provides one synthesis
channel that can compare:

- goal status and user gates;
- each peer's quota and interaction contract;
- todo claims, leases, and continuation state;
- recent agent-scoped evidence;
- compact runtime effect or state references.

The user can use the supervisor task as the preferred control-room conversation
while several peers run. The user may still talk directly to any peer. Decisions
that change goal authority remain LoopX user todos or gates, so they do not live
only in a supervisor transcript.

## Configuration

The supervisor must already be a registered peer. Enabling it is explicit:

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --supervisor-agent <registered-agent-id> \
  --supervised-agent <peer-a> \
  --supervised-agent <peer-b> \
  --execute
```

Omit `--supervised-agent` to observe every registered peer except the supervisor.
Disable the feature with:

```bash
loopx configure-goal --goal-id <goal-id> --clear-supervisor --execute
```

Configuration is stored as `coordination.supervisor` with schema
`peer_supervisor_v0`. Absence means disabled. The canonical configuration has
`execution_mode=proposal_only`.

Generate the dedicated task body after configuration:

```bash
loopx supervisor-prompt \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent-id>
```

The prompt runs the supervisor's own quota guard, then consumes one read-only
observation packet:

```bash
loopx supervisor-observe \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent-id>
```

`supervisor_observation_v0` selects from existing public-safe projections. For
each supervised peer it includes current claim, state, next action, last
activity, workspace/handoff references, recent thin evidence rows, and compact
effect references. It does not run another peer's quota guard, include raw
history or transcripts, or introduce write authority. Missing peer status or
evidence is projected as a warning and makes `decision_input_complete=false`.
Degraded status contracts behave the same way: the packet preserves the usable
read-only projection, reports compact health counts, and does not claim that
the decision input is complete.

## Durable Proposals And Host Receipts

The supervisor records its exact normalized decision before reporting it:

```bash
loopx supervisor-event propose \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent-id> \
  --decision-json <decision.json> \
  --execute
```

This appends a goal-local `supervisor_proposed` event. It remains
`proposal_only`; the proposal is never proof that a host changed a session.

The CLI can validate or append a `rejected` or `failed` attempt receipt:

```bash
loopx supervisor-event receipt \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent-id> \
  --receipt-json <receipt.json> \
  --execute
```

An `executed` receipt is stricter: it can only be appended through the host
adapter API, which supplies verified capabilities outside the editable receipt
JSON. It also requires an opaque authority reference, compact evidence
references, and an explicit rollback boundary. Rollback mode is closed to
`compensating_action` or `not_reversible`; neither mode authorizes automatic
rollback. Missing capability, authority, evidence, or rollback boundary fails
closed. A normal CLI caller therefore cannot promote a proposal to executed
merely by naming a capability. `rejected` and `failed` receipts remain durable
attempt evidence without projecting success. Reusing the same record id is
idempotent when the payload matches and a conflict when it differs.

Read the compact projection with:

```bash
loopx supervisor-event list \
  --goal-id <goal-id> \
  --agent-id <supervisor-agent-id>
```

The ledger is local-private goal runtime state. JSON input paths are never
recorded, and inline credential-shaped values are rejected.

## Decision Contract

`supervisor_decision_v0` uses an enum-like closed set:

| Kind | Meaning | Required host capability |
| --- | --- | --- |
| `observe` | Keep watching; no intervention is justified. | none |
| `inject` | Propose a bounded message to an existing session. | `session_message_injection` |
| `handoff` | Propose continuing a target from a named source state. | `session_state_fork`, `workspace_state_transfer` |
| `discard` | Propose terminating a failed branch while retaining compact evidence. | `session_termination` |

Every proposal names reason codes and compact evidence references. `inject`
names a target and message. `handoff` names source, target, and state reference.
`discard` names target and state reference.

The v0 CLI does not execute these actions. Missing host capabilities leave the
proposal unexecuted; a model response is never accepted as proof that a session
was injected, forked, or terminated. Destructive actions require explicit host
authority even after an executor exists.

## Future Fork Extension Gate

A Shepherd-style `fork` is useful, but it is not another spelling of
`handoff`:

| Operation | Source continues | Target | Scheduling effect |
| --- | --- | --- | --- |
| `handoff` | Usually finished or yielding | Another registered peer continues from `state_ref` | Transfers continuation; should not increase active branch count by default |
| future `fork` | Yes | The scheduler leases a temporary execution branch to an idle capability-matched registered peer | Increases active work and must reserve bounded capacity |

LoopX should add `fork` to the closed supervisor decision set only when a real
host call site can satisfy the complete execution contract. Until then it stays
a documented extension gate rather than speculative production schema or a
prompt-only action.

### Identity And State Model

Raft's persistent-agent model is the right constraint for LoopX: one registered
peer keeps one durable identity and accumulated context. A fork copies
execution state, not identity. It creates an `execution_branch_id`, then the
scheduler assigns a `branch_lease_id` to an existing idle, capability-matched
`executor_agent_id`. The source and executor may be the same peer, but the
normal multi-agent path uses another available peer so the source can continue.
The scheduler must not register a cloned peer, copy durable memory into a new
identity, or create a hidden leader/follower relationship.

The branch therefore separates four identities explicitly:

- `source_agent_id`: the peer whose versioned execution state is forked;
- `source_state_ref`: the immutable execution and workspace checkpoint;
- `execution_branch_id`: the temporary branch identity;
- `executor_agent_id` plus `branch_lease_id`: the registered peer temporarily
  scheduled to run it.

The branch lease is narrower than normal todo ownership. It authorizes bounded
execution of one branch, not claiming the source peer's todo, inheriting its
quota, or merging its durable memory. If the result is selected, ordinary
LoopX continuation or handoff policy decides which peer owns the next durable
todo.

The source must be a versioned immutable `source_state_ref`, not a reconstructed
chat transcript. The branch receives an isolated session/process view and a
copy-on-write workspace view. Its effect and evidence streams remain separately
addressable and join back to the source through stable refs.

### Admission And Settlement

Forking consumes more compute and creates competing outputs, so a supervisor
proposal is not enough to start one. A future host admission receipt must prove:

- `versioned_execution_state` and `session_state_fork`;
- `workspace_copy_on_write` or equivalent isolated workspace state;
- `scheduler_capacity_reservation` and `idle_peer_selection`, including
  capability matching, fanout, cost, fairness, expiry, and cancel boundaries;
- `branch_execution_lease`, preventing one idle peer from accepting competing
  branches and making lease loss fail closed;
- an opaque authority ref and idempotent branch id;
- compact effect/evidence refs without raw transcript injection; and
- `held_result_settlement`, so branch output cannot land in the canonical
  workspace or LoopX state merely because the branch finished.

The minimum lifecycle is:

```text
proposal_only -> admitted -> leased -> running -> held_result
                                                -> failed
                                                -> expired
held_result -> selected | discarded
```

`selected` still passes through ordinary LoopX todo ownership, validation,
review, merge, and user-gate policy. It is not an automatic merge. `discarded`
retains compact evidence and releases the executor peer plus reserved capacity;
it does not authorize destructive git cleanup. A branch that expires or loses
its lease must fail closed instead of silently continuing.

### Supervisor And Workspace Interaction

The supervisor remains the preferred synthesis channel, not a centralized
company brain. Branch progress should appear as queryable inbox-like projection
rows so the supervisor can pull relevant changes without pushing every branch
event into its context. Completed branch output remains held until explicit
settlement, matching an agent-native workspace where persistent peers keep
their own context and exchange bounded messages or artifacts.

This extension should first ship as a default-off dry-run canary over a concrete
multi-agent scheduler/host adapter. Required validation includes capacity
exhaustion, duplicate fork idempotency, source-state immutability, workspace
isolation, capability-based idle-peer selection, competing branch leases,
branch expiry/cancellation, held-result settlement, and recovery when the host
reports a partial failure. Only that evidence justifies widening
`SupervisorDecisionKind` and the public event schema.

## Opt-In Inject Adapter Canary

`loopx.control_plane.agents.supervisor_inject` exposes one narrow Python host
seam for `inject`. LoopX ships no default adapter and no CLI switch that enables
it. A host must explicitly supply an adapter with
`session_message_injection`, a rollback mode and opaque rollback policy ref,
plus an authority ref for the individual execution. Dry-run validates the
entire request without calling the host.

On execution, the adapter receives a stable `SupervisorInjectRequest` and must
return a typed `SupervisorInjectResult`. LoopX then appends the capability-
matched receipt. A prior executed receipt suppresses a second host call, so a
repeated control-plane request is idempotent. The rollback field records the
boundary; it does not retract a message or grant authority to send a
compensating message. `handoff` and `discard` remain proposal-only until their
own host contracts and safety evidence exist.

## Authority Boundaries

- The supervisor is an equal peer with an extra observation responsibility.
- It cannot claim another peer's todo, spend another peer's quota, or rewrite a
  user gate merely to resolve a proposal.
- Review and handoff remain ordinary task policies; the supervisor does not
  become a hidden review owner.
- Pre-peer hierarchy fields remain confined to the existing exactly-once
  migration reader. They are not a live configuration model and are not used
  by this protocol.

This separation lets LoopX test whether richer synthesis improves delivery
without coupling the State Kernel to a particular session runtime or bringing
durable hierarchy back into `peer_v1`.

## References

- [Shepherd: A Meta-Agent for Versioned Execution](https://arxiv.org/abs/2605.10913)
- [CooperBench](https://arxiv.org/abs/2601.13295)
- [Shepherd repository](https://github.com/shepherd-agents/shepherd)
- [Raft: Where Humans and Agents Build Together](https://raft.build/resources/blog/introducing-raft-where-humans-and-agents-build-together/)
- [Raft: Is Having Agents in the Room Meant to Be Chaotic?](https://raft.build/resources/blog/is-having-agents-in-the-room-meant-to-be-chaotic/)
- [Raft: You Don't Need a Company Brain](https://raft.build/zh-cn/resources/blog/you-dont-need-a-company-brain/)
