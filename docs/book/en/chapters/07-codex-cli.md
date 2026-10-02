# Start from the visible Codex CLI TUI

A visible TUI can also sustain progress. LoopX generates a stable task body for a Codex native Goal, while current decisions govern work, settlement, and waits. Distinguish running, blocked, and exited-process states.

## Why an active Goal differs from a background wake

Suppose a fix is unfinished and you terminate its Codex process. No further work occurs before you restart it. The execution environment has exited; this does not mean a live native Goal needs a user message for every turn.

| Host state | Behavior on this path | What the user needs to establish |
| --- | --- | --- |
| TUI and native Goal running | Read quota, perform allowed work, settle, reassess, and continue | Whether the current Todo, Gate, and authority allow the next step |
| Native Goal blocked | Stop automatic progress under the Host blocked/resume contract | Whether the blocker is resolved and explicit `/goal resume` is needed |
| Executing process exited | Setting `/goal` earlier does not create timed wakes | Restart and resume the existing Goal, or select a qualified external scheduling path |

Active Goal continuation and periodically starting work are separate capabilities. App heartbeat suits timed waking; the visible CLI path does not create an App automation or hidden worker by default.

## Boundary: the Host continues execution, LoopX keeps reassessing

A stable body does not copy dynamic Todos, Gates, or monitor state. Each iteration reads the complete current decision, follows its `interaction_contract`, and settles. It then rechecks quota and continues or follows current wait/block guidance.

The native Goal stays visible and interruptible while durable control information remains in LoopX. Users need not send a fresh task every turn, but continued Goal execution does not preserve a stale selected Todo or authorization.

## Start the visible TUI

```bash
cd /path/to/your-project
codex
```

Send this setup request:

```text
Connect the current project to LoopX. Run loopx doctor first, reuse existing
active state, and confirm that .loopx/, .loopx/goals/, and .local/ are ignored.
Do not use hidden headless execution. After connection, generate a thin heartbeat
task body and set the current Codex CLI task to a visible /goal <task_body>.
Report the active state id, current user gate, top agent todo, and next safe action.
```

The setup turn establishes connection and visible continuation. It should not start a large unplanned
delivery slice. If your very first turn produces a large diff, setup and delivery have been merged, and every
later step now stands on a plan nobody reviewed.

## Start a concrete objective with `$loopx`

With the command facade installed:

```text
$loopx Add compatible JSON output to this CLI, add tests, and wait for the
maintainer to approve the schema.
```

The Host should preserve the task text, plan Todos, and produce a Goal body suitable for the visible TUI.
The CLI fallback is:

```bash
loopx start-goal --guided --project . \
  --goal-text "Add compatible JSON output to this CLI, add tests, and wait for the maintainer to approve the schema" \
  --host-surface codex-cli-tui
```

The guided packet should contain or point to a copyable `/goal <task_body>`. It must not start a hidden agent
in another terminal. That one is worth confirming separately: if the guided start launched an agent in the
background, you have lost this path's only advantage, and it is hard to notice because everything on the
surface still looks correct.

## Compose native Goal and LoopX

The native Codex CLI Goal owns continuation inside the TUI. LoopX owns the project frontier:

```text
Visible Codex /goal
  -> run LoopX quota decision
  -> execute selected bounded Todo
  -> validate
  -> write LoopX state
  -> continue, wait, block, or complete
```

When LoopX returns a Gate, the Goal can become blocked. After the user satisfies the Gate, resume through
the Host's Goal surface. Do not create a second Goal to bypass the first Gate: the second Goal receives the
same frontier, and the two Goals then compete for the same Todos.

## Every turn still passes the quota Gate

Reading state from another shell does not mutate the TUI:

```bash
loopx status --goal-id <goal-id>
loopx history --goal-id <goal-id> --limit 10
loopx quota should-run \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --runtime-profile codex_cli
```

The Host runtime should identify `codex_cli`, with scheduling owned by the Goal or agent loop rather than a
Codex App heartbeat. That distinction decides who is responsible for waking the work. If the packet reports
missing scheduler context, fix the runtime profile instead of ignoring the warning. When scheduler context
is missing or contradictory, `scheduler_hint` returns `repair_scheduler_execution_context` and sets unchanged
polling to `stop_until_context_repaired`: until the runtime profile is repaired it proposes no cadence and
schedules no next wake for you.

## Preserve identity and Todo ownership

An argument-bearing guided start does not default to a fresh Agent identity when the Goal has registered
Agents (even a single one). If the current host thread is unbound, it returns an identity gate that requires
selecting one lane; if the thread is already bound to a lane, it keeps that binding. Fresh registration is
the default only for a Goal with no registered lanes or an explicit `--new-peer`. Reuse an existing id only
when the user explicitly requests takeover of that peer.

After selection, pass the same explicit `--agent-id` to the visible Goal, quota, refresh, and writeback
paths. Do not rely on a fallback: host-loop activation without task text auto-selects the only registered
lane (`single_registered_agent_selected`). "Just use it" looks harmless, but if that identity belongs to
another Host or another lane, the work's ownership was silently rewritten. With an explicit id, an
unregistered id is rejected, and a registered id that differs from the thread binding is treated as a
deliberate override, so pass one only when you mean to switch lanes.

Agent identity labels the LoopX lane. It does not prove that the work runs in Codex CLI. Use
`host_surface`, runtime profile, or run metadata to identify the Host.

A proper handoff is:

1. the current agent writes back validated work;
2. it updates or completes the Todo;
3. the new agent previews and atomically registers a fresh identity;
4. the new agent claims the unfinished Todo;
5. the new Host reads the same registry and Goal;
6. the visible Goal resumes.

## Cost and boundary

**Continuation depends on an executable Host state.** An active native Goal can progress autonomously; process exit and blocked state have separate boundaries. Automatic wakes across those boundaries require the corresponding scheduling integration and readback.

**Visibility does not replace validation.** A TUI shows activity; success still needs validation, writeback, and settlement. Whether someone watches the screen does not change those conditions.

**Handoff requires current authority checks.** App and CLI can read the same Goal. Check claim, lease, worktree, and applicable writer-fence mode when changing executors to avoid concurrent submission of the same effectful work.

## When to choose CLI, and when to choose App

| Need | Candidate path | Conditions to check |
| --- | --- | --- |
| Sustained work in the current execution session, with observation or intervention | Visible CLI native Goal | Active Goal, live Host, current admission allows work |
| Check again on cadence after a long wait | App heartbeat or a qualified scheduler integration | Actual automation, cadence, and ACK/readback |
| Continue after process exit | Resume the existing Goal or use a path supporting that boundary | An old prompt is not a new execution environment |

Select the wake mechanism you need. Visible execution does not mean manually triggering every turn. Both paths must read the current LoopX decision.

## Recovery paths

### The TUI closes

Start `codex` again from the same project root, inspect `loopx status`, and resume the existing Goal. Do not
bootstrap a duplicate objective, which leaves you with two Goals pointing at one target.

### The `/goal` body is stale

A stable body avoids copying dynamic Todos, but protocol or CLI versions can still change. Generate a new
thin body and replace the visible Goal through the Host surface. Do not hand-edit internal fields.

### Work moved to a hidden worker

Stop the worker and inspect whether it wrote evidence or acquired a lease. Restore Todo ownership before
returning to the visible TUI, and do not let two executors modify the same worktree. Check this against the
actual claim and lease ownership rather than your memory of who was running.

### The Goal polls without change

After the unchanged limit, block or wait quietly. External observation belongs in a monitor Todo. Resume
through the Host Goal surface instead of repeatedly resending the full objective; resending the same round
makes one round of work look like several of progress.

### App and CLI are both active

Inspect the old instance, claims, leases, scheduler ownership, and current writer-fence mode. Both Hosts may read one Goal; write takeover still needs the corresponding lifecycle conditions.

## Invariants

1. **Distinguish active, blocked, and exited.** An active native Goal can continue; blocked state follows the Host resume contract, and process exit does not imply a timed wake.
2. **The setup turn only establishes connection.** Merging connection with delivery puts every later step on
   an unreviewed plan.
3. **The `/goal` body stays stable.** Dynamic Todos, Gates, and capabilities come from the current decision
   packet, not from the prompt.
4. **The visible Goal and the selected Todo are separate things.** The Goal can continue; that does not make
   this Todo the right one.
5. **Pass identity explicitly.** A sole registered lane can be auto-selected; an explicit `--agent-id` gets
   an unregistered identity rejected and makes a lane switch a visible choice.
6. **Coexisting Hosts need explicit work ownership.** Check claims, leases, and fences in the current mode; shared Goal reads do not authorize concurrent writes to the same work.

## After project onboarding

At this point you can, without modifying LoopX core:

- give an existing Git project a recoverable Goal, Todo, Gate, and evidence lifecycle;
- start the same project state from Codex App or the visible Codex CLI TUI;
- preserve authority, identity, and workspace boundaries while changing Hosts;
- verify continuation through status, history, and quota.

Choose the next path by your job:

- to contribute a protocol-level change to LoopX core, continue with the
  [Developer contribution map](./source-protocol-map.md);
- to deliver an independently installed Provider, continue with
  [Choose the right extension point](./08-extension-placement.md);
- to use LoopX only as a project control plane, apply this onboarding pattern to your repository.
