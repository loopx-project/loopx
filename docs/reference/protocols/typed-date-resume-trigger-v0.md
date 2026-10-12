# Typed Date Resume Trigger v0

Status: implemented contract

## Purpose

`resume_at` gives a Todo one exact, machine-readable wakeup instant without
creating a second scheduler or message queue. It is part of the existing
`resume_when` owner and uses the same active-state, quota, managed Turn, frontend,
and Lark projection path as the other typed resume conditions.

## Authoring

The token is:

```text
resume_at:<timezone-aware-rfc3339-timestamp>
```

Examples:

```text
resume_at:2026-09-14T09:30:00+08:00
resume_at:2026-09-14T01:30:00Z
```

The boundary requires a complete calendar date, seconds, and either `Z` or an
explicit offset no larger than `14:00`. It rejects invalid dates and naive local
times. Equivalent instants are normalized to UTC before persistence. Natural
language is never inferred.

## Evaluation and receipt

One active-state projection uses one `evaluated_at` runtime-clock snapshot for
all date-bound Todos. Before the scheduled instant, the condition contains:

- `satisfied=false`;
- `material_change=false` and `material_change_generation=0`;
- `clock_provider=runtime_clock`;
- no resume receipt.

At or after the instant, it contains:

- `satisfied=true`;
- `material_change=true` and `material_change_generation=1`;
- `generation_fence=once_at_or_after_scheduled_for`;
- a `todo_resume_receipt_v0` whose identity is derived from the Todo id and
  canonical condition.

The receipt's `triggered_at` is the scheduled instant, not the observation
time. Repeated ticks, later reads, and process restarts therefore reproduce the
same receipt and never increment the generation beyond `1`.

## Consumer behavior

- CLI authoring validates and reads back the canonical UTC token.
- Status and quota keep a future Todo outside executable lanes. Other eligible
  work may continue according to the existing fallback policy.
- Canonical execution admission evaluates the same typed condition against the
  operation's runtime-clock snapshot. Future waits reject new acquisition and
  completion, even when a cached projection or old lease receipt says ready.
  Lease release, authorized condition repair and historical terminal readback
  retain their existing authority; reaching the time does not grant a lease.
- Once due, quota returns the existing `successor_replan_required` lifecycle
  action. A receipt is proof of the condition transition, not execution
  authority and not an implicit reopen.
- Managed Turn interprets the same quota packet. Heartbeat runs do not own a
  separate timer or readiness cache.
- The frontend extends the existing Todo defer/details surface with pending,
  ready, and receipt information. It does not add another configuration owner.
- Lark requires no condition-specific state owner: the Goal Channel consumes
  the same status/interaction projection and may render the receipt through its
  normal presentation sink.

This protocol does not alter periodic-report policy, scheduler cadence, or the
user's notification preference.

## Acceptance

1. Offset input round-trips as the equivalent UTC token; naive or invalid input
   fails before writeback.
2. Before due, CLI/quota/managed Turn agree that the Todo is waiting.
3. At due, they agree on `successor_replan_required` and the receipt identity.
4. A later tick and a fresh process reproduce that receipt exactly.
5. Frontend typecheck, route smoke, and packaged dashboard build pass.
