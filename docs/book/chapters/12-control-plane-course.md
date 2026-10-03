# Control-Plane Developer Course

> 面向准备修改 LoopX Kernel、CLI、状态投影、调度或扩展能力的开发者。

## 与 Dev Book 的关系

Dev Book 给外部开发者一条“从机制模型到接入/贡献”的完整路径；Control-Plane
Developer Course 是独立章节，面向需要进入源码实现、判断规则优先级、定位 bounded
context 或新增一条控制面规则的开发者。

两者共享官方协议与源码事实，但不维护两份完整课程：

- Dev Book 讲清楚预测行为所需的机制；
- Course 提供 Showcase 推导、decision table、源码领读、实验与 review 问题。

## 课程地图

| 课程章节 | 主题 | 适合在读完 Dev Book 哪部分后进入 |
|---|---|---|
| [概念导读：先把 LoopX 放进一张图](/loopx/docs/development/control-plane-course/00-concept-primer/) | 有限上下文、外置状态与核心概念总图 | [四个要求](02b-long-horizon-requirements.md) |
| [长程任务如何收敛](/loopx/docs/development/control-plane-course/topic-long-horizon-convergence/) | 方向、证据、Delta、活性与终局不变量 | [恢复与运行边界](04-runtime-boundaries.md) |
| [第 1 讲：Harness 是 effectful program](/loopx/docs/development/control-plane-course/01-agent-loop-effectful-program/) | effect interpreter 心智模型，以及三类 adapter 如何复用 typed settlement algebra | [一轮受治理的工作](03-one-turn.md) |
| [第 2 讲：从三个 Showcase 理解 LoopX 架构](/loopx/docs/development/control-plane-course/02-goal-control-plane-architecture/) | Agent / Provider / Capability / Kernel 分工 | [会话、Goal 与 LoopX](02-session-goal-loopx.md) |
| [第 3 讲：从 Showcase 到第一次真实 Loop](/loopx/docs/development/control-plane-course/03-first-real-loop/) | guided start、todo、quota、refresh、spend | [连接项目](05-connect-existing-project.md) |
| [第 4 讲：状态底座与可重放事实](/loopx/docs/development/control-plane-course/04-state-substrate/) | registry、event、active state、run history、projection | [持久状态](state-substrate.md) |
| [第 5 讲：Todo 工作图与 Peer 协作](/loopx/docs/development/control-plane-course/05-work-graph-and-peers/) | claim、lease、handoff、equal peer | [工作图与权限](work-graph-and-authority.md) |
| [第 6 讲：Quota 决策内核与 Interaction Contract](/loopx/docs/development/control-plane-course/06-quota-decision-kernel/) | `should-run`、route、mode、interaction contract | [一轮受治理的工作](03-one-turn.md) |
| [第 7 讲：Host、Heartbeat 与 Stateful Backoff](/loopx/docs/development/control-plane-course/07-host-scheduler-and-heartbeat/) | execution context、RRULE、ACK、backoff | [预算、准入与观察](04b-budget-and-admission.md) |
| [第 8 讲：证据、Refresh 与 Self-Repair](/loopx/docs/development/control-plane-course/08-evidence-refresh-and-self-repair/) | material progress、replan、repair delta | [恢复与运行边界](04-runtime-boundaries.md) |
| [第 9 讲：如何给 Control Plane 增加一条规则](/loopx/docs/development/control-plane-course/09-engineering-a-control-plane-rule/) | invariant、ordered rules、schema、smoke | [修改规则](source-change-control-plane-rule.md) |
| [第 10 讲：Agent 自主写代码时的分层质量门禁](/loopx/docs/development/control-plane-course/10-autonomous-agent-quality-gates/) | 确定性测试、canary、模型行为、release gate | [验证到 PR](source-validation-to-pr.md) |
| [第 11 讲：扩展层、Governed Execution、Explore 与领域产品](/loopx/docs/development/control-plane-course/11-extension-layer/) | 外部 effect 的可恢复结算、默认关闭的 Graph/Harness 与领域产品 | [Extension 生命周期](10-extension-lifecycle.md) |

## 与 Effect Interpreter RFC 的关系

课程第 1 讲与
[Agent Loop Effect Interpreter RFC](/loopx/docs/architecture/rfcs/agent-loop-effect-interpreter-v0/)
共用同一套语言：harness 是 agent loop 外面的 effectful program，状态机只是
interpretation table。进入 Kernel 实现前，建议先读第 1 讲，再按需要进入后续专题。

## 从“读懂”到“能够判断” {#reader-checkpoints}

采用[阅读导引的四问标准](00-reading-guide.md#judgment-standard)：**依据什么事实，为什么允许或拒绝，哪项证据支持，下一步找哪个入口。** 回答“重试一下”没有交代其中任何一项；回答“先停住”也要说清缺少什么、由谁补齐，以及何时重新判断。

若尚未完成一次实际工作，先沿[项目接入与第一份交付](05-connect-existing-project.md#first-delivery)学习，
这里的测试用于解释和贡献，不替代真实操作。只操作自己的项目，可以先读[现场查阅入口](#read-before-change)和[故障分流](#diagnostic-routing)。准备修改实现，再运行下面四组练习：先预测，再检查断言，最后写出带条件的下一步。不必学完整门 Course，也不必创建新的 Goal、验收协议或提交练习笔记。

### 环境与证据范围 {#checkpoint-environment}

测试命令在完整 LoopX 源码 checkout 的根目录执行，不在业务项目中执行。需要 Python 3.11+、Node.js 22.22.3+、uv 与 test extra；安装依赖可能访问包源。先记录实际 checkout：

```bash
git rev-parse HEAD
python --version
node --version
uv sync --extra test
```

前三组使用临时目录中的真实本地状态或 CLI，第四组使用构造的 quota 输入。它们会写测试状态，也可能启动本地 Effect runtime。不能把 fixture 的 registry、删除或损坏注入步骤替换成 live Goal。缺依赖、收集失败、零项测试或 runtime 不可用，都不等于行为通过。

源码链接固定到核对过的提交，命令运行你记录的 checkout。先核对版本差异；测试改名时沿原 invariant 找 owner，不把新输出直接抄为期望。这些测试不是模型、真实 Host、远端 provider 或全产品资格验证。

### 先读取，再决定是否改变状态 {#read-before-change}

日常诊断的完整命令与停止条件已集中到[附录：现场读取](appendix-reference.md#read-before-change)。本章保留练习，不再维护第二份操作 runbook。下面的 pytest 使用隔离 fixture，不能当作 live Goal 初始化或修复步骤。

<!-- reader-checkpoint:state:start -->
### 检查一：哪个状态可以用于准入？ {#checkpoint-state}

先预测：选定 authority 中的 T1 已经 done 或 blocked，但 Markdown 显示 open。可以执行吗？反过来，页面没显示某项工作，就说明它不存在吗？

#### 事实依据 {#state-facts}

需要确定同一个 Goal 选择了哪个 authority、精确 Todo 的当前状态，以及当前 Agent 的约束。展示内容和源状态的角色取决于模式：已提升的 File/SQLite 路径不能用旧 Markdown 替换权威答案，legacy 路径则仍可能以 Markdown 为源。

#### 允许或拒绝的理由 {#state-reason}

拒绝来自当前源状态或读取失败，不是页面措辞。done/blocked 不能因缓存显示 open 而重新获得准入；authority 不可读也不能证明工作开放。另一方面，显示截断不是拒绝依据：真实存在、当前满足条件的 Todo，可以经精确选择进入本轮。

#### 支持结论的证据 {#state-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_stale_markdown_cannot_admit_terminal_or_blocked_work \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_failed_canonical_read_cannot_fall_back_to_markdown \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_exact_selection_reaches_work_beyond_display_limits
```

[原测试](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_quota_authority_settlement_journey.py)的第一项断言 `decision=skip`、没有 selected Todo、receipt 为 `not_committed`；第二项断言没有选中项且本轮 receipt 数为零。第三项覆盖 legacy/File/SQLite：精确选择得到 `decision=run`，selected Todo 与 settlement identity 都绑定目标 id，即使它超出展示范围。

这证明特定路径的来源与选择约束，不证明 provider 已恢复、所有 legacy 路径只读投影，或仅凭 Todo open 就能执行。

#### 下一步入口与停止条件 {#state-next}

先用[精确读取](#read-before-change)确认来源和状态。源为 done 时沿现有后续工作继续，不为让教程跑通而重新打开；blocked 时回到[依赖与权限](work-graph-and-authority.md)检查阻塞条件。源不可读时，由[状态 owner](state-substrate.md)定位不可用原因，期间不以 Markdown 接管。只有来源恢复且当前条件重新检查后，才通过原 Host 的准入入口继续；“页面又能显示”不构成放行回执。
<!-- reader-checkpoint:state:end -->

<!-- reader-checkpoint:lease:start -->
### 检查二：历史成功是否仍是执行权？ {#checkpoint-lease}

先预测：A acquire 后 renew，再重放原 acquire 请求，历史 receipt 和当前 lease 是否应该相同？release 后复用旧 key，又意味着什么？

#### 事实依据 {#lease-facts}

分别读取当前 lease 与 original receipt，核对 Goal/Todo、owner、模式、状态、version 与 execution identity。本例仅覆盖 File/SQLite 的 `hard_lease` fixture；同名 Agent 和过去的成功都不是当前执行实例的充分证明。

#### 允许或拒绝的理由 {#lease-reason}

返回历史结果不应复活旧权限。renew 后仍需使用当前 lease 的证明；release 后旧 acquire key 已退役，不能把同一请求变成新的执行。新的 key 也不是绕过拒绝的方法：只有新的工作准入、当前归属和版本条件均满足时，才能获取新 lease。

#### 支持结论的证据 {#lease-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_canonical_lease_acquire.py::test_public_acquire_renew_complete_and_retired_retry
```

[原测试](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_canonical_lease_acquire.py)断言 renew 后 acquire 读回当前 lease，同时保留 original receipt；release 后复用旧 key 得到 `idempotency_key_reuse`。新的合法 acquire 推进版本，完成后 Todo 为 done、lease 为 released；对已完成 Todo 再 acquire 得到 `todo_not_open`。真正完成后 `state.exists()` 为真，并非所有状态文件都被删除。

这是顺序执行的测试，不证明 soft-claim 的强互斥、TTL 到期后的真实竞争，或外部服务能够拒绝旧执行者的写入。

#### 下一步入口与停止条件 {#lease-next}

从[lease inspect](#read-before-change)开始，而不是重新 acquire。当前 owner 不符或工作已结束时停止这条写入路径，交回[claim/lease 与 lifecycle owner](work-graph-and-authority.md)处理；不要替其他执行者续租。确需新执行时，由当前合法入口申请新的证明，随后核对 Todo 与 lease 的读回。保留旧 receipt 用于解释历史，不把它改造成新授权。
<!-- reader-checkpoint:lease:end -->

<!-- reader-checkpoint:settlement:start -->
### 检查三：记录不完整时，应重做哪一部分？ {#checkpoint-settlement}

先预测：T1 的写回和 quota 扣减已经存在，结算回执却缺失。应该重做 T1、再次扣额，还是恢复记录？

#### 事实依据 {#settlement-facts}

需要原 Goal/Agent/Todo/Turn 的完整绑定、已完成写回、扣减记录、回执及 settlement 状态。只有“命令超时”这一项观察，无法判断这些事实；不要把回执缺失自动等同于没有扣减。

#### 允许或拒绝的理由 {#settlement-reason}

已确认的工作应保留；本例修复的是原操作的记录完整性。`spend_required` 和 `spend_receipt_required` 表示不同的待完成步骤，不能统一处理成再运行 Host。另一种情况是外部 effect 是否提交仍未知，它必须进入[未知结果的确认](#unknown-outcome)，不能套用本例。

#### 支持结论的证据 {#settlement-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_quota_authority_settlement_journey.py::test_returned_command_settles_and_repairs_receipts_without_another_debit
```

[原测试](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_quota_authority_settlement_journey.py)先从 `spend_required` 到 `settled`，随后仅在 fixture 中移除对应回执。再次写回得到 `spend_receipt_required` 与 `recovery_does_not_spend=true`；执行返回的命令后 `appended=false`，扣减记录仍为一次。最终再次检查达到 `settled` 且没有 `settlement_owed`，下一轮不再卡在这项结算恢复上。

这些断言证明本例的内部结算恢复，不证明供应商账单为零、不确认其他未知 effect，也不关闭 G1 或接受整个 Goal。测试通过本身不是 live Goal 已恢复的证据。

#### 下一步入口与停止条件 {#settlement-next}

沿[Turn 的结算路径](03-one-turn.md)和[原身份的恢复入口](04-runtime-boundaries.md)检查当前状态。当前可信 LoopX 响应给出 `settlement_owed.command` 时，先核对它的 registry/runtime 与原身份，再按既有授权执行；不要截掉绑定参数、从旧聊天复制命令或把任意日志字符串交给 shell。

操作后重新核对 settlement 与是否重复扣减，不以退出码替代读回。响应缺少绑定、身份不匹配或结果仍未知时停止，交给原 transaction/provider owner 确认。不要手工删除回执来“触发修复”，也不要把 `refresh-state` 当作无写入查询。
<!-- reader-checkpoint:settlement:end -->

<!-- reader-checkpoint:monitor:start -->
### 检查四：没有变化就一定重规划吗？ {#checkpoint-monitor}

先预测：当前 Agent 的 monitor lane 连续五次无变化，peer 还有工作。当前 Agent 应继续等吗？它自己有 advancement，或 Monitor 显式为 watch-only 时呢？

#### 事实依据 {#monitor-facts}

读取当前 Agent 的 lane、可选 advancement、Monitor 的 `consecutive_no_change`、`watch_only`、target、due 与实际 Host 活性。peer 正在忙不能替代当前 lane 的事实；没有收到通知也不能证明外部源没有变化。

#### 允许或拒绝的理由 {#monitor-reason}

是否产生这项 replan obligation，要同时考虑计数、归属和可选工作。这里的五次是具体策略阈值，不是所有等待的定理；current-Agent advancement 可以优先，显式 watch-only 也不受同一重规划触发约束。不产生 obligation 不代表一定有 Host 会醒来，更不自动授权一次写入或交付扣额。

#### 支持结论的证据 {#monitor-evidence}

```bash
uv run --extra test pytest -q \
  tests/control_plane/test_monitor_replan_agent_scope.py::test_interleaved_monitors_keep_independent_no_change_streaks \
  tests/control_plane/test_monitor_replan_agent_scope.py::test_current_agent_advancement_still_preempts_monitor_streak_replan \
  tests/control_plane/test_monitor_replan_agent_scope.py::test_watch_only_monitor_streak_does_not_create_replan_obligation \
  tests/control_plane/test_settled_replay_construction.py::test_settled_replay_preserves_schedule_without_new_host_effects
```

[原测试](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/tests/control_plane/test_monitor_replan_agent_scope.py)分别断言：只有符合条件的当前 Agent lane 产生 `monitor_no_change_streak`；有当前 advancement 时为 `run` 且没有该 obligation；watch-only 即使 streak 为 50 也不因此触发。它们预置计数器后执行决策，没有交错发送远端 poll，不证明计数写入并发、真实唤醒或 backoff 时间。

这里同时给出一个直接反例：**已结算 Turn 的回放**。该测试在仍有 open advancement successor 时返回
`should_run=false`，但 `scheduler_hint.action` 是 `preserve_current_schedule`、`next_trigger` 是 `fresh_turn_identity`，host action 为 `none` 且不需要 ACK。所以 `false` 的
出现次数或本 Turn 的 receipt 都不能推出"该放慢 cadence"；放慢要等 owner 判定 frontier 确属等待，
再走第 4 条的四步。

#### 下一步入口与停止条件 {#monitor-next}

用[精确 Todo 读取](#read-before-change)定位 Monitor，再到[观察与调度](04b-budget-and-admission.md)核对等待条件。到期后也须由可用 Host 重新判断，不凭旧 `should_run` 开跑。有可选工作时交回当前准入；需要 replan 时沿返回的义务处理；无观察能力或 Host 不运行时，记录这个缺口并走[Host 接入](05-connect-existing-project.md)，不伪造检查时间或 ACK。

不要为了消除告警把普通 Monitor 改成 watch-only。等待策略变更应符合真实目标并经现有配置入口接受；修改后核对新的 due、条件和实际执行面，而不是只看告警消失。
<!-- reader-checkpoint:monitor:end -->

## 结果未知：先确认，不是先选一种重试 {#unknown-outcome}

假设外部写入已经发出，随后响应丢失。现在只有超时和 prepared 意图，缺少能确认结果的读回。**不知道是否发生**与**已经确认没发生**是不同事实；换 operation id 再发一次可能制造第二次效果。

在当前 Turn settlement 的
[`_resolve_prepared_effect`](https://github.com/loopx-project/loopx/blob/67930ab6af78491f10ca3de4ff74ef7a39954a51/loopx/control_plane/turn_driver/settlement.py)中，没有 resolver、resolver 异常或 unsupported kind 都不能授权执行；有效 committed payload 可供复用，`absent` 才进入未提交分支。这个局部判断仍受外围 journal、身份与恢复条件约束，不是所有外部系统的统一恢复保证。

| 原身份的读回 | 允许或拒绝的理由 | 下一入口及需要取得的证据 |
| --- | --- | --- |
| 已确认 committed，回执有效 | 不应重复已提交效果 | 原 settlement/recovery 路径复用结果；读回确认余下步骤 |
| 已确认 absent | 不再以“可能已提交”为依据阻塞，但其他 guard 仍需通过 | 同一恢复路径重新核对当前授权与绑定，再执行尚欠步骤 |
| unknown、不可用或相互矛盾 | 没有安全重试或宣告完成的充分依据 | 原 provider/transaction owner 恢复读回；保留身份，明确缺失项 |

这里给出的是源码依据，不声称本章的四组测试已经覆盖所有 unknown 分支。需要人工处理时，也必须留下对原效果的明确结论；“人工看过了”不能替代读回，更不能自动变成发布权限。

## 用四项检查定位真实问题 {#diagnostic-routing}

真实项目的[症状分流](appendix-reference.md#diagnostic-routing)统一放在附录；操作读者不需要先运行源码测试。练习与正文的主要关系分别是[状态](state-substrate.md)、[权限](work-graph-and-authority.md#authority-layers)、[结算](03-one-turn.md#settlement-recovery)和[观察](04b-budget-and-admission.md#observation-owners)。

## 综合练习：一次交付为什么仍未获准？ {#integrated-judgment}

回到[贯穿任务](00-reading-guide.md#running-example)。以下是合成推演，不是新增产品 fixture：T1 在 C1 上验证过且已结算；随后代码变成 C2。M1 仍只有 C1 的绿色 CI 观察，G1 的发布批准尚未完成，T2 有可独立检查的文档工作。

| 四问 | 足够具体的答案 |
| --- | --- |
| 依据什么事实？ | 当前目标产物是 C2；现有测试/CI 证据绑定 C1；发布 scope 尚未批准；T2 的独立性仍需当前条件确认 |
| 为什么允许或拒绝？ | R1 与结算证明历史工作，不覆盖 C2 或授予发布权；因此不能据此执行 T3。T2 不必被无关 Gate 全局冻结，但仍受自己的权限与能力约束 |
| 哪项证据支持？ | 当前 revision 的验证与外部读回、覆盖正确对象/scope 的明确决定，以及当前工作的读回；单独一张绿色截图不足以组合成批准 |
| 下一步找哪个入口？ | 经现有验证/Monitor 路径获取 C2 的证据；由维护者通过已有 Gate/Workspace 决定入口处理 G1；独立工作交回当前准入。条件满足后重算 T3，而不是重做已确认的 C1 工作 |

[权限](work-graph-and-authority.md)、[恢复](04-runtime-boundaries.md)与 [Workspace](workspace-v1.md)分别拥有这些操作说明。Owner 决定不统一由自动 Turn 的 quota 授权；运行某个内部测试也不证明本次真实 CI 或批准已经发生。

将任务换成“依据两份公开材料完成比较报告”，仍能使用同一判断：材料版本替代 commit，方法与引用检查支持结论，接受人与发布范围需要明确。材料更新后重新检查受影响的分析，不把全部旧讨论变成新证据；报告写完也不自动授权发布。这是概念迁移练习，不宣称另一条产品旅程已经验证。

### 再加一个条件：原 Turn 尚未收口

若 T1 还欠原身份的等待写回，“T2 独立”并不允许同一 Turn 改绑结算身份。先解释原 Turn 怎样收口，再解释新的准入怎样选择 T2。进阶测试与版本边界见[等待时序](03-one-turn.md#wait-closeout)；上面八个测试函数是四组练习选定的范围，不是完整源码的测试清单。

再检查协作：A 和 B 各自通过验证，不足以验收组合结果；按[交接到集成](work-graph-and-authority.md#handoff-to-integration)指出采用版本、集成验证、发布决定与最终接受者。

## 何时算读懂、何时算需要修订 {#judgment-exit}

能给出一个带条件的结论，并指向支持证据及下一入口，才完成这项阅读练习。证据不够时，写明“尚不能判断 X，缺少 Y，应由 Z 确认”；这比猜成功或笼统要求重跑更有用。

贡献时把四问压进现有 Issue/PR 的说明：当前事实、规则与反例、实际验证、合法后续及未验证边界。沿[修改规则](source-change-control-plane-rule.md)与[验证到 PR](source-validation-to-pr.md)完成，不增加独立审批表或第二套任务账本。章节缺少其中一环时补那一环，不靠增加术语掩盖断点。

配套维护测试只检查练习结构、测试选择器和链接锚点是否仍然存在；它不会判断推理是否正确、两种语言是否语义等价，或真人是否已经学会。那些仍需行为验证、双语审校和实际阅读反馈。
