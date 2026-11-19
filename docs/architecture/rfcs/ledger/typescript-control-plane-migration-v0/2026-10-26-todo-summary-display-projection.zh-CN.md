# Todo 摘要展示投影退役（2026-10-26）

| 边界 | 交付证据 |
| --- | --- |
| 目标／来源 | 既有 TypeScript 迁移 T3/T4 事务收益；基线 `c6462f902`。仅一个只读摘要展示切片，不新增 capability、RPC 操作、provider 或 runtime。 |
| 可观察缺口 | Python 仍在选择摘要／规划来源、规范化展示声明，并在请求 typed quota owner 选择 lane 前组装 handoff 展示。 |
| 唯一 owner | `todos/context_projection.ts` 通过既有 `todo.context.page` 统一负责独立摘要压缩、来源顺序／去重、后继就绪覆盖以及含终态的 frontier 字段保留。`todos/read_codec.ts` 复用 `handoffSource`；quota planning 在进程内先组合展示再决定 lane。两次完整规划决策保持独立。 |
| 删除的符号／调用方 | 删除 Python `_compact_todo_summary_item_record`、`_refs`、`_TEXT_FIELDS`、`handoff_context_source`。`compact_todo_summary_items`、`todo_summary_source_items`、`todo_planning_source_items` 的实现缩为传输 facade。`todo_index.py` 的两个 handoff 阶段改传原始记录事实。`todo_summary.py` 既有 compact/open 调用经 `attach_todo_handoff_notes` 使用同一原始事实适配器，无需新增决策分支。`quota_selection.py` 删除展示来源 join，改传原始展示事实。 |
| 保留 Python 的放置依据 | `summary_item.py` 保留公开导入签名、来源 key 兼容导出、空批次／有界传输、响应校验及 `todo_text_content_revision`。`handoff_note.py` 保留通用 JSON 转换，为 tuple、set、float 及其他非 JSON 标量提供 Python string/repr 事实，并保留分批／私有快照、拒绝传播及可变行 attachment identity。既有 handoff-gate 来源 IO／兼容适配保留。不新增 Python 决策回退或逐字段 RPC。 |
| Facade 删除条件 | 最后一个 Python 展示、索引、quota、管理及规划调用方改为在进程内消费既有 typed owner 后，删除摘要／handoff 传输 facade 及原始事实请求载体。`todo_text_content_revision` 仅在最后一个兼容调用方退出后删除。本切片不退役其他 context-page 调用方、委派事实、Turn recovery 或 monitor-poll facade。 |
| 格式／回滚 | 不改变持久化格式、authority、CAS、writer fence、lease、receipt、provider 选择或默认行为。仅回退代码即可，无需数据迁移。 |

## 成本与真实路径证据

生产源码：`summary_item.py` 从 329 降至 100 行，`handoff_note.py` 从 150
降至 112 行；Python 净减 273 行，TypeScript 净增 157 行，生产代码净减
116 行。七个生产文件共增加 242／删除 358 行。相关重构复用既有 handoff／read
codec，不新增 owner 或 RPC leaf。更大范围调用方退役沿用既有退出条件。

对每条命令、每个夹具规模（N=1／N=3）各执行一次真实 CLI 跟踪，结果如下：

| 命令 | 跨运行时调用：基线 → 候选 | Stdout 字节 N=1 | Stdout 字节 N=3 |
| --- | --- | --- | --- |
| `agent-context --phase before_plan` | 1 → 1 | 1885 → 1885 | 2247 → 2247 |
| `agent-context --phase after_delegate_result` | 3 → 3 | 1612 → 1612 | 1612 → 1612 |
| 同一 result 阶段，带 Turn identity | 3 → 3 | 2594 → 2594 | 2594 → 2594 |
| `quota should-run` | 75 → 74 | 25520 → 25520 | 25904 → 25904 |

预估的 73 次目标**尚未达到**：首次规划针对空 User 来源，没有展示行，因此原本
就不存在可删除的前置展示 RPC。仅退役非空 Agent 规划的展示调用。不得通过删除
第二次规划决策或合并无关调用方来凑足预估。调用减少不证明延迟改善；同条件
25 次性能对照仍由外部独立验收。

退役前的合成 quota Todo 行及其 Python 展示结果固定在
`tests/control_plane_ts/fixtures/todo_summary_display.json`。在外部隔离 runtime 中固定 Python／Node 墙钟后，八条完整 CLI 输出与重放基线
Python owner 的输出逐字节一致（包括 `context_id`）。独立 TS 用例覆盖
嵌套字段优先级、旧 identity、Unicode 与截断前凭据排除、有界且公开安全的阻塞
原因、仅 open advancement 可见的 hint、声明规范化、同 scope 最后 outcome 优先、
有序来源去重、显式 false readiness 以及终态 frontier 字段。Python 保留真实
File／SQLite CLI 恢复、可变行 identity、大／超大传输及 fail-closed 适配测试，
包括 tuple／set／float 事实。

聚焦验证：89 个 TS 测试通过；typecheck、变更 Python 的 ruff、配置内 mypy
（17 个文件）、diff 空白检查和语义 advisory 通过。要求的 Python 集合为 467 个通过、1 个临时路径深度限制的 skip，并存在四个
既有 legacy 路径失败（三个 quota legacy-provider 用例，以及
`test_absent_fence_preserves_legacy_path_without_starting_typescript`），在相同
环境加载基线 Python owner 后复现。它们仍是失败，不计为通过的 skip。mypy
配置文件列表不覆盖本次修改的 Python 模块。真实 installed-wheel、最低 Node
版本及隔离 PostgreSQL CI 尚未验证；未修改 PostgreSQL 代码。本源码检查点不
认证完整 CI、已安装交付或延迟收益。

审查修正：摘要展示复用 completion writer 的共享常量，将 `decision_scope_outcomes[*].schema_version` 保持为 canonical 记录中的 `todo_decision_scope_outcome_v0`，替换未注册的 `decision_scope_outcome_v0`；新增 TS 回归测试及 File／SQLite `quota should-run` 覆盖。
