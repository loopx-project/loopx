# Agent 失败控制与恢复

[English](agent-failure-containment-and-recovery.md)

LoopX 已经实质处理执行故障、陈旧状态、响应丢失和结算效果不明。对于错误结论的传播，以及从最后一个正确业务状态恢复，目前仍是部分实现。持久记录证明“记录了什么”；正确性还需要相关、当前的独立验证。

本实现地图依据源码版本 `44931b6d22a50b949d43354e6ea498fb6b68d231`，汇总现有 owner 与边界，不新增运行合同，不证明某个安装配置已启用或生产 Failover 已通过。[整体路线图](rfcs/loopx-overall-roadmap-v0.zh-CN.md)继续拥有优先级，尤其是 S2 持久权威状态、S10 可靠性与 S11 评估。

## 三个目标的落实程度

| 用户需要的结果 | 已有机制 | 尚未覆盖的边界 |
| --- | --- | --- |
| 尽早发现错误 | 类型化准入、身份与 lease 检查；独立任务验证；可选诊断与进展审查 | 任务验证通常发生在 Host 的有界 Turn 返回后。没有通用机制证明每个中间结论都正确。 |
| 限制错误传播 | 未知 effect 阻断；lease 失效停止所拥有的进程；来源与版本检查；依赖/采用的当前读回；记忆候选审阅 | 这些机制各有权威和证据边界，不能自动撤回所有受错误结论影响的上下文、产物与下游完成状态。 |
| 从可信状态恢复 | Turn 阶段与 effect 持久读回；canonical 状态恢复；显式跨 Agent/Session 接管 | 可信对象是身份、阶段记录和声明范围内的验证。任意文件回滚、外部副作用撤销、自动语义 Failover 尚未成立。 |

例如，Agent 在第十步做出错误假设，后续工具仍正常返回：传输、journal 与 lease 都可能健康。如果没有相关 validator 或独立复核，这些机制不能自动发现第十步的假设错了。这是执行可靠性与推理正确性的关键区别。

## 四条真实执行与证据链

### 1. 先分类失败，再决定是否重试

[`host_failure.py`](../../loopx/control_plane/turn_driver/host_failure.py)分类 Host 故障；[`managed_step.py`](../../loopx/control_plane/turn_driver/managed_step.py)的 `managed_step_receipt_from_journal` 拒绝非 retryable 故障；[`loop_controller.py`](../../loopx/control_plane/turn_driver/loop_controller.py)的 `decide_loop_disposition` 根据类型化结果与剩余预算选择有限重试、等待或修复。

Timeout 的尝试上限为两次；capacity、overload、rate-limit、transport 故障为三次。退避指数增长，最多 300 秒。这是外层调用者的调度依据，不是隐式 sleep 或通用无限重试循环。

[`turn_journal.ts`](../../loopx/control_plane/turn_driver/turn_journal.ts)的 `hostRetryPolicyCheck` / `recoveryDecision` 校验故障字段、尝试次数一致性和预算。显式 failed-Turn retry 为部分非 retryable 终态故障保留兼容路径，`output_budget_exhausted` 被拒绝。这属于显式修复入口，与自动重试不同，也不证明重复执行能修好问题。合同见 [Turn loop](../reference/protocols/turn-loop-controller-v0.md)。

这条链的收益是让已知瞬时故障得到有界处理。Validator 失败或副作用未知，需要修复或对账，不能靠盲目 rerun 消除。

### 2. 验证 Turn，再对账结算效果

[`executor.py`](../../loopx/control_plane/turn_driver/executor.py)的 `run_loopx_turn_once` 执行 Host，再由 `_task_validation_stage` 检查声明的任务后置条件，之后才进入结算。验证失败阻断成功结算；类型化 stop result 可以令验证为 `not_required`，它不等于 validator 通过。

[`settlement.ts`](../../loopx/control_plane/turn_driver/settlement.ts)的 `reduceTurnSettlementTransaction` 拥有 durable-writeback → quota-spend → terminal-closeout（需要时）的有序决策；[`settlement_provider.ts`](../../loopx/control_plane/turn_driver/settlement_provider.ts)的 `settlementProviderAction` 决定当前 provider 操作。在 provider I/O 前记录 effect intent，恢复时读回结果：

- 已提交且身份匹配：补记丢失的 checkpoint，不重复效果。
- 确认不存在：执行同一个已准入 intent。
- 未知、无法读取或没有可用 resolver：阻断后续效果。

因此，响应丢失不会自动变成再次执行副作用。但它不能撤销 Host 内已发生的文件写入或 API 调用，也不提供跨任意 provider 的分布式事务。[Provider-effect acceptance RFC](rfcs/provider-effect-acceptance-v0.zh-CN.md)的更强保证仍是设计，尚无完成运行接入与资格验证的 provider。

[`leased_host_process.ts`](../../loopx/control_plane/turn_driver/leased_host_process.ts)的 `runLeasedHostProcess` 在启动前、续约和输出结束后校验执行证明。续约卡住不会延长最近一次证明的截止时间；到期会 abort 并 drain 所拥有的 POSIX 进程组。Windows 的进程树终止明确为 best effort，不能继承更强的 drain 保证。它控制执行授权失效，不能识别错误推理，也不能逆转已准入的外部效果。

### 3. 后续使用必须有当前来源与结果依据

[`Decision Context assembler`](../../loopx/capabilities/decision_context/assembler.py)的 `_verified_recalled_claims` 将 recalled content 与当前 authority 的 exact read 对照。读取失败或 disposition 不完整，不能推进 reviewed cursor。这能防止旧记忆冒充当前来源证据；内容完全一致仍不证明来源或解释正确。

[`Reward Memory candidate review`](../../loopx/capabilities/reward_memory/candidate_review.py)在生成候选时检查 freshness、conflict 与 scope；审阅接受使用候选已保存的 guard，退休遵循 active 记录的生命周期。[`application.py`](../../loopx/capabilities/reward_memory/application.py)另外检查后续 recall 的当前依据。Retire 不会自动撤回已经进入所有 Agent Context 的内容、撤销衍生产物或逆转效果。

[`delegation_results.py`](../../loopx/control_plane/collaboration/delegation_results.py)的 `accepted_result`、`require_dependencies` 与 `result_relationships` 绑定输出/实际输入 hash，重跑当前 validator，并重新检查 adoption。输入改变可以使依赖或采用关系 unavailable；source current-read 不递归资格化自己的 incoming dependencies，后续 consumer 因而可能看不到上游依据不可用。这种读回也不会自动撤销 canonical done/accepted 状态。另一条 [`Todo execution dependency`](../../loopx/control_plane/coordination/todo_execution_dependency.ts)准入链会拒绝缺失、循环和未完成前件；`todo_done` 是状态条件，不是递归验证产物真值。

实际收益是：旧的成功记录、收到消息，都不能单独证明当前验收或实际采用。所查 owner 尚未提供通用的结论反驳与下游上下文撤回协议。

### 4. 续执行已证明阶段，或显式交接当前任务

Turn journal 根据持久化的合法阶段前缀继续未完成阶段，保留 prepared intent 与 receipt。恢复因此可以对账中断的结算，而不必重新派发已经完成的 Host。

[`todo_continuation.ts`](../../loopx/control_plane/coordination/todo_continuation.ts)的 `executeTodoContinuation` 支持显式跨 Agent/Session 接管，检查当前 canonical Todo facts、source session、note fingerprint 与 acceptance guard。Leased 路径另查 execution dependencies，soft-claim 路径不普遍提供这一依赖检查。Hard lease 接管需要转移 claim 和接收者自己的 current proof；adoption 绑定 revision 并读回。收到 note 本身不授予执行权。

Note 能保留尝试过的路径与下一步，但 summary 仍由发送者提供；artifact availability 检查的是存在性，不是内容正确的独立证明。这是受控的 continuation 工作流，尚不是自动选取最后正确 workspace、回滚并更换 core 的完整闭环。

## Checkpoint 到底可信在哪里

| Checkpoint | 保存或校验的依据 | 不能据此证明 |
| --- | --- | --- |
| Turn journal | Goal/Agent/Todo 身份、阶段前缀、Host result、验证与 effect receipt | 中间推理正确；所有文件和外部效果已快照 |
| `checkpoint-context` / vision supplement | 当前 Goal、Todo、依赖与 source component hash；补记前读取依据未变化 | 模型已经正确理解来源或修复实现 |
| AuthorityStore checkpoint / reviewed archive restore | Canonical projection、事件与 lineage；恢复到独立 destination | 自动恢复活跃 workspace 或全部 provider |
| Decision Context capture recovery | 声明恢复范围内的 reviewed cursor 与私有 capture material | 任务或业务状态回滚 |
| Continuation note 与 adoption | 当前任务归属、note basis、lease 与 receiver eligibility | 继承的每条结论正确 |

对应源码与合同：[checkpoint context](../../loopx/control_plane/goals/checkpoint_context_io.py)、[read-context snapshot](../../loopx/control_plane/goals/checkpoint_read_context.ts)、[authority archive](../reference/authority-archive.md)、[capture recovery](../../loopx/capabilities/decision_context/capture_recovery.py)。

## 检测信号不能越过自身证据范围

[`Reliability diagnostics`](../../loopx/capabilities/reliability_diagnostics/README.zh-CN.md)是显式 opt-in、默认关闭的 L1 observer，其 projection 没有执行权。活跃阶段静默至少五分钟是 stall 嫌疑；实时活性判断需要显式传当前 `--as-of`，不传则用末事件时间作历史重放。Repetition 按连续相同 tool name 达三次判断，不比较参数或业务语义。Recovered-error count 仅表示错误后观察到 step/Turn 进展，不能证明业务已恢复正确。

真正影响执行的既有 replan 使用 [`replan_history.ts`](../../loopx/control_plane/work_items/replan_history.ts) 与 [`replan_semantics.ts`](../../loopx/control_plane/work_items/replan_semantics.ts)。不同 Turn 的重复 typed progress 可以要求 replan；新增 ACK 或 evidence 名字本身不能构成合格 semantic delta。这仍依赖合法上报与证据 owner，不是通用正确性判定器。可选外部审查与 L1 被动观察也有独立边界。

## 验证方式与尚待交付的结果

现有回归入口包括：

- [Host failure](../../tests/test_loopx_turn_host_failure.py)、[loop controller](../../tests/test_loop_turn_loop_controller.py)、[journal](../../tests/control_plane_ts/turn_journal.test.ts)、[settlement recovery](../../tests/test_loopx_turn_settlement_recovery.py)。
- [Decision Context](../../tests/capabilities/test_decision_context_assembler.py)、[memory recall](../../tests/capabilities/test_reward_memory_agent_scoped_recall.py)、[delegation result use](../../tests/test_delegation_result_use.py)。
- [Diagnostics](../../tests/capabilities/test_reliability_diagnostics.py)、[typed progress](../../tests/control_plane/test_progress_observation.py)。

这些测试设计包含失败、陈旧来源、输入改变和 effect 中断等负例。Fixture provider 与一次性本地状态验证各自合同；通过不等于真实模型纠错、任意远端 exactly-once 或生产恢复时间合格。诊断部署、开销与长时间 non-interference 需要独立证据。

RFC 明确了这些部分实现路径中仍需补齐的三项整合缺口：

1. [显式委派来源链的当前使用检查](rfcs/shared-goal-alignment-and-governed-amendment-v0.zh-CN.md#38-失效证据与受影响消费者)：声明依据不可用时拒绝新的依赖使用，保留历史完成，限制遍历与验证成本。
2. [原任务修复与复验](rfcs/composable-state-machines-recovery-verification-v0.zh-CN.md#基于源码的实现顺序)：把独立失败依据传入可操作恢复路径，按当前 criterion 复验，再对账原效果。
3. [绑定 criterion 的 shadow 评估](rfcs/optional-semantic-assistance-jev-v0.zh-CN.md#检测结果与所属规则的衔接)：显示实际证据覆盖、缺失依据和独立判断维度，不能把未触发信号升级为正确性证明。

[Composable recovery RFC](rfcs/composable-state-machines-recovery-verification-v0.zh-CN.md)保留完整 M2 recovery 的开放边界；[Reliability diagnostics RFC](rfcs/long-running-agent-reliability-diagnostics-governed-delivery-v0.zh-CN.md)拥有 observer 资格验证与受控干预；[Alignment RFC §3.7](rfcs/shared-goal-alignment-and-governed-amendment-v0.zh-CN.md#37-当前工作与-goal-要求)保留 full-requirement ledger 与 global closeout 的未完成范围。本地图不关闭这些验收，也不另建平行路线图。
