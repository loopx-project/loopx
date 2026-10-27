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
  Persistent sessions forward a stable, path-digested thread id. An existing
  binding can resume its registered lane; an unbound session retains the native
  identity-selection packet. A sole candidate is never automatic takeover
  authorization. Sessions without a file retain explicit identity selection.
  Inspection (`/loopx`, `/loopx status`, `/loopx history`, `/loopx list`) never
  creates or changes session authority. Only explicit guided startup issues the
  activation token and locks its Goal, Agent, registry and capabilities, so
  inspecting a project first cannot constrain a later startup.
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
- **Input boundary** — observed Pi `input` events with source `interactive` or
  `rpc` pause automatic continuation, including queued `steer` / `followUp`,
  image-only input and text identical to the last injected task. Pi still
  processes the input and can compact/recover within that run; LoopX stops
  submitting subsequent tasks. Already-submitted messages and running tools
  are not cancelled. Input arriving during a pending resume or activation
  revokes dispatch immediately, then waits for already-started re-arm writes
  before saving the pause, so restarting Pi cannot restore that older re-arm.
  Use `/loopx resume` to re-arm with fresh quota admission.
- **Pause reasons** — a saved input pause records `pauseReason: "user_prompt"`,
  Escape records `"aborted"`, and explicit resume clears the reason. Input and
  run-start events for the same pause notify once and preserve an existing pause
  reason. The reason is diagnostic; durable `autoResume` and LoopX quota still
  authorize continuation. While saving an input pause, a session-local fence
  also denies dispatch. If saving fails, that instance stays stopped and reports
  that the pause may not survive restart; restore storage before explicit resume.
- **Origin limits** — `extension` does not identify which extension sent input.
  Unknown-source user starts retain the injected-text fallback; custom starts
  pause because LoopX only injects user messages. The fallback compares the
  injected text before waiting for a pending re-arm and persists a non-own
  start's pause after older writes; a later explicit resume still takes priority.
  Earlier Pi input handlers can consume an event before this extension sees it.
  This is not complete prompt
  provenance: compaction never exempts a prompt or re-arms a pause, and its
  occurrence does not establish the next prompt's producer.

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

## Stable identity across launches

By default, `piThreadId()` derives the LoopX host thread id from the Pi session
file path. Pi creates a new session file for each launch
(`<ISO timestamp>_<uuid>.jsonl`), so the derived thread id changes every time.
This means an already-bound agent lane requires re-binding on every fresh Pi
session.

Two supported options keep the thread id stable:

**Option 1 — environment variable (recommended).**
Set `LOOPX_PI_THREAD_ID` to any stable, project-scoped identifier before
starting Pi. The adapter uses that value verbatim and ignores the session file
path:

```bash
export LOOPX_PI_THREAD_ID="my-project-loopx"
pi
```

Pick a value unique to the project, such as a slug of the project directory
name. Once a lane is bound under this id, every subsequent Pi session in the
same project resolves that lane without a re-bind.

**Option 2 — fixed session file path.**
Start Pi with `pi --session <fixed-path>`. The session file path stays constant
across restarts, so the path-digested thread id is also constant:

```bash
pi --session ~/.pi/agent/sessions/my-project/loopx-session.jsonl
```

Without either option, a first-time user must bind the lane once per Pi
session. The identity-selection packet returned by `/loopx <goal text>` still
works; the binding just does not survive a fresh launch.

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

## Pause and resume boundaries

An explicit host abort or Core `stop_until_explicit_resume` disarms automatic
continuation and cancels its timer. Idle events and ordinary prompts do not
re-arm it. Resolve the Core Goal/quota stop, then use `/loopx resume`; the next
idle evaluation must obtain fresh admission. Pause/resume advances the existing
binding generation, so a late pre-pause result cannot send a task afterward.
Use `/loopx status` and `loopx quota should-run --goal-id <goal_id> --agent-id
<agent_id>` to distinguish host binding from Core permission. Validated
no-follow-up stops; dependency waits retain bounded checks without model calls.
Already-started tools and original settlement are not cancelled by this gate.
