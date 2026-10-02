# From one session to long-running work

An Agent that can edit code, run tests, and explain results inside the current session has not thereby demonstrated that it can reliably own work spanning several days. This chapter explains where that gap comes from, and why closing it needs something that lives outside the session.

## An opening that looks fine

Say you want to add `--format json` to an existing CLI:

```text
10:02  You ask the Agent to change the output layer.
10:11  Done — default text output unchanged, tests added.
10:14  Local tests pass; commit, push, open the PR.
10:16  CI starts. The Agent says: "Waiting on CI; once green, please have
       the maintainer confirm the JSON contract."
10:17  You close the window and head to a meeting.
```

Up to 10:17 everything is in order. The trouble starts there:

```text
Next day, 09:30  You reopen it. CI went green yesterday, but nobody knows.
Next day, 09:30  The maintainer asked about field naming in the PR. No reply.
Next day, 09:31  The Agent does not know what it did yesterday, rereads the
                 code, and asks: "Would you like me to implement
                 --format json?"
```

The code and tests in this scenario may be correct; the handoff failed to carry readable wait targets, acceptance ownership, and recovery entrypoints. Even if the Host persists chat history, the next executor needs current PR, CI, and work state.

## Why this is structural

The obvious reaction is "just don't close the window." But that depends on a premise you cannot maintain: that the work finishes inside one continuous session. Real work keeps breaking it:

- the context gets compacted, and early steps yield to recent ones;
- CI takes hours to answer;
- the maintainer replies the next day;
- another Agent takes over, starting from an empty context;
- an external dependency moves, and yesterday's judgment no longer holds.

Long-running work needs to prepare for these situations. Control information intelligible only within the current context makes handoff depend on manual reconstruction; durable records and clear read entrypoints reduce that dependency.

## Session context is working memory

A model context is a natural home for:

- local reasoning about the current problem;
- the code just read;
- this round's tool output;
- the short plan about to execute.

It is a poor home for the *only* copy of these facts:

| Fact | Why the context cannot be its only home |
|---|---|
| The current goal and acceptance criteria | After compaction only the latest steps remain; the "why" is gone |
| What work is done and what validated it | The next executor cannot tell "done and verified" from "intended" |
| Which action is waiting on whose decision | Waiting spans sessions, and the context does not |
| Which Agent owns the current task | After a restart or handoff, ownership cannot be recovered from memory |
| The current state of the outside world | Yesterday's CI status may already be stale |

A Host can persist transcripts, but old conversation alone does not establish current authority, acceptance, or recovery state. The test is whether the next executor can recheck those facts without depending on the original session's implicit understanding.

## Execution plane and control plane

Once state lives outside, the next question is who uses it.

The **execution plane** performs one bounded action: an Agent edits code, a shell runs tests, a provider calls GitHub, a Host starts the next model turn.

The **control plane** decides which action is legal now, why to continue, when to wait, and how results enter durable state — whether the goal and acceptance still hold, which Todo in the current frontier may run, whether a Gate needs a user, whether current evidence supports a transition, whether quota allows another round, and where to resume after an interruption.

```text
Control plane: select and constrain the next step
       ↓
Execution plane: perform one bounded action
       ↓
Observation / receipt: return verifiable results
       ↓
Control plane: accept, reject, or replan
```

**The control plane does not replace the execution plane.** LoopX does not write code, host Git, or stand in for CI. It lets those systems' results be consumed by a work lifecycle that spans turns — exactly the link missing after 10:17.

## Why three long-running workloads share one control plane

LoopX's control contract is not bound to one business process. The Control-Plane Course in the repository uses three showcases to show that domain facts and acceptance differ completely while Goal, Todo, Gate, Quota, Evidence, and recovery mechanisms stay reusable.

| Showcase | Domain facts and judgment | Reused control plane |
|---|---|---|
| PR Issue Fix | Issue feasibility, exact-head checks, review and merge state | Todo, claim, workspace guard, monitor, successor, terminal closeout |
| Single-Agent Auto ML | Metric contract, matched baseline, experiment revision, external task and guardrail | Quota, Provider receipt, monitor, defer/resume, promotion Gate |
| Multi-Agent Auto Research | Hypothesis, dev/holdout evidence, support or refutation | Per-Agent frontier, handoff, Evidence lineage, promotion/retirement |

All three compress into one long-horizon loop:

```text
external fact
  → Provider observation
  → Capability domain judgment and transition proposal
  → Kernel checks authority, frontier, quota, workspace
  → Agent / Host runs one bounded Turn
  → independent validation, evidence, receipt written back
  → recompute continue | wait | ask | replan | repair | terminal
```

What is reused is **lifecycle invariants** rather than a generic prompt. Issue-Fix understands `CHANGES_REQUESTED`; Auto ML understands a matched baseline; Auto Research understands holdout. Those domain meanings belong to Capability and Domain State. Who may claim, what is runnable, when to wake again, which evidence permits writeback, and whether the Goal can terminate all come from the same Kernel contract.

This boundary also explains why a new domain capability should not clone its own runner, queue, retry, and completion state machine. The domain layer supplies decidable facts and proposals, Providers make external calls, and the Kernel owns cross-domain lifecycle.

To enter architecture and source entry points through the three showcases, continue with [Control-Plane Course lesson 2](/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/); for a first pass at the vocabulary, start with the [concept primer](/loopx/docs/development/control-plane-course/00-concept-primer/).

## What minimal externalized state looks like

For the running example, the minimal state is not the full transcript but a set of facts that answer the recovery questions:

```yaml
# Simplified for explanation; not a LoopX file format
goal: add compatible JSON output to the CLI
acceptance:
  - default text output unchanged
  - JSON schema has tests
frontier:
  - todo: awaiting maintainer confirmation of field naming
    state: blocked
gate:
  question: is error_code acceptable as a stable field?
evidence:
  - unit tests passed at commit abc123
next_wake:
  when: maintainer decision arrives
```

The value of these fields: **the next executor does not have to trust the previous Agent's account** and can instead re-derive the next step from the goal, the work queue, Gates, evidence, and a fresh environment. The example is an explanatory model, not LoopX's storage format.

## Cost and boundary: what this gives up

Externalized state carries costs. Naming them is what lets you judge when it pays.

**Cost one: one more thing to maintain.** The project gains a directory that cannot be edited casually and whose lifecycle must be understood. Wrong state is worse than missing state, because downstream readers treat it as true.

**Cost two: every step gets slower.** Reading state, checking legality, validating, and writing back all cost more than simply starting. Small tasks finishable in one session are dragged down by those steps.

**Cost three: external facts must be observable.** If CI results, maintainer replies, and external dependency state cannot be read by a program, the control plane can only wait — and waiting needs someone to re-trigger it.

**Boundary one: this book is about long-running work, not every task.** Work that is closed in scope, finishable in the current context, free of waits on external events, and free of cross-Agent handoff is fine as a plain session — chapter 2 gives the qualification criteria.

**Boundary two: the control plane does not own domain judgment.** It does not decide whether an issue is worth fixing or whether an experiment's metric is significant; that belongs to Capability.

**Boundary three: recovery needs current conditions for action.** See [recovery and boundaries](04-runtime-boundaries.md).

## Invariants

1. **Prompt content alone does not prove durable writeback.** Inspect addressable records, identity, and revision rather than whether a window is open.
2. **The execution plane and control plane cannot substitute for each other.** The control plane does not write code; the execution plane does not decide whether it is allowed.
3. **What is reused is lifecycle invariants, not a prompt.** That is what lets three different domains share one control plane.
4. **The next executor does not need to trust the previous one's account.** If it does, the state was not externalized far enough.

Next, use [sessions, Host Goals, and LoopX](02-session-goal-loopx.md) to decide which state layer the task needs, then read the [four demands](02b-long-horizon-requirements.md) for the architectural framework.
