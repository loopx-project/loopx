# Codex CLI TUI-First LoopX Loop

Status: product contract and implementation target.

LoopX should make Codex CLI easy to adopt without taking away the
interactive TUI that users already trust. The target is not "run a hidden
daemon instead of Codex." The target is:

1. A user opens Codex CLI TUI inside a project repo.
2. The user sends one short message.
3. Codex discovers or installs LoopX without requiring a manual repo
   clone, connects the repo conservatively, reads gate/todo state, and reports
   the next safe action.
4. The setup turn installs the generated LoopX goal/heartbeat loader immediately, then
   stops before longer delivery work.
5. Later automation can steer the same visible session when safe, while the
   user can still watch, interrupt, review, or take over.

## Product Goal

The best first-run experience is one TUI setup message:

```text
Connect this repo to LoopX from this visible Codex CLI TUI. Do not clone the
LoopX repository for ordinary use. If `loopx` is not on PATH, install or repair
it from PyPI with Python 3.11+:
python3 -m pip install --upgrade loopx
loopx workflow-skills --install

Then run `loopx doctor`. Work only from this project root: if LoopX state
already exists, reuse it and do not create or overwrite a goal; if the project
is not connected, prefer `loopx connect`, and use `loopx bootstrap` only when
goal state clearly needs initialization. Ensure `.loopx/`, `.loopx/goals/`,
and `.local/` are ignored. Keep me in this TUI, do not use hidden headless
execution. After the project is connected, generate the host-specific bootstrap loader with `heartbeat-prompt --bootstrap --thin --runtime-profile codex_cli`
and set the current Codex CLI goal to `/goal <loader task_body>`. Then stop and
report the goal id, current user gate, top agent todo, and next safe action.
```

That text should be a Codex CLI-native setup path for the same lifecycle App
uses: setup first, including immediate installation of the generated loop loader into
the surface. In Codex CLI the loop is `/goal <loader task_body>`; in Codex App
the loop is heartbeat automation every 3 minutes with `<loader task_body>`. The
message should be enough for a terminal agent to:

- run `loopx doctor`;
- install or repair the local CLI if it is missing, using PyPI and the packaged
  workflow-skill installer before asking the user to clone the LoopX repo;
- reuse existing LoopX state without creating or overwriting a goal;
- connect the repo when needed, using bootstrap only for clear initialization;
- ensure `.loopx/`, `.loopx/goals/`, and `.local/` stay local;
- generate `heartbeat-prompt --bootstrap --thin --runtime-profile codex_cli`;
- set the current Codex CLI goal to `/goal <loader task_body>`;
- report the goal id, user gate, top agent todo, and next safe action;
- write back setup status without spending delivery quota unless delivery was
  explicitly requested and validated.

The user should not need to understand registry paths, runtime roots, active
state files, quota JSON, or heartbeat prompts before seeing value.

The CLI loader binds `codex_cli`; App automation uses its own
`heartbeat-prompt --bootstrap --thin --codex-app` loader. Each loads current
instructions on entry. Never reuse the CLI guard for an App wake, freeze a Turn
identity, or save today's execution body as the permanent prompt. Apply a new
loader through the authorized host update and read it back; source changes alone
do not migrate existing automations. Existing authority and cadence floors remain.

## Runtime Split

| Layer | Owns | Must Not Do |
| --- | --- | --- |
| Codex CLI TUI | visible user interaction, local tool execution, steering, review, manual takeover | hide user decisions inside LoopX state |
| LoopX | goal state, user gates, agent todos, claims, quota, writeback, compact evidence | replace the Codex CLI runtime or store raw transcripts |
| Local driver or scheduler | wakeups, idle checks, session attachment attempts, fallback launch | inject into an active user turn or bypass a gate |

LoopX should be the control plane. Codex CLI should remain the executor
and the user's live console.

When the Codex sandbox blocks loopback TCP on macOS or Linux, set
`LOOPX_EFFECT_RUNTIME_TRANSPORT=stdio` in the shell that starts Codex.
LoopX commands and their settlement subprocesses inherit the setting. Run
`loopx doctor --deep` from that environment to verify the Node/SQLite runtime.
The CLI uses the configured authority store and needs write access to the
project state and its normal temporary directory for large snapshots.
Launch Codex with the setting unset to use the default loopback bridge.

## Operating Modes

### 1. TUI Bootstrap

This is the first supported path. The user starts in Codex CLI TUI and pastes a
single LoopX setup request. The agent performs install/connect,
generates the current host bootstrap loader, sets the current Codex CLI goal to
`/goal <loader task_body>`, reports the current gate/todo/next-action snapshot,
and stops. It should not stop after describing the product, and it should not
spend delivery quota for setup-only work.

This mode preserves the TUI completely because the human explicitly starts the
loop there.

Current prototype:

```bash
loopx codex-cli-bootstrap-message --project . --goal-id <goal-id>
```

Copy the generated setup message into Codex CLI TUI. It tells the agent to
repair/install LoopX if needed, connect the repo conservatively,
generate the thin heartbeat body, set Codex CLI goal mode to `/goal <thin
task_body>`, run the quota/status guard for the first snapshot, obey
`interaction_contract`, preserve the visible TUI, and stop before longer work.

The first useful TUI response should be a control-plane snapshot, not a lecture
about internals:

- current goal id;
- concrete user gate, or "none";
- top user todo, or "none";
- top agent todo;
- next safe action.

Registry paths, runtime roots, JSON payloads, local-driver plans,
and clone/canary setup are follow-up diagnostics. They should not be required
before a first-time user sees the current goal/gate/todo state.

### 2. Session-Attached Automation

This is the preferred automation target. A scheduler wakes up, runs
`quota should-run`, then attempts to add a visible LoopX steering turn to
the same Codex CLI session.

A valid attachment needs:

- a stable session identifier or resume handle;
- an idle guard so automation does not race a human-typed message;
- a visible injected prompt that says why LoopX is steering now;
- a hard stop when `interaction_contract.user_channel.action_required=true`;
- writeback and spend only after the session produces validated evidence.

If Codex CLI cannot expose a safe session attachment primitive, LoopX
should not fake it by writing hidden state. It should fall back to a transparent
mode. Explicit non-TUI isolated headless execution is handled by `loopx turn`
(`turn_driver/codex_cli.py`), see [LoopX Turn quickstart](loopx-turn-codex-cli-quickstart.md)
and [Automation driver audit](codex-cli-automation-driver.md).

### 3. Headless Disabled Boundary

`codex exec` remains useful for scheduled or CI-like work via `loopx turn`, but
it is not the primary product experience for interactive users. The default
Codex CLI LoopX setup-then-`/goal` path does not expose a headless fallback,
even as an opt-in, so a first-run packet cannot accidentally move work into
hidden execution. The older pre-Turn pilot and probe subcommands have been
retired in favor of `loopx codex-cli-bootstrap-message`, `loopx start-goal`,
and `loopx turn`.

## Session-Attached Turn Algorithm

```text
1. Resolve repo, goal_id, registered agent_id, and current Codex session.
2. Run `loopx quota should-run --goal-id <goal> --agent-id <agent>`.
3. If user action is required, inject or display only the concrete user gate.
4. If `workspace_guard` blocks a repository-writing task, move the current peer
   to a compliant worktree before editing.
5. Choose among current-agent claimed advancement todos and runnable unclaimed
   candidates; monitor todos are context unless they produce a material event.
6. Inject a visible steering prompt into the idle TUI session when proven, or
   keep the one-message setup bootstrap as the user-facing path.
7. After validation, run `refresh-state` and `quota spend-slot --execute`.
8. If validation fails, write a compact blocker instead of spending success
   prose.
```

The actual todo choice remains the agent's steering decision. LoopX
projects runnable candidates; it should not over-specify the model's local plan.

## Safety Rules

- Do not store raw Codex transcripts, credentials, private local paths, raw
  logs, or production artifacts in LoopX state.
- Do not inject automation into a session while the user is actively typing or
  while a previous turn is still running.
- Do not answer a user gate on the user's behalf.
- Do not let a repository-writing peer edit from a workspace rejected by
  `workspace_guard`.
- Prefer a visible TUI prompt over silent background mutation.
- Treat session-attachment failure as a disabled-boundary decision, not as a
  reason to lose the LoopX loop.

## Implementation Roadmap

1. **Bootstrap prompt**: ship a concise Codex CLI TUI paste message in README
   and getting-started docs.
2. **No-clone install repair**: make the first-run agent path able to install
   the CLI and reusable skills from PyPI or a GitHub archive, while reserving
   clone-plus-canary setup for contributors.
3. **Bootstrap command**: `loopx codex-cli-bootstrap-message` prints a tailored
   Codex CLI bootstrap message for the current repo.
4. **LoopX Turn driver**: `loopx turn plan` and `loopx turn run-once`
   (`turn_driver/codex_cli.py`) replace the older pre-Turn pilot and probe
   commands for bounded isolated-headless execution, validation, durable
   writeback, and quota spend.
5. **Same-open-TUI attach**: `interactive-visible` remains gated until Codex CLI
   exposes a verified safe attach primitive with idle and interruption guarantees.
6. **Claude Code follow-up**: port the same product contract across hosts via the
   shared `start-goal` and `agent-onboard` surfaces.

## Success Criteria

- A first-time user can start in Codex CLI TUI with one message and see a
  current goal, user gate, agent todo, and next safe action without reading
  LoopX docs first.
- A returning user can keep the TUI open while LoopX automation performs
  bounded turns that are visible, interruptible, and reviewable.
- When session attachment is unavailable, the fallback is explicit and safe
  rather than pretending the same TUI session was preserved.
- LoopX state remains compact, public/private-safe, and independent of
  raw Codex CLI transcript storage.
