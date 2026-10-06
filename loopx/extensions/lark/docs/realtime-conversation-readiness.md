# Native realtime conversation readiness

LoopX's optional Lark provider receives messages through a long-lived
`im.message.receive_v1` consumer. The Chat server owns that listener; a Goal's
periodic heartbeat is a separate work trigger. Installation or a healthy
listener alone does not qualify an interactive conversation.

For separate assistant and steward Apps, follow the
[owner-controlled Bot handoff](owner-controlled-bots.md). App setup, live product
qualification and optional state inheritance remain separate outcomes.

## Existing path and its limits

Configure a verified Bot profile and the intended group/Agent connections in
the existing workspace settings. A manager connection and worker Topics share
the same bundled transport. Exact worker Topics select their bound Agent;
ambiguous group messages must not guess a recipient from their text. App
credentials remain in the provider's local profile, outside Goal state.

The existing runtime checks a provider-ready marker, reuses the bound Chat
Session, persists the source in the Inbox, and verifies the reply before ACK.
Received-message reactions, terminal replies, confirmation cards and
listener health are distinct observations. A private inbox configuration is
published with the shared atomic private-JSON writer: concurrent callers use
independent owner-private temporaries, and a failed publication preserves the
previous configuration and removes its temporary.

For already-enabled Lark listeners, independent conversations now run through
up to four transport handlers. The dispatch queue counts at most 64 messages
including active handlers; provider frame and pipe buffers remain provider-owned. A source stays FIFO across binding revisions; sources bound
to the same Session also share a scheduling fence. One busy source does not
consume workers by waiting on a Session lock. Capacity applies backpressure
instead of dropping messages or growing an unbounded executor queue. Actual
processing reads fresh bindings and still uses Core admission/budget rules.
These limits bound transport work, not permission to run four models.

Normal provider rotation drains observed messages before releasing the App
consumer lease. Stop or reader failure discards waiting transient lines without
Inbox ACK; active handlers retain ownership until they settle. This buffer is
not a durable queue or an RPO guarantee: only the existing Inbox/Chat path owns
persisted source/admission receipts. A handler failure surfaces through existing
service recovery, without retrying its model call in the transport helper.
Inactive profiles still start no consumer or dispatch workers.

Same-conversation handlers remain serialized through terminal answer/reply.
Core Session queue/steering support therefore does not yet prove that a busy
Lark conversation can admit a correction promptly. The shipped group and Topic
setup is also not qualification of private Bot DM onboarding, ordinary non-Goal
Codex chat, token streaming or attachment delivery to a model. Deterministic
native-path checks use real Inbox files and reply readback with provider/model
doubles; they do not qualify an installed live provider or host.

The dispatch helper stays in the Python Lark extension because it only schedules
that bundled provider's transient stream and invokes its existing handler. It
owns no shared domain policy, persistence, approvals or model execution. Shared
conversation/admission/progress changes belong to the existing typed Core owner.

## Product boundary

A Bot is the realtime entry to ordinary project chat, a selected existing Agent,
or an explicitly selected steward. The steward owns long-term commitments,
coordination and acceptance; ordinary chat should not inherit that objective or
portfolio access. Reuse Session/Turn and host capabilities, while preserving
recipient purpose, audience and workspace grants. The current Goal/manager
setup is the existing supported path, not proof that plain project chat is ready.

## Interactive experience to retain

[Claude-to-IM](https://github.com/op7418/Claude-to-IM) is a useful reference for
channel ergonomics. Retain persistent conversation continuity, visible
processing feedback, bounded streaming presentation, explicit permission
answers, media input and actionable recovery. Qualify each host adapter:
support in an IM adapter does not establish incremental model output or tool
approval callbacks in every SDK/runtime.

Reuse Core Chat Session/Turn, the existing event/progress projection, attachment
handling and canonical operation/proposal receipts. Lark presents those facts
and authenticated user responses; it must not add a second task ledger,
approval store, scheduler or model-execution authority. Missing evidence is not
permission to restart a request in a fresh model thread, elevate host policy
or acknowledge work as completed.

Markdown post presentation uses the existing shared inbox formatter. At a
closing strong-emphasis boundary, trailing punctuation can move outside the
bold span when followed by a word or non-ASCII symbol, including a fullwidth
separator. This preserves visible text and the canonical answer; code, link
destinations and ambiguous delimiter runs remain opaque. Preview and readback
verify the same normalized post. This is provider compatibility, not a new
Markdown parser or a change to Session, grants or result authority. Live
rendering still needs the actual provider journey; formatter tests alone do
not qualify it.

Private DM result reconciliation uses the existing Chat store's terminal-state
owner, including `timed_out`, for ordinary replies and commission results.
Timeouts return an explicit failure and the original conversation's recovery
controls. Provider readback still gates delivery: restarting the transport or
redelivering the source reconciles the saved attempt without another send or
model execution. Synthetic host/provider checks cover idle/hard DM timeouts
and an injected commission timeout; a live-provider recovery drill remains
part of the switch gate below.

### Private-message progress

Private project and steward conversations present the original Turn's persisted
`agent.phase` and filtered `answer.delta` events in one Bot-owned Markdown post.
The existing delivery pump coalesces changes with a minimum two-second interval;
provider latency can lengthen that interval. No timer invents activity. Thinking
text, command arguments and tool output are excluded; only coarse observed work
and the visible answer are presented. A draft shows at most the latest 6,000
characters and clearly says it is provisional.

The canonical terminal answer replaces that post without the draft limit. Stop,
timeout and failure replace the partial answer with the actual terminal outcome.
A commission's first native Turn uses the same presenter and original audience.
Updates freshly verify the App, source and target chat, preview the exact post,
and read it back before delivery/ACK. A lost edit acknowledgement is reconciled
on the same message ID. Event cursors and provider attempts stay in the existing
private delivery journal; they do not start another model Turn or listener.

This Python code is provider presentation: Core still owns the Session, Turn,
event stream, grants and result. It adds no shared state protocol or execution
authority. Synthetic native-host/provider tests cover incremental presentation,
coalescing, restart/replay, lost acknowledgement, exact stop, cleanup recovery,
full final answers and App/audience isolation. Live installed-provider streaming
and its latency remain separate qualification steps under S5/S10 and RFC A22/A23.

## Qualification before switching

The acceptance owner is the steward RFC's
[operational contract](../../../../docs/architecture/rfcs/capable-manager-semantic-handoff-v0.md#10-operational-contract),
under roadmap S1/S5/S10. Exercise a pinned installed package through the real
provider and host, with synthetic content:

- A first request and follow-up retain the exact intended Session and audience.
- While one model request is blocked, a second request, a correction and a
  distinct role's request receive truthful, bounded admission/queue feedback.
- A visible progress/partial card remains provisional; only the terminal
  canonical result qualifies completion. Lost card readback retries delivery,
  without another model request or replaying an effect.
- An image/file reaches the authorized host with bounded private lifetime;
  unsupported media is explicitly unavailable, rather than silently omitted.
- A permission decision binds the exact operation, audience, expiry and
  approving user. An unverified callback never broadens execution permission.
- Reconnect/restart, duplicate events, unavailable provider and stop preserve
  source identity and accepted work, with no duplicate answer or late dispatch.

Report pass, failure and untested separately for transport, admission, host
execution and user-visible delivery. Synthetic fixtures do not certify live
adoption. Do not transfer old sessions, credentials, bindings or permissions.
An existing bridge stays a separate operator-controlled service until the
replacement has passed its actual journey; setup must not silently start a
second client for the same App.

The [official Lark SDK](https://github.com/larksuite/node-sdk/blob/main/README.md#subscribing-to-events-using-long-connection-mode)
describes WebSocket events, the provider processing deadline and non-broadcast
delivery across clients. A provider receive ACK, an Inbox ACK and a completed
model Turn are separate lifecycle facts. Qualify the selected SDK/provider
version; the transport deadline is not a promise of model response latency.
