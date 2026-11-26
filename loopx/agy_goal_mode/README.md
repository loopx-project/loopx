# Antigravity CLI goal mode

LoopX adapter for the [Antigravity CLI](https://antigravity.google/docs/cli/using/)
(binary `agy`) — Google's terminal coding agent. agy ships both halves of a
goal-mode host natively: a goal primitive and an in-session scheduler, so
LoopX binds the objective to the host's own loop instead of pretending the
agent merely drives itself.

## Native goal primitive (verified live on agy 1.1.18)

`/goal <task>` — built-in command, "Run until the specified goal is
completely finished." The host injects a forced-continuation contract that
keeps auditing the work until the model emits `<!-- GOAL_COMPLETE -->`
(cancel with `<!-- GOAL_CANCELLED -->`). Live evidence:

- Interactive TUI (tmux): `/goal Create the file … and verify …` ran
  "Initiating Goal Analysis", created the file, self-verified content, and
  completed — file landed on disk with exact content.
- Headless print mode: `agy -p '/goal just reply ok'` returned a response
  ending in `<!-- GOAL_COMPLETE -->`.
- The mechanism is built into the binary (command description,
  forced-continuation system prompt, `GoalState` persistence, both tokens).

## Native wake primitives (verified live on agy 1.1.18)

- `schedule` tool — `DurationSeconds` + `Prompt` (wake message), recurring
  wakes via `MaxIterations`, one-shot timers with early-termination
  conditions. Live probe: a turn that ended with "scheduled" at T received a
  SYSTEM_MESSAGE wake at T+25s and the session answered autonomously — no
  external driver, no user input.
- Background tasks (`manage_task`) and async subagents
  (`invoke_subagent`/`send_message`) wake a live session; agent messages land
  in the session inbox (`manage_inbox`).
- `hooks.json` (user or plugin) runs `PostToolUse`/`Stop`/`PostInvocation`
  automation on tool events.

## What this surface is

Antigravity CLI discovers global skills from the fixed
`~/.gemini/antigravity-cli/skills` root using its documented flat layout —
one `<name>.md` file per skill. LoopX reaches an `agy` session through the
generated `/loopx` skill facade, and the activation binds the objective with
the native `/goal <task_body>` command.

Two honest limits, stated in the activation packet:

- **Quota pacing is advisory.** LoopX installs no AGY hook that intercepts
  native continuations or wakes, so `quota should-run` entry is facade
  guidance the agent is instructed to follow — not a host-enforced gate. agy
  will continue natively without it.
- **Everything lives and dies with the session.** The goal loop and wakes
  fire only while the CLI session is alive — there is no cross-session
  daemon — so they arm a live session's bounded segments, not an unattended
  host loop.

## Install

```bash
loopx slash-commands --install --surface agy
```

Writes the managed LoopX skill facades (`loopx.md`, `loopx-global-*.md`, …)
flat into `~/.gemini/antigravity-cli/skills/` — the documented agy layout.
The official CLI documents no home override, so LoopX offers none either:
installs target exactly that path. Managed files carry the
`loopx-managed-slash-command` marker and are refreshed by rerunning the
installer; user-owned files are never overwritten.

## Use

From an Antigravity CLI session in a connected project, invoke the `loopx`
skill (or type `/loopx <complex task>`). The facade instructs the agent to run:

```bash
loopx start-goal --guided --project . --slash-command-arguments="<task>" --host-surface agy
```

After todo writeback, bind the generated heartbeat task body with the native
`/goal <task_body>`, start every following turn (and every `schedule` wake
and audit-continuation) with `quota should-run` (advisory guidance; LoopX
does not intercept native host continuations), and arm the next bounded
wake with the native `schedule` tool only when quota allows more work.

## Reboot-Survival Watchdog (`antigravity-app` on macOS)

In-session `schedule(...)` timers suspend while Antigravity is running, so an
OS reboot or Antigravity Language Server restart clears in-memory timers.
`loopx.agy_goal_mode.host_runtime` provides two guarded subcommands for
surviving restarts without disturbing healthy `swarm_wait` or `schedule` sleeps:

- `heartbeat-tick`: discovers the running Antigravity Language Server, reads
  `trajectorySummaries[<conversation-id>]` (`status` and `lastModifiedTime`)
  and `ps -o lstart= -p <ls_pid>`, checks Core admission (`quota should-run --scheduler-observation`)
  only when eligible, acquires an exclusive file lock before sending
  `SendUserCascadeMessage`, and wakes **only** when `status == "CASCADE_RUN_STATUS_IDLE"` and either:
  1. `ls_start_epoch > last_modified_epoch + 5.0` (proven OS reboot or LS restart
     since the last turn, deduped once per `last_woken_ls_pid`), **or**
  2. `now - last_modified_epoch >= long_idle_seconds` (default `2400`s / 40m
     stall fallback, bounded by `>= 1800`s cooldown).
- `install-launchd`: previews (default dry-run) or installs/unloads a
  `~/Library/LaunchAgents/<label>.plist` (`RunAtLoad=true`, `StartInterval=300`)
  running `heartbeat-tick` with `--execute` (or `--uninstall --execute`).

```bash
python3 -E -P -m loopx.agy_goal_mode.host_runtime install-launchd \
  --goal-id <goal-id> --agent-id <agent-id> --conversation-id <conversation-id> --execute
```

## Layout

- `__init__.py` — host facts: install surface id, fixed skills root resolution,
  activation extras (advisory quota pacing and continuation boundaries),
  and the native goal + wake primitives the activation cites.
- `host_runtime.py` — stdio `Stop` hook recovery and guarded `heartbeat-tick` / LaunchAgent CLI.
- `launchd_watchdog.py` — macOS LaunchAgent plist builder and `launchctl` lifecycle helper.
