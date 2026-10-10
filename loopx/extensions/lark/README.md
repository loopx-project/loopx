# Lark Provider

The bundled `loopx-lark` extension supplies optional Lark execution and
presentation providers. It does not replace LoopX goal, todo, gate, quota,
evidence, or recovery authority.

## Provided capabilities

| Capability | Outcome | Primary implementation |
| --- | --- | --- |
| `lark-event-inbox` | Collect, inspect, reply to, and acknowledge bounded project feedback | [`event_inbox.py`](event_inbox.py), [`event_collector.py`](event_collector.py) |
| `lark-reviewer-notification` | Send and verify a reviewer notification through a project-dedicated Lark app | [`reviewer_notification.py`](reviewer_notification.py) |
| `lark-kanban-projection` | Render public-safe LoopX todo and control-plane projections into Lark Base | [`presentation/kanban.py`](presentation/kanban.py) |
| `lark-goal-channel` | Bind one verified Lark group and projection surface to one LoopX goal, including authenticated confirmation cards for canonical typed operations | [`goal_channel.py`](goal_channel.py), [`goal_channel_operation.py`](goal_channel_operation.py) |
| `lark-explore-projection` | Project canonical Explore results into Lark tables, cards, and whiteboards | [`presentation/explore_results.py`](presentation/explore_results.py) |
| `lark-periodic-report-announcement` | Deliver a periodic report through the current Goal Channel's verified project Bot while mentioning only recipients selected by its typed audience plan | [`periodic_report_delivery.py`](periodic_report_delivery.py) |
| `lark-periodic-report-source` | Bind and settle one exact Agent-selected Goal Channel source for a typed report action without classifying message text | [`periodic_report_request.py`](periodic_report_request.py) |
| `lark-miaoda-html-report` | Publish an already-rendered periodic report to an operator-selected existing Miaoda app | [`presentation/periodic_report.py`](presentation/periodic_report.py) |

Mention-bearing text delivery is owned by the extension's shared outbound
contract in [`outbound.py`](outbound.py). Inbox replies and reviewer
notifications use the same structured `<at ...>` construction, provider
dry-run, and exact readback rule. A visible literal `@Name`, successful message
creation, or matching display text is not mention-delivery evidence. The
provider readback must expose exactly the identities requested at send time in
`mentions[]`; missing, extra, ambiguous, or different identities fail closed.
Callers that need notification semantics must use these extension surfaces
instead of invoking a raw `lark-cli` send command.

For an inbox-configured Bot, preview and verify one proactive top-level message
with the same contract used by replies. A multi-chat collector requires its
public-safe `route_key`; a single inbox accepts the default route:

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

The command uses only the owner-local profile and chat already bound to the
selected inbox route. It resolves every structured identity against exact chat
membership, performs a provider dry-run, sends idempotently, and reads the
created message back. It returns no profile, chat id, message body, or raw
provider payload. Installation and the command itself grant no new Lark scope
or external-write authority.

When an Agent semantically interprets one inbox item as a report request, it
passes that item's exact `message_id` to `loopx periodic-report request`.
`periodic_report_request.py` validates only user authorship, provider-native
addressing, the selected Goal/Agent binding, provider target, and inbox
identity. It neither scans other inbox items nor inspects text for report
keywords. Its bind/settle ports are discovered from `extension.toml`; source
ACK happens only after the capability has persisted `delivery_ready` state.
Transient ACK failures remain replayable. A missing source or binding/receipt
identity drift is recorded as a terminal settlement failure and left un-ACKed;
after correcting the configuration or retention issue, re-deliver the request
as a new Lark message and invoke the typed action with its new `message_id`.

The [event inbox guide](docs/lark-event-inbox.md) documents the complete
collector, processing, reply, reaction, and acknowledgement lifecycle. The
[Lark Kanban integration guide](../../../docs/integrations/lark-kanban-control-plane-adapter.md)
documents projection configuration and lineage.

### Human-confirmed typed operations

The Goal Channel may project a canonical `operation.execute` typed-action
proposal as one non-forwardable Card 2.0 confirmation card. The operation
envelope, lifecycle, exact digests, authorized operator set, claim, and outcome
remain in the Core Chat action store; Lark owns only authenticated transport,
callback provenance checks, and result-card readback. It does not create a
second User Todo or approval ledger.

Confirmation and result cards consume the same internal TypeScript
`operation_review_frame_v0` used by Dashboard. Lark requests that frame once per
card render, then owns only provider-specific Card 2.0 markup and delivery; the
frame neither grants authority nor replaces the canonical operation receipt.

That request path is not operation-only. `presentation.action_review_plan.compile`
also returns a `review_card_frame_v0` for a validated steward team plan
(`team.plan`), whose confirmation identity is the action proposal and the state
fingerprint the apply re-validates against rather than an operation envelope. Its
fields are `{key, value}` pairs and its fixed labels are keys, so this boundary
stays language-neutral and the surface owns the words. A plan card can therefore
reuse the card shell, the callback consumer, the operator membership check,
replay protection and card readback described here. What a plan card still needs
is its own delivery route for the audience that asked (the manager group is not a
Goal channel binding) and a callback effect that applies the proposal through the
Chat action service instead of claiming an operation envelope.

```bash
loopx goal-channel prepare-operation \
  --goal-id <goal-id> \
  --agent-id <registered-agent-id> \
  --summary "Review one simulated order" \
  --idempotency-key <stable-request-key> \
  --request-json <loopx-operation-request.json> \
  --execute

loopx goal-channel deliver-operation \
  --goal-id <goal-id> \
  --proposal-id <typed-action-proposal-id>

loopx goal-channel deliver-operation \
  --goal-id <goal-id> \
  --proposal-id <typed-action-proposal-id> \
  --execute
```

The request file contains the provider-neutral `loopx_operation_request_v0`
fields except `goal_id` and `agent_id`, which come from the CLI scope. Preparing
uses the canonical Chat action service and store; it does not create a Lark- or
finance-owned approval ledger. Preview mode validates in an ephemeral store and
writes nothing durable.

Enable `operation_callbacks.enabled=true` in a v1 event collector config and
install that collector with the pinned LoopX runtime root. The service starts a
separate `card.action.trigger` consumer beside message capture because each
`lark-cli event consume` process owns one EventKey. Collector status reports
listener health separately from real callback evidence; a healthy process does
not prove the application console is configured to deliver callbacks.

The callback listener becomes ready only after the provider emits its event-bus
ready marker or the listener receives a typed callback. Status remains
`listener_ready_unqualified` until one authenticated callback is handled and its
canonical result-card readback succeeds; only then does it become
`callback_qualified`. Simulation-only cards use one explicit confirmation click.
Effectful protected operations retain the provider's second confirmation dialog
and label the first button as a continuation rather than a completed decision.

回调进程只有在 provider 发出事件总线 ready 标记，或收到一条真实 typed callback
后，才会被视为 ready。在一条已认证 callback 被处理且结果卡完成规范读回之前，
状态保持 `listener_ready_unqualified`；完成后才进入 `callback_qualified`。
纯模拟卡使用一次明确确认；有外部效果的受保护操作仍保留平台二次确认，并把第一步
按钮明确标为继续操作，而不是已经完成决策。

On click, LoopX verifies the original App, chat, message, immutable card digest,
operator allowlist, tenant membership, expiry, and globally unique event id.
Confirmation atomically claims the operation before dispatch. Exact event
replay reuses the existing claim/outcome, and a per-operation dispatch lock
prevents concurrent copies of that callback from invoking the executor twice.
The result update is not complete until the same card is read back with the
expected App, chat, message, and content digest. If that update is acknowledged
but cannot be verified, the canonical outcome remains durable and the collector
retries only the result-card patch after restart. A restart may also resume one
already-claimed request only when its exact executor permission, operation kind,
and destination prove that it is the bundled non-effectful M1 simulation; live or
otherwise effectful domain operations are never retried. Callback health,
recovered simulations, and recovered-result counts remain separate. The initial
finance executor is a
simulation-only optional package: it has no venue, signer, wallet, transfer, or
live-order permission. A successful SDK callback acknowledgement is never
reported as the domain execution receipt.

### Bounded group-history catch-up

The event inbox can reconcile messages that predate the live event collector.
Each invocation reads one ascending page from one configured `route_key` and
previews the inbox/cursor transition by default. `--execute` first persists and
reads back every canonical inbox event, then advances an owner-local cursor:

```bash
loopx lark-inbox history-catch-up \
  --project . \
  --config .loopx/config/lark-collector.json \
  --route-key project-feedback \
  --start 2026-08-01T00:00:00Z

loopx lark-inbox history-catch-up \
  --project . \
  --config .loopx/config/lark-collector.json \
  --route-key project-feedback \
  --start 2026-08-01T00:00:00Z \
  --execute
```

Retries resume the exact private page token. A completed window replays
without another provider read only while its upper coverage bound is current;
a later invocation opens one bounded forward window from the previous end, so
new messages in an existing group or topic are not stranded behind an old
`history_complete` state. A caller may also extend one completed history
window to an earlier start once; the provider covers only the missing earlier
window and rejects later source/config drift. Legacy v0 cursors migrate
conservatively: they may replay already ingested messages, but never advance a
coverage bound that could skip unseen history. The returned link-evidence packet
contains URL plus message and route lineage for the owner-local Agent, but not
the surrounding message body, sender, chat id, profile, cursor, or raw provider
payload. Inbox and cursor directories are restricted to the owner, and their
state files are written with mode `0600`. Product-specific URL classification
and field-enrichment policy remain with the consuming product or private skill.

The cursor binding includes the route key, Bot profile, chat, inbox config,
resolved inbox destination, and capture scope. If any of those inputs changes,
catch-up fails closed with `Lark group-history cursor source binding changed`
before reading the provider. Restore the original route to resume, or move the
owner-local `.loopx/inbox/.history/<route-key>.json` cursor aside and restart
from an explicit `--start`; canonical message ids keep inbox ingestion
idempotent while the replacement cursor rebuilds coverage.

Group-history reads use the configured Bot identity and require the Bot to be a
member of the group, the application to be published, and
`im:message:readonly` plus `im:chat:read`. Permission error `230027` is returned
as typed `group_history_permission_required`; it never advances the inbox or
cursor.

### Dynamic collector route reconcile

An already provisioned inbox can be enrolled into a v1 multi-chat collector
without rewriting the complete owner-local collector file by hand. Preview the
route first, then apply it explicitly:

```bash
loopx lark-inbox collector-route-reconcile \
  --project . \
  --config .loopx/config/lark-collector.json \
  --route-key project-feedback \
  --chat-id oc_<local-private-chat-id> \
  --event-inbox-config .loopx/config/lark/project-feedback.json

loopx lark-inbox collector-route-reconcile \
  --project . \
  --config .loopx/config/lark-collector.json \
  --route-key project-feedback \
  --chat-id oc_<local-private-chat-id> \
  --event-inbox-config .loopx/config/lark/project-feedback.json \
  --execute
```

The operation validates unique route, chat, inbox-config, and inbox-path
bindings; serializes concurrent writers; writes through an atomic replacement;
and reads the exact binding and config digest back. Repeating the same request
is a zero-write `already_applied` result, while any binding drift fails closed.
Receipts return the public-safe `route_key` but never the chat id, inbox config,
local path, profile, or credentials.

Config readback does not prove that a running collector has reloaded the new
route. Every successful plan/apply receipt therefore keeps
`runtime_reload_required=true`, `runtime_reload_performed=false`, and
`runtime_readback_verified=false`. The deployment owner must restart or
reinstall the collector and independently verify its runtime before treating
the route as live. Removing routes remains a separate owner-authorized
lifecycle operation; this additive command never deletes or rebinds one.

## Lifecycle

Install the bundled provider explicitly, then read back its readiness:

```bash
loopx extension install --bundled loopx-lark --execute --format json
loopx extension doctor loopx-lark --execute --format json
loopx capability list --format json
```

Disable or roll back the provider without changing the owning capabilities or
Kernel state:

```bash
loopx extension disable loopx-lark --execute --format json
loopx extension rollback loopx-lark --execute --format json
```

For Miaoda, `loopx periodic-report publish-miaoda --request-json <path>` first
previews a typed hosted-delivery intent. Add `--execute` only after checking the
profile-bound sink, request-selected app, and artifact. The command delegates
authentication and the external publish/readback calls to `lark-cli`; LoopX
stores no credentials and does not treat local HTML generation as hosted
delivery.

For a Lark report announcement, the Periodic Report profile owns symbolic
recipients, domains, and typed routing rules. The core compiles the relevance
plan without provider identities. Preview performs no identity lookup or send;
execute resolves only selected recipients and omits unrelated recipients. Raw
`<at>` markup in report content or card metadata cannot bypass that policy.
The Goal Channel delivery command accepts exactly two ordered HTTPS entries
(hosted report, then Lark document), emits two independently idempotent
messages, and verifies the native sender App plus exact chat for each readback.
Each provider idempotency key binds the base delivery identity to the rendered
announcement kind, title, body, footer, and an explicit semantic version. A
renderer change therefore cannot make an upgraded retry reuse an older card
under the same provider key.

Installation controls discoverability and provider lifecycle only. Every
private chat, app, group, Base, document, or Miaoda target remains in ignored
local configuration. External writes still require the owning capability's
exact authority, gate, revision, idempotency, and readback contract.

## Document-comment Connector provider

`document_comment_provider.py` adapts one owner-configured Lark document to the
provider-neutral Agent external Connector runtime. It delegates authentication
and API calls to `lark-cli`, probes the exact comment read/create scopes, and
turns one bounded comment or nested-reply page into owner-local inbox events.
The adapter requires `lark-cli` 1.0.69 or newer for `drive +list-comments`;
older binaries fail closed and must be upgraded before the Connector is ready.
The provider supports configured-source and incremental capture. It rejects
`addressed_only` until a caller supplies an explicit mention-identity contract;
it never guesses that every document comment addressed the Agent.

Lark comment pagination has separate cursors for comment cards and replies.
The adapter persists both phases in the private Connector cursor and restarts a
completed scan from the first comment page, relying on stable hashed event ids
and the generic inbox for deduplication. A response-capable binding must also
configure an owner-local reply receipt store. Reply creation writes a pending
receipt, reads the exact reply back, then marks the receipt verified; only that
verified receipt lets the generic runtime ACK the event. Solved and
whole-document comment cards are skipped for source-thread response bindings
because the provider does not permit replies to them.

Document URLs, `lark-cli` profiles, provider cursors, comment/reply ids, raw
payloads, and reply receipts remain owner-local. Public status reports only
permission readiness, operation counts, inbox health, and content-free failure
codes. The required provider scopes are
`docs:document.comment:read` for history/readback and
`docs:document.comment:create` for replies; enabling the extension does not
grant either scope or publish an app.

## Ownership boundary

- The extension owns Lark authentication checks, provider dispatch, bounded
  payload conversion, delivery receipts, and readback.
- Outcome capabilities such as Issue Fix, Explore, and Periodic Report own the
  domain request and interpret provider receipts.
- The Kernel alone accepts durable todo, gate, quota, evidence, and recovery
  transitions.
- Lark projections are sinks. They never become the control-plane source of
  truth.

The declarative capability and permission surface is maintained in
[`extension.toml`](extension.toml). Provider readiness never grants a new
permission or silently enables an external write.

## 创建 LoopX 机器人（推荐权限集）

每个新的 LoopX 企业自建应用（例如 Goal Channel 的发送 bot）应一次性申请
推荐权限集，而不是边用边补。清单见
[`bot_scopes.py`](bot_scopes.py)（`RECOMMENDED_BOT_SCOPES`），按用途分三档：

- 核心（Goal Channel / reviewer / kanban）：`im:message`、`im:chat:read`、
  `im:chat:create`、`im:chat:update`、`im:chat.members:read`、
  `im:chat.members:write_only`、`contact:user.base:readonly`、
  `contact:contact.base:readonly`、`application:application:self_manage`、
  `application:bot.basic_info:read`
- 收件箱（事件订阅与群历史，敏感需审核）：`im:message:readonly`、
  `im:message.group_msg`、`im:message.group_msg.include_bot:read`、
  `im:message.p2p_msg:readonly`
- 交互/文档 sink：`cardkit:card:read/write`、`docs:document.comment:read/create/delete`

`im:chat.members:read` 是原生 @ 身份校验的核心权限，也适用于 @ 机器人。
发送前使用 `im +chat-members-list --member-types user,bot --page-all` 查询
精确成员身份；旧的 `chat.members get` 不能作为机器人不在群内的证据。
默认权限清单不代表已有应用已获授权：旧应用仍需在开发者后台申请并完成审核，
LoopX 不会自动授予权限，也不会在成员读取失败时绕过 @ 校验。
若消息回读将机器人标为 `app_id`，仅接受当前群机器人列表中已验证且无歧义的
`member_id` ↔ `app_id` 映射；名称相同不构成身份验证。

拿到 App ID（`cli_xxx`）后，可在开发者后台一键批量申请：

```text
https://open.larkoffice.com/page/scope-apply?clientID=<app_id>&scopes=<scope1%2Cscope2...>
```

（`recommended_bot_scope_apply_url(app_id)` 会拼出完整 URL。）随后用
`lark-cli config init --app-id <app_id> --app-secret-stdin --name <profile> --brand feishu`
注册飞书 bot profile；国际版 Lark 应用才使用 `--brand lark`。品牌必须与应用实际
所属平台一致，不能从 App ID 或普通 API 查询成功推断。然后走
`loopx goal-channel setup`。敏感 scope 需企业管理员审核。

### Real-time source health / 实时连接排障

A successful bot authentication or history query does not verify the event
WebSocket. The CLI ready marker confirms local consumer registration, not
upstream connectivity. If the bus subsequently closes and the CLI exits with
`reason: signal`, LoopX reports `lark_event_source_disconnected` and retries;
exit code zero alone does not make this a healthy scheduled restart. Only the
requested timeout/limit or LoopX's own shutdown is a planned ending.

遇到该错误，先核对所选 profile 的品牌：飞书为 `feishu`，国际版为 `lark`。
上游 `1000040351` / `Incorrect domain name` 表示域名与应用平台不匹配。
随后核查事件总线的连接错误、订阅和权限；不要仅凭 `event status` 的本地
consumer 数或普通消息查询成功宣布恢复。不要自动切换品牌、账号或扩大权限。
修复后核验真实 WebSocket 连接，再通过原消息 ID 核对收件、执行、回复回读和
ACK；断线期间的历史消息不能批量重放，已执行请求必须复用原幂等标识。
错误日志可能带连接凭据，诊断与公开报告只保留错误码和脱敏结论。

## Goal Topic defaults and existing connections

New Agent connections use `async_inbox` by default across the provider and local
Chat API. `session_queue` and `live_steering` require an exact Agent session.
`direct_session` is accepted when reading old bindings; it cannot be selected
for a new connection write. Existing routes continue to work during migration.
The older `goal-channel setup` notification/kanban workflow remains available;
its recipientless connections need an Agent mapping before accepting modern
Agent inbox work.

Upgrade a Goal's existing Lark connections through the same provider contract:

```sh
loopx goal-channel upgrade --goal-id example-goal
loopx goal-channel upgrade --goal-id example-goal --execute
```

An existing recipient, or the only registered Agent, is reused automatically.
If an old connection has no recipient and the Goal has multiple Agents, select
its opaque connection ID and the intended Agent explicitly:

```sh
loopx goal-channel upgrade --goal-id example-goal \
  --connection-id lark_example --agent-id example-agent --execute
```

Preview is read-only. Upgrade retains the connection ID, default route, App,
group, Topic root, capture scope, notification settings and receipts. It verifies
the same Bot's membership and reads the exact old message back. Both the earlier
`LoopX Goal:` control marker and the newer `Goal ID:` Topic marker are recognized
as exact lines in that message. It creates no Topic and adds no group members.
An unavailable root, mismatched identity, changed capture scope, or duplicate
Agent route leaves the connection unmodified. Already upgraded routes are
skipped, and a blocked route does not prevent independent upgrades.

The local frontend uses `connection_id` for edits rather than reconstructing a
connection from displayed App/group names. Saving an old connection defaults to
its Agent inbox. Identity fields stay bound to the original connection even if
its App profile alias is missing from the current App catalog. Source snapshots,
private provider IDs and migration receipts belong in ignored local storage.
Configuration readback alone does not prove that a user message was processed;
verify runtime routing and delivery separately after migration.

## Built-in machine manager

Machine-level Lark onboarding defaults to **Manager · live conversation**. Goal
worker connections remain a separate purpose with async inbox defaults. The
manager is the built-in `loopx-manager` role, not an ordinary worker name or a
project-specific heartbeat. Its executor endpoint defaults to `codex` and is
recorded separately from its logical identity.

A manager connection uses `conversation_kind=manager`. Preview has no session
creation side effect. On apply, the Chat service opens or resumes the manager's
exact audience session and binds `session_queue`; delivery waits for that turn
and its verified reply, without waiting for a scheduled Agent wakeup. When a
manager Turn ends in a persisted failure, the same verified-reply path sends a
bounded failure notice before acknowledging the source. Unknown upstream
details are not copied into the group. The connection keeps its processing
failure state; delivery of that notice is not a successful model answer.
An unverified outbound notice leaves the source pending, and a verified notice
prevents duplicate source events from replaying the failed request. Group-root
mentions and addressed replies can reach the manager without an invented Topic
root. Exact worker Topics retain their own routing, and ambiguous manager
bindings fail closed.

Before Lark delivery, a manager answer is stored in a private, event-bound
outbox. A transport retry or service restart reuses that exact saved answer and
provider idempotency identity instead of rerunning the model. Presentation-only
defects may downgrade from Markdown to inert plain text: literal newline tokens
become real newlines and unresolved visual `@` text cannot become a native
mention. An incomplete review envelope is never delivered as visible protocol
and never grants a gate, proposal, or protected action. The source event is ACKed
only after reply readback and the durable delivery receipt are both verified.

管家答复在发送到飞书前，会先按来源事件写入本地私有 outbox。传输重试或服务
重启只续投同一份已保存答复并复用 provider 幂等标识，不会再次运行模型。仅影响
展示的瑕疵可以从 Markdown 安全降级为不可执行的纯文本：字面换行转为真实换行，
未解析的可视 `@` 文本不会变成原生 mention。不完整的 review envelope 不会作为
正文泄漏，也不能恢复 gate、proposal 或受保护动作。只有回复回读和持久投递回执
都验证通过后，来源事件才会 ACK。

The frontend and Lark use the same manager conversation service and the existing
typed control plane. Their transcripts are separated by audience: an external
conversation can never resume the owner's private frontend session or another
group's session. Long-running work still belongs to worker Agents. Conversation
turns retain the existing read-only tool policy and typed preview/apply authority;
a synchronous response is not permission to mutate arbitrary repositories or
skip a control-plane receipt.


### Native private Chat progress

Native private project Chat and steward conversations present persisted Core
Turn events as a mutable, source-bound Markdown reply. Answer chunks are coalesced at a two-second interval; changes only to fixed
activity labels are coalesced at ten seconds to avoid message churn. Reasoning,
tool arguments and command output are excluded. The transport does not run models.

The conservative edit budget applies to each draft, rather than the whole Turn.
When it is exhausted, the transport verifies a short continuation notice on the
old draft, then creates a numbered continuation using the existing sender and
source placement. A definite provider edit-limit rejection also allows this
continuation; uncertain closes or creates recover their frozen intent instead
of blindly sending another message. Archived draft proofs survive restarts.
The full canonical answer replaces the latest draft, with the existing oversized
answer and provider-limit fallback. Source/App/audience checks and final
readback still gate delivery, and only final delivery acknowledges the request.

原生私聊的回答片段按两秒间隔合并更新，仅工具状态变化时按十秒合并，避免刷屏。
每条草稿分别计算编辑预算。长任务用完
预算后，先读回旧草稿上的续接提示，再在同一来源位置发送编号的后续进度；明确的
provider 编辑次数耗尽也允许续接。关闭或创建结果不明时，恢复原有投递意图，不盲目
重发、不重跑任务。最终完整答案替换最新草稿，超长答案沿用既有完整发送路径；
来源、App、接收范围和最终读回校验仍然有效。
