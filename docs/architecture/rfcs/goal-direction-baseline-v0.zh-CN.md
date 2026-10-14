# RFC：Goal Direction Baseline（目标方向基线）v0

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** Partial（M1 与显式本地 M2 均为候选；准入和通用启用未验收）
- **作者 / 负责人：** LoopX 维护者与贡献者
- **创建日期：** 2026-09-10
- **最后规范修订：** 2026-10-13
- **实现基线：** `41a95d9`
- **跟踪 Issue：** [#2831](https://github.com/huangruiteng/loopx/issues/2831)
- **相关契约：**
  [Agent Material Frontier](../../reference/protocols/agent-material-frontier-v0.md)、
  [Goal Vision and Replan](../../reference/protocols/goal-vision-replan-contract-v0.md)
  与
  [Shared Goal Alignment and Governed Amendment](./shared-goal-alignment-and-governed-amendment-v0.md)
- **语言镜像：**
  [English](./goal-direction-baseline-v0.md)；中英文语义差异均属于缺陷

## 文档地图与维护契约

第 1-10 节是持久设计与验收契约。第 11 节是规范性交付计划。第 12 节
记录运行时交付前必须决定的问题，其中的建议不等于批准。附录记录非规范性的
执行、决策、证据与 fixture 计划。

RFC 成熟度与交付成熟度互相独立。M1 候选 TypeScript builder 已基于传入的完整 canonical
输入实现 `goal_direction_baseline_v0`。§12.5 的显式本地候选新增来源支持的读取 offer、
同 Agent 确认和 cold 回读；不代表通用运行时启用或模型真实消费，schema 合法仍不充分。
M1 准入仍待 owner 批准第 12 节容量与回执决策；M2 consumer/recovery UX
批准是另一项待完成 gate。实现和测试不授予其中任何批准。

独立的[验收合同 v0](../../reference/goal-acceptance-observations.md#owner-authorized-contract-v0)
不实现本 RFC 的材料声明或使用回执。#2831 的下一切片是**方向材料版本与验收基线关联**，
通过下面的同 Agent/当前版本 fixture 验证。

---

## 1. 决策摘要

本 RFC 提议一个 provider-neutral、只读的 `goal_direction_baseline_v0`
投影。显式启用该能力的 Goal 声明哪些 owner-controlled 材料和 topic 定义其
方向。投影面向单个 Agent，通过现有 canonical Goal authority registry 解析
声明，并验证同一 Agent 是否针对每个当前材料 revision 持有匹配的
`material_usage_receipt_v0`。

材料 revision 变化只产生 Agent-scoped 方向复核信号。它不会重写 Agent
Vision、创建工作、改变共享路线或修订 Goal intent。如果复核实际改变 Agent
路线，现有 `goal_path_delta_v0` 仍是唯一的路线变更记录。authority 缺失、
材料不可访问、未读、receipt 过期以及冲突全部 fail closed。

Repository owner 文档只是可选的材料 provider 之一。它不会成为写 authority，
repository 路径也不是协议。私有 wiki、外部文档系统或其他已注册 provider
可以在不暴露原始内容的情况下满足同一个契约。

M1 只提供纯 read model，不引入 writer、scheduler、状态 authority、自动 Vision
更新或运行时 gate。M2 保留独立的 consumer 与恢复验收。

## 2. 问题与动机

LoopX 当前可以检测执行漂移：过期 Todo basis、已耗尽 frontier、未关闭的
replan obligation 与 lease 冲突。现有 Agent Material Frontier 也能证明某个
Agent 是否读取了已注册材料的某个 revision。但这两类事实尚未被连接成对以下
问题的显式回答：

> 当前 Agent 的方向是否建立在 Goal owner 现在指定为权威的材料 revision
> 之上？

例如，维护者可能已经修订 architecture contract，但 Agent 仍依据旧理解继续。
此时 Todo graph 可以仍然有效，也无需出现 lease 冲突，但 Agent 路线可能已发生
语义偏离。相反，把 repository 文本当作可执行命令会让最后修改文档的人在无意
中成为 Goal authority。

因此最小有用边界是可审计的 read model。它绑定 Goal-owned 声明、当前
provider-neutral 材料元数据与当前 Agent 自己的 usage receipt，只暴露 drift，
而不决定 Agent 应该如何响应。

### 不变量

1. Goal authority 始终是 material id、topic 绑定、boundary、gate、freshness、
   conflict 与 revision 的唯一 owner。
2. Provider 只解析已注册材料，不授予 Goal authority，也不写 Agent Vision。
3. 只有同一 Goal、Agent、material 和 required revision 的 receipt 才能让材料
   成为 current；peer receipt 永不转移。
4. 缺失、不可访问、未读、过期、歧义或冲突输入永远不能投影出 current 方向
   基线。
5. Revision drift 是 Agent-scoped 的观察结果，本身不会创建 Todo、lease、
   scheduler wake、Goal amendment 或 Vision rewrite。
6. 实际路线变化继续使用 `goal_path_delta_v0`；canonical Goal 变化继续使用
   现有 governed authority 路径。
7. 投影与 fixture 不包含原始文档正文、prompt、reasoning、transcript、
   trajectory、credential、private URL 或本地绝对路径。
8. 未显式启用的 Goal 保持当前行为不变。

## 3. 范围与非目标

### 范围内

- Goal-owned 方向 material id 与 topic 声明；
- 经 `authority_registry.project_materials` 和
  `authority_registry.topic_authority` 的确定性解析；
- 单 Agent 作用域的只读 baseline 投影；
- typed current、re-evaluation-required 与 blocked 结果；
- 与现有 Agent material usage receipt 的 exact-revision 匹配；
- 与 Agent Vision、`goal_path_delta_v0` 及 shared Goal amendment 的关系；
- public-safe synthetic drift fixture 计划。

### 非目标

- 把文档文本解析为 Goal intent 或可执行指令；
- 让 repository、wiki、connector 或 provider 自身成为 authority；
- 引入文档 writer、crawler、cache、scheduler 或第二套 material registry；
- 把原始正文或 provider locator 复制进 control-plane state；
- 从 chat、run log、evidence row 或其他 Agent 的 receipt 推断当前 Agent 已阅读；
- 自动 patch Vision、创建 Todo、重排工作或提交 Goal amendment；
- 替代 `agent_material_frontier_v0`、`goal_path_delta_v0` 或 governed Goal
  amendment 契约。

## 4. 当前系统契约

实现基线已有以下相关 owner：

- `authority_registry.project_materials` 持有 canonical material metadata；
- `authority_registry.topic_authority` 把 topic 解析到 material id；
- `agent_material_frontier_v0` 把 Goal authority、Agent/Todo/Vision/handoff
  requirement、boundary、gate 与 `material_usage_receipt_v0` 组合成只读
  Agent 投影；
- 匹配 receipt 具有 Agent 作用域，并在适用时具有 Todo 作用域；handoff 只转移
  有界 material ref，从不转移 predecessor receipt；
- 当 Agent 实际执行路线变化时，`goal_path_delta_v0` 记录 retained、changed
  与 stopped 事实；
- 当前 `shared_goal_alignment_v0` 绑定的是 event-log 或 canonical Todo
  projection 事实，并非 typed canonical Goal-intent revision。

M1 builder 已将声明的方向材料与单个 Agent 的传入回执绑定。§12.5 本地来源 profile
实现声明和 read/ack provenance；其他生产者 profile 与通用启用仍是 M2 依赖，
shared-alignment source digest 或 Markdown 不能替代真实读取来源。

## 5. 提议架构

### 5.1 Ownership 与 authority

显式启用的 Goal 增加 `authority_registry.goal_direction_requirements`。这是
现有 Goal-owned authority registry 的逻辑字段，不是新 registry。它只能通过
authority owner 已有的 governed write boundary 写入。Provider 不能把自己加入
声明。

每条声明包含：

- `requirement_id`：在 Goal 内稳定；
- `material_id` 或 `topic` 二选一且仅有一个；
- `purpose`：说明该来源为何约束方向的有界 public-safe 上下文；
- `required`：布尔值，默认为 `true`。

直接 `material_id` 在 `project_materials` 中解析；`topic` 先通过
`topic_authority` 解析，再把所有引用 id 解析到 `project_materials`。解析得到
按 `material_id` 排序的确定性并集；重复 id 保留全部 `bound_by` requirement
ref。registry 缺失或畸形、id 缺失、空 topic mapping 与矛盾绑定全部 fail
closed。紧凑 authority summary 永远不够。

材料元数据继续由现有 registry 持有。Baseline 只读取以下 public-safe 字段：

- 稳定 `material_id` 与解析出的 topic；
- `role`、`owner_status` 与 `conflict_rule`；
- `boundary` 与 `gate_status`；
- `freshness`；
- 不透明 `revision`。

Revision 是 provider-neutral identity，不是可排序计数器。Provider 可以从
commit、ETag、version id 或 content digest 派生它，但 provider-specific 形式
不进入 core 语义。没有非空 canonical revision 的方向材料不能成为 current。

### 5.2 Read model

提议的投影具有 Agent 作用域：

```json
{
  "schema_version": "goal_direction_baseline_v0",
  "goal_id": "example-goal",
  "agent_id": "agent-reviewer",
  "generated_at": "2026-09-10T00:00:00Z",
  "baseline_digest": "sha256:<canonical-resolution-digest>",
  "direction_state": "re_evaluation_required",
  "reason_codes": ["material_revision_changed"],
  "summary": {
    "required_count": 2,
    "current_count": 1,
    "stale_count": 1,
    "blocked_count": 0
  },
  "items": [
    {
      "material_id": "architecture-owner-contract",
      "bound_by": ["direction:architecture"],
      "required_revision": "rev-7",
      "observed_revision": "rev-6",
      "state": "stale",
      "receipt_ref": "material_receipt:receipt-17"
    }
  ],
  "advisory": {
    "kind": "agent_vision_re_evaluation",
    "creates_work": false,
    "rewrites_vision": false,
    "changes_goal_route": false
  },
  "truth_contract": {
    "authority_is_goal_owned": true,
    "projection_is_read_only": true,
    "receipt_is_agent_scoped": true,
    "provider_is_not_write_authority": true,
    "raw_source_body_recorded": false
  }
}
```

`baseline_digest` 是对 schema version、Goal id、排序后的 requirement binding
与投影使用的排序后 authority-owned metadata 计算的确定性 SHA-256。它排除
`agent_id`、receipt、timestamp、source body 与 provider locator。因此，读取
相同 Goal authority 的两个 Agent 可以共享 baseline identity，同时保留独立
receipt state。该 digest 只证明 basis 相同，不证明语义正确或内容已被使用。

每个 item 复用 Agent Material Frontier 的状态词汇：

1. 声明目标或 revision 缺失：`missing`；
2. boundary 不允许、gate 阻塞或 unavailable receipt：`inaccessible`；
3. 当前 Agent 没有匹配 receipt：`required_unread`；
4. authority freshness 过期、ownership 冲突或 revision 不匹配：`stale`；
5. authority 可访问且无冲突，并存在 exact-revision matching receipt：
   `current`。

当值 public-safe 时，投影可以包含紧凑的 `role`、`owner_status`、
`conflict_rule`、`boundary`、`gate_status` 与 `freshness`，但绝不包含原始
locator、正文、摘录、diff 或展开的 receipt event。

### 5.3 聚合 drift signal

`direction_state` 按以下优先级派生：

1. 任一 required item 为 `missing`、`inaccessible`、`required_unread`、
   freshness-stale、ambiguous 或 conflicted 时为 `blocked`；
2. 没有 blocked item，且至少一份 receipt 的 observed revision 与当前
   authority revision 不同时为 `re_evaluation_required`；
3. 仅当所有 required item 均为 `current` 时为 `current`。

Optional requirement 可以被报告，但不能让 required baseline 变为 current。
未知 state 与未知 conflict policy 按 `blocked` fail closed。`reason_codes` 是
typed token，例如 `authority_registry_missing`、
`direction_requirement_unresolved`、`authority_revision_missing`、
`material_inaccessible`、`material_required_unread`、
`material_revision_changed` 与 `authority_conflict`。消费者不能解析 prose
来判断 drift。

Revision mismatch 是 v0 中唯一会发出 `agent_vision_re_evaluation` advisory
kind 的条件。信号属于所选 `agent_id`，并不表示 peer 已过期。该 Agent 后续
写入 exact-revision receipt 后，新投影可以恢复 current。Receipt replay 继续
遵循现有 receipt identity，且不能在 Agent 之间复制。

### 5.4 与 Vision 和路线变化的关系

Baseline 是 review 输入，不是 Vision writer。未来消费者可以把 advisory 信号
呈现为 Agent-scoped Vision acceptance gap，但不能自动生成 patch，也不能因
scheduler activity 而把 gap 标记为 resolved。Agent 要么记录符合现有契约的
Vision checkpoint，要么通过当前 Vision write boundary 写有界 Vision patch。

读取变更 revision 后仍保留同一路线，无需 path delta。如果 review 改变路线，
写入必须包含现有 `goal_path_delta_v0`，并携带 prior assumption、observed
reality、retained/changed/stopped 事实与 public-safe evidence ref。方向
baseline 不内嵌也不替代该记录。

如果材料揭示 canonical shared Goal intent 本身必须改变，Agent 可以提交现有
governed Goal-amendment proposal。Baseline 不能批准或提交它。

### 5.5 Provider 契约

Core 消费 canonical authority metadata 与 receipt，而不是 provider API。
Provider profile 可以说明如何获得稳定 revision 和判断 availability，但必须
返回相同逻辑字段，并遵守相同 boundary 与 gate 决策。

Repository 文档因此只是一个 profile：

- repository owner 注册稳定 material id 与 topic mapping；
- profile 解析 commit/blob 派生的不透明 revision；
- 在接受 receipt 前判断访问权限；
- 投影既不存 checkout path，也不存文档正文。

相同模型适用于 private wiki 或外部文档服务。Provider 失败时，不得回退到无关
本地文件、Markdown status、chat text 或缓存 prose 后仍报告 `current`。

## 6. 替代方案与设计选择

### 直接解析 repository owner 文档

拒绝。文件路径和 Markdown 约定会成为意外 authority，私有布局会泄露进 core，
非 repository provider 也需要特例。

### 在 Agent Vision 中保存方向文本副本

拒绝。副本会独立漂移、扩大 hot path，并让 owner material 与最新 Agent writer
之间的 authority 归属不清。

### 所有 Agent 共享一份 receipt

拒绝。一个 Agent 的阅读不能证明另一个 Agent 消费了相同 revision，handoff
也不能静默转移理解和权限。

### Revision 变化时自动重写 Vision

拒绝。Revision identity 只证明发生变化，不证明其语义后果。自动重写会把观察
契约变成 planner 与 write authority。

### 复用 shared-alignment source digest 作为方向 identity

拒绝。它们当前标识 event-log 或 Todo projection 事实，不标识 Goal owner 指定
的语义材料。

## 7. 安全、隐私与兼容性

- 能力按 Goal 显式启用。没有声明就不产生 baseline 投影，并保持当前行为。
- Builder 纯函数且只读，不授予 permission、material access、claim、lease、
  scheduler budget 或 amendment authority。
- 在接受 receipt currency 前完成 boundary 与 gate 检查。Boundary 缺失表示
  inaccessible，而不是 public。
- 仅可输出 public-safe id、metadata、digest、typed state 与紧凑 receipt ref。
  排除 source body、locator、本地路径、credential、prompt、reasoning、
  transcript、trajectory 与 raw run log。
- Mixed-version reader 必须忽略缺失的 optional projection。旧 reader 省略
  baseline 时，writer 不得因此创建或修改 baseline state。
- 未知 schema version、state、conflict rule 或畸形 receipt 字段全部 fail
  closed，绝不能被强制转换为 `current`。

## 8. 迁移与回滚

M0 只改文档，不需要数据迁移；回滚只需 revert 文档。

未来实现必须保持 default-off，并经现有 Goal authority owner 引入声明。启用投影
前，admission 必须依据完整 canonical registry read 校验每条 requirement。
回滚同时禁用 reader 与声明 authoring，但不得删除 registry material 或
receipt。因为投影不持有 canonical state，禁用它不能回滚或重写 Agent Vision、
Todo、lease 或 Goal intent。

在证明 mixed-version 行为与 provider failure 能够 fail closed 且不改变默认
scheduler/quota path 之前，任何实现 milestone 都不能 promotion。

## 9. 验证与验收

| 声明 | 测试或证据 | 必须结果 | 边界 / 排除项 |
| --- | --- | --- | --- |
| 提案 public-safe 且内部一致。 | `loopx check --scan-path docs/architecture/rfcs --scan-path docs/development/contributor-tasks.md` | 通过，且无 private-data 或 contract finding。 | 不证明运行时行为。 |
| Exact current revision 具有 Agent 作用域。 | 附录 D 的 synthetic case F1、F3。 | 只有所选 Agent 的匹配 receipt 得到 `current`。 | 不允许 cross-Agent receipt transfer。 |
| Revision 变化暴露 drift 且不修改状态。 | Synthetic case F2。 | `re_evaluation_required`；input、Vision、Todo、lease 与 Goal route byte-identical。 | 不自动解释语义。 |
| 缺失和不可访问 authority fail closed。 | Synthetic case F4、F5。 | `blocked`，含 typed reason code 且零 mutation。 | Provider availability 为模拟值。 |
| 聚合状态确定。 | Synthetic case F6、F7。 | 一个 stale required item 压过 current item；输入不变则结果稳定。 | 不做性能 qualification。 |
| 路线变更仍由现有契约持有。 | Synthetic case F8；未来实现还需 Goal Vision contract tests。 | 路线不变时无 `goal_path_delta_v0`；实际路线变化只经现有契约接受。 | 不批准路线变更本身。 |
| 不投影原始 source material。 | Fixture key allowlist 与 public-boundary scan。 | 无 body、locator、URL、本地路径、prompt、reasoning、transcript、trajectory、credential 或 run-log 字段。 | 允许不透明 id 与 digest。 |

未实现或 skip 的行不算绿色。Live provider qualification 与 production
promotion 不在本 RFC 当前交付成熟度内。

## 10. 运行契约

M0 没有运行时 operational surface。未来实现只能暴露紧凑
`direction_state`、typed reason code、count、baseline digest 与有界 item
ref。Provider error 必须成为 typed blocked reason，不能被吞掉，也不能展开成
source content。Operator 可以重试 read 或修复 canonical authority，但不能
通过修改投影来覆盖 drift。

Read model 大小必须有界。未来 schema 在实现前必须定义最大 requirement 和输出
item 数。Overflow 必须以 typed capacity reason fail closed，不能静默截断
required material。

## 11. 规范性交付计划

| Milestone | 交付行为 | 进入 gate | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M0 | 仅双语 public design note 与 synthetic fixture 计划。 | #2831 上维护者批准的 helper scope。 | 文档检查与 F1-F8 评审。 | Revert 文档。 |
| M1 | 基于完整 Goal authority 和 receipt 的 optional pure builder；无 consumer 或 writer。 | 批准第 12 节决策与 schema 限制。 | 确定性 fixture、mutation check；若双 runtime 消费则做 Python/TypeScript parity；public-boundary scan。 | 移除 default-off builder 与 projection。 |
| M2 | 一个只读 Agent-scoped Vision-gap consumer。 | M1 conformance，并由维护者显式批准 consumer 与 recovery UX。 | 端到端证明 drift 可见、同 revision replay 安静且不修改 Vision/Todo/route。 | 禁用 consumer；保留 canonical registry 与 receipt。 |

M1 候选实现在 [`direction_baseline.ts`](../../../loopx/control_plane/goals/direction_baseline.ts)。
现有 [F2 smoke](../../../examples/goal-direction-baseline-f2-smoke.py) 已改为执行真实
TypeScript 测试与两项源码 mutation，删除原 fixture-local reducer。下方本地 M2 候选
通过显式 cold Goal 操作调用它；polling、scheduler、quota 仍不调用该 builder。

## 12. 开放决策

以下建议**等待 owner 决策，不构成运行时授权**。它们根据附录 F 的源码审计与合成
测量细化现有三项决策；下文的实现证据不等于批准这些建议。

### 12.1 Schema 容量 — Goal authority owner

当前 Material Frontier 限制单个文本字段与 handoff ref，**没有完整 material 或
receipt 数量上限**，因此不存在可直接继承的完整 frontier 数值限制。建议 M1 对
一次所选 Goal/Agent 读取采用以下边界：

| 维度 | 建议上限 | 理由 / 所需验证 |
| --- | --- | --- |
| Declaration row | 64 | 限定 owner 声明的 requirement 数；topic 展开另有限制。 |
| 去重后的 resolved material，含 optional item | 256 | 包含声明的全部 topic 成员和直接 ref，保留每条 binding。 |
| 输入 receipt row | 4,096 | 限定历史检查量；不能任意截取最近一段，隐藏相关 receipt。 |
| 完整输入的紧凑 UTF-8 JSON 字节数 | 2 MiB | 包含完整传入的 authority registry、declaration 和 receipt；数量限制不能约束字符串和 topic 展开量。 |
| 完整投影的紧凑 UTF-8 JSON 字节数 | 256 KiB | 包含全部 binding 与 reason code；数量合规不代表输出大小合规。 |

各项限制独立适用，不承诺所有最大值能同时容纳。紧凑 JSON 指没有无意义空白且
字符串以 UTF-8 原文编码。仍须校验字段。输入或输出越界必须返回 typed capacity
refusal，不能给出可用的局部 baseline 或 `current` 结果。不能截断 material、
binding 或 receipt，不能用紧凑 authority summary 替代完整读取，也不能静默
扩大限制。现有 authority owner 必须提供完整且有界的读取，否则拒绝；加索引
属于另行举证的优化，不是丢弃历史的许可。

M1 进入批准可授权将这些数值作为实现的候选限制。M1 退出前，需在 TypeScript
builder 上验证恰好上限、超出一项、最大字段宽度、重叠 topic、重复 binding、
非法输入和组合边界。附录 F 为初始建议提供依据，不证明生产规模、SLO 或已验证
的最大容量；生产容量冻结仍需单独验证。

### 12.2 Receipt outcome 与来源 — Goal Vision owner

建议保留 `read`、`used`、`verified` 为 exact-revision receipt 的可接受 outcome；
`unavailable` 仍为 inaccessible。这些 outcome 均不证明认同、语义理解、工作
验收或写权限。收紧为仅 `used`/`verified` 需要迁移证据。

M1 决策还必须明确选择与拒绝行为：

- 先校验 schema、identity、outcome、revision 和 timestamp，再选择 receipt；
  非法或未知输入不能被忽略后回退到较早的通过记录；
- 只考虑所选 Goal、Agent 及适用的 Todo scope；其他 Agent 的合法 receipt
  不能满足当前 Agent，也不能复制到其结果中；
- 按 timestamp 选择最新的合格 observation；相同 replay id 的一致记录去重，
  只有同时间 observation 的 scope、outcome 和 revision 一致时才按 receipt-id
  顺序选择。重复 id 内容冲突或同时间 observation 矛盾必须拒绝，不能选择通过记录；
- 较晚的 `unavailable` 不能回退到较早的 `read`；revision 是不透明身份，
  不能作为可排序版本号；
- 保持 replay identity 和输入不变性，包括输入重新排序。

审计基线已有 receipt reader 与合成 fixture，但**没有生产环境的
`material_usage_receipt_v0` writer**。调用方提供 schema 合法的 row，不足以
证明真实 source read。M1 可以使用合成 receipt；M2 需要通过现有 material-read
owner 单独验证 producer，将实际获授权的读取绑定到精确 material revision 和
所选 Agent。存储与读取绑定应复用现有 Goal-instance identity 边界：复用
`goal_id` 或恢复出的 archive 本身不能证明 receipt 所属的生命周期。无法证明
来源的旧记录保持未验证。不能在 pure builder 内另建 receipt store 或静默
扩展 v0 schema。

### 12.3 首个 consumer 与恢复 — control-plane owner

建议从显式指定 Goal/Agent 的 status 进入已有 **agent-management 冷路径读取**，
并在已打包的 Goal 详情交互中呈现同一投影。当前 management 层只在
`material_lifecycle` envelope 下消费预构建的
`status.agent_material_frontiers`，不会读取完整 authority、生成 receipt 或
渲染 direction 详情。这些是明确的 M2 依赖，不是已交付行为。

Direction 加载保持 opt-in，不进入普通 status 轮询、quota 或 scheduler 准入。
复用 Goal/Agent identity 与现有 capability owner，不为该投影另建 capability。
已有 `agent-context` 命令提供 coordinator lifecycle guidance，并非通用
material-read consumer；peer discovery 同样不是该 consumer。

建议 M2 旅程为：选择 Goal 和 Agent → 查看当前 material basis 与缺失/过期
原因 → 通过 owner 执行已有授权的 source read → 刷新同一 Agent 的结果。
打包验证必须覆盖 revision drift、source unavailable、错误 Agent 的 receipt、
reload 和 retry，同时 Vision/Todo/lease/route 状态保持不变。Baseline current
只表示材料读取依据当前有效，不能让全 Goal 验收变绿。Direction 到 acceptance
的关联必须通过现有 acceptance owner 绑定观测 basis，不能改写历史结果。
CLI 与前端必须共享该结果；Lark 在实际交互测试前仍单独保持未验证。

维护者可以批准或调整容量和 receipt 决策，允许仅 TypeScript M1 pure builder，
同时明确暂缓 M2。这项较窄决策不批准 consumer、writer 或 recovery UX；第 11 节
仍要求 M2 显式批准及真实 producer/consumer 验证。

### 12.4 M1 候选实现 profile

纯构建器实现 §12.1 的建议包络和 §12.2 的回执规则。源码/测试不满足 §11 准入
门槛：容量、回执资格和有界元数据 profile 仍须由具名 owner 批准。超限返回
`blocked` / `direction_capacity_exceeded`、空 items 和 null digest，计数不代表
部分 authority。文本限制为 id/revision 180 个 Unicode 字符、topic 120、purpose
220、available boundary 80。调用者 identity/time 无效属于类型化输入错误。
回执时间按纳秒精度排序；内容一致的并列观察取最小 receipt id。

候选不从 prose 推导 authority：仅 `owner_status=reviewed`、
`conflict_rule=owner_wins`、`freshness=current` 和
`gate_status=ready|registered|readable` 可以通过。缺失/未知元数据或 boundary
保持阻塞；public/public_safe 或显式 available boundary 允许本次读取，本身不
授予访问权。Material role 仅提供信息。要求完整 `project_materials` 和
`topic_authority`；map/list identity 和重复声明必须一致。可选项保持可见，但
只有 required 项影响聚合状态/原因。完整输入的非法行即使属于可选项或 peer，
仍拒绝投影。§12.3 的 consumer/回执来源要求保持独立；纯构建器不新增回执
存储或 v0 字段。

### 12.5 显式本地 M2 候选与评审边界

本候选将 §12.3 consumer/recovery 方案落实为可供维护者按 §11 入口门槛评审的实现；
源码与隔离验证不等于批准在活跃 Goal 启用。入口为 `goal-direction` 和既有 Goal 设置。
`goals/direction_authority.ts` 拥有决策/写入，Python `direction_runtime` 只处理来源、
锁和有界文件 IO。这属于 Goal authority，不是材料目录的迁移/排序策略，不增加 capability
或第二个 store；它与既有 Decision Context local-file provider 共用 descriptor-bounded
文件快照 helper。

首个 profile 要求活跃精确 `source_session_v1` Goal、已注册 peer 和已有 File/SQLite
canonical authority。新建使用 `project register --canonical-storage file|sqlite`，
不迁移既有 Goal。若来源 registry 已有材料 authority，必须先由原 owner 显式迁移；
本实现拒绝建立第二份权威。此 profile 的 `authority_registry` 是 canonical Goal head
内同一个逻辑权威对象，并绑定精确实例。原要求、工作、租约和验收保持不变。

Operator 配置也先经过 TS 的活跃实例、唯一 authority 和 provider basis 检查，再读取
来源字节。紧凑旧 authority 摘要不能当作空来源，仍须迁移；活跃 owner 可停用 reader，
这个停止动作不授予新的来源读取权限。

Operator 声明完整本地 Markdown 资料集：稳定 id、用途、topic、required/optional 和
明确的 public/public-safe/local-private 边界。路径是仅用于本次读取的输入，只在既有
Goal repo/state/runtime 根内解析；相对路径固定到 repo，文件缺失不能回退到同名的
state/runtime 副本，其他已授权根须显式绝对路径；authority/journal 不保存原路径或正文；待确认的 operator 表单仅在当前浏览器 tab 的
私有恢复状态保留路径，直至操作已处理。实际有界字节
决定 content revision，只保留 locator digest。声明提交绑定 provider revision；UI 还
固定已检查的 authority digest，预览后文件变化会拒绝。未知/远程私有 provider 不支持。
这是 owner 显式声明的版本跟踪，不是文件 watcher：修改文件后须由 owner 更新声明；
实际读取若与声明字节不符，记录 unavailable，而不是沿用旧成功。

Usage 分两步：

1. **Read。** TS 先检查实例、Agent、声明、来源摘要、版本和边界，再读取实际有界文件。
   正文只临时返回，并附带原操作 offer/delivery digest。Offer 入库不产生 usage receipt，
   丢响应不会令 Agent current；仅当同一版本仍可读取时，重试原操作恢复内容和 offer。
2. **Acknowledge。** 同一 Agent 明确确认该 offer/digest，既有 canonical 事务添加一个
   `outcome=read` 的 `material_usage_receipt_v0`，外层 provenance 绑定实例。不能注入任意
   receipt、使用另一 Agent 的 offer，或确认已被后续可用性观察作废的旧 offer。这证明
   来源支持的读取和明确确认，不证明人/模型理解、路线采纳、`used` 或 `verified`。

后续真实读取失败由同一 owner 记录 unavailable，盖过旧成功。Producer 在 canonical
追加顺序上保持 recorded_at 严格递增，即使宿主时钟相同或回退；这是顺序证据，不是
独立墙钟证明。预览 acknowledge 明确 receipt_recorded=false，不产生记录。修复来源后重新 read/ack，
不能编辑 receipt 让投影变绿。实例退休/重建会 fence 新操作，包括 caller 超时后的迟到
RPC。原操作回执保持历史，不授予当前读取/执行权；peer receipt 不可转移。组合输入容量
在提交前检查，不会为凑预算持久化部分投影。

当前投影达到 4,096 条 usage 时，新观察只能替换**同一 Agent/material** 的一条旧记录。
完整旧 provenance 以 `archived_usage_record` 保存在同一次原子操作的 receipt/journal，
可用该 operation id 的 `receipt` 冷回读，原操作回执保持不变。这个限额约束当前投影，
不代表 journal 历史或 provider 自身容量无限。满额时若该 pair 没有旧记录则拒绝，
不挤掉其他 pair，未读 pair 继续 blocked。新 offer 绑定同 pair 完整 retained usage
摘要，行数不变也不能让 unavailable 前的旧 offer 重新有效；其他 pair 不受影响。
scope 的单调 `usage_generation` 在首次替换后拒绝升级前只有行数的旧 offer。
新的 read/ack 可正常恢复，不提高上限。产生这些记录后须保留兼容 reader；回退到无法
识别 generation 的旧 reader 不是受支持的 rollback。


设置入口 **Goal 设置 → 任务所有权 → 方向依据与资料版本** 支持检查/应用声明、独立查看
各 Agent、恢复未知响应和停用。浏览器不能代表 peer 读取、确认或提交 usage receipt。
Agent CLI 的写入显式加 `--execute`：

```sh
loopx --registry REGISTRY --format json goal-direction \
  --goal-id GOAL --goal-instance-id INSTANCE --agent-id AGENT --operation inspect
loopx --registry REGISTRY --format json goal-direction \
  --goal-id GOAL --goal-instance-id INSTANCE --agent-id AGENT \
  --operation read --request-json read.json --execute
loopx --registry REGISTRY --format json goal-direction \
  --goal-id GOAL --goal-instance-id INSTANCE --agent-id AGENT \
  --operation acknowledge --request-json acknowledgement.json --execute
```

合成打包界面示例：[检查声明](../../assets/goal-direction/declaration-preview.png)、
[版本变化](../../assets/goal-direction/revision-drift.png)、
[窄屏 Agent 回读](../../assets/goal-direction/peer-readback.png)。它们是 operator/CLI 验证，
不是模型真实理解证据。

Agent 须已从任务/provider 上下文获得获授权本地路径；本 profile 不从 material id
自动解析私有 locator。显式读取和自主来源发现/handoff 是不同验收边界。
`read.json` 包含 `operation_id`、`material_id`、临时 `source_path`，以及 inspect 的
`required_revision`、`authority_digest`。确认请求包含自己的 `operation_id`、返回的
`offer.read_operation_id` 和 `delivery_digest`。CLI configure 不带 Agent id，提交 materials、
expected_provider_revision、operation_id，默认仅预览。每项材料含 material_id、
source_path、purpose、topics、required、boundary；应用已审阅声明时带预览得到的
expected_authority_digest。receipt 用 receipt_operation_id 和原 actor/实例；disable
是带当前 provider revision/独立 operation id 的 operator 操作，保留声明和回执。

Cold inspect 返回 M1 投影和 typed Agent-scoped vision_gap。acceptance_basis 引用实际
观察的 canonical acceptance contract revision/digest（通过原 typed reader）及方向摘要，
明确 not_assessed；没有合同、未启用或未绑定精确实例时不输出关联版本。通用 source-session
CLI 的验收配置/执行仍在候选范围外，未放开其 writer gate；
不能让整 Goal 验收变绿，不写 Vision/checkpoint、Todo、route、lease、scheduler 或 quota。
实际改路线仍归 goal_path_delta_v0；通用 runtime 和 Lark 未启用。本机 operator/注册身份
沿原可信 transport 边界，不声称新增身份认证或独立 worker 证明。

候选用真实注册、File/SQLite、文件、HTTP、打包设置和独立 Agent CLI 验证版本/peer/实例
隔离、先鉴权再 IO、未知响应、重试、来源不可用、损坏及原工作保持。合成身份/文件不证明
真实模型读取或自主协作。M2 晋级仍须具名 consumer/UX 评审及新增 profile 验收；opt-in
声明不是晋级批准。未启用前可移除候选；使用后停用 reader 并保留 canonical 历史/fence。

---

## 附录 A：执行账本（非规范性）

### 2026-09-10 — M0 提案

- **基线：** `41a95d9`
- **交付：** 双语 RFC 与 synthetic fixture 计划
- **证据：** 文档与 public-boundary check 等待 PR 验证
- **已知缺口：** 无 schema 实现、writer、consumer 或 live provider
  qualification
- **对规范设计的影响：** 初始提案

### 2026-10-04 — F2 synthetic fixture（M1 前）

- **基线：** `99839ae`
- **交付：** 附录 D 的 F2 行以 checked-in public-safe smoke 及其 mutation
  arm 落地（`examples/goal-direction-baseline-f2-smoke.py`）；第 11 节附注
  记录了 M1 保持关闭的原因
- **证据：** smoke 本地通过；`loopx check --scan-path
  docs/architecture/rfcs` 干净；等待 PR 验证
- **已知缺口：** 无 runtime builder、writer 或 consumer；F1 与 F3-F8 仍未
  checked-in
- **对规范设计的影响：** 无 —— 仅固定附录 D 的 F2

## 附录 B：决策日志

| 日期 | 决策 | Owner / 批准 | 替代方案 | 修改的规范章节 |
| --- | --- | --- | --- | --- |
| 2026-09-10 | 接受 M0 为仅文档 helper scope；repository 文档只是一个 provider，revision drift 保持 advisory。 | [#2831](https://github.com/huangruiteng/loopx/issues/2831#issuecomment-5224703471) 上的维护者 scope | Runtime 实现或 repository-specific writer | 初始第 1-12 节 |

## 附录 C：证据注册表

| Evidence id | 声明 | 基线 / 环境 | Artifact 或命令 | 结果 | 隐私 / 有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | 已审计当前 Material Frontier 语义。 | `41a95d9` | [Agent Material Frontier](../../reference/protocols/agent-material-frontier-v0.md) | documented | 仅 public contract；无 live material。 |
| E2 | 路线变更 ownership 保持现有契约。 | `41a95d9` | [Goal Vision and Replan](../../reference/protocols/goal-vision-replan-contract-v0.md) | documented | 不证明 proposed baseline。 |
| E3 | 当前 RFC 双语文件保持 public-safe，符合文档治理。 | `c464cf308` 加第 12 节建议 | 对两个 RFC 文件运行 `loopx check`；`uv run --extra test python examples/docs-governance-smoke.py` | 通过 | 公开扫描覆盖两个文件；仅文档 scope。 |
| E4 | F2 revision-drift 不变量可执行且带 mutation 检查。 | `c464cf308`；fixture 引入于 `99839ae` | `uv run --extra test python examples/goal-direction-baseline-f2-smoke.py` | 通过，含 mutation arm | 仅 fixture 内部合成 builder；无 runtime builder 或 live material。 |
| E5 | 现有 frontier 成本与缺失的 runtime owner 为第 12 节建议提供依据。 | `c464cf308`，Python 3.11.7，macOS arm64 | 附录 F 配方；`material_frontier.py`、`management_projection.py`、`agent_context.py` | 已测量五种合成输入并检查源码边界。 | 仅现有 Python 基础实现；无 M1 builder、receipt writer、SLO 或运行时批准。 |

## 附录 D：Synthetic drift fixture 计划

所有 fixture 使用虚构 id 与 revision。Harness deep-copy 输入，运行 pure
builder，并断言输入 byte-for-byte 不变，同时不存在任何 Vision、Todo、lease、
scheduler、Goal-amendment 或 route-write effect。

| Case | 输入变化 | 预期投影 | 独立 negative assertion |
| --- | --- | --- | --- |
| F1 exact Agent receipt | 两个 required material；所选 Agent 均有 exact-revision receipt。 | `current`；digest 稳定；两项均 current。 | Goal、Agent、material 或 revision 不同的 receipt 均不能满足任何 item。 |
| F2 revision drift | Agent receipt 之后，一个 authority revision 发生变化。 | `re_evaluation_required`；`material_revision_changed`；变化项 stale。 | 不发出 Vision patch、Todo、wake、lease、amendment 或 path delta。 |
| F3 peer receipt | 只有另一个 Agent 持有新 revision receipt。 | 所选 Agent item 为 `required_unread`，或因自身旧 receipt 保持 stale；aggregate 按优先级为 blocked 或 re-evaluation-required。 | 投影不包含 peer receipt ref。 |
| F4 missing authority | 完整 `project_materials` 缺失、声明 id 缺失或 revision 为空。 | `blocked`，含精确 missing reason。 | 紧凑 count/summary 不能被当成空或 current registry。 |
| F5 inaccessible material | Boundary 不可用或 gate blocked。 | `blocked`；item inaccessible。 | matching revision receipt 不能覆盖 boundary/gate。 |
| F6 mixed required set | 一项 current，一项 exact revision-mismatch。 | `re_evaluation_required`；count 确定。 | Current item 不能遮蔽 required material 的 revision drift。 |
| F7 replay stability | 相同 canonical input 与 receipt 改序并投影两次。 | 除显式 `generated_at` 外，normalized item、digest、state 与 reason code 相同。 | Receipt 顺序与重复 declaration 不能制造 churn。 |
| F8 route ownership | Agent 读取变更 revision 后保持或改变路线。 | 新 receipt 让 baseline 恢复 current。 | 保持路线不产生 path delta；改变路线只有携带有效现有 `goal_path_delta_v0` 才能接受。 |

M1 前应把该表变成 checked-in public-safe fixture 与 mutation test。Mutation arm
必须故意放松 Agent 或 revision 匹配，并证明 F2 或 F3 会失败，从而避免测试因未
真正执行 happy path 而误通过。F2 已 checked-in 于
`examples/goal-direction-baseline-f2-smoke.py` 并带 mutation arm；其余 case
在 M1 前保持开放。

## 附录 E：拒绝或已取代方案

第 6 节记录当前拒绝设计。若证据表明某 provider-specific owner 能保持相同
authority、privacy 与 no-mutation 不变量，可以新增 profile，但不能分叉 core
contract。

## 附录 F：入口决策证据（非规范性）

在 `c464cf308`，`loopx/control_plane/agents/material_frontier.py` 中的
`build_agent_material_frontier` 会为每个 resolved material 扫描输入 receipt。
下表描述现有 Python 调用，不代表未来 TypeScript direction builder 的性能：

| Material 数 | Receipt 数 | Topic 数 | 输入字节 | 输出字节 | 中位数 ms | 观测范围 ms |
| --- | --- | --- | --- | --- | --- | --- |
| 16 | 16 | 16 | 8,153 | 5,988 | 0.783 | 0.679–1.032 |
| 64 | 64 | 64 | 31,961 | 22,404 | 3.193 | 3.013–3.419 |
| 256 | 256 | 64 | 122,741 | 88,226 | 19.286 | 18.857–19.675 |
| 256 | 4,096 | 64 | 1,059,587 | 88,482 | 184.327 | 177.839–200.030 |
| 512 | 8,192 | 64 | 2,119,171 | 176,546 | 670.294 | 658.070–688.730 |

复现配方：macOS arm64、Python 3.11.7；固定 Goal `direction-fixture`、Agent
`agent-a`、id `material-{i:04d}`、revision `revision-1`。每条 authority row
包含其 id、revision、`boundary=public_safe`、`gate_status=ready`、
`freshness=current`、`role=direction_source`、`owner_status=reviewed` 和
`conflict_rule=owner_wins`。对 `i < min(64, material_count)`，将
`topic-{i:02d}` 映射到下标为 `i::64` 的 material id；Agent profile 包含相同
`agent_id` 并请求全部 topic。前三种输入每个 material 生成一条 receipt，后两种每个生成十六条，
包含现有 schema、Goal/Agent/material id、observed revision、`outcome=read`、
id `receipt-{i}-{j}` 及 timestamp `2026-10-12T00:{j:02d}:00Z`；
`generated_at=2026-10-12T00:30:00Z`。

先调用现有 builder 预热一次，再对相同输入运行七次，用
`time.perf_counter_ns` 记录中位数与最小/最大值。将完整参数和结果用排序 key
及紧凑 JSON 分隔符序列化，计算字节数；断言输入序列化结果未变、输出 item
数量精确且全部 current。不涉及 provider、网络或模型。另行运行
`tracemalloc`，256/4,096 时峰值为 474,642 字节，512/8,192 时为 973,250
字节；这仅是调用期间 Python 分配追踪峰值，不是 RSS 或总进程内存。
计时不含 fixture 构造与序列化。

这些观测说明 material 数与 receipt 历史都影响成本，不验证拟议 declaration
schema、最大字段宽度、conflict、组合最坏情况、端到端时延或实际负载占比。
原始测量保留在公开仓库外；fixture 配方与有界汇总观测作为评审证据。

## M1 候选源码检查点

源码验证不代替 §11 的 M1 入口批准；§12 的 owner 决策仍待完成。

可选 TypeScript 纯构建器取代原合成 decision owner。验证覆盖完整 registry 解析、
准确 Agent/Todo/revision、冲突、时间顺序、输入不可变、输入/输出限额与 public-safe
投影。用 Node >=22.22.3 执行
`node --experimental-strip-types --test tests/control_plane_ts/direction_baseline.test.ts`
与 `uv run --extra test python examples/goal-direction-baseline-f2-smoke.py`；既有
`vision_checkpoint.test.ts` 验证路线 authority。没有新增平行 Python reducer 或
Python/TypeScript 双消费路径。§12.5 候选验证显式本地 read/ack、精确实例和 CLI/设置
链路；模型真实消费、其他 provider、生产容量 promotion 和 Lark 仍未验收。未启用前可
移除候选；使用后须停用 reader 并保留 canonical 历史与 fences。
