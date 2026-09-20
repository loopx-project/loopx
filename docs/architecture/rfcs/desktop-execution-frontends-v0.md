# RFC: LoopX Desktop Execution Frontends v0

- Status: Draft
- Decision boundary: support both attachment to an externally owned Agent
  session and an end-to-end LoopX-managed desktop runtime
- Initial attached runtime: Codex App / app-server
- Initial managed runtimes: Pi and DeepSeek Harness (`dsh`)
- Managed provider selection: explicit guided configuration and user intent; Agent allocation within authorized eligible profiles. Ark Agent Plan remains an optional distribution preset.

## Summary

LoopX Desktop should support two explicit execution frontend modes:

1. **Attached App Session.** The operator attaches LoopX Desktop to an
   already-running Codex App or app-server session. The external host keeps
   process, conversation, interruption, resume, and execution-loop ownership.
2. **Managed Agent Runtime.** LoopX Desktop launches and supervises Pi or
   DeepSeek Harness, selects an explicit provider profile, and advances work
   through bounded `loopx_turn_v0` transactions. A distribution may offer
   Volcengine Ark Agent Plan as a named preset. The operator's configuration and
   explicit intent bound autonomous Agent allocation; no provider is a universal
   product default. Runtime and provider contracts remain replaceable.

Both modes present the same LoopX Goal, Todo, gate, quota, evidence, and status
truth. They do not share process ownership. The frontend must never infer a
mode switch from chat prose or silently start a second executor.

Execution mode is independent from external connectors. Web Chat and a Lark
Bot may both connect to the same Agent-owned working session, while Lark group
messages or document comments may use an explicit ordered queue or
asynchronous inbox until that session can accept input. A Goal may contain
multiple Agents; each Agent has its own working-session binding, at most one
active runtime session, and explicit connector bindings without introducing a
manager Agent or hard-coding a connector to Codex.

Not every connector is a conversation transport. A Lark group can be a live
transport or an asynchronous event source. A document body is registered
authority material, while its comment stream is a separate event source with
its own cursor, capture policy, reply capability, and acknowledgement state.
LoopX must not equate fetching a document body with observing its comments.

The managed mode does not require a host-native Goal loop. A desktop-owned
runtime supervisor repeatedly asks LoopX whether another bounded Turn is
eligible, invokes the selected runtime adapter, validates its result, and
commits accepted state. `loopx_turn_v0` remains one transaction rather than a
second recurring scheduler.

## Guided configuration, user intent and autonomous allocation

This proposed selection rule refines the provider-default language; it does not
change shipped runtime defaults or qualify new adapters. Reuse the existing
machine/Goal capability editor, provider store, session binding and dispatch
admission. It adds no capability id, provider implementation or routing service.

- **Configuration makes choices usable.** Show detected installation and login,
  supported tools, model/account, cost or unknown cost, host availability and
  permission scope. Discovery does not grant access. A named preset is an
  offered choice; it cannot override an existing configured selection.
- **Explicit user intent binds the choice.** A fixed model/account, local-only
  requirement, budget or member restriction applies at its declared scope.
  A preference is not a hard lock unless the user made it one. Do not turn a
  single user's Codex preference, or one distribution's Ark preset, into a
  global provider rule. Unresolved conflicting instructions require clarification.
- **The Agent allocates inside that boundary.** Choose eligible models/runtimes,
  reuse or request workers and redistribute future work by task fit, tool access,
  cost and observed availability. A flexible authorized pool does not need a
  fresh confirmation for each assignment. Selection is semantic Agent judgment;
  typed owners enforce eligibility, budget, authority and session fences.

Project the effective runtime/model/profile, assignment reason and readiness in
settings and the team detail. Recheck admission at dispatch. An unavailable pinned
route blocks with a repair action; an unavailable member of a flexible pool may
be replaced by another eligible route with visible readback. No substitution may
expand data exposure, credentials, cost authority or supported tools. Active
sessions retain their binding and context ownership; an authorized autonomous
reassignment uses the existing explicit rebinding/continuation contract, not a
silent session migration. New paid resources or scope changes retain their
existing decision boundary.

Qualify through packaged setup, Chat/steward and independent CLI: pinned choice
wins over a preset; flexible allocation succeeds without repeated approval;
unavailable pinned choice stays blocked; exhausted budget or an unauthorized
fallback creates no execution; restart preserves the effective configuration.
Optional Lark must project the same choice and its own audience restrictions.
Until these cases pass, this is the allocation design, not a runtime guarantee.
See the [near-term launch route](loopx-overall-roadmap-v0.md#near-term-local-agent-product-and-launch).

## Problem

LoopX already has the pieces of two different products:

- a control-plane kernel with authoritative Goal and Todo state, gates, quota,
  validation, writeback, and scheduling hints;
- a desktop frontend and local application shell;
- visible-host integration, including the opt-in Pi Goal extension;
- a host-neutral bounded Turn protocol; and
- a DeepSeek Harness adapter that can execute a Turn through `dsh`.

What is missing is an accepted desktop ownership model that connects these
pieces without collapsing two valid workflows into one.

Some users already have a long-running App session with valuable context and
an installed LoopX automation prompt. Replacing that session with a newly
launched CLI would create two conversations, change runtime policy, and risk
two executors advancing the same Goal.

Other users want a complete desktop product: choose a Goal, configure a
provider, start an Agent, converse with it, interrupt it, close the application,
and resume the same working session later. Requiring them to start a separate
App or install a host-native Goal loop defeats that product shape.

The frontend therefore needs two explicit modes with a shared projection and
separate lifecycle contracts.

## Decision

LoopX Desktop exposes a tagged execution frontend:

```text
desktop_execution_frontend_v0 =
  attached_app_session
  | managed_agent_runtime
```

The mode is chosen explicitly when a frontend binding is created. It is
persisted with the binding and is visible in status. A reconnect may restore
the same mode and session identity, but it may not change modes implicitly.

Conversation transport is a second, orthogonal tag, and collaboration event
sources are a third:

```text
execution mode: attached_app_session | managed_agent_runtime
transport:      web_chat | lark_bot
event source:   lark_group_message | lark_document_comment | ...
ownership:      one Goal -> many Agents -> at most one active session per Agent
```

Adding or removing a transport or event source does not create, replace, or
migrate an execution session. Changing execution mode is a separate explicit
operation.

### Common invariants

Both modes preserve the following boundaries:

- **LoopX owns work truth.** Goal, Todo, claim, gate, quota, evidence, accepted
  progress, and terminal state remain authoritative in LoopX.
- **The runtime owns execution mechanics.** Codex App, Pi, or `dsh` owns its
  model/tool loop and opaque upstream session state.
- **The Agent owns the working-session route.** A Goal may have multiple
  Agents. Runtime, transport, and event-source bindings resolve through an
  explicit Agent id, never through an ambiguous Goal-wide default.
- **The provider owns inference.** Provider credentials, endpoints, model
  availability, and raw payloads are not LoopX task state.
- **Desktop owns presentation and supervision.** It projects work and runtime
  state, routes user input, and, in managed mode only, starts and supervises
  the runtime process.
- **One binding has at most one active executor.** Ingress is serialized and
  duplicate starts fail closed.
- **Conversation is not a write receipt.** Material state changes require the
  relevant LoopX validation and writeback contract.

### Mode comparison

| Boundary | Attached App Session | Managed Agent Runtime |
|---|---|---|
| Process owner | External App / app-server | LoopX Desktop runtime supervisor |
| Initial runtime | Codex App / app-server | Pi or `dsh` |
| Session creation | Before attachment | Created by Desktop |
| Conversation transport | Existing app-server connection | Managed runtime adapter |
| Execution-loop driver | Existing automation prompt or visible-host loop | Desktop outer loop plus bounded LoopX Turns |
| Host-native Goal required | Allowed, not imposed by Desktop | No |
| Provider configuration | Inherited from external session | Explicit managed provider profile |
| Disconnect behavior | Report stale/disconnected | Reconcile process and offer resume/restart |
| Mode fallback | Never automatic | Never attaches to an unrelated session |

## Agent-scoped Web and Lark convergence

The short-term collaboration product is not a separate status Bot. It is a
second frontend transport for an Agent's real working session:

```text
LoopX Goal
  -> Agent A
       -> working session A (attached or managed)
            -> Web Chat
            -> Lark Bot connection A
  -> Agent B
       -> working session B (attached or managed)
            -> Web Chat
            -> Lark Bot connection B
```

In v0, each Agent may have at most one active `lark_bot` connection. This is a
logical Agent-to-connection binding; it does not require a unique Lark
application credential for every Agent. One Bot application may serve multiple
connections if the local broker preserves explicit Agent and channel routing.

### One ordered working conversation

The baseline project coordinator surface is the existing **Goal → Chat**. A
registered peer may carry that responsibility instead; neither choice creates
a separate coordinator conversation or changes the steward's cross-Goal role.
[Explicit Codex continuation](../../reference/goal-chat-continuation.md) reuses
the composer with explicit enable/pause/continue, streamed work and original local
history. It joins the existing delegation service and independently accepted
member results; queue/inbox/steer retain distinct receipts. First-use settings
select an existing execution binding without granting authority by registration.
Lark/other-lead parity and unattended operation remain separate requirements.

In live-steering and queued-session modes, Web and Lark messages enter one
serialized ingress stream for the selected Agent session. Each message records
public-safe transport metadata such as `origin=web` or `origin=lark`, but origin
does not select a different Agent, conversation history, executor, or LoopX
state machine.

The session router assigns ordering before delivery to the runtime. A
simultaneous Web and Lark message may wait, interrupt through an explicit
control action, or fail closed according to session policy; it may not create
two concurrent Agent attempts. Responses may be projected to both surfaces
according to connection policy while preserving one canonical sequence.

An asynchronous inbox event is different: it remains owner-private external
input until the selected Agent drains and interprets it. Only the accepted
Agent-facing message or resulting durable effect joins the working-session
sequence. Provider collection alone does not create conversation history,
task authority, a Turn, or quota spend.

### Agent binding, not Goal-wide or runtime-specific binding

A Lark connection binds to a concrete Agent within a Goal. If a Goal has
multiple Agents and the connection does not identify one, routing fails closed.
The Bot talks directly to that working Agent; it does not first ask a manager
Agent to classify or relay the message. The binding is not hard-coded to Codex:
the Agent's execution session may be an attached Codex App today or a managed
Pi/`dsh` session later.

### Product projection

The selected Agent's Goal Chat header should show a bounded connection state,
for example `Lark Bot · connected`, `listening`, `stale`, or `disconnected`,
and provide a direct management entry. The management view owns explicit
attach, detach, channel selection, freshness, and reconnect actions.

`loopx_collaboration_status_v0` may provide a useful read-only card, but it is
not the core abstraction. The core objects are the Agent-scoped frontend
connection and the converged working session.

## Agent-scoped external Connector model

Lark group ingress and Lark document comments are two instances of one
provider-neutral Connector boundary. A Connector binds an external source to
one registered Agent and advertises only the operations it can actually
perform:

```text
agent_external_connector_v0 = {
  goal_ref,
  agent_ref,
  provider_kind,
  source_kind,
  source_ref,             // opaque owner-local reference
  capture_policy,
  ingress_policy,
  response_policy,
  cursor_ref,
  lifecycle,
  capabilities[]
}
```

The same provider may expose several source kinds. For example, a Lark group
source may advertise live delivery, history catch-up, thread reply, and ACK,
while a document-comment source may advertise incremental listing, anchor and
reply-chain readback, comment reply, and resolved-state observation. Missing
capabilities remain unavailable; LoopX does not emulate them by scraping an
unrelated surface.

A Connector capability may also expose typed `permission_requirements` with
the provider identity, exact scopes, publication requirement, and an official
repair URL bound to the selected App. The provider extension owns those facts;
the LoopX core only renders the typed guidance. Realtime receive, response
write, and history catch-up remain separate capabilities and must not be
collapsed into one generic "message permission" flag.

### Authority material versus collaboration events

A durable document and its comments have different authority semantics:

- the document body is registered as a Goal authority material with freshness,
  revision, owner status, and conflict policy;
- a comment is owner-private external input addressed to an Agent, not an
  accepted requirement, Todo mutation, or repository fact by itself; and
- incorporating a comment requires an explicit durable effect such as a Todo
  update, accepted design revision, no-follow-up rationale, or owner gate.

Reading the body does not advance the comment cursor. Listing comments does not
make the document authoritative. A comment that contradicts accepted state is
recorded as a pending decision or evidence gap rather than silently changing
Goal truth.

### Capture, replay, and acknowledgement

Every event-source Connector owns a stable provider event id, incremental
cursor or equivalent checkpoint, bounded catch-up policy, and idempotency key.
Real-time subscription and history catch-up feed the same deduplicated inbox so
that events created before attachment or during downtime are not silently
lost. A source may be filtered by mention, author, document, comment state,
anchor, or configured source scope without changing its delivery mode.

The Agent processes one accepted event with this ordering:

```text
capture and deduplicate
  -> mark processing
  -> read fresh Goal and authority state
  -> record durable effect or explicit no-follow-up rationale
  -> send an optional response through a declared Connector capability
  -> verify provider readback
  -> ACK and advance the source cursor
```

No ACK or cursor advance may precede the durable effect and required verified
response. A crash replays the same event idempotently. Private bodies, authors,
provider ids, source references, and comment text remain in owner-local inbox
storage; status and quota see content-free urgency only.

### Delivery into the working Agent

Connector capture and Agent delivery remain orthogonal. A live group message
may steer the current working session, wait in its ordered queue, or wake an
asynchronous Agent inbox. A document comment normally enters through
`async_inbox`, but the same event may be submitted into a verified live session
when an explicit policy permits it. In all cases it targets the existing bound
Agent and never starts a shadow manager or a fresh conversation implicitly.

### Short-term Goal Channel bridge

The existing Goal Channel transport may provide the first Lark delivery path,
provided that its Goal-level connection is refined with an explicit target
Agent and is routed into that Agent's existing ordered session. This bridge is
an incremental implementation path, not permission to keep a second IM-only
conversation lifecycle.

If accepted, this RFC supersedes the Goal Channel draft's one-Goal-to-one-Lark-
binding constraint for interactive chat. Goal-wide Kanban, lifecycle
notifications, and shared collaboration artifacts may remain Goal-scoped;
inbound working conversation is Agent-scoped.

## Agent-scoped Bot ingress modes

Agent-to-Bot connections and peer collaboration need the same three explicit
ingress semantics. User-facing names are **inbox**, **queue** and **steer**;
the existing vocabulary below remains. They express delivery intent for one
bound Agent, not three Agents or a natural-language classifier. This proposal
extends the common policy to peer ingress; it does not ship a new API or change
existing adapters merely by renaming their input:

```text
agent_bot_ingress_mode_v0 =
  live_steering
  | session_queue
  | async_inbox
```

The three policies solve different availability conditions:

| Mode | Delivery target | Availability model | Durable boundary |
|---|---|---|---|
| `live_steering` | The specified active execution in the bound working session | Host can apply input at a declared safe point | Existing session/event store and consumption receipt; no second executor |
| `session_queue` | A subsequent work input in the same bound session | Current work finishes or explicitly yields its execution before delivery | Owner-local durable ordered ingress queue keyed by Agent and session |
| `async_inbox` | The next eligible LoopX Agent turn after an explicit drain | No Agent process needs to remain alive | Existing provider-owned event inbox plus content-free quota urgency |

This refines the earlier queue phrase "when it next accepts input": a host that
merges pending input into the active work has not thereby implemented the
proposed queue semantics. Qualify the change explicitly, preserving old profile
behavior until its opt-in implementation and compatibility tests pass.

Persist the requested mode, permitted fallback and actual delivery disposition
with existing ingress identity and recipient scope. Readback distinguishes
durable receipt, queued dispatch, host consumption and steering application;
work adoption/acceptance remains with collaboration/work owners. Model prose or
HTTP success is not a consumption receipt. Unknown capabilities fail explicitly.
Frontend, CLI and Lark show the effective mode, waiting reason and result on the
original work/conversation surface, rather than creating a separate team board.

### Delivery intent does not choose the wake policy

The mode determines where input may be consumed; the binding's existing
continuation owner determines whether another execution opportunity is admitted.
This proposed matrix qualifies adapters without adding a fourth ingress mode:

| Recipient condition | Required behavior |
| --- | --- |
| Active turn or pending tool | Inbox remains for explicit drain; queue waits for a subsequent turn; steer targets the exact active generation and declared safe input boundary. Acceptance cannot imply that an already submitted model/tool request was preempted. |
| Idle or turn complete | Persist eligible inbox/queue input. Only the configured continuation owner may admit a new turn after scope/budget checks; without that policy, show pending input. Steer is unavailable without an active target. |
| Finalizing or interrupted | Preserve late input/result identity without reopening the finishing turn. Recheck after finalization; explicit interruption cannot be undone by a notification. Resume follows the existing owner and pause policy. |
| Unloaded or disconnected | Persistence does not prove a live session. Recover only through the qualified binding path, revalidate scope and generation, and retain an actionable pending/unavailable observation when recovery is unsupported. |

A queued input is not a promise to start a turn; a provider's trigger flag is
not LoopX admission. Multiple accepted messages may enter one eligible turn,
but independent work requests retain their identities and return obligations.
Use the [handoff contract](capable-manager-semantic-handoff-v0.md#request-identity-and-result-routing-across-a-team)
for those relations, rather than treating one transport receipt as a join.

Project three separate facts: the ingress receipt, the actual execution/wakeup
observation, and the work result/acceptance. Never show a saved message as
"the Agent is working" or a wake notification as "result received". Notification
loss must leave the saved input/result discoverable through readback; replay or
reconnect must deduplicate application by ingress/result identity and cannot start a second executor. Extend the existing
corrected-input fixture with idle input without a wake policy, finalization races,
coalesced notifications and restart between result commit and notification.
These are design requirements; every host still needs its own qualification.

### Capture, ingress, and reply are orthogonal

Provider selection and Agent delivery must not reuse one overloaded flag. The
initial Lark group shape is:

```text
capture_scope: mentions | configured_chat_all
ingress_mode: live_steering | session_queue | async_inbox
reply_mode: source_thread | topic_reply | configured_mirror
```

`capture_scope` answers which provider events are eligible. `ingress_mode`
answers how one eligible event reaches the Agent. `reply_mode` answers where a
verified response is delivered. The existing `incoming_mode=mentions|all`
expresses capture scope only; it is not proof of session attachment.

The persisted inbox scope must equal the effective provider routing scope. An
`addressed_only` stream is never projected as `thread_complete`, even when it
has an enabled source-message reply binding. `configured_chat_all` remains an
explicit owner choice: it stores the configured conversation for domain
interpretation, but only typed questions, mentions, or verified bot replies
activate `reply_due`.

Mention admission binds both the App id and the Bot open id returned by the
verified provider profile. A rendered display name is a compatibility signal,
not the only identity proof. Every rejected provider event retains one
content-free decision reason such as `not_addressed` or `topic_mismatch` in
listener health, so an event that was seen but not persisted cannot disappear
behind a bare `ignored` status.

Fallback is explicit and defaults to fail closed. A `live_steering`
connection may opt into `session_queue` or `async_inbox` when the session is
unavailable, but it may not silently start another runtime or write the same
event to multiple modes. The selected mode, fallback decision, and dedupe key
produce one content-free ingress receipt.

### Live steering

`live_steering` submits into a verified Agent working-session binding. It
shares the Web ingress serializer, upstream resume identity, interrupt policy,
workspace, runtime, trust, and capability boundary. If that binding is stale,
ambiguous, terminal, or owned by another Agent, delivery fails closed.

Steering is transport, not task authority. Read-only input can be consumed by
the active session without claiming new work. A material effect still requires
the fresh LoopX decision, validation, writeback, and settlement appropriate to the attached or
managed execution mode.

Steer targets the current execution generation and its next supported safe input
point; it is not interrupt/restart. While an external tool is outstanding, the
host may durably accept a pending correction without claiming it was applied.
If safe injection is unavailable, report that fact and use only the request's
explicit fallback. Never fabricate a tool result to deliver the correction.
An invalidated tool call needs an explicit cancellation disposition; reconcile
its late result against the current input version and execution fence. The
message itself neither cancels all peers nor revokes their authority.

### Session queue

`session_queue` is a broker-owned buffer for a known Agent working session. It
preserves stable event dedupe, per-session order, bounded size, expiry,
backpressure, cancellation, and crash-safe dispatch. It is not the LoopX Todo
queue and may not mutate Goal priority, claim work, or grant capabilities.

After the current work ends or explicitly yields execution, the broker submits
the oldest eligible entry through normal serialized ingress. A pending-tool
idle observation alone is not that boundary. A missing or replaced session
requires an explicit rebind or dead-letter decision; it does not silently
route the entry to a fresh Agent history.

### Asynchronous inbox

`async_inbox` reuses the existing Lark event inbox and collector rather than
keeping an Agent process alive. The collector writes owner-private bounded
events. LoopX projects only `operator_inbox_urgency_v0`: pending/question/
mention/reply counts, oldest age, and `reply_due`, never message bodies,
senders, provider ids, private paths, or chat ids.

When `reply_due=true`, the inbox lane preempts ordinary advancement and monitor
work at the next eligible admission; this does not interrupt an active execution.
The selected Agent drains bounded content, interprets it against fresh
Goal state, writes any durable effect first, sends at most one idempotent
source-thread reply with provider readback, and only then ACKs. Drain alone is
read-only; collection or ACK is never semantic authority.

The Goal Topic compatibility runtime currently composes provider collection,
an Inbox file, a Goal Chat answer, reply, and ACK inline. That path is useful
evidence but is not Agent-scoped convergence when it opens a generic Agent
session or fails to register inbox urgency on the bound Goal. The implementation
must split provider collection from ingress policy, require the registered
Agent id, and either submit through a verified working-session binding or
publish the inbox pointer to the canonical quota path.

Qualify the three modes with one corrected-input fixture: pending tool, busy and
offline recipient, expired message, full queue, duplicate/conflicting identity,
session replacement, sender revocation and late tool result. Assert the actual
consumption boundary and fallback, not just message existence. Inbox drain must
not claim work acceptance; queue must not alter active work; steer must not claim
application before the host receipt. These are proposed acceptance requirements,
not evidence that every host currently supports all modes.

### Initial product ordering

The first integration should enable `async_inbox` for environments where an
already-running App session cannot yet accept brokered input. This gives a
restart-safe, Agent-owned path without pretending that attachment exists.
`live_steering` follows with the attached App Session broker. `session_queue`
then closes busy/offline ordering and backpressure for both attached and
managed runtimes. A product may expose all three options at once, but each
incoming event selects exactly one effective mode.

## Mode A: Attached App Session

### Discover and attach

A host-local broker lists attachable sessions as bounded descriptors. A public
descriptor may include:

- a public-safe session reference;
- host kind and lifecycle state;
- Goal and Agent binding when known;
- workspace identity as an opaque or redacted reference;
- message, streaming, interrupt, and resume capabilities; and
- freshness and last-activity timestamps.

The operator explicitly selects one descriptor. The broker verifies that the
session is still live and that its Goal, Agent, workspace, and trust boundary
match the requested frontend context. A successful attachment creates a
frontend binding; it does not create an Agent process or a second upstream
session.

### Interact

All user messages continue through app-server. The frontend does not maintain
an ordinary-chat-versus-material-chat classifier. The working Agent and its
installed LoopX interaction contract decide which canonical commands or typed
actions are needed.

The existing automation prompt or visible-host loop remains the driver. It may
read fresh LoopX state, select a Todo, execute a bounded segment, validate the
result, write state back, and account quota through the normal LoopX command
surface. The frontend projects that state; it does not wrap every chat message
in `turn run-once`.

### Detach

Detaching removes only the frontend binding. It does not terminate the App
session, delete its automation, complete a Todo, spend quota, or change Goal
state. If the attached session disappears, Desktop reports it as stale or
disconnected and does not silently launch a managed runtime.

## Mode B: Managed Agent Runtime

The [DSH/Pi assessment](./harness-selection-dsh-pi-v0.md) separates the first
passive event source from managed-runtime selection and defines the remaining
lifecycle, readback and measurement gates. Combined diagnostic CLI readback is
available; the managed panel and supervisor are not delivered by that increment.

### Product flow

The managed desktop path is end to end:

1. select or create a LoopX Goal and working Agent binding;
2. select Pi or `dsh` as the runtime;
3. guide provider configuration and user constraints, then let the Agent select
   within the authorized eligible profiles; offer Ark Agent Plan as a named preset;
4. validate runtime installation, provider authentication, and advertised
   capabilities;
5. launch one runtime and create one opaque resumable session;
6. send user input to that same session;
7. advance material work through bounded LoopX Turns;
8. project conversation, runtime liveness, Goal/Todo state, validation, quota,
   and the next scheduler action in one view; and
9. interrupt, close, reopen, and resume without silently creating a new
   session.

### Managed loop controller

Managed mode uses a Desktop-owned runtime supervisor as the outer loop:

```text
fresh LoopX state
  -> gate, quota, and selected Todo decision
  -> create one idempotent loopx_turn_v0 envelope
  -> Pi or dsh executes one bounded attempt
  -> independent validation
  -> canonical LoopX writeback
  -> quota spend only after accepted writeback
  -> scheduler hint: continue, wait, replan, or stop
  -> Desktop supervisor decides whether to request another Turn
```

`loopx_turn_v0` stays a bounded transaction. It does not become an eternal
loop or a second scheduler. The supervisor is responsible for process
liveness, one-Turn-at-a-time serialization, cancellation, backoff, wakeup,
crash recovery, and session resume. LoopX remains responsible for whether work
is eligible and whether an outcome is accepted.

This mode does not depend on a runtime's native Goal abstraction. The existing
Pi Goal extension remains a supported visible-host integration, but managed Pi
may reuse Pi's Agent/session/tool surfaces without using that extension as the
desktop scheduler. Likewise, the existing `dsh` Turn connector is a useful
starting point; the desktop contract must not depend on an unaccepted native
plugin implementation.

### Runtime adapter contract

Pi and `dsh` implement the same narrow managed-runtime contract without
pretending that their internal loops are identical. At minimum it provides:

- install and version probe;
- capability discovery;
- create, resume, interrupt, and close session;
- submit one bounded host request;
- stream public-safe progress and final result events;
- return an opaque owner-local session reference; and
- map runtime failures into stable LoopX/Desktop error classes.

The adapter may keep native transcripts, checkpoints, and tool logs in its own
owner-local storage. LoopX stores only the identifiers and receipts required
for reconciliation, validation, and resume.

### Provider profile contract

Runtime choice and provider choice are orthogonal. Ark Agent Plan is a named
managed-provider preset. Guided configuration, scoped user intent and Agent
allocation select an eligible profile; no provider rule is embedded throughout
the LoopX kernel.

A provider profile must expose or resolve:

- provider and route identifiers;
- an owner-local credential reference;
- supported model discovery;
- API surface and streaming support;
- input/output modalities and tool-call support;
- reasoning or thinking modes when advertised;
- context and output limits when advertised;
- usage and rate-limit telemetry when available; and
- a redacted health-check result.

Capability discovery is versioned evidence. Unknown or conflicting provider
capabilities remain unknown until an explicit probe resolves them. Desktop
must not silently fall back to a different model, route, provider, or billing
plan.

Ark Agent Plan has its own supported-model, credential, and usage boundary.
The adapter must therefore validate the Plan route instead of assuming that a
model supported by a standard Ark endpoint is automatically available through
the Plan profile. Credentials and raw provider responses remain owner-local.

### Goal-bound external capabilities in both modes

Runtime and provider selection do not define the Agent's complete toolset.
Attached and managed working sessions must project the same external
capabilities from the selected Agent's Goal binding. This RFC does not add an
`external toolkit` runtime object or another capability-pack contract. The
existing boundaries remain sufficient:

- an extension owns provider packaging, installation, revision, enablement,
  doctor, and rollback;
- an external capability owns the caller-outcome contract, operations,
  permissions, schemas, validation, readback, and receipts; and
- an optional domain capability pack owns domain policy, state, and projection
  when those semantics are actually required.

A repository that happens to contain multiple skills, commands, and services
is only a source distribution. Its owner may keep a source inventory, but each
executable operation must enter LoopX through an existing extension and
capability contract. Installing the repository, finding a prompt skill, or
observing a same-named provider grants no authority. Duplicate providers fail
closed unless the Goal binding selects one exact ready revision.

Read-only operations may reuse the durable Goal binding without creating work
truth. A material operation must bind to the current authorized work attempt;
managed mode uses the governed Turn transaction, while attached mode preserves
the equivalent host or automation decision and settlement evidence. Neither
path may let a capability provider mutate Goal or Todo state directly.

Private provider coordinates, credentials, logs, traces, database rows, and
document content remain owner-local. LoopX durable state keeps only public-safe
capability and operation identity, provider revision and profile digests,
bounded evidence references, and admitted receipts.

## Optional computer use in the Manager workspace

Implementation tracker: [#4114](https://github.com/huangruiteng/loopx/issues/4114).

### Product decision and ownership

Add an opt-in desktop-operation entry to the existing Manager workspace for
bounded work that lacks a suitable API or CLI. Prefer a declared API/CLI when
it can perform and verify the same authorized effect. A first useful task is
preparing a draft in an approved application and stopping before submission.
The entry must resolve an explicit Goal, Todo, Agent, and existing working
session before dispatch. It neither inserts a relay manager Agent nor starts
a third execution mode. Attached sessions use only host-advertised tools;
managed sessions may explicitly enable an optional provider.

Placement: the first outcome owner is `content-ops` for draft preparation;
computer use remains a provider execution surface under
[`computer_use_runtime_v0`](../../reference/protocols/computer-use-runtime-v0.md),
not a new built-in capability. A candidate provider id is `maka-cu`, delivered
as an optional extension/package if selected. Its install, doctor, disable,
and compatibility belong to the provider; the existing Desktop broker owns
presentation. The capability interprets receipts and proposes transitions;
the Kernel retains Todo, gate, evidence, and quota authority. Existing schemas
and the contract validator are the starting point, not another builder layer.

### Implementation evidence and adoption boundary

The reference is Apache Maka at commit
[`87797378`](https://github.com/apache/maka/tree/87797378cec89e2a31351f64656dc8d26a10e73d).
This is a source inspection, not a LoopX live qualification:

- Its [model tool](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/packages/runtime/src/computer-use-tools.ts)
  exposes application discovery, observation, semantic element actions,
  keyboard input, and window operations. Observation defaults to an element
  tree; screenshots are explicit. Raw coordinate input is excluded from the
  production model action space. Ordered element sequences re-observe per step.
- The [host adapter](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/packages/computer-use/README.md)
  connects Runtime to a supervised native child through `maka.cu/2` JSON-RPC
  over stdio, with digest verification, handshake, cancellation, and generation
  invalidation. Desktop cursor/PiP is downstream presentation, not authority.
- The [pinned executor manifest](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/apps/desktop/bundled-tools.json)
  names native commit `4a9787d2`, ad-hoc signing, missing notarization, and
  `distributionReady: false`. The product selector enables only macOS with a
  supplied executable and digest. A downloadable Desktop does not prove CU
  executor availability or cross-platform readiness.
- [Host-event limitations](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/docs/computer-use-host-events-contract.md)
  distinguish typed intervention from UI content changes and explicitly leave
  global physical-input attribution and stronger process-instance identity
  incomplete. [Evidence classes](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/docs/computer-use-evidence-classes.md)
  separate real-runtime qualification, injected failures, protocol tests, and
  static checks; none substitutes for the others.

Adopt the observation/action/readback and takeover design. Keep provider
selection open until an isolated live slice proves it. The
[native implementation](https://github.com/maka-agent/maka-cu/tree/4a9787d2c7f2fbc6a29b33d691916c6b84543661/packages/OpenComputerUseKit/Sources/OpenComputerUseKit)
uses Swift, macOS Accessibility, and private SkyLight input paths. Its
[provenance record](https://github.com/apache/maka/blob/87797378cec89e2a31351f64656dc8d26a10e73d/docs/computer-use-provenance.md)
distinguishes MIT-derived code from proprietary-binary observations. Any
redistribution needs its own provenance, notice, signing, and compatibility
review; this RFC does not select the entire Maka runtime or copy its cursor.

### Required behavior

1. **Visible scope.** Show the chosen application/window, bounded objective,
   permitted effect, stop condition, provider readiness, and screenshot/model
   routing before activation. Enablement grants neither logged-in account
   access nor new external-write authority. Reuse valid scoped authorization;
   installation and OS permissions alone are insufficient.
2. **Fresh target binding.** Bind each action to the current observation and
   target process/window generation. Reject stale or ambiguous targets; never
   fall back to foreground/global input. Re-observe after actions and require
   effect-specific readback before claiming completion. Transport success alone
   cannot complete a Todo.
3. **Human control.** Show compact progress and an immediate Stop/Take over
   control in the existing workspace and Goal Chat. Stop revokes pending input
   authority; already-dispatched unknown effects require reconciliation.
   Lock, disconnect, target restart, or attributable intervention invalidate
   observations. Resume requires fresh observation and current scope. Unknown
   modals and new permission decisions return a concrete human gate.
4. **Bounded execution.** Serialize competing operations on the same target
   across Agents, bound action/time/retry budgets, and never blindly replay a
   mutation after service loss. Provider detail remains typed and local; map
   receipts to the existing closed stop-reason enum without adding ad hoc
   strings. Persist uncertain outcome facts for reconciliation, not retry
   instructions inferred from prose.
5. **Private evidence.** Keep screenshots, AX text, typed values, titles, and
   replay data in the host-owned private boundary. Model transmission requires
   the selected provider's consent and modality policy. Shared projections use
   redacted facts/handles; viewing private evidence requires owner access.
6. **Default-off isolation.** Disabled or unavailable providers expose no CU
   tools, launch no executor, and request no OS permissions. Disable revokes
   sessions and stops the child; uninstall removes its managed package and
   documents OS-permission revocation. Neither operation alters Goal state.

### First delivery slice and acceptance

Implement one `content-ops` draft-until-review flow through an optional
provider and the existing working session, then project its receipt and
human gate in Manager/Goal Chat. Do not add shared session/handoff protocols
until a second real consumer demonstrates the need. This is a proposed slice,
not a claim of shipped commands or a commitment to Maka as the runtime.

Acceptance requires the production entrypoint, a real model and native
executor, and an isolated synthetic application with independently checked
field values and an untouched Submit control. Also cover a decoy window,
stale observation, app restart, concurrent target claims, user takeover,
screen lock, uncertain dispatch, and disable/re-enable. Distinguish actual
host events from injected failures. Record exact provider/model/executor
versions, completion, latency, action count, interventions, forbidden effects,
and privacy checks; a recorded fixture or schema-only smoke is insufficient.
Prove feature-off parity in both frontend modes, including tool exposure,
process startup, ingress, projection, and writeback. Preview any visible UI
change for owner approval before committing its implementation.

## State and identity boundaries

The frontend stores one Agent-scoped working-session binding whose public
projection is sufficient to reconnect and explain ownership. Transport
connections reference that binding instead of owning another conversation:

```json
{
  "schema_version": "desktop_execution_session_v0",
  "mode": "attached_app_session | managed_agent_runtime",
  "goal_ref": "public-safe LoopX goal reference",
  "agent_ref": "public-safe LoopX agent reference",
  "runtime_kind": "codex_app | pi | dsh",
  "runtime_session_ref": "opaque owner-local reference",
  "provider_profile_ref": "managed mode only",
  "lifecycle": "starting | ready | running | waiting | interrupted | stale | terminal",
  "capability_snapshot_ref": "versioned public-safe projection",
  "frontend_connections": [
    {
      "kind": "web_chat | lark_bot | lark_document_comments",
      "surface_role": "conversation_transport | collaboration_event_source",
      "connection_ref": "public-safe broker reference",
      "capture_scope": "provider-specific typed policy",
      "ingress_mode": "live_steering | session_queue | async_inbox",
      "reply_mode": "none | source_thread | source_comment | configured_mirror",
      "cursor_ref": "owner-local event-source cursor",
      "state": "connected | listening | stale | disconnected"
    }
  ]
}
```

The schema is illustrative, not a commitment to expose opaque values to the
browser. At minimum, these identities remain distinct:

| Identity | Owner | Purpose |
|---|---|---|
| Goal/Todo | LoopX control plane | Work selection, authority, gates, accounting, termination |
| Agent working-session binding | LoopX control plane and Desktop broker | Scope one Agent's runtime and ordered conversation within a Goal |
| Upstream runtime session | Codex App, Pi, or `dsh` adapter | Conversation, model/tool execution, native resume |
| Provider profile | Owner-local provider store | Authentication, route, model, capability and usage boundary |
| Frontend connection | LoopX Desktop broker | Attach Web or Lark transport to one Agent working session |
| Turn journal | LoopX Turn | Idempotent bounded execution, validation, writeback and settlement evidence |

An attached descriptor or managed session reference cannot grant new Goal
authority. A stale or mismatched Goal, Agent, workspace, runtime, provider, or
trust binding fails closed.

## Safety and privacy

- Keep opaque session handles, credentials, environment values, process
  metadata, raw transcripts, provider payloads, tool logs, and local paths in
  owner-local storage.
- Require an authenticated local broker boundary for discovery, attachment,
  runtime launch, session control, and provider configuration.
- Authenticate Lark callbacks, map each channel to an explicit Agent binding,
  and reject ambiguous or replayed ingress before it reaches the Agent session.
- Keep document authority registration separate from document-comment event
  capture, and require explicit source and Agent bindings for both.
- Preserve the effective sandbox, workspace, approval, network, and capability
  policy in both modes; managed mode must make that policy visible before
  launch.
- Recheck binding freshness before control actions and before every managed
  Turn writeback.
- Use an idempotent Turn key and allow at most one in-flight Turn for a managed
  session.
- Spend quota only after independent validation and accepted state writeback.
- Do not copy private deployment or collaboration context into public fixtures,
  screenshots, examples, or documentation.
- Use synthetic provider fixtures for committed tests and make live provider
  tests explicit and opt-in.

### Team coordination product loop (2026-09-16)

#4547/#4548/#4552 delivered team-preview confirmation UI, packaged resources and a browser fixture; the confirmation entry is no longer unimplemented. Follow [roadmap](loopx-overall-roadmap-v0.md) R1 for semantically accurate partial/gap/stale readback, R2 for actual worker execution and R3 for automatic return/restart recovery. The confirmation fixture does not prove the complete Lark loop.

Frontend/Lark consume the same proposal, receipt and audience projection; confirmed plans cannot imply execution. Every implementation includes real packaged-frontend interaction and applicable Lark qualification, or explicitly names the untested surface. First-viewport/primary-CTA implementation changes still require concrete preview approval. This revision edits RFCs only, not UI.

## Delivery slices

### Slice A: Agent-scoped Lark connection

1. model an explicit Agent working-session binding within a Goal;
2. refine the existing Goal Channel connection with a required target Agent;
3. separate capture scope, ingress mode, and reply mode in the connection;
4. enable `async_inbox` first by registering its Goal-bound config pointer,
   projecting `reply_due`, and requiring drain/writeback/reply/readback/ACK;
5. add `live_steering` only through a verified working-session binding and the
   same serialized ingress used by Web;
6. add `session_queue` with stable dedupe, ordering, bounded backpressure, and
   explicit stale-session handling;
7. let each Agent attach at most one active Lark Bot connection;
8. show connection, capture, ingress, fallback, listening, and pending state in
   the selected Agent's Goal Chat header with a direct management entry; and
9. use the existing working Agent directly, without a manager Agent or a
   separate IM conversation lifecycle.

This is the first collaboration slice. It makes the current Goal Channel
useful immediately while establishing the session convergence needed by both
execution modes.

### Slice A2: document-comment awareness

1. define the provider-neutral Agent Connector and event-inbox contract;
2. register a document body as redacted Goal authority material independently
   from its comment stream;
3. bind one or more configured document-comment sources to a registered Agent;
4. support bounded initial catch-up plus incremental cursor-based reads without
   losing comments created before attachment or during downtime;
5. preserve comment anchors and reply-chain context in owner-local storage;
6. route actionable comments through the same durable-effect-before-response-
   before-ACK lifecycle as group inbox events;
7. expose comment reply and provider readback only when declared by the
   Connector; and
8. project only content-free pending, age, failure, and freshness state to
   LoopX status and quota.

This slice generalizes the group-specific inbox after Slice A proves the Agent
binding and acknowledgement lifecycle. It does not make external comments
authoritative or require the document provider to become a task database.

### Slice B: attached Codex App

1. one host-local app-server session descriptor source;
2. explicit attach and detach actions;
3. reuse of existing message, stream, interrupt, and resume transport;
4. bounded LoopX Goal/status projection beside the session; and
5. no second Agent process or managed fallback.

This is the short path for adopting Desktop around work that is already
running.

### Slice C: managed reference vertical

1. a provider-neutral managed-runtime and managed-provider interface;
2. one Desktop runtime supervisor with start, interrupt, close, reconcile, and
   resume;
3. `dsh` as the first reference runtime by reusing its accepted Turn adapter;
4. Ark Agent Plan as one explicitly selected provider preset;
5. one resumable conversation and one-at-a-time bounded Turn execution; and
6. joined runtime, Turn, and LoopX status in Desktop.

`dsh` is the reference ordering because it already has a bounded Turn
connector; this ordering does not make it the permanent default runtime.

### Slice D: Pi parity

1. a Pi managed-runtime adapter using Pi Agent/session/tool surfaces;
2. the same Ark Agent Plan provider profile and capability handshake;
3. parity for launch, conversation, stream, interrupt, resume, and Turn result;
4. proof that managed Pi progresses without the Pi Goal extension installed;
   and
5. preservation of the existing opt-in Pi Goal extension for visible-host use.

The dual-runtime managed mode is complete only after both Slice C and Slice D
pass the shared conformance suite.

## Validation criteria

### Shared

- one binding has at most one active executor and serialized user ingress;
- one Goal may route different Lark connections to different Agents without
  cross-session delivery;
- LoopX remains authoritative for Goal/Todo lifecycle in every frontend mode;
- a read-only exchange creates no task transition or quota spend;
- both modes expose only the external capability operations admitted by the
  same Goal binding at an exact provider revision;
- an external capability material operation cannot bypass the active work-
  attempt authority, validation, provider readback, or settlement receipt;
- stale or mismatched identity and capability bindings fail closed; and
- committed packets contain no credentials, raw transcripts, provider
  payloads, opaque handles, or real local paths.

### Attached mode

- attaching to a running session starts no second Agent process;
- three consecutive user messages use the same upstream App session;
- interrupt and resume preserve that session identity;
- automation-prompt-driven work updates LoopX state and is projected without
  a managed Turn launch;
- detaching leaves the underlying session and Goal unchanged; and
- loss of the App session never triggers silent managed fallback.

### Web and Lark convergence

- two Agents in one Goal can attach separate Lark connections and each message
  reaches only the explicitly bound Agent;
- capture scope, ingress mode, and reply mode are independently configured and
  an event produces exactly one effective ingress receipt;
- three interleaved Web and Lark messages enter one deterministic working-
  session order in `live_steering`/`session_queue` and preserve origin metadata;
- Web and Lark resume the same Agent session rather than creating parallel
  histories or executors;
- an unavailable steering session fails closed unless an explicit queue or
  inbox fallback is configured; fallback never duplicates delivery;
- queued ingress is ordered, bounded, restart-safe, and remains bound to the
  same Agent/session without becoming a LoopX Todo;
- a real inbox mention or direct question projects content-free `reply_due`,
  preempts ordinary work, and is ACKed only after durable effect plus verified
  source-thread reply; duplicate drain/reply/ACK is idempotent;
- a Goal Chat header projects connection freshness and links to explicit
  management actions;
- ambiguous Goal-only routing and replayed Lark callbacks fail closed; and
- attaching or detaching Lark does not change the Agent's execution mode or
  LoopX Goal/Todo state.

### External Connector awareness

- a group event and a document comment bound to the same Agent are captured as
  distinct provider event types and deduplicated independently;
- initial catch-up discovers an actionable event created before attachment,
  then real-time delivery or polling continues from the committed cursor;
- fetching a document body neither acknowledges comments nor marks them
  incorporated;
- incorporating a comment creates an auditable Todo/design/no-follow-up effect
  before any reply and cursor advance;
- a comment assertion does not become accepted capability or requirement fact
  until its configured evidence or owner boundary is satisfied;
- reply-capable Connectors verify provider readback, while read-only Connectors
  record an explicit no-response outcome; and
- status, quota, and public fixtures contain no comment bodies, author ids,
  private source references, or provider cursor values.

### Managed mode

- Desktop launches exactly one selected runtime and reuses the same opaque
  session across three user messages and multiple bounded Turns;
- the runtime progresses with no host-native Goal loop installed;
- every material attempt has a selected Todo, idempotent Turn identity,
  independent validation, accepted writeback, and post-writeback settlement;
- interruption and application restart preserve or explicitly reconcile the
  session instead of silently creating another one;
- crash replay does not duplicate state writeback or quota spend;
- Pi and `dsh` pass the same lifecycle and Turn-result conformance suite;
- the Ark Agent Plan profile validates its own supported-model and usage
  boundary and fails closed on missing authentication or unsupported
  capability; and
- provider or model changes are explicit rebinding operations, never silent
  fallback.

## Non-goals

- Replacing Pi or `dsh` with a new model/tool execution kernel.
- Removing existing automation-prompt, native Goal, or visible-host modes.
- Wrapping each attached chat message in a managed Turn.
- Making `turn run-once` an eternal scheduler or desktop process supervisor.
- Building a universal runtime abstraction beyond the behavior required by Pi
  and `dsh`.
- Vendoring enterprise toolkits, provider coordinates, credentials, or private
  operating procedures into LoopX core.
- Treating a toolkit repository, install script, or prompt skill as one
  implicitly trusted universal capability.
- Hard-coding a permanent model capability table in LoopX.
- Treating a standard Ark route and an Ark Agent Plan route as globally
  interchangeable.
- Making transcripts, Desktop storage, or provider responses authoritative for
  Goal/Todo lifecycle.
- Automatically migrating an attached session into managed mode.
- Introducing a manager Agent between Lark and the bound working Agent.
- Requiring one physical Lark application credential per Agent; v0 requires a
  logical Agent-scoped connection and explicit routing.
- Inferring ingress mode from message prose or using `mentions|all` as proof of
  a live working-session attachment.
- Treating the session ingress queue as the LoopX Todo queue, or storing raw
  inbox content in quota/status.
- Treating a document body fetch as comment awareness, treating document
  comments as accepted Goal truth, or advancing a comment cursor before the
  durable effect and required response are verified.

## Related surfaces and proposals

- [Runtime connector catalog](../../integrations/runtime-connector-catalog.md)
- [Domain capability packs](../../product/domain-capability-packs.md)
- [Extensions](../../reference/extensions.md)
- [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md)
- [DeepSeek Harness connector](../../integrations/deepseek-harness-connector.md)
- [Pi Goal mode](../../../loopx/pi_goal_mode/README.md)
- [Goal Channel collaboration v0](goal-channel-collaboration-v0.md)
- [Volcengine Ark Agent Plan documentation](https://www.volcengine.com/docs/82379/1928262)
- [Volcengine Ark API overview](https://api.volcengine.com/api-docs/view?serviceCode=ark&version=2024-01-01)

## Open questions

1. Which existing host-local registry should own attachable App descriptors and
   managed runtime session records?
2. Should the first managed Pi adapter embed Pi's Agent API or supervise its
   CLI protocol?
3. What is the smallest common streaming and tool-event surface that Pi and
   `dsh` can expose without leaking native transcripts?
4. Which Ark Agent Plan inference API surface should be the first supported
   provider adapter, and which capability probes are mandatory before launch?
5. Which Agent lifecycle operation explicitly rotates or replaces its working
   runtime session while preserving auditable conversation boundaries?
6. How should optional Pi and `dsh` runtime dependencies be installed and
   upgraded by Desktop?
7. After v0, should an Agent support multiple Lark channel connections, and
   what projection policy should control which responses are mirrored to each
   transport?
8. Which bounded owner-local store should back `session_queue`, and when may an
   explicit rebind preserve queued entries across a replaced runtime session?
9. Should `async_inbox` remain a selectable steady-state mode after
   `live_steering` ships, or primarily serve offline and non-resident Agents?
10. Which provider-neutral cursor contract can cover webhook delivery,
    incremental comment listing, and bounded initial catch-up without leaking
    provider identifiers into public state?
11. Should a document-comment Connector support automatic resolved-state
    transitions, or require an explicit human or capability-owned action in
    the first version?
