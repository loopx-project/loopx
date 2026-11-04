# 当前 Todo 的来源读取并入现有上下文事务

本轮要求是退役 Python 桥接工作，同时保持单一规则所有者。本切片把当前 Todo 的完整来源读取和来源字段组装并入现有 `work_item.context.project` 请求。Goal 文档、User 作用域和 acceptance/oracle 的 IO 继续由 Python provider 执行。

| 边界 | 结果 |
| --- | --- |
| 迁移前 | Python 读取完整 canonical 集合，选出一个 active Todo，补充正文版本，然后发给 TS 上下文投影器。 |
| 迁移后 | `readAndProjectInteractionWorkContext` 在进程内复用现有 canonical 快照读取器，再调用原有上下文校验与投影。 |
| 退役 | 删除 Python 的 selected-Todo 分支和来源读取 import。包含它的适配器仍有真实 Goal/acceptance IO 调用方，因此保留。 |
| 完整性 | 保留同一个只读既有 store 路由、完整快照校验、分页与版本约束、归档/缺失拒绝、原始正文、规范化正文版本、状态与认领检查。不增加 Markdown fallback。 |
| 传输 | 不新增 RPC。现有上下文投影接收 `source_context`；canonical 读取沿用原有 15 秒预算。其余 Python provider 两侧仍只有两个粗粒度上下文请求。 |
| 关闭路径 | 没有 selected-Todo required read 时，不打开 provider。已提供来源的原生调用方继续使用原有纯投影。 |
| 恢复 | 正文在选择后改变时，File/SQLite 的公开 CLI 均拒绝交付。绕过 typed 来源门禁后，两条独立回归测试均失败。 |
| 权限 | 本次读取不授予认领、lease、执行、验收或写入权限。不迁移持久格式或活跃 Goal 数据。 |

## 证据与范围

初始不可变基线为 `ecbf905402ccfd9e4d75b2b963b52376b4ef7332`。更新主干后的集成和最终准确 head 证据写入 PR。四项 selected-work 测试在该基线也失败，原因是旧 capture/输出布局断言。已有 PR #7430 负责修复，本切片不放宽这些断言。

保留的检查位于 `tests/control_plane_ts/interaction_context_readback.test.ts` 和 `tests/control_plane/test_selected_work_requirements.py`。检查覆盖完整正文与独立备注、正文版本、变更/归档/缺失/损坏/不可用来源、不创建 provider、关闭路径和 canonical 数据不变。wheel/sdist 的实际执行、完整 CLI 匹配成本与实际源码版本在 PR 单列；测试数量和更少请求不能证明整个 Goal 的成果价值。

共享的规范化正文版本函数同时服务原有 canonical 展示 codec 与新的来源读取。Python 不重建规则所有者。只有文档、User 作用域和 oracle provider 都有真实原生调用方后，才退役其余 Python 上下文适配器；语言本身不是删除条件。
