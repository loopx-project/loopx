# Lark event inbox

LoopX can consume Lark feedback without keeping an agent process alive. The
integration deliberately separates collection from interpretation:

```text
Lark event stream
  -> host-managed collector
  -> .loopx/inbox/<channel>/*.json
  -> loopx lark-inbox drain
  -> loopx lark-inbox processing (optional reaction lifecycle)
  -> domain agent writes a todo, vision correction, artifact update, or rationale
  -> direct bot question: loopx lark-inbox reply (optional, configured sender only)
  -> unaddressed material: loopx lark-inbox material-review (effect/no-follow-up)
  -> loopx lark-inbox ack --message-id ... --execute
```

The collector is host infrastructure. LoopX can validate a local-private
collector config, preview or explicitly install a macOS `launchd` / Linux
`systemd` user service, and report supervisor plus event-bus health. The
installed service runs a small LoopX collector runtime around
`lark-cli --profile <configured-profile> event consume` with a bounded timeout,
so stdin EOF under a supervisor cannot terminate an otherwise unbounded
consumer. When the official npm package exposes a Node wrapper, LoopX records
absolute paths for both Node and the wrapper so launchd does not depend on an
interactive-shell PATH. It filters before persistence and writes one compact
event per Lark `event_id`/`message_id`. Direct mentions are persisted
immediately. For a message without a direct mention, the runtime reads back the
current message and its direct parent, then marks it actionable only when the
parent sender is the app id of the configured profile. A reply to a person,
another app, or an unverifiable parent remains captured but does not wake the
agent. The agent does not need to keep a websocket open.

When Lark inbox and the same registered agent's Reward Memory are both enabled,
a non-empty registry-routed drain can also return an advisory
`reward_memory_feedback_review` hint. It asks the agent to review reusable
feedback and preview the existing scoped `reward-memory ingest-event` command;
it does not ingest chat, grant authority, or change settlement/ACK requirements.
This explicit path does not require `automatic_ingest=true`. See the
[Reward Memory inbox workflow](../../../capabilities/reward_memory/README.md#inbox-feedback-review-explicit-ingestion)
for eligibility, source verification, write/readback and default-off behavior.

### Optional turn-start Agent reading hook

Collector health does not prove that an Agent consumes every collected route.
Verify the canonical project registry's Agent inbox pointer and the normal
`lark-inbox drain --goal-id ... --agent-id ...` path together. For multiple
configured chats, bind the collector configuration rather than one child inbox.
An empty child inbox is not evidence that other routes have no updates.

Connecting an async Goal Topic now fails with `agent_inbox_binding_conflict`
before provider calls when it would replace a different enabled Agent inbox
(including an inherited Goal inbox). Reconnecting the same inbox is unchanged.
Reconcile route ownership explicitly through the canonical project registry;
do not clear a multi-route binding just to make topic setup pass. This guard
prevents silent replacement; it does not automatically merge Topic routes.

Before continuing fallback work, review fresh dependency messages and write any
resolved wait or priority change to the existing Todo/vision state. Settle the
message after that durable effect; collection alone is not interpretation or
permission to reply, deploy, or run work.

Realtime collection is the preferred ingress, but a long-running Agent may also
need a bounded provider-history tail at the beginning of every LoopX turn. The
collector config can opt into `turn_start_sync`. This is not a background-only
sync: it is a pre-decision capability hook with the following ordering:

```text
turn-start hook
  -> read one bounded provider page per route
  -> commit and read back owner-private inbox events and cursor
  -> ACK each newly read pending human message with one idempotent reaction
  -> recompute quota inbox urgency in the same CLI invocation
  -> agent_read_required=true when pending messages were newly read by the hook
  -> selected inbox lane drains private message content before ordinary work
  -> Agent chooses steering / Goal replan / context capture /
     continue-current-work / no-follow-up
  -> durable effect or no-follow-up receipt -> ACK
```

Core owns the provider-neutral hook registration, output budget, allowed
owner-private write scopes, the narrow `provider_message_reaction` external
write scope, failure isolation, and `agent_read_required` contract. A hook that
can require Agent reading must also register one bounded public-safe
`required_read`; the generic kernel validates and deduplicates it, then the live
decision mirrors it into both interaction channels with `ordering=before_work`.
Fresh ordinary material notifies without replacing the selected work lane;
durable material left unsettled preempts on the following turn, while direct
questions and verified replies retain immediate reply-lane precedence.
The Lark extension owns that drain descriptor, history pagination,
provider-envelope validation, private cursors, and inbox readback. The CLI
composition root runs the hook before status/quota projection. Raw content
remains only in the local inbox and appears to the Agent only through the
registered drain command; it never enters the public Goal registry, hook
receipt, or quota packet.

The distinction between `empty`, `provider_contract_error`, permission failure,
and provider unavailability is mandatory. A success envelope whose message list
does not match the declared provider schema fails closed and cannot be treated
as an empty inbox. Hook failure is isolated from ordinary Goal state, but it is
visible in `turn_start_capability_hook_dispatch` and does not claim that Agent
reading occurred.

The feature is default-off. Enable it only with `configured_chat_all` and
`material_review.enabled=true` on every route, so every newly accepted message
enters an Agent semantic-triage lane rather than being synchronized and ignored:

```json
{
  "schema_version": "lark_event_collector_config_v1",
  "enabled": true,
  "service_name": "loopx-project-context",
  "identity": "bot",
  "profile": "project-context-bot",
  "supervisor": "systemd",
  "consume_timeout": "30m",
  "turn_start_sync": {
    "enabled": true,
    "initial_lookback_seconds": 900,
    "overlap_seconds": 5,
    "page_size": 50
  },
  "routes": [
    {
      "route_key": "requirements-a",
      "chat_id": "oc_<local-private-chat-id>",
      "event_inbox_config": ".loopx/config/lark/requirements-a.json"
    }
  ]
}
```

Each completed poll opens a new forward window from the previous end with a
small overlap. Inbox `message_id` deduplication makes the overlap replay-safe.
When a page reports `has_more`, later turns resume the same private page token
before opening a new window. The initial lookback is bounded to seven days,
the overlap to five minutes, and each route reads at most one 50-message page
per turn. Cursor and single-flight lock identity combine the public-safe route
key with a digest of the configured profile, chat, inbox config, inbox path,
and capture scope. Two Agent-scoped collectors may therefore reuse a semantic
route key without sharing progress or permanently rejecting each other's
source binding, while duplicate registrations for the same source still share
one single-flight boundary.

Use `addressed_only` only when direct bot mentions are the entire feedback
contract. A review or collaboration inbox that accepts non-mention replies to
bot messages should use `configured_chat_all`: the host collector filters by
its local-private chat id, persists every message from that chat, and verifies
the reply relation through message readback before scheduling a reply. Full-chat
capture is not full-chat activation; unrelated conversation remains available
to domain interpretation without being treated as addressed to the bot.

Goal Channel connections do not treat a spawned `lark-cli` child as listener
readiness. The runtime waits for the provider event bus `ready` marker (or a
real typed event) before projecting `listening`; startup without that handshake
remains non-ready and retryable. Multi-Agent onboarding creates an
Agent-labelled Topic for each route. Users send requests inside the matching
Topic. A group-level message with more than one eligible Agent route is
deliberately rejected as ambiguous instead of guessing an Agent from prose.

For a periodic-report request, semantic activation belongs to the Agent. After
reading an exact item, the Agent calls `loopx periodic-report request` with its
`message_id`. The Lark adapter validates binding and addressing evidence only;
it never classifies the text or searches the inbox for weekly-report strings.

## Activate the provider

Install and explicitly activate the bundled provider once in the LoopX runtime
used by the project:

```bash
loopx extension install --bundled loopx-lark --execute --format json
```

Configured `lark-inbox` commands fail closed when `loopx-lark` is absent,
disabled, or no longer matches its doctor-verified revision. Each operation
also requires its manifest permission: inbox read/write, reply send, or
collector management. `extension upgrade` and `extension rollback` probe the
candidate revision before switching it. A goal with no Lark inbox pointer still
returns the existing quiet disabled drain projection and does not require the
extension, so projects that do not use Lark remain unaffected.

Quota and Turn planning also resolve `lark.inbox.read` before the extension
opens the local-private profile/chat config. A missing, disabled, or stale
extension yields an unavailable urgency projection, does not read that config,
and cannot schedule a Lark reply lane. The compatibility CLI performs this
composition internally; agents do not pass a provider, profile, or alias.

The collector service is a separate host lifecycle. Disabling or switching the
extension blocks its next `collector-run` start but does not signal a process
that is already consuming events. Stop or restart the configured launchd or
systemd user service when disabling, upgrading, or rolling back the provider.

## Local-private configuration

The inbox is opt-in. Create a local-private generic Lark inbox config:

```json
{
  "schema_version": "lark_event_inbox_config_v0",
  "enabled": true,
  "inbox_dir": ".loopx/inbox/team-feedback",
  "capture_scope": "configured_chat_all",
  "material_review": {
    "enabled": true,
    "drain_limit": 20
  }
}
```

`inbox_dir` must stay under `.loopx/inbox`. Destination ids, member ids,
profile names, raw provider payloads, and credentials stay in local-private
configuration or host state and must not enter public LoopX packets.

`capture_scope` defaults to `addressed_only` for compatibility. Drain output
reports `thread_complete=false` and a coverage warning for that mode. For
`configured_chat_all`, the collector's jq filter should select the configured
chat only; do not add a content-level `@bot` predicate. A Goal Topic root is
presentation and reply context, not an additional ingress filter for
`configured_chat_all`: new topics and replies in the same configured chat must
remain visible to the bound Agent. When more than one chat-wide Goal route is
eligible for the same Bot target, routing fails closed instead of choosing one
by iteration order.

`material_review` is an independent, default-off scheduling boundary. It
requires `configured_chat_all`; when enabled, captured messages and normalized
attachments that do not require a Bot reply produce `material_review_due`.
`drain_limit` is bounded to 1–100 and defaults to 20. Direct questions,
mentions, and verified Bot replies continue to use `reply_due` and take
precedence, so material review never grants outbound reply authority.

Optional source-thread replies are a separate, default-off boundary. Bind an
explicit non-default bot profile to the same local-private chat. An
`addressed_only` inbox may reply to the exact captured source message, but it
remains `thread_complete=false` and cannot discover unmentioned follow-up
messages; use `configured_chat_all` for complete collaboration threads:

```json
{
  "schema_version": "lark_event_inbox_config_v0",
  "enabled": true,
  "inbox_dir": ".loopx/inbox/team-feedback",
  "capture_scope": "configured_chat_all",
  "reply": {
    "enabled": true,
    "sender_profile": "project-review-bot",
    "sender_identity": "bot",
    "bot_display_name": "Project Review Bot",
    "chat_id": "oc_<local-private-chat-id>",
    "processing_reaction_emoji": "OnIt"
  }
}
```

For every reply-enabled Inbox, a missing `reply.received_reaction_emoji`
defaults to `Get`. Set it explicitly to the empty string to disable this
provider write. The reaction belongs to the same explicit sender boundary as
source-thread replies. The Agent's turn-start hook creates it after reading and
confirming a still-pending human message; the synchronous manager route creates
it immediately before invoking the manager. Realtime collection alone persists
events without reacting.
The receipt therefore means "read into the Agent processing chain"; it does not
mean "collector stored the event", "the Bot was mentioned", "a reply is due",
or "processing completed". Mention, reply, question, and material-review
classification remain independent scheduling and response decisions.

The hook records its first read in owner-private state independently of this
optional provider write. Thus a message captured earlier by the realtime
collector still requires Agent reading even when reactions are explicitly
disabled. Failed reactions are retried from this durable pending-read set while
the message remains unsettled, including after the bounded history cursor has
moved beyond the message timestamp. Provider failure increments compact
failure accounting but does not discard the Inbox event or grant execution
authority. Replay uses one aggregate bounded attempt budget per turn-start
dispatch. A collector-scoped private cursor rotates route priority across
dispatches, while each route keeps its own private round-robin message cursor.
The public receipt exposes only attempt and deferred counts, never cursor or
message identities. Messages with a durable received/processing receipt are
skipped without another provider call. A new reply in an old topic has a new
provider message identity, so the forward history tail captures it independently
of topic age and acknowledgement backlog.

`reply.processing_reaction_emoji` is optional and requires a distinct
received reaction. The default `Get` satisfies that requirement; when the read
acknowledgement is explicitly disabled, processing reaction must also be
disabled. When both are configured, the host should run
`lark-inbox processing` immediately before interpreting an actionable item.
`reply.received_reaction_policy` selects `transient` (the generic Inbox default)
or `retain`. With `transient`, LoopX first adds the processing reaction and then
removes the received reaction; a verified source-thread reply removes remaining
lifecycle reactions. With `retain`, the received reaction remains visible during
processing and after the answer; completion removes only processing reactions.
Generated manager routes default to `retain`, so their `Get` receipt does not
disappear when the answer arrives. Bound Goal routes keep `transient` behavior.
Retention uses the existing private reaction ledger across restart and replay;
it neither recreates reactions on historical messages nor means work completed.
If the provider cannot delete a transient reaction, the operation fails with a
retryable cleanup status instead of claiming completion.

Reaction ids are stored only in an owner-private receipt ledger under the
configured inbox. Each message transition is serialized with a private
per-message lock. A prepared/created operation receipt fences provider creation
before and after the external effect: a reaction whose normal receipt could not
be persisted is recovered from the known reaction id without another create;
an outcome that became uncertain before its id was durably recorded blocks
replay instead of risking a duplicate. LoopX deletes only reaction ids returned
by writes made through the configured bot profile; it never deletes another
participant's reaction by emoji type. Malformed private state fails closed.

The reply path never uses the machine default profile. Before any send it
verifies that the named profile resolves to the expected bot and that the bot
can read the configured chat. A profile/app mismatch fails with
`lark_inbox_reply_sender_identity_mismatch`; a profile that cannot access the
configured chat fails with
`lark_inbox_reply_sender_not_in_configured_chat`. Neither failure falls back
to another app. Inbox and Goal Channel replies retry only the provider's
explicit transient `verify_failed` Bot identity state, up to three checks.
Command failures, malformed identity responses, and configured Bot name
mismatches fail immediately; membership, provider preview, idempotency, send,
and readback gates remain unchanged. Public results contain only compact
status/receipt fields, not the profile, chat id, message id, reply text, or
provider payload.

## Host collector lifecycle

In Git projects, keep the collector config ignored and untracked. A non-Git
project may keep it only below `.loopx/config`; parent Git boundaries and paths
outside that private root remain rejected. The config references the generic
inbox config but owns host-only details such as the chat id and supervisor:

```json
{
  "schema_version": "lark_event_collector_config_v1",
  "enabled": true,
  "service_name": "loopx-lark-feedback",
  "event_key": "im.message.receive_v1",
  "identity": "bot",
  "profile": "project-review-bot",
  "supervisor": "launchd",
  "consume_timeout": "30m",
  "lark_cli_bin": "lark-cli",
  "routes": [
    {
      "route_key": "requirements-a",
      "chat_id": "oc_<local-private-chat-id-a>",
      "event_inbox_config": ".loopx/config/lark/requirements-a.json"
    },
    {
      "route_key": "requirements-b",
      "chat_id": "oc_<local-private-chat-id-b>",
      "event_inbox_config": ".loopx/config/lark/requirements-b.json"
    }
  ]
}
```

The packaged lifecycle accepts only `im.message.receive_v1`, bot identity, an
isolated `loopx-` service name, and `configured_chat_all`. Config v1 requires a
unique lowercase public-safe `route_key` for each route, consumes one
profile-bound event stream, and routes each configured chat into a distinct
inbox config and inbox path. Missing, unsafe, or duplicate route keys, duplicate
chat routes, shared inbox paths, reply chat mismatches, and route profile
divergence fail closed. Each accepted event persists the configured route key,
so aggregate drain gives the Agent a stable requirement-context identity without
exposing a private chat id. A missing or mismatched persisted route key also
fails closed instead of silently reclassifying an older message. Each inbox therefore
retains independent pending/processed state and source-context reply placement
while one Bot can serve several chats without competing consumers. The v0
single-chat shape remains accepted and is normalized to one route. Plan,
install, run, and status output expose only route and health counts; they never
return profile values, chat ids, local paths, generated jq, or credentials.

An enabled collector must bind an explicit non-default Lark CLI profile. When
`profile` is omitted, LoopX may reuse the shared enabled inbox reply
`sender_profile`; every routed reply profile must resolve to that same value.
When both are present they must match. The generated service
passes the profile-bound collector config to the LoopX runtime, which places
`--profile` before both `event consume` and message readback calls. Collection,
reply-target verification, and optional replies therefore cannot silently use
different app identities. Public plan/status packets expose only whether a
profile is bound and where the binding came from, never its value.
When the CLI uses a custom `--runtime-root`, the generated service records that
same root before `lark-inbox collector-run`; a supervisor restart therefore
resolves the same extension activation state that was validated at install.

```bash
loopx lark-inbox collector-plan \
  --project . \
  --config .loopx/config/lark/collector.json

# Preview first; this writes nothing and starts no process.
loopx lark-inbox collector-install \
  --project . \
  --config .loopx/config/lark/collector.json

# Explicitly write the user service and start/restart it.
loopx lark-inbox collector-install \
  --project . \
  --config .loopx/config/lark/collector.json \
  --execute

# Read-only supervisor, event-bus, and real-event evidence check.
loopx lark-inbox collector-status \
  --project . \
  --config .loopx/config/lark/collector.json \
  --probe-event-bus
```

Missing `lark-cli` produces a non-blocking install hint. Reply-target
verification also requires the configured bot to read messages in the selected
chat. Bot-identity group-history catch-up requires the application scopes
`im:message.group_msg` and `im:message.group_msg.include_bot:read`; the latter
keeps Bot-authored messages in the provider result. Before inbox ingestion,
realtime collection and bounded history sync both compare a provider-typed
`app` sender with the exact app identity verified for the configured profile.
An exact self match is counted and skipped; other apps and unresolved
identities remain visible so an identity lookup failure cannot silently lose a
message. When the Bot list-messages history path reports provider error `230027`,
LoopX must surface both scopes
and an official API page bound to the selected App id. The operator enables
the application scopes and publishes a new App
version; this is not a user OAuth login. These requirements belong to the
list-messages history capability. Exact message-by-id hydration and realtime
event delivery remain separate capabilities and must keep their own failure
status and permission evidence. LoopX does not authenticate a bot,
copy app credentials, silently grant provider permissions, or silently install
packages. Service installation is a
local host write and therefore requires explicit `--execute`. Status separates
`healthy` from `real_event_evidence_present`: a running subscriber can be
healthy before the first message, while acceptance of a real integration still
requires one post-install event to appear in the inbox.

Register one inbox or the v1 routed collector as the Agent-owned goal boundary.
The latter keeps one authority and one Agent lane while exposing aggregate,
content-free urgency across all configured chats:

```bash
loopx configure-goal \
  --goal-id <goal-id> \
  --lark-event-inbox-agent-id <context-assistant-agent-id> \
  --lark-event-inbox-config .loopx/config/lark/collector.json

# Review the preview, then apply explicitly.
loopx configure-goal \
  --goal-id <goal-id> \
  --lark-event-inbox-agent-id <context-assistant-agent-id> \
  --lark-event-inbox-config .loopx/config/lark/collector.json \
  --execute

# Drain all configured chats through the same Agent lane. Each item retains
# route-specific source-context reply guidance; message-scoped follow-up
# commands resolve exactly one isolated inbox or fail closed.
loopx lark-inbox drain \
  --goal-id <goal-id> \
  --agent-id <context-assistant-agent-id>
```

The configuration catalog exposes this optional capability on demand. Quota
projects `enabled`, `config_pointer_registered`, a local control command, and a
content-free urgency summary; it never projects the private path, message ids,
senders, or message bodies. The summary includes
pending/direct-question/direct-mention/verified-bot-reply counts, routed inbox
counts, and the oldest pending age. It does not expose route chat ids or profile
names. A configured direct mention or verified reply to a message authored by
the configured bot becomes a high-priority `lark_event_inbox` work lane
before ordinary monitor or advancement work. Generated heartbeat bodies run the
actual goal-boundary `drain_command`;
`loopx --registry <invoked-registry> lark-inbox drain --goal-id <goal-id>`
follows a shared registry's `source_registry` to the canonical project before
resolving the ignored config. It therefore remains correct from linked or
independent worktrees without binding control state to `--project .`. A disabled or empty inbox
is a quiet zero-spend path, so projects without Lark keep the default behavior.

## Drain and acknowledge

```bash
loopx lark-inbox drain \
  --project . \
  --config .loopx/config/lark/event-inbox.json

loopx lark-inbox processing \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx

# Execute only after reviewing the preview.
loopx lark-inbox processing \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx \
  --execute

loopx lark-inbox ack \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx \
  --execute
```

Drain is read-only and returns bounded local-private message content. A message
must be acknowledged only after its effect is written back. Duplicate event
files collapse by `message_id`; repeated acknowledgement is idempotent.
`processing` is also idempotent: retries reuse the recorded processing
reaction and finish any pending received-reaction cleanup without creating
another processing reaction.

For unaddressed material, use the dedicated settlement command rather than a
reply. It accepts either an event-bound committed external effect receipt or an
explicit no-follow-up rationale. The latter becomes a deterministic
`no_follow_up` effect receipt; repeated execution returns `already_settled`
without duplicating the ACK. Receipt replay/conflict checks, the ledger commit,
and the processed-message ACK share one per-inbox lock. The ledger remains
ledger-first, so a retry after interruption repairs an ACK that was not yet
written without losing a concurrent receipt or processed-message update.

```bash
loopx lark-inbox material-review \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx \
  --no-follow-up 'Informational material already captured.'

loopx lark-inbox material-review \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx \
  --no-follow-up 'Informational material already captured.' \
  --execute
```

### Steward groups without mentions

In App **Settings → Lark → edit the steward connection → When to respond**,
select **Respond to group members without @** to admit new human messages in
that one connected group. All group members can start a conversation in this
mode. The Lark application must have permission to receive all group messages;
a local switch cannot grant that provider permission. Save the connection and
reopen it to verify the setting; select **Only when mentioned or replied to**
to disable it. Existing connections default to that addressed-only mode.

The persisted connection field is `routing.turn_trigger` (`addressed` or
`human_messages`), exposed by the existing connection API. Capture scope remains
independent. The shared TypeScript conversation admission rule consumes verified
provider sender/addressing evidence; it does not inspect message keywords.
Historical backfill and bot messages do not start turns in the no-mention mode;
unknown senders without addressing stay context-only. Existing worker Topics
keep route priority. The same durable inbox/effect/reply path prevents duplicate
processing. Reception does not grant host tools, evidence access, delegation or
protected operations; the external-audience runtime profile is unchanged.

Urgency classification stays local. Under `configured_chat_all`, provider-native
mention evidence is normalized into a compact `addressed_to_bot` flag before the
event is persisted. Only that typed flag or a provider-verified direct reply can
produce Bot reply urgency; bounded question signals distinguish a direct question
only after addressing is proven. A question elsewhere in the group, an `@` mention
of another member, Bot-name prose, or a reply to a human remains material review and
does not become `reply_due`. Legacy persisted events without typed addressing also
fail closed to material review. The agent still drains and interprets the source
event before deciding the durable effect or reply; the summary is a scheduling
signal, not semantic authority.

For a direct question, explicit bot mention, or verified reply to the configured
bot, write the requested durable effect first, preview one concise reply,
execute it, require readback, and only then ACK. New Goal Topic inbox configs use
`reply.placement_policy=source_context`: a top-level chat request receives a new
top-level chat response, while an event already inside a topic receives a reply
inside that source topic. Existing configs without the field retain the legacy
`source_thread` policy. `reply.editorial_style=bullet_points_preferred` projects
an operator hint for structured replies; the command preserves line breaks.

Manager answers and delegated conclusions use a rich `post` with one Markdown
node, preserving paragraphs, lists and code indentation. The extension supplies
exact JSON to the existing CLI transport: it does not fetch Markdown images or
rewrite the source text. Preview and readback verify the post type and content;
plain-text lookalikes do not count as rich delivery. The frontend continues to
render the same stored Markdown through its existing message renderer.

Ordinary inbox CLI replies/notifications retain their text behavior. Structured
mentions keep the existing identity-verified text path. If the provider preview
exceeds the 30 KB rich-post request limit, the manager falls back **before any
send** to the existing 150 KB text transport and reports `format_fallback` as
`post_size_limit`; it does not truncate the answer. Format is bound into rich
reply idempotency keys. This changes presentation only, not conversation scope,
reply placement, authorization or ACK semantics.

```bash
loopx lark-inbox reply \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx \
  --text '已记录并修正。'

loopx lark-inbox reply \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx \
  --text '已记录并修正。' \
  --execute
```

The command uses an idempotency key derived from the source message, resolved
placement, and reply text, then reads the created message back through the same
configured profile. Lifecycle reactions are removed only after that readback
succeeds. A sent reply whose reaction cleanup fails returns
`sent_verified_cleanup_pending`; retry `lark-inbox reaction-complete` before
acknowledging the source:

```bash
loopx lark-inbox reaction-complete \
  --project . \
  --config .loopx/config/lark/event-inbox.json \
  --message-id om_xxx \
  --execute
```

Ordinary chatter remains a no-reply path;
enabling this capability does not grant reviewer-notification or other
outbound authority.

For text replies containing Lark `<at user_id="...">...</at>` mentions, provider
readback may replace the markup with tokens such as `@_user_1` or render the
visible body as `@Display Name` while retaining the token in structured mention
metadata. Verification therefore compares the normalized visible-text template
and requires every mention to resolve to the identity requested at send time.
A missing, extra, ambiguous, or differently resolved mention remains
`sent_unverified`; display-name or raw-markup similarity alone is not accepted.
Notification-style literal `@Name` text is rejected before any provider call;
resolve the exact chat member and supply a structured `<at ...>` node. The same
outbound verifier is used by top-level reviewer notifications, so reply and
proactive-send paths cannot disagree about what constitutes a delivered
mention. Both paths perform a provider dry-run before sending and verify the
created message rather than treating its message id as delivery proof.

Use the configured proactive-send surface instead of a raw provider command:

```bash
loopx lark-inbox send \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --route-key project-feedback \
  --text '<at open_id="ou_example">Example Reviewer</at> please review' \
  --provider-preflight

loopx lark-inbox send \
  --goal-id <goal-id> \
  --agent-id <agent-id> \
  --route-key project-feedback \
  --text '<at open_id="ou_example">Example Reviewer</at> please review' \
  --execute
```

`route_key` selects one isolated requirement/chat binding under a multi-chat
collector and fails closed when missing or unknown. The top-level send neither
requires nor fabricates a source message, so its verified placement is always
`chat_root`; source-message replies continue to preserve source-context
placement and reaction cleanup.

## Bounded history reconciliation

Real-time event subscriptions do not backfill messages sent before a collector
started, and an earlier `addressed_only` collector will already have omitted
unaddressed replies. Fetch the bounded source conversation with the Lark CLI,
project each message into `lark_event_inbox_event_v0`, then pipe the JSON array
or NDJSON into the generic importer:

```bash
<bounded-lark-message-export> \
  | loopx lark-inbox ingest \
      --project . \
      --config .loopx/config/lark/event-inbox.json \
      --execute
```

Ingest validates ids and schema, deduplicates by `message_id`, writes only to
the configured local-private inbox, and returns counts rather than message
content. It does not acknowledge imported messages; the domain agent must still
write each actionable effect before ACK. Provider-backed realtime and history
ingress also report `self_message_skipped_count`; raw generic imports cannot
claim this verification because they do not own the configured Bot identity.

Reviewer notification dedupe uses durable lifecycle receipts first, then exact
PR-link evidence in the persisted `configured_chat_all` inbox, and finally a
bounded user-identity search of the configured chat. Missing `search:message`
permission degrades to the two persisted sources and does not create a user
gate; other provider read failures remain blockers because absence cannot be
established safely.

## Domain bindings

The inbox itself does not know why a message matters. A domain capability binds
the generic event stream to its own interpretation and writeback rules. For
example, issue-fix can turn reviewer-group messages into PR-description
updates, Kanban context, vision corrections, or explicit no-follow-up
rationale. Other domains can consume the same inbox without adopting any
issue-fix schema or lifecycle.

For issue-fix, outbound GitHub reviewer requests and outbound Lark
notifications remain independent obligations. The Lark inbox is only the
inbound feedback path.
