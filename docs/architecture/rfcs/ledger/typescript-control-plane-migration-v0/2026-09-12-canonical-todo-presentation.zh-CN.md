# Canonical Todo 展示检查点（2026-09-12）

于 2026-09-26 从 [typescript-control-plane-migration-v0.zh-CN.md](../../typescript-control-plane-migration-v0.zh-CN.md)（原小节“Canonical Todo 展示检查点（2026-09-12）”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

authority 边界现在把 presentation 作为一等 projection contract，而不再把它命名为
`legacy_projection`。共享的 TS presentation normalizer 会把 v0 wire shape 的
`source_section`／`index` 映射为 `display_section`／`display_order`；native record
则根据 domain 的 role／archive state 推导展示 section，绝不伪造持久化 index。两种
wire shape 共用同一份 normalized presentation contract，wire 坐标不构成第二套 Todo
state machine。

Todo creation、terminal successor materialization、projection validation、
standing-decision ordering 与 archive ordering 现在共用同一个 presentation owner。
两种 wire shape 共用 canonical domain validator，v0 record 只是从已校验 domain
record 经过 adapter 生成。这统一了语义 owner，但不重写 v0 head 或 receipt。

Python read caller 现在直接导入语义 owner；兼容 facade 不再是内部依赖。Python 的
展示排序在存在 source `index` 时保持其顺序，在 native record 上使用完成／更新时间
加 Todo identity 做确定性排序，因此兼容 shape 不会泄漏进业务 eligibility 或 lifecycle
decision。

后续迁移可以持久化可选的 canonical `presentation` object，但必须先证明导入的
section 到底是 provenance 还是当前 display intent，并资格化稳定的 display-order
策略。在此之前，native display position 仍在 renderer 边界派生，不能参与 authority
lifecycle decision。
