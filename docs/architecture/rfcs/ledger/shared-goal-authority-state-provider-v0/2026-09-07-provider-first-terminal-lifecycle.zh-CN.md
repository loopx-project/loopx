# Provider-first terminal lifecycle 检查点（2026-09-07）

于 2026-09-26 从 [shared-goal-authority-state-provider-v0.zh-CN.md](../../shared-goal-authority-state-provider-v0.zh-CN.md)（原小节“Provider-first terminal lifecycle 检查点（2026-09-07）”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

Promotion 后的 `complete`、`supersede` 与按 role 执行的 `archive`，现在在 file、
NoKV、PostgreSQL 上使用同一笔 TypeScript 原生事务。authority owner 决定
actor/claim/lease admission，从 typed caller intent 推导 successor 的 priority、capability
与 Agent binding、exclusion、continuation 和 predecessor relation，reduce completion
policy，以 CAS 提交 Todo/lease/head/outbox write set，并持久化 replay receipt。Python
只保留 registry fact、caller-approved validation effect、intent/result transport 与兼容投影
drain 的 adapter 职责，不再针对不同 provider 选择另一种 terminal 或 successor outcome。
Legacy Markdown 与 event writer 在物化 record 前复用同一个纯 TypeScript successor
decision。

Validation declaration 只以 required marker 与 SHA-256 digest 跨越 canonical 边界。
raw argv 留在权限为 0600 的 host-local sidecar，恢复时必须先证明 digest 匹配才可执行。
这使 provider head 保持可移植、public-safe，同时不会让 recovery 静默绕过 validation。
导入 v0 的 `index` 继续作为归档顺序兼容事实；native record 回退到持久
completion/update 时间与 Todo identity。Todo 已不在当前 canonical collection 中的
legacy lease file 继续作为历史审计材料保留，但不进入 live projection。

资格验证使用同一份只读、生产复杂度快照做三臂对照：不可变 legacy baseline clone、
隔离 file store、隔离的真实 PostgreSQL tenant。两个 provider head 精确比较；legacy
结果按显式 compatibility projection 比较。归档时仅从 legacy hot view 排除 provider
保留的 archive 记录及其历史 lease，并且只有先证明每个 role 的相对顺序完全一致，才可
忽略导入 `index` 的绝对值；domain 字段、归档选择、active lease 与非目标记录不得归一化，
源快照必须不变。可执行演练为
`examples/control_plane/authority-three-arm-rehearsal.py`。受检入的确定性 public-safe
规模 fixture 在每个 provider conformance suite 中制造同样的分布、压力与 hard-lease
fence。它不能替代只读三臂演练，因为所有 provider 共享新的 semantic owner，可能同时
同意同一个回归。

凡声称推进本 RFC 的 PR，都必须遵守
[production-scale fixture 维护契约](../../../../development/testing-and-quality.md#production-scale-fixture-stewardship--生产规模-fixture-维护契约)：
声明 fixture 影响、覆盖所有受影响的 provider arm，并把只读三臂演练保留为独立的
promotion gate。

Legacy lifecycle 的字段组装现在调用唯一 TS field planner，详见
[TS 退役检查点](../typescript-control-plane-migration-v0/2026-09-09-legacy-field-rule-retirement.zh-CN.md)。
它删除 Python decision，但不改变逐 goal 的 authority 阶段：未 promotion 的 goal
仍由持锁 Markdown writer 提交，promoted goal 仍使用既有 provider transaction 与
unsupported-field fence。planner 不读取 provider，也不授予 lease、CAS receipt 或
写权限。该检查点闭合的是一个规则 owner，不是剩余 mutation inventory 或本地
store／promotion 资格化。
