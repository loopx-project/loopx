# Managed steward monitoring and periodic reports

Status: source-bound identity preparation, opt-in status monitoring and the
original steward's periodic full-body report path are implemented. Installation,
an enabled subscription and a natural scheduled delivery require their own
readback evidence. Preparing an identity enables neither monitoring nor reports.

The steward becomes a persistent LoopX-managed Agent. Its original private
conversation remains the user's entry point. Native monitor Todos, Turn
admission, scheduling and periodic-report publication own recurring work.

## Lifecycle owners

The current components already provide most of the lifecycle:

- `control_plane/scheduler/monitor_todo.py` and the typed scheduler project due
  monitor work and the next deadline.
- `control_plane/turn_driver/` provides registered Goal/Agent admission, single
  executor ownership, recovery, settlement and quota lineage.
- `capabilities/periodic_report/cadence.py`, `cadence_journal.py` and
  `cadence_runtime.py` evaluate calendar windows, retain a pending reporter and
  expose due report intents at Turn start.
- Native external-conversation admission and the existing Lark reply journal
  retain original requests, audience identity and delivery evidence.

The private steward retains the special `loopx-manager` conversation identity;
preparation adds the separate registered execution Goal/Agent described below.
The original project-Goal Chat endpoint still rejects this private steward.
Its source-bound native host bridge supplies monitoring admission. A report
Turn-start hook alone does not independently wake a sleeping actor, so calendar
calculation does not enable a report.

The existing calendar settings support an optional **First report date**, in
the schedule's timezone. Before its first matching daily or weekly deadline,
the calendar admits no report intent or generation work. The first report still
covers the preceding full interval. Leaving the date empty retains the existing
calendar behavior. This setting does not enable the steward's daily portfolio
report integration by itself.

New scheduled reports appear as independent full-body messages in the original
owner's private conversation. The retained source message verifies the original
App, chat and read grant; an unrelated historical question does not become the
new report's reply parent. Rich parts use the same frozen placement and delivery
journal, and readback verifies the private chat and main-conversation position.
An existing delivery record without explicit placement retains its original
source-context behavior, including pending or unknown sends. Upgrading neither
retargets those attempts nor republishes a report already verified as delivered.
Ordinary replies keep their existing source-context/thread behavior; this does
not enable chat-root delivery for group conversations.

The synthetic packaged settings journey below uses real preview/apply/readback
over local HTTP. It preserves the date across frequency, time and timezone
edits and reload. The narrow view is a 390×844 viewport emulation.

| Before: recurrence only | After: optional first report date |
| --- | --- |
| ![Calendar before the first-date control](../assets/personal-workspace/report-calendar-start-date/before-desktop.png) | ![Calendar with a first report date](../assets/personal-workspace/report-calendar-start-date/after-desktop.png) |

| Before, narrow viewport | After, narrow viewport |
| --- | --- |
| ![Recurrence controls in a narrow viewport](../assets/personal-workspace/report-calendar-start-date/before-narrow.png) | ![First report date in a narrow viewport](../assets/personal-workspace/report-calendar-start-date/after-narrow.png) |

Chat `/goal` transport supervision and a LoopX-managed Turn are distinct. Merely
relaxing `validate_goal_chat`, changing a Session field, adding a prompt, or
starting a Codex native Goal does not establish the LoopX lifecycle below.

## One persistent steward actor

Extend the existing source-bound upgrade/activation path. A typed preview must
identify the original private binding, its conversation, the steward workspace,
the persistent execution identity and the current host/profile. Apply is an
idempotent native transaction using the owner's existing authorization.

Register one visible steward Goal/Agent, rather than creating a hidden Goal for
each monitor or borrowing a project worker's identity. The Goal owns the
steward's durable monitoring and reporting lifecycle. Its execution authority
does not include performing other projects' business work.

The upgrade retains private chat identity, visible history, queued and accepted
requests, unfinished delegations, original delivery routes and existing grants.
In-flight work stays with its original accepted Turn; the upgrade waits for an
eligible idle boundary. If host tools require a replacement upstream thread,
preserve the visible conversation and history, and record that transport change.
Do not claim to preserve a physical thread that was actually replaced.

Keep the steward's existing portfolio-read authority separate from its execution
actor. Resolve both through their native owners on every admission. Registering
the actor does not grant new project writes or broaden the original audience.
Keep ordinary project assistants unchanged.

### Identity preparation available now

In the private steward's connection settings, expand **Native periodic
monitoring** and choose **Prepare steward identity**. The same gesture obtains
a current preview and applies it. A stale preview, unverified provider, changed
grant, active Turn or queued user request refuses preparation. Resolve that
condition and retry in the original conversation.

The existing local-owner endpoint is
`POST /api/chat/lark/private-conversations/managed-steward`. A preview accepts
`binding_id` and optional `session_id`. Apply additionally requires
`execute: true` and the preview's `plan_id`. Callers cannot select a Goal, Agent,
workspace, provider account or new grant. Existing loopback/origin checks apply.

The typed conversation owner names one Goal and registered `steward` Agent from
the original binding. Existing native Goal creation and configuration owners
initialize File or SQLite storage according to machine settings. The existing
conversation binding stores only the resulting identity reference, including
the original Session and native creation operation. Source-session registries
retain their lifetime token; ordinary registries retain the existing nullable
lifetime contract. Preparation does not strengthen that legacy contract.

A lost registration or binding response can resume the same verified creation;
it cannot adopt an unrelated Goal or recreate a missing bound Goal. Repeating
an applied request verifies the same registration and returns the same actor.
The original Session, history, upstream thread, accepted requests and portfolio
grant are retained. Preparation invokes no model and enables no native monitor.
The UI shows the prepared identity with monitoring not yet enabled.

Preparation remains separate from enabling recurring work. Disconnecting the conversation uses its existing owner
and leaves the registered, unscheduled Goal visible; it does not delete history
or accepted work. Removing that Goal remains an explicit native Goal lifecycle
operation. Monitoring requires the separate explicit setting below.

### Native status monitoring

After preparation, choose **Enable monitoring** in the same connection panel.
The native continuous-monitor Todo owns enable/pause, the 30–1440 minute cadence,
last observation, result hash and next deadline. The panel's single gesture
obtains a fresh source-bound preview and applies it through
`POST /api/chat/lark/private-conversations/managed-steward/monitor`; it accepts
only the original binding, enable/pause operation, cadence and preview id.
It cannot select a different actor, workspace, account or grant.

The existing Chat host wake service checks this native frontier. A due watch
enters the original Session's Core worker and settles through native
`quota monitor-poll`, with an exact task-lease generation. It reads the current
authorized local portfolio, including stopped Goals and coverage gaps, without
a model call or execution-quota spend. Its own monitor bookkeeping is excluded
from the material-change hash. This watch neither delivers business replies
nor enables a daily report.

Active or queued user work retains priority. Pause uses the existing native
blocked lifecycle to retire the current lease atomically; enable reopens that
same Todo and obtains a fresh generation for cadence edits. First enable checks
immediately; resume or a changed cadence starts a fresh future window. An
unchanged setting leaves the existing deadline alone. Each accepted watch fixes
its native deadline and observed lease version before collecting data, so a
pause/re-enable cannot let old data borrow a new lease generation. Final native
writeback rechecks the collected scope under the binding's configuration and
revocation fence. Native Goal stop
also blocks observation. The user can still pause the retained monitor while
that Goal is stopped. Restart reads the native Todo/deadline and original Core
request. A failed or unknown accepted attempt is retained, never replayed.
An in-memory wake hint expires within a minute and holds no scheduling
authority; configuration invalidates it immediately. No extra scheduler,
message consumer or binding-side scheduling toggle is created.

An unreadable Session or queued Turn file affects only its own binding. Other
stewards continue their due checks. The unreadable data and original monitor
deadline remain intact; once the file is repaired, queued user work still takes
priority over monitoring.

The packaged browser journey below uses synthetic private connections over
real HTTP, Core and native File storage. Desktop views are 1440×1000; narrow
views are 390×844 viewport emulations, not physical-phone acceptance. The
journey covers enable, one automatic poll, cadence edit, failed authorization,
pause, resume and reload. An ordinary web-manager Session may also be prepared
by the workspace; the original private Session remains unchanged and the watch
adds no model request. File/SQLite regression tests independently check that
monitor polls leave execution-quota slots and original business replies alone.

| Before: prepared identity | After: enabled native monitor |
| --- | --- |
| ![Prepared steward before scheduling](../assets/personal-workspace/steward-monitor/before-desktop.png) | ![Enabled native monitor with last and next checks](../assets/personal-workspace/steward-monitor/enabled-desktop.png) |

| Enabled, narrow viewport | Paused, retained deadline |
| --- | --- |
| ![Enabled monitor in a narrow viewport](../assets/personal-workspace/steward-monitor/enabled-phone.png) | ![Paused native monitor retaining its deadline](../assets/personal-workspace/steward-monitor/paused-phone.png) |

## Native scheduling and execution

### Owner Todo notifications during a source outage

The existing **Automatically notify new owner tasks** setting uses the original
private steward's background outbound worker. It does not enable a native
monitor, report, model Turn or second message consumer.

Discovery enumerates the complete current authorized Goal scope. Each Goal must
provide a complete native Todo read and exact current decision terms. If one
Goal is unreadable or unmigrated, its previous subjects, pending notices and
unknown sends remain retained; an unavailable source is never treated as an
empty or withdrawn Todo list. Notices from other readable Goals continue. An
unsent notice becomes eligible only after its own source is read again, with
the original generation and provider intent preserved. Real withdrawals or
scope removal still cancel unsent notices. A known provider locator continues
independent readback of the frozen original effect, even if the Todo changes
or its source is unavailable; current App, owner and original-route verification
remain required. This read cannot create another notification.

First enable baselines readable history and remembers unreadable initial Goals.
Their first successful read also baselines history, avoiding a delayed recovery
burst. Later changes and newly registered Goals retain ordinary discovery.
Failure to enumerate scope or verify the original owner, App, grant or route
still pauses delivery. The panel distinguishes a fresh partial-source check
from a stale worker or a delivery error; unknown receipts remain visible.

The packaged fixture
`apps/presentation/dashboard/smoke/owner-attention-source-fixture.py` uses real
HTTP, native File authority and the existing outbound worker, with synthetic
provider/model transport and no paid call. Its unmigrated Goal reproduces the
outage; initializing that disposable source verifies recovery. File/SQLite
regressions separately cover source errors, incomplete reads, changing exact
reads, retained queue identities and independent provider readback. The baseline
image shows the prior UI bundle over the same partial-source backend state;
the baseline adapter also fails the healthy-source regression independently.
These captures establish packaged browser behavior, not phone-device or live
account acceptance.

| Before: generic incomplete notice | After: explicit partial coverage |
| --- | --- |
| ![Previous notification status](../assets/personal-workspace/owner-attention-source/before-desktop.png) | ![Healthy Goals continue during a source outage](../assets/personal-workspace/owner-attention-source/partial-desktop.png) |

| Partial coverage, narrow viewport | Recovered worker, narrow viewport |
| --- | --- |
| ![Partial notification coverage in a narrow viewport](../assets/personal-workspace/owner-attention-source/partial-phone.png) | ![Recovered notification worker in a narrow viewport](../assets/personal-workspace/owner-attention-source/recovered-phone.png) |

The intended flow is:

```text
original private ingress -> original steward conversation
                                      |
                              steward Goal / Agent
                                      |
           native monitor/report deadline -> scheduler admission
                                      |
                              one managed LoopX Turn
                                      |
                    periodic-report generation/publication
                                      |
                     original private reply + provider readback
```

Represent monitors as native `continuous_monitor` Todos under this actor. Reuse
their due times, observations and lifecycle; do not create another task store,
cron, model loop or message consumer. Expose the report calendar's next boundary
to that same scheduler frontier. The periodic-report calendar/journal remains
the owner of report windows; a monitor deadline is its scheduling projection,
not a second publication cursor.

The existing runtime host checks the frontier without invoking a model. When
nothing is due, it sleeps until the next eligible native deadline. Only an
admitted Turn can invoke the model. A fixed daily report remains due even if
project state has not changed. Preserve the owner's minimum scheduling cadence;
align eligible calendar wakes to the requested wall time and disclose lateness
when the machine, host or actor cannot run then.

User messages, due monitors, outstanding original replies and reports use the
same actor's queue and admission. A busy actor retains due work and rechecks it
at the normal terminal boundary. It does not create another executor. Existing
priority, replan, budget, stop and settlement policies remain effective. Pausing
the steward prevents new scheduled model Turns without discarding accepted work
or historical results.

A native status watch calls the existing monitor-poll owner under its original
Chat Turn and task lease. It does not select a continuous monitor as a heartbeat
advancement task, so open executable work does not prevent a due observation.
Fresh source, user-gate, capability, deadline and lease checks still apply; the
poll keeps its stable Turn identity and does not invoke a model or spend a slot.
The earlier adapter's exact pre-poll selection rejection can receive one
separately correlated attempt only when its failed Turn has no native start,
answer, delta or poll event. The original failure remains; unknown failures,
interruptions and a failed recovery do not trigger a retry loop.

## Daily portfolio report

Configure the original steward's daily report for 09:00 `Asia/Shanghai`. At
scheduled boundary T, freeze the reporting interval as `[T-24h,T)`. Keep scheduled
boundary, actual generation time and actual delivery time separate.

Read all locally authorized registered Goals, including stopped Goals, through
their current native read surfaces. Do not reactivate them. Collect window
progress, relevant active/completed Todos, actual artifacts and validation,
unresolved requests/returns and concrete blockers. Distinguish reported claims
from independent verification, and report incomplete coverage explicitly.
Reconcile current approvals and dependencies before proposing a new owner ask.

The steward produces the full Chinese body and returns it to the original
private chat. A summary, queued outbox row, attachment alone or assistant-side
completion flag is insufficient evidence that the user received the report.

Use a stable identity for each scheduled window. Retain generation and delivery
progress separately. A restart resumes that window; confirmed delivery is not
sent again, and uncertain sends are read back before retry. After extended
offline time, produce the most recent complete window and disclose missing
windows, rather than starting an unbounded series of model calls.

### Original private report settings and execution

In **Settings → Lark → the original steward → Native periodic monitoring**,
the owner can enable or update detailed reports, select daily/weekly frequency,
local time, timezone and first report date, or pause/cancel. The existing calendar
field is reused. A source-bound preview and exact-plan apply configure the
existing `periodic_report` Goal setting with the `portfolio-progress` preset;
the destination is derived from the original private binding. Neither the
browser nor the model can supply another Goal, App or recipient. The generic
single-Goal report consumer cannot consume this private portfolio preset.
This preset requires a calendar; its switch cannot select the generic
stage-boundary reporting mode. Pause or cancel through the report actions.

Portfolio evidence collection finishes before the governed driver captures its
launch authority. A long read can then observe legitimate registry rewrites
without immediately invalidating an earlier launch witness. The original
source and execution lease are checked after collection and again at dispatch;
authority changes after plan capture still reject the launch. This ordering
does not authorize replay of a historical attempt with unknown host effects.

The original pump can resume one retained report after the first-party launch
owner's exact `stale_goal_authority` refusal. That owner rejects before publishing
`turn/start`; a missing answer or missing events alone is insufficient. The
typed admission checks the original Chat request, canonical native transaction,
one recorded attempt, no generation or settlement, and the current original
subscription and grant. A separately correlated Chat attempt resumes the same
native journal, window, Todo and settlement identity through `run-once` recovery.
The historical outer response is retained. A failed recovery, unknown transport
outcome, real model start, changed identity or revoked permission cannot trigger
another automatic model attempt. Existing no-model settlement recovery and
independent full-body delivery readback still apply.

Frozen generation is checked independently inside the original host using the
same installed report provider. An embedded Python host's bootstrap does not
imply that its bare child interpreter can import LoopX; no second installation
or ambient import path is required. Ordinary CLI command validators are unchanged.

If the original model has returned and the frozen generation validates, the
original pump can resume a failed native **validation** phase without invoking
the model again. The typed owner requires the same live subscription, actor,
window and open generation Todo, the canonical cached host/result phases, and
an explicit no-host recovery decision. The native driver revalidates the frozen
artifacts and current authorization, reacquires the original task lease and
performs ordinary completion and settlement. A paused subscription, changed
identity or grant, invalid generation or unknown model effect stays blocked.
The historical Chat response is retained; only the native committed receipt
allows the existing original-audience sender to proceed.

Once generation is frozen, delivery checks the original source Goal identities
against the current authorized scope. Adding an authorized Goal does not revoke
the frozen report or rewrite its source/body; later windows include the new
Goal. Removing a frozen Goal, replacing its identity, revoking the original
grant or changing frozen artifacts still blocks delivery, including after a
partial or uncertain send. Existing provider readback decides which original
parts remain unsent; scope growth never authorizes another model run or resend.

Read coverage and authorization are separate. A partial source may omit an
unreadable or oversized Goal without revoking the original report grant. New
private editorial requests freeze the complete authorized Goal identities
alongside their existing authorization digest. Delivery checks this immutable
witness, including unread Goals; it never upgrades partial coverage to complete.
Later authorized additions belong to a future report, while removal or identity
replacement of an original authorized Goal still blocks publication.

Older frozen requests remain immutable. When their readable Goal list does not
reconstruct the original authorization digest, recovery requires the complete
current authorized scope to match that original digest exactly. An absent
identity witness never permits guessing which Goals were originally authorized;
an ambiguous later scope change remains retained, without another model run.

The existing idle Chat wake producer admits one calendar window and creates
one native finite generation Todo for it. The original Chat worker and native
thread author the report, using complete frozen source records and the owner's
`REPORTING.md` criteria. Public-safe Turn fields remain separate from private
editorial content. A report-specific opt-in allows the kernel's existing
`validated_completion`; its original task lease and independent artifact
validator must pass before the generation Todo closes. Ordinary host prompts
and result vocabulary retain their defaults.
The finite postcondition is the validated generation itself: a complete valid
editorial response closes this generation Todo even if the model labels it
progress. This does not close the ongoing Goal or claim delivery.

If generation has completed but native writeback or debit has not committed,
the normal idle producer resumes the remaining effects of that same original
Turn. It reads the canonical completion under the original Turn fence, without
reopening the Todo or invoking the model again. Existing effect readbacks also
reconcile a committed effect whose response was lost; a missing outer Chat
result event can be recovered from the committed native journal. Historical
failures and generated content remain intact. Delivery still requires the
native committed result and current original authorization; unknown or
contradictory journal identity remains blocked.

The existing Lark outbound pool reads the committed native result, validates
the frozen source/editorial/artifact lineage and returns the full Chinese body
through the original reply owner, splitting long text into verified parts.
Known part IDs are read back after restart; a send with unknown outcome and no
locator is retained without another create. Only complete provider verification
advances the existing publication cursor. No additional scheduler, consumer,
model worker, business store or provider scope is created.

Pause disables subsequent generation and delivery. Cancel also requests a stop
only for an active report Turn; an unconfirmed stop is shown separately from
the committed configuration. Original reports and delivery receipts remain.
Revocation, subscription changes, source/provider changes or damaged artifacts
block late publication. Monitoring and ordinary conversation retain their own
lifecycles. The settings show the next due time, retained window, generation and
verified/unconfirmed delivery as separate facts.
Changing a frozen, unverified report's schedule is rejected with a specific
pending-window explanation. Pause and restoration of the same plan remain
available; schedule edits can proceed after verified publication. This keeps an
unfinished original window from being stranded by a different subscription.

A terminal native attempt with a retained invalid editorial response, no
generation and no delivery attempt is shown as generation failed. The owner's
existing cancel action can explicitly retire that failed window: its Todo is
paused as blocked, the native failure, cost and artifacts remain, and the cadence journal
records retirement separately from publication. Restoring the same plan does
not replay the failed attempt; a later due window can progress. A changed future
plan is also allowed after retirement. Unknown model outcomes and partial or
unknown sends cannot take this exit; they keep the original recovery boundary.

| Cancelled failure on the original settings page | Narrow retained-failure state |
| --- | --- |
| ![Cancelled generation is not completed or delivered](../assets/personal-workspace/steward-reports/failed-window-cancelled.png) | ![Retained cancelled window in a narrow viewport](../assets/personal-workspace/steward-reports/cancelled-window-narrow.png) |

![Default narrow settings viewport before opening monitoring](../assets/personal-workspace/steward-reports/default-narrow.png)

The packaged browser fixture uses actual HTTP/Core/calendar storage with a
synthetic original provider/model. Screenshots below are that isolated journey,
not production-message or real-phone proof.

| Before integration: monitoring only | Enabled future first date |
| --- | --- |
| ![Original steward has only status monitoring](../assets/personal-workspace/steward-reports/baseline-desktop.png) | ![Future first report date saved and read back](../assets/personal-workspace/steward-reports/enabled-desktop.png) |

![Report controls in a narrow viewport](../assets/personal-workspace/steward-reports/enabled-narrow.png)

## Acceptance boundaries

### Managed Agent execution boundary

A managed conversation must belong to the selected registered **Agent**, with
its own original Session, execution state and return path. In the Goal's Agent
view, managed and attached conversations are parallel sources; a Goal-wide
Codex chat with an extra Agent selector does not establish that binding.
The experimental **Advance LoopX tasks** control has therefore been withdrawn
from ordinary Goal Chat. The existing general conversation, native Goal mode
and original attached conversation entry retain their current owners.

The native host groundwork remains separately qualified: an explicitly invoked
local managed request can use the original Core worker and native Task/Turn
owners for a finite source-validation task. It retains the same thread and
sandbox, resolves the selected Task's immutable argv validation declaration
before model execution, and checks its canonical digest/revision and lease
before accepting completion. Missing validation starts no model; a failed
postcondition cannot complete the Task or spend a delivery slot. The low-level
request accepts no runtime profile or caller validation command. Stable Core
operation identity retains failed or unknown requests without replay.

The existing session creation API now accepts `managed_agent_id` for a local
Goal. It binds the registered work identity separately from the executor
(`agent_id`) and model. Core persists that immutable identity, Goal lifetime
and original thread, with a distinct Agent channel. Resume selects only that
Goal/Agent/executor channel; another Agent cannot execute its native work.
Old Goal-wide conversations remain readable and unassigned, and cannot gain a
native work identity through a request-time selector. Existing project,
steward and attached entrypoints retain their original identities and workers.
Registration removal, Goal stop, a changed lifetime or a mismatched workspace
refuses continued execution rather than transferring the conversation.

This is an API/host lifecycle slice, **not** the finished Agent workspace
entry or general periodic journey. The Agent view still needs to expose this
binding alongside attached sources, including all original history and honest
unavailable states. Reuse existing Session, native Turn,
monitor/calendar and periodic-report owners; do not add a second scheduler,
task store or model worker. General writable execution, natural-language
configuration and recurring task composition remain unfinished.

Earlier packaged browser evidence demonstrated only the finite host/API path
with disposable real Core/File data and a synthetic model/provider. It did not
establish the correct Agent hierarchy, real-model adoption or natural-deadline
acceptance. The UI withdrawal does not alter the original private steward's
subscription, published reports, pending requests or publication route.

### Original private steward

The original-Session host bridge now runs a bounded native Turn through the
existing Chat worker and Codex app-server connection. It uses the explicit
`codex_app_server` outer-controller profile, not Codex CLI execution or a
desktop App heartbeat. Fresh native work selection, independent validation,
writeback, quota and scheduler handoff remain with the existing Turn command.
It retains the original visible conversation, provider and portfolio grant.
With unchanged authorization it reuses the upstream model thread. For an
all-registered report whose authorized inventory or Goal lifetime changed before
execution, the existing context owner discards old model history and prepares a
fresh upstream context, records it through the existing context owner, then re-enters strict native
execution admission. No new user message is required to repair this context.
Selected-scope changes, revocation and changes during a model call still fail
closed. Busy/queued user work wins admission, and revoked or stopped sources cannot
commit a late model result. Same-request readback does not restart failed or
unknown work.

The native status-monitor producer now uses this original-Session bridge,
with the separate no-model observation path described above. Identity
preparation still leaves monitoring disabled. Calendar configuration, native
generation and original private publication use the owners described above.
A passing synthetic File/SQLite host journey does not establish an enabled
production subscription, real model/report quality or natural deadline delivery.

1. Qualify the native steward upgrade and rollback at an idle boundary. Verify
   one registered actor, preserved original history/requests/grants, and no
   change to ordinary assistants or other project Agents.
2. Connect a real monitor deadline to that actor's native host and Turn driver.
   Verify not-due checks invoke no model; due, busy, pause and restart cases
   preserve one executor and native settlement.
3. Add the authorized portfolio report source to periodic-report, project its
   next calendar deadline and configure the original private delivery route.
   Reuse the existing report settings and monitor views for edit/pause/cancel,
   next due time, last result, coverage and actual delivery status.
4. Run semantic File/SQLite and host tests for fixed windows, unchanged-but-due
   reports, offline recovery, original audience, revoked grants, duplicate or
   unknown sends, partial delivery and late callbacks after cancellation.
5. Qualify the exact final diff, publish and merge under the existing repository
   policy, install it, then independently verify original-service adoption,
   actual configuration and an end-to-end report. The first natural scheduled
   report remains a separate acceptance event.
## Reading a fixed local evidence window

The existing private steward's evidence tool can read an exact report interval.
For example, a report ending at 09:00 uses `view=deliveries`, the authorized
`goal_id`, `window_start=2026-01-01T09:00:00+08:00` and
`window_end=2026-01-02T09:00:00+08:00`. Both timestamps need offsets. The read
includes the start and excludes the end; it does not move the interval to local
midnight or to the time the report happens to run. The existing `days` lookback
keeps its calendar-day behavior and cannot be combined with these bounds.

Follow `next_offset` and pass the first page's `source.source_revision` as
`expected_source_revision` on subsequent pages. If the index changes, the reader
returns a conflict with no rows: restart that Goal's read rather than combining
pages from different revisions. Complete the authorized portfolio pagination,
including stopped Goals when requested; one Goal's page is not global coverage.
This exact-window interface currently supports local sources. It rejects these
arguments for SSH sources instead of silently substituting a remote lookback.

Use an observed row's `source_ref` as `delivery_ref` to recover its complete
permitted `recorded_details` and evidence references, with the same interval and
revision. A related task outside the overview is joined through its exact
canonical Todo read. Oversized, unavailable or changed records remain explicit
coverage gaps. Recorded delivery time is not necessarily actual work time;
reading a receipt does not read its referenced artifacts or independently verify
the Agent's claims.

This extends evidence reading in the existing conversation. It does not enable
a report subscription, start a model, change a Goal, or send a report. Calendar
admission, report generation and verified delivery retain their existing owners.

### Authorized portfolio source

The same original conversation reader now accepts `view=report_source` with a
closed `window_start`/`window_end`. This composes the existing local portfolio,
Todo, fixed-window delivery and handoff readers. It enumerates every permitted
Goal, including stopped Goals; expands task excerpts and delivery details;
and reads each delivery's associated task, including completed records. It does
not read unregistered projects, SSH sources or referenced artifact contents.
Its delivery reads include typed `outcome_gap` records as counterevidence;
ordinary `view=deliveries` retains progress-only selection unless the caller
explicitly sets `include_outcome_gaps=true`. Neutral observations and untyped
host failures are outside that delivery source, so even complete pagination is
not a claim to have independently audited every failure or artifact.

The first call freezes one bounded snapshot in that Turn's reader. Source
observations and individual coverage gaps are rows too. Page through
`next_offset`, carrying `source.source_revision`. Later pages reuse the frozen
data, while the original live authorization check still wins over the cache.
Another Turn or a lost snapshot cannot resume at an old offset. An inventory
change, source revision conflict, outage, invalid/oversized row or read budget
limit remains an explicit coverage gap. Other readable Goals continue. Current
task and handoff state is observed at collection time; it is not an as-of-window
reconstruction or proof of a newly completed business outcome.

The provider's `manager_portfolio_report_source_adapter` composes with the
existing `PeriodicReportAdapterRegistry` using that authorized reader. Its
supporting items contain lossless JSON-string parts of the permitted evidence
rows, grouped by `source_ref`; decode each part's `summary` and concatenate in
item order. These are editorial input data, not audience-facing report prose.
The Agent still reads relevant referenced artifacts and authors the report.
There is no new state store or background loop. A capture allows at most 512
reader calls, 4,000 evidence rows and 4 MiB of evidence data, plus bounded source
coverage metadata; exhaustion is partial coverage,
not success or zero progress. These are IO bounds, not a model budget.

This source can also be read through the original Chat dynamic tool. Reading
alone does not activate a subscription or deliver a report. The opt-in original
steward report path binds it to the admitted calendar window, native generation
and private full-body delivery; installed adoption, current configuration and
the original production route still need independent verification.
