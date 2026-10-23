# Managed steward monitoring and periodic reports

Status: native identity preparation is implemented. Scheduled execution and
daily portfolio reports remain the target described below; they are not enabled
by preparing an identity.

The steward should become a persistent LoopX-managed Agent. Its original private
conversation remains the user's entry point. Native monitor Todos, Turn
admission, scheduling and periodic-report publication own recurring work.

## What exists and what is missing

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

The private steward currently uses the special `loopx-manager` conversation
identity. It does not have its own registered execution Goal/Agent. The existing
Chat LoopX-mode endpoint accepts a project Goal Chat, and rejects this steward.
Also, a report Turn-start hook does not independently wake a sleeping actor.
These are integration gaps; calendar calculation alone does not enable a report.

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
grant are retained. No model, native monitor, scheduler host or extra message
consumer starts. The UI explicitly shows **Scheduled tasks are not enabled**.

This is a separately reviewable preparation boundary. There is no active host
to stop or migrate back. Disconnecting the conversation uses its existing owner
and leaves the registered, unscheduled Goal visible; it does not delete history
or accepted work. Removing that Goal remains an explicit native Goal lifecycle
operation. Activation and its pause/rollback controls are the next integration
stage, not an implied consequence of this preparation.

## Native scheduling and execution

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

## Implementation order and acceptance

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

The earlier conversation-specific subscription/executor prototype is preserved
as unmerged evidence. It is not the selected product path, an enabled schedule,
or proof of the native actor integration. Its local tests do not qualify this
design's installation or delivery requirements.
