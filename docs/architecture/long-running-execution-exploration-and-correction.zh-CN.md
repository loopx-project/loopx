# 长时执行、探索与纠偏的衔接

[English](long-running-execution-exploration-and-correction.md)

长时任务既需要连续推进，也需要在合适时机改变方向。有效的集成让 Host
连续完成有意义的工作片段，把新证据送回既有 LoopX 决策边界，再验证后续工作，
避免每个工具结果都引发一次完整规划。

本文是实现地图与集成指南，基于 LoopX 源码
`3dd3c550afecfa0c525a34e8744a3cf06774ac94`；2026-10-14 从首轮实现比较的
`b77f63495f2619237380755178c0780ae10a0f26` 刷新。外部源码仍固定到 2026-10-13
核验的版本，不代表各项目最新分支。
它衔接现有 RFC，不创建新策略、schema、调度器、默认行为或执行授权。
源码存在、安装后的行为、模型采用和实测收益分别成立。

复核周期文案另与 main `3300dfdd29fe6e53e1aa4a0282ca3952db6e9e8f` 对齐：
启用 Goal 策略时继承实时复核周期，仅显式 Goal 周期覆盖会固定它。下文中的实现
候选与上述已合并源码快照分开记录。

## 比较方法与证据范围

对每个仓库追问同一组问题：谁准入工作，什么驱动模型/工具循环继续，纠正何时进入
输入，谁决定调查或完成，哪些状态能跨中断保留，下一次决策实际读取什么。
功能名和 README 描述不足以证明这些连接；下面从实际 caller 追到状态 owner，
再检查已有失败用例。源码行为、解释与拟借鉴方案分别说明。

| 来源 | 固定版本 | 阅读范围 |
| --- | --- | --- |
| LoopX | `3dd3c550afecfa0c525a34e8744a3cf06774ac94` | quota/replan、monitor、Explore 回写、Turn 恢复、工作区 steering、managed ZCode native Goal 与 direction M1 builder |
| Grok Build | `2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8` | 公开 Rust CLI/runtime 的 Goal controller、插入消息与快照恢复 |
| Codex | `9167a0b2d4a74d44a4d388057d304682e47313c6` | 公开 Rust turn loop、Goal extension、压缩与 Agent 通信 |
| OpenCode | `be674e3158abb5f1b0c16b2bdeb3a9ba2b072774` | Session 准入/inbox、执行协调、重启和子任务结果投递 |
| Pi | `e6220e1316f86af818a0ccbfe1c09d7e918b41cf` | Agent core 与 coding-agent 队列、上下文刷新、会话持久化和扩展 |

引用的外部测试只读了源码，本轮没有运行；文档改动实际执行的 LoopX 验证在 PR 中
披露。没有真实模型或同条件成本实验。Provider 模型调用、Host 回合和 LoopX 的
accountable Turn 是不同单位，不能直接拿次数横向比较。

主干刷新改变实现地图，不改变外部比较，也不证明部署就绪。新增的具体能力包括
native Goal provider 和 Explore 页来源重放；direction M1 builder 也已存在，但
没有生产 consumer 或回执 writer。版本化验收和显式 result-use 已有版本检查，
后续应验证真实路径如何使用这些 owner，而非重新实现。

## 现有归属与已连接的路径

[整体路线图](rfcs/loopx-overall-roadmap-v0.zh-CN.md)保留优先级：S3 规划与协作、
S4 Host、S6 证据、S7 调度与成本，以及 S10–S11 可靠性与评测。
各项决策继续由以下文档负责：

| 边界 | 现有归属 | 集成要回答的问题 |
| --- | --- | --- |
| 工具反馈、交付结算、权限变化 | [分层步幅](rfcs/hierarchical-agent-stride-control-v0.zh-CN.md) §5–7 | 遇到重要反馈或权限边界之前，可以连续完成多大的有意义工作？ |
| 长任务结果与唤醒 | Stride §7.5；既有 Host/provider 与 monitor | 哪个确切结果版本值得再次判断，丢失通知后怎样恢复？ |
| 探索与路线选择 | [研究探索](rfcs/research-exploration-control-plane-v0.zh-CN.md) §11.5–11.6；[Explore](../../loopx/capabilities/explore/README.zh-CN.md) | 哪个未知可能改变下一步，什么证据能区分候选方案？ |
| 原始要求与来源变化 | [共享 Goal 对齐](rfcs/shared-goal-alignment-and-governed-amendment-v0.zh-CN.md) §3.3、§3.7–3.8 | 新路径是否保留当前要求、证据范围及其他 Agent 的工作？ |
| 纠正、交接与原会话返回 | [协调员运行契约](rfcs/capable-manager-semantic-handoff-v0.zh-CN.md) §10；[团队工作区](rfcs/live-team-workspace-v0.zh-CN.md) §9 | 纠正是否被接收、采用、执行、独立检查，并返回原入口？ |
| 恢复与性能 | [故障控制](agent-failure-containment-and-recovery.zh-CN.md)；[优化指南](../development/testing-and-quality.md#roadmap-aligned-optimization) | 哪个原操作可以继续，实际成本发生在哪里？ |

源码中的控制链如下。分支按条件进入，不要求额外模型调用依次走完全部步骤：

```text
Goal 身份、意图与适用验收合同
  → Agent Vision / 保留要求 / 路线
  → Todo / 显式依赖 / 当前工作边界
  → Core quota、claim、lease 与动作准入
  → 可记账 LoopX Turn 或经资格验证的 native Goal 集成
  → 所选 Host session / native target / 工具批次
  → 原操作 journal、效果与产物
  → 当前 source-use 检查、独立验证与应做结算
       → 合格的接续工作：继续推进
       → 相关未知或反证：探索后重新判断
       → 已准入复核：履行既有来源绑定义务
       → 候选完成：执行所属验收检查
       → 等待 / 中断 / 停止：沿既有生命周期处理
  → 被接受的决定与之后工作进入下一次复盘
```

这是职责地图，不声称每个 Host 都经过相同事务。Goal 可跨会话，会话可承载多个
Turn；native Goal 完成先是 Host 观察，不能代替适用的 LoopX 验收。
分别回答当前在执行什么、为什么允许继续、新输入何时消费、证据是否仍可用、
谁有权关闭 Goal，不能用一个 `ready` 或 `complete` 回答五个问题。

### LoopX：从发现缺口到选择下一段工作

目的：改变无效路线，同时保留原 Turn 的结算和其他 Agent 的工作。真实 caller 链为：

```text
规范 run/Todo 事实
  → projectReplanHistory 与 Goal-frontier 规则
  → build_replan_context → projectReplanContext
  → build_replan_action_packet → CLI / heartbeat / TurnEnvelope
  → Agent 回写 path / progress / successor
  → replan_semantics 验证 → 持久 Vision/ACK
  → 原 Turn 结算
  → 新 quota：确切后继 + 当前执行资格
  → evaluateDeliveryRoute → 下一次获准 Host 执行
```

[`replan_history.ts`](../../loopx/control_plane/work_items/replan_history.ts)
按 Agent 限定历史，在本 Agent 已接受的 ACK 处截断，并对计数的 Turn 身份去重。
`periodicTrigger` 计合格的已结算工作；monitor 存活记录只有实质变化才算工作。
`progressTrigger` 根据重复 typed observation 触发；`futureBlockingMonitor`
复用已有 resume planner，避免把合法外部等待判成停滞。这些是检测规则，不是模型
对科学效用的估计。

[Goal frontier caller](../../loopx/control_plane/goals/goal_frontier/__init__.py)
实际构造 context，[quota](../../loopx/control_plane/quota/should_run_packet.py)
生成 action packet 和精确命令，[TurnEnvelope](../../loopx/control_plane/quota/turn_envelope.ts)
保留结构化复核及其义务。回写时，[`replan_semantics`](../../loopx/control_plane/work_items/replan_semantics.ts)
检查该来源允许的 outcome；[`stride_policy.ts`](../../loopx/control_plane/work_items/stride_policy.ts)
中的 `strideReviewMismatch` 拒绝漏掉已投递的上一决定复核、引用其他证据或不兼容
Explore 依据。它验证声明的来源与结构，不认证 Agent 解释为真。

新 quota 从 canonical Todo 读取 ACK 的确切后继，核对当前 actionability/capability，
再调用 [`evaluateDeliveryRoute`](../../loopx/control_plane/turn_driver/delivery_continuity.ts)。
`prefersReplanSuccessor` 要求同一 Agent、已接受 ACK、精确 origin obligation 和当前
合法交付边界。它能改变下一项 primary task，但不授予 lease，也不在旧 Turn 内执行。
[后继重入测试](../../tests/control_plane/test_replan_successor_guard_reentry.py)
通过真实 File/SQLite CLI 覆盖结算、新 Turn 选择和后继失效；选对任务仍不等于采用方法。

### LoopX：从探索结果到后续判断

目的：保留可复用发现，同时不让技能报告变成第二份权威，也不因关联失败重复已完成工作。

```text
普通获准工作 + 显式 Explore 附件
  → 主回写前验证范围和输入
  → 已提交记录 → deliver_result_attachment
  → 幂等 graph event → canonical Todo 证据关联
  → explore_turn_context / 已开启 replan 的 Explore 读取
  → 带证据的 explore_review 和下一段任务决策
```

[`deliver_result_attachment`](../../loopx/capabilities/explore/result_writeback.py)
消费已提交主记录与纯 post-writeback intent。图投递或 Todo 关联失败时，仍保留
`primary_committed` 并返回可重试的投递结果。精确重放补齐缺失关联；已成功投递只回读，
未完成投递仍受 claim/lease 约束。[关联捕获测试](../../tests/control_plane/test_explore_linked_capture.py)
使用当前 canonical scope，拒绝相信过期共享投影，并在主提交前拒绝非法附件。
[结果回写测试](../../tests/control_plane/test_explore_result_writeback.py)还描述部分关联失败
的重放，以及较新正面观测出现时保留相关反证；这些仍不同于模型使用证据。

[`explore_turn_context`](../../loopx/capabilities/explore/turn_context.py)先验证完整证据日志，
再返回有界相关详情和版本绑定的下钻入口；replan 可经 `explore_review` 绑定这些发现。
另外，[`decisionFollowthrough`](../../loopx/control_plane/work_items/replan_context.ts)
用合格的追加顺序连接最近 accepted decision 与随后进度，保留省略数量和
`execution_and_goal_gain=unknown`。[三轮 CLI 测试](../../tests/control_plane/test_long_chain_projected_closeout.py)
执行复盘、由测试驱动的合成工作、再次复盘，并拒绝缺少 `previous_review` 的回写。
它证明传输和结算链已连接，不能证明真实模型选择或执行了更好的实验。

### LoopX：纠正投递、恢复与可见证据

工作区 steering 已在发送前保留精确 Session/Turn/文本/请求身份。
[`retainSteeringRequest`](../../apps/presentation/dashboard/src/features/personal-workspace/steering-recovery.ts)
检查持久回读；[输入框](../../apps/presentation/dashboard/src/features/personal-workspace/personal-workspace-page.tsx)
在保存失败时不发送，响应未知的重试即使原 Turn 已结束也保留原身份；已确认未投递则
走不同恢复路径。[存储测试](../../apps/presentation/dashboard/src/features/personal-workspace/steering-recovery.test.mjs)
覆盖重载、静默丢写和另一会话未保存草稿。这关闭了投递身份缺口，不证明模型改变了计划。

[`recoveryDecision`](../../loopx/control_plane/turn_driver/turn_journal.ts)检查 journal 一致性，
返回保留的终态，区分显式失败重试与阶段恢复。相关 Host Session binding 仍须合格，
未知 prepared effect 沿原回读路径处理。[`continuation_adoption.ts`](../../loopx/control_plane/coordination/continuation_adoption.ts)
把上下文采用与 lease 转移、依赖执行资格分开；恢复上下文不授权重复未知外部效果。

[团队纠偏视图](../../apps/presentation/dashboard/src/features/personal-workspace/goal-team-episode.tsx)
沿显式 `revises`、`responds_to` 和下游 `uses` 关系，按需核验产物版本并展示缺失采用。
它是实际配套入口，但没有把每个 `stride_decision` 都变成通用因果时间线。
源码接线、投递资格、真实模型采用、独立验收和可读的产品返回仍是不同层次。

[Goal Vision/replan 契约](../reference/protocols/goal-vision-replan-contract-v0.md)
定义启用方式与当前结算规则。`evidence_conditioned_v1` 可选且默认关闭，关闭时仍有
普通复核。此基线已要求启用策略的周期复核在每个合法结算出口都留下当前
`stride_decision`，绑定原义务与投递 context。进展、后继、blocker 和终止结果保留
各自证据规则，其他 replan 来源保留原出口。这项机器约束不证明实际采用；优化不能
绕过它、quota 结算、权限、lease 或版本检查。

### LoopX：已有原生 Goal provider 及恢复边界

[Managed ZCode provider](../../loopx/zcode_goal_mode/README.md)是 LoopX 内部的
具体接入示例，不是第五个上游运行时的独立审计。当前调用链区分绑定、启动与恢复：

```text
CLI / Goal drawer → bridge 验证 canonical Goal/Agent/project
  bind  → 创建/恢复 native session → 确认暂停 → 持久 binding
  start → 当前 Core heartbeat 正文 → validation/quota/validation 准入
        → 记录 start intent → native setGoal → 持久观察到的 target
  resume → 按需恢复同一 session/target → 当前准入
         → native resume，不再 setGoal
  monitor/status → 观察 native 状态与所需 Core 检查
                 → 拒绝/权威不可用：pause 并确认 drain
  cold recovery → 对账原 intent/target → 确认暂停
  native completed → 仅观察，不自动写 Core 完成或额度扣减
```

[`zcode_goal_operation`](../../loopx/zcode_goal_mode/bridge.py)在 bind/start
生成新的规范 heartbeat 正文；[`NativeGoalController`](../../loopx/zcode_goal_mode/runtime.ts)
串行执行操作，resume 保留原 native session/target，准入为 validation→quota→validation。
`setGoal` 回执丢失时，仅有持久获准 start intent 与匹配 objective hash 才能恢复；
冷恢复先暂停原 target，不自动续跑。[Controller 测试](../../tests/control_plane_ts/zcode_native_goal.test.ts)
用受控 Host double 覆盖拒绝、身份替换、持久化失败、精确恢复和原生完成不写 Core。
[Bridge 测试](../../tests/test_zcode_goal_bridge.py)覆盖另一层 Core/CLI 边界；测试代码
或模型清单都不证明真实付费 provider 就绪。

保留 target 只证明执行容器连续，不证明 resume 后消费了变化的 criterion。
反过来，没有再次 `setGoal` 也不证明模型使用旧标准：保留的 heartbeat 正文可能要求
Agent 读取当前 Core 状态。待补组合验收应保持同一 Goal instance，仅改变 criterion
或显式来源适用性，观察第一项新受保护动作。它须使用现有 owner 的当前 revision；
无法支持该刷新边界时保持 pending/paused。不能用整段 prompt hash 代替这项证据，
也不能改写旧回执。

[Broker](../../loopx/zcode_goal_mode/cli.ts)在一次串行检查结束后约两秒再安排下次
monitor，每个准入子进程有十秒 timeout。在线 status 可以重查 quota 并暂停 target，
不是对 Host 完全只读。Pause/stop 同样与串行队列竞争。每次执行的一小时安全期限
既不是整个 Goal 的 token 预算，也不是硬实时停机保证；native 用量仍未知。
这些是可测的成本/延迟边界，不是已确认瓶颈。合并观察前先测检查、子进程/IO、停止
排队和实际 drain；原生失败或未验证结果既不能冒充已验收，也不免除 Core 应做结算。

### LoopX：版本化合同验收与显式结果使用已经存在

[Acceptance adapter](../../loopx/control_plane/goals/acceptance.py)读取当前 owner
标准、执行 validator，再核对依据；[`acceptance_authority.ts`](../../loopx/control_plane/goals/acceptance_authority.ts)
将验证绑定 contract revision/digest、work basis 和 provider CAS。
[`projectGoalAcceptance`](../../loopx/control_plane/goals/acceptance_contract.ts)
区分 partial、accepted scope、stale 和 held。只验证一个 Todo，不因该项测试通过就
变成全合同 accepted；`selected_work` 合同的全量验证也只覆盖所选范围，不能代表
更大 Goal 的所有要求。

同样，[`qualifyDelegationResultUse`](../../loopx/control_plane/collaboration/delegation_result_use.ts)
已检查显式 `uses` 祖先、精确产物版本、accepted 来源、输入可用性、循环与有界收集。
A→B→C 中，即使 C 输出仍通过自身 validator，当前使用也可能不可用，历史
accepted/done 继续保留。[Result-use 集成测试](../../tests/test_delegation_result_use.py)
已有版本绑定重放、重连和传递失效且不重新 dispatch 原任务的用例。应组合这些机制，
而不是把它们当成缺失的 adoption/dependency 账本。

全 Goal 收口仍要证明当前合同覆盖已声明的必要要求，跨 Agent 必需工作已有合法处置，
相关 amendment 不会推翻结论。可选研究分支无需全部做完；持续型 Goal 的等待/终止
条件来自声明合同，一次观察成功不是永久完成。范围内验收、原回执、当前来源资格
和全局收口分别成立。

### LoopX：方向观察与渐进证据的接入程度不同

[`buildGoalDirectionBaseline`](../../loopx/control_plane/goals/direction_baseline.ts)
是对传入声明、资料 metadata 和同 Agent 使用回执的可选 M1 纯 builder。
未声明时保持投影缺席；digest 描述声明的资料依据，不是 canonical Goal-intent
revision 或动作授权。[Direction RFC](rfcs/goal-direction-baseline-v0.zh-CN.md)
仍将入口/回执 writer/consumer 的资格标为未完成；调用方给出格式合法回执，不能
认证真实资料使用。

Explore 已接入实际生产读取/回写路径：

```text
turn-context 返回有界 findings + 版本绑定 next_command
  → Agent 补读被省略结果页
  → stride_decision.explore_review 携带有界页坐标
  → state_refresh 获取当前 Explore 来源
  → projectExploreTurnContext 重放所声明切片
  → strideReviewMismatch 核对当前引用成员
  → 同依据补充引用保持准入身份 → 所属语义回写
```

[`explore_result_page.ts`](../../loopx/control_plane/capabilities/explore_result_page.ts)
限制描述符，[`explore_turn_context.ts`](../../loopx/control_plane/capabilities/explore_turn_context.ts)
重放来源页，不相信调用方自报 finding 列表。
[`stride_explore_pagination.test.ts`](../../tests/control_plane_ts/stride_explore_pagination.test.ts)
覆盖陈旧/伪造/其他 consumer 页及来源可用性区别。它验证引用，不证明理解或采用。
Page 与 bounded basis revision 范围不同；来源变化后须在原 Turn 重读，可更新
context/basis，但不替换原 obligation/settlement 身份。当前仍构造完整来源/result
rows，有界输出及较少单项 hash 不证明源 IO 为常数。测长历史时保留遗漏读取和有范围
反证；来源缺失不是反证，适用范围改变必须使对应旧依据失效。

## 借鉴机制及其限制

以下是实现参考，不是性能对比结论；固定源码版本不代表安装版本。
借鉴职责、触发条件与失败场景，再适配现有归属。

| 公开源码快照 | 值得借鉴的机制 | LoopX 落点与限制 |
| --- | --- | --- |
| Grok Build `2bdd1d6a`：[Goal 循环](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/goal.rs)、[策略顾问](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_strategist.rs) | Host-owned Goal 路径在工作轮结束后评估，候选完成时复核，反复未达成时进行建议性的策略调查 | 沿已有 owner 区分继续、验收与方法调查；不照搬固定评审人数，也不假设额外模型调用改善质量或成本 |
| Codex `9167a0b2`：[工具与输入循环](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/turn.rs)、[Goal 接续](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/goal/src/runtime.rs) | 工具结果和待处理输入驱动当前回合，生命周期扩展在空闲时启动合格 Goal 工作 | 复用 Host 执行与上下文维护；明确接续责任，避免原生 Goal 与外部 heartbeat 分别启动同一 attempt |
| OpenCode `be674e31`：[inbox](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/inbox.ts)、[执行协调器](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/run-coordinator.ts)、[子任务返回](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/subagent-completion.ts) | 当前工作纠正与排队任务分离；pending-wake 合并通知，覆盖 drain 到 idle 的竞态；子任务完成成为输入 | 扩展既有 inbox/session/Turn 投递；进程内单执行者不能替代跨主机 claim、lease 与采用证据 |
| Pi `e6220e13`：[Agent 循环](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/agent/src/agent-loop.ts)、[skills](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/docs/skills.md)、[subagent 扩展示例](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/examples/extensions/subagent/README.md) | 内层处理工具/steering，外层处理 follow-up；技能与复杂流程按需加载 | 普通工作不必依赖完整研究流程；扩展示例不等于默认产品支持、安全隔离或跨 Host 验收 |

### Grok Build：继续工作、候选评审与方法调查分开触发

**沿源码追的问题：** worker 看似完成或反复失败时，什么实际让它继续、接受批判或换方法？

```text
handle_turn_input_inner 完成一轮工作
  → 可执行的用户输入优先
  → run_goal_round_end → evaluate_goal_round
       continue           → 下一步指令 → worker 继续
       blocked            → 稳定 blocker key → 重复阻塞策略
       candidate_complete → verify_goal_candidate
           Achieved       → 本地 Goal 完成
           NotAchieved    → 保留具体 gaps → worker 继续
               达到策略调查节奏 → 建议性的 strategist
           评审基础设施不可用 → 新路径暂停
```

[工作轮 caller](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/turn.rs)
在普通模型/工具工作之后调用它，不在每次工具之前完整规划。
[evaluator](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_evaluator.rs)
接收有界 transcript、目标和 plan，不带工具，必须返回依据与下一步；稳定 key 用于
区分反复存在的外部阻塞和 worker 可以继续调查的普通错误。这仍是一次模型判断，
不是廉价的确定性完成判据。

只有候选完成才进入 verifier。[classifier](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_classifier.rs)
默认三个 skeptic 角色，但首个明确反驳可以让其余角色不启动；冷评审的投票规则也不让
恢复过的 gap reviewer 成为决定性赞成票。角色数不等于 API 调用数，每个角色仍可多轮
调用模型和工具。[verifier 指令](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/templates/goal_verifier_prompt.md)
主要审查实现者提交的证据并允许抽查，不独立证明每项业务后置条件。

`strategist_should_fire` 根据累计未达成触发，不逐步运行。
[策略指令](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/templates/goal_strategist_prompt.md)
要求依据 trace、diff 和测试提出结构性改法；`PlanGuard` 恢复被改写的 plan 字节，
避免方法调查悄悄降低验收。这是有界保护，不是通用权限系统。

**还要追失败路径。** 纠偏在 Host 安全点由 `RestoreOnCancel` 保护着取出；上下文
提交前取消会恢复原队列顺序。最后接收点之后到达的纠偏有保留 plan gate 的 fallback
prompt。[插入消息实现](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_impl/interjection.rs)
及 [actor 测试](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_tests/interjection_actor_tests.rs)
覆盖取消中的 drain 和晚到消息。LoopX 可以借鉴这些竞态测试，但 exact-Turn steer
不能自动继承 Grok 的后续投递策略。

压缩后重新注入当前 Goal，不增加有效工作轮。进程重启则不同：
[GoalTracker::from_snapshot](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_tracker.rs)
清除 in-flight child 状态，把原 active Goal 恢复为 user-paused。
[快照持久化](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/persistence.rs)
经异步通知到达；原子写一个快照文件，不等于全部 Goal 记账和外部效果组成已确认事务。

**对比结论：** 借鉴分级介入和当前 Goal 重建，保留 LoopX 的验收、原操作恢复和
配置复核义务。评审人数、计数重置、重启后暂停策略都须按 Host/profile 另行验证。
所读源码默认选择新 workflow 分支，但受配置和远端 kill switch 影响；
[配置定义](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/agent/config.rs)
不能证明安装行为。[压缩测试](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/acp_session_tests/goal/goal_compaction_reseed_tests.rs)
与[快照测试](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-shell/src/session/goal_tracker_tests.rs)
提供可复用反例，不是本轮运行通过的证据。

### Codex：响应完成、回合终结、Goal 续跑是不同边界

**问题：** 如何持续工作而不让每个工具结果都成为 Goal 规划事件，纠正与续跑竞争时怎么办？

```text
输入获准 → run_turn → sampling request
  → 模型输出 + 工具 → 等待并记录工具结果
  → 模型还需后续处理，或者存在待处理输入？
       是 → 准备当前上下文 → 下一次采样
       否 → stop hooks → 终结回合或有界继续
  → Goal lifecycle 为完成回合记账
  → on_thread_idle → continue_if_idle
       持有 Goal-state permit，读持久 active Goal
       → start_turn_if_idle 再做 Core 准入
```

同一 step context 用于请求、公开工具清单和实际工具执行；待处理输入在规定边界消费。
因此看似最终答复的文本、一次采样完成和整个回合终结并不是同一事件。
[Core 输入准入](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/turn_input.rs)
还将 steering 绑定到预期的活动 Turn。

原生 Goal 有两层保护：Goal-state permit 覆盖读取到启动的窗口，Core 再独立预留空闲
回合。待触发消息、Plan mode、draining 或正在运行的回合都能拒绝自动工作，拒绝后不
暗中转成队列或新 Turn 的 steering。[输入提交测试](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/tests/suite/turn_input_submission.rs)
覆盖 Plan mode 拒绝的原子性；[Goal lifecycle 测试](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/goal/tests/goal_extension_backend.rs)
覆盖恢复记账与禁止的状态变更。[`update_goal` 工具](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/goal/src/tool.rs)
写入允许的完成状态和用量，这次写入并不是对业务要求的独立测试。

**压缩中还有另一种竞态。** 准备好的摘要在安装时可能已过期。
可选[异步压缩路径](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/async_compaction.rs)
受 feature/provider 条件约束，绑定历史、模型和认证版本。
[`replace_compacted_history`](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/mod.rs)
在实际修改时校验捕获依据，保留实时 suffix 以及前台压缩快照之后接受的 Goal 编辑。
[Rollout 重建](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/session/rollout_reconstruction.rs)
同时恢复历史、归属和 settings metadata，不随便用一个仍可读的旧摘要代替。
预计算结果即使最终丢弃，也可能已经产生费用。

技能使用[有预算的 metadata 目录](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/skills/src/render.rs)
与[显式资源读取](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/ext/skills/src/tools/read.rs)。
探索可移到 child context，但返回边界有限：所读 V2 的
[completion routing](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/agent/control/completion.rs)
是 `trigger_turn=false` 的 best-effort mailbox 投递，`wait_agent` 报告等待活动，
不表示研究内容已经被采用。[多 Agent 测试](https://github.com/openai/codex/blob/9167a0b2d4a74d44a4d388057d304682e47313c6/codex-rs/core/src/tools/handlers/multi_agents_tests.rs)
明确区分完成正文、等待和中断。

**对比结论：** LoopX 复用 Host 内循环，并在自己的当前权威边界重新验证准备好的接续。
原生 Goal 状态、准备好的摘要或已入队子结果不能替代 LoopX acceptance、lease fence
和采用事实。这里得到的是职责分工，不是轻量 Host 已解决完整跨 Agent 生命周期的结论。

### OpenCode：持久工作与合并唤醒各司其职

**问题：** 工作在 busy→idle 边界到达时，怎样保留它，同时避免重复 runner 和模型轮询？

```text
Session.prompt：核对原身份 → prepare → admit
  → durable inbox → SessionExecution.wake
  → 每 Session coordinator → drain
  → runner advanceToStep → 提升输入 → runStep
       模型/工具 attempt → 持久 tool/step terminal events
       → retry / compact / continue / complete
  → pendingWake？再 drain：结束 execution
```

[`Session.prompt`](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/session.ts)
先准入输入再唤醒。[runner](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/runner/llm.ts)
进入工作时允许 queued input，继续当前工作时只提升 steering；provider retry 在同一
logical step 内刷新上下文，不反复消费更多 inbox 输入。内存 `pendingWake` 封住最后
资格检查到 idle 的竞态，合并的是唤醒意图，实际工作仍在 durable inbox。
[coordinator 测试](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/test/session-run-coordinator.test.ts)
使用受控调度覆盖 active、finalizing 与 cleanup 交错。

持久性不只来自内存 map：[execution](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/execution.ts)
将 execution-start event 与 durable claim 一起提交，正常终结/取消释放它，shutdown
保留恢复依据。[restart](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/execution/restart.ts)
扫描 claim 并持久记录有界 resume 次数；它声明的是 managed-server 交接假设下的
at-least-once。工具可能已改变外部世界但结果尚未入库，claim 不能消除这个未知。
[执行测试](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/test/session-execution.test.ts)
覆盖 claim 清理、取消和持久恢复次数上限。

后台探索通过 [SubagentJob](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/session/subagent-job.ts)
返回：child/job generation 绑定 completion observer，由它向父会话既有 inbox/wake
投递 stable-ID synthetic input。Observer 寿命超过局部视图；
[生命周期测试](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/test/subagent-lifetime.test.ts)
先移走/驱逐视图再完成子任务，核对结果通知。它验证预期路由和恢复边界，不证明真实父
模型使用了发现；技能也仍经[权限检查后按需读取](https://github.com/anomalyco/opencode/blob/be674e3158abb5f1b0c16b2bdeb3a9ba2b072774/packages/core/src/tool/plugin/skill.ts)。

**对比结论：** 借鉴持久工作和低成本唤醒信号的区别。LoopX Goal Chat 已有有范围的
accepted-result wake，应先验证其最后检查、重启和停止竞态，再推广到 provider
checkpoint。OpenCode 的进程/服务接任假设不能替代 LoopX claim、lease 和未知效果恢复。

### Pi：整批工具边界的 steering，与独立上下文刷新

**问题：** 纠正何时进入正在运行的工具流程，怎样避免上下文维护让它再晚一次模型请求？

```text
AgentSession prompt / steer / followUp
  → Agent pending queues → runLoop
  → prepareNextTurn → 补取准备期间到达的 steering
  → prepareRequest → 模型响应 → 执行整批工具
  → finishTurn → 下一次工具/steering 循环
  → 内循环准备停止时，才消费 follow-up
  → event listener 将完成消息追加到 session history
```

`finishTurn` 还是一个控制扩展点，不只是通知：

```text
finishTurn → end？→ agent_end
  否则 → 有自然工具/steering 工作？→ 继续这项工作
       → 有 follow-up？→ 带该输入继续
       → 显式 continue？→ 一次 context-only turn
       → 否则 agent_end
```

自然接续会消费显式继续请求，不额外重复；再做一次 context-only turn 需要新的
continuation 决定。它支持可选的长任务 controller，但不把持久业务 Goal、独立
验收或跨进程恢复自动加到基础循环。

此版本在**整批工具调用结束后**消费 steering。Sequential mode 也先完成 batch；
parallel 可以乱序完成，但结果按调用顺序进入上下文。Steering 不会终止已提交工具，
follow-up 则等内层工具/steering 循环准备停下时才消费。
[循环测试](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/agent/test/agent-loop.test.ts)
明确断言两个工具结果都在 queued steering 前。承诺长工具任务的纠偏延迟时必须考虑
这个粒度。

[`AgentSession`](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/src/core/agent-session.ts)
把上下文刷新接入 `prepareNextTurn`。大工具结果可能在下一次采样前要求压缩；准备结束
后 `runLoop` 补查新 steering，但 one-at-a-time 时不多取第二条。
[压缩测试](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/test/suite/agent-session-compaction.test.ts)
阻塞 compaction hook、入队纠正、再核对下一请求包含它。Faux provider 响应证明时序，
不证明模型服从。

[`SessionManager`](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/src/core/session-manager.ts)
维护带 parent ID 的 append-only JSONL tree。Compaction 追加摘要和保留历史边界，
重建模型投影不抹掉原历史。它能恢复上下文，但摘要或追加日志都不建立任务验收或外部
效果幂等。

Coding-agent 的 plan/subagent 流程是可选扩展，链接的 subagent 实现是示例而非必经
默认层。[技能 formatter](https://github.com/earendil-works/pi/blob/e6220e1316f86af818a0ccbfe1c09d7e918b41cf/packages/coding-agent/src/core/skills.ts)
先提供 metadata 和读取入口；可执行扩展能改生命周期并拥有进程权限，技能文本只是
指导选定工作。**LoopX 的适配**是按条件付出这些成本，并明确 Host 实际消费边界，
同时保留自身 required reads、独立验收和接续权威。

Grok Build 新旧 Goal 路径的失败语义不同，旧路径包含基础设施失败时放行完成的行为，
不应作为 LoopX 验收的候选方案。不同项目相似的 Goal/turn 流程也不是独立实验验证：
Grok 的[第三方声明](https://github.com/xai-org/grok-build/blob/2bdd1d6a6369de0e8c68132ea4539e9abd9e14a8/crates/codegen/xai-grok-tools/THIRD_PARTY_NOTICES.md)
明确有来自 Codex/OpenCode 的工具移植，但不能据此认定 Goal 循环同源。
这些客户端源码不能证明所有托管服务的实现，也不能保证任意外部操作恰好执行一次。

## 对比如何落实为现有 RFC 修订

审校按以下过程展开，不把外部项目的功能直接当作 LoopX 缺失能力：

1. 从外部 caller 追到实际循环、状态写入和返回路径，区分 Pi 工具批次、Grok 工作轮
   与 LoopX settled Turn，避免用同一种干预次数描述不同单位。
2. 阅读取消、晚到输入和重启测试。OpenCode 的持久恢复、Grok 的暂停恢复说明它们
   并非无状态循环，但也都不证明外部作用恰好一次。
3. 找到 LoopX 对应 caller 和参考契约。Steering receipt、accepted-result wake 和
   Explore capture 已有实现，应组合和验收，不换名字重做。
4. 区分源码连接与模型行为。上次决定投影和 CLI 链已存在，投递结果不证明下一动作更好。
5. 逐条对照 RFC，将过时的下一步与含糊义务修回所属章节，再补能区分新旧解释的失败场景。

| 所属章节 | 含糊或过时定义 | 修订与决定性验收 |
| --- | --- | --- |
| Stride §6.4、§7.5–7.7、§11–12 | 事件驱动可能被读成跳过周期义务；已有反馈投影仍列为下一实现 | 保留配置义务，分开候选评审和验收；下一片段改为真实结果→采用工作→再次复核，包含上下文恢复与完整成本 |
| Research Exploration §5.1、§11.5 | 通用捕获与未来科学决策/采用合同区分不足；有界输出暗示不读全来源 | 复用附件/重放，只识别仍缺的有效性/采用事实，测源 IO，分开验收捕获、使用和结果 |
| Agent Session §1、§4–5、§10–11 | 笼统“无自动恢复”和统一 successor-wake 缺口掩盖已有接续；相同原生身份可能暗示语义已更新 | 记录 managed ZCode 路径，保持单接续 owner；逐 Host 验同实例 resume 后当前标准/来源消费，以及终结和重连 |
| Manager A23、§10 | “steer 或 queue”可能静默改投纠正；投递 ACK 可能暗示后续采用 | 保留原 ingress/Turn 和已允许 fallback；queued、delivered、consumed、adopted、verified 各由所属事实证明 |
| Shared Goal Alignment §3.7–3.8 | 来源可读、局部 accepted、note 采用、journal 可继续可能被当成整个 Goal 就绪 | 保留 accepted 范围和必要要求覆盖，新工作前重接当前标准/显式来源；Direction M1 只作候选观察，原操作恢复保持不变 |

这些修改细化验收并澄清既有权威，不安装 evaluator、不改变当前周期，也不创造协议词表。
额外机器约束仍需所属实现、兼容和关闭态验证。

### 所核验 LoopX 版本的实现程度

| 范围 | 已有证据 | 仍待闭合的边界 |
| --- | --- | --- |
| Replan 投递与后继选择 | 实际 caller、typed 来源检查、跨 Turn CLI 测试 | 自然模型的方法采用与有效结果 |
| 结构化 stride 策略 | 可选提案结构、previous/Explore review、精确证据绑定，以及开启的周期复核所有出口必须记录决定的约束 | 声明合法不等于真实，实际方法采用和收益仍需证据 |
| Explore 捕获与显式 M3 composition | 普通附件和 File/SQLite composition gate，见 Research §5 | §11.5 单实验结果/采用门、完整远端/安装验收与科学效用 |
| 纠正与 accepted-result wake | 浏览器重试身份、exact-Turn ingress、有界 Goal Chat wake | 逐 Host/channel 的实际消费和采用，通用作业结果集成 |
| 恢复与 required reads | typed 原操作恢复、当前 Todo/acceptance/lease 检查和来源读取 | 跨压缩/交接的完整 Goal 要求覆盖，不推断全局就绪 |
| Managed native Goal | 可选 ZCode bind/start/pause/resume/stop、精确 native 身份、冷恢复暂停和 Core 准入 | 同实例恢复后消费当前标准、真实用量/成本与全部 Host 资格 |
| Acceptance 与 result-use | 版本化合同验证/CAS，以及当前显式依赖祖先检查 | Accepted 合同范围不等于任意 Goal 意图完整覆盖，不推断全局依赖图 |
| Direction 与省略的 Explore 证据 | 纯 direction M1 builder，生产路径来源重放结果页 | Direction 生产回执/consumer 未资格化；引用合法不证明模型采用或常数读取成本 |
| 产品过程解释 | 显式产物/复核/修订/采用视图 | 通用 stride 决定→产物过程，以及完整 Explore 附件/重试操作 |
| 运行效率 | 明确 owner 与可测成本候选 | 同负载、模型、历史规模的受控测量；代码量或测试通过不证明延迟 |

### 协调已有实现候选

源码地图不能把候选 PR 已有的工作写成需要重新实现的缺口。下列 PR 接入同一流程
的不同部分，各自当前提交的评审和验收证据仍决定其就绪程度：

| 已有提案 | 在流程中的职责 | 交接要求与边界 |
| --- | --- | --- |
| [请求纠正与 Goal 效果 #6357](https://github.com/loopx-project/loopx/pull/6357) | 原会话 → 请求修订 → 效果预留 → 真实 Goal 终局回执 → 原路返回 | 发送前持久保存并回读精确重试身份；已提交历史、当前请求版本与接收方采用分开。保留 Goal 变更不代表工作已完成。 |
| [材料读取与方向恢复 #6338](https://github.com/loopx-project/loopx/pull/6338) | Owner 声明 → 真实有界来源读取 → 同 Agent 确认 → 当前依据与恢复 | 即使历史满额，当前读取失败也须撤下旧有效状态；保留历史及有界 owner 恢复。读取确认不证明模型理解、工作改变或验收。 |
| [备份隔离检查 #6279](https://github.com/loopx-project/loopx/pull/6279) | 归档/清单验证 → 隔离 owner 读取 → 可读历史证据 | 检查保留原容量和 owner 决策门；激活、新权限、按当前要求继续及独立验收的新工作，仍归恢复 RFC 后续阶段。 |

每个 PR 都能交付自身有界结果；连贯旅程还须观察消费者实际使用上游结果，不能以
词语一致或三个 PR 均合并代替。按当前 main 协调重叠 caller，再从结果源码重新生成
派生 manifest。验收留在原 PR/RFC owner，不另建依赖账本，也不要求普通纠正先完成
无关的灾难恢复激活。

## 将探索接回执行，避免每项任务都变成研究

初始未知、新机会、用户方向或反例都可以触发探索。重复失败只是信号之一，不是前提，
也不自动要求换方法。选择技能或并行工作前，先说明调查可能改变什么决定。

| 未知 | 合适的工作 | 有用的返回 |
| --- | --- | --- |
| 现有实现及约束 | 定向源码阅读技能 | 已证实行为、可复用边界与限制 |
| 替代方法或外部依据 | 研究/搜索技能 | 候选、一手来源、适用条件与反证 |
| 候选是否适配项目 | 源码与研究结合的分析，或有界实验 | 能区分方案的结果、集成成本与采用条件 |

这些是工作方法分类，不是必装技能包或新路由 enum。选择能回答问题的最小方法。
子 Agent 在假设或信息来源不同时才有明确分工价值，摘要保留来源引用和未决问题。
研究报告本身可以完成研究任务，不必制造代码工作。

用 Explore 现有 `evidence` 模式保留发现，多候选分支确需只读选择支持时再用 `planning`。
模式不授予 claim、spawn 或执行权限。验证后可复用的发现通过既有 `explore_result`
附件或 `--explore-result-json` 随普通任务回写，包含测试输入版本、适用范围、观察、
解释和证据。兼容的 `path_delta` 可复用已有附件形式，避免重复同一叙述。
普通工作没有新发现时不需要填空 finding。

下一轮 context 和 `explore_review` 让 Agent 说明哪个发现支持换路、继续原路或合理复验。
反证限定在已测试条件内，保留当前最有依据的成果。输入不可读或测量未得出结论，都不
构成反驳。实际任务步骤、产物版本和独立验证才能支持采用声明，只有来源绑定还不够。

## 控制常态开销

先沿[优化证据指南](../development/testing-and-quality.md#roadmap-aligned-optimization)
测真实消费者，再选择抽象：

- 分开测重复调用、历史读取、序列化/跨语言成本、provider IO 和模型工作。
  返回短文本仍可能扫描完整来源；Explore 的有界 turn context 不承诺历史 IO 有界。
- 只在已证明的快照边界内复用事实，在所属准入/写入边界重新检查权限和版本。
  缓存不能成为另一个 writer；先消除已测到的重复读取/复制，再考虑缓存服务。
- 默认上下文保留决策所需事实与详情入口，相关时再读技能指令、完整证据和诊断。
  精简文案仍须保留顺序、来源、验收及停止义务。
- 暂态传输重试、上下文压缩、方法调整由各自 owner 处理。
  耗时或新标识不能证明进展；恢复沿原操作，未知效果先查询再重试。
- 总成本包含探索、评估、评审、压缩与 observer CPU/IO。
  可见响应更快不一定意味着总成本更低。

对受支持的长任务 profile，Stride §7.5 提议先进行无推理调用的观察，再由确切、
经过验证的结果推进现有 monitor generation，观察期限负责发现丢失事件。
不支持的 Host 保留当前路径并说明限制；本文不隐式抑制模型准入或修改 heartbeat 默认。

## 一次连贯的验收场景

选择一个受支持的 Host/profile 和隔离仓库任务：改进实现，同时保留约定命令和兼容合同。
整个场景保持原验收不变：

1. 开始有效工作，然后纠正实现方法但不弱化原要求；分别确认收到纠正和实际应用。
2. 调查一个相关未知，结果随普通回写返回。下一段已准入工作采用候选，或解释保留原方案
   的理由；检查实际产物与结果。
3. 将确切后台结果交给现有 monitor/resume owner。注入重复、乱序通知，以及消费者即将
   空闲时到达的通知。
4. 在结果已提交但尚未确认时中断，恢复原操作与当前 Goal 依据。覆盖来源版本变化、
   证据缺失及用户停止；旧结果不得重新启动已停止工作。
5. 独立验收后返回原会话。打包前端与 CLI 分开显示 queued、delivered、adopted、validated
   事实，缺失或陈旧证据不能保留成功标记。

现有[纠偏证据视图](../../apps/presentation/dashboard/src/features/personal-workspace/goal-team-episode.tsx)
可以核验显式产物/复核/修订/采用关系，不是所有 stride decision 的通用可视历史。
缺少前端附件提交/重试或决定到产物的连接时，仍是所属交付片段的配套工作；只有 CLI
的前置实现应说明为部分交付。

### 恢复与负向场景矩阵

下表把已有回归接缝组合成**待验收的连贯场景**，不是全部用例已通过的报告。
测试链接提供可复用起点；实际执行的测试、后端和跳过项在 PR 单独披露。
沿原操作与当前来源版本连接证据，不新建账本。

| 注入或变体 | 应观察到的结果 | 已有接缝与剩余验证 |
| --- | --- | --- |
| 暂停后保持 Goal instance，更新标准/来源范围再 resume；restore 期间再加入 amendment | 保留原生身份和原回执；第一项新受保护动作消费当前适用标准/来源，不支持的边界保持待处理 | [原生控制器测试](../../tests/control_plane_ts/zcode_native_goal.test.ts) 覆盖身份/恢复；跨 owner 恢复到真实模型行为仍待验 |
| 显式 A → B → C 的祖先失效，但 C 自身 validator 仍通过 | 保留历史 accepted/done；祖先不合格时拒绝当前使用，不重新派发原任务 | [Result-use 集成测试](../../tests/test_delegation_result_use.py) 已由受控 worker 覆盖；再接所选 Host 入口 |
| Native completed 而验收失败；selected-work 合同通过但仍有必要 Goal 工作 | 保留 provider 完成与局部 accepted，不升级为整个 Goal achieved；完成应做结算；仅可选研究未完成不必阻塞收口 | [验收 authority 测试](../../tests/control_plane_ts/goal_acceptance_authority.test.ts) 和原生完成测试覆盖独立 owner；最终组合回读仍待验 |
| 最后 drain 与 idle 之间提交结果；重复通知、停止后旧 wake，再加入新获准输入 | 持久工作可发现；旧 wake 不复活已停执行；新输入由原接续 owner 按当前准入处理 | 复用[已验收结果 wake](../../loopx/chat_loopx_mode.py) 和 Host 接续路径；同时验证不重复启动与不丢工作，不能只验证锁 |
| 工具、上下文准备或终结期间投递/重试 exact-Turn steer | 文本、ingress、目标不变；如实显示 pending/consumed/refused 和许可 fallback；steering 不等于工具取消 | [Steering 测试](../../tests/test_chat_turn_steering.py) 加每个 Host 的声明消费边界，不推广为全部 Host 通过 |
| Native set 已发生但回执丢失；重放旧操作后再准入新恢复工作 | 只恢复匹配持久 start intent 的 target；冷恢复暂停；旧重放幂等，新 accountable Turn 仍履行结算 | 复用原生控制器和 [Turn recovery](../../loopx/control_plane/turn_driver/turn_journal.ts)，分别检查原操作与新 Turn 回执 |
| Validation/quota 变慢时请求停止；到单次 deadline 后多次显式恢复 | 分别测请求排队、原生取消与确认 drain；跨计时器保留配置的累计预算及 unknown 用量 | [ZCode runtime/broker](../../loopx/zcode_goal_mode/cli.ts) 是测量候选，不是已证明的停止 SLO 或 token 用量限制 |
| 经合法/伪造/陈旧/跨消费者页引用省略结果；新增与旧反证条件不同的正 finding | 当前来源重放；反证保留适用范围及读取入口；坏页与来源不可用分开。同依据页保留准入身份，源漂移可在原 Turn 更新 basis | [分页测试](../../tests/control_plane_ts/stride_explore_pagination.test.ts) 与 Stride §7.6 已有合同；仍须验证实际采用省略证据 |
| 主工作提交后丢失 Explore 附件/关联确认 | 主工作仍成功；原操作下补回附件，不重复工作、不宣称已采用 | [Explore 回写测试](../../tests/control_plane/test_explore_result_writeback.py) 提供恢复接缝，验证产品重试与回读 |
| 遍历启用策略的周期复核出口，再关闭策略或不声明 direction | Progress/successor/blocker/terminal 保留现有义务；关闭保留普通行为/历史；未声明 Direction M1 不加投影或权限 | [Stride 测试](../../tests/control_plane_ts/stride_policy.test.ts) 与 [direction 测试](../../tests/control_plane_ts/direction_baseline.test.ts)；压缩、轮询、UI 刷新不能解除工作义务 |
| 分别给判别性结果、不可用来源、不适用 finding，以及支持保留原方案的结果 | 检查实际下一工作、产物和独立结果；有依据不换路合法。复述 finding 或单独声明 used 不足 | Research §11.5 与既有捕获/result-use owner；真实模型采用是独立 M3 验收，对比收益还需匹配证据 |
| 保持渲染上下文上限并扩大历史 | 省略反证仍可恢复；分测扫描/排序/hash/序列化/上下文成本，保留适用范围版本 | 当前源码处理完整来源；先测冷/热负载，再宣称 IO 可扩展或净节约 |

首行是对上述“验收不变”主场景的**显式授权改要求**变体，不允许 Agent 在普通优化时
自行弱化验收。先在既有 owner 跑故障用例，再组合受支持产品旅程；仅为需要的结论
追加真实模型或匹配实验。

### 交付与收益验收

| 交付片段 | 既有归属与决定性证据 |
| --- | --- |
| 移除一处实测开销 | S2/S7/S10 与优化指南：同负载 base/head 比较，保留要求的语义、重放与真实后端检查 |
| 一次探索影响后续工作 | Explore 与 Stride §7.6：真实模型判断、当前证据、可归因产物和独立结果，也允许明确不换路 |
| 恢复一条 provider 结果/唤醒路径 | Stride §7.5、Host 与 monitor：受支持产品入口覆盖重复/丢失/迟到事件、重启、停止、来源变化及原操作回读 |

这些片段沿用现有路线图和 RFC 里程碑，不创建新任务账本，也不要求先完成无关迁移。
实现前核对相关当前工作：周期继承与复核约束、纠正重试身份、已有 codec/Explore
性能修复可能已覆盖部分范围。

测量验收质量、遗漏要求、总时间/tokens/成本、纠正到行动的延迟、恢复损失、重复效果
和用户注意力。固定源码、Host/model、任务/输入、权限、验收器和预算；保留失败及
未完成样本，区分冷/热路径和历史规模。评价候选前确定阈值。合成 fixture 验证机械链，
真实采用和对比收益需要各自证据；开销下降但验收或恢复变差，不算优化成功。

保持行为的常规性能修复可沿现有合同推进。改变默认准入、复核、保留、权限或结算，
则在所属 RFC/契约中说明兼容和回滚。可选策略在完成资格验证前保持有界；关闭策略
保留历史证据，不取消已准入工作，也不抹除恢复义务。
