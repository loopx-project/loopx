---
title: "Run long-running AI coding agents with Codex and Claude Code"
description: "Keep Codex and Claude Code work moving across sessions with LoopX. Set up durable goals, resume from evidence, coordinate review, and stop at human approval gates."
---

# Run long-running coding agents with LoopX

Use LoopX when a coding task spans sessions, waits for review or CI, or needs
handoff between agents. Your coding agent still edits and tests the code.
LoopX preserves the goal, task ownership, decisions, evidence and next action
outside the conversation so another authorized turn can continue the work.

For a small task that fits in one session, start with the agent you already
use. A host's native Goal can also be sufficient when continuation stays in
one host. The [session, Codex Goal and LoopX comparison](../book/en/chapters/02-session-goal-loopx.md)
explains when project-level state earns its additional setup.

If this is your first LoopX task, run the [small bug-fixing exercise](first-bugfix-loop.md)
before leaving a real project running. This guide takes the next step: define
a bounded result, activate the host you use, then recover the same work after an interruption.

Before starting, decide what a successful result looks like. For a failing test,
that might be a reproduced failure, a patch, passing relevant checks, and a PR
ready for review. Name the module or repository the agent may change, the
existing test command, your spending boundary, and the point where it must stop.

## Install and check the current project

If LoopX is already installed, keep that installation and start with
`loopx doctor`. For a new installation, the normal release channel is PyPI.
Use a Python environment whose console scripts are on `PATH`:

```bash
python3 -m pip install --upgrade loopx
loopx workflow-skills --install
loopx doctor
```

You need Python 3.11+ and Node.js 22.22.3+ (24 LTS recommended). See
[Installing LoopX](installing-loopx.md) for pipx, uv, the no-clone installer,
and upgrades. Choose one installation owner rather than layering another copy
over a working installation. Open or restart the agent host after registering
its skills if it cannot see them yet.

Then open the project the agent will work on:

```bash
cd /path/to/your-project
loopx status
```

If the project already has a Goal, check its objective and current owner before
continuing. If state is missing, give the task below to your host and follow the
[guided first-goal path](newcomer-command-path.md#one-cli-quickstart).
`loopx connect` creates or connects project state; it is not a read-only health
check. Do not create a replacement Goal merely to restart a session. Keep
runtime state out of version control as described in the installation guide.

## Codex: continue a task across sessions

In Codex App, use the installed `loopx` skill through `$loopx` or `/skills`.
A useful first task has a bounded result and a clear stopping rule:

```text
$loopx Fix the failing integration test, explain the cause, and prepare a
reviewable PR. Read repository instructions first; identify the failing test
and its actual test command. Reuse the existing Goal and assigned owner when
present; otherwise complete guided setup for this project and host.
Reproduce the failure, repair its cause without weakening assertions, and
run the relevant checks. Record the actual diff, test results and remaining
failures. Follow the project's existing quota and spending boundary; stop
and report if a necessary boundary is missing. Do not merge or release.
Stop when the patch and validation are ready for review, or report a concrete blocker.
```

Replace “the failing integration test” with the actual test or issue and its
scope before sending. If another Agent already owns the work, continue through
that assignment instead of starting a second implementation.

For Codex CLI, start from the project root and use the generated bootstrap
message:

```bash
loopx codex-cli-bootstrap-message --project .
```

Follow the returned instructions in the actual Codex session. App automation,
visible CLI continuation and isolated headless execution have different host
contracts; they are not interchangeable. The [driver selection table](newcomer-command-path.md#choose-the-loop-driver)
and [Codex App chapter](../book/en/chapters/06-codex-app.md) explain activation.
Installing LoopX by itself does not keep a closed or unavailable host running.

Check three different outcomes before leaving:

| Checkpoint | Evidence to look for |
| --- | --- |
| Project connected | The intended Goal and Agent are identified; existing work is reused |
| Host activated | The native Goal or App automation is actually active, not merely described in a setup packet |
| Useful work started | The agent has reproduced the named failure or reports the exact reason it cannot |

These checks establish setup and initial execution. Accept the finished patch
only after reading its actual test results and diff.

## Claude Code: track work without losing review state

The installer registers lightweight Claude Code skills. Use `/loopx` with the
same concrete result and approval boundary. Check `loopx doctor` if the command
is not available, and follow the [skill registration guide](getting-started.md#command-skill-registration).

If one agent implements while another reviews, retain separate ownership and
return the review evidence to the shared task. The
[Claude implementation / Codex review example](../product/use-cases/cross-runtime/cross-runtime-impl-review-demo.md)
shows the roles and acceptance boundary. Switching the executor does not grant
it another agent's identity or the user's merge authority.

## DeepSeek Harness: choose the native plugin or SDK connector

For a DSH Web session, the
[native LoopX plugin](https://github.com/loopx-project/loopx/tree/main/packages/dsh-loopx-plugin#install)
provides bootstrap, workflow skills, a session-bound Driver and GoalBar. Follow
its install instructions and version compatibility notes: the published
prebuilt artifact and the latest source checkout can target different DSH
versions. Installing the plugin alone does not activate continued work.

Use the [DeepSeek Harness SDK connector](../integrations/deepseek-harness-connector.md)
when an outer supervisor needs bounded headless turns instead. It is a
different integration from the native Web plugin.

## Resume after an interruption

Open the same project and read `loopx status` before issuing another task.
Ask the agent to summarize the active goal, current owner, pending decision,
last accepted evidence and next permitted action. Explicitly select the prior
agent identity if you intend to resume that lane; sharing a goal is not enough
to infer identity takeover.

A compact restart instruction is:

```text
Read this project's existing LoopX state. Report the current goal, pending
user gate, last accepted result, and next permitted task. Reuse the existing
goal. Ask me to select the prior agent identity if a takeover is needed.
Continue only the authorized task and verify the result before writing back.
```

A transcript saying “done” is not a substitute for current tests, review or
accepted evidence. See [long-task operations](../operations/long-task-cadence-policy.md)
for waiting, budgets and continuation.

If work appears stuck, distinguish a host that is unavailable from a task that
is correctly waiting for CI, review, quota or a recorded decision. Inspect that
dependency before restarting anything. To pause, pause both the LoopX Goal and
its host driver and read back their states; the [exercise's pause instructions](first-bugfix-loop.md#5-turn-the-workflow-into-a-recurring-bug-hunt)
explain the two separate controls. Deleting state is not a pause mechanism.

## What to verify before leaving a task running

- `loopx status` shows the intended goal and a useful next action.
- The chosen host has an active continuation mechanism and remains available.
- The task has an acceptance check, spending boundary and stopping rule.
- Merge, publication and other consequential operations retain explicit approval.

## Setup effort and ongoing cost

Initial setup adds CLI/skill installation, project connection and host
activation. Keep the same installation owner for upgrades, then verify skill
discovery and the host driver again. Reopening a project should reuse its
existing Goal rather than repeat setup.

Agent turns consume the host's model allowance and the project's test, build
or external-service resources. Recurring checks add wakeup work even when no
patch is produced. Cost depends on cadence, context size, retries and provider
terms; this guide does not establish a fixed price or a saving. Use existing
quota and scheduled/event-driven waits. A shell `while true` or a more frequent
heartbeat does not establish better outcomes.

For examples rather than setup, explore the
[public application scenarios](https://loopx-project.github.io/loopx/blog/application-scenarios/)
and [long-horizon terminal study](https://loopx-project.github.io/loopx/benchmarks/lhtb/).
Study results have task, model and budget limits; they do not guarantee a gain
on every project.
