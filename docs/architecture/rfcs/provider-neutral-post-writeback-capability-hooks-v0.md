# RFC: Provider-Neutral Post-Writeback Capability Hooks v0

| Field | Value |
|---|---|
| Status | Accepted |
| Supersedes / closes | none |
| Date | 2026-08-26 |
| Tracking issue | [#3479](https://github.com/huangruiteng/loopx/issues/3479) |
| Source baseline | LoopX `11824ef5f` |
| Decision boundary | How an installed capability may propose bounded follow-up work after a successful durable writeback without joining the primary transaction or gaining effect authority |
| Core owner | Turn settlement and capability-hook lifecycle |
| Capability owner | Policy for deciding whether and which intent to propose |
| Effect owner | A separately authorized governed executor or sink |

> Language note: the
> [Chinese version](./provider-neutral-post-writeback-capability-hooks-v0.zh-CN.md)
> and this English version are semantic mirrors. A difference between them is
> a defect.

## 1. Decision Summary

LoopX should add a provider-neutral `post_writeback` capability-hook phase. The
phase runs only after the primary durable-writeback step has produced a valid,
committed receipt. An installed hook receives a compact public-safe receipt
projection and may return zero or more typed, idempotent **intent proposals**.

The proposal does not execute an external effect. Core validates and records it
in a bounded sidecar journal or outbox. A later governed executor must admit the
intent against its own capability, write-scope, budget, and authorization
policy before any effect occurs.

The authority split is deliberate:

```text
primary Turn
  -> validation
  -> durable writeback + committed receipt        (core lifecycle authority)
  -> isolated post-writeback hook dispatch
       -> typed intent proposal                    (capability policy authority)
       -> validated sidecar receipt                (core supervision authority)
  -> quota spend based on the primary receipt only

typed intent proposal
  -> separately admitted governed execution
  -> renderer / connector / external sink          (effect authority)
```

The hook is not another settlement step. Hook failure cannot roll back the
primary writeback, prevent the matching quota spend, create a user gate, or
silently acquire the primary Turn's write authority.

## 2. Problem

LoopX already has useful pieces, but they do not form an automatic
post-writeback contract:

- `loopx_capability_hook_registration_v0` supports the read-only
  `interaction_projection` phase. It demonstrates composition-root
  registration, TypeScript-owned validation, bounded output, slot conflict
  handling, and failure isolation.
- `periodic_report` can reduce durable public-safe rollout events into a typed
  trigger decision. Its runtime producer is called explicitly; the control
  plane does not invoke it after a Todo completion or replan writeback.
- the Turn settlement runtime has typed identities, ordered receipts, replay,
  and a durable-writeback checkpoint.
- rollout-event append is best-effort diagnostic logging. It intentionally
  cannot turn a successful primary command into a failure.
- some interaction contracts project command-shaped `post_writeback_actions`.
  Those operator hints are not a registry, typed provider result, or authority
  contract.

Without a shared post-writeback boundary, each capability must choose between
manual invocation, capability-specific imports in core, arbitrary callbacks,
or duplicating lifecycle logic. The first cannot automate a long-running Goal;
the other three create competing sources of truth or implicit effect authority.

The immediate motivating case is a periodic-report trigger after a bounded
segment completion or replan. The core contract must nevertheless be named and
designed around the caller outcome, not that first provider.

## 3. Goals and Non-Goals

### 3.1 Goals

The v0 contract must:

1. dispatch only from a committed primary durable-writeback receipt;
2. keep registration and result validation provider-neutral and core-owned;
3. expose only a compact, public-safe receipt projection;
4. accept only typed, idempotent intent proposals;
5. isolate registration, producer, timeout, validation, and conflict failures;
6. make replay, deduplication, budgets, and ordering deterministic;
7. preserve separate capability-policy, lifecycle, and effect authorities; and
8. support a real completion/replan-to-periodic-report-intent test without
   performing an external write.

### 3.2 Non-goals

This RFC does not:

- automatically send a report, message, email, webhook, or document update;
- add arbitrary shell commands, import strings, or user-provided callbacks as
  a hook interface;
- allow core settlement code to import `periodic_report` or any other concrete
  capability;
- put hook execution inside `append_rollout_event_once` or another low-level
  persistence helper;
- grant the hook the primary Turn's repository, network, credential, quota, or
  write authority;
- make hook completion a prerequisite for primary quota settlement;
- reinterpret existing command-shaped `post_writeback_actions` as trusted
  extension registrations;
- define a general workflow engine or a dependency graph between hooks; or
- promote the RFC itself to shipped behavior.

## 4. Ownership and Composition Boundary

The contract has four owners with non-overlapping responsibilities.

| Concern | Owner | Authority |
|---|---|---|
| Legal dispatch point, receipt validation, registry admission, budgets, ordering, journal, replay, and failure receipts | LoopX core | Control-plane lifecycle only |
| Whether a receipt is relevant and which intent should be proposed | Installed capability | Policy proposal only |
| Binding an implementation to a capability registration | Host or CLI composition root | Installation and configuration only |
| Rendering, connector calls, external writes, and readback | Governed executor or sink | Separately admitted effect authority |

Core must not branch on a capability identifier. The composition root may
register an installed provider, but installation proves only that the provider
is available and contract-compatible. It does not prove that an intent is
authorized to execute.

The TypeScript control-plane boundary remains the semantic owner of
registration, input, result, and dispatch-receipt validation. Python may hold a
callable adapter and invoke the TypeScript validators, as the existing
interaction-projection hook does; it must not implement a second acceptance
policy.

## 5. Dispatch Point and Primary-Transaction Boundary

### 5.1 Admissible source

A dispatch is admissible only when all of the following are true:

- the settlement identity contains non-empty goal, agent, Todo, Turn, and
  effect identities;
- the durable-writeback receipt uses the same effect identity as the active
  Turn settlement plan;
- the durable-writeback step is committed, not prepared, rejected, inferred,
  or merely present in prose;
- the receipt has been checkpointed durably enough that replay can recover the
  same dispatch identity; and
- the selected execution profile enables post-writeback hooks.

A successful diagnostic rollout-event append is not the source of authority.
It may provide bounded event facts to a capability after the committed receipt
has admitted dispatch, but a missing diagnostic event cannot change the truth
of primary settlement.

### 5.2 Placement

The orchestrator boundary dispatches after the committed writeback receipt and
before or after primary quota spend according to the host implementation. That
relative scheduling must not alter semantics:

- quota-spend eligibility depends only on the matching primary durable receipt;
- hook dispatch has its own sidecar checkpoint and idempotency identity;
- a process crash after primary writeback can replay the hook dispatch; and
- a hook failure never removes or changes the primary receipt.

The hook phase is therefore **post-writeback**, but outside the ordered primary
settlement-step list. Adding it as a fifth primary step would make optional
capability health authoritative over accounting and is rejected by this RFC.

## 6. Registration Contract

V0 introduces a phase-specific registration rather than weakening the
read-only `interaction_projection` schema:

```json
{
  "schema_version": "loopx_post_writeback_capability_hook_registration_v0",
  "hook_id": "periodic_report.runtime_trigger",
  "capability_id": "periodic_report",
  "phase": "post_writeback",
  "event_kinds": ["todo_completed", "replan_recorded"],
  "intent_kinds": ["periodic_report.trigger_evaluation"],
  "requested_read_scope": ["settlement_identity", "bounded_event_projection"],
  "budget": {
    "max_invocations_per_dispatch": 1,
    "max_intents_per_dispatch": 1,
    "max_result_bytes": 16384,
    "timeout_ms": 1000
  },
  "failure_policy": "isolate"
}
```

Core validates exact fields, bounded token arrays, known event and intent kinds,
duplicate-free identities, size limits, timeout limits, and the mandatory
`isolate` policy before invoking a provider.

The registration declares no executable command and no write scope. Intent
proposals may declare the scope a later executor would need, but that is a
request for admission, not authority held by the hook.

Registration order is not execution priority. Core sorts admitted
registrations by stable `hook_id` for deterministic dispatch. Hooks cannot
depend on another hook's result in v0; a real dependency belongs in a separately
governed workflow.

## 7. Input Contract

Each admitted hook receives one immutable
`loopx_post_writeback_hook_input_v0`:

```json
{
  "schema_version": "loopx_post_writeback_hook_input_v0",
  "dispatch_id": "pwh_sha256_opaque",
  "hook_id": "periodic_report.runtime_trigger",
  "capability_id": "periodic_report",
  "source": {
    "receipt_id": "receipt_opaque",
    "effect_id": "effect_opaque",
    "step_kind": "durable_writeback",
    "goal_id": "goal_opaque",
    "agent_id": "agent_opaque",
    "todo_id": "todo_opaque",
    "turn_instance_id": "turn_opaque",
    "event_kind": "todo_completed",
    "state_revision": "revision_opaque",
    "committed_at": "2026-08-26T00:00:00Z"
  },
  "projection": {
    "schema_version": "loopx_post_writeback_event_projection_v0",
    "transition": "segment_completed",
    "fact_refs": ["fact_opaque"]
  },
  "boundary": {
    "raw_task_text_recorded": false,
    "raw_logs_recorded": false,
    "raw_trajectory_recorded": false,
    "raw_session_transcript_recorded": false,
    "credential_values_recorded": false,
    "absolute_paths_recorded": false
  }
}
```

The source identities are public-safe opaque identifiers, not display names or
raw provider payloads. Core chooses the bounded projection schema for each
event kind. A registration cannot request the complete writeback payload.

Input must exclude task prose, prompts, logs, trajectories, transcripts,
credentials, environment values, local paths, repository contents, and
unregistered external references. If the boundary cannot prove the compact
projection safe, that hook is not invoked and receives an isolated failure
receipt.

## 8. Typed Intent Result

A provider returns a `loopx_post_writeback_hook_result_v0`. It is either
`not_applicable` with no intents, or `proposed` with a bounded list of intents.
One example intent is:

```json
{
  "schema_version": "loopx_post_writeback_intent_v0",
  "intent_id": "pwi_sha256_opaque",
  "hook_id": "periodic_report.runtime_trigger",
  "capability_id": "periodic_report",
  "source_dispatch_id": "pwh_sha256_opaque",
  "intent_kind": "periodic_report.trigger_evaluation",
  "operation": "evaluate_runtime_trigger",
  "policy_version": "weekly_v0",
  "payload": {
    "segment_ref": "segment_opaque",
    "source_event_refs": ["event_opaque"]
  },
  "budget": {
    "max_attempts": 1,
    "max_result_bytes": 16384
  },
  "requested_write_scope": [],
  "failure_policy": "isolate",
  "grants_new_action_authority": false,
  "external_write_performed": false
}
```

Core accepts an intent only when:

- its hook, capability, dispatch, and kind match the admitted registration;
- its serialized result stays within the declared budgets;
- its payload uses the known schema for that intent kind;
- its idempotency identity matches its semantic inputs;
- it claims no performed external write or new authority; and
- its requested scope is only a declarative input to later admission.

For periodic reports, the first useful intent requests trigger evaluation. The
existing capability-owned trigger reducer remains authoritative for promotion.
A later execution still uses the governed `compose-run -> renderer ->
authorized sink` boundary; the hook does not skip any of those stages.

## 9. Idempotency, Replay, and Conflict Rules

`dispatch_id` is a stable digest of the committed receipt identity, hook
identity, registration schema version, and event kind. `intent_id` is a stable
digest of the dispatch identity, intent kind, policy version, and canonical
typed payload.

The sidecar journal enforces:

- replay of the same dispatch and same canonical result is a no-op with the
  original receipt returned;
- the same dispatch identity with a different result is a conflict and the new
  result is rejected;
- a changed registration, policy version, or source receipt creates a new
  identity rather than replacing history;
- a crash between primary writeback and sidecar checkpoint can retry the same
  dispatch;
- a completed sidecar receipt is never reconstructed from a log message or a
  provider claim; and
- external executors deduplicate again on `intent_id` because hook recording
  and effect execution are separate transactions.

The journal stores bounded typed packets and compact failure codes. It does not
store raw provider exceptions, task context, credentials, or external payloads.

## 10. Supervision, Budgets, and Failure Isolation

Core applies per-hook and per-dispatch ceilings for invocation count, intent
count, bytes, and wall time. A profile may disable the phase or admit only an
allowlist of installed hooks. V0 executes hooks independently in stable order;
one hook's failure does not consume another hook's result slot.

Failure receipts use stable codes such as:

- `registration_rejected`;
- `input_boundary_rejected`;
- `producer_failed`;
- `producer_timed_out`;
- `result_contract_rejected`;
- `intent_conflict`; and
- `dispatch_budget_exhausted`.

Failures are observable and may be retried under a bounded policy with the same
dispatch identity. They do not:

- alter the primary durable receipt;
- block matching quota spend;
- change Todo state or selected work;
- create a blocker or user-action gate;
- invoke an external sink; or
- spend another capability's quota.

Repeated hook failure may produce a maintainer-facing diagnostic projection or
a separately admitted repair Todo. That policy is outside v0 and cannot be
inferred from exception text.

## 11. Smallest Useful Implementation Slices

### Slice 1: contracts and inert registry

- add TypeScript-owned validators for registration, input, result, intent, and
  dispatch receipt;
- add a Python callable adapter and deterministic registry;
- support disabled, not-applicable, replay, conflict, budget, and isolated
  failure tests;
- do not wire a production settlement path or capability.

### Slice 2: one primary lifecycle seam

- wire the hook dispatcher at one orchestrator-owned durable-writeback receipt
  boundary;
- persist a sidecar dispatch receipt and recover it on replay;
- prove quota-spend eligibility and primary receipts are unchanged;
- use an inert synthetic hook, default disabled.

### Slice 3: periodic-report intent producer

- register `periodic_report.runtime_trigger` at the composition root;
- map eligible completion and replan projections into one trigger-evaluation
  intent;
- feed that intent to the existing periodic-report producer through a fake
  scheduler or governed executor;
- stop before any external sink.

### Slice 4: qualified expansion

- extend wiring to other primary writeback paths only after receipt/replay
  parity is proven;
- add an authorized sink path as a separate change with explicit credentials,
  write-scope, readback, and rollback contracts.

Each slice should remain independently reviewable. Shipping Slice 1 does not
mean automatic reports exist; shipping Slice 3 does not authorize report
delivery.

## 12. Validation Matrix

| Case | Required result |
|---|---|
| Phase disabled or capability not installed | Zero provider invocations and no sidecar intent |
| Registration malformed or exceeds budget | Registration rejected before provider invocation |
| Primary validation or writeback fails | Zero post-writeback invocations |
| Committed writeback | Exactly one dispatch identity per admitted hook |
| Primary writeback replay | Same dispatch receipt; no duplicate intent |
| Same identity, different payload | Conflict rejected and original receipt retained |
| Provider raises, times out, or returns malformed output | Compact isolated failure; primary receipt and spend eligibility unchanged |
| Multiple hooks | Stable order, independent budgets, and failure isolation |
| Intent requests scope | Proposal recorded only; no authority or effect is granted |
| Periodic-report completion threshold | One typed trigger-evaluation intent under a fake scheduler |
| Periodic-report replan transition | One typed trigger-evaluation intent under a fake scheduler |
| External-write assertion | No network or sink call before separate governed admission |
| Public-boundary scan | No private names, URLs, paths, credentials, transcripts, raw logs, or provider payloads |

The end-to-end acceptance test must start at a real committed completion or
replan writeback boundary, not by calling the periodic-report producer directly.
It ends at a validated intent receipt, not at an external service.

## 13. Rejected Alternatives

### Import `periodic_report` from settlement code

This makes one capability part of core lifecycle and forces future capabilities
to repeat the coupling.

### Run hooks inside the rollout-event append helper

The helper is best-effort diagnostic persistence. Giving it orchestration
authority would either make diagnostics block primary work or hide hook loss
behind a successful primary receipt.

### Execute a command from `post_writeback_actions`

A command string has no typed provider result, bounded payload, deterministic
dedupe, or effect authority. It remains operator guidance, not the v0 hook
contract.

### Let a hook call the sink directly

This collapses policy and effect authority, bypasses write-scope admission and
readback, and makes retries unsafe.

### Add the hook as a primary settlement step

Optional provider availability would become authoritative over quota accounting
and Turn completion. The sidecar boundary preserves primary settlement truth.

### Reuse `interaction_projection` unchanged

That phase is read-time, has an empty write scope, maps typed projection slots,
and is evaluated before a primary effect. Post-writeback dispatch has a receipt
source, replay identity, sidecar journal, and intent output. Reusing its schema
would obscure materially different lifecycle semantics.

## 14. Promotion Gates and Open Implementation Choices

The architectural decisions above are stable for v0. Two implementation
choices remain deliberately deferred to the first wiring PR:

1. whether the sidecar journal is stored beside the Turn journal or in a
   dedicated hook-outbox path; it must preserve atomic per-dispatch dedupe and
   remain reconstructible from the committed receipt;
2. whether a host dispatches immediately after checkpoint or from a recovery
   queue; both must preserve the same identity, failure isolation, and primary
   spend semantics.

Merge accepts the design. General implementation qualification requires the
ownership split and a public test packet proving the validation matrix through Slice 2.
Automatic external delivery requires a separate accepted effect-boundary
change.
