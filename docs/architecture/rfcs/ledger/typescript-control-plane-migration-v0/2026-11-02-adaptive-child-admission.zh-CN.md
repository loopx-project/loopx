# 自适应子 Agent 准入 — 2026-11-02

本检查点将完整的自适应原生子 Agent 准入决策从
`quota/task_orchestration_admission.py` 移至 peer orchestration owner 旁的
`quota/adaptive_children.ts`。既有 `multi_subagent` capability 及其 builtin
provider 不变；这是 quota 内部 owner 切换，不新增 capability、provider、权限或前端设置。

单次 `quota.task_orchestration.admit` 请求拥有候选资格、User 依赖排除、协调者选举、
有序拒绝原因、主任务选择、容量延后、首个冲突 lane、子任务排序、brief 构造及
上下文／执行策略。Capability 要求和 quota 调度重叠复用既有 TypeScript owner，
在进程内执行。独立的调度重叠 RPC 已删除。普通 lease 匹配保持大小写折叠语义，
Goal allow-scope 匹配保持大小写敏感。Todo 可执行性复用 summary-lane owner，
包含 acceptance 拒绝。选举复用兼容 Python 的 JSON codec、Unicode 码点排序，
并通过 BigInt 处理无符号 64 位摘要前缀。

## 迁移收据

不可变 characterization fixture 在移动规则前，已于未修改的 `fb16c6cb1` 上捕获并通过。
18 个场景保留完整 admission JSON、quota contract/work-lane JSON 和 Markdown。
插桩单独测量 admission crossing，不混同外围 quota 命令：

| 操作 | 之前 | 之后 |
| --- | ---: | ---: |
| 无 capability 要求的已准入 lane | 0 | 1 |
| 缺少 capability 的场景 | 3 | 1 |
| 冲突／首个冲突场景 | 2 | 1 |
| 空源或非协调者 | 0 | 1 |
| 自适应模式关闭 | 0 | 0 |

此前有 P 次逐对重叠请求及 M 次未缓存 capability 请求；现在两个 owner 都在准入请求内
同进程执行。提前退出由零次变一次是有意代价：Python 不为省一次 crossing 而保留
候选／选举规则。这不表示整个 quota 命令只有一次请求，也不声称延迟或长程收益。

被替代的 Python dataclass、拒绝／选择／选举组合、brief／上下文策略、重叠 adapter
及准入常量已删除。Python 保留 transport adapter，负责历史 Todo／repository／resume
和 Agent-id codec、显式 source 与 display 选择及公开 objective 文本渲染。
这些在既有 Host 边界保留持久化拼写和公开文本，不选择、拒绝或排序 lane。
Capability wrapper、peer hash 及 projection-repair scope 匹配仍有独立生产调用方；
它们不是准入 fallback，也不是无人调用的兼容导出。

TypeScript `schedulingWriteScopeAllowed` 有意保留与 Python `projection_repair.write_scope_allowed` 的重复实现，后者仍由生产调用方 `build_boundary_projection_repair_hint` 用于缺失 scope 检测及非活动 boundary 候选匹配；当该调用方迁移至共享 typed scope owner 并验证等价行为后，删除该重复实现。

当 `task_orchestration.py` 中唯一生产调用方及其外围 should-run 事务原生消费 typed
源事实后，adapter 退出。届时调用方在进程内组合 `admitAdaptiveChildren`，删除 Python
adapter 及 `quota.task_orchestration.admit` transport 注册。在该调用方切换具有等价的
完整输出及 crossing 证据之前，保留冻结 fixture。

产品改动合计新增 342／删除 461 个物理行，净减 119 行。Python 准入模块由
490 行降至 137 行（新增 97／删除 450）；包含签名的 101 行 adapter 函数只负责
transport／兼容工作。新的 typed owner 为 222 行。测试、fixture 与文档行数单独计算。

## 资格边界

既有 dashboard capability workbench、orchestration 设置、Lark／CLI 消费方及 quota
回读保持原合同；没有视觉表面变化。Source-checkout 生产 `quota should-run` 入口与
`git archive HEAD` baseline 比较，使用合成临时 home、固定时钟并启用自适应准入。
File 和 SQLite 的完整 JSON（35,126／35,160 字节）及 Markdown
（8,565／8,587 字节）均逐字节一致。这资格化源码行为，不证明已安装采用。

聚焦 Python 套件通过 543 项；既有聚焦 TypeScript 套件通过 100 项，另有一个无关
PostgreSQL workspace 测试跳过；17 项新 typed 准入测试通过。Typecheck、语义词汇
漂移（独立 twin 45/45）、maintainability ratchet、interface budget 及 File／SQLite
集成 canary 均通过。Diff-scoped semantic advisory 未发现支持的新词汇 carrier；
其有限语法覆盖不证明语义完整。没有放宽预算、smoke anchor 或 gate。

有界重构共享 Todo 可执行性，并为既有 glob 引擎扩展大小写敏感模式，避免再造 matcher。
没有修改存储 backend、lease 持久化、lease authority 或 PostgreSQL IO。
在 `network` 可用的 capability 恢复读取中，完整输出也一致：File JSON／Markdown
为 31,907／8,478 字节，SQLite 为 31,937／8,500 字节。首次恢复比较观察到既有
capability-hook mutation：第一次 base 读取的 `local_private_state_mutated` 为 true，
随后 head 读取为 false。只运行 base 重复读取也复现 true → false，随后逐字节匹配 head。
最终比较在两次读取前先稳定该合成 hook 状态；没有改动 expected fixture 或产品规则。

合成 CLI 输出还保留一项 baseline 限制：
`interaction_contract.agent_channel.work_context.complete` 为 false，带有
`selected_todo`／`context_source_unavailable`。八份 JSON 观测（base／head、两种
provider 及两种 capability 状态）都有相同失败。已资格化准入及 quota 输出等价性，
未资格化完全可用的 selected-work context 交付。该独立回读缺口在本次准入 owner
切换范围之外，不能用 parity 成功掩盖。

本切片不资格化完整 CI、已安装打包、跨 Host 运行或性能。
