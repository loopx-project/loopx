# 持久状态与只读投影

> Update (2026-09-25): the Todo `events.jsonl` API, replay, backfill and completion
> examples below describe a retired experiment. Current Todos use legacy Markdown
> or the selected File/SQLite authority. See the
> [retirement contract](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/event-sourced-state-contract-v0.md).


长程任务能跨 session 恢复，不是因为系统保存了更多聊天记录，而是因为关键事实有稳定归属，
并且能够被重新投影成当前决策。本章建立 LoopX 的状态底座：哪些表面保存事实，哪些表面只负责
阅读，以及为什么“看起来像当前状态”的页面或 Markdown 不能自动成为写入入口。

## 本章目标

读完后，你应该能：

- 区分 registry、event ledger、active-state workbench、run history 与 status projection；
- 判断一个字段应该属于 canonical state、外部事实还是只读 projection；
- 解释 append-only event、replay、idempotency 与 freshness 的关系；
- 说明为什么 dashboard、prompt 和 task graph 都不能成为第二套状态机；
- 在协议变化时找到权威来源，而不是依赖某个 Python 函数名。

## Goal identity 不属于聊天线程

LoopX 的持久身份是 **Goal**，不是某个 Host thread：

```text
Goal
├── objective and boundary
├── todos, gates and evidence lineage
├── registered peer identities
└── runtime and projection routes

Session / thread
└── one temporary executor context
```

一个 Goal 可以先后由 Codex App、Codex CLI 或其他 Host 推进；一个 session 也可能读取多个 Goal。
读取 Goal 不会自动授予写权限，结束 session 也不会使 Goal 消失。

### 精确复用 Goal，不靠文本猜测

Goal 复用依赖 stable `goal_id` 和 registry 连接，不依赖 objective 的模糊相似度：

```text
one registered goal
  -> reuse that exact goal boundary

multiple registered goals
  -> read-only goal_selection_gate
  -> choose one exact goal_id
  -> rerun before any mutation
```

如果项目注册了多个 Goal，`start-goal --guided` 应列出可选 id、状态和精确重跑命令。在选择完成前，
Todo 写入、Agent 注册和 Host activation 都不应发生。目标文本相似、来自同一 repository，甚至
共享一部分 acceptance，都不是静默合并 Goal 的依据。

还要把 Goal reuse 与 Agent takeover 分开。新 Agent 可以读取同一 Goal 的公共 frontier 和历史，
但在无已注册 lane 时默认注册 fresh `agent_id`；复用已有 Agent identity 需要用户明确选择那个精确 id。这样历史
lineage 能连续，执行责任却不会被新 session 冒名继承。

因此，恢复模型不是：

```text
restore = replay the old conversation
```

而是：

```text
next decision =
  replay(durable project facts)
  + inspect(fresh workspace and external facts)
```

旧对话可以帮助理解，但不能比当前 Git、当前 Gate、当前 CI 和 LoopX canonical state 更权威。

## 五类状态表面

### 1. Registry：身份、连接与长期策略

Registry 回答“这个 Goal 是谁、连接到哪里、允许哪些运行路径”：

- Goal id、repository 与 active-state 路由；
- local/global runtime root；
- registered Agent identities；
- coordination、write scope 与 guard；
- default-off feature 的配置。

Registry 不证明某个 Host 已经成功启动，也不保存每一轮 Agent 输出。它是连接与策略事实，不是
执行回执。

### 2. Event ledger：发生过什么

[`event_sourced_state_contract_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/event-sourced-state-contract-v0.md)
把 Todo、Gate、run、evidence、projection 和 quota 变化表达为 append-only events。

事件至少需要满足四个不变量：

| 不变量 | 作用 |
| --- | --- |
| Append-only | 新事实追加，不能重写历史来伪装旧动作没有发生 |
| Ordered | replay 能重建相同的生命周期顺序 |
| Idempotent | 同一 `event_id` 与相同 payload 重放不会重复生效 |
| Privacy-partitioned | public-safe 摘要与 local/private payload 不混在同一公开流中 |

例如“Todo 已完成”不是把 Markdown 复选框改成 `[x]` 就结束。合法转换应留下 Todo id、producer、
completion evidence、时间和 event lineage，使 status、review packet 与下一轮 quota 能复用同一
事实。

### 3. Active-state workbench：人可读工作台

`ACTIVE_GOAL_STATE.md` 让人和 Agent 能快速阅读 Objective、Next Action、User Todo、Agent Todo 与
Progress。它是重要的工作台，但不能笼统地理解为“所有真相都在 Markdown”。

在迁移或兼容阶段，Markdown 可能仍参与 Todo 读取；规范写入仍应通过 LoopX lifecycle commands
形成事件或受控 writeback。直接编辑一个被投影出来的段落，不等于完成状态转换。

[`active_state_structured_projection_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/active-state-structured-projection-v0.md)
定义了如何从这个工作台生成 typed、read-only 的 Todo、Gate 与 Next Action 视图。协议明确：

- projection 可以重算；
- projection 不授予写权限；
- generated compatibility id 不等于 migration-ready canonical id；
- duplicate id、缺失 section 等问题应成为 diagnostics，而不是被静默忽略。

### 4. Run history：一轮发生了什么

Run history 保存一轮 bounded work 的紧凑索引，例如：

- 哪个 Agent、Todo 与 Goal 参与了本轮；
- 观察、交付或 blocker 的分类；
- validation 与 evidence refs；
- delivery scale 与 outcome；
- successor、replan 或 no-follow-up；
- 是否满足 spend 条件。

Run snapshot 不是完整 project memory。它回答“这一轮看到了什么、做了什么、证明了什么”，而
Goal lifecycle 仍由 Todo、Gate、events 与 acceptance 组合决定。

富日志、raw transcript 和 verifier tail 可以留在 local/private runtime artifact；公开 projection
只保留足以复核和恢复的 bounded references。

### 5. Status 与其他 projection：当前如何阅读

`loopx status`、`quota should-run`、dashboard、review packet 和 task graph 都是面向不同消费者的
读模型。

它们可以：

- 聚合多个 source facts；
- 压缩大 payload；
- 按 user、agent、CLI 或 operator 视角重新组织；
- 暴露 stale、gap、repair 与 attention signals。

它们不能：

- 发明一个 source 中不存在的 Todo；
- 用展示顺序替代 lifecycle priority；
- 通过修改卡片或图节点绕过 write API；
- 把 stale external observation 当成当前事实。

[`task_graph_projection_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)
尤其强调：图中的 `blocks`、`validates`、`continues` 和 `hands_off_to` 是派生关系，不是新的调度
命令。

## 三种 Ledger：Turn Journal、Goal State、Run History

把“所有记录”都当成同一类状态，会导致“阶段已记录”冒充“业务 transition”。LoopX 区分三种 ledger：

| Ledger | 拥有什么 | 生命周期 | 典型用途 |
| --- | --- | --- | --- |
| Turn journal | 单次事务的恢复信息 | 单次事务 | 恢复一个中断的 bounded segment |
| Goal/event state | 持久 lifecycle transition | 跨 session、跨 Host | 判断当前 frontier、Gate、acceptance |
| Run history/status | 历史的证据索引与投影 | 只读，不可重写 | 复审、replan、handoff 时的上下文 |

**Turn journal** 回答“本轮发生了什么，如果中断如何恢复”。它记录的是单次事务内的临时状态，不是
持久业务事实。把 journal 当 goal state 的典型错误：agent 在 journal 中看到“已进入阶段三”，就认为
goal 已经 transition 到阶段三。但 journal 只记录 agent 有过的意图，只有 goal/event state 才记录
实际完成的 transition。

**Goal/event state** 回答”当前 frontier 是什么，谁可以做什么”。它通过 append-only event 记录
lifecycle transition（Todo 完成、Gate 解决、Vision 更新），并支持跨 session 重建。它是
durable lifecycle fact 的权威来源，quota 将其与 registry/boundary、Todo/Gate、
capability/workspace、run outcomes/history、scheduler context 以及 fresh external fact 一起编译。

**Run history/status** 回答“历史上发生了什么，有什么证据”。它是只读的，不能反向写入 goal state。
run 记录说“这轮测试通过”，不等于 goal state 中对应的 acceptance 已闭合——只有通过 lifecycle
command 写入的 transition 才算。

区分这三者的实践意义：每次写回前，确认要写入的是 goal/event state（transition）而不是 turn
journal（临时记录）；每次读取 decision 前，确认读的是 goal/event state 而不是 run history 的旧
投影。完整三类 ledger 的源码路径和实验见
[Control-Plane Course 第 8 讲](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/)。

## Canonical、Workbench、Projection 与外部事实

四个词必须分开：

| 层 | 典型内容 | 谁能改变 | 能否直接支持状态转换 |
| --- | --- | --- | --- |
| Canonical state | event、typed Todo、Gate resolution、quota spend | LoopX lifecycle writer | 可以 |
| Workbench | active-state Markdown、人工说明 | 受控 writeback 或兼容编辑 | 需要转成规范事实 |
| Projection | status、quota packet、dashboard、task graph | projection builder | 不可以，只供决策读取 |
| External fact | Git commit、PR、CI、cloud resource | 对应外部系统 | 需要 fresh readback/evidence |

“某个页面显示 PR 已合并”可能只是旧 projection；“某次 run 说测试通过”也可能绑定旧 commit。
只有重新读取外部事实并检查 revision、freshness 与 scope，才能把观察用于当前转换。

## 存储介质不是 authority contract

LoopX 当前是 **本地优先** 的控制面：项目 registry、active-state workbench、event/run history 和
runtime state 位于项目或用户本地。这个事实不意味着“Markdown 文件本身就是 authority”，也不
意味着把目录换成数据库就自动获得正确的并发与恢复语义。

[`event_sourced_state_contract_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/event-sourced-state-contract-v0.md)
明确允许 JSONL、SQLite 或其他 local-first append-only 实现，只要它们保持：

- stable event id 与 ordered replay；
- idempotent append；
- projection head 与 event-store head 对齐；
- public-safe、local-private 与 private-pointer 分区；
- Markdown 继续作为 workbench/projection，而不是任意写入口。

[`local_state_write_correctness_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/local-state-write-correctness-v0.md)
当前标记为 public-safe protocol draft。它把更强的写入正确性目标分成
`prepare -> preview -> apply -> record -> project`：

- 同一 `idempotency_key` 不应重复产生逻辑 effect；
- `expected_revision` 不匹配时应 fail closed 或从新 revision 重算非重叠 patch；
- foreign/expired lease 不应被静默清除；
- lock 默认以 Goal 为目标边界，只有单 Todo 且不影响共享顺序时才可更窄；
- 外部写、凭据、production 和 private read 仍需独立 Gate。

当前 Todo lifecycle 命令已经在 active-state file lock 下重读并写回，preview 也会暴露 write
intent。协议文档同时明确：hard idempotency、统一 optimistic CAS 和 lease conflict enforcement
仍按 writer 分阶段 promotion，不能假设所有 writer 已完整执行上述 Draft。

因此，文件、SQLite 或未来 provider 回答的是“字节存在哪里”；event、revision、CAS、lease 与
authority 回答的是“哪次状态转换合法”。

### 当前已发布与仍在设计中的边界

`v0.5.4` 的 shared-authority 工作已经不只是纸面方案：仓库包含 provider-neutral TypeScript
`AuthorityStore` contract，以及 file、NoKV 和 PostgreSQL 的 staged candidate 与 conformance evidence。
但这仍不等于已启用 shared control plane。发布版没有把这些 candidate 接到默认运行时，也没有
提供可直接启用的远端 authority service；安装 Provider 更不会自动改变某个 Goal 的事实源。

`v0.5.4` 之后的 `main` 可能继续出现 default-off shadow、parity、fencing 或 provider-first cutover
切片。它们证明迁移机制，不应倒推成 `v0.5.4` 已交付云端协作。稳定版行为以对应 tag 和 release
notes 为准，实验状态以
[Shared Control-Plane Authority RFC](/loopx/docs/architecture/rfcs/shared-goal-authority-state-provider-v0/)
的当前 stage 为准。

当前可以依赖：

- 本地项目状态与 global registry projection；
- Todo lifecycle writer 的 active-state file lock、preview/readback 和当前已实现的幂等行为；
- registered peer、soft claim、可选 task lease 与独立 worktree guard；
- 不同 Host 通过同一 registry/Goal 读取并受控写回。
- 用于开发和 qualification 的 staged file/NoKV/PostgreSQL candidate；它们不自动获得 runtime
  authority。

当前不应承诺：

- 多台设备自动共享一个在线 authority；
- 离线设备可以新 claim、complete、续 lease 或执行 protected write；
- 把项目目录放进同步盘就得到一致的分布式状态；
- NoKV、数据库或 IM 自动替代 LoopX lifecycle owner。

如果要实现跨设备控制面，应保留一个 canonical LoopX authority，要求 revision-bound、幂等的受控
命令与 receipt，并把消息传递、上下文记忆和状态 authority 分开。直到 shared mode 经过独立
promotion、运行时接线和发布验证，Dev Book 只教授这些协议边界，不提供“云端模式已可用”的操作
步骤。

## 历史产物的三层完整性

LoopX 可以让研究、验证和决策产物不被静默改写，但这不等于旧结论永远适用于当前状态。判断一条
历史 evidence 能否进入当前决策，要分三层：

| 层次 | 需要回答 | 典型检查 |
| --- | --- | --- |
| Lineage integrity | 这条产物来自谁、何时生成，是否被追加、纠正或 supersede？ | `event_id`、`run_id`、producer、recorded revision、append-only refs |
| Current applicability | 它支持的输入、范围和外部事实与当前问题仍一致吗？ | commit、target key、source revision、time window、Gate scope、fresh readback |
| Supersession | 后来的 evidence 或决定是否替代、收窄或撤销了它？ | `supersedes`/`superseded_by`、compensating event、newer decision、replan delta |

因此，append-only lineage 解决的是**防止历史被无声重写**，不是自动证明**旧结论仍然新鲜**。
研究笔记、测试结果或 PR readback 要进入当前 frontier，至少应带稳定 join key，并在 material input
变化后重新验证 applicability。无法确认时，把它标为 historical observation 或 stale evidence，
不要删除历史，也不要继续把它当作 current authority。

[`agent_scoped_evidence_ledger_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/agent-scoped-evidence-ledger-v0.md)
提供 bounded、read-only 的 Agent chronology，适合 replan 和 handoff；它不替代 current status、
quota decision 或外部系统 readback。

## Replay 不是复制旧结论

Replay 的目标是从有序事实重建当前状态，而不是永久保留旧判断。

假设事件流记录：

```text
todo_added(T1)
todo_claimed(T1, agent-a)
gate_added(G1, scope=public_claim:action:homepage)
run_recorded(R1, tests_passed_at=commit-a)
```

随后 Git 前进到 `commit-b`，用户又改变首页方向。Replay 仍能说明 R1 和 G1 曾经存在，但不会自动
证明：

- R1 对 `commit-b` 仍有效；
- G1 已覆盖新的首页方案；
- agent-a 仍在当前 workspace 执行；
- 当前 frontier 可以继续发布。

恢复者必须把 replay 后的 project facts 与新鲜环境重新组合。

## Projection gap 是控制面故障

当 source 与读模型不一致时，不能任选一个看起来方便的表面继续：

- event 中有 open Todo，status 却没有；
- Gate 已解决，quota 仍显示 operator wait；
- active state 有 Next Action，但对应 Todo 不存在；
- dashboard 显示 runnable，workspace guard 却指向另一个 worktree。

这些情况属于 **projection gap**。正确动作是：

1. 找到 authoritative source；
2. 判断是 source 写入失败、projection stale、migration drift 还是 external observation 过期；
3. 通过原 lifecycle/writeback 路径修复；
4. 重算 projection 并验证 source revision；
5. 在修复前不运行依赖该状态的交付。

手工把多个展示面改成一致，只会隐藏问题。

## 如何决定一个新字段放在哪里

新增字段前按顺序问：

1. 它描述长期配置、身份或路由吗？放 registry。
2. 它描述一次 lifecycle transition 吗？放 event。
3. 它描述一轮观察或交付吗？放 run snapshot/evidence。
4. 它只服务某个读者视角吗？从现有事实生成 projection。
5. 它属于 GitHub、CI 或其他系统吗？保留外部 authority，只存 bounded readback。
6. 它是 Issue-Fix、Explore 等领域专属结果吗？放 Domain State，不要塞进通用 Todo/Quota。

如果一个字段同时想承担配置、事件、展示和权限四种责任，通常说明协议边界还没有拆清。

## 协议阅读入口

本章拥有概念顺序，不复制完整 schema。需要修改 LoopX 状态行为时，优先阅读：

- [`event_sourced_state_contract_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/event-sourced-state-contract-v0.md)：
  event、replay、ordering、privacy；
- [`active_state_structured_projection_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/active-state-structured-projection-v0.md)：
  Markdown workbench 的 typed read model；
- [`task_graph_projection_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/task-graph-projection-v0.md)：
  Todo、Gate、evidence 与 handoff 的只读关系图；
- [`long_horizon_agent_state_protocol_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/long-horizon-agent-state-protocol-v0.md)：
  长程工作中的 source/projection、并发 Agent 与 lifecycle；
- [`agent_scoped_evidence_ledger_v0`](https://github.com/loopx-project/loopx/blob/main/docs/reference/protocols/agent-scoped-evidence-ledger-v0.md)：
  replan/handoff 前的 Agent-scoped chronological read model；
- [Status Data Contract](https://github.com/loopx-project/loopx/blob/main/docs/status-data-contract.md)：
  operator 与 Agent 读取的聚合表面。

如果你准备修改 registry、event、Domain State、replay 或 projection builder，继续阅读
[Control-Plane Course 第 4 讲](/loopx/docs/development/control-plane-course/04-state-substrate/)。
它从 Issue-Fix、Auto ML 与 Auto Research 的事实归属进入源码路径和实验；本章继续作为外部
开发者的概念入口。

下一章在这套状态底座上建立工作图：谁可以做什么、什么条件阻塞它，以及一项工作如何合法地
继续、交接或结束。
