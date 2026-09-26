# Retire the experimental Todo event source

## Delivery brief

- Goal/source: roadmap R5/S12, TypeScript T4 and shared-authority D1–D3;
  maintainer direction on #5054 retires the old Todo event route.
- Gap: a second Todo projection/writeback pipeline was being extended solely to
  capture a retired source. Supervisor proposal/receipt logging was its remaining
  independent product caller.
- Result: delete Todo replay/overlay/backfill/completion and the unused migration
  bridge. Keep Markdown compatibility and provider authority; detect nonempty
  retired sources rather than silently omit their records.
- Owners: Todo source admission; existing TS completion/authority owners;
  experimental supervisor log under `control_plane/agents`.
- Acceptance: source selectors refuse without writes or validation effects;
  canonical reads ignore stale legacy files; normal completion/successors and
  downstream status/quota/review-packet work; supervisor concurrent receipts,
  preview, conflicting identity and uncertain-publication replay are covered.

## Compatibility and scope

A nonempty `events.jsonl`, `state_event_log`, `state_events_file` or `event_log`
source refuses legacy Todo reads/writes and shadow qualification. The operator
must preserve it and export/inspect its Todos with a compatible older release
before deliberately removing the binding/file from the active source location.
There is no automatic replay, deletion, Markdown fallback or new migration API.
Absent and zero-byte files have no event-owned Todos. Previously prepared
unsupported event outbox records remain rejected; this PR does not certify them.

The supervisor is experimental/default-off. Its `supervisor_log_event_v0`
envelope accepts only local-private proposals/receipts. Old experimental log
formats require manual archival before a fresh log; unknown formats fail without
rewriting. Admission and publication share the log lock; execution replay checks
full semantic identity while allowing a new observation timestamp. Preview never
publishes or syncs the log. A durable executed receipt prevents a second executed
receipt; this does not close the crash interval between an external host effect
and its receipt. External-effect fencing retains its own roadmap acceptance.

## Remaining boundary

This supersedes older entries calling for event-writer binding/capture. It does
not recount already delivered transaction capture, nor subtract a PR from a
fixed total. Remaining exits are executor-effect fencing, qualified whole-Goal
migration/rollback, default onboarding and bounded deletion of reachable Python
writers. D2 backend/capacity/soak and #4931 remain independent evidence. PostgreSQL
continues using the same authority contract; no new provider is added here.
