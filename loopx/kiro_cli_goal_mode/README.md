# Kiro CLI goal mode

LoopX adapter for [Kiro CLI](https://kiro.dev/) (binary `kiro-cli`) — a
terminal coding agent with skills, steering files, agent configs, hooks, MCP,
and a native goal loop. Kiro CLI ships both halves of a goal-mode host: a goal
primitive *and* a host-enforced iteration budget, so LoopX binds the objective
to the host's own loop instead of pretending the agent merely drives itself.

## Native goal primitive

`/goal [--max N] <description> | clear` — the built-in command as the host
contracts it. The host re-dispatches turns toward the stated objective, verifies
each iteration against the acceptance criteria it derives from the goal
statement, and the model must prove completion through the built-in `goal` tool
before the loop ends.

- Iteration budget: the host default is `5`, raised with `--max`. The flag
  precedes the description, and everything after it is the goal statement.
- **Acceptance criteria travel inside the description.** The host derives them
  from the goal statement, so LoopX appends the criteria its todo already owns
  (after `Done when:`) instead of passing a separate flag. There is no criteria
  flag to pass: an unrecognised flag after the description would be read as more
  goal text, polluting the objective and silently dropping `--max`.
- `/goal clear` cancels the active goal. A running goal is steered in place
  rather than read back — the host advertises no goal status subcommand.
- The `goal` tool's `complete` command enforces a completion contract: each
  success criterion needs cited tool output, and belief or narrative
  confidence is explicitly not evidence — the same standard LoopX writeback
  wants.

The contracted surface is checked against the host itself, not transcribed. The
installed binary advertises its own command registry over ACP, so the shape can
be replayed on any machine that has the CLI:

```bash
python3 examples/kiro-cli-goal-command-contract-probe.py
```

It asserts that `/goal` exists and that its advertised subcommands are exactly
the ones LoopX projects. An earlier revision of this document claimed
`--validate`, `--agent` and `/goal status` and cited a `strings` probe of the
binary; that probe returns nothing on the shipped app bundle, and neither the
published command reference nor the host's own registry contracts those
arguments, so they are no longer projected.

## Native hook seam

Agent configs (`~/.kiro/agents/<name>.json` or `.kiro/agents/<name>.json`)
carry `hooks` for `agentSpawn`, `userPromptSubmit`, `preToolUse`,
`postToolUse`, and `stop`. Checked on 2.24.1 with a probe agent:

- the hook receives `hook_event_name`, `cwd`, `session_id`, `tool_name` and
  `tool_input` as JSON on stdin; tool names are `read`, `write` (`path`),
  `shell` (`command`) and `@<server>/<tool>` for MCP tools;
- **only exit status `2` blocks** a `preToolUse` call, and its stderr reaches
  the model as `PreToolHook blocked the tool execution: …`; exit `1` lets the
  tool run;
- a hook that outlives the entry's `timeout_ms` is abandoned and **the tool
  runs** — a timeout fails open;
- the CLI 3.0 `.kiro/hooks/*.json` format did not fire on 2.24.1.

The default agent gets no LoopX hook. Enforcement is the opt-in agent below.

## Enforced gate: the `loopx` agent

**Supported host versions: Kiro CLI 2.x** (verified on 2.24.1). The gate relies
on the agent config's embedded `hooks`, which 2.24.1 loads and which Kiro CLI
3.0 documents as moving to `.kiro/hooks/*.json`. On a host that did not load
the embedded hook, the agent would run every tool ungated while looking gated,
so the installer reads `kiro-cli --version` and refuses a major it has not
verified (`blocked_unverified_kiro_cli_version`, no file written). When the
version cannot be read (the CLI is not on `PATH` yet) the file is written, and
this range is the contract.

```bash
loopx slash-commands --install --surface kiro-cli --with-gated-agent
kiro-cli chat --agent loopx
```

`--with-gated-agent` writes `<KIRO_HOME>/agents/loopx.json`. The agent keeps the
default agent's reach (all tools, `includeMcpJson` for the `loopx` MCP server,
the installed skills) and adds one `preToolUse` hook,
`loopx/kiro_cli_goal_mode/pretooluse_hook.py`, that sends every tool call
through the host-neutral rule in `loopx/control_plane/goal_mode_tool_policy.py` — the same
rule Claude Code's opt-in `--harden` hook uses:

- read-only tools (`read`, `glob`, `grep`, web search/fetch, `introspect`,
  `todo`, the host's own `goal` tool, and `@loopx/should_run` /
  `@loopx/list_todos`) are always allowed, so a closed gate stays inspectable
  and a host goal can still be ended;
- every other tool is denied while `quota should-run` is false for the agent
  this session's `KIRO_SESSION_ID` is bound to, and also when the probe cannot
  answer — the gate fails closed;
- with the gate open, `write` is confined to the goal's write scope (relative
  paths resolve against the session's cwd), `shell` is screened by a
  destructive-command denylist, and anything else goes to Kiro's own
  permission flow.

Because a timeout fails open in the host, the probe deadline (20 s) sits inside
the entry's `timeout_ms` (30000); a slow control plane is refused by the hook
rather than waved through by Kiro. A gated call costs about one `quota
should-run` (≈1 s on a local fixture); read-only calls skip the probe.

The session binding resolves to a typed state, and the gate treats the states
differently:

- **bound** — the Goal's `quota should-run` decides as above, and the gate
  records the session as engaged under `<KIRO_HOME>/loopx-gate/armed-sessions/`
  (outside the project, so the record survives the project registry going
  away);
- **pre-binding** — no LoopX registry in the project, or a registry with no
  binding for this session yet, in a session the gate has never engaged: the
  gate stays out of the way, which is what lets `/loopx` run `start-goal`;
- **lost or faulty** — an engaged session whose binding can no longer be
  resolved (registry removed or unreadable, binding deleted, ambiguous, or
  naming a Goal or agent the registry no longer holds), any of those faults in
  a session that was never engaged except the two pre-binding states, an event
  without a session id, or an engagement record that cannot be written: every
  state-changing call is denied. Re-binding the session with
  `bind-agent-thread`, or starting a new Kiro session, restores progress;
  retiring the agent clears the records.

Read-only calls pass in every state. This is a
deterministic policy layer, not a sandbox: `shell` inside an open gate can
still write outside the scope or reach the network, so run untrusted work in a
container or VM. The agent file carries a managed marker in `description`; a
`loopx.json` without it is the user's and is never replaced or removed, and
`loopx slash-commands --uninstall --surface kiro-cli` retires the managed agent
even without the flag, because an agent left pointing at a removed hook would
run every tool ungated.

## What this surface is

Kiro CLI discovers global skills from `<KIRO_HOME>/skills/<name>/SKILL.md`
(`~/.kiro` when `KIRO_HOME` is unset) and workspace skills from
`.kiro/skills/<name>/SKILL.md`; the default agent carries
both as `skill://` resources, and every discovered skill is invocable as a
`/<skill-name>` slash command with `$ARGUMENTS` expansion. LoopX reaches a
Kiro CLI session through the generated `/loopx` skill facade, and the
activation binds the objective with the native
`/goal --max <N> <task_body> Done when: <criteria>`.

Three honest limits, stated in the activation packet:

- **Quota pacing is advisory under the default agent.** LoopX installs no Kiro
  hook into it, so `quota should-run` entry is facade guidance the agent is
  instructed to follow. Only a session started with `kiro-cli chat --agent
  loopx` gets the [enforced gate](#enforced-gate-the-loopx-agent).
- **The loop lives and dies with the session.** The `/goal` loop runs only
  while the CLI session is alive; there is no cross-session daemon, so it
  bounds a live session's segments, not an unattended host loop.
- **A same-named file prompt wins.** Kiro resolves `.kiro/prompts/*.md` and
  `~/.kiro/prompts/*.md` before skills, so a user prompt named `loopx` shadows
  the managed skill. The installer never touches the prompt directories.

## Install

```bash
loopx slash-commands --install --surface kiro-cli
```

Writes the managed LoopX skill facades (`loopx/SKILL.md`,
`loopx-global-*/SKILL.md`, …) into `<KIRO_HOME>/skills/` using Kiro's per-skill
directory layout, and registers the LoopX MCP server as the `loopx` entry in
`<KIRO_HOME>/settings/mcp.json` (see [MCP control plane](#mcp-control-plane)). `KIRO_HOME` is the host's own override for that global root,
so LoopX resolves it and falls back to `~/.kiro` when it is unset; install and
uninstall always target the same resolved root. Managed files carry the
`loopx-managed-slash-command` marker and are refreshed by rerunning the
installer; user-owned files are never overwritten.

## Use

From a Kiro CLI session in a connected project, run `/loopx <complex task>`.
The facade instructs the agent to run:

```bash
loopx start-goal --guided --project . --slash-command-arguments="<task>" --host-surface kiro-cli
```

After todo writeback, bind the generated heartbeat task body with
`/goal --max <N> <task_body> Done when: <criteria>` — stating the criteria the
todo already names inside the goal statement, because the host derives its
acceptance criteria from that statement rather than from a flag, and `N` taken
from the remaining quota slots (host default is 5) — steer a running goal in
place rather than expecting a status readback, start every
turn and native goal iteration with `quota should-run` (advisory guidance;
LoopX does not intercept native host iterations), and settle through the
built-in `goal` tool only after LoopX writeback so the cited evidence matches
what LoopX recorded.

Kiro CLI exports `KIRO_SESSION_ID` for every session; it is the stable value a
LoopX thread binding should key on instead of prose.

## Runtime profile

Kiro CLI has its own typed scheduler runtime profile, `kiro_cli`
(`host_surface=kiro_cli`, `scheduler_owner=agent_cli_loop`,
`execution_mode=interactive`). Generated guards carry
`--runtime-profile kiro_cli`, and a validated Kiro iteration settles as a
`visible-goal` quota spend, the same accounting Codex CLI and Claude Code use
for their in-session loops. Before this profile existed Kiro fell through to
`generic_cli`, which booked the same work as a `heartbeat` spend and skipped
the visible-Goal Turn re-entry check. The heartbeat task body is unchanged: it
still asks the agent to mint `LOOPX_TURN` per iteration, because the Codex
native-goal body and its blocked-state rules do not apply to Kiro.

## MCP control plane

The kiro-cli surface registers `loopx/kiro_cli_goal_mode/mcp_server.py` in
`<KIRO_HOME>/settings/mcp.json`, which Kiro's default agent loads, so the
session that runs `/loopx` also gets the typed `should_run`, `list_todos`,
`claim_task`, `complete_task` and `review_task_vision` tools. It is the shared
`loopx.goal_mode_mcp` server Claude Code and KunlunCode use, run under the
`kiro_cli` profile.

- **Identity is the session binding.** Kiro starts each MCP server as a child
  of the session with that session's `KIRO_SESSION_ID` (verified on 2.24.1: the
  child sees the id ACP `session/new` returns). `start-goal --host-surface
  kiro-cli` binds that id to one registered agent through `bind-agent-thread`.
  The server acts only for that binding; with no id, no binding, a binding
  under another host surface, an unregistered agent, or one id bound to two
  lanes, every tool returns the setup hint instead of acting.
- **Ownership stays with the user.** Only the `loopx` key is written. A
  same-named entry LoopX did not write is reported as
  `skipped_user_owned_mcp_entry` and never replaced or removed; a malformed
  file is reported as `blocked_invalid_kiro_cli_mcp_json`. Provenance lives in
  the sidecar `<KIRO_HOME>/settings/.loopx-managed-mcp.json`.
- **Not an enforcement hook.** The tools make the control plane typed; they do
  not stop Kiro from running other tools. Enforcement is the opt-in
  [`loopx` agent](#enforced-gate-the-loopx-agent).

Check it with `kiro-cli mcp list` (the `loopx` server appears under the
default agent), and remove it with
`loopx slash-commands --uninstall --surface kiro-cli`, which retires the skills
and the `loopx` entry only while it still matches what LoopX wrote.

## Dashboard and Chat agent

Kiro CLI also ships an ACP agent (`kiro-cli acp`), so `loopx dashboard` and
`loopx chat` list **Kiro CLI** as a built-in Agent alongside Codex and Claude
Code. It reuses `loopx.chat_acp.ACPStdioAdapter` rather than a second
transport: probed on 2.21.1, the host answers `initialize` with
`protocolVersion: 1` and `loadSession: true`, and `session/new` returns a
session id, which is exactly what that adapter expects.

```bash
loopx dashboard                                  # Kiro CLI appears when it is on PATH
loopx dashboard --kiro-cli-bin /path/to/kiro-cli # explicit executable
```

Two boundaries this does **not** cross:

- **Owner-managed host permissions.** LoopX Chat answers every interactive ACP
  `session/request_permission` with `cancelled`, exposes no client host tools,
  and launches without `--trust-all-tools`. Kiro can still execute a tool
  without asking when its user, workspace, or agent permission rules already
  say `allow`; LoopX cannot turn those persistent host rules into a read-only
  sandbox. The capability therefore advertises `workspace_write`, and the
  owner must configure Kiro permissions for the desired boundary. A planning
  prompt or a cancelled request is not an authority gate.
- **Not the governed loop.** A dashboard Chat session is one bounded
  conversation. The `/goal` loop above is entered from a Kiro CLI session
  through the installed skill facade; the two surfaces share the host, not the
  loop.

The built-in id `kiro-cli` is reserved in the owner-local endpoint registry so
a hand-registered endpoint cannot silently shadow it.

## Control-plane identity

Serving an Agent row is not the same as being reachable. Three surfaces resolve
a host by identity, and each one needs Kiro CLI in its table:

- **Endpoint to Goal agent.** A durable Goal agent id is operator-chosen, so
  `chat_actions` collapses both onto a host family: Endpoint `kiro-cli`
  resolves a registered `kiro-worker-1`, the same way `codex` resolves
  `codex-main-control`. Without the row, selecting Kiro CLI in the workspace
  raised `agent_binding_required` for an agent the user did register.
- **Host thread binding.** `KIRO_SESSION_ID` is read for the `kiro-cli` host
  surface, so `start-goal` binds the live session instead of leaving the thread
  unbound.
- **Project skills.** `loopx project-skill --surface kiro-cli` delivers into
  `.kiro/skills`, the workspace skills root Kiro discovers per project.

## Layout

- `__init__.py` — host facts: install surface id, fixed skills root resolution,
  the agent-type catalog entry, the activation extras (native goal command,
  host iteration budget, completion tool, advisory quota boundary), the MCP
  config location, and the Chat/ACP launch facts the dashboard's built-in Agent
  row is built from.
- `mcp_server.py` — the stdio MCP entrypoint: the shared control plane under
  the `kiro_cli` profile.
- `session_context.py` — the one session-binding identity rule the MCP server
  and the gate share.
- `pretooluse_hook.py` — the gate: maps Kiro's event and exit-code contract onto
  `loopx/control_plane/goal_mode_tool_policy.py`.
- `gated_agent.py` — builds, refreshes and retires the opt-in `loopx` agent.
