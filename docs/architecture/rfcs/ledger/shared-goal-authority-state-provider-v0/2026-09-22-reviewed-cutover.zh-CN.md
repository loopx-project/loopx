# 已评审 cutover 检查点

于 2026-09-26 从 [shared-goal-authority-state-provider-v0.md](../../shared-goal-authority-state-provider-v0.md)（原小节 "Reviewed cutover checkpoint"）移入；中文版原本没有这一小节，本文是 [英文条目](2026-09-22-reviewed-cutover.md) 的语义镜像。RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

saved-plan / recovery 切片补上了一个具体的运维缺口：执行可以绑定到已评审的
source / provider / policy，带 fence 的 cutover 可以完成或读回，而不必从旧的
Markdown 重建意图。TS owner 让两条路径共享持久资格判定和精确回执证明。见
[操作与验收](../../../../reference/reviewed-coordination-promotion.md)。本阶段不授权
迁移任何活跃 Goal，也不翻转默认值。

保留 claim 的迁移 #4870 与已评审 cutover #4888 已合入；shadow drain 规划 #4920
也已合入。要对一个已有 claim 的既存 Goal 验证它们合起来的当前 head，而不是把一个旧
PR 上的 hold 当成现状。保留已登记 owner、现有 claim 和 lease；不能为了让存储迁移
看起来就绪而清空所有权。saved-plan 载体在合并验证期间必须保留迁移策略、已登记
Agent 事实和目标 digest。

上文当前的七边界计划把调用方准入、执行方 fence 和快照读取分开；D2 与整合迁移各自
都可能再拆。这个可移植恢复切片只是迁移资格的一部分，不是一个完整交付包。以这一份
当前计划为准，不要靠数叶子修复。真实的 soak 时长不能压缩成承诺的 PR 数量。
PostgreSQL 的服务准入与运维仍是独立的中期路线。

现有 Goal 的可审核晋升与恢复、所有新 Goal 默认选用 provider、删除全部 Python，
是三个不同完成条件。先交付一条能保留状态、能读回、能恢复的真实迁移路径，再按调用方
闭合程度删除旧实现。不要用已合入 PR 数量替代端到端验收。
