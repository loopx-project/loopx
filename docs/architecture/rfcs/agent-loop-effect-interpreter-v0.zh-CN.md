# RFC：Agent Loop Effect Interpreter（v0）

| 字段 | 值 |
|---|---|
| 状态 | 已接受 |
| 替代 / 关闭 | 无 |
| 日期 | 2026-08-08 |
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

### 还缺什么

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
