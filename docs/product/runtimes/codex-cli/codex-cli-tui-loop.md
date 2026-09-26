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
4. The setup turn installs the thin LoopX goal/heartbeat body immediately, then
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
execution. After the project is connected, generate the thin heartbeat prompt
and set the current Codex CLI goal to `/goal <thin task_body>`. Then stop and
report the goal id, current user gate, top agent todo, and next safe action.
```

That text should be a Codex CLI-native setup path for the same lifecycle App
uses: setup first, including immediate installation of the thin loop prompt into
the surface. In Codex CLI the loop is `/goal <thin task_body>`; in Codex App
the loop is heartbeat automation every 3 minutes with `<thin task_body>`. The
message should be enough for a terminal agent to:

- run `loopx doctor`;
- install or repair the local CLI if it is missing, using PyPI and the packaged
  workflow-skill installer before asking the user to clone the LoopX repo;
- reuse existing LoopX state without creating or overwriting a goal;
- connect the repo when needed, using bootstrap only for clear initialization;
- ensure `.loopx/`, `.loopx/goals/`, and `.local/` stay local;
- generate `heartbeat-prompt --thin`;
- set the current Codex CLI goal to `/goal <thin task_body>`;
- report the goal id, user gate, top agent todo, and next safe action;
- write back setup status without spending delivery quota unless delivery was
  explicitly requested and validated.

The user should not need to understand registry paths, runtime roots, active
state files, quota JSON, or heartbeat prompts before seeing value.

## Runtime Split

| Layer | Owns | Must Not Do |
| --- | --- | --- |
| Codex CLI TUI | visible user interaction, local tool execution, steering, review, manual takeover | hide user decisions inside LoopX state |
| LoopX | goal state, user gates, agent todos, claims, quota, writeback, compact evidence | replace the Codex CLI runtime or store raw transcripts |
| Local driver or scheduler | wakeups, idle checks, session attachment attempts, fallback launch | inject into an active user turn or bypass a gate |

LoopX should be the control plane. Codex CLI should remain the executor
and the user's live console.

## Operating Modes

### 1. TUI Bootstrap

This is the first supported path. The user starts in Codex CLI TUI and pastes a
single LoopX setup request. The agent performs install/connect,
generates the thin heartbeat prompt, sets the current Codex CLI goal to
`/goal <thin task_body>`, reports the current gate/todo/next-action snapshot,
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

Transcript-free first-run smoke packet:

```bash
loopx codex-cli-tui-bootstrap-smoke-bundle --project . --goal-id <goal-id> --agent-id <agent-id>
```

This packet is for product and release validation, not an extra user step. It
checks the PyPI-first install repair path, the copy-only paste block, the quota
guard command, and the bounded writeback/spend commands without launching
Codex, reading transcripts, inspecting session files, mutating a session, or
spending quota.

The first useful TUI response should be a control-plane snapshot, not a lecture
about internals:

- current goal id;
- concrete user gate, or "none";
- top user todo, or "none";
- top agent todo;
- next safe action.

Registry paths, runtime roots, JSON payloads, local-driver plans,
clone/canary setup, and visible-session proof fixtures are follow-up
diagnostics. They should not be required before a first-time user sees the
current goal/gate/todo state.

Current pilot packet:

```bash
loopx codex-cli-one-message-loop-pilot --project . --goal-id <goal-id> --agent-id <agent-id>
```

This command does not run Codex. It packages the first TUI paste message and
the safe scheduler/executor bridge into one reviewable packet:

- first turn: paste one LoopX message into the visible Codex CLI TUI;
- first response: show goal id, concrete user gate or none, top user todo or
  none, top agent todo, and next safe action;
- first work segment: if the guard permits work, claim or choose one runnable
  agent todo and complete one bounded validated segment in that same visible
  TUI turn;
- later scheduler: use `codex-cli-local-scheduler-exec` in dry-run mode by
  default;
- bridge side effects: require a fresh guard plus explicit candidate prefix or
  blocker-writeback opt-in.

The pilot is a contract check for the user experience. It is not a prerequisite
for a first-time user; the first-time path remains "paste one message and watch
the TUI."

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
mode.

Current probe:

```bash
loopx codex-cli-session-probe
```

The probe is help-only by default: it checks public Codex CLI command surfaces
such as `codex --help`, `codex exec --help`, and `codex resume --help`. It does
not read raw transcripts, credentials, local session files, or mutate a Codex
session. The key distinction is deliberate: `exec` or `resume` support can be a
useful fallback, but it is not evidence that LoopX can inject a visible
turn into the same open TUI. Same-session automation requires an explicit
visible attach/inject primitive plus an idle guard. A visible `resume [PROMPT]`
or experimental `remote-control` surface is stronger than plain headless
fallback, but it still belongs in a separate spike until LoopX proves the
turn is visible, idle-guarded, interruptible, and not racing a human-typed TUI
message.

Current driver-plan prototype:

```bash
loopx codex-cli-visible-driver-plan --project . --goal-id <goal-id>
```

This command turns the probe result into a dry-run driver plan. It does not run
Codex, read raw transcripts, read session files, mutate a Codex session, or
spend LoopX quota. Its job is to choose one of three next modes:

- `session_attached_visible_turn`: a future local driver may try the detected
  visible attach primitive, but only behind quota guard and idle guard.
- `visible_resume_or_remote_control_spike`: `resume [PROMPT]` or
  `remote-control` exists, but it must prove that the turn is visible and
  interruptible before LoopX treats it as session-attached automation.
- `tui_bootstrap_only`: ask the user to start inside Codex CLI TUI. If the
  probe only exposes `codex exec`, LoopX still stays in this mode
  because headless fallback is disabled for the default `/goal` product path.

Current local-driver planner:

```bash
loopx codex-cli-local-driver-plan --project . --goal-id <goal-id> --agent-id <agent-id>
```

This command is the conservative MVP for automation setup. It composes the
quota guard, visible-driver plan, TUI bootstrap command, headless-disabled
boundary, and idle-guard requirement into a single dry-run packet. It
does not run Codex, read transcripts, read session files, mutate a session, or
spend quota.

Current local-scheduler execution wrapper:

```bash
loopx codex-cli-local-scheduler-exec --project . --goal-id <goal-id> --agent-id <agent-id>
```

Without explicit execution flags, this command is still a no-execution packet.
For a later visible Codex CLI turn, it must also receive public-safe runtime
idle evidence through `--observe-local-runtime ...` or `--idle-fixture
<public-runtime-idle.json>`. A visible-session proof says the route is visible
and interruptible; the runtime-idle detector says this exact later turn is not
racing human typing or an already-running Codex turn. Missing runtime-idle
evidence produces a precise blocker instead of a candidate command.

With runtime-idle evidence and `--guard-checked`, a local scheduler may choose
exactly one opt-in side effect:

- `--execute-candidate --candidate-command-prefix <prefix>`: run a proven
  visible candidate whose command starts with an allowed prefix.
- `--execute-blocker-writeback`: run the precise LoopX blocker writeback
  command when the tick says proof is missing.

The wrapper reports only whether it ran, return code, timeout, and the selected
kind. It discards stdout/stderr, does not read transcripts, does not inspect
session files, does not mutate hidden Codex state, and does not spend Goal
Harness quota. This keeps the first executable bridge narrow enough to test
without turning the user's TUI into an opaque background daemon.

Current visible local-driver pilot:

```bash
loopx codex-cli-visible-local-driver-pilot --project . --goal-id <goal-id> --agent-id <agent-id>
```

This command still does not run Codex. It binds the first one-message TUI start
to later scheduler ticks and makes the returning-user contract explicit:

- later turns must remain visible to the user;
- the user must be able to interrupt or take over;
- a public-safe visible proof is required before resume, remote-control, or
  same-TUI prompt candidates can run;
- every later tick needs quota guard and idle guard;
- candidate execution still requires `--guard-checked` plus an allowed command
  prefix;
- blocker writeback still requires `--guard-checked`;
- the pilot never reads transcripts, session files, credentials, stdout, or
  stderr, and never spends quota by itself.

Current visible-session proof harness:

```bash
loopx codex-cli-visible-session-proof \
  --project . \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --proof-fixture visible-proof.public.json
```

The proof fixture must be public-safe. It records booleans for user opt-in,
quota guard, idle guard, turn visibility, interruptibility, private-data
boundaries, and compact writeback planning. Passing this proof only means a
future local driver may try that visible surface behind the same guards; it
does not mean LoopX may read transcripts, read session files, mutate
hidden session state, or bypass user gates.

Current runtime-idle detector:

```bash
loopx codex-cli-runtime-idle-detector \
  --project . \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --observe-local-runtime \
  --observed-surface visible_resume_prompt \
  --turn-state idle \
  --probe-human-input-idle \
  --checked-before-prompt \
  --visible-to-user \
  --user-can-interrupt \
  --manual-takeover-available
```

This detector accepts either a public-safe fixture or a narrow local
observation adapter. The local adapter may probe a coarse platform idle counter
for "no recent human input" and requires an explicit visible `--turn-state
idle`; unknown or running turn state fails closed. It is deliberately separate
from the visible-session proof: the proof says "this route can create a
visible, interruptible turn"; the idle detector says "this exact later turn is
not racing human typing or an already-running Codex turn." It must prove no
active human typing, no running turn, and no
transcript/session/stdout/stderr/credential reads before LoopX treats a
later visible prompt as executable.

For reproducible tests or external sensors, the fixture path remains:

```bash
loopx codex-cli-runtime-idle-detector \
  --project . \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --idle-fixture runtime-idle.public.json
```

Current same-TUI acceptance packet:

```bash
loopx codex-cli-visible-attach-acceptance \
  --project . \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --proof-fixture visible-proof.public.json \
  --idle-fixture runtime-idle.public.json
```

This packet is the promotion gate before LoopX treats later Codex CLI
automation as safe same-TUI attach. It composes the help-only probe,
visible-session proof, and runtime-idle detector. `remote-control` or `resume
[PROMPT]` can pass as a visible spike candidate, but they are not accepted as
same-TUI automation unless the proof surface is `same_tui_visible_attach` and
the idle detector passes. If either proof or idle evidence is missing, the
packet returns a precise blocker and keeps the one-message setup bootstrap as the
primary path.

The first public-safe proof pilot is recorded in
[Codex CLI Visible Attach Proof Pilot](codex-cli-visible-attach-proof-pilot.md):
current `resume` / `remote-control` evidence is promising, but still blocked
until a visible same-TUI proof and runtime-idle evidence exist.

The repeatable capture path is defined in
[Codex CLI Visible Proof Capture Protocol](codex-cli-visible-proof-capture-protocol.md).
It treats `resume` / `remote-control` as proof targets, keeps fixtures
public-safe, and records blocker-first stop conditions before any later visible
turn is promoted.

### 3. Headless Disabled Boundary

`codex exec` remains useful for scheduled or CI-like work, but it is not the
primary product experience for interactive users. The default Codex CLI
LoopX setup-then-`/goal` path does not expose a headless fallback, even
as an opt-in, so a first-run packet cannot accidentally move work into hidden
execution.

Compatibility boundary:

```bash
loopx codex-cli-exec-handoff --project . --goal-id <goal-id>
```

This command no longer prints a runnable `codex exec` handoff script. It
reports the disabled boundary and points back to
`codex-cli-bootstrap-message --message-only` for use inside the visible TUI.
It does not run Codex, read transcripts, read credentials, read session files,
mutate a session, or spend quota.

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
   the CLI and reusable skills from a GitHub archive, while reserving
   clone-plus-canary setup for contributors.
3. **Bootstrap command**: add a LoopX command that prints a tailored
   Codex CLI bootstrap message for the current repo.
4. **One-message loop pilot**: ship
   `loopx codex-cli-one-message-loop-pilot` to bind the first TUI paste
   message and the later scheduler/executor bridge into one public-safe packet.
5. **Session probe**: document whether current Codex CLI exposes a stable
   session id, resume handle, or safe injection primitive. The current
   implementation is `loopx codex-cli-session-probe`; it separates
   headless-disabled execution support, visible resume / remote-control spike
   surfaces, and true same-open-TUI visible injection.
6. **Visible driver plan**: generate a dry-run plan with
   `loopx codex-cli-visible-driver-plan` so the next local driver knows
   whether to attempt visible attach, run a resume/remote-control proof, or
   keep the one-message setup bootstrap as the product path.
7. **Local driver planner**: ship
   `loopx codex-cli-local-driver-plan` as the dry-run command that
   composes quota, visible-driver, TUI bootstrap, headless-disabled boundary,
   and idle-guard requirements.
8. **Visible-session proof harness**: validate public-safe observations with
   `loopx codex-cli-visible-session-proof` before promoting
   resume/remote-control into any same-session automation path.
9. **Visible driver run packet**: add
   `loopx codex-cli-visible-driver-run` as the no-execution packet that
   decides whether the next turn needs visible proof, TUI bootstrap, or a
   proven visible-session candidate.
10. **Local scheduler tick**: add
   `loopx codex-cli-local-scheduler-tick` as the first executor-facing
   one-shot packet. It emits either an external command candidate or a precise
   blocker writeback command, but does not run Codex, read session files, or
   write LoopX state itself. Visible candidates require both
   visible-session proof and runtime-idle detector approval; headless fallback
   remains disabled for the default `/goal` path.
11. **Local scheduler executor wrapper**: add
   `loopx codex-cli-local-scheduler-exec` as the explicit opt-in bridge
   that can run one tick result only after guard confirmation, runtime-idle
   approval for visible candidates, and an allowed command prefix.
12. **Visible local driver pilot**: ship
   `loopx codex-cli-visible-local-driver-pilot` to bind the one-message
   TUI start, scheduler executor, visible proof, idle guard, and no-transcript
   boundary into one public-safe packet.
13. **Runtime idle detector**: validate public-safe idle evidence with
   `loopx codex-cli-runtime-idle-detector` before a visible later turn;
   the command now supports fixture replay and a narrow local observation
   adapter that can prove coarse human-input idle plus explicit visible
   turn-state without reading transcripts, stdout/stderr, credentials, or
   hidden session files.
14. **Visible attach acceptance**: promote only a proven `same_tui_visible_attach`
   route with passing runtime-idle evidence; keep `resume [PROMPT]` and
   `remote-control` as visible spike candidates until they prove same-TUI
   semantics.
15. **Validation harness**: add a public-safe fixture that proves the driver
   never stores raw transcript text and never spends quota before writeback.
16. **Claude Code follow-up**: port the same product contract only after the
   Codex CLI path is credible.

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
