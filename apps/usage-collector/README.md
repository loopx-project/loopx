# LoopX usage collector

Cloudflare Worker + D1 for [basic usage statistics](../../docs/reference/usage-ping.md).
The TypeScript client/collector allowlist lives in
`loopx/control_plane/runtime/usage_statistics_contract.ts`.

| Endpoint | Contract |
|---|---|
| `POST /v1/ping` | Daily random-ID heartbeat with version/OS/CPU/Python/channel; ≤1 KiB |
| `POST /v1/aggregate` | Fixed CLI counts, no installation ID or join key; ≤16 KiB |
| `GET /v0/stats` | Deduplicated active/new installations, including retained v0 clients; version/OS/CPU/channel breakdown |
| `GET /v1/aggregate-stats` | Independent 30-day feature/result/duration/error totals; cells below 5 omitted |
| `POST /v0/ping` | Retained six-field opt-in client contract; no new default-on clients use this route |

Heartbeats are deduplicated by installation/day and retained 400 days.
Aggregate requests merge directly into `usage_counts(day, feature, outcome,
duration, error, count)` and are retained 30 days. No raw request rows, ID,
version or per-request timestamps enter that table. Aggregate writes are lossy,
not idempotent: clients make no retry. The server uses its UTC reception date.
Counters are estimates, not people, accepted Goal outcomes or billing records.

Neither handler reads/stores IP, user agent or Cloudflare request metadata.
The template disables Worker observability; Cloudflare still handles network
metadata. Do not describe the identified heartbeat as fully anonymous, or the
separate requests as impossible to correlate. The unauthenticated endpoint can
be inflated; use edge rate limiting if needed, not a new stored IP identifier.

## Fresh deployment

```bash
cd apps/usage-collector
npx wrangler d1 create loopx-usage
cp wrangler.example.toml wrangler.toml   # set database_id; ignored local configuration
npx wrangler d1 execute loopx-usage --remote --file schema.sql
npx wrangler deploy
```

## Upgrade an existing v0 deployment (including #5111)

Back up D1 before changing it. Apply the additive migration exactly once using
D1 migrations; **do not apply it to a fresh schema that already has `arch`**.
The existing installs and pings are retained. Old clients continue to work.

```bash
npx wrangler d1 export loopx-usage --remote --output /safe/backup/usage-before-v1.sql
npx wrangler d1 migrations apply loopx-usage --remote
npx wrangler deploy
```

Qualify `/v1/ping`, `/v1/aggregate`, both stats endpoints, and invalid-field/size
rejections on a separate database first. Deploy the collector before releasing
the new client default: the v0-only Worker does not accept v1 requests. Server
rollback can restore the prior Worker without dropping the additive columns
or counter table; v0 clients still work, v1 clients fail silently until restored.

The existing project service is
`https://loopx-usage-collector.huangrt01.workers.dev`; v1 deployment is a separate
operational step from merging client code. Some networks cannot reach workers.dev.
`LOOPX_USAGE_PING_ENDPOINT=https://<host>/v1/ping` selects another collector.
Changing destinations requires new disclosure and rotates the local ID.
Distribution owners can set `LOOPX_USAGE_POLICY=consent_required`; no region
inference or legal-compliance assertion is supplied by this setting.

## Validate

```bash
node --no-warnings --experimental-strip-types --test apps/usage-collector/test/collector.test.mjs
node --no-warnings --experimental-strip-types --test tests/control_plane_ts/usage_statistics.test.ts
```

Run from repository root. The collector suite executes actual SQL in SQLite,
including the v0 migration; no production telemetry is needed for these tests.
