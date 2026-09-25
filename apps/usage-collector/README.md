# LoopX usage collector

Receives the opt-in `loopx usage-ping` payload and publishes aggregate counts.
The client contract (payload, switches, consent) is in
[docs/reference/usage-ping.md](../../docs/reference/usage-ping.md).

It is a Cloudflare Worker backed by a D1 (SQLite) database:

| File | Role |
|---|---|
| `src/collector.js` | Validation, storage, stats, and retention. No Cloudflare APIs, so tests run it on local SQLite. |
| `src/worker.js` | Binds `collector.js` to the Worker `fetch` and `scheduled` handlers. |
| `schema.sql` | `installs(install_id, first_day)` and `pings(day, install_id, version, os, python, channel)`. |
| `wrangler.example.toml` | Deployment template. |
| `test/collector.test.mjs` | `node --test` suite; `tests/test_usage_collector.py` runs it in the Python lane. |

## Endpoints

- `POST /v0/ping`: JSON body of at most 1024 bytes with exactly the six payload
  fields. Returns `204`, or `400` / `413` / `415` for invalid input. One row is
  kept per installation per UTC day; later pings that day update it.
- `GET /v0/stats`: public `loopx_usage_stats_v0` document with 12 months of
  `monthly_active` and `new_installs`, `rolling_30d_active`, 30 days of
  `daily_active`, and a current-month version / OS / channel breakdown. Buckets
  under 5 installations are merged into `other`. Cached for an hour and
  CORS-open so the site or README badges can read it.

## What is stored

Only the payload fields and the UTC day. The Worker does not read or store the
client IP, user agent, `cf` request metadata, or headers, and Workers
observability logs are disabled in the template. A daily cron deletes pings
older than 400 days and installations with no remaining pings (an
installation that comes back after that counts as new).

## Deploy

```bash
cd apps/usage-collector
npx wrangler d1 create loopx-usage              # note the database_id
cp wrangler.example.toml wrangler.toml          # fill in database_id; wrangler.toml stays untracked
npx wrangler d1 execute loopx-usage --remote --file schema.sql
npx wrangler deploy
curl -s https://<worker-host>/v0/stats          # should return loopx_usage_stats_v0 with zeros
```

Then point a client at it without a release:

```bash
LOOPX_USAGE_PING_ENDPOINT=https://<worker-host>/v0/ping loopx usage-ping
```

Clients send by default only after `DEFAULT_ENDPOINT` in
`loopx/usage_ping.py` is set to the deployed URL and released.

## Limits

- Counts are a lower bound: only opted-in machines are counted.
- The endpoint is unauthenticated, so anyone can post random ids and inflate
  counts. Validation caps the damage per request; if abuse shows up, add a
  Cloudflare rate-limiting rule on `/v0/ping` rather than storing IPs.
