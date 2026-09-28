# Legacy field-rule retirement checkpoint

Moved without content change from [typescript-control-plane-migration-v0.md](../../typescript-control-plane-migration-v0.md) (former section "Legacy field-rule retirement checkpoint") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

`todos/field_update.ts` now owns the complete metadata intent assembly used by
the legacy `update`, `claim`, `complete`, and `supersede` line writer: status and
completion timestamps, omission versus explicit clears, binding precedence,
removed-policy repair, resume-generation pairing, and completion metadata.
It composes the existing TS completion rule directly. The replaced Python
decision branches and the last-caller `todo.completion_state.metadata_updates`
RPC/facade are removed, not kept as a fallback.

This is a pure plan, not admission or a provider commit. Python still owns
Markdown lookup/encoding, byte-level no-op detection, locking and external
effects. Public role/binding admission and the event writer are not declared
migrated by that slice. Native planning now composes this owner as described in
T1; unsupported fields gain no authority, no goal is promoted, and no third storage path appears.
Rejected plans now leave even the caller's in-memory line buffer unchanged;
public rejected transactions were already non-committing.

There is one field-plan crossing per legacy line write. It replaces the former
metadata RPC on ordinary edits; already-finalized completions with an override
gain one planning crossing. Cached codec normalization calls remain. This is
semantic deletion, not a claim of fewer crossings on every command. Retire the
adapter with its final legacy lifecycle caller after full-goal cutover, or fold
it into that caller's coarse transaction when migrating the caller; do not grow
a series of field-level RPCs. Retain Markdown rendering permanently.
