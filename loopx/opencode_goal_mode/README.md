# LoopX OpenCode adapter

LoopX exposes two separate OpenCode layers: a static command facade and an
optional executable goal bridge. Ordinary command installation never activates
the runtime bridge.

## Install

The default `all` surface installs only static files under
`~/.config/opencode/commands/`:

```bash
loopx slash-commands --install
```

Enable the goal bridge explicitly:

```bash
loopx slash-commands --install --surface opencode --with-goal-bridge
```

Before writing any OpenCode file, bridge installation validates
`opencode.json`, `opencode.jsonc`, and `package.json`. It fails closed if a
config is invalid or directly registers `opencode-goal-plugin`, because loading
that plugin beside the LoopX wrapper would start two independent goal runtimes.

After preflight succeeds, the installer writes the command facade plus:

- `~/.config/opencode/plugins/loopx-goal.js`;
- `~/.config/opencode/loopx/goal-bridge-runtime.mjs`;
- pinned bridge dependencies in `~/.config/opencode/package.json`.

OpenCode runs `bun install` for config-directory dependencies at startup.
Restart OpenCode after bridge installation so it installs those dependencies
and loads the local plugin.

## Runtime

Run `/loopx <task>`, then activate the returned host packet through
`loopx_goal_activate`. The runtime flow is:

```text
OpenCode idle event -> loopx quota should-run
  -> run_now: continue once
  -> wait: schedule a bounded local recheck without a model call
  -> user input: answer it; automatic native resume requires fresh Core admission
  -> explicit pause / permission denial: stop automatic continuation
  -> unchanged-poll limit: pause the visible goal and stop the timer
  -> terminal_no_followup: stop the timer and disarm continuation
```

Use `/goal pause` to pause this session and `/goal resume` to re-arm it. Core
Goal stop or zero compute must also be resolved through its owning Goal/quota
settings; host resume does not grant work. Idle events and ordinary chat do not
clear an explicit pause. Permission denial or host errors also disarm the bridge.
An interrupted tool's result/settlement remains the original operation's duty.
The bridge disarms its local continuation before awaiting a pause/block tool or
pause-command hook. A slow or failed native stop cannot admit another idle
continuation. Native errors remain visible; local disarming is not proof that
the host cancelled an already-submitted request.

This changes the previous default for validated terminal closure: the bridge
stops rather than polling every five minutes. A recurring Goal that still has
future work should project the existing wait/monitor contract, not terminal
no-follow-up. Pause→resume invalidates an earlier in-flight quota result;
subsequent continuation requires a new probe. Read back host `goal_status` and
`loopx quota should-run --goal-id <goal_id> --agent-id <agent_id>` separately:
host state alone is not Core admission.

Bindings are private per-session JSON files under
`$LOOPX_OPENCODE_STATE_DIR`, or `$XDG_STATE_HOME/loopx/opencode` by default,
and use mode `0600`. OpenCode's one-shot `opencode run` process cannot own
timers after exit; recurring operation requires the visible TUI or a
persistent OpenCode server. OpenCode 2 runs a persistent goal worker instead;
see `loopx/opencode2_goal_mode/README.md`.

Quota probes carry `--record-host-poll`, so LoopX writes a compact poll
receipt beside the goal state file and `loopx global-risks` can flag a loop
whose bridge died mid-wait. Probe failures retry with a bounded exponential
backoff (3 minutes up to 30) and reset on success. A per-session lock file
prevents two OpenCode processes from evaluating the same session at once.

A user message sets `userMessagePending` so the next admitted idle evaluation
can resume the wrapped goal, which may pause itself while answering. This does
not clear `autoResume=false`. Local binding writes are serialized with lifecycle
revision checks; a stale probe cannot overwrite a newer pause/resume transition.
The unchanged-poll limit pauses the visible goal rather than silently dropping
the timer. Host stop failure is not reported as confirmed native cancellation.

The wrapped goal plugin also persists private restart state under the active
project's `.opencode/goals/` directory. Add `.opencode/goals/` to the project
ignore rules so goal text, checkpoints, and lifecycle state cannot enter a
public commit.

## Uninstall

Remove only the static command facade:

```bash
loopx slash-commands --uninstall --surface opencode
```

Remove the static facade and LoopX-managed bridge files:

```bash
loopx slash-commands --uninstall --surface opencode --with-goal-bridge
```

Bridge uninstall preserves `package.json` dependencies because user-owned
local plugins may share them.
