# C1 上下文来源保真检查点

English mirror: [same checkpoint](2026-10-29-context-source-fidelity.md).

C1 集成 main `b201da8bab305d053866aa0d4516b97ba4a4499e`，复用普通
[PR #7661](https://github.com/loopx-project/loopx/pull/7661)，来源为
`4bb530c0f7b22950ba17300dacdc5a23debedf12`。这是提案源码采用，尚未证明合并、
安装或模型资格。独立的 journal 修复
[#7657](https://github.com/loopx-project/loopx/pull/7657) 仍沿既有 owner 作为前置。

通用上下文 codec 在冷态交付和全量恢复中也会归一化原始数值；温态相等判断可能忽略
`1` 与 `1.0`、负零及不同大整数的变化。未改基线的九个独立精确数值用例中七败。
普通字典相等掩盖了部分失败。

既有 TypeScript codec 现在比较稀疏原数值身份，返回既有来源修改计划；Python 将
修改应用到原值，不选择来源、不做差分或恢复决策。C1 验收共享与 codec 使用同一
来源身份 helper。完整正文、原始/分组 literal 与来源重排均保留出处。恢复会针对
传入的当前 capture 拒绝归一化后相等的损坏 delta。直接 JavaScript caller 仍取得
独立副本；native hook、来源选择、权限、准入、结算及历史 digest 均不改变。

独立切片通过 76 项 Python 检查，含真实 File/SQLite CLI 冷态、温态和丢 baseline
路径，以及 90 项 TypeScript 检查。另有 194 项捕获、选择、reader 和生命周期回归
通过；46 项显式 native binary 用例仍 skip。Typecheck、Ruff、公开扫描、语义建议
及标准本地 premerge 通过。C1 集成结果绑定在其 PR，旧 cohort 留在原版本。这证明
从传入来源重建，不证明旧语义 hash 已能区分所有数值来源。

同负载合成组件比较覆盖五次 codec RPC，不是完整模型或 Host Turn：

| 正文 | 请求参数 JSON，基线 → 修复 | 返回结果 JSON，基线 → 修复 |
| --- | --- | --- |
| 8 KiB | 70,855 → 72,040 bytes | 41,277 → 5,183 bytes |
| 64 KiB | 524,616 → 525,801 bytes | 300,569 → 5,183 bytes |

较大样本都保留温态 delta；模型可见增长来自恢复的数值差异。无正文时，返回元数据
由 2,400 增至 3,560 bytes。顺序本地延迟样本涨跌不一；内存、完整 Host/模型/
人工注意力费用尚未资格化。请求上限、原 quota 比较及冻结的 25,862 > 13,000
失败均保持。

继续在全部真实消费者和剩余 bridge 中验证数值 hash 身份，再进行 writer/caller
切换和历史 reader 退出；复用本 codec 与 journal owner，不给各 caller 增加数值
补丁。原 quota 单会话的验收成果/完整成本仍开放，可选双会话另评。
