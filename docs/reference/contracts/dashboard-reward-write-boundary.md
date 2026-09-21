# Dashboard Reward Write Boundary

LoopX can already validate a dashboard reward draft through
`POST /reward/dry-run`. A browser-side append path is a separate capability and
must stay opt-in. The default dashboard must remain read-mostly: status export,
run-history inspection, and dry-run validation are allowed; writing compact
reward overlays is not enabled by loading the dashboard.

This document defines the boundary for the opt-in `POST /reward/append`
endpoint. It is implemented only for loopback `loopx serve-status`
sessions started with the explicit write flag.

## Current State

- `loopx reward` is the canonical writer.
- `loopx serve-status` serves `GET /status.json` and
  `POST /reward/dry-run` on loopback by default.
- `loopx serve-status --enable-reward-write-api` also serves
  `POST /reward/append` on loopback.
- The dry-run endpoint validates goal id, selected run timestamp, reward value,
  public-safe text, and run artifact availability.
- Dry-run responses are compact, return `appended=false`, and include a
  `preview_id`.
- The dashboard can show the CLI command and the dry-run result. With the
  explicit write flag, it can append the reviewed preview to `index.jsonl`.

## Append Preconditions

A browser append endpoint may be implemented only when all of these are true:

- The server is started with an explicit write flag, for example
  `--enable-reward-write-api`. The flag must default to off.
- The server binds to loopback only. A non-loopback bind should reject enabling
  reward writes.
- The server rejects append requests from non-loopback browser origins.
- The append request targets an exact `goal_id` and `run_generated_at`; appending
  to an implicit latest run from the browser is not allowed.
- The payload has already passed the same validation as `/reward/dry-run`.
- The response remains compact and does not return `index_path`, `json_path`,
  `markdown_path`, local absolute paths, or raw private evidence.
- The trusted loopback adapter records `actor_kind=owner` in both preview and
  append receipts; the canonical CLI requires an explicit owner/controller
  actor kind for a durable write.

## Preview Handshake

Append should be a two-step flow:

1. The dashboard calls `POST /reward/dry-run` with the selected goal, selected
   run timestamp, and compact reward fields.
2. The server returns a `preview_id` derived from the selected run key, compact
   reward payload, and current raw index record count.
3. The dashboard can enable an append control only for that exact preview.
4. `POST /reward/append` recomputes the preview id and rejects the request if
   the payload changed, the selected run changed, or the raw index record count
   changed since preview.

This keeps the UI from appending feedback to a stale or different run after the
operator has reviewed a dry-run result.

## Request Shape

Future append requests should accept only compact fields:

```json
{
  "goal_id": "example-experiment-goal",
  "run_generated_at": "2026-06-01T00:00:00+00:00",
  "preview_id": "opaque-preview-id",
  "decision": "continue_route",
  "reward": "positive",
  "reason_summary": "comparable validation improved and the route is worth extending",
  "follow_up": "promote the route to the next longer-window check"
}
```

The endpoint should reject unknown or private-looking fields rather than ignore
them silently. Rejected text should reuse the same private-pattern checks as
`loopx reward`.

## Origin And Capability Checks

The write path needs stricter browser checks than the dry-run path:

- Allow CORS for the append endpoint only from configured loopback dashboard
  origins, such as `http://127.0.0.1:5173`.
- Require the explicit `--enable-reward-write-api` server flag and reject
  non-loopback browser origins.
- Return `403` when the write API is disabled or the browser origin is not
  loopback, and `409` for stale preview.
- Keep `GET /status.json` and `POST /reward/dry-run` usable without enabling
  append writes.

## UI Rules

The dashboard should not make reward writes feel like ordinary form submission:

- Default state is `Dry-run Check`.
- A write control appears only after a successful preview and explicit server
  capability.
- The control label should name the consequence, for example
  `Append reward overlay`.
- The confirmation copy should say that the action appends one compact
  `human_reward` row to the selected run index.
- After append, the dashboard should refresh status and show the new compact
  reward signal from `loopx status`.

## Validation Before Implementation

An implementation PR should prove:

- Starting `serve-status` without the write flag rejects append requests and
  leaves the index unchanged.
- Starting with the write flag on a non-loopback host fails.
- Append with a stale preview fails and leaves the index unchanged.
- Append with a changed payload fails and leaves the index unchanged.
- Append with public-safe text writes exactly one JSONL overlay row.
- The status export shows `human_reward` after append and still hides local
  paths.
- The dashboard can complete dry-run -> append -> refresh in a browser smoke
  test.

The CLI remains the canonical reward writer. The dashboard append path is a
local operator convenience over the same run-bound `human_reward` overlay.
