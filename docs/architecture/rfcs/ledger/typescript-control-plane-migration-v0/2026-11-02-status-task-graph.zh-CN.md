# Status 任务图组合（2026-11-02）

## Owner 边界

本检查点将可选 status 任务图的完整决策从 Python `work_items/task_graph.py`
迁入现有 TypeScript `work_items/task_graph.ts`。单个
`work_item.task_graph.status` 操作拥有所选工作、claim 展示、用户 gate
选择/计数/上限/溢出、有界前驱遍历、run 选择与 lineage 连接、证据、handoff、
repair、去重、悬空边拒绝、时间戳及完整性/限制。它在进程内组合现有 topology、
Todo 摘要、分类和 handoff owner。独立的 `work_item.task_graph.topology`
handler 已退役；topology 作为内部组合函数保留，由 status 及其语义测试使用。

Python 提供源形状的 status 事实、latest-run 事实和后备时间。它保留公开文本
脱敏/规范化事实。status 的 SHA-256 节点/边身份物化迁入组合器，Unicode 和
`None` 编码逐字节一致，从而使用实际历史身份去重，而非尚未求值的 descriptor。
Goal-map 也复用同一套原生身份、状态及 topology-row 函数；三个 Python 规则 helper 已删除。源字段限定为现有 Todo 读取字段及实际消费的 status lane。
派生展示文本使用下文限定的组合文本压缩边界。不存在 Python 的选择、遍历、
gate、证据、handoff、lineage、repair 或限制后备决策。

`build_goal_task_map` 保持公开签名及 `chat_status_api.py` 输出逐字节一致。
它向 `projectGoalTaskMap` 提交源数据/公开文本事实，由原生实现物化节点/边身份、
状态及元数据。现有原生 topology 事实请求保留兼容合同。被省略的 opaque 元数据
不会提前字符串化；非 JSON 公开文本值在 codec 边界标记为不可用，仅在消费时拒绝。
无调用方的 status builder、attacher、源收集器、ref helper 和常量已删除。
没有新增 Python/TypeScript 同名文件对。

相对归档基线，产品物理行差异为 +865/−1030，净 −165 行。
`task_graph.py` 从 1,044 行降至 255 行：一个模块级递归公开文本 codec 和两个
源数据/传输 facade，不含任务图决策规则。测试、fixture 和文档单独计数。

## 公开文本与保留的重复规则

选择 (a)：没有第二套 TS 脱敏规则。源字符串必须提供 Python 公开文本事实，
缺少事实时抛错。本地 fallback 仅对已登记的公开事实输出或显式组合字符串调用
`compactTodoReadText`。插值组件断言为已登记公开字符串或数字计数。原生测试枚举
组合标题、身份、引用及原因，并拒绝未限定的源字符串。构造器包括 claim/lease、
evidence 前缀及公开证据的前缀切片、handoff 身份/标题/原因、缺失 ID 的 gate、
gate 溢出、latest-run 标题、repair 默认文本和生成节点身份的连接。固定原因来自
原生图构造；生成身份仅含 ASCII 标识符/hash 字符。status 调用方已对 Goal scope
做公开规范化。模块不再复制 `INTERNAL_STATE_SHAPE_PATTERNS` 脱敏规则。

`rg` 未找到 Python `open_todo_items` 的等价 TS owner：`read_codec.ts` 提供
压缩/字段，`context_projection.ts` 提供规划/展示源，`summary_lanes.ts` 选择
规范化摘要 lane，不实现历史 source-key、先去重再排序、有界开放项展示选择。
Python 仍服务 status attention/monitor、project-asset 读回及 Todo 摘要渲染。
因此 `statusOpenItems` 暂时保留这项有限重复。退出条件：这些调用方迁入统一的
原生 Todo open-display 选择 owner，验收排序/handoff/上限一致性，再删除 Python
选择并让图委托同一 owner。不得用准入语义不同的摘要 lane 选择替代。

codec 缓存重复源字符串，只计算字段实际使用的上限；不从截断文本推断源脱敏事实。展示上限和不支持文本标记仅是本地传输事实，
不构成新的共享决策词汇。
下表包含初始 Goal 公开文本调用；“之前”指本次评审的 encoder 初稿，每个源字符串
调用六次（完整安全检查加五个展示上限），并非最初的全 Python 图：

| Status 用例 | 公开文本调用次数：之前 → 之后 |
| --- | --- |
| `diamond_at_cap` | 127 → 32 |
| `cycle` | 79 → 22 |
| `missing_prerequisite` | 31 → 12 |
| `open_predecessor` | 79 → 24 |
| `condition_and_lineage` | 55 → 19 |
| `predecessor_overflow` | 193 → 47 |
| `duplicate_identity` | 97 → 19 |
| `long_public_text` | 61 → 21 |
| `redaction` | 61 → 21 |
| `handoff_evidence` | 73 → 21 |
| `unicode_ids` | 43 → 15 |
| `all_empty` | 1 → 1 |
| `gate_overflow` | 145 → 24 |
| `no_selected_run_gate` | 43 → 17 |
| `repair_evidence` | 79 → 29 |
| `stale_source_time` | 43 → 18 |
| `absent_goal` | 1 → 1 |
| `invalid_goal` | 1 → 1 |

最大刻画请求 `long_public_text` 使用传输层 ASCII 转义的紧凑 JSON 参数计量，
6,797 → 4,899 字节（−27.9%），调用 61 → 21（−65.6%）。这些是参数字节，
不含 RPC framing。最大调用用例 predecessor overflow 从 193 降至 47。
这是 codec/请求成本证据，不代表端到端延迟或效果收益。

## 跨运行时调用与一致性

插桩统计公开 Python 图入口触发的每次 `effect_runtime_result`，包括 Todo
展示/handoff helper：

| 操作 | 迁移前 → 后 |
| --- | --- |
| 所选工作，包括 topology/错误证据情形 | 4 → 1 |
| 用户 gate 溢出 | 7 → 1 |
| 无所选 Todo、但有 run/gate 节点 | 4 → 1 |
| 有效 Goal、事实全空且无图 | 0 → 1 |
| 缺失或无效 Goal | 0 → 0 |
| Goal 任务地图 | 1 → 1 |

有效空快照由原生组合器决策；只有缺失或无效 Goal 在传输前返回。原生请求拒绝后
没有 Python fallback。大请求复用现有本地快照传输。

冻结的合成刻画语料在实现前已于未修改的
`fb16c6cb1369efce52a673030c914b38e1dac166` 通过（21 个用例）；预期 JSON
字节保持不变。独立原生用例验证语义，而不将刻画语料当作规范。status Markdown
渲染器没有图专属章节。真实 `status --include-task-graph` CLI 在隔离的 File 和
SQLite home 上，以固定观察时钟对比归档基线，JSON 和 Markdown 均逐字节相同：
分别为 33,417/33,445 及 5,120/5,126 字节。实际 chat `_goal_task_map`
入口使用相同的规范 File/SQLite 源，两者均逐字节一致，为 2,310 字节。三个已冻结的
Goal-map 用例在重构前已于归档基线通过；fixture 预期输出未修改。

## 验收与退出

本次重跑：六个指定 Python 套件及 `rg build_goal_task_map / chat_status_api`
找到的全部测试共 355 通过、2 失败；原生图/handler/loading 测试 50 通过。
两个变更 Python 文件的 Ruff、control-plane typecheck、任务图 fixture、attention
queue、语义词汇、可维护性 smoke、六项 CLI/chat 一致性和 `git diff --check`
均通过。早先接口预算和集成 canary 证据仅保留为历史，不声称本次重跑。
quota planning-horizon 套件仍有两项
失败（File 和 SQLite 缺少 `planning_horizon`），使用同样测试在未修改的
`git archive HEAD` 基线副本中复现；不将其报告为通过。未放宽任何 gate、预算、
词汇锚点或基线。

当 status 调用方直接向原生 owner 提供源/公开文本事实，且保留的公开文本 codec
已有独立验收的原生替代时，Python status facade 才可退出。只有这些调用方
改为进程内 owner 后，才移除其操作。Goal-map facade 使用相同的 codec 退出条件。facade 移除具备独立公开入口覆盖后，可退役刻画语料。

相邻清理移除了过时 topology RPC 和 callback 表面；没有引入无关重构或产品
旅程。现有 CLI、attention queue、chat 及 review-packet 消费方保持其公开
payload。不声称端到端延迟、完整资源成本或长程效果/效率收益。
