# RFC：Goal 级能力组合与 Connector 生命周期（v0）

- **RFC 状态：** 已接受
- **替代 / 关闭：** 无
- **交付成熟度：** Proposal；现有目录、hook 与外部证据切片只是部分前置
- **作者 / Owner：** LoopX capability 与 control-plane 维护者
- **创建时间：** 2026-09-21
- **最近一次规范修订：** 2026-09-26
- **实现基线：** `65afc4872db67d36f74625a9e53ae63da2bc619c`
- **相关契约：** [总路线图](loopx-overall-roadmap-v0.zh-CN.md)、
  [研究探索](research-exploration-control-plane-v0.zh-CN.md)、
  [Agent Loop Effect](agent-loop-effect-interpreter-v0.zh-CN.md)、
  [结果后 Memory 效果归因](post-outcome-memory-utility-attribution-v0.zh-CN.md)、
  [Extension 参考](../../reference/extensions.md)以及
  [外部证据生命周期 PR #4813](https://github.com/loopx-project/loopx/pull/4813)
- **语言镜像：** [English](goal-scoped-capability-portfolio-v0.md)

## 文档结构与维护约定

第 1–10 节是长期设计和验收契约，第 11 节是规范性交付计划，第 12
节是未决问题。附录只记录非规范性证据和历史。RFC 成熟度与交付成熟度
相互独立。中英文是语义镜像，规范内容必须同步修改。

---

## 1. 决策摘要

LoopX 将新增 **Goal 级 Capability Portfolio**，让 Agent 围绕 Goal 的结果
和验收缺口，判断、选择、组合、评估、降级和退役能力。Portfolio 拥有采用、
组合与生命周期迁移决策；它只索引既有 owner 的不可变回执，不拥有或重述
这些回执的效果。它不会把能力配置、provider 状态、证据、Todo、授权或
memory 再复制成一份真相。

Portfolio 组合现有 owner：

1. capability catalog 描述可考虑的能力；
2. 现有 Goal configuration 与 external-capability binding 选择 exact operation、
   provider revision 和 profile digest；
3. `agent_context` 在 `before_plan` 投影有界规划，在 `before_delegate`
   冻结选中路线，在 `after_delegate_result` 返回 typed 结果；
4. external-evidence 生命周期负责稳定研究方法和 connector 的资格认定；
5. Decision Context、Explore 与 reward memory 仍是有自己准入规则的下游。

**Goal 开启能力，就足以激活该能力支持的行为。** 既有配置 owner 将能力解析为
对该 Goal 当前 Agent/surface scope enabled 后，适用的 hook 和正常执行路径自动参与，
不再要求第二个
Portfolio 开关、手动 adoption 或每轮提示。激活仍遵守该能力自身的触发条件、
预算和权限契约，不表示每个 Turn 都调用全部已启用能力。

按实际需求使用最少机制：没有启用能力时不增加 Portfolio 工作；一个能力或多个
独立能力走原有直接路径；出现真实依赖或选择取舍时才生成有界组合。只有既有配置
无法表达的显式跨 Turn 采用/生命周期决策，才需要持久化 Portfolio 状态。

组合建议 fail-open，既有能力义务仍须执行。Portfolio 不可用不能抑制已启用的
直接路径，也不能绕过 Todo admission。自发现不能安装软件、启用 provider、
扩大 scope、修改模型授权或批准 protected effect。

本 RFC 不批准自动安装能力、connector 市场、Core 内的垂域排序或金融
执行权限。

## 2. 问题与动机

LoopX 已有 catalog、extension、readiness、Goal/Todo capability 要求、三个
Agent-context hook、external-evidence 规划、Explore、Decision Context 和
reward memory。但一个新 Agent 仍需自行猜测这些部件如何组合。当前缺失
的组织逻辑常由垂域 prompt 或本地策略文档补齐，导致采用理由不能跨
session 保留，换一个 Agent 又会重复探索、选择重复来源，或把 provider
ready 错当成证据质量。

Connector 存在同一问题。现有 registry 是有价值的库存和使用遥测；注册、
ready 和调用次数不能证明来源覆盖、时效、rights、真实执行、父 Agent
准入或决策价值。稳定来源应经过发现和有界 trial 才晋升，并在失效、
过期、成本过高或长期无效时降级或退役。

具体例子：一个投研 Goal 需要最新一手证据、独立反证和 read-heavy worker。
Agent 应能发现已有 external-research 方法、一个 qualified source connector
和一条可用 worker route；解释各自的选择理由；冻结 revision 和预算；记录
部分覆盖与失败；最后说明结果是否改变决策。目前这些事实分散在多个
projection 与说明文字里。

### 不变量

- Portfolio 采用永远不能新增或扩大授权。
- 配置仍归原 owner；Portfolio 只保存精确引用、digest 与有界 readback。
- `ready`、`executed`、`read`、`admitted`、`decision-changing`、
  `domain-eligible` 必须彼此区分。
- 同一来源被多个 connector 或 worker 读取，不能算独立证据。
- unknown、stale、partial、unavailable 必须显式；空结果不是完整覆盖。
- 模型回复、tool call、commit 或 connector 调用本身不是效果证据。
- replay 幂等；revision 漂移不能静默复用旧计划。
- CLI、managed Turn、前端和 Lark 读取同一份公开投影。
- enablement、adoption policy 与 owner qualification 是分别拥有的 typed fact。
- 使用已启用能力不要求存在 Portfolio record。
- 功能关闭和 Portfolio 失败时，保留既有能力路径及其义务，包括显式 automation 设置。

## 3. 范围与非目标

### 范围内

- provider-neutral 的 capability descriptor 引用与 Goal adoption record；
- 优先直接激活，仅在需要时生成有界组合 DAG；
- trial、adoption、degradation、retirement 决策，以及 owner 回执的类型化引用；
- 建立在 external-evidence 生命周期之上的 connector qualification profile；
- 通过 `before_plan`、`before_delegate`、`after_delegate_result` 注入；
- exact effective-config 与 provider revision readback；
- CLI/前端/Lark 共享查看与反馈；
- 以 finance 和一个非金融旅程做资格验证。

### 非目标

- 替代 Goal、Todo、quota、claim、lease 或 shared-authority 状态；
- 复制 provider 凭据、原始来源正文或私有配置；
- 将 Decision Context、Explore 或 reward-memory 状态搬进 Portfolio；
- 发明跨不同能力的统一总分；
- 自动安装、授权、支付、发布、签名或交易；
- 把 Portfolio 推荐当成 runtime 或垂域授权；
- 要求每个 Turn 全量扫描全部已安装能力。

## 4. 当前系统契约

在实现基线上：

- capability catalog 与 extension manifest 描述 installed/enabled 实现、
  provider、hook、权限与 readiness；
- `goal.external_capability_bindings` 已拥有持久的 Goal 级 enablement，绑定 exact
  operation、provider revision 和 profile digest；直接 binding invocation 只准入
  read-only operation，不创建 Turn、不消耗 quota，governed effect 继续走原执行路径；
- capability admission 与 capability memory 提供有界 Goal/provider 和宿主
  观察，但不授予权限；
- Todo capability gate 判断已知任务能否执行，不会从 Goal 缺口发现能力；
- `agent_context` 已支持三个 guidance-only、有界 phase；
- connector registry 保存库存和简单使用遥测，不负责来源资格；
- 已合并的 #4813 交付 `external-evidence` discovery、plan、receipt observation、
  parent admission 与 evidence retirement，provider execution 仍在 Core 外；
  它没有交付持久的 connector qualification 状态机；
- Decision Context 拥有决策证据，Explore 拥有研究拓扑，reward memory
  拥有经过资格审查的可复用结果经验。

既有 configuration editor 和 Goal capability settings 已共用 preview/apply/readback。
Periodic-report post-writeback hook 在组合入口解析 Goal subscription；reward-memory
hook 保留 owner 定义的 surface/automation 设置。这些是复用边界，不证明所有能力已在
所有 host 接通。通用自动参与、Portfolio 状态和跨入口读回仍是本 RFC 的提案。

## 5. 建议架构

### Owner 与权限

**放置决策：** 建议 capability id 为 `goal-capability-portfolio`，provider id 为
`builtin`（设计标记，本 PR 不新增注册）。独立用户结果是跨 Agent 查看和保留 Goal 的
能力选择。轻量 read model 归 built-in capability policy，读取既有 Goal 配置不应要求
安装垂域包。Catalog、configuration、Decision Context、extension lifecycle 继续满足
各自契约，但都不拥有跨能力选择历史。

选择、需求判断与生命周期策略归该 capability；typed normalization、identity 与迁移
留在它的 TypeScript owning boundary，Python 只适配传输。通用 Kernel 复用注册、
有界 dispatch、schema 校验、失败隔离和既有 effect/Todo admission；不理解能力名称、
connector 阶段、垂域排序或 Portfolio adoption 状态。注册发生在 composition root，
共享 quota/scheduler/Todo reducer 不导入 Portfolio policy。Provider 执行和独立版本的
垂域集成继续归原 capability/extension/package；不新增 worker、scheduler、workflow
DSL、binding store 或通用 effect ledger。

Portfolio 只拥有：

- Goal 为什么考虑、trial、采用、降级或退役某项能力；
- 选中的组合和 exact revision；
- Portfolio 生命周期迁移回执与复评触发条件。

既有 Goal configuration 与 external-capability binding 仍是 runtime enablement 的
唯一 owner，其 preview/apply/readback 无需 adoption record 就激活支持的行为；
Portfolio 不得否决该直接路径。反过来，adoption record 不启用或执行 capability：
`adopted` 所需 binding 缺失或 stale 时可展示但不可运行。Degrade/retire 只改变
Portfolio 选择策略，停用能力仍须通过原配置 owner。

它仅引用而不复制：catalog/extension 声明、生效配置、provider readiness、
Todo 要求、授权决策、external-evidence receipt，以及 Decision Context、
Explore、memory artifact id。

任何 chat、UI、connector、worker 或垂域 capability 都不能成为另一个
Portfolio writer。所有变更经过一个 typed reducer 和当前 Goal authority
provider。

### 激活与按需执行

配置 owner 统一解析继承、显式关闭、Agent/surface scope 和受支持的 operation/profile
设置。Agent 级激活（如 reward memory）不能顺带启用同 Goal 的其他 Agent。Portfolio 只
消费 exact effective result，不从 catalog presence 推断 enablement，也不重解释各
owner 的历史默认值。既有显式 manual-only 或已关闭的 automation 设置继续有效。
新增受支持的自动入口不需要 Portfolio opt-in；若改变既有能力默认值，必须由该 owner
在发布前披露并完成验证。

| 生效状态与当前工作 | 自动行为 | 额外 Portfolio 工作 |
| --- | --- | --- |
| 没有启用能力 | 保留原 Agent 路径 | 无 provider/model 调用、hook contribution 或持久写入 |
| 已启用，触发条件不适用 | 保持能力可用，不调用 | 热路径 contribution 为空，可按需 inspect 原因 |
| 已启用且适用，直接或独立工作 | 按原 admission 运行既有 hook/route | 不要求 trial、DAG、adoption receipt 或额外模型调用 |
| 已启用且存在真实依赖或取舍 | 生成最小有界计划，通过既有 owner 执行 | 只保留选中的依赖闭包和必要决策引用 |
| binding/readiness/authority/budget 缺失 | 保留 owner 的 unavailable、blocked 或 deferred 结果 | 不隐式修复、替换 provider 或授予权限 |

Host 必须真实 dispatch 已注册且适用的 hook，不能只打印“有这个 enabled 能力”。没有
自动 hook 的命令仍通过正常 Agent/tool 路径使用；不支持的 host integration 显示
unsupported，不能宣称激活成功。自动执行 protected effect 仍要求既有 exact admission。
可选排序建议不得把机器强制的能力义务改成 suggestion。

组合依据是 typed input/output dependency、共享受限资源、备选 provider 选择，或需要
联合结果的明确验收缺口。能力数量、关键词匹配本身不足以触发组合。优先读取已有
生效配置，仅为未解决缺口扩大发现范围。两个独立能力保持直接路径；一个操作若需在
高成本 provider 间取舍，也可能需要计划。规划失败时原生 hook 继续运行。

### 状态模型与 schema

以下是建议契约草图，不是五个必建的新 store。直接激活只读取既有 owner，不创建
这些持久记录。只有不能从配置或 owner receipt 推导的显式跨 Turn policy 才落 adoption/
lifecycle state；仅 composed 路径需要 plan。每个新增字段必须有真实 consumer。

#### `capability_catalog_entry_v1`

这是现有 capability declaration 的规范化引用：

```text
capability_id, capability_revision, owner_ref, declaration_ref, declaration_digest
effective_config_ref?, readiness_ref?
```

Portfolio 不编辑或持久化第二份 catalog。仅在选择需要时，从原 owner 解析 outcome、
phase、schema、authority、privacy 与 cost 声明。

#### `goal_capability_adoption_v1`

```text
goal_id, adoption_id, portfolio_revision
gap_ref, capability_id, capability_revision
status = candidate | trial | adopted | degraded | retired
reason, alternatives[], expected_effects[]
effective_config_ref, effective_config_revision, config_digest
binding_ref?, binding_digest?
trial_budget?, trial_window?, authority_refs[]
owner_observation_refs[], lifecycle_receipt_refs[]
review_after, degradation_conditions[], retirement_conditions[]
created_at, updated_at
```

`gap_ref` 指向结果或验收缺口，不创建第二份 Todo。状态迁移要求 expected
current revision。字段省略表示保留；可选字段按字段定义显式 clear 语义。
执行需要 binding 时，`binding_ref` 解析现有 Goal binding，而不是 Portfolio
复制一份 provider configuration。Binding readiness/status 只在读时联结原 owner。
已启用的直接能力可以没有 adoption record，此时 inspect 返回 `adoption_status=null`，
不能伪造 `adopted` 迁移。Read model 分开呈现 effective enablement、execution mode
（`disabled | direct | composed`）与可选 adoption status，并保留 provenance。

#### `capability_composition_plan_v1`

```text
goal_id, todo_id?, turn_id?, composition_id, portfolio_revision?
gap_refs[], nodes[], edges[], selected_at, expires_at
node: capability/provider/connector/worker/reducer 引用，
      phase、输入/输出 schema、exact revision、预算、
      所需授权、所需读写范围、disposition、reason
```

`composition_id` 是全部规范化决策字段的 canonical digest。图必须无环。
每个纳入考虑的候选都有 `selected`、`skipped`、`unavailable` 或 `incompatible` 及理由。
计划是 guidance，不是执行授权。

#### `capability_owner_receipt_observation_v1`

```text
observation_id, composition_id?, node_id?, phase
goal/todo/turn identity
owner_kind, owner_revision
owner_receipt_ref, owner_receipt_digest
observed_at, owner_receipt_status
lineage_digest, review_trigger, next_lifecycle_proposal
```

这是一份只读索引，指向 capability、provider、delegation、Decision Context、
external-evidence 或 outcome owner 的不可变回执。它不得复制 provider/model/
connector revision、coverage、source family、cost、失败细节、parent disposition、
decision effect 或 utility；这些事实只以被引用 owner record 为权威。

owner 的纠正、撤销或退役按该 owner 的协议产生或选择新回执。Portfolio 观察
新引用，并在读时把旧 observation 标为 superseded；它不会改写 owner 事实。
owner readback 与索引冲突时，以 owner 为准；Portfolio 的生命周期迁移不得消费
过期 observation。

每次成功的 Portfolio mutation 返回 `capability_lifecycle_transition_receipt_v1`，
只包含 adoption id、operation id、预期/已提交 Portfolio revision、迁移前后
adoption status、reason、可选 composition id、owner-observation refs 和下次 review
trigger。`adopted` 只表示 Goal 的 capability-adoption policy 选中了该能力，
不表示证据已准入、结果成功或获得新权限。

以下两个例子固定 owner 边界：

- **外部证据：** external-evidence owner 独自记录 provider execution observation、
  parent admission、coverage 与 retirement。该 owner 纠正或退役回执后，Portfolio
  跟随 superseding owner ref，并可提出 `degraded`；它不保留另一份 coverage 或
  admission 事实。
- **非证据能力：** delegation owner 记录 worker route/result receipt，相关评估
  owner 记录 outcome quality。Portfolio 复评 adoption 时只引用这些回执，不把它们
  翻译成通用 `admitted`、`refuted` 或 `decision_effects` 事实。

### 命令与事件生命周期

```text
读回既有 Goal enablement → capability owner 判断适用性
  ├─ 独立工作 → 原生 hook/direct route
  └─ 真实依赖/取舍 → 有界 plan → 既有 execution owner
两条路径 → owner result/readback
  → 仅在需要跨 Turn policy 时：adoption/lifecycle transition
```

Discovery/trial 用于缺失的方法或不确定的选择，不是已启用工作必经入口。组合中的
delegation 在 `before_delegate` 冻结路线，在 `after_delegate_result` 观察 owner receipt；
无 delegation 的工作保留原生执行和结果边界。

变更 identity 为 `(goal_id, adoption_id, expected_revision, operation_id)`。
同一意图 replay 返回原 receipt；identity drift fail-closed。provider/config
revision 漂移使计划失效。丢失响应时先读回 receipt，再重试。

Portfolio 不可读取时，规划继续并报告 Portfolio availability unknown，不构造证据
coverage 事实。若 Todo 明确要求
缺失能力，由已有 capability admission 只阻断该 Todo；Portfolio 不得削弱它。

### Connector qualification profile

Connector qualification 归 `external-evidence-research`，消费其已有 plan/receipt/
admission/retirement 引用。以下是拟议的 connector-owner profile，不是 #4813 已交付的
证据 retirement 状态机，也不是 Portfolio adoption 词汇：

```text
external-research discovery
  → connector candidate
  → bounded trial
  → parent qualification
  → active
  → degraded | retired
```

Connector descriptor 增加 source family、支持操作、coverage domain、发布/
观察时间语义、rights、cost、failure、fallback。call receipt 绑定 exact plan/
provider revision、source refs、coverage interval、freshness、rights snapshot、
cost、latency、failure 和 output digest。注册和 ready 仍只是库存事实。parent
qualification 与 finance evidence eligibility 或其他垂域准入继续分开。

Portfolio 的 `adopted` 只描述选择策略。若 qualification owner 支持 connector `active`，
它必须作为单独标注的 owner-joined fact 展示。例如 `adoption_status=adopted` 与
`connector_qualification.status=active` 可同时存在，但 owner ref 不同，互不映射。
已 adopted entry 可引用 degraded connector；active connector 也可没有 Portfolio
adoption。Connector owner 尚未提供该 typed fact 时 qualification 为 unknown，不能
从 registry readiness 或 evidence admission 构造它。安装、enablement、doctor
status、provider revision 与 rollback 继续来自 extension runtime 和现有 Goal
binding。disable、uninstall、doctor failure 或 binding revision drift 会令 composition
stale；Portfolio 不得静默切换 provider。

### Runtime 注入

- **`before_plan`：** 读取 enabled 且适用的能力；独立直接工作不增加 Portfolio
  contribution。组合工作只投影当前 gap、选中的依赖和 stale/unavailable 节点。
- **`before_delegate`：** 冻结 worker/connector/provider revision、预算、
  schema、authority refs 和 composition digest。垂域只描述问题和验收标准；
  通用 delegation owner 控制容量、route 和 result receipt。
- **`after_delegate_result`：** 只索引 typed owner-receipt ref，并提出 Portfolio
  生命周期复评。成本、覆盖、失败、admission、decision effect 与 utility 留在原
  owner receipt。worker 原始回答不是 adoption 或 lifecycle receipt。

对应生命周期事件发生时才适用这些 phase；不为 bound read-only call 强制创建
delegation 或 governed Turn。既有 turn-start/post-writeback hook 和 pending-intent
执行保留原 owner，Portfolio 不重复 dispatch 或对一个 effect 重复记账。

可选 Turn-start 摘要只包含相关 composition ref、stale/unavailable 依赖和下次复评
条件。直接工作不增加 Portfolio prompt 段落；完整 catalog/history 与 owner join 留给
按需 inspect。有界声明按既有 revision 缓存，配置/provider/receipt drift 仅失效相关项。

### 长程职责、渐进披露与记忆演化

**决策：** 统一何时披露上下文、如何验证更新，而不是把所有存储统一成万能记忆库。
长程 Agent 是 Goal 内具有职责和可恢复工作的持久身份，不是无限增长的宿主会话。
Managed Codex worker 与短生命周期原生 subagent 仍是不同执行模式；两者都不能
从记忆内容或功能角色获得权限。

以下是拟议的集成关系；已有 owner 不等于整条用户路径已经贯通：

| 层次 | 既有 owner 与披露时机 | 更新与失效 |
| --- | --- | --- |
| 职责与约束 | Goal vision/direction、注册 Agent profile、真实授权；规划/恢复时披露少量引用 | 按 owner 授权修改配置并保留版本；profile 只提供建议，不授予权限 |
| 当前工作的接力信息 | Todo、checkpoint、协作请求/结果、显式 continuation；恢复时读选中工作与未解决纠正 | 重读任务/验收/来源状态；显式采用交接，不从“已投递”推断“已接受” |
| 事实与事件证据 | material/source registry、Explore、Decision Context、Turn Recall；具体问题出现证据缺口后检索 | 保留来源、原始可得时间、主体、范围、版本与纠正/替代引用；未知时间不伪造 |
| 可复用经验与方法 | Reward Memory candidate/review/recall/application；程序性方法归 skill/capability owner | 评审有范围的经验，验证写入与目标端召回；验证过的方法变化才成为版本化 PR/配置提案 |

渐进式披露是读策略，不是存储格式。规划阶段提供有界职责与工作引用；具体任务
检索相关证据和经验；需要时才取完整来源。不要每次 `before_plan` 都注入完整
能力目录、历史对话或全部金融/研究记忆。负面检索结论只覆盖该查询和来源，
不证明某个标的或能力不存在。缺失、过期、不可读必须显式；除非当前工作自身
缺少必需证据或权限，否则不阻塞独立工作。

记忆更新沿现有 owner 路径完成：

1. 实质纠正或结果引用确切来源及受影响工作/能力版本；对话中的意图不是已写入。
2. 区分事实更正与可复用经验。通过原 owner 改当前事实，只把改变决策的学习提交
   到既有 candidate/review 接口；不能用摘要覆盖历史。
3. 保留冲突、范围、来源、替代/到期与删除规则。Provider 异步接受不等于完成：
   验证写入、逐项读回，再在新上下文边界验证目标端召回。
4. 显式把召回经验用于一个决策；使用、任务结果和 utility 分别记录。调用次数或
   模型自评分不是效果证据。
5. 重复、可迁移的证据可以提出 skill/配置/capability 修改，用留出失败案例与
   相同工作负载基线验证，经过原有评审、版本与回滚机制。不自授权限、不静默安装，
   不从一次成功推出普遍规则。

能力**组合**消费上述有范围上下文，填补验收缺口：优先直接调用，只引入必要依赖。
能力**演化**消费经评审的结果证据，改变版本或采用策略。事实、临时工作状态、经验、
可执行方法不能混成一句“记忆已更新”。Portfolio 引用原 owner 回执，不复制学习库
或结果状态机。

OpenViking 是既有 Reward Memory 或 Decision Context binding 后的可选上下文
provider，不是 Goal/Todo 存储，也不是安装前置。没有它时，本地带来源的召回和
既有 continuation 仍须可用。Hermes、LingTai 是设计参考，不增加必装运行时。
只有实测检索/更新缺口才引入新 adapter，不以集成数量作为价值。

### 产品路径与首步交付边界

入口是用户的 Goal 和注册 Agent 的工作，而不是“请选择记忆框架”。复用既有
Goal 设置/capability editor、请求时间线、产物详情抽屉和 Lark 回传。普通对话
呈现变化、结果及必需决定；按需详情解释用过哪个来源/版本、为什么适用、修改
了什么、如何纠正或退役。后台状态不变时不打扰。记忆服务异常不能被解释成
忘记 Goal 或丢失执行授权。

首步实现是 M0 的**只读检查切片**：
`capability inspect --goal-id ... [--agent-id ... --phase ...]`。复用 Dashboard
配置投影与既有 TS coordinator-context owner，不增加状态、生命周期 reducer、
启用开关、provider 或自动 hook。读数标明覆盖范围和独立读取的一致性限制；
配置、投影指导、原生可用性、执行、采纳、效果仍然分开。它**不代表**完整 M0
自动参与或 M1–M5 已交付。本切片无须新增前端控件：现有设置编辑器已经拥有这些
配置和读回。实时记忆/使用/纠正控件及 Lark 证据回传仍是后续交付项，不能用 CLI
测试冒充验收。

随后先验收一次**纠正 → 新会话决策**，再加组合 planner：持久化已获授权的纠正，
展示受影响来源和被替代判断，换会话恢复，召回正确版本，实际用于决策，并向原
请求回传证据。工程与研究问题各验一次，不复制宿主 session 文件。当前 continuation
与 Decision Context 契约拥有这条链；缺失来源/更新字段归原 owner，不另建 continuity
存储。

## 6. 替代方案与选择

### 由垂域 skill 组织能力

适合早期实验，但会丢失跨 Agent 采用历史、重复 runtime discovery，并让每个
垂域重复实现失败与授权规则。垂域保留语义和验收，Portfolio 拥有通用组织。

### Connector registry 成为质量 owner

拒绝。Registry 是库存和遥测。来源质量与 parent admission 需要 exact call
证据、时间、覆盖、rights 和垂域规则。

### Decision Context 拥有能力规划

拒绝。Decision Context 组装决策证据，不能成为配置、provider 或授权 owner。

### 强制 Portfolio adoption 与通用 planner

拒绝。既有 enablement 已决定能力是否参与；对独立工作再要求开关、trial、adoption
record 或 graph，会产生第二道门槛及额外 token、写入和故障依赖。直接路径保持优先，
组合是按需 capability policy，不是每个 Goal/Turn 的 Kernel 前置条件。

### 完全自动安装

v0 拒绝。它混淆推荐、配置和授权。Portfolio 可以通过现有 governed owner
提出安装/启用建议，但不能隐式执行。

## 7. 安全、隐私与兼容

- 自动激活是集成义务；组合排序是建议。既有 admission、必需验证与权限继续强制
  执行。Portfolio 不保存 secret 或原始私有 payload。
- 公共投影隐藏私有来源、账户和付费数据细节。它可在读时联结 owner 投影中
  已授权的有界 coverage/failure 字段，但 Portfolio 不持久化另一份副本。
- readiness observation 不能变成 durable grant；已有 authority 与 protected
  effect confirmation 继续有效。
- 混合版本 reader 保留 unknown field，拒绝不支持的语义收窄；revision
  mismatch 显式并阻断复用。
- connector rights 过期、revision stale 或执行歧义时进入 unknown/degraded，
  不能静默 active。
- source-family 去重避免多个 wrapper/worker 被算成独立证据。
- 功能关闭时保留现有 planning、delegation、evidence 路径。

## 8. 迁移与回滚

M0 先验证自动直接参与和既有 owner 的只读 inspect。未配置/已关闭能力保留原默认值；
已启用能力无需新的 Portfolio opt-in 或批量 adoption 迁移。新增集成必须保留各 owner
显式 automation 设置；connector 只有通过自身 exact-revision 证据才能取得资格。

按 Goal 使用既有 capability configuration 推进 rollout。任何持久 Portfolio 变更前，
preflight 校验 typed owner、authority provider 与引用。M0 不依赖新 storage；M2 只为
不可推导的 policy 增加状态。回滚移除 Portfolio planning/write，保留只读审计回执和
已启用的原生路径；若要停止能力本身，通过原 configuration/binding owner disable。
v0 不做破坏性 registry migration。

## 9. 验证与验收

| 声明 | 测试或证据 | 必须结果 | 边界 / 排除项 |
| --- | --- | --- | --- |
| 开启即自动可用 | 通过既有 Goal editor/CLI enable，随后新建受支持的 Agent session，不执行 Portfolio 命令或 adoption | 适用原生 hook/route 运行并完成 owner readback，无第二次 opt-in | 保留所需权限和显式 manual-only 设置 |
| 简单需求保持轻量 | 零个、一个、两个独立能力，再加入一个真实依赖 | 直接场景不增加 Portfolio 模型/provider 调用、DAG 或持久写入；依赖场景仅生成必要计划 | 原 owner 执行成本仍须可见 |
| 关闭/失败保持 parity | CLI、managed Turn、context、post-writeback 路径同工作负载比较 base/head，注入 Portfolio failure | 原生决策/effect 不变，不重复 dispatch 或加门槛 | 真正 required-capability failure 仍由 owner 阻断 |
| 状态词各有 owner | adoption `adopted` + connector `active`，再做 connector degradation 和无 adoption 场景 | 分别标注 refs/status，不自动映射、不伪造 adoption | connector qualification profile 尚未交付 |
| Core 保持通用 | caller/import 审计加无关能力执行 | policy 归 capability，Todo/quota/scheduler rule 无 Portfolio 分支 | 保留既有通用 admission |
| Portfolio 不授予权限 | mutation 与对抗 fixture | 扩权请求被拒绝，不写 grant | 不验证每个外部 provider |
| Plan 绑定 exact 语义 | 修改 gap/config/provider/route/budget/graph | digest mismatch fail-closed | 不证明 live execution |
| replay 幂等 | 丢响应与并发重试 fixture | 一次迁移、一份 receipt | provider side effect 仍归 provider |
| 失败保留有用工作 | Portfolio/provider unavailable fixture | 原生路径继续，Portfolio availability unknown；硬要求仅阻断该 Todo | 无 availability SLO |
| Connector 生命周期可审计 | discovery→trial→qualification→degrade→retire fixture | 每步 exact revision + typed reason | 垂域 eligibility 独立验证 |
| Provider binding 保持单一 owner | 现有 Goal binding preview/apply/readback 加 disable/upgrade/rollback fixture | Portfolio 引用 exact binding，drift 后变 stale，不写平行 binding | extension runtime 继续证明 provider readiness |
| 自发现有用 | 新 finance 与非金融 Agent 只收到同一 Goal | 选出最小合理组合或解释空选择 | 两例不证明普遍 uplift |
| 组合改善结果 | 冻结 baseline 对比 Portfolio-assisted trial | 在申明成本内改善首次有效行动、覆盖或决策质量，保留失败 | 不自动生产晋升 |
| 三个 hook 一致 | before-plan/delegate/result 契约测试 | 同一 composition identity 和 route/result lineage | 排除原始模型质量 |
| Owner truth 不重复 | 分别纠正并退役一个 external-evidence receipt 与一个非证据 outcome receipt | Portfolio 跟随不可变 superseding refs，不残留复制的 admission/effect 事实 | 各 owner 继续验证自己的语义 |
| 产品入口一致 | CLI、打包前端、Lark 的既有 Goal settings preview/apply/readback；新 session、stale、重连和重复操作 | 同 effective enablement、direct/composed mode、可选 adoption status 和 owner refs，无第二个激活控件 | 每个已开放入口发布时就有可用读回；owner fact 保留 provenance |
| 垂域边界成立 | finance 与另一垂域 fixture | Core 不理解垂域，domain admission 独立 | 不授予交易权限 |

衡量首次有效行动、证据覆盖、stale/重复来源错误、人工介入、token/费用、
决策变化和 accepted outcome。安装能力数、调用数和输出字数不是成功指标。

## 10. 运维契约

Operator 可查看 effective enablement、direct/composed mode、可选 Portfolio revision、
adopted/trial/degraded adoption、exact config/
provider revision、owner-receipt refs 和下次 review trigger。获授权视图可在读时
从原 owner 联结当前 failure、cost 与 coverage，但不持久化到 Portfolio。只对
revision drift、rights 过期、重复失败、预算耗尽或 required capability 不可用
产生事件提醒；日常成功调用不制造噪声。

复用既有 capability configuration editor 与 Goal settings 入口，先显示生效行为和
可操作的失败，按需展开组合细节和 owner receipt。不增加空 Portfolio panel、adoption
向导或第二个 enable 按钮。Lark 使用相同配置/read model。

每个 Goal 和相关事件的容量有界，按需候选分页，prompt projection 有大小限制。
对 off/direct/composed 路径用同工作负载测量 base/head latency、token、read、write
和 call。Off/direct 不增加 Portfolio 模型/provider 调用、持久写入或 prompt 段落；本地
projection 开销必须符合明确测量的预算，由 M0 按仓库 budget-decision guide 记录。
本提案不宣称通用 SLO 或性能提升。Failure class 区分 unavailable、incompatible、unauthorized、stale、rights-expired、
budget-exhausted、provider-failed、result-unqualified。备份恢复跟随所选 Goal
authority provider，原 provider artifact 跟随原 owner。

## 11. 规范性交付计划

| 里程碑 | 交付行为 | 入口条件 | 退出证据 | 回滚 |
| --- | --- | --- | --- | --- |
| M0 · 自动直接路径与 inspect | 解析既有 enablement，dispatch 受支持的原生 hook，共享 readback；无新 Portfolio store | 明确 config/hook/admission/UI owner | 零/单/独立能力、无第二次 opt-in、off/failure parity、测量开销和受影响 CLI/前端/Lark 旅程 | 移除集成，保留原 capability route/config |
| M1 · 外部证据与 connector trial | 复用已合入 #4813 的 evidence 生命周期，只补缺失的 connector-owner qualification | 真实 connector caller 和 exact-plan/provider boundary | 一个真实 host method + connector trial，含 partial/failure receipt，不伪造 connector status | 保留 inventory/evidence，关闭 qualification write |
| M2 · 可选持久 adoption | 仅为不可推导的跨 Turn policy 提供 candidate/trial/adopted/degraded/retired reducer 与 receipt | 真实 caller 需要超出 config/owner receipt 的 policy；选定 Goal authority provider | replay、并发、drift、recovery、无 adoption 直接路径 | 关闭 writer，保留只读回执 |
| M3 · 按需组合 | 通过现有 planning/delegation/result hook 传递最小依赖闭包 | M0；仅需持久 policy 时依赖 M2，仅需 connector qualification 时依赖 M1 | direct→composed→direct、无额外提示的 finance/非金融 trial、owner correction 和同工作负载开销 | 移除 planning，原生 hook 继续 |
| M4 · 效果资格 | external-only/connector-only/hybrid 实验、owner-backed review 与 retirement proposal | 冻结指标、预算、stop rule | 完整分母证明收益或明确 no-uplift，不复制 effect truth | 通过 typed owner 回退 Portfolio 选择 |
| M5 · 跨入口整合 | CLI、打包前端、Lark 共用 inspect/detail/recovery | shared projection 和前序纵向能力可用 | 组合旅程的跨 session、stale、重连、重复动作验收 | 隐藏可选 detail/mutation control，保留原生 readback |

M0 本身就是有用结果，不等待 connector qualification、新 authority store 或完整
M2–M5。各里程碑必须包含自己改变的入口；不能用 M5 推迟 M0/M2/M3 必需的设置
配套。#4813 是已合并的 evidence 基础，不证明 Portfolio 或真实 connector qualification。
Overall-roadmap owner 维护 S8 顺序，canonical Todo 维护执行状态。本 PR 只交付
修订后的 RFC 契约与上述 M0 只读检查切片，不宣称完整 Portfolio 已实现。

### 检查切片之后的集成顺序

1. **M0 剩余部分 / continuity pilot：** 按来源版本恢复并读回纠正；贯通一条
   CLI/managed Turn、打包前端、Lark 用户路径。证明旧判断不会冒充当前事实、
   provider 关闭路径等价、上下文有界、无越权跨 Goal 泄漏。
2. **M1 + M3：** 同一问题确实需要时才组合 external evidence 与一个 connector，
   选择引用既有职责、上下文和回执。验收 direct→composed→direct、部分失败、
   证据冲突、worker 不可用及原请求回传；不强依赖 M2。
3. **自主演化之前先做 M4：** 冻结案例、基线、反例，测量纠正保持率、重复/陈旧
   使用错误、有效召回精度、成本/延迟及真实决策变化，然后提出一次可回滚方法
   修改。没有提升也是合法结果，保留分母和失败案例。
4. **确有必要才做 M2，再整合 M5：** 只有真实 caller 无法用已有配置和回执表达
   时才持久化额外采用策略。两个领域验收后才发布开箱即用 preset；安装/配置预览、
   来源权限、关闭/卸载、降级回退与恢复都必须经过已有产品入口。

本顺序衔接总 roadmap 的 S1/S3 长程协作、S6 记忆、S8 组合、S11 评估，不另建
调度器或路线图。实施前对齐进行中的 continuity、checkpoint、Decision Context
freshness 与 utility-attribution PR；复用其 owner，不复制 writer。本次检查切片
不依赖未合并 PR。

## 12. 未决问题

1. **Portfolio storage profile。** Owner：shared-authority 与 capability
   维护者。建议 adoption record 使用当前 Goal authority provider，runtime
   enablement 复用现有 Goal external-capability binding，大 receipt/artifact 留在
   原 owner；不得新增 Portfolio 专属 provider binding。仅在 M2 有真实持久 policy
   caller 时决定，M0/直接使用不需要新 storage。
2. **跨能力比较。** Owner：capability 维护者。建议只在一个明确 Goal gap 内
   多维比较，不产生全局总分。M3/M4 验证。
3. **自动降级阈值。** Owner：capability + domain owner。建议自动提出 proposal，
   只有 typed policy 才应用；不能只因调用少而退役。M4 前决定。
4. **安装建议 UX。** Owner：产品和 extension 维护者。建议 M3 证明选择价值后
   才展示 governed repair/install proposal；它不属于 v0 执行权限。

---

## 附录 A：执行台账（非规范）

### 2026-09-21 — 调研与契约整合

- **基线：** `0ef7ebd749ec97a698a8fc7f2a29844dd368689b`；检查 PR #4813
  `491c0bf3ccd4804091d7611bd85d73f5f466fdd9`。
- **已交付：** 仅 RFC 契约。
- **证据：** 对 catalog、connector registry、capability admission/memory、
  Agent-context hook、Decision Context、Explore、reward memory 和 external-
  evidence proposal 的仓库审计；一次 finance connector inventory/use dogfood
  影响了生命周期设计，但不是公开 qualification evidence。
- **已知缺口：** 无 canonical Portfolio reducer、前端/Lark projection 和双垂域
  效果实验。
- **对规范设计影响：** 初始提案。

## 附录 B：决策日志

| 日期 | 决策 | Owner / 批准 | 替代项 | 修改的规范章节 |
| --- | --- | --- | --- | --- |
| 2026-09-21 | 初始提案，不从实现或沉默推断批准 | 待维护者评审 | 垂域组织、registry 质量 owner、Decision Context owner | 全部 |
| 2026-09-21 | 将通用 use/effect 状态收窄为 owner-receipt observation 和 Portfolio 自有 lifecycle receipt | exact head `1a6b15c6` 的维护者 review request | 重复的通用 effect/admission authority | 第 1、3、5、7、9–11 节 |
| 2026-09-21 | Goal 既有 enablement 激活支持行为，直接路径优先、按需组合；adoption 与 connector qualification 使用独立状态词 | 修订提案，待 exact-head review | 第二次 opt-in、强制 DAG/adoption、Kernel 拥有选择策略 | 第 1–12 节 |

## 附录 C：证据登记

| 证据 id | 声明 | 基线 / 环境 | artifact 或命令 | 结果 | 隐私 / 有效性边界 |
| --- | --- | --- | --- | --- | --- |
| E1 | 三个通用 hook phase 已存在 | 实现基线 | `agent_context`/subagent-context 源码与测试 | 已检查 | 静态检查，不证明 live uplift |
| E2 | Registry 是库存/遥测，不是 qualification | 实现基线 | connector-registry schema/CLI | 已检查 | 非穷尽 provider 审计 |
| E3 | external-evidence typed lifecycle 是已合并前置 | 当前实现基线 | `external_research/README.md`、typed external-evidence owner 与 CLI | #4813 已合并，已检查源码 | 不证明 connector qualification 或 live provider |
| E4 | 持久 Goal binding 已拥有 exact provider operation/revision/profile 选择 | 实现基线 | extension reference 与 capability-admission 源码 | 已检查 | 只读 binding 契约，不证明 provider 执行 |
| E5 | 自动参与和设置应复用既有 owner | 当前实现基线 | `agent_context.ts`、`capability_hooks.ts`、periodic-report/reward-memory hook、configuration editor 与 Goal settings | 已检查源码 | 通用 Portfolio 激活和开销测量尚未实现 |

### 2026-09-26 — 比较证据（源码/文档检查，非真实运行资格）

- [LingTai 换代实现](https://github.com/Lingtai-AI/lingtai-kernel/blob/5c65c3f9860de0addc0d2bb10d91b7d80a55930c/src/lingtai/tools/context/_molt.py)：
  上下文替换后提供恢复通知及显式确认。借鉴恢复纪律，不把 character 文本当执行授权。
- [Hermes 记忆文档](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/website/docs/user-guide/features/memory.md)
  与 [skill ledger](https://github.com/NousResearch/hermes-agent/blob/v2026.9.24/tools/skill_ledger.py)：
  有界 session-start 记忆、按需历史检索和版本化修改证据。冻结快照也解释了长会话
  为什么可能看不到更新；LoopX 应验证新规划/恢复边界，而非只验证持久化。
- [OpenViking session 生命周期](https://github.com/volcengine/OpenViking/blob/v0.4.21/docs/en/concepts/08-session.md)：
  同步归档、异步抽取及变化来源。复用 provider task/readback 证据；抽取被接受不等于
  记忆已可用。
- [Muse 产品设计](https://introducing.muse.ai/) 与
  [Grok Bot 产品设计](https://x.ai/news/designing-grok-bot)：厂商描述了持久工作、相关
  通知及主会话中的渐进详情。这些支持产品假设，不证明内部实现、可靠性或效果提升；
  没有测试真实账户。

上述 LoopX 方案是基于原始来源检查及现有 owner 边界的推断，不是复现 benchmark
结论。公开案例不得包含私有对话、账户信息或原始语料。

## 附录 D：拒绝或替代方案

第 6 节方案继续保持拒绝，除非新证据证明它们能用更少状态实现同等产品
清晰度并保持所有不变量。

## 附录 E：事故与评审经验

- 能力 installed 或 worker route 被投影，不代表 real call 可以执行。每个
  composition plan 都要 exact authority/readiness readback。
- 成功持久化或 memory exact readback 不代表经验改变未来行为。use 与 effect
  qualification 必须分开。
- Portfolio 若复制 coverage、admission 或 decision effect，会产生一份可能晚于
  owner 纠正仍存活的第二真相。因此 Portfolio 只记录自己的 adoption lifecycle
  和 owner receipt 的不可变引用。
