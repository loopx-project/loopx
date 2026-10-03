# LoopX usage collector

Cloudflare Worker + D1 for [basic usage statistics](../../docs/reference/usage-ping.md).
The TypeScript client/collector allowlist lives in
`loopx/control_plane/runtime/usage_statistics_contract.ts` and
`usage_statistics_diagnostics.ts`.

| Endpoint | Contract |
|---|---|
| `POST /v1/ping` | Daily random-ID heartbeat with version/OS/CPU/Python/channel; ≤1 KiB |
| `POST /v1/aggregate` | Fixed CLI counts, no installation ID or join key; ≤16 KiB |
| `POST /v1/goals` | Independent Goal/measurement/Host-day span/duration buckets, no identity; ≤16 KiB |
| `POST /v1/installation` | Installation-linked fixed daily CLI counts and independent interval-union minutes; ≤16 KiB; operator-only rows |
| `GET /v1/goal-stats` | Independent 30-day duration histograms; cells below 5 omitted |
| `GET /v0/stats` | Deduplicated active/new installations, including retained v0 clients; version/OS/CPU/channel breakdown |
| `GET /v1/aggregate-stats` | Independent 30-day feature/result/duration/error totals; cells below 5 omitted |
| `GET /v1/diagnostic-stats` | Independent versioned CLI/lifecycle marginals; cells below 5 omitted |
| `GET /v1/adoption-stats` | Mature 1/7/30-day installation return cohorts and 30-day activity-day buckets |
| `POST /v0/ping` | Retained six-field opt-in client contract; no new default-on clients use this route |

Heartbeats are deduplicated by installation/day and retained 400 days.
Aggregate requests merge directly into `usage_counts(day, feature, outcome,
duration, error, count)` and are retained 30 days. No raw request rows, ID,
version or per-request timestamps enter that table. Aggregate writes are lossy,
not idempotent: clients make no retry. The server uses its UTC reception date.
Clients may send multiple non-overlapping CLI batches within a day; the collector
adds each delta without requiring a schema migration. Delivery cadence does not
add a version or installation join key. Counters are estimates, not people,
accepted Goal outcomes or billing records.

The aggregate endpoint also accepts `loopx_usage_diagnostics_v1`.
`diagnostic_counts` stores fixed feature/sub-operation/result/reason/duration,
numeric version, UTC activity date, receipt date, voluntary deployment context
and receipt-backed lifecycle signal. It has no installation join key or raw
request rows. Activity dates older than seven days or in the future are rejected.
Keep 30 receipt days; legacy counters are never backfilled or reattributed.
Public endpoints expose independent marginals, not multi-dimensional histories.
Return cohorts count installation state, not people; `within_Nd` means any later
heartbeat within a mature N-day window, not exact day-N retention.

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

Existing v1 installations also apply `0002-goal-usage.sql` and then
`0003-goal-duration-sources.sql` before deploying the Goal-duration Worker.
Back up first. The latter adds `goal_duration_counts` and copies prior counts
as `host_call`/`unknown`, preserving the old table for rollback.
`quota_cycle`, `codex_turn` and `host_call` are overlapping populations and
must never be summed. The unreleased Goal payload requires measurement/Host
labels and uses `duration` instead of `execution`.
A Worker rollback can leave that additive table intact.

Before releasing notice-v5 clients, apply `0004-diagnostics.sql` with D1
migrations and deploy the updated Worker. It preserves existing installs,
pings and legacy counters. An older Worker rejects new diagnostics; loss is
not retried. Roll back the Worker/client without dropping the additive table.
Merging this code does not deploy the collector.

Before releasing notice-v6 clients, back up and apply additive migration
`0005-installation-usage.sql`, then deploy the Worker. `installation_usage`
keeps one snapshot per random installation/UTC activity day for 30 activity
days. Newer revisions replace counts, never add them; context and version
freeze per day. No historical profiles or runtime are backfilled. Rollback
retains the table, and all existing endpoint contracts remain supported.
Daily runtime is partial instrumented interval union, **not uptime**. Different
clocks cannot be added. [Operator read queries](queries/installation-usage.sql)
separate span, active days, fixed-family usage and measured minutes.
Only authorized D1/Access-protected operator surfaces may render linked rows;
do not add per-ID results to unauthenticated public stats.

Qualify `/v1/ping`, `/v1/aggregate`, `/v1/goals`, all stats endpoints, and invalid-field/size
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
including additive migrations and mature/suppressed cohorts; no production telemetry is needed for these tests.
