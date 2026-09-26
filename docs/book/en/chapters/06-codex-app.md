# Start from Codex App

Codex App supplies visible interaction, agent turns, and heartbeat automation. LoopX decides whether each
heartbeat should work, which bounded work it should select, and when it should back off or stop.

## Observable success

After setup:

- Codex App is open at the correct project root;
- `$loopx <task>` or the `LoopX` command skill receives a concrete objective;
- the Agent reuses an exact Goal or creates a planned Goal, and new onboarding uses a fresh identity;
- heartbeat automation uses a thin LoopX task body;
- `quota should-run` gates every delivery turn;
- the App reports active state id, current user Gate, top Todo, and next safe action.

## 1. Open the correct project root

The App workspace should be the Git root you intend to manage. A Host should not scan unrelated home
directories to guess a project or treat another worktree's registry as the active delivery workspace.

Ask the App Agent to begin with read-only inspection:

```text
Inspect the current project's LoopX connection. Run loopx doctor, loopx registry,
and loopx status first. Reuse existing active state and do not overwrite the Goal.
Confirm that .loopx/, .loopx/goals/, and .local/ are ignored by Git.
```

If the LoopX command facade is installed, select the `LoopX` skill or use:

```text
$loopx Inspect and improve this project's release workflow. Every transition
must have verifiable evidence.
```

Codex currently exposes LoopX through a command-facade skill. Do not assume that every Codex version
supports a user-defined top-level `/loopx`. `loopx slash-commands` prints the canonical entrypoints for the
installed version.

## 2. Plan before writing Todos

For a concrete task, the normal sequence is:

1. preserve the user's task text;
2. read or connect project state and resolve a Goal selection Gate with one exact `goal_id`;
3. register a fresh `agent_id`, or take over an existing identity only on explicit user instruction;
4. form an ordered P0/P1/P2 plan;
5. write Todos in the same order;
6. refresh state;
7. activate the App heartbeat;
8. run agent-scoped `quota should-run`;
9. deliver one bounded segment only when the contract permits it.

A natural-language plan is not proof that project state exists. A guided packet is not proof that a
heartbeat has been installed.

## 3. Understand the heartbeat boundary

The Codex App heartbeat is the Host scheduler, not a second control plane. Each thin task body should:

- read the current LoopX Goal;
- call `quota should-run`;
- respect user Gates, capability Gates, and write scope;
- advance one bounded segment;
- validate and refresh state;
- record spend only after progress writeback;
- follow `scheduler_hint` for cadence or stop.

```text
Codex App automation fires
          |
          v
LoopX quota should-run
  | run       | wait / gate / stop
  v           v
Agent turn    no delivery
  |
validate -> writeback -> optional spend
```

Do not paste complete project history into the automation prompt. The stable prompt owns the protocol;
current Todo, Gates, and capabilities arrive through the current decision packet.

## 4. Verify the connection

After setup, cross-check from a shell:

```bash
loopx status --goal-id <goal-id>
loopx quota should-run \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --codex-app
loopx history --goal-id <goal-id> --limit 10
```

Check:

- whether `normal_delivery_allowed` is true;
- whether the selected Todo matches priority;
- whether `requires_user_action` is present;
- whether `scheduler_hint` applies to `codex_app`;
- whether the latest run contains validation and writeback rather than only a status poll.

When `scheduler_hint.app_automation.stateful_backoff.apply_needed=true`, also verify that the App applied the
`recommended_rrule` and then ran the packet's complete `ack_hint.cli_args`. A recommendation or local ACK
ledger alone does not prove that Host cadence changed. If actual RRULE readback reports drift, repair it
from the current hint.

## 5. Switch between App and CLI safely

Changing Hosts does not require moving the Goal:

```text
Codex App --------┐
                  ├── .loopx registry + active Goal state
Codex CLI Goal ---┘
```

Do not let App and CLI create separate Goals with the same objective. Before switching, confirm that two
agents are not claiming the same Todo. A hard lease must expire or be explicitly handed over.

Changing Hosts and taking over an Agent are separate decisions. If this is continuation by the same peer,
carry the original `agent_id` explicitly. If this is a new peer, register a fresh id and complete the claim
or handoff before delivery.

## Recovery paths

### `$loopx` is unavailable

```bash
loopx slash-commands
loopx slash-commands --install
```

Refresh the Host's skill discovery. The CLI fallback is:

```bash
loopx start-goal --guided --project . \
  --goal-text "<task>" \
  --host-surface codex-app
```

### No heartbeat exists

Do not claim that LoopX is autonomous. Ask the Agent for the copyable thin heartbeat body and recommended
cadence, and report Host activation as a Gate. Activation is complete only when the automation exists in
the App or an equivalent readback proves it.

### The heartbeat runs without progress

Inspect `scheduler_hint`, monitor Todos, and spend history. External waits should become monitor/backoff
work rather than full agent turns.

### A Gate blocks the Goal

Inspect the Gate scope. A decision that blocks one Todo must not freeze safe work on other lanes. Repair an
overbroad Gate instead of prompting the Agent to ignore it.
