# C4.3 结算词汇确认

- 上级：[controller/executor RFC，C4.3](2026-10-27-refactor-handoff.zh-CN.md#c43--settlement-adapters-over-existing-primitives)。
- 前序：本目录 2026-10-29 的《C4.3 结算义务对照》条目。此处只用文字提及而不做
  链接：那条目尚未进入基线分支，指向缺失文件的链接会让严格文档构建失败。
- 基线：`544aa0fee`，2026-10-29。结算属主自 `fb331f45` 起未变。
- 状态：只读确认。不改代码、schema、wire、registry 或默认值。
- [英文版](2026-10-29-c4-3-settlement-vocabulary-confirmation.md)。

## 为什么有这条记录

C4.3 结算义务对照列出一个候选后续切片：*词汇统一（小）。若确有调用方同时采用
两条路径，为 `guard`、`lifecycle_completion` 与 `validation` 指定一个类型属主，
而不是让三个名字分散在两个文件中。不要合并描述不同义务的名字。*

该候选以一个问题为前提，本条目回答它：这三个名字描述的是不是同一个义务？
对照条目已经暗示它们并不全等价，但没有定论；在这里靠猜，要么会错误合并，
要么会错误拒绝合并。

## 三个名字

| 名字 | 属主 | 位置 | 作用 |
| --- | --- | --- | --- |
| `guard` | `turn_driver/host_todo_completion.ts` 的 `STEP_KINDS` | host provider plan 的第一步 | 带 turn instance id 执行 `quota should-run`；其 `continue_when` 决定后续步骤是否继续 |
| `lifecycle_completion` | 同上 | 第二步 | 带 completion 参数执行 `todo complete` |
| `validation` | `effect_program.ts` 的 `SETTLEMENT_STEP_KINDS`，以及 `turn_transaction_contract.json` 的一个阶段 | 调用方必须已经完成的事务阶段 | 断言准入已经发生；它从不被执行 |

## 证据

1. **`validation` 在两个运行时都不是可执行效果。**
   `effect_program.ts` 定义
   `TurnProviderStepKind = Exclude<SettlementStepKind, "validation">`；
   `effect_program.py` 镜像了同一个排除，
   `require_turn_provider_step_kind` 会抛出 `validation is not a provider effect
   step`。`settlement.ts` 在 reducer 试图执行它时抛出
   `unsupported base settlement step validation`，并且它收到的是
   `committed_payloads.validation = {}` 与 `require_validation: true`，
   即该阶段由调用方履行，B 从不执行它。registry 本身已经用同样的话记录了这点：
   `loopx/semantics/vocabulary_v0.json` 里 `validation` 的 value note 写明它是
   "dispatcher 从不执行的那一步"、"不携带命令模板"，并且"被直接种下——已完成阶段
   已包含它时直接发出回执"。

2. **`guard` 与 `lifecycle_completion` 是被执行的步骤。**
   `host_adapter_settlement.py` 解码一个 `ordered_cli_sequence`：四或五步，
   固定顺序为 `guard → lifecycle_completion → durable_writeback → quota_spend
   → [terminal_closeout]`，随后对每一步调用 `run_cli(step.args)`。
   每个非末步都带一个 `continue_when`，决定后续步骤是否继续。

3. **两套取值恰好在问题所在处不同。** A 产出 `guard`、`lifecycle_completion`、
   `durable_writeback`、`quota_spend`、`terminal_closeout`；B 的
   `SettlementStepKind` 是 `validation`、`durable_writeback`、`quota_spend`、
   `terminal_closeout`。`guard` 与 `lifecycle_completion` 只出现在 A，
   `validation` 只出现在 B。仓库自带的顾问工具
   （`scripts/generate_semantic_inventory.py --report`）没有把这两套列为合并
   候选，因为它们的取值集合不同。

4. **`lifecycle_completion` 在 B 无对应项。** B 把完成义务放在
   `durable_writeback` 里——当结果为 `validated_completion` 时，写回载荷必须满足
   完成条件——以及 `terminal_closeout_requirement` 里。

5. **已发布的一个调用点已经用两套词汇表达同一个准入决定。**
   `extensions/governed_capability_execution.py` 用 A 的
   `classify_host_guard_snapshot`（`host_todo_completion.ts` 的 `classify_guard`
   阶段）判定调用方给出的准入快照，断言 `should_run` 与类型化结算身份，
   随后调用 `execute_turn_driver_settlement`，其 `completed_phases` 已经包含
   `validation`。而 MCP host 路径（`goal_mode_mcp.py` →
   `settle_host_todo_completion`）是自己执行 `guard` 步骤的。

## 结论

- `guard` 与 `validation` 描述的是同一个义务的两个位置：执行准入的一方，与拒绝
  执行、只要求准入已被履行的一方。它们不是同一个值的两种拼写。改名会掩盖这个
  不对称，也会破坏 B "阶段从不被执行" 的不变式。
- `lifecycle_completion` 是不同的义务，保留自己的名字。
- 因此，候选切片 *为三个名字指定一个类型属主* **撤回**。只要回到各自属主阅读，
  这些名字本身已经不含糊。

## 这次确认留下什么

真正存在的那个词汇没有类型属主，而且字面量写了两遍：

- `loopx/control_plane/turn_driver/host_todo_completion.ts` 定义了本地的
  `STEP_KINDS`；
- `loopx/control_plane/host_adapter_settlement.py` 定义了私有的 `_STEP_KINDS`，
  并用它校验 TypeScript 产出的 plan 顺序。

两个符号都没有导出，都没有登记进 `loopx/semantics/vocabulary_v0.json`
（已登记 28 个词汇，`settlement_step_kind` 只覆盖 B 那一套），仓库的清单工具
也看不到其中任何一个载体。TypeScript 扫描匹配的是
`export const NAME = [...] as const;`，Python 扫描匹配的是模块级 `UPPER_NAME`
赋值，所以一对私有且未导出的字面量对 registry 策略所依赖的漂移 smoke 始终
不可见。手工维护的 Python 副本可能与 TypeScript 字面量漂移，届时会以
`TypeScript host provider step order mismatch` 的形式在运行时暴露。

这是一个独立切片：为 host-adapter 的有序步骤类型给出同一个共享属主，并登记进
词汇 registry，让 guard/validation 的区别从散文变成机器可读。它必须先调和在途的
结算工作：#3412、#5677、#7580、#7523。

## 本记录不成立的结论

不构成等价证明，不构成删除授权，不构成 registry 修改，不构成运行时资格，也不
主张任何一条路径是冗余的。源码事实按上述基线读取，之后可能漂移。
