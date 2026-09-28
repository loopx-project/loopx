# Provider-neutral projection conformance checkpoint (2026-09-12)

Moved without content change from [typescript-control-plane-migration-v0.md](../../typescript-control-plane-migration-v0.md) (former section "Provider-neutral projection conformance checkpoint (2026-09-12)") on 2026-09-26; RFC baseline `3e443ad7c`. Checkpoint records live in this ledger, not in the RFC body.

The conformance boundary now has one projection-fixture builder for both the
legacy v0 and native Todo record shapes. It owns deterministic Unicode ordering,
read-model digest/field construction, and the compatibility-only conversion;
provider tests no longer hand-rebuild those fields. The scale envelope declares
status ordering explicitly and validates its counts, so changing JSON key order
cannot silently change which Todo receives a lease, successor, or archive role.

The File, SQLite, and NoKV suites now execute the same production-scale terminal
cases in both record shapes. A separate parity harness replays one seed,
observation, and lease sequence through all three isolated providers and compares
the logical head plus committed event/projection/receipt trace while ignoring
provider-specific revision tokens. This is conformance evidence, not a new
authority writer, provider default, or promotion claim; PostgreSQL remains under
its existing real-service qualification gate.

The old v0 consumer manifest remains readable and retains all existing fields.
Default Markdown capture still emits v0; this PR neither rewrites stored heads
nor auto-promotes a goal. The schema split is not permission to drop v0
provenance or change legacy ordering during a later migration.
