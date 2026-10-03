# Trace one protocol chain

## Start from a silent field rename

A bad ending with no error message. That is what makes it dangerous.

```text
Mon 14:02  A contributor decides required_decision_scopes is too long. He
           renames it to something shorter, updates the write schema, the CLI
           write path, and the tests he can see.
Mon 14:40  The affected unit tests pass. The blast radius looks contained to
           the source side, so he opens the PR.
Mon 16:10  CI is green. The reviewer sees a diff limited to the schema and the
           writer, and merges.
Tue 09:31  The Host starts a new turn. A publish Todo appears in the frontier.
Tue 09:31  Its required scope field does not exist under the new name. The
           reader treats a missing field as an empty set, so the Todo now shows
           no scope requirement at all.
Tue 09:34  No scope requirement means no Gate covers it. Quota selects it, the
           TurnEnvelope packs it, the Host executes it.
Tue 09:41  The homepage ships.
Tue 11:20  A user notices the homepage is live while their approval Gate is
           still open.
Tue 11:26  The run history is checked: every step on that chain returned
           success.
```

From a code perspective the contributor did reasonable work: writer updated, schema updated, tests run. The mistake was tracking only the **producers** of the field and never its **consumers**. A field name is a contract on both sides, so renaming one side renames the contract, and the projection's missing-field fallback translated the contract violation into a legal-looking state.

That failure defines this chapter's core move: **follow a chain until you can name every place that consumes the fact, and what each place degrades into when the fact is absent.**

You cannot understand a control-plane behavior from the final status page or one reducer output. Start from source facts, follow the contracts that transform them, and inspect the effect and receipt that close the chain.

## Why "rename it and run the tests" is not enough

The obvious method is: change the field, run the tests, commit when green. The gap is that test coverage and chain coverage are different sets:

- **Tests cover the consumers you thought of.** A unit test asserts the behavior of the module you changed. It will not discover a different module reading the same field in a different way.
- **Consumption crosses layers and languages.** One field can be validated by a schema, normalized by a parser, renamed by a projection, and formatted by a renderer. Those four evolve separately and never reference each other.
- **A missing field has a default, and the default makes a decision.** When absence falls back to an empty set, `None`, or an empty string, nothing errors. The system keeps running with that default in the decision.
- **A successful return value is not evidence.** Every layer succeeded, and the chain was still wrong.

So replace the debugging question. Do not ask "why is the final JSON wrong?" Ask "**where is the first boundary that violates the invariant?**"

This chapter uses one reusable scenario:

> Publishing the homepage requires user approval. Repairing the internal link checker does not depend on that approval, so the Agent should continue with the repair.

The scenario connects Gate scope, the work graph, interaction channels, scheduling, a bounded Turn, and evidence. It also shows why "one open User Todo" cannot be compressed into "stop the whole Goal."

## What you should learn

After this chapter, you should be able to:

- trace an observable failure back through source, projection, policy, effect, and receipt;
- use protocol fields and invariants as source-reading breakpoints;
- distinguish source correctness, projection correctness, and decision correctness;
- detect when two protocol layers assign different meanings to the same field;
- draw the smallest verifiable protocol chain for a cross-module behavior.

## Freeze the scenario invariant first

Before reading code, state the expected semantics:

```text
G1 covers publish_homepage
T1 requires publish_homepage
T2 requires no user decision

therefore:
  user_channel must surface G1
  agent_channel must not run T1
  agent_channel may run T2
  scheduler must preserve active work
```

Forbidden outcomes:

```text
G1 blocks the entire Goal
G1 disappears because T2 can run
T1 runs because the user was merely notified
the Host reconstructs another action from status prose
```

Those outcomes represent overblocking, a lost user interaction, authority leakage, and Host policy drift.

## The complete protocol chain

```text
typed Todo and Gate facts
  -> normalized Todo and Gate projection
  -> decision-scope coverage
  -> Agent-scoped frontier
  -> quota precedence
  -> interaction_contract
  -> TurnEnvelope
  -> one bounded Host effect
  -> independent validation
  -> event / run / evidence writeback
  -> fresh projection
```

Each arrow is a contract boundary. Do not debug the entire final JSON at once. Locate the first boundary that violates the invariant.

## Stop 1: Can source facts express the intent?

This simplified shape is explanatory, not an importable LoopX configuration:

```json
{
  "todos": [
    {
      "todo_id": "publish-homepage",
      "task_class": "advancement_task",
      "required_decision_scopes": [
        "public_claim:action:bilingual_homepage"
      ]
    },
    {
      "todo_id": "repair-link-checker",
      "task_class": "advancement_task",
      "required_decision_scopes": []
    }
  ],
  "gates": [
    {
      "todo_id": "approve-homepage",
      "task_class": "user_gate",
      "decision_scope": "public_claim:action:bilingual_homepage",
      "status": "open"
    }
  ]
}
```

[`decision_scope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/decision-scope-v0.md)
owns coverage semantics. The important facts are:

- the Gate declares kind, granularity, and scope key;
- the protected Todo declares its required scope;
- the independent Todo does not inherit an unrelated Gate;
- a `user_action` or natural-language reminder is not promoted into authority.

If the source contains only "waiting for homepage approval," the system cannot reliably decide whether the link-check repair is blocked. Repair the source contract or return a typed diagnostic. Do not let a projection guess global authority.

### Source-reading breakpoints

Search for the Todo contract, Gate lifecycle, and decision-scope schema before opening a status renderer. Answer:

1. Which controlled CLI, event, or workbench path writes the field?
2. Does missing, unknown, or malformed scope cause rejection, repair, or an explicit compatibility path?
3. Does Gate resolution consume only covered requirements?
4. Does a stable decision or event identity make retries idempotent?

Question 2 is the entry point to the opening failure. If a missing field falls back to an empty set, renaming that field silently deletes a scope requirement. If the source semantics are unclear, reading deeper into quota only amplifies the ambiguity.

## Stop 2: Does the projection preserve the relationship?

Todo and Gate facts feed several read models:

- status summary;
- Agent-scoped frontier;
- task graph;
- attention queue;
- quota input.

[`task_graph_projection_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)
may display `blocks`, `requires_decision`, `validates`, and handoff relations. It does not create authority.

The scenario needs this relationship:

```text
G1 -> blocks -> T1
T2 -> independent of -> G1
```

If the projection retains only:

```text
open_user_gate_count = 1
```

then downstream policy cannot distinguish scoped blocking from global blocking.

### Three projection failures

| Failure | Symptom | Correct response |
| --- | --- | --- |
| Source lacks scope | Gate has no relation | Typed repair or a concrete controller question |
| Parser drops scope | Source has scope, projection does not | Repair the read model and add parity evidence |
| Renderer hides scope | JSON is correct, Markdown only says "waiting" | Repair presentation without changing source or policy |

All three can look like "status is wrong." They have different owners and tests.

### Projection-reading breakpoints

Inspect the read-model contract rather than HTML or Markdown text:

1. Does input come from canonical, public-safe sources?
2. Do malformed, duplicate, and truncated rows produce diagnostics?
3. Does Agent scope preserve current, other-agent, and unclaimed lanes?
4. Is projection construction deterministic and side-effect free?
5. Does rendering consume an already-built typed payload?

A renderer fix must not change the runnable frontier. A parser fix must not grant a Gate global scope.

## Stop 3: How does policy select the frontier?

Projected candidates pass through current guards:

```text
open work
  -> dependency and resume
  -> gate scope
  -> claim and agent scope
  -> capability
  -> workspace guard
  -> freshness
  -> runnable frontier
```

For this scenario:

- `publish-homepage` is blocked by the matching Gate;
- `repair-link-checker` remains runnable;
- the Gate remains visible in the user channel.

This is not a Gate bypass. The selected fallback never performs the protected action.

### Ordered policy, not scattered booleans

Quota can simultaneously observe:

- projection health failure;
- autonomous replan;
- Agent handoff wait;
- a due monitor;
- a capability gap;
- throttling or pause.

The right question is not:

```text
if open_gate_count > 0, should_run = false?
```

It is:

```text
Given current source facts and precedence,
which typed interaction mode owns this turn?
```

Rules need an explainable first match, suppression conditions, and a reason. Negative rules matter: why an unrelated Gate does not block independent work, and why a runnable advancement suppresses a monitor-derived replan.

### Policy-reading breakpoints

Use [State Machines](https://github.com/huangruiteng/loopx/blob/main/docs/product/core-control-plane/state-machine.md)
and the [Rule Seam Map](https://github.com/huangruiteng/loopx/blob/main/docs/product/core-control-plane/rule-seam-map.md)
to verify:

1. input facts are normalized;
2. repair, Gate, replan, monitor, and runnable-work precedence is explicit;
3. negative decisions have named rules and counterexamples;
4. one final interaction contract is authoritative;
5. the scheduler consumes that result instead of rechecking lower-level flags.

A current builder name can help navigation. It cannot replace these five protocol checks.

## Stop 4: Keep both interaction channels

This simplified contract shape expresses the intended semantics:

```json
{
  "user_channel": {
    "action_required": true,
    "selected_action": "review_homepage_preview"
  },
  "agent_channel": {
    "must_attempt": true,
    "selected_todo_id": "repair-link-checker"
  },
  "cli_channel": {
    "next_command_kind": "bounded_delivery"
  }
}
```

It states one protocol fact:

```text
user needs to know about G1
and
agent still has independent T2
```

The user has an open decision **and** the Agent has independent work. Different channel values are not a contradiction. Collapsing them into one `action_required` field loses one obligation.

After `interaction_contract` arbitrates the turn, the Host must not reread status prose and decide that all work should stop—or that the Gate can be hidden.

## Stop 5: TurnEnvelope carries an already-decided turn

[`turn_envelope_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/turn-envelope-v0.md)
is a bounded read model over the quota decision. It carries enough information for execution:

- Goal, Agent, and selected Todo;
- current action and authority boundary;
- allowed write and effect scope;
- validation and writeback obligations;
- diagnostics and cold-path references.

It must not:

- recompute Gate coverage;
- select another Todo from prose;
- place complete active state or raw transcripts on the hot path;
- grant authority merely because the Host has a capability.

If the Envelope selects `repair-link-checker`, the Turn remains bound to that causal frontier. A resumable Host session cannot silently return to an old homepage-publication action.

## Stop 6: LoopX Turn creates one bounded effect

[`loopx_turn_v0`](https://github.com/huangruiteng/loopx/blob/main/docs/reference/protocols/loopx-turn-v0.md)
constrains execution:

```text
decide
  -> prepare
  -> invoke one bounded host segment
  -> independently validate
  -> write back
  -> spend at most once
```

The Host may repair the link checker and run focused tests. It may not publish the homepage in the same transaction. A previously prepared publication command has no authority under the current Envelope.

Turn identity should bind Goal, Agent, Todo, decision revision, and idempotency or effect identity. A stop between phases returns typed failure or resumable state; it does not require transcript archaeology.

### Effect-reading breakpoints

Check the Host contract:

1. Is the request typed, bounded, and explicit about authority?
2. Is Host capability declared?
3. Are dry-run, denied authority, timeout, and failure distinct?
4. Is the result bound to the original proposal or effect identity?
5. Does raw output remain local adapter state?

A zero exit code is not Goal acceptance.

## Stop 7: Validation and receipt close the chain

A Host claim that the repair worked is only a candidate result. An independent validator checks:

- the targeted link failure is gone;
- publication state did not change;
- the relevant tests pass;
- the diff matches the selected Todo.

Successful writeback can create:

```text
run snapshot
  + validation receipt
  + artifact or commit reference
  + todo lifecycle event
  + quota spend event, when accountable
```

Failure still creates useful state:

```text
typed blocker
  + failed validation
  + safe retry / replan / repair route
```

Receipts are revision-bound. A link check from an old commit does not validate a new diff, and a Host success result does not replace the independent postcondition.

## Stop 8: Fresh replay detects projection drift

After writeback, read again:

```text
canonical events
  -> status projection
  -> quota decision
  -> next frontier
```

A healthy result shows:

- `repair-link-checker` completed with validation evidence;
- the homepage Gate remains open;
- `publish-homepage` remains blocked by the same scope;
- quota does not select the completed repair again;
- the scheduler derives its next action from the remaining frontier.

Run history saying "complete" while status says "open" is a projection gap. Correct status followed by quota selecting the old Todo is a replay or decision gap. Do not hand-edit displays until they agree.

## Keep a protocol matrix, not a call-stack diary

After source tracing, record:

| Boundary | Input | Owned invariant | Output | Proof |
| --- | --- | --- | --- | --- |
| Todo/Gate source | Lifecycle command or event | Typed scope and writer authority | Canonical facts | Schema/contract test |
| Projection | Canonical facts | Deterministic, read-only, diagnostic | Normalized relation | Parity fixture |
| Policy | Normalized facts | Ordered precedence and fail-closed authority | Interaction mode | Decision table |
| Envelope | Final decision | No re-arbitration | Bounded request | Contract test |
| Host | Bounded request | Capability and authority match | Candidate result | Fake-Host smoke |
| Validator | Candidate and source | Independent postcondition | Receipt or blocker | Focused smoke |
| Writeback | Receipt and revision | Idempotent durable transition | Event/run/spend | Replay test |

This matrix survives function moves. A call-stack screenshot rarely does. It is also what the opening PR lacked: one "Input" column would have shown that the rename's consumers were not yet all found.

## Common misreads

### Inferring authority from Markdown

Markdown is a human-facing projection. It can omit fields and cannot grant permission.

### Stopping all work at any Gate

Only scope coverage decides whether the selected action is blocked.

### Calling independent fallback a bypass

Fallback does not cross the Gate. It selects another frontier the Gate does not cover.

### Treating Host success as Todo completion

The result still needs independent validation, writeback, and fresh replay.

### Treating one function fix as a cross-layer protocol repair

When source, projection, decision, and receipt contracts are affected, one branch change can leave a second interpretation active.

## Cost and boundary: tracing is not free

Tracing a complete chain is far slower than reading one function, and it is not always the move you need.

**Cost one: the longer the chain, the higher the tracing bill.** The example here has eight stops; a real chain may cross two or three languages and a process boundary. Following every change to the end would make a two-line fix cost five documents. The workable rule: decide from blast radius whether this change touches a contract boundary. A renderer's formatting change does not require re-walking source and policy.

**Cost two: the code you read is current; the document is from whenever someone last maintained it.** The matrix you write at the end of a trace goes partly stale with the next migration. Its value is recording **who owned which boundary at that time**, so a later reader knows what to re-confirm rather than receiving a permanent answer.

**Cost three: tracing produces confidence, and confidence can be locally complete.** Reaching stop 8 with a healthy fresh replay feels like a whole chain. The opening failure is the opposite: every step succeeded and the chain was severed.

**Boundary one: when reading source beats reading the protocol document.** When you need to know how this release actually handles a missing field. Fallback values, defaults, and parse order live in code; a protocol summary usually does not cover every malformed input, and those details are exactly where silent failures land.

**Boundary two: when reading the protocol document beats reading source.** When you are deciding authority, scope coverage, or whether a change is legal. A field may be read and written in four or five places while only one is canonical; reading implementations one by one invites mistaking one consumer's local behavior for the contract.

**Boundary three: tracing locates, it does not authorize.** Walking a chain to its end does not qualify you to change it. Whether the work is yours, whether a maintainer must agree, and whether the slice is right-sized still come from the public contribution entrypoint. That boundary was the subject of the previous chapter.

## Named failures: two checks you can run

The opening failure compresses into two checks you can run on a clean checkout:

```bash
grep -rn "required_decision_scopes" loopx/ | wc -l
grep -rn "required_decision_scopes" loopx/ --include=*.ts | wc -l
```

The first lists every place the name appears: schema, writer, parser, projection, fixture, renderer. A rename is only finished when each of those has been re-confirmed, not when the lines that produce the field are updated. The second marks the language boundary: the semantic owner may already be on the other side, and changing only Python leaves a consumer still reading the old name.

The second check targets the missing-field fallback:

```bash
grep -rn "or \[\]\|or ()\|or {}\|\.get(.*, \[\])\|\.get(.*, {})" loopx/control_plane/todos/ | head -20
```

Each of those forms is reasonable on its own. They are a reminder: **as long as a missing field falls back to an empty set, one rename can delete an authority requirement without a single error.** That is the same mechanism as the "every layer returned success" ending above.

## Checklist

For a control-plane failure, verify in order:

- [ ] Are source facts complete, typed, and written by an authorized path?
- [ ] Does projection preserve relations, diagnostics, and Agent scope?
- [ ] Does policy use explicit precedence and negative rules?
- [ ] Does the interaction contract preserve user, Agent, and CLI obligations?
- [ ] Does TurnEnvelope carry rather than re-arbitrate the decision?
- [ ] Does the Host effect bind capability, authority, and proposal identity?
- [ ] Is validation independent from the Host's success claim?
- [ ] Is writeback idempotent, and does fresh replay produce the correct frontier?

## Invariants

After a trace, these should hold on their own:

1. **Every renamed or redefined field comes with a full consumer list.** Updating producers alone is incomplete, and a missing-field fallback counts as a consumer.
2. **Success at every layer is not evidence that the chain is correct.** The test is the first boundary that violates the invariant, not the final return value.
3. **A missing field's default participates in the decision.** So the contract must state whether absence means reject, repair, or compatibility fallback.
4. **Authority has exactly one canonical source.** Several places may read a field and only one owns it; mistaking a reader for the owner is the most common leftover from a cross-layer change.
5. **Judging authority follows the protocol document; judging current behavior follows the source.** When they conflict, pick by conflict type and make this change update the other side.
6. **Tracing locates; it does not authorize.** Reaching the end of the chain does not decide whether the change is yours to make.

The next chapter changes this rule deliberately: it begins from an invariant and decision table, then chooses the smallest complete implementation slice.
