# PostgreSQL authority service admission v0

This document defines the service-owned admission boundary for the switchable
PostgreSQL authority provider. It is an opt-in Stage 2B seam: it does not make
PostgreSQL the default, change file/SQLite selection, add a runtime caller, or
claim promotion readiness.

## Boundary

`PostgreSqlAuthorityService` receives an opaque transport credential, a tenant,
a goal, and the expected database-incarnation identity. It calls injected
authentication and tenant-authorization functions before opening the provider.
The credential is never persisted, passed to PostgreSQL, or exposed through the
provider-neutral `AuthorityStore` contract. A denied or unverifiable principal
fails closed before a database connection is opened.

The service returns one of these typed outcomes:

| Outcome | Meaning |
| --- | --- |
| `opened` | The principal is authenticated, authorized for the tenant, and the database identity matches the requested binding. |
| `principal_unauthenticated` | Authentication rejected the opaque credential or returned an invalid principal. |
| `principal_verification_unavailable` | Authentication could not be completed. |
| `tenant_unauthorized` | The authenticated principal is not allowed to use the tenant. |
| `tenant_authorization_unavailable` | Tenant policy could not be evaluated. |
| `store_identity_unavailable` | Provider metadata could not be read. |
| `store_identity_mismatch` | The requested incarnation is not the database's current incarnation. |

The service is intentionally in-process. A deployment supplies its own
transport, credential verifier, tenant policy, connection pool, and secret
handling. This module does not grant network access, actor ownership, lease
ownership, cross-host synchronization, or promotion authority.

## Restore-incarnation rotation

`rotatePostgreSqlAuthorityStoreIdentity` is an administrative operation owned by
the authenticated service deployment. It locks the singleton metadata row,
verifies the expected identity, and atomically writes a newly minted
`postgresql:<32 lowercase hex>` identity. It does not rewrite heads, commits,
events, receipts, goals, or operation IDs.

Provider revision tokens contain the database identity. Consequently, tokens
minted before a restore rotation become stale and conflict, while the durable
head and receipt history remain readable through a store opened with the new
identity. A wrong expected identity is rejected without a write. If the server
loses the response after `COMMIT`, the result is `ambiguous`; the service must
read metadata before retrying, rather than guessing whether rotation applied.

## Retained history and synthetic seed

PostgreSQL schema v1 keeps the latest head, an append-only checkpoint at
cursors 1, 65, 129, and so on, and one delta for each other new commit. Every
new journal row binds its resulting projection digest. A historical read
starts at the covering checkpoint (or a retained v0 full projection) and
checks the full cursor interval, including any prefix before the page. A
missing checkpoint, gap, or digest mismatch fails the scan. Events and
receipts remain tied to the original transaction.

Only an administrator runs `loopx postgres setup --execute`. When upgrading
v0, pass that database's existing `--store-identity`. Setup preserves full
projection rows and carries forward the journal's table-level `SELECT` and
`INSERT` grants to checkpoints, including grants through group roles. Setup
applies any explicit `--runtime-role` provisioning in the same transaction as
the format change under the lock used by writers and identity rotation. For
deployments with custom column-level privileges, provision the runtime role
explicitly. An already initialized v1 store can also use its existing identity
and `--runtime-role` to provision access. Keep a verified pre-upgrade backup and
quiesce old callers before upgrading. Verify historical reads and a new
checkpoint append with the restricted runtime credentials in an isolated
restore before activation. A rollback to v0 requires the verified v0 backup.

Use `loopx postgres local-to-pg-seed --goal-id <synthetic-goal>` to preview
a sanitized File or SQLite head at cursor 1. Preview never opens the target.
`--output` optionally creates a private file or reuses an identical regular
file. Existing different content and symbolic links cause the command to fail
before connecting to the target. With `--execute`, use a provisioned restricted connection via
`LOOPX_POSTGRES_URL` and optionally pin it with `--store-identity`. The seed
command neither installs a schema nor changes roles. It writes only to an empty
target Goal. Repeating the same seed command, including the same `--output`,
recovers its original receipt. Private
direction declarations and their dependent usage evidence are omitted together
if the declaration cannot be retained. Review the synthetic input before use:
the privacy filter looks for known private records and tokens, and cannot
classify arbitrary sensitive prose. The seed deliberately discards prior
history and does not migrate or activate a real Goal; use the retained journal
archive for history export, audit, or recovery.

## Validation

The public synthetic fixture is
[`postgresql_authority_service_v0.json`](../../tests/fixtures/control_plane/postgresql_authority_service_v0.json).
It covers authenticated admission, authentication denial, tenant denial,
identity drift, and restore rotation without credentials or private database
details.

Run the deterministic seam tests with:

```sh
npm run typecheck:control-plane
npm run test:postgresql-authority-service
```

Run the real PostgreSQL path against an isolated disposable database by setting
`LOOPX_TEST_POSTGRES_SERVICE_URL` and invoking the same script. The database
must be disposable and separate from any active goal or other PostgreSQL test
schema; the integration test mutates only its own tenant/goal and metadata.
