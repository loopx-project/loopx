# Core 发现入口检查点 — 2026-11-19

于 2026-11-21 从 [monorepo-distribution-split-v0.zh-CN.md](../../monorepo-distribution-split-v0.zh-CN.md)（原小节“Core 发现入口检查点 — 2026-11-19”）原文移入，内容未改；RFC 基线 `dcf208c855`。检查点记录放在本执行台账（延续附录 A），不放在 RFC 正文。

| 边界 | 交付增量／证据 | 仍未完成 |
| --- | --- | --- |
| V1/V3，能力发现 | Benchmark 包公开符号按需解析到所选既有 owner，保留全部 199 个符号及顺序；显式星号导入仍请求全部。执行实现不可用且无 Node 时，帮助／列表／详情仍可用，无进程启动、网络或状态写入。 | 未从 wheel 删除 benchmark 文件，也不验收 capability 分包缺席安装。 |
| 元数据与活跃调用方 | 同冻结 `main@e235cde9c` workload 的目录和全部 25 项详情逐字节一致；53 项 CLI 帮助／列表／详情／未知 id 观察一致。发现入口的 benchmark 子模块从 28 降到 2，LoopX 模块从 326 降到 298。保留 benchmark 语义及公开符号的 canonical 对象身份。 | Import 数量不代表时延、完整成本、模型收益或完整 D2/D5 闭包。 |
| 验收与继续方向 | 重建发行物通过 52 项安装 core 发现／执行／上下文／恢复用例；383 项所选消费方与 43 项架构／文档用例分别通过。没有新增状态 owner、provider id、启用开关或顶层模块。 | R1/M3 与 V1–V5/V10 继续开放；下一步仍是 capability 分包隔离与所选 Standard 交付。公开渠道 C1 资格仍归既有 release owner。 |
