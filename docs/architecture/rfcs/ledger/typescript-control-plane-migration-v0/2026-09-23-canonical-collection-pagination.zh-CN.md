# canonical collection 分页检查点（2026-09-23）

于 2026-09-26 从 [typescript-control-plane-migration-v0.zh-CN.md](../../typescript-control-plane-migration-v0.zh-CN.md)（原小节“canonical collection 分页检查点（2026-09-23）”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

canonical collection 跨语言传输改为 TS 一致性分页：旧 direct list 和分页共用
`canonicalTodoCollection` 规则 owner，Python 校验并组装完整分页，保持调用方形状。
不提高 2 MiB RPC 上限，不在 Python 重建 Todo/acceptance 规则；并发版本变化导致
整份读取失败，File 只读打开不创建缺失 authority。限制与开销见[分页合同](../../../../reference/canonical-snapshot-pagination.md)。
shared-authority 的当前核对表区分已合入实现、在途 PR、新代码边界和 D1–D3 证据，不再以粗粒度包数代替剩余 PR。
