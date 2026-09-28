# Legacy 字段规则退役检查点

于 2026-09-26 从 [typescript-control-plane-migration-v0.zh-CN.md](../../typescript-control-plane-migration-v0.zh-CN.md)（原小节“Legacy 字段规则退役检查点”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

`todos/field_update.ts` 现在持有 legacy `update`、`claim`、`complete`、`supersede`
line writer 共用的完整 metadata intent 组装：status 与 completion 时间、未传与显式
清空、binding 优先级、已移除 policy 的修复、resume-generation 配对及 completion
metadata。它直接组合已有 TS completion rule。被替代的 Python decision 分支，以及
失去最后调用者的 `todo.completion_state.metadata_updates` RPC/facade 一起删除，
不保留为 fallback。

这是一份纯 plan，不是 admission 或 provider commit。Python 仍保留 Markdown 定位／
编码、字节级 no-op 检查、锁与外部 effect；本批不宣称迁完公共 role/binding admission
或 event writer。Native planning 现按 T1 所述组合此 owner；不支持的字段不扩权、
不 promotion goal、不增加第三条存储路径。plan 拒绝时，现在连调用方的内存行缓冲也保持不变；公共
事务在拒绝时原本就不会提交。

每次 legacy line write 有一次 field-plan crossing：普通编辑替代原 metadata RPC；
已经 finalization、携带 override 的 completion 会增加一次 planning crossing。
带缓存的 codec normalization 调用仍在。这兑现的是语义代码删除，不宣称每个命令
都减少 round trip。完整 goal cutover 后随最后 legacy lifecycle caller 删除 adapter，
或者迁移该 caller 时将 plan 折叠进其粗粒度事务；不得继续扩张逐字段 RPC。
Markdown renderer 长期保留。
