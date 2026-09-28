# Cross-RFC semantic and presentation conformance checkpoint (2026-09-12)

Moved without content change from [shared-goal-authority-state-provider-v0.md](../../shared-goal-authority-state-provider-v0.md) (former section "Cross-RFC semantic and presentation conformance checkpoint (2026-09-12)") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

The TypeScript migration and this provider RFC now share one explicit Todo
semantic boundary. Python production callers import `todos/todo_semantics.py`
directly; `todos/projection.py` is retained only as an import-compatible facade
for external integrations. This is an ownership cleanup, not a second kernel.
The typed TypeScript `projection_delivery` union also owns the distinction
between mutation intent (`pending`/`not_required`) and provider readback
(`delivered`/`current`); unknown states fail closed before acknowledgement.

Priority intent now follows the same admitted create/update transaction on File,
SQLite and PostgreSQL. Explicit set/clear, omission and conflicting legacy text
are resolved by `todos/priority.ts`; Python reads share the generated grammar.
Markdown remains compatible display, while native records retain matching
priority/title. CLI and reviewed Chat edits preserve CAS and historical retry
identity. Real backend readback and a disposable clone of a long-lived local
Goal qualify this bounded change. See the [caller contract](../../../../project-agent-todo-contract.md#priority-intent).
This does not change provider defaults or close the remaining promotion gates.

Presentation is canonical at the projection layer, not in the domain record.
`source_section` and `index` are the v0 wire shape's display coordinates, while
native records derive the same display section from role/archive state and use
timestamp plus Todo identity as a deterministic fallback instead of a fake
persistent index. The normalized presentation metadata is therefore one
contract even when the wire shapes differ. The same rule is exercised by the
production-scale fixture and by File, SQLite, and NoKV conformance arms.
Provider revision tokens remain provider-owned and are compared only for the
provider-specific replay rules; they are not normalized into Todo semantics.

This checkpoint changes read/ordering and compatibility-adapter semantics only:
it does not promote a provider, add a writer, alter the transaction decoder
delivered by #4280, or make Markdown a second authority. The shared RFC still
owns durable truth, recovery, cutover, and projection delivery; the TS RFC owns
business-rule ownership and caller deletion.
