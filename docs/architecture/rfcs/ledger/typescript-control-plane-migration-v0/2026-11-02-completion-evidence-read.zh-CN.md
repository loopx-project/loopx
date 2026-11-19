# 完整 durable-completion 证据读取（2026-11-02）

内置 `turn run-once` provider 是 Python durable-completion 投影的唯一生产消费者。
其 intent、完成后读取、普通恢复和终态恢复现在复用既有 `todo.read` 事务。
`todos/read_transaction.ts` 在进程内收集 canonical 来源分页；
`todos/read_projection.ts` 组合既有 completion fence，负责 continuation/recovery
验证、后继存在性检查和结果构造。canonical 拒绝不会回退到陈旧 Markdown。
恢复时 fence 拒绝仍优先于 continuation 投影。

## 经济性与保留边界

File/SQLite 插桩测得 Python/TypeScript 调用数：普通恢复 **2 → 1**、终态恢复
**2 → 1**、intent **1 → 1**、完成后读取 **1 → 1**、显式 legacy materialized
读取 **0 → 1**。canonical 收集（含分页）留在请求内部。legacy 增加一次调用是为了
删除 Python 语义权限，不据此声称延迟改善。

删除的 Python 权限包括 `_project_durable_completion`、终态 fence 组合、Turn
独立 fence 调用、整个 `completion_fence.py` facade 及已过时的公开持久记录读取接口。
`todo.completion_fence.evaluate` 已无字符串分派消费者，因此删除其注册；原生
fence 导入和测试保留。

Python 保留 registry/路径解析、legacy Markdown 观察与解码、Python 标量/容器
编码、拒绝异常兼容和传输。canonical 来源结果解码器与现有权限读取共享，以在
不增加 provider 请求的情况下保留不可用/拒绝的异常类型。兼容投影函数委托同一个
完整原生 owner，不解释 continuation 或 recovery。

当 `turn run-once` 原生化，且没有 Python 调用者需要 legacy 持久格式观察或投影
导入契约时，删除 facade。completion-evidence 读取种类届时可改成进程内调用；
其他 Python 消费者使用的普通 `todo.read` 操作继续保留。独立原生覆盖保留负例后，
刻画 fixture 可随该 facade 退役。不声称长程效果/效率收益。

## 证据与范围

迁移前语料固定 83 个完整 JSON 观察、精确诊断、字段缺席及有序缺失后继列表。
权限迁移前，基线插桩和既有聚焦测试通过；期望字节保持不变。原生 fence 测试
独立于该语料。真实 CLI 检查覆盖 File/SQLite 完成后崩溃恢复和陈旧展示身份。
交付报告记录精确命令、结果、源码行增减及尚未合格的门禁。不计划改变存储格式、
写入权限、计分或默认设置。此内部读取重构复用现有 CLI 旅程，不增加能力或配置界面。

wheel 与 sdist 分别安装到全新的 Python 3.12 环境。两个安装包均只从
`site-packages` 导入，并执行 completion evidence、canonical 拒绝和真实
canonical recovery；三项检查全部通过。

默认 SQLite 的同条件资格使用固定 base `867615e2c` 与当前 head，以及相同合成
fixture、32 次冷启动、128 次 warm typed 请求、64 次完整 canonical transaction、
64 次完整 CLI recovery 和 256 次请求 burst。base → head 结果为：冷启动
p50/p95 115.522/128.518 → 102.829/135.269 ms；完整 transaction
68.774/103.729 → 9.806/14.320 ms；完整 CLI recovery
358.661/611.397 → 264.324/334.687 ms；进程树 RSS 的 idle/burst
30/30 → 32/33 MiB。冷启动 p95 差异为 +6.747 ms（+5.25%），但中位数下降且
低于 25 ms 加性余量。warm typed 请求包含持久化远程读取，不套用非持久化进程内核
预算；完整 transaction 和操作者 CLI 在同条件 SQLite 基线上均明显改善。
