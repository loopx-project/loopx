# 第 6 讲：Quota 决策内核与 Interaction Contract

> **本讲结论：** `quota should-run` 是只读决策编译器：它把 source facts 编译成 typed
> interaction contract 与 scheduler hint；host 消费决定，不重新实现决定。

建议时长：100 分钟。讲解 60 分钟、决策表推演 25 分钟、代码导航 15 分钟。

## 学习目标

完成本讲后，开发者应该能够：

1. 把 `quota should-run` 理解成状态到本轮协议的纯决策路径，而不是余额检查。
2. 识别 interaction contract 的 user、agent、CLI 三个通道。
3. 解释 decision pipeline 中 boundary、gate、capability、workspace、frontier、repair 的顺序。
4. 判断何时应该 bounded delivery、quiet no-op、monitor poll、wait 或 self-repair。
5. 新增规则时避免在多个 host 或输出字段中重复实现同一语义。

## 本讲技术契约

| 边界 | LoopX 中的答案 |
| --- | --- |
| Input snapshot | Goal、todo、gate、capability、workspace、monitor、vision 与 health projection |
| Decision owner | `build_quota_should_run` 按有序规则编译本轮义务 |
| Output contract | `interaction_contract`、`TurnEnvelope` 与 `scheduler_hint` |
| Effect owner | 无；quota path 必须保持只读，host/agent 只消费 typed decision |
| Re-evaluation | 状态或环境变化后重新编译，不复用旧布尔值推断新一轮 |

## 先判五个具体 Turn

在读 quota payload 之前，先对五组 source facts 做产品判断：

| Source facts | 正确的本轮模式 | 为什么 | 禁止的捷径 |
| --- | --- | --- | --- |
| PR checks pending，monitor 未到期，没有其他 work | wait / monitor quiet | 没有新 observation，也没有 runnable successor | 因 goal active 就调用模型 |
| PR checks 失败，已形成同 agent 的 fix successor | bounded delivery | 外部变化已经翻译成合法 advancement todo | 在 host prompt 里直接决定怎么修 |
| Auto ML Harness 推荐候选，但资源槽已满 | wait / deferred | planner proposal 不等于 capacity、claim 或 launch authority | 按排名直接启动昂贵实验 |
| 研究实验只有 dev lift，holdout successor 可运行 | bounded delivery | promotion 条件未满足，但下一验证步骤明确 | 把 dev score 当 terminal success |
| 所有普通 todo 已关闭，但 vision acceptance 仍有 gap | replan / repair | checklist 关闭不等于目标完成 | 用 `open_count=0` 推断 stop |

`build_quota_should_run` 的工作就是把这些 source facts 按稳定 precedence 编译成
interaction contract。后面的字段、mode 和 scheduler hint 都应能回到这张表中的某个
判断，而不是各自发明一套“是否继续”。

## 为什么叫 Quota，却不只是配额

历史上 quota 入口负责“这一轮是否可以运行”。随着长期任务机制变完整，它必须同时回答：

- goal 是否 active？
- 当前 agent 是否注册？
- 是否有具体 user action？
- agent 是否有独立可执行工作？
- capability 和 workspace 是否匹配？
- 是否存在 stale projection、replan 或 self-repair obligation？
- monitor 是否到期？
- 这一轮可以 spend 吗？
- host 下一次多久后再唤醒？

所以现在的核心函数 `loopx/control_plane/quota/should_run.py::build_quota_should_run` 更像一个 decision compiler：

```text
registered state + projections + runtime declarations
  -> normalized decision rules
  -> interaction_contract
  -> scheduler_hint
  -> CLI next actions
```

`should_run` 仍然保留，便于旧调用方快速判断；但新开发者必须以 `interaction_contract` 为一等协议。

## 输入：内核依赖哪些事实

典型输入集合：

```text
goal registry policy
active goal state
event/todo projection
status health
agent identity and scope
available capabilities
workspace state
user gates and decision scopes
quota/spend ledger
monitor due state
latest run and evidence
agent vision/replan state
host surface
```

内核不应读取 raw transcript 来猜当前任务。所有影响决策的事实都应先进入稳定状态或 projection。

## 输出：不要只读一个布尔值

一个当前 payload 的主要结构包括：

```text
should_run / decision / reason
interaction_contract
scheduler_hint
selected_todo
goal_boundary
capability_gate
work_lane_contract
vision_continuation_audit
user_todo_summary / agent_todo_summary
compatibility fields (historical protocol_action_packet is read-only)
```

其中最稳定的执行入口是：

```json
{
  "interaction_contract": {
    "schema_version": "loopx_interaction_contract_v0",
    "mode": "bounded_delivery",
    "user_channel": {},
    "agent_channel": {},
    "cli_channel": {}
  }
}
```

旧的 `execution_obligation`、`heartbeat_recommendation`、`work_lane_contract` 等仍可用于兼容或 drilldown，但不应在新 host 中重新拼装成另一个决策模型。

## 三个通道

### User channel

回答：是否需要通知用户，具体是什么问题？

```json
{
  "action_required": true,
  "notify": "NOTIFY",
  "reason": "<typed reason>",
  "todos": ["<concrete user todo>"]
}
```

要求：

- 有 open user todo 时，必须给出具体问题；
- 不允许只说“owner gate”；
- 如果 open count 和 payload 不一致，应触发 projection repair；
- user action 可以与 agent execution 同时为 true。

### Agent channel

回答：当前 peer 是否必须尝试、允许交付什么？

```json
{
  "must_attempt": true,
  "delivery_allowed": true,
  "quiet_noop_allowed": false,
  "primary_action": "<todo-id>: <bounded action>"
}
```

它还可能带：

- selected todo；
- safe fallback；
- repair obligation；
- vision continuation audit；
- blocking reason。

### CLI channel

回答：写回和记账的正确顺序是什么？

```json
{
  "next_cli_actions": [
    "loopx refresh-state ...",
    "loopx quota spend-slot ..."
  ],
  "spend_allowed_now": false,
  "spend_after_validation": true,
  "spend_policy": "spend once after validated writeback",
  "delivery_workspace_causality": {
    "schema_version": "delivery_workspace_causality_v0",
    "refresh": "delivery_workspace; otherwise --delivery-workspace-path",
    "spend": "recorded_delivery_workspace",
    "mismatch": "fail_closed"
  }
}
```

Host 和 agent 不应凭记忆重建这些命令，也不能从 goal repo、当前 shell 或下一条
Todo 猜测交付 workspace。`refresh-state` 记录本次交付的因果 workspace；`spend-slot`
只允许从这份记录对应的 workspace 记账。为保持默认 hot path 紧凑，这个子契约只在
selected Todo 显式声明 `task_repository` 且当前 mode 允许验证后记账时出现。

## Mode 是闭集协议

`interaction_contract.mode` 把一组相关布尔压缩成可测试的状态。典型语义包括：

| Mode/行为 | Agent 应做什么 | Spend |
| --- | --- | --- |
| `bounded_delivery` | 完成一个可验证 transition | 写回后一次 |
| user wait/gate | 输出具体用户问题；仅在允许时做 safe fallback | wait/ack 不 spend |
| monitor poll | 最多一次只读检查，状态无变化则 quiet | 无推进不 spend |
| quiet no-op | 不制造假进度，保持或调整 cadence | 不 spend |
| repair/replan | 改变 machine-visible frontier | 有验证写回才 spend |
| terminal no-followup | 所有 closure 条件满足，host 可停 | 不再 spend |

具体枚举应以当前 schema 和 smoke 为准。不要在课件或 host 中硬编码一个永远不更新的字符串列表。

## Decision Pipeline

`quota should-run` 的决策链分布在 `should_run.py`、`should_run_prepare.py` 与
`should_run_packet.py` 三个 bounded 模块中，阅读时按决策阶段理解，而不是按行号通读。

### 1. Resolve goal 与 agent identity

```text
registry contains goal?
agent is registered peer?
agent identity matches this quota call?
```

失败应 fail closed，不能默认使用“第一个 agent”。

### 2. Build goal boundary

边界包括：

- repo / write scope；
- authority sources；
- spawn policy；
- private/public boundary；
- user-owned actions；
- execution profile。

这一步先于 todo 选择，因为 todo runnable 不代表越过 goal boundary 后仍合法。

### 3. Normalize user gates

计算：

- blocking scope；
- decision scope 是否精确匹配；
- 是否 global；
- 是否有具体 user payload；
- 是否存在 projection gap。

### 4. Apply outcome floor 与 self-repair rules

如果连续多轮只做表面动作、没有 material outcome，内核可以要求扩大到真正结果或进入 self-repair。

Repair 必须改变 machine-visible frontier，例如：

- selected action；
- runnable todo set；
- gate/blocker；
- successor/supersede；
- capability boundary；
- monitor target；
- active state next action；
- agent vision patch。

只写“已重新思考”不是 repair delta。

### 5. Apply capability gate

从 open advancement todos 中筛出本轮能力可满足的候选。

重要边界：

- 先形成候选集，再由 agent 选择实际 todo；
- 未认领 advancement todo 应唤醒所有未被排除的 eligible peer；
- 不能因为同一 goal 中另一个 todo 已被 claim，就把未认领工作饿死。

### 6. Apply workspace guard

检查 task repository、required write scopes、worktree/branch boundary。Workspace 不匹配时，内核可以允许 workspace repair，但不能假装 normal delivery 已经合法。

### 7. Resolve frontier 与 continuation

处理：

- priority；
- claim/lease；
- dependency/resume condition；
- successor；
- supersede；
- monitor；
- replan/vision acceptance gap；
- terminal closure。

### 8. Compose interaction contract

不同规则不会各自返回一套 host 行为，而是最终合成三个通道。这样可以表达“需要用户动作，但 agent 仍有安全工作”的组合状态。

### 9. Compose scheduler hint

调度不是另一个独立 AI 判断。它从已解析的 interaction contract 和 lifecycle state 派生 cadence class、RRULE 建议、ACK 和 spend policy。

## 决策优先级为什么重要

假设顺序错误：先选 todo，再检查 workspace。内核可能先宣布“必须实现”，随后才发现 agent 在错误 repo，导致 host 已开始写入。

更安全的顺序是：

```text
identity
  -> authority/boundary
  -> user decision scope
  -> capability/workspace eligibility
  -> frontier/priority
  -> interaction contract
  -> scheduler
```

但 self-repair 和 projection gap 可能在多个阶段插入，因为它们修复的是决策输入本身。新增规则时应在 `rule-seam-map` 中明确它的输入和 precedence。

## 九个典型 Case

### Case A：有 runnable todo

```text
user open = 0
agent candidate = T1
capability = matched
workspace = matched
```

结果：bounded delivery，必须尝试，验证后 spend。

### Case B：P0 user gate + 独立 P1

```text
P0 requires D1
D1 user gate open
P1 independent and runnable
```

结果：user channel 通知 D1；agent channel 执行 P1；不是全局 wait。

### Case C：只有未到期 monitor

```text
advancement frontier empty
monitor next_due_at > now
```

结果：quiet wait/backoff，不 poll、不 spend、不 stop automation。

### Case D：Monitor 到期但证据不变

```text
monitor due
one external poll allowed
evidence unchanged
```

结果：记录 no-change 或更新 due state，quiet no-op，不 spend。连续重复应触发 repair，而不是每 2 秒刷盘。

### Case E：Todos 看似关闭，但 acceptance gap 仍开

```text
open todos = 0
monitor = closed
successor = none
agent vision acceptance gap = open
```

结果：不能 terminal。需要 authoritative evidence、successor、blocker/user gate 或 superseding vision。

### Case F：Due monitor 发现 gate，同时 replan 到期

```text
M1 monitor due
M1 poll => material evidence changed
evidence creates scoped gate G1 and blocked successor T1
periodic autonomous replan obligation = due
```

这不是“monitor 已完成，所以 quiet”的状态。合理推导是：

1. monitor 的一次 poll 结束，并以 compact evidence 写回；
2. G1 进入 user channel，T1 在对应 decision scope 上保持不可交付；
3. replan obligation 在 frontier 层优先于 monitor quiet，要求形成 keep/split/add/retire/ask-decision 的可见 delta；
4. 只有不依赖 G1、且 interaction contract 明确允许的 repair 或独立工作才可进入 agent channel；
5. host 依据新 contract 重算 cadence，不能沿用 M1 的旧 monitor backoff。

这里的复杂性不是三个 feature 相加，而是一次 evidence transition 同时改变 gate、frontier 和 scheduler identity。

### Case G：`user_action` 看起来相关，但不能满足 decision scope

```text
T1 requires decision D1
U1 task_class = user_action
U1 text mentions D1
no compatible user_gate decision exists
```

结果：U1 可以进入 user channel，但 T1 仍未获得 D1。Todo 文案相似、同一用户已看到提醒、甚至 `open_count > 0`，都不能把非阻塞 action 提升为 authority。若 reducer 或 compact projection 丢掉 task class，系统应 fail closed 或进入受控 repair，而不是恢复 normal delivery。

### Case H：多个 monitor 交错，某条 lane 已连续 no-change

```text
M1.consecutive_no_change = 3
M2.consecutive_no_change = 1
latest runs = M1 poll, M2 poll, M1 poll, M2 poll
same-agent runnable advancement = none
```

若只统计“run history 末尾连续出现了几次同类 monitor poll”，M1 和 M2 会互相打断，两个实际停滞的 target 都可能永远到不了阈值。正确 source 是每个 monitor todo 自己的 `consecutive_no_change`：M2 的 poll 不重置 M1；M1 的 material transition 也只重置 M1。

Quota 扫描 monitor lanes 后，只要任一 lane 达到阈值、且当前 agent 没有 runnable advancement，就形成 `monitor_no_change_streak` replan obligation。若存在可执行 advancement，则 normal delivery 优先；若 advancement 只是 blocked，则不能用它掩盖 monitor 已经停滞的事实。

### Case I：Harness 推荐两个候选，但只有一个资源槽

```text
Explore Harness = analysis_only
candidate T1, T2 = ranked and scope-compatible
short_pool capacity = 1
short_pool active usage = 1
T1/T2 resume_when = capacity_available:short_pool
```

结果：Harness packet 可以帮助解释当前 portfolio，但 quota frontier 仍为空；本轮应 wait，
而不是 claim、launch 或 spend。已有 external task 的 monitor 也不因为 planner 产生新排名
就提前 poll。资源 readback 释放槽位后，Kernel 重新计算 `resume_when`，只暴露合法候选。

如果 Harness 从 Graph 读到一条新 `refutes` edge，也只能形成 retirement/replan proposal。
Graph、Harness 和 quota 分别拥有 evidence、advisory planning 与 action legality，不能把三个
判断压成一个 `candidate_ready=true`。

## `action_required` 与 `open_count`

Heartbeat 合同要求：

```text
if action_required=true or open_count>0:
  show concrete Chinese todos/questions
else:
  quiet / no-user-todo
```

如果计数大于零但具体 payload 缺失，不应该猜：

```text
具体 user todo 未投影，需修复 LoopX 状态投影
```

这是状态契约错误，不是文案问题。

## Spend 规则

Quota slot 表示一次有效推进，不是一次调用。

### 应 spend

- 实现或文档 artifact 已完成并验证；
- blocker 已具体化并写入可恢复状态；
- repair 改变了 machine-visible frontier；
- authorized external effect 有 receipt 且已回写。

### 不应 spend

- quota/status 只读；
- guided preview；
- scheduler ACK；
- monitor no-change poll；
- quiet no-op；
- 只有“正在分析”的更新；
- host capability 缺失导致 proposal 未执行。

Spend 一次意味着同一 validated turn 不应被多个 compatibility path 重复记账。

## 实验：读一个 Quota Packet

在实验 goal 中准备一个 open agent todo，然后运行：

```bash
loopx --format json quota should-run \
  --goal-id <lab-goal> \
  --agent-id <lab-agent> \
  --available-capability shell \
  > /tmp/loopx-quota.json
```

只提取协议字段：

```bash
jq '{
  should_run,
  selected_todo,
  interaction_contract,
  capability_gate,
  scheduler_hint
}' /tmp/loopx-quota.json
```

回答：

1. 决策用了哪些 canonical/projection 输入？
2. 用户和 agent 通道是否可以同时 active？
3. 哪个字段说明 spend 时机？
4. 如果去掉 `--available-capability shell`，候选如何变化？

## 核心代码领读：quota 如何决定 run、repair、wait 或 terminal

先记住一个原则：`quota should-run` 不是布尔函数。布尔值只是兼容摘要，真正的产品是一个带证据的 decision packet。

### 1. 先建立 decision inputs，再谈优先级

`loopx/control_plane/quota/should_run_prepare.py` 负责解析 registry goal、quota plan、
user/agent todo projection 与 peer identity。`should_run_packet.py` 才进入核心 pipeline：

```python
goal_boundary = _goal_boundary(registry_goal or item, item=item, ...)

work_lane_contract = build_quota_work_lane_contract(
    item,
    status_payload=status_payload,
    goal_id=safe_goal_id,
    agent_id=agent_frontier_id,
    agent_todo_summary=agent_todo_summary,
)
task_orchestration_contract, work_lane_contract = (
    apply_task_orchestration_contract(
        fallback_work_lane_contract=work_lane_contract,
        goal_boundary=goal_boundary,
        agent_identity=agent_identity,
        agent_todo_summary=agent_todo_summary,
        ...,
    )
)
capability_gate, capability_monitor_contract, capability_monitor_fallback = (
    build_capability_gate_with_monitor_fallback(...)
)
workspace_guard = build_agent_workspace_guard(
    item,
    agent_identity,
    agent_todo_summary=agent_todo_summary,
    selected_todo=work_lane_selected_todo,
)
```

这一段的阅读顺序不能倒置：

1. `goal_boundary` 先给出允许的 scope 和 orchestration；
2. work lane 决定当前是在 advancement、monitor 还是特殊 inbox lane；
3. capability gate 判断候选是否可运行；
4. workspace guard 判断“即使能运行，当前地点能否交付”。

### 2. Repair 分支覆盖普通 delivery

接下来两个 projection repair 很关键：

```python
projection_gap_repair = build_state_projection_gap_repair_hint(
    projection_gap,
    candidate_should_run=bool(
        normal_delivery_allowed or recovery_allowed or self_repair_allowed
    ),
    user_todo_summary=user_todo_summary,
    agent_todo_summary=agent_todo_summary,
    work_lane_contract=work_lane_contract,
)
if projection_gap_repair:
    stall_self_repair = projection_gap_repair
    self_repair_allowed = True
    normal_delivery_allowed = False
    recovery_allowed = False

boundary_projection_repair = build_boundary_projection_repair_hint(...)
if boundary_projection_repair:
    self_repair_allowed = True
    normal_delivery_allowed = False
    recovery_allowed = False
```

原因是：如果 prose 与 structured todo、或 todo 与 write boundary 已经矛盾，继续产品交付只会把错误状态放大。此时 `should_run` 仍可能为真，但 `effective_action` 必须变成 repair。

### 3. Guard precedence 要从赋值看，不要只读注释

继续往下看状态如何被覆盖：

```python
if capability_gate and capability_gate.get("action") != "run":
    normal_delivery_allowed = False
    recovery_allowed = False
    capability_repair_allowed = (
        capability_gate.get("action") == "repair_bridge"
    )

if workspace_guard:
    normal_delivery_allowed = False
    recovery_allowed = False
    self_repair_allowed = False
    capability_repair_allowed = False
    workspace_repair_allowed = True

should_run = bool(
    normal_delivery_allowed
    or recovery_allowed
    or self_repair_allowed
    or capability_repair_allowed
    or workspace_repair_allowed
)
effective_action = _effective_action(...)
```

workspace guard 在这里覆盖 capability/self-repair，是因为任何会写 repository 的修复也不能发生在错误 workspace。课堂上可把这些 flag 画成 priority lattice，而不是互斥 enum。

### 4. `terminal_no_followup` 必须由完整 closure 推导

`loopx/control_plane/goals/goal_frontier/terminal.py` 把 terminal 拆成两次验证。

第一次验证 user/agent todo source：

```python
if not (
    completeness.get("status") == "valid"
    and completeness.get("source") == "structured_todo_projection"
    and completeness.get("terminal_closure") == "valid"
):
    return "invalid", False

if not (
    done == total
    and open_count == deferred_count == monitor_due_count == 0
    and not monitor_open_items
):
    return "invalid", False
```

这里的 `total == 0` 不是天然有效。只有 Active State 显式声明了对应的 todo
section，且 summary 带有匹配 role/source 的 `source_proof`，零条来源才是合法的空集合；
整个 section 缺失仍然 fail-closed。`items` 只是最多展示 12 条的有界投影，因此
`total > 0` 时允许 `0 < len(items) <= total`，完整性由 counts 与
`terminal_closure_proof.item_count` 证明，不能要求展示列表长度等于总数。

第二次验证整个 frontier：

```python
return (
    all(_strict_zero(value) for value in projected_counts)
    and monitors.get("present") is False
    and successors.get("ready_count") == 0
    and successors.get("blocked_count") == 0
    and projection.get("acceptance_gaps") == []
    and projection.get("autonomy_blockers") == []
    and projection.get("replan_required") is False
)
```

最终只有两边 source 都 valid、至少一边存在结构化 `no_followup` intent、且 frontier 为空，才生成：

```python
{
    "kind": "no_followup",
    "derived": True,
    "source": "validated_goal_closure",
}
```

这正是为什么不能由用户随手写一个 `terminal_no_followup` 字段来停掉 automation。

### 5. Interaction contract 把 decision 分给三个 actor

quota 末尾不是直接打印一句话，而是调用 `build_interaction_contract`，形成：

```text
user_channel  -> 是否真的需要用户动作、具体问题是什么
agent_channel -> 本轮 must_attempt / delivery_allowed / quiet_noop_allowed
cli_channel   -> 下一条 CLI 写回、何时允许 spend
```

旧的 `action_required`/`open_count` 只是 user channel 的兼容字段。阅读任何 quota packet 时，应先看 `interaction_contract.mode`，再分别看三个 channel，最后才看兼容布尔值。

### 断点与 decision table

建议在 `should_run_prepare.py` 与 `should_run_packet.py` 的关键阶段打断点，记录下面的最小表：

| 场景 | normal | self repair | workspace repair | terminal | 预期 action |
| --- | ---: | ---: | ---: | ---: | --- |
| runnable todo | 1 | 0 | 0 | 0 | run |
| projection gap | 0 | 1 | 0 | 0 | self repair |
| canonical checkout peer write | 0 | 0 | 1 | 0 | move worktree |
| todo/monitor/successor/replan/gap 全闭合 | 0 | 0 | 0 | 1 | terminal no-followup |

### 读完这一段应能回答

1. 为什么 `should_run=False` 不能单独解释“为什么没跑”？
2. 为什么 projection repair 要覆盖正常 delivery？
3. workspace repair 为什么比 capability repair 更晚覆盖？
4. terminal closure 同时检查哪些 todo、monitor、successor、replan 与 acceptance 状态？
5. `action_required=false` 为什么不代表 agent 可以 quiet no-op？

## 代码阅读方法

### 从入口向规则 seam 读

```text
loopx/cli.py
  -> quota command handler
  -> loopx/control_plane/quota/should_run.py::build_quota_should_run
  -> loopx/control_plane/quota/
  -> loopx/control_plane/runtime/
  -> loopx/control_plane/scheduler/
  -> loopx/control_plane/todos/
```

不要把 `loopx.quota` facade 的剩余文件长度当成设计边界。已证明的规则已经迁入
bounded context；`docs/product/core-control-plane/bounded-context-layout.md` 和
`rule-seam-map.md` 才是导航图。

### Characterize before move

修改共享规则前：

1. 找到现有 parity fixture；
2. 加一个能暴露边界的 case；
3. 确认 status、quota、scheduler 的输出关系；
4. 再提取或改变规则；
5. 删除重复入口，不保留没有真实兼容需求的 wrapper。

## 代表性 Smoke

- `examples/control_plane/heartbeat-quota-flow-smoke.py`
- `examples/control_plane/interaction-contract-state-machine-smoke.py`
- `examples/control_plane/capability-gate-projection-smoke.py`
- `examples/control_plane/peer-agent-workspace-guard-smoke.py`
- `examples/state-projection-gap-smoke.py`
- `tests/control_plane_ts/quota_monitor_poll_commit.test.ts`

## 常见实现错误

### 在 host prompt 中重新判断 runnable

Host 应服从 interaction contract，不应复制 todo/gate/capability 规则。

### 单看 `claimed_by`

Claimed todo 只约束它自己。未认领的独立 advancement todo 仍需进入 eligible peer 候选。

### 把所有 false 都解释为 stop

`should_run=false` 可能是等待证据、未到期 monitor、scheduler backoff 或 quiet no-op。只有完整 closure 才是 terminal。

### Repair ACK 没有 delta

ACK 本身不改变 frontier，不能清除 obligation。

## 课后检查

1. 为什么 `quota should-run` 不能被实现成 `open_todo_count > 0`？
2. 三个 interaction channels 分别服务谁？
3. Capability gate 和 goal authority 的差别是什么？
4. 哪些 no-op 必须保持 automation active？
5. 为什么 terminal closure 要检查 todo、monitor、successor、replan 和 acceptance gap？

下一讲把内核决策交给 host：heartbeat prompt、Codex App automation、stateful backoff、scheduler ACK 和 terminal stop。
