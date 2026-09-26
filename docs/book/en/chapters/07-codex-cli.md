# Start from the visible Codex CLI TUI

The defining constraint for the Codex CLI path is **visible and interruptible**. Work should remain in the
user-visible TUI instead of silently moving to a headless worker for the sake of automation.

## Observable success

After setup:

- `codex` starts from the intended project root;
- the setup turn reuses or connects LoopX state;
- the current Codex task becomes a visible `/goal <thin task_body>`;
- later turns continue in the same TUI;
- LoopX still owns Todo, Gate, Quota, and writeback;
- the user can inspect, interrupt, and resume the work.

## 1. Start the visible TUI

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
delivery slice.

## 2. Start a concrete objective with `$loopx`

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

The guided packet should contain or point to a copyable `/goal <task_body>`. It must not start a hidden
agent in another terminal.

## 3. Compose native Goal and LoopX

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
the Host's Goal surface. Do not create a second Goal to bypass the first Gate.

## 4. Verify visible continuation

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
Codex App heartbeat. If the packet reports missing scheduler context, fix the runtime profile instead of
ignoring the warning.

## 5. Preserve identity and Todo ownership

An argument-bearing guided start does not default to a fresh Agent identity when the Goal has registered
Agents (even a single one); it returns an identity gate that requires selecting one lane. Fresh
registration is the default only for a Goal with no registered lanes or an explicit `--new-peer`. Reuse
an existing id only when the user explicitly requests takeover of that peer.
After selection, the visible Goal, quota, refresh, and writeback paths should preserve the same explicit
`--agent-id`. A missing or mismatched identity must fail closed rather than fall back to “the only Agent.”

Agent identity labels the LoopX lane. It does not prove that the work runs in Codex CLI. Use
`host_surface`, runtime profile, or run metadata to identify the Host.

A proper handoff is:

1. the current agent writes back validated work;
2. it updates or completes the Todo;
3. the new agent previews and atomically registers a fresh identity;
4. the new agent claims the unfinished Todo;
5. the new Host reads the same registry and Goal;
6. the visible Goal resumes.

## Recovery paths

### The TUI closes

Start `codex` again from the same project root, inspect `loopx status`, and resume the existing Goal. Do not
bootstrap a duplicate objective.

### The `/goal` body is stale

A stable body avoids copying dynamic Todos, but protocol or CLI versions can still change. Generate a new
thin body and replace the visible Goal through the Host surface. Do not hand-edit internal fields.

### Work moved to a hidden worker

Stop the worker and inspect whether it wrote evidence or acquired a lease. Restore Todo ownership before
returning to the visible TUI, and do not let two executors modify the same worktree.

### The Goal polls without change

After the unchanged limit, block or wait quietly. External observation belongs in a monitor Todo. Resume
through the Host Goal surface instead of repeatedly resending the full objective.

### App and CLI are both active

Inspect claim, lease, and scheduler ownership. Both Hosts may read the same Goal, but an effectful Todo can
have only one legal executor.

## After project onboarding

At this point you can, without modifying LoopX core:

- give an existing Git project a recoverable Goal, Todo, Gate, and evidence lifecycle;
- start the same project state from Codex App or the visible Codex CLI TUI;
- preserve authority, identity, and workspace boundaries while changing Hosts;
- verify continuation through status, history, and quota.

Choose the next path by your job:

- to make any public LoopX contribution, continue with the
  [Developer contribution map](./source-protocol-map.md);
- if that contribution needs an independently installed Provider or package, continue from the map to
  [Choose the right extension point](./08-extension-placement.md);
- to use LoopX only as a project control plane, apply this onboarding pattern to your repository.
