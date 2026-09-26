# Basic usage statistics

[中文](usage-ping.zh-CN.md)

Basic usage statistics are **on by default after visible first-use disclosure**.
They help prioritize supported platforms, understand continued use and find slow
or failing CLI entry points. They are not a measurement of accepted Goal
outcomes. No content collection is implemented.

```bash
loopx usage-ping status      # current policy, recipient and outgoing payload previews
loopx usage-ping disable     # stop all channels; delete local ID and pending counts
loopx usage-ping enable      # explicitly allow all channels after reading the disclosure
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

An explicit stored disable blocks all channels. The following environment
settings also block all channels, even after explicit enable:

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
cannot change its output or exit code. They use the supported Node runtime's
`HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` settings; proxy addresses and credentials
never enter telemetry payloads. Disable deletes the local ID and buffered
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

## Observed Goal duration

Goal timing answers whether observed work continues across hours or days. It
reports three **independent populations**; never add their durations or counts:

| Measurement | Boundary and coverage | Interpretation |
| --- | --- | --- |
| `quota_cycle` | Every Host using the shared quota CLI: first allowed `should-run` to successful executed `spend-slot`, including Codex App | Coarse progression time, including intervening pauses and waits |
| `codex_turn` | Exact accepted Codex task binding, timing events in that selected local Codex home | Finer execution intervals, including tool and approval waits |
| `host_call` | Managed `turn run-once` and regular owner Goal chat around actual Host invocation | Directly instrumented call intervals, including network/tool waits |

Repeated allowed quota reads preserve the first start. Denial, spend preview,
failed settlement and receipt repair do not finish a cycle. Successful settlement
replays emit no new timing marker and cannot extend its end. Exact Turn identity separates concurrent cycles;
without one, only a single inferred cycle per Goal/agent lane is measured. This
fallback cannot distinguish concurrent unbound cycles. Missing spend produces no
finished interval. These are diagnostic observations, never quota authority.

Codex discovery uses accepted Goal/agent/task bindings and the selected
`CODEX_HOME` read-only metadata database. It does not search other homes or infer
ownership from cwd. Timing extraction runs in detached processes triggered by
quota observations. It reads at most 1 MiB of new JSONL per observation (the tail
on first discovery), retains only timing cursors and open Turn identity locally,
and never uploads transcript content. A terminal written after spend is picked
up on a later observation; this is not a global session watcher. Missing or
ambiguous bindings and unavailable files leave the common quota cycle working.
No historical backfill or extrapolation of crashed sessions occurs.

Within each Goal/measurement/Host series, **span** is first to most recent
observed activity, including pauses; **duration** is the union of observed
intervals. Parallel or nested overlap counts once within that series. Both stop
growing without new evidence. They are not Goal age, CPU time, completion or
billing evidence. Fixed Host labels are `codex_app`, `codex_cli`, `claude_code`,
`dsh`, `opencode`, `trae`, `other`, `unknown`; custom names never go on the wire.

One cumulative snapshot per observed series/UTC day is claimed after that day
closes, when another observation or normal usage occurs. Unfinished Goals count;
quiet Goals are not counted daily. Managed Host calls checkpoint every minute.
Counts are **Goal/measurement/Host-day observations**, not unique Goals or users.
The collector cannot join a Goal across days or machines.

The observer is independent of File/SQLite/PostgreSQL state ownership and never
changes authority state. Collection starts after notice acknowledgment. Disable,
recipient changes or clearing local state restart measurement. Crashes, missing
checkpoints, contention, offline collectors and limits can lose observations;
these are partial measurements, not a complete execution accounting system.
Local limits are 64 series, 512 recent disjoint intervals per series, 128 cycles,
64 Codex cursors. Closed cycles and least-recently-read cursors yield capacity to
new work; inactive cursors expire after seven days. Intervals older than 14 days compact into totals; series expire
after 90 inactive days. Observations may arrive seven days late; coarse/fine
intervals longer than seven days are discarded. Direct Host checkpoints longer
than two minutes are discarded as unproven scheduling suspension. Unsent daily
snapshots expire after seven days. No retries require an outgoing identity.

The outgoing Goal payload is strictly allowlisted:

```json
{"schema":"loopx_goal_usage_aggregate_v1","counters":[{"measurement":"quota_cycle","host":"codex_app","span":"lt_7d","duration":"lt_6h","count":1}]}
```

Both durations use `lt_1m`, `lt_10m`, `lt_1h`, `lt_6h`, `lt_1d`, `lt_7d`,
`lt_30d`, `gte_30d`. No Goal ID, installation ID, path, name, event time or free
text is sent. `/v1/goals` ingests counts; `/v1/goal-stats` returns separate
measurement histograms over 30 receipt days, omitting cells below five.

The existing settings switch, environment opt-outs and consent policy control
all channels and local timing reads. Settings and `loopx usage-ping status`
show `goal_preview`, a local snapshot rather than a delivery receipt. Expanded
scope requires notice version 3; an existing explicit disable persists.

Before shipping the client, back up D1, apply `0002-goal-usage.sql` and
`0003-goal-duration-sources.sql`, then deploy the Worker. The latter migrates
previous Goal counts into `host_call`/`unknown` without deleting the old table;
existing heartbeat and CLI counts remain intact. The unreleased Goal v1 payload
now requires measurement/Host labels and `duration` in place of `execution`.
