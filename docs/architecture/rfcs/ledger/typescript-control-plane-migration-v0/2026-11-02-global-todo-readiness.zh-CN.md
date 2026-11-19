# Global Todo 就绪分类归属切换 — 2026-11-02

## 边界与经济性

基线：`fb16c6cb1369efce52a673030c914b38e1dac166`。本次有界切片将
完整 Global Todo 就绪决策从 `global_todos.py` 移至现有
`todos/decision_scope.ts` owner 的 `projectGlobalTodoReadiness`，注册为
`todo.global_readiness.project`。`loopx global-todos` 与
`loopx goal-portfolio` 均通过共享展示适配器使用它；portfolio 对每个已注册
Agent 分别分类。

TypeScript 负责 selected/lane/summary/frontier 的有序遍历、首个来源优先并补齐
缺失字段的合并、来源/状态溯源、进程内
`todoGateRelation`、blocked/deferred/runnable 优先级，以及警告顺序。
`normal_delivery_allowed` 仍只是就绪投影信号，不授予执行权限。复用现有就绪值；
新增版本化请求/结果仅描述本次有界投影，不构成新的生命周期或权限词汇。

测试插桩固定所有跨运行时请求，包括 envelope sealing：

| 操作 | 基线 | 候选 |
| --- | --- | --- |
| 单次分类，同时存在 gate 与候选 | 1 次关系请求 | 1 次完整就绪请求 |
| 单次分类，无 gate | 0 | 1 |
| 单次分类，无候选（含空 payload） | 0 | 1 |
| Global 命令，分类一个有 gate 的 Goal | 2，含 envelope | 2，含 envelope |
| Global 命令，无 gate 或空分类 | 1 次 envelope | 2，含 envelope |
| Portfolio，两个有 gate 的 Agent | 2 | 2 |
| Portfolio，两个无 gate/空分类的 Agent | 0 | 2 |

无 gate/空路径的增加是有意且已披露的。不声称延迟或长程效果/效率收益。

产品物理行差异：+163/−166 行，净 −3。Python 删除来源遍历/合并 helper、
blocked/deferred 分类、就绪优先级组合及独立 safety-rank 表。删除无生产调用方的
Python 标量 `todo_gate_relation` 桥及其 TS dispatch；对应 smoke 和 decoder 测试
使用 summary 仍在调用的批量关系桥。

Python 保留 `open_user_gate_todo_items` 为唯一 gate 选择 owner：summary、
stall repair、Todo summary 和 agent scope 仍依赖它。有序选择、done 过滤及
Python 相等去重不可复制到 TypeScript。已选 gate 以解码 facts 和目标文本传输。
共享 `_facts` 保留持久化 ID/scope 归一化与旧 task-class 解释。有界请求仅传输
selected/lane 记录、delivery 信号以及 summary/frontier 的 list 字段。Python
通用解码每个 list 的 dict 元素；TypeScript 独占候选 list key 遍历规则，不再递归
遍历整个 payload。最大冻结用例的紧凑 UTF-8 JSON 请求从 9,749 降至 6,447 字节
（减少 33.9%），输出字节不变。Python 还保留 status/quota IO、公开文本脱敏、
`_work_kind` 展示解释、最终分组/去重/展示排序、envelope sealing 和 Markdown。
这些兼容与展示边界不是第二个候选就绪 owner。

Facade 退出条件：当 global 与 portfolio 的宿主/读模型都直接消费 typed Todo
snapshot 和投影，且各自边界已原生承接旧元数据解码及公开安全渲染时，删除
`project_global_todo_readiness` 及其字段解码 sidecar。当两个消费者均不再需要
Python 归一化时，删除共享展示适配器。在该退出点，持久语义与公开入口测试覆盖
保留契约后，移除临时 characterization corpus/archive 对照。

## 资格与限制

七个合成 characterization 用例先在未改动的 Python 上通过，然后冻结完整
JSON/Markdown 字节及基线/候选 crossing 列表。fixture SHA-256 为
`eaf17921f81357329ba7bc3c7dcb34253eed83b53d31c31d6dba0fdb5c547641`。
覆盖互补重复字段、冲突状态、delivery 拒绝、精确目标/scope 冲突、无关 gate、
blocked/deferred 重叠、缺失身份、两个 Agent、警告顺序、空输入与公开文本脱敏。
独立 TS 测试断言优先级、拒绝及精确目标/scope 冲突语义，并拒绝畸形协议。
Python decoder 测试拒绝畸形结果且不作回退。

`tests/test_global_todo_readiness_characterization.py::test_production_cli_archive_parity`
通过 archive `HEAD`、不用 stash，在同一合成临时 home 上以真实 File 和 SQLite
provider 对比两个公开命令的 JSON 与 Markdown。仅在导入生产 CLI 前冻结时钟；
字段存在性、诊断、来源版本与哈希均原样比较。八组命令/provider/格式均一致，
并要求结果实际包含 Todo 与 Agent。此证据资格为 source checkout，不覆盖安装包
或前端行为；没有新增调用方能力或用户旅程。

聚焦 scheduler-state 读回测试
`test_real_cli_quota_should_run_discloses_unreadable_scheduler_state` 在修改树与
archive 基线上都失败：其 quota 命令缺少 Turn 身份，返回
`QUOTA_VALIDATION_FAILED`，要求 `--turn-instance-id` 或 `--begin-turn`。
这是已有的调用方/验证不匹配，位于就绪 owner 之外。保留该失败；不得放宽参数门禁，
也不得改写本次迁移的期望输出来掩盖它。

语义漂移仍为 `independently_maintained=45/45`；未新增 control-plane 源文件或
同名配对。Maintainability 与接口预算保持不变并通过。集成 canary 的 File 和
SQLite 均通过。diff 范围 advisory 在四个变更产品源文件中未发现受支持的新词汇
载体；该工具的支持范围不包含测试源文件。

## 评审修正验证

删除重复 gate 选择并收窄请求后：指定 Python 套件 151 通过 / 1 失败（上述
scheduler 调用参数不匹配）；原生 Node 测试 19 通过 / 0 失败；边界套件
28 通过 / 0 失败。四个已修改 Python 文件的 Ruff、control-plane typecheck、
两个指定 smoke 和 diff 空白检查均通过。七个冻结 characterization 用例的输出
字节不变，fixture 哈希不变。File/SQLite 生产 CLI 对照覆盖八组命令/格式/provider。
聚焦边界回归验证未知 list 字段的通用解码、排除无关对象及复用共享 gate 去重。
未 commit、stage 或 push；已删除生成的 uv.lock。
