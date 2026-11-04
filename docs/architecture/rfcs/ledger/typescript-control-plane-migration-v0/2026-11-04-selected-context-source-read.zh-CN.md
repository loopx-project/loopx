# 当前 Todo 的来源读取并入现有上下文事务

本轮要求是退役 Python 桥接工作，同时保持单一规则所有者。本切片把当前 Todo 的完整来源读取和来源字段组装并入现有 `work_item.context.project` 请求。Goal 文档、User 作用域和 acceptance/oracle 的 IO 继续由 Python provider 执行。

| 边界 | 结果 |
| --- | --- |
| 迁移前 | Python 读取完整 canonical 集合，选出一个 active Todo，补充正文版本，然后发给 TS 上下文投影器。 |
| 迁移后 | `readAndProjectInteractionWorkContext` 在进程内复用现有 canonical 快照读取器，再调用原有上下文校验与投影。 |
| 退役 | 删除 Python 的 selected-Todo 分支和来源读取 import。包含它的适配器仍有真实 Goal/acceptance IO 调用方，因此保留。 |
| 完整性 | 保留同一个只读既有 store 路由、完整快照校验、分页与版本约束、归档/缺失拒绝、原始正文、规范化正文版本、状态与认领检查。不增加 Markdown fallback。 |
| 传输 | 不新增 RPC。现有上下文投影接收 `source_context`；canonical 读取沿用原有 15 秒预算。原有上下文 plan/project 阶段保留；其余 Python provider 仍有各自的调用。当前 Todo 快照不再单独跨运行时传输。 |
| 关闭路径 | 没有 selected-Todo required read 时，不打开 provider。已提供来源的原生调用方继续使用原有纯投影。 |
| 恢复 | 正文在选择后改变时，File/SQLite 的公开 CLI 均拒绝交付。绕过 typed 来源门禁后，两条独立回归测试均失败。 |
| 权限 | 本次读取不授予认领、lease、执行、验收或写入权限。不迁移持久格式或活跃 Goal 数据。 |

## 证据与范围

初始不可变基线为 `ecbf905402ccfd9e4d75b2b963b52376b4ef7332`。更新主干后的集成和最终准确 head 证据写入 PR。四项 selected-work 测试在该基线也失败，原因是旧 capture/输出布局断言。已有 PR #7430 负责修复，本切片不放宽这些断言。

保留的检查位于 `tests/control_plane_ts/interaction_context_readback.test.ts` 和 `tests/control_plane/test_selected_work_requirements.py`。检查覆盖完整正文与独立备注、正文版本、变更/归档/缺失/损坏/不可用来源、不创建 provider、关闭路径和 canonical 数据不变。wheel/sdist 的实际执行、完整 CLI 匹配成本与实际源码版本在 PR 单列；测试数量和更少请求不能证明整个 Goal 的成果价值。

共享的规范化正文版本函数同时服务原有 canonical 展示 codec 与新的来源读取。Python 不重建规则所有者。只有文档、User 作用域和 oracle provider 都有真实原生调用方后，才退役其余 Python 上下文适配器；语言本身不是删除条件。

## 最终源码候选资格

集成基线：`d29ec8bf2a105aac24629bcbf3dfb7c97aea51fe`。源码候选：`c137daecc56a5f1784097742b212d26b27f83892`；后续文档更新不改变四个生产文件。

- Python 适配器新增7行、删除35行，净减28行。生产代码合计新增63行、删除37行，净增26行。本次收敛来源与传输工作，不声称迁走了原本由 Python 持有的领域规则。
- 138项聚焦/相关 Python 测试和93项原生测试通过。四项旧 capture 布局失败保留为基线证据。绕过来源门禁后，File/SQLite 两条真实正文变更回归均失败。
- wheel/sdist 在独立环境执行，File/SQLite 四个实际命令读回通过，并核对源码文件哈希。
- 实际 `loopx` 入口使用 SQLite、500条历史记录和30对交错匹配样本：p50 为647→657 ms，p95 为779→799 ms。默认入口在既有5%/25 ms回退范围内，不声称加速。
- 兼容的 `python -m loopx.cli` 入口，匹配样本 p95 为1309→1486 ms，仍属未闭合的性能阻断。已观察到的导入/参数注册热点不能证明尾延迟的根因；原始结果与较早样本全部保留。本候选不认证完整成本或真实 Host 采用。
- 默认入口30对样本的完整内联工作上下文一致。仅固定事件时钟输入后，事件ID一致。采集时钟和计算出的age差异保留在原始观察中，不归一化身份、归属、正文、字段或拒绝结果。

**作者实现已完成；性能复核和最终准确 head 的复审完成前，继续阻断合并。** 本轮不执行合并、部署、authority迁移或持久数据删除。

### 后续上游 capture 集成

候选 `ff4e94459` 集成最新基线 `ce4cfc1ce`，四个改动生产文件与 `c137daecc` 字节一致。上游现在返回 capture 导航引用；原有产品读取器校验后打开完整当前观察。在该基线上，实际 File/SQLite 入口的完整工作上下文与事件身份保持一致；业务 Effect 调用在这组较新观察中为74→73。

六项旧断言仍直接读取 stdout 顶层 `should_run`，因此最新 main 和候选均为2通过/6失败，失败身份与边界相同。失败继续保留。此前安装包和成本测量保留各自版本；不声称最新主干的完整成本或 Host 资格已通过。
