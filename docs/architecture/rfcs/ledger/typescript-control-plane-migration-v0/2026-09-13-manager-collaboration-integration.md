# Manager collaboration integration checkpoint (2026-09-13)

Moved without content change from [typescript-control-plane-migration-v0.md](../../typescript-control-plane-migration-v0.md) (former section "Manager collaboration integration checkpoint (2026-09-13)") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

At `7eb4b7bb1661bd5eff63a8725a33169792d5964b`, #4152 is the merged
lease-fenced text/note update slice; #4121's SQLite candidate is also merged,
without provider promotion. These actual heads supersede the earlier execution
card's pending-code implication, not its qualification holds.

The [manager/handoff RFC](../../capable-manager-semantic-handoff-v0.md) follows this
RFC's transaction-payoff rule. Its proposed collaboration owner replaces one
complete request transaction and old semantic callers; it does not introduce
a leaf RPC per field, a new TS daemon, or another Todo/Vision/lease authority.
Existing `coordination/todo_continuation.ts` is a promoted-local, same-machine,
registered-agent, lease-free Todo path, not a general pre-Todo/cross-Goal
handoff. Retain its actual compatibility semantics while integrating it.
M2 reports the migration economics receipt and cross-commit recovery evidence;
M1 normal host tools need not wait for full TS or provider migration.
Shared Goal amendments retain their own proposal/commit boundary, and
shared-authority D1–D3/T4 conditions remain applicable to any affected storage
or full-writer retirement. No new runtime behavior is delivered by this note.
