# Durable state and read-only projections

A handoff often starts with one question: which record describes the current state? A Todo, dashboard, conversation, and historical receipt may describe different moments. Reading old information is not sufficient for safe continuation.

## Why one Todo can appear in two states

Consider a teaching scenario: Agent A completes a fix, and the current Todo source records completion and evidence. Agent B reads an older cached dashboard showing open and prepares to repeat the work.

Faster refresh narrows that window but does not decide which record wins a disagreement. LoopX assigns each class of fact an owner, then derives projections for different readers. Recovery starts with current sources and external readback.

This costs state maintenance, reads, and projection updates. It also allows sessions, Hosts, and interfaces to change while retaining checkable work records.

## Why current state also needs historical receipts {#design-choice}

T1 commits R1; later work advances the source revision. When A retries the original operation, it must distinguish current state from whether that operation was accepted. A current snapshot alone may not answer the latter; an old receipt cannot authorize the next write.

| Option | Useful for | Cost in long-running work |
| --- | --- | --- |
| Reconstruct progress from chat | Motivation and small manual handoffs | Reinterpret identity, revision, and external freshness |
| Keep only the latest snapshot | Fast current-work reads | May not identify a commit whose response was lost |
| Current source plus identity-bound receipts | Read current head and historical operations separately | Maintain identity, retention, and recovery contracts |

LoopX uses the last division on applicable authority paths. R1 may be recovered while current head includes a later R2. Replaying R1 must not restore an old state. This separation is a contract, not an automatic property of a file format.

See `CoordinationCommandReceipt` and the [operation replay contract](/loopx/docs/reference/authority-operation-replay/). Provider- and command-specific recovery boundaries still apply.

## Identify the Goal and current authority first

The durable boundary is the Goal: objective, Todos, Gates, Agent identities, and runtime routes. A Host thread is an execution context. Ending a session does not automatically delete the Goal, and reading it grants no write authority.

### Reuse an exact Goal instead of guessing from text

Reuse depends on a stable `goal_id` and registry route. With multiple Goals, `start-goal --guided` offers read-only selection before an exact-id rerun. Similar objective text does not justify merging Goals silently.

Goal reuse and Agent takeover are separate operations. A new session can read the Goal, but fresh `agent_id`, explicit lane reuse, and execution leases have their own checks. Old conversations or receipts grant no new executor authority.

### Todo storage follows the selected source

Current readers distinguish legacy Markdown from selected File/SQLite authority. Identify the path this Goal actually uses; a file's presence or an installed provider is insufficient.

| Path | Todo read source | Markdown role | Operational concern |
| --- | --- | --- | --- |
| Unmigrated legacy path | Active-state Todo sections and metadata | May still be source state | Manual edits may alter read results while bypassing acceptance and receipts |
| Selected canonical provider | Its authority Todo snapshot | Compatibility workbench or projection | Use provider revision and lifecycle readback |
| Former Todo `events.jsonl` experiment | Retired; not a current recovery entrypoint | Old examples cannot restore it as authority | Handle nonempty sources through the retirement contract; do not ignore or clear them to pass checks |

The old Todo event API, replay, backfill, and completion examples are retired; see the [retirement contract](/loopx/docs/reference/protocols/event-sourced-state-contract-v0/).

This does not retire run history, quota events, or `rollout_event_log`. Each retains its own recording responsibility.

## Five state surfaces

### 1. Registry: identity, connection, and policy

The registry holds Goal ids, repositories, active-state/runtime routes, registered Agents, and runtime policy. It describes connections and boundaries; it cannot prove that a Host started or a delivery completed.

Read Goal selection, Agent identity, and authority configuration through their respective public surfaces. Provider migration needs its cutover protocol; changing a displayed field does not prove migration.

### 2. Todo and Goal state: current work facts

Todo status, dependencies, Gates, claims, acceptance, and continuation shape the next action. Current values come from the selected source. Lifecycle writers change them while preserving the required identity, revision, and evidence.

Migrated transactions use provider state, receipts, and revisions to constrain writes. Legacy paths have their own implemented locking and validation boundaries; they do not inherit every other provider's guarantees.

### 3. Active-state workbench: human-readable work

`ACTIVE_GOAL_STATE.md` presents Objective, Next Action, User Todo, Agent Todo, and Progress. Its responsibility differs by authority path, so it is neither universally a pure projection nor the home of every fact.

The [structured projection protocol](/loopx/docs/reference/protocols/active-state-structured-projection-v0/) defines typed views from that workbench. They can be rebuilt and cannot authorize writes.

Generated compatibility ids are not migration-ready canonical ids. Duplicate ids and missing sections should surface diagnostics.

### 4. Run history and rollout events: historical evidence

Run history records observations, deliveries, blockers, validation, outcomes, and follow-up conditions. Rollout events provide compact timelines and join keys. Together they support explanation, replanning, handoff, and review.

Each record family has its own append, privacy, and idempotency contract. They are not one generic event store that can replay every Todo. Raw transcripts and private material remain in their appropriate private storage.

Public projections contain only permitted summaries and references.

### 5. Status and other projections: consumer read models

`loopx status`, quota packets, dashboards, review packets, and task graphs summarize source facts for people, Agents, and schedulers.

They may summarize, rank, and compress. They cannot invent source work, replace priority with display order, or bypass write APIs through card edits. Check the scope and freshness of the inputs each projection read.

## Three records: Turn Journal, Goal State, Run History

| Record | Question answered | Recovery use | Cannot replace |
| --- | --- | --- | --- |
| Turn journal | Which phases, results, intents, and receipts were recorded for the original Turn? | Distinguish reusable results, remaining steps, and unresolved effects | Whole-Goal acceptance and follow-up decisions |
| Goal state | What are the current Todos, Gates, Vision, and acceptance? | Rebuild the frontier and authority conditions | Real-time external readback |
| Run history | What happened in a round, and what evidence supports it? | Explain progress, review, and handoff | Current state or new write permission |

A Turn journal is a durable recovery record. It may contain both prepared intents and checkpoints of committed effects. Calling it only temporary intent loses that distinction; a phase name alone also cannot establish Goal completion.

Goal state holds current lifecycle facts. A run claiming tests passed still needs a matching commit, Todo, and acceptance. Current Goal state cannot use that old run to assert that remote CI remains green.

First decide which question you are answering: recover the original Turn, choose next work, or explain history. Then read the relevant sources and check their identity and revision bindings.

## What the projection truth contract expresses

The [long-horizon state protocol](/loopx/docs/reference/protocols/long-horizon-agent-state-protocol-v0/) exposes its read/write boundary:

```json
{
  "schema_version": "long_horizon_agent_state_protocol_v0",
  "projection_is_writable": false,
  "source_of_truth": [
    "registry", "active_state", "todo_item_v0", "run_history",
    "rollout_event_log", "operator_gate", "human_reward"
  ],
  "write_apis": [
    "loopx todo", "loopx refresh-state", "loopx operator-gate",
    "loopx reward", "loopx quota spend-slot"
  ]
}
```

These are fact categories and governed write entrypoints, not a claim that every record uses one storage medium. Interpret each field through its owner and current authority path.

Task graph and Agent management projections also declare `projection_is_writable: false` and `write_api: false`. These describe a consumer contract. Lifecycle writers still enforce authority; a JSON declaration alone cannot prove every entrypoint safe.

## Storage medium and the write contract

Files, SQLite, and other providers determine storage. Revisions, idempotency identifiers, leases, and receipts determine when a transition is valid. Switching to a database does not automatically solve identity, concurrency, or effect recovery.

The [local write-correctness protocol](/loopx/docs/reference/protocols/local-state-write-correctness-v0/) describes `prepare -> preview -> apply -> record -> project`: check expected revision, protect the relevant lease, commit a readable result, and refresh views.

Delivery remains writer-specific. Check the actual command, authority mode, and provider evidence instead of applying every draft hard-idempotency, CAS, and lease requirement to all legacy paths.

### Historical baseline and current delivery boundary

Shared-authority work in `v0.5.4` introduced a provider-neutral TypeScript `AuthorityStore` contract and candidate-provider validation. Later local cutover, fencing, projections, and default-entry adoption have separate milestones.

Historical capabilities are not evidence that current paths stop at that baseline, nor that candidate qualification delivers a remote service. Read the selected Goal to establish its current File/SQLite authority path.

Staged candidates such as NoKV/PostgreSQL do not acquire runtime authority automatically. Installing a Provider also does not change an existing Goal's source of truth.

Cross-device online authority, offline writes, and service recovery need separate qualification. Shared folders, sync drives, and IM messages do not replace the authority protocol. Consult the [Shared Authority RFC](/loopx/docs/architecture/rfcs/shared-goal-authority-state-provider-v0/) and its ledger.

## Using historical evidence in a current decision

| Layer | Question | Checks |
| --- | --- | --- |
| Lineage integrity | Who recorded it, and has later material superseded it? | Producer, run/event id, recorded revision, supersession |
| Current applicability | Do the original inputs and scope still apply? | Commit, target, source revision, time window, Gate scope |
| Fresh external observation | What is the external state now? | Independent remote-ref, CI-revision, or service readback |

A fix tested at `commit-a` may now be at `commit-b`. The old record still proves what happened then; it cannot directly accept the new code. Changed Goal direction or authority scope also requires reassessment.

A new projection can contain an old observation. `generated_at` dates projection generation, not its underlying evidence. Check source revision and external freshness as well.

## Handling a projection gap

First exclude legitimate view differences: limits, Agent scopes, and role filters may hide items. If views with matching scope and revision still disagree:

1. Identify current authority and the authoritative source.
2. Locate failed source writeback, stale cache, migration drift, or expired external evidence.
3. Repair through the original owner's write or recovery path.
4. Recompute the relevant projection and verify source revision and result.
5. Resume work dependent on those facts.

Manually making displays agree can hide the source problem and does not create a missing receipt.

## Cost and usage boundaries

**Recovery needs reads and checks.** An old dashboard or conversation is usually insufficient. Read the current source, original Turn, or external system. Caches and summaries can reduce cost but need sufficient identity and freshness data.

**Projection updates may lag their sources.** This separation lets interfaces share facts while requiring consumers to avoid treating cached state as commit authority. Not every read must be stale; the important property is detectable version difference.

**Manual Markdown edits may have real effects.** Without overriding metadata, the legacy decoder reads `[x]` as done. This bypasses lifecycle validation and may omit acceptance or receipts. Under a selected provider, the same edit may affect only the workbench.

Use the current Goal's Todo lifecycle entrypoint and verify the result and evidence. An unsupported edit is not necessarily an ineffective byte change.

## Evidence and further reading

| Claim | Implementation or protocol entry | Boundary |
| --- | --- | --- |
| Select the source before projecting | `loopx/todos.py`, `todo_block_codec.py` | Legacy and provider reads differ |
| Old Todo events are retired | `legacy_event_source.py`, retirement contract | Does not retire rollout/history as a whole |
| Journals retain uncertainty and committed facts | [LoopX Turn protocol](/loopx/docs/reference/protocols/loopx-turn-v0/) | Still requires provider readback and a recovery decision |
| Read models grant no write authority | [Task graph protocol](/loopx/docs/reference/protocols/task-graph-projection-v0/) | A declaration cannot replace writer checks |

For a new field, locate its owner first: configuration in the registry, Todo transitions in their lifecycle, run observations in history/evidence, display derived as a projection. External systems retain their facts; domain-specific results belong in Domain State.

For implementation detail, follow [Course: state substrate](/loopx/docs/development/control-plane-course/04-state-substrate/) and the [Agent-scoped evidence ledger](/loopx/docs/reference/protocols/agent-scoped-evidence-ledger-v0/).

The next chapter asks which executors may change the source once it is identified.
