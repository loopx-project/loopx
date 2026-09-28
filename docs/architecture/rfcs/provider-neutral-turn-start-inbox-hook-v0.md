# RFC: Provider-Neutral Turn-Start Inbox Hook v0

| Field | Value |
|---|---|
| Status | Accepted; implemented behind explicit provider configuration |
| Supersedes / closes | none |
| Date | 2026-08-26 |
| Decision boundary | How fresh external inbox evidence reaches an Agent before it selects ordinary Goal work |
| Core owner | Hook admission, ordering, bounded public receipt, and Agent-read obligation |
| Provider owner | External read, provider schema validation, private cursor, and local inbox persistence |
| Agent owner | Semantic triage and durable Goal/Todo/effect writeback |

## Decision

LoopX adds a provider-neutral `turn_start` capability-hook phase. An enabled
provider hook runs before status and quota projection. It may perform bounded
external reads, mutate declared owner-private inbox/cursor state, and—only when
the provider registration explicitly requests `provider_message_reaction`—add
one idempotent acknowledgement reaction to a captured, still-pending,
human-authored message that the hook has read into the Agent's turn-start
processing chain. This read acknowledgement is independent of mention, reply,
question, and other attention classifications. The
public result contains counts, booleans, status, and an error code; it cannot
contain message content, provider payloads, credentials, destinations, profile
names, or private cursor values.

The hook is complete only when fresh evidence is routed to Agent reading. A
result with new observations must set `agent_read_required=true`. Its
registration declares one bounded, public-safe `required_read` descriptor.
The generic hook kernel validates that descriptor, deduplicates it by command,
and projects it into both Agent and CLI interaction channels with
`ordering=before_work`. A fresh ordinary material read emits a non-blocking user
notification while preserving the already selected work lane. If the material
remains durably pending on the next turn, the existing material-review lane
preempts work for recovery. A direct question or verified reply still preempts
on its first turn. The Agent reads the messages and chooses one typed semantic
disposition:

- `steer_current_turn`: update the selected work without changing the durable
  Goal frontier;
- `replan_goal`: update Todo/vision/priority state before continuing;
- `record_context`: commit a durable domain effect or evidence record;
- `continue_current_work`: record that the message was considered but does not
  change the current plan; or
- `no_follow_up`: settle irrelevant or duplicate material explicitly.

Provider code never chooses these outcomes. Core never interprets private
message text. The Agent owns semantic judgment and must bind any material result
to a durable effect receipt before inbox ACK.

## Ordering

```text
provider-neutral turn_start dispatch
  -> provider read + owner-private inbox commit/readback
  -> first Agent-read receipt independent of optional provider reaction
  -> retry optional reaction from durable pending reads
  -> fresh status + quota projection
  -> generic kernel projects the registered required read before selected work
  -> private drain into the active Agent turn (ordinary selection may remain)
  -> semantic disposition + durable settlement
  -> ACK and resume the prior lane when appropriate
```

Running the hook after quota selection is incorrect because fresh steering can
arrive after ordinary work has already been chosen. Returning raw content in
the public hook result is also incorrect because shared registries and Turn
journals are public-safe control-plane surfaces. CLI request validation runs
before hook dispatch, so an invalid quota request performs no provider read or
owner-private write; a valid request still dispatches the hook before status
collection and quota selection.

## Failure and replay

- `empty` means a valid provider success envelope was read and no pending
  message received its first Agent-owned turn-start read in this dispatch.
- `provider_contract_error` means the success envelope did not match its
  declared schema; it must never degrade to `empty`.
- provider permission and availability failures remain typed and isolated.
- duplicate hook identities run once; duplicate messages collapse by provider
  message identity, and separate private read/effect receipts prevent duplicate
  Agent-read observations and duplicate provider reactions.
- collector-only capture performs no provider write. The acknowledgement is
  admitted only after the turn-start hook reads and confirms a pending message.
- reaction disablement never cancels the first-read obligation. Failed effects
  retry from the owner-private pending-read set rather than relying on the
  bounded provider overlap window; uncertain effects fail closed before create.
  Replay is bounded by one aggregate per-dispatch attempt budget. A private
  collector-scoped cursor rotates route priority across dispatches, while each
  route keeps a private round-robin message cursor. A large or failing
  acknowledgement backlog therefore cannot indefinitely block turn admission,
  and every pending read remains eligible across routes and messages.
- attention classification affects scheduling and reply policy, never whether
  a successfully read pending message receives the acknowledgement.
- a provider-owned self-message filter may run before inbox ingestion only from
  a typed sender and an exact identity verified for the configured profile;
  unresolved identity fails open to capture and cannot use display-name or body
  heuristics.
- provider-local cursors are single-flight and advance only after inbox and
  cursor readback. History ingestion and acknowledgement replay use independent
  cursor positions: an old topic's new reply is admitted by its new provider
  message identity even while older acknowledgement debt remains.
- `partial` multi-route success still requires Agent reading for accepted
  observations while retaining a compact failure code for the incomplete
  routes.

The hook grants no repository, production, outbound-message, or arbitrary
external-write authority. Its only allowed local writes are the registered
owner-private inbox and cursor scopes. Its only admitted external write is the
explicit `provider_message_reaction` scope: one configured reaction on a
captured, still-pending message read by the Agent turn-start hook. Realtime
collection cannot consume that scope. The public receipt
must expose `external_writes_performed`; provider or private-receipt failure is
`partial`, never a false success, and cannot discard the captured inbox event.
