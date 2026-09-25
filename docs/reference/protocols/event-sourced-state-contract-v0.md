# Retired: experimental Todo event source

This experimental protocol is retired by PR #5054. There is no current
`loopx.event_sourced_state` API, Todo event replay, Markdown backfill or event
completion route. Historical references to this page describe the former design.

Use [shared Goal authority and providers](../../architecture/rfcs/shared-goal-authority-state-provider-v0.md)
for canonical Todo storage and migration. Legacy Markdown remains supported;
nonempty old event sources fail explicitly instead of disappearing into fallback.
Preserve their bytes and inspect/export with a compatible older release before
removing them from the active source location. This release never deletes them.

The default filename is `events.jsonl`; old `state_event_log`, `state_events_file`
and `event_log` aliases are checked too. Empty files are allowed. Promoted Goals
use their provider even when unrelated old files remain. Source qualification
continues checking captured source bytes; no event writer or event outbox was added.

[Supervisor](peer-supervisor-v0.md) logs are independent, experimental, local-private
proposal/receipt records. They neither own Todos nor migrate Goal state.
