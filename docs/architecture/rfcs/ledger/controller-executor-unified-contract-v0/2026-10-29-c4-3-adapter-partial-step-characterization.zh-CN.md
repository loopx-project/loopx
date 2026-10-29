# C4.3 适配器部分步骤行为刻画

- 上级：[controller/executor RFC，C4.3](2026-10-27-refactor-handoff.zh-CN.md#c43-adapter)。
- 基线：`8caaf678e`，2026-10-29。结算属主自 `1e3401591` 起未变。
- 状态：只做刻画。不改变运行时、schema、wire 或默认值。
- 上游条目：[C4.3 结算义务对照](2026-10-29-c4-3-settlement-obligation-comparison.zh-CN.md)。
- [英文版](2026-10-29-c4-3-adapter-partial-step-characterization.md)。

## 为什么有这条记录

对照条目把"适配器没有本地 journal"记成关于适配器自身的事实，但适配器在
"某步成功、后续步失败"时的自身行为，以及适配器与 CLI 回执之间的恢复边界落在
哪里，当时仍没有被刻画。任何会改动这条路径的整合切片，都只能对这两点做假设。

本条目用可执行用例把该行为钉住，让后续的恢复改动从实测行为出发，而不是从对照
条目的措辞出发。

## 现在刻画了什么

| 场景 | 适配器行为 | 属主行为 |
| --- | --- | --- |
| 某一步返回适配器读不懂的输出 | `_provider_condition_matches` 把非 JSON 输出当作"条件不匹配"，计划在该步之后停止 | finalize 阶段仍然运行，由属主判定这次停止：`blocked`、`failed_stage: guard`、原因 `quota guard returned malformed JSON` |
| 某一步抛错 | 异常直接冒出适配器。finalize 阶段从未运行，这笔事务从未被分类。适配器不保留已成功那一步的任何记录 | 没有分类，因为属主从未被询问 |
| 抛错后的重试 | 适配器从计划第一步重新进入，绝不在中途续跑 | 身份由同一意图重新推导，因此使用同一个 settlement identity |
| 某一步在生命周期完成之前抛错 | 没有花费，Todo 保持 open | guard 已经跑过，因此 `validation` 凭据已提交。读回为 `open`，进度为 `writeback_required`；不存在可重放的生命周期完成、写回或花费凭据 |
| 某一步在生命周期完成提交之后抛错 | 适配器什么也不留下；已提交的凭据由 CLI 的规范凭据库持有 | 读回为 `settlement_pending`，进度为 `writeback_required`：`validation` 已提交，写回与花费凭据缺失 |
| 某一步在持久写回提交之后抛错 | 适配器什么也不留下；已提交的凭据由 CLI 的规范凭据库持有 | 读回为 `settlement_pending`，进度为 `spend_required`：`validation` 与 `durable_writeback` 已提交，花费凭据缺失 |
| 某一步在配额花费提交之后抛错 | 适配器什么也不留下；已提交的凭据由 CLI 的规范凭据库持有 | 读回报告 `settled`；重试重放该凭据（`idempotent_replay`），不会再扣一个槽位，并报告同一个 settlement identity |

由此得到三条结论。

1. **适配器不做任何分类判定。** 计划停止由 TypeScript 属主分类；某一步抛错则
   完全没有分类，因为属主从未被询问。两种情形下适配器都只是传输。
2. **恢复边界是 CLI 凭据，不是适配器状态。** 抛错之后，唯一持久存在的证据是
   CLI 已经提交的东西。重试之所以成功，是因为那份凭据可以重放；适配器除了重跑
   计划之外没有贡献任何东西。
3. **欠下的工作由属主按阶段点名。** 读回区分"结算尚未开始"与"结算待补"，并指出
   还欠哪一步：生命周期完成之后是 `writeback_required`，持久写回之后是
   `spend_required`，配额花费之后是 `settled`。因此抛错并不是一种不加区分的
   失败状态；已提交的凭据类型说明重试会从哪个阶段继续。

## 证据

`tests/control_plane/test_host_adapter_settlement_partial_steps.py`：

- `test_an_unreadable_step_output_stops_the_plan_and_the_owner_classifies_it`
- `test_a_raising_step_leaves_no_finalize_and_no_adapter_local_resume`
- `test_a_raised_step_after_a_durable_commit_leaves_only_that_stage_pending`
  （真实 CLI、真实配额存储；按 guard、生命周期完成、持久写回与配额花费四个阶段
  参数化，读回已提交的凭据类型、replay phase 与结算进度）
- `test_a_raising_step_after_a_durable_commit_recovers_through_the_cli_receipt`
  （真实 CLI、真实配额存储）
- `test_a_raised_step_before_completion_commits_no_settlement`（真实 CLI）

既有的端到端恢复用例仍在原处：
`tests/test_goal_mode_mcp_settlement.py::test_real_mcp_completion_recovers_a_lost_mutation_response`
覆盖的是提交之后响应丢失；新增用例覆盖同一条恢复路径在传输抛错时的形态。

## 本条目没有建立什么

- 没有等价性证明，没有删除授权，没有运行时资格，没有收益测量，也没有断言任何
  一条结算路径是多余的。
- 刻画覆盖的是经 `settle_host_todo_completion` 的已交付 MCP host 路径。它不覆盖
  适配器的其他调用方，也不描述纯 TypeScript 的 Turn settlement 路径——后者在
  自己的事务内按 provider 步骤恢复。
- 它没有给适配器增加 journal、重试策略或续跑能力。增加这些是新的设计决策，
  有各自的验收，不是刻画。
- 观察到的恢复依赖 CLI 的规范凭据库。源码事实基于上述基线读取，可能漂移。

## 后续切片

本条目不改变结论，但修正了剩余工作的表述方式。删除适配器侧解释器仍需要对照条目
记录的前提条件。词汇对照条目已经撤回的候选——把 `guard`、`lifecycle_completion`
与 `validation` 归到一个 typed 属主——在此不重新打开：那条目已经指出了同时采用
两条路径的已交付调用方，并把该候选记为 withdrawn。独立的登记切片——为 host-adapter
有序步骤类型设立共享属主——已在
[#7619](https://github.com/loopx-project/loopx/pull/7619) 中完成；那是登记切片，
不是被撤回的统一化工作的延续。仍然开放的是对照条目记录的删除与采用前提。
