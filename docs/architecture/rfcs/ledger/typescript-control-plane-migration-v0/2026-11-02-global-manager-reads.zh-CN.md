# 全局摘要与门禁读取事务 — 2026-11-02

已交付的 `global-summary` 和 `global-gates` CLI 命令现调用
`presentation/global_manager_read.ts` 中的
`presentation.global_summary.project` 与 `presentation.global_gates.project`。
这是完整读取投影：lane 选择和排序、门禁问题与路由、精确目标优先于作用域派生
阻塞引用、可见工作校验、进展过滤、截断前总数、分组、动作以及 envelope 组合均由
TypeScript 所有。决策关系与投影 envelope 封装通过已有 owner 在进程内组合，
没有新增独立叶级 RPC。

## 边界与跨运行时调用

冻结的合成刻画覆盖完整 JSON 与 Markdown 字节和源读取顺序，包括空或不健康源、
混合 lane、重复 Goal、quota 不可用、Agent 不匹配、提前终止、时间范围和 limit
规范化、精确/作用域引用、脱敏，以及过期、不可读、覆盖不完整的上游 envelope。
它在生产代码修改前已通过原有 Python 实现；预期输出保持不变。

仪表测得投影跨运行时调用：摘要 **1 → 1**，门禁 **R + 1 → 1**，R 为非空关系
求值次数。混合与提前终止门禁用例为 **2 → 1**；不需关系求值的空源、不健康源和
限定 Agent 用例为 **1 → 1**。这些计数不含保持不变的 status/quota 源 provider
及其内部请求。

Python 保留源采集与 quota 失败适配。门禁资格与用户门禁分类保留为单一 Python
源侧谓词：`_gate_source_facts` 复用共享的 `open_user_gate_todo_items` owner，
它还有其他 Python 调用方。增量扫描必须在进一步读取 host 之前决定提前停止。
扫描保留去重和原有停止位置，仅向门禁样本附加首个正式门禁行与资格布尔值。
TS 严格解码这些事实，组合 owner 选择、阻塞引用与完整门禁投影，不重复分类
或资格判断。这避免逐候选桥接调用及越过 limit 的额外读取。

原有 Python lane/门禁构建、关系调用、进展过滤、排序、计数、分组、动作/结果
组装以及 envelope 组合已删除。Python 保留文本脱敏和时间戳兼容 codec、已有错误
渲染以及 CLI 和展示 smoke 使用的 Markdown 渲染。`SOURCE_SURFACES` 仍是原有
注册上下文中的源采集元数据，传给 typed 投影。持久化格式、权限和状态变更不变。

## 退出与资格边界

当原生调用方通过 TS 所有的 status/quota 采集获取这些源事实并直接消费完整投影，
且 CLI 和展示 smoke 不再导入 Python Markdown 渲染器时，删除 Python facade、
codec 和两个桥接方法。只有当用户门禁分类拥有 TS owner 且源采集迁移到 TS 时，
才能迁移源侧谓词；届时删除其 Python 实现。最后一个 Python 调用合同退役时
删除冻结迁移 fixture，保留独立语义测试。

保持现有 CLI 表面：base 与 candidate 均未注册 `summary-all`；受支持的摘要命令为
`global-summary`。在相同时钟的合成 canonical runtime 上，真实 CLI 的 JSON 与
Markdown 读取结果与归档 base 逐字节一致。这是源码 checkout 资格，不是 wheel/
sdist 或已安装主机资格。不声称延迟、完整成本或长程效果收益。

聚焦 Python 刻画与已有摘要测试通过（31 项），38 项 TS owner/envelope/关系/分发
测试、类型检查、词汇门禁（独立 twins 为 45/45）、可维护性、接口预算以及
Markdown 展示 smoke 也通过。File 与 SQLite 集成 canary 行为通过；首次并发运行
超过未修改的 60 秒总预算，重跑通过（File 18.15 秒，SQLite 13.44 秒）。较广的
源码 Python 测试选择为 168 项通过、2 项失败，两项均在归档 base 复现：quota CLI
scheduler-envelope 测试缺少必需的决策捕获参数，steward 端点词汇测试仍预期旧端点
集合。未通过修改门禁或 fixture 预期来处理这些失败。

源侧谓词修复后，未修改的刻画与摘要测试选择全部通过（31 项）；全局管理读取与
handler 测试选择通过 22 项 TS 测试，包括拒绝畸形事实和采用源侧资格。
类型检查、ruff、词汇漂移、可维护性、热路径预算、Markdown 基础组件及全部六项
CLI 一致性比较通过。产品代码相对基线共增加 321 行、删除 629 行（净减 308 行）。
fixture 与门禁保持不变。
