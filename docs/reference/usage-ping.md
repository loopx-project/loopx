# Usage ping

[中文](usage-ping.zh-CN.md)

LoopX can send one anonymous, opt-in ping per day so maintainers can count
active installations instead of estimating them. It is **off by default**:
nothing is read, written, or sent until the machine owner runs
`loopx usage-ping enable`, and LoopX never prompts for it.

```bash
loopx usage-ping            # show consent, whether sending is active, and the exact payload
loopx usage-ping enable     # opt in; creates a random installation id
loopx usage-ping disable    # opt out; forgets the installation id
```

## Payload

The whole request body is this JSON object (`loopx_usage_ping_v0`). There are
no other fields, and the collector rejects any body with extra fields.

| Field | Example | Meaning |
|---|---|---|
| `schema` | `loopx_usage_ping_v0` | Payload version. |
| `install_id` | `3f0c…-4…` | Random UUIDv4 created by `enable`. Not derived from the machine, user, or account. |
| `version` | `1.2.0` | LoopX package version. |
| `os` | `darwin` | One of `darwin`, `linux`, `windows`, `other`. |
| `python` | `3.12` | Python major.minor. |
| `channel` | `pip` | How LoopX was installed: `pip`, `local_release`, `source`, or `unknown`. |

LoopX does not read or send project names, goal or todo contents, paths,
hostnames, usernames, account ids, command lines, environment variables, or
error reports. `loopx usage-ping` prints the next payload verbatim so you can
check it.

## When it sends

- Only when consent is `enabled`, a collector endpoint is configured, and no
  switch below blocks it.
- At most once per UTC day, triggered by the first `loopx` command of the day.
  The request runs in a detached background process with a 3-second timeout,
  so it never slows down or fails the command. A failed request is not retried
  until the next day.
- `loopx usage-ping` itself never triggers a send.

The endpoint is `LOOPX_USAGE_PING_ENDPOINT` if set, otherwise the release
default. It must be `https://` (plain `http://` is accepted only for
loopback addresses, for local testing). Until the project collector is
deployed the release default is empty, so enabling records consent but sends
nothing; `loopx usage-ping` shows `sending: false` in that case.

## Switches

Any of these blocks sending, whatever the stored consent:

| Variable | Blocks when |
|---|---|
| `LOOPX_USAGE_PING` | `0`, `false`, `no`, or `off` |
| `DO_NOT_TRACK` | set to anything other than empty or `0` |
| `CI` | set to anything other than empty, `0`, or `false` |

## Local state

Consent lives in `~/.codex/loopx/usage-ping.json` (`loopx_usage_ping_state_v0`,
file mode `0600`). It is machine-level, not per project. `disable` rewrites it
without the id, so a later `enable` is a new, unlinkable installation. Deleting
the file returns the machine to `undecided`.

## Collector and published numbers

The collector is a small Cloudflare Worker with a D1 database whose source is
in [`apps/usage-collector`](../../apps/usage-collector/README.md). It stores
only the payload fields plus the UTC day. It does not store IP addresses,
user agents, or request metadata, and deletes rows after 400 days.

Its public `GET /v0/stats` endpoint publishes:

- `monthly_active`: distinct installation ids with at least one ping in a
  calendar month (UTC);
- `new_installs`: ids first seen in that month;
- `rolling_30d_active` and `daily_active` for the last 30 days;
- a current-month breakdown by version, OS, and channel, where any bucket with
  fewer than 5 installations is merged into `other`.

These are lower bounds. They count only machines that opted in, and a machine
that is disabled and re-enabled counts as a new installation.
