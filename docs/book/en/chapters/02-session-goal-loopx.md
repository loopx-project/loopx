# Agent sessions, Codex Goal, and LoopX

The previous chapter introduced cross-session handoff. This chapter asks which state and continuation capabilities the task needs. A normal session, Host Goal, and LoopX can be combined; verify the current surface retains the needed state, waits, and recovery entrypoints.

## Start from a bad ending

```text
Fri 16:40  The user hands over a task: "add --format json to the CLI, add tests, open a PR."
Fri 16:52  Work starts in a normal session. The change is small and tests are under way.
Fri 18:10  Tests pass. The PR is open. The session ends and the user goes home.
Sat 05:00  CI fails on a second platform matrix. The failure lives only on the PR page.
Mon 09:20  The user returns. The session no longer exists. Whoever picks this up sees
           a failing PR and one sentence: "add tests and open a PR."
```

Nobody did anything wrong here. The change was correct, the tests really ran, the PR is real. What was
lost is **judgment**: why the schema looks the way it does, whether that failure was expected, which
fields "preserve the default output" actually covers, and the maintainer reply nobody has received yet.

Those existed only inside a session that has ended.

Now do the same task wrong in the other direction:

```text
Mon 09:00  The same task goes to LoopX. The Goal is registered and Todos exist: implement,
           test, release.
Mon 09:10  A test fails because one field name needs a maintainer decision. Nobody else
           may decide it.
Mon 09:12  The control plane opens Gate G, which blocks Todo C. That judgment is correct.
Mon 09:12  This machine runs only a heartbeat. It has no channel that reaches a living person.
Mon 09:40  Next tick: Gate G still blocks Todo C, and no independent Todo D is runnable.
Mon 10:20  Every tick after that produces the same result.
Tue 14:00  The user hears from someone else that the task is waiting on a decision.
```

In the first story the task stayed in the session layer and the next person knew nothing. In the second
the task was escalated to LoopX without the surface that delivers a pending question to a person. Both
failures have the same shape: **work stopped, and nobody was told who it was waiting for.**

The cost is not only the rework. From Monday to Tuesday, every wake-up recompiles the same conclusion
and costs Host wake-ups, time, and model calls (these no-change gated waits spend no LoopX quota), while
the task's completion date does not move.

## Why "big tasks use LoopX, small ones use a session" is not enough

The natural instinct sorts by task size: short work stays in a session, large work escalates to the
long-running control plane. Neither story fits. The second task was small, three Todos, and a single
pending question stalled it for two days.

The decisive variable is this: **when the task stops, what does it need in order to continue?** The
session layer has exactly one legal stopping point, the end of the session. It does not accept "waiting
for CI," "waiting on a maintainer," or "waiting until tomorrow." The long-running layer has other legal
stopping points, including waiting, blocking, and handoff, provided each one can be written down and
someone will be woken.

So two dimensions must be checked separately:

- **Task properties**: does it cross sessions, wait on an external condition, change hands;
- **Host surface**: on this runtime, can waiting, blocking, wake-up, and handoff actually happen.

Checking only the first dimension produces the second story. A decision aid has to cover both columns.

## Three layers of state

| Layer | State it primarily owns | Problem it primarily solves |
| --- | --- | --- |
| Normal agent session | Current transcript, tool results, and turn plan | Reason and execute inside one context |
| Codex Goal | Host or thread objective and Goal lifecycle | Continue turns around one goal and expose active, blocked, or complete |
| LoopX | Project-owned Goal, Todo, Gate, Evidence, Quota, and recovery state | Coordinate an auditable lifecycle across sessions, agents, Hosts, and external systems |

LoopX does not duplicate model execution, and it does not demote Codex Goal to a prompt trick. The Host
Goal owns continuation around an objective; LoopX compiles project state into the bounded work that is
legal for the next turn.

A Host may persist both transcripts and native Goal objectives/lifecycle. LoopX adds structured project work state, authority, and recovery entrypoints. The distinction is not only retention duration, but whether current decisions can reliably read and validate that information.

## Four actors

Separate four responsibilities before calling every component an "agent":

| Actor | Primarily owns | Must not own |
| --- | --- | --- |
| User / operator | Direction and decisions over private material, credentials, production, and public claims | Manual scheduling for every ordinary Todo |
| Host | Sessions, model turns, visible TUI, heartbeat, and wake-up surfaces | Project-long facts or a second custom state machine |
| Executor / Agent | Current-turn reasoning, tool calls, bounded delivery, and validation | Implicit long-term memory or unauthorized approval |
| LoopX control plane | Goal, Todo, Gate, quota, evidence lineage, and recovery protocol | Model reasoning or authoritative Git/CI facts |

Dashboards, review packets, and prompts are projection or interaction surfaces, not a fifth authority.
The wake-up surface from the last section lives in the Host row: whether a question can reach a person
depends on what the Host provides, not on what the control plane writes.

## Keep three identities separate

LoopX coordinates Goals, Agents, and Hosts, but each identity answers a different question:

| Identity | Question it answers | Stability and selection rule |
| --- | --- | --- |
| `goal_id` | Which long-running project boundary are we advancing? | Binds registry state, Todos, Gates, and evidence lineage; reuse requires the exact existing id |
| `agent_id` | Which peer or work lane owns the current responsibility? | Binds claims, Vision, quota, and writeback; new onboarding defaults to a fresh identity only when no registered lane exists or `--new-peer` is explicit |
| `host_surface` / runtime profile | Which product surface executes and wakes this turn? | Binds App heartbeat, a visible Goal, or another Host loop; declare the surface that is actually running |

Reusing a Goal does not imply that a new session should take over an existing Agent identity. The current
Goal-start contract keeps those choices explicit:

1. if several registered Goals exist, return a read-only `goal_selection_gate` and rerun with one exact
   `--goal-id`;
2. do not infer a Goal from similar objective text, a chat summary, or a directory name;
3. when registered lanes exist and neither `--agent-id` nor `--new-peer` is given, a host thread that has
   no stored binding returns `thread_binding_selection_required` (`select_agent_identity`): the default is
   to select an existing lane, neither registering a new identity nor taking one over;
4. fresh identity registration is the default only when no registered lane exists (first onboarding), or
   `--new-peer` is explicit and the current host thread has no stored binding: `start-goal` returns
   `fresh_agent_registration_required` (`register_fresh_agent`), and first onboarding needs no extra
   `--new-peer`; a bound thread keeps its bound lane;
5. continue an existing Agent only when the user explicitly selects that exact `agent_id`;
6. an Agent name or prefix does not prove the Host surface; runtime metadata does.

This lets a session continue the same project without silently impersonating the previous executor.

## Task qualification card: decide whether LoopX earns its cost

LoopX is not synonymous with "use it whenever the task is large." Write a reviewable qualification card
first:

This card is an editorial decision aid from this book. It is not a LoopX CLI schema, and `start-goal` does
not persist it automatically. The current Goal, Todo, Gate, acceptance, and boundary protocols remain
authoritative.

| Field | Question | Default when missing |
| --- | --- | --- |
| `duration` | Will work cross sessions, waiting windows, or workdays? | Normal session |
| `external_wait` | Will it wait for CI, review, approval, or an external resource? | Normal session or Host Goal |
| `handoff` | Will Agent, Host, device, or owner change? | One Host Goal |
| `authority` | Does it involve private reads, credentials, production, or external writes? | Define the Gate before automation |
| `acceptance` | Which observable evidence proves completion? | Define acceptance before "continuous improvement" |
| `baseline` | What stays matched against a normal session or Host Goal? | Make no uplift claim |
| `stop_condition` | When does work complete, block, downgrade, or stop? | Define the terminal contract first |

The first four fields map directly onto the failure in the second story. When `external_wait` is true,
the task will stop on a wait. When `authority` is true, that wait needs a human decision. With both set,
"who wakes that person" becomes a question to answer before starting, not a gap discovered later.

One positive field is not enough. LoopX usually earns its cost when work crosses sessions, includes an
external wait or handoff, has independent acceptance, and needs authority, evidence, and recovery outside
the transcript.

### Compare a session, Goal, and LoopX fairly

To evaluate real task outcomes, do not compare different tasks or budgets. Match at least:

```text
same task semantics
same runner / model / reasoning settings
same verifier contract
same time and cost budget
```

Record completion, independent verifier result, erroneous writes, human intervention, stop-policy
correctness, wall time, and cost. Without a matched baseline or independent verifier, report experience;
do not claim product uplift.

The [benchmark research RFC](https://github.com/huangruiteng/loopx/blob/main/docs/architecture/rfcs/long-horizon-harness-benchmark-research-program-v0.md)
defines the current research boundary. Benchmark-native studies can inform product research only when
their arm semantics, authority boundary, and independent verifier are stated explicitly.

## Compare them on one task

Use the same task in all three cases: add compatible JSON output to a CLI.

### Normal session

You tell an agent:

> Add `--format json`, preserve the default output, and add tests.

The agent can read, edit, and test. If the session ends while CI is running, recovery usually depends on
the Git diff, CI, and a human restating context. The rationale for the schema, the pending maintainer
decision, an expected failure, or an unperformed external action may exist only in chat.

### Codex Goal

Codex Goal separates the objective and Goal lifecycle from one prompt. The Host can start later turns
around the same objective and react differently when the Goal is active, blocked, or complete.

The user no longer needs to paste the complete objective after CI finishes. Codex Goal solves **goal
continuity inside the Host**. It does not have to become a project Todo graph, authority ledger, or
cross-Host registry.

!!! warning "Verify the current Host surface"
    Writing `/goal` in ordinary prose does not prove that a persistent Host Goal exists. Use the current
    Codex surface to create, inspect, block, resume, and complete it.

### LoopX

LoopX can retain a more specific project contract:

```text
Goal: ship-compatible-json-output
├── Todo A: implement formatter              done
├── Todo B: add schema tests                 done
├── Todo C: obtain field-name decision       blocked by Gate G
└── Todo D: release                          deferred until C

Gate G
├── scope: response.error_code
├── authority: maintainer
└── blocks: Todo C
```

The next turn can determine:

- which Todo is runnable;
- which lane the Gate blocks;
- which agent holds the claim or lease;
- which test result is valid evidence;
- whether release needs an external-effect receipt;
- whether the Host should run, wait, or monitor.

## Five project contracts LoopX adds

### 1. Todo, claim, and handoff

A Goal expresses the project outcome. A Todo is a schedulable work identity with priority, dependencies,
claim, lease, successor, and handoff information. Per-Agent Vision stores one peer's bounded role
direction, acceptance summary, and replan trigger. It is a per-Agent route, not a global product vision.

This separates "the Goal is active" from "who may perform which work now." An agent id labels a LoopX work
lane; it does not prove which Host is currently executing it. A new session may reuse the Goal history and
frontier, registering a fresh Agent identity when no lane is registered or `--new-peer` is explicit.
Existing claims move only through an explicit takeover or handoff.

### 2. Gate and authority

A chat can ask a human a question, but the project still needs to know whether that question blocks one
Todo, every lane, or no delivery at all.

LoopX distinguishes:

- `user_gate`: relevant work cannot legally continue without a decision;
- `user_action`: a person should act, but other agent work may continue;
- safe fallback: work that does not depend on the missing decision.

A useful Gate binds decision scope, authority, and blocked work. How it reaches a person is supplied by
the Host. That second half is exactly what the opening story was missing.

### 3. Evidence and receipt

"The agent invoked a command" is not proof that a transition happened. LoopX distinguishes:

- proposal: what should happen;
- observation: what was seen;
- validated evidence: checked material that supports a conclusion;
- effect readback: current fact returned by the external system;
- receipt: durable record of an accepted action or transition.

If `git push` times out, the tool invocation alone cannot mark publication complete. The workflow needs
remote readback or equivalent evidence.

### 4. Scheduler, monitor, and quota

Codex Goal lets the Host continue. LoopX adds a project decision about whether continuation is useful now:

- `quota should-run`: whether state and budget allow this turn;
- monitor: wait quietly when an external condition has not changed;
- scheduler hint: when a Host should wake again;
- backoff: avoid repeated no-change turns;
- spend: record budget only after bounded validated progress is written back.

The Host still owns the actual scheduler. LoopX emits the scheduling contract.

### 5. Cross-agent, cross-Host recovery

The canonical state belongs to the project. Codex App, Codex CLI, and other supported Hosts can read the
same Goal boundary:

```text
Codex App heartbeat ─┐
Codex CLI Goal ──────┼──> LoopX project state ──> current turn packet
Other Host hook ─────┘
```

Hosts may differ, but project state must not fork into multiple sources of truth. Recovery uses events,
lineage, projections, a fresh environment read, and replanning rather than requiring the new Host to
inherit the old transcript.

### Host compatibility matrix

LoopX preserves one control-plane contract, but Hosts do not share one wake-up implementation. The current
public
[Runtime Connector Catalog](https://github.com/huangruiteng/loopx/blob/main/docs/integrations/runtime-connector-catalog.md)
and each Host adapter's documentation define these main paths:

| Host surface | Driver | Key limit |
| --- | --- | --- |
| Codex App | `$loopx <task>` plus App heartbeat | Cadence needs RRULE apply/readback/ACK |
| Codex App over SSH | Visible `/goal` | Does not depend on App automation tools |
| Codex CLI TUI | Generated bootstrap plus visible `/goal` | Stays visible and interruptible |
| Claude Code | `/loopx` plus opt-in native `/loop` adapter | Uses the same quota and writeback |
| OpenCode 1/2 | `/loopx` plus an opt-in Goal bridge or persistent worker | The bridge or worker preserves Host visibility and stop semantics |
| Pi | Opt-in Goal extension plus `/loopx` | Bindings stay under project `.loopx/` and grant no extra authority |
| KunlunCode Goal Pro | `loopx-kunluncode` adapter | Completion and quota write back only after strict validation |
| DeepSeek Harness | Native skill plus same-session Driver or `loopx turn run-once` | Every bounded execution segment still needs independent validation |
| Shell / other Agent | Guided packet plus caller-owned runner | Caller owns wake-up without a runner hook |

This table is the data source for the second dimension of the qualification card. Catalog presence does
not mean every Host exposes the same automation API. When `host_surface` is unknown, omit it once and
follow the read-only selection Gate. Do not guess that a CLI, IDE plugin, App SSH workspace, or ordinary
shell is a Codex App heartbeat. Use the Runtime Connector Catalog and the corresponding Host documentation
for complete startup, stop, and validation details; the Dev Book does not duplicate every adapter runbook.

## How Codex Goal and LoopX compose

A typical composition is:

1. LoopX selects a Todo and checks Gates, capability, write scope, and quota.
2. LoopX produces a bounded task body or decision packet.
3. Codex Goal carries continuation on this Host.
4. The agent delivers and validates one work segment.
5. The result is written back to LoopX, which decides the next turn.

```text
LoopX control plane -> Codex Goal continuation -> Agent turn
        ^                                      |
        `---------- validated writeback -------'
```

Codex Goal is the Host lifecycle. LoopX is the project lifecycle. They should compose, not compete.

## Cost and boundary: what one layer of state buys

The two opening stories are one task that should have escalated and did not, and one that escalated
without the Host surface it needed. Putting a task in LoopX buys handoff and auditability. The price is
just as concrete.

**Cost one: the control plane has to be maintained.** Every Goal adds a registry, a Todo graph, and an
evidence lineage that must stay readable. That cost grows with the number of tasks, and what it buys is a
task that can still continue after an interruption.

**Cost two: one more input has to be filled in correctly.** `acceptance` and `stop_condition` on the
qualification card must be written by a person first. Left vague, LoopX can only spin on a goal that
cannot say when it is finished, which is more expensive than leaving the task in a session.

**Cost three: more questions before starting.** With several Goals, one must be selected exactly; an
agent identity cannot be inferred from a name prefix or similarity; an unknown host surface goes through a
read-only selection Gate first. That is noisy for a one-shot task and is exactly what makes recovery
possible for a cross-session one.

**Boundary one: task size is not the criterion.** The qualification card asks what the task needs when it
stops. Size appears only in the `duration` row, and only in the form "does it cross sessions."

**Boundary two: LoopX does not make the model smarter.** It delivers auditable execution. Model
capability, reasoning quality, and verification difficulty are unchanged; improvement there means investing
in the harness and the verifier.

**Boundary three: the Host surface decides which capabilities are actually available.** The
`external_wait` and `handoff` fields the card judges only take effect when the current Host can wake,
push, and read back. On a Host without those surfaces, the right move is to narrow the task or change
surfaces, not to escalate and expect the task to sort itself out.

**Boundary four: a control plane exists for project-level problems.** A one-shot task with no cross-session
work, no external wait, and no handoff is faster inside a session. Creating state to look more agentic
pays exactly the costs above.

## Named failures: the price of picking the wrong layer

Three scenarios follow directly from the mechanisms above, and each is observable without special tooling.

**Failure one: a task that should have escalated stays in a session.** A field name that needs a
maintainer decision can only wait for someone to ask again. The signal is `authority` or `external_wait`
being true while the task outlives a single session.

**Failure two: escalated to LoopX with no surface that wakes a person.** This is the second opening
story. The Gate was right, the Todo was blocked correctly, and every tick reached the correct conclusion,
while the person who could unblock it was never told.

**Failure three: continuing across Hosts on an inherited identity.** Reusing one Goal's history while
registering a fresh Agent identity (when no lane is registered or `--new-peer` is explicit) is a legal path. Inferring a similar `goal_id`, or taking over an old
`agent_id` directly, leaves two writers assuming they own the same "current progress."

The way to check is to read state, not the transcript. After a restart, the control plane should still be
able to say where the task stopped, whom it is waiting for, and who acts next. These are the basis for handoff; task risk and maintenance cost still determine whether onboarding is worthwhile.

## Invariants

1. **Transcripts can carry information, but cannot alone establish current control state.** After changing runtime surfaces, recheck identity, authority, work items, and evidence revisions.
2. **Verify each layer's continuation boundary.** Ordinary sessions can also wait or block; a long-running control plane gives those conditions structured persistence, readback, and continuation paths.
3. **The qualification card has two dimensions.** Task properties say what the task needs; the Host
   surface says whether that is available now. Checking only the first column yields a Goal that is
   correct on every wake-up and never advances.
4. **Identity is explicit.** Reusing a Goal, onboarding an Agent, and declaring the actual host surface
   are separate declarations, never inferred from a prefix or a similarity score.
5. **Compare the three layers with one ruler.** Without a matched baseline and an independent verifier,
   you can report experience, not product uplift.

Decide where the task will wait, who can advance it, and whether the current runtime supports that continuation. Next, [the four demands of long-running work](02b-long-horizon-requirements.md) develops the framework before state, work graphs, and Turn transactions.
