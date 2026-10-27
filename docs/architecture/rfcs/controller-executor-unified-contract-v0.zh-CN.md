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
2. 在可选的双会话 profile 中，**controller 模型会话是唯一面向 Core 的模型客户端**。
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
剩余 cache 一致性调查、其他旧 reader 断言、分阶段指令等价和实际 optional/external
消费者、版本准入及打包宿主连续性仍未完成。缺少显式原生 runtime 的 operation SDK
检查属于未测，不能算资格。默认 writer 变更尚未交付；PR 因作者实现未完而保持
Draft，不是仅等待评审。尚无模型或成本收益证据。历史 effect 解码只是临时迁移，
不得成为第二份永久可执行视图。

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

下一实施切片应先在 composition、reader 和 recovery 中验证同契约精确共享，再用上述
可比负载评估延后详情的取舍。冷启动与分阶段总量分别保留，保留负结果，不能靠删除独有
指令达到字节目标。

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
| **C2 — 一个上下文交付管线；C1** | Context owner + host observer：将 full/delta/detail/recovery 纳入 canonical 契约，删除 packet 格式分支和重复上下文副本 | 正常/replan/resume 中同 capture 恢复及精确 role/session 连续性；full 回退不重新准入；编码可回退 full |
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

C1 首个实现片必须把完整字段/条款迁移与真实单会话 producer、consumer 替换一起交付。
从共享 quota emission 边界开始，同时追踪 attached heartbeat 交付与 Turn adapter，
保留各自真实宿主归属。不能新增无人使用的第三个 serializer，而两个活跃 writer 继续
各自拥有语义。既有 `test_quota_decision_capture.py` 与
`test_turn_host_decision_capture.py` 提供可扩展的 capture/恢复覆盖，不证明新 schema。
通过隔离 fixture 上的真实入口验收普通工作、选择、replan、等待、未结算 Turn 恢复、
详情不可用与 capability-off 行为。每例除重建数据外，还须验证完整义务和原结算身份。
独立 oracle 必须包含来源不同而投影相同的情况；比较同一投影的两份输出不能证明完整性。
Native-resume/context 工作保留原 owner；采用其已验收改动，只补新组合边界缺少的集成
覆盖。替换真正可用且其余受支持 reader 有明确切换/退役条件之前，C1 保持未完成。

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
