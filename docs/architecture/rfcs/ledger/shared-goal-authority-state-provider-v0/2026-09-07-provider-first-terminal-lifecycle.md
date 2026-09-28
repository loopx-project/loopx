# Provider-first terminal lifecycle checkpoint (2026-09-07)

Moved without content change from [shared-goal-authority-state-provider-v0.md](../../shared-goal-authority-state-provider-v0.md) (former section "Provider-first terminal lifecycle checkpoint (2026-09-07)") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

Promoted `complete`, `supersede`, and role-scoped `archive` now use one native
TypeScript transaction across file, NoKV, and PostgreSQL. The authority owner
decides actor/claim/lease admission; derives successor priority, capability and
Agent bindings, exclusions, continuation, and predecessor relations from typed
caller intent; reduces completion policy; commits the Todo/lease/head/outbox
write set with CAS; and persists replay receipts. Python remains an adapter for
registry facts, the caller-approved validation effect, intent/result transport,
and compatibility projection drain; it does not select a different terminal or
successor outcome for a provider. The legacy Markdown and event writers reuse
the same pure TypeScript successor decision before materializing their records.

Validation declarations cross the canonical boundary as a required marker and
SHA-256 digest only. Raw argv stays in a 0600 host-local sidecar and recovery
must prove the digest before executing it. This keeps provider heads portable
and public-safe without turning recovery into a silent validation bypass.
Imported v0 `index` remains the archive-order compatibility fact; native records
fall back to durable completion/update time and Todo identity. Legacy lease
files whose Todo no longer exists in the current canonical collection remain
historical audit material and are excluded from live projection.

Qualification uses one read-only, production-complex snapshot for three arms:
an immutable legacy baseline clone, an isolated file store, and an isolated
real PostgreSQL tenant. The provider heads compare exactly; the legacy result
compares through the declared compatibility projection. Archive comparison
removes provider-retained archived records and their historical leases from the
legacy hot view, and ignores absolute imported indexes only after separately
proving identical per-role relative order. Domain fields, archive selection,
active leases, and non-target records are never normalized; the source snapshot
must remain unchanged. The executable rehearsal is
`examples/control_plane/authority-three-arm-rehearsal.py`. A checked-in,
deterministic, public-safe scale fixture exercises the same distribution and
pressure, including hard-lease fences, across every provider conformance suite. It cannot replace the
read-only three-arm rehearsal because all providers share the new semantic
owner and can therefore agree on the same regression.

Every pull request that claims progress against this RFC follows the
[production-scale fixture stewardship contract](../../../../development/testing-and-quality.md#production-scale-fixture-stewardship--生产规模-fixture-维护契约).
It declares fixture impact, exercises every affected provider arm, and keeps
the read-only three-arm rehearsal as a separate promotion gate.

Legacy lifecycle field assembly now calls the single TS field planner described
in the [TS retirement checkpoint](../typescript-control-plane-migration-v0/2026-09-09-legacy-field-rule-retirement.md).
This removes Python decisions without changing the per-goal authority phase:
unpromoted goals still commit through the locked Markdown writer, while promoted
goals retain their existing provider transactions and unsupported-field fences.
The planner neither reads a provider nor grants a lease, CAS receipt, or write
permission. This checkpoint closes one rule owner, not the remaining mutation
inventory or local-store/promotion qualification.
