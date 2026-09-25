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

## Install and check the current project

Run the [no-clone installer](installing-loopx.md), then open the project you
want your agent to work on:

```bash
curl -fsSL https://loopx-project.github.io/loopx/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
cd /path/to/your-project
loopx doctor
loopx connect
loopx status
```

`connect` should reuse an existing connection. If state is missing, follow the
[guided first-goal path](newcomer-command-path.md#one-cli-quickstart); do not
replace an existing goal just to restart a session. Keep runtime state out of
version control as described in the installation guide.

## Codex: continue a task across sessions

In Codex App, use the installed `loopx` skill through `$loopx` or `/skills`.
A useful first task has a bounded result and a clear stopping rule:

```text
$loopx Fix the failing integration test, explain the cause, and prepare a
reviewable PR. Preserve the existing project goal and record test evidence.
Wait for my approval before merging.
```

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

## What to verify before leaving a task running

- `loopx status` shows the intended goal and a useful next action.
- The chosen host has an active continuation mechanism and remains available.
- The task has an acceptance check, spending boundary and stopping rule.
- Merge, publication and other consequential operations retain explicit approval.

For examples rather than setup, explore the
[public application scenarios](https://loopx-project.github.io/loopx/blog/application-scenarios/)
and [long-horizon terminal study](https://loopx-project.github.io/loopx/benchmarks/lhtb/).
Study results have task, model and budget limits; they do not guarantee a gain
on every project.
