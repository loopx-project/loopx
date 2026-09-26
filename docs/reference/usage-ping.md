# Basic usage statistics

[中文](usage-ping.zh-CN.md)

Basic usage statistics are **on by default after visible first-use disclosure**.
They help prioritize supported platforms, understand continued use and find slow
or failing CLI entry points. They are not a measurement of accepted Goal
outcomes. No content collection is implemented.

```bash
loopx usage-ping status      # current policy, recipient and outgoing payload previews
loopx usage-ping disable     # stop both channels; delete local ID and pending counts
loopx usage-ping enable      # explicitly allow both channels after reading the disclosure
```

Settings → Capability Center exposes the same machine-wide switch and previews.
Opening settings or running these commands never sends a measurement. Settings
are owned by the TypeScript usage-statistics module, independently of a Goal's
File/SQLite/PostgreSQL provider; Python and the browser adapt that same owner.
Lark has no separate switch and cannot override the machine owner's choice.

## Product questions and exact scope

| Question | Evidence | Limit |
|---|---|---|
| Which versions/platforms need support? | Daily version, OS, CPU architecture, Python minor and install channel | Only reporting installations |
| Do installations keep using LoopX? | Random installation ID, deduplicated by UTC day | Installations, not people; reinstall/re-enable may count again |
| Which CLI entry points are used? | Fixed command-family counters | Polling and automation count too; not a measure of user value |
| Which commands fail or take time? | Result, typed error category, coarse elapsed-time bucket | CLI return status is not Goal acceptance; handled domain failures may return 0 |

The first version measures CLI invocations, including those made by agents.
Top-level `--help`/`--version` fast paths, native exec-replaced scheduler
followups, API-only interactions and individual App/Lark actions are not
instrumented. Heartbeats start in the background before dispatch; long-running server command
results are counted only when the CLI returns. There is no claim to complete product activity or task success rates.

## Two separate payloads

**Daily heartbeat** (`POST /v1/ping`), with exactly these fields:

```json
{"schema":"loopx_usage_ping_v1","install_id":"00000000-0000-4000-8000-000000000001","version":"1.2.0","os":"linux","arch":"x64","python":"3.13","channel":"pip"}
```

The ID is random, local to the installation and not derived from hardware or an
account. It enables cross-day association, so this is **not fully anonymous**.
OS is `darwin|linux|windows|other`, CPU is `x64|arm64|x86|other`, and channel is
`pip|local_release|source|unknown`. Version accepts only numeric major.minor.patch;
a custom version containing a private suffix is not sent.

**Closed-day CLI aggregate** (`POST /v1/aggregate`):

```json
{"schema":"loopx_usage_aggregate_v1","counters":[{"feature":"todo","outcome":"ok","duration":"lt_1s","error":"none","count":4}]}
```

No installation ID, version, timestamp, Goal or other join key is included.
The collector adds its reception day, merges counters, and stores no individual
request rows. Both sender and collector use the same strict TS allowlist:

- Feature: `status`, `quota`, `todo`, `turn`, `project`, `connect`, `pr-review`,
  `version`, `chat`, `other`. Unlisted commands become `other`, never raw names.
- Result: `ok`, `failed`, `cancelled`.
- Duration: `<100ms`, `100ms–<1s`, `1s–<10s`, `10s–<60s`, `≥60s`, encoded as
  `lt_100ms|lt_1s|lt_10s|lt_60s|gte_60s`. This measures command dispatch, not full
  interpreter startup or Goal duration.
- Error: `none`, `command_failed`, `timeout`, `connection`, `interrupted`.
  Typed exceptions supply categories; error messages are never parsed or sent.

No prompts, source code, paths, repositories, arguments, tool outputs, raw
errors, stack traces, Goal/Todo IDs or custom Agent/MCP names are collected.
Additional payload fields and invalid enum combinations are rejected.

## Disclosure and precedence

The first interactive CLI command prints the recipient, fields, purpose and
both disable mechanisms to stderr, records the disclosure, and sends nothing.
Later commands may measure/send. A fresh unattended installation does not
silently opt itself in: use the visible App setting or explicit CLI enable.
JSON stdout is unaffected. Previously enabled v0 clients keep their random ID
but must see the expanded-scope disclosure; previously disabled clients stay off.

An explicit stored disable blocks both channels. The following environment
settings also block both channels, even after explicit enable:

- `LOOPX_USAGE_PING=0|false|no|off`
- `DO_NOT_TRACK` set to a nonempty value other than `0`
- `CI` set to a nonempty value other than `0|false`

`LOOPX_USAGE_POLICY=consent_required` requires explicit enable; merely displaying
the notice is insufficient. Default policy is `opt_out`; unknown policies fail
closed. Distribution owners must choose the applicable policy before shipping;
this switch does not itself determine legal compliance, infer region from IP,
or replace any applicable consent requirement.

The project endpoint is
`https://loopx-usage-collector.huangrt01.workers.dev/v1/ping`.
`LOOPX_USAGE_PING_ENDPOINT` may override it with an HTTPS `/v1/ping` URL (HTTP is
allowed only on loopback for testing). Credentials, queries and fragments are
rejected. The aggregate destination is the same origin's sibling `/v1/aggregate`.
Recipient/policy changes invalidate the prior disclosure; explicit enable
confirms the new selection. Switching recipients clears buffered counts and
rotates the ID. Redirects are never followed.

## Delivery, local state and withdrawal

State remains in `~/.codex/loopx/usage-ping.json`, mode `0600`. It is not copied
into Goal state, backups of authority providers, or public projections. Normal
CLI invocation reads only a small local hint; a detached Node process owns
measurement, locks and network I/O. A first-use/settings operation may wait for
local Node execution, never for a collector connection.

Each installation attempts at most one heartbeat per UTC day. Counts are capped
at 128 distinct rows and 10,000 per row, then flushed on the first eligible
command after the UTC day closes. Unsent counts older than seven days are
discarded. An installation that never runs again will not flush its final day.
Lock contention, crashes and failed requests can lose counts. There are no
immediate retries and no durable network queue. Clock rollback does not reopen
a daily attempt. These are **lossy diagnostics**, not billing or audit records.

Requests have a three-second deadline, do not block command completion, and
cannot change its output or exit code. Disable deletes the local ID and buffered
counts; an old worker cannot restore them or send the next channel. A request
already handed to the network cannot be recalled. Re-enable uses a new ID.
Corrupt or unsupported state fails closed; explicit disable is the repair path.

## Collector and interpretation

[Collector source and upgrade instructions](../../apps/usage-collector/README.md).
Heartbeat history is retained for 400 days; aggregated counters for 30 days.
The existing `/v0/stats` endpoint continues to report deduplicated installations
from old and new heartbeat clients. `/v1/aggregate-stats` publishes independent
feature/result/duration/error totals, omitting cells below five. It does not
publish cross-dimensional combinations or per-install behavior histories.

Application code stores no client IP, user agent or Cloudflare request metadata;
Worker observability is disabled in the deployment template. Network providers
still handle connection metadata: separating bodies does not guarantee that
requests can never be correlated. Public unauthenticated counters can be
inflated, and suppression/loss makes these estimates unsuitable for billing.
The service may be unreachable on some networks; the owner can supply a reachable
collector, and LoopX continues to work without telemetry.
