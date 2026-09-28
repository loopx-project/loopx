# Provider-neutral projection conformance 检查点（2026-09-12）

于 2026-09-26 从 [typescript-control-plane-migration-v0.zh-CN.md](../../typescript-control-plane-migration-v0.zh-CN.md)（原小节“Provider-neutral projection conformance 检查点（2026-09-12）”）原文移入，内容未改；RFC 基线 `3e443ad7c`。检查点记录放在本账本，不放在 RFC 正文。

conformance 边界现在为 legacy v0 与 native Todo record 共用一个 projection-fixture
builder。它统一负责确定性的 Unicode 排序、read-model digest/field 构造，以及仅限
兼容层的转换；provider 测试不再手工重建这些字段。规模 envelope 显式声明 status
顺序并校验计数，因此 JSON key 顺序变化不会静默改变哪个 Todo 获得 lease、successor
或 archive 角色。

File、SQLite 与 NoKV suite 现在会在两种 record shape 上执行同一组生产规模 terminal
case。另有独立 parity harness，使用三个隔离 provider 重放同一条 seed、observation、
lease 序列，并在忽略 provider-specific revision token 后比较 logical head 以及已提交
的 event/projection/receipt trace。这是 conformance 证据，不是新的 authority writer、
provider 默认值或 promotion 声明；PostgreSQL 仍受现有真实服务资格化 gate 约束。

旧 v0 consumer manifest 继续可读，并保留所有已有字段。默认 Markdown capture 仍
输出 v0；本 PR 不改写已存 head，也不自动晋升 goal。schema 分层不等于允许后续迁移
丢失 v0 provenance 或改变旧排序。
