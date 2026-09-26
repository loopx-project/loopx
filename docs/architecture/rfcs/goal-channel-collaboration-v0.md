# RFC: Goal Channel Collaboration v0

- Status: Accepted
- Supersedes / closes: none
- Scope: provider-backed collaboration channels for one LoopX goal
- Decision type: product architecture and staged integration contract

## Summary

This RFC introduces **Goal Channel** as the LoopX-owned abstraction for an
external collaboration channel bound to exactly one goal. A channel may be a
Lark/Feishu group, Slack channel or thread, GitHub issue, Linear thread, or
another provider surface. The provider owns delivery and UI primitives; LoopX
owns goal state, todos, human gates, quota, evidence, receipts, and accepted
state transitions.

The first provider target is Lark/Feishu:

- create or reuse one group chat for a goal;
- create or reuse a Lark Base Kanban projection;
- pin a compact control message and Kanban link in the group;
- send bounded human-gate notifications;
- sync accepted LoopX state back to the Kanban projection.

The channel is not the source of truth. It is the visible collaboration entry
point and feedback surface for a LoopX goal.

## Problem

LoopX already has durable state and Lark-specific pieces:

- `lark-kanban` projects LoopX todos and status into Lark Base;
- Lark notification code paths already prove send, readback, idempotency, and
  profile checks for narrower domains.

These pieces do not yet compose into the product shape users expect from a
Claude Tag-like workflow:

1. Mention a bot in a collaboration surface.
2. Get or create an isolated collaboration channel for that objective.
3. See progress and gates in the same place.
4. Receive human-gate prompts without leaving the collaboration surface.
5. Let LoopX keep the authoritative goal, todo, gate, and evidence state.

Without a first-class Goal Channel abstraction, a Lark group, Base board,
message thread, pinned status, and notification receipts can drift apart.

## Goals

- Define a provider-neutral LoopX concept for "the external collaboration
  channel for this goal".
- Keep Lark group chat, Kanban, pinned messages, and gate notifications bound
  to one `goal_id`.
- Preserve LoopX as the only writer of canonical goal, todo, gate, evidence,
  and quota state.
- Make provider writes explicit, previewable, idempotent, and readback
  verified.
- Let humans see and answer gate prompts in the channel without granting the
  channel broad write authority.
- Allow later provider adapters such as Slack, GitHub, or Linear without
  renaming the core concept.

## Non-Goals

- Replacing `lark-kanban`; Goal Channel composes it.
- Making Lark, Slack, or any external tool the source of truth.
- Shipping a LoopX-managed global Lark app in the open-source CLI.
- Requiring one fixed bot identity across all users or tenants.
- Treating arbitrary chat text as an accepted state transition.
- Copying raw chat history, private message ids, local paths, credentials, or
  raw provider payloads into public packets.
- Solving full remote runner orchestration in this RFC.

## Naming

Use **Goal Channel** for the core abstraction.

Avoid `room` as the primary name. A group chat may be one implementation
detail, but a Goal Channel can contain chat, pinned status, Kanban,
notification receipts, and provider-specific metadata.

Suggested command surface:

```bash
loopx goal-channel setup --provider lark --goal-id <goal-id>
loopx goal-channel target add --name <target> --provider lark ...
loopx goal-channel setup --goal-id <goal-id> --target <target>
loopx goal-channel attach --target <target> --goal-id <goal-a> --goal-id <goal-b>
loopx goal-channel configure --goal-id <goal-id> --auto-notify-human-gates
loopx goal-channel doctor --goal-id <goal-id>
loopx goal-channel sync --goal-id <goal-id>
loopx goal-channel notify-gate --goal-id <goal-id>
loopx goal-channel runtime setup --goal-id <goal-id> --bot-id <bot-id> --chat-id <chat-id>
```

`goal-channel` is the durable control-plane object and the user-facing CLI.
The optional [botmux runtime integration](../../integrations/botmux-goal-channel-runtime.md)
delegates IM delivery and persistent agent sessions to botmux without changing
Goal Channel or LoopX state authority. Delivery does not imply a state
transition; the configured agent runtime must still invoke LoopX explicitly.

## Ownership Model

| Capability | LoopX | Provider channel | Provider adapter |
| --- | --- | --- | --- |
| Goal lifecycle | Owner | Projection | Calls LoopX |
| Todos, claims, gates, quota | Owner | Projection and prompts | Syncs bounded packets |
| Kanban rows | Source data owner | Display owner | Upserts rows |
| Group/chat/thread | References binding | Owner | Creates, updates, reads |
| Pinned status | Builds bounded content | Displays | Sends and pins |
| Human gate question | Owner of question and cooldown | Delivery | Sends and verifies |
| Credentials and profile | Never stores secrets | Provider auth | Uses local-private profile |
| Receipts | Owner of accepted transition receipts | Message ids are private | Records compact send/readback receipt |

The provider may store its own state. LoopX stores only the minimum local-private
binding needed to operate the channel.

## Lark Provider Binding

A Lark Goal Channel binding is local-private and project-scoped:

```json
{
  "schema_version": "loopx_goal_channel_lark_binding_v0",
  "goal_id": "loopx-goal",
  "provider": "lark",
  "enabled": true,
  "channel": {
    "chat_id": "oc_<private-chat-id>",
    "chat_name": "LoopX - loopx-goal",
    "pinned_message_id": "om_<private-message-id>"
  },
  "kanban": {
    "base_token": "<private-base-token>",
    "table_id": "tbl...",
    "view_ids": {
      "Kanban": "vew...",
      "User Gates": "vew..."
    }
  },
  "identity": {
    "mode": "project_bot",
    "sender_profile": "loopx-project-bot",
    "sender_identity": "bot",
    "bot_display_name": "LoopX Bot"
  },
  "receipts": {}
}
```

The file belongs under `.loopx/` or another ignored local-private path. Public
status packets must not expose chat ids, member ids, message ids, profile names,
raw Lark payloads, local file paths, or credentials. Public packets may expose
booleans, counts, sanitized provider labels, and operator-safe URLs only when
the caller has already chosen to show them.

### Shared provider targets

Several Goal Channels may reference one named local-private provider target.
The target owns the reusable Lark chat and sender identity; each Goal binding
continues to own its control message, Kanban, receipts, and cooldown state:

```bash
loopx goal-channel target add \
  --name loopx-dev \
  --provider lark \
  --chat-id <private-chat-id> \
  --bot-app-id <private-app-id> \
  --execute

loopx goal-channel attach \
  --target loopx-dev \
  --goal-id goal-a \
  --goal-id goal-b \
  --execute
```

The target store belongs under the resolved LoopX runtime root and is never a
public or repository-tracked configuration. A target-linked Goal binding stores
only `target_ref` plus Goal-local state; changing a target updates the resolved
chat or sender for every referencing Goal without merging their state.
After moving a target to another group, rerun bounded `attach` batches so each
Goal can establish and verify its own control message in the new group.

Machines do not synchronize private chat ids or authentication profiles. To
use the same group from a workstation and a development host, configure the
same target name independently on each machine. Inbound routing must reply to a
specific gate message or carry an explicit Goal id; ordinary group text must
never be inferred as belonging to one of several Goals.

## BYO Provider Identity

Open-source LoopX should default to **Bring Your Own provider identity**:

- users create or select their own Lark app or bot in their tenant;
- users authenticate it through `lark-cli` or a future provider-specific
  profile manager;
- LoopX stores only the local profile reference and compact verification state;
- LoopX never ships a fixed cross-tenant bot as an implicit dependency.

Supported identity modes:

| Mode | Intended use | Tradeoff |
| --- | --- | --- |
| `local_user` | Create and own the group and Base as the user | Easy resource ownership; the bot is still required for messages |
| `project_bot` | Use a dedicated bot profile for channel messages | Requires app/bot setup but gives stable message identity |
| `managed_app` | Future hosted product | Best UX, requires tenant install, compliance, and operations |

The first implementation uses the local user identity for group and Base
operations. Goal Control messages, pins, and gate notifications always use the
configured bot identity. It does not require or request
`im:message.send_as_user`.

Effectful direct setup requires an explicit `--bot-app-id cli_...`; target-based
setup obtains that explicit selection from the local-private target. LoopX
verifies that it matches the selected `lark-cli` profile before adding the bot or
sending a message. Omitting the flag is a preview-only convenience, not
authorization to select the default profile's bot.

## Lifecycle

### Setup

`goal-channel setup --provider lark --goal-id <goal-id>` should:

1. Resolve and validate the goal.
2. Load or create the local-private Lark channel binding.
3. Verify `loopx-lark` extension activation and required permissions.
4. Verify the local user resource identity and the configured bot sender.
5. Create or reuse a Lark group chat and verify the bot is a member.
6. Create or reuse the Lark Kanban Base through `lark-kanban`.
7. Read back and persist the canonical Base URL.
8. Send a compact Goal Control message containing the Kanban link.
9. Pin that verified control message.
10. Save the local-private binding and compact receipts.

Default mode is dry-run. External writes require `--execute`.

### Sync

`goal-channel sync` composes existing projections:

- `lark-kanban sync-loopx-todos` for active user/agent todos and derived
  domain outcomes;
- a compact status/control message update or append when the visible channel
  summary changed materially;
- optional periodic report or explore projection sinks only when separately
  configured.

The sync command must not create new canonical todos from remote rows.

### Human Gate Notification

`goal-channel notify-gate` sends a bounded message when LoopX already decided a
human gate or user todo needs attention. The trigger input is the existing quota
and interaction-contract surface:

- `state=operator_gate`;
- `notify_user_on_gate=true`;
- `notify_user_on_open_todo=true`;
- `gate_prompt`;
- `operator_question`;
- `open_todo_notify_reason`;
- `user_todo_summary`;
- `user_gate_notification_cooldown`.

The message includes:

- goal label and short objective;
- concrete gate question;
- up to three user-gate or user-action todos;
- expected reply format;
- Kanban link or channel control link;
- next safe action while waiting, if any.

It excludes local paths, raw active state, private logs, credentials, message
ids, and raw provider payloads.

Automatic delivery is disabled by default. After Goal Channel setup, preview
and then enable it explicitly:

```bash
loopx goal-channel configure --goal-id <goal-id> --auto-notify-human-gates
loopx goal-channel configure --goal-id <goal-id> --auto-notify-human-gates --execute
```

Once enabled, each successful non-dry-run `refresh-state` rebuilds quota from
canonical LoopX state. It sends only when quota selects a human gate, and it
reuses the same bot verification, semantic idempotency, cooldown, provider
idempotency key, and message readback as `notify-gate`.

Use `loopx refresh-state ... --suppress-external-sinks` to suppress delivery for
one refresh without disabling the binding. Turn-bound recovery remembers that
pause and requires `--resume-external-sinks <resume_key>` to resume delivery using
the returned key. This acknowledges the current operation, not new permissions;
historical operations without pause evidence retain their previous behavior.
See the [recovery handshake](../../state-interaction-model.md). Disable automatic
delivery persistently with:

```bash
loopx goal-channel configure --goal-id <goal-id> --no-auto-notify-human-gates --execute
```

The opt-in is stored only in the project-local private Goal Channel binding.
It does not grant repository or LoopX transition authority. Chat replies can
provide context, but a gate changes only after LoopX validates and records the
corresponding decision.

Automatic lifecycle delivery resolves the enabled, doctor-verified Lark
extension before reading the private binding. Enabling automatic delivery
requires the canonical project-local binding path; custom `--binding-path`
values are rejected because `refresh-state` has no per-invocation path input.
The explicit local disable command is the only recovery exception: it may clear
the opt-in while the extension or binding is incomplete, and it never enters
provider code or performs an external write.

Enabling also writes an owner-only local marker containing only the enabled
boolean. The lifecycle may read this marker before extension activation solely
to distinguish a never-configured project from a configured sink whose
extension became unavailable. The latter fails closed with a retryable
`extension_unavailable` postcondition; the marker contains no provider ids,
credentials, channel metadata, or raw payloads.

## Command Contract

Each effectful command returns a compact packet:

```json
{
  "schema_version": "loopx_goal_channel_operation_v0",
  "ok": true,
  "goal_id": "loopx-goal",
  "provider": "lark",
  "operation": "notify_gate",
  "execute": true,
  "external_write_performed": true,
  "readback_verified": true,
  "idempotency_key": "sha256:...",
  "receipt_id": "receipt_...",
  "public_summary": "sent one gate notification to the configured Lark channel",
  "private_provider_payload_captured": false
}
```

Failures should be typed:

- `extension_unavailable`;
- `provider_identity_unverified`;
- `channel_binding_missing`;
- `channel_membership_unverified`;
- `kanban_binding_missing`;
- `notification_cooldown_active`;
- `readback_mismatch`;
- `state_transition_rejected`;
- `provider_api_failed`.

## Idempotency And Cooldown

Provider writes use idempotency keys derived from the semantic action, not the
wall-clock attempt:

```text
goal_id + provider + operation + todo_id/gate_id + gate_text_hash + channel_id
```

Rules:

- retrying the same send returns `already_sent` or the original receipt;
- gate text changes may create a new notification key;
- cooldown suppresses repeated reminders without closing the gate;
- stale provider events cannot override newer LoopX revisions.

## Security And Privacy

- Channel membership is not LoopX write authority.
- Bot membership is verified before sending.
- Message readback is required before recording a successful send receipt.
- Raw provider payloads stay local-private.
- Shared/global registry calls resolve Goal Channel state beside the selected
  goal's canonical `source_registry`; caller CWD is never a default state root.
- Local-private JSON uses an owner-only temporary file plus atomic replace, so
  interrupted writes do not expose or truncate the previous binding.
- Local checkout paths, active-state paths, credentials, chat ids, member ids,
  message ids, and profile names do not enter public artifacts.
- The channel may show a Kanban link, but the Kanban remains a projection.
- Destructive, credentialed, production, publish, merge, or external-write
  gates remain LoopX gates and cannot be bypassed by chat text.

## Smallest Useful Slice

The smallest useful implementation should be:

1. Add `loopx goal-channel` with `setup`, `configure`, `doctor`, `sync`, and
   `notify-gate`.
2. Implement only the Lark provider.
3. Reuse existing `lark-kanban` setup/sync and `loopx-lark` extension
   activation checks.
4. Create or reuse one Lark group for one existing goal.
5. Send and pin one compact Goal Control message.
6. Send a human-gate notification with idempotency and readback.
7. Optionally send LoopX-selected human gates after authorized `refresh-state`
   writes.
8. Store local-private binding, automation opt-in, and receipts under `.loopx/`.

This slice proves the external collaboration entry point.

## Validation

The first slice must prove:

- setup is dry-run by default and performs external writes only with
  `--execute`;
- one goal maps to one local-private Lark binding;
- extension activation is checked before private config is read;
- a Kanban board can be reused or created and then synced;
- a Goal Control message is sent, pinned, and readback verified;
- human-gate notification respects cooldown and idempotency;
- repeated notification retries do not duplicate visible messages;
- automatic delivery is disabled by default and can be suppressed per refresh;
- automatic delivery reads canonical quota and does not send for non-gate state;
- doctor reports missing bot auth, missing channel, missing Kanban, or stale
  extension activation with typed blockers;
- local-private binding files remain ignored and untracked;
- public packets do not contain chat ids, member ids, message ids, profile
  names, local paths, raw provider payloads, or credentials.
