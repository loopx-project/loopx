# Newcomer Command Path

LoopX should feel like one product path before it feels like a CLI catalog.
For a first-time user, the default surface is:

1. Install or repair the CLI.
2. Ask an agent to connect the current project.
3. Use the host's LoopX command entry to start useful work.

The full command set remains available for operators and contributors, but it
should not be the first thing a newcomer has to understand.

For a task-oriented walkthrough, see
[long-running coding agents with Codex and Claude Code](long-running-coding-agents.md).

## The Two Commands To Remember

| Need | Use | Expected result |
| --- | --- | --- |
| Check whether this project is connected and what is waiting. | LoopX status entry | The agent reads LoopX status, gates, todos, and next safe action without starting a new delivery path. |
| Start concrete long-running work. | LoopX task entry with `<task text>` | The agent plans first, writes ordered todos, then advances one bounded, validated slice at a time. |

Examples:

```text
$loopx fix the open PR review feedback and keep the patch reviewable
$loopx split this refactor into PR-sized slices and stop at unsafe gates
```

In current Codex surfaces, invoke the explicit `loopx` skill with `$loopx` or
choose it from `/skills`. On hosts that expose native custom slash commands,
the same task text may appear as `/loopx <task text>`.

## Choose The Loop Driver

LoopX preserves objective state, gates, todos, quota, and evidence. It does not
replace the app or CLI that runs the next agent turn. Pick the driver that
matches the surface you already use:

| Surface | Start with | What keeps it moving |
| --- | --- | --- |
| Codex App | `$loopx <task text>` or `/skills` -> `loopx` in the project thread | The app heartbeat automation. Let the agent install or refresh the generated LoopX heartbeat body; start at the bootstrap cadence, then follow `quota should-run.scheduler_hint`. |
| Codex CLI | `codex` from the project root, then paste `loopx codex-cli-bootstrap-message --project .` output | Current verified Codex CLI builds do not load user-installed `/loopx` or `/prompts:loopx` commands. Keep the executor visible, then set the generated `/goal <thin task_body>`. |
| Claude Code | Install LoopX, then `/loopx <task text>` | The installer registers lightweight slash-command skills. Enable the opt-in adapter only when Claude Code's native `/loop` should be gated by LoopX `should_run`. |
| OpenCode | Install the OpenCode surface, then `/loopx <task text>` | The bridge writes commands, plugin, runtime, and pinned dependencies. After todos are written, call `loopx_goal_activate` to bind the quota-gated goal loop. |
| Other agent or shell | `loopx start-goal --guided --project . --goal-text "<task text>" --host-surface <exact-host>` | The guided packet previews the same transaction an agent should execute: inspect or connect state, plan todos, refresh status, activate a host loop, run quota, and ack scheduler hints when needed. If the surface has no runner hook, LoopX can track state but the user drives it manually. |

If you already own the Agent runner or workflow supervisor, use
[Embed LoopX In Your Agent Runner](custom-agent-runner-integration.md) instead
of reconstructing the lifecycle from the full CLI catalog.

## One CLI Quickstart

Use this when an agent asks for the manual shell path, or when you are setting
up a fresh terminal without an agent driving the first step:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
loopx slash-commands --install
loopx bootstrap-command-pack --project .
loopx start-goal --guided --project . --goal-text "<your first long-running task>" \
  --host-surface codex-cli-tui
```

The command pack checks the host-facing recovery packet. The guided start
packet is the first task path: paste the generated transaction into Codex,
Claude Code, or another compatible agent that can run shell commands from the
project root. Replace `codex-cli-tui` with `codex-app`, `codex-app-ssh`,
`codex-ide-plugin`, `claude-code`, or `shell` when that is the actual host. Use
`codex-app-ssh` for Codex App attached over SSH without automation tools. Use
`codex-ide-plugin` only for the installed IDE plugin, not for another Codex
surface that happens to be used near an editor. When the host is unclear, omit
the flag once and follow the returned read-only selection gate; that preview
does not write project state.

## Multi-Project Manager Commands

Keep these out of the first two commands, but do show them once a user has more
than one LoopX project or agent lane:

| Need | Use |
| --- | --- |
| See the cross-project progress digest. | `/loopx-global-summary` |
| See only user or owner gates. | `/loopx-global-gates` |
| See runnable project-agent work. | `/loopx-global-todos` |
| See risks and blocked lanes. | `/loopx-global-risks` |

These are manager views. They should summarize and route work across projects;
they should not replace the project-local LoopX task entry as the way to start
useful work inside one repository.

## When To Use More Commands

| If you are trying to... | Use this surface first | Only then reach for... |
| --- | --- | --- |
| Start project-local work | LoopX task entry with `<task text>` | `loopx start-goal --guided --goal-text ...` when manually bootstrapping an agent; `loopx bootstrap-command-pack --goal-text ...` only when implementing or debugging a host handoff. |
| Understand several LoopX projects at once | `/loopx-global-summary` | `/loopx-global-gates`, `/loopx-global-todos`, or `/loopx-global-risks` when you need a focused manager view. |
| Understand why work is paused | `/loopx` | `loopx diagnose --goal-id <goal-id>` when the agent needs a deeper evidence packet. |
| Review a handoff or gate | The agent's LoopX status summary | `loopx review-packet --goal-id <goal-id>` for a copyable operator packet. |
| Operate recurring work | Codex App heartbeat or visible Codex CLI task body | `loopx heartbeat-prompt --thin --goal-id <goal-id>` when installing or repairing the loop. |
| Debug the control plane | The specific error or blocked todo | `loopx status`, `loopx history`, `loopx quota should-run`, or `loopx check`. |

## Reference Boundary

The command catalog is reference material. Keep it useful, but keep it below
the product path:

- New users should not need to scan every command before trying LoopX.
- Public examples should prefer the host's LoopX task entry unless they are
  teaching installation, debugging, or maintainer workflows.
- Contributors may still use the full command reference in
  [Getting started](getting-started.md#command-reference).
- Installed users can run `man loopx` or `loopx commands` when they need a
  grouped operator reference instead of the first-run path.
