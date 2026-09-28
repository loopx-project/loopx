# 管家 collaboration 衔接检查点（2026-09-13）

于 2026-09-26 从 [typescript-control-plane-migration-v0.zh-CN.md](../../typescript-control-plane-migration-v0.zh-CN.md)（原小节“管家 collaboration 衔接检查点（2026-09-13）”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

在 `7eb4b7bb1661bd5eff63a8725a33169792d5964b`，#4152 是已合并的
lease-fenced text/note update 切片；#4121 SQLite 候选也已合并，但未晋级 provider。
实际 head 更新早期执行卡暗示的代码待合并状态，不解除其资格保留条件。

[管家/handoff RFC](../../capable-manager-semantic-handoff-v0.zh-CN.md) 遵循本文完整
事务收益规则：拟议 collaboration owner 替换一个完整请求事务与旧语义 caller，
不按字段增加 leaf RPC、不新增 TS daemon、不另造 Todo/Vision/lease authority。
已有 `coordination/todo_continuation.ts` 仅支持 promoted-local、同机、已注册
Agent、无 lease Todo，不是通用 pre-Todo/cross-Goal handoff；集成时保留其真实
兼容语义。M2 提供迁移收益回执及跨提交恢复证据；M1 普通主机工具无需等待全部 TS
或 provider 迁移。共享 Goal amendment 保留独立 proposal/commit 边界；受影响的
存储或完整 writer 退役，继续遵守 shared-authority D1–D3/T4 条件。此说明不交付
新的 runtime 行为。
