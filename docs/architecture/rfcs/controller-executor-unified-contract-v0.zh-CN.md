# RFC：Controller / Executor 会话与统一受治理工作契约（v0）

- **RFC 状态：** Accepted（设计接受在合并后生效）
- **替代 / 关闭：** 无
- **交付成熟度：** Proposal；本文不交付新运行时、schema 或默认行为。
- **作者 / 负责人：** LoopX 维护者、控制面与运行时 owner
- **创建日期：** 2026-10-27
- **最近规范性修订：** 2026-10-27
- **实现基线：** `fe2b8dcbcb998647edb4bafb51932988bd7fdaa6`
- **相关契约：** [Effect interpreter](agent-loop-effect-interpreter-v0.zh-CN.md)、[会话执行模式](agent-session-execution-modes-v0.zh-CN.md)、[语义交接](capable-manager-semantic-handoff-v0.zh-CN.md)、[整体路线图](loopx-overall-roadmap-v0.zh-CN.md)
- **语言镜像：** [English](controller-executor-unified-contract-v0.md)

## 文档地图与维护契约

英文与中文文档互为语义镜像；两者出现差异即为缺陷。

第 4 节记录指定基线的源码事实；第 1–3、5–11 节定义提议的决策与验收；第 12 节记录
待决事项。合并前本文是待评审提案。合并接受设计，不证明实现、安装、实验或默认提升。
首次实现产出证据后，历史记录进入独立的日期 ledger。

## 1. 决策摘要

1. 将 quota 执行 packet 与 TurnEnvelope 融合成**一份版本化的受治理工作契约**：
   同一 schema、语义生产入口、capture、交付/恢复机制和结算身份。不同宿主携带同一契约，
   长期保留 quota/Turn 兼容视图不是目标。吸收 quota packet 的必要信息量优势与
   TurnEnvelope 的交付效率优势，一起替换二者，并相对原 quota packet 路径验收净变化。
   各 domain owner 仍决定自己拥有的事实。
2. 在可选的双会话 profile 中，**controller 模型会话是唯一面向 Core 的模型客户端，
   也是控制工作的智能中介**。
   它读契约、判断工作、与 executor 沟通并提交显式 typed Core 操作。Core 拥有合法迁移与
   回执；controller 不是第二个权威。
3. Executor 接收自然语言任务对话、原始材料和普通宿主工具，返回发现、产物、证据与问题。
   它不消费第二份 Core packet 视图，不编写 quota、Vision、lease 或 settlement 字段。
   任务正文之外的小型机器载体负责记录路由与因果关系。
4. 完整首包、精确差分上下文与自动全量恢复属于统一契约的能力。Controller 与 executor
   分别证明各自上下文连续性，不能借用对方的基线。
5. 先在现有单会话路径验收契约统一；双会话保持 opt-in，直至可比成果与完整成本证据支持
   采用。本文不启动 benchmark 或特权操作。

## 2. 问题与动机

同一任务可从 App heartbeat、external worker 或 `loopx turn` 进入，但上下文和结果
义务的呈现不同。共享决策事实已经存在，调用方仍分别选择 packet 格式、组织提示，并适配
模型编写的记账信息。再增加一份 controller 专用 packet 会继续增加分支。

例如，修复导出失败的 executor 需要原始要求、失败事实、允许使用的工作区和独立验收，
不需要学习如何编码 Vision delta 或扣除 LoopX Turn。分离这些工作可能减少干扰与重复
协议输入，但额外 controller 也可能丢约束、增加延迟或过度指挥。这些是待验证假设，
不能从会话拆分直接推出收益。

### 不变量

- 保留完整已接受要求、revision、权限、未完成必读和证据来源；短提示不能成为遗漏理由。
- 选定工作只有一个当前控制 owner 和一个已准入 executor attempt。对话、路由 ACK 和
  模型宣告完成都不是回执。
- 通知不决定执行。安静通知不能取消已准入工作；消息不能覆盖真实停止或预算 gate。
- 受治理 Turn 是有界工作/结算单位，不是 wake、模型回复或 controller/executor 往返。
  不能按每条消息另扣 Turn。
- 重试、来源修正与收尾保留原身份；不能把旧结果结算到新选中的 Todo。

## 3. 范围与非目标

范围包括：契约融合、上下文交付、按角色绑定会话、controller/executor 沟通、共享生命周期
复用、宿主 adapter、benchmark 采用、旧路径退役与可观察的产品验收。

非目标：新 Goal store、workflow DSL、通用 effect executor、权限引擎、scheduler、
强制双 Agent 团队、自动切换宿主或修改 benchmark 评分。Peer 团队沿用现有委派/验收
契约，不把业务判断移入确定性的 Core 规则。

## 4. 当前系统契约

| 现有概念 | 实际职责 / 源码 | 对目标设计的含义 |
| --- | --- | --- |
| Quota packet / TurnEnvelope | [`quota.py`](../../../loopx/cli_commands/quota.py) 对已决 payload 分普通/envelope 分支投影；[`turn_envelope.ts`](../../../loopx/control_plane/quota/turn_envelope.ts) 提供有界分节与动作绑定 | 将两者义务迁入同一 schema，再退役两条旧输出路径 |
| Work context | [`interaction_contract.ts`](../../../loopx/control_plane/work_items/interaction_contract.ts) 承载 typed 执行/上下文要求 | 扩展现有 owner，不新建 controller 决策引擎 |
| Thin heartbeat | [`task_body.py`](../../../loopx/control_plane/heartbeat/task_body.py) 按格式分支渲染产品入口指令 | 保留 bootstrap/宿主入口，统一其消费的契约 |
| `loopx turn` | [`turn.py`](../../../loopx/cli_commands/turn.py)、[`executor.py`](../../../loopx/control_plane/turn_driver/executor.py)、[`settlement.py`](../../../loopx/control_plane/turn_driver/settlement.py) 实现受治理调用与回执绑定收尾 | 复用生命周期；CLI 入口不是另一套产品 runtime |
| Managed Codex | [`codex_agent_session.py`](../../../loopx/control_plane/turn_driver/codex_agent_session.py) 与 [`codex_sessions.py`](../../../loopx/control_plane/turn_driver/codex_sessions.py) 实现原生会话执行/续接 | 属于 runtime adapter，不是竞争控制面；当前 Agent 级存储还没有提议中的角色区分 |
| Executor result | [`codex_cli.py`](../../../loopx/control_plane/turn_driver/codex_cli.py) 要求 schema 约束结果，包括 delivery、Vision/progress 字段 | 双会话 profile 把 Core 编写责任交给 controller，保留验证 |
| External worker | [`external_scheduler_worker.py`](../../../scripts/external_scheduler_worker.py) 拥有外层唤醒/命令传输 | 保留该归属；对已经证实重复的语义编排进行合并 |
| Benchmark runner | [`worker.py`](../../../benchmark/runtime/worker.py) 的 heartbeat 使用产品 thin body，Turn mode 使用公开 Turn CLI；[`sessions.py`](../../../benchmark/runtime/sessions.py) 复用原生会话 adapter | 它是产品 profile 外的评估 harness，不是独立实现目标 |
| Turn Loop Controller | [稳定协议](../../reference/protocols/turn-loop-controller-v0.md) 定义纯 disposition 函数 | 保持确定性；它不是本提案的 controller 模型会话 |

执行模式（`managed_runtime` / `attached_host`）、runtime provider、唤醒来源、单/双会话
组织与评估组是独立维度；并非所有组合都受支持。Native/plain benchmark 对照可以有意
不包含 LoopX Core，必须保留其独立性。

### C1 源码核对：已有共享 owner 与剩余分叉

源码实际共享的部分比输出名称体现的更多。`_emit_quota_result` 在
`_project_quota_cli_payload` 选择普通 CLI 压缩或 `buildTurnEnvelope` **之前**，
已经捕获完整决策。后者委托 TypeScript owner，Python 模块是传输 adapter。
Quota capture 与 Turn journal capture 都调用
[`decision_capture.py`](../../../loopx/control_plane/turn_driver/decision_capture.py)
中的 `write_decision_capture`。原生上下文交付与显式宿主 handoff 也复用该模块和既有
context-delivery owner。C1 应收敛剩余的组合逻辑与消费者假设，不另建 capture service，
也不重写已经共享的差分与 native-resume 逻辑。

| 当前来源 / 义务 | 当前 envelope 处理 | C1 必须保留的归属与语义 |
| --- | --- | --- |
| `interaction_contract.agent_channel.work_context` 与未完成必读 | `work_context` 加 `required_reads`；相同内联材料可改用引用 | 一份完整要求/证据来源与可解析引用；保留未完成必读及其完成条件 |
| 选定 Todo、recommended action、action portfolio 与 planning horizon | `action`；`turnActionProjection` 进行引用迁移与文本压缩 | 一份选定工作/动作身份；保留完整可执行条款，区分摘要与执行指令 |
| `interaction_contract.cli_channel.settlement_plan` | 复制至 `writeback.settlement_plan`，包含原 effect 身份与条件步骤 | 原样复用 settlement owner；controller 对话和新 capture 都不另建扣款身份 |
| User/agent interaction channel、execution policy 与 task orchestration | 分为 `user`、`execution_policy`、`boundary` 与 orchestration 分节 | 保留通知与执行义务的区别、权限和停止 gate；不从正文猜测 |
| Replan packet、必读与分阶段写回 | 专门的 replan 投影与延后写回 | 保留事实/来源访问、policy 特定触发与有序变更；不统一打开 stride 行为 |
| Scheduler context 与执行归属 | 携带调用方执行上下文的 scheduler 投影 | 保留真实宿主 owner 与 ACK 义务；统一 schema 不转移 scheduler 权限 |
| 完整已保存决策、action signature 与 journal 绑定 | 来源 hash、投影动作 hash、captured-decision 引用分别存在 | 区分语义完整性、精确传输字节与回执身份，不互换 hash |

这份表是 owner 与义务映射，**不是完整字段清单，也不是等价验证结果**。当前投影有文本
预算、字段白名单和引用迁移；仅比较 action signature，不能证明完整来源条款与未知字段
都被保留。C1 实现必须按确切 base/head 枚举字段和调用方；未分类项阻止切换，不能以
假定的兼容视图为由悄悄丢弃。

对上述基线的合成探针展示了这一限制：两段前缀相同、尾部不同的长 `recommended_action`
产生相同的 action-signature document，且均返回 `matches=true`，但各自的
`source_decision_hash` 不同。未注册的顶层来源字段也有相同区别。反之，完整的已注册
settlement plan 在投影中保留，改变其 Turn 身份会改变 action signature。这些观察
排除了直接把现有有界 envelope 原样提升成完整契约的路线，**不证明当前发生权限丢失**：
recommendation 属于诊断，未知字段是合成输入，完整来源承诺仍能区分两份输入。
没有测量模型收益。

## 5. 提议架构

### 归属与统一契约

```text
Core owners <-> unified governed work contract <-> controller session
                                                       |
                                      task / findings / artifacts
                                                       |
                                                 executor session
                                                       |
                                              scoped host tools

Host adapter 执行 dispatch、session fence、中断与传输。
Core owner 校验操作，提交既有 effect/settlement 回执。
```

复用 TypeScript interaction/work-context 与 effect owner。组合 owner 一次捕获它们的
当前事实，不重写 quota、replan、Todo 或 lease 决策。Python 可以收集来源、适配原生
传输。宿主特定执行归属在同一契约中显式表达，包括委托给宿主的 scheduler effect 和
相应 ACK 义务。

Schema 名称与逐字段迁移需完成下文 C1 盘点。以下是必须覆盖的语义组，**不是再加几层
嵌套协议**：

| 语义组 | 保留能力 |
| --- | --- |
| 身份与当前决策 | 契约版本、capture/revision、Goal/Agent/work/Turn 绑定、选定动作、准入、claim/lease、范围、批准、取消与停止条件 |
| 完整工作上下文 | 原始意图与要求、未满足义务、已接受证据、当前来源/revision、必读完成状态与授权详情访问 |
| 上下文交付 | Full/delta 编码、精确 base/target digest、消费 session/role/epoch 与不可变 capture 恢复 |
| 操作与收尾 | 可用 typed 操作、动作绑定/完整性、条件顺序、验证及 writeback/debit 身份、scheduler 与通知归属 |

CLI、heartbeat、managed Turn 与评估使用一个语义实例、一个 schema。Full/delta 是该
实例的编码选择，分页是传输限制，都不形成 quota schema 和 Turn schema。原生工具
可以带 provider 必需的外层包装，但不能改名、遗漏或另行决定契约字段。人类 UI read
model 继续作为展示 sink，不成为另一份可执行契约。

组合边界位于**当前 domain 决策与宿主操作绑定之后、不可变 capture 与上下文编码之前**。
所有活跃 writer 使用该边界，不能先把 quota packet 压成 envelope，再把结果改称完整
统一契约。必需来源材料可以使用经授权的详情引用，但引用、revision 和必读义务必须完整，
并能从同一次语义 capture 恢复。

“一份 capture”指一次调用的一份不可变语义观察，不是所有用途只存一个文件。完整规范
实例、编码后的 full/delta/page 产物和 Turn journal 的绑定应保持可区分。精确文件字节
digest 保护传输，canonical hash 保护语义内容，既有 effect 身份保护只结算一次；三者
不能互相替代，也不授予当前权限。恢复引用需要身份/位置时，在序列化前预留；不要要求
文档包含自身最终字节的 digest。编码、分页、native resume 与恢复都从已保存实例派生，
不再执行 guard。合法的新选择或权限复核可以产生新 capture，但不能改写旧观察，或把
未结算结果悄悄绑定到新观察。

### Controller 的智能与语义压缩

智能来自 controller 会话本身。它在架构上把控制面的完整事实与义务转化成 executor
可执行的有用工作，再把 executor 的发现解释为有依据的 Core 操作。“智能压缩器”描述
的是对控制工作的这种中介作用；少查几次或缩短文本本身不能实现它。既有摘要已把阅读、
推理与 typed 操作交给 controller，这里补充的设计决定是**把判断与注意力放在哪里**：

| 判断 | Controller 的职责 | 保留的边界 |
| --- | --- | --- |
| 下一步做什么 | 比较当前 Goal 缺口、证据、可行工作与完整成本；有依据地继续或换路 | 合法准入、claim、lease、预算和迁移仍由 Core 决定；controller 的判断不授予权限 |
| 哪些工作一起交接 | 选择连贯、可验证的任务，决定自己吸收哪些控制事务 | Executor 在范围内自主选择局部工具与步骤，不逐次请示，也不编写 Core 记账 |
| Executor 必须知道什么 | 交付任务完整约束、相关反证、原始材料和未完成必读，用任务语言解释判断 | 摘要不是另一份契约，也不证明读过来源；相关性未知时，依赖工作前须补足材料 |
| 结果改变什么 | 区分发现、已验证产物、受阻尝试与已接受成果，判断继续、调查或 replan | 独立验证与当前 Core 回执决定验收和结算，不能由任一会话的叙述替代 |

区分三种操作：确定性的 composition owner 组合 domain 事实，不另造 policy；既有
codec 编码可精确恢复的 full/delta 输入；controller 按任务进行语义选择和推理。自然
语言任务可以省去无关控制语法，不能承诺逐字节等价。其充分性要由约束保留、executor
实际决策与验收成果检验，同时原始获授权材料仍可到达。不能用第二套 schema、模型编写的
状态库或按关键词判断相关性的分类器替代这些操作。Controller 笔记是工作上下文，不是
新的持久权威。

例如导出修复已产出有效文件，但打包检查失败：controller 可以保留有用修复，交接有
证据的打包纠正，并保留原未结算身份。它不能把结果缩成“完成”、丢掉失败检查，或要求
executor 学习 writeback 格式。如果同一证据表明修复违反共同要求，则先修改任务并交付
该要求，再继续依赖工作。这是实际判断后果，不是压缩比例目标。

成本摊薄仍是假设。Executor 继续普通获授权工作时，controller 可以吸收多次控制密集
交互，但每次新选择、权限变化、取消与收尾仍遵循既有 owner 和新鲜度规则。不要为每个
局部工具步骤增加一次 Core 往返或新 Turn，也不能缓存旧权限来跳过必需复核。小任务、
快速变化的共同约束和频繁 controller 恢复，都可能让额外会话的成本超过节省。

Controller 失败不能让 executor 变成备用 Core 客户端。暂停依赖的 dispatch，恢复原
capture 与 session/attempt 记录，在必要处读取当前权限，并按原身份对账已准入结果。
Executor 摘要不能重建缺失的控制义务。必需材料矛盾或不可达时，依赖工作保持阻塞；
独立获授权工作继续沿原路径推进。

### 评审新鲜度与恢复

完整 capture 证明当时观察到了什么，不会冻结写回时的有效性。语义内容身份、评审
相关性和结算身份应分别保留。由其他 Agent 持有的 supporting outcome 仍可能约束
共享 Goal；不能仅凭 owner 标签，从评审依据中排除其验收或要求状态变化。缩小失效
范围需要明确的依赖范围规则及共享约束测试，不能依靠角色名过滤或从文案推断白名单。

同一已准入 obligation 拒绝过期评审时，当前 typed owner 应返回完整的当前评审依据和
适用控制指令。消费者先阅读并重新判断，再以原身份重试原写回。不要返回丢失绑定的
通用写命令，也不要为了获取 writer 已使用的事实而要求再次 guard。依据再次变化仍
应拒绝；返回上下文不等于接受决策或授权扣额。这一恢复归现有组合/写回边界，应在
C1 writer 切换中保留；不代表已缩小跨 Agent 失效范围或交付完整统一契约。

### Executor 输入与输出

采用**自然语言工作交流，加运行时持有的元数据**，不提供 executor 专用 Core 视图，也
不退化为无记录聊天。Controller 用适合任务的正文说明目标、相关事实、边界和验收，可
steer、回答问题或修订请求。Host 通过既有 request/dispatch 记录携带 opaque request/
revision、attempt、session/generation、因果序列与产物引用；模型不必在正文复述这些
标识。不增加独立消息账本。

原始要求和授权证据以附件或宿主材料 reader 提供。Core capture 与访问校验由 controller/
runtime 完成，executor 不通过调用 Core 取材料。必读要求由实际材料交付/读取机制保留，
controller 转述或路由 ACK 不能替代。必需材料不能交付时，阻止依赖它的工作；独立工作
可以继续。不可信来源正文不能获得指令或工具权限。

Executor 在委派范围内自主选择工具和步骤，不必每次调用工具都询问 controller。工具
权限由宿主/provider 强制，包括文件与进程访问范围；仅从提示中移除 Core 工具名不构成
隔离。能访问 Core 凭据的无限制 shell 会违反这个 profile。不支持所需隔离的宿主应明确
显示不支持，不能静默声称双会话已经合格。

输出保留用户要求的交付物、产物引用、实测验证、失败与不确定性。Controller 理解结果、
执行或请求独立验证，再提交显式 typed Core 命令。Core 不从 executor 散文中的子串推断
完成、权限或 replan。宿主进程退出不等于业务完成。验证失败可在同一已准入范围内反馈
并继续工作；实质性的范围/revision 变化需要重新核验 dispatch。

**长度要求与语言无关，并随任务变化。** 先保留可独立执行的完整含义，再优化大小。实验
起点可采用：普通任务正文 200–800 model tokens，复杂交接 800–2000，结果摘要
150–600。这些是未经验证的起点，不是 schema 上限、默认值、通过条件或截断规则。
使用实际模型 tokenizer 计量，并记录语言/内容构成。代码、数学、表格、来源材料和完整
交付物保留自然格式；摘要、附件、补读和所有往返合计成本，引用不会消除材料成本。
同会话的小修正在连续性有据可查时只传变化，没有最低长度要求。

例如，controller 可以说：“修复附件复现中的导出失败；保留原始排序要求，在分配的
工作区操作。验证回归，说明剩余缺口。”结果可以解释修改并引用 patch 与独立测试。
这两段自然语言均不替代原始要求、权限授予或 canonical 完成回执。

### 会话、生命周期与恢复

首版使用一个逻辑工作 Agent 和两个显式绑定的原生 session role。不能只为认知分工再建
一个 Goal/Todo/quota 身份。扩展既有 session binding owner，加入 role/generation、
独立有序队列及上下文基线；不能把两个 native ID 写进当前单一 Agent 级槽位。
一个 controller 拥有控制对话，选定工作最多由一个 executor attempt 执行。Controller
role 不是第二个并发业务 executor。Peer 或并行执行仍需既有独立 claim/lease/handoff
契约。

1. 既有入口取得新鲜准入或恢复应结回执，把捕获的统一契约交给 controller。
2. Controller 围绕 Goal、未满足要求、已有结果和可行工作判断，Core 验证其选定操作。
   Replan policy 继续决定何时必须 review；先看事实不意味着把 stride 独有触发条件变成
   默认 policy，也不强迫无意义换路。
3. Dispatch 前，host 绑定 work/request revision、attempt、session role、generation、
   范围与预算；调用 executor 前记录 intent。
4. Executor 工作并返回观察/产物。Readback 区分投递、执行、结果可用、独立验证与已接受
   写回。多次交流不产生多次 quota 扣除。
5. Controller 提议接受或修改；既有 effect owner 校验并对原 Turn 结算一次。后续新鲜
   决策继续可执行工作、按记录的事件/时间等待或停止。

| 故障 | 必需处理 |
| --- | --- |
| Controller 在 dispatch 后崩溃 | 先恢复已持久化 attempt 与 executor 状态/结果，再决定是否重发；不能启动重复 executor |
| 结果/结算响应丢失 | 以原幂等身份读既有回执，仅重试未解决的 effect |
| Executor 重启或上下文被压缩 | 围栏旧 generation，重新核验连续性；未知时完整交付当前材料 |
| 取消、需求变化、lease 到期或结果迟到 | 每次拥有的 dispatch/write 前重验当前权限/revision；保留证据，拒绝过时接受并核对已准入 effect |
| 工具执行中 Stop | Host 在受支持范围内中断；分别报告 ACK 与真实退出。保留未知/执行中 effect；停止回执不能证明物理回滚 |
| Controller 不可用 | 既有 host/Core 停止与预算机制仍有效；暂停依赖工作并恢复，不能让 executor 临时成为 Core owner |

### 差分上下文

复用现有 context-delivery owner 与不可变 capture 机制。先完整交付，只有在精确消费
session、role、epoch、schema、source capture 下证明已完整送达并保留上下文，才能
发送注册子树的精确 delta。保留删除、数组顺序、null、空值和完整字符串。未知字段在
注册复用语义前每次完整发送；delta 加元数据更大时发送 full。

当前权限、准入、取消、未完成必读、结算与 scheduler 事实即使未变也新鲜内联交付，温态
传输不能减弱正文义务。缺失/乱序 delta、截断、压缩、损坏或未知基线自动从同次 capture
全量恢复，不重新准入/扣额。执行仍检查来源有效性与当前权限；恢复旧 capture 不能复活
已取消工作。Executor 任务对话独立消费语义材料，不是 controller Core packet 换名后的
delta。

冷态交付默认展开当前 Agent 的成果。其他 Agent 的独立成果保留可发现索引，包含
身份、owner、目标、revision、工作关联、未满足要求标识、当前 review/check 状态及
精确 capture 正文引用。全局 Goal 意图、共同要求、权限与停止条件始终进入必读输入。
跨 Agent 依赖、共同验收，以及影响当前决策的阻塞或矛盾证据，须在相关决策前自动
展开，不受 owner 限制。Goal 级重规划和整体验收扩大到所有受影响成果；局部交付
无需重读无关历史证据。Owner 只是默认范围，不是权限边界或充分的相关性判断。
工作未绑定、关联未知或来源形状不受支持时，保持完整交付，直至既有 typed owner
能够证明较窄的必读范围。

Canonical 契约仍保留完整要求和证据。以现有同 capture 成果索引及 replan 分阶段
reader 为起点，合并普通/replan/detail 管线；索引须补足当前决策事实，不能只有成果
数量。必读范围保留要求全文，支持性来源和 review 细节通过绑定完整性、顺序明确的
reader 按需交付；依赖该材料的决策须先读完它。当前结论、新鲜度与未满足要求仍
内联，Agent review 不能变成独立检查。界面折叠本身不会减少模型输入；交付投影、
required-read owner 与 host 恢复契约须一起迁移，已有 capture 保留原完整读取义务。
正文缺失或保留情况不确定时，执行原 capture 支持的恢复，不能重新准入或假定已读。
不能为字节目标缩短保留要求、裁剪声明来源或靠正文关键词推断相关性。

C2 须在相同冷态、已保留温态及上下文丢失来源上验证：默认自己工作、跨 Agent
依赖/约束、未绑定工作、同伴证据变化、广域 replan 和 Goal 验收。计入实际模型输入、
分页及正文读取往返、恢复、延迟和完整成果成本；首屏变小不能抵消必要补读或漏掉
约束的成本。这是拟议交付行为，替换与回滚完成资格验证前，仍以当前完整来源 reader
为准。

第一项 reader 前置现已有实际普通 CLI 调用方：给定原 capture 文件和文件字节
SHA-256，`quota context-read --outcome-ref` 分页读取成果索引，附上返回的精确引用
则分页读取该成果全文。普通与 staged replan 复用同一 TypeScript registry/index
owner；索引保留 owner/工作关联，并将当前 check/planning/review 事实完全相同的
要求标识分组，缺失事实保持未知。相同正文共享引用，同 id 的变化版本仍分别保留。
详情保留原描述、声明来源、review 证据与未知字段。每页重新核验原文件，返回的
接续命令还绑定所选 JSON 字节 digest，拒绝在原文件 hash 未变时混合变化的渲染。
游标仅定位所选 JSON，不能证明完整 capture 已保留。这是读取前置，尚未切换默认自己
Agent：原完整读取义务、Host 连续性和 Goal 权限保持不变。

既有 canonical acceptance 读取还从同一份完整工作快照提供版本化的候选读取计划，
不增加 authority 读取或 RPC。计划包含自己的成果、选定工作、传递的 typed 依赖/
lineage、共同工作/check scope、精确声明来源或 review 证据，以及失败/过期观察及其
相关工作。工作未绑定、关系/生命周期不可用或 Goal 全局检查均保留全部成果。
捕获索引仅在计划与 Goal/Agent/Todo、provider revision 和完整验收来源 digest
一致时标记 `required_in_scope`；oracle 事实变化、额外载体版本或不支持的计划均
扩大为完整读取。原完整 capture reader 仍返回 `scope_applied=false`：索引不改变
该 capture 的完整读取义务；普通 acceptance inspect 保持原完整格式。

可选 native transport 现在将计划同时接入 required-read、交付与恢复。
`outcome_context_projection_v0` 只标识派生的模型展示，不是部分 canonical decision
或新的决策 owner。原完整 capture 保持不可变和 digest 绑定；展示展开全部必读成果，
保留所有全局字段、完整当前成果索引及精确原来源引用。未知可选字段、未解决的
acceptance read 或不认识的来源指导均保持完整交付。既有 interaction owner 仅替换
自己精确生成、已履行的成果读取指导；目标采用、原始和累计要求、工作核对、独立检查
区别、新鲜度、权限及停止义务保留，不引入自然语言相关性分类器。注册关系闭包仍
不能证明任意语义独立性：依赖可选成果前先读完整正文，相关性未知或需要 Goal 全局
决定时扩大为原完整来源。

native candidate 与压缩恢复指向真正交付的必读模型产物，而不是机器保存的完整
canonical 来源。既有 delta codec 因而会交付新增必读正文，不能从隐藏 baseline 借用
模型未读过的可选正文。当前完整来源的恢复引用先于可选 scope 组合发布，再在编码前
替换为精确必读产物；组合期间中断不能让恢复仍选中前一次 capture。完整分页覆盖仍只是
显式 native-hook 连续性假设，不证明模型
保留。可选详情复用同一原来源 registry，重验文件和派生展示，不确认完整 capture
保留。transport 关闭和旧 capture 保持原行为；其他 Host adapter、正式 App 采用、
实际模型决定及匹配验收成果/完整成本仍未资格。

同一 24 成果/253 要求合成快照，以 61 组交替温态真实 provider owner 调用对照
旧实现：File 中位数 5.890→6.317 ms，SQLite 为 2.239→2.521 ms；p95 分别为
15.267→17.580 和 6.504→9.412 ms。原响应字段全等，计划元数据增加 405/415 个
序列化字节。此对照仅隔离同一 TypeScript 进程的 acceptance owner，不含 bridge、
CLI、冷启动或模型成本；此基础在 native 范围切换之前增加成本，不是效率提升。
native 展示还增加精确必读产物及既有 codec 之前的一次 typed owner 调用，必须与
分页、补读和恢复成本一起计入；首包缩小不证明净收益。

native 切片通过 406 项真实入口 Python（五项显式 native/SDK 案例未资格）、169 项
TypeScript 和四项真实 Codex `0.163.0-alpha.11.1` 静态 loopback 检查。后者核实际
模型请求中的冷态全文、保留差分与同 capture 恢复字节，不证明自由生成模型行为。
四个新增 File/SQLite 范围断言在 `1ac29089f` 失败。集成中发现的恢复引用回归已修复：
只为显式 scoped 产物替换 metadata，普通恢复保留原文件字节身份。
中断回归最初因仍选中前次 capture 而失败；将当前恢复发布前移到可选 scope 调用之前
后通过。在运行源码 `e1c69289a`，标准 premerge 有两项在 `8b37000b3` 同样复现的失败：既有
`run_loopx_turn_once` 决策函数过大，以及前序 owner 收敛后 `turn_route` 的 producer
覆盖过期。本 native 增量未修改这些文件；历史结果不是通过或例外。集成已先 rebase
到 `b69021498`，再到 `f63647e2e`，保留目标主干的原生输出预算行为，并采用 [#7520](https://github.com/loopx-project/loopx/pull/7520)
对真实 TypeScript producer/scanner 的修复，并在 `fec9cd3af` 将原 capture 恢复从
run-once 编排中分离，保留既有 capture owner 与 session 恢复。大小阈值不变。当前
完整组合的资格归精确集成 head 的 PR 证据，不能沿用旧 native 切片的测试计数。

最终运行源码 `e1c69289a` 的成本含上述恢复发布顺序修复；对照不可变 `8b37000b3`（前序 C1 与相同 target-main 大页配置），24 成果/253 要求
来源上，每侧三组温态 cohort、每 provider/阶段 21 样本。温态新增真实 typed 依赖并核
先前未读正文的交付。File 冷态/温态/恢复的完整分页序列化输出为
105,380/40,473/103,372→37,908/33,495/38,594 字节。延迟中位数：

| 阶段 | File，原→新 ms | SQLite，原→新 ms |
| --- | ---: | ---: |
| 冷态全文输入 | 72.856→48.749 | 67.237→46.477 |
| 温态范围扩大 | 38.744→51.944 | 35.953→52.165 |
| 上下文丢失后全文恢复 | 109.126→61.425 | 109.725→56.777 |
| 索引加一项可选全文 | 96.918→108.557 | 92.460→109.251 |

新增 scope RPC 仍在，温态 bridge 调用由二增三；File 温态产物文件共
131,020→160,192 个序列化字节。索引/详情仍为两调用，但增加派生展示重验；冷态和恢复由两页
变一页。测量含温态 runtime 下 Python native 投影、分页与生命周期，未控制机器负载；
不含首次验收收集、进程启动、子进程 CPU、模型/缓存 tokens、账单、人工/实施及验收
成果。冷态/恢复传输改善，温态及详情中位数退化；这不是原 quota 对照或完整成本正向结论。

小来源给出体积节省的反例：四个真实入口 File/SQLite 夹具各含两成果、四要求，
由 36,293–40,548 增至 37,948–42,203 字节，每项恰好增加 1,655 字节，索引和范围
元数据超过短可选正文。提案不从大夹具推导语义省略规则或生产体积阈值。

同一 24 成果/253 要求的合成来源，按旧的仅数量索引形状渲染分配 8,190 字节，
staged 状态分组分配 18,979 字节，原 12 KiB 索引预算失败。保留这些当前决策事实，
据此将独立索引上限调整为 20 KiB；history、horizon、完整 envelope 与生产性能
限额均不变。完全相同状态分组避免逐要求重复状态字段。这里只测序列化字节，
不是 token/延迟/成果收益或不可变 base/head 对照。上述源码范围检查和传输成本不证明
一般语义独立性或匹配验收成果/完整成本收益。

## 6. 备选方案与取舍

| 备选方案 | 结论 |
| --- | --- |
| Quota 与 TurnEnvelope 长期保留兼容视图 | 拒绝作为终态：仍有重复选择/渲染与字段漂移；仅保留有界迁移 decoder |
| 给 executor 一份紧凑 Core packet 视图 | 不作为双会话默认设计：仍有协议编写负担和两个面向 Core 的模型客户端 |
| 没有运行时绑定的纯自然语言聊天 | 拒绝：无法可靠核对过时/重复结果、取消与重启 |
| 始终使用双会话 | 拒绝：controller 成本、延迟和交接损失可能超过收益；保留合格单会话 profile |
| 一个万能 runtime/effect executor | 拒绝：host、scheduler、事务与 provider 权限不同；共享语义与回执，不共享执行所有权 |

## 7. 安全、隐私与兼容性

Controller 判断不授予额外权限。运行时凭据与 Core 写能力对 executor 不可访问；按
当前访问规则交付范围内工作材料。撤权和取消在模型记忆之外强制执行。敏感材料保留原有
受众、保留期限及模型 provider 选择边界。

现有单会话与可选能力关闭路径需要行为等价。不支持的 profile 显式失败。旧持久化
capture/回执保留原版本/签名与 replay 身份，不能改写成新 schema。混合版本 live writer
通过围栏处理，不靠尽力而为的散文转换合并。Schema 删除须满足 RFC 索引规定的逐字段
盘点、无损映射和显式维护者批准。

## 8. 迁移与回退

### C1 wire 布局建议

目标是吸收 quota packet 的必要信息量优势与 TurnEnvelope 的效率优势，一起替换
二者。“完整”指必要语义完整，不表示今天 packet 中所有重复诊断都永久内联。下述
保留根路径的无损候选只是迁移对照，用于在消费者迁移时发现遗漏与引用断裂；给它改名
或转发全部旧字段，均不能满足最终设计。

逐字段、逐条指令盘点所属规则与实际消费者。在依赖它的决策之前，必须具备当前任务、
原需求及修订、相关证据、权限、必读、执行/停止条件和原结算义务。通过 typed owner
识别并消除已证实的重复表达与无关诊断噪声。稳定材料只有在保留证据已验证，或来源
可解析且绑定完整性、读取顺序明确时，才可引用或差分交付；缺失上下文须先恢复再执行
依赖工作。引用本身不证明模型收到了约束。不能凭正文启发式把未知字段当成可选或静默删除。

在同一规范契约内吸收 envelope 的有效特性：清晰的执行身份与动作组织、单一机器
binding、减少重复指令、有界交付与可靠详情/恢复。Full/delta/detail 是同一实例的
编码，不是永久并存的不同语义视图。原始来源证据保留在既有 owner，不再输出第二份
可执行 packet。最终布局可在引用映射、消费者迁移与实测收益成立时调整；保留根路径
不是不可更改的产品约束。

| 来源或关注点 | 候选映射与资格要求 |
| --- | --- |
| 迁移中的当前 decision 字段 | 无损对照保留路径和值；最终映射前枚举各模式，区分必要语义、已证实重复与纯诊断材料，不能凭某次 packet 推断可选性。 |
| Work context、必读与结算 | 保留既有 `interaction_contract` 路径与 owner。现有根相对引用保持含义；有意搬迁须显式迁移引用与 reader，不能按模式改写正文或命令字符串。 |
| Host-operation binding | 从真实调用方 execution context 组合，验证与源权限一致；binding 冲突时拒绝，不能选择方便的表示。 |
| 内容与传输完整性 | 对整个语义实例作完整性承诺，独立于原紧凑 action digest。精确 capture 字节校验和原结算身份仍分别检查；更换传输编码不创建新 effect。 |
| 历史回执与 reader | 未结工作按记录版本解码。临时内部 adapter 可以服务既有效果解释器，但不能成为第二份输出契约，也不能用紧凑副本替代模型必需输入。 |

一个合成布局探针使用真实隔离 File/SQLite quota capture 和既有 TypeScript context
codec。保留源路径时可使用 warm delta；将全部事实搬至新 `facts` key、同时沿用旧
`quota_packet` surface 时，以 `delta_not_smaller` 回退 full。两种布局均精确重建，
epoch 变化均要求 full。Retention 为合成声明；这衡量 codec 行为，不证明宿主观察、
模型理解或成果/成本收益。它排除的是**沿用旧 surface 却未映射的根路径搬迁**，不是
一般性的嵌套布局。若映射与消费者收益足以抵偿迁移成本，仍可设计嵌套布局。

因此 C1/C2 须随 wire 迁移显式版本化 context surface 及其 fresh/reuse 路径，不能
改名冒用旧 surface 或凭 Turn 身份继承基线。迁移后的首次交付使用 full；只有验证了
兼容的 retained baseline 才可复用。资格覆盖旧 action 投影之外的字段、基线缺失、
session/epoch 变化和同 capture 恢复。这里消除了具体布局风险；下一步仍是共享 writer
与最终消费者替换，不能再建设一个布局框架，也不能据此宣布 C1 交付。

尚未完成的实现候选 [#7430](https://github.com/loopx-project/loopx/pull/7430) 已在既有
TypeScript owner 中、quota 与原生 Turn 捕获之前组合完整来源，并传入实际 Codex
stdin。隔离 File/SQLite 的真实 CLI 加合成可执行程序覆盖来源完整性、失败 attempt
恢复与一次结算。这推进了两个 writer 和最终输入替换，尚未取得 C1 切换资格。
候选现已通过规范 CLI channel 路由能力重入，并在完整契约的 Codex 输入中保留原始
hash 绑定的 review/writeback reader。合成 File/SQLite 执行验证了新 obligation 绑定
和原 Todo 绑定 replan 的材料读取。原生启动现只从已记录的结算身份取得唯一因果
绑定，观察到的语义 replan obligation 不得替换原 Todo 绑定。源 quota 与健康拒绝
仍阻止宿主启动。Release fixture 现只导入一次最终来源；这些可执行检查不代表真实
模型或已安装宿主验收。结构化宿主 renderer 现与 Codex 共享完整输入传输及原 hash
绑定的材料读取器。实际隔离 File/SQLite Turn→DSH runner 接收同一捕获来源；历史
请求 prompt 正文保持精确一致。结构化 DSH 仍使用 Todo 范围的 session：只有
obligation 的 replan 在派发前被拒绝，不伪造 Todo 绑定。完整来源有意保留未激活能力的
配置和诊断事实；这些事实不会激活 recall 指令或 reflection 结果字段。这是已披露的
展示变化，激活边界与历史等价分开验证。
Markdown quota 入口现渲染同一份完整契约，不再另外挑选事实子集。真实 File/SQLite
CLI 捕获验证普通入口与原 envelope flag 均保留全部字段和指令，JSON 类型与任意
来源正文经过渲染后不丢失。外部 worker 的 File/SQLite 准入、未决 attempt 恢复和
重启检查已通过完整 producer 输出；但不透明 shell wake 仍不能证明该原始契约已
交付 executor 模型。
真实 benchmark checkpoint preflight 已验证 File/SQLite、replan/非 replan 及
context delivery 开关组合下的完整来源相等；可选 full recovery 保留同一来源，初始
准入只有一次，reader 不写状态。这只证明契约传到 checkpoint 的模型输入文件，
不代表原生 session 捕获或 benchmark/模型成果。此前只要外层 source hash 相符，
`replan-view` 就会接受不支持的版本或无效的内部绑定；现在复用既有完整契约 validator，
未版本化的历史读取保留原解码器。
successor 写入指引现已在完整 capture 前绑定到已决定的 Turn，复用历史 staged
读取的同一格式化 owner。完整契约包含原 Turn/Goal 绑定，以及结束前完成创建回执
所返原结算、不得执行 successor 的义务；后续现代材料读取不再追加第二次绑定。
真实 File/SQLite 的 App heartbeat 和 generic CLI 测试从该内联契约创建 successor，
拒绝过早 spend，原身份只结算一次并保持 successor open，不依赖再次 guard 或
staged reader。这改变了完整输入的 successor 指引；未版本化历史读取保持原行为。
仅结算、capability intent、未绑定和 context 不匹配的输入保留原写入指引。
checkpoint 的旧 reader 测试本身不能证明这条生命周期。剩余材料 reader 退役及
其他 C1 消费者不能为满足历史断言而恢复第二份精简可执行事实视图。
完整契约边界现复用既有 decision/frontier 词表，在 capture 前与后续读取时拒绝未知、
空值或类型错误的根执行 action；hash 合法不能证明消费者理解未知执行语义。可选证据
仍完整保留，包括陌生版本和形似 action 的字段名；未版本化历史 action 路由不变。
这只验证根 action 边界，不代表所有必需语义字段均已验收。真实 File/SQLite 原生 CLI
的 producer 故障在 capture、派发与 spend 前被拒绝；前一候选会到达更晚的启动拒绝，
这些探针不证明此前存在无保护执行。Selected-work 测试现读取
完整 interaction context，并通过真实原生 `turn plan` 入口验证宿主输入。真实
File/SQLite 检查保留完整任务正文、规范 note、作用域 gate、需求新鲜读取及来源丢失
恢复，不把 App capture 换绑到原生宿主。此前六项旧根布局断言失败仍作为历史失败
保留；它们后续的语义检查现在已实际执行。
此前六项 staged-reader 失败作为历史证据保留。完整 composer 现于 capture 前
复用 Explore 既有可用性修正：已证明没有 Todo 的 pure replan 不再展示必须绑定
Todo 的结果附件写法。证据、独立图命令与全部工作前义务保留；陌生/自定义指令及
歧义绑定保持原文。有 Todo 的可用附件写法仍完整内联。这是可用性修正，不改变
权限，也不新增精简事实视图。真实 File/SQLite 原生宿主输入覆盖两种绑定。
Staged 测试现消费完整 CLI 输入，并对同一不可变 capture 运行历史材料 reader
codec，覆盖恶意 PATH、不可用 registry、身份/验收篡改及实际 writer。测试比较
全部 successor 绑定指令，不再排除完整输入已拥有的字段。附件拒绝 fixture 提供
合法、显式的 finding status，确保实际到达缺少 Todo 的身份门。23 项 staged
检查与四项原生宿主用例通过；这些相互重叠的范围不能证明全部消费者退役或打包
宿主资格。
原生 context fixture 现将两种 CLI 写法都绑定到 `complete_turn`，并将重建后的
全部事实与原始 capture 比较。首次四项失败源于恢复 oracle 仍使用旧 surface；
Unix rendezvous 检查此前只比较内容，允许 full 回退掩盖同一错配。修正后要求
原 target 和实际 delta 模式。十项用例通过真实 Codex exec/app-server/code-mode
及 worker resume，使用隔离 SQLite 与确定性本地回环响应，覆盖压缩、进程恢复、
历史文件替换和未合格 replay profile。未调用付费模型或启动 benchmark 实验。
当前完整 quota-settlement CLI 模块 158 项通过，包含历史 cache parity 用例；
此前间歇性错配的原因尚未证明，不能改写为历史通过。
File/SQLite context→DSH 子进程检查现使用完整 capture 和生产 request builder，
不再把当前 CLI 响应当作旧 action projection。普通 capability 开/关输入保留全部
来源事实；已绑定 replan 使用生产 capture binder，并执行实际交付的原证据 reader。
来源事实、schema 版本或 replan 义务被修改时，在 runner 接缝前拒绝；显式历史引用
reader 覆盖仍保留。八项聚焦用例通过，使用注入的记录 runner，未调用真实 DSH 模型；
本次测试迁移没有修改 runtime 代码。
完整契约边界现进一步复用既有 interaction decoder，校验必需版本、channel 类型、
通知策略和执行一致性。四种非法语义原先能通过组合；现在即使 producer 提供正确
完整性哈希也会拒绝。保留原对象及未知可选事实，不用 decoder 规范化输出替换原文。
File/SQLite 原生输入检查确认不支持的内层版本在 Codex prompt 执行或 DSH runner
加载前拒绝，且不扣额。完整契约/context/DSH/stdio 集合 352 项通过，TypeScript
检查及聚焦 lint 通过。这只证明上述交互语义，不代表所有必需字段或打包宿主已验收。
在该历史检查点，剩余 cache 一致性调查、其他旧 reader 断言、分阶段指令等价和实际 optional/external
消费者、版本准入及打包宿主连续性仍未完成。缺少显式原生 runtime 的 operation SDK
检查属于未测，不能算资格。默认 writer 变更尚未交付；PR 因作者实现未完而保持
Draft，不是仅等待评审。尚无模型或成本收益证据。历史 effect 解码只是临时迁移，
不得成为第二份永久可执行视图。

External scheduler 候选在读取节奏或唤醒许可前，通过
`assert_complete_turn_decision` 复用现有 TypeScript 完整 reader owner 验证带版本输入。
这是纯读取：不重组已捕获的 host binding，也不生成旧 executor 视图。缺失 binding、
不支持的版本／必需语义、内容篡改和无效同 capture 引用均在 wake command 前拒绝。
无版本普通 quota packet 暂保留原兼容路径，退役须核对受支持的已部署 worker。
File／SQLite 源码 CLI 到 worker 验证使用本地子进程标记，不执行模型；这里只取得
该输入边界资格，不代表 attached host 任务交付、结算或验收成果／完整成本收益。

可选 Ark adapter 复用同一宿主输入 owner 的 `extract_turn_authority`／
`render_prompt`，无需在可选包里新增契约 builder。File／SQLite 的真实
`turn plan --host generic-cli --iteration-context fresh` 已将完整请求送入
真实 Ark SDK，并使用合成 HTTP transport 验证。检查对照实际 user-message
中的契约与原 CLI 来源，恢复持久化 running receipt 时不重新提交输入，重放完成
结果时不再调用 provider；来源／版本／binding 被篡改或完整输入显式为 null 时，在调用
provider 或创建 receipt 之前拒绝。Core 状态与记账保持不变，历史 Ark 请求和恢复
测试继续保留。这验证了 SDK wire 边界，尚未验证实际云端 session、模型成果或
Core 结算。Ark 与 MemT 现共用真实 File／SQLite CLI 请求夹具。MemT 在 provider IO
之前拒绝该完整输入，因为可执行的 legacy registry 不提供精确 `goal_ref`。
现有 `source_session_v1` 虽有精确生命周期，但属于 lifecycle-only；真实通用 Turn
规划入口会在 capture 或执行前拒绝它。规划后补造引用会掩盖这一生产者／消费者缺口。
完整输入损坏也会在 provider 或 receipt 创建前拒绝；历史 MemT SDK／snapshot 测试
继续保留，但不能证明端到端完整输入送达。MemT 路径的所属边界修复须将可执行 registry
准入的精确生命周期沿共享 Turn 请求与恢复 owner 传递，并保留重建及旧实例拒绝。
不能通过放松 MemT 隔离或启用 lifecycle-only profile 来绕过。这是已证实的采用缺口，
尚未取得原生记忆路径资格。operation-SDK 原生 runtime 与打包 App 采用也仍待验。
内部 effect envelope 与历史请求 decoder
仍服务结算／结果／会话 caller；只有这些 caller 和未结 receipt 已采用替代契约后
才能移除，不能仅因模型输入不再包含旧 envelope 就删除。

新请求的 Codex／DSH 共享 session lineage 已从完整契约读取，复用 TS 的
完整输入校验与自适应主任务选择 owner。模型输入校验使用同一 owner，并拒绝完整
输入与内部 effect adapter 的 Goal／Agent／Todo 不一致，即使两份摘要各自有效。
完整路径不再重复调用 Python 的版本与签名校验；历史请求继续使用原 decoder，
持久化 session schema、路径与 scope 不变。File／SQLite 真实 CLI capture、失败恢复
和一次结算重放通过；普通／自适应任务在 todo／agent scope 均能读回 Codex／DSH
原 session。相同夹具在迁移前基线保留合法路径通过，但新增身份冲突用例失败，
在候选通过。原生 operation SDK 环境仍有跳过，不能宣称其 runtime 已验证。
会话身份迁移未移除全部结果资格与结算的内部 envelope caller，尚未达到完整退役
或净成本收益结论。MemT 生命周期缺口仅阻塞
其依赖路径，不阻塞这些独立迁移。

规划与失败重试的 session 查找现在传递原始完整契约，journal inspection 也复用
同一身份 owner。缺省契约仍解码历史记录；显式 null 契约会拒绝。实时与缓存 vision
结果的归一化也复用该身份读取，不再从 effect adapter 重建 Goal／Agent。
会话 scope／生命周期门与原生 app-server callback 仍由既有 owner 负责。
本片移除这些仅用于读取身份的旧调用，尚未删除内部 adapter：执行路由、结果证据／
profile 判定与结算仍须迁移。
自适应恢复夹具在签名前构造：历史未验证选择仍按 primary Todo 写回；冲突的已验证
任务来源在 host 执行和额度支出前拒绝。既有来源校验门保持有效。
完成恢复验证读取原始持久完成及支出回执，不重新提交完成。四项过时的重复调用断言
在前一基线同样失败；修正后的验收保留真实 File／SQLite 中后续任务文本变化和历史
未带来源凭据但已经接受完成的恢复场景。

完成任务正文的解析现由既有 TypeScript interaction 来源 owner 统一负责。
新 `run-once` 请求先校验完整 capture 与内部 adapter 的绑定，再解析并计算完整
契约中已准入正文的摘要，不再从 adapter 重建该来源。历史 journal 经 Python
薄 transport 使用同一解析规则，保留 Unicode／空白原始字节、缺失／重复来源拒绝
及已接受完成回执的恢复。此次删除一套 Python 语义算法，没有新增第二种 packet
视图。迁移前 `599cf1f72` 的 48 项 File／SQLite 捕获及完成检查通过；候选完整来源
检查也拒绝被修改的 binding 和显式 null 输入。历史解析原为本地 Python，现在
增加一次 TypeScript 边界调用；完整路径净成本尚未测量。结果 profile、证据解释
和其余结算消费者仍须继续迁移。

Codex 结果 schema 与共享 candidate adapter 现通过同一经过校验的完整契约
profile 读取判断证据要求是否适用；新请求不再从内部 capsule 决定该要求。
该读取与模型输入校验复用既有 TypeScript 完整性／来源／身份校验器，没有新增
packet 视图、wire 字段或按证据字段分别调用。缺省契约仍解码历史 capsule；
显式 null 或被修改的契约会拒绝。空／非对象 profile 保持原来的不适用行为，
停止结果仍不要求证据，已提供证据原样交给既有结果验证器。此次迁移的是适用性
判断，未改写证据内容校验，也尚未迁移全部执行路由和结算消费者。
schema／material candidate 每次读取各新增一次校验边界调用；这是语义收敛，
尚不能据此宣称性能改善。
使用同一真实 SQLite CLI host 请求（JSON 45,405 字节、合成 provider）进行
31 组交替热边界测量，schema 构造加 material 结果转换的中位耗时从
`68223e337` 的 0.013 ms 增至候选的 19.6 ms（p95 为 0.019／22.8 ms），
合法输出一致。保留这一局部新增成本的负向证据；测量不包括冷启动、模型工作或
完整 Turn，总体成果／成本资格仍开放。检查覆盖真实 File／SQLite 捕获恢复与
Codex／共享 host 消费者，不能证明打包 App 已采用或模型成果改善。

执行入口现在在 dispatch 或 journal 恢复前校验一次完整契约身份。结算计划缺失时
用该身份补建，恢复检查在同一次调用内复用它，不再读取 adapter 的 Agent。
已有结算计划与回执不重建；缺省完整契约的历史记录保留原解码与字符串处理。
该值不会跨 host 尝试、来源替换或后续调用缓存。此举同时关闭一个恢复绕过点：
此前已有结算计划会跳过来源校验，null／被修改的完整请求仍可返回已有 journal
结果。真实 File／SQLite CLI journal 的新拒绝保留 journal 字节与扣额数，
不调用 host 或结算 callback。十个定向用例在 `ce996ea0f` 的新增拒绝断言处失败；
候选 32 项捕获／恢复检查和扩大后的 605 项回归通过（相互重叠）。入口新增一次
校验边界调用，由两个消费者共用。同一份 45,405 字节真实 SQLite CLI 请求，
31 组交替热测已有结算计划读取与恢复身份，合法输出一致：中位数从 0.016 增至
12.382 ms，p95 从 0.024 增至 14.883 ms。这是新增局部校验成本，不是效率收益；
不含冷启动、完整 Turn 与模型成本。完整路径性能及其余路由／结算退役仍开放。

Codex CLI 构建提示词时，有效完整请求现在直接调用既有完整校验 owner，删除重复的
单独 envelope 版本调用。历史请求保留版本检查；完整校验失败时仍检查版本，以保留
两处同时损坏时的诊断优先级。未引入校验缓存、新 wire 字段或第二 owner，提示词正文
不变。这是调用边界合并，不代表剩余 driver 路由或结算 adapter 已退役。

对照 `e1dcd95dd`，完整与历史提示词字节一致，六类无效输入保留相同拒绝诊断。
同一份 45,429 字节真实 SQLite CLI 请求生成 34,043 字节提示词；61 组交替热测中，
提示词构建中位数从 24.229 降至 14.819 ms，p95 从 30.189 降至 17.854 ms；
桥接追踪确认两次调用变为一次。局部降幅约 39%，不含冷启动、模型执行和完整 Turn
成本，不抵销单独测得的 profile／结算开销，也不证明相对原 quota 的成果收益。
诊断补充前的较早样本为中位 21.279／12.994 ms、p95 29.729／17.321 ms；保留其
早期证据身份，不作为第二份独立的最终资格。

规划现在在构建路由、会话和事务之前接收完整契约，不再在计划完成后附加它。
规划与失败会话恢复通过一个粗粒度身份操作复用既有 TypeScript 完整来源校验；
同一次边界调用选择 Goal/Agent/工作身份并返回动作签名前像，替代原先仅取签名的
调用，不增加逐字段 RPC 或缓存。显式空值、不支持版本、被修改的完整契约均拒绝，
已处于 resume 的请求也一样；缺省完整契约的历史输入保留原 decoder。
Todo-less replan 身份仍由回执 owner 提供；下文路由与启动增量记录后续消费者迁移。

Python JSON/hash 传输编码特意保持不变。直接采用 JavaScript 哈希的试验未通过
小数／指数前像一致性检查，会改变已持久化的 Turn key；File/SQLite 特征化现在
覆盖这些数值、Unicode、自适应工作选择和重复恢复。一个真实 SQLite CLI 请求
（45,380 JSON bytes）相对 `088867421` 的 31 组交替热态采样，完整／历史计划及
失败恢复输出保持一致。规划 median/p95 从 39.632/47.964 变为 42.608/53.270 ms；
恢复从 12.330/20.858 变为 18.136/23.242 ms。这是局部增加的校验成本，不是完整
Turn 或原 quota 成果对照，也不代表模型、已安装 App 或 PostgreSQL authority
资格。剩余路由／材料 adapter 退役及端到端成本资格继续使 C1 保持开放。

Host 启动前实时复查现在将回执绑定的实时观察交给初始规划所用的同一完整契约
composition owner，再把完整契约传入共享 planner，替换原先 raw quota 直接进入历史
规划的捷径。Stop 和动作变化仍先于 composition 拒绝；实时观察不替换原始 capture
中的模型输入，也不生成另一份回执。剩余 effect／材料 adapter 仍需退役。

路由分类现在归既有 TypeScript envelope owner 所有。显式注册 action／frontier 表在
编译期检查穷尽性，历史未知 action 仍进入 Host；签名拒绝、attempt、先 replan 后
普通 delivery 门、能力 intent 身份、repair、用户行动和安静等待保留原顺序。
Python 仅保留 controller 薄传输与历史 scalar-text 编码，不再拥有第二份分类表。
完整契约校验忽略历史解码提示；已持久化动作 hash 的 Python 编码及历史工作选择
reader 均保持不变。

规划在既有粗粒度身份／签名调用中同时取得 route；完整与历史普通计划仍各需两次
桥接调用。相对旧 Python 分类器，1,152 组 action／gate 组合一致；固定真实 SQLite
CLI fixture 的完整与历史计划输出也一致。计划对照共用当前 TypeScript endpoint，
因此不是精确旧版／新版端到端延迟实验。controller 单独读取 route 现在新增一次
桥接：44,891 字节请求的 61 组交替热测，median/p95 从 0.004792/0.010125 升至
13.252792/15.855042 ms。这是该 caller 的实测回归，不是效率收益；作为 controller
调用边界合并的明确成本缺口保留，不引入缓存或重复分类器掩盖它。File/SQLite
入口、恢复、一次结算资格，剩余 adapter 退役、打包采用和原 quota 成果／完整成本
资格仍分别验收。

生产 `turn managed-step` caller 现在将 envelope 构造与同一路由求值合并在一次
TypeScript 请求中。Python adapter 返回绑定 route 的本地不可变 envelope 快照；它
不是新的 Agent wire 版式，也不拥有第二份决策规则。读取或修改返回的字典不会
改变快照，也不能把它的资格附到另一份 wire 字典。controller 保留既有用户行动
优先级、回执／前驱校验和 journal 所有的重试预算；普通历史或外部 envelope 仍走
原 route reader 与签名拒绝。构造 adapter 和普通 envelope builder 共用宿主绑定及
capture 参数准备，删除 managed caller 原本多余的中间 builder。上段独立 reader
回归保留为历史实测；只有生产中新生成与消费的调用对能够省去这次额外请求。
剩余工作选择／effect／材料解码、大型冷上下文恢复、打包与成果资格继续开放。
caller 直接消费统一 typed controller 结果后，本地快照随之退役；它不新增持久化
schema 或兼容 wire。

固定 31,088 字节合成来源的 61 组交替热测，将 `ddf96b79c` 的原 Python producer／
controller 函数与合并调用比较；共用当前 TypeScript 服务和未变的 build／route 规则。
最终源码的组件 median/p95 为 22.477/25.417 对 13.739/17.479 ms。另以真实隔离 File/SQLite
store 经公开 `managed-step` CLI，各 provider／arm 取 21 组交替热测：File median
为 135.989 对 137.316 ms，SQLite 为 149.310 对 146.081 ms；p95 分别为
209.730 对 192.544 ms、222.299 对 240.848 ms。另行传输计数探测发现每个 provider
的桥接总请求数从 70 降为 69：省去一次 route 请求，其余 live-decision 调用仍在。
完整输出一致，失败 journal 逐字节不变，两臂均不扣额。请求数改善，但整条 CLI 的
median 和尾部结果混合，尚不能证明加速；本地小
样本及共享机器资源限制尾部推断。这是具名源码边界的观察，不是整个不可变旧树
对照、冷启动、已安装 App、模型成果或原 quota 完整成本资格。

完整 reader 现在还将 adapter 的已签名动作文档，与原完整来源经过既有 producer
投影的结果比较。原先来源和 adapter 各自的 hash 只能证明各自完整：重新计算 hash
后的 adapter 可以保留 Goal/Agent/Todo，却改变执行或写回条件。共享校验复用既有
签名维度，不新增第二份字段清单、RPC 或 wire 视图。仅含历史输入时保留旧 reader。
完整请求的 profile adapter 与来源不一致时，现在明确拒绝，替代此前忽略冲突、直接
采用来源 profile 的行为；这是有意的拒绝语义变化，历史 profile 行为另行保留。

相对 `b9e2c5120`，File/SQLite 的规划拒绝断言在修复前失败。六类独立重算 hash 的
反例覆盖运行资格、有效动作、交付许可、用户介入、执行策略和扣额许可；候选在
host／写回／扣额回调前拒绝，不产生 Turn journal 或结算回执。既有 lane 锁仍可能
先分配。一个 44,891-byte 真实 SQLite CLI fixture 的 101 组交替热态进程内
TypeScript 采样保持合法身份输出一致；校验 median 从 4.864 增至 5.163 ms，p95 从
5.570 增至 6.010 ms。这是局部新增成本，不含桥接、实时 composition、冷启动、模型
或完整 Turn 成本，不证明相对原 quota 的成果收益或 C1 切换资格。

### C1 消费链替换与切换单元

切换单元是一个受支持入口**及其下游消费者**，不是一个输出开关。对
`99921829f2c5` 的源码核对确认以下活跃接缝。这补充前述基线审计；它是迁移设计，
不表示这些路径已经改变。

| 活跃接缝 | 具体替换与保留的 owner |
| --- | --- |
| `quota.py::_emit_quota_result` / `_project_quota_cli_payload` | 在 capture 前组合完整契约，替换 ordinary/envelope 执行格式分支。准入、capability hook、capture IO 与 host poll 回执保留原 owner。诊断 status 视图不是可执行契约。 |
| `turn.py` 的 `build_fresh_turn_decision_owner().resolve()` 之后 | 携带实际 scheduler execution context 调用同一组合 owner。此入口直接调用 `build_turn_envelope`；仅修改 quota CLI emission 不会迁移 Turn。保留 Turn plan、journal 和回执执行器。 |
| `heartbeat/task_body.py::render_thin_heartbeat_task_body` | 用统一契约读取指令替换 `captured_envelope` 指令分支，保留精确 selection/reentry transport、通知独立性、必读与原结算身份。保存的 bootstrap 仍在唤醒时解析当前指令。 |
| `turn_driver/codex_cli.py::_prompt` 与 `host_candidate.py::extract_turn_authority` / `render_prompt` | 在实际 prompt 边界替换格式特定输入。当前普通 Codex 接收 envelope，分阶段 replan 使用提取的 authority，结构化 host 也使用提取的 authority。共享源字节或签名不能证明这些最终输入保留相同义务。保留 provider 结果编码与外层 effect 归属。 |
| `turn_driver/decision_capture.py` 与 `cli_commands/quota_capture.py` | 复用不可变 capture、校验读取器和 full/delta 交付。首次交付和保存后恢复必须重建同一契约；不能仅因 Turn 身份相同，就把某投影的 continuity baseline 用于另一投影。 |
| `benchmark/runtime/worker.py`、`benchmark/runtime/sessions.py` 与 `scripts/external_scheduler_worker.py` | 迁移它们实际调用的产品入口。保留评估配置与外层唤醒归属，不新增 benchmark 专用转换，也不把 managed-host 测试算作 attached-host 验收。 |

替换任一接缝前，实现 PR 记录源路径、字段或指令条款、当前 reader、目标 owner、表示、
恢复与退役条件。字段搬迁须包含引用解析；删除诊断须证明其中不含动作、权限、必读或
验收义务。未知数据不得静默消失；未知的必需执行语义也不能靠转发 JSON 就变安全，
相关操作须保持不可用，直到兼容消费者能够理解。不能用子串匹配分类义务。

使用**一次明确的版本迁移**，不在每次唤醒自动协商格式。已迁移 binding 的新准入要求
组合、交付和消费 reader 兼容。不支持的消费者在 dispatch 前收到可操作的不可用结果，
不能因此创建第二个活跃 writer。已准入工作继续使用记录的版本和原回执；在这些工作及
受支持恢复窗口核清前保留历史 decoder。回退同样区分未结工作与新准入。版本迁移不能
把保存的 prompt、journal 或源 hash 改写成仿佛一直使用新契约。

代码收敛属于每个迁移切片，不只留给最终 C9 清理。两个入口须共用 composition、引用
校验、上下文重建与结算的语义实现。真实的宿主 launch/observation 差异留在薄 adapter；
不为强行统一不同生命周期而另造通用 runner。各项决策仍由既有 typed owner 负责。

每个切片记录迁移前后调用点、共享 owner、已删除的重复算法，以及剩余兼容 caller 和
退出条件。两条路径共用独立编写的语义用例，并分别验证真实传输与恢复。仅委托调用的
wrapper 可以过渡保留；给公共模块起新名字却继续保留两套规则实现，不算收敛。下一次
修改共同规则，应只改、只测一个 owner 并传递到两条路径。代码行数下降不是验收指标，
也不能为此删除宿主独有的验证覆盖。

C1 首个 PR 必须覆盖上述两个生产组合调用点，并交付至少一条从生产端到最终输入的
完整旅程，同时明确剩余 reader。这个有界切片通过资格，不代表可向未验收 host 开启
新 writer，也不关闭 C1。最终切换须在全部受支持 reader 验收后删除活跃格式选择。
临时 decoder 有具名 caller 与退出条件，不是面向用户的兼容视图。

对 normal、selection、replan、wait、recovery 和 capability-off 的每个 fixture，
在**三个边界**核对独立编写的义务：组合契约、重建交付、最终模型输入及必需材料读取。
包含旧 action signature 未覆盖的源变化、必需来源缺失、不兼容消费者和结算响应丢失。
仅重建相等会漏掉 prompt 层丢义务；仅 prompt 完整会漏掉过期权限或重复扣额。
这些是集成要求，不授权运行模型或启动实验。

1. 盘点实际 producer、reader、writer、已保存 prompt、capture 和外部集成。把每个旧
   字段与正文义务映射到保留的 owner/字段、权威派生或明确批准的删除。“事实相同”的
   总括声明不够；保留原失败 fixture。
2. 通过真实 quota、thin-heartbeat、Turn 入口验收统一契约的单会话执行。每个 binding
   只切一个 writer；旧 reader 是绑定版本的显式迁移 adapter，列出真实调用方与删除
   条件，不能成为长期产品视图。
3. 加入按角色区分的 session、共享生命周期复用及 opt-in 双会话 profile，复用既有
   same-session/native-resume 和 effect recovery 代码。
4. Benchmark worker 与打包 App heartbeat 采用同一产品 profile。各 host 分别证明
   观察、停止和恢复；CLI 测试或模型模拟不能证明已安装 App 合格。
5. 最后一个受支持活跃 reader 迁移后，删除旧 producer、格式选择开关、重复 renderer
   与双格式测试。只为受支持历史回执 replay 保留版本 decoder。终态验收要求只发出一份
   契约，不再选择 quota 或 Turn packet 格式。

回退先关闭受影响 profile 的新准入，drain/中断 attempt，核对回执，再在下次准入工作
恢复先前合格 profile。不能静默替换 native session 或重解释 pending 回执。删除字段/
decoder 前证明 export、旧版本 readback 与降级行为；满足受支持保留/升级边界前保留旧
reader。本文不单独授权破坏性迁移。

## 9. 验证与验收

| 主张 | 证据与必需结果 | 边界 |
| --- | --- | --- |
| 一份完整契约 | 字段/条款盘点；同次捕获的正常、replan、wait、recovery、能力关闭负载经过全部活跃入口；重建语义实例相同 | 单 serializer 单测不足；畸形/不支持版本显式失败 |
| 无损上下文交付 | Full/delta/recovery 等价；缺页、迟到、截断、role/session 不符与真实原生压缩/续接 | 合计所有读取/恢复成本，不重新准入/扣额 |
| 角色隔离与恢复 | 实际 host tools 下的 controller/executor 崩溃窗口、重复 dispatch、迟到结果、revision 变化、lease 到期、Stop | Executor 不能访问 Core，不能采用过时结果或隐藏第二个业务 executor |
| 语义采用 | 原始要求 → controller 判断 → executor 实际工作 → 独立验证 → 接受结果并回原受众 | 读回执、合法 JSON 或 replan 成功不能单独通过 |
| 智能中介 | 普通继续、共同约束变化、controller 丢失和小任务反例中的连贯交接、变化／反向证据及结果解释 | 说明真实判断后果与保留义务；少查询或短交接均不足 |
| 共享生产路径 | 真实 CLI 与受支持 File/SQLite；涉及 authority 持久化变更时跑真实隔离 PostgreSQL；打包 App heartbeat 与 benchmark worker 复用 | 不破坏 live Goal；未支持/未运行 host 仍未合格 |
| 成果与成本收益 | 当前单会话、统一契约单会话、统一契约双会话三组，固定 task/model/policy/budget/evaluator | 报告不确定性、失败与反例，不从 prompt bytes 单独宣称收益 |

C1 的主要对照是**同一单会话产品路径中的原 quota packet 与融合后新契约**。
匹配 host、任务、原始事实、模型/policy、evaluator 和可用预算，覆盖冷启动全量、普通
暖续接、replan、缺失基线与中断恢复，并包含长/重复历史和 capability-off。旧 envelope
可以作为诊断参考组，不成为永久第二目标。新契约 full 与 delta 编码分别比较，避免
传输复用掩盖语义退步。Controller/executor 分离是之后独立的对照，不能用来解释或
抵消 C1 回归。

### C1 基线边界与复用盘点

基线是**原路径中调用者实际收到的 quota 交付**，不是内部原始 decision。
`cli_commands/quota.py` 的原 emitter 先捕获 source；有 capture 时按条件应用
`replan_cli_view`，再应用 `compact_quota_should_run_cli_payload`，随后原生上下文
交付还有 full/delta 传输。两组须记录精确版本、host flags、capture 是否可用及 detail
选项。用更大的内部 source 对比紧凑候选会夸大节省；只用延后读取的首包对比完整候选，
则会漏算基线的后续成本。

现有路径已包含值得复用的效率行为。C1 应把已合格的语义吸收到共享 TypeScript
composition/context owner，再退役旧的 caller-specific producer。不能直接套用整个旧
compactor：精确共享、省略和延后读取的信息与失败后果各不相同。

| 现有信息／变换 | 融合处理与决定性检查 |
| --- | --- |
| 已选工作、原始要求、验收、证据、权限及停止条件 | 依赖它们的决策前可用。保留不同 revision 和矛盾证据；lane 标签或 action signature 均不能证明完整性。 |
| 相同验收副本、嵌套 vision audit、authoring 和可选指导 | 在更小时优先使用契约内一份完整 owner 加精确引用。比较 JSON 类型及每条义务；不同或缺失 owner 时分别保留。不增加补读或决策权。已有 projection 测试只提供特征验证，不代表新消费者已合格。 |
| 有限 Todo lanes、缩短的建议、省略的边界／历史项 | 视为主动省略，而非无损去重。保留完整性／计数事实和同 capture 恢复；证明被省略项不影响当前决策，或先加载再决策。旧显示上限不能自动变成语义上限。 |
| 捕获后的 replan review、验收详情和 writeback reader | 区分决策事实与后续 authoring 格式，同时保留原身份和全部必要含义。读取绑定同一 capture；计入每个必需阶段、错误和恢复。引用缺失／过期不能重新 guard 或授权工作。 |
| Full/delta/resume 交付 | 在 composition 后复用既有 continuity owner。行动前验证精确语义重建与必要材料可用；计入冷回退及丢失基线的恢复。 |
| Scheduler、准入与结算身份 | Capture 前只有一份权威实例；传输保留真实 host owner、原 Turn 与一次性 effects。重复表示不构成削弱检查的理由。 |

候选 `895f38fc5` 的固定 source 合成 replan 审计说明了边界的影响。关闭 stride 且不做
capture 分阶段时，原始 source 为 21,461 个紧凑 UTF-8 JSON 字节，原 quota CLI
projection 为 20,543，完整候选为 21,738：**相对 CLI 增加 5.82%**，相对原始 source
则是 1.29%。启用 capture 分阶段后，entry/review/writeback 合计 21,882 字节，候选
为 21,821。启用 `evidence_conditioned_v1` 时，分阶段合计 28,558，候选为 33,368。
这里使用既有合成 replan fixture、生产 projection/read 函数与合成 capture 路由；
不是实际模型轨迹或安装宿主实验。追加的验收／Explore 读取、实际请求包装、缓存行为
及成果差异均未测。切换 legacy receipt 得到相同数据，不增加独立 workload。这些数字
不支持净收益结论。

#7430 的验收共享增量把原 Python 展示算法移入既有 TypeScript composition owner，
在 hash/capture 前执行；旧展示 helper 只委托同一实现。完整契约 reader 与历史分阶段
读取共用一个引用解析器。相同契约保留一份完整 owner；不同 revision、JSON 类型、来源
和负面证据分别保留。已有引用随 owner 的迁移同步调整；即使完整性 hash 正确，悬空、
循环或不可用引用也会拒绝。引用比原对象更大时仍保留短对象原文。

隔离 File/SQLite 的 quota 和 native host fixture 覆盖真实合成 Codex 子进程输入中的
完整验收、失败后恢复和原身份一次性结算，也验证共享后的 full/delta/丢失基线重建。
这些证明传输语义，不证明模型遵从。旧展示 adapter 增加一次传输调用；完整契约在原有
composition 调用内部完成共享。未声称延迟改善。

固定 source 的四份相同验收 fixture 中，融合契约为 23,432 个紧凑 UTF-8 JSON 字节，
原未分阶段 CLI 为 22,237（+5.37%）。启用 capture 分阶段时，融合契约为 23,515，
原 entry/review/writeback 合计为 24,509，未计追加材料读取。对照冻结 `895f38fc5`
的原展示共享 helper；上述不含验收副本的 workload 数字不变。这把 quota 已有的效率
吸收到共同路径，不能证明优于 quota 或完整成本获益。下述 guidance 增量进一步缩小缺口；下一步先在既有 owner 验证剩余
vision 共享；标题由下述增量处理，然后再评估延后详情。冷启动与分阶段总量分别保留，
保留负结果，不能靠删除独有指令达到字节目标。

#7430 后续增量通过同一个 TypeScript composition 边界共享 replan 的 vision-authoring
指令，保留的 owner 是既有 durable-writeback 结算步骤。旧 Python 展示 helper 委托
共享判断，仅添加历史展示元数据。完整 reader 和历史未分阶段／分阶段 adapter 解析同一个
精确引用，不另建 authoring 策略。JSON 类型、版本或指令不同则保留原文，引用必须更小。
即使 hash 匹配，冲突的原文／引用、错误步骤类型、缺失目标和引用链也会拒绝。
结算计划、全部原指令和示例保留在 capture 中；历史 writeback 阶段维持原有的示例省略。

两个旧 CLI 参数写法现在暴露同一份共享完整契约；材料回读、产生效果前拒绝和 native
输入分别验证。已验证的 Native 外层 controller 输入没有内联结算 authoring owner，authoring 仍保留原文；
共享不会虚构该 owner 或转移结算权。authoring 检查点的四项旧 projection 测试期待
完整候选尚未采用的多行标题或 Explore 指导共享，在此前及 authoring head 都失败。
下述 guidance 增量解决其中两项，标题增量解决另外两项。历史失败继续保留，不豁免或改写。
这些切片不证明已安装 App 的采用。

固定的合成 authoring fixture 中，原未分阶段 CLI 为 22,062 个紧凑 UTF-8 JSON 字节，
融合契约为 23,266（+5.46%）。启用 capture 分阶段时，原 entry/review/writeback 合计
24,311，融合为 23,349。原 CLI projection 模块冻结在 `895f38fc5`，输入及投影读取阶段
保持一致。追加读取、请求包装、tokens/cache、延迟和模型成果尚未测量。共享消除了
候选中的重复实现和重复指令，但不能证明优于 quota 已有共享或完整成本 diff 正向。


Explore guidance 增量删除 Python 的精确匹配循环，完整契约与旧展示调用者复用同一个
TypeScript composition 边界。仅当指令完全一致、已有 durable-writeback Explore
附件 owner 且引用更小时，才共享同一 capture 的 owner。独有／短文本及缺失 owner
保留原文。非法、冲突或成链引用拒绝；不缩短指令，也不给可选附件新增义务。
历史分阶段及未分阶段 reader 在移动上下文前解析引用，再复用 Explore 既有领域投影。
独立 Explore 读取仍返回完整指导。兼容 helper 随旧展示调用者退役，不拥有独立决策或共享算法。

真实隔离 File／SQLite CLI capture 现在通过此前失败的两项 Explore 共享用例，
包括独立材料对照、历史回读及非法 capture 在状态／额度不变时拒绝。
既有分阶段 CLI 测试仍执行 registry 不可用时读取、篡改拒绝及实际写回／结算。
差分重建保留变化后的指令并支持 baseline 丢失恢复；不完整上下文回退完整交付。
这些证明传输及本地集成，不证明已安装 App 或模型成果。


扩大调用者验证也将旧测试假设迁移到准入后的完整契约：native 子进程 capture／重试
比较整份原始契约，生成的结算命令仍仅结算原 Turn。历史 capability reader 维持
显式初始化隔离 canonical fixture、拒绝事实及 scheduler 信息，不因此授予执行权。
capability 准入被拒时仍交付 typed diagnostic／历史投影，准入成功才交付完整契约。
这个失败路径迁移仍属于 C1 切换前缺口，不是永久第二套契约。
合成 native／stdio provider 不证明模型收益。

同样冻结 `895f38fc5` 原投影，合成 guidance fixture 的原未分阶段路径为 22,848 个
紧凑 UTF-8 JSON 字节，融合为 24,320（+6.44%）。启用 capture 后，原
entry/review/writeback 合计 26,081，融合为 24,403。不含 guidance 的 fixture
未分阶段仍为 20,543 对 21,738 字节。必需追加读取、请求包装、tokens/cache、延迟及
模型验收成果尚未测量。代码共享及恢复 quota 已有的信息效率不能证明完整成本 diff 正向；
C1 继续保持未完成。

当前 Task 标题增量删除 Python 的精确来源匹配算法，完整 composition 和旧展示委托
共用 TypeScript owner。只有与唯一、已核验、完整的当前选中 Task 的 `text` 或 `title`
精确重复时，才转换为同一 capture 的 `title_ref`；完整原始声明及来源继续内联。
来源缺失、不同、不完整、歧义、失败、已完成或已归档均不符合共享条件。历史字符阈值
（包括 Unicode 码点计数）保持不变。旧展示按 label 前缀省略标题的逻辑留在展示层，
不应用于完整契约；委托 helper 随旧展示调用者退役。

完整 reader 再次验证身份及引用归属，即使 hash 有效，也拒绝冲突原文、引用链、
非法路径及不可用目标。历史 envelope 保留完整 Task 来源，不携带移动后悬空的 lane
引用。真实 File／SQLite CLI 在差分关闭时已通过此前两项多行标题用例；capture 变体
还验证精确回读及状态／额度变化前拒绝。差分及 baseline 丢失／全量恢复保留变化后的要求；
独立 Todo detail 仍返回完整声明。这证明本地传输语义，不证明已安装 App 或模型行为。

固定合成当前 Task fixture 中，冻结 `895f38fc5` 的原未分阶段 CLI 为 28,585 个紧凑
UTF-8 JSON 字节，融合契约为 29,780（+4.18%）。启用 capture 后，原
entry/review/writeback 合计 29,906，融合为 29,863。不含当前 Task 的控制样例
未分阶段仍为 20,543 对 21,738 字节。这些总量未含请求包装、追加材料读取、tokens/cache、
延迟及验收成果；它们将 quota 已有共享恢复到共同路径，不能证明完整成本收益正向。

vision audit 增量现已将 Python 嵌套精确匹配算法迁入同一 TypeScript owner。完整契约
和历史展示只在引用更小时共享完全相同的 audit，包括未知事实、负面证据及有序义务。
不同 JSON 类型不相等：旧 Python 的 `True == 1` 比较可能抹掉 channel 差异，现保留
该差异原文；短 audit 也保持内联。根保留完整 audit 和 judge，旧展示 metadata 留在 adapter。

完整恢复即使面对重新计算的有效 hash，也拒绝缺失、非法、链式或冲突的 audit／judge
引用。历史 envelope 仍从内联根取得 judge，不产生移动后的悬空引用；其已有投影限制
不变。真实隔离 File／SQLite CLI 在含活动历史 vision、差分关闭时，验证完整 judge、
精确 capture 回读及状态／额度变化前拒绝。差分／全量及 baseline 丢失恢复保留变化的
要求。没有活动 vision 的 fixture 正确地不产生 audit，不能用来验收这条路径。

冻结原 CLI projection 于 `895f38fc5`，固定合成 audit 样例的未分阶段紧凑 UTF-8 JSON
为原 22,337 字节对融合 23,333（+4.46%）；capture 原 entry/review/writeback 合计
23,658 对融合 23,416。不含 audit 的控制样例仍为 20,543 对 21,738。它们不计
tokens/cache、延迟、追加读取、请求包装及验收成果。恢复共享、减少规则 owner 仍不
证明完整成本正向。旧 audit 委托随展示调用者退役；剩余 consumer 退役及打包／成果
资格继续使 C1 保持开放。

拒绝准入现在只要已有 interaction contract，就在不可变 capture 前使用同一完整
composer，包括 health 为 false 的暂停来源。`ok=false` 仍阻止 host handoff 和原生
context baseline 发布，保留失败退出码；组合不授予交付或结算权限。非法完整来源在
capture 前验证失败。工作契约形成前的错误则在两种 CLI flag 写法下保留原 typed
诊断及恢复事实，不补造工作契约，也不再经过历史 envelope renderer。

真实隔离 File／SQLite CLI 覆盖 App heartbeat 和 generic 两种 profile 下的工作
缺失、能力不可用及 selection-required reentry。重入保留原 receipt，不产生额度
记账、baseline 或越权状态修改。Capture consumer 正迁向完整原件精确回读，同时
显式保留历史长路径恢复覆盖。这验收本地传输和拒绝语义，不代表已安装 App 采用或
全部 provider／入口交付。剩余 consumer 退役及打包后的单会话成果／完整成本资格
继续使 C1 保持开放。

后续观察验证区分了两个问题。#7464 修复测试对两次独立实时观察时钟的误比，同时
保留其他全部事实。#7468 让 `quota status/plan` 保留既有权威 owner 的 typed 拒绝
及备份／迁移指引，修复提前返回错误时丢失这些信息的问题。按照已接受的 #7032
截止规则，未迁移 Goal 的普通读取继续拒绝；File／SQLite 保留范围隔离断言。
新的恢复断言在旧实现上失败，修复后的范围／诊断／权威集合 156 项通过。这些可独立
评审的修复已由集成候选吸收，不代表安装采用或成果／成本改善。早期失败运行仍作为
历史证据保留；这几项已归因失败不再列为未完成调查。

固定 unhealthy-paused 合成来源在原 CLI projection `895f38fc5` 下为 6,325 个
紧凑 UTF-8 JSON 字节，完整契约为 6,639（+4.96%）。新增 binding 保留统一验证，
这不是效率收益；该样例的 tokens/cache、延迟、追加读取、请求包装及验收成果仍未
测量。这些检查不代表启动过真实模型实验。

共享 owner 前置通过 #7457 独立评审。Task 标题与 Explore 操作在旧投影中合用
一次粗粒度调用；完整 composition 在既有调用内部复用相同函数。集成分支吸收
前置提交，不复制一份 owner。新的 `should-run` emission 只接受已组合契约或
原始 predecision 诊断，不再回退旧 packet／envelope 投影；历史 capture 恢复
仍为显式路径。组件测量显示桥接成本比两次调用更低，但高于原 Python 投影，
不能作为完整 quota 路径净收益的证据。

同时报告字段/条款 diff 及其决策后果、实际模型输入、cached/uncached tokens、追加
读取/往返、重建、延迟、失败/重试、验证与人工介入。首包更小但总补读更多，可能是负向。
只有必要语义保留且同等验收成果的完整成本降低，或可比预算下验收成果改善，才判正向。
效果混合则报告取舍；只有传输通过或成果未测时结论仍不确定。按既有测量合同在可比实验
前确定数值标准，不凭空规定压缩比例。C1 切换须完成这个 quota 基线对照，不能仅凭
serializer 或字节减少结清。

使用[既有测量模型](long-horizon-harness-benchmark-research-program-v0.md#6-measurement-model)。
计入两个会话的输入/输出/cached tokens、工具及材料读取、planning/replan/recovery、
墙钟时间、验证、人工注意力和 provider 成本；未知用量不是零。持久化因果归因，转发
产物时不重复计算成本。比较可比预算下的已接受成果，不能只看 replan 次数或 executor
速度。另行授权实验前冻结标准，本文不预设胜出方案。

在后续双会话对照中，除 executor 工作，还要归因 controller 推理及全部交接／材料、
验证与恢复成本。收支平衡问题是：在保留可比验收成果时，这些**新增完整成本**能否被
避免的控制／记账、重复执行或人工介入所抵消。即使两会话读同一产物，也按实际重复
模型消费计量；转发同一用量回执不能把该回执重复计费。报告不同 workload 的取舍和
账单／缓存覆盖未知。本文不规定通用开销阈值、压缩比例或每条消息 token 上限；此对照
不替代前置的原 quota 与统一单会话验收。

## 10. 运行契约

既有 Goal Chat、settings 与工作 readback 应呈现选定 profile、controller/executor
真实可用性、当前工作、有意义进展、不可用/错误、Stop 范围及返回产物。不要把内部 packet
选择变成用户任务。复用共享 typed 投影与配置优先级，不建独立 controller console 或
重复 provider/model 设置。

既有诊断记录 capture、request/attempt/role/generation、当前 revision、effect 回执
和成本覆盖；提供可操作恢复，不暴露私有原始对话。通知与工作延续分离；事件/时间等待及
backoff 继续归当前 scheduler owner。本文不引入模型空轮询或新的 recurring automation。

## 11. 规范性交付计划与重构任务

这些任务扩展整体路线图 S2/S4/S6/S7/S11 和 effect interpreter 既有融合工作，不另立
路线图。认领前对齐相关活跃任务/PR，复用已合格修复，不重复实现。下列 task ID 是稳定
设计引用，不代表 canonical Todo 已完成。

| 任务 / 依赖 | Owner 与实际替换内容 | 决定性退出 / 回退 |
| --- | --- | --- |
| **C1 — 统一契约；首先** | Interaction/work-context + quota/Turn owner：盘点字段与指令条款，捕获同一 schema，迁移真实普通 quota/Turn producer 与 thin renderer | 单会话真实入口保留必要信息、动作绑定和一次结算，并完成第 9 节相对 quota 基线的效果/完整成本对照；cutover 合格前保留绑定版本的 reader |
| **C2 — 一个上下文交付管线；C1** | Context owner + host observer：将 full/delta/detail/recovery 纳入 canonical 契约；默认自己的成果、全局约束及相关跨 Agent 材料，提供精确正文索引；删除 packet 格式分支和重复上下文副本 | 在计量完整成本下，冷/温态、上下文丢失及正常/replan/resume 保留必读全文、跨 Agent 采用和精确 role/session 连续性；不支持的范围回退 full 且不重新准入；编码可回退 full |
| **C3 — 会话绑定与原生生命周期；C1** | Session/runtime owner：扩展 role/generation，复用 native create/resume/observe/interrupt 和有序队列 | 两角色不覆盖 Agent native ID、不借用基线、不竞态执行；重试前观察既有 attempt；关闭新角色准入即可开始回退 |
| **C4 — 共享受治理生命周期；C1** | Effect/Turn owner + 外层 worker adapter：围绕既有 typed 操作与回执合并重复 prepare/dispatch/observe/validate/writeback/settle 编排 | App/Turn/external worker 在部分失败下对准入、原结果与一次扣额一致；scheduler effect 保留真实 host owner；核对回执后才切回旧调用方 |
| **C5 — Replan 与结果编写边界；C1** | Goal/Vision 与 interaction owner：提供当前事实和 typed controller 操作，移除双会话 profile 中普通 executor 编造 Core 写回字段的义务 | 先看事实保留 default/stride policy 区别、全部证据与必要验证；非法写回拒绝；rollout 期间单会话编写仍合格 |
| **C6 — Controller/executor 交流；C2–C5** | Manager/request + host owner：复用版本化语义请求与结果返回，用自然语言工作/材料交流替换 executor 的 schema 约束任务 | 完整来源交付、executor 局部自主、强制 Core 隔离、纠偏往返与独立验收结果；回退后新工作使用合格单会话 profile |
| **C7 — Benchmark 复用与完整成本归因；C4/C6** | Benchmark toolkit/execution owner：薄 adapter 选择相同产品 profile；合并重复 launch/session 配置，跟踪两个会话，不改冻结 trial | Native/plain 对照不变，可比组配置/replay 与完整成本覆盖；合并 helper 不启动 job |
| **C8 — 产品配置与运行 readback；C3/C6** | 既有 App/settings/Chat 投影：同一 effective configuration owner，按角色展示 activity、失败/恢复、Stop 和产物返回 | 打包 managed 及受支持 attached heartbeat 流程，包括 host 不可用和结果迟到；安装资格另验；不新增 packet 格式开关 |
| **C9 — 旧路径退役与采用资格；C1–C8** | Contract/runtime 维护者：删除被替代 producer、格式开关、重复提示/测试，更新 protocol、guide 和 RFC 交付事实 | 调用方/字段/decoder 退役清单、历史 replay 与回退证据；App + benchmark 验收及可比收益成立后再决定默认提升 |

C1 盘点是可用替换的一部分，不单独建设架构框架。C1 契约稳定后，C3/C4/C5 可以独立
推进；C7 可以提前准备测量，但不能提前宣称双会话成果。实现 PR 列明替换的调用方、剩余
缺口、实际测试与回退。完整语言迁移或无关 manager 重写不是前置条件；新抽象能编译
不能算任务完成。

C1 是一个验收目标，不要求放进一个 PR。独立有用的前置改动可以先落地，前提是现有
调用方实际采用共享 owner、入口契约经过验证，且能局部回退。例如，在现有 quota 和
Turn 入口保持各自 schema 时，将精确上下文引用组合与恢复收敛到既有 TypeScript
owner。列明后续切换消费者；不能添加无人使用的基础设施或第二套台账。
共享 owner 前置、producer/capture 切换、reader 恢复与退役、采用及完整成本验收，
可按独立可审查的边界拆分，在既有任务和 PR 中记录依赖。拆分交付不代表 C1 完成，
也不豁免第 9 节相对原 quota 基线的验证。

当前作者拆分把 [#7517](https://github.com/loopx-project/loopx/pull/7517) 作为独立
双语设计细化，把 [#7520](https://github.com/loopx-project/loopx/pull/7520) 作为可在
main 独立使用的 route owner／caller 重构。已有独立提案
[#7457](https://github.com/loopx-project/loopx/pull/7457) 与
[#7468](https://github.com/loopx-project/loopx/pull/7468) 分别保留上下文引用和观察诊断
归属；cache 时钟修复 [#7464](https://github.com/loopx-project/loopx/pull/7464) 已进入
上述目标 main。这些边界可以局部回退，设计改动没有 runtime 依赖。
[#7430](https://github.com/loopx-project/loopx/pull/7430) 仍是未完成的集成候选，为组合
测试保留前置内容；前置进入 main 后须再次 rebase，移除重叠 diff，再交付切换评审。
不能合入两套实现，也不能把这种分阶段安排说成 C1 整体完成。

首个**切换**片必须把完整字段/条款迁移与真实单会话 producer、consumer 替换一起交付。
从共享 quota emission 边界开始，同时追踪 attached heartbeat 交付与 Turn adapter，
保留各自真实宿主归属。不能新增无人使用的第三个 serializer，而两个活跃 writer 继续
各自拥有语义。既有 `test_quota_decision_capture.py` 与
`test_turn_host_decision_capture.py` 提供可扩展的 capture/恢复覆盖，不证明新 schema。
通过隔离 fixture 上的真实入口验收普通工作、选择、replan、等待、未结算 Turn 恢复、
详情不可用与 capability-off 行为。每例除重建数据外，还须验证完整义务和原结算身份。
独立 oracle 必须包含来源不同而投影相同的情况；比较同一投影的两份输出不能证明完整性。
Native-resume/context 工作保留原 owner；采用其已验收改动，只补新组合边界缺少的集成
覆盖。替换真正可用且其余受支持 reader 有明确切换/退役条件之前，C1 保持未完成。

### 职责收敛与贡献者交接

[有源码依据的重构交接清单](ledger/controller-executor-unified-contract-v0/2026-10-27-refactor-handoff.zh-CN.md)
把 C3/C4/C7/C8/C9 拆成具体精简候选。它属于本计划，不是另一份 roadmap，也不证明
实现已交付。固定版本核对区分重复决策、归属不当的共享代码、已有共享 owner，以及
需要语义比较才能成立的假设。

保持三个清晰的职责：

1. **Core 语义 owner：** 在既有 typed 领域内裁决合法工作、会话、effect 转移，验证
   原权限与回执。
2. **公共受管执行：** 按这些裁决驱动会话绑定、原生调用、观察与恢复。复用既有
   Turn/effect owner；不是另一套权限引擎或调度器。
3. **Host adapter：** 实现原生 SDK/CLI transport、事件转换、实际支持的 resume 和资源
   清理。保留真实宿主差异，不能用名义统一接口隐藏不支持的行为。

从整体 roadmap、当前用户成果、实际瓶颈、在途交付与 canonical 工作中选择任务。
工作包编号和表格顺序只是引用，不是全局优先级、某个 Agent 的固定议程或预留归属。
C1/C2 组合/capture/最终输入工作与这些精简同属一个候选前沿，不是永久分给不同 Agent
的竞争议程。Owner 指代码边界，不代表任务分配或执行授权；认领前重新核对目标 main、
开放 PR 与 canonical 工作。

| 既有任务 / 工作包 | 精简结果 | 启动条件 |
| --- | --- | --- |
| **C3.1 受管操作协议** | Codex、DSH 采用同一操作会话流程；删除重复 binding/profile 校验、handoff/start 编排，以及 DSH 对 Codex 模块内通用 handler 的依赖 | 此重复被选为当前瓶颈时：现有单会话行为、两个真实调用方，保留原生差异 |
| **C3.2 会话绑定调用方** | 复用共享持久化；以既有完整操作替换调用方重建 binding 和跨 Host 私有导入 | 若独立拆开只剩改名/facade，则与 C3.1 一起交付；role/generation 扩展等待 C1 稳定 |
| **C4.1 回执到继续决策** | 采用已合格 typed Turn 观察/继续边界；退役被替代的 Python 回执重建与决策 | 保留 journal 恢复与下一工作决策的区别；整合 C1 及已有恢复修复 |
| **C4.2 外层 worker 采用** | 消费已合格语义结果，移除自行重建的公共重试/继续策略 | C4.1；保留唤醒归属、原回执和不透明命令的不确定性 |
| **C4.3 结算 adapter 收敛** | 复用 settlement 原语；仅在义务一致时删除第二套步骤/条件解释器 | 候选：比较 Host Todo completion 与 Turn settlement，并对齐在途结算 PR |
| **C7.1 Worker/进程 adapter 精简** | 复用适用 launch/drain/cleanup helper，删除已证实重复的机制 | 候选：先核对归属、信号/截止时间及对照组语义 |
| **C8.1 运行 readback** | App/Chat/CLI 消费同一 typed 工作/会话事实，退役被替代的调用方分类 | 候选：role 事实可用且证实分类重复；保留 unknown/coverage 与受众边界 |
| **C9.1 租约/来源旧路径退役** | 调用方迁移后删除过时正常 writer 路由/wrapper；保留必要 release、fence-close 和原回执恢复 | 跨 RFC 候选：遵循 shared-authority/TS 验收，不作为 controller 启动前置 |
| **C9.2 契约测试合并** | 共用不变量案例，加薄原生 adapter 测试，替换重复完整流程 fixture | 随所属替换交付；保留独有集成与负例 |

这是九个调查/交付工作包，**不是承诺九个 PR**、固定剩余数量，也不是 C1 合入前必须
完成的工作。不能因 C3.1 排在前面就选择它，也不能为了可见删代码而放弃当前更有价值的
正确性、契约交付或 authority 退役成果。若选定 C3.1，C3.2 可以属于同一个可逆 PR。
只有真实语义依赖约束顺序。现有行为修复和已证实局部精简可以先于 C1 交付；依赖契约
的切换仍保留该依赖。不能仅凭文件相似就认定候选具备实施条件。

记录所选切片为何推进当前全局成果、考虑过哪些更高价值替代项或在途交付，以及实际
依赖。交付或新证据出现后重新评估；此盘点不穷尽有价值工作，也不要求 Agent 做完
一条工作线才能换方向。优先级建议不新增准入或批准 gate。

每个已认领工作包在既有 Todo/PR 内携带来源 owner/调用方、目标边界、具体删除的规则/
分支、真实路径 oracle、依赖与回退。审计不替其他 Agent 分配任务，也不建立平行任务库。
可用切片应完成调用方替换和删除；仅对具名外部接口或恢复契约保留 wrapper。记录跨语言
生产代码总变化、语义 owner、完整事务 runtime crossing，以及下一项代表性规则的
修改位置。Python LOC 减少或新增共享类不能单独证明精简；不以任意净减行数配额
替代正确性或必需验证。

复用 [TS 修改局部性收益](typescript-control-plane-migration-v0.zh-CN.md#28-以修改局部性衡量迁移收益)
及 [smoke 合并规则](../../development/good-smokes.md)。内部行为保持替换需要真实调用方/
后端证据，无需新设置页面。改变调用方操作/readback 时，仍需 C8 的打包前端失败/恢复
旅程及首屏证据。C1/默认采用的完整成本与收益资格不变；有界重构不代表完整 RFC 验收。

## 12. 待决事项

1. **精确 wire 名称与字段映射——contract owner，在 C1 cutover 前。** 优先演进一个
   现有注册 vocabulary/version，不另起协议族。第 8 节建议保留源根路径；完成
   producer/reader/字段盘点后确定版本与 host-binding 映射；
   字段删除需要维护者批准，保留原含义不因此新增批准要求。
2. **首个合格双会话 host——runtime owner，在 C6 dispatch 前。** 根据实际会话控制与
   隔离能力选择。Managed Codex 可以是实现载体，App heartbeat 是必要产品验收；两者
   不能互相替代，也不能据此证明 benchmark 使用。
3. **资源分配与默认——product/evaluation owner，在 C7–C8 后。** 先用相同模型的
   controller/executor 隔离架构影响；更便宜的 controller 模型与不同预算另行实验。
   完整成果/成本证据支持决定前，profile 保持 opt-in。

## Appendix A: Execution ledger / 执行记录

源码核对及后续交付证据位于
[`ledger/controller-executor-unified-contract-v0/`](ledger/controller-executor-unified-contract-v0/)。
目录即索引。规划/源码检查记录不证明 runtime 交付；当前优先级与认领仍在 roadmap 和
canonical 工作中维护。历史记录保持 append-only，当前规范性计划在本文修订，不能把
某日审计当作永久执行队列。
