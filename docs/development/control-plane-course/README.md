# LoopX Control-Plane Developer Course / LoopX 控制面开发者课程

> **课程结论：** LoopX 要让长程任务在无人干预时能跑稳、有人干预时能跑好。它把模型
> 有限上下文之外仍需成立的目标、工作、权限、证据、时间和恢复状态外置，再把当前状态
> 编译成一轮可执行的 CLI packet。Issue-Fix、Single-Agent Auto ML、Auto Research 等领域
> 增加专属判断，但不创建第二套控制面。

这套课程由概念导读、第 1 讲 Harness effectful-program、第 2 讲架构导论和第 3 到第 11 讲专题组成，面向准备修改 LoopX kernel、CLI、
状态投影、调度或扩展能力的开发者。它先从模型上下文为什么不能承担长期状态讲起，再用
三个端到端 Showcase 推导 ownership，最后沿真实 transition 进入状态机、CLI、核心函数和测试。

贯穿全课的形象化理解是“面向长程 Agent 的可执行看板”：Todo 是带身份、权限和证据的
卡片，claim、gate、monitor、writeback 等 Kernel operator 决定卡片能否移动，Capability
Pack 增加领域泳道。看板只是 projection；canonical state 和 typed transition 才是控制合同。

贯穿全课的另一种判定是 effect interpreter：模型提出 effect request，harness 解释后返回
observation，再把 next effect 交回 host。每个状态机都是解释器内部的一张 interpretation
table。

## 课程先建立一个递进

普通 Agent 对话主要依赖当前 transcript。Codex 原生 Goal 再向外走一步：通过持久 goal object
保存 objective、status 和可选预算，让 host 能围绕同一目标继续运行并判断生命周期。LoopX
在此基础上外置更完整的项目状态，并把过程约束编译到每一轮 packet：

```text
普通对话：当前上下文中的推理与工具执行
  -> 原生 Goal：外置 objective 与 goal lifecycle
  -> LoopX State：外置 todo、authority、quota、evidence、cadence 与 recovery
  -> CLI packet：把当前状态投影成这一次 bounded Turn 的过程协议
```

这是让有限上下文每次只接收当前 objective、必要 evidence ref、
合法 frontier 和 writeback contract。完整推导见[概念导读](00-concept-primer.md)。

## 三个 Showcase 是课程主线

第一次阅读不需要先记住所有状态。先比较三条产品闭环：

| Showcase | 领域事实 | 领域 transition | 复用的 Kernel 能力 |
| --- | --- | --- | --- |
| PR Issue Fix | issue feasibility、repository context、checks、review、merge state | fix、monitor、review correction、user gate、terminal | todo、claim、authority、quota、monitor、successor、closeout |
| Single-Agent Auto ML | metric contract、matched baseline、hypothesis、external run、resource capacity、negative result | launch proposal、monitor、evaluate、promote/no-promote、retry、replan | todo、claim、quota、defer/resume、monitor、evidence、gate |
| Multi-Agent Auto Research | research contract、hypothesis、dev/holdout evidence、evidence graph | holdout、promotion、retirement、retry、quiet completion | per-agent frontier、claim、quota、evidence、handoff、gate |

三条链路共同说明四种运行责任：Agent 通过 host/runtime 完成一次有界执行；Provider
获取外部事实并返回 observation/readback；Capability Pack 归一化事实并提出有限
transition；State Kernel 拥有通用生命周期。Domain State 和 receipt 是跨角色传递的紧凑
工件，Extension 是 provider 的交付边界，都不新增 control-plane owner。

架构导论先完整走过这三个案例。后续每讲再放大其中
一个共同机制；读者可以始终回到“PR 下一步为什么是 monitor”“外部训练任务为什么不能
自动算作模型证据”“研究假设为什么需要 holdout successor”这类具体问题，而不是在抽象
名词之间跳转。

若要在一小时内向接入方讲清楚“长程、复杂任务如何不跑偏、不陷入局部循环”，可以直接使用
[长程任务如何收敛](topic-long-horizon-convergence.md)专题。它把方向锚点、Turn 合同、
material delta、monitor quiet、replan、self-repair、Explore 与独立 oracle 串成一条收敛闭环，
并用 PR Issue Fix 和 Auto Research 做成对回放。

Single-Agent Auto ML 还提供一条贯穿第 0、1、2、3、4、5、6、8、9 讲的进阶主线：
Explore Graph 把假设、实验、支持、反驳和负向结果保存成可追溯证据拓扑；Explore Harness
读取显式 Graph refs、当前 todo 和资源容量，给出 analysis-only 的候选组合；单个 Agent 仍必须经
普通 claim、quota、provider effect、monitor、validator 和 promotion gate 才能推进实验。

## 先边界，再逻辑，再代码

控制面文档容易写成模块清单：registry、quota、scheduler、todo、event 都画进图里，却没有
说明谁拥有事实、谁能写、一次变化何时提交。课程统一用一份技术讲解骨架：

| 顺序 | 必须回答的技术问题 | 课程中的表达 |
| --- | --- | --- |
| Requirement / non-goal | 这层必须解决什么，明确不解决什么？ | 章首结论、目标与非目标 |
| State ownership | 哪个对象拥有 mutable truth，谁只能读取？ | ownership table、authority map |
| Turn boundary | 本轮拿到哪份只读 snapshot，怎样绑定 lineage？ | envelope、context、decision table |
| Effect boundary | 谁执行外部动作，proposal 怎样变成 receipt？ | host contract、typed result、ACK |
| Write / replay | transition 在哪里提交，崩溃后从哪个 phase 恢复？ | sequence、journal、state machine |
| Code / acceptance | 哪个函数实现规则，什么反例证明它没有被绕过？ | 代码领读、smoke、常见错误 |

这套顺序是评审方法。架构层没有明确 owner，技术层通常会复制状态机；
逻辑层没有 receipt，测试往往只能证明函数跑过。

课程采用一条贯穿始终的架构边界：执行 runtime、memory provider 和 workspace storage
可以替换或协作，但 goal lifecycle、canonical state contract、验证与恢复闭环由 LoopX
组织；任何 adapter 都不能静默成为第二个长期事实源。

这条边界可以展开成一条贯穿全课的闭环：

```text
vision / goal boundary
  -> todo、gate、monitor、successor 组成当前 frontier
  -> quota 把 source facts 编译成 interaction contract
  -> runtime 执行一个 bounded Turn，host 执行必要的外部 effect
  -> evidence、effect receipt、vision checkpoint 写回 canonical state
  -> acceptance audit 决定 continue、wait、replan、repair 或 terminal
```

## 七个贯穿问题

每次读代码或评审 PR，都先回答七个问题：

1. **外置边界**：哪些内容必须跨有限上下文继续成立，哪些只是本轮临时推理？
2. **事实源**：这个决定依赖的事实由 registry、event、todo、vision 还是外部观察拥有？
3. **权限**：哪个 agent、user gate 或 host capability 有权改变它？
4. **归因**：状态属于整个 goal、某个 agent lane、某个 monitor target，还是某次 Turn？
5. **本轮协议**：哪个 projection/packet 把这些事实编译成当前允许的动作和 CLI 命令？
6. **回执**：外部动作是否形成了与原 proposal 绑定的 durable receipt？
7. **延续**：本轮之后是 runnable successor、明确等待、replan、repair，还是满足 terminal closure？

控制面的复杂性通常是这七个问题被压进一个布尔值，导致一层
事实误替另一层做决定。

每一讲都包含四类材料：

- 一条真实 CLI 或状态执行路径；
- 一组核心代码领读入口，说明调用顺序、关键分支和不变量；
- 一个公开 smoke、测试或实验，用来验证理解；
- 一组 review 问题，帮助开发者判断改动应落在哪个 bounded context。

## 课程地图

如果你第一次接触控制面，先读[概念导读](00-concept-primer.md)。它用一张总图和公开的 Auto PR
Issue Fix 案例，区分 goal、todo、capability、provider、Turn、scheduler、gate、evidence、
receipt、projection、replan 与 self-repair；随后再进入下面的代码课程。

| 讲次 | 主题 | 读完应能回答 |
| --- | --- | --- |
| 导读 | [先把 LoopX 放进一张图](00-concept-primer.md) | 有限上下文为何需要外置状态，原生 Goal 与 LoopX 如何递进，核心概念怎样组成一条生命周期？ |
| 心智模型 | [Goal、Control State、Kernel 与 Packet](goal-control-state-kernel-packet-mental-model.md) | Goal 是否等于全部状态，Kernel 怎样从当前局面生成本轮 Packet，并在 Turn 后验证和写回状态？ |
| 专题 | [长程任务如何收敛：不跑偏、不陷入局部循环](topic-long-horizon-convergence.md) | 如何用方向、权限、证据、Delta、活性与终局不变量，让复杂任务持续接力而不把忙碌误判为进展？ |
| [第 1 讲](01-agent-loop-effectful-program.md) | Harness 是 effectful program | Harness 为什么是 effect interpreter，三类 adapter 如何共享 typed settlement algebra 而不共享万能 executor？ |
| [第 2 讲](02-goal-control-plane-architecture.md) | 从三个 Showcase 理解 LoopX 架构 | Issue-Fix、Single-Agent Auto ML 与 Auto Research 如何按 Agent / Provider / Capability / Kernel 分工并复用同一控制面？ |
| [第 3 讲](03-first-real-loop.md) | 从 Showcase 到第一次真实 Loop | 用户只说一句目标后，guided start、todo、heartbeat、quota、refresh 和 spend 如何串起来？ |
| [第 4 讲](04-state-substrate.md) | 状态底座与可重放事实 | registry、event、active state、run history 和 projection 分别拥有什么事实？ |
| [第 5 讲](05-work-graph-and-peers.md) | Todo 工作图与 Peer 协作 | equal peer 如何 claim、显式委托 lifecycle authority、handoff 材料前沿，而不恢复 primary/side 层级？ |
| [第 6 讲](06-quota-decision-kernel.md) | Quota 决策内核与 Interaction Contract | `should-run` 如何压成 operator-facing mode，以及它与 `LoopXTurnRoute` / `LoopXTurnResultKind` 的关系？ |
| [第 7 讲](07-host-scheduler-and-heartbeat.md) | Host、Heartbeat 与 Stateful Backoff | LoopX 决策、heartbeat prompt、execution context、Codex App RRULE 和 ACK 各自负责什么？ |
| [第 8 讲](08-evidence-refresh-and-self-repair.md) | 证据、Refresh 与 Self-Repair | 什么算 material progress，何时必须 replan，连续无推进如何形成可验证 repair delta？ |
| [第 9 讲](09-engineering-a-control-plane-rule.md) | 如何给 Control Plane 增加一条规则 | 如何从 invariant、ordered rules、schema、projection 到 smoke 完成一次可审计变更？ |
| [第 10 讲](10-autonomous-agent-quality-gates.md) | Agent 自主写代码时的分层质量门禁 | 如何按风险选择确定性测试、canary、模型行为验证与 release gate，既保护质量又不阻断普通迭代？ |
| [第 11 讲](11-extension-layer.md) | 扩展层、Governed Execution、Explore 与领域产品 | 外部 effect 如何安全 start/reconcile/settle，Graph/Harness、Auto ML、Auto Research 和 Supervisor 又如何复用 kernel？ |

## 建议学习方式

面向潜在合作方、自编排 runner 或远端开发机接入者的一小时分享，建议使用“导读 + 长程收敛
专题”；专题正文已给出 60 分钟主讲路线，延伸实验和代码领读可以留作课后材料。

准备系统开发 LoopX 的读者先读导读，再读第 1 讲 effectful-program，再读第 2 讲架构导论，最后按
3 到 11 的顺序进行。第 2 讲架构导论从 Issue-Fix、Single-Agent Auto ML 与 Auto Research
推导共同架构，第 3 讲运行端到端路径，第 4 到 8 讲拆开状态、工作图、决策、host 和证据，
第 9 讲把这些知识收束成工程变更方法，第 10 讲建立自主交付的质量门禁，第 11 讲再系统
讨论扩展层及 material external effect 的可恢复执行。

只准备开发 Capability Pack 的读者，可以先读 2、4、6、8、11，再按改动涉及的 Kernel
边界补读 5、7、9、10。准备修改 Kernel 的读者应按完整顺序阅读，因为 quota、scheduler、
todo 和 evidence 的局部规则会在同一轮组合生效。

不要从模块文件头一路向下读。每讲的“核心代码领读”会给出函数级入口，先搜索目标函数，再沿 bounded-context helper 向下读。运行实验时使用临时 goal 和测试仓库，不要把课程占位 id 当作真实配置。

## 组合推理是课程主线

单个概念通常不难；控制面的复杂性来自多个各自正确的规则在同一轮里同时成立。读课时不要只问“这个字段是什么意思”，还要推导：谁拥有事实、哪条规则优先、三个 interaction channel 分别输出什么、host 是否应继续唤醒，以及什么证据才允许 spend 或 closeout。

下面几组组合会在后续章节反复出现：

| 组合场景 | 必须回答的关键问题 | 主要章节 |
| --- | --- | --- |
| due monitor + scoped user gate + autonomous replan | monitor 的新证据如何形成 gate；replan 是否覆盖 quiet；未获授权的 delivery 为什么仍不可执行？ | 第 4、5、6 讲 |
| interleaved monitors + per-lane no-change streak + advancement precedence | 为什么一个 monitor 的轮询不能替另一个清零；何时应 replan，何时仍由 runnable advancement 优先？ | 第 4、6、8 讲 |
| non-blocking `user_action` + `required_decision_scopes` + interaction budget | 用户可见提醒为什么不能冒充授权；压缩输出时哪些 gate 语义必须保留？ | 第 3、4、7 讲 |
| unscoped user gate + multi-agent frontier + explicit global authority | 缺失 scope 为什么应触发 projection repair，而不是默认冻结整个 goal；真正的全局 gate 如何明确表达？ | 第 3、4、8 讲 |
| claim + lease + capability + workspace guard + handoff | 谁可领取、谁正在执行、在哪里允许写、何时必须换 peer，为什么是五个不同问题？ | 第 3、4 讲 |
| stateful backoff + proposal identity + host readback + durable ACK | cadence 改变后如何区分“宿主值正确”和“当前 proposal 已结算”；为什么 ACK 与 quiet poll 都不是 delivery？ | 第 5、6 讲 |
| Explore Graph + Harness + resource capacity + provider receipt | 已验证 finding 怎样影响候选组合但不直接 launch；资源释放和 exact task readback 怎样重新打开合法 frontier？ | 第 2、3、4、5、6、9 讲 |
| guided hot path + deterministic oracle + actual-default model qualification | 缩短 agent-facing packet 时，如何同时证明字段合同、状态语义和真实模型行为没有漂移？ | 第 4、7、8 讲 |
| benchmark postcondition + committed Turn + runner readiness | 结果通过为什么不能替代因果归因与控制面回执；测试替身怎样避免改写 meaningful operation 语义？ | 第 6、8 讲 |
| external write + Turn journal + provider receipt + settlement checkpoint | 远端效果不确定时为何不能盲目 retry；什么时候应 replay provider，什么时候只能恢复 writeback 或 spend？ | 第 6、8、11 讲 |

这些组合是同一状态图的交叉切面。课程中的 case、decision table 和 smoke 应尽量覆盖交叉项。

## 版本与边界

课程以仓库当前 `main` 的公开 CLI、协议文档和 smoke 为准。代码移动后，应在同一个 PR 中更新函数路径和阅读顺序；行为变化后，应先更新 canonical contract 或 focused test，再调整课程解释。

课程不承载真实线程、私有 todo、内部文档、本机路径、raw transcript、凭证或生产操作记录。需要讲解真实故障时，只保留能复现状态机的最小 public-safe fixture。

继续开发前还应阅读：

- [Developer guide](../README.md)
- [Testing and quality](../testing-and-quality.md)
- [Core control-plane graphs](../../product/core-control-plane/README.md)
- [Public/private boundary](../../public-private-boundary.md)
