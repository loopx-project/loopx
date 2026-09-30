# 第 1 讲：Harness 是 effectful program

> 建议时长：30 分钟。先建立心智模型，不进入具体状态枚举。

## 这一讲要建立的判断

Harness 不是“一堆状态机”，而是 agent loop 的 effectful program 和 effect
interpreter。agent loop 是被解释的循环，harness 才是解释外部 effect 的程序；
状态机只是 harness 内部的决策表。

全文参考：
[Agent Loop Effect Interpreter RFC](../../architecture/rfcs/agent-loop-effect-interpreter-v0.md)。
公开来源：
[主线一：Agent Loop 是 effectful program(1)](https://www.xiaohongshu.com/discovery/item/6a01d501000000003700c5de?source=webshare&xhsshare=pc_web&xsec_token=ABqpNuladcxhev099wLKw8M3ilhKBua0BQXNpxnBZEGkc=&xsec_source=pc_share)、
[主线一：Tool Calling 是 Kleisli arrow(2)](https://www.xiaohongshu.com/discovery/item/6a02f388000000003502b2d6?source=webshare&xhsshare=pc_web&xsec_token=ABHcIpzpd2RlhAaRr9sZZ-q1OIfRgt7rvG2jn7GUO3tNo=&xsec_source=pc_share)、
[主线一：Agent Loop 里的小魔法：函数的组合(3)](https://www.xiaohongshu.com/discovery/item/6a057524000000003701f6aa?source=webshare&xhsshare=pc_web&xsec_token=AB43lNCJ5ULmfTrGfeTLWd2-jQ6q8nFMGyNAd-tlXJ1uw=&xsec_source=pc_share)。

## 一个最朴素的 Agent Loop

```text
while true:
  response = llm_api.invoke(messages)
  if not response.tool_calls:
    return response.final_answer
  observations = [tools[call.name].invoke(call.args) for call in response.tool_calls]
  messages += [response.message] + observations
```

把 tool call 推广到所有外部动作，就得到控制面真正在解释的形状：

```text
model -> effect request -> harness interprets effect -> observation -> model
```

模型输出的是动作请求，不是动作本身。真正的执行、权限、预算、调度、失败恢复、
证据沉淀都发生在 harness 这一侧。

## 为什么 harness 是这个解释器

普通 Agent 只在当前上下文里推理。LoopX 把一个长程任务的有限上下文之外的
目标、todo、authority、quota、evidence、cadence 和 recovery 状态外置，再把
当前状态编译成一轮可执行的 CLI packet。

用纯函数和 effect 的差别表达：

```text
A => B        // 普通计算：给定状态，算出结果
A => F[B]     // effectful：结果活在外部世界
```

LoopX harness 是那个 `F`：

```text
GoalState => F[QuotaDecision]
```

## Effect Request 到 Observation 的映射

| Agent loop 环节 | LoopX 对应物 |
|---|---|
| Agent loop | 每轮 automation、heartbeat、PR monitor、持续重构 |
| Effect request | `todo add`、`quota spend`、`refresh-state`、`notify`、`monitor poll`、`bind-agent-thread` |
| Harness interprets effect | `quota should-run` + `interaction_contract` + `capability_gate` + `work_lane_contract` + `scheduler_hint` |
| Observation | quota packet、run history、evidence log、状态 writeback |
| Middleware 挂载点 | user gate、capability bridge、scheduler ACK、cooldown、外部 evidence poll |

## State Machine 是 Interpretation Table

每个状态机都回答同一组问题：

```text
输入 effect | 解释器 | 决策 | observation | 下一 effect
```

例子：monitor scheduler。

```text
Monitor cadence / due horizon
  -> scheduler interpreter
  -> host RRULE / initial interval
  -> scheduler_hint packet
  -> next heartbeat or monitor poll
```

例子：quota runtime。

```text
Agent proposes next bounded turn
  -> quota interpreter
  -> run / gate / wait / repair / quiet
  -> quota packet + interaction contract
  -> execute, ask owner, observe, repair, or no-op
```

## 组合：Around 是数据，不是回调

公开课程把组合分成三层：

| 组合 | 形状 | LoopX 对应物 |
|---|---|---|
| 函数组合 | `A => B`、`B => C` | read model -> projection -> decision |
| Kleisli 组合 | `A => F[B]`、`B => F[C]` | 一轮 bounded turn、host effect、validated writeback |
| Middleware 组合 | `(A => F[B]) => (A => F[B])` | `capability_gate`、`interaction_contract`、`work_lane_contract`、`scheduler_hint` |

很多 runtime 的 around 逻辑会拿到 `handler`：一个可继续执行主流程的回调，
然后决定是否调用、调用几次、失败后怎样 fallback。LoopX 不能跨上下文和 session
传递一个可调用对象。它的 `handler` 是数据：packet 里的 `next_effect` 编码下一组
CLI 动作、scheduler ACK 和 failure hint，由 host 或下一轮 automation 执行。

这个差异不是缺一个 middleware 层，而是控制面的合理形状：

- 可以 short-circuit：`decision` / `effective_action` 直接表达
  `skip`、`wait`、`monitor_quiet_skip`、`repair_bridge`、`ask_owner`，不会假装原
  effect 已经执行；
- 可以 rewrite：`capability_gate` 把下一步改写成先补能力，`work_lane_contract`
  可以用 due monitor 或 Lark inbox 抢占普通 advancement；
- 可以 settle：`scheduler_hint` 的 ACK / failure hint 告诉 host 如何提交成功或
  失败，`unchanged_poll` 限制重复轮询。

失败、取消、权限和预算必须保持结构化，不能被一个通用 catch 吞掉。看一个
around 决策时，问七件事：

1. 它在解释哪个 effect request？
2. 哪个 around layer 拥有决策，输出什么 observation？
3. 它能否 short-circuit，而且不假装原 effect 已执行？
4. `next_effect` 在哪里被编码？
5. 失败、取消、权限、预算是否仍然结构化可见？
6. around layer 的先后顺序是否明确并有测试？
7. host effect 之后，evidence、trace、budget 是否通过 writeback / ACK / spend
   继续成立？

## CLI 是更高密度的 effect

单个 tool call 是 `ToolInput => F[ToolOutput]`。LoopX 的 CLI packet 是一条更高
密度的 effect：一条命令可以把权限、预算、参数校验、外部执行、失败语义、scheduler
ACK 和 writeback 都编码进同一个 request。模型仍然只提出 effect request，harness
负责解释并决定下一步执行什么。

如果未来厂商 API 原生支持 serial tool calls 或 interleaved reasoning，对 LoopX
来说只是解释器内部的一种 execution mode：

- 串行、并行、交错是 execution strategy，不是新的状态机；
- `effect_request -> interpretation -> observation -> next_effect` 仍然稳定；
- `next_effect` 从一条 CLI 命令变成一段有序 effect program。

## 当前实现：先区分 read lens 与 settlement algebra

`loopx.control_plane.effect_program` 目前承载两组相关但不同的抽象。

第一组是 packet read lens：

- `EffectRequest`、`EffectInterpretation`、`EffectObservation`、`EffectNext`
  和 `EffectTurn` 把现有 packet 映射到稳定语义槽；
- `EffectProgram` / `EffectStep` 读取 bootstrap 和本地 scheduler 已有的
  `ordered_steps`；
- 这组对象不自动获得执行权。读取一组命令，不等于 LoopX core 有权执行并结算它们。

第二组是 typed settlement algebra：

- `SettlementIdentity` 把 `goal_id + agent_id + todo_id + turn_instance_id`
  压成同一条 effect identity；
- `SettlementPlan` / `SettlementStep` 声明步骤顺序、owner、precondition、
  idempotency key 和 expected receipt；
- `SettlementReceipt` 证明一个步骤已经提交；
- `SettlementFailure` 保留失败发生在哪一步，以及它是 identity、permission、
  budget、writeback 还是 spend 问题；
- `SettlementResult.bind` 只在前一步成功时进入下一步，并把此前 receipt 链带过去。

最小组合可以写成：

```python
result = (
    validate(identity)
    .bind(writeback)
    .bind(spend)
    .bind(terminal_closeout)  # conditional no-followup only
)
```

这里的 `F[B]` 就是 `SettlementResult[B]`。它不只是“可能失败”的返回值，还携带
已经提交的 receipts。前一步失败后，`bind` 原样保留 failure 和已有 receipts，后续
effect 不再执行。

## 三类真实 adapter 怎样复用核心 algebra

当前 `main` 有三类真实采用者。它们共享 identity、plan、receipt、failure 和
`bind` 语义，不共享一个万能 executor。

| Adapter | 有序链 | 执行与结算边界 |
|---|---|---|
| Codex App / CLI quota | validation -> durable writeback -> quota spend -> conditional terminal closeout | quota 构建 data-encoded CLI plan，并从 event/run receipt 复核原始 turn identity；只有 final no-followup 在 spend 后关闭 Goal，host scheduler 留在 settlement 外 |
| Isolated turn driver | validation -> durable writeback -> quota spend -> conditional terminal closeout | turn driver 拥有 in-process callbacks 和 journal checkpoint；terminal closeout 失败时只重放 closeout，不重复 writeback/spend |
| Task-lease acquire | validation -> durable lease write | native TS transaction 拥有 source-CAS、owner eligibility、conflict、file lock、CAS 和 atomic write；Python 只投影 compact authority facts 并执行一次 transport call |

这也是“quota 的 Effect Program 是否继承 core”的准确答案：它复用并 re-export
core-owned algebra，再提供自己的 plan builder 和 durable receipt 查询；它不继承一个
领域基类。Task-lease acquire 已退出这种 settlement adapter 形态，由 bounded native
transaction 直接完成 decision、durable write 与 canonical receipt projection。

这些路径的 authority boundary 不同：quota CLI 跨 agent/host，turn driver 拥有
callback，task lease 则在自己的 native transaction 内完成原子 persistence。把它们
塞进一个 shared executor 会隐藏 owner，而不是减少重复知识。当前共享层只统一可复用
primitive，不统一执行权。

## 四条不变量比类名更重要

Effect Program 的价值落在四条可复核不变量上：

1. **同一 identity**：一条 settlement 的所有 receipts 使用同一个 `effect_id`。
2. **有序提交**：没有 durable writeback receipt 就不能 spend；没有 matching spend
   receipt 就不能提交 terminal no-followup closeout。
3. **失败短路**：某一步失败后，不允许出现后续外部 effect。
4. **可重放、至多一次**：已提交 prefix 可以恢复，但同一 identity 不重复结算。

普通 successor completion 仍是 Todo bounded context 的 lifecycle action，可以在
settlement 前建立下一条 runnable frontier；它不是 terminal closeout。只有 final
`no_followup` 会改变 Goal 的终态，因此必须放到 matching spend 后。这样无需放宽
`terminal_no_followup` 守卫，也不会出现“先终止、再给终态 effect 补 spend”的例外。

测试不是从当前输出生成 golden，而是用独立语义 oracle 检查三类 adapter：

- `test_effect_program_adapter_conformance.py` 统一验证 identity、receipt 顺序、failure、
  short-circuit 和 scheduler-outside-settlement，并用 step reorder、failure 丢失、
  effect-id 漂移、重复 side effect mutation 证明 oracle 能抓住错误；
- `test_effect_program_fault_replay_matrix.py` 穷举仍使用 Effect Program 的合法 phase
  prefix 与失败点；
- `test_effect_program_incident_replay.py` 用 public-safe 事故 fixture 回放真实 adapter；
- native task-lease tests 独立覆盖 idempotent replay、参数漂移、source-CAS、conflict、
  crash/retry 与 atomic write；共享 algebra 不接管领域 truth。

## 哪些路径不应该 Kleisli 化

Effect Program 是有界采用规则，不是代码风格运动。只有一条路径同时具备多步外部
effect、稳定 identity、durable receipt、replay 要求，并且能删除重复 settlement
truth 时，才值得接入。

read model、projection、quota decision、vision/replan policy、gate selection 和 monitor
routing 仍然适合普通纯函数或领域状态机。scheduler apply / ACK 是 host handoff，也不应
为了“统一”被搬进 agent settlement。共享抽象要减少重复知识，不能只减少看起来相似的
代码。

## 代码领读顺序

按下面的顺序读，比从一个大型 executor 文件开头向下读更容易建立边界：

1. `loopx/control_plane/effect_program.py`：先读 `SettlementResult.bind`、identity、
   plan 与 receipt；
2. `loopx/control_plane/quota/effect_program.py` 和 `quota/settlement.py`：看 CLI plan
   与 durable receipt 怎样绑定原始 turn；
3. `loopx/control_plane/turn_driver/settlement.py`：看 callback adapter 怎样从合法
   journal prefix 恢复；
4. `loopx/control_plane/work_items/task_lease_acquire.ts`：看 bounded transaction 怎样在
   一个 lock boundary 内完成 source-CAS、decision 与 durable write；
5. Effect Program 的 conformance / fault-replay / incident-replay 测试，以及
   task-lease native focused tests：从不变量反向检查两种边界。

## 读代码前先问五个问题

1. 这个 effect request 是谁提出的？
2. 哪个 interpreter 决定它能否执行？
3. 决策输出了什么 observation？
4. observation 如何回灌到下一轮模型上下文？
5. 失败、取消、权限、预算和证据在哪里被解释？

## 实验

运行一次真实 CLI：

```bash
loopx --format json quota should-run \
  --goal-id loopx-meta \
  --agent-id codex-quality-qualification \
  --available-capability network \
  --available-capability external_evidence_poll \
  --codex-app
```

在输出里找到：

- `interaction_contract`：解释后的本轮协议；
- `capability_gate`：权限解释；
- `scheduler_hint`：时间解释；
- `work_lane_contract`：路由解释；
- `recommended_action`：observation 指向的下一 effect。

再运行 typed settlement 的最小验证集：

```bash
python -m pytest -q \
  tests/control_plane/test_effect_program_adapter_conformance.py \
  tests/control_plane/test_effect_program_fault_replay_matrix.py \
  tests/control_plane/test_effect_program_incident_replay.py \
  tests/control_plane/test_quota_settlement_cli.py
```

阅读失败用例时，先定位 failure step，再检查后续 effect 是否为零；不要只看最终
`ok=false`。

## Review 问题

- 如果把状态机文档写成 interpretation table，哪个状态维度最容易被讲清楚？
- 为什么 `quota should-run` 是解释器而不是另一个状态机？
- 一个新的 capability 应该解释哪一类 effect request，输出什么 observation？
- 新路径真的有稳定 identity、durable receipt 和 replay 要求，还是只有几段相似代码？
- 接入 core algebra 后删除了哪份重复 settlement truth？如果没有删除，抽象是否过早？
