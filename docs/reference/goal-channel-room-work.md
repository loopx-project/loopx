# Canonical room work through the Agent CLI

This composition stage of [the Agent IM / LoopX / OpenViking RFC](../architecture/rfcs/agent-im-openviking-collaboration-v0.md)
lets a local registered Agent publish compact work orientation and a canonical
claim receipt into its existing Lark Goal Channel. It uses one already promoted
local File or SQLite authority. It does not qualify independent multi-host
service authority or live OpenViking integration. A scoped IM claim button
callback is implemented and tested with synthetic transport.

The existing Goal Channel connection remains the configuration owner. An exact,
enabled Agent connection and a verified project Bot are required; a default or
sibling connection cannot stand in for the named Agent. No new capability,
provider, scheduler, memory store, or frontend setting is introduced. Python
adapts CLI/provider IO; the TypeScript Todo transaction retains state, CAS,
identity and receipt authority. Identity follows the existing trusted local CLI
model; `--agent-id` is not remote authentication. The typed room read model reuses that claim
admission rule. No existing command automatically publishes work cards.

## Preview and publish

This stage ships the Agent CLI path only; the earlier proposed Room work
frontend entry is withdrawn to a follow-up proposal so steward-led automatic
delegation remains the default collaboration route. Steward-publish with
Agent self-claim goes through the CLI and the scoped IM claim-button offer
below, not through a manual browser claim page.

Room reads carry the existing `loopx_projection_envelope_v0` freshness and coverage
contract. Registry, connection and
canonical Todo source timestamps precede assembly; a delayed read cannot make
old inputs fresh. Unknown, stale, unreadable or incomplete reads are disclosed
before counts or current ownership. CLI Markdown and public room cards disclose
the same envelope alerts. A claim replay rereads current sources; serving a
saved copy retains its original read time and recomputes age through the
existing envelope owner.

### Agent CLI

From a source checkout use `uv run --extra test loopx` in place of `loopx`.
Use the source registry and a previously authorized non-production connection.
Do not promote an active Goal merely to try these commands. Follow the existing
[reviewed promotion](reviewed-coordination-promotion.md) procedure on a disposable
fixture if qualification needs canonical authority.

```sh
loopx --registry .loopx/registry.json --format json goal-channel work project \
  --goal-id room-goal --agent-id agent-a
```

This preview reads canonical state and resolves the exact delivery binding. It
does not contact Lark, accept a claim, or send a message. The projection carries
`source_revision`, `generated_at`, the first eligible unclaimed Todo id and
actor-scoped counts. Task prose, notes, validation declarations, evidence,
artifacts and recalled context are omitted. `authority_state=available` describes
the provider read, not Goal completion or permission to execute.

Add `--execute` to publish that orientation through the existing verified Bot:

```sh
loopx --registry .loopx/registry.json --format json goal-channel work project \
  --goal-id room-goal --agent-id agent-a --execute
```

## Claim and receipt recovery

Take `--expected-revision` from the fresh projection and choose a stable
idempotency key. Preview first by omitting `--execute`:

```sh
loopx --registry .loopx/registry.json --format json goal-channel work claim \
  --goal-id room-goal --agent-id agent-a --todo-id todo_example \
  --expected-revision '<source_revision>' --idempotency-key room-claim-example
```

With `--execute`, the local Agent CLI asks the same canonical Todo owner used
by direct `todo claim`, then publishes the receipt and current orientation.
This explicit command authorizes those two effects. Room membership, delivery,
a card or recalled text supplies neither the Agent identity nor write scope.
Identity and binding are reread; binding/target revocation serialize with the
claim transaction. The facade retains original broker/source registry witnesses
before provider reads. Claim request v3 holds their existing cross-runtime
mutation guards through the native canonical commit; drift rejects before a
claim or receipt is written, and a busy source requires a fresh retry. Ordinary
direct claim v0/v1 requests keep their existing behavior and reject v3 guard
fields rather than silently dropping that obligation. Claim request v2 remains
main's managed-execution claim and is not used by this facade.

The provider compares the expected revision inside the claim transaction.
Competing commands at one revision cannot both commit. A stale revision returns
`conflict`; it never silently refreshes its basis. Canonical claims now also
reject a Todo bound to another Agent, matching the lifecycle authority boundary.
This affects direct promoted claims as well as the optional room facade.

On an uncertain response, retry the **same** Agent, Todo, revision and key.
Changing intent with the same key is rejected. `already_applied` returns
historical acceptance; it does not renew or acquire a lease. The receipt reports
`current_owner` and `current_claim_matches_actor`, and every result declares
`execution_authority_granted=false`. Execution still needs current quota,
applicable gates and, in hard-lease mode, a separate current lease acquisition.

A claim result card is the message identity of the one accepted operation. It
carries immutable acceptance facts only (Goal, Agent, task, acceptance,
receipt id); the live revision, claimable counts and current ownership stay in
the separate `work project` orientation and the machine readback, never in
this identity. All room-facing cards — the scoped offer card, the committed
confirmation, the honest unconfirmed hint and every recovery patch — use the
Card 2.0 schema (`schema: "2.0"` with `body.elements`): Lark rejects updating
an existing card with a payload of a different schema, so the first update
and the committed-receipt recovery share one structure that always matches
the original card. The same 2.0 confirmation is used for the CLI's
new-message delivery, keeping one card builder for every room write.
Dynamic identifiers interpolated into the Markdown element (Goal id, Agent
id, Todo id, receipt id, revision) are escaped with the existing card-text
rules, so CommonMark rendering always shows them literally — a legal Agent id
like `agent-_ops_` never loses its underscores to emphasis parsing. The provider idempotency key is likewise derived from the
claim identity (Goal, Todo, Agent, original key), not from card content, so
an unrelated Todo write between the original send and its recovery cannot
mint a second key. Recovery checks the original send first — the transport
matches the rebuilt immutable card against exact Bot history under the same
key — and only an unmatched operation performs an external write. Readers
therefore get exactly one readable card per accepted claim.

Check current canonical ownership through the existing direct CLI:

```sh
loopx --registry .loopx/registry.json --format json todo list --goal-id room-goal
```

`canonical_claim_accepted` and `readback_verified` are separate facts. If a
claim committed but room send/readback failed, the result retains acceptance
and reports a recovery requirement. Unknown sends are conservatively reported
as possible external writes. Repeating the same semantic card uses exact Bot
history and provider idempotency rather than minting a new claim. Outages do
not queue unbounded writes or fall back to Markdown authority. After reconnect,
resolve the current binding and canonical revision again; a revoked connection
or identity cannot use an old receipt to resume this facade.

## Offer a claim in the room

A trusted local Agent CLI may offer exactly one revision-bound `claim_todo`
interaction to explicitly named Lark principals. This is a per-request grant,
not room membership, remote authentication, an execution lease or a remembered
approval. The broker runtime must still route the Goal to the same source
registry and authority root when a callback arrives. This local facade supports
the existing legacy registry profile. It rejects source-session exact-instance
profiles until the canonical claim wire is explicitly bound to their GoalRef;
an older offer cannot inherit a recreated instance.

```sh
loopx --registry .loopx/registry.json --format json goal-channel work offer \
  --goal-id room-goal --agent-id agent-a --todo-id todo_example \
  --expected-revision '<source_revision>' --idempotency-key room-offer-example \
  --principal lark:ou_example --expires-at '<RFC3339 expiry>'
```

Preview does not persist a grant, send a card or claim work. Add `--execute` to
persist the bounded private offer and publish its confirmation button through
the verified Goal Channel. The existing collector's explicit v1
`operation_callbacks.enabled=true` configuration is required to consume
`card.action.trigger`; offering a card does not start or enable that collector.
Existing operation callbacks and CLI `project|claim` defaults remain unchanged.

The incoming principal must pass current provider tenant/member verification
and the offer's explicit scope. The callback also rechecks Bot/profile,
originating message and exact action-card content, current Goal source route,
Agent registration, channel binding and expiry. Actor, Todo, revision and key
come from the private offer, never from incoming fields or memory. The canonical
Todo transaction then owns the claim and idempotent receipt; the result replaces
the initiating card after independent provider readback. A callback ACK alone
is not an accepted claim. No claim callback acquires or renews execution authority.
Once the canonical claim is committed, its receipt id is published on the
originating card from the immutable acceptance facts alone: a later live
projection read failure cannot replace that card with a receipt-less summary.
Without a confirmed accepted receipt, the card keeps the honest unconfirmed
hint instead of fabricating a receipt number.

The original offer expiry also travels to the canonical owner as `admit_before`
in claim request v4. It retains v3's broker/source guards. After provider and
source reads, the native Todo owner uses its current clock immediately before
dispatching the new claim's CAS; equality with the deadline is expired. The
provider may finish persistence later. A deadline rejection leaves ownership,
revision, operation receipt, offer and room message unchanged. An already
committed native receipt is historical acceptance, not a renewed offer or
execution lease; the callback still applies its current offer admission.
Old receivers reject v4, and v0/v1/v2/v3 reject the deadline field rather than
silently ignoring it. Untimed direct CLI claims retain their existing wire and
behavior. This extends the existing claim owner, not a new permission service.

The original broker/source witnesses survive remote member/card reads and the
claim handoff. A route replacement at any of those stages leaves the old-source
Todo, provider revision, exact operation receipt and room messages unchanged.
Normal registry administration serializes with the native claim commit; the
effect runtime owns those guards independently of the Python transport caller.

Revoke that one offer through the same trusted CLI:

```sh
loopx --registry .loopx/registry.json --format json goal-channel work revoke \
  --goal-id room-goal --agent-id agent-a --request-id '<request_id>' --execute
```

Revocation, actor removal, channel retargeting, authority-route replacement and
expiry fail closed on subsequent callbacks, including replay. Revoke serializes
with dispatch for that offer. It does not undo a historical accepted claim.
Private offer files preserve intent/scope and message delivery metadata, not an
independently advanceable copy of Todo state. On restart the existing collector
may recover a recorded public result card; recovery only patches/reads that
message and never executes another canonical claim. The current binding and
offer scope still govern recovery. Permanently ineligible records — revoked,
expired or already delivered — are rechecked under the record lock and never
consume a recovery delivery slot, so a dead prefix in the fixed scan order
cannot starve the still-valid receipts queued behind it. The per-round
transport budget also rotates across that order: each round starts after the
last record that spent a slot in the previous round, so a persistently
failing prefix cannot monopolize recovery and every eligible record is
reached within a bounded number of rounds. The rotation cursor is keyed by
the collector's effective delivery scope (Bot app/profile and allowed
chats): collectors with different room scopes may share one runtime
directory, and each keeps its own rotation instead of overwriting the
other's progress. An unresolved transport failure
is never terminal — its record, card and receipt evidence are preserved and
retried on later rounds; only a verified delivery readback exits the queue.

Validation uses synthetic Lark transport and real disposable File/SQLite
providers, including source CLI preview and the production collector dispatch
entrypoint. This implements the callback path in code; native Lark rendering,
console/listener setup and independent-host live qualification remain separate.

## Private read-only reconnect context

`work resume` composes the existing private
[Agent Turn Recall](../../loopx/capabilities/agent_turn_recall/README.md) path with
current channel identity, canonical projection and quota readback. Use the exact
admitted quota packet saved by the host; the command does not admit another Turn:

```sh
loopx --registry .loopx/registry.json --format json goal-channel work resume \
  --goal-id room-goal --agent-id agent-a --turn-instance-id '<admitted-turn-id>' \
  --quota-decision-json .local/admitted-quota.json
```

Native discovery can bind the selected Todo later in the **same Turn**, yielding
an `upgraded` receipt and then `replayed` on reread. Resume accepts those native
outcomes, checks the original persisted event and settlement binding, and reads
current quota for that exact Todo/Turn. It neither replaces the binding nor
records another guard. If the work requires host capabilities, repeat current
`--available-capability` flags as for `quota should-run`; the saved packet does
not establish their present availability. The existing native capability owner
also reads current host observations; omitting a flag does not revoke a
still-valid observation. A changed native capability scope, revoked connection
or mismatched receipt fails before retrieval.

Preview performs no provider retrieval, receipt write, claim or room send.
Add `--execute` to verify the current Bot identity and restore scoped context.
The existing Reward Memory configuration and Agent enablement receipts remain
the configuration owner; this command never enables or broadens recall. It
explicitly skips pending memory-ingest reconciliation even when ordinary
automatic recall enables that behavior. Normal automatic recall retains its
existing default. Retrieval may write the existing private local recall receipt;
it performs no memory-provider write, quota spend, lease renewal or room delivery.

Restore always retrieves anew rather than trusting an earlier same-Turn context
receipt. It checks current source route, Agent registration, exact channel,
Bot identity and configured memory scope, then reads canonical state and quota.
After retrieval it checks those observations again. A changed binding, scope,
Todo revision, selected work or gate discards the context and references. A
failed scope/readback check also omits every earlier quota/projection snapshot;
those observations are not returned as current. A healthy fresh observation
with no selected work can still show the current gate while discarding context.
A provider outage or disabled configuration leaves the fresh LoopX observations
available with `status=context_unavailable`; it does not manufacture a user gate.
Exact-instance source-session profiles remain unsupported and fail closed.
The current direct canonical Todo facade is profile-gated too: its witnessed
claim wire and provider receipts identify `goal_id`, without an exact GoalRef.
Supporting that profile must first bind the existing Todo head/receipt owner to
the lifetime contract; a room adapter cannot bypass the codec gate or substitute
provider incarnation/revision for Goal identity. See the
[Goal-instance owner boundary](../architecture/rfcs/goal-instance-identity-and-orphan-recovery-v0.md#roadmap-placement-and-contract-cooperation).
The current Goal-id claim wire now rejects any explicit `goal_ref` property,
including null, before provider or old-receipt access. Previously that field was
ignored, so both a fresh claim and a same-key replay could look lifetime-bound
without actually checking the instance. Existing callers that omit the field
retain their behavior; supplying an unqualified lifetime reference is a
machine-enforced protocol failure, not optional guidance or instance support.

To carry an inspected reference, repeat `--artifact-ref '<viking://...>'` up to
eight times. Only explicitly requested references inside the current configured
read scope and covered by the current accepted recall's verified application
receipt are returned. Unknown, expired or out-of-scope references are omitted.
This stage carries pointers to scoped retrieved record artifacts; it does not
qualify arbitrary external artifact targets or fetch them through a new provider.
A pointer never grants access to its target or proves completion.

The JSON result has `visibility=private`, `private_context`, bounded
`artifact_references`, fresh `current_quota` and content-minimal `work_projection`.
Do not copy that private packet into a room, public evidence or shared logs.
Remembered approvals cannot satisfy current gates, and restored context grants
no execution authority. Reconcile an uncertain prior claim separately with the
original `work claim` tuple/key; resume itself never replays a work command.

## Remaining qualification

The new checks use synthetic Lark transport and real disposable File/SQLite
stores. Two independent source CLI processes, each served by a separate TS
runtime, compete at the same canonical revision. One accepts the claim; a new
client/runtime recovers its exact receipt without another transition or card.
A separate direct CLI reads current ownership. This proves the supported local
process boundary; each client still uses the trusted local Agent identity model.
They are not evidence of a real room, two independently authenticated hosts,
a live daemon reconnect, or OpenViking provider qualification. Those checks need
an explicitly authorized non-production room, actor bindings and read-only
resource scope. Existing shared authority and provisioning tasks retain their
ownership; this stage does not promote or deploy them.

Source-session activation is a later owner boundary, not a prerequisite for
qualifying this legacy-profile local stage. Its lifetime-bound head and
operation receipts, retirement serialization and old-writer fencing belong to
the existing [shared authority](../architecture/rfcs/shared-goal-authority-state-provider-v0.md)
and [Goal-instance](../architecture/rfcs/goal-instance-identity-and-orphan-recovery-v0.md)
contracts. Keep that profile closed until those owners qualify the full path;
adding a GoalRef to a room request cannot provide that qualification.

To disable publication, stop issuing the optional `work ... --execute` commands
or disconnect the exact connection through the existing Goal Channel connection
owner. This does not delete canonical claims or retract their receipts. Revert
this change to remove the optional CLI facade; existing direct Todo CLI and
Goal Channel configuration remain usable.

## 中文边界

领取回调通过 v4 请求把原 offer 的 `expires_at` 作为 `admit_before` 传给既有
canonical Todo owner，并保留 v3 的来源守卫。原生 owner 在 provider／来源读取后、
派发新领取 CAS 前读取当前时钟；到达期限即拒绝，存储完成时间可以更晚。拒绝不改变
归属、revision、操作回执、offer 或房间消息。既有回执只证明历史接受，不延长 offer
或执行租约；回调仍检查当前 offer 准入。旧 receiver 拒绝 v4，旧 v0/v1/v2/v3 请求
拒绝夹带期限字段；不带期限的直接 CLI 领取行为保持不变。v2 仍是主干上的受管执行
领取请求，本入口不使用它。

本阶段组合已有的 Agent CLI、Goal Channel 与 File/SQLite Todo 权威。默认仅预览；
显式 `--execute` 才请求领取并发布房间回执。没有新增配置 owner 或前端开关，
不自动发送卡片，也不把群成员、卡片、投递或记忆当作执行权限。

竞争领取在权威事务内校验 revision；相同 key 的重试恢复历史接受结果，不能续租。
当前 owner、历史回执与消息读回分别呈现。领取结果卡只承载一次已接受操作的不可变
事实（Goal、Agent、任务、接受状态、回执 id），provider 幂等键同样由领取身份而非
卡内容派生；无关待办变动不能在恢复时生成第二个键或第二张卡，恢复先按原键核对
原发送，仅未匹配的操作才外发。实时 revision、可领数量与当前归属继续由独立的
`work project` 投影与机器读回提供。领取成功但消息失败时保留接受事实；
重新连接后复核当前身份、连接、权威、quota 和 lease。绑定给其他 Agent 的任务会被拒绝，
这一修复也适用于直接的 promoted Todo claim。

这里的 legacy-profile 仅指 Goal 身份兼容，不恢复已退役的 ownership mode 或未迁移的
Markdown 写入。正常领取继续服从 canonical owner 的当前策略与准入；该入口不迁移策略，
也不提供执行所需的 lease proof。策略迁移仍由已有的 [handoff mode](handoff-mode.md) 入口负责。

公开卡片仅包含标识、计数与安全回执，不携带任务正文、证据、artifact 或记忆内容。
canonical 领取提交后，原卡上的回执号直接由不可变接受事实构造，后续实时投影读失败
不会把它替换成无回执的摘要卡；未确认接受时保留诚实的未确认提示，不伪造回执号。
所有房间卡片——受限 offer 卡、已提交确认、未确认提示与每次恢复补丁——统一使用
Card 2.0 结构（`schema: "2.0"` 加 `body.elements`）：飞书拒绝以不同 schema 就地更新
既有卡片，首次更新与已提交回执的恢复因此共用同一结构并始终与原卡匹配；CLI 新消息
投递同样使用这份 2.0 确认卡。插入 Markdown 元素的动态标识（Goal、Agent、Todo、
回执号与 revision）按既有 card-text 规则转义，CommonMark 渲染始终保留字面原串——
合法的 `agent-_ops_` 不会因强调解析丢失下划线。
已提供单次授权的 IM 领取按钮、显式撤销和结果卡片恢复；默认不启用 collector。
恢复在记录锁内复核资格，已撤销、已过期或已送达的记录不消耗投递名额，固定扫描
顺序中的失效前缀不会饿死排在后面的有效回执。每轮投递预算还在该顺序上轮转：新一轮
从上一轮最后一个消耗名额的记录之后开始，持续失败的前缀无法垄断恢复，每条有资格的
记录都会在有限轮次内被轮到。轮转游标按 collector 的有效投递范围（Bot 应用／profile
与允许房间）隔离：不同房间范围的 collector 可以共用同一运行目录，各自保留自己的
轮转进度，互不覆盖。传输失败不是终态——记录、卡片与回执证据全部保留并在
后续轮次重试，只有经读回确认的投递才退出队列。
真实房间、多主机、daemon reconnect、授权 artifact 引用和 OpenViking 只读检索仍待联调。
合成 Lark transport 加真实隔离权威的测试不能宣称三方 RFC 已完成。
