# deepresearch 账本保留来源时钟（2026-10-05）

本条目为 [#5609](https://github.com/loopx-project/loopx/pull/5609) 记录。RFC 正文只保留
指向[公开 GitHub 方法检查点（2026-10-02）](2026-10-02-public-github-method-delivery.zh-CN.md)
的指针；这次较晚的交付记录按[账本约定](../README.zh-CN.md)写在这里。

[English mirror](2026-10-05-source-clock-preservation.md)

新增的外部证据账本行保留规范化回执的 `accessed_at` 与 `publication_date`（出版日期未知时
保留空值）。单独新增的 `recorded_at` 记录入账落盘时间；入账或重放一次 capture 都不构成
一次新的来源读取。精确回执重放不重写既有行，包括历史遗留行。普通
`deepresearch add-source` 继续使用原有的本机读取时钟。

这是时钟保留，不是 provider 执行或出版 attestation：canonical TypeScript 回执/admission
校验仍是唯一权威；既有 CLI 与同源回读消费同一个回执，本次持久化改动不构成 provider 挂载
或打包前端/Lark 链路的资格证明。

通过：base/head 成对的真实 CLI 重放，覆盖历史访问时间、`Z` 与带时区精度、以及 null 或
已知出版日期（两侧各 19 个 fixture，改动前全部不保留来源时钟，改动后 19 个全部保留）；
时钟被篡改时拒绝并在恢复原回执后完成实际入账；只读隔离；既有行（含历史遗留行）的精确
重放不产生任何写入；未采纳引用被拒绝；两个真实 CLI 进程并发写入同一 claim 只产生一行；
普通 `add-source` 的行形状不变。聚焦 Python/TypeScript 套件、语义 inventory smoke、
公共边界扫描与 `git diff --check` 通过。
