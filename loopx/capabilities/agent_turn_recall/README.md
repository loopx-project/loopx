# Agent Turn Recall

`agent-turn-recall` prepares memory guidance for an autonomous agent turn when
there may be no new user prompt. It composes existing LoopX surfaces instead of
introducing another memory store:

- quota and Todo projection select the current work;
- Agent Turn Recall builds the bounded situation and query;
- Reward Memory enforces corpus, identity, authority, freshness, and lifecycle;
- the configured context provider performs read-only retrieval.

The capability is default-off. A goal must explicitly enable a Reward Memory
experiment for the agent and configure the `agent_workflow.turn_admission`
surface.

## Turn Contract

`agent_turn_situation_v0` includes the agent, goal, project, selected Todo,
phase, recent outcomes, and next intent. It records
`user_prompt_included=false`; chat text is not a fallback input. The material
fields produce a `situation_fingerprint`. Combining that fingerprint with the
host-provided `turn_instance_id` produces a `turn_recall_id`.

This gives two useful identities:

Turn ids are opaque host identities, not timestamps. Execution uses the current
UTC observation time for memory freshness while preserving the original turn
identity for validation and deduplication.

- a changed Todo, target, phase, or intent changes the situation fingerprint;
- a new turn changes the recall id and recalls again, even when the situation
  is otherwise unchanged.

Repeated execution with the same recall id may reuse one ignored local receipt.
The receipt stores only the compact private context needed to reproduce that
turn's guidance; it never stores provider payloads, credentials, or query text.
Its path includes collision-safe digests of both `goal_id` and `agent_id`, so
the same Goal-local Agent name in another Goal cannot reuse the receipt.

## Usage

Preview without provider access:

```bash
# Persist the exact packet already used for turn routing in ignored local state.
loopx quota should-run ... --format json > .local/turn-quota.json

loopx agent-turn-recall \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --turn-instance-id <iso-turn-id> \
  --quota-decision-json .local/turn-quota.json \
  --format json
```

Recall after quota/Todo selection:

```bash
loopx agent-turn-recall \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --turn-instance-id <iso-turn-id> \
  --quota-decision-json .local/turn-quota.json \
  --execute \
  --format json
```

The command consumes the exact quota packet instead of rebuilding status. This
keeps turn admission bounded and ensures the recall query uses the same selected
Todo and interaction state that the host is following. `-` may be used to read
the packet from stdin when the host owns a safe pipeline.

The result carries a private `context.guidance` list for agent reasoning. It is
not action authority. The agent must still obey the current interaction
contract, capability gates, write scopes, and user gates.

The production `loopx turn run-once --execute` and executing quota admission
call this hook before the host. The DSH adapter injects the same bounded context
into the actual task body rather than merely reporting a hit count. At Turn
start the hook first reconciles any ambiguous evidence-backed outcome write for
this exact Goal+Agent; successful exact readback can therefore participate in
the next recall without creating another queue or memory store.

Retrieval relevance and action applicability remain separate. Recalled
guidance is conditional private context; the agent must compare it with the
exact turn situation and current authority before acting.

The optional Goal Channel `work resume` composition uses a read-only invocation:
it refreshes recall instead of reusing a previous receipt and disables pending
ingest reconciliation for that invocation. Ordinary automatic admission retains
its existing reconciliation behavior. See the
[room-work usage contract](../../../docs/reference/goal-channel-room-work.md#private-read-only-reconnect-context)
for current authority readback and private reference handling.

## Freshness And Failure

Active Reward Memory records may carry `lifecycle.expires_at`. Retrieval ignores
the record at or after that timestamp. Wrong user, peer, project, session, or
surface scope is rejected before provider application.

Provider and application failures preserve an empty base context, do not create
a user gate, do not spend quota, and do not deliver to external sinks. The host
can therefore call this capability at turn admission without making memory
availability a prerequisite for safe autonomous progress.
