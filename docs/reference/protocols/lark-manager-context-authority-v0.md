# Lark Manager Context and Turn Authority v0

## English

A LoopX Manager connection separates **message visibility** from **Turn
authority**. When exactly one enabled Manager binding owns a Lark App and group,
LoopX may retain compact non-self messages from that group as local-private
context. Retention does not start a model call, send a reply or reaction,
acknowledge the provider event, or authorize any Goal/Todo mutation.

This is an early Lark adapter slice for M3/A10 of the
[capable-manager semantic-handoff RFC](../../architecture/rfcs/capable-manager-semantic-handoff-v0.md).
A context-only item is an adapter-owned Observation/material, not a WorkRequest,
Assessment, Todo, or delivery receipt. The Lark inbox and material-review ledger
do not own generic collaboration lifecycle state and must later project the M2
request/result contract rather than becoming a second request database.

A Manager Turn is authorized only by a provider-native mention of the bound Bot,
a provider-verified reply to that Bot, or another existing typed authority
record. The next authorized Turn may receive up to eight recent context-only
messages with a 4,000-character content budget. Every item is labeled
`context-only`; the prompt explicitly states that these items are not commands,
authorization, or independent Todos.

Before an authorized Manager Turn reads that context, the existing bounded
turn-start history sync fills gaps left by the live event subscription. Items
recovered from history are always marked `context-only`, including old messages
that originally mentioned the Bot: catch-up never replays a missed Turn. This
recovery uses the same private cursor and inbox, performs no history-message
reaction or reply, and degrades without blocking the current authorized Turn if
the provider history read is unavailable.

Provider addressing is preserved as historical provenance while normalized live
attention/reply flags are cleared. The generic urgency projection and the Lark
material-settlement path therefore agree that a recovered mention is material,
not a delayed request.

History and live capture retain the provider's exact bounded sender identifier
and sender kind in the private event. Conversation context includes those facts
alongside the source message identifier and time, as structured data. Missing
sender identity stays missing; a malformed identity is rejected, and replaying
the same message cannot replace its captured sender. Sender provenance is not a
verified LoopX Agent binding or an owner grant. Public mutation/evidence receipts
continue to exclude these private identities. Retaining a Bot notification as
context still does not prove steward intake, work adoption or owner presentation.

After a successful authorized Turn and verified reply, consumed context items
are settled through the existing event-bound material-review ledger. Duplicate
delivery and restart recovery remain idempotent. Self messages, another chat,
invalid routing, and ambiguous Manager bindings remain closed and are not
captured.

The connection health projection distinguishes `context_only_captured` from
`replied_and_acknowledged`. CLI/managed Turn, frontend, and Lark must reuse this
single runtime inbox and receipt model; adapters must not invent a second
authority source.

The eight-item / 4,000-character limits bound one Turn projection, not durable
content retention. A separate adapter-owned retention/expiry/compaction change is
still required before this slice can claim long-running M3/A10 acceptance; any
discard must expose a reason and preserve duplicate/restart safety.

## 中文

LoopX 管家连接将**消息可见性**与 **Turn 权限**分开处理。当且仅当一个启用的
管家绑定唯一拥有某个 Lark App 与群聊时，LoopX 可以把该群中非本机器人发送的消息以
紧凑、本地私有的上下文材料保留下来。仅保留消息不会调用模型、发送回复或
reaction、确认 provider event，也不会授权任何 Goal/Todo 修改。

这是[强能力管家与语义工作交接 RFC](../../architecture/rfcs/capable-manager-semantic-handoff-v0.zh-CN.md)
下 M3/A10 的早期 Lark adapter 切片。`context-only` 项是 adapter owner 管理的
Observation/材料，不是 WorkRequest、Assessment、Todo 或送达回执。Lark inbox 与
material-review ledger 不拥有通用协作生命周期；后续应投影 M2 的 request/result
契约，不能变成第二套请求数据库。

只有以下来源能够授权管家 Turn：provider 原生的目标机器人 mention、provider
验证过的对机器人回复，或其他既有 typed authority 记录。下一次获得授权的 Turn
最多读取最近八条、正文总计不超过 4,000 字符的仅上下文消息。每条材料都会标记为
`context-only`，prompt 也会明确说明这些内容不是指令、授权或独立 Todo。

在已授权的管家 Turn 读取上下文前，既有的有界 turn-start 历史同步会补齐实时
事件订阅遗漏的消息。所有历史补采项一律标记为 `context-only`；即使旧消息原本
真正 mention 了机器人，也不得借补采重放成一个 Turn。补采复用同一私有游标和
inbox，不给历史消息发送 reaction 或回复；provider 历史读取不可用时，会准确
降级但不阻塞当前已授权 Turn。

provider 的原始寻址信息作为历史 provenance 保留，但 live attention/reply 标志会被
清除，因此通用 urgency 投影与 Lark material settlement 对“历史 mention 只是材料”
得出同一个结论，不会把它恢复为延迟请求。

历史补读与实时采集在私有事件中保留 provider 的精确、有界发送者编号与类型。对话上下文
以结构化数据同时提供这些事实、来源消息编号和时间。未知身份保持未知；损坏身份会被
拒绝，同一消息重放不能替换已捕获的发送者。来源身份不等于已核验的 LoopX Agent 绑定
或用户授权；公开 mutation / evidence receipt 仍不包含这些私有身份。Bot 通知被保留为
上下文，也不证明管家接手、工作采用或用户呈现。

获得授权的 Turn 成功完成且回复验证通过后，已使用的上下文材料通过现有的、
绑定事件的 material-review ledger 结算。重复投递与重启恢复保持幂等。机器人
自身消息、其他群聊、无效路由以及多重歧义的管家绑定继续安全关闭且不采集。

连接健康投影会区分 `context_only_captured` 与
`replied_and_acknowledged`。CLI/managed Turn、frontend 与 Lark 必须复用同一份
运行时 inbox 和 receipt 模型；适配器不得另造权限来源。

八条/4,000 字符只约束单次 Turn 投影，不等于持久内容 retention 已有上限。要宣称
长期运行的 M3/A10 验收，仍需 adapter owner 交付 retention/expiry/compaction；任何
丢弃都必须显示原因，并保留重复投递与重启安全。
