# Remote authority task service v0 (candidate)

This opt-in source-checkout service lets independent clients claim a canonical
task, record progress, renew/release its execution lease and read back the result
over authenticated HTTPS. It reuses PostgreSQL service admission and the existing
Todo/lease transaction owners. It does not select a provider, seed or migrate a
Goal, expose arbitrary authority commits, or enable a shipped App/CLI route.

## Ownership and access

`RemoteAuthorityService` owns transport admission, bounded result projection and
request-to-receipt binding. `PostgreSqlAuthorityService` opens the tenant/Goal
store; `executeCoordinationTodoClaim`, `executeCoordinationTodoUpdate` and
`executeCanonicalTaskLeaseLifecycle` remain the business decision owners.
The PostgreSQL store retains atomic head/event/receipt persistence and CAS.

The service operator supplies TLS, a private policy file and a database role.
Clients receive only a service credential and their explicit scope. They cannot
submit database credentials, a principal identity, a clock, a registry, a raw
projection or a local filesystem path. Each policy grant binds an authenticated
principal to a tenant, Goal, actor and allowed operations. Each scope also pins
the expected database incarnation and its service-owned registered actor roster.
This explicit candidate roster does not mirror or promote a live local registry.

The policy schema is `loopx_remote_authority_policy_v0`:

```json
{
  "schema_version": "loopx_remote_authority_policy_v0",
  "scopes": [{
    "tenant_id": "tenant-one",
    "goal_id": "goal-one",
    "store_identity": "postgresql:cccccccccccccccccccccccccccccccc",
    "registered_agents": ["actor-a"]
  }],
  "principals": [{
    "principal_id": "principal-a",
    "credential_sha256": "<SHA-256 of the service credential>",
    "grants": [{
      "tenant_id": "tenant-one",
      "goal_id": "goal-one",
      "actor_id": "actor-a",
      "operations": ["read", "claim", "progress", "renew", "release", "complete", "block", "defer", "todo_add", "snapshot"]
    }]
  }]
}
```

These are synthetic scope values; replace the identity and credential digest
before use. Generate a high-entropy credential (at least 32 printable ASCII
characters) and deliver it privately. Keep the policy, TLS private key and
database configuration outside version control. Publish policy changes by
atomically renaming a complete replacement file. Missing, malformed or unreadable
policy fails closed; every request rereads it, including reused TLS connections.
Revocation prevents subsequent admissions. A request already admitted under a
policy snapshot may finish and retains that snapshot's revision in its receipt;
revocation does not claim to undo an in-flight commit.

## Commands and recovery

POST JSON to `/v0/tasks/execute` with a `Bearer` credential. Every command includes
`schema_version: "loopx_remote_authority_request_v0"`, `tenant_id`, `goal_id`,
`actor_id`, `store_identity`, `todo_id` and `operation`. Task IDs use canonical
`todo_<token>` syntax. Unknown fields and operations are rejected.

| Operation | Additional fields | Behavior |
| --- | --- | --- |
| `read` | None | Bounded current task and lease state; no lease key. |
| `claim` | `request_id` | Atomically claim and acquire managed execution under `hard_lease`. |
| `progress` | `request_id`, `claim_request_id`, `note` | Update a note using the original claim and current execution fence. |
| `renew` | `idempotency_key`, `expected_version`, `ttl_seconds` | Renew the current lease through the existing lifecycle owner. |
| `release` | `idempotency_key`, `expected_version` | Release execution; this does not clear the task's assignment or complete it. |
| `complete` | `request_id`, `claim_request_id`, `evidence`, `note?` | Settle task completion with verified evidence string, release lease, and generate receipt. |
| `block` | `request_id`, `reason`, `claim_request_id?`, `resume_when?`, `note?` | Pause task as blocked with explicit reason, releasing execution lease. |
| `defer` | `request_id`, `reason`, `resume_when`, `claim_request_id?`, `note?` | Defer task with resumption condition and rationale, releasing execution lease. |
| `todo_add` | `request_id`, `text`, `priority?`, `role?`, ... | Create a canonical task under policy grant and tenant fence. |
| `snapshot` | `todo_id?` (optional) | Read-only projection of Goal todos, leases, and deterministic cross-machine receipt digest. |

Use the lease key/version returned by claim or renewal for lifecycle commands.
Progress names the original claim's client `request_id`; normal activity can
maintain that execution and advance its lease version. Before a later lifecycle
command, use `read` to confirm the same owner/epoch and obtain the current version
while retaining the original lease key. Stale versions fail without an automatic
rebase. Versions are non-negative safe integers, including `0` on existing
canonical leases. Releasing an absent lease with version `0` seals a successful
no-op receipt with `missing: true`, `released: false` and `changed: false`;
replay and recovery retain that outcome without creating a lease.
Progress cannot adopt another execution generation.
Remote claims and lease lifecycle commands require `hard_lease` in the canonical
observation used to prepare the commit, including every claim CAS retry. A
concurrent switch to `soft_claim` rejects the remote operation without changing
work or recording a receipt. Local soft claims and cleanup retain their existing
behavior; historical hard-lease receipts keep the same identity.
Read responses retain current lease facts even when a canonical task has a long
note. If the note's JSON encoding exceeds 4 KiB, the response omits `note` and
sets `todo.note_omitted: true`; the stored note remains unchanged. The overall
response limit remains 64 KiB.
Claim/progress request IDs identify immutable intent within
the principal/tenant/Goal/actor scope. Reusing one with different intent fails.
Renew/release retain the canonical lease operation identity. Their receipts also
verify the admitted principal, actor and request digest before reuse.

The service clock decides expiry. Mutations require `hard_lease`; normal task
eligibility, write-scope and lease checks still apply. A receipt is historical
evidence, not permission to execute after expiry, release or takeover.

A timeout, lost response or unverified reply is **ambiguous** for a mutation.
The client checks the operation-specific result evidence as well as the response
schema and request digest. Missing or invalid operation, task, lease or receipt
fields cannot produce a successful client exit; recovery/read failures remain
`unavailable` and mutation replies remain `ambiguous`.
The client never retries automatically. Retain the exact command and send it to
`/v0/tasks/recover` (the example client uses `--recover`). A `found` response
includes `historical_only: true` and a digest of the stored receipt; `missing`
does not execute anything or prove that another in-flight attempt cannot commit.
Reconcile current state before explicitly retrying the same intent/identity;
never substitute a fresh ID to hide uncertainty. Even a successful historical
recovery does not reacquire execution authority.

Admission metadata is written inside the existing business receipt transaction,
not to a second ledger. A request pins every read and commit to its admitted
database incarnation. Receipt presence or absence is checked against the identity
from the same PostgreSQL read transaction. Rotation makes an old binding unavailable; the provider's
transactional CAS also fences rotation after a read. Following a reviewed restore,
the operator must explicitly authorize the new identity. The same principal and
intent can then query retained historical receipts with that new binding.

## Running the candidate

Use the repository's qualified Node runtime and `npm ci --ignore-scripts`.
Provision an isolated PostgreSQL authority schema/head using the existing
provider administration path. The listener never installs schemas or seeds
state. Use a restricted runtime role: metadata SELECT, tenant tables
SELECT/INSERT and heads UPDATE, with existing forced RLS; never a database owner
or superuser. Schema setup and incarnation rotation remain separate administration.

Set `LOOPX_AUTHORITY_DATABASE_URL` privately on the server, then run:

```sh
node --experimental-strip-types examples/coordination/remote-authority-service.ts \
  --policy "$SERVICE_POLICY" --cert "$TLS_CERT" --key "$TLS_KEY" \
  --host 127.0.0.1 --port 8443
```

For a reachable remote listener, choose an explicit bind address and a certificate
valid for its hostname. The client always verifies TLS; private CAs use `--ca`.
Set `LOOPX_AUTHORITY_CREDENTIAL` privately on the client, put the complete command
JSON in a file, and run:

```sh
node --experimental-strip-types examples/coordination/remote-authority-client.ts \
  --endpoint "$SERVICE_HTTPS_ORIGIN" --ca "$TLS_CA" < command.json
```

Add `--recover` for a read-only receipt query. Credentials do not enter arguments,
command JSON or receipts. Responses contain typed outcomes and reason codes,
not raw provider errors or filesystem details. No browser/cookie authentication,
CORS or redirects are supported. Input/output are bounded to 16/64 KiB; the
default listener admits at most 16 concurrent requests and the launcher pools
at most eight database connections with query, statement and lock deadlines.
SIGTERM/SIGINT stop new dispatches and allow sockets five seconds to drain,
then close remaining connections, including partial TLS/HTTP requests. Already
dispatched work still finishes before the pool closes, even if its response
socket has gone away. Disconnecting a client does not establish that its write rolled back.
Stop the listener and remove access grants to disable this candidate; existing
local provider selection is unaffected and durable receipts remain in PostgreSQL.

## Qualification and remaining work

`npm run test:remote-authority` runs decoding/policy/TLS tests. With
`LOOPX_TEST_POSTGRES_URL` pointing to a **disposable administrative test server**,
it additionally creates a private test database and a restricted runtime role,
starts real TLS server/client processes, and checks races, replay, revocation,
expiry/takeover, incarnation rotation and response loss followed by restart.
It removes its own database and role afterwards. The dedicated PostgreSQL CI
workflow requires a real integration subtest; a skipped backend is not a pass.

This qualifies a source candidate on one host with independent client processes
and real PostgreSQL. It does not qualify two physical hosts/cloud deployment,
distributed clock discipline, high availability, capacity/soak, shared budgets,
Host supervision, App/CLI integration or provider/default promotion. Those remain
with the existing shared-authority P lane and roadmap R6/D1–D3 owners under
[#3245](https://github.com/loopx-project/loopx/issues/3245). No new scheduler,
registry, storage implementation or broad runtime RPC is introduced.
