# Manager evidence and continuity v0

Design evolution: [Capable Agent Manager and Semantic Work Handoff RFC](../../architecture/rfcs/capable-manager-semantic-handoff-v0.md)
([中文版](../../architecture/rfcs/capable-manager-semantic-handoff-v0.zh-CN.md))
proposes the next product direction. This document retains the implemented
baseline and compatibility contract until the RFC milestones are qualified;
its restricted manager defaults are not silently changed by the proposal.

Status: staged implementation. The synchronous manager transport and read-only
`goal-portfolio` provider are implemented. Managed conversational turns now read
fresh scoped Core evidence. The shared cross-entry request ledger, optional
Decision Context adapter and complete three-goal acceptance remain unqualified;
this change does not claim those outcomes.

## Ownership and defaults

Core retains authoritative Goal, Todo, gate, quota, evidence, and lifecycle
state. The manager observes, explains, and coordinates that state. Decision
Context, when explicitly enabled, combines verified evidence with background
context to make advisory proposals. Neither consumer may store an editable
second version of "real progress" or turn its own chat summary into evidence.

The built-in manager is a stable logical role, separate from a replaceable
executor session. Frontend conversation and an addressed Lark message should
receive a synchronous conversational response within their authorized scope.
Long-running work is assigned to an exact worker with a durable return route.
The initial receipt does not end the exchange: the worker evaluates or executes
within its current authority and returns an audience-ready decision/result to
the original conversation automatically. Acknowledgement or adoption does not
remove the request from the receiver's hook; a committed conclusion does. The
Chat runtime then owns transport recovery, separately from work completion.

The expected exchange is receipt → receiver assessment/work → conclusion.
Meaningful intermediate feedback is optional and coalesced when a conclusion
is already ready. Normal delegation requires neither a second confirmation nor
a second status question. Failure, deferral and rejection are explicit results,
not silent disappearance. A direction-setting request may conclude in a plan
change; an execution request needs an execution result or explicit blocker.

The builtin manager-context capability owns this round trip; its Lark provider
reuses the existing Inbox reply/ACK path. Current Core Todos and deliveries own
work truth. Private decision reasoning is not automatically sent to a group;
the receiver supplies audience-ready reply text. The exact source grant and live
binding are checked again before transport, and a failed/unverified send never
means a successful user-visible return. See the capability README for the
receiver `read/acknowledge/link/report` workflow and recovery behavior.

Live activity labels follow the executor's typed event. Receiving a message,
reasoning, producing an answer, and invoking a tool are different activities.
An unknown item remains generic activity; item completion alone proves neither
a Goal read nor a successful check. Do not expose item bodies or tool inputs in
activity labels. Diagnose response latency using request acceptance, upstream
turn start, first answer, and completion timestamps; a silent upstream interval
does not identify a scheduler delay or prove what the executor was doing.

Reuse the existing [global-manager protocol](global-manager-command-v0.md),
[Decision Context source plane](decision-context-architecture-v0.md), and
[periodic report lifecycle](periodic-report-v0.md). The proposed Goal Portfolio
provider is a read model serving the existing manager outcome, not a new
capability merely to introduce a provider. Core owns typed state semantics;
presentation owns bounded collection and rendering. An optional
`DecisionSourceProvider` adapter can exact-read this same projection without
making Decision Context a prerequisite for basic manager reads.

Manager upgrades retain the existing connection identity, Topic and receipts.
Retiring an async inbox and saving its replacement form one compensated write
operation: hold the binding/source mutation locks, and restore the prior
binding, source registry and affected shared Goal if a write fails. Recovery
must verify those authorities before claiming the old route was preserved;
failed compensation returns `upgrade_recovery_required`. Shared-registry
recovery must retain concurrent updates to other Goals. This exception-recovery
contract does not claim crash-atomic persistence across multiple files.

## Global conversation initialization

The owner manager uses the canonical `loopx-manager` identity and an isolated
runtime workspace. Session creation requires no Goal, and selecting a Goal
cannot change its workspace or evidence scope. Every conversational turn reads
the current authorized registry through `goal-portfolio`, freezes that snapshot
in a `manager.context` event, and supplies it to the executor. Chat prose is
never the inventory. Normal manager questions no longer silently use a limited
frontend projection; explicitly choosing status-only still uses that projection.

The manager channel's model and reasoning effort follow its executor. On the
interactive CLI endpoint the defaults are `gpt-6-astra` with `high` reasoning; on
the managed host they are the managed execution profile
(`deepseek-official` / `deepseek-flash` / `high`). The machine's
`steward_executor` machine configuration decides the executor, the model and the
effort first; set `LOOPX_MANAGER_MODEL` and `LOOPX_MANAGER_REASONING_EFFORT` on
the Chat service when a machine has no such configuration, and the shipped
default applies when neither exists. A blank model or effort in the machine
configuration means that field keeps resolving from the layers below it. Thread
start, resume and turn start explicitly carry the settings; worker configuration
is unchanged. Capabilities expose the manager defaults, their source, and the
status and revision of the machine document they were read from.

The managed profile reaches the channel as the same one-line readback the
governed Turn publishes (`deepseek-flash@high`), so the channel and the
bounded Turns it drives cannot report two different managed profiles; the
provider is prepended when it is not the shipped one.

### Steward channel host selection

The steward channel resolves one machine configuration, then one service
environment value, then one shipped default. The `steward_executor` machine
configuration (`loopx/capabilities/steward_executor/machine_defaults.py`) selects
the primary executor for its machine and is the setting the Dashboard edits and
`loopx machine-config describe`/`inspect` read back; it accepts only the
endpoints LoopX ships as channel executors and stores no credential. Its
`selection_policy` is `preferred`, `pinned`, or `flexible`: preferred honors an
explicit user pick, pinned rejects substitution, and flexible permits fallback
only within its closed `eligible_endpoints` pool. Stored v0 configurations map
to preferred behavior.
`LOOPX_MANAGER_ENDPOINT` bootstraps a machine or names an unlisted adapter;
without either the channel runs the interactive CLI endpoint (`codex`) on every
machine, and the managed host (`dsh`) is reached by selecting it. The channel
reports where the endpoint came from (`executor_endpoint_source`, including
`machine_configuration`), the status and revision of the machine document it
read, and, when it is the shipped default, which decision that was
(`executor_endpoint_default_reason`, `steward_channel_default`), so the default is
disclosed rather than silent or inferred from the resolved host name.

The channel must stay reachable, which is why its default does not move: the
steward is the surface a person talks to, a managed host without its credential
cannot answer at all, and an operator whose machine has only a personal login
must still get a steward. A credential authenticates the endpoint it belongs to
and never selects one. Its presence is still reported as a fact
(`operator_credential_configured`), as the variable name and never the value,
and only for the endpoint that actually authenticates with it.

The model follows the executor, so a credential can no longer pair a managed
model with an individual CLI login: an explicitly selected managed host takes the
managed execution profile in full, and the interactive CLI endpoint keeps its
vendor model. The resolved model and effort are never discovered from a
credential.

The managed host answers through a **segment transport**: the channel runs
exactly one bounded DeepSeek Harness work segment per Chat turn on the managed
execution profile, with the channel's bounded visible history as input. That
transport deliberately claims no partial streaming, no cross-turn host session
and no tool authority: LoopX pins `DSH_PERMISSION_MODE=read-only` for those
segments, so dsh refuses a write or shell action itself instead of trusting the
channel prompt. "Exactly one" is held, not assumed: a segment is the binding's
single executor until its thread exits, so a start while one is still running is
refused with the typed `managed_host_chat_segment_in_flight` instead of quietly
running a second executor, and an answer that arrives for an interrupted turn is
discarded rather than folded into the visible history the next segment reads. A
segment that cannot run (missing credential or missing runtime)
makes the endpoint unavailable in `channel_binding` with the typed reason the
governed Turn surface already publishes, and a session request for that endpoint
fails as a typed host-tool gate with the next step instead of an unknown-endpoint
error. The steward still **drives** managed work on `dsh`: those bounded Turns
are the managed execution unit described by the LoopX Turn host selection
contract, and they remain separate from the channel the steward answers on.

The Chat capabilities payload carries this resolution in its `manager` block
(`channel_binding`): the resolved executor endpoint and its source, its executor
kind in the same vocabulary as the governed Turn surface (`individual` runs on
one person's CLI login, `managed` on an operator credential), the reason the
shipped default applied, the resolved model and its source, the managed
execution profile when the endpoint is managed, whether an operator credential is
configured, and `available`/`unavailable_reason` when LoopX can prove the selected
endpoint cannot serve this channel. `available` is `null` when the projection
makes no claim. A frontend can show which executor and model the steward channel
resolved, the selection policy, the allocation reason, and the eligible pool
without re-deriving the rule. `loopx chat-endpoint inspect-steward` exposes the
same effective binding for CLI readback.

The Chat server that opens a steward Session is an entry point, not a second
owner: it resolves the executor through the same channel decision this payload
reports. A session request that carries no executor pick lands on the resolved
endpoint and reports it back as the session's `executor_endpoint_id`, so a client
that ships with its own silent executor default cannot re-point the channel
behind its own readback. An explicit pick is accepted only when the configured
policy permits it. The chosen endpoint, model, effort, policy, reason, pool and
configuration revision are persisted with the manager Session; restart and
later machine-configuration edits do not silently reinterpret that Session.
A Goal-scoped session keeps the Goal channel's own default. The steward channel
is one conversation across whatever executor it currently resolves, so a client
reading that conversation asks for the channel instead of filtering it by an
executor the client assumed.

The flexible policy in this version reacts to endpoint availability. It does
not classify the user's prose or claim semantic task-fit routing. An Agent may
later choose an executor by passing an explicit pick, and that pick remains
subject to the same pinned or flexible boundary.

The Personal Workspace manager header renders that binding as one compact chip
(`executor · executor kind · model`). The chip is display-only: it reads the
projected fields, keeps the executor kind in the same `individual`/`managed`
vocabulary, and names an unrecognized kind as an unclaimed registered endpoint
rather than guessing. When the projection proves the selected endpoint cannot
serve the channel, the chip is marked unavailable and the header names the
reported reason -- the missing operator credential, the missing runtime, or a
rejected reasoning effort -- instead of asserting that one particular host is
required. The header also names the shipped default and the one way to move it,
so a steward an operator selected looks different from the one every machine
runs; a configured credential changes nothing visible, because it selects
nothing. A capabilities payload without `channel_binding` renders the previous
header unchanged, and an unrecognized reason stays unclaimed rather than being
rendered as a reason this build invented.

| Surface, Chinese | Shipped selection | Managed host selected |
| --- | --- | --- |
| Desktop, 1512px | [Chip](../../assets/personal-workspace/steward-execution-chip-desktop.png) | [Unavailable](../../assets/personal-workspace/steward-execution-chip-unavailable-desktop.png) |
| Mobile, 390px | -- | [Unavailable](../../assets/personal-workspace/steward-execution-chip-unavailable-mobile.png) |

Legacy managed manager sessions retain their logical identity and bounded chat
history but start a fresh executor thread in the same Codex home on first
restore. This removes inherited project instructions without importing sessions
across homes. Existing home-identity checks still apply.

External manager continuity is also bounded by the exact authorized Goal set.
An empty, missing, or changing external authorization fails before the model is
called. When the authorized Goal set changes, LoopX starts a fresh upstream
thread without the previous model history before supplying the new scope. This
prevents evidence learned under a former binding from crossing a later
authorization boundary while retaining the owner-visible local receipt history.

Global means all Goals in the current owner's registry, including stale or
unavailable entries; it does not claim discovery of unregistered remote hosts.
External manager channels resolve their current authorized Goal scope through
an injected control-plane resolver before collection and recheck it before
handing evidence to the executor. The Lark adapter uses the active connection,
exact audience, session, executor and connector identity. Missing, disabled or
ambiguous grants produce no Goal evidence; a session's old Goal is not a grant.
Changing/revoking scope during collection discards the collected snapshot.
Global scope never means broadcasting private owner context to all Lark groups.
A proposal without an exact Goal must not inherit the first visible Goal as a
write target. The current Todo proposal schema requires entering a specific
Goal for preview and apply.

Full-inventory reads reuse one Core status collection. Bounded or externally
scoped reads select their Goal before collection. Inventory and source version
changes during collection make evidence conflicting; missing sources remain
unknown. Collection is capped at 128 Goals and eight agents per Goal, with
explicit omissions. Recorded evidence references identify Core receipts, not
independent artifact verification.

Each dated delivery now includes bounded `recorded_details` from the same Core
run index: the Agent's checkpoint explanation, observed reality, path outcome,
result class, probe kind and surface identity. Missing, malformed and truncated
fields are explicit. Evidence identifiers are hashed into stable lineage refs,
with included/omitted counts; they are not artifact access capabilities.
The manager should explain these concrete recorded findings and counterevidence,
joined to the task title, rather than return only receipt IDs and future plans.
These facts retain `recorded_claim_not_independent_verification` and
`artifact_read_status=not_read`. No repository, arbitrary path, URL or transcript
reader is added. Existing owner/external scope checks and snapshot identity
cover this hydration; there is no second progress store or extra history scan.

## A bounded recent-evidence window with declared sources

The turn context reads a bounded recent window rather than one day, because a
single-day read cannot answer a week question and an unbounded one would grow a
prompt-only managed transport without limit. `evidence_window` records the
schema, the number of days, the local window bounds, the per-day and total
receipt limits, the per-day matched counts, included versus omitted receipts,
invalid records, and `receipt_detail`: each Goal's newest receipt in the window
keeps full `recorded_details`, while older in-window receipts are compacted to
their recorded outcome, result class, probe kind and surface. Receipts outside
the window are outside coverage, not evidence of no progress, and the newest
finding for a Goal is never dropped by the window bound.

The same block declares `sources`: the local registry source plus every
SSH host this machine **registered** for LoopX evidence (an `evidence_ssh_hosts`
grant on any channel), with its read status and scope — not every configured SSH
alias, since an operator's `git` host or personal jump host holds no Core state
and only adds prompt noise. Declaring a source performs no remote connection and
grants no authority. A declared but unread source is a named coverage gap. The
manager must state the window and the sources it actually read, and must not
present an unread host as whole coverage.

The window itself is a selected decision rather than a discovered fact:
`days_source` reports `product_default`, `explicit_config`
(`LOOPX_MANAGER_EVIDENCE_WINDOW_DAYS`, 1..30) or `explicit_argument`, beside
`days_default`, `days_env_var`, `days_bounds` and `days_reason`. A missing,
out-of-bounds or unreadable explicit value keeps the shipped default and reports
its reason, so a misconfiguration can neither widen the prompt nor answer a
narrower window than it declares.

## A prompt-only segment receives the declared source read

An interactive endpoint reads a declared remote source on demand and pays no
source latency. A prompt-only steward segment has no read tool, so a declared
source it never receives is a coverage gap it cannot close. In that case the Turn
owner reads the registered sources before the segment starts and adds a
`manager_remote_evidence_v0` block, with `remote_read` declaring `inline_in_prompt`
rather than `on_demand_tool` in `evidence_window`.

The read is bounded so it cannot slow every Turn: one dial per Turn, at most two
hosts, nine seconds per host inside a ten-second Turn budget, and eight portfolio
rows per host. A read inside `ttl_seconds` (ten minutes) is served from cache
instead of dialling again, and cache entries are keyed by host, window and the
exact grant scope, so a changed grant or window re-reads rather than answering
from a narrower cached read. The read reuses the same `read_remote` path and its
before/after scope checks, so it never widens authority. The declaration and the
read use the same SSH configuration, so one packet cannot call a host
unconfigured and read it in the same Turn.

Because one Turn dials one host, which source receives that dial rotates: a
source that was never read goes first, then the source whose successful read is
oldest. Declaration order never decides it, so a source listed last cannot be
deferred on every Turn that arrives after the cache TTL. The packet still
reports its sources in declaration order, so the rotation chooses the dial
rather than the shape of the answer.

Every declared source carries a typed outcome in the same block: `read` or
`cached` with `read_at` and `age_seconds`; `unavailable` with its reason, the last
successful read and a `coverage_effect`; `not_configured` for alias drift; or
`deferred_budget` with the last successful read. A failed read keeps its last
successful rows only as `source_freshness: "stale"` with
`remote_source_rows_are_stale` in `limitations`. A stale or unavailable source is
a named coverage gap: it must never be presented as current progress, and it must
never be read as no progress. A source read failure never fails the Turn.

## A bounded portfolio with explicit coverage

Discover Goals from the authorized registry inventory, including unavailable
registered hosts. Do not infer inventory from recent chats, a limited dashboard
list, or only Goals present in the attention queue. Bind every row to opaque
host, project, Goal, registered Agent, and task references. Missing Agent/task
identity is explicit unknown, never guessed from a display name.

For each discovered Goal, collect compact canonical status, typed Todo/gate
projections, and evidence-backed delivery history. Existing global-todos and
global-risks classification rules remain authoritative at their owning seam;
the portfolio must not duplicate them in prose or an LLM prompt.

The proposed projection records:

| Component | Required meaning |
| --- | --- |
| Identity | Exact host/project/Goal identity and verified Agent/task relations |
| Provenance | Source reference, revision or digest, source observation time, collection time |
| Work | Recent verified deliveries and evidence refs, separately from accounting and liveness |
| Readiness | Typed runnable, awaiting acceptance, dependency blocked, or unknown; explicit source relation |
| Owner attention | Only verified owner actions/gates, with affected scope and why the Agent cannot proceed |
| Quality | Fresh, stale, conflicting, or unreadable facts; affected fields and reason |
| Coverage | Inventory source/revision, discovered, attempted, verified, omitted counts and omission reasons |

Coverage counts apply to the visible authorized inventory. Verification quality
partitions discovered Goals into verified, stale, conflicting, unreadable, and
uninspected categories; readiness is a separate axis. Pagination, time budgets,
unreachable hosts, and permission restrictions remain visible. If discovery
itself fails, the total is unknown, not zero. A verified empty inventory is
distinct from a failed empty response. Hidden Goal identities are not revealed
to an unauthorized audience merely to explain coverage.

A collection is not an atomic cross-host snapshot. Freeze its source revision
vector and time window. If a source changes during collection, boundedly retry
or mark the affected row inconsistent. Older evidence may remain visible with
its timestamp but cannot prove current absence of progress. An unreadable or
stale Goal must say "current progress unknown", not "no progress".

Progress selection uses typed delivery/evidence relations and stable outcome
identities. Select material outcomes before applying the display limit;
quota charges, wake-ups, polling, and report generation belong in supporting
accounting. A newer quota event must not evict an older genuine delivery.
Only a complete, fresh, bounded comparison may assert no new verified delivery
within that observed window; it says nothing about work outside coverage.

## One service, scoped continuity

The durable manager identity is independent of session process lifetime.
Conversations bind an authorization scope, audience policy revision, and
logical conversation ID. Default frontend private and external group scopes
remain separate. Transport membership alone does not grant access to another
Goal, personal financial context, or private company material.

Where an owner has explicitly established equivalent authorization scopes,
both entrypoints may reference the same logical conversation, frozen evidence
packet, request identity, and action receipt. "Synchronous" means a response
in the active conversation and consistent authorized state; it does not mean
mirroring all transcripts between groups. The current audience-hashed session
foundation provides separation, not proof of this cross-entrypoint continuity.

Evidence selection and audience filtering occur before model context and
rendering. Frontend and Lark views of the same snapshot and scope must agree on
facts, evidence, coverage, and uncertainty; wording may differ. Different
scopes may reveal different subsets and must not imply equal coverage. Caches
are disposable derived views keyed by source revisions and audience policy,
never lifecycle authorities.

## Requests, authority, and recovery

Reuse Core typed preview/apply commands and existing connector receipts.
Extend their owning boundary with a durable request envelope only where a
real cross-entrypoint call site needs it. Its identity binds an origin request,
logical conversation, verified requester/scope, exact target tuple, action,
payload digest, authorization reference, and expected target revision.

Transport event identity deduplicates transport retries. Explicit forwarding
or handoff carries the same origin request ID across entrypoints. Independently
typed similar messages are not safely deduplicated by text or time heuristics;
without shared identity, propose and confirm the intended action rather than
guess that a second execution was authorized. Changed payload with a reused
request ID is rejected. Recheck current scope and target revision before apply.

The action lifecycle distinguishes proposed, authorized, dispatched, confirmed,
failed, and outcome-unknown. Persist the dispatch intent before its effect and
bind the resulting receipt to the exact target. Replays return that receipt
without reapplying. After a crash with an uncertain external effect, reconcile
by provider idempotency/readback before retry; if the provider cannot prove the
result, retain outcome-unknown. Do not promise universal exactly-once effects.

Default manager duties allow authorized reads and summaries; reminders use an
existing authorized destination/subscription. Goal changes, steering,
cross-Goal dispatch, pause/resume, publishing, and trading keep their existing
operation-specific authority. A special role grants no extra permissions.
No new confirmation is needed where a valid standing grant already covers the
exact action. Missing grants or ambiguous targets never cause broadcast.

Periodic report persistence stays in `periodic_report`: freeze the evidence
snapshot and period, retain per-audience/sink delivery status, and commit the
publication cursor only after required delivery readback. Keep source scan,
decision review, report generation, and publication cursors distinct. A failed
optional sink does not erase a successful required-sink receipt. Restart resumes
pending delivery with the original report identity and reconciles uncertain
sends. Late-arriving material evidence remains eligible for the next report.

## Five acceptance scenarios

Use three owner-authorized real Goals from different projects. Keep actual
identities, source payloads, and receipts in ignored private qualification
state; public fixtures use synthetic peers. Never corrupt a live Goal to
simulate staleness or failures.

| Scenario | Required evidence |
| --- | --- |
| Ask what advanced today through both entrypoints | Same authorized frozen snapshot: matching material outcomes, evidence refs, coverage and uncertainty; exact channel readback |
| Material work followed by many accounting events | Real delivery retained after ranking and truncation; quota entries identified only as supporting accounting |
| One stale/unreadable Goal | Named authorized coverage gap and unknown current progress; no healthy/unchanged inference; controlled replay plus actual source observation |
| One authorized steering forwarded across entrypoints | One exact target transition; shared request ID and receipt; duplicate replay, wrong target, changed payload and revoked grant rejection |
| Restart after report dispatch | Resume from persisted per-sink receipt; no duplicate confirmed send; uncertain send reconciled; late major evidence included subsequently |

Mocks and transport tests support these checks but do not replace real source,
session, routing, and delivery qualification. Keep each scenario pending until
its evidence is recorded. Frontend/Lark equality is assessed only within the
same authorized scope; denied content must never reach model context.

## Delivery sequence and separate accounting

Host automations are generated by `loopx heartbeat-prompt --thin`. They bind
execution identity and wake the host; they do not contain domain priorities,
report calendars, source lists, strategy rules, or a private dispatch loop.
Goals and typed Todos own work and dependencies; capability profiles own
specialized behavior; providers and hooks execute their declared contracts.
The existing generator binds one Goal/Agent. A host allowing only one heartbeat
per conversation therefore needs a generic, explicit multi-Goal dispatch
contract before that one heartbeat can claim to drive a portfolio. Do not
silently drop a Goal, concatenate conflicting per-Goal lifecycle prompts, or
create a shadow scheduler to disguise the missing contract. Qualification must
prove authorized bindings, isolated quota/receipts, bounded selection and
fairness, and that one Goal's pause hint cannot stop other eligible work.

1. Finish the synchronous manager/worker default and in-place connection
   migration foundation. Qualify installed session routing; do not label it
   complete portfolio or reporting support.
2. Implement the bounded portfolio at the existing manager read boundary;
   prove coverage, evidence selection, freshness, and failure semantics first.
3. Feed the frozen projection to frontend and Lark, and optionally the existing
   Decision Context source interface; verify scoped consistency.
4. Close cross-entrypoint request identity and exact-target steering recovery
   through existing typed control contracts.
5. Extend existing periodic report recovery and complete all five real-goal
   acceptance scenarios before claiming reduced operator attention.

Track domain research outcomes, manager attention reduction, and reusable
product delivery separately, each with its own Goal, budget, evidence, and
next action. One delivery spends once against its accountable Goal; another
Goal may reference the evidence without claiming a second outcome. Protect a
recurring domain-validation slot in the owner's plan. Infrastructure progress
does not prove a research hypothesis or improve an investment result.

### Current work details and default manager instructions

Each conversational turn also reads current Todo records through Core's
canonical-first `list_goal_todos` path for each authorized Goal. This read is
independent of the age of progress receipts. It retains bounded task titles,
owner gate/action distinctions, declared priority and target/dependency IDs;
owner conversations also receive bounded continuation notes. Terminal items
are excluded and active/included/omitted counts are explicit. Read failures
mean unknown, never an empty healthy queue. The evidence is a frozen projection,
not a second Todo store or a fresh claim that old tasks remain urgent.

The neutral manager workspace receives managed `AGENTS.md` instructions and the
same role contract is supplied to its executor. Custom owner instructions are
not overwritten. The default manager must connect concrete owner decisions to
affected work and explain its recommended order; bare Todo IDs, Goal ordering
and gate counts are insufficient prioritization evidence. It distinguishes
current declarations from verified execution and names unavailable/truncated
details. External audiences retain their existing Goal authorization boundary
and do not receive owner continuation notes. No repository browsing or write
permission is added to the manager model.


## Intent delegation and worker-owned planning

The default manager interaction is intent delegation: the owner expresses an
objective, new information or constraints; the manager routes the original
message to an exact registered worker; that worker assesses its current Goal,
evidence and commitments, decides whether to replan, and reports its decision.
Todo editing is an internal planning operation, not a required user interaction.
Ordinary authorized delegation does not require a second preview confirmation.

The built-in `manager-context` capability supplies a private durable inbox and
uses the existing turn-start hook contract. The Chat host, not model prose,
writes the original message and verifies its receipt. The model can select only
`context_handoff={goal_id,agent_id}` from the supplied recipient catalog; it
cannot supply replacement text, priority or Todo edits. Delivery does not
interrupt an active turn, change scheduling, or claim the worker finished.
The next existing worker turn reads pending context before choosing work; the
worker can adopt, defer, reject or retain its plan, recording a reason through
`manager-inbox acknowledge`. Delivery and decision are separate receipts.
Core remains the only authority for actual Goal/Todo/progress state.

Owner-local manager conversations use registered recipients by default. External
manager channels require provider-recorded sender/source provenance plus an
exact sender/recipient grant in private runtime configuration. Recipient routing
does not expand the channel's Goal evidence read scope. Revocation is rechecked
at delivery. See [manager context configuration](../../../loopx/capabilities/manager_context/README.md).
Missing or ambiguous targets require resolution, not an invented recipient.
Trading, payments, publishing and other protected operations retain their own
authority requirements; forwarding context supplies no additional authority.

Requests are idempotent by original source identity and exact recipient, never
by text similarity. Different independent frontend and Lark requests remain
different requests: this implementation does not claim automatic cross-entry
origin correlation. A delivered request remains deduplicated after worker
acknowledgment and service restart. Full three-Goal live acceptance remains open.


### Lark receipt feedback

After a routed manager message is durably captured, the synchronous ingress
uses the existing inbox reaction ledger to add the configured received emoji
(default `Get`) before waiting for the model. Replayed source messages reuse the
same receipt. Emoji failure is diagnostic and does not suppress the answer;
verified final reply performs the existing reaction cleanup and message ACK.
This received indicator is distinct from internal processed-message ACK and
from downstream work completion. Provider sender identity is preserved through
canonical event conversion into the manager handoff provenance record.

Listener registration alone cannot prove upstream message delivery. If provider
history contains an addressed message but the bus received count stays zero,
record that gap explicitly; do not report a missing event as successful intake.
