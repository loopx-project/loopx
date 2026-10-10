# LoopX Pi goal mode

The Pi host adapter for LoopX. Pi is a terminal coding agent whose extensions
register commands, tools, and event handlers; this adapter turns a Pi session
into a LoopX-governed visible goal loop.

## Surface

- **`/loopx`** — with no arguments, runs `loopx bootstrap-command-pack --project .`
  and shows the packet as a widget plus a transcript entry. With a goal text
  argument, runs `loopx start-goal --guided --project . --goal-text "<text>"
  --host-surface pi` and delivers the returned packet directly to the agent as
  a follow-up user message (`sendUserMessage` with `triggerTurn`), so the
  guided transaction starts without a popup box or a manual Enter step.
  `/loopx resume` re-arms auto-continuation after a user-driven pause or an
  aborted run.
- **`loopx_goal_activate`** — agent-callable tool. Binds the current session to
  the host-verified Pi startup/session packet using its one-time
  `activationToken`, plus the heartbeat `objective`/task_body, then starts the
  quota-gated loop. Goal, agent, registry, and mutation capabilities are
  derived from that packet; compatibility echoes are rejected when they do not
  match the locked authority.
- **`loopx_task_lease`** — agent-callable, explicit facade over the existing
  `task_lease_v0` CLI. It supports `acquire`, `renew`, `transfer`, `release`,
  and read-only `inspect`. The active Pi binding supplies `goalId` and the
  current owner; the model cannot substitute either authority. Mutation calls
  require a non-empty host-verified bound `agentId` and the host-issued
  `task_lease_v0` capability. Typed
  conflict and CAS payloads (for example `write_scope_conflict` and
  `lease_cas_mismatch`) are preserved as the tool result.
- **Goal loop** — on every `agent_settled`, the extension probes
  `loopx quota should-run --runtime-profile generic_cli` for the bound goal.
  LoopX decides whether to continue (the heartbeat task_body is injected as a
  follow-up), wait with scheduler-hint backoff (unchanged-poll limits apply), or
  stop at a validated terminal no-follow-up. Probe failures fail closed with a
  bounded retry; the extension never self-declares closure.
- **Abort boundary** — pressing Escape while Pi is running persists
  `autoResume: false` before `agent_settled`, cancels any pending backoff timer,
  and fences an in-flight quota probe. For persistent sessions, the loop stays
  paused across Pi restarts until `/loopx resume` or a fresh goal activation
  explicitly re-arms it.
- **Host-generated prompts** — Pi continues a session on its own after context
  compaction, overflow recovery, or a retry, and those runs reach
  `before_agent_start` with a prompt that is not the injected task body. The
  adapter marks that prompt as host-generated so the runtime does not read it as
  the owner taking the wheel; only real user input pauses the loop, and the pause
  is reported to the owner (`pauseReason: "user_prompt"`, or `"aborted"` after
  Escape). `/loopx resume` clears the reason and re-arms the loop.

## Install / uninstall

```bash
loopx slash-commands --install --surface pi --pi-project .
loopx slash-commands --uninstall --surface pi --pi-project .
loopx slash-commands --install --surface pi --pi-scope user
loopx slash-commands --inspect --surface pi --pi-project .
loopx slash-commands --uninstall --surface pi --pi-scope user
```

Installs two LoopX-managed files into the project (loaded after project
trust):

- `.pi/extensions/loopx-goal.ts` — the extension adapter that registers
  `/loopx`, `loopx_goal_activate`, `loopx_task_lease`, and the `agent_settled`
  loop wiring.
- `.pi/extensions/pi-goal-loop-runtime.mjs` — the quota/wait/store loop core
  (not auto-discovered as an extension; the adapter imports it directly).

Pi's extension loader aliases `typebox` and the `@earendil-works/*` packages,
so no local `node_modules` are required. The `--pi-project` flag points the
installer at the target project so the command is correct even when run from
another directory; `agent-onboard --agent-type pi --project <path>` emits the
resolved project automatically.

The optional user scope installs the same adapter as
`<agent-dir>/extensions/loopx/index.ts` with the runtime beside it. Pi
discovers `index.ts` inside extension subdirectories. `<agent-dir>` defaults to
`~/.pi/agent` and follows `PI_CODING_AGENT_DIR`. Inspect reports both scopes as
absent, current, stale, partial, or user-owned, and warns when both entries
would load in the same project. Run Pi `/reload` or restart after install,
upgrade, or uninstall. Only the adapter is global: bindings remain under the
active project's `.loopx/pi/`.

## State

Bindings persist under `<project>/.loopx/pi/` (gitignored), keyed by session.
Override with `LOOPX_PI_STATE_DIR`. Invoke the CLI binary via `LOOPX_BIN`.

Sessions without a session file (`pi --no-session`) are ephemeral: the
adapter uses a unique in-memory identity per extension instance and never
persists its binding, so a later `--no-session` run cannot inherit the
previous run's goal and must activate again through `loopx_goal_activate`.

## Explicit task leases

Lease support is explicit at the tool call, while its mutation capability is
host-bound. Start the Pi flow with `/loopx <goal text>` and use the
`pi_session_authority.token` from that startup packet when activating:

```text
loopx_goal_activate({
  activationToken: "<pi_session_authority.token>",
  objective: "<heartbeat_prompt.task_body>"
})
```

The authority is locked to this Pi session. A later activation that changes the
goal, agent, registry, or capability set returns a typed authority failure and
does not reach the lease CLI. Re-run `/loopx <goal text>` in a new host session
when a different authority is required.

After activation, call the lease tool with only the lifecycle fields:

```text
loopx_task_lease({
  action: "acquire",
  todoId: "todo_ab12cd34ef56",
  idempotencyKey: "pi-turn-42",
  writeScopes: ["loopx/**"],
  ttlSeconds: 2700
})
```

`renew`, `transfer`, and `release` require the lease's `expectedVersion`;
`transfer` additionally takes `newOwner` and `newIdempotencyKey`. `inspect`
is read-only and only needs the active goal binding. Pi does not automatically
acquire, renew, transfer, or release leases. The equivalent CLI fallback is:

```bash
loopx --registry <registry> --format json task-lease acquire \
  --goal-id <goal> --todo-id <todo> --owner <agent> \
  --idempotency-key <turn-key> --write-scope 'loopx/**'
```

The same fallback uses `renew`, `transfer`, `release`, or `inspect` in place
of `acquire`; `renew`/`transfer`/`release` pass `--expected-version`, and
`transfer` also passes `--new-owner` and `--new-idempotency-key`:

```bash
loopx --registry <registry> --format json task-lease renew \
  --goal-id <goal> --todo-id <todo> --owner <agent> \
  --idempotency-key <turn-key> --expected-version <version>
loopx --registry <registry> --format json task-lease inspect \
  --goal-id <goal> --todo-id <todo>
```

The tool exposes public-safe typed receipts and conflicts only. It does not
grant authority outside the active LoopX goal binding, bypass registration,
change quota/scheduler/todo defaults, or copy Pi transcripts, credentials, or
session paths into LoopX state.

## Boundary

The extension reads only LoopX public-safe state and never copies raw
transcripts, credentials, or local session paths. Continuation is governed by
LoopX quota; user prompts and aborted runs pause auto-resume; `/loopx resume`
or re-activation re-arms it. No external writes happen without the active
LoopX state or owner authorization.

On `session_shutdown` (session switch, fork, or reload) the extension instance
is atomically disposed: every timer is cancelled and an in-flight quota probe
that returns afterwards stops at the disposed guard, so the old session can
never inject a follow-up or reschedule past the reload / session-replacement
boundary.
