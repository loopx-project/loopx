# C4.3 结算义务对照

- 上级：[controller/executor RFC，C4.3](2026-10-27-refactor-handoff.zh-CN.md#c43--settlement-adapters-over-existing-primitives)。
- 基线：`e19d94ca0`，2026-10-29。结算属主在 `fb331f45` 未变。
- 状态：只读对照。不删除代码，不改变运行时、schema、wire 或默认值。
- 更正：本条目早前版本把"适配器没有本地 journal"读成"整条 MCP 路径没有恢复"。
  该结论错误。恢复基线已在下方更正，两侧镜像同步更正。
- [英文版](2026-10-29-c4-3-settlement-obligation-comparison.md)。

## 为什么有这条记录

C4.3 要求在删除所谓重复的 condition/step 解释器**之前**，逐项比对
`host_adapter_settlement.py`、`turn_driver/host_todo_completion.ts` 与
`turn_driver/settlement.py` 的义务。此前没有留下这样的对照。本条目补上，
使下一个 PR 可以依据证据决定，而不是靠猜测。

这里的**义务**指：每笔事务必须恰好发生一次的决定或持久效果，且其属主必须能够
拒绝、重放或恢复它。

## 基线下的三个属主

| 位置 | 入口 | 决定属主 | Python 的职责 |
| --- | --- | --- | --- |
| A — Host Todo completion | `settle_host_todo_completion` | `host_todo_completion.ts`，经 `turn.host_todo_completion.evaluate` | 落盘作者提交的 vision JSON，解码产出的 plan，执行 CLI 步骤，求值 `continue_when` |
| B — Turn settlement | `execute_turn_driver_settlement` | `settlement.ts`，经 `turn.settlement.reduce` | 调用 provider effect、写 checkpoint、回传 observation |
| C — MCP host 门面 | `goal_mode_mcp.py` | 端到端走 A | 从 host 装载 A 的请求 |

## 义务矩阵

| 义务 | A 的决定者 | B 的决定者 | 是否等价 |
| --- | --- | --- | --- |
| 事务身份 | TS `turnInstanceId` / `expectedIdentity`，以 `SettlementIdentity` 呈现 | TS 身份 + 逐步骤 receipt 匹配 | 词汇相同，推导输入不同 |
| 请求校验 | TS `decodeRequest`、`TODO_ID_PATTERN` | TS 解码 + `receipt_missing` / `identity_mismatch` | 不等价：A 校验形状，B 校验已提交回执 |
| 写入前准入 | `guard` 步骤携带 turn instance id 调用真实 `quota should-run` CLI | `validation` 是已提交阶段；B 若需执行它会直接抛错 | **不等价**——两者义务不同，且不共用代码 |
| 生命周期完成 | `lifecycle_completion` 步骤，带 completion 参数 | 无对应项；完成并入 writeback | A 独有 |
| 持久写回 | `durable_writeback` 步骤，受条件约束 | 经 reducer 的 `durable_writeback` provider effect | 类型相同，执行与恢复不同 |
| 配额花费 | `quota_spend` 步骤 | `quota_spend` provider effect | 类型相同，执行与恢复不同 |
| 终态收尾 | `terminal_closeout` 步骤，最后发出且 `continue_when: null` | `terminal_closeout` provider effect + `terminal_closeout_requirement` | 类型相同，B 拥有需求判定规则 |
| 重放 / 恢复 | 适配器自身不持有 journal。但调用链仍然能恢复：`turnInstanceId` 从同一意图推导出稳定身份，CLI 持久化规范结算回执，continue-when 条件接受 `idempotent_replay` / `receipt_repaired`，因此以同一意图重新进入可以成功且只扣一个槽位 | 逐步骤 checkpoint、`resolve_prepared`、回执重放、重试上限 | **属主与粒度不同**——不是"有恢复与无恢复"之分 |
| 持久化 | 适配器只写一个临时 vision 文件；持久结算回执存放在规范的 CLI/quota 存储中，属主是 CLI | 持久 checkpoint 加来源 journal | 属主不同：CLI 回执与事务 journal |

## 发现

1. **两条路径都不是第二个策略属主。** 二者都把决定交给 TypeScript 属主，
   Python 只做传输。`host_adapter_settlement.py` 自身不携带生命周期策略。

2. **步骤词汇只部分重叠。** A 产出
   `guard / lifecycle_completion / durable_writeback / quota_spend /
   terminal_closeout`；B 的 `SettlementStepKind` 是
   `validation / durable_writeback / quota_spend / terminal_closeout`。
   `guard` 与 `lifecycle_completion` 在 B 无对应项；B 的 `validation` 是一个
   拒绝执行的阶段标记，而不是 A 的准入调用。

3. **恢复的差异在属主与粒度，而不在有无。** 适配器不持有 journal，但 MCP 路径
   并非无恢复能力。`turnInstanceId` 从同一意图推导出稳定身份，CLI 持久化规范结算
   回执，`spendContinueWhen` 接受 `idempotent_replay` / `receipt_repaired`。
   因此真实提交之后响应丢失的场景可以靠以同一意图重新进入而恢复：持久读回报告
   `settlement_pending` 或 `settled`，过早的终态完成被拦下，重试成功，且只扣一个
   槽位——见 `tests/test_goal_mode_mcp_settlement.py::test_real_mcp_completion_recovers_a_lost_mutation_response`，
   参数覆盖 `lifecycle`、`writeback`、`spend`。B 的恢复是单个事务内按 provider 步骤
   的粒度，靠自己的 checkpoint 与 `resolve_prepared`。整合方案必须保留 CLI 回执
   重放这条路径：既不能假定 MCP host 需要新增 journal，也不能假定 CLI 回执会随
   适配器一起消失。

4. **在已发布的能力调用点上，二者已经是组合关系。**
   `extensions/governed_capability_execution.py` 先用 A 的
   `classify_host_guard_snapshot` 做准入，再用 `execute_turn_driver_settlement`
   携带自己的 journal 执行事务。该调用点不支持"二者是竞争事务"的假设。

5. **Python 侧唯一的解释器很小，且是传输必需。** `_condition_matches` 支持
   `all`、`any`、`equals`、`nullish`、`object`、`not_object` 和
   `normalized_string_equals`；`_decode_provider_steps` 校验 plan 是四或五步的
   固定顺序。条件全部由 TypeScript 编写。改由 TypeScript 逐步求值需要每个步骤
   一次运行时调用，RFC 明确指出这不自动等于改进。

## 删除需要什么前提

以下今均不成立：

- 整合保留 CLI 的规范回执重放与稳定 turn instance id（当前正是它们让同一意图的
  再次进入成功且不重复扣费），同时为 A 的各步骤补上适配器本地 journal *或* 等价的
  效果接收方；或
- B 接受带 `continue_when` 的 `ordered_cli_sequence` plan，并接受 A 的 `guard`
  与 `lifecycle_completion` 类型，且不引入逐步运行时调用，并接手 A 路径今天依赖的
  CLI 回执重放；或
- 证明没有已发布的调用方端到端使用 A。

## 候选后续切片

1. **词汇统一（小）。** 若确有调用方同时采用两条路径，为 `guard`、
   `lifecycle_completion` 与 `validation` 指定一个类型属主，而不是让三个名字
   分散在两个文件中。不要合并描述不同义务的名字。
2. **A 的适配器本地行为刻画（中）。** 在任何恢复改动之前，先钉住适配器自身在
   "某步成功、后续步骤抛错"时的实际行为。端到端的响应丢失场景已由
   `test_real_mcp_completion_recovers_a_lost_mutation_response` 刻画；尚未刻画的是
   适配器自身的部分步骤行为，以及适配器与 CLI 回执之间的恢复边界在哪里。
3. **当前不建议。** 合并 A 与 B，或新增第三种 plan 格式，都会增加事务边界而
   不删除任何义务。

## 协同

涉及这些义务的在研或刚合并工作：
[#3412](https://github.com/loopx-project/loopx/pull/3412)（歧义结算恢复）、
[#5677](https://github.com/loopx-project/loopx/pull/5677)（调用方作者的首投递）、
[#7580](https://github.com/loopx-project/loopx/pull/7580)（失败 journal 存在未了结的
prepared effect）、[#7523](https://github.com/loopx-project/loopx/pull/7523)
（类型化回执与续跑）。任何实现切片都必须先调和对这些 diff 的理解。

## 本记录不成立的结论

不构成等价证明，不构成删除授权，不构成运行时资格，不构成收益测量，也不主张
任何一条路径是冗余的。恢复基线是今天已经存在的 CLI 回执重放；适配器本地的部分
步骤行为不在本记录范围内。源码事实按上述基线读取，之后可能漂移。
