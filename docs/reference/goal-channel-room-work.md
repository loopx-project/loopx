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
claim transaction; the registry witness is rechecked by the typed owner.

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
offer scope still govern recovery.

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

本阶段组合已有的 Agent CLI、Goal Channel 与 File/SQLite Todo 权威。默认仅预览；
显式 `--execute` 才请求领取并发布房间回执。没有新增配置 owner 或前端开关，
不自动发送卡片，也不把群成员、卡片、投递或记忆当作执行权限。

竞争领取在权威事务内校验 revision；相同 key 的重试恢复历史接受结果，不能续租。
当前 owner、历史回执与消息读回分别呈现。领取成功但消息失败时保留接受事实；
重新连接后复核当前身份、连接、权威、quota 和 lease。绑定给其他 Agent 的任务会被拒绝，
这一修复也适用于直接的 promoted Todo claim。

公开卡片仅包含标识、计数与安全回执，不携带任务正文、证据、artifact 或记忆内容。
已提供单次授权的 IM 领取按钮、显式撤销和结果卡片恢复；默认不启用 collector。
真实房间、多主机、daemon reconnect、授权 artifact 引用和 OpenViking 只读检索仍待联调。
合成 Lark transport 加真实隔离权威的测试不能宣称三方 RFC 已完成。
