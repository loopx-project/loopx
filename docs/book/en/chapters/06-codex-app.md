# Start from Codex App

Monday afternoon you install heartbeat automation in Codex App. The RRULE says every 20 minutes. Tuesday
morning you open the App to see how far the "rewrite the release workflow" goal has moved.

The log shows the automation fired 47 times since yesterday afternoon and every run exited cleanly. Not one
Todo moved. Not one piece of evidence was added. The rewrite still sits on the first step you wrote on
Monday. Nothing errored; each wake simply read a waiting condition and went quietly back to sleep. You spent
the night assuming it was working.

None of those 47 runs was wrong. The monitor genuinely was not due, the external state genuinely had not
changed, and `should_run=false` genuinely was the correct answer at the time. The failure is that **you read
"was woken" as "was working"**, and in any log those two states look the same.

## Why "just call it manually" does not hold

The reflex is to switch the automation off and run it by hand when you need it. That does stop the idle
spinning, but it buys a more expensive problem: the entire premise of long-running work is that you do not
have to stand watch. If you must remember to invoke it on Tuesday morning, the goal can only advance while
you are awake and thinking about it.

The other instinct is to make every heartbeat do something — "it woke up, so let's move a little." That one
is worse. It turns a waiting condition into a real spend: acting while external state has not changed
produces no progress, only side effects that later need cleaning up.

So there is a tension that has to hold both ways: **you need a periodic wake to cross the hours you are not
looking at the screen, and each wake has to be able to prove it did not waste itself.** Together, those two
require that the act of waking and the judgment of whether to work live in different places.

## The boundary: the Host owns waking, the LoopX decision owns the next step

The split looks like this:

```text
Codex App  -- creates the session, fires on the RRULE, applies cadence, returns real readback
LoopX      -- decides whether this wake should work, which Todo, when to back off or stop
```

The App is the Host scheduler. It decides **when** someone takes a look; it does not decide **whether that
look should act**. Only the current decision packet can answer the second question, because only it knows
the Gates, claims, leases, and monitor due times.

That boundary has a direct consequence: **there is no local copy of state inside the App that can be used
to guess the next step.** The automation prompt stays thin and carries protocol only. Dynamic Todos, Gates,
and capabilities arrive through the packet each wake reads.

```text
Codex App automation fires
          |
          v
LoopX quota should-run
  | run       | wait / gate / stop
  v           v
Agent Turn    no delivery
  |
validate -> writeback -> optional spend
```

The right branch is a legal outcome, and it is the common one. It means no work was due for **this** turn; it
does not mean the Goal frontier is closed. A settled turn's replay returns `should_run=false` while preserving
the current cadence and deferring evaluation to a fresh Turn identity. Landing there often is therefore not
evidence that the cadence should slow down. Apply the four steps below only when the current `scheduler_hint`
proposes a change (`apply_needed=true`).

## Open the correct project root

The App workspace should be the Git root you intend to manage. A Host should not scan unrelated home
directories to guess a project, and it should not treat another worktree's registry as the active delivery
workspace.

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

## Plan before writing Todos

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

Steps 5 and 7 are easy to reverse. Activating the heartbeat first means you start being woken on a timer
before the Todos exist, and each wake receives a Goal with no frontier yet. Write the Todos first and the
very first wake already has a defined candidate.

You do not have to run every internal command yourself, but you should be able to see these transitions in
the Agent's report. A natural-language plan is not proof that project state exists, and a guided packet is
not proof that a heartbeat has been installed. Each needs its own readback.

## Every wake still passes the quota Gate

Once the heartbeat is installed, each thin task body should:

- read the current LoopX Goal;
- call `quota should-run`;
- respect user Gates, capability Gates, and write scope;
- advance one bounded segment;
- validate and refresh state;
- record spend only after progress writeback;
- follow `scheduler_hint` for cadence or stop.

Cross-check from another shell:

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

That last line is what separates working from merely waking. A read-only poll leaves a history record, but
it advanced no frontier.

## The ACK convergence chain: proposal, apply, readback, ACK

Back to the 47 idle wakes. The other half of that story is cadence: when the decision says to slow down,
does the App actually slow down?

With `apply_needed=true`, the packet publishes a `recommended_rrule`. Four steps follow, and none is
optional:

```text
proposal  ->  host apply  ->  host readback  ->  ACK
```

Apply the RRULE in the App, read back the value that **actually took effect**, and then run the packet's
full `ack_hint.cli_args`. That route is typically:

```bash
loopx quota scheduler-ack-current <packet-bound-args...>
```

The step most often skipped is the readback. Treating a successful `automation update` as done is a common
shortcut: the local ACK ledger has an entry and the cadence looks applied. But the ledger records the
**proposal**, not the value on the Host. When the two disagree, the packet reports `drift_detected` on
`scheduler_hint.app_automation.stateful_backoff.host_observation.status`, and the real cadence has already
diverged from the ledger. Repair it from the current hint rather than trusting the record that looks
complete.

The reverse combination exists too. When `apply_needed=false` and `ack_needed=true`, exact Host readback
already matches the target cadence, so skip the no-op update and run the bound ACK directly. Reading that as
"nothing to do" leaves an unsettled proposal, and the next wake raises the same requirement again.

If the apply fails or times out, do not ACK; run `failure_hint.cli_args` once so the proposal stops at an
identifiable position. A cadence change does not itself record delivery spend, so a failure here will not
corrupt the quota ledger — but it leaves the system waking on the wrong rhythm.

## Cost and boundary

**Cost one: waking is a fixed overhead that does not track the workload.** Every 20 minutes means 504 wakes
a week, most of which take the `wait` branch. Each one creates a session, reads state, and compiles a
decision. That cost is unrelated to how many steps the week actually advanced, and it does not shrink when
the task slows down. Cadence therefore needs to converge, but LoopX's `scheduler_hint` and stateful backoff
propose that convergence and the four steps carry it to the Host. Do not hand-edit the RRULE because many
wakes take `wait`; a manual change shows up in readback as `drift_detected`.

**Cost two: the convergence chain is long.** Four steps separate proposal from final ACK, and on the books
a missing step looks the same as a completed one. This is a chain that only closes with readback, not
something that settles as fast as assigning a local variable.

**Cost three: idle spinning and real work look identical on the graph.** Both leave trigger records, and
both exit successfully. Telling them apart requires the writeback and spend history, not the heartbeat log.

**Boundary one: a heartbeat does not create a frontier.** A Goal with no Todos will not produce a next step
because it was woken more often. The App guarantees someone looks; it does not guarantee there is anything
to do. With no runnable candidate, enter wait or monitor and let the cadence slow.

**Boundary two: the automation is not a second control plane.** Do not copy the quota state machine into
the automation prompt, and do not let it resolve runnable Todos on its own. The stable prompt carries
protocol; the judgment belongs to the packet read on each wake.

**Boundary three: switching Hosts requires ownership checks.** App and CLI may read one Goal. Check the old instance, current claims/leases, and writer-fence mode. Use lifecycle handoff where required; a newly started Host does not authorize concurrent work on the same item.

## When to choose App, and when to choose CLI

The main difference is the execution and wake boundary. An active CLI native Goal can continue allowed work in a visible TUI without a user message every turn. App heartbeat additionally provides a configured, verified timed-wake path.

| Need | Candidate path | Checks |
| --- | --- | --- |
| Wait for an external condition and check again on cadence | App heartbeat | Automation exists; cadence and ACK/readback agree |
| Sustained work in the current session with visible intermediate results | CLI native Goal | Live Host, active Goal, current admission permits work |
| Continue a blocked native Goal | Explicit Host resume path | Recheck quota after resolving the blocker |
| Continue after the executing process exits | Resume the existing Goal or use qualified external scheduling | Do not infer a background service from an old Goal body |

Check continuation and timed waking separately. Whichever Host you select, new work follows the current LoopX decision.

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

Inspect `scheduler_hint`, monitor Todos, and spend history. An unchanged external wait should become
monitor/backoff work rather than a full agent turn each time. If the RRULE was slowed but the firing
frequency did not change, check whether `host_observation.status` reports `drift_detected` and re-apply and
ACK from the current hint.

### A Gate blocks the Goal

Inspect the Gate scope. A decision that blocks one Todo must not freeze safe work on other lanes. For an
overbroad Gate, repair the project state instead of prompting the Agent to ignore it. While a Gate stays
unhandled, the `human_gate` cadence backs off and surfaces the concrete Gate once instead of repeating the
same quiet poll; if a failed Host cadence update leaves a tighter poll, the notice cooldown also bounds
repeat reminders so that a wait does not become a high-frequency notification.

## Invariants

1. **A heartbeat firing is not work happening.** Judge reality by writeback and spend, not by trigger count.
2. **Every wake passes through `quota should-run`.** Without that Gate, the automation becomes a bypass
   around the decision.
3. **`should_run=false` is a legal outcome that closes this turn only.** A settled replay preserves the
   current schedule and re-evaluates under a fresh Turn identity, so neither the count of `false` results nor
   this turn's receipt is grounds to slow the cadence. Slowing happens when the current `scheduler_hint`
   proposes it (`apply_needed=true`), through the four steps below.
4. **A cadence change needs all four steps**: proposal, host apply, host readback, ACK.
5. **A local ACK ledger does not prove Host state.** Only a Host readback that matches the target closes the
   loop; a mismatch is repaired as `drift_detected`.
6. **Switching Hosts needs explicit handoff.** Shared Goal reads do not replace current authority, claim/lease, and fence checks.

Those six point at the same thing: a periodic wake buys the **ability to keep trying**, not the **fact of
continued progress**. Telling those apart is the whole of requirement four in this chapter.
