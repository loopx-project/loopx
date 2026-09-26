# RFC: Automatic Execution Admission (v0)

- **RFC status:** Accepted
- **Supersedes / closes:** none
- **Delivery maturity:** Partial, proposed implementation; not promoted
- **Owners:** Quota, scheduler and host-runtime maintainers
- **Created / last normative revision:** 2026-09-23
- **Implementation baseline:** `79241d7ef`
- **Language mirror:** [中文版](automatic-execution-admission-v0.zh-CN.md) is the semantic mirror.
- **Related contracts:** [roadmap](loopx-overall-roadmap-v0.md), [quota](../../quota-allocation.md), [cadence hint](../../operations/long-task-cadence-policy.md), [session execution modes](agent-session-execution-modes-v0.md)

Sections 1–12 define the proposal and acceptance contract. The appendix records
implementation boundaries; implementing a milestone does not approve the RFC.

## 1. Decision summary

Put the owner-configured minimum automatic execution interval in the **typed
quota admission boundary**. Scheduler backoff consumes this constraint; it
cannot rewrite it. A quota slot, a timer tick and a model invocation remain
different events. The target design requires every controlled new host invocation to pass temporal
admission as well as the existing budget, permission, binding and work gates.

No configured interval preserves existing behavior. M1 changes Codex App schedule
recommendations, including reset and backoff. M2 adds pre-host admission to managed
`turn run-once`; App timer and other launchers remain separate qualification work.
Every new managed host invocation and failed-result retry passes admission; cached
settlement does not.
This RFC does not authorize changes to existing automations, model selection,
quota allocation, remote services or public publishing.

## 2. Problem and invariants

An owner asks for one automatic run per day. A new Todo or a failed host resets
adaptive backoff to three minutes; a host adapter clips the interval to one
hour. The owner now pays for repeated wakeups despite the daily intent.
Notification cooldown cannot solve this: suppressing a message does not suppress
model execution.

Invariants:

- Owner constraints survive reset, replan, retries, restart and model changes.
- Concurrent ticks share durable admission; missed ticks coalesce, never burst.
- A failed launch consumes its admitted interval. An ambiguous launch is not
  refunded automatically; conservative waiting is preferable to duplicate cost.
- Temporal admission never grants budget, permission, a work claim or settlement.
- Manual execution requires explicit intent and bypasses only the interval.
- A host without a pre-model hook cannot claim to prevent model wakeup charges.

## 3. Scope and non-goals

Scope: single-runtime-root Goal/agent/automation interval policy, controlled
launch admission, scheduler projection and host guarantee/readback. Cheap
source collection remains separate from paid model decisions.

Non-goals: distributed reservations, exact provider token caps, a new daemon,
a second quota ledger, generic workflow redesign or automatic host activation.

## 4. Audited baseline

Quota owns work eligibility and validated spend. Scheduler hints own adaptive
cadence and host ACKs, with a legacy 60-minute App backoff cap. Managed
`loopx turn run-once` owns host dispatch, recovery and settlement. Codex App
owns its timer; an in-model LoopX guard runs after model startup. Runtime hooks
are a separate potential pre-model boundary, investigated below.
Existing compute-budget and replan settings do not represent a minimum
execution interval. Long-task cadence is advisory, not an owner constraint.

## 5. Architecture and state

### One decision owner, separate responsibilities

| Boundary | Responsibility | Does not establish |
| --- | --- | --- |
| Quota configuration | Owner floor and revision; inherited scope | Actual usage or permission |
| Quota admission | Atomic start reservation before controlled invocation | Successful outcome or quota spend |
| Scheduler | Next eligible wake and adaptive backoff | Authority to reduce a floor |
| Turn executor | Launch, bounded invocation, recovery, settlement | Host capabilities it cannot enforce |
| Host adapter | Apply and read back actual timer; report limitations | Zero-cost waking without pre-model admission |
| Usage/notification | Observed spend and attention policy | Execution permission |

Admission is the intersection of existing gates and temporal eligibility.
The floor is start-to-start, not completion-to-start. Goal defaults apply
**per agent**, not as one global daily slot shared by every agent. Agent rules
constrain all its automations; automation rules narrow one identified lane.
The effective floor is the maximum applicable rule. A narrower rule cannot
weaken its parent; lowering requires changing the owning scope explicitly.

The target local admission profile stores policy and starts together under the existing
atomic file mutation machinery. M1 stores configuration only: typed TypeScript owns validation, inheritance and
CAS; Python transports results. M2 adds admission records to the same owner. This is an opt-in quota
subdomain, not a new capability or provider. It must not be copied into separate
App, Turn, frontend or Lark policy stores. A future shared authority provider
must implement equivalent atomic semantics before claiming cross-host limits.

Policy fields: scoped Goal/agent/automation identity, minimum minutes,
configuration revision and owner instruction reference. Configure uses exact
revision CAS. Reducing or disabling requires explicit reduction authorization;
zero removes only that scope's constraint, preserving starts. The local CLI
records caller-asserted owner intent; it is **not** an authenticated boundary
against another process running as the same OS user.

Admission records retain start time, request identity and trigger time. They
are independent of scheduler reset keys, Todo identity and model identity.
Readback exposes effective floor, contributing scopes/revisions, next eligible
time and reason. Duplicate/stale triggers are denied; a due tick writes the
reservation before launch. Corrupt state fails closed. Clock rollback delays
admission; correctness assumes a trustworthy host clock and does not promise
protection against clock manipulation.

### Host contracts

| Host path | Required contract | Initial boundary |
| --- | --- | --- |
| Managed `turn run-once` | Atomic admission before a new host attempt, including failed-result recovery | M2 candidate; isolated CLI, concurrency and crash-recovery tests, no external host promotion |
| Local legacy scheduler / external launchers | Route launches through admitted Turn or implement the same owner call | Not yet qualified; do not advertise enforcement |
| Codex App automation | Apply floor-compatible timer, read actual schedule, ACK only matching facts | M1 schedule recommendation floor; hook coverage not qualified |
| Attached interactive/manual session | Explicit manual intent; existing authority gates remain | Caller records reason; automatic continuation cannot masquerade as manual |

For App, show desired versus observed schedule and apply failure. Unsupported
intervals require holding the affected automation, not shortening the interval.
The activation path must be qualified separately before advertising one-click
floor-safe activation. An in-model guard can stop further work after waking;
it cannot recover already consumed model tokens. Merely changing a prompt is
not enforcement. A native `UserPromptSubmit` hook is a candidate; until its automation coverage is
qualified, schedule-only mode must not claim to prevent all premature model starts.

### Bounded work and cheap observation

One admitted unit is one host invocation. Retries and subsequent invocations
in a managed loop re-enter admission; a failed call does not get a free retry
storm. A denial returns wait/next-eligible without host launch or quota spend.
Existing settlement replay is allowed without re-admission. Existing maximum
turn counts and provider/process timeouts remain independently binding. A token
hint is soft unless the provider enforces a limit; arbitrary callback runners
and external App sessions do not gain hard cancellation guarantees here.

Read-only source polling or cached status collection does not consume model
admission. It may queue/coalesce a due trigger. It must never invoke an LLM in
the “cheap collection” path or convert a non-material poll into charged work.
Notification policy remains independent of both paths.

## 6. Alternatives

Only increasing scheduler backoff fails after resets and does not serialize
concurrent launches. Notification cooldown saves attention but not execution.
A separate App-only setting duplicates authority and misses managed Turn.
A generic distributed budget framework would delay the real local fix.
The chosen bounded subdomain reuses existing locks and effect transport; later
provider adoption is a separate qualified milestone.

## 7. Safety and compatibility

Opt-in only. No-rule read/admission creates no cadence state and keeps existing
host/retry behavior. Do not claim a configured policy covers an executor that
has not integrated admission. Automations must use stable identity; omitting an
automation id does not inherit automation-specific rules. Use an agent or Goal
rule for coverage across launch mechanisms. Rules grant no credentials or
remote authority. Owner references should be short, non-sensitive identifiers.

## 8. Migration and rollback

Pause the affected automatic launcher before initial configuration. Read policy,
preview an exact-revision change, apply it, read it back, then verify the chosen
executor/host capability before resuming. Preserve the policy and starts during
upgrades. Older versions ignore the policy: pause before downgrade; do not
silently resume them. Disable via explicit owner-authorized zero at each active
scope; do not delete the state file as routine rollback.

## 9. Acceptance

| Claim | Decisive evidence | Exclusion |
| --- | --- | --- |
| Daily interval | Fake clock: 24h minus 1ms denied; exactly 24h admitted | Wall-clock manipulation |
| Durable one-start | Concurrent callers and process restart using real temporary files | Distributed/shared filesystem guarantees |
| Owner control | Inheritance, exact-revision conflict, reduction rejection, explicit disable | Same-UID authentication |
| No reset bypass | Scheduler replan/reset/model changes retain floor | Unintegrated launchers |
| No retry storm | Managed host failure/invalid-result retries denied; no spend on denial | Internal provider tool-call limits |
| Compatible off | Existing executor suite; absent policy creates no cadence files | Configured lanes intentionally change |
| App honesty | Desired schedule and guarantee readback; mismatched ACK rejected | No zero-token wakeup claim |
| Full product delivery | Settings/CLI projection parity and packaged interaction | Pending companion milestone |

## 10. Operations

Inspect policy revision, source rules, next eligibility and the admission reason
before diagnosing “stuck” work. Distinguish interval wait, exhausted quota,
unavailable host and awaiting owner. Do not solve wait by resetting scheduler
history. Backup the policy and starts together; recovering an older snapshot
can permit an early launch and requires a conservative hold. Future retention
must preserve the latest start for every active scope.

## 11. Delivery plan aligned with the roadmap

| Milestone | Outcome | Exit / rollback |
| --- | --- | --- |
| M1 · S7/S2/S4 | Codex App first: owner CLI, durable inherited floor, reset/backoff-safe recommendations, activation guidance and compact readback | Real file/CLI and App projection negative tests; opt-in, pause before rollback |
| M2 · S4/R2 | Managed Turn atomic admission plus scoped App hook qualification; actual timer apply/readback and legacy launcher coverage | Isolated host acceptance, manual/automatic identity, hook trust/failure coverage, unsupported schedule holds and bounded continuation |
| M3 · S5/S7 | Existing settings editor exposes inherited floor and next eligibility; unified quota wait feedback in frontend/Lark/CLI | Packaged interaction/readback parity; no duplicate policy owner |
| M4 · S7/R6 | Cross-host reservations only when a real shared-runtime caller requires them | Provider concurrency/fence/recovery evidence; explicit promotion |

M1 is useful for App schedule management, not completion of the multi-host
product journey. The broader product goal remains open until M2/M3 acceptance.

## 12. Open decisions

1. Host maintainers: verify App heartbeat coverage of `UserPromptSubmit`, hook
   failure/trust and automatic/manual identity before any zero-token wake guarantee.
2. Quota/runtime maintainers: bounded episode admission versus per-invocation
   admission. Keep the conservative per-invocation unit until real continuation
   evidence justifies explicit time/step limits for an episode.
3. Settings owners: agent-versus-automation editor placement for M3; reuse the
   existing quota/configuration projection, not another preferences store.

## Appendix: implementation ledger

Baseline audit at `23edcb19c`: no durable owner minimum interval. M1 merged in
[#4921](https://github.com/loopx-project/loopx/pull/4921), and managed Turn
admission merged in [#4929](https://github.com/loopx-project/loopx/pull/4929).
The M2 candidate reserves a managed Turn start in the same quota
policy file and lock before host invocation. Denial returns the next eligible
time without a host call, writeback or quota spend; a failed host consumes its
start, and settlement replay skips admission. Goal floors apply per agent;
automation floors require an explicit stable `--automation-id`. A manual start
requires `--manual-interval-bypass-reason`, records a start, and bypasses only
the interval. The local CLI remains a same-UID trust boundary.

A managed start is two-phase in the same store: admission reserves the interval
slot, and the Turn executor confirms that reservation only after the host
attempt is durable in its journal. A crash between the two leaves the
reservation resumable by the same Turn identity once the floor is reached, so a
reserved-but-unstarted start never strands a Turn; a confirmed start stays
fail-closed for the same identity, and an explicit manual reason cannot bypass
that. A store record written without the phase field is read as an attempted
start, so an older or hand-edited file fails closed rather than resuming.

The M3 settings companion presents the quota-owned Goal/agent/automation policy
through a revision-locked local preview, apply and readback, and reports stale
configuration intent as a typed conflict instead of parsing error text. It does
not edit existing Codex App timers, and next-eligible time plus Lark/CLI wait
parity remain open. The App timer-to-hook path, non-Turn launchers and live
model-host promotion remain unqualified; M4 remains a design option. No existing
automation is activated or rebound by this proposal. Tests and PR validation
must distinguish deterministic evidence from host promotion.

M1 policy files are read as v1 and upgraded in place to v2 on the first
configuration write or admitted start. The path stays stable; older binaries
reject the v2 schema rather than silently discarding start records. Pause the
launcher before downgrade.

### Hook research — 2026-09-23

[Official hooks documentation](https://learn.chatgpt.com/docs/hooks) documents
`UserPromptSubmit` blocking and review/trust of hook definitions. Its documented
input includes prompt and turn/session ids, not a structured automation id or
trigger origin. [Scheduled tasks documentation](https://learn.chatgpt.com/docs/automations)
describes minute intervals and daily/weekly schedules. Neither page establishes
complete heartbeat coverage of the hook or an automation-specific launch SLA.

An isolated experiment using the App-bundled runtime `0.155.0-alpha.9`, a
synthetic heartbeat prompt, disposable config and a local HTTP counting endpoint
observed zero provider requests and zero token usage when the hook blocked.
The allow control reached the local endpoint (six requests including retries).
No real model service or credential was used. The reviewed fixture hook used
invocation-local trust bypass only in this disposable test. This qualifies the
runtime hook mechanism, **not** the App timer-to-hook path, installed hook trust,
reconnect/resume, or reliable automatic/manual classification. No production
hook was installed. A raw prompt marker is not an authenticated trigger identity.
