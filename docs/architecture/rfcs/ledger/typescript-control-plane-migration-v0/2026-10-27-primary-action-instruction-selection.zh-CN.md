# Primary-action 指令选择

[English mirror](2026-10-27-primary-action-instruction-selection.md)

## 边界与归属

本 Stage 2B 切片删除 Python 指令决策 owner，保持调用方可见文字和渲染。
`work_items/interaction_contract.ts` 中的 `projectInteractionExecution` 在原有
`work_item.interaction_execution.project` 请求内同时选择 mode 与 primary 指令。
不新增 operation、handler 注册、runtime、provider 或 store。
下述验证 hold 解决之前，资格仍未完成。

删除 `work_items/primary_action.py` 中的 Python 符号：

- `protocol_first_candidate_action`：来源优先序、带地址标签、capability 候选数量、
  scoped successor 与 advancement 回退。
- `protocol_replan_action_packet_instruction`：仅结算、successor 和通用 typed-outcome 文字。
- `resolve_canonical_primary_action`：各 mode 的精确文字、回退和 child/peer lane 数量，
  包括空 child 列表优先于 peer lanes 的规则。

`interaction_contract.py::_project_interaction_execution` 发送一个可选 facts/views
carrier 和现有 `action_selection_context` 投影。最终 contract builder 请求并将返回
指令经 `_build_interaction_agent_channel` 传入 `build_primary_action_projection`。
`unadmitted_action_selection` 保留仅 mode 的 preflight 和精确结果形状。
TS owner 在读取指令事实前组合 `actionSelectionNeedsRecovery`；rejected/deferred
恢复不构建可执行指令。既有 Python 恢复分支、selection/capability channel 覆盖、
blocked-successor 覆盖及 blocked-wait 覆盖均保留。

Python 保留 `protocol_action_text` 和 `protocol_action_label`：Python `str`、嵌套
repr、真值、空白、标签前缀和按码点限制都是兼容 codec，包括 `...`，而非 trace
codec 的 `…`。`primary_action_instruction_facts` 传递这些视图及现有 Todo 资格／
lifecycle 来源事实，不选择指令，也无 Python 决策回退。Python 对每个列表来源至多
传输从相同强制转换视图判定的首个可选行；typed owner 应用相同谓词和来源优先序。
请求大小不随 backlog／Todo 列表长度增长。已注册 effective action 和
agent-scope 值导入生成的 TS exports。其他现有局部规则字符串保持精确拼写；
不新增共享词汇或持久化 schema。

Python 还保留 `protocol_monitor_action` 和经
`state_projection.next_action_resolution_trace` 构建的 resolution trace。
`primary_action` 仍先于可选 `resolution_trace`；channel 插入顺序和最终 Python JSON
序列化不变。仅当剩余消费者采用完整 typed action/trace 投影时，才删除 trace/monitor
facade。仅当最后一个 Python text/label 消费者退出，且完整 typed 兼容 codec 证明
Python coercion、Unicode、截断及 trace 等价时，才删除 scalar/label codec 和 facts
carrier。本切片不宣称这些 owner 已退役。

## 词汇预算

registry 的 `meaning` 写明 "module_budget counts modules under loopx/ whose text carries the standalone field token" 和 "the token count stays until Q11 decides whether the syntactic metric replaces it"；这一刻意保守的词法代理用于防止潜在字段 reader 数量出现无解释增长。
在相同 tracked-source 负载上，base `be1dc37a0` → `this commit` 的 `goal_boundary` 词法计数从 Python/TypeScript 30/2 → 29/3（总数仍为 32），原始失败为 `legacy field goal_boundary grew to 3 .ts modules; budget is 2`，以及 retrodiction `(13, 15)` 对比冻结值 `(12, 15)`。
迁移的句子 `repair goal_boundary.write_scope projection before attempting the selected write` 是逐字节一致的 agent-facing 文字，未新增 reader、writer 或 binder；smoke 对文字与 reader 的区分使 migration surface 保持 Python 16 / TypeScript 1。
保留该指令，registry 将 Python token 预算收紧至实测 29，并将 TypeScript 从 2 增至 3，词法余量为零；migration-surface 预算及其 anchor 维持 16/1 不变，而 registry 与 `RETIREMENT_ANCHOR` 按 anchor 机制要求在同一提交中一起调整；retrodiction 基线不变，预算接受实测计数后，实测结果恢复为 `(12, 15)`（`would_have_caught=12/15`、`caught_baseline=12/15`）。

## 经济性与等价

相对固定基线，三个删除函数跨度共 267 物理行（含间隔行则为 269）。生产 Python
净减 185 行，TS 增 109 行，生产总代码净减 76 行。primary-action 模块从 347 行缩为
152 行。新增 Python facts codec 为 77 行加间隔；其余 Python 变化为 transport／结果
传递。TS helper 内有 14 行局部 view decoder（`facts`、`object`、`text`、`rows`、
`count`），不另建 RPC surface。伴随的有界重构复用 `action_selection_context`，不另建 admission facts 编码器。
更广的 admission、monitor 和 trace 迁移延后。

在隔离的合成 SQLite fixture 上，分别使用 1 或 3 个 delegation binding；每条命令
各执行一次不计时的 base/head 输出和一次逻辑请求 trace：

| 命令 | Binding 数 | Crossings base → head | Stdout 字节 base → head |
| --- | --- | --- | --- |
| `agent-context --phase before_plan` | 1 | 1 → 1 | 1885 → 1885 |
| `agent-context --phase after_delegate_result` | 1 | 3 → 3 | 1612 → 1612 |
| 同一 result phase，带 Turn instance | 1 | 3 → 3 | 2594 → 2594 |
| `quota should-run` | 1 | 75 → 75 | 25520 → 25520 |
| `agent-context --phase before_plan` | 3 | 1 → 1 | 2247 → 2247 |
| `agent-context --phase after_delegate_result` | 3 | 3 → 3 | 1612 → 1612 |
| 同一 result phase，带 Turn instance | 3 | 3 → 3 | 2594 → 2594 |
| `quota should-run` | 3 | 75 → 75 | 25904 → 25904 |

排除以 `_at` 结尾的时间字段、`context_id`、`event_id` 和 `staleness_seconds` 后，
八组规范化 JSON 均一致。这证明字节数和规范化内容等价，不代表易变字段字面一致，
也不宣称延迟提升。不改变持久化格式，无数据迁移。

## 验证与剩余门禁

修改前合成 fixture 固定 182 个案例：全部 mode、候选来源优先序、回退及 lane 数量
分支、空／非法来源形状、布尔值、数值、嵌套值、空白、Unicode／非 BMP 和截断。
原生测试核对独立固定的指令和 recovery getter sentinel；Python 核对 codec 视图、
非 JSON `str` 及投影顺序。既有 Python CLI／Host 集成断言保留。仅当最后一个 codec 消费者退出，且原生
invariant 测试独立覆盖相同文字／来源分支时，才删除固定的 Python payload/view fixture。

超大 backlog 回归通过公开 Python 投影固定 22,000 行：lane 指令与 BASE 一致，
rejected selection 保留 BASE 恢复命令，且 `must_attempt` 和 `delivery_allowed` 均为 false。
编码后的指令事实保持在 16 KiB 以下。

<!-- validation-results -->
独立 review 的规则表判定选择语义为 EQUIVALENT，仅超大 eager transport 一行为
DIVERGENT；有界首个可选行 carrier 修复该问题：22,000 行的 facts 从
2,354,852 字节缩至 959 字节，lane 与 rejected-selection recovery 输出均与 base
逐字节一致，并由测试固定。修复通过 38 项 arbitration、37 项原生及 309 项 Python
adapter 测试，包含 182 案例 typed-instruction parity。`goal_boundary` 词汇预算决策
将 Python/TypeScript 从 30/2 调整为 29/3，并在本提交中同步调整
`RETIREMENT_ANCHOR`；smoke 报告 `semantic-vocabulary-drift-smoke: ok`，
architecture 测试 128/128 通过，retrodiction 恢复至未变的 12/15 基线。
完整 TS suite 为 5,528 项通过、0 项失败、75 项 PostgreSQL skip。原始完整 Python
suite 记录 27,169 项通过、908 项失败、234 项 skip、8 项 error；base 重放其
147 个失败文件，记录 2,353 项通过、905 项失败、18 项 skip、8 项 error，
在相同 guard 下复现 913/916 个不同的 failure/error ID，其中部分失败由 guard
限制引起。三个 head-only 失败中，两个 architecture 失败已由上述修复解决；第三个
由环境引起，因为验证期间安装了 dashboard 依赖，而 base 跳过该测试。最终代码的
44 模块 sweep 在 base 与 head 上具有完全相同的 84 个失败，新增失败为零，
通过数为 1,871 → 1,880，两边各有 20 项 skip；这些限定范围的运行不能证明完整
Python suite 全绿。最终 canary 报告 status: `passed`、ok: `true`、
merge_gate_passed: `true`、manual_holds: `0`，失败数为 0。四轮交错 benchmark
运行（每条命令 25 次计时、3 次 warmup）中，八条命令的字节数全部相等，两种 binding
数量的 crossings 均维持 1/3/3/75；每项中位数回退均在既定 5% 规则内，因此字节数、
crossing 和中位数检查通过，不宣称延迟提升。仅 CI 可验证的限制仍包括真实
PostgreSQL server、Node 22.22.3、Windows 和 browser lanes；PostgreSQL skip
不等于通过。main 的继承失败仍由 #7433–#7436 和 #7389 跟踪；本次验证未解决它们。

本地未验证 installed wheel、最低 Node、Windows、真实 PostgreSQL 及完整 CI；
PostgreSQL skip 不等于通过。提交确切 head 后的双语检查和 premerge canary 由
orchestrator／验证工程师负责。这些 hold 不授权更广的 authority/provider 修改或
降低验收。回滚仅需撤销代码，无需改写状态。
