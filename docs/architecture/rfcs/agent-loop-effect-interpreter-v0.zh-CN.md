# RFC：Agent Loop Effect Interpreter（v0）

| 字段 | 值 |
|---|---|
| 状态 | 已接受 |
| 替代 / 关闭 | 无 |
| 日期 | 2026-08-08 |
| 最近规范性修订 | 2026-10-27 |
| 作者 | LoopX maintainers |
| 范围 | 公开控制面文档、packet 合同、重构方向、测试策略 |

> 本文与[英文版](./agent-loop-effect-interpreter-v0.md)互为语义镜像；两者不一致属于缺陷。

## 摘要

LoopX harness 应该被解释、设计和测试为**一个 agent loop 外围的 effectful program**，而不是一组彼此无关的状态机集合。

标准形状是：

```text
model -> effect request -> harness interprets effect -> observation -> model
```

agent loop 是循环本身；harness 是解释每个 effect request 并向下一个模型步骤返回 observation 的 effectful program。

该框架建立在齐梦星空的公开讲座系列之上：
[主线一：Agent Loop 是 effectful program(1)](https://www.xiaohongshu.com/discovery/item/6a01d501000000003700c5de?source=webshare&xhsshare=pc_web&xsec_token=ABqpNuladcxhev099wLKw8M3ilhKBua0BQXNpxnBZEGkc=&xsec_source=pc_share)、
[主线一：Tool Calling 是 Kleisli arrow(2)](https://www.xiaohongshu.com/discovery/item/6a02f388000000003502b2d6?source=webshare&xhsshare=pc_web&xsec_token=ABHcIpzpd2RlhAaRr9sZZ-q1OIfRgt7rvG2jn7GUO3tNo=&xsec_source=pc_share) 和
[主线一：Agent Loop 里的小魔法：函数的组合(3)](https://www.xiaohongshu.com/discovery/item/6a057524000000003701f6aa?source=webshare&xhsshare=pc_web&xsec_token=AB43lNCJ5ULmfTrGfeTLWd2-jQ6q8nFMGyNAd-tlXJ1uw=&xsec_source=pc_share)。

LoopX 的职责是中间两步：它接收来自 agent 或 host 的 effect request，决定是否以及如何解释它，写回 observation，并把控制权交还给下一个循环迭代。

本 RFC 建立这套心智模型，定义 canonical packet 语义，并给出一个里程碑计划，让文档、代码和测试逐步与该模型对齐。

## 里程碑状态

| 里程碑 | 状态 |
|---|---|
| M0 RFC 与 Lecture 0 | 已合并（#2905、#2906、#2908） |
| M1 Canonical packet 示例 | 已合并（#2907、#2910） |
| M1.5 组合视角 | 已合并（#2911） |
| M2 Bounded context 对齐 | 已合并/完成（#2912-#2915、#2919、#2926、#2933、#2963-#2982） |
| M3 聚焦测试族 | 已合并/完成（#2916-#2918、#2925、#2929、#2984） |
| M4 架构文档 | 已合并/完成（#2921、#2923、#2924、#2985） |
| M5 稳态评审 | 已合并/完成（#2922、#2931、#2984、#2985） |
| M6 通用 effect-program 抽象 | Narrow gate 已完成（#2963-#2987）；定性提升需要 M7 |
| M7.1 因果刻画 | 已合并/完成（#2994、#2998、#3009、#3022、#3026） |
| M7.2 Typed settlement runtime | 已合并/完成（#3016、#3020、#3023、#3024、#3033-#3036） |
| M7.3 共享 executor 决策 | 以 no-follow-up 关闭：两个 adapter 共享 algebra，但不共享执行所有权 |
| M7.4 有界核心路径采用 | 首个非 Turn 路径 task lease 已落地（#3091、#3095）；仅在 typed effect 能删除重复 runtime truth 时继续 |

## 为什么这很重要

现在 LoopX 有很多正确但难以解释的部件：

- todo lifecycle 与 handoff 状态；
- quota decision 与 spend 状态；
- scheduler 与 heartbeat 状态；
- capability gate 与 user gate；
- vision、monitor 与 replan 状态；
- evidence 与 run history。

每个部件都有自己的状态机。难点不是这些状态机存在，而是读者无法立即看到每个状态机解释的是什么 effect、产生什么 observation，以及该 observation 如何回到下一轮循环。

agent-loop-as-effectful-program 视角通过在每个地方问同一个问题来解决这一点：

> 谁解释这个 effect request，返回什么 observation？

## 核心心智模型

### Agent Loop

底层循环是：

```text
model -> effect request -> harness interprets effect -> observation -> model
```

模型提出下一个动作。harness 决定该动作是否被允许、如何执行、如何处理失败，以及如何把结果编码成下一个模型步骤可用的输入。

### Effectful Program

纯计算是：

```text
A => B
```

effectful 计算是：

```text
A => F[B]
```

`F` 捕获外部世界：持久化、权限、预算、时序、通知、调度、evidence 和失败。

最好把 LoopX harness 理解为长程 agent loop 外层的那个 `F`：

```text
GoalState => F[QuotaDecision]
```

## 把 LoopX 概念映射到本框架

| 讲座概念 | LoopX 对应物 |
|---|---|
| Agent loop | 每个自动化 heartbeat、PR monitor 和持续重构 turn |
| Effect request | `todo add`、`quota spend`、`refresh-state`、`notify`、`monitor poll`、`bind-agent-thread` |
| Harness 解释 effect | `quota should-run` + `interaction_contract` + `capability_gate` + `work_lane_contract` + `scheduler_hint` |
| Observation | Quota packet、run history、evidence log、state writeback |
| Middleware mount points | User gate、capability bridge、scheduler ACK、cooldown、external evidence poll |
| `A => B` | 理想化的 `GoalState => GoalState` |
| `A => F[B]` | 真实的 `GoalState => F[QuotaDecision]` |

## Canonical Packet 语义

每个重要的控制面 packet 都应该能用四个语义槽解释：

1. `effect_request`
2. `interpretation`
3. `observation`
4. `next_effect`

以 `quota should-run` 为例：

```json
{
  "effect_request": "agent proposes next bounded turn",
  "interpretation": {
    "route": "advancement_task",
    "capability_gate": "repair_bridge",
    "scheduler_hint": "active_work"
  },
  "observation": {
    "decision": "run",
    "recommended_action": "...",
    "state_writeback": "validated_progress"
  },
  "next_effect": "execute bounded turn, then refresh-state"
}
```

这些槽不是第二套 schema。它们是对现有 packet 字段的文档与命名纪律。只有当真实调用方需要一个统一位置读取全部四个槽时，新 packet 才可以增加 `effect_interpretation` envelope。

## 组合与 Around 语义

标准循环是一个 effectful 步骤：

```text
GoalState => F[QuotaDecision]
```

公开讲座系列区分三层组合：

| 组合 | 形状 | LoopX 对应物 |
|---|---|---|
| 函数组合 | `A => B`、`B => C` | Read model -> projection -> decision |
| Kleisli 组合 | `A => F[B]`、`B => F[C]` | 一个有界 turn、host effect、经过验证的 writeback |
| Middleware 组合 | `(A => F[B]) => (A => F[B])` | `capability_gate`、`interaction_contract`、`work_lane_contract`、`scheduler_hint` 中的 around decision |

LoopX 不暴露通用 Python middleware registry。它的 around 语义是声明式的、packet 形状的。

### 有界 Kleisli Runtime Decision

M7 把 Kleisli 组合当作执行要求，而不是装饰性术语。选中的 turn-closeout slice 应该能解释为一组 typed 步骤：

```text
A => F[B]
B => F[C]
A => F[C]
```

对该 slice 而言，`F` 必须保留带 receipt 的结果，并显式表达 cancellation、permission-denial、budget-rejection 和 settlement outcome。组合可以用 closeout 局部的 `bind`、`flat_map` 或 `and_then` seam 实现，但 M7.2 必须证明语义，而不是标准化某一个方法名。聚焦测试必须覆盖：

- identity：增加 typed no-op 步骤不改变 receipt 或 effect；
- associativity：对同一组有序步骤重新分组，不改变 receipt、短路点或外部可见 effect 顺序；
- ordered short-circuit：typed failure 阻止后续 effect 执行，同时不丢失失败类型；
- replay：durable receipt 跳过已经提交的 effect；以及
- non-commutativity：writeback、spend 与 host handoff 不允许重排。

runtime algebra 目前有三个一等 adapter。默认 Codex App 路径通过跨 agent/host 边界的 data-encoded CLI effects 结算普通 LoopX turn。隔离 turn driver 通过 in-process callbacks 执行同一 settlement 形状。Task-lease acquire 也组合相同 algebra 来连接 validation 与 durable lease write，但 owner eligibility、conflict、file lock 和 CAS 仍归自己的 bounded context。三个 adapter 共享 plan、receipt、effect identity 和 failure 语义，不共享同一个 executor，因为它们的 authority boundary 不同。在共享执行所有权被证明之前，通用 `Kleisli`、middleware stack、executor registry 或通用 `Effect` monad 仍为时过早。

共享 settlement algebra 由核心 `effect_program` 模块拥有。Quota 只提供 Codex App/CLI plan builder 与兼容 re-export；各 runtime adapter 组合核心 algebra，而不是继承领域 program，也不会把自己的执行权上移到通用基类。

### Handler 是数据，不是 Callable

Runtime middleware 接收一个 `handler` callable，并决定是否调用、调用一次、重试、fallback 或短路。LoopX 无法跨 context 和 session 边界接收 model 或 host callable。相反，interpreter 在 packet 中返回 `next_effect`：CLI actions、scheduler ACK 和 failure hint。host 或下一个自动化 turn 调用这个 data-encoded handler。

这保留了 around 风格的能力，同时让 handler 可持久、可重放：

- short-circuit：`decision` 和 `effective_action` 可以说 `skip`、`wait`、`monitor_quiet_skip`、`repair_bridge` 或 `ask_owner`，而不假装原 effect 已执行；
- rewrite：`work_lane_contract` 可以用到期 monitor 或 Lark inbox 抢占普通 advancement，`capability_gate` 可以把 next effect 重写为先物化缺失能力；
- settle：`scheduler_hint.ack_hint` 和 `failure_hint` 告诉 host 如何提交成功或失败，`unchanged_poll` 限制重复尝试。

失败、取消、权限和预算保持在 typed packet 字段中可见，而不是被 catch-all wrapper 吞掉：

| Around layer | Packet 字段 | 短路示例 | 重写示例 |
|---|---|---|---|
| Capability | `capability_gate` | `ask_owner`、`repair_bridge`、`unsupported` | 为缺失能力生成 repair todo 与 CLI actions |
| Interaction | `interaction_contract` | 用户通道 `action_required`、`mode` | Primary action、protocol action、next CLI actions |
| Work lane | `work_lane_contract` | Monitor 或 inbox 抢占、`must_attempt_work=false` | Selected lane、obligation、`next_lane` |
| Scheduler | `scheduler_hint` | 暂停/删除 heartbeat、no-spend quiet | RRULE、cadence class、stateful backoff |

这些 around layer 的顺序是合同，不是实现细节。改变顺序会改变先观察到哪个 gate、哪个 monitor 可以抢占普通工作，以及 host update 失败后是否仍然期待 ACK。这类变更需要 parity fixtures 和聚焦测试。

用讲座审视 middleware stack 的同样问题来评审 LoopX around decision：

1. 正在解释哪个 effect request？
2. 哪个 around layer 拥有该 decision，它发出什么 observation？
3. 它能否在不假装 effect 已执行的情况下短路？
4. data-encoded handler（`next_effect`）在哪里？
5. failure、cancellation、permission 和 budget 是结构化的还是被吞掉的？
6. around-layer 顺序是否显式并被测试？
7. evidence、trace 和 budget continuity 是否能穿过 host effect 到达 writeback、ACK 和 spend？

### CLI 是高密度 Effect

单个 tool call 是 `ToolInput => F[ToolOutput]`。LoopX CLI packet 是高密度 effect：一条命令可以在同一个 request 中携带 permission、budget、参数校验、外部执行、失败语义、scheduler ACK 和 writeback。模型仍然只提出 effect request；harness 把它们解释为 CLI actions。

如果某个 vendor API 后续支持串行 tool calls 或交错推理，并不会改变 LoopX 的形状。它只是 interpreter 内部的一种 execution mode：

- serial、parallel 和 interleaved 是执行策略，不是新状态机；
- `effect_request -> interpretation -> observation -> next_effect` 保持稳定；
- `next_effect` 从单条 CLI command 变成有序 effect program。

## 通用 Effect-Program 抽象

当前的 `EffectTurn` 视角刻意是 read-only 且 quota-specific 的。它为 LoopX 提供一个稳定词汇、一个 canonical read model，以及围绕一个真实 packet 的 around 语义。它还不是通用 effect-program 抽象。

仅靠重构不会产生该抽象。重构会创建共享抽象能够安全存在的 bounded contexts。两条轨道并行且同等重要：

- refactor：让每个状态族留在自己的 bounded context；
- generalize：只有在真实 runtime 调用方需要时，才抽取共享 effect 形状。

### 与 Goal Replan 的边界

Effect 执行与 goal replan 相邻但是不同的控制面问题：

| Plane | 问题 | 权威状态 |
|---|---|---|
| Goal path | 为什么继续、还缺什么 outcome、下一步该跑哪条路径？ | Vision、acceptance evidence、path delta、Todo frontier |
| Effect runtime | 一条选中的路径应如何执行、失败、恢复和结算？ | Effect plan、host execution receipts、observation、writeback |

effect runtime 不能决定某个 milestone 是否仍然服务于最终目标。反过来，goal replan 不能重复 effect runtime 的 permission、idempotency、failure 或 settlement 语义。更通用的 effect interpreter 本身不会改善 long-horizon goal alignment。

### Product Outcome Contract

M7 只有在至少产生一个下列最终 effect 时才有理由存在：

1. 从真实 host path 中移除一个竞争性的 transition 或 command truth 来源。
2. 通过稳定 effect ids、显式 authority、idempotency 和 typed receipts，让部分执行可恢复。
3. 让第二个 runtime caller 以更少的编排代码、且不丢失 domain invariants 的方式复用同一执行合同。

下面是支持性证据，而不是 product outcome 本身：

- 存在一个 protocol 或 dataclass；
- `EffectTurn` 在 packet builder 中更早构造；
- 另一个 packet 可以映射到同样的四个名词；
- module line budgets 和 parity tests 通过；或
- 更多 Todo、monitor 或 gate 族放在同一个 interface 后面。

第一个 M7 vertical slice 必须满足所有这些验收检查：

- 一条真实路径拥有 `request -> plan -> host execution -> receipt -> reduce`；
- 至少删除一个旧 command builder、settlement branch 或并行 runtime path；
- fault injection 证明 retry/resume 不会重复 external effect、ACK、writeback 或 spend；
- permission denial、cancellation、budget rejection 和 partial completion 仍然可区分；
- public packets、CLI budgets 和现有 domain transition invariants 保持兼容；并且
- 在抽取共享 interpreter protocol 前识别出第二个 caller。

出现任一 kill criterion 时停止或收窄 M7：

- 新 layer 主要把 raw mappings 或 CLI strings 穿过另一个对象，而不拥有执行语义；
- production code 增长，但没有移除任何既有 truth source；
- 提议的 executor 跨越它无法自行结算的 model、user 或 host ownership boundary；
- parity 无法把行为变化归因到新路径；或
- 第二个真实 caller 并不需要提议的共享 protocol。

### 今天已存在什么

- `EffectRequest`、`EffectInterpretation`、`EffectObservation`、`EffectNext` 和 `EffectTurn` 作为 canonical slots。
- 核心层已经拥有 settlement algebra：`SettlementIdentity`、`SettlementPlan`、`SettlementReceipt`、typed failure kinds，以及保留 receipt 的 `SettlementResult.bind`。
- 默认 Codex App / CLI quota 路径构建一份 typed settlement plan，把 validation、durable writeback、quota spend 和 conditional terminal closeout 绑定到原始 turn effect identity。final `no_followup` 是 spend 后 effect；普通 successor completion 仍属于 Todo lifecycle（#3016、#3033、#3034）。
- 隔离 turn driver 通过自己的 callback executor 消费同一套 plan、identity、receipt、failure、replay 和 short-circuit algebra（#3020、#3023）；terminal closeout 单独写入 journal，因此 closeout 失败只重试 closeout，不重复 writeback/spend；loop controller 从已提交 receipt chain 派生 continuation，不再维护第二份 settlement truth（#3024）。
- Task-lease acquire 是第一个有界采用该 algebra 的非 Turn 核心路径。adapter 把 validation 绑定到现有原子 lease write；纯 eligibility、conflict、file-lock 和 CAS 规则仍由 task-lease bounded context 持有（#3091、#3095）。
- quota spend 的 callback 失败使用可选事实 `quota_rejection_action`，只有配额准入本身拒绝扣费时才从既有 effective-action owner 复制该值。TypeScript 结算 owner 将 `throttled_skip` 映射到 `budget_rejected`；嵌套 quota 快照不构成这项事实，writeback 和 terminal 拒绝保留各自的失败类型。值缺失、格式错误或未知时保留 `quota_spend_rejected`，即使说明文字包含 "budget"。这改变此前依赖文本的诊断分类，不改变准入、扣费或重试权限。已保存的失败回执保持原样读取；新 callback 不得依赖文案要求预算分类。
- Scheduler apply、ACK、failure writeback 和 cadence 仍是 agent-owned settlement 之外的数据化 host handoff。
- `interpret_quota_should_run_packet` 与 `interpret_turn_result_packet` 继续作为 packet lens；`EffectProgram` 和 `effect_program_from_ordered_steps` 继续为 bootstrap 与本地 scheduler construction 提供兼容的 ordered-step reader。
- Outcome-continuity wait 已按因果关系判断。没有 material trigger 和 fresh evidence-linked path decision 的 `unchanged_with_reason` checkpoint，不能清除更早的 material checkpoint 或五条 Todo 完成长链 gap。这是有意的 qualification 行为，不是 watch-ACK 集成回归（#2998、#3009、#3022）。
- 正式测试已经覆盖合法 phase prefix、failure short-circuit、replay、effect identity exactly-once、跨 adapter conformance、语义 mutation sentinel 和 public-safe 事故回放（#3026、#3032、#3035、#3036）。
- R1 替换：bootstrap guided rendering 通过 `EffectProgram` 读取 `ordered_steps`（#2955）。
- R2 替换：turn executor 通过 `interpret_turn_result_packet` 解析 result kind（#2956）。
- R3 替换：Codex CLI 本地 scheduler commands 通过 `EffectProgram` 构建（#2957）。
- R5 替换：quota should-run TurnEnvelope 通过 `interpret_quota_should_run_packet` 派生 canonical action、writeback 和 scheduler slots。
- around 语义编码在 `capability_gate`、`interaction_contract`、`work_lane_contract` 和 `scheduler_hint` 中。
- 聚焦测试和文档固定该视角。

配额收尾适配现在消费 TS readback 按回执归约的结算进度，不再独立把存在 spend run 当作已结算。正常刷新、重放和扣款响应共享该投影；可执行命令绑定原 Agent 和路由。补回执复用现有幂等 writer。这是有界的 M7.4 接入，没有增加共享 executor 或 authority store，也不证明 Todo 终态或 Goal 验收完成。

此前宿主 Turn 的恢复由 guard 与 settlement readback 共享的 TS candidate owner
决定。重入 Turn 只能依赖同一准入回执历史中首次记录早于自己的 Turn；后来 Turn
不能阻断前序恢复，晚到重放或绑定升级不改变这个顺序。不以 Turn 编号或墙钟作为
排序授权。持久身份冲突仍 fail closed，新 Turn 仍须处理全部未结算回合。
真实 File/SQLite CLI 覆盖旧任务受阻后另一 Turn 已准入、旧任务重开、恢复原绑定、
检查失败无扣费收尾，以及后继重放只扣费一次。这修复本机恢复循环；安装态宿主
和更广协作资格仍须独立验收。

R5 短包投影也完整保留已有 CLI 结算计划，包括 effect identity、带条件的步骤顺序和
宿主交接。签名覆盖 v5 能检测计划被删除或修改；没有计划的输入保留原覆盖版本。
真实 CLI 验证覆盖提前扣额度被拒绝、原 Turn 写回和结算一次，以及幂等重放。
这补上了投影缺口；同 Turn 缓存详情、普通轮与 replan 上下文选择、模型行为收益
尚未验收，8 KiB 目标和交付时增长检查保持不变。见
[TurnEnvelope](../../reference/protocols/turn-envelope-v0.md)。

共享结算命令 renderer 现在在生成的步骤子命令前加入全局 JSON 输出选项，包含
写回后的恢复命令。App heartbeat、外部 CLI 和 visible Goal 无需由调用者补插
参数；真实 CLI 测试覆盖完整包/短包一致、写回前拒绝和重放只扣费一次。这是
传输修正，不代表 R5 模型上下文效率已验收，也不改变类型化结算规则。

现有 R5 CLI 保存完整决策后再投影短包；每次 guard 使用独立私有目录，补读不重跑准入。默认关闭的 heartbeat renderer/shared worker 接入现已覆盖明确的 host-owned Turn：同轮选择保留捕获入口，真实 CLI 验证重入和一次结算。Python 仅适配文件与命令传输，复用 TypeScript 决策 owner。原生 Goal 自动 begin-Turn、安装态 App/Lark/UI、模型 token/IO 与决策质量仍未验收；不据此变更现有实验。参见 [TurnEnvelope 捕获契约](../../reference/protocols/turn-envelope-v0.md)。

### 还缺什么

#### Heartbeat 与 TurnEnvelope 收敛

沿用既有 M7.4 与 roadmap S2/S3/S6/S8，把 heartbeat 和 Turn 执行统一为
**一份受治理工作契约**，显式保留宿主效果所有权。[Controller/executor RFC](controller-executor-unified-contract-v0.zh-CN.md)
拥有完整 schema/消费者迁移和可选模型会话分工：以同一 schema、full/delta 交付及不可变
恢复替代长期 quota/Turn 视图。这不宣称 cutover 已交付。已准入 Todo 随后等待定时、PR 或能力时，
typed receipt owner 保留原回写与结算路径；即使工作已 deferred 或等待已到期，重入也
只投影原收尾，不恢复业务执行、不选择后继、不创建重试时钟。恢复复用原回执中的正向
工作准入和精确结算绑定；工作区修复本身没有业务结果，须先经同轮正常 guard 对精确
同一 Todo 的新鲜选择重新准入。没有该选择的宽泛兼容标志不能把原结算绑定变成工作
准入。原语义重规划轮次仍保留该决策，不会被误当成已有业务结果。真实 File/SQLite CLI 验证
返回命令、拒绝换绑、一次回写与扣额、初始重规划及已结算重放。此源码路径修复不代表
安装宿主采用或完整成本验收。
显式选择的 CLI 程序现通过共享 invocation transport 保留到生成的工作选择、必读、
调度及结算/恢复命令。默认路由不变；选择程序不新增准入或执行权限。候选安装和
接收者实际采用仍须与命令路由资格分别验收。
现有大 quota packet 与小 TurnEnvelope
都不是仅凭大小就合理的目标形态。可选 memory 参与事实现进入一个有界、签名覆盖的
envelope 投影，Codex CLI 保持关闭隔离；这不代表安装态 heartbeat/App 或模型收益
已验收。

memory 参与现由能力层组合的选择后上下文 hook 注册接入 native Turn 和执行中的
quota/heartbeat guard。Codex、DSH、generic CLI 传输相同的有界上下文、指导与可选
结果字段；绑定和模式判断归能力，adapter 只保留宿主格式。TypeScript 限制贡献，拒绝
替换权限／核心结果字段，复用既有 governing 签名文档及来源读回绑定上下文。只增加
原捕获的定位信息不使未变决策失效；actor、权限、配置读回或贡献变化则丢弃可选上下文。

通用 Python 贡献 IO 与既有注册和 dispatcher 共同归属
`loopx.control_plane.capability_hooks`，不再保留平行的 Python Turn-context 模块。
校验与来源绑定继续调用 TypeScript decoder，宿主 adapter 消费同一份 hook 投影。

注册的 `PostSettlement` callback 在独立精确反思证明和可记账 writeback/spend 后调用
既有 ingest owner，手动宿主的 sidecar adapter 也使用同一注册。`post_writeback` 继续
无副作用；逐 hook 回执保留已完成工作，只恢复结果不确定的其他 hook，provider owner
继续负责写入去重与对账。终态重放保留原 journal 与回执，从原 owner 回读的恢复结果
通过 `post_settlement_recovery` 返回，不重新结算。File/SQLite 原生宿主及隔离 provider 用例只资格源码路径；
安装态 App/Lark、模型收益、完整成本及以下三项收敛验收仍开放。

注册前 journal 的恢复保留已准入的有界反思及独立验证 digest，不改写原 plan、不重启
Host／validator，也不为新工作激活旧输入。provider 效果仍由当前 ingest 策略及原结算
owner 决定，缓存内容本身不授予写入权限。

PR 依赖观察现复用既有注册 TurnStart 生命周期，由执行性 quota guard、原生 Turn
与兼容 prequota 共用。权限来自 typed Goal／Agent capability owner，包括已记住的
宿主观察与撤销；已有授权时不必逐命令补 flag。能力自有诊断使 PR 业务规则留在 core
Turn 之外；完整源 TS 计划、合并事实 owner 与正常执行准入仍为原权威。Goal 内
single-flight 与每项依赖的持久节奏避免并发／到期读取重复。File/SQLite 验证覆盖业务
额度暂停／停止、full/thin heartbeat 生成命令、跨入口观察、撤销、不确定效果、provider
失败后的无关独立校验工作及原 journal 重放。App/Lark 共用上下文后端只消费 canonical
就绪状态，不轮询。plan、只读／status／上下文、缺少权限与恢复路径均不添加观察。
安装态执行器采用、既有真实 owner 的合法恢复、持续调度、模型结果与以下三批收敛验收
仍开放。安装态 memory 生命周期采用仍单独验收；post-writeback hook 继续无效果。

同次捕获决策中的所选 Task 继续条件与满足状态，现在沿完整选择投影、签名 envelope
到实际协议宿主输入完整保留。File/SQLite 验证区分不同仓库的同号 PR，保留原事件时间
和长要求尾部，独立验证后续工作并拒绝证明篡改。只读 managed-step 与原 journal
重放不新增观察或扣费。本批补齐事实交付遗漏，安装采用与模型结果仍另行验收。

有界规划上下文现在保持内联，不再因短包预算压力而强制读取整份决策；既有 owner 的
3 KiB 限制保留。共享 DSH/cloud adapter 传递签名内容，历史引用同时保留原捕获路由和
哈希。thin renderer 仅在存在 `agent_context.detail_ref` 时要求能力补读，不能把也用于
完整上下文的 `delivery=projected` 当作省略标志。原始来源与实际 Host 输入 oracle 通过
File/SQLite CLI 和无模型 DSH 子进程覆盖开启／关闭、历史引用及签名拒绝。8 KiB 警告
保持；选择默认形态前须计算 packet 与必要补读／源 IO。真实模型行为、安装采用及以下
三项验收仍未完成。

真实绑定的重规划暴露了共享 Host adapter 的另一处遗漏：保留了泛化动作，却丢失
签名 replan packet 及其证据路由。DSH/cloud adapter 现在同时传递两者，保留既有
完整 Goal 必读和同次捕获的 replan context。File/SQLite 从选择到重规划的资格验证
通过仅使用协议 fixture 的 runner 核对实际 DSH provider 输入、签名篡改拒绝及来源／
状态未变，不调用模型。写回、quota 和 scheduler 仍归外层 Turn executor；普通请求
保持原输入。本切片只验证该传输边界，安装采用、后续模型决策、完整成本及以下三项
验收仍未完成。

第一批收敛的当前源码路径核对：

| 执行事实 | 既有 owner／投影 | 保留的边界 |
|---|---|---|
| Goal/Agent/Todo 身份 | quota 选择及回执；envelope actor、selected Todo 和签名结算身份 | 捕获身份与写入时校验仍必要；展示身份不授予执行权 |
| 完整要求 | Agent channel 权威 work context 与剩余必读；有效 Todo、验收及 scoped User 义务，完整 Goal 保留渐进式全文读取 | 注册 reader 在两种传输中满足当前任务读取；来源失败会阻止依赖它的交付，真实模型采用和打包宿主旅程仍待验收 |
| 能力拒绝 | 既有 quota `capability_gate_v0`；短包保留精确 `required`/`missing` 数组与源中历史字段 | 修复事实遗漏，不改变就绪策略或能力开启 |
| 选择／claim／lease | selected Todo、action portfolio 及当前所属事务 | 短包归属不是新鲜 lease；保留捕获源并在所属写入入口复核 |
| replan／Goal 收尾 | replan action packet 与既有 TS capsule 保留完整、有序 audit 步骤/指导、当前 judge（收尾评估） 及证据与非证明条件 | 同一捕获的 File/SQLite 决策验证条款保留与签名突变；完整证据仍走有权限的详情。宿主/模型采用仍开放；Todo 完成不证明 Goal 完成 |
| 结算／scheduler | 完整 typed 结算计划及显式宿主调度投影；省略的调度 argv 引用同一捕获的完整决策 | 此投影退役未绑定的详情命令。实际宿主读回、过期写入拒绝与一次恢复仍待宿主采用验收 |
| 可选 memory | 核验后的 boundary 参与事实及新鲜宿主绑定 | 关闭／recall／ingest／失效／provider 失败隔离持续保留；传输等价不是模型收益 |

边界投影现完整保留范围、审批、守卫和停止条款，不再套用显示裁剪。共享 DSH／云端
candidate 适配器也传递此前遗漏的签名限制，原范围／workspace 兼容字段各保留一份。
独立来源 oracle、真实 File/SQLite CLI 和仅使用协议 fixture 的 Host 子进程能复现旧
遗漏，并验证完整传递、篡改拒绝和不扣费回放。这只修复一个既有调用族的边界交付，
不新增执行规则 owner。保留 8 KiB 警告，披露恢复条款的成本；live 模型行为、安装态
采用和完整成本仍须在推广前分别验收。

能力事实修复归既有 TS read-model owner。真实捕获的 File/SQLite 决策覆盖拒绝及
准入，包含关闭、仅 recall、仅 ingest 与配置失效。新投影签名覆盖这些保留事实，
不重写历史签名、不增覆盖版本、准入规则或 Python 策略。宿主 provider 失败和
结算另有覆盖，仍不证明安装态 App 收敛或模型成本。以下三项继续按各自验收保持
未完成；这份核对不授权批量删除剩余 Python IO adapter。

接续 audit 投影还保留每个有序 closeout 步骤和完整当前 judge（收尾评估）。旧 capsule 在签名前就
遗漏这些字段，并把接续指导截成 48 字符。捕获的 File/SQLite 工作和 replan 路径现在
保留这些条款，修改步骤、顺序或 judge 后拒绝签名等价。既有 8 KiB 诊断阈值不变，
必需文本可以超出并警告。这只关闭传输遗漏，不代表三项收敛验收通过，也不授权退役仍
活跃的 Python/host adapter。

沿 capsule 追到实际共享 Host 输入又暴露一处丢失：DSH／云端只传递其中的
unavailable-context 字段，签名有效的 audit 没有到达 provider。适配器现传递既有
audit 及原捕获路由／哈希。真实 File/SQLite 普通工作与 replan 捕获通过无模型 DSH
子进程验证有序义务、继续执行的 judge、非证明条件、签名拒绝及来源／状态未变。
同次核对还发现工作资格丢失：capsule 过滤了成果底线／退化规则，该 Host 又遗漏整份
既有 execution profile。typed 投影现完整保留，Host 直接传递。携带该 profile 的新
material result 返回公开安全的 typed `work_evidence` 与 `validation_evidence`
数组，并保留实际 delivery scale／outcome；外层 executor 验证并保留两组证据及分类。
原生来源 oracle 和签名篡改覆盖这些规则。两种契约都不存在的 prompt 保持原输入，旧
journal replay 保持可读；所有状态、quota 和 scheduler 效果仍归外层 executor。本批
只验证该调用方的条款交付，不证明模型遵守、安装采用或完整成本收益。应计入恢复要求
的成本；旧的不完整输入不能作为已验收的效率基线。

完整 execution profile 在原有 96 行公开 CLI 夹具中，令普通 Turn plan 增加
138–142 个 JSON 字符／UTF-8 字节，不增加行数。原 base/head 差分超过 64／70
字符和 128 字节增长额度，仍按原失败保留。仅当旧四字段 profile 补齐成果底线和
退化字段、签名覆盖不变且无其他 JSON 结构变化时，给予一次性 160 字符／字节额度。
已经完整、只补部分或无关变化均不适用。绝对预算、行数、夹具人口、完整来源／Host
条款验证及生产 8 KiB 诊断保持。本结果说明必要展示成本，不代表模型 token 或效率收益。

统一工作上下文现在由既有 TS interaction owner 选择并满足必读，注册的 Python
adapter 核验来源，在普通 heartbeat/quota 与签名 envelope 中返回完整当前任务来源，
无需启用 TurnEnvelope。混合 Goal 文件仍必须经渐进式路径全文读取，不能按历史标题
裁剪后冒充原始目标。未支持的来源保留必读命令，来源失败或变化会阻止依赖它的交付。
一份 Agent carrier 保留明确空列表、完整要求尾部及 hook 身份；精确 Todo 返回一份
完整源记录，概览仍有界。真实 legacy/File/SQLite CLI 覆盖源丢失、长要求尾部、
scoped gate、恢复和签名内容篡改。见[工作上下文契约](../../reference/required-work-context.md)。
这是普通上下文交付默认值的变化，不是 transport 默认值的变化；模型结果、安装态
App/Lark 旅程及整体效率仍单独验收，保留尚有真实调用方的 Python IO adapter。

no-write model-behavior 安全适配器现在识别原生 scheduler 在 ACK/failure
命令前绑定的 `--registry`／`--runtime-root` 路由前缀。验证走真实 binder 和
TurnEnvelope 投影，不能只用未绑定的合成 argv。适配器只为递归保密扫描解码
有界 wire：路由值、解码后的扩展及无关 alias 仍被扫描，原 packet 和 wire
原样传递。未知或损坏前缀、hint 与命令不匹配继续拒绝。这修复传输误拒绝，
不改变 scheduler 准入、执行授权、默认值或宿主效果；打包后的传输验证不代表
live 模型结果、App 采用或以下三项收敛验收完成。

完整要求交付（#5844）、完整收尾条款（#6337）和原始 Host 决策捕获（#6350）的
源码修复已合入。保留这些成果；它们的完成不关闭 M7.4、R5 或下列三条验收线。
后续沿既有 owner 和 checkpoint 按此顺序推进：

| Todo | 可观察结果与决定性验收 |
|---|---|
| 核对 heartbeat 与 TurnEnvelope 的执行和上下文要求 | 同一捕获的权威决策保留 actor/Goal/Todo、必读全文、claim/lease、选择、replan/收尾、带条件结算和 scheduler 所有权。删除渲染分支前列明遗漏与重复事实。覆盖可选能力关闭、仅 recall、仅 ingest、绑定失效和 provider 失败；私有详情只经有权限的引用访问。 |
| 在真实宿主入口采用统一契约 | heartbeat full/thin 与 Turn host 消费同一 schema 及同 Turn 捕获/详情入口，不长期保留分格式的语义视图；显式保留通知和 scheduler 的宿主传输。真实 File/SQLite CLI 与打包 Codex App 覆盖重入、来源丢失、必读前拒绝、迟到结果、backoff 和一次结算；补读不触发第二次准入。最后调用方迁移后才退役旧投影。 |
| 核验上下文形态及迁移默认 | 将相同的正常、replan、等待/恢复与可选能力负载同时对照当前完整和短包路径，测量载荷/model token、详情 IO、延迟、资源增长、遗漏、决策与结果质量。数据丢失、重复效果、身份和结算错误保持硬约束；保留受支持的已保存 prompt/回执与可逆 rollout，根据证据再改预算或默认。 |

**上下文成本与投影融合：下一实现切片**

现有完整来源交付修复了要求遗漏，但共享 `work_context` 并不意味着两种传输已经紧凑。
改变默认值前，先用同次捕获的数据对照两条生产投影。区分包字节与模型输入，以及操作者/
控制面轨迹与 solver 轨迹；操作者收到大包，不能证明 solver 消费了那个包。初次返回、
截断后的恢复和必要详情读取应合计，缓存输入仍占上下文；历史上漏要求的短输入不能作为
效率基线。

下一切片继续归属上文已有三个融合验收流：

1. **以完整首包、后续子树增量作为融合目标。** 优先优化 benchmark case 内 solver 实际
   消费的普通 `quota should-run` 输入，实验操作者/控制面对话及个人维护包为次要项。
   共享投影首次完整交付，支持温态复用时只交付精确变化子树。Solver 保留产品入口，
   两种旧 packet 格式共同迁入统一契约。
   包内去重和冷启动缩减作为配套；连续做小清理不能替代重复输入路径的验收。存储 capture
   缩小或 import 清理不能证明模型输入节省。
2. **跨宿主使用同一契约、同一交付机制。** 扩展已有 typed interaction/work-context owner，
   同时生成完整和增量上下文，用统一 schema 替代 quota 与 TurnEnvelope 输出 schema；
   旧 decoder 只服务具名迁移 reader 和历史回执 replay。不同宿主外层包装不构成不同契约
   视图。保留原始捕获、required-source 校验、调用、选择、结算与详情入口；Python 保留来源 IO，host 保留效果执行和上下文连续性
   观察。不新增独立准入服务、diff daemon 或 Python 上下文决策 owner。刻画调用方并在采用后
   删除被替代投影。输出释放前的 host 传输 ACK 既不证明模型完整收到了内容，也不证明其仍被
   保留。现有 capture/handoff 是接入位置，并非已经实现的连续性契约。
3. **保留完整当前权限事实与冷启动要求。** 每次响应重新给出身份、动作、准入、范围/审批/停止
   条件、取消、选择、claim/lease、未完成必读及结算/scheduler 义务。注册的完整来源及 packet
   投影可以精确复用，当前控制结果即使字节未变也完整交付。复用的要求正文仍具有完整约束力，包括其中
   的文字停止条件；它不是摘要或可选背景。重建后的上下文保留全部原始
   要求。先在结构化 owner 理清混合 Todo 历史和验收解释，再改变必读范围；时间、owner、状态、
   标题、长度均不能证明过时。把必读正文放到引用后再全读回来不是节省，替换累计备注是状态
   变化而不是交付压缩。
4. **自动全量回退，不增加人工 gate。** host 显式提供此前完整交付投影的上下文连续性依据，
   绑定 Goal/Agent、消费 session 与 context epoch、投影版本、来源 capture 及投影 digest。
   仅有文件、匹配 hash 或历史回执不够。compaction、reset、不能证明连续性的重启、输出截断、
   损坏、身份/版本不匹配或保留状态未知，自动选择完整当前投影。不引入审批、确认问询、
   第二次 quota 准入或扣额；已有真实停止和来源不可用规则继续生效。

**已接受的增量交付设计（待实现）。** 一期须同时包含共享投影、benchmark solver 与
产品化 Codex App 两个调用场景及各自的全量回退；单独增加 diff helper 不构成交付。

| 边界 | 提议行为 |
|---|---|
| 基线归属 | host 跟踪模型输入的完整交付和上下文连续性；typed 投影核验所提供的绑定并计算 delta，不能从磁盘推断保留状态。不支持的 host 继续全量交付，不要求模型自行声明“仍然记得”。 |
| 完整捕获 | 保留不可变的当前完整决策，供审计、签名与同次捕获恢复。delta 只是展示，不成为新的权威存储，也不用于修改 Goal 状态。 |
| 增量单位 | 精确比较注册的完整 JSON 子树，保留标量类型、缺失与 null、空值及完整字符串。数组按精确摘要所绑定的基线索引编辑，并表达长度变化；索引只是传输坐标，不是推测的实体身份。重排须精确恢复，若全量更小则选全量。不忽略时间戳/revision，也不从正文猜等价性。使用分层变化树表达完整值、对象变化/删除及数组项目/长度，避免重复长路径前缀。 |
| 消费恢复 | 基线缺失/不匹配、跳过/乱序 delta 或输出截断，从原始 capture 返回完整当前投影，不重新准入。丢失的 delta 不能确认为下一基线；完整交付后重设基线，以全量刷新限制增量祖先链，避免无限累积 patch。执行前仍核验来源新鲜度与权限；展示恢复不能重新授权过时或已取消的决策。 |
| CLI 采用 | 保持普通 quota 入口和现有 capture/detail 路径；能观察活跃模型上下文的 host 在传输边界提供可选基线绑定。没有连续性观察者的裸 CLI 返回全量，不能静默信任上次输出文件。具体参数/wire 形式在调用方刻画后确定，不新增命令族。 |
| Turn 采用 | 消费同一完整/增量上下文 owner，同时保留 Turn 签名和外层 executor 效果归属。host 独占的结算执行与手动 CLI agent 的可执行结算义务仍有区别。 |
| 表示选择 | 计入绑定和删除元数据后，只有更小才发送 delta，否则发送全量。不为字节目标截断值或改变来源选择。 |

以下按 surface 区分的绑定描述现有交付/迁移契约；对应 reader 活跃时仍必须遵守。
Controller/executor 提案的 C1–C9 替换两种输出格式；历史 capture 保留原版本，
不能借用新 schema 基线。

**来源与变化边界。** 共享的 `context_delivery_v1` 展示覆盖完整 quota packet、
完整 Turn envelope 及其公共 work context。surface 纳入既有 Goal/Agent/session/epoch/
capture 绑定，不同展示不能借用彼此基线。扩展的是展示，不改变准入、持久化或结算 authority。
注册的完整 packet 投影可精确复用未变子树，未知根字段仍每轮完整交付。

当前身份、动作、准入、范围/批准/停止条件、取消、claim、来源健康、待完成读取、quota
及 scheduler 事实仍每轮给出。结算身份、命令、步骤顺序、条件和附件要求也保持当前完整。
静态前置说明、编写 schema 和可选附件 guidance 可精确复用；温态 wire 未重发，不减弱
它们的约束力。既有 vision audit 的说明适用相同规则，当前判断仍每轮给出。这些控制 owner
内的未知字段保留每轮交付。delta 不再重复携带另一份相同的当前控制字段。

work context 保留注册的完整 Goal 叙述、canonical 验收正文和选定 Todo 文字/备注。
Explore owner 注册精确证据、guidance 和编写元数据；编排 gate、分支选择、分页、当前
来源 revision 和未知字段继续每轮交付。来源解释、上下文读取指令以及 Todo read-model
字段名描述可复用，不删除实际 Todo 字段、当前 revision 或来源依据。未知或混合来源若无
合格 split 则完整交付；来源身份不明确或读取不完整时选全量。变更字符串仍完整替换，包含
迟到约束；不忽略证据状态或时间戳。

此前仅正文 split、变化数组整段替换及逐叶重复路径是初步实现，不是效率目标的上限。
当前 codec 将必发字段直接放进同一分层变化树，其余部分计算精确变化。数组增删、重排、
重复值和 null 都针对该精确基线做结构转换，不模糊匹配。owner 校验预期变化树并重建
不可变完整目标后才交付；即使其余 patch 能重建正确，漏掉未变的当前控制字段仍会被拒绝。
损坏、丢失、基线不匹配或更贵的 delta 自动采用同 capture 全量表示。

比较计入冷启动全量 envelope、绑定、必发控制、变化树元数据、变化值及恢复读取。
狭窄正文比例不能代表整包复用；假定基线保留的离线字节结果仍不证明真实 solver 输入节省
或模型成果。准备/恢复继续使用既有 projection RPC，两种传输共用同一个 TS codec，
Python 仅作薄 adapter；生产 host 采用验收继续保留。

**首个真实调用方与压缩竞态。** 当前原生 Codex CLI session adapter 只观察会话身份，
capture/handoff 的 release 发生在 stdout 交付之前，两者都不提供所需的模型输入连续性
证明。读取保存的原生历史，或在模型请求发出后才观察到 compaction，也已经太迟。因此首个
调用方需要在现有 host 的模型输入组装边界接入 adapter，具备下列性质后才启用增量：

1. compaction、裁剪、restore、fork 或连续性未知时使 context epoch 失效，并观察完整输出
   交付，而不是文件创建。
2. 候选展示绑定原始 capture 与预期基线。在提交模型输入前，置于同一顺序边界内再次核对
   epoch 和基线；若已变化，从该 capture 替换为完整投影。仅有 CLI stdout 回调不够。
3. 只有该精确展示完整进入活跃模型输入后才推进基线。失败或 ACK 不确定时不能确认新基线，
   下一次发送全量；该传输确认仅证明交付，不证明模型理解。

这是 host adapter 的验证条件，不是向 Agent 或 owner 发问的步骤。真实 host 尚无该接入
边界时，普通 CLI 自动保持全量；全部走 full 的 pilot 不能证明增量收益。不能禁用原生
compaction 来制造连续性，也不为这项优化另建模型 runner。先通过成对的 host 边界原型
验证现有传输能否支持 adapter；负结果保留基础包缩减的价值，并记录具体缺失的集成点。

| 情况 | 自动表示选择与效果行为 |
|---|---|
| 首次调用或 host 不支持 | 完整当前控制与上下文；不能仅靠输出文件建立可信温态基线。 |
| 同一 epoch、基线已确认且 delta 更小 | 当前控制全量，加变化来源子树和删除；未变要求保留原有约束力。 |
| guard 到模型提交之间发生 reset/compaction | 使基线失效，在提交前将候选 delta 替换为同 capture 的完整投影；不重新准入或扣额。 |
| 交付缺失、损坏、截断或乱序 | 使相应基线失效，从原 capture 恢复全量。host 无法完整交付正文时沿已有完整读取路径恢复，不确认温态基线。 |
| 来源不可用或权限变化 | 保留既有拒绝/重新 guard 规则；存储的完整 capture 不能制造当前权限。 |
| 增量元数据比全量更贵 | 发送全量，不裁剪值、放宽限制或要求 owner 选择。 |

**一期调用方与交付顺序。** 可独立评审的基础包缩减先进入既有集成测试，该测试不等待
新协议。已接受的设计须在一期同时验证下列两个场景，使用同一 typed 投影与回退契约。
benchmark 可以先落地，但产品化 Codex App 尚未验收时不能关闭一期。

| 调用方 | 一期验收 |
|---|---|
| Benchmark solver | 既有普通 quota 路径先交付全量，确认温态基线后交付更小的精确子树变化。在匹配且已授权的测试中统计首次及补读的全部模型输入、详情读取、工具调用、后续决策和验收成果；管理包或离线字节统计不能代替 solver 采用。 |
| 产品化 Codex App | 已交付的 heartbeat/bootstrap 与 quota 续跑入口复用同一 owner，提供实际打包与安装态读回。验证温态增量及其后有效动作，并覆盖首次使用、重启/恢复、压缩/裁剪、基线丢失和同 capture 全量恢复。检查既有产品入口及可见错误/恢复结果；只有 CLI fixture 不证明 App 采用。 |

每个 host 都须建立前述模型输入顺序与连续性边界，验证各自真实 adapter，不能借另一
host 的证明替代。若 Codex App 缺少该接入点，记录具体缺失的 host 边界、继续安全全量
交付，并保持该一期验收未完成；全部走 full 的 App pilot 只是正确性回退，不算增量
产品化完成。这是工程证据缺口，不能变成运行时 owner 问询或新增准入 gate。保留现有
通知、scheduler、取消、claim、结算与恢复归属，不另建 runner 或 authority。

同一 owner 用于 Turn 传输后，只在调用方采用后退役被替代投影分支。冻结 campaign
保留其来源/处理，通过回放和另行准入的后续运行验证新交付。设计接受本身不授予合并、
发布、安装或新增 benchmark 运行权限；运行时回退保持自动。

**原生输入边界刻画（局部验证，尚未启用）。** 显式选择运行的
`tests/control_plane/test_codex_context_boundary.py` 使用真实 Codex `exec` 和
`app-server` 二进制，连接确定性的本机 loopback Responses provider。在
`0.163.0-alpha.11.1` 上，两条路径均能保留首次完整来源与一条字面温态变化，并通过
`SessionStart(compact)` 在自动压缩后的第一条模型请求中交付完整目标来源。
oracle 检查实际发出的模型输入，而非 hook 日志或 stdout。这只验证传输原语：fixture
没有实现共享子树 owner、运行 solver，也不证明打包或安装态的 App 采用。

成对负例说明了为何单靠该 hook 仍不足以构成 adapter：

- 默认 hook 输出会溢写并缩成预览。首尾要求同时出现，仍不能证明中间正文进入模型。
- 未信任的 handler 不运行。配置 hook，或给 app-server 启动器传 CLI 的信任跳过参数，
  都不是交付证据。测试只信任其隔离环境内自建的合成 handler；产品安装须保留宿主
  既有信任策略。
- compact 恢复 handler 非成功退出后，两条受测宿主仍发送下一条模型请求，且完整
  必需来源已缺失。验证将此记录为预期失败，不算恢复通过。

还在不配置 hook 的条件下验证了 app-server 的键控
`turn/start.additionalContext`，同时覆盖 `untrusted` 和 `application` 类型。
小型完整来源能进入首次及温态请求，但在自动压缩后缺失；大型来源在首次请求中
就已不完整。因此，不透明 source key 既不是完整交付回执，也不是压缩后仍保留的
常驻来源。这些案例继续记为资格验证预期失败，不证明全量回退有效，也不构成将
来源数据提升为指令的理由。当前 adapter 不得从该字段推断连续性，也不应在宿主
边界未变化时反复尝试这些不受支持的路径。

[原生 hook 契约](https://learn.chatgpt.com/docs/hooks) 提供了有用的执行顺序和显式完整
输出选项，但 hook 成功回调本身不能建立故障安全的输入边界。生产交付继续保持全量，
直到宿主集成能在 hook／输出处理后核对候选输入，并在失败、丢失或 epoch 改变时自动
替换为同 capture 的完整来源。该回退须覆盖 hook 信任、超时、截断和进程失败；不能
以新增 Agent／owner 问询或禁用 compaction 弥补。下一步须在每个调用方的既有宿主
最终输入组装边界证明这一集成，再让共享 typed 投影采用已验证的接缝。本检查点保持
一期未完成，不将合成 usage 计数解释为 token 或成果收益。

**轻量生命周期 hook 与捕获文件恢复（架构修订，待 review）。** 上述原生 hook
负例继续作为整包 hook 注入的证据，不应成为共享实现的前置阻塞。生命周期事件可以
使基线失效，而不传输大型要求正文。恢复通过普通入口完整读取不可变捕获；因此，
恢复控制消息可以先于完整来源读取，依赖该来源的业务工作仍须等完整来源读取后执行。
这修订了此前“压缩后第一条模型请求必须装入全部恢复正文”的要求，不增加 owner
问询、准入或扣账。

| Owner | 职责与边界 |
|---|---|
| 既有 quota/Turn producer | 只计算一次完整权威结果，先持久化不可变捕获，再发布展示形式。保留既有必读来源、签名、选择和结算 owner。 |
| 共享 TypeScript 上下文投影 | 绑定 Goal/Agent/session/epoch、capture 和摘要；比较已注册来源正文；每次保留新鲜控制与来源事实；选择较小的精确差分或全量，发布前按完整目标重建核对。 |
| Codex 生命周期 adapter | 观察 startup/resume/fork/compaction，使先前连续性观察失效；resume 只可保留未确认候选，复用仍须新的实际输入证据。只携带小事件和捕获恢复引用，不传整个 packet。每种真实宿主分别验证 hook 配置、信任与事件覆盖。 |
| 既有本地 capture/detail IO | 有边界地分段读取原捕获，保留完整正文，核对目标身份和摘要。预览或写文件成功不证明完整消费。恢复展示不需要新 guard 或 quota 操作。 |

**时序与失败处理。** 首次使用或连续性未知时走全量；只有同会话、同 epoch 内
完整且可观察的交付才能成为温态基线，文件存在本身不够。依据该基线生成下一展示，
同时留存当前完整捕获。生成期间 epoch 改变则丢弃待发布差分，走全量恢复；事件在
发布后到达时，小型恢复引用指向同一份当前捕获。丢失或截断的 delta 不推进确认基线。
限制差分继承深度并定期全量刷新，不积累无限 patch 链。

恢复 wrapper 捕获主 handler 错误，从保存的捕获生成有界的全文读取引用。事件观察
缺失或 wrapper 健康未知时，在下一次普通入口取消温态资格。只重试展示 IO，不重放
准入或业务效果。捕获缺失/损坏继续走既有来源恢复失败，不能伪造基线。事件检测、
完整交付与完整文件消费分别保留证据；恢复提示不能变成新增的强制用户问询门槛。

**实现与验证检查点。** 共享投影已通过既有 Turn RPC 提供 full/delta 准备和同捕获
重建，也复用于普通 quota 上下文。仅当完整正文逐字相同时省略重复的 selected-Todo
标题，保留不同标题及来源状态。真实 File/SQLite quota 测试覆盖普通 CLI、Codex App
模式、共享 Turn 来源内容、epoch 丢失、差分损坏、来源状态不变和零额外扣账。这是
产品代码提案，还不是已启用的原生温态 adapter 或安装态 App 交付。

普通 `quota should-run` 的 capture rendezvous 现已把该 codec 接到 JSON stdout，
同时支持 quota 与 `--turn-envelope`。已获资格的调用方在被保留的 `decision.json`
旁写入私有 `delivery-request.json`，沿用 `scope` 及可选的 `baseline`、`retained`
输入，并在既有摘要绑定的 release 中添加 `context_delivery: true` 显式启用。
CLI 将 Goal/Agent 绑定到实际决策，保存不可变的 `projection.json`，返回共享全量/
差分展示及完整恢复文件的路径、字节摘要。优化输入缺失、损坏、身份不符或不可用时
返回全量，不重跑准入或改变原决策成功状态。旧 release 与普通调用的输出、文件行为
保持不变。

真实 File/SQLite 子进程测试覆盖首次/温态输出、epoch 重置、损坏请求、跨 Agent
拒绝、来源保留、原 receipt 和零扣额。差分准备后仍可从精确保存的投影恢复。
这只关闭 CLI producer 接缝；rendezvous 仍位于原生输出处理之前，release 不等于
模型交付 ACK，没有合格连续性观察器的调用方不得启用温态复用。实际 solver 与
安装态 App 的生命周期消费者仍未获得资格。

恢复引用现包含可直接执行的 `quota context-read` 命令。这是既有 quota 入口下的
本地 capture IO，不另建投影或决策 owner。传入原始 `--capture-file` 和
`--capture-sha256`，依次执行每页 `next_command` 到 `eof`，按字节 offset 顺序组合
`content`。`--page-bytes` 限制 UTF-8 内容大小（256–8192 字节，默认 4096）；JSON
元数据和 Host 包装同样计入输出成本。每页返回内容前校验完整文件摘要及 JSON object。
文件被替换、offset 错误或 capture 不可用时返回错误，不交付部分上下文或产生 quota
副作用。父目录别名仍可使用；拒绝被替换为符号链接的最终文件及非普通文件。该命令
无需初始化 Goal 或 runtime，不执行 guard、准入或扣额。分页 EOF 只表示文件结尾，
不证明模型完整消费，也不授予基线复用资格。传输局部的 `context_capture_page_v1`
形状不扩展控制面生命周期状态。

原生传输 probe 使用实际共享投影、有界工具读取和很小的 `SessionStart(compact)`
引用。确定性的本机 provider 请求相应读取，不证明自主 solver 会遵循恢复路线。
保留之前整包 hook 与 fail-open 负例；从实际发出的模型输入验证首次完整读取、温态
差分、压缩、主 handler 失败和完整目标读回。成本与调用次数须包含每个恢复分段，
hook 或 packet 变小本身不等于节省。下一接入是让 Codex 生命周期 adapter 消费
既有普通 quota capture/detail 路径，覆盖一期两个调用方，不增加第二套 runner 或审批流程。

组合原生 fixture 现通过 Codex 工具执行真实 `quota should-run` CLI，使用 SQLite、
私有 rendezvous 和共享投影；在实际发出的模型输入中观察到完整分页输出后，才提供温态
基线。Codex exec 覆盖普通 quota，app-server 覆盖 Turn envelope。两条路径均在自动
压缩后恢复同一份不可变 CLI 投影，包含主恢复 handler 失败，且不触发第三次 guard 或
quota 扣额。这补齐 producer 到原生传输的测试缺口；独立 oracle 检查确定性 loopback
provider 的实际输入。来源正文、来源状态和原始 capture 独立于
delta 校验。包括恢复在内的全部分页仍属于成本边界；这些读取现执行带摘要校验的
`quota context-read`，不再自行编写文件切片。安装采用和自主成果保持未完成。

既有 CLI rendezvous 还接受可选的 `native_input` 交叉校验，字段为 `trace_bundle`、
`tool_call_id`、`capture_file`、`capture_sha256`。原生 Codex trace-v1 reader 核对
产生当前仍在执行的、由模型发起的 `exec_command` 调用的那次推理输入，是否包含精确
全量基线；要求 session 匹配、trace 顺序有效、已完成 response 包含该调用，并观察到
完整工具结果或全部摘要绑定的 context 分页。更早的推理历史、部分分页、预览、其他
消息角色、已完成调用及隐式服务端历史均不具备资格。code-mode 的 `exec_command`
子调用还须绑定唯一仍在执行的原生 cell，其模型可见 call id 与完整 JavaScript 必须
匹配当前推理 response。完整正文/分页须实际出现在模型可见 custom-tool 文本块中，
可以是正文或原生 exec 结果封装；私有子调用结果及未转发的输出不具备资格。观察缺失、
格式错误、超限或不兼容时，丢弃保留声明并自动全量；普通调用方保持不变。
Reader 只读取已经启用的私有 trace，复用既有传输大小上限；读取有界事件日志尾部，
不读整份 session，当前推理已超出该窗口时自动全量。不启用诊断或改变原生 session。
Python 持有 provider 特有的 IO；基线身份、新鲜度和精确重建仍由共享 TS
投影器校验。

该检查只能收窄调用方的保留声明，不能自行生成声明或证明未来 dispatch 的顺序。
只有每个原始字节仍可观察时，才可复用全量基线；reader 不从已存文件推断 patch 链。
原生 CLI/SQLite 组合使用该 reader 选择温态交付，loopback provider 保留为独立
oracle。当前生产接入新增 `quota context-hook --codex-context-dir <private-session-dir>`，
从 stdin 读取原生事件；普通 `quota should-run` 在既有 capture destination 上携带同一
`--codex-context-dir`，直接调用共享 projector，无需测试侧 rendezvous controller。
该 Python 模块只负责 Codex 特有的事件和私有文件 IO，不新增 capability、runner、
Goal 状态或决策 owner；TS 继续持有差分、绑定和回退语义。

调用方为每个原生 session/workspace 准备独立的私有目录，并通过原生配置与信任流程
把上述 hook 命令接到 `SessionStart`、`PreToolUse`、`PreCompact`。CLI 不安装 hook、
修改信任或启用 tracing。它核对当前 `CODEX_THREAD_ID`、工作目录和 active call，并
要求已经启用的 trace 提供完整输入；缺失、不可兼容或无法确认时自动全量。移除 opt-in
参数即可恢复原路径；普通参数和旧 rendezvous 行为保持不变。selection 命令继续携带
同一 transport 参数，不增加人工步骤。

启动、fork、压缩事件使 epoch 失效。同 session/workspace 的 resume 清除 active call，
只保留未确认候选及其 epoch；这是查找线索，不是连续性回执。新的仍在执行的调用必须
独立证明完整基线存在于实际模型输入；输入缺失或裁剪仍全量。同 session 的多个原生
bundle 只由唯一有效的当前调用/输入观察选择，不使用 mtime 或最新文件；歧义或发现
范围超限仍全量。同一 session 的恢复提示只带最新不可变
capture 的完整读取命令，跨 session 不借用旧引用。生成候选和保存最新恢复引用使用同一
私有锁；发布之后的压缩也能恢复同 capture。主 journal 写入失败时，handler 仍返回已知
恢复引用；生产发布无法记录引用时返回原全量。候选文件从不等于 ACK，完整模型输入需在
每次复用时重新核对。`context-read` 读回的完整投影可以提供基线正文证据；`1` 与 `1.0`
均属 JSON number，布尔值仍须不同。基线缺失或错配、来源权限与新鲜度规则不变。

原生 exec/quota 与 app-server/Turn 测试现执行上述生产 CLI 和 hook，而非测试侧
生命周期 controller；覆盖完整初读、温态差分、发布后自动压缩、主 handler 写入失败、
同 capture 完整补读和零额外 guard/扣额。它们仍使用确定性 loopback provider，不证明
真实模型自主遵循恢复提示或总成本收益。同一生产路径也通过原生 app-server 的
code-mode 验证 quota 与 Turn 两种视图，覆盖父 cell/子调用绑定、输出转发、压缩和
主 journal 写入失败。cell 缺失、错配或结束以及输出不完整时仍全量。这验证原生
code-mode transport，不等于已安装桌面产品采用。下述常驻恢复入口验证整个 hook 未执行/
被杀；安装采用、trace 与全部补读开销及真实 solver 成果仍须独立验证；不开新 runtime/owner
问询门，一期保持开放。

既有 benchmark worker 现通过显式 `context_delivery: "on"` heartbeat 选项消费上述生产
路径（默认 `"off"`）；EdgeBench/SForge 以 `--context-delivery on` 暴露同一选择。trial 私有 home 放置
产品 hook，并用原生 `hooks/list` 获取精确 hash，仅在该进程信任这些 hook；无关
workspace hook 仍不受信任。显式启用的私有 trace 支持输入 observer。设置失败继续
普通全量，既有 checkpoint handoff 保留原 capture 所有权。run/install/wake receipt
记录选择，设置可用不等于保留回执。默认运行不新增 hook、trace 或 trust 配置；改变
treatment 须另起 trial。产品 renderer 与恢复分页保留原 Turn、capture 和固定 CLI
路由。确定性原生测试驱动真实 worker，覆盖 quota 与 Turn 两种视图的完整初读、温态
差分、自动压缩及同 capture 完整恢复，零额外 guard/扣额。第二次真实 worker 调用和
第二个 code-mode app-server 进程现恢复同一原生 session，核验先前完整输入，直接
选择温态差分而不再初读；quota 与 Turn 两种视图均覆盖。之后的原生压缩仍恢复原始
全量 capture。benchmark 每次 wake 使用独立私有 trace 子目录，旧 wake 不进入热路径
诊断扫描。这些确定性检查验证跨唤醒传递，不证明 solver 净收益。Harbor/container 与 Codex App
安装与完整 solver/成本资格仍开放；hook 丢失恢复在下文验证。成本比较须计入原生发现、
trace IO 和所有补读。

同负载原生输入重放现将子树 codec 与读取策略分开：先在相同分页大小、转发与批量
策略下比较全量和差分，再单独比较读取策略。统计全部重复模型输入（含历史与工具
定义）、模型请求、CLI 读取和私有 trace 字节。一次初读加一次温态续跑，节省比例
可能小于长期温态序列。将更小的页合并到更少模型轮次，可能增加 CLI 调用却减少重复
输入；不能把组合收益全部归给 codec。对已捕获输入的确定性重放只验证传输成本估算，
不证明自主 solver 成果、计费 token、缓存收益或安装采用。

code-mode 调用方可以直接转发成功的 CLI stdout，避免再次序列化 exec 结果包装。
只有每一页都完整到达**最终模型输入**时才可合并有界读取；还须考虑宿主的独立上限。
单独调高请求的输出预算并不足够：原生宿主可能省略文本块，或在仍可解析的 JSON
内部截断内容。因此 JSON 解析成功和分页 EOF 都不证明传递完整。须验证连续 UTF-8
偏移及原始完整摘要；批次失败时缩小批次并重读同一原 capture，不重新准入。单次
重放不能资格化通用批次数或提高默认上限。保留截断负例，并在后续推广成本中计入恢复。

生产 quota 恢复指令与原生 resume/compact hook 现共用这份有界读取指导，包含直接转发
成功 stdout、小批量自适应读取及同 capture 重试。损坏的模型可见页不贡献保留证据，
也不会永久否决当前输入中随后完整的重读。分页大小改变后，原生 observer 仅在每个
完整区间都匹配原摘要绑定的字节、且区间并集覆盖整个 capture 时接受重叠读取。
缺口、只有预览或不同 capture 仍不足以证明保留；无需清空历史、新增 ACK 或重新准入。
组合 code-mode 测试覆盖批量 stdout、重叠分页边界、跨进程续跑、温态差分及原生压缩
恢复，包含 quota 和 Turn 两种视图。

事件 handler 完全丢失时，现有独立的**只读恢复入口**，不新增执行或准入路径。
`quota context-read --codex-context-dir DIR --goal-id GOAL --agent-id AGENT`
仅在私有 journal 匹配环境中的原生 session、当前工作目录和所请求 Goal/Agent 时，
选取最新的原 capture。它不读取当前 Goal、不执行 guard、不扣额，也不修改 journal。
缺失、跨身份或旧版未标注范围的 journal 拒绝隐式查找；已有显式摘要绑定引用仍可使用。
查找从 offset 零开始，后续每个 `next_command` 固定到原路径和 hash，避免并发发布
更新 capture 时混读。文件缺失、内容变化或符号链接继续沿既有 reader 拒绝。

实验开关现为 `--context-delivery {off,on}`，默认 `off`。复用已有声明的宿主上下文：
`--codex-app` / 等价 `codex_app_heartbeat` profile，或 benchmark worker 既有
`generic_cli` profile 加显式 `LOOPX_EXECUTION_MODE=heartbeat` 与
`LOOPX_CONTEXT_DELIVERY=on` 声明。worker 既有执行 owner 拒绝非 heartbeat 模式，
并把这些设置传递给原生工具；scheduler 声明不变。不通过进程名或环境中的原生
session 启发式认证宿主。`on` 时，跑法声明缺失、不完整、
矛盾或不受支持会在准入与 transport 写入前以非零退出码和可操作原因报错，hook 也遵守
此规则。应修正声明或改为 `off`，不新增 owner 问询或审批。受支持跑法内，输入保留证据
缺失或失效仍自动恢复同一 capture 的全量。hook、配置、已保存
包展示和重入均传递这一二态选择。目录参数或 host handoff 本身不能开启差分。关闭时
仍可回查原 capture 全量。这些调用方声明只选择受支持 adapter 路径，不授予执行权限，
也不替代实际输入保留验证。

`quota context-config --context-delivery on --codex-app` 通过 CLI 生成原生 hook/恢复配置，benchmark worker 复用同一
provider helper。Codex App 共享配置使用 `--codex-context-root` 按原生 session 隔离
journal，独立 trial home 保留 `--codex-context-dir`。只读查找不创建未知 session
目录。心跳 bootstrap 保留 root/capture 参数，使用既有每次唤醒的 `LOOPX_TURN`
绑定，不持久化固定 Turn ID。原生 hook 信任和私有实际输入诊断仍由宿主管理；缺少
诊断时使用全量。生成配置不安装文件、不改变信任、不授予执行权限。参见
[启用/读回/回滚](../../heartbeat-automation-prompt.md#opt-in-native-context-delivery)。
本段验证生成配置及原生消费，不等于真实桌面安装采用或新的成本测量。

原生上下文交付也可复用既有 checkpoint handoff 的同一 capture。checkpoint owner
先持有并核验原始完整决策，按精确摘要释放后才执行原生投影。释放丢失或拒绝不发布
优化结果；中断时保留原始全量 continuation，不再次 guard。原生中断 cursor 核验
除既有身份/capture 路由外，也绑定 context 路由。宿主侧 preflight 保留完整原始捕获，
Darwin 原生 checkpoint 限制保持不变。确定性原生测试覆盖普通持有释放、warm 复用、
hook 丢失后的压缩恢复，以及唤醒中途捕获/拒绝。续跑 worker 测试读取最新生成的
guard，在新 Turn 前通过正常入口结算独立核验的前次读回；旧历史不能替代新准入。
这只验证传输组合，不构成新的成本/结果资格，也不授予修改冻结 trial 的权限。

已保存 preflight 的交付现走公开 `quota context-project`：宿主固定原始路径/摘要和
Goal/Agent/Turn，原生工具调用同一 projector/实际输入 observer，不再次 guard 或扣额。
简短描述返回 full/delta 读取命令、可用的 target 和原始全量回退。冷启动全文仍可核验；
原生状态不确定或可选 cache 失败选择全量，来源摘要/作用域失败则在发布前拒绝。
物理文件和摘要的有界 IO 与既有 reader 复用。原生测试通过真实 checkpoint worker
跨唤醒恢复后生成的 saved-read 入口验证 warm 完整重建及零 guard 重放。原决策新鲜度、checkpoint
顺序和默认关闭时的全量预载保持；完整成本/solver 结果资格须计入新增描述及工具读取，
不能只计算 delta 字节。

新增匹配的确定性原生 worker 诊断，计入生成的 preflight、跨唤醒历史和全部分页；
两侧使用相同合成要求及默认 function-tool 读取策略，并覆盖正反执行顺序。每侧两组
双唤醒中，序列化输入 token 估算下降 35.83%，reader 调用从 72 次降为 48 次。
冷启动输入约增加 11%，收益来自 warm 复用；耗时区间重叠，未证明延迟改善。
两侧均开启诊断捕获，不据此认定相对未采集诊断时的追踪成本、自主 solver 成果或精确
计费。既有真实捕获回放是另一种负载及读取策略，分别保留。

后续以运行时源 `912d56036` 对照普通关闭 trace、普通开启 trace、显式差分并开启
trace 三组路径，每组按交叉顺序执行三次双唤醒。计入生成 preflight、恢复历史、描述、
常驻指令及全部 4096 字节串行读取。相对普通关闭 trace，差分累计序列化输入 o200k
代理值从 5,482,641 降为 3,499,312（36.17%），整请求代理值从 5,892,161 降为
3,801,600（35.48%），请求数从 114 降为 84；reader 调用从 108 降为 72，另增六次
投影调用。全部样本核验原包完整字节、独立重建、session 连续性及不变的 guard/扣额
次数。分词在计时之外完成；合成 loopback usage 不证明计费或自主 solver 成果。

默认关闭对照也揭示 IO 代价：差分每次双唤醒保留约 5.77 MB 原生诊断，未追踪时为零。
原生后台插件目录克隆占据大部分波动的总保留量；终点文件大小不等于累计写入。
延迟、CPU 和净 IO 收益仍未验收。hook 身份查询现仅在临时原生进程关闭插件功能，
避免启动无关目录；仍由原生 `hooks/list` 生成哈希，且只信任精确自有 hook。
solver 配置、默认关闭路径和模型设置保持不变。真实原生配置回读核验该隔离，重复查询
返回相同信任身份，且不发送推理请求；同一测试能够发现旧 helper 仍开启插件功能。
上述成本数据采于本次 helper 修复之前，不代表其延迟或落盘收益。

共享 codec 同时避免复制未变化或未选中的替换子树，以及对相同要求字符串重复规范化。
V8 采样定位比较、尺寸计算及复制开销；受控修改使用 31 份固定真实 capture 回放已加载
owner。四组交叉进程对照，每进程预热后测 12 次回放，prepare/recovery 平均耗时从
347.30 降至 303.06 ms（12.74%）。所有交付输出逐字节一致；重建与值隔离核验保持两份
来源不被修改。这只测单机已加载 codec，不含启动、CLI/backend IO、原生宿主、模型或
完整 Turn；不改变 packet 字节或输入 token 结果，完整成本和 solver 成果验收仍开放。

原生 adapter 改为复用既有 Core 原始 capture IO owner 的共享读取指导与投影，消除
Core 反向依赖 CLI。CLI 展示命令仍在权限 IO 之前分派；事件记录、settlement 回读与
memory ingest 保持原顺序。原生输入解析、调用绑定和摘要绑定分页核验分清职责，
不削弱生命周期或完整覆盖检查。既有旧 quota adapter 向编码与恢复两侧提供自己拥有
的字段绑定；调用方 JSON 不能指定更多可复用控制字段，未知字段仍为 fresh。这些调整
保持已测运行契约，不新增另一套投影或兼容读取入口。

显式启用的 benchmark worker 将简短恢复指令放入原生 `developer_instructions`，
独立于 SessionStart/PreToolUse 的交付。原生 code-mode 消费者也可在隔离配置中携带
同一产品 owner 生成的指令；应追加到既有宿主指令，不替换无关规则。触发条件是
上下文丢失后继续一个已捕获的决策，不是每个普通新 turn 的前置检查。指令要求依赖
该上下文的工作先读回同 capture，不增加用户问询；独立授权工作仍可继续，恢复不刷新
权限或新鲜度。首个查找页提供既有的有界读取指导。关闭功能的 worker 不增加常驻
指令。确定性原生测试在真实压缩中删除完整来源，随后跳过或杀死所有后续 hook 进程，
由真实 worker 与 code-mode 宿主通过常驻公开 CLI 入口读回全部字节，零额外 guard/扣额。
私有文件与 session 身份仍不构成保留声明；是否可复用仍独立核验实际当前输入。
后续成本比较须计入常驻指令与全部恢复读取，先前测量不包含这条新指令。此处不宣称
真实桌面安装采用或自主 solver 效果。

Capture 回归验证同时区分 settlement 绑定与 closeout：receipt 已升级但仍欠语义
重规划时，可以捕获无执行效果的 replan，且不授予交付权限；显式 pending closeout
则抑制该捕获。真实 CLI fixture 和 typed selector 现分别验证这些事实，不再从
receipt status 推断 closeout。

在可选能力模式不变的条件下，比较匹配的普通执行、重规划和等待/恢复负载，覆盖首次交付、
温态复用、reset、删除、数组重排、丢失/重复/乱序 delta、部分输出、来源/身份不匹配及迟到
要求变化。重建结果须等于完整当前投影，fresh 控制事实始终不得省略。统计首次和补读的模型输入、上下文占用、
cache/non-cache 与完整成本、读取次数、延迟和 IO，再验证后续动作与验收结果。恰好一次
效果及 required-source 拒绝保持硬约束。序列化往返或离线 delta 仅证明表示可行，不能
结算安装采用或完整成本验收。SQLite 继续承担默认本地性能资格，保留受影响支持后端的
正确性/恢复验证。本检查点不改变 runtime 默认值、预算、必读规则、权限或活动实验。

原生 Turn detail 采用现持久化真实 `run-once` producer 的完整 observation，由既有 TS
envelope owner 将 Host 请求绑定到它。真实 File/SQLite CLI 检查实际 subprocess 输入，
改变 canonical Todo 来源，验证原详情、重试和无效果 replay；模型 provider 是协议
fixture。只读规划与可选 quota capture 默认值不变，原生执行 Turn 现保留私有 capture
证据。证据缺失或不匹配拒绝再次调用 Host；legacy 缓存结果恢复与终态读回保留原 receipt。
这只关闭 producer 到 detail 的传输缺口。真实模型、打包 App/Lark、后续 authority 检查
及其余收敛矩阵仍独立验收，本切片不退役任何 Python 投影。

新的 quota 与原生 Turn capture 共用紧凑 JSON 编码，去除所存详情的排版空白，
完整保留决策值与字符串内容。原子发布、来源签名与重试仍由既有 owner 持有。
File/SQLite 原生 Host 验证覆盖新紧凑格式与历史缩进观察，包括来源改变、详情
缺失或损坏及恰好一次恢复。这是无损的传输成本缩减，不证明模型质量、完整
Turn 成本或新的收敛默认值。

共享 Host 结果适配器现在保留所提供文本，交给既有 Turn 校验器，而不通过截短使
候选通过。超限后续行动原本可能丢失末尾审批约束却仍获接受。JSON 布尔值、数字和
容器也保留原类型，交给同一校验器拒绝；false/零不能消失到
稀疏字段默认值，旧 null/省略的可选文本仍按缺省处理。明确提供的交付规模与结果
现在也保留原值及 JSON 类型，只有省略元数据时才沿用稀疏默认值。此前声明的
`surface_only`、不满足条件的 `outcome_gap` 或非法值都会变成 `outcome_progress`，
绕过既有拒绝并记入 quota 扣费。共享 prompt 使用既有交付词汇。原生 File/SQLite
测试保留合法规模历史。新材料结果也在任务验证或效果准备前复用既有结算规则：
`surface_only` 与 `outcome_gap` 返回 typed `validation_failed`，不留下 prepared
写回。此前词汇合法但不满足结算条件的结果会进入 refresh-state，抛出通用 CLI
错误，留下反复遭遇同一拒绝的 in-progress prepared 写回。Host 合同不携带 typed
blocker observation；普通 CLI 的阻塞结算保持原行为。历史缓存结果仍走 provider
读回／恢复，覆盖 provider 返回 unknown 后读到 committed receipt，不重启 Host 或
重复写回；此处没有新增结算策略。
File/SQLite 原生 CLI
和实际 DSH 子进程验证任务校验、写回、扣费、ACK 前的拒绝、失败结果重放，以及
修正后的新 Turn 只结算一次。边界长度的合法结果保持原行为。这只证明结果传输
正确性，不证明模型采用、撤销 Host 已执行工作或关闭原三批收敛验收。

Turn 完成写入现将已准入 Todo 全文的精确摘要交给既有原子终态 owner。正文要求
变化拒绝完成，单独备注或其他 Todo 变化仍有效；provider CAS 阻止并发替换，历史
receipt 恢复先于当前来源检查。已保存 Turn 缺少全文来源证明时拒绝新完成写入，
已接受的旧 receipt 则保留原指纹并优先恢复。真实 File/SQLite CLI 和共享 File/SQLite/PostgreSQL
conformance 覆盖拒绝与恢复。最终 no-followup 保留既有结算顺序：终态拒绝保留此前
已经提交的 writeback/spend receipt。本批只证明任务正文的完成安全性，不证明整个
结算的来源原子性、安装态宿主采用、真实模型或其余收敛验收完成。

首个普通 CLI 整合将完全相同的
`heartbeat_recommendation.blocked_priority_fallback` 副本替换为
`blocked_priority_fallback_ref="$.blocked_priority_fallback"`。根字段的完整事实、
通知、执行义务和原始 capture 保留；不同副本继续保留，包括 Boolean 与数字的差异。
既有 `--include-detail agent-todos` 视图保留原副本。真实 File/SQLite CLI 覆盖同次
捕获的动作维度及无扣费重放。原始 source hash 本就区分不同渲染；本批不改变签名
覆盖，也不让渲染视图替代原始 capture。
旧字段访问保留在既有 packet owner；CLI 调用其投影函数，不增加待迁移的旧字段消费者。

验收上下文 adapter 现在对已选定工作复用既有精确 Todo 读取，保留完整 Goal 验收依据
和累计 outcome，避免再次搬运无关任务的关联观察及选定 Todo 正文。真实 File/SQLite
CLI 与 DSH 协议输入覆盖已配置检查、仅声明 outcome 两种情形；无选定工作的读取
仍返回原列表。这只验证局部 IO 边界，不能替代真实模型收益、安装采用或整轮成本。

原生验收／capture 矩阵还覆盖捕获后新增或修改工作、未来恢复条件以及显式移除检查。
先验证并结算原合成工作，再进入新的 managed Turn：当前准入拒绝未关联／失效工作
和待恢复任务；移除检查不会产生新的 owner 问询。累计要求及其来源完整保留，当前
工作状态及其派生依据摘要可随实际变化更新。原详情仍可逐字节读取，不再次准入或
改变 authority revision。这些案例验证既有边界，不确立新的执行默认，也不结清
三条完整收敛验收。

Goal 来源上下文现复用原 writer 的严格归属 parser，分离显式标记的生成 Todo 区域，
通过共享 typed context owner 原样传递其余全部文字，附带完整文件摘要与被分离区域
的来源记录。未标记／混合文档保留渐进式全文读取，归属错误拒绝。此处改变默认上下文
交付，不改变 canonical Todo authority、源文件或累计验收。File/SQLite 原生 guard
与签名 DSH 协议输入验证这一来源边界；模型采用、安装态 App/Lark、完整 Turn 成本
及三条收敛验收仍未完成。任意大段正文完整保留，不做截断。

下一步先调查获准真实 Goal 的活跃工作、peer/replan、等待/恢复和可选能力形态，
将每条保留义务映射到共享 typed owner 与实际消费者，再完成一族调用方迁移，最后
退役其旧投影。用同次捕获决策比较 full/thin heartbeat 与 Turn，将必需详情补读、
token、序列化/IO、延迟及资源增长一并计入。此次移除副本的字节节省只证明该渲染
差异；模型质量、完整成本、安装态 App/Lark 采用和迁移默认仍需独立证据。真实私有
来源不进入公开 fixture 或 checkpoint。

此前成本切片已复现回归预算缺口，不能归因于 fixture alias，也不授权删掉
必需上下文。`aa87cc019` 与 `fc411c878` 使用相同公共 CLI fixture、临时 alias
和命令参数，实际 JSON stdout 测量如下：

| 公共负载／入口 | base 字符 | candidate 字符 | base 紧凑 JSON | candidate 紧凑 JSON |
|---|---:|---:|---:|---:|
| 36 Todo／1 Agent／12 run，`turn plan` | 16,115 | 17,403 | 12,112 | 13,145 |
| 1 Todo／1 Agent／1 run，开启 multi-subagent 的 `quota should-run --turn-envelope` | 10,990 | 11,661 | 8,892 | 9,464 |
| 36 Todo／1 Agent／12 run，`heartbeat-prompt --thin` | 3,122 | 3,122 | 3,069 | 3,069 |

crowded Turn 的增长包括 envelope 内 484 个紧凑字符，以及新增 hook-dispatch
诊断的 510 个紧凑字符。envelope 现在说明哪些 hook 观察到空上下文、清除其缓存，
并区分已满足的 pre-work 读取和后续 action 的新鲜来源要求；这些指令和签名观测
有实际决策价值。hook 诊断另有副作用披露职责，移动或删除前须核验真实消费者。
pretty-print 开销单独计量，不等于 token、延迟或模型质量结果。

按原检查，crowded `turn plan` 超过 16,000 字符上限，开启
multi-subagent 的 runner 超过 9,000 字符 envelope 上限。后者因嵌套 fixture
路径在 base/candidate 分别输出 11,291／11,961 字符，与表中的路径负载不同。
历史 base 已失败；原检查下的失败结果保持。

当前源码资格另列。已合并的
`a855a547e7a3fc4f9b5c3e02e1ea73cf0f591f95` 校准既有回归检查，同口径负载与
有界余量见[预算决策指南](../../development/testing-and-quality.md#budget-failure-decisions)。
在 `0cd547b0c442a5c0ec7f608827b02dce7aeb0aac` 独立重跑真实 CLI，candidate
执行全部预算检查，96 行均通过；与不可变
`2244b96f1e2e5c90bef43ae4c140c0994bfcc07a` 的既有语义差分两侧各 96 行，
无 candidate-only 行或待复核变化。fixture 保留 1/36/18 个 Todo、1/12/12 个
run、完整命令路由、开启 multi-subagent 及 blocking gate。crowded Turn 输出
16,250 字符，上限 17,000；开启后的 envelope 为 11,426，上限 12,000；thin
JSON 仍为 3,122。既有 runner 测试仍拒绝真实 stdout 膨胀，不改写路径或缩减负载。

消费者审计保留两种 hook 视图：typed envelope 签名覆盖不可用上下文、hook 坐标
和依赖动作的新鲜度；live Turn adapter 另读 hook 结果，通过 `effects` 与
`boundary` 披露本机状态写入。把诊断当重复上下文删除会丢失副作用披露。紧凑
序列化仅单列格式开销节省，不证明新的宿主传输资格。当前源码回归覆盖 File/SQLite
偏好、空／失败 hook 的缓存失效、可选能力关闭隔离、所选要求与后续读取。本批
关闭有界源码预算缺口；M7.4 仍未完成，packaged host 采用、模型 token／IO／延迟／
结果证据及可逆 transport 默认决策继续保留。执行 quota、wire 限制和冻结的
promotion 条件不变，不声称运行成本下降。

#7161 取消旧 `protocol_action_packet` reader 格式支持，删除字段选择、summary
重建、residue／opaque fallback 及其传输输入。Effect／CLI／Envelope／Host
在转换或使用签名前明确拒绝旧包及 capsule witness。冻结历史记录保持原样；
不带旧包的 v0 签名与执行检查语义不变。本项有界清理替代旧兼容承诺，不关闭
M7.4 或 benchmark／Codex App 上下文传输验收。见
[格式边界](../../reference/protocols/protocol-action-packet-decision-v0.md)。

不引入通用 executor，也不降低验收门槛来让短包通过。保留未满足要求，分别记录
传输等价、安装态宿主采用和有效模型结果。见
[当前 envelope 契约](../../reference/protocols/turn-envelope-v0.md#optional-memory-participation)。

- 通用共享 executor 被有意保留为空。当前 adapter 共享 plan/receipt algebra，却拥有不同的执行边界，因此 M7.3 应以 no-follow-up 关闭，而不是用推测性 framework 填充。
- 常规 LoopX 核心路径仍需逐条做有界采用判断。只有当路径包含多步 external effect、单一稳定 identity、durable receipt、replay 要求，并且变更能删除重复 settlement truth 时，才应该使用这套 algebra。
- Race/CAS qualification 推迟到真实并发执行入口出现后；同步 adapter 本身不足以证明需要并发基础设施或测试。
- M7.4 仍是 evidence-driven replacement gate，而不是把每个 Todo、gate、monitor、scheduler 或 replan rule 都改成 Kleisli arrow 的要求。

### 核心路径采用矩阵

| 核心路径 | 决策 | 边界 |
|---|---|---|
| Codex App / CLI 常规 turn closeout | 已采用 | core plan/receipt algebra；quota adapter 拥有 CLI binding 和 durable settlement check |
| 隔离 turn-driver closeout | 已采用 | 共享同一 algebra；local callback executor 与 journal 仍归 turn driver 所有 |
| Task-lease acquire | 有界采用 | validation 与 durable write 共享 core algebra；eligibility、conflict、locking、CAS 和 persistence 仍归 task lease 所有 |
| Turn continuation | 作为 consumer 采用 | pure controller 读取已提交 receipt chain，不执行 host effect |
| Todo completion、`refresh-state`、quota spend | 有界采用 | 普通 completion 保持 Todo-owned；refresh/spend 组成基础 settlement，final `no_followup` 是 conditional post-spend closeout |
| Goal vision 与 replan checkpoint | 选择性 typed qualification | causal evidence 与完成链 checkpoint 是共享 invariant；vision policy 不进入 settlement executor |
| Capability gate、user gate、monitor selection | 保持 domain-local | 除非未来证明存在重复 external-effect settlement，否则它们仍是 decision state machine |
| Scheduler apply、ACK、cadence、failure hint | settlement 之外 | host-owned effect 保持数据化，不隐藏到 agent executor 后面 |
| Bootstrap 与本地 scheduler command rendering | 只复用 read model | `EffectProgram` 可以读取 ordered steps；没有可删除的重复 truth 就不迁移 runtime |
| 并发/racing settlement | 推迟 | 只有真实 concurrent caller 与 authority boundary 出现后才加入 race/CAS 行为 |

### 何时泛化

只有至少两个真实 runtime path 同时共享 plan/receipt 语义和执行所有权时，才泛化执行层。当前 adapter 证明了共享 algebra，却反证了共享 executor：一条路径跨越 CLI/host boundary，一条拥有 in-process callback，另一条把原子 persistence 委托给 task-lease bounded context。Packet 相似或共同的 `bind` 方法不能覆盖这些边界。

在此之前，把抽象保持为文档化视角，并增加证明每个 packet 无损映射的测试。这可以避免构建一个没有 runtime 使用的通用 `Effect` 框架。

### 替换状态

R1、R2、R3 和 R5 已完成：

- R1 bootstrap guided rendering 通过 `EffectProgram`（#2955）；
- R2 turn executor result-kind resolution 通过 `interpret_turn_result_packet`（#2956）；
- R3 Codex CLI scheduler command set 通过 `EffectProgram`（#2957）；
- R5 quota should-run TurnEnvelope 通过 `interpret_quota_should_run_packet`。

R4 原来的 generic-executor 提案以 no-follow-up 关闭。只有当另一个真实 caller 能在不跨 authority boundary 的前提下删除重复编排时，才重新开启。

### 定性改进计划

当前 effect 抽象是 read lens 加三个小的 runtime replacement。只有以下全部为真时，M6 才能被称为 mostly complete：

1. 热模块缩小到有界大小：
   - `loopx/quota.py` 低于 2000 行（当前 1043）；
   - `loopx/status.py` 低于 2000 行；
   - `loopx/heartbeat_prompt.py` 低于 1200 行。
2. `loopx quota should-run` 通过有界的 `should_run` decision module 构建，`loopx.quota.build_quota_should_run` 成为 thin compatibility wrapper。
3. `EffectTurn` 和 `EffectProgram` 被 CLI quota、turn driver 和 bootstrap construction 消费，而不只是测试和 renderer。
4. 没有 effect 抽象保持 test-only。
5. Maintainability、import-graph、CLI output 和 hot-path interface ratchets 无新增 exception 通过。
6. Doubao/model-behavior shadow qualification 覆盖变更后的 agent-facing packets。

阶段：

- Q1：停止 milestone claims，保持 M6 in progress。
- Q2：刻画热模块并为 `quota.py`、`status.py`、`heartbeat_prompt.py` 捕获 parity fixtures。
- Q3：把 quota `should-run` decision 和 packet builder 抽取到有界模块。已完成：`should_run.py` entry decision（#2963）、`should_run_prepare.py` preparation chain（#2964）、`should_run_packet.py` route/packet assembly（#2965）。
- Q4：把 status read models、collection 和 presentation 抽取到有界模块。已完成：bounded status projections（#2967-#2978）；`status.py` 1392。
- Q5：把 heartbeat prompt builders 抽取到有界模块。已完成：bounded heartbeat task body/builder/support modules（#2979/#2980/#2982）；`heartbeat_prompt.py` 159。
- Q6：让 CLI quota、turn driver 和 bootstrap construction 消费 `EffectTurn` / `EffectProgram`。已完成：quota should-run TurnEnvelope 消费 `interpret_quota_should_run_packet`（#2983）；turn driver 和 bootstrap 消费 `interpret_turn_result_packet` / `effect_program_from_ordered_steps`。
- Q7：为每个抽取增加 quality gates 和聚焦测试。已完成：RFC module budgets 在 `module_metric_baseline.json` 中 ratchet，聚焦 M6 quality-gate pytest 固定热模块上限和 runtime `EffectTurn` 消费（#2984）。
- Q8：gate 通过后再重新评估 M6。已完成：见下方 audit evidence。

### M6 完成证据

- 热模块行数：`loopx/quota.py` 1049、`loopx/status.py` 1392、`loopx/heartbeat_prompt.py` 159。
- Maintainability ratchet：`ok=true`，无 unreviewed findings，无 stale exceptions。
- 聚焦 M6 audit suite：172 通过，覆盖 quota parity、status re-export、heartbeat support、effect interpreter/program/turn families、CLI output budget/differential、import boundaries、model-behavior/Doubao shadow 和 turn driver/executor。
- `loopx canary quality-audit`：`ready=true`、`gap_count=0`、`drift_count=0`。

### M7：Effect Program Runtime

M6 让 effect lens 被 runtime 消费，但仍然偏描述性：packet builders 先计算 decision，再映射到 `EffectTurn`。M7 不能因此让每个状态族实现同一个 protocol。它必须首先证明 typed effect runtime 移除一个真实的编排 split-brain。

M7.0：盘点真实多步 runtime 候选。选中的核心是从稳定 quota decision 出发，经过验证 writeback 和 exactly-once spend 的 normal-turn settlement。它有两个真实 adapter：默认 Codex App interaction path 和隔离 turn driver。Scheduler apply 和 ACK 保持为 delegated host handoffs。Guided bootstrap 未被选中，因为部分 ordered steps 属于 model、user 或 host；quota-to-host scheduling 未被选中，因为 LoopX 无法自行结算外部自动化 mutation。

M7.1：在添加 protocol 前刻画选中的 vertical slice。为合法与非法 transition、部分执行、重试、取消、权限拒绝、预算拒绝和结算捕获 parity fixtures。durable transfer 必须包含 writeback 和 scheduler handoff 时的 cancellation、host execution 和 quota spend 时的 permission denial，以及 writeback 后的 spend-budget rejection。该阶段保留当前 runtime behavior，包括 M7.2 预期修复的任何 split projection。还必须刻画默认 Codex App selection-drift seam：选中 Todo 完成后，writeback 推进 frontier 时，spend 仍必须结算原始 effect identity，而不是绑定到新选中的 successor。

M7.2：用一个 typed plan/receipt algebra 替换核心 settlement truth。plan step 必须携带稳定 kind、owner、precondition、idempotency identity 和 expected receipt。默认 Codex App path 与隔离 turn driver 把 validation、durable writeback、quota spend 和 conditional terminal closeout 绑定到原始 quota-turn effect identity。普通 successor completion 可以在 settlement 前推进 Todo frontier；final `no_followup` 只有在 matching writeback/spend receipt 后才提交，不能增加 terminal guard 例外。每个 replacement PR 都必须删除对应的 manual command 或 settlement truth。Raw mappings 和 free-form CLI commands 可以保留为 compatibility payloads，但不是语义执行合同。组合必须满足上文定义的 identity、associativity、short-circuit、replay 和 ordering 性质，保持 cancellation、permission denial 和 budget rejection 可区分，并让 scheduler apply 或 ACK 留在 agent-owned settlement boundary 之外。

M7.3：在两个 M7.2 adapter 都消费经过验证的 plan/receipt 语义后，比较它们的执行所有权。2026-08-21 的 cutover qualification 发现，settlement identity、bind/short-circuit、replay seeding、next-action selection 与 commit reduction 仍在 adapter 间重复。因此重新打开 M7.3，引入一个 bounded TypeScript Effect runtime。Runtime 拥有共享 algebra 和第一个内部 effect——atomic Turn-journal checkpoint。它的 server 只是临时 Python-to-TypeScript transport；一个静态 typed handler registry 把粗粒度 transaction 路由给 domain owner。它不是通用组合框架，也不会把 model、user、host scheduler、credential 或第三方 authority 藏到万能 executor 后面。每条被替代的 Python 语义路径都必须在同一 cutover PR 删除。

**2026-10-01 identity 边界切片：** `effect_program.ts` 统一解码可执行 Todo／replan
身份，Turn settlement 与 journal 验证复用这一 owner。内部判别联合排除双 target
与字段修改；可执行值排除 `unbound`。支持的 v0、scoped v1 和无 schema adapter
输入保持 effect ID。非字符串 ID、不支持的声明版本与矛盾 binding metadata 在
provider dispatch 或 receipt replay 前失败。Journal reader 删除独立的 v1 binding
比较。证据复用 effect-program、settlement-parity、journal-effect 与 inspect-journal
CLI 测试，包含真实 File 回读。此处只验收 identity 边界；跨域 crash／lease 恢复及
完整 provider conformance 仍需单独验收。

2026-10-01 的 Turn 恢复迁移把 provider 返回值、completion 结果与回读分类收进 TypeScript
Turn owner。每次 reduction 只授权 prepare/execute、resolve、
execute-prepared、checkpoint 或 abort-prepared 中的一步；确认 checkpoint
已持久化后才能进入下一个 provider。非法 completion 或显式不匹配的 payload
effect ref 在推进 journal 前被拦截。Unknown 保留 prepared；确认 absent 后才
允许沿用同一 ref 执行。既有 failed-turn 显式重试 gate 保持不变。

临时 request/reduction 合同升至 v1，v0 caller 在 provider 授权前被拒绝。
升级或回滚时须让 Python interpreter 和 TypeScript reducer 一起变化。
持久化 journal／receipt schema、effect identity、公开 CLI 参数及省略显式 ref
的旧 provider payload 保持兼容。既有 Turn 局部 provider step／resolution
词表迁入对应模块，内部 action union 扩展，不新增共享状态词表。
首次两 provider 结算由三次 RPC 变成五次（preflight，加每步返回值准入及
持久化确认）；完整重放仍为一次。这是 Python 仍负责 IO 时明确接受的正确性成本，
不是延迟优化；PR 证据需比较相同 base/head 工作负载。验证范围见
[有界恢复 checkpoint](composable-state-machines-recovery-verification-v0.zh-CN.md#turn-结算资格范围)。

M7.4：只有在移除重复知识并切换真实生产 caller 时，才一次扩展一个 bounded 状态族。Todo、monitor、capability、scheduler 和 gate 状态机保留自己的 domain transition invariants。它们迁移后可以通过同一个 managed runtime 执行，但不能仅仅因为 packet 字段相似就移到一个通用状态协议后面。CLI 原生迁到 TypeScript 后，CLI-only 在进程内 import kernel；daemon 只在 App/多 client 共享 authority 时可选保留，而不是每个状态族一个必选 server。

#3208 的 replan semantic-exit 修复明确不是候选：`refresh-state` 已经会重新推导当前 obligation 并记录 typed semantic ACK，实际缺陷是 goal-frontier 中一个额外的 settlement 条件在 acceptance gaps 仍存在时忽略了合法的 non-successor ACK。这是 domain-local reducer/ACK invariant，不是第二个 multi-step executor，应继续由 replan/goal-frontier owner 持有。只有第二个真实 runtime 场景（例如具有相同 plan/receipt lifecycle 的 quota/status read ACK）出现，并且能在两个 adapter 间删除重复编排时，才重新评估 Effect Program 迁移。

因此，之前的 R5-R9 列表不是实施队列：

- 共享 `EffectInterpreter` protocol 推迟到 M7.3；
- packet-before-view ordering 被一个 canonical decision-plan source 取代；
- guided bootstrap 仍是候选，并受 host-boundary review 约束；
- turn closeout 是另一个候选，可能是更好的第一个 vertical slice；并且
- family-wide alignment 被 M7.4 的 duplicate-knowledge gate 取代。

M7 只有在真实 vertical slice 满足 Product Outcome Contract、旧路径被移除、且第二个 caller 为保留的抽象提供证据时才完成。

### Replacement-First 规则

每个 M6 代码变更都必须替换现有真实 runtime call path，而不是增加并行的未使用抽象。

- 替换前：为现有路径捕获 parity fixture 或 smoke。
- 替换：让 runtime 读写通过 `EffectTurn` / `EffectProgram`。
- 替换后：删除旧路径；只有当真实外部 import 或持久化合同需要时，才保留 compatibility wrapper。
- 仅测试性增加不算 M6 progress。

示例替换：

- `bootstrap_command_pack` 应在 rendering 或 validation 前通过 `effect_program_from_ordered_steps` 读取 `ordered_steps`；
- `turn_driver/executor` 应在提交 receipt 前通过 `interpret_turn_result_packet` 派生 result status 和 next phase。

## 语义控制与执行归属

可选的 [controller/executor profile](controller-executor-unified-contract-v0.zh-CN.md)
在既有 owner 内分配模型职责：controller 是唯一面向 Core 的模型客户端，executor 在
host 强制范围内交流自然语言工作与产物。它不同于确定性的 Turn Loop Controller。
同一 Agent 可绑定这两个角色，不增加竞争业务 executor 或第二份权限。单会话仍可用；
角色绑定、结果验证和完整成本资格先于默认提升。

LoopX 的语义控制面跨 Turn、Agent 与 runtime 保持工作含义：intent、ownership、
dependency、authority、evidence 和 continuation。执行责任覆盖它实际拥有的 typed
transaction 与 settlement step。Agent／capability 推理提出领域结果，kernel 核验
绑定、准入和生命周期义务；领域 verifier 与用户仍判断成果内容。

| 边界 | Owner | 可观察承诺 |
| --- | --- | --- |
| 领域判断 | Agent 与 capability | 带作用域证据的动作／结果提案 |
| 控制决策 | 既有 typed domain kernel | 从显式 facts 得出的合法下一步与所需证明 |
| 内部执行 | 所属 transaction／effect adapter | 持久状态转换与绑定 receipt |
| 外部执行 | Host／provider | 实际 model／tool／environment effect 与事实回读 |
| 展示 | Read-model owner | 带新鲜度边界的证据状态与可用动作 |

共享 algebra 不转移执行权。Host continuation、sandbox snapshot、model/tool
拦截和外部回滚需要各自受支持的 runtime 合同；effect plan 或 transcript 不证明
这些能力。不新增通用 executor 或第二个 permission owner。

## 决策回放、效果恢复与模拟

| 操作 | 输入与承诺 | 边界 |
| --- | --- | --- |
| 纯决策回放 | 固定可信 facts、command 与规则版本，重现决策 | 不执行 effect、不恢复当前权限 |
| 已提交效果恢复 | 相同逻辑身份与已验证持久 receipt，跳过已提交步骤并接续所属协议 | 不证明 unknown 外部效果没有发生 |
| 假设模拟 | 显式替换 facts，用受控 interpreter 比较可能决策 | 不证明真实 provider 或模型会产生该结果 |

在既有 receipt 边界记录版本、identity、顺序和相关 outcome。不能重跑模型后把新
输出称为历史回放。Unknown、permission denial、cancellation、budget rejection
和 committed success 保持各自恢复含义。取消不抹掉 in-flight 外部效果。Retry
保留身份并遵守 provider 保证；不支持的 readback 仍是 unknown。

Adapter conformance 比较可观察 effect 顺序、短路点、receipt、权限拒绝与恢复，
不能只比较返回值。Identity／associativity 适用于同一有序程序的重新分组，不允许
重排或投机并行。维持既有不建设万能 executor 的决定，直到真实共享权威边界能
证明需要。跨域序列采用[组合验证](composable-state-machines-recovery-verification-v0.zh-CN.md)，
受保护外部效果采用[provider acceptance](provider-effect-acceptance-v0.zh-CN.md)；
两者都不意味着跨系统 exactly-once。

## 把状态机当作解释表

不要把状态机教成一串 enum values，而是把每个状态机教成一张解释表：

```text
Input effect | Interpreter | Decision | Observation | Next effect
```

以 monitor scheduling 为例：

```text
Monitor cadence or due horizon
  -> scheduler interpreter
  -> host RRULE / initial interval
  -> scheduler_hint packet
  -> next heartbeat or monitor poll
```

这保留了现有状态机，同时让它们的目的可见。

## 里程碑

### M0：RFC 与 Lecture 0

**目标**：发布本 RFC，并在任何状态机细节之前增加一讲来讲述这个故事。

步骤：

1. 合并本 RFC。
2. 在 `docs/development/control-plane-course/` 增加 `Lecture 0: Harness Is the Effectful Program`。
3. 重写 `docs/product/core-control-plane/state-machine.md`，为每个状态族加入 interpretation-table section。
4. 更新 `docs/README.md` 和课程导航，指向本 RFC。

验收标准：

- 新贡献者可以用一段话和标准循环形状解释 LoopX。
- 每份现有状态机文档都链接回 interpretation-table 模式。
- 没有 runtime behavior 变更。

### M1：Canonical Packet 示例

**目标**：选择 `quota should-run` 作为 canonical 示例，并让四个语义槽在文档和 smoke 中可见。

步骤：

1. 在 `docs/reference/effect-interpreter-packet.md` 增加描述 `quota should-run` 四个槽的 public-safe 文档 section。
2. 增加聚焦 pytest 或 smoke，断言 raw inputs 到 canonical interpretation fields 的映射。
3. 保持现有 payload 字段不变。

验收标准：

- 读者能从一个真实 packet 的 effect request 追踪到 observation。
- 没有 CLI output budget regression。
- 没有真实 caller 时不引入新 runtime contract。

### M1.5：组合视角

**目标**：让 around 语义在 canonical packet 视角中可见。

步骤：

1. 在本 RFC 和 Lecture 1 中记录三层组合与 data-encoded handler。
2. 扩展 `EffectTurn` 增加 `next_effect`，让四个语义槽不仅在 prose 中、也在代码中表示。
3. 增加聚焦测试，证明 capability gate 是结构化 around decision：它会短路、重写 next effect，并保持 permission 语义可见。
4. 在公开文档中引用公开 Tool Calling 与 Function Composition 来源。绝不引用内部讲座材料。

验收标准：

- 读者能回答真实 packet 的 `next_effect` 编码在哪里。
- 代码视角覆盖 `effect_request`、`interpretation`、`observation` 和 `next_effect`。
- 没有 runtime behavior 变更。

### M2：Bounded Context 对齐

**目标**：把现有重构与 effect-interpreter boundary 对齐。

步骤：

1. 继续把 `status.py`、`quota.py` 和 `goal_frontier.py` 拆分为 read-model、projection 和 decision modules。
2. 用 loop 术语命名边界：
   - read model = 当前 `A`（state）；
   - projection = observation；
   - decision = effect interpreter。
3. 为现有 public imports 保持 re-export compatibility。
4. 在至少两个真实 caller 需要同一 envelope 之前，不创建通用 effect abstraction。

验收标准：

- Module names 和 docstrings 明确 effect-interpreter 角色。
- Public import compatibility tests 保持绿色。
- Maintainability 和 line-budget smokes 保持绿色。

### M3：聚焦测试族

**目标**：把大型控制面 smoke 按 effect family 转换为聚焦 pytest modules。

步骤：

1. 为以下内容创建聚焦 pytest modules：
   - work-lane contract；
   - quota decision；
   - scheduler/monitor interpretation；
   - state-machine interpretation tables。
2. 保留证明 CLI 仍可工作的 thin end-to-end smokes。
3. 为 failure、cancellation、gate 和 observation writeback 路径增加 regression tests。

验收标准：

- 每个 effect family 都有聚焦 pytest module。
- 在其聚焦替代通过前，不删除大型 smoke。
- Full public smoke suite 保持绿色。

### M4：架构文档

**目标**：让架构和产品文档使用同一个故事。

步骤：

1. 围绕标准循环重构 `docs/architecture.md`。
2. 更新 control-plane course，让每讲引用同一个 `effect_request -> interpretation -> observation` 流程。
3. 在 README 产品语言仍只说 "state machine" 而未解释 interpretation 角色处更新。

验收标准：

- 公开文档不再把 LoopX 呈现为一堆无关状态机。
- 技术读者能在每个文档化工作流中识别 loop boundary、effect request、interpreter 和 observation。

### M5：稳态评审

**目标**：让 RFC 成为 living contract。

步骤：

1. 增加 canary smoke 或 docs smoke，检查 canonical packet 文档存在。
2. 对照四个语义槽评审新状态机和 packet fields。
3. 当新 effect family 需要新 canonical slot 时更新本 RFC。

验收标准：

- 维护者文档和课程材料引用本 RFC。
- 新控制面功能说明它们解释哪个 effect。

### M6：通用 Effect-Program 抽象

**目标**：在没有投机性框架构建的前提下，从 quota-only read lens 走向共享 effect-program 抽象。

步骤：

1. 增加第二个真实 interpreter，例如 `interpret_turn_result_packet` 或 `interpret_status_packet`，并用聚焦测试证明 `EffectTurn` 对该 family 也无损。
2. 把 packet interpretation 保持为 read-model seam。只有当两个执行路径需要相同 plan/receipt 语义时，才抽取共享 runtime interpreter 或 executor protocol。暂不增加 registry 或通用组合框架。
3. 不再把 replan 当作通用 read-and-ACK 的先例。replan evidence 由 host 投影为 context，精确绑定当前 obligation 的 runnable-successor Todo 或 typed progress 写入才是语义 receipt。在第二个 runtime caller 同时需要相同 effect identity、freshness、原子状态转移和 turn boundary 之前，该 transition 继续归 replan 领域所有。只有真实第二调用方出现时才抽取最小 observation/transition receipt；不要恢复手工 evidence-read ACK 仪式。
4. 为 `EffectNext` 增加 `execution_mode`，用聚焦测试文档化 `serial` / `parallel` / `interleaved` 语义。
5. 当一个 owner 能执行并结算多个步骤时，引入 data-encoded ordered effect program shape 和真实 executor seam。在选中第一个 slice 前，qualify turn closeout、guided bootstrap 和 quota-to-host scheduling；现有 ordered list 本身不建立可执行 authority boundary。
6. 在每一个 interpreter 中保持 failure、cancellation、permission 和 budget 语义结构化。不引入 catch-all wrapper。

验收标准：

- 至少两个 packet family 产生 `EffectTurn`。
- Runtime code（不只是 tests）消费共享 shape。
- `next_effect` 能用显式 execution mode 表达有序 effect program。
- 共享 observation/transition receipt contract 至少有两个 runtime caller；只有一条领域 transition 时继续由领域 owner 持有。
- 在出现第二个 runtime caller 之前，不增加通用 `Effect` monad、registry 或 middleware framework。

## 测试策略

测试应按 effect family 组织，而不是按源文件大小：

```text
effect_request -> interpretation -> observation -> next_effect
```

每个聚焦 pytest module 都应覆盖：

- positive routing；
- gate 与 capability decisions；
- failure 与 cancellation；
- observation writeback；
- public imports 兼容性。

大型 smoke 只保留为 thin end-to-end checks。

### Runtime Replacement 测试

对每个 runtime replacement：

- focused pytest 覆盖新 seam 并与旧路径 parity；
- thin public smoke 练习真实 CLI 或 host path；
- CLI output budget regression 保持绿色；
- model-behavior / Doubao shadow qualification 覆盖 agent-facing packet 变更；
- canary premerge 包含 `core-control-plane` 和 `canary-runner` profiles。

## Non-Goals

- 不把全部状态机合并成一个巨型 enum。
- 在没有两个真实 caller 时，不创建通用 `Effect` 抽象。
- 不把 test-only lenses 算作 M6 progress；每个 M6 变更必须替换真实 runtime call path。
- 当 `quota.py`、`status.py` 或 `heartbeat_prompt.py` 仍然过大，或 effect 抽象仍 test-only 时，不把 M6 标记为 mostly complete。
- 在出现第二个 interpreter 和真实 executor caller 前，不把当前 `EffectTurn` 视角当作通用 runtime 抽象。
- 不为命名而重写 `quota should-run`。
- 不用 effect-runtime 泛化替代 final-goal acceptance、evidence 或 replan。
- 不因为 guided bootstrap 的 ordered steps 可以渲染成 `EffectProgram` 就让它可执行；保留 model、user 和 host ownership boundary。
- 在没有证明重复 transition knowledge 并删除它之前，不让 Todo、monitor 和 gate 族对齐到共享 protocol 后。
- 没有迁移窗口时，不删除现有 public compatibility routes。

## 风险

- 命名漂移：可能把 "effect" 当装饰而不改变语义。缓解：每个 RFC milestone 必须产生真实文档或测试变更。
- 过度抽象：通用 effect envelope 可能变成未使用 scaffolding。缓解：只有第二个 caller 需要时才增加共享 envelope。
- 装饰性命名：文档说 "effect program"，runtime 仍只传 CLI strings。缓解：M6 要求在 RFC 声称通用抽象前出现第二个 interpreter 和真实 runtime replacement。
- 测试 churn：过快转换大型 smoke 会降低 e2e confidence。缓解：在聚焦测试覆盖相同行为前保留 thin e2e。
- Goal/effect 混淆：可靠 executor 可能持续执行错误 milestone。缓解：把 goal-path evidence 和 effect settlement 作为独立合同，并在 milestone closeout 时同时要求两者。
- Executor boundary 越界：ordered steps 可能属于不同 actor。缓解：只有在 owner 和 receipt boundaries 明确后才选择第一个 vertical slice。

## 开放问题

- `effect_interpretation` 应该成为 hot quota packet 的一等字段，还是只作为文档化视角？
- 每个 capability 应该拥有自己的 interpretation table，还是 table 保留在中心文档？
- 何时应把新状态机视为新 effect family？
- 哪个 packet family 应该成为第二个真实 `EffectTurn` interpreter：turn result、status 还是 monitor poll？
- 何时 `next_effect` 应从扁平 CLI tuple 变成带 `execution_mode` 的有序 effect program？
- 哪个候选以最窄 authority boundary 移除最多重复编排：turn closeout、guided bootstrap 还是 quota-to-host scheduling？
- 什么稳定 effect identity 和 receipt 能让该路径在部分执行后恢复，而不会重复 ACK、writeback、spend 或外部 action？
- 哪个第二个 runtime caller 需要相同、经过验证的 plan/receipt 语义？
- 何时 `EffectProgram` 应从 host-driven 变成 runtime-owned，哪些 steps 必须保持 model、user 或 host-owned？

## 成功指标

- 新技术读者能用一段话解释 LoopX。
- 每个主要控制面 packet 都能穿过四个语义槽追踪。
- Focused pytest coverage 增长，大型 smoke files 缩小。
- 公开文档和课程材料使用同一套 loop 词汇。
- 现有 CLI output budgets 和 public compatibility contracts 保持绿色。
- 至少一个 M7 vertical slice 删除旧 command/settlement source，并通过 retry、partial-failure、permission、cancellation 和 budget 测试。
- 只有在两个真实 caller 使用它之后，共享 runtime protocol code 才存在。

## 结论

LoopX harness 不是 "一组状态机"。它是长程 agent loop 外围的 effectful program 和 effect interpreter。本 RFC 让这个故事显式化，并为重构和测试工作提供稳定目标。

## 参考

- 齐梦星空,
  [*主线一：Agent Loop 是 effectful program(1)*](https://www.xiaohongshu.com/discovery/item/6a01d501000000003700c5de?source=webshare&xhsshare=pc_web&xsec_token=ABqpNuladcxhev099wLKw8M3ilhKBua0BQXNpxnBZEGkc=&xsec_source=pc_share).
- 齐梦星空,
  [*主线一：Tool Calling 是 Kleisli arrow(2)*](https://www.xiaohongshu.com/discovery/item/6a02f388000000003502b2d6?source=webshare&xhsshare=pc_web&xsec_token=ABHcIpzpd2RlhAaRr9sZZ-q1OIfRgt7rvG2jn7GUO3tNo=&xsec_source=pc_share).
- 齐梦星空,
  [*主线一：Agent Loop 里的小魔法：函数的组合(3)*](https://www.xiaohongshu.com/discovery/item/6a057524000000003701f6aa?source=webshare&xhsshare=pc_web&xsec_token=AB43lNCJ5ULmfTrGfeTLWd2-jQ6q8nFMGyNAd-tlXJ1uw=&xsec_source=pc_share).
