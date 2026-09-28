# 显式 Todo 接续：阶段 A

- **RFC 状态：** Accepted（阶段 A 已在 #4094 合入；历史文件名有意保留）
- **替代 / 关闭：** 无

## 交付状态与整合方向（2026-09-13）

Stage A 已在 [#4094](https://github.com/huangruiteng/loopx/pull/4094) 以 `2ebd921ee989f7c696a7214ba1176d3bd5de6fb3` 合并。历史文件名不表示通用 memory substrate 已交付。[强能力管家与语义交接 RFC](capable-manager-semantic-handoff-v0.zh-CN.md#513-整合已交付的显式接续4094) 将本实现纳入 M2/M3 重构范围。后继方案仍是提案；替代路径验收前，本文继续作为已交付 CLI 兼容性及权威边界参考。

复用 rich/legacy context、既有 note validator 与 claim transfer 边界。接收方接受建议不同于 `handoff adopt` 修改所有权。Stage A note 是可覆盖的当前 Todo 状态，不是私有不可变历史。后继方案将有权限的上下文映射为可恢复 brief，引用当前工作，补齐通用 assessment/result/自动回传关系，不新增 memory ledger 或复制 claim authority。迁移期间 CLI `prepare/inspect/adopt` 保持可用；前端/飞书及自动宿主续接单独验收。同机、注册 Agent 限制继续适用于此 adapter，不约束所有通用请求；带租约工作使用下述显式转移路径。映射、迁移条件和保留负例见后继 §5.13。

后继 [§5.7](capable-manager-semantic-handoff-v0.zh-CN.md#57-会话与产品连续性) 明确区分原会话恢复、同 Agent 换 session、跨 Agent 接管，只有后者可能需要本 adapter 的所有权变更。自动 brief 捕获、来源消失恢复、执行 session fencing、回原入口报结论是明确的后续集成工作；可选 Obelisk recall 只补缺失历史证据，不替代 transfer grant。

语言镜像：[English](cross-session-memory-substrate-v0.md)。

本阶段替代原通用 memory substrate 方案，基于 canonical Todo authority
交付跨注册 agent 的接续工作流。不冻结通用 memory v0。

## 问题：coding agent 中途切换

当开发者从一个 coding agent 切换到另一个（如 Claude Code 切换到 Codex）时，
目标 agent 对已完成的工作、尝试过哪些方案为什么失败、接下来该做什么一无所知，
开发者必须重新解释整个项目状态。本工作流让源 agent 以结构化方式倾倒完整工作上下文，
使目标 agent 无需重新解释即可接续。

阶段 A 解决**同机器、不同注册 agent** 场景：源端准备丰富的接续说明，目标端
inspect、验证并 adopt Todo。不实现自动上下文捕获、agent 无关协议或零配置接入，
这些需要完全不同的产品层，不在本控制平面原语范围内。

## 实现分工

当前状态、稳定 Todo ID、revision 和 claim/lease 仍归现有 Todo coordination
边界所有；持久化复用选定的 canonical File/SQLite authority。CLI 只做宿主适配，状态规则由
TypeScript 执行。不新增 capability、数据库、memory store、索引、发现、恢复
服务或所有权协议。历史检索与长期记忆复用可选 `decision_context` /
`agent_turn_recall` provider；本流程不调用它们，也不依赖它们可用。

## 可运行入口和范围

需要已显式晋升的 canonical authority，以及注册 Agent 持有的 open、active Todo。
无租约接力继续复用原 claim 事务。带租约工作必须提供当前执行 key/version，
所有权通过既有 `task-lease transfer --transfer-claim` 原子转移；本入口不晋升
Goal、不改 handoff mode、不回退到 Markdown。

用户在同一宿主上显式 handoff 给另一个注册 agent。源会话将 revision 保护的
接续说明（传统 rationale 或 rich context）写入现有 Todo note；目标会话读取
当前 authority，验证本地 artifact 可用性，并通过现有 claim 交易接续，附带
当前 authority readback。停止源会话执行后再接续目标。session ID 只记录来源，
不是身份认证或会话租约。此阶段不自动创建目标会话。

以下沿用现有 CLI 的 registry/runtime 参数，并替换为确实存在的标识：

```sh
# 源会话读取 provider_revision。
loopx --format json handoff inspect --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id source-session --workspace .

# 将返回的精确 revision 填入 REVISION，从 JSON 文件读取 rich handoff context。
loopx handoff prepare --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id source-session --operation-id handoff-1 \
  --expected-revision "$REVISION" --from-context ./handoff-context.json

# 或使用传统紧凑形式（rationale + source references）：
loopx handoff prepare --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id source-session --operation-id handoff-1 \
  --expected-revision "$REVISION" --rationale '选择窄改动以保留现有权限边界' \
  --source-ref 'artifact:decision.md'

# 目标会话以接收 agent 身份重读当前状态及本地 artifact 可用性。
loopx handoff inspect --goal-id demo --todo-id todo_a \
  --agent-id agent-b --session-id target-session --workspace . --artifact decision.md

# 目标会话以可读的 handoff digest 格式 inspect（任意注册 agent、任意 session）。
loopx --format json handoff inspect --goal-id demo --todo-id todo_a \
  --agent-id agent-a --session-id target-session --workspace . --format digest

# REVISION 改为目标 inspect 返回值，调用既有 claim 并回读。
# --target-agent-id 指定接收 agent（默认与 --agent-id 相同）。
loopx handoff adopt --goal-id demo --todo-id todo_a \
  --agent-id agent-b --session-id target-session --operation-id resume-1 \
  --expected-revision "$REVISION" --target-agent-id agent-b --workspace . --artifact decision.md
```

inspect 返回 goal/Todo/revision、决定理由、来源指针、本地 workspace/artifact
可用性及来自当前 Todo 的下一步。使用 `--format digest` 时，inspect 渲染可读的
handoff 摘要，任何目标 agent 都能直接消费，包括 work summary、尝试过的方案、
下一步、涉及文件、关键决策和未解决问题。来源引用由源侧提供，不代表远端内容已验证；
artifact 检查证明文件存在，不证明内容完整。同 owner 的 claim 可以合法 no-op，
但仍必须调用既有边界并回读，不能据文本新建 Todo 或凭空产生 lease。

## Rich handoff context

`--from-context` 标志接受包含结构化 handoff context 的 JSON 文件：

```json
{
  "work_summary": "实现带 typed transfer grant 的跨 agent handoff。需要强制 note 不变性。",
  "approaches_tried": [
    {"approach": "只用 hash 的无结构 note", "outcome": "failed", "reason": "任何调用方都能计算 public facts"},
    {"approach": "无 note 校验的 typed transfer_grant", "outcome": "partial", "reason": "仍接受任意 JSON note"}
  ],
  "next_steps": ["添加共享 validateContinuationNote predicate", "添加 regression test"],
  "files_touched": [
    {"path": "todo_claim.ts", "action": "edited", "summary": "引入 validateContinuationNote"},
    {"path": "todo_continuation.ts", "action": "edited", "summary": "adopt 使用 noteValidation.noteFacts"}
  ],
  "key_decisions": [
    {"decision": "Typed transfer_grant", "rationale": "绑定 source/target/todo/revision/note"}
  ],
  "open_questions": ["Stage B 是否需要支持 agent 无关的 handoff？"]
}
```

字段说明：
- `work_summary`（最多 2000 字符）：已完成工作的主要描述及原因。
  `work_summary` 与 `rationale` 至少提供一个。
- `approaches_tried`（最多 20 条）：尝试的方案、结果（`success`、`partial`、
  `failed`）及原因。
- `next_steps`（最多 20 条）：目标 agent 接下来应做什么。
- `files_touched`（最多 50 条）：读取/编辑/创建/删除的文件，附可选摘要。
- `key_decisions`（最多 20 条）：关键决策及理由。
- `open_questions`（最多 20 条）：目标 agent 需解决的未决问题。
- 传统兼容：`rationale`（最多 600 字符）和 `source_refs`（最多 20 条）仍受支持。

## 产生、缺失、过期和失败

只有显式 prepare 事件让源 agent 写记录；上下文以一次带 revision 条件的更新
写入现有 Todo note，替换当前说明，不追加历史。内部 marker 和执行事实指纹只服务
此流程，不是面向未来所有场景的公共 memory schema。

说明缺失、损坏或被覆盖时仍能 inspect，但拒绝 adopt，需源侧重新 prepare。
Todo 执行事实改变时，读取方判定说明过期；完成、归档或 owner 改变直接拒绝。
源侧重新 prepare 替换旧说明，没有后台过期线程。即使 Todo 没变，只要 inspect
后 provider revision 改变，新的 adopt 也拒绝，必须重读并重新判断。

正常重启从既有 file authority 读取。写入失败保留原 note；写入成功但响应丢失，
复用原 operation ID 和 expected revision，通过既有 receipt 回读。历史 receipt
不能替代当前 open 状态、owner、note 和 revision 的确认。artifact 缺失不会
触发状态写入。仅在 `ok` 和 `current_authority_verified` 都为 true 后继续执行。

没有额外启用开关；停止调用即可停用，过时说明可用 `loopx todo update --note ...`
替换。普通 Todo/claim 默认行为不变；cross-agent transfer 仅由 handoff 流程
通过 typed transfer grant 发起，普通 claim 无法构造该 grant。说明与引用沿用现有
Todo 投影的可见边界，没有独立 memory ACL；不得写入凭据或原始私密对话。

## 验证与后续

薄测试使用隔离真实 file authority，分别运行源、目标 Python CLI 进程，覆盖
正常重启、丢失确认、写入失败、artifact 缺失、revision 改变、他人接管、已完成、
rich context 及传统向后兼容等场景。既有 claim/update 回归覆盖默认行为与缺失、过期、错误执行证明的拒绝。

```sh
node --experimental-strip-types --test tests/control_plane_ts/todo_continuation.test.ts
```

共享的 `continuation_note` 模块是接续说明校验的唯一真相源。handoff helper
（`todo_continuation.ts`）和最终 claim authority（`todo_claim.ts`）都使用
`validateContinuationNote`，强制 typed `loopx-explicit-continuation` marker、
有界字段、source session 和当前 `todo_facts`。仅 hash 匹配的任意 JSON note 因
缺少 typed invariant 而被拒绝。

跨 agent transfer 已通过 typed transfer grant 在本交付中实现，由 handoff 流程
（prepare/inspect/adopt）独占发起，普通 claim 无法构造该 grant。带租约 Todo 的转移由既有 task-lease lifecycle 所有，adopt 不复制这份权限。


## 带租约的接力与显示恢复

完整可运行命令见[英文镜像的租约流程](cross-session-memory-substrate-v0.md#leased-execution-continuation)。
源 Agent 先凭当前 `--task-lease-idempotency-key` / `--task-lease-expected-version`
和 provider revision 准备上下文，再执行 `task-lease transfer --transfer-claim`。
接收 Agent 用新 key/version inspect，取回当前 revision 后 adopt。版本号必须来自实际读回。

- inspect 无执行证明也能读取上下文，但带租约工作不会得到 `can_adopt=true`。
- 原子 transfer 只延续转移前仍有效的 note，并在同一 CAS 内重绑定 owner 摘要。
  已过时的 note、变化的任务要求不会被重新认证；普通 metadata 写入不能重绑定。
- 带租约 adopt 记录绑定 Agent、session、note、revision 和执行证明的 receipt，
  不申请、续期或转移租约。返回 `adoption.changed=false`；无租约路径保留 `claim`。
  当前权限必须重新读回，历史回执不能越过过期/释放的租约、换主、任务变化或验收 hold。
- prepare/adopt 将当前 canonical Todo 投递至既有 Markdown 投影。显示失败保留
  已提交结果，返回 `projection_delivery=pending`、`retry_business_mutation=false`。
  修复本地显示输入后可重试同一操作，或运行 `todo project-markdown`；inspect 不写投影。

session ID 仍是来源说明，不是宿主认证或外部工具授权。这是显式 CLI 接力，
没有新增前端/飞书入口、自动启动目标会话或自动 manager/worker 派工能力。
