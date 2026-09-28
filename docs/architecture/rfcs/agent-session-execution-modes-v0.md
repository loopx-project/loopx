# RFC: Agent Session Execution Modes (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Partial. The attached-host binding, broker, and runtime
  fencing already ship on `main`; the cross-host admission contract below is
  proposed and only partially enforced.
- **Authors / owners:** Maintainer-directed contract. It extracts and normalizes
  the session-ownership decision already described for the Desktop frontend.
  Session-ownership and host-admission decisions require maintainer acceptance.
- **Created:** 2026-09-15
- **Last normative revision:** 2026-09-15
- **Implementation baseline:** `6c3da75ca`
- **Related contracts:** [Desktop execution frontends](desktop-execution-frontends-v0.md),
  [Single-owner local daemon](single-owner-local-daemon-v0.md),
  [Capable manager and semantic handoff](capable-manager-semantic-handoff-v0.md),
  [Governed Turn](../../reference/protocols/loopx-turn-v0.md),
  [Host mode plan](../../reference/protocols/host-mode-plan-v0.md),
  [Attached Agent session broker](../../integrations/attached-agent-session-broker.md)
- **Language mirror:** [中文版](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/agent-session-execution-modes-v0.zh-CN.md)

Language note: the
[Chinese version](./agent-session-execution-modes-v0.zh-CN.md) and this English
version are semantic mirrors. A difference between them is a defect.

## Document map and maintenance contract

Sections 1-11 are the durable design and acceptance contract. Section 6 is the
normative ownership map and is the authoritative answer to which document owns
which boundary; report a contradiction there as a defect rather than resolving
it in prose elsewhere. Section 12 is the normative delivery plan. Section 13
lists decisions that still require approval, and a recommendation there is not
an accepted decision. Appendices A-E are non-normative.

This RFC does not change runtime behavior by itself. It names the ownership
contract that existing code already partially implements, so that a new host
frontend can be admitted without inventing a second authority for Goals,
Todos, sessions, or execution.

## 1. Decision summary

1. Every LoopX agent session binding carries exactly one explicit execution
   mode from a closed set:
   - `managed_runtime`: a LoopX-owned host creates, launches, supervises,
     interrupts, stops, and replaces the runtime session;
   - `attached_host`: an already-running external host session is bound to
     LoopX, and that external host keeps process, conversation, interruption,
     resume, and execution-loop ownership.
2. The mode is chosen when the binding is created, persisted with the binding,
   visible in readback, and never changed implicitly. Reconnecting may restore
   the same mode and session identity; it may not switch modes.
3. LoopX keeps sole ownership of work truth in both modes. Goal, Todo, claim,
   gate, quota, evidence, accepted progress, and terminal state are LoopX
   state. Conversation, including an explicit-sounding chat message, is not a
   write receipt.
4. One Agent-scoped binding has at most one active executor. Input is
   serialized through one ordered session queue, and duplicate or conflicting
   execution attempts fail closed with a typed error instead of racing.
5. Mode is orthogonal to transport, event source, user-facing host-mode
   selection, and ingress/delivery mode. A delivery capability a binding does
   not advertise is unavailable, and unavailability fails closed rather than
   silently routing work to a different executor.

What remains unchanged: Desktop product flows, connector model, Web/Lark
convergence, and computer-use scope stay with the
[Desktop execution frontends RFC](desktop-execution-frontends-v0.md); local
service identity and supervision stay with the
[single-owner daemon RFC](single-owner-local-daemon-v0.md); the bounded Turn
transaction stays with [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md);
user-facing host selection stays with
[host mode plan v0](../../reference/protocols/host-mode-plan-v0.md); manager
semantics and continuation paths stay with the
[capable manager RFC](capable-manager-semantic-handoff-v0.md).

Default and opt-in boundary: a newly created LoopX Chat session defaults to
`managed_runtime`. Attaching an external host session is opt-in through an
explicit binding command, and a managed host only runs when an operator
launches it. Nothing attaches, resumes, replaces, or migrates a session
automatically, and no mode grants Goal, quota, or permission authority.

This RFC does not approve new always-on residency, automatic session resume,
cross-host session takeover, mode change as a side effect of a message or
probe, promotion of any optional preview host into a supported product
surface, or any new credential or permission boundary.

## 2. Problem and motivation

Operators run agent sessions in two shapes that look similar in the UI and are
very different underneath: a session LoopX started, and a session that already
belongs to another host. When the binding does not state which shape it is, the
projection and the real executor diverge. Observed failure shapes:

- A user has a long-running visible host session with valuable context. A
  "continue this Goal" request starts a second runtime, and two executors
  advance the same Todo.
- A host session ends, crashes, or is replaced while its LoopX binding still
  reads `ready`. The UI reports work in progress that nothing can execute.
- A conversation agrees on a plan, and the agreement is read as accepted
  state: Todos or progress appear without the validation and writeback
  contract that LoopX requires.
- A transport or connector is added, and its presence is treated as proof that
  a working session is attached to the Agent.
- An optional host integration defines its own Goal, member, or execution
  records beside LoopX and becomes a second, quieter authority.

The existing owners cannot solve this locally. The LoopX Chat store knows the
binding, the broker knows the claim and completion receipts, the Desktop
frontend knows its product flow, and each external host knows its own process
semantics. Without one admitted contract, every new host re-decides session
ownership, and each re-decision is another chance to create a second authority.
That risk is concrete rather than hypothetical: an optional local host
prototype for AI-led team work introduces conversational setup and its own
bounded execution outside the Desktop frontend, and it must be admitted to the
same contract instead of defining a parallel one.

### Invariants

Every implementation must preserve these properties.

1. **LoopX owns work truth.** Goal, Todo, claim, gate, quota, evidence,
   accepted progress, and terminal state are LoopX state in both modes.
2. **The host owns execution mechanics.** Process lifetime, model and tool
   loop, sandbox, interruption, resume, raw transcripts, and opaque upstream
   session handles belong to the executing host, not to LoopX.
3. **The Agent owns the working-session route.** A Goal may have several
   Agents. Runtime, transport, and event-source bindings resolve through an
   explicit registered `agent_id`, never through a Goal-wide default.
4. **One binding has at most one active executor.** Ingress is serialized and
   duplicate or conflicting starts fail closed.
5. **Mode is explicit, persisted, and read back.** It is never inferred from
   prose, prompt text, a capability probe, or a transport, and it never changes
   implicitly.
6. **Conversation is not a write receipt.** Material state changes require the
   relevant LoopX validation and writeback contract.
7. **The provider owns inference.** Provider credentials, endpoints, model
   availability, and raw payloads are not LoopX task state.
8. **Unavailable delivery fails closed.** A binding may only use a delivery
   capability it advertises; otherwise the result is a typed unavailability or
   the explicitly declared fallback, never a different executor.

## 3. Scope and non-goals

### In scope

- the closed mode vocabulary and its persistence and readback requirements;
- the binding identity model: registered Agent, executor endpoint, host
  surface, ordered session queue;
- the admission contract a new host, including an optional preview host, must
  satisfy before it may bind sessions to LoopX;
- fail-closed behavior for capability-gated delivery and duplicate executors;
- the ownership map across the RFCs and protocols that touch sessions,
  execution, continuity, and collaboration.

### Non-goals

- Desktop product flows, connector model, Web/Lark convergence, bot ingress
  modes, and optional computer use. Those stay with the
  [Desktop execution frontends RFC](desktop-execution-frontends-v0.md).
- Local service identity, readiness, supervision, and migration. Those stay
  with the [single-owner daemon RFC](single-owner-local-daemon-v0.md).
- Manager semantics, semantic handoff, and session-continuation paths. Those
  stay with the [capable manager RFC](capable-manager-semantic-handoff-v0.md)
  and the [explicit continuation contract](cross-session-memory-substrate-v0.md).
- Which managed runtime is promoted for production work. That stays with the
  [harness selection assessment](harness-selection-dsh-pi-v0.md).
- The bounded Turn transaction and its disposition rules. Those stay with
  [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md) and the
  [Turn loop controller](../../reference/protocols/turn-loop-controller-v0.md).
- Selecting the user-facing host mode, connector catalog entries, provider
  profiles, model routing, and pricing.
- New authorization, credentials, tenancy, or always-on residency.

## 4. Current-system contract

Audited at `6c3da75ca`. These are current facts, not proposed behavior.

| Boundary | Current behavior |
| --- | --- |
| Mode vocabulary | [`loopx/chat_store.py`](../../../loopx/chat_store.py) defines `managed_runtime` and `attached_host`, rejects any other value, and defaults a new session to `managed_runtime`. |
| Binding fields | Session creation accepts `session_mode`, `executor_endpoint_id`, `host_surface`, and `attached_capabilities`. An attached binding requires a non-empty `host_surface`; a managed Codex home cannot be attached. |
| Capability keys | Attached capabilities are filtered to the closed set `live_steering`, `session_queue`, `claim_wait`, and `reply_readback`; unknown keys are dropped. |
| Public projection | `public_session()` returns mode, executor endpoint, host surface, attached capabilities, and the manager-runtime readback. It does not return the host session id or message bodies, and attached capabilities are blanked for managed sessions. |
| Attached broker | [`loopx/attached_session.py`](../../../loopx/attached_session.py) implements bind, claim, and complete under `loopx_attached_agent_session_broker_v0`, adapter kind `attached_host_session`, upstream mode `host_broker`, with a bounded claim wait of 1800 seconds, duplicate-safe claim and completion receipts, and per-binding file locks. |
| Runtime fencing | [`loopx/chat_runtime.py`](../../../loopx/chat_runtime.py) never starts a managed adapter for an attached session and fails closed with typed errors such as `attached_session_live_steering_unavailable`, `live_steering_requires_active_turn`, and `live_steering_session_not_attached`. |
| CLI surface | `loopx worker-bridge attached-session-bind`, `-list`, `-claim`, and `-complete` exist in [`loopx/cli_commands/worker_bridge.py`](../../../loopx/cli_commands/worker_bridge.py), documented in the [broker guide](../../integrations/attached-agent-session-broker.md) and the [worker-bridge install contract](../../integrations/worker-bridge-install-contract.md). |
| Existing-session delegation | [`loopx delegation`](../../reference/local-delegation.md#use-an-existing-agent-conversation-through-its-shell) exposes the same explicitly bound work as MCP to an existing shell-capable Agent. `operations` recovers requester-scoped work without remembered IDs, rechecks acceptance and exposes unavailable items and further pages. Newly tool-equipped Goal Chat consumes the same inventory; resumed native threads keep their original tool schema. It does not provision an Agent, migrate a host or install an automatic wake policy. |
| Focused tests | [`tests/test_attached_session_cli.py`](../../../tests/test_attached_session_cli.py) and `tests/test_chat_codex_home.py::test_attached_session_uses_existing_host_not_managed_adapter` cover bind/claim/complete and the no-managed-adapter fence. |
| Product-level proposal | The [Desktop execution frontends RFC](desktop-execution-frontends-v0.md) owns the Mode A/Mode B product comparison, the connector and event-source orthogonality, and the Desktop non-goals. |
| Host-side loop guidance | [Codex CLI TUI loop](../../product/runtimes/codex-cli/codex-cli-tui-loop.md) documents session-attached automation and resume options for one visible host. |

Not established by these facts: there is no cross-host admission contract, no
accepted answer for rotating or replacing a bound working session, no
steady-state capability policy for `live_steering`, no accepted rule for an
optional host that creates Goal or member drafts conversationally, and no
mode-aware projection parity across frontends.

## 5. Proposed architecture

### Ownership and authority

```text
loopx_agent_session_execution_mode_v0 =
  managed_runtime   # LoopX-owned host creates and supervises the session
  | attached_host   # external host session is bound to LoopX
```

| Boundary | `managed_runtime` | `attached_host` |
| --- | --- | --- |
| Process owner | LoopX-owned host | External host / host application |
| Session creation | By the host, before or at bind time | Before attachment; LoopX only records it |
| Conversation transport | Host adapter | The external host's existing connection |
| Execution-loop driver | Host supervisor plus bounded LoopX Turns | The external host's own loop or prompt |
| Disconnect behavior | Reconcile the process, then offer resume or restart | Report stale or disconnected; never restart the host |
| Mode fallback | Never attaches to an unrelated session | Never starts a managed runtime |
| Required authority | None beyond the operator's launch action | An existing exact host-agent binding |

Shared in both modes: goal binding, agent scope, ordered session queue,
public-safe session readback, capability advertisement, claim and completion
receipts, and the rule that only validated writeback advances work.

Forbidden alternate authorities: a host-local store as Goal, Todo, quota,
monitor, or lifecycle truth; conversation text as a receipt; a second scheduler
for the same binding; a Goal-wide default binding; and a mode change inferred
from a probe, a transport, or a prompt.

### Reusable Agent operations and continuation ownership

The existing frontend Goal Chat is the baseline conversational coordinator. The
owner may instead assign peer task coordination to a registered Agent; that role
does not itself create an Agent, transfer work authority or activate a driver.
The local steward retains cross-Goal intake and owner attention. Conversation
and peer coordinators reuse scoped delegation, independent acceptance and return.

The [explicit Goal Chat continuation](../../reference/goal-chat-continuation.md)
slice connects the composer’s explicit LoopX mode to Codex native continuation,
host-bound shared delegation, queue/inbox/steer and pause/recovery. First enable
upgrades an idle executor’s tools while preserving local history; unfinished
native Goals cannot be replaced. Member Turns retain TS acceptance authority.
Native completion does not settle the canonical Goal or report Todo. Other lead
drivers, Lark parity, unattended service and the broader multi-Agent acceptance
rows below remain separate qualification requirements.

This proposed extension refines the managed-team delivery contract; it does not
add CLI flags, promote a host, or change existing session/profile defaults.
Keep three identities separate: the registered Agent, its current host session
and execution generation, and each work request/attempt. Creating an Agent is
not starting a process, attaching a session is not claiming work, and a returned
artifact is not accepted work.

| Operation family | Existing owner to reuse | Required observation |
| --- | --- | --- |
| Discover/create/reuse an Agent | Registry, directory, onboarding and configured execution profile | Stable Agent identity, effective scope and supported capabilities; repeated creation does not duplicate the identity |
| Attach/start/resume/stop | This RFC's binding and the selected host adapter | Exact session/generation, actual running or blocked state, cancellation and replacement readback; attached hosts never acquire a substitute executor |
| Send/receive/return | Collaboration request owner and the [ingress policies](desktop-execution-frontends-v0.md#agent-scoped-bot-ingress-modes) | Requested/effective inbox, queue or steer semantics; delivery, consumption and work adoption remain distinct |
| Claim/validate/settle | Existing Todo, lease, acceptance and quota owners | Current execution proof and independent acceptance; a host cannot certify its own completion |

These are semantic operation families, not a new universal adapter API. Both a
lead Agent and an authorized managed worker may call them. Host lifecycle
differences remain explicit; a coordination role grants no extra authority.

**Continuation ownership is a separate axis from session mode and provider.**
Each binding has one qualified owner of the next execution opportunity:

- **LoopX-governed Turn:** the existing runtime/scheduler admits a complete
  bounded work unit; the host runs its own model/tool loop and returns a typed
  candidate for independent validation and settlement. Local and cloud hosts
  may implement the same contract. A Turn is not one model call or a scripted
  business phase; the Agent may investigate, delegate and revise within scope.
- **Native Goal runtime:** submit one Goal/task body and let that runtime own
  continuation, with qualified handling of LoopX continue/defer/complete,
  cancellation, budget and result readback. A prompt alone is not a scheduler;
  provider Goal evaluation does not replace LoopX work acceptance.
- **Same-session host driver:** an explicitly activated host integration can
  obtain fresh LoopX admission and enqueue the next task in the existing
  session. Qualify this separately from a provider's native Goal evaluator.

Never wrap a self-continuing native Goal in repeated externally driven Turns on
the same binding. To change the continuation owner, stop new admission, reconcile
pending tools and uncertain effects, fence the old executor, and read back the
new binding before execution. Disconnection does not authorize that switch.

For the first mixed managed cohort, qualify a cloud adapter against the same
governed Turn contract as the local worker before expanding native Goal profiles.
This is a delivery priority, not a default migration. The
[harness selection RFC](harness-selection-dsh-pi-v0.md) owns provider qualification.
Reuse the existing profile editor and session projections; do not add a
manager-only creation service, task ledger or scheduling loop.

### Model selection within Agent creation and attachment

This is a proposed refinement of the reusable Agent operations above, not a
shipped model-catalog API. Model selection belongs to an Agent's execution
profile and binding, independently of whether that Agent coordinates others.
Reuse the existing managed execution profile, subagent launch preferences and
profile editor; the steward's machine defaults are one caller's defaults, not
the universal configuration owner. A model change does not create a new logical
Agent or grant permission to launch one.

| Step | Owner and required observation |
| --- | --- |
| Discover choices | The selected executor/provider adapter reports model IDs, supported parameters, capability limits, discovery scope and freshness. Keep provider catalog presence, managed-host compatibility and account authorization separate; unknown or failed discovery is not an empty supported list. |
| Request and resolve a profile | The shared typed TS boundary validates caller scope, allowed profiles, budget constraints and explicit configuration precedence. Retain the requested model and parameters separately from resolved values and their sources. SDK/network discovery remains in the adapter; do not copy selection or admission rules into each Python launcher. |
| Create or attach | Creation uses the resolved profile through the selected adapter. Attachment observes the existing host's actual profile; it cannot silently change its model, start a replacement executor or claim that a requested preference already took effect. Repeated creation reuses the existing identity/binding contract. |
| Start and read back | Bind the profile revision to the execution generation and read back the provider-reported model and effective parameters. A visible catalog row or successful Agent-definition creation does not establish that an inference session can run. A mismatch or unsupported option produces an actionable failure, never an implicit model fallback. |

Parameter support is provider-specific: the same reasoning-effort label need
not have the same meaning across hosts, and speed, thinking mode, context limits
and tool support are not universal knobs. Use a small common selection contract
with validated provider-owned options rather than a core list of vendor models
or one global parameter enum. Credentials remain in the selected provider's
credential scope and never enter an Agent profile or public projection.

Mutable model aliases require explicit readback. Record a resolved version only
when the provider exposes it; otherwise record that the backing version is
unknown rather than treating the alias as a reproducible snapshot. Profile
changes use the existing binding revision/generation and rebind boundary;
running work retains its admitted profile until a qualified transition occurs.
Changing the parent profile does not silently change existing children. An
authorized child coordinator may choose only within its inherited profile and
budget scope, using the same operation as the lead.

The next implementation slice must connect discovery, selection, creation or
attachment, launch and readback for both a local and a cloud executor. Qualify
unsupported parameters, stale discovery, unavailable authorization, retry,
mutable aliases, and profile changes during active work. Reuse the existing
CLI, frontend and Lark configuration owners/projections where affected; a
backend field alone does not complete that user journey. Existing defaults and
explicit host choices remain unchanged until a disclosed implementation lands.

### Effective launch context and recoverable presence

Refine creation/attachment in the existing profile and binding owners; this is
proposed qualification, not a new factory API or an implemented residency policy.
Three independent inputs must remain distinguishable:

| Input | Resolution and readback |
| --- | --- |
| Context selection | A bounded semantic brief or a host-supported history projection, with source revision, coverage and omissions. A history fork is not a process checkpoint or a copy of pending tools, claims or authority. Rebuild receiver instructions; do not inherit a parent coordinator role as a grant. |
| Execution preferences | Resolve model, effort and supported tools from the current effective profile, not a stale initial configuration. The shared resolver owns explicit/omitted/clear intent and precedence; record requested, resolved and provider-observed values plus their source. A role name or parseable option is not evidence it took effect. |
| Execution authority | Reapply the current resource, tool, environment and budget scope after preference resolution. Role/context inheritance cannot broaden it. Workspace sharing or isolation is an explicit host fact; a separate model context does not imply a separate worktree or sandbox. |

Readiness requires both the actual binding's capabilities and permission to use
them. Model catalog availability, exposed tool schema, owner activation and
successful host readback are different observations. A child capable of inference
may still lack delegation or steering. Discover that before dispatch and return
the missing condition; do not repair it by silently changing provider or authority.
Use the same resolution for a steward, project coordinator and nested member.

Do not collapse identity, residency and execution into a single Agent status:

| Dimension | Existing source and invariant |
| --- | --- |
| Registered identity | Registry/directory owns identity and scope. A missing live-runtime row or a truncated display cannot prove the Agent was deleted. |
| Loaded runtime | Host supervisor observes resident, unloaded, unavailable or unknown instances. These are conceptual distinctions, not new registry enum values. |
| Active execution | Binding/Turn owner identifies the active execution and its generation. Turn completion or interruption does not itself delete identity, release a work lease or prove all descendant effects stopped. |

Any future capacity controller must declare what it counts: active executions,
resident instances, pending reservations and root inclusion are distinct from
registered identity count and display limits. Do not introduce eviction merely
to expose these observations. If a host later supports unloading, qualify it only
after no active execution/pending input and a durable recovery basis are verified.
Capacity exhaustion returns an explicit disposition; it does not promise a queue.

Creation retries first reconcile the original operation and provider identity;
a lost response cannot justify a second runtime. Recovery rechecks binding
version/generation, current scope, environment and durable context before an
execution effect. Restoring an identity roster does not start every member.
Unavailable history or an unsupported resume leaves a specific gap and preserves
pending work; use the separately qualified replacement path, never a guessed
session or broader policy. This reuses the existing operation journal and
supervisor, not another lifecycle ledger.

The creation/profile slice must exercise changed parent preferences, unsupported
child tools, filtered/compacted context, shared workspace assumptions, ambiguous
creation response, unloaded versus absent identity, and revoked scope on resume.
Use actual local and cloud adapter readback; list length and role prose are not
acceptance evidence.

### State model and schema

The binding is the unit of mode ownership. Its canonical fields:

| Field | Requirement | Semantics |
| --- | --- | --- |
| `session_id` | required, opaque | LoopX binding identity; not a host session id |
| `goal_id`, `agent_id` | required, opaque | Exact Goal and registered Agent scope |
| `session_mode` | required, closed set | `managed_runtime` or `attached_host` |
| `executor_endpoint_id` | required | Identity of the executor, distinct from `agent_id` |
| `host_surface` | required for `attached_host` | Named host surface used for exact admission |
| `attached_capabilities` | optional, closed keys | Only meaningful for `attached_host`; empty for `managed_runtime` |
| `channel_id` | required | Ordered conversation channel for the binding |
| `status`, `active_turn_id`, `last_error_code` | projected | Current lifecycle readback, including typed failure |

Legacy rows without `session_mode` read as `managed_runtime`; unknown values are
rejected, not coerced. Adding a mode, a capability key, or a binding field is a
versioned contract change, and removing one requires the compatibility and
approval evidence the RFC index requires. Public projections never carry host
session ids, transcripts, message bodies, credentials, or local paths.

### Command and event lifecycle

Attached binding:

1. **Bind.** An exact `(Goal, registered Agent, host surface, host session,
   executor endpoint)` tuple is bound. Re-binding the same tuple is idempotent;
   a different tuple for the same binding conflicts instead of silently
   replacing a live route.
2. **Claim.** The host claims the oldest queued message with a bounded wait
   (capped at 1800 seconds). A timeout returns `claimed=false`; the host decides
   whether to subscribe again. Claim never starts, resumes, or replaces a
   runtime.
3. **Complete.** Completion references the exact claim and a stable completion
   id, then readback returns the response through the existing Chat turn and
   reply path. A late or duplicated completion is rejected, not replayed as new
   work.
4. **Close.** Closing the binding removes the route without deleting LoopX work
   state.

Managed binding: the host creates the session, launches and supervises the
runtime, advances work through bounded Turns, validates each result, commits
accepted state, and reconciles process state on disconnect before offering
resume or restart. The host must not attach to an unrelated session to look
healthy.

Fail-closed rules for both modes:

- a second executor start for an active binding is refused with a typed error;
- a delivery capability the binding does not advertise is refused, never
  silently re-routed;
- an ambiguous or missing completion receipt leaves the turn unresolved and
  requires reconciliation, not replay;
- an unknown mode, capability key, or receipt version is refused rather than
  interpreted;
- no timeout, missing response, or stale readback implies that an effect did
  not happen.

### Host admission contract

A host, including an optional preview host, must satisfy all of the following
before it may bind sessions to LoopX:

1. declare one mode per binding and persist it with readback;
2. register its Agent (or reuse an exact registered `agent_id`) before binding,
   with an `executor_endpoint_id` distinct from the Agent identity;
3. never run a second executor for a bound Agent, including after a restart,
   crash, or manual relaunch;
4. route external input through the LoopX ordered session queue or another
   declared ingress path instead of a private inbox that duplicates Todo
   authority;
5. treat conversational output as a proposal until the user confirms and LoopX
   writes the state;
6. validate a result independently before journaling it or using it as
   progress;
7. keep host-local storage non-authoritative for Goal, Todo, quota, monitor,
   and lifecycle state;
8. expose public-safe typed readback: mode, capabilities, status, and stable
   error codes, without host session ids, transcripts, credentials, or paths;
9. remain opt-in and removable without a LoopX schema migration.

A host that cannot satisfy these requirements may still read LoopX state, but
it may not claim an executing session binding.

### Capability and delivery-mode orthogonality

Six axes that are frequently confused, each with one owner:

| Axis | Values | Owner |
| --- | --- | --- |
| Execution mode | `managed_runtime`, `attached_host` | This RFC |
| Continuation owner (proposed) | LoopX Turn driver, native Goal runtime, same-session host driver | This RFC; qualification per selected profile, never inferred from provider or location |
| Transport | web chat, Lark, CLI | Desktop frontends RFC; transports never change mode |
| Event source | group message, document comment, monitor observation, inbound file | Connector and collaboration contracts |
| Ingress/delivery mode | `live_steering`, `session_queue`, `async_inbox` | Desktop frontends RFC, gated per binding |
| Host-mode selection | `visible_tui`, `isolated_headless_turn`, `im_gateway`, `shell_service`, `hybrid_handoff` | [Host mode plan v0](../../reference/protocols/host-mode-plan-v0.md) |

Adding or removing a transport or event source does not create, replace, or
migrate a session. Changing mode is a separate explicit operation with its own
receipt. Selecting a host mode does not authorize a session mode.

## 6. Ownership map across RFCs and protocols

Read this table before changing session, execution, or collaboration behavior.
If a change touches a row's owned boundary, update that document instead of
extending this RFC with a competing rule.

| Document | Owns | Relationship to this RFC |
| --- | --- | --- |
| [Desktop execution frontends](desktop-execution-frontends-v0.md) | Desktop product shape, Mode A/Mode B product comparison, Web/Lark convergence, connector and bot ingress model, optional computer use, Desktop delivery slices | Source of the mode comparison used here. This RFC extracts the owner-agnostic session-execution contract; that RFC keeps frontend product flows and should reference this one for mode admission. |
| [Single-owner local daemon](single-owner-local-daemon-v0.md) | Service-profile identity, readiness, supervised composition, lifecycle receipts, migration | Owns *process and service* ownership for LoopX components. A managed host may run as a supervised service only under that RFC; this RFC does not create daemons, listeners, or endpoints. |
| [Capable manager and semantic handoff](capable-manager-semantic-handoff-v0.md) | Manager capability, semantic handoff, session and product continuity (§5.7), continuation path selection, result return | Owns continuity across sessions: same-session resume, same-Agent replacement, cross-Agent takeover. This RFC owns the mode tag and binding ownership; a handoff may not change mode implicitly. |
| [Manager runtime profile](manager-runtime-profile-v0.md) | The manager's effective runtime profile: sandbox, prompt, managed workspace instructions, configuration revision and readback agreement | A managed-mode profile detail. This RFC requires the mode to be explicit and read back; profile content and its approval stay there. |
| [Harness selection: DSH and Pi](harness-selection-dsh-pi-v0.md) | Evidence-backed selection and qualification of managed runtimes and observation lanes | Owns *which* runtime qualifies for managed execution. This RFC owns only the fact that a managed session is LoopX-owned and how it is bound; it promotes no runtime. |
| [Explicit continuation (Stage A)](cross-session-memory-substrate-v0.md) | Shipped continuation note CLI, its same-host registered-Agent restrictions, and revision-guarded ownership adoption | Continuation is a different operation from a mode change. Its restrictions remain; a continuation note is not a session-mode migration. |
| [Shared goal authority](shared-goal-authority-state-provider-v0.md) | Cross-host authority, claim, lease, fence, and provider qualification for shared goals | Owns cross-host work ownership. This RFC's "one active executor per binding" is local binding serialization and creates no second claim or lease authority. |
| [TypeScript control-plane migration](typescript-control-plane-migration-v0.md) | Which surface owns typed state machines, effects, and settlement rules during migration | Mode transitions that become machine-enforced belong to that typed boundary. Python adapters may bridge the contract but must not fork session-mode authority. |
| [Goal Channel collaboration](goal-channel-collaboration-v0.md) | Goal-bound collaboration surfaces, channel delivery, notifications | Owns where collaboration is delivered. This RFC owns which session binding the delivery targets and whether that binding can accept it. |
| [LoopX Turn v0](../../reference/protocols/loopx-turn-v0.md) | The bounded governed Turn: decide, execute, validate, write back, spend once | The execution unit used by managed hosts and by attached hosts that return governed results. This RFC does not extend the Turn contract. |
| [Turn loop controller](../../reference/protocols/turn-loop-controller-v0.md) | The pure disposition transition for continued Turns and its budget semantics | Decides whether another Turn is eligible. It does not own a session, start processes, or schedule work. |
| [Host mode plan](../../reference/protocols/host-mode-plan-v0.md) | User-facing host-mode selection from intent and advertised capabilities, and the preview command it prints | A different axis: how work advances and through which connector. It is not a mode authority; a selected host must still declare its session mode per this RFC. |
| [Session runtime projection](../../reference/protocols/session-runtime-loopx-projection-v0.md) | The read-only projection of an external runtime session into LoopX without copying private traces | Consistent with `attached_host`: the runtime owns transcripts, LoopX owns its own state. This RFC adds no projection fields. |
| [Session runtime controlled writeback](../../reference/protocols/session-runtime-controlled-writeback-v0.md) | Compact writeback of LoopX decisions into external runtime metadata after projection exists | Owns runtime-metadata writeback. A session binding is not a writeback channel for Goal truth. |
| [Codex CLI TUI loop](../../product/runtimes/codex-cli/codex-cli-tui-loop.md) | Host-side loop guidance for one visible runtime, including session-attached automation and resume options | Implementation guidance for one host. It carries no mode authority and does not define admission. |
| [Attached Agent session broker](../../integrations/attached-agent-session-broker.md) and [worker-bridge install contract](../../integrations/worker-bridge-install-contract.md) | The shipped broker CLI semantics, install and operation contract | The operational reference for the shipped attached binding that this RFC's Section 4 audits. |

Two consequences worth stating explicitly:

- The Desktop RFC remains the product-level proposal for its frontend. This RFC
  does not supersede it, and a conflict about Desktop screen flow, connectors,
  or computer use is resolved there.
- The manager, continuation, and handoff RFCs own *continuity*. This RFC owns
  *identity*: which session, in which mode, under which Agent, is the current
  executor. Continuity operations consume that binding; they do not redefine it.

## 7. Alternatives and design choices

| Option | Benefit | Cost / decision |
| --- | --- | --- |
| Infer the mode from environment, host probes, or conversation | No explicit binding field; less setup | Ambiguous with a live external host, allows silent second executors, and cannot be projected honestly. Rejected. |
| One implicit universal host adapter | Fewer concepts for integrators | Hides the ownership difference that causes the failures, and pushes host-specific lifecycle into a fake common layer. Rejected. |
| Auto-migrate `attached_host` into `managed_runtime` when the host disconnects | Appears to keep work moving | Creates a second executor and a new session without operator intent. Rejected; the host may report stale and offer an explicit, receipted replace operation. |
| Treat conversation as sufficient writeback | Simple for preview hosts | Breaks the validation and receipt contract and makes chat text authoritative. Rejected. |
| Explicit mode plus capability advertisement per binding | Fail-closed, projectable, admits new hosts without new authority | Requires hosts to declare and read back state, and requires typed unavailability handling. Chosen. |

## 8. Safety, privacy, and compatibility

- **Default-off and feature-off parity.** Existing managed sessions keep their
  behavior. An installation that never binds an external host sees no new
  required endpoint, process, or field. A host without the mode contract is
  treated as a reader, not as an executor.
- **Authorization.** A session mode is not a permission grant. It grants no
  Goal, Todo, quota, gate, evidence, filesystem, provider, or messaging
  authority, and it does not weaken the operator's existing grants.
- **Public and private boundary.** Public readback carries mode, capabilities,
  status, and stable error codes. Host session ids, transcripts, message
  bodies, prompts, credentials, provider payloads, and local paths stay out of
  public projections and out of committed evidence.
- **Legacy and mixed-version readers.** Legacy rows without a mode read as
  `managed_runtime`. Unknown mode values, capability keys, and receipt versions
  are refused. A client that cannot express the mode must not bind a session.
- **Split-brain prevention.** Per-binding serialization plus explicit mode
  prevents two executors from advancing one Agent. Cross-host coordination
  stays with the shared-authority contract; this RFC adds no parallel fence.
- **Fail-closed choices.** Unavailable delivery, ambiguous receipts, and
  unknown contracts fail closed. A bounded wait that expires reports
  unavailability and returns control to the operator or host; it never
  triggers a fallback executor.

## 9. Migration and rollback

Admission of a new host:

1. declare the mode, the Agent scope, the executor endpoint, and the capability
   set, and prove readback;
2. prove one-executor behavior under restart, crash, and concurrent launch;
3. prove that conversational output alone does not change Goal or Todo state;
4. prove that host-local state is not a competing authority for Goal, Todo,
   quota, monitor, or lifecycle status;
5. install as opt-in with an explicit launch action.

Rollback for an attached host: close the binding, stop the host, and verify the
route is gone. Rollback for a managed host: stop the supervised process and
reconcile any in-flight turn before another executor starts. Both rollbacks
preserve LoopX work state; neither requires a LoopX schema migration, and
neither deletes accepted work.

An optional preview host is removable by deleting its own package and private
data directory once no binding remains. Nothing in this RFC authorizes an
automatic mode migration, an automatic session replacement, or a silent
adoption of a host-local record as LoopX state.

## 10. Validation and acceptance

Rows marked shipped are current evidence. Rows marked unverified are required
future evidence and must not be reported as green.

| Claim | Test / evidence | Required result | Boundary |
| --- | --- | --- | --- |
| Mode is explicit and persisted | Create managed and attached bindings, then read back the projection | Exact mode and host surface returned; unknown mode refused | Shipped at the audited baseline |
| No implicit mode change | Reconnect, reconnect after host restart, and re-bind the same tuple | Same mode and binding identity; different tuple conflicts | Shipped for the broker admission path |
| One executor per binding | Concurrent claim, duplicate start, restart during an active turn | One active executor; duplicates fail closed with typed errors | Partially shipped; managed restart paths need explicit rows |
| Capability fail-closed | Request a delivery capability the binding does not advertise | Typed unavailability such as `attached_session_live_steering_unavailable`; no fallback executor | Shipped for `live_steering` |
| Bounded host wait | Claim with and without an available message, and past the wait cap | Returns `claimed=false` at the bound; never starts a runtime | Shipped at the audited baseline |
| Public-safe readback | Inspect projections and receipts for host ids, transcripts, paths | No host session id, transcript, message body, credential, or path | Shipped for the Chat projection |
| Conversation is not a receipt | Host replies with an agreeing message and no writeback | No Goal or Todo transition; projection shows the result as unresolved | Unverified for new hosts |
| Host-local state is not authority | Inspect host storage for Goal, Todo, quota, monitor, lifecycle copies | No competing status; journals are replayable results, not authority | Unverified for new hosts |
| Admission under restart and crash | Kill the host during execution with a bound session | No orphan executor; replacement binding has one owner and correct mode | Unverified for new hosts |
| Feature-off parity | Run without any attached binding and without the preview host | Existing managed behavior, routes, and defaults unchanged | Unverified for new hosts |

Deterministic package tests are not sufficient for admission. A new host needs
at least one real-host row: a live process, a real bind, and a real restart.

The proposed continuation extension also requires an installed local/cloud pair
to use the same admission/result/independent-validation contract, a managed
worker to request and adopt another worker's artifact, and a driver-switch race
to reject the old executor. Test ordinary waiting, pending tools, budget
exhaustion and native Goal defer/terminal handling separately. None of these
rows is qualified by registration, HTTP acknowledgement or a fixed phase script.
Preserve feature-off behavior for every existing profile and entrypoint.

## 11. Operational contract

- **Observability.** Session readback exposes mode, host surface, executor
  endpoint, capabilities, status, active turn, and a stable error code. Claims
  and completions are individually inspectable without a working UI.
- **Typed failures.** Stable reasons include a missing or unknown mode, a
  missing host surface, an unavailable delivery capability, a conflict on
  re-bind, an expired claim, and a rejected duplicate completion.
- **Bounds.** Claim waits are bounded and never hold a runtime open. Host
  launch, restart, and reconciliation attempts are bounded, with backoff, and
  exhaustion leaves an observable failed state instead of an unbounded retry
  loop.
- **Operator actions.** When a binding is stale, the operator either restores
  the same host session under the same mode or performs an explicit, receipted
  replacement. No operator action silently changes the mode or deletes work.
- **Capacity.** One ordered queue per binding; queue depth and expiry are
  bounded and reported. Full or expired queues fail closed on the ingress side
  and never move work to another executor.
- **Recovery.** An interrupted executor turn is reconciled before retry. A
  validated result is journaled before lifecycle writeback.

### Long-horizon managed route (2026-09-16)

[Roadmap](loopx-overall-roadmap-v0.md) R2 first qualifies a steward and 2–3 actual managed workers across Turns; R6 qualifies local/cloud execution on one authority, then R7 expands active scale. M1–M4 here retain host admission ownership. Team-plan `ready` cannot replace binding, qualification, claim/lease or actual process readback.

DSH steward Chat is currently single-segment, read-only and without cross-turn host sessions; `turn run-once` is a separate bounded execution path. The next slice proves successor wake, cancellation/stop, crash recovery and returning stale-executor fences with packaged frontend/CLI/Lark readback. An executor name, one segment or multiple registrations cannot establish continuous managed execution. Disconnection never switches attached hosts to managed, and unqualified hosts retain their existing boundary.

The opt-in [local delegation interface](../../reference/local-delegation.md)
now provides durable operations around bounded Turns, including member-to-member
launch grants and TS task acceptance. Its Ark process-loss drill resumes the
original Session/input after cloud tool waiting; it does not resend acknowledged
effects or reset the deadline. This qualifies local execution recovery, not
successor wake, attached-host takeover or full fleet cancellation. Provider file
profiles preserve the existing model/tool configuration boundary; the general
Agent creation/model discovery proposal above remains separate.


## 12. Normative delivery plan

| Milestone | Shipped behavior | Entry gate | Exit evidence | Rollback |
| --- | --- | --- | --- | --- |
| M0 | This RFC: the normalized mode contract, admission requirements, and the ownership map | Audited baseline and maintainer review | Merged RFC; no runtime change claimed | Documentation revert |
| M1 | Step 1 minimal prototype: an optional local host that declares its mode, binds one Agent, keeps LoopX authoritative, and drives bounded execution with independent verification and conversational setup | M0 merged; the open process-cleanup review finding on the candidate prototype resolved by reusing the established bounded-process runtime helper | Admission rows: mode readback, one executor, conversation-is-not-a-receipt, host-local state is not authority, restart behavior, feature-off parity | Remove the optional host package and its private data directory; no LoopX schema migration |
| M2 | Attached-host host-adapter parity on a real external host session, including capability-gated delivery and typed unavailability | M0 merged; broker contract accepted | Real-host bind, claim, complete, stale, and duplicate rows | Close the binding and stop the host |
| M3 | Mode-aware projection parity across frontends with no mode inference and no second executor | M0-M2 evidence | Cross-frontend readback rows; no implicit mode change under reconnect | Revert the projection change; bindings unchanged |
| M4 | Promotion decision for the prototype host surface, or an explicit decision to keep it an optional preview | M1 evidence and product review | Recorded decision with the surfaces and audiences it covers | Retain the preview boundary |

M0 and M1 are deliberately small. M1 must bind a real Agent and a real process;
it must not add uncalled schema builders, a second scheduler, or a new runtime
authority. M2 may not change Goal, Todo, quota, or claim semantics. M3 may not
become a second source of mode truth.

## 13. Open decisions

1. **Session rotation.** Which operator-visible operation may replace or rotate
   the working session of an Agent while preserving an auditable conversation
   boundary, and which receipt proves it? Owner: maintainer. Depends on M2 and
   on the continuity path chosen by the manager RFC.
2. **Steady-state capability policy.** After a queued ingress path ships, is
   `live_steering` still a per-binding capability, and what evidence promotes
   it? Owner: maintainer. Depends on M3.
3. **Queue storage.** Which bounded owner-local store backs the ordered session
   queue, and under which conditions may an explicit re-bind preserve queued
   entries across a replaced host session? Owner: store owner. Depends on M2.
4. **Preview-host admission.** May an optional preview host bind executing
   sessions before it satisfies every admission row, and if so which rows are
   mandatory for a preview? Recommendation: allow preview binding only with
   mode readback, one-executor behavior, and conversation-is-not-a-receipt;
   keep the remaining rows as promotion gates. Owner: maintainer.
5. **Promotion criteria.** Which evidence converts an optional preview host
   into a supported frontend surface, and who owns its lifecycle after
   promotion? Owner: maintainer. Depends on M1 and M4.
6. **Mode-aware projection parity.** Should every frontend show the execution
   mode to the operator, or only the surfaces that can change it? Owner:
   frontend owners. Depends on M3.
7. **Contract placement.** Should mode transitions become machine-enforced in
   the typed control plane, and which Python surfaces then become adapters
   only? Owner: control-plane owners. Depends on the
   [TypeScript migration RFC](typescript-control-plane-migration-v0.md).

## Appendix A: Execution ledger (non-normative)

### 2026-09-15 - Contract normalized as an RFC

- **Baseline:** `6c3da75ca`
- **Delivered:** The attached/managed session-ownership decision, previously
  described only inside the Desktop frontend proposal, is restated as a
  standalone contract with admission requirements, an ownership map across
  RFCs and protocols, and a delivery plan whose Step 1 is a minimal host
  prototype.
- **Evidence:** Section 4 source audit at the named baseline; focused tests
  named there.
- **Known gaps:** every admission row for a new host is unverified; no
  cross-host admission, rotation, or promotion decision is recorded.
- **Effect on normative design:** establishes Sections 1-6; changes no runtime
  behavior.

## Appendix B: Decision log

| Date | Decision | Owner / approval | Alternatives | Normative sections changed |
| --- | --- | --- | --- | --- |
| 2026-09-15 | Session execution mode is an explicit, persisted, per-binding contract; the Desktop RFC keeps product flows, and continuity RFCs keep continuation paths | Repository owner, through RFC review and merge | Implicit mode inference; universal adapter; automatic migration to managed | Sections 1-6 |

No other decision is approved. Recommendations in Section 13 remain proposals.

## Appendix C: Evidence registry

| Evidence id | Claim | Baseline / environment | Artifact or command | Result | Privacy / validity boundary |
| --- | --- | --- | --- | --- | --- |
| E1 | The mode vocabulary is closed and attached bindings require a host surface | `6c3da75ca`, source audit | `loopx/chat_store.py` session creation and mode validation | pass (source) | Source inspection only; not a live host qualification |
| E2 | The public session projection hides host session identity and blanks attached capabilities for managed sessions | `6c3da75ca`, source audit | `loopx/chat_store.py` public session projection | pass (source) | Does not prove absence of host ids in host-local logs |
| E3 | The attached broker bounds claim waits and never starts a runtime | `6c3da75ca`, source audit | `loopx/attached_session.py` bind, claim, and complete | pass (source) | Bounds are reviewed as constants; no soak evidence |
| E4 | Attached sessions never start a managed adapter and fail closed with typed errors | `6c3da75ca`, focused test | `tests/test_chat_codex_home.py::test_attached_session_uses_existing_host_not_managed_adapter`, `loopx/chat_runtime.py` | pass (focused test) | Covers the described fence, not every delivery path |
| E5 | Bind, claim, and complete are reachable through the CLI | `6c3da75ca`, focused test | `tests/test_attached_session_cli.py`, `loopx/cli_commands/worker_bridge.py` | pass (focused test) | Synthetic host fixtures, not a real external host |
| E6 | The Desktop frontend proposal already contains the Mode A/Mode B comparison and its non-goals | `6c3da75ca`, document audit | `docs/architecture/rfcs/desktop-execution-frontends-v0.md` | pass (document) | A proposal, not shipped product behavior |
| E7 | An optional local host prototype proposes a host-owned execution surface with conversational setup | Public pull request #4376, open at review head | Public review findings on that pull request | proposed, not accepted | An open pull request is a candidate, not admission evidence |

## Appendix D: Rejected or superseded alternatives

- **Implicit mode from environment or capability probe.** Rejected: it cannot
  distinguish a live external host from a stale one, and it hides the ownership
  difference that produces duplicate executors.
- **Automatic migration from attached to managed on disconnect.** Rejected: it
  creates a second session and executor without operator intent.
- **One universal adapter that hides mode.** Rejected: host lifecycle semantics
  are genuinely different, and a fake common layer would relocate the ambiguity
  instead of removing it.
- **Conversation as an accepted receipt.** Rejected: it bypasses validation and
  writeback, and it makes the transcript authoritative.
- **Host-local Goal or Todo mirror as a status source.** Rejected: it creates a
  second authority and a divergence that the operator cannot resolve.

## Appendix E: Incident and review lessons

- A binding that exists is not proof that an executor exists. The failure mode
  that motivated this contract is a healthy-looking projection with no process
  behind it, so readback must distinguish mode, capability, status, and error
  code rather than reporting one success flag.
- A preview host is still a host. Reviewing its user-facing value is not
  enough; the review must also decide which modes, bindings, and stores it may
  own, because that decides whether LoopX can still be the single work
  authority.
- Process supervision details are contract-relevant when they can leave a live
  writer behind. A bounded execution helper that leaves a surviving child
  writing after a reported timeout is a second-executor risk, not only a
  cleanup defect.
